"""Synthetic transport checks for the A01 decision boundary.

Failure modes specified before implementation: output spoofs usage or arbitrary
commands, extra arguments escape the closed union, input exceeds 128 KiB, an
unaccounted call reaches HTTP, malformed output loses billing, or an uncertain
request is automatically sent again. No real vendor traffic is used.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from app.core.config import Settings
from app.providers.agent import HTTPAgentProvider
from app.providers.base import ProviderFailure
from app.providers.calls import current_accounting
from app.providers.llm import OpenAICompatibleExtractor
from app.schemas.agent_contracts import AgentDecisionRequest, AgentMessageView
from cryptography.fernet import Fernet
from test_check_provider import Accounting


def settings_for(tmp_path, provider):
    return Settings(
        database_url="postgresql+psycopg://unused:synthetic@localhost/synthetic",
        encryption_key=Fernet.generate_key().decode(),
        token_key=Fernet.generate_key().decode(),
        data_dir=tmp_path,
        llm_provider=provider,
        llm_model="synthetic-model",
        llm_api_key="synthetic-test-key-not-real",
        llm_base_url="https://agent.example.test/v1",
        llm_input_usd_per_mtok=1,
        llm_output_usd_per_mtok=1,
    )


def request(content="Prepare response proposals for human review."):
    session_id = uuid4()
    return AgentDecisionRequest(
        session_id=session_id,
        step_id=uuid4(),
        messages=[
            AgentMessageView(
                id=uuid4(),
                org_id=uuid4(),
                session_id=session_id,
                ordinal=1,
                role="human",
                content=content,
                content_sha256="0" * 64,
                created_at=datetime.now(UTC),
            )
        ],
        tools=[],
        input_refs=[],
        remaining_steps=24,
        deadline=datetime.now(UTC) + timedelta(minutes=2),
    )


def reply(action):
    return httpx.Response(
        200,
        json={
            "model": "synthetic-model",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps({"action": action}),
                    },
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 35},
        },
    )


async def test_decision_uses_closed_wire_and_trusted_metering(tmp_path):
    bodies = []

    def vendor(req):
        bodies.append(json.loads(req.content))
        return reply(
            {
                "kind": "human_action",
                "action": "review_cards",
                "resource_ids": [],
                "question": "Review the proposals.",
            }
        )

    llm = OpenAICompatibleExtractor(settings_for(tmp_path, "openai"), httpx.MockTransport(vendor))
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        output = await HTTPAgentProvider(llm).decide(request())
    finally:
        current_accounting.reset(token)
    assert output.usage.tokens == 155 and output.decision.action.kind == "human_action"
    assert len(accounting.completed) == len(bodies) == 1
    schema = bodies[0]["response_format"]["json_schema"]["schema"]
    assert "usage" not in schema["properties"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"action"}
    (tmp_path / "decision-metering.json").write_text(
        json.dumps(
            {
                "events": accounting.events,
                "tokens": output.usage.tokens,
                "wire": schema,
            },
            indent=2,
        )
    )


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "tool", "call": {"command": "export", "arguments": {}}, "input_refs": []},
        {
            "kind": "tool",
            "call": {
                "command": "card show",
                "arguments": {"id": str(uuid4()), "history": False, "url": "https://invalid.test"},
            },
            "input_refs": [],
        },
        {"kind": "complete", "summary": "Done", "output_refs": [], "usage": {"usd": 0}},
    ],
)
async def test_invalid_decision_settles_before_rejection(tmp_path, action):
    llm = OpenAICompatibleExtractor(
        settings_for(tmp_path, "openai"), httpx.MockTransport(lambda req: reply(action))
    )
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        with pytest.raises(ProviderFailure, match="output"):
            await HTTPAgentProvider(llm).decide(request())
    finally:
        current_accounting.reset(token)
    assert len(accounting.completed) == 1


async def test_no_accounting_and_uncertain_request_never_retries(tmp_path):
    sent = []

    def vendor(req):
        sent.append(req)
        raise httpx.ReadTimeout("Synthetic unknown outcome")

    llm = OpenAICompatibleExtractor(settings_for(tmp_path, "openai"), httpx.MockTransport(vendor))
    llm.retry_delays = (0, 0)
    with pytest.raises(ProviderFailure) as rejected:
        await HTTPAgentProvider(llm).decide(request())
    assert rejected.value.code == "provider_accounting_required" and not sent
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        with pytest.raises(ProviderFailure):
            await HTTPAgentProvider(llm).decide(request())
    finally:
        current_accounting.reset(token)
    assert len(sent) == len(accounting.unknown_calls) == 1


async def test_oversized_context_fails_before_http(tmp_path):
    llm = OpenAICompatibleExtractor(
        settings_for(tmp_path, "openai"),
        httpx.MockTransport(lambda req: pytest.fail("Oversized context must not reach the vendor")),
    )
    large = request("x" * 8000)
    large.messages = [large.messages[0].model_copy(update={"ordinal": i + 1}) for i in range(40)]
    with pytest.raises(ProviderFailure) as rejected:
        await HTTPAgentProvider(llm).decide(large)
    assert rejected.value.code == "agent_context_limit"
