"""Reading extracted requirements and a task's extraction history."""

from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.models.entities import Chunk, Job, Requirement, Task
from app.services.auth import Identity


def latest_extractions(task_id: UUID):
    # The most recent succeeded extraction of each document in the task.
    return (
        select(Job.id)
        .where(Job.task_id == task_id, Job.kind == "extract", Job.status == "succeeded")
        .order_by(Job.document_id, Job.finished_at.desc().nulls_last(), Job.created_at.desc())
        .distinct(Job.document_id)
    )


def _fields(row, names: tuple[str, ...]) -> dict:
    return jsonable_encoder({name: getattr(row, name) for name in names})


async def list_requirements(
    session: AsyncSession, identity: Identity, task_id: UUID, job: UUID | None
) -> list[dict]:
    """Requirements of one extraction job, or of each document's latest, in source order."""
    identity.require("task:read")
    if await session.get(Task, task_id) is None:
        raise not_found()
    if job is not None:
        chosen = await session.get(Job, job)
        if chosen is None or chosen.task_id != task_id or chosen.kind != "extract":
            raise not_found()
        jobs = select(Job.id).where(Job.id == job)
    else:
        jobs = latest_extractions(task_id)
    pairs = (
        await session.execute(
            select(Requirement, Chunk, Job.reasoning)
            .join(Chunk, (Chunk.org_id == Requirement.org_id) & (Chunk.id == Requirement.chunk_id))
            .join(Job, (Job.org_id == Requirement.org_id) & (Job.id == Requirement.job_id))
            .where(Requirement.task_id == task_id, Requirement.job_id.in_(jobs))
        )
    ).all()

    def reading_order(pair):
        # Source order: chunk sequence, then the cited block's position inside the chunk.
        requirement, chunk, _ = pair
        block_ids = [block["block_id"] for block in chunk.blocks or []]
        block_id = (requirement.location or {}).get("block_id")
        position = block_ids.index(block_id) if block_id in block_ids else 0
        return (
            str(requirement.document_id),
            chunk.seq,
            position,
            requirement.created_at,
            str(requirement.id),
        )

    return [
        {
            **_fields(
                row, ("id", "text", "category", "starred", "condition", "job_id", "model_quote")
            ),
            "reasoning": reasoning,
            "source": _fields(row, ("document_id", "chunk_id", "page", "location", "quote")),
        }
        for row, _, reasoning in sorted(pairs, key=reading_order)
    ]


async def extraction_history(
    session: AsyncSession, identity: Identity, task_id: UUID, document: UUID | None
) -> list[dict]:
    identity.require("task:read")
    if await session.get(Task, task_id) is None:
        raise not_found()
    query = select(Job).where(Job.task_id == task_id, Job.kind == "extract")
    if document is not None:
        query = query.where(Job.document_id == document)
    jobs = (await session.scalars(query.order_by(Job.created_at.desc(), Job.id))).all()
    latest = set((await session.scalars(latest_extractions(task_id))).all())
    items = []
    for job in jobs:
        outcome = job.result or {}
        items.append(
            {
                "job_id": str(job.id),
                "document_id": str(job.document_id),
                "reasoning": job.reasoning,
                "model": outcome.get("model"),
                "status": job.status,
                "created_at": job.created_at.isoformat(),
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "saved": outcome.get("created"),
                "rejected": len(outcome["rejected"]) if "rejected" in outcome else None,
                "tokens": (outcome.get("cost") or {}).get("llm_tokens"),
                "error": job.error,
                # Shown by default in req list.
                "latest": job.id in latest,
            }
        )
    return items
