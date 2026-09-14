from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv


CONTENT_BASES = {
    "sandbox": "https://sandbox.api.service.nhs.uk/nhs-website-content",
    "integration": "https://int.api.service.nhs.uk/nhs-website-content",
    "production": "https://api.service.nhs.uk/nhs-website-content",
}

DOHS_BASES = {
    "sandbox": "https://sandbox.api.service.nhs.uk/service-search-api",
    "integration": "https://int.api.service.nhs.uk/service-search-api",
    "production": "https://api.service.nhs.uk/service-search-api",
}

RETRYABLE_STATUS_CODES = {502, 503, 504}
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.0
INTEGRATION_REQUEST_INTERVAL = 1.05
_last_integration_request = 0.0


def pace_request(url: str) -> None:
    """Keep integration request starts within the NHS one-request/second limit."""
    global _last_integration_request

    if urlparse(url).hostname != "int.api.service.nhs.uk":
        return

    elapsed = time.monotonic() - _last_integration_request
    if elapsed < INTEGRATION_REQUEST_INTERVAL:
        time.sleep(INTEGRATION_REQUEST_INTERVAL - elapsed)
    _last_integration_request = time.monotonic()


def get_json(
    url: str,
    *,
    params=None,
    headers=None,
    auth=None,
    max_attempts: int = MAX_ATTEMPTS,
    backoff_seconds: float = BACKOFF_SECONDS,
) -> dict:
    for attempt in range(1, max_attempts + 1):
        pace_request(url)
        response = requests.get(
            url,
            params=params,
            headers=headers or {"Accept": "application/json"},
            auth=auth,
            timeout=(5, 30),
        )
        print(f"\nGET {response.url}")
        print(f"HTTP {response.status_code}")

        should_retry = (
            response.status_code in RETRYABLE_STATUS_CODES
            and attempt < max_attempts
        )
        if should_retry:
            delay = backoff_seconds * (2 ** (attempt - 1))
            print(
                f"Temporary NHS API error; retrying in {delay:g} seconds "
                f"(attempt {attempt + 1}/{max_attempts})..."
            )
            time.sleep(delay)
            continue

        response.raise_for_status()
        return response.json()

    raise RuntimeError("Request retry loop ended unexpectedly.")


def api_key_headers(key: str | None) -> dict:
    headers = {"Accept": "application/json"}
    if key:
        headers["apikey"] = key
    return headers


def test_content(env: str) -> None:
    key = os.getenv("NHS_CONTENT_API_KEY") or None
    data = get_json(
        f"{CONTENT_BASES[env]}/symptoms",
        params={"category": "a", "page": 1},
        headers=api_key_headers(key),
    )
    links = data.get("significantLink") or []
    print(f"NHS Website Content API: {len(links)} symptom links returned")
    if links:
        print(json.dumps(links[0], indent=2)[:1500])


def test_dohs(env: str) -> None:
    key = os.getenv("NHS_DOHS_API_KEY") or None
    data = get_json(
        f"{DOHS_BASES[env]}/",
        params={
            "api-version": "3",
            "search": "Y02494",
            "$top": "5",
        },
        headers=api_key_headers(key),
    )
    values = data.get("value") or []
    print(f"Directory of Healthcare Services v3: {len(values)} records returned")
    if values:
        print(json.dumps(values[0], indent=2)[:1500])


def test_dos_legacy(postcode: str, distance_miles: float) -> None:
    username = os.getenv("NHS_DOS_USERNAME")
    password = os.getenv("NHS_DOS_PASSWORD")
    base = (
        os.getenv("NHS_DOS_BASE")
        or "https://usertest.directoryofservices.nhs.uk/app/controllers/api/v1.0"
    ).rstrip("/")

    if not username or not password:
        raise RuntimeError(
            "Set NHS_DOS_USERNAME and NHS_DOS_PASSWORD before testing legacy DoS REST."
        )

    # Service type 13 = Pharmacy.
    # caseId=0, gppracticeId=0, age=0, gender=0, disposition=0.
    path = f"services/byServiceType/0/{postcode}/{distance_miles}/0/0/0/0/13/10"
    data = get_json(
        f"{base}/{path}",
        headers={"Accept": "application/json"},
        auth=(username, password),
    )
    print("Legacy DoS urgent/emergency REST response:")
    print(json.dumps(data, indent=2)[:3000])


def run_test(name: str, test: Callable[[], None]) -> str | None:
    print(f"\n--- {name} ---")
    try:
        test()
    except Exception as exc:
        print(f"\n[FAIL] {name}")
        print(f"{type(exc).__name__}: {exc}")
        return name
    print(f"\n[PASS] {name}")
    return None


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env",
        choices=("sandbox", "integration", "production"),
        default=os.getenv("NHS_ENV", "sandbox"),
    )
    parser.add_argument("--include-dos", action="store_true")
    parser.add_argument("--postcode", default="M1 1AE")
    parser.add_argument("--distance", type=float, default=10)
    args = parser.parse_args()

    print("=" * 50)
    print("NHS API SMOKE TEST")
    print(f"Environment: {args.env}")
    print("=" * 50)

    tests = [
        (
            "NHS Website Content API",
            lambda: test_content(args.env),
        ),
        (
            "Directory of Healthcare Services v3",
            lambda: test_dohs(args.env),
        ),
    ]

    if args.include_dos:
        tests.append(
            (
                "Legacy DoS urgent/emergency REST",
                lambda: test_dos_legacy(args.postcode, args.distance),
            )
        )

    failures = [
        failure
        for name, test in tests
        if (failure := run_test(name, test)) is not None
    ]

    print("\nAPI smoke-test summary")
    print(f"Passed: {len(tests) - len(failures)}")
    print(f"Failed: {len(failures)}")
    for failure in failures:
        print(f"- {failure}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
