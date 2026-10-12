"""Retained single-human response and draft transaction acceptance."""

import json
from pathlib import Path

import pytest
from response_review_fixtures import attach_material, confirm, response_scope, submit

ARTIFACT_ROOT = Path(__file__).resolve().parents[2] / "data/work/response-review-acceptance"


async def test_single_domain_disposition_after_reopen_preserves_board_and_draft(
    api, headers, tenants, admin_engine
):
    scope = await response_scope(api, headers, tenants, admin_engine, required=False)
    await submit(api, headers[0], scope)
    tech = scope["members"]["technical"]["headers"]
    card = scope["card"]
    confirmed = await api.post(
        f"/cards/{card['id']}/actions",
        headers=tech,
        json={
            "expected_revision": card["revision"],
            "action": "confirm",
            "reviewed_warning_codes": card["warning_codes"],
            "reason": "Synthetic single-domain review",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    reopened = await api.post(
        f"/cards/{card['id']}/actions",
        headers=tech,
        json={
            "expected_revision": confirmed.json()["data"]["revision"],
            "action": "reopen",
            "reason": "Synthetic disposition edit",
        },
    )
    assert reopened.status_code == 200, reopened.text
    disposed = await api.post(
        f"/tasks/{scope['task_id']}/cards/dispositions",
        headers=tech,
        json={
            "extraction_job_id": str(scope["job_id"]),
            "items": [
                {
                    "requirement_id": str(scope["requirement_ids"][0]),
                    "expected_revision": reopened.json()["data"]["revision"],
                    "disposition": "comply_only",
                    "reason": "Synthetic comply-only decision",
                }
            ],
        },
    )
    assert disposed.status_code == 200, disposed.text
    board = await api.get(
        f"/tasks/{scope['task_id']}/board",
        headers=headers[0],
        params={"extraction_job_id": str(scope["job_id"])},
    )
    assert board.status_code == 200, board.text
    assert board.json()["items"][0]["bucket"] == "comply_only"
    preview = await api.post(
        f"/tasks/{scope['task_id']}/drafts",
        headers=headers[0],
        json={"extraction_job_id": str(scope["job_id"]), "dry_run": True},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["comply_only_requirements"] == 1


@pytest.mark.parametrize("fail_after_validation", [False, True])
async def test_human_confirmation_and_consumption_commit_atomically(
    api,
    application,
    headers,
    tenants,
    admin_engine,
    tmp_path,
    monkeypatch,
    fail_after_validation,
):
    from app.core.errors import ServiceError
    from app.services import drafts, response_cards

    scope = await response_scope(api, headers, tenants, admin_engine)
    await attach_material(api, headers[0], scope, tmp_path)
    await submit(api, headers[0], scope)
    pending = scope["card"]
    assert pending["evidence"] and all(row["confirmed_by"] is None for row in pending["evidence"])
    validate = response_cards.validate_confirmation
    observations = []

    async def observe_final_recheck(session, actor, card, revision, requirement, body, storage):
        await validate(session, actor, card, revision, requirement, body, storage)
        assert revision.state == "pending_review" and card.current_revision_id == revision.id
        evidence = await response_cards.linked_evidence(session, revision.id)
        assert all(row.confirmed_by is None and row.confirmed_at is None for row in evidence)
        _, items, _, _, _ = await drafts.assemble(
            session, actor, card.task_id, card.extraction_job_id, storage
        )
        assert len(items) == 1 and items[0]["kind"] == "gap"
        assert items[0]["gap_reasons"] == ["unconfirmed"]
        assert "response_text" not in items[0]
        observations.append({"state": revision.state, "partition": "gap"})
        if fail_after_validation:
            raise ServiceError(
                "synthetic_final_review_failure", "Synthetic final review interruption", 409, 2
            )

    monkeypatch.setattr(response_cards, "validate_confirmation", observe_final_recheck)
    final = await confirm(api, scope)
    assert len(observations) == 1
    current = await api.get(f"/cards/{pending['id']}", headers=headers[0])
    assert current.status_code == 200
    card = current.json()["data"]
    if fail_after_validation:
        assert final.status_code == 409, final.text
        assert final.json()["data"]["error"]["code"] == "synthetic_final_review_failure"
        assert card["revision"] == pending["revision"] and card["state"] == "pending_review"
        assert all(row["confirmed_by"] is None for row in card["evidence"])
    else:
        assert final.status_code == 200, final.text
        assert card["revision"] == pending["revision"] + 1 and card["state"] == "confirmed"
        assert card["confirmed_by"] == str(scope["members"]["technical"]["user"])
        assert all(row["confirmed_by"] == card["confirmed_by"] for row in card["evidence"])
    target = ARTIFACT_ROOT / "confirmation-boundary"
    target.mkdir(parents=True, exist_ok=True)
    (target / (("rollback" if fail_after_validation else "commit") + ".json")).write_text(
        json.dumps(
            {
                "command": ".venv/bin/pytest -q server/tests/test_response_review_consumers.py -k commit_atomically",
                "observations": observations,
                "final_http_status": final.status_code,
                "confirmed_evidence": sum(
                    row["confirmed_by"] is not None for row in card["evidence"]
                ),
            },
            indent=2,
        )
        + "\n"
    )
