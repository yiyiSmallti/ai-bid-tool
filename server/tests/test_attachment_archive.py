"""Attachment first-slice acceptance specified before runtime implementation.

Failure inventory: implicit file inheritance or latest-revision retargeting; invalid
review transitions, forged file/hash/prior decision or stale state;
blank/foreign/mismatched declaration links; wrong task profile;
raw bytes or labels reaching tokens; clearance leaking across
pages or extraction runs; privacy hold reusing old clearance; forged/expired links;
archive/link/selection revival; competing writes publishing partial state; storage
failure reporting success; committed immutable parents accepting later files; runtime grants,
composite FK or CHECK rejection being replaced by parent-dependent trigger errors.

All workflows use synthetic files and PostgreSQL-backed HTTP/services. No external
providers are called. Artifacts contain scenario gates and hashes, never credentials,
signed URLs, labels from real documents or uploaded multipart payloads.
"""

import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pymupdf
import pytest
from app.core.security import TokenSigner
from app.models.entities import AuditLog, UsageRecord
from app.services.auth import AGENT_SCOPES, HUMAN_ONLY_SCOPES, HUMAN_SCOPES, SCOPES
from sqlalchemy import func, select, text
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
    assert envelope["ok"] is True and all(value == 0 for value in envelope["cost"].values())
    return envelope["data"]


def error_code(response):
    data = response.json()["data"]
    return data.get("code") or data.get("error", {}).get("code")


def upload_body(**changes):
    return {
        "request_id": str(uuid4()),
        "metadata": {"kind": "contract", "label": "Synthetic confidential archive label"},
        **changes,
    }


async def upload(api, header, pdf, *, body=None, root=None):
    return await api.post(
        BASE if root is None else f"{BASE}/{root}/revisions",
        headers=header,
        data={"metadata": json.dumps(body or upload_body())},
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


async def archive_fixture(api, header, pdf, *, task_id=None):
    written = result(await upload(api, header, pdf))
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
    api, headers, database, pdf_bytes, application
):
    from app.schemas.attachment_contracts import AttachmentRevisionView, AttachmentSourceArchive

    chain = await archive_fixture(api, headers, pdf_bytes)
    written = chain["written"]
    fixed = await revision(api, headers, written["revision_id"])
    AttachmentRevisionView.model_validate(fixed)
    assert fixed["revised_at"] == fixed["created_at"]
    assert "revised_by" not in fixed
    assert fixed["original_sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    assert fixed["parts"] == [] and fixed["original"]["page_count"] == 2
    root = await show(api, headers, written["attachment_id"])
    assert "custodian_user_id" not in root and "reviewer_user_id" not in root
    assert root["revised_at"] == fixed["created_at"]
    assert "revised_by" not in root
    assert "reviewed_by" not in chain["approved"]
    original = result(
        await api.get(f"{BASE}/revisions/{fixed['id']}/file/download-link", headers=headers)
    )
    downloaded = await api.get(original["url"], headers=headers)
    assert downloaded.content == pdf_bytes
    assert downloaded.headers["cache-control"] == "no-store"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in downloaded.headers["content-disposition"]
    signer = TokenSigner.for_tokens(application.state.processor.settings)
    current = signer.open(parse_qs(urlsplit(original["url"]).query)["signature"][0])
    for payload, ttl in (
        (current, -1),
        ({**current, "kind": "certificate-file-download"}, 300),
        ({**current, "id": str(uuid4())}, 300),
    ):
        assert (
            await api.get(
                f"{BASE}/revisions/{fixed['id']}/file/download",
                headers=headers,
                params={"signature": signer.issue(payload, ttl)},
            )
        ).status_code == 404
    request_id = str(uuid4())
    archived = result(await source(api, headers, chain, request_id=request_id))
    actual = AttachmentSourceArchive.model_validate(archived["source"])
    assert actual.dpi == 150 and actual.render_profile == "pdf-page-preview-v1"
    assert actual.confirmed_at is None and actual.eligible_for_draft_export is False
    assert {"privacy_pending", "extraction_required"} <= set(actual.readiness.blockers)
    raw_link = result(
        await api.get(f"/attachment-sources/{actual.id}/preview/download-link", headers=headers)
    )
    raw = await api.get(raw_link["url"], headers=headers)
    pixels = pymupdf.Pixmap(raw.content)
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        expected = document[0].get_pixmap(dpi=150, colorspace=pymupdf.csRGB, alpha=False)
        assert (pixels.width, pixels.height, pixels.samples) == (
            expected.width,
            expected.height,
            expected.samples,
        )
    assert hashlib.sha256(raw.content).hexdigest() == actual.source_png_sha256
    repeat = result(await source(api, headers, chain, request_id=request_id))
    assert repeat["duplicate"] and repeat["source"]["id"] == str(actual.id)
    updated = result(
        await upload(
            api,
            headers,
            pdf_bytes,
            root=written["attachment_id"],
            body=upload_body(expected_state_version=root["state_version"]),
        )
    )
    assert updated["revision"] == 2
    chosen = result(await api.get(f"/tasks/{chain['task']}/attachments", headers=headers))
    assert chosen is not None
    assert (await api.get(f"/tasks/{chain['task']}/attachments", headers=headers)).json()["items"][
        0
    ]["attachment_revision_id"] == written["revision_id"]
    pinned = (await api.get(f"/tasks/{chain['task']}/attachments", headers=headers)).json()[
        "items"
    ][0]
    assert pinned["library_updated"] is True and pinned["current_library_revision"] == 2
    async with application.state.db.transaction() as session:
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
            encrypted = application.state.storage.path(key).read_bytes()
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
            "result": archived,
        },
    )


