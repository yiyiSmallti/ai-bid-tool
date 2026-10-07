---
kind: plan
---

# B03 typed parameter assessment contract

Status: **Approved with all recommended defaults, not implemented**.

This contract covers B03 in [the roadmap](roadmap.md), with consumption boundaries for
B02/B04/B07–B11, R02/R03/R06, C01/C02 and E01/E02. The importable
[Pydantic v2 contract](parameter-assessment/parameter_assessment_contracts.py) declares
payloads, Provider/service interfaces and Result projections. It registers no runtime
behavior. Implementation follows the approved [decisions](#decisions) under
[agent.md](../../agent.md#workflow).

## Goal and boundary

A bid specialist (投标专员) working in a cloud-hosted team must see which technical (技术)
requirements in tender documents (招标文件) have a supported comparison, which need
materials or interpretation, the responsible person, and the next action. Every
conclusive comparison must cite both the literal tender clause and the proposed
product (拟投产品) or feature source fixed to the task (任务). An org
(organization/tenant; 单位) remains the data boundary.

The flow is typed condition proposal → deterministic source and shape validation →
comparison against exact task resource revisions → human confirmation (人工确认) →
explicit attachment to a response card (响应卡). A comparison is advisory until
accepted; it never confirms a requirement, response (响应), evidence (证据), co-sign,
scoring rubric or export. Parameter satisfaction, response deviation (偏离), and
bid-rejection (废标) or point-deduction (扣分) risk are separate decisions.

In scope: scalar thresholds, bounded ranges, finite enumerations, Boolean capabilities,
flat AND/OR expressions, starred (★) mandatory clauses (★条款), deterministic unit
normalization, immutable input bindings, human resolution, four comparison outcomes,
and integration into the existing team workflow. Unsupported expressions remain
visible work; they do not disappear from coverage.

Excluded: universal technical ontologies, arbitrary formulas, benchmark interpretation,
cross-lot totals, inferred hardware configurations, model-authored unit conversions,
new crawling/OCR, new specification-file uploads, fabricated vendor (厂家) materials,
automatically accepting declared feature status, automatic scoring or response approval,
and editing/superseding the original requirement meaning. Genuine file ingestion and
capture remain in their existing resource/evidence chains. No network request is made
by the deterministic comparator.

## Current code basis and design differences

The following paths describe the integration baseline, not implementation of this contract.

| Current code anchor | Existing behavior and B03 difference |
| --- | --- |
| `Requirement` in [models/entities.py](../../server/app/models/entities.py); `ExtractedRequirement` in [schemas/contracts.py](../../server/app/schemas/contracts.py); `RequirementContent` in [schemas/requirement_confirmation.py](../../server/app/schemas/requirement_confirmation.py) | Persisted/shared `condition` is a free-form dict. Manual-entry validation checks finite JSON, not parameter algebra. B03 adds a typed sidecar rather than rewriting historical JSON or silently changing B02 review hashes. |
| `CONDITION_SCHEMA`, `WireCondition`, `HTTPExtractor.attach` in [providers/llm.py](../../server/app/providers/llm.py) | Model wire output already has param/op/value/unit fields; JSON Schema supplies operator choices, while `WireCondition.op` is a string and value is text. `attach` turns it into a dict. This is a field shape, not deterministic parameter-semantic validation. |
| `source_text`, `split_cited`, `validate_extraction`, `merge_starred`, `locate_source_citation_span` in [services/extraction.py](../../server/app/services/extraction.py); `Processor.__call__` in [jobs/processor.py](../../server/app/jobs/processor.py) | Extraction verifies PDF pages/Word blocks and literal quotes, retains model quotes, and unions starred scans. Valid quoted text alone does not prove that a model's operator, number, unit or scope follows from it. There is no separate `jobs/extract.py`. |
| `make_pin`, `verify`, `review_hash`, `effective_reviews` in [requirement_source.py](../../server/app/services/requirement_source.py); `preparation`, `require_confirmed` in [requirement_consumption.py](../../server/app/services/requirement_consumption.py) | Source pins hash document/chunk/location/quote and bind exact character offsets. B02 accepts the entire requirement meaning and Source. B03 binds that review revision/hash and source binding; preparation can precede acceptance. |
| `ProductData`/`ProductRevision` in [resource_contracts.py](../../server/app/schemas/resource_contracts.py); `FeatureData`/`FeatureRevision` in [feature_contracts.py](../../server/app/schemas/feature_contracts.py) | Product data has exact model/version and URLs, no typed specifications. Feature data has description and declared implemented/developing/planned status. A URL or an implemented declaration is not verified product evidence. |
| `select_product`, `snapshot_data` in [resources.py](../../server/app/services/resources.py); `select_feature` in [features.py](../../server/app/services/features.py); `select_revision` in [versioned.py](../../server/app/services/versioned.py) | Explicit task selections fix revision and lot; replacement retires the old selection. B03 records selection ID as well as revision ID, including a feature's parent product pin. Library head changes alone do not alter an existing pin. |
| `VendorArchiveView`, `ScreenshotView` in [screenshot_contracts.py](../../server/app/schemas/screenshot_contracts.py) | Real captures expose artifact hashes, capture time, revision binding and incomplete status. No shared contract yet certifies a literal archived text span as a typed fact. B03 adds a bounded deterministic text projection; screenshots remain separately confirmed Evidence. |
| `SimulatedResource` in [entities.py](../../server/app/models/entities.py); `read_vendor_page`, `record`, `simulated_ids` in [product_simulation.py](../../server/app/services/product_simulation.py) | Simulated proposals (模拟拟投) mark product/feature roots across revisions. Some proposed specs can come from search extracts, and simulation can create features declared implemented. Neither changing names nor a later revision removes provenance. |
| `CardContent`, `ResponseRow` in [response_card_contracts.py](../../server/app/schemas/response_card_contracts.py); `card_eligibility`, `confirmation_inputs`, `validate_confirmation`, `card_action` in [response_cards.py](../../server/app/services/response_cards.py) | Cards carry human response/deviation and evidence, not typed assessments. Missing material is not automatically negative deviation (负偏离). B03 adds explicit revision bindings and retains these gates. |
| `evaluate_item` in [check_rules.py](../../server/app/services/check_rules.py) | `negative_deviation` reads an assembled response's negative deviation and note. It signals disqualification risk for starred/substantive clauses (实质性条款), deduction risk otherwise, with human consequence review. It neither compares parameters nor establishes an actual penalty. |
| `assemble`, `current_draft_inputs` in [drafts.py](../../server/app/services/drafts.py); `fixed_rows`, `snapshot` in [score_run_inputs.py](../../server/app/services/score_run_inputs.py); `accept_batch` in [score_semantic.py](../../server/app/services/score_semantic.py) | Assembly partitions every requirement into a response, comply-only (须遵守), or gap (缺口). Scoring uses fixed inputs and guards `negative_deviation_conflict`; an assessment cannot grant full marks or silently remove a requirement. |
| `build_manifest`, `fresh_manifest`, `release`, `download_gate` in [exports.py](../../server/app/services/exports.py) | Export revalidates approvals and input freshness; negative deviation requires acknowledgment rather than blanket prohibition. Final sections (正式件) block simulated materials; review copies (审阅件) retain their acknowledgment policy. |
| `Identity`, `SCOPES`, `HUMAN_ONLY_SCOPES` in [auth.py](../../server/app/services/auth.py); `live_actor`, `access` in [task_workflow.py](../../server/app/services/task_workflow.py); `sign`, `apply_eligibility`, `manifest_fields` in [task_cosign.py](../../server/app/services/task_cosign.py) | Live task/domain authority and complete/current co-sign consumption predicates already exist. Org admin alone is not technical-review authority. A summary saying complete is not an approval predicate. |
| `CONTRACT_VERSION`, `Result`, `Cost` in [contracts.py](../../server/app/schemas/contracts.py); `BudgetJobResult` in [budget_contracts.py](../../server/app/schemas/budget_contracts.py) | Task-budget work is merged: Result 4.0 retains seven envelope keys and detailed cost fields. B03 reuses these types, not older cost examples in the design. |

The [design's example](../design.md#data-model) uses `tech_param`, `param/op/value/unit`
and an Evidence verdict of `satisfied`. Runtime `Category` uses `technical`, `substantive`,
`qualification`, `scoring`. B03 keeps those categories and introduces separate
`meets/does_not_meet/unknown/ambiguous` assessment values, without renaming Evidence
verdicts. The [design's memory table](../design.md#memory-system) lists unit conversions
as global-memory examples; that prose must not authorize executable conversion rules.
Only a reviewed versioned table participates in B03. Memory, model knowledge and search
snippets cannot supply parameter facts or conversions.

## First vertical slice

One active task, explicit extraction scope and lot, pinned declared product/feature,
and already captured genuine product material support the complete path below.

1. Select at most 100 requirement/resource pairs. Show current B02 review, selected
   model/revision, owner and missing inputs before any paid call.
2. Propose typed conditions for literal clauses such as `内存≥64GB`,
   `端口数不少于24个`, `工作温度覆盖-10℃至50℃`, and `支持IPv4和IPv6`.
   Validate every field against its literal Source; preserve unsupported or ambiguous
   clauses as work for the technical reviewer. A human may also enter a candidate
   through the same validator.
3. Bind literal facts from complete archived vendor HTML/PDF text or existing parsed
   genuine documents to the selected revision and exact configuration. A feature's
   `implemented` flag alone leaves an unknown result. Missing materials do not trigger
   hidden fetching or fabrication.
4. Compare locally; show both sources, original and normalized values, rules used,
   the four-state result and the next actor. Confirm B02, the exact condition and the
   conclusive comparison through their separate human decisions.
5. Explicitly attach the accepted result to a draft response card. Reuse evidence
   binding, response review, co-sign and assembly. Inspect the deviation table, B09
   risk, scoring inputs and review/final export behavior through their actual routes.
6. Replace a task product pin or reopen the requirement. Show stale assessment and
   dependent artifacts, remove consumption eligibility and guide re-review. Old
   immutable records remain readable; a cached comparison does not revive approval.

The slice includes API/CLI, a compact console panel, tenant tests and the consumer
freshness path together. Later slices may expand the parameter registry or grammar,
but the first slice includes ranges, enumerations, ★ preservation and uncertainty.

## Typed conditions and extraction

`Condition` is a discriminated union of `ResolvedCondition` and `AmbiguousCondition`.
A resolved condition is a bounded flat `all` or `any` group of at most 16 atoms; there
is no arbitrary expression tree. Each atom has a stable local ID, registered parameter
key, display label, subject, configured/supported/maximum/minimum basis, optional exact
configuration, typed value, operator, optional explicit tolerance, and literal field
support. The registry defines allowed subjects, basis, dimension, units and operators
per key. A model cannot register a parameter or alias.

| Value | Operators | Required meaning |
| --- | --- | --- |
| `number` | `eq/ne/gt/gte/lt/lte` | Exact decimal string plus unit; comparisons preserve strict/inclusive endpoints. Counts are nonnegative integers; fractions fail the parameter validator. |
| `range` | `within/covers` | Two finite ordered decimal endpoints with inclusion flags. `within` means the offered guaranteed values are contained in the permitted range; `covers` means the offered guaranteed operating range contains the entire required interval. An ambiguous “range” does not choose a direction. |
| `enumeration` | `eq/contains_all/contains_any` | Finite unique members, dimensionless unit `1`. Equality is set equality. `contains_all` requires every named member; `contains_any` requires at least one. Registry aliases only; no fuzzy synonym matching. |
| `boolean` | `eq` | Explicit true/false and dimensionless unit `1`; an implemented declaration or absent negative wording is not proof of true. |

Proposed first registry keys are `memory.capacity`, `network.port_count`,
`network.port_rate`, `temperature.operating`, `interface.protocol`, and
`feature.support`. Each key distinguishes subject and configured versus supported
capacity. For example, `最多支持128GB` does not establish `已配置内存≥64GB`, 24 total ports
do not establish 24 optical ports, and aggregate rate does not prove per-port rate.
Unknown aliases/configurations become ambiguous; registry expansion is a reviewed rule
release. Multiple unrelated measurements must never be squeezed into a single value.

`DecimalText` is a JSON string with at most 18 integral and 12 fractional digits.
No floats, NaN/infinity, arithmetic code, scientific notation or locale guessing enter
comparison. A bounded grammar may explicitly map full-width digits and Chinese
operator phrases to supported tokens, keeping original spans. Unrecognized number
syntax is ambiguous. Round only display text; compare exact decimal/rational values.
Overflow or precision outside the supported bound is an explicit unsupported result.

Tolerance defaults to **none**, not an implicit epsilon. V1 permits only source-stated
absolute or percent tolerance on numeric equality; `64±2GB` means the closed acceptable
interval [62,66] GB. Percentage tolerance is `abs(target) × percent / 100`. Threshold
tolerances, “about”, “typical”, measurement uncertainty, or unstated manufacturing
tolerances need clarification; a human cannot invent a relaxation. Zero-target relative
tolerance remains zero. Positive deviation (正偏离) is not inferred from a larger value;
some parameters prefer smaller values and some require exact equality.

The new `ConditionProvider.propose` is an LLM capability behind the existing Provider
layer, independent of legacy extraction. Input contains server-selected requirements,
bounded literal Source quotes, original starred flags and the fixed rule manifest.
Output proposes source-unit values and spans only; it cannot return authoritative
normalized values, conversion factors, approved state, product truth or a final verdict.
The model sees no cross-org materials or executable instructions from documents.

`ConditionVerifier.verify` has two separate checks:

1. Reuse `requirement_source.verify` and its unique original-span guard. Recompute
   document/chunk/position/quote bindings; never verify against a UI-truncated window,
   model paraphrase, search excerpt or redacted placeholder.
2. Check each field's `QuoteSpan` against the exact quote and reparse supported grammar
   deterministically. Verify negation, unit association, endpoints, per-device scope,
   connective, enumerated members and numeric meaning, not just token presence. For
   example, quoting both `64` and `128` is not permission to choose the wrong number.
   Multi-atom clauses need literal connective support and full supported-clause coverage;
   an omitted qualifier or unconsumed substantive phrase makes the group ambiguous.

Invalid citations are rejected with a receipt, never saved as verified conditions.
Valid citations with unsupported/competing interpretations are saved as ambiguous
candidates with reason codes, literal spans and at most three labeled suggestions.
The ★ flag is copied from the pinned Requirement and included in the hash; the model
cannot remove it. `merge_starred` remains the extraction safety net; B03 does not
claim extraction completeness or suppress rule-only clauses with empty conditions.

A human correction creates a new condition revision, preserving the tender text and
B02 review. It passes the same literal, registry, dimensional and numeric validators.
Where grammar alone cannot choose between valid supported interpretations, the human
selects one with a reason and exact revision/hash; the original ambiguity remains in
history. An undefined threshold/unit cannot become a resolved condition by approval
alone. A genuine purchaser (招标人) clarification must first enter a verified tender
requirement/source workflow; merging or superseding requirements is outside B03.
No “force meets” endpoint exists. Unresolved items can still be handled through an
honest manual response/material workflow; assessment uncertainty remains visible.

## Deterministic units and product facts

Versioned release manifests pin parameter aliases, dimensions, unit aliases, conversion
entries, grammar and comparator independently, plus a manifest SHA256. Store every
version used in immutable assessment inputs. Releases are reviewed repository artifacts
under a future `server/app/data/parameter_rules/` package, with source references and
golden boundary vectors. This contract adds no tables of executable rules and no dependency.
Runtime rejects unknown versions/checksum mismatches. A later release cannot mutate a
historical table or silently upgrade a task's accepted assessment.

| Family | Recommended initial exact normalization |
| --- | --- |
| Capacity | `B` base; `kB/MB/GB/TB` are powers of 1000, `KiB/MiB/GiB/TiB` powers of 1024. `1 GB = 1000000000 B`, `1 GiB = 1073741824 B`; never equate them or apply a memory-derived convention. |
| Counts | Explicit `个/口/端口` aliases map to `count` only with registered subject/context. Count is not bytes or dimensionless Boolean. |
| Rates/frequency | `Mbit/s` and `Gbit/s` map to `bit/s` by powers of 1000; `MHz/GHz` to `Hz`. Do not equate `GB` and `Gb`, storage and rate, or port rate and aggregate bandwidth. Bytes-per-second is unsupported initially. |
| Length/time | `mm/cm/m` and `ms/s` use exact powers of ten; cross-dimension conversions are forbidden. |
| Temperature/percent | `℃/°C` maps to `degC` with identity only in v1; `%` is explicit percent, not automatically fraction `1`. Fahrenheit/Kelvin and logarithmic/nonlinear units remain unsupported. |
| Enumeration/Boolean | `1` denotes dimensionless values; no numeric or truthiness coercion. |

Conversions use reviewed positive rational multipliers and explicit offsets, with
dimension checks before applying an entry. V1 offset entries are zero. A conversion
trace records original value/unit, exact normalized value/unit, table version and rule
ID for each side. Decimal spelling such as `64.0` may canonicalize to `64` for hashing;
literal source spelling remains unchanged. Unit symbols are case-sensitive; accept only
listed contextual aliases, never blanket lowercasing. Missing units, a source using `G`
without a defined meaning, inconsistent headers, or a GB/GiB convention explicitly
contradicted by the source require human clarification. The conversion table does not
decide what an ambiguous source intended.

`FactSetCreate` binds a bounded complete fact set to one task product/feature selection,
its exact revision, lot and parent product when applicable. `ProductPin`/`FeaturePin`
reuse `ProductRevision`/`FeatureRevision`. The server loads these pins; clients supply
only IDs and expected revisions. Product fact evidence uses either a verified existing
`Source` or a `VendorArchiveView`-bound archived text span. Model name/version and
configuration must match the selected revision with literal identity support. A series
page, upgrade option, maximum specification or another lot cannot substitute for the
selected configuration. A missing model version cannot mean every version.

The new archived-text projection extracts text locally from a complete existing capture,
retaining artifact hash, text hash, extractor version, block/page and literal offsets.
`ProductSourceVerifier.project_archive/verify_fact` defines this service boundary.
`SourceTextView` and `ArchiveCitation` distinguish `archive_artifact_id` (the
`ScreenshotVendorArchive` sandbox artifact), `artifact_sha256` (`ArchiveDescriptor.sha256`,
the retained archive/PDF bytes), `content_sha256` (the captured entry's content hash in `VendorArchiveView`), and
the extracted-text hash. The source binding hashes all of those identities plus extractor
version; the citation verifier additionally binds the exact block/page/quote offsets.
Clients cannot certify these fields by submitting them: the service loads the original
archive and recomputes/checks every binding, following `_read`, `archived_entry` and
`record` in [vendor_screenshots.py](../../server/app/services/vendor_screenshots.py).
No source projection asserts evidence approval.
It may not silently OCR images or revisit a live URL. An incomplete capture, unavailable
text, visual-only observation or declaration is `unknown` until genuine verifiable
material is supplied. Multiple incompatible genuine facts with equal applicable scope
are `ambiguous`; do not pick the favorable one. A human may select an applicable source
with a recorded reason and a new fact set, retaining excluded conflicts in history.
`FactConflictResolution` fixes the prior set/hash, retained fact IDs and every excluded
fact with a reason and applicable-source citation. Replacements name the previous set;
the service carries forward unresolved conflicts and rejects omissions without a
disposition. Only a human with `parameter:confirm` and technical task authority may
submit that resolution; it creates an immutable `parameter_reviews` receipt before
the resolved fact set can support a conclusive assessment. Token proposals cannot
erase conflicts by selecting fewer facts. Ordinary fact proposals remain unconfirmed
source data, with exact technical acceptance performed on the assessment.
No conclusive result may cite only a product-library URL.

Simulated-resource root marks are rechecked for product, feature and parent product
at preparation, confirmation and consumption. A marked resource yields `unknown` with
`simulated_resource`; source quotes do not launder simulation into an accepted fact.
A prototype (原型) screenshot does not establish hardware capacity or implemented feature
behavior. Existing software prototype keep/replace decisions remain independent.

## Comparison results and human decisions

| Verdict | Meaning | Next action |
| --- | --- | --- |
| `meets` | Verified facts guarantee the full typed condition for this exact pin. | Technical human reviews source, condition and result; then prepare/review the response. |
| `does_not_meet` | Verified applicable facts establish failure of the typed condition. | Technical human reviews failure, chooses another proposed product or records an honest negative response. |
| `unknown` | Needed applicable facts, exact model, genuine source or supported comparison are absent. | Assigned contributor supplies source/material; no implied failure or success. |
| `ambiguous` | More than one plausible requirement/fact interpretation or conflicting applicable facts remains. | Technical human resolves interpretation with source and reason, or leaves it unresolved. |

Every atom result carries tender support, product citations when available, conversions
and reason codes. Conclusive results require both source sides. For a missing product
fact, retain the tender citation and selection/revision searched with `missing_fact`;
do not fabricate a second citation. Conflicts cite all applicable conflicting facts
within bounds or return an explicit size error.

Numeric facts may describe a guaranteed interval. A threshold/equality returns meets
only if **all** possible guaranteed values satisfy the predicate, does-not-meet only
if **none** do, and ambiguous if some do. For example, offered [32,128] GB against
configured `>=64 GB` cannot prove the installed configuration. Range `covers` compares
operating envelopes; range `within` compares allowed values. Endpoint inclusion is
significant. Finite supported-protocol sets use the operators defined above; unknown
facts and unsupported scope never become empty sets or zero.

Aggregate `all`: any established failure → does-not-meet; otherwise ambiguous outranks
unknown, then all-meets → meets. Aggregate `any`: any established success → meets;
otherwise ambiguous outranks unknown, then all-fail → does-not-meet. Keep all atom
outcomes and unresolved notices even when Boolean short-circuiting fixes the result.
Apply this only to a fully resolved connective; an ambiguous expression has aggregate
ambiguous regardless of a suggested interpretation. Starred status changes prominence
and downstream risk review, never numeric comparison.

Human condition and assessment decisions are separate immutable receipts. Confirmation
requires live technical review-domain (职责) authority, active task access, currently
confirmed B02 input, validated resolved condition, current rule/input hashes and, for
assessment confirmation, a conclusive result using genuine applicable facts. Unknown
and ambiguous results may be read, assigned, rejected or reopened, but not accepted as
proof. A reject/reopen is always available to an otherwise authorized human even if
the original input is stale. Confirmation does not set `Evidence.confirmed_by`.
`ConditionView.review_state` and `AssessmentView.review_state` are current effective
projections, following `requirement_source.effective_reviews`: a stale previously
accepted item reads as invalidated while keeping `confirmed_by/confirmed_at` and its
immutable acceptance receipt. History includes the original confirmed decision and the
later invalidation; no stored acceptance event is overwritten. The invalidated projection
does not claim that the original decision never happened.

## Response, check, scoring and export consumption

The recommended default is **optional explicit assessment attachment, strict freshness
once attached**. B03 does not globally block already valid manual responses or require
every requirement to have an assessment. Adoption changes the particular response-card
revision's dependency set and is never silently applied to an existing card.

| Consumer | Proposed B03 behavior and retained gates |
| --- | --- |
| Response preparation | `CardAttach` adds the exact accepted assessment/review IDs to a new draft card revision, subject to expected card version. It refuses a protected/confirmed card until the human uses existing reopen/withdraw. No automatic revision overwrite or weakening of existing negative deviation. A meets result may suggest no deviation (无偏离); does-not-meet may suggest negative. Humans write the actual response and note. Positive is always a human proposal requiring justification. |
| Response acceptance | Validate B02, condition acceptance, assessment acceptance/freshness, material/Evidence confirmation and current task/domain permissions. Preserve `task_cosign` complete/current round gates; summary completeness and an assessment confirmer are insufficient. An attached does-not-meet plus nonnegative card is a conflict requiring input revision or attachment withdrawal with human reason; it cannot be waived as meets. |
| Table assembly (组表) | Extend `card_eligibility` and `current_draft_inputs` with exact assessment and review receipt bindings. Stale/reopened attached dependencies produce `parameter_assessment_invalidated`; they remain gaps until re-reviewed. Unknown/ambiguous proposals stay in the work panel, not confirmed rows. Preserve the exhaustive response/comply-only/gap partition and independent comply-only authority. |
| B09 check | Reuse `check_inputs.snapshot` and `check_rules.evaluate_item`. Human-confirmed negative rows still generate `negative_deviation`; add linked tender/product comparison citations as supporting details, not fabricated bid quotes. Provisional assessments can produce a clearly provisional advisory observation, never an accepted response. Do not infer statutory rejection or a numeric penalty; existing risk severity remains a review signal. |
| Scoring | Bind accepted assessment IDs/hashes into `score_run_inputs.fixed_rows` and report stale input if they change. Only confirmed scoring criteria (评分标准), the accepted rubric and actual bid (标书) material can support points. Preserve `score_semantic.accept_batch` negative-deviation protections. No “meets = full score” or “unknown = zero” shortcut. |
| Export deviation tables | Extend response-row/export manifest bindings and the approved template projection to show tender wording, offered specification, original units, human deviation/note, tender location and product source location. Include assessment provenance in the internal manifest; avoid internal IDs in printed prose. Unattached manual rows retain their existing format. |
| Review copy / final section | Reuse `build_manifest`, `fresh_manifest`, `release`, `download_gate`, Evidence confirmation, human-only export, prototype decisions and simulated-material policy. Stale attached assessments follow existing stale-input/gap rules, including explicit gaps in review copies; never print an unconfirmed assessment as a response. Accepted negative deviations retain `export_negative_deviation` acknowledgment, not a new blanket block. Previously generated artifacts retain historical snapshots but cannot receive a fresh release/download eligibility claim after invalidation. |

An authorized human may detach an assessment through a new card revision with a reason;
the full response review and co-sign cycle restarts. This is not deletion of the failed
result, a change to its verdict, or a means to erase the underlying negative-deviation
conflict from assessment history/check notices. The existing manual evidence standard
still applies. Detach is a proposed addition to card revision metadata, not a new route
that bypasses the existing card update/reopen workflow.

## Freshness, cache and concurrent work

`InputManifest` binds org/task/extraction scope and set revision, exact requirement review revision/hash
and source binding, condition revision/hash and human receipt, fact set ID/hash, product
or feature selection/revision/lot/configuration, parent product, provenance marks,
rule versions and task membership/review-policy state. Assessment review has its own
monotone revision. Card consumers additionally bind Evidence source/rendition hashes,
assessment review receipt and co-sign manifest. Server-computed hashes cannot be supplied
as authorization by a client.

| Change | Required effect |
| --- | --- |
| Requirement text/category/★/legacy condition/source changes, citation repair, B02 reopen or new extraction scope | Old assessment is historical/stale; re-extract or revalidate condition, recompute and reconfirm. No cross-extraction approval inheritance. |
| Condition revision, clarification decision or fact-set replacement | Invalidate dependent acceptance and card consumption; keep prior calculation/result/receipt. |
| Task selection replacement/removal or feature parent-pin replacement | Old selection becomes stale even if the new revision has equal values. Selecting the old revision again does not resurrect an earlier human receipt. |
| Library head revision changes while task pin is unchanged | Notify that a newer library revision exists; do not silently repin or invalidate solely because the library head moved. |
| Withdrawn/replaced source, Evidence/rendition changes or root simulation mark | Recheck relevant source/provenance/material bindings; invalidate affected consumers. Source archives are immutable; replacement has a new identity. |
| Reviewed rule release | Retain pinned historic release; expose recompute available. Explicit task adoption creates new assessment inputs and requires re-review. A revoked unsafe rule release blocks acceptance/consumption immediately without rewriting history. |
| Membership/domain/co-sign policy changes or task archival | Re-evaluate live acceptance and consumption. Loss of authorized signers cannot be overridden by cached success; archived tasks allow authorized history reads only. |

Jobs capture expected input hash under task lock; recheck live authorization and all
pins at publication and before each paid call. No publication against changed inputs:
return `parameter_inputs_changed` with recoverable receipts and accounted cost. Use
`JobExecution` run IDs, cancellation and recovery rather than a second queue.
Extraction cache keys include full source hashes, registry/grammar version, prompt/model
and redaction policy; deterministic comparison keys include exact condition/facts/rules.
Cached computation may save cost; human receipts and task eligibility are never cached
across changed input/review epochs. Emit durable task events in the same transaction as
decision/invalidation; consumers revalidate even if SSE delivery or an invalidation
worker is delayed.

## Data model and migration outline

These are proposed org business tables, not migrations in this contract. Every row has
`org_id UUID NOT NULL`, `task_id UUID NOT NULL`, primary key `id`, unique `(org_id,id)`
and `(org_id,task_id,id)` where referenced. Task, requirement, job, resource revision,
selection, archive, card revision and decision references use composite org keys;
task-scoped references also constrain task identity. Global `User` references alone
cannot establish reviewer authority: store the org membership reference and audit actor
identity, then recheck active grants. No nullable-org global rule-table exception.

| New table | Content and constraints | Runtime mutation |
| --- | --- | --- |
| `parameter_conditions` | One head per `(org_id,task_id,requirement_id)`; optimistic revision and current typed revision FK. | Head advance only under task lock. |
| `parameter_condition_revisions` | Full Condition, B02/source pin, ★ flag, origin, rules/hash, previous revision and reason hash; unique `(org_id,condition_id,revision)`. | INSERT/SELECT only. |
| `parameter_source_texts` | Existing archive identity, original artifact hash, deterministic extractor version, text hash, org storage key and bounded block index; parsed documents reuse existing verified Source pins. | INSERT/SELECT only; no new upload surface. |
| `parameter_fact_sets` | Exact product XOR feature selection/revision, parent product pin when feature, previous set/revision, bounded typed facts and source-text IDs/hash, conflict disposition. Unique revision per task selection. | INSERT/SELECT only; new set supersedes by explicit reference, not UPDATE. |
| `parameter_assessment_runs` | Job/run ID, explicit extraction scope, selected target count, full input manifest hash/rules, publication status; result rows individually persisted. Unique `(org_id,job_id,run_id)`. | Bounded publication-state updates; no editing fixed inputs. |
| `parameter_assessments` | One selected pair per run; exact condition/fact revision FKs, immutable per-atom and aggregate result, both source bindings, manifest and comparison version. Unique `(org_id,run_id,requirement_id,selection_id)`. | INSERT/SELECT only. |
| `parameter_reviews` | Append-only condition XOR assessment XOR fact-conflict decision; exact subject revision/input hash, monotone decision revision, actor membership/user, session provenance, reason hash and timestamp. CHECK exactly one subject FK. | INSERT/SELECT only; reject/reopen is another event. |
| `parameter_card_bindings` | Card revision + assessment + exact confirming review receipt; detach is represented by a later card revision/event. Unique `(org_id,card_revision_id,assessment_id)`. | INSERT/SELECT only. |

JSON payloads are versioned and shape checked on every write/read; composite FKs also
bind immutable facts/conditions to the same assessment's org/task, not merely a JSON ID.
An archive-backed fact references `parameter_source_texts`; existing parsed document
facts constrain `Document`/`Chunk` ownership and hash. Product/feature selection
polymorphism uses nullable explicit FK columns plus XOR CHECK constraints, not an
unenforced `(kind,id)` string. Rules remain immutable application artifacts; a missing
release blocks recomputation rather than guessing a replacement.

Enable and **FORCE RLS** on all eight tables. SELECT/INSERT/UPDATE policies require
`org_id = NULLIF(current_setting('app.current_org', true), '')::uuid` with matching
`WITH CHECK`; missing/empty context fails closed, following
`0029_simulated_resources.upgrade` in the existing migrations.
Apply existing task visibility predicates in service queries; org RLS does not replace
task authorization. Use `bid_app` without owner/BYPASSRLS privileges. Revoke DELETE and
immutable UPDATE, including from job paths. Audits remain in existing `AuditLog`; no
cross-org function or global business-data table is introduced. All stored artifacts
use encrypted `org/{org_id}/...` keys and short-lived authorized download links.

Recommended migration sequence:

1. Add tables, composite unique keys/FKs/checks/indexes, RLS policies, least-privilege
   grants, and token human-scope exclusion constraints atomically. Index org/task/ID,
   requirement head, selection revision, run target and review sequence for keyset reads.
2. Add nullable assessment-binding fields to immutable card/draft/check/score/export
   snapshots with a schema-version discriminator. No existing response changes state
   merely because B03 is available; new bindings opt in to its freshness rules.
3. Leave `Requirement.condition` and legacy provider/CLI output unchanged. Lazily create
   unconfirmed typed candidates for explicit selected requirements; `{}`, malformed or
   unsupported historical dicts never backfill a positive result or human receipt.
4. Deploy the additive routes/schema discovery and consumer readers, then enable B03
   writes after two-org/gate acceptance. Reconcile queued jobs with fixed schema/rules.
5. Rollback disables new submissions while retaining immutable tables/history. A runtime
   unable to enforce attached-assessment freshness cannot serve acceptance/export for
   such cards; retain the compatible gate or disable those writes. Never drop receipts
   or silently treat a B03-bound card as an old unbound card. Repair forward.

## HTTP, CLI and Result 4.0

All routes below are proposed relative to the same API prefix as existing routes.
`org_id`, actor, approval state and rule release come from live server context; write
bodies cannot override them. Every route resolves the task and parent first; nonexistent
and inaccessible objects both return 404. CLI supports `--json`, never prompts, and
uses the same services in remote/local PostgreSQL modes. `--input` accepts one bounded
JSON payload file; missing input fails immediately.

| HTTP route | CLI | Request → Result `data` / `items` |
| --- | --- | --- |
| `POST /tasks/{task}/parameters/extractions` | `bid parameter extract --task ID --input FILE [--dry-run] [--wait] --json` | `ExtractionRequest` → `BudgetPreflightData` or `JobAccepted` / `[]`; terminal `RunSummary` / `TargetReceipt[]`. |
| `GET /tasks/{task}/requirements/{req}/parameter-condition` | `bid parameter condition show --task ID --requirement ID --json` | → `ConditionView` / `[]`. |
| `PUT /tasks/{task}/requirements/{req}/parameter-condition` | `bid parameter condition propose --task ID --requirement ID --input FILE --json` | `ConditionCreate` → new unconfirmed `ConditionView` / `[]`; not original requirement editing. |
| `POST /tasks/{task}/parameter-conditions/{id}/decisions` | `bid parameter condition decide --task ID --condition ID --input FILE --json` | `Decision` → `ConditionView` / `[]`; human only. |
| `POST /tasks/{task}/parameter-fact-sets` | `bid parameter facts create --task ID --input FILE --json` | `FactSetCreate` → `FactSetView` / `[]`; checked source proposals, no acceptance. |
| `GET /tasks/{task}/parameter-fact-sets/{id}` | `bid parameter facts show --task ID --facts ID --json` | → `FactSetView` / `[]`. |
| `POST /tasks/{task}/parameter-assessments` | `bid parameter assess --task ID --input FILE [--dry-run] [--wait] --json` | `RunRequest` → `BudgetPreflightData` or `JobAccepted` / `[]`; terminal `RunSummary` / `TargetReceipt[]`. |
| `GET /tasks/{task}/parameter-assessments` | `bid parameter list --task ID --extraction-job ID [--verdict V] [--review-state S] [--requirement ID] [--limit N] [--cursor C] --json` | `AssessmentQuery` → `PageData` / `AssessmentView[]`. |
| `GET /tasks/{task}/parameter-assessments/{id}` | `bid parameter show --task ID --assessment ID --json` | → `AssessmentView` / `[]`. |
| `GET /tasks/{task}/parameter-assessments/{id}/history` | `bid parameter history --task ID --assessment ID [--cursor C] [--limit N] --json` | `PageQuery` → `PageData` / `AuditEvent[]`, including linked condition/fact decisions. |
| `POST /tasks/{task}/parameter-assessments/{id}/decisions` | `bid parameter decide --task ID --assessment ID --input FILE --json` | `Decision` → `AssessmentView` / `[]`; no caller-provided verdict. |
| `POST /tasks/{task}/cards/{id}/parameter-assessment` | `bid parameter attach --task ID --card ID --input FILE --json` | `CardAttach` → `CardAttachment` / `[]`; returns a draft response revision. |

Reuse existing job status/wait/cancel and source-preview routes, with B03 task/scope
checks on job kind and receipt reads. Recovery uses immutable run receipts and explicit
remaining target IDs; no unbounded task-wide implicit selection. Add these schemas to
`cli/bid_cli/schema.py` only during implementation. No existing wire shape changes;
breaking a later typed condition representation requires the normal major-version policy.

`ConditionView.id` is its immutable revision ID and `condition_id` identifies the
head used by the decision route. Decision `expected_revision` is the current
`review_revision`; `expected_input_hash` binds the shown condition content hash or
assessment input hash. Revision creation instead uses `expected_condition_revision`.
Confirmation changes review state/receipt, never the immutable content revision.
Terminal receipts enumerate published/failed/remaining targets; `RunSummary.processed`
counts published outputs and `failed` counts execution failures, excluding remaining.

Example successful submission (IDs are illustrative, not fixture business data):

```json
{
  "ok": true,
  "command": "parameter assess",
  "data": {
    "task_id": "00000000-0000-4000-8000-000000000001",
    "job_id": "00000000-0000-4000-8000-000000000002",
    "input_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "kind": "parameter_assess",
    "state": "queued",
    "cached": false
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
  "duration_ms": 8
}
```

This is the current `Result` envelope, not an invented `Result4` type. `data` is the
JSON-mode projection of the table's Pydantic model; list rows belong in `items`, not a
second unbounded nested list. Cost is required in snapshots even when all values are
zero. Deterministic assessment has zero provider calls and local zero cost. Extraction
uses `BudgetProviderUsage`, `budget_preflight.estimate_quotes/attach`, and
`JobExecution.admit`; every actual call is priced/accounted even if its output is
rejected. Unknown prices use `basis=unknown` and null amounts, never false zero. Dry-run
makes no call/write/reservation. Cached results report current operation cost separately
from historical generation cost. Terminal partial/failed jobs retain `BudgetJobResult`
published/remaining/usage and human intervention, without automatic budget escalation.

| Exit | Meaning for B03 |
| --- | --- |
| 0 | Read/write/submission completed, or completed assessment including unknown/ambiguous outcomes; an honest unresolved conclusion is not an execution failure. |
| 2 | Invalid payload, unsupported requested option, invalid citation, stale expected hash/revision, human gate or configured size limit; caller must fix inputs. |
| 3 | Retryable provider/queue/database timeout or rate limit; preserve receipts and cost. |
| 4 | Forbidden human/token action, inaccessible/not-found object, content refusal or unrecoverable source corruption; no permission bypass retry. |
| 5 | Some selected targets published and others failed execution; `ok=false`, explicit receipts for every selected target and remaining IDs. Successful ambiguous targets alone do not produce exit 5. |

HTTP follows existing error envelopes: 422 input/shape/size, 409 stale/gate, 403 visible
but unauthorized action, 404 invisible/missing parent, 429/503 retryable failure.
Non-waiting submissions use 202. `--wait` returns the terminal mapped exit. Request
hash idempotence may reuse exact work but cannot combine targets from different scopes
or retry already published items silently.

## Permissions, audit and bounded reads

| Action | Proposed scope | Role/task gate |
| --- | --- | --- |
| List/detail/history/source preview | `parameter:read` | Active org membership plus readable task; admin/bidder/technical/viewer within existing task visibility. Tokens need task/read and resource/source read grants where applicable. |
| Extract/compare and preview | `parameter:run` | Admin/bidder/technical with task owner/contributor write authority; explicitly scoped tokens may prepare. Archived tasks reject mutation. |
| Propose/revise condition or facts | `parameter:write` | Same task write authority; tokens may propose source-checked candidates but cannot resolve their approval state. |
| Confirm/reject/reopen condition or assessment | `parameter:confirm` | Human session only, technical org role and live technical task review-domain grant under existing domain predicates. Admin/bidder alone cannot perform a technical decision. Assignment routes work; assignment alone grants no authority. |
| Attach assessment to response | `parameter:write` + existing `card:write` | Current accepted assessment and writable draft card; no token can confirm the resulting card or withdraw a protected human decision. |
| Response approval/co-sign/export | Existing scopes | Preserve `evidence:confirm`, `card:cosign`, review-domain roles and `export`, plus live B02/assessment gates. No new export permission. |

Add `parameter:confirm` to `HUMAN_ONLY_SCOPES` and database token-scope denial constraints;
it is never in token-issuable `SCOPES`. Tokens never receive any human-only scope,
`evidence:confirm` or `export`, including through task ownership, delegation or internal
agents. Token run jobs recheck issuer/task grants at dispatch, each paid call and
publication; revocation stops further work. UI capability hints are explanations, not
authorization. Workers cannot impersonate a session to accept their outputs.

Audit condition proposals/revisions/decisions, source/fact-set creation, comparison
submission/completion/failure, exact human acceptance/reject/reopen, invalidation and
card attachment/detachment. Use existing `AuditLog`, authenticated invocation/token
provenance and durable task events. Record org/task, subject revision, input/rule hashes,
old/new decision state, actor membership/user/token/job/run/request IDs, reason hash,
timestamp and cost/usage references. No raw quotes, prompts, secrets, confidential
prices or signed URLs in logs. Human-readable reasons and literal source text reside
only in authorized encrypted business records. Read history uses the same task gates.

Bounded reads are a contract, not a UI pagination hint:

- Lists default to 25 and cap at 100; SQL keyset queries use limit+1, ordered by
  `(created_at,id)`. Fifteen-minute signed cursors bind org, principal/current authority,
  task, extraction scope, filters and parent. Reject cross-context or expired reuse.
- Serialize the **whole seven-key Result** within 256 KiB per page and 1 MiB per detail.
  Use `assessment_bounds.fit_items`/`encoded_size` with B03 limits, retaining complete
  rows and cursor advancement. An oversized first entry is an explicit 422, never an
  empty successful page or truncated citation masquerading as complete proof.
  Check the corresponding complete detail envelope before publication too; refuse an
  oversized target with a receipt rather than storing a result that cannot be reviewed.
- Request bodies cap at 1 MiB; at most 100 selected targets per job, 16 atoms per
  requirement, 64 facts per set, eight product citations per atom, 20,000 characters
  per quote. Provider batches cap at 20 requirements and 64 KiB serialized text;
  oversize single candidates return `parameter_input_too_large` before billing.
- Local archived-text projection caps at 8 MiB UTF-8 text and 5000 blocks per archive,
  with bounded streaming input and the existing archive limits. It fails explicitly
  rather than truncating a source while claiming complete fact verification.
- Validate any whole-scope B02 manifest with SQL 2001 sentinel and reject over 2000;
  never call an unbounded legacy `.all()` and then label the truncated set complete.
  Explicit target lists still compare only those selected and expose coverage counts
  as “selected”, not extraction completeness.
- Batch distinct documents/chunks/source-texts, pin lookups and simulation-root checks
  for the selected set. Reuse source verification per distinct immutable source;
  no N+1 whole-document loading and no whole-org simulation scan. Source windows are
  for display only, with full verified source available through authorized bounded
  preview/download paths. Use a two-second DB statement timeout for interactive reads.
- Preview/extraction/comparison preflight names blockers and responsible next human
  without querying every historical card. Lists are live keyset views; histories are
  immutable events, not snapshots of current permission. Page totals never imply all
  work is complete. SSE reconnect/polling uses current revisions and permissions.

## Console outline

Add “参数核对” to the task workspace using the existing task board and assessment
patterns in [org-console.md](org-console.md), [console-assessments.md](console-assessments.md)
and `web/`. Lead with five work buckets: needs requirement review, needs interpretation,
needs product material, awaiting technical review, and accepted/stale. Verdict and
review state remain separate labels; ★ and known failures remain visible in all views.

The row shows requirement, selected lot/model/version, observed versus required value,
comparison status, assigned person, blocker and next action. “Unassigned” routes to task
owner assignment; a technical confirmation button identifies the eligible review domain.
Do not require bid staff to understand hashes, source parsers, jobs or migrations.

The detail panel puts literal tender and product sources side by side with locations,
exact selected version and capture time. Show the original units first, with a small
expandable deterministic-conversion explanation. Ambiguity highlights the conflicting
words and offers allowed interpretations or “request source/clarification”; it never
defaults to meets. Confirmation names exactly what is being accepted and separately
shows B02, response/Evidence and co-sign blockers. Comparison reruns do not tick any of
those checkboxes.

Batch extraction/assessment starts with selected count, missing inputs and budget
preview. Progress keeps successful, ambiguous, unknown and failed targets distinguishable.
No implicit “confirm all” or cross-domain approval is introduced. After accepted
assessment attachment, link to the existing card review, assembly, check, scoring and
export screens. A changed revision gives a stale banner with the specific dependency
and next actor. Token-driven proposals remain visibly attributed. Read-only users get
source/history views, without disabled controls suggesting they can grant approval.

## Failure modes and recovery

| Failure | Required behavior |
| --- | --- |
| Literal citation absent, repeated occurrence unresolved or page/block mismatch | Reject verification with per-target reason; preserve original requirement and B02 state; repair through authorized source workflow. |
| Number/operator/scope absent from exact quote, model invents conversion or drops negation/★ | Reject unsupported typed candidate or persist explicit ambiguity; never publish a conclusive assessment. Preserve paid-call usage. |
| Missing fact, incomplete archive, unverified declared feature, simulated resource or prototype-only support | Unknown with source/material action; no zero/default value or automatic negative deviation. |
| Dimension mismatch, ambiguous alias, contradictory source or unsupported tolerance | Ambiguous with literal supporting spans and technical owner; no widening retry/prompt to force success. |
| Revision changes between preview, worker and confirmation | 409 `parameter_inputs_changed`; retain historical computation, require fresh preview/review. No partial update of confirmation/bindings. |
| Lost membership, wrong review domain, archived task or token confirmation | Deny on server and transaction boundary; 404 for invisible objects, 403 for forbidden visible actions. Recovery reads remain subject to visibility. |
| Provider budget/call cap/cancellation after some targets | Persist exact published/remaining receipts and all admitted usage; partial exit 5, no fabricated missing results or automatic budget increase. |
| Database/source storage failure or queue replay | Retry only classified transient failures through existing job recovery; idempotent fixed-input publication and unique run/target keys prevent duplicate decisions. |
| Oversized data, excessive facts/conflicts or expired cursor | Explicit input/size/cursor error with narrower selection guidance; never silently clip evidence or cross-task results. |
| Accepted assessment conflicts with proposed response deviation | Keep the comparison and block contradictory attached acceptance; revise inputs or reopen/detach with human reason and full response review. |

## Verification and evaluation plan

Before implementation, enumerate failure cases above and freeze contract/schema/CLI
examples. This contract's own verification is limited to ruff, format, pyright and import;
it does not claim runtime, database, provider effectiveness or browser acceptance.
Implementation should prioritize end-to-end acceptance against the actual routes and
roles, with fake Providers in CI. No post-implementation unit tests that merely mirror
the comparator are prescribed.

| Acceptance lane | Required cases and repeatable artifact |
| --- | --- |
| Two-org database isolation | For **each of the eight new tables**, org A cannot SELECT/INSERT/UPDATE/reference B; missing org context fails; composite FK rejects mixed org/task/selection parents; immutable UPDATE/DELETE denied to `bid_app`. Include source object prefix/download isolation and task-private visibility within the same org. Retain machine-readable case results. |
| Two-org HTTP/job isolation | For **each route in the HTTP table**, A against B and A against a nonexistent parent both return 404, including cursor/history/source/facts/card/job children. Revoke token/membership between submit and publication; check no B data enters Provider input, caches, SSE or error details. |
| Real workflow gates | Human with technical task authority accepts B02/condition/result separately, attaches a card, completes Evidence and required co-sign, assembles and exports. Negative cases cover every missing gate, wrong role, token scope issuance/request, stale source/pin/rules, a changed-back revision, simulated/prototype declarations and contradictory card. Reopen/reject recovery remains usable. |
| Parameter end-to-end vectors | Run source → condition → facts → assessment through actual API with fake model proposals: Chinese inequalities/negation/full-width digits, 64 GB/64 GiB, 23/24 ports, exact boundaries, negative temperature, open/closed ranges, all/any sets, per-port versus total, installed versus maximum, explicit tolerance, unsupported/ambiguous statements, missing or conflicting facts, ★ preservation and quote-matched-but-semantically-wrong proposals. |
| CLI snapshots | Snapshot `--json` for **every listed command**, dry-run/job wait, page/cursor, human denial, stale gate, retryable/nonretryable/partial outcomes and Result 4.0 costs. Import schema discovery and ensure no unregistered command or older cost projection is advertised as B03. Snapshot decimal strings, both source sides and empty-result pages. |
| Browser end-to-end | Two humans with distinct required review domains and a read-only user exercise next-owner assignment, source inspection, ambiguity correction, card attachment, co-sign, negative-deviation acknowledgment and export. Replace the product pin during an open review and test SSE reconnect/stale refresh. Retain a redacted Playwright trace and assertions tied to immutable IDs, not screenshots alone. |
| Boundedness/concurrency | Seed enough data to cross each page/batch/quote/byte/manifest bound. Assert SQL bounded reads, no N+1 source loading, correct UTF-8 envelope shrinking, cursor forward progress, timeout mapping and changed-input publication rejection. Queue replay and cancellation retain cost and one receipt per target. |

Keep repeatable acceptance artifacts under git-ignored `data/work/b03-acceptance/<run>/`
or the CI artifact store, never under `docs/`. Include command/version/seed manifest,
exit results, sanitized JSON snapshots and browser trace. Database/service operation is
owned by the main implementation session, not delegated workers.

Synthetic evaluation uses deterministic, source-bearing tender/product pairs covering
every supported operator/unit/dimension/qualifier plus adversarial near-misses. Labels
include requirement spans, parameter identity, expected typed form, exact product pin,
facts, outcome, ambiguity reason and ★. Keep parser/comparator rule vectors distinct
from natural-language extraction efficacy; a low ambiguity rate is not success if it
comes from guessing. Fake Providers drive CI reproducibly.

Public evaluation uses a separately acquired and hashed set of publicly available
hardware tender PDFs/Word documents and matching official vendor specifications, with
source URLs, publication/capture dates, permitted reuse/attribution, exact models and
human annotations. No documents are downloaded or real models called by this contract.
Use two annotators, adjudicate disagreement, and split by procurement/project/vendor
family so near-duplicate templates do not leak between development and held-out sets.
Record unmatchable models and missing official materials rather than excluding them to
inflate performance. Real bid data requires its own consent and org isolation.

| Metric | Proposed acceptance basis |
| --- | --- |
| ★ recall | 100% of labeled starred clauses remain represented, including unsupported/ambiguous cases; follows [design evaluation](../design.md#evaluation). |
| Literal citation validity and reproducibility | 100% of accepted conclusive pairs have independently re-resolvable tender and product citations; same inputs/rule release produce identical normalized results. |
| Conversion/boundary correctness | 100% of the finite supported synthetic vectors; no model-supplied conversion accepted. |
| Dangerous false meet rate | Zero on mandatory synthetic adversarial cases and no observed false meets on the held-out mandatory subset; report numerator, denominator and uncertainty, never claim a universal zero risk. |
| Typed extraction and four-state classification | Per-field precision/recall, four-class confusion matrix, coverage/abstention and human disagreement by unit/operator/domain; establish public-set release thresholds after the first authorized baseline, not invented accuracy numbers. |
| Workflow usefulness | Time to next responsible action, source-supply rate, human correction/rejection rate and stale-decision prevention; distinguish no material from unsupported grammar. |
| Cost/performance | Per-document selected/processed/remaining counts, latency, calls/tokens, Result cost and retries, with fixed model/prompt/rule hashes and bounded-read statistics. |

Evaluation manifests and licensed synthetic/public fixtures belong under a future
`evals/parameter_assessment/` lane, disabled from real-network CI. Store predictions,
human labels and metric reports as versioned artifacts. Prompt/model/table changes rerun
the same frozen set and compare regressions; evaluation never auto-approves production
requirements or imports one org's private data into a global test set.

## Decisions

The owner approved every recommended default; implementation follows these decisions.

| Decision | Approved default | Reason |
| --- | --- | --- |
| First vocabulary and expression scope | Six registry keys listed above, finite aliases, flat all/any, explicit numeric/range/set/Boolean semantics. | Delivers useful hardware/feature cases without pretending to understand every specification. |
| Legacy `Requirement.condition` | Preserve as historical free JSON; typed sidecar with explicit source/B02 bindings. | Avoids retroactive approval and a breaking extraction contract. |
| GB/GiB and conversion authority | Exact SI/binary distinctions in reviewed immutable releases; ambiguous source spelling goes to human. | Reproducible comparisons without model or memory guesses. |
| Tolerance | No implicit tolerance; only cited absolute/percent numeric equality in v1. | Prevents an apparent match created by relaxing the tender. |
| Fact extraction | Human/source-checked fact entry and local archive text parsing initially; LLM proposes tender conditions only. | Keeps the first slice auditable and budget behavior understandable; future automated product extraction can reuse facts. |
| Product/feature proof | Exact task pin and applicable genuine source; declaration, simulated proposal and prototype alone yield unknown. | Prevents implementation-state or simulation markers being laundered into proof. |
| Human authority | Technical review-domain humans decide typed meaning/result; B02 and response/co-sign gates remain independent. | Gives a visible responsible owner without granting technical authority to every admin. |
| Require B03 for every response? | No; explicit optional attachment, then strict freshness and contradiction gates. | Preserves valid manual work while making machine-supported claims auditable. |
| Model-before-B02 work | Permit provisional preparation; require current B02 for typed acceptance and downstream confirmed consumption. | Staff can prepare material while coordination review is pending. |
| Negative deviations | Suggest only from established failure; retain current B09 risk and export acknowledgment semantics. | Missing proof is not failure and parameter failure is not a penalty formula. |
| Rule upgrades | Pin existing releases; explicit adoption invalidates dependent acceptance, unsafe release revocation blocks use. | Avoids silent changes to already reviewed bid material. |
| Feature parent product | Require explicit task parent-product pin, linked revision and exact relevant source. | A feature root association alone does not prove the selected delivery configuration. |
| Batch/size limits | Bounds in this contract, per-target receipts and explicit split/retry. | Keeps cloud team pages and worker costs predictable. |
| Public-set release thresholds | Gate citation/conversion/★ invariants now; set statistical accuracy targets after an authorized annotated baseline. | Avoids fabricated performance promises while making regressions measurable. |

Approval covers the Pydantic shapes, source/semantic boundaries, routes/CLI, recommended
defaults and consumer policy together. Implementation and production acceptance remain
separate work after that decision.
