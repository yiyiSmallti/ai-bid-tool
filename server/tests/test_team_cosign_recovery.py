"""DB-backed recovery scenarios specified before their integration changes.

Failure inventory: a legacy disposition batch mutates an otherwise permitted
single-domain item before rejecting its multi-domain sibling; completed response
approvals survive actual task certificate replacement or citation-repair writes;
an org role restored after authority loss resurrects old signatures; already
released DOCX bytes or cached preview PNGs remain readable through old signed
links after review-policy or signer invalidation. Historical metadata stays
readable with stale status; it cannot make the files usable again.

Execution requires the explicitly supplied isolated PostgreSQL runtime. HTTP
conversion uses the existing fake converter transport and starts no service.
Artifacts contain public synthetic metadata/hashes, never headers or signed URLs.
"""

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from app.models.entities import Membership
from sqlalchemy import text, update
from sqlalchemy.orm import Session
from task_fixtures import actor_context
from test_check import publish_draft
from test_exports import prepared, setup_template
from test_page_previews import FakeConverter, is_png, preview_client, two_page_pdf
from test_response_cards import CERTIFICATE_PAGE, select_real_materials
from test_team_cosign_acceptance import complete, history, refresh_card, revoke
from test_team_cosign_consumers import cosign_scope

ARTIFACTS = (
    Path(__file__).resolve().parents[2] / "data/work/team-workflow-acceptance/cosign/recovery"
)


