---
kind: adr
---

# 0007 Task liability and prepaid reservations

Status: accepted. Date: 2026-10-05.

## Context

[ADR 0002](0002-prepaid-billing.md) originally allowed in-flight spending without
estimated holds. The durable per-call prepaid reservation mechanism and the
approved [task-budget contract](../plan/budget.md) require concurrent work to
respect both prepaid availability and task liability.

## Decision

Supersede ADR 0002's no-reservation and unbounded in-flight debit decisions.
Reserve task liability and prepaid charge in the existing VendorCall transaction,
using Task → Job → OrgBalance locks. Settle actual usage once and preserve unknown
reservations through cancellation, lease expiry and retry. BYOK liability uses
fixed declared USD prices; absorbed and local operations have explicit zero
liability. Humans alone change caps with optimistic revision checks and audit.

Keep all other prepaid decisions, including deployment currency, controlled
credit functions, immutable ledgers and token restrictions. Operational behavior
is specified in [Task budgets](../notes/task-budgets.md).

## Consequences

Concurrent calls whose actual usage respects the reserved bound cannot exceed
the task cap. Vendor bound violations still retain full cost and stop further
calls; unknown outcomes require reconciliation before their holds can be removed.
Conservative estimates can refuse a call whose eventual cost would have been
lower. Budget changes cannot resolve missing prices, prepaid insufficiency or
job limits. No subscription or monthly quota policy follows from this decision.
