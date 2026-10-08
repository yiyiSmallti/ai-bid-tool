---
kind: reference
---

# Uploaded-bid preparation and authorized text review

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
and per-document page counts, text/image classification, warnings, signature
evidence and cited 签章要求候选 (signing-clause candidates).

The CLI commands and their approved metadata types are defined in the
[HTTP and CLI contract](../plan/bid-review.md#preflight-http-and-cli):
`bid review upload`, `bid review prepare`, and `bid review submission list/show`.
Platform operators use 信任根证书 or `bid platform trust-anchor list/add/disable`
to compare public CA fingerprints and manage the local trust store.
Prepare submission requires the exact preview hash and signed receipt. Retrying a
failed or cancelled job requires a fresh request ID, a current preview and explicit
`retry`; replaying that retry request returns the existing job. All responses use
Result 4.0, and these commands have no legacy Result 3.0 route.

The prepared submission also offers a cleared-text review and a separate review-run
preflight. A human admin or bidder who owns the task reviews the actual sanitized
native text, selects exact pages and confirms its external use before submission.
The review extracts cited tender obligations and classifies signing-clause
applicability. It does not determine whether the uploaded bid satisfies an
obligation or contains a required mark. Runs remain advisory and record those
unassessed areas explicitly.

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
establish field presence only, including empty fields; separate cryptographic
observations establish the local digital-signature dimensions. All pages start restricted, with
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

The [local signature validator](../../server/app/services/bid_pdf_signatures.py)
runs in the bounded disposable PDF process on verified original bytes before any
conversion/rasterization. The [approved PDF profiles](../plan/bid-review.md#signature-and-seal-completeness)
define the GM/T CMS and straightforward standard detached paths. The strict
[DER reader](../../server/app/core/bid_der.py) rejects indefinite/noncanonical
lengths and limits bytes, nodes and depth. Every signature records its original
field hash, revision hash, byte ranges, content digest and signature value results,
certificate period, modification and trust independently. The final revision never
inherits validity from an earlier signature. Unsupported formats and time proofs
stay explicit; claimed signing time is not trusted time, and revocation is unknown.

[Migration 0062](../../server/migrations/versions/0062_bid_signatures.py) adds a
preparation-bound trust snapshot plus immutable per-document validation and
page-bound clause candidates. Admission includes the trust-store hash in the
preview/input identity and serializes snapshot publication with anchor changes.
Retries retain the admitted snapshot; disabling an anchor affects later
preparations, never earlier evidence. Snapshot and evidence tables use the same
org/task/preparation lineage, FORCE RLS and live worker publication fences as the
page inventory. Ciphertext is org/row-bound and owner-only rotation preserves
business data. Public CA management uses the fixed functions and global exception
in [ADR 0010](../adr/0010-offline-signature-trust.md).

The [clause scan](../../server/app/services/bid_signing_clauses.py) reads native
tender page text without OCR or models. Quotes retain Unicode codepoint offsets,
page IDs, mark/owner/date hints and unknown applicability. The scan stops with an
explicit failure above 2,000 candidates or a 2,000-character indivisible quote;
it does not truncate obligations. Pages without native text remain visible gaps
in the existing inventory. Candidate reads are paged, with at most 50 per request.

[Read projections](../../server/app/services/bid_signature_views.py) decrypt only
inside the service and select explicit public fields. Human admin/bidder task
readers with original-read authority can see certificate names and exact tender
quotes. Technical/viewer sessions and tokens receive safe status/identity metadata
without signer names, field names, serial numbers or quotes. All HTTP responses
retain the existing no-store policy; jobs/audits contain only fixed metadata.

### Authorized native-text review

[Privacy snapshot construction](../../server/app/services/bid_review_privacy.py)
loads fixed prepared pages, registered confidential-value bindings, task redaction
settings and pinned model configuration. Registered values become placeholders
before pattern masking; bidder and staff names derived from labelled bid text,
local certificate subjects and human additions are also masked. The preview
returns only the cleared native text to authorized human task owners. Image,
price, mixed and uncertain pages cannot be authorized for external text calls.
Original filenames, storage locations, identifiers and pixels are absent from
provider requests.

The human grant fixes exact page IDs and sanitized-text hashes, submission and
preparation identity, redaction/confidential/name-list hashes, provider bindings
and its specific text-review purpose. Grants and revocations append history;
changing any fixed input invalidates the grant. Tokens cannot inspect cleared
text, maintain name lists or create/revoke grants. A token with explicit
`bid-review:run` may execute only a current human-authorized snapshot under live
task authority. Its review and job projections contain safe metadata only.

[Review preflight and admission](../../server/app/services/bid_review_run.py) use a
write-free actor-bound receipt and exact-request budget quotes. Missing preparation,
human authorization or provider configuration appear as admission blockers without
external discovery. Submission verifies the receipt and live bindings, deduplicates
request identity and enqueues the existing durable worker. Before every model
admission and publication, the worker checks authority, cancellation, lease,
provider identity and the exact grant again.

The [review adapter](../../server/app/providers/bid_reviewing.py) sends complete
sanitized tender-page text with local refs and corresponding deterministic
signing candidates. Candidate quotes must survive masking unchanged. The
[local acceptance path](../../server/app/services/bid_review_text.py) accepts only
unique contiguous citations in both the sent page and fixed original page.
Unsent refs, fabricated or joined quotes, ambiguity and privacy placeholders are
explicit gaps. Rejected model strings do not enter diagnostics. Accepted
obligations retain original PDF-page or mapped Word-block citations, and signing
requirements retain candidate identity, applicability and required-location rules.
Every-page and seam groups expand against the full prepared bid inventory; each
required occurrence remains unresolved because mark presence is not checked.

The job's `bid_submission_document_id` binds the real uploaded tender row through
an org/task foreign key. It does not create an ordinary tender `Document` or open
legacy parsing/download routes. [Migration 0063](../../server/migrations/versions/0063_bid_review_privacy.py)
owns local privacy snapshots and exact grants; [migration 0064](../../server/migrations/versions/0064_bid_review_run.py)
owns review inputs, normalized tender-page/signing-location links and atomic
publication. Both retain immutable tenant rows with forced RLS; rollback disables
admission and repairs forward without removing encrypted history or charges.

Each call contains one whole authorized tender page. A page above the configured
context bound blocks submission instead of being truncated. The request plan
quotes only calls within the lower deployment/run ceiling and explicitly retains
uncovered pages. Deterministic price classification treats four numeric values
covering at least eight percent of native text as price content, or three covering
at least four percent as uncertain; both are excluded, as are keyword matches.
Human-reviewed text does not authorize image disclosure. Local identity lists are
bounded to 1,000 values; oversized local input or response items fail explicitly.

Calls reuse `JobExecution` admission and the shared task/prepaid accounting ledger.
Refusal, malformed/truncated output and completed calls after cancellation retain
actual usage; uncertain outcomes retain their holds. Budget or provider stops may
retain a partial report with explicit unfinished coverage. Changed input, cancelled
or lost attempts and accounting-bound violations fence publication. Report bodies
are encrypted; bounded protected sections serve cited obligations and signing
requirements to authorized humans, while token projections disclose only IDs,
counts and fixed coverage codes. The
[budget mechanism](task-budgets.md#how-it-works) owns settlement and exposure rules.

## Pitfalls

- Synthetic fixtures alone missed the real 点聚 layout (embedded content digest,
  `1.2.156.10197.1.301.1`, critical extended key usage); validate changes against a
  real signed bid locally, reporting aggregates only, because real bids cannot be sent
  to external tools. An unreadable CMS is `unsupported`, never `invalid`.
- Pure-Python SM3 takes seconds per megabyte; hashing goes through OpenSSL in
  `cryptography`, and only SM2 point arithmetic uses gmssl.
- Extracted obligations and classified signing clauses do not establish bid compliance,
  visible-signature presence, image support, scoring or a complete console/Word report.
  Missing native text and unreviewed locations remain explicit coverage gaps.
- A prepared inventory is not a compliance result, OCR transcript, visible-signature
  completeness assessment, privacy clearance or permission to transmit a page externally.
  Local digital validity does not establish visible mark completeness or revocation.
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
- [Signature persistence](../../server/app/models/bid_signature.py),
  [trust-anchor platform routes](../../server/app/api/platform_trust_anchors.py),
  [trust-anchor console](../../web/src/views/TrustAnchors.vue),
  [HTTP/worker signature acceptance](../../server/tests/test_bid_review_signatures_db.py)
  and [signature browser acceptance](../../web/e2e/bid-review-signatures.spec.js).

- [Authorized text schemas](../../server/app/schemas/bid_review_privacy.py),
  [review-run schemas](../../server/app/schemas/bid_review_run.py),
  [privacy persistence](../../server/app/models/bid_review_privacy.py),
  [review persistence](../../server/app/models/bid_review_run.py) and
  [review worker](../../server/app/jobs/bid_review_run.py).
- [HTTP-to-worker review acceptance](../../server/tests/test_bid_review_run_db.py)
  captures synthetic provider requests and sanitized result receipts.
