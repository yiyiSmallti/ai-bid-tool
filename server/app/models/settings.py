from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class PlatformCredential(Base):
    __tablename__ = "platform_credentials"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(300), nullable=False)
    encrypted_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    envelope_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("1")
    )
    fingerprint: Mapped[str] = mapped_column(String(23), nullable=False)
    last_four: Mapped[str] = mapped_column(String(4), nullable=False)
    state: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=sql_text("'disabled'::character varying")
    )
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sql_text("1"))
    secret_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=sql_text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((((state)::text = 'removed'::text) AND (encrypted_key IS NULL)) OR (((state)::text <> 'removed'::text) AND (encrypted_key IS NOT NULL) AND ((length(encrypted_key) >= 100) AND (length(encrypted_key) <= 16384)))))",
            name="credential_cipher_state",
        ),
        CheckConstraint(
            "(((((purpose)::text = 'vendor_search'::text) AND ((provider)::text = 'perplexity'::text) AND ((endpoint)::text = 'https://api.perplexity.ai'::text)) OR (((purpose)::text = ANY ((ARRAY['standalone_llm'::character varying])::text[])) AND ((provider)::text = ANY ((ARRAY['anthropic'::character varying, 'openai'::character varying])::text[]))) OR (((purpose)::text = 'clef_workers_ai'::text) AND ((provider)::text = 'cloudflare'::text) AND ((endpoint)::text = 'https://gateway.ai.cloudflare.com'::text)) OR (((purpose)::text = 'clef_gateway'::text) AND ((provider)::text = 'cloudflare'::text) AND ((endpoint)::text = 'https://api.cloudflare.com/client/v4'::text))))",
            name="credential_provider_purpose",
        ),
        CheckConstraint(
            "(((secret_version >= 1) AND (secret_version <= revision)))",
            name="platform_credentials_check",
        ),
        CheckConstraint(
            "((((endpoint)::text ~ '^https://(\\[[0-9a-f:]+\\]|[a-z0-9][a-z0-9.-]*)(/[^[:space:]?#%\\\\]*)?$'::text) AND ((endpoint)::text !~ '/$'::text) AND ((endpoint)::text !~ '[[:cntrl:]]'::text)))",
            name="platform_credentials_endpoint_check",
        ),
        CheckConstraint(
            "((envelope_version = 1))", name="platform_credentials_envelope_version_check"
        ),
        CheckConstraint(
            "(((fingerprint)::text ~ '^sha256:[a-f0-9]{16}$'::text))",
            name="platform_credentials_fingerprint_check",
        ),
        CheckConstraint(
            "(((last_four)::text ~ '^[!-~]{4}$'::text))",
            name="platform_credentials_last_four_check",
        ),
        CheckConstraint(
            "(((name)::text ~ '^[a-z0-9_]{1,40}$'::text))", name="platform_credentials_name_check"
        ),
        CheckConstraint(
            "(((provider)::text = ANY ((ARRAY['anthropic'::character varying, 'openai'::character varying, 'perplexity'::character varying, 'cloudflare'::character varying])::text[])))",
            name="platform_credentials_provider_check",
        ),
        CheckConstraint(
            "(((purpose)::text = ANY ((ARRAY['standalone_llm'::character varying, 'vendor_search'::character varying, 'clef_workers_ai'::character varying, 'clef_gateway'::character varying])::text[])))",
            name="platform_credentials_purpose_check",
        ),
        CheckConstraint("((revision >= 1))", name="platform_credentials_revision_check"),
        CheckConstraint(
            "(((state)::text = ANY ((ARRAY['active'::character varying, 'disabled'::character varying, 'removed'::character varying])::text[])))",
            name="platform_credentials_state_check",
        ),
        UniqueConstraint("name", name="platform_credentials_name_key"),
        PrimaryKeyConstraint("id", name="platform_credentials_pkey"),
        Index(
            "platform_credentials_one_service",
            "purpose",
            unique=True,
            postgresql_where=sql_text("((state)::text <> 'removed'::text)"),
        ),
    )


class ClefSettings(Base):
    __tablename__ = "clef_settings"
    id: Mapped[bool] = mapped_column(
        Boolean, nullable=False, primary_key=True, server_default=sql_text("true")
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint("(id)", name="clef_settings_singleton"),
        CheckConstraint(
            "(((jsonb_typeof(settings) = 'object'::text) AND (revision >= 1) AND ((settings ->> 'revision'::text) = (revision)::text)))",
            name="clef_settings_shape",
        ),
        PrimaryKeyConstraint("id", name="clef_settings_pkey"),
    )


class CredentialProbe(Base):
    __tablename__ = "credential_probes"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    credential_id: Mapped[UUID] = mapped_column(nullable=False)
    tested_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    secret_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("clock_timestamp()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    __table_args__ = (
        CheckConstraint(
            "((((finished_at IS NULL) AND (outcome IS NULL)) OR ((finished_at IS NOT NULL) AND ((outcome)::text = ANY ((ARRAY['passed'::character varying, 'auth_failed'::character varying, 'unsupported'::character varying, 'timeout'::character varying, 'rate_limited'::character varying, 'unavailable'::character varying, 'interrupted'::character varying])::text[])))))",
            name="credential_probes_outcome",
        ),
        PrimaryKeyConstraint("id", name="credential_probes_pkey"),
        ForeignKeyConstraint(
            ["credential_id"],
            ["platform_credentials.id"],
            name="credential_probes_credential_id_fkey",
        ),
        Index("credential_probes_started", "started_at"),
        Index("credential_probes_credential_started", "credential_id", "started_at"),
    )
