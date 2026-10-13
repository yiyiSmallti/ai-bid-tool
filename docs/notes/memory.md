---
kind: reference
---

# Memory mechanisms

Memory stores reusable drafting guidance separately from evidence and human decisions.
The enabled `library` scope belongs to the installation. The wire contracts and
remaining scope enablement work are defined in [the memory plan](../plan/memory.md).
The installation identity boundary is defined by [ADR 0012](../adr/0012-single-user-installation.md).

## Lifecycle and authority

A new entry starts as a candidate. A local human session approves or rejects the
exact current revision. Approval records `confirmed_at`; actor attribution records
session, token, agent and job context without a human account identifier. Tokens
and agents cannot approve, disable, delete or review evaluation samples, including
when a caller fabricates human scopes. Every mutation rechecks live authority.

Editing an active entry appends a candidate and withdraws the previous effective
rule. A token may edit only its own candidate, provided that entry has never been
active. Human disablement and logical deletion preserve historical revisions and
model inputs. Expiry uses server time and never changes historical snapshots.

The application checks these transitions in
[`crud.py`](../../server/app/memory/crud.py). Independent database checks live in
`memory_parent_gate`, `memory_revision_gate` and `memory_revision_complete` in the
[baseline functions](../../server/migrations/baseline/00_functions_core.sql).
Revisions, feedback and retrieval items are immutable; only the declared lifecycle,
evaluation review and admitted-call settlement paths may change their mutable rows.

## Retrieval and cache validity

[`retrieval.retrieve`](../../server/app/memory/retrieval.py) selects only current,
approved, unexpired entries without a deletion tombstone. Exact text, keyword and
tag matching use normalized text. Rules precede preferences; deterministic ranking,
conflict exclusions, hit limits and context limits are recorded in the result.
Memory remains guidance and cannot satisfy evidence or human confirmation gates.

A persisted retrieval fixes the selected revision IDs, content and sent-text hashes,
policy versions, omitted entries and a `library` epoch. The epoch table is keyed by
scope alone. Changes to effective entries advance that epoch, so a new approval also
invalidates a previous empty retrieval. The earliest expiry across the complete
active library bounds validity, including entries excluded from the selected hits.
`require_current` rechecks this manifest before a vendor call is admitted.
`memory_manifest_current` supplies the corresponding database check.

Retrieval queries, snapshots and exact prompts are encrypted with their object IDs
bound inside each envelope. A dry-run retrieval does not persist a retrieval, job,
audit or usage row. Historical retrieval reads report explicit stale reasons rather
than rebuilding the original result from current entries.

## Calls and feedback

`attach_call` records the admitted retrieval, exact prompt hash, requirements, job,
run and call IDs. `settle_call` distinguishes completed calls from calls with unknown
outcomes. Replacement attempts preserve an unknown predecessor outcome.
`memory used` reports what was admitted to each actual call; it does not claim that
a model relied on each rule internally.

Human rejection or relevant editing of a model-derived response card creates
sanitized feedback, an evaluation sample and a durable candidate job in the card
transaction. Human confirmation creates only an evaluation sample. The sanitizer
removes confidential values, identifiers, contacts, prices and evidence quotations
before reusable guidance is proposed. Candidate generation is deterministic and
never approves its own proposal.

Queue failure preserves the card decision and returns a durable job ID. Explicit
retry reuses the recorded event set and immutable submission provenance. The original
submission actor must retain its authority; a retry may narrow recorded grants but
cannot enlarge them. Lease and run checks fence stale workers.
Evaluation acceptance remains separate from memory approval. Feedback envelopes
bind their event or sample IDs, and hash checks reject substituted summaries.
These paths are defined in [`feedback.py`](../../server/app/memory/feedback.py),
[`candidates.py`](../../server/app/memory/candidates.py),
[`memory_candidate.py`](../../server/app/jobs/memory_candidate.py) and
[`safety.py`](../../server/app/memory/safety.py).

## Interfaces

The HTTP adapters are [`api/memory.py`](../../server/app/api/memory.py); CLI commands
are [`bid_cli/memory.py`](../../cli/bid_cli/memory.py). Request and result models
have their single definition in
[`memory_contracts.py`](../../server/app/schemas/memory_contracts.py) and are exposed
by `bid schema`. Task provenance requires task read authority and is redacted when
that authority is absent. Bounded browse, exact detail and feedback recovery are
specified in [memory management](memory-management.md).
