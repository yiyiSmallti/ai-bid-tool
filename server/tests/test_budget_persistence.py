"""DB-backed budgets: stale revisions, tenant escape, forged history and repeat alerts."""

from decimal import Decimal

import pytest
from app.models.entities import OrgBalance, TaskBudgetRevision
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError


async def create_budget_task(api, header, limit="10.00000000"):
    response = await api.post(
        "/v4/tasks",
        headers=header,
        json={
            "name": "Synthetic budget task",
            "budget": {"limit": limit, "currency": "USD"},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


async def test_budget_revision_history_and_cross_tenant_api(api, headers):
    task = await create_budget_task(api, headers[0])
    read = await api.get(f"/v4/tasks/{task}/budget", headers=headers[0])
    budget = read.json()["data"]["budget"]
    assert budget["revision"] == 1
    assert Decimal(budget["available"]) == Decimal(10)
    change = {"limit": "12", "currency": "USD", "expected_revision": 1, "reason": "One more pass"}
    response = await api.put(f"/v4/tasks/{task}/budget", headers=headers[0], json=change)
    assert response.status_code == 200
    assert response.json()["data"]["budget"]["revision"] == 2
    stale = await api.put(f"/v4/tasks/{task}/budget", headers=headers[0], json=change)
    assert stale.status_code == 409
    history = await api.get(f"/v4/tasks/{task}/budget/history?limit=1", headers=headers[0])
    assert history.json()["items"][0]["revision"] == 2
    assert history.json()["data"]["next_before_revision"] == 2
    for method, suffix, body in (("GET", "", None), ("GET", "/history", None), ("PUT", "", change)):
        denied = await api.request(
            method, f"/v4/tasks/{task}/budget{suffix}", headers=headers[1], json=body
        )
        assert denied.status_code == 404


async def test_budget_history_rls_and_immutability(application, headers, api, tenants):
    task = await create_budget_task(api, headers[0])
    async with application.state.db.transaction(tenants["orgs"][1]) as session:
        assert not (await session.scalars(select(TaskBudgetRevision))).all()
    async with application.state.db.transaction() as session:
        assert not (await session.scalars(select(TaskBudgetRevision))).all()
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await session.execute(
                text("UPDATE task_budget_revisions SET reason_sha256=:v WHERE task_id=:t"),
                {"t": task, "v": "0" * 64},
            )


async def test_balance_notification_cycle_and_platform_function(api, application, headers, tenants):
    org = tenants["orgs"][0]
    async with application.state.db.transaction() as session:
        await session.execute(
            text(
                "SELECT * FROM platform_adjust_balance(:org,'add',20,'synthetic credit','ops','USD')"
            ),
            {"org": org},
        )
    changed = await api.put(
        "/v4/billing/low-balance-policy",
        headers=headers[0],
        json={
            "threshold": "5",
            "currency": "USD",
            "expected_revision": 1,
        },
    )
    assert changed.status_code == 200
    for amount in (-16, -1, 10, -10):
        async with application.state.db.transaction() as session:
            await session.execute(
                text(
                    "SELECT * FROM platform_adjust_balance(:org,'add',CAST(:amount AS numeric),"
                    "'synthetic adjustment','ops','USD')"
                ),
                {"org": org, "amount": amount},
            )
    notices = await api.get("/v4/billing/notices", headers=headers[0])
    assert notices.status_code == 200
    assert [item["cycle"] for item in notices.json()["items"]] == [2, 1]
    assert (await api.get("/v4/billing/notices", headers=headers[1])).json()["items"] == []
    cursor = notices.json()["items"][0]["id"]
    assert (
        await api.get(f"/v4/billing/notices?before={cursor}", headers=headers[1])
    ).status_code == 404
    async with application.state.db.transaction(org) as session:
        balance = await session.get(OrgBalance, org)
        assert balance is not None and balance.low_balance_active


async def test_budget_token_creation_and_human_role_gate(
    api, headers, application, tenants, admin_engine
):
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from app.core.errors import ServiceError
    from app.models.entities import Membership
    from app.schemas.budget_contracts import TaskBudgetSet
    from app.services import budgets
    from app.services.auth import ROLE_SCOPES, Identity
    from sqlalchemy.orm import Session

    task = await create_budget_task(api, headers[0])
    expiry = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    for forbidden in ("task:budget:write", "billing:alert:write"):
        response = await api.post(
            "/tokens",
            headers=headers[0],
            json={
                "name": "Synthetic forbidden scope",
                "scopes": [forbidden],
                "expires_at": expiry,
            },
        )
        assert response.status_code == 403
    response = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic task token",
            "scopes": ["task:read", "task:create"],
            "expires_at": expiry,
        },
    )
    assert response.status_code == 200
    token_header = {**headers[0], "Authorization": "Bearer " + response.json()["data"]["token"]}
    assert (await api.get(f"/v4/tasks/{task}/budget", headers=token_header)).status_code == 200
    body = {"limit": None, "currency": "USD", "expected_revision": 1, "reason": "Remove cap"}
    assert (
        await api.put(f"/v4/tasks/{task}/budget", headers=token_header, json=body)
    ).status_code == 403
    assert (
        await api.post(
            "/v4/tasks",
            headers=token_header,
            json={
                "name": "Synthetic token capped task",
                "budget": {"limit": "2", "currency": "USD"},
            },
        )
    ).status_code == 403
    assert (
        await api.post("/v4/tasks", headers=token_header, json={"name": "Synthetic uncapped task"})
    ).status_code == 200
    actor = Identity(
        tenants["users"][0], tenants["orgs"][0], ROLE_SCOPES["admin"], "admin", actor_kind="agent"
    )
    with pytest.raises(ServiceError, match="human"):
        async with application.state.db.transaction(actor.org_id) as session:
            await budgets.set_budget(session, actor, UUID(task), TaskBudgetSet.model_validate(body))
    for role in ("technical", "viewer", "bidder"):
        with Session(admin_engine) as session, session.begin():
            member = session.scalar(
                select(Membership).where(Membership.org_id == tenants["orgs"][0])
            )
            assert member is not None
            member.role = role
        response = await api.put(f"/v4/tasks/{task}/budget", headers=headers[0], json=body)
        assert response.status_code == (200 if role == "bidder" else 403)
    policy = await api.put(
        "/v4/billing/low-balance-policy",
        headers=headers[0],
        json={
            "threshold": None,
            "currency": "USD",
            "expected_revision": 1,
        },
    )
    assert policy.status_code == 403


