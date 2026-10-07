# Organization memory

## Problem

Reusable drafting guidance must remain separate from evidence and human decisions. Copying a
rejected model answer into an active rule would amplify the original error. A cache tied only to
selected memory IDs also misses newly approved rules when the previous retrieval had no hits.

## Usage

The [management pages](memory-management.md) provide bounded org browse, exact
revision detail and task feedback recovery over these services.

Create an organization candidate with `bid memory add --input FILE --json`, inspect it with
`memory show` and `memory history`, then approve its exact revision through an administrator human
session using `memory approve --id UUID --input FILE`. Editing active content creates a new candidate
and withdraws the old rule. Disable and delete are human administrator operations; deletion preserves
history. Request shapes and every command are defined by the [memory contract](../plan/memory.md#interfaces)
and exposed by `bid schema`.

Use `memory retrieve --input FILE --dry-run` to preview selection without creating a retrieval, job,
audit or usage row. A persisted retrieval has a historical show endpoint. `memory used --job UUID`
reports the context admitted to each vendor call, including unsuccessful calls; it does not claim
that the model relied on a particular rule internally.

Human changes to model-derived cards create feedback and evaluation samples. A dispatch warning
contains a durable job ID; inspect feedback and replay selected event IDs through `memory candidates
run`. Do not repeat the card decision. Administrators review samples inside the organization;
accepting a sample grants neither memory approval nor permission to share it externally.

## How it works

The [memory migration](../../server/migrations/versions/0037_memory.py) binds all business rows to an
organization using forced RLS and composite foreign keys. Revisions, feedback, retrievals and input
snapshots are append-only. Database predicates independently enforce the human administrator gate,
exact current revision, source bindings and worker attempt lease. API tokens cannot receive the
human-only scopes.

[Retrieval](../../server/app/memory/retrieval.py) restricts PostgreSQL queries to current, active,
undeleted, unexpired organization revisions before scoring or limiting. Normalized text and tags
use NFKC, case folding and collapsed whitespace. The approved [scoring and priority rules](../plan/memory.md#retrieval-and-precedence)
resolve explicit conflict keys, retain whole entries and preserve stable ordering. The service does
not make embedding calls and rejects unavailable modes explicitly.

Scope epochs advance atomically when the effective set changes. A retrieval also fixes the earliest
expiry of the complete accessible active set. Drafting snapshots fix those dependencies, exact
revision hashes and policy versions, including an explicit empty memory context. Admission and
publication check them again. Changed memory blocks confirmation of an unreviewed model draft;
a previously confirmed human decision remains valid with a warning. Evidence eligibility checks
remain independent.

[Drafting](../../server/app/providers/drafting.py) sends separate rule and preference blocks and
includes their size in batching and reservations. Memory cannot become a material reference.
`JobExecution.admit` stores the per-request manifest in the same transaction as `VendorCall`.
Settlement attaches the matching usage record atomically, even after cancellation or revoked
membership. Retries and split batches each have their own call ID. Card and draft views retain
only authorized IDs and hashes; encrypted prompts are not returned.

[Feedback](../../server/app/memory/feedback.py) captures adjacent human card revisions before an
edit clears model provenance. It writes the bounded sanitized event, evaluation sample and candidate
outbox in the original transaction. The deterministic candidate worker rechecks current grants and
its lease before publishing. Unique source and generator keys prevent duplicate candidates. It
never produces a vendor usage row.

## Pitfalls

Memory contains reusable methods, never proof that a product satisfies a requirement. Known sensitive
values and redaction detections are rejected regardless of the task's model-redaction switch.
Feedback, queries and sent snapshots use organization/record-bound encrypted envelopes; audits contain
hashes and identifiers only. Pattern detection has limits, which is why candidates require review.

User, project and global scopes remain unavailable until their ownership and publication boundaries
are implemented. A scope is never silently mapped to the organization. The optional embedding table
is absent; any later vector migration must check that pgvector is installed before declaring a vector
column. A model or dimension choice requires the provider and billing contract first.

Migration rollback retains historical tables. Reverting application code must not delete the audit
trail. PostgreSQL acceptance tests use two organizations and synthetic transports; their reproducible
reports belong under `data/work/memory-validation/`.

## Code

- [Schemas](../../server/app/schemas/memory_contracts.py): HTTP, CLI, retrieval and provider boundaries.
- [Storage](../../server/app/models/memory.py) and [migration](../../server/migrations/versions/0037_memory.py): tenant keys and SQL gates.
- [CRUD](../../server/app/memory/crud.py), [access](../../server/app/memory/access.py), [safety](../../server/app/memory/safety.py): authorization, revisions and sensitive-value handling.
- [Retrieval](../../server/app/memory/retrieval.py) and [card generation](../../server/app/services/card_generation.py): fixed context, epochs and call lineage.
- [Call accounting](../../server/app/jobs/execution.py): atomic admission and settlement.
- [Feedback](../../server/app/memory/feedback.py), [candidates](../../server/app/memory/candidates.py), [worker](../../server/app/jobs/memory_candidate.py): outbox, deterministic proposals and recovery.
- [API](../../server/app/api/memory.py) and [CLI](../../cli/bid_cli/memory.py): discoverable entry points.
- [Storage acceptance](../../server/tests/test_memory_storage.py), [API acceptance](../../server/tests/test_memory_api.py), [drafting acceptance](../../server/tests/test_memory_drafting.py), [feedback acceptance](../../server/tests/test_memory_feedback.py), [CLI snapshots](../../server/tests/test_memory_cli.py).
