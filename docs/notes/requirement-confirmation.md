---
kind: reference
---

# Requirement confirmation and manual entry

## Problem

A verified citation proves where a quotation occurs. It does not prove that an
extracted interpretation is faithful or that extraction found every requirement.
Requirement review accepts the exact interpretation and source independently of
response, Evidence, rubric and co-sign decisions. The approved boundaries and
payloads are in [the B02 contract](../plan/requirement-confirmation.md).

## Usage

The task's requirement-review workspace and `bid req review-list` select an explicit
extraction scope. `bid req show` supplies its review revision, hash, source pin and
next accountable human. `bid req confirm` and `bid req reopen` consume a JSON input
file carrying that revision/hash, a reason and a caller-generated request UUID.
The task owner can confirm up to 100 explicitly inspected items with
`bid req confirm-batch`; entry and confirmation may be performed by the same human.

`bid req rejected` reads the original, incomplete extraction receipts.
`bid req add --input FILE --dry-run` verifies a complete manual entry at an existing
parsed PDF page or Word block. Saving with `bid req add --input FILE` requires the
preview hash and leaves the requirement unconfirmed. An omitted extraction ID
creates an explicitly selected manual set; an existing ID appends to that set with
its observed revision. Manual sets never replace the default latest model result.

Owners/contributors with eligible org roles and `req:confirm` or `req:manual` may
write. Tokens and delegated workers cannot receive those capabilities. Archived
tasks retain readable history and reject review/manual writes. Assignment routes
responsibility; it does not grant confirmation rights.

## How it works

The review identity combines org, task, extraction and requirement IDs. A canonical
hash binds content and a verified source pin: document hash, complete chunk hash,
full location, literal quotation, Python character offsets and verifier policy.
The deterministic locator is shared with extraction; a manual quote must equal the
located original slice. Ambiguous, missing, normalize-only and unverified inputs
cannot be saved as requirements.

Review sets and current reviews have separate revisions. Immutable events retain
encrypted snapshots/reasons; encrypted request receipts make identical authorized
retries return their original event IDs and the current view. Cross-action reuse
of a request ID conflicts. Database parent keys, FORCE RLS and human-state triggers
enforce the same org/task authority as the services. Source mutation records
invalidation permanently, including when content is later changed back.

Preparation binds meaning, source and membership without requiring human approval.
For selected response generation, the preparation manifest binds the complete
extraction scope while the provider receives only the selected requirements.
Acceptance binds the current review revision/hash as well. Response confirmation,
comply-only disposition, whole-rubric confirmation and score consumption require
current confirmation. Draft assembly partitions every saved requirement into an
accepted row, comply-only item or explicit gap. Checks can diagnose partial drafts;
unconfirmed tender interpretations remain provisional. Export freshness follows
the fixed draft and review inputs. Existing evidence, professional-domain,
prototype and co-sign gates remain independent.

The opt-in board places pending review in `requirement_review`, names the eligible
assignee/owner or recovery action, and keeps response completion separate. Legacy
boards retain their existing enums and show pending requirement review as a gap.
Metadata-only durable task events invalidate snapshots in the committing
transaction; audit payloads contain IDs and hashes, never reasons or quotations.

## Pitfalls

- Confirmation is not extraction-completeness certification. Rejected receipts
  are not saved requirements and never enter the progress denominator.
- A failed extraction retains its status, rejected summaries and cost. Manual
  recovery adds lineage without modifying that receipt or scheduling model work.
- A saved requirement cannot be deleted, excluded or freely edited through this
  slice. Disputed content remains unconfirmed; citation repair retains its existing
  human administrator gate and invalidates the affected requirement binding.
- Historical accepted artifacts remain readable as stale. Reconfirmation does
  not approve an old response or make an old draft/report current automatically.
  The deferred draft gate validates a newly inserted publication; it does not
  prohibit later source changes because a historical draft now needs reassembly.
- Source-event producers may derive a missing org context only when the current
  database role already has superuser or BYPASSRLS authority. They restore that
  context afterward. Runtime roles and explicitly conflicting org contexts retain
  the normal tenant boundary; automatic invalidation never grants human approval.
- Migration seeds `legacy_unconfirmed` without invented historical approval.
  Existing rows require the data-encryption key for their encrypted baseline.
  Stop writes and restore a gate-aware release for recovery; do not run a binary
  that ignores requirement review. Ciphertext rotation uses the existing migration
  owner path and cannot change review metadata or append a human decision.

## Code

- [Runtime contracts](../../server/app/schemas/requirement_confirmation.py),
  [migration](../../server/migrations/versions/0046_requirement_confirmation.py),
  [models](../../server/app/models/requirement_confirmation.py).
- [Review/manual services](../../server/app/services/requirement_confirmation.py),
  [source pins](../../server/app/services/requirement_source.py),
  [consumer bindings](../../server/app/services/requirement_consumption.py),
  [HTTP routes](../../server/app/api/requirement_confirmation.py).
- [Board projection](../../server/app/services/requirement_board.py),
  [CLI commands](../../cli/bid_cli/requirement_confirmation.py),
  [console workspace](../../web/src/views/OrgRequirements.vue).
- [API/database acceptance](../../server/tests/test_requirement_confirmation.py),
  [consumer acceptance](../../server/tests/test_requirement_consumption.py),
  [browser acceptance](../../web/e2e/requirement-confirmation.spec.js).
