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
set of scoring requirements from that extraction. The generation provider receives only the verified
source text for those requirements after configured redaction. It creates candidates and cannot
confirm, classify, revise, or exclude anything.

Rubric sets, sections, items, requirement coverage, normalized coverage links, and append-only review
events use tenant-bound composite keys and forced row-level security. Revisions create a new candidate
set with new child IDs; they never rewrite an earlier set. Expected revision and input hash checks stop
stale decisions. Session-only review scopes and stored review domains enforce the human role boundary;
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

Rubric extraction submits the entire selected scoring table in one model request so section and
overall rules share the same context. Admission counts the complete serialized HTTP JSON in UTF-8
bytes, including prompts, schema and vendor options, using `Settings.rubric_max_request_bytes`.
The [deployment template](../../deploy/.env.example) explains the long-context budget and its token
limitations. An oversized request returns `rubric_context_limit` in preview and is refused on
submission without jobs or calls. Malformed or truncated output fails with occurred usage retained;
it never triggers table splitting. Acceptance requires exactly one complete response with the
fixed table's ID/ref allowlist, then verifies each candidate citation and detects duplicate keys,
orphan sections and missing requirement output. Ordinary HTTP retries retain the whole request.

Scoring fixes the rubric set, coverage decisions, section/item revisions and DraftRun partitions.
Only fixed confirmed response text and deviation notes become bid-side inputs; all confirmed
response rows are candidate support, including rows for other requirements. Comply-only and gap
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
final section and overall outputs to eight decimal places with ROUND_HALF_UP. Weights must sum
exactly to one at their own node. Unassessable included children and unsupported aggregations make
the parent unavailable; assessed subtotals remain visibly separate from totals.

The score worker shares per-call admission and settlement with rubric/check jobs. A later provider
or admission failure can retain earlier valid batches in a partial report. Cancellation, lease loss,
input changes or accounting failure prevent publication without erasing occurred usage. Publication
rechecks rubric confirmation and draft currency under locks and database triggers. Report reads
reauthorize fixed parents and recompute staleness without rewriting history. Section and overall
aggregates are stored on the immutable report; item support and citations use separate RLS tables.

## Pitfalls

A complete rubric means that it covers the scoring requirements saved by the specified extraction.
It does not prove that extraction found every scoring rule in the tender. Generation never scans the
document or adjacent chunks to discover missing requirements.

Rubric confirmation and advisory scoring do not modify response cards, confirm evidence, fill
confidential values, or produce an official tender score. The boundaries are fixed in the
[score contract](../plan/score.md). Formula text is retained as text and is never evaluated.
Rejected or ambiguous model citations are not repaired into another source.

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
