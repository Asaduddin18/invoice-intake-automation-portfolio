# Gmail demonstration attachments

Use `invoice_001_duplicate.pdf` as a renamed byte-identical copy of `../extraction/tuning/invoice_001.pdf`. `invoice_013.pdf` lacks the invoice number. `invoice_015.pdf` preserves an incorrect printed total. `unsupported.txt` exercises the unsupported-file path.

The valid first email uses `../extraction/tuning/invoice_001.pdf`. The second email combines the other four attachments. All documents are fictional and contain no banking details. Apply `Invoices-Demo` in the receiving mailbox. These files reuse the existing portfolio dataset; no new model calls are needed when its genuine extraction cache is present.

Only attach the four fixture files to the exception email. This README is documentation, not a fixture.
