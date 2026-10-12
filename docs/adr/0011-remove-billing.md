---
kind: adr
---

# 0011 Usage records without billing, prices or task budgets

Status: accepted. Date: 2026-10-11.

## Context

Provider calls need attribution, durable resource counts and operational limits.
Money features couple model configuration, work admission and human review to
balances, prices and financial reservations. These features are outside the product.

## Decision

Supersede [ADR 0002](0002-prepaid-billing.md) and
[ADR 0007](0007-task-budget-reservations.md). Remove billing, recharge cards,
prepaid balances, provider/model prices, monetary task budgets and monetary agent
limits from the application, CLI, web console and deployment configuration.

Record each actual Provider dispatch with provider, model, duration and resource
usage. The Result 5.0 envelope retains its seven keys. Its `cost` field contains
exactly `calls`, `llm_tokens`, `ocr_pages`, `search_requests`, `browser_units` and
`unresolved_calls`; all are nonnegative integer counts. No money value, currency,
price, payer, reservation or financial ledger belongs to this contract.

Keep provider catalogs, organization model selection, encrypted credentials,
org isolation, roles, scopes, human confirmation, explicit outbound authorization
and nonmonetary limits on calls, tokens, pages, requests, concurrency and duration.
Read-only `data.preflight` reports planned calls, resource estimates, configuration
blockers and uncertainty. It creates no calls or writes and makes no complete-run
guarantee. Failed or uncertain dispatches remain visible in durable call records;
retries and cancellation cannot erase historical resource use.

## Consequences

Model configuration and task creation require no financial setup. Platform usage
views and exports show attributed counts only. There are no recharge, balance,
price or task-budget APIs, commands or navigation entries. Historical migrations,
changelog entries and superseded ADRs remain records of earlier decisions.
Tender prices, opening-price calculations and price redaction are business content
and retain their separate confidentiality and human-review boundaries.
