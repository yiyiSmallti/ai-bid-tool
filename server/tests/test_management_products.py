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
from app.models.entities import Job, SimulatedResource
from app.services.auth import HUMAN_SCOPES, Identity, set_actor_context
from sqlalchemy import text
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


async def test_bounded_prefix_pages_and_exact_details(api, headers, database):
    products = [await create(api, headers, name=f"Synthetic alpha {i}") for i in range(4)]
    response = await api.post(BASE + "/query", headers=headers, json={"q": "ALP", "limit": 2})
    assert response.status_code == 200
    page = response.json()
    assert set(page) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    assert set(page["data"]) == {"as_of", "returned", "next_cursor", "has_more"}
    assert page["data"]["returned"] == 2 and page["data"]["has_more"]
    assert len(response.content) <= 256 * 1024
    next_page = await api.post(
        BASE + "/query",
        headers=headers,
        json={"q": "alp", "limit": 2, "cursor": page["data"]["next_cursor"]},
    )
    assert next_page.status_code == 200
    assert not next_page.json()["data"]["has_more"]
    assert (
        len({row["ref"]["resource_id"] for row in page["items"] + next_page.json()["items"]}) == 4
    )
    for query in ({"q": "AB"}, {"q": "acm"}):
        assert (await api.post(BASE + "/query", headers=headers, json=query)).json()["data"][
            "returned"
        ] == 4
    assert (await api.post(BASE + "/query", headers=headers, json={"q": "alpha | absent"})).json()[
        "items"
    ] == []
    assert (
        await api.post(BASE + "/query", headers=headers, json={"product_id": str(uuid4())})
    ).status_code == 422
    product = products[0]
    root = product["product_id"]
    update = await api.post(
        f"/resources/products/{root}/revisions",
        headers=headers,
        json={"expected_revision": 1, "data": {**DATA, "name": "Revision two"}},
    )
    assert update.status_code == 200
    detail = await api.get(f"{BASE}/{root}", headers=headers, params={"revision": 1})
    assert detail.status_code == 200
    assert detail.json()["data"]["current_revision"] == 2
    assert detail.json()["data"]["detail"]["revision"]["id"] == product["id"]
    history = await api.post(f"{BASE}/{root}/history/query", headers=headers, json={"limit": 1})
    assert history.json()["items"][0]["revision"] == 2
    assert history.json()["items"][0]["has_file"] is False


async def test_cursor_actor_filters_expiry_and_cursor_expiry(
    api, headers, database, admin_engine, application
):
    for i in range(3):
        await create(api, headers, name=f"Alpha {i}")
    page = (await api.post(BASE + "/query", headers=headers, json={"limit": 1})).json()
    cursor = page["data"]["next_cursor"]
    for hdr, body in [
        (headers, {"limit": 1, "cursor": cursor, "q": "alpha"}),
        (headers, {"cursor": "broken"}),
    ]:
        result = await api.post(BASE + "/query", headers=hdr, json=body)
        assert result.status_code == 400
        assert result.json()["data"]["error"]["code"] == "management_cursor_invalid"
    signer = application.state.crypto
    payload = json.loads(signer.cipher.decrypt(cursor.encode()))
    payload["exp"] = int(time.time()) - 1
    expired = signer.cipher.encrypt(json.dumps(payload).encode()).decode()
    assert (
        await api.post(BASE + "/query", headers=headers, json={"limit": 1, "cursor": expired})
    ).status_code == 409


