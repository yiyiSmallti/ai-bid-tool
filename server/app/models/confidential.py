from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    column,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class ConfidentialFieldSearchToken(Base):
    "Derived key/label lexemes, never values; maintained only by field triggers."

    __tablename__ = "confidential_field_search_tokens"
    token: Mapped[str] = mapped_column(Text(collation="C"), nullable=False, primary_key=True)
    field_id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True)
    __table_args__ = (
        PrimaryKeyConstraint("token", "field_id", name="management_confidential_token_prefix"),
        ForeignKeyConstraint(
            ["field_id"],
            ["confidential_fields.id"],
            name="management_confidential_token_field",
            ondelete="CASCADE",
        ),
        Index("management_confidential_token_owner", "field_id", "token"),
    )


class ConfidentialField(Base):
    __tablename__ = "confidential_fields"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    key: Mapped[str] = mapped_column(String(48), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=sql_text("1")
    )
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "public.management_confidential_vector((key)::text, (label)::text)", persisted=True
        ),
        nullable=True,
    )
    __table_args__ = (
        CheckConstraint(
            "(((key)::text ~ '^[a-z][a-z0-9_]{1,47}$'::text))", name="confidential_field_key"
        ),
        CheckConstraint(
            "(((kind)::text = ANY ((ARRAY['amount'::character varying, 'contact'::character varying, 'identity'::character varying, 'bank_account'::character varying, 'other'::character varying])::text[])))",
            name="confidential_field_kind",
        ),
        CheckConstraint("((btrim((label)::text) <> ''::text))", name="confidential_field_label"),
        CheckConstraint("((revision >= 1))", name="confidential_field_revision"),
        CheckConstraint(
            "(((scope)::text = ANY ((ARRAY['library'::character varying, 'task'::character varying])::text[])))",
            name="confidential_field_scope",
        ),
        UniqueConstraint("key", name="confidential_fields_key_key"),
        PrimaryKeyConstraint("id", name="confidential_fields_pkey"),
        Index("management_confidential_fields_active_browse", "archived", "key", "id"),
        Index("management_confidential_fields_browse", "key", "id"),
        Index("management_confidential_fields_org_browse", "scope", "archived", "key", "id"),
    )


class ConfidentialValue(Base):
    __tablename__ = "confidential_values"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    field_id: Mapped[UUID] = mapped_column(nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    tail: Mapped[str | None] = mapped_column(String(4), nullable=True)
    __table_args__ = (
        CheckConstraint("((version >= 1))", name="confidential_value_version"),
        PrimaryKeyConstraint("id", name="confidential_values_pkey"),
        ForeignKeyConstraint(
            ["field_id"], ["confidential_fields.id"], name="confidential_values_field_id_fkey"
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="confidential_values_task_id_fkey"),
        Index(
            "management_confidential_org_history",
            "field_id",
            column("version").desc(),
            column("id").desc(),
            postgresql_include=["created_at", "tail"],
            postgresql_where=sql_text("(task_id IS NULL)"),
        ),
        Index(
            "management_confidential_task_history",
            "field_id",
            "task_id",
            column("version").desc(),
            column("id").desc(),
            postgresql_include=["created_at", "tail"],
            postgresql_where=sql_text("(task_id IS NOT NULL)"),
        ),
        Index(
            "uq_confidential_values_org_version",
            "field_id",
            "version",
            unique=True,
            postgresql_where=sql_text("(task_id IS NULL)"),
        ),
        Index(
            "uq_confidential_values_task_version",
            "field_id",
            "task_id",
            "version",
            unique=True,
            postgresql_where=sql_text("(task_id IS NOT NULL)"),
        ),
    )
