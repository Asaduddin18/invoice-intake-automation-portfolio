# n8n operator handover

The extension is complete for local synthetic-data demonstration. It provides visible intake, sequential report refresh and failure handling while Python retains invoice validation, configurable review, extraction, duplicate protection and Xero writes.

## Choose what to run

| Goal | Entry point | Accounts or cost |
| --- | --- | --- |
| Understand the business decisions | Public illustrated invoice replay | None; no API calls |
| Understand node inputs, outputs and connections | Public n8n walkthrough | None; synthetic fixed examples |
| Run local automated tests | README setup | No provider calls |
| Run the connected workflow | Local n8n and Python services | Your own existing provider setup; uncached extraction requires a funded model key |

The n8n intake endpoint has zero new extraction allowance. Cached eligible invoices can still create draft bills through existing controls. This endpoint is not read-only. The browser's separate intake button retains its bounded allowance of three new extractions.

## Fresh local setup

1. Follow `n8n-local.md` to start the pinned Docker services. Create your own local owner account; skip optional marketing/license registration.
2. Configure your own Google/Xero connections using `operations.md`. Use synthetic invoices and Demo Company (UK).
3. Follow `n8n-api.md` to prepare a dedicated token and create Header Auth. Header Name is `Authorization`; the saved value includes `Bearer `.
4. Import the Phase 9 status example, bind that credential and execute. Saved readiness does not prove remote provider access.
5. Import the three JSON files under `deploy/n8n/phase-10`. Select the same credential on every HTTP node. Check that every Code node uses **JavaScript**, **Run Once for All Items**, and the code from the export.
6. In intake/report workflow settings, select the imported failure helper as **Error workflow**. In **Record safe failure**, select that same helper. Editor imports can assign different IDs, so verify both references.
7. Publish only the failure helper, which has no timer or provider operation. Keep intake/reporting unpublished and both schedule nodes disabled.
8. Save. Execute intake once, inspect its result, then execute reporting. Inspect the invoice app and reports separately.

For the existing development setup, `install_n8n_workflows.py` can reuse the bound Phase 9 credential reference. It never exports the credential value. It refuses replacement unless `--replace` is deliberately supplied. Export your editor changes privately before using that option. CLI publication changes require an n8n restart.

The final fresh-import acceptance used new workflow IDs in the existing local instance, then restarted both services. It did not provision a second owner account. Fresh Python installation and tests are separate release checks.

## Your ten-minute walkthrough

1. Open **Phase 10 - Invoice intake**. Execute once. Follow the highlighted connections.
2. Double-click **Check invoice inbox**. Left INPUT is a starting item; Parameters holds the POST request; right OUTPUT contains `body`, headers and status.
3. Press Escape and open **Read safe intake result**. Select JSON on both sides. Compare nested `body.data` input with the safe summary output.
4. Open **Intake request succeeded**. Its condition reads `$json.request_ok`. The same item is routed through True or False.
5. Open **Any deliveries need attention**. Its condition reads `$json.attention_required`. Green execution can still lead to attention.
6. For the saved demo expect five deliveries, two verified, two held, one unsupported, and one duplicate within the five. A connection labelled **1 item** carries one summary of five deliveries.
7. Open `http://localhost:8080/mail`. Use **Source and workflow** to inspect the original PDF, extracted fields, reasons and history. The deliberately missing invoice number must remain missing. Printed financial errors stay held.
8. Execute **Phase 10 - Monthly reporting**. Inspect each month and **Reporting complete**. Successful reports require write/read-back verification. The loop handles one month at a time.
9. Open **Executions**, choose a saved run and inspect each node. Inspecting saved data does not rerun it. **Execute step** can run work and required predecessors.
10. Confirm both schedule nodes are disabled and intake/reporting are unpublished. The helper remains published so manual failures can call it.

The final run reused five existing deliveries and cached extractions. It made zero new model calls or bill-creation intents. Earlier evaluation covers 20 of the 50 synthetic PDFs; this release adds no all-50 model evaluation.

## Recovery decisions

