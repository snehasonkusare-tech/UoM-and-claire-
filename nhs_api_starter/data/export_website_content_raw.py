"""Export raw NHS Website Content API v2 responses.

The manifest is the authoritative directory of content pages. The exporter
saves every manifest response, then downloads every listed page unchanged.
It also saves all explicit catalogue/root endpoints from the current v2 OAS.
Runs are resumable because existing immutable response files are never replaced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "nhs_website_content_v2"
STATE_DIR = PROJECT_ROOT / "data" / "export_state"
STATE_FILE = STATE_DIR / "nhs_website_content_v2.json"

BASE_URLS = {
    "sandbox": "https://sandbox.api.service.nhs.uk/nhs-website-content",
    "integration": "https://int.api.service.nhs.uk/nhs-website-content",
    "production": "https://api.service.nhs.uk/nhs-website-content",
}

# NHS Website Content v2: sandbox 60/min, integration 120/min,
# production 1,200/min. Small margins keep starts below each cap.
MIN_INTERVAL_SECONDS = {"sandbox": 1.05, "integration": 0.55, "production": 0.06}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Every concrete GET path in the current NHS Website Content v2 OAS, excluding
# /manifest/pages (handled separately) and wildcard detail routes (discovered
# through the manifest and significantLink entries).
CATALOGUE_PATHS = [
    "health-a-to-z",
    "health-a-to-z/conditions",
    "conditions",
    "symptoms",
    "tests-and-treatments",
    "medicines",
    "mental-health",
    "live-well",
    "pregnancy",
    "nhs-services",
    "contraception",
    "vaccinations",
    "womens-health",
    "baby",
    "social-care-and-support",
]


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


def new_state(environment: str) -> dict[str, Any]:
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if state.get("environment") != environment:
            raise RuntimeError(
                "Existing Website Content checkpoint uses a different environment."
            )
        return state
    return {
        "api": "NHS Website Content API v2",
        "environment": environment,
        "base_url": BASE_URLS[environment],
        "requests_started": 0,
        "manifest_complete": False,
        "manifest_error": None,
        "catalogue_complete": False,
        "details_complete": False,
        "errors": [],
        "created_at_utc": utc_now(),
        "updated_at_utc": utc_now(),
    }


class PacedClient:
    def __init__(
        self,
        environment: str,
        api_key: str | None,
        state: dict[str, Any],
        max_requests: int,
    ) -> None:
        self.environment = environment
        self.state = state
        self.max_requests = max_requests
        self.requests_this_run = 0
        self.last_started = 0.0
        self.session = requests.Session()
        headers = {
            "Accept": "application/json",
            "User-Agent": "NHS-API-Starter-raw-export/1.0",
        }
        if api_key:
            headers["apikey"] = api_key
        self.session.headers.update(headers)

    def _pace_and_count(self) -> None:
        if self.requests_this_run >= self.max_requests:
            raise RuntimeError(
                f"Stopped at the configured limit of {self.max_requests} requests; "
                "rerun the command to resume."
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

    def get_path(
        self, path: str, params: dict[str, Any] | None = None, attempts: int = 5
    ) -> requests.Response:
        url = f"{BASE_URLS[self.environment].rstrip('/')}/{path.lstrip('/')}"
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            self._pace_and_count()
            try:
                response = self.session.get(
                    url, params=params, timeout=(10, 90), allow_redirects=True
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
            return response

        raise RuntimeError(f"Request failed: {last_error}")


def record_error(
    state: dict[str, Any], path: str, response: requests.Response | None, message: str
) -> None:
    item = {
        "path": path,
        "message": message,
        "recorded_at_utc": utc_now(),
    }
    if response is not None:
        item.update(
            {
                "status": response.status_code,
                "content_type": response.headers.get("Content-Type"),
                "www_authenticate": response.headers.get("WWW-Authenticate"),
            }
        )
    state["errors"].append(item)
    state["updated_at_utc"] = utc_now()
    atomic_json(STATE_FILE, state)


def parse_success(response: requests.Response, path: str) -> dict[str, Any]:
    if response.status_code != 200:
        raise RuntimeError(
            f"{path} returned HTTP {response.status_code} "
            f"({response.headers.get('Content-Type')})"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{path} returned non-JSON content") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} returned an unexpected JSON shape")
    return payload


def next_page_present(payload: dict[str, Any]) -> bool:
    return any(
        isinstance(link, dict)
        and str(link.get("name", "")).strip().casefold() == "next page"
        for link in (payload.get("relatedLink") or [])
    )


def safe_component(path: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", path.strip("/"))
    return value.strip("_") or "root"


def export_manifest(
    client: PacedClient, state: dict[str, Any]
) -> None:
    manifest_dir = RAW_DIR / "manifest"
    page = 1
    while True:
        destination = manifest_dir / f"page_{page:05d}.json"
        if destination.exists():
            payload = json.loads(destination.read_text(encoding="utf-8"))
        else:
            print(f"Requesting Website Content manifest page {page}...")
            try:
                response = client.get_path("manifest/pages/", {"page": page})
                payload = parse_success(response, "manifest/pages/")
            except (requests.RequestException, RuntimeError) as exc:
                state["manifest_error"] = str(exc)
                record_error(state, "manifest/pages/", locals().get("response"), str(exc))
                print(f"Manifest unavailable: {exc}")
                return
            atomic_bytes(destination, response.content)

        pagination = payload.get("pagination") or {}
        if not pagination.get("next"):
            state["manifest_complete"] = True
            state["manifest_error"] = None
            state["manifest_pages_saved"] = page
            state["manifest_reported_count"] = pagination.get("count")
            atomic_json(STATE_FILE, state)
            print(f"Manifest complete after {page} page(s).")
            return
        page += 1


def export_catalogue(
    client: PacedClient, state: dict[str, Any]
) -> None:
    catalogue_dir = RAW_DIR / "catalogue"
    for path in CATALOGUE_PATHS:
        page = 1
        while True:
            destination = catalogue_dir / safe_component(path) / f"page_{page:05d}.json"
            if destination.exists():
                payload = json.loads(destination.read_text(encoding="utf-8"))
            else:
                print(f"Requesting catalogue path /{path}, page {page}...")
                response: requests.Response | None = None
                try:
                    response = client.get_path(path, {"page": page})
                    payload = parse_success(response, path)
                except (requests.RequestException, RuntimeError) as exc:
                    record_error(state, path, response, str(exc))
                    print(f"Skipping /{path}: {exc}")
                    break
                atomic_bytes(destination, response.content)

            if not next_page_present(payload):
                break
            page += 1

    state["catalogue_complete"] = True
    state["updated_at_utc"] = utc_now()
    atomic_json(STATE_FILE, state)


def relative_api_path(url: str) -> str | None:
    parsed = urlparse(url)
    marker = "/nhs-website-content/"
    if marker not in parsed.path:
        return None
    return parsed.path.split(marker, 1)[1].strip("/")


def discover_detail_targets() -> dict[str, str]:
    targets: dict[str, str] = {}
    manifest_dir = RAW_DIR / "manifest"
    for source in sorted(manifest_dir.glob("page_*.json")) if manifest_dir.exists() else []:
        payload = json.loads(source.read_text(encoding="utf-8"))
        for item in payload.get("results") or []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            path = relative_api_path(str(item["url"]))
            if path:
                targets[path] = str(item.get("id") or "")

    catalogue_dir = RAW_DIR / "catalogue"
    for source in sorted(catalogue_dir.rglob("page_*.json")) if catalogue_dir.exists() else []:
        payload = json.loads(source.read_text(encoding="utf-8"))
        for item in payload.get("significantLink") or []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            path = relative_api_path(str(item["url"]))
            if path:
                targets.setdefault(path, "")
    return targets


def detail_filename(path: str, manifest_id: str) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
    tail = safe_component(path)[-100:]
    prefix = f"{manifest_id}_" if manifest_id else ""
    return f"{prefix}{tail}_{digest}.json"


def export_details(
    client: PacedClient, state: dict[str, Any]
) -> None:
    targets = discover_detail_targets()
    state["detail_targets_discovered"] = len(targets)
    atomic_json(STATE_FILE, state)
    if not targets:
        print("No detail URLs were discovered from successful directory responses.")
        return

    details_dir = RAW_DIR / "pages"
    consecutive_access_denials = 0
    for number, (path, manifest_id) in enumerate(sorted(targets.items()), start=1):
        destination = details_dir / detail_filename(path, manifest_id)
        if destination.exists():
            continue
        print(f"Requesting detail {number}/{len(targets)}: /{path}")
        response: requests.Response | None = None
        try:
            response = client.get_path(path)
            payload = parse_success(response, path)
        except (requests.RequestException, RuntimeError) as exc:
            record_error(state, path, response, str(exc))
            is_access_denial = bool(
                response is not None
                and response.status_code == 401
                and "text/html" in (response.headers.get("Content-Type") or "")
            )
            consecutive_access_denials = consecutive_access_denials + 1 if is_access_denial else 0
            if consecutive_access_denials >= 3:
                print(
                    "Stopping detail requests after three consecutive upstream HTML "
                    "401 responses; rerun later to resume."
                )
                return
            continue

        if not payload:
            record_error(state, path, response, "Successful response contained empty JSON")
            continue
        consecutive_access_denials = 0
        atomic_bytes(destination, response.content)

    state["details_complete"] = True
    state["updated_at_utc"] = utc_now()
    atomic_json(STATE_FILE, state)


def export(environment: str, max_requests: int, skip_details: bool) -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("NHS_CONTENT_API_KEY")
    if environment != "sandbox" and not api_key:
        raise RuntimeError("NHS_CONTENT_API_KEY is missing from the project-root .env")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = new_state(environment)
    client = PacedClient(environment, api_key, state, max_requests)

    print(f"Raw output: {RAW_DIR}")
    export_manifest(client, state)
    export_catalogue(client, state)
    if not skip_details:
        export_details(client, state)

    state["updated_at_utc"] = utc_now()
    atomic_json(STATE_FILE, state)
    print(
        "Website Content export pass finished. "
        f"Requests started across runs: {state['requests_started']}; "
        f"errors recorded: {len(state['errors'])}."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export NHS Website Content API v2 responses as raw JSON."
    )
    parser.add_argument(
        "--env", choices=sorted(BASE_URLS), default="integration"
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=10000,
        help="Safety cap for requests started by this run (default: 10000).",
    )
    parser.add_argument(
        "--skip-details",
        action="store_true",
        help="Save manifest/catalogue responses but do not fetch discovered pages.",
    )
    args = parser.parse_args()
    if args.max_requests < 1:
        parser.error("--max-requests must be positive")
    return args


if __name__ == "__main__":
    arguments = parse_args()
    export(arguments.env, arguments.max_requests, arguments.skip_details)
