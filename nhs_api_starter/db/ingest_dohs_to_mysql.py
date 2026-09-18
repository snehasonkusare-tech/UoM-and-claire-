"""Load raw DoHS v3 JSON pages into the MySQL schema defined in schema.sql.

Run schema.sql in MySQL Workbench first (creates the `nhs_directory`
database), then:

    pip install -r requirements.txt
    cp .env.example .env        # fill in the DB_* values for your local server
    python db/ingest_dohs_to_mysql.py

Every primary key the loader generates is deterministic (derived from the
source data, e.g. the organisation's SearchKey or a hash of a service's
code+name), and every insert is an upsert (`ON DUPLICATE KEY UPDATE`). That
makes the script safe to re-run after re-exporting fresh pages: rows are
updated in place, never duplicated.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "directory_of_healthcare_services_v3"

# LastUpdatedDates has one nested list (KeyValueData) mixed in with the
# scalar date fields; it isn't a component timestamp so it's skipped.
COMPONENT_UPDATE_SKIP_KEYS = {"KeyValueData"}


def load_db_config() -> dict:
    load_dotenv(PROJECT_ROOT / ".env")
    return {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "nhs_directory"),
    }


def short_hash(*parts: str) -> str:
    digest = hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def clean(value):
    """Treat blank strings as NULL; pass everything else through."""
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def collation_key(text: str) -> str:
    """Fold text the way MySQL's utf8mb4_unicode_ci comparisons do (case-
    insensitive), so a service/facility name that only differs by case hashes
    to the same id as MySQL would already treat it as the same unique key."""
    return text.strip().casefold()


def to_bit(value):
    """Render booleans as 0/1. The connector's batched multi-row INSERT
    (needed here because some VALUES contain a subquery) mis-renders Python
    bool literals as the bare word true/false, which MySQL rejects for an
    integer/boolean column."""
    if value is None:
        return None
    return 1 if value else 0


def parse_iso_datetime(value):
    value = clean(value)
    if value is None:
        return None
    value = value.rstrip("Z").split(".")[0]
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def parse_additional_date(value):
    value = clean(value)
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%b %d %Y").date()
    except ValueError:
        return None


class Batches:
    """Accumulates rows for one page file, table by table."""

    def __init__(self):
        self.classifications: dict[str, tuple] = {}
        self.organisations: list[tuple] = []
        self.locations: list[tuple] = []
        self.contacts: list[tuple] = []
        self.services: dict[str, tuple] = {}
        self.organisation_services: dict[tuple, tuple] = {}
        self.opening_times: list[tuple] = []
        self.facilities: dict[str, tuple] = {}
        self.organisation_facilities: dict[tuple, tuple] = {}
        self.component_updates: list[tuple] = []


def build_record_rows(record: dict, batches: Batches, parent_links: list[tuple]) -> None:
    organisation_id = record.get("SearchKey")
    if not organisation_id:
        return

    type_code = clean(record.get("OrganisationTypeId")) or "UNKNOWN"
    type_name = clean(record.get("OrganisationType")) or type_code
    # OrganisationTypeId alone isn't a stable key (e.g. "TRU" covers "Acute
    # Trust", "SocialCare Trust", "MentalHealth Trust", ...), so the
    # classification dimension is keyed by the (code, name) pair.
    batches.classifications[(type_code, type_name)] = (type_code, type_name)

    parent = record.get("ParentOrganisation") or {}
    parent_ods_code = clean(parent.get("ODSCode"))
    if parent_ods_code:
        parent_links.append((organisation_id, parent_ods_code))

    batches.organisations.append((
        organisation_id,
        organisation_id,                      # source_record_key == SearchKey
        clean(record.get("ODSCode")),
        type_code,
        type_name,
        record.get("OrganisationName"),
        clean(record.get("OrganisationSubType")),
        clean(record.get("OrganisationStatus")),
        to_bit(record.get("IsEpsEnabled")),
    ))

    geocode = record.get("Geocode") or {}
    coordinates = geocode.get("coordinates") or [None, None]
    geo_longitude, geo_latitude = (list(coordinates) + [None, None])[:2]
    latitude = record.get("Latitude")
    latitude = latitude if latitude is not None else geo_latitude
    longitude = record.get("Longitude")
    longitude = longitude if longitude is not None else geo_longitude
    batches.locations.append((
        f"{organisation_id}#LOC",
        organisation_id,
        clean(record.get("Address1")),
        clean(record.get("Address2")),
        clean(record.get("Address3")),
        clean(record.get("City")),
        clean(record.get("County")),
        clean(record.get("Postcode")),
        clean(record.get("Country")),
        latitude,
        longitude,
    ))

    for index, contact in enumerate(record.get("Contacts") or [], start=1):
        value = clean(contact.get("ContactValue"))
        if value is None:
            continue
        context = " / ".join(
            part for part in (
                clean(contact.get("ContactType")),
                clean(contact.get("ContactAvailabilityType")),
            ) if part
        ) or None
        batches.contacts.append((
            f"{organisation_id}#CONTACT#{index}",
            organisation_id,
            clean(contact.get("ContactMethodType")),
            context,
            value,
        ))

    for service in record.get("Services") or []:
        service_name = clean(service.get("ServiceName"))
        if not service_name:
            continue
        service_code = clean(service.get("ServiceCode")) or ""
        service_id = "SVC-" + short_hash(collation_key(service_code), collation_key(service_name))
        batches.services[service_id] = (service_id, service_code or None, service_name)
        key = (organisation_id, service_id)
        batches.organisation_services[key] = (
            f"{organisation_id}#SVC#{service_id}",
            organisation_id,
            service_id,
        )

    for index, opening in enumerate(record.get("OpeningTimes") or [], start=1):
        batches.opening_times.append((
            f"{organisation_id}#OPEN#{index}",
            organisation_id,
            clean(opening.get("OpeningTimeType")),
            clean(opening.get("Weekday")),
            clean(opening.get("OpeningTime")),
            clean(opening.get("ClosingTime")),
            parse_additional_date(opening.get("AdditionalOpeningDate")),
            to_bit(opening.get("IsOpen") is False),
        ))

    for facility in record.get("Facilities") or []:
        source_code = facility.get("Id")
        facility_name = clean(facility.get("Name"))
        if source_code is None or not facility_name:
            continue
        source_code = str(source_code)
        facility_id = f"FAC-{source_code}"
        batches.facilities[facility_id] = (
            facility_id,
            source_code,
            clean(facility.get("FacilityGroupName")),
            facility_name,
        )
        key = (organisation_id, facility_id)
        batches.organisation_facilities[key] = (
            f"{organisation_id}#FAC#{source_code}",
            organisation_id,
            facility_id,
            clean(facility.get("Value")),
        )

    last_updated = record.get("LastUpdatedDates") or {}
    for component_name, raw_value in last_updated.items():
        if component_name in COMPONENT_UPDATE_SKIP_KEYS:
            continue
        timestamp = parse_iso_datetime(raw_value)
        if timestamp is None:
            continue
        batches.component_updates.append((
            f"{organisation_id}#UPD#{component_name}",
            organisation_id,
            component_name,
            timestamp,
        ))


CLASSIFICATION_UPSERT = """
    INSERT INTO organisation_classification (organisation_type_code, organisation_type_name)
    VALUES (%s, %s)
    ON DUPLICATE KEY UPDATE organisation_type_code = organisation_type_code
