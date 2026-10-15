---
kind: reference
---

# Requirement confirmation and manual entry

## Problem

A verified citation proves where a quotation occurs. It does not prove that an
extracted interpretation is faithful or that extraction found every requirement.
Requirement review accepts the exact interpretation and source independently of
response, Evidence and rubric decisions. The approved boundaries and
payloads are in [the B02 contract](../plan/requirement-confirmation.md).

## Usage

The task's requirement-review workspace and `bid req review-list` select an explicit
extraction scope. `bid req show` supplies its review revision, hash, source pin and
the next action and whether the installation human can perform it. `bid req confirm` and `bid req reopen` consume a JSON input
file carrying that revision/hash, a reason and a caller-generated request UUID.
The installation human can confirm up to 100 explicitly inspected items with
`bid req confirm-batch`; entry and confirmation may be performed by the same human.

`bid req rejected` reads the original, incomplete extraction receipts.
`bid req add --input FILE --dry-run` verifies a complete manual entry at an existing
parsed PDF page or Word block. Saving with `bid req add --input FILE` requires the
preview hash and leaves the requirement unconfirmed. An omitted extraction ID
creates an explicitly selected manual set; an existing ID appends to that set with
its observed revision. Manual sets never replace the default latest model result.

The installation human may write with `req:confirm` or `req:manual`. Tokens,
agents and workers cannot receive these human-only capabilities. Read access
continues to require the retained task and source scopes.

## How it works

The review identity combines task, extraction and requirement IDs. A canonical
hash binds content and a verified source pin: document hash, complete chunk hash,
full location, literal quotation, Python character offsets and verifier policy.
The deterministic locator is shared with extraction; a manual quote must equal the
located original slice. Ambiguous, missing, normalize-only and unverified inputs
cannot be saved as requirements.

Review sets and current reviews have separate revisions. Immutable events retain
encrypted snapshots/reasons; encrypted request receipts make identical authorized
retries return their original event IDs and the current view. Cross-action reuse
of a request ID conflicts. Database parent keys and human-state triggers enforce task binding and the
same human-only authority as the services. Source mutation records
invalidation permanently, including when content is later changed back. The source
trigger owns `requirement.repair_citation` system audit rows, bound to immutable
`source_repair` events. The human repair operation records its input and quotation
hashes under `requirement.citation_repair.executed`.

Preparation binds meaning, source and membership without requiring human approval.
For selected response generation, the preparation manifest binds the complete
extraction scope while the provider receives only the selected requirements.
Acceptance binds the current review revision/hash as well. Response confirmation,
comply-only disposition, whole-rubric confirmation and score consumption require
current confirmation. Draft assembly partitions every saved requirement into an
accepted row, comply-only item or explicit gap. Checks can diagnose partial drafts;
unconfirmed tender interpretations remain provisional. Export freshness follows
the fixed draft and review inputs. Existing evidence, prototype and retained database confirmation gates remain
independent. Review domains classify response tables.

The opt-in board places pending review in `requirement_review`, names the confirmation or recovery action, and keeps response completion separate. `mine` selects decisions the current actor can perform: the installation human
can act across review domains, while tokens and agents cannot make human decisions. Legacy
boards retain their existing enums and show pending requirement review as a gap.
Default-board state reads skip full pins only for missing or nonconfirmed review
metadata; confirmed rows still recheck current meaning, complete source and policy.
Explicit requirement-review views and all preparation/consumption manifests retain
full current hashes. Both board paths reuse the exact citation batch's source spans
within the same read, without changing PDF page or complete Word-location matching.
Metadata-only durable task events invalidate snapshots in the committing
transaction; audit payloads contain IDs and hashes, never reasons or quotations.
Source-invalidation audit binding includes the actual savepoint writer transaction,
not only the top-level transaction ID. Rolling back a savepoint removes its source
change, review event, audit and task-event head increment together.

## Pitfalls

- Confirmation is not extraction-completeness certification. Rejected receipts
  are not saved requirements and never enter the progress denominator.
- A failed extraction retains its status, rejected summaries and cost. Manual
  recovery adds lineage without modifying that receipt or scheduling model work.
- A saved requirement cannot be deleted, excluded or freely edited through this
  slice. Disputed content remains unconfirmed; citation repair retains its existing
  human-only gate and invalidates the affected requirement binding.
- Historical accepted artifacts remain readable as stale. Reconfirmation does
  not approve an old response or make an old draft/report current automatically.
  The deferred draft gate validates a newly inserted publication; it does not
  prohibit later source changes because a historical draft now needs reassembly.
- Migration seeds `legacy_unconfirmed` without invented historical approval.
  Existing rows require the data-encryption key for their encrypted baseline.
  Stop writes and restore a gate-aware release for recovery; do not run a binary
  that ignores requirement review. Ciphertext rotation uses the existing migration
  owner path and cannot change review metadata or append a human decision.

## Code

- [Runtime contracts](../../server/app/schemas/requirement_confirmation.py),
  [models](../../server/app/models/requirement_confirmation.py).
- [00_functions_rubric.sql](../../server/migrations/baseline/00_functions_rubric.sql):
  `requirement_review_guard`, `requirement_event_pointer`, `requirement_pin_valid`
  and `requirement_draft_gate` enforce review history, source pins and publication.
- [Review/manual services](../../server/app/services/requirement_confirmation.py),
  [source pins](../../server/app/services/requirement_source.py),
  [consumer bindings](../../server/app/services/requirement_consumption.py),
  [HTTP routes](../../server/app/api/requirement_confirmation.py).
- [Board projection](../../server/app/services/requirement_board.py),
  [CLI commands](../../cli/bid_cli/requirement_confirmation.py),
  [native review workspace](../../desktop/native_requirements.go).
- [API/database acceptance](../../server/tests/test_requirement_confirmation.py),
  [consumer acceptance](../../server/tests/test_requirement_consumption.py),
  [native acceptance boundary](org-console.md#native-rendering-boundary).
