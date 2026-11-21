import pandas as pd
import pytest

from hackathon import location_check as L


def office(pin, name, district, state, lat, lon):
    return {"officename": name, "pincode": pin, "district": district, "statename": state,
            "latitude": lat, "longitude": lon}


@pytest.fixture(scope="module")
def directory():
    rows = [
        office(226001, "Lucknow G.P.O.", "LUCKNOW", "UTTAR PRADESH", 26.850, 80.950),
        office(226010, "Gomti Nagar S.O", "LUCKNOW", "UTTAR PRADESH", 26.860, 81.000),
        office(431001, "Aurangabad H.O", "AURANGABAD", "MAHARASHTRA", 19.880, 75.340),
        office(400001, "Mumbai G.P.O.", "MUMBAI", "MAHARASHTRA", 18.940, 72.830),
        # a directory error: an Agartala office filed under a Telangana district
        office(799001, "Agartala H.O", "Nirmal", "TELANGANA", 23.830, 91.280),
    ]
    rows += [office(799002 + i, f"Agartala Ward {i} S.O", "WEST TRIPURA", "TRIPURA", 23.84, 91.27)
             for i in range(11)]
    return L.Directory(L.normalize_directory(pd.DataFrame(rows)))


def facility(uid, pin, city, state, lat, lon, line1=None):
    return {"unique_id": uid, "address_zipOrPostcode": pin, "address_city": city,
            "address_stateOrRegion": state, "latitude": lat, "longitude": lon,
            "address_line1": line1, "address_line2": None, "address_line3": None,
            "dq_flags": "pin_invalid" if pin is None else None}


@pytest.fixture(scope="module")
def checked(directory):
    fac = pd.DataFrame([
        facility("ok", "226010", "Lucknow", "Uttar Pradesh", "26.861", "81.001"),
        facility("coords_wrong", "431001", "Aurangabad", "Maharashtra", "18.94", "72.83"),
        facility("pin_wrong", "400001", "Aurangabad", "Maharashtra", "19.88", "75.34"),
        facility("alias", "400001", "Bombay", "Maharashtra", "18.941", "72.831"),
        facility("missing_place", "999999", "Atlantis", "Kerala", None, None),
        facility("directory_error", "799001", "Agartala", "Tripura", "23.83", "91.28"),
        facility("coords_only", None, "Xyzpur", "Uttar Pradesh", "26.851", "80.951"),
        facility("conflicting", "226001", "Mumbai", "Maharashtra", "19.88", "75.34"),
    ])
    results, summary = L.verify(fac, directory)
    return fac, results.set_index("unique_id"), summary


def test_place_key():
    assert L.place_key("Dombivli East") == "dombivli"
    assert L.place_key("Bengaluru Urban") == "bengaluru"
    assert L.place_key("Anna Nagar S.O") == "anna nagar"
    assert L.place_key("New Town") == "new town"
    assert L.place_key("North 24 Parganas") == "parganas"
    assert L.place_key(None) is None


def test_directory_needs_columns():
    with pytest.raises(ValueError, match="missing"):
        L.normalize_directory(pd.DataFrame({"pincode": [110001]}))


def test_all_fields_agree(checked):
    r = checked[1].loc["ok"]
    assert (r.location_status, r.location_confidence) == ("verified", "high")
    assert r.resolved_location_source == "facility_coordinates"


def test_wrong_coordinates_use_pin_area(checked):
    r = checked[1].loc["coords_wrong"]
    assert r.coords_check == "contradicted" and r.pin_check == "confirmed"
    assert r.location_status == "partly_verified"
    assert r.resolved_location_source == "pin_area"
    assert r.resolved_latitude == pytest.approx(19.88)
    assert "coordinates are probably wrong" in r.location_evidence
    assert "coords_contradicted" in r.dq_flags


def test_wrong_pin(checked):
    r = checked[1].loc["pin_wrong"]
    assert r.pin_check == "contradicted"
    assert r.coords_check == "confirmed" and r.city_check == "confirmed"


def test_alias(checked):
    r = checked[1].loc["alias"]
    assert r.city_match == "alias:mumbai" and r.location_status == "verified"


def test_location_not_found_is_removed(checked):
    fac, res, summary = checked
    assert not res.loc["missing_place"].keep
    assert res.loc["missing_place"].location_status == "not_found"
    assert summary["removed"] == 1
    kept, removed = L.apply(fac, res.reset_index())
    assert list(removed.unique_id) == ["missing_place"]
    assert len(kept) == len(fac) - 1 and "location_evidence" in kept.columns


def test_directory_error_does_not_blame_the_facility(checked):
    r = checked[1].loc["directory_error"]
    assert r.state_check == "confirmed"
    assert "looks wrong" in r.location_evidence


def test_coordinates_near_a_post_office_keep_the_row(checked):
    r = checked[1].loc["coords_only"]
    assert r.keep and r.location_status == "unverified"
    assert "pin_invalid" in r.dq_flags and "location_unverified" in r.dq_flags


def test_conflicting(checked):
    r = checked[1].loc["conflicting"]
    assert r.location_status == "conflicting" and r.location_confidence == "low"
    assert r.keep


def test_can_also_remove_conflicting(directory, checked):
    fac = checked[0]
    results, _ = L.verify(fac, directory, remove_statuses=("not_found", "conflicting"))
    assert set(results[~results.keep].unique_id) == {"missing_place", "conflicting"}
