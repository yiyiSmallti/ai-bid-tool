"""Sandbox job dispatch, live authorization fencing and publication after cleanup."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import ServiceError
from app.core.security import Secrets
from app.jobs.execution import JobExecution, locked_job
from app.models.entities import Job
from app.models.sandbox import SandboxAttempt, SandboxInput, SandboxRun
from app.providers.base import ProviderFailure
from app.providers.sandbox_fetch import FetchDenied
from app.providers.sandbox_runtime import ExecutionResult, SandboxFailure
from app.services import sandbox as service
from app.services.auth import Identity


async def watch(execution: JobExecution, run_id: UUID) -> None:
    while True:
        await asyncio.sleep(1)
        if execution.stopped is not None:
            raise execution.stopped
        async with execution.db.transaction(execution.org_id) as session:
            await execution.owned_job(session)
            run = await session.get(SandboxRun, run_id)
            assert run is not None
            row = await session.get(SandboxInput, run.input_id)
            assert row is not None
            actor = await service.actor_for(session, run)
            await service.check_live(
                session,
                actor,
                run,
                row,
                action=(
                    "sandbox:render" if row.purpose == "prototype_offline" else "sandbox:capture"
                ),
            )


def safe_failure(
    exc: ServiceError
    | ProviderFailure
    | SandboxFailure
    | FetchDenied
    | SQLAlchemyError
    | OSError
    | InvalidToken,
):
    if isinstance(exc, InvalidToken):
        return "sandbox_input_integrity", 4
    if isinstance(exc, ServiceError):
        return exc.code, exc.exit_code
    if isinstance(exc, FetchDenied):
        error = service.fetch_error(exc)
        return error.code, error.exit_code
    if isinstance(exc, ProviderFailure):
        return exc.code, 3 if exc.retryable else 4
    if isinstance(exc, OSError):
        return "sandbox_storage_unavailable", 3
    if isinstance(exc, SQLAlchemyError):
        return "sandbox_database_unavailable", 3
    transient = {
        "sandbox_disabled",
        "sandbox_supervisor_unavailable",
        "sandbox_runtime_failure",
        "sandbox_runtime_not_accepted",
        "sandbox_node_capacity",
        "sandbox_org_capacity",
        "cleanup_pending",
        "sandbox_node_quarantined",
        "sandbox_metrics_unavailable",
    }
    code = exc.code if exc.code.startswith("sandbox_") else "sandbox_" + exc.code
    return code, 3 if exc.code in transient else 4


async def process_if_sandbox(processor, org_id: UUID, job_id: UUID) -> bool:
    # Existing processors retain their complete claim/usage path for all other job kinds.
    async with processor.db.transaction(org_id) as session:
        job = await session.get(Job, job_id)
        if job is None or job.kind != "sandbox":
            return False
        run = await session.scalar(select(SandboxRun).where(SandboxRun.job_id == job_id))
        if run is None:
            raise ServiceError("sandbox_run_missing", "Sandbox run is missing", 409, 4)
        task_id, run_id = run.task_id, run.id
    browser = service.browser_for(processor)
    await service.reconcile_run(processor.db, run_id, org_id, browser)
    crypto = Secrets.for_data(processor.settings)
    actor = None
    attempt = None
    result: ExecutionResult | None = None
    broker = None
    descriptor = None
    source_url = None
    failure_metrics = None
    failure_versions = {}
    executing = None
    guardian = None
    run_claimed = False
    execution = None
    cleanup_state = "complete"  # No external instance has been requested yet.
    error_code, exit_code = "sandbox_failed", 4
    cancelled = False
    try:
        async with processor.db.transaction(org_id) as session:
            current = await locked_job(session, job_id)
            await service.org_lock(session, org_id)
            assert current is not None
            if current.status in {"succeeded", "failed", "cancelled"}:
                return True
            now = datetime.now(UTC)
            if current.status == "running" and current.lease_until and current.lease_until > now:
                return True
            previous = await service.last_attempt(session, run_id)
            if previous is not None and not await service.cleanup_confirmed(session, previous):
                raise ServiceError(
                    "sandbox_cleanup_pending",
                    "Previous attempt needs cleanup reconciliation",
                    503,
                    3,
                )
            queued_at = datetime.fromisoformat(current.result["queued_at"])
            if now - queued_at > timedelta(minutes=10):
                raise ServiceError(
                    "sandbox_queue_timeout", "Sandbox queue deadline reached", 503, 3
                )
            running = await session.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.kind == "sandbox", Job.status == "running", Job.id != job_id)
            )
            if running is not None and running >= 2:
                # Queue infrastructure retries dispatch; no new sandbox attempt is consumed.
                raise ProviderFailure(
                    "Sandbox organization capacity reached",
                    retryable=True,
                    code="sandbox_org_capacity",
                )
            run = await session.get(SandboxRun, run_id)
            assert run is not None
            row = await session.get(SandboxInput, run.input_id)
            assert row is not None
            actor = await service.actor_for(session, run)
            await service.check_live(
                session,
                actor,
                run,
                row,
                action=(
                    "sandbox:render" if row.purpose == "prototype_offline" else "sandbox:capture"
                ),
            )
            if browser.profile_digest != run.runtime_profile_digest:
                raise ServiceError(
                    "sandbox_profile_changed", "Sandbox runtime profile changed", 409, 4
                )
            if current.attempts >= 3:
                raise ServiceError(
                    "sandbox_attempt_limit", "Sandbox attempt ceiling reached", 409, 4
                )
            attempt_id = uuid4()
            current.status, current.run_id = "running", attempt_id
            current.attempts += 1
            current.lease_until = now + timedelta(seconds=processor.settings.job_lease_seconds)
            current.error = None
            await session.flush()
            descriptor = await service.budgeted_descriptor(session, run, row, attempt_id)
            attempt = SandboxAttempt(
                id=uuid4(),
                org_id=org_id,
                sandbox_run_id=run.id,
                input_id=row.id,
                task_id=task_id,
                job_id=job_id,
                attempt_id=attempt_id,
                instance_group_ref_hash=descriptor.digest,
                descriptor=service.descriptor_json(descriptor),
                started_at=now,
                cleanup_state="pending",
                metrics={},
                runtime_versions={},
                issues=[],
            )
            session.add(attempt)
            service.event(
                session,
                actor,
                "started",
                run,
                attempt_id=str(attempt_id),
                input_hash=descriptor.input_sha256,
            )
        run_claimed = True
        execution = JobExecution(processor.settings, processor.db, org_id, job_id, attempt_id)
        async with execution.activate():
            if row.purpose == "prototype_offline":
                assert row.object_key is not None
                content = await processor.storage.read_bounded(
                    org_id, row.object_key, row.size_bytes
                )
                if len(content) != row.size_bytes or service.sha(content) != row.html_sha256:
                    raise ServiceError(
                        "sandbox_input_changed", "Stored input failed integrity checks", 409, 4
                    )
                call = browser.render_prototype(descriptor, content)
            else:
                assert row.encrypted_source_url is not None
                source_url = crypto.decrypt(row.encrypted_source_url)
                if service.sha(source_url.encode("utf-8")) != row.source_url_sha256:
                    raise ServiceError(
                        "sandbox_input_changed", "Stored input failed integrity checks", 409, 4
                    )
                broker = service.make_fetcher(processor, run, row)
                call = browser.capture(descriptor, source_url, broker)
            cleanup_state = (
                "failed"  # Until a trusted matching supervisor receipt confirms removal.
            )
            executing = asyncio.create_task(call)
            guardian = asyncio.create_task(watch(execution, run.id))
            done, _ = await asyncio.wait((executing, guardian), return_when=asyncio.FIRST_COMPLETED)
            if guardian in done:
                await guardian
            result = await executing
            cleanup_state = (
                "complete"
                if result.cleanup_state == "complete"
                and result.descriptor_hash == descriptor.digest
                else "failed"
            )
            if broker is not None:
                broker.close()
            # Object writes are candidates; no database link is created until the final transaction.
            artifacts, metrics = await service.store_artifacts(
                processor.storage, run, row, attempt, result, broker, source_url
            )
            async with processor.db.transaction(org_id) as session:
                current = await execution.owned_job(session)
                await service.org_lock(session, org_id)
                run = await session.get(SandboxRun, run_id)
                assert run is not None
                row = await session.get(SandboxInput, run.input_id)
                assert row is not None
                actor = await service.actor_for(session, run, lock=True)
                await service.check_live(
                    session,
                    actor,
                    run,
                    row,
                    action=(
                        "sandbox:render"
                        if row.purpose == "prototype_offline"
                        else "sandbox:capture"
                    ),
                )
                saved_attempt = await session.get(SandboxAttempt, attempt.id)
                assert saved_attempt is not None
                saved_attempt.ended_at = datetime.now(UTC)
                saved_attempt.termination_code, saved_attempt.cleanup_state = (
                    "succeeded",
                    "complete",
                )
                saved_attempt.metrics, saved_attempt.runtime_versions = (
                    metrics,
                    result.runtime_versions,
                )
                saved_attempt.issues = [
                    {"code": code, "severity": "warning", "object_ids": []}
                    for code in result.issues
                ]
                current.status, current.finished_at, current.error = (
                    "succeeded",
                    datetime.now(UTC),
                    None,
                )
                current.result = {
                    "sandbox_run_id": str(run.id),
                    "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
                }
                await session.flush()
                # Parent source_pdf precedes page children in the fixed artifact order.
                for artifact in artifacts:
                    session.add(artifact)
                    await session.flush()
                await service.save_receipts(
                    session, run, row, saved_attempt, broker, crypto, artifacts
                )
                service.event(
                    session,
                    actor,
                    "completed",
                    run,
                    attempt_id=str(attempt_id),
                    provenance_manifest_hash=artifacts[-1].plaintext_sha256,
                    metrics=metrics,
                )
            return True
    except asyncio.CancelledError:
        cancelled = True
        error_code, exit_code = "sandbox_cancelled", 4
    except (
        ServiceError,
        ProviderFailure,
        SandboxFailure,
        FetchDenied,
        SQLAlchemyError,
        OSError,
        InvalidToken,
    ) as exc:
        error_code, exit_code = safe_failure(exc)
        if (
            isinstance(exc, SandboxFailure)
            and exc.cleanup_state == "complete"
            and descriptor
            and exc.descriptor_hash == descriptor.digest
        ):
            cleanup_state = "complete"
            failure_metrics = exc.metrics
            failure_versions = exc.runtime_versions
        if isinstance(exc, ProviderFailure) and not run_claimed:
            raise
    finally:
        if broker is not None:
            broker.close()
        if guardian is not None:
            guardian.cancel()
            with suppress(asyncio.CancelledError, ServiceError, ProviderFailure, SQLAlchemyError):
                await guardian
        if executing is not None and not executing.done():
            executing.cancel()
            with suppress(asyncio.CancelledError, SandboxFailure, FetchDenied):
                await executing
    if cleanup_state != "complete" and descriptor is not None and hasattr(browser, "recover"):
        try:
            receipt = await browser.recover(descriptor)
        except (SandboxFailure, OSError, TimeoutError):
            receipt = None
        if (
            receipt is not None
            and receipt.descriptor_hash == descriptor.digest
            and receipt.cleanup_state in {"complete", "not_started"}
        ):
            cleanup_state = "complete"
            failure_metrics = receipt.metrics
            failure_versions = receipt.runtime_versions
            if receipt.cleanup_state == "not_started":
                failure_metrics = {key: 0 for key in service.SandboxMetrics.model_fields}
                failure_versions = {"accounting": "no_instance_started"}
    async with processor.db.transaction(org_id) as session:
        current = await locked_job(session, job_id)
        await service.org_lock(session, org_id)
        run = await session.get(SandboxRun, run_id)
        assert current is not None and run is not None
        # Failure audit keeps the initiating identity even when that membership was revoked.
        audit_actor = Identity(
            run.requested_by, org_id, set(), "viewer", run.token_id, run.actor_kind
        )
        saved_attempt = await session.get(SandboxAttempt, attempt.id) if attempt else None
        if saved_attempt is not None and saved_attempt.ended_at is None:
            saved_attempt.ended_at = datetime.now(UTC)
            saved_attempt.termination_code = (
                "cancelled" if current.status == "cancelled" or cancelled else error_code
            )
            saved_attempt.cleanup_state = cleanup_state
            saved_attempt.issues = [{"code": error_code, "severity": "block", "object_ids": []}]
            if result is not None:
                saved_attempt.metrics = result.metrics
                saved_attempt.runtime_versions = result.runtime_versions
            elif descriptor is not None:
                if executing is None:
                    failure_metrics = {key: 0 for key in service.SandboxMetrics.model_fields}
                    failure_versions = {"accounting": "no_instance_started"}
                if (
                    failure_metrics
                    and failure_versions.get("accounting")
                    != "recovered_samples_require_conservative_budget"
                ):
                    saved_attempt.metrics = service.SandboxMetrics.model_validate(
                        failure_metrics
                    ).model_dump()
                    saved_attempt.runtime_versions = failure_versions
                else:
                    saved_attempt.metrics = service.conservative_metrics(descriptor)
                    saved_attempt.runtime_versions = {"accounting": "conservative_bound"}
            await session.flush()
            row = await session.get(SandboxInput, run.input_id)
            assert row is not None
            await service.save_receipts(session, run, row, saved_attempt, broker, crypto, [])
        if not attempt or current.run_id == attempt.attempt_id:
            if current.status != "cancelled":
                current.status = "cancelled" if cancelled else "failed"
                current.error = {
                    "code": error_code,
                    "message": "Sandbox execution did not complete",
                    "exit_code": exit_code,
                    "retryable": exit_code == 3,
                }
                current.finished_at = datetime.now(UTC)
        action = "cancelled" if current.status == "cancelled" else "failed"
        service.event(
            session,
            audit_actor,
            action,
            run,
            reason_code=error_code,
            attempt_id=str(attempt.attempt_id) if attempt else None,
            cleanup_state=cleanup_state,
        )
        if cleanup_state != "complete":
            service.event(
                session, audit_actor, "cleanup_failed", run, reason_code="sandbox_cleanup_pending"
            )
        if error_code in {
            "sandbox_cpu_limit",
            "sandbox_output_limit",
            "sandbox_resource_limit",
            "sandbox_deadline",
        }:
            service.event(session, audit_actor, "resource_limit", run, reason_code=error_code)
        if broker is not None and broker.denials:
            service.event(
                session,
                audit_actor,
                "network_denied",
                run,
                reason_code=error_code,
                denied_count=len(broker.denials),
            )
    # The failure state is committed after activate() exits; attach its budget
    # receipt now. finalize_result() fences cancelled or superseded attempts.
    if execution is not None:
        await execution.finalize_result()
    if cancelled:
        raise asyncio.CancelledError
    return True
