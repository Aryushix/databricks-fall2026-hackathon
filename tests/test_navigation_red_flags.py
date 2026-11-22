"""Red-flag screen: recall gate (100%), negation, over-triage report. SYNTHETIC phrasings only."""

from pathlib import Path

import pytest
import yaml

from hackathon.navigation.red_flags import RedFlagRules, higher, normalize

CASES = yaml.safe_load((Path(__file__).parent / "fixtures" / "navigation" / "red_flag_cases.yaml").read_text(encoding="utf-8"))
RULES = RedFlagRules.load()
POSITIVES = [(cat, m) for cat, msgs in CASES["positives"].items() for m in msgs]
BRIEF_CATEGORIES = {"possible_heart_attack", "possible_stroke", "breathing_difficulty", "major_trauma_bleeding", "burns",
                    "pregnancy_emergency", "newborn_child_emergency", "unconscious_seizure", "poisoning_overdose_bites",
                    "severe_allergic_reaction", "severe_abdominal_pain", "self_harm"}


def test_every_category_has_at_least_ten_phrasings_and_a_rule():
    assert set(CASES["positives"]) == BRIEF_CATEGORIES
    assert all(len(v) >= 10 for v in CASES["positives"].values())
    assert BRIEF_CATEGORIES <= {r.category for r in RULES.rules}
    assert RULES.review_status == "draft"


@pytest.mark.parametrize("category, message", POSITIVES)
def test_recall_gate(category, message):
    """CI gate: every synthetic emergency phrasing must fire its category."""
    assert category in RULES.screen(message).categories, message


@pytest.mark.parametrize("message, fires", [
    ("no chest pain", False),
    ("I don't have chest pain", False),
    ("seene mein dard nahi hai", False),
    ("सीने में दर्द नहीं है", False),
    ("not short of breath", False),
    ("there is no bleeding, only a small bruise", False),
    ("pregnant, no bleeding", False),
    ("baby is feeding well, no fever", False),
    # err toward firing when the negation isn't clearly about the symptom
    ("no relief from chest pain", True),
    ("not just chest pain, my arm hurts too", True),
    ("no chest pain but my left arm and jaw hurt and I am sweating", True),
    ("chest pain? no", True),
    ("no fever, chest pain since morning", True),
    ("bachcha doodh nahi pi raha", True),        # Hindi word order: the cue negates the symptom, not "bachcha"
    ("I can't breathe", True),                   # "can't" is part of the symptom
    ("saans nahi aa rahi", True),
    ("I am not suicidal", True),                 # self-harm is never negated
])
def test_negation(message, fires):
    assert bool(RULES.screen(message).fired) is fires, message


def test_confusables_do_not_fire():
    for m in ["heartburn after spicy food", "burning sensation when I pass urine", "pet mein halka dard hai"]:
        assert not RULES.screen(m).fired, m


def test_floors_and_crisis():
    s = RULES.screen("severe pain in my lower right abdomen")
    assert s.floor == "urgent" and s.rule_ids == ["ABD_SEVERE_PAIN"]
    assert RULES.screen("severe stomach pain and I fainted").floor == "emergency"
    assert RULES.screen("burnt my finger on the stove").floor == "urgent"
    s = RULES.screen("I want to die")
    assert s.crisis and s.floor == "emergency"


def test_levels_and_normalisation():
    assert higher("urgent", "emergency") == "emergency" and higher(None, "routine") == "routine"
    assert higher("urgent", None) == "urgent" and higher("unclear", "routine") == "routine"
    assert normalize("  CHEST’s   Pain ") == "chest's pain"
    assert not RULES.screen("").fired and not RULES.screen("\x00￿" * 50).fired


def test_over_triage_report():
    """Reported, not gated (brief s.9): share of non-emergency phrasings that fire any rule."""
    fired = [m for m in CASES["negatives"] if RULES.screen(m).fired]
    rate = len(fired) / len(CASES["negatives"])
    print(f"over-triage on {len(CASES['negatives'])} synthetic non-emergency messages: {rate:.0%} {fired}")
