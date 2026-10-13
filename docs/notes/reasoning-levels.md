---
kind: reference
---

# Reasoning levels and extraction history

## Problem

A model's reasoning level changes extraction completeness, duration, and cost.
Each extraction must retain its own level and requirement history.

## Usage

The human registers the vendor's supported levels with `bid provider set`;
choose one with
`bid req extract --reasoning LEVEL`, list runs with `bid req history`, and view
an older run with `bid req list --job JOB_ID`. Commands are in
[cli.md](../guides/cli.md#run-the-tender-workflow); the decision record is
[ADR 0004](../adr/0004-extractions-per-reasoning-level.md).

## How it works

`ProviderConfigSet.reasoning` is a list of `ReasoningLevel` in
[platform_contracts.py](../../server/app/schemas/platform_contracts.py): the
official level name, an optional label, request options merged into the body,
an Anthropic `effort`, and a batch size. `default_reasoning` names the
configured default; input validation and the baseline CHECK keep both empty or
both set. The saved revision defines the permitted level schema.

`resolve_configured` attaches the saved levels to the adapter. `with_reasoning` in
[llm.py](../../server/app/providers/llm.py) resolves the requested or default
level at submission and returns `HTTPExtractor.at_reasoning(name)`, a copy
whose settings carry that level's options, batch size, and effort; the level's
options replace `BID_LLM_REQUEST_OPTIONS`. An unknown level fails with
`unsupported_reasoning` (exit 2). A model without levels ignores the option and
warns. Model configuration uses the level schema through
[provider configuration](provider-config.md#resolution-and-cache).

The level is stored in `jobs.reasoning` and appended to the cache key, so each
level is a separate job and repeating one returns the existing job. The
processor applies the stored level again before calling the vendor; a level
missing from the pinned configuration fails the job.

Requirements carry `job_id`, unique per job and fingerprint instead of per
task. `req list` returns, per document, the requirements of the latest
succeeded extraction (`latest_extractions` in
[requirements.py](../../server/app/services/requirements.py)), or those of the job given with
`job`. `GET /tasks/{id}/extractions` lists every extraction job with its level,
the model and counts from `jobs.result`, and whether `req list` shows it by
default.

The explicit provider test can check registered levels and reports receipts
against the saved configuration, through the accounted provider-test path.

## Pitfalls

- Zhipu documents that GLM-5.3 models cannot turn thinking off. The Coding
  Plan endpoint accepts `thinking.type=disabled` anyway; that behaviour is
  undocumented and is not registered as a level.
- The highest levels think roughly ten times the input. Give them a smaller
  `batch_chars` (4,000 for GLM `max`), or batches are truncated and halved,
  paying for the truncated call first.
- Saving a model configuration appends a revision and changes the cache key;
  earlier extractions stay in the history but are not reused.

## Code

- [server/app/providers/llm.py](../../server/app/providers/llm.py): `at_reasoning`, `with_reasoning`, `reasoning_choices`.
- [server/app/services/tender_jobs.py](../../server/app/services/tender_jobs.py): `submit`.
- [server/app/services/requirements.py](../../server/app/services/requirements.py): `list_requirements`, `extraction_history`.
- [server/app/jobs/processor.py](../../server/app/jobs/processor.py): applying the stored level and saving `job_id`.
- [server/app/services/provider_configs.py](../../server/app/services/provider_configs.py): `test_config`.
- [server/tests/test_reasoning_levels.py](../../server/tests/test_reasoning_levels.py): end-to-end cases.
