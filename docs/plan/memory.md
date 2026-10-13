---
kind: plan
---

# Memory storage, retrieval and feedback

The memory workstream supports [roadmap](roadmap.md) M01–M03 under the installation
boundary in [ADR 0012](../adr/0012-single-user-installation.md). The runtime contracts
have one home in [`memory_contracts.py`](../../server/app/schemas/memory_contracts.py).
Lifecycle, authority, retrieval validity and feedback mechanics have one home in
[memory mechanisms](../notes/memory.md). Management reads have one home in
[memory management](../notes/memory-management.md).

## Goals and boundaries

Provide reusable rules and preferences with explicit candidate creation, exact
human approval, disablement, expiry, retrieval and admitted-call records. Human
feedback may propose new candidates and installation-local evaluation samples.
Memory guides working methods; it cannot become parameter evidence, replace real
materials, or satisfy evidence confirmation and export gates.

Scope and epoch requirements follow [memory mechanisms](../notes/memory.md#retrieval-and-cache-validity).
Requests and results do not carry organization, human account or ownership identifiers.

The complete enabled flow is candidate creation → human approval → PostgreSQL
keyword retrieval → response-card drafting → per-call input manifests → human
feedback → candidate proposals and separate evaluation review. Candidate conversion,
CRUD and keyword retrieval do not make model calls; response-card drafting uses the
configured LLM provider.

## Interfaces

The CLI and HTTP adapters linked from [memory mechanisms](../notes/memory.md#interfaces)
provide create, show, update, decisions, disable, delete, list and history operations.
Retrieval accepts explicit scopes, query text, keywords, tags, mode, hit limits and
context limits. Preview and persisted retrieval share selection semantics. Persisted
retrievals expose historical validity; job memory reads expose exact admitted calls.

Candidate submission names a bounded, unique event set and supports dry-run and
explicit retry. Evaluation review names an exact review revision, action and reason.
Write bodies cannot supply effective status, confirmation, source origin or actor
identity. The server derives those fields from the authenticated action and fixed
provenance.

## Subsequent scope enablement

`global` and `project` are reserved contract values and must return an explicit
unavailable error until their complete boundaries are implemented. They must never
silently select `library`. Project contracts require an exact task binding.
Enabling another scope requires storage constraints, live task access where
applicable, human decision gates, epoch invalidation, historical reads and full
admitted-call provenance before a consumer may use it.

Vector and hybrid modes require configured embedding support. The embedding
contracts bind provider configuration, provider, model, version, model revision,
dimensions and metric. An implementation must account for usage, stale revisions,
index rebuilds and deterministic conflict handling; it must not silently fall back
to keyword mode. Semantic retrieval and reranking, model summaries and historical
card backfill are separate work.

Other consumers, including extraction, checking, scoring and agents, must use the
same retrieval and admitted-call protocol rather than reading tables directly into
prompts. Their evidence and human confirmation gates remain independent.

## Acceptance

Existing HTTP flows must cover candidate lifecycle, exact revision conflicts,
concurrent conflicting approvals, expiry, empty-result epoch invalidation, token
candidate ownership, human-only decisions, sensitive-content rejection, immutable
history, encrypted snapshot binding and task-provenance redaction. Feedback flows
must cover saved dispatch failures, explicit retry, partial batches and stale
worker publication fences. Evaluation review must not approve memory.

Database acceptance uses the application role against the immutable baseline,
including raw mutation attempts. Fixed-scale management acceptance must measure
real query plans, response bounds, query counts and mutation paths. Repeatable
artifacts belong under `data/work`; tests and artifact paths are linked from
[memory management](../notes/memory-management.md#implementation-and-verification)
and [`test_memory_storage.py`](../../server/tests/test_memory_storage.py).
