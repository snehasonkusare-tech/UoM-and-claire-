"""Unit tests for the pure parsing/mapping logic in ingest_dohs_to_mysql.py.

These run against small, hand-built JSON records -- no database connection
needed. They catch mapping bugs (wrong field, wrong dedupe key, wrong
tuple order) before you ever touch MySQL.

    python3 db/test_ingest_mapping.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest_dohs_to_mysql as m


def run_test(name, test) -> str | None:
    print(f"\n--- {name} ---")
    try:
        test()
    except Exception as exc:
        print(f"[FAIL] {name}")
        print(f"{type(exc).__name__}: {exc}")
        return name
    print(f"[PASS] {name}")
    return None


def test_clean_blanks_strings_only():
    assert m.clean("") is None
    assert m.clean("   ") is None
    assert m.clean("England") == "England"
    assert m.clean(None) is None
    assert m.clean(0) == 0          # falsy but not blank -- must survive
    assert m.clean(False) is False  # same


def test_to_bit_maps_bool_to_int():
    assert m.to_bit(True) == 1
    assert m.to_bit(False) == 0
    assert m.to_bit(None) is None


def test_collation_key_folds_case_and_whitespace():
    assert m.collation_key("  Book by phone ") == m.collation_key("book by phone")
    assert m.collation_key("Flu vaccination") != m.collation_key("Flu vaccinations")


def test_parse_iso_datetime_handles_z_and_milliseconds():
    assert m.parse_iso_datetime("2025-09-30T13:56:49.357Z") == datetime(2025, 9, 30, 13, 56, 49)
    assert m.parse_iso_datetime(None) is None
    assert m.parse_iso_datetime("") is None
    assert m.parse_iso_datetime("not-a-date") is None


def test_parse_additional_date_handles_nhs_format():
    assert m.parse_additional_date("Apr 05 2026") == date(2026, 4, 5)
    assert m.parse_additional_date("") is None
    assert m.parse_additional_date(None) is None


SAMPLE_RECORD = {
    "SearchKey": "FFL154QR_CVD19",
    "ODSCode": "FFL154QR_CVD19",
    "OrganisationName": "247 Pharmacy",
    "OrganisationTypeId": "VAC",
    "OrganisationType": "Vaccination Centre",
    "OrganisationStatus": "Visible",
    "OrganisationSubType": None,
    "Address1": "15 Stuart Road",
    "Address2": None,
    "Address3": None,
    "City": "Liverpool",
    "County": None,
    "Postcode": "L22 4QR",
    "Country": "England",
    "Latitude": 53.480267,
    "Longitude": -3.019019,
    "Geocode": {"type": "Point", "coordinates": [-3.01902, 53.4803]},
    "IsEpsEnabled": True,
    "ParentOrganisation": {"ODSCode": "QYG", "OrganisationName": "NHS Cheshire and Merseyside ICB"},
    "Services": [
        {"ServiceCode": "SRV1", "ServiceName": "Flu vaccination service: Book by phone"},
        {"ServiceCode": "SRV1", "ServiceName": "flu vaccination service: book by phone"},  # same service, different casing
        {"ServiceCode": "", "ServiceName": ""},  # should be skipped -- no name
    ],
    "OpeningTimes": [
        {
            "Weekday": "Monday", "OpeningTime": "09:00", "ClosingTime": "18:00",
            "OpeningTimeType": "General", "AdditionalOpeningDate": "", "IsOpen": True,
        },
        {
            "Weekday": None, "OpeningTime": None, "ClosingTime": None,
            "OpeningTimeType": "Additional", "AdditionalOpeningDate": "Apr 05 2026", "IsOpen": False,
        },
    ],
    "Contacts": [
        {"ContactType": "Primary", "ContactAvailabilityType": "Office hours", "ContactMethodType": "Telephone", "ContactValue": "0151 123 4567"},
        {"ContactType": "Primary", "ContactAvailabilityType": "Office hours", "ContactMethodType": "Website", "ContactValue": ""},  # blank -- should be skipped
    ],
    "Facilities": [
        {"Id": 24, "Name": "Cafe", "Value": "Yes", "FacilityGroupName": "Food and amenities on-site"},
        {"Id": None, "Name": "Ignored, no Id", "Value": "Yes", "FacilityGroupName": "X"},  # should be skipped
    ],
    "LastUpdatedDates": {
        "ServiceOpeningTimes": "2025-09-30T13:56:49.357Z",
        "Facilities": None,
        "KeyValueData": [],  # not a timestamp -- must be skipped, not crash
    },
}


def build_sample():
    batches = m.Batches()
    parent_links = []
    m.build_record_rows(SAMPLE_RECORD, batches, parent_links)
    return batches, parent_links


def test_organisation_row_shape_and_values():
    batches, _ = build_sample()
    assert len(batches.organisations) == 1
    org_id, src_key, ods, type_code, type_name, name, subtype, status, eps = batches.organisations[0]
    assert org_id == src_key == "FFL154QR_CVD19"
    assert ods == "FFL154QR_CVD19"
    assert type_code == "VAC"
    assert type_name == "Vaccination Centre"
    assert name == "247 Pharmacy"
    assert subtype is None
    assert status == "Visible"
    assert eps == 1  # to_bit(True)


def test_classification_keyed_by_code_and_name_pair():
    batches, _ = build_sample()
    assert ("VAC", "Vaccination Centre") in batches.classifications


def test_parent_link_captured_for_second_pass():
    _, parent_links = build_sample()
    assert parent_links == [("FFL154QR_CVD19", "QYG")]


def test_location_row_uses_top_level_lat_long_over_geocode():
    batches, _ = build_sample()
    assert len(batches.locations) == 1
    row = batches.locations[0]
    assert row[0] == "FFL154QR_CVD19#LOC"
    assert row[-2] == 53.480267   # Latitude, not Geocode's 53.4803
    assert row[-1] == -3.019019


def test_blank_contact_value_is_skipped():
    batches, _ = build_sample()
    assert len(batches.contacts) == 1
    assert batches.contacts[0][2] == "Telephone"
    assert batches.contacts[0][4] == "0151 123 4567"


def test_case_variant_services_collapse_to_one_row():
    batches, _ = build_sample()
    # Two "Book by phone" / "book by phone" entries + one blank-named entry
    # (skipped) should leave exactly one distinct service.
    assert len(batches.services) == 1
    assert len(batches.organisation_services) == 1


def test_facility_without_id_is_skipped():
    batches, _ = build_sample()
    assert len(batches.facilities) == 1
    assert "FAC-24" in batches.facilities
    assert len(batches.organisation_facilities) == 1


def test_opening_times_map_closed_flag_correctly():
    batches, _ = build_sample()
    assert len(batches.opening_times) == 2
    monday = next(r for r in batches.opening_times if r[3] == "Monday")
    assert monday[7] == 0  # not closed
    additional = next(r for r in batches.opening_times if r[2] == "Additional")
    assert additional[6] == date(2026, 4, 5)
    assert additional[7] == 1  # closed


def test_component_update_skips_null_and_list_values():
    batches, _ = build_sample()
    assert len(batches.component_updates) == 1
    assert batches.component_updates[0][2] == "ServiceOpeningTimes"


def main() -> int:
    tests = [
        (name, func)
        for name, func in list(globals().items())
        if name.startswith("test_") and callable(func)
    ]
    failures = [failure for name, func in tests if (failure := run_test(name, func)) is not None]

    print("\n" + "=" * 50)
    print(f"Passed: {len(tests) - len(failures)}")
    print(f"Failed: {len(failures)}")
    for failure in failures:
        print(f"- {failure}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
