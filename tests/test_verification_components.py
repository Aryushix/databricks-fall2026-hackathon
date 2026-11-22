"""Unit tests for the treatment-verification components. Every text here is SYNTHETIC (made up for the
tests); the repo is public and the dataset's sharing terms keep real records out of it."""

import json

import pytest

from hackathon.verification import copies
from hackathon.verification.classify import (
    CachedClassifier,
    DualPass,
    QuoteRejected,
    RuleClassifier,
    parse_llm_output,
)
from hackathon.verification.entities import FacilityProfile, check_entity, find_mentions, owns_site
from hackathon.verification.kb import PatternTagger, load_kb
from hackathon.verification.ledger import content_hash, facility_claims, split_sentences
from hackathon.verification.records import Claim, Classification


@pytest.fixture(scope="module")
def kb():
    return load_kb()


@pytest.fixture(scope="module")
def tagger(kb):
    return PatternTagger(kb)


def claim(text, field="procedure", uid="f1", context=None, source_type="extracted_record"):
    return Claim(f"{uid}:{field}:0", uid, field, 0, text, content_hash(text), "vf_extraction", f"vf:{uid}",
                 source_type, context=context)


def first(tagger, text, concept, role=None):
    ms = [m for m in tagger.tag(text, "procedure") if m.concept_id == concept and (role is None or m.role == role)]
    assert ms, f"no {role or ''} match for {concept} in {text!r}"
    return ms[0]


# ----------------------------------------------------------------------------------------- KB
def test_kb_examples_match_their_expected_kind(kb, tagger):
    for entry in kb.values():
        assert entry.review_status == "draft"
        for ex in entry.examples:
            kinds = {m.specificity_kind for m in tagger.tag(ex["text"], "procedure") if m.concept_id == entry.concept_id}
            assert ex["match"] in kinds, (entry.concept_id, ex)


def test_confusable_blocks_the_overlapping_positive(tagger):
    ms = [m for m in tagger.tag("CT coronary angiography", "procedure") if m.concept_id == "cardiac_catheterization"]
    assert [m.role for m in ms] == ["confusable"]


def test_longest_match_wins_so_a_cath_lab_is_a_prerequisite(tagger):
    m = first(tagger, "Cardiac catheterization laboratory", "cardiac_catheterization")
    assert (m.role, m.sub_id) == ("prerequisite", "cath_lab")


def test_ambiguous_term_settled_by_context(tagger):
    assert first(tagger, "Angioplasty", "cardiac_catheterization").role == "ambiguous"
    assert first(tagger, "Interventional cardiology services including angiography", "cardiac_catheterization",
                 "direct").sub_id == "ambiguous_upgraded_by_context"
    assert first(tagger, "Eye clinic with ICG and angiography", "cardiac_catheterization").role == "confusable"


def test_child_negative_does_not_negate_parent(tagger):
    assert first(tagger, "Coronary angioplasty (PTCA)", "cardiac_catheterization").negates_parent is False
    assert first(tagger, "Dialysis", "hemodialysis").negates_parent is True


def test_specialty_labels_are_prerequisites_only(tagger):
    ms = tagger.tag("interventionalCardiology", "specialties")
    assert [(m.role, m.sub_id, m.key) for m in ms] == [("prerequisite", "interventional_cardiology", True)]


# ----------------------------------------------------------------------------------------- ledger
def test_ledger_splits_fields_into_claims():
    row = {"unique_id": "f1", "description": "A 50-bed hospital. It offers dialysis.",
           "capability": json.dumps(["Dialysis unit", ""]), "procedure": None, "equipment": json.dumps(["RO plant"]),
           "specialties": json.dumps(["nephrology"]), "recency_of_page_update": "2025-03-01"}
    cs = facility_claims(row)
    assert [(c.field, c.text) for c in cs] == [
        ("description", "A 50-bed hospital."), ("description", "It offers dialysis."),
        ("capability", "Dialysis unit"), ("equipment", "RO plant"), ("specialties", "nephrology")]
    assert all(c.origin_cluster_id == "vf:f1" and c.source_date == "2025-03-01" for c in cs)
    assert split_sentences("One. Two!\nThree") == ["One.", "Two!", "Three"]
    assert content_hash("Dialysis  Unit") == content_hash("dialysis unit")


