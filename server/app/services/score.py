"""Rubric human review, immutable replacement revisions and authorized history."""

import json
from datetime import datetime
from functools import wraps
from inspect import signature
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, literal, select, text, tuple_, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.core.config import Settings
from app.core.errors import ServiceError, not_found
from app.core.security import TokenSigner
from app.models.entities import Chunk, Document, Requirement, Task
from app.models.score import (
    ScoreRubricClassification,
    ScoreRubricCoverage,
    ScoreRubricCoverageDecision,
    ScoreRubricCoverageItem,
    ScoreRubricDecision,
    ScoreRubricItem,
    ScoreRubricRevisionEvent,
    ScoreRubricSection,
    ScoreRubricSet,
)
from app.providers.storage import Storage
from app.schemas.check_contracts import AssessmentListData
from app.schemas.score_contracts import (
    RubricClassificationView,
    RubricClassifyRequest,
    RubricCoverageDecisionRequest,
    RubricCoverageDecisionView,
    RubricDecisionView,
    RubricItemDecisionRequest,
    RubricItemView,
    RubricReportData,
    RubricRequirementCoverageView,
    RubricReviseRequest,
    RubricRevisionView,
    RubricSectionDecisionRequest,
    RubricSectionRevisionInput,
    RubricSectionView,
    RubricSetDecisionRequest,
)
from app.services import drafts, score_generation, score_inputs, score_normalization
from app.services import response_cards as cards
from app.services.auth import Identity
from app.services.extraction import locate_sent_source_quote
from app.services.task_authorization import task_authorized
from app.services.task_workflow import access as task_access
from app.services.versioned import audit


async def access(session: AsyncSession, actor: Identity, scope: str = "score:read") -> Identity:
    return await cards.access(session, actor, scope)


@task_authorized("score:read")
async def get_set(
    session: AsyncSession, actor: Identity, task_id: UUID, rubric_id: UUID, *, lock: bool = False
) -> tuple[Identity, ScoreRubricSet]:
    actor = await access(session, actor)
    if await session.get(Task, task_id) is None:
        raise not_found()
    query = select(ScoreRubricSet).where(
        ScoreRubricSet.id == rubric_id, ScoreRubricSet.task_id == task_id
    )
    if lock:
        await cards.task_lock(session, task_id)
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise not_found()
    await require_dependencies(session, actor, row, require_current=lock)
    await require_section_sources(session, row, require_current=lock)
    return actor, row


async def require_dependencies(
    session: AsyncSession, actor: Identity, row: ScoreRubricSet, *, require_current=False
) -> None:
    await score_inputs.require_dependencies(session, actor, row.task_id, row.input_manifest)
    if not require_current:
        return
    from app.services.requirement_consumption import preparation

    fixed = await score_inputs.snapshot(session, actor, row.task_id, row.extraction_job_id)
    if row.input_manifest.get("requirement_preparation") != await preparation(
        session, fixed.requirements, citations={req.id: True for req in fixed.requirements}
    ):
        cards.fail(
            "rubric_input_changed", "The fixed scoring inputs changed; generate a new rubric", 409
        )
    document = await session.get(Document, row.document_id)
    if document is None:
        raise not_found()
    if document.sha256 != row.input_manifest["document_sha256"]:
        cards.fail("rubric_input_changed", "The fixed scoring document has changed", 409)
    requirements = {
        value.id: value
        for value in await session.scalars(
            select(Requirement).where(
                Requirement.id.in_(
                    [UUID(entry["requirement_id"]) for entry in row.input_manifest["requirements"]]
                )
            )
        )
    }
    chunks = {
        value.id: value
        for value in await session.scalars(
            select(Chunk).where(
                Chunk.id.in_(
                    [UUID(entry["chunk_id"]) for entry in row.input_manifest["requirements"]]
                )
            )
        )
    }
    citations = cards.citation_validity_batch(requirements.values(), chunks)
    for entry in row.input_manifest["requirements"]:
        requirement = requirements.get(UUID(entry["requirement_id"]))
        chunk = chunks.get(UUID(entry["chunk_id"]))
        if (
            requirement is None
            or chunk is None
            or requirement.document_id != row.document_id
            or requirement.chunk_id != chunk.id
            or chunk.document_id != row.document_id
            or chunk.task_id != row.task_id
        ):
            raise not_found()
        if (
            not citations[requirement.id]
            or drafts.digest(cards.source(requirement)) != entry["source_sha256"]
            or drafts.digest(
                {
                    "text": requirement.text,
                    "category": requirement.category,
                    "starred": requirement.starred,
                }
            )
            != entry["requirement_sha256"]
            or drafts.digest(
                {
                    "text": chunk.text,
                    "blocks": chunk.blocks,
                    "page": chunk.page,
                    "citation_verified": chunk.citation_verified,
                }
            )
            != entry["chunk_sha256"]
        ):
            cards.fail(
                "rubric_input_changed",
                "The fixed scoring source has changed or is no longer verifiable",
                409,
            )


def section_sources(row: ScoreRubricSection) -> list[dict[str, Any]]:
    """Expand legacy snapshots in memory without rewriting their stored history."""
    if row.sources is not None:
        return row.sources
    return [
        {
            "requirement_id": str(row.requirement_id),
            "source": row.source,
            "quote": row.source["quote"],
        }
    ]


