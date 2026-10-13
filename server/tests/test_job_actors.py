"""Real database job provenance gates, including actorless local worker jobs.

Failure modes: reset custom GUCs invent provenance; worker provenance invents a
human; absent authentication accepts claimed grants; actorless jobs admit vendor
calls; unsupported actor kinds enter the ledger; snapshots change after submission.
"""

import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from app.jobs.execution import JobExecution, authorized_job
from app.models.entities import Job, VendorCall
from app.providers.base import ProviderFailure
from app.schemas.preflight_contracts import CallPlan
from app.services.auth import HUMAN_SCOPES, Identity, set_actor_context
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_api import create_document


def local_job(task, document, **fields):
    return Job(
        id=uuid4(),
        task_id=UUID(task),
        document_id=UUID(document),
        kind="parse",
        cache_key=uuid4().hex,
        run_id=uuid4(),
        **fields,
    )


@pytest.mark.parametrize("kind", [None, "", "worker"])
async def test_actorless_job_stays_actorless_and_cannot_dispatch(
    api, headers, application, tmp_path, pdf_bytes, kind
):
    task, document = await create_document(api, headers, pdf_bytes)
    async with application.state.db.transaction() as session:
        if kind is not None:
            await session.execute(
                text(
                    "SELECT set_config('app.actor_kind',:kind,true), "
                    "set_config('app.actor_token_id','',true), "
                    "set_config('app.actor_scopes','[]',true)"
                ),
                {"kind": kind},
            )
        job = local_job(task, document)
        session.add(job)
        await session.flush()
        await session.refresh(job)
        assert job.actor_token_id is None
        assert job.actor_kind == (kind or None)
        assert job.actor_scopes == []
        with pytest.raises(ProviderFailure, match="authorized identity") as denied:
            await authorized_job(session, job)
        assert denied.value.code == "job_actor_required"
        job.status = "running"
        job.lease_until = await session.scalar(text("SELECT clock_timestamp()")) + timedelta(
            minutes=5
        )
    quote = CallPlan(
        capability="llm",
        provider="synthetic",
        model="synthetic",
        version="1",
        request_sha256="a" * 64,
    )
    execution = JobExecution(
        application.state.processor.settings,
        application.state.db,
        job.id,
        job.run_id,
    )
    with pytest.raises(ProviderFailure, match="authorized identity") as denied:
        await execution.admit(quote)
    assert denied.value.code == "job_actor_required"
    async with application.state.db.transaction() as session:
        saved = await session.get(Job, job.id)
        assert saved is not None
        assert saved.actor_kind == (kind or None) and saved.actor_scopes == []
        assert not await session.scalar(
            text("SELECT EXISTS (SELECT FROM vendor_calls WHERE job_id=:id)"),
            {"id": job.id},
        )
        saved.status = "succeeded"
    with pytest.raises(DBAPIError, match="live job execution lease") as denied:
        async with application.state.db.transaction() as session:
            session.add(
                VendorCall(
                    task_id=UUID(task),
                    job_id=job.id,
                    run_id=job.run_id,
                    capability=quote.capability,
                    request_sha256=quote.request_sha256,
                    provider_identity=quote.model_dump(mode="json"),
                )
            )
            await session.flush()
    assert denied.value.orig.sqlstate == "23514"
    (tmp_path / f"actorless-{kind or 'unset'}.json").write_text(
        json.dumps(
            {
                "job_id": str(job.id),
                "actor_kind": kind or None,
                "status": "succeeded",
                "dispatch_error": "job_actor_required",
                "vendor_admission": "rejected",
            }
        )
    )


async def test_system_job_kind_is_rejected_by_actor_shape(api, headers, application, pdf_bytes):
    task, document = await create_document(api, headers, pdf_bytes)
    with pytest.raises(DBAPIError, match="job_actor_shape") as denied:
        async with application.state.db.transaction() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.actor_kind','system',true), "
                    "set_config('app.actor_token_id','',true), "
                    "set_config('app.actor_scopes','[]',true)"
                )
            )
            session.add(local_job(task, document))
            await session.flush()
    assert denied.value.orig.sqlstate == "23514"


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("", "authenticated job actor required"),
        ("worker", "job actor does not match authenticated actor"),
    ],
)
async def test_actorless_context_cannot_claim_a_submitter(
    api, headers, application, pdf_bytes, kind, message
):
    task, document = await create_document(api, headers, pdf_bytes)
    with pytest.raises(DBAPIError, match=message) as denied:
        async with application.state.db.transaction() as session:
            await session.execute(
                text("SELECT set_config('app.actor_kind',:kind,true)"),
                {"kind": kind},
            )
            session.add(
                local_job(
                    task,
                    document,
                    actor_kind="session",
                    actor_scopes=["req:extract"],
                )
            )
            await session.flush()
    assert denied.value.orig.sqlstate == "42501"


async def test_job_actor_snapshot_is_truthful_and_immutable(api, headers, application, pdf_bytes):
    task, document = await create_document(api, headers, pdf_bytes)
    actor = Identity(scopes=set(HUMAN_SCOPES))
    async with application.state.db.transaction() as session:
        await set_actor_context(session, actor)
        job = local_job(task, document)
        session.add(job)
        await session.flush()
        await session.refresh(job)
        assert job.actor_kind == "session"
        assert set(job.actor_scopes) == actor.scopes
    with pytest.raises(DBAPIError, match="job actor is immutable") as denied:
        async with application.state.db.transaction() as session:
            await session.execute(
                text("UPDATE jobs SET actor_scopes='[]' WHERE id=:id"), {"id": job.id}
            )
    assert denied.value.orig.sqlstate == "23514"
