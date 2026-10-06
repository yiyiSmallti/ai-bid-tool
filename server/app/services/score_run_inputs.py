"""Fixed confirmed-draft inputs for scoring; material bodies never enter the profile."""

from dataclasses import dataclass
from datetime import date
from typing import NoReturn
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.core.errors import not_found
from app.models.entities import Chunk, Document, Job, Requirement, Task
from app.models.response_cards import (
    CardEvidenceLink,
    DraftRun,
    Evidence,
    ResponseCard,
    ResponseCardRevision,
    ResponseItem,
)
from app.models.score import ScoreRubricSection, ScoreRubricSet
from app.services import check_inputs, drafts, score, score_inputs
from app.services import response_cards as cards
from app.services.auth import Identity
from app.services.card_generation import check_input_access
from app.services.score_semantic import SCORING_RULE_VERSION as RULE_VERSION

LIMITATIONS = [
    "confirmed_draft_only",
    "advisory_score_not_official_award",
    "coverage_relative_to_saved_scoring_requirements",
    "released_documents_not_read",
    "document_layout_signatures_and_attachments_not_checked",
    "evidence_bodies_and_page_images_not_read",
]


@dataclass
class ScoreSnapshot:
    task: Task
    draft: DraftRun
    extraction: Job
    document: Document
    rubric: ScoreRubricSet
    manifest: dict
    secret: dict
    input_hash: str
    limitations: list[str]


async def access(session: AsyncSession, actor: Identity, scope: str) -> Identity:
    return await check_inputs.access(session, actor, scope)


def integrity() -> NoReturn:
    cards.fail("score_input_integrity", "Fixed scoring input relationships are invalid", 409, 4)


def stale() -> NoReturn:
    cards.fail("score_stale_draft", "Choose a current draft; assemble changed inputs again", 409)


async def lock_inputs(session, actor, task_id, extraction_id):
    session.expire_all()
    await cards.task_lock(session, task_id)
    await session.scalars(
        select(ResponseCard.id)
        .where(ResponseCard.task_id == task_id)
        .order_by(ResponseCard.id)
        .with_for_update(read=True)
    )
    await score_inputs.lock_inputs(session, actor, task_id, extraction_id)


