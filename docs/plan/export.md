---
kind: plan
status: "Partially implemented"
---

# Contract: human export of response sections to Word

Status: **Partially implemented**. Covers [roadmap](roadmap.md#coverage-matrix-parsing-requirements-evidence-responses-and-checking) B11, depending on R01/B07/B08. Approved scope follows [Decisions](#decisions); delivery and remaining integration boundaries:

| Part | Status and boundary |
| --- | --- |
| Human template binding, preflight, prepare → worker candidate → release → download, final section (正式件)/review copy (审阅件), certificate-page attachments, encryption/audit/limits/hashes | **Implemented**; mechanisms/code entrypoints: [human-section-exports.md](../notes/human-section-exports.md) |
| Image-evidence attachments and prototype_decision_required/prototype_replacement_pending/prototype_decision_stale | **Implemented**; image/prototype gates: [human-section-exports.md](../notes/human-section-exports.md) |
| Word visual pagination | Accepted in macOS Microsoft Word with synthetic long responses (150 requirements/130 confirmed attachment pages): editable tables, no document protection, separate embedded images, one-to-one bookmarks/attachments, one page per attachment; [development.md](../guides/development.md#check-an-export-in-word) |
| WPS visual pagination/complete isolated S3 download | Retained acceptance items; DOCX unpack/reopen/synthetic-artifact checks do not replace them |

## Goals and boundaries

Apply an org (organization/tenant, 单位) Word template at a fixed revision to an explicitly selected deviation (偏离) table draft (初稿) and its confirmed Evidence, exporting editable `.docx` response (响应) sections. Preserve the substantive (实质性) response summary, commercial (商务) response deviation table, technical (技术) response deviation table, comply-only (须遵守) clauses, gaps (缺口) and evidence (证据) attachment index. Copy response text/deviations/explanations verbatim without model rewriting.

Human confirmation (人工确认), complete coverage across three output classes and invalidation follow [ADR 0005](../adr/0005-human-confirmed-responses.md); delivered cards/table assembly (组表): [response-cards.md](../notes/response-cards.md). DraftView.status remains draft, without assuming a confirmed-draft state. This human export decision binds the entire draft input hash; response rows/Evidence must separately pass upstream confirmation, and comply_only needs valid human disposition. Export decisions neither replace per-card confirmation nor change upstream states.

Model drafting/outbound redaction (遮挡) are separately defined by those contracts. B11 consumes explicit DraftView/fixed card revisions/Evidence, without relying on other-checkout files/unshipped commands/model-job internals. Export adds no models/OCR/search/web capture/outbound redaction; confirmed source text/real attachments remain in authorized downloaded files.

Product background is a real 663-page winning bid (标书) PDF: bid/authorization forms, three response tables, approximately 305 pages of itemized technical responses, 217 screenshots/diagrams, 125 certificate (证书)/test-report scan pages, performance contracts/award notices/team social-insurance/certificates, a 164-page technical solution and almost page-by-page seals. This informs scale/boundaries, not materials read by this contract, authorized for copying or test data. This slice delivers response sections/certificate pages with confirmation chains; confirmed image attachments follow the integration above. It promises no whole-file reproduction and does not call scan images editable text.

Hard rules: [agent.md](../../agent.md#hard-rules-must-never-be-violated). File constraints follow [versioned templates](../notes/versioned-templates.md), [versioned certificate originals](../notes/versioned-certificate-files.md), [org isolation](../notes/tenant-isolation.md). Upload/storage/human confirmation does not certify original authenticity, complete tender-obligation coverage or satisfaction of tender documents (招标文件).

## Human identity and release gate

Recommend export for valid human bidder memberships in valid orgs only. admin maintains template adaptation without automatic bidder release responsibility; technical/viewer may review drafts under existing permissions. Role decisions: [Decisions](#decisions). Also require task:read/draft:read/card:read/template:read and reads for materials actually referenced. Certificate pages require evidence:source:read/certificate:read/certificate:file:read together.

Routes/services/DB check trusted authentication: actor_kind=session, token_id=null, valid user/org/membership and permitted current role. This applies to every preflight/submission/release/history/link/file entrypoint. Human-initiated built-in agents retain agent identity, never impersonate session. API tokens cannot request/store export/evidence:confirm; old tokens gain no privileges and cannot use job:read/template:read/original-download rights for export candidates/files/signatures.

To fit [background jobs](../notes/background-jobs.md) without worker export rights, recommend two steps:

1. **Human preparation**: after preflight, submit fixed inputs with expected_input_hash/item warning acknowledgments. Service creates immutable export run and dispatches export_render. Workers read only run-listed materials/create private candidates, without final records/publisher fields/download links.
2. **Human release**: worker completion is awaiting_release only. Human submits candidate hash; service rechecks all identities/inputs/consumption gates before immutable export/success audit. CLI --wait ends at readiness without release. No scheduled-worker or saved-human-identity automatic release.

Trusted services supply actor context; callers cannot submit org_id/actor/publisher/release time. DB rejects token/agent/worker publication insertion/state changes/human fields; runtime roles cannot change gates/history. Arbitrary SQL forging trusted authentication context is not a supported authentication entrypoint, retaining response-cards' trust boundary; RLS cannot claim recognition of stolen human sessions.

## Export gates and document content

Preflight/submission/worker loading/candidate saving/human release/download check their applicable gates. Draft-fixed extraction job/tender Document/all Requirements/card revisions/dispositions/citation hashes/Evidence/material selections/template selection must agree within one org/task. Never implicitly select latest drafts/templates. Old extraction jobs may be explicitly selected with warnings; coverage includes only that job's saved requirements.

| Condition | final_section final response section | review_copy |
| --- | --- | --- |
| validity=stale, cards requiring reconfirmation or replaced selections | Reject; upstream rereview/table assembly | Reject equally, not watermarked stale responses |
| Any invalid requirement citation, including comply-only/gaps | Reject with requirement IDs/reasons | Reject equally, no invented locations/citations |
| Nonempty gaps with valid citations | Reject export_gaps_present | Explicit gap list/document-wide “审阅件·存在缺口·不得提交”, completion code 5 |
| Unconfirmed/rejected/awaiting-material cards | Upstream gaps block final | Requirements/location/gap reasons only; no candidate response/Evidence/screenshots/excerpts |
| Rows linked to unconfirmed Evidence/incomplete confirmations | Hard reject as illegal input | Same; cannot simply remove Evidence or turn into commitment |
| Original/template/page-image hash-length mismatch/missing file | Hard reject/no partial files | Same, no placeholders/blank-attachment fallback |
| Valid confirmed negative deviation (负偏离) | Truthful output/warning acknowledgment; not automatically rejected/partial | Truthful output equally |
| Extraction omissions/rejections/historical extraction/declared materials/obligation warnings | Explicit display/human acknowledgment binding, no complete-text claim | Same, review mode does not remove warnings |
| Missing binding/unsupported anchors-styles/resource-attachment limits | Reject explicitly | Same, no template switching/page omission/quality loss |
| Confirmed prototype (原型) Evidence, keep/replace undecided | Reject prototype_decision_required | Allow; undecided alone does not reject |
| Prototype replacement selected but no confirmed real replacement | Reject prototype_replacement_pending | Allow still-valid confirmed original image |
| Decision-bound Evidence/card/image/HTML hash changed | Reject prototype_decision_stale; new decision | Decision staleness alone does not block; material/card invalidity follows above |

Prototype decisions: [screenshots.md](screenshots.md#prototype-decisions-before-final-export). Reasons appear only in internal preflight/review workspace, not drafts/captions/exports.

Review copies always bear “审阅件·不得提交”/code 5 even without gaps. Renaming downloads cannot make final sections; resubmit final_section. Without confirmed rows, review copies list only valid comply-only/gaps, never default-satisfied rows. Final means section-export conditions passed, not complete-bid finalization/compliance approval.

Every requirement appears exactly once in one of three tables/comply-only/gaps, retaining upstream source order/table assignment. No implicit package/category/deviation filters/cross-extraction merging. Empty tables retain headers/empty explanation. Rows include original category/star, tender text/actual location, response kind/text, deviation/specific difference/evidence index. Visible “负偏离” text is mandatory beyond color, never “无偏离” or hidden in comments/revisions. Commitments say “承诺” with no Evidence; declarations say “声明”, with no fabricated attachment numbers when originals are absent.

Comply-only retains source text/location/human disposition without becoming material responses. Empty gaps explicitly say “本次抽取范围内无缺口”. PDF citations retain source pages; Word citations retain real structure with page=null. Stable numbers/bookmarks reference attachments, never Word-reflow page numbers as tender-source pages.

Each gate generates stable issue_id bound to reason/affected objects/fixed revisions. Submission exactly acknowledges all acknowledge issues, never force=true/ignore-all. Blocks cannot be bypassed by acknowledgment. Warning acknowledgment covers this export scope only and creates no upstream confirmation/authenticity/materials.

## Fixed templates, placeholders and section mapping

Input requires current valid task_template_id, resolving fixed template_revision_id/original SHA-256/package number. Reject resource current pointers/external URLs/arbitrary disk paths. New template revisions leave selections unchanged; explicit replacement invalidates runs, and reselecting old templates needs new selection/export decisions. One run uses one template; package numbers identify selections without narrowing draft coverage.

New immutable export_template_bindings are human-admin/template:write maintenance, fixing exact revision/hash. Changed mappings append new IDs, retaining old bindings. Existing upload creates template revisions; B11 neither overwrites originals, infers chapters nor assumes declared chapters adapted.

Recommend independent body-paragraph placeholders for six fixed sections:

| section | Required exactly-once anchor | Fixed content |
| --- | --- | --- |
| substantive | `{{bid.substantive}}` | Substantive response summary |
| commercial | `{{bid.commercial}}` | Commercial response deviation table |
| technical | `{{bid.technical}}` | Technical response deviation table |
| comply_only | `{{bid.comply_only}}` | Comply-only clauses |
| gaps | `{{bid.gaps}}` | Gap list/no-gaps-in-scope explanation |
| evidence_appendix | `{{bid.evidence_appendix}}` | Evidence index/declaration excerpts/certificate-page attachments |

Order is fixed as above, with no missing/duplicate/nested anchors or textbox/header/footer/cell placement. Whole markers split across runs may be recognized by visible paragraph text; no other text in that paragraph. Optional {{bid.task_name}}/{{bid.tender_number}} metadata comes only from fixed Task snapshots; missing used fields reject. No general expressions/scripts/arbitrary object paths/free business text. Unknown markers error; metadata cannot act as responses/qualification (资格) proof.

Each mapping specifies real heading/table style IDs; three tables also set column order/width ratios. The customary winning-bid response-table layout has exactly one each of four columns, positive widths totaling 100%, with adaptable order/width:

| Column | Header | Content |
| --- | --- | --- |
| ordinal | 序号 | Continuous numbering from 1 within each table |
| requirement | 招标文件要求 | Verbatim source citation; starred requirements start ★ |
| response | 投标文件响应内容 | Verbatim confirmed response with internal links like “（见附件 E003、声明 D001）” appended |
| compliance | 响应情况 | No deviation: substantive table “响应且无负偏离”, other tables “响应”; positive deviation (正偏离) “正偏离：” plus difference; negative deviation bold “负偏离：” plus difference |

Body omits internal categories/source coordinates/card-Evidence IDs/confirmers/timestamps/hashes, retained in [provenance manifest](#provenance-manifest). Comply-only table is “序号｜招标文件要求｜响应情况（遵守）”; gaps “序号｜招标文件要求｜缺口原因”. Old seven-column bindings fail preflight export_binding_outdated; create new current-column bindings. Other three sections use fixed fields without scriptable row templates. Renderer guarantees visible headings/gap warnings/negative-deviation text, not swallowed by empty styles/hidden fonts/template conditions.

Beyond upload validation, support only bounded OOXML: retain page size/margins/orientation/page-section breaks/approved styles; reject macros/OLE/external resources-fields/altChunk/comments/revisions/hidden body/unsupported textboxes. Only PAGE/NUMPAGES fields, no execution/external access. Static content is registered headings/blank layout/header-footer formatting only, without sample responses/completed business tables/old-project names/certificate pages/unconfirmed images. No initial embedded-media templates; logos need later scope. admin checks static contents/template hash at binding; adaptation is not Evidence confirmation.

Preflight lists anchor paragraph/chapter locations/style resolution/static-content digest/unsupported items. Missing/unsupported needs revised template/mapping, not silent deletion/repair. Word uses actual paragraphs/cells/repeated headers/fixed widths, retaining long-response wraps; scans are images. Editable does not promise lossless arbitrary-template round trips, final automatic-contents pages, fixed full-bid page counts or identical pagination across Word/WPS fonts.

## Evidence index and PDF page attachments

Index is “编号｜材料｜内容｜对应条款”: certificate/image attachments E001 onward, declarations D001 onward. Material shows resource name/number; content is “原件第 N 页”, declaration excerpt or observed image; corresponding clauses identify table/within-table ordinal. Each attachment starts a new page, with only captions such as “附件 E007　材料名称　原件第 3 页” or “附件 E008　证据图片”.

Traverse confirmed Evidence only from draft rows' fixed card revisions. Declaration excerpts/exact selection/revision enter indexes still identified as declarations; product URLs cannot obtain new screenshots and feature declarations cannot become drawn implemented UIs. Unlinked certificates/full library/arbitrary extra uploads never enter.

Resolve certificate pages through Evidence → evidence_source → task_certificate → certificate_revision → certificate_file, checking confirmed_by/at, quote_check=human_page_review, active selections/original hash/page/archived PNG hash. Source Archive remains permanently unconfirmed; Evidence grants consumption without Archive-field changes.

Recommend attachment numbers in first-response-reference order, deduplicating pages by fixed selection/revision/original hash/page/PNG hash. Multiple Evidence on a page retain separate excerpts/confirmations/row references. One confirmed page does not admit others; all multi-page attachments need per-page confirmation. Missing required pages means no partial attachments. Full original PDF is never OLE/hidden/extra DOCX files.

Each confirmed page starts a new appendix page scaled into usable area, with an outside-image attachment-number/material-name/original-page caption. Material nature, certificate revision, SHA-256 hashes and requirement identifiers stay in the provenance manifest rather than printed captions, matching the document-content boundary above. Titles/footnotes cannot cover images. Reuse existing pdf-page-preview-v1 full-page 150 dpi RGB PNG with identical bytes/hash before/after embedding. Change Word display size only, never resample/crop/fill/remove original watermarks/change certificate pixels. Scan/photo text remains uneditable images; tables/headings/indexes/responses editable.

Internal provenance for each index item retains Evidence ID/confirmer-time/selection-revision/nature/excerpt or original page/hashes, without storage paths/credentials/signatures. Body references attachment numbers/bookmarks, not guessed Word pages. No initial inline/appendix switch; separately approved layouts enter input hashes.

Scan-only pages with no verifiable text may have only upstream source previews and cannot be confirmed/attached merely because export needs them. They require a supported upstream confirmation chain: the separately contracted traceable OCR/human transcription path, or confirmed image_region under [screenshots.md](screenshots.md#gates-for-images-to-become-response-evidence). B11 supplies no missing upstream gate. Background reports/contracts/social-insurance/screenshots can be consumed only as supported confirmed-source pages; others remain manual or future material-type extensions.

## Data models and database constraints

Each new business table requires org_id NOT NULL, UNIQUE(org_id,id), ENABLE/FORCE RLS. Runtime roles lack ownership/SUPERUSER/BYPASSRLS/TRUNCATE/history-change rights; absent org context denies reads/writes. Future implementation delivers tables/policies/constraints/two-org acceptance together, not tests appended later.

| New table | Fields, ownership and write boundary |
| --- | --- |
| export_template_bindings | template_revision_id, template_sha256, binding_hash, sections, static_content_hash, adapter_version, reviewed_by/at; unique revision/mapping hash; append-only human admin |
| export_runs | task_id, extraction_job_id, document_id, draft_run_id, task_template_id, binding_id, render_job_id, mode, input_hash, manifest, issue_snapshot, acknowledged_issue_ids, initiated_by/at; immutable manifest, human exporter creation, no task/selection/draft rebinding |
| export_run_items | run_id, response_item_id, requirement_id, card_revision_id?, kind, ordinal; unique run/requirement; covers all draft row/comply_only/gap via immutable response_items, no FK-free JSON impersonation |
| export_run_evidence | run_id, run_item_id, card_revision_id, evidence_id, attachment_ordinal?; confirmed row links only, page attachment through typed Evidence FKs without unconstrained resource_id; unique item/Evidence |
| export_render_candidates | run_id, render_job_id, attempt_id, input_hash, plaintext_sha256, size_bytes, object_key, renderer_profile, manifest_hash, created_at; only bound worker/current attempt appends one; private staging, not exports/no read entrypoint |
| exports | run_id, candidate_id, task_id, mode, input_hash, manifest_hash, file_sha256, size_bytes, media_type, object_key, released_by/at; at most one immutable publication/run, authorized humans only/no worker insertion/runtime-role edits-deletion |

All relationships use org_id composite FKs, adding parent unique keys if needed. Humans use `(memberships.org_id,user_id)`; runs constrain full task/document/extraction/draft chain. Same-task template selection and same-revision binding are mandatory. Items/cards/Evidence share task/job/requirement/card; wrong same-org parents reject too.

Read policies restrict actors beyond org RLS: exports SELECT for currently authorized humans only, no token/agent/worker descriptors. Runs/items/links/candidates are human-exporter reads; workers use strictly current-run/attempt-bound internal inputs. Human admin/bidder may read bindings; workers only their fixed run binding. Narrow worker rules grant neither generic job:read nor exports SELECT; absent actor context means no readable rows.

Immediate actor gates/deferred integrity constraints guarantee at least:

1. Valid human exporters create run/exports; human admins bindings. export stays outside token allowlists; prohibited direct SQL writes fail. API/service/DB consistently reject nonhumans, not merely accept valid released_by FKs.
2. Submitted/released rows reference current confirmed valid response revisions, evidence links all confirmed, commitments exactly zero links, comply_only valid human dispositions, final_section no gaps. Nonrows link no Evidence; complete set equals response_items without missing/extra/mixed-job rows.
3. Candidates bind current export_render job/run/attempt/input hash. Internal workers can only make these writes, not change runs/confirmations/exports/human audit identity. Generic job queries/results expose no candidate path/file/download bypass.
4. Release references complete successful current candidates matching mode/manifest/hash/descriptors. Cancelled/failed/old-attempt/unconfirmed/no-context transactions fail. Services verify bytes/citations; DB checks identity/state/relations/integrity, not DOCX layout/authenticity.
5. Lock order task → ID-sorted cards → related records → run/job; publication rereads all dependencies. Task material/template replacements/card reopening/citation repairs/member disabling/publication serialize consistently. File/render I/O is outside long transactions; final changes reject without success audit.

RLS-protected manifests include each requirement/citation hash, kind/order, card/disposition revisions/confirmations, Evidence/all selections-revisions-file hashes, template/binding/mode/task metadata/all issues-acknowledgments/table-assembly-adapter-render versions. Normalized rows govern permission/FKs; generate/validate manifests against them, not one uninspectable total hash.

## Pydantic contract

Approved fields follow; executable validation: [export_contracts.py](../../server/app/schemas/export_contracts.py). Reuse [`Contract, Cost, Result`](../../server/app/schemas/contracts.py) and upstream DraftView/Source, extra=forbid, without new top-level Result/current-version freezing.

```python
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ExportMode = Literal["final_section", "review_copy"]
SectionKind = Literal[
    "substantive", "commercial", "technical", "comply_only", "gaps", "evidence_appendix"
]
ColumnKind = Literal["ordinal", "requirement", "response", "compliance"]


class ExportColumn(Contract):
    key: ColumnKind
    width_percent: Decimal = Field(gt=0, le=100)


class ExportSectionBinding(Contract):
    section: SectionKind
    heading_style_id: str = Field(min_length=1, max_length=200)
    table_style_id: str = Field(min_length=1, max_length=200)
    columns: list[ExportColumn] = Field(default_factory=list, max_length=4)


class ExportBindingCreate(Contract):
    template_revision_id: UUID
    expected_template_sha256: Sha256
    sections: list[ExportSectionBinding] = Field(min_length=6, max_length=6)
    expected_static_content_hash: Sha256 | None = None
    dry_run: bool = False


class ExportBindingView(Contract):
    id: UUID
    org_id: UUID
    template_revision_id: UUID
    template_sha256: Sha256
    binding_hash: Sha256
    static_content_hash: Sha256
    adapter_version: str
    sections: list[ExportSectionBinding]
    reviewed_by: UUID
    reviewed_at: datetime


class ExportIssue(Contract):
    issue_id: Sha256
    code: str = Field(min_length=1, max_length=100)
    severity: Literal["block", "acknowledge"]
    requirement_ids: list[UUID] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)


class ExportPrepare(Contract):
    draft_id: UUID
    task_template_id: UUID
    binding_id: UUID
    mode: ExportMode
    expected_input_hash: Sha256 | None = None
    acknowledged_issue_ids: list[Sha256] = Field(default_factory=list)
    dry_run: bool = False
    retry: bool = False


class ExportPreview(Contract):
    dry_run: Literal[True] = True
    task_id: UUID
    draft_id: UUID
    mode: ExportMode
    input_hash: Sha256
    ready: bool
    requirement_count: int = Field(ge=0)
    table_rows: dict[Literal["substantive", "commercial", "technical"], int]
    comply_only_count: int = Field(ge=0)
    gap_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    attachment_pages: int = Field(ge=0)
    issues: list[ExportIssue]
    estimated_output_bytes: int | None = Field(default=None, ge=0)
    estimated_duration_ms: int | None = Field(default=None, ge=0)
    estimated_cost: Cost


class ExportRunView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    draft_id: UUID
    render_job_id: UUID
    mode: ExportMode
    input_hash: Sha256
    state: Literal[
        "queued", "rendering", "awaiting_release", "released", "failed", "cancelled", "invalidated"
    ]
    candidate_sha256: Sha256 | None
    export_id: UUID | None
    issues: list[ExportIssue]


class ExportRelease(Contract):
    expected_input_hash: Sha256
    expected_candidate_sha256: Sha256


class ExportFile(Contract):
    name: str = Field(min_length=1, max_length=200)
    sha256: Sha256
    size_bytes: int = Field(gt=0)
    media_type: Literal[
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ] = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class ExportView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    run_id: UUID
    draft_id: UUID
    task_template_id: UUID
    template_revision_id: UUID
    binding_id: UUID
    mode: ExportMode
    completion: Literal["complete", "partial"]
    validity: Literal["current", "stale"]
    input_hash: Sha256
    manifest_hash: Sha256
    file: ExportFile
    released_by: UUID
    released_at: datetime
    issues: list[ExportIssue]
    invalidated_requirement_ids: list[UUID]


class ExportDownloadLink(Contract):
    export_id: UUID
    file: ExportFile
    url: str
    expires_in: Literal[300] = 300


class ExportDownloadReceipt(Contract):
    export_id: UUID
    output_path: str
    file: ExportFile
```

Cross-field validation and return additions:

- Six unique sections in fixed order; three table columns exactly four, other sections empty. Real matching-type styles/nonblank strings/timezone-aware times/lowercase SHA-256 only. Binding dry-run data returns template/static hashes/anchor locations/adapter version/issues without fabricated binding ID/reviewer; creation requires matching static-content hash.
- Non-dry-run prepare requires expected_input_hash, deduplicated issue acknowledgments exactly matching preflight. dry-run accepts neither retry nor acknowledgments. Preflight hash fixes facts/issue set without circular client-action hashing; complete manifest separately binds acknowledgments as manifest_hash.
- ready means no block, still needing per-issue human acknowledge. Nonnegative counts/all table keys; row+comply_only+gap equals requirement_count. Unknown bytes/duration null.
- run.state derives from job/candidate/exports/live invalidation without update API. candidate_sha256 non-null only when ready/published, export_id only with publication record.
- completion=partial iff mode=review_copy; final_section requires zero gaps. validity/affected items recompute on read without history-file changes; issues carry nonrequirement invalidations such as template changes.
- Safe .docx filenames without separators/control characters and size limits below. URLs must be fixed same-service relative routes. Inputs cannot set confirmation/storage/free text-Evidence/non-SHA file copies/gate bypasses. Service recomputes both expected hashes.

## CLI, API and Result

All commands support --json, fail on missing inputs and have no interactive default confirmation. Both modes use identical services/PostgreSQL RLS/identity gates. --input FILE contains these models; CLI options set dry-run/retry without conflicting duplicate values. --wait is client waiting, absent from input manifests.

| CLI | API | Input/output |
| --- | --- | --- |
| `bid export binding create --input FILE [--dry-run]` | POST /export-template-bindings | ExportBindingCreate → adaptation preflight / ExportBindingView; human admin only |
| `bid export binding list --template-revision V` | GET /export-template-bindings?template_revision_id=V | Immutable same-revision bindings; human admin/bidder with template:read |
| `bid export prepare --task T --input FILE [--dry-run] [--retry] [--wait]` | POST /tasks/{T}/export-runs | ExportPrepare → ExportPreview / ExportRunView; human exporter only |
| `bid export run show --id R` | GET /export-runs/{R} | ExportRunView readiness/invalidation, no candidate download |
| `bid export release --run R --input FILE` | POST /export-runs/{R}/release | ExportRelease → ExportView; human exporter only |
| `bid export list --task T` | GET /tasks/{T}/exports | Immutable history summaries/live validity |
| `bid export show --id E` | GET /exports/{E} | ExportView; historical completion does not imply downloadable |
| `bid export download --id E --output NEW.docx` | GET /exports/{E}/download-link, then GET /exports/{E}/download?signature=... | ExportDownloadLink → verified ExportDownloadReceipt |
| `bid job status/wait/cancel J` | Existing /jobs/{J}, /jobs/{J}/cancel | export_render additionally requires human export permission; no candidate/body disclosure to tokens/agents |

Binding is template maintenance without admin export rights; all other commands follow export gates. Cancellation affects unpublished rendering only, never existing history. API acceptance is not successful export; successful release is not local download.

Result retains exactly **`ok`, `command`, `data`, `items`, `warnings`, `cost`, `duration_ms`**. Detail/writes use model data/items=[]; lists use items/filter scope in data. Errors use data.error/stable code/necessary IDs without input/body/credentials. Preflight blocks/review issues are data.issues, warnings redacted codes/IDs only. Successful downloads return binary streams; links/CLI receipts remain Result. Errors precede streams; CLI reports interruption as failure without treating partial files as JSON/success.

| Exit code | Meaning and examples |
| --- | --- |
| 0 | Unblocked preflight/acceptance/binding/history/candidate readiness or successful final-section release/complete download. Readiness explicitly state=awaiting_release without export_id |
| 2 | Missing/illegal input/expected-hash conflict/missing acknowledgment/final_section gaps/stale draft/bad citation/unsupported template/limit/existing path; correct and resubmit |
| 3 | Retryable network/storage/queue/render timeout; post-submission fixed-input change is export_input_changed, requiring preflight reread/human resubmission, no automatic rebinding |
| 4 | Identity/role failure/token-agent-worker release/unauthorized object/corruption/internal relation-confirmation failure/nonretryable rendering |
| 5 | Explicit review_copy successfully released/downloaded; ok=false/completion=partial with valid output/all issues, never a damaged half-DOCX |

Errors/partial successes have ok=false, others true. HTTP 401/403 retain authentication/action meanings; missing/cross-org/unauthorized resources all 404. Input 400/422, revision/input conflicts 409, temporary faults 503 map to codes above. Blocked dry-run returns 2/ExportPreview; history reads remain 0 with mode/completion/validity. job status/wait report rendering only; review_copy readiness is 0, final release/download 5, avoiding mistaken human release. Nonretryable protocol errors use Result/code 4, never local traceback/code 1.

Register new commands/Pydantic Schema in bid schema, retaining old structures; incompatible changes increment versions under [CLI contract](../../agent.md#hard-rules-must-never-be-violated).

## Jobs, costs and resource limits

Add jobs.kind=export_render; document_id is draft-selected extraction's actual tender Document, never certificate/nullable relaxation. Fix run/job inputs together before dispatch, retaining retryable jobs after dispatch failure. Restore org context/recheck initiator membership/read/export role, using existing lease/run_id/cancellation/bounded retries. Workers inherit no session and only run/attempt-scoped render capability; internal signatures/job parameters are not human release authority.

Recommend internal `ExportRenderer.render(manifest, authorized_inputs) -> candidate`, with service-resolved authorized template/pages and no external URLs/caller paths; it is not a model-calling Provider. Reuse python-docx/PyMuPDF/standard ZIP, no major dependency/cloud conversion/real vendor calls. Workers are terminable; deadlines/cancellation kill/reap owned render processes rather than leave timeout threads writing objects. Read/validate/stage media page-by-page without simultaneous whole-PDF/scan decoding.

Fixed server profiles manage approved maxima: 2,000 requirements, 300 unique attachment pages, 512 MiB DOCX, 1 GiB unpacked size, 1 GiB render-process memory, 15-minute job deadline; deployments may lower them. Stricter template/original/PNG limits remain; export cannot expand uploads. 300 means attachment pages, not total Word pages. The background count of 217 screenshots does not itself establish supported coverage; confirmed image attachment consumption follows the integration scope above. Serialized overflow fails without silent page omission/downsampling/incomplete final sections. Recommended thresholds are approved.

dry-run performs identical authorization/citation/manifest/file-integrity/template-mapping checks and returns table/comply-only/gap/negative-deviation/attachment counts, every gate and reliable estimates, with no queues/business-audit-usage writes/objects. It does not render full files, so unknown bytes/time is null and total pages are not fabricated facts.

Export invokes no model/OCR; actual/estimated Cost is llm_tokens=0, ocr_pages=0, usd=0, no empty UsageRecord/model debit/insufficient-model-balance block. Record actual render duration/storage bytes without promising free infrastructure; export/storage billing is outside this slice. Upstream drafting costs remain in original jobs, never zeroed by later zero-call export.

## File storage, download and history

Existing Storage encrypts candidates/final sections at `org/{org_id}/export-candidates/{run_id}/{attempt_id}/{sha256}.docx` / `org/{org_id}/exports/{export_id}/{sha256}.docx`, never overwrite. Validation hashes plaintext bytes; encryption binds full org/object key and random ciphertext nonces do not affect plaintext DOCX hashes. Stage only in private 0700 directories/0600 files; completion/failure cleans this operation's temporary files.

Release reads/verifies candidates, stores identical plaintext at final keys, then short-transaction rechecks/exports/audit. Unverified descriptors cannot become publications. DB/storage are not distributed transactions: failure may leave encrypted unreferenced objects, never downloadable partial publication. Automatic GC/history deletion/key rotation is excluded; rollback does not delete old materials.

Existing 300-second signatures bind org/export_id/file_sha256/dedicated kind. Link issuance/actual download require current human identity/membership/role/source permissions/live gates. Old links cannot bypass disabled members/reopened cards/template-material replacement/invalidation. Verify decrypted length/hash before streaming. History/original files remain immutable, but validity=stale rejects reissuance/download even previously signed. No --allow-stale; downloaded local copies cannot be recalled.

CLI accepts matching same-service relative routes only, rejects redirects, bounds streams/verifies SHA-256/DOCX, fsyncs private temporaries then atomically writes new 0600 paths. Reject existing outputs/symlinks/path races; failure cleans only own temporaries. Ordinary logs contain no signed URLs/keys/server paths; no permanent public API addresses. Local PostgreSQL/org-prefix/encrypted storage likewise prevents direct-object permission bypass; editing downloaded Word files never changes server publications.

History binds task/draft/template selection-revision/binding/input-manifest-file hashes/mode/publisher-time/current invalidation. Repeated release returns same export while gates/permissions pass without duplicate success audit. Link requests/server delivery attempts separately audit. Server transmission does not prove local save; CLI reports download only after hash-verified persistence.

## Reproducibility and audit

Identical same-org fixed inputs/mode/mapping/renderer_profile should produce identical **plaintext DOCX SHA-256**. input_hash uses canonical UTF-8 JSON with explicit defaults/fixed field-order rules/stable arrays/UTC/lowercase hashes, sort_keys/compact separators/ensure_ascii=true/no trailing newline. manifest_hash additionally binds human acknowledgments; reconstruction uses actual fixed manifests, not new current queries.

Fix ZIP order/timestamps/compression, OOXML serialization/bookmark-relationship-media numbers/core properties/dependency versions; remove unrelated machine usernames/metadata. Do not embed random job/attempt/export IDs/wall clocks/publisher times; those remain history/audit. Evidence confirmation identity/time is fixed input retained in provenance. Filenames do not participate in DOCX hash; modes/visible review markers do.

Org-only render cache keys include all dependencies/profile; run idempotency adds initiator so invalid authorization cannot transfer. Same submissions reuse own run/jobs; failed/cancelled needs explicit retry. Changed contents need new preflight, never retry updating old manifests. Different people/runs may yield same bytes while independently obtaining human release authorization. Unselected library revisions do not change hashes.

No byte-identity promise across renderer/dependency versions/templates/layouts; new profiles change keys/retain old records. Same-profile mismatch is export_nondeterministic, stops release/retains originals; matching unpacked text does not prove equal hashes. Word/WPS resaves/contents updates/human edits change hashes without server reproducibility failure. No identical display/print pagination across environments.

Reuse audit_logs with export.binding_created/prepared/render_completed/failed/cancelled/released/download_link_issued/download_served. Record actual actor_kind/user-token-job/org/task-draft-binding-run-export IDs/input-file hashes/mode/reasons/correlation. Worker renders are not human releases. Success/audit share transactions; denial/conflict/failure leaves no success event, and necessary denial events use safe redacted metadata.

Audit/logs/errors/usage do not copy tender text/response-material excerpts/quoted prices/identity-card-bank numbers/file contents/credentials/signatures. Bodies remain authorized drafts/fixed manifests/encrypted files; loosened logging cannot substitute audit. Release logs retain gate results/issue IDs, not sensitive handling text.

## End-to-end acceptance after approval

Order: contract/schema/identity → migrations/DB gates → template adaptation → fixed manifests/worker → human release/download → checks below. Implemented code does not mean all acceptance passed; pending database/isolated S3/Word-WPS scope is stated at the start. Logs/synthetic artifacts stay worktree data/work/, not docs.

1. **Actual entrypoint chain**: isolated two-human-domain accounts upload synthetic tenders/fix resources/confirm per card/draft/bind/prepare/actual worker/release/download across both CLI/API modes. Word/WPS opens editable table text/cells/separate evidence images; complete three-class coverage/source order matches draft. Reopen downloaded DOCX/check body-media relationships, beyond HTTP 200.
2. **Human identity/SQL gates**: bidder succeeds, admin/technical/viewer release rejected per role recommendation. token/agent/worker submission/release/download/generic-job bypass attempts fail. Runtime DB transactions with forged publishers/exports/prohibited-token scopes/missing actor fail. Workers leave candidates only, never spontaneous release; --wait yields no exports/download link.
3. **Two-org integrity**: full A/B chains cover every new table/route/candidate/history/signature/job/object prefix. Cross-org/unknown 404, no-context DB has no access. Mixed same-org task/job/card/template revisions/cross-org composite insertions fail, beyond list checks.
4. **Gate matrix**: actual-interface gaps/invalid citations/unconfirmed Evidence/stale draft/unacknowledged warnings. Final rejects, review allows only specified gaps with persistent visible markers/code 5. Unpack to prove absence of candidate text/media. All-comply/all-gap/valid-negative cases are correct; negative remains explicit even final. Invalid inputs create no exports/success audit.
5. **Binding/leakage**: two revisions/mappings, fixed old selection never follows current. Missing/duplicate/misplaced/split-run markers/unknown styles/missing four columns/static business text/hidden text/revisions/external links/OLE/media. Supported renders work, unsupported explicitly reject without external access.
6. **Certificate attachments**: confirm only specified synthetic PDF pages; output only them. Shared-page Evidence deduplicates images/retains every reference/confirmation. Extract all DOCX media/compare source PNG hashes, original SHA/pages/full edges/rotation/outside labels/bookmarks. No unconfirmed scans/no-file revisions/full-PDF-OLE/guessed pages/placeholders/removed watermarks. Damaged materials fail both modes.
7. **Concurrency/revocation**: replace selections/reopen cards/repair citations/disable members at queue/render/save/release/link stages. Old runs/signatures reject, reselecting old resources does not restore authorization. Concurrent release yields one exports/success audit; old/cancelled attempts cannot persist/overwrite new ones.
8. **Storage/download**: complete local-encrypted/isolated-S3 downloads check no plaintext ciphertext, failed cross-org copies, 300-second expiry, wrong kind/hash, interruptions/redirects/existing paths/symlinks/races. Final 0600 files appear only after verification; failures retain old files without partial downloads.
9. **Idempotency/reproducibility/cost**: repeats/independent same-profile renders have equal plaintext SHA. Mode/mapping/confirmation-revision changes alter inputs; upgrades never reuse old caches. dry-run zero writes, entire export zero Provider/UsageRecord/model debit, prior drafting costs retained.
10. **Scale/failure artifacts**: labeled synthetic long responses/over 125 confirmed pages test pagination/contents-bookmarks/four-column tables/readability and approved page-byte-memory-deadline limits. Overflow/storage-DB failures yield no publications/silent omission. Real 663-page bids are not authorized test sets by default; CI has no real external calls, real-material checks need explicit authorization.
11. **Contracts/repeatable delivery**: seven keys/0-2-3-4-5/schema/mode parity across chains. Save rerun instructions/redacted commands-JSON/input manifests-hashes/synthetic DOCX/media hashes/opened-file records in independent acceptance directories, not docs logs/screenshots/evidence. Explicit synthetic labels, no real tender text/tokens/signed URLs. Plans/worker state do not replace actual downloads.

## Provenance manifest

GET /exports/{id}/provenance (`bid export provenance --id ID --json`) returns published-file machine-readable provenance: SHA-256/mode/render profile/input-manifest hashes/publisher-time, plus each row/comply-only/gap's table/ordinal/requirement-card revision/confirmer-time/Evidence ID/selection-resource revision/attachment number/original page/original-PNG hashes. Same permissions as download, read-only/no links, fixed-manifest contents numbered consistently with Word.

## Starter template

`bid export template-sample --output NEW.docx --json` locally writes the built-in standard template: A4, 2.54 cm top/bottom and 3.18 cm side margins, 宋体 小四 body, 黑体 三号 headings, TableGrid tables, centered footer page numbers, six ordered anchors/no other static body. data.binding_sections supplies directly usable styles/recommended widths (ordinal 6%, requirement 36%, response 42%, compliance 16%). Orgs may upload unchanged or style-edit their templates.

## Explicitly out of scope

- Automatic whole-bid assembly/bid-authorization letters/164-page solution drafting/price strategy/automatic commitments-response rewriting/semantic check-score/missing-requirement entry/multiple extraction-document-package merging.
- Vendor (厂家)/web evidence generation-capture/new screenshots-diagrams/prototypes/Rust annotations/OCR-transcription/new contract-award-social-insurance types or packaging unconfirmed materials as “已附证明”.
- Arbitrary Word matching/public-template sharing/DOCM/PDF final export/full-original-PDF concatenation/final-page guarantees/automatic compliance-seal verification/multiple signers/bulk automatic release.
- Electronic/handwritten signatures/page-by-page-cross-page seals/government clients/encrypted bid packages/platform upload-submission/API integration. Humans merge full bids/check layouts/sign/upload after export.
- Vue dashboard/role-membership management/automatic expired-file reissue/export charges/history deletion-object cleanup/key rotation/production deployment; reupload/reapproval of local Word edits needs separate contract.

## Decisions

All items were approved as recommended.

| Decision | Options and recommendation |
| --- | --- |
| Gaps | **Recommend zero-gap final sections plus explicit review_copy**, valid confirmed content/gap metadata only, persistent “不得提交”/code 5; alternative reject every gap initially/no review. Both reject stale/bad citations/unconfirmed row Evidence |
| Export authority | **Human bidder only**, bidder release review domain (职责); alternative human admin+bidder. Both deny technical/viewer/token/agent/worker; template maintenance is not release authorization |
| Human/worker split | **prepare candidate then separate human release**, service/DB prohibit worker publication; alternative initial human request waits/renders/releases while human request remains valid, disconnect requires new request/no automatic background release |
| Templates | **Six fixed body markers/explicit column-style bindings/restricted nonbusiness templates**, incompatible templates get new revisions; alternative separate arbitrary-table-bookmark contract with structure/roundtrip acceptance |
| Certificate layout/clarity | **Verified 150 dpi full-page PNG, one appendix page each/dedup**, minimal confirmation-chain change; alternative higher-resolution versioned rendering/hash contract first or separate inline layouts. No default whole-PDF inclusion |
| Stale history downloads | **Metadata readable/files cannot redownload/old signatures rejected**; alternative future human archive-only capability with explicit markers/permissions. No allow-stale here |
| First-version maxima | **2,000 requirements/300 unique pages/512 MiB DOCX/1 GiB unpack-process/15-minute deadline**, adopted after synthetic-scale acceptance; alternative 1,000/150/256 MiB/10 minutes for smaller delivery. Neither silently trims materials |

## Details resolved during implementation

- Static-title allowlists use selected TemplateRevision.data.chapters' complete tree without free-body request fields; missing anchors/styles rejects bindings.
- input_hash fixes facts/issues/render profile; manifest_hash also exact acknowledgments; initiator joins run idempotency. Declarations have indexes only, confirmed certificate pages receive attachment numbers; integrated image attachments follow their own confirmed-Evidence path above.
- Invalidated published runs return state=invalidated with existing export_id/candidate_sha256=null; history reads succeed, issuance/download reject.
- CLI reads ExportView before signatures and checks identical descriptors. Review downloads use ExportDownloadResult adding mode/completion/validity/issues without seven-key changes.
- bid schema adds output JSON Schema for new export commands only, retaining existing definitions.
- Linux bounds process address space; macOS parents sample RSS/terminate render processes. Every limit fails without omission/downsampling, deployment lowers maxima only.
- Existing material tables retain org RLS; trusted fixed-input loading services narrow worker per-material reads. New export tables separately have run/attempt worker RLS, without claiming old tables acquired it.
