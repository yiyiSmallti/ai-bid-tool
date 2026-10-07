"""Queue recovery failures specified before implementation.

Interrupted sandbox deliveries can outlive terminal or missing application jobs.
Recovery must retain healthy workers, queued/running jobs (including expired leases),
other task kinds and queues, and malformed identities. Tenant RLS must distinguish
each org's live jobs from missing rows. Repeated/concurrent recovery must not repeat
terminal events or change application data; a renewed heartbeat must fence recovery.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.jobs.queue import Queue
from app.models.entities import Document, Job, Task
from procrastinate.schema import SchemaManager
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.orm import Session


@pytest.fixture
async def sandbox_queue(application, admin_engine):
    with admin_engine.begin() as connection:
        if connection.scalar(text("SELECT to_regclass('public.procrastinate_jobs')")) is None:
            connection.connection.driver_connection.execute(SchemaManager.get_schema())
        connection.execute(
            text("TRUNCATE procrastinate_jobs, procrastinate_workers RESTART IDENTITY CASCADE")
        )
        for name in (
            "procrastinate_jobs",
            "procrastinate_workers",
            "procrastinate_events",
            "procrastinate_periodic_defers",
        ):
            connection.exec_driver_sql(
                sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO bid_app")
                .format(sql.Identifier(name))
                .as_string()
            )
        for name in connection.scalars(
            text(
                "SELECT sequencename FROM pg_sequences WHERE schemaname='public' "
                "AND sequencename LIKE 'procrastinate_%'"
            )
        ).all():
            connection.exec_driver_sql(
                sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {} TO bid_app")
                .format(sql.Identifier(name))
                .as_string()
            )
    queue = Queue(application.state.processor.settings)
    queue.processor = application.state.processor
    try:
        async with queue.app.open_async():
            yield queue
    finally:
        await application.state.db.engine.dispose()


def seed_application_jobs(admin_engine, tenants):
    output = []
    with Session(admin_engine) as session, session.begin():
        for org_id, user_id in zip(tenants["orgs"], tenants["users"], strict=True):
            task = Task(id=uuid4(), org_id=org_id, name="Queue recovery", created_by=user_id)
            session.add(task)
            session.flush()
            document = Document(
                id=uuid4(),
                org_id=org_id,
                task_id=task.id,
                name="Synthetic tender.pdf",
                sha256="a" * 64,
                storage_key=f"org/{org_id}/synthetic.pdf",
                media_type="application/pdf",
            )
            session.add(document)
            session.flush()
            jobs = {}
            for label, status, kind, lease in (
                ("succeeded", "succeeded", "sandbox", None),
                ("failed", "failed", "sandbox", None),
                ("cancelled", "cancelled", "sandbox", None),
                ("queued", "queued", "sandbox", None),
                ("running", "running", "sandbox", timedelta(minutes=5)),
                ("expired", "running", "sandbox", timedelta(minutes=-5)),
                ("other_kind", "failed", "parse", None),
            ):
                job = Job(
                    id=uuid4(),
                    org_id=org_id,
                    task_id=task.id,
                    document_id=document.id,
                    kind=kind,
                    cache_key=uuid4().hex,
                    status=status,
                    lease_until=datetime.now(UTC) + lease if lease else None,
                )
                session.add(job)
                jobs[label] = str(job.id)
            output.append((str(org_id), jobs))
    return output


async def seed_delivery(queue, admin_engine, org_id, job_id, **overrides):
    delivery = await queue.sandbox_task.defer_async(org_id=org_id, job_id=job_id)
    values = {
        "id": delivery,
        "status": "doing",
        "worker_id": None,
        "task_name": "bid.sandbox",
        "queue_name": "bid",
        **overrides,
    }
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE procrastinate_jobs SET status=CAST(:status AS procrastinate_job_status), "
                "worker_id=:worker_id, task_name=:task_name, queue_name=:queue_name WHERE id=:id"
            ),
            values,
        )
    return delivery


async def test_reconcile_stale_sandbox_deliveries(
    sandbox_queue, admin_engine, tenants, caplog, tmp_path
):
    queue = sandbox_queue
    jobs = seed_application_jobs(admin_engine, tenants)
    old_worker = await queue.app.job_manager.register_worker()
    live_worker = await queue.app.job_manager.register_worker()
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE procrastinate_workers SET last_heartbeat=now()-interval '1 hour' "
                "WHERE id=:id"
            ),
            {"id": old_worker},
        )
        before = connection.execute(text("SELECT * FROM jobs ORDER BY id")).mappings().all()
    expected = {}
    recovered = {}
    for org_id, by_status in jobs:
        for status in ("succeeded", "failed", "cancelled"):
            delivery = await seed_delivery(
                queue, admin_engine, org_id, by_status[status], worker_id=old_worker
            )
            expected[delivery] = recovered[delivery] = (
                "aborted" if status == "cancelled" else status
            )
        missing = await seed_delivery(queue, admin_engine, org_id, str(uuid4()))
        expected[missing] = recovered[missing] = "aborted"
        for label in ("queued", "running", "expired", "other_kind"):
            delivery = await seed_delivery(queue, admin_engine, org_id, by_status[label])
            expected[delivery] = "doing"
        for job_id in (by_status["succeeded"], str(uuid4())):
            delivery = await seed_delivery(
                queue, admin_engine, org_id, job_id, worker_id=live_worker
            )
            expected[delivery] = "doing"
    org_id, by_status = jobs[0]
    for overrides in (
        {"task_name": "bid.process"},
        {"queue_name": "unrelated"},
        {"status": "todo"},
        {"status": "failed"},
    ):
        delivery = await seed_delivery(
            queue, admin_engine, org_id, by_status["succeeded"], **overrides
        )
        expected[delivery] = overrides.get("status", "doing")
    malformed = await seed_delivery(queue, admin_engine, "invalid-org", "invalid-job")
    expected[malformed] = "doing"
    caplog.set_level(logging.INFO, logger="app.jobs.queue")
    await asyncio.gather(queue.recover_sandbox_jobs(), queue.recover_sandbox_jobs())
    await queue.recover_sandbox_jobs()
    with admin_engine.begin() as connection:
        actual = dict(connection.execute(text("SELECT id, status FROM procrastinate_jobs")).all())
        assert actual == expected
        assert connection.execute(text("SELECT * FROM jobs ORDER BY id")).mappings().all() == before
        terminal_events = connection.execute(
            text(
                "SELECT job_id, type FROM procrastinate_events "
                "WHERE type IN ('succeeded','failed','aborted') ORDER BY job_id"
            )
        ).all()
        assert terminal_events == sorted(recovered.items())
    assert "Sandbox queue reconciliation" in caplog.text
    assert "terminal=" in caplog.text and "missing=" in caplog.text
    (tmp_path / "sandbox-queue-recovery.json").write_text(
        json.dumps(
            {
                "queue_states": actual,
                "application_jobs_unchanged": True,
                "recovered_count": len(recovered),
                "rerun": "pytest -q server/tests/test_sandbox_queue_recovery.py",
            },
            indent=2,
        )
    )


async def test_recovery_rechecks_renewed_worker_heartbeat(
    sandbox_queue, admin_engine, tenants, monkeypatch
):
    queue = sandbox_queue
    worker = await queue.app.job_manager.register_worker()
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE procrastinate_workers SET last_heartbeat=now()-interval '1 hour'")
        )
    delivery = await seed_delivery(
        queue, admin_engine, str(tenants["orgs"][0]), str(uuid4()), worker_id=worker
    )
    original = queue.app.job_manager.get_stalled_jobs

    async def renew_after_scan(**kwargs):
        candidates = await original(**kwargs)
        await queue.app.job_manager.update_heartbeat(worker)
        return candidates

    monkeypatch.setattr(queue.app.job_manager, "get_stalled_jobs", renew_after_scan)
    await queue.recover_sandbox_jobs()
    with admin_engine.begin() as connection:
        assert (
            connection.scalar(
                text("SELECT status FROM procrastinate_jobs WHERE id=:id"), {"id": delivery}
            )
            == "doing"
        )
