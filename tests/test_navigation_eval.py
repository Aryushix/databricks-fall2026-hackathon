"""Evaluation pipeline on SYNTHETIC cases. Gates: emergency recall 100%, no under-triage, consistency 100%."""

from nav_synthetic import P, at, facility, record, store

from hackathon.navigation import evaluate as ev
from hackathon.navigation.care_needs import CareNeedTable
from hackathon.navigation.extract import make_extractor
from hackathon.navigation.rank import NavigationRanker
from hackathon.navigation.red_flags import RedFlagRules
from hackathon.navigation.service import Navigator, load_nav_config

CFG, RULES, TABLE = load_nav_config(), RedFlagRules.load(), CareNeedTable.load()
CASES = ev.load_cases()


def navigator(st=None):
    return Navigator(RULES, CFG, extractor=make_extractor(CFG, TABLE.terms, RULES, use_llm=False), mapper=TABLE,
                     store_provider=(lambda: st) if st is not None else None, ranker=NavigationRanker(CFG, TABLE))


def test_red_flag_set_and_labeled_cases_gate():
    rf = ev.red_flag_set(RULES)
    assert rf["recall"] == 1.0
    tn = ev.triage_and_needs(CASES, navigator())
    assert tn["emergency_recall_cases"]["recall"] == 1.0
    assert tn["urgency"]["under"] == []                 # never less urgent than labeled
    assert tn["care_needs"]["required"]["recall"] is not None


def test_extraction_accuracy_is_reported_per_field():
    per = ev.extraction_accuracy(CASES, make_extractor(CFG, TABLE.terms, RULES, use_llm=False))
    assert {"body_locations", "severity", "special_context"} <= set(per)
    assert all(0 <= s["accuracy"] <= 1 for s in per.values())


def test_capability_consistency_and_judgment_export_on_a_synthetic_store():
    facs = [facility(f"h{i}", 2 + 3 * i) for i in range(6)] + [facility("d", 4, ftype="dentist")]
    recs = [record(f"h{i}", "hemodialysis", s, c, 2 + 3 * i) for i, (s, c) in
            enumerate([("CONFIRMED", 0.95), ("LIKELY", 0.7), ("CONTRADICTED", 0.9), ("NOT CONFIRMED", 0.6),
                       ("UNVERIFIED", None), ("LIKELY", 0.85)])]
    recs += [record(f"h{i}", "cardiac_catheterization", "LIKELY", 0.7, 2 + 3 * i) for i in (1, 4)]
    locs = [("synthetic A", *P), ("synthetic B", *at(10)), ("synthetic C", *at(-20))]
    cap, responses = ev.capability_and_coverage(CASES, navigator(store(facs, recs)), locs)
    assert cap["consistency"] == 1.0 and cap["violations"] == []
    assert cap["coverage_by_case"]["c04"]["share_within_radius"] == 1.0
    rows = ev.judgment_rows(responses, n_cases=5)
    assert rows and len({r["case_id"] for r in rows}) == 5
    assert "h2" not in {r["hospital_id"] for r in rows if r["case_id"] in ("c04", "c27", "c30")}   # CONTRADICTED
    assert "not clinical correctness" in ev.report_markdown({"extractor": "rules", "red_flags": ev.red_flag_set(RULES),
                                                              "triage_and_needs": ev.triage_and_needs(CASES[:3], navigator()),
                                                              "extraction": {}, "capability": cap})
