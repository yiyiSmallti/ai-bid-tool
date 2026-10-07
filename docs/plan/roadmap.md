---
kind: plan
---

# Remaining scope and roadmap

This page lists unfinished parts of [the design](../design.md), open decisions and
suggested order. Shipped behavior is defined in the [mechanism notes](../README.md#mechanism-notes)
and their linked code; delivery history is in [changelog.md](../changelog.md).
The design's non-goals—fabricating materials, electronic bidding platform integration,
pricing strategy and developing models in-house—are excluded.

For new features, follow [the agent workflow](../../agent.md#workflow): submit
Pydantic models, Provider interfaces and CLI JSON structures for confirmation before implementation.
Terminology follows [the glossary](../glossary.md).

## Coverage matrix: foundations, permissions and data model

| ID | Current state | Gap (缺口) | Dependency |
| --- | --- | --- | --- |
| F01 Architecture | Server stack, real Procrastinate worker, Vue 3 + Element Plus console and [agent API/CLI/controller](../notes/builtin-agent.md#code) implemented | A01 database and queue acceptance; agent UI | [Agent contract](agent.md); separate UI contract |
| F02 Database isolation | Existing business tables have NOT NULL `org_id`, FORCE RLS and org (organization/tenant; 单位) composite foreign keys | Each new table and endpoint must include two-org and missing-context tests in the same change | Hard rule |
| F03 File isolation | Tender documents (招标文件), templates, original certificates (证书), source PNGs and export files use protected storage and signed downloads; [cloud annotation](../notes/annotation.md#how-it-works) adds immutable certificate-page candidates/releases and source-bound downloads | Database/isolation/download acceptance; vendor annotation sources; [org-profile and contract attachment archives](attachment-archive.md) | [Cloud annotation contract](annotation.md); [attachment archive contract](attachment-archive.md) approved, not implemented |
| F04 Accounts and roles | Global User, Membership, four roles; platform administrators with a configured allowlist, TOTP and operator console; human-only task discussion for active task readers | Org member management, OIDC and global memory (记忆) maintenance | New contracts; SSO requires authorization |
| F05 ApiToken | Issuance, scopes, expiration; database prohibits confirmation/export scopes | Revocation entry point, token listing, issuance and revocation audit | New interface confirmation |
| F06 Org/Task | Task metadata, [members, archival and requirement assignment](../notes/team-workflow.md), and [human-revised task budgets](../notes/task-budgets.md), enforced across calls, jobs and retries | Plans and monthly quotas; combined creation | [Approved budget contract](budget.md); subscription rules remain undecided |
| F07 Background jobs | Persistent parse/extract/card_generate/draft/provider_test/export_render/export_preview/sandbox/screenshot/prototype (原型) generation/vendor (厂家) search/simulated proposal (模拟拟投)/score_rubric/score/agent jobs, cancellation, bounded retries, leases and `run_id` overwrite protection | Jobs for other commands, automatic recovery of legacy jobs | New interface confirmation |
| F08 AuditLog | Resource changes, human response-card (响应卡) decisions, model drafting, scoring-rubric generation and human decisions, scoring execution, redaction (遮挡) settings and table assembly (组表); export events are defined in [human-section-exports.md](../notes/human-section-exports.md#how-it-works); [agent/token invocation provenance](../notes/builtin-agent.md#how-it-works) | Login, token and other configuration audit and queries | New contract |
| F09 UsageRecord | Per-call LLM/Vision/OCR/search/Browser accounting, task liability and prepaid reservation/settlement; durable low-balance notices | Storage metering; online payments | [Task budgets](../notes/task-budgets.md) |
| F10 Deployment and quality | Local migrations, Compose with SearXNG and Gotenberg, locked dependencies, GitHub Actions checks scoped to changes on every PR, parallel tests with a database per worker; dedicated platform credential roles and root rotation | [Production object storage, TLS, backups and release acceptance](production-deployment.md), [platform credential database acceptance and deployment cutover](platform-credentials.md), private-deployment packaging | Platform credential contract approved; deployment plan approved; environment decisions pending |

## Coverage matrix: resources and task selection

| ID | Current state | Gap | Dependency |
| --- | --- | --- | --- |
| R01 Template | Org-private DOCX, declared type and sections, immutable revisions, task pinning | Automatic section extraction, section matching, public sharing, export adaptation | New contract |
| R02 Product | Model and source URLs, revisions, task pinning; sandbox capture turns source URLs into vendor evidence, and vendor-search candidates can populate the library; demonstration simulated proposals enter products marked 【模拟】 and verbatim parameters from official vendor sites by procurement item; final sections (正式件) reject them; see [product-simulation.md](../notes/product-simulation.md) | Specification-file uploads, exact model matching and [typed fact bindings](parameter-assessment.md#deterministic-units-and-product-facts); simulated-proposal recall when official sites are unreachable or table rows lack names | New contract |
| R03 FeatureItem | Description, declared status, product association | Real feature screenshots, status verification, `ui mock` comparison, requirements-document import; [typed assessment of pinned features](parameter-assessment.md), approved, not implemented | File chain is independent; generation depends on the API |
| R04 Certificate | Declarations, date checks, original-file revisions: PDF or ordered composition of multiple images/PDFs, with image metadata removed; the console's “单位资料” (org profile; 单位资料) page manages certificates and flags expiry or expiry within 30 days | OCR, authenticity verification, expiry notifications | New contract |
| R05 OrgProfile | Text declarations, revisions, task pinning | [Immutable supporting documents and performance-contract attachments](attachment-archive.md), exact declaration links; authenticity verification and links to qualification (资格)/past-performance tables remain separate | [Attachment archive contract](attachment-archive.md) approved, not implemented |
| R06 TaskResource | Pinned selections for five resource types, explicit replacement and response (响应) dependency invalidation | [Explicit attachment selection and invalidation](attachment-archive.md#b05-source-binding-and-invalidation); configuration profiles, atomic selection on creation, dashboard change notifications | [Attachment archive contract](attachment-archive.md) approved, not implemented; other additions need new contracts |
| R07 Task creation | Creation and later selection both available | Combined creation with the design's `--template/--features/--certs/--providers` | New contract |

## Coverage matrix: parsing, requirements, evidence, responses and checking

| ID | Current state | Gap | Dependency |
| --- | --- | --- | --- |
| B01 tender parse | Per-page PDF text and local OCR of scanned pages; Word paragraphs and table cells parsed with structural citations | PDF paragraph/table/coordinate structures, OCR coordinate persistence; Word text boxes, headers and footers | Explain new major dependencies first |
| B02 req extract | Anthropic and OpenAI-compatible adapters, concurrent batches, per-citation verification, union with starred (★) mandatory clause (★条款) rules, cache, per-item rejection/reporting of invalid citations, official reasoning levels and extraction history; real-model end-to-end extraction from Word and public PDF tender documents; independent review, verified manual recovery and source-bound consumer gates | [Requirement confirmation, verified manual entry and acceptance gates](../notes/requirement-confirmation.md); database/browser integration acceptance | [Approved; first slice implemented](requirement-confirmation.md) |
| B03 Parameter assessment | `condition` is a free-form dict | Typed param/op/value/unit, deterministic versioned conversion, source-bound comparison and human handling of ambiguous expressions | [Parameter assessment contract](parameter-assessment.md), approved, not implemented |
| B04 evidence fetch | Pinned certificate PDF-page sources and response Evidence bindings; source archives remain unconfirmed; manual screenshot ingestion and `image_region` evidence; allowlisted sandbox capture of vendor web/PDF sources, with page images manually ingested as archive-bound vendor evidence; see [screenshot-evidence.md](../notes/screenshot-evidence.md#vendor-captures); explicit Perplexity Search API or self-hosted SearXNG selection finds vendor-source candidates, which enter the product library after human selection | Automatic satisfaction assessment | New contract |
| B05 evidence stamp | [Cloud certificate-page annotation](../notes/annotation.md): explicit preflight, Rust server jobs, immutable candidates, manual response-card attachment, exact-approval confirmed releases and draft/export bindings; existing screenshot profiles remain available | Database/browser integration acceptance; vendor rendition enablement; profile/contract sources wait for [attachment archives and their adapter](attachment-archive.md#b05-source-binding-and-invalidation) | [Approved certificate-page first slice implemented](annotation.md); real PostgreSQL/Chromium acceptance remains pending |
| B06 ui mock | LLM-generated single-page HTML prototypes, offline sandbox screenshots, ingestion and per-item keep/replace decisions; see [screenshot-evidence.md](../notes/screenshot-evidence.md) | Automatic software-response table layout | New contract |
| B07 Human confirmation (人工确认) | Human response-card/Evidence confirmation by review domain (职责), immutable revisions, atomic disposition, model proposals and [complete co-sign gates](../notes/team-workflow.md#how-it-works); independent [B02 requirement confirmation](../notes/requirement-confirmation.md); [B05 exact-approval release bindings](../notes/annotation.md#how-it-works) | Database/browser integration acceptance of requirement, Evidence, co-sign and annotation release invalidation | [ADR 0005](../adr/0005-human-confirmed-responses.md), [B02 contract](requirement-confirmation.md), [B05 contract](annotation.md) |
| B08 draft | Three human-confirmed response tables, exhaustive partition into comply-only (须遵守) and gaps, negative deviation (负偏离), and obsolete-draft invalidation; see [response-cards.md](../notes/response-cards.md) | Merging multiple documents/extraction jobs | New contract |
| B09 check | Rules and combined checking, bounded console preview/history/reports, recovered jobs, verified citation windows and responsible-human dismiss/reopen; see [check](../notes/check.md) and [console assessments](../notes/console-assessments.md) | Browser and database integration acceptance; real-model effectiveness and repeated-call variation under the [checking contract](check.md); [B03 negative-deviation integration](parameter-assessment.md#response-check-scoring-and-export-consumption), approved, not implemented | [Approved console implementation and acceptance limits](console-assessments.md#implementation-acceptance-note); controlled evaluation inputs and explicit calls |
| B10 score | Two-stage rubric generation and review with verified multi-source sections, whole-set confirmation, complete replacement, score preview and bounded advisory console reports; see [score](../notes/score.md) and [console assessments](../notes/console-assessments.md) | Browser and database integration acceptance; controlled real-Provider evaluation and manual score changes under the [scoring plan](score.md) | [Approved console implementation and acceptance limits](console-assessments.md#implementation-acceptance-note); separate contracts for additional scope |
| B11 export | Human export of Word response sections, final sections/review copies (审阅件), certificate-page attachments and audit; Gotenberg conversion to PDF for online page previews; see [human-section-exports.md](../notes/human-section-exports.md) and [page-previews.md](../notes/page-previews.md) | WPS visual pagination and isolated S3-download acceptance; see [export.md](export.md) | Approved |

## Coverage matrix: Providers, memory, dashboard, agent and CLI

| ID | Current state | Gap | Dependency |
| --- | --- | --- | --- |
| P01 LLMProvider | `extract`/`draft` protocols, two HTTP adapters, DisabledLLM and test doubles; structured JSON calls shared by drafting and simulated proposals; separate CheckProvider, RubricProvider and ScoreProvider with structured HTTP adapters; [agent decision adapter](../notes/builtin-agent.md#code) | Controlled real-Provider evaluation of the agent | Contract for the corresponding capability |
| P02 OCRProvider | Local Tesseract | Coordinate persistence, org-level language/switch settings, cloud OCR | Cloud services require authorization |
| P03 Vision/Search/Embedding/Browser | Multimodal screenshot matching, region suggestions and text reading; sandbox Browser offline rendering and vendor capture; Perplexity Search API or self-hosted SearXNG search | Later Embedding boundaries in [memory.md](memory.md#pgvector-and-later-indexing); local-browser capture, recall under search-engine rate limits with SearXNG alone; kernel-level egress filtering on proxy nodes, sandbox lease takeover, and disconnect/storage-failure injection acceptance | New contract |
| P04 ProviderConfig | Platform model catalog and billing; org BYOK and platform-model selection, `provider set/list/history/test`; [platform credential management and per-call resolution](../notes/platform-credentials.md); see [provider-config.md](../notes/provider-config.md) | [Platform credential database acceptance and cutover](platform-credentials.md); org configuration for vision, search and other capabilities | Platform credential contract approved; new contracts for other capabilities |
| P05 Common controls | Call admission, immediate accounting, deadlines, bounded retries, atomic extraction failure and partial drafting/scoring-rubric success; extraction, drafting, simulated proposals and rubric item batches run concurrently according to `BID_LLM_CONCURRENCY`; two-stage rubric request boundaries in [scoring plan](score.md#providers-jobs-and-prepaid-billing) | Cross-capability rate limits and unified progress | New contract |
| M01 Memory storage | Org candidates, human approval of exact revisions, immutable history, FORCE RLS and logical deletion | user/project/global scopes and their ACLs | [Decisions](memory.md#decisions) |
| M02 Memory retrieval | Org keyword/tag retrieval, rule/preference precedence, separate drafting context, per-call usage records | Embedding/hybrid retrieval, other consumers | [Phased scope](memory.md#goals-and-boundaries) |
| M03 Automatic candidates | Sanitized deterministic candidates and org evaluation samples from human reject/edit of model response cards; recovery jobs | LLM generalization, risk-card false positives, historical backfill | [Feedback contract](memory.md#automatic-candidates-samples-and-jobs) |
| U01 Dashboard | Org console task board, member management, bounded progress, lifecycle, assignment and review-policy controls; tender parsing/extraction, response review, drafts/exports, simulated proposals, org profiles, confidential fields and original-page previews; check/score workspaces and reports; product, feature, certificate, profile and template libraries with bounded browse/search, exact content revisions, lifecycle and explicit task pins; DOCX originals and human-reviewed export bindings; bounded org memory management and task-authorized saved feedback-job recovery; see [management pages](../notes/management-pages.md), [org console](../notes/org-console.md), [team workflow](../notes/team-workflow.md) and [console assessments](../notes/console-assessments.md) | [Later management-page slices](management-pages.md#first-vertical-slice), including the approved [attachment archive slice](attachment-archive.md#first-vertical-slice); integrated resource-library database/browser/scale acceptance, assessment and [B02 requirement-review](requirement-confirmation.md) browser/database integration acceptance | Existing console approved; B02 first slice implemented; [management pages](management-pages.md) approved; product, feature, certificate/profile, template and memory slices implemented; integrated acceptance pending |
| U02 Response cards/SSE | API/CLI response-card revisions and transitions; [durable task/job SSE, assignment, discussion and co-sign review](../notes/team-workflow.md); assessment domain decisions, citation/card deep links, recoverable polling and optional observed generation-stage progress | [Requirement-review buckets and durable invalidations](requirement-confirmation.md#dashboard-progress-and-console-outline); remaining dashboard interaction and integration acceptance | B02 first slice implemented; [assessment implementation](console-assessments.md) |
| A01 Built-in agent | API/CLI owner-only sessions, seven command tools, human pauses, persistent recovery, immutable session limits and no-memory generation; [mechanism](../notes/builtin-agent.md) | PostgreSQL/RLS, queue crash recovery, concurrent budget and full human-review workflow acceptance; Vue pages and later tools | [Approved contract and acceptance requirements](agent.md#acceptance-requirements); all recommended defaults adopted |
| A02 External agents | CLI, `bid schema`, scoped tokens and authenticated invocation/token-automation provenance; [shared contract](agent.md#audit-and-a02-provenance) | Database provenance acceptance, product-specific identity and optional `mcp serve` | Product identity and MCP require their own contract |
| C01 CLI contract | Seven-key Result, schema registration, uniform exit codes, two modes; [task workflow, assignment, discussion and co-sign commands](team-workflow.md#http-cli-and-result); `bid check run/list/show/decide/history` and `bid score rubric generate/list/show/revise/classify/section decide/item decide/coverage decide/decide/history` | Later commands, major-version compatibility period | [B02 Result 4.0 commands implemented](requirement-confirmation.md#http-cli-json-and-service-interfaces) |
| C02 Cache | Fixed-input caches for model drafting and check/score; invalidation by memory scope epoch, expiry and policy version, retaining human confirmation with a notice | [B03 assessment and consumer invalidation](parameter-assessment.md#freshness-cache-and-concurrent-work), approved, not implemented; cross-dependency invalidation for other capabilities; PostgreSQL memory concurrency acceptance | [Memory cache contract](memory.md#drafting-consumption-usage-audit-and-c02-cache-invalidation); [B02 review/membership freshness implemented](requirement-confirmation.md#consumer-gates-and-freshness) |
| C03 dry-run/budget | Read-only budget preflight, first-pass estimates and next-call blockers; Result 4.0 with legacy projection; preserved cost/intervention on terminal jobs | Measured duration profiles; bounded estimates for future providers | [Approved budget contract](budget.md) |

## Coverage matrix: evaluation and confidentiality

| ID | Current state | Gap | Dependency |
| --- | --- | --- | --- |
| E01 Public test set | Not implemented | Public hardware tender PDFs, human annotations, matching and metric definitions; [B03 synthetic/public evaluation plan](parameter-assessment.md#verification-and-evaluation-plan) | Real metrics depend on the API; B03 contract approved, not implemented |
| E02 Quality metrics | Not implemented | Measured starred-clause recall and other metrics; [B03 conversion, citation and four-state comparison metrics](parameter-assessment.md#verification-and-evaluation-plan) | B02, B03 (approved, not implemented), B04, B09 |
| S01 Confidential data | Object/drafting-snapshot encryption, org isolation, default outbound redaction and controlled switch; confidential fields replaced with placeholders before outbound transmission and restored at export, per [confidential-values.md](../notes/confidential-values.md); data-key rotation and separate session/signed-link keys, per [model-drafting-redaction.md](../notes/model-drafting-redaction.md); org BYOK per [provider-config.md](../notes/provider-config.md) | More sensitive-data patterns, disk encryption, retention periods; [attachment privacy and no-delete retention](attachment-archive.md#privacy-permissions-and-signed-downloads) | [Attachment archive contract](attachment-archive.md) approved, not implemented; other scope needs new contracts |
| S02 Authentic materials | No fabrication interface, dual-text model citation validation and human confirmation gates; sandbox capture and ingestion of real vendor web pages and white papers | Positive export-manifest acceptance in Word/WPS | Corresponding business chains |

## Real-service integration scope

The provider layer supports platform LLMs (Anthropic and OpenAI-compatible) for requirement
extraction, response drafting, prototype generation and simulated proposals; multimodal
screenshot analysis; Perplexity Search API or self-hosted SearXNG vendor-source search;
and Gotenberg document conversion. Scoring-rubric generation, scoring execution and
[agent decisions](../notes/builtin-agent.md#code) reuse structured-model integration.
Semantic vectors, cloud OCR and their evaluations are not integrated. Controlled
real-service evaluation of the agent and
[A01 database/queue acceptance](agent.md#acceptance-requirements) remain pending.

Work that does not depend on real services and can proceed under separate contracts:
local-browser evidence capture, Rust annotation, deterministic rules, Vue interfaces,
memory CRUD and approval, budget mechanisms and public evaluation-set preparation.

## Open decisions

| Decision | Needed when |
| --- | --- |
| Platform model providers, models and unit prices | Configuring default models in the platform operator console (平台运营后台) |
| Platform default Vision/Embedding/OCR, costs and data policies | Before Provider configuration |
| Per-org requests-per-minute limit for production vendor capture (currently 60; large pages can be truncated as incomplete) | Before enabling production vendor-web capture |
| Public template sharing | Before broadening template read access |
| Billing beyond the approved prepaid model, and data residency | Contracts for the corresponding scope; existing billing decisions are in [ADR 0002](../adr/0002-prepaid-billing.md) |
| Global memory | [Memory decisions and later enablement conditions](memory.md#decisions) |
| Dashboard response-card interaction and progress display | Before U02; service state machine in [response-cards.md](../notes/response-cards.md) |
| Platform search cost attribution: Perplexity charges per call, currently borne by the platform and not debited from org balances | Before enabling production Perplexity |
| Production object-storage maintainer | Production deployment |

## Known defects

| Defect | Impact | Direction |
| --- | --- | --- |
| Some drafting batches omit items, recorded as `missing_proposal` | Partial job completion; omitted requirements have no response cards | Rerun drafting to fill gaps; the job could automatically resubmit omitted items once |
| Tender table rows without a name column are named “表 X 第 Y 行” | Simulated proposals have difficulty determining the category, reducing recall | Derive the name from the table title or preceding paragraph |
| Some vendor official sites are unreachable from the worker network or have incomplete certificate chains | Only search excerpts can be used, or the vendor is omitted | Verify production-node networking; do not relax certificate validation |
| With high reasoning, `glm-5.3-flash` can spend the 32,000-token output ceiling before answering a rubric structure request | The request ends as `provider_output_truncated` | Use low reasoning for rubric generation or raise `BID_LLM_MAX_OUTPUT_TOKENS` |

## Suggested order

1. Remaining configuration and confidential management slices (U01), following the approved [management contract](management-pages.md); implement the approved [attachment archive](attachment-archive.md) first slice before profile/contract attachments; run database, browser and scale acceptance against the integrated resource-library migration chain.
2. [Cloud certificate-page annotation acceptance](annotation.md#failure-modes-and-test-plan) (B05); enable vendor renditions only after archive/privacy/mapping acceptance, and define separate profile/contract attachment archives.
3. Acceptance: WPS visual pagination for exports (B11), sandbox lease-expiry takeover, disconnects and storage-failure injection, and integration database/browser acceptance listed in the matrix rows.
4. Production deployment.
