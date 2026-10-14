# Background jobs

## Problem

Parsing and extraction need durable state, repeatable requests and safe
cancellation, rather than processing inside the request transaction.

## Usage

Upload first, then run `bid tender parse --document <id> --wait --json`.
Inspect jobs with `bid job status <id>`; cancel with `bid job cancel <id>`.
`--dry-run` writes nothing. Identical inputs reuse the job and its results.

## How it works

Ordinary process-job submission and its Procrastinate delivery commit in the same
PostgreSQL transaction through `dispatch_process_job`. A dispatch interruption
rolls both back and returns `queue_unavailable`; repeat the original request.
No newly saved job ID is promised on that failure. Read-only previews create
neither business jobs nor deliveries. Independently retained memory-feedback
candidates keep the recovery semantics in [memory management](memory-management.md).

Repeating an already queued request locks its business row and verifies that its
delivery belongs to the same job and is either waiting or owned by a live queue
worker. A stale queue ID alone cannot suppress rescheduling. Worker startup and
periodic reconciliation also inspect abandoned process deliveries in bounded
batches. Only still-queued ordinary jobs whose current delivery matches the
abandoned record and have no pending/unknown vendor call are redispatched; the
abandoned delivery is closed. Concurrent recovery must retain one runnable delivery. Agent-owned
and sandbox work use their separate recovery mechanisms.

Workers claim queued state under a row lock.
External work runs outside that transaction, allowing cancellation to commit.
Before committing results, workers recheck cancellation and `run_id`. Usage is retained even
when an extraction result is invalid or cancelled. Provider transient failures
retry at most three times. Citation checks reject individual invalid quotes;
fatal extraction or guard failures save no partial requirement list.
Star rules merge with valid provider results. No rules pretend to be a real LLM.

`JobExecution.activate` in [execution.py](../../server/app/jobs/execution.py)
starts an independent lease heartbeat, including while a vendor HTTP request
or retry delay is pending. Renewal is conditional on the job still running,
the same `run_id`, and an unexpired lease. It cannot revive an expired attempt
or extend its successor's lease. Renewal failure prevents further admission;
the next call also checks live ownership under the job row lock.

Every model request inherits this execution context through
[calls.py](../../server/app/providers/calls.py), rather than storing attempt
state on an adapter shared by concurrent jobs. Cancellation or takeover fences
new calls. An already admitted request may finish and settle its usage even
after losing ownership, but cannot save requirements or replace the new job's
status. Cooperative worker cancellation drains these issued requests and
their settlement before propagating cancellation. This can take up to the
vendor request deadline plus accounting time.

The call ledger persists before HTTP dispatch; response usage is committed
before processing output.
No partial extraction is published on a
guard failure.
Model card generation activates the same execution context;
its publication and partial-result rules are defined in
[response-cards.md](response-cards.md#model-proposals).

Configure lease and heartbeat settings using
[development.md](../guides/development.md#configure-job-guards).

## Pitfalls

An unconfigured production LLM fails explicitly.
Test fixtures are labelled and
only injected by tests.
Native PDF work runs in one disposable child per parse;
OCR calls and usage accounting remain in the worker.
Child resource failures are
terminal, non-retryable `pdf_resource_limits` failures.
Process limits, mixed-page
OCR selection and incomplete-page warnings are defined in [PDF parsing](pdf-parsing.md).
Local OCR requires actual Tesseract language data.
DOCX layout is unverified and cannot provide valid page
citations until converted to PDF.
No Word page numbers are guessed.
Queue permissions apply only to internal dispatch tables.
There is no agent
confirmation/export endpoint.
Failed/cancelled identical jobs stay terminal until an explicit `--retry`; a new
attempt UUID prevents older cancelled workers from overwriting the new attempt.
`--retry` may reclaim an expired lease; it cannot take a live, renewed lease.
Interactive progress/SSE is
separate from the lease heartbeat.

Automatic process reconciliation does not take over `running` jobs, even when
their lease expired. An abandoned queued job with an unresolved vendor call
cannot be redispatched by repeating its submission; `job_recovery_required`
requires explicit outcome reconciliation. Legacy queued business rows with no
retained queue delivery cannot be discovered by this sweep and need an authorized
repeat request. Recovery cannot infer that a missing worker sent no provider request.

## Code

- [Queue](../../server/app/jobs/queue.py): `dispatch_process_job`,
  `ensure_process_delivery`, `recover_process_jobs` and transaction-bound insertion.
- [Worker](../../server/app/jobs/worker.py) `run`: startup reconciliation and queue lifecycle.
- [Tender submission](../../server/app/services/tender_jobs.py) `submit` and
  [processor](../../server/app/jobs/processor.py) `Processor`: fixed-input reuse,
  locked claims and guarded publication.
- [Delivery recovery acceptance](../../server/tests/test_process_delivery_recovery.py),
  [runtime integration](../../server/tests/test_runtime_integration.py),
  [job boundaries](../../server/tests/test_job_boundaries.py) and
  [vendor call guards](../../server/tests/test_vendor_call_guards.py).

Reference: https://procrastinate.readthedocs.io/en/stable/quickstart.html
