# Phase 9: authenticated invoice operations

Business outcome: n8n can check local workflow status, request one bounded invoice poll and refresh a saved-data report month. Existing Python services retain extraction, financial controls, configurable review and accounting. The Phase 9 example only reads status. Intake/reporting workflows and optional schedules follow in Phase 10.

## Prepare and bind the credential

1. Run `python scripts/prepare_n8n_api.py` with the project virtual environment. It preserves any existing valid `INVOICE_N8N_API_TOKEN` in private `.env`, or generates one. It also creates owner-only `.runtime/n8n/api-token.txt` containing the complete `Bearer ...` header value. Neither file is committed or mounted into n8n.
2. Recreate the backend with `docker-compose --context colima-invoice-n8n up -d --build --wait`. Container restart alone does not reload values from a changed Compose env file.
3. In n8n, open Credentials, add **Header Auth**, and name it **Northmere Invoice API**. Header Name: `Authorization`. Header Value: the complete private token-file content, including `Bearer `.
   The credential display name is a label you choose. Its HTTP header **Name must be Authorization**, without spaces. On this machine the user-created display name is **Header Auth account**.
4. Import `deploy/n8n/phase-09-status.json`. Open **Read invoice workflow status**, select the saved Header Auth credential, save and execute.
5. Expect `status: ok`, `code: STATUS_READY` and operational data. Do not hardcode the token into node parameters, workflow JSON, chat or screenshots.

The local user grants this token access to the three endpoints below. Intake can create draft bills for valid cached invoices under the current review policy. The token grants no correction, approval, supplier-mapping, OAuth or extraction-resume endpoint. n8n never receives provider credentials.

## Interface

Every route requires `Authorization: Bearer <dedicated-token>`. Configuration must have 32-256 token characters. A missing/invalid server configuration disables the API with HTTP 503. Missing/incorrect caller credentials return HTTP 401. Browser Origin headers are rejected, including localhost, because this interface is for server requests. Existing browser forms continue to require their own origin and CSRF checks. No CORS relaxation was added.

| Method / path | Input | Result |
| --- | --- | --- |
| GET `/integrations/n8n/status` | No body | Saved-connection/configuration flags, delivery counts, duplicate-arrival count, current review policy, report months/statuses |
| POST `/integrations/n8n/intake` | JSON `{}` | One existing Gmail poll, then sanitized delivery summary and new/already-recorded/page counts |
| POST `/integrations/n8n/report` | JSON `{"month":"2026-08"}` | Refresh/read-back of one available report month; returns month, delivery count and verified status |

POST requests require `Content-Type: application/json`, exactly the documented keys and a body no larger than 1,024 bytes. Extra keys are rejected. Month uses YYYY-MM and must appear in available reporting months; arbitrary empty report creation is refused. Methods that are not listed have no handler.

Responses have `status`, a stable `code`, a server-generated `correlation_id` and `data`. The correlation ID identifies this response; it is not a durable job ID, audit record or idempotency key. No mailbox address, invoice number, source file, supplier, amount, raw provider error, tenant ID, spreadsheet ID or OAuth token is returned. Responses are not cached.

The status operation is local only. Saved tokens may have expired or been revoked remotely; `remote_access_checked: false` is explicit. A successful status response does not confirm live provider access. Configuration flags say whether credentials are locally present, without exposing their values.

Counts refer to **deliveries**. The original and duplicate may both be verified while sharing one Xero bill. Reporting preserves the existing unique-bill total behavior. A held invoice is a business exception; a successful intake request can contain held, unsupported or inspection-needed arrivals.

## Cost and accounting boundaries

The integration always passes `max_new_extractions=0` to the existing intake service. The service's default remains three for its existing browser caller. There is no request option to increase the integration budget. New uncached PDFs are recorded as inspection-needed and require explicit handling through the existing application. Cached genuine extractions still pass through normal validation and review policy. No second LLM implementation or prompt change was added.

This API can cause Gmail reads, Xero draft writes for eligible cached invoices and Google Sheets writes. Zero new extraction budget means zero new model calls; it does not make the API read-only. Supplier/payment/banking actions remain outside the endpoint scope.

The existing durable Gmail delivery identity, PDF hash and supplier/invoice business identity remain authoritative. Repeated polls skip known deliveries. Interrupted/uncertain operations retain their existing inspection/reconciliation rules. There is no automatic extraction resume, approval bypass or financial retry in the adapter.

Both status and reports use the same reporting-period helper: valid invoice date chooses the month; otherwise receipt time in UTC does. Report refresh delegates to the existing independent snapshot writer. It never invokes extraction or Xero posting.

## Error handling

| HTTP / code | Operator action |
| --- | --- |
| 503 `API_NOT_CONFIGURED` | Prepare server token and recreate backend |
| 401 `UNAUTHORIZED` | Inspect the n8n Header Auth credential; do not reconnect Google/Xero |
| 403 `SERVER_REQUEST_REQUIRED` | Use a server HTTP node without browser-origin headers |
| 400 `INVALID_REQUEST` | Fix content type, JSON shape, size or month |
| 400 `UNKNOWN_REPORT_MONTH` | Read status and choose an available month |
| 409 `CONNECTION_REQUIRED` | Inspect existing local Google/Xero configuration and saved connection |
| 409 `OPERATION_BUSY` | Inspect current operation, then retry manually if appropriate |
| 503 `OPERATION_FAILED` / `STATUS_UNAVAILABLE` | Inspect existing app state and connection; raw errors are deliberately excluded |

Set an intake/report HTTP node timeout to 300,000 ms and leave **Retry On Fail disabled**. An HTTP timeout does not cancel or roll back the Python action. Inspect the existing receipts, workflow and report checkpoint before rerunning. Report retries remain separate from intake. Recover uncertain spreadsheet creation through the existing app, rather than blindly creating another report.

To rotate the dedicated token, explicitly change its private `.env` value, rerun the preparation helper to update the copy file, recreate the backend and update the n8n credential. Previously issued tokens then fail. The helper never silently rotates a valid existing token.

## Limits and verification

Single local backend worker and one configured mailbox/tenant. No remote hosting, TLS termination, per-user RBAC, durable orchestration queue, API rate limiter, notification delivery or enabled schedule is introduced. A token holder can request the three operations; keep it private. n8n execution data contains operational metadata and is subject to local retention settings. Use synthetic data.

`python scripts/verify_n8n_api.py --output artifacts/phase-09-live-evidence.json` uses the existing five synthetic deliveries, polls twice, refreshes the two known reports and checks saved extraction/accounting counters. It makes live provider requests; it is separate from mocked unit tests and should not be used against a client's mailbox. The status workflow makes no provider requests.
