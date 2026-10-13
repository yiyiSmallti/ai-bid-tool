"""Attempt-scoped export rendering in a terminable private child process."""

import asyncio
import ctypes
import errno
import hashlib
import json
import os
import signal
import struct
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, text

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.jobs.execution import JobExecution
from app.models.entities import Job
from app.models.exports import ExportRenderCandidate, ExportRun
from app.providers.storage import Storage
from app.schemas.contracts import Cost
from app.services import confidential, evidence_sources, exports, screenshots, templates
from app.services.auth import set_actor_context
from app.services.versioned import audit

SERVER_ROOT = Path(__file__).resolve().parents[2]


def private_write(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(content)


def darwin_resident_bytes(pid: int) -> int:
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        query = library.proc_pidinfo
        query.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        query.restype = ctypes.c_int
        # Darwin's proc_taskinfo ABI is six uint64_t values followed by twelve
        # int32_t values; its second field is resident bytes. Querying the child
        # directly avoids ps's broader process-list permission requirement.
        layout = struct.Struct("=6Q12i")
        buffer = ctypes.create_string_buffer(layout.size)
        received = query(pid, 4, 0, buffer, layout.size)  # PROC_PIDTASKINFO
        if received == 0 and ctypes.get_errno() == errno.ESRCH:
            return 0
        if received != layout.size:
            raise OSError(ctypes.get_errno(), "Invalid process memory response")
        return layout.unpack(buffer.raw)[1]
    except OSError as exc:
        raise ServiceError(
            "export_memory_monitor_failed", "Could not enforce render memory limit", 503, 4
        ) from exc


async def resident_bytes(pid: int) -> int:
    if sys.platform == "darwin":
        return await asyncio.to_thread(darwin_resident_bytes, pid)
    if sys.platform == "linux":
        try:
            values = (await asyncio.to_thread(Path(f"/proc/{pid}/statm").read_text)).split()
        except (FileNotFoundError, ProcessLookupError):
            # Exited processes have no resident allocation; a renderer that exits between
            # open and read makes the read fail with ESRCH instead of ENOENT.
            return 0
        return int(values[1]) * os.sysconf("SC_PAGE_SIZE")
    try:
        process = await asyncio.create_subprocess_exec(
            "/bin/ps",
            "-o",
            "rss=",
            "-p",
            str(pid),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        raise ServiceError(
            "export_memory_monitor_failed", "Could not enforce render memory limit", 503, 4
        ) from exc
    output, _ = await process.communicate()
    if process.returncode == 1 and not output.strip():
        return 0  # ps reports no matching process after a normal exit.
    if process.returncode != 0:
        raise ServiceError(
            "export_memory_monitor_failed", "Could not enforce render memory limit", 503, 4
        )
    try:
        return int(output.strip()) * 1024
    except ValueError as exc:
        raise ServiceError(
            "export_memory_monitor_failed", "Could not enforce render memory limit", 503, 4
        ) from exc


async def render_process(root: Path, request: dict, execution: JobExecution) -> dict:
    private_write(root / "request.json", exports.canonical_bytes(request))
    env = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")
        if key in os.environ
    }
    env["PYTHONPATH"] = str(SERVER_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.jobs.export_child",
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
                raise ServiceError(
                    "export_render_timeout", "Export render deadline exceeded", 503, 3
                )
            if execution.stopped is not None:
                raise execution.stopped
            async with execution.db.transaction() as session:
                await execution.owned_job(session)
            if await resident_bytes(child.pid) > execution.settings.export_memory_bytes:
                raise ServiceError(
                    "export_memory_limit", "Export render memory limit exceeded", 400, 2
                )
            try:
                await asyncio.wait_for(child.wait(), timeout=0.25)
            except TimeoutError:
                continue
        if child.returncode != 0:
            raise ServiceError(
                "export_render_failed", "Export renderer exited without a valid candidate", 500, 4
            )
        try:
            result = json.loads((root / "result.json").read_text())
        except (OSError, ValueError) as exc:
            raise ServiceError(
                "export_render_failed", "Export renderer did not return a valid receipt", 500, 4
            ) from exc
        if "error" in result:
            error = result["error"]
            raise ServiceError(
                error["code"],
                "Export renderer refused the fixed inputs",
                400 if error["exit_code"] == 2 else 500,
                error["exit_code"],
            )
        return result
    finally:
        if child.returncode is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # Child exited between returncode inspection and kill.
            await child.wait()


async def render(execution: JobExecution, storage: Storage) -> None:
    started = time.monotonic()
    storage = exports.ExportStorage(storage)
    # The private directory and all plaintext files disappear on success, refusal,
    # timeout or cancellation. Only this attempt can write its encrypted key.
    with tempfile.TemporaryDirectory(prefix="bid-export-") as directory:
        root = Path(directory)
        async with execution.db.transaction() as session:
            job = await session.get(Job, execution.job_id)
            if job is None:
                raise not_found()
            actor = await exports.worker_access(session, job, execution.run_id)
            run = await session.get(ExportRun, UUID(job.result["submission"]["export_run_id"]))
            if run is None:
                raise not_found()
            await exports.fresh_manifest(session, actor, run, storage, verify_files=True)
            run_id, task_id, manifest, input_hash, manifest_hash = (
                run.id,
                run.task_id,
                run.manifest,
                run.input_hash,
                run.manifest_hash,
            )
            # Decrypted only into the private render directory, by the run's fixed rows.
            confidential_values = {
                entry["key"]: await confidential.value_text(
                    session, UUID(entry["value_id"]), Secrets.for_data(execution.settings)
                )
                for entry in manifest.get("confidential", [])
                if entry["value_id"] is not None
            }
            template, _ = await templates.read_revision(
                session, actor, run.template_revision_id, storage
            )
            private_write(root / "template.docx", template)
            del template
            pages = []
            for attachment in manifest["attachments"]:
                if attachment.get("kind") == "image":
                    asset, rendition = await screenshots.rendition_access(
                        session,
                        actor,
                        UUID(
                            attachment.get(
                                "annotation_release_rendition_id", attachment["rendition_id"]
                            )
                        ),
                    )
                    content = await screenshots.read_rendition(storage, asset, rendition)
                else:
                    content, _ = await evidence_sources.read_preview(
                        session, actor, UUID(attachment["evidence_source_id"]), storage
                    )
                private_write(root / f"page-{attachment['ordinal']}.png", content)
                del content
                pages.append(attachment["ordinal"])
        settings = execution.settings
        request = {
            "manifest": manifest,
            "pages": pages,
            "confidential": confidential_values,
            "memory_bytes": settings.export_memory_bytes,
            "limits": {
                "max_requirements": settings.export_max_requirements,
                "max_attachments": settings.export_max_attachments,
                "max_output_bytes": settings.export_max_output_bytes,
                "max_expanded_bytes": settings.export_max_expanded_bytes,
            },
        }
        result = await render_process(root, request, execution)
        path = root / "candidate.docx"
        if path.stat().st_size > settings.export_max_output_bytes:
            raise ServiceError(
                "export_output_limit", "Serialized export exceeds output limit", 400, 2
            )
        content = path.read_bytes()
        if (
            hashlib.sha256(content).hexdigest() != result["sha256"]
            or len(content) != result["size_bytes"]
            or result["manifest_hash"] != manifest_hash
            or result["renderer_profile"] != manifest["renderer_profile"]
        ):
            raise ServiceError(
                "export_candidate_integrity", "Renderer candidate receipt is inconsistent", 500, 4
            )
        # Recheck before writing and again under publication locks. A cancellation
        # during object I/O may leave an encrypted unreferenced object, never a
        # candidate row or a downloadable publication.
        async with execution.db.transaction() as session:
            job = await execution.owned_job(session)
            actor = await exports.worker_access(session, job, execution.run_id)
            run = await session.get(ExportRun, run_id)
            if run is None:
                raise not_found()
            await exports.fresh_manifest(session, actor, run, storage, verify_files=False)
            old = await session.scalar(
                select(ExportRenderCandidate).where(
                    ExportRenderCandidate.run_id == run_id,
                    ExportRenderCandidate.plaintext_sha256 != result["sha256"],
                )
            )
            if old is not None:
                raise ServiceError(
                    "export_nondeterministic", "Repeated render produced different bytes", 500, 4
                )
        key = f"export-candidates/{run_id}/{execution.run_id}/{result['sha256']}.docx"
        await storage.put(key, content)
        del content
        async with execution.db.transaction() as session:
            # Lock order is task/cards/dependencies then run/job, matching release.
            job = await session.get(Job, execution.job_id)
            if job is None:
                raise not_found()
            actor = await exports.worker_access(session, job, execution.run_id)
            await exports.lock_inputs(session, actor, task_id)
            job = await execution.owned_job(session)
            actor = await exports.worker_access(session, job, execution.run_id)
            # The job row locked by owned_job serializes this run; runs are append-only.
            run = await session.scalar(select(ExportRun).where(ExportRun.id == run_id))
            if run is None:
                raise not_found()
            await exports.fresh_manifest(session, actor, run, storage, verify_files=False)
            candidate = ExportRenderCandidate(
                id=uuid4(),
                run_id=run_id,
                render_job_id=job.id,
                attempt_id=execution.run_id,
                input_hash=input_hash,
                plaintext_sha256=result["sha256"],
                size_bytes=result["size_bytes"],
                object_key=key,
                renderer_profile=result["renderer_profile"],
                manifest_hash=manifest_hash,
            )
            session.add(candidate)
            await session.flush()
            job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
            job.result = {
                **job.result,
                "state": "awaiting_release",
                "export_run_id": str(run_id),
                "cost": Cost().model_dump(mode="json"),
                "exit_code": 0,
                "render_duration_ms": int((time.monotonic() - started) * 1000),
                "storage_bytes": result["size_bytes"],
            }
            audit(
                session,
                actor,
                "export.render_completed",
                run_id,
                {
                    "actor_kind": "worker",
                    "run_id": str(run_id),
                    "render_job_id": str(job.id),
                    "attempt_id": str(execution.run_id),
                    "input_hash": input_hash,
                    "file_sha256": result["sha256"],
                    "mode": run.mode,
                },
            )


async def failure_audit(session, job: Job, code: str) -> None:
    submission = job.result["submission"]
    actor = exports.Identity(scopes=set(), actor_kind="worker", job_id=job.id, run_id=job.run_id)
    await set_actor_context(session, actor)
    await session.execute(
        text(
            "SELECT set_config('app.export_run_id', :run, true), set_config('app.export_attempt_id', :attempt, true)"
        ),
        {"run": submission["export_run_id"], "attempt": str(job.run_id)},
    )
    audit(
        session,
        actor,
        "export.render_failed",
        UUID(submission["export_run_id"]),
        {
            "actor_kind": "worker",
            "run_id": submission["export_run_id"],
            "render_job_id": str(job.id),
            "attempt_id": str(job.run_id),
            "reason_code": code,
        },
    )
