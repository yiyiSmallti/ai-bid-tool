import asyncio
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.core.errors import ServiceError
from app.models.entities import AuditLog, Membership, Template, TemplateRevision
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def metadata(**patch):
    return {"name": "Synthetic DOCX declaration", "project_types": None, "chapters": None, **patch}


async def upload(
    api, headers, content, *, path="/resources/templates", body=None, name="synthetic.docx"
):
    return await api.post(
        path,
        headers=headers,
        data={"metadata": json.dumps(body or {"data": metadata()})},
        files={"file": (name, content)},
    )


async def template(api, headers, content):
    response = await upload(api, headers, content)
    assert response.status_code == 200, response.text
    assert response.json()["warnings"]
    row = response.json()["data"]
    assert "storage_key" not in row
    return row


async def task(api, headers):
    response = await api.post("/tasks", headers=headers, json={"name": "Synthetic template task"})
    assert response.status_code == 200
    return response.json()["data"]["id"]


async def test_template_versions_fixed_selections_history_download_and_encryption(
    api, headers, docx_bytes, application
):
    row = await template(api, headers[0], docx_bytes)
    assert row["data"]["chapters"] is None
    assert row["file"]["sha256"] == hashlib.sha256(docx_bytes).hexdigest()
    task_id = await task(api, headers[0])
    path = f"/tasks/{task_id}/templates"
    first = (
        await api.post(path, headers=headers[0], json={"template_id": row["template_id"]})
    ).json()["data"]
    updated = await upload(
        api,
        headers[0],
        docx_bytes,
        path=f"/resources/templates/{row['template_id']}/revisions",
        body={
            "expected_revision": 1,
            "data": metadata(
                project_types=["Synthetic", "Synthetic"],
                chapters=[{"title": "Chapter", "children": [{"title": "Subchapter"}]}],
            ),
        },
    )
    assert updated.status_code == 200 and updated.json()["data"]["revision"] == 2
    fixed = (await api.get(path, headers=headers[0])).json()["items"]
    assert fixed[0]["id"] == first["id"] and fixed[0]["revision"] == 1
    assert fixed[0]["data"]["chapters"] is None
    replacement = (
        await api.post(path, headers=headers[0], json={"template_id": row["template_id"]})
    ).json()["data"]
    assert replacement["replaced_snapshot_id"] == first["id"]
    repeated = (
        await api.post(path, headers=headers[0], json={"template_id": row["template_id"]})
    ).json()["data"]
    assert repeated["id"] == replacement["id"] and repeated["duplicate"]
    assert [
        entry["revision"]
        for entry in (await api.get(path, headers=headers[0], params={"history": True})).json()[
            "items"
        ]
    ] == [1, 2]
    lot = await api.post(
        path,
        headers=headers[0],
        json={"template_id": row["template_id"], "revision": 1, "lot": "Synthetic lot"},
    )
    assert lot.status_code == 200 and lot.json()["data"]["revision"] == 1
    assert len((await api.get(path, headers=headers[0])).json()["items"]) == 2
    history = (
        await api.get("/resources/templates", headers=headers[0], params={"history": True})
    ).json()
    assert [entry["revision"] for entry in history["items"]] == [1, 2]
    assert history["items"][1]["data"]["project_types"] == ["Synthetic", "Synthetic"]
    for version in history["items"]:
        linked = await api.get(
            f"/resources/templates/revisions/{version['id']}/download-link", headers=headers[0]
        )
        assert linked.json()["data"]["expires_in"] == 300
        downloaded = await api.get(linked.json()["data"]["url"], headers=headers[0])
        assert downloaded.status_code == 200 and downloaded.content == docx_bytes
        assert "attachment" in downloaded.headers["content-disposition"]
    async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
        revisions = (await session.scalars(select(TemplateRevision))).all()
        for revision in revisions:
            stored = application.state.storage.path(
                revision.org_id, revision.storage_key
            ).read_bytes()
            assert stored.startswith(application.state.storage.cipher.marker)
            assert not stored.startswith(b"PK") and docx_bytes not in stored
        events = (
            await session.scalars(select(AuditLog).where(AuditLog.action.like("%template%")))
        ).all()
        assert len(events) == 5
        assert all("Synthetic" not in json.dumps(event.details) for event in events)


