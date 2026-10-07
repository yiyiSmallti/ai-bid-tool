---
kind: readme
---

# Documentation index

Documentation follows the [Seiso Convention 0.2.0](https://seiso.fog.moe/0.2.0/convention):
each page declares one `kind`, and each fact has one home. Kind mappings are in
[seiso.toml](../seiso.toml). Canonical terminology is defined in [the glossary](glossary.md).

## Design and rules

- [Design](design.md) (plan): the authoritative complete design.
- [agent.md](../agent.md) (agents): hard rules and workflow.
- [Glossary](glossary.md) (reference): English renderings of domain terms.

## Guides

- [Local development and verification](guides/development.md): tests, development database, API and worker, containers, OCR and local vendor-source search.
- [bid CLI](guides/cli.md): login, tender workflow, resource maintenance, vendor (厂家) evidence (证据) capture, result handling and tokens.
- [Sandbox runtime setup and acceptance](guides/sandbox-runtime.md): Colima/Docker, runsc, Unix/mTLS control channels, open capture networking on development nodes, and repeatable verification steps.

<a id="机制笔记"></a>

## Mechanism notes

Notes use the structure required by [the agent workflow](../agent.md#workflow).

- [Built-in agent orchestration](notes/builtin-agent.md): bounded command tools, owner authority, durable pauses and recovery.
- [platform-credentials.md](notes/platform-credentials.md): platform credential authority, resolution before each call, import and root-key rotation.
- [tenant-isolation.md](notes/tenant-isolation.md): RLS, org (organization/tenant; 单位) context and file isolation.
- [memory.md](notes/memory.md): human approval of org memory (记忆), keyword retrieval, per-call traceability, cache invalidation and feedback candidates.
- [requirement-confirmation.md](notes/requirement-confirmation.md): independently reviewed requirements, verified manual recovery, source-bound acceptance and stale-output gates.
- [response-cards.md](notes/response-cards.md): human response cards (响应卡), evidence confirmation, atomic disposition and three-table drafts (初稿).
- [console-assessments.md](notes/console-assessments.md): bounded check/score console reads, human review, budget preflight and complete rubric replacement.
- [check.md](notes/check.md): deterministic risk checks on confirmed drafts, certificate (证书) dates, citations and human false-positive decisions.
- [task-budgets.md](notes/task-budgets.md): task liability, atomic reservations, human changes and low-balance notices.
- [score.md](notes/score.md): two-stage scoring-rubric generation, fixed inputs, human classification, coverage decisions, per-item confirmation and immutable history.
- [model-drafting-redaction.md](notes/model-drafting-redaction.md): model-drafting input snapshots, outbound redaction (遮挡) and dual-text citation validation.
- [confidential-values.md](notes/confidential-values.md): confidential fields (保密字段), outbound placeholder substitution and filling values at export.
- [background-jobs.md](notes/background-jobs.md): job states, cancellation and retries.
- [pdf-parsing.md](notes/pdf-parsing.md): PDF rasterization budgets, mixed-page OCR, verifiable text merging and parsing warnings.
- [llm-providers.md](notes/llm-providers.md): model calls, batching, errors and billing for extraction and response (响应) drafting.
- [docx-citations.md](notes/docx-citations.md): Word citations by section, paragraph and table cell.
- [reasoning-levels.md](notes/reasoning-levels.md): official reasoning levels and extraction history.
- [platform-console.md](notes/platform-console.md): platform operator console (平台运营后台), TOTP, org activation/deactivation and model-catalog billing.
- [prepaid-billing.md](notes/prepaid-billing.md): prepaid balance (预付余额), recharge cards (充值卡密), charges and admission blocks.
- [versioned-resources.md](notes/versioned-resources.md): product revisions and task (任务) snapshots.
- [model-settings.md](notes/model-settings.md): metadata-only org model settings, immutable identity and explicit connection tests.
- [management-pages.md](notes/management-pages.md): bounded product library reads, independent lifecycle and explicit revision pinning.
- [versioned-features.md](notes/versioned-features.md): software feature declarations.
- [versioned-certificates.md](notes/versioned-certificates.md): certificate declarations and date checks.
- [versioned-profiles.md](notes/versioned-profiles.md): org profile (单位资料) declarations.
- [versioned-templates.md](notes/versioned-templates.md): private DOCX templates.
- [versioned-certificate-files.md](notes/versioned-certificate-files.md): original certificate PDFs.
- [unconfirmed-evidence-sources.md](notes/unconfirmed-evidence-sources.md): unconfirmed PDF-page sources.
- [provider-config.md](notes/provider-config.md): org model revisions, separate key encryption, model resolution, call billing and provider balance.
- [human-section-exports.md](notes/human-section-exports.md): human export of Word response sections, fixed manifests, candidate and release gates, evidence-page attachments and signed downloads.
- [team-workflow.md](notes/team-workflow.md): task membership, archival, bounded board snapshots, durable progress and reviewed authorization cutover.
- [org-console.md](notes/org-console.md): org task recovery, parsing and extraction, human response-card review, drafting cost previews and the draft-gap (缺口) console.
- [product-simulation.md](notes/product-simulation.md): simulated proposals (模拟拟投), product selection by procurement item, official-page search and capture, verbatim parameter extraction and simulated-material markings.
- [page-previews.md](notes/page-previews.md): online page previews of original tender documents (招标文件), original certificates and exports, and export conversion.
- [sandbox-execution.md](notes/sandbox-execution.md): isolated execution, job authorization, artifact provenance, cleanup and downloads.
- [sandbox-fetch.md](notes/sandbox-fetch.md): allowlist and open-development policies, DNS/IP pinning, fetch quotas, individual-resource denial and trusted request receipts.
- [annotation.md](notes/annotation.md): source-bound cloud certificate-page candidates, exact-approval confirmed releases, and explicit human attachment.
- [screenshot-evidence.md](notes/screenshot-evidence.md): screenshot pixel redaction and privacy clearance, vendor web/PDF capture and archival, vendor-source search, response-card image evidence, prototype (原型) delivery decisions, analysis admission and billing, and invalidation/recomputation.

## Decision records

- [0001 Cross-org access for the platform operator console](adr/0001-platform-console-access.md) (adr)
- [0002 Prepaid balance and recharge cards](adr/0002-prepaid-billing.md) (adr)
- [0003 Structural citations for Word tender documents](adr/0003-word-structural-citations.md) (adr)
- [0004 Official reasoning levels and independent extraction history](adr/0004-extractions-per-reasoning-level.md) (adr)
- [0005 Response cards separating model proposals from human confirmation (人工确认)](adr/0005-human-confirmed-responses.md) (adr)
- [0006 Platform-managed service credentials](adr/0006-platform-credentials.md) (adr)
- [0007 Task liability and prepaid reservations](adr/0007-task-budget-reservations.md) (adr)

## Plans and records

Implementation and acceptance status is maintained on each plan page; the
[roadmap](plan/roadmap.md) links the remaining work and dependencies.

- [Remaining scope and roadmap](plan/roadmap.md) (plan): coverage matrices, known defects and open decisions.
- [Cloud annotation contract](plan/annotation.md) (plan): archived certificate pages, bounded server rendering, human response review and confirmed image releases.
- [Org model configuration contract](plan/provider-config.md) (plan): org BYOK models and platform-model selection.
- [Export contract](plan/export.md) (plan): human export of deviation (偏离) tables and evidence attachments using org Word templates; Word/WPS visual pagination acceptance.
- [Screenshot and evidence-image contract](plan/screenshots.md) (plan): screenshots, vendor-material capture, redaction, model prototypes and response evidence.
- [Sandbox contract](plan/sandbox.md) (plan): isolated execution for untrusted generated content and built-in agents; isolation, proxy and lifecycle acceptance.
- [Org console contract](plan/org-console.md) (plan): web tender, extraction and response-card review workflows.
- [Drafting preview-binding contract](plan/drafting-binding.md) (plan): binding paid drafting to a preview hash and a user spending ceiling.
- [Draft checking contract](plan/check.md) (plan): deterministic rules and semantic checks.
- [Scoring contract](plan/score.md) (plan): rubric normalization and human confirmation, scoring of confirmed DraftRuns and reports.
- [Memory contract](plan/memory.md) (plan): the first org-scope slice and boundaries for later scopes.
- [Confidential-field contract](plan/confidential-values.md) (plan): registered confidential values and export-time substitution.
- [Platform credential contract](plan/platform-credentials.md) (plan): credential management, resolution, cutover and acceptance.
- [Built-in agent contract](plan/agent.md) (plan): command orchestration, human pauses, recovery and shared provenance.
- [Budget contract](plan/budget.md) (plan): task budgets and cost preflight.
- [Changelog](changelog.md) (changelog): shipped scope.
