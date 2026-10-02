"""API/processor export acceptance gates, with synthetic, repeatable artifacts.

Failure modes enumerated before implementation:
- A token, agent, worker, admin, technical or viewer must never prepare/release/download.
- Cross-org and unknown IDs must be indistinguishable on every export route/job.
- Gaps block final_section; review_copy exposes only source/gap metadata and is partial.
- Invalid citations, stale cards/selections and unconfirmed evidence block both modes.
- Missing/duplicate acknowledgments and input/candidate hash conflicts cannot publish.
- Worker completion produces only a candidate; cancellation/old attempts cannot publish.
- DOCX bytes must reproduce, stay encrypted, retain editable text and exact page PNGs.
- Changed selections/reviews or membership revoke already issued download signatures.
- Dry run and export make no provider calls or usage rows; duplicate release audits once.
"""

import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from app.models.entities import AuditLog, UsageRecord
from docx import Document
from sqlalchemy import func, select
from test_response_cards import (
    CERTIFICATE_PAGE,
    create_card,
    create_tender,
    phase_one_client,
    require_action,
    select_real_materials,
    set_role,
)

SECTIONS = ("substantive", "commercial", "technical", "comply_only", "gaps", "evidence_appendix")
COLUMNS = (
    "ordinal",
    "tender_clause",
    "source_location",
    "response",
    "deviation",
    "deviation_note",
    "evidence",
)


def export_template():
    doc = Document()
    # Register only style IDs actually present in the package.
    doc.add_table(rows=1, cols=1).style = "Table Grid"
    doc.tables[0]._element.getparent().remove(doc.tables[0]._element)
    doc.add_heading("", level=1)
    for section in SECTIONS:
        doc.add_paragraph("{{bid." + section + "}}")
    output = BytesIO()
    doc.save(output)
    return output.getvalue()


