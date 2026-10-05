"""Durable rubric generation fenced by one owned job attempt."""

import json
from datetime import UTC, datetime
from uuid import UUID

from cryptography.fernet import InvalidToken

from app.core.security import Secrets
from app.jobs.execution import JobExecution
from app.providers.base import ProviderFailure
from app.providers.rubric import rubric_provider
from app.services import response_cards as cards
from app.services import score_generation, score_inputs


async def current_snapshot(session, actor, task_id, submitted, settings, job=None):
    manifest = submitted["input_manifest"]
    fixed = await score_inputs.snapshot(
        session,
        actor,
        task_id,
        UUID(manifest["extraction_job_id"]),
    )
    llm = await score_generation.resolve(session, settings, job)
    llm = await score_generation.prepare(
        session,
        fixed,
        llm,
        manifest["reasoning"],
        settings,
    )
    if fixed.input_hash != submitted["input_hash"] or fixed.manifest != manifest or llm is None:
        cards.fail(
            "score_rubric_input_changed",
            "Rubric inputs changed; preview and submit again",
            409,
        )
    return fixed, llm


async def process(execution: JobExecution) -> None:
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        actor = await score_inputs.access(
            session,
            score_generation.worker(job),
            "score:rubric:generate",
        )
        submitted = job.result["submission"]
        task_id = job.task_id
        if task_id is None:
            score_inputs.integrity()
        fixed, llm = await current_snapshot(
            session,
            actor,
            task_id,
            submitted,
            execution.settings,
            job,
        )
        try:
            saved = json.loads(
                Secrets.for_data(execution.settings).decrypt(submitted["encrypted_input"])
            )
        except (InvalidToken, ValueError, TypeError):
            cards.fail(
                "score_rubric_input_integrity",
                "The encrypted rubric input cannot be verified",
                409,
                4,
            )
        if saved != score_generation.safe_input(fixed):
            cards.fail(
                "score_rubric_input_integrity",
                "The encrypted rubric input does not match its manifest",
                409,
                4,
            )
    adapter = rubric_provider(llm)
    request = score_generation.provider_request(fixed.secret["outbound"])

    async def before_admit(session):
        live_job = await execution.owned_job(session)
        live_actor = await score_inputs.access(
            session,
            score_generation.worker(live_job),
            "score:rubric:generate",
        )
        await current_snapshot(
            session,
            live_actor,
            task_id,
            submitted,
            execution.settings,
            live_job,
        )

    execution.before_admit = before_admit
    output = await adapter.extract_rubric(request)
    stop_reason = output.failure.code if output.failure else None
    if output.failure and (
        output.failure.code in score_generation.HARD_STOPS or not output.batches
    ):
        raise ProviderFailure(
            "Rubric generation stopped; see the error code",
            code=output.failure.code,
            retryable=output.failure.retryable,
            refused=output.failure.refused,
            usage=output.usages,
        )
    if (
        execution.stopped is not None
        and execution.stopped.code not in score_generation.PARTIAL_STOPS
    ):
        raise execution.stopped
    async with execution.db.transaction(execution.org_id) as session:
        await score_inputs.lock_inputs(session, actor, task_id, fixed.extraction.id)
        job = await execution.owned_job(session)
        actor = await score_inputs.access(
            session,
            score_generation.worker(job),
            "score:rubric:generate",
        )
        current, _ = await current_snapshot(
            session,
            actor,
            task_id,
            submitted,
            execution.settings,
            job,
        )
        if score_generation.safe_input(current) != saved:
            cards.fail(
                "score_rubric_input_changed",
                "Rubric inputs changed during generation",
                409,
            )
        _, library = await score_generation.secret_library(session, task_id, execution.settings)
        accepted = score_generation.accept_batches(
            current.secret,
            current.secret["outbound"],
            output.batches,
            library,
        )
        await score_generation.usage_integrity(
            session,
            job,
            execution.run_id,
            output.usages,
        )
        result = await score_generation.publish(
            session,
            actor,
            job,
            current,
            accepted,
            execution.settings,
            stop_reason,
        )
        if (
            execution.stopped is not None
            and execution.stopped.code not in score_generation.PARTIAL_STOPS
        ):
            raise execution.stopped
        await execution.owned_job(session)
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
        job.result = {"submission": submitted, **result}