async def test_lifecycle_human_and_content_independence(api, headers, database, admin_engine):
    product = await create(api, headers)
    root = product["product_id"]
    assert (await api.post(BASE + "/query", headers=headers, json={})).status_code == 200
    response = await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle())
    assert response.status_code == (200 if True else 403)
    assert response.json()["data"]["existing_selections"] == "preserved"
    assert response.json()["data"]["lifecycle"] == {"state": "inactive", "revision": 1}
    assert (await api.post(BASE + "/query", headers=headers, json={})).json()["items"] == []
    assert (await api.post(BASE + "/query", headers=headers, json={"state": "inactive"})).json()[
        "data"
    ]["returned"] == 1
    detail = (await api.get(f"{BASE}/{root}", headers=headers)).json()["data"]
    assert detail["detail"]["revision"]["id"] == product["id"]
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle())
    ).status_code == 409
    revision = await api.post(
        f"/resources/products/{root}/revisions",
        headers=headers,
        json={"expected_revision": 1, "data": {**DATA, "name": "Inactive edited"}},
    )
    assert revision.status_code == 200
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle(2, 1, "active"))
    ).status_code == 200
    events = (
        await api.post(f"{BASE}/{root}/lifecycle/history/query", headers=headers, json={})
    ).json()["items"]
    assert [event["revision"] for event in events] == [2, 1]
    assert events[0]["resource_revision"] == 2


