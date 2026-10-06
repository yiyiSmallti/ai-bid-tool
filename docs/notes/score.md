# Human-reviewed rubrics and confirmed-draft scoring

## Problem

Scoring requirements are cited tender text, but their free-form condition payload is not a safe
executable rubric. A model may miss a scoring requirement, merge two rules, invent a bound, or
choose the wrong commercial or technical reviewer. Scoring a draft before people resolve those
errors would create a precise-looking result from an unconfirmed interpretation.

## Usage

Run `bid score rubric generate --dry-run` for a successful extraction job, inspect the fixed input
hash and cost ceiling, and submit the same input hash to create a candidate rubric. Use the list and
show commands to inspect sections, items, requirement coverage, source citations, and completeness.

An admin classifies each section and item as commercial or technical. The responsible bidder or
technical reviewer then confirms or rejects sections and items. Coverage decisions must map every
scoring requirement, name a canonical requirement for a duplicate, or explain an exclusion. A
revision is a complete JSON replacement snapshot and resets classifications and confirmations. The
bidder confirms the set only after all deterministic completeness checks pass. History retains every
classification, decision, and replacement event.

Replacement events retain the validated complete request, including proposed coverage, with a
database-verified snapshot hash. Effective coverage in the new version starts pending and requires
new coverage decisions; a proposal in a replacement request is not an inherited confirmation.

Complex inputs are UTF-8 JSON files. All commands return the seven-key `Result` envelope and never
prompt. Generate submission can return immediately or wait for the rubric job; a retained partial
result exits 5.

After set confirmation, preview `bid score run --task UUID --draft UUID --rubric UUID
--as-of YYYY-MM-DD --dry-run`, then submit its `--expected-input-hash`. Use `score list` and
`score show` to inspect immutable reports. Waiting for a report or showing it exits 5 when any
item is unassessable, a provider batch is incomplete, or a complete total cannot be formed.

## How it works

The rubric input fixes one organization, task, successful extraction job, document, and the complete
set of scoring requirements from that extraction. The generation provider receives the verified
source text with requirement summaries and position metadata after configured redaction. It creates candidates and cannot
confirm, classify, revise, or exclude anything.

Rubric sets, sections, items, requirement coverage, normalized coverage links, and append-only review
events use tenant-bound composite keys and forced row-level security. Revisions create a new candidate
set with new child IDs; they never rewrite an earlier set. Expected revision and input hash checks stop
stale decisions. Session-only review scopes and stored review domains (职责) enforce the human role boundary;
tokens, agents, and workers cannot perform review actions.

Completeness is deterministic. Every extracted scoring requirement needs an explicit coverage result;
all retained sections and items need classification and confirmation; keys, order, source bindings,
bounds, weights, caps, and aggregation must be coherent. `sum`, `weighted_sum`, and `capped_sum` are the
only executable aggregation forms. Formula and non-additive wording can be preserved and confirmed,
but remains unavailable for automatic aggregation.

Dry-run writes no job, review event, audit row, or usage record and makes no provider call. A paid
submission binds the dry-run input hash and follows the shared queue, lease, retry, cancellation,
provider resolution, redaction, usage, and prepaid charge controls. Cached input returns the retained
job without duplicating review or usage history.

Rubric generation uses two stages. Stage 1 sends the entire fixed scoring table in one request, with
no table splitting. Admission enforces `BID_RUBRIC_MAX_REQUEST_BYTES` against the entire serialized
HTTP request body. If the limit is exceeded, preview reports a capacity blocker and submission is
refused before any Provider call. Stage 1 sees every scoring Requirement and proposes section
keys/titles/order, section aggregation, section bounds/caps/weights/inclusion, review domains (职责),
and the overall rule. Each proposed section and the overall rule require verified citations; stage 1
does not propose item order, and is not required to cite every Requirement. The service validates the proposed structure
and citations against the full Requirement/ref allowlist before admitting stage 2. Invalid or failed
stage 1 fails the job without item-generation calls.

Stage 2 generates item details in batches using `llm_batch_chars`; batching and concurrency are
implemented in the Provider. Every batch receives the entire fixed stage-1 section list as immutable
context, and its structure hash is included in the call/input manifest. Item generation cannot alter
that structure. Unknown section references, cross-batch references, and unresolved Requirements are
rejected. A stage-2 failure retains other valid sections/items as partial output with exit 5, including
validated sections when no item was accepted. Ordinary HTTP retries repeat the same fixed request or
batch.