async def test_review_transitions_replay_hashes_and_stale_state(api, headers, database, pdf_bytes):
    written = result(await upload(api, headers, pdf_bytes))
    rejected = result(await review(api, headers, written, decision="reject"))
    assert rejected["decision"] == "reject"
    assert (await review(api, headers, written, decision="reject")).status_code == 409
    approved = result(await review(api, headers, written))
    assert approved["prior_review_id"] == rejected["id"]
    assert (await review(api, headers, written, decision="reject")).status_code == 409
    revoked = result(await review(api, headers, written, decision="revoke", reason="superseded"))
    assert revoked["prior_review_id"] == approved["id"]
    again = result(await review(api, headers, written))
    assert again["id"] != approved["id"] and again["prior_review_id"] == revoked["id"]
    root = await show(api, headers, written["attachment_id"])
    fixed = await revision(api, headers, written["revision_id"])
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
            "reason": "superseded",
            **patch,
        }
        denied = await api.post(
            f"{BASE}/revisions/{fixed['id']}/reviews", headers=headers, json=body
        )
        assert denied.status_code in {404, 409}, denied.text
    history = (
        await api.get(
            f"{BASE}/revisions/{fixed['id']}/reviews", headers=headers, params={"limit": 2}
        )
    ).json()
    assert len(history["items"]) == 2 and history["data"]["next_cursor"]
    cursor = history["data"]["next_cursor"]
    following = (
        await api.get(
            f"{BASE}/revisions/{fixed['id']}/reviews",
            headers=headers,
            params={"limit": 2, "cursor": cursor},
        )
    ).json()
    assert not {item["id"] for item in history["items"]} & {
        item["id"] for item in following["items"]
    }


async def test_upload_replay_dry_run_and_concurrent_revisions(
    api, headers, database, pdf_bytes, application
):
    body = upload_body()
    dry = result(await upload(api, headers, pdf_bytes, body={**body, "dry_run": True}))
    assert dry["dry_run"] and dry["enabled_upload_mode"] == "single_pdf"
    assert (await api.get(BASE, headers=headers)).json()["items"] == []
    written = result(await upload(api, headers, pdf_bytes, body=body))
    repeat = result(await upload(api, headers, pdf_bytes, body=body))
    assert repeat["duplicate"] and repeat["revision_id"] == written["revision_id"]
    altered = {**body, "metadata": {**body["metadata"], "kind": "performance_record"}}
    assert (await upload(api, headers, pdf_bytes, body=altered)).status_code == 409
    competitors = await asyncio.gather(
        *(
            upload(
                api,
                headers,
                pdf_bytes,
                root=written["attachment_id"],
                body=upload_body(expected_state_version=1),
            )
            for _ in range(2)
        )
    )
    assert sorted(item.status_code for item in competitors) == [200, 409]
    assert (
        len(
            (await api.get(f"{BASE}/{written['attachment_id']}/revisions", headers=headers)).json()[
                "items"
            ]
        )
        == 2
    )
    assert application.state.queue.calls == []


