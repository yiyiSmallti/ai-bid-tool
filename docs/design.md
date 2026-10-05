---
kind: plan
---

# AI Bid Tool design

## Background, goals, and non-goals

The tool helps bidders (投标人) understand tender documents (招标文件), verify technical parameters, check bids (标书), and estimate scores. Every conclusion traces to original text or a real source. It runs as SaaS: multiple orgs (organizations/tenants, 单位) share a service with isolated data, and external agents can invoke it directly through CLI.

Bid preparation is repetitive and time-consuming, and a single omission can cause bid rejection (废标). Nearby bid-preparation staff currently give materials to someone familiar with AI for manual model-assisted checks and parameter organization. Results are difficult to reuse and verify.

**Goals**

- Extract all qualification (资格) requirements, technical parameters, scoring items, and starred (★) mandatory clauses (★条款), each with a verifiable original location; PDF pages and Word structural locations follow [ADR 0003](adr/0003-word-structural-citations.md).
- Collect screenshot evidence (证据) for hardware parameters from official vendor (厂家) sites or white papers; generate functional UI screenshots for in-house software.
- Check bids against tender documents for bid-rejection and point-deduction (扣分) risks.
- Estimate scores item by item against scoring criteria (评分标准) and explain lost points.
- Connect these tasks in a dashboard where humans and agents collaborate.
- Deploy as SaaS: isolate org accounts/data, with each org maintaining templates, feature lists, products, and qualification materials.
- Make CLI an external interface: internal and external agents (such as Claude Code and Codex) use the same commands.
- Access all model capabilities (LLM, vision, OCR, search, embeddings) through common interfaces that orgs can replace.
- Use four memory layers—global, org, user, and project—to learn org practices over time.

**Non-goals**

- Fabricate no supporting material: hardware evidence comes only from real pages/files; honestly report missing evidence.
- Do not integrate electronic bidding platforms, electronic signatures, or encrypted submission uploads.
- Do not implement pricing strategies or competitor analysis.
- Do not build OCR/model engines; integrate existing services through interfaces.

## Users and core scenarios

There are four user groups: org administrators maintain accounts, resources, and model configuration; bid specialists manage formatting, qualifications, and progress; technical leads handle parameters and solutions; external agents perform repetitive operations through CLI. Initial users are nearby people who regularly prepare bids.

| Scenario | Primary user | Input | Output |
| --- | --- | --- | --- |
| Maintain org resources | Org administrator | Bid templates, feature lists, products, qualifications, company information | Resource library selectable in tasks |
| Create bid task | Bid specialist | Tender documents + selected resources | Task with a dashboard |
| Extract requirements | Bid specialist | Tender documents | Qualification, parameter, scoring, and starred requirement list with source locations |
| Respond to hardware parameters | Technical lead | Parameter list + proposed product (拟投产品) model from product library | Response (响应) deviation (偏离) table + official-site/white-paper screenshot per parameter |
| Respond for in-house software | Technical lead | Functional requirements + org feature list | Feature response table + UI screenshots; model-generated prototype (原型) screenshots for unimplemented features, with human keep/replace decisions before final export |
| Check bid | Bid specialist | Tender documents + bid draft (初稿) | Bid-rejection risks, point-deduction risks, and notices |
| Estimate score | Bid specialist | Scoring criteria + bid draft | Item estimates, reasons for lost points, possible improvements |
| External agent invocation | External agent | CLI commands + org-issued token | Same JSON as human operations; confirmation remains human |

Typical flow: create task/upload tender documents/select templates and products → confirm requirements → verify proposed-model parameters → confirm evidence item by item → export response tables → check and estimate scores after the bid is finalized.

## Overall architecture

The system runs as a server: web, CLI, and internal agents all use one API layer. Background jobs perform expensive work; adapters unify models and external services.

&#91;embedded content: Overall architecture · three entry points, API layer, background jobs, data and model adapters\]

