"""Failure-first PostgreSQL/API acceptance for the product management slice.

Risks covered before implementation: org/parent confusion, unbounded reads,
prefix syntax injection, stale/foreign/expired cursors, unavailable authority,
incorrect revision authors/provenance, independent lifecycle CAS, human gate,
direct SQL bypass/immutability, and concurrent transitions. These tests use the
real restricted PostgreSQL role and produce normal pytest/JUnit artifacts.
"""

import asyncio
import json
import time
from uuid import UUID, uuid4

import pytest
from app.models.entities import AuditLog, Job, Membership, Org, SimulatedResource, User
from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

BASE = "/v4/management/resources/products"
DATA = {"name": "Synthetic alpha product", "vendor": "Acme", "model": "AB-20"}


async def create(api, headers, **fields):
    response = await api.post(
        "/resources/products", headers=headers, json={"data": {**DATA, **fields}}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def lifecycle(revision=1, lifecycle_revision=0, state="inactive"):
    return {
        "expected_revision": revision,
        "expected_lifecycle_revision": lifecycle_revision,
        "state": state,
        "reason_code": "restored" if state == "active" else "obsolete",
    }


async def test_bounded_prefix_pages_and_exact_details(api, headers, tenants):
    products = [await create(api, headers[0], name=f"Synthetic alpha {i}") for i in range(4)]
    await create(api, headers[1])
    response = await api.post(BASE + "/query", headers=headers[0], json={"q": "ALP", "limit": 2})
    assert response.status_code == 200
    page = response.json()
    assert set(page) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    assert set(page["data"]) == {"org_id", "as_of", "returned", "next_cursor", "has_more"}
    assert page["data"]["returned"] == 2 and page["data"]["has_more"]
    assert len(response.content) <= 256 * 1024
    assert all(row["org_id"] == str(tenants["orgs"][0]) for row in page["items"])
    next_page = await api.post(
        BASE + "/query",
        headers=headers[0],
        json={"q": "alp", "limit": 2, "cursor": page["data"]["next_cursor"]},
    )
    assert next_page.status_code == 200
    assert not next_page.json()["data"]["has_more"]
    assert (
        len({row["ref"]["resource_id"] for row in page["items"] + next_page.json()["items"]}) == 4
    )
    for query in ({"q": "AB"}, {"q": "acm"}):
        assert (await api.post(BASE + "/query", headers=headers[0], json=query)).json()["data"][
            "returned"
        ] == 4
    # Search syntax is not a tsquery program: punctuation cannot select everything.
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"q": "alpha | absent"})
    ).json()["items"] == []
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"product_id": str(uuid4())})
    ).status_code == 422
    product = products[0]
    root = product["product_id"]
    update = await api.post(
        f"/resources/products/{root}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "data": {**DATA, "name": "Revision two"}},
    )
    assert update.status_code == 200
    detail = await api.get(f"{BASE}/{root}", headers=headers[0], params={"revision": 1})
    assert detail.status_code == 200
    assert detail.json()["data"]["current_revision"] == 2
    assert detail.json()["data"]["detail"]["revision"]["id"] == product["id"]
    history = await api.post(f"{BASE}/{root}/history/query", headers=headers[0], json={"limit": 1})
    assert history.json()["items"][0]["revision"] == 2
    assert history.json()["items"][0]["created_by"] == str(tenants["users"][0])
    assert history.json()["items"][0]["has_file"] is False


@pytest.mark.parametrize(
    "suffix,body",
    [
        ("", {}),
        ("history/query", {}),
        ("lifecycle/history/query", {}),
        ("lifecycle", lifecycle()),
    ],
)
async def test_every_object_route_masks_foreign_and_missing(suffix, body, api, headers):
    foreign = await create(api, headers[1])
    replies = []
    for root in (foreign["product_id"], str(uuid4())):
        replies.append(
            await api.post(f"{BASE}/{root}/{suffix}", headers=headers[0], json=body)
            if suffix
            else await api.get(f"{BASE}/{root}", headers=headers[0])
        )
    assert [reply.status_code for reply in replies] == [404, 404]
    assert replies[0].json()["data"]["error"] == replies[1].json()["data"]["error"]


