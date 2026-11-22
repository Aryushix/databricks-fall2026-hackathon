"""End-to-end regression suite on a SYNTHETIC fixture: one made-up facility per verification status.

Nothing here comes from the real dataset. The fixture exercises the whole path (ledger, official pages,
tagging, labels, copies, scoring, rules, output contract) and pins one expected status per facility.
"""

import json
import sqlite3
import zlib

import pandas as pd
import pytest

from hackathon.verification.pipeline import Verifier

pytest.importorskip("bs4")

CONTRACT = {"hospital_id", "hospital_name", "target_treatment", "normalized_treatment", "verification_status",
            "reason_codes", "confidence_score", "confidence_basis", "independent_origin_count", "direct_evidence",
            "supporting_evidence", "contradictory_evidence", "missing_prerequisites", "reasoning_summary",
            "requires_manual_review", "review_reasons", "pipeline_version", "run_timestamp"}
EVIDENCE = {"claim_id", "quote", "field", "assertion_type", "concept_ids", "source_url", "source_type", "lineage",
            "origin_cluster_id", "source_date", "retrieval_date", "entity_match", "relevance_score",
            "classifier_model", "classifier_version"}

COPIED = "Provides advanced kidney dialysis with modern machines for every patient"


def fac(uid, name, city, ftype="hospital", description="", capability=(), procedure=(), equipment=(),
        specialties=(), domain=None, content=None):
    return {"unique_id": uid, "name": name, "address_city": city, "address_stateOrRegion": "Maharashtra",
            "address_zipOrPostcode": "411001", "facilityTypeId": ftype, "description": description,
            "capability": json.dumps(list(capability)), "procedure": json.dumps(list(procedure)),
            "equipment": json.dumps(list(equipment)), "specialties": json.dumps(list(specialties)),
            "source_urls": json.dumps([f"https://{domain}/"] if domain else ["https://www.practo.com/x"]),
            "officialWebsite_domain": domain, "recency_of_page_update": None,
            "source_content_id": content or f"content-{uid}", "dq_flags": None, "location_status": "verified",
            "location_confidence": "high", "location_evidence": "synthetic", "resolved_latitude": "18.5",
            "resolved_longitude": "73.8"}


FACILITIES = [
    # CONFIRMED: the facility's own site offers it, the extracted record agrees, key prerequisites documented
    fac("confirmed", "Kaveri Heart Hospital", "Pune", domain="kaverihearthospital.example",
        capability=["Offers cardiac catheterization", "24x7 cath lab"], specialties=["interventionalCardiology"]),
    # LIKELY: one extracted direct claim plus a key prerequisite
    fac("likely", "Narmada Hospital", "Nashik", capability=["Performs coronary angiography"],
        equipment=["Cath lab"]),
    # LIKELY from prerequisites alone: cath lab plus interventional cardiology, no claim about the procedure
    fac("prereqs", "Tapti Hospital", "Surat", equipment=["Biplane cath lab"], specialties=["interventionalCardiology"]),
    # NOT CONFIRMED: only a confusable (CT angiography) and an educational mention
    fac("confusable", "Godavari Imaging", "Nagpur", ftype="clinic",
        procedure=["CT coronary angiography", "What is cardiac catheterization?"]),
    # NOT CONFIRMED: the claim is about another facility
    fac("other", "Krishna Hospital", "Satara", capability=["Yamuna Dialysis Centre offers hemodialysis"]),
    # NOT CONFIRMED: a copied listing shared by unrelated facilities
    fac("copy1", "Indus Hospital", "Akola", procedure=[COPIED]),
    fac("copy2", "Sutlej Clinic", "Wardha", ftype="clinic", procedure=[COPIED]),
    fac("copy3", "Beas Nursing Home", "Latur", ftype="clinic", procedure=[COPIED]),
    # CONTRADICTED: an unopposed, explicit negative
    fac("contra", "Chenab Hospital", "Sangli", capability=["Dialysis facility not available"]),
    # NOT CONFIRMED: positive and negative claims of equal standing in one record
    fac("conflict", "Ravi Hospital", "Jalna", capability=["Dialysis unit", "Dialysis department is to be shut down"]),
    # UNVERIFIED: nothing about either treatment
    fac("nothing", "Kosi Hospital", "Beed", capability=["General surgery", "Maternity ward"]),
]
EXPECTED = {
    ("confirmed", "cardiac_catheterization"): "CONFIRMED",
    ("likely", "cardiac_catheterization"): "LIKELY",
    ("prereqs", "cardiac_catheterization"): "LIKELY",
    ("confusable", "cardiac_catheterization"): "NOT CONFIRMED",
    ("other", "hemodialysis"): "NOT CONFIRMED",
    ("copy1", "hemodialysis"): "NOT CONFIRMED",
    ("contra", "hemodialysis"): "CONTRADICTED",
    ("conflict", "hemodialysis"): "NOT CONFIRMED",
    ("nothing", "cardiac_catheterization"): "UNVERIFIED",
    ("nothing", "hemodialysis"): "UNVERIFIED",
}


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("synthetic")
    db = sqlite3.connect(tmp / "crawl.sqlite")
    db.executescript("""CREATE TABLE pages (url TEXT PRIMARY KEY, host TEXT, status INTEGER, content_type TEXT,
                        fetched_at TEXT, html BLOB, error TEXT, final_url TEXT);
                        CREATE TABLE results (unique_id TEXT PRIMARY KEY, website TEXT, status TEXT);""")
    html = ("<html><nav><a>Home</a><a>Cardiac Catheterization</a></nav><main><h1>Kaveri Heart Hospital</h1>"
            "<p>Our cath lab performs cardiac catheterization and coronary angioplasty around the clock.</p>"
            "</main></html>")
    db.execute("INSERT INTO pages VALUES (?,?,?,?,?,?,?,?)", ("https://kaverihearthospital.example/", "kaverihearthospital.example",
               200, "text/html", "2026-11-21T00:00:00+00:00", zlib.compress(html.encode()), None, None))
    db.execute("INSERT INTO results VALUES (?,?,?)", ("confirmed", "https://kaverihearthospital.example", "ok"))
    db.commit()
    db.close()
    df = pd.DataFrame(FACILITIES).astype(object)
    df = df.where(df.notna(), None)
    v = Verifier().build(df, tmp / "crawl.sqlite")
    return v, {(r["hospital_id"], r["normalized_treatment"]["concept_id"]): r for r in v.run()}


