"""Append-only solution drafts and explicit human review."""

import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import Document
from app.models.solution import SolutionChapter, SolutionRevision
from app.schemas.solution import SolutionChapter as ChapterView
from app.schemas.solution import SolutionConfirm, SolutionEdit, SolutionList, SolutionSection
from app.schemas.solution import SolutionRevision as RevisionView
from app.services import task_access
from app.services.auth import Identity
from app.services.versioned import audit

PLACEHOLDER = re.compile(r"【待填[：:][^】]*】")


def view(chapter: SolutionChapter, revision: SolutionRevision) -> ChapterView:
    current = RevisionView.model_validate(
        {name: getattr(revision, name) for name in RevisionView.model_fields}
    )
    return ChapterView(
        id=chapter.id,
        task_id=chapter.task_id,
        document_id=chapter.document_id,
        title=chapter.title,
        score_range=chapter.score_range,
        current=current,
        placeholder_count=sum(
            len(PLACEHOLDER.findall(text))
            for section in current.sections
            for text in section.texts()
        ),
    )


async def current_chapters(session: AsyncSession, document_id: UUID) -> list[ChapterView]:
    rows = (
        await session.execute(
            select(SolutionChapter, SolutionRevision)
            .join(
                SolutionRevision,
                (SolutionRevision.chapter_id == SolutionChapter.id)
                & (SolutionRevision.revision == SolutionChapter.current_revision),
            )
            .where(SolutionChapter.document_id == document_id)
            .order_by(SolutionChapter.title)
        )
    ).all()
    return [view(chapter, revision) for chapter, revision in rows]


async def list_chapters(session, actor, task_id, document_id=None):
    await task_access.access(session, actor, task_id, "solution:read")
    query = (
        select(SolutionChapter, SolutionRevision)
        .join(
            SolutionRevision,
            (SolutionRevision.chapter_id == SolutionChapter.id)
            & (SolutionRevision.revision == SolutionChapter.current_revision),
        )
        .where(SolutionChapter.task_id == task_id)
        .order_by(SolutionChapter.title)
    )
    if document_id is not None:
        document = await session.get(Document, document_id)
        if document is None or document.task_id != task_id:
            raise not_found()
        query = query.where(SolutionChapter.document_id == document_id)
    rows = (await session.execute(query)).all()
    items = [view(chapter, revision).model_dump(mode="json") for chapter, revision in rows]
    return SolutionList(task_id=task_id, document_id=document_id, total=len(items)).model_dump(
        mode="json"
    ), items


async def load(session, actor, chapter_id, scope="solution:read", *, write=False):
    chapter = await session.get(SolutionChapter, chapter_id)
    if chapter is None:
        raise not_found()
    await task_access.access(session, actor, chapter.task_id, scope, write=write)
    # Reload under the task lock: another reviewer may have advanced the pointer.
    await session.refresh(chapter)
    revision = await session.scalar(
        select(SolutionRevision).where(
            SolutionRevision.chapter_id == chapter.id,
            SolutionRevision.revision == chapter.current_revision,
        )
    )
    if revision is None:
        raise not_found()
    return chapter, revision


async def show_chapter(session, actor, chapter_id):
    chapter, revision = await load(session, actor, chapter_id)
    return view(chapter, revision).model_dump(mode="json")


def expected(chapter, number):
    if chapter.current_revision != number:
        raise ServiceError("revision_conflict", "Read the current revision before writing", 409, 2)


async def append(session, actor, chapter, sections, *, origin, state="draft", job_id=None):
    # Stored dicts are kept verbatim: confirmation compares them with the draft, and
    # drafts written before subsections existed have no `subsections` key.
    stored = []
    for section in sections:
        parsed = SolutionSection.model_validate(section)
        stored.append(section if isinstance(section, dict) else parsed.model_dump(mode="json"))
    revision = SolutionRevision(
        chapter_id=chapter.id,
        revision=chapter.current_revision + 1,
        sections=stored,
        origin=origin,
        state=state,
        actor_kind=actor.actor_kind,
        actor_token_id=actor.token_id,
        agent_session_id=actor.session_id,
        job_id=job_id,
        confirmed_at=datetime.now(UTC) if state == "confirmed" else None,
    )
    session.add(revision)
    await session.flush()
    chapter.current_revision = revision.revision
    await session.flush()
    audit(
        session,
        actor,
        "solution.confirm" if state == "confirmed" else "solution." + origin,
        chapter.id,
        {"revision": revision.revision, "state": state},
    )
    return view(chapter, revision).model_dump(mode="json")


async def edit(session, actor, chapter_id, body: SolutionEdit):
    chapter, _ = await load(session, actor, chapter_id, "solution:edit", write=True)
    actor.require("solution:edit")
    expected(chapter, body.expected_revision)
    return await append(session, actor, chapter, body.sections, origin="human")


async def confirm(session, actor, chapter_id, body: SolutionConfirm):
    chapter, revision = await load(session, actor, chapter_id, "solution:confirm", write=True)
    actor.require("solution:confirm")
    expected(chapter, body.expected_revision)
    if revision.state != "draft":
        raise ServiceError("invalid_state", "Only a draft chapter can be confirmed", 409, 2)
    return await append(
        session, actor, chapter, revision.sections, origin="human", state="confirmed"
    )


async def publish_model(
    session,
    actor: Identity,
    task_id,
    document_id,
    title,
    score_range,
    sections,
    *,
    job_id,
    expected_revision=None,
):
    await task_access.access(
        session, actor, task_id, "solution:draft", write=True, bind_context=False
    )
    document = await session.get(Document, document_id)
    if document is None or document.task_id != task_id:
        raise not_found()
    chapter = await session.scalar(
        select(SolutionChapter).where(
            SolutionChapter.document_id == document_id,
            SolutionChapter.title == title,
            SolutionChapter.score_range == score_range,
        )
    )
    if chapter is None:
        if expected_revision not in (None, 0):
            raise ServiceError("revision_conflict", "The solution chapter changed", 409, 2)
        chapter = SolutionChapter(
            task_id=task_id,
            document_id=document_id,
            title=title,
            score_range=score_range,
            current_revision=0,
        )
        session.add(chapter)
        await session.flush()
    if expected_revision is not None:
        expected(chapter, expected_revision)
    return await append(session, actor, chapter, sections, origin="model", job_id=job_id)
