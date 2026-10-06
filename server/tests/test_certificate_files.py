import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.core.errors import ServiceError
from app.models.entities import AuditLog, CertificateFile, CertificateRevision, Membership
from app.services.auth import ROLE_SCOPES
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

DATA = {
    "kind": "qualification",
    "name": "Synthetic scan declaration",
    "number": "SYNTHETIC-ONLY",
    "valid_from": None,
    "valid_until": None,
}


OLD_ROLE_SCOPES = json.loads(
    (Path(__file__).resolve().parent / "fixtures/phase7-role-baseline.json").read_text()
)


async def setup(api, headers):
    row = (await api.post("/resources/certificates", headers=headers, json={"data": DATA})).json()[
        "data"
    ]
    task = (await api.post("/tasks", headers=headers, json={"name": "Synthetic scan task"})).json()[
        "data"
    ]["id"]
    return row, task


async def upload(api, headers, row, content, *, expected=1, data=None, name="synthetic.pdf"):
    return await api.post(
        f"/resources/certificates/{row['certificate_id']}/file-revisions",
        headers=headers,
        data={"metadata": json.dumps({"expected_revision": expected, "data": data or DATA})},
        files={"file": (name, content)},
    )


async def select_version(api, headers, row, task, revision=None, lot=None):
    return await api.post(
        f"/tasks/{task}/certificates",
        headers=headers,
        json={"certificate_id": row["certificate_id"], "revision": revision, "lot": lot},
    )


async def test_database_forbids_backfilling_committed_revision(
    api, headers, application, tenants, pdf_bytes
):
    from app.services.certificate_files import validate_file

    row, task = await setup(api, headers[0])
    await select_version(api, headers[0], row, task)
    org = tenants["orgs"][0]
    revision = UUID(row["id"])
    certificate = UUID(row["certificate_id"])
    descriptor = validate_file(pdf_bytes, "synthetic.pdf")
    with pytest.raises(DBAPIError) as error:
        async with application.state.db.transaction(org) as session:
            session.add(
                CertificateFile(
                    org_id=org,
                    certificate_id=certificate,
                    certificate_revision_id=revision,
                    created_by=tenants["users"][0],
                    file=descriptor.model_dump(),
                    storage_key=f"org/{org}/certificate/{certificate}/{revision}/{descriptor.sha256}.pdf",
                )
            )
    assert error.value.orig.sqlstate == "23514"
    snapshots = (await api.get(f"/tasks/{task}/certificate-files", headers=headers[0])).json()
    assert snapshots["items"][0]["file"] is None


