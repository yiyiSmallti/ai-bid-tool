from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class Installation(Base):
    """The one row recording the deployment mode the database was initialized with."""

    __tablename__ = "installation"
    id: Mapped[bool] = mapped_column(
        Boolean, primary_key=True, default=True, server_default=sql_text("true")
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint("id", name="installation_singleton"),
        CheckConstraint(
            "((mode)::text = ANY ((ARRAY['personal'::character varying, 'enterprise'::character varying])::text[]))",
            name="installation_mode",
        ),
        CheckConstraint(
            "(((mode)::text = 'enterprise'::text) = (company_name IS NOT NULL))",
            name="installation_company",
        ),
    )
