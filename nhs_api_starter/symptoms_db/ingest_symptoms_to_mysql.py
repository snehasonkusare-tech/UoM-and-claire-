"""Scrape NHS symptom pages and load them into the MySQL schema defined in
symptoms_db/schema.sql.

Run symptoms_db/schema.sql in MySQL Workbench first (creates its own
nhs_symptoms database -- separate from the DoHS nhs_directory database in
db/schema.sql -- and the scrape_runs / symptoms / scrape_errors tables),
then:

    pip install -r requirements.txt
    cp .env.example .env        # fill in the DB_* / NHS_* values
    python symptoms_db/ingest_symptoms_to_mysql.py [--env sandbox] [--limit 20]

Every run gets its own row in scrape_runs (pages found/selected/succeeded/
failed, marked complete once the run finishes). Each symptom row is upserted
(ON DUPLICATE KEY UPDATE) keyed on a deterministic symptom_id hashed from its
NHS URL, so re-running the ingest updates rows in place under the newest
scrape_run_id instead of duplicating them -- same idempotent pattern as
ingest_dohs_to_mysql.py. A symptom whose detail page fails to fetch is
recorded in scrape_errors instead of aborting the whole run.

Embeddings are computed locally with sentence-transformers
(all-MiniLM-L6-v2, 384 dims) -- no API key, nothing leaves this machine.
MySQL 8.0 has no native VECTOR type (that arrived in 9.0), so
article_text_embedding is stored as a JSON array of floats; see the comment
at the top of schema.sql.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import mysql.connector
import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
import nhs_symptoms as ns  # NHSContentClient, fetch_symptom_index, extract_sections, clean_html, safe_slug, write_json

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def load_db_config() -> dict:
    load_dotenv(PROJECT_ROOT / ".env")
    return {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        # Own database, separate from DB_NAME (nhs_directory, used by
        # db/ingest_dohs_to_mysql.py) -- same server and credentials, no
        # shared tables.
        "database": os.getenv("SYMPTOMS_DB_NAME", "nhs_symptoms"),
        # Forces TLS 1.3. Python's ssl module has no public API for pinning
        # a specific TLS 1.3 ciphersuite (only set_ciphers(), which is a
        # TLS<=1.2-only mechanism) -- so the exact suite negotiated
        # (commonly TLS_AES_256_GCM_SHA384) is up to OpenSSL, not us.
        "ssl_disabled": False,
        "tls_versions": ["TLSv1.3"],
    }


def short_hash(*parts: str) -> str:
    digest = hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def build_symptom_id(url: str) -> str:
    return "SYM-" + short_hash(url)


def clean(value):
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_iso_date(value) -> date | None:
    """Symptom date fields come back as full ISO datetimes
    (e.g. "2024-11-05T00:00:00+00:00"); only the date part matters here."""
    value = clean(value)
    if value is None:
        return None
    value = value.split("T", 1)[0]
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_uk_date(value) -> date | None:
    """The local raw_pages/*.json dump (a separate HTML scrape of the public
    NHS pages, not the Content API) renders review dates as "08 April 2024"."""
    value = clean(value)
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%d %B %Y").date()
    except ValueError:
        return None


def parse_iso_datetime_naive(value) -> datetime | None:
    value = clean(value)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def run_local_ingest(local_dir: Path) -> tuple[dict, list[dict], list[dict]]:
    """Load pre-scraped NHS page JSON files from local_dir instead of hitting
    the live NHS API. These come from a separate HTML scrape of the public
    NHS website (requested_url/final_url/http_status/page_text/review_dates
    shape) -- not the Content API's schema.org format used by run_scrape --
    so the field mapping below is specific to that shape. Used when the API
    is unavailable but a local dump of raw pages already exists."""
    files = sorted(local_dir.glob("*.json"))
    pages_found = len(files)

    symptom_rows: list[dict] = []
    error_rows: list[dict] = []
    succeeded = 0
    failed = 0

    for file_path in files:
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            error_rows.append(
                {
                    "url": file_path.name,
                    "http_status": None,
                    "error_message": f"Failed to read/parse {file_path.name}: {exc}"[:1000],
                }
            )
            failed += 1
            continue

        url = data.get("final_url") or data.get("requested_url")
        http_status = data.get("http_status")
        if not url or http_status != 200:
            error_rows.append(
                {
                    "url": url or file_path.name,
                    "http_status": http_status,
                    "error_message": f"Non-200 or missing URL in {file_path.name}"[:1000],
                }
            )
            failed += 1
            continue

        review_dates = data.get("review_dates") or {}
        symptom_rows.append(
            {
                "url": url,
                "symptom_name": clean(data.get("title")) or file_path.stem,
                "description": clean(data.get("meta_description")),
                "reviewed_date": parse_uk_date(review_dates.get("page_last_reviewed")),
                "next_reviewed_date": parse_uk_date(review_dates.get("next_review_due")),
                "article_text": clean(data.get("page_text")),
                "page_scraped_at_utc": parse_iso_datetime_naive(data.get("retrieved_at_utc")),
            }
        )
        succeeded += 1

    run_info = {
        "base_url": "https://www.nhs.uk",
        "scrape_method": "local_raw_pages_json",
        "pages_found": pages_found,
        "pages_selected": pages_found,
        "pages_succeeded": succeeded,
        "pages_failed": failed,
        "complete": True,
    }
    return run_info, symptom_rows, error_rows


def first_date(raw_list) -> date | None:
    for entry in raw_list or []:
        parsed = parse_iso_date(entry) if isinstance(entry, str) else None
        if parsed:
            return parsed
    return None


def build_article_text(sections: list[dict]) -> str | None:
    parts = []
    for section in sections:
        headline = section.get("headline")
        text = section.get("text") or ""
        parts.append(f"{headline}\n{text}" if headline else text)
    text = "\n\n".join(p for p in parts if p.strip())
    return text or None


def run_scrape(env: str, data_dir: Path, limit: int | None) -> tuple[dict, list[dict], list[dict]]:
    """Fetch the symptom index, then each symptom's detail page. Returns
    (run_info, symptom_rows, error_rows); a failed detail fetch is recorded
    in error_rows rather than raising."""
    client = ns.NHSContentClient(env=env, api_key=os.getenv("NHS_CONTENT_API_KEY") or None)
    raw_index_dir = data_dir / "raw" / "symptoms_index"
    raw_detail_dir = data_dir / "raw" / "symptom_details"

    symptom_rows: list[dict] = []
    error_rows: list[dict] = []
    succeeded = 0
    failed = 0

    # A failed index fetch means we don't know how many symptom pages exist
    # at all -- there's nothing to loop over. Record it as a scrape error and
    # let main() still write a scrape_runs row (complete=False) rather than
    # crashing before any DB write happens.
    try:
        index_rows = ns.fetch_symptom_index(client, raw_index_dir)
    except requests.exceptions.RequestException as exc:
        status = None
        response = getattr(exc, "response", None)
        if response is not None:
            status = response.status_code
        error_rows.append(
            {
                "url": f"{client.base}/symptoms",
                "http_status": status,
                "error_message": f"Failed to fetch symptom index: {exc}"[:1000],
            }
        )
        run_info = {
            "base_url": client.base,
            "scrape_method": f"nhs_website_content_api:/symptoms ({env})",
            "pages_found": 0,
            "pages_selected": 0,
            "pages_succeeded": 0,
            "pages_failed": 0,
            "complete": False,
        }
        return run_info, symptom_rows, error_rows

    pages_found = len(index_rows)
    selected_rows = index_rows[:limit] if limit else index_rows
    pages_selected = len(selected_rows)

    for number, row in enumerate(selected_rows, start=1):
        api_url = row["source_api_url"]
        if not api_url:
            continue

        try:
            detail = client.get_returned_api_url(api_url)
        except (requests.exceptions.RequestException, ValueError) as exc:
            status = None
            response = getattr(exc, "response", None)
            if response is not None:
                status = response.status_code
            error_rows.append(
                {
                    "url": api_url,
                    "http_status": status,
                    "error_message": str(exc)[:1000],
                }
            )
            failed += 1
            continue

        slug = ns.safe_slug(api_url, f"symptom-{number:04d}")
        ns.write_json(raw_detail_dir / f"{slug}.json", detail)

        sections = ns.extract_sections(detail)
        detail_meta = detail.get("mainEntityOfPage") or {}
        last_reviewed = detail.get("lastReviewed") or detail_meta.get("lastReviewed")
        review_due = detail.get("reviewDue") or detail_meta.get("reviewDue")

        symptom_rows.append(
            {
                "url": detail.get("webpage") or api_url,
                "symptom_name": detail.get("name") or row["name"],
                "description": ns.clean_html(detail.get("description") or row["description"]) or None,
                "reviewed_date": first_date(last_reviewed) or first_date(json.loads(row["last_reviewed_raw"] or "[]")),
                "next_reviewed_date": parse_iso_date(review_due) or parse_iso_date(row["review_due"]),
                "article_text": build_article_text(sections),
                "page_scraped_at_utc": utc_now_dt(),
            }
        )
        succeeded += 1

    run_info = {
        "base_url": client.base,
        "scrape_method": f"nhs_website_content_api:/symptoms ({env})",
        "pages_found": pages_found,
        "pages_selected": pages_selected,
        "pages_succeeded": succeeded,
        "pages_failed": failed,
        "complete": True,
    }
    return run_info, symptom_rows, error_rows


def compute_embeddings(texts: list[str | None]) -> list[list[float] | None]:
    """Encode each non-empty article_text locally. A model's own max
    sequence length (256 tokens for all-MiniLM-L6-v2) truncates long
    articles; that's the model's job, not ours."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    indices = [i for i, text in enumerate(texts) if text]
    embeddings: list[list[float] | None] = [None] * len(texts)
    if indices:
        vectors = model.encode(
            [texts[i] for i in indices],
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        for index, vector in zip(indices, vectors):
            embeddings[index] = [round(float(x), 6) for x in vector]
    return embeddings


SCRAPE_RUN_INSERT = """
    INSERT INTO scrape_runs (
        scrape_run_id, scraped_at_utc, base_url, scrape_method,
        pages_found, pages_selected, pages_succeeded, pages_failed, complete
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

SYMPTOM_UPSERT = """
    INSERT INTO symptoms (
        symptom_id, scrape_run_id, symptom_name, url, description,
        reviewed_date, next_reviewed_date, article_text,
        article_text_embedding, embedding_model, page_scraped_at_utc
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        scrape_run_id = VALUES(scrape_run_id),
        symptom_name = VALUES(symptom_name),
        description = VALUES(description),
        reviewed_date = VALUES(reviewed_date),
        next_reviewed_date = VALUES(next_reviewed_date),
        article_text = VALUES(article_text),
        article_text_embedding = VALUES(article_text_embedding),
        embedding_model = VALUES(embedding_model),
        page_scraped_at_utc = VALUES(page_scraped_at_utc)
"""

SCRAPE_ERROR_INSERT = """
    INSERT INTO scrape_errors (error_id, scrape_run_id, url, http_status, error_message, recorded_at_utc)
    VALUES (%s, %s, %s, %s, %s, %s)
"""


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    parser = argparse.ArgumentParser(
        description="Scrape NHS symptom pages and load them into MySQL."
    )
    parser.add_argument(
        "--env",
        choices=tuple(ns.CONTENT_BASES),
        default=os.getenv("NHS_ENV", "sandbox"),
    )
    parser.add_argument(
        "--data-dir",
        default=os.getenv("NHS_DATA_DIR", "./data"),
        help="Project-relative or absolute output directory for raw JSON.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only fetch detail pages for the first N symptoms (useful for a quick test run).",
    )
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Skip local embedding generation; article_text_embedding, "
        "embedding_model, and page_scraped_at_utc are left NULL.",
    )
    parser.add_argument(
        "--source",
        choices=("api", "local"),
        default="api",
        help="'api' (default) scrapes the live NHS Website Content API. "
        "'local' loads pre-scraped page JSON files from --local-dir instead "
        "(useful when the API is down but a local dump already exists).",
    )
    parser.add_argument(
        "--local-dir",
        default=str(PROJECT_ROOT.parent / "raw_pages"),
        help="Directory of pre-scraped page JSON files, used when --source local.",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser()
    if not data_dir.is_absolute():
        data_dir = (PROJECT_ROOT / data_dir).resolve()

    scrape_run_id = f"SYM-RUN-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    started_at = utc_now_dt()

    if args.source == "local":
        local_dir = Path(args.local_dir).expanduser().resolve()
        print(f"Starting scrape run {scrape_run_id} (source=local, dir={local_dir})")
        run_info, symptom_rows, error_rows = run_local_ingest(local_dir)
    else:
        print(f"Starting scrape run {scrape_run_id} (env={args.env})")
        run_info, symptom_rows, error_rows = run_scrape(args.env, data_dir, args.limit)

    print(f"Pages found:    {run_info['pages_found']}")
    print(f"Pages selected: {run_info['pages_selected']}")
    print(f"Succeeded:      {run_info['pages_succeeded']}")
    print(f"Failed:         {run_info['pages_failed']}")

    if args.skip_embeddings:
        print("Skipping embeddings (--skip-embeddings): article_text_embedding "
              "and embedding_model will be NULL.")
        embeddings = [None] * len(symptom_rows)
    else:
        print(f"Computing embeddings locally with {EMBEDDING_MODEL_NAME} ...")
        embeddings = compute_embeddings([row["article_text"] for row in symptom_rows])

    connection = mysql.connector.connect(**load_db_config())
    cursor = connection.cursor()

    cursor.execute(
        SCRAPE_RUN_INSERT,
        (
            scrape_run_id,
            started_at,
            run_info["base_url"],
            run_info["scrape_method"],
            run_info["pages_found"],
            run_info["pages_selected"],
            run_info["pages_succeeded"],
            run_info["pages_failed"],
            run_info["complete"],
        ),
    )
    connection.commit()

    symptom_params = []
    for row, embedding in zip(symptom_rows, embeddings):
        symptom_params.append(
            (
                build_symptom_id(row["url"]),
                scrape_run_id,
                row["symptom_name"],
                row["url"],
                row["description"],
                row["reviewed_date"],
                row["next_reviewed_date"],
                row["article_text"],
                json.dumps(embedding) if embedding is not None else None,
                EMBEDDING_MODEL_NAME if embedding is not None else None,
                row["page_scraped_at_utc"],
            )
        )
    if symptom_params:
        cursor.executemany(SYMPTOM_UPSERT, symptom_params)

    error_params = [
        (
            f"{scrape_run_id}#ERR#{index}",
            scrape_run_id,
            error["url"],
            error["http_status"],
            error["error_message"],
            utc_now_dt(),
        )
        for index, error in enumerate(error_rows, start=1)
    ]
    if error_params:
        cursor.executemany(SCRAPE_ERROR_INSERT, error_params)
    connection.commit()

    cursor.close()
    connection.close()

    print(f"Done. Scrape run {scrape_run_id} (complete={run_info['complete']}).")
    print(f"Symptoms upserted: {len(symptom_params)}")
    print(f"Errors recorded:   {len(error_params)}")


if __name__ == "__main__":
    main()
