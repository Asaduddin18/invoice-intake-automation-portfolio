# A bounded invoice-automation pilot

A suitable starting point is one supplier-invoice mailbox, one accounting organisation, a small agreed set of suppliers and text-based GBP invoices. The goal is reliable draft preparation with traceable exceptions and reporting.

## Deliverables

- Agreed intake filter and OAuth connections owned by the client.
- Field extraction tested against a representative, permitted sample.
- Explicit supplier, expense-account and tax mappings agreed with the accounting owner.
- Required validation, duplicate reconciliation and a configurable approval policy.
- Draft supplier bills, exception visibility and a monthly report.
- Acceptance evidence, operator walkthrough and recovery/handover documentation.

## Discovery questions

What accounting software and organisation are used? Which mailbox receives invoices? What languages, currencies and document types occur? How are suppliers identified? Which expense/tax mappings are approved? Who may correct or approve? What data may leave the environment? What volume, delay and retention are acceptable?

## Acceptance to agree before implementation

Measure field correctness and missing/invented values on held-out client examples. Test identical and reformatted duplicates, wrong totals, unknown suppliers, interrupted writes and reporting retries. Define which documents must be held and who resolves them. Reconcile the resulting draft fields, amounts and status with the source.

This portfolio demonstrates a local synthetic workflow. A client pilot needs separately scoped authentication, deployment, monitoring, secret management, retention, access control and provider terms. Scans, handwriting, mixed tax, credit notes, foreign currency, payments and bill authorization expand the scope. No time-saving, error-reduction, compliance or production-accuracy claim has been measured here.

## n8n orchestration option

The portfolio includes visible intake/reporting workflows, safe local failure summaries and disabled schedule examples. A client pilot can agree who operates them, which permitted mailbox is in scope, which exceptions need action and how report recovery is accepted. The demonstration uses a zero-new-extraction n8n budget; increasing uncached processing, enabling schedules, notifications or production hosting requires an explicit client scope and cost policy.