async def test_nonempty_exact_declaration_and_active_exact_profile_selection(
    api, headers, database, pdf_bytes
):
    chain = await archive_fixture(api, headers, pdf_bytes)
    linked_body = {
        "request_id": str(uuid4()),
        "field": "registration_details",
        "attachment_revision_id": chain["written"]["revision_id"],
        "approval_id": chain["approved"]["id"],
    }
    empty = await api.post(
        f"/resources/profiles/revisions/{chain['profile']['id']}/attachments",
        headers=headers,
        json=linked_body,
    )
    assert empty.status_code == 409
    changed = result(
        await api.post(
            f"/resources/profiles/{chain['profile']['profile_id']}/revisions",
            headers=headers,
            json={
                "expected_revision": 1,
                "data": metadata(performance_summary="Synthetic changed declaration"),
            },
        )
    )
    new_profile = result(
        await api.post(
            f"/tasks/{chain['task']}/profiles",
            headers=headers,
            json={
                "profile_id": chain["profile"]["profile_id"],
                "revision": 2,
                "lot": "Synthetic lot",
            },
        )
    )
    assert changed["id"] == new_profile["profile_revision_id"]
    assert (await source(api, headers, chain)).status_code == 409
    mismatched = await api.post(
        f"/tasks/{chain['task']}/attachments",
        headers=headers,
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
            f"/tasks/{chain['task']}/attachments", headers=headers, params={"history": True}
        )
    ).json()["items"]
    assert old[0]["attachment_revision_id"] == chain["written"]["revision_id"]


@pytest.mark.parametrize("scope", sorted(HUMAN_ATTACHMENT_SCOPES | {"evidence:confirm", "export"}))
async def test_token_scope_issuance_rejects_atomically(scope, api, headers):
    response = await token(api, headers, ["attachment:read", scope])
    assert response.status_code in {400, 403}, response.text
    assert response.json()["ok"] is False


def test_attachment_scope_matrix_and_unchanged_agent_defaults():
    assert HUMAN_ATTACHMENT_SCOPES <= HUMAN_ONLY_SCOPES
    assert HUMAN_ATTACHMENT_SCOPES.isdisjoint(SCOPES)
    assert "attachment:read" in SCOPES and "attachment:read" not in AGENT_SCOPES
    assert HUMAN_ATTACHMENT_SCOPES <= HUMAN_SCOPES