The write-free preview upper bound covers both stages, including the entire fixed section list
repeated in each stage-2 batch. Because the structure is unknown at preview time, the item-call
input bound conservatively uses twice the enforced compact request-byte ceiling plus the shared
framing allowance. Each actual item batch must fit the configured byte ceiling before admission. Stage-specific prompt and schema versions are part of preview
cache/input identity. Old queued jobs with incompatible versions fail explicitly and must be
resubmitted. The published rubric hash derives from the manifest containing stage-1 output and its
structure hash. The queued job `cache_key` remains the preview input; submission stores
`preview_input_hash` as provenance. The existing rubric `input_manifest` stores the verified proposal.
Each call continues through shared per-call admission, reservation, metering, and settlement. Preflight carries a quote for the structure
call and each planned item batch; actual calls obtain quotes for their exact request bodies. Shared
[task-budget admission](task-budgets.md) reserves platform charges or direct-provider liability.
Cached previews resolve the same actor-bound cache key as submission, independently of the final
published structure hash.

Scoring fixes the rubric set, coverage decisions, section/item revisions and DraftRun partitions.
Only fixed confirmed response text and deviation notes become bid-side inputs; all confirmed
response rows are candidate support, including rows for other requirements. Comply-only (须遵守) and gap
anchors contribute metadata and tender text. Current card revision numbers are used only to detect
staleness; current card pointers, candidate text, material bodies, released documents, templates and
page images are not scoring inputs.

The score provider receives redacted tender, normalized rule and confirmed-response refs with the
assessment date. Local acceptance checks each batch's item/ref allowlist and verifies unique,
continuous quotes against both actual sent text and the fixed tender location or response field.
Assessed items require tender and bid citations, bounded scores and reasons for lost points.
Ambiguous rules, external/price comparisons, unsupported formulas, missing support, thin promises
and redacted-value dependence stay unassessable. Unsafe reasons and unknown placeholders cannot be
saved. Verified supporting responses have normalized database links.

Aggregation uses Decimal without intermediate rounding. Sum, weighted sum and capped sum round only
final section (正式件) and overall outputs to eight decimal places with ROUND_HALF_UP. Weights must sum
exactly to one at their own node. Unassessable included children and unsupported aggregations make
the parent unavailable; assessed subtotals remain visibly separate from totals.

The rubric and score workers share per-call admission and settlement with check jobs. A later provider
or admission failure can retain earlier valid stage-2 batches or score batches in a partial result.
Cancellation, lease loss, input changes or accounting failure prevent publication without erasing occurred usage. Publication
rechecks rubric confirmation and draft currency under locks and database triggers. Report reads
reauthorize fixed parents and recompute staleness without rewriting history. Section and overall
aggregates are stored on the immutable report; item support and citations use separate RLS tables.

## Pitfalls

A complete rubric means that it covers the scoring requirements saved by the specified extraction.
It does not prove that extraction found every scoring rule in the tender. Generation never scans the
document or adjacent chunks to discover missing requirements.