| Symptom | Inspect first | Recovery |
| --- | --- | --- |
| Python runner unavailable in a Code node | Language and source code | Restore JavaScript and the original node code; save, execute the parser against existing input, then inspect the full run |
| Invalid response or missing output fields | Previous node OUTPUT, next node INPUT and field types | Restore the expected response settings/code; do not mask invalid data as success |
| 401 UNAUTHORIZED | Header Auth and backend token configuration | Fix the dedicated integration credential; no Google/Xero reauthorization is implied |
| Backend unavailable | Service health and internal `invoice-api` address | Restore the service, inspect saved operation state, then retry manually |
| Provider connection expired | Invoice app's existing connection | Renew the same connection and inspect state before processing |
| Held or unsupported invoice | Source evidence and validation reason | Correct an extraction error with evidence, or leave the source defect held; review policy stays configurable |
| Failed report after an earlier month succeeded | Saved report checkpoint and cloud document | Refresh reporting after fixing access; earlier months may refresh again; never rerun invoice creation to repair a report |
| Uncertain accounting outcome | Ledger intent, saved IDs and Xero reference | Reconcile through existing app controls; never clear the intent to force another write |

An HTTP timeout does not cancel or undo an external operation. No HTTP node automatically retries financial work. Manual failures call the helper explicitly; automatic failures use Error Trigger. Failure summaries are local execution records, without email/Slack delivery. Original HTTP data and internal error traces can remain private in n8n history.

## Restart, backup and schedule shutdown

Use the explicit Docker context for this project. On Docker Desktop, replace the command prefix below with `docker compose`.

```sh
docker-compose --context colima-invoice-n8n stop
# Back up the stopped backend runtime, n8n volume and original encryption keys privately.
docker-compose --context colima-invoice-n8n up -d --wait
```

Keep `.runtime`, n8n's volume and the original encryption key together in restricted backups. They contain credentials and financial data. Never publish or upload them with a support request. Do not use volume-deletion options during restart. Do not run a second backend against the same runtime.

Schedules remain off at handover. To shut down a schedule that you later enable: unpublish intake/reporting, disable the Schedule Trigger nodes, save, then verify the state. A running execution may finish; inspect it before repeating work. Stopping the stack provides a complete local execution shutdown. The published failure helper has no schedule and can remain published.

To enable a schedule later, deliberately enable its node and publish that workflow after checking timezone, provider access, review policy and extraction budget. Intake example is every 15 minutes; reporting is 09:00 Asia/Kolkata. Local services must be running. No production scheduler or uptime commitment is provided.

## Dedicated API-token rotation

Rotation is an operator procedure; this release does not change your current token.

1. Unpublish intake/reporting and stop new manual runs. Inspect any running or uncertain operations.
2. In private `.env`, replace `INVOICE_N8N_API_TOKEN` with a new cryptographically random value of at least 32 characters, within the API's 256-character maximum. Use your password manager or a local secure generator. Keep it out of terminals, screenshots and chat.
3. Run `python scripts/prepare_n8n_api.py` to update the owner-only local copy file. The helper preserves a valid token rather than rotating it silently.
4. Recreate the backend with `docker-compose --context colima-invoice-n8n up -d --force-recreate --no-deps invoice-api`. A restart alone does not reload changed Compose environment values. Wait for health.
5. Update the existing Header Auth credential locally with the complete new `Bearer ...` value. Keep the header Name as `Authorization`.
6. Execute the harmless status workflow to verify the new credential. Automated API tests cover rejection of invalid tokens; never paste an old token into workflow JSON to test it.
7. Keep schedules off until you deliberately choose to resume them.

The n8n encryption key is a different key. Do not replace it as an API-token rotation step; replacing it can prevent saved credential decryption. Restore a lost-key situation from the restricted matching backup.

## Final scope and operator acknowledgement

Engineering acceptance and publication are completed in the final release evidence. Your independent learning exercise is the ten-minute walkthrough above. No claim is made that you independently performed it unless you confirm that yourself. It is not an unfinished implementation phase.

Future client work requires separate agreement on representative permitted samples, mappings, authentication, hosting, managed secrets, monitoring, retention and acceptance. Payments, bill authorization, complex tax and production deployment remain outside this portfolio scope.
