---
kind: plan
---

# AI Bid Tool design

## Background, goals, and non-goals

The tool helps bidders (投标人) understand tender documents (招标文件), verify technical parameters, check bids (标书), and estimate scores. Every conclusion traces to original text or a real source. It runs as one local installation for one human, with external agents using the CLI. [ADR 0012](adr/0012-single-user-installation.md) defines the installation boundary; [the single-user plan](plan/single-user.md) defines the combined access-key and baseline cutover.

Bid preparation is repetitive and time-consuming, and a single omission can cause bid rejection (废标). Nearby bid-preparation staff currently give materials to someone familiar with AI for manual model-assisted checks and parameter organization. Results are difficult to reuse and verify.

**Goals**

- Extract all qualification (资格) requirements, technical parameters, scoring items, and starred (★) mandatory clauses (★条款), each with a verifiable original location; PDF pages and Word structural locations follow [ADR 0003](adr/0003-word-structural-citations.md).
- Collect screenshot evidence (证据) for hardware parameters from official vendor (厂家) sites or white papers; generate functional UI screenshots for in-house software.
- Check bids against tender documents for bid-rejection and point-deduction (扣分) risks.
- Estimate scores item by item against scoring criteria (评分标准) and explain lost points.
- Connect these tasks in a dashboard where humans and agents collaborate.
- Maintain local templates, feature lists, products and qualification materials for the person preparing bids.
- Make CLI an external interface: internal and external agents (such as Claude Code and Codex) use the same commands.
- Access all model capabilities (LLM, vision, OCR, search, embeddings) through common interfaces the human can configure with their own credentials.
- Reuse human-reviewed installation memory; later scopes and retrieval capabilities follow [the memory plan](plan/memory.md).

**Non-goals**

- Fabricate no supporting material: hardware evidence comes only from real pages/files; honestly report missing evidence.
- Do not integrate electronic bidding platforms, electronic signatures, or encrypted submission uploads.
- Do not implement pricing strategies or competitor analysis.
- Do not build OCR/model engines; integrate existing services through interfaces.

## Users and core scenarios

One human maintains resources and model configuration, prepares responses and reviews results. Agents perform scoped repetitive operations through CLI. Human decisions need no user record, domain-role assignment or second reviewer; human-versus-agent gates remain.

| Scenario | Primary user | Input | Output |
| --- | --- | --- | --- |
| Maintain resources | Human | Bid templates, feature lists, products, qualifications, company information | Resource library selectable in tasks |
| Create bid task | Human | Tender documents + selected resources | Task with a dashboard |
| Extract requirements | Human or scoped agent | Tender documents | Qualification, parameter, scoring, and starred requirement list with source locations |
| Respond to hardware parameters | Human or scoped agent | Parameter list + proposed product (拟投产品) model from product library | Response (响应) deviation (偏离) table + official-site/white-paper screenshot per parameter |
| Respond for in-house software | Human or scoped agent | Functional requirements + feature list | Feature response table + UI screenshots; model-generated prototype (原型) screenshots for unimplemented features, with human keep/replace decisions before final export |
| Check bid | Human or scoped agent | Tender documents + bid draft (初稿) | Bid-rejection risks, point-deduction risks, and notices |
| Estimate score | Human or scoped agent | Scoring criteria + bid draft | Item estimates, reasons for lost points, possible improvements |
| External agent invocation | External agent | CLI commands + human-issued token | Same Result contract; confirmation and export remain human-only |

Typical flow: create task/upload tender documents/select templates and products → confirm requirements → verify proposed-model parameters → confirm evidence item by item → export response tables → check and estimate scores after the bid is finalized.

## Overall architecture

The local application exposes one API layer to web, CLI and internal agents on loopback. Background jobs perform expensive work; adapters unify models and external services.

&#91;embedded content: Overall architecture · three entry points, API layer, background jobs, data and model adapters\]

The API layer enforces access-key/session authentication, token scopes, human-versus-agent gates and auditing. It also reads/writes PostgreSQL directly, omitted from the diagram. Commands such as `ui mock`, `draft`, and `export` also run as background jobs.

<a id="multi-tenancy-and-permissions"></a>
## Installation identity and permissions

[ADR 0012](adr/0012-single-user-installation.md) is the authority for identity,
database roles and local isolation. The operating-system account is the boundary;
there are no orgs, user accounts, memberships, application role assignments or RLS policies.