async def test_cursor_actor_filters_expiry_and_live_membership(
    api, headers, tenants, admin_engine, application
):
    for i in range(3):
        await create(api, headers[0], name=f"Alpha {i}")
    page = (await api.post(BASE + "/query", headers=headers[0], json={"limit": 1})).json()
    cursor = page["data"]["next_cursor"]
    for hdr, body in [
        (headers[1], {"limit": 1, "cursor": cursor}),
        (headers[0], {"limit": 1, "cursor": cursor, "q": "alpha"}),
        (headers[0], {"cursor": "broken"}),
    ]:
        result = await api.post(BASE + "/query", headers=hdr, json=body)
        assert result.status_code == 400
        assert result.json()["data"]["error"]["code"] == "management_cursor_invalid"
    signer = application.state.crypto
    payload = json.loads(signer.cipher.decrypt(cursor.encode()))
    payload["exp"] = int(time.time()) - 1
    expired = signer.cipher.encrypt(json.dumps(payload).encode()).decode()
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"limit": 1, "cursor": expired})
    ).status_code == 409
    with Session(admin_engine) as session, session.begin():
        session.scalar(
            select(Membership).where(Membership.org_id == tenants["orgs"][0])
        ).role = "viewer"
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"limit": 1, "cursor": cursor})
    ).status_code == 400


@pytest.mark.parametrize(
    "role,allowed", [("admin", True), ("technical", True), ("bidder", False), ("viewer", False)]
)
async def test_lifecycle_roles_and_content_independence(
    role, allowed, api, headers, tenants, admin_engine
):
    product = await create(api, headers[0])
    root = product["product_id"]
    with Session(admin_engine) as session, session.begin():
        session.scalar(
            select(Membership).where(Membership.org_id == tenants["orgs"][0])
        ).role = role
    assert (await api.post(BASE + "/query", headers=headers[0], json={})).status_code == 200
    response = await api.post(f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle())
    assert response.status_code == (200 if allowed else 403)
    if not allowed:
        return
    assert response.json()["data"]["existing_selections"] == "preserved"
    assert response.json()["data"]["lifecycle"] == {"state": "inactive", "revision": 1}
    assert (await api.post(BASE + "/query", headers=headers[0], json={})).json()["items"] == []
    assert (await api.post(BASE + "/query", headers=headers[0], json={"state": "inactive"})).json()[
        "data"
    ]["returned"] == 1
    detail = (await api.get(f"{BASE}/{root}", headers=headers[0])).json()["data"]
    assert detail["detail"]["revision"]["id"] == product["id"]
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle())
    ).status_code == 409
    revision = await api.post(
        f"/resources/products/{root}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "data": {**DATA, "name": "Inactive edited"}},
    )
    assert revision.status_code == 200
    assert (
        await api.post(
            f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle(2, 1, "active")
        )
    ).status_code == 200
    events = (
        await api.post(f"{BASE}/{root}/lifecycle/history/query", headers=headers[0], json={})
    ).json()["items"]
    assert [event["revision"] for event in events] == [2, 1]
    assert events[0]["resource_revision"] == 2


