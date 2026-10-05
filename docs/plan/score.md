---
kind: plan
---

# Itemized score-estimation contract for confirmed response drafts

Status: **Approved; stages A and B implemented.** This contract corresponds to [roadmap](roadmap.md) B10. Stage A covers two-stage rubric generation, normalization, human classification, item-level confirmation, and set-level confirmation. Stage B covers current `DraftRun` scoring, immutable reports, and scoring Provider jobs. Implemented scope is recorded in the [changelog](../changelog.md#2026-10-05-two-stage-score-rubric-generation). Pydantic and Provider contracts live together in the [runtime contract](../../server/app/schemas/score_contracts.py). Shared assessment, citation, outbound, and Result conventions follow the [approved B09 contract](check.md), with types from the [shared contract](../../server/app/schemas/check_contracts.py).

## Goals and conclusion boundaries

Stage B initially produces a “confirmed response draft score estimate”: compare a human-confirmed scoring rubric item by item against a specified current `DraftRun`, returning scores for assessable items, point deduction (扣分) reasons, strengthening actions, and verbatim citations. It supports judgment; it is not the purchaser's (招标人) official score or a review of the full bid (标书), layout, attachment completeness, or final delivery file.

Scoring has two stages:

1. Read only `Category.scoring` Requirements from one explicitly selected successful extraction job to generate versioned rubric candidates. After confirming items individually, a human confirms completeness of the entire rule set.
2. Use only a fully confirmed rubric to score one explicitly selected, still-current `DraftRun`. Model output must pass local boundary, citation, and aggregation checks; results are always advisory.

Existing `Requirement.condition` is a free `dict`, without a rubric contract. The first version neither uses it to determine points, weights, bounds, or formulas nor sends it to the scoring Provider. Rubric candidates rely only on pinned text and verified `Source` for the selected scoring Requirements. They cannot rewalk Chunk, scan complete tender documents (招标文件), or secretly start extraction. To reverify citations, the service reads only the chunk/block referenced by `Source.chunk_id` and locates its contiguous original text; it cannot scan adjacent Chunk objects or discover new scoring items. “Complete” means covering the scoring Requirements saved by that extraction job, never that extraction missed nothing in the original tender documents.

## Pinned input

### Rubric candidate input

`RubricInput` pins `org_id`, `task_id`, `extraction_job_id`, `document_id`, and `input_hash`. The service verifies extraction Job success and that Job, Document, and Requirement belong to the same org (organization/tenant; 单位), task, and document. Selection includes every `Category.scoring` Requirement under that Job; callers cannot select a subset and still request a “complete rubric.”

Generation uses two ordered stages. Stage 1 receives the whole fixed scoring table in one request, without splitting. Admission enforces `BID_RUBRIC_MAX_REQUEST_BYTES` against the entire serialized HTTP request body. If it exceeds the limit, preview reports the capacity blocker and submission is refused before any Provider call. Stage 1 proposes only section keys, titles and order, section aggregation, section bounds/caps/weights/inclusion, review domains (职责), and the overall aggregation rule/bounds. It sees every scoring Requirement. Each proposed section and the overall rule must carry verified citations; stage 1 does not propose item order, and is not required to cite every Requirement. The service validates this structure and all citations against the complete Requirement/ref allowlist before admitting stage 2. An invalid or failed stage 1 fails the job and makes no item-generation calls.

Stage 2 generates item details in Requirement batches using `llm_batch_chars`; batch sizing and concurrency are implemented in the Provider. Every batch receives the entire fixed stage-1 section list as immutable context, with its structure hash bound into the call/input manifest. Stage 2 may produce item detail but cannot revise that structure. Unknown sections, cross-batch references, or unresolved Requirements are rejected and remain unresolved. A failed stage-2 batch can leave other valid sections/items retained as a partial result (exit 5), including validated sections even when no item is accepted.

The write-free preview upper bound covers both stages, including the entire fixed section list repeated in each stage-2 batch. Stage-specific prompt and schema versions participate in the preview cache/input identity. Old queued jobs with incompatible versions fail explicitly and require resubmission. The published rubric `input_hash` derives from the manifest containing stage-1 output and its structure hash; the queued job `cache_key` remains the preview input, and submission stores `preview_input_hash` as provenance. No migration is required. Provider input remains limited to pinned scoring text, source location and local refs, never arbitrary `condition`, complete tender documents, other requirement categories or resource-library content.

### Scoring input

`ScoreRequest` adds only `rubric_id` to shared `AssessmentRequest`. The snapshot must pin:

- The specified current `DraftRun`'s `org_id`, `task_id`, `draft_id`, `extraction_job_id`, `document_id`, and `draft_input_hash`. DraftRun and confirmed rubric must pin the same extraction job/document.
- Partition metadata for every `ResponseItem`: `response`, `comply_only`, or `gap`, plus requirement, source, gap (缺口) reason, disposition, and pinned revision bindings. The scoring item's own row is `anchor_response_item`; its partition is `anchor_partition`.
- Every `response` row in the same DraftRun as candidate support, sending only its pinned, confirmed `response_text` and `deviation_note`. Never read current Card pointers, unconfirmed/pending/rejected Card text, or substitute later Card changes. This lets a scoring item cite real support from other confirmed technical/commercial responses (响应), rather than assuming support has the scoring Requirement's ID.
- Only necessary metadata and tender-side text for `comply_only`/`gap`, without generating or borrowing card text. A gap anchor does not prove the entire draft lacks materials; assessment is possible only when verbatim text in other confirmed responses genuinely supports it. `comply_only` has no bid-side text and cannot independently earn points; without any confirmed bid-side citation, it is always unassessable.
- Confirmed rubric set/section/item versions, coverage decisions, bounds, weights, and aggregation rules.
- `assessment_date`, Provider/platform model catalog revisions, reasoning, prompt/schema/scoring rule versions, and redaction setting/rule revisions.

The first version reads no released/review DOCX, export runs, Gotenberg PDF, template text, or page images. Export fills actual confidential values into human-only downloads; agents/models cannot download them. Scoring released files would breach both human and confidentiality gates. A future need requires a separate “human-session-only released-export review” contract with explicit authorization, outbound, file-citation, fee, and audit decisions, never implicit expansion of this command's input.

## Rubric versions, coverage, and human confirmation (人工确认)

Providers create candidates only. Candidate sections/items pin original Requirement/Source; citations must uniquely and contiguously match both actual sent text and pinned sources. Unknown, ambiguous, joined, or redacted citations are not repaired; affected candidates remain unresolved and cannot enter confirmed sets.

Each scoring Requirement needs one `RubricRequirementCoverageView`:

- `mapped`: explicitly linked to one or more rubric items; split items each require confirmed boundaries and points.
- `duplicate`: a human names the canonical Requirement and supplies a reason; models cannot silently deduplicate.
- `excluded`: a human explains why the Requirement is not scoreable; source and decision remain.
- `pending`: undecided; blocks set confirmation.

Rubric items save normalized rule text, section, order, assessment mode, score bounds, optional weight, review domain (职责), source, and content fingerprint. Sections save their own bounds, weight, cap, overall inclusion, review domain, and aggregation method: `sum`, `weighted_sum`, `capped_sum`, `formula`, or `non_additive`. Before set confirmation the service checks deterministically:

- All scoring Requirements have non-pending coverage decisions; item/section keys, order, and fingerprints have no unresolved duplicates; citations still bind the same pinned sources.
- Each section/item has `review_domain` and confirmation from that domain's reviewer; rejected items cannot remain in the set. bidder handles commercial items; technical handles technical items. Classification does not reuse existing Card-only endpoints: this contract adds section/item classify requests, executable only by admin human sessions, with nonempty reason and expected revision/hash. Classification grants no cross-domain confirmation authority to admins. Confirmation requests accept no `review_domain`; authorization uses stored classification, never caller-supplied replacement domains.
- Every declared bound satisfies `0 <= minimum <= maximum`; weights are valid; item→section and section→overall inclusion has no cycles, duplicates, or dangling references.
- Section totals/caps/weights and section→overall aggregation are explicit, with mechanically verifiable bounds/totals consistent. Genuine tender ambiguity can be explicitly confirmed as `ambiguous`, without inventing numeric rules; such items are always unassessable during scoring.

If candidate titles, rules, bounds, weights, caps, aggregation, or review domains are wrong, a human submits a complete replacement snapshot through `RubricReviseRequest`, with expected revision/input hash. It can reference only Requirements in the pinned input and prior-version sections/items, never rewrite Source. The service creates a new candidate version/new IDs, retaining `prior_rubric_id` and revision reason; old versions/decisions remain intact. New versions repeat classification, coverage, item, and set confirmation. Revision input accepts no `review_domain`; all new section/item domains reset to null and require admin reclassification, preventing inherited/self-assigned domains from bypassing gates. Decision, classification, coverage, and revision history are append-only and paginated through history GET.

A set becomes confirmed only when `completeness.complete=true`, no normalization errors exist, and all item gates are complete; score accepts only confirmed sets. `formula`/`non_additive` can enter a complete rubric as fully recorded, human-confirmed rules, but have `aggregation_assessable=false`; they are neither executed nor blockers to rubric completeness. Their section/overall scores always remain `unavailable`.

### First-version aggregation algorithms

The service executes only these three deterministic algorithms, using `Decimal`, without intermediate rounding. Final section (正式件)/overall results use `0.00000001` with `ROUND_HALF_UP`:

- `sum`: add included scores; add their minimums and maximums separately for possible ranges.
- `weighted_sum`: weights are decimal proportions in `(0,1]`, not percentage strings. Included children under one aggregation node must sum exactly to `1.00000000`; compute `sum(score * weight)` and corresponding ranges.
- `capped_sum`: compute `sum`, then `min(sum, cap)`; cap is required and nonnegative, and also applies to ranges.

A section's `weight` is used only by overall `weighted_sum`; an item's `weight` only by its section's `weighted_sum`. Unneeded weights for other algorithms, missing caps, duplicate inclusion, weights not summing to 1, or declared bounds inconsistent with algorithm results produce normalization errors. `formula`/`non_additive` save only verbatim rules and limitation reasons; arbitrary formula strings are neither parsed nor executed.

## Scoring semantics and aggregation

The first version provides `estimated_score` only for `assessment_mode=model_assessable` items with sufficient rules, bounds, and input evidence (证据). These items must be explicitly `unassessable`, retaining reasons and strengthening actions without guessed scores:

- Ambiguous scoring text/bands, incomplete rubric formulas, or formulas unsupported by this version.
- Any price-comparison item or any dependence on other bidders (投标人), reviewer rankings, reference prices, live demonstrations, subjective impressions, external rankings, or third-party data absent from current input.
- No confirmed response provides verbatim bid-side support; required attachments/proof are replaced by thin unsupported commitments; or actual sent text and pinned originals cannot support the conclusion.

A pure commitment is scoreable only when the confirmed rubric explicitly states commitment text alone earns points. Rules requiring certificates, reports, screenshots, parameters, performance records, or attachments cannot receive full marks solely from thin claims such as “满足、完全响应、可提供”. Model scores must lie within confirmed item bounds and have at least one locally verified tender citation and one confirmed bid-side citation from the current DraftRun; tender citations alone cannot earn points. Report `response_item_ids` lists only verified response rows actually supporting the score; it is empty for unassessable items. Invalid citations are never automatically reassigned to other sources. Scores below confirmed item maximum require at least one `deduction_reasons` entry. Strengthening actions may be empty but must not recommend fabricated certificates/reports/screenshots/parameters or pricing strategies excluded by the design.

Every section/report always outputs `assessed_subtotal`, explicitly named as the assessed-item subtotal. `estimated_score`/`estimated_total` appear only when all included items are assessed, all aggregation rules execute deterministically, all Provider batches are complete, and local validation passes. Any included unassessable child or non-executable aggregation makes total status `unavailable` and `estimated_total` null; confirmed bounds may allow `possible_range`. Never rename a partial subtotal as a total or set unassessed items to zero to manufacture one.

Report reads recompute `validity`. Non-current DraftRun, superseded rubric, invalidated pinned Card revision confirmation, or redaction/input dependency changes mark reports stale with invalidation codes. Historical reports cannot be rewritten.

## Citations, outbound calls, and confidentiality

Provider requests use shared `OutboundContext`: each local ref in `texts` is unique; `confidential_fields` contains only placeholders, names, and categories. Outbound processing follows [model drafting and redaction](../notes/model-drafting-redaction.md): registered values become `{{secret.<key>}}` before versioned redaction rules; original materials/DraftRun remain intact. Pinned manifests include actual confidential field (保密字段)/value-row IDs, SHA-256 of every actual sent text, and the full input hash. Even without redaction revision changes, a new confidential-value version changes redacted output, forming new input and rejecting old previews.

Models return only `ModelEvidenceRef`. The service resolves refs against the current batch's sent-ref allowlist and verifies quotes verbatim in both sent text and pinned originals. Persistence uses shared `VerifiedCitation`. The initial score profile accepts only `TenderCitation` and `DraftCitation` bound to confirmed ResponseItem in the current DraftRun. Though shared types include `EvidenceCitation`, this version rejects it because evidence text is absent from outbound scoring input. Model-generated UUIDs, URLs, paths, selection/revision numbers, or unknown refs cannot create bindings; invalid refs record only sanitized summaries without echoing arbitrary model strings.

The rubric Provider receives only redacted tender-text refs. Each score Provider item must explicitly receive `tender_ref`, confirmed `rule_ref`, and candidate `draft_refs` from all confirmed responses in that DraftRun. `rule_ref` is human-normalized reasoning text and cannot generate `TenderCitation`; real tender quotations come only from `tender_ref`. Before publishing assessed results, services separately validate tender/rule/draft refs and require nonempty model tender and draft citations. Rubric/historical-report reads re-resolve every Source, DraftRun, ResponseItem, and Card revision parent under current org/task scope. Unauthorized/missing objects uniformly return 404; invalid dependencies make reports stale and block new scoring. Saved Source JSON cannot bypass current read permissions. Original-source verification reads only the referenced chunk/block, never scanning the full document or producing new Requirements.

Actual confidential values never enter score snapshots, prompts, Provider errors, Job results, UsageRecord, audit, or reports. Disabling redaction remains an existing human org-admin, revision-checked, audited task setting, but B10 is stricter than drafting: rubric/score dry-run returns `admission_blocker=redaction_required` when disabled; submission rejects it and request parameters cannot override it. Settings revision changes stop later calls and require fresh preview/submit. Allowing org-owned/local models with disabled redaction requires separate approval.

## Providers, jobs, and prepaid billing

The [runtime contract](../../server/app/schemas/score_contracts.py) defines these structured Provider methods:

- `RubricProvider.extract_structure`: complete pinned scoring Requirements → candidate sections and overall rule.
- `RubricProvider.extract_items`: bounded Requirement batches plus the complete fixed section list → candidate items.
- `ScoreProvider.score`: confirmed rubric items, corresponding DraftRun partitions, and outbound context → item-level assessed/unassessable results, scores, reasons, strengthening actions, and refs.

Only implementations in `server/app/providers/` call vendor SDKs/HTTP, using strict JSON Schema; business services depend on Protocols only. `RubricProvider.extract_structure` sends the complete Requirement table once for structure; `RubricProvider.extract_items` performs bounded Requirement batching and concurrency, with the complete fixed section list in every batch. Stage-1 structure must validate before stage 2 begins; stage-2 items cannot change its keys, ordering, or aggregation. Prompt/schema versions bind preview cache identity; the structure hash binds the published input manifest. The preview accounts for both stages and the entire repeated section context. Invalid/truncated stage 1 fails without stage-2 calls; stage-2 structural errors or batch failures leave unresolved Requirements and may retain independent valid batches, including sections without accepted items. Score adapters retain completed batches and per-call `ProviderUsage`; the first nonrecoverable failure stops unstarted batches, while in-flight calls may finish and be charged. Default scores, empty citations, or broad retries cannot conceal failures.

Rubric/score Job kinds are respectively `score_rubric` and `score`. Both require non-null `task_id`/`document_id`; score's document comes from the real Document pinned by DraftRun's extraction Job, never a placeholder. Execution reuses `JobExecution.activate/owned_job/admit/_complete_once`: attempts hold lease/run_id and revalidate all pinned inputs before publication. Old runs cannot publish after takeover, expiry, cancellation, or settings changes.

Provider resolution, pinned identity, and fee rules reuse [B09 jobs and billing](check.md#jobs-provider-and-billing). `resolve_llm` may select org-owned configuration or platform defaults; snapshots pin `provider_config_id`, `provider_source`, and provider identity, never only the platform catalog. Every actual call uses VendorCall/call ceiling and writes `UsageRecord` under `(org_id, job_id, run_id, call_id)`. Only platform-paid calls reserve positive amounts at catalog prices and deduct prepaid balance (预付余额) through `billing.charge_usage` in the same transaction; sent requests with unknown outcomes retain reservations. Org-owned keys have reservation/platform charge=0, skip prepaid deductions, and retain null vendor `usd` when unknown. `max_charge` bounds only platform selling-price charges, not org-paid vendor bills. Cancellation, rejected citations, invalid model content, or partial reports never erase incurred usage. Services cannot implement separate billing.

`--dry-run` performs snapshots/authorization for the same scope without business/audit/Job writes or Provider calls, returning first-pass cost bounds for both rubric stages or the score run, model catalog, and redaction summaries. Submission requires the preview's `expected_input_hash`. `max_charge` is only this Job's platform-charge cap, not an org-owned-key vendor-bill limit. Calls use shared per-call admission and settlement; task-budget controls are defined by the [budget contract](budget.md), rather than a score-specific budget implementation.

Cache keys include at least org/task/document/extraction/draft/rubric IDs and pinned versions, input hash, assessment date, Provider/model catalog revisions, reasoning, and prompt/schema/rule/redaction versions. Identical keys can return cached Jobs; `retry=true` retries only pinned input, never selecting “latest” DraftRun/rubric.

## HTTP, CLI, and Result

Interfaces register by stage; HTTP/CLI use identical services and Pydantic models. Complex revision/decision/classify input uses UTF-8 JSON files with `RubricReviseRequest`, `Rubric*DecisionRequest`, or `RubricClassifyRequest`; CLI has no interactive prompts.

Stage A registers these rubric entry points:

| HTTP | CLI (all support `--json`) | data / items |
| --- | --- | --- |
| `POST /tasks/{task_id}/score-rubrics/preview` | `bid score rubric generate --task UUID --extraction-job UUID [--reasoning LEVEL] [--max-charge DECIMAL] --dry-run --json` | `RubricPreview` / `[]` |
| `POST /tasks/{task_id}/score-rubrics` | `bid score rubric generate --task UUID --extraction-job UUID --expected-input-hash SHA256 [--reasoning LEVEL] [--max-charge DECIMAL] [--retry] [--wait] --json` | `RubricJobAccepted`; after wait, `RubricGenerateResult` / `[]` |
| `GET /tasks/{task_id}/score-rubrics?cursor=…&limit=…` | `bid score rubric list --task UUID [--cursor CURSOR] [--limit N] --json` | `AssessmentListData` / `RubricSetView[]` |
| `GET /tasks/{task_id}/score-rubrics/{rubric_id}` | `bid score rubric show --task UUID --rubric UUID --json` | `RubricReportData` / `[]` |
| `POST /tasks/{task_id}/score-rubrics/{rubric_id}/revisions` | `bid score rubric revise --task UUID --rubric UUID --input PLAN.json --json` | `RubricReportData` / `[]` |
| `POST /tasks/{task_id}/score-rubrics/{rubric_id}/sections/{section_id}/classification` | `bid score rubric classify --task UUID --rubric UUID --section UUID --input DECISION.json --json` | `RubricClassificationView` / `[]` |
| `POST /tasks/{task_id}/score-rubrics/{rubric_id}/items/{item_id}/classification` | `bid score rubric classify --task UUID --rubric UUID --item UUID --input DECISION.json --json` | `RubricClassificationView` / `[]` |
| `POST /tasks/{task_id}/score-rubrics/{rubric_id}/sections/{section_id}/decisions` | `bid score rubric section decide --task UUID --rubric UUID --section UUID --input DECISION.json --json` | `RubricDecisionView` / `[]` |
| `POST /tasks/{task_id}/score-rubrics/{rubric_id}/items/{item_id}/decisions` | `bid score rubric item decide --task UUID --rubric UUID --item UUID --input DECISION.json --json` | `RubricDecisionView` / `[]` |
| `POST /tasks/{task_id}/score-rubrics/{rubric_id}/coverage/{requirement_id}/decisions` | `bid score rubric coverage decide --task UUID --rubric UUID --requirement UUID --input DECISION.json --json` | `RubricCoverageDecisionView` / `[]` |
| `POST /tasks/{task_id}/score-rubrics/{rubric_id}/decisions` | `bid score rubric decide --task UUID --rubric UUID --input DECISION.json --json` | `RubricSetView` / `[]` |
| `GET /tasks/{task_id}/score-rubrics/{rubric_id}/history?cursor=…&limit=…` | `bid score rubric history --task UUID --rubric UUID [--cursor CURSOR] [--limit N] --json` | `AssessmentListData` / `RubricHistoryItem[]` |

Stage B registers these scoring entry points:

| HTTP | CLI (all support `--json`) | data / items |
| --- | --- | --- |
| `POST /tasks/{task_id}/scores/preview` | `bid score run --task UUID --draft UUID --rubric UUID --as-of YYYY-MM-DD [--reasoning LEVEL] [--max-charge DECIMAL] --dry-run --json` | `ScorePreview` / `[]` |
| `POST /tasks/{task_id}/scores` | `bid score run --task UUID --draft UUID --rubric UUID --as-of YYYY-MM-DD --expected-input-hash SHA256 [--reasoning LEVEL] [--max-charge DECIMAL] [--retry] [--wait] --json` | `ScoreJobAccepted`; after wait, `ScoreJobResult` / `[]` |
| `GET /tasks/{task_id}/scores?cursor=…&limit=…` | `bid score list --task UUID [--cursor CURSOR] [--limit N] --json` | `AssessmentListData` / `ScoreRunView[]` |
| `GET /tasks/{task_id}/scores/{report_id}` | `bid score show --task UUID --report UUID --json` | `ScoreReportData` / `[]` |

Actual path prefixes follow existing API routers; this fixes relative resource structure. Pagination limit defaults to 50, range 1–200; cursors bind org, task, resource kind, and ordering. Path `task_id` must match extraction/draft/rubric/report task or uniformly return 404. Long-running submissions return Jobs immediately by default; `--wait` reuses existing wait/status queries.

All success/failure/`--json` output uses the seven-key Result from `contracts.CONTRACT_VERSION`: `ok`, `command`, `data`, `items`, `warnings`, `cost`, `duration_ms`. `cost` is provable actual usage for that response; preview `estimated_cost/estimated_charge` in data cannot masquerade as incurred fees. Schema adds these commands without changing existing commands; `bid schema` publishes implemented rubric/scoring commands.

| Exit code | Explicit score semantics |
| --- | --- |
| 0 | Successful preview/submission/list/complete rubric or report read; rubric wait is 0 only for complete candidates, score wait only when all items are assessable and a complete total exists |
| 2 | Parameter/date/UUID/expected hash or revision errors, non-current DraftRun, unconfirmed rubric, cross-extraction bindings, or input integrity errors |
| 3 | Retryable Provider/queue/network/temporary storage failure before a persistent report exists |
| 4 | Identity/permission, missing resource, content refusal, pinned-input/billing/citation integrity, or nonretryable Provider failure |
| 5 | Valid rubric/report retained but rubric has unresolved requirements, or scoring has failed batches, unassessable items, nonaggregatable sections, or therefore no complete total |

Asynchronous acceptance itself returns 0. `job wait`/`score show` consistently return 5 for partial reports, unassessable items, or missing complete totals, without another switch. Cross-org/unauthorized resources remain uniform 404; errors never echo original/outbound text, raw model output, or vendor error bodies.

## Permissions and human gates

Permissions use approved `score:read`, `score:run`, `score:rubric:generate`, and `score:rubric:review` mappings; execution uses only the first two:

- `score:read` is grantable to the four existing org roles and may enter the token allowlist, still intersected with Membership/task-read permissions.
- `score:run`/`score:rubric:generate` are granted to admin/bidder/technical, matching `check:run`. Only admins issue tokens, within their own permissions. These scopes may explicitly enter the token allowlist for external-agent previews/advisory submissions, never human decisions.
- `score:rubric:review` belongs only to logged-in human sessions, not token `SCOPES`. DB CHECK/triggers and services both reject token/agent/worker actors. technical confirms/revises technical content only; bidder commercial content only. In complete replacement snapshots, other domains' content must remain verbatim identical to the prior version. bidder confirms the entire set. admin performs section/item classify only, with no default revision/cross-domain confirmation rights. history follows `score:read`.
- Score reports cannot automatically confirm cards, modify DraftRun, write exports, fill confidential values, or publish final scores. Internal/external agents/tokens cannot turn advisory output into human decisions.

Routes validate session/token scopes and valid Membership before setting `app.current_org`. Workers carry submission org and read only within that RLS context; BYPASSRLS roles are forbidden.

## Tables and migration outline

Business tables also land by stage. Names may be adjusted during implementation migration review; constraints cannot weaken:

| Table | Purpose and pinned fields |
| --- | --- |
| `score_rubric_sets` | task/extraction job/document, version, input hash, rule/prompt/schema versions, overall rules, status, confirmer/time |
| `score_rubric_sections` | rubric/source/key/order, aggregation, bounds, weight, overall inclusion, status/revision |
| `score_rubric_items` | rubric/section/requirement/source, fingerprint, rules, mode, bounds, weight, domain, status/revision |
| `score_rubric_coverage` | mapped/duplicate/excluded/pending per scoring Requirement, canonical binding/revision |
| `score_rubric_coverage_items` | Normalized composite FK relations from mapped coverage to one or more rubric items, never pretending JSON item IDs enforce constraints |
| `score_rubric_decisions` | Append-only human section/item/set decisions, reason hash, revision, session actor/time |
| `score_rubric_coverage_decisions` | Append-only human coverage mapped/duplicate/excluded/reopen decisions |
| `score_rubric_classifications` | Append-only admin human section/item review-domain classifications |
| `score_rubric_revision_events` | Append-only link between new candidate/prior rubric, reason, and human actor |

These rubric tables belong to stage A. Report tables belong to stage B:

| Table | Purpose and pinned fields |
| --- | --- |
| `score_reports` | job/run, AssessmentInput, pinned rubric version, rule version, completion/validity, aggregation status/usage IDs |
| `score_report_items` | report/rubric item/requirement/anchor ResponseItem, anchor partition, outcome, score, reasons, strengthening actions |
| `score_report_item_responses` | Normalized composite FK relations from assessed items to one or more confirmed ResponseItems actually supporting scores |
| `score_item_citations` | Structured report-item bindings to verified tender/draft citations |

Each table has `org_id UUID NOT NULL`, `task_id UUID NOT NULL`, and both `ENABLE ROW LEVEL SECURITY`/`FORCE ROW LEVEL SECURITY` in the same migration. Policies accept only the exact org in `current_setting('app.current_org', true)`. Each table has `(org_id,id)` and necessary `(org_id,task_id,id)` unique keys. Public coverage views contain id/org/task/rubric/revision; item IDs are authorized aggregates from `score_rubric_coverage_items`. Every Task, Document, Job, Requirement, DraftRun, ResponseItem, rubric/report/decision/citation relation uses composite FKs containing `org_id`; task-scoped chains also contain `task_id`, letting the DB reject cross-org/cross-task joins. Required business fields are NOT NULL; nullable fields are only unavailable scores/bounds/confirmation actor-time/optional reasons, with paired CHECK constraints.

Migrations also require status/numeric/actor-kind CHECK; unique rubric versions, section keys, item keys/fingerprints, coverage Requirements, and report+rubric items; DB gates requiring all confirmed items/sections for confirmed sets; append-only decision/classification/revision/citation permissions; existing org-deletion policy. SQL/aggregate views must use security-invoker/current org scope, never owner-based RLS bypass. APIs may instead assemble Pydantic aggregate views after authorized queries. Jobs reuse existing tables with explicit kind/cache/result schema/processor additions; UsageRecord, VendorCall, AuditLog, and balance tables are not duplicated.

Migrations and their implementation changes must include end-to-end acceptance for orgs A/B, missing org context, cross-task composite FKs, and normal runtime roles unable to bypass FORCE RLS. Every new table, public/aggregate view, and route verifies A cannot read/write B. Implement migration/models first, then service/provider/job, API/CLI/schema, and finally console; no stage may temporarily store candidates/reports without RLS.

## Audit

Fixed successful events are `score_rubric.submitted/completed/cancelled/revised/classified/decided` and `score.submitted/completed/cancelled`. Fixed failure events are `score_rubric.failed`, `score_rubric.decision_denied`, and `score.failed`. Persistent audit defaults to authenticated actions with resolved org that entered business boundaries. Pre-auth, uniform cross-org 404, and refusals lacking safe object bindings use existing security logs only. Metadata contains only org/task/object, actor kind/id, revision, input/reason hashes, stable error code, and usage IDs, never outbound text, confidential values, raw model output, or vendor error bodies.

Dry-run is strictly write-free and creates no AuditLog. Cached hits return existing Jobs/reports without new submitted/completed/usage audits. Pre-auth/cross-org 404 stay in security logs, not persistent AuditLog; these event/metadata boundaries are settled.

## Evaluation basis

Provider-matching experiment source limits, supported conclusions, and prohibited claims live in [B09 stage two evaluation basis](check.md#stage-two-evaluation-basis). B10 inherits only conservative constraints: local verbatim verification for all model citations; missing/unknown never defaults to full marks; matching experiments are neither real tender scores nor reproducibility evidence. This page does not repeat sample statistics.

## Staged acceptance

Repository rules require end-to-end acceptance only, without model-class unit tests that repeat implementation. Test Providers use structured fakes; real-service evaluations live in `evals/`, outside default CI. Cover at least:

Stage A covers rubric-related migration, HTTP, CLI, jobs, fees, and human gates in items 1, 2, 5, and 6. Stage B covers reports, ScoreProvider, aggregation, and the remaining items. Stage A completion cannot substitute for stage B scoring acceptance.

1. Two orgs, missing org context, cross-task/document/draft/rubric FKs, FORCE RLS; reject invalid token scopes/Membership, mismatched domains, and agent confirmation attempts. 404 must not enumerate another org's objects.
2. Rubric reads all scoring Requirements only from the selected extraction Job, never condition/Chunk traversal/full text. Source verification reads only the referenced chunk/block. Reproduce coverage, explicit deduplication, revision/new version, admin classification, item/set confirmation, history, weights/bounds, and section/overall total gates.
3. Score reads all confirmed response candidates plus comply-only (须遵守)/gap metadata only from the specified current DraftRun. Cross-Requirement support verifies verbatim and enters `response_item_ids`; tender-only citations never establish assessed results. Unconfirmed/later Cards, released DOCX, templates, and actual confidential values never appear in fake Provider requests. Disabled redaction blocks preview/submission without Job/Provider calls.
4. Test decimal weights, caps, ranges, and ROUND_HALF_UP for sum/weighted_sum/capped_sum. formula/non_additive never executes but permits complete rule confirmation. Cover ambiguity, price/external comparisons, thin commitments, negative deviation (负偏离), below-maximum scores without deduction reasons, out-of-bound scores, unknown/ambiguous/redacted citations, duplicate/missing Provider items. Retain valid batches only; partial subtotals never become totals; strengthening actions cannot fabricate certificates or pricing strategies.
5. Write-free/call-free preview; expected hash, redaction revision, rubric/Draft current fences; lease expiry, run_id takeover, cancellation, concurrent batches, sent-request unknown-outcome reservations, unique UsageRecord, and exactly-once prepaid deductions.
6. HTTP/remote/local CLI seven-key Result, schema, 0/2/3/4/5, all route task bindings, pagination, cache/retry; historical partial report show always returns 5. End-to-end output includes sanitized JSON rubric/report artifacts verifying sources, confirmed revisions, usage IDs, unassessable/missing-total reasons, in test temporary directories rather than `docs/`.

Implementation runs affected ruff, pyright, migration, PostgreSQL/RLS, API/CLI, and worker end-to-end gates. Verify successful/business-failure events, write-free dry-run, and no duplicate cached audits. No real Provider, no released-file reading, and no layout review must remain in report limitations; “score complete” cannot mean final bid evaluation.

Implementation confidentiality-representation limits are in [scoring Pitfalls](../notes/score.md#pitfalls). When pinned sources/section keys cannot safely be represented as placeholders, never rewrite confirmed bindings to bypass confidentiality.

## Decisions

These adopt the originally recommended defaults.

| Topic | Decision | Rationale |
| --- | --- | --- |
| Platform default scoring Provider | Follow [B09 decisions](check.md#decisions); no independent B10 selection | check/score share Chinese long-document, structured output, citation, price, and data-policy evaluation, avoiding contradictory defaults for one capability |
| Rubric generation structure | Replace single-request section/item generation with two stages: validate sections and overall rule from the full table, then generate item details in bounded batches against the fixed section list | The real-model run exceeded reliable structured-output capacity for a whole-table response; earlier independent batches lacked the table-wide aggregation context. The [changelog](../changelog.md#2026-10-05-two-stage-score-rubric-generation) records the run evidence. A small global structure response followed by bounded item responses addresses both failures |
| Persistent failure-audit extension | Persist only the fixed authenticated business-failure events here; pre-auth/cross-org 404 remain security logs | Prevent enumeration and excessive audit noise |
| Task budget | Score/rubric calls use quote-based shared per-call admission under the [budget contract](budget.md); rubric preflight includes both stages | Keep task liability, prices and call bounds consistent with each actual admitted request |
| `max_charge` | Bounds only platform selling-price charges for this Job | It cannot bound org-owned-key vendor bills |
| Price/subjective scoring | All unassessable initially | Missing verifiable external input makes guessing falsely precise; pricing strategy is a design non-goal |
| Released-export scoring | Do not expand this command; use a separate human-only contract if needed | Released files contain real confidential values and require export download permission; agent `score:run` cannot become a download channel |
| Human score changes/adoption/UI | Later separate append-only decision/UI contract; initial reports remain immutable | Preserve distinct provenance for model output, human judgment, and final evaluation, without overwriting history |
