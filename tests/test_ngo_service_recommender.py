"""Tests for the NGO Service Recommender, Claim Verification, and 150 km Matching."""

import pytest

from hackathon.navigation.contract import ServiceRequest
from hackathon.navigation.matcher import ServiceMatcher
from hackathon.navigation.service import build_navigator
from hackathon.verification.claim_verifier import verify_facility_claim
from hackathon.verification.domain_trust import score_domain


def test_domain_trust_scoring():
    # Official government / academic
    assert score_domain("https://mohfw.gov.in") == 1.0
    assert score_domain("https://aiims.edu.in") == 0.95

    # Known aggregators and directories
    assert score_domain("https://www.justdial.com/pune") == 0.50
    assert score_domain("https://www.practo.com/delhi") == 0.50
    assert score_domain("https://facebook.com/hospital") == 0.35

    # Matching facility name
    assert score_domain("https://aravind.org", facility_name="Aravind Eye Hospital") == 0.95


def test_claim_verification_engine():
    facility = {
        "unique_id": "test_1",
        "name": "City General Hospital",
        "description": "24/7 dialysis unit with modern Fresenius dialysis machines and nephrologist care.",
        "source_urls": ["https://cityhospital.org"],
        "officialWebsite": "https://cityhospital.org",
        "dup_group_id": None
    }

    # Corroborated claim
    res = verify_facility_claim("hemodialysis", "procedure", facility)
    assert res.claim_status in ("VERIFIED_OFFICIAL", "CORROBORATED_BY_DESC")
    assert res.source_trust_score >= 0.85
    assert res.corroboration_quote is not None

    # Contradicted claim
    facility_negative = {
        "unique_id": "test_2",
        "name": "Community Clinic",
        "description": "No hemodialysis services available at this branch; referred to main hospital.",
        "source_urls": ["https://justdial.com/clinic"],
        "dup_group_id": None
    }
    res_neg = verify_facility_claim("hemodialysis", "procedure", facility_negative)
    assert res_neg.claim_status == "CONTRADICTED"
    assert res_neg.source_trust_score == 0.0


def test_service_matcher_within_150km():
    nav = build_navigator(use_llm=False)
    store = nav.store_provider()
    matcher = ServiceMatcher()

    # Search hemodialysis at Pune coordinates
    recs = matcher.match_facilities(store, "hemodialysis", 18.5204, 73.8567, radius_km=150.0, max_results=5)
    assert len(recs) > 0

    # Ensure all returned facilities are within 150 km
    for r in recs:
        assert r.straight_line_distance_km <= 150.0
        assert r.matched_care_needs[0].status in ("CONFIRMED", "LIKELY", "SEMANTIC_MATCH")
        assert r.matched_care_needs[0].confidence > 0.0

    # Verify sort order: rank 1 has highest status/confidence and shortest distance
    assert recs[0].straight_line_distance_km <= recs[-1].straight_line_distance_km or recs[0].matched_care_needs[0].confidence >= recs[-1].matched_care_needs[0].confidence


def test_navigator_find_service_integration():
    nav = build_navigator(use_llm=False)
    resp = nav.find_service(ServiceRequest(service="hemodialysis", latitude=18.5204, longitude=73.8567, max_results=3))
    assert len(resp.recommendations) > 0
    top = resp.recommendations[0]
    assert top.straight_line_distance_km <= 150.0
    assert len(top.matched_care_needs) > 0