async def test_template_all_routes_foreign_hidden_missing_headers_and_no_identity(
    api, headers, docx_bytes
):
    own = await template(api, headers[0], docx_bytes)
    foreign = await template(api, headers[1], docx_bytes)
    foreign_task = await task(api, headers[1])
    paths = [
        ("GET", "/resources/templates", {"params": {"template_id": foreign["template_id"]}}),
        (
            "POST",
            f"/resources/templates/{foreign['template_id']}/revisions",
            {
                "data": {"metadata": json.dumps({"expected_revision": 1, "data": metadata()})},
                "files": {"file": ("test.docx", docx_bytes)},
            },
        ),
        ("GET", f"/tasks/{foreign_task}/templates", {}),
        ("POST", f"/tasks/{foreign_task}/templates", {"json": {"template_id": own["template_id"]}}),
        ("GET", f"/resources/templates/revisions/{foreign['id']}/download-link", {}),
        (
            "GET",
            f"/resources/templates/revisions/{foreign['id']}/download",
            {"params": {"signature": "invalid"}},
        ),
    ]
    for method, path, kwargs in paths:
        assert (await api.request(method, path, headers=headers[0], **kwargs)).status_code == 404
        assert (
            await api.request(method, path, headers={"X-Org-Id": headers[0]["X-Org-Id"]}, **kwargs)
        ).status_code == 401
        assert (
            await api.request(
                method, path, headers={"Authorization": headers[0]["Authorization"]}, **kwargs
            )
        ).status_code == 422
    own_task = await task(api, headers[0])
    assert (
        await api.post(
            f"/tasks/{own_task}/templates",
            headers=headers[0],
            json={"template_id": foreign["template_id"]},
        )
    ).status_code == 404
    assert (await upload(api, {"X-Org-Id": headers[0]["X-Org-Id"]}, docx_bytes)).status_code == 401
    assert (
        await upload(api, {"Authorization": headers[0]["Authorization"]}, docx_bytes)
    ).status_code == 422
    assert len((await api.get("/resources/templates", headers=headers[0])).json()["items"]) == 1


async def test_template_signature_binding_expiry_revoked_membership_and_integrity(
    api, headers, docx_bytes, application, tenants, admin_engine, monkeypatch
):
    row = await template(api, headers[0], docx_bytes)
    other = await template(api, headers[0], docx_bytes)
    from app.core.security import TokenSigner

    crypto = TokenSigner.for_tokens(application.state.processor.settings)
    path = f"/resources/templates/revisions/{row['id']}/download"
    for patch in [
        {"kind": "download"},
        {"org_id": headers[1]["X-Org-Id"]},
        {"revision_id": other["id"]},
    ]:
        signature = crypto.issue(
            {
                "kind": "template-download",
                "org_id": row["org_id"],
                "revision_id": row["id"],
                **patch,
            },
            300,
        )
        assert (
            await api.get(path, headers=headers[0], params={"signature": signature})
        ).status_code == 404
    expired = crypto.issue(
        {"kind": "template-download", "org_id": row["org_id"], "revision_id": row["id"]}, -1
    )
    assert (
        await api.get(path, headers=headers[0], params={"signature": expired})
    ).status_code == 404
    linked = (await api.get(path + "-link", headers=headers[0])).json()["data"]["url"]
    assert (await api.get(linked, headers={"X-Org-Id": row["org_id"]})).status_code == 401
    original = application.state.storage.read

    async def corrupt(*args):
        return (await original(*args))[:-1]

    monkeypatch.setattr(application.state.storage, "read", corrupt)
    failure = await api.get(linked, headers=headers[0])
    assert (
        failure.status_code == 500
        and failure.json()["data"]["error"]["code"] == "template_integrity"
    )
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(select(Membership).where(Membership.org_id == tenants["orgs"][0]))
        member.active = False
    assert (await api.get(linked, headers=headers[0])).status_code == 404


async def test_template_conflicts_and_concurrent_duplicate_before_file_write(
    api, headers, docx_bytes, application, monkeypatch
):
    row = await template(api, headers[0], docx_bytes)
    writes = []
    original = application.state.storage.put

    async def track(*args):
        writes.append(args[1])
        return await original(*args)

    monkeypatch.setattr(application.state.storage, "put", track)
    updates = await asyncio.gather(
        *(
            upload(
                api,
                headers[0],
                docx_bytes,
                path=f"/resources/templates/{row['template_id']}/revisions",
                body={"expected_revision": 1, "data": metadata(name=name)},
            )
            for name in ("Synthetic A", "Synthetic B")
        )
    )
    assert sorted(response.status_code for response in updates) == [200, 409]
    assert len(writes) == 1
    assert (
        next(response for response in updates if response.status_code == 409).json()["data"][
            "error"
        ]["exit_code"]
        == 4
    )
    task_id = await task(api, headers[0])
    selections = await asyncio.gather(
        *(
            api.post(
                f"/tasks/{task_id}/templates",
                headers=headers[0],
                json={"template_id": row["template_id"]},
            )
            for _ in range(4)
        )
    )
    assert all(response.status_code == 200 for response in selections)
    assert len({response.json()["data"]["id"] for response in selections}) == 1
    assert sum(not response.json()["data"]["duplicate"] for response in selections) == 1


