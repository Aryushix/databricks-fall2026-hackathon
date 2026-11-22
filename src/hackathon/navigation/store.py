"""Facility and capability data for navigation, behind a CapabilityStore interface.

Facility data lives here only (patient data never does). FileCapabilityStore loads:
  * facilities_enriched.csv : canonical facility fields
  * facilities_verified.csv : resolved_latitude / resolved_longitude, location_status, location_confidence
  * records.jsonl(.gz) + summary.json from `python -m hackathon.verification run`
A Delta-table store can implement the same four methods on Databricks later.

Paths: NAV_DATA_DIR (default ~/hackathon-data), or NAV_FACILITIES_ENRICHED, NAV_FACILITIES_VERIFIED,
NAV_RECORDS, NAV_SUMMARY. A /Volumes/... path that isn't mounted (Databricks Apps) is downloaded with
the Databricks SDK first. Data files never go in the repo.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, Protocol

import numpy as np
import pandas as pd

from ..cleaning import jlist, missing
from ..verification.query import concept_of, load_records

FACILITY_COLUMNS = ["unique_id", "name", "facilityTypeId", "specialties", "address_city", "address_stateOrRegion",
                    "source_urls", "dup_group_id"]
LOCATION_COLUMNS = ["unique_id", "resolved_latitude", "resolved_longitude", "location_status", "location_confidence"]
OPTIONAL_LOCATION_COLUMNS = ["resolved_location_source"]
OPTIONAL_FACILITY_COLUMNS = ["officialPhone", "dq_flags"]
MAX_EVIDENCE = 2


class DataError(RuntimeError):
    """The data files are missing or don't agree with each other."""


class CapabilityStore(Protocol):
    def facilities(self) -> pd.DataFrame: ...

    def records_for(self, kb_id: str) -> list[dict]: ...

    def kb_ids(self) -> set[str]: ...

    def versions(self) -> dict: ...


def _slim(rec: dict) -> dict:
    """Only what navigation reads: status, confidence, reasons, review flags, location, top quotes."""
    ev = [{k: e.get(k) for k in ("quote", "field", "source_type", "source_url")}
          for e in (rec.get("direct_evidence") or [])[:MAX_EVIDENCE]]
    keep = ("hospital_id", "normalized_treatment", "verification_status", "reason_codes", "confidence_score",
            "confidence_basis", "requires_manual_review", "review_reasons", "hospital_location", "pipeline_version",
            "config_hash", "run_timestamp")
    return {**{k: rec.get(k) for k in keep}, "direct_evidence": ev}


