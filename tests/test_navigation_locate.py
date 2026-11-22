"""Town / PIN lookup for the Find care page and GET /resolve-location. SYNTHETIC directory rows only."""

import pandas as pd
import pytest

from hackathon.navigation.locate import LocationResolver

# A tiny India Post-style directory (column names as in the real tables). Places and coordinates are
# rough real-world positions; office names are made up.
ROWS = [
    # officename, pincode, district, statename, latitude, longitude
    ("Gayaganj H.O", "823001", "Gaya", "Bihar", 24.79, 85.00),
    ("Gayaganj Bazar S.O", "823002", "Gaya", "Bihar", 24.77, 85.01),
    ("Tarbunda B.O", "824101", "Aurangabad", "Bihar", 24.75, 84.37),
    ("Kolpura B.O", "824102", "Aurangabad", "Bihar", 24.70, 84.40),
    ("Sinnarpet S.O", "431001", "Aurangabad", "Maharashtra", 19.88, 75.34),
    ("Velvadi B.O", "431002", "Aurangabad", "Maharashtra", 19.90, 75.30),
    ("Patna Kotwali H.O", "800001", "Patna", "Bihar", 25.61, 85.14),
    ("Danapur S.O", "801503", "Patna", "Bihar", 25.63, 85.05),
    ("Patna B.O", "495442", "Korba", "Chhattisgarh", 22.35, 82.68),
    ("Ahmadpur H.O", "380001", "Ahmadabad", "Gujarat", 23.03, 72.58),
    ("Naroda S.O", "382330", "Ahmadabad", "Gujarat", 23.07, 72.65),
    ("Shivaji Peth H.O", "411001", "Pune", "Maharashtra", 18.52, 73.86),
    ("Kothrud S.O", "411038", "Pune", "Maharashtra", 18.50, 73.81),
    # a whole district filed under Bihar that carries a Maharashtra position (the real directory has these)
    ("Bhabhua H.O", "821101", "Kaimur", "Bihar", 18.51, 73.85),
    ("Mohania S.O", "821109", "Kaimur", "Bihar", 18.52, 73.86),
]
# enough ordinary Bihar offices for the state's extent to mean something
ROWS += [(f"Village {i} B.O", f"803{i:03d}", "Nalanda", "Bihar", 25.0 + (i % 20) / 20, 85.0 + (i // 20) / 20)
         for i in range(240)]


@pytest.fixture(scope="module")
def resolver():
    raw = pd.DataFrame(ROWS, columns=["officename", "pincode", "district", "statename", "latitude", "longitude"])
    return LocationResolver.from_raw(raw)


def test_pin(resolver):
    r = resolver.resolve("823001")
    assert r.status == "found" and r.method == "pin"
    assert r.latitude == pytest.approx(24.79, abs=0.01) and "Gaya" in r.label and "Bihar" in r.label


def test_pin_inside_text(resolver):
    assert resolver.resolve("near 411 038, Pune").method == "pin"


def test_unknown_pin(resolver):
    assert resolver.resolve("999999").status == "not_found"


def test_unique_place(resolver):
    r = resolver.resolve("gaya")
    assert r.status == "found" and r.label == "Gaya, Bihar" and r.method == "place"
    assert r.spread_km is not None


def test_same_name_in_two_states_is_never_guessed(resolver):
    r = resolver.resolve("Aurangabad")
    assert r.status == "ambiguous" and r.latitude is None
    assert {c.state for c in r.candidates} == {"Bihar", "Maharashtra"}


@pytest.mark.parametrize("text, state, lat", [
    ("Aurangabad, Bihar", None, 24.7),
    ("Aurangabad, MH", None, 19.9),
    ("Aurangabad", "Maharashtra", 19.9),
])
def test_state_settles_it(resolver, text, state, lat):
    r = resolver.resolve(text, state)
    assert r.status == "found" and r.latitude == pytest.approx(lat, abs=0.1)


def test_a_district_in_one_state_wins_over_small_offices(resolver):
    r = resolver.resolve("Patna")
    assert r.status == "found" and r.label == "Patna, Bihar" and "Chhattisgarh" in r.note


def test_district_spelling(resolver):
    r = resolver.resolve("Ahmedabad")
    assert r.status == "found" and r.method == "place_approx" and "Gujarat" in r.label


def test_point_in_the_wrong_state_is_refused(resolver):
    for text in ("Kaimur", "821101"):                   # a Bihar district and PIN carrying a Pune position
        r = resolver.resolve(text)
        assert r.status == "not_found" and "wrong" in r.note, text


@pytest.mark.parametrize("text", ["", "   ", "Atlantis"])
def test_not_found(resolver, text):
    r = resolver.resolve(text)
    assert r.status == "not_found" and r.latitude is None and r.note


def test_api_resolve_location(resolver):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from hackathon.navigation.api import create_app
    from hackathon.navigation.red_flags import RedFlagRules
    from hackathon.navigation.service import Navigator, load_nav_config

    nav = Navigator(RedFlagRules.load(), load_nav_config())
    with TestClient(create_app(navigator=nav, resolver=resolver)) as c:
        ok = c.get("/resolve-location", params={"q": "Aurangabad", "state": "Bihar"})
        assert ok.status_code == 200 and ok.json()["status"] == "found"
        amb = c.get("/resolve-location", params={"q": "Aurangabad"}).json()
        assert amb["status"] == "ambiguous" and len(amb["candidates"]) == 2
        bad = c.get("/resolve-location", params={"q": "x" * 300})
        assert bad.status_code == 422 and "x" * 50 not in bad.text