async def test_scan_versions_no_inheritance_fixed_task_and_actual_download(
    api, headers, pdf_bytes, application
):
    row, task = await setup(api, headers[0])
    old = (await select_version(api, headers[0], row, task)).json()["data"]
    path = f"/tasks/{task}/certificate-files"
    initial = (await api.get(path, headers=headers[0])).json()
    assert (
        initial["items"][0]["file"] is None and initial["items"][0]["certificate_file_id"] is None
    )
    assert "no original PDF" in " ".join(initial["warnings"])
    uploaded = await upload(api, headers[0], row, pdf_bytes)
    assert uploaded.status_code == 200, uploaded.text
    scan = uploaded.json()["data"]
    assert scan["revision"] == 2 and scan["file"]["page_count"] == 2
    assert (
        scan["file"]["sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
        and "storage_key" not in scan
    )
    assert (await api.get(path, headers=headers[0])).json()["items"][0]["file"] is None
    chosen = (await select_version(api, headers[0], row, task)).json()["data"]
    assert chosen["replaced_snapshot_id"] == old["id"]
    repeat = (await select_version(api, headers[0], row, task)).json()["data"]
    assert repeat["duplicate"] and repeat["id"] == chosen["id"]
    listed = (await api.get(path, headers=headers[0])).json()["items"][0]
    assert listed["file"] == scan["file"]
    plain = await api.post(
        f"/resources/certificates/{row['certificate_id']}/revisions",
        headers=headers[0],
        json={"expected_revision": 2, "data": {**DATA, "name": "Synthetic changed declaration"}},
    )
    assert plain.status_code == 200
    current = (
        await api.get(
            "/resources/certificates/files",
            headers=headers[0],
            params={"certificate_id": row["certificate_id"]},
        )
    ).json()
    assert not current["items"] and list(current["data"]["current_revisions"].values()) == [3]
    assert "no original PDF" in " ".join(current["warnings"])
    assert (await api.get(path, headers=headers[0])).json()["items"][0]["file"] == scan["file"]
    await select_version(api, headers[0], row, task)
    history = (await api.get(path, headers=headers[0], params={"history": True})).json()["items"]
    assert [v["revision"] for v in history] == [1, 2, 3] and [
        v["file"] is None for v in history
    ] == [True, False, True]
    lot = await select_version(api, headers[0], row, task, 2, "Synthetic lot")
    assert lot.status_code == 200
    files = (
        await api.get(
            "/resources/certificates/files",
            headers=headers[0],
            params={"certificate_id": row["certificate_id"], "history": True},
        )
    ).json()["items"]
    assert len(files) == 1 and files[0] == scan
    revision = scan["certificate_revision_id"]
    link = (
        await api.get(
            f"/resources/certificates/revisions/{revision}/file/download-link", headers=headers[0]
        )
    ).json()["data"]
    assert link["expires_in"] == 300
    response = await api.get(link["url"], headers=headers[0])
    assert (
        response.content == pdf_bytes
        and response.headers["content-type"] == "application/pdf"
        and "attachment" in response.headers["content-disposition"]
    )
    for unavailable in (row["id"], plain.json()["data"]["id"]):
        assert (
            await api.get(
                f"/resources/certificates/revisions/{unavailable}/file/download-link",
                headers=headers[0],
            )
        ).status_code == 404
    async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
        stored = await session.scalar(select(CertificateFile))
        encrypted = application.state.storage.path(stored.org_id, stored.storage_key).read_bytes()
        assert (
            encrypted.startswith(application.state.storage.cipher.marker)
            and pdf_bytes not in encrypted
        )
        events = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "resource.certificate.file.create")
            )
        ).all()
        assert len(events) == 1 and "SYNTHETIC-ONLY" not in json.dumps(events[0].details)


async def test_every_file_route_fail_closed_and_foreign_hidden(api, headers, pdf_bytes):
    foreign, task = await setup(api, headers[1])
    scan = (await upload(api, headers[1], foreign, pdf_bytes)).json()["data"]
    revision = scan["certificate_revision_id"]
    routes = [
        (
            "POST",
            f"/resources/certificates/{foreign['certificate_id']}/file-revisions",
            {
                "data": {"metadata": json.dumps({"expected_revision": 2, "data": DATA})},
                "files": {"file": ("test.pdf", pdf_bytes)},
            },
        ),
        (
            "GET",
            "/resources/certificates/files",
            {"params": {"certificate_id": foreign["certificate_id"]}},
        ),
        ("GET", f"/tasks/{task}/certificate-files", {}),
        ("GET", f"/resources/certificates/revisions/{revision}/file/download-link", {}),
        (
            "GET",
            f"/resources/certificates/revisions/{revision}/file/download",
            {"params": {"signature": "invalid"}},
        ),
    ]
    for method, path, options in routes:
        assert (await api.request(method, path, headers=headers[0], **options)).status_code == 404
        assert (
            await api.request(method, path, headers={"X-Org-Id": headers[0]["X-Org-Id"]}, **options)
        ).status_code == 401
        assert (
            await api.request(
                method, path, headers={"Authorization": headers[0]["Authorization"]}, **options
            )
        ).status_code == 422
    assert (
        await api.get(
            "/resources/certificates/files", headers=headers[0], params={"revision_id": revision}
        )
    ).status_code == 404


