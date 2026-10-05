---
kind: plan
status: "Implemented (U1, U2, U3)"
---

# Contract: org tender-task and response-review console

Status: **U1, U2, and U3 implemented**; paid drafting binds preview hashes/spending caps under [drafting-binding.md](drafting-binding.md). Implementation is explained in [org-console.md](../notes/org-console.md). Maintainers run database/real-API Playwright acceptance; large-list performance still needs verification there. Implementation status is not acceptance success. Scope corresponds to [roadmap](roadmap.md) B02/U01/U02, connecting tasks, parsing, extraction, response-card (响应卡) review, and deviation (偏离) table drafts in the web Vue 3 application. This page defines connections to existing business contracts, never permission to rewrite card rules/expand materials.

## Goals, basis, and boundaries

Flow: org (organization/tenant; 单位) login → create task/upload tender documents (招标文件) → track parsing → select official reasoning level/extract → select extraction history → review cards/real materials against originals → human decisions/confirmation → view three tables/gaps (缺口). Model drafting uses [model drafting](../notes/model-drafting-redaction.md), showing fee previews before calls. “处理完成” means processing the selected extraction set, not complete bid (标书) delivery or omission-free tender extraction.

Rules belong to these sources, without separate confirmation/billing/outbound rules here:

| Basis | Usage here |
| --- | --- |
| [agent.md hard rules](../../agent.md#hard-rules-must-never-be-violated), [product design](../design.md) | Org isolation, real materials, human gates, token confirmation/export prohibitions, CLI Result |
| [ADR 0005](../adr/0005-human-confirmed-responses.md), [response-cards.md](../notes/response-cards.md), [model-drafting-redaction.md](../notes/model-drafting-redaction.md) | Adopted human-confirmation decisions/card mechanisms; delivered model drafting/redaction |
| [reasoning-levels.md](../notes/reasoning-levels.md), [docx-citations.md](../notes/docx-citations.md) | Official levels, independent extraction sets, verbatim citations, structural Word locations |
| [platform-console.md](../notes/platform-console.md), [api.js](../../web/src/api.js), [router.js](../../web/src/router.js), [App.vue](../../web/src/App.vue) | Separate platform/org sessions, same-origin requests/navigation/existing org billing |
| [provider-config.md](provider-config.md), [annotation.md](annotation.md) | Independent contract boundaries; schema/planning alone never proves org-owned models/annotation/derived images are usable evidence (证据) |

Interface evidence is limited to this checkout's [tenders.py](../../server/app/api/tenders.py), [resources.py](../../server/app/api/resources.py), [jobs.py](../../server/app/api/jobs.py), [response_cards.py](../../server/app/api/response_cards.py), [CLI registry](../../cli/bid_cli/schema.py), and approved contracts above. Old roadmap/card-plan status wording cannot replace entry-point checks. Drafting schemas/reserved tables alone do not establish route availability. Second-stage work from another checkout must be verified at integration, never assumed locally present.

### Page constraints from actual bid scale

The product-supplied winning-bid sample is a 663-page PDF: bid letter/authorization, substantive-response summary, commercial/technical response deviation tables, approximately 305 pages of itemized technical responses, approximately 217 screenshots/diagrams, approximately 125 whole-page scans, contracts/award notices as performance evidence, social-security/certificate team materials, and a 164-page technical proposal; almost every page also bears an electronic seal. These illustrate workload, are not additive chapter budgets, and do not claim sample-content verification here.

- Operate per requirement→response (响应)→material→human decision, loading previews on demand, never entire long PDFs/all images into pages/model input.
- Contracts/reports/screenshots/team attachments/long proposals have different chains. Initially select only supported pinned resource fields/certificate PDF pages; unsupported materials remain explicit gaps, never mislabeled certificates/declarations/placeholder images.
- Three draft tables are part of a bid only; letters/authorization/full proposals/layout/attachment assembly are not complete. Government electronic-bidding clients handle seals; this console neither connects that platform nor signs/encrypts uploads.

## Pages and roles

Reuse [style.css](../../web/src/style.css) base styles/native forms, adding task navigation in org workspaces with separate platform areas. These Vue browser routes are **not new API routes**:

| Browser entry | Responsibility |
| --- | --- |
| `/app/org/login` | Existing org login/selection |
| `/app/org/tasks` | Org tasks/create entry; all business roles enter after login |
| `/app/org/tasks/:taskId` | Upload/parsing/extraction/history, server-retrievable information only |
| `/app/org/tasks/:taskId/review?job=J` | Pinned extraction requirements/single-card edit-review; optional requirement ID location |
| `/app/org/tasks/:taskId/drafts?job=J` | Assembly preview/history/three response tables/comply-only list/gaps |
| `/app/org/billing` | Existing balance/recharge, admin only |

Guards obtain role from GET /org/current, never emails/cached buttons/platform identities. [ROLE_SCOPES / Identity](../../server/app/services/auth.py) and card-service human checks define authorization; buttons only guide interaction, with server reauthorization on every request.

| Identity | Visible content/nondecision operations | Human decisions |
| --- | --- | --- |
| admin | Org tasks/requirements/materials/cards/drafts; create/upload/parse/extract/edit/submit/draft/assemble; balance/redaction settings | Classify unclassified drafts with reasons; no cross-domain confirm/reject/material requests/reopen/batches |
| bidder | Org content and task creation/business operations; no balance/model-key view | Confirm/reject/request materials/reopen/disposition within commercial/qualification (资格) domain |
| technical | Org content; upload/parse/extract/edit/submit/draft/assemble existing tasks; no task creation | Same human actions within technical domain |
| viewer | Read-only requirements/originals/authorized materials/cards-revisions/jobs/drafts | No edit/run/confirm/batch/comment entry |
| Platform operator | Existing platform pages, no org materials/tasks | Platform session cannot enter org business; people with Membership log in separately |
| API token / agent / worker | No Web login; CLI/API intersect existing scopes/valid member grants | Never confirm/reject/material decisions/classify/disposition/reopen/export or simulate human checks |

“待我审阅” derives from current role/review_domain, without fictitious task owners/member ACLs/countersigning. Unknown domains display “待单位管理员分类”; table names/star marks/model suggestions never grant authority.

## Tasks, parsing, and extraction

### Creation and recovery

Forms reuse TaskCreate: name required, reference/timezone-aware deadline/budget optional. Budget is recorded only, not an enforced task-cost cap. Combined resource creation/member management are excluded. Call existing create/upload/parse APIs sequentially, retaining task/document/job IDs. Upload is not parsing completion; upload failure after creation never creates another task. Reselecting identical real files uses duplicate receipts; failed parsing retries original documents explicitly, never successful placeholders.

Upload uses FormData file, not JSON/manual multipart boundaries. Show file-type/size errors/server limits. Without progress events show in-progress, never invented percentages. Parsing shows queued/running/succeeded/failed/cancelled; unknown totals get no estimated progress bars.

Refresh recovers current-tab IDs then reauthorizes reads. Across devices, [G1 recovery reads](../notes/org-console.md#recovery-reads) discover task documents/parsing jobs; unextracted documents come from document lists, not extraction history/browser-cache guesses.

### Reasoning levels and extraction history

Before extraction forms call POST /documents/{D}/extract with JobAction(dry_run=True). Build selectors from data.reasoning_levels name/label/default and submit official name, without hardcoded “快速/均衡/深度” mapping or platform-catalog reads for org data. Empty catalogs show “此模型未提供可选推理档位”; send no invented values. Omitted reasoning uses server defaults. unsupported_reasoning refreshes preview/requires selection, never silent downgrade.

Preview parsed is not a completion promise; execution checks parsed document/server conditions. Unknown estimates show “费用暂不可估” with warnings; absent model/prices/duration remain unknown. Clearly distinguish “预检” and “开始抽取（可能产生费用）”; opening/filtering never triggers paid calls.

History shows job/document/official level/model/state/start-end/saved-rejected counts/tokens/errors/latest, with unknown missing values. Explicitly select successful jobs in page context. Failed/running jobs allow status/errors, not card scope. Reextraction retains old cards under old jobs without migration/merging/latest navigation. Default req list multi-document output cannot be the review set.

Requirement rows show category/star/description/full source.quote/location links. Description, raw model_quote, and verifiable quote display separately; only source.quote is tender original, never cleaned/summarized. PDF labels use filename/page N; Word filename/Location.label/section path with page=null, no guessed layout pages. Details compare authorized chunks/originals; raw model quotes collapse by default as traceability only. Rejected entries/parsing warnings/gap_fill.remaining display separately and never count as saved requirements.

## Single-card review, materials, and batches

### Single-card actions

Desktop uses requirement list/detail, ordered original→materials→response text→human actions; narrow screens use sequential blocks. CardSlot missing_card still occupies one requirement/progress slot. State/eligibility/disposition/model suggestions remain separate under [states/domains](../notes/response-cards.md#how-it-works).

- Edits submit CardCreate/CardUpdate; save and submit-for-review are distinct. Pending cards require withdrawal, confirmed cards domain-authorized reopen, with history reasons. Normal edit/generation never overwrites pending/confirmed/comply_only.
- evidence uses verbatim pinned fields/existing certificate-page sources; commitment must have empty Evidence and show “承诺”. Switching kinds first shows removed-material impact, never silently discarding edits. Both require text/deviation/notes; negative deviation (负偏离) stays visible, never auto-converted to no deviation (无偏离).
- Before confirmation, individually check linked Evidence/pages/warnings, without defaults/select-all. reviewed_evidence_ids exactly covers current evidence, each warning acknowledged with reasons; commitment review set is empty. proof_material_required never disappears on “我方承诺”.
- reject/needs-material/withdraw/reopen require nonempty reasons. review_hint=needs_material is model/service guidance, never a completed human decision. Confirmer/time are read-only authenticated fields.
- Every write supplies expected_revision. On 409 show server/local-unsaved differences for human resolution, never auto-merge/resend confirmation/reuse old checks. stale_material/invalid_citation/needs_reconfirmation from others' changes clears review checks and requires reinspection.

### Material selection and preview

Selectors consume current task-pinned products/features/certificates/profiles, certificate-files, evidence-sources only. Latest library revisions never replace pinned versions. Show selection/revision IDs, material nature, active-selection status, certificate date warnings. Allowed Evidence kind/field_path follows [RESOURCE_FIELD_PATHS](../../server/app/schemas/response_card_contracts.py); arbitrary pasted paths/URLs cannot fabricate evidence.

Existing resources pin through selection APIs; explicit replacement shows invalidation impacts. No full resource CRUD here; absent real materials remain gaps with CLI maintenance links. Product URLs/feature implemented flags/performance text are declarations, not captured pages/contracts. Whole certificate-page sources stay unconfirmed_source; Evidence confirmation never rewrites source archive status. Textless scans are viewable, but exact-excerpt chains cannot create confirmable page Evidence; no silent OCR/image sends/fake text proof.

First obtain authorized short-lived links, then bytes with org Bearer/X-Org-Id; signatures alone are insufficient. Signed URLs cannot enter unauthenticated `<img>`/iframe and session tokens never enter URLs. Initial PNGs use type-verified in-memory data:image/png under CSP, loading only opened materials and releasing on close/logout/org switch. Authorized original-PDF downloads allow human page comparison; base tender comparison uses chunks/structural locations. Online original page previews are additionally implemented through DocumentPreview.vue and authenticated document-page APIs; their implementation does not authorize arbitrary attachment rendering/OCR. Word remains structural, not a promised layout preview. Future blob viewers require separate minimal CSP review, without relaxed script/cross-origin/frame limits.

### Batch comply-only decisions

Batch entry makes disposition decisions, mainly comply_only, never batch response/evidence confirmation. Show selected requirements/domains/originals/expected revisions/per-item reasons; missing-card revision is null. Submit same-task/same-extraction authorized sets only, within DispositionBatch limits. Pending/confirmed items require withdrawal/reopen first.

Use existing whole-batch atomic transactions: any authorization/staleness/revision failure rejects all, retaining selection/showing reread scope. Browsers never split batches into automatic write loops. Reverting comply_only→respond is human-only; model suggestions auto-select nothing, with dispositions/confirmations counted separately.

## Model drafting, assembly, and fees

Model-drafting preview uses registered second-stage endpoints. Approved G3-B binding is implemented by [drafting-binding.md](drafting-binding.md): paid runs require a valid preview, input hash, explicit platform spending cap, and human outbound authorization. No production mock candidates. Select pinned job/all or explicit requirement IDs/official level. Levels reuse extraction-preview catalogs; generation preview verifies actual reasoning, reloading rather than changing selections on mismatch.

CardGeneratePreview shows selected/skipped counts, model/catalog revision, reasoning, input refs, redaction state/hit counts, estimated tokens/vendor USD/platform charge/currency/basis/duration. No outbound bodies/preredaction values in summaries. Unknowns are null, never 0/free; wrapper dry-run cost is actual zero preview cost, not execution estimates. Redaction defaults on; read task-list settings revisions, admin humans alone change it. Disabled redaction explicitly warns which unredacted text will be sent.

Humans confirm this paid operation/outbound summary before execution; **this confirms no response or material**. Options/material/settings changes require a new preview. Previews reserve no budgets/lock no prices and are not final authorization. CardGenerateRequest now accepts optional expected_input_hash/max_charge for compatibility, with both required by the console. Submission recomputes bound input/model/price identity and rejects changed hashes; per-job platform charges follow the approved cap. BYOK vendor bills are not bounded by platform caps. Outbound scope/four redaction classes/dual verbatim citation checks/worker gates reuse [model drafting](../notes/model-drafting-redaction.md); frontends do no separate redaction/vendor calls.

After generation refresh affected cards, highlighting new revisions/rejected citations/protected skips/material needs. Results remain draft, without auto-submit/disposition adoption/review checkmarks.

Assembly independently previews DraftRequest(dry_run=True), then submits, showing rows/comply_only/gaps/negative deviations. Assembly calls no model/OCR; zero current fees cannot hide previous generation fees. Show substantive (实质性条款), commercial (商务), and technical (技术) tables separately from comply-only (仅需遵守)/gaps, exactly one partition per requirement. Valid negative deviations are not gaps and remain prominent. Gaps show only original/location/reason/review links, never unconfirmed candidate text as response rows.

Drafts show status=draft/completion/validity/input job/affected requirements. Stale drafts retain snapshots with re-review/assembly notices; old complete badges cannot imply delivery readiness. This contract's base scope has no export button; later export integration follows its own contract.

### Jobs, cancellation, and cost records

Parsing/extraction/generation/assembly use existing jobs. Poll GET /jobs/{id}, suggested foreground interval 2 seconds, backoff to 30 on repeated errors, honoring Retry-After. Background tabs pause high-frequency polling, requery on return, stop at terminal. No assumed SSE without routes. Show actual attempts/reasoning/errors/results, no invented percentages.

Cancel uses existing APIs; acceptance does not mean sent vendor calls cost nothing. Incurred usage settles. Disconnect/wait timeout neither auto-cancels nor retries paid writes. Explicit parse/extract/assembly retry reuses existing parameters; paid drafting follows G3-B preview/hash/cap binding, without automatic retry. Job/attempt isolation/cache follows [background-jobs.md](../notes/background-jobs.md)/second-stage drafting contracts.

Costs use existing job-result/UsageRecord accounting, separating Cost.usd/platform charge. Query-wrapper zero cost is not total job cost; show returned amounts only, otherwise unknown. [Prepaid billing](../notes/prepaid-billing.md) owns balance/per-call admission/reservations/cross-attempt caps/idempotent settlement. Insufficient funds stops new calls/contact admin; nonadmins never query operator APIs for balances. Task/monthly budgets/refunds/org usage-detail pages are excluded; missing interfaces remain G5.

## Data models and Pydantic boundaries

### Persistence and isolation

**No new business tables/migrations are recommended for this console**: pages project existing entities without duplicate Web cards/approval truth.

| Data | Authoritative model/usage |
| --- | --- |
| User/Membership, Task/Document/Chunk/Requirement/Job | [entities.py](../../server/app/models/entities.py): identities/tasks/originals/extraction scope |
| Pinned task resources/certificate originals/EvidenceSource | Resource schemas/[source archives](../notes/unconfirmed-evidence-sources.md): candidates/real previews |
| response_cards/response_card_revisions/evidence/card_evidence_links | [response_cards.py](../../server/app/models/response_cards.py): current pointers/immutable revisions/human-confirmed materials |
| draft_runs/response_items | Same model file: draft snapshots/response-comply-only-gap rows |
| card_generation_runs | Card-generation model/second-stage contract ownership, not frontend table creation/model-result injection |
| audit_logs/usage_records/vendor_calls/balance ledger | Existing business audit/billing, no duplicate frontend truth |

Filters/selections live in page memory. Recoverable org/task/job/requirement IDs/page/nontext filters use current-tab org-namespaced storage, cleared on switch. Search text/response edits/material bytes/signed links never enter URL/localStorage/IndexedDB/persistent caches; navigation prompts about unsaved edits, without hidden disk saves.

Future server-side review positions/preferences require separate approved tables/interfaces, never implied by UI work. **Every new business table** requires org_id NOT NULL/UNIQUE(org_id,id)/ENABLE-FORCE RLS and same-change two-org acceptance. Parents use org composite FKs, with task/job/card as needed; people reference org Membership keys. No unconstrained resource_id/frontend-only filtering/new BYPASSRLS exceptions. Object keys retain org/{org_id}/; workers restore context, missing contexts fail.

### Shared inputs and views

Inputs reuse Pydantic Contract(extra=forbid), without writable org/actor/confirmer/time/permission/server-state fields. Single sources:

| Operation | Pydantic contract |
| --- | --- |
| Login/create/parse-extract/citations/Result | [contracts.py](../../server/app/schemas/contracts.py): Login, TaskCreate, JobAction, Source/Location, Cost/Result |
| Card edit/actions/classify/batches | [response_card_contracts.py](../../server/app/schemas/response_card_contracts.py): CardCreate/CardUpdate/CardAction/CardClassify/DispositionBatch |
| Card list/details/materials/drafts | Same file: CardSlot/CardView/EvidenceInput-EvidenceView/DraftRequest-Preview-View-Summary, retaining needs_reconfirmation |
| Generation/redaction | Same file/second-stage contracts: CardGenerateRequest-Preview-Result/TaskRedactionSet-View; schema alone never proves route registration |
| Task materials | TaskProductSelection in [resource_contracts.py](../../server/app/schemas/resource_contracts.py), TaskFeatureSelection in [feature_contracts.py](../../server/app/schemas/feature_contracts.py), TaskCertificateSelection in [certificate_contracts.py](../../server/app/schemas/certificate_contracts.py), TaskOrgProfileSelection in [profile_contracts.py](../../server/app/schemas/profile_contracts.py) |
| Certificate-page sources | [evidence_source_contracts.py](../../server/app/schemas/evidence_source_contracts.py): EvidenceSourceCreate/EvidenceSourceArchive |

Proposed frontend-adapter shapes follow. First two accurately describe existing extraction-preview data; last two define page state only, never API parameters or required server filter/pagination support:

```python
from typing import Literal
from uuid import UUID
from pydantic import Field
from app.schemas.contracts import Category, Contract
from app.schemas.response_card_contracts import CardState, Disposition, ReviewDomain


class ReasoningChoice(Contract):
    name: str
    label: str | None
    default: bool


class ExtractionPreviewData(Contract):
    dry_run: Literal[True]
    document_id: UUID
    parsed: bool
    estimated_cost_usd: float | None
    reasoning: str | None
    reasoning_levels: list[ReasoningChoice]


class ReviewContext(Contract):
    task_id: UUID
    extraction_job_id: UUID
    requirement_id: UUID | None = None


class ReviewListState(Contract):
    context: ReviewContext
    category: Category | None = None
    starred: bool | None = None
    state: CardState | Literal["missing_card"] | None = None
    review_domain: ReviewDomain | None = None
    disposition: Disposition | None = None
    only_mine: bool = False
    only_gaps: bool = False
    query: str = Field(default="", max_length=200)
    page: int = Field(default=1, ge=1)
    page_size: Literal[25, 50, 100] = 50
    selected_requirement_ids: list[UUID] = Field(default_factory=list, max_length=1000)
```

Adapter checks also require every CardSlot/Requirement to match selected task/job, unique selected IDs from loaded sets, no page-change writes, missing_card distinct from blank CardView, and saved revisions for confirmation/batches rather than list indexes/pages. Page state grants no permissions; servers revalidate relationships. No Web-specific Result wrapper; field changes enter original schemas/compatibility policy.

## CLI, API, and interface gaps

T/D/J/C/S/R mean task/document/extraction-job/card/source/certificate-revision IDs. Job status/cancel use submission job IDs, never extraction J for generation/assembly. Existing means verified API/CLI registration; agreed awaits second-stage delivery. All CLI supports --json, missing arguments fail, without implicit browser-only business commands.

### Task and extraction interfaces

| CLI / existing helper reads | API | Status/usage |
| --- | --- | --- |
| `bid auth orgs`, `bid login` | `POST /auth/orgs`, `POST /auth/login` | Existing org lookup/login |
| `bid org use ORG_ID` | `GET /org/current` | Existing selected-org context/role |
| `bid task create`, `bid task list` | `POST /tasks`, `GET /tasks` | Existing; lists not full details |
| `bid tender upload --task T --file FILE` | `POST /tasks/{T}/documents` | Existing multipart/document/duplicate |
| No registered CLI, helper reads | `GET /documents/{D}`, `GET /documents/{D}/chunks` | Existing status/original chunks |
| No registered CLI, original reads | `GET /documents/{D}/download-link`, `GET /documents/{D}/download?signature=...` | Existing authenticated binary downloads |
| `bid tender parse --document D [--dry-run] [--retry] [--wait]` | `POST /documents/{D}/parse` | Existing JobAction, no reasoning |
| `bid req extract --document D [--reasoning LEVEL] [--dry-run] [--retry] [--wait]` | `POST /documents/{D}/extract` | Existing official levels in dry-run |
| `bid req history --task T [--document D]` | `GET /tasks/{T}/extractions?document={D}` | Existing optional document filter |
| `bid req list --task T --job J` | `GET /tasks/{T}/requirements?job={J}` | Existing, explicit review job |
| `bid job status ID`, `bid job wait ID` | `GET /jobs/{id}` | Existing CLI polling, no wait route |
| `bid job cancel ID` | `POST /jobs/{id}/cancel` | Existing permission/terminal gates |

### Material, response, and draft interfaces

| CLI | API | Status/usage |
| --- | --- | --- |
| `bid task resource/feature/certificate/profile list --task T` | `GET /tasks/{T}/products`, `/features`, `/certificates`, `/profiles` | Existing, history for old selections |
| Corresponding `bid resource product/feature/certificate/profile list` | `GET /resources/products`, `/features`, `/certificates`, `/profiles` | Existing read-only library selection, service query parameters |
| `bid task resource/feature/certificate/profile add --task T --input FILE` | `POST /tasks/{T}/products`, `/features`, `/certificates`, `/profiles` | Existing selection/explicit replacement under role scopes |
| `bid task certificate file list --task T` | `GET /tasks/{T}/certificate-files` | Existing pinned originals |
| `bid resource certificate file download` | `GET /resources/certificates/revisions/{R}/file/download-link`, `GET /resources/certificates/revisions/{R}/file/download?signature=...` | Existing authorized original reads, not bid export |
| `bid evidence source add/list --task T` | `POST /tasks/{T}/evidence-sources`, `GET /tasks/{T}/evidence-sources` | Existing pinned certificate selection/real page |
| `bid evidence source download --id S` | `GET /evidence-sources/{S}/preview/download-link`, `GET /evidence-sources/{S}/preview/download?signature=...` | Existing archived PNG |
| `bid card list --task T --job J`, `bid card show --id C [--history]` | `GET /tasks/{T}/cards?job={J}`, `GET /cards/{C}?history=...` | Existing complete slots/immutable history |
| `bid card create/update ... --input FILE` | `POST /tasks/{T}/cards`, `PUT /cards/{C}` | Existing CardCreate/CardUpdate |
| `bid card classify --id C --input FILE` | `POST /cards/{C}/classification` | Existing human admin classification |
| `bid card disposition --task T --input FILE` | `POST /tasks/{T}/cards/dispositions` | Existing atomic DispositionBatch |
| `bid card submit/withdraw/confirm/reject/needs-material/reopen --id C --expected-revision N ...` | `POST /cards/{C}/actions` | Existing CardAction/action-review fields |
| `bid task redaction set --task T --input FILE` | `PUT /tasks/{T}/model-redaction` | Existing settings; outbound uses second-stage drafting |
| `bid card generate --task T --job J [--requirement ID ...] [--reasoning LEVEL] [--expect-input-hash HASH] [--max-charge AMOUNT] [--dry-run] [--wait]` | `POST /tasks/{T}/cards/generations` | Registered preview/paid run with implemented G3-B binding |
| `bid draft --task T --job J [--dry-run] [--retry] [--wait]` | `POST /tasks/{T}/drafts` | Existing assembly only |
| `bid draft list --task T --job J`, `bid draft show --id ID` | `GET /tasks/{T}/drafts?job={J}`, `GET /drafts/{id}` | Existing summaries/revalidated snapshots |
| `bid billing balance/redeem` | `GET /billing`, `POST /billing/redeem` | Existing admin pages |
| `bid schema --json` | No business API | Existing command/input verification, no invented schema route |

Family-path abbreviations expand using preceding prefixes. No independent Evidence confirmation endpoint: card actions confirm materials transactionally, no automatic confirmation button. Citation repair retains existing admin CLI flows; this page shows invalid_citation/needs_reconfirmation without automatic tender-original repair.

### Gaps that must remain explicit

| ID | Gap/impact | Contract handling |
| --- | --- | --- |
| G1 | Full task details/document/parsing-job discovery | Authorized three read-only interfaces close discovery; paths/projections/permissions in [recovery reads](../notes/org-console.md#recovery-reads), no migration/new scopes |
| G2 | Requirements/cards/most lists lack server filters/pagination/totals/cursors; card lists return full CardView | Real full sets/frontend pagination must pass large-list gates; otherwise approve summaries/pagination on existing reads, no invented page/q or truncation |
| G3 | Drafting preview/submission race; extraction previews do not pin model/price snapshots | Drafting hash/model/price/user cap binding is now implemented under drafting-binding.md. Extraction estimates remain advisory; stronger extraction binding needs a separate contract |
| G4 | Original base contract excluded tender page images/Word layout/arbitrary attachments; scans lack verified text | Original blocks/downloads/certificate PNG remain valid. Authenticated tender PDF page previews now exist via DocumentPreview.vue/tenders.py; this does not add Word layout/arbitrary attachments/OCR. New material/rendering capabilities require separate contracts |
| G5 | No org audit-query/full usage/SSE/enforced task budgets/session revocation-renewal | Existing revisions/results/polling; local logout is not server revocation; no platform API reuse/invented endpoints |

Frontend adapters support specified business partial success/multipart/authorized binary/Retry-After; rules in [request boundaries](../notes/org-console.md#session-and-request-boundaries). Unexpected server failures return Result internal_error ([handling results](../guides/cli.md#handle-results)), rendered as failure, never unknown-response empty-list fallback.

### Result and failure display

JSON keeps exactly seven top-level keys: ok/command/data/items/warnings/cost/duration_ms; errors data.error, lists items, details/receipts data. Successful binary downloads follow listed media types rather than Result parsing; errors follow error handling.

| CLI exit | Web behavior |
| --- | --- |
| 0 | Query/action/preview success or job accepted; only terminal results show execution complete |
| 2 | Input/level/transition/missing confirmations/revision conflicts: locate/reread, no auto-write retry |
| 3 | Retryable network/queue/rate-limit/wait timeout/pinned-input changes: retain IDs, honor Retry-After or repreview |
| 4 | Identity/permission/unauthorized resource/nonretryable vendor/material integrity/insufficient balance: stop explicitly, never downgrade to success |
| 5 | Assembly gaps/partial generation/later CLI combined-create failure: retain valid outputs/per-item reasons |

Handle HTTP status/exit separately, no top-level exit_code. HTTP 2xx/ok=false renders partial outputs only for explicitly contracted completion=partial in detail/job result; unknown shapes are contract errors. Jobs may succeeded with business partial; inspect parsing/extraction data.error/terminal state beyond ok. Rejected extraction entries do not automatically use generation partial semantics. 401 clears matching sessions/returns login; known org disablement stops requests; 403 is not session expiry. Missing/cross-org objects show “不可访问”; 404 leaks no existence.

## Sessions, CSRF, and audit

Keep separate sessionStorage bid.org.session/bid.platform.session. Org requests explicitly send current human Bearer/X-Org-Id only to same-origin allowlisted paths, never user-supplied server URLs. Authentication uses no automatic cookies; invent no CSRF cookies/endpoints/X-CSRF-Token. Retain required Authorization/org context, rejecting external forms/unauthenticated requests, without credentialed cross-origin access. Explicit credentials: "omit" avoids future cookie dependence. Cookie sessions require separately approved CSRF/SameSite/Origin/renewal contracts.

Login reuses existing lookup/login/rate limits; passwords live only in login forms and clear on success. Tokens never enter URL/logs/screenshots/errors. Logout clears org sessions/business memory/previews/requests, retaining platform separation; it does not claim server revocation. Org switches reverify Membership/role and discard old responses/materials/checks/edits/caches, beyond renamed headers. Server role changes apply immediately.

Retain same-origin CSP/no-referrer/nosniff in [CONSOLE_HEADERS](../../server/app/api/main.py). Render originals/model text/filenames as plain text, no v-html/third-party analytics reading materials. CSRF cannot substitute for XSS protection.

Backend audit_logs record card edits/actions/classification/dispositions/material selection-replacement/redaction/generation-assembly submissions/results. Frontends cannot write successful audits or treat clicks as completion. Atomic batches share IDs, with per-item revisions/audits; failures/conflicts create no success audits. Records contain identity/object-revision-job IDs/state-disposition changes/reason codes/time/hashes only. Reasons remain in authorized history; clauses/responses/model input-output/credentials/four sensitive classes never enter audits/ordinary logs. Org audit pages depend on G5; existing card history/jobs do not justify frontend telemetry as missing audit proof.

## Large lists and accessibility

Accept sets over 1,000 requirements with cards/multiple evidence items, beyond empty-card benchmarks. Fetch selected-job requirements/cards, join requirement IDs, paginate original server order, default 50/options 25/100. Complete-set counts identify selected jobs. Before full load, never show “0 项缺口” or allow whole-set batches; virtual scrolling cannot conceal truncation.

Filters: category/star/state-missing card/domain/disposition/invalid-gap/pending mine, plus requirement/quote search. Show matched/full counts; filtering never changes assembly scope. Full-draft negative-deviation counts/warnings stay visible without hide switches. Invalid pages clamp to valid pages; job changes clear filter-associated selections.

Distinguish current-page/all-matches selection; all-matches first shows fixed ID counts/domain restrictions for human selection. Over 1,000 requires narrower scope. Filter/scope changes clear batches with notices; hidden items cannot enter decisions silently. After review, offer next-pending-mine in original order, never auto-confirm next. Details reread revisions, writes update local lists, no all-card refetch per keystroke/all-image prefetch.

Prefer semantic tables/captions/headers/native buttons-checkboxes/labels, with textual states beyond red-green/stars. Add skip-main, visible focus, region titles/detail-return focus. Restrained aria-live for pagination/save/job states, no focus resets during polling. Field-associated errors, focus-trapped conflicts/decision dialogs returning focus. Originals/evidence images have readable locations/alternatives; OCR is not complete image replacement.

Keyboard supports filters/pages/cards/edits/individual checks/actions/next navigation without intercepting text shortcuts in inputs. Optional shortcuts are discoverable/disableable; confirm/reject/batch never executes with one key. At 200% zoom/narrow screens originals/actions stay visible; horizontal tables are keyboard-focusable; images zoom/return.

Suggested repeatable baseline: 1,200 entries, mount current page/one detail only, no image preloads. Loaded-data filter/page/next interactive p95 ≤300ms. Real API first-load interactive target ≤5 seconds; artifacts specify machine/browser/material sizes/request durations. If G2 full APIs miss gates, pause large-scale delivery/add summary-pagination contracts, never relax completeness.

## Suggested staged delivery

Implementation boundaries follow the table; real-API Playwright/performance acceptance remain separate.

| Stage | Deliverable | Dependencies/exit conditions |
| --- | --- | --- |
| U1 tasks/extraction (implemented) | Org navigation/roles/create-upload-parse/official levels/history/requirements-originals | Creation/known-ID/G1 discovery connected; maintainers run acceptance |
| U2 human review/drafts (implemented) | Pinned materials/evidence-commitment editing/domain decisions/atomic comply_only/three tables-comply-only-gaps/concurrency-invalidation | Existing card APIs; 1,200-entry/two-org-role end-to-end checks written, pending runs. Failed G2 requires pagination contract |
| U3 drafting integration (implemented) | Fee/outbound previews/official levels/redaction, paid runs with hash/cap/human authorization | Real second-stage APIs/G3-B request binding; disabled before preview/on invalidation or admission blockers |

Suggested sequence U1→U2→U3; another checkout's progress need not block the human flow. Extra G4/G5 capabilities stay in their contracts; pages do not implicitly authorize backend expansion.

## End-to-end acceptance

Playwright conditions follow; entry point [org-console.spec.js](../../web/e2e/org-console.spec.js). No real model calls. Use isolated PostgreSQL/API/real worker/storage/built Vue, with admin/bidder/technical/viewer in two orgs, platform identity/restricted token. CI fakes only external Providers with clearly labeled synthetic materials, never tested APIs/DB confirmation gates/job persistence. Authorized real samples have separate acceptance, without test-service uploads/public evidence copies.

1. **Entry/identity:** login→tasks, admin billing, technical no create, viewer read-only. Reject platform sessions/org APIs, changed browser roles/cross-org deep links/IDs/inactive Membership; separately verify 401/403/404/org_inactive/rate-limit countdown.
2. **Creation/recovery:** upload parseable real PDF/DOCX, worker parses, refresh rereads same objects. Retry only unfinished upload/dispatch stages without duplicate tasks; identical content shows duplicate. New browser sessions recover uploaded/unparsed/failed tasks via G1 without caches.
3. **Levels/history:** dry-run lists two configured official levels/default without jobs/usage writes. Different-level runs preserve independent sets, identical inputs cache. Empty/unknown/unavailable/rejected citations display honestly; reextraction preserves old cards. PDF page/Word label quotes match verbatim.
4. **Material boundaries:** pinned fields/textful certificate pages become Evidence, authenticated original preview allows checks. Reject expired/cross-org signatures-IDs/wrong pages-fields/inactive selections. Scans preview only, no fabricated text. Planned features cannot show implemented; URL/performance declarations cannot show captured pages/contracts.
5. **Human gates:** bidder commercial/technical technical/admin classify-settings only; evidence without materials cannot confirm, evidence-free commitments confirm by domain, negative deviations/proof warnings remain. Unchecked evidence/warnings block confirmation. Browser-associated API verifies token/worker cannot bypass decisions; evidence:confirm/export token requests reject; unconfirmed materials never assemble.
6. **Batches/concurrency:** competing same-revision edit-confirm permits one success; loser retains edits but clears review checks. Hundreds of comply_only items commit once; one unauthorized/conflicting item rolls all back. Persistent revisions/audits verify results. Success creates no confirmed responses; filter changes cannot submit hidden items.
7. **Assembly/invalidation:** UI preview/assembly partitions rows/comply_only/gaps exactly once across selected job. Three-table sources/originals/confirmed text match. HTTP 2xx/ok=false partial shows valid results/gaps; all-gap/all-comply-only/negative cases follow exit semantics. Reopen/material replacement/citation repair makes old drafts stale, not current; history/snapshots immutable.
8. **Second-stage integration:** unavailable endpoints produce no paid requests. Delivered UI previews/authorizes/runs with actual org-only pinned text, default redaction, no full PDFs/images/unselected materials. Usage/platform charges/retry-cache-cancel settlement match. Generation writes draft only, skips/fails changed inputs without overwriting confirmation. Unknown estimates display unknown; G3-B hash/cap checks use actual implemented boundaries, never claims to cap vendor bills.
9. **Large lists/accessibility:** 1,200 mixed states/long quotes/multiple materials allow filtering/cross-page selection/keyboard review/role switches/position recovery with complete statistics/performance/no image bulk loads. Locate by accessible names/roles; verify focus/field errors/announcements/200% zoom/narrow screens/no console errors.
10. **Isolation/CSRF/audit:** every read/write/preview/job endpoint tests A→B IDs/missing context and same-org cross-task/job/source joins. External forms/no Bearer cannot mutate; delayed old-org responses never render after switches. Restricted DB roles verify existing chains/future new FORCE RLS tables/composite FKs/A-B isolation. Atomic failures leave no success audits; logs no text/credentials.
11. **CLI/reproducible artifacts:** compare real CLI --json/API seven keys/0-2-3-4-5 on identical isolated data, distinguishing accepted/completed/partial. New artifacts/org-console/<run-id>/ or controlled temporary dirs contain sanitized result.json/assertions/synthetic IDs-hashes/performance/key screenshots/rerun arguments. Save command shapes/environment names only, no passwords/Authorization/Cookie/signed URLs/raw network traces; no docs evidence.

## Explicit exclusions

- Complete 663-page bid assembly, letters/authorization, 164-page proposal generation, Word/PDF exports/template adaptation in this base contract.
- Government electronic bidding, page seals/e-signature/encryption/bid upload/pricing strategy.
- Fabricated reports/certificates/performance/social-security/vendor pages/replacement screenshots; automatic/batch confirmation/token exports.
- New arbitrary contract/report/team libraries, web capture/Rust annotation/ui mock/cloud OCR/multimodal image sends.
- Requirement edits/manual additions/automatic citation repair/multidocument-extraction merges/scoring/full-text check/risk cards/comments.
- Complete resource management/org members/countersigning/task ACL/OIDC/server revocation/org-owned model-Provider configuration/memory/agent orchestration/SSE/new audit-usage queries/task budgets/payment extensions.
- Deciding missing API URLs/fields/migrations/persistent preferences here; separate contracts precede integration.

## Decisions

All adopted recommended options are approved.

| Decision | Options | Recommendation |
| --- | --- | --- |
| First delivery | A: U1/U2 human flow before second-stage integration; B: wait for generation together | **A**, existing business APIs validate review efficiency; U3 requires real endpoints |
| G1 discovery | A: limited creation/known-ID delivery, reads before full console; B: reads/contracts before U1 | **A**; later implementation authorization covered small G1 reads, closing discovery under [recovery reads](../notes/org-console.md#recovery-reads) |
| 1,000+ pagination | A: full APIs/frontend pagination, 1,200 acceptance; B: server pagination-summary-total contracts first | **A**, small cost/preserved APIs; failed performance immediately needs B, no truncation/lower completeness |
| Drafting fee binding | A: advisory estimates/explicit paid consent/current backend caps; B: input-model-price/user cap binding first | **B** remains approved and is implemented through drafting-binding.md. U3 requires bound preview/hash/cap/human outbound authorization before paid execution |
| Preview depth | A: original blocks/existing certificate PNG/authorized downloads; B: embedded PDF-Word positioning contracts first | **A** for this base scope, reusing bytes/CSP; later original PDF page previews follow their implementation, without arbitrary materials |
| Review position | A: current-tab IDs/nontext filters/no tables; B: server cross-device positions | **A**, fewer competing business states; B needs Pydantic/interfaces/org_id NOT NULL/FORCE RLS/composite FKs/two-org acceptance |
