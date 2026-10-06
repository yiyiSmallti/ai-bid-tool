---
kind: reference
---

# Task membership, discussion, co-sign and durable progress

## Problem

An org (organization/tenant; 单位) is the storage isolation boundary, but shared
org membership must not disclose every bid task (任务). A dashboard (看板) must
project committed requirements and current validity without becoming a second
source of approval. Disconnects must not lose changes or expose private job data.
Requirement ownership and card discussion must remain separate from response
approval, and comments must not become evidence (证据) or model input.
Where a requirement needs both professional domains, approval must bind distinct
humans to the same current inputs and remain valid at every consumption boundary.
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

Assign requirement work with `bid card assign --task T --requirement R --job J
--input FILE`. The JSON input carries `expected_assignment_revision`,
`assignee_user_id` (or null to unassign), and a reason. A requirement can be assigned
before a response card (响应卡) exists. The console offers assignment in the board
row using active eligible task members; the task owner and a human org administrator
can manage it.

Use `bid card thread list --card C`, `bid card thread create --card C --input FILE`,
`bid card comment list --card C --thread H` and `bid card comment add --card C
--thread H --input FILE`. Thread creation requires `expected_card_revision`;
both writes require a `client_request_id`, plain-text `body` and optional
`mentioned_user_ids`. The console's discussion tab keeps the draft in memory,
selects mentions from authorized current task members and renders messages as
escaped text. Read pages use an opaque `--cursor` and default 50/max 100 entries.
Retry an uncertain comment result only with the same UUID and unchanged input;
changed content requires a new request UUID. An explicit revision conflict requires
refreshing and reviewing the current state.

Inspect the task rule with `bid task review-rule show --task T`; an owner or human
org administrator previews a change with `bid task review-rule set --task T --input
FILE --dry-run` and submits the same reviewed input. Inspect or change an explicit
requirement policy through `bid card policy show/set --task T --requirement R --job
J`. Rule and policy changes require their expected revision and an encrypted reason.
The board and card panel show required, signed and pending domains separately.

Normal card submission opens response review. Use `bid card signoff list --card C`
to inspect its round and signature history, and `bid card signoff add --card C
--input FILE` to sign the exact card revision and round for the current human's
selected domain. Every response signer explicitly reviews all Evidence IDs and
warnings. For disposition, use `bid card review-round open --card C --input FILE`,
then sign with purpose `disposition`, no Evidence IDs and a reason. The authorized
round view exposes its decrypted intended disposition reason for review. Signatures
have independent encrypted handling reasons and bind the immutable round reason.
History pages are newest first; encrypted cursors bind the card, task and reader.
A lost reply can be retried with the identical request UUID and input; a 409 requires
refresh and a new human review, never automatic resubmission.

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

Assignment metadata binds one saved requirement to its successful extraction and
task. Writes serialize with task membership changes, compare the assignment revision
and require an active owner/contributor with existing edit authority as the target.
Removing or demoting an assigned member fails until their work is explicitly
reassigned or unassigned. External org revocation still takes effect immediately;
the board marks the unavailable assignee for the owner to resolve. Assignment
changes neither card content nor review authority.

Human task readers, including observers with org role `viewer`, may append comments
through the session-only `card:comment` grant. Tokens and workers cannot obtain it.
Archived tasks retain readable discussion but reject new writes. A thread pins the
card revision that existed at creation; later comments never update card revisions,
confirmation or evidence. Bodies are encrypted on the owning message, bounded to
4,000 Unicode characters/16 KiB UTF-8, and mentions to 20 unique current task members.
Request UUID receipts bind the authenticated actor, endpoint and input hash in the
same transaction; identical replay returns the original receipt and a different
input conflicts. Thread/comment pages use chronological keysets and encrypted
cursors bound to org/task/card/thread and current reader authority. Audit records
contain IDs and hashes; durable events contain bounded IDs only, so discussion text
and member labels stay out of live frames and activity summaries.

The requirement's explicit flag and the starred-task rule determine required domains;
an explicit false cannot defeat a matching rule, and the primary domain remains
required. `card_review_rounds` freezes the card revision, requirement/citation,
material/content hashes, policy revisions, intended disposition and required domains.
`card_review_signatures` binds one distinct human per domain to the round. The DB
computes snapshots and signature ordinals under locks rather than trusting submitted
hashes or actor fields. Policy and rule change times prevent an old legacy approval
from becoming eligible again merely because a stricter policy was later disabled.

