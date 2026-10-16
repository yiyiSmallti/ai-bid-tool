"""Gateway company and personal models, member defaults, quotas and usage.

Failure matrix before implementation: a member's personal model reuses the
company key or reaches an unlisted endpoint; another member or an
administrator sees, tests, defaults to or edits someone's personal model; a
member writes or tests a company model; a personal model becomes the company
default; a run without a model ignores the member's default; a personal call
counts against the company quota; parallel jobs at the cap each see room and
together exceed it; usage attributes calls to the wrong member or names a
member's personal model to an administrator. Artifacts are synthetic and
reproducible with --basetemp=data/work/member-models.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.models.entities import VendorCall
from conftest import FakeQueue
from sqlalchemy import select, text
from test_api import create_document, run_job
from test_llm_providers import GOOD_ITEMS, provider_reply
from test_members import GATEWAY, bootstrap_admin, code, run_cli, start_gateway
from test_project_visibility import new_member, signed_in
from test_provider_config import KEY, local_config

PERSONAL_KEY = "synthetic-personal-key-2468"


class GatedVendor:
    """Answers every call, holding replies while `gate` is clear."""

    def __init__(self):
        self.keys: list[str | None] = []
        self.gate = asyncio.Event()
        self.gate.set()
        self.waiting = 0

    async def __call__(self, request):
        self.keys.append(request.headers.get("authorization"))
        self.waiting += 1
        try:
            await self.gate.wait()
        finally:
            self.waiting -= 1
        return provider_reply("openai", GOOD_ITEMS)


@pytest.fixture
async def gateway(database, tmp_path, monkeypatch):
    for name, value in GATEWAY.items():
        monkeypatch.setenv(name, value)
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO public.installation (mode, company_name)"
                " VALUES ('enterprise', 'Synthetic Company')"
            )
        )
    vendor = GatedVendor()
    # The `database` fixture's BID_SECRETS_KEY, which the CLI's gateway process reads too.
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings, queue=FakeQueue(), llm_transport=httpx.MockTransport(vendor))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        yield app, api, vendor


async def listed(api, header):
    response = await api.get("/providers", headers=header)
    assert response.status_code == 200, response.text
    return {row["slot"]: row["owner"] for row in response.json()["items"]}


async def job_calls(app, job):
    async with app.state.db.transaction() as session:
        rows = (
            await session.scalars(select(VendorCall).where(VendorCall.job_id == UUID(job)))
        ).all()
        return [
            (
                row.member_id,
                row.provider_identity["input_tokens_upper_bound"]
                + row.provider_identity["output_tokens_upper_bound"],
            )
            for row in rows
        ]


async def test_member_models_quota_and_usage(gateway, tmp_path, pdf_bytes):
    app, api, vendor = gateway
    report: dict = {
        "rerun": "pytest -q server/tests/test_member_models.py --basetemp=data/work/member-models"
    }
    root = bootstrap_admin("root.admin")
    admin = await signed_in(api, "root.admin", root["initial_password"])
    alice_id, alice = await new_member(api, admin, "alice.one")
    bob_id, bob = await new_member(api, admin, "bob.two")

    company = await api.post("/providers", headers=admin, json=local_config())
    assert company.status_code == 200, company.text
    company = company.json()["data"]
    assert company["owner"] == "company" and company["is_default"]

    personal = {**local_config(owner="personal"), "new": True}
    personal.pop("api_key")
    # A member writes only personal models, never with the company key or an
    # endpoint off the allowlist.
    report["writes"] = {
        "company_by_member": code(await api.post("/providers", headers=alice, json=local_config())),
        "personal_reusing_company_key": code(
            await api.post("/providers", headers=alice, json=personal)
        ),
        "personal_unlisted_endpoint": code(
            await api.post(
                "/providers",
                headers=alice,
                json={
                    **personal,
                    "api_key": PERSONAL_KEY,
                    "base_url": "https://llm.example.test/v1",
                },
            )
        ),
        "company_test_by_member": code(
            await api.post("/providers/test", headers=alice, json={"capability": "llm_extract"})
        ),
    }
    assert report["writes"] == {
        "company_by_member": [403, "forbidden"],
        "personal_reusing_company_key": [400, "provider_key_required"],
        "personal_unlisted_endpoint": [400, "provider_endpoint_not_allowed"],
        "company_test_by_member": [403, "forbidden"],
    }, report["writes"]
    own = await api.post("/providers", headers=alice, json={**personal, "api_key": PERSONAL_KEY})
    assert own.status_code == 200, own.text
    own = own.json()["data"]
    assert own["owner"] == "personal" and not own["is_default"]
    slot = own["slot"]

    # Only the owner sees, defaults to or changes a personal model.
    report["visible"] = {
        "alice": await listed(api, alice),
        "bob": await listed(api, bob),
        "admin": await listed(api, admin),
    }
    assert report["visible"] == {
        "alice": {company["slot"]: "company", slot: "personal"},
        "bob": {company["slot"]: "company"},
        "admin": {company["slot"]: "company"},
    }
    hints = {}
    for name, header in (("alice", alice), ("admin", admin)):
        shown = (await api.get("/management/providers", headers=header)).json()["data"]
        hints[name] = {
            "models": sorted(row["slot"] for row in shown["models"]),
            "actions": {row["action"]: row["reason"] for row in shown["actions"]},
        }
    report["management"] = hints
    assert hints == {
        "alice": {
            "models": sorted([company["slot"], slot]),
            "actions": {"configure": None, "test": "scope_required"},
        },
        "admin": {
            "models": [company["slot"]],
            "actions": {"configure": None, "test": None},
        },
    }, hints
    report["others"] = {
        "bob_default": code(
            await api.put("/account/default-model", headers=bob, json={"slot": slot})
        ),
        "admin_edit": code(
            await api.post(
                "/providers",
                headers=admin,
                json={**local_config(), "slot": slot, "expected_revision": 1},
            )
        ),
        "company_default": code(await api.post(f"/providers/models/{slot}/default", headers=alice)),
    }
    assert report["others"] == {
        "bob_default": [404, "provider_model_unavailable"],
        "admin_edit": [404, "provider_model_unavailable"],
        "company_default": [403, "forbidden"],
    }, report["others"]

    # A run without a model uses the member's default; the personal call is not capped.
    chosen = await api.put("/account/default-model", headers=alice, json={"slot": slot})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["data"] == {"default_slot": slot, "effective_slot": slot}
    capped = await api.put(
        f"/members/{alice_id}/quota", headers=admin, json={"monthly_company_tokens": 0}
    )
    assert capped.status_code == 200, capped.text
    _, document = await create_document(api, alice, pdf_bytes)
    await run_job(api, app, alice, document, "parse")
    job, status = await run_job(api, app, alice, document, "extract")
    assert status["status"] == "succeeded", status
    assert vendor.keys[-1] == f"Bearer {PERSONAL_KEY}"
    personal_calls = await job_calls(app, job)
    assert {member for member, _ in personal_calls} == {UUID(alice_id)}

    # Back on the company model, one job measures the bound each call reserves.
    cleared = await api.put("/account/default-model", headers=alice, json={"slot": None})
    assert cleared.json()["data"] == {"default_slot": None, "effective_slot": company["slot"]}
    await api.put(
        f"/members/{alice_id}/quota", headers=admin, json={"monthly_company_tokens": None}
    )
    job, status = await run_job(api, app, alice, document, "extract")
    assert status["status"] == "succeeded" and vendor.keys[-1] == f"Bearer {KEY}"
    calls = await job_calls(app, job)
    assert len(calls) == 1, calls
    bound = calls[0][1]

    # Quota race: parallel jobs at the cap admit exactly the calls within it.
    usage = await api.get(f"/members/{alice_id}/usage", headers=alice)
    used = usage.json()["data"]["company_tokens"]
    within, parallel = 2, 5
    await api.put(
        f"/members/{alice_id}/quota",
        headers=admin,
        json={"monthly_company_tokens": used + within * bound},
    )
    jobs = []
    for _ in range(parallel):
        # A fresh document each, since an extraction is cached per document and model.
        _, fresh = await create_document(api, alice, pdf_bytes)
        await run_job(api, app, alice, fresh, "parse")
        queued = await api.post(f"/documents/{fresh}/extract", headers=alice, json={})
        assert queued.status_code == 200, queued.text
        jobs.append(queued.json()["data"]["job_id"])
    vendor.gate.clear()
    running = [asyncio.create_task(app.state.processor(job)) for job in jobs]
    # Hold admitted calls in flight until every other job has been refused, so
    # no settled receipt frees room for a late admission.
    for _ in range(600):
        done = sum(task.done() for task in running)
        if vendor.waiting == within and done == parallel - within:
            break
        await asyncio.sleep(0.05)
    report["race_in_flight"] = {"waiting": vendor.waiting, "refused": done}
    assert report["race_in_flight"] == {"waiting": within, "refused": parallel - within}
    vendor.gate.set()
    await asyncio.gather(*running)
    outcomes = []
    for job in jobs:
        shown = (await api.get(f"/jobs/{job}", headers=alice)).json()["data"]
        outcomes.append([shown["status"], (shown.get("error") or {}).get("code")])
    report["race"] = sorted(outcomes, key=str)
    assert sorted(outcomes, key=str) == sorted(
        [["succeeded", None]] * within
        + [["failed", "member_quota_exceeded"]] * (parallel - within),
        key=str,
    ), outcomes

    # Usage: by owner and model for the member, without the personal model's
    # name for an administrator; nobody else reads it.
    month = datetime.now(UTC).strftime("%Y-%m")
    mine = (await api.get(f"/members/{alice_id}/usage", headers=alice)).json()
    theirs = (
        await api.get(f"/members/{alice_id}/usage", headers=admin, params={"month": month})
    ).json()
    report["usage"] = {
        "alice": [[row["owner"], row["slot"], row["calls"]] for row in mine["items"]],
        "admin": [[row["owner"], row["slot"], row["calls"]] for row in theirs["items"]],
        "bob": code(await api.get(f"/members/{alice_id}/usage", headers=bob)),
        "bad_month": code(
            await api.get(f"/members/{alice_id}/usage", headers=admin, params={"month": "2026-13"})
        ),
    }
    assert report["usage"]["alice"] == [
        ["company", company["slot"], 1 + within],
        ["personal", slot, len(personal_calls)],
    ]
    assert report["usage"]["admin"] == [
        ["company", company["slot"], 1 + within],
        ["personal", None, len(personal_calls)],
    ]
    assert report["usage"]["bob"] == [403, "forbidden"]
    assert report["usage"]["bad_month"] == [422, "invalid_input"]
    assert mine["data"]["company_tokens"] <= mine["data"]["monthly_company_tokens"]
    assert theirs["data"]["company_tokens"] == mine["data"]["company_tokens"]

    # The CLI sets and reads quotas with an administrator's `bid_` token.
    token = await api.post(
        "/tokens",
        headers=admin,
        json={
            "name": "synthetic quota cli",
            "scopes": ["member:manage"],
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert token.status_code == 200, token.text
    secret = token.json()["data"]["token"]
    server, address = start_gateway(tmp_path)
    try:
        set_status, set_body = run_cli(
            ["member", "quota", "--member", bob_id, "--tokens", "1000"], address, secret
        )
        read_status, read_body = run_cli(
            ["member", "usage", "--member", alice_id, "--month", month], address, secret
        )
        clear_status, clear_body = run_cli(
            ["member", "quota", "--member", bob_id, "--clear"], address, secret
        )
    finally:
        server.terminate()
        server.wait(timeout=30)
    report["cli"] = {
        "quota": [set_status, set_body["data"]["monthly_company_tokens"]],
        "usage": [read_status, len(read_body["items"])],
        "clear": [clear_status, clear_body["data"]["monthly_company_tokens"]],
    }
    assert report["cli"] == {"quota": [0, 1000], "usage": [0, 2], "clear": [0, None]}
    await asyncio.to_thread(
        (tmp_path / "member-models.json").write_text,
        json.dumps(report, indent=2, sort_keys=True, default=str),
    )
