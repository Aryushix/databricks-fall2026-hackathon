"""Adapter from Care Finder's confirmed SearchQuery to the team's service recommender.

Put beside app/carefinder/api.py and main.py. The repository's src/ directory is
added to the import path so this works with `uvicorn api:app` from app/carefinder.
No model code, evidence scoring, or ranking is duplicated here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# app/carefinder/recommendation_connector.py -> repository root -> src/
_REPO = Path(__file__).resolve().parents[2]
_SRC = str(_REPO / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from hackathon.navigation.contract import ServiceRequest  # noqa: E402
from hackathon.navigation.locate import default_resolver  # noqa: E402
from hackathon.navigation.service import build_navigator  # noqa: E402
from schemas import Evidence, EvidenceStatus, Hospital, TrustLabel  # noqa: E402

# The chatbot/frontend use ordinary service terms; the recommender uses care-need IDs.
_SERVICE_ALIASES = {
    "dialysis": "hemodialysis",
    "haemodialysis": "hemodialysis",
    "hemodialysis": "hemodialysis",
    "ct scan": "ct_scan",
    "ct": "ct_scan",
    "mri scan": "mri",
    "emergency": "emergency_department",
    "emergency care": "emergency_department",
    "cardiac catheterization": "cardiac_catheterization",
    "cardiac cath": "cardiac_catheterization",
    "eye care": "ophthalmology",
    "dentist": "dentistry",
    "orthopaedics": "orthopedics",
    "ent": "ent",
}


class RecommendationSearchService:
    """search(SearchQuery) -> list[Hospital], preserving source ranking."""

    def __init__(self, navigator: Any = None, resolver: Any = None):
        # Lazily initialize to avoid loading model/data during API import.
        self._navigator = navigator
        self._resolver = resolver

    def search(self, query) -> list[Hospital]:
        if self._navigator is None:
            # The confirmation/form flow already interprets service; no second LLM is needed.
            self._navigator = build_navigator(use_llm=False)
        if self._resolver is None:
            self._resolver = default_resolver()

        nav = self._navigator
        raw = query.service.strip().casefold().replace("-", " ")
        alias = _SERVICE_ALIASES.get(raw, raw.replace(" ", "_"))
        service_ids = {s.id for s in nav.services()}
        if alias not in service_ids:
            # Accept exact display names from the team's service catalog, too.
            by_name = {s.name.casefold(): s.id for s in nav.services()}
            alias = by_name.get(raw, alias)
        if alias not in service_ids:
            raise ValueError(f"Unsupported service: {query.service}. Supported IDs: {', '.join(sorted(service_ids))}")

        location = self._resolver.resolve(query.location)
        if location.status == "ambiguous":
            choices = ", ".join(f"{c.place}, {c.state}" for c in location.candidates)
            raise ValueError(f"Location is ambiguous; specify a state or PIN code: {choices}")
        if location.status != "found" or location.latitude is None or location.longitude is None:
            raise ValueError(location.note or "Could not find that location. Enter city, state or PIN code.")

        result = nav.find_service(ServiceRequest(
            service=alias,
            latitude=location.latitude,
            longitude=location.longitude,
            max_results=5,
        ))
        if result.degraded:
            # Don't turn missing real data into an apparently valid empty search.
            raise RuntimeError(
                "Recommendation data unavailable: " + ", ".join(result.degraded_reasons)
                + ". Configure NAV_DATA_DIR or NAV_* files."
            )

        hospitals: list[Hospital] = []
        for rec in result.recommendations:
            matched = [m for m in rec.matched_care_needs if m.status in {"CONFIRMED", "LIKELY"}]
            # Respect whether the TEAM considers this recommendation verified vs fallback.
            if rec.list == "verified" and matched and all(m.status == "CONFIRMED" for m in matched):
                trust = TrustLabel.CONFIRMED
            elif rec.list == "verified" and matched:
                trust = TrustLabel.LIKELY
            else:
                trust = TrustLabel.CLAIMED_ONLY

            evidence = [
                Evidence(
                    text=e.quote,
                    source_field=e.field,
                    source_url=e.source_url,
                    status=EvidenceStatus.SUPPORTING,
                )
                for m in matched
                for e in m.evidence
            ]
            warnings = list(rec.warnings)
            if result.coverage_note and result.coverage_note not in warnings:
                warnings.append(result.coverage_note)
            if rec.list == "unverified_fallback":
                if "Capability not verified; call ahead." not in warnings:
                    warnings.append("Capability not verified; call ahead.")

            hospitals.append(Hospital(
                facility_id=rec.hospital_id,
                name=rec.hospital_name or f"Facility {rec.hospital_id}",
                latitude=rec.latitude,
                longitude=rec.longitude,
                distance_km=rec.straight_line_distance_km,
                distance_type="straight_line",
                phone_number=rec.listed_phone,
                facility_level=rec.facility_type,
                score=None,  # Source model uses tiered ranking, NOT a numeric 0-100 score.
                score_breakdown={},
                evidence=evidence,
                trust_label=trust,
                warnings=warnings,
                explanation=rec.explanation,
            ))
        return hospitals


class PreserveRecommendationRanking:
    """Do not re-rank: the data team's recommender already orders its results."""

    def rank(self, hospitals: list[Hospital], query) -> list[Hospital]:
        return hospitals
