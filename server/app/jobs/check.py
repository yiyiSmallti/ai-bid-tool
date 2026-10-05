"""Confirmed-draft checks, fenced by the durable job attempt at final publication."""

import asyncio
import json
from datetime import UTC, date, datetime
from uuid import UUID

from cryptography.fernet import InvalidToken

from app.core.errors import ServiceError
from app.core.security import Secrets
from app.jobs.execution import JobExecution
from app.providers.base import ProviderFailure
from app.providers.checking import check_provider
from app.providers.llm import with_reasoning
from app.providers.storage import Storage
from app.services import check, check_inputs, check_rules, check_semantic
from app.services import response_cards as cards


async def current_snapshot(session, actor, task_id, submitted, storage, settings=None, job=None):
    manifest = submitted["input_manifest"]
    try:
        fixed = await check_inputs.snapshot(
            session,
            actor,
            task_id,
            UUID(manifest["draft_id"]),
            date.fromisoformat(manifest["assessment_date"]),
            storage,
            semantic=manifest["mode"] == "combined",
        )
    except ServiceError as error:
        if error.code == "check_stale_draft":
            cards.fail(
                "check_input_changed",
                "Draft inputs changed; preview and submit a current draft",
                409,
            )
        raise
    if manifest["mode"] == "combined":
        llm = await check_semantic.resolve(session, settings, job)
        await check_semantic.prepare(session, fixed, llm, manifest["reasoning"], settings)
    if fixed.input_hash != submitted["input_hash"] or fixed.manifest != manifest:
        cards.fail("check_input_changed", "Check inputs changed; preview and submit again", 409)
    return fixed


async def process(execution: JobExecution, storage: Storage) -> None:
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        actor = await check_inputs.access(session, check.worker(job), "check:run")
        submitted = job.result["submission"]
        task_id = job.task_id
        if task_id is None:
            check_inputs.integrity()
        fixed = await current_snapshot(
            session, actor, task_id, submitted, storage, execution.settings, job
        )
        try:
            secret = json.loads(
                Secrets.for_data(execution.settings).decrypt(submitted["encrypted_input"])
            )
        except (InvalidToken, ValueError, TypeError):
            cards.fail(
                "check_input_integrity", "The encrypted check input cannot be verified", 409, 4
            )
        if secret != fixed.secret:
            cards.fail(
                "check_input_integrity",
                "The encrypted check input does not match its manifest",
                409,
                4,
            )
    evaluated = await asyncio.to_thread(check_rules.evaluate, secret, str(fixed.draft.id))
    stop_reason = None
    reported_usages = []
    if fixed.manifest["mode"] == "combined" and secret["outbound"]["requirements"]:
        async with execution.db.transaction(execution.org_id) as session:
            job = await execution.owned_job(session)
            llm = await check_semantic.resolve(session, execution.settings, job)
            llm, _, _ = with_reasoning(llm, job.reasoning)
            _, library = await check_semantic.secret_library(session, task_id, execution.settings)
        adapter = check_provider(llm)
        requests = check_semantic.requests_for(secret["outbound"], llm)
        execution.plan(len(requests))

        async def before_admit(session):
            live_job = await execution.owned_job(session)
            live_actor = await check_inputs.access(session, check.worker(live_job), "check:run")
            await current_snapshot(
                session, live_actor, task_id, submitted, storage, execution.settings, live_job
            )

        execution.before_admit = before_admit
        completed = set()
        for request in requests:
            try:
                output = await adapter.check(request)
            except ProviderFailure as error:
                if (
                    error.code in check_semantic.HARD_STOPS
                    or error.code
                    not in check_semantic.PARTIAL_STOPS
                    | {
                        "provider_unavailable",
                        "provider_refused",
                        "provider_quota_exhausted",
                        "invalid_provider_output",
                        "invalid_provider_model",
                    }
                ):
                    raise
                reported_usages.extend(error.usage)
                stop_reason = error.code
                break
            reported_usages.extend(output.usages)
            check_semantic.accept_batch(
                evaluated, secret["outbound"], request, output.wire, library, str(fixed.draft.id)
            )
            completed.update(request.requested_requirement_ids)
        for local, requirement_id in secret["outbound"]["requirements"].items():
            if local not in completed:
                row = next(
                    row for row in evaluated if row["item"]["requirement_id"] == requirement_id
                )
                check_semantic.reject(row, "semantic_provider_failed")
    elif fixed.manifest["mode"] == "combined":
        execution.plan(0)
    if execution.stopped is not None and execution.stopped.code not in check_semantic.PARTIAL_STOPS:
        raise execution.stopped
    async with execution.db.transaction(execution.org_id) as session:
        await check_inputs.lock_inputs(session, actor, task_id)
        job = await execution.owned_job(session)
        actor = await check_inputs.access(session, check.worker(job), "check:run")
        # Rebuild under the same locks used by task/card edits, membership changes
        # and confidential values. An earlier preview is never a publication permit.
        current = await current_snapshot(
            session, actor, task_id, submitted, storage, execution.settings, job
        )
        if current.secret != secret:
            cards.fail("check_input_changed", "Check inputs changed during rule evaluation", 409)
        if fixed.manifest["mode"] == "combined":
            await check_semantic.usage_integrity(session, job, execution.run_id, reported_usages)
        result = await check.publish(
            session, actor, job, current, evaluated, execution.settings, stop_reason
        )
        if (
            execution.stopped is not None
            and execution.stopped.code not in check_semantic.PARTIAL_STOPS
        ):
            raise execution.stopped
        await execution.owned_job(session)
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
        job.result = {"submission": submitted, **result}
