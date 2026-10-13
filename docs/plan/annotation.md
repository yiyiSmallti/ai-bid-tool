---
kind: plan
---

# Cloud evidence annotation jobs

The worker-based annotation direction supersedes the earlier, unimplemented
local-CLI annotation draft; installation scope follows
[the single-user transition](single-user.md).

The [runtime schemas](../../server/app/schemas/annotation_contracts.py) define
payloads; the [mechanism note](../notes/annotation.md) defines rendering and recovery.
Vendor and profile/contract attachment enablement require separate source acceptance.

## Goal and boundary

The human selects an authorized archived
evidence (证据) page for a task (任务), previews a crop and region boxes in the console,
and submits a background job (后台作业). A server worker uses the existing Rust renderer
to save an immutable annotated material. A human attaches the exact result to a
response card (响应卡) and requests human confirmation (人工确认). The dashboard (看板) identifies the blocker and next
action. Only current, confirmed evidence can enter a draft (初稿) or export.

Processing, archival, review state and recovery belong to the cloud service. The CLI
is an authenticated API client; `--input` reads a small JSON plan, not image bytes.
There is no client renderer, local image output path, local toolchain installation or
offline annotation mode. Self-hosted servers retain the same worker, PostgreSQL,
storage and authorization boundaries. Originals and stored renditions never change.

Annotation means cropping, bounded red region boxes and mandatory provenance (溯源).
It cannot create or rewrite certificates (证书), org profile (单位资料) facts, vendor
(厂家) claims, reports, text, signatures or seals. No arbitrary labels, arrows,
highlights, generative fills, OCR, model calls, web capture or user-controlled watermark
text. Redaction (遮挡) and source capture remain upstream workflows; annotation cannot
recover removed pixels. Prototype (原型) images are excluded, preserving their separate
clean-image and human keep/replace policy in [screenshots.md](screenshots.md).

## Code basis and design differences

These integration requirements apply to certificate pages; vendor and
profile/contract attachment source enablement remains deferred. The
[mechanism note](../notes/annotation.md) names the runtime ownership and recovery paths.

