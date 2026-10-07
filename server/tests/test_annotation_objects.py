"""Durable B05 staging failure inventory, fixed before implementation.

A process can die immediately before or after storage.put: the committed stage
must retain a delayed delivery in the same transaction, and cleanup must be
idempotent for absent or uploaded bytes. A current live attempt and every committed
rendition remain intact. An expired or superseded run cannot delete a committed
winner. Early delivery reschedules after grace without requeuing cancelled render.
Foreign-org or forged keys never cause reads/deletes. Clock advancement is explicit
and test-local; no bucket scan, provider call or fabricated material is involved.

The full API/PostgreSQL paths require the isolated external test fixture. This
module does not start or configure database/services.
"""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from annotation_test_renderer import configure_renderer
from app.core.errors import ServiceError
from app.models.entities import Job
from app.services import annotation_jobs, annotation_objects
from sqlalchemy import text
from test_annotation_api import preview_submit, seed_annotation
from test_response_cards import phase_one_client

ARTIFACT = Path(__file__).resolve().parents[2] / "data/work/annotation-object-reconciliation"


class WorkerKilled(BaseException):
    """Simulate disappearance outside Processor's ordinary failure recovery."""


def _exists(storage, org, key):
    return storage.path(org, key).is_file()


def _record(name, value):
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    (ARTIFACT / f"{name}.json").write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


async def ledger(app, org, job_id):
    async with app.state.db.transaction(org) as session:
        job = await session.get(Job, UUID(job_id))
        assert job is not None
        return job.result[annotation_objects.FIELD], job.status, job.run_id, job.lease_until


def set_clock(monkeypatch, now):
    async def clock(session):
        return now

    monkeypatch.setattr(annotation_objects, "_clock", clock)


async def crash_after_stage(api, app, auth, task, target, org, monkeypatch, *, uploaded):
    _, _, accepted = await preview_submit(api, auth, task, target)
    storage = app.state.processor.storage
    original_put = storage.put
    captured = {}

    async def put(org_id, key, content):
        if "/screenshots/" in key:
            captured.update(key=key, image_sha256=hashlib.sha256(content).hexdigest())
            if uploaded:
                await original_put(org_id, key, content)
            raise WorkerKilled()
        await original_put(org_id, key, content)

    async def lost_finally(storage, org_id, key):
        # A dead process cannot execute the ordinary best-effort finally cleanup.
        return None

    monkeypatch.setattr(storage, "put", put)
    monkeypatch.setattr(annotation_jobs, "discard", lost_finally)
    with pytest.raises(WorkerKilled):
        await app.state.processor(str(org), accepted["job_id"])
    descriptors, status, run_id, _ = await ledger(app, org, accepted["job_id"])
    assert status == "running" and len(descriptors) == 1
    staged = descriptors[0]
    assert staged["key"] == captured["key"] and staged["image_sha256"] == captured["image_sha256"]
    assert UUID(staged["run_id"]) == run_id
    assert app.state.queue.annotation_cleanups == [(str(org), accepted["job_id"], 300)]
    assert await asyncio.to_thread(_exists, storage, org, staged["key"]) is uploaded
    return accepted["job_id"], staged


@pytest.mark.parametrize("uploaded", [False, True])
async def test_crash_before_or_after_upload_reconciles_only_saved_attempt(
    tenants, tmp_path, monkeypatch, uploaded
):
    configure_renderer(monkeypatch)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, _, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        org = tenants["orgs"][0]
        job_id, staged = await crash_after_stage(
            api, app, headers[0], task, target, org, monkeypatch, uploaded=uploaded
        )
        cancelled = await api.post(f"/v4/jobs/{job_id}/cancel", headers=headers[0])
        assert cancelled.status_code == 200, cancelled.text
        calls = list(app.state.queue.calls)
        created = datetime.fromisoformat(staged["created_at"])
        set_clock(monkeypatch, created + timedelta(seconds=annotation_objects.GRACE_SECONDS - 1))
        early = await annotation_objects.reconcile(app.state.processor, org, UUID(job_id))
        assert early == {"deleted": 0, "retained": 0, "pending": 1}
        assert await asyncio.to_thread(_exists, app.state.storage, org, staged["key"]) is uploaded
        set_clock(monkeypatch, created + timedelta(seconds=annotation_objects.GRACE_SECONDS + 1))
        result = await annotation_objects.reconcile(app.state.processor, org, UUID(job_id))
        assert result == {"deleted": 1, "retained": 0, "pending": 0}
        assert not await asyncio.to_thread(_exists, app.state.storage, org, staged["key"])
        assert (await ledger(app, org, job_id))[1] == "cancelled"
        assert app.state.queue.calls == calls
        assert await annotation_objects.reconcile(app.state.processor, org, UUID(job_id)) == result
        await asyncio.to_thread(
            _record,
            f"crash-{'after' if uploaded else 'before'}-put",
            {
                "job_id": job_id,
                "uploaded_before_reconcile": uploaded,
                "early": early,
                "after_grace": result,
                "status": "cancelled",
                "saved_key_sha256": hashlib.sha256(staged["key"].encode()).hexdigest(),
                "render_was_not_requeued": True,
            },
        )


