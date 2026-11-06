# Three-minute client walkthrough

1. Open the public illustrated replay. State that the documents are fictional and the replay makes no API calls. The connected workflow was tested separately against Gmail, DeepSeek, Xero Demo Company and Sheets.
2. Select the valid invoice and open its PDF. Show supplier identity, invoice number, line items and GBP 95.16 total. Step through intake, extraction, validation, duplicate reconciliation and verified draft outcome.
3. Select its duplicate. Explain that another email delivery is recorded while the obligation stays linked to one bill. The reporting count stays GBP 95.16.
4. Select missing invoice number, then incorrect total. Show that missing fields are preserved and printed errors are not silently repaired. These sources remain held under automatic and approval-required policies.
5. Explain the three identities: Gmail delivery, PDF hash and supplier/invoice number. Mention that rotating download locators are deliberately excluded from delivery identity.
6. Show the architecture and measured evidence. Twenty synthetic documents were evaluated; no production or scan accuracy is claimed. Explain text-first extraction, one bounded vision fallback, and the conservative disagreement hold.
7. Close with a bounded pilot: one mailbox, one organisation, agreed supplier/account/tax mappings, sample-based acceptance and an operator recovery guide. Client data permissions and production controls must be agreed first.

For a live local demonstration, follow operations.md using your own demo credentials. Never show .env, token files or browser callback codes in a recording. A screen recording is optional; the public replay and source are sufficient for initial sharing.

## Optional n8n demonstration

Open the public n8n walkthrough. Select Check invoice inbox, then Read safe intake result. Explain that one item carries a summary of five deliveries. Show request_ok=true and attention_required=true. Select the IF nodes and explain how the same summary follows the True branch. Explain that held invoices stay visible with source evidence, review remains configurable, and report recovery never repeats invoice creation.

For a private local demo, execute intake and reporting using the operator guide. Keep credential screens and raw execution logs private. Both schedules are disabled at handover.
