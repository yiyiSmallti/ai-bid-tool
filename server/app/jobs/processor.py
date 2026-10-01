import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError
from app.models.entities import Chunk, Document, Job, Requirement, UsageRecord
from app.providers.base import LLMProvider, OCRProvider, ProviderFailure
from app.providers.storage import Storage
from app.schemas.contracts import Extraction, ProviderUsage
from app.services.extraction import fingerprint, merge_starred, validate_extraction
from app.services.parsing import parse_document


class Processor:
    def __init__(
        self, settings: Settings, db: Database, storage: Storage, llm: LLMProvider, ocr: OCRProvider
    ):
        self.settings, self.db, self.storage, self.llm, self.ocr = settings, db, storage, llm, ocr

    async def record_usage(self, org_id: UUID, task_id: UUID, usages: list[ProviderUsage]):
        async with self.db.transaction(org_id) as session:
            for usage in usages:
                session.add(UsageRecord(org_id=org_id, task_id=task_id, **usage.model_dump()))

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
                )
                for row in (
                    await session.scalars(
                        select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.page)
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
                output = await self.llm.extract(chunks, Extraction.model_json_schema())
                usages = [output.usage]
                # Usage is recorded even when output is invalid or the job is cancelled.
                await self.record_usage(org_id, task_id, usages)
                charged.extend(usages)
                usages = []
                validate_extraction(output.extraction, chunks)
                requirements = merge_starred(output.extraction, chunks)
                validate_extraction(requirements, chunks)
                warnings = (
                    ["TEST PROVIDER: results are synthetic and not real AI output."]
                    if output.usage.test_only
                    else []
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
                    for page in pages:
                        await session.execute(
                            insert(Chunk)
                            .values(
                                org_id=org_id,
                                task_id=task_id,
                                document_id=document_id,
                                **page.model_dump(),
                            )
                            .on_conflict_do_nothing(
                                index_elements=["org_id", "document_id", "page"]
                            )
                        )
                    document.status = "parsed"
                    document.page_count = len(pages)
                    result = {
                        "pages": len(pages),
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
                            quote=item.source.quote,
                            text=item.text,
                            category=item.category.value,
                            starred=item.starred,
                            condition=item.condition,
                            fingerprint=fingerprint(item),
                        )
                        created = await session.scalar(
                            insert(Requirement)
                            .values(**row)
                            .on_conflict_do_nothing(
                                index_elements=["org_id", "task_id", "fingerprint"]
                            )
                            .returning(Requirement.id)
                        )
                        saved += created is not None
                    result = {
                        "created": saved,
                        "starred": sum(item.starred for item in requirements.items),
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
