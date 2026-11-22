"""Capability filter, distance and ranking (query mode 3 extension). SYNTHETIC data only."""

import json
import random

import pytest
from nav_synthetic import P, facility, record, store

from hackathon.cleaning import haversine_km
from hackathon.navigation.care_needs import CareNeedTable
from hackathon.navigation.contract import CareNeed, RecommendRequest
from hackathon.navigation.rank import NavigationRanker
from hackathon.navigation.service import in_coverage, load_nav_config
from hackathon.navigation.store import DataError, FileCapabilityStore
from hackathon.verification.query import mode1, mode2_profile, mode3_rank

CFG = load_nav_config()
TABLE = CareNeedTable.load()
RANKER = NavigationRanker(CFG, TABLE)
DIALYSIS = [CareNeed(id="hemodialysis", role="required", verification_kb="hemodialysis")]
HEART = [CareNeed(id="emergency_department", role="required"),
         CareNeed(id="cardiac_catheterization", role="required", verification_kb="cardiac_catheterization"),
         CareNeed(id="adult_icu_ventilation", role="supporting")]


def rank(st, needs=DIALYSIS, urgency="routine", max_results=5):
    return RANKER.rank(st, needs, urgency, P[0], P[1], max_results)


# ---------------------------------------------------------------- distance and coordinates
@pytest.mark.parametrize("a, b, km", [((51.5074, -0.1278), (48.8566, 2.3522), 343.5),     # London-Paris
                                      ((0.0, 0.0), (1.0, 0.0), 111.19),                   # one degree of latitude
                                      ((28.6139, 77.2090), (19.0760, 72.8777), 1150.0)])  # Delhi-Mumbai
def test_haversine_against_known_distances(a, b, km):
    assert float(haversine_km(a[0], a[1], b[0], b[1])) == pytest.approx(km, rel=0.015)


def test_coordinate_validation():
    bbox = CFG["coverage"]["bbox"]
    assert in_coverage(19.07, 72.88, bbox) and in_coverage(11.62, 92.73, bbox)        # Mumbai, Port Blair
    assert not in_coverage(0, 0, bbox) and not in_coverage(6.93, 79.85, bbox) and not in_coverage(51.5, -0.1, bbox)
    for bad in [{"latitude": float("nan"), "longitude": 72.0}, {"latitude": 95, "longitude": 72.0},
                {"latitude": 19.0, "longitude": 181}]:
        with pytest.raises(ValueError):
            RecommendRequest(patient_message="knee pain", **bad)


# ---------------------------------------------------------------- query modes
def test_query_modes():
    recs = [record("a", "hemodialysis", "LIKELY", 0.7, 10), record("b", "hemodialysis", "CONFIRMED", 0.95, 30),
            record("a", "cardiac_catheterization", "UNVERIFIED", None, 10), record("c", "hemodialysis", "LIKELY", 0.7, 5)]
    assert mode1(recs, "a", "hemodialysis")["verification_status"] == "LIKELY"
    assert [r["normalized_treatment"]["concept_id"] for r in mode2_profile(recs, "a")] == ["hemodialysis"]
    rows = mode3_rank(recs, "hemodialysis", *P, radius_km=40)
    assert [r["hospital_id"] for r in rows] == ["b", "c", "a"]           # status, confidence, then distance
    assert [r["hospital_id"] for r in mode3_rank(recs, "hemodialysis", *P, radius_km=20)] == ["c", "a"]


# ---------------------------------------------------------------- status policy
def test_contradicted_never_appears_anywhere():
    facs = [facility("near", 2), facility("mid", 8), facility("far", 15)]
    recs = [record("near", "hemodialysis", "CONTRADICTED", 0.9, 2), record("mid", "hemodialysis", "LIKELY", 0.7, 8),
            record("far", "hemodialysis", "UNVERIFIED", None, 15)]
    for urgency in ("routine", "urgent", "emergency"):
        recs_out, *_ = rank(store(facs, recs), urgency=urgency)
        assert "near" not in [r.hospital_id for r in recs_out]
        assert all(m.status in ("CONFIRMED", "LIKELY") for r in recs_out for m in r.matched_care_needs)


