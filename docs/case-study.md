# Case study: supplier invoice intake

## Outcome

Built an inspectable supplier-invoice workflow using Gmail, DeepSeek, Xero and Google Sheets. Valid, configured documents can reach checked draft bills; duplicates reconcile to the existing obligation; exceptions retain source evidence and correction history. Monthly reporting retries separately from accounting.

This is an independently built portfolio demonstration. It was not delivered to a paying client, and no real client documents were used.

## Decisions that matter

Text-first extraction keeps embedded PDF content cheap to process. Page markers retain document context. One complete-document vision fallback handles extraction problems, missing/invalid output or unclear readings. Conflicting financial readings remain held. No claim is made that multimodal extraction eliminates mistakes.

Model output is untrusted. Nullable structured fields preserve omissions; deterministic validation checks arithmetic and dates. Supplier and accounting mappings are operator configuration. Automatic mode permits eligible documents without human approval; required mode binds approval to source, revision, policy and mapping.

Duplicate protection has separate delivery, document and business identities. Gmail attachment download tokens rotated during live testing, so durable delivery identity uses message and MIME part. PDF hashing saves extraction cost; supplier/reference identity handles different exports. Creation intents precede writes; uncertain outcomes reconcile without blind POST retries.

Reporting uses a durable checkpoint and a dedicated app-created Sheet. A report failure cannot create another bill. Numeric report values remain sortable/summable, and RAW writes avoid interpreting extracted text as spreadsheet formulas.

## Measured evidence

The original dataset contains 50 searchable fictional PDFs and ground truth, including missing data, inconsistent totals, unknown suppliers, multi-page examples and duplicates. Three main supplier layouts and an unknown fourth supplier appear. Splits keep duplicate families together. These are familiar-layout synthetic tests, not unseen-layout generalization or scan evaluation.

Two batches totaling 20 selected documents produced 836/836 compared leaf-field matches under the frozen final prompt. Zero expected missing fields were invented in that sample. One valid document in the initial batch remained held because text and vision disagreed. The initial batch informed prompt refinement; its aggregate is not an untouched hold-out accuracy claim. A historical extraction cost estimate of USD 0.02536268 includes tuning/smoke calls at rates recorded on 2 November 2026; it is not a provider billing statement or current price quote.

Live Gmail acceptance used five arrivals. Original and duplicate linked to one previously created GBP 95.16 Xero DRAFT; missing invoice number and wrong printed total stayed held; a text attachment was skipped. Repeat polling recorded zero new deliveries. Both monthly reports matched API read-back. A deliberate local report-write failure recovered against live Sheets. Extraction and accounting counts did not change. This check reused saved model results and an existing draft; draft creation and model calls were verified in earlier development phases.

See [machine-readable public metrics](../artifacts/public/metrics.json), [architecture](architecture.md), [privacy](privacy.md) and [release sign-off](sign-off.md).

## Limits

One local worker, on-demand polling, synthetic UK/GBP documents, configured fictional suppliers and simple uniform 0%/20% tax assumptions. No background scheduler, production authentication, managed key store, immutable audit, retention scheduler or malware scanning. Local locks do not coordinate unrelated external accounting writers. Real provider-permission revocation and crash-during-Sheet-creation were covered by mocked boundary tests, not destructive live drills.

No measured commercial time saving, production error reduction, client adoption, compliance certification or universal-layout accuracy is claimed. Those require a separately scoped pilot with representative client data and operational controls.