The real-provider evidence and decision rationale are recorded in the
[score-generation changelog](../changelog.md#2026-10-05-two-stage-score-rubric-generation).

Rubric confirmation and advisory scoring do not modify response cards (响应卡), confirm evidence, fill
confidential values, or produce an official tender score. The boundaries are fixed in the
[score contract](../plan/score.md). Formula text is retained as text and is never evaluated.
Rejected or ambiguous model citations are not repaired into another source.

Model citation uniqueness is relative to the pinned `Source.quote`, not every sentence on its
PDF page or Word block. [`locate_source_citation_span`](../../server/app/services/extraction.py)
first locates the full Source with the extraction boundary preference, then locates the citation
only inside that original span and maps its offsets back to the page/block. A repeated sentence
outside the Source does not invalidate the citation. An absent or ambiguous Source, an out-of-span
citation, or a citation repeated within the Source still fails with the existing location reason.
Rubric generation and score execution share this check; sent-text verification remains required.

Sent-side tender verification must use the redacted `source_quote` segment retained as
`sent_source`, through [`locate_sent_source_quote`](../../server/app/services/extraction.py).
A rubric ref also carries the requirement summary and source-position labels; those fields can
repeat a valid quotation or contain words absent from the tender. Searching the assembled ref can
therefore reject a valid citation as ambiguous. A quote found only in the summary or position text
fails with `quote_not_at_position`; repetitions within the sent source segment fail with
`ambiguous_quote`. Score execution keeps normalized rules and metadata in separate refs and uses
the same tender-segment matcher. Original-quote and pinned-span checks remain independent gates.

Provider JSON shape validation must not enforce confirmed-rule semantics. A formula section with
`cap: 35`, or a `capped_sum` section with an explicit null cap, is a reviewable candidate with
normalization errors. The same applies to unused weights, invalid weight sums and inconsistent
bounds; the [aggregation contract and error table](../plan/score.md#first-version-aggregation-algorithms)
define the codes. Candidate storage and console reads preserve the declared values and verbatim
rule text. Do not move cap into score bounds, relabel an aggregation, or fill missing weights.
Malformed types/keys/enums and invalid citations still fail their existing checks.

Candidate numeric storage checks differ from confirmation checks. Migration
[`0044_score_rubric_candidates.py`](../../server/migrations/versions/0044_score_rubric_candidates.py)
permits invalid numeric declarations only as unconfirmed history; the subject and set gates still
check their strict invariants independently of the stored error list. A human fixes them through
the existing complete replacement flow and repeats classification, coverage and confirmation.
The console's “待处理问题” tab displays every normalization error code. The generation wire schema
version participates in the preview identity, so queued work with an incompatible schema must be
resubmitted from a new preview.

Output-token exhaustion is `provider_output_truncated`, distinct from malformed JSON/schema
`invalid_provider_output`; the [Provider failure table](../plan/score.md#providers-jobs-and-prepaid-billing)
defines both. Lower reasoning or raise `BID_LLM_MAX_OUTPUT_TOKENS`, then preview and resubmit.
Stage-1 truncation makes no item calls; stage-2 truncation can retain independent valid batches,
with the same action guidance in job warnings and the incurred usage preserved.

Generated item keys use the fixed tender-ref prefix and a local item ordinal, such as `r1.item-1`.
Embedding numeric local UUIDs in free-text keys can match bank-account or phone redaction rules and
leave valid items unresolved. Identifiers must be chosen without weakening sensitive-text checks.

Phase A has one narrower implementation limitation than the approved redacted-provider flow. If
redaction would change a fixed `Source.quote` or `Source.location`, preview reports
`sensitive_scoring_source` and submission refuses before creating a job. The persisted Source must
remain an exact citation, confidential plaintext cannot be saved in the rubric, and the current
schema has no placeholder-safe Source representation. This is an implementation limitation, not a
new contract default. A later schema and contract change is required before such scoring sources can
be sent safely. Ordinary unknown, ambiguous, or invalid model citations still remain unresolved.

Report titles and recorded aggregation wording use the current redaction library. If a newly
registered confidential value overlaps an immutable section key, preview returns
`sensitive_report_identifier` and submission stops before a call. Section keys bind the persisted
summary to confirmed rubric sections and cannot be replaced with placeholders without changing the
report contract; this narrow representation limit preserves both the fixed key and confidentiality.

## Code

- `server/app/schemas/score_contracts.py` defines the rubric, score, provider and report contracts.
- `server/app/services/score.py`, `score_inputs.py`, and `score_normalization.py` enforce fixed inputs,
  human review, and deterministic completeness.
- `server/app/services/score_generation.py`, `server/app/providers/rubric.py`, and
  `server/app/jobs/score_rubric.py` enforce provider acceptance and guarded publication.
- `server/app/api/score.py` exposes tenant-scoped rubric and score routes.
- `cli/bid_cli/score.py` implements noninteractive rubric/score commands and exit semantics.
- `server/migrations/versions/0034_score_rubric.py` defines forced RLS, tenant-bound relationships,
  immutable review history, and runtime grants.
- `server/tests/test_score_cli.py` covers both CLI transports without a database; score service,
  migration, API, and worker tests cover the persistent boundary.

- `server/app/services/score_run_inputs.py` selects fixed draft input and enforces current-input gates.
- `server/app/services/score_execution.py` handles scoring previews, submissions and report reads.
- `server/app/services/score_semantic.py`, `server/app/providers/scoring.py` and
  `server/app/jobs/score.py` implement local acceptance, aggregation and accounted model execution.
- `server/migrations/versions/0035_score.py` defines score report RLS, immutable children and
  worker-attempt publication gates.
- `server/tests/test_score_run.py`, `test_score_execution_provider.py`, `test_score_report_storage.py`
  and `test_score_run_cli.py` exercise scoring input, calls, reports, isolation and CLI boundaries.
