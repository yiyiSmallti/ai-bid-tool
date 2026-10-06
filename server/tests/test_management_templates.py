"""DB/API acceptance: bounded templates, immutable binding and human gates.

Failure inventory established before implementation: foreign/missing parents,
absent/inactive actors, role/token/authority drift, forged or expired cursors,
ambiguous exact authors, content/lifecycle CAS confusion, inactive and archived
pin writes, file disclosure, binding hash/static review races, SQL bypass and
unbounded revision/audit work. Tests use the supplied restricted PostgreSQL role.
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.models.entities import ApiToken, AuditLog, Membership, Org, User
from app.schemas.export_contracts import ExportBindingView
from app.services import management_templates
from app.services.auth import HUMAN_ONLY_SCOPES
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_exports import COLUMNS, SECTIONS, export_template
from test_management_products import lifecycle
from test_team_workflow_membership import add_member, person, workflow
from test_templates import metadata, task, upload

BASE = "/v4/management/resources/templates"
BINDINGS = "/v4/management/export-bindings"


async def create(api, headers, **fields):
    response = await upload(api, headers, export_template(), body={"data": metadata(**fields)})
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def revise(api, headers, row, revision=1, **fields):
    response = await upload(
        api,
        headers,
        export_template(),
        path=f"/resources/templates/{row['template_id']}/revisions",
        body={"expected_revision": revision, "data": {**row["data"], **fields}},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def pin(api, headers, task_id, row, revision=1, lot=None):
    return await api.post(
        f"/v4/tasks/{task_id}/templates",
        headers=headers,
        json={"template_id": row["template_id"], "revision": revision, "lot": lot},
    )


def binding_request(row, *, dry_run=True):
    return {
        "template_revision_id": row["id"],
        "expected_template_sha256": row["file"]["sha256"],
        "sections": [
            {
                "section": section,
                "heading_style_id": "Heading1",
                "table_style_id": "TableGrid",
                "columns": [
                    {"key": key, "width_percent": width}
                    for key, width in zip(COLUMNS, [6, 36, 44, 14], strict=True)
                ]
                if index < 3
                else [],
            }
            for index, section in enumerate(SECTIONS)
        ],
        "dry_run": dry_run,
    }


async def bind(api, headers, row, *, widths=None):
    body = binding_request(row)
    if widths:
        for section in body["sections"][:3]:
            for column, width in zip(section["columns"], widths, strict=True):
                column["width_percent"] = width
    preview = await api.post("/export-template-bindings", headers=headers, json=body)
    assert preview.status_code == 200, preview.text
    body.update(
        dry_run=False, expected_static_content_hash=preview.json()["data"]["static_content_hash"]
    )
    response = await api.post("/export-template-bindings", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.mark.parametrize(
    "suffix,body",
    [("", {}), ("history/query", {}), ("lifecycle/history/query", {}), ("lifecycle", lifecycle())],
)
async def test_each_object_route_masks_foreign_and_missing(suffix, body, api, headers):
    foreign = await create(api, headers[1])
    responses = []
    for root in (foreign["template_id"], str(uuid4())):
        responses.append(
            await api.post(f"{BASE}/{root}/{suffix}", headers=headers[0], json=body)
            if suffix
            else await api.get(f"{BASE}/{root}", headers=headers[0])
        )
    assert [reply.status_code for reply in responses] == [404, 404]
    assert responses[0].json()["data"]["error"] == responses[1].json()["data"]["error"]


@pytest.mark.parametrize(
    "role,allowed", [("admin", True), ("technical", False), ("bidder", False), ("viewer", False)]
)
async def test_lifecycle_roles_independent_cas_events_and_audit(
    role, allowed, api, headers, tenants, admin_engine, application
):
    row = await create(api, headers[0])
    root = row["template_id"]
    with Session(admin_engine) as session, session.begin():
        session.scalar(
            select(Membership).where(Membership.org_id == tenants["orgs"][0])
        ).role = role
    assert (await api.post(BASE + "/query", headers=headers[0], json={})).status_code == 200
    reply = await api.post(f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle())
    assert reply.status_code == (200 if allowed else 403), reply.text
    if not allowed:
        for path, body in (
            ("/resources/templates", {"data": row["data"]}),
            (
                f"/resources/templates/{root}/revisions",
                {"expected_revision": 1, "data": row["data"]},
            ),
        ):
            assert (
                await upload(api, headers[0], export_template(), path=path, body=body)
            ).status_code == 403
        return
    assert reply.json()["data"]["existing_selections"] == "preserved"
    assert reply.json()["data"]["lifecycle"] == {"state": "inactive", "revision": 1}
    assert reply.json()["data"]["event"]["ref"] == {"kind": "templates", "resource_id": root}
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
            "resource.template.create",
            "resource.template.deactivate",
            "resource.template.update",
            "resource.template.restore",
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
            "name": "Synthetic template token",
            "scopes": ["template:read", "template:write"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert issued.status_code == 200
    token_headers = {**headers[0], "Authorization": "Bearer " + issued.json()["data"]["token"]}
    assert (await api.post(BASE + "/query", headers=token_headers, json={})).status_code == 200
    assert (await api.get(f"{BASE}/{row['template_id']}", headers=token_headers)).status_code == 200
    assert (
        await api.post(f"{BASE}/{row['template_id']}/history/query", headers=token_headers, json={})
    ).status_code == 200
    assert (
        await api.post(
            f"{BASE}/{row['template_id']}/lifecycle/history/query", headers=token_headers, json={}
        )
    ).status_code == 200
    await revise(api, token_headers, row, name="Agent declaration")
    path = f"{BASE}/{row['template_id']}/lifecycle"
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
                action="resource.template.create",
                object_id=UUID(row["template_id"]),
                details={"new_revision_id": row["id"], "revision": 1},
            )
        )
    assert (await api.post(BASE + "/query", headers=headers[0], json={})).json()["items"][0][
        "revised_by"
    ] is None
    assert (await api.get(f"{BASE}/{row['template_id']}", headers=headers[0])).json()["data"][
        "revised_by"
    ] is None


async def test_two_task_pins_template_stop_restore_and_archive(api, headers):
    row = await create(api, headers[0])
    first, second, empty = [await task(api, headers[0]) for _ in range(3)]
    initial = await pin(api, headers[0], first, row)
    await pin(api, headers[0], second, row)
    updated = await revise(api, headers[0], row, name="Revised template")
    for task_id in (first, second):
        snapshot = (await api.get(f"/tasks/{task_id}/templates", headers=headers[0])).json()[
            "items"
        ][0]
        assert snapshot["template_revision_id"] == row["id"] and snapshot["data"] == row["data"]
    assert (await pin(api, headers[0], second, row, revision=2)).json()["data"][
        "template_revision_id"
    ] == updated["id"]
    assert (
        await api.post(
            f"{BASE}/{row['template_id']}/lifecycle", headers=headers[0], json=lifecycle(2)
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
            json={"expected_revision": state["revision"], "reason": "Synthetic template archive"},
        )
    ).status_code == 200
    assert (await pin(api, headers[0], first, row)).json()["data"]["error"][
        "code"
    ] == "task_archived"
    assert (await api.get(f"/tasks/{first}/templates", headers=headers[0])).json()["items"][0][
        "id"
    ] == initial.json()["data"]["id"]
    assert (
        await api.post(
            f"{BASE}/{row['template_id']}/lifecycle",
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
    assert (await api.get(f"{BASE}/{row['template_id']}", headers=auth)).status_code == 200
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
        await api.get(f"{BASE}/{row['template_id']}", headers=headers[0])
    ).status_code == expected
    for path in (
        BASE + "/query",
        f"{BASE}/{row['template_id']}/history/query",
        f"{BASE}/{row['template_id']}/lifecycle/history/query",
    ):
        assert (await api.post(path, headers=headers[0], json={})).status_code == expected


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
        assert (
            await management_templates.query(session, actor, ResourceQuery())
        ).data.returned == 1
        assert "management_authenticated_actor" not in session.info
        with Session(admin_engine) as owner, owner.begin():
            owner.scalar(select(Membership).where(Membership.org_id == org)).active = False
        with pytest.raises(ServiceError) as failure:
            await management_templates.query(session, actor, ResourceQuery())
        assert failure.value.code == "not_found"


async def test_template_tokens_never_gain_any_human_only_scope(api, headers, application, tenants):
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


async def test_bounded_name_prefix_pages_exact_file_revision_and_authors(api, headers, tenants):
    rows = [await create(api, headers[0], name=f"Alpha template {i}") for i in range(4)]
    await create(api, headers[1], name="Alpha template foreign")
    await create(
        api,
        headers[0],
        name="Other",
        project_types=["Alpha type"],
        chapters=[{"title": "Alpha chapter"}],
    )
    query = {"q": "ALP", "limit": 2}
    first = await api.post(BASE + "/query", headers=headers[0], json=query)
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["command"] == "resource template browse" and page["warnings"]
    assert set(page["data"]) == {"org_id", "as_of", "returned", "next_cursor", "has_more"}
    assert page["data"]["returned"] == 2 and page["data"]["has_more"]
    assert len(first.content) <= 256 * 1024
    second = await api.post(
        BASE + "/query",
        headers=headers[0],
        json={**query, "q": "alp", "cursor": page["data"]["next_cursor"]},
    )
    assert second.status_code == 200 and not second.json()["data"]["has_more"]
    items = page["items"] + second.json()["items"]
    assert {row["ref"]["resource_id"] for row in items} == {row["template_id"] for row in rows}
    assert all(
        row["ref"]["kind"] == "templates" and row["revised_by"] == str(tenants["users"][0])
        for row in items
    )
    for q in ("chapter", "type", "!!!", "alpha | absent"):
        assert (await api.post(BASE + "/query", headers=headers[0], json={"q": q})).json()[
            "items"
        ] == []
    updated = await revise(api, headers[0], rows[0], name="Updated template")
    detail = await api.get(
        f"{BASE}/{rows[0]['template_id']}", headers=headers[0], params={"revision": 1}
    )
    assert detail.status_code == 200, detail.text
    value = detail.json()["data"]
    assert value["current_revision"] == 2 and value["detail"]["kind"] == "templates"
    assert value["detail"]["revision"]["id"] == rows[0]["id"]
    assert value["detail"]["revision"]["file"] == rows[0]["file"]
    assert value["revised_by"] == str(tenants["users"][0])
    history = (
        await api.post(
            f"{BASE}/{rows[0]['template_id']}/history/query", headers=headers[0], json={"limit": 1}
        )
    ).json()
    assert history["items"][0]["revision_id"] == updated["id"]
    assert history["items"][0]["has_file"] is True
    assert history["items"][0]["created_by"] == str(tenants["users"][0])
    old = await api.post(
        f"{BASE}/{rows[0]['template_id']}/history/query",
        headers=headers[0],
        json={"limit": 1, "cursor": history["data"]["next_cursor"]},
    )
    assert [item["revision"] for item in old.json()["items"]] == [1]
    assert (
        await api.get(
            f"{BASE}/{rows[0]['template_id']}", headers=headers[0], params={"revision": 3}
        )
    ).status_code == 404
    assert "storage_key" not in detail.text


async def test_cursor_binding_kind_parent_filters_actor_and_expiry(
    api, headers, tenants, application, admin_engine
):
    rows = [await create(api, headers[0], name=f"Alpha {i}") for i in range(3)]
    cursor = (await api.post(BASE + "/query", headers=headers[0], json={"limit": 1})).json()[
        "data"
    ]["next_cursor"]
    for hdr, path, body in (
        (headers[1], BASE + "/query", {"cursor": cursor}),
        (headers[0], BASE + "/query", {"cursor": cursor, "q": "alpha"}),
        (headers[0], BASE + "/query", {"cursor": cursor, "state": "inactive"}),
        (headers[0], BASE + "/query", {"cursor": "broken"}),
        (headers[0], "/v4/management/resources/products/query", {"cursor": cursor}),
        (headers[0], f"{BASE}/{rows[0]['template_id']}/history/query", {"cursor": cursor}),
        (
            headers[0],
            BINDINGS + "/query",
            {"template_revision_id": rows[0]["id"], "cursor": cursor},
        ),
    ):
        response = await api.post(path, headers=hdr, json=body)
        assert response.status_code == 400, response.text
        assert response.json()["data"]["error"]["code"] == "management_cursor_invalid"
    payload = json.loads(application.state.crypto.cipher.decrypt(cursor.encode()))
    payload["exp"] = int(time.time()) - 1
    expired = application.state.crypto.cipher.encrypt(json.dumps(payload).encode()).decode()
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"cursor": expired})
    ).status_code == 409
    await revise(api, headers[0], rows[0], name="Next revision")
    history_cursor = (
        await api.post(
            f"{BASE}/{rows[0]['template_id']}/history/query", headers=headers[0], json={"limit": 1}
        )
    ).json()["data"]["next_cursor"]
    assert (
        await api.post(
            f"{BASE}/{rows[1]['template_id']}/history/query",
            headers=headers[0],
            json={"cursor": history_cursor},
        )
    ).status_code == 400
    with Session(admin_engine) as session, session.begin():
        session.scalar(
            select(Membership).where(Membership.org_id == tenants["orgs"][0])
        ).role = "bidder"
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"cursor": cursor})
    ).status_code == 400


async def test_input_unicode_envelope_and_foreign_feature_filters(api, headers):
    query = "汉" * 200
    rows = [await create(api, headers[0], name=query) for _ in range(2)]
    first = await api.post(BASE + "/query", headers=headers[0], json={"q": query, "limit": 1})
    assert first.status_code == 200, first.text
    cursor = first.json()["data"]["next_cursor"]
    assert cursor and len(cursor) <= 2048
    second = await api.post(
        BASE + "/query", headers=headers[0], json={"q": query, "limit": 1, "cursor": cursor}
    )
    assert {
        item["ref"]["resource_id"] for item in first.json()["items"] + second.json()["items"]
    } == {row["template_id"] for row in rows}
    street = await create(api, headers[0], name="Straße ABC/DEF")
    for q in ("STRASSE", "straße", "ABC/DEF", "stras abc"):
        assert [
            item["ref"]["resource_id"]
            for item in (await api.post(BASE + "/query", headers=headers[0], json={"q": q})).json()[
                "items"
            ]
        ] == [street["template_id"]]
    for body in (
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"q": " "},
        {"q": "x" * 201},
        {"product_id": str(uuid4())},
        {"implementation_status": "implemented"},
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


@pytest.mark.parametrize(
    "role,reads,writes",
    [
        ("admin", True, True),
        ("bidder", True, False),
        ("technical", False, False),
        ("viewer", False, False),
    ],
)
async def test_binding_role_matrix_preview_create_query_detail(
    role, reads, writes, api, headers, tenants, admin_engine
):
    row = await create(api, headers[0])
    binding = await bind(api, headers[0], row)
    with Session(admin_engine) as session, session.begin():
        session.scalar(
            select(Membership).where(Membership.org_id == tenants["orgs"][0])
        ).role = role
    assert (
        await api.post(
            BINDINGS + "/query", headers=headers[0], json={"template_revision_id": row["id"]}
        )
    ).status_code == (200 if reads else 403)
    assert (
        await api.get(
            f"{BINDINGS}/{binding['id']}",
            headers=headers[0],
            params={"template_revision_id": row["id"]},
        )
    ).status_code == (200 if reads else 403)
    body = binding_request(row)
    preview = await api.post("/export-template-bindings", headers=headers[0], json=body)
    assert preview.status_code == (200 if writes else 403)
    body.update(dry_run=False, expected_static_content_hash=binding["static_content_hash"])
    assert (
        await api.post("/export-template-bindings", headers=headers[0], json=body)
    ).status_code == (200 if writes else 403)


async def test_binding_query_detail_all_foreign_missing_and_same_org_parent_mismatch(api, headers):
    own, wrong, foreign = (
        await create(api, headers[0]),
        await create(api, headers[0]),
        await create(api, headers[1]),
    )
    own_binding, foreign_binding = (
        await bind(api, headers[0], own),
        await bind(api, headers[1], foreign),
    )
    query_replies = [
        await api.post(
            BINDINGS + "/query", headers=headers[0], json={"template_revision_id": revision}
        )
        for revision in (foreign["id"], str(uuid4()))
    ]
    assert [response.status_code for response in query_replies] == [404, 404]
    assert query_replies[0].json()["data"]["error"] == query_replies[1].json()["data"]["error"]
    for binding, parent in (
        (foreign_binding["id"], foreign["id"]),
        (foreign_binding["id"], own["id"]),
        (own_binding["id"], wrong["id"]),
        (own_binding["id"], foreign["id"]),
        (str(uuid4()), own["id"]),
    ):
        response = await api.get(
            f"{BINDINGS}/{binding}", headers=headers[0], params={"template_revision_id": parent}
        )
        assert response.status_code == 404, response.text
    assert (
        await api.post(
            BINDINGS + "/query", headers=headers[0], json={"template_revision_id": wrong["id"]}
        )
    ).json()["items"] == []
    for row in (foreign, {**own, "id": str(uuid4())}):
        assert (
            await api.post(
                "/export-template-bindings", headers=headers[0], json=binding_request(row)
            )
        ).status_code == 404


async def test_binding_preview_static_hash_cas_immutable_duplicate_and_revision_independence(
    api, headers, tenants, application
):
    row = await create(api, headers[0])
    body = binding_request(row)
    preview = await api.post("/export-template-bindings", headers=headers[0], json=body)
    assert preview.status_code == 200
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        assert (
            list(
                (
                    await session.scalars(
                        select(AuditLog).where(AuditLog.action == "export.binding_created")
                    )
                ).all()
            )
            == []
        )
    assert (
        await api.post(
            "/export-template-bindings",
            headers=headers[0],
            json={**body, "expected_template_sha256": "0" * 64},
        )
    ).status_code == 409
    missing = await api.post(
        "/export-template-bindings", headers=headers[0], json={**body, "dry_run": False}
    )
    assert missing.status_code == 422
    conflict = await api.post(
        "/export-template-bindings",
        headers=headers[0],
        json={**body, "dry_run": False, "expected_static_content_hash": "0" * 64},
    )
    assert (
        conflict.status_code == 409
        and conflict.json()["data"]["error"]["code"] == "export_static_hash_conflict"
    )
    binding = await bind(api, headers[0], row)
    duplicate = await bind(api, headers[0], row)
    assert duplicate["id"] == binding["id"]
    assert binding["reviewed_by"] == str(tenants["users"][0]) and binding["current"] is True
    updated = await revise(api, headers[0], row, name="New DOCX version")
    assert (
        await api.post(
            BINDINGS + "/query", headers=headers[0], json={"template_revision_id": updated["id"]}
        )
    ).json()["items"] == []
    retained = await api.get(
        f"{BINDINGS}/{binding['id']}",
        headers=headers[0],
        params={"template_revision_id": row["id"]},
    )
    assert retained.status_code == 200
    # PostgreSQL may return the same instant in its session timezone. Compare
    # every typed field, including the aware timestamp, rather than its spelling.
    assert ExportBindingView.model_validate(retained.json()["data"]) == (
        ExportBindingView.model_validate(binding)
    )
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        audits = list(
            (
                await session.scalars(
                    select(AuditLog).where(AuditLog.action == "export.binding_created")
                )
            ).all()
        )
        assert len(audits) == 1 and audits[0].object_id == UUID(binding["id"])
        assert "Synthetic" not in json.dumps(audits[0].details)


async def test_binding_bounded_pages_cursors_and_inactive_read_preview(api, headers, application):
    row, other = await create(api, headers[0]), await create(api, headers[0])
    bindings = [
        await bind(api, headers[0], row, widths=widths)
        for widths in ([6, 36, 44, 14], [7, 35, 44, 14], [8, 34, 44, 14])
    ]
    body = {"template_revision_id": row["id"], "limit": 1}
    first = await api.post(BINDINGS + "/query", headers=headers[0], json=body)
    cursor = first.json()["data"]["next_cursor"]
    assert first.status_code == 200 and cursor and len(first.content) <= 256 * 1024
    assert first.json()["data"]["returned"] == 1
    second = await api.post(
        BINDINGS + "/query", headers=headers[0], json={**body, "cursor": cursor}
    )
    assert (
        second.status_code == 200
        and second.json()["items"][0]["id"] != first.json()["items"][0]["id"]
    )
    assert first.json()["items"][0]["id"] == bindings[-1]["id"]
    for hdr, parent in ((headers[1], row["id"]), (headers[0], other["id"])):
        # Foreign parent is hidden before cursor parsing; visible wrong parent fails cursor binding.
        response = await api.post(
            BINDINGS + "/query",
            headers=hdr,
            json={"template_revision_id": parent, "cursor": cursor},
        )
        assert response.status_code == (404 if hdr == headers[1] else 400), response.text
    payload = json.loads(application.state.crypto.cipher.decrypt(cursor.encode()))
    payload["exp"] = int(time.time()) - 1
    expired = application.state.crypto.cipher.encrypt(json.dumps(payload).encode()).decode()
    assert (
        await api.post(BINDINGS + "/query", headers=headers[0], json={**body, "cursor": expired})
    ).status_code == 409
    assert (
        await api.post(
            f"{BASE}/{row['template_id']}/lifecycle", headers=headers[0], json=lifecycle()
        )
    ).status_code == 200
    assert (await api.post(BINDINGS + "/query", headers=headers[0], json=body)).status_code == 200
    assert (
        await api.post("/export-template-bindings", headers=headers[0], json=binding_request(row))
    ).status_code == 200
    inactive = {
        **binding_request(row),
        "dry_run": False,
        "expected_static_content_hash": bindings[0]["static_content_hash"],
    }
    response = await api.post("/export-template-bindings", headers=headers[0], json=inactive)
    assert response.status_code == 200 and response.json()["data"]["id"] == bindings[0]["id"]
    added = await bind(api, headers[0], row, widths=[9, 33, 44, 14])
    assert added["id"] not in {entry["id"] for entry in bindings}


async def test_tokens_read_template_metadata_but_never_files_bindings_or_lifecycle(api, headers):
    row = await create(api, headers[0])
    binding = await bind(api, headers[0], row)
    issued = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic template metadata",
            "scopes": ["template:read", "template:write"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert issued.status_code == 200
    token = {**headers[0], "Authorization": "Bearer " + issued.json()["data"]["token"]}
    assert (await api.post(BASE + "/query", headers=token, json={})).status_code == 200
    signed = (
        await api.get(
            f"/resources/templates/revisions/{row['id']}/download-link", headers=headers[0]
        )
    ).json()["data"]["url"]
    for path, args in (
        (f"/resources/templates/revisions/{row['id']}/download-link", {}),
        (signed, {}),
        (f"{BINDINGS}/{binding['id']}", {"params": {"template_revision_id": row["id"]}}),
    ):
        assert (await api.get(path, headers=token, **args)).status_code == 403
    assert (
        await api.post(BINDINGS + "/query", headers=token, json={"template_revision_id": row["id"]})
    ).status_code == 403
    for dry_run in (True, False):
        request = {
            **binding_request(row),
            "dry_run": dry_run,
            "expected_static_content_hash": binding["static_content_hash"],
        }
        assert (
            await api.post("/export-template-bindings", headers=token, json=request)
        ).status_code == 403


@pytest.mark.parametrize("disabled,expected", [("user", 401), ("membership", 404), ("org", 403)])
async def test_binding_and_file_live_identity_all_routes(
    disabled, expected, api, headers, tenants, admin_engine
):
    row = await create(api, headers[0])
    binding = await bind(api, headers[0], row)
    signed = (
        await api.get(
            f"/resources/templates/revisions/{row['id']}/download-link", headers=headers[0]
        )
    ).json()["data"]["url"]
    with Session(admin_engine) as session, session.begin():
        if disabled == "user":
            session.get(User, tenants["users"][0]).active = False
        elif disabled == "membership":
            session.scalar(
                select(Membership).where(Membership.org_id == tenants["orgs"][0])
            ).active = False
        else:
            session.get(Org, tenants["orgs"][0]).active = False
    for method, path, args in (
        ("POST", BINDINGS + "/query", {"json": {"template_revision_id": row["id"]}}),
        ("GET", f"{BINDINGS}/{binding['id']}", {"params": {"template_revision_id": row["id"]}}),
        ("GET", signed, {}),
        ("POST", "/export-template-bindings", {"json": binding_request(row)}),
    ):
        assert (await api.request(method, path, headers=headers[0], **args)).status_code == expected


async def test_every_new_route_requires_identity_and_org_header(api, headers):
    row = await create(api, headers[0])
    binding = await bind(api, headers[0], row)
    routes = [
        ("POST", BASE + "/query", {}),
        ("GET", f"{BASE}/{row['template_id']}", None),
        ("POST", f"{BASE}/{row['template_id']}/history/query", {}),
        ("POST", f"{BASE}/{row['template_id']}/lifecycle/history/query", {}),
        ("POST", f"{BASE}/{row['template_id']}/lifecycle", lifecycle()),
        ("POST", BINDINGS + "/query", {"template_revision_id": row["id"]}),
        ("GET", f"{BINDINGS}/{binding['id']}?template_revision_id={row['id']}", None),
    ]
    for method, path, body in routes:
        for missing in (
            {},
            {"X-Org-Id": headers[0]["X-Org-Id"]},
            {"Authorization": headers[0]["Authorization"]},
        ):
            response = await api.request(method, path, headers=missing, json=body)
            assert response.status_code in (401, 422), response.text


async def test_exact_revision_uuid_masks_wrong_root_foreign_and_missing(api, headers):
    first, other, foreign = (
        await create(api, headers[0]),
        await create(api, headers[0]),
        await create(api, headers[1]),
    )
    updated = await revise(api, headers[0], first, name="UUID detail current")
    response = await api.get(
        f"{BASE}/{first['template_id']}", headers=headers[0], params={"revision_id": first["id"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["current_revision"] == 2
    assert response.json()["data"]["detail"]["revision"]["id"] == first["id"]
    assert (
        await api.get(
            f"{BASE}/{first['template_id']}",
            headers=headers[0],
            params={"revision_id": updated["id"]},
        )
    ).json()["data"]["detail"]["revision"]["id"] == updated["id"]
    replies = [
        await api.get(
            f"{BASE}/{first['template_id']}", headers=headers[0], params={"revision_id": revision}
        )
        for revision in (other["id"], foreign["id"], str(uuid4()))
    ]
    assert [reply.status_code for reply in replies] == [404, 404, 404]
    assert (
        len({json.dumps(reply.json()["data"]["error"], sort_keys=True) for reply in replies}) == 1
    )
    assert (
        await api.get(
            f"{BASE}/{first['template_id']}",
            headers=headers[0],
            params={"revision": 1, "revision_id": first["id"]},
        )
    ).status_code == 422


async def test_legacy_binding_stays_inspectable_current_false(api, headers, tenants, application):
    from app.models.exports import ExportTemplateBinding
    from app.services.auth import ROLE_SCOPES, Identity, set_actor_context

    row = await create(api, headers[0])
    current = await bind(api, headers[0], row)
    legacy_keys = (
        "ordinal",
        "tender_clause",
        "source_location",
        "response",
        "deviation",
        "deviation_note",
        "evidence",
    )
    legacy_columns = [
        {"key": key, "width_percent": width}
        for key, width in zip(legacy_keys, (5, 25, 15, 25, 10, 10, 10), strict=True)
    ]
    legacy_id = uuid4()
    org, user = tenants["orgs"][0], tenants["users"][0]
    async with application.state.db.transaction(org) as session:
        await set_actor_context(session, Identity(user, org, set(ROLE_SCOPES["admin"]), "admin"))
        session.add(
            ExportTemplateBinding(
                id=legacy_id,
                org_id=org,
                template_revision_id=UUID(row["id"]),
                template_sha256=current["template_sha256"],
                binding_hash="e" * 64,
                sections=[
                    {**section, "columns": legacy_columns if section["columns"] else []}
                    for section in current["sections"]
                ],
                static_content_hash=current["static_content_hash"],
                adapter_version=current["adapter_version"],
                reviewed_by=user,
                reviewed_at=datetime.now(UTC),
            )
        )
    query = await api.post(
        BINDINGS + "/query", headers=headers[0], json={"template_revision_id": row["id"]}
    )
    assert query.status_code == 200
    assert sorted(item["current"] for item in query.json()["items"]) == [False, True]
    detail = await api.get(
        f"{BINDINGS}/{legacy_id}", headers=headers[0], params={"template_revision_id": row["id"]}
    )
    assert detail.status_code == 200 and detail.json()["data"]["current"] is False
    assert len(detail.json()["data"]["sections"][0]["columns"]) == 7
    # Export-run acceptance in test_exports.py checks that the retained legacy
    # row is rejected by preflight even when the task pins this exact revision.


async def test_read_cursors_bind_token_and_live_token_status(api, headers, tenants, admin_engine):
    rows = [await create(api, headers[0]) for _ in range(2)]
    issued = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic cursor identity",
            "scopes": ["template:read", "template:write"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert issued.status_code == 200
    token = {**headers[0], "Authorization": "Bearer " + issued.json()["data"]["token"]}
    cursor = (await api.post(BASE + "/query", headers=token, json={"limit": 1})).json()["data"][
        "next_cursor"
    ]
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"cursor": cursor})
    ).status_code == 400
    with Session(admin_engine) as session, session.begin():
        session.scalar(
            select(Membership).where(Membership.org_id == tenants["orgs"][0])
        ).role = "bidder"
    assert (
        await api.post(BASE + "/query", headers=token, json={"cursor": cursor})
    ).status_code == 400
    assert (await upload(api, token, export_template())).status_code == 403
    with Session(admin_engine) as session, session.begin():
        session.scalar(select(ApiToken).where(ApiToken.org_id == tenants["orgs"][0])).revoked = True
    for method, path, body in (
        ("POST", BASE + "/query", {}),
        ("GET", f"{BASE}/{rows[0]['template_id']}", None),
        ("POST", f"{BASE}/{rows[0]['template_id']}/history/query", {}),
        ("POST", f"{BASE}/{rows[0]['template_id']}/lifecycle/history/query", {}),
    ):
        assert (await api.request(method, path, headers=token, json=body)).status_code == 401


async def test_lifecycle_audit_failure_rolls_back_root_and_event(
    api, headers, tenants, application, monkeypatch
):
    row = await create(api, headers[0])
    org = tenants["orgs"][0]

    def invalid_audit(session, actor, action, object_id, details):
        session.add(
            AuditLog(
                org_id=org,
                actor_user_id=uuid4(),
                action=action,
                object_id=object_id,
                details=details,
            )
        )

    monkeypatch.setattr(management_templates, "audit", invalid_audit)
    response = await api.post(
        f"{BASE}/{row['template_id']}/lifecycle", headers=headers[0], json=lifecycle()
    )
    assert response.status_code == 409 and not response.json()["ok"]
    detail = (await api.get(f"{BASE}/{row['template_id']}", headers=headers[0])).json()["data"]
    assert detail["lifecycle"] == {"state": "active", "revision": 0}
    assert (
        await api.post(
            f"{BASE}/{row['template_id']}/lifecycle/history/query", headers=headers[0], json={}
        )
    ).json()["items"] == []
    async with application.state.db.transaction(org) as session:
        audits = list(
            (
                await session.scalars(
                    select(AuditLog).where(AuditLog.object_id == UUID(row["template_id"]))
                )
            ).all()
        )
        assert [entry.action for entry in audits] == ["resource.template.create"]


async def test_database_read_timeout_returns_retryable_without_legacy_fallback(
    api, headers, admin_engine
):
    from sqlalchemy import text

    row = await create(api, headers[0])
    # Hold a real table lock in a separate owner connection. The API's 2-second
    # statement budget must cancel its bounded query, not call the legacy list.
    with admin_engine.begin() as connection:
        connection.execute(text("LOCK TABLE templates IN ACCESS EXCLUSIVE MODE"))
        response = await api.post(BASE + "/query", headers=headers[0], json={})
    assert response.status_code == 503, response.text
    error = response.json()["data"]["error"]
    assert error["code"] == "management_query_timeout" and error["exit_code"] == 3
    fresh = await api.get(f"{BASE}/{row['template_id']}", headers=headers[0])
    assert fresh.status_code == 200 and fresh.json()["data"]["current_revision"] == 1