async def require_section_sources(
    session: AsyncSession, row: ScoreRubricSet, *, require_current=False
) -> None:
    """Resolve every fixed Source; freshness blocks writes without hiding history.

    Immutable Source identity and parent authority remain required for reads.
    Content, classification and verifier drift are reported through B02 readiness;
    decisions additionally verify every current citation before changing state.
    """
    sections = list(
        await session.scalars(
            select(ScoreRubricSection)
            .options(
                load_only(
                    ScoreRubricSection.id,
                    ScoreRubricSection.requirement_id,
                    ScoreRubricSection.source,
                    ScoreRubricSection.sources,
                )
            )
            .where(
                ScoreRubricSection.rubric_id == row.id,
                ScoreRubricSection.org_id == row.org_id,
                ScoreRubricSection.task_id == row.task_id,
            )
        )
    )
    requirement_ids = {
        UUID(citation["requirement_id"])
        for section in sections
        for citation in section_sources(section)
    }
    requirements = {
        requirement.id: requirement
        for requirement in await session.scalars(
            select(Requirement).where(Requirement.id.in_(requirement_ids))
        )
    }
    chunks = {
        chunk.id: chunk
        for chunk in await session.scalars(
            select(Chunk).where(
                Chunk.id.in_({requirement.chunk_id for requirement in requirements.values()})
            )
        )
    }
    citations_valid = (
        cards.citation_validity_batch(requirements.values(), chunks) if require_current else {}
    )
    pinned = {entry["requirement_id"] for entry in row.input_manifest["requirements"]}
    for section in sections:
        sources = section_sources(section)
        if (
            not sources
            or sources[0]["requirement_id"] != str(section.requirement_id)
            or sources[0]["source"] != section.source
        ):
            cards.fail("rubric_input_changed", "The section source binding has changed", 409)
        for citation in sources:
            requirement = requirements.get(UUID(citation["requirement_id"]))
            if (
                requirement is None
                or requirement.org_id != row.org_id
                or requirement.task_id != row.task_id
            ):
                raise not_found()
            chunk = chunks.get(requirement.chunk_id)
            if (
                chunk is None
                or chunk.task_id != row.task_id
                or chunk.document_id != row.document_id
            ):
                raise not_found()
            # A changed Source no longer resolves the saved immutable identity.
            # Requirement review state alone remains a readable freshness change.
            if citation["source"] != cards.source(requirement):
                raise not_found()
            if (
                citation["requirement_id"] not in pinned
                or requirement.job_id != row.extraction_job_id
                or requirement.document_id != row.document_id
            ):
                raise not_found()
            if require_current and (
                requirement.category != "scoring"
                or not citations_valid[requirement.id]
                or locate_sent_source_quote(requirement.quote, citation["quote"])[0]
                != citation["quote"]
            ):
                cards.fail(
                    "rubric_input_changed", "The section citation is no longer verifiable", 409
                )


def require_confirmed_section_sources(report, reviews):
    """Check every ordered section binding using the already loaded review batch."""
    from app.services.requirement_consumption import gap_reason

    for section in report["sections"]:
        for citation in section["sources"]:
            current = reviews.get(UUID(citation["requirement_id"]))
            if current is None:
                cards.fail(
                    "rubric_input_changed",
                    "A section source is outside the fixed scoring input",
                    409,
                )
            reason = gap_reason(current)
            if reason:
                cards.fail(reason, "Confirm every scoring section source before acceptance", 409)


async def set_revision(session: AsyncSession, row: ScoreRubricSet) -> int:
    return int(
        await session.scalar(
            text("SELECT rubric_revision(:org,:rubric)"), {"org": row.org_id, "rubric": row.id}
        )
        or 1
    )


async def review_state(
    session: AsyncSession,
    row: ScoreRubricSet,
    section_id: UUID | None = None,
    item_id: UUID | None = None,
) -> dict:
    filters = (
        ScoreRubricDecision.rubric_id == row.id,
        ScoreRubricDecision.section_id == section_id,
        ScoreRubricDecision.item_id == item_id,
    )
    decision = await session.scalar(
        select(ScoreRubricDecision)
        .where(*filters)
        .order_by(ScoreRubricDecision.revision.desc())
        .limit(1)
    )
    classification = None
    if section_id is not None or item_id is not None:
        classification = await session.scalar(
            select(ScoreRubricClassification)
            .where(
                ScoreRubricClassification.rubric_id == row.id,
                ScoreRubricClassification.section_id == section_id,
                ScoreRubricClassification.item_id == item_id,
            )
            .order_by(ScoreRubricClassification.revision.desc())
            .limit(1)
        )
    revision = max(
        decision.revision if decision else 1, classification.revision if classification else 1
    )
    current = decision if decision and decision.revision == revision else None
    state = (
        {"confirm": "confirmed", "reject": "rejected", "reopen": "candidate"}.get(
            current.action, "candidate"
        )
        if current
        else "candidate"
    )
    is_set = section_id is None and item_id is None
    if is_set:
        revision = await set_revision(session, row)
        if await session.scalar(
            select(ScoreRubricSet.id).where(ScoreRubricSet.prior_rubric_id == row.id).limit(1)
        ):
            state = "superseded"
    return {
        "state": state,
        "revision": revision,
        "review_domain": classification.review_domain if classification else None,
        "confirmed_by": current.decided_by if current and current.action == "confirm" else None,
        "confirmed_at": current.decided_at if current and current.action == "confirm" else None,
    }


def fields(row, keys: tuple[str, ...]) -> dict:
    return {key: getattr(row, key) for key in keys}


async def coverage_view(session: AsyncSession, row: ScoreRubricCoverage) -> dict:
    latest = await session.scalar(
        select(ScoreRubricCoverageDecision)
        .where(ScoreRubricCoverageDecision.coverage_id == row.id)
        .order_by(ScoreRubricCoverageDecision.revision.desc())
        .limit(1)
    )
    mapped = (
        list(
            (
                await session.scalars(
                    select(ScoreRubricCoverageItem.rubric_item_id)
                    .where(ScoreRubricCoverageItem.coverage_decision_id == latest.id)
                    .order_by(ScoreRubricCoverageItem.rubric_item_id)
                )
            ).all()
        )
        if latest
        else []
    )
    active = latest is not None and latest.action != "reopen"
    return RubricRequirementCoverageView.model_validate(
        {
            **fields(row, ("id", "org_id", "task_id", "rubric_id", "requirement_id", "source")),
            "disposition": latest.action if active and latest else "pending",
            "rubric_item_ids": mapped,
            "canonical_requirement_id": latest.canonical_requirement_id
            if active and latest
            else None,
            "reason": latest.reason if active and latest else None,
            "decided_by": latest.decided_by if active and latest else None,
            "decided_at": latest.decided_at if active and latest else None,
            "revision": latest.revision if latest else 1,
        }
    ).model_dump(mode="json")