async def fixed_rows(session, actor, draft, *, require_current: bool):
    """Read fixed revisions only; the current revision number is a currency fence.

    No current-revision pointer is dereferenced and no candidate response text is
    selected. Non-response rows load only their fixed disposition metadata.
    """
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
                        Requirement.job_id,
                        Requirement.chunk_id,
                        Requirement.page,
                        Requirement.location,
                        Requirement.quote,
                        Requirement.text,
                        Requirement.category,
                        Requirement.starred,
                    )
                )
                .join(Chunk, Chunk.id == Requirement.chunk_id)
                .where(
                    Requirement.job_id == draft.extraction_job_id,
                    Requirement.task_id == draft.task_id,
                )
                .order_by(Chunk.seq, Requirement.id)
            )
        ).all()
    )
    rows = list(
        (await session.scalars(select(ResponseItem).where(ResponseItem.draft_id == draft.id))).all()
    )
    fixed = {entry["requirement_id"]: entry for entry in draft.input_manifest["requirements"]}
    if (
        drafts.digest(draft.input_manifest) != draft.input_hash
        or len(rows) != len(located)
        or len(fixed) != len(rows)
        or {str(row.requirement_id) for row in rows} != set(fixed)
        or {str(req.id) for req, _ in located} != set(fixed)
    ):
        integrity()
    if len(rows) > score_inputs.MAX_SCORING_REQUIREMENTS:
        cards.fail("score_requirement_limit", "A score supports at most 2000 requirements")
    by_requirement = {row.requirement_id: row for row in rows}
    current = (
        {
            entry.requirement_id: entry
            for entry in (
                await session.execute(
                    select(
                        ResponseCard.id, ResponseCard.requirement_id, ResponseCard.revision
                    ).where(
                        ResponseCard.task_id == draft.task_id,
                        ResponseCard.extraction_job_id == draft.extraction_job_id,
                    )
                )
            ).all()
        }
        if require_current
        else {}
    )
    secret_items, manifest_items = [], []
    citations = cards.citation_validity_batch(
        (requirement for requirement, _ in located), {chunk.id: chunk for _, chunk in located}
    )
    for requirement, chunk in located:
        row = by_requirement[requirement.id]
        entry = fixed[str(requirement.id)]
        if (
            requirement.document_id != chunk.document_id
            or chunk.task_id != draft.task_id
            or row.source != cards.source(requirement)
            or row.category != requirement.category
            or row.starred != requirement.starred
            or row.kind != entry["kind"]
            or (str(row.card_revision_id) if row.card_revision_id else None)
            != entry["card_revision_id"]
        ):
            integrity()
        if not citations[requirement.id]:
            cards.fail("invalid_input_citation", "Requirement source cannot be verified", 409, 4)
        revision = None
        if row.card_revision_id:
            projection = [
                ResponseCardRevision.id,
                ResponseCardRevision.card_id,
                ResponseCardRevision.revision,
                ResponseCardRevision.state,
                ResponseCardRevision.review_domain,
                ResponseCardRevision.disposition,
                ResponseCardRevision.disposition_by,
                ResponseCardRevision.disposition_at,
                ResponseCardRevision.quote_sha256,
                ResponseCardRevision.confirmed_by,
                ResponseCardRevision.confirmed_at,
                ResponseCardRevision.model_job_id,
            ]
            if row.kind == "row":
                projection.extend(
                    getattr(ResponseCardRevision, key) for key in cards.CONTENT_FIELDS
                )
            revision = await session.scalar(
                select(ResponseCardRevision)
                .options(load_only(*projection))
                .where(ResponseCardRevision.id == row.card_revision_id)
            )
            if revision is None or revision.card_id != row.card_id:
                raise not_found()
        if require_current:
            head = current.get(requirement.id)
            if (row.card_id is None and head is not None) or (
                row.card_id is not None
                and (
                    head is None
                    or head.id != row.card_id
                    or revision is None
                    or head.revision != revision.revision
                )
            ):
                stale()
            if entry["source_hash"] != drafts.digest(cards.source(requirement)):
                stale()
        dependencies = []
        if revision is not None:
            for evidence in await cards.linked_evidence(session, revision.id):
                view = await cards.evidence_view(session, actor, evidence)
                dependencies.append(drafts.evidence_dependency(view))
                if row.kind == "row" and (
                    evidence.confirmed_by is None or evidence.confirmed_at is None
                ):
                    integrity()
            if require_current and dependencies != entry["evidence"]:
                stale()
            if revision.model_job_id is not None:
                generated = await session.get(Job, revision.model_job_id)
                if generated is None:
                    raise not_found()
                await check_input_access(
                    session, actor, draft.task_id, generated.result["submission"]["input_manifest"]
                )
        item = {
            "requirement_id": str(requirement.id),
            "response_item_id": str(row.id),
            "card_revision_id": str(row.card_revision_id) if row.card_revision_id else None,
            "partition": "response" if row.kind == "row" else row.kind,
            "source": cards.source(requirement),
            "tender_original": score_inputs.source_original(requirement, chunk),
            "category": requirement.category,
            "starred": requirement.starred,
            "gap_reasons": row.gap_reasons or [],
            "disposition": revision.disposition if revision else None,
        }
        if row.kind == "row":
            if (
                revision is None
                or revision.state != "confirmed"
                or revision.confirmed_by is None
                or revision.confirmed_at is None
                or revision.disposition != "respond"
                or any(getattr(row, key) != getattr(revision, key) for key in cards.CONTENT_FIELDS)
            ):
                integrity()
            item |= {key: getattr(row, key) for key in cards.CONTENT_FIELDS}
        secret_items.append(item)
        manifest_items.append(
            {
                "requirement_id": str(requirement.id),
                "document_id": str(requirement.document_id),
                "chunk_id": str(chunk.id),
                "source_sha256": drafts.digest(item["source"]),
                "source_original_sha256": drafts.digest({"text": item["tender_original"]}),
                "response_item_id": str(row.id),
                "card_id": str(row.card_id) if row.card_id else None,
                "card_revision_id": item["card_revision_id"],
                "partition": item["partition"],
                "content_sha256": drafts.digest(item),
                "evidence": dependencies,
            }
        )
    return secret_items, manifest_items


