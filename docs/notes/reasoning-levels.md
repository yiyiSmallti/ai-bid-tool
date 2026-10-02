# Reasoning levels and extraction history

## Problem

A model's reasoning level decides how complete, slow, and costly an
extraction is: on GLM-5.3-Flash one 8,000-character batch gave 24 items in 25
seconds at `low` and 59 items in under five minutes at `high`, while `max`
overflowed the output limit. Users need to pick a level per extraction, and
two extractions of the same document must not merge into one list.

## Usage

Operators register the vendor's official levels for a catalog model in the
console model form or with `bid platform model set`; org users choose one with
`bid req extract --reasoning LEVEL`, list runs with `bid req history`, and view
an older run with `bid req list --job JOB_ID`. Commands are in
[cli.md](../guides/cli.md#run-the-tender-workflow); the decision record is
[ADR 0004](../adr/0004-extractions-per-reasoning-level.md).

## How it works

`PlatformModelSet.reasoning` is a list of `ReasoningLevel` in
[platform_contracts.py](../../server/app/schemas/platform_contracts.py): the
official level name, an optional label, request options merged into the body,
an Anthropic `effort`, and a batch size. `default_reasoning` names the
official default; a CHECK in migration `0014` keeps both empty or both set.
Vendors publish no API listing a model's levels, so the catalog is the source.

`platform_llm` attaches the levels to the adapter. `with_reasoning` in
[llm.py](../../server/app/providers/llm.py) resolves the requested or default
level at submission and returns `HTTPExtractor.at_reasoning(name)`, a copy
whose settings carry that level's options, batch size, and effort; the level's
options replace `BID_LLM_REQUEST_OPTIONS`. An unknown level fails with
`unsupported_reasoning` (exit 2). A model without levels ignores the option and
warns. Org models use the same level schema through
[provider configuration](provider-config.md#resolution-and-cache).

The level is stored in `jobs.reasoning` and appended to the cache key, so each
level is a separate job and repeating one returns the existing job. The
processor applies the stored level again before calling the vendor; a level
removed from the catalog in between fails the job.

Requirements carry `job_id`, unique per job and fingerprint instead of per
task. `req list` returns, per document, the requirements of the latest
succeeded extraction (`latest_extractions` in
[main.py](../../server/app/api/main.py)), or those of the job given with
`job`. `GET /tasks/{id}/extractions` lists every extraction job with its level,
the model and counts from `jobs.result`, and whether `req list` shows it by
default.

The console model test calls the vendor once per registered level,
concurrently, and reports each; levels come from the catalog row, so a model
without its key still lists every level as failed.

## Pitfalls

- Zhipu documents that GLM-5.3 models cannot turn thinking off. The Coding
  Plan endpoint accepts `thinking.type=disabled` anyway; that behaviour is
  undocumented and is not registered as a level.
- The highest levels think roughly ten times the input. Give them a smaller
  `batch_chars` (4,000 for GLM `max`), or batches are truncated and halved,
  paying for the truncated call first.
- Changing a catalog model bumps its revision and therefore the cache key;
  earlier extractions stay in the history but are not reused.
- Migration `0014` assigns existing requirements to their document's latest
  succeeded extraction and stops if any requirement has none.

## Code

- [server/app/providers/llm.py](../../server/app/providers/llm.py): `at_reasoning`, `with_reasoning`, `reasoning_choices`, `platform_llm`.
- [server/app/api/main.py](../../server/app/api/main.py): `start_job`, `req_list`, `req_history`.
- [server/app/jobs/processor.py](../../server/app/jobs/processor.py): applying the stored level and saving `job_id`.
- [server/app/services/platform.py](../../server/app/services/platform.py): `test_model`.
- [web/src/views/Models.vue](../../web/src/views/Models.vue): level editing and per-level test results.
- [server/tests/test_reasoning_levels.py](../../server/tests/test_reasoning_levels.py): end-to-end cases.
