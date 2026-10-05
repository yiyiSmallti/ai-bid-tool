"""Durable rubric generation fenced by one owned job attempt."""

import json
from datetime import UTC, datetime
from uuid import UUID

from cryptography.fernet import InvalidToken

from app.core.security import Secrets
from app.jobs.execution import JobExecution
from app.providers.base import ProviderFailure
from app.providers.rubric import (
    ITEMS_PROMPT_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    STRUCTURE_PROMPT_VERSION,
    rubric_provider,
)
from app.schemas.check_contracts import AssessmentFailure
from app.schemas.score_contracts import RubricProviderResult
from app.services import response_cards as cards
from app.services import score_generation, score_inputs


async def current_snapshot(session, actor, task_id, submitted, settings, job=None):
    manifest = submitted["input_manifest"]
    if any(
        manifest.get(key) != version
        for key, version in (
            ("prompt_version", PROMPT_VERSION),
            ("schema_version", SCHEMA_VERSION),
            ("structure_prompt_version", STRUCTURE_PROMPT_VERSION),
            ("items_prompt_version", ITEMS_PROMPT_VERSION),
        )
    ):
        cards.fail(
            "score_rubric_version_unsupported",
            "This queued rubric job uses an obsolete contract; preview and submit again",
            409,
            4,
        )
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
        _, library = await score_generation.secret_library(session, task_id, execution.settings)
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
    structure_output = await adapter.extract_structure(request)
    if structure_output.failure or structure_output.output is None:
        failure = structure_output.failure
        raise ProviderFailure(
            "Rubric structure generation failed; no item calls were made",
            code=failure.code if failure else "invalid_provider_output",
            retryable=failure.retryable if failure else False,
            refused=failure.refused if failure else False,
            usage=structure_output.usages,
        )
    structure = score_generation.accept_structure(
        fixed.secret, fixed.secret["outbound"], structure_output.output, library
    )
    try:
        output = await adapter.extract_items(
            score_generation.items_request(fixed.secret["outbound"], structure)
        )
    except ProviderFailure as error:
        output = RubricProviderResult(
            batches=[],
            usages=error.usage,
            failure=AssessmentFailure(
                code=error.code, retryable=error.retryable, refused=error.refused
            ),
        )
    usages = [*structure_output.usages, *output.usages]
    stop_reason = output.failure.code if output.failure else None
    failures = [*output.failures, *([output.failure] if output.failure else [])]
    hard_failure = next(
        (failure for failure in failures if failure.code in score_generation.HARD_STOPS), None
    )
    if hard_failure is not None:
        raise ProviderFailure(
            "Rubric generation stopped; see the error code",
            code=hard_failure.code,
            retryable=hard_failure.retryable,
            refused=hard_failure.refused,
            usage=usages,
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
        structure = score_generation.accept_structure(
            current.secret, current.secret["outbound"], structure_output.output, library
        )
        accepted = score_generation.accept_batches(
            current.secret,
            current.secret["outbound"],
            structure,
            output.batches,
            library,
        )
        await score_generation.usage_integrity(
            session,
            job,
            execution.run_id,
            usages,
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
        job.result = {"submission": job.result["submission"], **result}
