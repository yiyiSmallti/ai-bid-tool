# Built-in agent orchestration

## Problem

A built-in agent turns a human goal into bounded command work without gaining
human confirmation, export or confidential-value access. Decisions, child jobs
and pauses need durable state so recovery cannot duplicate work or erase usage.
The approved scope is in the [agent contract](../plan/agent.md).

## Usage

Start from an existing task and a successful tender extraction, using a human
session. API tokens cannot create or control built-in sessions. Prepare
`AgentStartRequest` with extraction_job_id, message, requested_scopes, immutable
limits and a caller-generated idempotency_key. Read request types through
`bid schema --json` and the [runtime schemas](../../server/app/schemas/agent_contracts.py).

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

Mutations bind expected_revision and endpoint-scoped keys. Replaying identical
input returns the saved receipt; changed input conflicts. Messages may be added
while paused but do not resume or authorize execution. Resolve a human-review
pause through the existing response-card workflow, then resume with the observed
pause ID and revision. A written approval cannot confirm a card. An uncertain
provider outcome requires verification; it has no automatic replay path.

## How it works

A persistent principal binds original human grants, reduced agent scopes and
an authority expiry. Admission intersects those fixed grants with live principal
permissions and task access. The installation human can read and control every
session. Renewal may reduce authority or extend it from a fresh human session;
it never resets cumulative resource limits. Sessions have no user owner or
organization identifier. [ADR 0012](../adr/0012-single-user-installation.md)
defines this installation boundary.

The broker exposes req list, card list, card show, card generate, draft, draft show
and job status. It has no shell, arbitrary HTTP or model-selected file access.
Generation first reads a preflight and fixes the input hash. Proposals remain
unconfirmed; deterministic draft assembly copies only human-confirmed responses.
This first agent workflow retrieves no memory.

Their Result discovery entries also expose command-specific `output`, `items`
and a complete seven-key `result` schema, covering preview, accepted-job,
published, partial and error branches, including CLI `--wait` projections.
`job status` keeps its public common record for all CLI jobs and an `agent_result`
profile limited to extract, card_generate, draft and agent results. The broker
uses that narrower profile; it does not advertise arbitrary job kinds as agent
capabilities. These types reuse the domain response contracts in
[tool_outputs.py](../../server/app/schemas/tool_outputs.py).

Each tool's contract hash includes its command, invocation input and complete
business Result schema. The session's fixed tool-set digest therefore changes
when an output schema changes even if its invocation arguments stay identical.
An existing incompatible session stops with `tool_schema_changed`; it cannot
silently continue under a different output contract. Schema discovery additions
leave the seven-key runtime envelope intact. For model requests,
`decision_payload` shares identical definition graphs in a request-local JSON
Schema catalog and rewrites their references. Discovery and contract hashes
retain the complete standalone schemas. This representation must reconstruct the
original contracts without losing fields or constraints and stay within the
existing provider byte ceiling; larger conversations still pause.

Messages, arguments and receipts are encrypted. Public projections are bounded
and redacted. Model decisions use a closed schema; trusted provider usage comes
from the adapter rather than the model. Hidden reasoning is not persisted.
Controller jobs checkpoint and release their slot while waiting for a child or
human. State, receipts, audit and queue records commit together. Lease, run_id and
revision fences prevent old workers from submitting or publishing work.

Every actual decision or child-model dispatch records provider, model, duration
and resource use through the shared provider boundary. Session totals deduplicate
direct decisions and owned child UsageRecords by ID. Historical cache reuse adds
no new dispatch and preserves the original job's usage. Immutable limits bound
steps, calls and duration; descendants, retries and recovery share these limits.
Cancellation fences new dispatch and publication while retaining sent-call usage
and unresolved outcomes. The [usage-only decision](../adr/0011-remove-billing.md)
defines the Result counts.

Audit distinguishes the immediate worker from built-in-agent provenance. Human
confirmation retains its actual human actor. External token calls use verified
token identity and the token-automation marker. Dry-run creates no writes or calls.

## Pitfalls

- Controller completion is a checkpoint, not proof that the overall task finished.
  Partial completion requires saved work and visible remaining gaps.
- A lost lease cannot prove that provider HTTP was unsent. Unresolved calls block
  unsafe automatic replay; cancellation cannot erase completed resource use.
- Scope grants cannot make human confirmation, export, prototype decisions or
  confidential-value access available to an agent.
- Context overflow, changed fixed inputs or disabled redaction pause explicitly.
  Do not shrink requirements, switch models or truncate citations silently.
- Database-free tests cannot prove database triggers, queue transaction atomicity, lease
  takeover or concurrent call admission. The contract owns those acceptance gates.

## Code

The following symbols own the implementation boundaries; database and worker
acceptance remains defined by the
[contract](../plan/agent.md#acceptance-requirements).

- [Runtime schemas](../../server/app/schemas/agent_contracts.py) and
  [approved interface](../plan/agent/agent_contracts.py): requests, public views,
  discriminated decisions, pauses and limits. Readonly business origin uses
  [AgentProvenance](../../server/app/schemas/agent_provenance.py).
- [Agent models](../../server/app/models/agent.py) and
  [baseline](../../server/migrations/baseline/10_tables.sql): persistent
  installation state, composite relationships, immutable history and database gates.
- [Session services](../../server/app/services/agents.py) `human_access/session_record/start/resume/cancel`:
  human authorization, revisions, human review/limit rechecks and saved receipts.
- [Agent routes](../../server/app/api/agent.py) `create_router`: authenticated
  HTTP management and history endpoints.
- [Tool broker](../../server/app/services/agent_tools.py) and
  [session limits](../../server/app/services/agent_limits.py): allowlisted services,
  parent chains, proven retries and descendant admission ceilings through
  `definitions/invoke/recover/prove_safe_retry` and `enforce/guard_job/cost_view`.
- [Controller](../../server/app/jobs/agent.py) `decision_request/projection/fragment/checkpoint/wake`
  and [queue](../../server/app/jobs/queue.py) `enqueue_in_transaction/enqueue_agent_wake`:
  redacted context, transactional dispatch, checkpoints, durable wakes and lease fences.
- [Decision Provider](../../server/app/providers/agent.py) `HTTPAgentProvider/decision_payload`:
  closed structured decisions, shared output-schema definitions and trusted HTTP
  usage under the pinned model.
- [Generation](../../server/app/services/card_generation.py) `snapshot/prompt_memory/worker`,
  [cards](../../server/app/services/response_cards.py) `access/card_view` and
  [drafts](../../server/app/services/drafts.py) `submit_draft/complete_draft/show_draft`:
  no-memory A01 inputs, principal-preserving worker identity, original human gates
  and public business provenance.
- [CLI registry](../../cli/bid_cli/schema.py) and
  [agent CLI](../../cli/bid_cli/agent.py): shared invocation schemas, management
  commands, and local/remote Result and exit semantics.
