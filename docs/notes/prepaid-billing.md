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

`Processor.record_usage` inserts each usage record and, for platform-billed
calls, deducts `charge` and writes a `usage` entry in the same transaction.
`require_funds` refuses platform-billed extraction with 402
`insufficient_balance` when the balance is not positive, at submission in
[main.py](../../server/app/api/main.py) and again before the vendor call in
[processor.py](../../server/app/jobs/processor.py). Failed redemptions are
recorded in the org audit log, committed separately, and ten within an hour
lock redemption for that org. Startup compares every stored balance currency
with `BID_BILLING_CURRENCY` and refuses to run on a mismatch.

## Pitfalls

- A running job can leave the balance negative; only the next job is refused.
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
- [server/app/services/billing.py](../../server/app/services/billing.py); `create_cards`, `void_card`, `adjust_balance` in [server/app/services/platform.py](../../server/app/services/platform.py)
- [web/src/views/Cards.vue](../../web/src/views/Cards.vue), [web/src/views/OrgBilling.vue](../../web/src/views/OrgBilling.vue), [web/src/views/OrgLogin.vue](../../web/src/views/OrgLogin.vue)
- Tests: [test_billing_db.py](../../server/tests/test_billing_db.py), [test_billing.py](../../server/tests/test_billing.py), card and balance cases in [test_platform_api.py](../../server/tests/test_platform_api.py), [web/e2e/platform.spec.js](../../web/e2e/platform.spec.js)
