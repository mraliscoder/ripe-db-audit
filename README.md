# RIPE DB Audit

[![Checks](https://github.com/mraliscoder/ripe-db-audit/actions/workflows/ci.yml/badge.svg)](https://github.com/mraliscoder/ripe-db-audit/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/mraliscoder/ripe-db-audit)](https://github.com/mraliscoder/ripe-db-audit/releases/latest)

Audit a LIR's IPv4, IPv6 and ASN resources using its RIPE regID, such as
`ru.ecode`. RIPE DB Audit combines registration data and BGP observations in a
searchable dashboard with a network tree, findings and CSV export.

The application is read-only. It does not modify RIPE objects, send emails or
create tickets.

## Features

- Discover allocations, child networks, organisations, and own or sponsored ASNs.
- Compare registrations with exact, more-specific and covering BGP announcements.
- Inspect findings with error codes, explanations, source evidence and RIPE Database links.
- Follow analysis progress live; reload the page to resume viewing a running job.
- Reuse completed results for one hour and keep previous snapshots at permanent job URLs.

## Quick start

Requires Docker with the Compose plugin. Published images support **Linux amd64
and arm64** and can be pulled without signing in.

```sh
git clone https://github.com/mraliscoder/ripe-db-audit.git
cd ripe-db-audit

export AUDIT_IMAGE=ghcr.io/mraliscoder/ripe-db-audit:0.1.1
docker compose pull
docker compose up -d --no-build --wait
```

Open **http://127.0.0.1:8090** and enter your regID. Public analysis works without
RIPE credentials. Interactive API documentation is available at
**http://127.0.0.1:8090/docs**.

For a different port, set `AUDIT_PORT` before starting Compose. To save your
settings, copy [.env.example](.env.example) to `.env` and set `AUDIT_IMAGE` there.
Use a version tag for a fixed release, or `latest` to follow stable releases.

To build the image from source instead:

```sh
AUDIT_IMAGE=ripe-db-audit:local docker compose up -d --build --wait
```

## Audit checks

| Resource | Checks                                                                                                                                                                                                                                |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| IPv6     | ASSIGNED prefix lengths of /48–/64; valid AGGREGATED-BY-LIR assignment sizes; no more than five nested ALLOCATED-BY-LIR objects; assignment registration for each announcement, including more-specifics.                             |
| IPv4     | PA/PI status consistency, SUB-ALLOCATED PA parentage, assignment nesting and aggregate assignment sizes, based on [RIPE-826](https://www.ripe.net/publications/docs/ripe-826/). ALLOCATED-ASSIGNED PA is recognised as an assignment. |
| ASN      | Current origin announcements and inactivity over three calendar months for own and sponsored ASNs. Newly registered ASNs are excluded from the inactivity violation until that period has elapsed.                                    |

The announcement check requires at least one relevant ASSIGNED or valid
AGGREGATED-BY-LIR registration within the allocation for each announcement. It
checks more-specific announcements separately; it does **not** require assignments
to cover every address in an announced aggregate.

IPv6 size and nesting thresholds are application audit rules. Findings distinguish
RIPE policy, database schema and audit rules. Missing IPv4 assignments inferred
from BGP are warnings. Existing IPv4 allocations larger than /24 are not flagged
solely because of their size.

### ASN status colours

| Status                                                               | Display                             |
| -------------------------------------------------------------------- | ----------------------------------- |
| Currently announcing                                                 | Neutral: **Active**                 |
| Not announcing now, but active within the last three calendar months | Yellow: **Inactive < 3 months**     |
| Newly registered and not announcing                                  | Yellow: **New ASN · not announced** |
| Inactive for more than three calendar months                         | Red finding                         |
| Insufficient activity or registration data                           | Explicit unknown status             |

Yellow statuses do not increase the finding count. An unavailable source is not
treated as evidence that a resource is compliant.

### Data sources and coverage

Discovery starts with the [RIPE NCC allocation catalogue](https://ftp.ripe.net/ripe/stats/membership/alloclist.txt).
The [RIPE Database REST API](https://docs.db.ripe.net/RIPE-Database-Structure/REST-API/)
provides registrations and ORG relationships; [RIPEstat / RIS](https://stat.ripe.net/docs/data-api/)
provides routing and ASN activity data. Source timestamps are shown in UTC.

Public ORG relationships can span multiple LIR accounts, so public discovery is
not an exact private LIR Portal inventory. Catalogue updates may also lag behind
registration changes. Optional LIR Portal keys can refine the inventory; see
[private inventory](docs/operations.md#private-lir-portal-inventory).

Contracts, assignment justification, utilisation and aggregate assignment
statistics require manual review. The audit does not establish full policy
compliance. BGP-only tree entries are observations, not additional RIPE objects.

## Configuration

Docker Compose reads `.env` automatically. Local runs use exported environment
variables. Defaults are listed in [.env.example](.env.example).

| Variable                    | Default               | Purpose                                                                |
| --------------------------- | --------------------- | ---------------------------------------------------------------------- |
| `AUDIT_IMAGE`               | `ripe-db-audit:local` | Compose image; set to a GHCR tag to use a published release.           |
| `AUDIT_BIND`                | `127.0.0.1`           | Compose host bind address.                                             |
| `AUDIT_PORT`                | `8090`                | Compose host port.                                                     |
| `AUDIT_DB`                  | `data/audit.sqlite3`  | Local SQLite path. Compose fixes this to `/app/data/audit.sqlite3`.    |
| `AUDIT_MAX_JOBS`            | `2`                   | Maximum concurrent analyses.                                           |
| `AUDIT_MAX_OBJECTS`         | `20000`               | Maximum resources collected per analysis.                              |
| `AUDIT_MAX_ROUTING_QUERIES` | `4096`                | Maximum BGP queries per analysis.                                      |
| `AUDIT_JOB_TIMEOUT`         | `1800`                | Analysis timeout in seconds.                                           |
| `AUDIT_ALLOWED_ORIGINS`     | Unset                 | Exact public origins for proxy clients without Fetch Metadata headers. |
| `FORWARDED_ALLOW_IPS`       | Uvicorn default       | Trusted proxy addresses/networks for forwarded headers.                |
| `AUDIT_ACCESS_TOKEN`        | Unset                 | Optional shared instance access token.                                 |
| `RIPE_API_KEYS_JSON`        | `{}`                  | Server-side regID-to-LIR-Portal-key mapping.                           |
| `AUDIT_WEBHOOK_URL`         | Unset                 | Optional HTTPS completion callback.                                    |
| `AUDIT_WEBHOOK_SECRET`      | Unset                 | HMAC secret for completion callbacks.                                  |

Portal keys require `AUDIT_ACCESS_TOKEN`. When access protection is enabled, the
UI prompts for the token; API clients can use `Authorization: Bearer <token>`.
There are no per-user accounts: authorised users share access to the instance's
analyses. See [authentication and webhooks](docs/operations.md).

## Storage and deployment

Jobs, results and progress events are stored in SQLite. Compose mounts a persistent
`analysis-data` volume at `/app/data`.

- **Cache:** complete results are reused for 3,600 seconds after completion.
- **Retention:** cache expiry does not delete jobs or change their IDs. Saved result links continue to work while the database is retained.
- **Failures:** partial and failed results are not reused as successful cache entries. Analyses interrupted by a restart must be run again.

Keep the same Compose project and data volume when redeploying. A new or empty
volume starts a separate history. `docker compose down` preserves the volume;
`docker compose down -v` deletes it and the stored results.

```sh
docker compose ps
docker compose logs -f analyzer
docker compose down
```

Run **one application worker**: job scheduling and deduplication use in-process
state. The container runs as a non-root user, has a healthcheck and keeps the
application filesystem read-only.

For external deployment, configure HTTPS and access control at your reverse proxy.
Disable proxy buffering for `/api/analyses/*/events` and keep read timeouts above
the 10-second SSE heartbeat interval. The default Compose port is loopback-only.

See [backup and database migration](docs/operations.md#backups-and-migration)
before moving an existing installation, and [reverse proxy setup](docs/operations.md#reverse-proxy-setup)
when serving the application behind HTTPS.

## API

Start an analysis:

```sh
curl -X POST http://127.0.0.1:8090/api/analyses \
  -H 'Content-Type: application/json' \
  -d '{"regid":"ru.ecode"}'
```

The response contains the job `id`. A valid cached result returns the same ID with
`cache_hit: true`; a running analysis for the same regID is joined.

| Method | Endpoint                                     | Purpose                                                      |
| ------ | -------------------------------------------- | ------------------------------------------------------------ |
| `POST` | `/api/analyses`                              | Start, join or reuse an analysis.                            |
| `GET`  | `/api/analyses`                              | Latest 20 stored jobs.                                       |
| `GET`  | `/api/analyses/{id}`                         | Job status and progress.                                     |
| `GET`  | `/api/analyses/{id}/events`                  | SSE progress stream.                                         |
| `GET`  | `/api/analyses/{id}/result`                  | Result metadata and summary; `?full=true` includes all data. |
| `GET`  | `/api/analyses/{id}/resources`               | Paginated resources.                                         |
| `GET`  | `/api/analyses/{id}/resources/{resource_id}` | Resource details, findings and routes.                       |
| `GET`  | `/api/analyses/{id}/findings`                | Paginated findings.                                          |
| `GET`  | `/api/analyses/{id}/export.csv`              | Findings CSV.                                                |
| `POST` | `/api/session`                               | Create a browser session using an instance token.            |
| `GET`  | `/api/health`                                | Health and ruleset information.                              |

Resources support `family`, `q`, `org`, `bad`, `parent`, `include_bgp`, `offset`
and `limit`. Findings support `family`, `q`, `offset` and `limit`. Page size defaults
to 50, with a maximum of 200. URL-encode resource IDs containing slashes or colons.

The SSE stream emits `progress` and `finished` events. Reconnect using
`Last-Event-ID` or `?after=<event-id>`. The UI also polls status if streaming is
unavailable. See `/docs` for request and response schemas.

## Development

Use **Python 3.13+** and **Node.js 24**. The stack is FastAPI, React, TypeScript,
Tailwind CSS, shadcn/ui and SQLite.

```sh
./start.sh
```

The script creates a virtual environment, installs dependencies, builds the
frontend and starts the application at http://127.0.0.1:8090. It accepts `AUDIT_HOST`
and `PORT` to change the local bind address and port.

For frontend development, keep the backend running and use a second terminal:

```sh
npm --prefix frontend run dev
```

Vite proxies `/api` to the backend on port 8090.

### Checks

```sh
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend scripts
npm --prefix frontend run format:check
npm --prefix frontend run build
python3 scripts/verify_docker.py
```

Docker verification uses a temporary Compose project to check the API, frontend,
SSE and job persistence across container recreation. It does not query RIPE or
modify the application's existing database.

GitHub CI runs backend tests, linting, frontend builds and Docker checks. Browser
flows are not part of CI. An optional local browser check is available through
`node scripts/verify_ui.mjs`; set `CHROME_PATH` if Chrome is not at the default
macOS location.

## Releases

[Release history](https://github.com/mraliscoder/ripe-db-audit/releases) ·
[Container images](https://github.com/mraliscoder/ripe-db-audit/pkgs/container/ripe-db-audit)

Pushing a version tag runs the release checks, publishes multi-platform images to
`ghcr.io/mraliscoder/ripe-db-audit` and creates a GitHub release:

```sh
git tag v0.1.2
git push origin v0.1.2
```

Stable releases publish exact-version, minor-version and `latest` tags. Major
version tags are also published from version 1 onwards. Prereleases publish only
their exact version and leave `latest` unchanged. Release notes include an
immutable image digest for pinned deployments.
