"""Durable bounded board invalidation; no business Job and no image processing."""

from uuid import UUID

from sqlalchemy import select

from app.models.attachments import TaskAttachment
from app.models.entities import AuditLog
from app.services import task_events

BATCH = 100


def register(app, processor_getter):
    @app.task(name="bid.attachment_invalidate", queue="bid", retry=True)
    async def run(
        org_id: str,
        action: str,
        request_id: str,
        root_id: str,
        link_id: str | None = None,
        after_task_id: str | None = None,
    ):
        processor = processor_getter()
        if processor is None:
            raise RuntimeError("Worker processor is not configured")
        async with processor.db.transaction(UUID(org_id)) as session:
            # Only a committed successful mutation can produce this maintenance work.
            receipt = await session.scalar(
                select(AuditLog.id)
                .where(
                    AuditLog.action == action, AuditLog.details["request_id"].astext == request_id
                )
                .limit(1)
            )
            if receipt is None:
                return
            query = select(TaskAttachment.task_id).where(
                TaskAttachment.attachment_id == UUID(root_id)
            )
            if link_id:
                query = query.where(TaskAttachment.profile_attachment_link_id == UUID(link_id))
            if after_task_id:
                query = query.where(TaskAttachment.task_id > UUID(after_task_id))
            tasks = list(
                (
                    await session.scalars(
                        query.distinct().order_by(TaskAttachment.task_id).limit(BATCH + 1)
                    )
                ).all()
            )
            for task_id in tasks[:BATCH]:
                await task_events.append(
                    session,
                    UUID(org_id),
                    task_id,
                    {"type": "board_changed", "invalidate_all": True},
                )
            if len(tasks) > BATCH:
                await enqueue(
                    processor.queue,
                    session,
                    org_id=org_id,
                    action=action,
                    request_id=request_id,
                    root_id=root_id,
                    link_id=link_id,
                    after_task_id=str(tasks[BATCH - 1]),
                )

    return run


async def enqueue(queue, session, **arguments):
    connection = await queue.transaction_connection(session)
    return await queue.attachment_invalidation_task.configure(connection=connection).defer_async(
        **arguments
    )