async def setup_template(api, header, task):
    uploaded = await api.post(
        "/resources/templates",
        headers=header,
        data={
            "metadata": json.dumps({"data": {"name": "Synthetic export layout", "chapters": []}})
        },
        files={
            "file": (
                "synthetic-export.docx",
                export_template(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    template = uploaded.json()["data"]
    selection = await api.post(
        f"/tasks/{task}/templates", headers=header, json={"template_id": template["template_id"]}
    )
    assert selection.status_code == 200, selection.text
    selected = selection.json()["data"]
    body = {
        "template_revision_id": template["id"],
        "expected_template_sha256": template["file"]["sha256"],
        "sections": [
            {
                "section": section,
                "heading_style_id": "Heading1",
                "table_style_id": "TableGrid",
                "columns": [
                    {"key": key, "width_percent": width}
                    for key, width in zip(COLUMNS, [5, 25, 10, 25, 10, 15, 10], strict=True)
                ]
                if index < 3
                else [],
            }
            for index, section in enumerate(SECTIONS)
        ],
        "dry_run": True,
    }
    preview = await api.post("/export-template-bindings", headers=header, json=body)
    assert preview.status_code == 200, preview.text
    body.update(
        dry_run=False, expected_static_content_hash=preview.json()["data"]["static_content_hash"]
    )
    binding = await api.post("/export-template-bindings", headers=header, json=body)
    assert binding.status_code == 200, binding.text
    return selected, binding.json()["data"]


async def draft(api, app, header, task, extraction):
    submitted = await api.post(
        f"/tasks/{task}/drafts", headers=header, json={"extraction_job_id": extraction}
    )
    assert submitted.status_code == 200, submitted.text
    job = submitted.json()["data"]["job_id"]
    await app.state.processor(header["X-Org-Id"], job)
    result = await api.get(f"/jobs/{job}", headers=header)
    assert result.json()["data"]["status"] == "succeeded", result.text
    return result.json()["data"]["result"]["draft_id"]


async def prepared(api, app, header, task, body):
    preview = await api.post(
        f"/tasks/{task}/export-runs", headers=header, json={**body, "dry_run": True}
    )
    assert preview.status_code == 200, preview.text
    data = preview.json()["data"]
    submitted = await api.post(
        f"/tasks/{task}/export-runs",
        headers=header,
        json={
            **body,
            "expected_input_hash": data["input_hash"],
            "acknowledged_issue_ids": [
                i["issue_id"] for i in data["issues"] if i["severity"] == "acknowledge"
            ],
        },
    )
    assert submitted.status_code == 200, submitted.text
    run = submitted.json()["data"]
    await app.state.processor(header["X-Org-Id"], run["render_job_id"])
    ready = await api.get(f"/export-runs/{run['id']}", headers=header)
    assert ready.status_code == 200, ready.text
    assert ready.json()["data"]["state"] == "awaiting_release", ready.text
    assert ready.json()["data"]["export_id"] is None
    return ready.json()["data"]


@pytest.mark.parametrize("with_page", [False, True])
async def test_review_export_real_chain_and_every_route_isolated(
    tenants, tmp_path, admin_engine, with_page
):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, provider):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(api, app, header, tmp_path)
        selected, binding = await setup_template(api, header, task)
        foreign_binding = await api.post(
            "/export-template-bindings",
            headers=headers[1],
            json={
                "template_revision_id": binding["template_revision_id"],
                "expected_template_sha256": binding["template_sha256"],
                "sections": binding["sections"],
                "dry_run": True,
            },
        )
        assert foreign_binding.status_code == 404
        confirmed = None
        expected_png = None
        if with_page:
            _, _, _, _, source = await select_real_materials(api, header, task, tmp_path)
            card = await create_card(
                api,
                header,
                task,
                extraction,
                requirements[1],
                {
                    "response_kind": "evidence",
                    "response_text": "Synthetic certificate QMS-2026 is supplied.",
                    "deviation": "none",
                    "deviation_note": "The named synthetic page is attached.",
                    "evidence": [
                        {
                            "kind": "certificate_pdf_page",
                            "evidence_source_id": source["id"],
                            "quote": CERTIFICATE_PAGE,
                        }
                    ],
                },
            )
            set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
            submitted = await require_action(api, header, card, "submit")
            confirmed = await require_action(
                api,
                header,
                submitted,
                "confirm",
                reviewed_evidence_ids=[e["id"] for e in submitted["evidence"]],
                reviewed_warning_codes=submitted["warning_codes"],
                reason="Synthetic acceptance review.",
            )
            expected_png = source["preview"]["sha256"]
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
        set_role(admin_engine, tenants["orgs"][1], tenants["users"][1], "bidder")
        draft_id = await draft(api, app, header, task, extraction)
        body = {
            "draft_id": draft_id,
            "task_template_id": selected["id"],
            "binding_id": binding["id"],
            "mode": "final_section",
        }
        calls = provider.calls
        blocked = await api.post(
            f"/tasks/{task}/export-runs", headers=header, json={**body, "dry_run": True}
        )
        assert not blocked.json()["data"]["ready"]
        assert any(i["code"] == "export_gaps_present" for i in blocked.json()["data"]["issues"])
        body["mode"] = "review_copy"
        run = await prepared(api, app, header, task, body)
        released = await api.post(
            f"/export-runs/{run['id']}/release",
            headers=header,
            json={
                "expected_input_hash": run["input_hash"],
                "expected_candidate_sha256": run["candidate_sha256"],
            },
        )
        assert released.status_code == 200, released.text
        assert released.json()["ok"] is False
        export = released.json()["data"]
        assert export["completion"] == "partial" and export["validity"] == "current"
        repeated = await api.post(
            f"/export-runs/{run['id']}/release",
            headers=header,
            json={
                "expected_input_hash": run["input_hash"],
                "expected_candidate_sha256": run["candidate_sha256"],
            },
        )
        assert repeated.json()["data"]["id"] == export["id"]
        link = await api.get(f"/exports/{export['id']}/download-link", headers=header)
        assert link.status_code == 200, link.text
        downloaded = await api.get(link.json()["data"]["url"], headers=header)
        assert downloaded.status_code == 200, downloaded.text
        assert hashlib.sha256(downloaded.content).hexdigest() == export["file"]["sha256"]
        doc = Document(BytesIO(downloaded.content))
        assert len(doc.tables) >= 3
        with ZipFile(BytesIO(downloaded.content)) as archive:
            assert "不得提交" in "".join(
                archive.read(name).decode() for name in archive.namelist() if name.endswith(".xml")
            )
            media = [
                hashlib.sha256(archive.read(n)).hexdigest()
                for n in archive.namelist()
                if n.startswith("word/media/")
            ]
            assert media == ([expected_png] if with_page else [])
        assert provider.calls == calls
        checks = [
            ("GET", f"/export-runs/{run['id']}", None),
            (
                "POST",
                f"/export-runs/{run['id']}/release",
                {
                    "expected_input_hash": run["input_hash"],
                    "expected_candidate_sha256": run["candidate_sha256"],
                },
            ),
            ("GET", f"/tasks/{task}/exports", None),
            ("GET", f"/exports/{export['id']}", None),
            ("GET", f"/exports/{export['id']}/download-link", None),
            ("GET", link.json()["data"]["url"], None),
            ("GET", f"/jobs/{run['render_job_id']}", None),
            ("POST", f"/jobs/{run['render_job_id']}/cancel", None),
            (
                "GET",
                f"/export-template-bindings?template_revision_id={binding['template_revision_id']}",
                None,
            ),
            ("POST", f"/tasks/{task}/export-runs", {**body, "dry_run": True}),
        ]
        for method, path, request in checks:
            response = await api.request(
                method,
                path,
                headers=headers[1],
                **({"json": request} if request is not None else {}),
            )
            assert response.status_code == 404, (method, path.split("?")[0], response.text)
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(UsageRecord)
                    .where(UsageRecord.job_id == UUID(run["render_job_id"]))
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "export.released")
                )
                == 1
            )
        artifact = Path("data/work/export-acceptance") / ("page" if with_page else "gaps")
        await asyncio.to_thread(artifact.mkdir, parents=True, exist_ok=True)
        (artifact / "synthetic-review.docx").write_bytes(downloaded.content)
        (artifact / "receipt.json").write_text(
            json.dumps(
                {"file_sha256": export["file"]["sha256"], "media_sha256": media, "synthetic": True},
                indent=2,
            )
        )
        if confirmed:
            await require_action(
                api, header, confirmed, "reopen", reason="Synthetic invalidation check."
            )
            old_link = await api.get(link.json()["data"]["url"], headers=header)
            assert old_link.status_code == 409
            history = await api.get(f"/exports/{export['id']}", headers=header)
            assert history.json()["ok"] and history.json()["data"]["validity"] == "stale"
            stale_run = await api.get(f"/export-runs/{run['id']}", headers=header)
            assert stale_run.json()["data"]["state"] == "invalidated"
            assert stale_run.json()["data"]["candidate_sha256"] is None
            assert stale_run.json()["data"]["export_id"] == export["id"]
            assert (
                confirmed["requirement_id"] in history.json()["data"]["invalidated_requirement_ids"]
            )
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "admin")
        for method, path, request in checks[:6]:
            response = await api.request(
                method, path, headers=header, **({"json": request} if request is not None else {})
            )
            assert response.status_code == 403


