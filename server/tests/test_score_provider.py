"""DB-free rubric provider acceptance through the accounted HTTP boundary."""

import json
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import Settings
from app.providers.base import ProviderFailure
from app.providers.calls import current_accounting
from app.providers.llm import OpenAICompatibleExtractor
from app.providers.rubric import (
    RUBRIC_ADAPTER_VERSION,
    RUBRIC_PROMPT_VERSION,
    RUBRIC_SCHEMA_VERSION,
    HTTPRubricProvider,
    rubric_provider,
    supports_rubric,
)
from app.schemas.check_contracts import OutboundContext, OutboundText
from app.schemas.score_contracts import RubricProviderRequest, RubricProviderRequirement
from cryptography.fernet import Fernet

REQ_1 = UUID("00000000-0000-0000-0000-000000000001")
REQ_2 = UUID("00000000-0000-0000-0000-000000000002")


class Accounting:
    def __init__(self) -> None:
        self.planned: list[int] = []
        self.reservations: list[Decimal] = []
        self.completed = []
        self.unknown_calls = []

    def plan(self, first_pass_calls: int) -> None:
        self.planned.append(first_pass_calls)

    async def admit(self, quote):
        reserved_charge, platform_billed = quote.reserved_charge, quote.payer == "org_platform"
        self.reservations.append(reserved_charge)
        assert platform_billed is False
        return uuid4()

    async def complete(self, call_id, usage) -> None:
        self.completed.append((call_id, usage))

    async def unknown(self, call_id) -> None:
        self.unknown_calls.append(call_id)


def request() -> RubricProviderRequest:
    return RubricProviderRequest(
        requirements=[
            RubricProviderRequirement(requirement_id=REQ_1, tender_ref="r1.tender"),
            RubricProviderRequirement(requirement_id=REQ_2, tender_ref="r2.tender"),
        ],
        context=OutboundContext(
            texts=[
                OutboundText(ref="r1.tender", text="技术部分满分 40 分。"),
                OutboundText(ref="r2.tender", text="内存 64 GB 得 5 分。"),
            ]
        ),
    )


def wire() -> dict:
    return {
        "sections": [
            {
                "key": "technical",
                "title": "技术评分",
                "order": 1,
                "aggregation": "sum",
                "aggregation_rule_text": None,
                "score_range": {"minimum": "0", "maximum": "40"},
                "weight": None,
                "cap": None,
                "included_in_overall_total": True,
                "ambiguity_reason": None,
                "citations": [{"ref": "r1.tender", "quote": "满分 40 分"}],
            }
        ],
        "items": [
            {
                "requirement_id": str(REQ_2),
                "section_key": "technical",
                "key": "memory",
                "title": "内存",
                "rule_text": "内存 64 GB 得 5 分。",
                "order": 1,
                "assessment_mode": "model_assessable",
                "score_range": {"minimum": "0", "maximum": "5"},
                "weight": None,
                "ambiguity_reason": None,
                "citations": [{"ref": "r2.tender", "quote": "内存 64 GB 得 5 分"}],
            }
        ],
        "overall_aggregation": "sum",
        "overall_rule_text": None,
        "overall_score_range": {"minimum": "0", "maximum": "40"},
        "overall_cap": None,
    }


def adapter(tmp_path, replies: list[dict], sent: list[dict]) -> HTTPRubricProvider:
    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "synthetic-rubric",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(replies.pop(0), ensure_ascii=False)},
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10},
            },
        )

    llm = OpenAICompatibleExtractor(
        Settings(
            data_dir=tmp_path,
            database_url="postgresql+psycopg://unused/unused",
            encryption_key=Fernet.generate_key().decode(),
            token_key=Fernet.generate_key().decode(),
            llm_provider="openai",
            llm_model="synthetic-rubric",
            llm_api_key="synthetic-key",
            llm_base_url="https://rubric.example.test/v1",
            llm_batch_chars=8000,
            llm_max_output_tokens=4096,
        ),
        httpx.MockTransport(transport),
        org_owned=True,
    )
    return HTTPRubricProvider(llm)


async def test_http_rubric_provider_sends_only_fixed_refs_and_accounts_call(tmp_path):
    sent: list[dict] = []
    provider = adapter(tmp_path, [wire()], sent)
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.extract_rubric(request())
    finally:
        current_accounting.reset(token)

    assert result.failure is None
    assert len(result.batches) == 1
    assert result.batches[0].requested_requirement_ids == [REQ_1, REQ_2]
    assert result.batches[0].sent_refs == ["r1.tender", "r2.tender"]
    assert result.batches[0].output.items[0].requirement_id == REQ_2
    assert len(result.usages) == 1
    assert result.usages[0] == accounting.completed[0][1]
    assert accounting.planned == [1]
    assert accounting.unknown_calls == []
    assert len(sent) == 1
    payload = json.loads(sent[0]["messages"][1]["content"])
    assert payload == request().model_dump(mode="json")
    assert "condition" not in json.dumps(payload)
    assert sent[0]["response_format"]["json_schema"]["strict"] is True
    assert (
        provider.adapter_version,
        provider.prompt_version,
        provider.schema_version,
    ) == (RUBRIC_ADAPTER_VERSION, RUBRIC_PROMPT_VERSION, RUBRIC_SCHEMA_VERSION)


async def test_long_rubric_table_is_one_accounted_request_even_after_structure_failure(tmp_path):
    sent: list[dict] = []
    provider = adapter(tmp_path, [{"sections": []}], sent)
    whole = request()
    for entry in whole.context.texts:
        entry.text += " Synthetic complete table context." * 180
    assert len(whole.model_dump_json()) > 8000
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.extract_rubric(whole)
    finally:
        current_accounting.reset(token)
    assert result.failure is not None
    assert result.batches == []
    assert len(result.usages) == len(accounting.completed) == len(sent) == 1
    assert accounting.planned == [1]
    assert json.loads(sent[0]["messages"][-1]["content"]) == whole.model_dump(mode="json")


async def test_complete_rubric_request_limit_accounts_messages_schema_and_options(tmp_path):
    sent: list[dict] = []
    provider = adapter(tmp_path, [wire()], sent)
    provider.llm.settings.rubric_max_request_bytes = 2000
    assert len(request().model_dump_json().encode()) < 2000
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        with pytest.raises(ProviderFailure) as blocked:
            await provider.extract_rubric(request())
    finally:
        current_accounting.reset(token)
    assert blocked.value.code == "rubric_context_limit"
    assert not sent and not accounting.reservations and not accounting.planned


async def test_rubric_provider_requires_accounting_and_supported_adapter(tmp_path):
    sent: list[dict] = []
    provider = adapter(tmp_path, [wire()], sent)
    with pytest.raises(ProviderFailure) as unaccounted:
        await provider.extract_rubric(request())
    assert unaccounted.value.code == "rubric_accounting_required"
    assert sent == []

    assert supports_rubric(provider.llm) is True
    assert isinstance(rubric_provider(provider.llm), HTTPRubricProvider)
    assert supports_rubric(object()) is False
    with pytest.raises(ProviderFailure) as unsupported:
        rubric_provider(object())
    assert unsupported.value.code == "rubric_capability_unavailable"


async def test_rubric_provider_preserves_untrusted_unknown_ids_for_service_rejection(tmp_path):
    candidate = wire()
    candidate["items"][0]["requirement_id"] = str(uuid4())
    sent: list[dict] = []
    provider = adapter(tmp_path, [candidate], sent)
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.extract_rubric(request())
    finally:
        current_accounting.reset(token)

    assert result.batches[0].output.items[0].requirement_id not in {REQ_1, REQ_2}
