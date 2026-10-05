"""Tasks and their tender documents: creation, upload and access."""

import hashlib
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import PDFSettings
from app.core.errors import ServiceError, not_found
from app.models.entities import AuditLog, Document, Task
from app.providers.storage import Storage
from app.schemas.budget_contracts import BudgetTaskCreate, TaskBudgetInput
from app.schemas.contracts import TaskCreate
from app.services import budgets
from app.services.auth import Identity
from app.services.parsing import validate_document_async
from app.services.task_authorization import task_authorized

TASK_FIELDS = ("id", "name", "org_id", "model_redaction_enabled", "model_redaction_revision")


async def create_task(
    session: AsyncSession,
    identity: Identity,
    body: TaskCreate,
    currency: str = "USD",
) -> Task:
    identity.require("task:create")
    data = body.model_dump(exclude={"budget"})
    initial = body.budget if isinstance(body, BudgetTaskCreate) else None
    if body.budget_usd is not None:
        if currency != "USD":
            raise ServiceError(
                "billing_currency_mismatch", "Legacy USD budgets require USD billing", 400, 2
            )
        initial = TaskBudgetInput(limit=Decimal(str(body.budget_usd)), currency="USD")
    if initial is not None:
        await budgets._human(session, identity, "task:budget:write", {"admin", "bidder"})
        if initial.currency != currency:
            raise ServiceError(
                "billing_currency_mismatch", "Budget currency must match billing currency", 400, 2
            )
    task = Task(
        org_id=identity.org_id,
        created_by=identity.user_id,
        **data,
        budget_limit=initial.limit if initial else None,
        budget_currency=currency,
        budget_revision=1,
        budget_state="active",
    )
    session.add(task)
    await session.flush()
    session.add(
        AuditLog(
            org_id=identity.org_id,
            actor_user_id=identity.user_id,
            actor_token_id=identity.token_id,
            action="task.budget.created",
            object_id=task.id,
            details={"revision": 1, "currency": currency},
        )
    )
    from app.services.task_workflow import seed

    await seed(session, identity, task)
    return task


async def list_tasks(session: AsyncSession, identity: Identity):
    identity.require("task:read")
    from app.models.team_workflow import TaskMember

    query = select(Task).order_by(Task.created_at)
    if not (
        identity.actor_kind == "session" and identity.token_id is None and identity.role == "admin"
    ):
        query = query.where(
            Task.id.in_(
                select(TaskMember.task_id).where(
                    TaskMember.org_id == identity.org_id,
                    TaskMember.user_id == identity.user_id,
                    TaskMember.active.is_(True),
                )
            )
        )
    return (await session.scalars(query)).all()


@task_authorized("task:read", parent=("document_id", "documents"))
async def require_document(session: AsyncSession, document_id: UUID) -> Document:
    document = await session.get(Document, document_id)
    if document is None:
        raise not_found()
    return document


@task_authorized("tender:upload", write=True)
async def upload(
    session: AsyncSession,
    identity: Identity,
    task_id: UUID,
    filename: str | None,
    read: Callable[[int], Awaitable[bytes]],
    storage: Storage,
    *,
    max_bytes: int,
    max_pages: int,
    settings: PDFSettings | None = None,
) -> tuple[Document, bool]:
    """Store a validated tender once per task and content hash: (document, duplicate)."""
    identity.require("tender:upload")
    if await session.get(Task, task_id) is None:
        raise not_found()
    # The body is read only after authorization, and never beyond the limit.
    content = await read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ServiceError("file_too_large", "File exceeds upload limit", 413, 2)
    name = Path(filename or "").name
    if not name or len(name) > 200:
        raise ServiceError("invalid_filename", "Valid file name required", 400, 2)
    suffix = Path(name).suffix.lower()
    await validate_document_async(content, suffix, max_pages, settings)
    digest = hashlib.sha256(content).hexdigest()
    key = f"org/{identity.org_id}/task/{task_id}/{digest}{suffix}"
    await storage.put(identity.org_id, key, content)
    identifier = await session.scalar(
        insert(Document)
        .values(
            org_id=identity.org_id,
            task_id=task_id,
            name=name,
            sha256=digest,
            storage_key=key,
            media_type="application/pdf"
            if suffix == ".pdf"
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        .on_conflict_do_nothing(index_elements=["org_id", "task_id", "sha256"])
        .returning(Document.id)
    )
    document = await session.scalar(
        select(Document).where(Document.task_id == task_id, Document.sha256 == digest)
    )
    assert document is not None
    return document, identifier is None