def artifact(name, **values):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / f"{name}.json").write_text(
        json.dumps(
            {
                "command": "uv run pytest server/tests/test_team_cosign_recovery.py -q",
                **values,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )


def revision_count(admin_engine, task_id):
    with admin_engine.connect() as connection:
        return connection.scalar(
            text(
                "SELECT count(*) FROM response_card_revisions v "
                "JOIN response_cards c ON c.org_id=v.org_id AND c.id=v.card_id "
                "WHERE c.task_id=:task"
            ),
            {"task": task_id},
        )


async def test_mixed_disposition_batch_rolls_back_single_domain_item(
    api, headers, tenants, admin_engine
):
    # Index 0 is co-sign technical; index 2 is independent single-domain technical.
    # One professional actor can legitimately approve either primary domain, so
    # the batch cannot pass merely because a different-domain permission fails.
    scope = await cosign_scope(api, headers, tenants, admin_engine, count=3)
    single = await api.post(
        f"/tasks/{scope['task_id']}/cards",
        headers=headers[0],
        json={
            "extraction_job_id": str(scope["job_id"]),
            "requirement_id": str(scope["requirement_ids"][2]),
            "content": {
                "response_kind": "commitment",
                "response_text": "Synthetic independent response",
                "deviation": "none",
                "deviation_note": "Synthetic requirement is met",
            },
        },
    )
    assert single.status_code == 200, single.text
    single = single.json()["data"]
    technical = scope["members"]["technical"]["headers"]
    policy = await api.get(
        f"/tasks/{scope['task_id']}/requirements/{scope['requirement_ids'][2]}/review-policy",
        headers=technical,
        params={"extraction_job_id": str(scope["job_id"])},
    )
    assert policy.status_code == 200, policy.text
    assert policy.json()["data"]["policy"]["required_domains"] == ["technical"]
    before = revision_count(admin_engine, scope["task_id"])
    response = await api.post(
        f"/tasks/{scope['task_id']}/cards/dispositions",
        headers=technical,
        json={
            "extraction_job_id": str(scope["job_id"]),
            "items": [
                {
                    "requirement_id": str(scope["requirement_ids"][2]),
                    "expected_revision": single["revision"],
                    "disposition": "comply_only",
                    "reason": "Synthetic permitted single-domain item",
                },
                {
                    "requirement_id": str(scope["requirement_ids"][0]),
                    "expected_revision": scope["card"]["revision"],
                    "disposition": "comply_only",
                    "reason": "Synthetic unsigned multi-domain item",
                },
            ],
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["data"]["error"]["code"] == "cosign_required"
    assert revision_count(admin_engine, scope["task_id"]) == before
    for initial in (single, scope["card"]):
        shown = await api.get(f"/cards/{initial['id']}", headers=headers[0])
        assert shown.status_code == 200, shown.text
        current = shown.json()["data"]
        assert current["revision"] == initial["revision"]
        assert current["revision_id"] == initial["revision_id"]
        assert current["disposition"] == initial["disposition"]
        assert current["disposition"] != "comply_only"
        assert current["disposition_by"] is None and current["disposition_at"] is None
    artifact("mixed-disposition-rollback", failure=response.json(), revision_count=before)


async def test_completed_round_retires_when_task_certificate_is_replaced(
    api, headers, tenants, admin_engine, tmp_path
):
    scope = await cosign_scope(api, headers, tenants, admin_engine)
    _, _, certificate, selected, source = await select_real_materials(
        api,
        headers[0],
        str(scope["task_id"]),
        tmp_path,
    )
    updated = await api.put(
        f"/cards/{scope['card']['id']}",
        headers=headers[0],
        json={
            "expected_revision": scope["card"]["revision"],
            "content": {
                "response_kind": "evidence",
                "response_text": "Synthetic reviewed certificate",
                "deviation": "none",
                "deviation_note": "Synthetic certificate page is retained",
                "evidence": [
                    {
                        "kind": "certificate_pdf_page",
                        "evidence_source_id": source["id"],
                        "quote": CERTIFICATE_PAGE,
                    }
                ],
            },
        },
    )
    assert updated.status_code == 200, updated.text
    scope["card"] = updated.json()["data"]
    await complete(api, headers[0], scope)
    before = await history(api, headers[0], scope)
    assert before["data"]["summary"]["status"] == "complete"
    current = await api.get(
        "/resources/certificates",
        headers=headers[0],
        params={"certificate_id": certificate["certificate_id"]},
    )
    assert current.status_code == 200, current.text
    current = current.json()["items"][0]
    revised = await api.post(
        f"/resources/certificates/{certificate['certificate_id']}/revisions",
        headers=headers[0],
        json={
            "expected_revision": current["revision"],
            "data": {**current["data"], "name": "Synthetic replaced certificate"},
        },
    )
    assert revised.status_code == 200, revised.text
    # The library's newer revision alone does not replace the task's pinned input.
    still_pinned = await history(api, headers[0], scope)
    assert still_pinned["data"]["summary"]["status"] == "complete"
    replacement = await api.post(
        f"/tasks/{scope['task_id']}/certificates",
        headers=headers[0],
        json={"certificate_id": certificate["certificate_id"]},
    )
    assert replacement.status_code == 200, replacement.text
    assert replacement.json()["data"]["replaced_snapshot_id"] == selected["id"]
    retired = await history(api, headers[0], scope)
    card = await refresh_card(api, headers[0], scope)
    assert retired["data"]["summary"]["status"] == "invalidated"
    assert retired["data"]["summary"]["signed_domains"] == []
    assert retired["items"] == before["items"]
    assert card["eligibility"] == "stale_material"
    assert card["revision"] == before["data"]["round"]["card_revision"] + 1
    assert card["evidence"][0]["active_selection"] is False
    # Historic Evidence confirmation remains true but cannot approve the new material set.
    assert card["evidence"][0]["confirmed_by"] is not None
    preview = await api.post(
        f"/tasks/{scope['task_id']}/drafts",
        headers=headers[0],
        json={"extraction_job_id": str(scope["job_id"]), "dry_run": True},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["response_requirements"] == 0
    artifact("material-replacement", before=before, retired=retired, card=card)


async def test_citation_repair_retires_completed_round_without_fabricating_new_signatures(
    api, headers, tenants, admin_engine
):
    scope = await cosign_scope(api, headers, tenants, admin_engine)
    await complete(api, headers[0], scope)
    before = await history(api, headers[0], scope)
    original_revision = scope["card"]["revision"]
    preview = await api.get(
        f"/tasks/{scope['task_id']}/requirements/repair",
        headers=headers[0],
        params={"job": str(scope["job_id"])},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["repairable"] == 1
    item = preview.json()["items"][0]
    assert item["model_quote"] is None and item["quote_changed"] is False
    repaired = await api.post(
        f"/tasks/{scope['task_id']}/requirements/repair",
        headers=headers[0],
        json={
            "extraction_job_id": str(scope["job_id"]),
            "expected_preview": preview.json()["data"]["preview_hash"],
            "reason": "Synthetic provenance repair preserving the existing exact quote",
        },
    )
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["data"]["changed"] == 1
    with admin_engine.connect() as connection:
        quote, model_quote = connection.execute(
            text("SELECT quote,model_quote FROM requirements WHERE org_id=:org AND id=:id"),
            {"org": tenants["orgs"][0], "id": scope["requirement_ids"][0]},
        ).one()
    assert quote == model_quote == item["source"]["quote"]
    retired = await history(api, headers[0], scope)
    card = await refresh_card(api, headers[0], scope)
    assert retired["data"]["summary"]["status"] == "invalidated"
    assert retired["data"]["summary"]["signed_domains"] == []
    assert retired["items"] == before["items"]
    assert card["revision"] == original_revision and card["state"] == "confirmed"
    assert card["eligibility"] == "needs_reconfirmation"
    artifact("citation-repair", receipt=repaired.json(), retired=retired, card=card)


async def test_restored_org_role_does_not_resurrect_completed_round(
    api, headers, tenants, admin_engine
):
    scope = await cosign_scope(api, headers, tenants, admin_engine)
    await complete(api, headers[0], scope)
    before = await history(api, headers[0], scope)
    states = []
    for role in ("viewer", "bidder"):
        with Session(admin_engine) as session, session.begin():
            actor_context(session, tenants["orgs"][0], tenants["users"][0])
            session.execute(
                update(Membership)
                .where(
                    Membership.org_id == tenants["orgs"][0],
                    Membership.user_id == scope["members"]["commercial"]["user"],
                )
                .values(role=role)
            )
        state = await history(api, headers[0], scope)
        assert state["data"]["summary"]["status"] == "invalidated"
        assert state["data"]["summary"]["signed_domains"] == []
        assert state["items"] == before["items"]
        states.append(state)
    preview = await api.post(
        f"/tasks/{scope['task_id']}/drafts",
        headers=headers[0],
        json={"extraction_job_id": str(scope["job_id"]), "dry_run": True},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["response_requirements"] == 0
    artifact("restored-org-role", before=before, retired=states)


@pytest.mark.parametrize("mutation", ["policy", "member"])
async def test_released_export_and_cached_preview_enforce_live_cosign_gate(
    tenants, admin_engine, tmp_path, mutation
):
    fake = FakeConverter()
    fake.reply = two_page_pdf()
    async with preview_client(tenants, tmp_path, "http://converter.test") as (api, app, headers):
        app.state.processor.converter_transport = httpx.MockTransport(fake.handler)
        scope = await cosign_scope(api, headers, tenants, admin_engine)
        selected, binding = await setup_template(api, headers[0], str(scope["task_id"]))
        await complete(api, headers[0], scope)
        bidder = scope["members"]["commercial"]["headers"]
        draft = await publish_draft(api, app, bidder, str(scope["task_id"]), str(scope["job_id"]))
        run = await prepared(
            api,
            app,
            bidder,
            str(scope["task_id"]),
            {
                "draft_id": draft["draft_id"],
                "task_template_id": selected["id"],
                "binding_id": binding["id"],
                "mode": "review_copy",
            },
        )
        released = await api.post(
            f"/export-runs/{run['id']}/release",
            headers=bidder,
            json={
                "expected_input_hash": run["input_hash"],
                "expected_candidate_sha256": run["candidate_sha256"],
            },
        )
        assert released.status_code == 200, released.text
        export = released.json()["data"]
        base = f"/exports/{export['id']}"
        link = await api.get(base + "/download-link", headers=bidder)
        assert link.status_code == 200, link.text
        signed_url = link.json()["data"]["url"]
        downloaded = await api.get(signed_url, headers=bidder)
        assert downloaded.status_code == 200
        assert hashlib.sha256(downloaded.content).hexdigest() == export["file"]["sha256"]
        opened = await api.post(base + "/preview", headers=bidder)
        assert opened.status_code == 200, opened.text
        await app.state.processor(bidder["X-Org-Id"], opened.json()["data"]["job_id"])
        assert is_png(await api.get(base + "/preview/pages/1", headers=bidder))
        assert len(fake.received) == 1
        await revoke(api, headers[0], scope, mutation)
        failures = {}
        for label, method, path in (
            ("old_signed_download", "GET", signed_url),
            ("new_download_link", "GET", base + "/download-link"),
            ("cached_preview_page", "GET", base + "/preview/pages/1"),
            ("preview_reopen", "POST", base + "/preview"),
        ):
            response = await api.request(method, path, headers=bidder)
            assert response.status_code == 409, response.text
            assert response.json()["data"]["error"]["code"] == "export_input_changed"
            failures[label] = response.json()
        # Metadata is auditable history; its stale state cannot expose usable bytes.
        shown = await api.get(base, headers=bidder)
        preview = await api.get(base + "/preview", headers=bidder)
        retired_run = await api.get(f"/export-runs/{run['id']}", headers=bidder)
        assert shown.status_code == preview.status_code == retired_run.status_code == 200
        assert shown.json()["data"]["validity"] == "stale"
        assert preview.json()["data"]["status"] == "invalidated"
        assert preview.json()["data"]["page_count"] is None
        assert retired_run.json()["data"]["state"] == "invalidated"
        assert retired_run.json()["data"]["candidate_sha256"] is None
        assert len(fake.received) == 1
        artifact(
            f"released-export-{mutation}",
            file_sha256=export["file"]["sha256"],
            failures=failures,
            stale_export=shown.json(),
            stale_preview=preview.json(),
        )
