---
kind: plan
---

# Requirement confirmation and manual entry

This plan defines review of the extracted tender interpretation, independently of
response and Evidence decisions. Installation identity and scope boundaries follow
[ADR 0012](../adr/0012-single-user-installation.md). Mechanism definitions live in
[requirement-confirmation.md](../notes/requirement-confirmation.md); request and
response shapes live in [requirement_confirmation.py](../../server/app/schemas/requirement_confirmation.py).

## Goal and boundary

The installation human must inspect a requirement against its pinned original source,
accept that exact text, category, starred flag and structured condition, and see the
next action. Confirmation must not imply extraction completeness, response approval,
material authenticity or rubric approval. Entry and confirmation may be performed
by the same human.

Provisional preparation remains available before requirement confirmation. Response
confirmation, comply-only disposition, rubric acceptance and score consumption must
use the current accepted requirement binding. Draft assembly must represent every
saved requirement once, as an accepted row, comply-only item or explicit gap.
Checks may diagnose partial drafts while identifying provisional interpretations.
Historical accepted artifacts remain readable with validity information.

Manual entry must recover an omitted item or an original rejected extraction item
from a real parsed PDF page or complete Word location. Preview is read-only and
requires a unique exact source span. Save requires the observed preview hash,
request UUID and reason, and creates an unconfirmed requirement. Manual entry must
not rewrite the original extraction receipt, counts, rejected summary or costs,
and must not dispatch model work.

No extraction-completeness certification, delete/exclude/free-edit operation,
automatic semantic repair, automatic confirmation or multi-job union is introduced.

## Identity, source and history

Stable identity is task, extraction job and requirement. Re-extraction creates a new
scope; identical fingerprints across jobs do not transfer approval. An explicitly
selected manual scope must not replace default latest-model discovery.

The canonical review hash must bind the complete interpretation and verified source
pin, including document hash, complete chunk hash, complete location, literal quote,
character offsets and verifier version. An ambiguous, absent, normalize-only or
unverified source must fail before saving. Preparation must bind the complete
extraction set even when the provider receives a selected subset. Acceptance must
also bind the current review revision and hash.

Review sets and individual reviews have independent revisions. Decisions must append
immutable events containing encrypted snapshots and reasons. Current review pointers,
parent keys, source predicates and immutable-history checks must remain enforced by
[00_functions_rubric.sql](../../server/migrations/baseline/00_functions_rubric.sql)
(`requirement_event_pointer`, `requirement_review_guard`, `requirement_pin_valid`)
and [40_foreign_keys.sql](../../server/migrations/baseline/40_foreign_keys.sql), with
[review models](../../server/app/models/requirement_confirmation.py).

Confirmed state retains `confirmed_at`; other states clear it. Audit and run identity
retain actor kind, token and agent/job attribution. A request UUID is globally unique
across actions and binds the task and full request hash. An identical authorized replay
returns the original event IDs with the current view; changed input or cross-action
reuse conflicts. No raw request text, reason, token or quotation enters audit indexes
or durable task events.

Source mutation must invalidate the prior binding permanently, including a later
change back to the same content. It must commit source changes, review events, audit
and task-event head updates atomically. Savepoint rollback must remove all those
changes together. The invalidation audit must prove ownership of the actual writer
transaction, including a savepoint transaction, and reject replay of a committed event.
Automatic invalidation must never fabricate human confirmation.

## Human authority and concurrency

Services must recheck current scopes using `task_access.access`, `live_actor` and
`Identity.require`. Transactions have no organization parameter. The installation
human alone may use `req:confirm` and `req:manual`; API tokens, agents and workers
must fail both API and database human-decision gates. Citation repair retains its
human-only `req:extract` and `evidence:confirm` gate.

Single and batch decisions require the displayed revision/hash, reason and request
UUID. Batch confirmation is an explicit inspected selection of at most 100 items,
with `reviewed_each=true`; it must be atomic when any item is stale or unknown.
No default select-all or automatic conflict retry is allowed.

Lock order must be task → review set → requirements/chunks/reviews in UUID order →
dependent cards → task-event sequence. Quote repair must use the same ordering.
Deferred publication must reject missing, duplicate or stale accepted input bindings.
Already dispatched calls still settle actual usage; cancellation, lease loss and
changed input must not publish stale output.

## Entry points and presentation

The review/manual endpoints and CLI commands defined by
[api/requirement_confirmation.py](../../server/app/api/requirement_confirmation.py)
and [cli/requirement_confirmation.py](../../cli/bid_cli/requirement_confirmation.py)
must expose explicit task/extraction scopes. Input files keep reasons and quotations
out of command history. Cursor validation must bind actor access fingerprint,
task/job or requirement, query, set revision and committed event watermark.

The opt-in `view=requirement-review` board and progress paths require an explicit
extraction ID. Pending, legacy and invalidated requirements occupy the separate
`requirement_review` bucket. Response state remains visible independently. Invalid
sources add `invalid_citation` and source-repair guidance. Once a requirement is
confirmed, the normal response projection selects its bucket. Counts must partition
saved requirements without counting rejected summaries or implying completeness.

Next-action hints expose the action and whether the current actor can perform it.
They contain no user identity, assignment, owner, role or membership guidance.
Hints guide navigation; endpoints must independently enforce authority.

The console must provide bounded pages, pinned sources, exact quote highlighting,
interpretation, provenance, immutable history, separate requirement/response actions,
a rejected-item recovery picker and explicit batch review. Conflicts retain unsaved
input and require a refreshed read. Empty results, failed source previews, stale
cursors, offline recovery and denied scopes have explicit states. Short-lived
original/preview download rules remain authenticated.

## Acceptance requirements

The retained API, PostgreSQL, CLI and browser entry points must verify:

- Every route preserves task/document/job bindings, source scope checks and uniform
  missing-parent errors; tokens and nonhuman service identities cannot make decisions.
- PDF and Word source previews reject normalized-only, absent, ambiguous, wrong-position,
  unverified and oversized input without writes. Rejected lineage stays immutable.
- Confirmation, reopen, changed source and change-away-and-back maintain immutable
  history. Stale batches and changed request UUID reuse fail atomically.
- Unconfirmed/invalidated interpretations prevent response acceptance and complete
  consumption while preparation and diagnostic checks remain available.
- Current source and material hashes fence generation and deferred draft publication;
  historical draft/check/score/export snapshots report stale state after invalidation.
- Board counts, next actions, cursor watermark and reconnect reload reflect only
  committed state. Source and audit rollback leave no durable notification.
- Both CLI transport modes preserve the seven-key Result and cost/exit contracts;
  malformed inputs fail before transport and decisions never retry silently.
- The human can recover a rejected or omitted item from its actual source, review it,
  confirm the response and produce a draft through the retained end-to-end flow.

Reproducible acceptance artifacts belong in gitignored `data/work/`, outside `docs/`.
The fixtures must use synthetic providers and real parsing, authentication, transactions
and human-state triggers. No trigger bypass or fabricated confirmed fixture is permitted.