async def test_live_same_run_is_not_deleted_and_only_cleanup_is_rescheduled(
    tenants, tmp_path, monkeypatch
):
    configure_renderer(monkeypatch)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, _, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        org = tenants["orgs"][0]
        job_id, staged = await crash_after_stage(
            api, app, headers[0], task, target, org, monkeypatch, uploaded=True
        )
        _, _, _, lease = await ledger(app, org, job_id)
        now = datetime.fromisoformat(staged["created_at"]) + timedelta(seconds=301)
        assert lease > now
        set_clock(monkeypatch, now)
        calls = list(app.state.queue.calls)
        result = await annotation_objects.reconcile(app.state.processor, org, UUID(job_id))
        assert result == {"deleted": 0, "retained": 0, "pending": 1}
        assert await asyncio.to_thread(_exists, app.state.storage, org, staged["key"])
        assert app.state.queue.calls == calls and len(app.state.queue.annotation_cleanups) == 2


@pytest.mark.parametrize("fence", ["expired", "superseded"])
async def test_expired_or_superseded_run_allows_orphan_cleanup(
    tenants, tmp_path, monkeypatch, admin_engine, fence
):
    configure_renderer(monkeypatch)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, _, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        org = tenants["orgs"][0]
        job_id, staged = await crash_after_stage(
            api, app, headers[0], task, target, org, monkeypatch, uploaded=True
        )
        _, _, old_run, lease = await ledger(app, org, job_id)
        if fence == "expired":
            now = lease + timedelta(seconds=1)
        else:
            with admin_engine.begin() as connection:
                connection.execute(
                    text("UPDATE jobs SET run_id=:run WHERE id=:id"),
                    {"run": uuid4(), "id": UUID(job_id)},
                )
            now = datetime.fromisoformat(staged["created_at"]) + timedelta(seconds=301)
        set_clock(monkeypatch, now)
        result = await annotation_objects.reconcile(app.state.processor, org, UUID(job_id))
        assert result == {"deleted": 1, "retained": 0, "pending": 0}
        assert not await asyncio.to_thread(_exists, app.state.storage, org, staged["key"])
        assert UUID(staged["run_id"]) == old_run


async def test_committed_rendition_survives_cleanup_and_ledger_is_private(
    tenants, tmp_path, monkeypatch
):
    configure_renderer(monkeypatch)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, _, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        org = tenants["orgs"][0]
        _, _, accepted = await preview_submit(api, headers[0], task, target)
        job_id = accepted["job_id"]
        await app.state.processor(str(org), job_id)
        descriptors, status, _, _ = await ledger(app, org, job_id)
        assert status == "succeeded" and len(descriptors) == 1
        staged = descriptors[0]
        set_clock(monkeypatch, datetime.fromisoformat(staged["created_at"]) + timedelta(days=1))
        result = await annotation_objects.reconcile(app.state.processor, org, UUID(job_id))
        assert result == {"deleted": 0, "retained": 1, "pending": 0}
        assert await asyncio.to_thread(_exists, app.state.storage, org, staged["key"])
        public = await api.get(f"/v4/jobs/{job_id}", headers=headers[0])
        assert public.status_code == 200
        assert annotation_objects.FIELD not in public.json()["data"]["result"]
        assert staged["key"] not in public.text
        await asyncio.to_thread(
            _record,
            "committed-reference",
            {
                "job_id": job_id,
                "status": status,
                "cleanup": result,
                "object_retained": True,
                "ledger_not_public": True,
                "saved_key_sha256": hashlib.sha256(staged["key"].encode()).hexdigest(),
            },
        )


