"""NGO service finder: facilities within a radius for one service, from verified evidence.

Path A (services with a verifier KB, configs/services.yaml `kb_id`): the KB's precomputed record decides. CONFIRMED
and LIKELY records are recommended with their own heuristic_v1 score, never boosted; NOT CONFIRMED and CONTRADICTED
are left out. The KB already reads every claim with look-alike, negation and outsourcing rules, so a second, looser
match on the same claims would only add the look-alikes the KB rejected.

Path B (services without a KB): the facility's claims (claim_verifier.py statuses) are matched to the service's
4-pillar profile. A claim matches a requirement term when the term's words appear in order in the claim, or their
word sets overlap by Jaccard >= 0.75. Each pillar scores the best matching claim's status weight x source trust;

    overall = 0.35 procedures + 0.30 equipment + 0.20 specialties + 0.15 capabilities

A CONTRADICTED claim that matches disqualifies the facility. Path B results are tier SEMANTIC_MATCH: a description
of what the listings say, below every KB result, and never called verified.

Sort: tier (CONFIRMED > LIKELY > SEMANTIC_MATCH), then confidence, then distance.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

from ..cleaning import haversine_km
from ..verification.claim_verifier import STATUS_WEIGHT, tokens
from .contract import FacilityServiceRecommendation, MatchedClaim, ServiceRecommendRequest
from .red_flags import REPO_ROOT

SERVICES = REPO_ROOT / "configs" / "services.yaml"
PILLARS = {"procedures": ("procedure", "procedure"), "equipment": ("equipment", "equipment"),
           "specialties": ("specialties", "specialty"), "capabilities": ("capability", "capability")}
TIER_PRIORITY = {"CONFIRMED": 3, "LIKELY": 2, "SEMANTIC_MATCH": 1, "UNVERIFIED": 0}
JACCARD = 0.75


def load_services(path: str | Path = SERVICES) -> dict:
    return yaml.safe_load(Path(path).read_text())


def term_matches(term_toks: list[str], claim_toks: list[str]) -> bool:
    if not term_toks or not claim_toks:
        return False
    if f" {' '.join(term_toks)} " in f" {' '.join(claim_toks)} ":
        return True
    a, b = set(term_toks), set(claim_toks)
    return len(a & b) / len(a | b) >= JACCARD


class ServiceMatcher:
    def __init__(self, facilities: Optional[pd.DataFrame] = None, evidence: Optional[pd.DataFrame] = None,
                 kb_records: Optional[dict] = None, services: Optional[dict] = None):
        """facilities: facilities_verified (unique_id, name, facilityTypeId, resolved_latitude/longitude,
        officialPhone, dq_flags, resolved_location_source). evidence: tools/verify_claims.py output.
        kb_records: {(hospital_id, kb_id): verifier record}."""
        if facilities is None:
            facilities, evidence, kb_records = self.load_files()
        self.cfg = services or load_services()
        self.fac = facilities.set_index("unique_id", drop=False)
        self.lat = pd.to_numeric(self.fac.get("resolved_latitude", self.fac.get("latitude")), errors="coerce").to_numpy()
        self.lon = pd.to_numeric(self.fac.get("resolved_longitude", self.fac.get("longitude")), errors="coerce").to_numpy()
        self.kb = kb_records or {}
        self.claims: dict[str, list] = {}
        if evidence is not None and len(evidence):
            for rec in evidence.to_dict("records"):
                self.claims.setdefault(rec["facility_id"], []).append(
                    (rec["category"], rec["evidence_text"], tokens(rec["evidence_text"]), rec["claim_status"],
                     float(rec["source_trust_score"]), rec.get("corroboration_quote"), rec.get("corroboration_source")))

    @staticmethod
    def load_files(data_dir: Optional[str] = None):
        base = Path(data_dir or os.getenv("NAV_DATA_DIR", str(Path.home() / "hackathon-data"))).expanduser()
        fac = pd.read_csv(base / "facilities_verified.csv", dtype=str, keep_default_na=False, low_memory=False).replace({"": None})
        ev_path = base / "evidence_verified.csv.gz"
        ev = pd.read_csv(ev_path, dtype=str, keep_default_na=False) if ev_path.exists() else None
        recs = {}
        rec_path = base / "verification" / "records.jsonl.gz"
        if rec_path.exists():
            import gzip
            with gzip.open(rec_path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    r = json.loads(line)
                    recs[(r["hospital_id"], r["normalized_treatment"]["concept_id"])] = r
        return fac, ev, recs

    # ------------------------------------------------------------------ public
    def match(self, service_id: str, lat: float, lon: float, radius_km: float = 150.0, min_confidence: float = 0.5,
              max_results: int = 10) -> list[FacilityServiceRecommendation]:
        req = ServiceRecommendRequest(service_id=service_id, latitude=lat, longitude=lon, radius_km=radius_km,
                                      min_confidence=min_confidence, max_results=max_results)
        svc = self.cfg["services"].get(req.service_id)
        if svc is None:
            raise ValueError(f"unknown service: {req.service_id}")
        dist = haversine_km(req.latitude, req.longitude, self.lat, self.lon)
        in_range = np.flatnonzero((dist <= req.radius_km) & ~np.isnan(dist))
        out = []
        for i in in_range:
            uid = self.fac.index[i]
            rec = self._kb_path(uid, svc, float(dist[i])) if svc.get("kb_id") else self._pillar_path(uid, svc, float(dist[i]))
            if rec is not None and rec.overall_confidence >= req.min_confidence:
                out.append(rec)
        out.sort(key=lambda r: (-TIER_PRIORITY[r.verification_tier], -round(r.overall_confidence, 2), round(r.distance_km, 1)))
        return out[:req.max_results]

    # ------------------------------------------------------------------ paths
    def _base(self, uid: str, d: float, tier: str, conf: float, claims, quotes, warnings) -> FacilityServiceRecommendation:
        f = self.fac.loc[uid]
        flags = str(f.get("dq_flags") or "")
        phone = f.get("officialPhone")
        if "phone_shared_across_facilities" in flags:
            warnings.append("shared_phone: this number is listed for several facilities in different cities")
        if f.get("resolved_location_source") == "pin_area":
            warnings.append("coords_approximate: the position is the PIN area's, not the facility's")
        return FacilityServiceRecommendation(
            hospital_id=uid, hospital_name=f.get("name"), facility_type=f.get("facilityTypeId"), distance_km=round(d, 1),
            overall_confidence=round(float(conf), 3), verification_tier=tier, matched_claims=claims,
            corroborating_quotes=quotes[:3], official_phone=phone, warnings=warnings)

    def _kb_path(self, uid: str, svc: dict, d: float) -> Optional[FacilityServiceRecommendation]:
        r = self.kb.get((uid, svc["kb_id"]))
        if r is None or r["verification_status"] not in ("CONFIRMED", "LIKELY"):
            return None
        ev = r.get("direct_evidence") or []
        claims = [MatchedClaim(pillar="kb", claim_text=e["quote"], claim_status=r["verification_status"],
                               source_trust_score=1.0 if e.get("source_type") == "official_site_page" else 0.5,
                               source_url=e.get("source_url")) for e in ev[:5]]
        warnings = [f"review: {x}" for x in r.get("review_reasons", [])[:3]]
        return self._base(uid, d, r["verification_status"], r.get("confidence_score") or 0.0, claims,
                          [e["quote"] for e in ev], warnings)

    def _pillar_path(self, uid: str, svc: dict, d: float) -> Optional[FacilityServiceRecommendation]:
        fclaims = self.claims.get(uid)
        if not fclaims:
            return None
        weights = self.cfg.get("pillar_weights", {})
        total, matched, quotes, trusts = 0.0, [], [], []
        for pillar, terms in (svc.get("requirements") or {}).items():
            category, label = PILLARS[pillar]
            term_toks = [tokens(t) for t in terms]
            best = None
            for cat, text, ctoks, status, trust, quote, source in fclaims:
                if cat != category or not any(term_matches(t, ctoks) for t in term_toks):
                    continue
                if status == "CONTRADICTED":
                    return None                                    # a credible negative: not offered here
                score = STATUS_WEIGHT.get(status, 0.0) * trust
                if best is None or score > best[0]:
                    best = (score, text, status, trust, quote, source)
            if best:
                total += weights.get(pillar, 0.0) * best[0]
                trusts.append(best[3])
                matched.append(MatchedClaim(pillar=label, claim_text=best[1], claim_status=best[2],
                                            source_trust_score=best[3],
                                            source_url=best[5] if best[5] not in ("listing", "description") else None))
                if best[4]:
                    quotes.append(best[4])
        if not matched:
            return None
        warnings = ["not_verified: matched on the facility's listed claims, not by a verifier KB"]
        if all(t <= 0.5 for t in trusts):
            warnings.append("unverified_sources_only: the claims come only from directories or shared sites")
        return self._base(uid, d, "SEMANTIC_MATCH", total, matched, quotes, warnings)
