"""API contract: 200 with the emergency block, 422 that still screens, no echo. SYNTHETIC inputs only."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from hackathon.navigation.api import create_app  # noqa: E402
from hackathon.navigation.red_flags import RedFlagRules  # noqa: E402
from hackathon.navigation.service import Navigator, load_nav_config  # noqa: E402

NAV = Navigator(RedFlagRules.load(), load_nav_config())


@pytest.fixture()
def client():
    with TestClient(create_app(navigator=NAV)) as c:
        yield c


def test_recommend_returns_emergency_block(client):
    r = client.post("/recommend-hospitals", json={"patient_message": "my father is unconscious", "latitude": 28.61, "longitude": 77.21})
    assert r.status_code == 200
    body = r.json()
    assert body["emergency"]["call_emergency_services"] and body["triage"]["urgency"] == "emergency"


def test_invalid_input_is_422_but_still_screened(client):
    r = client.post("/recommend-hospitals", json={"patient_message": "chest pain ZZQ", "latitude": 123, "longitude": 77.2})
    assert r.status_code == 422
    body = r.json()
    assert body["emergency"]["call_emergency_services"]
    assert "ZZQ" not in r.text                                    # errors don't echo the input


@pytest.mark.parametrize("payload", [{"patient_message": "", "latitude": 1, "longitude": 1},
                                     {"patient_message": "x" * 2001, "latitude": 1, "longitude": 1},
                                     {"patient_message": "knee pain", "latitude": "abc", "longitude": 1},
                                     {"patient_message": "knee pain", "latitude": 1, "longitude": 1, "max_results": 21}])
def test_validation(client, payload):
    r = client.post("/recommend-hospitals", json=payload)
    assert r.status_code == 422 and r.json()["emergency"] is None


def test_health(client):
    body = client.get("/health").json()
    assert body["red_flag_rules"]["review_status"] == "draft" and body["data"]["loaded"] is False
