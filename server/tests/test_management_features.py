"""Failure-first DB/API acceptance for feature-library management.

Failures specified before implementation: foreign product filters, name-only prefix
search, changed/foreign/expired cursors, unavailable live authority, ambiguous
revision authors, lifecycle/content CAS confusion, token transitions, inactive
parents, stale pins, archival bypass, excessive response bytes and SQL timeouts.
The supplied restricted PostgreSQL fixture is required; no service is started.
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.models.entities import ApiToken, AuditLog, Membership, Org, User
from app.services import management_features
from app.services.auth import HUMAN_ONLY_SCOPES
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_features import metadata, product, task
from test_management_products import lifecycle
from test_team_workflow_membership import add_member, person, workflow

BASE = "/v4/management/resources/features"


async def create(api, headers, product_id=None, **fields):
    product_id = product_id or await product(api, headers)
    response = await api.post(
        "/resources/features", headers=headers, json={"data": {**metadata(product_id), **fields}}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def revise(api, headers, row, revision=1, **fields):
    response = await api.post(
        f"/resources/features/{row['feature_id']}/revisions",
        headers=headers,
        json={"expected_revision": revision, "data": {**row["data"], **fields}},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def pin(api, headers, task_id, row, revision=1, lot=None):
    return await api.post(
        f"/v4/tasks/{task_id}/features",
        headers=headers,
        json={"feature_id": row["feature_id"], "revision": revision, "lot": lot},
    )


async def test_name_prefix_product_status_pages_and_exact_revision(api, headers, tenants):
    parent, other = await product(api, headers[0]), await product(api, headers[0])
    rows = [await create(api, headers[0], parent, name=f"Alpha module {i}") for i in range(4)]
    await create(api, headers[0], other, name="Alpha other", status="implemented")
    await create(api, headers[1], name="Alpha foreign")
    query = {"q": "ALP", "product_id": parent, "implementation_status": "planned", "limit": 2}
    first = await api.post(BASE + "/query", headers=headers[0], json=query)
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["command"] == "resource feature browse" and page["warnings"]
    assert set(page["data"]) == {"org_id", "as_of", "returned", "next_cursor", "has_more"}
    assert page["data"]["returned"] == 2 and page["data"]["has_more"]
    assert len(first.content) <= 256 * 1024
    second = await api.post(
        BASE + "/query",
        headers=headers[0],
        json={**query, "q": "alp", "cursor": page["data"]["next_cursor"]},
    )
    assert second.status_code == 200 and not second.json()["data"]["has_more"]
    assert {row["ref"]["resource_id"] for row in page["items"] + second.json()["items"]} == {
        row["feature_id"] for row in rows
    }
    assert all(row["ref"]["kind"] == "features" for row in page["items"])
    for q in ("declaration", "Synthetic vendor", "alpha | absent", "!!!"):
        assert (await api.post(BASE + "/query", headers=headers[0], json={"q": q})).json()[
            "items"
        ] == []
    for status, count in (("planned", 4), ("implemented", 1), ("developing", 0)):
        response = await api.post(
            BASE + "/query", headers=headers[0], json={"implementation_status": status}
        )
        assert response.json()["data"]["returned"] == count
    row = rows[0]
    updated = await revise(api, headers[0], row, name="Revised module", status="developing")
    detail = await api.get(
        f"{BASE}/{row['feature_id']}", headers=headers[0], params={"revision": 1}
    )
    assert detail.status_code == 200
    value = detail.json()["data"]
    assert value["current_revision"] == 2 and value["detail"]["kind"] == "features"
    assert value["detail"]["revision"]["id"] == row["id"]
    assert value["revised_by"] == str(tenants["users"][0])
    history = await api.post(
        f"{BASE}/{row['feature_id']}/history/query", headers=headers[0], json={"limit": 1}
    )
    assert history.json()["items"][0]["revision_id"] == updated["id"]
    assert history.json()["items"][0]["created_by"] == str(tenants["users"][0])
    assert history.json()["items"][0]["has_file"] is False
    old = await api.post(
        f"{BASE}/{row['feature_id']}/history/query",
        headers=headers[0],
        json={"limit": 1, "cursor": history.json()["data"]["next_cursor"]},
    )
    assert [item["revision"] for item in old.json()["items"]] == [1]
    assert (
        await api.get(f"{BASE}/{row['feature_id']}", headers=headers[0], params={"revision": 3})
    ).status_code == 404


@pytest.mark.parametrize(
    "suffix,body",
    [("", {}), ("history/query", {}), ("lifecycle/history/query", {}), ("lifecycle", lifecycle())],
)
async def test_each_object_route_masks_foreign_and_missing(suffix, body, api, headers):
    foreign = await create(api, headers[1])
    responses = []
    for root in (foreign["feature_id"], str(uuid4())):
        responses.append(
            await api.post(f"{BASE}/{root}/{suffix}", headers=headers[0], json=body)
            if suffix
            else await api.get(f"{BASE}/{root}", headers=headers[0])
        )
    assert [reply.status_code for reply in responses] == [404, 404]
    assert responses[0].json()["data"]["error"] == responses[1].json()["data"]["error"]


async def test_product_filter_masks_foreign_missing_and_allows_empty(api, headers):
    own, foreign = await product(api, headers[0]), await product(api, headers[1])
    assert (await api.post(BASE + "/query", headers=headers[0], json={"product_id": own})).json()[
        "items"
    ] == []
    replies = [
        await api.post(BASE + "/query", headers=headers[0], json={"product_id": root})
        for root in (foreign, str(uuid4()))
    ]
    assert [reply.status_code for reply in replies] == [404, 404]
    assert replies[0].json()["data"]["error"] == replies[1].json()["data"]["error"]


async def test_cursor_kind_filters_parent_authority_and_expiry(
    api, headers, tenants, admin_engine, application
):
    parent = await product(api, headers[0])
    rows = [await create(api, headers[0], parent, name=f"Alpha {i}") for i in range(3)]
    first = (await api.post(BASE + "/query", headers=headers[0], json={"limit": 1})).json()
    cursor = first["data"]["next_cursor"]
    for hdr, path, body in (
        (headers[1], BASE + "/query", {"cursor": cursor}),
        (headers[0], BASE + "/query", {"cursor": cursor, "q": "alpha"}),
        (headers[0], BASE + "/query", {"cursor": cursor, "product_id": parent}),
        (headers[0], BASE + "/query", {"cursor": cursor, "implementation_status": "planned"}),
        (headers[0], BASE + "/query", {"cursor": "broken"}),
        (headers[0], "/v4/management/resources/products/query", {"cursor": cursor}),
        (headers[0], f"{BASE}/{rows[0]['feature_id']}/history/query", {"cursor": cursor}),
    ):
        reply = await api.post(path, headers=hdr, json=body)
        assert reply.status_code == 400, reply.text
        assert reply.json()["data"]["error"]["code"] == "management_cursor_invalid"
    payload = json.loads(application.state.crypto.cipher.decrypt(cursor.encode()))
    payload["exp"] = int(time.time()) - 1
    expired = application.state.crypto.cipher.encrypt(json.dumps(payload).encode()).decode()
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"cursor": expired})
    ).status_code == 409
    row = await revise(api, headers[0], rows[0], name="Revision two")
    history = (
        await api.post(
            f"{BASE}/{row['feature_id']}/history/query", headers=headers[0], json={"limit": 1}
        )
    ).json()
    assert (
        await api.post(
            f"{BASE}/{rows[1]['feature_id']}/history/query",
            headers=headers[0],
            json={"cursor": history["data"]["next_cursor"]},
        )
    ).status_code == 400
    with Session(admin_engine) as session, session.begin():
        session.scalar(
            select(Membership).where(Membership.org_id == tenants["orgs"][0])
        ).role = "viewer"
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"cursor": cursor})
    ).status_code == 400


@pytest.mark.parametrize(
    "role,allowed", [("admin", True), ("technical", True), ("bidder", False), ("viewer", False)]
)
async def test_lifecycle_roles_independent_cas_events_and_audit(
    role, allowed, api, headers, tenants, admin_engine, application
):
    row = await create(api, headers[0])
    root = row["feature_id"]
    with Session(admin_engine) as session, session.begin():
        session.scalar(
            select(Membership).where(Membership.org_id == tenants["orgs"][0])
        ).role = role
    assert (await api.post(BASE + "/query", headers=headers[0], json={})).status_code == 200
    reply = await api.post(f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle())
    assert reply.status_code == (200 if allowed else 403), reply.text
    if not allowed:
        for path, body in (
            ("/resources/features", {"data": row["data"]}),
            (
                f"/resources/features/{root}/revisions",
                {"expected_revision": 1, "data": row["data"]},
            ),
        ):
            assert (await api.post(path, headers=headers[0], json=body)).status_code == 403
        return
    assert reply.json()["data"]["existing_selections"] == "preserved"
    assert reply.json()["data"]["lifecycle"] == {"state": "inactive", "revision": 1}
    assert reply.json()["data"]["event"]["ref"] == {"kind": "features", "resource_id": root}
    assert (await api.post(BASE + "/query", headers=headers[0], json={})).json()["items"] == []
    assert (await api.post(BASE + "/query", headers=headers[0], json={"state": "inactive"})).json()[
        "data"
    ]["returned"] == 1
    assert (await api.get(f"{BASE}/{root}", headers=headers[0])).json()["data"]["detail"][
        "revision"
    ]["id"] == row["id"]
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle())
    ).status_code == 409
    await revise(api, headers[0], row, name="Inactive revised")
    assert (
        await api.post(
            f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle(1, 1, "active")
        )
    ).status_code == 409
    assert (
        await api.post(
            f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle(2, 0, "active")
        )
    ).status_code == 409
    assert (
        await api.post(
            f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle(2, 1, "active")
        )
    ).status_code == 200
    events = (
        await api.post(
            f"{BASE}/{root}/lifecycle/history/query", headers=headers[0], json={"limit": 1}
        )
    ).json()
    assert events["items"][0]["revision"] == 2 and events["items"][0]["resource_revision"] == 2
    previous = await api.post(
        f"{BASE}/{root}/lifecycle/history/query",
        headers=headers[0],
        json={"cursor": events["data"]["next_cursor"]},
    )
    assert [item["revision"] for item in previous.json()["items"]] == [1]
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        audits = (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.object_id == UUID(root))
                .order_by(AuditLog.created_at)
            )
        ).all()
        assert [item.action for item in audits] == [
            "resource.feature.create",
            "resource.feature.deactivate",
            "resource.feature.update",
            "resource.feature.restore",
        ]
        assert (
            audits[0].details["new_revision_id"] == row["id"] and audits[0].details["revision"] == 1
        )


async def test_tokens_read_write_declarations_but_cannot_transition_and_concurrent_cas(
    api, headers
):
    row = await create(api, headers[0])
    issued = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic feature token",
            "scopes": ["resource:read", "resource:write"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert issued.status_code == 200
    token_headers = {**headers[0], "Authorization": "Bearer " + issued.json()["data"]["token"]}
    assert (await api.post(BASE + "/query", headers=token_headers, json={})).status_code == 200
    assert (await api.get(f"{BASE}/{row['feature_id']}", headers=token_headers)).status_code == 200
    assert (
        await api.post(f"{BASE}/{row['feature_id']}/history/query", headers=token_headers, json={})
    ).status_code == 200
    assert (
        await api.post(
            f"{BASE}/{row['feature_id']}/lifecycle/history/query", headers=token_headers, json={}
        )
    ).status_code == 200
    await revise(api, token_headers, row, name="Agent declaration")
    path = f"{BASE}/{row['feature_id']}/lifecycle"
    assert (await api.post(path, headers=token_headers, json=lifecycle(2))).status_code == 403
    replies = await asyncio.gather(
        *(api.post(path, headers=headers[0], json=lifecycle(2)) for _ in range(2))
    )
    assert sorted(reply.status_code for reply in replies) == [200, 409]


async def test_ambiguous_exact_author_is_unknown(api, headers, tenants, admin_engine):
    row = await create(api, headers[0])
    with Session(admin_engine) as session, session.begin():
        session.add(
            AuditLog(
                org_id=tenants["orgs"][0],
                actor_user_id=tenants["users"][0],
                action="resource.feature.create",
                object_id=UUID(row["feature_id"]),
                details={"new_revision_id": row["id"], "revision": 1},
            )
        )
    assert (await api.post(BASE + "/query", headers=headers[0], json={})).json()["items"][0][
        "revised_by"
    ] is None
    assert (await api.get(f"{BASE}/{row['feature_id']}", headers=headers[0])).json()["data"][
        "revised_by"
    ] is None


async def test_parent_inactive_preserves_pins_blocks_new_associations_and_allows_restore(
    api, headers
):
    parent = await product(api, headers[0])
    row = await create(api, headers[0], parent)
    tasks = [await task(api, headers[0]) for _ in range(2)]
    selected = await pin(api, headers[0], tasks[0], row)
    assert selected.status_code == 200
    stopped = await api.post(
        f"/v4/management/resources/products/{parent}/lifecycle",
        headers=headers[0],
        json=lifecycle(),
    )
    assert stopped.status_code == 200, stopped.text
    assert (await pin(api, headers[0], tasks[0], row)).json()["data"]["duplicate"] is True
    denied = await pin(api, headers[0], tasks[1], row)
    assert (
        denied.status_code == 409 and denied.json()["data"]["error"]["code"] == "resource_inactive"
    )
    for path, body in (
        ("/resources/features", {"data": row["data"]}),
        (
            f"/resources/features/{row['feature_id']}/revisions",
            {"expected_revision": 1, "data": row["data"]},
        ),
    ):
        reply = await api.post(path, headers=headers[0], json=body)
        assert (
            reply.status_code == 409
            and reply.json()["data"]["error"]["code"] == "resource_inactive"
        )
    detail = (await api.get(f"{BASE}/{row['feature_id']}", headers=headers[0])).json()["data"]
    assert (
        next(action for action in detail["actions"] if action["action"] == "select")["reason"]
        == "resource_inactive"
    )
    assert (
        await api.post(
            f"/v4/management/resources/products/{parent}/lifecycle",
            headers=headers[0],
            json=lifecycle(1, 1, "active"),
        )
    ).status_code == 200
    await revise(api, headers[0], row, status="implemented")
    assert (await api.get(f"/tasks/{tasks[0]}/features", headers=headers[0])).json()["items"][0][
        "feature_revision_id"
    ] == row["id"]
    assert (await pin(api, headers[0], tasks[1], row, revision=2)).status_code == 200


async def test_two_task_pins_feature_stop_restore_and_archive(api, headers):
    row = await create(api, headers[0])
    first, second, empty = [await task(api, headers[0]) for _ in range(3)]
    initial = await pin(api, headers[0], first, row)
    await pin(api, headers[0], second, row)
    updated = await revise(api, headers[0], row, status="implemented")
    for task_id in (first, second):
        snapshot = (await api.get(f"/tasks/{task_id}/features", headers=headers[0])).json()[
            "items"
        ][0]
        assert snapshot["feature_revision_id"] == row["id"] and snapshot["data"] == row["data"]
    assert (await pin(api, headers[0], second, row, revision=2)).json()["data"][
        "feature_revision_id"
    ] == updated["id"]
    assert (
        await api.post(
            f"{BASE}/{row['feature_id']}/lifecycle", headers=headers[0], json=lifecycle(2)
        )
    ).status_code == 200
    replay = await pin(api, headers[0], first, row)
    assert (
        replay.json()["data"]["duplicate"] is True
        and replay.json()["data"]["id"] == initial.json()["data"]["id"]
    )
    for task_id, revision, lot in (
        (first, 2, None),
        (first, 1, "new"),
        (second, 1, None),
        (empty, 1, None),
    ):
        assert (await pin(api, headers[0], task_id, row, revision, lot)).status_code == 409
    state = await workflow(api, headers[0], first)
    assert (
        await api.post(
            f"/v4/tasks/{first}/archive",
            headers=headers[0],
            json={"expected_revision": state["revision"], "reason": "Synthetic feature archive"},
        )
    ).status_code == 200
    assert (await pin(api, headers[0], first, row)).json()["data"]["error"][
        "code"
    ] == "task_archived"
    assert (await api.get(f"/tasks/{first}/features", headers=headers[0])).json()["items"][0][
        "id"
    ] == initial.json()["data"]["id"]
    assert (
        await api.post(
            f"{BASE}/{row['feature_id']}/lifecycle",
            headers=headers[0],
            json=lifecycle(2, 1, "active"),
        )
    ).status_code == 200
    assert (await pin(api, headers[0], empty, row)).status_code == 200


@pytest.mark.parametrize(
    "org_role,task_role,domains,status",
    [
        ("technical", "contributor", ["technical"], 200),
        ("bidder", "contributor", ["commercial"], 200),
        ("technical", "reviewer", ["technical"], 403),
        ("technical", "observer", [], 403),
        ("admin", None, [], 403),
        ("technical", None, [], 404),
    ],
)
async def test_library_detail_does_not_grant_task_pinning(
    api, headers, tenants, admin_engine, org_role, task_role, domains, status
):
    row = await create(api, headers[0])
    task_id = await task(api, headers[0])
    user, auth = await person(api, admin_engine, tenants["orgs"][0], org_role)
    if task_role:
        assert (
            await add_member(api, headers[0], task_id, user, 1, task_role, domains)
        ).status_code == 200
    assert (await api.get(f"{BASE}/{row['feature_id']}", headers=auth)).status_code == 200
    assert (await pin(api, auth, task_id, row)).status_code == status


@pytest.mark.parametrize("disabled,expected", [("user", 401), ("membership", 404), ("org", 403)])
async def test_live_identity_denials_on_every_read(
    disabled, expected, api, headers, tenants, admin_engine
):
    row = await create(api, headers[0])
    with Session(admin_engine) as session, session.begin():
        if disabled == "user":
            session.get(User, tenants["users"][0]).active = False
        elif disabled == "membership":
            session.scalar(
                select(Membership).where(Membership.org_id == tenants["orgs"][0])
            ).active = False
        else:
            session.get(Org, tenants["orgs"][0]).active = False
    assert (
        await api.get(f"{BASE}/{row['feature_id']}", headers=headers[0])
    ).status_code == expected
    for path in (
        BASE + "/query",
        f"{BASE}/{row['feature_id']}/history/query",
        f"{BASE}/{row['feature_id']}/lifecycle/history/query",
    ):
        assert (await api.post(path, headers=headers[0], json={})).status_code == expected


async def test_input_unicode_cursor_and_envelope_bounds(api, headers):
    parent = await product(api, headers[0])
    query = "汉" * 200
    rows = [await create(api, headers[0], parent, name=query) for _ in range(2)]
    first = await api.post(BASE + "/query", headers=headers[0], json={"q": query, "limit": 1})
    assert first.status_code == 200
    cursor = first.json()["data"]["next_cursor"]
    assert cursor and len(cursor) <= 2048
    second = await api.post(
        BASE + "/query", headers=headers[0], json={"q": query, "limit": 1, "cursor": cursor}
    )
    assert {
        item["ref"]["resource_id"] for item in first.json()["items"] + second.json()["items"]
    } == {row["feature_id"] for row in rows}
    street = await create(api, headers[0], parent, name="Straße ABC/DEF")
    for q in ("STRASSE", "straße", "ABC/DEF", "stras abc"):
        response = await api.post(BASE + "/query", headers=headers[0], json={"q": q})
        assert [item["ref"]["resource_id"] for item in response.json()["items"]] == [
            street["feature_id"]
        ]
    for body in (
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"q": " "},
        {"q": "x" * 201},
        {"implementation_status": "verified"},
    ):
        assert (await api.post(BASE + "/query", headers=headers[0], json=body)).status_code == 422
    response = await api.post(
        BASE + "/query",
        headers={**headers[0], "Content-Type": "application/json"},
        content=b" " * (16 * 1024) + b"{}",
    )
    assert response.status_code == 413
    assert (
        await api.post(BASE.replace("/v4", "") + "/query", headers=headers[0], json={})
    ).status_code == 404


async def test_page_adapter_refuses_final_envelope_overflow(api, headers, tenants):
    from app.core.errors import ServiceError
    from app.schemas.management_pages import ResourceQuery

    await create(api, headers[0])
    page_response = await api.post(BASE + "/query", headers=headers[0], json={})
    from app.schemas.management_pages import FeatureRow, Page

    page = Page[FeatureRow](data=page_response.json()["data"], items=page_response.json()["items"])
    with pytest.raises(ServiceError) as failure:
        management_features.page_result("x" * (256 * 1024), page, "USD", 0)
    assert failure.value.code == "management_result_too_large"
    assert ResourceQuery(limit=100).limit == 100


async def test_statement_timeout_is_retryable_without_downgrade(tenants, application):
    from app.core.errors import ServiceError
    from app.schemas.management_pages import ResourceQuery

    class Cancelled(Exception):
        sqlstate = "57014"

    class CancelledSession:
        async def execute(self, statement):
            raise DBAPIError(str(statement), {}, Cancelled(), False)

    with pytest.raises(ServiceError) as failure:
        async with management_features.read_budget(CancelledSession(), ResourceQuery()):
            pytest.fail("cancelled query must not enter its body")
    assert failure.value.code == "management_query_timeout" and failure.value.exit_code == 3


async def test_direct_service_reauthorizes_after_read_marker(
    api, headers, tenants, application, admin_engine
):
    from app.core.errors import ServiceError
    from app.schemas.management_pages import ResourceQuery
    from app.services.auth import authenticate

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
        assert (await management_features.query(session, actor, ResourceQuery())).data.returned == 1
        assert "management_authenticated_actor" not in session.info
        with Session(admin_engine) as owner, owner.begin():
            owner.scalar(select(Membership).where(Membership.org_id == org)).active = False
        with pytest.raises(ServiceError) as failure:
            await management_features.query(session, actor, ResourceQuery())
        assert failure.value.code == "not_found"


async def test_historical_detail_checks_selected_and_current_product_states(api, headers):
    """Reassociation must not conceal a withdrawn historical revision parent."""
    old_parent, active_parent = await product(api, headers[0]), await product(api, headers[0])
    row = await create(api, headers[0], old_parent)
    await revise(api, headers[0], row, product_id=active_parent)
    stopped = await api.post(
        f"/v4/management/resources/products/{old_parent}/lifecycle",
        headers=headers[0],
        json=lifecycle(),
    )
    assert stopped.status_code == 200, stopped.text

    async def selection_reason(revision):
        reply = await api.get(
            f"{BASE}/{row['feature_id']}", headers=headers[0], params={"revision": revision}
        )
        assert reply.status_code == 200, reply.text
        detail = reply.json()["data"]
        assert detail["detail"]["revision"]["revision"] == revision
        return next(action for action in detail["actions"] if action["action"] == "select")[
            "reason"
        ]

    assert await selection_reason(1) == "resource_inactive"
    assert await selection_reason(2) == "task_access_required"
    # Reversing the two product states must still block the historical detail:
    # its own parent is now active while the feature's current association is not.
    assert (
        await api.post(
            f"/v4/management/resources/products/{old_parent}/lifecycle",
            headers=headers[0],
            json=lifecycle(1, 1, "active"),
        )
    ).status_code == 200
    assert (
        await api.post(
            f"/v4/management/resources/products/{active_parent}/lifecycle",
            headers=headers[0],
            json=lifecycle(),
        )
    ).status_code == 200
    assert await selection_reason(1) == "resource_inactive"
    assert await selection_reason(2) == "resource_inactive"


async def test_feature_tokens_never_gain_any_human_only_scope(api, headers, application, tenants):
    expiry = datetime.now(UTC) + timedelta(hours=1)
    for scope in sorted(HUMAN_ONLY_SCOPES):
        denied = await api.post(
            "/tokens",
            headers=headers[0],
            json={
                "name": "Synthetic forbidden scope",
                "scopes": [scope],
                "expires_at": expiry.isoformat(),
            },
        )
        assert denied.status_code == 403, scope
        with pytest.raises(DBAPIError) as error:
            async with application.state.db.transaction(tenants["orgs"][0]) as session:
                session.add(
                    ApiToken(
                        org_id=tenants["orgs"][0],
                        user_id=tenants["users"][0],
                        name="Synthetic forbidden scope",
                        digest=uuid4().hex * 2,
                        scopes=[scope],
                        expires_at=expiry,
                    )
                )
        assert error.value.orig.sqlstate == "23514", scope
