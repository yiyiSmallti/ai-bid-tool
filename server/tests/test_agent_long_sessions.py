"""Long built-in agent sessions: token budget, stored summaries and their gates.

Failure matrix before implementation: a session stops at a fixed message count;
a decision request grows past its token budget; a summary forgets a card that
waits for human review, or names a resource that does not wait; a summary that
fails validation is stored anyway or loses the original messages; a summary
counts as a confirmation; the summary call is not accounted as a step and a
vendor call. Artifacts are synthetic and reproducible with
--basetemp=data/work/agent-long-sessions.
"""

import json
from uuid import UUID

import httpx
import pymupdf
import test_response_cards
from app.models.agent import AgentMessage, AgentStep
from app.models.entities import UsageRecord
from app.services import agent_context
from sqlalchemy import select
from test_agent_workflow import advance, decision_reply, start_body, workflow_client
from test_response_cards import create_card, create_tender, require_action

FILLER = "synthetic delivery schedule clause " + "kept for the long session check " * 8


def padded_pdf(lines):
    """One requirement line per page followed by many searchable filler lines."""
    with pymupdf.open() as document:
        for line in lines:
            page = document.new_page()
            page.insert_text((40, 60), "SYNTHETIC TEST MATERIAL - NOT A REAL BID OR PROOF")
            page.insert_text((40, 90), line)
            page.insert_textbox(
                pymupdf.Rect(40, 110, 560, 820),
                "\n".join(f"{index}. {FILLER}" for index in range(60)),
                fontsize=4,
            )
        return document.tobytes()


def summary_reply(summary):
    return httpx.Response(
        200,
        json={
            "model": "synthetic-model",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(summary)}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 120},
        },
    )


async def pending_session(api, app, header, tmp_path, monkeypatch, *, max_steps=64):
    monkeypatch.setattr(test_response_cards, "labelled_pdf", padded_pdf)
    task, document, extraction, requirements = await create_tender(
        api, app, header, tmp_path, confirmed=True
    )
    card = await create_card(
        api,
        header,
        task,
        extraction,
        requirements[0],
        {
            "response_kind": None,
            "response_text": None,
            "deviation": None,
            "deviation_note": None,
            "evidence": [],
        },
    )
    card = await require_action(api, header, card, "submit")
    body = start_body(extraction, max_steps=max_steps, max_vendor_calls=64)
    body["message"] = "Survey the delivery clauses, then report what still needs a human."
    started = await api.post(f"/v4/tasks/{task}/agent-sessions", headers=header, json=body)
    assert started.status_code == 200, started.text
    return task, document, card, started.json()["data"]["session"]["id"]


def search(document):
    return {
        "kind": "tool",
        "call": {
            "command": "source search",
            "arguments": {"document": document, "query": "delivery schedule"},
        },
        "input_refs": [],
    }