"""

ORGANISATION_UPSERT = """
    INSERT INTO organisation (
        organisation_id, source_record_key, ods_code, classification_id,
        organisation_name, organisation_subtype, organisation_status, eps_enabled
    )
    VALUES (%s, %s, %s, (SELECT classification_id FROM organisation_classification
                          WHERE organisation_type_code = %s AND organisation_type_name = %s),
            %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        ods_code = VALUES(ods_code),
        classification_id = VALUES(classification_id),
        organisation_name = VALUES(organisation_name),
        organisation_subtype = VALUES(organisation_subtype),
        organisation_status = VALUES(organisation_status),
        eps_enabled = VALUES(eps_enabled)
"""

LOCATION_UPSERT = """
    INSERT INTO organisation_location (
        location_id, organisation_id, address_line_1, address_line_2, address_line_3,
        city, county, postcode, country, latitude, longitude
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        address_line_1 = VALUES(address_line_1), address_line_2 = VALUES(address_line_2),
        address_line_3 = VALUES(address_line_3), city = VALUES(city), county = VALUES(county),
        postcode = VALUES(postcode), country = VALUES(country),
        latitude = VALUES(latitude), longitude = VALUES(longitude)
"""

CONTACT_UPSERT = """
    INSERT INTO organisation_contact (contact_id, organisation_id, contact_type, contact_context, contact_value)
    VALUES (%s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        contact_type = VALUES(contact_type), contact_context = VALUES(contact_context),
        contact_value = VALUES(contact_value)
"""

SERVICE_UPSERT = """
    INSERT INTO service (service_id, service_code, service_name)
    VALUES (%s, %s, %s)
    ON DUPLICATE KEY UPDATE service_id = service_id
"""
# Casing/wording can vary between organisations for what is otherwise the
# same service (e.g. "Book by phone" vs "book by phone"); service_id is
# hashed from a case-folded key so these collapse onto one row, and whichever
# casing is inserted first is kept rather than flip-flopping between runs.

ORGANISATION_SERVICE_UPSERT = """
    INSERT INTO organisation_service (organisation_service_id, organisation_id, service_id)
    VALUES (%s, %s, %s)
    ON DUPLICATE KEY UPDATE organisation_service_id = organisation_service_id
"""

OPENING_TIME_UPSERT = """
    INSERT INTO organisation_opening_time (
        opening_time_id, organisation_id, opening_type, day_of_week,
        start_time, end_time, exception_date, closed
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        opening_type = VALUES(opening_type), day_of_week = VALUES(day_of_week),
        start_time = VALUES(start_time), end_time = VALUES(end_time),
        exception_date = VALUES(exception_date), closed = VALUES(closed)
"""

FACILITY_UPSERT = """
    INSERT INTO facility (facility_id, source_facility_code, facility_category, facility_name)
    VALUES (%s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        facility_category = facility_category, facility_name = facility_name
"""
# NOTE: the same Facility.Id is sometimes paired with an organisation-specific
# FacilityGroupName in the source data (e.g. facility 15 is filed under
# "Bringing children to Priory Hospital Hayes Grove" for one org and
# "Bringing children to Pelhams Community Clinic" for another). The catalog
# keeps whichever label it saw first and never overwrites it, so the result
# is stable and doesn't depend on file-processing order.

ORGANISATION_FACILITY_UPSERT = """
    INSERT INTO organisation_facility (organisation_facility_id, organisation_id, facility_id, availability_status)
    VALUES (%s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE availability_status = VALUES(availability_status)
"""

COMPONENT_UPDATE_UPSERT = """
    INSERT INTO component_update (component_update_id, organisation_id, component_name, last_updated_at)
    VALUES (%s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE last_updated_at = VALUES(last_updated_at)
"""

def flush_batches(cursor, batches: Batches) -> None:
    if batches.classifications:
        cursor.executemany(CLASSIFICATION_UPSERT, list(batches.classifications.values()))
    if batches.organisations:
        # Tuple order already matches the SQL placeholders: organisation_id,
        # source_record_key, ods_code, organisation_type_code, organisation_type_name
        # (the last two resolve classification_id via a subquery), organisation_name,
        # organisation_subtype, organisation_status, eps_enabled.
        cursor.executemany(ORGANISATION_UPSERT, batches.organisations)
    if batches.locations:
        cursor.executemany(LOCATION_UPSERT, batches.locations)
    if batches.contacts:
        cursor.executemany(CONTACT_UPSERT, batches.contacts)
    if batches.services:
        cursor.executemany(SERVICE_UPSERT, list(batches.services.values()))
    if batches.organisation_services:
        cursor.executemany(ORGANISATION_SERVICE_UPSERT, list(batches.organisation_services.values()))
    if batches.opening_times:
        cursor.executemany(OPENING_TIME_UPSERT, batches.opening_times)
    if batches.facilities:
        cursor.executemany(FACILITY_UPSERT, list(batches.facilities.values()))
    if batches.organisation_facilities:
        cursor.executemany(ORGANISATION_FACILITY_UPSERT, list(batches.organisation_facilities.values()))
    if batches.component_updates:
        cursor.executemany(COMPONENT_UPDATE_UPSERT, batches.component_updates)


def resolve_parent_organisations(cursor, parent_links: list[tuple]) -> int:
    """Second pass: point each organisation at its parent's organisation_id.

    ParentOrganisation only gives us the parent's ODS code, so we resolve it
    against organisation.ods_code once every organisation row already exists
    (a parent can appear in a later page file than its children).
    """
    cursor.execute("SELECT organisation_id, ods_code FROM organisation WHERE ods_code IS NOT NULL")
    by_ods_code: dict[str, str] = {}
    for organisation_id, ods_code in cursor.fetchall():
        # Prefer the canonical record (organisation_id == ods_code) when an
        # ODS code is shared by more than one record (e.g. vaccination sites).
        if ods_code not in by_ods_code or organisation_id == ods_code:
            by_ods_code[ods_code] = organisation_id

    updates = []
    for organisation_id, parent_ods_code in parent_links:
        parent_id = by_ods_code.get(parent_ods_code)
        if parent_id and parent_id != organisation_id:
            updates.append((parent_id, organisation_id))

    if updates:
        cursor.executemany(
            "UPDATE organisation SET parent_organisation_id = %s WHERE organisation_id = %s",
            updates,
        )
    return len(updates)


def main() -> None:
    page_files = sorted(RAW_DIR.glob("page_*.json"))
    if not page_files:
        raise FileNotFoundError(f"No DoHS page files found in {RAW_DIR}")

    connection = mysql.connector.connect(**load_db_config())
    cursor = connection.cursor()

    parent_links: list[tuple] = []
    organisation_count = 0

    for page_number, page_file in enumerate(page_files, start=1):
        with page_file.open("r", encoding="utf-8") as file:
            records = json.load(file).get("value", [])

        batches = Batches()
        for record in records:
            build_record_rows(record, batches, parent_links)
            organisation_count += 1

        flush_batches(cursor, batches)
        connection.commit()

        if page_number % 10 == 0 or page_number == len(page_files):
            print(f"Loaded {page_number}/{len(page_files)} pages ({organisation_count:,} organisations)")

    updated = resolve_parent_organisations(cursor, parent_links)
    connection.commit()
    print(f"Linked {updated:,} organisations to their parent organisation")

    cursor.close()
    connection.close()
    print(f"Done. {organisation_count:,} organisations ingested into nhs_directory.")


if __name__ == "__main__":
    main()
