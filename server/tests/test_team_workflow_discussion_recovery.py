"""API/PostgreSQL recovery acceptance for the prewritten slice-two failures.

The inventory in test_team_workflow_discussion.py precedes implementation. These
scenarios exercise immutable confirmed evidence, request recovery across card
revisions, revocation/races, parent/identity-bound pages, event-producer rollback
and real SSE replay. Only the main session executes these against its explicitly
provided bid_test database; tests never start services or call external vendors.
"""

import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_response_cards import CERTIFICATE_PAGE, require_action, select_real_materials
from test_team_workflow_discussion import assign, board, message, scope_with_card, thread
from test_team_workflow_membership import add_member, person, workflow
from test_team_workflow_stream_acceptance import LiveASGIStream, assert_no_private_fields, poll

ARTIFACTS = Path(__file__).resolve().parents[2] / "data/work/team-workflow-acceptance/discussion"


def artifact(name, value):
    destination = ARTIFACTS / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, default=str, indent=2) + "\n")


async def test_confirmed_card_discussion_preserves_revision_and_evidence(
    api, headers, tenants, admin_engine, tmp_path
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    reviewer, reviewer_header = await person(api, admin_engine, scope["org_id"], "bidder")
    assert (
        await add_member(api, headers[0], scope["task_id"], reviewer, 1, "reviewer", ["commercial"])
    ).status_code == 200
    *_, source = await select_real_materials(api, headers[0], scope["task_id"], tmp_path)
    created = await api.post(
        f"/tasks/{scope['task_id']}/cards",
        headers=headers[0],
        json={
            "extraction_job_id": str(scope["job_id"]),
            "requirement_id": str(scope["requirement_ids"][1]),
            "content": {
                "response_kind": "evidence",
                "response_text": "Synthetic certificate response",
                "deviation": "none",
                "deviation_note": "The synthetic certificate supports the requirement.",
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
    assert created.status_code == 200, created.text
    submitted = await require_action(api, headers[0], created.json()["data"], "submit")
    confirmed = await require_action(
        api,
        reviewer_header,
        submitted,
        "confirm",
        reviewed_evidence_ids=[item["id"] for item in submitted["evidence"]],
        reviewed_warning_codes=submitted["warning_codes"],
        **(
            {"reason": "Reviewed synthetic certificate warnings"}
            if submitted["warning_codes"]
            else {}
        ),
    )
    assert confirmed["state"] == "confirmed" and confirmed["evidence"]
    assert all(item["confirmed_by"] == str(reviewer) for item in confirmed["evidence"])
    before = await api.get(
        f"/cards/{confirmed['id']}", headers=headers[0], params={"history": "true"}
    )
    assert before.status_code == 200, before.text
    receipt = await thread(api, headers[0], confirmed)
    reply = await api.post(
        f"/cards/{confirmed['id']}/threads/{receipt['thread']['id']}/comments",
        headers=reviewer_header,
        json=message(),
    )
    assert reply.status_code == 200, reply.text
    after = await api.get(
        f"/cards/{confirmed['id']}", headers=headers[0], params={"history": "true"}
    )
    assert after.status_code == 200 and before.json()["data"] == after.json()["data"]
    assert before.json()["items"] == after.json()["items"]
    artifact(
        "confirmed-card-unchanged.json",
        {
            "task_id": scope["task_id"],
            "card_id": confirmed["id"],
            "revision_id": confirmed["revision_id"],
            "evidence_ids": [item["id"] for item in confirmed["evidence"]],
            "thread_id": receipt["thread"]["id"],
            "card_and_evidence_unchanged": True,
        },
    )


async def test_create_receipt_survives_card_revision_but_new_stale_request_conflicts(
    api, headers, tenants, admin_engine
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    path = f"/cards/{scope['card']['id']}/threads"
    payload = message(expected_card_revision=scope["card"]["revision"])
    original = await api.post(path, headers=headers[0], json=payload)
    assert original.status_code == 200, original.text
    edited = await api.put(
        f"/cards/{scope['card']['id']}",
        headers=headers[0],
        json={
            "expected_revision": scope["card"]["revision"],
            "content": {
                "response_kind": "commitment",
                "response_text": "Changed synthetic response",
            },
        },
    )
    assert (
        edited.status_code == 200 and edited.json()["data"]["revision"] > scope["card"]["revision"]
    )
    recovered = await api.post(path, headers=headers[0], json=payload)
    assert recovered.status_code == 200 and recovered.json()["data"] == original.json()["data"]
    stale = await api.post(
        path, headers=headers[0], json={**payload, "client_request_id": str(uuid4())}
    )
    assert stale.status_code == 409 and stale.json()["data"]["error"]["code"] == "revision_conflict"
    listing = await api.get(path, headers=headers[0])
    assert listing.status_code == 200 and len(listing.json()["items"]) == 1
    assert listing.json()["items"][0]["created_card_revision_id"] == scope["card"]["revision_id"]
    artifact(
        "lost-create-receipt.json",
        {
            "task_id": scope["task_id"],
            "thread_id": original.json()["data"]["thread"]["id"],
            "original_card_revision": scope["card"]["revision"],
            "current_card_revision": edited.json()["data"]["revision"],
            "identical_receipt": True,
            "new_stale_request": stale.status_code,
        },
    )


@pytest.mark.parametrize("revocation", ["removed", "org_inactive"])
async def test_revoked_mentions_stay_historical_but_disappear_from_reads_and_new_writes(
    api, headers, tenants, admin_engine, revocation
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    mentioned, _ = await person(api, admin_engine, scope["org_id"], "viewer")
    assert (
        await add_member(api, headers[0], scope["task_id"], mentioned, 1, "observer")
    ).status_code == 200
    receipt = await thread(api, headers[0], scope["card"], mentioned_user_ids=[str(mentioned)])
    assert receipt["first_comment"]["mentioned_user_ids"] == [str(mentioned)]
    if revocation == "removed":
        response = await api.post(
            f"/tasks/{scope['task_id']}/members/{mentioned}/remove",
            headers=headers[0],
            json={"expected_revision": 2, "reason": "Synthetic mention removal"},
        )
        assert response.status_code == 200, response.text
    else:
        with admin_engine.begin() as connection:
            connection.execute(
                text("UPDATE memberships SET active=false WHERE org_id=:org AND user_id=:user"),
                {"org": scope["org_id"], "user": mentioned},
            )
    comments_path = f"/cards/{scope['card']['id']}/threads/{receipt['thread']['id']}/comments"
    listed = await api.get(comments_path, headers=headers[0])
    assert listed.status_code == 200 and listed.json()["items"][0]["mentioned_user_ids"] == []
    rejected = await api.post(
        comments_path, headers=headers[0], json=message(mentioned_user_ids=[str(mentioned)])
    )
    assert rejected.status_code == 404 and rejected.json()["data"]["error"]["code"] == "not_found"
    with admin_engine.connect() as connection:
        historical = connection.scalar(
            text(
                "SELECT count(*) FROM card_comment_mentions WHERE comment_id=:comment AND user_id=:user"
            ),
            {"comment": UUID(receipt["first_comment"]["id"]), "user": mentioned},
        )
    assert historical == 1


async def test_same_org_other_task_member_is_not_a_mention_target(
    api, headers, tenants, admin_engine
):
    one = await scope_with_card(api, headers, tenants, admin_engine)
    two = await scope_with_card(api, headers, tenants, admin_engine)
    user, _ = await person(api, admin_engine, one["org_id"], "viewer")
    assert (
        await add_member(api, headers[0], two["task_id"], user, 1, "observer")
    ).status_code == 200
    denied = await api.post(
        f"/cards/{one['card']['id']}/threads",
        headers=headers[0],
        json=message(expected_card_revision=1, mentioned_user_ids=[str(user)]),
    )
    assert denied.status_code == 404 and denied.json()["items"] == []
    assert str(user) not in denied.text and str(two["task_id"]) not in denied.text


async def test_mention_and_member_removal_serialize_without_an_active_revoked_target(
    api, headers, tenants, admin_engine
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    user, _ = await person(api, admin_engine, scope["org_id"], "viewer")
    assert (
        await add_member(api, headers[0], scope["task_id"], user, 1, "observer")
    ).status_code == 200
    commented, removed = await asyncio.gather(
        api.post(
            f"/cards/{scope['card']['id']}/threads",
            headers=headers[0],
            json=message(expected_card_revision=1, mentioned_user_ids=[str(user)]),
        ),
        api.post(
            f"/tasks/{scope['task_id']}/members/{user}/remove",
            headers=headers[0],
            json={"expected_revision": 2, "reason": "Synthetic concurrent mention removal"},
        ),
    )
    assert removed.status_code == 200, removed.text
    assert commented.status_code in {200, 404}, commented.text
    if commented.status_code == 200:
        receipt = commented.json()["data"]
        listed = await api.get(
            f"/cards/{scope['card']['id']}/threads/{receipt['thread']['id']}/comments",
            headers=headers[0],
        )
        assert listed.status_code == 200 and listed.json()["items"][0]["mentioned_user_ids"] == []
    denied = await api.post(
        f"/cards/{scope['card']['id']}/threads",
        headers=headers[0],
        json=message(expected_card_revision=1, mentioned_user_ids=[str(user)]),
    )
    assert denied.status_code == 404
    artifact(
        "mention-removal-race.json",
        {
            "task_id": scope["task_id"],
            "removed_user_id": user,
            "comment_status": commented.status_code,
            "removal_status": removed.status_code,
            "new_mention_rejected": True,
        },
    )


async def test_thread_pages_bind_card_reader_and_org(api, headers, tenants, admin_engine):
    one = await scope_with_card(api, headers, tenants, admin_engine)
    two = await scope_with_card(api, headers, tenants, admin_engine)
    foreign = await scope_with_card(api, headers, tenants, admin_engine, tenant=1)
    user, reader = await person(api, admin_engine, one["org_id"], "viewer")
    assert (
        await add_member(api, headers[0], one["task_id"], user, 1, "observer")
    ).status_code == 200
    expected = []
    for _ in range(3):
        expected.append((await thread(api, headers[0], one["card"]))["thread"]["id"])
    path = f"/cards/{one['card']['id']}/threads"
    first = await api.get(path, headers=headers[0], params={"limit": 1})
    assert first.status_code == 200 and first.json()["data"]["has_more"]
    cursor = first.json()["data"]["next_cursor"]
    seen = [first.json()["items"][0]["id"]]
    while cursor:
        page = await api.get(path, headers=headers[0], params={"limit": 1, "cursor": cursor})
        assert page.status_code == 200, page.text
        seen += [item["id"] for item in page.json()["items"]]
        cursor = page.json()["data"]["next_cursor"]
    assert seen == expected and len(set(seen)) == 3
    cursor = first.json()["data"]["next_cursor"]
    for card, auth in (
        (one["card"], reader),
        (two["card"], headers[0]),
        (foreign["card"], headers[1]),
    ):
        denied = await api.get(
            f"/cards/{card['id']}/threads", headers=auth, params={"limit": 1, "cursor": cursor}
        )
        assert denied.status_code == 404 and denied.json()["data"]["error"]["code"] == "not_found"


async def test_assigned_owner_handover_cannot_demote_but_can_retain_contributor(
    api, headers, tenants, admin_engine
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    target, target_header = await person(api, admin_engine, scope["org_id"], "bidder")
    assert (await assign(api, headers[0], scope, scope["user_id"])).status_code == 200
    before = await workflow(api, headers[0], scope["task_id"])
    path = f"/tasks/{scope['task_id']}/handover"
    payload = {
        "expected_revision": before["revision"],
        "reason": "Synthetic assigned-owner handover",
        "target_user_id": str(target),
        "previous_owner_role": "observer",
        "previous_owner_review_domains": [],
    }
    blocked = await api.post(path, headers=headers[0], json=payload)
    assert (
        blocked.status_code == 409
        and blocked.json()["data"]["error"]["code"] == "member_has_assignments"
    )
    after_denial = await workflow(api, headers[0], scope["task_id"])
    for key in ("owner_user_id", "state", "revision", "access_epoch"):
        assert after_denial[key] == before[key]
    retained = await api.post(
        path, headers=headers[0], json={**payload, "previous_owner_role": "contributor"}
    )
    assert retained.status_code == 200, retained.text
    current = await workflow(api, target_header, scope["task_id"])
    assert current["owner_user_id"] == str(target) and current["revision"] == before["revision"] + 1
    mine = await board(api, headers[0], scope, mine="true")
    assert mine["items"][0]["owner_user_id"] == str(scope["user_id"])
    members = await api.get(f"/tasks/{scope['task_id']}/members", headers=target_header)
    old = next(item for item in members.json()["items"] if item["user_id"] == str(scope["user_id"]))
    assert old["role"] == "contributor" and old["active"]


@pytest.mark.parametrize("operation", ["assignment", "thread"])
async def test_event_producer_failure_rolls_back_business_audit_and_sequence(
    api, headers, tenants, admin_engine, operation
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    trigger = "test_event_failure_" + uuid4().hex

    def saved_state():
        with admin_engine.connect() as connection:
            counts = {
                table: connection.scalar(
                    text(f"SELECT count(*) FROM public.{table} WHERE task_id=:task"),
                    {"task": scope["task_id"]},
                )
                for table in (
                    "requirement_workflows",
                    "card_comment_threads",
                    "card_comments",
                    "card_comment_mentions",
                    "task_events",
                )
            }
            counts["audit_logs"] = connection.scalar(
                text("SELECT count(*) FROM public.audit_logs WHERE object_id=:task"),
                {"task": scope["task_id"]},
            )
            counts["last_seq"] = connection.scalar(
                text("SELECT last_seq FROM public.task_event_heads WHERE task_id=:task"),
                {"task": scope["task_id"]},
            )
            return counts

    before = saved_state()
    # Fault only this synthetic task; restore the trigger/function even if an
    # assertion fails. A real producer must fail the surrounding API transaction.
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE FUNCTION public.{trigger}() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN IF NEW.task_id='{scope['task_id']}'::uuid THEN RAISE EXCEPTION 'synthetic_event_failure' USING ERRCODE='23514'; END IF; RETURN NEW; END $$"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER {trigger} BEFORE INSERT ON public.task_events FOR EACH ROW EXECUTE FUNCTION public.{trigger}()"
        )
    try:
        if operation == "assignment":
            rejected = await assign(api, headers[0], scope, scope["user_id"])
        else:
            rejected = await api.post(
                f"/cards/{scope['card']['id']}/threads",
                headers=headers[0],
                json=message(expected_card_revision=1),
            )
        assert (
            rejected.status_code == 409 and rejected.json()["data"]["error"]["code"] == "conflict"
        )
        assert saved_state() == before
    finally:
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON public.task_events")
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS public.{trigger}()")
    if operation == "assignment":
        assert (await assign(api, headers[0], scope, scope["user_id"])).status_code == 200
    else:
        await thread(api, headers[0], scope["card"])
    assert saved_state()["last_seq"] > before["last_seq"]
    artifact(
        f"event-failure-{operation}.json",
        {
            "task_id": scope["task_id"],
            "operation": operation,
            "rollback_complete": True,
            "recovery_committed": True,
        },
    )


async def test_live_sse_replays_assignment_and_discussion_without_body_or_labels(
    api, application, headers, tenants, admin_engine
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    initial = await poll(api, headers[0], scope["task_id"])
    assert initial.status_code == 200
    baseline = initial.json()["data"]["head_cursor"]
    stream_headers = {**headers[0], "Last-Event-ID": baseline}
    async with LiveASGIStream(
        application, f"/tasks/{scope['task_id']}/events", stream_headers
    ) as stream:
        start = await stream.next()
        assert start["type"] == "http.response.start" and start["status"] == 200
        assert (await assign(api, headers[0], scope, scope["user_id"])).status_code == 200
        receipt = await thread(api, headers[0], scope["card"])
        reply = await api.post(
            f"/cards/{scope['card']['id']}/threads/{receipt['thread']['id']}/comments",
            headers=headers[0],
            json=message(),
        )
        assert reply.status_code == 200, reply.text
        expected_comment_ids = {
            receipt["first_comment"]["id"],
            reply.json()["data"]["comment"]["id"],
        }
        frames = []
        async with asyncio.timeout(6):
            while not expected_comment_ids <= {
                item["payload"].get("comment_id") for item in frames
            }:
                await stream.next()
                frames = [
                    json.loads(line.removeprefix("data: "))
                    for line in stream.body().splitlines()
                    if line.startswith("data: ")
                ]
        assert any(
            str(scope["requirement_ids"][0]) in item["payload"]["requirement_ids"]
            for item in frames
        )
        assert_no_private_fields(frames)
        assert "Synthetic <script>" not in stream.body() and "example.test" not in stream.body()
        delivered = {item["event_id"] for item in frames}
    replay = await poll(api, headers[0], scope["task_id"], baseline)
    assert replay.status_code == 200
    replayed = {item["event_id"] for item in replay.json()["items"]}
    assert delivered == replayed
    assert_no_private_fields(replay.json())
    again = await poll(api, headers[0], scope["task_id"], replay.json()["data"]["next_cursor"])
    assert again.status_code == 200 and again.json()["items"] == []
    artifact(
        "discussion-sse-replay.json",
        {
            "task_id": scope["task_id"],
            "thread_id": receipt["thread"]["id"],
            "event_ids": sorted(delivered),
            "sse_equals_durable_replay": True,
            "no_private_payload": True,
            "checkpoint_caught_up": True,
        },
    )
