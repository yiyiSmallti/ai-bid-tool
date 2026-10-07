"""Database/API B05 consumption acceptance; no external renderer or converter.

Failure cases: a partial co-sign confirms Evidence or admits a release; a task
reviewer without annotation creation authority cannot run the authorized final
release; draft loses the canonical candidate binding; either export mode embeds
UNCONFIRMED bytes; archival destroys readable history or permits writes; signer
revocation leaves a release, old signed DOCX link or export preflight usable.

The common workflow is repeated independently for signer loss, explicit source
selection replacement, B02 reopening and same-candidate card relinking. Metadata,
canonical Evidence and byte exports are checked separately after each mutation.
"""

import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile

import pytest
from app.models.annotations import AnnotationRelease
from app.models.entities import Job
from app.models.response_cards import Evidence
from sqlalchemy import func, select, text
from test_annotation_api import preview_submit, seed_annotation
from test_check import publish_draft
from test_export_images import commitment
from test_exports import prepared, setup_template
from test_response_cards import phase_one_client, require_action
from test_team_cosign_consumers import signature, submit
from test_team_workflow_membership import add_member, person, workflow

ARTIFACTS = Path(__file__).resolve().parents[2] / "data/work/annotation-acceptance"


@pytest.mark.parametrize(
    "mutation", ["signer_loss", "source_replacement", "b02_reopen", "card_relink"]
)
async def test_cosign_release_exports_and_dependency_invalidation(
    tenants, tmp_path, admin_engine, monkeypatch, mutation
):
    from annotation_test_renderer import configure_renderer

    configure_renderer(monkeypatch, real=False)
    org = tenants["orgs"][0]
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        owner = headers[0]
        task, card, source, selected_source, target = await seed_annotation(
            api, app, owner, tmp_path, confirmed=True
        )
        extraction = target["extraction_job_id"]
        members = {}
        for domain, org_role, task_role in (
            ("commercial", "bidder", "contributor"),
            ("technical", "technical", "reviewer"),
        ):
            user, auth = await person(api, admin_engine, org, org_role)
            current = await workflow(api, owner, task)
            added = await add_member(
                api, owner, task, user, current["revision"], task_role, [domain]
            )
            assert added.status_code == 200, added.text
            members[domain] = {"user": user, "headers": auth}
        bidder = members["commercial"]["headers"]
        reviewer = members["technical"]["headers"]
        scope = {"card": card, "members": members}
        policy = await api.put(
            f"/tasks/{task}/requirements/{card['requirement_id']}/review-policy",
            headers=owner,
            params={"extraction_job_id": extraction},
            json={
                "expected_policy_revision": 0,
                "co_sign_required": True,
                "reason": "Both domains inspect this certificate page.",
            },
        )
        assert policy.status_code == 200, policy.text
        denied = await api.post(
            f"/v4/tasks/{task}/annotations",
            headers=reviewer,
            json={"dry_run": True, "input": target},
        )
        assert denied.status_code == 403, denied.text
        _, _, receipt = await preview_submit(api, owner, task, target)
        await app.state.processor(str(org), receipt["job_id"])
        job = await api.get(f"/v4/jobs/{receipt['job_id']}", headers=owner)
        assert job.json()["data"]["status"] == "succeeded", job.text
        material_id = job.json()["data"]["result"]["annotation_id"]
        response = await api.get(f"/v4/annotations/{material_id}", headers=owner)
        candidate = response.json()["data"]["candidate"]
        attachment = {
            "kind": "image_region",
            "asset_id": candidate["asset_id"],
            "rendition_id": candidate["rendition_id"],
            "expected_image_sha256": candidate["rendering"]["image"]["sha256"],
            "region": {"x": 1, "y": 1, "width": 40, "height": 20},
            "claim_scope": "document_excerpt",
            "visual_observation": "The certificate number is legible.",
        }
        attached = await api.put(
            f"/cards/{card['id']}",
            headers=owner,
            json={
                "expected_revision": card["revision"],
                "content": {**card["content"], "evidence": [attachment]},
            },
        )
        assert attached.status_code == 200, attached.text
        scope["card"] = attached.json()["data"]
        # Fill the other tender rows so final export has no unrelated gaps.
        requirements = (
            await api.get(f"/tasks/{task}/requirements", headers=owner, params={"job": extraction})
        ).json()["items"]
        for index, requirement in enumerate(requirements):
            if requirement["id"] == card["requirement_id"]:
                continue
            other = await commitment(api, owner, task, extraction, requirement, index)
            if other["review_domain"] is None:
                classified = await api.post(
                    f"/cards/{other['id']}/classification",
                    headers=owner,
                    json={
                        "expected_revision": other["revision"],
                        "review_domain": "commercial",
                        "reason": "Commercial reviewer owns this unclassified commitment.",
                    },
                )
                assert classified.status_code == 200, classified.text
                other = classified.json()["data"]
            submitted = await require_action(api, owner, other, "submit")
            await require_action(
                api,
                members[submitted["review_domain"]]["headers"],
                submitted,
                "confirm",
                reviewed_evidence_ids=[],
                reviewed_warning_codes=submitted["warning_codes"],
                reason="The assigned domain reviewed this synthetic commitment.",
            )
        await submit(api, owner, scope)
        first = await signature(api, scope, "commercial")
        assert first.status_code == 200, first.text
        assert first.json()["data"]["summary"]["status"] == "partial"
        async with app.state.db.transaction(org) as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.task_id == UUID(task), Job.kind == "annotation_release")
                )
                == 0
            )
            evidence = await db.scalar(select(Evidence).where(Evidence.card_id == UUID(card["id"])))
            assert evidence is not None and evidence.confirmed_by is None
        partial = await publish_draft(api, app, bidder, task, extraction)
        partial_view = (await api.get(f"/drafts/{partial['draft_id']}", headers=bidder)).json()[
            "data"
        ]
        assert partial_view["completion"] == "partial"
        assert any("cosign_required" in gap["reasons"] for gap in partial_view["gaps"])
        final = await signature(api, scope, "technical")
        assert final.status_code == 200, final.text
        assert final.json()["data"]["summary"]["status"] == "complete"
        async with app.state.db.transaction(org) as db:
            release_job = await db.scalar(
                select(Job).where(Job.task_id == UUID(task), Job.kind == "annotation_release")
            )
            assert release_job is not None
            assert release_job.actor_user_id == members["technical"]["user"]
            release_job_id = release_job.id
        # Confirmation can feed a draft immediately, but both image export modes
        # must wait for the immutable CONFIRMED rendition.
        pending_draft = await publish_draft(api, app, bidder, task, extraction)
        selected, binding = await setup_template(api, owner, task)
        for pending_mode in ("review_copy", "final_section"):
            pending_export = await api.post(
                f"/tasks/{task}/export-runs",
                headers=bidder,
                json={
                    "draft_id": pending_draft["draft_id"],
                    "task_template_id": selected["id"],
                    "binding_id": binding["id"],
                    "mode": pending_mode,
                    "dry_run": True,
                },
            )
            assert pending_export.status_code in {400, 409}, pending_export.text
            assert "annotation_release_pending" in pending_export.text
        await app.state.processor(str(org), str(release_job_id))
        completed = await api.get(f"/v4/jobs/{release_job_id}", headers=owner)
        assert completed.json()["data"]["status"] == "succeeded", completed.text
        releases = await api.get(f"/v4/annotations/{material_id}/releases", headers=owner)
        release = releases.json()["items"][0]
        assert release["releasable"] and release["approval"]["decision_kind"] == "cosign"
        assert len(release["approval"]["co_sign"]["signatures"]) == 2
        assert release["approval"]["confirmed_by"] == str(members["technical"]["user"])
        draft = await publish_draft(api, app, bidder, task, extraction)
        draft_view = (await api.get(f"/drafts/{draft['draft_id']}", headers=bidder)).json()["data"]
        assert draft_view["completion"] == "complete"
        async with app.state.db.transaction(org) as db:
            evidence = await db.scalar(select(Evidence).where(Evidence.card_id == UUID(card["id"])))
            assert str(evidence.screenshot_rendition_id) == candidate["rendition_id"]
            assert evidence.image_sha256 == candidate["rendering"]["image"]["sha256"]
            assert await db.scalar(select(func.count()).select_from(AnnotationRelease)) == 1
        exports = []
        evidence_artifact = []
        exported_files = {}
        for mode in ("review_copy", "final_section"):
            body = {
                "draft_id": draft["draft_id"],
                "task_template_id": selected["id"],
                "binding_id": binding["id"],
                "mode": mode,
            }
            run = await prepared(api, app, bidder, task, body)
            published = await api.post(
                f"/export-runs/{run['id']}/release",
                headers=bidder,
                json={
                    "expected_input_hash": run["input_hash"],
                    "expected_candidate_sha256": run["candidate_sha256"],
                },
            )
            assert published.status_code == 200, published.text
            export = published.json()["data"]
            link = await api.get(f"/exports/{export['id']}/download-link", headers=bidder)
            assert link.status_code == 200, link.text
            signed = link.json()["data"]["url"]
            downloaded = await api.get(signed, headers=bidder)
            assert downloaded.status_code == 200, downloaded.text
            with ZipFile(BytesIO(downloaded.content)) as package:
                images = [
                    hashlib.sha256(package.read(name)).hexdigest()
                    for name in package.namelist()
                    if name.startswith("word/media/")
                ]
            assert images == [release["rendering"]["image"]["sha256"]]
            assert candidate["rendering"]["image"]["sha256"] not in images
            exports.append((export["id"], signed, body))
            exported_files[mode] = downloaded.content
            evidence_artifact.append(
                {
                    "mode": mode,
                    "export_id": export["id"],
                    "embedded_png_sha256": images[0],
                    "docx_sha256": hashlib.sha256(downloaded.content).hexdigest(),
                }
            )
        state = await workflow(api, owner, task)
        archived = await api.post(
            f"/tasks/{task}/archive",
            headers=owner,
            json={
                "expected_revision": state["revision"],
                "reason": "Retain finished review history.",
            },
        )
        assert archived.status_code == 200, archived.text
        history = await api.get(f"/v4/annotations/{material_id}/releases", headers=owner)
        assert history.status_code == 200 and history.json()["items"][0]["id"] == release["id"]
        blocked = await api.post(
            f"/v4/tasks/{task}/annotations", headers=owner, json={"dry_run": True, "input": target}
        )
        assert blocked.status_code == 409, blocked.text
        restored = await api.post(
            f"/tasks/{task}/unarchive",
            headers=owner,
            json={
                "expected_revision": archived.json()["data"]["revision"],
                "reason": "Inspect access revocation.",
            },
        )
        assert restored.status_code == 200, restored.text
        await _invalidate(
            api, owner, bidder, task, scope, source, selected_source, attachment, release, mutation
        )
        stale_image = await api.get(
            f"/v4/annotation-releases/{release['id']}/preview", headers=bidder
        )
        assert stale_image.status_code == 409, stale_image.text
        for export_id, signed, body in exports:
            old_download = await api.get(signed, headers=bidder)
            assert old_download.status_code == 409, old_download.text
            new_link = await api.get(f"/exports/{export_id}/download-link", headers=bidder)
            assert new_link.status_code == 409, new_link.text
            preview = await api.post(
                f"/tasks/{task}/export-runs", headers=bidder, json={**body, "dry_run": True}
            )
            assert preview.status_code in {400, 409}, preview.text
        async with app.state.db.transaction(org) as db:
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM audit_logs WHERE action='annotation.invalidate' AND object_id=:id"
                    ),
                    {"id": UUID(release["id"])},
                )
                == 1
            )
        await asyncio.to_thread(
            _artifact,
            {
                "mutation": mutation,
                "candidate_id": candidate["id"],
                "release_id": release["id"],
                "exports": evidence_artifact,
                "partial_blocked": True,
                "reviewer_release_succeeded": True,
                "revoked_links_blocked": True,
            },
            exported_files,
        )