async def test_wrong_org_and_forged_saved_key_never_delete_objects(
    tenants, tmp_path, monkeypatch, admin_engine
):
    configure_renderer(monkeypatch)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, _, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        org = tenants["orgs"][0]
        job_id, staged = await crash_after_stage(
            api, app, headers[0], task, target, org, monkeypatch, uploaded=True
        )
        cancelled = await api.post(f"/v4/jobs/{job_id}/cancel", headers=headers[0])
        assert cancelled.status_code == 200
        set_clock(monkeypatch, datetime.fromisoformat(staged["created_at"]) + timedelta(days=1))
        result = await annotation_objects.reconcile(
            app.state.processor, tenants["orgs"][1], UUID(job_id)
        )
        assert result == {"deleted": 0, "retained": 0, "pending": 0}
        assert await asyncio.to_thread(_exists, app.state.storage, org, staged["key"])
        forged = {**staged, "key": staged["key"].replace(str(org), str(tenants["orgs"][1]))}
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE jobs SET result=jsonb_set(result,'{annotation_staged_objects}',CAST(:ledger AS jsonb)) WHERE id=:id"
                ),
                {"ledger": json.dumps([forged]), "id": UUID(job_id)},
            )
        with pytest.raises(ServiceError) as error:
            await annotation_objects.reconcile(app.state.processor, org, UUID(job_id))
        assert error.value.code == "annotation_cleanup_ledger"
        assert await asyncio.to_thread(_exists, app.state.storage, org, staged["key"])


async def test_cleanup_delivery_failure_rolls_back_stage_before_upload(
    tenants, tmp_path, monkeypatch
):
    configure_renderer(monkeypatch)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, _, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        org = tenants["orgs"][0]
        _, _, accepted = await preview_submit(api, headers[0], task, target)
        job_id = accepted["job_id"]

        async def failed_delivery(*args, **kwargs):
            raise ServiceError(
                "cleanup_queue_unavailable", "Synthetic durable queue outage", 503, 3
            )

        monkeypatch.setattr(
            app.state.queue, "enqueue_annotation_cleanup_in_transaction", failed_delivery
        )
        uploaded = []
        original_put = app.state.storage.put

        async def put(org_id, key, content):
            if "/screenshots/" in key:
                uploaded.append(key)
            await original_put(org_id, key, content)

        monkeypatch.setattr(app.state.storage, "put", put)
        from app.providers.base import ProviderFailure

        with pytest.raises(ProviderFailure):
            await app.state.processor(str(org), job_id)
        async with app.state.db.transaction(org) as session:
            job = await session.get(Job, UUID(job_id))
            assert job is not None and annotation_objects.FIELD not in job.result
            assert job.status == "queued"
        assert uploaded == [] and app.state.queue.annotation_cleanups == []


async def test_upload_finishing_after_cancellation_requeues_only_object_cleanup(
    tenants, tmp_path, monkeypatch
):
    configure_renderer(monkeypatch)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        org = tenants["orgs"][0]
        task, _, _, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        _, _, accepted = await preview_submit(api, headers[0], task, target)
        original = app.state.storage.put
        started, finish, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def delayed(org_id, key, content):
            if "/screenshots/" in key:
                started.set()
                await finish.wait()
            await original(org_id, key, content)
            completed.set()

        monkeypatch.setattr(app.state.storage, "put", delayed)
        worker = asyncio.create_task(app.state.processor(str(org), accepted["job_id"]))
        try:
            await asyncio.wait_for(started.wait(), 3)
            cancelled = await api.post(f"/v4/jobs/{accepted['job_id']}/cancel", headers=headers[0])
            assert cancelled.status_code == 200, cancelled.text
            await asyncio.wait_for(worker, 2)
            descriptors, status, _, _ = await ledger(app, org, accepted["job_id"])
            assert status == "cancelled"
            finish.set()
            await asyncio.wait_for(completed.wait(), 2)
            for _ in range(100):
                if len(app.state.queue.annotation_cleanups) >= 2:
                    break
                await asyncio.sleep(0.01)
            assert len(app.state.queue.annotation_cleanups) >= 2
            set_clock(
                monkeypatch,
                datetime.fromisoformat(descriptors[0]["created_at"]) + timedelta(seconds=601),
            )
            receipt = await annotation_objects.reconcile(
                app.state.processor, org, UUID(accepted["job_id"])
            )
            assert receipt["deleted"] == 1
            assert not await asyncio.to_thread(
                _exists, app.state.storage, org, descriptors[0]["key"]
            )
            assert (await api.get(f"/v4/jobs/{accepted['job_id']}", headers=headers[0])).json()[
                "data"
            ]["status"] == "cancelled"
        finally:
            finish.set()
            if not worker.done():
                worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
