"""Retained product and feature lifecycle history with exclusive root arms."""

from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant


class ResourceLifecycleEvent(Tenant, Base):
    __tablename__ = "resource_lifecycle_events"
    product_id: Mapped[UUID | None] = mapped_column()
    feature_id: Mapped[UUID | None] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    resource_revision: Mapped[int] = mapped_column(Integer)
    before_state: Mapped[str] = mapped_column(String(8))
    after_state: Mapped[str] = mapped_column(String(8))
    reason_code: Mapped[str] = mapped_column(String(20))
    actor_user_id: Mapped[UUID] = mapped_column()
    actor_kind: Mapped[str] = mapped_column(String(8), default="session", server_default="session")
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        Index(
            "lifecycle_product_sequence",
            "org_id",
            "product_id",
            "revision",
            unique=True,
            postgresql_where=text("product_id IS NOT NULL"),
        ),
        Index(
            "lifecycle_feature_sequence",
            "org_id",
            "feature_id",
            "revision",
            unique=True,
            postgresql_where=text("feature_id IS NOT NULL"),
        ),
        CheckConstraint("num_nonnulls(product_id,feature_id)=1", name="lifecycle_one_root"),
        ForeignKeyConstraint(["org_id", "feature_id"], ["features.org_id", "features.id"]),
        ForeignKeyConstraint(
            ["org_id", "feature_id", "resource_revision"],
            [
                "feature_revisions.org_id",
                "feature_revisions.feature_id",
                "feature_revisions.revision",
            ],
        ),
        ForeignKeyConstraint(["org_id", "product_id"], ["products.org_id", "products.id"]),
        ForeignKeyConstraint(
            ["org_id", "product_id", "resource_revision"],
            [
                "product_revisions.org_id",
                "product_revisions.product_id",
                "product_revisions.revision",
            ],
        ),
        ForeignKeyConstraint(
            ["org_id", "actor_user_id"], ["memberships.org_id", "memberships.user_id"]
        ),
        CheckConstraint("revision >= 1 AND resource_revision >= 1", name="lifecycle_versions"),
        CheckConstraint(
            "before_state IN ('active','inactive') AND after_state IN ('active','inactive') "
            "AND before_state <> after_state",
            name="lifecycle_transition",
        ),
        CheckConstraint(
            "reason_code IN ('obsolete','duplicate','unavailable','restored','other') "
            "AND ((after_state = 'active') = (reason_code = 'restored'))",
            name="lifecycle_reason",
        ),
        CheckConstraint("actor_kind = 'session'", name="lifecycle_human"),
    )
