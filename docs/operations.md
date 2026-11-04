# Setup and operations

## Reproduce locally

Use Python 3.12 or newer; the release was tested with Python 3.14. The app targets macOS/Linux because its process locks use `fcntl`. Windows users can use WSL.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pytest -q
python -m http.server 8090 --directory site
```

Visit `http://localhost:8090` for the illustrated replay. This needs no credentials, balance or accounts. It is separate from the connected app. Stop the static server before starting another server on that port.

For the real integration, copy `.env.example` to `.env` and enter your own values locally. Use a dedicated synthetic-data Gmail account, enable Gmail/Sheets APIs, add it as an OAuth test user, and create a Google Web OAuth client. Register exact callbacks:

```text
http://localhost:8080/oauth/google/callback
http://localhost:8080/oauth/xero/callback
```

Create a Xero developer app, activate your own Demo Company (UK), and supply its client ID/secret. A DeepSeek key and funded account are needed only for uncached extraction. Existing evaluation summaries and the replay do not make paid requests.

```sh
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080 --no-access-log
```

Use `http://localhost:8080` consistently. Connect Google and Xero from the local home page. Create Gmail label `Invoices-Demo` and send synthetic fixtures to that account. Poll explicitly from `/mail`. Configure supplier/account/tax mappings against the destination company's actual active options. Account 325 / INPUT2 is the original UK demo example, not a universal accounting policy.

Run one worker and one process using the token directory. Do not expose this unauthenticated local app publicly. The hosted portfolio walkthrough contains no connected backend.

## Recovery

| Symptom | Action |
| --- | --- |
| Callback mismatch or expired browser state | Check the exact callback and start again from local home in the same browser |
| Google Testing connection expired | Reconnect the same account; Gmail test refresh tokens can expire after seven days |
| Xero demo reset, revoked access or changed tenant | Reconnect and inspect existing bill references before processing again |
| Stale form protection | Reload its local page before submitting |
| Interrupted extraction | Inspect saved processing evidence; do not delete it to force another paid call |
| Uncertain Xero write | Inspect the ledger and reconcile by saved ID or supplier/reference; never erase an intent to force POST |
| Invalid totals or missing data | Inspect PDF and correct with a reason; revalidate; obtain approval if policy requires it |
| Sheets pending | Restore access and refresh reporting only; it never creates another bill |
| Lost Sheet creation response | Find the app-created file in Drive and recover its ID using the report form |
| Gmail page token invalid | Stop the server, preserve `.runtime/mail/poll-cursor.json`, remove only that cursor, restart and poll; durable delivery receipts prevent replay |

## Backups, reset and teardown

Stop the server before copying `.runtime`. Back up the whole directory as one restricted unit, including encryption keys and databases. Protect that backup as invoice data and credentials. Never commit it or send it with a support request.

A safe demo reset starts with inspection of held/uncertain operations and cloud drafts. Revoke Google/Xero access if retiring the connection, and remove synthetic cloud reports/bills through their interfaces if desired. Archive the entire runtime directory privately before initializing a new one. Do not partially clear ledger attempts while retaining cloud bills. A clean local runtime does not mean cloud records were deleted.

There is no automated provider-deletion tool, background scheduler, production deployment or disaster-recovery service. Application history and the report document these boundaries.
