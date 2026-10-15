---
kind: plan
---

# Built-in agent command orchestration and human pauses

## Objective and boundaries

The built-in agent turns a human goal into bounded CLI command work. It proposes
responses, pauses for human review, and assembles confirmed responses through the
existing draft service. It never confirms evidence, exports bids, reveals
confidential values, fabricates vendor materials, runs a shell or sends arbitrary
HTTP requests. [ADR 0012](../adr/0012-single-user-installation.md) defines the
single-human installation boundary; the
[usage-only decision](../adr/0011-remove-billing.md) defines resource accounting.

The contract uses PostgreSQL, the provider layer and the same Result/exit codes
as other commands. The approved typed interfaces are in
[agent_contracts.py](agent/agent_contracts.py); executable request and response
validation is in [runtime schemas](../../server/app/schemas/agent_contracts.py).

## Interfaces

A human with installation access starts a session from an existing task and a
successful extraction. API tokens cannot create or manage built-in sessions.
Start input contains the extraction job ID, goal message, requested agent scopes,
immutable limits and an idempotency key. Bodies cannot set actor attribution,
effective grants, principal IDs, job/run IDs or session counters.

| CLI | API | Behavior |
| --- | --- | --- |
| `bid agent start --task T --input start.json` | `POST /tasks/T/agent-sessions` | Preflight or start one bounded workflow |
| `bid agent list --task T` | `GET /tasks/T/agent-sessions` | List installation sessions for this task |
| `bid agent show --id S` | `GET /agent-sessions/S` | Current state, limits, cost and pause |
| `bid agent messages --id S` | `GET /agent-sessions/S/messages` | Redacted ordered messages |
| `bid agent steps --id S` | `GET /agent-sessions/S/steps` | Bounded execution history |
| `bid agent message --id S --input message.json` | `POST /agent-sessions/S/messages` | Append clarification while paused |
| `bid agent resume --id S --input resume.json` | `POST /agent-sessions/S/resume` | Resolve the observed pause after rechecking facts |
| `bid agent cancel --id S --input cancel.json` | `POST /agent-sessions/S/cancel` | Fence new dispatch and publication |

Routes use installation credentials and ordinary database transactions. Missing
objects and cursors outside the exact task/session query return 404. Mutations
require the expected revision and endpoint-scoped idempotency key. Same input/key
returns the saved receipt even after revision changes; a changed request with
that key returns 409/exit 2. Start uniqueness is task plus key. Terminal sessions
cannot reopen. A message does not resume work or confirm a response card.

## CLI tool mapping

The broker exposes `req list`, `card list`, `card show`, `card generate`, `draft`,
`draft show` and `job status` from the CLI registry. Definitions include typed
invocation schemas and the uniform Result schema. Each selector must match the
session task, document, extraction and its linked child jobs. A previously seen
UUID is never sufficient proof of a valid parent. Schema hashes bind both session
and step; changed command definitions require a new preflight.

Generation reads preflight before dispatch and fixes the input hash. Retry requires
one uniquely linked owned job, settled provider calls and an eligible prior state.
A cached successful job is linked with `owned=false`; it is not charged again or
cancelled with the session. Work submission, child linkage, receipt, audit and
queue deferral commit in one transaction. The agent has no arbitrary file, URL,
SQL, shell, policy-edit or memory-retrieval tool.

## Identity, permissions and human gates

A principal stores immutable original grants, reduced scopes, expiry and optional
revocation time. It stores no user, membership or organization identifier. The
installation human can read and control every session; sessions have no owner.
Admission intersects principal scopes, original grants and the agent allowlist.
Expiry, revocation or a changed grant pauses work. Renewal from a fresh human
session can reduce scopes or extend expiry without restoring removed grants or
resetting limits.

A live task must still exist. Agent, worker, token, principal, session, step,
invocation, job and run attribution remain distinct. Human requests keep agent
principal/session transaction settings unset; controller Job rows bind their
principal and session. Delegation cannot grant
human-only scopes even if a caller submits them. Human confirmation records its
human actor kind and decision time. Agents cannot satisfy review by sending text
such as “approved”. Review pauses pin card/revision and input hashes; resume
rereads the actual human decisions and refreshes changed inputs.

## Data model and migration outline

The [agent models](../../server/app/models/agent.py) and
[baseline tables](../../server/migrations/baseline/10_tables.sql) define the
persistent installation state. The runtime role cannot alter tables or disable
triggers. Foreign keys and actor checks retain parent and execution integrity.

| Table | Required boundary |
| --- | --- |
| `agent_principals` | Immutable initial grants; bounded reduced scopes and expiry |
| `agent_sessions` | Task/document/extraction/principal; state, revision, fixed inputs/model/tool hashes; immutable limits and cumulative counters |
| `agent_messages` | Unique session/ordinal; encrypted original, redacted content/hash; append-only complete receipts |
| `agent_steps` | Decision/tool, stable invocation, immutable creating job/run, fenced transition job/run; encrypted arguments/results; terminal immutability |
| `agent_pauses` | At most one pending pause; redacted question and pinned objects; human resolution time and complete resume receipt |
| `agent_job_links` | Unique session/job; same task/document; one owning session per owned job; cached links are nonowned |

