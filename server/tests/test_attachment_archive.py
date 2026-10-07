"""Attachment first-slice acceptance specified before runtime implementation.

Failure inventory: implicit file inheritance or latest-revision retargeting; invalid
review transitions, forged file/hash/prior decision or stale state; departed assignee
silently approving; blank/foreign/mismatched declaration links; wrong task profile;
raw bytes or labels reaching tokens or human team readers; clearance leaking across
pages or extraction runs; privacy hold reusing old clearance; forged/expired links;
archive/link/selection revival; competing writes publishing partial state; storage
failure reporting success; committed immutable parents accepting later files; RLS,
composite FK or CHECK rejection being replaced by parent-dependent trigger errors.

All workflows use synthetic files and PostgreSQL-backed HTTP/services. No external
providers are called. Artifacts contain scenario gates and hashes, never credentials,
signed URLs, labels from real documents or uploaded multipart payloads.
"""

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest
from app.core.security import TokenSigner
from app.models.entities import AuditLog, Membership, UsageRecord, User
from app.services.auth import AGENT_SCOPES, HUMAN_ONLY_SCOPES, ROLE_SCOPES, SCOPES
from conftest import PASSWORD, PASSWORD_HASH
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from test_profiles import metadata, profile, task
from test_response_cards import create_tender, phase_one_client, sanitized_artifact
from test_screenshot_renderer import renderer as renderer  # noqa: F401 -- real renderer dependency

BASE = "/resources/attachments"
OUTPUT = Path(__file__).resolve().parents[2] / "data/work/attachment-archive-acceptance"
HUMAN_ATTACHMENT_SCOPES = {
    "attachment:write",
    "attachment:review",
    "attachment:manage",
    "attachment:original:read",
    "attachment:page:read",
    "attachment:privacy",
    "task:attachment",
}


class V4API:
    """Use Result 4.0 without changing the shared legacy ASGI fixture."""

    def __init__(self, client):
        self.client = client

    async def request(self, method, url, **kwargs):
        path = str(url)
        if path.startswith("/") and not (path == "/v4" or path.startswith("/v4/")):
            path = "/v4" + path
        return await self.client.request(method, path, **kwargs)

    async def get(self, url, **kwargs):
        return await self.request("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self.request("POST", url, **kwargs)


@pytest.fixture
def api(api):
    return V4API(api)


def artifact(name, data):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / f"{name}.json").write_text(
        json.dumps(sanitized_artifact(data), ensure_ascii=False, indent=2) + "\n"
    )


def result(response):
    assert response.status_code == 200, response.text
    envelope = response.json()
    assert set(envelope) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    assert envelope["ok"] is True and envelope["cost"]["basis"] == "zero"
    assert envelope["cost"]["charge"] == envelope["cost"]["task_amount"] == "0"
    return envelope["data"]


def error_code(response):
    data = response.json()["data"]
    return data.get("code") or data.get("error", {}).get("code")


def upload_body(user, **changes):
    return {
        "request_id": str(uuid4()),
        "metadata": {"kind": "contract", "label": "Synthetic confidential archive label"},
        "reviewer_user_id": str(user),
        **changes,
    }


async def upload(api, header, user, pdf, *, body=None, root=None):
    return await api.post(
        BASE if root is None else f"{BASE}/{root}/revisions",
        headers=header,
        data={"metadata": json.dumps(body or upload_body(user))},
        files=[("files", ("synthetic-private-contract.pdf", pdf, "application/pdf"))],
    )


async def show(api, header, root):
    return result(await api.get(f"{BASE}/{root}", headers=header))["attachment"]


async def revision(api, header, revision_id):
    return result(await api.get(f"{BASE}/revisions/{revision_id}", headers=header))


async def review(api, header, written, *, decision="approve", reason=None, request_id=None):
    root = await show(api, header, written["attachment_id"])
    fixed = await revision(api, header, written["revision_id"])
    body = {
        "request_id": request_id or str(uuid4()),
        "expected_state_version": root["state_version"],
        "expected_review_id": fixed["latest_review_id"],
        "file_id": fixed["file_id"],
        "original_sha256": fixed["original_sha256"],
        "metadata_sha256": fixed["metadata_sha256"],
        "decision": decision,
        "reason": reason
        or ("accepted_for_internal_use" if decision == "approve" else "wrong_document"),
    }
    return await api.post(f"{BASE}/revisions/{fixed['id']}/reviews", headers=header, json=body)


async def archive_fixture(api, header, user, pdf, *, task_id=None):
    written = result(await upload(api, header, user, pdf))
    approved = result(await review(api, header, written))
    declared = await profile(api, header)
    task_id = task_id or await task(api, header)
    selected_profile = result(
        await api.post(
            f"/tasks/{task_id}/profiles",
            headers=header,
            json={"profile_id": declared["profile_id"], "revision": 1, "lot": "Synthetic lot"},
        )
    )
    linked = result(
        await api.post(
            f"/resources/profiles/revisions/{declared['id']}/attachments",
            headers=header,
            json={
                "request_id": str(uuid4()),
                "field": "performance_summary",
                "attachment_revision_id": written["revision_id"],
                "approval_id": approved["id"],
            },
        )
    )
    selected = result(
        await api.post(
            f"/tasks/{task_id}/attachments",
            headers=header,
            json={
                "request_id": str(uuid4()),
                "task_org_profile_id": selected_profile["id"],
                "profile_attachment_link_id": linked["id"],
                "expected_selection_id": None,
            },
        )
    )
    return {
        "written": written,
        "approved": approved,
        "profile": declared,
        "task": task_id,
        "task_profile": selected_profile,
        "link": linked,
        "selection": selected,
    }


