from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    column,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class ResourceLifecycleEvent(Base):
    __tablename__ = "resource_lifecycle_events"
    id: Mapped[UUID] = mapped_column(
        nullable=False,
        primary_key=True,
        default=uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    product_id: Mapped[UUID | None] = mapped_column(nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    resource_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    before_state: Mapped[str] = mapped_column(String(8), nullable=False)
    after_state: Mapped[str] = mapped_column(String(8), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_kind: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="session",
        server_default=sql_text("'session'::character varying"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("clock_timestamp()")
    )
    feature_id: Mapped[UUID | None] = mapped_column(nullable=True)
    certificate_id: Mapped[UUID | None] = mapped_column(nullable=True)
    profile_id: Mapped[UUID | None] = mapped_column(nullable=True)
    template_id: Mapped[UUID | None] = mapped_column(nullable=True)
    __table_args__ = (
        CheckConstraint(
            "((num_nonnulls(product_id, feature_id, certificate_id, profile_id, template_id) = 1))",
            name="lifecycle_one_root",
        ),
        CheckConstraint(
            "(((actor_kind)::text = 'session'::text))",
            name="resource_lifecycle_events_actor_kind_check",
        ),
        CheckConstraint(
            "(((after_state)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])))",
            name="resource_lifecycle_events_after_state_check",
        ),
        CheckConstraint(
            "(((before_state)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])))",
            name="resource_lifecycle_events_before_state_check",
        ),
        CheckConstraint(
            "(((before_state)::text <> (after_state)::text))",
            name="resource_lifecycle_events_check",
        ),
        CheckConstraint(
            "((((after_state)::text = 'active'::text) = ((reason_code)::text = 'restored'::text)))",
            name="resource_lifecycle_events_check1",
        ),
        CheckConstraint(
            "(((reason_code)::text = ANY ((ARRAY['obsolete'::character varying, 'duplicate'::character varying, 'unavailable'::character varying, 'restored'::character varying, 'other'::character varying])::text[])))",
            name="resource_lifecycle_events_reason_code_check",
        ),
        CheckConstraint(
            "((resource_revision >= 1))", name="resource_lifecycle_events_resource_revision_check"
        ),
        CheckConstraint("((revision >= 1))", name="resource_lifecycle_events_revision_check"),
        PrimaryKeyConstraint("id", name="resource_lifecycle_events_pkey"),
        ForeignKeyConstraint(
            ["certificate_id", "resource_revision"],
            ["certificate_revisions.certificate_id", "certificate_revisions.revision"],
            name="lifecycle_certificate_revision",
        ),
        ForeignKeyConstraint(
            ["certificate_id"], ["certificates.id"], name="lifecycle_certificate_root"
        ),
        ForeignKeyConstraint(
            ["feature_id", "resource_revision"],
            ["feature_revisions.feature_id", "feature_revisions.revision"],
            name="lifecycle_feature_revision",
        ),
        ForeignKeyConstraint(["feature_id"], ["features.id"], name="lifecycle_feature_root"),
        ForeignKeyConstraint(
            ["profile_id", "resource_revision"],
            ["org_profile_revisions.profile_id", "org_profile_revisions.revision"],
            name="lifecycle_profile_revision",
        ),
        ForeignKeyConstraint(["profile_id"], ["org_profiles.id"], name="lifecycle_profile_root"),
        ForeignKeyConstraint(
            ["template_id", "resource_revision"],
            ["template_revisions.template_id", "template_revisions.revision"],
            name="lifecycle_template_revision",
        ),
        ForeignKeyConstraint(["template_id"], ["templates.id"], name="lifecycle_template_root"),
        ForeignKeyConstraint(
            ["product_id"], ["products.id"], name="resource_lifecycle_events_product_id_fkey"
        ),
        ForeignKeyConstraint(
            ["product_id", "resource_revision"],
            ["product_revisions.product_id", "product_revisions.revision"],
            name="resource_lifecycle_events_product_id_resource_revis_fkey",
        ),
        Index(
            "lifecycle_certificate_sequence",
            "certificate_id",
            "revision",
            unique=True,
            postgresql_where=sql_text("(certificate_id IS NOT NULL)"),
        ),
        Index(
            "lifecycle_feature_sequence",
            "feature_id",
            "revision",
            unique=True,
            postgresql_where=sql_text("(feature_id IS NOT NULL)"),
        ),
        Index(
            "lifecycle_product_sequence",
            "product_id",
            "revision",
            unique=True,
            postgresql_where=sql_text("(product_id IS NOT NULL)"),
        ),
        Index(
            "lifecycle_profile_sequence",
            "profile_id",
            "revision",
            unique=True,
            postgresql_where=sql_text("(profile_id IS NOT NULL)"),
        ),
        Index(
            "lifecycle_template_sequence",
            "template_id",
            "revision",
            unique=True,
            postgresql_where=sql_text("(template_id IS NOT NULL)"),
        ),
        Index(
            "management_certificate_lifecycle_history",
            "certificate_id",
            column("revision").desc(),
            column("id").desc(),
            postgresql_where=sql_text("(certificate_id IS NOT NULL)"),
        ),
        Index(
            "management_feature_lifecycle_history",
            "feature_id",
            column("revision").desc(),
            column("id").desc(),
            postgresql_where=sql_text("(feature_id IS NOT NULL)"),
        ),
        Index(
            "management_lifecycle_history",
            "product_id",
            column("revision").desc(),
            column("id").desc(),
        ),
        Index(
            "management_profile_lifecycle_history",
            "profile_id",
            column("revision").desc(),
            column("id").desc(),
            postgresql_where=sql_text("(profile_id IS NOT NULL)"),
        ),
        Index(
            "management_template_lifecycle_history",
            "template_id",
            column("revision").desc(),
            column("id").desc(),
            postgresql_where=sql_text("(template_id IS NOT NULL)"),
        ),
    )
