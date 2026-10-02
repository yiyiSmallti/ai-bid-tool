import asyncio
import hashlib
import json
import time
from datetime import UTC
from pathlib import Path
from uuid import UUID, uuid4

import pymupdf
import pytest
from app.core.errors import ServiceError
from app.core.security import Secrets
from app.models.entities import AuditLog, EvidenceSource, Membership
from app.schemas.evidence_source_contracts import EvidenceSourceArchive, EvidenceSourcePreview
from app.services import evidence_sources as services
from app.services.auth import ROLE_SCOPES
from app.services.certificate_files import validate_file
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_certificate_files import DATA, select_version, setup, upload

BASELINE = json.loads(
    (Path(__file__).resolve().parent / "fixtures/phase8-role-baseline.json").read_text()
)


async def source_fixture(api, headers, pdf_bytes):
    certificate, task = await setup(api, headers)
    scan = (await upload(api, headers, certificate, pdf_bytes)).json()["data"]
    chosen = (await select_version(api, headers, certificate, task)).json()["data"]
    return certificate, task, scan, chosen


async def add(api, headers, task, snapshot, page=1):
    return await api.post(
        f"/tasks/{task}/evidence-sources",
        headers=headers,
        json={"task_certificate_id": snapshot, "page": page},
    )


async def test_genuine_pixels_dedup_fixed_original_and_explicit_history(
    api, headers, pdf_bytes, application
):
    spec = application.openapi()["paths"]["/evidence-sources/{source_id}/preview/download"]["get"][
        "responses"
    ]["200"]["content"]
    assert set(spec) == {"image/png"} and spec["image/png"]["schema"]["format"] == "binary"
    certificate, task, scan, chosen = await source_fixture(api, headers[0], pdf_bytes)
    first = await add(api, headers[0], task, chosen["id"])
    assert first.status_code == 200
    source = first.json()["data"]["source"]
    EvidenceSourceArchive.model_validate(source)
    assert (
        not first.json()["data"]["duplicate"]
        and first.json()["items"] == []
        and first.json()["duration_ms"] > 0
    )
    assert (
        source["original"] == scan["file"]
        and source["certificate_revision_id"] == scan["certificate_revision_id"]
        and source["certificate_file_id"] == scan["id"]
    )
    assert (
        source["status"] == "unconfirmed_source"
        and source["confirmed_by"] is None
        and source["eligible_for_draft_export"] is False
    )
    path = f"/evidence-sources/{source['id']}/preview/download"
    linked = (await api.get(path + "-link", headers=headers[0])).json()
    assert linked["data"]["expires_in"] == 300 and linked["items"] == [source]
    downloaded = await api.get(linked["data"]["url"], headers=headers[0])
    assert (
        downloaded.status_code == 200
        and downloaded.headers["content-type"] == "image/png"
        and "attachment" in downloaded.headers["content-disposition"]
    )
    assert hashlib.sha256(downloaded.content).hexdigest() == source["preview"]["sha256"]
    actual = pymupdf.Pixmap(downloaded.content)
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        expected = pdf[0].get_pixmap(dpi=150, colorspace=pymupdf.csRGB, alpha=False)
        assert (actual.width, actual.height, actual.samples) == (
            expected.width,
            expected.height,
            expected.samples,
        )
    original_read, original_put = application.state.storage.read, application.state.storage.put

    async def forbidden(*args):
        raise AssertionError("Repeat performed storage work")

    application.state.storage.read = application.state.storage.put = forbidden
    repeated = await add(api, headers[0], task, chosen["id"])
    assert repeated.json()["data"] == {"source": source, "duplicate": True}
    application.state.storage.read, application.state.storage.put = original_read, original_put
    second = (await add(api, headers[0], task, chosen["id"], 2)).json()["data"]["source"]
    assert (
        second["id"] != source["id"] and second["preview"]["sha256"] != source["preview"]["sha256"]
    )
    await api.post(
        f"/resources/certificates/{certificate['certificate_id']}/revisions",
        headers=headers[0],
        json={"expected_revision": 2, "data": {**DATA, "name": "Synthetic metadata change"}},
    )
    listing = f"/tasks/{task}/evidence-sources"
    assert len((await api.get(listing, headers=headers[0])).json()["items"]) == 2
    plain = (await select_version(api, headers[0], certificate, task)).json()["data"]
    assert (await api.get(listing, headers=headers[0])).json()["items"] == []
    history = (await api.get(listing, headers=headers[0], params={"history": True})).json()
    assert (
        len(history["items"]) == 2
        and all(not r["active_selection"] for r in history["items"])
        and history["data"]["active_source_ids"] == []
    )
    assert "replaced" in " ".join(history["warnings"])
    assert (await add(api, headers[0], task, chosen["id"])).status_code == 409
    assert (await add(api, headers[0], task, plain["id"])).json()["data"]["error"][
        "code"
    ] == "missing_source_original"
    assert (await api.get(linked["data"]["url"], headers=headers[0])).content == downloaded.content
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        pdf[0].draw_rect((25, 25, 250, 150), color=(1, 0, 0), fill=(1, 0, 0))
        changed = pdf.tobytes()
    new_scan = (await upload(api, headers[0], certificate, changed, expected=3)).json()["data"]
    new_choice = (await select_version(api, headers[0], certificate, task)).json()["data"]
    new_source = (await add(api, headers[0], task, new_choice["id"])).json()["data"]["source"]
    assert (
        new_source["id"] != source["id"]
        and new_source["certificate_revision_id"] == new_scan["certificate_revision_id"]
        and new_source["preview"]["sha256"] != source["preview"]["sha256"]
    )
    async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
        rows = (await session.scalars(select(EvidenceSource))).all()
        events = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "evidence.source.create")
            )
        ).all()
        assert len(rows) == len(events) == 3 and all(
            "SYNTHETIC-ONLY" not in json.dumps(e.details) for e in events
        )
        for row in rows:
            encrypted = application.state.storage.path(row.org_id, row.storage_key).read_bytes()
            assert encrypted.startswith(
                application.state.storage.cipher.marker
            ) and not encrypted.startswith(b"\x89PNG")