async def test_export_role_and_unknown_resource_boundaries(tenants, tmp_path, admin_engine):
    async with phase_one_client(tenants, tmp_path) as (api, _, headers, _):
        unknown = str(uuid4())
        for role in ("admin", "technical", "viewer"):
            set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], role)
            for path in (
                f"/export-runs/{unknown}",
                f"/exports/{unknown}",
                f"/exports/{unknown}/download-link",
                f"/tasks/{unknown}/exports",
            ):
                assert (await api.get(path, headers=headers[0])).status_code == 403
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
        for path in (
            f"/export-runs/{unknown}",
            f"/exports/{unknown}",
            f"/exports/{unknown}/download-link",
            f"/tasks/{unknown}/exports",
        ):
            assert (await api.get(path, headers=headers[0])).status_code == 404


async def complete_inputs(api, app, header, tenants, admin_engine, tmp_path):
    """Create three confirmed tables plus an independent comply-only decision."""
    task, _, extraction, requirements = await create_tender(api, app, header, tmp_path)
    selected, binding = await setup_template(api, header, task)
    _, _, _, _, page = await select_real_materials(api, header, task, tmp_path)
    reviewed = {}
    for index in (0, 1, 2, 4):
        card = await create_card(
            api,
            header,
            task,
            extraction,
            requirements[index],
            {
                "response_kind": "evidence" if index == 1 else "commitment",
                "response_text": f"Synthetic confirmed response for requirement {index + 1}.",
                "deviation": "negative" if index == 2 else "none",
                "deviation_note": "Offered forty days exceeds required thirty days."
                if index == 2
                else "The specified synthetic response is retained verbatim.",
                "evidence": [
                    {
                        "kind": "certificate_pdf_page",
                        "evidence_source_id": page["id"],
                        "quote": CERTIFICATE_PAGE,
                    }
                ]
                if index == 1
                else [],
            },
        )
        if index == 0:
            response = await api.post(
                f"/cards/{card['id']}/classification",
                headers=header,
                json={
                    "expected_revision": card["revision"],
                    "review_domain": "technical",
                    "reason": "The synthetic scored requirement is technical.",
                },
            )
            assert response.status_code == 200, response.text
            card = response.json()["data"]
        reviewed[index] = card
    for role, indices in (("technical", (0, 2, 4)), ("bidder", (1,))):
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], role)
        for index in indices:
            submitted = await require_action(api, header, reviewed[index], "submit")
            reviewed[index] = await require_action(
                api,
                header,
                submitted,
                "confirm",
                reviewed_evidence_ids=[e["id"] for e in submitted["evidence"]],
                reviewed_warning_codes=submitted["warning_codes"],
                reason="Synthetic review of proof obligations.",
            )
        if role == "technical":
            disposition = await api.post(
                f"/tasks/{task}/cards/dispositions",
                headers=header,
                json={
                    "extraction_job_id": extraction,
                    "items": [
                        {
                            "requirement_id": requirements[3]["id"],
                            "expected_revision": None,
                            "disposition": "comply_only",
                            "reason": "The complaint procedure requires compliance only.",
                        }
                    ],
                },
            )
            assert disposition.status_code == 200, disposition.text
    draft_id = await draft(api, app, header, task, extraction)
    body = {
        "draft_id": draft_id,
        "task_template_id": selected["id"],
        "binding_id": binding["id"],
        "mode": "final_section",
    }
    return task, body, reviewed, page


