# ARC LIR Analyzer

Audience: network operators and LIR administrators. Enter a RIPE regID and inspect
registered IPv4, IPv6, own/sponsored ASNs, BGP visibility and explainable findings.
The application reads sources; it never changes RIPE objects or sends email/tasks.

## Authoritative scope and policy

- User brief: analysis by regID, real-time progress, one-hour result cache.
- Existing ARC audit: IPv6 ASSIGNED /48–/64, aggregate assignment-size /48–/64,
  five ALLOCATED-BY-LIR per ancestry, at least one assignment per individual
  announcement, three calendar months of ASN origin activity. These are audit
  requirements, not universal RIPE schema constraints.
- IPv4: [RIPE-826](https://www.ripe.net/publications/docs/ripe-826/), §§4, 5.3,
  6.2, 6.3 and 7. /24 in §5.1 governs new pool allocations, not all existing
  resources. ALLOCATED-ASSIGNED PA is an assignment. IPv4 aggregate
  assignment-size is optional; non-CIDR inetnum ranges are valid.
- BGP absence/presence is observation, not proof of contractual compliance.
  Missing IPv4 assignment inferred from BGP is a warning, explicitly an audit rule.
- Contracts, end-user purpose/uniformity, utilisation statistics and allocation
  history require manual review. They are never reported automatically verified.

## Resource discovery

Official allocation catalogue maps regID to roots. The root objects identify ORG.
Inverse org / sponsoring-org searches add registered and sponsored resources,
and all-more queries collect descendant inetnum/inet6num. Every relation is kept.
Different LIR accounts can share an ORG; public discovery is labelled accordingly.
It is not an exact private LIR Portal inventory. Optional server-side My Resources
keys refine root inventory; private mode requires instance authentication.

Sources: official [allocation catalogue](https://ftp.ripe.net/ripe/stats/membership/alloclist.txt),
[database REST API](https://docs.db.ripe.net/RIPE-Database-Structure/REST-API/),
[RIPEstat](https://stat.ripe.net/docs/data-api/),
[My Resources API](https://www.ripe.net/publications/documentation/developer-documentation/lir-portal-api-keys/).

## Lifecycle, retention and security

queued → running → complete / partial / failed / interrupted. Same regID joins an
active job. Only complete results are reused, for 3,600 seconds from completion;
partial/failed jobs never poison success cache. Cache keys include rules and
credential scope. SQLite preserves snapshots and replayable events across restarts;
history is local until the operator removes the database. A restart interrupts
unfinished jobs and preserves completed results. One application worker owns jobs.
Only source-constructed HTTPS RIPE URLs are fetched; input is a validated regID.
No nameservers, URLs or callbacks supplied by users are fetched.

Portal keys and webhook secrets are server environment only. Optional instance
access token protects all analysis APIs. Browser login uses an HttpOnly,
SameSite=Strict, signed eight-hour cookie; no credentials in URLs or local storage.
Deployment beyond loopback is an operator action and requires appropriate access
control/reverse proxy. Public mode has no user accounts or organisation permissions.

SSE streams persisted events with resume IDs and heartbeat; polling independently
recovers terminal status. There are no inbound webhooks. Optional admin-configured
HTTPS completion callbacks use HMAC-SHA256, stable event IDs and bounded retries.
Receivers must deduplicate by event ID. No webhook is configured or sent by default.