async def test_token_read_but_no_lifecycle_and_concurrent_cas(api, headers):
    from datetime import UTC, datetime, timedelta

    product = await create(api, headers[0])
    token = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic management token",
            "scopes": ["resource:read", "resource:write"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert token.status_code == 200
    token_headers = {**headers[0], "Authorization": "Bearer " + token.json()["data"]["token"]}
    assert (await api.post(BASE + "/query", headers=token_headers, json={})).status_code == 200
    path = f"{BASE}/{product['product_id']}/lifecycle"
    assert (await api.post(path, headers=token_headers, json=lifecycle())).status_code == 403
    results = await asyncio.gather(
        *(api.post(path, headers=headers[0], json=lifecycle()) for _ in range(2))
    )
    assert sorted(reply.status_code for reply in results) == [200, 409]


async def test_exact_audit_author_unknown_if_ambiguous(api, headers, tenants, admin_engine):
    product = await create(api, headers[0])
    with Session(admin_engine) as session, session.begin():
        session.add(
            AuditLog(
                org_id=tenants["orgs"][0],
                actor_user_id=tenants["users"][0],
                action="resource.product.create",
                object_id=UUID(product["product_id"]),
                details={"new_revision_id": product["id"], "revision": 1},
            )
        )
    page = (await api.post(BASE + "/query", headers=headers[0], json={})).json()
    assert page["items"][0]["revised_by"] is None


async def test_lifecycle_table_rls_immutable_and_transition_guard(
    api, headers, tenants, application
):
    product = await create(api, headers[0])
    await api.post(
        f"{BASE}/{product['product_id']}/lifecycle", headers=headers[0], json=lifecycle()
    )
    async with application.state.db.transaction(tenants["orgs"][1]) as session:
        assert await session.scalar(text("SELECT count(*) FROM resource_lifecycle_events")) == 0
    async with application.state.db.transaction() as session:
        assert await session.scalar(text("SELECT count(*) FROM resource_lifecycle_events")) == 0
    for sql in [
        "UPDATE resource_lifecycle_events SET reason_code='other'",
        "DELETE FROM resource_lifecycle_events",
        "UPDATE products SET lifecycle_state='active',lifecycle_revision=2 WHERE id=:root",
    ]:
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction(tenants["orgs"][0]) as session:
                await session.execute(text(sql), {"root": UUID(product["product_id"])})
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        forced = await session.scalar(
            text(
                "SELECT relforcerowsecurity FROM pg_class WHERE oid='resource_lifecycle_events'::regclass"
            )
        )
        assert forced


async def test_complete_http_body_bound_and_literal_unicode_prefix(api, headers):
    product = await create(api, headers[0], name="Straße device", model="AB-20 ABC/DEF")
    for value in ("STRASSE", "straße", "AB-2", "stras ab", "ABC/DEF"):
        page = await api.post(BASE + "/query", headers=headers[0], json={"q": value})
        assert page.status_code == 200, page.text
        assert [row["ref"]["resource_id"] for row in page.json()["items"]] == [
            product["product_id"]
        ]
    response = await api.post(
        BASE + "/query",
        headers={**headers[0], "Content-Type": "application/json"},
        content=b" " * (16 * 1024) + b"{}",
    )
    assert response.status_code == 413
    assert response.json()["data"]["error"]["code"] == "invalid_input"
    assert (
        await api.post(BASE.replace("/v4", "") + "/query", headers=headers[0], json={})
    ).status_code == 404


async def test_maximum_unicode_search_keeps_a_bounded_usable_cursor(api, headers):
    # JSON Unicode escaping must not make a valid 200-character filter produce
    # a cursor beyond the 2048-character contract limit.
    query = "汉" * 200
    products = [await create(api, headers[0], name=query) for _ in range(2)]
    first = await api.post(BASE + "/query", headers=headers[0], json={"q": query, "limit": 1})
    assert first.status_code == 200, first.text
    cursor = first.json()["data"]["next_cursor"]
    assert cursor and len(cursor) <= 2048
    second = await api.post(
        BASE + "/query", headers=headers[0], json={"q": query, "limit": 1, "cursor": cursor}
    )
    assert second.status_code == 200, second.text
    assert {
        row["ref"]["resource_id"] for row in first.json()["items"] + second.json()["items"]
    } == {product["product_id"] for product in products}


async def test_same_org_parent_cursor_and_exact_detail_author(api, headers, tenants):
    first = await create(api, headers[0])
    second = await create(api, headers[0])
    root = first["product_id"]
    for revision in range(1, 28):
        reply = await api.post(
            f"/resources/products/{root}/revisions",
            headers=headers[0],
            json={
                "expected_revision": revision,
                "data": {**DATA, "name": f"Revision {revision + 1}"},
            },
        )
        assert reply.status_code == 200
    page = (
        await api.post(f"{BASE}/{root}/history/query", headers=headers[0], json={"limit": 1})
    ).json()
    bad = await api.post(
        f"{BASE}/{second['product_id']}/history/query",
        headers=headers[0],
        json={"limit": 1, "cursor": page["data"]["next_cursor"]},
    )
    assert bad.status_code == 400
    detail = (await api.get(f"{BASE}/{root}", headers=headers[0], params={"revision": 1})).json()[
        "data"
    ]
    assert detail["revised_by"] == str(tenants["users"][0])
    assert detail["revised_at"] and detail["detail"]["revision"]["id"] == first["id"]
    assert (
        await api.get(f"{BASE}/{second['product_id']}", headers=headers[0], params={"revision": 28})
    ).status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        "foreign_org",
        "foreign_root",
        "foreign_actor",
        "wrong_revision",
        "wrong_sequence",
        "no_context",
        "token",
        "role",
        "missing_audit",
    ],
)
async def test_direct_event_inserts_cannot_bypass_isolation_or_authority(
    change, api, headers, tenants, application, admin_engine
):
    first, foreign = await create(api, headers[0]), await create(api, headers[1])
    org, user = tenants["orgs"][0], tenants["users"][0]
    actor = Identity(user, org, set(ROLE_SCOPES["admin"]), "admin")
    params = {
        "id": uuid4(),
        "org": org,
        "root": UUID(first["product_id"]),
        "actor": user,
        "version": 1,
        "sequence": 1,
    }
    if change == "foreign_org":
        params["org"] = tenants["orgs"][1]
    if change == "foreign_root":
        params["root"] = UUID(foreign["product_id"])
    if change == "foreign_actor":
        params["actor"] = tenants["users"][1]
    if change == "wrong_revision":
        params["version"] = 2
    if change == "wrong_sequence":
        params["sequence"] = 2
    if change == "token":
        actor.actor_kind = "token"
    if change == "role":
        with Session(admin_engine) as session, session.begin():
            session.scalar(select(Membership).where(Membership.org_id == org)).role = "viewer"
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction(
            None if change == "no_context" else org
        ) as session:
            await set_actor_context(session, actor)
            await session.execute(
                text(
                    "INSERT INTO resource_lifecycle_events(id,org_id,product_id,revision,resource_revision,before_state,after_state,reason_code,actor_user_id) VALUES(:id,:org,:root,:sequence,:version,'active','inactive','obsolete',:actor)"
                ),
                params,
            )
    async with application.state.db.transaction(org) as session:
        assert (
            await session.scalar(
                text("SELECT lifecycle_revision FROM products WHERE id=:root"),
                {"root": UUID(first["product_id"])},
            )
            == 0
        )
        assert await session.scalar(text("SELECT count(*) FROM resource_lifecycle_events")) == 0


