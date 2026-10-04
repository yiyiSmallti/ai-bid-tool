# Prepaid billing

## Problem

Platform-billed model calls must be paid for in advance. Orgs recharge with
cards, operators issue cards and correct balances, and no org may spend
beyond what it holds, credit another org, or have a card redeemed twice.

## Usage

Set `BID_BILLING_CURRENCY` before the first balance exists. Operators issue
and void cards on the console card page or with `bid platform card`, and adjust
balances from the org page or with `bid platform org balance`. Org admins
redeem at `/app/org/billing` or with `bid billing redeem`. Commands are in
[cli.md](../guides/cli.md#recharge-and-billing); the decision record is
[ADR 0002](../adr/0002-prepaid-billing.md).

## How it works

Migration `0012` creates `org_balances` and the append-only `balance_entries`
under forced RLS, and the global `platform_cards`. A card code is 16 characters
from a 32-symbol alphabet without I, O, 0 and 1, printed in groups of four;
`normalize_code` in [billing.py](../../server/app/services/billing.py) accepts
lowercase, spaces and missing dashes. Only `sha256(code)` and the last four
characters are stored.

`redeem_card` locks the org balance, then the card, and returns no row for a
missing, used, void, expired or other-currency card, so callers cannot tell the
reasons apart. On success it marks the card redeemed, credits the balance and
writes a `redeem` entry. `platform_adjust_balance` turns `set` into a delta,
requires a reason, and writes an `adjust` entry. Both restore the caller's
`app.current_org`. A trigger makes `redeemed` and `void` final, and the
runtime role has no column grant that could mark a card redeemed.

`require_funds` checks positive available funds at extraction submission in
[tender_jobs.py](../../server/app/services/tender_jobs.py) and drafting submission in
[card_generation.py](../../server/app/services/card_generation.py). This check is advisory: every model
request must also pass `JobExecution.admit` in
[execution.py](../../server/app/jobs/execution.py), including split batches,
gap filling and transient retries. The execution context is shared by model
capabilities; extraction and card generation use the same context and
`accounted_call` in [calls.py](../../server/app/providers/calls.py).

### Admission and the spending bound

For a platform call, `HTTPExtractor.reservation` in
[llm.py](../../server/app/providers/llm.py) computes an upper charge `R` at the
catalog sale prices: input allowance is the UTF-8 byte length of the whole JSON
request plus 4,096 framing tokens; output allowance is the request's output
largest top-level output token limit actually sent, multiplied by `n` when
present. Every recognized limit and `n` must be a positive integer; choosing a
smaller alias cannot lower the reservation. Request construction and catalog
validation prohibit output-limit options as defined in
[llm-providers.md](llm-providers.md#how-it-works). The amount is rounded upward to
eight decimal places. Unknown or invalid sale prices fail with
`billing_price_unavailable`, rather than authorizing an unpriced call.

Admission locks the job and then its org balance. It checks attempt ownership,
the job's cumulative call count, its settled charges plus outstanding
reservations, and the org's balance minus **all** outstanding reservations.
It commits a `vendor_calls` row before sending anything. Job ceilings apply
across automatic and explicit retries; `--retry` cannot erase them. The call
ceiling scales with the first pass: the adapter reports its planned batches,
and the ceiling is the larger of the fixed minimum and the batches times the
per-batch allowance, so a long tender can finish while halving loops stop. Settings
and defaults are in [development.md](../guides/development.md#configure-job-guards).
Exhaustion fails with `job_call_limit_exceeded`, `job_charge_limit_exceeded`,
or `insufficient_balance`; no new call is sent. Even calls with unknown usage
consume the call ceiling and retain their monetary reservation.

The concurrent overdraft bound is **zero** when each call's actual charge
`C <= R`: org admission serializes against the balance row, and settlement
replaces a reservation with the actual deduction in one transaction. The
number of workers, jobs and batch slots does not change that bound. Reducing
a balance through an operator adjustment is an independent authorized write,
not a model admission.

This bound requires a text endpoint with byte-level tokenization, framing
within the allowance, and enforcement of the requested output limit. A vendor
report exceeding `R` is still recorded and fully charged, and fails the
attempt with `call_charge_bound_exceeded`. For an incompatible vendor, the
possible overdraft is bounded by the sum of `max(0, C - R)` for already
admitted calls; no provider-independent fixed monetary guarantee is claimed
for a service that ignores its token limits. Verify that contract when adding
an endpoint; token counts and per-call reservations make violations observable.

### Immediate, idempotent settlement

The adapter settles a response immediately after reading its token usage,
before output parsing or citation checks. Refused, truncated and malformed
answers, and error envelopes carrying usage, follow the same path. One
transaction inserts `UsageRecord`, deducts `charge`, writes its balance entry,
settles `vendor_calls`, and updates `jobs.result.cost` from all usage for that
job. Later failure, cancellation or takeover cannot erase those charges.
Vendor USD cost remains distinct from the platform charge in billing currency.

Migration [0016_vendor_call_guards.py](../../server/migrations/versions/0016_vendor_call_guards.py)
adds tenant-scoped reservations and the unique `(org_id, job_id, run_id,
call_id)` usage key, with a composite foreign key to the admitted call. A
balance entry also has a unique usage reference. A transient accounting write
is retried at most three times with the same identifiers, only for invalidated
connections, connection SQLSTATEs, serialization failures and deadlocks; an
ambiguous commit cannot deduct twice. Unrecovered driver errors, pool timeouts,
other SQLAlchemy failures and missing accounting parents set the attempt stop
flag before any unknown-state write and surface as `usage_accounting_failed`.
Failure to persist that marker also stops admission; the existing `pending`
reservation remains held. Admission rechecks the flag after waiting for locks.
Waiting extraction batches stop, while already sent requests drain through
settlement even after the flag is set.
Legacy usage is left intact; the job attribution and ceilings cover calls
admitted through this mechanism.

Failed redemptions are recorded in the org audit log, committed separately,
and ten within an hour lock redemption for that org. Startup compares every
stored balance currency with `BID_BILLING_CURRENCY` and refuses a mismatch.

## Pitfalls

- Reservations reduce available funds without manufacturing a ledger charge.
  Timeouts, missing/invalid usage, and requests interrupted by process death
  remain `pending` or `unknown`; neither lease expiry nor retry releases them.
  Reconcile against vendor records before settling an unresolved call. There
  is no automatic reconciliation command. A crash after the vendor responds
  but before the settlement commits can leave this unresolved state: local
  storage cannot atomically commit a third-party HTTP request.
- Graceful cancellation drains already admitted requests through accounting,
  within the vendor timeout. It cannot protect against SIGKILL or permanent
  database failure; durable reservations prevent spending that money again.
- Changing `BID_BILLING_CURRENCY` with balances in place stops startup. Settle
  or adjust balances to zero in the old currency first, then clear them with
  an operator-owned migration; nothing converts amounts.
- Card codes cannot be recovered. Keep the exported CSV safe, and void cards
  that are lost.
- Money columns are `numeric(18, 8)`. Pass amounts to the functions with an
  explicit `CAST(... AS numeric)`; a float parameter does not match.
- On macOS, `/tmp` is a symlink, so CLI output paths under it are refused like
  any symlinked parent. Use `/private/tmp` or another real directory.

## Code

- [0012_prepaid_billing.py](../../server/migrations/versions/0012_prepaid_billing.py)
- [execution.py](../../server/app/jobs/execution.py): `JobExecution`, `job_cost`;
  [calls.py](../../server/app/providers/calls.py): `accounted_call`.
- [server/app/services/billing.py](../../server/app/services/billing.py); `create_cards`, `void_card`, `adjust_balance` in [server/app/services/platform.py](../../server/app/services/platform.py)
- [web/src/views/Cards.vue](../../web/src/views/Cards.vue), [web/src/views/OrgBilling.vue](../../web/src/views/OrgBilling.vue), [web/src/views/OrgLogin.vue](../../web/src/views/OrgLogin.vue)
- Tests: [test_vendor_call_guards.py](../../server/tests/test_vendor_call_guards.py), [test_billing_db.py](../../server/tests/test_billing_db.py), [test_billing.py](../../server/tests/test_billing.py), card and balance cases in [test_platform_api.py](../../server/tests/test_platform_api.py), [web/e2e/platform.spec.js](../../web/e2e/platform.spec.js)