@pytest.mark.parametrize(
    "role,write,select_allowed",
    [
        ("admin", True, True),
        ("bidder", False, True),
        ("technical", False, True),
        ("viewer", False, False),
    ],
)
async def test_template_roles_keep_existing_grants(
    role, write, select_allowed, api, headers, docx_bytes, admin_engine, tenants
):
    row, task_id = await template(api, headers[0], docx_bytes), await task(api, headers[0])
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(select(Membership).where(Membership.org_id == tenants["orgs"][0]))
        member.role = role
    assert (await api.get("/resources/templates", headers=headers[0])).status_code == 200
    assert (await api.get(f"/tasks/{task_id}/templates", headers=headers[0])).status_code == 200
    assert (
        await api.get(
            f"/resources/templates/revisions/{row['id']}/download-link", headers=headers[0]
        )
    ).status_code == 200
    assert (await upload(api, headers[0], docx_bytes)).status_code == (200 if write else 403)
    assert (
        await upload(
            api,
            headers[0],
            docx_bytes,
            path=f"/resources/templates/{row['template_id']}/revisions",
            body={"expected_revision": 1, "data": metadata()},
        )
    ).status_code == (200 if write else 403)
    assert (
        await api.post(
            f"/tasks/{task_id}/templates",
            headers=headers[0],
            json={"template_id": row["template_id"]},
        )
    ).status_code == (200 if select_allowed else 403)


@pytest.mark.parametrize(
    "patch",
    [
        {"name": None},
        {"name": " "},
        {"project_types": [""]},
        {"project_types": ["x" * 201]},
        {"chapters": [{"title": " "}]},
        {"chapters": [{"title": "x", "extra": True}]},
        {"chapters": "fake"},
        {"proof": "fake"},
        {"project_types": ["x"] * 101},
    ],
)
async def test_template_invalid_declarations_never_write(patch, api, headers, docx_bytes):
    response = await upload(api, headers[0], docx_bytes, body={"data": metadata(**patch)})
    assert response.status_code == 422 and not response.json()["ok"]
    assert (await api.get("/resources/templates", headers=headers[0])).json()["items"] == []


@pytest.mark.parametrize(
    "bad",
    [
        "empty",
        "badzip",
        "pdf",
        "docm",
        "path",
        "macro",
        "traversal",
        "overlong",
        "control",
        "encrypted",
        "package_limit",
    ],
)
async def test_template_invalid_files_never_write(bad, api, headers, docx_bytes):
    content, name = docx_bytes, "synthetic.docx"
    if bad == "empty":
        content = b""
    elif bad == "badzip":
        content = b"Synthetic invalid bytes"
    elif bad in ("pdf", "docm"):
        name = "synthetic." + bad
    elif bad == "path":
        name = "../synthetic.docx"
    elif bad == "overlong":
        name = "x" * 201 + ".docx"
    elif bad == "control":
        name = "synthetic\x01.docx"
    else:
        output = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(content)) as source,
            zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target,
        ):
            for entry in source.infolist():
                target.writestr(entry, source.read(entry.filename))
            if bad == "macro":
                target.writestr("word/vbaProject.bin", b"synthetic")
            if bad == "traversal":
                target.writestr("../synthetic", b"synthetic")
            if bad == "package_limit":
                target.writestr("large.bin", b"x" * (101 * 1024 * 1024))
        content = output.getvalue()
        if bad == "encrypted":
            altered = bytearray(content)
            altered[6:8] = (int.from_bytes(altered[6:8], "little") | 1).to_bytes(2, "little")
            central = altered.index(b"PK\x01\x02")
            altered[central + 8 : central + 10] = (
                int.from_bytes(altered[central + 8 : central + 10], "little") | 1
            ).to_bytes(2, "little")
            content = bytes(altered)
    if bad == "control":
        # HTTPX safely percent-encodes control bytes in its normal multipart writer.
        # Use a raw multipart request to exercise the server's actual rejection.
        boundary = "synthetic-template-boundary"
        raw = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n'
                + json.dumps({"data": metadata()})
                + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                + "Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n"
            ).encode()
            + content
            + f"\r\n--{boundary}--\r\n".encode()
        )
        response = await api.post(
            "/resources/templates",
            headers={**headers[0], "Content-Type": f"multipart/form-data; boundary={boundary}"},
            content=raw,
        )
    else:
        response = await upload(api, headers[0], content, name=name)
    assert response.status_code == 400 and response.json()["data"]["error"]["exit_code"] == 2
    assert (await api.get("/resources/templates", headers=headers[0])).json()["items"] == []


