# Small Phase 4 extraction subset

Ten original synthetic PDFs and their reference labels copied from the owner's [50-invoice dataset](../../dataset/README.md). Six tuning, four held out. The subset preserves original split assignments and contains no duplicate families.

Three fictional supplier layouts, explicit 0%/20% test tax, missing invoice number/due date, wrong printed total/subtotal, and two 28-line multi-page examples. These are searchable PDFs, with no real customer, banking or identity data. They are test fixtures and not tax-compliant documents.

The extractor reads only PDF bytes. The evaluation script reads sibling JSON only after extraction to compare outputs. Neither labels nor filenames enter model requests. Evaluation was limited by the owner to these ten invoices.

Generation sources, source research and the full dataset are included in dataset/. This app does not regenerate or alter the source PDFs. Read artifacts/phase-04-report.md for measured scope and limitations.

## Additional requested batch

`additional/` contains ten more PDFs and labels, retaining six tuning and four evaluation assignments. It adds missing supplier/date, incorrect tax/total, unknown suppliers, explicit 0% tax and a re-laid-out duplicate of tuning invoice 002. The original and its duplicate remain in the tuning split. This brings copied extraction fixtures to 20 distinct files. Prompt v3 stayed frozen; labels were loaded only after extraction. See artifacts/phase-04-additional-report.md.