| Actor | Capabilities |
| --- | --- |
| Human using the installation access key or its console session | Maintain local settings and resources, issue tokens, review and confirm content, export |
| Built-in agent or external agent using a scoped `bid_` token | Run granted operations; no human-only confirmation or export |

The migration owner owns database objects; runtime `bid_app` executes application work and cannot alter tables or disable triggers. Native desktop PostgreSQL uses a rootless local socket; configured API/Compose installations connect over their configured transport. Database gates retain actor kind, token, agent-session and job attribution, and human decisions retain their timestamps. No reviewer/creator separation or co-signing between people is required.

Files use protected local storage without an org prefix; configured object storage
keeps the same protected-download contract. Provider and service credentials are
encrypted local settings; model calls use the human's own provider configuration.
Trust-anchor settings follow ADR 0010 as amended by ADR 0012.

Audit authentication, confirmation, rejection, export, configuration changes and
token issuance with actor attribution. Record model calls/tokens, OCR pages and
other resource usage. Bound jobs by resource counts, concurrency and duration.
Authentication details belong in [the single-user plan](plan/single-user.md), and
local startup commands in [the development guide](guides/development.md).

## Data model

Requirements and evidence remain central: requirements locate original tender text, and evidence locates real sources. Task records carry `task_id`; there are no org/user foreign keys. PostgreSQL stores structured data; protected storage holds files/screenshots. The single-user baseline follows [ADR 0012](adr/0012-single-user-installation.md).

| Group | Entity | Meaning | Key fields |
| --- | --- | --- | --- |
| Access/audit | ApiToken | CLI/external-agent token | Scopes, expiry, revocation |
| Access/audit | AuditLog, UsageRecord | Audit and usage | Actor kind, token/agent/job attribution, action, object, usage |
| Resources | Template | Bid template | Word file, sections, project types, version |
| Resources | FeatureItem | In-house software feature | Product, description, implementation state (implemented/in development/planned), real screenshots |
| Resources | Product | Frequently proposed product | Vendor, exact model/version, official specs page, white paper |
| Resources | Certificate | Qualification/personnel certificate (证照) | Name, number, expiry, scans |
| Resources | OrgProfile (`OrgProfile` model) | Company profile (单位资料) and track record; this historical model name does not imply tenancy | Registration, past contracts, common wording |
| Tasks | Task | One bid-preparation project | Name, tender number, deadline |
| Tasks | TaskResource | Selected-resource snapshot | Resource type/ID/version |
| Tasks | Document, Chunk | Uploaded file and parsed content | Type, content hash, page, section path, OCR flag |
| Tasks | Requirement | One requirement | Category, starred flag, original text/location, structured condition |
| Tasks | Evidence | One evidence item | Requirement/product, source, capture time, quote, screenshot/hash, verdict, human confirmation timestamp |
| Tasks | ResponseItem, Finding, ScoreItem | Response row, check finding, score estimate | Deviation, risk level, estimate/basis |
| Tasks | Card | Dashboard card | Related object, state, version |
| Configuration/memory | ProviderConfig | Model/OCR service configuration | Capability, provider, endpoint, encrypted key, scope |
| Configuration/memory | Memory | One memory | Scope, content, source, state, vector |

Structured parameter conditions let code compare satisfaction rather than relying solely on model judgments. A requirement and evidence example (omitting `task_id`):

```json
{
  "requirement": {
    "id": "req-031",
    "category": "tech_param",
    "starred": true,
    "text": "★ 单卡显存不低于 80GB",
    "page": 42,
    "condition": { "param": "显存", "op": ">=", "value": 80, "unit": "GB" }
  },
  "evidence": {
    "id": "ev-117",
    "requirement_id": "req-031",
    "product": { "vendor": "某厂家", "model": "某型号 SXM" },
    "source": { "type": "web", "url": "https://…", "captured_at": "2025-02-18T10:31:00+08:00" },
    "quote": "显存容量 80GB",
    "screenshot": { "path": "evidence/ev-117.png", "sha256": "…" },
    "model_matched": true,
    "verdict": "satisfied",
    "confirmed_at": null
  }
}
```

Evidence with empty `confirmed_at` cannot enter response tables or exports.

<a id="org-resources-and-task-creation"></a>
## Resource library and task creation

The human maintains a local library and selects resources for a task. Tasks lock selected versions; later library changes cannot silently affect ongoing tasks.

