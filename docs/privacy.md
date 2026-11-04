# Data flow, privacy and retention

The published dataset is original fictional content. It contains no real customers, banking details or account tokens. The public walkthrough loads only these local synthetic assets and performs no account authentication or external API calls. Account-linked development screenshots, mailbox addresses, live resource identifiers, credentials and runtime storage are excluded from the public export.

## Connected application

| Data | Destination | Local retention |
| --- | --- | --- |
| Selected-label message metadata and PDF attachment | Local FastAPI app via Gmail API | Delivery identities and source PDFs remain until explicit reset |
| PDF embedded text, or bounded rendered pages on fallback | DeepSeek API | Structured fields, response/usage evidence and source stay locally |
| Configured supplier identity and eligible invoice fields | Xero API, selected UK Demo Company | IDs, durable intents and verification results stay in SQLite |
| Delivery/invoice summary, exceptions, source hash and verified bill ID | Google Sheets API, app-created report | Report checkpoint remains locally; Sheet remains in the account |
| OAuth credentials and tokens | Google or Xero authorization/token endpoints | Credentials in ignored local .env; tokens encrypted locally |

Only invoice content enters the extraction prompt. Ground truth, scenario labels and filenames are excluded. Gmail bodies are not part of model input. Upstream credentials and raw error bodies are excluded from application error responses. Access logs should stay disabled because OAuth callbacks contain temporary codes.

Gmail `gmail.readonly` permits mailbox-wide reading. `Invoices-Demo` narrows application processing, not Google's permission. Google `drive.file` covers files created or used with this app. Xero's invoice/contact permissions are broader than draft creation; application code limits this demonstration to synthetic draft bills.

Source PDFs, extraction outputs, SQLite and reports contain plaintext invoice data. Runtime directories are owner-only. Fernet tokens and their encryption key are on the same machine, so a compromised OS account can decrypt them. Local audit records can be modified by that account. These controls do not establish production confidentiality, regulatory compliance or tamper-proof auditing.

There is no automatic expiry/deletion scheduler. Follow the reset runbook to archive or remove local demonstration data after inspecting pending writes. Deleting local tokens does not revoke provider access or remove cloud bills/reports. Revoke access in Google account connections and Xero Connected Apps separately.

Before a real client pilot, agree permitted data, provider contracts/retention, transfer restrictions, access roles, hosting region, backup/deletion policy and incident handling. Do not assume API use means zero provider retention or that sensitive client invoices may be sent without agreement. Live client records were not used in this project.