async def test_all_four_routes_identity_org_and_foreign_hidden(api, headers, pdf_bytes):
    _, task, _, choice = await source_fixture(api, headers[1], pdf_bytes)
    source = (await add(api, headers[1], task, choice["id"])).json()["data"]["source"]
    path = f"/evidence-sources/{source['id']}/preview/download"
    link = (await api.get(path + "-link", headers=headers[1])).json()["data"]["url"]
    routes = [
        (
            "POST",
            f"/tasks/{task}/evidence-sources",
            {"json": {"task_certificate_id": choice["id"], "page": 1}},
        ),
        ("GET", f"/tasks/{task}/evidence-sources", {}),
        ("GET", path + "-link", {}),
        ("GET", link, {}),
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


@pytest.mark.parametrize("role", ["admin", "bidder", "technical", "viewer"])
async def test_source_role_matrix_preserves_all_prior_grants(
    role, api, headers, pdf_bytes, tenants, admin_engine
):
    _, task, _, choice = await source_fixture(api, headers[0], pdf_bytes)
    source = (await add(api, headers[0], task, choice["id"])).json()["data"]["source"]
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(select(Membership).where(Membership.org_id == tenants["orgs"][0]))
        member.role = role
    assert (await add(api, headers[0], task, choice["id"], 2)).status_code == (
        403 if role == "viewer" else 200
    )
    assert (await api.get(f"/tasks/{task}/evidence-sources", headers=headers[0])).status_code == 200
    assert (
        await api.get(f"/evidence-sources/{source['id']}/preview/download-link", headers=headers[0])
    ).status_code == 200
    later = {
        "evidence:source:read",
        "evidence:source:write",
        "billing:read",
        "billing:redeem",
        "provider:read",
        "provider:write",
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
    }
    assert ROLE_SCOPES[role] - later == set(BASELINE[role])
    # Export is a human bidder responsibility; source-read remains unchanged.
    assert ("export" in ROLE_SCOPES[role]) == (role == "bidder")


@pytest.mark.parametrize(
    "scopes,read,write",
    [
        (["task:read", "certificate:read", "certificate:file:read"], False, False),
        (["evidence:source:read", "evidence:source:write"], False, False),
        (
            ["task:read", "certificate:read", "evidence:source:read", "evidence:source:write"],
            False,
            False,
        ),
        (
            ["task:read", "certificate:file:read", "evidence:source:read", "evidence:source:write"],
            False,
            False,
        ),
        (
            [
                "certificate:read",
                "certificate:file:read",
                "evidence:source:read",
                "evidence:source:write",
            ],
            False,
            False,
        ),
        (
            ["task:read", "certificate:read", "certificate:file:read", "evidence:source:read"],
            True,
            False,
        ),
        (
            ["task:read", "certificate:read", "certificate:file:read", "evidence:source:write"],
            False,
            True,
        ),
        (
            [
                "task:read",
                "certificate:read",
                "certificate:file:read",
                "evidence:source:read",
                "evidence:source:write",
            ],
            True,
            True,
        ),
    ],
)
async def test_source_token_scope_intersection(scopes, read, write, api, headers, pdf_bytes):
    _, task, _, choice = await source_fixture(api, headers[0], pdf_bytes)
    source = (await add(api, headers[0], task, choice["id"])).json()["data"]["source"]
    token = (
        await api.post(
            "/tokens",
            headers=headers[0],
            json={
                "name": "Synthetic bounded scopes",
                "scopes": scopes,
                "expires_at": "2030-01-01T00:00:00Z",
            },
        )
    ).json()["data"]["token"]
    h = {**headers[0], "Authorization": "Bearer " + token}
    path = f"/evidence-sources/{source['id']}/preview/download"
    assert (await api.get(f"/tasks/{task}/evidence-sources", headers=h)).status_code == (
        200 if read else 403
    )
    assert (await api.get(path + "-link", headers=h)).status_code == (200 if read else 403)
    linked = (await api.get(path + "-link", headers=headers[0])).json()["data"]["url"]
    assert (await api.get(linked, headers=h)).status_code == (200 if read else 403)
    assert (await add(api, h, task, choice["id"], 2)).status_code == (200 if write else 403)


async def test_signatures_revoked_member_and_corrupt_preview(
    api, headers, pdf_bytes, application, admin_engine, tenants
):
    _, task, _, choice = await source_fixture(api, headers[0], pdf_bytes)
    source = (await add(api, headers[0], task, choice["id"])).json()["data"]["source"]
    path = f"/evidence-sources/{source['id']}/preview/download"
    crypto = Secrets(application.state.processor.settings.encryption_key.get_secret_value())
    payload = {
        "kind": "source-preview",
        "org_id": headers[0]["X-Org-Id"],
        "source_id": source["id"],
    }
    for changed in [
        {**payload, "kind": "certificate-file-download"},
        {**payload, "org_id": headers[1]["X-Org-Id"]},
        {**payload, "source_id": str(uuid4())},
    ]:
        assert (
            await api.get(
                path, headers=headers[0], params={"signature": crypto.issue(changed, 300)}
            )
        ).status_code == 404
    for signature in ["invalid", crypto.issue(payload, -1)]:
        assert (
            await api.get(path, headers=headers[0], params={"signature": signature})
        ).status_code == 404
    signature = crypto.issue(payload, 300)
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        row = await session.get(EvidenceSource, UUID(source["id"]))
        application.state.storage.path(row.org_id, row.storage_key).write_bytes(
            application.state.storage.cipher.encrypt(row.storage_key, b"corrupt")
        )
    assert (await api.get(path, headers=headers[0], params={"signature": signature})).json()[
        "data"
    ]["error"]["code"] == "source_preview_integrity"
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(select(Membership).where(Membership.org_id == tenants["orgs"][0]))
        member.active = False
    assert (
        await api.get(path, headers=headers[0], params={"signature": signature})
    ).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"task_certificate_id": str(uuid4()), "page": 0},
        {"task_certificate_id": str(uuid4()), "page": 201},
        {"task_certificate_id": "invalid", "page": 1},
        {"task_certificate_id": str(uuid4()), "page": 1, "org_id": "synthetic-sensitive"},
    ],
)
async def test_invalid_source_input_redacted(body, api, headers, pdf_bytes):
    _, task, _, _ = await source_fixture(api, headers[0], pdf_bytes)
    response = await api.post(f"/tasks/{task}/evidence-sources", headers=headers[0], json=body)
    assert (
        response.status_code == 422
        and response.json()["data"]["error"]["exit_code"] == 2
        and "synthetic-sensitive" not in response.text
    )