# ----------------------------------------------------------------------------------------- rule classifier
CASES = [
    ("Performs hemodialysis", "hemodialysis", "OFFERED", {}),
    ("Dialysis unit is overseen by a visiting nephrologist", "hemodialysis", "PARTNER_ON_SITE", {}),
    ("CT scan and dialysis are outsourced", "hemodialysis", "OUTSOURCED_OFF_SITE", {}),
    ("ESIC tie-up hospital providing dialysis", "hemodialysis", "OFFERED", {}),
    ("Dialysis patients are referred to the district hospital", "hemodialysis", "REFERRAL_ONLY", {"scope": "all"}),
    ("High-risk dialysis cases are referred to the medical college", "hemodialysis", "REFERRAL_ONLY",
     {"scope": "subgroup"}),
    ("Dialysis unit coming soon", "hemodialysis", "PLANNED_OR_COMING_SOON", {"temporal": "future"}),
    ("Dialysis department is to be shut down", "hemodialysis", "DISCONTINUED", {}),
    ("The dialysis unit became non-functional in 2015 and was restarted later", "hemodialysis", "OFFERED",
     {"temporal": "restarted"}),
    ("Dialysis facility not available", "hemodialysis", "NOT_OFFERED", {}),
    ("Does not have a dialysis unit", "hemodialysis", "NOT_OFFERED", {}),
    ("Facilitates kidney transplantation and dialysis when relatives are not available", "hemodialysis", "OFFERED", {}),
    ("Kidney failure cure without dialysis", "hemodialysis", "EDUCATIONAL_OR_GENERIC", {}),
    ("What is dialysis and how does it work", "hemodialysis", "EDUCATIONAL_OR_GENERIC", {}),
    ("Diploma in Dialysis Technology", "hemodialysis", "EDUCATIONAL_OR_GENERIC", {}),
    ("Free dialysis at monthly health camps", "hemodialysis", "OUTREACH_CAMP", {}),
    ("Dr. Rao is a consultant nephrologist with dialysis experience", "hemodialysis", "PERSONNEL_ONLY", {}),
    ("Listed among top dialysis centres in a blog post", "hemodialysis", "RESEARCH_OR_LISTING", {}),
    ("No dialysis services listed in the provided content", "hemodialysis", "EXTRACTOR_ARTIFACT", {}),
    ("Performs angioplasty", "cardiac_catheterization", "AMBIGUOUS", {}),
    ("Thankful for the care during my father's dialysis", "hemodialysis", "AMBIGUOUS", {}),
    ("Dialysis was performed for 900 patients in 1999", "hemodialysis", "OFFERED", {"temporal": "past"}),
]


@pytest.mark.parametrize("text,concept,expected,attrs", CASES)
def test_rule_classifier_regression(tagger, text, concept, expected, attrs):
    m = first(tagger, text, concept)
    lab = RuleClassifier().classify(claim(text), m)
    assert lab.assertion_type == expected, lab.cues
    for k, v in attrs.items():
        assert getattr(lab, k) == v
    assert lab.quote in text


def test_official_blog_page_is_educational(tagger):
    m = first(tagger, "Cardiac catheterization explained for patients", "cardiac_catheterization")
    lab = RuleClassifier().classify(claim("Cardiac catheterization explained for patients", "official_page",
                                          context="blog", source_type="official_site_page"), m)
    assert lab.assertion_type == "EDUCATIONAL_OR_GENERIC"


# ----------------------------------------------------------------------------------------- LLM output checks
def test_llm_quote_must_be_an_exact_substring():
    text = "Hemodialysis is available 24x7"
    ok = parse_llm_output(json.dumps({"assertion_type": "OFFERED", "quote": "Hemodialysis is available",
                                      "scope": "all", "temporal": "current", "entity_mentions": ["Made Up Hospital"]}),
                          text, "m", "v1")
    assert ok.assertion_type == "OFFERED" and ok.entity_mentions == ()   # invented mention dropped
    with pytest.raises(QuoteRejected):
        parse_llm_output(json.dumps({"assertion_type": "OFFERED", "quote": "Dialysis available",
                                     "scope": "all", "temporal": "current", "entity_mentions": []}), text, "m", "v1")


class _FakeModel:
    model_id, version = "fake-endpoint", "llm_v1"

    def __init__(self, kind=None, fail=False):
        self.kind, self.fail, self.calls = kind, fail, 0

    def classify(self, c, m):
        self.calls += 1
        if self.fail:
            raise QuoteRejected("bad quote")
        return Classification(self.kind, c.text, self.model_id, self.version)


