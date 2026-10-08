"""Local uploaded-bid inventory, published once behind live attempt and input fences."""

import asyncio
import base64
import hashlib
import logging
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.errors import ServiceError
from app.jobs.execution import JobExecution, job_cost
from app.models.bid_review import (
    BidDocumentPage,
    BidPreparation,
    BidPreparationPublication,
    BidPreparedDocument,
    BidSubmission,
    BidSubmissionDocument,
)
from app.schemas.screenshot_contracts import PNGDescriptor
from app.services import bid_preparation as local
from app.services.auth import set_actor_context
from app.services.versioned import audit

logger = logging.getLogger(__name__)


async def staged_put(
    execution,
    storage,
    submission_id,
    preparation_id,
    object_id,
    document_id,
    page,
    key,
    digest,
    content,
):
    # A put receipt is not proof of business commit. These safe identities allow
    # reconciliation to retain encrypted orphans after ambiguous DB outcomes.
    logger.info(
        "bid_review.object_staged org_id=%s submission_id=%s preparation_id=%s "
        "job_id=%s run_id=%s object_id=%s document_id=%s page=%s sha256=%s",
        execution.org_id,
        submission_id,
        preparation_id,
        execution.job_id,
        execution.run_id,
        object_id,
        document_id,
        page,
        digest,
    )
    await storage.put(execution.org_id, key, content)


def changed() -> ServiceError:
    return ServiceError(
        "bid_prepare_input_changed", "Preparation input changed; preview and submit again", 409, 4
    )


async def snapshot(session, job, settings):
    """Resolve every parent through org/task/submission, never unrestricted Document rows."""
    from app.services.bid_review import build_manifest

    try:
        submitted = job.result["submission"]
        submission_id = UUID(submitted["submission_id"])
        preparation_id = UUID(submitted["preparation_id"])
        manifest = submitted["input_manifest"]
        input_hash = submitted["input_hash"]
    except (KeyError, TypeError, ValueError) as exc:
        raise changed() from exc
    root = await session.scalar(
        select(BidSubmission).where(
            BidSubmission.org_id == job.org_id,
            BidSubmission.task_id == job.task_id,
            BidSubmission.id == submission_id,
            BidSubmission.state == "uploaded",
        )
    )
    preparation = await session.scalar(
        select(BidPreparation).where(
            BidPreparation.org_id == job.org_id,
            BidPreparation.task_id == job.task_id,
            BidPreparation.submission_id == submission_id,
            BidPreparation.id == preparation_id,
            BidPreparation.job_id == job.id,
            BidPreparation.input_hash == input_hash,
            BidPreparation.created_by == job.actor_user_id,
        )
    )
    documents = list(
        (
            await session.scalars(
                select(BidSubmissionDocument)
                .where(
                    BidSubmissionDocument.org_id == job.org_id,
                    BidSubmissionDocument.task_id == job.task_id,
                    BidSubmissionDocument.submission_id == submission_id,
                )
                .order_by(BidSubmissionDocument.ordinal)
            )
        ).all()
    )
    if (
        root is None
        or preparation is None
        or len(documents) < 2
        or len(documents) > 20
        or {document.role for document in documents} != {"tender", "bid"}
        or [document.ordinal for document in documents] != list(range(1, len(documents) + 1))
        or manifest != build_manifest(root, documents, settings)
        or local.digest(manifest) != input_hash
    ):
        raise changed()
    if await session.scalar(
        select(BidPreparationPublication.id).where(
            BidPreparationPublication.org_id == job.org_id,
            BidPreparationPublication.submission_id == submission_id,
        )
    ):
        raise ServiceError(
            "bid_already_prepared", "Submission already has an immutable inventory", 409, 4
        )
    return root, preparation, documents


def word_map(structure: dict, pages: list[dict]) -> tuple[dict, dict[int, list[dict]], bool]:
    """Map only uniquely located structural text; unmapped blocks remain explicit gaps."""
    native = {page["page"]: re.sub(r"\s+", "", page["text"]) for page in pages}
    mapping = []
    per_page: dict[int, list[dict]] = {}
    unknown = False
    remaining = local.STRUCTURAL_MAPPING_WORK_LIMIT
    scan_work = 2 * (sum(len(text) for text in native.values()) + 32 * len(native))
    for section in structure["sections"]:
        for block in section["blocks"]:
            value = re.sub(r"\s+", "", block["text"])
            bounded = remaining >= scan_work
            matches = (
                [
                    number
                    for number, text in native.items()
                    if bounded and value and text.count(value) == 1
                ]
                if bounded
                else []
            )
            unique = (
                bounded
                and len(matches) == 1
                and sum(text.count(value) for text in native.values()) == 1
            )
            if bounded:
                remaining -= scan_work
            entry = {
                "block_id": block["block_id"],
                "page": matches[0] if unique else None,
                "mapping_status": "exact_text" if unique else "unknown",
                "mapping_reason": "exact_text"
                if unique
                else "not_unique"
                if bounded
                else "mapping_budget",
            }
            mapping.append(entry)
            if unique:
                per_page.setdefault(matches[0], []).append(entry)
            else:
                unknown = True
    return {**structure, "block_page_mapping": mapping}, per_page, unknown