async def _invalidate(
    api, owner, bidder, task, scope, source, selected_source, attachment, release, mutation
):
    if mutation == "signer_loss":
        current = await workflow(api, owner, task)
        changed = await api.post(
            f"/tasks/{task}/members/{scope['members']['technical']['user']}/remove",
            headers=owner,
            json={
                "expected_revision": current["revision"],
                "reason": "Retire this signer authority.",
            },
        )
    elif mutation == "source_replacement":
        current = await api.get(
            "/resources/certificates",
            headers=owner,
            params={"certificate_id": source["certificate_id"]},
        )
        assert current.status_code == 200, current.text
        certificate = current.json()["items"][0]
        revised = await api.post(
            f"/resources/certificates/{source['certificate_id']}/revisions",
            headers=owner,
            json={
                "expected_revision": certificate["revision"],
                "data": {**certificate["data"], "name": "Explicitly replaced certificate."},
            },
        )
        assert revised.status_code == 200, revised.text
        # A newer library revision alone cannot invalidate the still-pinned page.
        retained = await api.get(f"/v4/annotation-releases/{release['id']}/preview", headers=bidder)
        assert retained.status_code == 200, retained.text
        changed = await api.post(
            f"/tasks/{task}/certificates",
            headers=owner,
            json={"certificate_id": source["certificate_id"]},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["data"]["replaced_snapshot_id"] == selected_source["id"]
    elif mutation == "b02_reopen":
        from test_requirement_confirmation import decision, post_decision, review

        current = await review(api, owner, scope["card"]["requirement_id"])
        changed = await post_decision(
            api, owner, scope["card"]["requirement_id"], decision(current, "reopen")
        )
    else:
        current = await api.get(f"/cards/{scope['card']['id']}", headers=owner)
        assert current.status_code == 200, current.text
        previous = current.json()["data"]
        reopened = await require_action(
            api, bidder, previous, "reopen", reason="Relink the same candidate for a new review."
        )
        changed = await api.put(
            f"/cards/{previous['id']}",
            headers=owner,
            json={
                "expected_revision": reopened["revision"],
                "content": {**previous["content"], "evidence": [attachment]},
            },
        )
        assert changed.status_code == 200, changed.text
        replacement = changed.json()["data"]["evidence"][0]
        assert replacement["id"] != previous["evidence"][0]["id"]
        assert replacement["screenshot_rendition_id"] == attachment["rendition_id"]
        assert replacement["confirmed_by"] is None
    assert changed.status_code == 200, changed.text


def _artifact(value, exported_files):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    mutation = value["mutation"]
    for mode, content in exported_files.items():
        (ARTIFACTS / f"consumption-{mutation}-{mode}.docx").write_bytes(content)
    (ARTIFACTS / f"cosign-export-consumption-{mutation}.json").write_text(
        json.dumps(
            {"command": "pytest server/tests/test_annotation_consumption.py -q", **value}, indent=2
        )
        + "\n"
    )
