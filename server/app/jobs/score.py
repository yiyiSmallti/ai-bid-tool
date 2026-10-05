"""Durable scoring, with per-call accounting and a live publication fence."""

import json
from datetime import UTC, date, datetime
from uuid import UUID

from cryptography.fernet import InvalidToken

from app.core.security import Secrets
from app.jobs.execution import JobExecution
from app.providers.base import ProviderFailure
from app.providers.scoring import score_provider
from app.schemas.score_contracts import ScoreProviderResult
from app.services import response_cards as cards
from app.services import score_execution, score_generation, score_run_inputs, score_semantic


async def current_snapshot(session, actor, task_id, submitted, settings, job=None):
    manifest = submitted["input_manifest"]
    fixed = await score_run_inputs.snapshot(
        session,
        actor,
        task_id,
        UUID(manifest["draft_id"]),
        UUID(manifest["rubric_id"]),
        date.fromisoformat(manifest["assessment_date"]),
    )
    llm = (
        await score_execution.resolve(session, settings, job)
        if fixed.manifest["model_redaction_enabled"]
        else None
    )
    llm = await score_execution.prepare(session, fixed, llm, manifest["reasoning"], settings)
    if fixed.input_hash != submitted["input_hash"] or fixed.manifest != manifest or llm is None:
        cards.fail("score_input_changed", "Scoring inputs changed; preview and submit again", 409)
    return fixed, llm


async def process(execution: JobExecution, storage) -> None:
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        actor = await score_run_inputs.access(session, score_execution.worker(job), "score:run")
        submitted = job.result["submission"]
        task_id = job.task_id
        if task_id is None:
            score_run_inputs.integrity()
        fixed, llm = await current_snapshot(
            session, actor, task_id, submitted, execution.settings, job
        )
        try:
            saved = json.loads(
                Secrets.for_data(execution.settings).decrypt(submitted["encrypted_input"])
            )
        except (InvalidToken, ValueError, TypeError):
            cards.fail(
                "score_input_integrity", "Encrypted scoring input cannot be verified", 409, 4
            )
        if saved != score_execution.safe_input(fixed):
            score_run_inputs.integrity()

    async def before_admit(session):
        live_job = await execution.owned_job(session)
        live_actor = await score_run_inputs.access(
            session, score_execution.worker(live_job), "score:run"
        )
        await current_snapshot(
            session, live_actor, task_id, submitted, execution.settings, live_job
        )

    execution.before_admit = before_admit
    request = score_semantic.provider_request(fixed.secret["outbound"])
    output = (
        await score_provider(llm).score(request)
        if request
        else ScoreProviderResult(batches=[], usages=[])
    )
    stop_reason = output.failure.code if output.failure else None
    if output.failure and (output.failure.code in score_execution.HARD_STOPS or not output.batches):
        raise ProviderFailure(
            "Scoring stopped; see the error code",
            code=output.failure.code,
            retryable=output.failure.retryable,
            refused=output.failure.refused,
            usage=output.usages,
        )
    if (
        execution.stopped is not None
        and execution.stopped.code not in score_execution.PARTIAL_STOPS
    ):
        raise execution.stopped
    async with execution.db.transaction(execution.org_id) as session:
        await score_run_inputs.lock_inputs(session, actor, task_id, fixed.extraction.id)
        job = await execution.owned_job(session)
        actor = await score_run_inputs.access(session, score_execution.worker(job), "score:run")
        current, _ = await current_snapshot(
            session, actor, task_id, submitted, execution.settings, job
        )
        if score_execution.safe_input(current) != saved:
            cards.fail("score_input_changed", "Scoring inputs changed during assessment", 409)
        _, library = await score_generation.secret_library(session, task_id, execution.settings)
        evaluated = score_semantic.accept_batches(
            current.secret, current.secret["outbound"], output, library
        )
        await score_generation.usage_integrity(
            session, job, execution.run_id, output.usages, kind="score"
        )
        result = await score_execution.publish(
            session, actor, job, current, evaluated, execution.settings, stop_reason
        )
        if (
            execution.stopped is not None
            and execution.stopped.code not in score_execution.PARTIAL_STOPS
        ):
            raise execution.stopped
        await execution.owned_job(session)
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
        job.result = {"submission": submitted, **result}
