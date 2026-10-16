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


@pytest.mark.parametrize("failure", [None, "short", "truncated", "malformed"])
async def test_solution_one_item_one_accounted_call(tmp_path, failure):
    sent = []

    async def vendor(request):
        body = json.loads(request.content)
        sent.append(body)
        output = {
            "sections": [
                {"heading": title, "paragraphs": ["我方建立交接台账。负责人：【待填：姓名】。"]}
                for title in ("岗位安排", "培训实施", "考勤管理", "替补机制")
            ]
        }
        if failure == "short":
            output["sections"] = output["sections"][:3]
        return httpx.Response(
            200,
            json={
                "model": "synthetic-check-model",
                "choices": [
                    {
                        "finish_reason": "length" if failure == "truncated" else "stop",
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
        if failure:
            with pytest.raises(ProviderFailure) as error:
                await provider.draft(draft_request())
            assert error.value.code == (
                "provider_output_truncated" if failure == "truncated" else "invalid_provider_output"
            )
        else:
            result = await provider.draft(draft_request())
            assert len(result.output.sections) == 4
            assert len(result.usages) == 1
            assert result.usages[0].input_tokens == 120
    finally:
        current_accounting.reset(token)
    assert len(sent) == len(accounting.completed) == 1
    assert not accounting.unknown_calls
    assert json.loads(sent[0]["messages"][-1]["content"]) == draft_request().model_dump(mode="json")
    assert sent[0]["response_format"]["json_schema"]["strict"]
    system = sent[0]["messages"][0]["content"]
    assert "不可信" in system and "【待填：" in system and "我方" in system
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
    assert json.loads(artifact.read_text())["receipts"] == 1


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