async def test_source_missing_wrong_task_and_bounded_chunked_json(api, headers, pdf_bytes):
    _, task, _, choice = await source_fixture(api, headers[0], pdf_bytes)
    wrong = (
        await api.post("/tasks", headers=headers[0], json={"name": "Synthetic other task"})
    ).json()["data"]["id"]
    assert (await add(api, headers[0], wrong, choice["id"])).status_code == 404
    assert (await add(api, headers[0], str(uuid4()), choice["id"])).status_code == 404
    assert (await add(api, headers[0], task, str(uuid4()))).status_code == 404
    assert (await add(api, headers[0], task, choice["id"], 3)).status_code == 400

    async def chunks():
        for _ in range(9):
            yield b" " * (16 * 1024)

    response = await api.post(
        f"/tasks/{task}/evidence-sources", headers=headers[0], content=chunks()
    )
    assert response.status_code == 413 and response.json()["data"]["error"]["exit_code"] == 2


async def test_concurrent_sources_store_once(api, headers, pdf_bytes, application, monkeypatch):
    _, task, _, choice = await source_fixture(api, headers[0], pdf_bytes)
    calls = []
    original = application.state.storage.put

    async def counted(*args):
        calls.append(args[1])
        await original(*args)

    monkeypatch.setattr(application.state.storage, "put", counted)
    replies = await asyncio.gather(*(add(api, headers[0], task, choice["id"]) for _ in range(2)))
    assert all(r.status_code == 200 for r in replies) and len(calls) == 1
    assert len({r.json()["data"]["source"]["id"] for r in replies}) == 1 and sorted(
        r.json()["data"]["duplicate"] for r in replies
    ) == [False, True]


