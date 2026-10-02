"""Prototype evidence decisions through real preparation, ingest, cards and API routes.

Failure modes fixed before the scenarios:
- a prototype record is accepted only when its running generation job, task feature/revision,
  requirement, stored HTML hash, stored source PNG hash and sandbox receipt are fixed together;
- preparation and ingest must reproduce that exact stored PNG before a prototype asset exists;
- preview contains only live, confirmed prototype image evidence owned by the requested module,
  and explicit evidence ids cannot omit, add or cross an org/task boundary;
- duplicate module selections, omitted decision items, duplicate evidence items, stale preflight
  hashes and mismatched previous-decision ids fail atomically;
- keep requires an explicit keep basis, replace forbids it, and changing an existing decision
  requires both the exact previous id and a non-empty reason;
- bidder/admin responsibility mismatches and API tokens cannot preview or apply a human
  prototype decision even if they can read screenshots and cards;
- idempotent replay creates no second batch, replacement supersedes keep, and reopening the
  confirmed card makes the latest decision stale;
- preview, apply and list all hide the other organization's task and evidence as 404.

The test uses synthetic HTML/PNG stored through the real local storage provider and seeds only
the otherwise-unimplemented ``ScreenshotPrototypeRun`` plus its running generation job. It does
not invent a prototype/product API or call a model. PostgreSQL and the Rust renderer are genuine
dependencies and are explicitly skipped when unavailable.
"""

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.models.entities import Job, TaskFeature
from app.models.screenshots import PrototypeDecisionBatch, ScreenshotPrototypeRun
from app.providers.screenshot_renderer import ScreenshotRenderer
from app.schemas.screenshot_contracts import ImagePlan, PrototypeSource, ScreenshotIngest
from app.services import screenshots
from sqlalchemy import func, select
from test_card_generation import token_header  # pyright: ignore[reportMissingImports]
from test_response_cards import (  # pyright: ignore[reportMissingImports]
    create_card,
    create_tender,
    phase_one_client,
    require_action,
    set_role,
)
from test_screenshot_cards import selected_feature  # pyright: ignore[reportMissingImports]
from test_screenshot_renderer import _rgb_png  # pyright: ignore[reportMissingImports]


def renderer_required():
    renderer = ScreenshotRenderer()
    if not renderer.binary.is_file() or not os.access(renderer.binary, os.X_OK):
        pytest.skip(f"build the Rust renderer first: {renderer.binary}")


async def seed_prototype(
    app,
    org_id: UUID,
    user_id: UUID,
    task_id: UUID,
    extraction_id: UUID,
    requirement_id: UUID,
    task_feature_id: UUID,
):
    source_png = _rgb_png(32, 20, [(220, 235, 250)] * (32 * 20))
    html = b"<!doctype html><title>Synthetic prototype fixture</title><main>Workflow</main>"
    prototype_id, generation_id = uuid4(), uuid4()
    html_hash, image_hash = hashlib.sha256(html).hexdigest(), hashlib.sha256(source_png).hexdigest()
    html_key = f"org/{org_id}/synthetic-prototype/{prototype_id}/prototype.html"
    image_key = f"org/{org_id}/synthetic-prototype/{prototype_id}/source.png"
    await app.state.storage.put(org_id, html_key, html)
    await app.state.storage.put(org_id, image_key, source_png)
    async with app.state.db.transaction(org_id) as session:
        extraction = await session.get(Job, extraction_id)
        feature = await session.get(TaskFeature, task_feature_id)
        assert extraction is not None and feature is not None
        job = Job(
            id=generation_id,
            org_id=org_id,
            task_id=task_id,
            document_id=extraction.document_id,
            kind="prototype_generate",
            cache_key=uuid4().hex * 2,
            status="running",
            run_id=uuid4(),
            result={
                "submission": {
                    "actor_user_id": str(user_id),
                    "actor_token_id": None,
                    "actor_kind": "session",
                    "scopes": [
                        "task:read",
                        "resource:read",
                        "screenshot:read",
                        "screenshot:write",
                    ],
                }
            },
        )
        session.add(job)
        session.add(
            ScreenshotPrototypeRun(
                id=prototype_id,
                org_id=org_id,
                task_id=task_id,
                extraction_job_id=extraction_id,
                requirement_id=requirement_id,
                task_feature_id=task_feature_id,
                feature_revision_id=feature.feature_revision_id,
                generation_job_id=generation_id,
                input_hash="1" * 64,
                html_sha256=html_hash,
                html_storage_key=html_key,
                source_image_sha256=image_hash,
                source_image_key=image_key,
                sandbox_receipt_id="synthetic-sandbox-receipt",
                sandbox_receipt_sha256="2" * 64,
                provenance={
                    "provider": "synthetic-test-only",
                    "model": "synthetic-prototype-model",
                    "catalog_identity": "synthetic-catalog@1",
                },
            )
        )
    return {
        "id": prototype_id,
        "html_sha256": html_hash,
        "source_png": source_png,
        "source_image_sha256": image_hash,
    }


