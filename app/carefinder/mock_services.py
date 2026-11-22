
"""
mock_services.py

Care Finder — Application Orchestrator

Simulates:
1. Hospital search / RAG retrieval
2. Hospital ranking
3. Human representative assistance

All hospitals, evidence, distances and scores
in this file are FICTIONAL.

This module is intended ONLY for testing
the application workflow.

No external packages required.
"""

from copy import deepcopy
from uuid import uuid4

from schemas import (
    SearchQuery,
    Hospital,
    Evidence,
    EvidenceStatus,
    TrustLabel,
)


# ==========================================
# 1. CREATE FICTIONAL HOSPITAL RECORDS
# ==========================================

def create_demo_hospital(
    facility_id: str,
    name: str,
    city: str,
    distance_km: float,
    evidence_text: str,
    evidence_status: EvidenceStatus = (
        EvidenceStatus.SUPPORTING
    )
) -> Hospital:
    """
    Construct a fictional hospital.

    No actual medical facility is represented.
    All data is for demonstration purposes.
    """

    conflicting = (
        evidence_status == EvidenceStatus.CONTRADICTORY
    )

    return Hospital(
        facility_id=facility_id,
        name=name,
        city=city,
        state="Bihar" if city in (
            "Gaya", "Patna"
        ) else "Rajasthan",

        # Distances are fictional.
        distance_km=distance_km,
        distance_type="simulated",

        evidence=[
            Evidence(
                text="[FICTIONAL] " + evidence_text,
                source_field="mock_description",
                source_url=None,
                status=evidence_status
            )
        ],

        trust_label=(
            TrustLabel.CONFLICTING_INFO
            if conflicting
            else TrustLabel.CLAIMED_ONLY
        ),

        warnings=[
            "FICTIONAL DEMO DATA — not for patient use.",
            "Current service availability is unverified."
        ],

        explanation=(
            "Fictional record used to test the "
            "Care Finder recommendation workflow."
        )
    )


# ==========================================
# 2. MOCK HOSPITAL DATABASE
# ==========================================

# These records deliberately include:
# - Supported-looking fictional examples
# - Contradictory evidence
# - Different distances
# - Different geographic locations

MOCK_HOSPITALS = {

    ("dialysis", "gaya"): [

        create_demo_hospital(
            facility_id="DEMO-GAYA-001",
            name="Demo Hospital A (Fictional)",
            city="Gaya",
            distance_km=7.4,
            evidence_text=(
                "This fictional hospital lists "
                "hemodialysis services."
            )
        ),

        create_demo_hospital(
            facility_id="DEMO-GAYA-002",
            name="Demo Hospital B (Fictional)",
            city="Gaya",
            distance_km=13.0,
            evidence_text=(
                "This fictional facility lists "
                "dialysis equipment and services."
            )
        ),

        create_demo_hospital(
            facility_id="DEMO-GAYA-003",
            name="Demo Hospital C (Fictional)",
            city="Gaya",
            distance_km=3.9,
            evidence_text=(
                "The fictional record contains "
                "contradictory dialysis information."
            ),
            evidence_status=EvidenceStatus.CONTRADICTORY
        )
    ],

    ("dialysis", "patna"): [

        create_demo_hospital(
            facility_id="DEMO-PATNA-001",
            name="Demo Hospital D (Fictional)",
            city="Patna",
            distance_km=5.1,
            evidence_text=(
                "This fictional facility claims "
                "to provide hemodialysis."
            )
        ),

        create_demo_hospital(
            facility_id="DEMO-PATNA-002",
            name="Demo Hospital E (Fictional)",
            city="Patna",
            distance_km=9.2,
            evidence_text=(
                "Dialysis services appear in "
                "this fictional description."
            )
        )
    ],

    ("dialysis", "jaipur"): [

        create_demo_hospital(
            facility_id="DEMO-JAIPUR-001",
            name="Demo Hospital F (Fictional)",
            city="Jaipur",
            distance_km=8.6,
            evidence_text=(
                "This fictional hospital lists "
                "dialysis services."
            )
        )
    ],

    ("mri", "jaipur"): [

        create_demo_hospital(
            facility_id="DEMO-JAIPUR-002",
            name="Demo Imaging Centre (Fictional)",
            city="Jaipur",
            distance_km=11.2,
            evidence_text=(
                "This fictional facility lists "
                "MRI imaging equipment."
            )
        )
    ]
}


# ==========================================
# 3. MOCK SEARCH / RAG SERVICE
# ==========================================