async def test_direct_inactive_pin_insert_and_reactivation_refused(
    api, headers, tenants, application
):
    product = await create(api, headers[0])
    task = (
        await api.post("/tasks", headers=headers[0], json={"name": "Synthetic SQL pin task"})
    ).json()["data"]["id"]
    pin = await api.post(
        f"/tasks/{task}/products", headers=headers[0], json={"product_id": product["product_id"]}
    )
    assert pin.status_code == 200
    await api.post(
        f"{BASE}/{product['product_id']}/lifecycle", headers=headers[0], json=lifecycle()
    )
    org, user = tenants["orgs"][0], tenants["users"][0]
    actor = Identity(user, org, set(ROLE_SCOPES["admin"]), "admin")
    async with application.state.db.transaction(org) as session:
        await set_actor_context(session, actor)
        await session.execute(
            text("UPDATE task_resources SET active=false WHERE id=:pin"),
            {"pin": UUID(pin.json()["data"]["id"])},
        )
    for sql in (
        "UPDATE task_resources SET active=true WHERE id=:pin",
        "INSERT INTO task_resources(id,org_id,task_id,product_id,product_revision_id,lot,active) VALUES(:id,:org,:task,:root,:revision,'new-lot',true)",
    ):
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction(org) as session:
                await set_actor_context(session, actor)
                await session.execute(
                    text(sql),
                    {
                        "id": uuid4(),
                        "org": org,
                        "task": UUID(task),
                        "root": UUID(product["product_id"]),
                        "revision": UUID(product["id"]),
                        "pin": UUID(pin.json()["data"]["id"]),
                    },
                )


