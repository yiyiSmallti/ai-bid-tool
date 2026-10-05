---
kind: adr
---

# 0002 Prepaid balances (预付余额) and recharge cards

Date: 2026-10-01. Status: accepted.

## Context

Calls to platform-default models accrue receivables at the selling price and need a payment mechanism. Settlement is prepaid: orgs (organizations/tenants, 单位) redeem recharge cards (充值卡密); platform administrators can generate cards or adjust balances directly. Deployment operators choose the currency. There is no overdraft allowance.

## Decision

- Each org has one balance (`org_balances`) and an append-only ledger (`balance_entries`), both under org isolation. The balance derives from the ledger; `org_balances` is the current value maintained under a lock.
- Cards live in the global `platform_cards` table, the third approved global table: an unredeemed card belongs to no org. Store only its SHA-256 hash and last 4 characters; plaintext appears only in the generation response and export file.
- Credits use only two functions owned by `bid_platform_fn`: `redeem_card` locks the balance before the card to serialize concurrent redemption of one card; `platform_adjust_balance` converts a “set to” operation into a delta and requires a reason. The runtime role can only generate cards and void unused cards. Redeemed and voided are terminal states enforced by triggers.
- Charges and usage records commit in one transaction. The balance must be greater than 0 to submit a platform-billed job and is checked again before processing starts. There is no estimated-charge reservation.
- `BID_BILLING_CURRENCY` defines the currency for selling prices, receivables, balances, and card face values. Cost prices remain the provider's USD prices. The service refuses to start if existing balances use a different currency from configuration.
- Redemption is a financial operation: only org administrators may redeem; API tokens can never receive `billing:redeem`.

The no-reservation decision above records the original settlement contract. Current per-call reservation and admission behavior is maintained in [Prepaid billing](../notes/prepaid-billing.md#admission-and-the-spending-bound).

## Tradeoffs

- There is no overdraft allowance, but a call's cost is unknown in advance, so a running job can still make the balance negative. Subsequent jobs are rejected until recharge covers the deficit. Preventing all negative balances would require estimated-charge reservations, adding complexity and false rejections; this is not adopted here.
- Cards cannot be recovered: a lost card must be voided and reissued. In return, a database leak does not reveal usable cards.
- Redemption failures do not distinguish causes and are rate-limited by org to prevent enumeration. Users receive only a generic failure message.
- Currency is deployment-wide rather than per org, avoiding cross-currency settlement and exchange rates. Balances must be resolved before changing currency.
- Functions restore the caller's previous org context instead of clearing it so an org request can continue writing audit entries in the same transaction.