The API layer enforces org isolation, permissions, and auditing in one place, so external agents and humans have the same constraints. It also reads/writes PostgreSQL directly, omitted from the diagram. Commands such as `ui mock`, `draft`, and `export` also run as background jobs.

## Multi-tenancy and permissions

The org is the primary isolation boundary: files, resources, memory, model configuration, and usage belong to an org. Global login identities join orgs through Membership; see [agent.md hard rule 1](../agent.md#hard-rules-must-never-be-violated). One org's data never appears in another's queries, prompts, or search results.

**Accounts and roles**

| Role | Capabilities |
| --- | --- |
| Platform administrator | Provision orgs; maintain global memory and platform-default models; no org business-data access |
| Org administrator | Manage org members, resources, model/OCR configuration, tokens, and org memory approval |
| Bid specialist | Create tasks, confirm qualification response cards (响应卡), export bids |
| Technical lead | Confirm parameter/feature evidence |
| Read-only reviewer | View tasks/dashboards and comment |
| External agent (token) | Invoke granted CLI permissions; no evidence confirmation or export |

A user may join multiple orgs. Every request carries current-org context.

**Isolation**

- Database: every org business table carries `org_id`; PostgreSQL RLS filters by session org even if application code omits a condition. Approved global-table exceptions are defined in [agent.md hard rule 1](../agent.md#hard-rules-must-never-be-violated).
- Files: object-storage prefix `org/<org_id>/`, short-lived signed download links.
- Model calls: when an org supplies its own key, costs and data policies follow its provider contract.
- Memory/retrieval: vector queries enforce org filters; orgs read global memory only.
- Background jobs: org context on every job, redacted logs.

**Audit and usage**

- Audit login, confirmation, rejection, export, configuration changes, and token issuance; query by org.
- Meter model calls/tokens, OCR pages, and storage by org for quotas/billing.
- Org monthly budget caps pause new jobs and notify administrators when exceeded.
- Private deployment can enable one org using the same code on the customer's server.

## Data model

Requirements and evidence remain central: requirements locate original tender text, and evidence locates real sources. Org business records carry `org_id`; task records also carry `task_id`. PostgreSQL stores structured data; object storage holds files/screenshots. Global identities and approved platform exceptions follow [agent.md hard rule 1](../agent.md#hard-rules-must-never-be-violated).

| Group | Entity | Meaning | Key fields |
| --- | --- | --- | --- |
| Tenants/accounts | Org | One org | Name, plan, monthly budget |
| Tenants/accounts | User, Membership | Global user and roles in orgs | Login method, org, role |
| Tenants/accounts | ApiToken | CLI/external-agent token | Org, issuer, scopes, expiry |
| Tenants/accounts | AuditLog, UsageRecord | Audit and usage | Actor, action, object, usage, cost |
| Org resources | Template | Bid template | Word file, sections, project types, version |
| Org resources | FeatureItem | In-house software feature | Product, description, implementation state (implemented/in development/planned), real screenshots |
| Org resources | Product | Frequently proposed product | Vendor, exact model/version, official specs page, white paper |
| Org resources | Certificate | Qualification/personnel certificate (证照) | Name, number, expiry, scans |
| Org resources | OrgProfile | Org profile (单位资料) and track record | Registration, past contracts, common wording |
| Tasks | Task | One bid-preparation project | Name, tender number, deadline, members, budget |
| Tasks | TaskResource | Selected-resource snapshot | Resource type/ID/version |
| Tasks | Document, Chunk | Uploaded file and parsed content | Type, content hash, page, section path, OCR flag |
| Tasks | Requirement | One requirement | Category, starred flag, original text/location, structured condition |
| Tasks | Evidence | One evidence item | Requirement/product, source, capture time, quote, screenshot/hash, verdict, confirmer |
| Tasks | ResponseItem, Finding, ScoreItem | Response row, check finding, score estimate | Deviation, risk level, estimate/basis |
| Tasks | Card | Dashboard card | Related object, state, owner, version |
| Configuration/memory | ProviderConfig | Model/OCR service configuration | Capability, provider, endpoint, encrypted key, scope |
| Configuration/memory | Memory | One memory | Scope, owner, content, source, state, vector |

Structured parameter conditions let code compare satisfaction rather than relying solely on model judgments. A requirement and evidence example (omitting `org_id` and `task_id`):

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
    "source": { "type": "web", "url": "https://…", "captured_at": "2026-10-12T10:31:00+08:00" },
    "quote": "显存容量 80GB",
    "screenshot": { "path": "evidence/ev-117.png", "sha256": "…" },
    "model_matched": true,
    "verdict": "satisfied",
    "confirmed_by": null
  }
}
```

Evidence with empty `confirmed_by` cannot enter response tables or exports.

## Org resources and task creation

Each org maintains its own library and selects resources for a task. Tasks lock selected versions; later library changes cannot silently affect ongoing tasks.

**Resource library**

| Resource | Usual maintainer | Task use |
| --- | --- | --- |
| Bid templates | Org administrator | Word template/sections for `export` |
| Feature lists | Technical lead | `ui mock` checks implementation; prefer real screenshots for implemented features |
| Product library | Technical lead | Proposed model, official specs, white-paper sources for `evidence fetch` |
| Qualification/personnel certificates | Bid specialist | `check` verifies completeness/expiry |
| Company information/track record | Bid specialist | Basis for qualification responses and track-record tables |
| Provider profiles | Org administrator | Task model/OCR service combination |

Every resource change creates a new version and retains older versions. Certificate-expiry reminders use an org-configurable lead time.

**Task creation**

1. Enter name, tender number, deadline, and members.
2. Upload tender documents; background parsing starts.
3. Select template, products (separately per lot if needed), feature list, certificate set, and provider profile.
4. Set a budget cap and confirm to generate the dashboard.

CLI equivalent:

```bash
bid task create --name "某单位 GPU 服务器采购" --tender tender.pdf \
  --template tpl-standard@v3 --product prod-gpu-01 --features fl-platform@v5 \
  --certs cert-set-default --providers profile-default --budget-usd 20
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

**Configuration hierarchy**: platform defaults → org overrides → task-selected profiles. An org can override a single capability, such as local OCR. Encrypt keys and show only trailing characters in UI.

**Configuration page (OCR example)**

Org administrators configure each capability on “模型与服务配置”:

- Select cloud, local engine, or disabled.
- Enter endpoint/key; choose language and accuracy.
- Choose scanned pages only versus OCR verification of all pages.
- “测试连接”: upload a sample page and immediately view recognition/latency.
- Save a profile for task selection.

LLM, vision, search, and embeddings use the same page structure with different parameters. `bid provider set` and `bid provider test` also configure/test services.

## Memory system

Global, org, user, and project layers remember how an org/person/project works. Memory guides behavior and can never serve as parameter evidence.

| Scope | Content | Writers | Readers | Example |
| --- | --- | --- | --- | --- |
| Global | General procurement knowledge | Platform administrators | All orgs, read-only | Common rejection clauses, unit conversions |
| Org | Org practices/experience | Members propose, org admins approve | Org members/agents | Vendor specs use white papers; earlier points lost for track-record formatting |
| User | Personal preferences | User | User and user-initiated agents | Show only rejection risks; concise responses |
| Project | Task-specific information | Task members | Task members | Lot 2 changes model; purchaser (招标人) clarification changes delivery date |

**Writes**

- Explicit: click “记住” or use `bid memory add --scope org`.
- Automatic proposals: evidence rejection, correcting agent conclusions, or marking false-positive checks creates candidate memory; effective only after human confirmation (人工确认), with org candidates approved by admins.
- Project decisions (selection, clarification, assignments) enter project memory; retained read-only after archival.
- Exclude sensitive quotes, identity numbers, bank accounts; evidence uses the evidence chain rather than memory.

**Reads**

- Before each agent/command, retrieve project → user → org → global relevant memory and put top relevant entries in context.
- Fact/rule priority: project > org > global. Preference priority: user > org defaults. Personal preferences cannot override org compliance rules.
- Record memory used for every result; expand it on the dashboard and disable individual entries.

**Isolation and management**

- Org memory never enters global memory, maintained only by platform admins.
- Each memory carries candidate/active/disabled state, source task/card, creator, and optional expiry.
- “记忆管理” supports scope-based viewing/search/edit/disable; CLI `bid memory list`, `add`, `disable`.
- Store pgvector columns on memory; enforce org/scope filters during retrieval.

## Processing flow and CLI inventory

The business flow has 8 commands plus management commands. All use `bid`, execute as server background jobs, do not invoke each other, and can rerun independently.

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
| 8 | `bid export` | Apply org Word template with response tables/evidence appendices | ResponseItem, Evidence, Template | Word file | Python |

**Management commands**

| Command | Behavior |
| --- | --- |
| `bid login` ∕ `bid org use` | Log in and switch org |
| `bid token create` | Issue external-agent token with scopes/expiry |
| `bid task create` ∕ `list` | Create/select resources; view tasks |
| `bid resource <类型> list` ∕ `add` ∕ `update` | Maintain templates/features/products/certificates/org profile |
| `bid provider set` ∕ `test` | Configure/test models/OCR |
| `bid memory list` ∕ `add` ∕ `disable` | Manage memory |
| `bid job status` ∕ `wait` | Inspect/wait for jobs |
| `bid schema` | Emit command parameters/output JSON Schema for discovery |
| `bid mcp serve` | Expose the same commands as MCP (optional) |

A human gate between steps 4/5 and 6 confirms/rejects annotated evidence before it enters response tables.

## CLI design and external agent integration

CLI is an external interface. Dashboard backend, internal agents, and external agents use the same commands/JSON; identity and tokens determine permissions.

**Invocation**: `bid <object> <action> [--task <task id>] [--json] [--dry-run] [--wait]`. No interactive questions; missing parameters fail directly.

**Modes**

- Remote (default): thin API client; upload files to org object storage; execute jobs on server.
- Local: development/offline trials use local PostgreSQL and local files, retaining RLS, permissions, and `org/{org_id}/` isolation as required by [agent.md CLI contract](../agent.md#hard-rules-must-never-be-violated). Both modes have identical commands/outputs.

**Authentication and permissions**

- Humans use `bid login` with the same roles as web.
- External agents use org-admin-issued tokens bound to org, expiry, and scopes such as `task:read`, `evidence:fetch`, `check:run`.
- Tokens can never receive `evidence:confirm` or `export`; only logged-in humans confirm/export.

**Output**: `--json` emits a uniform result; each conclusion includes a source:

```json
{
  "ok": true,
  "command": "req extract",
  "data": { "created": 86, "starred": 7 },
  "items": [ { "id": "req-031", "source": { "doc": "tender.pdf", "page": 42 } } ],
  "warnings": [ "第 18–20 页为图片，未解析" ],
  "cost": { "llm_tokens": 152000, "usd": 0.41 },
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

**Estimation**: `--dry-run` reports item count, estimated cost/time without writes.

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
| Common interfaces | Separate LLM/vision/OCR/search/embedding/browser interfaces | Org service choice and easy replacement | Shared capabilities only; special features need adapters |
| Row-level isolation | Shared database with org business-table `org_id` and RLS; approved exceptions in hard rules | Low operations cost; omitted conditions cannot leak data | Very large customers may need separate deployment |
| Resource snapshots | Task locks selected versions | No silent changes | Explicit version switching |
| Layered reviewed memory | Four scopes; automatic proposals need human approval | Avoid amplifying errors/cross-org memory | Slower accumulation |

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
- Aggregate `--dry-run` estimates; ask a human before exceeding task budget/org quota.
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
| Per-document cost/time | Model cost/total time from parse through check | After measurement |

**Dataset**

1. Development: download public hardware tender documents from government procurement sites; annotate requirements.
2. After first usable release: add real bids with consent, using final human deliverables as ground truth.
3. Record dashboard confirmations/rejections as new test samples automatically.

## Security and compliance

Bids contain quotes/customer information; false supporting material can cause rejection or liability. Confidentiality and authentic evidence are mandatory.

**Confidentiality**

- Org-specific object paths, encryption at rest, short-lived signed links.
- Use only model services that do not train on API data; disclose retention policies on configuration pages.
- Optional outbound redaction (遮挡) of quotes, contacts, identity numbers, bank accounts.
- BYOK/local models; private deployment for strict confidentiality.
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
| API | FastAPI | REST, SSE, remote CLI |
| Database | PostgreSQL + pgvector | RLS isolation, memory retrieval |
| Storage | S3-compatible | Cloud storage; MinIO for private deployment |
| Jobs | Procrastinate | PostgreSQL queue; see [queue.py](../server/app/jobs/queue.py); parsing/verification/checks, retry/cancel |
| Auth | Password + OIDC SSO; scoped tokens | Org identity-system integration |
| PDF | PyMuPDF | Text/coordinates, white-paper region rendering |
| Word | python-docx | Parse bids/apply org templates |
| Screenshots | Playwright (BrowserProvider default) | Dynamic pages |
| LLM/vision/embedding/search/OCR | Adapter configuration; defaults remain open decisions | Structured outputs, long context, image understanding, no API-data training |
| Annotation | Rust clap/serde/image/sha2 | Crop/box/watermark/hash; independent job executable |
| Frontend | Vue 3 + Vite + Element Plus | Dashboard/resources/providers/memory |
| Deployment | Docker Compose; Kubernetes later | Same images for SaaS/private |
| Tests/CI | pytest, cargo test, GitHub Actions | Unit/isolation tests, evaluation scripts |

## Implementation phases

Establish isolation before adding capabilities. Org business tables carry `org_id` and RLS from day one, with approved exceptions defined in hard rules.

| Phase | Window | Scope |
| --- | --- | --- |
| 1 | 10/5–11/1 | FastAPI/PostgreSQL RLS/storage foundation; accounts/one org; remote/local CLI; initial `tender parse`, `req extract`, `check`; one LLM/OCR adapter each |
| 2 | 11/2–11/29 | Products/features; tasks/resource selection; `evidence fetch`, `evidence stamp`, `ui mock`, `draft`; search/vision/browser adapters |
| 3 | 11/30–12/27 | Dashboard/internal agent/`score`/`export`/org templates; multi-org/roles/tokens/external agents; model/OCR pages; project/org memory |
| 4 | 12/28–2027/1/24 | User/global memory, audit/usage quotas, `bid mcp serve`, private deployment packaging, polish/demo |

The scope is much larger than originally planned. Phase 4 is the most likely to be reduced: if time is short, global memory, quota billing, MCP and private deployment can wait until after the job search.

These are planning windows, not implementation status. Remaining scope and priorities are maintained in the [roadmap](plan/roadmap.md).

## Open decisions

- [ ] Platform-default LLM/vision/embedding providers: compare Chinese long-document extraction, prices, and data policies.
- [ ] Search API: coverage of domestic vendor sites. Existing explicit selection is defined in [platform credentials](plan/platform-credentials.md#resolution-cache-and-job-boundaries).
- [ ] Platform-default OCR: cloud versus local engine.
- [x] Background queue: Procrastinate; see [queue.py](../server/app/jobs/queue.py).
- [x] Payment: prepaid balance (预付余额)/recharge cards (充值卡密) under [ADR 0002](adr/0002-prepaid-billing.md); current admission/accounting is maintained in [prepaid billing](notes/prepaid-billing.md).
- [ ] Initial global-memory sources and reviewers.
- [ ] Public platform templates and cross-org template sharing.
- [ ] Adapt Word exports to widely varying org templates.
- [ ] Data residency: promise domestic-only storage?
- [ ] Automatically import org feature lists from existing requirements/product documents?
