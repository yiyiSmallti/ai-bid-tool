from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class TaskEventHead(Base):
    __tablename__ = "task_event_heads"
    id: Mapped[UUID] = mapped_column(
        nullable=False,
        primary_key=True,
        default=uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    last_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=sql_text("0")
    )
    retained_floor_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=sql_text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((retained_floor_seq >= 0) AND (retained_floor_seq <= last_seq)))",
            name="task_event_heads_check",
        ),
        CheckConstraint("((last_seq >= 0))", name="task_event_heads_last_seq_check"),
        UniqueConstraint("task_id", name="task_event_heads_task_id_key"),
        PrimaryKeyConstraint("id", name="task_event_heads_pkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="task_event_heads_task_id_fkey"),
    )


class TaskEvent(Base):
    __tablename__ = "task_events"
    id: Mapped[UUID] = mapped_column(
        nullable=False,
        primary_key=True,
        default=uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_id: Mapped[UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((jsonb_typeof(payload) = 'object'::text) AND (octet_length((payload)::text) <= 3072) AND ((payload ->> 'type'::text) = event_kind)))",
            name="task_events_check",
        ),
        CheckConstraint(
            "((event_kind = ANY (ARRAY['board_changed'::text, 'job_progress'::text, 'access_changed'::text])))",
            name="task_events_event_kind_check",
        ),
        CheckConstraint("((seq > 0))", name="task_events_seq_check"),
        UniqueConstraint("task_id", "seq", name="task_events_task_id_seq_key"),
        PrimaryKeyConstraint("id", name="task_events_pkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="task_events_task_id_fkey"),
    )
