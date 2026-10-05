"""Org-defined confidential fields and their append-only encrypted values."""

from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant
from app.models.response_cards import tenant_fk


class ConfidentialField(Tenant, Base):
    __tablename__ = "confidential_fields"
    created_by: Mapped[UUID] = mapped_column()
    key: Mapped[str] = mapped_column(String(48))
    label: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))
    scope: Mapped[str] = mapped_column(String(10))
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "key"),
        tenant_fk("created_by", "memberships", "user_id"),
        CheckConstraint("key ~ '^[a-z][a-z0-9_]{1,47}$'", name="confidential_field_key"),
        CheckConstraint("btrim(label) <> ''", name="confidential_field_label"),
        CheckConstraint(
            "kind IN ('amount', 'contact', 'identity', 'bank_account', 'other')",
            name="confidential_field_kind",
        ),
        CheckConstraint("scope IN ('org', 'task')", name="confidential_field_scope"),
        CheckConstraint("revision >= 1", name="confidential_field_revision"),
    )


class ConfidentialValue(Tenant, Base):
    __tablename__ = "confidential_values"
    created_by: Mapped[UUID] = mapped_column()
    field_id: Mapped[UUID] = mapped_column()
    # Null for an org-scope field's value; the task for a task-scope field's value.
    task_id: Mapped[UUID | None] = mapped_column()
    version: Mapped[int] = mapped_column(Integer)
    encrypted_value: Mapped[str] = mapped_column(Text)
    tail: Mapped[str | None] = mapped_column(String(4))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("field_id", "confidential_fields"),
        tenant_fk("task_id", "tasks"),
        tenant_fk("created_by", "memberships", "user_id"),
        CheckConstraint("version >= 1", name="confidential_value_version"),
        Index(
            "uq_confidential_values_org_version",
            "org_id",
            "field_id",
            "version",
            unique=True,
            postgresql_where=text("task_id IS NULL"),
        ),
        Index(
            "uq_confidential_values_task_version",
            "org_id",
            "field_id",
            "task_id",
            "version",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL"),
        ),
    )
