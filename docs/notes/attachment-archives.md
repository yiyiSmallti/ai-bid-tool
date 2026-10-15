---
kind: reference
---

# Attachment archives and exact page privacy

## Problem

An org profile declaration does not authorize its supporting PDF for a task or
make the PDF privacy-cleared. Attachments need separate immutable file review,
exact declaration/task bindings, and human privacy decisions on individual page
pixels. The [attachment contract](../plan/attachment-archive.md#decisions) defines
those boundaries and the branches that require later acceptance.

## Usage

The console attachment library and `bid resource attachment` commands maintain
originals, decisions and independent histories. `bid resource profile
attachment` links an accepted revision to one nonempty declaration. `bid task
attachment` fixes that link beneath an active exact task-profile selection.
`bid evidence attachment source` archives one page; `bid evidence attachment privacy
review` records unchanged-page clearance or a durable need for redaction.

Metadata lists use the contract's bounded cursor pages. The management browse
endpoint and console use smaller pages and prefix search over safe category/ID
metadata; encrypted display labels are not a search projection. Exact revision
views require human original-reader permission. Decisions and revisions carry
timestamps; the single human is implicit under
[ADR 0012](../adr/0012-single-user-installation.md).

## How it works

[Attachments](../../server/app/services/attachments.py) locks the archive workflow,
uses request IDs with canonical payload hashes, and writes immutable revisions and
files. Metadata labels and original upload names use record-bound encrypted
values. Single PDFs retain the uploaded bytes exactly; the neutral
[PDF validator](../../server/app/core/pdf_files.py) runs inside the existing bounded
PDF subprocess. The file's safe descriptor contains a generated filename.

[Attachment pages](../../server/app/services/attachment_pages.py) adds a typed branch
to `EvidenceSource`. Canonical rendering uses the existing source renderer; temporary
zoom previews do not create provenance. Publication rechecks task authority, the
exact profile selection, link and archive approval after rendering.
Privacy clearance writes the existing screenshot asset/rendition/privacy-review
chain from the server-held page, without a client upload receipt or second raw copy.
A negative hold invalidates cleared use of that exact page; it cannot be cleared by an
unchanged-page attestation. Existing screenshot withdrawals preserve every artifact.

[Runtime authorization](../../server/app/services/auth.py) grants tokens only explicit
attachment metadata read. Every attachment-derived screenshot read follows the
fixed ancestry, live task grants, approval, link, hold and withdrawal. Internal
bounded integrity checks can inspect the fixed original/page without granting a
token original-byte access. Signed application downloads still require live
authentication; original preview signatures also bind archive workflow state. Withdrawn raw pages
require the explicit `history=true` preview route and human original-reader grants.
No archive or privacy action creates confirmed Evidence or authorizes a bid export.

Task changes reuse statement event producers.
Library changes enqueue
[bounded task invalidation](../../server/app/jobs/attachment_events.py) in the same
transaction as their audit.
Queue arguments carry the durable task-ID cursor;
resolvers deny stale use before projections catch up.

## Pitfalls

- Storage publication and database commit are separate. Operational stage records
  contain only server-generated object identities, immutable keys and hashes.
  [Reconciliation](../../server/app/services/attachment_objects.py) checks a bounded
  stage manifest against both stores and reports committed, retained-orphan,
  unavailable or integrity-failure states. It never deletes or promotes an orphan.
- Reapproval cannot revive an old task pin or privacy review. Create explicit new
  selections and page reviews. Historical decisions remain intact.
- A clear page stays unconfirmed material. Attachment annotation, generic image
  Evidence admission and model analysis remain blocked by their explicit gates.
- Preserve encryption recovery keys with retained ciphertext. The owner-only data
  rotation path may rewrap encrypted metadata fields without changing any other
  immutable revision/file column.

## Code

- [Runtime contracts](../../server/app/schemas/attachment_contracts.py) and
  [HTTP routes](../../server/app/api/attachments.py).
- [Attachment baseline](../../server/migrations/baseline/00_functions_bid.sql)
  and [models](../../server/app/models/attachments.py).
- [API acceptance](../../server/tests/test_attachment_archive.py),
  [storage and integrity acceptance](../../server/tests/test_attachment_storage.py),
  [fixed-scale reads](../../server/tests/test_attachment_scale.py), and
  [CLI checks](../../server/tests/test_attachment_cli.py).
- [Native archive pages](../../desktop/native_management_attachments.go),
  [task attachments](../../desktop/native_management_task_attachments.go) and the
  [acceptance plan](../plan/attachment-archive.md#acceptance-plan).