Partial response signatures keep the card pending and do not confirm Evidence or
append a content revision. The last authorized human request revalidates every
signer, material and warning, appends its signature, confirms Evidence and appends
the established confirmation revision atomically. The actual finalizing human stays
in `confirmed_by`; the complete signature set governs co-sign approval. Disposition
rounds apply the intended disposition only on completion and never confirm Evidence.
Mixed legacy disposition batches preflight every requirement and reject atomically
when any needs multiple domains. Existing single-domain confirmation commands use a
real one-domain round for newly submitted cards, retaining their Result shape.

Revisions, policy changes, citation/material changes and loss of an actual signer's
grants append `card_review_invalidations`. Re-adding a signer never restores a
retired round; unrelated assignment, discussion or member edits do not retire it.
Reads recalculate dependency and signer validity even without a materialized
invalidation. User and org activation epochs prevent an authorization restored
later from reviving a signature. Platform org-management functions update only the
org epoch; they do not read tenant review content or receive business-table grants.
The review validity predicate checks that epoch even before retirement is materialized. Complete-round checks apply to direct SQL revision/Evidence/response
item writes as well as service actions. All review history uses org/task composite
keys, FORCE RLS and immutable records, with metadata-only audit and durable events. A narrow column-level update grant permits
review-round row locks; history triggers still reject every actual update/delete.

Drafts turn incomplete or invalidated approval into explicit `cosign_required` gaps
and bind policy, round, signature IDs and hashes in their input manifest. Untouched
legacy single-domain inputs keep their original manifest shape. Draft freshness,
check and score input assembly, export preview/admission/publication/release/download
revalidate current approval. An old draft read omits revoked response text and
Evidence from its current table projection. Export keeps its original Evidence,
negative-deviation, review-copy and final prototype keep/replace gates; co-sign
completion never supplies a prototype decision or an export authorization.

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
An assignment or comment does not confirm a response, and mention text alone does
not identify a person: only the validated mention ID list does. Discussion has no
external notification, edit/delete operation, automatic memory or model ingestion.
The [co-sign ADR amendment](../adr/0005-human-confirmed-responses.md) requires
complete current rounds at consumption; a stored confirmed state, old Evidence
flags or historical signatures alone do not authorize reuse. Archived tasks permit
review-history reads but no policy, round or signature mutations.

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
- [task_discussion.py](../../server/app/services/task_discussion.py): revision-checked
  assignment, immutable discussion, mention validation and replay receipts.
- [0041_team_workflow.py](../../server/migrations/versions/0041_team_workflow.py):
  org tables, owner constraints, human-only scope exclusions and lifecycle gates.
- [0043_team_workflow_assignment.py](../../server/migrations/versions/0043_team_workflow_assignment.py):
  assignment/discussion RLS tables, immutable messages and membership-change guards.
- [task_cosign.py](../../server/app/services/task_cosign.py): policies, current approval,
  encrypted disposition reasons, signature receipts and atomic human completion.
- [0044_team_workflow_cosign.py](../../server/migrations/versions/0044_team_workflow_cosign.py):
  co-sign history, direct SQL gates, retirement and transactional producers.
- [drafts.py](../../server/app/services/drafts.py),
  [exports.py](../../server/app/services/exports.py) and
  [score_run_inputs.py](../../server/app/services/score_run_inputs.py): bound consumer manifests.
- [Co-sign database acceptance](../../server/tests/test_team_cosign_database.py),
  [consumer acceptance](../../server/tests/test_team_cosign_acceptance.py) and
  [console scenarios](../../web/e2e/cosign.spec.js): review and consumption boundaries.
- [task_board.py](../../server/app/services/task_board.py): bounded read projections.
- [task_events.py](../../server/app/services/task_events.py) and
  [task_event_sql.py](../../server/app/services/task_event_sql.py): encrypted replay
  checkpoints and transactional producers.
- [task_board.py API](../../server/app/api/task_board.py): snapshots and streaming
  transactions; [task-live.js](../../web/src/task-live.js): reconnect and polling.
- [team_workflow.py CLI](../../cli/bid_cli/team_workflow.py): shared API commands.
- [Assignment/discussion CLI acceptance](../../server/tests/test_team_discussion_cli.py):
  authenticated local/remote transport, bounded inputs, snapshots and discovery.
- [Membership acceptance](../../server/tests/test_team_workflow_membership.py),
  [board/event acceptance](../../server/tests/test_team_board_events.py),
  [legacy route acceptance](../../server/tests/test_team_workflow_legacy.py) and
  [browser scenarios](../../web/e2e/team-workflow.spec.js): reproducible gates.
