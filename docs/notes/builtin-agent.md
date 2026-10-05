# Built-in agent orchestration

## Problem

A built-in agent (内置 agent) must turn a human goal into bounded command work.
It must not gain authority for human confirmation (人工确认), export, or writing
or revealing confidential field (保密字段) values. Paid decisions, child background
jobs (后台作业) and human pauses (人工暂停) need durable state so a
restart cannot duplicate work, forget cost or treat an uncertain request as free.
The approved boundaries and acceptance requirements are in the
[agent contract](../plan/agent.md); task admission and human budget changes belong
to [Task budgets](task-budgets.md).

## Usage

Start from an existing task (任务) and a successful extraction of a real tender
document (招标文件).
Use a human login session; API tokens cannot create or control built-in sessions.
Prepare a JSON request with extraction_job_id, message, requested_scopes, limits
and a caller-generated idempotency_key, using
`AgentStartRequest` in the [runtime schemas](../../server/app/schemas/agent_contracts.py).
Monetary bounds and billing_currency must be explicit; the
[limit contract](../plan/agent.md#budget-pauses-and-hard-limits) defines the defaults
and maxima. Material read scopes must match the task's actual selected resources.

```sh
bid agent start --task TASK_UUID --input start.json --dry-run --json
bid agent start --task TASK_UUID --input start.json --json
bid agent list --task TASK_UUID --json
bid agent show --id SESSION_UUID --json
bid agent messages --id SESSION_UUID --json
bid agent steps --id SESSION_UUID --json
bid agent message --id SESSION_UUID --input message.json --json
bid agent resume --id SESSION_UUID --input resume.json --json
bid agent cancel --id SESSION_UUID --input cancel.json --json
```

These are the `agent` subcommands in
[agent.py](../../cli/bid_cli/agent.py); `TASK_UUID`, `SESSION_UUID` and request files
refer to real task/session IDs and locally prepared JSON. Inspect requests and
Results through `bid schema --json`, including the tools' `invocation_input`.
Local and remote CLI modes enter the same authenticated command services.

Mutations use expected_revision and distinct endpoint-scoped UUID keys; start
uses its creation key. Replaying the same normalized request returns its saved
receipt; changed input with the same key conflicts. A message can be appended only
while paused and does not resume execution. Read the pause and perform the
required action through the existing human response-card (响应卡) or budget commands, then resume
with the observed pause ID and revision. Resume verifies current facts; a written
“approved” message cannot confirm a card. A controller job reaching succeeded
means one checkpoint completed. Agent show reports the overall session outcome.
For a review pause, inspect `bid card list --task TASK_UUID --job EXTRACTION_UUID
--json` and follow the existing
[human review workflow](../guides/cli.md#review-responses-and-assemble-a-draft).
Confirmation stays with the card's commercial (商务) or technical (技术) review domain (职责).
If review changes its stored revisions, resume can return a refreshed pause;
inspect that response before resuming with the new pause/revision pair.

Budget pauses carry `BudgetDependencyRef`. Resolve the task-budget or prepaid
blocker through [Task budgets](task-budgets.md), then submit the original
question reference and current task-budget revision from `bid task budget show
--task TASK_UUID --json`; `agents.resume` rechecks live preflight. A reference is
not a spending permission. Recovery pauses with an uncertain paid outcome have
no ordinary resume path: retain the existing ledger, reconcile the outcome and
cancel/start a new session if it cannot be proved.

## How it works

A persistent agent principal binds the initiator (发起人), who owns the session,
Membership, org (organization/tenant; 单位), original grants,
reduced scopes and authority expiry. Each admission intersects original grants,
current Membership/role, requested scope reduction and the A01 allowlist, subject
to the [live task boundary](../plan/agent.md#identity-permissions-and-human-gates). Owner
sessions alone can read/control the conversation; other org members receive 404.
No user credential is retained. Renewal by the same human can reduce authority or
extend it within a fresh valid session, but never reset cumulative limits.

The broker exposes exactly req list, card list, card show, card generate, draft,
draft show and job status, with invocation schemas derived from the CLI registry.
It validates arguments and every parent relation, then invokes the same protected
command services as the API. It has no shell, arbitrary HTTP, model-selected file
access or browser. Generation first previews cost and fixes the input hash;
proposals remain unconfirmed. Human review uses the existing card review domains,
then deterministic draft (初稿) table assembly (组表) copies only confirmed responses
(响应) and reports remaining gaps (缺口). A01 does not retrieve memory (记忆),
including in its generation path.

Decision and tool steps retain fixed schemas, inputs, invocation identities,
results and attempt provenance (溯源). Six org tables use FORCE RLS and composite org/
task/session constraints. Original messages, arguments and receipts are encrypted;
public views contain bounded redacted fields. Model inputs first replace registered
confidential values, then apply outbound redaction (遮挡). A changed or disabled setting
pauses. Model decisions use a closed schema; trusted HTTP usage is attached by
the adapter, never supplied by the model. Hidden reasoning is not persisted.

Controller jobs run to a checkpoint, then release their worker slot while a
child or human is pending. Job/step/link/receipt/audit and the durable queue record
commit in one PostgreSQL transaction. A persistent bid.agent_wake chain drives
recovery through normal worker lifecycle and bounded scheduled wakes; queue IDs
identify the org before its business state is read in an org transaction. Live
run_id/lease and revision fences protect both child submission and publication.
Completed steps reuse receipts; submitted children reconnect; live jobs are not
taken over. A retry must uniquely identify an owned job with the current fixed
input hash, a failed/cancelled state or expired lease, and no pending/unknown call.
The original command service resubmits that same job; a new counted step keeps
the job's historical charges and the session's cumulative limits. An admitted
decision without a persisted result or a pending/unknown
VendorCall becomes uncertain and pauses for verification, retaining holds.

Every real decision or child-model request uses the shared admission/settlement
boundary. Task budgets, prepaid availability, original job ceilings and immutable
parent session ceilings all apply to child retries, splitting and recovery.
Session cost aggregates direct decisions and owned child usage IDs; it creates
neither copied UsageRecords nor an extra debit. Reused historical jobs are
nonowned and add zero new session expense. Provider USD and platform currency stay
separate; BYOK's zero platform charge does not erase provider cost. Unknown paid
prices block admission. Cancellation fences dispatch/publication but preserves
settlement of already issued calls and their fees.

Audit (审计) distinguishes the immediate worker from its built-in-agent origin and
retains the verified principal/session/step/invocation/job/run references.
Public provenance survives later human edits while actual human confirmation
keeps its human actor. External token calls use verified token identity and the
“token automation” (令牌自动化) marker; client-supplied identity is untrusted.
Dry-run remains zero-write, including audit. UI markers consume this contract;
Vue agent pages and external-agent product registration are separate scope.

## Pitfalls

- A preview neither reserves money nor guarantees total plan cost. Task-budget
  changes cannot recharge the org, grant permissions or raise session hard limits.
- Waiting for a human is not partial success. Partial completion requires valid
  published work plus real gaps/failures; controller completion is not session
  completion. Job wait watches a segment, not the entire conversation.
- A fresh controller, retry, restart or resume cannot clear time, calls, steps,
  fees or holds. Active time includes queue waits, child waits, backoff and
  downtime; human pause time is excluded but total lifetime still expires.
- A lost lease is not proof that provider HTTP was unsent. Do not release unknown
  holds, regenerate settled decisions or promise provider exactly-once execution.
  Unprovable steps require termination rather than automatic paid replay.
- Scope checks alone do not make a human-only action safe for an agent. Keep
  actor_kind and database gates; confirmations, exports, prototype decisions and
  confidential writes/reveals remain human actions.
- Browser logout has no persistent revocation ID in the existing session design.
  Use explicit cancel; authority expiry and Membership/org invalidation also
  fence new work. Do not claim logout instantly revokes delegation.
- Context overflow or changed fixed inputs pause without silently shrinking
  requirements or adopting a different extraction/model. Do not truncate
  citations, save raw provider responses or fabricate memory retrieval. Negative deviations
  (负偏离), protected cards and draft gaps must remain visible.
- Recovery during a database outage retains durable work but cannot promise
  progress within the sweep interval. Retain active sessions' last wake until
  terminal state; rollback disables execution without deleting accounting history.
- Database-free broker, Provider and CLI checks cannot prove FORCE RLS, composite
  constraints, same-transaction queue rollback, lease takeover or concurrent
  reservation/settlement. The [acceptance requirements](../plan/agent.md#acceptance-requirements)
  require the disposable PostgreSQL workflow and its failure injections, with
  reproducible artifacts outside `docs/`.

## Code

The following symbols own the implementation boundaries; database and worker
acceptance remains defined by the
[contract](../plan/agent.md#acceptance-requirements).

- [Runtime schemas](../../server/app/schemas/agent_contracts.py) and
  [approved interface](../plan/agent/agent_contracts.py): requests, public views,
  discriminated decisions, pauses and limits. Readonly business origin uses
  [AgentProvenance](../../server/app/schemas/agent_provenance.py).
- [Agent models](../../server/app/models/agent.py) and
  [migration](../../server/migrations/versions/0042_builtin_agent.py): persistent
  tenant state, composite relationships, immutable history and database gates.
- [Session services](../../server/app/services/agents.py) `human_access/owned/start/resume/cancel`:
  owner authorization, revisions, human review/budget rechecks and saved receipts.
- [Agent routes](../../server/app/api/agent.py) `create_router`: authenticated
  HTTP management and history endpoints.
- [Tool broker](../../server/app/services/agent_tools.py) and
  [session limits](../../server/app/services/agent_limits.py): allowlisted services,
  parent chains, proven retries and descendant admission ceilings through
  `definitions/invoke/recover/prove_safe_retry` and `enforce/guard_job/cost_view`.
- [Controller](../../server/app/jobs/agent.py) `decision_request/projection/fragment/checkpoint/wake`
  and [queue](../../server/app/jobs/queue.py) `enqueue_in_transaction/enqueue_agent_wake`:
  redacted context, transactional dispatch, checkpoints, durable wakes and lease fences.
- [Decision Provider](../../server/app/providers/agent.py) `HTTPAgentProvider`:
  closed structured decisions and trusted HTTP usage under the pinned model.
- [Generation](../../server/app/services/card_generation.py) `snapshot/prompt_memory/worker`,
  [cards](../../server/app/services/response_cards.py) `access/card_view` and
  [drafts](../../server/app/services/drafts.py) `submit_draft/complete_draft/show_draft`:
  no-memory A01 inputs, principal-preserving worker identity, original human gates
  and public business provenance.
- [Execution](../../server/app/jobs/execution.py),
  [call boundary](../../server/app/providers/calls.py) and
  [budgets](../../server/app/services/budgets.py): reservations, live grants,
  immediate settlement and actual cost preservation.
- [CLI registry](../../cli/bid_cli/schema.py) and
  [agent CLI](../../cli/bid_cli/agent.py): shared invocation schemas, management
  commands, and local/remote Result and exit semantics.
