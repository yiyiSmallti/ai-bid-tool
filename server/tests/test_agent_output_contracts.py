"""Command-output acceptance, specified before implementing output discovery.

Failure matrix: discovery omits data/items/error or preview/receipt/terminal
branches; typed payloads reject real review metadata or accept invented fields;
partial/failed/cancelled jobs lose their result; schema output changes leave an
old agent executable; schema generation changes legacy business responses;
nine full schemas exceed provider byte limits, or sharing definitions changes
the meaning of local references or the hashed contract.
Run with disposable PostgreSQL and fake Providers only. Artifacts contain
synthetic Results and discovery schemas, never real documents or credentials.
"""

import asyncio
import copy
import importlib
import json
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.errors import ServiceError
from app.schemas.agent_contracts import ReqListCall
from app.services import agent_tools
from bid_cli import schema as discovery
from bid_cli.main import main
from test_agent_tools import context
from test_card_generation import drafting_client
from test_response_cards import create_tender, select_real_materials

TOOLS = (
    "req list",
    "card list",
    "card show",
    "card generate",
    "draft",
    "draft show",
    "job status",
    "source search",
    "source read",
)


async def test_cli_discovery_and_output_changes_fence_existing_agent(capsys, monkeypatch, tmp_path):
    main(["schema", "--json"])
    emitted = json.loads(capsys.readouterr().out)["data"]
    for name in TOOLS:
        entry = emitted["commands"][name]
        assert "output" in entry and "items" in entry and "result" in entry
        assert set(entry["result"]["properties"]) == {
            "ok",
            "command",
            "data",
            "items",
            "warnings",
            "cost",
            "duration_ms",
        }
        assert "error" in json.dumps(entry["result"])
    actor, principal, state, step = context()
    original = await agent_tools.definitions(principal)
    state.tool_schema_sha256 = step.schema_sha256 = agent_tools.schema_digest(original)
    original_schema = discovery.command_schema

    def changed_schema(*args, **kwargs):
        schema = copy.deepcopy(original_schema(*args, **kwargs))
        schema["commands"]["req list"]["result"]["properties"]["data"]["description"] = (
            "Synthetic output-contract revision"
        )
        return schema

    monkeypatch.setattr(discovery, "command_schema", changed_schema)
    changed = await agent_tools.definitions(principal)
    assert original[0].invocation_input == changed[0].invocation_input
    assert agent_tools.schema_digest(original) != agent_tools.schema_digest(changed)
    call = ReqListCall(
        command="req list", arguments={"task": state.task_id, "job": state.extraction_job_id}
    )
    with pytest.raises(ServiceError) as error:
        await agent_tools.validate_call(None, actor, state, step, call)
    assert error.value.code == "tool_schema_changed"
    (tmp_path / "output-schema-fence.json").write_text(
        json.dumps(
            {
                "commands": list(TOOLS),
                "old_hash": state.tool_schema_sha256,
                "new_hash": agent_tools.schema_digest(changed),
                "error": error.value.code,
            },
            indent=2,
        )
    )


