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
from app.models.entities import ApiToken, AuditLog
from app.schemas.export_contracts import ExportBindingView
from app.services import management_templates
from app.services.auth import HUMAN_ONLY_SCOPES
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_exports import COLUMNS, SECTIONS, export_template
from test_management_products import lifecycle
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


async def test_lifecycle_human_independent_cas_events_and_audit(
    api, headers, database, admin_engine, application
):
    row = await create(api, headers)
    root = row["template_id"]
    assert (await api.post(BASE + "/query", headers=headers, json={})).status_code == 200
    reply = await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle())
    assert reply.status_code == (200 if True else 403), reply.text
    assert reply.json()["data"]["existing_selections"] == "preserved"
    assert reply.json()["data"]["lifecycle"] == {"state": "inactive", "revision": 1}
    assert reply.json()["data"]["event"]["ref"] == {"kind": "templates", "resource_id": root}
    assert (await api.post(BASE + "/query", headers=headers, json={})).json()["items"] == []
    assert (await api.post(BASE + "/query", headers=headers, json={"state": "inactive"})).json()[
        "data"
    ]["returned"] == 1
    assert (await api.get(f"{BASE}/{root}", headers=headers)).json()["data"]["detail"]["revision"][
        "id"
    ] == row["id"]
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle())
    ).status_code == 409
    await revise(api, headers, row, name="Inactive revised")
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle(1, 1, "active"))
    ).status_code == 409
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle(2, 0, "active"))
    ).status_code == 409
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle(2, 1, "active"))
    ).status_code == 200
    events = (
        await api.post(f"{BASE}/{root}/lifecycle/history/query", headers=headers, json={"limit": 1})
    ).json()
    assert events["items"][0]["revision"] == 2 and events["items"][0]["resource_revision"] == 2
    previous = await api.post(
        f"{BASE}/{root}/lifecycle/history/query",
        headers=headers,
        json={"cursor": events["data"]["next_cursor"]},
    )
    assert [item["revision"] for item in previous.json()["items"]] == [1]
    async with application.state.db.transaction() as session:
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
    row = await create(api, headers)
    issued = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": "Synthetic template token",
            "scopes": ["template:read", "template:write"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert issued.status_code == 200
    token_headers = {**headers, "Authorization": "Bearer " + issued.json()["data"]["token"]}
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
        *(api.post(path, headers=headers, json=lifecycle(2)) for _ in range(2))
    )
    assert sorted(reply.status_code for reply in replies) == [200, 409]


async def test_two_task_pins_template_stop_restore_and_archive(api, headers):
    row = await create(api, headers)
    first, second, empty = [await task(api, headers) for _ in range(3)]
    initial = await pin(api, headers, first, row)
    await pin(api, headers, second, row)
    updated = await revise(api, headers, row, name="Revised template")
    for task_id in (first, second):
        snapshot = (await api.get(f"/tasks/{task_id}/templates", headers=headers)).json()["items"][
            0
        ]
        assert snapshot["template_revision_id"] == row["id"] and snapshot["data"] == row["data"]
    assert (await pin(api, headers, second, row, revision=2)).json()["data"][
        "template_revision_id"
    ] == updated["id"]
    assert (
        await api.post(f"{BASE}/{row['template_id']}/lifecycle", headers=headers, json=lifecycle(2))
    ).status_code == 200
    replay = await pin(api, headers, first, row)
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
        assert (await pin(api, headers, task_id, row, revision, lot)).status_code == 409
    assert (await api.get(f"/tasks/{first}/templates", headers=headers)).json()["items"][0][
        "id"
    ] == initial.json()["data"]["id"]
    assert (
        await api.post(
            f"{BASE}/{row['template_id']}/lifecycle",
            headers=headers,
            json=lifecycle(2, 1, "active"),
        )
    ).status_code == 200
    assert (await pin(api, headers, empty, row)).status_code == 200


async def test_template_tokens_never_gain_any_human_only_scope(api, headers, application, database):
    expiry = datetime.now(UTC) + timedelta(hours=1)
    for scope in sorted(HUMAN_ONLY_SCOPES):
        denied = await api.post(
            "/tokens",
            headers=headers,
            json={
                "name": "Synthetic forbidden scope",
                "scopes": [scope],
                "expires_at": expiry.isoformat(),
            },
        )
        assert denied.status_code == 403, scope
        if scope in {"credential:manage", "clef:manage", "trust-anchor:manage"}:
            continue
        with pytest.raises(DBAPIError) as error:
            async with application.state.db.transaction() as session:
                session.add(
                    ApiToken(
                        name="Synthetic forbidden scope",
                        digest=uuid4().hex * 2,
                        scopes=[scope],
                        expires_at=expiry,
                    )
                )
        assert error.value.orig.sqlstate == "23514", scope


