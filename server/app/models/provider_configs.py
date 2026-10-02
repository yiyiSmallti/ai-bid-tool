"""Append-only tenant configuration revisions; latest revision is the active choice."""

from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant


class ProviderConfig(Tenant, Base):
    __tablename__ = "provider_configs"
    capability: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(10))
    platform_model_id: Mapped[str | None] = mapped_column(ForeignKey("platform_models.id"))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    encrypted_key: Mapped[str | None] = mapped_column(Text)
    key_last4: Mapped[str | None] = mapped_column(String(4))
    updated_by: Mapped[UUID] = mapped_column()
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "capability", "revision"),
        ForeignKeyConstraint(
            ["org_id", "updated_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        CheckConstraint(
            "revision > 0 AND capability = 'llm_extract'", name="provider_revision_capability"
        ),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="provider_config_object"),
        CheckConstraint(
            "(source = 'org' AND platform_model_id IS NULL AND encrypted_key IS NOT NULL AND key_last4 IS NOT NULL) OR (source = 'platform' AND platform_model_id IS NOT NULL AND encrypted_key IS NULL AND key_last4 IS NULL)",
            name="provider_config_source",
        ),
    )