async def test_budget_reservation_reduction_and_settlement_do_not_duplicate_notice(
    api, headers, application, tenants, pdf_bytes
):
    from uuid import UUID, uuid4

    from app.models.entities import Job, UsageRecord, VendorCall
    from app.schemas.budget_contracts import BudgetCallQuote
    from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
    from test_api import create_document

    org, user = tenants["orgs"][0], tenants["users"][0]
    task, document = await create_document(api, headers[0], pdf_bytes)
    changed = await api.put(
        f"/v4/tasks/{task}/budget",
        headers=headers[0],
        json={
            "limit": "20",
            "currency": "USD",
            "expected_revision": 1,
            "reason": "Synthetic budget",
        },
    )
    assert changed.status_code == 200
    async with application.state.db.transaction() as session:
        await session.execute(
            text("SELECT * FROM platform_adjust_balance(:org,'add',10,'credit','ops','USD')"),
            {"org": org},
        )
    assert (
        await api.put(
            "/v4/billing/low-balance-policy",
            headers=headers[0],
            json={
                "threshold": "2",
                "currency": "USD",
                "expected_revision": 1,
            },
        )
    ).status_code == 200
    run_id, call_id, job_id = uuid4(), uuid4(), uuid4()
    quote = BudgetCallQuote(
        capability="llm",
        payer="org_platform",
        provider="synthetic",
        model="synthetic",
        version="v1",
        platform_model_id="synthetic",
        price_revision="v1",
        request_sha256="1" * 64,
        currency="USD",
        reserved_charge=Decimal(9),
        reserved_task_amount=Decimal(9),
        vendor_usd_upper_bound=Decimal(1),
    )
    async with application.state.db.transaction(org) as session:
        await set_actor_context(session, Identity(user, org, ROLE_SCOPES["admin"], "admin"))
        session.add(
            Job(
                id=job_id,
                org_id=org,
                task_id=UUID(task),
                document_id=UUID(document),
                kind="extract",
                cache_key=uuid4().hex,
                run_id=run_id,
            )
        )
        await session.flush()
        session.add(
            VendorCall(
                id=call_id,
                org_id=org,
                task_id=UUID(task),
                job_id=job_id,
                run_id=run_id,
                budget_revision=2,
                reserved_charge=Decimal(9),
                reserved_task_amount=Decimal(9),
                capability=quote.capability,
                payer=quote.payer,
                currency=quote.currency,
                price_revision=quote.price_revision,
                request_sha256=quote.request_sha256,
                quote=quote.model_dump(mode="json"),
            )
        )
    notices = (await api.get("/v4/billing/notices", headers=headers[0])).json()["items"]
    assert len(notices) == 1
    other_task, other_document = await create_document(api, headers[0], pdf_bytes)
    with pytest.raises(DBAPIError, match="job with admitted calls"):
        async with application.state.db.transaction(org) as session:
            await session.execute(
                text("UPDATE jobs SET task_id=:task,document_id=:document WHERE id=:job"),
                {"task": UUID(other_task), "document": UUID(other_document), "job": job_id},
            )
    with pytest.raises(DBAPIError, match="job with admitted calls"):
        async with application.state.db.transaction(org) as session:
            await session.execute(
                text("UPDATE jobs SET provider_identity='{}'::jsonb WHERE id=:job"),
                {"job": job_id},
            )
    with pytest.raises(DBAPIError, match="current task budget revision"):
        async with application.state.db.transaction(org) as session:
            session.add(
                VendorCall(
                    id=uuid4(),
                    org_id=org,
                    task_id=UUID(task),
                    job_id=job_id,
                    run_id=run_id,
                    budget_revision=1,
                    reserved_charge=Decimal(9),
                    reserved_task_amount=Decimal(9),
                    capability=quote.capability,
                    payer=quote.payer,
                    currency=quote.currency,
                    price_revision=quote.price_revision,
                    request_sha256=quote.request_sha256,
                    quote=quote.model_dump(mode="json"),
                )
            )
            await session.flush()
    async with application.state.db.transaction(org) as session:
        unchanged = await session.get(Job, job_id)
        assert unchanged is not None
        assert unchanged.task_id == UUID(task) and unchanged.document_id == UUID(document)
        assert unchanged.provider_identity is None
    denied = await api.put(
        f"/v4/tasks/{task}/budget",
        headers=headers[0],
        json={
            "limit": "8.99999999",
            "currency": "USD",
            "expected_revision": 2,
            "reason": "Too low",
        },
    )
    assert denied.status_code == 409
    async with application.state.db.transaction(org) as session:
        call = await session.get(VendorCall, call_id)
        assert call is not None
        call.state, call.charge = "completed", Decimal(9)
        session.add(
            UsageRecord(
                org_id=org,
                task_id=UUID(task),
                job_id=job_id,
                run_id=run_id,
                call_id=call_id,
                provider="synthetic",
                model="synthetic",
                version="v1",
                duration_ms=1,
                capability="llm",
                payer="org_platform",
                billing_currency="USD",
                price_revision="v1",
                platform_model_id="synthetic",
                charge=Decimal(9),
                task_amount=Decimal(9),
                usd=Decimal(1),
            )
        )
        await session.flush()
        balance = await session.get(OrgBalance, org)
        assert balance is not None
        balance.balance -= Decimal(9)
    final_notices = (await api.get("/v4/billing/notices", headers=headers[0])).json()["items"]
    assert [row["id"] for row in final_notices] == [row["id"] for row in notices]
    read = (await api.get(f"/v4/tasks/{task}/budget", headers=headers[0])).json()["data"]["budget"]
    assert Decimal(read["spent"]) == 9 and Decimal(read["reserved"]) == 0


