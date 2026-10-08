---
kind: reference
---

# Uploaded-bid submissions and local preparation

## Problem

A completed bid can contain confidential originals that have never entered the
confirmed-response workflow. Reviewing it requires an independent immutable input
and fixed page inventory, without granting an agent access through tender parsing
or assuming that uploaded assertions are confirmed evidence. The
[approved review plan](../plan/bid-review.md#objective-and-input-boundary) defines
this boundary and the subsequent signature, compliance, report and triage stages.

## Usage

The org task's **标书检验** entry opens the upload wizard and submission list.
Choose tender and bid files with explicit roles and material kinds, preview the
actual deployment limits, then upload a fixed version. Its detail page offers a
separate local-preparation preview and explicit submission. It shows job status
and per-document page counts, text/image classification, warnings and the number
of PDF signature fields found.

The CLI commands and their approved metadata types are defined in the
[HTTP and CLI contract](../plan/bid-review.md#preflight-http-and-cli):
`bid review upload`, `bid review prepare`, and `bid review submission list/show`.
Prepare submission requires the exact preview hash and signed receipt. Retrying a
failed or cancelled job requires a fresh request ID, a current preview and explicit
`retry`; replaying that retry request returns the existing job. All responses use
Result 4.0, and these commands have no legacy Result 3.0 route.

## How it works

[Upload and admission](../../server/app/services/bid_review.py) checks live org
Membership and task authority. Human admin/bidder owners or contributors upload
and prepare; readers receive safe identifiers, hashes, counts and fixed warning
codes. Tokens need an explicitly issued `bid-review:read` grant and receive no
original filenames, text, pixels, storage keys or download links. Original bytes
are not served by the submission metadata API.

The memory-only multipart receiver bounds metadata, headers, file count and bytes.
The lower of the approved limit and `Settings.max_upload_bytes` applies separately
to each file and to the aggregate submission. Every descriptor's extension, MIME,
hash and length must match. The complete file set passes isolated validation before
any original object or row is written. Request identity fixes the ordered manifest
and upload-name hashes; replay rechecks authorization and returns the same receipt.
Original bytes use the existing encrypted storage provider, and names use encrypted
org/row-bound database values.

Preparation preflight reads only immutable metadata and current authority/budget.
It makes no conversion, OCR or model call and writes no Job, object, usage, audit or
reservation. The signed 15-minute receipt binds actor, org, task, submission and
input hash. The input includes file order/hashes, parser/render identities and
processing limits. Submission checks that receipt and atomically enqueues the
existing Job/Procrastinate path. Local parsing and rendering have zero cost and
create no invented UsageRecord; terminal accounting uses the shared budget result.

The [local preparation worker](../../server/app/jobs/bid_review_prepare.py) reads
and verifies exact encrypted originals, uses the disposable
[PDF process](pdf-parsing.md), and converts DOCX only through the configured bounded
private Gotenberg service. `BID_REVIEW_OFFICE_PROFILE` names its versioned image,
font set and conversion configuration; DOCX admission is blocked when it is absent.
Compose supplies the profile for its pinned bundled-font image. A custom converter
requires its own profile, changed whenever its image/fonts/settings change. The
profile hash is part of renderer identity and invalidates old previews.
Office parsing rejects external relationships, macros,
embedded active objects and unsafe fields before conversion. Archive bounds are
4,096 entries, 32 MiB per inflated part and 128 MiB total inflated content. The
[Word structural parser](docx-citations.md) retains original block locations;
unique text matches map blocks to the fixed derived PDF. Ambiguous, split or
unmapped blocks remain explicit gaps. Mapping work is bounded and records unknown
mappings when that bound is exhausted.

Native text is retained encrypted. Pages with substantial uncovered image regions
are classified as image pages even if a page number or header provides native
text; availability of native text does not claim complete page coverage. Preparation
performs no OCR or model transcription. Every page has a fixed PNG descriptor,
original/rendered source hash and encrypted structural map. Signature-widget counts
establish field presence only, including empty fields; they establish neither a
signature's presence nor cryptographic validity. All pages start restricted, with
uncertain price classification and no outbound clearance.

[Migration 0061](../../server/migrations/versions/0061_bid_review_upload.py) adds
immutable submissions, ordered documents, preparation bindings, prepared-document
metadata, pages and a unique publication receipt. Each table has org/task composite
parents, Membership actor references, ENABLE/FORCE RLS and restricted runtime grants.
Deferred guards require a complete upload manifest and a complete page publication.
The worker rechecks live authority, input identity, lease, run ID and cancellation
before publishing every document/page and the successful job result in one
transaction. Task events use the existing bounded metadata producers. No generic
Document or Chunk row exposes these originals to the tender-only workflow.

## Pitfalls

- A prepared inventory is not a compliance result, OCR transcript, signature
  validation, privacy clearance or permission to transmit a page externally.
  Signature validation requires the separately approved library and later slice.
- DOCX pages describe a derived rendering, not pagination of the original Word file.
  Missing structural mappings and skipped headers/text boxes remain warnings.
- Storage and PostgreSQL cannot commit atomically. Failures or cancellations expose
  no partial inventory, but encrypted staged objects can remain unreachable. Safe
  object identifiers and hashes support reconciliation; there is no automatic purge
  or blind deletion. Attempt-owned plaintext spools are removed on every exit.
- Submitted versions and published pages cannot be changed or deleted. Replacement
  is another submission. Owner-only encryption rotation can rewrap designated
  ciphertext columns while preserving business identity. Downgrade preserves data
  by refusing destructive rollback; disable admissions and repair forward.

## Code

- [Runtime contracts](../../server/app/schemas/bid_review.py),
  [models](../../server/app/models/bid_review.py),
  [routes](../../server/app/api/bid_review.py) and
  [bounded multipart input](../../server/app/api/bid_upload.py).
- [Admission and reads](../../server/app/services/bid_review.py),
  [local authorization and isolated processing](../../server/app/services/bid_preparation.py),
  [preparation job](../../server/app/jobs/bid_review_prepare.py) and
  [DOCX inspection](../../server/app/core/bid_docx.py).
- [CLI](../../cli/bid_cli/bid_review.py),
  [upload console](../../web/src/views/OrgBidSubmissions.vue) and
  [submission console](../../web/src/views/OrgBidSubmission.vue).
- [PostgreSQL HTTP acceptance](../../server/tests/test_bid_review_upload_db.py),
  [CLI transport snapshots](../../server/tests/test_bid_review_cli.py) and
  [mocked browser acceptance](../../web/e2e/bid-review-upload.spec.js).