def test_dual_pass_flags_disagreement_and_falls_back(tagger):
    m = first(tagger, "Dialysis coming soon", "hemodialysis")
    c = claim("Dialysis coming soon")
    agree = DualPass(RuleClassifier(), _FakeModel("PLANNED_OR_COMING_SOON")).classify(c, m)
    differ = DualPass(RuleClassifier(), _FakeModel("OFFERED")).classify(c, m)
    failed = DualPass(RuleClassifier(), _FakeModel(fail=True)).classify(c, m)
    assert not agree.disagreement
    assert differ.assertion_type == "OFFERED" and differ.disagreement and differ.rule_assertion_type == "PLANNED_OR_COMING_SOON"
    assert failed.assertion_type == "PLANNED_OR_COMING_SOON" and failed.disagreement


def test_cache_reuses_labels_by_content_hash(tagger, tmp_path):
    model = _FakeModel("OFFERED")
    cached = CachedClassifier(model, tmp_path / "labels.sqlite")
    m = first(tagger, "Dialysis", "hemodialysis")
    a = cached.classify(claim("Dialysis", uid="f1"), m)
    b = cached.classify(claim("dialysis", uid="f2"), m)     # same normalized text, another facility
    assert model.calls == 1 and cached.hits == 1
    assert a.assertion_type == b.assertion_type == "OFFERED" and b.quote == "dialysis"


# ----------------------------------------------------------------------------------------- entities
def test_entity_mentions_and_matching():
    shahi = FacilityProfile.build("f1", "Shahi Hospital", "Patna")
    assert check_entity("Patna Kidney Centre is a state-of-the-art dialysis facility", shahi).entity_match == "other"
    assert check_entity("Dialysis at Shahi Hospital, Patna", shahi).entity_match == "this"
    assert check_entity("Dialysis Centre", shahi).entity_match == "unnamed"     # a service, not an organisation
    rel = check_entity("Complex cases referred to Govind Medical College", shahi)
    assert rel.entity_match == "unnamed" and rel.related == ("Govind Medical College",)
    assert find_mentions("Cath Lab and Radiology & Diagnostic Centre") == []


def test_owns_site():
    p = FacilityProfile.build("f1", "Safe Gastro & Surgery Center", "Agra")
    assert owns_site(p, "safegastro.in", "")
    assert not owns_site(p, "cityheartcentre.com", "safe care for surgery patients")
    assert owns_site(p, "sgsc.in", "Welcome to Safe Gastro and Surgery Center")
    q = FacilityProfile.build("f2", "Asha Hospital", "Firozabad")
    assert not owns_site(q, "ashahospitalprayagraj.com", "", frozenset({"prayagraj", "firozabad"}))
    r = FacilityProfile.build("f3", "Medical College and Hospital", "Kolkata")
    assert not owns_site(r, "nrs.edu.in", "Kolkata medical college")


# ----------------------------------------------------------------------------------------- copies
def _items(uid, texts, field="procedure"):
    return [Claim(f"{uid}:{field}:{i}", uid, field, i, t, content_hash(t), "vf_extraction", f"vf:{uid}",
                  "extracted_record") for i, t in enumerate(texts)]


def test_text_copies_need_unrelated_facilities():
    long = "Provides advanced kidney dialysis with modern machines for all patients"
    names = {"a": "Alpha Hospital", "b": "Beta Clinic", "c": "Gamma Nursing Home", "d": "Alpha Hospital Pune"}
    profiles = {u: FacilityProfile.build(u, n) for u, n in names.items()}
    cs = _items("a", [long]) + _items("b", [long]) + _items("c", [long])
    assert len(copies.text_copies(cs, profiles)) == 3
    chain = _items("a", [long]) + _items("d", [long]) + _items("b", [long])   # a and d are one brand
    assert copies.text_copies(chain, profiles) == {}


def test_block_copies_catch_pasted_lists():
    block = [f"made up procedure number {w}" for w in ("one", "two", "three", "four", "five", "six")]
    names = {"a": "Alpha Hospital", "b": "Beta Clinic", "c": "Gamma Eye Hospital", "e": "Epsilon Hospital"}
    profiles = {u: FacilityProfile.build(u, n) for u, n in names.items()}
    cs = (_items("a", block + ["own item alpha"]) + _items("b", block) + _items("c", block + ["eye care item"])
          + _items("e", block[:2] + ["other thing", "more things", "even more things"]))
    out = copies.block_copies(cs, profiles)
    assert {k.split(":")[0] for k in out} == {"a", "b", "c"}
    assert not any(k.startswith("a:procedure:6") for k in out)          # its own item is not copied


def test_city_only_names_need_the_full_name():
    p = FacilityProfile.build("f1", "Patna Medical College & Hospital", "Patna")
    assert not p.matches("Netaji Subhas Medical College, Patna")
    assert p.matches("Patna Medical College & Hospital")