async def test_actual_tool_results_match_discovery(database, tmp_path, monkeypatch, capsys):
    from app.schemas.tool_outputs import AGENT_JOB_RESULT, TOOL_RESULTS

    captured = []

    def capture(name, response):
        assert response.status_code in {200, 404}, response.text
        body = response.json()
        TOOL_RESULTS[name].validate_python(body)
        if name == "job status":
            AGENT_JOB_RESULT.validate_python(body)
        captured.append({"command": name, "result": body})
        return body

    async with drafting_client(database, tmp_path) as (api, app, header, vendor, _):
        task, _, extraction, _ = await create_tender(api, app, header, tmp_path, confirmed=True)
        await select_real_materials(api, header, task, tmp_path)
        capture(
            "req list",
            await api.get(
                f"/v4/tasks/{task}/requirements", headers=header, params={"job": extraction}
            ),
        )
        capture(
            "card list",
            await api.get(f"/v4/tasks/{task}/cards", headers=header, params={"job": extraction}),
        )
        capture("job status", await api.get(f"/v4/jobs/{extraction}", headers=header))
        capture(
            "card generate",
            await api.post(
                f"/v4/tasks/{task}/cards/generations",
                headers=header,
                json={"extraction_job_id": extraction, "dry_run": True},
            ),
        )
        receipt = capture(
            "card generate",
            await api.post(
                f"/v4/tasks/{task}/cards/generations",
                headers=header,
                json={"extraction_job_id": extraction},
            ),
        )
        capture(
            "job status", await api.get(f"/v4/jobs/{receipt['data']['job_id']}", headers=header)
        )
        await app.state.processor(receipt["data"]["job_id"])
        generated = capture(
            "job status", await api.get(f"/v4/jobs/{receipt['data']['job_id']}", headers=header)
        )
        slots = capture(
            "card list",
            await api.get(f"/v4/tasks/{task}/cards", headers=header, params={"job": extraction}),
        )["items"]
        card = slots[0]["card"]
        capture("card show", await api.get(f"/v4/cards/{card['id']}", headers=header))
        capture(
            "card show",
            await api.get(f"/v4/cards/{card['id']}", headers=header, params={"history": True}),
        )
        for dry_run in (True, False):
            draft = capture(
                "draft",
                await api.post(
                    f"/v4/tasks/{task}/drafts",
                    headers=header,
                    json={"extraction_job_id": extraction, "dry_run": dry_run},
                ),
            )
        await app.state.processor(draft["data"]["job_id"])
        terminal = capture(
            "job status", await api.get(f"/v4/jobs/{draft['data']['job_id']}", headers=header)
        )
        assert terminal["data"]["result"]["completion"] == "partial"
        capture(
            "draft show",
            await api.get(f"/v4/drafts/{terminal['data']['result']['draft_id']}", headers=header),
        )
        capture("card show", await api.get(f"/v4/cards/{uuid4()}", headers=header))

        # Drive the actual CLI entry against captured real API results, including
        # --wait merge behavior and its process exit for partial work.
        cli = importlib.import_module("bid_cli.main")
        selectors = {
            "req list": ["--task", task, "--job", extraction],
            "card list": ["--task", task, "--job", extraction],
            "card show": ["--id", card["id"]],
            "card generate": ["--task", task, "--job", extraction],
            "draft": ["--task", task, "--job", extraction],
            "draft show": ["--id", terminal["data"]["result"]["draft_id"]],
            "job status": [receipt["data"]["job_id"]],
        }
        for item in list(captured):
            name, body = item["command"], item["result"]
            monkeypatch.setattr(cli, "call", lambda *a, _body=body, **kw: copy.deepcopy(_body))
            args = name.split() + selectors[name] + ["--json"]
            if body["data"].get("dry_run"):
                args += ["--dry-run"]
            try:
                main(args)
            except SystemExit:
                pass
            emitted = json.loads(capsys.readouterr().out)
            TOOL_RESULTS[name].validate_python(emitted)
            assert emitted["data"] == body["data"]
        for name, accepted, finished in (
            ("card generate", receipt, generated),
            ("draft", draft, terminal),
        ):
            monkeypatch.setattr(cli, "call", lambda *a, _body=accepted, **kw: copy.deepcopy(_body))

            async def wait(*args, _body=finished):
                return copy.deepcopy(_body)

            monkeypatch.setattr(cli, "wait_for_job", wait)

            # CLI owns its synchronous event loop, so invoke it in a worker thread.
            def invoke_wait(_name=name):
                try:
                    main(_name.split() + selectors[_name] + ["--wait", "--json"])
                except SystemExit:
                    pass

            await asyncio.to_thread(invoke_wait)
            body = json.loads(capsys.readouterr().out)
            TOOL_RESULTS[name].validate_python(body)
            captured.append({"command": name, "result": body})

        # Distinct input selections create new real jobs; cancelled jobs never
        # reach the fake provider, and failure uses only MockTransport.
        cancelled = capture(
            "card generate",
            await api.post(
                f"/v4/tasks/{task}/cards/generations",
                headers=header,
                json={
                    "extraction_job_id": extraction,
                    "requirement_ids": [slots[0]["requirement_id"]],
                },
            ),
        )
        response = await api.post(f"/v4/jobs/{cancelled['data']['job_id']}/cancel", headers=header)
        assert response.status_code == 200, response.text
        body = capture(
            "job status", await api.get(f"/v4/jobs/{cancelled['data']['job_id']}", headers=header)
        )
        assert body["data"]["status"] == "cancelled"

        async def provider_failure(sent):
            return httpx.Response(400, json={"error": {"message": "Synthetic rejection"}})

        vendor.respond = provider_failure
        failed = capture(
            "card generate",
            await api.post(
                f"/v4/tasks/{task}/cards/generations",
                headers=header,
                json={
                    "extraction_job_id": extraction,
                    "requirement_ids": [slots[1]["requirement_id"]],
                },
            ),
        )
        await app.state.processor(failed["data"]["job_id"])
        body = capture(
            "job status", await api.get(f"/v4/jobs/{failed['data']['job_id']}", headers=header)
        )
        assert body["data"]["status"] == "failed"
        (tmp_path / "tool-output-results.json").write_text(json.dumps(captured, indent=2))


