---
kind: reference
---

# Task budgets

## Problem

Concurrent jobs can spend the same task allowance unless admission reserves both
task liability and prepaid funds before dispatch. A timeout is not proof that a
vendor did no work. Previewing a job, cancelling it or retrying it must not erase
spent amounts or unresolved exposure. The approved behavior and scope are in the
[task-budget contract](../plan/budget.md); reservation rationale is in
[ADR 0007](../adr/0007-task-budget-reservations.md).

## Usage

The budget commands use human login sessions for writes:

```sh
bid task create --name "Tender review" --budget 20 --budget-currency USD --json
bid task budget show --task TASK_UUID --json
bid task budget set --task TASK_UUID --input budget-change.json --json
bid task budget history --task TASK_UUID --json
bid billing alert show --json
bid billing alert set --input alert-policy.json --json
bid billing notices --json
```

A budget change names the currently observed revision and a nonblank reason:

```json
{
  "limit": "25.00000000",
  "currency": "USD",
  "expected_revision": 1,
  "reason": "Approved additional review work"
}
```

A null limit removes the amount cap; zero allows only explicitly free operations.
An alert policy takes threshold, currency and expected_revision; null threshold
disables notices. Reasons are hashed for audit, not stored as arbitrary plaintext.
Budget writes require a human admin or bidder; policy writes require a human admin.
Tokens and agents cannot obtain either write scope.

Every costed command exposes `--dry-run`. The preview includes current task
exposure, first-pass cost, next-call quote and an admission blocker. A blocker in a
successful preview still exits 0. Job status/wait retain actual cost and a budget
attachment when execution fails. Resolve the intervention before explicitly
retrying an atomic failure or submitting the unfinished IDs from a partial result.
Increasing a cap does not recharge the org or restart a terminal job.

## How it works

The existing `accounted_call` boundary receives a quote derived inside providers
from exact request bytes, fixed configuration and price revision. `JobExecution`
reads the Job's task and takes Task → Job → OrgBalance locks. Live membership and
token scopes intersect the persisted submission grants before each dispatch.
Actorless local system jobs retain a null submitter and empty grants. Inserting
such a job does not authorize a Provider call: admission still requires a saved,
currently authorized submitter. A worker never borrows the task creator's identity.

For settled task liability S, pending/unknown holds H, new conservative bound R
and finite limit L, admission requires S + H + R ≤ L. Both task and applicable
prepaid reservations occupy one VendorCall row. Commit precedes dispatch. The
same row records fixed payer, currency, pricing identity and request hash.
The quote identifies the requested model; UsageRecord preserves the actual model
name validated or sanitized by the adapter. A fallback response cannot change the
admitted catalog/configuration, payer, price revision or currency.

Settlement inserts one UsageRecord under the original org/job/run/call key,
replaces the hold with actual liability, debits prepaid funds and writes its
BalanceEntry in one transaction. Late usage remains billable even after lease
loss. Business publication independently checks the current attempt. Unknown
outcomes retain both holds; only a proven unsent request releases them. Actual
cost above a quoted bound is retained and fences subsequent calls/publication.

Platform liability uses sale-price charges. BYOK liability uses declared vendor
USD prices in USD deployments. Platform-absorbed search and local OCR/Browser
have zero org liability, but still consume calls and record usage. Missing vendor
USD for absorbed search stays null; it does not become zero vendor cost.
Pre-cutover search jobs retain an immutable vendor-history completeness flag and
an explicit warning; their unknown vendor USD does not make proven zero task
liability unpriced, and retry cannot erase that historical uncertainty.

Read-only preflight reuses Provider quotes without dispatch or persistence.
First-pass affordability and next-call admission are different fields. Cache hits
report zero new cost and retain the previous result cost separately. Result 4.0
adds typed cost metadata while preserving the seven-key envelope; `/v4` and the
new CLI expose it, and the legacy projection preserves strict 3.0 Cost fields.

Task revision history and low-balance notices are append-only tenant tables with
FORCE RLS and org composite foreign keys. Human budget changes lock Task and
compare expected revision and total exposure. Database gates bind the current
budget to its exact history revision and reject token/agent writes.

Low-balance state evaluates balance minus outstanding prepaid holds. Deferred
transaction triggers see the final reservation/settlement state, so releasing a
hold and recording its debit cannot create a spurious recovery cycle. Entering
available ≤ threshold emits once; remaining low does not repeat; recovery permits
another cycle. Recharge and controlled platform adjustments use the same state
transition. Read-only queries never emit a notice.

## Pitfalls

- A preview is a point-in-time estimate, not a reservation or full-run guarantee.
  Dynamic search, retries and split requests add uncertainty.
- Unlimited budgets still obey prepaid, permission, job-charge and call ceilings.
  Unknown prices cannot silently enter finite budgets; explicitly free work remains
  usable while paid history needs review.
- Do not add UsageRecord rows after processing or release holds on cancellation,
  retry or deployment. All real dispatches require active accounting; explicit
  standalone evaluation contexts are outside task execution.
- Partial success requires existing command semantics and validated publishable
  results. Atomic parsing/extraction failures retain accounting but publish no
  incomplete data. Lease, accounting and bound violations remain hard fences.
- Do not aggregate different billing currencies or convert legacy USD caps.
  Non-USD legacy budgets require human review; unresolved holds prevent a currency
  switch. Monetary calculations use Decimal and eight decimal places.
- Deploy with old workers stopped. Downgrade preserves accounting history and must
  not resume binaries that can dispatch without task admission.
- In-app notices are persisted API/CLI records, not email, push or SSE delivery.
  Scope checks protect precise org balances and notice history.

## Code

- [Budget schemas](../../server/app/schemas/budget_contracts.py): budgets, quotes,
  preflight, intervention, job attachment and alert policy.
- [Budget persistence](../../server/app/services/budgets.py) and
  [migration](../../server/migrations/versions/0039_task_budget.py): exposure,
  human changes, history, tenant constraints and notification transitions.
- [Execution](../../server/app/jobs/execution.py): lock order, live grants,
  reservation, settlement, cumulative cost and terminal budget attachment.
- [Call boundary](../../server/app/providers/calls.py): dispatch, cancellation drain
  and unknown/unsent outcomes. [LLM](../../server/app/providers/llm.py),
  [Vision](../../server/app/providers/screenshot_vision.py),
  [search](../../server/app/providers/search.py),
  [OCR](../../server/app/providers/local_ocr.py) and
  [Browser](../../server/app/providers/browser.py) construct capability quotes.
- [Preflight](../../server/app/services/budget_preflight.py): read-only aggregation
  and admission hints; [CLI schema](../../cli/bid_cli/schema.py) describes versions.
- [Execution scenarios](../../server/tests/test_task_budget_execution.py): API/worker
  concurrency, unknown outcomes, revocation and idempotent late settlement.
