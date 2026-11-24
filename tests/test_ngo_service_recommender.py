"""NGO service finder: claim statuses, source trust, the 150 km radius, KB vs pillar paths. SYNTHETIC data only."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest
from nav_synthetic import P, at

from hackathon.navigation.contract import ServiceRecommendRequest
from hackathon.navigation.matcher import ServiceMatcher, load_services, term_matches
from hackathon.verification.claim_verifier import ClaimVerifier, explode_claims, tokens
from hackathon.verification.domain_trust import score_domain

SERVICES = load_services()


def fac(uid, km, name=None, ftype="hospital", desc="", urls=(), domain=None, **lists):
    lat, lon = at(km)
    row = {"unique_id": uid, "name": name or f"Synthetic Facility {uid}", "facilityTypeId": ftype,
           "resolved_latitude": str(lat), "resolved_longitude": str(lon), "description": desc,
           "source_urls": json.dumps(list(urls)), "officialWebsite_domain": domain, "officialPhone": "+911234567890",
           "dq_flags": "", "resolved_location_source": "facility_coordinates"}
    for k in ("specialties", "procedure", "equipment", "capability"):
        row[k] = json.dumps(lists.get(k, []))
    return row


def line(uid, text, url):
    return SimpleNamespace(unique_id=uid, text=text, source_type="official_site_page", source_url=url)


def kb_record(uid, kb, status, conf):
    return {"hospital_id": uid, "normalized_treatment": {"concept_id": kb}, "verification_status": status,
            "confidence_score": conf, "review_reasons": [],
            "direct_evidence": [{"quote": "Dialysis unit with 10 machines", "source_type": "official_site_page",
                                 "source_url": "https://synthetic-a.example/services"}]}


# ---------------------------------------------------------------- contract and config
def test_contract_and_catalog():
    assert "hemodialysis" in SERVICES["services"]
    req = ServiceRecommendRequest(service_id="hemodialysis", latitude=18.52, longitude=73.85, radius_km=150.0)
    assert req.radius_km == 150.0 and req.min_confidence == 0.5
    with pytest.raises(ValueError):
        ServiceRecommendRequest(service_id="hemodialysis", latitude=18.5, longitude=73.8, radius_km=300)


# ---------------------------------------------------------------- domain trust
def test_domain_trust():
    assert score_domain("https://aiims.edu.in") >= 0.9
    assert score_domain("https://www.justdial.com/x") <= 0.5
    assert score_domain("https://portal.gov.in", shared_domains={"portal.gov.in"}) == 0.5   # a portal listing many
    assert score_domain("https://synthetic-a.example", own_domain="synthetic-a.example") == 1.0
    assert score_domain("https://unknown.example") == 0.6


# ---------------------------------------------------------------- claim statuses
def test_claim_statuses():
    copied = "Advanced modular operation theatres with laminar air flow"
    facs = pd.DataFrame([
        fac("a", 5, desc="We run a dialysis unit. Cath lab services are not available; patients are referred to the city.",
            domain="synthetic-a.example", urls=["https://synthetic-a.example"],
            procedure=["Dialysis unit", "Cath lab services", "Laparoscopic hernia repair"], equipment=[copied]),
        fac("b", 6, equipment=[copied], urls=["https://www.justdial.com/b"]),
        fac("c", 7, equipment=[copied]),
    ]).replace({"": None})
    official = [line("a", "Laparoscopic hernia repair by experienced surgeons", "https://synthetic-a.example/surgery")]
    res = ClaimVerifier(facs, official).verify(explode_claims(facs))
    st = {(r.facility_id, r.evidence_text): r for r in res.itertuples()}
    assert st[("a", "Dialysis unit")].claim_status == "CORROBORATED_BY_DESC"
    assert st[("a", "Cath lab services")].claim_status == "CONTRADICTED"
    assert st[("a", "Cath lab services")].source_trust_score == 0.0
    off = st[("a", "Laparoscopic hernia repair")]
    assert off.claim_status == "VERIFIED_OFFICIAL" and off.source_trust_score == 1.0
    assert off.corroboration_quote == "Laparoscopic hernia repair by experienced surgeons"   # verbatim
    assert st[("b", copied)].claim_status == "COPIED_LISTING"
    assert st[("b", copied)].source_trust_score <= 0.5


# ---------------------------------------------------------------- matcher
def _matcher(rows, evidence=None, kb=None):
    f = pd.DataFrame(rows).replace({"": None})
    if evidence is None:
        evidence = ClaimVerifier(f, []).verify(explode_claims(f))
    return ServiceMatcher(f, evidence, kb or {})


def test_kb_service_uses_kb_records_and_150_km_boundary():
    rows = [fac("near", 20), fac("in", 149), fac("out", 151), fac("nokb", 10, procedure=["Hemodialysis"])]
    kb = {(u, "hemodialysis"): kb_record(u, "hemodialysis", "CONFIRMED", 0.95) for u in ("near", "in", "out")}
    res = _matcher(rows, kb=kb).match("hemodialysis", P[0], P[1], radius_km=150)
    ids = [r.hospital_id for r in res]
    assert ids == ["near", "in"]                                   # 151 km excluded; no KB record -> not shown
    assert all(r.verification_tier == "CONFIRMED" and r.overall_confidence == 0.95 for r in res)   # never boosted


def test_confirmed_outranks_nearer_likely():
    rows = [fac("likely_near", 2), fac("confirmed_far", 90)]
    kb = {("likely_near", "hemodialysis"): kb_record("likely_near", "hemodialysis", "LIKELY", 0.7),
          ("confirmed_far", "hemodialysis"): kb_record("confirmed_far", "hemodialysis", "CONFIRMED", 0.95)}
    assert [r.hospital_id for r in _matcher(rows, kb=kb).match("hemodialysis", *P)] == ["confirmed_far", "likely_near"]


def test_pillar_path_is_semantic_and_never_confirmed():
    rows = [fac("ortho", 10, specialties=["orthopedicSurgery"], procedure=["Knee replacement surgery"],
                urls=["https://synthetic-ortho.example"], domain="synthetic-ortho.example"),
            fac("dental", 5, specialties=["dentistry"])]
    res = _matcher(rows).match("orthopedics", *P, min_confidence=0.0)
    assert [r.hospital_id for r in res] == ["ortho"]
    assert res[0].verification_tier == "SEMANTIC_MATCH" and any("not_verified" in w for w in res[0].warnings)
    assert {c.pillar for c in res[0].matched_claims} == {"specialty", "procedure"}


def test_aggregator_only_listing_scores_lower_than_official():
    rows = [fac("official", 10, procedure=["Knee replacement surgery"], domain="synthetic-o.example",
                urls=["https://synthetic-o.example"]),
            fac("directory", 10, procedure=["Knee replacement surgery"], urls=["https://www.practo.com/x"])]
    f = pd.DataFrame(rows).replace({"": None})
    official = [line("official", "Knee replacement surgery", "https://synthetic-o.example/ortho")]
    ev = ClaimVerifier(f, official).verify(explode_claims(f))
    res = {r.hospital_id: r for r in ServiceMatcher(f, ev, {}).match("orthopedics", *P, min_confidence=0.0)}
    assert res["official"].overall_confidence > res["directory"].overall_confidence
    assert any("unverified_sources_only" in w for w in res["directory"].warnings)


def test_contradicted_claim_disqualifies():
    rows = [fac("x", 10, desc="Knee replacement surgery is not available here.", procedure=["Knee replacement surgery"],
                specialties=["orthopedicSurgery"])]
    assert _matcher(rows).match("orthopedics", *P, min_confidence=0.0) == []


def test_unknown_service():
    with pytest.raises(ValueError):
        _matcher([fac("a", 1)]).match("teleportation", *P)


@pytest.mark.parametrize("term, claim, ok", [("c section", "Emergency C-Section 24x7", True),
                                             ("dialysis", "Dialysis unit", True),
                                             ("knee replacement", "Total knee replacement", True),
                                             ("ct scanner", "CT angiography", False)])
def test_term_matching(term, claim, ok):
    assert term_matches(tokens(term), tokens(claim)) is ok
