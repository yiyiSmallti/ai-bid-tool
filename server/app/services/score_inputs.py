"""Frozen scoring-requirement inputs for rubric candidate generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.core.errors import not_found
from app.models.confidential import ConfidentialField
from app.models.entities import (
    ApiToken,
    Chunk,
    Document,
    Job,
    Membership,
    Org,
    Requirement,
    Task,
    User,
)
from app.services import confidential, drafts, redaction
from app.services import response_cards as cards
from app.services.auth import Identity

MAX_SCORING_REQUIREMENTS = 2000
NORMALIZATION_RULE_VERSION = "score-rubric-normalization-v1"


@dataclass
class RubricSnapshot:
    task: Task
    extraction: Job
    document: Document
    requirements: list[Requirement]
    manifest: dict
    secret: dict
    input_hash: str


async def access(session: AsyncSession, actor: Identity, scope: str) -> Identity:
    return await cards.access(session, actor, scope)


def integrity() -> NoReturn:
    cards.fail(
        "score_rubric_input_integrity",
        "Scoring requirement relationships or citations are invalid",
        409,
        4,
    )


def provider_id(index: int) -> UUID:
    """A repeatable call-local UUID label with no persisted-ID information."""

    return UUID(int=index)


def source_original(requirement: Requirement, chunk: Chunk) -> str:
    """Read only the cited page/block; never walk adjacent chunks or the document."""

    if requirement.page is not None:
        if requirement.location is not None or chunk.page != requirement.page:
            integrity()
        return chunk.text
    if requirement.location is None:
        integrity()
    matches = [
        block["text"]
        for block in chunk.blocks or []
        if {key: value for key, value in block.items() if key != "text"} == requirement.location
    ]
    if len(matches) != 1:
        integrity()
    return matches[0]


async def lock_inputs(
    session: AsyncSession, actor: Identity, task_id: UUID, extraction_job_id: UUID
) -> None:
    """Freeze authorization, redaction revisions and the immutable extraction scope."""

    session.expire_all()
    await cards.task_lock(session, task_id)
    await session.scalar(select(Job).where(Job.id == extraction_job_id).with_for_update(read=True))
    await session.scalars(
        select(Requirement.id)
        .where(
            Requirement.job_id == extraction_job_id,
            Requirement.category == "scoring",
        )
        .order_by(Requirement.id)
        .with_for_update(read=True)
    )
    await session.scalar(select(Org).where(Org.id == actor.org_id).with_for_update(read=True))
    await session.scalar(select(User).where(User.id == actor.user_id).with_for_update(read=True))
    await session.scalar(
        select(Membership).where(Membership.user_id == actor.user_id).with_for_update(read=True)
    )
    if actor.token_id is not None:
        await session.scalar(
            select(ApiToken).where(ApiToken.id == actor.token_id).with_for_update(read=True)
        )
    await session.scalars(
        select(ConfidentialField).order_by(ConfidentialField.id).with_for_update(read=True)
    )


async def snapshot(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    extraction_job_id: UUID,
) -> RubricSnapshot:
    task = await session.get(Task, task_id)
    extraction = await session.get(Job, extraction_job_id)
    if (
        task is None
        or extraction is None
        or extraction.task_id != task_id
        or extraction.kind != "extract"
        or extraction.document_id is None
    ):
        raise not_found()
    if extraction.status != "succeeded":
        cards.fail("invalid_extraction_job", "Choose a succeeded extraction job")
    document = await session.get(Document, extraction.document_id)
    if document is None or document.task_id != task_id:
        raise not_found()
    located = list(
        (
            await session.execute(
                select(Requirement, Chunk)
                .options(
                    load_only(
                        Requirement.id,
                        Requirement.org_id,
                        Requirement.task_id,
                        Requirement.document_id,
                        Requirement.chunk_id,
                        Requirement.page,
                        Requirement.location,
                        Requirement.quote,
                        Requirement.text,
                        Requirement.category,
                        Requirement.starred,
                        Requirement.condition,
                        Requirement.model_quote,
                        Requirement.job_id,
                    ),
                    load_only(
                        Chunk.id,
                        Chunk.org_id,
                        Chunk.task_id,
                        Chunk.document_id,
                        Chunk.page,
                        Chunk.seq,
                        Chunk.text,
                        Chunk.citation_verified,
                        Chunk.blocks,
                    ),
                )
                .join(Chunk, Chunk.id == Requirement.chunk_id)
                .where(
                    Requirement.job_id == extraction_job_id,
                    Requirement.task_id == task_id,
                    Requirement.document_id == document.id,
                    Requirement.category == "scoring",
                )
                .order_by(Chunk.seq, Requirement.id)
            )
        ).all()
    )
    if not located:
        cards.fail(
            "empty_scoring_requirements",
            "Extraction job has no saved scoring requirements",
        )
    if len(located) > MAX_SCORING_REQUIREMENTS:
        cards.fail(
            "score_rubric_requirement_limit",
            "A rubric supports at most 2000 scoring requirements",
        )
    requirements: list[Requirement] = []
    fixed_requirements: list[dict] = []
    secret_requirements: list[dict] = []
    citations = cards.citation_validity_batch(
        (requirement for requirement, _ in located), {chunk.id: chunk for _, chunk in located}
    )
    from app.services import requirement_consumption

    review_inputs = await requirement_consumption.preparation(
        session, [row for row, _ in located], citations=citations
    )
    for index, (requirement, chunk) in enumerate(located, 1):
        if requirement.category != "scoring" or not citations[requirement.id]:
            integrity()
        source = cards.source(requirement)
        original = source_original(requirement, chunk)
        local_id = provider_id(index)
        fixed_requirements.append(
            {
                "requirement_id": str(requirement.id),
                "provider_id": str(local_id),
                "document_id": str(requirement.document_id),
                "chunk_id": str(requirement.chunk_id),
                "chunk_sha256": drafts.digest(
                    {
                        "text": chunk.text,
                        "blocks": chunk.blocks,
                        "page": chunk.page,
                        "citation_verified": chunk.citation_verified,
                    }
                ),
                "source_sha256": drafts.digest(source),
                # condition is intentionally absent from both this digest and outbound input.
                "requirement_sha256": drafts.digest(
                    {
                        "text": requirement.text,
                        "category": requirement.category,
                        "starred": requirement.starred,
                    }
                ),
            }
        )
        secret_requirements.append(
            {
                "requirement_id": str(requirement.id),
                "provider_id": str(local_id),
                "text": requirement.text,
                "source": source,
                "source_original": original,
            }
        )
        requirements.append(requirement)
    registered = await confidential.task_entries(session, task_id)
    if registered:
        actor.require("confidential:read")
    fixed_fields = [
        {
            "field_id": str(entry.field.id),
            "field_revision": entry.field.revision,
            "value_id": str(entry.value.id) if entry.value else None,
        }
        for _, entry in sorted(registered.items())
    ]
    warning_codes = await cards.scope_warnings(session, extraction.id)
    manifest = {
        "org_id": str(actor.org_id),
        "task_id": str(task_id),
        "extraction_job_id": str(extraction.id),
        "document_id": str(document.id),
        "document_sha256": document.sha256,
        "scope": "scoring_requirements",
        "requirement_preparation": review_inputs,
        "requirements": fixed_requirements,
        "warnings": warning_codes,
        "normalization_rule_version": NORMALIZATION_RULE_VERSION,
        "model_redaction_enabled": task.model_redaction_enabled,
        "model_redaction_revision": task.model_redaction_revision,
        "redaction_rule_version": redaction.RULE_VERSION,
        "confidential": fixed_fields,
    }
    return RubricSnapshot(
        task=task,
        extraction=extraction,
        document=document,
        requirements=requirements,
        manifest=manifest,
        secret={"requirements": secret_requirements},
        input_hash=drafts.digest(manifest),
    )


async def require_dependencies(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    manifest: dict,
) -> None:
    """Authorize every parent again when reading/cancelling a durable rubric job."""

    task = await session.get(Task, task_id)
    extraction = await session.get(Job, UUID(manifest["extraction_job_id"]))
    document = await session.get(Document, UUID(manifest["document_id"]))
    if (
        task is None
        or extraction is None
        or extraction.task_id != task_id
        or document is None
        or extraction.document_id != document.id
        or document.task_id != task_id
        or manifest.get("org_id") != str(actor.org_id)
    ):
        raise not_found()
    expected = {UUID(entry["requirement_id"]) for entry in manifest.get("requirements", [])}
    rows = list(
        await session.scalars(
            select(Requirement).where(
                Requirement.org_id == actor.org_id,
                Requirement.task_id == task_id,
                Requirement.job_id == extraction.id,
                Requirement.id.in_(expected),
            )
        )
    )
    if {row.id for row in rows} != expected:
        raise not_found()
    chunk_ids = {UUID(entry["chunk_id"]) for entry in manifest.get("requirements", [])}
    available_chunks = set(
        await session.scalars(
            select(Chunk.id).where(
                Chunk.org_id == actor.org_id,
                Chunk.task_id == task_id,
                Chunk.document_id == document.id,
                Chunk.id.in_(chunk_ids),
            )
        )
    )
    if available_chunks != chunk_ids:
        raise not_found()