@pytest.mark.parametrize("failure", ["read", "original_hash", "put", "audit", "timeout", "size"])
async def test_source_failures_never_commit_and_late_render_never_writes(
    failure, api, headers, pdf_bytes, application, monkeypatch, tenants
):
    _, task, _, choice = await source_fixture(api, headers[0], pdf_bytes)
    writes = []
    original_put = application.state.storage.put

    async def counted(*args):
        writes.append(args[1])
        await original_put(*args)

    monkeypatch.setattr(application.state.storage, "put", counted)

    async def unavailable(*args):
        raise ServiceError("storage_unavailable", "Synthetic storage failure", 503, 3)

    if failure == "read":
        monkeypatch.setattr(application.state.storage, "read", unavailable)
    elif failure == "put":
        monkeypatch.setattr(application.state.storage, "put", unavailable)
    elif failure == "original_hash":

        async def corrupt(*args):
            return b"x" * len(pdf_bytes)

        monkeypatch.setattr(application.state.storage, "read", corrupt)
    elif failure == "audit":

        def fail_audit(session, actor, action, object_id, details):
            session.add(
                AuditLog(
                    org_id=actor.org_id,
                    actor_user_id=uuid4(),
                    action=action,
                    object_id=object_id,
                    details={},
                )
            )

        monkeypatch.setattr(services, "audit", fail_audit)
    elif failure == "size":
        application.state.processor.settings.max_upload_bytes = 1
    else:
        original_render = services.render_page
        completed = []

        def late(*args):
            time.sleep(0.05)
            result = original_render(*args)
            completed.append(True)
            return result

        monkeypatch.setattr(services, "render_page", late)
        monkeypatch.setattr(services, "RENDER_SECONDS", 0.001)
    response = await add(api, headers[0], task, choice["id"])
    assert (
        response.status_code
        == {
            "read": 503,
            "original_hash": 502,
            "put": 503,
            "audit": 409,
            "timeout": 503,
            "size": 413,
        }[failure]
    )
    if failure == "timeout":
        await asyncio.sleep(0.3)
        assert completed and not writes and response.json()["data"]["error"]["exit_code"] == 3
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        assert await session.scalar(select(func.count()).select_from(EvidenceSource)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "evidence.source.create")
            )
            == 0
        )
    previews = list(application.state.storage.root.rglob("*.png"))
    assert len(previews) == (1 if failure == "audit" else 0)
    if previews:
        assert previews[0].read_bytes().startswith(application.state.storage.cipher.marker)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_render_pixels_and_rotation_genuine(rotation):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=280, height=170)
        page.draw_rect((20, 30, 120, 100), color=(0.2, 0.3, 0.8), fill=(0.2, 0.3, 0.8))
        page.set_rotation(rotation)
        content = pdf.tobytes()
    descriptor = validate_file(content, "synthetic.pdf")
    png, preview, timestamp = services.render_page(
        content, descriptor, 1, "synthetic.png", 40 * 1024 * 1024
    )
    services.check_png(png, preview)
    assert timestamp.tzinfo == UTC
    actual = pymupdf.Pixmap(png)
    with pymupdf.open(stream=content, filetype="pdf") as pdf:
        expected = pdf[0].get_pixmap(dpi=150, colorspace=pymupdf.csRGB, alpha=False)
        assert (actual.width, actual.height, actual.samples) == (
            expected.width,
            expected.height,
            expected.samples,
        )


