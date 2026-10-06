"""Memory drafting boundary scenarios, specified before implementation.

Failures: candidates leaking into prompts; empty-result cache surviving approval;
per-batch accounting missing retries; stale admitted inputs publishing; memory refs
being accepted as evidence; changed memory silently revoking a human decision.
Synthetic transports only. The DB scenario emits a reproducible hashed artifact.
"""

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.providers.drafting import groups, request_body
from app.providers.llm import OpenAICompatibleExtractor
from app.schemas.memory_contracts import MemoryHit, MemoryPromptContext, MemoryRef
from cryptography.fernet import Fernet
from test_card_generation import drafting_client, execute, submit
from test_llm_providers import settings_for
from test_response_cards import create_tender


def context(text="Use concise response wording"):
    return MemoryPromptContext(
        org_id=uuid4(),
        retrieval_id=uuid4(),
        manifest_sha256="a" * 64,
        rules=[
            MemoryHit(
                memory=MemoryRef(
                    memory_id=uuid4(),
                    revision_id=uuid4(),
                    revision=2,
                    scope="org",
                    content_sha256="b" * 64,
                    sent_sha256="c" * 64,
                ),
                kind="rule",
                conflict_key="response.style",
                text=text,
                rank=1,
                priority=2,
                relevance=10,
                matched_by=["keyword"],
            )
        ],
    )


def test_memory_is_separate_from_evidence_and_included_in_budget(tmp_path):
    llm = OpenAICompatibleExtractor(
        settings_for(
            tmp_path,
            "openai",
            database_url="postgresql+psycopg://unused@127.0.0.1/unused",
            encryption_key=Fernet.generate_key().decode(),
            token_key=Fernet.generate_key().decode(),
            llm_input_usd_per_mtok=1,
            llm_output_usd_per_mtok=1,
        )
    )
    requirements = [{"requirement_id": str(uuid4()), "quote": "one"} for _ in range(2)]
    memory = context("x" * 1000)
    body = request_body(llm, requirements, [], memory=memory)
    payload = json.loads(body["messages"][-1]["content"])
    assert payload["materials"] == []
    assert payload["memory_rules"][0]["text"] == "x" * 1000
    assert payload["memory_usable_as_evidence"] is False
    assert len(groups(requirements, [], 1200)) == 1
    assert len(groups(requirements, [], 1200, memory=memory)) == 2
    assert llm.reservation(body) >= llm.reservation(request_body(llm, requirements, []))


