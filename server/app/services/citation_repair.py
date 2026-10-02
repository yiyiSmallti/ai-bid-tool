"""Tenant-admin citation repair through the authenticated API transaction."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Chunk, Requirement
from app.models.response_cards import ResponseCard
from app.schemas.citation_repair_contracts import CitationRepairItem, CitationRepairRequest
from app.schemas.contracts import Source
from app.services import response_cards as cards
from app.services.auth import Identity
from app.services.drafts import digest
from app.services.extraction import locate_quote, source_text
from app.services.resources import audit


async def repair_citations(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    job_id: UUID,
    request: CitationRepairRequest | None = None,
) -> tuple[dict, list[dict]]:
    actor = await cards.access(session, actor, "req:extract")
    cards.human(actor, admin=True)
    if request is not None:
        if request.extraction_job_id != job_id:
            cards.fail("invalid_repair_scope", "The extraction job must match the repair scope")
        if not request.reason.strip():
            cards.fail("invalid_repair_reason", "A repair reason is required")
        await cards.task_lock(session, task_id)
    await cards.extraction_scope(session, task_id, job_id)
    query = (
        select(Requirement, Chunk)
        .join(Chunk, (Chunk.org_id == Requirement.org_id) & (Chunk.id == Requirement.chunk_id))
        .where(Requirement.task_id == task_id, Requirement.job_id == job_id)
        .order_by(Requirement.id)
        .execution_options(populate_existing=True)
    )
    if request is not None:
        query = query.with_for_update(of=(Requirement, Chunk))
    rows = (await session.execute(query)).all()
    bound_cards = list(
        (
            await session.scalars(
                select(ResponseCard)
                .where(ResponseCard.task_id == task_id, ResponseCard.extraction_job_id == job_id)
                .order_by(ResponseCard.id)
            )
        ).all()
    )
    items, manifest = [], []
    for requirement, chunk in rows:
        source = Source.model_validate(cards.source(requirement))
        stored = {
            "id": chunk.id,
            "document_id": chunk.document_id,
            "page": chunk.page,
            "blocks": chunk.blocks,
            "text": chunk.text,
            "citation_verified": chunk.citation_verified,
        }
        text = source_text(source, stored) if chunk.task_id == requirement.task_id else None
        proposed, reason = (
            locate_quote(text, requirement.quote)
            if text is not None
            else (None, "quote_not_at_position")
        )
        status = (
            "unlocatable"
            if proposed is None
            else "repairable"
            if proposed != requirement.quote or requirement.model_quote is None
            else "unchanged"
        )
        item = CitationRepairItem.model_validate(
            {
                "requirement_id": requirement.id,
                "source": source,
                "model_quote": requirement.model_quote,
                "proposed_quote": proposed,
                "status": status,
                "reason": reason,
                "quote_changed": proposed is not None and proposed != requirement.quote,
            }
        ).model_dump(mode="json")
        items.append(item)
        manifest.append(
            {
                **item,
                "text": requirement.text,
                "category": requirement.category,
                "starred": requirement.starred,
                "condition": requirement.condition,
                "fingerprint": requirement.fingerprint,
                "chunk_text": chunk.text,
                "blocks": chunk.blocks,
                "citation_verified": chunk.citation_verified,
            }
        )
    preview_hash = digest(
        {
            "version": "citation-repair-v1",
            "org_id": str(actor.org_id),
            "task_id": str(task_id),
            "extraction_job_id": str(job_id),
            "requirements": manifest,
            "cards": [
                {"id": str(card.id), "revision_id": str(card.current_revision_id)}
                for card in bound_cards
            ],
        }
    )
    changed = 0
    if request is not None:
        if request.expected_preview != preview_hash:
            cards.fail(
                "repair_preview_changed", "Inputs changed; obtain and review a new preview", 409
            )
        correlation = uuid4()
        for (requirement, _), item in zip(rows, items, strict=True):
            if item["status"] != "repairable":
                continue
            old_quote = requirement.quote
            old_model_quote = requirement.model_quote
            requirement.quote = item["proposed_quote"]
            if requirement.model_quote is None:
                requirement.model_quote = old_quote
            audit(
                session,
                actor,
                "requirement.repair_citation",
                requirement.id,
                {
                    "task_id": str(task_id),
                    "extraction_job_id": str(job_id),
                    "correlation_id": str(correlation),
                    "preview_hash": preview_hash,
                    "old_quote_sha256": cards.quote_hash(old_quote),
                    "new_quote_sha256": cards.quote_hash(requirement.quote),
                    "old_model_quote_sha256": cards.quote_hash(old_model_quote)
                    if old_model_quote is not None
                    else None,
                    "new_model_quote_sha256": cards.quote_hash(requirement.model_quote),
                    "reason_sha256": cards.quote_hash(request.reason),
                    "quote_changed": item["quote_changed"],
                    "card_ids": [
                        str(card.id)
                        for card in bound_cards
                        if card.requirement_id == requirement.id
                    ],
                    "actor_kind": actor.actor_kind,
                },
            )
            changed += 1
        await session.flush()
    return {
        "task_id": str(task_id),
        "extraction_job_id": str(job_id),
        "preview_hash": preview_hash,
        "execute": request is not None,
        "changed": changed,
        **{
            key: sum(item["status"] == key for item in items)
            for key in ("repairable", "unlocatable", "unchanged")
        },
    }, items
