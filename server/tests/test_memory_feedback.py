"""Feedback boundary failure matrix, written before its implementation.

Reject copying sensitive-only text, evidence quotes, oversized rules, foreign org
inputs, confirmations as candidates, and nonhuman/model-free/no-op feedback.
Replay must keep candidate state and produce no provider usages; encryption must
reject a swapped org/event/sample binding. DB/API integration gates are exercised
by the memory acceptance suite against PostgreSQL, never an alternate database.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.schemas.memory_contracts import MemoryCandidateInput, MemoryFeedbackView, MemoryTarget
from pydantic import ValidationError


def candidate_input(summary="Use exact versioned models", kind="card_rejected"):
    org, event = uuid4(), uuid4()
    return MemoryCandidateInput(
        org_id=org,
        event=MemoryFeedbackView(
            id=event,
            org_id=org,
            task_id=uuid4(),
            card_id=uuid4(),
            before_revision_id=uuid4(),
            after_revision_id=uuid4(),
            actor_user_id=uuid4(),
            kind=kind,
            review_domain="technical",
            created_at=datetime.now(UTC),
            sanitized_sha256="a" * 64,
            sanitizer_version="test-v1",
        ),
        target=MemoryTarget(scope="org"),
        sanitized_summary=summary,
        generator_version="feedback-copy-v1",
    )


async def test_deterministic_candidate_keeps_human_approval_gate():
    from app.memory.candidates import FeedbackCopyProvider

    request = candidate_input()
    first = await FeedbackCopyProvider().propose(request)
    second = await FeedbackCopyProvider().propose(request)
    assert first == second
    assert first.proposal is not None
    assert first.proposal.status == "candidate"
    assert first.proposal.origin == "system"
    assert first.proposal.confirmed_by is None
    assert first.proposal.content.kind == "rule"
    assert str(request.event.id) in first.proposal.content.conflict_key
    assert first.usages == []


async def test_sensitive_only_candidate_skips():
    from app.memory.candidates import FeedbackCopyProvider

    output = await FeedbackCopyProvider().propose(candidate_input("[REDACTED_IDENTITY]"))
    assert output.proposal is None
    assert output.skip_reason == "sensitive_only"


def test_confirmation_and_foreign_org_cannot_be_candidate_input():
    with pytest.raises(ValidationError):
        candidate_input(kind="card_confirmed")
    data = candidate_input().model_dump()
    data["org_id"] = uuid4()
    with pytest.raises(ValidationError):
        MemoryCandidateInput.model_validate(data)


def test_summary_is_bounded_and_has_no_evidence_content():
    from app.memory.feedback import summarize_edit

    before = {
        "response_text": "64 GB memory",
        "deviation": "none",
        "deviation_note": "Exact version",
    }
    after = {
        **before,
        "deviation_note": "Match exact offered version",
        "evidence": [{"quote": "DO NOT COPY"}],
    }
    summary = summarize_edit(before, after)
    assert "DO NOT COPY" not in summary
    assert "deviation_note" in summary
    assert summarize_edit(before, before) == ""
    assert summarize_edit(before, {**after, "deviation_note": "x" * 2100}) == ""


async def test_feedback_api_candidate_replay_and_tenant_isolation(tenants, tmp_path, admin_engine):
    """Real API/worker/RLS chain with only synthetic providers and documents."""
    import asyncio
    import json
    from pathlib import Path

    from app.models.memory import Memory, MemoryEvalSample, MemoryFeedbackEvent, MemoryRevision
    from sqlalchemy import select
    from test_card_generation import drafting_client, execute, slots, submit
    from test_response_cards import (
        create_tender,
        login,
        require_action,
        select_real_materials,
        set_role,
    )

    async with drafting_client(tenants, tmp_path) as (api, app, headers, vendor, _):
        admin = headers[0]
        task, _, extraction, _ = await create_tender(api, app, admin, tmp_path)
        await select_real_materials(api, admin, task, tmp_path)
        terminal = await execute(api, app, admin, await submit(api, admin, task, extraction))
        assert terminal["data"]["status"] == "succeeded"
        current = await slots(api, admin, task, extraction)
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
        human = await login(api, tenants["orgs"][0], "a")
        pending = await require_action(api, human, current[2]["card"], "submit")
        rejected = await require_action(
            api, human, pending, "reject", reason="Use concrete delivery wording"
        )
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            event = await session.scalar(select(MemoryFeedbackEvent))
            sample = await session.scalar(select(MemoryEvalSample))
            assert event is not None and sample is not None
            assert str(event.after_revision_id) == rejected["revision_id"]
            event_id, sample_id = str(event.id), str(sample.id)
        foreign = headers[1]
        for route in (
            f"/tasks/{task}/memory-feedback",
            f"/tasks/{task}/memory-evaluations",
            f"/memory-evaluations/{sample_id}",
        ):
            denied = await api.get(route, headers=foreign)
            assert denied.status_code == 404, denied.text
        denied = await api.post(
            f"/memory-evaluations/{sample_id}/review",
            headers=foreign,
            json={"expected_revision": 1, "action": "accept", "reason": "Synthetic review"},
        )
        assert denied.status_code == 404, denied.text
        denied = await api.post(
            f"/tasks/{task}/memory-candidates", headers=foreign, json={"event_ids": [event_id]}
        )
        assert denied.status_code == 404, denied.text
        run = await api.post(
            f"/tasks/{task}/memory-candidates", headers=human, json={"event_ids": [event_id]}
        )
        assert run.status_code == 200, run.text
        job = run.json()["data"]["job_id"]
        await app.state.processor(human["X-Org-Id"], job)
        await app.state.processor(human["X-Org-Id"], job)
        status = await api.get(f"/jobs/{job}", headers=human)
        assert status.json()["data"]["status"] == "succeeded", status.text
        assert (await api.get(f"/jobs/{job}", headers=headers[1])).status_code == 404
        assert (await api.post(f"/jobs/{job}/cancel", headers=headers[1])).status_code == 404
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            memories = list(await session.scalars(select(Memory)))
            assert len(memories) == 1
            revision = await session.get(MemoryRevision, memories[0].current_revision_id)
            assert revision.status == "candidate" and revision.confirmed_by is None
            assert revision.source["origin"] == "system"
            memory_id, candidate_text = str(memories[0].id), revision.content["text"]
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "admin")
        reviewing_admin = await login(api, tenants["orgs"][0], "a")
        reviewed = await api.post(
            f"/memory-evaluations/{sample_id}/review",
            headers=reviewing_admin,
            json={
                "expected_revision": 1,
                "action": "accept",
                "reason": "Reviewed organization-only synthetic sample",
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["data"]["sample"]["review_state"] == "accepted"
        assert reviewed.json()["data"]["sample"]["org_id"] == str(tenants["orgs"][0])
        shown = await api.get(f"/memories/{memory_id}", headers=reviewing_admin)
        assert shown.json()["data"]["memory"]["current"]["status"] == "candidate"
        # Save the rejected-card cache state before changing only the memory epoch.
        before_approval = await submit(api, reviewing_admin, task, extraction)
        approved = await api.post(
            f"/memories/{memory_id}/decisions",
            headers=reviewing_admin,
            json={
                "expected_revision": 1,
                "action": "approve",
                "reason": "Reviewed applicability to delivery responses",
            },
        )
        assert approved.status_code == 200, approved.text
        next_generation = await submit(api, reviewing_admin, task, extraction)
        assert next_generation["data"]["job_id"] != before_approval["data"]["job_id"]
        assert next_generation["data"]["job_id"] != terminal["data"]["id"]
        next_terminal = await execute(api, app, reviewing_admin, next_generation)
        assert next_terminal["data"]["status"] == "succeeded", next_terminal
        rules = vendor.drafts[-1]["memory_rules"]
        assert any(
            rule["memory"]["memory_id"] == memory_id and rule["text"] == candidate_text
            for rule in rules
        )
        used = await api.get(
            f"/jobs/{next_generation['data']['job_id']}/memory", headers=reviewing_admin
        )
        assert used.status_code == 200 and used.json()["data"]["calls"], used.text
        assert all(
            any(ref["memory_id"] == memory_id for ref in call["memories"])
            for call in used.json()["data"]["calls"]
        )
        artifact = Path("data/work/memory-validation/feedback.json")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            artifact.write_text,
            json.dumps(
                {
                    "version": "feedback-copy-v1",
                    "candidate_count": 1,
                    "tenant_isolation": True,
                    "replay_unique": True,
                    "system_candidate_approved": True,
                    "approved_feedback_in_next_model_context": True,
                    "approval_invalidated_cache": True,
                    "sample_accepted_within_source_org": True,
                },
                sort_keys=True,
            ),
        )


@asynccontextmanager
async def feedback_client(tenants, tmp_path, admin_engine, *, masked=False):
    from test_card_generation import drafting_client, execute, slots, submit
    from test_llm_providers import provider_reply
    from test_response_cards import create_tender, login, select_real_materials, set_role

    async with drafting_client(tenants, tmp_path) as (api, app, headers, vendor, llm):
        task, _, extraction, _ = await create_tender(api, app, headers[0], tmp_path, confirmed=True)
        await select_real_materials(api, headers[0], task, tmp_path)
        if masked:

            async def masked_reply(sent):
                proposals = vendor.proposals(sent)
                for proposal in proposals:
                    proposal["response_text"] = "[REDACTED_IDENTITY]"
                return provider_reply("openai", proposals)

            vendor.respond = masked_reply
        terminal = await execute(
            api, app, headers[0], await submit(api, headers[0], task, extraction)
        )
        assert terminal["data"]["status"] == "succeeded", terminal
        current = await slots(api, headers[0], task, extraction)
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
        human = await login(api, tenants["orgs"][0], "a")
        yield api, app, human, task, current, llm.settings


async def feedback_artifact(name, findings):
    import asyncio
    import json
    from pathlib import Path

    artifact = Path("data/work/memory-validation") / (name + ".json")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        artifact.write_text,
        json.dumps({"version": "feedback-copy-v1", **findings}, sort_keys=True) + "\n",
    )


@pytest.mark.parametrize(
    "action", ["edit", "confirm", "sensitive_reject", "sensitive_edit", "no_op"]
)
async def test_human_feedback_labels_and_sanitized_candidates(
    tenants, tmp_path, admin_engine, action
):
    from app.memory.feedback import unseal
    from app.models.entities import Job
    from app.models.memory import Memory, MemoryEvalSample, MemoryFeedbackEvent, MemoryRevision
    from sqlalchemy import select
    from test_response_cards import require_action

    async with feedback_client(
        tenants, tmp_path, admin_engine, masked=action == "sensitive_edit"
    ) as (api, app, human, task, current, settings):
        card = current[2]["card"]
        if action in {"edit", "no_op", "sensitive_edit"}:
            content = {**card["content"], "evidence": []}
            if action == "edit":
                content["response_text"] = "Use a concrete delivery date. [REDACTED_BANK_ACCOUNT]"
            elif action == "sensitive_edit":
                content["response_text"] = "[REDACTED_BANK_ACCOUNT]"
            updated = await api.put(
                f"/cards/{card['id']}",
                headers=human,
                json={"expected_revision": card["revision"], "content": content},
            )
            assert updated.status_code == 200, updated.text
        else:
            pending = await require_action(api, human, card, "submit")
            if action == "confirm":
                await require_action(
                    api,
                    human,
                    pending,
                    "confirm",
                    reviewed_evidence_ids=[],
                    reviewed_warning_codes=pending["warning_codes"],
                    reason="Reviewed synthetic delivery wording",
                )
            else:
                await require_action(api, human, pending, "reject", reason="[REDACTED_IDENTITY]")
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            events = list(await session.scalars(select(MemoryFeedbackEvent)))
            samples = list(await session.scalars(select(MemoryEvalSample)))
            jobs = list(await session.scalars(select(Job).where(Job.kind == "memory_candidate")))
            if action == "no_op":
                assert events == samples == jobs == []
            else:
                assert len(events) == len(samples) == 1
                assert samples[0].review_state == "unreviewed"
                if action == "confirm":
                    assert events[0].kind == "card_confirmed" and jobs == []
                else:
                    assert len(jobs) == 1
                    job_id = str(jobs[0].id)
                    summary = unseal(
                        settings, events[0].org_id, events[0].id, events[0].encrypted_summary
                    )
                    assert "{{secret." not in summary
        if action not in {"confirm", "no_op"}:
            await app.state.processor(human["X-Org-Id"], job_id)
            status = await api.get(f"/jobs/{job_id}", headers=human)
            assert status.json()["data"]["status"] == "succeeded", status.text
            outcome = status.json()["data"]["result"]["items"][0]
            async with app.state.db.transaction(tenants["orgs"][0]) as session:
                memories = list(await session.scalars(select(Memory)))
                if action == "edit":
                    assert len(memories) == 1 and outcome["outcome"] == "created"
                    revision = await session.get(MemoryRevision, memories[0].current_revision_id)
                    assert revision.status == "candidate" and revision.confirmed_by is None
                    assert "Use a concrete delivery date" in revision.content["text"]
                else:
                    assert memories == [] and outcome["outcome"] == "skipped"
                    assert outcome["error_code"] == "sensitive_only"
        await feedback_artifact("human-" + action, {"action": action, "checked": True})


async def test_enqueue_failure_preserves_human_decision_and_recovers(
    tenants, tmp_path, admin_engine, monkeypatch
):
    from app.models.entities import Job
    from app.models.memory import MemoryFeedbackEvent
    from sqlalchemy import select
    from test_response_cards import card_action, require_action

    async with feedback_client(tenants, tmp_path, admin_engine) as (
        api,
        app,
        human,
        task,
        current,
        _,
    ):
        pending = await require_action(api, human, current[2]["card"], "submit")
        original = app.state.queue.enqueue

        async def unavailable(*args):
            raise OSError("synthetic queue boundary failure")

        monkeypatch.setattr(app.state.queue, "enqueue", unavailable)
        response = await card_action(
            api, human, pending, "reject", reason="Use firm delivery wording"
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["state"] == "rejected"
        assert any(
            warning.startswith("memory_candidate_dispatch_pending:")
            for warning in response.json()["warnings"]
        )
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            event = await session.scalar(select(MemoryFeedbackEvent))
            job = await session.scalar(select(Job).where(Job.kind == "memory_candidate"))
            assert event and job and job.status == "queued" and job.queue_id is None
            event_id, original_job = str(event.id), str(job.id)
        monkeypatch.setattr(app.state.queue, "enqueue", original)
        receipt = await api.post(
            f"/tasks/{task}/memory-candidates", headers=human, json={"event_ids": [event_id]}
        )
        assert receipt.status_code == 200 and receipt.json()["data"]["job_id"] == original_job
        await app.state.processor(human["X-Org-Id"], original_job)
        status = await api.get(f"/jobs/{original_job}", headers=human)
        assert status.json()["data"]["status"] == "succeeded", status.text
        await feedback_artifact(
            "enqueue-recovery", {"human_decision_preserved": True, "same_durable_job": True}
        )


async def test_partial_candidate_batch_replays_only_failed_event(
    tenants, tmp_path, admin_engine, monkeypatch
):
    from app.core.errors import ServiceError
    from app.memory.candidates import FeedbackCopyProvider
    from app.models.memory import Memory, MemoryFeedbackEvent
    from sqlalchemy import select
    from test_response_cards import require_action

    async with feedback_client(tenants, tmp_path, admin_engine) as (
        api,
        app,
        human,
        task,
        current,
        _,
    ):
        for slot in current[2:4]:
            pending = await require_action(api, human, slot["card"], "submit")
            await require_action(
                api, human, pending, "reject", reason="Use concrete delivery wording"
            )
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            events = list(
                await session.scalars(
                    select(MemoryFeedbackEvent).order_by(
                        MemoryFeedbackEvent.created_at, MemoryFeedbackEvent.id
                    )
                )
            )
            ids = [str(event.id) for event in events]
        original = FeedbackCopyProvider.propose

        async def fail_one(self, request):
            if str(request.event.id) == ids[1]:
                raise ServiceError("synthetic_event_failure", "Synthetic event failure", 503, 3)
            return await original(self, request)

        monkeypatch.setattr(FeedbackCopyProvider, "propose", fail_one)
        receipt = await api.post(
            f"/tasks/{task}/memory-candidates", headers=human, json={"event_ids": ids}
        )
        batch_id = receipt.json()["data"]["job_id"]
        await app.state.processor(human["X-Org-Id"], batch_id)
        partial = await api.get(f"/jobs/{batch_id}", headers=human)
        assert partial.json()["data"]["status"] == "succeeded" and not partial.json()["ok"], (
            partial.text
        )
        assert partial.json()["data"]["result"]["completion"] == "partial"
        reused = await api.post(
            f"/tasks/{task}/memory-candidates",
            headers=human,
            json={"event_ids": ids, "action": {"retry": True}},
        )
        assert reused.json()["data"]["job_id"] == batch_id
        unchanged = await api.get(f"/jobs/{batch_id}", headers=human)
        assert unchanged.json()["data"]["status"] == "succeeded"
        monkeypatch.setattr(FeedbackCopyProvider, "propose", original)
        retry = await api.post(
            f"/tasks/{task}/memory-candidates",
            headers=human,
            json={"event_ids": [ids[1]], "action": {"retry": True}},
        )
        failed_id = retry.json()["data"]["job_id"]
        assert failed_id != batch_id
        await app.state.processor(human["X-Org-Id"], failed_id)
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert len(list(await session.scalars(select(Memory)))) == 2
        await feedback_artifact(
            "partial-recovery",
            {"partial_exit": 5, "retry_failed_subset": True, "candidate_count": 2},
        )


@pytest.mark.parametrize("stop", ["cancel", "lease_takeover"])
async def test_candidate_attempt_fences_old_publication(
    tenants, tmp_path, admin_engine, monkeypatch, stop
):
    from datetime import timedelta

    from app.memory.candidates import FeedbackCopyProvider
    from app.models.entities import Job
    from app.models.memory import Memory, MemoryFeedbackEvent
    from sqlalchemy import select
    from test_response_cards import require_action

    async with feedback_client(tenants, tmp_path, admin_engine) as (
        api,
        app,
        human,
        task,
        current,
        _,
    ):
        pending = await require_action(api, human, current[2]["card"], "submit")
        await require_action(api, human, pending, "reject", reason="Use firm delivery wording")
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            event = await session.scalar(select(MemoryFeedbackEvent))
            event_id = str(event.id)
        receipt = await api.post(
            f"/tasks/{task}/memory-candidates", headers=human, json={"event_ids": [event_id]}
        )
        job_id = receipt.json()["data"]["job_id"]
        original = FeedbackCopyProvider.propose
        old_run, replacement_run = None, uuid4()

        async def stop_during_prepare(self, request):
            nonlocal old_run
            async with app.state.db.transaction(tenants["orgs"][0]) as session:
                job = await session.scalar(
                    select(Job).where(Job.id == UUID(job_id)).with_for_update()
                )
                old_run = job.run_id
                if stop == "lease_takeover":
                    job.run_id = replacement_run
                    job.lease_until = datetime.now(UTC) + timedelta(minutes=2)
            if stop == "cancel":
                cancelled = await api.post(f"/jobs/{job_id}/cancel", headers=human)
                assert cancelled.status_code == 200, cancelled.text
            return await original(self, request)

        monkeypatch.setattr(FeedbackCopyProvider, "propose", stop_during_prepare)
        await app.state.processor(human["X-Org-Id"], job_id)
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert list(await session.scalars(select(Memory))) == []
            job = await session.get(Job, UUID(job_id))
            assert job.status == ("cancelled" if stop == "cancel" else "running")
            if stop == "lease_takeover":
                assert job.run_id == replacement_run and job.run_id != old_run
                job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        monkeypatch.setattr(FeedbackCopyProvider, "propose", original)
        retry = await api.post(
            f"/tasks/{task}/memory-candidates",
            headers=human,
            json={"event_ids": [event_id], "action": {"retry": True}},
        )
        assert retry.status_code == 200 and retry.json()["data"]["job_id"] == job_id
        await app.state.processor(human["X-Org-Id"], job_id)
        status = await api.get(f"/jobs/{job_id}", headers=human)
        assert status.json()["data"]["status"] == "succeeded", status.text
        assert status.json()["data"]["result"]["run_id"] != str(old_run)
        await feedback_artifact(
            "attempt-" + stop, {"old_attempt_fenced": True, "retry_new_attempt": True}
        )
