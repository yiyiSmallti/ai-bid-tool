---
kind: reference
---

# Task membership, archival and durable progress

## Problem

An org (organization/tenant; 单位) is the storage isolation boundary, but shared
org membership must not disclose every bid task (任务). A dashboard (看板) must
project committed requirements and current validity without becoming a second
source of approval. Disconnects must not lose changes or expose private job data.
The approved boundaries are in [Team workflow](../plan/team-workflow.md).

## Usage

Create a task through `bid task create`; its eligible human creator becomes its
owner in the same transaction, including tasks created by that human's scoped
token. Use `bid task workflow`, `bid task member list` and `bid task member
candidates` to inspect current authority. Membership writes, handover and lifecycle
commands require a reason and the expected workflow revision. A revision conflict
requires a new read and an explicit decision, not automatic replay.

The console exposes `/app/org/tasks/:taskId/board?job=J` and
`/app/org/tasks/:taskId/members`. Select a successful extraction explicitly.
`bid task progress` reads jobs before an extraction exists; `bid task board --job J`
returns one row per saved requirement in that extraction. `bid task events`
returns one bounded Result for polling. Streaming clients use authenticated fetch
with Bearer and `X-Org-Id`, and resume with `Last-Event-ID`.

For an existing deployment, drain old workers and stop admissions before enabling
task ACL. The migration does not infer members from historical activity. Run
`python -m app.admin team-workflow preflight --org-id ORG_UUID`, prepare the reviewed
mapping described by [team_workflow_admin.py](../../server/app/team_workflow_admin.py),
including `reviewed: true` and an active org administrator in
`reviewed_by_user_id` for the import audit,
then run `team-workflow import --org-id ORG_UUID --mapping FILE` and
`team-workflow cutover --org-id ORG_UUID --workers-drained`. Every existing task
must have a reviewed owner and explicit member list; unresolved tasks, unfinished
jobs or unsettled vendor calls prevent cutover. Keep the mapping as deployment
recovery material outside the repository. Roll forward after authorization cutover;
do not serve an older binary that ignores task membership or archival.

## How it works

`task_workflows` stores the lifecycle, owner, workflow revision and access epoch.
`task_members` stores one task role and bounded review domains per person.
Composite foreign keys bind people to org membership and tasks to their org.
Deferred constraints and a partial owner index prevent committing zero or multiple
owners. Mutation reasons are encrypted on the owning workflow/member record;
audit entries contain their hashes, actor kind and before/after roles or states. These tables and the event tables use ENABLE and FORCE RLS under
`app.current_org`; task ACL adds a narrower service boundary.

Effective authority intersects current active user/org membership, the current org
role, token scopes, task role and existing object/domain/human gates. A human org
admin can read and recover membership without joining a task. This exception never
becomes a token or worker grant. Observers read; reviewers make authorized human
decisions; contributors and the owner may perform permitted task operations.
Ownership grants neither a new review domain nor export permission.

Archive takes the task/workflow lock and refuses queued/running jobs or
pending/unknown vendor calls, including calls on terminal jobs. Archived tasks
remain readable under current grants; existing downloads retain their freshness
checks. Uploads, edits, new jobs, new preview conversion, release and membership
changes require an active task. Explicit unarchive is the lifecycle exception.
Already-dispatched usage continues to settle through `JobExecution`; archival
does not erase liabilities. Local evidence-page rendering checks write authority
before preparation without retaining a task lock, then locks and refreshes the
workflow/member and selected source again before publication. Ownership recovery
resolves the old member's availability before changing its stored role, preventing
an autoflush of a half-prepared recovery.

Board reads use a short repeatable-read snapshot, explicit extraction binding,
current card eligibility and shared task-budget projections. Encrypted cursors
bind org/task, actor visibility, filters, access epoch, event watermark and UTC
assessment date. Rows use stable requirement UUID order. Scope, page, time and
serialized-response limits fail explicitly rather than truncate counts. Clock
validity refreshes within thirty seconds even without a mutation event.
Citation validity is computed in source groups and reused for each card within
the snapshot; UUID ordering does not cause source normalization cache thrashing.
The shared [bulk citation mechanism](docx-citations.md#how-it-works) retains the
scalar predicate for every saved requirement, including requirements without cards.

Business changes produce metadata-only `board_changed`, `job_progress` and
`access_changed` records in their own transaction. The independent event head
locks sequence allocation until commit; rollback removes both state and sequence
increment. Runtime producers require their authenticated org context. A database
administrator already permitted to cross RLS may perform offline writes without
that setting: the invoker trigger derives each org from its actual transition rows,
sets the context only around the strict append call, then restores it. An explicit
mismatched org still fails, and neither `bid_app` nor direct append calls acquire
this exception. Ordered replay uses opaque encrypted checkpoints; raw sequence values,
quotes, filenames, content, money and download links never enter frames. Replay
checks current authorization and job-specific visibility. SSE releases business database
transactions before writing to the socket; a shared AUTOCOMMIT advisory-lock
connection enforces stream caps across API processes, and a lost connection fences
its streams. Retention loss asks for a new snapshot,
and browser transport failures fall back to bounded polling.

## Pitfalls

A task owner is escalation responsibility, not the assignee of every requirement.
Assignment, discussions and co-sign writes belong to later slices. Approval of the
[ADR amendment](../adr/0005-human-confirmed-responses.md) does not activate those
handlers or weaken existing single-domain confirmation gates.

An active stored task-member row cannot revive a disabled org member. Ownership
recovery is an explicit admin operation. Reconnection never resubmits paid work.
A board badge is advisory; draft/export services recheck the authoritative inputs
and human gates. Shared org-memory content keeps its org policy; source references are projected
with `provenance_redacted: true` when their task is inaccessible, preserving the
immutable stored chain. Shared library mutations do not disclose affected task IDs and
must fail above the approved fan-out bound rather than lose invalidations.

## Code

- [task_workflow.py](../../server/app/services/task_workflow.py): current authorization,
  membership, handover and lifecycle service.
- [task_authorization.py](../../server/app/services/task_authorization.py): declared
  task/stored-parent guards on existing service entries.
- [0041_team_workflow.py](../../server/migrations/versions/0041_team_workflow.py):
  org tables, owner constraints, human-only scope exclusions and lifecycle gates.
- [task_board.py](../../server/app/services/task_board.py): bounded read projections.
- [task_events.py](../../server/app/services/task_events.py) and
  [task_event_sql.py](../../server/app/services/task_event_sql.py): encrypted replay
  checkpoints and transactional producers.
- [task_board.py API](../../server/app/api/task_board.py): snapshots and streaming
  transactions; [task-live.js](../../web/src/task-live.js): reconnect and polling.
- [team_workflow.py CLI](../../cli/bid_cli/team_workflow.py): shared API commands.
- [Membership acceptance](../../server/tests/test_team_workflow_membership.py),
  [board/event acceptance](../../server/tests/test_team_board_events.py),
  [legacy route acceptance](../../server/tests/test_team_workflow_legacy.py) and
  [browser scenarios](../../web/e2e/team-workflow.spec.js): reproducible gates.