async def source(api, header, chain, page=1, request_id=None):
    return await api.post(
        f"/tasks/{chain['task']}/attachment-sources",
        headers=header,
        json={
            "request_id": request_id or str(uuid4()),
            "task_attachment_id": chain["selection"]["id"],
            "page": page,
        },
    )


async def token(api, header, scopes):
    return await api.post(
        "/tokens",
        headers=header,
        json={
            "name": "Synthetic attachment metadata token",
            "scopes": scopes,
            "expires_at": "2030-01-01T00:00:00Z",
        },
    )


async def test_exact_pdf_review_link_task_source_and_encrypted_history(
    api, headers, tenants, pdf_bytes, application
):
    from app.schemas.attachment_contracts import AttachmentRevisionView, AttachmentSourceArchive

    chain = await archive_fixture(api, headers[0], tenants["users"][0], pdf_bytes)
    written = chain["written"]
    fixed = await revision(api, headers[0], written["revision_id"])
    AttachmentRevisionView.model_validate(fixed)
    assert fixed["revised_at"] == fixed["created_at"]
    assert fixed["revised_by"] == str(tenants["users"][0])
    assert fixed["original_sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    assert fixed["parts"] == [] and fixed["original"]["page_count"] == 2
    root = await show(api, headers[0], written["attachment_id"])
    assert root["custodian_user_id"] == root["reviewer_user_id"] == str(tenants["users"][0])
    assert root["revised_at"] == fixed["created_at"]
    assert root["revised_by"] == str(tenants["users"][0])
    assert chain["approved"]["reviewed_by"] == str(tenants["users"][0])
    original = result(
        await api.get(f"{BASE}/revisions/{fixed['id']}/file/download-link", headers=headers[0])
    )
    downloaded = await api.get(original["url"], headers=headers[0])
    assert downloaded.content == pdf_bytes
    assert downloaded.headers["cache-control"] == "no-store"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in downloaded.headers["content-disposition"]
    request_id = str(uuid4())
    archived = result(await source(api, headers[0], chain, request_id=request_id))
    actual = AttachmentSourceArchive.model_validate(archived["source"])
    assert actual.dpi == 150 and actual.render_profile == "pdf-page-preview-v1"
    assert actual.confirmed_by is None and actual.eligible_for_draft_export is False
    assert {"privacy_pending", "extraction_required"} <= set(actual.readiness.blockers)
    raw_link = result(
        await api.get(f"/attachment-sources/{actual.id}/preview/download-link", headers=headers[0])
    )
    raw = await api.get(raw_link["url"], headers=headers[0])
    pixels = pymupdf.Pixmap(raw.content)
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        expected = document[0].get_pixmap(dpi=150, colorspace=pymupdf.csRGB, alpha=False)
        assert (pixels.width, pixels.height, pixels.samples) == (
            expected.width,
            expected.height,
            expected.samples,
        )
    assert hashlib.sha256(raw.content).hexdigest() == actual.source_png_sha256
    repeat = result(await source(api, headers[0], chain, request_id=request_id))
    assert repeat["duplicate"] and repeat["source"]["id"] == str(actual.id)
    updated = result(
        await upload(
            api,
            headers[0],
            tenants["users"][0],
            pdf_bytes,
            root=written["attachment_id"],
            body=upload_body(tenants["users"][0], expected_state_version=root["state_version"]),
        )
    )
    assert updated["revision"] == 2
    chosen = result(await api.get(f"/tasks/{chain['task']}/attachments", headers=headers[0]))
    assert chosen is not None
    assert (await api.get(f"/tasks/{chain['task']}/attachments", headers=headers[0])).json()[
        "items"
    ][0]["attachment_revision_id"] == written["revision_id"]
    pinned = (await api.get(f"/tasks/{chain['task']}/attachments", headers=headers[0])).json()[
        "items"
    ][0]
    assert pinned["library_updated"] is True and pinned["current_library_revision"] == 2
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        assert await session.scalar(select(func.count()).select_from(UsageRecord)) == 0
        events = list(
            (
                await session.scalars(select(AuditLog).where(AuditLog.action.like("%attachment%")))
            ).all()
        )
        assert events and all(
            "confidential archive label" not in json.dumps(event.details) for event in events
        )
        keys = (
            (
                await session.execute(
                    text(
                        "SELECT storage_key FROM attachment_files UNION ALL SELECT storage_key FROM evidence_sources WHERE source_kind='user_supplied_attachment_pdf'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(keys) == 3
        for key in keys:
            encrypted = application.state.storage.path(tenants["orgs"][0], key).read_bytes()
            assert encrypted.startswith(application.state.storage.cipher.marker)
            assert pdf_bytes not in encrypted
    artifact(
        "exact-chain",
        {
            "scenario": "unchanged-pdf-exact-review-source",
            "expected_gate": "privacy_pending",
            "fixture_pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "source_png_sha256": actual.source_png_sha256,
            "selection": chain["selection"],
            "reviewed_by": chain["approved"]["reviewed_by"],
            "result": archived,
        },
    )


async def test_review_transitions_replay_hashes_and_stale_state(api, headers, tenants, pdf_bytes):
    written = result(await upload(api, headers[0], tenants["users"][0], pdf_bytes))
    rejected = result(await review(api, headers[0], written, decision="reject"))
    assert rejected["decision"] == "reject"
    assert (await review(api, headers[0], written, decision="reject")).status_code == 409
    approved = result(await review(api, headers[0], written))
    assert approved["prior_review_id"] == rejected["id"]
    assert (await review(api, headers[0], written, decision="reject")).status_code == 409
    revoked = result(
        await review(api, headers[0], written, decision="revoke", reason="withdrawn_by_org")
    )
    assert revoked["prior_review_id"] == approved["id"]
    again = result(await review(api, headers[0], written))
    assert again["id"] != approved["id"] and again["prior_review_id"] == revoked["id"]
    root = await show(api, headers[0], written["attachment_id"])
    fixed = await revision(api, headers[0], written["revision_id"])
    for patch in (
        {"expected_state_version": 1},
        {"original_sha256": "f" * 64},
        {"metadata_sha256": "f" * 64},
        {"file_id": str(uuid4())},
        {"expected_review_id": str(uuid4())},
    ):
        body = {
            "request_id": str(uuid4()),
            "expected_state_version": root["state_version"],
            "expected_review_id": again["id"],
            "file_id": fixed["file_id"],
            "original_sha256": fixed["original_sha256"],
            "metadata_sha256": fixed["metadata_sha256"],
            "decision": "revoke",
            "reason": "withdrawn_by_org",
            **patch,
        }
        denied = await api.post(
            f"{BASE}/revisions/{fixed['id']}/reviews", headers=headers[0], json=body
        )
        assert denied.status_code in {404, 409}, denied.text
    history = (
        await api.get(
            f"{BASE}/revisions/{fixed['id']}/reviews", headers=headers[0], params={"limit": 2}
        )
    ).json()
    assert len(history["items"]) == 2 and history["data"]["next_cursor"]
    cursor = history["data"]["next_cursor"]
    following = (
        await api.get(
            f"{BASE}/revisions/{fixed['id']}/reviews",
            headers=headers[0],
            params={"limit": 2, "cursor": cursor},
        )
    ).json()
    assert not {item["id"] for item in history["items"]} & {
        item["id"] for item in following["items"]
    }


async def test_upload_replay_dry_run_and_concurrent_revisions(
    api, headers, tenants, pdf_bytes, application
):
    body = upload_body(tenants["users"][0])
    dry = result(
        await upload(
            api, headers[0], tenants["users"][0], pdf_bytes, body={**body, "dry_run": True}
        )
    )
    assert dry["dry_run"] and dry["enabled_upload_mode"] == "single_pdf"
    assert (await api.get(BASE, headers=headers[0])).json()["items"] == []
    written = result(await upload(api, headers[0], tenants["users"][0], pdf_bytes, body=body))
    repeat = result(await upload(api, headers[0], tenants["users"][0], pdf_bytes, body=body))
    assert repeat["duplicate"] and repeat["revision_id"] == written["revision_id"]
    altered = {**body, "metadata": {**body["metadata"], "kind": "performance_record"}}
    assert (
        await upload(api, headers[0], tenants["users"][0], pdf_bytes, body=altered)
    ).status_code == 409
    competitors = await asyncio.gather(
        *(
            upload(
                api,
                headers[0],
                tenants["users"][0],
                pdf_bytes,
                root=written["attachment_id"],
                body=upload_body(tenants["users"][0], expected_state_version=1),
            )
            for _ in range(2)
        )
    )
    assert sorted(item.status_code for item in competitors) == [200, 409]
    assert (
        len(
            (
                await api.get(f"{BASE}/{written['attachment_id']}/revisions", headers=headers[0])
            ).json()["items"]
        )
        == 2
    )
    assert application.state.queue.calls == []


async def test_departed_reviewer_requires_versioned_reassignment_and_retains_approval(
    api, headers, tenants, admin_engine, pdf_bytes
):
    reviewer = uuid4()
    with Session(admin_engine) as session, session.begin():
        session.add(
            User(id=reviewer, email="attachment-reviewer@example.test", password_hash=PASSWORD_HASH)
        )
        session.flush()
        session.add(Membership(org_id=tenants["orgs"][0], user_id=reviewer, role="bidder"))
    login = await api.post(
        "/auth/login",
        json={
            "email": "attachment-reviewer@example.test",
            "password": PASSWORD,
            "org_id": str(tenants["orgs"][0]),
        },
    )
    reviewer_header = {**headers[0], "Authorization": "Bearer " + login.json()["data"]["session"]}
    written = result(await upload(api, headers[0], reviewer, pdf_bytes))
    assert (await review(api, headers[0], written)).status_code == 403
    approved = result(await review(api, reviewer_header, written))
    root = await show(api, headers[0], written["attachment_id"])
    next_revision = result(
        await upload(
            api,
            headers[0],
            reviewer,
            pdf_bytes,
            root=written["attachment_id"],
            body=upload_body(reviewer, expected_state_version=root["state_version"]),
        )
    )
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(
            select(Membership).where(
                Membership.org_id == tenants["orgs"][0], Membership.user_id == reviewer
            )
        )
        member.active = False
    root = await show(api, headers[0], written["attachment_id"])
    assert "reviewer_unavailable" in root["readiness"]["blockers"]
    assert root["readiness"]["next_action"] == "assign_reviewer"
    old = await revision(api, headers[0], written["revision_id"])
    assert old["review_state"] == "approved" and old["latest_review_id"] == approved["id"]
    assignment = {
        "request_id": str(uuid4()),
        "expected_state_version": root["state_version"],
        "custodian_user_id": str(tenants["users"][0]),
        "reviewer_user_id": str(tenants["users"][0]),
        "reason": "reviewer_unavailable",
    }
    assigned = result(
        await api.post(
            f"{BASE}/{written['attachment_id']}/assignment", headers=headers[0], json=assignment
        )
    )
    assert assigned["attachment"]["state_version"] == root["state_version"] + 1
    assert (
        await api.post(
            f"{BASE}/{written['attachment_id']}/assignment",
            headers=headers[0],
            json={**assignment, "request_id": str(uuid4())},
        )
    ).status_code == 409
    assert result(await review(api, headers[0], next_revision))["reviewed_by"] == str(
        tenants["users"][0]
    )


async def test_nonempty_exact_declaration_and_active_exact_profile_selection(
    api, headers, tenants, pdf_bytes
):
    chain = await archive_fixture(api, headers[0], tenants["users"][0], pdf_bytes)
    linked_body = {
        "request_id": str(uuid4()),
        "field": "registration_details",
        "attachment_revision_id": chain["written"]["revision_id"],
        "approval_id": chain["approved"]["id"],
    }
    empty = await api.post(
        f"/resources/profiles/revisions/{chain['profile']['id']}/attachments",
        headers=headers[0],
        json=linked_body,
    )
    assert empty.status_code == 409
    changed = result(
        await api.post(
            f"/resources/profiles/{chain['profile']['profile_id']}/revisions",
            headers=headers[0],
            json={
                "expected_revision": 1,
                "data": metadata(performance_summary="Synthetic changed declaration"),
            },
        )
    )
    new_profile = result(
        await api.post(
            f"/tasks/{chain['task']}/profiles",
            headers=headers[0],
            json={
                "profile_id": chain["profile"]["profile_id"],
                "revision": 2,
                "lot": "Synthetic lot",
            },
        )
    )
    assert changed["id"] == new_profile["profile_revision_id"]
    assert (await source(api, headers[0], chain)).status_code == 409
    mismatched = await api.post(
        f"/tasks/{chain['task']}/attachments",
        headers=headers[0],
        json={
            "request_id": str(uuid4()),
            "task_org_profile_id": new_profile["id"],
            "profile_attachment_link_id": chain["link"]["id"],
            "expected_selection_id": chain["selection"]["id"],
        },
    )
    assert mismatched.status_code == 404 and error_code(mismatched) == "not_found"
    old = (
        await api.get(
            f"/tasks/{chain['task']}/attachments", headers=headers[0], params={"history": True}
        )
    ).json()["items"]
    assert old[0]["attachment_revision_id"] == chain["written"]["revision_id"]


@pytest.mark.parametrize("scope", sorted(HUMAN_ATTACHMENT_SCOPES | {"evidence:confirm", "export"}))
async def test_token_scope_issuance_rejects_atomically(scope, api, headers):
    response = await token(api, headers[0], ["attachment:read", scope])
    assert response.status_code in {400, 403}, response.text
    assert response.json()["ok"] is False


def test_attachment_scope_matrix_and_unchanged_agent_defaults():
    assert HUMAN_ATTACHMENT_SCOPES <= HUMAN_ONLY_SCOPES
    assert HUMAN_ATTACHMENT_SCOPES.isdisjoint(SCOPES)
    assert "attachment:read" in SCOPES and "attachment:read" not in AGENT_SCOPES
    for role in ROLE_SCOPES:
        allowed = {"attachment:read", "attachment:page:read"}
        if role in {"admin", "bidder"}:
            allowed |= HUMAN_ATTACHMENT_SCOPES
        assert ROLE_SCOPES[role] & (HUMAN_ATTACHMENT_SCOPES | {"attachment:read"}) == allowed


@pytest.mark.parametrize("role", ["admin", "bidder", "technical", "viewer"])
async def test_human_original_and_safe_metadata_read_boundary(
    role, api, headers, tenants, admin_engine, pdf_bytes
):
    chain = await archive_fixture(api, headers[0], tenants["users"][0], pdf_bytes)
    archived = result(await source(api, headers[0], chain))["source"]
    link = result(
        await api.get(
            f"/attachment-sources/{archived['id']}/preview/download-link", headers=headers[0]
        )
    )
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(
            select(Membership).where(
                Membership.org_id == tenants["orgs"][0], Membership.user_id == tenants["users"][0]
            )
        )
        member.role = role
    for path in (
        BASE,
        f"{BASE}/{chain['written']['attachment_id']}",
        f"/attachment-sources/{archived['id']}",
    ):
        response = await api.get(path, headers=headers[0])
        assert response.status_code == 200
        assert (
            "confidential archive label" not in response.text
            and "synthetic-private-contract" not in response.text
        )
    expected = 200 if role in {"admin", "bidder"} else 403
    for path in (
        f"{BASE}/revisions/{chain['written']['revision_id']}",
        f"{BASE}/revisions/{chain['written']['revision_id']}/file/download-link",
        f"/attachment-sources/{archived['id']}/preview/download-link",
        link["url"],
    ):
        response = await api.get(path, headers=headers[0])
        assert response.status_code == expected, (path, response.text)


async def test_explicit_token_metadata_scope_cannot_read_labels_or_any_raw_route(
    api, headers, tenants, pdf_bytes
):
    chain = await archive_fixture(api, headers[0], tenants["users"][0], pdf_bytes)
    archived = result(await source(api, headers[0], chain))["source"]
    minted = result(
        await token(
            api,
            headers[0],
            [
                "attachment:read",
                "task:read",
                "evidence:source:read",
                "screenshot:read",
                "screenshot:write",
                "card:read",
                "draft:read",
                "job:read",
            ],
        )
    )
    automated = {**headers[0], "Authorization": "Bearer " + minted["token"]}
    for path in (
        BASE,
        f"{BASE}/{chain['written']['attachment_id']}",
        f"{BASE}/{chain['written']['attachment_id']}/revisions",
        f"/attachment-sources/{archived['id']}",
    ):
        response = await api.get(path, headers=automated)
        assert response.status_code == 200, response.text
        assert "confidential archive label" not in response.text
    for path in (
        f"{BASE}/revisions/{chain['written']['revision_id']}",
        f"{BASE}/revisions/{chain['written']['revision_id']}/file/download-link",
        f"{BASE}/revisions/{chain['written']['revision_id']}/pages/1/preview-link",
        f"/attachment-sources/{archived['id']}/preview/download-link",
        f"/evidence-sources/{archived['id']}/preview/download-link",
    ):
        response = await api.get(path, headers=automated)
        assert response.status_code in {403, 404}, (path, response.text)


async def test_two_org_route_inventory_foreign_absent_and_signature_boundaries(
    api, headers, tenants, pdf_bytes, application
):
    chain = await archive_fixture(api, headers[1], tenants["users"][1], pdf_bytes)
    archived = result(await source(api, headers[1], chain))["source"]
    root, fixed, task_id = (
        chain["written"]["attachment_id"],
        chain["written"]["revision_id"],
        chain["task"],
    )
    review_body = {
        "request_id": str(uuid4()),
        "expected_state_version": 2,
        "expected_review_id": chain["approved"]["id"],
        "file_id": chain["written"]["file_id"],
        "original_sha256": archived["original_sha256"],
        "metadata_sha256": "a" * 64,
        "decision": "revoke",
        "reason": "withdrawn_by_org",
    }
    deactivate = {
        "request_id": str(uuid4()),
        "expected_state_version": 1,
        "reason": "withdrawn_by_org",
    }
    routes = [
        ("GET", f"{BASE}/{root}", None),
        ("GET", f"{BASE}/{root}/revisions", None),
        ("GET", f"{BASE}/revisions/{fixed}", None),
        ("GET", f"{BASE}/revisions/{fixed}/reviews", None),
        ("POST", f"{BASE}/revisions/{fixed}/reviews", review_body),
        ("POST", f"{BASE}/{root}/deactivate", deactivate),
        ("GET", f"/resources/profiles/revisions/{chain['profile']['id']}/attachments", None),
        ("POST", f"/profile-attachment-links/{chain['link']['id']}/deactivate", deactivate),
        ("GET", f"/tasks/{task_id}/attachments", None),
        ("POST", f"/task-attachments/{chain['selection']['id']}/deactivate", deactivate),
        ("GET", f"/tasks/{task_id}/attachment-sources", None),
        ("GET", f"/attachment-sources/{archived['id']}", None),
        ("GET", f"/attachment-sources/{archived['id']}/preview/download-link", None),
        ("GET", f"{BASE}/revisions/{fixed}/file/download-link", None),
        ("GET", f"{BASE}/revisions/{fixed}/parts/1/download-link", None),
        ("GET", f"{BASE}/revisions/{fixed}/pages/1/preview-link", None),
    ]
    for method, path, body in routes:
        foreign = await api.request(method, path, headers=headers[0], json=body)
        assert foreign.status_code == 404, (path, foreign.text)
        absent_path = path
        for identifier in (
            root,
            fixed,
            task_id,
            chain["profile"]["id"],
            chain["link"]["id"],
            chain["selection"]["id"],
            archived["id"],
        ):
            absent_path = absent_path.replace(identifier, str(uuid4()))
        absent = await api.request(method, absent_path, headers=headers[0], json=body)
        assert absent.status_code == foreign.status_code and error_code(absent) == error_code(
            foreign
        )
    path = f"{BASE}/revisions/{fixed}/file/download"
    from urllib.parse import parse_qs, urlsplit

    signer = TokenSigner.for_tokens(application.state.processor.settings)
    issued = result(
        await api.get(f"{BASE}/revisions/{fixed}/file/download-link", headers=headers[1])
    )
    current = signer.open(parse_qs(urlsplit(issued["url"]).query)["signature"][0])
    for payload, ttl in (
        (current, -1),
        ({**current, "kind": "certificate-file-download"}, 300),
        ({**current, "org_id": headers[0]["X-Org-Id"]}, 300),
        ({**current, "id": str(uuid4())}, 300),
    ):
        assert (
            await api.get(
                path, headers=headers[1], params={"signature": signer.issue(payload, ttl)}
            )
        ).status_code == 404
    artifact(
        "two-org-route-inventory",
        {
            "scenario": "foreign-equals-absent",
            "expected_gate": "404",
            "routes": [{"method": method, "path": path} for method, path, _ in routes],
        },
    )


async def test_privacy_is_exact_page_rendition_hold_and_legacy_read_boundary(
    tenants, tmp_path, admin_engine, pdf_bytes, renderer
):
    async with phase_one_client(tenants, tmp_path) as (legacy_api, app, headers, _):
        api = V4API(legacy_api)
        task_id, _, extraction, _ = await create_tender(
            api, app, headers[0], tmp_path, suffix="attachment-privacy"
        )
        chain = await archive_fixture(
            api, headers[0], tenants["users"][0], pdf_bytes, task_id=task_id
        )
        first = result(await source(api, headers[0], chain, 1))["source"]
        second = result(await source(api, headers[0], chain, 2))["source"]
        body = {
            "mode": "clear",
            "request_id": str(uuid4()),
            "extraction_job_id": extraction,
            "reviewed_source_png_sha256": first["source_png_sha256"],
            "clearance": "safe_for_task_team",
            "expected_hold_id": None,
        }
        cleared = result(
            await api.post(
                f"/attachment-sources/{first['id']}/privacy", headers=headers[0], json=body
            )
        )
        assert cleared["reviewed_by"] == str(tenants["users"][0])
        assert cleared["rendition"]["profile"] == "screenshot-markup-v1"
        assert cleared["confirmed_by"] is None and not cleared["eligible_for_draft_export"]
        repeat = result(
            await api.post(
                f"/attachment-sources/{first['id']}/privacy", headers=headers[0], json=body
            )
        )
        assert repeat["duplicate"] and repeat["rendition"]["id"] == cleared["rendition"]["id"]
        wrong_page = await api.post(
            f"/attachment-sources/{second['id']}/privacy",
            headers=headers[0],
            json={**body, "request_id": str(uuid4())},
        )
        assert wrong_page.status_code == 409
        cleared_source = result(
            await api.get(f"/attachment-sources/{first['id']}", headers=headers[0])
        )
        readiness = cleared_source["readiness"]
        assert cleared_source["cleared_asset_id"] == cleared["rendition"]["asset_id"]
        assert cleared_source["cleared_rendition_id"] == cleared["rendition"]["id"]
        assert (
            "annotation_adapter_not_enabled" in readiness["blockers"]
            and not readiness["can_annotate"]
        )
        assert (
            "privacy_pending"
            in result(await api.get(f"/attachment-sources/{second['id']}", headers=headers[0]))[
                "readiness"
            ]["blockers"]
        )
        rendition = cleared["rendition"]["id"]
        preview = result(
            await api.post(f"/screenshot-renditions/{rendition}/preview-link", headers=headers[0])
        )
        team_png = await api.get(preview["url"], headers=headers[0])
        assert team_png.status_code == 200
        assert (
            hashlib.sha256(team_png.content).hexdigest() == cleared["rendition"]["image"]["sha256"]
        )
        minted = result(
            await token(
                api,
                headers[0],
                [
                    "attachment:read",
                    "task:read",
                    "evidence:source:read",
                    "screenshot:read",
                    "screenshot:write",
                    "card:read",
                    "draft:read",
                    "job:read",
                ],
            )
        )
        automated = {**headers[0], "Authorization": "Bearer " + minted["token"]}
        assert (await api.get(preview["url"], headers=automated)).status_code == 403
        for method, path, payload in (
            ("GET", f"/screenshots/{cleared['rendition']['asset_id']}", None),
            ("GET", f"/tasks/{task_id}/screenshots?job={extraction}", None),
            ("POST", f"/screenshot-renditions/{rendition}/preview-link", None),
            (
                "POST",
                f"/screenshots/{cleared['rendition']['asset_id']}/renditions",
                {
                    "parent_rendition_id": rendition,
                    "expected_image_sha256": cleared["rendition"]["image"]["sha256"],
                    "plan": {},
                    "dry_run": True,
                },
            ),
        ):
            denied = await api.request(method, path, headers=automated, json=payload)
            assert denied.status_code in {403, 404}, (path, denied.text)
        with Session(admin_engine) as session, session.begin():
            member = session.scalar(
                select(Membership).where(
                    Membership.org_id == tenants["orgs"][0],
                    Membership.user_id == tenants["users"][0],
                )
            )
            member.role = "viewer"
        assert (await api.get(preview["url"], headers=headers[0])).status_code == 200
        assert (
            await api.get(
                f"/attachment-sources/{first['id']}/preview/download-link", headers=headers[0]
            )
        ).status_code == 403
        with Session(admin_engine) as session, session.begin():
            member = session.scalar(
                select(Membership).where(
                    Membership.org_id == tenants["orgs"][0],
                    Membership.user_id == tenants["users"][0],
                )
            )
            member.role = "admin"
        held = result(
            await api.post(
                f"/attachment-sources/{first['id']}/privacy",
                headers=headers[0],
                json={
                    "mode": "needs_redaction",
                    "request_id": str(uuid4()),
                    "reviewed_source_png_sha256": first["source_png_sha256"],
                    "expected_hold_id": None,
                },
            )
        )
        assert held["reason"] == "sensitive_content" and held["next_action"] == "redact_page"
        assert (
            await api.post(
                f"/attachment-sources/{first['id']}/privacy",
                headers=headers[0],
                json={**body, "request_id": str(uuid4())},
            )
        ).status_code == 409
        assert (await api.get(preview["url"], headers=headers[0])).status_code in {403, 409}
        held_source = result(
            await api.get(f"/attachment-sources/{first['id']}", headers=headers[0])
        )
        assert (
            held_source["cleared_asset_id"] is None and held_source["cleared_rendition_id"] is None
        )
        assert (
            "needs_redaction"
            in result(await api.get(f"/attachment-sources/{first['id']}", headers=headers[0]))[
                "readiness"
            ]["blockers"]
        )
        artifact(
            "privacy-per-page",
            {
                "scenario": "clear-page-one-hold-withdraws-only-page-one",
                "expected_gate": "needs_redaction",
                "page_one_hash": first["source_png_sha256"],
                "page_two_hash": second["source_png_sha256"],
                "rendition_sha256": hashlib.sha256(team_png.content).hexdigest(),
                "privacy_receipt": cleared,
                "hold": held,
            },
        )


@pytest.mark.parametrize("mode", ["multipart", "image", "rotation"])
async def test_disabled_upload_modes_fail_without_write(
    mode, api, headers, tenants, pdf_bytes, application
):
    body = upload_body(tenants["users"][0])
    files = [("files", ("first.pdf", pdf_bytes, "application/pdf"))]
    if mode == "multipart":
        files.append(("files", ("second.pdf", pdf_bytes, "application/pdf")))
    elif mode == "image":
        files = [("files", ("first.png", b"\x89PNG\r\n\x1a\n", "image/png"))]
    else:
        body["parts"] = [{"rotation": 90}]
    response = await api.post(
        BASE, headers=headers[0], data={"metadata": json.dumps(body)}, files=files
    )
    assert response.status_code in {400, 409, 422}
    assert error_code(response) == "attachment_upload_mode_not_enabled"
    assert (await api.get(BASE, headers=headers[0])).json()["items"] == []
    assert application.state.queue.calls == []


async def test_review_revocation_and_reapproval_never_revives_exact_old_pin(
    api, headers, tenants, pdf_bytes
):
    chain = await archive_fixture(api, headers[0], tenants["users"][0], pdf_bytes)
    archived = result(await source(api, headers[0], chain))["source"]
    result(
        await review(
            api, headers[0], chain["written"], decision="revoke", reason="withdrawn_by_org"
        )
    )
    revoked = result(await api.get(f"/attachment-sources/{archived['id']}", headers=headers[0]))
    assert "archive_review_revoked" in revoked["readiness"]["blockers"]
    next_approval = result(await review(api, headers[0], chain["written"]))
    assert next_approval["id"] != chain["approved"]["id"]
    still_old = result(await api.get(f"/attachment-sources/{archived['id']}", headers=headers[0]))
    assert "archive_review_revoked" in still_old["readiness"]["blockers"]
    assert (await source(api, headers[0], chain)).status_code == 409
    result(
        await api.post(
            f"/profile-attachment-links/{chain['link']['id']}/deactivate",
            headers=headers[0],
            json={
                "request_id": str(uuid4()),
                "expected_state_version": chain["link"]["state_version"],
                "reason": "superseded",
            },
        )
    )
    linked = result(
        await api.post(
            f"/resources/profiles/revisions/{chain['profile']['id']}/attachments",
            headers=headers[0],
            json={
                "request_id": str(uuid4()),
                "field": "performance_summary",
                "attachment_revision_id": chain["written"]["revision_id"],
                "approval_id": next_approval["id"],
            },
        )
    )
    chosen = result(
        await api.post(
            f"/tasks/{chain['task']}/attachments",
            headers=headers[0],
            json={
                "request_id": str(uuid4()),
                "task_org_profile_id": chain["task_profile"]["id"],
                "profile_attachment_link_id": linked["id"],
                "expected_selection_id": chain["selection"]["id"],
            },
        )
    )
    assert chosen["id"] != chain["selection"]["id"] and chosen["approval_id"] == next_approval["id"]
    fresh = result(await source(api, headers[0], {**chain, "selection": chosen}))["source"]
    assert fresh["id"] != archived["id"] and "privacy_pending" in fresh["readiness"]["blockers"]
    artifact(
        "revocation-reapproval",
        {
            "scenario": "reapproval-requires-new-link-pin-page-privacy",
            "expected_gate": "old-pin-never-revived",
            "old_source": archived["id"],
            "new_source": fresh["id"],
            "old_approval": chain["approved"]["id"],
            "new_approval": next_approval["id"],
        },
    )


@pytest.mark.parametrize("target", ["archive", "link", "selection"])
async def test_deactivation_is_one_way_history_retained_and_replay_bound(
    target, api, headers, tenants, pdf_bytes
):
    chain = await archive_fixture(api, headers[0], tenants["users"][0], pdf_bytes)
    archived = result(await source(api, headers[0], chain))["source"]
    root = await show(api, headers[0], chain["written"]["attachment_id"])
    paths = {
        "archive": (f"{BASE}/{root['id']}/deactivate", root["state_version"]),
        "link": (
            f"/profile-attachment-links/{chain['link']['id']}/deactivate",
            chain["link"]["state_version"],
        ),
        "selection": (
            f"/task-attachments/{chain['selection']['id']}/deactivate",
            chain["selection"]["state_version"],
        ),
    }
    path, version = paths[target]
    body = {
        "request_id": str(uuid4()),
        "expected_state_version": version,
        "reason": "withdrawn_by_org",
    }
    result(await api.post(path, headers=headers[0], json=body))
    result(await api.post(path, headers=headers[0], json=body))
    assert (
        await api.post(
            path,
            headers=headers[0],
            json={**body, "request_id": str(uuid4()), "expected_state_version": version + 1},
        )
    ).status_code == 409
    assert (await source(api, headers[0], chain)).status_code == 409
    historical = await revision(api, headers[0], chain["written"]["revision_id"])
    assert historical["original_sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    history = await api.get(
        f"/tasks/{chain['task']}/attachment-sources", headers=headers[0], params={"history": True}
    )
    assert any(row["id"] == archived["id"] for row in history.json()["items"])


@pytest.mark.parametrize(
    "bad",
    ["malformed_pdf", "encrypted_pdf", "too_many_pages", "label_control", "metadata_oversized"],
)
async def test_invalid_input_bounds_leave_no_archive_and_never_echo_sensitive_input(
    bad, api, headers, tenants, pdf_bytes, application, monkeypatch
):
    body = upload_body(tenants["users"][0])
    content = pdf_bytes
    secret = "SYNTHETIC-MUST-NOT-ECHO"
    if bad == "malformed_pdf":
        content = b"%PDF-1.7\n" + secret.encode()
    elif bad == "encrypted_pdf":
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
            content = document.tobytes(
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="synthetic-owner",
                user_pw="synthetic-user",
            )
    elif bad == "too_many_pages":
        with pymupdf.open() as document:
            for _ in range(201):
                document.new_page(width=10, height=10)
            content = document.tobytes()
    elif bad == "label_control":
        body["metadata"]["label"] = secret + "\n" + secret
    else:
        body["metadata"]["label"] = secret * 7000

    async def no_put(*args, **kwargs):
        raise AssertionError("invalid upload wrote ciphertext")

    monkeypatch.setattr(application.state.storage, "put", no_put)
    response = await upload(api, headers[0], tenants["users"][0], content, body=body)
    assert response.status_code in {400, 413, 422}, response.text
    assert secret not in response.text and "synthetic-private-contract" not in response.text
    assert (await api.get(BASE, headers=headers[0])).json()["items"] == []


async def test_cursor_binds_actor_org_limit_filters_and_current_live_role(
    api, headers, tenants, pdf_bytes, admin_engine
):
    for _ in range(3):
        result(await upload(api, headers[0], tenants["users"][0], pdf_bytes))
    first = await api.get(BASE, headers=headers[0], params={"limit": 1})
    cursor = first.json()["data"]["next_cursor"]
    assert cursor and len(first.json()["items"]) == 1
    for params, header in (
        ({"limit": 1, "cursor": cursor, "kind": "contract"}, headers[0]),
        ({"limit": 2, "cursor": cursor}, headers[0]),
        ({"limit": 1, "cursor": cursor}, headers[1]),
        ({"limit": 1, "cursor": "forged"}, headers[0]),
    ):
        denied = await api.get(BASE, headers=header, params=params)
        assert denied.status_code in {404, 409}, denied.text
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(
            select(Membership).where(
                Membership.org_id == tenants["orgs"][0], Membership.user_id == tenants["users"][0]
            )
        )
        member.role = "bidder"
    assert (
        await api.get(BASE, headers=headers[0], params={"limit": 1, "cursor": cursor})
    ).status_code == 409


async def test_selection_changed_while_rendering_cannot_publish_source(
    api, headers, tenants, pdf_bytes, application, monkeypatch
):
    from app.services import evidence_sources

    chain = await archive_fixture(api, headers[0], tenants["users"][0], pdf_bytes)
    rendered, resume = asyncio.Event(), asyncio.Event()
    original = evidence_sources.bounded_render_async

    async def paused(*args, **kwargs):
        actual = await original(*args, **kwargs)
        rendered.set()
        await resume.wait()
        return actual

    monkeypatch.setattr(evidence_sources, "bounded_render_async", paused)
    preparing = asyncio.create_task(source(api, headers[0], chain))
    try:
        await asyncio.wait_for(rendered.wait(), timeout=30)
        result(
            await api.post(
                f"/task-attachments/{chain['selection']['id']}/deactivate",
                headers=headers[0],
                json={
                    "request_id": str(uuid4()),
                    "expected_state_version": chain["selection"]["state_version"],
                    "reason": "selection_replaced",
                },
            )
        )
    finally:
        resume.set()
    rejected = await asyncio.wait_for(preparing, timeout=30)
    assert rejected.status_code == 409, rejected.text
    from sqlalchemy import text

    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM evidence_sources WHERE source_kind='user_supplied_attachment_pdf'"
                )
            )
            == 0
        )
        assert (
            await session.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action='attachment.source.create'")
            )
            == 0
        )
    artifact(
        "selection-render-race",
        {
            "scenario": "selection-deactivated-after-pixels-before-publication",
            "expected_gate": "no-source-or-success-audit",
            "result_code": error_code(rejected),
        },
    )


async def test_signed_original_issued_before_member_departure_has_no_bearer_authority(
    api, headers, tenants, pdf_bytes, admin_engine
):
    chain = await archive_fixture(api, headers[0], tenants["users"][0], pdf_bytes)
    linked = result(
        await api.get(
            f"{BASE}/revisions/{chain['written']['revision_id']}/file/download-link",
            headers=headers[0],
        )
    )
    assert (await api.get(linked["url"], headers=headers[0])).status_code == 200
    assert (
        await api.get(linked["url"], headers={"X-Org-Id": headers[0]["X-Org-Id"]})
    ).status_code == 401
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(
            select(Membership).where(
                Membership.org_id == tenants["orgs"][0], Membership.user_id == tenants["users"][0]
            )
        )
        member.active = False
    denied = await api.get(linked["url"], headers=headers[0])
    assert denied.status_code == 404
    assert "confidential archive label" not in denied.text
