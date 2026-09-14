from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "directory_of_healthcare_services_v3"
STATE_DIR = PROJECT_ROOT / "data" / "export_state"
STATE_FILE = STATE_DIR / "directory_of_healthcare_services_v3.json"

BASE_URLS = {
    "integration": "https://int.api.service.nhs.uk/service-search-api/",
    "production": "https://api.service.nhs.uk/service-search-api/",
}

# NHS: integration is 1 request/second and 1,500 requests/week.
MIN_INTERVAL_SECONDS = {"integration": 1.05, "production": 0.91}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def load_state(environment: str, page_size: int) -> dict[str, Any]:
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if state.get("environment") != environment:
            raise RuntimeError(
                "Existing DoHS checkpoint uses a different environment. "
                f"Move {STATE_FILE} and its raw folder before changing environments."
            )
        return state

    existing_pages = list(RAW_DIR.glob("page_*.json")) if RAW_DIR.exists() else []
    if existing_pages:
        raise RuntimeError(
            f"Raw pages exist but the checkpoint is missing: {STATE_FILE}. "
            "The exporter will not risk overwriting them."
        )

    return {
        "api": "Directory of Healthcare Services v3",
        "environment": environment,
        "base_url": BASE_URLS[environment],
        "page_size": page_size,
        "next_page": 1,
        "last_search_key": None,
        "records_saved": 0,
        "approximate_total": None,
        "requests_started": 0,
        "complete": False,
        "created_at_utc": utc_now(),
        "updated_at_utc": utc_now(),
    }


class PacedClient:
    def __init__(
        self,
        api_key: str,
        environment: str,
        state: dict[str, Any],
        max_requests: int,
    ) -> None:
        self.environment = environment
        self.state = state
        self.max_requests = max_requests
        self.requests_this_run = 0
        self.last_started = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "apikey": api_key,
                "User-Agent": "NHS-API-Starter-raw-export/1.0",
            }
        )

    def _pace_and_count(self) -> None:
        if self.requests_this_run >= self.max_requests:
            raise RuntimeError(
                f"Stopped at the configured limit of {self.max_requests} requests. "
                "Run the same command later to resume."
            )
        elapsed = time.monotonic() - self.last_started
        interval = MIN_INTERVAL_SECONDS[self.environment]
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self.last_started = time.monotonic()
        self.requests_this_run += 1
        self.state["requests_started"] += 1
        self.state["updated_at_utc"] = utc_now()
        atomic_json(STATE_FILE, self.state)

    def get(self, params: dict[str, str], attempts: int = 5) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            self._pace_and_count()
            try:
                response = self.session.get(
                    BASE_URLS[self.environment],
                    params=params,
                    timeout=(10, 120),
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt == attempts:
                    raise
                delay = min(30.0, 2.0 ** (attempt - 1))
                print(f"Network error; retrying in {delay:g}s ({attempt}/{attempts})")
                time.sleep(delay)
                continue

            if response.status_code in RETRYABLE_STATUS and attempt < attempts:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(
                    30.0, 2.0 ** (attempt - 1)
                )
                print(
                    f"HTTP {response.status_code}; retrying in {delay:g}s "
                    f"({attempt}/{attempts})"
                )
                time.sleep(delay)
                continue

            response.raise_for_status()
            return response

        raise RuntimeError(f"Request failed: {last_error}")


def escaped_odata_string(value: str) -> str:
    return value.replace("'", "''")


def export(environment: str, page_size: int, max_requests: int) -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("NHS_DOHS_API_KEY")
    if not api_key:
        raise RuntimeError("NHS_DOHS_API_KEY is missing from the project-root .env")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state(environment, page_size)
    if state.get("complete"):
        print(
            "DoHS export is already complete: "
            f"{state['records_saved']:,} records in {state['next_page'] - 1:,} pages."
        )
        return

    client = PacedClient(api_key, environment, state, max_requests)
    current_page_size = min(page_size, int(state.get("page_size") or page_size))

    print(f"Raw output: {RAW_DIR}")
    print(
        f"Resuming at page {state['next_page']:,}; "
        f"{state['records_saved']:,} records already saved."
    )

    while True:
        page_number = int(state["next_page"])
        last_key = state.get("last_search_key")
        params = {
            "api-version": "3",
            "search": "*",
            "$top": str(current_page_size),
            "$orderBy": "SearchKey asc",
        }
        if page_number == 1:
            params["$count"] = "true"
        if last_key:
            params["$filter"] = (
                "SearchKey gt '" + escaped_odata_string(str(last_key)) + "'"
            )

        print(
            f"Requesting page {page_number:,} (up to {current_page_size:,} records)..."
        )
        response = client.get(params)
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("DoHS returned a non-JSON success response") from exc

        values = payload.get("value")
        if not isinstance(values, list):
            raise RuntimeError("DoHS response is missing the JSON 'value' array")

        page_path = RAW_DIR / f"page_{page_number:06d}.json"
        if page_path.exists():
            raise RuntimeError(f"Refusing to overwrite existing raw page: {page_path}")

        if values:
            keys = [item.get("SearchKey") for item in values if isinstance(item, dict)]
            if len(keys) != len(values) or any(not key for key in keys):
                raise RuntimeError("A DoHS record is missing SearchKey; export stopped safely")
            if keys != sorted(keys) or len(keys) != len(set(keys)):
                raise RuntimeError("SearchKey ordering/uniqueness check failed; export stopped")
            if last_key and str(keys[0]) <= str(last_key):
                raise RuntimeError("Pagination did not advance beyond the checkpoint key")

        # Store the body returned by requests without parsing/reformatting it.
        atomic_bytes(page_path, response.content)

        if state.get("approximate_total") is None:
            state["approximate_total"] = payload.get("@odata.count")
        state["records_saved"] += len(values)
        state["next_page"] = page_number + 1
        state["last_search_key"] = values[-1]["SearchKey"] if values else last_key
        state["page_size"] = current_page_size
        state["updated_at_utc"] = utc_now()

        if not values or len(values) < current_page_size:
            state["complete"] = True
            state["completed_at_utc"] = utc_now()

        atomic_json(STATE_FILE, state)
        print(
            f"Saved {len(values):,}; cumulative {state['records_saved']:,}; "
            f"file {page_path.name}"
        )
        if state["complete"]:
            print(
                "DoHS raw export complete: "
                f"{state['records_saved']:,} records across {page_number:,} pages."
            )
            return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export all DoHS v3 documents to untouched raw JSON page files."
    )
    parser.add_argument(
        "--env", choices=sorted(BASE_URLS), default="integration"
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=1000,
        help="Requested records per page (1-1000; default: 1000).",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=1400,
        help="Safety cap for requests started by this run (default: 1400).",
    )
    args = parser.parse_args()
    if not 1 <= args.page_size <= 1000:
        parser.error("--page-size must be between 1 and 1000")
    if args.max_requests < 1:
        parser.error("--max-requests must be positive")
    return args


if __name__ == "__main__":
    arguments = parse_args()
    export(arguments.env, arguments.page_size, arguments.max_requests)