async def test_final_export_verbatim_negative_ack_hash_gates_and_immutable_download(
    tenants, tmp_path, admin_engine
):
    from app.models.exports import Export
    from app.services.auth import ROLE_SCOPES, Identity, set_actor_context

    async with phase_one_client(tenants, tmp_path) as (api, app, headers, provider):
        header = headers[0]
        task, body, _, page = await complete_inputs(
            api, app, header, tenants, admin_engine, tmp_path
        )
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            before = await session.scalar(select(func.count()).select_from(AuditLog))
        preview = await api.post(
            f"/tasks/{task}/export-runs", headers=header, json={**body, "dry_run": True}
        )
        assert preview.status_code == 200, preview.text
        fixed = preview.json()["data"]
        assert fixed["ready"] and fixed["gap_count"] == 0
        assert fixed["table_rows"] == {"substantive": 1, "commercial": 1, "technical": 2}
        assert (
            fixed["comply_only_count"] == 1
            and fixed["negative_count"] == 1
            and fixed["attachment_pages"] == 1
        )
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert await session.scalar(select(func.count()).select_from(AuditLog)) == before
        missing_ack = await api.post(
            f"/tasks/{task}/export-runs",
            headers=header,
            json={**body, "expected_input_hash": fixed["input_hash"]},
        )
        assert missing_ack.status_code == 400
        assert missing_ack.json()["data"]["error"]["code"] == "export_acknowledgment_mismatch"
        wrong_input = await api.post(
            f"/tasks/{task}/export-runs",
            headers=header,
            json={**body, "expected_input_hash": "0" * 64},
        )
        assert wrong_input.status_code == 409
        run = await prepared(api, app, header, task, body)
        bad_candidate = await api.post(
            f"/export-runs/{run['id']}/release",
            headers=header,
            json={"expected_input_hash": run["input_hash"], "expected_candidate_sha256": "0" * 64},
        )
        assert bad_candidate.status_code == 409
        release_body = {
            "expected_input_hash": run["input_hash"],
            "expected_candidate_sha256": run["candidate_sha256"],
        }
        released = await api.post(
            f"/export-runs/{run['id']}/release", headers=header, json=release_body
        )
        assert released.status_code == 200 and released.json()["ok"], released.text
        export = released.json()["data"]
        assert export["completion"] == "complete"
        link = await api.get(f"/exports/{export['id']}/download-link", headers=header)
        content = (await api.get(link.json()["data"]["url"], headers=header)).content
        with ZipFile(BytesIO(content)) as package:
            document_xml = package.read("word/document.xml").decode()
            assert "负偏离" in document_xml and "不得提交" not in document_xml
            assert "Synthetic confirmed response for requirement 3." in document_xml
            assert [
                hashlib.sha256(package.read(n)).hexdigest()
                for n in package.namelist()
                if n.startswith("word/media/")
            ] == [page["preview"]["sha256"]]
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            await set_actor_context(
                session,
                Identity(
                    tenants["users"][0], tenants["orgs"][0], set(ROLE_SCOPES["bidder"]), "bidder"
                ),
            )
            row = await session.get(Export, UUID(export["id"]))
            encrypted = app.state.storage.path(row.org_id, row.object_key).read_bytes()
            assert encrypted.startswith(b"BIDFILE1\n") and content not in encrypted
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(UsageRecord)
                    .where(UsageRecord.job_id == UUID(run["render_job_id"]))
                )
                == 0
            )
        artifact = Path("data/work/export-acceptance/final")
        await asyncio.to_thread(artifact.mkdir, parents=True, exist_ok=True)
        (artifact / "synthetic-final.docx").write_bytes(content)
        (artifact / "receipt.json").write_text(
            json.dumps(
                {
                    "file_sha256": hashlib.sha256(content).hexdigest(),
                    "input_hash": run["input_hash"],
                    "synthetic": True,
                },
                indent=2,
            )
        )
        # An old signature never bypasses its current authenticated membership.
        from app.models.entities import Membership
        from sqlalchemy.orm import Session

        with Session(admin_engine) as session, session.begin():
            member = session.scalar(
                select(Membership).where(
                    Membership.org_id == tenants["orgs"][0],
                    Membership.user_id == tenants["users"][0],
                )
            )
            member.active = False
        denied = await api.get(link.json()["data"]["url"], headers=header)
        assert denied.status_code in (403, 404)
        assert provider.calls == 1


