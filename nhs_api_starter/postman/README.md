# Postman guide for the NHS API starter

This folder contains a ready-to-import Postman collection and two environments:

- `NHS_Community_Platform.postman_collection.json` — all API requests and automated response checks.
- `NHS_Integration.postman_environment.json` — use this for the NHS integration environment and your application API key.
- `NHS_Sandbox.postman_environment.json` — use this only for sandbox checks. Sandbox availability and sample data can differ from integration.

The environment files intentionally contain no credentials. Postman does not read the project's `.env` file, so the API key must also be entered in Postman.

## 1. Import the files

1. Open Postman.
2. Select **Import**.
3. Select **Files**.
4. Import all three JSON files listed above.
5. Confirm that the collection **NHS Community Navigation - API Tests** appears in the Collections area.
6. In the environment selector at the top right, select **NHS Integration**.

## 2. Enter the integration API key

1. Open **Environments** in Postman.
2. Select **NHS Integration**.
3. Find `content_api_key` and paste the NHS application API key into its value field.
4. Find `dohs_api_key` and paste the same NHS application API key into its value field.
5. Keep both variables marked as **secret**.
6. Save the environment.

Use the application **Key**, not the application **Secret**, in both variables. Do not put the secret in any request header. The requests use this header automatically:

```text
apikey: {{content_api_key}}
```

or:

```text
apikey: {{dohs_api_key}}
```

The correct integration values are already present for the base URLs:

```text
content_base = https://int.api.service.nhs.uk/nhs-website-content
dohs_base    = https://int.api.service.nhs.uk/service-search-api
```

Do not change the Website Content request to v1 and do not rename the header to `subscription-key`. The starter uses NHS Website Content API v2 and its documented `apikey` header.

## 3. Check the NHS portal products

The NHS application must have these integration APIs enabled:

- **NHS Website Content API (Integration Testing Environment)**
- **Service search - REST API (Integration Testing Environment)**

The second product supplies Directory of Healthcare Services v3. Other products with names such as Directory of Services Ingest, EPS DoS, or Spine Directory Service do not replace it for these requests.

## 4. Run the normal tests manually

Select the **NHS Integration** environment before sending requests. Wait at least 1.1 seconds between requests.

### Test 1 — Website Content symptoms

Open:

```text
01 - NHS Website Content API v2
  Symptoms A - smoke test
```

Select **Send**. A healthy response has:

- HTTP `200`.
- A JSON body.
- A non-empty `significantLink` array.
- A name and URL for every returned item.

Open the **Test Results** panel in the response area. All four checks should pass.

### Test 2 — Known DoHS organisation

Wait at least 1.1 seconds, then open:

```text
02 - Directory of Healthcare Services v3
  Known organisation Y02494
```

Select **Send**. Expected results:

- HTTP `200`.
- A non-empty `value` array.
- A record whose `ODSCode` is `Y02494`.
- The record is Shakespeare Medical Practice.

### Test 3 — EPS-enabled community pharmacies

Wait at least 1.1 seconds, then open:

```text
02 - Directory of Healthcare Services v3
  EPS-enabled community pharmacies
```

Select **Send**. Expected results:

- HTTP `200`.
- Five results in normal circumstances.
- Each result has `OrganisationTypeId` equal to `PHA`.
- Each result has `OrganisationSubType` equal to `Community`.
- Each result has `IsEpsEnabled` equal to the value `"true"` (the API currently
  serializes this field as a JSON string).
- The response includes `@odata.count`.

### Test 4 — Nearest pharmacies to Manchester

Wait at least 1.1 seconds, then open:

```text
02 - Directory of Healthcare Services v3
  Nearest EPS-enabled pharmacies to Manchester
```

Select **Send**. The environment defaults to central Manchester:

```text
longitude = -2.2426
latitude  = 53.4808
```

Expected results:

- HTTP `200`.
- All records are EPS-enabled community pharmacies.
- Every record has latitude and longitude values.
- Results are ordered from nearest to farthest from the supplied point.

