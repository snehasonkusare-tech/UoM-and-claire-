from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


CONTENT_BASES = {
    "sandbox": "https://sandbox.api.service.nhs.uk/nhs-website-content",
    "integration": "https://int.api.service.nhs.uk/nhs-website-content",
    "production": "https://api.service.nhs.uk/nhs-website-content",
}

# Conservative client-side pacing. This matters mainly when --details is used.
MIN_REQUEST_INTERVAL = {
    "sandbox": 1.05,
    "integration": 1.05,
    "production": 0.06,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def clean_html(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(BeautifulSoup(value, "html.parser").stripped_strings)


def collect_text_fields(node: Any) -> list[str]:
    """Recursively collect schema.org WebPageElement 'text' fields."""
    found: list[str] = []
    if isinstance(node, dict):
        text = node.get("text")
        if isinstance(text, str) and text.strip():
            plain = clean_html(text)
            if plain:
                found.append(plain)
        for value in node.values():
            found.extend(collect_text_fields(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(collect_text_fields(item))
    return found


def compact_aspect(value: Any) -> str:
    if isinstance(value, str):
        return value.rstrip("/").rsplit("/", 1)[-1]
    if isinstance(value, dict):
        raw = value.get("@id") or value.get("url") or value.get("name")
        return compact_aspect(raw)
    return ""


def safe_slug(url: str, fallback: str) -> str:
    path = urlparse(url).path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if path else fallback
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return name or fallback


class NHSContentClient:
    def __init__(self, env: str, api_key: str | None, timeout: tuple[int, int] = (5, 30)):
        if env not in CONTENT_BASES:
            raise ValueError(f"Unsupported environment: {env}")
        self.env = env
        self.base = CONTENT_BASES[env].rstrip("/")
        self.timeout = timeout
        self._last_request = 0.0

        if env != "sandbox" and not api_key:
            raise RuntimeError(
                "NHS_CONTENT_API_KEY is required for integration/production."
            )

        retry = Retry(
            total=5,
            connect=3,
            read=3,
            status=5,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)

        self.session = requests.Session()
        self.session.mount("https://", adapter)
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "Manchester-Community-Navigation/0.1",
            }
        )
        if api_key:
            self.session.headers["apikey"] = api_key

    def _pace(self) -> None:
        minimum = MIN_REQUEST_INTERVAL[self.env]
        elapsed = time.monotonic() - self._last_request
        if elapsed < minimum:
            time.sleep(minimum - elapsed)

    def get_path(self, path: str, params: dict | None = None) -> dict:
        self._pace()
        url = f"{self.base}/{path.lstrip('/')}"
        response = self.session.get(url, params=params, timeout=self.timeout)
        self._last_request = time.monotonic()
        response.raise_for_status()
        return response.json()

    def get_returned_api_url(self, api_url: str, params: dict | None = None) -> dict:
        """
        Follow a URL returned by the Content API, but force it back onto the
        currently selected NHS environment so a sandbox/integration run cannot
        accidentally jump to production.
        """
        parsed = urlparse(api_url)
        marker = "/nhs-website-content/"
        if marker not in parsed.path:
            raise ValueError(f"Unexpected NHS Content API URL: {api_url}")
        relative = parsed.path.split(marker, 1)[1]
        return self.get_path(relative, params=params)


def normalize_index_item(item: dict, fetched_at: str) -> dict:
    meta = item.get("mainEntityOfPage") or {}

    codes = []
    for code in ensure_list(meta.get("code")):
        if isinstance(code, dict):
            code_value = code.get("codeValue")
            if code_value:
                codes.append(str(code_value))

    return {
        "name": (item.get("name") or "").strip(),
        "description": clean_html(item.get("description")),
        "source_api_url": item.get("url") or "",
        "article_status": meta.get("articleStatus") or item.get("articleStatus") or "",
        "page_type": meta.get("@type") or "",
        "genres": " | ".join(str(x) for x in ensure_list(meta.get("genre")) if x),
        "aliases": " | ".join(str(x) for x in ensure_list(meta.get("alternateName")) if x),
        "keywords": " | ".join(str(x) for x in ensure_list(meta.get("keywords")) if x),
        "date_published": meta.get("datePublished") or "",
        "date_modified": meta.get("dateModified") or "",
        "review_due": meta.get("reviewDue") or "",
        "last_reviewed_raw": json.dumps(
            ensure_list(meta.get("lastReviewed")), ensure_ascii=False
        ),
        "snomed_codes": " | ".join(codes),
        "fetched_at_utc": fetched_at,
    }


def fetch_symptom_index(
    client: NHSContentClient,
    raw_dir: Path,
    modified_since: str | None = None,
) -> list[dict]:
    fetched_at = utc_now()
    page = 1
    rows: list[dict] = []
    seen_urls: set[str] = set()

    while True:
        params: dict[str, str | int] = {"page": page}
        if modified_since:
            params.update(
                {
                    "startDate": modified_since,
                    "orderBy": "dateModified",
                    "order": "newest",
                }
            )

        payload = client.get_path("symptoms", params=params)
        write_json(raw_dir / f"symptoms_page_{page:03d}.json", payload)

        items = payload.get("significantLink") or []
        if not items:
            break

        for item in items:
            if not isinstance(item, dict):
                continue
            record = normalize_index_item(item, fetched_at)
            key = record["source_api_url"] or record["name"].casefold()
            if key and key not in seen_urls:
                seen_urls.add(key)
                rows.append(record)

        next_page = any(
            isinstance(link, dict)
            and str(link.get("name", "")).strip().casefold() == "next page"
            for link in (payload.get("relatedLink") or [])
        )
        if not next_page:
            break

        page += 1

    rows.sort(key=lambda r: (r["name"].casefold(), r["source_api_url"]))
    return rows


def extract_sections(detail: dict) -> list[dict]:
    sections: list[dict] = []

    for index, part in enumerate(ensure_list(detail.get("hasPart")), start=1):
        if not isinstance(part, dict):
            continue

        text_fragments = collect_text_fields(part)
        text = "\n\n".join(dict.fromkeys(t for t in text_fragments if t))
        if not text:
            continue

        sections.append(
            {
                "section_index": index,
                "headline": clean_html(part.get("headline")),
                "health_aspect": compact_aspect(part.get("hasHealthAspect")),
                "text": text,
            }
        )

    if not sections:
        all_text = "\n\n".join(dict.fromkeys(collect_text_fields(detail)))
        if all_text:
            sections.append(
                {
                    "section_index": 1,
                    "headline": "Full page",
                    "health_aspect": "",
                    "text": all_text,
                }
            )

    return sections


def fetch_details(
    client: NHSContentClient,
    index_rows: list[dict],
    raw_detail_dir: Path,
    modules: bool,
) -> tuple[list[dict], list[dict]]:
    detail_rows: list[dict] = []
    section_rows: list[dict] = []

    for number, row in enumerate(index_rows, start=1):
        api_url = row["source_api_url"]
        if not api_url:
            continue

        # NHS documents `modules=true` for condition/medicine detail endpoints.
        # Do not attach it blindly to other page families returned by /symptoms.
        returned_path = urlparse(api_url).path
        supports_modules = (
            "/nhs-website-content/conditions/" in returned_path
            or "/nhs-website-content/medicines/" in returned_path
        )
        detail = client.get_returned_api_url(
            api_url,
            params={"modules": "true"} if (modules and supports_modules) else None,
        )

        slug = safe_slug(api_url, f"symptom-{number:04d}")
        write_json(raw_detail_dir / f"{slug}.json", detail)

        sections = extract_sections(detail)
        detail_record = {
            "name": detail.get("name") or row["name"],
            "description": clean_html(detail.get("description") or row["description"]),
            "source_api_url": api_url,
            "nhs_webpage": detail.get("webpage") or "",
            "date_modified": detail.get("dateModified")
            or (detail.get("mainEntityOfPage") or {}).get("dateModified")
            or row["date_modified"],
            "fetched_at_utc": utc_now(),
            "sections": sections,
        }
        detail_rows.append(detail_record)

        for section in sections:
            section_rows.append(
                {
                    "name": detail_record["name"],
                    "source_api_url": api_url,
                    "nhs_webpage": detail_record["nhs_webpage"],
                    "date_modified": detail_record["date_modified"],
                    **section,
                }
            )

    detail_rows.sort(key=lambda r: r["name"].casefold())
    section_rows.sort(
        key=lambda r: (r["name"].casefold(), int(r["section_index"]))
    )
    return detail_rows, section_rows


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    parser = argparse.ArgumentParser(
        description="Download and normalize NHS Website Content API symptom data."
    )
    parser.add_argument(
        "--env",
        choices=tuple(CONTENT_BASES),
        default=os.getenv("NHS_ENV", "sandbox"),
    )
    parser.add_argument(
        "--data-dir",
        default=os.getenv("NHS_DATA_DIR", "./data"),
        help="Project-relative or absolute output directory.",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Also fetch each symptom/condition page returned by /symptoms.",
    )
    parser.add_argument(
        "--modules",
        action="store_true",
        help="When fetching details, request modules=true.",
    )
    parser.add_argument(
        "--modified-since",
        help="Optional YYYY-MM-DD startDate for incremental refreshes.",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser()
    if not data_dir.is_absolute():
        data_dir = (project_root / data_dir).resolve()

    raw_index_dir = data_dir / "raw" / "symptoms_index"
    raw_detail_dir = data_dir / "raw" / "symptom_details"
    processed_dir = data_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    client = NHSContentClient(
        env=args.env,
        api_key=os.getenv("NHS_CONTENT_API_KEY") or None,
    )

    rows = fetch_symptom_index(
        client,
        raw_index_dir,
        modified_since=args.modified_since,
    )

    index_csv = processed_dir / "symptoms_index.csv"
    index_jsonl = processed_dir / "symptoms_index.jsonl"
    pd.DataFrame(rows).to_csv(index_csv, index=False, encoding="utf-8")
    append_jsonl(index_jsonl, rows)

    print(f"Environment: {args.env}")
    print(f"Symptoms indexed: {len(rows)}")
    print(f"CSV:   {index_csv}")
    print(f"JSONL: {index_jsonl}")

    if args.details:
        details, sections = fetch_details(
            client,
            rows,
            raw_detail_dir,
            modules=args.modules,
        )
        details_jsonl = processed_dir / "symptoms_details.jsonl"
        sections_csv = processed_dir / "symptoms_sections.csv"
        append_jsonl(details_jsonl, details)
        pd.DataFrame(sections).to_csv(sections_csv, index=False, encoding="utf-8")
        print(f"Details JSONL: {details_jsonl}")
        print(f"Sections CSV:  {sections_csv}")


if __name__ == "__main__":
    main()