async def session_rows(app, session_id):
    async with app.state.db.transaction() as db:
        steps = (
            await db.scalars(
                select(AgentStep)
                .where(AgentStep.session_id == UUID(session_id))
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
        usage = (await db.scalars(select(UsageRecord))).all()
    return steps, messages, usage


async def test_long_session_summarizes_and_keeps_gates(database, tmp_path, monkeypatch):
    async with workflow_client(database, tmp_path) as (api, app, header, vendor, llm):
        task, document, card, session_id = await pending_session(
            api, app, header, tmp_path, monkeypatch
        )
        decisions, summaries = [], []

        async def respond(sent):
            if "pending_resources" in sent:
                summaries.append(sent)
                return summary_reply(
                    {
                        "goal": "Survey the delivery clauses and report what needs a human.",
                        "progress": f"Searched delivery clauses; messages {sent['covers_from']}"
                        f"-{sent['covers_to']} summarized.",
                        "open_gaps": ["No delivery clause has been quoted yet."],
                        "pending_human_actions": [
                            {"resource_id": value, "action": "Review the submitted card."}
                            for value in sent["pending_resources"]
                        ],
                    }
                )
            decisions.append(sent)
            # Leave room for one more search and its summary before the ceiling.
            if sent["remaining_steps"] <= 4:
                return decision_reply(
                    {"kind": "complete", "summary": "Survey finished.", "output_refs": []}
                )
            return decision_reply(search(document))

        vendor.respond = respond
        final = await advance(api, app, header, session_id, maximum=400)
        assert final["session"]["state"] == "paused", final
        assert final["pause"]["action"] == "clarify"
        steps, messages, usage = await session_rows(app, session_id)

        assert final["session"]["steps_used"] > 60 and len(messages) > 60
        assert all(step.state == "completed" for step in steps)
        stored = [message for message in messages if message.role == "summary"]
        assert len(stored) == len(summaries) >= 2
        for message in stored:
            assert card["id"] in message.content and "No delivery clause" in message.content
            assert message.covers_from == 1 and message.covers_to < message.ordinal
        # Every decision after the first summary starts from the latest summary
        # and stays within the budget; originals stay stored.
        later = [sent for sent in decisions if sent["messages"][1]["role"] == "summary"]
        assert later
        for sent in decisions:
            tail = [m for m in sent["messages"] if m["role"] not in {"system", "summary"}]
            assert sum(agent_context.estimate(m["content"]) for m in tail) <= (
                agent_context.RECENT_TOKENS
            )
            assert len(json.dumps(sent).encode()) < agent_context.WIRE_BYTES
        summary_steps = [step for step in steps if step.kind == "summary"]
        assert len(summary_steps) == len(stored) and all(step.usage_ids for step in summary_steps)
        assert final["session"]["vendor_calls_used"] == len(decisions) + len(summaries)

        # A summary is context only: the card still waits for its human review.
        current = await api.get(f"/cards/{card['id']}", headers=header)
        assert current.json()["data"]["state"] == "pending_review"

        session_usage = [row for row in usage if row.job_id in {s.created_by_job_id for s in steps}]
        (tmp_path / "agent-long-session.json").write_text(
            json.dumps(
                {
                    "steps_used": final["session"]["steps_used"],
                    "messages": len(messages),
                    "steps": [[s.ordinal, s.kind, s.command, s.state] for s in steps],
                    "summaries": [[m.ordinal, m.covers_from, m.covers_to] for m in stored],
                    "summary_text": stored[-1].content,
                    "usage": {
                        "records": len(session_usage),
                        "input_tokens": sum(row.input_tokens or 0 for row in session_usage),
                        "output_tokens": sum(row.output_tokens or 0 for row in session_usage),
                    },
                    "rerun": "pytest -q server/tests/test_agent_long_sessions.py "
                    "--basetemp=data/work/agent-long-sessions",
                },
                indent=2,
            )
        )


async def test_invalid_summary_pauses_and_keeps_messages(database, tmp_path, monkeypatch):
    async with workflow_client(database, tmp_path) as (api, app, header, vendor, llm):
        task, document, card, session_id = await pending_session(
            api, app, header, tmp_path, monkeypatch
        )

        async def respond(sent):
            if "pending_resources" in sent:
                assert sent["pending_resources"] == [card["id"]]
                # The card that waits for review is missing.
                return summary_reply(
                    {
                        "goal": "Survey the delivery clauses.",
                        "progress": "Searched.",
                        "open_gaps": [],
                        "pending_human_actions": [],
                    }
                )
            return decision_reply(search(document))

        vendor.respond = respond
        paused = await advance(api, app, header, session_id, maximum=200)
        assert paused["session"]["state"] == "paused", paused
        assert paused["pause"]["kind"] == "human_action"
        assert "summary failed validation" in paused["pause"]["question"]
        steps, messages, _ = await session_rows(app, session_id)
        last = steps[-1]
        assert (last.kind, last.state, last.error_code) == (
            "summary",
            "failed",
            "agent_summary_invalid",
        )
        assert not any(message.role == "summary" for message in messages)
        tool_messages = [message for message in messages if message.role == "tool"]
        assert sum(agent_context.estimate(m.content) for m in tool_messages) > (
            agent_context.RECENT_TOKENS
        )
        (tmp_path / "agent-invalid-summary.json").write_text(
            json.dumps(
                {
                    "pause": paused["pause"],
                    "steps": [[s.ordinal, s.kind, s.state, s.error_code] for s in steps],
                    "messages": len(messages),
                },
                indent=2,
            )
        )
