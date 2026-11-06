# Northmere Invoice Automation

**Supplier email → checked invoice data → duplicate-safe Xero draft → traceable monthly report.**

A Python/FastAPI and n8n portfolio project for accounts-payable workflow automation. It combines DeepSeek extraction with deterministic financial checks, configurable approval, durable duplicate protection and recoverable Google Sheets reporting.

[**Explore the illustrated demo**](https://asaduddin18.github.io/invoice-intake-automation-portfolio/) · [Case study](docs/case-study.md) · [Architecture](docs/architecture.md) · [Client pilot scope](docs/client-pilot.md)

> The public demo is an illustrated replay of fictional fixtures. It makes no API calls. The connected application was tested separately using Gmail, DeepSeek, Xero Demo Company (UK) and Google Sheets. The connected backend stays localhost-only.

[Open the five-scenario walkthrough](https://asaduddin18.github.io/invoice-intake-automation-portfolio/) to inspect the source PDFs, duplicate decision and held examples.

## The problem

Supplier invoices arrive in different PDF layouts. Copying fields into accounting software creates opportunities for missing references, incorrect totals and duplicate obligations. A useful automation must retain source evidence, make exceptions visible and recover safely when an external request fails.

## What this project does

- Reads PDF attachments from a selected Gmail label and records each delivery before processing.
- Extracts page-aware embedded text using `deepseek-flash`, with one bounded complete-document vision fallback when needed. Disagreement stays held.
- Preserves missing values and printed errors, then checks required fields, dates, quantities, tax and totals with Decimal arithmetic.
- Applies explicit supplier/account/tax mappings and optional revision-bound approval. Automatic is the default; invalid invoices remain held in either mode.
- Reconciles supplier/invoice identity and saves a creation intent before creating a Xero **DRAFT** bill. Retrieved fields and totals are compared with the source.
- Refreshes monthly Sheets reports independently, counts a verified bill once across duplicate arrivals and verifies the saved values by reading them back.
- Orchestrates bounded intake and sequential reporting through inspectable n8n workflows, with safe failure summaries and disabled optional schedules.

## Evidence and limits

| Check | Observed result |
| --- | --- |
| Extraction evaluation | 20 selected synthetic PDFs across familiar supplier layouts; 836/836 compared leaf fields matched after the documented prompt revision |
| Missing-field invention | Zero in that sample |
| Conservative exception | One valid document in the initial batch remained held because text and vision disagreed |
| Live intake acceptance | Five arrivals: valid + duplicate linked to one GBP 95.16 draft, two invalid invoices held, one text attachment unsupported |
| Repeated polling | Zero new receipts for the same five deliveries after fixing rotating Gmail download locators |
| Reporting recovery | Locally injected write failure recovered against live Sheets; accounting and extraction counts stayed unchanged |
| Test suite | 164 automated tests; see CI and release evidence |

These are small synthetic tests, not production accuracy or scan/OCR coverage. The full dataset has 50 searchable fictional PDFs, but only 20 were evaluated with the model. Phase 6 reused saved extractions and an existing draft, adding no model calls or bill creation intents. [Measured evidence](artifacts/public/metrics.json) · [Case study](docs/case-study.md).

## Try it without accounts

```sh
python3 -m http.server 8090 --directory site
```

Open `http://localhost:8090`. Select a scenario, inspect its PDF and step through the illustrated workflow. No credentials, paid API requests or installation are needed for this replay.

## Run the connected application

Use Python 3.12+ on macOS/Linux or WSL. The release was verified with Python 3.14.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pytest -q
cp .env.example .env
# Enter your own credentials locally, then:
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080 --no-access-log
```

Open `http://localhost:8080`. The real integration needs your own Google OAuth client, Gmail/Sheets APIs, synthetic-data mailbox, Xero developer app and UK Demo Company, plus a DeepSeek key for uncached extraction. [Complete setup and recovery](docs/operations.md).

Keep one worker. Do not deploy this unauthenticated connected app publicly. Source PDFs and extraction data are local plaintext; provider tokens are encrypted with a key on the same machine. [Privacy and retention](docs/privacy.md).

## Operate the n8n extension

A separate Docker learning setup connects n8n to the existing backend. [Local setup and recovery](docs/n8n-local.md). The [authenticated operation API](docs/n8n-api.md) exposes status, cache-only intake and monthly reporting. The [Phase 10 workflows](docs/n8n-workflows.md) add manual intake, sequential report refresh and safe failure summaries. Optional schedules are disabled. Python retains invoice extraction, validation, configurable review and Xero duplicate protection. Start with the [final operator handover](docs/n8n-handover.md), including node input/output inspection, recovery, schedule shutdown and token rotation. [Explore the public n8n walkthrough](https://asaduddin18.github.io/invoice-intake-automation-portfolio/n8n.html).

## Inspect the implementation

| Area | Source |
| --- | --- |
| Gmail delivery and bounded polling | `app/mail_intake.py`, `app/mail_store.py` |
| Text/vision extraction and exact prompt | `app/extraction.py`, `app/prompts/invoice-extraction.md` |
| Financial validation and Xero payloads | `app/bills.py` |
| Durable duplicate checks and reconciliation | `app/ledger.py`, `app/pipeline.py` |
| Corrections, policy and revision-bound approval | `app/review.py`, `app/workflow.py` |
| Independent reporting | `app/monthly_report.py` |
| OAuth and token refresh | `app/connection.py`, `app/google_connection.py` |
| Original synthetic dataset and generator | [dataset/](dataset/README.md) |

[Three-minute demo script](docs/demo-script.md) · [Handover and sign-off](docs/sign-off.md) · [Security reporting](SECURITY.md)

## A suitable client pilot

One mailbox, one accounting organisation, agreed suppliers and tax mappings, representative permitted samples and explicit acceptance tests. Authentication, deployment, monitoring, retention and provider terms need separate client agreement. Scans, credit notes, mixed tax, foreign currencies, payment and bill authorization are outside this demonstration. [Pilot deliverables and discovery questions](docs/client-pilot.md).

Built by [Asaduddin18](https://github.com/Asaduddin18). Public for portfolio inspection. An open-source license has not been selected; contact the author to agree reuse terms.