async def test_scan_signature_expiry_member_and_integrity(
    api, headers, pdf_bytes, application, admin_engine
):
    row, _ = await setup(api, headers[0])
    scan = (await upload(api, headers[0], row, pdf_bytes)).json()["data"]
    revision = scan["certificate_revision_id"]
    path = f"/resources/certificates/revisions/{revision}/file/download"
    payload = {
        "kind": "certificate-file-download",
        "org_id": headers[0]["X-Org-Id"],
        "revision_id": revision,
    }
    assert (
        await api.get(path, headers=headers[0], params={"signature": "invalid"})
    ).status_code == 404
    from app.core.security import TokenSigner

    crypto = TokenSigner.for_tokens(application.state.processor.settings)
    for changed in (
        {**payload, "kind": "template-download"},
        {**payload, "org_id": headers[1]["X-Org-Id"]},
        {**payload, "revision_id": str(uuid4())},
    ):
        assert (
            await api.get(
                path, headers=headers[0], params={"signature": crypto.issue(changed, 300)}
            )
        ).status_code == 404
    assert (
        await api.get(path, headers=headers[0], params={"signature": crypto.issue(payload, -1)})
    ).status_code == 404
    signature = crypto.issue(payload, 300)
    assert (
        await api.get(
            path, headers={"X-Org-Id": headers[0]["X-Org-Id"]}, params={"signature": signature}
        )
    ).status_code == 401
    async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
        file = await session.scalar(select(CertificateFile))
        application.state.storage.path(file.org_id, file.storage_key).write_bytes(
            application.state.storage.cipher.encrypt(file.storage_key, b"truncated")
        )
    assert (await api.get(path, headers=headers[0], params={"signature": signature})).json()[
        "data"
    ]["error"]["code"] == "certificate_file_integrity"
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(
            select(Membership).where(Membership.org_id == UUID(headers[0]["X-Org-Id"]))
        )
        member.active = False
    assert (
        await api.get(path, headers=headers[0], params={"signature": signature})
    ).status_code == 404


async def test_concurrent_old_version_only_one_file_write(
    api, headers, pdf_bytes, application, monkeypatch
):
    row, _ = await setup(api, headers[0])
    calls = []
    original = application.state.storage.put

    async def counted(*args):
        calls.append(args[1])
        await original(*args)

    monkeypatch.setattr(application.state.storage, "put", counted)
    results = await asyncio.gather(*(upload(api, headers[0], row, pdf_bytes) for _ in range(2)))
    assert sorted(r.status_code for r in results) == [200, 409] and len(calls) == 1
    assert (
        next(r for r in results if r.status_code == 409).json()["data"]["error"]["exit_code"] == 4
    )


@pytest.mark.parametrize("role", ["admin", "bidder", "technical", "viewer"])
async def test_new_scan_roles_and_old_grants(role, api, headers, pdf_bytes, tenants, admin_engine):
    from app.services.auth import SCOPES

    human_workflow_scopes = {"req:confirm", "req:manual", "evidence:annotate"}
    assert ROLE_SCOPES[role] & human_workflow_scopes == (
        human_workflow_scopes if role in {"admin", "bidder", "technical"} else set()
    )
    assert human_workflow_scopes.isdisjoint(SCOPES)

    row, task = await setup(api, headers[0])
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(select(Membership).where(Membership.org_id == tenants["orgs"][0]))
        member.role = role
    response = await upload(api, headers[0], row, pdf_bytes)
    assert response.status_code == (200 if role in ("admin", "bidder") else 403)
    assert (await api.get("/resources/certificates/files", headers=headers[0])).status_code == 200
    assert (
        await api.get(f"/tasks/{task}/certificate-files", headers=headers[0])
    ).status_code == 200
    old = OLD_ROLE_SCOPES
    assert ROLE_SCOPES[role] - {
        "certificate:file:read",
        "certificate:file:write",
        "evidence:source:read",
        "evidence:source:write",
        "evidence:annotate",
        "billing:read",
        "billing:redeem",
        "provider:read",
        "provider:write",
        "screenshot:read",
        "screenshot:write",
        "screenshot:ingest",
        "confidential:read",
        "confidential:write",
        "confidential:reveal",
        "check:read",
        "check:run",
        "check:decide",
        "score:read",
        "score:run",
        "score:rubric:generate",
        "score:rubric:review",
        "agent:read",
        "agent:run",
        "agent:cancel",
        "task:members:write",
        "task:archive",
        "card:assign",
        "card:comment",
        "task:review-policy",
        "card:cosign",
        "task:budget:write",
        "billing:alert:write",
        "memory:read",
        "memory:write",
        "memory:retrieve",
        "memory:candidate:run",
        "memory:approve",
        "memory:manage",
        "memory:eval:read",
        "memory:eval:review",
        "card:read",
        "card:write",
        "card:generate",
        "draft:run",
        "draft:read",
        "evidence:confirm",
        "export",
        "sandbox:read",
        "sandbox:render",
        "sandbox:capture",
        "req:confirm",
        "req:manual",
    } == set(old[role])