async def test_root_simulated_provenance_applies_to_old_revisions(
    api, headers, tenants, admin_engine, pdf_bytes
):
    product = await create(api, headers[0])
    task = await api.post("/tasks", headers=headers[0], json={"name": "Synthetic provenance task"})
    assert task.status_code == 200, task.text
    task_id = task.json()["data"]["id"]
    document = await api.post(
        f"/tasks/{task_id}/documents",
        headers=headers[0],
        files={"file": ("synthetic-provenance.pdf", pdf_bytes, "application/pdf")},
    )
    assert document.status_code == 200, document.text
    org, user = tenants["orgs"][0], tenants["users"][0]
    with Session(admin_engine) as session, session.begin():
        # Product simulation is task/document-bound even when this fixture only
        # needs its retained provenance marker and does not execute the job.
        job = Job(
            org_id=org,
            task_id=UUID(task_id),
            document_id=UUID(document.json()["data"]["id"]),
            kind="product_simulate",
            cache_key="a" * 64,
            actor_user_id=user,
        )
        session.add(job)
        session.flush()
        session.add(
            SimulatedResource(org_id=org, product_id=UUID(product["product_id"]), job_id=job.id)
        )
    reply = await api.post(
        f"/resources/products/{product['product_id']}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "data": {**DATA, "name": "Changed simulated"}},
    )
    assert reply.status_code == 200
    page = (await api.post(BASE + "/query", headers=headers[0], json={})).json()
    assert page["items"][0]["provenance"] == "simulated"
    detail = (
        await api.get(f"{BASE}/{product['product_id']}", headers=headers[0], params={"revision": 1})
    ).json()["data"]
    assert detail["provenance"] == "simulated"


@pytest.mark.parametrize("new_parents", [False, True])
async def test_valid_pin_relations_still_require_actor_after_fk_checks(
    new_parents, api, headers, tenants, application
):
    """FK-first rejection must never turn missing-parent lookups into an ACL bypass.

    Valid parents, including those inserted by the same SQL statement, must reach
    the selection authority gate. Its failure rolls back the entire statement.
    """
    product = await create(api, headers[0])
    task = await api.post("/tasks", headers=headers[0], json={"name": "Synthetic SQL guard task"})
    assert task.status_code == 200, task.text
    params = {
        "org": tenants["orgs"][0],
        "user": tenants["users"][0],
        "task": UUID(task.json()["data"]["id"]),
        "root": uuid4() if new_parents else UUID(product["product_id"]),
        "revision": uuid4() if new_parents else UUID(product["id"]),
        "pin": uuid4(),
    }
    statement = (
        "WITH new_product AS ("
        " INSERT INTO products(id,org_id,created_by,current_revision)"
        " VALUES(:root,:org,:user,1) RETURNING id"
        "), new_revision AS ("
        " INSERT INTO product_revisions(id,org_id,product_id,revision,data)"
        " SELECT :revision,:org,new_product.id,1,'{}'::jsonb FROM new_product"
        " RETURNING id,product_id"
        ") INSERT INTO task_resources(id,org_id,task_id,product_id,product_revision_id,lot,active)"
        " SELECT :pin,:org,:task,new_revision.product_id,new_revision.id,'guard-check',true"
        " FROM new_revision"
        if new_parents
        else "INSERT INTO task_resources(id,org_id,task_id,product_id,product_revision_id,lot,active)"
        " VALUES(:pin,:org,:task,:root,:revision,'guard-check',true)"
    )
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(params["org"]) as session:
            await session.execute(text(statement), params)
    assert failure.value.orig.sqlstate == "42501"
    async with application.state.db.transaction(params["org"]) as session:
        assert (
            await session.scalar(text("SELECT id FROM task_resources WHERE id=:pin"), params)
            is None
        )
        if new_parents:
            assert (
                await session.scalar(text("SELECT id FROM products WHERE id=:root"), params) is None
            )


