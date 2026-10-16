from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base

LEGACY_SLOT = UUID("00000000-0000-4000-8000-000000000001")


class ProviderConfig(Base):
    __tablename__ = "provider_configs"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    capability: Mapped[str] = mapped_column(String(40), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    encrypted_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    # Rows saved before several models existed belong to the legacy slot.
    slot: Mapped[UUID] = mapped_column(
        nullable=False,
        default=lambda: LEGACY_SLOT,
        server_default=sql_text(f"'{LEGACY_SLOT}'::uuid"),
    )
    __table_args__ = (
        CheckConstraint("((jsonb_typeof(data) = 'object'::text))", name="provider_config_object"),
        CheckConstraint(
            "(((encrypted_key IS NOT NULL) AND (length(encrypted_key) > 0) AND (key_last4 IS NOT NULL) AND (length((key_last4)::text) = 4) AND ((data ->> 'provider'::text) = ANY (ARRAY['anthropic'::text, 'openai'::text])) AND ((length((data ->> 'model'::text)) >= 1) AND (length((data ->> 'model'::text)) <= 100)) AND ((data ->> 'json_mode'::text) = ANY (ARRAY['json_schema'::text, 'json_object'::text]))))",
            name="provider_config_source",
        ),
        CheckConstraint(
            "(((revision > 0) AND ((capability)::text = 'llm_extract'::text)))",
            name="provider_revision_capability",
        ),
        UniqueConstraint("capability", "revision", name="provider_configs_capability_revision_key"),
        PrimaryKeyConstraint("id", name="provider_configs_pkey"),
        Index(
            "management_provider_history",
            "capability",
            column("revision").desc(),
            column("id").desc(),
        ),
        Index("provider_config_slot", "capability", "slot", column("revision").desc()),
    )


class ProviderSlot(Base):
    """One saved model: whether it is the default and whether it was removed."""

    __tablename__ = "provider_slots"
    slot: Mapped[UUID] = mapped_column(nullable=False, primary_key=True)
    capability: Mapped[str] = mapped_column(
        String(40), nullable=False, default="llm_extract", server_default="llm_extract"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Null is a company model; a member is the owner of a personal model.
    owner_member_id: Mapped[UUID | None] = mapped_column(nullable=True)
    __table_args__ = (
        CheckConstraint(
            "((capability)::text = 'llm_extract'::text)", name="provider_slot_capability"
        ),
        CheckConstraint(
            "((NOT is_default) OR (removed_at IS NULL))", name="provider_slot_default_live"
        ),
        CheckConstraint(
            "((NOT is_default) OR (owner_member_id IS NULL))", name="provider_slot_default_company"
        ),
        Index(
            "provider_slot_default",
            "capability",
            unique=True,
            postgresql_where=sql_text("is_default"),
        ),
    )
