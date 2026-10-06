---
kind: plan
---

# Org attachment archives

Status: **Pending approval, not implemented.** Corresponds to [roadmap](roadmap.md)
F03/R05/R06/U01 and the profile/contract prerequisite of B05.

The [Pydantic and service interface draft](attachment-archive/attachment_archive_contracts.py)
is a review-only contract. It registers no routes, commands, tables, permissions or
workers. Implementation requires owner approval under [agent.md](../../agent.md#workflow).
The approved [cloud annotation contract](annotation.md) remains unchanged; its
profile/contract source branch stays disabled until this archive chain and the B05
adapter below pass acceptance.

## Goal and boundary

A bid specialist (投标专员) maintains supporting files for an org (organization/tenant;
单位), links an exact file revision to an org profile (单位资料) declaration, and selects
it explicitly for a task (任务). Examples are business licences, qualification (资格)
scans, signed contracts and performance records. The console shows the responsible
uploader, reviewer, blocking condition and next action. Staff need not understand
storage keys or revision hashes to complete the workflow.

Keep uploaded bytes, normalized PDFs, page PNGs and review history immutable and
encrypted. A file is user-supplied evidence (证据), not proof of authenticity. Archive
review accepts a revision for internal selection; privacy review accepts exact page
pixels for team use. Neither is human confirmation (人工确认) of a response card
(响应卡), confirmation of a qualification claim, or permission to export a bid
(标书). The archive/source rows never acquire `confirmed_by` or direct eligibility
for a draft (初稿) or export. Existing certificate (证书) workflows remain the home for certificates
already managed there; do not upload a second copy merely to annotate one.

This contract covers cloud API/CLI/console archival, human review, declaration links,
task pins, page sources, downloads, deactivation and the B05 handoff. It excludes OCR,
extraction of contract terms/prices, authenticity verification, signatures/seals,
pricing strategy, arbitrary URLs, Office/ZIP/HTML upload, cross-org sharing, public
links, automatic resource selection, deletion and complete bid attachment assembly.
No attachment text or image enters model prompts, memory (记忆), check/score, or export
just because it is archived. Later consumers require an explicit reviewed contract.

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
| [api/common.py](../../server/app/api/common.py): `signed_link`, `check_signature`, `attachment`; [api/resources.py](../../server/app/api/resources.py): certificate/source download routes; [api/main.py](../../server/app/api/main.py): `context` | Reuse 300-second application links plus live authentication. Existing signatures bind org, purpose and object IDs, not actor/hash; immutable IDs resolve hashes at download. No S3 presigned/public URL or raw-parts download route exists. New part routes and lineage checks must be explicit. |
| [screenshots.py](../../server/app/services/screenshots.py): `prepare`, `ingest`, `resolve_source`, `rendition_access`, `withdraw`, `resolve_image_material`; [screenshot_renderer.py](../../server/app/providers/screenshot_renderer.py): `render`, `validate_png` | Reuse `ScreenshotAsset`, `ScreenshotRendition`, `ScreenshotPrivacyReview`, existing privacy plans and renderer. Add a server-bound attachment source, not a client download/re-upload receipt or second redaction engine. This cloud extension is not implemented. |
| [response_cards.py](../../server/app/services/response_cards.py): `resolve_material`, `build_evidence`, `confirmation_inputs`; [exports.py](../../server/app/services/exports.py): `build_manifest`, `fresh_manifest`, `download_gate` | Existing `certificate_pdf_page` can contribute to exports after human page review. Do not alias attachments to that kind or to scalar `org_profile` material. New attachment evidence uses the B05 image chain only after its gates are implemented. |
| [auth.py](../../server/app/services/auth.py): `Identity`, `authenticate`, `membership`, `HUMAN_ONLY_SCOPES`, `SCOPES`, `ROLE_SCOPES`; [task_authorization.py](../../server/app/services/task_authorization.py): `task_authorized`; [task_workflow.py](../../server/app/services/task_workflow.py): `access` | Preserve live org membership, token/role intersection, task membership, write ceilings and archival gates. Same-org resource access is not automatically task access. |
| [contracts.py](../../server/app/schemas/contracts.py): `CONTRACT_VERSION`, `Result`, `Cost`; [budget_preflight.py](../../server/app/services/budget_preflight.py): `attach` | Task-budget work is present: use Result 4.0. The design's two-field cost example and older draft examples do not define this contract. No new billed Provider is introduced. |

The `memory/memory_contracts.py` example has moved to
[runtime memory contracts](../../server/app/schemas/memory_contracts.py); the
[memory plan](memory.md), [org-console plan](org-console.md) and
[checking plan](check.md) provide structure, not authority to copy stale claims.

## First vertical slice

Recommend **one unchanged PDF → archive review → exact declaration link → task pin →
one archived source PNG → human privacy clearance → visible B05 readiness**. Accept
business-licence or contract/performance PDFs with the same path; kind is a descriptive
category, not a different storage or permission chain. Include API, CLI and console.

1. A human `admin` or `bidder` uploads one unrotated PDF and assigns a live human archive
   reviewer with one of those roles. The server assigns the uploader as custodian and
   publishes revision 1 as awaiting review. Show “Review uploaded file” to that reviewer.
2. The reviewer inspects the original and records approve/reject against its exact
   revision, file hash and metadata hash. Approval means suitable for internal use,
   with no claim of external authenticity. Rejection preserves bytes and tells the
   custodian to upload a corrected revision. Self-review is allowed and visibly recorded;
   no invented separation-of-duties rule prevents a small team from proceeding.
3. A human links the accepted revision to one existing nonempty declaration field on
   an exact `OrgProfileRevision`. Linking asserts relevance only. A task contributor
   explicitly selects that link under the matching active `TaskOrgProfile`. The task
   displays the fixed file revision and any later-library-version notice.
4. A human with original access requests one page. Render and persist its genuine PNG
   in `EvidenceSource`, with no Evidence creation. If task/profile/link authority changed
   during rendering, publish nothing. Archive creation may precede requirement extraction.
5. After an extraction exists, the privacy reviewer opens the raw page and attests the
   exact reviewed PNG hash. Server-bound ingestion into the existing screenshot chain
   records a human review and immutable rendition. The first slice accepts only an
   unchanged page safe for team use; `needs_redaction` blocks team pixels/B05 and directs
   the custodian to the later privacy workflow. No clear attestation is inferred.
6. The console exposes that page as “Ready for annotation” only if the attachment B05
   adapter is enabled. Until then, it says `annotation_adapter_not_enabled`; it never
   claims a completed response (响应). B05 completion remains a separate dependency.

The full contract includes ordered PDF/PNG/JPEG parts and redacted page derivatives.
Enable these only after their composition/metadata/privacy acceptance passes. Reject
disabled branches with `attachment_upload_mode_not_enabled` or
`attachment_redaction_not_enabled`, not silent conversion or fallback. Existing
certificate uploads retain their own enabled formats. A first slice is complete when
its archive flow works through the real console and task role, even while B05 is
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
  pins hashes and the reviewing Membership; rejection/revocation cannot alter it.
  Selection pins its approval ID. A later revocation of that revision's approval
  invalidates its dependent selections; reapproval requires explicit new selection and
  privacy review, never automatic revival of prior accepted material. A review of
  revision 2 does not withdraw revision 1.
  Approve is allowed from pending/rejected/revoked, reject from pending, and revoke
  from approved. Repeating an already approved exact decision returns its receipt;
  changing a positive decision requires explicit revoke. Invalid transitions conflict.
- Custodian/reviewer reassignment changes only root workflow state under an expected
  state version and audit (审计). Removed reviewers block pending work until reassigned.
  A recorded archive decision remains historical after its author leaves; it is not
  silently erased. Current Evidence/co-sign authority freshness follows B05/B07 and
  is separate from this archive-review rule.
- Root, declaration link and task-selection deactivation are one-way. Retain revisions,
  files, parts, links, reviews, pages, privacy/rendition records and audits. No DELETE
  route, retention timer, storage lifecycle expiry or cascading delete is permitted.
  Fix accidental deactivation through explicit new records and new reviews, not by
  reviving previous decisions. History reads still require live authorization.
- Root deactivation or link withdrawal blocks new use and invalidates current dependent
  B05 material. Historical originals remain available only to authorized humans via an
  explicit history read. Task archival blocks writes/render/review/selection, preserving
  authorized history; unarchival does not revive withdrawn selections.
- Retention defaults to indefinite preservation, including backups and encryption-key
  recovery material. This is a product default, not a claim about a legal retention
  period. Any later mandatory purge process needs a separately approved contract; this
  one provides no soft-delete UI masquerading as erasure.

## Data model and migration outline

Reuse [schema types](attachment-archive/attachment_archive_contracts.py) including
`Contract`, `Result`, `Cost`, `CertificateScanFile`, `CertificatePart`,
`CertificatePartOptions`, `EvidenceSourcePreview`, `OrgProfileRevision`,
`TaskOrgProfileSnapshot`, `Sha256`, `PNGDescriptor`, `PixelRect`, `ContentMapping` and
`RenditionView`. Certificate descriptor names are reused as byte-format types only;
the attachment view has its own domain IDs. Models validate structure, not authority.

Every new table below has **NOT NULL `org_id`, ENABLE and FORCE ROW LEVEL SECURITY**,
matching `USING`/`WITH CHECK` current-org policies, `(org_id,id)` uniqueness and no access
without org context. Use runtime `bid_app` without ownership/BYPASSRLS. Actors reference
`(org_id,user_id) → memberships`, not bare global users. All parent references include
org and the complete binding, adding unique keys to parents first.

| New table | Content, constraints and allowed mutation |
| --- | --- |
| `attachment_archives` | ID, current content revision, `state_version`, active flag, custodian/reviewer Membership IDs. Only pointer/workflow/deactivation updates under a root lock; no delete. Deferred current pointer binds `(org_id,id,current_revision)` to the revision table. |
| `attachment_revisions` | Root/revision, kind, encrypted display label, metadata hash, creator/time, request ID/payload hash. `UNIQUE(org_id,attachment_id,revision)` and complete parent unique keys; append-only. One committed revision must have one file in the same transaction. |
| `attachment_files` | Root/revision IDs, safe `CertificateScanFile` descriptor, storage key and encrypted upload name for the single-PDF case. `UNIQUE(org_id,attachment_revision_id)`; composite FK binds root/revision. File descriptor/key immutable; no attaching a file to an already committed revision. |
| `attachment_file_parts` | File/root/revision IDs, `CertificatePart`, storage key, encrypted original upload name. Unique `(org_id,attachment_file_id,ordinal)`; composite FK binds all parents. Append-only, created with the file; ordinal/page spans contiguous and total pages agree with composed PDF. Single unchanged PDF has no duplicated part row. |
| `attachment_reviews` | Root/revision/file, hashes, decision, prior review ID, reviewer Membership/time, reason code, request ID/hash. Append-only; serialize on the revision/root. Expected latest review must match; approving pins bytes/metadata, never an Evidence confirmer. Prior-review FK includes the same revision; hash/actor checks run in DB and service. |
| `profile_attachment_links` | Exact profile/root/revision, declaration field, attachment/root/revision/file and approval IDs, creator/time, active flag/state version. Composite FKs pin both chains. Unique active `(org_id,profile_revision_id,field,attachment_revision_id)`; field must be nonempty on the pinned profile. Only deactivation may update a link; correction creates another. |
| `task_attachments` | Task, exact `task_org_profile_id`/profile revision/link, attachment/root/revision/file/approval, selected by/time, active flag/state version. Unique active slot `(org_id,task_id,task_org_profile_id,attachment_id)`; lot inherited from profile selection. Composite FKs and a gate verify every redundant binding. Replacement only deactivates the old row and appends a new one atomically. |
| `attachment_privacy_holds` | Append-only negative page decisions: source/task, exact raw PNG hash, prior hold, reviewer/time, request ID/hash and fixed `sensitive_content` reason. Composite FK pins EvidenceSource/task/org; prior hold pins the same source. It grants no clearance and stores no image. The existing screenshot privacy review resolves an exact hold; no second positive privacy-review system is introduced. |

Extend existing `evidence_sources`, **not a new page store**: add an internal source
kind and attachment selection/root/revision/file/link/approval references. Certificate
columns become nullable only behind a CHECK requiring exactly one complete source
branch. Legacy rows are `user_supplied_certificate_pdf`; the new branch is
`user_supplied_attachment_pdf`. New composite FKs bind selection/task/file/revision.
Keep `source_unconfirmed`, page/count/profile/hash/path checks and append-only grants.
Use separate partial unique keys for each branch's `(org_id,selection_id,page,profile)`;
queries must branch explicitly rather than silently excluding attachment rows in a
certificate-only join. Preserve old `EvidenceSourceArchive` responses; use an additive
typed attachment view and separate list route.

Extend existing screenshot source bindings with an attachment discriminator and exact
EvidenceSource identity; add its org/task composite FKs, immutable human privacy receipt
and source checks. Reuse the existing asset/rendition/review tables and
`screenshot-privacy-v1`/`screenshot-markup-v1` profiles. Do not store original PDFs or
raw page copies in a second screenshot-original table. Task sources reference the
same retained file. Source PNG and a redacted/marked rendition are distinct intentional
artifacts, with explicit mapping and hashes.

Storage keys are server-generated:

```text
org/{org_id}/attachment/{attachment_id}/{revision_id}/{sha256}.pdf
org/{org_id}/attachment/{attachment_id}/{revision_id}/parts/{ordinal}/{sha256}.{ext}
org/{org_id}/evidence-source/{source_id}/{sha256}.png
```

Screenshot derivatives keep existing `org/{org_id}/screenshots/...` paths. DB checks
tie keys to IDs/hash/type; no client key, absolute path or filename enters a key.
Encrypt labels/original names with org/record-bound data envelopes using
`Secrets.for_data` in [security.py](../../server/app/core/security.py); public descriptors
use neutral names. No search index over plaintext contract contents is created.

Migration order after approval:

1. Add parent composite keys, the eight tables, minimal grants, RLS and indexes. Reuse
   [0006_versioned_profiles.py](../../server/migrations/versions/0006_versioned_profiles.py),
   [0008_certificate_files.py](../../server/migrations/versions/0008_certificate_files.py)
   and [0031_certificate_file_parts.py](../../server/migrations/versions/0031_certificate_file_parts.py)
   for immutable revision/part guards; do not invent empty legacy attachment records.
2. Extend EvidenceSource and screenshot branches compatibly. Preserve existing source
   bytes/IDs/response shapes. New nullable columns have branch-specific NOT NULL CHECKs.
   Add exact-parent and page-range gates following
   [0009_evidence_sources.py](../../server/migrations/versions/0009_evidence_sources.py).
3. Add human decision/lifecycle gates, append-only grants and token exclusions together.
   RLS/FK isolation must run before parent-dependent error disclosure, following
   [0049_product_guard_order.py](../../server/migrations/versions/0049_product_guard_order.py).
   Deferred cyclic completeness checks run at commit. Raw SQL cannot bypass review,
   file-new-revision-only or branch-completeness checks.
4. Extend `task_workflow` action ceilings, source/screenshot access, invalidation,
   job visibility and task events. Lock task first, then roots/revisions in stable ID
   order; library-only writes never acquire task locks in reverse order. Recheck
   lifecycle/review state at publication and every consumer, so asynchronous board
   projection updates cannot authorize stale content.
5. Enable archive routes after isolation/gate acceptance. B05 stays disabled until its
   adapter/gates pass. Rollback disables new writes/consumption and retains tables,
   ciphertext/history; repair forward, no destructive downgrade.

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

For every original, part and PNG read, use `Storage.read_bounded(org_id,key,limit)`
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
is explicit. Signed cursors bind org/actor/route/filters/task and live access version.
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

Persist replay identity for every mutation, including assignment/deactivation, in
the existing audit event's bounded request ID/payload-hash fields. Add an attachment-
action partial unique index over org/actor/command/request ID; look up under the
same object lock. Revision/review/link/selection/hold rows also retain their originating
request ID and payload hash. The root state version advances on each workflow change;
readiness is computed from exact pinned review IDs, never only that counter.

## Privacy, permissions and signed downloads

Contracts can contain prices, contacts, personal identifiers, signatures and bank
details, including scanned pixels and original image metadata. First-slice defaults:
raw originals, parts and unreviewed pages are human-only for `admin`/`bidder`; other
task readers see safe workflow metadata and privacy-cleared renditions only. An
encrypted file is not automatically privacy-cleared. Category, IDs, revision/status
and responsibility are safe metadata; user-entered labels/filenames are restricted
details and are excluded from token responses, audit, events, error messages and URLs.

| Capability / proposed scope | Role and task boundary | Token policy |
| --- | --- | --- |
| `attachment:read` | All four org roles may list safe metadata; task views also require live `task:read` and task access. Root details with decrypted labels use original-read authority. | Only newly issued explicit scope, intersected with issuer role/task grants; no file or pixel access. No addition to built-in `AGENT_SCOPES`. |
| `attachment:write` | Human admin/bidder uploads/revises/assigns custody or reviewer and creates/deactivates profile links; require `profile:read`/`profile:write` for links. | Human-only; absent from `SCOPES` and DB token allowlist. |
| `attachment:review` | Assigned human admin/bidder inspects exact revision and approves/rejects; admin/bidder with manage authority can revoke with a reason. | Human-only; workers cannot manufacture review. |
| `attachment:manage` | Human admin/bidder deactivates root, withdraws approved use, or reassigns a departed reviewer; expected state/review required. | Human-only. |
| `attachment:original:read` | Human admin/bidder reads library originals/parts/raw previews. Task raw-page routes additionally require task/source read. Library access never authorizes an arbitrary task. | Human-only, even when token issuer can read originals. |
| `task:attachment` | Human admin/bidder, active task owner/contributor, selects/replaces/deactivates and creates task sources; requires matching `task:profile`, `profile:read` and `evidence:source:write` for sources. Reviewers/observers cannot select. | Human-only. |
| `attachment:privacy` | Human admin/bidder, live task contributor and assigned archive reviewer or explicit reassignment, attests exact pixels; requires original access and `screenshot:ingest`. | Human-only. |
| `attachment:page:read` | Human admin/bidder/technical/viewer with task/source/screenshot read can view **privacy-cleared** page renditions. Unreviewed/withdrawn pages require original-read authority and an explicit historical endpoint. | Human-only for all attachment-derived pixels in this contract, including B05 candidates/releases. |

Add human-only scopes to `HUMAN_ONLY_SCOPES`, role ceilings, task ceilings and database
token CHECKs together, retaining **all existing exclusions, `evidence:confirm` and
`export`**. `attachment:read` does not imply older certificate/source scopes. Old tokens
gain nothing. Issuance requests mixing allowed and human-only scopes fail atomically;
never silently strip forbidden grants. Platform operator sessions have no business
access; a separate active org Membership/session is required. Workers may process
only a fixed authorized request with live input checks; `actor_kind=worker` is not a
read bypass. Hidden buttons are not authorization.

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

Keep lineage metadata resolution separate from raw-byte read permission. A human
technical contributor may annotate an authorized cleared rendition without receiving
`attachment:original:read`; resolving its fixed parents must not force a raw preview
download. Raw-page endpoints retain the stronger permission. Internal integrity checks
may inspect only the fixed authorized ancestry and must never return original bytes
to a caller allowed only the cleared content.

Use `signed_link`/`check_signature`, authenticated `context`, and the same-org
resource resolver both when issuing a link and at byte delivery. Links expire in
300 seconds and bind kind/org plus immutable revision/file/part/source/rendition IDs.
Extend typed identifiers if needed, not cryptography. Resolve the descriptor/hash
server-side and validate bytes; there is no caller-supplied key, redirect or cross-org
deduplication. The link is not bearer authority and does not grant permissions to
another same-org reader. Membership removal, role/task removal, archive withdrawal,
privacy withdrawal and approval invalidation take effect at download, even before
expiry. Explicit original-history access remains available as described above.

Responses use `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`; neutral
download names and attachment disposition for original/part bytes. The console fetches
with live credentials and disposes in-memory preview URLs. Do not persist signatures
in browser storage, events, audits, tracing or referrers. Disable logging of link query
parameters and multipart content. There is no anonymous image link or S3 credential
exposure. A signature of the wrong kind/ID/org or an expired signature returns 404.

Redaction (遮挡) must happen **before** B05. Reuse `ImagePlan.redact`,
`ScreenshotRenderer`, `PreparedScreenshot`, immutable `ScreenshotPrivacyReview` and
`ContentMapping`; B05 never paints privacy masks. Later redacted input pins source
PNG hash, plan hash, prepared image hash, exact reviewer and stored rendition/hash.
The human reviews the actual sanitized pixels and declares clearance; the server
verifies them by rendering from the archived page. Reject forged uploads, changed
hashes, out-of-bounds plans, masks that destroy the claimed supporting region and
unreviewed derivatives. Redaction does not certify contract truth. Withdrawn privacy
assets invalidate descendants; no widening back to the raw page through ancestor reads.

The privacy endpoint also accepts `mode=needs_redaction`, without an extraction or
rendition: append a negative `attachment_privacy_holds` receipt bound to the exact
page/hash and show the custodian as responsible. Lock the source while checking the
expected latest hold. A later hold withdraws previously cleared usability; it cannot
delete a privacy review. `mode=clear` requires no hold. The later `mode=redacted` branch
adopts an already human-reviewed rendition from the existing privacy workflow and must
resolve the exact latest hold, with a nonempty redaction plan and a new review after
that hold. Record the hold binding on the existing `ScreenshotPrivacyReview` through
an org/task/source composite reference. Reused old clearances cannot resolve a new
hold. Thus the first slice can record a durable blocker without fabricating safe pixels
or implementing a second redaction engine.

[confidential.py](../../server/app/services/confidential.py) and
[redaction.py](../../server/app/services/redaction.py) protect text/outbound workflows;
they are not a PDF or pixel privacy scanner. No automatic “safe” decision, OCR, Vision,
LLM or external provider call is made here. Any later model consumer must separately
authorize sanitized bytes and preserve confidential-field (保密字段) controls.

## HTTP, CLI and Result 4.0

All routes below are proposed authenticated org routes. Request org/actor/review time,
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
| `POST /resources/attachments/{id}/assignment` | `resource attachment assign --id UUID --input INPUT.json` | `AttachmentAssignment` → `AttachmentShowData` / [] |
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
| `GET /attachment-sources/{source_id}/preview/download-link`; `GET …/preview/download?signature=` | `evidence attachment source preview --id UUID` | Signed raw-source PNG, original-read plus task access; not the team rendition link |
| Existing screenshot rendition preview/content routes | Existing screenshot preview command | Reviewed team pixels with attachment lineage checks; immutable rendition/hash/privacy receipt |

Archive/upload/review/source commands are atomic request operations: no `--wait` or
job success is invented. Upload/revise also accept `--dry-run`, validate bounded input
without storing files/rows/audits and return `AttachmentUploadPreview`. Dry-run is
advisory, not authorization; submission fully revalidates. The first slice's privacy
clear path reuses server rendering with no new public queue; later asynchronous
redaction uses existing screenshot jobs and their status/wait/cancel contract. Do not
advertise that branch until the privacy workflow is enabled.

Every command uses existing `Result` with exactly `ok`, `command`, `data`, `items`,
`warnings`, `cost`, `duration_ms`. Decimal amounts serialize as strings. Set currency
from deployment configuration; local deterministic processing has `basis=zero`, zero
amounts and no `UsageRecord` pretending that a model was called. Storage accounting is
not a quoted monetary charge. Future Provider usage (用量) follows the existing budget
preflight/admission/accounting contract, not an attachment-specific budget object.

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
  "cost": {
    "llm_tokens": 0,
    "ocr_pages": 0,
    "usd": 0.0,
    "basis": "zero",
    "charge": "0",
    "billing_currency": "USD",
    "task_amount": "0",
    "unpriced_calls": 0,
    "unresolved_calls": 0
  },
  "duration_ms": 18
}
```

Errors use `data.code`/`data.message`, optional safe IDs and a next-action reason,
never file text or signed URLs. JSON contains no file/base64/storage key; explicit
download receipts may contain only the caller's requested local output path and safe
descriptor, following existing `CertificateFileDownloadReceipt`. The link command's
ephemeral URL is sensitive transport output, not an audit record. Register schemas
only during implementation; this draft imports Result 4.0 without changing runtime
`bid schema` or older command serialization.
Sanitize validation errors as well: never echo Pydantic input values, uploaded
filenames, label text or multipart bodies in a 422 response or log.

| Exit | HTTP and behavior |
| --- | --- |
| 0 | Completed read/write or duplicate; dry-run validation completed. Readiness blockers remain visible and do not mean that B05 is usable. |
| 2 | 400/413/422 invalid input, unsupported enabled format/page/limits; 409 stale state/review/profile selection, archived task, privacy pending, disabled source branch or request-ID payload conflict. Correct input/state before retry. |
| 3 | 429/503 transient storage/process-capacity failure, retryable timeout; no successful revision/source reported. Respect Retry-After. |
| 4 | 401 unauthenticated, 403 denied action on a visible object, 404 absent/inaccessible org/task/object/signature; hash/encryption/integrity failure or nonretryable PDF process failure. |
| 5 | Reserved uniform partial-success code; unused for one atomic upload (including its parts), review or source. No partial archive masquerades as success. |

New attachment errors follow this table even where a legacy certificate error uses a
different exit code. Do not change the old command contract incidentally.

## B05 source binding and invalidation

After this contract and B05 integration are approved and accepted, add an additive
`attachment_page` selector to B05. It names an `evidence_source_id`, a privacy-reviewed
`asset_id`/`rendition_id` and expected image hash. The server resolves
`AttachmentAnnotationBinding`: org/task; task profile selection and exact declaration
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
privacy mapping, card material resolver, B02 requirement gates, B07 domain/co-sign
gates, draft dependencies and export release gates all understand it. Archive approval
or privacy clearance alone cannot attach/confirm Evidence. B05 produces an unconfirmed
candidate; the human supplies a visual observation and attaches it to a response
card, then the current review domain (职责) confirms it. Admin archive authority does
not confer commercial (商务)/technical (技术) confirmation authority. Tokens never confirm/export.

| Change | Required effect |
| --- | --- |
| New attachment/profile library revision only | Pinned task continues on its exact old revision; show an update notice, no silent retargeting or inferred file inheritance. |
| Explicit profile/task attachment replacement or deactivation | Old source remains historical; dependent candidates/releases become stale. A new selection/source/review is required. |
| Archive root/link withdrawal or pinned approval revoked | Invalidate dependent current material even if hashes are unchanged. Reapproval alone never restores old selection eligibility. Rejected revisions cannot be selected in the first place. |
| Privacy asset withdrawn, sanitized hash/plan/mapping changed, corrupt/missing original/page/rendition | Deny publication, team-pixel read, B05 acceptance/release and dependent draft/export; keep history and identify recovery owner. Never fall back to raw pages or another revision. |
| Membership/task write authority lost while rendering | Recheck after work and before publication; no source/decision is published by stale authority. Existing authorized readers still follow their own live grants. |
| Card/B02/co-sign approval/policy/signature changes | Apply B05 invalidation and retain old decision history; only a current exact confirmation can produce a usable release. |

Extend `drafts.evidence_dependency`, `exports.build_manifest`/`fresh_manifest`/
`download_gate`, the B05 export DB gate and
`team_cosign_invalidate_dependencies` with these attachment dependencies. Invalidate
both new use and previously generated export downloads according to the existing
live export gate. Library original history downloads remain distinct from export
authorization. Do not relax ordinary confidential-field, prototype (原型) or human-export
gates. Successful rendering never completes a requirement or marks a dashboard
(看板) card confirmed.

## Audit, recovery and failure modes

Use `versioned.audit` and existing org audit storage for successful writes, atomically
with DB state: `attachment.create`, `attachment.revise`, `attachment.assign`,
`attachment.review.approve`, `.reject`, `.revoke`, `attachment.deactivate`,
`profile.attachment.link`, `profile.attachment.deactivate`, `task.attachment.select`,
`task.attachment.deactivate`, `attachment.source.create`, `attachment.privacy.clear`,
`attachment.privacy.hold`, `attachment.privacy.withdraw`, `attachment.invalidate`. Retain existing screenshot/
job/card/B05 audit events; do not create a second generic audit table.

Add `attachment.download.issue` and `attachment.download.read` for original/part/raw/
reviewed pixel deliveries, including inherited routes. The read audit means authorized
bytes were validated and prepared for delivery, not proof the browser received them.
An unavailable audit transaction prevents private-byte delivery. Failed attempts use
sanitized operational security events without confirming another org's object exists.
No success event for rolled-back writes; idempotent duplicates do not invent another
review. Store actor kind/Membership IDs, request ID, task/selection/revision/file/source/
review IDs, hashes, bounded counts, fixed reason codes and timestamps; exclude labels,
filenames, prices, personal data, signatures, credentials, source text and URLs.

Reuse `task_events.append`/statement producers in
[task_event_sql.py](../../server/app/services/task_event_sql.py) for task changes and
access invalidation, and batched `task_board_projection.load`. Events contain IDs/
state only; use existing bounded target batches or `invalidate_all`. Fan-out runs in
bounded batches with a durable cursor; live resolver checks deny stale use before a
projection catches up. No per-dependent-file scan on one dashboard request.

| Failure | Observable state and next action |
| --- | --- |
| Wrong org/task/parent, guessed ID, wrong-purpose link | Uniform resource 404 before business-parent details; no bytes or existence signal. |
| Missing/stale expected version/review/selection | 409 with safe current revision where already authorized; preserve UI input and require explicit refresh. |
| Invalid PDF, active limit breach, disabled multipart/redaction path | No committed archive; custodian supplies supported input. Never truncate pages, drop parts or silently flatten at lower DPI. |
| Reviewer removed or no live assignee | `reviewer_unavailable`; admin/bidder reassigns, no fallback approval. |
| Unreviewed/rejected/revoked archive; empty or mismatched declaration | Selection blocked; inspect revision or repair explicit link. No “latest valid file” fallback. |
| Storage put/commit failure | No public successful revision; possible unreachable ciphertext as above. Reconcile/retry the same request ID. |
| Missing key/object, decryption/hash mismatch | Fail closed with sanitized integrity error; authorized operator restores exact ciphertext/key from recovery material. No regenerated substitute at the same source ID. |
| Render deadline/resource failure or capacity exhaustion | No partial source. Retry only retryable failures; page/format bounds need corrected source, not an infinite retry. |
| Selection/authority changes during work | Discard publication; show replace-source or reassign action. Orphan ciphertext is never a selectable result. |
| Privacy not clear or redaction required | Only authorized raw reviewers can inspect; page remains blocked for team/B05/model consumers. |
| B05 adapter disabled or confirmed release pending/stale | Archive stays readable under its grants; show specific B05 blocker. No direct archive-to-export shortcut. |

## Acceptance plan

Before enabling routes, specify failure scenarios and exercise whole API/CLI/browser
flows with synthetic documents in two orgs A/B. No live vendor calls are needed. This
draft adds no runtime tests or service changes; implementation must produce repeatable
artifacts under ignored `data/work/attachment-archive-acceptance/`, never under `docs/`.

| Acceptance surface | Required evidence |
| --- | --- |
| Every new table; extended EvidenceSource and screenshot branches | As runtime `bid_app`, A cannot SELECT/INSERT/UPDATE B rows; no-org context denies; FORCE RLS cannot be bypassed. Exercise same-org wrong root/revision/file/task/profile/reviewer parents, append-only grants, no-delete, current-pointer completeness, wrong branch and SQL human/token gate bypass. Include cross-org error ordering. |
| Every HTTP route above, including lists/history/links/byte handlers and inherited screenshot/job/annotation/export paths | Parameterized two-org route inventory proves foreign and absent IDs produce matching 404, forged cursors hide rows, expired signatures deny, same-org unauthorized task denies. Test issue-then-role/member/task/archive/privacy revocation before download. Token metadata scope never yields labels/raw bytes/renditions through another route. |
| Complete archive workflow and races | Upload → review → link → task pin → source → privacy-clear receipt, repeat request IDs, divergent replay, concurrent revisions/reviews/selections, removed assignee and reassignment; rendering blocked by concurrent selection change; transaction rollback/orphan behavior and storage failure. New library revision preserves pins; explicit replacement invalidates dependencies. |
| Byte/render/privacy gates | Real synthetic PDF pixels/hash, single-PDF byte identity, canonical 150-dpi source versus zoom preview; corrupt ciphertext/descriptor, huge page/decompression/allocation bound, child timeout/kill and capacity limit. For later multipart: ordering/rotation/EXIF original retention and sanitized composed output. For later redaction: actual removed pixels, mapping/hash forgery, withdrawn ancestor, legacy-route bypass. |
| Human/token/evidence gates | Tokens requesting any human-only scope, `evidence:confirm` or `export` fail issuance and raw SQL constraints. Upload/review/privacy never changes Evidence confirmation. Admin archive review cannot sign a domain. Unconfirmed/stale attachment Evidence cannot enter draft/export or download an existing invalidated export. |
| B05 enablement | One approved linked contract page → privacy-cleared source → cloud annotation candidate → human card attachment → current B02 and exact domain/co-sign approval → confirmed release → draft/export. Revoke each dependency and prove consumption/download fail. Until accepted, source-advertisement/submit tests show the branch disabled. |
| CLI and schema | Snapshot every command/variant in the route table: success/error, zero-cost Result 4.0, duplicate, dry-run, bounded list/cursor/history, download receipt and terminal codes. `bid schema` and HTTP inputs agree; no interactive fallback, secrets/pixels or partial-success archive. |
| Browser | Real `admin`/`bidder` uploads, assigned reviewer sees next action, task contributor selects exact linked revision and opens one page; technical/viewer sees only cleared rendition and denied buttons align with server gates. Test wrong org, lost task membership, rejected revision, needs-redaction blocker, failed upload/retry, library-update notice and disabled B05 entry. |
| Scale/audit/recovery | Seed large archive/review/selection histories; assert bounded query/image reads, stable pagination and batched board updates. Verify encrypted originals/parts/labels and metadata-only audit events. Restore exact retained file and rotate data keys without changing plaintext hashes; current membership still gates access. |

Reuse acceptance patterns from `server/tests/test_certificate_files.py`,
`test_certificate_images.py`, `test_evidence_sources.py`, `test_profiles.py`,
`test_resource_rls.py`, `test_storage.py`, `test_page_previews.py`, `test_pdf_process.py`
and `test_pdf_raster.py`, plus existing team/B05 gate suites when implemented. Tests
must write a sanitized command manifest, fixture hashes, result JSON, downloaded
synthetic PDF/PNG hashes, JUnit and browser trace/screenshots to the acceptance directory.
Each artifact identifies the scenario and expected gate; never include credentials,
signed URLs or real contract data. Stub storage failure tests do not establish live
S3 acceptance; record any required isolated S3 run separately and truthfully.

## Open decisions

All defaults below remain pending owner approval; approval of B05 did not approve
these archive decisions.

| Decision | Recommended default | Reason / enablement consequence |
| --- | --- | --- |
| First file slice | One unchanged unrotated PDF, all listed attachment categories | Completes responsibility/review/task chain before multipart complexity; scans can initially be supplied as PDFs. |
| Archive ownership | Uploader is custodian; explicit live admin/bidder reviewer; reassignment under state version | A rejected or blocked item always has a named next actor. |
| Review separation | Human archive and privacy review; self-review allowed with recorded identity | Small teams can work; it confers no Evidence/co-sign authority. |
| Read privacy boundary | Metadata-only explicit tokens; originals/raw pages human admin/bidder; cleared pages available to authorized human task readers | Prevents contract prices/personal data entering automation through generic image routes. This narrows B05 reads for this source kind. |
| Profile links | Exact revision plus one nonempty declaration field; no automatic copy-forward | Avoids claiming a file supports a changed declaration; multiple explicit links permit legitimate reuse. |
| Task selection | Require an active exact TaskOrgProfile/link/approval; no unattached task-only files initially | Keeps context visible and reuses current profile selection workflow. |
| Multipart and image enablement | Later within the same contract, after subprocess/composition/metadata acceptance | Reuses certificate mechanics without claiming in-process parsing is resource isolated. |
| Privacy workflow | Clear unchanged page first; redacted pages later through existing screenshot privacy chain | Privacy is per exact page/rendition; renderer output must be reviewed before B05. |
| Canonical page identity | Existing 150-dpi RGB `pdf-page-preview-v1`; no renderer downgrade | Makes source binding verifiable; zoom previews remain disposable. |
| Library updates and reviewer departure | No automatic task retarget; historical archive approvals retained unless explicitly revoked | Preserves fixed-task evidence while keeping live access and pending assignment checks. |
| Lifecycle/retention | One-way deactivation, indefinite history, no delete or automatic object expiry | Provides recoverable provenance; any future erasure policy needs its own decision and implementation. |
| Download mechanism | Existing 300-second signed application routes with live authorization | Reuses encryption/read integrity and immediate access revocation; no public object-store links. |
| Storage/DB failure recovery | Retain inaccessible encrypted orphans and reconcile IDs; no garbage collector in this slice | Matches actual Storage capabilities and avoids an implicit deletion policy. |
| B05 release order | Archive slice first; attachment selector enabled only after full B05 dependency/read/export acceptance | The blocked-next-action UI stays truthful while approved certificate B05 can proceed independently. |
| Limits and billing | Existing 40 MiB/200-page/20-part/raster limits; metadata pagination 50/100; deterministic zero cost | Reuses measured bounds and Result 4.0; storage quotas and charged processing require a separate contract. |
