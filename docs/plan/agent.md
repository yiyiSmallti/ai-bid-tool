---
kind: plan
---

# Draft contract: built-in agent command orchestration and human pauses

Status: **Pending approval, not implemented.** Corresponds to [roadmap](roadmap.md) A01; invocation provenance also serves A02.

Under [project rules](../../agent.md#workflow), confirm Pydantic, Provider, and CLI JSON contracts before implementation. The [contract models](agent/agent_contracts.py) are review-only, unregistered in runtime API/CLI/worker, and create no tables. All choices here are proposals; unresolved items are in [open decisions](#open-decisions).

## Goals and boundaries

The [design document's agent section](../design.md#dashboard-and-agent-design) requires intent understanding, command dispatch, bounded retries, summaries, server-state recovery, and human approval before exceeding budgets. Built-in agents share CLI requests/Result with consoles/external agents, without evidence (证据) confirmation, confirmed response-card (响应卡) modification, or export authority. Execution boundaries follow [sandbox agent tools](sandbox.md#future-built-in-agent-tool-boundary).

The first flow starts with **an existing task, real tender Document (招标文件), and successful extraction job**: a human supplies goals/limits → agent reads that extraction's requirements/cards → previews/submits `card generate` → waits for existing generation jobs → shows proposals and pauses → a human reviews/submits/confirms through existing card entry points → agent rereads state, calls `draft` to assemble confirmed responses (响应) → returns draft (初稿) references/unresolved gaps (缺口). Generated proposals stay `draft`; unconfirmed evidence never enters response tables. Draft gaps are honestly returned as partial completion.

This covers tool discovery, paid decisions, tool child jobs, human gates, recovery, and provenance audit without requiring the entire task be confirmed complete first. Existing human actions/review domains (职责) remain; agent approval never means evidence confirmation. Task budgets/org allowances, previews, approvals, reservations, and invalidation are defined only by the parallel **contract-budget** draft `docs/plan/budget.md`. That draft exists but remains pending approval and unimplemented; its dependency contract/interfaces must be completed and confirmed before implementation. Per-job caps/positive balances cannot replace it. A01 saves opaque references and displays pending actions only.

Initially excluded: task creation, upload/parsing/reextraction, automatic capture, prototype generation, check/score, memory, MCP, cross-task agents, parallel tools, SDK frameworks, persistent shells, code interpreters, browsers. Later tools require individual contracts for read sets, effects, fees, cancellation/recovery. This slice does not activate sandbox `agent_tool`; sandbox support does not authorize arbitrary commands. Instructions in webpages/PDF/tool output are always data.

## Basis and design differences

| Existing basis | Proposed reuse and required additions |
| --- | --- |
| `COMMANDS`, `command_schema()` in [schema.py](../../cli/bid_cli/schema.py); `emit()` in [main.py](../../cli/bid_cli/main.py) | Registry publishes implemented commands only; request body input and cli_parameters are separate, read input may be null, dedicated output covers only some commands. No complete directly executable agent tool schema exists; fill it below rather than treating null as arbitrary parameters |
| `Contract/Result/Cost/ProviderUsage` in [contracts.py](../../server/app/schemas/contracts.py) | Import directly, no copied seven-key envelope; version from CONTRACT_VERSION, without fixing a current number here |
| `Identity/authenticate/membership/set_actor_context` in [auth.py](../../server/app/services/auth.py); [core/db.py](../../server/app/core/db.py), [core/security.py](../../server/app/core/security.py) | Core supplies transaction isolation/signing/encryption primitives; roles/scopes live in services/auth.py. Existing tokens intersect scopes/current roles; new built-in principals/persistent grants do not already exist |
| `status/cancel` in [jobs.py](../../server/app/services/jobs.py); `Processor` in [processor.py](../../server/app/jobs/processor.py); `JobExecution` in [execution.py](../../server/app/jobs/execution.py) | Retain business jobs/cancellation/leases/heartbeats/run_id. Agent state machine/recovery sweep do not exist; job status/cancel needs session access checks, never a bypass |
| `Job.job_document_binding` in [entities.py](../../server/app/models/entities.py) | task/document required except provider_test; A01 uses a real document from successful same-task extraction, without fake Document/relaxed constraints |
| `LLMProvider` in [base.py](../../server/app/providers/base.py); `HTTPExtractor.post/resolve_llm/with_reasoning` in [llm.py](../../server/app/providers/llm.py); `json_request/json_call/strict_schema` in [structured.py](../../server/app/providers/structured.py) | Existing protocol has only extract/draft; drafting uses structured HTTP. Add decide, without claiming the design's general messages+Schema interface is implemented |
| `JobExecution.admit/complete`, `job_cost` in [execution.py](../../server/app/jobs/execution.py); `accounted_call` in [calls.py](../../server/app/providers/calls.py); `require_funds` in [billing.py](../../server/app/services/billing.py) | Reuse per-call UsageRecord/prepaid settlement. Balance/per-job charge/call ceilings exist; task-budget enforcement is contract-budget. Repeated agent jobs cannot reset cumulative caps |
| [response_cards.py](../../server/app/services/response_cards.py), `submit_generation/check_input_access` in [card_generation.py](../../server/app/services/card_generation.py), `submit_draft/show_draft` in [drafts.py](../../server/app/services/drafts.py) | Reuse protected-card skipping, revision checks, pinned input, cost preview, confirmed-content-only consumption; agents cannot rewrite these rules |
| `audit()` in [versioned.py](../../server/app/services/versioned.py); `AuditLog` in [entities.py](../../server/app/models/entities.py) | Existing user/token/business audits; no unified invocation audit/constrained agent-source columns. A01/A02 share the provenance model below |
| [Confidentiality](../notes/confidential-values.md), [model input rules](../notes/model-drafting-redaction.md) | Disabling task redaction also disables registered-value replacement. A01 forbids that state for agent outbound calls; redaction is required/rechecked at every admission without automatic setting changes |

Design `evidence fetch/stamp` has no complete identically named commands; capture/annotation have staged commands, while check/score now have registered commands. All remain outside the initial tool table. The design starts with external CLI agents, then considers SDKs; this proposal uses existing workers plus narrow Providers without an SDK. Card-state recovery alone cannot restore paid model decisions/submitting child jobs, requiring persistent step records. Local mode remains PostgreSQL/RLS, never the pure-file mode early design wording might imply.

## Interfaces

All routes use existing authentication, `X-Org-Id`, and org (organization/tenant; 单位) transactions. S/T are UUIDs. Bodies accept no server-owned org/owner/principal/actor/effective scopes/job/run IDs. New entry points/models are proposals.

| HTTP | CLI (all support `--json`) | Input and Result content |
| --- | --- | --- |
| `POST /tasks/{T}/agent-sessions` | `bid agent start --task T --input REQUEST.json [--dry-run]` | `AgentStartRequest`; data=AgentMutationData accepts/returns jobs only; dry-run=AgentPreviewData |
| `GET /tasks/{T}/agent-sessions` | `bid agent list --task T [--cursor ID] [--limit N]` | AgentListRequest query; data=AgentPageData, items=AgentSessionView[] |
| `GET /agent-sessions/{S}` | `bid agent show --id S` | data=AgentShowData |
| `GET /agent-sessions/{S}/messages` | `bid agent messages --id S [--cursor ID] [--limit N]` | Pagination; items=AgentMessageView[] |
| `GET /agent-sessions/{S}/steps` | `bid agent steps --id S [--cursor ID] [--limit N]` | Pagination; items=AgentStepView[] |
| `POST /agent-sessions/{S}/messages` | `bid agent message --id S --input REQUEST.json` | AgentMessageRequest; paused only, appends human messages without auto-resume; data=AgentMutationData |
| `POST /agent-sessions/{S}/resume` | `bid agent resume --id S --input REQUEST.json` | AgentResumeRequest; checks pause/current facts then queues; data=AgentMutationData |
| `POST /agent-sessions/{S}/cancel` | `bid agent cancel --id S --input REQUEST.json` | AgentCancelRequest; persistent session/owned in-flight job cancellation; data=AgentMutationData |
| Existing `GET /jobs/{J}` | `bid job status/wait J` | Controller data.result=AgentJobResult, expanded kind access guards; child jobs retain contracts |
| Existing `POST /jobs/{J}/cancel` | `bid job cancel J` | Controller cancellation cancels session; independent owned-child cancellation stops dispatch and reports reason |
| Existing schema | `bid schema --json` | Add agent commands/tool invocation structures below; discovery grants no permissions |

Messages come from JSON files, without terminal questions. Missing arguments exit 2. New commands initially add no long-polling `--wait`; show displays session state, and existing job wait waits only the specified segment, never implying session completion. List cursors stably order `(created_at,id)` and locate IDs within the same query scope. Cross-org/task/other-owner/missing cursors all return 404.

Mutations require expected_revision and caller-generated UUID idempotency keys. Records persist with their parents: same principal/endpoint/key/normalized request replays the original receipt; same key/different arguments yields 409/exit 2. Check idempotency before expected_revision so successful network replays are not falsely conflicts. Initial start `task + owner + key` is org-unique. Dry-run consumes no keys and creates no session/audit/usage/object/queue records.

## CLI tool mapping

Tool names exactly equal registered CLI command names, one command per tool, without generic run(command). AgentToolProvider is a trusted broker protocol, not a model shell interface. Parameters pass ToolCall command branches, current registry Schema, and business service validation. CLI file paths exist only during CLI ingestion; brokers receive typed bodies and cannot read model-selected local files.

| Tool/argument model | Existing CLI / HTTP mapping | Minimum scopes/behavior |
| --- | --- | --- |
| `req list` / `ExtractionArguments` | `--task --job` → `GET /tasks/{T}/requirements?job=J` | task:read; pinned extraction, no omitted job selecting newer extraction |
| `card list` / `ExtractionArguments` | `--task --job` → `GET /tasks/{T}/cards?job=J` | task:read, card:read; extraction's card slots |
| `card show` / `CardShowArguments` | `--id [--history]` → `GET /cards/{C}?history=...` | task:read, card:read plus material access; pinned task/extraction only |
| `card generate` / `CardGenerateArguments` | task→`--task`; `input: CardGenerateRequest` extraction_job_id→`--job`, other fields follow card_generate() flags → `POST /tasks/{T}/cards/generations` | task:read, card:read, card:generate plus pinned-material read grants; dry-run first, submit existing job with expected_input_hash/max_charge; outbound only through existing Provider, proposals always unconfirmed |
| `draft` / `DraftArguments` | task→`--task`, `input: DraftRequest`→`--job/--dry-run/--retry` → `POST /tasks/{T}/drafts` | task:read, card:read, draft:run plus material reads; existing table-assembly (组表) jobs, no model fees, gaps may form partial drafts |
| `draft show` / `DraftShowArguments` | `--id` → `GET /drafts/{D}` | task:read, draft:read plus original dependencies; linked session drafts only |
| `job status` / `JobStatusArguments` | Positional J → `GET /jobs/{J}` | job:read plus dynamic kind permissions; session controller/children/pinned extraction only, no arbitrary jobs/export/provider_test |

Material read grants come from actual check_input_access/material resolution, not just three fixed scopes above. Initial AgentScope allows only needed read/generation/assembly scopes. Existing services still skip protected/pending/confirmed/human comply-only (须遵守) cards; models cannot turn negative deviation (负偏离) into compliance. task/job/card/draft/requirement IDs revalidate parents, never trusted merely because previous responses supplied them. Models cannot control --wait/--timeout/authentication/URL/method/retry count/global CLI configuration. retry=true requires broker verification of safe original-job retry; unknown calls are not retried. Generation expected_input_hash comes from that step's preview; brokers validate/tighten max_charge against previews/approved budgets/session remainder. Model fields are not authorization and cannot increase approved amounts.

Implementation adds backward-compatible `invocation_input` to command_schema(): full JSON Schema for path/query selectors plus existing Pydantic bodies, retaining input/cli_parameters. Table argument models draft those schemas; req list requires explicit job only in the agent profile. Consoles/external callers discover the same structure. Existing CLI flags map deterministically to the object. Hash complete schemas with Result schema/command name, persisting session/step hashes. Upgrade mismatches pause for new preview rather than executing old plans against new schemas. After approval, snapshot registry/flags/broker/API consistency without a second handwritten tool definition; incompatible changes increment major versions under project rules.

Brokers call **the same authenticated command services as API routes** in-process and return identical Result/exit codes, without forking bid, arbitrary HTTP, or duplicated algorithms. HTTP/brokers share submission transactions so child jobs/step links commit atomically. Identity/org transactions/material-card guards/budgets/audits still apply; in-process does not skip API security. Tool output retains existing structures, while models receive restricted redacted projections that neither impersonate original Result nor become evidence.

## Identity, permissions, and human gates

New persistent agent_principals are created at start by authenticated human sessions, binding valid Membership/user/org/grant expiry/scope snapshots. actor_kind=agent, without reusable human Bearer/Cookie. Effective step permission is **initial human grants ∩ current Membership/role grants ∩ requested reduced scope ∩ server A01 allowlist**; missing any component rejects. Later role additions never expand old sessions. Even admin/bidder initiators permanently lose confirmation/export/confidential write-reveal/provider-token-resource management/red-line settings/human-decision permissions. Scope reduction alone cannot preserve human review domains; actor_kind also remains.

New agent:read/run/cancel are human-only: admin/bidder/technical run/cancel, viewer read only. Initially only initiators read/control sessions; other same-org members get 404, without default admin observation. run also needs initial business scopes; roles do not guarantee material access. Demotion prevents resume, but valid owners retain cancellation. Org exit/disablement closes worker admission immediately; valid principals cannot bypass Membership. API tokens cannot request agent-management scopes, create built-in/nested agents; A02 keeps original command-scope tokens.

API tokens never have evidence:confirm, export, confidential:write/reveal, per [tokens.create_token](../../server/app/services/tokens.py)/DB constraints. Built-in agents do not impersonate initiators with human-issued API tokens. Child workers remain immediate actor_kind=worker with immutable agent provenance. Human-only services/DB gates explicitly reject agent/token/worker.

Grant lifetime is the lesser of initiating session remainder and 8 hours. Store issue/expiry metadata, no original credentials. Existing signed sessions have no persistent revocation ID, so browser logout is not claimed to revoke delegation immediately. Explicit cancel/grant expiry/member-org invalidation blocks new calls. Expiry pauses authority; only a new valid session for the same user resumes with reduced grants and unchanged cumulative limits.

Human pauses use agent_pauses, not terminal questions. Models propose HumanActionNeeded and systems may pause on gates; questions/results are sanitized/bounded. export/confidential_reveal creates fixed human instructions only, never executes commands or returns full values to conversations/models. Humans act through original UI/CLI; resume rereads facts and clears pauses, never confirms on their behalf. review_cards lists card/revision IDs; changed versions/input refresh pending actions rather than treating “approved” text as confirm. Humans may explicitly finish review and let existing draft services list gaps.

## Data models and migration outline

Add six business tables, **each with NOT NULL org_id, UNIQUE(org_id,id), ENABLE/FORCE RLS, USING/WITH CHECK policies**. Follow [org isolation](../notes/tenant-isolation.md), Database.transaction(org_id), transaction-local app.current_org. No context means no reads/writes. Runtime roles are nonowners without BYPASSRLS/SUPERUSER; recovery never uses privileged cross-org business scans.

| Table / public view | Storage/constraints |
| --- | --- |
| `agent_principals` / `AgentPrincipalView` | Owner user/membership, initial grants/reduced scopes, issue/expiry/revoked_at; matching membership/user. Initial grants immutable; renewal updates reduced effective scope/lifetime with audit |
| `agent_sessions` / `AgentSessionView` | task/document/extraction/principal, owner, state/revision, limits, step/call/activity counters, active_since, pinned schema/model/input hashes, current controller job/run/pause. start and unique terminal cancel save request hash/key/encrypted receipt separately; terminal sessions cannot reopen |
| `agent_messages` / `AgentMessageView` | session, increasing ordinal, role/author/step, encrypted content/sanitized content/hash, key/request hash/receipt; UNIQUE(org_id,session_id,ordinal), append-only, no reasoning traces |
| `agent_steps` / `AgentStepView` | decision/tool, ordinal, stable invocation_id, immutable created_by_job_id/run_id, revision, last_transition_job_id/run_id, pinned invocation/argument hash/input refs/schema hash, child job, state/result hash/exit/usage IDs. Invocation/full receipts encrypted, no raw parameters publicly; UNIQUE(org_id,session_id,ordinal), UNIQUE(org_id,invocation_id); terminal states immutable |
| `agent_pauses` / `AgentPauseView` | kind/pending objects/input snapshot/sanitized question/opaque contract-budget ref/state/resolver/time; resume key/original receipt; at most one pending pause per session, authorized humans resolve |
| `agent_job_links` / `AgentJobLinkView` | session/step/job, controller/tool, owned, creation time; session/job unique. Partial UNIQUE(org_id,job_id) WHERE owned=true, ownership/new Job commit together; owned=true aggregates all usage, historical cache refs owned=false never count as session spending |

All parents use org composite FKs: principal→Membership candidate key `(org_id,membership_id,user_id)`; session→task, Document `(org_id,task_id,document_id)`, Job `(org_id,task_id,document_id,extraction_job_id)`, and same-owner principal; message/step/pause/link→same-org session; step/pause crossrefs include session_id; link/step child_job→same-org/task/document Job, never another task. Add necessary parent composite UNIQUE constraints in migration. User remains global identity; Membership establishes business ownership, not a global-user FK alone. Extraction must be successful kind=extract. Service/DB triggers jointly validate kind/status/current revision; RLS alone does not ensure same-task binding.

Cancelling queued/running/waiting_job without pauses still saves cancel receipts on session; same-key replay returns original, changed arguments conflict. A different cancel key after terminal returns terminal_session/exit 2, without fake pauses. start/cancel/message/resume keys are isolated by principal/endpoint, without unbounded JSON receipt lists.

Controller jobs add kind=agent, retaining job_document_binding. Deferred current_job/pause composite FKs permit same-transaction initialization; historical rows do not cascade-delete. Only genuine actor-context paths write steps/messages/pauses; clients cannot inject assistant/tool roles, alter completed steps, or supply resolved_by. Views use explicit field allowlists, never arbitrary ORM serialization.

Migration order: parent candidate keys/six tables → RLS/composite constraints/append-only-state triggers/minimum column grants → job/audit ownership/human gates → provable historical audit backfill → same-change per-table two-org/missing-context tests → enable entry points. New audit refs use org composite FKs. Unknown provenance becomes legacy_unknown, never guessed agent. Business text uses core.security.Secrets bound to org/session/step; encrypted columns join [admin.py](../../server/app/admin.py) rotation. No new object-storage format; later attachments retain org/{org_id}/ and signed-download contracts. Rollback disables entry points/worker branches, retaining history/ledgers; downgrade cannot delete sessions/audits/fees.

## Worker, state recovery, and cancellation

Loops run in existing Procrastinate worker agent controller jobs; API validates/persists/dispatches only. Each controller exits at a persistent checkpoint. Waiting for children/humans occupies no worker slot/DB transaction. Controller success means saved checkpoint; session holds full state. Checkpoint transactions first publish session/step under live-run fencing, then set controller Job succeeded, finished_at/AgentJobResult, clear lease_until/session.current_job_id/current_run_id. Nonterminal results have completion=null and continue/waiting_job/paused disposition; completed/partial sessions use complete/partial. Wakes create new controller Jobs, never rerun normally finished segments as disconnected running jobs.

| Session state | Entry/exit conditions |
| --- | --- |
| queued→running | New/recovered controller; lock session/current_job, Processor obtains new run_id/lease |
| running→waiting_job | Same transaction saves step/child job/link/session revision; controller finishes normally |
| waiting_job→queued | Recovery wakes after child terminal, rereads real Result, completes step, queues next segment |
| running→paused | Budget/human action/invalid authority/uncertain outcome; atomic checkpoint/pause releases worker |
| paused→queued | Same human owner resumes after versions/permissions/budget/remainder checks |
| running→completed/partial/failed | Goal complete; published independent valid output plus failure=partial; failure without output=failed |
| Any nonterminal→cancelled | Persist cancellation closes admission first, then cancels owned in-flight jobs; published proposals/fees remain |

Each decision/tool execution uses one step; failures/dry-runs/retries count, idempotent replays do not. Validate/encrypt/save plans before executing their single tool. Parameters include explicit defaults, SHA-256 of sorted-key compact UTF-8 JSON, original-byte hash, tool schema/hash. Brokers generate trusted invocation IDs bound to steps, never model UUIDs.

Existing Queue.enqueue uses separate connections and cannot cover post-commit/pre-enqueue crashes. A01 needs transactional dispatch: **business Job, step/link, idempotency receipt, submission audit, and persistent Procrastinate task record share one PostgreSQL transaction**, with notifications visible on commit and joint rollback. Installed Procrastinate Task.configure(connection=...).defer_async/JobManager.defer_job_async accept external connections. Adapters borrow underlying psycopg from current SQLAlchemy transactions, without separate transactions/commit/close. Interfaces belong in [queue.py](../../server/app/jobs/queue.py); pinned dependencies in [uv.lock](../../uv.lock). After approval, real PostgreSQL must verify connection/rollback; API availability alone is not completion. This does not claim recovery for old non-agent queue paths. External work stays outside transactions; existing jobs record reuse. Models cannot execute arbitrary SQL. Write commands lacking atomic links/queryable idempotent receipts cannot become tools.

Every step/session/checkpoint publication/failure checks uncancelled session, expected revision, current_job_id/current_run_id, live Job lease, step revision/state CAS, current permissions/input. created_by_job_id/run_id retains original provenance; new controllers may advance waiting_job→completed under their own valid claim, updating revision/last_transition. Old creation-attempt leases cannot reject legitimate recovery. Child run_id comes from its own Job. Terminal steps are immutable; transitions/provenance audit separately. Final session-update checks alone are insufficient: fence before child submissions too. Old attempts may settle real usage, never dispatch/save successful steps/overwrite new state. Reuse [job heartbeats/cancellation](../notes/background-jobs.md), without new leases.

Add startup and at-most-30-second periodic recovery, with internal Procrastinate bid.agent_wake tasks forming a persistent wake chain. start and every nonterminal checkpoint save next wake in the same transaction, containing only org/session IDs and bounded scheduled_at; predecessor completes only after successor commit. Recovery scans internal todo/doing queue metadata and reschedules disconnected wakes. Last wakes for active sessions cannot be cleaned before business terminal states. Precommit crashes roll everything back; postcommit crashes retain wakes, without relying on successful enqueue to discover orgs or adding cross-org business-query exceptions. For each ID, use Database.transaction(org_id), checking sessions/locking queued/expired-running/waiting_job/checkpoints. Models cannot write queues. Paused wakes inspect expiry only, no model calls. Worker lifecycle continuously schedules recovery; DB outages retain records until recovery, without 30-second downtime guarantees. Delayed dispatch/rollback/duplicate wakes stay idempotent.

Recovery reads steps/linked jobs first: completed steps reuse; submitted children reconnect; live leases are not taken; expired jobs follow existing retry rules without clearing counts/cost/input. Waiting controllers do not hold locks, avoiding single-slot parent/child deadlock. Settled saved model decisions are never regenerated. Admitted calls without saved decisions or pending/unknown VendorCall mark step=uncertain and pause recovery, retaining reservations. No cross-vendor HTTP exactly-once claim or silent repeat payment. Verify ledgers/saved results; unverifiable steps terminate for humans to start another session. A01 grants no automatic reconciliation/unknown-reservation release.

services/jobs.status/cancel adds session/current-identity checks for agent jobs/linked children. All cancellation paths close parent admission; automatically cancel owned=true only, never historical reused jobs/other work. Admitted calls may settle within original vendor deadlines but cannot publish afterward. Cancellation is not a refund; interrupted accounting cannot become zero cost.

## Providers, context, and metering

AgentReasoningProvider extends LLMProvider with `decide(AgentDecisionRequest) -> AgentDecisionOutput`, returning exactly one tool/human_action/complete without reasoning traces/parallel tools. AgentToolProvider.definitions/invoke/recover handles discovery/authorized submission/idempotent recovery. AgentRecoveryProvider.wake is internal scheduling only, never a tool. Protocol declarations are not implementations or permission to import vendor SDKs in business modules.

Proposed implementations live in providers, resolve_llm/with_reasoning pin models/config/price revisions, and use structured.json_request/json_call, HTTPExtractor.post, accounted_call. Existing json_call returns wire output without settled usage. Add a shared Provider helper returning (validated_decision, trusted_provider_usage), preserving json_call's return contract. **Model output schema is AgentDecision only**; adapters assemble AgentDecisionOutput from trusted HTTP usage, never model-supplied ProviderUsage or a wire wrapper. Strict vendor schemas expand selected-tool branches, not arbitrary dict parameters through strict_schema, which closes objects and breaks open-dictionary semantics. Tool JSON Schemas are trusted input data; output uses closed discriminated AgentDecision. Unknown commands/extra parameters/fake results reject. Bounded network retries use at most three actual requests, each metered.

Models receive only session human messages, server-selected current task/extraction data, verified tool projections, tool schemas, remaining limits. Replace registered values before [redaction.py](../../server/app/services/redaction.py); require enabled redaction/pinned revision, pausing on disable/change. Original files, signed URLs, value suffixes, credentials, unrelated materials, full job.submission, raw vendor output never enter context. Conversations retain encrypted originals; public messages expose sanitized content; logs cannot echo bodies. Current messages max 8,000 characters, single projections max 64 KiB, serialized model requests max 128 KiB. Overflow pauses to narrow selections, without silently truncating citations/claiming full-text reads. Models receive at most 40 messages; initial boundary pauses without another model doing unreviewable summaries. No memory retrieval/fake used-memory fields. Human-readable summaries/tool receipts may persist, hidden reasoning does not.

Every model HTTP call, including decisions/child generation/retries/rejections/truncation/post-cancel responses, follows HTTPExtractor.post/accounted_call→JobExecution.admit→VendorCall→HTTP→complete→UsageRecord. Account before interpretation; uniqueness uses (org_id,job_id,run_id,call_id). Import ProviderUsage directly. Execution contexts settle immediately; AgentDecisionOutput.usage describes calls only, never another UsageRecord. Read tools/model-free assembly create no empty usage records.

Session costs are **deduplicated direct decision usage plus owned child-job usage IDs**, without copied UsageRecords/extra balance charges. Each controller AgentJobResult.cost covers itself only; session aggregation is AgentSessionView.cost, never double-counted. owned=false historical cache hits add zero costs; old job costs remain inspectable but not session spending. Initially other running jobs are not taken over; completed results may be referenced, without cancellation or shared billing ownership.

Vendor USD and platform settlement currency stay separate. Result.cost.usd is vendor cost; AgentCostView contains platform charge/outstanding reservations/currency/unpriced-unresolved counts. Org-owned models still record provider_config_id/tokens/cost with platform charge=0; external cost remains relevant. Unknown prices stay null; initially no paid calls without enforceable cost bounds, never unknown→0. Admission/settlement/actual-over-reservation handling follows [prepaid ledger](../notes/prepaid-billing.md#admission-and-the-spending-bound), without claiming enforcement against vendors ignoring token limits. Over-reservation actual costs are recorded and future calls stop.

## Budget pauses and hard limits

Task budgets depend exclusively on **contract-budget / docs/plan/budget.md**. A01 requires preview/per-call admission for pinned plans, input/model/price revisions, settled/unsettled calls, returning human-action refs. Interface names, allowance fields, approvers, reservation rules, lock order belong there, not another budget model here. BudgetDependencyRef carries question/revision refs only, never tickets/credentials/approvals.

Preview budgets before the first paid decision. Each later tool uses original --dry-run; plan changes/retries/splits/child requests pass budget dependency admission again. Human-action results persist pauses/known plan summaries before API/CLI questions, never sending over-budget requests. Unavailable dependencies return budget_dependency_unavailable, never ad hoc task.budget_usd arithmetic. Existing dry-runs bound first passes only, not total agent-plan costs.

Humans adjust/approve through separate budget entry points; resume supplies refs and server-verifies current status/principal/plan/revision. Model text, expired approvals, changed prices/input cannot reuse decisions. Prepaid ledgers still reject insufficient balances; budget answers cannot bypass arrears. Rejection never automatically switches models/splits calls to evade questions. Permanent budget refusal stops work, with independent outputs possibly partial.

AgentLimits are additional nonexpandable session caps, never replacements for task budgets. Recommended defaults: 24 steps, 32 actual vendor calls, 900 active seconds, 24-hour total lifetime. Server maxima: 64 steps, 64 calls, 3,600 active seconds. Humans explicitly provide max_vendor_usd/max_platform_charge/system currency; suggested interactive vendor-cost initial value USD 2. Platform amounts are explicit in org settlement currency, without implicit exchange conversion. Platform-paid calls cannot enter with charge=0; org-owned models may.

Counters accumulate across controllers/children/attempts/recovery/resume. Admission for all paid descendants checks remaining steps/time/cost/outstanding reservations/task budget, then original job/balance gates, not just tool submissions. Shared admission extensions align budget lock ordering before A01 enablement. Caps constrain both vendor USD/platform charge; unknown prices block. Autoexpanded per-job batch call ceilings never expand parent session ceilings.

Active time includes queued/running/waiting_job, retry backoff/outages; paused human wait is excluded but total lifetime applies. Persist active_since; DB-time checkpoints accumulate durations, never treating restart losses as zero. Each HTTP deadline is the shortest Provider deadline/remaining active time/total expiry. Cancellation/timeout settlement may finish without more dispatch. Recovery terminates expired sessions. Hard step/time/cost/call caps terminate; approval cannot raise them in-session. New sessions still share task budgets/outstanding reservations.

## Audit and A02 provenance

Propose indexed actor_kind, initiated_by, agent_principal_id, agent_session_id, agent_step_id, invocation_id, job_id, run_id on existing audit_logs, retaining actor_user_id/actor_token_id. Nullable additions preserve history; new agent events validate AgentProvenance relations, all tenant FKs include org. Backfill reliable evidence only, never treating missing token as human. Audits stay append-only; business changes/success audits transact together, failures append separately.

Immediate actor and initiating source differ: builtin_agent carries principal/session/step under current human Membership's reduced grants; background publication actor_kind=worker, initiated_by=builtin_agent. A02 derives token ID/owner from verified ApiToken, marking external_agent/token, never JSON/User-Agent claims. Initially token-initiated commands display “令牌自动化”, without claims of specific underlying models; product names await A02. A02 creates no A01 sessions and uses no A01 recovery/budget-answer endpoints.

Suggested events: agent.session.started/resumed/cancelled/completed/failed, agent.decision.started/completed/uncertain, agent.tool.submitted/completed/failed, agent.pause.created/resolved, agent.recovery.claimed, agent.limit_reached. Unified commands add command.invoked/completed/failed for tokens, including reads. Dry-run remains write-free despite invocation auditing. Mutation events keep commands/object-revision IDs/hashes/rejection reasons/cost associations, never conversations/text/full parameters/model output/credentials/signed links/confidential values.

Public card/generation-run/draft views propose read-only `AgentProvenance | null`. Later human edits retain historical agent sources plus new human actors, never erase provenance or call human confirmation (人工确认) agent confirmation. Trusted jobs/steps supply sources, not guessed audit details. Concrete records may resolve via model_job_id/generation run plus org; direct tool writes save source refs transactionally. UI shows built-in-agent/token-automation badges; display boundaries are delivered as contracts, without initial Vue implementation.

## Result, exit codes, and failure modes

New commands directly wrap Result with exactly **ok, command, data, items, warnings, cost, duration_ms**, without top-level status/version/agent. Details/mutations have items=[]; list data has pagination, items public views above. Errors data=AgentFailureData contain data.error.code/message/retryable plus visible org IDs, never traceback/input. Internal Provider/tool wrappers are not CLI envelopes. Example UUID/duration illustrate format only:

```json
{
  "ok": false,
  "command": "agent resume",
  "data": {
    "error": {
      "code": "budget_confirmation_required",
      "message": "预算待办尚未由人处理",
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

| Exit code | HTTP/session semantics |
| --- | --- |
| 0 | Successful preview/acceptance/read/legal cancellation; ordinary queued/waiting_job/paused queries explicitly remain unfinished. Controller success means checkpoint only; full completion queries return 0 |
| 2 | 400/422 invalid parameters, 409 revision/idempotency conflict/terminal resume, resume before budget/human actions resolved; correct input/resolve actions first |
| 3 | 429/503 temporary queue/DB/ bounded Provider failures; retain sessions/keys, retry within count/time caps. Explicit retryable failed-session queries also return 3 |
| 4 | 401 unauthenticated, 403 action denied, 404 missing/unauthorized resource, permanent refusal, post-cancel execution result, hard caps, unknown usage/accounting failure, protocol mismatch/unsafe recovery; no automatic bypass |
| 5 | Published independently valid output plus explicit failure/gap, ok=false; waiting for humans or costs without output are not partial success |

agent show returns 0/3/4/5 by terminal result, still AgentShowData. Successful historical list/messages/steps reads return 0 without rewriting failure facts. session.cost supplies aggregate cost; show wrapper matches it, list/pure controls have zero wrapper cost. Local/remote semantics match; private-read failures uniformly 404 without revealing others' sessions.

| Failure | Required handling |
| --- | --- |
| Prompt injection/fake commands-IDs/sensitive read-confirm-export | Broker allowlist plus parent/actor gates; safe human needs become pending actions; malicious parameters never echoed |
| Card/material/model/schema/redaction revision changes | Pause/repreview, without expanded inputs/overwriting human confirmations |
| Lost dispatch/restarts/duplicates/old-lease returns | Persistent step/link recovery/live run_id fence; no repeated completed steps/charges |
| Unknown vendor outcomes/metering persistence failures | Retain reservations, stop for recovery, never auto-resend/zero |
| Missing budget dependency/task over-budget/insufficient balance | Block admission/pause for humans/use ledger rejection respectively; none substitutes for another |
| Hard limits/cancellation/Membership-delegation expiry | Close calls; settle existing calls without publication; human renewals remain within caps |
| Provider refusal/invalid JSON/context overflow | Fixed safe errors, no arbitrary output persistence; bounded retries never expand input/prices/permissions |
| Partial generation/protected cards/draft gaps | Save verifiable output/real gaps, no auto-confirmation or swallowed failures claiming completeness |

## Post-approval test plan

Acceptance uses CLI/API→real PostgreSQL/RLS→real queue worker→fake Provider→existing human entry points→draft. These are proposed tests; static draft checks prove no runtime behavior.

1. **Per-table two-org tests:** every six-table A→B read/insert/update/delete/missing-context/FORCE RLS/nonowner/composite-FK cross-org/same-org cross-task-session binding. Cover audit refs/job links/append-only-terminal triggers/genuine actors/reduced scopes/owner-only beyond SELECT.
2. **Per-route two-org tests:** every GET/POST including messages/steps/list/cursor/start dry-run/resume/cancel/existing job branches returns uniform A→B/missing 404. Other same-org users get 404; platform operators have no bypass. Queues/model input contain no B canaries. Cover success/error/list/permission withdrawal.
3. **Human gates:** tokens requesting evidence:confirm/export/confidential:write/reveal fail. Principals directly calling human-only APIs/DB/tools/children cannot confirm/reopen-overwrite confirmed/pending/comply-only, decide prototypes/export/reveal. Real bidder/technical humans retain positive domain confirmation. Unconfirmed evidence never enters draft/export.
4. **Complete trusted flow:** synthetic successful extraction→req/card reads→generation dry-run→fake structured decision→child→human confirmation→draft→cited summary. Registry/schema/parameters/default/hash match real APIs. Model/tool/URL-ID injections never expand allowlists. Redaction changes/text-value canaries/overflow/negative deviation/card conflicts cannot hide.
5. **Recovery/concurrency injection:** SIGKILL around checkpoints, business/queue same-transaction commit-rollback, old run_id writes, heartbeat failures, concurrent resume/cancel workers, duplicate keys, recovery restarts, single worker slots, unknown VendorCall. Controllers terminalize normally; every nonterminal session has persistent wakes, disconnected doing wakes recover, pauseless cancel receipts are idempotent. Completed steps/usage never repeat. Expiry processes on the next sweep when data services return; old attempts publish no step/business state.
6. **Budget/billing:** contract-budget fakes/integration interfaces cover first-decision budget questions, child retries/splits, concurrent balances/parent caps, resume retaining ledger, expired/changed approvals, crashes after settlement, excluded historical-cache costs, exact new-call deduplication, post-cancel settlement, unknown org-owned cost blocking, hard deadlines on child calls. Budget answers never elevate permissions.
7. **CLI/audit artifacts:** new commands/tool --json/schema/local-remote/0/2/3/4/5 snapshots. Pauses machine-readable without terminal questions. Agent/token provenance throughout steps/cards/jobs/audits; writes/success audits transact together, dry-run write-free. Save synthetic inputs/commands/sanitized Result/usage-balance IDs/checkpoints/JUnit in artifacts/temporary directories, never docs logs/screenshots, reproducible by the same commands.

CI uses fake/MockTransport external Providers, never real vendors or fake ledgers as RLS/concurrent-charge proof. Authorized real-model/website evals run only in `evals/` with public input/pinned model-Schema/actual costs, each run within budget/human gates. Draft verification runs only ruff, pyright, offline imports, without PostgreSQL/runtime implementation/changelog additions.

## Open decisions

| Decision | Options | Recommended default/rationale |
| --- | --- | --- |
| Initial tools | ① Seven extracted-task tools plus human review/assembly; ② also parse/extract/search/capture/prototypes | **①** reuses generation/confirmation/assembly to test paid orchestration/recovery; later tools need contracts, and check/score stay outside this slice |
| Loop location | ① Checkpointed worker controllers; ② API loops; ③ new SDK service | **①** reuses leases/cancel/metering, releases slots while waiting, no large dependency |
| Visibility | ① Initiator-only; ② shared task members/admin reads | **①** task membership lacks complete entry points; avoid org-wide conversations, later sharing needs roles |
| Grant lifetime | ① Lesser of original session remainder/8h, same-human renewal; ② long delegation | **①** no session secrets, bounded unattended window; renewal retains caps, logout revocation not promised |
| Initial limits | ① 24 steps/32 calls/900 active seconds/24h lifetime, explicit amounts; ② larger initial caps, raised during runs | **①** testable recovery/parent-child costs; suggest vendor USD 2, platform currency separate, budget approval cannot raise hard caps |
| Uncertain recovery | ① Pause/verify, terminate/restart if unverifiable; ② auto-resend with duplicate-cost risk | **①** no automatic vendor reconciliation; lease expiry cannot release unknown reservations/repeat actions |
| Redaction/unknown prices | ① Enabled redaction/computable bounds; ② humans permit disabled redaction/unlimited org-owned models | **①** agents have no confidential-value authority and enforce bounded cost; price/redaction changes pause without auto-config changes |
| A02 labels | ① Token automation with token IDs; ② registered external agents/product names | **①** existing authentication proves sources; share audits first; A02 defines ② separately, never trusting self-report |
| Content retention | ① Encrypted append-only retention/no auto-delete initially; ② configurable org retention/deletion now | **①** recovery/accounting history first; ② needs audit-ledger refs/deletion-recovery contract |

Human confirmation of these choices is required before implementation. Task-budget rules are decided only in contract-budget, without a second budget-decision table here. Approval covers these interfaces/first flow; roadmap owns remaining design goals.
