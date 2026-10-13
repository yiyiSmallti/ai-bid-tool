---
kind: plan
---

# Attachment archives

[Runtime schemas](../../server/app/schemas/attachment_contracts.py) define the
payloads. The [mechanism note](../notes/attachment-archives.md) defines archival,
privacy and recovery behavior. The [single-user decision](../adr/0012-single-user-installation.md)
defines identity; the [annotation plan](annotation.md) defines the later source handoff.

## Goal and boundary

The human maintains supporting files in the local library, links an exact file revision to an org profile (单位资料) declaration, and selects
it explicitly for a task (任务). Examples are business licences, qualification (资格)
scans, signed contracts and performance records. The console shows the blocking condition and next action. The human need not understand
storage keys or revision hashes to complete the workflow.

Keep uploaded bytes, normalized PDFs, page PNGs and review history immutable and
encrypted. A file is user-supplied evidence (证据), not proof of authenticity. Archive
review accepts a revision for internal selection; privacy review accepts exact page
pixels for reviewed use. Neither is human confirmation (人工确认) of a response card
(响应卡), confirmation of a qualification claim, or permission to export a bid
(标书). The archive/source rows never acquire `confirmed_at` or direct eligibility
for a draft (初稿) or export. Existing certificate (证书) workflows remain the home for certificates
already managed there; do not upload a second copy merely to annotate one.

This contract covers cloud API/CLI/console archival, human review, declaration links,
task pins, page sources, downloads, deactivation and the B05 handoff.
No attachment text or image enters model prompts, memory (记忆), check/score, or export
just because it is archived.
Later consumers require an explicit reviewed contract.

## Current code basis and differences

All proposed extensions below are requirements, not statements of deployed behavior.