async def test_bounded_name_prefix_pages_exact_file_revision_and_authors(api, headers, database):
    rows = [await create(api, headers, name=f"Alpha template {i}") for i in range(4)]
    await create(
        api,
        headers,
        name="Other",
        project_types=["Alpha type"],
        chapters=[{"title": "Alpha chapter"}],
    )
    query = {"q": "ALP", "limit": 2}
    first = await api.post(BASE + "/query", headers=headers, json=query)
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["command"] == "resource template browse" and page["warnings"]
    assert set(page["data"]) == {"as_of", "returned", "next_cursor", "has_more"}
    assert page["data"]["returned"] == 2 and page["data"]["has_more"]
    assert len(first.content) <= 256 * 1024
    second = await api.post(
        BASE + "/query",
        headers=headers,
        json={**query, "q": "alp", "cursor": page["data"]["next_cursor"]},
    )
    assert second.status_code == 200 and (not second.json()["data"]["has_more"])
    items = page["items"] + second.json()["items"]
    assert {row["ref"]["resource_id"] for row in items} == {row["template_id"] for row in rows}
    for q in ("chapter", "type", "!!!", "alpha | absent"):
        assert (await api.post(BASE + "/query", headers=headers, json={"q": q})).json()[
            "items"
        ] == []
    updated = await revise(api, headers, rows[0], name="Updated template")
    detail = await api.get(
        f"{BASE}/{rows[0]['template_id']}", headers=headers, params={"revision": 1}
    )
    assert detail.status_code == 200, detail.text
    value = detail.json()["data"]
    assert value["current_revision"] == 2 and value["detail"]["kind"] == "templates"
    assert value["detail"]["revision"]["id"] == rows[0]["id"]
    assert value["detail"]["revision"]["file"] == rows[0]["file"]
    history = (
        await api.post(
            f"{BASE}/{rows[0]['template_id']}/history/query", headers=headers, json={"limit": 1}
        )
    ).json()
    assert history["items"][0]["revision_id"] == updated["id"]
    assert history["items"][0]["has_file"] is True
    old = await api.post(
        f"{BASE}/{rows[0]['template_id']}/history/query",
        headers=headers,
        json={"limit": 1, "cursor": history["data"]["next_cursor"]},
    )
    assert [item["revision"] for item in old.json()["items"]] == [1]
    assert (
        await api.get(f"{BASE}/{rows[0]['template_id']}", headers=headers, params={"revision": 3})
    ).status_code == 404
    assert "storage_key" not in detail.text


async def test_cursor_binding_kind_parent_filters_actor_and_expiry(
    api, headers, database, application, admin_engine
):
    rows = [await create(api, headers, name=f"Alpha {i}") for i in range(3)]
    cursor = (await api.post(BASE + "/query", headers=headers, json={"limit": 1})).json()["data"][
        "next_cursor"
    ]
    for hdr, path, body in (
        (headers, BASE + "/query", {"cursor": cursor, "q": "alpha"}),
        (headers, BASE + "/query", {"cursor": cursor, "state": "inactive"}),
        (headers, BASE + "/query", {"cursor": "broken"}),
        (headers, "/v4/management/resources/products/query", {"cursor": cursor}),
        (headers, f"{BASE}/{rows[0]['template_id']}/history/query", {"cursor": cursor}),
        (headers, BINDINGS + "/query", {"template_revision_id": rows[0]["id"], "cursor": cursor}),
    ):
        response = await api.post(path, headers=hdr, json=body)
        assert response.status_code == 400, response.text
        assert response.json()["data"]["error"]["code"] == "management_cursor_invalid"
    payload = json.loads(application.state.crypto.cipher.decrypt(cursor.encode()))
    payload["exp"] = int(time.time()) - 1
    expired = application.state.crypto.cipher.encrypt(json.dumps(payload).encode()).decode()
    assert (
        await api.post(BASE + "/query", headers=headers, json={"cursor": expired})
    ).status_code == 409
    await revise(api, headers, rows[0], name="Next revision")
    history_cursor = (
        await api.post(
            f"{BASE}/{rows[0]['template_id']}/history/query", headers=headers, json={"limit": 1}
        )
    ).json()["data"]["next_cursor"]
    assert (
        await api.post(
            f"{BASE}/{rows[1]['template_id']}/history/query",
            headers=headers,
            json={"cursor": history_cursor},
        )
    ).status_code == 400


