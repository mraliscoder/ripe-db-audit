# Operations

[Back to README](../README.md)

## Reverse proxy setup

Same-origin browser requests are recognised using `Sec-Fetch-Site`, so HTTPS
termination or an internal upstream hostname does not cause a false origin
rejection. Requests from other sites remain blocked.

For clients without Fetch Metadata headers, configure the exact public origin:

```dotenv
AUDIT_ALLOWED_ORIGINS=https://audit.example.com
```

Separate multiple origins with commas. Origins include a scheme and optional
port, without a path or wildcard. Forwarded Host headers alone are not trusted
as a source of allowed origins.

Preserve `Host`, `Origin` and `Sec-Fetch-Site` at the proxy. If TLS terminates at
the proxy, set `X-Forwarded-Proto` and configure `FORWARDED_ALLOW_IPS` with its
actual address or network so Uvicorn recognises HTTPS and sets secure cookies.
Do not broadly trust forwarded headers on an unprotected, publicly reachable
backend. Compose forwards this optional setting to Uvicorn.

Keep SSE buffering disabled and allow a read timeout longer than the 10-second
heartbeat. After changing `.env`, recreate the service with
`docker compose up -d --no-build --wait`.

## Instance authentication

Set `AUDIT_ACCESS_TOKEN` in `.env` for Compose, or export it before starting the
application locally. The token protects all analysis endpoints. `/api/health`
remains accessible for healthchecks.

API clients send `Authorization: Bearer <token>`. The UI exchanges the token for
a signed, HttpOnly, SameSite=Strict cookie valid for eight hours. Tokens are not
stored in browser local storage or URLs. Use HTTPS when exposing the service
outside your machine.

Authentication is shared across the instance, not scoped to individual users or
organisations. Changing the access token invalidates existing browser sessions.

## Private LIR Portal inventory

Public analysis needs no RIPE credentials. To refine discovery using the
[My Resources API](https://www.ripe.net/publications/documentation/developer-documentation/lir-portal-api-keys/),
configure a JSON mapping of regIDs to server-side keys:

```dotenv
RIPE_API_KEYS_JSON={"ru.ecode":"YOUR_MY_RESOURCES_KEY"}
AUDIT_ACCESS_TOKEN=YOUR_LONG_RANDOM_INSTANCE_ACCESS_TOKEN
```

Instance authentication is required whenever Portal keys are configured. The
application checks that the key belongs to the requested regID. Keys remain in
the server environment and are not stored in the analysis database.

After editing `.env`, apply the configuration with `docker compose up -d --no-build --wait`.
For local runs, export the variables and restart the server; `start.sh` does not
load `.env` automatically.

## Completion webhooks

The UI uses SSE and does not require a webhook. To notify another service when an
analysis finishes, configure both variables:

```dotenv
AUDIT_WEBHOOK_URL=https://receiver.example/audit-completed
AUDIT_WEBHOOK_SECRET=YOUR_LONG_RANDOM_SIGNING_SECRET
```

Callbacks are disabled by default. The URL must use HTTPS and is configured by the
instance operator, not supplied by analysis requests.

The JSON payload contains `event: "analysis.finished"`, `event_id`, `job_id`,
`regid`, `status`, `completed_at` and `summary`. The delivery headers are:

| Header            | Value                                                         |
| ----------------- | ------------------------------------------------------------- |
| `X-ARC-Event-ID`  | Stable job ID; use it to deduplicate deliveries.              |
| `X-ARC-Signature` | `sha256=<hex HMAC-SHA256>` over the exact request body bytes. |

There are up to three attempts. Verify the signature before processing a callback
and handle duplicate event IDs. Delivery failure does not invalidate a result or
restart the analysis.

## Backups and migration

The database contains jobs, snapshots and replayable progress events. Preserve it
to keep existing job IDs and result links. When changing domains, the job IDs
remain the same but links must use the new hostname.

Use SQLite's backup API for a consistent backup of a local installation, including
any committed data in its WAL:

```sh
python3 - <<'PY'
import sqlite3

with sqlite3.connect('data/audit.sqlite3') as source:
    with sqlite3.connect('/tmp/ripe-db-audit.sqlite3') as target:
        source.backup(target)
PY
```

For a Docker installation, create and copy a backup from the running container:

```sh
docker compose exec -T analyzer python -c 'import sqlite3; source = sqlite3.connect("/app/data/audit.sqlite3"); target = sqlite3.connect("/tmp/ripe-db-audit.sqlite3"); source.backup(target); target.close(); source.close()'
docker compose cp analyzer:/tmp/ripe-db-audit.sqlite3 ./ripe-db-audit-backup.sqlite3
```

### Import into a new Docker volume

Before starting the application in the destination volume, import a backup using
the configured application image. For example, after setting `AUDIT_IMAGE` to a
published version:

```sh
docker compose pull
docker compose run --rm --no-deps -T analyzer python -c 'import sys; from pathlib import Path; path = Path("/app/data/audit.sqlite3"); path.exists() and sys.exit("Database already exists; import cancelled"); path.write_bytes(sys.stdin.buffer.read())' < /tmp/ripe-db-audit.sqlite3
docker compose up -d --no-build --wait
```

Replace the input path with your backup's location. Import refuses to overwrite an
existing database. Completed jobs remain available after migration. Unfinished
jobs are marked interrupted on startup and must be run again.

The volume belongs to the Compose project. Retain the same project name when
redeploying, or explicitly attach the existing volume. Do not use
`docker compose down -v` when you need to retain results.