**Resource library**

| Resource | Task use |
| --- | --- |
| Bid templates | Word template/sections for `export` |
| Feature lists | `ui mock` checks implementation; prefer real screenshots for implemented features |
| Product library | Proposed model, official specs, white-paper sources for `evidence fetch` |
| Qualification/personnel certificates | `check` verifies completeness/expiry |
| Company information/track record | Basis for qualification responses and track-record tables |
| Provider profiles | Task model/OCR service combination |

Every resource change creates a new version and retains older versions. Certificate-expiry reminders use a configurable lead time.

**Task creation**

1. Enter name, tender number and deadline.
2. Upload tender documents; background parsing starts.
3. Select template, products (separately per lot if needed), feature list, certificate set, and provider profile.
4. Review scope and model configuration, then confirm to generate the dashboard.

CLI equivalent:

```bash
bid task create --name "某单位 GPU 服务器采购" --tender tender.pdf \
  --template tpl-standard@v3 --product prod-gpu-01 --features fl-platform@v5 \
  --certs cert-set-default --providers profile-default
```

Resources can be added/replaced later. Audit every change and identify potentially affected confirmed cards.

## Model and OCR provider layer

All external capabilities use common interfaces. Business code depends on interfaces rather than vendors; replacing a model/OCR changes configuration, not code.

**Capability interfaces**

| Interface | Input → output | Consumer | Implementations |
| --- | --- | --- | --- |
| LLMProvider | Messages + JSON Schema → structured result | `req extract`, `check`, `score`, agent | Cloud models, self-hosted open models |
| VisionProvider | Image + question → judgment/location | `evidence fetch` verifies screenshot values | Multimodal models |
| OCRProvider | Image/scanned page → text/coordinates | `tender parse` scanned pages/certificates | Cloud OCR, local engines |
| SearchProvider | Query → URLs | `evidence fetch` finds official pages/white papers | Search APIs |
| EmbeddingProvider | Text → vector | Memory retrieval, similar-clause matching | Cloud/local embedding models |
| BrowserProvider | URL → screenshot/page text | `evidence fetch` | Local Playwright, remote browser |

**Common rules**

- Return provider, model name/version, latency, and cost with every result; store these in usage and with results for evaluation/tracing.
- Normalize failures into retryable, non-retryable, and content refusal, mapped to CLI exit codes.
- Handle timeouts, retries, and rate limits in adapters rather than repeating them in business code.

**Configuration**: the human configures their own provider credentials and task-selected profiles; there is no platform model catalog or platform-default inheritance. Encrypt keys and show only trailing characters in UI. Service credentials and Clef are local settings under [ADR 0012](adr/0012-single-user-installation.md).

**Configuration page (OCR example)**

The human configures each capability on “模型与服务配置”:

- Select cloud, local engine, or disabled.
- Enter endpoint/key; choose language and accuracy.
- Choose scanned pages only versus OCR verification of all pages.
- “测试连接”: upload a sample page and immediately view recognition/latency.
- Save a profile for task selection.

LLM, vision, search, and embeddings use the same page structure with different parameters. `bid provider set` and `bid provider test` also configure/test services.

## Memory system

Currently enabled installation memory uses only the `library` scope, which
replaces the former `org` scope. `global` and `project` are reserved and unavailable.
Memory guides behavior and can never serve as parameter evidence. See the
[memory plan](plan/memory.md) for retrieval boundaries and later scope enablement.

**Writes**

- Explicit: click “记住” or use `bid memory add --scope library`.
- Automatic proposals from evidence rejection, corrected agent conclusions or false-positive checks remain candidates until human confirmation of the exact revision.
- Exclude sensitive quotes, identity numbers and bank accounts; evidence uses the evidence chain rather than memory.

**Reads and management**

- Retrieve only enabled scopes and active, applicable revisions; retain rule/preference precedence and the source of each memory used.
- Record memory used for every result; expand it on the dashboard and disable individual entries.
- Each memory carries candidate/active/disabled state, source task/card and optional expiry, with immutable revision history.
- “记忆管理” supports scope-based viewing/search/edit/disable; CLI `bid memory list`, `add`, `disable`.
- Embedding/pgvector retrieval remains unavailable until its conditions in the memory plan are met; current keyword retrieval has no tenant filter or cross-installation memory layer.

## Processing flow and CLI inventory

