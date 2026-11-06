# Invoice operations in n8n

Business outcome: an operator can check the invoice inbox, see which saved deliveries need attention, and refresh monthly reports through visible workflows. Python retains financial rules and provider access. Review remains configurable in the invoice app.

## What each workflow does

| Workflow | Steps | Result |
| --- | --- | --- |
| Invoice intake | Manual trigger → authenticated intake request → safe result → success/exception branch | Counts, duplicate count and a clear attention outcome |
| Monthly reporting | Manual trigger → available months → one month at a time → report refresh/read-back | Verified monthly report summary, or an explicit failure |
| Safe failure handling | Automatic Error Trigger or a manual sub-workflow call → allowlisted summary | Code, correlation reference when available, inspection action; no outgoing notification |

A successful request can still contain held or unsupported invoices. Green means the operation ran successfully. Inspect `attention_required` and the counts to understand the business result. A red execution means the operation failed and requires inspection before a retry.

The n8n intake endpoint has zero allowance for new uncached extraction. New documents needing a model call remain deferred until the existing app's bounded intake is used. Existing cached documents can still proceed through the normal configurable review and draft-bill controls.

## Install locally

Prerequisites: complete the local Docker setup, Phase 9 API configuration and saved working Header Auth credential. No additional account, scope or model credit is required.

From the repository root:

```sh
python scripts/build_n8n_workflows.py
python scripts/install_n8n_workflows.py --context colima-invoice-n8n
docker-compose --context colima-invoice-n8n restart n8n
```

Use your own Docker context if different. The installer reuses the credential reference from `northmerePhase09`; it does not export its value. It refuses to replace existing Phase 10 workflows unless `--replace` is deliberately supplied. Back up your editor changes before replacing them.

The three JSON source exports contain no credential bindings and import inactive. The installer publishes only the failure helper because the pinned n8n version requires published called workflows. This helper has no schedule or provider operation. Intake and reporting remain unpublished, with their schedule nodes explicitly disabled. Restart n8n after CLI publication changes.

For a manual import, import the three JSON files in `deploy/n8n/phase-10`, bind your existing Header Auth credential to every HTTP Request node, publish the failure helper, and select it as the error workflow and called failure workflow. Imported IDs can differ when importing through the editor, so check these references. Keep intake/report schedules disabled.

## Run and inspect

1. Open `http://localhost:5678` and select **Phase 10 - Invoice intake**.
2. Click **Execute workflow**. Inspect **Read safe intake result** for counts and `attention_required`.
3. Open the invoice app to inspect held/unsupported deliveries. Its configured review policy decides any approval requirement.
4. Open **Phase 10 - Monthly reporting**, execute, and inspect **Reporting complete**. Each available month is refreshed sequentially and read back for verification.
5. Inspect **Executions** for failures. Manual failures explicitly call the helper before stopping. Automatic failures use Error Trigger. The helper produces a local saved summary, without sending email or chat messages.

## Failure and recovery

- `UNAUTHORIZED`: check the existing Header Auth binding and backend token configuration locally. Keep credentials out of chat and screenshots.
- `CONNECTION_REQUIRED`: renew the provider connection in the invoice app.
- `TRANSPORT_FAILED`: check service health and connectivity.
- Report failure: inspect the saved report status. Manually rerun reporting after fixing the cause. It may safely refresh an already successful earlier month again. It never invokes intake or invoice creation.
- Uncertain intake result: inspect the app's saved deliveries and accounting ledger before manually retrying. No HTTP node automatically retries a financial operation.

Safe summary nodes discard free-form provider errors and unknown fields. Original HTTP-node inputs and internal n8n error traces can remain in the private local execution history. The Compose setup retains up to seven days/1,000 executions. Do not publish raw execution exports or use real client documents in this learning setup.

## Optional schedules

The disabled examples are intake every 15 minutes and reporting daily at 09:00 Asia/Kolkata. Neither runs in this phase. Enabling a schedule also requires publishing its workflow; leave both controls off until the Phase 11 operator walkthrough. The failure helper can stay published without enabling these schedules.

## Verification and editable sources

`deploy/n8n/phase-10/logic` contains the editable JavaScript. Regenerate the JSON after changing it. Unit tests run those same functions and check export controls.

```sh
python scripts/verify_n8n_workflows.py --context colima-invoice-n8n
python scripts/verify_n8n_error_trigger.py --context colima-invoice-n8n
```

The first verifier polls the existing demo inbox and refreshes saved monthly Sheets reports, then runs isolated synthetic fault cases. It requires working existing provider grants. The second temporarily publishes a local synthetic webhook probe, checks automatic failure execution, unpublishes the probe and restarts n8n. Neither enables invoice schedules. Private CLI logs stay under `.runtime/n8n`.

Built against n8n 2.46.6. References: [Error Trigger](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.errortrigger/), [error handling](https://docs.n8n.io/flow-logic/error-handling/) and [Schedule Trigger](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger/). Manual executions need the explicit helper call because Error Trigger handles automatic failures.