class MockSearchService:
    """
    Simulates a facility retrieval service.

    Future replacement:
        Real Databricks RAG / hybrid search.

    Interface:
        search(SearchQuery) -> list[Hospital]
    """

    def __init__(self, fail: bool = False):
        self.fail = fail

        # Used to demonstrate when searches occur.
        self.search_calls: list[SearchQuery] = []

    def search(
        self,
        query: SearchQuery
    ) -> list[Hospital]:

        self.search_calls.append(deepcopy(query))

        # Simulate a backend failure.
        if self.fail:
            raise RuntimeError(
                "Simulated search service failure"
            )

        # Normalize the search values.
        service = query.service.strip().casefold()

        location = query.location.strip().casefold()

        # Allow input such as "Gaya, Bihar".
        location = location.split(",")[0].strip()

        search_key = (service, location)

        # Return an empty list when the
        # fictional test database has no match.
        results = MOCK_HOSPITALS.get(
            search_key,
            []
        )

        # Return copies so ranking cannot
        # modify our original mock records.
        return deepcopy(results)


# ==========================================
# 4. FICTIONAL RANKING SCORES
# ==========================================

# These are fixed demonstration values.
# They are NOT clinical confidence scores.
# They are NOT calculated from real evidence.

MOCK_SCORE_BREAKDOWNS = {

    "DEMO-GAYA-001": {
        "service_match": 36,
        "accessibility": 23,
        "reliability": 17,
        "readiness": 10
    },

    "DEMO-GAYA-002": {
        "service_match": 34,
        "accessibility": 16,
        "reliability": 17,
        "readiness": 8
    },

    "DEMO-PATNA-001": {
        "service_match": 35,
        "accessibility": 22,
        "reliability": 16,
        "readiness": 9
    },

    "DEMO-PATNA-002": {
        "service_match": 33,
        "accessibility": 18,
        "reliability": 16,
        "readiness": 8
    },

    "DEMO-JAIPUR-001": {
        "service_match": 34,
        "accessibility": 20,
        "reliability": 16,
        "readiness": 9
    },

    "DEMO-JAIPUR-002": {
        "service_match": 37,
        "accessibility": 18,
        "reliability": 17,
        "readiness": 11
    }
}


# ==========================================
# 5. MOCK RANKING SERVICE
# ==========================================

class MockRankingService:
    """
    Simulates ranking retrieved facilities.

    Future replacement:
        Real evidence and referral-level
        ranking engine.

    Interface:
        rank(hospitals, query) -> list[Hospital]

    This is only a workflow simulation.
    """

    def __init__(self):
        self.ranking_calls = 0

    def rank(
        self,
        hospitals: list[Hospital],
        query: SearchQuery
    ) -> list[Hospital]:

        self.ranking_calls += 1

        ranked = []

        for hospital in deepcopy(hospitals):

            # For demo purposes, exclude records
            # explicitly flagged as contradictory.
            #
            # The real ranking engine will have
            # more nuanced eligibility rules.
            has_contradiction = any(
                evidence.status
                == EvidenceStatus.CONTRADICTORY
                for evidence in hospital.evidence
            )

            if has_contradiction:
                continue

            breakdown = MOCK_SCORE_BREAKDOWNS.get(
                hospital.facility_id
            )

            if breakdown is None:
                # Unknown mock entries must not
                # receive an invented score.
                hospital.warnings.append(
                    "No demo ranking score available."
                )
                hospital.score = None
                hospital.score_breakdown = {}

            else:
                hospital.score_breakdown = dict(
                    breakdown
                )

                hospital.score = float(
                    sum(breakdown.values())
                )

            ranked.append(hospital)

        # Respect the mock sort preference.
        sort_by = query.preferences.get(
            "sort_by",
            "score"
        )

        if sort_by == "distance":

            ranked.sort(
                key=lambda h: (
                    h.distance_km
                    if h.distance_km is not None
                    else float("inf")
                )
            )

        else:

            ranked.sort(
                key=lambda h: (
                    -h.score if h.score is not None
                    else float("inf"),
                    h.distance_km
                    if h.distance_km is not None
                    else float("inf")
                )
            )

        return ranked


# ==========================================
# 6. MOCK REPRESENTATIVE ASSISTANCE
# ==========================================

class MockAssistanceService:
    """
    Simulates requesting human assistance.

    Future replacement:
        Real assistance queue, representative
        authentication and shared sessions.

    No real representative is contacted.
    """

    def __init__(self):

        # Maps user session IDs to fictional
        # assistance session IDs.
        self.active_requests: dict[str, str] = {}

    def request_assistance(
        self,
        session_id: str
    ) -> str:
        """
        Create a fictional assistance request.

        Calling again with the same session
        returns the existing request ID.
        """

        if not session_id or not session_id.strip():
            raise ValueError(
                "session_id cannot be empty"
            )

        if session_id in self.active_requests:
            return self.active_requests[session_id]

        assistance_id = (
            "DEMO-HELP-" + uuid4().hex[:10]
        )

        self.active_requests[session_id] = (
            assistance_id
        )

        return assistance_id

    def end_assistance(
        self,
        session_id: str
    ) -> bool:
        """
        End a fictional assistance request.

        Returns True if one existed.
        """

        if session_id in self.active_requests:

            del self.active_requests[session_id]

            return True

        return False
