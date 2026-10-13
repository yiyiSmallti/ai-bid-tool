"""Fenced local report worker; no partial snapshot artifact can be published."""

import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from app.core.errors import ServiceError
from app.jobs.export_render import SERVER_ROOT, private_write, resident_bytes
from app.models.bid_review_report import BidReviewReportArtifact, BidReviewReportSnapshot
from app.services import bid_review as bids
from app.services import bid_review_report as reports
from app.services import bid_review_report_renderer as renderer
from app.services.auth import HUMAN_SCOPES, Identity, set_actor_context
from app.services.versioned import audit

logger = logging.getLogger(__name__)


async def render_process(execution, payload):
    """Terminate and reap the report child on deadline, memory or attempt loss."""
    limit = min(100 * 1024 * 1024, execution.settings.export_max_output_bytes)
    request = json.dumps(
        {
            "snapshot": payload,
            "memory_bytes": execution.settings.export_memory_bytes,
            "max_output_bytes": limit,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    if len(request) > 100 * 1024 * 1024:
        bids.fail("bid_review_output_limit", "Frozen report input exceeds byte bound", 400, 4)
    parent = execution.settings.data_dir / "work" / "bid-review-report"
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="attempt-", dir=parent) as directory:
        root = Path(directory)
        private_write(root / "request.json", request)
        env = {
            key: os.environ[key]
            for key in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")
            if key in os.environ
        }
        env["PYTHONPATH"], env["PYTHONDONTWRITEBYTECODE"] = str(SERVER_ROOT), "1"
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.jobs.bid_review_report_child",
            str(root),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        started = time.monotonic()
        try:
            while child.returncode is None:
                if time.monotonic() - started > execution.settings.export_deadline_seconds:
                    bids.fail(
                        "bid_review_report_timeout", "Report render deadline exceeded", 503, 4
                    )
                if execution.stopped is not None:
                    raise execution.stopped
                async with execution.db.transaction() as session:
                    await execution.owned_job(session)
                if await resident_bytes(child.pid) > execution.settings.export_memory_bytes:
                    bids.fail(
                        "bid_review_report_memory_limit",
                        "Report render memory limit exceeded",
                        400,
                        4,
                    )
                try:
                    await asyncio.wait_for(child.wait(), timeout=0.25)
                except TimeoutError:
                    continue
            if child.returncode != 0:
                bids.fail(
                    "bid_review_report_render_failed", "Report renderer did not complete", 500, 4
                )
            with (root / "result.json").open("rb") as handle:
                receipt = handle.read(4097)
            if len(receipt) > 4096:
                raise ValueError("oversized child receipt")
            result = json.loads(receipt)
            if result.get("error"):
                bids.fail(result["error"], "Local report rendering failed", 500, 4)
            outputs = []
            for name in ("report.docx", "report.json"):
                descriptor = result["files"][name]
                with (root / name).open("rb") as handle:
                    content = handle.read(limit + 1)
                if (
                    not content
                    or len(content) > limit
                    or len(content) != descriptor["size_bytes"]
                    or hashlib.sha256(content).hexdigest() != descriptor["sha256"]
                ):
                    bids.fail(
                        "bid_review_output_integrity",
                        "Report child output integrity failed",
                        500,
                        4,
                    )
                outputs.append(content)
            return tuple(outputs)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ServiceError(
                "bid_review_report_render_failed",
                "Report child returned an invalid receipt",
                500,
                4,
            ) from exc
        finally:
            if child.returncode is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await child.wait()


async def worker_access(session, job, *, bind_context=True):
    if job.actor_kind != "session" or job.actor_token_id is not None:
        bids.fail("human_session_required", "Report work requires a human initiator", 403, 4)
    if job.agent_principal_id is not None or job.agent_session_id is not None:
        bids.fail("human_session_required", "Report work requires a human initiator", 403, 4)
    human = Identity(scopes=set(job.actor_scopes) & HUMAN_SCOPES)
    await reports.report_access(
        session,
        human,
        job.task_id,
        scope="bid-review:report:render",
        write=True,
        bind_context=False,
    )
    worker = Identity(
        scopes=human.scopes,
        actor_kind="worker",
        job_id=job.id,
        run_id=job.run_id,
    )
    if bind_context:
        await set_actor_context(session, worker)
    return worker


async def process(execution, storage):
    async with execution.db.transaction() as session:
        job = await execution.owned_job(session)
        await worker_access(session, job)
        snapshot = await session.get(
            BidReviewReportSnapshot, UUID(job.result["submission"]["snapshot_id"])
        )
        if (
            snapshot is None
            or snapshot.job_id != job.id
            or snapshot.input_hash != job.result["submission"]["input_hash"]
        ):
            bids.fail("bid_review_input_changed", "Report snapshot binding changed", 409, 4)
        payload = reports.open_snapshot(snapshot, execution.settings)
        if snapshot.renderer_identity != renderer.RENDERER_IDENTITY:
            bids.fail(
                "renderer_profile_mismatch", "Retained report renderer is unavailable", 409, 4
            )
        snapshot_id, task_id, submission_id, review_id = (
            snapshot.id,
            snapshot.task_id,
            snapshot.submission_id,
            snapshot.review_id,
        )
    docx_bytes, console_bytes = await render_process(execution, reports.current_snapshot(payload))
    staged = []
    for format_value, content in (("docx", docx_bytes), ("console", console_bytes)):
        if not content or len(content) > 100 * 1024 * 1024:
            bids.fail("bid_review_output_limit", "Rendered report exceeds byte bound", 400, 4)
        sha256 = hashlib.sha256(content).hexdigest()
        key = f"bid-review/{submission_id}/reports/{snapshot_id}/{sha256}.{format_value}"
        logger.info(
            "bid_review.report_object_staged snapshot_id=%s job_id=%s run_id=%s format=%s sha256=%s",
            snapshot_id,
            execution.job_id,
            execution.run_id,
            format_value,
            sha256,
        )
        await storage.put(key, content)
        verified = await storage.read_bounded(key, len(content))
        if verified != content:
            bids.fail("bid_review_output_integrity", "Staged report object differs", 409, 4)
        staged.append((format_value, sha256, len(content), key))
    async with execution.db.transaction() as session:
        job = await execution.owned_job(session)
        actor = await worker_access(session, job)
        snapshot = await session.get(BidReviewReportSnapshot, snapshot_id)
        if snapshot.input_hash != payload["input_hash"]:
            bids.fail("bid_review_input_changed", "Report snapshot binding changed", 409, 4)
        for format_value, sha256, size, key in staged:
            session.add(
                BidReviewReportArtifact(
                    id=uuid4(),
                    task_id=task_id,
                    submission_id=submission_id,
                    review_id=review_id,
                    snapshot_id=snapshot_id,
                    run_id=execution.run_id,
                    format=format_value,
                    sha256=sha256,
                    size_bytes=size,
                    storage_key=key,
                )
            )
        await session.flush()
        values = await reports.artifacts(session, snapshot)
        job.result = {
            **job.result,
            "snapshot_id": str(snapshot_id),
            "review_id": str(review_id),
            "artifacts": [v.model_dump(mode="json") for v in values],
        }
        audit(
            session,
            actor,
            "bid_review.report_published",
            snapshot.id,
            {
                "task_id": str(task_id),
                "job_id": str(job.id),
                "run_id": str(execution.run_id),
                "input_hash": snapshot.input_hash,
                "decisions_snapshot_sha256": snapshot.decisions_snapshot_sha256,
            },
        )
        await execution.owned_job(session)
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)


async def failure_audit(session, job, code):
    actor = await worker_access(session, job)
    audit(
        session,
        actor,
        "bid_review.report_failed",
        job.id,
        {
            "task_id": str(job.task_id),
            "job_id": str(job.id),
            "run_id": str(job.run_id),
            "error_code": code,
        },
    )