async def test_template_metadata_limits_extra_scope_file_limit_and_depth(
    api, headers, docx_bytes, application
):
    for raw in [
        "not json",
        json.dumps({"org_id": headers[1]["X-Org-Id"], "data": metadata()}),
        " " * (128 * 1024 + 1),
    ]:
        response = await api.post(
            "/resources/templates",
            headers=headers[0],
            data={"metadata": raw},
            files={"file": ("synthetic.docx", docx_bytes)},
        )
        assert response.status_code in (413, 422)
        assert raw.strip() not in response.text or not raw.strip()
    tree = {"title": "x"}
    for _ in range(6):
        tree = {"title": "x", "children": [tree]}
    assert (
        await upload(api, headers[0], docx_bytes, body={"data": metadata(chapters=[tree])})
    ).status_code == 422
    application.state.processor.settings.max_upload_bytes = 8
    assert (await upload(api, headers[0], docx_bytes)).status_code == 413
    assert (await api.get("/resources/templates", headers=headers[0])).json()["items"] == []


async def test_template_storage_and_commit_failure_keep_encrypted_unreferenced_object(
    api, headers, docx_bytes, application, monkeypatch
):
    original = application.state.storage.put

    async def unavailable(*args):
        raise ServiceError("storage_unavailable", "Temporary storage unavailable", 503, 3)

    monkeypatch.setattr(application.state.storage, "put", unavailable)
    failed = await upload(api, headers[0], docx_bytes)
    assert failed.status_code == 503 and failed.json()["data"]["error"]["exit_code"] == 3
    monkeypatch.setattr(application.state.storage, "put", original)
    from app.services import versioned

    def invalid_event(session, actor, action, object_id, details):
        session.add(
            AuditLog(
                org_id=actor.org_id,
                actor_user_id=uuid4(),
                action="synthetic-invalid-actor",
                object_id=object_id,
                details={},
            )
        )

    monkeypatch.setattr(versioned, "audit", invalid_event)
    failed = await upload(api, headers[0], docx_bytes)
    assert failed.status_code == 409 and not failed.json()["ok"]
    assert (await api.get("/resources/templates", headers=headers[0])).json()["items"] == []
    async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
        assert await session.scalar(select(func.count()).select_from(Template)) == 0
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
    files = list(application.state.storage.root.rglob("*.docx"))
    assert len(files) == 1 and files[0].read_bytes().startswith(
        application.state.storage.cipher.marker
    )


async def test_template_old_tokens_do_not_gain_access_and_actual_role_intersects(
    api, headers, docx_bytes, admin_engine, tenants
):
    row = await template(api, headers[0], docx_bytes)
    task_id = await task(api, headers[0])

    async def token(scopes):
        response = await api.post(
            "/tokens",
            headers=headers[0],
            json={
                "name": "Synthetic token",
                "scopes": scopes,
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        assert response.status_code == 200
        return {**headers[0], "Authorization": "Bearer " + response.json()["data"]["token"]}

    old = await token(
        [
            "task:read",
            "resource:read",
            "resource:write",
            "task:resource",
            "profile:read",
            "profile:write",
            "task:profile",
        ]
    )
    assert (await upload(api, old, docx_bytes)).status_code == 403
    assert (await api.get("/resources/templates", headers=old)).status_code == 403
    assert (
        await api.get(f"/resources/templates/revisions/{row['id']}/download-link", headers=old)
    ).status_code == 403
    assert (
        await api.post(
            f"/tasks/{task_id}/templates", headers=old, json={"template_id": row["template_id"]}
        )
    ).status_code == 403
    assert (await api.get(f"/tasks/{task_id}/templates", headers=old)).status_code == 403
    selected = await token(["task:template", "template:read", "task:read"])
    assert (
        await api.post(
            f"/tasks/{task_id}/templates",
            headers=selected,
            json={"template_id": row["template_id"]},
        )
    ).status_code == 200
    reader = await token(["template:read"])
    assert (await api.get(f"/tasks/{task_id}/templates", headers=reader)).status_code == 403
    writer = await token(["template:read", "template:write"])
    created = await template(api, writer, docx_bytes)
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(select(Membership).where(Membership.org_id == tenants["orgs"][0]))
        member.role = "bidder"
    assert (await upload(api, writer, docx_bytes)).status_code == 403
    assert created["template_id"] != row["template_id"]