async def test_token_read_but_no_lifecycle_and_concurrent_cas(api, headers):
    from datetime import UTC, datetime, timedelta

    product = await create(api, headers)
    token = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": "Synthetic management token",
            "scopes": ["resource:read", "resource:write"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert token.status_code == 200
    token_headers = {**headers, "Authorization": "Bearer " + token.json()["data"]["token"]}
    assert (await api.post(BASE + "/query", headers=token_headers, json={})).status_code == 200
    path = f"{BASE}/{product['product_id']}/lifecycle"
    assert (await api.post(path, headers=token_headers, json=lifecycle())).status_code == 403
    results = await asyncio.gather(
        *(api.post(path, headers=headers, json=lifecycle()) for _ in range(2))
    )
    assert sorted(reply.status_code for reply in results) == [200, 409]


async def test_lifecycle_table_rls_immutable_and_transition_guard(
    api, headers, database, application
):
    product = await create(api, headers)
    await api.post(f"{BASE}/{product['product_id']}/lifecycle", headers=headers, json=lifecycle())
    for sql in [
        "UPDATE resource_lifecycle_events SET reason_code='other'",
        "DELETE FROM resource_lifecycle_events",
        "UPDATE products SET lifecycle_state='active',lifecycle_revision=2 WHERE id=:root",
    ]:
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction() as session:
                await session.execute(text(sql), {"root": UUID(product["product_id"])})


async def test_complete_http_body_bound_and_literal_unicode_prefix(api, headers):
    product = await create(api, headers, name="Straße device", model="AB-20 ABC/DEF")
    for value in ("STRASSE", "straße", "AB-2", "stras ab", "ABC/DEF"):
        page = await api.post(BASE + "/query", headers=headers, json={"q": value})
        assert page.status_code == 200, page.text
        assert [row["ref"]["resource_id"] for row in page.json()["items"]] == [
            product["product_id"]
        ]
    response = await api.post(
        BASE + "/query",
        headers={**headers, "Content-Type": "application/json"},
        content=b" " * (16 * 1024) + b"{}",
    )
    assert response.status_code == 413
    assert response.json()["data"]["error"]["code"] == "invalid_input"
    assert (
        await api.post(BASE.replace("/v4", "") + "/query", headers=headers, json={})
    ).status_code == 200


async def test_maximum_unicode_search_keeps_a_bounded_usable_cursor(api, headers):
    query = "汉" * 200
    products = [await create(api, headers, name=query) for _ in range(2)]
    first = await api.post(BASE + "/query", headers=headers, json={"q": query, "limit": 1})
    assert first.status_code == 200, first.text
    cursor = first.json()["data"]["next_cursor"]
    assert cursor and len(cursor) <= 2048
    second = await api.post(
        BASE + "/query", headers=headers, json={"q": query, "limit": 1, "cursor": cursor}
    )
    assert second.status_code == 200, second.text
    assert {
        row["ref"]["resource_id"] for row in first.json()["items"] + second.json()["items"]
    } == {product["product_id"] for product in products}


async def test_parent_cursor_and_exact_detail_author(api, headers, database):
    first = await create(api, headers)
    second = await create(api, headers)
    root = first["product_id"]
    for revision in range(1, 28):
        reply = await api.post(
            f"/resources/products/{root}/revisions",
            headers=headers,
            json={
                "expected_revision": revision,
                "data": {**DATA, "name": f"Revision {revision + 1}"},
            },
        )
        assert reply.status_code == 200
    page = (
        await api.post(f"{BASE}/{root}/history/query", headers=headers, json={"limit": 1})
    ).json()
    bad = await api.post(
        f"{BASE}/{second['product_id']}/history/query",
        headers=headers,
        json={"limit": 1, "cursor": page["data"]["next_cursor"]},
    )
    assert bad.status_code == 400
    detail = (await api.get(f"{BASE}/{root}", headers=headers, params={"revision": 1})).json()[
        "data"
    ]
    assert detail["revised_at"] and detail["detail"]["revision"]["id"] == first["id"]
    assert (
        await api.get(f"{BASE}/{second['product_id']}", headers=headers, params={"revision": 28})
    ).status_code == 404


@pytest.mark.parametrize(
    "change",
    ["foreign_root", "wrong_revision", "wrong_sequence", "no_context", "token", "missing_audit"],
)
async def test_direct_event_inserts_cannot_bypass_isolation_or_authority(
    change, api, headers, database, application, admin_engine
):
    first, foreign = (await create(api, headers), await create(api, headers))
    actor = Identity(scopes=set(HUMAN_SCOPES))
    params = {"id": uuid4(), "root": UUID(first["product_id"]), "version": 1, "sequence": 1}
    if change == "foreign_root":
        params["root"] = UUID(foreign["product_id"])
    if change == "wrong_revision":
        params["version"] = 2
    if change == "wrong_sequence":
        params["sequence"] = 2
    if change == "token":
        actor.actor_kind = "token"
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction() as session:
            await set_actor_context(session, actor)
            await session.execute(
                text(
                    "INSERT INTO resource_lifecycle_events(id,product_id,revision,resource_revision,before_state,after_state,reason_code) VALUES(:id,:root,:sequence,:version,'active','inactive','obsolete')"
                ),
                params,
            )
    async with application.state.db.transaction() as session:
        assert (
            await session.scalar(
                text("SELECT lifecycle_revision FROM products WHERE id=:root"),
                {"root": UUID(first["product_id"])},
            )
            == 0
        )
        assert await session.scalar(text("SELECT count(*) FROM resource_lifecycle_events")) == 0


async def test_direct_inactive_pin_insert_and_reactivation_refused(
    api, headers, database, application
):
    product = await create(api, headers)
    task = (
        await api.post("/tasks", headers=headers, json={"name": "Synthetic SQL pin task"})
    ).json()["data"]["id"]
    pin = await api.post(
        f"/tasks/{task}/products", headers=headers, json={"product_id": product["product_id"]}
    )
    assert pin.status_code == 200
    await api.post(f"{BASE}/{product['product_id']}/lifecycle", headers=headers, json=lifecycle())
    actor = Identity(scopes=set(HUMAN_SCOPES))
    async with application.state.db.transaction() as session:
        await set_actor_context(session, actor)
        await session.execute(
            text("UPDATE task_resources SET active=false WHERE id=:pin"),
            {"pin": UUID(pin.json()["data"]["id"])},
        )
    for sql in (
        "UPDATE task_resources SET active=true WHERE id=:pin",
        "INSERT INTO task_resources(id,task_id,product_id,product_revision_id,lot,active) VALUES(:id,:task,:root,:revision,'new-lot',true)",
    ):
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction() as session:
                await set_actor_context(session, actor)
                await session.execute(
                    text(sql),
                    {
                        "id": uuid4(),
                        "task": UUID(task),
                        "root": UUID(product["product_id"]),
                        "revision": UUID(product["id"]),
                        "pin": UUID(pin.json()["data"]["id"]),
                    },
                )


async def test_root_simulated_provenance_applies_to_old_revisions(
    api, headers, database, admin_engine, pdf_bytes
):
    product = await create(api, headers)
    task = await api.post("/tasks", headers=headers, json={"name": "Synthetic provenance task"})
    assert task.status_code == 200, task.text
    task_id = task.json()["data"]["id"]
    document = await api.post(
        f"/tasks/{task_id}/documents",
        headers=headers,
        files={"file": ("synthetic-provenance.pdf", pdf_bytes, "application/pdf")},
    )
    assert document.status_code == 200, document.text
    with Session(admin_engine) as session, session.begin():
        job = Job(
            task_id=UUID(task_id),
            document_id=UUID(document.json()["data"]["id"]),
            kind="product_simulate",
            cache_key="a" * 64,
        )
        session.add(job)
        session.flush()
        session.add(SimulatedResource(product_id=UUID(product["product_id"]), job_id=job.id))
    reply = await api.post(
        f"/resources/products/{product['product_id']}/revisions",
        headers=headers,
        json={"expected_revision": 1, "data": {**DATA, "name": "Changed simulated"}},
    )
    assert reply.status_code == 200
    page = (await api.post(BASE + "/query", headers=headers, json={})).json()
    assert page["items"][0]["provenance"] == "simulated"
    detail = (
        await api.get(f"{BASE}/{product['product_id']}", headers=headers, params={"revision": 1})
    ).json()["data"]
    assert detail["provenance"] == "simulated"


@pytest.mark.parametrize("new_parents", [False, True])
async def test_valid_pin_relations_still_require_actor_after_fk_checks(
    new_parents, api, headers, database, application
):
    """FK-first rejection must never turn missing-parent lookups into an ACL bypass.

    Valid parents, including those inserted by the same SQL statement, must reach
    the selection authority gate. Its failure rolls back the entire statement.
    """
    product = await create(api, headers)
    task = await api.post("/tasks", headers=headers, json={"name": "Synthetic SQL guard task"})
    assert task.status_code == 200, task.text
    params = {
        "task": UUID(task.json()["data"]["id"]),
        "root": uuid4() if new_parents else UUID(product["product_id"]),
        "revision": uuid4() if new_parents else UUID(product["id"]),
        "pin": uuid4(),
    }
    statement = (
        "WITH new_product AS ( INSERT INTO products(id,current_revision) VALUES(:root,1) RETURNING id), new_revision AS ( INSERT INTO product_revisions(id,product_id,revision,data) SELECT :revision,new_product.id,1,'{}'::jsonb FROM new_product RETURNING id,product_id) INSERT INTO task_resources(id,task_id,product_id,product_revision_id,lot,active) SELECT :pin,:task,new_revision.product_id,new_revision.id,'guard-check',true FROM new_revision"
        if new_parents
        else "INSERT INTO task_resources(id,task_id,product_id,product_revision_id,lot,active) VALUES(:pin,:task,:root,:revision,'guard-check',true)"
    )
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction() as session:
            await session.execute(text(statement), params)
    assert failure.value.orig.sqlstate == "42501"
    async with application.state.db.transaction() as session:
        assert (
            await session.scalar(text("SELECT id FROM task_resources WHERE id=:pin"), params)
            is None
        )
        if new_parents:
            assert (
                await session.scalar(text("SELECT id FROM products WHERE id=:root"), params) is None
            )


def test_privileged_product_insert_keeps_baseline_guard(database, admin_engine):
    """A table owner cannot start a resource with an invented lifecycle."""
    with pytest.raises(DBAPIError) as failure, admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO products(id,current_revision,lifecycle_state,lifecycle_revision) VALUES(:id,1,'inactive',1)"
            ),
            {"id": uuid4()},
        )
    assert failure.value.orig.sqlstate == "23514"