def test_not_confirmed_and_unverified_are_never_capability_matches():
    facs = [facility("nc", 1), facility("uv", 2), facility("lk", 20)]
    recs = [record("nc", "hemodialysis", "NOT CONFIRMED", 0.8, 1), record("uv", "hemodialysis", "UNVERIFIED", None, 2),
            record("lk", "hemodialysis", "LIKELY", 0.7, 20)]
    out, *_ = rank(store(facs, recs))
    verified = [r for r in out if r.list == "verified"]
    assert [r.hospital_id for r in verified] == ["lk"] and verified[0].capability_match
    fb = [r for r in out if r.list == "unverified_fallback"]
    assert {r.hospital_id for r in fb} == {"nc", "uv"} and not any(r.capability_match for r in fb)
    assert all("Capability not verified: call ahead." in r.warnings for r in fb)


def test_emergency_property_farther_confirmed_outranks_nearer_non_match_randomised():
    rnd = random.Random(20261122)
    for _ in range(200):
        facs, recs = [], []
        for i in range(8):
            km = rnd.uniform(0.5, 24)
            status = rnd.choice(["CONFIRMED", "LIKELY", "NOT CONFIRMED", "UNVERIFIED"])
            conf = None if status == "UNVERIFIED" else round(rnd.uniform(0.3, 0.99), 3)
            facs.append(facility(f"f{i}", km))
            recs.append(record(f"f{i}", "cardiac_catheterization", status, conf, km))
        out, *_ = rank(store(facs, recs), needs=HEART, urgency="emergency")
        seen_non_match = False
        for r in out:
            if r.capability_match:
                assert not seen_non_match, "a verified match ranked below a non-match"
            else:
                seen_non_match = True
        statuses = [r.matched_care_needs[0].status for r in out if r.capability_match]
        assert statuses == sorted(statuses, key=lambda s: s != "CONFIRMED")   # every CONFIRMED before any LIKELY


def test_bands_emergency_vs_routine():
    facs = [facility("near", 3), facility("far", 12)]
    recs = [record("near", "hemodialysis", "LIKELY", 0.60, 3), record("far", "hemodialysis", "LIKELY", 0.72, 12)]
    em, *_ = rank(store(facs, recs), urgency="emergency")
    assert [r.hospital_id for r in em if r.list == "verified"] == ["far", "near"]     # band 0.65 separates them
    rt, *_ = rank(store(facs, recs), urgency="routine")
    assert [r.hospital_id for r in rt if r.list == "verified"] == ["near", "far"]     # same band: distance decides


def test_more_required_needs_matched_ranks_first():
    needs = [CareNeed(id="hemodialysis", role="required", verification_kb="hemodialysis"),
             CareNeed(id="cardiac_catheterization", role="required", verification_kb="cardiac_catheterization")]
    facs = [facility("one", 2), facility("both", 9)]
    recs = [record("one", "hemodialysis", "CONFIRMED", 0.95, 2), record("both", "hemodialysis", "LIKELY", 0.7, 9),
            record("both", "cardiac_catheterization", "LIKELY", 0.7, 9)]
    out, *_ = rank(store(facs, recs), needs=needs)
    assert [r.hospital_id for r in out if r.list == "verified"] == ["both", "one"]
    assert out[1].unmatched_required_needs == ["cardiac_catheterization"]


# ---------------------------------------------------------------- radius, fallback, duplicates
def test_widen_once_and_say_so():
    facs = [facility("far", 40)]
    recs = [record("far", "hemodialysis", "LIKELY", 0.7, 40)]
    out, search, note, _ = rank(store(facs, recs), urgency="emergency")
    assert search.widened and search.radius_km == 50 and out[0].hospital_id == "far"
    assert "widened once to 50 km" in note
    out, search, note, _ = rank(store([facility("x", 80)], [record("x", "hemodialysis", "LIKELY", 0.7, 80)]), urgency="emergency")
    assert out == [] and "No facility in our data is within 50 km" in note


