"""Authorized sandbox submissions and immutable, encrypted source archives.

No browser, PDF or PNG decoder runs in this process. The BrowserProvider talks to
an independently supervised executor; only complete, cleaned results may publish.
"""

import hashlib
import json
import os
import struct
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.entities import (
    ApiToken,
    Document,
    Job,
    Membership,
    Org,
    ProductRevision,
    Task,
    TaskFeature,
    TaskResource,
    User,
)
from app.models.sandbox import (
    SandboxArtifact,
    SandboxAttempt,
    SandboxFetchReceipt,
    SandboxInput,
    SandboxRun,
)
from app.providers.browser import BrowserProvider, create_browser_provider
from app.providers.sandbox_fetch import (
    FetchBroker,
    FetchDenied,
    PolicySource,
    SQLiteFetchQuota,
    system_resolver,
)
from app.providers.sandbox_runtime import (
    ArtifactPayload,
    ExecutionResult,
    RunDescriptor,
    SandboxFailure,
)
from app.providers.storage import Storage
from app.schemas.contracts import Cost
from app.schemas.sandbox_contracts import (
    MAX_ARTIFACT_BYTES,
    PNG_KINDS,
    PrototypeSpec,
    SandboxArtifactView,
    SandboxIssue,
    SandboxMetrics,
    SandboxPreview,
    SandboxRunView,
    SandboxSubmit,
    VendorSpec,
)
from app.services.auth import ROLE_SCOPES, SCOPES, Identity, membership
from app.services.versioned import audit

OFFLINE_POLICY = "offline-v1"
OFFLINE_HASH = hashlib.sha256(b"offline-v1:no-network").hexdigest()
READ_SCOPES = {"sandbox:read", "task:read", "resource:read", "job:read"}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def browser_for(processor) -> BrowserProvider:
    if processor.sandbox_browser is None:
        processor.sandbox_browser = create_browser_provider()
    return processor.sandbox_browser


def policy_source() -> PolicySource:
    configured = os.environ.get("BID_SANDBOX_POLICY_FILE")
    if not configured:
        raise ServiceError("sandbox_policy_unavailable", "Sandbox policy is unavailable", 503, 3)
    return PolicySource(Path(configured))


def fetch_error(error: FetchDenied) -> ServiceError:
    transient = error.code in {
        "fetch_timeout",
        "fetch_transport_failed",
        "quota_unavailable",
        "origin_concurrency_limit",
        "org_rate_limit",
    }
    return ServiceError(
        "sandbox_" + error.code,
        "Sandbox capture was refused",
        503 if transient else 403,
        3 if transient else 4,
    )


def require_access(actor: Identity, action: str = "sandbox:read") -> None:
    actor.require(action)
    for scope in ("task:read", "resource:read", "job:read"):
        actor.require(scope)


def event(session: AsyncSession, actor: Identity, action: str, run: SandboxRun, **details) -> None:
    audit(
        session,
        actor,
        "sandbox." + action,
        run.id,
        {
            "actor_kind": actor.actor_kind,
            "task_id": str(run.task_id),
            "job_id": str(run.job_id),
            "run_id": str(run.id),
            "input_id": str(run.input_id),
            "request_hash": run.request_hash,
            "profile_digest": run.runtime_profile_digest,
            "policy_sha256": run.policy_sha256,
            **details,
        },
    )


async def org_lock(session: AsyncSession, org_id: UUID) -> None:
    # Persistent admission is serialized across API and worker processes, not a local semaphore.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": f"sandbox:{org_id}"}
    )


async def resolve_input(
    session: AsyncSession, actor: Identity, task_id: UUID, spec, *, active=True
):
    if await session.get(Task, task_id) is None:
        raise not_found()
    extraction = await session.scalar(
        select(Job).where(Job.id == spec.extraction_job_id, Job.task_id == task_id)
    )
    if extraction is None:
        raise not_found()
    document = await session.scalar(
        select(Document).where(Document.id == extraction.document_id, Document.task_id == task_id)
    )
    if document is None:
        raise not_found()
    if extraction.kind != "extract" or extraction.status != "succeeded":
        raise ServiceError(
            "sandbox_extraction_required", "A successful extraction is required", 409, 2
        )
    url = None
    if isinstance(spec, PrototypeSpec):
        selection = await session.scalar(
            select(TaskFeature).where(
                TaskFeature.id == spec.task_feature_id, TaskFeature.task_id == task_id
            )
        )
        if selection is None:
            raise not_found()
        if selection.feature_revision_id != spec.expected_feature_revision_id:
            raise ServiceError(
                "sandbox_revision_conflict", "Selected revision does not match", 409, 2
            )
    else:
        selection = await session.scalar(
            select(TaskResource).where(
                TaskResource.id == spec.task_resource_id, TaskResource.task_id == task_id
            )
        )
        if selection is None:
            raise not_found()
        if selection.product_revision_id != spec.expected_product_revision_id:
            raise ServiceError(
                "sandbox_revision_conflict", "Selected revision does not match", 409, 2
            )
        revision = await session.get(ProductRevision, selection.product_revision_id)
        if revision is None:
            raise not_found()
        url = revision.data.get(spec.source_field)
        if not isinstance(url, str) or not url:
            raise ServiceError(
                "sandbox_source_missing", "Selected revision has no source URL", 409, 2
            )
        if sha(url.encode("utf-8")) != spec.expected_source_url_sha256:
            raise ServiceError("sandbox_source_conflict", "Selected source does not match", 409, 2)
    if active and not selection.active:
        raise ServiceError("sandbox_selection_inactive", "Select an active source revision", 409, 4)
    return document, selection, url