async def report_data(session: AsyncSession, row: ScoreRubricSet) -> dict:
    await require_section_sources(session, row)
    sections = []
    for section in (
        await session.scalars(
            select(ScoreRubricSection)
            .where(ScoreRubricSection.rubric_id == row.id)
            .order_by(ScoreRubricSection.order, ScoreRubricSection.id)
        )
    ).all():
        value = RubricSectionView.model_validate(
            {
                **fields(
                    section,
                    (
                        "id",
                        "org_id",
                        "task_id",
                        "rubric_id",
                        "key",
                        "title",
                        "order",
                        "aggregation",
                        "aggregation_rule_text",
                        "score_range",
                        "weight",
                        "cap",
                        "included_in_overall_total",
                        "ambiguity_reason",
                    ),
                ),
                "sources": section_sources(section),
                "aggregation_assessable": section.aggregation
                in {"sum", "weighted_sum", "capped_sum"},
                **await review_state(session, row, section_id=section.id),
            }
        ).model_dump(mode="json")
        value.update(citation_valid=section.citation_valid, fingerprint=section.fingerprint)
        sections.append(value)
    items = []
    for item in (
        await session.scalars(
            select(ScoreRubricItem)
            .where(ScoreRubricItem.rubric_id == row.id)
            .order_by(ScoreRubricItem.order, ScoreRubricItem.id)
        )
    ).all():
        value = RubricItemView.model_validate(
            {
                **fields(
                    item,
                    (
                        "id",
                        "org_id",
                        "task_id",
                        "rubric_id",
                        "section_id",
                        "requirement_id",
                        "key",
                        "title",
                        "rule_text",
                        "order",
                        "assessment_mode",
                        "score_range",
                        "weight",
                        "ambiguity_reason",
                        "source",
                        "fingerprint",
                    ),
                ),
                **await review_state(session, row, item_id=item.id),
            }
        ).model_dump(mode="json")
        value["citation_valid"] = item.citation_valid
        items.append(value)
    coverage = [
        await coverage_view(session, entry)
        for entry in (
            await session.scalars(
                select(ScoreRubricCoverage)
                .where(ScoreRubricCoverage.rubric_id == row.id)
                .order_by(ScoreRubricCoverage.requirement_id)
            )
        ).all()
    ]
    state = await review_state(session, row)
    state.pop("review_domain")
    rubric = {
        **fields(
            row,
            (
                "id",
                "org_id",
                "task_id",
                "extraction_job_id",
                "document_id",
                "version",
                "prior_rubric_id",
                "input_hash",
                "normalization_rule_version",
                "prompt_version",
                "schema_version",
                "overall_aggregation",
                "overall_rule_text",
                "overall_score_range",
                "overall_cap",
                "created_at",
            ),
        ),
        "overall_aggregation_assessable": row.overall_aggregation
        in {"sum", "weighted_sum", "capped_sum"},
        **state,
    }
    report = {
        "rubric": rubric,
        "sections": sections,
        "items": items,
        "coverage": coverage,
        "expected_requirement_ids": [entry["requirement_id"] for entry in coverage],
    }
    rubric["completeness"] = score_normalization.completeness(report).model_dump(mode="json")
    if row.normalization_errors:
        complete = rubric["completeness"]
        complete["normalization_errors"] = sorted(
            set(complete["normalization_errors"] + row.normalization_errors)
        )
        complete["complete"] = False
    for section in sections:
        section.pop("citation_valid")
        section.pop("fingerprint")
    for item in items:
        item.pop("citation_valid")
    rubric["requirement_review"] = await requirement_readiness(session, row)
    return RubricReportData.model_validate(
        {key: report[key] for key in ("rubric", "sections", "items", "coverage")}
    ).model_dump(mode="json")


async def requirement_readiness(session, row):
    from app.services import requirement_consumption

    fixed_ids = {UUID(entry["requirement_id"]) for entry in row.input_manifest["requirements"]}
    requirements = list(
        await session.scalars(
            select(Requirement).where(
                Requirement.task_id == row.task_id,
                Requirement.job_id == row.extraction_job_id,
                Requirement.id.in_(fixed_ids),
            )
        )
    )
    reviews = await requirement_consumption.effective(session, requirements)
    current_preparation = {
        "policy_version": "requirement-review-v1",
        "entries": [
            {"requirement_id": str(req.id), "review_hash": reviews[req.id].review_hash}
            for req in sorted(requirements, key=lambda value: str(value.id))
        ],
    }
    scoring_ids = set(
        await session.scalars(
            select(Requirement.id).where(
                Requirement.task_id == row.task_id,
                Requirement.job_id == row.extraction_job_id,
                Requirement.category == "scoring",
            )
        )
    )
    changed = (
        scoring_ids != fixed_ids
        or row.input_manifest.get("requirement_preparation") != current_preparation
    )
    confirmed = sum(value.confirmed for value in reviews.values())
    return {
        "state": "stale" if changed else "ready" if confirmed == len(fixed_ids) else "preparation",
        "fixed_count": len(fixed_ids),
        "confirmed_count": confirmed,
        "invalidation_codes": ["review_changed"]
        if changed
        else sorted(
            {
                reason
                for value in reviews.values()
                if (reason := requirement_consumption.gap_reason(value))
            }
        ),
    }


async def show_rubric(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    rubric_id: UUID,
    storage: Storage,
    settings: Settings,
) -> tuple[dict, list[dict]]:
    _, row = await get_set(session, actor, task_id, rubric_id)
    return await report_data(session, row), []


def cursor_read(
    settings: Settings, actor: Identity, task_id: UUID, rubric_id: UUID | None, cursor: str | None
) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    try:
        data = TokenSigner.for_tokens(settings).open(cursor)
        expected = {
            "kind": "rubric_cursor",
            "org_id": str(actor.org_id),
            "task_id": str(task_id),
            "rubric_id": str(rubric_id) if rubric_id else None,
        }
        if any(data.get(key) != value for key, value in expected.items()):
            raise ValueError("binding")
        return datetime.fromisoformat(data["created_at"]), UUID(data["id"])
    except (ServiceError, ValueError, KeyError, TypeError):
        cards.fail("invalid_cursor", "Cursor does not match this authorized query")