async def snapshot(
    session,
    actor,
    task_id,
    draft_id,
    rubric_id,
    assessment_date: date,
    *,
    defer_database_validation=False,
):
    task = await session.get(Task, task_id)
    draft = await session.get(DraftRun, draft_id)
    if task is None or draft is None or draft.task_id != task_id:
        raise not_found()
    _, rubric = await score.get_set(session, actor, task_id, rubric_id)
    rubric_data = await score.report_data(session, rubric)
    if (
        rubric_data["rubric"]["state"] != "confirmed"
        or not rubric_data["rubric"]["completeness"]["complete"]
    ):
        cards.fail("score_rubric_unconfirmed", "Choose a complete, confirmed rubric set", 409)
    extraction = await session.get(Job, draft.extraction_job_id)
    if extraction is None or extraction.task_id != task_id or extraction.document_id is None:
        raise not_found()
    if extraction.id != rubric.extraction_job_id or extraction.document_id != rubric.document_id:
        cards.fail(
            "score_input_mismatch",
            "Draft and rubric must use the same extraction and document",
            409,
        )
    if extraction.status != "succeeded":
        cards.fail("invalid_extraction_job", "Choose a succeeded extraction job")
    document = await session.get(Document, extraction.document_id)
    if document is None or document.task_id != task_id:
        raise not_found()
    rubric_fixed = await score_inputs.snapshot(session, actor, task_id, extraction.id)
    items, fixed_items = await fixed_rows(session, actor, draft, require_current=True)
    if any(entry["document_id"] != str(document.id) for entry in fixed_items):
        integrity()
    # Previews and paid admissions need the full database predicate immediately.
    # Publication rechecks it in the mandatory deferred gate, including live
    # citations, so that transaction must not locate every quote twice.
    if not defer_database_validation and not await session.scalar(
        text("SELECT score_inputs_current(:org, :draft, :rubric, :revision)"),
        {
            "org": actor.org_id,
            "draft": draft.id,
            "rubric": rubric.id,
            "revision": rubric_data["rubric"]["revision"],
        },
    ):
        stale()

    manifest = {
        **rubric_fixed.manifest,
        "scope": "confirmed_draft",
        "items": fixed_items,
        "draft_id": str(draft.id),
        "draft_input_hash": draft.input_hash,
        "assessment_date": assessment_date.isoformat(),
        "rubric_id": str(rubric.id),
        "rubric_version": rubric.version,
        "rubric_input_hash": rubric.input_hash,
        "rubric_revision": rubric_data["rubric"]["revision"],
        "rubric_sha256": drafts.digest(rubric_data),
        "scoring_rule_version": RULE_VERSION,
    }
    secret = {
        "draft_id": str(draft.id),
        "assessment_date": assessment_date.isoformat(),
        "items": items,
        "rubric": rubric_data,
    }
    return ScoreSnapshot(
        task,
        draft,
        extraction,
        document,
        rubric,
        manifest,
        secret,
        drafts.digest(manifest),
        [*LIMITATIONS, *manifest["warnings"]],
    )


