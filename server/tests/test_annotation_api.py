"""B05 end-to-end failure inventory, fixed before service implementation.

A read-only preview must never create state or paid usage. Wrong org/task/card,
source hash, revision, or replay payload must not mutate a card. Only active human
owner/contributors prepare/attach; complete B02 and domain/co-sign approval alone
admits a release. Source/pin/review/authority changes fence publication and reads.
Cancelled, expired, corrupt, or oversized work publishes no material. Retried fixed
inputs yield the same bytes. Neither legacy screenshot routes nor exports can strip
or bypass the fixed status footer. Artifacts contain synthetic IDs/hashes only.
"""

import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.models.entities import Job, UsageRecord, VendorCall
from sqlalchemy import func, select, text
from test_response_cards import (
    create_card,
    create_tender,
    phase_one_client,
    require_action,
    select_real_materials,
    set_role,
)

ARTIFACT = Path(__file__).resolve().parents[2] / "data/work/annotation-acceptance"


async def seed_annotation(api, app, headers, tmp_path, *, confirmed=False):
    task, _, extraction, requirements = await create_tender(
        api, app, headers, tmp_path, confirmed=confirmed
    )
    _, _, _, selected, source = await select_real_materials(api, headers, task, tmp_path)
    requirement = next(r for r in requirements if r["category"] == "qualification")
    card = await create_card(
        api,
        headers,
        task,
        extraction,
        requirement,
        {
            "response_kind": "evidence",
            "response_text": "The certificate page supports this response.",
            "deviation": "none",
            "deviation_note": "The shown certificate corresponds to the requested qualification.",
            "evidence": [],
        },
    )
    target = {
        "extraction_job_id": extraction,
        "card_id": card["id"],
        "expected_card_revision": card["revision"],
        "source": {"kind": "certificate_page", "evidence_source_id": source["id"]},
        "plan": {
            "crop": {"x": 0, "y": 0, "width": 200, "height": 120},
            "boxes": [{"x": 2, "y": 2, "width": 50, "height": 30}],
        },
    }
    return task, card, source, selected, target


async def preview_submit(api, auth, task, target):
    preview = await api.post(
        f"/v4/tasks/{task}/annotations", headers=auth, json={"dry_run": True, "input": target}
    )
    assert preview.status_code == 200, preview.text
    value = preview.json()["data"]
    body = {
        "input": target,
        "request_id": str(uuid4()),
        "expected_input_hash": value["input_hash"],
        "reviewed_source_png_sha256": value["manifest"]["source"]["source_png"]["sha256"],
    }
    submitted = await api.post(f"/v4/tasks/{task}/annotations", headers=auth, json=body)
    assert submitted.status_code == 200, submitted.text
    return value, body, submitted.json()["data"]


