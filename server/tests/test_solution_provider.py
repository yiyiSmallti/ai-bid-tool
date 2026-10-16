"""Synthetic solution requests across the real structured/accounted HTTP boundary.

Failure modes: invented chapters instead of selected scoring items, source text
escaping its chapter, invalid/short model output, truncated output, unmetered
requests and admission limits allowing an extra outbound call. No vendor runs.
"""

import json
from typing import cast

import httpx
import pytest
from app.providers.base import ProviderFailure
from app.providers.calls import current_accounting
from app.providers.solution import HTTPSolutionProvider
from app.schemas.solution import SolutionProviderRequest
from cryptography.fernet import Fernet
from tests.test_check_combined import SemanticVendor, semantic_llm
from tests.test_score_provider import Accounting


def synthetic_llm(tmp_path, vendor):
    return semantic_llm(
        tmp_path,
        cast(SemanticVendor, vendor),
        database_url="postgresql+psycopg://unused/unused",
        access_key="synthetic-solution-key-with-32-characters",
        encryption_key=Fernet.generate_key().decode(),
        token_key=Fernet.generate_key().decode(),
    )


def draft_request():
    return SolutionProviderRequest(
        title="人员管理方案",
        score_range="0~15",
        rule="岗位安排、培训、考勤与替补机制齐全得分。",
        needs_text="实行交接台账与考核闭环。",
        project_name="合成服务项目",
        tenderee="合成采购单位",
    )


def synthetic_output(name: str) -> dict:
    """A plan of four sections, or one written section, by the requested schema."""
    if name == "solution_plan":
        return {
            "sections": [
                {"heading": title, "points": ["岗位安排", "检查记录"]}
                for title in ("岗位安排", "培训实施", "考勤管理", "替补机制")
            ]
        }
    return {
        "paragraphs": ["我方建立交接台账。负责人：【待填：姓名】。"],
        "subsections": [
            {"heading": heading, "paragraphs": ["我方每周检查并记录整改。"], "table": None}
            for heading in ("岗位安排", "检查记录")
        ],
    }


@pytest.mark.parametrize("failure", [None, "looped", "short", "truncated", "malformed"])
async def test_solution_plan_then_one_accounted_call_per_section(tmp_path, failure):
    sent = []

    async def vendor(request):
        body = json.loads(request.content)
        sent.append(body)
        name = body["response_format"]["json_schema"]["name"]
        output = synthetic_output(name)
        if failure == "short":
            output["sections"] = output["sections"][:3]
        return httpx.Response(
            200,
            json={
                "model": "synthetic-check-model",
                "choices": [
                    {
                        "finish_reason": "length"
                        if failure == "truncated" or (failure == "looped" and len(sent) == 2)
                        else "stop",
                        "message": {
                            "content": "{" if failure == "malformed" else json.dumps(output)
                        },
                    }
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 80},
            },
        )

    provider = HTTPSolutionProvider(synthetic_llm(tmp_path, vendor))
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        if failure and failure != "looped":
            with pytest.raises(ProviderFailure) as error:
                await provider.draft(draft_request())
            assert error.value.code == (
                "provider_output_truncated" if failure == "truncated" else "invalid_provider_output"
            )
        else:
            result = await provider.draft(draft_request())
            assert len(result.sections) == 4
            assert all(len(section.subsections) == 2 for section in result.sections)
            # A section cut off at its output limit is retried once, and both calls count.
            assert len(result.usages) == (6 if failure else 5)
            assert result.usages[0].input_tokens == 120
    finally:
        current_accounting.reset(token)
    calls = {None: 5, "looped": 6}.get(failure, 1)
    assert len(sent) == len(accounting.completed) == calls
    assert not accounting.unknown_calls
    plan = json.loads(sent[0]["messages"][-1]["content"])
    assert plan == draft_request().model_dump(mode="json") | {"sections_min": 6, "sections_max": 8}
    assert sent[0]["response_format"]["json_schema"]["strict"]
    for body in sent:
        system = body["messages"][0]["content"]
        assert "不可信" in system and "【待填：" in system and "我方" in system
    if failure == "looped":
        first, retried = (json.loads(sent[i]["messages"][-1]["content"]) for i in (1, 5))
        assert "retry" not in first and retried["retry"]
        assert retried["section"] == first["section"]
        assert all(body["max_tokens"] <= 12000 for body in sent[1:])
    if failure in (None, "looped"):
        written = json.loads(sent[1]["messages"][-1]["content"])
        assert written["item"] == draft_request().model_dump(mode="json")
        assert len(written["plan"]) == 4 and written["section"] in written["plan"]
    artifact = tmp_path / "solution-accounted-call.json"
    artifact.write_text(
        json.dumps(
            {
                "failure": failure,
                "request": draft_request().model_dump(mode="json"),
                "calls": len(sent),
                "receipts": len(accounting.completed),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    assert json.loads(artifact.read_text())["receipts"] == calls


async def test_solution_requires_accounting_and_admission_before_outbound(tmp_path):
    async def vendor(request):
        pytest.fail("No HTTP request may cross a denied admission")

    provider = HTTPSolutionProvider(synthetic_llm(tmp_path, vendor))
    with pytest.raises(ProviderFailure) as error:
        await provider.draft(draft_request())
    assert error.value.code == "provider_accounting_required"
    token = current_accounting.set(Accounting(block_after=0))
    try:
        with pytest.raises(ProviderFailure) as error:
            await provider.draft(draft_request())
        assert error.value.code == "job_call_limit_exceeded"
    finally:
        current_accounting.reset(token)