async def require_dependencies(session, actor, task_id, manifest):
    """Authorize historical parents without treating content drift as missing access."""
    task = await session.get(Task, task_id)
    extraction = await session.get(Job, UUID(manifest["extraction_job_id"]))
    document = await session.get(Document, UUID(manifest["document_id"]))
    draft = await session.get(DraftRun, UUID(manifest["draft_id"]))
    rubric = await session.get(ScoreRubricSet, UUID(manifest["rubric_id"]))
    if (
        task is None
        or extraction is None
        or document is None
        or draft is None
        or rubric is None
        or manifest["org_id"] != str(actor.org_id)
        or any(
            parent.org_id != actor.org_id for parent in (task, extraction, document, draft, rubric)
        )
        or any(parent.task_id != task_id for parent in (extraction, document, draft, rubric))
        or extraction.document_id != document.id
        or rubric.document_id != document.id
        or draft.extraction_job_id != extraction.id
        or rubric.extraction_job_id != extraction.id
    ):
        raise not_found()
    if manifest.get("confidential"):
        actor.require("confidential:read")
    entries = manifest["items"]
    # Historical reads authorize every saved section source independently of the
    # freshness snapshot, which may stop before visiting the rubric's citations.
    sections = await session.scalars(
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
            ScoreRubricSection.org_id == actor.org_id,
            ScoreRubricSection.task_id == task_id,
            ScoreRubricSection.rubric_id == rubric.id,
        )
    )
    section_sources = [
        citation for section in sections for citation in score.section_sources(section)
    ]
    requirement_ids = {UUID(entry["requirement_id"]) for entry in entries} | {
        UUID(citation["requirement_id"]) for citation in section_sources
    }
    chunk_ids = {UUID(entry["chunk_id"]) for entry in entries} | {
        UUID(citation["source"]["chunk_id"]) for citation in section_sources
    }
    requirements = {
        row.id: row
        for row in (
            await session.execute(
                select(
                    Requirement.id,
                    Requirement.org_id,
                    Requirement.task_id,
                    Requirement.document_id,
                    Requirement.chunk_id,
                    Requirement.job_id,
                ).where(Requirement.id.in_(requirement_ids))
            )
        ).all()
    }
    chunks = {
        row.id: row
        for row in (
            await session.execute(
                select(Chunk.id, Chunk.org_id, Chunk.task_id, Chunk.document_id).where(
                    Chunk.id.in_(chunk_ids)
                )
            )
        ).all()
    }
    pinned_sources = {
        entry["requirement_id"]: entry for entry in rubric.input_manifest["requirements"]
    }
    for citation in section_sources:
        requirement = requirements.get(UUID(citation["requirement_id"]))
        saved_source = citation["source"]
        chunk = chunks.get(UUID(saved_source["chunk_id"]))
        pinned = pinned_sources.get(citation["requirement_id"])
        if (
            requirement is None
            or chunk is None
            or pinned is None
            or requirement.org_id != actor.org_id
            or chunk.org_id != actor.org_id
            or requirement.task_id != task_id
            or chunk.task_id != task_id
            or requirement.document_id != document.id
            or chunk.document_id != document.id
            or requirement.job_id != extraction.id
            or requirement.chunk_id != chunk.id
            or saved_source["document_id"] != str(document.id)
            or pinned["chunk_id"] != str(chunk.id)
        ):
            raise not_found()
    responses = {
        row.id: row
        for row in (
            await session.execute(
                select(
                    ResponseItem.id,
                    ResponseItem.draft_id,
                    ResponseItem.requirement_id,
                    ResponseItem.card_id,
                    ResponseItem.card_revision_id,
                ).where(ResponseItem.id.in_([UUID(entry["response_item_id"]) for entry in entries]))
            )
        ).all()
    }
    card_ids = {row.card_id for row in responses.values() if row.card_id is not None}
    revision_ids = {
        row.card_revision_id for row in responses.values() if row.card_revision_id is not None
    }
    card_rows = {
        row.id: row
        for row in (
            await session.execute(
                select(
                    ResponseCard.id,
                    ResponseCard.task_id,
                    ResponseCard.requirement_id,
                    ResponseCard.extraction_job_id,
                ).where(ResponseCard.id.in_(card_ids))
            )
        ).all()
    }
    revisions = {
        row.id: row
        for row in (
            await session.execute(
                select(
                    ResponseCardRevision.id,
                    ResponseCardRevision.card_id,
                    ResponseCardRevision.model_job_id,
                ).where(ResponseCardRevision.id.in_(revision_ids))
            )
        ).all()
    }
    linked: dict[UUID, list[Evidence]] = {}
    for evidence, revision_id in await session.execute(
        select(Evidence, CardEvidenceLink.revision_id)
        .join(CardEvidenceLink, CardEvidenceLink.evidence_id == Evidence.id)
        .where(CardEvidenceLink.revision_id.in_(revision_ids))
    ):
        linked.setdefault(revision_id, []).append(evidence)
    generated_ids = {
        revision.model_job_id
        for revision in revisions.values()
        if revision.model_job_id is not None
    }
    generated_jobs = {
        job.id: job for job in await session.scalars(select(Job).where(Job.id.in_(generated_ids)))
    }
    authorized_generations: set[UUID] = set()
    for entry in entries:
        requirement = requirements.get(UUID(entry["requirement_id"]))
        chunk = chunks.get(UUID(entry["chunk_id"]))
        row = responses.get(UUID(entry["response_item_id"]))
        if (
            requirement is None
            or chunk is None
            or row is None
            or requirement.task_id != task_id
            or requirement.document_id != document.id
            or requirement.job_id != extraction.id
            or requirement.chunk_id != chunk.id
            or chunk.task_id != task_id
            or chunk.document_id != document.id
            or row.draft_id != draft.id
            or row.requirement_id != requirement.id
            or (str(row.card_id) if row.card_id else None) != entry["card_id"]
            or (str(row.card_revision_id) if row.card_revision_id else None)
            != entry["card_revision_id"]
        ):
            raise not_found()
        if row.card_id is None:
            continue
        card = card_rows.get(row.card_id)
        revision = revisions.get(row.card_revision_id)
        if (
            card is None
            or revision is None
            or card.task_id != task_id
            or card.requirement_id != requirement.id
            or card.extraction_job_id != extraction.id
            or revision.card_id != card.id
        ):
            raise not_found()
        for evidence in linked.get(revision.id, []):
            if evidence.task_id != task_id or evidence.card_id != card.id:
                raise not_found()
            await cards.evidence_view(session, actor, evidence)
        if (
            revision.model_job_id is not None
            and revision.model_job_id not in authorized_generations
        ):
            generated = generated_jobs.get(revision.model_job_id)
            if generated is None or generated.task_id != task_id:
                raise not_found()
            await check_input_access(
                session, actor, task_id, generated.result["submission"]["input_manifest"]
            )
            authorized_generations.add(revision.model_job_id)
