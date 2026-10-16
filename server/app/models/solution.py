"""Append-only solution chapter revisions, scoped to a tender document."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class SolutionChapter(Base):
    __tablename__ = "solution_chapters"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    document_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(String(300))
    score_range: Mapped[str] = mapped_column(String(100))
    current_revision: Mapped[int] = mapped_column(default=0)
    __table_args__ = (
        ForeignKeyConstraint(["document_id", "task_id"], ["documents.id", "documents.task_id"]),
        UniqueConstraint("document_id", "title", "score_range"),
        CheckConstraint("current_revision >= 0"),
    )


class SolutionRevision(Base):
    __tablename__ = "solution_revisions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("solution_chapters.id"))
    revision: Mapped[int]
    state: Mapped[str] = mapped_column(String(20))
    origin: Mapped[str] = mapped_column(String(20))
    sections: Mapped[list[dict]] = mapped_column(JSONB)
    actor_kind: Mapped[str] = mapped_column(String(20))
    actor_token_id: Mapped[UUID | None] = mapped_column(ForeignKey("api_tokens.id"))
    agent_session_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_sessions.id"))
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("jobs.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("chapter_id", "revision"),
        CheckConstraint("revision > 0"),
        CheckConstraint("state IN ('draft','confirmed')"),
        CheckConstraint("origin IN ('human','model')"),
        CheckConstraint("actor_kind IN ('session','token','agent','worker')"),
        CheckConstraint("(state = 'confirmed') = (confirmed_at IS NOT NULL)"),
        CheckConstraint("jsonb_typeof(sections) = 'array' AND jsonb_array_length(sections) > 0"),
    )
