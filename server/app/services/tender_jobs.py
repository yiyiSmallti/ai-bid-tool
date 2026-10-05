"""Submission of `tender parse` and `req extract` jobs for one document."""

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError
from app.models.entities import Chunk, Job
from app.providers.configured import model_identity
from app.providers.llm import billable, reasoning_choices, with_reasoning
from app.schemas.contracts import JobAction
from app.services import billing
from app.services.auth import Identity, set_actor_context
from app.services.documents import require_document
from app.services.extraction import EXTRACTION_VERSION, PROMPT_VERSION
from app.services.parsing import PARSER_VERSION
from app.services.task_authorization import task_authorized


@task_authorized("task:read", parent=("document_id", "documents"), write=True)
async def submit(
    session: AsyncSession,
    identity: Identity,
    document_id: UUID,
    kind: str,
    body: JobAction,
    *,
    settings: Settings,
    db: Database,
    queue,
    llm,
    resolve: Callable[..., Awaitable[Any]] | None,
    ocr,
    storage=None,
) -> tuple[dict, list[str]]:
    """Create or reuse the cached job for this document, kind and model: (data, warnings).

    The job row is committed before it is enqueued, so a queue failure leaves a saved
    job that repeating the request schedules.
    """
    identity.require("tender:parse" if kind == "parse" else "req:extract")
    document = await require_document(session, document_id)
    count = await session.scalar(select(Chunk.id).where(Chunk.document_id == document_id).limit(1))
    if kind == "parse" and body.reasoning is not None:
        raise ServiceError("invalid_input", "Reasoning levels apply only to extraction", 400, 2)
    model = await resolve(session) if resolve is not None and kind == "extract" else llm
    level, level_warnings = None, []
    if kind == "extract":
        model, level, level_warnings = with_reasoning(model, body.reasoning)
    version = (
        f"{PARSER_VERSION}:{ocr.name}:{ocr.version}:{settings.ocr_language}"
        if kind == "parse"
        else f"{PROMPT_VERSION}:{EXTRACTION_VERSION}:{model.name}:{model.model}:{model.version}"
    )
    if level is not None:
        # Each level is its own job; repeating the same level returns the same job.
        version += f":reasoning={level}"
    cache_key = hashlib.sha256(
        f"{document_id}:{document.sha256}:{kind}:{version}".encode()
    ).hexdigest()
    if body.dry_run:
        from app.core.pdf_process import PDFOperation
        from app.providers.llm import batches
        from app.services import budget_preflight
        from app.services.parsing import pdf_page_result

        cached = await session.scalar(select(Job).where(Job.cache_key == cache_key))
        quotes = []
        quote_sources = None
        planned_calls = None
        data: dict[str, Any] = {
            "dry_run": True,
            "document_id": str(document_id),
            "parsed": count is not None,
            "estimated_cost_usd": None,
        }
        warnings = list(level_warnings)
        if kind == "extract":
            data |= {"reasoning": level, "reasoning_levels": reasoning_choices(model)}
            if document.status != "parsed":
                # Recovery previews are available while parsing is queued. No
                # extraction request or honest price exists until chunks do.
                data["input_blocker"] = "not_parsed"
                warnings.append("Parse the document before requesting an extraction estimate.")
            else:
                chunks = (
                    await session.scalars(
                        select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.seq)
                    )
                ).all()
                if not chunks or not all(chunk.citation_verified for chunk in chunks):
                    raise ServiceError(
                        "unverified_document",
                        "Parse a PDF with verified pages before extraction",
                        400,
                        2,
                    )
                if hasattr(model, "extraction_request") and hasattr(model, "quote"):
                    outgoing = [
                        {
                            key: getattr(chunk, key)
                            for key in (
                                "id",
                                "document_id",
                                "page",
                                "text",
                                "citation_verified",
                                "blocks",
                            )
                        }
                        for chunk in chunks
                    ]
                    requests = batches(outgoing, model.settings.llm_batch_chars)
                    quote_sources = [
                        lambda batch=batch: model.quote(model.extraction_request(batch))
                        for batch in requests
                    ]
                    planned_calls = len(requests)
        else:
            suffix = Path(document.name).suffix.lower()
            if suffix == ".docx":
                planned_calls = 0
            elif storage is not None:
                content = await storage.read(identity.org_id, document.storage_key)
                if hashlib.sha256(content).hexdigest() != document.sha256:
                    raise ServiceError(
                        "content_mismatch", "Stored document hash does not match", 409, 4
                    )
                with PDFOperation(
                    content, "parse", {"max_pages": settings.max_pages}, settings
                ) as pdf:
                    await pdf.wait_async()
                    for number, record in enumerate(pdf.records(), 1):
                        _, image, _ = pdf_page_result(record, number)
                        if image is not None:
                            quotes.append(ocr.quote(image, number))
                planned_calls = len(quotes)
                data["candidate_ocr_pages"] = planned_calls
        data = await budget_preflight.attach(
            session,
            data,
            command="tender parse" if kind == "parse" else "req extract",
            task_id=document.task_id,
            input_hash=cache_key,
            currency=settings.billing_currency,
            settings=settings,
            quotes=quotes,
            quote_sources=quote_sources,
            planned_calls=planned_calls,
            cached_job=cached,
        )
        data["estimated_cost_usd"] = data["budget_preflight"]["estimate"]["usd"]
        return data, warnings
    if kind == "extract" and document.status != "parsed":
        raise ServiceError("not_parsed", "Parse the document before extraction", 400, 2)
    # A missing credential fails as provider_unavailable, not as a balance problem.
    if kind == "extract" and billable(model):
        await billing.require_funds(session, settings.billing_currency)
    await set_actor_context(session, identity)
    identifier = await session.scalar(
        insert(Job)
        .values(
            org_id=identity.org_id,
            actor_user_id=identity.user_id,
            actor_token_id=identity.token_id,
            actor_kind=identity.actor_kind,
            actor_scopes=sorted(identity.scopes),
            task_id=document.task_id,
            document_id=document_id,
            kind=kind,
            cache_key=cache_key,
            reasoning=level,
            provider_config_id=getattr(model, "provider_config_id", None)
            if kind == "extract"
            else None,
            provider_identity=model_identity(model) if kind == "extract" else None,
        )
        .on_conflict_do_nothing(index_elements=["org_id", "cache_key"])
        .returning(Job.id)
    )
    job = await session.scalar(select(Job).where(Job.cache_key == cache_key).with_for_update())
    assert job is not None
    if body.retry and (
        job.status in {"failed", "cancelled"}
        or (job.status == "running" and job.lease_until and job.lease_until < datetime.now(UTC))
    ):
        job.status, job.error, job.result, job.queue_id = "queued", None, {}, None
        if job.actor_user_id is None:
            job.actor_user_id, job.actor_token_id = identity.user_id, identity.token_id
            job.actor_kind, job.actor_scopes = identity.actor_kind, sorted(identity.scopes)
        job.attempts, job.lease_until, job.finished_at, job.run_id = 0, None, None, None
    if job.status in {"failed", "cancelled"}:
        return {
            "job_id": str(job.id),
            "status": job.status,
            "cached": True,
            "reasoning": job.reasoning,
        }, [
            *level_warnings,
            "This identical job is terminal. Inspect its status; use --retry explicitly to run it again.",
        ]
    if job.status == "queued" and job.queue_id is None:
        # Commit the durable job before making it visible to the independent queue.
        await session.commit()
        try:
            queue_id = await queue.enqueue(str(identity.org_id), str(job.id))
        except Exception as exc:
            raise ServiceError(
                "queue_unavailable",
                "Job is saved but queue is unavailable; repeat the request to schedule it",
                503,
                3,
            ) from exc
        async with db.transaction(identity.org_id) as update:
            saved = await update.get(Job, job.id)
            if saved is not None:
                saved.queue_id = queue_id
    return {
        "job_id": str(job.id),
        "status": job.status,
        "cached": identifier is None,
        "reasoning": job.reasoning,
    }, level_warnings
