"""Tests for LLM Query Expansion, Vector Semantic Similarity, and Hybrid Service Matching."""

import pytest

from hackathon.navigation.contract import ServiceRequest
from hackathon.navigation.embeddings import (
    compute_pillar_similarity,
    compute_semantic_similarity,
)
from hackathon.navigation.matcher import ServiceMatcher
from hackathon.navigation.query_expansion import expand_service_query
from hackathon.navigation.service import build_navigator


def test_query_expansion_lay_to_clinical():
    """Verify that lay phrases expand into 4 clinical pillars and medical synonyms."""
    # 1. Kidney dialysis lay query
    exp_renal = expand_service_query("kidney dialysis", use_llm=False)
    assert any("renal" in s.lower() or "hemo" in s.lower() for s in exp_renal.clinical_synonyms + exp_renal.procedures)
    assert any("nephro" in s.lower() or "renal" in s.lower() for s in exp_renal.specialties)
    assert any("dialy" in e.lower() for e in exp_renal.equipment)

    # 2. Heart bypass lay query
    exp_heart = expand_service_query("heart bypass", use_llm=False)
    assert any("cabg" in s.lower() or "coronary" in s.lower() for s in exp_heart.clinical_synonyms + exp_heart.procedures)
    assert any("cardio" in s.lower() or "ctvs" in s.lower() for s in exp_heart.specialties)


def test_semantic_cosine_similarity_synonyms():
    """Verify that semantic similarity scores clinical synonyms high and unrelated low."""
    # Clinical synonyms (zero token overlap)
    sim_dialysis = compute_semantic_similarity("kidney dialysis", "Renal replacement therapy unit")
    assert sim_dialysis >= 0.80

    sim_bypass = compute_semantic_similarity("heart bypass", "CABG revascularization")
    assert sim_bypass >= 0.80

    sim_chemo = compute_semantic_similarity("cancer chemo", "Medical oncology daycare")
    assert sim_chemo >= 0.80

    # Unrelated services
    sim_unrelated = compute_semantic_similarity("kidney dialysis", "Dermatology cosmetic laser clinic")
    assert sim_unrelated < 0.30


def test_pillar_similarity():
    """Test comparing lists of requirements against facility claims."""
    req_equip = ["hemodialysis machine", "ro water plant"]
    fac_equip = ["Fresenius Dialysis Machine Model 4008S", "Digital X-Ray"]

    score, matched = compute_pillar_similarity(req_equip, fac_equip)
    assert score >= 0.45
    assert len(matched) >= 1
    assert any("Dialysis" in m for m in matched)


def test_hybrid_service_matcher_synonym_resolution():
    """Test that lay queries with zero literal overlap successfully retrieve matching facilities within 150 km."""
    nav = build_navigator(use_llm=False)
    store = nav.store_provider()
    matcher = ServiceMatcher()

    # Query 'kidney filtration' at Pune coordinates (18.5204, 73.8567)
    # The facility lists specialties: ['cardiology', 'nephrology', 'emergencyMedicine', 'radiology']
    recs = matcher.match_facilities(
        store,
        "kidney filtration",
        18.5204,
        73.8567,
        radius_km=150.0,
        max_results=3,
        use_llm_expansion=False
    )
    assert len(recs) > 0
    top = recs[0]
    assert top.straight_line_distance_km <= 150.0
    assert top.capability_match is True
    # Matched care need should be confident
    assert top.matched_care_needs[0].confidence >= 0.40


def test_navigator_find_service_with_lay_synonyms():
    """Test the complete find_service pipeline with lay terminology."""
    nav = build_navigator(use_llm=False)

    # 1. Heart bypass (lay) -> should match cardiology facility
    resp_heart = nav.find_service(
        ServiceRequest(service="heart bypass", latitude=18.5204, longitude=73.8567, max_results=2)
    )
    assert len(resp_heart.recommendations) > 0
    assert resp_heart.recommendations[0].capability_match is True

    # 2. Cancer chemo (lay) -> test execution with graceful handling
    resp_chemo = nav.find_service(
        ServiceRequest(service="cancer chemo", latitude=18.5204, longitude=73.8567, max_results=2)
    )
    assert resp_chemo.search.radius_km == 150.0