@pytest.mark.parametrize(
    "invalid",
    [
        "empty",
        "damaged",
        "docx",
        "encrypted",
        "owner_encrypted",
        "too_many_pages",
        "bad_name",
        "traversal",
        "control",
    ],
)
async def test_bad_files_never_store(
    invalid, api, headers, pdf_bytes, docx_bytes, application, monkeypatch
):
    import pymupdf

    row, _ = await setup(api, headers[0])
    content = pdf_bytes
    name = "test.pdf"
    calls = []

    async def forbidden(*args):
        calls.append(args)
        raise AssertionError("Invalid file stored")

    monkeypatch.setattr(application.state.storage, "put", forbidden)
    if invalid == "empty":
        content = b""
    elif invalid == "damaged":
        content = b"%PDF-1.7\nbroken"
    elif invalid == "docx":
        content = docx_bytes
    elif invalid in ("encrypted", "owner_encrypted"):
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
            content = pdf.tobytes(
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="synthetic-owner",
                user_pw="synthetic-user" if invalid == "encrypted" else "",
            )
    elif invalid == "too_many_pages":
        with pymupdf.open() as pdf:
            for _ in range(201):
                pdf.new_page()
            content = pdf.tobytes()
    elif invalid == "bad_name":
        name = "test.png"
    elif invalid == "traversal":
        name = "../test.pdf"
    elif invalid == "control":
        # HTTPX percent-escapes controls in filenames; supply genuine raw multipart.
        raw = (
            b'--synthetic\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n'
            + json.dumps({"expected_revision": 1, "data": DATA}).encode()
            + b'\r\n--synthetic\r\nContent-Disposition: form-data; name="file"; filename="bad\x01.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
            + content
            + b"\r\n--synthetic--\r\n"
        )
        response = await api.post(
            f"/resources/certificates/{row['certificate_id']}/file-revisions",
            headers={**headers[0], "Content-Type": "multipart/form-data; boundary=synthetic"},
            content=raw,
        )
        assert response.status_code == 400 and not calls
        return
    response = await upload(api, headers[0], row, content, name=name)
    assert response.status_code == 400 and not calls


@pytest.mark.parametrize(
    "body",
    [
        {"expected_revision": 1, "data": {**DATA, "name": " "}},
        {"expected_revision": 0, "data": DATA},
        {"expected_revision": 1, "data": DATA, "org_id": "fake"},
        {
            "expected_revision": 1,
            "data": {**DATA, "valid_from": "2030-01-01", "valid_until": "2020-01-01"},
        },
        "not json",
        "oversize",
    ],
)
async def test_metadata_failures_redacted(body, api, headers, pdf_bytes, application, monkeypatch):
    row, _ = await setup(api, headers[0])
    calls = []

    async def forbidden(*args):
        calls.append(args)
        raise AssertionError("Invalid input stored")

    monkeypatch.setattr(application.state.storage, "put", forbidden)
    raw = (
        "x" * (128 * 1024 + 1)
        if body == "oversize"
        else body
        if isinstance(body, str)
        else json.dumps(body)
    )
    response = await api.post(
        f"/resources/certificates/{row['certificate_id']}/file-revisions",
        headers=headers[0],
        data={"metadata": raw},
        files={"file": ("test.pdf", pdf_bytes)},
    )
    assert (
        response.status_code in (413, 422) and not calls and "SYNTHETIC-ONLY" not in response.text
    )