async def test_agent_checkpoint_is_a_typed_job_result(database, tmp_path):
    from app.models.agent import AgentJobLink
    from app.schemas.tool_outputs import AGENT_JOB_RESULT
    from sqlalchemy import select
    from test_agent_workflow import advance, decision_reply, start_body, workflow_client

    async with workflow_client(database, tmp_path) as (api, app, header, vendor, _):
        task, _, extraction, _ = await create_tender(api, app, header, tmp_path, confirmed=True)

        async def respond(sent):
            return decision_reply(
                {"kind": "complete", "summary": "Synthetic completion.", "output_refs": []}
            )

        vendor.respond = respond
        started = await api.post(
            f"/v4/tasks/{task}/agent-sessions", headers=header, json=start_body(extraction)
        )
        assert started.status_code == 200, started.text
        session_id = started.json()["data"]["session"]["id"]
        await advance(api, app, header, session_id)
        async with app.state.db.transaction() as db:
            job_id = await db.scalar(
                select(AgentJobLink.job_id).where(
                    AgentJobLink.session_id == UUID(session_id), AgentJobLink.role == "controller"
                )
            )
        response = await api.get(f"/v4/jobs/{job_id}", headers=header)
        assert response.status_code == 200, response.text
        AGENT_JOB_RESULT.validate_python(response.json())
        (tmp_path / "agent-checkpoint-result.json").write_text(
            json.dumps(response.json(), indent=2)
        )


async def test_all_scopes_fit_provider_and_shared_schemas_are_lossless(tmp_path):
    from app.providers.agent import HTTPAgentProvider
    from app.providers.llm import OpenAICompatibleExtractor
    from app.providers.quotes import serialized_request
    from test_agent_provider import request, settings_for

    _, principal, _, _ = context()
    tools = await agent_tools.definitions(principal)
    assert len(tools) == len(TOOLS)
    complete = [tool.model_dump(mode="json") for tool in tools]
    decision = request("合成输入" * 2000)
    decision.tools = tools
    provider = HTTPAgentProvider(OpenAICompatibleExtractor(settings_for(tmp_path, "openai")))
    body = provider.request_body(decision)
    wire_bytes = len(serialized_request(body))
    assert wire_bytes <= 128 * 1024
    wire = json.loads(body["messages"][-1]["content"])
    catalog = wire["result_schema_catalog"]
    prefix = catalog["$id"] + "#/$defs/"

    def restore(value, names):
        if isinstance(value, dict):
            return {
                key: "#/$defs/" + names[child.removeprefix(prefix)]
                if key == "$ref" and isinstance(child, str) and child.startswith(prefix)
                else {
                    **restore(child, names),
                    "mapping": {
                        tag: "#/$defs/" + names[pointer.removeprefix(prefix)]
                        for tag, pointer in child["mapping"].items()
                    },
                }
                if key == "discriminator" and isinstance(child, dict) and "mapping" in child
                else restore(child, names)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [restore(child, names) for child in value]
        return value

    def resolve_discriminators(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "discriminator":
                    for pointer in child.get("mapping", {}).values():
                        assert pointer.startswith(prefix)
                        assert pointer.removeprefix(prefix) in catalog["$defs"]
                resolve_discriminators(child)
        elif isinstance(value, list):
            for child in value:
                resolve_discriminators(child)

    resolve_discriminators(wire)

    for original, sent in zip(complete, wire["tools"], strict=True):
        full = original["result_schema"]
        names = {
            key: key.split("__", 1)[0]
            for key in catalog["$defs"]
            if key.split("__", 1)[0] in full.get("$defs", {})
        }
        restored = restore(sent["result_schema"], names)
        restored["$defs"] = {
            name: restore(catalog["$defs"][key], names) for key, name in names.items()
        }
        assert restored == full
        assert sent["schema_sha256"] == original["schema_sha256"]
    assert [tool.model_dump(mode="json") for tool in tools] == complete
    (tmp_path / "provider-schema-budget.json").write_text(
        json.dumps(
            {
                "bytes": wire_bytes,
                "limit": 128 * 1024,
                "commands": list(TOOLS),
                "shared_definitions": len(catalog["$defs"]),
                "lossless": True,
            },
            indent=2,
        )
    )
