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
from app.models.entities import Chunk, Document, Job, Requirement, UsageRecord
from app.providers.base import LLMProvider, OCRProvider, ProviderFailure
from app.providers.llm import with_reasoning
from app.providers.storage import Storage
from app.schemas.contracts import Extraction, ProviderUsage, SectionText
from app.services import billing
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

    async def record_usage(self, org_id: UUID, task_id: UUID, usages: list[ProviderUsage]):
        async with self.db.transaction(org_id) as session:
            for usage in usages:
                record = UsageRecord(
                    id=uuid4(), org_id=org_id, task_id=task_id, **usage.model_dump()
                )
                session.add(record)
                await session.flush()
                # The charge leaves the prepaid balance in the same transaction as the record.
                await billing.charge_usage(session, org_id, record, self.settings.billing_currency)

    async def __call__(self, org: str, job: str):
        org_id, job_id = UUID(org), UUID(job)
        async with self.db.transaction(org_id) as session:
            current = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if current is None or current.status in {"cancelled", "succeeded", "failed"}:
                return
            if (
                current.status == "running"
                and current.lease_until
                and current.lease_until > datetime.now(UTC)
            ):
                return
            current.status = "running"
            current.attempts += 1
            current.run_id = uuid4()
            run_id = current.run_id
            current.lease_until = datetime.now(UTC) + timedelta(minutes=15)
            document = await session.get(Document, current.document_id)
            if document is None:
                raise ServiceError("missing_document", "Resource not found", 404, 4)
            task_id, kind, document_id = current.task_id, current.kind, document.id
            reasoning = current.reasoning
            llm = await self.resolve(session) if self.resolve else self.llm
            key, suffix, expected_hash = (
                document.storage_key,
                Path(document.name).suffix.lower(),
                document.sha256,
            )
            chunks = [
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
                        select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.seq)
                    )
                ).all()
            ]

        usages: list[ProviderUsage] = []
        charged: list[ProviderUsage] = []

        class RecordingOCR:
            name, version = self.ocr.name, self.ocr.version

            async def recognize(_self, image: bytes, page: int):
                recognized = await self.ocr.recognize(image, page)
                await self.record_usage(org_id, task_id, [recognized.usage])
                charged.append(recognized.usage)
                return recognized

        try:
            if kind == "parse":
                content = await self.storage.read(org_id, key)
                if hashlib.sha256(content).hexdigest() != expected_hash:
                    raise ServiceError(
                        "content_mismatch", "Stored document hash does not match", 409, 4
                    )
                pages, _, warnings = await parse_document(
                    content, suffix, RecordingOCR(), self.settings.max_pages
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
                if getattr(llm, "platform_model_id", None):
                    async with self.db.transaction(org_id) as session:
                        await billing.require_funds(session, self.settings.billing_currency)
                output = await llm.extract(chunks, Extraction.model_json_schema())
                usages = [output.usage]
                # Usage is recorded even when output is invalid or the job is cancelled.
                await self.record_usage(org_id, task_id, usages)
                charged.extend(usages)
                usages = []
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
                        "quote was empty or not found at the cited position; see result.rejected."
                    )
                pages = []
            else:
                raise ServiceError("invalid_job", "Unsupported job kind", 400, 2)

            if usages:
                await self.record_usage(org_id, task_id, usages)
            async with self.db.transaction(org_id) as session:
                current = await session.scalar(
                    select(Job).where(Job.id == job_id).with_for_update()
                )
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
                        await session.execute(
                            insert(Chunk)
                            .values(
                                org_id=org_id, task_id=task_id, document_id=document_id, **values
                            )
                            .on_conflict_do_nothing(index_elements=["org_id", "document_id", "seq"])
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
                    for item in requirements.items:
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
                            text=item.text,
                            category=item.category.value,
                            starred=item.starred,
                            condition=item.condition,
                            fingerprint=fingerprint(item),
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
                    result = {
                        "created": saved,
                        "starred": sum(item.starred for item in requirements.items),
                        "rejected": rejected,
                        "model": llm.model,
                        "reasoning": reasoning,
                        "warnings": warnings,
                    }
                result["cost"] = {
                    "llm_tokens": sum(usage.tokens for usage in charged),
                    "ocr_pages": sum(usage.ocr_pages for usage in charged),
                    "usd": None
                    if any(usage.usd is None for usage in charged)
                    else sum(usage.usd or 0 for usage in charged),
                }
                current.status, current.result, current.error = "succeeded", result, None
                current.finished_at = datetime.now(UTC)
        except Exception as exc:
            if isinstance(exc, ServiceError):
                error = {"code": exc.code, "message": exc.message, "exit_code": exc.exit_code}
                # Exit code 3 marks transient failures such as object storage outages.
                retryable = exc.exit_code == 3
            elif isinstance(exc, ProviderFailure):
                error = {
                    "code": exc.code,
                    "message": str(exc),
                    "exit_code": 3 if exc.retryable else 4,
                }
                retryable = exc.retryable
                if exc.usage:
                    await self.record_usage(org_id, task_id, exc.usage)
            else:
                log_unexpected(logger, f"Job {job_id}", exc)
                error = {
                    "code": "processing_failed",
                    "message": "Processing failed; no unverified results were saved",
                    "exit_code": 4,
                }
                retryable = False
            async with self.db.transaction(org_id) as session:
                current = await session.scalar(
                    select(Job).where(Job.id == job_id).with_for_update()
                )
                if current is None or current.status == "cancelled" or current.run_id != run_id:
                    return
                should_retry = retryable and current.attempts < 3
                current.status = "queued" if should_retry else "failed"
                current.error = error
                current.finished_at = None if should_retry else datetime.now(UTC)
            if should_retry:
                raise ProviderFailure("Retryable provider failure", retryable=True) from None