You can change `longitude` and `latitude` in the selected environment to test another location. The request passes them to the API in `POINT(longitude latitude)` order; do not reverse them.

## 5. Run the core folders with Collection Runner

Use the Runner separately for the two core folders. This avoids running the optional legacy request and the currently problematic Website Content detail diagnostics.

1. In the collection, select the three-dot menu beside **01 - NHS Website Content API v2**.
2. Select **Run folder**.
3. Choose the **NHS Integration** environment.
4. Set **Iterations** to `1`.
5. Set **Delay** between requests to `1100` milliseconds.
6. Start the run and confirm every test passes.
7. Repeat these steps for **02 - Directory of Healthcare Services v3**.

The NHS integration limit used by this starter is one request per second. A delay of 1100 ms keeps the run slightly below that limit. Do not run many parallel Postman requests against integration.

Do not select **Run collection** for the complete collection during the normal smoke test. That would also run folders 03 and 04, which are intentionally separate.

## 6. Optional Website Content diagnostics

Folder **04 - Website Content detail diagnostics (run separately)** contains:

- Conditions index.
- Back pain detail with modules.
- Module manifest.

On 8 September 2026, the NHS integration gateway accepted the application API key, but these routes returned an HTML `401 Access Denied` page from the NHS staging content origin. The Symptoms A request still returned HTTP 200. This combination indicates an NHS upstream Website Content staging problem rather than an incorrect local header or base URL.

Run folder 04 only when checking whether NHS has restored those routes. Its tests correctly expect HTTP 200 and JSON, so they will fail while the upstream issue remains. Wait 1.1 seconds between each diagnostic request.

## 7. Optional legacy DoS request

Folder **03 - Legacy DoS REST (optional; run separately)** is unrelated to the application API key. It requires separately issued legacy Directory of Services Basic Auth credentials.

Only if those credentials have been provided, enter these environment values:

```text
dos_username = your separately issued legacy DoS username
dos_password = your separately issued legacy DoS password
postcode     = M1 1AE
```

The `dos_username` and `dos_password` fields are marked secret. Leave both blank and skip folder 03 if you do not have these separate credentials.

## 8. Common errors

### HTTP 401 with an API-key error in JSON

Check all of the following:

- **NHS Integration** is selected in Postman.
- The application Key is saved in both `content_api_key` and `dohs_api_key`.
- The correct API product is enabled for the same NHS application that issued the key.
- The header is named exactly `apikey`.

### HTTP 401 with an HTML Access Denied page

If the response refers to `nhswebsite-staging.nhs.uk`, this is the known Website Content staging-origin problem described above. Confirm that **Symptoms A - smoke test** still passes, then retry the detail diagnostics later or report the affected path and time to NHS Website Content support.

### HTTP 429

The requests are being sent too quickly. Stop the run, wait, and rerun with a delay of at least 1100 ms. Avoid parallel runs.

### HTTP 502, 503, or 504

These normally indicate a temporary gateway or upstream failure. Wait one second and retry once; if needed, wait two more seconds and retry again. If the same route repeatedly fails in Postman and a browser while other APIs pass, record the response body, time, environment, and path for NHS support.

### A test says the response is not JSON

Inspect the raw response body and its `Content-Type` header. NHS gateway and staging-origin error pages can be HTML even though the successful endpoint returns JSON.

## 9. Keep credentials safe

- Never commit a Postman environment containing API keys.
- Do not put API keys into the collection JSON or this README.
- Do not include the application Secret in these requests.
- Before exporting or sharing an environment, clear all secret values and inspect the exported JSON.
- If a real key or secret is exposed in chat, source control, screenshots, or email, rotate it in the NHS developer portal.

## Official NHS references

- [NHS Website Content API v2](https://digital.nhs.uk/developer/api-catalogue/nhs-website-content/v2)
- [Directory of Healthcare Services v3](https://digital.nhs.uk/developer/api-catalogue/directory-of-healthcare-services/version-3)
- [NHS API integration testing guidance](https://digital.nhs.uk/developer/guides-and-documentation/testing#integration-testing)