The business flow has 8 commands plus management commands. Commands run through the local API/worker or supported local CLI mode, do not invoke each other and can rerun independently.

**Business commands**

| Order | Command | Behavior | Reads | Writes | Language |
| --- | --- | --- | --- | --- | --- |
| 1 | `bid tender parse` | Parse PDF/Word paragraphs/tables with verifiable locations; OCR scanned pages | Document | Chunk | Python |
| 2 | `bid req extract` | Extract requirements/structured parameter conditions | Chunk, memory | Requirement | Python |
| 3 | `bid evidence fetch` | Verify product-library models, capture web screenshots/render PDF regions | Requirement, Product | Unconfirmed Evidence | Python |
| 4 | `bid evidence stamp` | Crop, box key values, watermark source/time, hash | Evidence | Annotated Evidence | Rust |
| 5 | `bid ui mock` | Use real screenshots for implemented features; generate HTML prototypes for others and capture in sandbox | Requirement, FeatureItem | UI Evidence | Python |
| 6 | `bid draft` | Assemble confirmed responses into deviation tables under [ADR 0005](adr/0005-human-confirmed-responses.md) | Requirement, confirmed responses/evidence | ResponseItem | Python |
| 7 | `bid check` ∕ `bid score` | Check against tender documents; estimate against scoring criteria | Requirement, bid, certificate library, memory | Finding, ScoreItem | Python |
| 8 | `bid export` | Apply selected Word template with response tables/evidence appendices | ResponseItem, Evidence, Template | Word file |

**Management commands**
| Command | Behavior |
| --- | --- |
| `bid token create` | Issue scoped external-agent token with expiry |
| `bid task create` ∕ `list` | Create/select resources; view tasks |
| `bid resource <类型> list` ∕ `add` ∕ `update` | Maintain templates/features/products/certificates/company profiles |
| `bid provider set` ∕ `test` | Configure/test models/OCR |
| `bid memory list` ∕ `add` ∕ `disable` | Manage memory |
| `bid job status` ∕ `wait` | Inspect/wait for jobs |
| `bid schema` | Emit command parameters/output JSON Schema for discovery |
| `bid mcp serve` | Expose the same commands as MCP (optional) |

A human gate confirms/rejects evidence and response cards before they enter response tables; human-only decisions use actor checks rather than separate reviewer roles.

## CLI design and external agent integration

CLI is an external interface. Dashboard backend, internal agents, and external agents use the same commands/JSON; identity and tokens determine permissions.

**Invocation**: `bid <object> <action> [--task <task id>] [--json] [--dry-run] [--wait]`. No interactive questions; missing parameters fail directly.

**Modes**

**API mode** is a thin client of the configured local loopback API; upload to installation storage and execute jobs on its worker. The desktop app may run rootless PostgreSQL over a local socket; other API setups use their configured database transport.

**Local CLI mode** uses rootless PostgreSQL over a local socket and local files, with the same human-versus-agent gates, Result contract and no org storage prefix. Local commands belong in [the development guide](guides/development.md).

**Authentication and permissions**
- Humans use `BID_ACCESS_KEY` or an owner-only `BID_ACCESS_KEY_FILE`; console sessions are minted from the same installation key.
- External agents use human-issued `bid_` tokens via `BID_TOKEN`, bound to expiry and scopes such as `task:read`, `evidence:fetch`, `check:run`. There is no login or org-selection command.
- Tokens and built-in agents can never receive `evidence:confirm` or `export`; confirmation/export require the human actor.

```json
{
  "ok": true,
  "command": "req extract",
  "data": { "created": 86, "starred": 7 },
  "items": [ { "id": "req-031", "source": { "doc": "tender.pdf", "page": 42 } } ],
  "warnings": [ "第 18–20 页为图片，未解析" ],
  "cost": { "calls": 2, "llm_tokens": 152000, "ocr_pages": 0, "search_requests": 0, "browser_units": 0, "unresolved_calls": 0 },
  "duration_ms": 38200
}
```

**Exit codes**

| Code | Meaning | Caller action |
| --- | --- | --- |
| 0 | Success | Continue |
| 2 | Parameter/input error | Correct before retry; waiting will not fix it |
| 3 | Retryable timeout/rate-limit/network failure | Retry with backoff |
| 4 | Non-retryable missing-page/content refusal | Change source or involve a human |
| 5 | Partial success | Inspect failed `items`; rerun only failures |