def spec_of(row: SandboxInput):
    model = PrototypeSpec if row.purpose == "prototype_offline" else VendorSpec
    return model.model_validate(row.spec)


async def actor_for(session: AsyncSession, run: SandboxRun, *, lock: bool = False) -> Identity:
    user = await session.get(
        User, run.requested_by, with_for_update={"read": True} if lock else None
    )
    if user is None or not user.active:
        raise ServiceError("sandbox_actor_inactive", "Sandbox actor is no longer active", 403, 4)
    member = await membership(session, run.requested_by, run.org_id)
    if lock:
        member = await session.scalar(
            select(Membership)
            .where(
                Membership.org_id == run.org_id,
                Membership.user_id == run.requested_by,
                Membership.active.is_(True),
            )
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        active_org = await session.scalar(
            select(Org).where(Org.id == run.org_id, Org.active.is_(True)).with_for_update(read=True)
        )
        if member is None or active_org is None:
            raise ServiceError(
                "sandbox_actor_inactive", "Sandbox actor is no longer active", 403, 4
            )
    scopes = set(run.scope_snapshot) & ROLE_SCOPES[member.role]
    if run.token_id is not None:
        token = await session.get(
            ApiToken, run.token_id, with_for_update={"read": True} if lock else None
        )
        if (
            token is None
            or token.user_id != run.requested_by
            or token.revoked
            or token.expires_at <= datetime.now(UTC)
        ):
            raise ServiceError(
                "sandbox_actor_inactive", "Sandbox actor is no longer active", 403, 4
            )
        scopes &= set(token.scopes) & SCOPES
    return Identity(run.requested_by, run.org_id, scopes, member.role, run.token_id, run.actor_kind)


async def check_live(
    session: AsyncSession,
    actor: Identity,
    run: SandboxRun,
    row: SandboxInput,
    *,
    action="sandbox:read",
    active=True,
    policy=True,
):
    require_access(actor, action)
    document, selection, url = await resolve_input(
        session, actor, run.task_id, spec_of(row), active=active
    )
    if document.id != row.document_id:
        raise ServiceError("sandbox_input_changed", "Sandbox input binding changed", 409, 4)
    if policy and row.purpose == "vendor_capture":
        try:
            policy_source().check(run.policy_revision, run.policy_sha256)
        except FetchDenied as exc:
            raise fetch_error(exc) from None
    return selection, url


async def require_run(
    session: AsyncSession, actor: Identity, run_id: UUID, *, action: str = "sandbox:read"
):
    require_access(actor, action)
    run = await session.get(SandboxRun, run_id)
    if run is None:
        raise not_found()
    row = await session.get(SandboxInput, run.input_id)
    job = await session.get(Job, run.job_id)
    if row is None or job is None:
        raise not_found()
    return run, row, job


async def last_attempt(session: AsyncSession, run_id: UUID):
    return await session.scalar(
        select(SandboxAttempt)
        .where(SandboxAttempt.sandbox_run_id == run_id)
        .order_by(SandboxAttempt.started_at.desc(), SandboxAttempt.id.desc())
        .limit(1)
    )


def artifact_view(row: SandboxArtifact, attempt: SandboxAttempt) -> SandboxArtifactView:
    return SandboxArtifactView.model_validate(
        {
            "id": row.id,
            "run_id": row.sandbox_run_id,
            "attempt_id": attempt.attempt_id,
            "kind": row.kind,
            "sha256": row.plaintext_sha256,
            "size_bytes": row.size_bytes,
            "media_type": row.media_type,
            "width": row.width,
            "height": row.height,
            "page": row.page,
            "parent_artifact_id": row.parent_artifact_id,
            "provenance_manifest_hash": row.provenance_manifest_hash,
        }
    )


async def show_run(
    session: AsyncSession, actor: Identity, run_id: UUID, *, action: str = "sandbox:read"
) -> dict:
    run, row, job = await require_run(session, actor, run_id, action=action)
    selection, _ = await check_live(
        session, actor, run, row, action=action, active=False, policy=False
    )
    attempt = await last_attempt(session, run.id)
    state = job.status
    cleanup = (
        ("complete" if await cleanup_confirmed(session, attempt) else attempt.cleanup_state)
        if attempt
        else "not_started"
    )
    issues = list(attempt.issues) if attempt else []
    if cleanup == "failed":
        state = "cleanup_pending"
    elif job.status == "failed" and not issues:
        issues = [
            {
                "code": job.error["code"] if job.error else "sandbox_failed",
                "severity": "block",
                "object_ids": [],
            }
        ]
    artifacts = []
    if (
        state == "succeeded"
        and attempt
        and attempt.attempt_id == job.run_id
        and cleanup == "complete"
    ):
        rows = (
            await session.scalars(
                select(SandboxArtifact)
                .where(SandboxArtifact.attempt_record_id == attempt.id)
                .order_by(SandboxArtifact.ordinal)
            )
        ).all()
        artifacts = [artifact_view(item, attempt) for item in rows]
    return SandboxRunView.model_validate(
        {
            "id": run.id,
            "org_id": run.org_id,
            "task_id": run.task_id,
            "job_id": run.job_id,
            "purpose": row.purpose,
            "request_hash": run.request_hash,
            "profile": run.profile,
            "policy_revision": run.policy_revision,
            "selection_active": selection.active,
            "state": state,
            "attempt_id": attempt.attempt_id if attempt else None,
            "cleanup_state": cleanup,
            "artifacts": artifacts,
            "metrics": attempt.metrics if attempt and attempt.metrics else None,
            "issues": issues,
            "usage_ids": [],
            "charge": None,
            "charge_currency": None,
        }
    ).model_dump(mode="json")


async def submit(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    body: SandboxSubmit,
    html: bytes | None,
    storage: Storage,
    crypto: Secrets,
    browser: BrowserProvider,
):
    prototype = isinstance(body.spec, PrototypeSpec)
    require_access(actor, "sandbox:render" if prototype else "sandbox:capture")
    # Lock order matches worker publication and resource selection before any row is written.
    if not body.dry_run:
        await org_lock(session, actor.org_id)
        if await session.scalar(select(Task).where(Task.id == task_id).with_for_update()) is None:
            raise not_found()
    document, _, url = await resolve_input(session, actor, task_id, body.spec)
    if isinstance(body.spec, PrototypeSpec):
        if html is None:
            raise ServiceError("sandbox_html_required", "HTML upload is required", 422, 2)
        if len(html) > 4 * 1024 * 1024:
            raise ServiceError("sandbox_input_limit", "HTML input exceeds limit", 413, 2)
        if sha(html) != body.spec.html_sha256 or len(html) != body.spec.html_size_bytes:
            raise ServiceError(
                "sandbox_input_conflict", "HTML length or hash does not match", 409, 2
            )
        try:
            html.decode("utf-8")
        except UnicodeError:
            raise ServiceError("sandbox_invalid_html", "HTML must be UTF-8", 422, 2) from None
        revision, policy_hash, profile = OFFLINE_POLICY, OFFLINE_HASH, "prototype-offline-v1"
    else:
        if html is not None:
            raise ServiceError("sandbox_unexpected_html", "Capture does not accept HTML", 422, 2)
        assert url is not None
        try:
            selected = policy_source().select(url)
        except FetchDenied as exc:
            raise fetch_error(exc) from None
        revision, policy_hash, profile = selected.revision, selected.sha256, "vendor-capture-v1"
    request_hash = sha(
        canonical(
            {
                "org_id": str(actor.org_id),
                "task_id": str(task_id),
                "user_id": str(actor.user_id),
                "actor_kind": actor.actor_kind,
                "scopes": sorted(actor.scopes),
                "token_id": str(actor.token_id) if actor.token_id else None,
                "spec": body.spec.model_dump(mode="json"),
                "profile": profile,
                "runtime_profile_digest": browser.profile_digest,
                "policy_revision": revision,
                "policy_sha256": policy_hash,
            }
        )
    )
    issues = []
    if not browser.available:
        issues.append(SandboxIssue(code="sandbox_runtime_unavailable", severity="block"))
    if body.dry_run:
        data = SandboxPreview(
            request_hash=request_hash,
            purpose=body.spec.purpose,
            profile=profile,
            policy_revision=revision,
            ready=not issues,
            issues=issues,
            estimated_cost=Cost(usd=0),
            estimate_basis="no_vendor_call",
        ).model_dump(mode="json")
        from app.services import budget_preflight

        settings = session.info.get("memory_settings")
        return await budget_preflight.attach(
            session,
            data,
            command="sandbox render" if prototype else "sandbox capture",
            task_id=task_id,
            input_hash=request_hash,
            currency=settings.billing_currency if settings else "USD",
            planned_calls=0,
        ), None
    if body.expected_request_hash != request_hash:
        raise ServiceError("sandbox_request_conflict", "Preflight request changed", 409, 2)
    criteria = (
        SandboxRun.request_hash == request_hash
        if isinstance(body.spec, PrototypeSpec)
        else SandboxRun.capture_key == body.spec.capture_key
    )
    existing = await session.scalar(select(SandboxRun).where(criteria))
    if existing is not None:
        if existing.request_hash != request_hash:
            raise ServiceError(
                "sandbox_capture_key_conflict", "Capture key has different parameters", 409, 2
            )
        job = await session.scalar(select(Job).where(Job.id == existing.job_id).with_for_update())
        assert job is not None
        if body.retry:
            attempt = await last_attempt(session, existing.id)
            if job.status not in {"failed", "cancelled"} or (
                attempt and not await cleanup_confirmed(session, attempt)
            ):
                raise ServiceError(
                    "sandbox_retry_not_ready", "Retry requires a terminal cleaned run", 409, 2
                )
            if job.attempts >= 3:
                raise ServiceError(
                    "sandbox_attempt_limit", "Sandbox attempt ceiling reached", 409, 4
                )
            if job.error and job.error.get("exit_code") == 4 and job.status != "cancelled":
                raise ServiceError(
                    "sandbox_not_retryable", "Sandbox failure cannot be retried", 409, 4
                )
            fixed_input = await session.get(SandboxInput, existing.input_id)
            assert fixed_input is not None
            await budgeted_descriptor(session, existing, fixed_input, uuid4())
            if issues:
                raise ServiceError(
                    "sandbox_runtime_unavailable", "Sandbox runtime is unavailable", 503, 3
                )
            job.status, job.queue_id, job.error, job.finished_at = "queued", None, None, None
            job.result = {
                "sandbox_run_id": str(existing.id),
                "queued_at": datetime.now(UTC).isoformat(),
            }
        return await show_run(
            session, actor, existing.id, action="sandbox:render" if prototype else "sandbox:capture"
        ), job
    if body.retry:
        raise ServiceError("sandbox_retry_missing", "No matching run exists to retry", 409, 2)
    if issues:
        raise ServiceError("sandbox_runtime_unavailable", "Sandbox runtime is unavailable", 503, 3)
    queued = await session.scalar(
        select(func.count()).select_from(Job).where(Job.kind == "sandbox", Job.status == "queued")
    )
    if queued is not None and queued >= 20:
        raise ServiceError("sandbox_queue_limit", "Organization sandbox queue is full", 503, 3)
    input_id, job_id, run_id = uuid4(), uuid4(), uuid4()
    values = {
        "id": input_id,
        "org_id": actor.org_id,
        "task_id": task_id,
        "document_id": document.id,
        "extraction_job_id": body.spec.extraction_job_id,
        "purpose": body.spec.purpose,
        "spec": body.spec.model_dump(mode="json"),
    }
    if isinstance(body.spec, PrototypeSpec):
        assert html is not None
        key = f"org/{actor.org_id}/sandbox-inputs/{input_id}/{body.spec.html_sha256}"
        await storage.put(actor.org_id, key, html)
        values.update(
            task_feature_id=body.spec.task_feature_id,
            feature_revision_id=body.spec.expected_feature_revision_id,
            html_sha256=body.spec.html_sha256,
            size_bytes=len(html),
            object_key=key,
        )
    else:
        assert url is not None
        values.update(
            task_resource_id=body.spec.task_resource_id,
            product_revision_id=body.spec.expected_product_revision_id,
            source_field=body.spec.source_field,
            source_url_sha256=body.spec.expected_source_url_sha256,
            encrypted_source_url=crypto.encrypt(url),
        )
    row = SandboxInput(**values)
    job = Job(
        id=job_id,
        org_id=actor.org_id,
        task_id=task_id,
        document_id=document.id,
        kind="sandbox",
        cache_key=sha(b"sandbox-v1:" + request_hash.encode()),
        status="queued",
        result={"sandbox_run_id": str(run_id), "queued_at": datetime.now(UTC).isoformat()},
    )
    session.add_all([row, job])
    await session.flush()
    run = SandboxRun(
        id=run_id,
        org_id=actor.org_id,
        input_id=input_id,
        task_id=task_id,
        document_id=document.id,
        job_id=job_id,
        request_hash=request_hash,
        profile=profile,
        policy_revision=revision,
        policy_sha256=policy_hash,
        runtime_profile_digest=browser.profile_digest,
        requested_by=actor.user_id,
        actor_kind=actor.actor_kind,
        token_id=actor.token_id,
        scope_snapshot=sorted(actor.scopes & (READ_SCOPES | {"sandbox:render", "sandbox:capture"})),
        capture_key=None if isinstance(body.spec, PrototypeSpec) else body.spec.capture_key,
    )
    session.add(run)
    await session.flush()
    event(session, actor, "submitted", run, input_hash=row.html_sha256 or row.source_url_sha256)
    return await show_run(
        session, actor, run.id, action="sandbox:render" if prototype else "sandbox:capture"
    ), job


async def list_runs(session: AsyncSession, actor: Identity, task_id: UUID, offset: int, limit: int):
    require_access(actor)
    if await session.get(Task, task_id) is None:
        raise not_found()
    rows = (
        await session.scalars(
            select(SandboxRun)
            .where(SandboxRun.task_id == task_id)
            .order_by(SandboxRun.created_at.desc(), SandboxRun.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return [await show_run(session, actor, row.id) for row in rows]


async def require_artifact(session: AsyncSession, actor: Identity, artifact_id: UUID):
    require_access(actor)
    artifact = await session.get(SandboxArtifact, artifact_id)
    if artifact is None:
        raise not_found()
    run, row, job = await require_run(session, actor, artifact.sandbox_run_id)
    await check_live(session, actor, run, row)
    attempt = await session.get(SandboxAttempt, artifact.attempt_record_id)
    if (
        attempt is None
        or attempt.cleanup_state != "complete"
        or attempt.termination_code != "succeeded"
        or job.status != "succeeded"
        or job.run_id != attempt.attempt_id
    ):
        raise not_found()
    return artifact, attempt, run


async def guard_job(
    session: AsyncSession, actor: Identity, job: Job, *, cancel: bool = False
) -> None:
    if job.kind == "sandbox":
        require_access(actor)
        run = await session.scalar(select(SandboxRun).where(SandboxRun.job_id == job.id))
        if run is None:
            raise not_found()
        await require_run(session, actor, run.id)
        if cancel and job.status in {"queued", "running"}:
            event(
                session,
                actor,
                "cancelled",
                run,
                reason_code="sandbox_cancelled",
                executor_kind="api",
            )


def descriptor_for(run: SandboxRun, row: SandboxInput, attempt_id: UUID) -> RunDescriptor:
    spec = spec_of(row)
    return RunDescriptor(
        org_id=run.org_id,
        job_id=run.job_id,
        attempt_id=attempt_id,
        input_sha256=spec.html_sha256
        if isinstance(spec, PrototypeSpec)
        else spec.expected_source_url_sha256,
        purpose=spec.purpose,
        viewport_width=spec.viewport.width,
        viewport_height=spec.viewport.height,
        format="web" if isinstance(spec, PrototypeSpec) else spec.format,
        pdf_pages=() if isinstance(spec, PrototypeSpec) else tuple(spec.pdf_pages),
        archive="manifest" if isinstance(spec, PrototypeSpec) else spec.archive,
    )


def make_fetcher(processor, run: SandboxRun, row: SandboxInput) -> FetchBroker:
    path = os.environ.get("BID_SANDBOX_FETCH_QUOTA")
    if not path:
        raise ServiceError(
            "sandbox_quota_unavailable", "Persistent proxy quota is not configured", 503, 3
        )
    broker = FetchBroker(
        policy_source(),
        run.policy_revision,
        run.org_id,
        quota=SQLiteFetchQuota(Path(path)),
        transport=processor.sandbox_fetch_transport,
        resolver=processor.sandbox_resolver or system_resolver,
        bundle=row.spec.get("archive") == "bundle",
    )
    if broker.policy.sha256 != run.policy_sha256:
        broker.close()
        raise ServiceError("sandbox_policy_changed", "Sandbox policy changed", 403, 4)
    return broker


def checked_payloads(
    result: ExecutionResult,
    descriptor: RunDescriptor,
    broker: FetchBroker | None,
    source_url: str | None = None,
) -> tuple[list[ArtifactPayload], dict]:
    if result.cleanup_state != "complete":
        raise ServiceError("sandbox_cleanup_pending", "Sandbox cleanup is not confirmed", 503, 3)
    if result.descriptor_hash != descriptor.digest:
        raise ServiceError("sandbox_receipt_mismatch", "Sandbox receipt binding failed", 409, 4)
    items = list(result.artifacts)
    if broker is not None:
        if source_url is None:
            raise ServiceError("sandbox_source_missing", "Authorized source is missing", 409, 4)
        entry = broker.entry_receipt(source_url)
        if descriptor.format == "web" and entry.headers.get("content-type", "").split(";", 1)[
            0
        ] not in {"text/html", "application/xhtml+xml"}:
            raise ServiceError("sandbox_source_mismatch", "Selected web source is not HTML", 409, 4)
        if descriptor.format == "pdf":
            originals = [item for item in items if item.kind == "source_pdf"]
            if (
                len(originals) != 1
                or originals[0].sha256 != entry.sha256
                or len(originals[0].data) != entry.bytes
                or entry.headers.get("content-type", "").split(";", 1)[0] != "application/pdf"
            ):
                raise ServiceError(
                    "sandbox_source_mismatch", "Original PDF differs from proxy response", 409, 4
                )
    # The driver cannot produce proxy observations. Replace only byte-identical broker artifacts.
    if broker is not None:
        trusted = {"request_manifest": broker.manifest_bytes()}
        if descriptor.archive == "bundle":
            trusted["capture_archive"] = broker.archive_bytes()
        for kind, data in trusted.items():
            matches = [item for item in items if item.kind == kind]
            if len(matches) > 1 or (matches and matches[0].data != data):
                raise ServiceError(
                    "sandbox_receipt_mismatch", "Proxy artifact binding failed", 409, 4
                )
            if not matches:
                items.append(ArtifactPayload(kind, data))
    expected = (
        ["prototype_png", "rendered_html"]
        if descriptor.purpose == "prototype_offline"
        else (
            ["source_pdf", *["pdf_page_png"] * len(descriptor.pdf_pages), "request_manifest"]
            if descriptor.format == "pdf"
            else ["capture_png", "rendered_html", "request_manifest"]
        )
    )
    if descriptor.archive == "bundle":
        expected.append("capture_archive")
    if sorted(item.kind for item in items) != sorted(expected):
        raise ServiceError("sandbox_artifact_set", "Sandbox artifact set is incomplete", 409, 4)
    pages = [item.page for item in items if item.kind == "pdf_page_png"]
    if any(page is None for page in pages) or [page for page in pages if page is not None] != list(
        descriptor.pdf_pages
    ):
        raise ServiceError("sandbox_artifact_set", "Sandbox PDF page set does not match", 409, 4)
    for item in items:
        if item.kind in {"prototype_png", "capture_png"} and (item.width, item.height) != (
            descriptor.viewport_width,
            descriptor.viewport_height,
        ):
            raise ServiceError(
                "sandbox_invalid_output", "Sandbox image does not match the fixed viewport", 409, 4
            )
        if not 0 < len(item.data) <= MAX_ARTIFACT_BYTES[item.kind]:
            raise ServiceError("sandbox_output_limit", "Sandbox output exceeds limit", 413, 4)
        if item.kind in PNG_KINDS:
            if (
                len(item.data) < 33
                or item.data[:8] != b"\x89PNG\r\n\x1a\n"
                or item.data[12:16] != b"IHDR"
                or struct.unpack(">II", item.data[16:24]) != (item.width, item.height)
            ):
                raise ServiceError(
                    "sandbox_invalid_output", "Sandbox PNG header does not match", 409, 4
                )
        elif item.kind == "source_pdf" and not item.data.startswith(b"%PDF-"):
            raise ServiceError(
                "sandbox_invalid_output", "Sandbox PDF header does not match", 409, 4
            )
        elif item.kind == "rendered_html":
            try:
                item.data.decode("utf-8")
            except UnicodeError:
                raise ServiceError(
                    "sandbox_invalid_output", "Sandbox DOM is not UTF-8", 409, 4
                ) from None
    try:
        metrics = SandboxMetrics.model_validate(result.metrics).model_dump()
    except ValidationError:
        raise ServiceError(
            "sandbox_invalid_metrics", "Sandbox metrics are invalid", 409, 4
        ) from None
    if (
        metrics["wall_ms"] > descriptor.wall_seconds * 1000
        or metrics["cpu_ms"] > descriptor.wall_seconds * 1000
        or metrics["peak_memory_bytes"]
        > (1024 if descriptor.purpose == "prototype_offline" else 2048) * 1024**2
    ):
        raise ServiceError("sandbox_resource_limit", "Sandbox resource ceiling reached", 409, 4)
    if broker is not None:
        metrics["network_bytes"] = sum(receipt.bytes for receipt in broker.receipts)
        metrics["request_count"] = len(broker.receipts)
    metrics["output_bytes"] = max(metrics["output_bytes"], sum(len(item.data) for item in items))
    return items, metrics


async def store_artifacts(
    storage: Storage,
    run: SandboxRun,
    row: SandboxInput,
    attempt: SandboxAttempt,
    result: ExecutionResult,
    broker: FetchBroker | None,
    source_url: str | None = None,
):
    descriptor = RunDescriptor.from_wire(attempt.descriptor)
    payloads, metrics = checked_payloads(result, descriptor, broker, source_url)
    origin = (
        "prototype"
        if row.purpose == "prototype_offline"
        else ("public_pdf_capture" if row.spec["format"] == "pdf" else "public_web_capture")
    )
    records = []
    for ordinal, payload in enumerate(payloads):
        identifier = uuid4()
        record = SandboxArtifact(
            id=identifier,
            org_id=run.org_id,
            sandbox_run_id=run.id,
            attempt_record_id=attempt.id,
            input_id=row.id,
            task_id=run.task_id,
            kind=payload.kind,
            ordinal=ordinal,
            parent_artifact_id=None,
            plaintext_sha256=sha(payload.data),
            size_bytes=len(payload.data),
            media_type="image/png" if payload.kind in PNG_KINDS else "application/octet-stream",
            object_key=f"org/{run.org_id}/sandbox-artifacts/{run.id}/{attempt.attempt_id}/{identifier}/{sha(payload.data)}",
            origin=origin,
            width=payload.width,
            height=payload.height,
            page=payload.page,
        )
        records.append(record)
    pdf_parent = next((record.id for record in records if record.kind == "source_pdf"), None)
    for record in records:
        if record.kind == "pdf_page_png":
            record.parent_artifact_id = pdf_parent
    descriptors = [
        {
            "id": str(record.id),
            "kind": record.kind,
            "sha256": record.plaintext_sha256,
            "size_bytes": record.size_bytes,
            "width": record.width,
            "height": record.height,
            "page": record.page,
            "parent_artifact_id": str(record.parent_artifact_id)
            if record.parent_artifact_id
            else None,
        }
        for record in records
    ]
    provenance = {
        "origin": origin,
        "input_mode": "supplied_html" if origin == "prototype" else "fixed_product_revision",
        "generating_job_id": None,
        "generating_provider": None,
        "generating_model": None,
        "html_sha256": row.html_sha256,
        "render_manifest_sha256": sha(canonical(descriptors)),
        "org_id": str(run.org_id),
        "task_id": str(run.task_id),
        "document_id": str(run.document_id),
        "extraction_job_id": str(row.extraction_job_id),
        "input_id": str(row.id),
        "spec": row.spec,
        "run_id": str(run.id),
        "job_id": str(run.job_id),
        "attempt_id": str(attempt.attempt_id),
        "request_hash": run.request_hash,
        "profile": run.profile,
        "profile_digest": run.runtime_profile_digest,
        "policy_revision": run.policy_revision,
        "policy_sha256": run.policy_sha256,
        "runtime_versions": result.runtime_versions,
        "cleanup_state": result.cleanup_state,
        "rendered_at": datetime.now(UTC).isoformat(),
        "artifacts": descriptors,
        "metrics": metrics,
        "observations": "proxy supplies request manifest; supervisor supplies resources and cleanup",
    }
    base_output_bytes = metrics["output_bytes"]
    for _ in range(4):
        manifest = canonical(provenance)
        final_output_bytes = base_output_bytes + len(manifest)
        if metrics["output_bytes"] == final_output_bytes:
            break
        metrics["output_bytes"] = final_output_bytes
    else:
        raise ServiceError("sandbox_invalid_metrics", "Provenance size did not stabilize", 409, 4)
    manifest_hash = sha(manifest)
    if len(manifest) > 4 * 1024**2:
        raise ServiceError("sandbox_output_limit", "Provenance exceeds output limit", 413, 4)
    identifier = uuid4()
    records.append(
        SandboxArtifact(
            id=identifier,
            org_id=run.org_id,
            sandbox_run_id=run.id,
            attempt_record_id=attempt.id,
            input_id=row.id,
            task_id=run.task_id,
            kind="provenance_manifest",
            ordinal=len(records),
            plaintext_sha256=manifest_hash,
            size_bytes=len(manifest),
            media_type="application/octet-stream",
            origin=origin,
            object_key=f"org/{run.org_id}/sandbox-artifacts/{run.id}/{attempt.attempt_id}/{identifier}/{manifest_hash}",
        )
    )
    if metrics["output_bytes"] > descriptor.output_limit or len(records) > 32:
        raise ServiceError("sandbox_output_limit", "Sandbox total output exceeds limit", 413, 4)
    for record, content in zip(records, [*(item.data for item in payloads), manifest], strict=True):
        record.provenance_manifest_hash = manifest_hash
        # Pydantic checks sizes and dimensions without running a decoder in the trusted worker.
        try:
            artifact_view(record, attempt)
        except ValidationError:
            raise ServiceError(
                "sandbox_invalid_output", "Sandbox artifact descriptor is invalid", 409, 4
            ) from None
        await storage.put(run.org_id, record.object_key, content)
    return records, metrics


async def save_receipts(
    session: AsyncSession,
    run: SandboxRun,
    row: SandboxInput,
    attempt: SandboxAttempt,
    broker: FetchBroker | None,
    crypto: Secrets,
    artifacts: list[SandboxArtifact],
):
    if broker is None:
        return
    bundle = next((item.id for item in artifacts if item.kind == "capture_archive"), None)
    allowed = {receipt.ordinal: receipt for receipt in broker.receipts}
    denied = {receipt.ordinal: receipt for receipt in broker.denials}
    identifiers = {ordinal: uuid4() for ordinal in allowed.keys() | denied.keys()}
    for ordinal in sorted(identifiers):
        receipt, denial = allowed.get(ordinal), denied.get(ordinal)
        observed = receipt if receipt is not None else denial
        assert observed is not None
        parent = observed.parent_ordinal
        if parent is not None and parent not in identifiers:
            raise ServiceError("sandbox_receipt_mismatch", "Proxy redirect binding failed", 409, 4)
        if receipt is not None:
            end = datetime.fromtimestamp(receipt.captured_at, UTC)
            start = end - timedelta(milliseconds=receipt.duration_ms)
        else:
            assert denial is not None
            start = end = datetime.fromtimestamp(denial.occurred_at, UTC)
        session.add(
            SandboxFetchReceipt(
                id=identifiers[ordinal],
                org_id=run.org_id,
                sandbox_run_id=run.id,
                attempt_record_id=attempt.id,
                input_id=row.id,
                task_id=run.task_id,
                request_ordinal=ordinal,
                parent_redirect_id=identifiers[parent] if parent is not None else None,
                url_sha256=observed.url_sha256,
                encrypted_request_metadata=crypto.encrypt(
                    canonical(asdict(receipt)).decode("ascii")
                )
                if receipt
                else None,
                response_sha256=receipt.sha256 if receipt else None,
                response_bytes=receipt.bytes if receipt else 0,
                status_code=receipt.status if receipt else None,
                decision_code=denial.code if denial else "allowed",
                policy_revision=run.policy_revision,
                started_at=start,
                ended_at=end,
                bundle_artifact_id=bundle if receipt else None,
            )
        )
        await session.flush()


def descriptor_json(descriptor: RunDescriptor) -> dict:
    return json.loads(json.dumps(asdict(descriptor), default=str))


async def budgeted_descriptor(
    session: AsyncSession, run: SandboxRun, row: SandboxInput, attempt_id: UUID
) -> RunDescriptor:
    previous = (
        await session.scalars(select(SandboxAttempt).where(SandboxAttempt.sandbox_run_id == run.id))
    ).all()
    descriptor = descriptor_for(run, row, attempt_id)
    wall = descriptor.wall_seconds * 1000
    output = descriptor.output_limit
    if previous:
        wall -= sum(item.metrics["wall_ms"] for item in previous)
        cpu = descriptor.wall_seconds * 1000 - sum(
            max(item.metrics["cpu_ms"], item.metrics["wall_ms"] + 20) for item in previous
        )
        output -= sum(item.metrics["output_bytes"] for item in previous)
        if min(wall, cpu) < 2000 or output < 1:
            raise ServiceError(
                "sandbox_budget_exhausted", "Cumulative sandbox budget is exhausted", 409, 4
            )
        descriptor = replace(
            descriptor, budget_wall_ms=wall, budget_cpu_ms=cpu, budget_output_bytes=output
        )
    return descriptor


def conservative_metrics(descriptor: RunDescriptor) -> dict[str, int]:
    # Lost final accounting cannot earn another full execution budget on retry.
    return {
        "wall_ms": descriptor.wall_seconds * 1000,
        "cpu_ms": descriptor.wall_seconds * 1000,
        "peak_memory_bytes": (1024 if descriptor.purpose == "prototype_offline" else 2048)
        * 1024**2,
        "input_bytes": 0,
        "network_bytes": 0,
        "output_bytes": descriptor.output_limit,
        "request_count": 0,
    }


async def cleanup_confirmed(session: AsyncSession, attempt: SandboxAttempt) -> bool:
    if attempt.cleanup_state == "complete":
        return True
    from app.models.entities import AuditLog

    return bool(
        await session.scalar(
            select(AuditLog.id)
            .where(
                AuditLog.action == "sandbox.reaped",
                AuditLog.object_id == attempt.sandbox_run_id,
                AuditLog.details["attempt_id"].astext == str(attempt.attempt_id),
                AuditLog.details["descriptor_hash"].astext == attempt.instance_group_ref_hash,
            )
            .limit(1)
        )
    )


async def reconcile_run(db, run_id: UUID, org_id: UUID, browser: BrowserProvider) -> None:
    """Reconcile only an already registered attempt; never authorize or start new execution."""
    if not hasattr(browser, "recover"):
        return
    async with db.transaction(org_id) as session:
        run = await session.get(SandboxRun, run_id)
        if run is None:
            return
        attempt = await last_attempt(session, run.id)
        if attempt is None or await cleanup_confirmed(session, attempt):
            return
        job = await session.get(Job, run.job_id)
        assert job is not None
        if job.status == "running" and job.lease_until and job.lease_until > datetime.now(UTC):
            return
        descriptor = RunDescriptor.from_wire(attempt.descriptor)
    try:
        receipt = await browser.recover(descriptor)
    except (SandboxFailure, OSError, TimeoutError):
        return  # No confirmation: the existing failed/pending cleanup remains the admission fence.
    if receipt.descriptor_hash != descriptor.digest or receipt.cleanup_state not in {
        "complete",
        "not_started",
    }:
        return
    async with db.transaction(org_id) as session:
        await org_lock(session, org_id)
        run = await session.get(SandboxRun, run_id)
        assert run is not None
        job = await session.scalar(select(Job).where(Job.id == run.job_id).with_for_update())
        attempt = await session.get(SandboxAttempt, attempt.id)
        assert attempt is not None and job is not None
        if await cleanup_confirmed(session, attempt):
            return
        actor = Identity(run.requested_by, org_id, set(), "viewer", run.token_id, run.actor_kind)
        if attempt.ended_at is None:
            attempt.ended_at = datetime.now(UTC)
            attempt.cleanup_state, attempt.termination_code = "complete", "sandbox_orphan_reaped"
            attempt.metrics = conservative_metrics(descriptor)
            attempt.runtime_versions = {"accounting": "recovered_conservative_bound"}
            attempt.issues = [
                {"code": "sandbox_orphan_reaped", "severity": "block", "object_ids": []}
            ]
        if job.run_id == attempt.attempt_id and job.status == "running":
            job.status, job.finished_at = "failed", datetime.now(UTC)
            job.error = {
                "code": "sandbox_orphan_reaped",
                "message": "Interrupted sandbox attempt was reaped",
                "exit_code": 3,
                "retryable": True,
            }
        event(
            session,
            actor,
            "reaped",
            run,
            attempt_id=str(attempt.attempt_id),
            descriptor_hash=descriptor.digest,
            cleanup_state=receipt.cleanup_state,
        )
