"""Slice-three API/consumer failure scenarios, specified before implementation.

Partial signatures never confirm Evidence or response revisions and become draft
(and consequently check/score) gaps. Both domain orders and simultaneous signers
complete exactly once. Revisions racing signatures either retire the round or
reject the revision; stale decisions cannot leak partial writes. Reopen retires
all signatures, including when historical Evidence keeps its confirmation flag.
Policy, citation, material and actual signer-authority changes retire approvals;
unrelated assignment/comments/member edits must not. Co-sign disposition batches
are atomic and cannot exempt unsigned requirements. Legacy single-domain decisions
and prototype/export hard gates retain their existing behavior.
"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from test_team_workflow_membership import add_member, person
from test_team_workflow_stream_acceptance import seed_scope

ARTIFACT_ROOT = Path(__file__).resolve().parents[2] / "data/work/team-workflow-acceptance/cosign"


async def cosign_scope(api, headers, tenants, admin_engine, *, required=True, count=1):
    scope = await seed_scope(api, headers, tenants, admin_engine, count=count)
    members = {}
    for index, (domain, role) in enumerate((("commercial", "bidder"), ("technical", "technical"))):
        user, auth = await person(api, admin_engine, tenants["orgs"][0], role)
        result = await add_member(
            api, headers[0], scope["task_id"], user, index + 1, "contributor", [domain]
        )
        assert result.status_code == 200, result.text
        members[domain] = {"user": user, "headers": auth}
    scope["members"] = members
    if required:
        response = await api.put(
            f"/tasks/{scope['task_id']}/requirements/{scope['requirement_ids'][0]}/review-policy",
            headers=headers[0],
            params={"extraction_job_id": str(scope["job_id"])},
            json={
                "expected_policy_revision": 0,
                "co_sign_required": True,
                "reason": "Synthetic co-sign policy",
            },
        )
        assert response.status_code == 200, response.text
    created = await api.post(
        f"/tasks/{scope['task_id']}/cards",
        headers=headers[0],
        json={
            "extraction_job_id": str(scope["job_id"]),
            "requirement_id": str(scope["requirement_ids"][0]),
            "content": {
                "response_kind": "commitment",
                "response_text": "Synthetic delivery commitment",
                "deviation": "negative",
                "deviation_note": "Synthetic one-day delivery difference",
            },
        },
    )
    assert created.status_code == 200, created.text
    scope["card"] = created.json()["data"]
    return scope


async def submit(api, headers, scope):
    card = scope["card"]
    response = await api.post(
        f"/cards/{card['id']}/actions",
        headers=headers,
        json={"expected_revision": card["revision"], "action": "submit"},
    )
    assert response.status_code == 200, response.text
    scope["card"] = response.json()["data"]
    return scope["card"]


async def signature(api, scope, domain, **changes):
    card = scope["card"]
    listing = await api.get(
        f"/cards/{card['id']}/signoffs", headers=scope["members"][domain]["headers"]
    )
    assert listing.status_code == 200, listing.text
    round_view = listing.json()["data"]["round"]
    body = {
        "purpose": "response",
        "action": "confirm",
        "expected_revision": card["revision"],
        "expected_round": round_view["round_revision"],
        "selected_domain": domain,
        "reviewed_evidence_ids": [e["id"] for e in card["evidence"]],
        "reviewed_warning_codes": card["warning_codes"],
        "reason": "Synthetic warning review",
        "client_request_id": str(uuid4()),
        **changes,
    }
    return await api.post(
        f"/cards/{card['id']}/signoffs", headers=scope["members"][domain]["headers"], json=body
    )


@pytest.mark.parametrize("order", [("commercial", "technical"), ("technical", "commercial")])
async def test_domain_orders_partial_gap_complete_and_reopen(
    api, headers, tenants, admin_engine, order
):
    scope = await cosign_scope(api, headers, tenants, admin_engine)
    await submit(api, headers[0], scope)
    first = await signature(api, scope, order[0])
    assert first.status_code == 200, first.text
    assert first.json()["data"]["summary"]["status"] == "partial"
    gap = await api.post(
        f"/tasks/{scope['task_id']}/drafts",
        headers=scope["members"]["commercial"]["headers"],
        json={"extraction_job_id": str(scope["job_id"]), "dry_run": True},
    )
    assert gap.status_code == 200, gap.text
    assert gap.json()["data"]["response_requirements"] == 0
    second = await signature(api, scope, order[1])
    assert second.status_code == 200, second.text
    result = second.json()["data"]
    assert result["summary"]["status"] == "complete"
    assert result["current_card_revision"] == scope["card"]["revision"] + 1
    reopened = await api.post(
        f"/cards/{scope['card']['id']}/actions",
        headers=scope["members"]["technical"]["headers"],
        json={
            "expected_revision": result["current_card_revision"],
            "action": "reopen",
            "reason": "Synthetic new review",
        },
    )
    assert reopened.status_code == 200, reopened.text
    state = (await api.get(f"/cards/{scope['card']['id']}/signoffs", headers=headers[0])).json()
    assert state["data"]["summary"]["status"] == "invalidated"
    assert state["data"]["summary"]["signed_domains"] == []
    assert len(state["items"]) == 2
    target = ARTIFACT_ROOT
    target.mkdir(parents=True, exist_ok=True)
    (target / ("-".join(order) + ".json")).write_text(
        json.dumps(
            {
                "task_id": str(scope["task_id"]),
                "card_id": scope["card"]["id"],
                "summary": state["data"]["summary"],
                "command": "uv run pytest server/tests/test_team_cosign_consumers.py",
            },
            indent=2,
        )
    )


async def test_simultaneous_domains_serialize_finalization(api, headers, tenants, admin_engine):
    scope = await cosign_scope(api, headers, tenants, admin_engine)
    await submit(api, headers[0], scope)
    outcomes = await asyncio.gather(
        *(signature(api, scope, domain) for domain in ("commercial", "technical"))
    )
    assert [r.status_code for r in outcomes] == [200, 200], [r.text for r in outcomes]
    assert sorted(r.json()["data"]["summary"]["status"] for r in outcomes) == [
        "complete",
        "partial",
    ]


async def test_revision_during_signing_never_keeps_stale_approval(
    api, headers, tenants, admin_engine
):
    scope = await cosign_scope(api, headers, tenants, admin_engine)
    await submit(api, headers[0], scope)
    signed, withdrawn = await asyncio.gather(
        signature(api, scope, "commercial"),
        api.post(
            f"/cards/{scope['card']['id']}/actions",
            headers=headers[0],
            json={
                "expected_revision": scope["card"]["revision"],
                "action": "withdraw",
                "reason": "Synthetic racing revision",
            },
        ),
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert signed.status_code in {200, 409}, signed.text
    history = (await api.get(f"/cards/{scope['card']['id']}/signoffs", headers=headers[0])).json()
    assert history["data"]["summary"]["status"] == "invalidated"


async def test_multi_domain_legacy_confirm_and_disposition_batch_rejected(
    api, headers, tenants, admin_engine
):
    scope = await cosign_scope(api, headers, tenants, admin_engine)
    auth = scope["members"]["technical"]["headers"]
    disposed = await api.post(
        f"/tasks/{scope['task_id']}/cards/dispositions",
        headers=auth,
        json={
            "extraction_job_id": str(scope["job_id"]),
            "items": [
                {
                    "requirement_id": str(scope["requirement_ids"][0]),
                    "expected_revision": scope["card"]["revision"],
                    "disposition": "comply_only",
                    "reason": "Cannot bypass",
                }
            ],
        },
    )
    assert disposed.status_code == 409, disposed.text
    assert disposed.json()["data"]["error"]["code"] == "cosign_required"
    await submit(api, headers[0], scope)
    rejected = await api.post(
        f"/cards/{scope['card']['id']}/actions",
        headers=auth,
        json={
            "expected_revision": scope["card"]["revision"],
            "action": "confirm",
            "reviewed_warning_codes": scope["card"]["warning_codes"],
            "reason": "Cannot bypass",
        },
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["data"]["error"]["code"] == "cosign_required"


async def test_real_draft_worker_records_partial_and_retired_rounds_as_gaps(
    api, application, headers, tenants, admin_engine
):
    from test_check import publish_draft
    from test_team_workflow_membership import workflow

    scope = await cosign_scope(api, headers, tenants, admin_engine)
    await submit(api, headers[0], scope)
    assert (await signature(api, scope, "commercial")).status_code == 200
    bidder = scope["members"]["commercial"]["headers"]

    async def assemble():
        result = await publish_draft(
            api, application, bidder, str(scope["task_id"]), str(scope["job_id"])
        )
        response = await api.get(f"/drafts/{result['draft_id']}", headers=bidder)
        assert response.status_code == 200, response.text
        return response.json()["data"]

    partial = await assemble()
    assert partial["completion"] == "partial"
    assert all(not rows for rows in partial["tables"].values())
    assert "cosign_required" in partial["gaps"][0]["reasons"]
    assert (await signature(api, scope, "technical")).status_code == 200
    complete = await assemble()
    assert complete["completion"] == "complete"
    rows = [row for table in complete["tables"].values() for row in table]
    assert len(rows) == 1 and rows[0]["deviation"] == "negative"
    current = await workflow(api, headers[0], scope["task_id"])
    retired = await api.post(
        f"/tasks/{scope['task_id']}/members/{scope['members']['technical']['user']}/remove",
        headers=headers[0],
        json={
            "expected_revision": current["revision"],
            "reason": "Synthetic review authority retirement",
        },
    )
    assert retired.status_code == 200, retired.text
    stale = (await api.get(f"/drafts/{complete['id']}", headers=bidder)).json()["data"]
    assert stale["validity"] == "stale" and all(not table for table in stale["tables"].values())
    retired_gap = await assemble()
    assert retired_gap["validity"] == "current" and retired_gap["completion"] == "partial"
    assert retired_gap["gaps"][0]["reasons"] == ["cosign_required"]


async def test_lost_signature_reply_replays_exact_original_receipt(
    api, headers, tenants, admin_engine
):
    scope = await cosign_scope(api, headers, tenants, admin_engine)
    await submit(api, headers[0], scope)
    request_id = str(uuid4())
    first = await signature(api, scope, "commercial", client_request_id=request_id)
    assert first.status_code == 200, first.text
    last = await signature(api, scope, "technical")
    assert last.status_code == 200, last.text
    replay = await signature(api, scope, "commercial", client_request_id=request_id)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == first.json()["data"]
    conflict = await signature(
        api,
        scope,
        "commercial",
        client_request_id=request_id,
        reason="Different input with used request ID",
    )
    assert conflict.status_code == 409
    assert conflict.json()["data"]["error"]["code"] == "idempotency_conflict"


async def test_single_domain_disposition_after_reopen_preserves_board_and_draft(
    api, headers, tenants, admin_engine
):
    scope = await cosign_scope(api, headers, tenants, admin_engine, required=False)
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
    assert "invalidated_cosign" not in board.json()["items"][0]["blockers"]
    assert board.json()["items"][0]["co_sign"]["status"] == "not_required"
    preview = await api.post(
        f"/tasks/{scope['task_id']}/drafts",
        headers=headers[0],
        json={"extraction_job_id": str(scope["job_id"]), "dry_run": True},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["comply_only_requirements"] == 1


@pytest.mark.parametrize(
    "order", [("technical",), ("commercial", "technical"), ("technical", "commercial")]
)
@pytest.mark.parametrize("fail_after_final_signature", [False, True])
async def test_final_signature_round_summary_precedes_consumption_approval_atomically(
    api,
    application,
    headers,
    tenants,
    admin_engine,
    tmp_path,
    monkeypatch,
    order,
    fail_after_final_signature,
):
    """A full signature set is complete during final recheck, but not consumable.

    Failure inventory: the final in-transaction recheck mislabels a full set as
    partial; using round completion alone admits still-unconfirmed response or
    Evidence; a failed final recheck commits its signature or confirmation.
    Exercise the real API/DB transaction for one domain and both signing orders.
    """
    from app.core.errors import ServiceError
    from app.services import drafts, response_cards, task_cosign
    from test_team_cosign_acceptance import attach_material

    scope = await cosign_scope(api, headers, tenants, admin_engine, required=len(order) == 2)
    await attach_material(api, headers[0], scope, tmp_path)
    await submit(api, headers[0], scope)
    pending = scope["card"]
    assert pending["evidence"] and all(row["confirmed_by"] is None for row in pending["evidence"])
    validate = response_cards.validate_confirmation
    observations = []

    async def observe_final_recheck(session, actor, card, revision, requirement, body, storage):
        await validate(session, actor, card, revision, requirement, body, storage)
        projection = (await task_cosign.projections(session, actor.org_id, [card.id]))[card.id]
        summary = projection["summary"]
        if summary["pending_domains"]:
            assert summary["status"] == ("partial" if summary["signed_domains"] else "pending")
            return
        assert summary["status"] == "complete"
        assert set(summary["signed_domains"]) == set(order)
        assert projection["approved"] is False
        assert revision.state == "pending_review" and card.current_revision_id == revision.id
        evidence = await response_cards.linked_evidence(session, revision.id)
        assert all(row.confirmed_by is None and row.confirmed_at is None for row in evidence)
        _, items, _, _, _ = await drafts.assemble(
            session, actor, card.task_id, card.extraction_job_id, storage
        )
        assert len(items) == 1 and items[0]["kind"] == "gap"
        assert "cosign_required" in items[0]["gap_reasons"]
        assert "response_text" not in items[0]
        observations.append({"summary": summary, "approved": False, "partition": "gap"})
        if fail_after_final_signature:
            raise ServiceError(
                "synthetic_final_review_failure", "Synthetic final review interruption", 409, 2
            )

    monkeypatch.setattr(response_cards, "validate_confirmation", observe_final_recheck)
    for domain in order[:-1]:
        first = await signature(api, scope, domain)
        assert first.status_code == 200, first.text
        assert first.json()["data"]["summary"]["status"] == "partial"
    final = await signature(api, scope, order[-1])
    assert len(observations) == 1
    history = await api.get(f"/cards/{pending['id']}/signoffs", headers=headers[0])
    current = await api.get(f"/cards/{pending['id']}", headers=headers[0])
    assert history.status_code == current.status_code == 200
    card = current.json()["data"]
    async with application.state.db.transaction(scope["org_id"]) as session:
        from uuid import UUID

        approved = (await task_cosign.projections(session, scope["org_id"], [UUID(card["id"])]))[
            UUID(card["id"])
        ]["approved"]
    if fail_after_final_signature:
        assert final.status_code == 409, final.text
        assert final.json()["data"]["error"]["code"] == "synthetic_final_review_failure"
        assert card["revision"] == pending["revision"] and card["state"] == "pending_review"
        assert all(row["confirmed_by"] is None for row in card["evidence"])
        assert len(history.json()["items"]) == len(order) - 1
        assert history.json()["data"]["summary"]["status"] == (
            "partial" if len(order) == 2 else "pending"
        )
        assert approved is False
    else:
        assert final.status_code == 200, final.text
        assert card["revision"] == pending["revision"] + 1 and card["state"] == "confirmed"
        assert card["confirmed_by"] == str(scope["members"][order[-1]]["user"])
        assert all(row["confirmed_by"] == card["confirmed_by"] for row in card["evidence"])
        assert len(history.json()["items"]) == len(order)
        assert history.json()["data"]["summary"]["status"] == "complete"
        assert approved is True
    target = ARTIFACT_ROOT / "final-signature-boundary"
    target.mkdir(parents=True, exist_ok=True)
    (
        target
        / ("-".join(order) + ("-rollback" if fail_after_final_signature else "-commit") + ".json")
    ).write_text(
        json.dumps(
            {
                "command": "uv run pytest -q server/tests/test_team_cosign_consumers.py -k final_signature_round_summary",
                "observations": observations,
                "final_http_status": final.status_code,
                "final_summary": history.json()["data"]["summary"],
                "approved": approved,
                "confirmed_evidence": sum(
                    row["confirmed_by"] is not None for row in card["evidence"]
                ),
            },
            indent=2,
        )
        + "\n"
    )
