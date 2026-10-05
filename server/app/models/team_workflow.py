"""Task authorization and durable, transaction-ordered invalidation metadata."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant


def task_fk():
    return ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"])


def member_fk(column):
    return ForeignKeyConstraint(["org_id", column], ["memberships.org_id", "memberships.user_id"])


class TaskWorkflow(Tenant, Base):
    __tablename__ = "task_workflows"
    task_id: Mapped[UUID] = mapped_column()
    owner_user_id: Mapped[UUID] = mapped_column()
    state: Mapped[str] = mapped_column(String(20), default="active")
    last_reason_ciphertext: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    access_epoch: Mapped[int] = mapped_column(Integer, default=1)
    co_sign_starred: Mapped[bool] = mapped_column(Boolean, default=False)
    rule_revision: Mapped[int] = mapped_column(Integer, default=1)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by_user_id: Mapped[UUID | None] = mapped_column()
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "task_id"),
        task_fk(),
        member_fk("owner_user_id"),
        member_fk("archived_by_user_id"),
        ForeignKeyConstraint(
            ["org_id", "task_id", "owner_user_id"],
            ["task_members.org_id", "task_members.task_id", "task_members.user_id"],
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
            name="task_workflow_owner_member",
        ),
    )


class TaskMember(Tenant, Base):
    __tablename__ = "task_members"
    task_id: Mapped[UUID] = mapped_column()
    user_id: Mapped[UUID] = mapped_column()
    role: Mapped[str] = mapped_column(String(20))
    review_domains: Mapped[list[str]] = mapped_column(JSONB, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    workflow_revision: Mapped[int] = mapped_column(Integer, default=1)
    changed_by_user_id: Mapped[UUID] = mapped_column()
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_reason_ciphertext: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "task_id", "user_id"),
        task_fk(),
        member_fk("user_id"),
        member_fk("changed_by_user_id"),
    )


class TaskEventHead(Tenant, Base):
    __tablename__ = "task_event_heads"
    task_id: Mapped[UUID] = mapped_column()
    last_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    retained_floor_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "task_id"),
        task_fk(),
    )


class TaskEvent(Tenant, Base):
    __tablename__ = "task_events"
    task_id: Mapped[UUID] = mapped_column()
    seq: Mapped[int] = mapped_column(BigInteger)
    event_kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_id: Mapped[UUID | None] = mapped_column()
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "task_id", "seq"),
        task_fk(),
    )
