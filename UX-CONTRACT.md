# RIPE DB Audit observable UI contract

## Sources and scope

Domain source: PRODUCT.md and backend/app/rules.py. API schema: /docs.
Visual source: DESIGN.md and frontend/src/index.css. English UI, en-GB formatting,
UTC times. Registry names are source data and are not translated.
Read only: no email, task creation or RIPE writes occur in this application.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Form | App.tsx forms + shadcn Input/Label/Button | PRODUCT.md, this contract | analysis, instance access | UI tests |
| Select/Listbox | shadcn Select backed by Radix | DESIGN.md | family and page size | keyboard UI tests |
| Scrollbar | frontend/src/index.css global tokens | DESIGN.md | tables, sheets | mobile overflow test |
| Toast | shared notice region + shadcn Alert | this contract | error, incomplete coverage | UI failure tests |
| CRUD | Jobs API + App.tsx lifecycle | PRODUCT.md | create analysis/read result | API + UI tests |
| Table Selection | prefix-button + shadcn Sheet | this contract | resource details | UI tests |

## State and flow ledger

| Trigger | Outcome | Recovery | Domain source |
|---|---|---|---|
| Submit regID | validate, POST; join/cache/new job | inline error, value retained | PRODUCT.md |
| Running | current stage and actual count; SSE and fallback polling | reload resumes job URL | PRODUCT.md |
| Complete | hide form; show result navigation, cache expiry and Another analysis | URL restores filters/page | PRODUCT.md |
| Partial | persistent incomplete Alert | source details; rerun | PRODUCT.md |
| Failed | persistent English error and retry form; result navigation stays hidden | rerun | PRODUCT.md |
| Resource click | accessible modal Sheet with evidence | close/Escape, restore trigger focus | PRODUCT.md |
| Access required | masked token form, HttpOnly signed cookie | inline error; no token persistence | PRODUCT.md |

Resources, tree and findings share filters and snapshot. Primary navigation lives
in the sidebar alongside the audit heading, description, LIR details, timestamps
and resource counts; the content area uses a plain view heading and table controls. Authored shadcn Select
owns focus, popup width, collision handling and keyboard navigation. Server pages
are 25/50/100. Tree roots paginate; child pages are bounded at 200 with a Load more
action. Search is 300ms, IME-aware, stale responses abort; explicit clear is
immediate and restores focus. Codes, provenance, routes and detailed rules are
secondary content, not repeated on every row.

## Async and cache

Completed_at + 3600 determines reuse. Same regID joins existing work; submitting
is disabled while busy. Expired snapshots remain viewable, labelled historical.
SSE replays events and reconnects; a 3-second polling fallback recovers blocked
streams. Stale fetches cannot replace a newer job or selected resource.
Partial and failed jobs are not reused as successful cache entries.
Result navigation is hidden until the current job has a complete or partial result.
Another analysis clears job, result and filters, then focuses a blank regID form.
Recent analyses and global history requests are removed; no history is stored in
the browser. Exact cache expiry appears only after analysis. Expired snapshots
show Cache expired on; partial results show Not cached.

## Accessibility and states

English accessible names; labelled forms, visible focus, native table semantics,
aria-invalid/describedby and live progress. Empty, no match, source failure and
clean results have distinct text. Shared shadcn Sheet traps focus and restores it
on close; dropdowns support keyboard and Escape. Table overflow is local and the
whole page scrolls normally. Alerts persist and never rely only on colour.
Desktop navigation remains pinned while results scroll. On short viewports the
sidebar scrolls internally; mobile navigation stays in normal flow.
Reduced motion and forced colours are supported. No browser alert/prompt dialogs.

ASN activity display: currently announcing is Active. No current announcements
with activity within three calendar months is yellow, labelled Inactive < 3 months.
New ASNs without announcements are also yellow, labelled New ASN · not announced.
These statuses are not findings. Longer inactivity retains the existing violation.
Legacy cached recently_announced + BGP none renders the same yellow state.

Initial analysis form has an empty regID value; ru.ecode appears only as its
placeholder. Same-origin Fetch Metadata works across reverse proxies; clients
without it use exact-origin verification or the configured public-origin list.
