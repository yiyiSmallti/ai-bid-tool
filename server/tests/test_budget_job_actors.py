"""Real database job provenance gates, including actorless local worker jobs.

Failure modes: reset custom GUCs reject system jobs; worker provenance invents a
human; absent authentication accepts claimed grants; actorless jobs admit vendor
calls; snapshots change after submission; missing tenant context bypasses RLS.
"""

import json
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from app.jobs.execution import authorized_job
from app.models.entities import Job, VendorCall
from app.providers.base import ProviderFailure
from app.schemas.budget_contracts import BudgetCallQuote
from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_api import create_document


def local_job(org, task, document, **fields):
    return Job(
        id=uuid4(),
        org_id=org,
        task_id=UUID(task),
        document_id=UUID(document),
        kind="parse",
        cache_key=uuid4().hex,
        run_id=uuid4(),
        **fields,
    )


@pytest.mark.parametrize("kind", [None, "", "worker", "system"])
async def test_actorless_job_stays_actorless_and_cannot_dispatch(
    api, headers, application, tenants, tmp_path, pdf_bytes, kind
):
    task, document = await create_document(api, headers[0], pdf_bytes)
    org = tenants["orgs"][0]
    async with application.state.db.transaction(org) as session:
        if kind is not None:
            await session.execute(
                text(
                    "SELECT set_config('app.actor_kind',:kind,true), "
                    "set_config('app.actor_user_id','',true), "
                    "set_config('app.actor_token_id','',true), "
                    "set_config('app.actor_scopes','[]',true)"
                ),
                {"kind": kind},
            )
        job = local_job(org, task, document)
        session.add(job)
        await session.flush()
        await session.refresh(job)
        assert job.actor_user_id is None and job.actor_token_id is None
        assert job.actor_kind is None and job.actor_scopes == []
        with pytest.raises(ProviderFailure, match="authorized identity"):
            await authorized_job(session, job)
        job.status = "succeeded"
    async with application.state.db.transaction(tenants["orgs"][1]) as session:
        assert await session.get(Job, job.id) is None
    async with application.state.db.transaction() as session:
        assert await session.get(Job, job.id) is None
    quote = BudgetCallQuote(
        capability="llm",
        payer="org_direct",
        provider="synthetic",
        model="synthetic",
        version="1",
        price_revision="synthetic-v1",
        request_sha256="a" * 64,
        currency="USD",
        reserved_charge=Decimal(0),
        reserved_task_amount=Decimal(1),
        vendor_usd_upper_bound=Decimal(1),
    )
    with pytest.raises(DBAPIError, match="authenticated job"):
        async with application.state.db.transaction(org) as session:
            session.add(
                VendorCall(
                    org_id=org,
                    task_id=UUID(task),
                    job_id=job.id,
                    run_id=job.run_id,
                    budget_revision=1,
                    capability=quote.capability,
                    payer=quote.payer,
                    currency=quote.currency,
                    price_revision=quote.price_revision,
                    request_sha256=quote.request_sha256,
                    reserved_charge=quote.reserved_charge,
                    reserved_task_amount=quote.reserved_task_amount,
                    quote=quote.model_dump(mode="json"),
                )
            )
            await session.flush()
    (tmp_path / f"actorless-{kind or 'unset'}.json").write_text(
        json.dumps(
            {
                "job_id": str(job.id),
                "actor_user_id": None,
                "status": "succeeded",
                "paid_admission": "rejected",
                "cross_org": "hidden",
                "missing_org": "hidden",
            }
        )
    )


@pytest.mark.parametrize("kind", ["", "worker"])
async def test_actorless_context_cannot_claim_a_submitter(
    api, headers, application, tenants, pdf_bytes, kind
):
    task, document = await create_document(api, headers[0], pdf_bytes)
    org, user = tenants["orgs"][0], tenants["users"][0]
    with pytest.raises(DBAPIError, match="authenticated job actor required"):
        async with application.state.db.transaction(org) as session:
            await session.execute(
                text(
                    "SELECT set_config('app.actor_kind',:kind,true), "
                    "set_config('app.actor_user_id','',true)"
                ),
                {"kind": kind},
            )
            session.add(
                local_job(
                    org,
                    task,
                    document,
                    actor_user_id=user,
                    actor_kind="session",
                    actor_scopes=["req:extract"],
                )
            )
            await session.flush()


async def test_job_actor_snapshot_is_truthful_and_immutable(
    api, headers, application, tenants, pdf_bytes
):
    task, document = await create_document(api, headers[0], pdf_bytes)
    org, user = tenants["orgs"][0], tenants["users"][0]
    actor = Identity(user, org, ROLE_SCOPES["admin"], "admin")
    async with application.state.db.transaction(org) as session:
        await set_actor_context(session, actor)
        job = local_job(org, task, document)
        session.add(job)
        await session.flush()
        await session.refresh(job)
        assert job.actor_user_id == user and job.actor_kind == "session"
        assert set(job.actor_scopes) == actor.scopes
    with pytest.raises(DBAPIError, match="job actor is immutable"):
        async with application.state.db.transaction(org) as session:
            await session.execute(
                text("UPDATE jobs SET actor_scopes='[]' WHERE id=:id"), {"id": job.id}
            )
    with pytest.raises(DBAPIError, match="job actor is not active"):
        async with application.state.db.transaction(org) as session:
            await set_actor_context(
                session, Identity(tenants["users"][1], org, actor.scopes, "admin")
            )
            session.add(local_job(org, task, document))
            await session.flush()
