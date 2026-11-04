# Phase 2 reference invoice

`invoice_001.pdf` and its JSON ground truth are copied from the owner's synthetic dataset in [ai-freelance-playbook](https://github.com/Asaduddin18/ai-freelance-playbook/tree/main/projects/01-invoice-automation/dataset). They are fictional test data, not an invoice received from a client or an accounting-software export.

Known input bypasses model extraction in this phase. Invoice AP-2026-1001 is from Alder Packing Supplies Ltd: two rolls of pallet wrap at GBP 12.35 and three batches of storage labels at GBP 18.20. Net GBP 79.30, exclusive test VAT 20% GBP 15.86, total GBP 95.16. Issue date 2026-08-03; due date 2026-08-17. The dates are deliberately historical test values.

Demo mapping: both lines use account 325 Direct Expenses and tax INPUT2 20% VAT on Expenses. These were read from the UK Demo Company and are revalidated at runtime. This is an explicit synthetic-business assumption, not a universal accounting rule or tax recommendation.

Supplier seed identity: ContactNumber PORTFOLIO-ALDER-001. The example email uses the reserved .example domain. No payment or banking data is provided. The source PDF names the fictional buyer Northmere Distribution Ltd; our API target remains the generic Xero Demo Company (UK) container. We do not rename the demo organization or imply that it is a live business.

Phase 3 adds invoice_004 (another valid Alder invoice) and invoice_001_reexport (the dataset's regenerated duplicate of invoice_001, with different PDF bytes). Renamed-copy scenarios reuse the exact original bytes with a different receipt filename. Conflict, arithmetic-error and unknown-supplier scenarios deliberately mutate confirmed JSON while leaving the PDF unchanged; they test downstream rules, not PDF extraction. These samples are selected from the same original dataset and introduce no real financial or personal data.
