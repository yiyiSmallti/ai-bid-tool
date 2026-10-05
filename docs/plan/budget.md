---
kind: plan
---

# Task budget enforcement and cost preflight

Status: **Approved. All recommended defaults are adopted.** This contract covers
[roadmap](roadmap.md) F06, F09 and C03. The approved interface is preserved in
[budget_contracts.py](budget/budget_contracts.py); the registered models live in
[budget_contracts.py](../../server/app/schemas/budget_contracts.py). Approval satisfies
the interface-first requirement in [agent.md](../../agent.md#工作方式).
Implementation details belong to [Task budgets](../notes/task-budgets.md).

## Objective and boundaries

The [design](../AI%20标书工具设计文档.md#cli-设计规范与外部-agent-接入)
requires read-only cost estimates and task budgets that agents cannot increase.
The slice is: a human sets a task budget; a command previews its first pass; each
Provider dispatch atomically reserves task liability and applicable prepaid funds;
usage settles immediately; insufficient capacity stops new calls; safe partial
results retain their existing publication rules; a machine-readable intervention
identifies the human action required. Task expenditure accumulates across commands,
jobs and retries, without a monthly reset. Durable low-balance notices cover work
without a person waiting at the CLI.

The scope includes LLM, Vision, local OCR, search and local Browser operations,
including the existing check and score workflows. Local Browser work has explicit
zero liability and retains sandbox access controls. Cloud OCR, Embedding and paid
Browser adapters must satisfy this contract before being enabled; adding such
vendors is outside the slice. Taskless `provider test` checks org balance and job
limits and provides the same cost preflight.

Subscriptions, plans, monthly quotas, online payments, exchange rates, automatic
budget increases, email/SMS/webhooks, automatic reconciliation and a generic
pause/resume framework are excluded. Budgets never enter model prompts and do not
approve evidence, exports or prototype delivery decisions.

## Existing mechanisms and prerequisites

| Component | Reuse and required change |
| --- | --- |
| [contracts.py](../../server/app/schemas/contracts.py), [documents.py](../../server/app/services/documents.py) | Preserve legacy `budget_usd` compatibility; add explicit limit/currency/revision. Preserve the seven Result keys; vendor USD remains distinct from sale-price charges. |
| [calls.py](../../server/app/providers/calls.py), [execution.py](../../server/app/jobs/execution.py) | Evolve the existing single admission boundary, durable VendorCall reservations and idempotent settlement. Adopt Task → Job → OrgBalance locks. |
| [llm.py](../../server/app/providers/llm.py), [screenshot_vision.py](../../server/app/providers/screenshot_vision.py) | Reuse exact request bytes, token/output upper bounds and fixed image-price revisions. |
| [billing.py](../../server/app/services/billing.py), [provider-config.md](../notes/provider-config.md) | Preserve fixed catalog/config identities, currency checks and prepaid ledger settlement. A zero BYOK platform charge is not a zero task cost. |
| [ADR 0002](../adr/0002-prepaid-billing.md) | Its earlier no-reservation decision is superseded for admission by [ADR 0007](../adr/0007-task-budget-reservations.md). |
| [tender_jobs.py](../../server/app/services/tender_jobs.py), [card_generation.py](../../server/app/services/card_generation.py) | Add task exposure to first-pass estimates and next-call blockers; an estimate is not a whole-job guarantee. |
| [drafts.py](../../server/app/services/drafts.py) | `draft` assembles deterministic tables; paid writing is `card generate`. Do not invent usage for assembly. |
| [local_ocr.py](../../server/app/providers/local_ocr.py), [processor.py](../../server/app/jobs/processor.py) | Admit and settle each OCR page; remove post-processing duplicate accounting. |
| [search.py](../../server/app/providers/search.py), [vendor_search.py](../../server/app/services/vendor_search.py) | Account each HTTP dispatch, including a broadened search after a domain-limited query. |
| [product_simulation.py](../../server/app/services/product_simulation.py) | Share one task budget across classification, queries, page reads and model extraction; lock Task before the publication Job. Register the existing simulation API as a thin CLI command. |
| [platform.py](../../server/app/services/platform.py) | Live model probes use an explicitly authorized internal test org and the existing metered provider-test framework. No global usage exception. |
| [background-jobs.md](../notes/background-jobs.md) | Preserve leases, run IDs, publication fences and terminal states; do not add paused jobs. |

## Interfaces

Reuse Contract, Cost, Result, ProviderUsage, TaskCreate, ProviderTest and Sha256;
do not duplicate domain content or evidence schemas. Paths below omit the version
prefix described under Result compatibility.

| Entry point | Request and response |
| --- | --- |
| `GET /tasks/{task_id}/budget`; `bid task budget show --task UUID --json` | No body; `TaskBudgetData`; empty items. |
| `PUT /tasks/{task_id}/budget`; `bid task budget set --task UUID --input FILE --json` | `TaskBudgetSet`; committed `TaskBudgetData`; revision and exposure checks, history and audit in one transaction. |
| `GET /tasks/{task_id}/budget/history?before_revision=N&limit=100`; `bid task budget history` | Limit 1–100; `BudgetHistoryData`; `TaskBudgetRevisionView[]`, descending revision. |
| `POST /tasks`; `bid task create --budget AMOUNT --budget-currency CODE` | `BudgetTaskCreate`; original task fields plus `budget: TaskBudgetView`. Explicit API `budget.limit=null` removes the amount cap; omitting CLI budget flags creates an unlimited task. |
| `GET/PUT /billing/low-balance-policy`; `bid billing alert show/set --input FILE` | PUT accepts `LowBalancePolicySet`; returns `LowBalancePolicyData`; show has no input. |
| `GET /billing/notices?before=UUID&limit=100`; `bid billing notices` | Descending `(created_at,id)` cursor; limit 1–100; `LowBalanceNoticesData` and `LowBalanceNoticeView[]`. |
| Costed command `--dry-run --json` | Preserve domain preview fields/items; add `data.budget_preflight: BudgetPreflightData`; top-level cost equals its estimate. |
| `GET /jobs/{job_id}`, `bid job status/wait`, command `--wait` | Preserve domain results; attach `data.result.budget: BudgetJobResult`; completion/stop_reason agree; never expose submission snapshots. |

All affected entry points must participate:

| Command | Estimate boundary |
| --- | --- |
| `tender parse` | Inspect local file/page metadata and candidate OCR pages without running OCR; local OCR is explicitly free. |
| `req extract` | Reuse batch and request construction for the first pass; report splitting, gap filling and retries as uncertainty. |
| `card generate` | Preserve outbound manifest, expected_input_hash and max_charge; add current task exposure. |
| `screenshot analyze` | Use each image's verified token/price revision; distinguish first-pass affordability from next-call admission. |
| `ui mock` | Model first pass plus zero-liability local Browser work; retain sandbox blockers. |
| `evidence search` | Fixed initial query count and platform-absorbed liability; no actual searches during preview. |
| `product simulate --task UUID --input FILE` | Reuse ProductSimulationInput and human-only access; estimate locally constructible classification requests. Dynamic queries/pages remain uncertain; maximum_calls is bounded only when justified. |
| `provider test --dry-run` | BudgetProviderTest; synthetic page and local prices; no remote balance request or Job creation. |
| `platform model test --id MODEL --dry-run` | BudgetPlatformModelTest; synthetic request based on fixed catalog reasoning. Live calls require `--test-org UUID`. |
| `check`, `score rubric generate`, `score run` | Preserve domain snapshots, redaction, prices, expected hashes and first-pass previews; rules-only checks are zero cost. |
| `draft`, `screenshot annotate`, `sandbox render/capture`, `export prepare` | Preserve zero-cost previews. No fabricated usage for pure assembly, storage or conversion. Actual local Browser operations are metered as free calls. |

### Read-only preflight

Preflight creates no Job, audit, notice, usage or reservation. It sends no model,
OCR, search, Browser or vendor-balance request. Cached results require fresh access
and dependency checks. A valid hit uses `basis=cache_hit` and a zero current
estimate; `cached_result_cost` carries the original job's paid cost. Task spent
never decreases because of a cache hit.

`next_call` is the conservative bound for the next real request.
`admission_blocker` describes why that call cannot start now. `first_pass_fits=false`
means the whole first pass exceeds available capacity, not that a safe prefix must
be rejected. Dynamic work can return null with explicit uncertainty. A successful
preview that finds a blocker exits 0; actual admission uses the error table below.

Input, price and Provider identity hashes exclude transient balance and budget
revision. Existing expected-input-hash gates remain mandatory. Preflight amounts
are never trusted as admission authority. `full_run_guaranteed` is always false.
`duration_ms` measures preflight; estimated job duration belongs in
`estimated_duration_ms`, null unless backed by a measured profile.

### Result cost and compatibility

Result retains exactly `ok`, `command`, `data`, `items`, `warnings`, `cost` and
`duration_ms`. `llm_tokens`, `ocr_pages` and `usd` keep their types. Execution cost
is cumulative actual usage; dry-run cost is a conservative first-pass estimate.
`usd` always means vendor USD cost and is null if any included vendor cost is
unknown. It never contains sale prices or a different currency.

Cost adds exactly `basis`, `charge`, `billing_currency`, `task_amount`,
`unpriced_calls` and `unresolved_calls`. Charge means prepaid debit only.
Task amount is the org's responsibility in billing_currency. New monetary fields
use Decimal, JSON decimal strings and database `numeric(18,8)`. Unpriced calls
count unknown task liability, not missing vendor USD when the sale price is known.
Taskless provider-test retains responsibility-based amounts with null task_id,
task_budget and budget_revision. Reads and queue acceptance report zero current
cost; status/wait report the queried job's cumulative cost.

The new contract is **4.0**, served under `/v4`. Existing unprefixed APIs retain a
3.0 projection for at least one major-version cycle and execute the same hard
budget checks. CLI defaults to 4.0; `--contract-version 3.0` exposes/calls only
legacy commands. `bid schema` describes the selected version's exact arguments,
output and exit codes. Legacy strict Cost parsers must not receive new fields.

## Liability and currency

| Payer | Task reservation and settlement | Prepaid balance |
| --- | --- | --- |
| `org_platform` | Fixed catalog sale-price charge in BID_BILLING_CURRENCY. | Reserve in the same admission; deduct on valid usage. |
| `org_direct` | Fixed configuration's vendor prices; bounded paid admission under a finite budget requires complete USD pricing and USD deployment. | Zero prepaid debit; task cost may be positive or unknown. |
| `platform_absorbed` | Search has explicit zero task liability; still record each call and vendor USD cost, null when unknown. | No org debit; charging for search needs a separate decision. |
| `local_free` | Local OCR/Browser has explicit zero liability and records pages/calls. | No debit. |

A null limit is unlimited; zero permits only explicitly free responsibility.
Unknown request liability or incomplete historical liability blocks positive or
unknown requests under a finite budget with `task_budget_unpriced`. Reads,
compliant exports, assembly and explicitly free local work remain available.
Unlimited tasks still obey org balance, job limits, call ceilings, permissions and
accounting. Null/unknown must never be treated as zero.

Currency is deployment-wide, not request-selectable. No conversion is provided.
Legacy budget_usd maps to limit only in USD deployments and is mutually exclusive
with budget. New legacy USD inputs in other currencies fail with exit 2. Existing
non-null USD caps on non-USD deployments require human currency review before new
positive liability; do not relabel or convert them. BYOK control is limited to
configured prices, not the vendor's entire external bill.

## Persistence and migration

Reuse Task, VendorCall, UsageRecord, OrgBalance, Job and AuditLog. Do not create
another money ledger or infer all task costs from prepaid balance entries.

| Table | Required state and invariants |
| --- | --- |
| `task_budget_revisions` | Append-only id/org/task/revision/limit/currency/state/actor/origin/reason hash/timestamp. UNIQUE(org_id,id), UNIQUE(org_id,task_id,revision); human changes require actor and hash. Token creation can only leave an unlimited initial revision. |
| `tasks` | Nullable budget_limit, budget_currency, budget_revision and budget_state. Task is the expense mutex. Deferred consistency binds current fields to history. |
| `vendor_calls` | Task (null only for provider_test), capability, payer, budget revision, nullable reserved_task_amount, currency, price revision, request hash and public quote. Existing charge fields remain prepaid-only. Bindings cannot change. |
| `usage_records` | Capability, payer, task_amount, billing_currency, price_revision and search_requests, plus original provider/model/tokens/pages/USD/image metadata. New usage references an admitted call. |
| `jobs` | Durable submitting user/token/actor kind and granted scopes; immutable vendor-cost history completeness preserves unknown pre-cutover search costs across retries; domain result gains budget attachment. No paused status. |
| `org_balances` | Threshold defaults to zero, null disables; alert_revision starts at one; low state and cycle track transitions. Available is balance minus outstanding prepaid holds, never a second stored balance. |
| `org_balance_notices` | Append-only id/org/policy revision/cycle/threshold/currency/available balance/timestamp; unique org/id and org/policy revision/cycle. |

Both new tables require NOT NULL org_id, ENABLE and FORCE RLS using app.current_org
with USING/WITH CHECK, org composite parent references and two-org isolation tests.
No missing-context access, table ownership, BYPASSRLS or new cross-org read policy
is granted to the runtime. History references tasks and memberships by org;
notices reference org balances; calls reference task/revision and the exact job;
usage retains its `(org_id,job_id,run_id,call_id)` key. Same-org but different-task
bindings must also fail. Taskless calls have no budget revision.

Amounts are nonnegative except derived available values. Currency is three uppercase
letters. Revisions increase; completed calls have one matching usage. Human actor
gates protect budget writes and history/notices cannot be updated or deleted.
Audits contain IDs, revisions, currency, reason hashes and safe result codes, never
budget values, raw reasons, URLs, prompts, quotes or secrets.

Migration `0040_task_budget.py` has revision `0040`, parent `0039`.
Cutover requirements:

1. Stop old workers and drain determinable calls. Preserve pending/unknown holds;
   do not mix binaries with old admission paths.
2. Backfill call tasks from jobs. Recover known platform charges, BYOK USD and
   proven free responsibility from stored usage. Count legacy unlinked usage once;
   never add completed VendorCall charges again. Unknown provenance stays unknown.
3. Map legacy USD budgets exactly and create migration revisions; non-USD caps
   require review. Do not invent missing submitters: fresh authorized submission
   must bind a legacy job before execution. Proven platform-absorbed search does
   not make task-liability history incomplete merely because vendor USD is unknown.
4. Validate task totals, org exposure and two-org isolation, then enforce complete
   gates. Incomplete finite-budget history blocks new paid calls until separately
   reconciled. No automatic repair is authorized.
5. Preserve ledger/history on downgrade. A rollback must not restart an old worker
   that bypasses budgets.

Recover old platform currency from its BalanceEntry rather than the current env.
Unrecoverable liability remains unknown. Deployment currency checks include task
budgets and pending/unknown calls; unresolved holds forbid currency switching.
Historical settled entries retain their original currencies. Currency conversion
and history resets are outside scope.

## Admission and concurrent settlement

Provider quotes derive from actual request bytes, fixed configuration and prices.
Services cannot supply trusted payer, prices or task IDs. Task comes from the
current authorized Job. Text/image bounds reuse existing formulas; OCR uses pages
and search uses requests. BudgetCallAccounting evolves CallAccounting;
accounted_call remains the only dispatch boundary.

Production calls require current_accounting and fail explicitly without it.
Standalone real adapters are for explicit eval contexts only. Count each actual
search HTTP request, OCR page and local Browser operation; sandbox subresources
remain governed by fetch quotas rather than becoming model calls.

The lock order is **Task → Job → OrgBalance** for admission, settlement and
publication. Taskless provider_test uses Job → OrgBalance. Budget changes lock
Task only. Existing sandbox org mutexes, when needed, precede Task; never acquire
them from OrgBalance.

One short admission transaction:

1. Resolve the same-org Job's task, lock Task then Job, and recheck binding,
   run/lease/cancel state, live submitter grants and before_admit input/config gates.
2. Compute S from settled task UsageRecord amounts and H from pending/unknown
   reserved_task_amount. For new bound R and finite limit L require **S + H + R ≤ L**.
   Read the current revision, not the queue-time cap. Unknown positive exposure
   cannot enter a finite budget.
3. Apply existing cumulative call, job-charge and user max_charge limits. Include
   OCR/search/Browser in call planning. Job monetary limits remain prepaid-charge
   based. Platform calls lock OrgBalance and check all org outstanding holds.
4. Insert both reservations in one VendorCall. Dispatch only after commit.
   Rejection leaves no call/usage/debit or one-sided reservation. Ambiguous commits
   must not dispatch; any durable hold remains for reconciliation.

Settlement uses the original call idempotency key. In one transaction insert
usage, settle both reservations, debit balance/write its ledger, refresh job cost
and evaluate low-balance state. Round reservations upward to eight decimals and
actual amounts with ROUND_HALF_UP; never sum money with binary floats. Usage
payer/currency/price/config must match admission. Preserve the bounded three-retry
policy for transient database failures. Refused, malformed and error replies with
valid usage settle before content parsing.

Timeouts, missing/invalid usage and worker death keep pending/unknown holds.
Cancellation, lease loss, retry and raising a budget never release them. A proven
unsent credential-preparation failure releases both reservations. Late usage may
settle the original call after takeover but cannot publish old results or overwrite
a successor's state. Drain admitted requests within their deadlines and stop new
batches.

If actual C ≤ reserved R, concurrent task overspend is zero. A provider violating
its bound still records full cost; possible overrun is the sum of max(0,C−R) for
admitted calls. Return `call_charge_bound_exceeded` and fence further calls and
publication. Other attempts reread actual exposure. Never truncate bills or hide
negative available values. Fixed identity checks prevent cheaper previews from
funding more expensive actual models.

## Authorization, changes and audit

Reuse [auth.py](../../server/app/services/auth.py) authentication, Identity,
ROLE_SCOPES, SCOPES and actor context, and intersect saved worker grants with live
membership and token rights before every admission.

| Actor | Allowed operations |
| --- | --- |
| Authorized human roles/tokens with task:read | Read same-org task budgets/history; execution and preflight additionally require the domain scopes. |
| Human admin or bidder session | Set initial limits; change/raise/lower/remove a cap with human-only task:budget:write. No invented task-creator ownership boundary. |
| Technical/viewer/agent/token | Cannot change caps or create explicit budgets. Existing task:create tokens may create unlimited tasks; mandatory caps need another decision. |
| Human admin session | Change policy with billing:alert:write. billing:read permits balance/notices, including appropriately scoped tokens. |
| Platform operator | No implicit tenant rights; live model testing also needs active human admin membership in the selected test org. |

Neither new write scope belongs to token SCOPES. API and DB token/actor gates also
reject them. Tokens still cannot gain evidence:confirm or export; agents preserve
a non-human actor kind. Missing/cross-org objects return 404; a same-org operation
without scope follows existing 403 behavior; invalid identities return 401/403.
Without billing:read, preflight reports only low_balance/insufficient_balance and
an action, never precise org balance or notice history.

Every change requires expected_revision and a trimmed nonblank reason. A new
finite cap cannot be below S+H or cover unknown settled positive exposure. Conflicts
return 409/exit 2. Removing the cap is also audited. Changes do not credit prepaid
funds, bypass job limits or automatically restart terminal work.

Events: `task.budget.created`, `task.budget.changed`,
`task.budget.admission_denied`, `task.budget.bound_exceeded`,
`billing.low_balance_policy.changed` and `billing.low_balance`. Human history and
audit commit together. Admission denial is audited after rollback, deduplicated by
job/run/reason/revision using the actual submitter and worker identity. System
notices themselves are automatic events; do not invent a human audit actor.
Preflight writes none of these events.

## Terminal behavior and continuation

| Condition | Result |
| --- | --- |
| First call denied or no safely publishable result | Failed, ok=false, exit 4; retain intervention, actual cost and unresolved holds. Paid usage alone is not partial success. |
| Existing partial-capable command has validated results | Succeeded with completion=partial, safe stop_reason, ok=false, exit 5; expose incomplete IDs/reasons and preserve original publication gates. |
| Atomic parse/extract, or search/prototype without a complete artifact | Failed, exit 4; no partial chunks, requirements or files. Accounting survives. |
| Cancellation, lease/heartbeat/accounting failure, bound overrun | Preserve hard publication fences; no partial-result escape hatch. |

BudgetIntervention includes code, org/task/job, budget revision, currency,
available, required_next_call, minimum_new_limit, action and authorized roles.
It fixes human_required=true and auto_retry=false. The minimum cap S+H+R covers
only the next request, never the whole run; unknown price/history yields null.
Org insufficiency needs recharge; job caps need separate review. Agents may ask a
person, but only a human budget API can authorize changes; the CLI never prompts.

Explicit retry keeps accumulated calls, usage and holds, using a new run ID only
for attempt ownership. Partial drafting remains a terminal cache entry; submit
remaining requirement_ids as a new input. Budget revisions do not change model
input cache keys or overwrite confirmed cards. Continuation is none, retry_job,
submit_remaining or reconcile_first; no general checkpoint/resume guarantee.
Status, wait and command --wait must preserve identical paid cost, intervention
and domain items before mapping exits, rather than replacing failure with a
zero-cost generic error.

## Low-balance notices

The threshold is a fixed amount in billing currency, defaults to zero and is
disabled by null. Available balance is stored balance minus all outstanding
prepaid reservations. Admission, settlement, redemption, platform adjustment and
policy changes update state under the balance lock. Entering available ≤ threshold
increments cycle and writes one unique notice; continuous low state does not
repeat. Recovery above the threshold permits a later new cycle. First enabling a
policy while already low also emits once. Deferred transaction evaluation must
not mistake settlement's temporary release/debit steps for recovery and re-entry.

GET/preflight never repairs or emits notices. In-app notification means persisted
API/CLI notices; no email or SSE delivery is claimed. Cost results can add a
balance-free low_balance warning. Old notices remain immutable and consumers read
the policy to determine current state. Recharge needs no notice acknowledgment.
Controlled platform functions must not gain arbitrary tenant business reads.

## Error and exit mapping

| Exit | HTTP / meaning |
| --- | --- |
| 0 | Successful read/change/preview, including a preview blocker; queued means accepted, not completed. |
| 2 | 400/422 invalid precision/nonfinite/currency/mutually exclusive input; 409 stale revision or cap below exposure. |
| 3 | 408/429/503 transient network/queue/database/wait timeout. A timeout does not cancel work or release holds. |
| 4 | 401/403/404 identity/access/object; 402 task_budget_exceeded/insufficient_balance; 409 unpriced/currency-review/job cap; nonretryable Provider or atomic failure. Preserve accounting/intervention. |
| 5 | HTTP 200 terminal partial success, only with publishable domain results; ok=false, completion=partial and safe unfinished items. |

Budget rejection is never auto-retryable. Task locks resolve races between cap
changes and settlement. Lock timeouts cannot mean sufficient budget. Unknown
outcomes never imply no charge.

## Verification and acceptance

Use API → worker → CLI scenarios against restricted PostgreSQL roles with two
orgs; vendor traffic uses fakes or MockTransport. No real external calls in CI.

- Every new/extended table and route: missing-context and two-org isolation;
  cross-org 404; same-org wrong-task FK rejection; no direct history/notice mutation,
  forged human budget action or token-only write scope.
- Migration: exact USD backfill, non-USD review, old call-less usage counted once,
  unknown price/currency retained, unavailable submitter blocked, unresolved holds
  preserved and unsafe downgrade refused.
- Admission: concurrent sibling tasks/jobs, cap zero/null, BYOK known/unknown,
  absorbed search, local OCR/Browser, revision races and both-reservation atomicity.
- Settlement: duplicate and ambiguous commits, over-bound actual cost, missing or
  malformed usage, stale lease, cancel/retry/takeover, late usage and live revocation.
- Publication: valid partial output exits 5; first-call/atomic failure exits 4;
  no unconfirmed evidence, prototype/export bypass or overwrite of confirmed cards.
- Preflight: every affected command, zero provider requests and no DB/file/audit
  changes, precise version projection, null/Decimal/basis semantics, cache zero
  current cost and retained historical cost. Status/wait preserve costs on 0/2/3/4/5.
- Notifications: first entry, continued low state, recovery/re-entry, concurrent
  settlement/adjustment, disable/re-enable, admin-only mutation and read-scope privacy.
- Keep synthetic inputs, request/result snapshots, call/usage/ledger reconciliation
  and JUnit under `artifacts/budget/` or `data/work/budget-validation/`, never docs.
  Live vendor checks belong only in explicit evals. Database execution is delegated
  to the integration environment when the implementation sandbox cannot reach it.

## Decided decisions

| Decision | Adopted default | Reason |
| --- | --- | --- |
| Running job exhausts budget | Stop; validated partial output exits 5 where supported, atomic failure exits 4. | Reuse terminal states and leases; human questions happen outside workers. |
| Task liability | Platform sale charges plus declared BYOK vendor cost; absorbed search stays zero org liability. | BYOK cannot silently bypass a finite task cap. |
| Non-USD, BYOK and legacy caps | No FX; platform uses deployment currency; bounded BYOK pricing initially requires USD; legacy USD caps on other deployments need review. | Do not invent conversion or prices. |
| Who can change limits | Human admin and bidder sessions with reason and expected revision. | Align task creation with bidder responsibilities while excluding tokens/agents. |
| Unlimited tasks | Preserve null and existing unlimited creation. | Mandatory caps and agent creation defaults need a separate decision. |
| Low-balance notification | Durable in-app notice plus warning; fixed admin-configurable threshold default 0. | Avoid external communication services and arbitrary percentage bases. |
| Search payer | Platform-absorbed with per-call metering. | Do not begin charging without an approved price policy. |
| Live platform probes | Explicit internal test org; operator also its active human admin; existing provider-test accounting with fixed tested catalog identity and sale price. | No global business-table exception or active config mutation. Missing test_org_id exits 2; unauthorized org or insufficient balance exits 4; dry-run remains available. |
| Result transition | 4.0 with a 3.0 projection for one major-version cycle. | Existing strict parsers cannot silently accept extended Cost. |
| Plans/monthly org quotas | Excluded. | Billing periods, time zones, rollover, refund and excess rules need a separate contract. |