def test_privileged_product_insert_without_org_context_keeps_baseline_guard(tenants, admin_engine):
    """RLS deferral applies only when RLS will actually reject the row."""
    with pytest.raises(DBAPIError) as failure, admin_engine.begin() as connection:
        connection.execute(text("SELECT set_config('app.current_org','',true)"))
        assert not connection.scalar(text("SELECT row_security_active('products'::regclass)"))
        connection.execute(
            text(
                "INSERT INTO products(id,org_id,created_by,current_revision,lifecycle_state,lifecycle_revision)"
                " VALUES(:id,:org,:user,1,'inactive',1)"
            ),
            {"id": uuid4(), "org": tenants["orgs"][0], "user": tenants["users"][0]},
        )
    assert failure.value.orig.sqlstate == "23514"


@pytest.mark.parametrize("disabled,expected", [("user", 401), ("membership", 404), ("org", 403)])
async def test_joined_read_auth_retains_identity_membership_org_denials(
    disabled, expected, api, headers, tenants, admin_engine
):
    """Joining auth cannot alter failures or reuse an earlier request's authority."""
    product = await create(api, headers[0])
    root = product["product_id"]
    assert (await api.post(BASE + "/query", headers=headers[0], json={})).status_code == 200
    with Session(admin_engine) as session, session.begin():
        if disabled == "user":
            session.get(User, tenants["users"][0]).active = False
        elif disabled == "membership":
            session.scalar(
                select(Membership).where(Membership.org_id == tenants["orgs"][0])
            ).active = False
        else:
            session.get(Org, tenants["orgs"][0]).active = False
    # Each new read independently authenticates; old all-row auth also keeps its semantics.
    assert (await api.get("/resources/products", headers=headers[0])).status_code == expected
    assert (await api.get(f"{BASE}/{root}", headers=headers[0])).status_code == expected
    for path in (
        BASE + "/query",
        f"{BASE}/{root}/history/query",
        f"{BASE}/{root}/lifecycle/history/query",
    ):
        response = await api.post(path, headers=headers[0], json={})
        assert response.status_code == expected, response.text


async def test_read_auth_marker_is_consumed_and_direct_service_reauthorizes(
    api, headers, tenants, application, admin_engine
):
    from app.core.errors import ServiceError
    from app.schemas.management_pages import ResourceQuery
    from app.services.auth import authenticate
    from app.services.management_products import query

    await create(api, headers[0])
    org = tenants["orgs"][0]
    async with application.state.db.transaction(org) as session:
        actor = await authenticate(
            session,
            headers[0]["Authorization"].removeprefix("Bearer "),
            org,
            application.state.crypto,
            joined_membership=True,
        )
        session.info["management_authenticated_actor"] = actor
        assert (await query(session, actor, ResourceQuery())).data.returned == 1
        assert "management_authenticated_actor" not in session.info
        with Session(admin_engine) as owner, owner.begin():
            owner.scalar(select(Membership).where(Membership.org_id == org)).active = False
        with pytest.raises(ServiceError) as failure:
            await query(session, actor, ResourceQuery())
        assert failure.value.code == "not_found"