def page(
    settings: Settings,
    actor: Identity,
    task_id: UUID,
    rubric_id: UUID | None,
    rows: list[tuple[datetime, UUID, dict]],
    cursor: str | None,
    limit: int,
) -> tuple[dict, list[dict]]:
    if not 1 <= limit <= 200:
        cards.fail("invalid_input", "Limit must be between 1 and 200")
    anchor = cursor_read(settings, actor, task_id, rubric_id, cursor)
    rows.sort(key=lambda row: (row[0], row[1]))
    selected = [row for row in rows if anchor is None or (row[0], row[1]) > anchor]
    next_cursor = None
    if len(selected) > limit:
        last = selected[limit - 1]
        next_cursor = TokenSigner.for_tokens(settings).issue(
            {
                "kind": "rubric_cursor",
                "org_id": str(actor.org_id),
                "task_id": str(task_id),
                "rubric_id": str(rubric_id) if rubric_id else None,
                "created_at": last[0].isoformat(),
                "id": str(last[1]),
            },
            7 * 86400,
        )
    return AssessmentListData(task_id=task_id, total=len(rows), next_cursor=next_cursor).model_dump(
        mode="json"
    ), [row[2] for row in selected[:limit]]


@task_authorized("score:read")
async def list_rubrics(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    storage: Storage,
    settings: Settings,
    *,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[dict, list[dict]]:
    actor = await access(session, actor)
    if await session.get(Task, task_id) is None:
        raise not_found()
    if not 1 <= limit <= 200:
        cards.fail("invalid_input", "Limit must be between 1 and 200")
    anchor = cursor_read(settings, actor, task_id, None, cursor)
    conditions = [ScoreRubricSet.task_id == task_id, ScoreRubricSet.org_id == actor.org_id]
    for row in await session.scalars(select(ScoreRubricSet).where(*conditions)):
        await require_dependencies(session, actor, row)
    total = (
        await session.scalar(select(func.count()).select_from(ScoreRubricSet).where(*conditions))
        or 0
    )
    statement = select(ScoreRubricSet).where(*conditions)
    if anchor is not None:
        statement = statement.where(
            tuple_(ScoreRubricSet.created_at, ScoreRubricSet.id)
            > tuple_(literal(anchor[0]), literal(anchor[1]))
        )
    selected = list(
        (
            await session.scalars(
                statement.order_by(ScoreRubricSet.created_at, ScoreRubricSet.id).limit(limit + 1)
            )
        ).all()
    )
    next_cursor = None
    if len(selected) > limit:
        last = selected[limit - 1]
        next_cursor = TokenSigner.for_tokens(settings).issue(
            {
                "kind": "rubric_cursor",
                "org_id": str(actor.org_id),
                "task_id": str(task_id),
                "rubric_id": None,
                "created_at": last.created_at.isoformat(),
                "id": str(last.id),
            },
            7 * 86400,
        )
    return AssessmentListData(task_id=task_id, total=total, next_cursor=next_cursor).model_dump(
        mode="json"
    ), [(await report_data(session, row))["rubric"] for row in selected[:limit]]


async def human_set(
    session: AsyncSession, actor: Identity, task_id: UUID, rubric_id: UUID
) -> tuple[Identity, ScoreRubricSet]:
    actor, row = await get_set(session, actor, task_id, rubric_id)
    if actor.actor_kind != "session" or actor.token_id is not None:
        cards.fail(
            "human_session_required", "A human session is required for rubric review", 403, 4
        )
    await task_access(
        session,
        actor,
        task_id,
        scope="score:rubric:review",
        write=True,
        domain={"bidder": "commercial", "technical": "technical"}.get(actor.role),
    )
    actor, row = await get_set(session, actor, task_id, rubric_id, lock=True)
    actor = await access(session, actor, "score:rubric:review")
    extraction_id = row.extraction_job_id
    await score_inputs.lock_inputs(session, actor, task_id, extraction_id)
    await session.refresh(row)
    actor = await access(session, actor, "score:rubric:review")
    await require_dependencies(session, actor, row, require_current=True)
    if (await review_state(session, row))["state"] == "superseded":
        cards.fail("rubric_superseded", "Review the replacement rubric", 409)
    return actor, row


def cas(body, row: ScoreRubricSet, revision: int) -> None:
    if body.expected_input_hash != row.input_hash:
        cards.fail("rubric_input_changed", "Rubric input hash changed", 409)
    if body.expected_revision != revision:
        cards.fail("revision_conflict", "Read the current revision before reviewing", 409)


def human_role(actor: Identity, role: str | None) -> None:
    if role is None:
        cards.fail("unclassified", "Assign a review domain before deciding", 409)
    if actor.role != role:
        cards.fail("forbidden", "Review role does not match the stored domain", 403, 4)


async def require_candidate(session: AsyncSession, row: ScoreRubricSet) -> None:
    if (await review_state(session, row))["state"] == "confirmed":
        cards.fail("invalid_transition", "Reopen the rubric set before reviewing its contents", 409)


async def subject(
    session: AsyncSession,
    row: ScoreRubricSet,
    *,
    section_id: UUID | None = None,
    item_id: UUID | None = None,
) -> ScoreRubricSection | ScoreRubricItem:
    entry = (
        await session.get(ScoreRubricSection, section_id)
        if section_id
        else await session.get(ScoreRubricItem, item_id)
    )
    if entry is None or entry.rubric_id != row.id or entry.task_id != row.task_id:
        raise not_found()
    return entry


async def review_values(session: AsyncSession, actor: Identity, row: ScoreRubricSet, body) -> dict:
    return {
        "id": uuid4(),
        "org_id": actor.org_id,
        "task_id": row.task_id,
        "rubric_id": row.id,
        "revision": body.expected_revision + 1,
        "set_revision": await set_revision(session, row) + 1,
        "reason": body.reason,
        "reason_sha256": cards.quote_hash(body.reason),
        "expected_input_hash": body.expected_input_hash,
        "decided_by": actor.user_id,
        "actor_kind": "session",
    }


def review_audit(
    session: AsyncSession, actor: Identity, row: ScoreRubricSet, event, action: str = "decided"
) -> None:
    audit(
        session,
        actor,
        f"score_rubric.{action}",
        row.id,
        {
            "task_id": str(row.task_id),
            "rubric_id": str(row.id),
            "event_id": str(event.id),
            "input_hash": row.input_hash,
            "reason_sha256": event.reason_sha256,
            **(
                {"snapshot_sha256": event.snapshot_sha256}
                if isinstance(event, ScoreRubricRevisionEvent)
                else {}
            ),
        },
    )


async def validate_review_text(
    session: AsyncSession, actor: Identity, row: ScoreRubricSet, body, settings: Settings
) -> None:
    prompt_fields, library = await score_generation.secret_library(session, row.task_id, settings)
    allowed = {entry["placeholder"] for entry in prompt_fields}
    prose_keys = {
        "reason",
        "key",
        "title",
        "rule_text",
        "aggregation_rule_text",
        "ambiguity_reason",
        "overall_rule_text",
        "rubric_item_keys",
    }

    def safe(value) -> bool:
        if isinstance(value, dict):
            return all(
                score_generation.safe_stored_value(child, library, allowed)
                if key in prose_keys
                else safe(child)
                for key, child in value.items()
            )
        if isinstance(value, list):
            return all(safe(child) for child in value)
        return True

    if not safe(body.model_dump(mode="json")):
        cards.fail(
            "sensitive_review_text",
            "Use registered placeholders instead of confidential values in rubric review text",
            409,
        )


def review_denials(function):
    """Attach only authorized identifiers; API persists denial after rollback."""

    @wraps(function)
    async def wrapped(*args: Any, **kwargs: Any):
        try:
            arguments = signature(function).bind(*args, **kwargs).arguments
            _, bound = await get_set(
                arguments["session"],
                arguments["actor"],
                arguments["task_id"],
                arguments["rubric_id"],
            )
            if "section_id" in arguments or "item_id" in arguments:
                await subject(
                    arguments["session"],
                    bound,
                    section_id=arguments.get("section_id"),
                    item_id=arguments.get("item_id"),
                )
            if "requirement_id" in arguments and not await arguments["session"].scalar(
                select(ScoreRubricCoverage.id).where(
                    ScoreRubricCoverage.rubric_id == bound.id,
                    ScoreRubricCoverage.requirement_id == arguments["requirement_id"],
                )
            ):
                raise not_found()
            actor = arguments["actor"]
            if actor.actor_kind != "session" or actor.token_id is not None:
                cards.fail(
                    "human_session_required",
                    "A human session is required for rubric review",
                    403,
                    4,
                )
            await task_access(
                arguments["session"],
                actor,
                bound.task_id,
                scope="score:rubric:review",
                write=True,
                domain={"bidder": "commercial", "technical": "technical"}.get(actor.role),
            )
            await validate_review_text(
                arguments["session"],
                arguments["actor"],
                bound,
                arguments["body"],
                arguments["settings"],
            )
            return await function(*args, **kwargs)
        except ServiceError as error:
            if error.status == 404:
                raise
            arguments = signature(function).bind(*args, **kwargs).arguments
            session = arguments["session"]
            actor = arguments["actor"]
            try:
                _, bound = await get_set(
                    session, actor, arguments["task_id"], arguments["rubric_id"]
                )
                if "section_id" in arguments or "item_id" in arguments:
                    await subject(
                        session,
                        bound,
                        section_id=arguments.get("section_id"),
                        item_id=arguments.get("item_id"),
                    )
                if "requirement_id" in arguments and not await session.scalar(
                    select(ScoreRubricCoverage.id).where(
                        ScoreRubricCoverage.rubric_id == bound.id,
                        ScoreRubricCoverage.requirement_id == arguments["requirement_id"],
                    )
                ):
                    raise not_found()
            except ServiceError:
                raise error from None
            body = arguments["body"]
            error.__dict__["rubric_audit"] = {
                "object_id": str(bound.id),
                "task_id": str(bound.task_id),
                "rubric_id": str(bound.id),
                "input_hash": bound.input_hash,
                "reason_sha256": cards.quote_hash(body.reason),
                "revision": body.expected_revision,
                "error_code": error.code,
                "actor_kind": actor.actor_kind,
            }
            raise

    return wrapped


@review_denials
async def classify_rubric(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    rubric_id: UUID,
    body: RubricClassifyRequest,
    storage: Storage,
    settings: Settings,
    *,
    section_id: UUID | None = None,
    item_id: UUID | None = None,
) -> dict:
    actor, row = await human_set(session, actor, task_id, rubric_id)
    await subject(session, row, section_id=section_id, item_id=item_id)
    human_role(actor, "admin")
    await require_candidate(session, row)
    current = await review_state(session, row, section_id, item_id)
    cas(body, row, current["revision"])
    event = ScoreRubricClassification(
        **await review_values(session, actor, row, body),
        section_id=section_id,
        item_id=item_id,
        review_domain=body.review_domain,
    )
    session.add(event)
    await session.flush()
    review_audit(session, actor, row, event, "classified")
    return RubricClassificationView.model_validate(event).model_dump(mode="json")


async def decide_subject(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    rubric_id: UUID,
    body: RubricSectionDecisionRequest | RubricItemDecisionRequest,
    *,
    section_id: UUID | None = None,
    item_id: UUID | None = None,
) -> dict:
    actor, row = await human_set(session, actor, task_id, rubric_id)
    entry = await subject(session, row, section_id=section_id, item_id=item_id)
    await require_candidate(session, row)
    current = await review_state(session, row, section_id, item_id)
    human_role(
        actor, {"commercial": "bidder", "technical": "technical"}.get(current["review_domain"])
    )
    cas(body, row, current["revision"])
    if (
        body.action == "reopen"
        and current["state"] == "candidate"
        or body.action != "reopen"
        and current["state"] != "candidate"
    ):
        cards.fail(
            "invalid_transition",
            "Only candidates can be confirmed or rejected; decided subjects must reopen",
            409,
        )
    if body.action == "confirm" and not entry.citation_valid:
        cards.fail(
            "unresolved_citation", "Revise unresolved candidate citations before confirming", 409
        )
    if body.action == "confirm":
        kind = "section" if section_id is not None else "item"
        values = fields(
            entry,
            ("score_range", "weight", "ambiguity_reason")
            + (
                ("aggregation", "aggregation_rule_text", "cap")
                if kind == "section"
                else ("assessment_mode",)
            ),
        )
        if score_normalization.subject_errors(kind, values):
            cards.fail(
                "rubric_incomplete",
                "Revise the candidate's normalization errors before confirming it",
                409,
            )
    event = ScoreRubricDecision(
        **await review_values(session, actor, row, body),
        section_id=section_id,
        item_id=item_id,
        action=body.action,
    )
    session.add(event)
    await session.flush()
    review_audit(session, actor, row, event)
    return RubricDecisionView.model_validate(event).model_dump(mode="json")


@review_denials
async def decide_section(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    rubric_id: UUID,
    section_id: UUID,
    body: RubricSectionDecisionRequest,
    storage: Storage,
    settings: Settings,
) -> dict:
    return await decide_subject(session, actor, task_id, rubric_id, body, section_id=section_id)


@review_denials
async def decide_item(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    rubric_id: UUID,
    item_id: UUID,
    body: RubricItemDecisionRequest,
    storage: Storage,
    settings: Settings,
) -> dict:
    return await decide_subject(session, actor, task_id, rubric_id, body, item_id=item_id)


@review_denials
async def decide_coverage(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    rubric_id: UUID,
    requirement_id: UUID,
    body: RubricCoverageDecisionRequest,
    storage: Storage,
    settings: Settings,
) -> dict:
    actor, row = await human_set(session, actor, task_id, rubric_id)
    entry = await session.scalar(
        select(ScoreRubricCoverage).where(
            ScoreRubricCoverage.rubric_id == row.id,
            ScoreRubricCoverage.requirement_id == requirement_id,
        )
    )
    if entry is None:
        raise not_found()
    human_role(actor, "bidder")
    await require_candidate(session, row)
    current = await coverage_view(session, entry)
    cas(body, row, current["revision"])
    for item_id in body.rubric_item_ids:
        item = await session.get(ScoreRubricItem, item_id)
        if item is None or item.rubric_id != row.id or item.requirement_id != requirement_id:
            cards.fail(
                "invalid_coverage_mapping",
                "Coverage must reference its own rubric requirement items",
                409,
            )
    if body.canonical_requirement_id is not None:
        if body.canonical_requirement_id == requirement_id or not await session.scalar(
            select(ScoreRubricCoverage.id).where(
                ScoreRubricCoverage.rubric_id == row.id,
                ScoreRubricCoverage.requirement_id == body.canonical_requirement_id,
            )
        ):
            cards.fail(
                "invalid_canonical_requirement",
                "Canonical requirement must be a different fixed scoring requirement",
                409,
            )
    event = ScoreRubricCoverageDecision(
        **await review_values(session, actor, row, body),
        coverage_id=entry.id,
        requirement_id=requirement_id,
        canonical_requirement_id=body.canonical_requirement_id,
        action=body.action,
    )
    session.add(event)
    await session.flush()
    for item_id in body.rubric_item_ids:
        session.add(
            ScoreRubricCoverageItem(
                id=uuid4(),
                org_id=actor.org_id,
                task_id=row.task_id,
                rubric_id=row.id,
                coverage_decision_id=event.id,
                rubric_item_id=item_id,
            )
        )
    await session.flush()
    review_audit(session, actor, row, event)
    return RubricCoverageDecisionView.model_validate(
        {
            **fields(
                event,
                (
                    "id",
                    "org_id",
                    "task_id",
                    "rubric_id",
                    "requirement_id",
                    "revision",
                    "action",
                    "canonical_requirement_id",
                    "reason",
                    "decided_by",
                    "decided_at",
                    "actor_kind",
                ),
            ),
            "rubric_item_ids": body.rubric_item_ids,
        }
    ).model_dump(mode="json")


@review_denials
async def decide_rubric(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    rubric_id: UUID,
    body: RubricSetDecisionRequest,
    storage: Storage,
    settings: Settings,
    *,
    console: bool = False,
) -> dict:
    actor, row = await human_set(session, actor, task_id, rubric_id)
    human_role(actor, "bidder")
    current = await review_state(session, row)
    cas(body, row, current["revision"])
    if (body.action == "confirm") != (current["state"] == "candidate"):
        cards.fail("invalid_transition", "Only candidate sets confirm; confirmed sets reopen", 409)
    if body.action == "confirm":
        report = await report_data(session, row)
        if not report["rubric"]["completeness"]["complete"]:
            cards.fail(
                "rubric_incomplete",
                "Resolve all coverage, normalization and human confirmation blockers",
                409,
            )
        from app.services.requirement_consumption import scoring_confirmed

        reviews = await scoring_confirmed(session, task_id, row.extraction_job_id)
        require_confirmed_section_sources(report, reviews)
    event = ScoreRubricDecision(
        **await review_values(session, actor, row, body),
        action=body.action,
        section_id=None,
        item_id=None,
    )
    session.add(event)
    await session.flush()
    review_audit(session, actor, row, event)
    if console:
        from app.services.assessment_rubrics import summary

        return (await summary(session, actor, task_id, row.id)).model_dump(mode="json")
    return (await report_data(session, row))["rubric"]


async def rubric_history(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    rubric_id: UUID,
    storage: Storage,
    settings: Settings,
    *,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[dict, list[dict]]:
    actor, row = await get_set(session, actor, task_id, rubric_id)
    if not 1 <= limit <= 200:
        cards.fail("invalid_input", "Limit must be between 1 and 200")
    anchor = cursor_read(settings, actor, task_id, rubric_id, cursor)
    event_types = {
        "decision": (ScoreRubricDecision, RubricDecisionView),
        "classification": (ScoreRubricClassification, RubricClassificationView),
        "coverage": (ScoreRubricCoverageDecision, RubricCoverageDecisionView),
        "revision": (ScoreRubricRevisionEvent, RubricRevisionView),
    }
    statements = []
    for kind, (model, _) in event_types.items():
        created = model.revised_at if model is ScoreRubricRevisionEvent else model.decided_at
        condition = (
            (
                (ScoreRubricRevisionEvent.rubric_id == row.id)
                | (ScoreRubricRevisionEvent.prior_rubric_id == row.id)
            )
            if model is ScoreRubricRevisionEvent
            else model.rubric_id == row.id
        )
        statements.append(
            select(
                model.id.label("id"), created.label("created_at"), literal(kind).label("kind")
            ).where(condition)
        )
    events = union_all(*statements).subquery()
    total = await session.scalar(select(func.count()).select_from(events)) or 0
    statement = select(events)
    if anchor is not None:
        statement = statement.where(
            tuple_(events.c.created_at, events.c.id)
            > tuple_(literal(anchor[0]), literal(anchor[1]))
        )
    positions = (
        await session.execute(statement.order_by(events.c.created_at, events.c.id).limit(limit + 1))
    ).all()
    items = []
    for position in positions[:limit]:
        model, view = event_types[position.kind]
        event = await session.get(model, position.id)
        if event is None:
            raise not_found()
        if model is ScoreRubricCoverageDecision:
            mapped = list(
                (
                    await session.scalars(
                        select(ScoreRubricCoverageItem.rubric_item_id).where(
                            ScoreRubricCoverageItem.coverage_decision_id == event.id
                        )
                    )
                ).all()
            )
            keys = (
                "id",
                "org_id",
                "task_id",
                "rubric_id",
                "requirement_id",
                "revision",
                "action",
                "canonical_requirement_id",
                "reason",
                "decided_by",
                "decided_at",
                "actor_kind",
            )
            value = RubricCoverageDecisionView.model_validate(
                {**fields(event, keys), "rubric_item_ids": mapped}
            )
        else:
            value = view.model_validate(event)
        items.append(value.model_dump(mode="json"))
    next_cursor = None
    if len(positions) > limit:
        last = positions[limit - 1]
        next_cursor = TokenSigner.for_tokens(settings).issue(
            {
                "kind": "rubric_cursor",
                "org_id": str(actor.org_id),
                "task_id": str(task_id),
                "rubric_id": str(rubric_id),
                "created_at": last.created_at.isoformat(),
                "id": str(last.id),
            },
            7 * 86400,
        )
    return AssessmentListData(task_id=task_id, total=total, next_cursor=next_cursor).model_dump(
        mode="json"
    ), items


@review_denials
async def revise_rubric(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    rubric_id: UUID,
    body: RubricReviseRequest,
    storage: Storage,
    settings: Settings,
    *,
    console: bool = False,
) -> dict:
    actor, prior = await human_set(session, actor, task_id, rubric_id)
    if actor.role not in {"bidder", "technical"}:
        cards.fail(
            "forbidden", "Only responsible bidder or technical sessions can revise rubrics", 403, 4
        )
    cas(body, prior, await set_revision(session, prior))
    current = await report_data(session, prior)
    own_domain = {"bidder": "commercial", "technical": "technical"}[actor.role]
    fixed_coverage = {UUID(entry["requirement_id"]): entry for entry in current["coverage"]}
    if {entry.requirement_id for entry in body.coverage} != set(fixed_coverage):
        cards.fail(
            "invalid_coverage", "Replacement must cover every fixed scoring requirement", 409
        )
    proposed_items = {entry.key: entry for entry in body.items}
    for coverage in body.coverage:
        if any(
            proposed_items[key].requirement_id != coverage.requirement_id
            for key in coverage.rubric_item_keys
        ):
            cards.fail(
                "invalid_coverage_mapping",
                "Proposed coverage must name its own requirement items",
                409,
            )
        if coverage.canonical_requirement_id is not None and (
            coverage.canonical_requirement_id == coverage.requirement_id
            or coverage.canonical_requirement_id not in fixed_coverage
        ):
            cards.fail(
                "invalid_canonical_requirement",
                "Proposed canonical must reference another fixed scoring requirement",
                409,
            )
    old_sections = {UUID(entry["id"]): entry for entry in current["sections"]}
    old_items = {UUID(entry["id"]): entry for entry in current["items"]}
    section_fields = (
        "key",
        "title",
        "order",
        "aggregation",
        "aggregation_rule_text",
        "score_range",
        "weight",
        "cap",
        "included_in_overall_total",
        "ambiguity_reason",
    )
    item_fields = (
        "key",
        "title",
        "rule_text",
        "order",
        "assessment_mode",
        "score_range",
        "weight",
        "ambiguity_reason",
    )
    for replacements, originals, keys, source_key in (
        (body.sections, old_sections, section_fields, "source_section_id"),
        (body.items, old_items, item_fields, "source_item_id"),
    ):
        seen = set()
        for entry in replacements:
            source_id = getattr(entry, source_key)
            requirements = (
                [citation.requirement_id for citation in entry.sources]
                if isinstance(entry, RubricSectionRevisionInput)
                else [entry.requirement_id]
            )
            if any(requirement_id not in fixed_coverage for requirement_id in requirements):
                cards.fail(
                    "invalid_requirement",
                    "Replacement may only use fixed scoring requirements",
                    409,
                )
            if source_id is not None:
                previous = originals.get(source_id)
                if previous is None or source_id in seen:
                    cards.fail(
                        "invalid_revision_source",
                        "Each prior rubric subject may be referenced once",
                        409,
                    )
                seen.add(source_id)
                if previous["review_domain"] != own_domain:
                    replacement = entry.model_dump(mode="json")
                    if any(replacement[key] != previous[key] for key in keys) or (
                        replacement["sources"]
                        != [
                            {
                                "requirement_id": citation["requirement_id"],
                                "quote": citation["quote"],
                            }
                            for citation in previous["sources"]
                        ]
                        if isinstance(entry, RubricSectionRevisionInput)
                        else previous["source"] != fixed_coverage[entry.requirement_id]["source"]
                    ):
                        cards.fail(
                            "forbidden",
                            "Other review domains must remain unchanged in a replacement",
                            403,
                            4,
                        )
                if source_key == "source_item_id":
                    prior_section = old_sections[UUID(previous["section_id"])]
                    if (
                        previous["review_domain"] != own_domain
                        and entry.model_dump(mode="json")["section_key"] != prior_section["key"]
                    ):
                        cards.fail(
                            "forbidden",
                            "Other review domains cannot be moved between sections",
                            403,
                            4,
                        )
        if any(
            previous["review_domain"] != own_domain and source_id not in seen
            for source_id, previous in originals.items()
        ):
            cards.fail("forbidden", "Replacement cannot remove other review domains", 403, 4)
    if actor.role != "bidder":
        for key in (
            "overall_aggregation",
            "overall_rule_text",
            "overall_score_range",
            "overall_cap",
        ):
            if body.model_dump(mode="json")[key] != current["rubric"][key]:
                cards.fail("forbidden", "Only bidder may revise the overall aggregation", 403, 4)
    verified_sources = {
        (citation["requirement_id"], citation["quote"]): citation
        for section in old_sections.values()
        for citation in section["sources"]
    }
    for requirement_id, coverage in fixed_coverage.items():
        source = coverage["source"]
        verified_sources[(str(requirement_id), source["quote"])] = {
            "requirement_id": str(requirement_id),
            "source": source,
            "quote": source["quote"],
        }
    selected_sources = {}
    for entry in body.sections:
        citations = []
        for citation in entry.sources:
            verified = verified_sources.get((str(citation.requirement_id), citation.quote))
            if verified is None:
                cards.fail(
                    "invalid_revision_source",
                    "Section citations must retain a verified quotation",
                    409,
                )
            citations.append(verified)
        selected_sources[entry.key] = citations
    version = (
        await session.scalar(
            select(func.max(ScoreRubricSet.version)).where(
                ScoreRubricSet.extraction_job_id == prior.extraction_job_id
            )
        )
        or 0
    ) + 1
    row = ScoreRubricSet(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=task_id,
        job_id=prior.job_id,
        run_id=prior.run_id,
        extraction_job_id=prior.extraction_job_id,
        document_id=prior.document_id,
        prior_rubric_id=prior.id,
        version=version,
        input_hash=prior.input_hash,
        input_manifest=prior.input_manifest,
        encrypted_input=prior.encrypted_input,
        normalization_rule_version=prior.normalization_rule_version,
        prompt_version=prior.prompt_version,
        schema_version=prior.schema_version,
        overall_aggregation=body.overall_aggregation,
        overall_rule_text=body.overall_rule_text,
        overall_score_range=body.overall_score_range.model_dump(mode="json")
        if body.overall_score_range
        else None,
        overall_cap=body.overall_cap,
        normalization_errors=[],
        actor_user_id=actor.user_id,
        actor_token_id=None,
        actor_kind="session",
    )
    session.add(row)
    await session.flush()
    section_ids = {}
    for entry in body.sections:
        payload = entry.model_dump(mode="json", exclude={"source_section_id"})
        payload["sources"] = selected_sources[entry.key]
        payload["requirement_id"] = UUID(payload["sources"][0]["requirement_id"])
        payload["weight"] = entry.weight
        payload["source"] = payload["sources"][0]["source"]
        payload["cap"] = entry.cap
        section = ScoreRubricSection(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task_id,
            rubric_id=row.id,
            **payload,
            fingerprint=score_normalization.content_fingerprint("section", payload),
            citation_valid=True,
        )
        section_ids[entry.key] = section.id
        session.add(section)
    await session.flush()
    fingerprints = set()
    for entry in body.items:
        payload = entry.model_dump(mode="json", exclude={"source_item_id", "section_key"})
        payload["requirement_id"] = entry.requirement_id
        payload["weight"] = entry.weight
        payload["source"] = fixed_coverage[entry.requirement_id]["source"]
        fingerprint = score_normalization.content_fingerprint("item", payload)
        if fingerprint in fingerprints:
            cards.fail(
                "duplicate_item_fingerprint", "Replacement contains duplicated scoring content", 409
            )
        fingerprints.add(fingerprint)
        session.add(
            ScoreRubricItem(
                id=uuid4(),
                org_id=actor.org_id,
                task_id=task_id,
                rubric_id=row.id,
                section_id=section_ids[entry.section_key],
                **payload,
                fingerprint=fingerprint,
                citation_valid=True,
            )
        )
    for requirement_id, entry in fixed_coverage.items():
        session.add(
            ScoreRubricCoverage(
                id=uuid4(),
                org_id=actor.org_id,
                task_id=task_id,
                rubric_id=row.id,
                requirement_id=requirement_id,
                source=entry["source"],
            )
        )
    replacement_snapshot = body.model_dump(mode="json")
    snapshot_sha256 = await session.scalar(
        text("SELECT encode(sha256(convert_to(CAST(:snapshot AS jsonb)::text,'UTF8')),'hex')"),
        {"snapshot": json.dumps(replacement_snapshot, ensure_ascii=False)},
    )
    event = ScoreRubricRevisionEvent(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=task_id,
        rubric_id=row.id,
        prior_rubric_id=prior.id,
        version=version,
        input_hash=row.input_hash,
        expected_revision=body.expected_revision,
        expected_input_hash=body.expected_input_hash,
        reason=body.reason,
        reason_sha256=cards.quote_hash(body.reason),
        replacement_snapshot=replacement_snapshot,
        snapshot_sha256=snapshot_sha256,
        revised_by=actor.user_id,
        actor_kind="session",
    )
    session.add(event)
    await session.flush()
    review_audit(session, actor, row, event, "revised")
    if console:
        from app.services.assessment_rubrics import summary

        return (await summary(session, actor, task_id, row.id)).model_dump(mode="json")
    return await report_data(session, row)
