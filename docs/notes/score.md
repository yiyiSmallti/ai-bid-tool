# Human-reviewed score rubrics

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

## Pitfalls

A complete rubric means that it covers the scoring requirements saved by the specified extraction.
It does not prove that extraction found every scoring rule in the tender. Generation never scans the
document or adjacent chunks to discover missing requirements.

Rubric confirmation does not score a draft, modify response cards, confirm evidence, or produce an
official tender score. The score execution and report commands are a separate implementation phase
tracked in the [score plan](../plan/score.md). Formula text is retained as text and is never evaluated.
Rejected or ambiguous model citations are not repaired into another source.

Phase A has one narrower implementation limitation than the approved redacted-provider flow. If
redaction would change a fixed `Source.quote` or `Source.location`, preview reports
`sensitive_scoring_source` and submission refuses before creating a job. The persisted Source must
remain an exact citation, confidential plaintext cannot be saved in the rubric, and the current
schema has no placeholder-safe Source representation. This is an implementation limitation, not a
new contract default. A later schema and contract change is required before such scoring sources can
be sent safely. Ordinary unknown, ambiguous, or invalid model citations still remain unresolved.

## Code

- `server/app/schemas/score_contracts.py` defines the approved rubric and later score contracts.
- `server/app/services/score.py`, `score_inputs.py`, and `score_normalization.py` enforce fixed inputs,
  human review, and deterministic completeness.
- `server/app/services/score_generation.py`, `server/app/providers/rubric.py`, and
  `server/app/jobs/score_rubric.py` enforce provider acceptance and guarded publication.
- `server/app/api/score.py` exposes tenant-scoped rubric routes.
- `cli/bid_cli/score.py` implements the noninteractive rubric commands and exit semantics.
- `server/migrations/versions/0034_score_rubric.py` defines forced RLS, tenant-bound relationships,
  immutable review history, and runtime grants.
- `server/tests/test_score_cli.py` covers both CLI transports without a database; score service,
  migration, API, and worker tests cover the persistent boundary.
