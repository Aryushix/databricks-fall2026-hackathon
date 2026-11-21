"""Tests for the cleaning rules, on small made-up rows (the real dataset never goes in Git).

Run:  pip install pandas numpy phonenumbers pytest && pytest -q
"""

import pandas as pd
import pytest

from hackathon import cleaning
from hackathon.cleaning import (
    RAW_COLUMNS,
    TAIL_COLUMNS,
    clean,
    clean_city,
    clean_date,
    clean_emails,
    clean_pin,
    domain_of,
    is_non_official,
    normalize_phone,
    prepare_parsed_rows,
    resolve_state,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("9876543210", ("+919876543210", "mobile")),
        ("+91 98765 43210", ("+919876543210", "mobile")),
        ("022 2345 6789", ("+912223456789", "landline")),
        ("1800 123 4567", ("+9118001234567", "toll_free")),
        ("+5322421425", ("+915322421425", "landline")),  # stray '+' on an Indian national number
        ("+1 415 555 2671", ("+14155552671", "foreign")),
        ("12345", ("12345", "invalid")),
        (None, (None, None)),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_clean_emails():
    assert clean_emails("info [at] apollo [dot] com") == (["info@apollo.com"], set())
    assert clean_emails("[email protected]") == ([], {"email_obfuscated"})
    assert clean_emails('["a@x.com", "bad@", "A@X.com"]') == (["a@x.com"], {"email_invalid"})


def test_geography_helpers():
    assert resolve_state("tamilnadu") == "Tamil Nadu"
    assert resolve_state("Ghaziabad, Uttar Pradesh") == "Uttar Pradesh"
    assert resolve_state("Bijapur-karnataka") == "Karnataka"
    assert resolve_state("Atlantis") is None
    assert clean_pin("560 001") == "560001"
    assert clean_pin("012345") is None  # PINs never start with 0
    assert clean_city("Bangalore") == "Bengaluru"
    assert clean_city("Ludhiana-1") == "Ludhiana"


def test_website_and_dates():
    assert domain_of("https://www.practo.com/x") == "practo.com"
    assert is_non_official("practo.com") and not is_non_official("apollohospitals.com")
    assert clean_date("2023-05-01") == ("2023-05-01", False)
    assert clean_date("9 months ago") == (None, True)
    assert clean_date("2024-02-30") == (None, True)


def _row(uid, **overrides):
    row = {c: None for c in RAW_COLUMNS}
    row.update(
        unique_id=uid, name="City Care Hospital", address_city="Lucknow", address_stateOrRegion="UP",
        address_zipOrPostcode="226001", latitude="26.85", longitude="80.95", officialPhone="9876543210",
        email="info@citycare.in", specialties='["cardiology"]', capability='["ICU"]', procedure='["Dialysis"]',
        description="A 50-bed hospital.", source_urls='["https://citycare.in"]', organization_type="facility",
        address_country="India", source="kie",
    )
    row.update(overrides)
    return row


UID = "{:08x}-0000-4000-8000-{:012x}"


def test_prepare_parsed_rows_drops_fragments_and_flags_truncated():
    normal = _row(UID.format(1, 1))
    truncated = _row(UID.format(2, 2), **{c: None for c in TAIL_COLUMNS})
    fragment = _row("Mobile: +91-9451520000", **{c: None for c in TAIL_COLUMNS})
    df, repaired, extra, info = prepare_parsed_rows(pd.DataFrame([normal, truncated, fragment]))
    assert len(df) == 2 and repaired == set()
    assert info == {"fragment_rows_dropped": 1, "truncated_records_flagged": 1}
    assert extra == {UID.format(2, 2): {"row_truncated_in_source"}}


def test_prepare_parsed_rows_needs_all_columns():
    with pytest.raises(ValueError, match="missing expected columns"):
        prepare_parsed_rows(pd.DataFrame([{"unique_id": UID.format(1, 1)}]))


def test_clean_end_to_end():
    rows = [
        _row(UID.format(1, 1)),
        _row(UID.format(1, 1)),                                     # exact duplicate -> dropped
        _row(UID.format(3, 3), latitude="51.5", longitude="-0.12"),  # London -> coords removed
        _row(UID.format(4, 4), officialPhone="12345", email="[email protected]"),
    ]
    raw = pd.DataFrame(rows).astype(object)
    out, stats = clean(raw, set(), {UID.format(4, 4): {"row_truncated_in_source"}})
    assert stats["rows_out"] == 3 and stats["exact_duplicates_dropped"] == 1
    flags = dict(zip(out.unique_id, out.dq_flags.fillna("")))
    assert "coords_outside_india_removed" in flags[UID.format(3, 3)]
    assert {"phone_invalid", "email_obfuscated", "row_truncated_in_source"} <= set(flags[UID.format(4, 4)].split(";"))
    assert out.loc[out.unique_id == UID.format(1, 1), "address_stateOrRegion"].iloc[0] == "Uttar Pradesh"
    assert not set(cleaning.DROP_COLS) & set(out.columns)
    assert {"dq_flags", "dq_flag_count", "dup_group_id", "officialPhone_type"} <= set(out.columns)


def test_control_characters_are_repaired():
    assert cleaning.clean_text("32 PEARLS\x00ae Clinic") == "32 PEARLS® Clinic"
    assert cleaning.clean_text("Dr. Shinde\x19s Clinic") == "Dr. Shinde’s Clinic"
    assert cleaning.clean_text("Clinic \x1f4cc First Floor") == "Clinic First Floor"
    assert cleaning.clean_text("Line one\nLine two") == "Line one\nLine two"  # newlines kept
    out, _ = clean(pd.DataFrame([_row(UID.format(9, 9), name="Smile Delhi \x00 The Dental Clinic")]).astype(object), set())
    assert out.name.iloc[0] == "Smile Delhi The Dental Clinic"
    assert "text_control_chars_repaired" in out.dq_flags.iloc[0]
