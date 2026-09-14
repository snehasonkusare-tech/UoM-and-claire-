# NHS API starter — Manchester Community Navigation

This starter intentionally stays within the NHS sources requested:

1. NHS Website Content API v2
2. Directory of Healthcare Services / Service Search API v3
3. Legacy Directory of Services — Urgent & Emergency Care REST

The public NHS Symptoms A–Z webpage is not scraped. Symptom ingestion uses the
NHS Website Content API `/symptoms` endpoint.

## 1. Create the environment

### Windows PowerShell

```powershell
mkdir nhs-community-platform
cd nhs-community-platform
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks activation only for the current shell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

## 2. Folder layout

```text
project/
  .venv/                  # Python packages only
  .env                    # local credentials; never commit
  requirements.txt
  requirements-notebook.txt
  src/
    test_apis.py
    nhs_symptoms.py
  notebooks/
    NHS_API_Integration_Tests.ipynb
  postman/
    NHS_Community_Platform.postman_collection.json
    NHS_Integration.postman_environment.json
    NHS_Sandbox.postman_environment.json
    README.md
  data/                   # created automatically
    export_dohs_raw.py
    export_website_content_raw.py
    raw/
      directory_of_healthcare_services_v3/
      nhs_website_content_v2/
    export_state/         # resumable checkpoints; no API keys
    processed/
```

The virtual environment does not control where downloaded data goes.
`nhs_symptoms.py` explicitly resolves `NHS_DATA_DIR` relative to the project
root, which keeps all raw and processed NHS data under one chosen folder.

## 3. Smoke-test the APIs

Sandbox:

```bash
python src/test_apis.py --env sandbox
```

Each API smoke test runs independently, so a temporarily unavailable NHS
Website Content endpoint does not prevent the Directory of Healthcare Services
v3 test from running. HTTP 502, 503 and 504 responses are retried up to three
times with exponential backoff (one second, then two seconds). The final summary
lists passed and failed tests, and the command returns a non-zero exit code if
any test failed. Integration requests are paced at least 1.05 seconds apart to
remain within the NHS integration limit of one request per second.

Integration after adding API keys to `.env`:

```bash
python src/test_apis.py --env integration
```

For equivalent interactive requests in Postman, import the collection and
environment files under `postman/` and follow the detailed instructions in
[`postman/README.md`](postman/README.md). The Postman environment is separate
from `.env`, so enter the application key in Postman as described there.

## 3a. Visual API tests in Jupyter

The notebook at `notebooks/NHS_API_Integration_Tests.ipynb` runs the same core
integration checks with short PASS/FAIL messages, compact tables, and a final
summary. It reuses the retry/backoff and request pacing from `src/test_apis.py`.

Install the optional notebook tools and open it from the project root:

```bash
pip install -r requirements-notebook.txt
jupyter lab notebooks/NHS_API_Integration_Tests.ipynb
```

Run the notebook cells from top to bottom. It reads the existing `.env` file
and never prints the API-key values.

Legacy DoS REST requires separate DoS username/password:

```bash
python src/test_apis.py --env integration --include-dos --postcode "M1 1AE"
```

## 4. Download symptom index

```bash
python src/nhs_symptoms.py --env integration
```

Outputs:

- `data/raw/symptoms_index/symptoms_page_XXX.json`
- `data/processed/symptoms_index.csv`
- `data/processed/symptoms_index.jsonl`

## 5. Fetch and flatten full detail pages

```bash
python src/nhs_symptoms.py --env integration --details --modules
```

Additional outputs:

- raw source JSON for each page
- `data/processed/symptoms_details.jsonl`
- `data/processed/symptoms_sections.csv`

`sections.csv` is deliberately one row per NHS page section, which is a
better shape for later search/RAG ingestion than concatenating every page into
one giant text field.

## 6. Incremental refresh

```bash
python src/nhs_symptoms.py --env integration --modified-since 2026-09-01
```

This uses `/symptoms` with `startDate`, `orderBy=dateModified` and
`order=newest`.

## 6a. Export untouched raw API responses

The raw exporters deliberately do not create CSV files. They preserve each
successful JSON response as a separate page and keep resumable checkpoints in
`data/export_state/`. API keys are read from the project-root `.env` and are
never written to output files.

Export all Directory of Healthcare Services v3 search documents:

```bash
python data/export_dohs_raw.py --env integration
```

This requests up to 1,000 records per page, orders by `SearchKey`, and uses the
last key as the next-page cursor. It waits at least 1.05 seconds between
integration requests and stops before 1,400 requests in one run. Running the
same command again resumes from the checkpoint.

Export NHS Website Content v2 directories and their discoverable pages:

```bash
python data/export_website_content_raw.py --env integration
```

This uses `/manifest/pages/` as the authoritative page directory, saves all
explicit v2 catalogue endpoints, and then saves the detail JSON for every URL
it discovers. Integration calls are paced at 0.55 seconds (below 120/minute).
If the NHS staging manifest or page origin returns its current HTML 401, the
failure is recorded without replacing any raw JSON and the next independent
directory is attempted. Rerun the same command after NHS restores the affected
routes to continue.

## 7. Production hygiene

- Keep `.env` out of Git.
- Keep raw source JSON immutable.
- Build transformed data under `data/processed`.
- Preserve NHS source URLs and review/modified metadata.
- Add the required NHS attribution in any user-facing product.
- Do not treat Symptoms content as a diagnostic or triage model.