| Current module / symbol | Reuse and required extension |
| --- | --- |
| [profiles.py](../../server/app/services/profiles.py): `PROFILES`, `create_profile`, `update_profile`, `select_profile`; [profile_contracts.py](../../server/app/schemas/profile_contracts.py): `OrgProfileData` | Only `name`, `registration_details`, `performance_summary`, `standard_wording` declarations exist. The [design resource model](../design.md#data-model) describes scans and past contracts broadly; it does not supply their storage/authorization chain. Keep declarations intact and add explicit revision links. |
| [certificate_files.py](../../server/app/services/certificate_files.py): `validate_file`, `compose`, `image_pdf`, `create_file`, `require_file`, `read_revision`; [certificate_file_contracts.py](../../server/app/schemas/certificate_file_contracts.py) | Reuse byte validation/composition, descriptors, ordered parts, exact revision publication and hash checks. Extract neutral helpers when implementing; certificate-specific `create_file`/`require_file` cannot authorize attachments or create fake Certificate records. |
| [versioned.py](../../server/app/services/versioned.py): `VersionedKind`, `select_revision`, `audit` | Reuse root locks, append-only content revisions and explicit retained selections. Add attachment review/link gates; selecting the latest library version implicitly is forbidden. |
| [evidence_sources.py](../../server/app/services/evidence_sources.py): `source_inputs`, `bounded_render_async`, `render_page_async`, `check_png`, `create_source`, `require_source`, `read_preview` | Extend this one source archive service/table with a typed attachment branch. Preserve the certificate branch and its public shape. Bind the task selection/file/page and recheck after rendering; do not build an independent page-image archive. |
| [page_previews.py](../../server/app/services/page_previews.py): `render_pdf_page_async`, `certificate_page`; [pdf_process.py](../../server/app/core/pdf_process.py): `run_pdf_operation_async`; [pdf_raster.py](../../server/app/core/pdf_raster.py): `raster_dimensions` | Reuse temporary PDF preview and bounded PDF subprocess. Temporary zoom previews are not canonical annotation sources: source archives are fixed 150-dpi `pdf-page-preview-v1`. Upload composition currently uses thread execution; move attachment validation/composition behind the same process boundary before accepting untrusted uploads. |
| [storage.py](../../server/app/providers/storage.py): `Storage`, `FileCipher`, `LocalStorage`, `S3Storage`, `validate_key` | Use the same authenticated encryption bound to object key and immutable `put`; use `read_bounded` before decoding. Current certificate/source reads include unbounded `read` followed by checks. This contract requires bounded attachment reads; it does not claim the older paths already meet that rule. |
| [api/common.py](../../server/app/api/common.py): `signed_link`, `check_signature`, `attachment`; [api/resources.py](../../server/app/api/resources.py): certificate/source download routes; [api/main.py](../../server/app/api/main.py): `context` | Reuse 300-second application links plus live authentication. Signatures bind purpose and object IDs, not actor/hash; immutable IDs resolve hashes at download. No S3 presigned/public URL or raw-parts download route exists. New part routes and lineage checks must be explicit. |
| [screenshots.py](../../server/app/services/screenshots.py): `prepare`, `ingest`, `resolve_source`, `rendition_access`, `withdraw`, `resolve_image_material`; [screenshot_renderer.py](../../server/app/providers/screenshot_renderer.py): `render`, `validate_png` | Reuse `ScreenshotAsset`, `ScreenshotRendition`, `ScreenshotPrivacyReview`, existing privacy plans and renderer. Add a server-bound attachment source, not a client download/re-upload receipt or second redaction engine. The attachment branch follows the enabled scope in the first slice. |
| [response_cards.py](../../server/app/services/response_cards.py): `resolve_material`, `build_evidence`, `confirmation_inputs`; [exports.py](../../server/app/services/exports.py): `build_manifest`, `fresh_manifest`, `download_gate` | Existing `certificate_pdf_page` can contribute to exports after human page review. Do not alias attachments to that kind or to scalar `org_profile` material. New attachment evidence uses the B05 image chain only after its gates are implemented. |
| [auth.py](../../server/app/services/auth.py): `Identity`, `authenticate`, `HUMAN_ONLY_SCOPES`, `SCOPES`, `HUMAN_SCOPES`; [task_authorization.py](../../server/app/services/task_authorization.py): `task_authorized`; [task_access.py](../../server/app/services/task_access.py): `access` | Preserve human-only scopes, scoped token limits and exact task/source binding. A resource ID alone does not authorize its bytes. |
| [contracts.py](../../server/app/schemas/contracts.py): `CONTRACT_VERSION`, `Result`, `Cost`; [preflight.py](../../server/app/services/preflight.py): `attach` |The design's two-field cost example and older draft examples do not define this contract.
No new billed Provider is introduced. |

The `memory/memory_contracts.py` example has moved to
[runtime memory contracts](../../server/app/schemas/memory_contracts.py); the
[memory plan](memory.md), [org-console plan](org-console.md) and
[checking plan](check.md) provide structure, not authority to copy stale claims.

## First vertical slice

Implemented scope: **one unchanged PDF → archive review → exact declaration link → task pin →
one archived source PNG → human privacy clearance → visible B05 readiness**. Accept
business-licence or contract/performance PDFs with the same path; kind is a descriptive
category, not a different storage or permission chain. Include API, CLI and console.

1. The human uploads one unrotated PDF. The server publishes revision 1 as awaiting
   review and shows “Review uploaded file”.
2. The human inspects the original and records approve/reject against its exact
   revision, file hash and metadata hash. Approval means suitable for internal use,
   with no claim of external authenticity. Rejection preserves bytes and requires
   a corrected revision before use.

3. A human links the accepted revision to one existing nonempty declaration field on
   an exact `OrgProfileRevision`. Linking asserts relevance only. The human
   explicitly selects that link under the matching active `TaskOrgProfile`. The task
   displays the fixed file revision and any later-library-version notice.
4. A human with original access requests one page. Render and persist its genuine PNG
   in `EvidenceSource`, with no Evidence creation. If task/profile/link authority changed
   during rendering, publish nothing. Archive creation may precede requirement extraction.
5. After an extraction exists, the human opens the raw page and attests the
   exact reviewed PNG hash. Server-bound ingestion into the existing screenshot chain
   records a human review and immutable rendition. The first slice accepts only an
   unchanged page safe for reviewed use; `needs_redaction` blocks cleared pixels/B05 and directs
   the human to the later privacy workflow. No clear attestation is inferred.
6. The console exposes that page as “Ready for annotation” only if the attachment B05
   adapter is enabled. Until then, it says `annotation_adapter_not_enabled`; it never
   claims a completed response (响应). B05 completion remains a separate dependency.

The full contract includes ordered PDF/PNG/JPEG parts and redacted page derivatives.
Enable these only after their composition/metadata/privacy acceptance passes. Reject
disabled branches with `attachment_upload_mode_not_enabled` or
`attachment_redaction_not_enabled`, not silent conversion or fallback. Existing
certificate uploads retain their own enabled formats. A first slice is complete when
its archive flow works through the real console and human scope, even while B05 is
explicitly unavailable; enabling B05 requires its additional end-to-end gate below.

## Revision, review and retention rules

- An attachment root owns an ordered content history. Every upload/revision supplies
  complete metadata and explicit file bytes; no missing file inherits an earlier one.
  A new revision does not change existing profile links or task pins. A metadata-only
  correction also creates a revision with explicitly resupplied bytes.
- For a single unrotated PDF, the normalized original is exactly the upload. For
  composed input, retain every uploaded part unchanged with order/rotation/page span,
  plus the composed PDF. Image re-encoding removes EXIF/GPS from the composed PDF and
  preview, **not from retained parts**. Generated neutral filenames avoid exporting
  customer names from uploaded filenames; retain original names encrypted internally.
- Archive decisions are append-only and reference one content revision. An approval
  pins hashes and the decision timestamp; rejection/revocation cannot alter it.
  Selection pins its approval ID. A later revocation of that revision's approval
  invalidates its dependent selections; reapproval requires explicit new selection and
  privacy review, never automatic revival of prior accepted material. A review of
  revision 2 does not withdraw revision 1.
  Approve is allowed from pending/rejected/revoked, reject from pending, and revoke
  from approved. Repeating an already approved exact decision returns its receipt;
  changing a positive decision requires explicit revoke. Invalid transitions conflict.

- Root, declaration link and task-selection deactivation are one-way. Retain revisions,
  files, parts, links, reviews, pages, privacy/rendition records and audits. No DELETE
  route, retention timer, storage lifecycle expiry or cascading delete is permitted.
  Fix accidental deactivation through explicit new records and new reviews, not by
  reviving previous decisions. History reads still require live authorization.
- Root deactivation or link withdrawal blocks new use and invalidates current dependent
  B05 material. Historical originals remain available only to authorized humans via an
  explicit history read. Withdrawal does not delete history or revive older selections.
- Retention defaults to indefinite preservation, including backups and encryption-key
  recovery material. This is a product default, not a claim about a legal retention
  period. Any later mandatory purge process needs a separately approved contract; this
  one provides no soft-delete UI masquerading as erasure.

## Data model and migration outline

Reuse [schema types](../../server/app/schemas/attachment_contracts.py) including
`Contract`, `Result`, `Cost`, `CertificateScanFile`, `CertificatePart`,
`CertificatePartOptions`, `EvidenceSourcePreview`, `OrgProfileRevision`,
`TaskOrgProfileSnapshot`, `Sha256`, `PNGDescriptor`, `PixelRect`, `ContentMapping` and
`RenditionView`. Certificate descriptor names are reused as byte-format types only;
the attachment view has its own domain IDs. Models validate structure, not authority.

The authoritative [single-user baseline](../../server/migrations/baseline/10_tables.sql)
fixes archive/revision/file/part/review/link/selection/hold tables and their exact
parent bindings. Keep immutable file descriptors, contiguous part/page spans,
append-only decisions and root-locked state transitions. Human attribution is
implicit; decisions retain timestamps. No assignment or custodian record is needed.
The baseline is not changed by this conversion.

Extend existing `evidence_sources`, **not a new page store**: add an internal source
kind and attachment selection/root/revision/file/link/approval references. Certificate
columns become nullable only behind a CHECK requiring exactly one complete source
branch. Legacy rows are `user_supplied_certificate_pdf`; the new branch is
`user_supplied_attachment_pdf`. New composite FKs bind selection/task/file/revision.
Keep `source_unconfirmed`, page/count/profile/hash/path checks and append-only grants.
Use separate partial unique keys for each branch's `(selection_id,page,profile)`;
queries must branch explicitly rather than silently excluding attachment rows in a
certificate-only join. Preserve old `EvidenceSourceArchive` responses; use an additive
typed attachment view and separate list route.

Extend existing screenshot source bindings with an attachment discriminator and exact
EvidenceSource identity; add its task composite FKs, immutable human privacy receipt
and source checks. Reuse the existing asset/rendition/review tables and
`screenshot-privacy-v1`/`screenshot-markup-v1` profiles. Do not store original PDFs or
raw page copies in a second screenshot-original table. Task sources reference the
same retained file. Source PNG and a redacted/marked rendition are distinct intentional
artifacts, with explicit mapping and hashes.

Storage keys are server-generated:

```text
attachment/{attachment_id}/{revision_id}/{sha256}.pdf
attachment/{attachment_id}/{revision_id}/parts/{ordinal}/{sha256}.{ext}
evidence-source/{source_id}/{sha256}.png
```

Screenshot derivatives keep existing `screenshots/...` paths. DB checks
tie keys to IDs/hash/type; no client key, absolute path or filename enters a key.
Encrypt labels/original names with record-bound data envelopes using
`Secrets.for_data` in [security.py](../../server/app/core/security.py); public descriptors
use neutral names. No search index over plaintext contract contents is created.

The baseline's composite parent checks, human-only decision/lifecycle gates,
immutable-row guards and token exclusions remain mandatory. Lock task first, then
roots/revisions in stable ID order; library-only writes must not reverse this
order. Recheck approval and lifecycle at publication and every consumer. Disable
new writes/consumption for rollback while retaining ciphertext and history.

## Upload, rendering and bounded reads

The full upload contract accepts 1–20 PDF/PNG/JPEG parts, at most 40 MiB total input
and 40 MiB composed PDF, 1–200 pages and 40 million source-image pixels. Enforce the
lower deployment limit too. Validate magic bytes/extensions, safe names, rotation and
PDF readability; reject encrypted, repaired, password-protected or empty PDFs. No
ZIP extraction, office conversion, remote fetching or active-document execution.

Bound multipart input as it is received, not after buffering every part. Run reused
composition/validation operations through the existing PDF subprocess protocol before
enabling uploads: bytes/parts/output limits, timeout, memory, CPU and child termination
must apply. `PDFSettings` supplies deployment process bounds; do not describe the
current threaded certificate composer as already isolated. Inspecting/rasterizing a
PDF does not remove its embedded content; raw-original downloads use attachment
disposition and the console displays rendered PNGs, not an inline PDF executable view.

For every original, part and PNG read, use `Storage.read_bounded(key,limit)`
with `limit=min(stored_descriptor.size_bytes,deployment_limit,40 MiB)`. Then require
exact length/hash/media/dimensions before use. Do not decrypt all history, trust S3
Content-Length alone, or allocate a declared image before checking its dimensions.
Temporary library previews reuse `render_pdf_page_async` at zoom 1/2 (110/200 dpi),
have `Cache-Control: no-store`, and cannot serve as annotation provenance (溯源).
Canonical sources use `bounded_render_async`, 150-dpi RGB PNG, ≤8192 pixels per edge,
≤20 million pixels, ≤40 MiB and one page per request. Preserve PDF page rotation.

Use the existing two-render admission slots plus PDF process limits. Saturation is a
retryable busy failure; the application must not build an unbounded semaphore queue.
Library previews/source creation are bounded request operations, not fake taskless
`Job` rows. Long B05/privacy rendering uses the existing screenshot/job execution
chain; add transactional enqueue and run fencing as required by B05, not a new queue.

Lists/history default 50 and cap 100, stable `(created_at,id)` keyset order; `history`
is explicit. Signed cursors bind actor/route/filters/task and live access version.
Reject a mismatched/expired cursor. No `--all`, offset scan, unbounded nested reviews
or whole-PDF/all-page browser preload. File metadata includes at most 20 parts; list
rows omit parts and label text, show returns only one revision, and each history/review/
link/source list paginates separately. UI loads one preview and uses batched readiness
metadata, not per-row storage reads. Bound JSON input to 128 KiB and list output to
1 MiB; fail explicitly if one indivisible item exceeds the response bound.

Publication writes ciphertext before DB commit, as existing storage does. **DB and
object storage do not share an atomic transaction.** A storage/DB failure yields no
successful public record or success audit; encrypted unreachable objects can remain.
`Storage` has no delete/garbage-collection API. Retain these without public links and
record a safe operational orphan identifier outside business success audits; recovery
reconciles immutable keys, it does not erase committed content. Repeat requests use
request ID plus canonical metadata/ordered part hashes; same actor/route/payload is a
duplicate only after reauthorization, different payload is 409. Failed requests may
be retried without inventing a partial successful revision.

Persist replay identity for every mutation, including deactivation, in
the existing audit event's bounded request ID/payload-hash fields. Add an attachment-
action partial unique index over actor/command/request ID; look up under the
same object lock. Revision/review/link/selection/hold rows also retain their originating
request ID and payload hash. The root state version advances on each workflow change;
readiness is computed from exact pinned review IDs, never only that counter.

## Privacy, permissions and signed downloads

Contracts can contain prices, contacts, personal identifiers, signatures and bank
details, including scanned pixels and original image metadata. First-slice defaults:
raw originals, parts and all attachment-derived pixels are human-only. Explicitly
scoped tokens can list safe metadata, without labels, filenames or image links.
An encrypted file is not automatically privacy-cleared.

The human uses `attachment:write`, `attachment:review`, `attachment:manage`,
`attachment:original:read`, `task:attachment`, `attachment:privacy` and
`attachment:page:read` for their matching operations. Keep profile/source/screenshot
scopes and exact bindings at those entry points. These scopes remain human-only
in API and database checks; token issuance must reject forbidden scopes atomically.
Workers process fixed authorized requests with live input and lease checks;
`actor_kind=worker` never grants human confirmation authority.

Extend *every* source/screenshot/rendition/image-evidence/annotation/download/export
read path with the attachment-origin restriction; this must follow lineage through
derived assets and historical records, not only new HTTP routes. Task metadata may
show that a page awaits review without exposing pixels. The stricter token pixel
boundary is an attachment-specific proposed extension to B05's existing explicit-read
token policy; certificate/vendor (厂家) policy is unchanged. Do not enable attachment kinds
until this intersection is tested on legacy routes as well.

The same restriction covers attachment-linked quotes/visual observations in card,
draft and agent projections: tokens receive only safe material-state metadata, not
an alternative text copy of restricted content. Extend serialization at those real
entry points; changing a download button cannot enforce this boundary.

Keep lineage metadata resolution separate from raw-byte delivery. Internal integrity
checks may inspect only fixed ancestry and cannot disclose original bytes to a token.

Use `signed_link`/`check_signature` and authenticated `context` at issuance and delivery.
Links expire in 300 seconds and bind purpose and immutable object IDs. Resolve hashes
server-side; no caller-supplied storage key or redirect is allowed. Archive/privacy
withdrawal and approval invalidation take effect at download even before expiry.
Explicit original-history access remains human-only.

Responses use `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`; neutral
download names and attachment disposition for original/part bytes. The console fetches
with live credentials and disposes in-memory preview URLs. Do not persist signatures
in browser storage, events, audits, tracing or referrers. Disable logging of link query
parameters and multipart content. There is no anonymous image link or S3 credential
exposure. A signature of the wrong kind/ID or an expired signature returns 404.

Redaction (遮挡) must happen **before** B05. Reuse `ImagePlan.redact`,
`ScreenshotRenderer`, `PreparedScreenshot`, immutable `ScreenshotPrivacyReview` and
`ContentMapping`; B05 never paints privacy masks. Later redacted input pins source
PNG hash, plan hash, prepared image hash, exact human review and stored rendition/hash.
The human reviews the actual sanitized pixels and declares clearance; the server
verifies them by rendering from the archived page. Reject forged uploads, changed
hashes, out-of-bounds plans, masks that destroy the claimed supporting region and
unreviewed derivatives. Redaction does not certify contract truth. Withdrawn privacy
assets invalidate descendants; no widening back to the raw page through ancestor reads.

The privacy endpoint also accepts `mode=needs_redaction`, without an extraction or
rendition: append a negative `attachment_privacy_holds` receipt bound to the exact
page/hash and show the need for human redaction. Lock the source while checking the
expected latest hold. A later hold withdraws previously cleared usability; it cannot
delete a privacy review. `mode=clear` requires no hold. The later `mode=redacted` branch
adopts an already human-reviewed rendition from the existing privacy workflow and must
resolve the exact latest hold, with a nonempty redaction plan and a new review after
that hold. Record the hold binding on the existing `ScreenshotPrivacyReview` through
a task/source composite reference. Reused old clearances cannot resolve a new
hold. Thus the first slice can record a durable blocker without fabricating safe pixels
or implementing a second redaction engine.

[confidential.py](../../server/app/services/confidential.py) and
[redaction.py](../../server/app/services/redaction.py) protect text/outbound workflows;
they are not a PDF or pixel privacy scanner. No automatic “safe” decision, OCR, Vision,
LLM or external provider call is made here. Any later model consumer must separately
authorize sanitized bytes and preserve confidential-field (保密字段) controls.

## HTTP, CLI and Result 5.0

All routes below require authenticated scoped access; disabled branches return their explicit blockers. Request actor kind/review time,
storage key and success status are server-owned. The CLI is a cloud client; upload
`--file` reads bounded local bytes, `--input` reads bounded metadata JSON. It performs
no local render or file transformation. Commands support `--json`, missing arguments
fail without prompts. Local deployments run the same server/DB/storage authorization.

| HTTP route | CLI command (`bid … --json`) | Request → `data` / `items` |
| --- | --- | --- |
| `POST /resources/attachments` (multipart `metadata`, ordered `files`) | `resource attachment upload --input META.json --file FILE [--file FILE]` | `AttachmentCreate` → `AttachmentWriteReceipt` / [] |
| `POST /resources/attachments/{id}/revisions` (same multipart) | `resource attachment revise --id UUID --input META.json --file FILE` | `AttachmentRevise` → same receipt; expected state version required |
| `GET /resources/attachments?kind=&history=&cursor=&limit=` | `resource attachment list [--kind KIND] [--history] [--cursor C] [--limit N]` | `AttachmentListQuery` → `PageData` / `AttachmentSummary[]` |
| `GET /resources/attachments/{id}` | `resource attachment show --id UUID` | `AttachmentShowData` (safe metadata and current revision IDs) / [] |
| `GET /resources/attachments/{id}/revisions?cursor=&limit=` | `resource attachment history --id UUID [--cursor C] [--limit N]` | `PageQuery` → `PageData` / `AttachmentRevisionSummary[]` |
| `GET /resources/attachments/revisions/{revision_id}` | `resource attachment revision show --id UUID` | `AttachmentRevisionView` / []; original-read authority for restricted details |
| `POST /resources/attachments/revisions/{revision_id}/reviews`; `GET` same with cursor/limit | `resource attachment review --id UUID --input REVIEW.json`; `resource attachment reviews --id UUID [--cursor C] [--limit N]` | `AttachmentReviewInput` → `AttachmentReviewView`; list → `PageData` / review views |
| `POST /resources/attachments/{id}/deactivate` | `resource attachment deactivate --id UUID --input INPUT.json` | `AttachmentDeactivate` → `AttachmentShowData` / [] |
| `POST /resources/profiles/revisions/{profile_revision_id}/attachments`; `GET` same | `resource profile attachment link --profile-revision UUID --input LINK.json`; `resource profile attachment list --profile-revision UUID [--history] [--cursor C] [--limit N]` | `ProfileAttachmentLinkInput` → `ProfileAttachmentLinkView`; list → `PageData` / link views |
| `POST /profile-attachment-links/{link_id}/deactivate` | `resource profile attachment deactivate --id UUID --input INPUT.json` | `AttachmentDeactivate` → link view / [] |
| `POST /tasks/{task_id}/attachments`; `GET` same | `task attachment select --task UUID --input SELECT.json`; `task attachment list --task UUID [--history] [--cursor C] [--limit N]` | `TaskAttachmentSelect` → `TaskAttachmentView`; list → `PageData` / task views |
| `POST /task-attachments/{selection_id}/deactivate` | `task attachment deactivate --id UUID --input INPUT.json` | `AttachmentDeactivate` → task view / [] |
| `POST /tasks/{task_id}/attachment-sources`; `GET` same with selection/history/cursor/limit | `evidence attachment source create --task UUID --input SOURCE.json`; `evidence attachment source list --task UUID [--selection UUID] [--history] [--cursor C] [--limit N]` | `AttachmentSourceCreate` → `AttachmentSourceReceipt`; list → `PageData` / `AttachmentSourceSummary[]` |
| `GET /attachment-sources/{source_id}` | `evidence attachment source show --id UUID` | `AttachmentSourceSummary` with `AttachmentReadiness` / []; no raw filename/pixels |
| `POST /attachment-sources/{source_id}/privacy` | `evidence attachment privacy review --id UUID --input PRIVACY.json` | `AttachmentPrivacyInput` → `AttachmentPrivacyReceipt` or `AttachmentPrivacyHoldView` / []; clear/needs-redaction first slice, existing privacy-reviewed rendition adoption in the later redacted branch |
| Existing screenshot withdrawal route, extended attachment-origin checks | Existing screenshot withdraw command | `ScreenshotWithdraw`; records privacy withdrawal/invalidation, never deletes bytes |
| `GET /resources/attachments/revisions/{revision_id}/file/download-link`; `GET …/file/download?signature=` | `resource attachment file download --revision UUID --output PATH` | `AttachmentDownloadLink`; CLI fetches authenticated bytes and returns `AttachmentDownloadReceipt` after checking hash/size |
| `GET /resources/attachments/revisions/{revision_id}/parts/{ordinal}/download-link`; `GET …/download?signature=` | `resource attachment part download --revision UUID --part N --output PATH` | Same link/receipt with part identity, full human original-read gate |
| `GET /resources/attachments/revisions/{revision_id}/pages/{page}/preview-link?zoom=`; `GET …/preview?zoom=&signature=` | `resource attachment page preview --revision UUID --page N [--zoom 1\|2]` | `AttachmentDownloadLink`; signed bounded temporary preview, no source creation |
| `GET /attachment-sources/{source_id}/preview/download-link`; `GET …/preview/download?signature=` | `evidence attachment source preview --id UUID` | Signed raw-source PNG, original-read plus task access; not the cleared rendition link |
| Existing screenshot rendition preview/content routes | Existing screenshot preview command | Reviewed cleared pixels with attachment lineage checks; immutable rendition/hash/privacy receipt |

Archive/upload/review/source commands are atomic request operations: no `--wait` or
job success is invented. Upload/revise also accept `--dry-run`, validate bounded input
without storing files/rows/audits and return `AttachmentUploadPreview`. Dry-run is
advisory, not authorization; submission fully revalidates. The first slice's privacy
clear path reuses server rendering with no new public queue; later asynchronous
redaction uses existing screenshot jobs and their status/wait/cancel contract. Do not
advertise that branch until the privacy workflow is enabled.

Every command uses existing `Result` with exactly `ok`, `command`, `data`, `items`,
`warnings`, `cost`, `duration_ms`.
The `cost` field has the six resource counts defined by Result 5.0. Local
deterministic processing makes zero provider calls and creates no invented
`UsageRecord`. Future Provider usage (用量) follows shared read-only resource
preflight, admission and durable usage recording.

Illustrative accepted upload:

```json
{
  "ok": true,
  "command": "resource attachment upload",
  "data": {
    "attachment_id": "00000000-0000-4000-8000-000000000101",
    "revision_id": "00000000-0000-4000-8000-000000000102",
    "file_id": "00000000-0000-4000-8000-000000000103",
    "revision": 1,
    "state_version": 1,
    "review_state": "pending",
    "duplicate": false,
    "next_action": "review_uploaded_file"
  },
  "items": [],
  "warnings": ["User-supplied file; archive review does not confirm evidence"],
  "cost": {"calls": 0, "llm_tokens": 0, "ocr_pages": 0, "search_requests": 0, "browser_units": 0, "unresolved_calls": 0},
  "duration_ms": 18
}
```

Errors use `data.code`/`data.message`, optional safe IDs and a next-action reason,
never file text or signed URLs. JSON contains no file/base64/storage key; explicit
download receipts may contain only the caller's requested local output path and safe
descriptor, following existing `CertificateFileDownloadReceipt`. The link command's
ephemeral URL is sensitive transport output, not an audit record. Runtime schemas register the attachment commands with Result 5.0; older command serialization remains unchanged.
Sanitize validation errors as well: never echo Pydantic input values, uploaded
filenames, label text or multipart bodies in a 422 response or log.

| Exit | HTTP and behavior |
| --- | --- |
| 0 | Completed read/write or duplicate; dry-run validation completed. Readiness blockers remain visible and do not mean that B05 is usable. |
| 2 | 400/413/422 invalid input, unsupported enabled format/page/limits; 409 stale state/review/profile selection, privacy pending, disabled source branch or request-ID payload conflict. Correct input/state before retry. |
| 3 | 429/503 transient storage/process-capacity failure, retryable timeout; no successful revision/source reported. Respect Retry-After. |
| 4 | 401 unauthenticated, 403 denied action on a visible object, 404 absent/inaccessible task/object/signature; hash/encryption/integrity failure or nonretryable PDF process failure. |
| 5 | Reserved uniform partial-success code; unused for one atomic upload (including its parts), review or source. No partial archive masquerades as success. |

New attachment errors follow this table even where a legacy certificate error uses a
different exit code. Do not change the old command contract incidentally.

## B05 source binding and invalidation

After this contract and B05 integration are approved and accepted, add an additive
`attachment_page` selector to B05. It names an `evidence_source_id`, a privacy-reviewed
`asset_id`/`rendition_id` and expected image hash. The server resolves
`AttachmentAnnotationBinding`: task; task profile selection and exact declaration
field/link; task attachment selection; attachment/root/revision/file/approval; original
hash; PDF page; raw source PNG hash/profile/render time; sanitized rendition hash,
privacy-review ID/plan/mapping and authorized content plane. No path/URL, uploaded
PNG or scalar profile text can act as this selector.

The adapter reads via the extended `evidence_sources.require_source` and
`screenshots.rendition_access`/`read_rendition`, checking all parent bindings and current
gates. B05 operates on the privacy-cleared content plane, retains redactions and
mapping to the raw archive, and adds its candidate (候选)/release footer. Bound ancestry to
32 steps with cycle/missing-parent rejection as in B05; no access through a cleared
derivative to unreviewed raw pixels. Footer provenance says
`USER-SUPPLIED ATTACHMENT PAGE` plus server-owned kind/IDs/page/hash/render time; a
contract date or issuer field is not independently verified capture time.

Match the existing B05 `VendorRenditionBinding` coordinate convention:
`root_source_png` identifies the unmodified archived page for provenance, while
`source_png` is the **actual privacy-cleared stored rendition** supplied to the
renderer. Its `authorized_content` rectangle uses the rendition mapping's offsets
and content dimensions, excluding padding/footer. The renderer checks that entire
input PNG hash, strips only generated margins, then applies content-relative geometry.
Never supply `archive.preview` bytes where B05 expects sanitized input. Pin the
verified ancestry/mapping hashes as well; raw hashes do not authorize removed pixels.

Do not expose `attachment_page` until the annotation source union/resolver/footer,
DB source/rendition gates, source-origin read restrictions, job visibility/cancellation,
privacy mapping, card material resolver, B02 requirement gates, B07 human review/material
gates, draft dependencies and export release gates all understand it. Archive approval
or privacy clearance alone cannot attach/confirm Evidence. B05 produces an unconfirmed
candidate; the human supplies a visual observation and attaches it to a response
card, then the human confirms it. Archive approval does not confer commercial (商务)/technical (技术) confirmation authority. Tokens never confirm/export.

| Change | Required effect |
| --- | --- |
| New attachment/profile library revision only | Pinned task continues on its exact old revision; show an update notice, no silent retargeting or inferred file inheritance. |
| Explicit profile/task attachment replacement or deactivation | Old source remains historical; dependent candidates/releases become stale. A new selection/source/review is required. |
| Archive root/link withdrawal or pinned approval revoked | Invalidate dependent current material even if hashes are unchanged. Reapproval alone never restores old selection eligibility. Rejected revisions cannot be selected in the first place. |
| Privacy asset withdrawn, sanitized hash/plan/mapping changed, corrupt/missing original/page/rendition | Deny publication, cleared-pixel read, B05 acceptance/release and dependent draft/export; keep history and identify recovery owner. Never fall back to raw pages or another revision. |
| Actor authorization or source binding lost while rendering | Recheck after work and before publication; no stale source/decision may publish. |
| Card/B02/human approval or human-authority changes | Apply B05 invalidation and retain old decision history; only a current exact confirmation can produce a usable release. |

Extend `drafts.evidence_dependency`, `exports.build_manifest`/`fresh_manifest`/
`download_gate`, the B05 export DB gate and
the retained database approval-invalidation trigger with these attachment dependencies. Invalidate
both new use and previously generated export downloads according to the existing
live export gate. Library original history downloads remain distinct from export
authorization. Do not relax ordinary confidential-field, prototype (原型) or human-export
gates. Successful rendering never completes a requirement or marks a dashboard
(看板) card confirmed.

## Audit, recovery and failure modes

Use `versioned.audit` and existing local audit storage for successful writes, atomically
with DB state: `attachment.create`, `attachment.revise`,
`attachment.review.approve`, `.reject`, `.revoke`, `attachment.deactivate`,
`profile.attachment.link`, `profile.attachment.deactivate`, `task.attachment.select`,
`task.attachment.deactivate`, `attachment.source.create`, `attachment.privacy.clear`,
`attachment.privacy.hold`, `attachment.privacy.withdraw`, `attachment.invalidate`. Retain existing screenshot/
job/card/B05 audit events; do not create a second generic audit table.

Add `attachment.download.issue` and `attachment.download.read` for original/part/raw/
reviewed pixel deliveries, including inherited routes. The read audit means authorized
bytes were validated and prepared for delivery, not proof the browser received them.
An unavailable audit transaction prevents private-byte delivery. Failed attempts use
sanitized operational security events without confirming an unrelated object exists.
No success event for rolled-back writes; idempotent duplicates do not invent another
review. Store actor kind/token/agent session IDs, request ID, task/selection/revision/file/source/
review IDs, hashes, bounded counts, fixed reason codes and timestamps; exclude labels,
filenames, prices, personal data, signatures, credentials, source text and URLs.

Reuse `task_events.append` and the `produce_task_events` statement producer in
[the core baseline functions](../../server/migrations/baseline/00_functions_core.sql) for task changes and
access invalidation, and batched `task_board_projection.load`. Events contain IDs/
state only; use existing bounded target batches or `invalidate_all`. Fan-out runs in
bounded batches with a durable cursor; live resolver checks deny stale use before a
projection catches up. No per-dependent-file scan on one dashboard request.

| Failure | Observable state and next action |
| --- | --- |
| Wrong task/parent, guessed ID, wrong-purpose link | Uniform resource 404 before business-parent details; no bytes or existence signal. |
| Missing/stale expected version/review/selection | 409 with safe current revision where already authorized; preserve UI input and require explicit refresh. |
| Invalid PDF, active limit breach, disabled multipart/redaction path | No committed archive; the human supplies supported input. Never truncate pages, drop parts or silently flatten at lower DPI. |
| Unreviewed/rejected/revoked archive; empty or mismatched declaration | Selection blocked; inspect revision or repair explicit link. No “latest valid file” fallback. |
| Storage put/commit failure | No public successful revision; possible unreachable ciphertext as above. Reconcile/retry the same request ID. |
| Missing key/object, decryption/hash mismatch | Fail closed with sanitized integrity error; the human restores exact ciphertext/key from recovery material. No regenerated substitute at the same source ID. |
| Render deadline/resource failure or capacity exhaustion | No partial source. Retry only retryable failures; page/format bounds need corrected source, not an infinite retry. |
| Selection/authority changes during work | Discard publication; show the required source replacement. Orphan ciphertext is never a selectable result. |
| Privacy not clear or redaction required | Only the human with original-read authority can inspect; page remains blocked for downstream/B05/model consumers. |
| B05 adapter disabled or confirmed release pending/stale | Archive stays readable under its grants; show specific B05 blocker. No direct archive-to-export shortcut. |

## Acceptance plan

Before enabling routes, specify failure scenarios and exercise whole API/CLI/browser
flows with synthetic documents and human/token/agent identities. No live vendor calls are needed. The
implementation must produce repeatable
artifacts under ignored `data/work/attachment-archive-acceptance/`, never under `docs/`.

| Acceptance surface | Required evidence |
| --- | --- |
| Every table and source branch | Runtime human/token/agent gates; exact root/revision/file/task/profile parents; immutable history, no-delete, pointer completeness, typed branches and SQL bypass rejection. |
| Every HTTP route, inherited byte handler and job | Missing or unrelated IDs give uniform 404; forged cursors and expired signatures fail. Withdraw archive/privacy approval after link issue and verify delivery is denied. Tokens never receive labels, bytes or renditions through another route. |
| Complete archive workflow and races | Upload → review → link → task pin → source → privacy-clear receipt, repeat request IDs, divergent replay, concurrent revisions/reviews/selections; rendering blocked by concurrent selection change; transaction rollback/orphan behavior and storage failure. New library revision preserves pins; explicit replacement invalidates dependencies. |
| Byte/render/privacy gates | Real synthetic PDF pixels/hash, single-PDF byte identity, canonical 150-dpi source versus zoom preview; corrupt ciphertext/descriptor, huge page/decompression/allocation bound, child timeout/kill and capacity limit. For later multipart: ordering/rotation/EXIF original retention and sanitized composed output. For later redaction: actual removed pixels, mapping/hash forgery, withdrawn ancestor, legacy-route bypass. |
| Human/token/evidence gates | Tokens requesting any human-only scope, `evidence:confirm` or `export` fail issuance and raw SQL constraints. Upload/review/privacy never changes Evidence confirmation. Archive approval cannot confirm response Evidence. Unconfirmed/stale attachment Evidence cannot enter draft/export or download an existing invalidated export. |
| B05 enablement | One approved linked contract page → privacy-cleared source → cloud annotation candidate → human card attachment → current B02 and exact human/material approval → confirmed release → draft/export. Revoke each dependency and prove consumption/download fail. Until accepted, source-advertisement/submit tests show the branch disabled. |
| CLI and schema | Snapshot every command/variant in the route table: success/error, zero-cost Result 5.0, duplicate, dry-run, bounded list/cursor/history, download receipt and terminal codes. `bid schema` and HTTP inputs agree; no interactive fallback, secrets/pixels or partial-success archive. |
| Browser | Human upload, exact revision review, profile/task selection and page privacy; token metadata-only denial. Exercise rejected revision, redaction blocker, failed upload/retry, library updates and disabled annotation entry. |
| Scale/audit/recovery | Seed large archive/review/selection histories; assert bounded query/image reads, stable pagination and batched board updates. Verify encrypted originals/parts/labels and metadata-only audit events. Restore exact retained file and rotate data keys without changing plaintext hashes; live human/token scope checks still gate access. |

Reuse acceptance patterns from `server/tests/test_certificate_files.py`,
`test_certificate_images.py`, `test_evidence_sources.py`, `test_profiles.py`,
`test_storage.py`, `test_page_previews.py`, `test_pdf_process.py`
and `test_pdf_raster.py`, plus human/B05 gate suites. Tests
must write a sanitized command manifest, fixture hashes, result JSON, downloaded
synthetic PDF/PNG hashes, JUnit and browser trace/screenshots to the acceptance directory.
Each artifact identifies the scenario and expected gate; never include credentials,
signed URLs or real contract data. Stub storage failure tests do not establish live
S3 acceptance; record any required isolated S3 run separately and truthfully.

## Decisions

These decisions define the archive boundary.

| Decision | Approved default | Reason / enablement consequence |
| --- | --- | --- |
| First file slice | One unchanged unrotated PDF, all listed attachment categories | Completes responsibility/review/task chain before multipart complexity; scans can initially be supplied as PDFs. |
| Archive review | The single human reviews exact revisions; no assignment step | Review remains explicit and timestamped. |
| Review separation | Human archive and privacy review with recorded timestamps | The same human may review; archive approval confers no Evidence confirmation. |
| Read privacy boundary | Metadata-only explicit tokens; originals/raw pages human; cleared pages available to authorized huma task readers | Prevents contract prices/personal data entering automation through generic image routes. This narrows B05 reads for this source kind. |
| Profile links | Exact revision plus one nonempty declaration field; no automatic copy-forward | Avoids claiming a file supports a changed declaration; multiple explicit links permit legitimate reuse. |
| Task selection | Require an active exact TaskOrgProfile/link/approval; no unattached task-only files initially | Keeps context visible and reuses current profile selection workflow. |
| Multipart and image enablement | Later within the same contract, after subprocess/composition/metadata acceptance | Reuses certificate mechanics without claiming in-process parsing is resource isolated. |
| Privacy workflow | Clear unchanged page first; redacted pages later through existing screenshot privacy chain | Privacy is per exact page/rendition; renderer output must be reviewed before B05. |
| Canonical page identity | Existing 150-dpi RGB `pdf-page-preview-v1`; no renderer downgrade | Makes source binding verifiable; zoom previews remain disposable. |
| Library updates | No automatic task retarget; historical archive approvals retained unless explicitly revoked | Preserves fixed-task evidence while keeping exact source and approval checks. |
| Lifecycle/retention | One-way deactivation, indefinite history, no delete or automatic object expiry | Provides recoverable provenance; any future erasure policy needs its own decision and implementation. |
| Download mechanism | Existing 300-second signed application routes with live authorization | Reuses encryption/read integrity and immediate access revocation; no public object-store links. |
| Storage/DB failure recovery | Retain inaccessible encrypted orphans and reconcile IDs; no garbage collector in this slice | Matches actual Storage capabilities and avoids an implicit deletion policy. |
| B05 release order | Archive slice first; attachment selector enabled only after full B05 dependency/read/export acceptance | The blocked-next-action UI stays truthful while approved certificate B05 can proceed independently. |
