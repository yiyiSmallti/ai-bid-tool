"""Tender source tools through the API and a built-in agent session.

Failure matrix before implementation: a search misses a line or returns text
from another document; a long chunk is returned whole instead of in bounded
pages; a confidential value reaches the result or can be found by searching
for it; an agent reads a chunk of another task; a Word block cannot be
addressed; a question-only session loses its answer. Artifacts are synthetic
and reproducible with --basetemp=data/work/agent-source-tools.
"""

import json
from uuid import UUID, uuid4

from app.models.agent import AgentMessage, AgentStep
from sqlalchemy import select
from test_agent_workflow import advance, decision_reply, start_body, workflow_client
from test_bid_review_run_db import add_field, set_value
from test_product_simulation import tender_docx
from test_response_cards import create_tender, run_document_job

ESCALATION = "The support team shall provide a named escalation contact."


async def search(api, header, document, query, **params):
    response = await api.get(
        f"/documents/{document}/source/search",
        headers=header,
        params={"query": query, **params},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def read(api, header, chunk, **params):
    response = await api.get(f"/chunks/{chunk}/source", headers=header, params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_search_read_bounds_and_redaction(database, tmp_path):
    async with workflow_client(database, tmp_path) as (api, app, header, vendor, llm):
        task, document, _, _ = await create_tender(api, app, header, tmp_path)
        _, other, _, _ = await create_tender(api, app, header, tmp_path, suffix="two")

        found = await search(api, header, document, "THIRTY  calendar")
        assert found["data"] | {"query": None} == {
            "document_id": document,
            "query": None,
            "matches": 1,
            "truncated": False,
        }
        [match] = found["items"]
        assert match["page"] == 3 and match["block_id"] is None and match["cut"] is False
        assert "thirty calendar days" in match["line"]
        assert (await search(api, header, document, "thirty absent"))["items"] == []

        page = await read(api, header, match["chunk_id"])
        assert page["page"] == 3 and page["offset"] == 0 and page["next_offset"] is None
        assert match["line"] in page["text"] and page["length"] == len(page["text"])
        first = await read(api, header, match["chunk_id"], limit=10)
        rest = await read(api, header, match["chunk_id"], offset=first["next_offset"])
        assert first["next_offset"] == 10 and first["text"] + rest["text"] == page["text"]

        limited = await search(api, header, document, "shall", limit=2)
        assert limited["data"]["truncated"] is True and len(limited["items"]) == 2
        assert limited["warnings"] == ["source_search_truncated"]

        # The value is masked before searching, so it can be neither read nor found.
        field = await add_field(api, header, "escalation_name", "联系人", "other", "task")
        await set_value(api, header, field, "named escalation contact", task)
        masked = await search(api, header, document, "support team")
        assert "named escalation contact" not in json.dumps(masked)
        assert "{{secret.escalation_name}}" in masked["items"][0]["line"]
        assert (await search(api, header, document, "named escalation"))["items"] == []
        whole = await read(api, header, masked["items"][0]["chunk_id"])
        assert ESCALATION not in whole["text"]

        foreign = await search(api, header, other, "thirty")
        assert foreign["items"][0]["chunk_id"] != match["chunk_id"]
        missing = await api.get(f"/chunks/{uuid4()}/source", headers=header)
        assert missing.status_code == 404
        bad = await api.get(
            f"/chunks/{match['chunk_id']}/source", headers=header, params={"block": "p9999"}
        )
        assert bad.status_code == 404


async def test_word_blocks_are_addressable(database, tmp_path):
    async with workflow_client(database, tmp_path) as (api, app, header, vendor, llm):
        task = (await api.post("/tasks", headers=header, json={"name": "Word source"})).json()
        uploaded = await api.post(
            f"/tasks/{task['data']['id']}/documents",
            headers=header,
            files={
                "file": (
                    "synthetic-tender.docx",
                    tender_docx(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        document = uploaded.json()["data"]["id"]
        await run_document_job(api, app, header, document, "parse")
        [match] = (await search(api, header, document, "24个千兆"))["items"]
        assert match["page"] is None and match["block_id"] and "采购需求" in match["label"]
        block = await read(api, header, match["chunk_id"], block=match["block_id"])
        assert block["block_id"] == match["block_id"]
        assert block["text"] == "端口不少于24个千兆电口"
        chunk = await read(api, header, match["chunk_id"])
        assert chunk["block_id"] is None and block["text"] in chunk["text"]


async def test_agent_reads_tender_and_keeps_its_answer(database, tmp_path):
    async with workflow_client(database, tmp_path) as (api, app, header, vendor, llm):
        task, document, extraction, _ = await create_tender(
            api, app, header, tmp_path, confirmed=True
        )
        [match] = (await search(api, header, document, "thirty calendar"))["items"]
        answer = f"Delivery: 'thirty calendar days' (chunk {match['chunk_id']}, page 3)."
        actions = [
            {
                "kind": "tool",
                "call": {
                    "command": "source search",
                    "arguments": {"document": document, "query": "thirty calendar"},
                },
                "input_refs": [],
            },
            {
                "kind": "tool",
                "call": {"command": "source read", "arguments": {"chunk": match["chunk_id"]}},
                "input_refs": [],
            },
            {"kind": "complete", "summary": answer, "output_refs": []},
        ]
        requests = []

        async def respond(sent):
            requests.append(sent)
            return decision_reply(actions.pop(0))

        vendor.respond = respond
        body = start_body(extraction)
        body["message"] = "What is the delivery period? Quote the tender."
        started = await api.post(f"/v4/tasks/{task}/agent-sessions", headers=header, json=body)
        assert started.status_code == 200, started.text
        session_id = started.json()["data"]["session"]["id"]
        paused = await advance(api, app, header, session_id)
        # Without a draft the goal stays open, but the human keeps the answer.
        assert paused["session"]["state"] == "paused" and paused["pause"]["action"] == "clarify"
        sent = json.dumps(requests[0], ensure_ascii=False)
        assert '"source search"' in sent and '"source read"' in sent
        async with app.state.db.transaction() as db:
            steps = (
                await db.scalars(
                    select(AgentStep)
                    .where(AgentStep.session_id == UUID(session_id), AgentStep.kind == "tool")
                    .order_by(AgentStep.ordinal)
                )
            ).all()
            messages = (
                await db.scalars(
                    select(AgentMessage)
                    .where(AgentMessage.session_id == UUID(session_id))
                    .order_by(AgentMessage.ordinal)
                )
            ).all()
        assert [(row.command, row.state) for row in steps] == [
            ("source search", "completed"),
            ("source read", "completed"),
        ]
        tool_text = "\n".join(row.content for row in messages if row.role == "tool")
        assert "thirty calendar days" in tool_text and match["chunk_id"] in tool_text
        assert messages[-1].role == "assistant" and messages[-1].content == answer
        (tmp_path / "agent-source-session.json").write_text(
            json.dumps(
                {
                    "steps": [[row.command, row.state, row.error_code] for row in steps],
                    "messages": [[row.role, row.content] for row in messages],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


async def test_agent_cannot_read_another_tasks_chunk(database, tmp_path):
    async with workflow_client(database, tmp_path) as (api, app, header, vendor, llm):
        task, _, extraction, _ = await create_tender(api, app, header, tmp_path, confirmed=True)
        _, other, _, _ = await create_tender(api, app, header, tmp_path, suffix="two")
        [foreign] = (await search(api, header, other, "thirty calendar"))["items"]
        actions = [
            {
                "kind": "tool",
                "call": {"command": "source read", "arguments": {"chunk": foreign["chunk_id"]}},
                "input_refs": [],
            }
        ]

        async def respond(sent):
            return decision_reply(actions.pop(0))

        vendor.respond = respond
        started = await api.post(
            f"/v4/tasks/{task}/agent-sessions", headers=header, json=start_body(extraction)
        )
        session_id = started.json()["data"]["session"]["id"]
        paused = await advance(api, app, header, session_id)
        assert paused["pause"]["kind"] == "authority"
        async with app.state.db.transaction() as db:
            step = await db.scalar(
                select(AgentStep).where(
                    AgentStep.session_id == UUID(session_id), AgentStep.kind == "tool"
                )
            )
            contents = (
                await db.scalars(
                    select(AgentMessage.content).where(AgentMessage.session_id == UUID(session_id))
                )
            ).all()
        assert step is not None and (step.state, step.error_code) == ("failed", "not_found")
        assert not any("thirty calendar" in content for content in contents)