**Idempotence/cache**: identical file hash/prompt version/model inputs reuse cached results without another call. Reruns update changed records only and never overwrite human-confirmed content.

**Estimation**: `--dry-run` reports item count, estimated resource use/time without writes.

**Jobs**: expensive commands immediately return job ID; `--wait` waits for completion. `bid job status` queries state; dashboards use SSE.

**External agents**

- Discovery: `bid schema` emits all parameters/outputs, plus an agent-readable guide with typical flows/prohibitions.
- CLI-capable agents (Claude Code, Codex) invoke directly; tool-protocol-only agents use `bid mcp serve`.
- Audit each invocation with its token; clearly identify agent-produced content on the dashboard.
- Version commands/outputs; incompatible changes increment major version; retain old versions for at least one major-version cycle.

## Key design decisions

The shared principle is to require human verification before content enters a bid.

| Decision | Choice | Reason | Cost |
| --- | --- | --- | --- |
| Cited conclusions | Original quote + verifiable location per extraction | Fast human checks and automatic existence validation | More complex prompts/postprocessing |
| Structured parameter conditions | Parameter, operator, value, unit | Programmatic satisfaction comparison | Unit conversions/ambiguous text need handling; send unconvertible cases to humans |
| Double protection for starred clauses | Union of LLM extraction and symbol/keyword scans | One missed substantive clause (实质性条款) can reject a bid | False positives require human filtering |
| Exact model matching | Evidence matches exact versioned product model | Versions in one series differ | Lower evidence hit rate |
| Human gate | Unconfirmed evidence excluded from response/export; tokens cannot confirm | Humans own authenticity; agents cannot decide for them | Extra human step |
| Archive on capture | Screenshot/hash/time/source together | Pages change/disappear; retained verification | Storage |
| Human prototype decisions before export | No screenshot labels; keep/replace individually or by module before final section (正式件) | Bid and delivery can overlap; prototype may become delivered frontend | Extra pre-export decision |
| CLI external interface | Humans/internal/external agents share commands/JSON | One permission/test system; ready integration | Strict version management |
| Common interfaces | Separate LLM/vision/OCR/search/embedding/browser interfaces | User service choice and easy replacement | Shared capabilities only; special features need adapters |
| Single-user installation | Local OS-account boundary, owner/runtime database roles and human-versus-agent gates | [ADR 0012](adr/0012-single-user-installation.md) | Multi-user/hosted operation requires a new decision |
| Resource snapshots | Task locks selected versions | No silent changes | Explicit version switching |
| Reviewed memory | Library scope; automatic proposals need human approval | Avoid amplifying errors | Slower accumulation; later scopes follow the memory plan |

## Dashboard and agent design

Each dashboard card represents a requirement or check finding. Humans/agents advance work through card state changes.

&#91;embedded content: Parameter card states · 5 states, 1 human gate\]

Parameter cards reach terminal state only through pending-human-confirmation. Missing-evidence cards also stop there; humans decide to supply material or record negative deviation (负偏离). Risk cards have pending/fixed/ignored states.

**Dashboard**

- Parameter cards (technical/functions), qualification cards, and risk cards (check findings).
- Show tender quote, screenshot, source, model verdict; one-click confirm/reject.
- Filters: starred, awaiting my confirmation, bid-rejection risk.
- Optimistic locking with card versions and explicit conflicts; SSE progress.

**Agent**

- Tools are the CLI commands above with CLI JSON inputs/outputs.
- Understand intent, prioritize/dispatch verification, retry failures, summarize results.
- Run as initiator but cannot confirm evidence, edit confirmed cards, or export, like external agents.
- Load memory under memory-system rules and identify used entries.
- Keep state on server; resume from cards without repeating completed work.
- Initially use Claude Code/Codex with a CLI guide to validate external integration; later implement an internal agent with an SDK.

## Evaluation

Rerun a fixed dataset after prompt/model/rule changes. Starred-clause recall is mandatory; set other targets after first measurements.

| Metric | Calculation | Initial target |
| --- | --- | --- |
| Starred-clause recall | Extracted ground-truth starred clauses / all such clauses | 100% |
| Requirement recall/precision | Item matching against ground truth | After measurement |
| Citation validity | Quote appears at referenced location | After measurement |
| Parameter verdict accuracy | Agreement on satisfied/not satisfied/not found | After measurement |
| Evidence acceptance | Human-confirmed rather than rejected | After measurement |
| Risk detection/false positives | Compare human-annotated findings | After measurement |
| Per-document resource use/time | Model cost/total time from parse through check | After measurement |