async def prepare_ingest_prototype(api, app, header, task_id, extraction_id, prototype):
    source = PrototypeSource(kind="prototype_render", prototype_run_id=prototype["id"])
    png, receipt = await screenshots.prepare(prototype["source_png"], source, ImagePlan())
    body = ScreenshotIngest(
        extraction_job_id=extraction_id,
        prepared=receipt,
        reviewed_upload_sha256=receipt.image.sha256,
        idempotency_key=uuid4(),
    )
    response = await api.post(
        f"/tasks/{task_id}/screenshots",
        headers=header,
        files={
            "file": ("SYNTHETIC-PROTOTYPE.png", png, "image/png"),
            "input": (None, body.model_dump_json(), "application/json"),
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["asset"]["origin"] == "prototype"
    assert data["asset"]["source_hash_assurance"] == "server_verified"
    assert data["asset"]["source_sha256"] == prototype["source_image_sha256"]
    return data


async def confirmed_prototype_card(api, header, task_id, extraction_id, requirement, uploaded):
    rendition = uploaded["rendition"]
    card = await create_card(
        api,
        header,
        task_id,
        extraction_id,
        requirement,
        {
            "response_kind": "evidence",
            "response_text": "The synthetic prototype displays the requested workflow.",
            "deviation": "none",
            "deviation_note": "The selected region visibly contains the workflow control.",
            "evidence": [
                {
                    "kind": "image_region",
                    "asset_id": uploaded["asset"]["id"],
                    "rendition_id": rendition["id"],
                    "expected_image_sha256": rendition["image"]["sha256"],
                    "region": {"x": 0, "y": 0, "width": 16, "height": 10},
                    "claim_scope": "functional_observation",
                    "visual_observation": "Synthetic workflow control is visible.",
                }
            ],
        },
    )
    pending = await require_action(api, header, card, "submit")
    confirmed = await require_action(
        api,
        header,
        pending,
        "confirm",
        reviewed_evidence_ids=[pending["evidence"][0]["id"]],
        reviewed_warning_codes=pending["warning_codes"],
        reason="Reviewed the prototype provenance and delivery obligation.",
    )
    return confirmed


def preview_input(extraction_id, feature_id, evidence_id=None):
    value = {
        "extraction_job_id": str(extraction_id),
        "module_label": "Synthetic workflow module",
        "task_feature_ids": [str(feature_id)],
    }
    if evidence_id:
        value["evidence_ids"] = [str(evidence_id)]
    return value


def decision_input(preview, extraction_id, feature_id, *, decision, reason=None, key=None):
    return {
        "extraction_job_id": str(extraction_id),
        "module_label": preview["module_label"],
        "task_feature_ids": [str(feature_id)],
        "expected_input_hash": preview["input_hash"],
        "items": [
            {
                **target,
                "decision": decision,
                "keep_basis": "will_deliver" if decision == "keep" else None,
                "reason": reason,
            }
            for target in preview["targets"]
        ],
        "idempotency_key": str(key or uuid4()),
    }


def artifact(value):
    path = Path("data/work/screenshots")
    path.mkdir(parents=True, exist_ok=True)
    sanitized = json.loads(json.dumps(value))
    (path / "prototype-decisions.json").write_text(
        json.dumps(sanitized, indent=2, sort_keys=True) + "\n"
    )


async def test_prototype_keep_replace_permissions_and_card_reopen(tenants, tmp_path, admin_engine):
    renderer_required()
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(
            api, app, header, tmp_path, suffix="prototype-decisions"
        )
        feature = await selected_feature(api, header, task)
        prototype = await seed_prototype(
            app,
            tenants["orgs"][0],
            tenants["users"][0],
            UUID(task),
            UUID(extraction),
            UUID(requirements[2]["id"]),
            UUID(feature["id"]),
        )
        uploaded = await prepare_ingest_prototype(
            api, app, header, task, UUID(extraction), prototype
        )
        second_prototype = await seed_prototype(
            app,
            tenants["orgs"][0],
            tenants["users"][0],
            UUID(task),
            UUID(extraction),
            UUID(requirements[3]["id"]),
            UUID(feature["id"]),
        )
        second_uploaded = await prepare_ingest_prototype(
            api, app, header, task, UUID(extraction), second_prototype
        )
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
        card = await confirmed_prototype_card(
            api, header, task, extraction, requirements[2], uploaded
        )
        second_card = await confirmed_prototype_card(
            api, header, task, extraction, requirements[3], second_uploaded
        )
        evidence = card["evidence"][0]
        second_evidence = second_card["evidence"][0]
        preview_body = preview_input(extraction, feature["id"])

        foreign_preview = await api.post(
            f"/tasks/{task}/prototype-decisions/preview",
            headers=headers[1],
            json=preview_body,
        )
        assert foreign_preview.status_code == 404
        duplicated_module = await api.post(
            f"/tasks/{task}/prototype-decisions/preview",
            headers=header,
            json={**preview_body, "task_feature_ids": [feature["id"], feature["id"]]},
        )
        assert duplicated_module.status_code == 422
        assert duplicated_module.json()["data"]["error"]["code"] == "invalid_input"

        for role in ("admin", "bidder"):
            set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], role)
            wrong_role = await api.post(
                f"/tasks/{task}/prototype-decisions/preview", headers=header, json=preview_body
            )
            assert wrong_role.status_code == 403
        # Only an admin can issue tokens; the token is then used while the member is technical.
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "admin")
        token = await token_header(
            api,
            header,
            scopes=["task:read", "card:read", "resource:read", "screenshot:read"],
        )
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
        token_denied = await api.post(
            f"/tasks/{task}/prototype-decisions/preview", headers=token, json=preview_body
        )
        assert token_denied.status_code == 403

        preview_response = await api.post(
            f"/tasks/{task}/prototype-decisions/preview",
            headers=header,
            json=preview_body,
        )
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()["data"]
        assert len(preview["targets"]) == 2
        target = next(row for row in preview["targets"] if row["evidence_id"] == evidence["id"])
        assert target["evidence_id"] == evidence["id"]
        assert target["html_sha256"] == prototype["html_sha256"]
        assert {row["evidence_id"] for row in preview["targets"]} == {
            evidence["id"],
            second_evidence["id"],
        }

        omitted_body = decision_input(preview, extraction, feature["id"], decision="keep")
        omitted_body["items"] = omitted_body["items"][:1]
        omitted = await api.post(
            f"/tasks/{task}/prototype-decisions",
            headers=header,
            json=omitted_body,
        )
        assert omitted.status_code == 409
        assert omitted.json()["data"]["error"]["code"] == "decision_set_mismatch"
        duplicate_item = decision_input(preview, extraction, feature["id"], decision="keep")
        duplicate_item["items"] = duplicate_item["items"] * 2
        duplicated = await api.post(
            f"/tasks/{task}/prototype-decisions", headers=header, json=duplicate_item
        )
        assert duplicated.status_code == 422

        keep_body = decision_input(preview, extraction, feature["id"], decision="keep", key=uuid4())
        kept_response = await api.post(
            f"/tasks/{task}/prototype-decisions", headers=header, json=keep_body
        )
        assert kept_response.status_code == 200, kept_response.text
        kept = kept_response.json()["data"]
        assert kept["duplicate"] is False
        assert len(kept["decisions"]) == 2
        assert all(row["decision"] == "keep" for row in kept["decisions"])
        assert all(row["validity"] == "current" for row in kept["decisions"])
        replay = await api.post(
            f"/tasks/{task}/prototype-decisions", headers=header, json=keep_body
        )
        assert replay.status_code == 200 and replay.json()["data"]["duplicate"] is True
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert (
                await session.scalar(select(func.count()).select_from(PrototypeDecisionBatch)) == 1
            )

        stale_body = decision_input(
            preview, extraction, feature["id"], decision="replace", reason="Synthetic change"
        )
        stale = await api.post(
            f"/tasks/{task}/prototype-decisions", headers=header, json=stale_body
        )
        assert stale.status_code == 409
        assert stale.json()["data"]["error"]["code"] == "prototype_decision_stale"

        second_preview = (
            await api.post(
                f"/tasks/{task}/prototype-decisions/preview",
                headers=header,
                json=preview_body,
            )
        ).json()["data"]
        assert {row["expected_previous_decision_id"] for row in second_preview["targets"]} == {
            row["id"] for row in kept["decisions"]
        }
        missing_reason = decision_input(
            second_preview, extraction, feature["id"], decision="replace"
        )
        no_reason = await api.post(
            f"/tasks/{task}/prototype-decisions", headers=header, json=missing_reason
        )
        assert no_reason.status_code == 422
        replaced_response = await api.post(
            f"/tasks/{task}/prototype-decisions",
            headers=header,
            json=decision_input(
                second_preview,
                extraction,
                feature["id"],
                decision="replace",
                reason="Synthetic reviewed replacement is pending",
            ),
        )
        assert replaced_response.status_code == 200, replaced_response.text
        replaced = replaced_response.json()["data"]
        assert len(replaced["decisions"]) == 2
        assert all(row["decision"] == "replace" for row in replaced["decisions"])
        assert all(row["keep_basis"] is None for row in replaced["decisions"])

        listed = await api.get(
            f"/tasks/{task}/prototype-decisions", headers=header, params={"job": extraction}
        )
        assert listed.status_code == 200, listed.text
        views = listed.json()["items"]
        assert [row["validity"] for row in views].count("superseded") == 2
        assert [row["validity"] for row in views].count("current") == 2
        assert (
            await api.get(
                f"/tasks/{task}/prototype-decisions",
                headers=headers[1],
                params={"job": extraction},
            )
        ).status_code == 404
        assert (
            await api.post(
                f"/tasks/{task}/prototype-decisions",
                headers=headers[1],
                json=decision_input(
                    second_preview,
                    extraction,
                    feature["id"],
                    decision="replace",
                    reason="Foreign decision must be hidden",
                ),
            )
        ).status_code == 404

        reopened = await require_action(
            api,
            header,
            card,
            "reopen",
            reason="Synthetic prototype card changed after the module decision.",
        )
        assert reopened["state"] == "draft"
        after_reopen = await api.get(
            f"/tasks/{task}/prototype-decisions", headers=header, params={"job": extraction}
        )
        validity = {row["id"]: row["validity"] for row in after_reopen.json()["items"]}
        assert all(validity[row["id"]] == "superseded" for row in kept["decisions"])
        replacement_by_evidence = {
            row["target"]["evidence_id"]: row for row in replaced["decisions"]
        }
        assert validity[replacement_by_evidence[evidence["id"]]["id"]] == "stale"
        assert validity[replacement_by_evidence[second_evidence["id"]]["id"]] == "current"
        artifact(
            {
                "task_id": task,
                "prototype_run_id": str(prototype["id"]),
                "keep": kept["decisions"][0],
                "replace": replaced["decisions"][0],
                "after_reopen": validity,
            }
        )
