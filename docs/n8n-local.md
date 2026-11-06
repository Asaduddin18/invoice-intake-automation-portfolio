# Local n8n learning environment

This guide covers the Phase 8 local infrastructure. For the completed intake/reporting extension, continue with [the final handover](n8n-handover.md). Phase 8 adds local orchestration infrastructure. The only example workflow calls `/health`. It cannot extract invoices, create bills or refresh reports. The existing connected app still has its previous manual capabilities.

## Services and storage

| Component | Browser address / internal address | Persistence |
| --- | --- | --- |
| Invoice application | http://localhost:8080 / http://invoice-api:8080 | Existing `.runtime` bind-mounted at `/app/.runtime` |
| n8n 2.46.6 | http://localhost:5678 | Named `n8n-data` volume |
| n8n encryption key | Not an HTTP resource | `.runtime/n8n/encryption.key`, mounted as a secret |

Both host ports bind to `127.0.0.1`. The services share a project Docker network. The Python image runs one worker as UID 1000. Container listening on `0.0.0.0` is limited by the loopback host port mapping. The backend accepts the exact Docker alias only when `INVOICE_DOCKER_NETWORK=true`; arbitrary Host headers are still rejected.

n8n receives no Google, Xero or DeepSeek environment variables and cannot mount the backend runtime. It sees only its volume, encryption key and read-only learning workflow exports. Its HTTP node can contact the trusted local backend; this topology is for a local learning machine, with no production authentication claim.

## Requirements

Docker Engine with Compose. Docker Desktop is one option. This machine uses an isolated Colima profile because Desktop was absent. The original Docker context was restored; use the explicit context in every command below. No n8n Cloud subscription, activation key or new provider account is required. Existing Google/Xero sessions are reused by the backend, but this phase does not test their remote validity.

## First startup

Run from the repository root. Keep the existing `.env` private. On a fresh clone, copy `.env.example` to `.env`; blank values are sufficient for the health example. Real provider actions require your existing credentials.

```sh
# This machine's isolated engine. For Docker Desktop, omit this line.
colima start --profile invoice-n8n --cpu 2 --memory 4 --disk 20 --vm-type vz --activate=false
python3 scripts/prepare_n8n_local.py
docker-compose --context colima-invoice-n8n config --quiet
docker-compose --context colima-invoice-n8n up -d --build --wait
```

With Docker Desktop use `docker compose` in place of `docker-compose --context colima-invoice-n8n`. The standalone Compose command is used here because the Docker CLI plugin was absent. The installed standalone version is 5.7.0.

Before the first migration, stop the old host-based invoice server and back up `.runtime`. Only one backend may access this financial state. For the current migration, a private backup is retained at `.runtime/backups/phase-08-before-containers.tar.gz`. Never commit or publish that archive.

Open n8n and create your local owner account yourself. Keep your password out of source and chat. Skip optional assistant/marketing/activation setup. Create a workflow with Manual Trigger to learn the canvas.

## Import and run the reference example

Import `deploy/n8n/phase-08-connectivity.json` through n8n's workflow file-import menu. Its name is **Phase 8 - Local connectivity only**. On this machine it has already been imported and verified.

Open it and click **Execute workflow**. Inspect **Check invoice API health**. It should return:

```json
{"status":"ok","service":"invoice-intake"}
```

`invoice-api` resolves inside the Docker network. In a browser on your Mac use `localhost:8080`. A health result proves the process responds; it does not prove OAuth tokens are remotely valid or that an invoice was processed.

## Restart and stop

```sh
docker-compose --context colima-invoice-n8n restart invoice-api n8n
docker-compose --context colima-invoice-n8n ps
docker-compose --context colima-invoice-n8n stop
# Start the same persisted services again:
docker-compose --context colima-invoice-n8n up -d --wait
```

The stack does not auto-start after Docker/VM restart. Close the app or stop the stack before switching back to the host Python server. Keep the n8n volume and encryption key together; replacing the key can make saved credentials unreadable. Do not remove volumes when stopping or updating. Back up the stopped n8n volume and key securely before upgrades. Provider tokens and invoice data remain private backend files.

n8n keeps execution data for learning, with pruning configured to 168 hours or 1,000 executions. Pruning is asynchronous, not an exact deletion deadline. Never place raw invoice documents or provider responses into learning workflow data. Diagnostics, template downloads, version notifications, personalization and community-package installation are disabled. No schedules exist in the Phase 8 workflow.

## Troubleshooting and rollback

- Engine unavailable: start the selected runtime and verify `docker --context colima-invoice-n8n info`. Do not repair unrelated Colima profiles.
- Port 8080 busy: stop the old project server after confirming its identity. Avoid running two backends against one runtime.
- n8n cannot reach health: check both service health states; use the exact internal URL above. Do not publish ports on the LAN or disable TrustedHost/CSRF protections.
- Bind-mount permission error: inspect the mounted directory's access for UID 1000. Do not make runtime files world-readable or run the application as root to bypass the error.
- Credential decryption error: preserve the original keys and restore the private backup with services stopped. Health checks never reconnect accounts automatically.
- To return to the original runtime: stop both Compose services, preserve all volumes/keys, then launch the documented host Python command in README. Callback URLs remain unchanged.

## References

[Official n8n stable release](https://github.com/n8n-io/n8n/releases/tag/n8n%402.46.6), [n8n Docker installation](https://docs.n8n.io/hosting/installation/docker/), [execution retention](https://docs.n8n.io/hosting/configuration/environment-variables/executions/), [Compose networking](https://docs.docker.com/compose/how-tos/networking/).
