"""Care-need mapping from configs/care_needs.yaml. SYNTHETIC inputs only."""

import pytest
import yaml

from hackathon.navigation.care_needs import DEFAULT_CARE_NEEDS, CareNeedTable
from hackathon.navigation.extract import RuleExtractor
from hackathon.navigation.red_flags import RedFlagRules

RULES = RedFlagRules.load()
TABLE = CareNeedTable.load()
RX = RuleExtractor(TABLE.terms, RULES)


def needs_for(message):
    a, s = RX.extract(message), RULES.screen(message)
    u = TABLE.urgency(a, s)
    return u, [(n.id, n.role) for n in TABLE.care_needs(a, s, u)], TABLE.care_needs(a, s, u)


@pytest.mark.parametrize("message, required, supporting", [
    ("severe pain in my lower right abdomen", ["emergency_department", "general_surgery"], ["ct_scan"]),
    ("knee hurts for months, stairs are hard", ["orthopedics"], ["mri"]),
    ("vision getting blurry over the past year", ["ophthalmology"], []),
    ("I'm on dialysis and moved to Pune", ["hemodialysis"], []),
    ("bad toothache", ["dentistry"], []),
    ("crushing chest pain", ["emergency_department", "cardiac_catheterization"], ["adult_icu_ventilation"]),
    ("snake bite on the leg", ["emergency_department"], ["adult_icu_ventilation", "anti_snake_venom", "anti_rabies"]),
])
def test_brief_examples(message, required, supporting):
    _, pairs, _ = needs_for(message)
    assert [n for n, r in pairs if r == "required"] == required
    assert [n for n, r in pairs if r == "supporting"] == supporting


def test_kb_ids_align_with_real_kb_files():
    _, _, needs = needs_for("I'm on dialysis and moved to Pune")
    assert needs[0].verification_kb == "hemodialysis"
    _, _, needs = needs_for("crushing chest pain")
    kb = {n.id: n.verification_kb for n in needs}
    assert kb["cardiac_catheterization"] == "cardiac_catheterization"
    assert kb["emergency_department"] == "emergency_department"   # KB added in Phase 3


def test_adding_a_kb_file_lights_up_the_need():
    t = CareNeedTable.load(kb_ids={"hemodialysis", "cardiac_catheterization", "emergency_department", "knee_replacement"})
    assert t.verification_kb("emergency_department") == "emergency_department"
    assert t.proxy_kbs("orthopedics") == ["knee_replacement"]


def test_every_emergency_category_includes_the_emergency_department():
    cfg = yaml.safe_load(DEFAULT_CARE_NEEDS.read_text(encoding="utf-8"))
    assert all("emergency_department" in spec["required"] for spec in cfg["categories"].values())
    assert {r.category for r in RULES.rules} == set(cfg["categories"])


def test_urgency_from_findings():
    assert needs_for("knee hurts for months")[0] == "routine"
    assert needs_for("severe pain in my knee")[0] == "urgent"
    assert needs_for("I am pregnant and have stomach pain")[0] == "urgent"
    assert needs_for("I feel bad")[0] == "unclear"


def test_unknown_need_in_config_is_rejected():
    cfg = yaml.safe_load(DEFAULT_CARE_NEEDS.read_text(encoding="utf-8"))
    cfg["categories"]["burns"]["required"].append("made_up_need")
    with pytest.raises(ValueError):
        CareNeedTable(cfg, TABLE.terms, set())