@pytest.mark.parametrize("revoke", ["reopen", "cancel", "role"])
async def test_queued_export_rechecks_inputs_attempt_and_authorization(
    tenants, tmp_path, admin_engine, revoke
):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        task, body, reviewed, _ = await complete_inputs(
            api, app, header, tenants, admin_engine, tmp_path
        )
        preview = (
            await api.post(
                f"/tasks/{task}/export-runs", headers=header, json={**body, "dry_run": True}
            )
        ).json()["data"]
        submitted = await api.post(
            f"/tasks/{task}/export-runs",
            headers=header,
            json={
                **body,
                "expected_input_hash": preview["input_hash"],
                "acknowledged_issue_ids": [
                    i["issue_id"] for i in preview["issues"] if i["severity"] == "acknowledge"
                ],
            },
        )
        assert submitted.status_code == 200, submitted.text
        run = submitted.json()["data"]
        if revoke == "reopen":
            await require_action(
                api, header, reviewed[1], "reopen", reason="Synthetic queued invalidation."
            )
        elif revoke == "cancel":
            response = await api.post(f"/jobs/{run['render_job_id']}/cancel", headers=header)
            assert response.status_code == 200, response.text
        else:
            set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "viewer")
        await app.state.processor(header["X-Org-Id"], run["render_job_id"])
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
        shown = await api.get(f"/export-runs/{run['id']}", headers=header)
        assert shown.status_code == 200, shown.text
        assert shown.json()["data"]["state"] in ("invalidated", "cancelled", "failed")
        assert shown.json()["data"]["export_id"] is None
        assert (await api.get(f"/tasks/{task}/exports", headers=header)).json()["items"] == []