@pytest.mark.parametrize("real_renderer", [False, True])
async def test_annotation_candidate_human_review_release_and_invalidation(
    tenants, tmp_path, admin_engine, monkeypatch, real_renderer
):
    from annotation_test_renderer import configure_renderer

    configure_renderer(monkeypatch, real=real_renderer)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        original_transaction = app.state.db.transaction

        @asynccontextmanager
        async def non_utc_transaction(org_id=None):
            # A new DB read must not change the pin created from the confirming
            # session's UTC datetime, even when the connection returns local time.
            async with original_transaction(org_id) as session:
                await session.execute(
                    text("SELECT set_config('TimeZone', 'America/Los_Angeles', true)")
                )
                yield session

        monkeypatch.setattr(app.state.db, "transaction", non_utc_transaction)
        task, card, source, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        preview, body, receipt = await preview_submit(api, headers[0], task, target)
        assert preview["budget_preflight"]["planned_calls"] == 0
        assert preview["budget_preflight"]["admission_blocker"] is None
        duplicate = await api.post(f"/v4/tasks/{task}/annotations", headers=headers[0], json=body)
        assert duplicate.json()["data"]["job_id"] == receipt["job_id"]
        assert duplicate.json()["data"]["duplicate"]
        changed = await api.post(
            f"/v4/tasks/{task}/annotations",
            headers=headers[0],
            json={**body, "reviewed_source_png_sha256": "0" * 64},
        )
        assert changed.status_code == 409
        await app.state.processor(str(tenants["orgs"][0]), receipt["job_id"])
        status = await api.get(f"/v4/jobs/{receipt['job_id']}", headers=headers[0])
        assert status.json()["data"]["status"] == "succeeded", status.text
        ident = status.json()["data"]["result"]["annotation_id"]
        shown = await api.get(f"/v4/annotations/{ident}", headers=headers[0])
        candidate = shown.json()["data"]["candidate"]
        assert candidate["status"] == "unconfirmed_material" and candidate["confirmed_by"] is None
        assert not candidate["eligible_for_draft_export"]
        unchanged = (await api.get(f"/cards/{card['id']}", headers=headers[0])).json()["data"]
        assert unchanged["revision"] == card["revision"] and not unchanged["evidence"]
        linked = await api.get(f"/v4/annotations/{ident}/preview", headers=headers[0])
        png = await api.get(linked.json()["data"]["url"], headers=headers[0])
        assert hashlib.sha256(png.content).hexdigest() == candidate["rendering"]["image"]["sha256"]
        attachment = {
            "kind": "image_region",
            "asset_id": candidate["asset_id"],
            "rendition_id": candidate["rendition_id"],
            "expected_image_sha256": candidate["rendering"]["image"]["sha256"],
            "region": {"x": 1, "y": 1, "width": 50, "height": 30},
            "claim_scope": "document_excerpt",
            "visual_observation": "I inspected this archived certificate region.",
        }
        updated = await api.put(
            f"/cards/{card['id']}",
            headers=headers[0],
            json={
                "expected_revision": card["revision"],
                "content": {**card["content"], "evidence": [attachment]},
            },
        )
        assert updated.status_code == 200, updated.text
        card = await require_action(api, headers[0], updated.json()["data"], "submit")
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
        action = {
            "expected_revision": card["revision"],
            "action": "confirm",
            "reviewed_evidence_ids": [e["id"] for e in card["evidence"]],
            "reviewed_warning_codes": card["warning_codes"],
            "reason": "Inspected every source pixel and warning.",
        }
        blocked = await api.post(f"/cards/{card['id']}/actions", headers=headers[0], json=action)
        assert blocked.status_code == 409
        from test_requirement_confirmation import decision, post_decision, review

        current = await review(api, headers[0], card["requirement_id"])
        confirmed = await post_decision(api, headers[0], card["requirement_id"], decision(current))
        assert confirmed.status_code == 200, confirmed.text
        accepted = await api.post(f"/cards/{card['id']}/actions", headers=headers[0], json=action)
        assert accepted.status_code == 200, accepted.text
        approved_card = accepted.json()["data"]
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            release_job = await session.scalar(
                select(Job).where(Job.task_id == UUID(task), Job.kind == "annotation_release")
            )
            assert release_job is not None
            release_job_id = str(release_job.id)
            submitted_approval = release_job.result["submission"]["approval"]
            assert submitted_approval["confirmed_at"].endswith("Z")
        await app.state.processor(str(tenants["orgs"][0]), release_job_id)
        release_status = await api.get(f"/v4/jobs/{release_job_id}", headers=headers[0])
        assert release_status.json()["data"]["status"] == "succeeded", release_status.text
        releases = await api.get(f"/v4/annotations/{ident}/releases", headers=headers[0])
        release = releases.json()["items"][0]
        assert release["releasable"] and release["decision_validity"] == "current"
        assert release["approval"] == submitted_approval
        assert (
            release["rendering"]["content_pixel_sha256"]
            == candidate["rendering"]["content_pixel_sha256"]
        )
        assert approved_card["evidence"][0]["screenshot_rendition_id"] == candidate["rendition_id"]
        assert (
            approved_card["evidence"][0]["image_sha256"]
            == candidate["rendering"]["image"]["sha256"]
        )
        release_link = await api.get(
            f"/v4/annotation-releases/{release['id']}/preview", headers=headers[0]
        )
        assert release_link.status_code == 200, release_link.text
        released_png = await api.get(release_link.json()["data"]["url"], headers=headers[0])
        assert (
            hashlib.sha256(released_png.content).hexdigest()
            == release["rendering"]["image"]["sha256"]
        )
        release_retry = {
            "evidence_id": release["approval"]["evidence_id"],
            "card_id": card["id"],
            "expected_card_revision": approved_card["revision"],
            "expected_approval_binding_hash": release["approval"]["approval_binding_sha256"],
            "request_id": str(uuid4()),
            "job_id": release_job_id,
            "retry": True,
        }
        for method, path, payload in (
            ("GET", f"/v4/tasks/{task}/annotations", None),
            ("POST", f"/v4/tasks/{task}/annotations", {"dry_run": True, "input": target}),
            ("POST", f"/v4/tasks/{task}/annotations", body),
            ("GET", f"/v4/annotations/{ident}", None),
            ("GET", f"/v4/annotations/{ident}/preview", None),
            ("GET", linked.json()["data"]["url"], None),
            ("GET", f"/v4/annotations/{ident}/releases", None),
            ("POST", f"/v4/annotations/{ident}/releases", release_retry),
            ("GET", release_link.json()["data"]["url"], None),
            (
                "GET",
                f"/v4/tasks/{task}/jobs?kind=annotation_release&annotation_id={ident}&limit=20",
                None,
            ),
            ("GET", f"/v4/annotation-releases/{release['id']}/preview", None),
            ("GET", f"/v4/jobs/{receipt['job_id']}", None),
            ("POST", f"/v4/jobs/{release_job_id}/cancel", None),
        ):
            denied = await api.request(method, path, headers=headers[1], json=payload)
            assert denied.status_code == 404, (path, denied.text)
        for parent in (candidate, release):
            legacy = await api.post(
                f"/screenshots/{parent['asset_id']}/renditions",
                headers=headers[0],
                json={
                    "parent_rendition_id": parent["rendition_id"],
                    "expected_image_sha256": parent["rendering"]["image"]["sha256"],
                    "plan": {},
                    "dry_run": True,
                },
            )
            assert legacy.status_code == 400, legacy.text
        for url in (linked.json()["data"]["url"], release_link.json()["data"]["url"]):
            tampered = await api.get(url + "changed", headers=headers[0])
            assert tampered.status_code == 404
        reopened = await require_action(
            api, headers[0], approved_card, "reopen", reason="Review again."
        )
        assert reopened["state"] != "confirmed"
        stale = await api.get(
            f"/v4/annotation-releases/{release['id']}/preview", headers=headers[0]
        )
        assert stale.status_code == 409
        bypass = await api.post(
            f"/screenshot-renditions/{release['rendition_id']}/preview-link", headers=headers[0]
        )
        assert bypass.status_code == 409
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            for model in (VendorCall, UsageRecord):
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.job_id.in_([UUID(receipt["job_id"]), UUID(release_job_id)]))
                    )
                    == 0
                )
        ARTIFACT.mkdir(parents=True, exist_ok=True)
        (ARTIFACT / f"{'real' if real_renderer else 'fake'}-candidate.png").write_bytes(png.content)
        (ARTIFACT / f"{'real' if real_renderer else 'fake'}-flow.json").write_text(
            json.dumps(
                {
                    "candidate": candidate,
                    "release": release,
                    "original_sha256": source["original"]["sha256"],
                    "replay": "uv run pytest server/tests/test_annotation_api.py -q",
                },
                indent=2,
            )
            + "\n"
        )
