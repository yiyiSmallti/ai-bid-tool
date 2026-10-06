---
kind: plan
---

# Team workflow: task membership, dashboard, live progress, and co-sign

Status: **approved; slices 1 and 2 implemented**. Covers [roadmap](roadmap.md) F06,
U01, U02, and B07. The owner approved every default in [Decisions](#decisions),
including the co-sign amendment to [ADR 0005](../adr/0005-human-confirmed-responses.md).
Slice 1 supplies task membership, archival, the read board and durable progress.
Slice 2 supplies requirement assignment and immutable card discussion with in-task
mentions. Slice 3 remains approved and unimplemented; co-sign write handlers are
not registered. The [contract module](team-workflow/team_workflow_contracts.py)
records the approved interfaces; [runtime schemas](../../server/app/schemas/team_workflow.py)
implement the slices 1 and 2 subset with the shared Result 4.0 budget types.
See [Team workflow](../notes/team-workflow.md) for the mechanism and cutover procedure.

## Goal and boundary

Give bid specialists (投标专员) a structured cloud workflow for preparing a bid
(标书): identify who owns each task (任务) and requirement, what is blocked, and the
next action each person can take. The task board is the dashboard (看板), not a
project-management system requiring staff to maintain a second set of statuses.
An org (organization/tenant; 单位) remains the tenant boundary; task membership adds
an authorization boundary inside it.

The board projects requirements from one explicitly selected successful extraction
job, response cards (响应卡), evidence (证据), jobs, and existing material validity.
It adds task membership, assignment, card discussions, and co-sign (会签) by review
domain (职责). It preserves the separation between a model proposal, human
confirmation (人工确认), table assembly (组表), and export authorization.

Exclude org account provisioning/invitations, custom review-domain vocabularies,
electronic signatures, external messaging/email, workflow designers, deadlines per
card, multiple extraction-set merging, shared cross-org tasks, new paid Provider
calls, and automatic approval or reassignment. Mentions notify inside the task
only. Comment text never becomes evidence, response (响应) text, memory, or model
input automatically. Budget enforcement belongs to [budget.md](budget.md).

## Code basis and differences from the design

These are integration boundaries. Membership, assignment and discussion are
implemented; co-sign rows describe the approved remaining slice.

| Basis | Integration contract |
| --- | --- |
| [entities.py](../../server/app/models/entities.py), `Task`, `Membership`, `ApiToken`; [documents.py](../../server/app/services/documents.py), `create_task`, `list_tasks`, `upload` | Tasks have a creator, deadline, and `budget_usd`, but no members, owner, lifecycle, or task ACL. `list_tasks` is org-wide after `task:read`. Add membership/lifecycle checks to these existing entry points, not just new board routes. |
| [auth.py](../../server/app/services/auth.py), `Identity`, `authenticate`, `membership`, `ROLE_SCOPES`, `SCOPES`; [api/main.py](../../server/app/api/main.py), `context` | Bearer credentials plus `X-Org-Id` establish an active org membership and transaction context. Task roles must intersect these grants. Org RLS alone does not authorize a particular task. |
| [response_cards.py](../../server/app/services/response_cards.py), `access`, `human`, `extraction_scope`, `CardReadBatch`, `card_view_data`, `card_action`, `dispose_cards` | One primary domain and confirmer; state, disposition, and eligibility are separate. Reuse the validity calculation and explicit extraction scope. New co-sign checks are additional gates. |
| [ADR 0005](../adr/0005-human-confirmed-responses.md) | The approved co-sign amendment permits one professional reviewer per required domain. Slice 3 must extend the database and consumption gates before enabling that policy; slice 1 retains the existing single-domain runtime. |
| [0015_response_cards.py](../../server/migrations/versions/0015_response_cards.py), `response_revision_gate`, `response_evidence_confirmation_complete`, `response_revision_complete`, `response_item_gate`; [0023_screenshots.py](../../server/migrations/versions/0023_screenshots.py), `response_evidence_gate` | Database human/confirmation/completion gates assume single-review confirmation. Later migrations must extend these gates together with Python services, retaining immutable history and direct-SQL rejection. |
| [drafts.py](../../server/app/services/drafts.py), `assemble`, `current_draft_inputs`, `draft_view`; [exports.py](../../server/app/services/exports.py), `build_manifest`, `fresh_manifest`, `release`, `download_gate` | Existing draft (初稿) and export gates inspect fixed inputs and current validity. Bind policy/round/signature manifests here; no board badge authorizes consumption. |
| [prototype_decisions.py](../../server/app/services/prototype_decisions.py), `apply`, `decision_view`, `export_decision_manifest` | Prototype (原型) decisions bind exact card/evidence inputs. Co-sign does not replace keep/replace decisions, nor change their corresponding-domain rule. |
| [tender_jobs.py](../../server/app/services/tender_jobs.py), `submit`; [jobs.py](../../server/app/services/jobs.py), `status`, `cancel`; [processor.py](../../server/app/jobs/processor.py), `Processor.__call__`; [execution.py](../../server/app/jobs/execution.py), `JobExecution` | Jobs persist status, attempts, run ownership and accounting, but provide no task SSE stream. Cancellation does not undo already-dispatched charges. Progress must come from committed records, not simulated percentages. |
| [contracts.py](../../server/app/schemas/contracts.py), `CONTRACT_VERSION`, `Result`, `Cost`; [CLI schema](../../cli/bid_cli/schema.py) | The branch uses runtime Result 4.0 and its shared accounting Cost. Workflow reads and writes import it without reproducing task-budget accounting. |
| [org-console.md](org-console.md), [api.js](../../web/src/api.js), [router.js](../../web/src/router.js), [JobPanel.vue](../../web/src/components/JobPanel.vue) | Console currently polls jobs and derives review responsibility from org role. Add real task ownership and SSE; retain separate platform/org sessions and authenticated previews. |

The [design](../design.md#multi-tenancy-and-permissions) promises that a read-only
reviewer can comment; the human-only `card:comment` grant supplies that discussion
entry to active task readers, including org `viewer` observers. The design also describes members,
archival, and SSE without their persistence/authorization contracts. Its sample
Result lacks `ocr_pages`, which exists in runtime Cost. Only `commercial` (商务)
and `technical` (技术) are runtime ReviewDomain values; qualification (资格) maps
to commercial, while unclassified scoring/substantive clauses still need the
existing administrator classification. Do not invent additional domain literals.
The `export_decision_manifest` docstring still says no export routes are wired;
`exports.prototype_gate` and the export image migration actually consume it.
Follow that live call chain, not the stale comment.

## Vertical slices

| Slice | End-to-end outcome and exit condition |
| --- | --- |
| 1 — membership, archive, board, progress | Create task with one owner; assign existing org members; enforce task ACL on every related read/write/download/job; show a bounded read board and deterministic next actions; SSE resumes without holes with polling fallback; archive an idle task read-only. Acceptance must include old-route bypass attempts, two orgs, two same-org tasks, and browser refresh/reconnect. No assignment/comment/co-sign writes yet. |
| 2 — assignment and discussion | Assign requirement/card work to an active task contributor, discuss through card threads, and mention current task members. Assignment is metadata, preserving all existing card transitions. Acceptance includes duplicate request recovery, removal/reassignment races, and viewer comments. |
| 3 — co-sign | Explicit or rule-required domain approvals, partial status, invalidation, and draft/export enforcement, including comply-only (须遵守). The ADR decision is approved; implementation requires simultaneous service/database/manifest gates. No partially enabled co-sign UI before gates pass. |

No new vendor Provider is needed. The contract's PostgreSQL read-provider and
service Protocols separate projection, authorization, and event persistence. All
HTTP, local CLI, remote CLI, and browser paths use the same services. Later slices
must not create placeholder production handlers in slice 1.

## Membership, ownership, and authorization

Authorization is **active User + active Org + active Membership + current org-role
grant + task role + object relationship + existing domain/human gate**. A scoped
token additionally intersects its saved scopes with the issuer's current grants
and task membership. Workers retain the actual initiating identity, task and
run/lease; constructing a synthetic owner or human Identity is forbidden.

| Task role | Capability within existing org grants |
| --- | --- |
| `owner` | One active human, org `admin` or `bidder`; manage members, hand over, archive, assign work, and perform contributor operations. Ownership grants no extra review domain or export permission. |
| `contributor` | Edit/create cards and run task operations when the existing scope permits; comment; review only assigned domains supported by current org role. |
| `reviewer` | Read, comment, and make existing domain-authorized human decisions, including reopen and disposition; no general card drafting/job submission. |
| `observer` | Read and, for human sessions, comment. No task mutations or review decisions. |

One role per membership. `review_domains` is a unique subset of existing
ReviewDomain: bidder permits commercial, technical permits technical, admin/viewer
permit none. Reviewers require at least one domain; observers have none. Owner
and contributor may have domains within that ceiling. A removed/deactivated
membership remains in history but grants nothing. Assignment alone grants no
scope or review domain. One card has one primary domain for table placement,
regardless of how many domains must co-sign.

Human org admins can read/recover any task in their org and manage its members,
even without task membership. This is an explicit management exception, not a
token/worker exception; nonmanagement task work still requires task membership.
An admin cannot confirm technical/commercial cards. The last active owner cannot
be removed/demoted; hand-over atomically installs an eligible active org member
as task owner and changes the former owner's role. Disabling the owner's org
membership immediately denies access; an active org admin can recover ownership.
No automatic successor is selected. Archived tasks must be unarchived before
membership/ownership edits, apart from external org account revocation.

New task creation seeds the creator as owner in the same transaction, with
commercial review for a bidder and no review domain for an admin. Hand-over
derives the new owner's domains by the same rule; it cannot assign arbitrary
authority. A token
with existing `task:create` may create a task only for its active eligible human
issuer, who becomes owner; the token itself never becomes a member. No request
may name an arbitrary creator. Membership changes use the workflow revision;
card assignment and review rounds have separate revisions, so comments or
assignment cannot manufacture a content revision conflict or approval.

| Operation | Required scopes and additional checks |
| --- | --- |
| Workflow/member list and board | Existing `task:read`; board also `card:read`; current task read access. Directory for adding members is owner/admin only, minimal display label, user ID, role, active flag; never account secrets. |
| Member add/change/remove, hand-over | New human-only `task:members:write`; current owner or human org admin; active task; expected workflow revision. |
| Archive/unarchive | New human-only `task:archive`; current owner or human org admin. Unarchive is the sole business mutation allowed on an archived task. |
| Assignment/reassignment | New human-only `card:assign`; owner/admin; target active owner/contributor with `card:write`; null unassigns. Reviewers remain independently responsible for their domains. |
| Thread/reply/mention | New human-only `card:comment`, all org human roles; current active task member and active task. The admin recovery/read exception alone does not authorize commenting. Comment is never review approval. |
| Co-sign policy/rule edit | New human-only `task:review-policy`; owner/admin; active task, reason and expected policy/workflow revision. Cannot remove the primary domain or override rule-required co-sign. |
| Response sign-off | Existing `evidence:confirm` plus new human-only `card:cosign`; task reviewer/contributor/owner domain, org domain, active task, exact round and inputs. `human` is extended with explicit required-domain validation, never a cross-domain admin bypass. |
| Existing human decisions/export | Existing scopes and corresponding-domain gates still apply. `export` remains human bidder only; membership/ownership does not give technical/admin an export grant. |
| SSE/event polling | `task:read`, `card:read`, `job:read` plus task access; individual job visibility follows `jobs.status` kind-specific access. No token entitlement to human-only export job details. |

Put only grantable automation scopes in `auth.SCOPES`. All six new write scopes
above (`task:members:write`, `task:archive`, `card:assign`, `card:comment`,
`task:review-policy`, `card:cosign`) stay out of it and enter token-table CHECK
constraints alongside every existing human-only exclusion, `evidence:confirm`,
and `export`. Human role grants alone do not authorize a token. Update issuance,
authentication/current-scope intersection, service checks, and direct-SQL gates.
In `ROLE_SCOPES`, grant members/archive/assign/review-policy for admin and bidder,
comment for all four human roles, and co-sign for bidder and technical. Each scope
still requires its task-role and actor-kind checks from the table.

Task lookup must precede object-specific disclosure. Missing, other-org, and
inaccessible-task IDs return identical 404 results. Authenticated task readers
lacking an action receive 403; invalid credentials receive 401. Same-org wrong
task/card/extraction/parent bindings also return 404. Proposed integration includes
`documents.require_document/upload/list_tasks`, tender parse/extract and citation
repair, `response_cards.access/require_card/extraction_scope`, resource-selection
mutations, confidential settings, screenshots/sandbox/prototypes, check/score,
draft/export, job status/cancel/retry and all related history, preview, and signed
download paths. Derive task from the stored parent; never trust a path task ID
without matching it. Org resource CRUD keeps its org policy, but task references
must not reveal inaccessible tasks. Shared library mutations must still invalidate
affected task projections without giving the caller those task IDs.

## Archival and concurrency

Archive sets `active → archived` under the task/workflow lock. It fails with
`task_busy` if any task job is queued/running or any vendor call is pending/unknown,
including calls attached to failed/cancelled jobs. It neither cancels jobs nor
forgives charges. The owner explicitly finishes/cancels/reconciles work and retries.
Unarchive increments workflow/access revisions and records the reason; it does
not restore expired grants, discarded signatures, or obsolete drafts.

| Archived task operation | Contract |
| --- | --- |
| Read board, documents, cards/history, comments, signatures, jobs, audit activity | Allowed under current access and existing content permissions. SSE may observe lifecycle/security/derived-validity changes. |
| Download an already released export or existing material | Allowed only through existing authorization, signed-link and freshness gates. Archival is not an override for an invalidated export. No new render/preview conversion jobs; existing stored previews are readable. |
| Upload, edit, assign, comment, mention, sign, classify, disposition, reopen, resource selection, settings, draft/export generation/release, job submission/retry | Reject `task_archived`; includes tokens, internal agents, workers and future project-memory writes. Pure read-only previews may run only if they create no artifacts, jobs, or other task business state. |
| External org/account revocation, security audit, required accounting settlement | Continue. These are administrative/accounting facts, not task-content edits. A late vendor receipt still settles exactly once in `JobExecution._complete_once`. |

The implementation must normalize lock ordering across old and new paths:
task/workflow first, jobs in UUID order, cards/rounds and related business rows,
accounting locks where needed, event head last. Review the existing job-first
paths before adding locks. Never hold locks or an open database transaction while
sending network data or calling a Provider. Submission, retry, worker claim,
before-call admission, publication, and archive must share lifecycle checks.
Check run ownership/lease and initiating grants again before publication. Events
and audits reflect failed/cancelled/stale attempts without publishing their results.

## Data model and migration outline

The following are approved **org business tables**, each with UUID `id`,
`org_id NOT NULL`, `UNIQUE(org_id,id)`, ENABLE and **FORCE ROW LEVEL SECURITY**,
USING/WITH CHECK against transaction `app.current_org`, and no access with missing
context. All task references are `(org_id,task_id) → tasks(org_id,id)`. Every
person reference uses `(org_id,user_id) → memberships(org_id,user_id)`, including
authors, assignees and signers; global User is not a substitute. Activity/status
and parent binding also receive service/trigger checks. Org RLS is independent
of application task authorization; runtime roles have no BYPASSRLS privileges.

| Table | Fields, relationships, and constraints |
| --- | --- |
| `task_workflows` | Unique `(org_id,task_id)`; owner user, `state`, `revision`, `access_epoch`, `co_sign_starred`, rule revision, archive actor/time. Deferred FK `(org_id,task_id,owner_user_id)` to task member; deferred constraint ensures exactly one owner. |
| `task_members` | Unique `(org_id,task_id,user_id)`; role, review_domains, active, revision, actor/time. Partial unique owner per task; workflow consistency ensures at least one stored owner. Effective access additionally requires active Membership/User/Org. No hard deletion of history references. |
| `requirement_workflows` | Unique `(org_id,task_id,extraction_job_id,requirement_id)`; nullable assigned user, assignment revision starting at 0, explicit `co_sign_required`, policy revision, current round pointer. Requirement/extraction must match existing saved requirement. Task-member composite FK for assignee; absent row means unassigned/default policy, not missing requirement. |
| `card_comment_threads` | `(org_id,task_id,card_id)` composite FK; creator, created time; first message created atomically. No thread on a missing card. |
| `card_comments` | Thread/card/task composite FK, author, encrypted plain-text body, body hash, timestamp, client request ID; unique `(org_id,task_id,author_user_id,client_request_id)`. Append-only, no edit/delete in this scope. Idempotency compares hash and mentions; mismatch is a conflict. |
| `card_comment_mentions` | Unique `(org_id,comment_id,user_id)`; thread/card/task chain and task-member FK. No external address or notification token. Inactive recipients remain historical but receive no content. |
| `card_review_rounds` | Append-only snapshot of task/requirement/extraction/card, reviewed card revision ID, round number, purpose `response` or `disposition`, intended disposition, required domains, rule/policy revisions, citation/material/content hashes, authorization epoch, actor/time. Unique card/round number. Completion/state derive from signatures/final card revision; retirement records invalidate rather than rewriting signatures. |
| `card_review_signatures` | Round/task/card composite FK, domain, signer membership, reviewed evidence IDs and warning codes, encrypted handling reason/hash, request ID and time; unique round/domain, unique round/signer, idempotency key per task/signer. No token signer; one person cannot occupy two required domains. |
| `card_review_invalidations` | Append-only round reference, fixed cause, actor or system source ID, timestamp; unique round/cause/source deduplicates retries. Current validity also recalculates from dependencies if no materialized invalidation has run. |
| `task_event_heads` | Unique `(org_id,task_id)`; transactionally incremented `last_seq`, retained-floor sequence. Separate lock row from workflow/task revisions; no nontransactional database sequence as replay order. |
| `task_events` | Unique `(org_id,task_id,seq)`; event kind, bounded typed metadata, source correlation ID, time. Immutable until bounded retention cleanup; no content, bodies, source quotes, URLs or signed links. |

Add parent UNIQUE keys needed for `(org_id,task_id,id)` and
`(org_id,task_id,extraction_job_id,id)` references to existing cards, revisions,
requirements and jobs. Nullable alternatives require CHECKs, never unconstrained
polymorphic IDs. Signature evidence IDs must be validated against the pinned round
in both service and DB gate; a JSON array is not a referential-integrity guarantee.
Actor/input fields are server-derived. Inputs use `Contract(extra='forbid')` and
reuse existing `Category`, `CardState`, `ReviewDomain`, `CardAction`, and Result.
New tables' audit history goes into existing `audit_logs`, not another general log.

Indexes cover membership lookup `(org_id,user_id,task_id)`; requirement scope and
assignment; card state/current revision; comments `(org_id,card_id,created_at,id)`;
mentions `(org_id,user_id,comment_id)`; round/signature/invalidation lookups; events
`(org_id,task_id,seq)`; and jobs `(org_id,task_id,status,id)`. SQL views, if used,
are PostgreSQL security-invoker views with no owner/RLS bypass. No materialized
duplicate of response text or card state is introduced for the board.

Migration sequence after approval:

1. Preflight existing Membership/Task parent keys and creator eligibility; report
   orphaned/deactivated owners. Seed each new task with its creator; migrate old
   tasks with a reviewed owner map and explicit member list. Do not infer team
   access from historic activity or silently add the entire org. Block cutover for
   unresolved tasks; never deploy an empty member list that strands existing work.
2. Add tables/keys/indexes, org policies, immutability and human-state triggers;
   populate workflow/member/event heads. Restrict runtime grants until isolation
   checks pass. Drain old binaries/jobs during permission/event-producer cutover;
   old workers must not bypass archive/member checks or write untracked changes.
3. Wire every affected old route/service/worker and transactional producer in
   slice 1. Introduce task ACL as an announced authorization change; CLI shapes
   stay compatible, but prior org-wide task visibility intentionally narrows.
4. Add assignment/comments in slice 2. Add co-sign tables/gates/manifests and token
   exclusions in slice 3. Default existing cards to the single primary-domain
   policy, retaining real historical decisions. Never fabricate another signature.
   Enabling stricter policy retires approval eligibility and requires new review.
5. Keep archived content, decisions and audit history during rollback; disable
   new writes and repair forward. Do not downgrade to a binary that bypasses task
   ACL/co-sign while serving traffic. No destructive down migration is proposed.

## Board read contract and bounded queries

`GET /tasks/{T}/board` requires `extraction_job_id=J`. Reuse the task/job/document
binding checks in `extraction_scope` for the selected successful extraction.
That existing helper rejects empty requirements; the read-only board
validator permits a zero-row successful extraction with explicit extraction
warnings, without changing submit/review/assembly validation. The task shell shows job progress
and an explicit extraction selector before a successful extraction exists; a
missing selection returns `extraction_required`, not a fabricated empty board.
`GET /tasks/{T}/progress` supplies that pre-extraction job snapshot with the same
workflow authorization and snapshot/event cursor. It projects all visible job
kinds, unlike the current parse-only `org_console.task_job_list`: default/max 20
per page, active jobs first then created time/UUID, a continuation cursor when
needed, no hidden job counts. Status changes between pages require refreshing
the snapshot. It needs no extraction selection and starts no jobs.
One row per saved requirement, including missing cards. Never select latest or
merge extraction jobs. Re-extraction starts a separate board scope; assignments,
comments and approvals remain attached to their original objects.

Board data contains workflow/owner, selected extraction, `as_of`, replay cursor,
six bucket counts, total/matching/page counts, next cursor, and at most 20 visible
job summaries/recent activities. Result.items contains requirement rows: IDs,
bounded requirement label, category/starred, stored state, computed eligibility,
primary review domain, assignee, required/signed/pending domains, blocker codes,
and deterministic next actions. Exact source/response/evidence content loads in
the authorized side panel through existing `CardView`/requirement reads; a label
is not a replacement for the verifiable tender-document (招标文件) citation.

| Bucket | Deterministic rule, in precedence order |
| --- | --- |
| `gap` (缺口) | Missing card, unclassified domain, rejected card, invalid citation, changed quote, stale material, or invalidated approval. A previously confirmed card that fails validity belongs here, not in confirmed. |
| `comply_only` | Existing human disposition is currently eligible and any required disposition co-sign is complete. Suggestions never count. |
| `needs_material` | Stored `needs_material`, or current draft's `review_hint=needs_material`; show which is human decision versus guidance. |
| `pending_review` | Pending response review, partial response co-sign, or an open comply-only decision round. Partial sign-off never appears confirmed. |
| `confirmed` | Current `eligible` response plus complete current required-domain approval. This does not mean final-export ready. |
| `draft_card` | Remaining draft cards; never null-card placeholders. |

Blockers are orthogonal to buckets: unconfirmed evidence, inactive/expired
certificate (证书), unresolved prototype decision/replacement, missing reviewer,
unassigned work, archived task, co-sign pending/invalidated, current job failure,
and actual admission-blocker codes. Certificate date status is evaluated at
`as_of` UTC date and labeled as an advisory dashboard date, not a promise of
procurement-date validity; check/export retain their own assessment dates and
warning handling. Expiry is not invented as a new unconditional rejection rule.
Prototype undecided blocks a final section (正式件), not independently a review
copy (审阅件). A task may have confirmed rows with either of these warnings.

Budget displays `not_enforced` for legacy task `budget_usd`; never infer remaining
budget by subtracting a USD estimate from prepaid charges. Existing job errors
such as `insufficient_balance` or charge/call caps provide actionable blocker
codes. Do not disclose org balances to nonadmins. Once budget interfaces merge,
consume their typed task status/admission blocker without reproducing accounting.

Filters: bucket, category, starred, assignee (including unassigned), primary/required
review domain, blocker, and `mine`. `mine` means assigned work or a pending decision
the current human can perform, plus current mentions for discussion navigation;
it is never an access grant. For tokens it means issuer-assigned automation work,
never pending human approval. Counts apply the same filters except bucket, then
return all six buckets; their sum is `total`. `matching` adds the requested bucket,
and `returned` counts this page. Separate labels explain filtered versus full
scope; omitted inaccessible jobs/activities have no visible count or placeholder.

Next actions are code/target/eligible-user/domain tuples, not generated advice:
owner assigns unowned work or finds missing reviewers; assignee supplies material
or edits/submits; eligible domain member reviews; the missing co-signer signs;
authorized bidder checks prototype decisions/export readiness. Archived tasks show
read-only and eligible owner/admin unarchive. Do not automatically write, run jobs,
or claim that one next action completes a bid.

Query bounds are contractual: default 50 rows, maximum 100; keyset order is stable
requirement UUID ascending, not mutable bucket position; up to 5,000 saved
requirements per extraction scope, 20 job/activity items, 20 blockers/next-action
entries per row, and 1 MiB serialized Result. Above the scope/response bound return
`board_limit_exceeded` with no truncated counts. Do not silently omit requirements.
Use grouped SQL for counts and batched dependency loads modeled on `CardReadBatch`,
not `assemble`'s per-item loop or one HTTP request per requirement. Hard timeout is
2 seconds; timeout returns retryable `board_busy`, never partial success. Validate
bounded query count (target at most 20 SQL statements per page, excluding auth)
and EXPLAIN plans at the largest accepted scope during implementation.

Read rows, validity dependencies, counts and event head in one short REPEATABLE
READ snapshot. The cursor is authenticated encrypted, at most 1,024 characters, and binds
org/task/extraction, actor visibility fingerprint, filters, sort key, workflow
access epoch, event watermark and assessment date. It expires after 5 minutes.
If relevant changes occur between pages, return `board_changed` and reload the
first page; do not imply snapshot consistency across unrelated transactions.
Clock-dependent validity uses a refresh deadline at the next UTC day/known expiry,
at most 30 seconds for active clients; no event producer is required to notice
the clock turning. Counts never cache across org/task/identity/epoch.

## Assignment, threads, and mentions

Assignment attaches to a saved requirement/extraction, so a gap can have an owner
before a card exists; `card assign` is CLI vocabulary for the same record. The task
owner is escalation responsibility, not an implicit assignee on every row. Writes
carry `expected_assignment_revision` (0 when absent) and a reason. Target must be
an active task owner/contributor with existing edit permissions. Removing or
demoting an assigned member returns `member_has_assignments`; explicitly unassign
or reassign first. External org deactivation cannot be blocked by task assignments:
the board marks `assignee_unavailable` and the owner resolves it.

Card side-panel threads are chronological append-only messages: 4,000 Unicode
characters and 16 KiB UTF-8 per message, 20 unique mentioned user IDs, 50 messages
per page (maximum 100), cursor bound to org/task/card/thread. Store body encrypted
with existing data-encryption conventions; render escaped plain text, no HTML or
remote embeds. Mention selection uses current task members only; server rechecks
membership at write/read. There is no cross-task directory search. Existing User
has only an email identity, not a display-name field; member/candidate views use
that verified same-org email as a server-derived display label. Expose only labels
for authorized team members (or owner/admin candidate selection), never an
unfiltered global-user search, and keep labels out of events/audits.

Thread/reply writes require a client request UUID. Retry of the identical request
returns the original object; different content with that key returns 409. Audit
contains IDs/hash and mention IDs, not body/reason text. Comments may be posted on
confirmed cards without modifying state, content revision, Evidence, or signatures.
An inline “@member” becomes a notification only through the validated mention ID
list, never text parsing into an arbitrary identity. No notification is sent
externally. SSE emits the card/thread/comment ID so an authorized client can reload.

## Durable task events and SSE

`GET /tasks/{T}/events` returns `text/event-stream`, with authenticated streaming
fetch carrying the existing Bearer and `X-Org-Id` headers. Native EventSource is
not suitable for those headers; credentials never enter query strings, stream IDs,
browser storage URLs, or logs. `GET /tasks/{T}/events/poll` provides the same replay
window in Result form. HTTP errors before streaming use existing Result semantics.

Event names are `board_changed`, `job_progress`, and `access_changed`; data is a
typed envelope with event/org/task ID, opaque cursor, time, and IDs/state/progress
metadata only. No text, quotes, comments, model messages, filenames, amounts,
arbitrary JSON, Job.result, errors with raw content, URLs, or signed download links.
Each serialized data frame is at most 4 KiB. Board invalidation carries up to 100
IDs subject to that byte limit; larger sets use `invalidate_all=true`, with no
truncated list. Job totals are nullable; known completed units cannot exceed total.
Attempts/run IDs distinguish a restarted attempt; unknown progress has no percent.

Events are produced from the **same database transaction** as business state and
audit writes. A single helper, called after business locks and before commit, locks
the independent task_event_heads row, increments `last_seq`, inserts typed events,
and holds that lock until commit. A later transaction cannot publish a higher
sequence before the earlier one commits. Rollback removes its increment/events.
Never use a global sequence/UUID/timestamp/MAX(id) as an assumed commit watermark.
For transactions affecting multiple tasks, acquire task/head locks in UUID order.
No out-of-transaction publish, best-effort in-memory channel, or after-commit-only
outbox write can satisfy delivery.

Producers must cover job submission/cache-relevant updates/retry, queued→running,
committed progress, failure/cancellation/success/lease takeover; requirement
publication/citation repair; card revisions/dispositions; task material changes;
prototype decisions; draft/export validity changes; membership/archive/rules;
assignment/comments/signatures/invalidation. Hook `Processor.__call__`,
`JobExecution` and specialized job processors as well as HTTP services. Use minimal
`board_changed` invalidation when a precise projection change is expensive.
Shared-library updates write `task_events` for every affected task in the origin
transaction when the affected set fits the transaction bound (100 tasks). Above that
bound, fail the mutation with `affected_task_limit` until a separately approved
durable fan-out contract exists; do not silently lose notifications. Reads always
recompute validity, so even an event delay cannot authorize stale output.

No network broker/dependency is required. Poll committed events in short org
transactions (up to 100 rows, ascending seq, at most once per second per task
dispatcher); an optional PostgreSQL NOTIFY is only a wake-up hint. Queue dispatch
failure after a committed job leaves its event/job visible, following existing job
recovery; reconnect does not resubmit paid work. Stream handlers hold no long-lived
transaction/row lock. `api.main.context` cannot simply be retained through an
unbounded streaming response: authenticate the handshake, then open fresh scoped
transactions per replay/auth check and release them before writes to the socket.

SSE `id` is an authenticated encrypted cursor binding org/task, sequence and authorization
fingerprint. Start from the board snapshot's `event_cursor`; a job-only shell first
reads the task progress snapshot. Reconnect sends `Last-Event-ID`. At-least-once
replay accepts duplicate frames and the client deduplicates by immutable event UUID. The raw
sequence/head stays server-side: even gaps between visible sequence numbers can
reveal counts of hidden export/job activity. Signing base64-encoded cursor fields
without encryption is insufficient. Cursors may be re-encrypted on delivery;
event UUIDs stay stable without exposing internal ordering or hidden counts.
No-cursor subscription receives a `reset_required` control asking for a snapshot,
not a misleading claim that historical events were delivered. Controls/heartbeats
contain no business data and do not pretend to be durable mutation events.

Revalidate active identity, token expiry/revocation, current issuer grants, task
membership, and each object/job's visibility before **every emitted batch**, and
at heartbeat (15 seconds) when idle. Fetch only task-scoped rows; apply job-kind
ACL before projecting their IDs. Skip invisible records and advance an opaque
safe checkpoint; do not emit their names, IDs, counts, or action kinds. An admin's
token does not inherit the human recovery exception. Revocation closes the stream
without delivering later data; org switch closes the old stream and clears views.
A network frame already sent before revocation cannot be recalled. Handshake
failures do not confirm task existence. Visibility-fingerprint changes require a
new authorized snapshot, never replay under a stale filter.

Retain events for 7 days, additionally cap at 50,000 events per task, deleting the
oldest prefix and updating the retained floor atomically under the head lock.
Expired/pruned/future or foreign cursors never cause silent skipping: foreign task
returns 404, malformed/future cursor 422 `invalid_event_cursor`, retained-window
loss 409 `event_cursor_expired` (or `reset_required` on an open stream), followed
by a fresh authorized board snapshot. Retention does not delete audit history.
Slow clients exceeding 256 KiB queued bytes or 100 unsent frames disconnect and
resume by cursor; do not accumulate unbounded server/browser memory. Limit to
3 streams per identity/task and 100 per org, returning 429 with Retry-After.

On disconnect, exponential backoff with jitter from 1 to 30 seconds. After repeated
transport failures use event polling/board reload every 5 seconds, backing off to
30 seconds and honoring Retry-After. Job-specific fallback follows current
`jobs.status` gates and stops at terminal; hide high-frequency polling in background
tabs and refetch on visibility return. Even healthy streams refresh clock-derived
validity within 30 seconds. Authentication/authorization failures stop retries and
clear the view; they are not a reason to try another org or anonymous endpoint.

## Co-sign policy, rounds, and consumption gates

Default is the single primary domain. Explicit requirement `co_sign_required=true`
or task rule `co_sign_starred=true` on a starred (★) mandatory clause (★条款)
requires both commercial and technical. The task rule is deterministic and
versioned; model suggestions do not enable/disable it. Effective required domains
always include the classified primary domain. Explicit false cannot weaken a
matching rule. The policy lives on the requirement; its one current card uses the
same policy, preventing disagreement between requirement/card flags. Unclassified
cards cannot open review. Changing a policy/rule on pending/approved items first
retires affected rounds and marks them for review; never counts old signatures
against a different policy. Rule preview must report affected count before save.

A response review round opens on normal submit and freezes the pending card
revision, full requirement/citation and material inputs, policy/rule revisions and
required domains. Partial signatures append independently, leaving `pending_review`
and writing no new Evidence confirmations. Reopen retains historical Evidence
confirmation flags in current code; they do not confer approval for the new card
round, and new Evidence stays unconfirmed until final sign-off. Each signer must independently inspect **all** evidence
IDs and current warnings under existing `CardAction` requirements; selecting a
domain does not let the caller sign as another person. Only the last authorized
human request atomically appends the final signature, rechecks every dependency
and signer grant, confirms Evidence, appends the existing `confirm` card revision,
and audits/emits events. No worker can finalize. `confirmed_by` remains the actual
finalizing human for backward-compatible display; the signature set is authoritative
for multi-domain approval. Do not append a content revision for each partial sign.

Existing single-domain legacy confirmations/dispositions continue to use their
real stored human decision, with `not_required` co-sign status and no fabricated
round/signature. A newly submitted single-domain review uses a real one-domain
round. Enabling multi-domain policy requires fresh review and never upgrades an
old decision into a signature for another domain.

Existing single-domain `card action confirm` remains available and writes its
single-domain round/signature atomically; multi-domain use returns
`cosign_required` directing callers to the explicit sign-off command. Reject,
needs-material, and reopen keep their existing primary-domain authorization.
Another required-domain reviewer may withhold signature and comment; this does not
grant cross-domain rejection. Withdraw/reject/needs-material/edit/new submission,
reopen, citation/material changes, policy changes, or lost signer authorization
retire the complete round. A reopen invalidates **all** signatures, never just the
reopener's. Preserve immutable decisions and reasons for history.

Signer loss includes removal/deactivation, losing the org role/domain or losing
task review-domain permission. This slice 3 rule is stricter than current
`card_view_data`, which does not independently revoke historical confirmation
when a confirmer loses membership. Recompute effective approval on read and gates;
invalidations record the cause but their scheduling is not an authorization gate.
Re-adding a signer never resurrects a retired round. Comments and simple assignment
changes do not invalidate content review; task authority changes increment access
epoch, but invalidate only rounds whose required policy or actual signers cease
to qualify, not every unrelated card in the task.

Comply-only cannot bypass co-sign. Single-domain disposition keeps existing atomic
`dispose_cards` behavior. For a multi-domain requirement, existing disposition
batch rejects the **whole batch** with `cosign_required`. An explicit comply-only
round requires an authorized human in the primary domain, `evidence:confirm` and
`card:cosign`, and may open only on a card in an existing disposition-editable state
(`draft/rejected/needs_material`); create a normal blank card first if absent.
Pending response review must be withdrawn; confirmed response must be reopened.
Until every required domain signs the same reason/policy/requirement snapshot,
the effective disposition is unchanged and the board shows pending review; no
comply-only success count or draft exemption. Final sign-off atomically applies
the existing disposition revision, without confirming Evidence or response text.
Switching back to respond also requires all domains when a co-sign policy applies.
Disposition signatures require reasons, no reviewed Evidence IDs. They cannot be
reused as response-confirmation signatures.

| Consumer | Additional requirement |
| --- | --- |
| `card_view_data` / board | Retain stored CardState; expose round required/signed/pending domains and current validity. Never treat partial as confirmed, or historical signatures as current. |
| `drafts.assemble`, `complete_draft`, `current_draft_inputs`, `draft_view` | An incomplete/invalidated co-sign becomes an explicit gap; no unconfirmed text/evidence enters a response row. Bind policy, round, signature IDs and hashes in the draft manifest and freshness check. Comply-only needs its complete disposition round. Preserve negative deviation (负偏离). |
| `exports.build_manifest`, `fresh_manifest`, worker access, `release`, `download_gate` | Revalidate both original human/evidence gates and current co-sign manifest at preview, admission, worker publication, release and download. Final section blocks gaps; review copy can expose gaps only under existing rules, never include unsigned candidate response text/evidence. |
| Prototype decisions | Continue exact evidence/card revision and corresponding-domain keep/replace gates. Completing co-sign is not a keep decision; final export still blocks undecided/incomplete replacement, while review-copy behavior stays unchanged. |

Slice 3 must extend database gates on revision/signature/evidence/response-item
inserts, not merely add a UI or a JSON flag. A stale `expected_revision` or round
is 409 with no partial write; client refreshes/reviews again. Distinct simultaneous
domain signatures serialize on the round; identical retries return the original
signature; finalization happens once. Counter-signature history is internal human
workflow evidence, not a legally qualified electronic signing service.

## HTTP, CLI, and Result

All ordinary HTTP responses use existing Result, with `data` metadata/one-object
wrappers and page entries in `items`. CLI flags are noninteractive; all commands
support `--json`. Bodies never accept org/actor/confirmer/timestamps/derived grants.
T/J/R/C/H mean task/extraction-job/requirement/card/thread IDs. Every path verifies
the full org/task/parent chain. `--input FILE` uses the named Pydantic input; version
flags are mandatory on revision-sensitive mutations. Append-only replies require
the client request ID instead of a thread version. Archive/unarchive HTTP bodies
are `WorkflowMutation`; their route selects the internal `TaskArchiveSet.state`.
It is not a writable server-state field on an arbitrary mutation.

| HTTP route | CLI command (append `--json`) | Input → data / items |
| --- | --- | --- |
| `GET /tasks/{T}/workflow` | `bid task workflow --task T` | workflow view / [] |
| `GET /tasks/{T}/progress?cursor=&limit=` | `bid task progress --task T [--cursor X --limit 20]` | TaskProgressQuery → TaskProgressData / BoardJobView[] |
| `GET /tasks/{T}/members?cursor=&limit=` | `bid task member list --task T [--cursor X --limit 50]` | page metadata / task member views |
| `GET /tasks/{T}/member-candidates?cursor=&limit=` | `bid task member candidates --task T [--cursor X --limit 50]` | page metadata / minimal active org-member candidates; owner/admin only |
| `PUT /tasks/{T}/members/{U}` | `bid task member set --task T --user U --input FILE` | TaskMemberSet → TaskMemberData (workflow and member) / [] |
| `POST /tasks/{T}/members/{U}/remove` | `bid task member remove --task T --user U --expected-revision N --reason TEXT` | version/reason → workflow view / [] |
| `POST /tasks/{T}/handover` | `bid task handover --task T --to-user U --previous-owner-role ROLE [--previous-domain DOMAIN] --expected-revision N --reason TEXT` | TaskOwnerHandover → workflow view / []; repeat domain flag for the former owner's retained domains |
| `POST /tasks/{T}/archive`, `/unarchive` | `bid task archive\|unarchive --task T --expected-revision N --reason TEXT` | version/reason → workflow view / [] |
| `GET /tasks/{T}/board?extraction_job_id=J&…` | `bid task board --task T --job J [--bucket STATE --category CATEGORY --starred --owner U --unassigned --domain DOMAIN --blocker CODE --mine --cursor X --limit 50]` | board query → board metadata / board rows |
| `GET /tasks/{T}/activity?cursor=&limit=` | `bid task activity --task T [--cursor X --limit 50]` | page metadata / structured activity views |
| `GET /tasks/{T}/events` | no separate long-lived JSON format | Last-Event-ID → bounded SSE frames |
| `GET /tasks/{T}/events/poll?cursor=&limit=&wait_seconds=` | `bid task events --task T [--cursor X --limit 100 --wait-seconds 0]` | replay metadata / event envelopes; wait 0–25 seconds, one Result |
| `PUT /tasks/{T}/requirements/{R}/assignment?extraction_job_id=J` | `bid card assign --task T --requirement R --job J --input FILE` | RequirementAssignmentSet → AssignmentData / [] |
| `GET /cards/{C}/threads?cursor=&limit=` | `bid card thread list --card C [--cursor X --limit 50]` | page metadata / thread views |
| `POST /cards/{C}/threads` | `bid card thread create --card C --input FILE` | first comment input → thread and first comment / [] |
| `GET /cards/{C}/threads/{H}/comments?cursor=&limit=` | `bid card comment list --card C --thread H [--cursor X --limit 50]` | page metadata / comment views |
| `POST /cards/{C}/threads/{H}/comments` | `bid card comment add --card C --thread H --input FILE` | reply input → comment view / [] |
| `GET /tasks/{T}/review-rule` | `bid task review-rule show --task T` | rule view / [] |
| `PUT /tasks/{T}/review-rule` | `bid task review-rule set --task T --input FILE [--dry-run]` | task rule input → rule/affected count / []; dry-run no writes |
| `GET /tasks/{T}/requirements/{R}/review-policy?extraction_job_id=J` | `bid card policy show --task T --requirement R --job J` | effective policy view / [] |
| `PUT /tasks/{T}/requirements/{R}/review-policy?extraction_job_id=J` | `bid card policy set --task T --requirement R --job J --input FILE` | RequirementCoSignPolicySet → CoSignPolicyData / [] |
| `GET /cards/{C}/signoffs?cursor=&limit=` | `bid card signoff list --card C [--cursor X --limit 50]` | current round/summary, page metadata / signature history |
| `POST /cards/{C}/review-rounds` | `bid card review-round open --card C --input FILE` | disposition round input → round view / []; response round uses existing submit |
| `POST /cards/{C}/signoffs` | `bid card signoff add --card C --input FILE` | response-confirm or disposition-sign discriminated input → summary/signature/current card revision / [] |

All paginated reads default 50/max 100 except event replay default/max 100 and task
progress default/max 20; opaque
cursors bind org/task/parent, identity and filters. Member-candidates list contains
active Memberships only and is not a replacement for org member management.
`--owner` names the row's assignee, not the task owner; it is mutually exclusive
with `--unassigned`. Streaming is for the browser/API; CLI `task events` is a bounded
single JSON result suitable for polling, avoiding an incompatible JSON-lines mode.
Local mode uses the same services/PostgreSQL/RLS and bounded replay without
bypassing authentication. Register every command/schema in `bid schema` at
implementation, not by importing the plan contract.

The Result envelope retains seven top-level fields. The following example is conceptual; the authoritative Cost schema is [budget_contracts.py](../../server/app/schemas/budget_contracts.py):

```json
{
  "ok": true,
  "command": "task events",
  "data": {
    "org_id": "00000000-0000-4000-8000-000000000002",
    "task_id": "00000000-0000-4000-8000-000000000001",
    "next_cursor": null,
    "head_cursor": "example-opaque-head",
    "has_more": false,
    "reset_required": {
      "type": "reset_required",
      "reason": "initial_snapshot_required",
      "head_cursor": "example-opaque-head"
    }
  },
  "items": [],
  "warnings": [],
  "cost": {
    "llm_tokens": 0,
    "ocr_pages": 0,
    "usd": 0.0,
    "basis": "zero",
    "charge": "0",
    "billing_currency": "USD",
    "task_amount": "0",
    "unpriced_calls": 0,
    "unresolved_calls": 0
  },
  "duration_ms": 4
}
```

This illustrative no-cursor replay tells the caller to fetch a fresh snapshot.
All new workflow operations make no paid calls: wrapper cost is actual zero, not
accumulated job/task cost. Board job summaries never overwrite that meaning.
Runtime schema version remains 4.0 for additive commands; preserve existing card
Result shapes with separate sign-off data. If an implementation changes an existing
output incompatibly, bump the major version and apply the project's compatibility
policy. Do not copy draft BudgetCost into this contract.

| Exit | Semantics and failure examples |
| --- | --- |
| 0 | Successful read/atomic mutation/partial sign-off recorded. Partial sign-off is a successful action with `status=partial`, not CLI partial failure. A read-only board may contain blockers. |
| 2 | Invalid/missing arguments, invalid cursor, bounds, revision conflict, task archived/busy, member assignments outstanding, invalid transition, co-sign required, stale inputs/policy, expired board/event cursor requiring refresh. |
| 3 | Temporary DB/transport failure, board timeout, stream cap/rate limit; honor Retry-After. Do not replay non-idempotent writes blindly. |
| 4 | Invalid session, action forbidden, missing/inaccessible object, content-integrity failure, or existing hard billing/evidence gate failure. |
| 5 | Reserved for existing job commands' partial output. New workflow writes are atomic and do not report half-applied membership, signature or comment batches. |

Errors use `ok=false`, `data.error={code,message,exit_code}`, empty items and measured duration/cost;
do not echo sensitive input. HTTP validation uses 422/400, conflicts 409,
authentication 401, action denial 403, object masking 404, throttling 429, temporary
failure 503. Existing job commands retain their own response and exit semantics.

## Console pages

Follow [org-console.md](org-console.md#pages-and-roles), existing `style.css`,
Element Plus components, org navigation and server reauthorization. No platform session is
an org session; GET /org/current remains the org-role source, with workflow/member
views adding task authority. Browser buttons do not define permission.

| Browser route | Experience |
| --- | --- |
| `/app/org/tasks/:taskId/board?job=J` | Owner/status at top, explicit extraction selector, six count/filter tabs and paginated rows, blocking reasons and one primary next action per row, recent activity and jobs. On narrow screens use the same filtered list, not six horizontal scrolling lanes. |
| `/app/org/tasks/:taskId/members` | Current members, task role and review domain separately, eligible member picker, owner hand-over and archive actions with concrete impact summary. Show unavailable owner/assignee and blocked hand-over instead of guessing. |
| Board/review card side panel | Reuse original→material→response→human decision order; add assignee, structured blockers, partial domain signature list and separate comments tab. Show signature actor/time/round, disabled action reason and safe retry after conflict. |

Use IDs/nontext filters in URLs and existing org-namespaced current-tab recovery.
Do not persist comment drafts, source quotes, response text, search terms, tokens
in URLs, material bytes or signed links to localStorage/IndexedDB. Warn before
discarding unsaved text. Open only authorized previews on demand; org switch,
logout, membership loss or stream denial clears task content and closes the panel.
Do not auto-select Evidence review checkboxes or co-sign domains, or automatically
replay a stale human decision after 409. Each signer's pending/complete indicator
has text as well as color; keyboard navigation and screen-reader announcements
must not move focus on live updates. SSE invalidates loaded rows/counts, then
coalesces authenticated refetches; it never patches approval based solely on a
client-maintained event count.

## Audit and failure recovery

Use [versioned.py](../../server/app/services/versioned.py) `audit` and existing
AuditLog transaction semantics. Audit task/member added/changed/removed,
owner_handed_over, archived/unarchived, assignment_changed, thread_created,
comment_added, mention_created, review_rule_changed, review_policy_changed,
review_round_opened/invalidated, domain_signed and co-sign completed. Record
org/task/object IDs, authenticated actor kind/user/token where allowed, revision,
domain, before/after enum values, request/correlation ID and reason/body hashes.
Keep body/reason text encrypted in its owning record, never in logs/events.
Successful stream opens/closes are bounded operational metrics, not per-heartbeat
audit inserts. Denials log only nonsensitive codes and identity context.

| Failure | Required behavior |
| --- | --- |
| Same-org foreign member/card/requirement/thread or other-org cursor | Uniform 404; no body, name, counts, activity, notification, or audit-query leakage. |
| Concurrent member/handover/archive/assignment update | Expected revision plus row lock; exactly one commit, other request 409. No zero/two owners or assignment to removed member. |
| Comment or signature response lost after commit | Repeat client request ID with identical input returns original receipt; mismatched content conflicts. |
| Business write/event insertion/audit fails | Roll back the entire transaction. Never report a committed card without its durable event. |
| Disconnect after commit, missed NOTIFY, process restart | Replay durable events; safe duplicates; no paid write resubmission. Expired cursor explicitly resets. |
| Slow/unavailable client, DB timeout, clock-derived expiry | Bounded buffers/queries/backoff and fresh snapshot; never fake progress or stale confirmation eligibility. |
| Archive racing job submission/admission/publication or late billing | Shared locks/checks prevent new task work; accounting settles dispatched calls once. Archived means business read-only, not deletion of liabilities. |
| Policy/material/citation/member change while signing | Current dependency/authority validation invalidates the round, rejects stale sign-off, marks consumption stale; no partial Evidence confirmation. |
| Shared-resource fan-out exceeds 100 tasks | Explicit mutation failure before commit; approved scalable outbox is prerequisite to lifting this bound. |

## Acceptance plan

Implementation tests use synthetic fixtures/fake Providers and real PostgreSQL
RLS under `bid_app`. The main session owns database runtime execution. Do not add
unit tests after implementation; write failure scenarios first and verify them
through API/CLI/browser end-to-end paths. Acceptance artifacts belong under
git-ignored `data/work/team-workflow-acceptance/`, never `docs/`; retain commands,
fixture seed IDs, JUnit/CLI snapshots, redacted browser trace/screenshots and
`result.json` so the scenario can be repeated.

| Area | Required scenarios and evidence |
| --- | --- |
| Every table in the implemented slice | Two orgs A/B; runtime SELECT/INSERT/UPDATE/DELETE against B as A fail, missing org context fails; FORCE RLS and no bypass, org/task composite FK rejection, immutability, actor membership bindings; same-org task T1/T2 additionally tests service ACL. |
| Every new route and CLI command | Parameterized A/B/nonexistent/removed member, wrong task/extraction/card/thread, all roles, token scope intersection, archived/active; identical 404 shape. Include event handshake/poll and directory, activity, round/history reads. |
| Every existing task access family listed above | Old task/list/document/download/preview/card/material/draft/export/job/check/score/sandbox paths cannot bypass ACL or archival; signed links reauthorize; org libraries do not reveal inaccessible task references. |
| Membership and archive | New task atomic owner; migrated owner map; zero/two-owner failures; disabled owner recovery; concurrent remove/assign/handover; task_busy on queued/running and unknown calls; no new calls/publication after archive; late settlement idempotency. |
| Board | Exactly one partition per requirement, missing cards, rejected/stale/partial/comply-only, counts with every filter/mine/domain/assignee, unclassified clauses, zero rows, 5,000-row bound/overflow, SQL budget/query plans, response-byte bound, no N+1. No claim of omitted-extraction completeness. |
| SSE durability and privacy | Hold transaction A before commit while B writes, prove no cursor skips late A; rollback/crash before and after commit, restart/missed wakeup, snapshot/subscription race, duplicate replay, retention/future/foreign cursors, authorization changes/expiry, export-job visibility, byte/queue/rate bounds and fallback polling. Scan frames for content/secrets. |
| Assignment/comments | Thread/card parent mismatch, escaped HTML, body/mention bounds, inactive/cross-task mentions, idempotency conflicts, concurrent member removal, confirmed-card comments do not alter revisions/signatures; no external messages. |
| Co-sign and human gates | Both signing orders and concurrent last signature; exact review/warnings, distinct human domains, admin/token/worker denials, DB direct-write bypass, no Evidence confirmation after first sign; reopen invalidates all; all dependency/role changes; single-domain compatibility; multi-domain comply-only and mixed atomic batches cannot bypass. |
| Draft/export | Partial/invalidated signatures become gaps; unconfirmed Evidence never reaches draft/export; current policy/round hashes invalidate old manifests; review-copy gap behavior, final prototype keep/replace, stale released download gates and negative deviations remain intact. |
| CLI contract | Snapshot success, empty lists, every new command, invalid input, 404, 409, 429/503, both modes, schema discovery, exactly seven Result keys/current Cost; bounded `task events` produces one JSON document; existing job exit 5 remains unchanged. |
| Browser e2e | Owner creates/adds member, separate authorized member sees correct next action, second browser observes real job/card changes, refresh/reconnect/expired cursor works, role denial/org switch clears data, archive becomes read-only, assign/comment/mention, both-domain partial/final review, mobile/keyboard flow. Save verifiable redacted artifacts against a real API. |

Run the membership, board/event and assignment/discussion API/PostgreSQL acceptance
suites and the mocked-API Playwright board/member/discussion scenarios.
Ruff, format, pyright and CLI contract checks complement
those suites; they do not establish PostgreSQL isolation or streaming behavior.
The integrating session runs the database acceptance tests when the implementation
workspace cannot reach PostgreSQL. Test artifacts remain outside `docs/`.

## Decisions (已定决定)

| Decision | Approved default | Reason |
| --- | --- | --- |
| Task visibility and org admin access | Explicit members; human org admin read/member-recovery exception | Clear ownership and recoverability without expanding automation permissions. |
| Existing task migration | Reviewed owner/member mapping; creator owner only when eligible; unresolved tasks block cutover | Avoid either silent broad access or stranding existing work. |
| Task roles/domain ceiling | One role plus bounded existing domains; admin has no confirmation domain | Understandable labels while preserving ADR 0005 professional authority. |
| Archive with unfinished work | Reject while queued/running or pending/unknown vendor calls; unarchive explicitly | Simple read-only promise and accurate accounting without hidden cancellation. |
| Org viewer discussion | Human-only comments, all task readers; tokens cannot comment/mention | Matches product design without allowing automation impersonation/spam. |
| Assignee permissions | Owner/admin assigns active owner/contributor; reviewer responsibility remains by domain | Separate preparation from professional sign-off with one clear work owner. |
| Co-sign trigger and scope | Explicit requirement flag or starred-task rule; both existing domains, primary always required | Deterministic and reviewable; no generic workflow engine or model policy decisions. |
| Co-sign ADR and legacy gate integration | Approved amendment of ADR 0005; slice 3 must implement complete-round gates at DB/service/draft/export | Multi-review must not weaken present human/Evidence gates. |
| Comply-only and lost signer authority | Co-sign applies to disposition too; loss of signer grant retires round | Prevent exemption/role-change paths from bypassing review. |
| Signature acknowledgement | Every signer reviews all Evidence/warnings; distinct people | Existing org domain model makes a clear accountability boundary. |
| Board query/stream limits | Bounds defined above; error on overflow; 7-day/50,000-event retention | Predictable resource use, honest counts and recoverable clients. |
| Shared-library fan-out | Fail above 100 affected tasks until a durable fan-out contract is approved | Prevent undocumented event loss or unbounded transactions. |
| New domains, notifications and comment editing | Defer; two domains, in-task mentions, append-only discussion | Focus on ownership/blockers/next action with a small auditable surface. |
| Budget Result dependency | Import current runtime Result/Cost; migrate to shared 4.0 only after budget merges | One authoritative cost shape and no invented budget enforcement. |
