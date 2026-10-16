"""Gateway private memory: one member's, seen and changed by that member only.

Failure matrix before implementation: another member or an administrator sees
a private row in show, list, history, management browse or detail, a saved
retrieval, a drafting prompt or a job's memory references; drafting omits the
acting member's private memory; changing private memory marks every member's
drafting inputs stale instead of the owner's; a member without `memory:edit`
changes public memory, or is refused their own private memory; a database
write files private memory under another member; a personal installation
accepts the private scope. Artifacts are synthetic and reproducible with
--basetemp=data/work/private-memory.
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.providers.llm import OpenAICompatibleExtractor
from conftest import FakeQueue, credential_app
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_card_generation import DraftVendor, execute, submit
from test_llm_providers import settings_for
from test_members import GATEWAY, bootstrap_admin, code, run_cli, start_gateway
from test_project_visibility import new_member, signed_in
from test_response_cards import create_tender


@pytest.fixture
async def gateway(database, tmp_path, monkeypatch):
    for name, value in GATEWAY.items():
        monkeypatch.setenv(name, value)
    settings = settings_for(tmp_path, "openai")
    vendor = DraftVendor("openai", None)
    llm = OpenAICompatibleExtractor(settings, httpx.MockTransport(vendor))
    # The drafting provider is stored first: a gateway write needs a member.
    application = await credential_app(settings, llm=llm, queue=FakeQueue())
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO public.installation (mode, company_name)"
                " VALUES ('enterprise', 'Synthetic Company')"
            )
        )
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client,
    ):
        yield client, application, vendor


def rule(key, words):
    return {"kind": "rule", "conflict_key": key, "text": words, "tags": ["drafting"]}


async def approved(api, header, scope, content):
    created = await api.post(
        "/memories", headers=header, json={"target": {"scope": scope}, "content": content}
    )
    assert created.status_code == 200, created.text
    memory = created.json()["data"]["memory"]
    decision = await api.post(
        f"/memories/{memory['id']}/decisions",
        headers=header,
        json={"expected_revision": 1, "action": "approve", "reason": "Synthetic review"},
    )
    assert decision.status_code == 200, decision.text
    return decision.json()["data"]["memory"]


async def retrieved(api, header, scopes=("library", "private")):
    saved = await api.post(
        "/memories/retrieve",
        headers=header,
        json={"scopes": list(scopes), "query": "response wording"},
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


async def valid(api, header, retrieval):
    shown = await api.get(f"/memory-retrievals/{retrieval}", headers=header)
    assert shown.status_code == 200, shown.text
    return shown.json()["data"]["currently_valid"]


async def sightings(api, header, memory):
    """What `header` learns of `memory` through every memory read."""
    browse = await api.post(
        "/management/memories/query", headers=header, json={"scope": "private", "limit": 50}
    )
    return {
        "show": code(await api.get(f"/memories/{memory}", headers=header)),
        "history": code(await api.get(f"/memories/{memory}/history", headers=header)),
        "management": code(await api.get(f"/management/memories/{memory}", headers=header)),
        "list": [
            row["id"]
            for row in (
                await api.get("/memories", headers=header, params={"scope": "private"})
            ).json()["items"]
        ],
        "browse": [row["id"] for row in browse.json()["items"]],
        "retrieve": sorted(
            item["memory"]["memory_id"] for item in (await retrieved(api, header))["items"]
        ),
    }


async def test_private_memory_is_the_owners_alone(gateway, database, tmp_path):
    api, application, vendor = gateway
    report = {
        "rerun": "pytest -q server/tests/test_private_memory.py --basetemp=data/work/private-memory"
    }
    root = bootstrap_admin("root.admin")
    admin = await signed_in(api, "root.admin", root["initial_password"])
    owner_id, owner = await new_member(api, admin, "owner.one", ["memory:edit"])
    other_id, other = await new_member(api, admin, "other.two")

    public = await approved(
        api, owner, "library", rule("public.wording", "Public response wording")
    )
    private = await approved(
        api, owner, "private", rule("private.wording", "Private response wording")
    )
    assert private["current"]["target"]["scope"] == "private"

    # Nobody else learns of it, an administrator included.
    report["owner"] = await sightings(api, owner, private["id"])
    assert report["owner"] == {
        "show": [200, None],
        "history": [200, None],
        "management": [200, None],
        "list": [private["id"]],
        "browse": [private["id"]],
        "retrieve": sorted([public["id"], private["id"]]),
    }
    for name, header in (("other", other), ("admin", admin)):
        report[name] = await sightings(api, header, private["id"])
        assert report[name] == {
            "show": [404, "not_found"],
            "history": [404, "not_found"],
            "management": [404, "not_found"],
            "list": [],
            "browse": [],
            "retrieve": [public["id"]],
        }, name

    # Public memory needs the grant; one's own private memory never does.
    report["other_writes"] = {
        "public": code(
            await api.post(
                "/memories",
                headers=other,
                json={"target": {"scope": "library"}, "content": rule("x", "Ungranted")},
            )
        ),
        "public_disable": code(
            await api.post(
                f"/memories/{public['id']}/disable",
                headers=other,
                json={"expected_revision": public["current"]["revision"], "reason": "Ungranted"},
            )
        ),
    }
    assert report["other_writes"] == {
        "public": [403, "forbidden"],
        "public_disable": [403, "forbidden"],
    }
    own = await approved(api, other, "private", rule("private.wording", "Other response wording"))

    # A saved retrieval holding private text is the owner's alone; a private
    # change makes only the owner's retrievals stale.
    mine = (await retrieved(api, owner))["data"]
    theirs = (await retrieved(api, other))["data"]
    assert [item["scope"] for item in mine["epochs"]] == ["library", "private"]
    assert mine["epochs"][1]["owner_member_id"] == owner_id
    report["retrieval_show"] = {
        "other": code(await api.get(f"/memory-retrievals/{mine['retrieval_id']}", headers=other)),
        "admin": code(await api.get(f"/memory-retrievals/{mine['retrieval_id']}", headers=admin)),
    }
    assert report["retrieval_show"] == {"other": [404, "not_found"], "admin": [404, "not_found"]}
    disabled = await api.post(
        f"/memories/{own['id']}/disable",
        headers=other,
        json={"expected_revision": own["current"]["revision"], "reason": "Retired"},
    )
    assert disabled.status_code == 200, disabled.text
    report["stale_after_other_change"] = {
        "owner": await valid(api, owner, mine["retrieval_id"]),
        "other": await valid(api, other, theirs["retrieval_id"]),
    }
    assert report["stale_after_other_change"] == {"owner": True, "other": False}

    # Drafting sends the acting member's private memory; a project mate sees
    # that public memory was used, never the private reference.
    task, _, extraction, _ = await create_tender(api, application, owner, tmp_path)
    receipt = await submit(api, owner, task, extraction)
    await execute(api, application, owner, receipt)
    sent = {item["text"] for draft in vendor.drafts for item in draft["memory_rules"]}
    assert {"Public response wording", "Private response wording"} <= sent
    assert "Other response wording" not in sent
    joined = await api.post(
        f"/tasks/{task}/members", headers=owner, json={"member_ids": [other_id]}
    )
    assert joined.status_code == 200, joined.text
    job = receipt["data"]["job_id"]
    report["job_memory_scopes"] = {}
    for name, header in (("owner", owner), ("other", other), ("admin", admin)):
        used = await api.get(f"/jobs/{job}/memory", headers=header)
        assert used.status_code == 200, used.text
        report["job_memory_scopes"][name] = sorted(
            {ref["scope"] for call in used.json()["data"]["calls"] for ref in call["memories"]}
        )
    assert report["job_memory_scopes"] == {
        "owner": ["library", "private"],
        "other": ["library"],
        "admin": ["library"],
    }

    # The database files private memory under the acting member only.
    with pytest.raises(DBAPIError, match="Private memory belongs to the acting member"):
        with database.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.actor_member_id', :member, true)"),
                {"member": other_id},
            )
            connection.execute(
                text(
                    "INSERT INTO memories (id, scope, owner_member_id, current_revision_id,"
                    " revision) SELECT gen_random_uuid(), scope, owner_member_id,"
                    " current_revision_id, 1 FROM memories WHERE id=:memory"
                ),
                {"memory": private["id"]},
            )
    report["database_owner_check"] = "rejected"

    # The CLI lists and browses private memory with a member's `bid_` token.
    browse = tmp_path / "browse.json"
    browse.write_text(json.dumps({"scope": "private", "limit": 50}))
    tokens = {}
    for name, header in (("owner", owner), ("other", other)):
        minted = await api.post(
            "/tokens",
            headers=header,
            json={
                "name": "synthetic memory cli",
                "scopes": ["memory:read"],
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        )
        assert minted.status_code == 200, minted.text
        tokens[name] = minted.json()["data"]["token"]
    server, address = start_gateway(tmp_path)
    try:
        report["cli"] = {
            name: {
                "list": [
                    row["id"]
                    for row in run_cli(["memory", "list", "--scope", "private"], address, token)[1][
                        "items"
                    ]
                ],
                "browse": [
                    row["id"]
                    for row in run_cli(
                        ["memory", "browse", "--input", str(browse)], address, token
                    )[1]["items"]
                ],
            }
            for name, token in tokens.items()
        }
    finally:
        server.terminate()
        server.wait(timeout=30)
    assert report["cli"] == {
        "owner": {"list": [private["id"]], "browse": [private["id"]]},
        "other": {"list": [own["id"]], "browse": [own["id"]]},
    }
    (tmp_path / "private-memory.json").write_text(json.dumps(report, indent=2, sort_keys=True))