async def test_explicit_token_metadata_scope_cannot_read_labels_or_any_raw_route(
    api, headers, database, pdf_bytes
):
    chain = await archive_fixture(api, headers, pdf_bytes)
    archived = result(await source(api, headers, chain))["source"]
    minted = result(
        await token(
            api,
            headers,
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
    automated = {**headers, "Authorization": "Bearer " + minted["token"]}
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


async def test_privacy_is_exact_page_rendition_hold_and_legacy_read_boundary(
    database, tmp_path, admin_engine, pdf_bytes, renderer
):
    async with phase_one_client(database, tmp_path) as (legacy_api, app, headers, _):
        api = V4API(legacy_api)
        task_id, _, extraction, _ = await create_tender(
            api, app, headers, tmp_path, suffix="attachment-privacy"
        )
        chain = await archive_fixture(api, headers, pdf_bytes, task_id=task_id)
        first = result(await source(api, headers, chain, 1))["source"]
        second = result(await source(api, headers, chain, 2))["source"]
        body = {
            "mode": "clear",
            "request_id": str(uuid4()),
            "extraction_job_id": extraction,
            "reviewed_source_png_sha256": first["source_png_sha256"],
            "clearance": "safe_for_task_team",
            "expected_hold_id": None,
        }
        cleared = result(
            await api.post(f"/attachment-sources/{first['id']}/privacy", headers=headers, json=body)
        )
        assert "reviewed_by" not in cleared
        assert cleared["rendition"]["profile"] == "screenshot-markup-v1"
        assert cleared["confirmed_at"] is None and not cleared["eligible_for_draft_export"]
        repeat = result(
            await api.post(f"/attachment-sources/{first['id']}/privacy", headers=headers, json=body)
        )
        assert repeat["duplicate"] and repeat["rendition"]["id"] == cleared["rendition"]["id"]
        wrong_page = await api.post(
            f"/attachment-sources/{second['id']}/privacy",
            headers=headers,
            json={**body, "request_id": str(uuid4())},
        )
        assert wrong_page.status_code == 409
        cleared_source = result(
            await api.get(f"/attachment-sources/{first['id']}", headers=headers)
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
            in result(await api.get(f"/attachment-sources/{second['id']}", headers=headers))[
                "readiness"
            ]["blockers"]
        )
        rendition = cleared["rendition"]["id"]
        preview = result(
            await api.post(f"/screenshot-renditions/{rendition}/preview-link", headers=headers)
        )
        reviewed_png = await api.get(preview["url"], headers=headers)
        assert reviewed_png.status_code == 200
        assert (
            hashlib.sha256(reviewed_png.content).hexdigest()
            == cleared["rendition"]["image"]["sha256"]
        )
        minted = result(
            await token(
                api,
                headers,
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
        automated = {**headers, "Authorization": "Bearer " + minted["token"]}
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
        assert (await api.get(preview["url"], headers=headers)).status_code == 200
        held = result(
            await api.post(
                f"/attachment-sources/{first['id']}/privacy",
                headers=headers,
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
                headers=headers,
                json={**body, "request_id": str(uuid4())},
            )
        ).status_code == 409
        assert (await api.get(preview["url"], headers=headers)).status_code in {403, 409}
        held_source = result(await api.get(f"/attachment-sources/{first['id']}", headers=headers))
        assert (
            held_source["cleared_asset_id"] is None and held_source["cleared_rendition_id"] is None
        )
        assert (
            "needs_redaction"
            in result(await api.get(f"/attachment-sources/{first['id']}", headers=headers))[
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
                "rendition_sha256": hashlib.sha256(reviewed_png.content).hexdigest(),
                "privacy_receipt": cleared,
                "hold": held,
            },
        )


@pytest.mark.parametrize("mode", ["multipart", "image", "rotation"])
async def test_disabled_upload_modes_fail_without_write(
    mode, api, headers, database, pdf_bytes, application
):
    body = upload_body()
    files = [("files", ("first.pdf", pdf_bytes, "application/pdf"))]
    if mode == "multipart":
        files.append(("files", ("second.pdf", pdf_bytes, "application/pdf")))
    elif mode == "image":
        files = [("files", ("first.png", b"\x89PNG\r\n\x1a\n", "image/png"))]
    else:
        body["parts"] = [{"rotation": 90}]
    response = await api.post(
        BASE, headers=headers, data={"metadata": json.dumps(body)}, files=files
    )
    assert response.status_code in {400, 409, 422}
    assert error_code(response) == "attachment_upload_mode_not_enabled"
    assert (await api.get(BASE, headers=headers)).json()["items"] == []
    assert application.state.queue.calls == []


async def test_review_revocation_and_reapproval_never_revives_exact_old_pin(
    api, headers, database, pdf_bytes
):
    chain = await archive_fixture(api, headers, pdf_bytes)
    archived = result(await source(api, headers, chain))["source"]
    result(await review(api, headers, chain["written"], decision="revoke", reason="superseded"))
    revoked = result(await api.get(f"/attachment-sources/{archived['id']}", headers=headers))
    assert "archive_review_revoked" in revoked["readiness"]["blockers"]
    next_approval = result(await review(api, headers, chain["written"]))
    assert next_approval["id"] != chain["approved"]["id"]
    still_old = result(await api.get(f"/attachment-sources/{archived['id']}", headers=headers))
    assert "archive_review_revoked" in still_old["readiness"]["blockers"]
    assert (await source(api, headers, chain)).status_code == 409
    result(
        await api.post(
            f"/profile-attachment-links/{chain['link']['id']}/deactivate",
            headers=headers,
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
            headers=headers,
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
            headers=headers,
            json={
                "request_id": str(uuid4()),
                "task_org_profile_id": chain["task_profile"]["id"],
                "profile_attachment_link_id": linked["id"],
                "expected_selection_id": chain["selection"]["id"],
            },
        )
    )
    assert chosen["id"] != chain["selection"]["id"] and chosen["approval_id"] == next_approval["id"]
    fresh = result(await source(api, headers, {**chain, "selection": chosen}))["source"]
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
    target, api, headers, database, pdf_bytes
):
    chain = await archive_fixture(api, headers, pdf_bytes)
    archived = result(await source(api, headers, chain))["source"]
    root = await show(api, headers, chain["written"]["attachment_id"])
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
        "reason": "superseded",
    }
    result(await api.post(path, headers=headers, json=body))
    result(await api.post(path, headers=headers, json=body))
    assert (
        await api.post(
            path,
            headers=headers,
            json={**body, "request_id": str(uuid4()), "expected_state_version": version + 1},
        )
    ).status_code == 409
    assert (await source(api, headers, chain)).status_code == 409
    historical = await revision(api, headers, chain["written"]["revision_id"])
    assert historical["original_sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    history = await api.get(
        f"/tasks/{chain['task']}/attachment-sources", headers=headers, params={"history": True}
    )
    assert any(row["id"] == archived["id"] for row in history.json()["items"])


@pytest.mark.parametrize(
    "bad",
    ["malformed_pdf", "encrypted_pdf", "too_many_pages", "label_control", "metadata_oversized"],
)
async def test_invalid_input_bounds_leave_no_archive_and_never_echo_sensitive_input(
    bad, api, headers, database, pdf_bytes, application, monkeypatch
):
    body = upload_body()
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
    response = await upload(api, headers, content, body=body)
    assert response.status_code in {400, 413, 422}, response.text
    assert secret not in response.text and "synthetic-private-contract" not in response.text
    assert (await api.get(BASE, headers=headers)).json()["items"] == []


async def test_cursor_binds_actor_limit_and_filters(
    api, headers, database, pdf_bytes, admin_engine
):
    for _ in range(3):
        result(await upload(api, headers, pdf_bytes))
    first = await api.get(BASE, headers=headers, params={"limit": 1})
    cursor = first.json()["data"]["next_cursor"]
    assert cursor and len(first.json()["items"]) == 1
    issued = result(await token(api, headers, ["attachment:read"]))
    automated = {"Authorization": "Bearer " + issued["token"]}
    for params, header in (
        ({"limit": 1, "cursor": cursor, "kind": "contract"}, headers),
        ({"limit": 2, "cursor": cursor}, headers),
        ({"limit": 1, "cursor": cursor}, automated),
        ({"limit": 1, "cursor": "forged"}, headers),
    ):
        denied = await api.get(BASE, headers=header, params=params)
        assert denied.status_code in {404, 409}, denied.text


async def test_selection_changed_while_rendering_cannot_publish_source(
    api, headers, database, pdf_bytes, application, monkeypatch
):
    from app.services import evidence_sources

    chain = await archive_fixture(api, headers, pdf_bytes)
    rendered, resume = asyncio.Event(), asyncio.Event()
    original = evidence_sources.bounded_render_async

    async def paused(*args, **kwargs):
        actual = await original(*args, **kwargs)
        rendered.set()
        await resume.wait()
        return actual

    monkeypatch.setattr(evidence_sources, "bounded_render_async", paused)
    preparing = asyncio.create_task(source(api, headers, chain))
    try:
        await asyncio.wait_for(rendered.wait(), timeout=30)
        result(
            await api.post(
                f"/task-attachments/{chain['selection']['id']}/deactivate",
                headers=headers,
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

    async with application.state.db.transaction() as session:
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