Parent chains bind task/document/extraction and session/step without organization
columns. Cancellation retains its receipt on the session even when no pause
exists. Public views use explicit allowlists and exclude original messages,
arguments and hidden reasoning. Encrypted JSON binds row and session IDs; moving
ciphertext to another row or session fails validation. Rotation preserves these
bindings and immutable history.

## Worker recovery and cancellation

A controller performs one durable checkpoint and releases its worker slot while
waiting for a child or human. Controller success means checkpoint persistence;
the session holds overall completion. A checkpoint finishes its Job, clears its
lease and current session job/run, and saves its next durable wake atomically.
Process queue entries carry only the job ID; wake entries carry only the session
ID. Recovery never depends on user ownership or organization discovery.

| Transition | Required condition |
| --- | --- |
| queued → running | Fresh controller job/run and live lease |
| running → waiting_job | Atomic child/step/link/checkpoint publication |
| waiting_job → queued | Linked child terminal; read its real Result |
| running → paused | Human action, authority loss or uncertain outcome |
| paused → queued | Human resume; current inputs/grants/remaining limits |
| running → completed/partial/failed | Real terminal outcome; partial requires valid saved work |
| Nonterminal → cancelled | Fence dispatch/publication; cancel only owned live jobs |

Session and step writes verify current controller job/run, live lease, revision,
state and invocation before every dispatch/publication. New valid controllers
may advance checkpoints made by earlier runs; old controllers cannot overwrite
new state. Settled decisions are reused. Unknown or pending provider outcomes
require explicit recovery rather than blind replay. Cancellation and authority
loss retain actual usage and historical receipts. Database outages retain queue
and checkpoint state for continuation.

## Providers, context and metering

Only provider adapters dispatch model calls. Pin provider, model, adapter version
and local provider configuration; there is no platform model catalog. A closed
decision union admits one allowed tool, human action or terminal response.
Adapter receipts supply usage; model output cannot invent cost or grants.
Redaction is mandatory and pins its rule/revision and confidential-value IDs.
Changed masking, model or context inputs pauses rather than truncating citations,
switching models or silently dropping requirements.

Limits bound total steps, vendor calls, active duration and lifetime. Decisions,
child dispatches, retries and recovery share cumulative limits. Admission reads
the shared VendorCall/UsageRecord ledger under the task/session fence. Cached
historical jobs add no new resource use. Unknown calls block unsafe replay;
late real receipts may settle usage after cancellation but cannot publish work.
The model receives bounded redacted projections; hidden reasoning is not stored.

## Tender source tools

The agent reads the parsed tender of its session through `source search` and
`source read`; the [mechanism note](../notes/builtin-agent.md#how-it-works)
describes the bounds and redaction.

Remaining acceptance: on a real tender, a session asked for the bid deadline
and the document collection period answers with verbatim citations to the
stored chunks; calls, tokens and duration are recorded under `data/work`. It
needs real provider calls and runs as an eval, not in CI.

## Planned: long sessions

A session currently fails with `agent_context_limit` at 40 stored messages.
Replace the count with an estimated token budget per pinned model:

- Keep a reserve for the reply (16,384 tokens) and the most recent messages up
  to about 20,000 tokens verbatim.
- When the budget would be exceeded, the pinned model at its lowest reasoning
  level summarizes the older messages into one stored summary message that
  records the message ordinals it covers. Originals remain stored; the summary
  is redacted like other messages.
- A summary is context only: it never satisfies a human gate, never stands in
  for a citation, and must list open gaps and pending human actions. A summary
  that fails validation pauses the session instead of dropping messages.
- A single tool result longer than its bound is cut with a continuation hint,
  as `source read` does.

Acceptance: a session of more than 60 turns keeps working, its summary retains
open gaps and pending human actions, and its usage is recorded under `data/work`.

## Audit and A02 provenance

Audit distinguishes immediate human/agent/token/worker execution from built-in
or external-agent origin. Public
[AgentProvenance](../../server/app/schemas/agent_provenance.py) carries verified
principal/session/step or token IDs, invocation, command, job and run; it carries
no human user ID. Never infer missing historical command or invocation values.
Human confirmation remains an explicit human decision.

## Acceptance requirements

Exercise the real authenticated API and PostgreSQL worker path for start,
idempotent replay, proposals, human review, resume, confirmed draft and cancel.
Retain reproducible artifacts outside `docs/`. Verify malformed decisions,
forbidden tools/scopes, input substitution, schema changes, revision conflicts,
stale workers, child linkage, queue rollback, expired/revoked principals,
uncertain dispatch, append-only receipts, limits and usage retention. Tokens and
agents must fail human confirmation/export and session management gates.
Database-free provider and broker checks complement these workflow gates.