async def process(execution: JobExecution, storage, converter) -> None:
    """Stop expensive child/conversion work when heartbeat loses the attempt."""

    async def watch():
        while True:
            if execution.stopped is not None:
                raise execution.stopped
            await asyncio.sleep(0.2)

    work = asyncio.create_task(prepare(execution, storage, converter))
    guard = asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait({work, guard}, return_when=asyncio.FIRST_COMPLETED)
        if guard in done:
            await guard
        await work
    finally:
        guard.cancel()
        work.cancel()
        await asyncio.gather(guard, work, return_exceptions=True)


async def prepare(execution: JobExecution, storage, converter) -> None:
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        actor = await local.worker_access(session, job)
        root, preparation, documents = await snapshot(session, job, execution.settings)
        task_id, submission_id, preparation_id = root.task_id, root.id, preparation.id
        input_hash = preparation.input_hash
        # Detached immutable parent data avoids long-running DB transactions.
        originals = [
            {
                field: getattr(document, field)
                for field in (
                    "id",
                    "file_id",
                    "ordinal",
                    "role",
                    "kind",
                    "media_type",
                    "sha256",
                    "size_bytes",
                    "storage_key",
                )
            }
            for document in documents
        ]
    prepared_documents = []
    prepared_pages = []
    page_total = 0
    for original in originals:
        await execution.heartbeat()
        content = await storage.read_bounded(
            execution.org_id, original["storage_key"], original["size_bytes"]
        )
        if (
            len(content) != original["size_bytes"]
            or hashlib.sha256(content).hexdigest() != original["sha256"]
        ):
            raise ServiceError(
                "bid_file_integrity", "Uploaded file failed length or hash verification", 409, 4
            )
        await local.validate_upload(content, original["media_type"], execution.settings)
        word = original["media_type"] == local.DOCX_MEDIA_TYPE
        structure = None
        office_identity = ""
        if word:
            if not execution.settings.review_office_profile:
                raise ServiceError(
                    "converter_profile_unavailable",
                    "DOCX renderer and font profile is not configured",
                    409,
                    4,
                )
            office_identity = (
                "office:"
                + hashlib.sha256(execution.settings.review_office_profile.encode()).hexdigest()
                + "+"
            )
            structure = await local.native_docx_structure(content, execution.settings)
            if converter is None:
                raise ServiceError(
                    "converter_unavailable", "Local DOCX conversion is not configured", 503, 4
                )
            # Office conversion is exclusively the existing bounded private service.
            # It sees a fixed neutral name, never an uploaded business filename.
            content = await converter.docx_to_pdf(content, "submission.docx")
        rendered_hash = hashlib.sha256(content).hexdigest()
        rendered_key = (
            original["storage_key"]
            if not word
            else (
                f"org/{execution.org_id}/bid-review/{submission_id}/preparations/{preparation_id}"
                f"/documents/{original['id']}/{rendered_hash}.pdf"
            )
        )
        document_id = uuid4()
        pages, warnings = [], set(structure["warnings"] if structure else [])
        signatures = 0
        renderer = ""
        # The full child parse must finish before its spool is consumed. One
        # artifact is uploaded at a time; no image list accumulates in memory.
        if page_total >= local.PAGE_LIMIT:
            raise ServiceError(
                "bid_page_limit", "Combined page inventory exceeds 1000 pages", 400, 2
            )
        async for page in local.pdf_pages(
            content, local.PAGE_LIMIT - page_total, execution.settings
        ):
            await execution.heartbeat()
            number = page["page"]
            if number != len(pages) + 1:
                raise ServiceError("bid_page_integrity", "Prepared page order is invalid", 409, 4)
            png = base64.b64decode(page.pop("image"), validate=True)
            descriptor = PNGDescriptor(
                sha256=hashlib.sha256(png).hexdigest(),
                size_bytes=len(png),
                width_px=page["width"],
                height_px=page["height"],
            ).model_dump(mode="json")
            key = (
                f"org/{execution.org_id}/bid-review/{submission_id}/preparations/{preparation_id}"
                f"/pages/{original['id']}/{number}/{descriptor['sha256']}.png"
            )
            identifier = uuid4()
            await staged_put(
                execution,
                storage,
                submission_id,
                preparation_id,
                identifier,
                original["id"],
                number,
                key,
                descriptor["sha256"],
                png,
            )
            renderer = office_identity + page["renderer_identity"]
            native = page["text"] if page["text"].strip() else None
            warnings.update(page["warnings"])
            signatures += page["signature_field_count"]
            prepared_pages.append(
                BidDocumentPage(
                    id=identifier,
                    org_id=execution.org_id,
                    task_id=task_id,
                    submission_id=submission_id,
                    preparation_id=preparation_id,
                    document_id=original["id"],
                    page=number,
                    role=original["role"],
                    original_sha256=original["sha256"],
                    rendered_pdf_sha256=rendered_hash,
                    image=descriptor,
                    storage_key=key,
                    render_profile=local.RENDER_PROFILE,
                    renderer_identity=renderer,
                    page_kind=page["page_kind"],
                    text_status="native" if native is not None else "unavailable",
                    text_sha256=hashlib.sha256(native.encode()).hexdigest()
                    if native is not None
                    else None,
                    text_encrypted=local.seal(
                        execution.settings, execution.org_id, identifier, native
                    )
                    if native is not None
                    else None,
                    price_page=True,
                    redaction_status="human_only",
                )
            )
            pages.append(page)
        if word:
            assert structure is not None
            structure, mapped, unknown = word_map(structure, pages)
            if unknown:
                warnings.add("docx_page_map_unknown")
        else:
            mapped = {}
        if signatures:
            warnings.add("pdf_signature_fields_not_validated")
        document_pages = prepared_pages[-len(pages) :]
        for row, page in zip(document_pages, pages, strict=True):
            row.structure_encrypted = local.seal(
                execution.settings,
                execution.org_id,
                row.id,
                {
                    **page["structure"],
                    "warnings": page["warnings"],
                    "signature_field_count": page["signature_field_count"],
                    "word_blocks": mapped.get(row.page, []),
                },
            )
        if word:
            await execution.heartbeat()
            await staged_put(
                execution,
                storage,
                submission_id,
                preparation_id,
                document_id,
                original["id"],
                None,
                rendered_key,
                rendered_hash,
                content,
            )
        prepared_documents.append(
            BidPreparedDocument(
                id=document_id,
                org_id=execution.org_id,
                task_id=task_id,
                submission_id=submission_id,
                preparation_id=preparation_id,
                document_id=original["id"],
                page_count=len(pages),
                rendered_pdf_sha256=rendered_hash,
                rendered_pdf_storage_key=rendered_key,
                render_profile=local.RENDER_PROFILE,
                renderer_identity=renderer,
                citation_mode="block" if word else "page",
                parsing_warnings=sorted(warnings),
                signature_field_count=signatures,
                structure_encrypted=local.seal(
                    execution.settings, execution.org_id, document_id, structure
                )
                if structure is not None
                else None,
            )
        )
        page_total += len(pages)
    # No inventory rows exist until every file/render has succeeded. Encrypted
    # orphan objects are retained on ambiguous commits; never delete blindly.
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        actor = await local.worker_access(session, job)
        await snapshot(session, job, execution.settings)
        await set_actor_context(session, actor)
        session.add_all(prepared_documents)
        await session.flush()
        session.add_all(prepared_pages)
        await session.flush()
        publication = BidPreparationPublication(
            id=uuid4(),
            org_id=execution.org_id,
            task_id=task_id,
            submission_id=submission_id,
            preparation_id=preparation_id,
            job_id=job.id,
            run_id=execution.run_id,
            input_hash=input_hash,
            page_count=page_total,
            published_by=actor.user_id,
        )
        session.add(publication)
        await session.flush()
        await execution.owned_job(session)
        job.result = {
            "submission": job.result["submission"],
            "submission_id": str(submission_id),
            "preparation_id": str(preparation_id),
            "page_count": page_total,
            "documents": len(prepared_documents),
            "completion": "complete",
            "cost": await job_cost(session, job.id, execution.settings.billing_currency),
            "published_ids": [str(publication.id)],
        }
        audit(
            session,
            actor,
            "bid_review.preparation.published",
            publication.id,
            {
                "task_id": str(task_id),
                "submission_id": str(submission_id),
                "preparation_id": str(preparation_id),
                "job_id": str(job.id),
                "run_id": str(execution.run_id),
                "input_hash": input_hash,
                "page_count": page_total,
                "documents": len(prepared_documents),
            },
        )
        job.status, job.error, job.finished_at, job.lease_until = (
            "succeeded",
            None,
            datetime.now(UTC),
            None,
        )


async def failure_audit(session, job, code: str) -> None:
    """Record a fixed failure code; operational events contain no native content."""
    actor = await local.worker_access(session, job)
    audit(
        session,
        actor,
        "bid_review.preparation.failed",
        job.id,
        {
            "task_id": str(job.task_id),
            "job_id": str(job.id),
            "run_id": str(job.run_id),
            "input_hash": job.result["submission"]["input_hash"],
            "reason_code": code,
        },
    )
