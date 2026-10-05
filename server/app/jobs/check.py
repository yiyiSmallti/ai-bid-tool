"""Rules-only checks, fenced by the durable job attempt at final publication."""

import asyncio
import json
from datetime import UTC, date, datetime
from uuid import UUID

from cryptography.fernet import InvalidToken

from app.core.errors import ServiceError
from app.core.security import Secrets
from app.jobs.execution import JobExecution
from app.providers.storage import Storage
from app.services import check, check_inputs, check_rules
from app.services import response_cards as cards


async def current_snapshot(session, actor, task_id, submitted, storage):
    manifest = submitted["input_manifest"]
    try:
        fixed = await check_inputs.snapshot(
            session,
            actor,
            task_id,
            UUID(manifest["draft_id"]),
            date.fromisoformat(manifest["assessment_date"]),
            storage,
        )
    except ServiceError as error:
        if error.code == "check_stale_draft":
            cards.fail(
                "check_input_changed",
                "Draft inputs changed; preview and submit a current draft",
                409,
            )
        raise
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
        fixed = await current_snapshot(session, actor, task_id, submitted, storage)
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
    if execution.stopped is not None:
        raise execution.stopped
    async with execution.db.transaction(execution.org_id) as session:
        await check_inputs.lock_inputs(session, actor, task_id)
        job = await execution.owned_job(session)
        actor = await check_inputs.access(session, check.worker(job), "check:run")
        # Rebuild under the same locks used by task/card edits, membership changes
        # and confidential values. An earlier preview is never a publication permit.
        current = await current_snapshot(session, actor, task_id, submitted, storage)
        if current.secret != secret:
            cards.fail("check_input_changed", "Check inputs changed during rule evaluation", 409)
        result = await check.publish(session, actor, job, current, evaluated, execution.settings)
        if execution.stopped is not None:
            raise execution.stopped
        await execution.owned_job(session)
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
        job.result = {"submission": submitted, **result}
