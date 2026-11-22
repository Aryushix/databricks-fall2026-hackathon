"""Query modes over precomputed verification records. No re-verification and no re-scoring.

  mode 1  hospital + treatment -> its record
  mode 2  hospital -> capability profile: every treatment not UNVERIFIED, by status then confidence
  mode 3  treatment (+ optional location) -> hospitals by status, then confidence, then straight-line
          distance from the verified file's resolved coordinates; `location_status: conflicting`
          carries a warning

Records are what `python -m hackathon.verification run` writes (records.jsonl, or .jsonl.gz).
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from ..cleaning import haversine_km

STATUS_ORDER = {"CONFIRMED": 0, "LIKELY": 1, "NOT CONFIRMED": 2, "UNVERIFIED": 3, "CONTRADICTED": 4}


def load_records(path: str | Path) -> list[dict]:
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def concept_of(rec: dict) -> str:
    return rec["normalized_treatment"]["concept_id"]


def mode1(records: Iterable[dict], hospital_id: str, concept_id: str) -> Optional[dict]:
    return next((r for r in records if r["hospital_id"] == hospital_id and concept_of(r) == concept_id), None)


def mode2_profile(records: Iterable[dict], hospital_id: str) -> list[dict]:
    rows = [r for r in records if r["hospital_id"] == hospital_id and r["verification_status"] != "UNVERIFIED"]
    return sorted(rows, key=lambda r: (STATUS_ORDER[r["verification_status"]], -(r.get("confidence_score") or 0)))


def mode3_rank(records: Iterable[dict], concept_id: str, latitude: Optional[float] = None,
               longitude: Optional[float] = None, radius_km: Optional[float] = None,
               statuses: Optional[Iterable[str]] = None) -> list[dict]:
    """Hospitals for one treatment. With a location, records without resolved coordinates are dropped
    (never imputed) and `radius_km` limits straight-line distance."""
    keep = set(statuses) if statuses is not None else set(STATUS_ORDER)
    recs = [r for r in records if concept_of(r) == concept_id and r["verification_status"] in keep]
    dist = [None] * len(recs)
    if latitude is not None and longitude is not None:
        loc = [r.get("hospital_location") or {} for r in recs]
        lat = np.array([x.get("resolved_latitude") if x.get("resolved_latitude") is not None else np.nan for x in loc], float)
        lon = np.array([x.get("resolved_longitude") if x.get("resolved_longitude") is not None else np.nan for x in loc], float)
        d = haversine_km(latitude, longitude, lat, lon) if len(recs) else np.array([])
        ok = ~np.isnan(d)
        if radius_km is not None:
            ok &= d <= radius_km
        recs = [r for r, k in zip(recs, ok) if k]
        dist = [float(x) for x, k in zip(d, ok) if k]
    rows = []
    for r, d in zip(recs, dist):
        status = (r.get("hospital_location") or {}).get("location_status")
        rows.append({"hospital_id": r["hospital_id"], "status": r["verification_status"],
                     "confidence": r.get("confidence_score"), "distance_km": d, "location_status": status,
                     "location_warning": "conflicting" if status == "conflicting" else None, "record": r})
    rows.sort(key=lambda x: (STATUS_ORDER[x["status"]], -(x["confidence"] or 0.0),
                             x["distance_km"] if x["distance_km"] is not None else 0.0))
    return rows
