---
kind: reference
---

# Attachment archives and exact page privacy

## Problem

An org profile declaration does not authorize its supporting PDF for a task or
make the PDF safe for the team. Attachments need separate immutable file review,
exact declaration/task bindings, and human privacy decisions on individual page
pixels. The [attachment contract](../plan/attachment-archive.md#decisions) defines
those boundaries and the branches that require later acceptance.

## Usage

The org console attachment library and `bid resource attachment` commands maintain
originals, assignments, decisions and independent histories. `bid resource profile
attachment` links an accepted revision to one nonempty declaration. `bid task
attachment` fixes that link beneath an active exact task-profile selection.
`bid evidence attachment source` archives one page; `bid evidence attachment privacy
review` records unchanged-page clearance or a durable need for redaction.

Metadata lists use the contract's bounded cursor pages. The management browse
endpoint and console use smaller pages and prefix search over safe category/ID
metadata; encrypted display labels are not a search projection. Exact revision
views require original-reader permission and derive revision authors from the
page's indexed audit records. Missing or ambiguous authors remain null.

## How it works

[Attachments](../../server/app/services/attachments.py) locks the archive workflow,
uses request IDs with canonical payload hashes, and writes immutable revisions and
files. Metadata labels and original upload names use org/record-bound encrypted
values. Single PDFs retain the uploaded bytes exactly; the neutral
[PDF validator](../../server/app/core/pdf_files.py) runs inside the existing bounded
PDF subprocess. The file's safe descriptor contains a generated filename.

[Attachment pages](../../server/app/services/attachment_pages.py) adds a typed branch
to `EvidenceSource`. Canonical rendering uses the existing source renderer; temporary
zoom previews do not create provenance. Publication rechecks task authority, the
exact profile selection, link, archive approval and assignment after rendering.
Privacy clearance writes the existing screenshot asset/rendition/privacy-review
chain from the server-held page, without a client upload receipt or second raw copy.
A negative hold invalidates team use of that exact page; it cannot be cleared by an
unchanged-page attestation. Existing screenshot withdrawals preserve every artifact.

[Runtime authorization](../../server/app/services/auth.py) grants tokens only explicit
attachment metadata read. Every attachment-derived screenshot read follows the
fixed ancestry, live task grants, approval, link, hold and withdrawal. Internal
bounded integrity checks can inspect the fixed original/page without granting a
team reader original-byte access. Signed application downloads still require live
authentication; original preview signatures also bind archive workflow state. Withdrawn raw pages
require the explicit `history=true` preview route and human original-reader grants.
No archive or privacy action creates confirmed Evidence or authorizes a bid export.

Task changes reuse statement event producers. Library changes enqueue
[bounded task invalidation](../../server/app/jobs/attachment_events.py) in the same
transaction as their audit. Queue arguments carry the durable task-ID cursor;
resolvers deny stale use before projections catch up. No separate business Job,
model call or charge is created for deterministic archive operations.

## Pitfalls

- Storage publication and database commit are separate. Operational stage records
  contain only server-generated object identities, immutable keys and hashes.
  [Reconciliation](../../server/app/services/attachment_objects.py) checks a bounded
  stage manifest against both stores and reports committed, retained-orphan,
  unavailable or integrity-failure states. It never deletes or promotes an orphan.
- Reapproval cannot revive an old task pin or privacy review. Create explicit new
  selections and page reviews. Reviewer departure blocks pending work while keeping
  historical decisions intact.
- A clear page stays unconfirmed material. Attachment annotation, generic image
  Evidence admission and model analysis remain blocked by their explicit gates.
- Preserve encryption recovery keys with retained ciphertext. The owner-only data
  rotation path may rewrap encrypted metadata fields without changing any other
  immutable revision/file column.

## Code

- [Runtime contracts](../../server/app/schemas/attachment_contracts.py) and
  [HTTP routes](../../server/app/api/attachments.py).
- [Attachment migration](../../server/migrations/versions/0055_attachment_archive.py)
  and [models](../../server/app/models/attachments.py).
- [API acceptance](../../server/tests/test_attachment_archive.py),
  [storage/isolation acceptance](../../server/tests/test_attachment_storage.py),
  [fixed-scale reads](../../server/tests/test_attachment_scale.py), and
  [CLI checks](../../server/tests/test_attachment_cli.py).
- [Browser scenarios](../../web/e2e/attachment-management.spec.js) and the
  [acceptance plan](../plan/attachment-archive.md#acceptance-plan).
