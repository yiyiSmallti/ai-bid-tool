---
kind: plan
---

# Cloud evidence annotation jobs

Status: **Approved with all recommended defaults, not implemented.** Corresponds to [roadmap](roadmap.md)
B05 and its B07/F03 dependencies.

The owner's cloud-hosted team workflow direction supersedes the earlier, unimplemented
local-CLI annotation draft.

The [Pydantic and interface draft](annotation/annotation_contracts.py) and
[JSON schema inventory](annotation/annotation-schemas.json) are review-only contracts,
not registered routes, commands, database models or renderer features. Approval under
[agent.md](../../agent.md#workflow) is required before implementation.

## Goal and boundary

A bid specialist (投标专员) or technical contributor selects an authorized archived
evidence (证据) page for a task (任务), previews a crop and region boxes in the console,
and submits a background job (后台作业). A server worker uses the existing Rust renderer
to save an immutable annotated material. A human attaches the exact result to a
response card (响应卡) and requests human confirmation (人工确认) in the relevant review
domain (职责). The dashboard (看板) identifies the responsible person, blocker and next
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

These are integration points, not claims that the proposed feature already runs.

| Current basis | Reuse or required difference |
| --- | --- |
| [Design: processing flow](../design.md#processing-flow-and-cli-inventory), [CLI modes](../design.md#cli-design-and-external-agent-integration) | The design names `bid evidence stamp` and server jobs, but also describes local/offline mode. B05 adopts the owner's cloud-only direction and the `evidence stamp` name. |
| `EvidenceSource`, `CertificateFile`, `CertificateFilePart`, `TaskCertificate` in [entities.py](../../server/app/models/entities.py); `require_source`, `source_inputs`, `read_preview` in [evidence_sources.py](../../server/app/services/evidence_sources.py) | Source pins task certificate selection, immutable revision/file, PDF page, 150-dpi RGB PNG, hashes, profile and render time. It is permanently `unconfirmed_source`; B05 never confirms this row. |
| `OrgProfileRevision`, `TaskOrgProfile` in [entities.py](../../server/app/models/entities.py); `MATERIALS` and `resolve_material` in [response_cards.py](../../server/app/services/response_cards.py) | Profile materials are scalar declarations, not attachment pages. Certificates managed on the org-profile console page use the certificate chain. Arbitrary profile/contract attachments require a later archive contract. |
| `ScreenshotAsset`, `ScreenshotRendition`, `ScreenshotPrivacyReview`, `ScreenshotVendorArchive` in [screenshots.py](../../server/app/models/screenshots.py); `resolve`, `record`, `verify` in [vendor_screenshots.py](../../server/app/services/vendor_screenshots.py) | Reuse source selection/revision, immutable image/plan/hash/mapping, privacy review and vendor capture archives; do not introduce a second image store. |
| `RenderRequest`, `render`, `draw_inner_border`, `add_markup_footer` in [stamp/src/main.rs](../../stamp/src/main.rs); `ScreenshotRenderer`, `validate_png`, `_validate_rendering` in [screenshot_renderer.py](../../server/app/providers/screenshot_renderer.py) | `bid-screenshot-renderer` already uses framed in-memory input, a fixed font, redaction/crop/boxes and a source footer. B05 confirmation/provenance profiles are new. Extend the same crate and both validators while keeping old profiles byte-compatible. |
| `render_manifest`, `create_job`, `process_render` in [screenshot_jobs.py](../../server/app/services/screenshot_jobs.py); `JobExecution` in [execution.py](../../server/app/jobs/execution.py) | Reuse input pinning, locking, immutable publication, cleanup, leases and run fencing. Existing screenshot dispatch commits before enqueue; B05 needs transactional enqueue/recovery. Current API cancellation fences publication but does not immediately interrupt Rust. |
| `build_evidence`, `update_card`, `confirmation_inputs`, `card_action` in [response_cards.py](../../server/app/services/response_cards.py); `sign`, `projections`, `manifest_fields` in [task_cosign.py](../../server/app/services/task_cosign.py) | Image-region Evidence, immutable card revisions, domain review and team slice 3 complete co-sign rounds already exist. B05 adds no independent material approval authority. |
| `require_confirmed`, `fields`, `preparation`, `current_preparation` in [requirement_consumption.py](../../server/app/services/requirement_consumption.py); [B02 contract](requirement-confirmation.md) | Requirement confirmation is independent of evidence confirmation. Material preparation can precede it; evidence acceptance, confirmed release and consumption must recheck it. |
| `evidence_dependency`, `assemble` in [drafts.py](../../server/app/services/drafts.py); `build_manifest`, `fresh_manifest`, `release`, `download_gate` in [exports.py](../../server/app/services/exports.py); `export_complete_gate` in [0024_export_images.py](../../server/migrations/versions/0024_export_images.py) | Consumers bind canonical Evidence rendition/hash. Confirmed release images need an additional export dependency and DB gate; substituting a PNG silently violates current bindings. |
| `CONTRACT_VERSION`, `Result`, `Cost` in [contracts.py](../../server/app/schemas/contracts.py); `attach` in [budget_preflight.py](../../server/app/services/budget_preflight.py) | Task-budget work is present: use Result 4.0 and its full cost object, not older plan examples. |

[build_screenshot_renderer.sh](../../scripts/build_screenshot_renderer.sh) and
[CI](../../.github/workflows/check.yml) already build the renderer. The
[crate manifest](../../stamp/Cargo.toml) pins `fontdue`, `image`, `serde`, `serde_json`
and `sha2`; current renderer input also supports JPEG. B05 accepts only verified
archived PNG inputs, without adding a binary, dependency, font download or host setup.

## First vertical slice and source eligibility

The recommended first slice is one **certificate source page → preview → cloud job →
persisted candidate → human card attachment/review → confirmed release → draft/export**.
Include console/API/CLI, roles, B02/co-sign, jobs/events and export binding; an image-only
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
   and expanded output limits. It returns normalized plan/mapping, predicted canvas,
   provenance, input hash, warnings and budget preflight. The console loads one source
   image and previews crop/boxes/footer layout. This overlay is a proposal; only the
   later server PNG is a reviewable material. Input changes invalidate the preview.
   Preflight creates no job, object, material, review, reservation or audit row.
2. **Submit the exact preview.** A human sends input hash, request ID and reviewed
   source PNG hash. Revalidate under task/card locks, persist the immutable request and
   enqueue `annotation_render` atomically. Return a job receipt, not a completed image.
   A conflict preserves unsent geometry for explicit re-preview; no automatic rebinding.
3. **Publish an unconfirmed candidate.** Verify source bytes, render, independently
   validate output, then persist a new `ScreenshotRendition` and annotation-material
   binding to the intended task/card. Certificate input may need a new server-bound
   asset; vendor input reuses its asset. Recheck expected card revision at publication;
   never overwrite intervening edits. Candidate view always says `unconfirmed_material`,
   `confirmed_by=null`, `eligible_for_draft_export=false`.
4. **Review actual pixels and attach.** The human opens the persisted candidate and
   explicitly saves the card via `CardUpdate` + `ImageEvidenceInput`, with a bounded
   content-region and `visual_observation`. Use `document_excerpt` for certificates,
   `hardware_documentation` for vendor pages. `build_evidence` creates unconfirmed
   Evidence and revision links, without inheriting old confirmation. Preserve existing
   response (响应) text unless edited by the human. `CardAction(action=submit)` starts
   review; annotation never invents an observation from source text.
5. **Use existing human review.** Review all Evidence IDs, candidate hashes and warnings
   on the exact revision. B02 must be current/confirmed. Single-domain review uses
   `card_action`/existing legacy-confirm integration; multi-domain policy uses
   `CoSignConfirm` and a complete input-bound round. Partial signatures do not admit
   material. Existing service/DB gates recheck domain, policy, signer authority, warnings
   and source validity. Disposition-only rounds never confirm Evidence.
6. **Produce a confirmed release.** In the final successful confirmation transaction,
   enqueue `annotation_release` per linked B05 candidate. Recheck the exact approval
   binding and generate a new immutable rendition with a fixed `CONFIRMED` footer.
   Its content pixels, including boxes/redactions, equal the candidate's approved
   content. The worker changes no confirmer, Evidence binding or human decision.

Confirmation belongs to Evidence in a card/review context, not an asset globally.
`EvidenceSourceArchive` and candidate views remain unconfirmed historical materials;
the UI separately shows the linked card's current review status. Another card using
the candidate requires its own confirmation and release. No `annotation confirm` exists.

Approval binds canonical candidate rendition/image/content hashes, Evidence ID,
confirmed card revision, B02 review revision/hash, and the actual review manifest from
`task_cosign.manifest_fields`. Co-sign includes round ID/revision, snapshot hashes,
policy/star-rule revisions and signatures. Single-domain approval retains the actual
one-domain response round: `legacy_confirm` also creates/signs that round. Only genuine
older decisions without a round use the historical card/Evidence confirmation binding;
never invent a round or discard a real one. Canonical approval hash
and renderer identity select a release; never select “latest confirmation.”

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
plans, signatures and audits; new material starts a new review.

Card edit/reopen/relink, requirement meaning/citation change or B02 reopen/invalidation,
policy/star-rule change, signer authority loss or invalidated co-sign round invalidates
the matching release. A source change during a job prevents publication. Task archival
stops writes/jobs; history remains under live read grants and existing export archive
policy. Membership removal denies access immediately, including at signed-link download.

Extend `team_cosign_invalidate_dependencies` and current source/requirement checks with
annotation dependencies. Retain historical decisions; do not clear audit history.
Views expose current/stale validity and fixed reason codes. Acceptance, release download
and export recompute validity; a delayed dashboard event cannot authorize stale material.
The initial target card revision is request provenance: the expected human attachment
creates a new revision and does not itself invalidate the candidate's source pixels.
Review/release validity instead follows the exact revision that actually links Evidence.

## Pixel, provenance and renderer contract

Reuse `PixelRect`, `PNGDescriptor`, `ContentMapping` and `Sha256` from
[screenshot_contracts.py](../../server/app/schemas/screenshot_contracts.py), adding
cross-field checks in the draft. Request models forbid extra fields.

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

## Jobs, budget and publication

Add `annotation_render` and `annotation_release` Job kinds to the existing `Processor`,
Procrastinate `bid.process` and `JobExecution` patterns. Register kinds in DB constraints,
dispatch, `JOB_SCOPES`, status/cancel access, task-event visibility and budget command
mapping. Use the real extraction job's `document_id`, including a valid B02 manual scope:
this is the tender document (招标文件) behind the card, not its certificate. Never invent
a Document, use `provider_test`, or loosen `Job.job_document_binding` for annotation.

Candidate jobs pin submitting human, org (organization/tenant; 单位), task/card/requirement/
extraction, source/selection/revision, privacy input, plan/preview hash and renderer.
Release jobs pin the exact approval and candidate content. Worker access retains the
initiator and checks live grants; a restricted execution scope covers only that fixed
job. Do not grant human-only scopes to workers or forge a session for human card APIs.
Completed human review authorizes automatic release rendering; retry is a human action.
The final signer can be a task reviewer without annotation write authority. Automatic
release admission therefore validates the complete human approval, not the candidate
owner/contributor create gate. Register `annotation_release` with `card:read` plus
kind-specific source-read and approval checks; that read scope alone cannot authorize
publication. Preserve the actual final signer's identity and verify current round/
signer authority without giving the worker `evidence:confirm`. Candidate admission
uses `evidence:annotate`; its worker rechecks the human initiator's live create ceiling
through a fixed-job gate, not `Identity.require` on a human-only scope stripped from
workers. Explicit release retry still needs a human owner/contributor creation grant.

Use `Queue.enqueue_in_transaction`/`ensure_process_delivery` in
[queue.py](../../server/app/jobs/queue.py), rather than the screenshot route's delivery
gap. Persist request ID/payload hash; identical replay returns the same job/material,
different payload with the same ID returns 409. Cache binds org/task/source/card/input/
renderer/submitter, not another actor's response or stale approval. Explicit terminal
retry retains the original manifest. Input change needs a new preview/request.

Heartbeat and `owned_job` fence publication. Acquire task/card locks in established
order; recheck current membership, source/bytes, requirement binding, archive state,
run ID and lease. Write a new encrypted object at
`org/{org_id}/screenshots/{asset_id}/{rendition_id}/{sha256}.png`, then commit material,
audit, task event and job success together. On failure/duplicate/cancel, remove only
attempt-owned objects without committed references. Crash reconciliation applies a
bounded grace period to unreferenced keys; losing a lease cannot delete retained bytes.
Storage keys are internal; public contracts expose IDs/descriptors/authorized links.

### Cost and execution limits

Rust is server-local compute: **zero vendor requests/cost, zero platform charge and
zero task liability**, including previews, retries and release rendering. Call
`budget_preflight.attach(quotes=[], planned_calls=0)` with configured billing currency.
Estimate basis is `zero` or a valid `cache_hit`, `next_call=null`. Exhausted/unpriced
task budgets or zero prepaid balance do not block zero-charge work; preserve budget
uncertainty for display, without a paid admission blocker. Reserve no funds and create
no fictitious `UsageRecord`/`VendorCall`. CPU/memory/storage limits remain operational
constraints. Paid OCR/vision would require a separate approved admission contract.

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
org storage; the renderer receives only selected verified bytes and public provenance.

## Data model and migration outline

Reuse screenshot assets/renditions/privacy, `Evidence`, `ResponseCard`,
`ResponseCardRevision`, `CardEvidenceLink`, Job, AuditLog and task events. Add only
annotation-specific immutable bindings, not a parallel approval machine or PDF store.

Every new table requires `org_id NOT NULL`, `task_id NOT NULL`, UUID primary key,
`UNIQUE(org_id,id)`, appropriate `UNIQUE(org_id,task_id,id)`, **ENABLE and FORCE RLS**
and USING/WITH CHECK against transaction `app.current_org`. Missing context denies
reads/writes. `bid_app` cannot own tables, bypass RLS or update/delete immutable rows.

| Proposed table | Immutable fields and relational constraints |
| --- | --- |
| `annotation_requests` | Human request ID/payload hash, task/extraction/requirement/card/expected revision, typed source refs, normalized plan/hash, input hash, reviewed source hash/attestation and optional inherited privacy review, renderer identity, submitter and job. Unique `(org_id,task_id,actor_user_id,request_id)`; exact replay only. Job owns execution state. |
| `annotation_materials` | Request, successful job/run, candidate asset/rendition, source manifest/hashes, content hash/mapping, renderer build/profile/font/protocol and creation time. Unique `(org_id,request_id)` and candidate rendition binding. Intended card is provenance, not permission to change a newer card. |
| `annotation_releases` | Material, canonical Evidence/candidate/hash, confirmed card revision, B02 and complete domain/co-sign approval manifest/hash, release job/run/rendition/hash/content mapping/renderer/time. Unique `(org_id,evidence_id,card_revision_id,approval_sha256,renderer_identity)`. Current/stale validity derives from live dependencies, not mutable confirmation flags. |

Typed nullable source columns plus exactly-one-kind CHECK replace unconstrained source
UUIDs/JSON-only FKs. Certificate references bind full source/task/TaskCertificate/revision/
file tuple; vendor refs bind asset/parent rendition/task/extraction/product selection/
revision/archive. Add missing parent unique keys before composite FKs. Requirements
include extraction/task; card revisions include card/task; renditions include asset/task;
jobs include task with run fenced by `JobExecution`. Actors use `(org_id,user_id) →
Membership`. Evidence/release references include the exact card revision link. Privacy,
signature and B02 review references use org composite keys and exact parent binding.
Every JSON-manifest reference is checked in addition to these relational constraints.

After approval, migrate in this order:

1. Add parent composite keys, tables, indexes, constraints, RLS and minimal grants.
   Extend job/profile CHECKs and `screenshot_rendition_gate` from
   [0023_screenshots.py](../../server/migrations/versions/0023_screenshots.py), preserving
   old row/profile interpretation. The current rendition uniqueness on asset/parent/
   plan/profile omits approval and renderer identity: preserve it as a legacy-profile
   partial unique index, and use a new-profile key including renderer identity and
   provenance hash. Add those nullable-on-legacy fields with new-profile NOT NULL
   checks. Different approvals/builds must not collide or reuse different footer bytes.
   Add server-bound certificate privacy review; never
   backfill old material as reviewed/confirmed. No global table or content migration.
2. Ensure tenant RLS/WITH CHECK and nondeferrable composite FKs reject wrong parents
   before business lookup errors. Follow
   [0049_product_guard_order.py](../../server/migrations/versions/0049_product_guard_order.py):
   parent-dependent business checks use ordered AFTER triggers after
   `RI_ConstraintTrigger_*`; necessary BEFORE lock/shape guards defer tenant mismatch
   to RLS and avoid parent-dependent disclosure. No BEFORE `NOT EXISTS` FK substitute.
   Keep task-first archive locks. For cyclic deferred FKs, completeness checks run at
   transaction end after matching constraints. Test actual ordering; current screenshot
   BEFORE guards do not establish it for these new tables.
3. Add immutable-row, human request/privacy, exact source/renderer, worker run/lease and
   release-content/approval guards. Extend scope/token CHECKs together. Complete
   single-domain/co-sign/B02 gates before exposing routes; workers cannot confirm.
4. Extend export attachments/manifests and DB `export_complete_gate` for B05 releases,
   keeping non-B05 behavior. Extend invalidation and task-event statement producers,
   job visibility and batched board metadata without per-row storage reads.
5. Grant runtime INSERT/SELECT only as needed; enable routes/worker kinds after guards.
   Quiesce incompatible workers for protocol cutover. Disable B05 writes for rollback,
   retaining originals/materials/jobs/audits; repair forward, not by dropping history.

## HTTP, CLI and JSON contract

Use the existing authenticated org transaction dependency. All path/body IDs must
resolve to one authorized task. No caller-provided org, actor, confirmer, storage key,
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
| Existing `PUT /cards/{id}`, `POST /cards/{id}/actions`, co-sign routes | Existing card/team-workflow commands | `CardUpdate`, `CardAction`, `CoSignConfirm`; no B05 confirmation shortcut |
| Existing `GET /jobs/{id}`, `POST /jobs/{id}/cancel` | `bid job status/wait/cancel --id UUID --json` | Existing safe job envelope, extended annotation kinds/results |

`AnnotationInput` is the plan file: extraction/card/expected card revision, source
selector and plan. CLI wraps it for preflight/submit. Required arguments fail immediately,
without questions; `--dry-run` rejects retry/submit-only fields. `--wait` polls safe job
status and maps terminal errors. No image crosses the CLI for computation. Register
new commands in `bid schema` only after approval; the JSON inventory is review-only.

Preflight is metadata/overlay preview. Persisted PNG links use application download
handlers requiring current identity and underlying task/source access, not public
bearer URLs. Expiry is 300 seconds, bound to org/object/hash/variant; never persist
links in events, audits or browser storage. Lists default 50, cap 100, stable
`(created_at,id)` keyset order; cursors bind org/actor/task/card/filter/access epoch.
Release lists are separate; show never embeds unbounded review history. All object
paths return identical 404 for missing/inaccessible IDs, including inaccessible
same-org tasks; denied actions on visible objects use 403, absent authentication 401.

Result 4.0 has exactly `ok`, `command`, `data`, `items`, `warnings`, `cost`, `duration_ms`.
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

Currency comes from configuration, not a USD constant. Dry-run includes existing
`BudgetPreflightData`; completed job cost comes from `job_cost(..., currency)` with
`basis=actual` and zero amounts. Reads/admission make zero calls. Duration is measured.
No image/base64, storage key, local path, secret or source-text dump in JSON. Only a
succeeded job with a verified material proves publication, not `ok=true` on admission.

| Exit | HTTP/error examples and meaning |
| --- | --- |
| 0 | Preflight complete, job accepted, read successful, or candidate/release fully published; preflight blockers still prevent submission |
| 2 | 400/413/422 invalid plan/limit/source; 409 preview/source/card/approval changed, B02 not confirmed, archived task or request-ID payload conflict; correct input/review before retry |
| 3 | 503 transient queue/storage/worker failure or retryable timeout; wait timeout retains job ID without cancelling or claiming publication |
| 4 | 401/403/404 identity/resource denial; integrity failure; missing/untrusted renderer or unsupported deployed protocol; nonretryable resource/process error; terminal cancellation |
| 5 | Reserved by Result, unused for one atomic image; no partial PNG/material success |

Errors retain `data.code`/`data.message` and safe job ID when available. Failed preview
or submit returns no success material; failed release creates no new human decision.

## Permissions, audit and task events

Add human-only `evidence:annotate` for preflight admission, candidate creation and
release retry. Role ceiling: org admin, `bidder`, `technical`, intersected with active
task membership; owner/contributor can create, reviewer/observer cannot. Update
`auth.HUMAN_ONLY_SCOPES`, live checks and DB token exclusions with all existing
human-only scopes, `evidence:confirm` and `export`. Tokens/agents never receive it;
old tokens gain nothing. Workers execute only fixed authorized requests.

| Actor intersection | Read | Create/attach candidate | Confirm Evidence / co-sign | Retry release / export |
| --- | --- | --- | --- | --- |
| Human org admin, task owner/contributor | Existing source/file/screenshot/card scopes | Yes with new scope, existing card write and privacy review | No domain by virtue of admin | Retry yes; export remains existing gate, no override |
| Human bidder, task owner/contributor | Same scoped reads | Yes | Commercial, when task domain granted | Retry yes; human bidder export gate |
| Human technical, task owner/contributor if otherwise eligible | Same scoped reads | Yes | Technical, when task domain granted | Retry yes; no export grant |
| Human task reviewer | Scoped reads | No general material/card writes | Granted professional domain plus matching org role | No material retry; export separately gated |
| Viewer/observer | Read only | No | No | No |
| Token/agent | Explicit read scopes intersected with live issuer/task/source grants | No | No | No |
| Worker | Fixed job input | Authorized immutable publication, not human card edit | Never | Render a fixed approved release; never human export |

Use `task_workflow.access`/`live_actor` before source reads/writes. B05 requires active
task membership; admin recovery is not annotation or domain authority. Certificate
reads additionally need `evidence:source:read`, `task:read`, `certificate:read`,
`certificate:file:read`; vendor reads use `screenshot:read` and underlying archive/
product permissions. Card actions retain `card:write`/`evidence:confirm`/`card:cosign`
and domain gates. Status/results/cancel use matching task/source access. Cancellation
is for the initiating human or authorized task owner with the annotation write ceiling,
never a read-only token. Hidden buttons do not enforce any of these restrictions.

Audit successful writes atomically with `versioned.audit`: `annotation.submit`,
`annotation.publish`, `annotation.release.enqueue`, `annotation.release.publish`,
`annotation.retry`, `annotation.invalidate`, plus existing job/card/privacy/review
events. Keep actor kind/user, job/run, task/card/source IDs, plan/input/image/approval
hashes, profile, reason code and time. Exclude pixels, observation text, arbitrary
labels, credentials, signed URLs and material text. Rolled-back writes have no success audit.

Use `task_events.append` and statement producers in
[task_event_sql.py](../../server/app/services/task_event_sql.py) for atomic `board_changed`,
`job_progress` and existing `access_changed`; reuse SSE/replay/reset, not a second
stream. Frames contain IDs/states only, ≤100 targets or `invalidate_all`, ≤4096 bytes.
Extend hidden-kind/`visible_job` handling for both annotation kinds.

Extend `task_board_projection.load` using batched metadata. New next actions:
`inspect_annotated_material` (“核对标注图”), `confirm_annotated_material`
(“确认标注材料”), `retry_annotation_release` (“重试确认图生成”),
`replace_annotation_source` (“重新选择来源”). Existing B02 confirmation stays the
first blocker when needed. Owner comes from assignment; eligible signers come from
domain/co-sign policy, not necessarily creator. Render success never marks a requirement
complete. `BoardNextAction`/target/schema updates are implementation dependencies.

## Console outline and bounded reads

Proposed route: `/app/org/tasks/{task_id}/cards/{card_id}/annotation?job={extraction_job_id}`.
Enter from a card material panel/dashboard action with explicit extraction scope.
Show owner, required domain/reviewer, blocker and one primary next action. Keep unsent
geometry only in current-tab memory; clear material/preview data on org/task switch.

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
observation, response, submit and review. Show B02, remaining co-sign domains and release
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
| Membership/domain loss, archive/withdrawal | Deny writes/consumption; retain authorized history; fence late publication even after object upload |
| B02 incomplete/invalidated, partial/stale co-sign, wrong evidence/warnings | Candidate preparation may remain readable; acceptance/release/draft/export blocked by the corresponding gate |
| Release missing/corrupt/stale/content mismatch | Block export with exact reason/next action; no candidate fallback or worker confirmation |
| Unsupported/untrusted renderer | Explicit configuration failure; no Python or old-profile fallback |
| UI read failure/org switch | Cancel old reads, remove stale images, require authorized refresh, retain unsent input only in the active context |

Implementation acceptance must prove the full flow; these runtime checks have not run
as part of this draft.

| Area | Required acceptance and repeatable artifact |
| --- | --- |
| Each new table | Two orgs A/B and missing context; runtime SELECT/INSERT/UPDATE/DELETE rejection, FORCE RLS/no bypass, immutability, every source/task/card/Evidence/review/job/actor composite FK. Same-org T1/T2 and mixed parents. Assert RLS/FK before business errors, concurrent/same-statement parents and deferred completeness. |
| Each route/download/job variant | A cannot list/show/preview/submit/retry/cancel B; uniform missing/unauthorized 404. Signed-link org/object/hash/expiry/tampering/removal, archived tasks, historical source, cursor/filter replay and candidate/release download gates. |
| Roles and gates | Owner/contributor vs reviewer/observer, admin without domain, bidder commercial, technical technical, inactive/removed member, token/agent/worker. API and DB reject human-only scopes including evidence:confirm/export. Partial co-sign, same signer twice, stale round/policy/B02, wrong Evidence/hash/warnings/reopen fail; worker cannot confirm. |
| Real renderer flow | Synthetic pages/archive → API/CLI → real Rust → storage/job → card review → release → draft/export. Artifact includes source/plan/profile hashes, candidate/release PNGs, unchanged original, expected pixels and reversible mapping. Repeat fixed inputs for deterministic bytes and profile changes for cache separation. |
| Pixel/process limits | No crop/crop/20 boxes, overlap/edge/small boxes, rotation, Chinese source pixels, cropped/redacted parents, bounds/21 boxes/bools/floats/labels, min width/footer overflow, 8192/20M/40MiB/decoded/metadata limits, malformed PNG/receipts. Timeout/cancel/pipe bounds/no-network/CPU-RSS and reap assertions. |
| Jobs/budget/publication | Transactional enqueue/crash recovery, request replay/conflict, changing input/withdrawal/authority during render, lease takeover, retries/cancel/orphans. Zero quotes/reservations/usage/vendor calls and exact Result 4.0 cost with exhausted budgets. One verified binding per published object. |
| Draft/export | Unconfirmed material never reaches rows/files. Complete B02/domain/co-sign admits canonical Evidence; B05 image export additionally requires release. Evidence hash unchanged. Pin replacement/B02 reopen/card relink/signer loss/tampering block consumption; non-B05/prototype paths unchanged. |
| CLI/schema | Snapshot every new command and dry-run/queued/succeeded/duplicate/retry/cancel/error/empty/paged result; seven keys, Decimal strings, configured currency and exit 0/2/3/4. No false partial image. Client never runs Rust or downloads image for processing. |
| Browser end to end | Follow [requirement-confirmation.spec.js](../../web/e2e/requirement-confirmation.spec.js) Playwright mocked-API pattern: exact routes/bodies, bounded reads, preview/explicit use/review, role/B02/co-sign blockers, cancellation/failure/retry, conflicts, release stale/wait, org-switch races and keyboard use. Reject unexpected/paid requests. Retain screenshots, trace and JSON with seed, request counts and replay command. |
| Events/board | Correct owner/blocker/next action on publish/review/release/invalidate; no success event after rollback; replay/reset/revocation observes task/org boundaries and frame caps. Board state never authorizes consumption. |

Retain artifacts under ignored `data/work/annotation-acceptance/`: PNGs, hash manifest,
CLI snapshots, Playwright screenshots/trace/result JSON and DB report, never under
`docs/`. Browser mocks prove UI, not database isolation or Rust execution; keep separate
real-service/renderer acceptance. Synthetic data/Providers only, no external services.
Define failure scenarios and end-to-end cases first; do not add after-the-fact unit
tests that merely repeat implementation.

Draft validation is limited to ruff, format check, explicit pyright on the documentation
module, import and schema consistency. No runtime implementation or migration is included.

## Decisions

The owner approved every recommended default; implementation follows these decisions.

| Decision | Approved default | Reason |
| --- | --- | --- |
| First source | Certificate EvidenceSource, one card/page; vendor slice follows | Complete common workflow with existing archive, without inventing profile files. |
| Profile/contract attachments | Separate immutable archive contract before enablement | Current declarations provide no attachment storage/authorization chain. |
| Command/renderer | Cloud `bid evidence stamp`, annotation read/recovery commands, extend `stamp/` | Matches product direction and existing CI/service ownership. |
| Preview | Read-only plan/overlay before job; actual candidate before attach/confirm | Prevents accidental submission while reviews bind real pixels. |
| Attachment | Explicit human CardUpdate after render | Preserves concurrent edits and human ownership of observation/response. |
| Confirmed watermark | Immutable UNCONFIRMED candidate plus exact-approval CONFIRMED release | Preserves image/hash/signature history without overwriting files. |
| Consumption | Confirmed canonical Evidence for draft, current release for all B05 image exports | Retains human gates and prevents stale/unconfirmed labeled attachments. |
| Reviewer policy | Existing primary domain/co-sign plus B02 acceptance | No competing or weaker approval path. |
| Creation role | Human-only evidence:annotate, task owner/contributor; existing professional confirmation | Source/privacy/material choices remain attributable team actions. |
| Pixel semantics | Existing fixed font, 2px boxes, no labels/rescale, versioned profiles | Reuses bounded renderer while keeping deterministic provenance. |
| Source change | Invalidate on task pin/source/review change, not library edit alone | Preserves task snapshots and requires explicit reconfirmation when inputs change. |
| Cost | Zero vendor/platform/task charge, budget preview retained | Server compute does not need a fictitious model allowance. |
| Runtime | 20s process/60s attempt, cancel poll ≤1s, ≤3 attempts, initial 1 CPU/512MiB | Bounded recovery consistent with screenshot patterns; test the envelope at maximum input. |
| Batching/history | Single page/card, ≤20 boxes; new plan creates new material | Keeps conflicts and next review action clear before adding batch complexity. |
| Stale release access | Metadata history readable under live ACL; normal CONFIRMED PNG preview denied | Retains audit history without silently reusing obsolete approvals. |