@pytest.mark.parametrize("key,status", sorted(EXPECTED.items()))
def test_each_status(run, key, status):
    rec = run[1][key]
    assert rec["verification_status"] == status, (rec["reason_codes"], rec["reasoning_summary"])


def test_output_contract_and_quotes(run):
    v, recs = run
    texts = {r["unique_id"]: json.dumps(r) for r in FACILITIES}
    for rec in recs.values():
        assert CONTRACT <= set(rec)
        assert rec["confidence_basis"] == "heuristic_v1"
        for lst in ("direct_evidence", "supporting_evidence", "contradictory_evidence", "not_counted_evidence"):
            for ev in rec[lst]:
                assert EVIDENCE <= set(ev)
                if ev["lineage"] == "vf_extraction":   # every quote is verbatim from the input record
                    assert json.dumps(ev["quote"])[1:-1] in texts[rec["hospital_id"]]


def test_reason_codes_and_review(run):
    recs = run[1]
    confirmed = recs[("confirmed", "cardiac_catheterization")]
    assert "DIRECT_OFFICIAL" in confirmed["reason_codes"] and confirmed["independent_origin_count"] == 2
    assert "highly specialized treatment" in confirmed["review_reasons"]
    assert "COPIED_LISTING" in recs[("copy1", "hemodialysis")]["reason_codes"]
    assert "OTHER_ENTITY" in recs[("other", "hemodialysis")]["reason_codes"]
    assert "CONFLICT_UNRESOLVED" in recs[("conflict", "hemodialysis")]["reason_codes"]
    assert "PREREQS_MET" in recs[("prereqs", "cardiac_catheterization")]["reason_codes"]
    assert recs[("nothing", "hemodialysis")]["confidence_score"] is None


def test_extracted_claims_alone_never_confirm(run):
    for (uid, _), rec in run[1].items():
        if rec["verification_status"] == "CONFIRMED":
            assert any(e["source_type"] == "official_site_page" for e in rec["direct_evidence"]), uid


def test_normalize_treatment(run):
    v = run[0]
    assert v.normalize_treatment("heart catheterization") == "cardiac_catheterization"
    assert v.normalize_treatment("Haemodialysis") == "hemodialysis"
    with pytest.raises(ValueError):
        v.normalize_treatment("CT angiography")