| Current basis | Reuse or required difference |
| --- | --- |
| [Design: processing flow](../design.md#processing-flow-and-cli-inventory), [CLI modes](../design.md#cli-design-and-external-agent-integration) | The design names `bid evidence stamp` and server jobs, but also describes local/offline mode. B05 adopts the owner's cloud-only direction and the `evidence stamp` name. |
| `EvidenceSource`, `CertificateFile`, `CertificateFilePart`, `TaskCertificate` in [entities.py](../../server/app/models/entities.py); `require_source`, `source_inputs`, `read_preview` in [evidence_sources.py](../../server/app/services/evidence_sources.py) | Source pins task certificate selection, immutable revision/file, PDF page, 150-dpi RGB PNG, hashes, profile and render time. It is permanently `unconfirmed_source`; B05 never confirms this row. |
| `OrgProfileRevision`, `TaskOrgProfile` in [entities.py](../../server/app/models/entities.py); `MATERIALS` and `resolve_material` in [response_cards.py](../../server/app/services/response_cards.py) | Profile materials are scalar declarations, not attachment pages. Certificates managed on the org-profile console page use the certificate chain. Arbitrary profile/contract attachments require a later archive contract. |
| `ScreenshotAsset`, `ScreenshotRendition`, `ScreenshotPrivacyReview`, `ScreenshotVendorArchive` in [screenshots.py](../../server/app/models/screenshots.py); `resolve`, `record`, `verify` in [vendor_screenshots.py](../../server/app/services/vendor_screenshots.py) | Reuse source selection/revision, immutable image/plan/hash/mapping, privacy review and vendor capture archives; do not introduce a second image store. |
| `RenderRequest`, `render`, `draw_inner_border`, `add_markup_footer` in [stamp/src/main.rs](../../stamp/src/main.rs); `ScreenshotRenderer`, `validate_png`, `_validate_rendering` in [screenshot_renderer.py](../../server/app/providers/screenshot_renderer.py) | `bid-screenshot-renderer` already uses framed in-memory input, a fixed font, redaction/crop/boxes and a source footer. B05 confirmation/provenance profiles are new. Extend the same crate and both validators while keeping old profiles byte-compatible. |
| `render_manifest`, `create_job`, `process_render` in [screenshot_jobs.py](../../server/app/services/screenshot_jobs.py); `JobExecution` in [execution.py](../../server/app/jobs/execution.py) | Reuse input pinning, locking, immutable publication, cleanup, leases and run fencing. Existing screenshot dispatch commits before enqueue; B05 needs transactional enqueue/recovery. Current API cancellation fences publication but does not immediately interrupt Rust. |
| `build_evidence`, `update_card`, `confirmation_inputs`, `card_action` in [response_cards.py](../../server/app/services/response_cards.py) | Image-region Evidence, immutable card revisions, human review and confirmation gates already exist. B05 adds no independent material approval authority. |
| `require_confirmed`, `fields`, `preparation`, `current_preparation` in [requirement_consumption.py](../../server/app/services/requirement_consumption.py); [B02 contract](requirement-confirmation.md) | Requirement confirmation is independent of evidence confirmation. Material preparation can precede it; evidence acceptance, confirmed release and consumption must recheck it. |
| `evidence_dependency`, `assemble` in [drafts.py](../../server/app/services/drafts.py); `build_manifest`, `fresh_manifest`, `release`, `download_gate` in [exports.py](../../server/app/services/exports.py); `export_complete_gate` in [the bid baseline](../../server/migrations/baseline/00_functions_bid.sql) | Consumers bind canonical Evidence rendition/hash. Confirmed release images need an additional export dependency and DB gate; substituting a PNG silently violates current bindings. |
| `CONTRACT_VERSION`, `Result`, `Cost` in [contracts.py](../../server/app/schemas/contracts.py); `attach` in [preflight.py](../../server/app/services/preflight.py) | Reuse the strict seven-key Result 5.0 and six-field resource contract; dry-run uses shared read-only preflight. |

[build_screenshot_renderer.sh](../../scripts/build_screenshot_renderer.sh) and
[CI](../../.github/workflows/check.yml) already build the renderer. The
[crate manifest](../../stamp/Cargo.toml) pins `fontdue`, `image`, `serde`, `serde_json`
and `sha2`; current renderer input also supports JPEG. B05 accepts only verified
archived PNG inputs, without adding a binary, dependency, font download or host setup.

## First vertical slice and source eligibility

The first slice requires one **certificate source page → preview → cloud job →
persisted candidate → human card attachment/review → confirmed release → draft/export**.
Include console/API/CLI, human gates, B02, jobs/events and export binding; an image-only
endpoint is not completion. One request targets one editable card in an explicit
extraction scope. The worker does not rewrite card text or attach evidence for a reviewer.

| Source selector | Server-resolved binding and eligibility |
| --- | --- |
| `certificate_page`: `evidence_source_id` | `EvidenceSourceArchive` supplies task/selection/certificate/revision/file/page, original PDF hash, source PNG, `rendered_at` and `pdf-page-preview-v1`. Require active `TaskCertificate`, same task, valid page and existing certificate-file/source read permissions. Historical sources remain readable but cannot start new current annotations. |
| `vendor_rendition`: `asset_id`, `rendition_id`, `expected_image_sha256` | Resolve with `screenshots.rendition_access`, `selection`, `read_rendition` and `vendor_screenshots.verify`. Require nonwithdrawn vendor asset, active product selection/revision in the same task/extraction, succeeded sandbox capture/archive/page-artifact lineage and valid privacy review. PDF page comes from capture provenance; web uses `page=1, page.kind=web_page`, not a PDF citation. No arbitrary URL or fresh fetch. |
| Org profile attachment | Unsupported until an immutable file/revision archive contract exists. Profile declaration text is not a PNG source; certificates in the org-profile library use `certificate_page`. |

The full source union includes vendor renditions, but the first slice rejects that
branch with `annotation_source_unavailable` and preflight blocker `vendor_source_not_enabled` until its archive/privacy/mapping/hardware
acceptance is complete. Advertise enabled kinds to the editor; never offer a selectable
source that silently falls back to another kind. Vendor enablement is slice two.

Certificate submission records explicit human review of the archived input PNG hash.
A server-owned file is not automatically privacy-reviewed. Extend the existing
`ScreenshotPrivacyReview`/asset-ingest boundary for cloud reuse, retaining the human
actor and exact reviewed hash; no download/re-upload or synthetic upload is needed.
The immutable request records that human attestation before the job. Certificate
asset/privacy-review rows are published together with the material, referencing the
attestation; the worker cannot invent or broaden it. An inherited vendor privacy-review
ID can be pinned at submission. This avoids a request FK to a not-yet-created certificate
asset/review and avoids changing immutable request fields after publication.
Vendor derivation inherits its valid privacy review and removed pixels. Additional
redaction uses the upstream privacy workflow and requires a new annotation preview.
Vendor input uses an existing `screenshot-markup-v1` rendition, not a B05 confirmed
release or prototype. Resolve at most 32 parent links with cycle/missing-parent rejection;
require a complete reversible chain instead of unbounded ancestry reads.

## Preview, material lifecycle and confirmation

1. **Select and preview.** Read-only preflight checks source/card access, exact card
   revision and extraction/requirement binding, hashes, coordinates, renderer identity
   and expanded output limits.
It returns normalized plan/mapping, predicted canvas,
   provenance, input hash, warnings and resource preflight.
The console loads one source
   image and previews crop/boxes/footer layout.
This overlay is a proposal; only the
   later server PNG is a reviewable material.
Input changes invalidate the preview.
2. **Submit the exact preview.** A human sends input hash, request ID and reviewed
   source PNG hash. Revalidate under task/card locks, persist the immutable request and
   enqueue `annotation_render` atomically. Return a job receipt, not a completed image.
   A conflict preserves unsent geometry for explicit re-preview; no automatic rebinding.
3. **Publish an unconfirmed candidate.** Verify source bytes, render, independently
   validate output, then persist a new `ScreenshotRendition` and annotation-material
   binding to the intended task/card. Certificate input may need a new server-bound
   asset; vendor input reuses its asset. Recheck expected card revision at publication;
   never overwrite intervening edits. Candidate view always says `unconfirmed_material`,
   `confirmed_at=null`, `eligible_for_draft_export=false`.
4. **Review actual pixels and attach.** The human opens the persisted candidate and
   explicitly saves the card via `CardUpdate` + `ImageEvidenceInput`, with a bounded
   content-region and `visual_observation`. Use `document_excerpt` for certificates,
   `hardware_documentation` for vendor pages. `build_evidence` creates unconfirmed
   Evidence and revision links, without inheriting old confirmation. Preserve existing
   response (响应) text unless edited by the human. `CardAction(action=submit)` starts
   review; annotation never invents an observation from source text.
5. **Use existing human review.** Review all Evidence IDs, candidate hashes and warnings
   on the exact revision. B02 must be current/confirmed. Human review uses
   `card_action` and the retained human response-review integration. Existing service
   and database gates recheck human authority, warnings and source validity.

6. **Produce a confirmed release.** In the final successful confirmation transaction,
   enqueue `annotation_release` per linked B05 candidate. Recheck the exact approval
   binding and generate a new immutable rendition with a fixed `CONFIRMED` footer.
   Its content pixels, including boxes/redactions, equal the candidate's approved
   content. The worker changes no Evidence binding or human decision.

Confirmation belongs to Evidence in a card approval context, not an asset globally.
`EvidenceSourceArchive` and candidate views remain unconfirmed historical materials;
the UI separately shows the linked card's current review status. Another card using
the candidate requires its own confirmation and release. No `annotation confirm` exists.

Approval binds canonical candidate rendition/image/content hashes, Evidence ID,
confirmed card revision and B02 review revision/hash. The single-user baseline retains exact human approval and source checks.
`review_domain` remains nullable content classification in the baseline approval/hash
binding; it grants no authority and requires no separate reviewer.
Canonical approval hash and renderer identity select a release; never select
“latest confirmation” or invent a signature.

Keep `Evidence.screenshot_rendition_id`, `image_sha256`, region and plan/mapping on
the candidate. `drafts.evidence_dependency` consumes those confirmed canonical inputs.
Extend export manifests and `export_complete_gate` with additional release-rendition/
approval-hash binding and equal-content verification. Both review-copy (审阅件) and
final-section (正式件) exports using B05 material require a current release. Pending or
failed release blocks image export with a recovery action, without undoing the human
decision. Never fall back to an `UNCONFIRMED` PNG. Ordinary export human permission,
confidential-field and prototype gates still apply.

New profiles cannot bypass these rules through existing screenshot routes. Extend
`rendition_access`/preview/content handling so every release-byte read invokes the
same live approval resolver; raw storage keys remain inaccessible. Existing screenshot
annotate/ingest and `resolve_image_material` must reject release renditions as source
material or new Evidence. Legacy annotation must also reject B05 candidate/release
parents, preventing removal of their status footer through an old profile. Expose B05
metadata through the new typed views; do not serialize these profiles as the existing
two-profile `RenditionView`. Include generic screenshot routes in bypass acceptance.

### Invalidation and history

Adding a library revision alone does not change a pinned task. Explicit selection
replacement, withdrawal, different page/file/source hash, invalid privacy lineage or
integrity failure invalidates dependent usability/releases. Keep historical bytes,
plans, human decisions and audits; new material starts a new review.

Card edit/reopen/relink, requirement meaning/citation change or B02 reopen/invalidation,
or invalidated approval inputs invalidate the matching release. A source change
during a job prevents publication. Every signed-link download rechecks live
source and approval validity.

Extend the retained database approval-invalidation trigger and current source/requirement checks with
annotation dependencies. Retain historical decisions; do not clear audit history.
Views expose current/stale validity and fixed reason codes. Acceptance, release download
and export recompute validity; a delayed dashboard event cannot authorize stale material.
The initial target card revision is request provenance: the expected human attachment
creates a new revision and does not itself invalidate the candidate's source pixels.
Review/release validity instead follows the exact revision that actually links Evidence.

## Pixel, provenance and renderer contract

Reuse `PixelRect`, `PNGDescriptor`, `ContentMapping` and `Sha256` from
[screenshot_contracts.py](../../server/app/schemas/screenshot_contracts.py), adding
cross-field checks in the runtime schemas. Request models forbid extra fields.
`AnnotationRenderer.predict` uses the same trusted binary with
`--annotation-describe` to measure the full content/padding/footer canvas without
producing PNG bytes, creating jobs or calling Providers. See the
[renderer ownership](../notes/annotation.md#code).

- Plan: `crop: PixelRect|null`, `boxes: PixelRect[]`, at most 20; explicit defaults
  `null`/`[]` mean watermark-only. Strict integer x/y ≥ 0, width/height ≥ 1, endpoints
  ≤8192 and within actual content. Boxes must be fully inside the crop. Reject rather
  than clip/scale/round. HTTP/CLI JSON ≤128 KiB; normalized renderer metadata ≤64 KiB.
  Booleans/floats, labels, watermark overrides and confirm/export fields are rejected.
- Coordinates address the authorized **source content plane**: rotated 150-dpi PNG for
  certificates, selected vendor rendition content before generated padding/footer.
  Strip only generated margins using verified mapping and compose ancestors to the
  archived page. Cropped/redacted pixels stay unavailable. Output content `(u,v)` maps
  to `(u-offset_x+crop.x, v-offset_y+crop.y)` in the immediate content plane; repeat
  through ancestry. Padding/footer have no source coordinate or Evidence-region use.
  Browser zoom converts pointer positions back to integer source pixels before preview.
- Preserve pixels except crop and fixed 2-pixel red inner borders `[255,0,0]`; very small
  boxes can be entirely border. No fill, text redraw, resampling or image completion.
- New profiles `annotation-candidate-v1` and `annotation-release-v1` reuse the bundled
  font/rasterizer from `add_markup_footer`: minimum width 1024, centered content at y=0,
  white padding/footer, 16px margins, 16px font, 24px line height and fixed measured-width
  wrapping. Pin font hash, encoder settings and renderer build/protocol identity; return
  exact `ContentMapping`. This replaces the old unused ASCII-cell proposal, not old
  screenshot profiles. No downloaded font or OS fallback.
- Fixed footer order: `STATUS`, `SOURCE_KIND`, `SOURCE_ID`, `TASK_ID`, `SELECTION_ID`,
  `SOURCE_REVISION_ID`, `SOURCE_FILE_ID` or `VENDOR_ARCHIVE_ID`, `PAGE_KIND`, `PAGE`,
  `DPI`, `SOURCE_PROFILE`, `SOURCE_TIME_KIND`, `SOURCE_TIME`, `ORIGINAL_SHA256`,
  `SOURCE_PNG_SHA256`, `PLAN_SHA256`, `RENDERER_PROFILE`, `RENDERER_VERSION`; release
  adds `APPROVAL_SHA256`. Nonapplicable fields use fixed `NOT_APPLICABLE`. Status is
  `UNCONFIRMED USER-SUPPLIED CERTIFICATE PAGE` or `UNCONFIRMED ARCHIVED VENDOR PAGE`;
  release changes the first word to `CONFIRMED`. Human review is not source authenticity.
- Source time is archived certificate `rendered_at` or vendor capture receipt time,
  normalized to UTC with explicit meaning, never certificate issue time. No arbitrary
  title/URL, price, source text, token, signed link, attempt ID or current render clock
  enters the footer. Publication time is metadata. Output PNG hash stays in its receipt,
  avoiding a recursive self-hash in the image.
- `plan_sha256`: SHA-256 of validated `model_dump(mode="json")`, UTF-8 canonical JSON,
  explicit defaults, sorted keys, separators `(',', ':')`, `ensure_ascii=True`, no final
  newline, box order preserved. Rust uses that exact plan shape; old `ImagePlan` with
  `redact=[]` has a different profile/hash. Input hash also pins source/privacy/mapping,
  card revision, requirement preparation hash, renderer build/profile/font/protocol.
- Input/output sides ≤8192px, total ≤20,000,000 pixels, PNG ≤40 MiB and any lower
  configured limit. Validate expanded footer before allocation, reject overflow without
  downsampling. Retain 160,000,000-byte decoder allocation bound and 64 KiB metadata/
  response/diagnostic stream caps. B05 input is PNG only; output RGB8 PNG without alpha,
  animation, EXIF/text or other ancillary chunks. Identical source bytes, plan,
  provenance and pinned renderer identity yield identical bytes across retries.

`AnnotationRenderer` extends `ScreenshotRenderer` through the same binary: 8-byte
big-endian JSON length, bounded JSON, image bytes; stdout is a bounded receipt frame
then PNG. Extend Rust and Python profile/provenance/receipt validation together, reject
unknown fields. No paths, shell, network or credentials in the payload. `AnnotationService`
describes authorization/preflight/job/read interfaces, not a second renderer.

Python checks input length/hash/PNG/dimensions against authorized storage and output
descriptor/profile/plan/provenance/mapping against actual bytes. A release strips only
the candidate's generated footer/padding and does not reapply crop/boxes. It verifies
equal content dimensions and a content digest: SHA-256 of ASCII
`annotation-rgb-v1\n`, unsigned 32-bit big-endian width and height, then row-major RGB8
bytes. The new footer may differ; approved content and source mapping may not.
`input_content_sha256` hashes the actual framed PNG bytes, distinct from this decoded
content digest. `approval_binding_sha256` hashes the normalized approval JSON excluding
that hash field itself, using the plan's canonical JSON rules. `provenance_sha256`
hashes the fixed ordered footer key/value pairs and renderer identity, never a new
operation timestamp or arbitrary serialized request. Verify these hashes in the service and both protocol
boundaries; Pydantic shape validation alone does not establish storage or review trust.

## Jobs, usage and publication

Add `annotation_render` and `annotation_release` Job kinds to the existing `Processor`,
Procrastinate `bid.process` and `JobExecution` patterns. Register kinds in DB constraints,
dispatch, `JOB_SCOPES`, status/cancel access, task-event visibility and resource usage command
mapping. Use the real extraction job's `document_id`, including a valid B02 manual scope:
this is the tender document (招标文件) behind the card, not its certificate. Never invent
a Document, use `provider_test`, or loosen `Job.job_document_binding` for annotation.

Candidate jobs pin human actor kind and task/card/requirement/
extraction, source/selection/revision, privacy input, plan/preview hash and renderer.
Release jobs pin the exact approval and candidate content. Worker access retains the
initiator and checks live grants; a restricted execution scope covers only that fixed
job. Do not grant human-only scopes to workers or forge a session for human card APIs.
Completed human review authorizes automatic release rendering; retry is a human action.
Automatic release admission validates the complete human approval, not the candidate
human-only create gate. Register `annotation_release` with `card:read` plus
kind-specific source-read and approval checks; that read scope alone cannot authorize
publication. Preserve the initiating actor kind and verify the exact human approval without giving the worker `evidence:confirm`. Candidate admission
uses `evidence:annotate`; its worker rechecks the human initiator's live create ceiling
through a fixed-job gate, not `Identity.require` on a human-only scope stripped from
workers. Explicit release retry still needs a human-only creation grant.

Use `Queue.enqueue_in_transaction`/`ensure_process_delivery` in
[queue.py](../../server/app/jobs/queue.py), rather than the screenshot route's delivery
gap. Persist request ID/payload hash; identical replay returns the same job/material,
different payload with the same ID returns 409. Cache binds task/source/card/input/
renderer/submitter, not another actor's response or stale approval. Explicit terminal
retry retains the original manifest. Input change needs a new preview/request.

Heartbeat and `owned_job` fence publication. Acquire task/card locks in established
order; recheck current actor scopes, source/bytes, requirement binding, archive state,
run ID and lease. Write a new encrypted object at
`screenshots/{asset_id}/{rendition_id}/{sha256}.png`, then commit material,
audit, task event and job success together. On failure/duplicate/cancel, remove only
attempt-owned objects without committed references. Crash reconciliation applies a
bounded grace period to unreferenced keys; losing a lease cannot delete retained bytes.
Storage keys are internal; public contracts expose IDs/descriptors/authorized links.

### Cost and execution limits

Estimate basis is `zero` or a valid `cache_hit`, `next_call=null`.
CPU/memory/storage limits remain operational
constraints.

Reuse absolute trusted binary validation, minimal environment, no shell, bounded pipes,
20-second per-process timeout and terminate → one-second grace → kill/reap from
`ScreenshotRenderer.render`. Add a 60-second entire-attempt deadline including storage
and content extraction, distinct from the lease. During execution check persisted
cancel/lease/access state at most every second, cancel the renderer coroutine and reap.
Publication fencing remains mandatory for late cancellation. CLI wait timeout stops
waiting, not the server job; explicit `job cancel` cancels it.

Reuse the processor's three-attempt maximum and queue backoff; no adapter retry loop.
Retry transient queue/storage/worker failures only. Timeouts remain bounded; repeatable
resource/size failures, corrupted input/output, stale binding, unsupported profile or
revoked access fail without automatic retry. Cancelled work does not auto-requeue.

Renderer performs no URL fetch or child execution. Retain decoder allocation caps and
minimal environment; require nonroot worker execution, no renderer network capability,
read-only binary/font installation and process/CPU/RSS limits. Recommended initial
envelope: 1 CPU, 512 MiB RSS, one live renderer per job, tested with maximum accepted
images. These OS/container restrictions and cancellation polling are B05 additions,
not an assertion that the existing adapter provides an OS sandbox. The server reads
installation storage; the renderer receives only selected verified bytes and public provenance.

## Data model and migration outline

Reuse screenshot assets/renditions/privacy, `Evidence`, `ResponseCard`,
`ResponseCardRevision`, `CardEvidenceLink`, Job, AuditLog and task events. Add only
annotation-specific immutable bindings, not a parallel approval machine or PDF store.

The [single-user baseline](../../server/migrations/baseline/10_tables.sql) fixes the
annotation request/material/release tables. Requests bind exact payload, task,
card/revision, source, plan, renderer and job. Materials bind the successful run and
candidate pixels. Releases bind the exact Evidence, card revision, human approval
hash, renderer and output mapping. Typed source branches and composite parents
prevent unrelated task, card, source or job references.

Keep the baseline unchanged. Preserve immutable rows, human request/privacy gates,
worker lease/run fences, equal-content release checks and complete source bindings.
Human decisions use timestamps; worker publication cannot create them. Both export
variants require current annotation releases without weakening non-annotation gates.
Rollback disables writes/consumption while retaining history and encrypted originals.

## HTTP, CLI and JSON contract

Use the existing authenticated transaction dependency and the Result 5.0 unversioned API
HTTP prefix for metadata requests. The paths below are relative to that prefix;
signed binary handlers also retain their authenticated unversioned links. All path/body IDs must
resolve to one authorized task. No caller-provided actor, confirmer, storage key,
arbitrary provenance, job kind or release status. Models reuse runtime schema types.

| HTTP | CLI (all support `--json`) | Request → `data` / `items` |
| --- | --- | --- |
| `POST /tasks/{task_id}/annotations` (`dry_run=true`) | `bid evidence stamp --task UUID --input PLAN.json --dry-run --json` | `AnnotationPreflightRequest` → `AnnotationPreflight` / [] |
| Same route, submission | `bid evidence stamp --task UUID --input PLAN.json --expected-input-hash HASH --reviewed-source-png-sha256 HASH --request-id UUID [--retry] [--wait] --json` | `AnnotationSubmit` → `AnnotationJobAccepted`; successful wait → candidate view |
| `GET /tasks/{task_id}/annotations?card_id=…&cursor=…&limit=…` | `bid evidence annotation list --task UUID [--card UUID] [--cursor CURSOR] [--limit N] --json` | Page data / `AnnotationCandidateView[]` |
| `GET /annotations/{id}` | `bid evidence annotation show --id UUID --json` | `AnnotationShowData` / [] |
| `GET /annotations/{id}/preview` | `bid evidence annotation preview --id UUID --json` | `AnnotationPreviewLink` / [] |
| `POST /annotations/{id}/releases` | `bid evidence annotation release retry --id UUID --input REVIEW.json --json` | `AnnotationReleaseRetry` names exact Evidence/card revision/approval hash and failed job → `AnnotationJobAccepted`. Normally enqueued automatically at complete confirmation; retry is not a new approval |
| `GET /annotations/{id}/releases?cursor=…&limit=…` | `bid evidence annotation releases --id UUID [--cursor CURSOR] [--limit N] --json` | Page data / `AnnotationReleaseView[]` |
| `GET /annotation-releases/{id}/preview` | `bid evidence annotation release preview --id UUID --json` | Signed release preview after current approval/source checks; stale release conflicts |
| Existing `PUT /cards/{id}`, `POST /cards/{id}/actions` | Existing card commands | `CardUpdate`, `CardAction`; no B05 confirmation shortcut |
| Existing `GET /jobs/{id}`, `POST /jobs/{id}/cancel` | `bid job status/wait/cancel --id UUID --json` | Existing safe job envelope, extended annotation kinds/results |

`AnnotationInput` is the plan file: extraction/card/expected card revision, source
selector and plan. CLI wraps it for preflight/submit. Required arguments fail immediately,
without questions; `--dry-run` rejects retry/submit-only fields. `--wait` polls safe job
status and maps terminal errors. No image crosses the CLI for computation. Register
the implemented commands and enabled source kinds in `bid schema`; the documentation
JSON inventory retains the approved contract baseline.

Preflight is metadata/overlay preview. Persisted PNG links use application download
handlers requiring current identity and underlying task/source access, not public
bearer URLs. Expiry is 300 seconds, bound to object/hash/variant; never persist
links in events, audits or browser storage. Lists default 50, cap 100, stable
`(created_at,id)` keyset order; cursors bind actor/task/card/filter/access epoch.
Release lists are separate; show never embeds unbounded review history. All object
paths return identical 404 for missing/inaccessible IDs, including inaccessible
tasks; denied actions on visible objects use 403, absent authentication 401.

Result 5.0 has exactly `ok`, `command`, `data`, `items`, `warnings`, `cost`, `duration_ms`.
Illustrative successful admission (no PNG claimed yet):

```json
{
  "ok": true,
  "command": "evidence stamp",
  "data": {
    "job_id": "00000000-0000-4000-8000-000000000101",
    "task_id": "00000000-0000-4000-8000-000000000102",
    "card_id": "00000000-0000-4000-8000-000000000103",
    "kind": "annotation_render",
    "status": "queued",
    "duplicate": false
  },
  "items": [],
  "warnings": [],
  "cost": {"calls": 0, "llm_tokens": 0, "ocr_pages": 0, "search_requests": 0, "browser_units": 0, "unresolved_calls": 0},
  "duration_ms": 18
}
```

Dry-run includes existing
`PreflightData`; completed job usage comes from `job_cost(...)` with the six
resource counts in the Result 5.0 contract.
Reads/admission make zero calls.
Duration is measured.
No image/base64, storage key, local path, secret or source-text dump in JSON.
Only a
succeeded job with a verified material proves publication, not `ok=true` on admission.

| Exit | HTTP/error examples and meaning |
| --- | --- |
| 0 | Preflight complete, job accepted, read successful, or candidate/release fully published; preflight blockers still prevent submission |
| 2 | 400/413/422 invalid plan/limit/source; 409 preview/source/card/approval changed, B02 not confirmed, or request-ID payload conflict; correct input/review before retry |
| 3 | 503 transient queue/storage/worker failure or retryable timeout; wait timeout retains job ID without cancelling or claiming publication |
| 4 | 401/403/404 identity/resource denial; integrity failure; missing/untrusted renderer or unsupported deployed protocol; nonretryable resource/process error; terminal cancellation |
| 5 | Reserved by Result, unused for one atomic image; no partial PNG/material success |

Errors retain `data.code`/`data.message` and safe job ID when available. Failed preview
or submit returns no success material; failed release creates no new human decision.

## Permissions, audit and task events

`evidence:annotate` is human-only for preflight, candidate admission and release
retry. The human can create, attach and review material across all professional
domains. Tokens and agents cannot annotate, confirm or export. Workers publish
only the fixed authorized job input and cannot edit human card decisions.

Use `task_access.access` and `live_actor` before source reads/writes. Retain source,
certificate, screenshot and card scopes and exact task/source bindings. Cancellation
and retry require human write authority. Confirmation requires current requirement,
source, hash and warning acknowledgements; hidden buttons are not authorization.

Audit successful writes atomically with `versioned.audit`: `annotation.submit`,
`annotation.publish`, `annotation.release.enqueue`, `annotation.release.publish`,
`annotation.retry`, `annotation.invalidate`, plus existing job/card/privacy/review
events. Keep actor kind/token/agent session, job/run, task/card/source IDs, plan/input/image/approval
hashes, profile, reason code and time. Exclude pixels, observation text, arbitrary
labels, credentials, signed URLs and material text. Rolled-back writes have no success audit.

Use `task_events.append` and the `produce_task_events` statement producer in
[the core baseline functions](../../server/migrations/baseline/00_functions_core.sql) for atomic `board_changed`,
`job_progress` and existing `access_changed`; reuse SSE/replay/reset, not a second
stream. Frames contain IDs/states only, ≤100 targets or `invalidate_all`, ≤4096 bytes.
Extend hidden-kind/`visible_job` handling for both annotation kinds.

Extend `task_board_projection.load` using batched metadata. New next actions:
`inspect_annotated_material` (“核对标注图”), `confirm_annotated_material`
(“确认标注材料”), `retry_annotation_release` (“重试确认图生成”),
`replace_annotation_source` (“重新选择来源”). Existing B02 confirmation stays the
first blocker when needed. The human performs the indicated review action. Render success never marks a requirement
complete. `BoardNextAction`/target/schema updates are implementation dependencies.

## Console outline and bounded reads

Console route: `/app/tasks/{task_id}/cards/{card_id}/annotation?job={extraction_job_id}`.
Enter from a card material panel/dashboard action with explicit extraction scope.
Show required review, blocker and one primary next action. Keep unsent
geometry only in current-tab memory; clear material/preview data on task switch.

Reuse [DocumentPreview.vue](../../web/src/components/DocumentPreview.vue) for tender
citations beside the card and extend its source adapter/dialog with
[PageViewer.vue](../../web/src/components/PageViewer.vue) for authorized source images.
`DocumentPreview` currently takes a tender `documentId`; do not pass an EvidenceSource
UUID as that ID. Add a typed adapter, retaining existing document routes and zoom/page/
keyboard/error behavior. An overlay/numeric controls edit bounds; final pixels always
come from the worker, never browser canvas export.

Page sections: paged source picker; page/hash/provenance/warnings; original and proposed/
actual image; crop/boxes/reset; “预览标注” then “生成标注材料”; job status/cancel.
After success, show actual candidate before “用于此响应卡”. Existing CardEditor owns
observation, response, submit and review. Show B02, material confirmation and release
generation separately. Conflicts preserve unsent geometry but require new preview.

Read one source page/candidate at a time, maximum two in-flight image requests, cancel
stale requests and revoke object URLs on close. Lists use 50/100 pagination; no task-wide
image downloads, complete histories or signed links on entry. Reuse bounded board
snapshot, ≤20 jobs and event cursor; refresh affected resources, without per-row image
fetch or unbounded polling. SSE reset fetches a new bounded snapshot. Delayed responses
from another context never repaint the editor. Numeric bounds, keyboard controls,
visible focus and text errors make state understandable without color alone.

## Failure modes and test plan

| Failure | Required behavior |
| --- | --- |
| Selection/source/hash/card changed before submit or publish | Conflict, no material/card mutation; preserve geometry for explicit re-preview, no automatic latest-source binding |
| Corrupt/oversized/animated PNG, decompression bomb, forged mapping/receipt | Fail bounded validation, no referenced output or integrity-error retry loop |
| Crop/box/footer expansion limit | Input error; no scaling, clipping, omitted provenance or watermark removal |
| Queue interruption, lease takeover, duplicate delivery | Transactional recovery/dedup; one immutable result, stale attempt cannot publish; clean only its unreferenced objects |
| Cancel/deadline/pipe overflow | Terminate/kill/reap, bounded terminal result, no partial image or automatic cancelled-job restart |
| Source or approval invalidation, withdrawal | Deny writes/consumption; retain authorized history; fence late publication even after object upload |
| B02 incomplete/invalidated, stale human approval, wrong evidence/warnings | Candidate preparation may remain readable; acceptance/release/draft/export blocked by the corresponding gate |
| Release missing/corrupt/stale/content mismatch | Block export with exact reason/next action; no candidate fallback or worker confirmation |
| Unsupported/untrusted renderer | Explicit configuration failure; no Python or old-profile fallback |
| UI read failure/task switch | Cancel old reads, remove stale images, require authorized refresh, retain unsent input only in the active context |

Implementation acceptance must prove the full flow. PostgreSQL/API and mocked-browser
regression cases require rerun after the approval-binding and fixture corrections.
DB-free transport, collection and renderer-only checks do not establish the complete
isolation, publication and browser path.

| Area | Required acceptance and repeatable artifact |
| --- | --- |
| Each table | Human-versus-token/agent gates, immutability, exact source/task/card/Evidence/review/job composite parents, mixed-parent rejection and deferred completeness. |
| Each route/download/job variant | Uniform missing/unauthorized 404; task/source binding, object/hash/expiry/tampering, historical source, cursor/filter replay and candidate/release download gates. |
| Human gates | API and database reject token/agent/worker human-only actions; stale human approval, wrong Evidence/hash/warnings and reopened requirements block consumption. Workers cannot confirm. |
| Real renderer flow | Synthetic pages/archive → API/CLI → real Rust → storage/job → card review → release → draft/export. Artifact includes source/plan/profile hashes, candidate/release PNGs, unchanged original, expected pixels and reversible mapping. Repeat fixed inputs for deterministic bytes and profile changes for cache separation. |
| Pixel/process limits | No crop/crop/20 boxes, overlap/edge/small boxes, rotation, Chinese source pixels, cropped/redacted parents, bounds/21 boxes/bools/floats/labels, min width/footer overflow, 8192/20M/40MiB/decoded/metadata limits, malformed PNG/receipts. Timeout/cancel/pipe bounds/no-network/CPU-RSS and reap assertions. |
| Jobs/usage/publication | Transactional enqueue/crash recovery, request replay/conflict, changing input/withdrawal/authority during render, lease takeover, retries/cancel/orphans.
One verified binding per published object. |
| Draft/export | Unconfirmed material never reaches rows/files. Current B02 and human/material decisions admit canonical Evidence through the baseline human confirmation gates; B05 image export additionally requires release. Evidence hash unchanged. Pin replacement/B02 reopen/card relink/approval invalidation/tampering block consumption; non-B05/prototype paths unchanged. |
| CLI/schema | Snapshot every new command and dry-run/queued/succeeded/duplicate/retry/cancel/error/empty/paged result; seven keys, resource counts and exit 0/2/3/4. No false partial image. Client never runs Rust or downloads image for processing. |
| Browser end to end | Follow [requirement-confirmation.spec.js](../../web/e2e/requirement-confirmation.spec.js) Playwright mocked-API pattern: exact routes/bodies, bounded reads, preview/explicit use/review, human/B02/material blockers, cancellation/failure/retry, conflicts, release stale/wait, task-switch races and keyboard use.
Retain screenshots, trace and JSON with seed, request counts and replay command. |
| Events/board | Correct blocker/next action on publish/review/release/invalidate; no success event after rollback; replay/reset/revocation observes task/source boundaries and frame caps. Board state never authorizes consumption. |

Retain artifacts under ignored `data/work/annotation-acceptance/`: PNGs, hash manifest,
CLI snapshots, Playwright screenshots/trace/result JSON and DB report, never under
`docs/`. Browser mocks prove UI, not database isolation or Rust execution; keep separate
real-service/renderer acceptance. Synthetic data/Providers only, no external services.
Define failure scenarios and end-to-end cases first; do not add after-the-fact unit
tests that merely repeat implementation.

## Decisions

These decisions define the annotation boundary.

| Decision | Approved default | Reason |
| --- | --- | --- |
| First source | Certificate EvidenceSource, one card/page; vendor slice follows | Complete common workflow with existing archive, without inventing profile files. |
| Profile/contract attachments | Separate immutable archive contract before enablement | Current declarations provide no attachment storage/authorization chain. |
| Command/renderer | Cloud `bid evidence stamp`, annotation read/recovery commands, extend `stamp/` | Matches product direction and existing CI/service ownership. |
| Preview | Read-only plan/overlay before job; actual candidate before attach/confirm | Prevents accidental submission while reviews bind real pixels. |
| Attachment | Explicit human CardUpdate after render | Preserves concurrent edits and the humanhip of observation/response. |
| Confirmed watermark | Immutable UNCONFIRMED candidate plus exact-approval CONFIRMED release | Preserves image/hash/signature history without overwriting files. |
| Consumption | Confirmed canonical Evidence for draft, current release for all B05 image exports | Retains human gates and prevents stale/unconfirmed labeled attachments. |
| Reviewer policy | Existing human material review plus B02 acceptance; exact baseline approval checks | No competing or weaker approval path. |
| Creation authority | Human-only evidence:annotate, human confirmation | Source/privacy/material choices remain explicit human actions. |
| Pixel semantics | Existing fixed font, 2px boxes, no labels/rescale, versioned profiles | Reuses bounded renderer while keeping deterministic provenance. |
| Source change | Invalidate on task pin/source/review change, not library edit alone | Preserves task snapshots and requires explicit reconfirmation when inputs change. |
| Cost | Local rendering makes zero provider calls. Retain resource preflight and measured duration. | Server compute does not need a fictitious model allowance. |
| Runtime | 20s process/60s attempt, cancel poll ≤1s, ≤3 attempts, initial 1 CPU/512MiB | Bounded recovery consistent with screenshot patterns; test the envelope at maximum input. |
| Batching/history | Single page/card, ≤20 boxes; new plan creates new material | Keeps conflicts and next review action clear before adding batch complexity. |
| Stale release access | Metadata history readable under live ACL; normal CONFIRMED PNG preview denied | Retains audit history without silently reusing obsolete approvals. |
