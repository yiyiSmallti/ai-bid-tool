# Background jobs

## Problem

Parsing and extraction need durable state, repeatable requests, tenant context and
safe cancellation, rather than processing inside the request transaction.

## Usage

Upload first, then run `bid tender parse --document <id> --wait --json`.
Inspect jobs with `bid job status <id>`; cancel with `bid job cancel <id>`.
`--dry-run` writes nothing. Identical inputs reuse the job and its results.

## How it works

The RLS business job is committed before dispatch to Procrastinate. If dispatch
fails, the API returns a retryable error; a repeat request schedules the durable
job. Workers claim queued state under a row lock and restore tenant context.
External work runs outside that transaction, allowing cancellation to commit.
Before committing results, workers recheck cancellation. Usage is retained even
when an extraction result is invalid or cancelled. Provider transient failures
retry at most three times. Citation validation is atomic: any invalid document,
chunk, page or quote rejects the extraction without partial requirements.
Star rules merge with valid provider results. No rules pretend to be a real LLM.

## Pitfalls

An unconfigured production LLM fails explicitly. Test fixtures are labelled and
only injected by tests. Native PDF text parsing is real; local OCR requires actual
Tesseract language data. DOCX layout is unverified and cannot provide valid page
citations until converted to PDF. No Word page numbers are guessed.
Queue permissions apply only to internal dispatch tables. There is no agent
confirmation/export endpoint. Failed/cancelled identical jobs stay terminal until an explicit `--retry`; a new
attempt UUID prevents older cancelled workers from overwriting the new attempt.
Operational recovery of abandoned jobs and interactive progress/SSE remain work
for the next iteration and are not claimed as verified in this delivery.

## Code

`server/app/jobs/`, `services/parsing.py`, `services/extraction.py`,
`tests/test_runtime_integration.py`, `tests/test_job_boundaries.py`.

Reference: https://procrastinate.readthedocs.io/en/stable/quickstart.html