async def test_limits_filters_and_storage_commit_rollback(
    api, headers, pdf_bytes, application, monkeypatch
):
    row, _ = await setup(api, headers[0])
    application.state.processor.settings.max_upload_bytes = 1
    assert (await upload(api, headers[0], row, pdf_bytes)).status_code == 413
    application.state.processor.settings.max_upload_bytes = 40 * 1024 * 1024
    assert (
        await api.get(
            "/resources/certificates/files",
            headers=headers[0],
            params={"revision_id": row["id"], "history": True},
        )
    ).status_code == 400
    original = application.state.storage.put

    async def failure(*args):
        raise ServiceError("storage_unavailable", "Synthetic failure", 503, 3)

    monkeypatch.setattr(application.state.storage, "put", failure)
    assert (await upload(api, headers[0], row, pdf_bytes)).status_code == 503
    monkeypatch.setattr(application.state.storage, "put", original)
    from app.services import certificate_files

    def failed_audit(session, actor, action, object_id, details):
        session.add(
            AuditLog(
                org_id=actor.org_id,
                actor_user_id=uuid4(),
                action="synthetic-invalid-actor",
                object_id=object_id,
                details={},
            )
        )

    monkeypatch.setattr(certificate_files, "audit", failed_audit)
    assert (await upload(api, headers[0], row, pdf_bytes)).status_code == 409
    async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
        assert await session.scalar(select(func.count()).select_from(CertificateFile)) == 0
        assert await session.scalar(select(func.count()).select_from(CertificateRevision)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "resource.certificate.file.create")
            )
            == 0
        )
    files = list(application.state.storage.root.rglob("*.pdf"))
    assert len(files) == 1
    assert files[0].read_bytes().startswith(application.state.storage.cipher.marker)


async def test_old_tokens_and_scope_intersection(
    api, headers, pdf_bytes, application, tenants, admin_engine
):
    from app.core.security import token_digest
    from app.models.entities import ApiToken

    row, task = await setup(api, headers[0])
    scan = (await upload(api, headers[0], row, pdf_bytes)).json()["data"]
    revision = scan["certificate_revision_id"]
    scopes = ["certificate:read", "certificate:write", "task:certificate", "task:read"]
    response = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic old scopes",
            "scopes": scopes,
            "expires_at": "2030-01-01T00:00:00Z",
        },
    )
    token = response.json()["data"]["token"]
    h = {**headers[0], "Authorization": "Bearer " + token}
    assert (await api.get("/resources/certificates", headers=h)).status_code == 200
    assert (await api.get("/resources/certificates/files", headers=h)).status_code == 403
    assert (await upload(api, h, row, pdf_bytes, expected=2)).status_code == 403
    assert (await api.get(f"/tasks/{task}/certificate-files", headers=h)).status_code == 403
    assert (
        await api.get(f"/resources/certificates/revisions/{revision}/file/download-link", headers=h)
    ).status_code == 403

    # A signed token cannot exceed its issuer's current role grants.
    secret = "bid_synthetic-file-writer-token"
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(select(Membership).where(Membership.org_id == tenants["orgs"][0]))
        member.role = "viewer"
        session.add(
            ApiToken(
                org_id=tenants["orgs"][0],
                user_id=tenants["users"][0],
                name="Synthetic",
                digest=token_digest(secret),
                scopes=["certificate:write", "certificate:file:write"],
                expires_at=datetime(2030, 1, 1, tzinfo=UTC),
            )
        )
    assert (
        await upload(
            api, {**headers[0], "Authorization": "Bearer " + secret}, row, pdf_bytes, expected=2
        )
    ).status_code == 403


@pytest.mark.parametrize(
    "scopes,read,write,task_read",
    [
        (["certificate:file:read"], False, False, False),
        (["certificate:file:write"], False, False, False),
        (["certificate:read", "certificate:file:read"], True, False, False),
        (["certificate:write", "certificate:file:write"], False, True, False),
        (["certificate:read", "certificate:file:read", "task:read"], True, False, True),
    ],
)
async def test_explicit_new_token_scope_combinations(
    scopes, read, write, task_read, api, headers, pdf_bytes
):
    row, task = await setup(api, headers[0])
    scanned = (await upload(api, headers[0], row, pdf_bytes)).json()["data"]
    token_response = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic scope matrix",
            "scopes": scopes,
            "expires_at": "2030-01-01T00:00:00Z",
        },
    )
    assert token_response.status_code == 200
    scoped = {**headers[0], "Authorization": "Bearer " + token_response.json()["data"]["token"]}
    assert (await api.get("/resources/certificates/files", headers=scoped)).status_code == (
        200 if read else 403
    )
    assert (await upload(api, scoped, row, pdf_bytes, expected=2)).status_code == (
        200 if write else 403
    )
    assert (await api.get(f"/tasks/{task}/certificate-files", headers=scoped)).status_code == (
        200 if task_read else 403
    )
    revision = scanned["certificate_revision_id"]
    assert (
        await api.get(
            f"/resources/certificates/revisions/{revision}/file/download-link", headers=scoped
        )
    ).status_code == (200 if read else 403)
