"""The emergency block survives every later failure. SYNTHETIC inputs only."""

import pytest

from hackathon.navigation.contract import RecommendRequest, SymptomAnalysis
from hackathon.navigation.red_flags import RedFlagRules
from hackathon.navigation.service import Navigator, load_nav_config

CFG = load_nav_config()
RULES = RedFlagRules.load()
CHEST = RecommendRequest(patient_message="I have chest pain and sweating", latitude=19.07, longitude=72.88)


class Boom:
    def __init__(self, exc):
        self.exc = exc

    def extract(self, message):
        raise self.exc

    def rank(self, *a, **k):
        raise self.exc


class GoodExtractor:
    def extract(self, message):
        return SymptomAnalysis(body_locations=["chest"], severity="severe")


def _assert_emergency(resp):
    assert resp.emergency is not None and resp.emergency.call_emergency_services
    assert [n.number for n in resp.emergency.numbers][:2] == ["112", "108"]
    assert all(n.source_url.startswith("https://") for n in resp.emergency.numbers)
    assert resp.triage.urgency == "emergency" and resp.triage.rule_floor == "emergency"


def test_emergency_block_with_nothing_else_available():
    resp = Navigator(RULES, CFG).recommend(CHEST)
    _assert_emergency(resp)
    assert resp.degraded and "extraction_unavailable" in resp.degraded_reasons


@pytest.mark.parametrize("exc", [TimeoutError("llm timed out"), ValueError("not JSON: <<garbage>>")])
def test_survives_extraction_failure(exc):
    resp = Navigator(RULES, CFG, extractor=Boom(exc)).recommend(CHEST)
    _assert_emergency(resp)
    assert "extraction_failed" in resp.degraded_reasons


def test_survives_missing_data_files():
    def provider():
        raise FileNotFoundError("records.jsonl.gz")
    resp = Navigator(RULES, CFG, extractor=GoodExtractor(), store_provider=provider, ranker=Boom(RuntimeError())).recommend(CHEST)
    _assert_emergency(resp)
    assert "data_unavailable" in resp.degraded_reasons


def test_survives_ranking_exception():
    resp = Navigator(RULES, CFG, extractor=GoodExtractor(), store_provider=lambda: object(),
                     ranker=Boom(ZeroDivisionError())).recommend(CHEST)
    _assert_emergency(resp)
    assert "ranking_failed" in resp.degraded_reasons and resp.recommendations == []


def test_screen_failure_escalates():
    class BrokenRules:
        version, review_status = "x", "draft"

        def screen(self, message):
            raise RuntimeError("bug")
    resp = Navigator(BrokenRules(), CFG).recommend(CHEST)
    assert resp.emergency is not None and resp.triage.urgency == "emergency"
    assert "red_flag_screen_failed" in resp.degraded_reasons


def test_crisis_block_has_tele_manas_and_never_only_a_list():
    req = RecommendRequest(patient_message="I want to end my life", latitude=19.07, longitude=72.88)
    resp = Navigator(RULES, CFG).recommend(req)
    assert resp.emergency.crisis
    assert [n.number for n in resp.emergency.numbers] == ["112", "108", "14416"]


def test_response_never_echoes_message_or_coordinates():
    req = RecommendRequest(patient_message="chest pain QQZX-unique", latitude=19.0123456, longitude=72.8765432)
    dumped = Navigator(RULES, CFG).recommend(req).model_dump_json()
    assert "QQZX" not in dumped and "19.0123456" not in dumped and "72.8765432" not in dumped


def test_outside_coverage_still_returns_emergency_block():
    req = RecommendRequest(patient_message="I can't breathe", latitude=51.5, longitude=-0.12)
    resp = Navigator(RULES, CFG, extractor=GoodExtractor()).recommend(req)
    _assert_emergency(resp)
    assert resp.recommendations == [] and "outside" in resp.coverage_note