async def test_input_unicode_envelope_and_foreign_feature_filters(api, headers):
    query = "汉" * 200
    rows = [await create(api, headers, name=query) for _ in range(2)]
    first = await api.post(BASE + "/query", headers=headers, json={"q": query, "limit": 1})
    assert first.status_code == 200, first.text
    cursor = first.json()["data"]["next_cursor"]
    assert cursor and len(cursor) <= 2048
    second = await api.post(
        BASE + "/query", headers=headers, json={"q": query, "limit": 1, "cursor": cursor}
    )
    assert {
        item["ref"]["resource_id"] for item in first.json()["items"] + second.json()["items"]
    } == {row["template_id"] for row in rows}
    street = await create(api, headers, name="Straße ABC/DEF")
    for q in ("STRASSE", "straße", "ABC/DEF", "stras abc"):
        assert [
            item["ref"]["resource_id"]
            for item in (await api.post(BASE + "/query", headers=headers, json={"q": q})).json()[
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
        assert (await api.post(BASE + "/query", headers=headers, json=body)).status_code == 422
    response = await api.post(
        BASE + "/query",
        headers={**headers, "Content-Type": "application/json"},
        content=b" " * (16 * 1024) + b"{}",
    )
    assert response.status_code == 413
    assert (
        await api.post(BASE.replace("/v4", "") + "/query", headers=headers, json={})
    ).status_code == 200


async def test_binding_human_preview_create_query_detail(api, headers, database, admin_engine):
    row = await create(api, headers)
    binding = await bind(api, headers, row)
    assert (
        await api.post(
            BINDINGS + "/query", headers=headers, json={"template_revision_id": row["id"]}
        )
    ).status_code == 200
    assert (
        await api.get(
            f"{BINDINGS}/{binding['id']}",
            headers=headers,
            params={"template_revision_id": row["id"]},
        )
    ).status_code == 200
    body = binding_request(row)
    preview = await api.post("/export-template-bindings", headers=headers, json=body)
    assert preview.status_code == 200
    body.update(dry_run=False, expected_static_content_hash=binding["static_content_hash"])
    assert (
        await api.post("/export-template-bindings", headers=headers, json=body)
    ).status_code == 200


async def test_binding_query_detail_all_foreign_missing_and_parent_mismatch(api, headers):
    own, wrong = await create(api, headers), await create(api, headers)
    foreign = {**own, "id": str(uuid4())}
    own_binding, foreign_binding = (
        await bind(api, headers, own),
        {"id": str(uuid4())},
    )
    query_replies = [
        await api.post(
            BINDINGS + "/query", headers=headers, json={"template_revision_id": revision}
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
            f"{BINDINGS}/{binding}", headers=headers, params={"template_revision_id": parent}
        )
        assert response.status_code == 404, response.text
    assert (
        await api.post(
            BINDINGS + "/query", headers=headers, json={"template_revision_id": wrong["id"]}
        )
    ).json()["items"] == []
    for row in (foreign, {**own, "id": str(uuid4())}):
        assert (
            await api.post("/export-template-bindings", headers=headers, json=binding_request(row))
        ).status_code == 404


async def test_binding_preview_static_hash_cas_immutable_duplicate_and_revision_independence(
    api, headers, database, application
):
    row = await create(api, headers)
    body = binding_request(row)
    preview = await api.post("/export-template-bindings", headers=headers, json=body)
    assert preview.status_code == 200
    async with application.state.db.transaction() as session:
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
            headers=headers,
            json={**body, "expected_template_sha256": "0" * 64},
        )
    ).status_code == 409
    missing = await api.post(
        "/export-template-bindings", headers=headers, json={**body, "dry_run": False}
    )
    assert missing.status_code == 422
    conflict = await api.post(
        "/export-template-bindings",
        headers=headers,
        json={**body, "dry_run": False, "expected_static_content_hash": "0" * 64},
    )
    assert (
        conflict.status_code == 409
        and conflict.json()["data"]["error"]["code"] == "export_static_hash_conflict"
    )
    binding = await bind(api, headers, row)
    duplicate = await bind(api, headers, row)
    assert duplicate["id"] == binding["id"]
    assert binding["current"] is True
    updated = await revise(api, headers, row, name="New DOCX version")
    assert (
        await api.post(
            BINDINGS + "/query", headers=headers, json={"template_revision_id": updated["id"]}
        )
    ).json()["items"] == []
    retained = await api.get(
        f"{BINDINGS}/{binding['id']}", headers=headers, params={"template_revision_id": row["id"]}
    )
    assert retained.status_code == 200
    assert ExportBindingView.model_validate(
        retained.json()["data"]
    ) == ExportBindingView.model_validate(binding)
    async with application.state.db.transaction() as session:
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
    row, other = (await create(api, headers), await create(api, headers))
    bindings = [
        await bind(api, headers, row, widths=widths)
        for widths in ([6, 36, 44, 14], [7, 35, 44, 14], [8, 34, 44, 14])
    ]
    body = {"template_revision_id": row["id"], "limit": 1}
    first = await api.post(BINDINGS + "/query", headers=headers, json=body)
    cursor = first.json()["data"]["next_cursor"]
    assert first.status_code == 200 and cursor and (len(first.content) <= 256 * 1024)
    assert first.json()["data"]["returned"] == 1
    second = await api.post(BINDINGS + "/query", headers=headers, json={**body, "cursor": cursor})
    assert (
        second.status_code == 200
        and second.json()["items"][0]["id"] != first.json()["items"][0]["id"]
    )
    assert first.json()["items"][0]["id"] == bindings[-1]["id"]
    for hdr, parent in ((headers, other["id"]),):
        response = await api.post(
            BINDINGS + "/query",
            headers=hdr,
            json={"template_revision_id": parent, "cursor": cursor},
        )
    payload = json.loads(application.state.crypto.cipher.decrypt(cursor.encode()))
    payload["exp"] = int(time.time()) - 1
    expired = application.state.crypto.cipher.encrypt(json.dumps(payload).encode()).decode()
    assert (
        await api.post(BINDINGS + "/query", headers=headers, json={**body, "cursor": expired})
    ).status_code == 409
    assert (
        await api.post(f"{BASE}/{row['template_id']}/lifecycle", headers=headers, json=lifecycle())
    ).status_code == 200
    assert (await api.post(BINDINGS + "/query", headers=headers, json=body)).status_code == 200
    assert (
        await api.post("/export-template-bindings", headers=headers, json=binding_request(row))
    ).status_code == 200
    inactive = {
        **binding_request(row),
        "dry_run": False,
        "expected_static_content_hash": bindings[0]["static_content_hash"],
    }
    response = await api.post("/export-template-bindings", headers=headers, json=inactive)
    assert response.status_code == 200 and response.json()["data"]["id"] == bindings[0]["id"]
    added = await bind(api, headers, row, widths=[9, 33, 44, 14])
    assert added["id"] not in {entry["id"] for entry in bindings}


async def test_tokens_read_template_metadata_but_never_files_bindings_or_lifecycle(api, headers):
    row = await create(api, headers)
    binding = await bind(api, headers, row)
    issued = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": "Synthetic template metadata",
            "scopes": ["template:read", "template:write"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert issued.status_code == 200
    token = {**headers, "Authorization": "Bearer " + issued.json()["data"]["token"]}
    assert (await api.post(BASE + "/query", headers=token, json={})).status_code == 200
    signed = (
        await api.get(f"/resources/templates/revisions/{row['id']}/download-link", headers=headers)
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


async def test_every_new_route_requires_identity(api, headers):
    row = await create(api, headers)
    binding = await bind(api, headers, row)
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
        for missing in ({},):
            response = await api.request(method, path, headers=missing, json=body)
            assert response.status_code in (401, 422), response.text


async def test_exact_revision_uuid_masks_wrong_root_foreign_and_missing(api, headers):
    first, other, foreign = (
        await create(api, headers),
        await create(api, headers),
        await create(api, headers),
    )
    updated = await revise(api, headers, first, name="UUID detail current")
    response = await api.get(
        f"{BASE}/{first['template_id']}", headers=headers, params={"revision_id": first["id"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["current_revision"] == 2
    assert response.json()["data"]["detail"]["revision"]["id"] == first["id"]
    assert (
        await api.get(
            f"{BASE}/{first['template_id']}", headers=headers, params={"revision_id": updated["id"]}
        )
    ).json()["data"]["detail"]["revision"]["id"] == updated["id"]
    replies = [
        await api.get(
            f"{BASE}/{first['template_id']}", headers=headers, params={"revision_id": revision}
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
            headers=headers,
            params={"revision": 1, "revision_id": first["id"]},
        )
    ).status_code == 422


async def test_legacy_binding_stays_inspectable_current_false(api, headers, database, application):
    from app.models.exports import ExportTemplateBinding
    from app.services.auth import HUMAN_SCOPES, Identity, set_actor_context

    row = await create(api, headers)
    current = await bind(api, headers, row)
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
    async with application.state.db.transaction() as session:
        await set_actor_context(session, Identity(scopes=set(HUMAN_SCOPES)))
        session.add(
            ExportTemplateBinding(
                id=legacy_id,
                template_revision_id=UUID(row["id"]),
                template_sha256=current["template_sha256"],
                binding_hash="e" * 64,
                sections=[
                    {**section, "columns": legacy_columns if section["columns"] else []}
                    for section in current["sections"]
                ],
                static_content_hash=current["static_content_hash"],
                adapter_version=current["adapter_version"],
                reviewed_at=datetime.now(UTC),
            )
        )
    query = await api.post(
        BINDINGS + "/query", headers=headers, json={"template_revision_id": row["id"]}
    )
    assert query.status_code == 200
    assert sorted(item["current"] for item in query.json()["items"]) == [False, True]
    detail = await api.get(
        f"{BINDINGS}/{legacy_id}", headers=headers, params={"template_revision_id": row["id"]}
    )
    assert detail.status_code == 200 and detail.json()["data"]["current"] is False
    assert len(detail.json()["data"]["sections"][0]["columns"]) == 7


async def test_read_cursors_bind_token_and_live_token_status(api, headers, database, admin_engine):
    rows = [await create(api, headers) for _ in range(2)]
    issued = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": "Synthetic cursor identity",
            "scopes": ["template:read", "template:write"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert issued.status_code == 200
    token = {**headers, "Authorization": "Bearer " + issued.json()["data"]["token"]}
    cursor = (await api.post(BASE + "/query", headers=token, json={"limit": 1})).json()["data"][
        "next_cursor"
    ]
    assert (
        await api.post(BASE + "/query", headers=headers, json={"cursor": cursor})
    ).status_code == 400
    with Session(admin_engine) as session, session.begin():
        session.scalar(select(ApiToken)).revoked = True
    for method, path, body in (
        ("POST", BASE + "/query", {}),
        ("GET", f"{BASE}/{rows[0]['template_id']}", None),
        ("POST", f"{BASE}/{rows[0]['template_id']}/history/query", {}),
        ("POST", f"{BASE}/{rows[0]['template_id']}/lifecycle/history/query", {}),
    ):
        assert (await api.request(method, path, headers=token, json=body)).status_code == 401


async def test_lifecycle_audit_failure_rolls_back_root_and_event(
    api, headers, database, application, monkeypatch
):
    row = await create(api, headers)

    def invalid_audit(session, actor, action, object_id, details):
        session.add(
            AuditLog(actor_token_id=uuid4(), action=action, object_id=object_id, details=details)
        )

    monkeypatch.setattr(management_templates, "audit", invalid_audit)
    response = await api.post(
        f"{BASE}/{row['template_id']}/lifecycle", headers=headers, json=lifecycle()
    )
    assert response.status_code == 409 and (not response.json()["ok"])
    detail = (await api.get(f"{BASE}/{row['template_id']}", headers=headers)).json()["data"]
    assert detail["lifecycle"] == {"state": "active", "revision": 0}
    assert (
        await api.post(
            f"{BASE}/{row['template_id']}/lifecycle/history/query", headers=headers, json={}
        )
    ).json()["items"] == []
    async with application.state.db.transaction() as session:
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

    row = await create(api, headers)
    with admin_engine.begin() as connection:
        connection.execute(text("LOCK TABLE templates IN ACCESS EXCLUSIVE MODE"))
        response = await api.post(BASE + "/query", headers=headers, json={})
    assert response.status_code == 503, response.text
    error = response.json()["data"]["error"]
    assert error["code"] == "management_query_timeout" and error["exit_code"] == 3
    fresh = await api.get(f"{BASE}/{row['template_id']}", headers=headers)
    assert fresh.status_code == 200 and fresh.json()["data"]["current_revision"] == 1