async def test_token_cannot_use_export_or_generic_job_read_cancel(tenants, tmp_path, admin_engine):
    from datetime import UTC, datetime, timedelta

    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        token_reply = await api.post(
            "/tokens",
            headers=header,
            json={
                "name": "Synthetic export rejection token",
                "scopes": [
                    "task:read",
                    "draft:read",
                    "card:read",
                    "template:read",
                    "job:read",
                    "job:cancel",
                ],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        assert token_reply.status_code == 200, token_reply.text
        token = token_reply.json()["data"]["token"]
        token_headers = {**header, "Authorization": "Bearer " + token}
        task, body, _, _ = await complete_inputs(api, app, header, tenants, admin_engine, tmp_path)
        run = await prepared(api, app, header, task, body)
        for method, path, data in [
            ("GET", f"/export-runs/{run['id']}", None),
            ("GET", f"/jobs/{run['render_job_id']}", None),
            ("POST", f"/jobs/{run['render_job_id']}/cancel", None),
            ("POST", f"/tasks/{task}/export-runs", {**body, "dry_run": True}),
            (
                "POST",
                f"/export-runs/{run['id']}/release",
                {
                    "expected_input_hash": run["input_hash"],
                    "expected_candidate_sha256": run["candidate_sha256"],
                },
            ),
        ]:
            result = await api.request(
                method, path, headers=token_headers, **({"json": data} if data is not None else {})
            )
            assert result.status_code == 403, result.text


@pytest.mark.parametrize("limit", ["memory", "deadline", "output"])
async def test_export_worker_limits_never_publish_a_partial_file(
    tenants, tmp_path, admin_engine, limit
):
    from app.providers.base import ProviderFailure

    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        task, body, _, _ = await complete_inputs(api, app, header, tenants, admin_engine, tmp_path)
        preview = (
            await api.post(
                f"/tasks/{task}/export-runs", headers=header, json={**body, "dry_run": True}
            )
        ).json()["data"]
        submitted = await api.post(
            f"/tasks/{task}/export-runs",
            headers=header,
            json={
                **body,
                "expected_input_hash": preview["input_hash"],
                "acknowledged_issue_ids": [
                    i["issue_id"] for i in preview["issues"] if i["severity"] == "acknowledge"
                ],
            },
        )
        assert submitted.status_code == 200, submitted.text
        run = submitted.json()["data"]
        settings = app.state.processor.settings
        if limit == "memory":
            settings.export_memory_bytes = 1
        elif limit == "deadline":
            settings.export_deadline_seconds = 0.001
        else:
            settings.export_max_output_bytes = 1
        for attempt in range(3 if limit == "deadline" else 1):
            if limit == "deadline" and attempt < 2:
                with pytest.raises(ProviderFailure):
                    await app.state.processor(header["X-Org-Id"], run["render_job_id"])
            else:
                await app.state.processor(header["X-Org-Id"], run["render_job_id"])
        status = await api.get(f"/jobs/{run['render_job_id']}", headers=header)
        assert status.json()["data"]["status"] == "failed", status.text
        assert status.json()["data"]["error"]["code"] in {
            "export_memory_limit",
            "export_render_timeout",
            "export_resource_limit",
        }
        assert (await api.get(f"/tasks/{task}/exports", headers=header)).json()["items"] == []
        shown = (await api.get(f"/export-runs/{run['id']}", headers=header)).json()["data"]
        assert (
            shown["state"] == "failed"
            and shown["candidate_sha256"] is None
            and shown["export_id"] is None
        )
