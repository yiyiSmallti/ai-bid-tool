"""Shared B02 acceptance and preparation bindings for existing consumers.

Citation batches stay owned by their caller. Passing their results through review
evaluation preserves the one-locate-per-source invariant of assessment reads.
"""

from sqlalchemy import select

from app.core.errors import ServiceError
from app.models.entities import Requirement


def fields(review) -> dict:
    return {
        "policy_version": "requirement-review-v1",
        "revision": review.revision,
        "review_hash": review.review_hash,
        "state": review.state,
    }


def gap_reason(review) -> str | None:
    if review.state == "confirmed":
        return None
    return "requirement_invalidated" if review.state == "invalidated" else "requirement_unconfirmed"


async def effective(session, requirements, *, citations=None):
    from app.services.requirement_confirmation import effective_reviews

    return await effective_reviews(session, requirements, citation_validity=citations)


async def require_confirmed(session, requirements, *, citations=None):
    reviews = await effective(session, requirements, citations=citations)
    for requirement in requirements:
        reason = gap_reason(reviews[requirement.id])
        if reason:
            raise ServiceError(
                reason, "Confirm the current requirement before accepting output", 409, 2
            )
    return reviews


async def scoring_confirmed(session, task_id, extraction_id):
    requirements = list(
        await session.scalars(
            select(Requirement).where(
                Requirement.task_id == task_id,
                Requirement.job_id == extraction_id,
                Requirement.category == "scoring",
            )
        )
    )
    return await require_confirmed(session, requirements)


async def preparation(session, requirements, *, citations=None):
    """Pin full meaning/source and membership, without making confirmation paid work."""
    if len(requirements) > 2000:
        raise ServiceError(
            "requirement_manifest_limit", "At most 2000 requirements can be consumed", 422, 2
        )
    reviews = await effective(session, requirements, citations=citations)
    return {
        "policy_version": "requirement-review-v1",
        "entries": [
            {"requirement_id": str(row.id), "review_hash": reviews[row.id].review_hash}
            for row in sorted(requirements, key=lambda row: str(row.id))
        ],
    }


async def current_preparation(session, task_id, extraction_id, expected):
    requirements = list(
        await session.scalars(
            select(Requirement).where(
                Requirement.task_id == task_id,
                Requirement.job_id == extraction_id,
            )
        )
    )
    if expected != await preparation(session, requirements):
        raise ServiceError(
            "review_changed", "Requirement preparation inputs changed; refresh first", 409, 2
        )
