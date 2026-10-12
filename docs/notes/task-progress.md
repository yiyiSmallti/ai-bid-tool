---
kind: reference
---

# Task board and durable progress

## Problem

A task dashboard must reflect committed requirements, response validity and job
progress without becoming a second approval source. Disconnects must not lose
changes or expose private job data. The installation boundary and transition
scope are defined by [ADR 0012](../adr/0012-single-user-installation.md) and
[the single-user plan](../plan/single-user.md).

## Usage

Open `/app/org/tasks/:taskId/board?job=J` and select a successful extraction
explicitly. The board has one row per saved requirement, including missing cards.
Progress works before an extraction exists. Activity shows authorized committed
changes. CLI steps are in [Inspect task progress](../guides/cli.md#inspect-task-progress).

| API | CLI | Projection |
| --- | --- | --- |
| `GET /tasks/{T}/board?extraction_job_id=J` | `bid task board --task T --job J` | Bounded requirement rows, counts, blockers and next actions |
| `GET /tasks/{T}/progress` | `bid task progress --task T` | Visible jobs and snapshot cursor; no extraction needed |
| `GET /tasks/{T}/activity` | `bid task activity --task T` | Bounded activity page |
| `GET /tasks/{T}/events/poll` | `bid task events --task T` | One Result with a bounded event page |
| `GET /tasks/{T}/events` | Authenticated browser fetch | SSE replay with `Last-Event-ID` |

Board filters are bucket, category, starred, review domain, blocker and `mine`.
`mine` identifies human decisions the current actor can perform; it is not an
access grant. Requirement-review board/progress reads additionally use
`--view requirement-review --job J`; their contract is in
[Requirement confirmation](../plan/requirement-confirmation.md#dashboard-progress-and-console-outline).

## How it works

### Task access

[task_access.py](../../server/app/services/task_access.py) refreshes the active
identity, org membership and current grants before checking the task and stored
creator membership. Scoped tokens intersect the issuer's live grants and cannot
receive human-only decisions. Task creation seeds its creator atomically in
`seed()` in [task_access.py](../../server/app/services/task_access.py), with the
existing database constraints enforcing the stored relationship. The first single-user slice keeps that schema and
the default policy without co-sign. Missing and inaccessible objects return the
same 404; authority checks re-run at write and worker-publication boundaries.

### Bounded snapshots

The selected extraction binds task, job and document; the board never picks the
latest job or merges jobs implicitly. Counts describe saved requirements only,
not whether the tender has omitted requirements. Buckets distinguish gaps,
draft cards, pending review, needs material, confirmed responses and comply-only
human decisions. Confirmation eligibility follows
[Response cards](response-cards.md); a confirmed row is not final-export approval.
Exact source, response and evidence content loads through authorized detail reads.

Board pages default to 50 rows and cap at 100. The extraction scope caps at
5,000 requirements, the response at 1 MiB and job/activity summaries at 20.
Overflow fails with `board_limit_exceeded`; a query timeout fails with retryable
`board_busy`. Counts and dependencies use one short snapshot and batched reads.
Progress pages cap at 20 jobs, activity and polling pages at 100 entries.
An authenticated encrypted cursor binds task, extraction, actor visibility and
filters. A changed or expired snapshot requires reloading the first page.

Certificate-date warnings use the board's UTC assessment date and do not claim
procurement-date validity. Prototype keep/replace decisions remain a separate
final-export gate. Known progress units may have totals; unknown totals never
become an invented percentage. Labels, blockers and next actions describe actual
state and authority rather than starting jobs or confirming anything.

### Durable events

`task_event_heads` and `task_events` keep a task-local committed order. Producers
lock the head after business locks, increment and insert events in the business
transaction, then hold the head until commit. Rollback removes the event and its
increment. A later transaction cannot commit a higher cursor ahead of it.
PostgreSQL notification is only a wake-up hint; replay reads committed rows.
Shared-library mutations above the 100-task fan-out bound fail explicitly.

Event envelopes carry IDs, time and bounded state/progress metadata. They contain
no quotes, response text, model input, filenames, arbitrary job results, URLs or
signed downloads. Cursors are encrypted so hidden job activity cannot leak through
visible sequence gaps. Board invalidations trigger an authorized refetch; frames
never directly approve a response.

Streams use authenticated fetch with Bearer and `X-Org-Id`; credentials never
enter URLs. A snapshot supplies the initial event cursor and reconnect supplies
`Last-Event-ID`. Clients deduplicate repeated event UUIDs. A missing, expired or
pruned cursor explicitly requests a fresh snapshot. Retention keeps at most seven
days and 50,000 events per task; it does not delete audit history.

Each replay batch and idle heartbeat rechecks identity, current grants, token
expiry and job visibility. Stream handlers release database transactions before
socket writes. Revocation stops delivery; org switch or logout clears the old
view. Bounded buffers disconnect slow clients so they can resume by cursor.

The browser reconnects with bounded backoff and falls back to event polling and
snapshot reload. It honors `Retry-After`, reduces work in hidden tabs and refreshes
clock-derived validity. Authorization failures stop retries. Ordinary polling
returns one seven-field Result, with `wait_seconds` between 0 and 25; SSE frames
are a separate transport and do not change CLI JSON output.

## Pitfalls

- A board count or event is observation, not human approval or evidence confirmation.
- Retained legacy workflow rows do not authorize restoring member, assignment,
  discussion, archival or co-sign operations.
- A signed download still requires the current authorized session and freshness
  checks; a snapshot cursor cannot authorize another task or actor.
- Provider failure, incomplete jobs and stale material remain explicit blockers.
  Refresh and review current inputs before making a new human decision.

## Code

- [task_board.py schemas](../../server/app/schemas/task_board.py): read projections, bounds and cursors.
- [task_board.py service](../../server/app/services/task_board.py), [task_board_projection.py](../../server/app/services/task_board_projection.py): snapshots and derived state.
- [task_events.py](../../server/app/services/task_events.py), [task_event_sql.py](../../server/app/services/task_event_sql.py): replay and transactional event producers.
- [task_board.py API](../../server/app/api/task_board.py): snapshot, poll and stream routes.
- [task_board.py CLI](../../cli/bid_cli/task_board.py): bounded JSON commands.
- [OrgTaskBoard.vue](../../web/src/views/OrgTaskBoard.vue), [task-live.js](../../web/src/task-live.js): browser board and reconnect behavior.
- [Board/event acceptance](../../server/tests/test_team_board_events.py), [CLI acceptance](../../server/tests/test_task_board_cli.py): retained runtime and command contracts.