**Dataset**

1. Development: download public hardware tender documents from government procurement sites; annotate requirements.
2. After first usable release: add real bids with consent, using final human deliverables as ground truth.
3. Record dashboard confirmations/rejections as new test samples automatically.

## Security and compliance

Bids contain quotes/customer information; false supporting material can cause rejection or liability. Confidentiality and authentic evidence are mandatory.

**Confidentiality**

- Protected local files without tenant prefixes, encrypted content and short-lived signed downloads.
- Use only model services that do not train on API data; disclose retention policies on configuration pages.
- Optional outbound redaction (遮挡) of quotes, contacts, identity numbers, bank accounts.
- User-provided credentials and local models; disclose outbound service use for confidentiality decisions.
- Encrypt provider keys/tokens; decrypt only for jobs; revoke tokens anytime.
- Demos/portfolios use public tender documents only.

**Authenticity**

- Hardware evidence comes only from real web/file screenshots; no vendor-page generation.
- Save source/time/quote/hash; optional evidence manifest at export.
- Unimplemented software UI prototypes have no visible labels, retain internal provenance, and require human keep/real-screenshot replacement decisions before final export.
- Honestly record unsatisfied parameters as negative deviation; no parameter-rewriting suggestions.

**Operations**: every confirmation/rejection/export records actor/time for tracing.

## Technology choices

Python handles most work; Rust owns the bounded annotation CLI; Vue handles the dashboard. Unresolved choices remain in the open decisions below.

| Component | Choice | Notes |
| --- | --- | --- |
| CLI | Typer | Python, generated help |
| Data | Pydantic | Structured-output validation and `bid schema` JSON Schema |
| API | FastAPI | REST, SSE, loopback API for web/CLI |
| Database | PostgreSQL + pgvector | Local desktop uses a rootless socket; owner and `bid_app`; API setups use configured transport; memory retrieval follows its plan |
| Storage | Local files; S3-compatible adapter | Protected files and signed downloads without org prefixes |
| Jobs | Procrastinate | PostgreSQL queue; see [queue.py](../server/app/jobs/queue.py); parsing/verification/checks, retry/cancel |
| Auth | Installation access key, console sessions and scoped tokens | Human-versus-agent gates; [ADR 0012](adr/0012-single-user-installation.md) |
| PDF | PyMuPDF | Text/coordinates, white-paper region rendering |
| Word | python-docx | Parse bids/apply selected templates |
| Screenshots | Playwright (BrowserProvider default) | Dynamic pages |
| LLM/vision/embedding/search/OCR | Adapter configuration; defaults remain open decisions | Structured outputs, long context, image understanding, no API-data training |
| Annotation | Rust clap/serde/image/sha2 | Crop/box/watermark/hash; independent job executable |
| Frontend | Vue 3 + Vite + Element Plus | Dashboard/resources/providers/memory |
| Local operation | Local services; Compose for supporting development services | Desktop packaging, bundled runtimes and signing require a separate plan |
| Tests/CI | pytest, cargo test, GitHub Actions | Single-user fixtures, actor/confirmation gates, evaluation scripts |

## Implementation phases

Follow [Single-user local installation](plan/single-user.md): removal of multi-person
surfaces, access-key authentication and the baseline schema form one cutover.
Task board, progress, activity and events follow
[Task board and durable progress](notes/task-progress.md). Remaining acceptance,
scope and dependencies are maintained in [the roadmap](plan/roadmap.md).

## Open decisions

- [ ] Recommended LLM/vision/embedding configurations: compare Chinese long-document extraction, capabilities and data policies for the human's own provider accounts.
- [ ] Search API coverage of domestic vendor sites; explicit service selection follows [provider configuration](notes/provider-config.md).
- [ ] Optional cloud OCR versus local engines.
- [x] Background queue: Procrastinate; see [queue.py](../server/app/jobs/queue.py).
- [ ] Later memory scopes and retrieval: [memory plan](plan/memory.md).
- [ ] Adapt Word exports to widely varying company templates.
- [ ] Automatically import feature lists from existing requirements/product documents.
- [ ] Desktop packaging, supported operating systems, bundled runtimes and signing: separate planning required under [the single-user plan](plan/single-user.md#out-of-scope).
