"""Real PostgreSQL/API/queue recovery acceptance, specified before implementation.

Failure cases: business commit survives a failed queue insert; a worker dies after
claiming a delivery but before claiming the Job; repeated/concurrent submissions
create duplicate deliveries; recovery trusts a stale delivery for a rescheduled
job; live workers, running jobs (including expired paid attempts), terminal jobs and unresolved
call holds are replayed. All provider traffic is fake. Artifacts retain synthetic
job/delivery identities and the actual resulting states for reproducible acceptance.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.models.entities import Job, VendorCall
from fakes import FakeLLM
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError as SqlAlchemyOperationalError
from test_api import create_document
from test_job_boundaries import session_for
from test_sandbox_queue_recovery import sandbox_queue  # noqa: F401, F811


@pytest.fixture
async def delivery_runtime(sandbox_queue, database, tmp_path):  # noqa: F811
    queue = sandbox_queue
    app = create_app(Settings(data_dir=tmp_path / "files"), llm=FakeLLM(), queue=queue)
    async with app.router.lifespan_context(app), session_for(app, database) as (api, header):
        yield app, queue, api, header


async def submit(api, header, document, **body):
    return await api.post(f"/documents/{document}/parse", headers=header, json=body)


def abandon(admin_engine, delivery_id):
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE procrastinate_jobs SET status='doing', worker_id=NULL WHERE id=:id"),
            {"id": delivery_id},
        )


async def job_state(app, job_id):
    async with app.state.db.transaction() as session:
        row = await session.get(Job, UUID(str(job_id)))
        return row.status, row.queue_id


@pytest.mark.parametrize("failure", ["transport", "database"])
async def test_failed_transactional_delivery_rolls_back_submission(
    delivery_runtime, admin_engine, pdf_bytes, monkeypatch, tmp_path, failure
):
    app, queue, api, header = delivery_runtime
    _, document = await create_document(api, header, pdf_bytes)
    original = queue.enqueue_in_transaction

    async def fail_after_insert(*args, **kwargs):
        await original(*args, **kwargs)
        if failure == "database":
            raise SqlAlchemyOperationalError(None, None, OSError("Synthetic database interruption"))
        raise OSError("Synthetic dispatch interruption")

    monkeypatch.setattr(queue, "enqueue_in_transaction", fail_after_insert)
    response = await submit(api, header, document)
    assert response.status_code == 503
    assert response.json()["data"]["error"]["code"] == "queue_unavailable"
    async with app.state.db.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == 0
        assert await session.scalar(text("SELECT count(*) FROM procrastinate_jobs")) == 0
    monkeypatch.setattr(queue, "enqueue_in_transaction", original)
    accepted = await submit(api, header, document)
    assert accepted.status_code == 200, accepted.text
    job_id = accepted.json()["data"]["job_id"]
    await queue.app.run_worker_async(queues=["bid"], wait=False, concurrency=1)
    assert (await job_state(app, job_id))[0] == "succeeded"
    (tmp_path / "atomic-delivery.json").write_text(
        json.dumps(
            {
                "failure_status": response.status_code,
                "rollback_verified": True,
                "retried_job": job_id,
                "final_status": "succeeded",
            },
            indent=2,
        )
    )


@pytest.mark.parametrize("retry", [False, True])
async def test_resubmission_recovers_abandoned_delivery_without_duplicate_work(
    delivery_runtime, admin_engine, pdf_bytes, retry, tmp_path
):
    app, queue, api, header = delivery_runtime
    _, document = await create_document(api, header, pdf_bytes)
    first = await submit(api, header, document)
    assert first.status_code == 200, first.text
    job_id = first.json()["data"]["job_id"]
    _, old_delivery = await job_state(app, job_id)
    abandon(admin_engine, old_delivery)
    results = await asyncio.gather(*(submit(api, header, document, retry=retry) for _ in range(2)))
    assert all(result.status_code == 200 for result in results)
    _, new_delivery = await job_state(app, job_id)
    assert new_delivery != old_delivery
    with admin_engine.begin() as connection:
        deliveries = (
            connection.execute(
                text(
                    "SELECT id FROM procrastinate_jobs WHERE status='todo' AND args->>'job_id'=:job"
                ),
                {"job": job_id},
            )
            .scalars()
            .all()
        )
    assert deliveries == [new_delivery]
    await queue.app.run_worker_async(queues=["bid"], wait=False, concurrency=1)
    assert (await job_state(app, job_id))[0] == "succeeded"
    async with app.state.db.transaction() as session:
        saved = await session.get(Job, UUID(job_id))
        assert saved.attempts == 1
    (tmp_path / "resubmission-recovery.json").write_text(
        json.dumps(
            {
                "retry": retry,
                "job": job_id,
                "old_delivery": old_delivery,
                "new_delivery": new_delivery,
                "attempts": 1,
                "status": "succeeded",
            },
            indent=2,
        )
    )


async def test_periodic_recovery_skips_stale_and_uncertain_deliveries(
    delivery_runtime, admin_engine, pdf_bytes, tmp_path
):
    app, queue, api, header = delivery_runtime
    records = {}
    documents = {}
    for label in ("queued", "live", "running", "expired", "pending", "unknown", "succeeded"):
        _, document = await create_document(api, header, pdf_bytes)
        response = await submit(api, header, document)
        assert response.status_code == 200, response.text
        job_id = response.json()["data"]["job_id"]
        _, delivery = await job_state(app, job_id)
        records[label] = (job_id, delivery)
        documents[label] = document
        abandon(admin_engine, delivery)
    live_worker = await queue.app.job_manager.register_worker()
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE procrastinate_jobs SET worker_id=:worker WHERE id=:id"),
            {"worker": live_worker, "id": records["live"][1]},
        )
    async with app.state.db.transaction() as session:
        for label in ("running", "expired", "succeeded"):
            row = await session.get(Job, UUID(records[label][0]))
            row.status = "succeeded" if label == "succeeded" else "running"
            row.run_id = uuid4()
            row.lease_until = datetime.now(UTC) + timedelta(minutes=-5 if label == "expired" else 5)
        for label in ("pending", "unknown", "expired"):
            # A call is admitted only under a live attempt; a retried attempt then
            # returns its job to the queue with the call still unresolved.
            job = await session.get(Job, UUID(records[label][0]))
            job.status, job.run_id = "running", uuid4()
            job.lease_until = datetime.now(UTC) + timedelta(minutes=5)
            await session.flush()
            request = "0" * 64
            call = VendorCall(
                job_id=job.id,
                run_id=job.run_id,
                task_id=job.task_id,
                capability="llm",
                request_sha256=request,
                provider_identity={
                    "capability": "llm",
                    "request_sha256": request,
                    "provider": "synthetic",
                    "version": "synthetic-recovery",
                },
            )
            session.add(call)
            await session.flush()
            if label != "pending":
                call.state = "unknown"
            if label == "expired":
                job.lease_until = datetime.now(UTC) - timedelta(minutes=5)
            else:
                job.status, job.lease_until = "queued", None
    for label in ("pending", "unknown"):
        refused = await submit(api, header, documents[label], retry=True)
        assert refused.status_code == 409
        assert refused.json()["data"]["error"]["code"] == "job_recovery_required"
        assert (await job_state(app, records[label][0]))[1] == records[label][1]
    # A delivery the job no longer records cannot reschedule it.
    stale = await queue.task.defer_async(job_id=records["queued"][0])
    abandon(admin_engine, stale)
    await asyncio.gather(queue.recover_process_jobs(), queue.recover_process_jobs())
    await queue.recover_process_jobs()
    after = {}
    for label, (job_id, delivery) in records.items():
        state, current = await job_state(app, job_id)
        after[label] = {"status": state, "delivery": current}
        if label == "queued":
            assert current != delivery
        else:
            assert current == delivery
    with admin_engine.begin() as connection:
        assert (
            connection.scalar(
                text("SELECT status FROM procrastinate_jobs WHERE id=:id"), {"id": stale}
            )
            == "doing"
        )
        assert (
            connection.scalar(text("SELECT count(*) FROM procrastinate_jobs WHERE status='todo'"))
            == 1
        )
    await queue.app.run_worker_async(queues=["bid"], wait=False, concurrency=1)
    assert (await job_state(app, records["queued"][0]))[0] == "succeeded"
    (tmp_path / "periodic-delivery-recovery.json").write_text(
        json.dumps(
            {
                "cases": after,
                "stale_delivery": stale,
                "only_safe_queued_job_executed": True,
                "rerun": "pytest -q server/tests/test_process_delivery_recovery.py",
            },
            indent=2,
        )
    )
