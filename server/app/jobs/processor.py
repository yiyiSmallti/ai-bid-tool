import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError, log_unexpected
from app.jobs.execution import JobExecution, authorized_job, job_cost, locked_job
from app.models.entities import Chunk, Document, Job, Requirement
from app.providers.base import LLMProvider, OCRProvider, ProviderFailure
from app.providers.llm import uncovered_parameters, with_reasoning
from app.providers.storage import Storage
from app.schemas.contracts import Extraction, SectionText
from app.services.extraction import fingerprint, merge_starred, split_cited, validate_extraction
from app.services.parsing import parse_document

logger = logging.getLogger(__name__)


class Processor:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        storage: Storage,
        llm: LLMProvider,
        ocr: OCRProvider,
        resolve=None,
    ):
        self.settings, self.db, self.storage, self.llm, self.ocr = settings, db, storage, llm, ocr
        # Optional coroutine (session) -> LLMProvider choosing the platform default model.
        self.resolve = resolve
        self.sandbox_browser = None
        self.sandbox_fetch_transport = None
        self.sandbox_resolver = None
        # Test seams for the vendor search and document converter HTTP transports.
        self.search_transport = None
        self.converter_transport = None

    async def __call__(self, org: str, job: str):
        org_id, job_id = UUID(org), UUID(job)
        from app.jobs.sandbox import process_if_sandbox

        if await process_if_sandbox(self, org_id, job_id):
            return
        async with self.db.transaction(org_id) as session:
            current = await locked_job(session, job_id)
            if current is None or current.status in {"cancelled", "succeeded", "failed"}:
                return
            if (
                current.status == "running"
                and current.lease_until
                and current.lease_until > datetime.now(UTC)
            ):
                return
            try:
                await authorized_job(session, current)
            except (ServiceError, ProviderFailure) as error:
                current.status = "failed"
                current.error = {
                    "code": error.code,
                    "message": "Submission authorization is no longer valid",
                }
                current.finished_at = datetime.now(UTC)
                return
            if current.kind == "card_generate":
                from app.memory.retrieval import recover_unsettled_calls

                # The job row is locked; preserve old reservations and lineage
                # before issuing a new run ID after a retry or expired lease.
                await recover_unsettled_calls(session, current)
            current.status = "running"
            current.attempts += 1
            run_id = uuid4()
            current.run_id = run_id
            current.result = {
                **current.result,
                "cost": await job_cost(session, job_id, self.settings.billing_currency),
            }
            current.lease_until = datetime.now(UTC) + timedelta(
                seconds=self.settings.job_lease_seconds
            )
            document = (
                await session.get(Document, current.document_id) if current.document_id else None
            )
            if document is None and current.kind != "provider_test":
                raise ServiceError("missing_document", "Resource not found", 404, 4)
            task_id, kind, document_id = current.task_id, current.kind, current.document_id
            reasoning = current.reasoning
            key, suffix, expected_hash = (
                (
                    document.storage_key,
                    Path(document.name).suffix.lower(),
                    document.sha256,
                )
                if document is not None
                else ("", "", "")
            )
            chunks = (
                []
                if kind
                in {
                    "draft",
                    "check",
                    "score_rubric",
                    "score",
                    "card_generate",
                    "memory_candidate",
                    "provider_test",
                    "export_render",
                    "export_preview",
                    "product_simulation",
                    "screenshot_render",
                    "screenshot_analyze",
                    "prototype_generate",
                    "screenshot_search",
                }
                else [
                    dict(
                        id=row.id,
                        document_id=row.document_id,
                        page=row.page,
                        text=row.text,
                        citation_verified=row.citation_verified,
                        blocks=row.blocks,
                    )
                    for row in (
                        await session.scalars(
                            select(Chunk)
                            .where(Chunk.document_id == document_id)
                            .order_by(Chunk.seq)
                        )
                    ).all()
                ]
            )

        execution = JobExecution(self.settings, self.db, org_id, job_id, run_id)
        async with execution.activate():
            rejected: list[dict[str, str]] = []

            try:
                if kind in {"parse", "extract"}:
                    async with self.db.transaction(org_id) as session:
                        authorized = await execution.owned_job(session)
                        await authorized_job(session, authorized)
                llm = self.llm
                if self.resolve and kind in {
                    "extract",
                    "card_generate",
                    "provider_test",
                    "screenshot_analyze",
                    "prototype_generate",
                    "product_simulation",
                }:
                    async with self.db.transaction(org_id) as session:
                        llm = await self.resolve(session, current)
                if kind == "memory_candidate":
                    from app.jobs.memory_candidate import process as propose_memories

                    await propose_memories(execution)
                    return
                if kind == "provider_test":
                    from app.services.provider_configs import execute_test

                    await execute_test(execution, llm)
                    return
                if kind == "export_render":
                    from app.jobs.export_render import render

                    try:
                        async with asyncio.timeout(self.settings.export_deadline_seconds):
                            await render(execution, self.storage)
                    except TimeoutError as exc:
                        raise ServiceError(
                            "export_render_timeout", "Export job deadline exceeded", 503, 3
                        ) from exc
                    return
                if kind == "product_simulation":
                    from app.services.product_simulation import process as simulate

                    await simulate(execution, self, llm)
                    return
                if kind == "export_preview":
                    from app.providers.converter import create_converter
                    from app.services.page_previews import convert

                    converter = create_converter(self.settings, self.converter_transport)
                    await convert(execution, self.storage, converter, self.settings)
                    return
                if kind in {"screenshot_render", "screenshot_analyze"}:
                    from app.services.screenshot_jobs import process_analysis, process_render

                    if kind == "screenshot_render":
                        await process_render(execution, self.storage)
                    else:
                        await process_analysis(execution, self.storage, llm)
                    return
                if kind == "screenshot_search":
                    from app.providers.search import create_search_provider
                    from app.services.vendor_search import process as search_vendor

                    await search_vendor(
                        execution,
                        await create_search_provider(self.settings, self.search_transport),
                    )
                    return
                if kind == "prototype_generate":
                    from app.services.prototype_generation import process as generate_prototype
                    from app.services.sandbox import browser_for

                    await generate_prototype(execution, self.storage, llm, browser_for(self))
                    return
                assert task_id is not None and document_id is not None
                if kind == "score":
                    from app.jobs.score import process as process_score

                    await process_score(execution, self.storage)
                    return
                if kind == "score_rubric":
                    from app.jobs.score_rubric import process as process_score_rubric

                    await process_score_rubric(execution)
                    return
                if kind == "check":
                    from app.jobs.check import process as process_check

                    await process_check(execution, self.storage)
                    return
                if kind == "card_generate":
                    from app.services.card_generation import generate

                    # Drafting uses only its encrypted submission snapshot, and every
                    # HTTP call settles through this active JobExecution context.
                    await generate(execution, llm, self.storage)
                    return
                if kind == "draft":
                    from app.services.drafts import complete_draft
                    from app.services.response_cards import task_lock

                    async with self.db.transaction(org_id) as session:
                        # Submission and selection changes use this same lock order.
                        await task_lock(session, task_id)
                        current = await session.scalar(
                            select(Job).where(Job.id == job_id).with_for_update()
                        )
                        if (
                            current is None
                            or current.status != "running"
                            or current.run_id != run_id
                        ):
                            return
                        await authorized_job(session, current)
                        result = await complete_draft(session, current, self.storage)
                        current.status, current.result, current.error = "succeeded", result, None
                        current.finished_at = datetime.now(UTC)
                    return
                if kind == "parse":
                    content = await self.storage.read(org_id, key)
                    if hashlib.sha256(content).hexdigest() != expected_hash:
                        raise ServiceError(
                            "content_mismatch", "Stored document hash does not match", 409, 4
                        )
                    if not getattr(self.ocr, "records_calls", False):
                        raise ProviderFailure(
                            "OCR provider does not support call accounting",
                            code="provider_accounting_required",
                        )
                    pages, _, warnings = await parse_document(
                        content, suffix, self.ocr, self.settings.max_pages, self.settings
                    )
                    requirements = None
                elif kind == "extract":
                    if not chunks or not all(chunk["citation_verified"] for chunk in chunks):
                        raise ServiceError(
                            "unverified_document",
                            "Parse a PDF with verified pages before extraction",
                            400,
                            2,
                        )
                    # The level was fixed when the job was created; a removed level fails here.
                    llm, reasoning, _ = with_reasoning(llm, reasoning)
                    if not getattr(llm, "records_calls", False):
                        raise ProviderFailure(
                            "LLM provider does not support call accounting",
                            code="provider_accounting_required",
                        )
                    output = await llm.extract(chunks, Extraction.model_json_schema())
                    kept, rejected = split_cited(output.extraction, chunks)
                    rejected = [*output.rejected, *rejected]
                    if rejected and not kept.items:
                        raise ServiceError(
                            "invalid_citation",
                            "No extracted requirement cited its source verbatim; nothing was saved",
                            400,
                            4,
                        )
                    requirements = merge_starred(kept, chunks)
                    # Rule-added items quote the source themselves; a failure here is a bug.
                    validate_extraction(requirements, chunks)
                    warnings = (
                        ["TEST PROVIDER: results are synthetic and not real AI output."]
                        if output.usage.test_only
                        else []
                    )
                    if rejected:
                        warnings.append(
                            f"{len(rejected)} extracted requirements were not saved because their "
                            "quote or position was empty, unknown, unmatched or ambiguous; see result.rejected."
                        )
                    pages = []
                else:
                    raise ServiceError("invalid_job", "Unsupported job kind", 400, 2)

                async with self.db.transaction(org_id) as session:
                    current = await execution.owned_job(session)
                    await authorized_job(session, current)
                    if current is None or current.status == "cancelled" or current.run_id != run_id:
                        return
                    document = await session.get(Document, document_id)
                    if document is None:
                        raise ServiceError("missing_document", "Resource not found", 404, 4)
                    if requirements is None:
                        word = bool(pages) and isinstance(pages[0], SectionText)
                        if word:
                            # Earlier parser versions stored one unverified page-less chunk;
                            # nothing can cite it, so it is replaced by located sections.
                            await session.execute(
                                delete(Chunk).where(
                                    Chunk.document_id == document_id,
                                    Chunk.blocks.is_(None),
                                    Chunk.citation_verified.is_(False),
                                )
                            )
                        for page in pages:
                            values = (
                                {
                                    "seq": page.seq,
                                    "page": None,
                                    "text": page.text,
                                    "blocks": [block.model_dump() for block in page.blocks],
                                }
                                if isinstance(page, SectionText)
                                else {**page.model_dump(), "seq": page.page}
                            )
                            inserted = await session.scalar(
                                insert(Chunk)
                                .values(
                                    org_id=org_id,
                                    task_id=task_id,
                                    document_id=document_id,
                                    **values,
                                )
                                .on_conflict_do_nothing(
                                    index_elements=["org_id", "document_id", "seq"]
                                )
                                .returning(Chunk.id)
                            )
                            if inserted is None:
                                stored = await session.scalar(
                                    select(Chunk).where(
                                        Chunk.document_id == document_id,
                                        Chunk.seq == values["seq"],
                                    )
                                )
                                if stored is not None and any(
                                    getattr(stored, key) != value for key, value in values.items()
                                ):
                                    warnings.append(
                                        f"{'Section' if word else 'Page'} {values['seq']} retains "
                                        "previously stored parse text to preserve existing citations; "
                                        "the new parse was not saved. Upload the document in a new "
                                        "task to use the new parsing result."
                                    )
                        document.status = "parsed"
                        document.citation_mode = "block" if word else "page"
                        document.page_count = None if word else len(pages)
                        result = {
                            "pages": 0 if word else len(pages),
                            "sections": len(pages) if word else 0,
                            "document_id": str(document_id),
                            "warnings": warnings,
                        }
                    else:
                        saved = 0
                        gap_added = 0
                        for item in requirements.items:
                            item_fingerprint = fingerprint(item)
                            row = dict(
                                org_id=org_id,
                                task_id=task_id,
                                document_id=document_id,
                                chunk_id=item.source.chunk_id,
                                page=item.source.page,
                                location=item.source.location.model_dump()
                                if item.source.location
                                else None,
                                quote=item.source.quote,
                                model_quote=item.model_quote,
                                text=item.text,
                                category=item.category.value,
                                starred=item.starred,
                                condition=item.condition,
                                fingerprint=item_fingerprint,
                                job_id=job_id,
                            )
                            created = await session.scalar(
                                insert(Requirement)
                                .values(**row)
                                .on_conflict_do_nothing(
                                    index_elements=["org_id", "job_id", "fingerprint"]
                                )
                                .returning(Requirement.id)
                            )
                            saved += created is not None
                            if (
                                created is not None
                                and item_fingerprint in output.gap_fill.fingerprints
                            ):
                                gap_added += 1
                        result = {
                            "created": saved,
                            "starred": sum(item.starred for item in requirements.items),
                            "rejected": rejected,
                            "model": llm.model,
                            "reasoning": reasoning,
                            "warnings": warnings,
                            "gap_fill": {
                                "segments": output.gap_fill.segments,
                                "calls": output.gap_fill.calls,
                                "added": gap_added,
                                "remaining": uncovered_parameters(chunks, requirements.items)[1],
                            },
                        }
                    result["cost"] = await job_cost(session, job_id, self.settings.billing_currency)
                    current.status, current.result, current.error = "succeeded", result, None
                    current.finished_at = datetime.now(UTC)
            except Exception as exc:
                if isinstance(exc, ServiceError):
                    error = {"code": exc.code, "message": exc.message, "exit_code": exc.exit_code}
                    # Exit code 3 marks transient failures such as object storage outages.
                    retryable = exc.exit_code == 3 and exc.code not in {
                        "draft_input_changed",
                        "check_input_changed",
                        "score_rubric_input_changed",
                        "score_input_changed",
                        "score_stale_draft",
                        "score_rubric_unconfirmed",
                        "export_input_changed",
                        "generation_input_changed",
                        "generation_model_changed",
                        "generation_rules_changed",
                    }
                elif isinstance(exc, ProviderFailure):
                    error = {
                        "code": exc.code,
                        "message": str(exc),
                        "exit_code": 3 if exc.retryable else 4,
                    }
                    retryable = exc.retryable
                else:
                    log_unexpected(logger, f"Job {job_id}", exc)
                    error = {
                        "code": "processing_failed",
                        "message": "Processing failed; no unverified results were saved",
                        "exit_code": 4,
                    }
                    retryable = False
                async with self.db.transaction(org_id) as session:
                    current = await locked_job(session, job_id)
                    if current is None or current.status == "cancelled" or current.run_id != run_id:
                        return
                    should_retry = retryable and current.attempts < 3 and kind != "provider_test"
                    current.status = "queued" if should_retry else "failed"
                    current.error = error
                    current.result = {
                        **current.result,
                        "cost": await job_cost(session, job_id, self.settings.billing_currency),
                    }
                    if rejected:
                        current.result = {**current.result, "rejected": rejected}
                    current.finished_at = None if should_retry else datetime.now(UTC)
                    if kind in {"score_rubric", "score"}:
                        from app.services.score_generation import worker
                        from app.services.versioned import audit

                        audit(
                            session,
                            worker(current),
                            f"{kind}.failed",
                            job_id,
                            {
                                "task_id": str(task_id),
                                "job_id": str(job_id),
                                "run_id": str(run_id),
                                "actor_kind": "worker",
                                "input_hash": current.result["submission"]["input_hash"],
                                "error_code": error["code"],
                            },
                        )
                    if kind == "export_render":
                        from app.jobs.export_render import failure_audit

                        await failure_audit(session, current, error["code"])
                    if kind == "card_generate":
                        from app.services.card_generation import worker
                        from app.services.versioned import audit

                        audit(
                            session,
                            worker(current),
                            "card.generate.retry" if should_retry else "card.generate.failed",
                            job_id,
                            {
                                "generation_job_id": str(job_id),
                                "run_id": str(run_id),
                                "reason_code": error["code"],
                                "actor_kind": "worker",
                                "correlation_id": str(job_id),
                            },
                        )
                if should_retry:
                    raise ProviderFailure("Retryable provider failure", retryable=True) from None