class FileCapabilityStore:
    def __init__(self, facilities: pd.DataFrame, records: Iterable[dict], summary: Optional[dict] = None):
        df = facilities.copy()
        df["hospital_id"] = df["unique_id"]
        for col in ("resolved_location_source", "officialPhone", "dq_flags"):
            if col not in df.columns:
                df[col] = None
        df["latitude"] = pd.to_numeric(df["resolved_latitude"], errors="coerce")
        df["longitude"] = pd.to_numeric(df["resolved_longitude"], errors="coerce")
        bad = ~(df.latitude.between(-90, 90) & df.longitude.between(-180, 180))
        df.loc[bad, ["latitude", "longitude"]] = np.nan          # out of range: excluded, never imputed
        df["specialties_list"] = df["specialties"].map(lambda v: [x for x in jlist(v) if isinstance(x, str)])
        df["source_urls_list"] = df["source_urls"].map(lambda v: [x for x in jlist(v) if isinstance(x, str)])
        df["dup_group_id"] = df["dup_group_id"].map(lambda v: None if missing(v) else str(v)).astype(object)
        self._df = df.set_index("hospital_id", drop=False)
        self._by_kb: dict[str, list[dict]] = {}
        ids = set(self._df.index)
        unknown = 0
        for r in records:
            if r["hospital_id"] not in ids:
                unknown += 1
                continue
            self._by_kb.setdefault(concept_of(r), []).append(_slim(r))
        if unknown:
            raise DataError(f"{unknown} verification records name hospitals that aren't in the facility files")
        self._summary = summary or {}
        runs = [r for recs in self._by_kb.values() for r in recs[:1]]
        stamps = [r.get("run_timestamp") for recs in self._by_kb.values() for r in recs if r.get("run_timestamp")]
        first = runs[0] if runs else {}
        self._versions = {
            "kb": self._summary.get("kb_version") or ";".join(sorted({r["normalized_treatment"]["kb_version"] for r in runs})) or None,
            "verification_run": (f"{first.get('pipeline_version')}#{first.get('config_hash')}@{max(stamps)}" if stamps else None),
        }

    # ------------------------------------------------------------------ interface
    def facilities(self) -> pd.DataFrame:
        return self._df

    def records_for(self, kb_id: str) -> list[dict]:
        return self._by_kb.get(kb_id, [])

    def kb_ids(self) -> set[str]:
        return set(self._by_kb)

    def versions(self) -> dict:
        return dict(self._versions)

    def summary(self) -> dict:
        return {"facilities": int(len(self._df)), "without_coordinates": int(self._df.latitude.isna().sum()),
                "kb_ids": sorted(self._by_kb), "records": int(sum(len(v) for v in self._by_kb.values())),
                **self._versions}

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_files(cls, enriched: str | Path, verified: str | Path, records: str | Path,
                   summary: Optional[str | Path] = None) -> FileCapabilityStore:
        for p in (enriched, verified, records):
            if not Path(p).exists():
                raise DataError(f"missing data file: {Path(p).name}")
        e = pd.read_csv(enriched, dtype=str, keep_default_na=False, low_memory=False).replace({"": None})
        v = pd.read_csv(verified, dtype=str, keep_default_na=False, low_memory=False).replace({"": None})
        for name, frame, cols in (("facilities_enriched", e, FACILITY_COLUMNS), ("facilities_verified", v, LOCATION_COLUMNS)):
            gone = [c for c in cols if c not in frame.columns]
            if gone:
                raise DataError(f"{name} is missing columns {gone}")
        if set(e.unique_id) != set(v.unique_id):
            raise DataError("facilities_enriched and facilities_verified have different unique_id sets")
        cols = LOCATION_COLUMNS + [c for c in OPTIONAL_LOCATION_COLUMNS if c in v.columns]
        fcols = FACILITY_COLUMNS + [c for c in OPTIONAL_FACILITY_COLUMNS if c in e.columns]
        df = e[fcols].merge(v[cols], on="unique_id", how="left", validate="one_to_one")
        summ = json.loads(Path(summary).read_text(encoding="utf-8")) if summary and Path(summary).exists() else None
        return cls(df, load_records(records), summ)


def default_paths() -> dict:
    base = Path(os.getenv("NAV_DATA_DIR", str(Path.home() / "hackathon-data"))).expanduser()
    rec = base / "verification" / "records.jsonl.gz"
    if not rec.exists() and (base / "verification" / "records.jsonl").exists():
        rec = base / "verification" / "records.jsonl"
    return {"enriched": os.getenv("NAV_FACILITIES_ENRICHED", str(base / "facilities_enriched.csv")),
            "verified": os.getenv("NAV_FACILITIES_VERIFIED", str(base / "facilities_verified.csv")),
            "records": os.getenv("NAV_RECORDS", str(rec)),
            "summary": os.getenv("NAV_SUMMARY", str(base / "verification" / "summary.json"))}


def _local(path: str) -> str:
    """A /Volumes path that isn't mounted (Databricks Apps) is downloaded once to a temp folder."""
    if not path.startswith("/Volumes/") or Path(path).exists():
        return path
    from databricks.sdk import WorkspaceClient
    target = Path(tempfile.gettempdir()) / "nav-data" / Path(path).name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with WorkspaceClient().files.download(path).contents as src, open(target, "wb") as dst:
            dst.write(src.read())
    return str(target)


def lazy_file_store(paths: Optional[dict] = None, retry_after_s: float = 60.0) -> Callable[[], FileCapabilityStore]:
    """Load on first use and keep it. A failed load is retried at most once per `retry_after_s`."""
    state: dict = {"store": None, "error": None, "at": 0.0}

    def provider() -> FileCapabilityStore:
        if state["store"] is not None:
            return state["store"]
        if state["error"] is not None and time.monotonic() - state["at"] < retry_after_s:
            raise state["error"]
        p = {**default_paths(), **(paths or {})}
        try:
            try:
                summary = _local(p["summary"])
            except Exception:                     # summary.json is optional
                summary = None
            state["store"] = FileCapabilityStore.from_files(_local(p["enriched"]), _local(p["verified"]),
                                                            _local(p["records"]), summary)
            state["error"] = None
            return state["store"]
        except Exception as exc:
            state["error"], state["at"] = exc, time.monotonic()
            raise

    return provider
