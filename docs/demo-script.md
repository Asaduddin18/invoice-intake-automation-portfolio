# Three-minute client walkthrough

1. Open the public illustrated replay. State that the documents are fictional and the replay makes no API calls. The connected workflow was tested separately against Gmail, DeepSeek, Xero Demo Company and Sheets.
2. Select the valid invoice and open its PDF. Show supplier identity, invoice number, line items and GBP 95.16 total. Step through intake, extraction, validation, duplicate reconciliation and verified draft outcome.
3. Select its duplicate. Explain that another email delivery is recorded while the obligation stays linked to one bill. The reporting count stays GBP 95.16.
4. Select missing invoice number, then incorrect total. Show that missing fields are preserved and printed errors are not silently repaired. These sources remain held under automatic and approval-required policies.
5. Explain the three identities: Gmail delivery, PDF hash and supplier/invoice number. Mention that rotating download locators are deliberately excluded from delivery identity.
6. Show the architecture and measured evidence. Twenty synthetic documents were evaluated; no production or scan accuracy is claimed. Explain text-first extraction, one bounded vision fallback, and the conservative disagreement hold.
7. Close with a bounded pilot: one mailbox, one organisation, agreed supplier/account/tax mappings, sample-based acceptance and an operator recovery guide. Client data permissions and production controls must be agreed first.

For a live local demonstration, follow operations.md using your own demo credentials. Never show .env, token files or browser callback codes in a recording. A screen recording is optional; the public replay and source are sufficient for initial sharing.