def test_fallback_always_for_emergency_and_capped_at_three():
    facs = [facility(f"h{i}", 1 + i) for i in range(6)] + [facility("v", 10)]
    recs = [record("v", "cardiac_catheterization", "CONFIRMED", 0.95, 10)]
    out, *_ = rank(store(facs, recs), needs=HEART, urgency="emergency", max_results=1)
    assert [r.list for r in out] == ["verified", "unverified_fallback", "unverified_fallback", "unverified_fallback"]
    out, *_ = rank(store(facs, recs), needs=DIALYSIS, urgency="routine", max_results=1)
    assert [r.list for r in out] == ["unverified_fallback"] * 3        # 0 verified < max_results: call-ahead list
    recs2 = [record("v", "hemodialysis", "CONFIRMED", 0.95, 10)]
    out, *_ = rank(store(facs, recs2), needs=DIALYSIS, urgency="routine", max_results=1)
    assert [r.list for r in out] == ["verified"]                        # verified == max_results: no fallback


def test_dentistry_fallback_uses_dentists_and_signals():
    facs = [facility("hosp", 1), facility("dent", 3, ftype="dentist", specialties=["dentistry"]), facility("clinic", 2, ftype="clinic")]
    needs = TABLE.care_needs(None, None, "routine") or [CareNeed(id="dentistry", role="required")]
    out, _, note, _ = rank(store(facs, []), needs=needs)
    assert [r.hospital_id for r in out] == ["dent", "hosp"]          # dentists first, then hospitals; no clinics
    dent = out[0]
    assert {(s.source, s.value) for s in dent.unverified_signals} == {("specialties", "dentistry"), ("facilityTypeId", "dentist")}
    assert "Dentistry can't be verified for any facility yet." in note


def test_duplicates_collapse_to_one_entry():
    facs = [facility("a", 2, dup="g1"), facility("b", 2.1, dup="g1"), facility("c", 6)]
    recs = [record("a", "hemodialysis", "LIKELY", 0.7, 2), record("b", "hemodialysis", "LIKELY", 0.7, 2.1),
            record("c", "hemodialysis", "LIKELY", 0.7, 6)]
    out, *_ = rank(store(facs, recs))
    ids = [r.hospital_id for r in out]
    assert ids.count("a") + ids.count("b") == 1 and "c" in ids
    first = out[0]
    assert first.also_listed_as == (["b"] if first.hospital_id == "a" else ["a"])


def test_facilities_without_a_duplicate_group_are_not_collapsed():
    # Real files store "no group" as an empty cell, which pandas reads as NaN (truthy). Regression test.
    facs = [facility(f"h{i}", 1 + i, dup=float("nan")) for i in range(5)] + [facility("g1a", 30, dup="g1"),
                                                                         facility("g1b", 31, dup="g1")]
    out, *_ = rank(store(facs, []), needs=HEART, urgency="emergency")
    assert [r.hospital_id for r in out] == ["h0", "h1", "h2"] and all(r.also_listed_as == [] for r in out)


def test_fallback_prefers_the_need_s_facility_types_in_order():
    facs = [facility("hosp", 1), facility("dent_far", 20, ftype="dentist")]
    out, *_ = rank(store(facs, []), needs=[CareNeed(id="dentistry", role="required")])
    assert [r.hospital_id for r in out] == ["dent_far", "hosp"]


def test_missing_coordinates_are_excluded_and_counted():
    facs = [facility("nocoords", 1, coords=False), facility("ok", 3)]
    recs = [record("ok", "hemodialysis", "LIKELY", 0.7, 3)]
    out, _, note, _ = rank(store(facs, recs))
    assert "nocoords" not in [r.hospital_id for r in out]
    assert "1 facility without usable coordinates was left out." in note


