---
kind: plan
---

# Built-in agent command orchestration and human pauses

Status: **Approved. All recommended defaults are adopted.** This contract covers
[roadmap](roadmap.md) A01; invocation provenance (溯源) also serves A02. Approval satisfies
the interface-first requirement in [project rules](../../agent.md#workflow).
The [approved interface models](agent/agent_contracts.py) retain the reviewed
contract; [runtime models](../../server/app/schemas/agent_contracts.py), the
[agent API](../../server/app/api/agent.py), [CLI](../../cli/bid_cli/agent.py) and
[controller](../../server/app/jobs/agent.py) implement it. PostgreSQL/RLS, real
queue recovery and concurrent budget acceptance remain required by
[Acceptance requirements](#acceptance-requirements); static checks and fake
Provider responses do not establish those guarantees. Adopted choices are in
[Decisions](#decisions). Terminology follows the [glossary](../glossary.md).

## Objective and boundaries

The [agent design](../design.md#dashboard-and-agent-design) requires intent
understanding, command dispatch, bounded retries, summaries, server-side recovery
and human intervention before exceeding budgets. The built-in agent (内置 agent),
console and external agents share CLI command requests and Result contracts.
The agent cannot confirm evidence (证据), change confirmed response cards (响应卡)
or export. Execution follows the
[agent tool boundary](sandbox.md#future-built-in-agent-tool-boundary).

The first workflow starts with **an existing task (任务), a real tender document
(招标文件; `Document`) and a successful extraction background job (后台作业)**:
a human submits a goal and limits; the agent reads
that extraction's requirements and cards; it previews and submits `card generate`;
it waits for the generation job; it shows proposals and pauses; the human reviews,
submits and confirms through the existing card entry points; the agent rereads
state and invokes `draft` to assemble confirmed responses (响应); it returns draft
(初稿) references and unresolved gaps (缺口). Proposals remain `draft`. Unconfirmed evidence
cannot enter response tables; a draft with gaps reports partial completion.

This workflow covers discovery, paid decisions, tool child jobs, human gates,
recovery and provenance audit (审计). It does not require the human to declare the entire
task complete first. Existing human card actions and review domains (职责) remain
the authority; an agent approval cannot substitute for human confirmation (人工确认)
of evidence.
Task-budget definitions, preflight, human changes, reservations and invalidation
have one home: the approved and implemented [budget contract](budget.md),
[Task budgets](../notes/task-budgets.md) and [ADR 0007](../adr/0007-task-budget-reservations.md).
A01 consumes those mechanisms and stores opaque intervention references; a
single-job cap or positive prepaid balance (预付余额) cannot replace task admission.
Monthly org (organization/tenant; 单位) quotas remain outside the budget slice and
are not claimed as implemented.

The first slice excludes task creation, upload/parse/re-extraction, automatic
evidence capture (取证), prototypes (原型), check/score, memory (记忆), MCP, cross-task delegation, parallel
tools, an SDK framework, persistent shells, code interpreters and browsers. Each
later tool requires a contract for its readable inputs, side effects, fees,
cancellation and recovery. The first slice does not start the sandbox `agent_tool`
profile. Existing sandbox support does not authorize arbitrary commands.
Instructions inside webpages, PDFs and tool outputs are always data.

## Existing mechanisms and design differences

The following assigns the contract to concrete implementation boundaries. Source
registration is separate from the database and worker acceptance requirements.

| Component | Implementation responsibility |
| --- | --- |
| [schema.py](../../cli/bid_cli/schema.py) `COMMANDS`, `command_schema()`; [main.py](../../cli/bid_cli/main.py) `emit()` | Publish implemented commands only. Preserve separate body `input` and `cli_parameters`; null read input does not mean arbitrary arguments. Complete executable agent invocation schemas; dedicated `output` previously covered only some commands. |
| [contracts.py](../../server/app/schemas/contracts.py) `Contract/Result/Cost/ProviderUsage` | Import directly; do not copy the seven-key envelope. Take the version from `CONTRACT_VERSION`, without fixing a release number here. |
| [auth.py](../../server/app/services/auth.py) `Identity/agent_identity/set_actor_context`; [agents.py](../../server/app/services/agents.py) `human_access/owned/start/resume`; [db.py](../../server/app/core/db.py), [security.py](../../server/app/core/security.py) | Core supplies transaction isolation, signing and encryption. Live Membership grants intersect immutable initial grants and reduced principal scopes; owner authorization and delegation renewal remain separate from token authentication. |
| [jobs.py](../../server/app/services/jobs.py) `status/cancel`; [agent controller](../../server/app/jobs/agent.py) `process_if_agent/fragment/wake`; [execution.py](../../server/app/jobs/execution.py) `JobExecution` | Business jobs retain cancellation, leases, heartbeats and `run_id`. Agent controllers checkpoint between decisions, tools and human pauses; job status/cancel apply session authorization before exposing or changing agent-owned jobs. |
| [entities.py](../../server/app/models/entities.py) `Job.job_document_binding` | Except provider_test, jobs remain task/document bound. Derive a real Document from a successful extraction in the same task; never fabricate one or relax the constraint. |
| [agent contracts](../../server/app/schemas/agent_contracts.py) `AgentReasoningProvider`; [agent adapter](../../server/app/providers/agent.py) `HTTPAgentProvider`; [structured.py](../../server/app/providers/structured.py) `json_request/json_call_with_usage/strict_schema` | The narrow `decide` capability wraps the existing HTTP model adapter and returns a closed decision with trusted usage. It does not add a general SDK, message executor or arbitrary tool protocol. |
| [execution.py](../../server/app/jobs/execution.py) `JobExecution.admit/complete`, `job_cost`; [agent limits](../../server/app/services/agent_limits.py) `enforce/guard_job/cost_view`; [calls.py](../../server/app/providers/calls.py) `accounted_call` | Reuse per-call UsageRecord, prepaid settlement and task admission. Every owned descendant checks parent exposure and deadlines as well as original job/balance gates; fresh controller jobs cannot reset cumulative session ceilings. |
| [response_cards.py](../../server/app/services/response_cards.py), [card_generation.py](../../server/app/services/card_generation.py) `submit_generation/check_input_access`, [drafts.py](../../server/app/services/drafts.py) `submit_draft/show_draft` | Preserve protected-card skipping, revision checks, fixed inputs, cost preflight and confirmed-only assembly. The agent cannot rewrite these rules. |
| [versioned.py](../../server/app/services/versioned.py) `audit()`; [shared provenance](../../server/app/schemas/agent_provenance.py) `AgentProvenance`; [migration](../../server/migrations/versions/0042_builtin_agent.py) `agent_origin_guard` | Preserve immediate actor and immutable invocation origin separately. Database-validated Job/audit columns supply A01/A02 provenance; public card, generation and draft views do not trust client-supplied identity. |
| [Confidential fields (保密字段)](../notes/confidential-values.md), [model input rules](../notes/model-drafting-redaction.md) | Disabling task redaction (遮挡) also disables registered-value replacement. Agent outbound calls require redaction enabled and checked at every admission; the agent never changes the switch. |

The design's `evidence fetch/stamp` names do not map to complete commands of those
names; evidence capture and annotation have staged commands. They and existing
check/score commands are outside this tool set. The design proposes external CLI
agents before an SDK; this approved slice uses the existing worker with a narrow
Provider, without an SDK. Card state alone cannot recover a paid decision or a
child submission in progress; persistent steps are required. Local mode remains
PostgreSQL/RLS and does not adopt a file-only database interpretation of the early
design.

## Interfaces

Every route uses existing authentication, `X-Org-Id` and org transactions. `S/T`
are UUIDs. Bodies cannot supply org, owner, principal, actor, effective scopes,
job/run IDs or other server-owned identity fields.

| HTTP | CLI (all support `--json`) | Input and Result content |
| --- | --- | --- |
| `POST /tasks/{T}/agent-sessions` | `bid agent start --task T --input REQUEST.json [--dry-run]` | `AgentStartRequest`; `data=AgentMutationData`, admission and job receipt only; dry-run uses `AgentPreviewData`. |
| `GET /tasks/{T}/agent-sessions` | `bid agent list --task T [--cursor ID] [--limit N]` | `AgentListRequest` query; `data=AgentPageData`, `items=AgentSessionView[]`. |
| `GET /agent-sessions/{S}` | `bid agent show --id S` | `data=AgentShowData`. |
| `GET /agent-sessions/{S}/messages` | `bid agent messages --id S [--cursor ID] [--limit N]` | Pagination; `items=AgentMessageView[]`. |
| `GET /agent-sessions/{S}/steps` | `bid agent steps --id S [--cursor ID] [--limit N]` | Pagination; `items=AgentStepView[]`. |
| `POST /agent-sessions/{S}/messages` | `bid agent message --id S --input REQUEST.json` | `AgentMessageRequest`; paused only; append a human message without resuming; `data=AgentMutationData`. |
| `POST /agent-sessions/{S}/resume` | `bid agent resume --id S --input REQUEST.json` | `AgentResumeRequest`; validate pause/current facts, then queue; `data=AgentMutationData`. |
| `POST /agent-sessions/{S}/cancel` | `bid agent cancel --id S --input REQUEST.json` | `AgentCancelRequest`; durable session and owned in-flight job cancellation; `data=AgentMutationData`. |
| Existing `GET /jobs/{J}` | `bid job status/wait J` | Controller `data.result=AgentJobResult`; extend authorization for this kind. Child jobs retain their contracts. |
| Existing `POST /jobs/{J}/cancel` | `bid job cancel J` | Controller cancellation equals session cancellation. Cancelling an owned child independently stops session dispatch and reports why. |
| Existing schema entry | `bid schema --json` | Add agent commands and tool invocation structures below. Discovery grants no permissions. |

Messages come from JSON files; there are no interactive terminal questions.
Missing arguments exit 2. New commands do not add long-polling `--wait`. Show
reports session state; job wait waits for one controller segment and does not
establish session completion. Lists sort stably by `(created_at,id)`; a cursor ID
must belong to the same query scope. Cross-org/task/owner and nonexistent cursors
all return 404.

Mutations require `expected_revision` and a caller-generated UUID idempotency
key. Persist the record with the main row. The same principal, endpoint, key and
normalized request replay the original receipt; the same key with changed input
returns 409/exit 2. Check replay before revision, so a successful network replay
does not become a conflict. Initial `task + owner + key` is unique within the org.
Dry-run consumes no key and creates no session, audit, usage, object or queue row.

## CLI tool mapping

Tool names exactly match registered CLI command names; each tool represents one
command. There is no generic `run(command)`. `AgentToolProvider` is a trusted
broker protocol, not a model shell. Arguments pass three checks: the `ToolCall`
command branch, current registry Schema and business service. Local file paths
exist only at CLI input reading; the broker receives a typed body and cannot read
model-selected local files.

| Tool and argument model | CLI / HTTP mapping | Minimum scopes and behavior |
| --- | --- | --- |
| `req list` / `ExtractionArguments` | `--task --job` → `GET /tasks/{T}/requirements?job=J` | `task:read`; fixed session extraction. Never omit job and silently read a newer extraction. |
| `card list` / `ExtractionArguments` | `--task --job` → `GET /tasks/{T}/cards?job=J` | `task:read, card:read`; slots from that extraction. |
| `card show` / `CardShowArguments` | `--id [--history]` → `GET /cards/{C}?history=...` | `task:read, card:read` plus service material access checks; fixed task/extraction only. |
| `card generate` / `CardGenerateArguments` | `task` → `--task`; `input: CardGenerateRequest.extraction_job_id` → `--job`; other flags follow `card_generate()` → `POST /tasks/{T}/cards/generations` | `task:read, card:read, card:generate` and fixed material read grants. Dry-run first, then bind expected_input_hash/max_charge to existing job submission. Outbound work uses the existing Provider; proposals remain unconfirmed. |
| `draft` / `DraftArguments` | `task` → `--task`; `input: DraftRequest` → `--job/--dry-run/--retry` → `POST /tasks/{T}/drafts` | `task:read, card:read, draft:run` and material reads. Existing assembly job, no model charge; gaps may produce a partial draft. |
| `draft show` / `DraftShowArguments` | `--id` → `GET /drafts/{D}` | `task:read, draft:read` plus existing dependency checks; session-linked drafts only. |
| `job status` / `JobStatusArguments` | Positional J → `GET /jobs/{J}` | `job:read` and dynamic kind permissions; session controller/child jobs and fixed extraction only. Exclude arbitrary jobs, export and provider_test. |

Resolve material grants through real `check_input_access`/material resolution;
the three fixed generation scopes alone are insufficient. `AgentScope` permits
only these reading, generation and assembly paths. Existing services still skip
protected, pending, confirmed and human comply-only (须遵守) cards. Models cannot convert
recorded negative deviations (负偏离) to compliance. Revalidate the parent chain of
every task/job/card/draft/requirement ID; a prior response does not make it trusted.
Models cannot control `--wait/--timeout`, credentials, URL, method, retry count or
global CLI settings. Accept `retry=true` only after the broker proves safe retry;
unknown calls are not retried. expected_input_hash must come from this step's
preflight. The broker checks/tightens max_charge against preflight, approved budget
and remaining session amount; model fields cannot authorize or increase spending.

Add backward-compatible `invocation_input` to `command_schema()`: path/query
selectors plus the full existing Pydantic body schema. Preserve
`input/cli_parameters`. The argument models above define that schema; only the
agent profile tightens `req list` to require job explicitly. Console and external
callers discover the same structure. Existing CLI flags map deterministically to
it. Hash the complete schema with Result schema and command names, persisting it
on sessions/steps. An upgrade mismatch pauses for preflight; an old plan cannot
execute a new schema. Verify registry, CLI flags, broker and API consistency in
snapshots instead of maintaining a second handwritten tool definition. Incompatible
changes follow the project's major-version rule.

The broker invokes **the same authenticated command services as API routes**, in
process, returning the same Result and exit code. It does not fork `bid`, construct
arbitrary HTTP or duplicate business algorithms. HTTP/broker submission shares a
transaction boundary so child job and step link commit atomically. Identity, org
transactions, material/card guards, budgets and audit still apply. Model-visible
output is a restricted redacted projection; it is neither the original Result
nor stored evidence.

## Identity, permissions and human gates

The initiator (发起人) uses an authenticated human session to create persistent
`agent_principals` at start,
bound to valid Membership/user/org, authority expiry and a scope snapshot.
`actor_kind=agent`; no reusable user Bearer/Cookie is stored. At each step,
effective permissions are **initial human grants ∩ current Membership/role grants
∩ requested reduced scopes ∩ server A01 allowlist**. Any missing grant rejects the
action. Later role increases cannot expand a session. Even for an owner with the
`admin` or `bidder` role, permanently remove confirmation, export, confidential write/reveal,
provider/token/resource management, redline switches and human disposition.
Scope reduction alone cannot protect human responsibility; retain actor_kind.

The scope intersection also passes the current task membership, task role,
lifecycle and review-domain boundaries in
[Team workflow authorization](team-workflow.md#membership-ownership-and-authorization).
Start, resume and message require active owner/contributor task membership;
the human administrator's recovery exception does not authorize agent work.
Every tool, model admission and child publication rechecks that boundary using
the initiating human's current grants while retaining the agent/worker identity
and execution job/run. Removed membership returns the same inaccessible-resource
response as the human task path. Archived tasks reject new agent work with
`task_archived`, including read tools within an executing session. Loss of access
pauses further work for authority renewal; cleanup, accounting and immutable
history remain possible without publishing business output. Resume repeats the
task checks before resolving the pause or renewing delegation. Cancellation
remains available to the authenticated session owner under the existing human
cancellation rules.

`agent:read/run/cancel` are human-only: `admin`, `bidder` and `technical` can run/cancel;
`viewer` can read. Sessions are owner-only; another org member gets 404, including
an administrator. Run also requires the workflow's business grants; a role alone
does not establish material access. Demotion blocks resume while a valid owner
retains cancellation of their session. Membership removal or org disablement
immediately blocks worker admission; a principal cannot bypass Membership.
API tokens cannot request agent management scopes, create built-in agents or nest
agents. A02 uses existing command-scoped tokens.

API tokens never receive `evidence:confirm`, `export` or
`confidential:write/reveal`; preserve [tokens.create_token](../../server/app/services/tokens.py)
and database constraints. The agent never impersonates its owner with a manually
issued token. Child workers keep immediate actor_kind=worker and immutable agent
provenance. All human-only services/database gates reject agent/token/worker.

Authority expires at the earlier of the initiating session's expiry and eight
hours. Store issuance/expiry metadata, never the original session credential.
Existing signed sessions have no durable revocation ID, so browser logout is not
claimed to immediately revoke delegation. Explicit cancel, expiry and member/org
invalidation block new calls. Expiry creates an authority pause; only a new valid
session of the same user can resume, reducing grants again without resetting
cumulative limits.

Human pauses (人工暂停) live in `agent_pauses`, not terminal prompts. The model may
request `HumanActionNeeded`; server gates may also create pauses. Questions and
results are redacted and bounded. `export/confidential_reveal` only yield fixed
human action instructions, never command execution; full confidential values
never return to conversation/model. Humans act through existing business UI/CLI.
Resume rereads facts and resolves the pause without confirming evidence.
`review_cards` records card/revision IDs. New revisions/inputs refresh the pending
work first; saying “approved” is not confirmation. A human may explicitly finish
review and let the existing draft service report remaining gaps accurately.

## Data model and migration outline

Add six org business tables. **Each has NOT NULL `org_id`, UNIQUE(org_id,id),
ENABLE/FORCE RLS and USING/WITH CHECK policies.** Follow
[Tenant isolation](../notes/tenant-isolation.md), `Database.transaction(org_id)` and
transaction-local `app.current_org`; missing context permits no read/write.
Runtime roles are nonowners without BYPASSRLS/SUPERUSER. Recovery cannot scan
business rows under a cross-org privileged role.

| Table / public view | Content and constraints |
| --- | --- |
| `agent_principals` / `AgentPrincipalView` | Owner user/membership, initial grants/reduced scopes, issuance/expiry/revoked_at. Membership and user must match. Initial grants are immutable; renewal changes only reduced effective scope/expiry with audit. |
| `agent_sessions` / `AgentSessionView` | task/document/extraction/principal, owner, state/revision/limits, step/call/active-time counters, active_since, fixed schema/model/input hashes, current controller job/run/pause. Start and the unique terminal cancel separately retain request hash/key/encrypted receipt. Terminal sessions cannot reopen. |
| `agent_messages` / `AgentMessageView` | session, increasing ordinal, role/author/step, encrypted original, redacted content/hash, key/request hash/receipt. UNIQUE(org_id,session_id,ordinal); append-only; no chain of thought. |
| `agent_steps` / `AgentStepView` | decision/tool, ordinal, stable invocation_id, immutable created_by_job_id/run_id, revision/last_transition_job_id/run_id, fixed invocation/argument hash/input refs/schema hash, child job, state/result hash/exit/usage IDs. Invocation/full receipts are encrypted; public views exclude raw arguments. UNIQUE(org_id,session_id,ordinal), UNIQUE(org_id,invocation_id); terminal steps cannot be overwritten. |
| `agent_pauses` / `AgentPauseView` | Kind, pending objects/input snapshot, redacted question, opaque budget reference, status/resolver/time, resume key/original receipt. At most one pending pause/session; only an authorized human resolves it. |
| `agent_job_links` / `AgentJobLinkView` | session/step/job, controller/tool, owned, created_at. Unique session/job. Partial UNIQUE(org_id,job_id) WHERE owned=true; ownership commits with the new Job. Aggregate all usage for owned jobs. Historical cache references have owned=false and never enter session expenditure. |

All parent chains use org-composite foreign keys: principal → Membership candidate
key `(org_id,membership_id,user_id)`; session → task, Document candidate key
`(org_id,task_id,document_id)`, Job candidate key
`(org_id,task_id,document_id,extraction_job_id)` and same-owner principal;
message/step/pause/link → same-org session. Step/pause references also include
session_id. Link/step child jobs must match org/task/document, never another task.
Add required parent UNIQUE constraints in the migration. User remains global;
Membership proves org affiliation, not a global user FK. Extraction must be
kind=extract and successful. Services/triggers verify kind/state/current revision;
RLS alone does not establish same-task relations within an org.

Cancelling queued/running/waiting_job without a pause still stores its receipt on
the session. Same-key replay returns it; changed input conflicts. Another cancel
key after terminal state returns terminal_session/exit 2 without a fabricated
pause. Start/cancel/message/resume keys are principal/endpoint scoped; do not use
unbounded JSON receipt lists.

Add controller Job kind `agent`, retaining job_document_binding. Deferrable
composite current_job/pause references permit same-transaction initialization.
Do not cascade-delete history. Step/message/pause writes require real actor
context; clients cannot inject assistant/tool roles, change completed steps or
supply resolved_by. Public views use explicit field allowlists, not arbitrary ORM
serialization.

Migration order: parent candidate keys/six tables; RLS/composite constraints,
append-only/state triggers and minimum column grants; job/audit provenance and
human gates; reliable historical audit backfill; per-table two-org/missing-context
verification in the same delivery; then enable entry points. New audit references
also use org-composite FKs. Unknown historical origin is legacy_unknown, never a
guessed agent. Encrypt business text with `core.security.Secrets`, bound to
org/session/step; add columns to [admin.py](../../server/app/admin.py)'s rotation
inventory. No new object-storage format is required. Future attachments still
use `org/{org_id}/` and signed downloads. Rollback disables entry/worker branches
while preserving history/ledger, rather than deleting sessions/audit/cost on
downgrade.

## Worker, recovery and cancellation

The loop runs as an `agent` controller job in the existing Procrastinate worker;
API requests validate, persist and dispatch. A controller runs to one durable
checkpoint, then exits. Waiting for a child/human holds neither a worker slot nor
a DB transaction. Controller success proves checkpoint persistence; the session
holds overall state. Under a live run fence the checkpoint transaction publishes
session/step, marks controller Job succeeded, writes finished_at/AgentJobResult
and clears lease_until plus session.current_job_id/current_run_id. Nonterminal
result.completion=null; disposition is continue/waiting_job/paused. Completed and
partial sessions use complete and partial respectively. A wake creates a new
controller Job; a normally finished segment cannot be rerun as lost running work.

| Transition | Conditions |
| --- | --- |
| queued → running | New/recovered controller; lock session/current_job; Processor obtains fresh run_id/lease. |
| running → waiting_job | Persist step/child/link/session revision in one submission transaction; finish controller normally. |
| waiting_job → queued | Recovery wakes after child terminal state, rereads real Result, completes step and queues next controller. |
| running → paused | Budget/human/authority/uncertain result; atomically checkpoint/pause and release worker. |
| paused → queued | Same human owner resumes after input/permission/budget/remaining-limit validation. |
| running → completed/partial/failed | Goal completed; partial requires independently valid published work plus failure; failure without work is failed. |
| Any nonterminal → cancelled | Persistently fence admission first, then cancel owned in-flight jobs; retain published proposals/cost. |

Each decision and tool execution consumes a step, including failures, dry-runs
and retries; idempotent replay creates no new step. Validate/encrypt the decision
before executing its single tool. Include explicit defaults and hash sorted-key
UTF-8 JSON without extra whitespace with SHA-256; fix raw-byte hash and tool
schema/hash too. The broker creates invocation IDs and binds them to steps;
model-generated UUIDs are not trusted invocation identities.

Non-agent `Queue.enqueue` uses a separate connection and leaves a crash window
between commit/enqueue. A01 uses `Queue.enqueue_in_transaction`: **business Job, agent
step/link, idempotent receipt, submission audit and durable Procrastinate task
record share one PostgreSQL transaction**. Notifications become visible with
commit; failures roll back together. Installed Procrastinate
`Task.configure(connection=...).defer_async` / `JobManager.defer_job_async` accepts
an external connection. Borrow the current SQLAlchemy transaction's underlying
psycopg connection without opening a transaction or committing/closing it.
The adapter is [Queue.transaction_connection/enqueue_in_transaction](../../server/app/jobs/queue.py);
dependency lock: [uv.lock](../../uv.lock). Verify real PostgreSQL connection/rollback behavior;
interface availability is not implementation proof. This does not claim automatic
recovery for all non-agent queue paths. External work is outside transactions;
existing-job hits record reuse. The model cannot execute SQL. Exclude writes
without atomic linkage or queryable idempotent receipts from the tool set.

Every step/session/checkpoint publication and failure transition checks no cancel,
expected revision, current_job_id/current_run_id, live Job lease and step
revision/state CAS, then current permission/input. Preserve created_by_job_id/run_id
forever. A new valid controller claim may advance waiting_job → completed and
update revision/last_transition; the old creating attempt's lease cannot reject
valid recovery. Read the child's run_id from its own Job. Terminal steps are
immutable; transitions/provenance get separate audit. A final session-update fence
alone is insufficient: fence child submission too. Old attempts may settle real
usage but cannot dispatch, publish a successful step or overwrite newer state.
Reuse [background-job](../notes/background-jobs.md) heartbeat/cancellation leases.

Add startup recovery and a cycle of at most 30 seconds, with internal
Procrastinate `bid.agent_wake` tasks as a durable wake chain. Start and every
nonterminal checkpoint save the next wake atomically, containing only org/session
IDs and bounded scheduled_at. Finish the prior wake only after its successor
commits. Recovery can inspect internal queue todo/doing metadata and reschedule
lost wakes. Retain an active session's last wake until business terminal state.
Before-commit crashes roll back; after-commit crashes leave a wake. Discovering
orgs does not depend on a successful later enqueue and creates no cross-org
business exception. With IDs, use `Database.transaction(org_id)` individually to
check/lock queued, expired running, waiting_job and checkpoints. Models cannot
write the queue. Paused wakes only check expiry, without model calls. Recovery
runs throughout the worker lifecycle. DB outages retain records for continuation;
there is no promise of progress within 30 seconds while down. Delays, dispatch
rollbacks and duplicate wakes remain idempotent.

Recovery reads step/linked job first: reuse completed work, reconnect submitted
children, do not take over a live lease, and recover expired jobs only within
existing retry rules without clearing cumulative calls/cost/input. Controllers
do not hold locks while waiting, preventing a parent/child deadlock with one
worker slot. Persisted settled decisions are never regenerated. If an admitted
request lacks a persisted decision, or VendorCall is pending/unknown, mark
step=uncertain, pause for recovery and preserve holds. Do not claim exactly-once
provider HTTP or silently pay again. Verify the ledger/stored results; unprovable
steps terminate and the human opens a new session. A01 grants no automatic
reconciliation or unknown-hold-release authority.

`services/jobs.status/cancel` adds session/current-identity checks for kind=agent
and linked children. Every cancellation path fences parent admission. Automatically
cancel only owned=true jobs; reused historical jobs cannot cancel other work.
Admitted calls may settle within their original provider deadline, then cannot
publish business results. Cancellation is not a refund; do not interrupt
accounting and report zero cost.

## Providers, context and metering

`AgentReasoningProvider` extends `LLMProvider` with
`decide(AgentDecisionRequest) -> AgentDecisionOutput`. Return exactly one
 tool/human_action/complete decision, without chain-of-thought fields or parallel
tools. `AgentToolProvider.definitions/invoke/recover` discovers tools, submits
under authorization and recovers idempotently. `AgentRecoveryProvider.wake` is an
internal scheduling protocol, not a tool. Declaring protocols is not implementation
and does not authorize provider SDK imports in business modules.

Implement under providers; `resolve_llm/with_reasoning` selects/fixes model,
configuration and price revisions. Reuse structured.json_request/json_call,
HTTPExtractor.post and accounted_call. Preserve json_call's wire-result return
contract; add a shared Provider helper returning
`(validated_decision, trusted_provider_usage)`. **Only AgentDecision is the model
output schema.** Build AgentDecisionOutput from trusted HTTP usage; the model
cannot fill ProviderUsage and the wrapper is not the wire schema. Expand provider
strict schemas using concrete selected-tool branches. Do not call strict_schema
on arbitrary dict arguments: it closes objects and destroys open-dictionary
semantics. Tool JSON Schemas are trusted model input; returned AgentDecision is
closed and discriminated. Reject unknown commands, extra arguments and forged
results. Bounded network retries permit at most three real requests, all metered.

Model context contains only session human messages, server-selected current
task/extraction facts, verified result projections, tool schemas and remaining
limits. Before every outbound call replace registered values, then apply
[redaction.py](../../server/app/services/redaction.py). Require redaction on and
fix its revision; disablement/change pauses. Exclude original files, signed URLs,
value suffixes, credentials, unrelated materials, whole job.submission and raw
provider outputs. Store encrypted original messages; public messages are redacted.
Never echo bodies in refusal logs. A message is at most 8,000 characters, a result
projection 64 KiB, a serialized model request 128 KiB. Exceeding bounds pauses to
reduce selection; never silently truncate citations or claim complete reading.
At most 40 messages enter a request; at the boundary pause rather than pay for an
unreviewable automatic summary. Memory retrieval is excluded, so do not invent
“memory used” claims. Human-readable summaries/receipts may persist; hidden
reasoning does not.

Every model HTTP call, including decisions, child generation, retries, refusals,
truncation and post-cancel responses, follows
`HTTPExtractor.post/accounted_call → JobExecution.admit → VendorCall → HTTP → complete → UsageRecord`.
Account before interpreting responses; preserve `(org_id,job_id,run_id,call_id)`
uniqueness. Import ProviderUsage directly. Job execution already settles it;
AgentDecisionOutput.usage describes this call and consumers insert no duplicate
UsageRecord. Reads/assembly without real provider calls create no empty usage rows.

Session cost is **direct decision usage plus owned tool-child usage, deduplicated
by usage ID**. Never copy UsageRecord or debit again. Each controller
AgentJobResult.cost contains only its own cost; session aggregates belong in
AgentSessionView.cost. Adding both duplicates costs. Historical cache hits with
owned=false have zero new session cost; their old job cost remains readable but
excluded. Do not take over another running job; after completion only reference
its result, without cancellation or assigning its bill to two sessions.

Keep provider USD separate from platform currency. Result.cost.usd is provider cost;
AgentCostView holds platform charge, unresolved holds, currency and
unpriced/unresolved counts. BYOK retains provider_config_id, tokens and cost with
zero platform charge; external expense still matters. Unknown price remains null;
reject paid admission without an enforceable upper bound rather than treating it
as zero. Admission, settlement failure and bound overruns follow the
[prepaid ledger](../notes/prepaid-billing.md#admission-and-the-spending-bound).
Do not promise control over a provider ignoring token caps. Retain real overrun cost
and stop subsequent calls.

## Budget pauses and hard limits

[Budget contract](budget.md), [Task budgets](../notes/task-budgets.md) and
[ADR 0007](../adr/0007-task-budget-reservations.md) own task admission, fixed
input/model/price revisions, settled/unsettled exposure and human intervention.
A01 adapts their typed preflight/intervention interfaces; it does not create a
second budget/approval model. BudgetDependencyRef holds opaque question/revision
references, never a spending ticket, credential or approval. Budget increases use
the existing human budget-change entry point; that contract does not imply a
standalone reusable approval token or generic pause/resume service.

Preflight precedes the first paid decision. Each subsequent tool uses its original
dry-run; plan changes, retries, splitting and internal child calls reenter budget
admission. Persist pause and known plan summary before showing intervention via
API/CLI, without sending over-budget calls. An unavailable dependency returns
budget_dependency_unavailable; never calculate an alternative from task.budget_usd.
Existing dry-run bounds cover the first pass, not the entire agent plan.

Humans resolve intervention through budget commands. Resume supplies a reference
and validates current state, principal, plan and revisions server-side. Model text,
stale approval or changed price/input cannot reuse an old decision. Prepaid
insufficiency still rejects; a budget answer cannot bypass debt. Refusal cannot
trigger automatic model switching or splitting to avoid asking. Permanent budget
rejection stops work; independent valid outputs may yield partial completion.

AgentLimits are additional immutable session ceilings, never a task-budget
replacement. Adopt defaults of 24 steps, 32 actual provider calls, 900 active seconds
and a 24-hour lifetime; server maxima are 64 steps, 64 calls and 3,600 active
seconds. Humans explicitly provide max_vendor_usd, max_platform_charge and currency.
A provider USD 2 input suggestion is permitted; platform amount is explicit in the
org currency, without implied exchange conversion. Paid platform work cannot
admit charge=0; BYOK may use zero platform charge.

Counters span controllers, children, attempts, recovery and resume. All paid
descendant admissions check remaining session steps/time/cost/holds and task
budget, then existing job/balance gates; checking only tool submission is
insufficient. Shared admission must follow budget lock ordering before A01 is
enabled. Enforce both provider USD and platform charge; unknown prices block.
A job's batch-expanded call ceiling cannot enlarge its parent session ceiling.

Active time includes queued/running/waiting_job, retry backoff and downtime;
paused human waiting does not count but lifetime still applies. Persist
active_since and accumulate checkpoints with database time; restart cannot turn
lost time into zero. Each HTTP deadline is the minimum of Provider deadline,
remaining active time and lifetime expiry. Post-cancel/timeout settlement may
complete without redispatch. Recovery terminates expired sessions. Reaching a hard
step/time/cost/call ceiling terminates; human approval cannot raise it in that
session. A new session remains subject to the same task budget/outstanding holds.

## Audit and A02 provenance

Add indexable audit_logs actor_kind, initiated_by, agent_principal_id,
agent_session_id, agent_step_id, invocation_id, job_id and run_id while preserving
actor_user_id/actor_token_id. Nullable columns preserve history. New agent events
validate AgentProvenance combinations; all tenant references include org.
Backfill only reliable history, never classify every null token as human.
Audit remains append-only; successful business writes/audit share a transaction.
Failures write separate events, never revise history.

Separate immediate actor from origin. builtin_agent identifies principal/session/
step under current human Membership's reduced authority. A publishing worker has
actor_kind=worker and initiated_by=builtin_agent. A02 gets token ID/owner only
from verified ApiToken authentication and marks external_agent/token; JSON and
self-reported User-Agent cannot supply identity. Initially display token commands
as “token automation” (令牌自动化), without asserting that a model is behind them.
Product-specific names need A02's own contract. A02 creates no A01 session and
uses neither its recovery nor budget-answer entry points.

Use events agent.session.started/resumed/cancelled/completed/failed,
agent.decision.started/completed/uncertain, agent.tool.submitted/completed/failed,
agent.pause.created/resolved, agent.recovery.claimed and agent.limit_reached.
The shared command layer adds command.invoked/completed/failed for token calls,
including reads. Explicit dry-run remains zero-write, including invocation audit.
Events retain commands, object/revision IDs, hashes, refusal reasons and cost
references; exclude conversation/bodies/full arguments/model output/credentials/
signed links/confidential values.

Public card/generation-run/draft views expose readonly AgentProvenance | null.
Human edits retain historical agent origin plus the new human actor; neither
launder origin nor label actual human confirmation as agent confirmation.
Provenance flows from trusted job/step links, not guesses from audit details.
Existing model_job_id/generation-run org relations may resolve records; direct
tool writes save provenance references atomically. UI consumes this contract for
built-in-agent/token-automation markers; Vue pages are outside this slice.

## Result, exit codes and failure modes

Every new command uses Result directly: **ok, command, data, items, warnings,
cost, duration_ms**, without extra status/version/agent top-level fields.
Detail/mutation items=[]; list data contains pagination and items contain public
views. Errors use AgentFailureData with data.error.code/message/retryable and
org-visible linked IDs; never traceback/input. Internal Protocol wrappers are
not CLI envelopes. UUIDs/timing below illustrate format only:

```json
{
  "ok": false,
  "command": "agent resume",
  "data": {
    "error": {
      "code": "budget_confirmation_required",
      "message": "The budget intervention still requires human action",
      "retryable": false
    },
    "session_id": "00000000-0000-0000-0000-000000000001",
    "job_id": null
  },
  "items": [],
  "warnings": [],
  "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
  "duration_ms": 12
}
```

| Exit | HTTP/session meaning |
| --- | --- |
| 0 | Successful preview/admission/read/legal cancel. Normal queued/waiting_job/paused reads explicitly show unfinished state. Controller success means checkpoint only; complete session reads exit 0. |
| 2 | 400/422 invalid input; 409 revision/key conflict, terminal resume or unresolved budget/human intervention. Correct input or handle intervention first. |
| 3 | 429/503 temporary queue/DB outage or bounded Provider transient failure. Preserve session/key and respect attempts/deadlines. Explicit reads of retryable failed sessions also map to 3. |
| 4 | 401 unauthenticated, 403 forbidden action, 404 absent/inaccessible resource; permanent refusal, cancelled run outcome, hard ceiling, unknown usage/accounting failure, invalid protocol or unsafe recovery. No bypass. |
| 5 | Session/child has independently valid published outputs plus a clear failure/gap, ok=false. Waiting for a human or cost without output is not partial success. |

Agent show returns terminal outcome 0/3/4/5 while retaining AgentShowData.
Successful historical list/messages/steps reads exit 0 despite internal failures.
Session cost is session.cost; show's outer cost equals the aggregate. Lists/pure
control requests have zero outer cost. Local/remote semantics match. Private
read failures uniformly return 404 without leaking another session's existence.

| Failure | Required behavior |
| --- | --- |
| Prompt injection, forged commands/IDs, sensitive reads/confirmation/export | Allowlist plus parent-chain/actor rejection; safe action needs become human tasks; do not echo malicious input. |
| Changed card/material/model/schema/redaction revision | Pause/re-preflight; do not expand input or overwrite human confirmation. |
| Lost dispatch, worker restart, duplicate dispatch, old lease returns | Recover persistent step/link with live run fences; never repeat completed steps/charges. |
| Unknown provider outcome or accounting persistence failure | Retain hold, stop for recovery, no automatic resend/zeroing. |
| Missing budget dependency, task cap, prepaid insufficiency | Block admission/pause for human/existing ledger rejection respectively; these gates cannot substitute for one another. |
| Hard ceiling, cancellation, Membership/delegation expiry | Fence new calls; settle admitted calls without publication. Same human may renew authority within remaining ceilings. |
| Provider refusal/invalid JSON/context overflow | Fixed safe error; no arbitrary output persistence. Bounded temporary retries cannot expand input/price/authority. |
| Partial generation/protected cards/draft gaps | Preserve verifiable work and actual gaps; never auto-confirm or conceal failure as completion. |

## Acceptance requirements

Acceptance uses CLI/API → real PostgreSQL/RLS → real queue worker → fake Provider
→ original human entry point → draft. Database-free contract, broker, Provider and
CLI checks cannot establish RLS, transactional dispatch or concurrent settlement.
[Workflow scenarios](../../server/tests/test_agent_workflow.py),
[storage scenarios](../../server/tests/test_agent_storage.py) and
[API scenarios](../../server/tests/test_agent_api.py) provide executable entry
points; the requirements below also include crash and concurrency cases beyond
a successful workflow. Follow [development checks](../guides/development.md#run-the-checks)
for a disposable PostgreSQL environment. Keep reproducible Results, synthetic
inputs and JUnit outside `docs/`.

1. **Every table/two orgs:** A cannot read/insert/update/delete B or operate without
   org context. Cover FORCE RLS, nonowner roles and composite FK cross-org and
   same-org cross-task/session binding. Include audit/job links, append-only/terminal
   triggers, real actors, reduced grants and owner-only access; not SELECT alone.
2. **Every route/two orgs:** every GET/POST above, messages/steps/list/cursors,
   start dry-run/resume/cancel and job status/wait/cancel agent branches treat
   cross-org and missing resources identically as 404. Same-org nonowners also
   get 404; platform identity has no tenant bypass. Queue/model input excludes
   B's synthetic canary. Cover success/errors/lists and revoked permissions.
3. **Human gates:** token requests for evidence:confirm/export/confidential:write/
   reveal fail. Agent principals cannot confirm, reopen/overwrite confirmed/pending/
   comply-only cards, decide prototypes, export or reveal through original APIs,
   database gates, tools or child workers. Original bidder/technical humans retain
   positive confirmation; unconfirmed evidence never enters draft/export.
4. **Full workflow/trusted inputs:** synthetic successful extraction → req/card
   reads → generation preview → fake structured decision → child job → human
   confirmation → draft → cited summary. Registry/schema/arguments/defaults/hashes
   match real APIs. Model/result/URL/ID injection cannot expand tools. Redaction
   changes, original/confidential canaries, overflow, negative deviations and card
   conflicts cannot disappear.
5. **Recovery/concurrency:** inject SIGKILL around checkpoints, transactional
   business/queue commit/rollback, stale run writes, lost heartbeats, concurrent
   resume/cancel, repeated keys, recovery restart, single worker slot and unknown
   VendorCall. Verify normal controller termination, durable wakes for every
   nonterminal session, lost doing-wake takeover and no-pause cancel receipts.
   Steps/usage never duplicate. Once DB service resumes, expiry is handled at the
   next sweep; old attempts publish neither step nor business state.
6. **Budgets/accounting:** integrate implemented budget preflight/admission, with
   bounded substitutes for injected failures. Cover intervention before first
   decision, child retries/splitting, concurrent balance/parent ceilings, resume
   without reset, stale/changed decisions, crash after settlement, historical cache
   cost exclusion, usage deduplication, cancel settlement, unknown BYOK cost and
   hard deadlines applying to children. A budget answer never raises authority.
7. **CLI/audit artifacts:** snapshots for every new command/tool --json/schema,
   local/remote modes and exits 0/2/3/4/5. Pauses are machine-readable with no
   terminal questions. Agent/token provenance spans steps/cards/jobs/audit;
   successful audit is atomic and dry-run writes nothing. Keep synthetic inputs,
   commands, redacted Results, usage/balance IDs, checkpoints and JUnit in
   `artifacts/` or an allowed temporary directory, never docs logs/screenshots,
   so the same commands reproduce verification.

CI uses fake Providers/MockTransport, never real providers. Fake ledgers do not
prove RLS or concurrent debit safety. Authorized real model/site evaluations live
in evals with public inputs, fixed model/schema and actual cost, under all budget
and human gates. Approval alone does not establish that these requirements passed.

## Decisions

All recommended defaults are approved. The alternatives remain here to preserve
the reviewed choice and reason; they are not open implementation decisions.

| Decision | Adopted choice | Reason and alternative boundary |
| --- | --- | --- |
| First tool set | Seven tools for an extracted task plus human review/assembly | Reuse generation/confirmation/draft contracts and verify paid orchestration/recovery. Parse/extract/search/screenshots/prototypes need individual later contracts; check/score stay excluded. |
| Loop location | Checkpoint controller jobs in existing worker | Reuse leases/cancellation/metering and release slots for humans/children. No API-request loop or new SDK service/dependency. |
| Session visibility | Initiating owner only | Task-member permissions have no complete sharing entry point; do not implicitly expose conversations to the org. Shared/admin visibility requires a later role contract. |
| Authority duration | Earlier of original session expiry and eight hours; same-human renewal | Store no session secrets; reduce unattended authority. Renewal keeps cumulative limits and cannot promise logout revocation. Independent long-lived delegation is excluded. |
| Default limits | 24 steps / 32 calls / 900 active seconds / 24-hour lifetime; explicit money | Test parent/child and recovery ceilings. Suggest provider USD 2; platform currency amount is separately explicit. Task budget changes cannot increase session hard limits. |
| Uncertain recovery | Pause to verify; terminate and open another session if unprovable | Existing ledger has no automatic provider reconciliation. Lease expiry cannot justify freeing holds or repeating paid work. Automatic uncertain resends are excluded. |
| Outbound redaction/unknown prices | Redaction enabled and enforceable cost bound required | Agent has no confidential-value access; changes pause without rewriting human config. No redaction-off authorization or unbounded BYOK path. |
| A02 marker granularity | Token automation with verified token ID | Existing authentication proves the identity. External-agent registration/product names need A02's own contract; client self-identification is untrusted. |
| Content retention | Encrypted append-only retention, no automatic deletion | Preserve recovery and accounting history. Configurable expiry/deletion requires a later contract for audit/ledger references and recovery. |

The approved scope is these interfaces and the first workflow. Budget policy has
one home in budget.md; do not add a second budget decision table. Other design
goals remain in the roadmap.