async def test_candidate_approval_invalidates_empty_cache_and_records_calls(tenants, tmp_path):
    async with drafting_client(tenants, tmp_path) as (api, app, headers, vendor, _):
        header = headers[0]
        task, _, extraction, _ = await create_tender(api, app, header, tmp_path)
        first = await submit(api, header, task, extraction)
        await execute(api, app, header, first)
        created = await api.post(
            "/memories",
            headers=header,
            json={
                "target": {"scope": "org"},
                "content": {
                    "kind": "rule",
                    "conflict_key": "response.concise",
                    "text": "Use concise wording for every response",
                    "tags": ["drafting"],
                },
            },
        )
        assert created.status_code in {200, 201}, created.text
        memory = created.json()["data"]["memory"]
        same = await submit(api, header, task, extraction)
        assert same["data"]["job_id"] == first["data"]["job_id"]
        decision = await api.post(
            f"/memories/{memory['id']}/decisions",
            headers=header,
            json={"expected_revision": 1, "action": "approve", "reason": "Reviewed applicability"},
        )
        assert decision.status_code == 200, decision.text
        second = await submit(api, header, task, extraction)
        assert second["data"]["job_id"] != first["data"]["job_id"]
        result = await execute(api, app, header, second)
        assert vendor.drafts[-1]["memory_rules"][0]["text"] == memory["current"]["content"]["text"]
        used = await api.get(f"/jobs/{second['data']['job_id']}/memory", headers=header)
        assert used.status_code == 200, used.text
        calls = used.json()["data"]["calls"]
        assert calls and all(call["state"] == "completed" for call in calls)
        assert all(call["memories"][0]["memory_id"] == memory["id"] for call in calls)
        foreign = await api.get(f"/jobs/{second['data']['job_id']}/memory", headers=headers[1])
        assert foreign.status_code == 404
        report = {
            "version": "memory-validation-v1",
            "job_id": second["data"]["job_id"],
            "org_id": str(UUID(header["X-Org-Id"])),
            "calls": calls,
            "result_sha256": hashlib.sha256(
                json.dumps(result, sort_keys=True).encode()
            ).hexdigest(),
            "assertions": [
                "candidate_excluded",
                "empty_cache_invalidated",
                "calls_bound",
                "org_isolated",
            ],
        }
        destination = Path("data/work/memory-validation")
        await asyncio.to_thread(destination.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(
            (destination / "drafting.json").write_text, json.dumps(report, indent=2)
        )


@pytest.mark.parametrize("boundary", ["queued", "inflight"])
async def test_changed_memory_fences_dispatch_or_publication_and_keeps_usage(
    tenants, tmp_path, boundary
):
    from app.models.memory import MemoryCallInput
    from app.models.response_cards import CardGenerationRun
    from sqlalchemy import select
    from test_llm_providers import provider_reply
    from test_memory_api import approved_rule

    async with drafting_client(tenants, tmp_path) as (api, app, headers, vendor, _):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(api, app, header, tmp_path)
        memory = await approved_rule(
            api, header, "boundary.rule", "Use concise wording", ["drafting"]
        )
        receipt = await submit(
            api, header, task, extraction, requirement_ids=[requirements[2]["id"]]
        )

        async def disable():
            result = await api.post(
                f"/memories/{memory['id']}/disable",
                headers=header,
                json={"expected_revision": 2, "reason": "Guidance withdrawn"},
            )
            assert result.status_code == 200, result.text

        if boundary == "queued":
            await disable()
        else:

            async def respond(sent):
                await disable()
                return provider_reply("openai", vendor.proposals(sent))

            vendor.respond = respond
        result = await execute(api, app, header, receipt)
        assert result["data"]["status"] == "failed", result
        assert result["data"]["error"]["code"] == "memory_input_changed"
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            calls = list(
                await session.scalars(
                    select(MemoryCallInput).where(
                        MemoryCallInput.job_id == UUID(receipt["data"]["job_id"])
                    )
                )
            )
            runs = list(await session.scalars(select(CardGenerationRun)))
            assert runs == []
            assert len(calls) == (0 if boundary == "queued" else 1)
            if calls:
                assert calls[0].state == "completed"
                assert calls[0].usage_record_id == calls[0].call_id


@pytest.mark.parametrize("cosign_required", [False, True])
async def test_memory_stale_review_gate_preserves_already_confirmed_card(
    tenants, tmp_path, admin_engine, cosign_required
):
    from task_fixtures import reviewer_header
    from test_card_generation import slots
    from test_memory_api import approved_rule
    from test_response_cards import require_action

    async with drafting_client(tenants, tmp_path) as (api, app, headers, _, _):
        task, _, extraction, requirements = await create_tender(
            api, app, headers[0], tmp_path, confirmed=True
        )
        memory = await approved_rule(
            api, headers[0], "review.rule", "Use concise wording", ["drafting"]
        )
        receipt = await submit(
            api,
            headers[0],
            task,
            extraction,
            requirement_ids=[requirements[2]["id"], requirements[3]["id"]],
        )
        await execute(api, app, headers[0], receipt)
        current = await slots(api, headers[0], task, extraction)
        _, human = await reviewer_header(
            api, admin_engine, tenants["orgs"][0], UUID(task), "technical"
        )
        if cosign_required:
            policy = await api.put(
                f"/tasks/{task}/requirements/{requirements[2]['id']}/review-policy",
                headers=headers[0],
                params={"extraction_job_id": extraction},
                json={
                    "expected_policy_revision": 0,
                    "co_sign_required": True,
                    "reason": "Both domains must retain current generation inputs",
                },
            )
            assert policy.status_code == 200, policy.text
        pending = await require_action(api, human, current[2]["card"], "submit")
        if cosign_required:
            _, commercial = await reviewer_header(
                api, admin_engine, tenants["orgs"][0], UUID(task), "commercial"
            )
            listing = await api.get(f"/cards/{pending['id']}/signoffs", headers=human)
            assert listing.status_code == 200, listing.text
            review_round = listing.json()["data"]["round"]
            for domain, signer in (("technical", human), ("commercial", commercial)):
                signed = await api.post(
                    f"/cards/{pending['id']}/signoffs",
                    headers=signer,
                    json={
                        "purpose": "response",
                        "action": "confirm",
                        "expected_revision": pending["revision"],
                        "expected_round": review_round["round_revision"],
                        "selected_domain": domain,
                        "reviewed_evidence_ids": [],
                        "reviewed_warning_codes": pending["warning_codes"],
                        "reason": "Reviewed the current response and its guidance",
                        "client_request_id": str(uuid4()),
                    },
                )
                assert signed.status_code == 200, signed.text
                assert signed.json()["data"]["summary"]["status"] == (
                    "partial" if domain == "technical" else "complete"
                )
            confirmed = (await api.get(f"/cards/{pending['id']}", headers=human)).json()["data"]
        else:
            confirmed = await require_action(
                api, human, pending, "confirm", reviewed_evidence_ids=[]
            )
        another = await require_action(api, human, current[3]["card"], "submit")
        admin = headers[0]
        disabled = await api.post(
            f"/memories/{memory['id']}/disable",
            headers=admin,
            json={"expected_revision": 2, "reason": "Guidance withdrawn"},
        )
        assert disabled.status_code == 200, disabled.text
        shown = await api.get(f"/cards/{confirmed['id']}", headers=admin)
        assert shown.status_code == 200, shown.text
        assert shown.json()["data"]["state"] == "confirmed"
        assert shown.json()["data"]["eligibility"] == (
            "needs_reconfirmation" if cosign_required else "eligible"
        )
        assert "memory_changed_after_review" in shown.json()["data"]["warning_codes"]
        preview = await api.post(
            f"/tasks/{task}/drafts",
            headers=admin,
            json={"extraction_job_id": extraction, "dry_run": True},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["data"]["response_requirements"] == (0 if cosign_required else 1)
        result = await api.post(
            f"/cards/{another['id']}/actions",
            headers=human,
            json={
                "expected_revision": another["revision"],
                "action": "confirm",
                "reviewed_evidence_ids": [],
            },
        )
        assert result.status_code == 409, result.text
        assert result.json()["data"]["error"]["code"] == "memory_input_stale"


async def test_split_drafting_records_every_admitted_batch_and_success_lineage(tenants, tmp_path):
    from app.models.response_cards import CardGenerationRun
    from sqlalchemy import select
    from test_llm_providers import provider_reply
    from test_memory_api import approved_rule

    async with drafting_client(tenants, tmp_path) as (api, app, headers, vendor, _):
        task, _, extraction, requirements = await create_tender(api, app, headers[0], tmp_path)
        await approved_rule(api, headers[0], "batch.rule", "Use concise wording", ["drafting"])

        async def respond(sent):
            if len(sent["requirements"]) > 1:
                return provider_reply("openai", [{"invalid": "synthetic split trigger"}])
            return provider_reply("openai", vendor.proposals(sent))

        vendor.respond = respond
        receipt = await submit(
            api,
            headers[0],
            task,
            extraction,
            requirement_ids=[requirements[2]["id"], requirements[3]["id"]],
        )
        terminal = await execute(api, app, headers[0], receipt)
        assert terminal["data"]["status"] == "succeeded", terminal
        response = await api.get(f"/jobs/{receipt['data']['job_id']}/memory", headers=headers[0])
        assert response.status_code == 200, response.text
        calls = response.json()["data"]["calls"]
        assert sorted(len(item["requirement_ids"]) for item in calls) == [1, 1, 2]
        assert len({item["call_id"] for item in calls}) == 3
        assert all(item["state"] == "completed" and item["usage_record_id"] for item in calls)
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            run = await session.scalar(
                select(CardGenerationRun).where(
                    CardGenerationRun.generation_job_id == UUID(receipt["data"]["job_id"])
                )
            )
            assert run is not None
            successful_calls = set(run.input_manifest["requirement_calls"].values())
            assert successful_calls == {
                item["call_id"] for item in calls if len(item["requirement_ids"]) == 1
            }