# ---------------------------------------------------------------- warnings, evidence, explanations
def test_location_status_and_review_flags_carry_through():
    facs = [facility("a", 2, location_status="conflicting")]
    recs = [record("a", "hemodialysis", "LIKELY", 0.7, 2, location_status="conflicting", review=True,
                   reasons=["the only direct evidence is an extracted claim whose source page is unknown"])]
    r = rank(store(facs, recs))[0][0]
    assert r.requires_manual_review and r.review_reasons
    assert "The source page for this listing is unknown." in r.warnings
    assert any("point to different places" in w for w in r.warnings)
    assert "Hemodialysis is likely, not confirmed." in r.warnings


def test_pin_area_coordinates_are_flagged_as_approximate():
    f = facility("a", 2)
    f["resolved_location_source"] = "pin_area"
    r = rank(store([f], [record("a", "hemodialysis", "LIKELY", 0.7, 2)]))[0][0]
    assert any("approximate" in x for x in r.warnings)


def test_evidence_is_verbatim_from_records_and_page_unknown_is_labelled():
    q = "Synthetic Dialysis Centre runs 12 machines."
    facs = [facility("a", 2, source_urls=["https://example.org/a", "https://example.org/b"])]
    recs = [record("a", "hemodialysis", "LIKELY", 0.7, 2, quotes=[(q, "description", "extracted_record", None)])]
    m = rank(store(facs, recs))[0][0].matched_care_needs[0]
    assert m.evidence[0].quote == q and m.evidence[0].page_unknown
    assert m.evidence[0].record_source_urls == ["https://example.org/a", "https://example.org/b"]


def test_proxy_kb_explanation_never_implies_the_procedure():
    table = CareNeedTable.load(kb_ids={"hemodialysis", "cardiac_catheterization", "knee_replacement"})
    ranker = NavigationRanker(CFG, table)
    needs = [CareNeed(id="orthopedics", role="required", proxy_kbs=["knee_replacement"])]
    facs = [facility("o", 4, specialties=["orthopedicSurgery"])]
    recs = [record("o", "knee_replacement", "LIKELY", 0.75, 4)]
    r = ranker.rank(store(facs, recs), needs, "routine", P[0], P[1], 5)[0][0]
    assert r.capability_match and r.matched_care_needs[0].via == "proxy_kb"
    assert "suggests a orthopedics department" in r.explanation.replace("an orthopedics", "a orthopedics")
    assert "doesn't mean you need that procedure" in r.explanation


def test_explanation_and_no_travel_time():
    facs = [facility("a", 2.345, specialties=["nephrology"])]
    recs = [record("a", "hemodialysis", "CONFIRMED", 0.95, 2.345)]
    r = rank(store(facs, recs))[0][0]
    assert r.explanation.startswith("Confirmed hemodialysis") and r.explanation.endswith("2.3 km straight-line.")
    assert r.travel is None and "fastest" not in json.dumps(r.model_dump())


# ---------------------------------------------------------------- store loading
def test_store_rejects_records_for_unknown_hospitals():
    with pytest.raises(DataError):
        store([facility("a", 1)], [record("zzz", "hemodialysis", "LIKELY", 0.7, 1)])


def test_store_from_files(tmp_path):
    import gzip

    import pandas as pd
    f = pd.DataFrame([facility("a", 2)])
    f.drop(columns=["resolved_latitude", "resolved_longitude", "location_status", "location_confidence"]).to_csv(tmp_path / "e.csv", index=False)
    f[["unique_id", "resolved_latitude", "resolved_longitude", "location_status", "location_confidence"]].to_csv(tmp_path / "v.csv", index=False)
    with gzip.open(tmp_path / "r.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(record("a", "hemodialysis", "LIKELY", 0.7, 2)) + "\n")
    st = FileCapabilityStore.from_files(tmp_path / "e.csv", tmp_path / "v.csv", tmp_path / "r.jsonl.gz")
    assert st.kb_ids() == {"hemodialysis"} and st.summary()["facilities"] == 1
    assert st.versions()["verification_run"].startswith("0.2.0-phase2#synthetic00@")
    with pytest.raises(DataError):
        FileCapabilityStore.from_files(tmp_path / "e.csv", tmp_path / "missing.csv", tmp_path / "r.jsonl.gz")
