"""API contract for the service search: services, find-facilities, 422 without echo. SYNTHETIC inputs only."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from nav_synthetic import P, facility, record, store  # noqa: E402

from hackathon.navigation.api import create_app  # noqa: E402
from hackathon.navigation.care_needs import CareNeedTable  # noqa: E402
from hackathon.navigation.rank import NavigationRanker  # noqa: E402
from hackathon.navigation.red_flags import RedFlagRules  # noqa: E402
from hackathon.navigation.service import Navigator, load_nav_config  # noqa: E402

CFG, TABLE = load_nav_config(), CareNeedTable.load()
STORE = store([facility("h1", 3), facility("h2", 8), facility("c1", 2, ftype="clinic")],
              [record("h1", "hemodialysis", "CONFIRMED", 0.95, 3), record("h2", "hemodialysis", "LIKELY", 0.7, 8)])
NAV = Navigator(RedFlagRules.load(), CFG, mapper=TABLE, store_provider=lambda: STORE, ranker=NavigationRanker(CFG, TABLE))


@pytest.fixture()
def client():
    with TestClient(create_app(navigator=NAV)) as c:
        yield c


def test_services_lists_verifiable_first(client):
    body = client.get("/services").json()
    ids = [s["id"] for s in body]
    assert "hemodialysis" in ids and body[0]["verifiable"]
    assert [s["verifiable"] for s in body] == sorted([s["verifiable"] for s in body], reverse=True)


def test_find_facilities(client):
    r = client.post("/find-facilities", json={"service": "hemodialysis", "latitude": P[0], "longitude": P[1]})
    assert r.status_code == 200
    body = r.json()
    assert body["service_name"] and body["verifiable"] and not body["degraded"]
    verified = [x for x in body["recommendations"] if x["list"] == "verified"]
    assert [x["hospital_id"] for x in verified] == ["h1", "h2"]                  # CONFIRMED before LIKELY
    assert "emergency" not in body and "triage" not in body                      # no symptoms, no triage
    assert "112" not in r.text


def test_unknown_service_is_422(client):
    r = client.post("/find-facilities", json={"service": "teleportation", "latitude": P[0], "longitude": P[1]})
    assert r.status_code == 422


@pytest.mark.parametrize("payload", [{"service": "", "latitude": 1, "longitude": 1},
                                     {"service": "hemodialysis", "latitude": 123, "longitude": 1},
                                     {"service": "hemodialysis", "latitude": "ZZQ", "longitude": 1},
                                     {"service": "hemodialysis", "latitude": 1, "longitude": 1, "max_results": 21}])
def test_validation_does_not_echo(client, payload):
    r = client.post("/find-facilities", json=payload)
    assert r.status_code == 422 and "ZZQ" not in r.text


def test_outside_india(client):
    body = client.post("/find-facilities", json={"service": "hemodialysis", "latitude": 51.5, "longitude": -0.1}).json()
    assert body["recommendations"] == [] and "outside" in body["coverage_note"]


def test_health(client):
    body = client.get("/health").json()
    assert body["data"]["loaded"] is True