def test_render_rejects_large_pixels_before_allocating():
    with pymupdf.open() as pdf:
        pdf.new_page(width=6000, height=5000)
        content = pdf.tobytes()
    with pytest.raises(ServiceError) as error:
        services.render_page(
            content, validate_file(content, "synthetic.pdf"), 1, "synthetic.png", 40 * 1024 * 1024
        )
    assert error.value.code == "source_preview_limits" and error.value.exit_code == 2


@pytest.mark.parametrize("failure", ["short", "long", "hash", "signature", "dimensions", "decode"])
def test_png_integrity_never_accepts_corruption(failure, pdf_bytes):
    png, preview, _ = services.render_page(
        pdf_bytes, validate_file(pdf_bytes, "synthetic.pdf"), 1, "synthetic.png", 40 * 1024 * 1024
    )
    if failure == "short":
        content = png[:-1]
    elif failure == "long":
        content = png + b"x"
    elif failure == "hash":
        content = b"x" * len(png)
    else:
        content = bytearray(png)
        if failure == "signature":
            content[:8] = b"INVALID!"
        elif failure == "dimensions":
            content[16:20] = (8193).to_bytes(4, "big")
        else:
            content = content[:24] + b"not a valid PNG"
        content = bytes(content)
        preview = EvidenceSourcePreview.model_validate(
            {
                **preview.model_dump(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    with pytest.raises(ServiceError) as error:
        services.check_png(content, preview)
    assert error.value.code == "source_preview_integrity" and error.value.exit_code == 4


async def test_source_token_cannot_exceed_changed_issuer_role(
    api, headers, pdf_bytes, tenants, admin_engine
):
    _, task, _, choice = await source_fixture(api, headers[0], pdf_bytes)
    scopes = [
        "task:read",
        "certificate:read",
        "certificate:file:read",
        "evidence:source:read",
        "evidence:source:write",
    ]
    token = (
        await api.post(
            "/tokens",
            headers=headers[0],
            json={
                "name": "Synthetic changed role",
                "scopes": scopes,
                "expires_at": "2030-01-01T00:00:00Z",
            },
        )
    ).json()["data"]["token"]
    scoped = {**headers[0], "Authorization": "Bearer " + token}
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(select(Membership).where(Membership.org_id == tenants["orgs"][0]))
        member.role = "viewer"
    assert (await add(api, scoped, task, choice["id"])).status_code == 403
    assert (await api.get(f"/tasks/{task}/evidence-sources", headers=scoped)).status_code == 200


@pytest.mark.parametrize("failure", ["damaged", "encrypted", "descriptor"])
def test_original_descriptor_and_pdf_validation_fail_closed(failure, pdf_bytes):
    content = pdf_bytes
    if failure == "damaged":
        content = b"%PDF-1.7\nbroken"
    elif failure == "encrypted":
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
            content = pdf.tobytes(
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="synthetic-only",
                user_pw="synthetic-only",
            )
    descriptor = validate_file(pdf_bytes, "synthetic.pdf").model_copy(
        update={"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
    )
    if failure == "descriptor":
        descriptor = descriptor.model_copy(update={"page_count": 1})
    with pytest.raises(ServiceError):
        services.render_page(content, descriptor, 1, "synthetic.png", 40 * 1024 * 1024)