async def test_budget_migration_rls_parent_keys_and_rollback(api, headers, application, tenants):
    from uuid import UUID

    from app.models.entities import Task

    task = UUID(await create_budget_task(api, headers[0]))
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        tables = (
            await session.execute(
                text(
                    "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class "
                    "WHERE oid IN ('task_budget_revisions'::regclass,'org_balance_notices'::regclass)"
                )
            )
        ).all()
        assert len(tables) == 2 and all(
            row.relrowsecurity and row.relforcerowsecurity for row in tables
        )
        forbidden = await session.scalar(
            text(
                "SELECT has_table_privilege('bid_app','task_budget_revisions','UPDATE') OR "
                "has_table_privilege('bid_app','org_balance_notices','DELETE') OR "
                "has_table_privilege('bid_platform_fn','org_balance_notices','SELECT')"
            )
        )
        assert forbidden is False
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await session.execute(
                text(
                    "INSERT INTO task_budget_revisions(org_id,task_id,revision,currency,state,origin) "
                    "VALUES(:org,:task,2,'USD','active','migration')"
                ),
                {"org": tenants["orgs"][0], "task": task},
            )
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await session.execute(
                text("UPDATE tasks SET budget_limit=99,budget_revision=2 WHERE id=:task"),
                {"task": task},
            )
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        row = await session.get(Task, task)
        assert row is not None and row.budget_revision == 1 and row.budget_limit == Decimal(10)
    for header in headers:
        assert (await api.get("/v4/billing/low-balance-policy", headers=header)).status_code == 200
        assert (await api.get("/v4/billing/notices", headers=header)).status_code == 200
    high_precision = await api.put(
        f"/v4/tasks/{task}/budget",
        headers=headers[0],
        json={
            "limit": "1000000000.12345678",
            "currency": "USD",
            "expected_revision": 1,
            "reason": "Eight digit precision",
        },
    )
    assert high_precision.status_code == 200
    assert high_precision.json()["data"]["budget"]["limit"] == "1000000000.12345678"
