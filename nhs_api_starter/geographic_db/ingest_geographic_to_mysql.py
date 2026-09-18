"""Load the "All in one" tab of the Greater Manchester borough master
spreadsheet into the MySQL schema defined in geographic_db/schema.sql.

Run geographic_db/schema.sql in MySQL Workbench first (creates its own
geographic_areas database -- separate from nhs_directory and nhs_symptoms --
and the geographic_area table), then:

    pip install -r requirements.txt
    pip install openpyxl
    cp .env.example .env        # fill in the DB_* values if not already done
    python geographic_db/ingest_geographic_to_mysql.py

Only the "All in one" sheet is read; the per-borough sheets in the same
workbook are the same data split out and are not re-ingested.

Each row is upserted (ON DUPLICATE KEY UPDATE) keyed on a deterministic
geographic_area_id hashed (uuid5) from (area_name, area_type,
official_area_code), so re-running the ingest updates rows in place instead
of duplicating them -- same idempotent pattern as
symptoms_db/ingest_symptoms_to_mysql.py.

Parent linking: the sheet only gives a parent AREA NAME (not an id), and
~12% of area names are reused -- either because the same name legitimately
denotes two different places (e.g. "Moorside" exists in three different
boroughs), or because the sheet lists both a "named place" row and an
administrative "Ward" row for the same place/postcode. Parent resolution
therefore proceeds in two passes: first every row is inserted with
parent_geographic_area_id left NULL, then a second pass resolves each row's
"Parent area" name against the other rows using find_parent_id() below. Where
the name is genuinely ambiguous or matches nothing, the parent link is left
NULL and the row is reported in the "unresolved parent" summary printed at
the end -- it is never guessed.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import mysql.connector
import openpyxl
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SHEET_NAME = "All in one "
UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # DNS namespace, reused as a fixed seed


def load_db_config() -> dict:
    load_dotenv(PROJECT_ROOT / ".env")
    return {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("GEOGRAPHIC_DB_NAME", "geographic_areas"),
        "ssl_disabled": False,
        "tls_versions": ["TLSv1.3"],
    }


def clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_geographic_area_id(area_name: str, area_type: str, official_area_code: str | None) -> str:
    key = f"{area_name}|{area_type}|{official_area_code or ''}"
    return str(uuid.uuid5(UUID_NAMESPACE, key))


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def read_sheet(xlsx_path: Path) -> list[dict]:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    if SHEET_NAME not in wb.sheetnames:
        raise SystemExit(
            f"Sheet {SHEET_NAME!r} not found in {xlsx_path.name}. "
            f"Available sheets: {wb.sheetnames}"
        )
    ws = wb[SHEET_NAME]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    header = [clean(h) for h in rows[0]]
    expected = ["Area Name", "Area Type", "Area Postcode", "Parent area"]
    if header[:4] != expected:
        raise SystemExit(f"Unexpected header in {SHEET_NAME!r}: {header[:4]} (expected {expected})")

    records = []
    for raw in rows[1:]:
        if not raw or clean(raw[0]) is None:
            continue
        area_name = clean(raw[0])
        area_type = clean(raw[1])
        official_area_code = clean(raw[2])
        parent_raw = clean(raw[3])
        parent_name = None if parent_raw is None or parent_raw.upper() == "NIL" else parent_raw
        records.append(
            {
                "area_name": area_name,
                "area_type": area_type,
                "official_area_code": official_area_code,
                "parent_name": parent_name,
            }
        )
    return records


def find_parent_id(record: dict, by_name: dict[str, list[dict]], unresolved: list[dict]) -> str | None:
    parent_name = record["parent_name"]
    if parent_name is None:
        return None

    candidates = by_name.get(parent_name, [])
    if len(candidates) == 1:
        return candidates[0]["geographic_area_id"]

    if len(candidates) == 0:
        unresolved.append({**record, "reason": "no area matches this parent name"})
        return None

    # Multiple areas share this name. Prefer the "named place" row over a
    # same-named administrative Ward row (the sheet lists both for many
    # places, always at the same postcode) -- see module docstring.
    non_ward = [c for c in candidates if c["area_type"] != "Ward"]
    if len(non_ward) == 1:
        return non_ward[0]["geographic_area_id"]

    unresolved.append(
        {
            **record,
            "reason": f"{len(candidates)} areas are named {parent_name!r}: "
            + ", ".join(f"{c['area_type']}/{c['official_area_code']}" for c in candidates),
        }
    )
    return None


GEOGRAPHIC_AREA_UPSERT = """
    INSERT INTO geographic_area (
        geographic_area_id, area_name, area_type, official_area_code,
        created_at, updated_at, parent_geographic_area_id
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        updated_at = VALUES(updated_at),
        parent_geographic_area_id = VALUES(parent_geographic_area_id)
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Load the "All in one" tab of the GM borough master spreadsheet into MySQL.'
    )
    parser.add_argument(
        "--xlsx",
        default=str(PROJECT_ROOT.parent / "Geographic" / "Greater_Manchester_10_Borough_Master-2.xlsx"),
        help="Path to the source .xlsx workbook.",
    )
    args = parser.parse_args()

    xlsx_path = Path(args.xlsx).expanduser().resolve()
    print(f"Reading sheet {SHEET_NAME!r} from {xlsx_path}")
    records = read_sheet(xlsx_path)
    print(f"Rows read: {len(records)}")

    now = utc_now_dt()
    by_name: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        record["geographic_area_id"] = build_geographic_area_id(
            record["area_name"], record["area_type"], record["official_area_code"]
        )
        by_name[record["area_name"]].append(record)

    unresolved: list[dict] = []
    root_count = 0
    for record in records:
        if record["parent_name"] is None:
            root_count += 1
        record["parent_geographic_area_id"] = find_parent_id(record, by_name, unresolved)

    connection = mysql.connector.connect(**load_db_config())
    cursor = connection.cursor()

    params = [
        (
            r["geographic_area_id"],
            r["area_name"],
            r["area_type"],
            r["official_area_code"],
            now,
            now,
            r["parent_geographic_area_id"],
        )
        for r in records
    ]
    cursor.executemany(GEOGRAPHIC_AREA_UPSERT, params)
    connection.commit()

    cursor.close()
    connection.close()

    resolved_links = sum(1 for r in records if r["parent_name"] is not None and r["parent_geographic_area_id"])
    print(f"Upserted:            {len(records)}")
    print(f"Root areas (NIL):    {root_count}")
    print(f"Parent links resolved: {resolved_links}")
    print(f"Parent links unresolved (left NULL): {len(unresolved)}")
    if unresolved:
        print("\nUnresolved parent links:")
        for u in unresolved:
            print(f"  - {u['area_name']} ({u['area_type']}, {u['official_area_code']}): "
                  f"parent {u['parent_name']!r} -- {u['reason']}")


if __name__ == "__main__":
    main()
