from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
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
    )
