"""DB-free two-stage rubric acceptance through the accounted HTTP boundary."""

import asyncio
import json
from decimal import ROUND_CEILING, Decimal
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import Settings
from app.providers.base import ProviderFailure
from app.providers.calls import current_accounting
from app.providers.llm import OpenAICompatibleExtractor
from app.providers.rubric import (
    ITEMS_PROMPT_VERSION,
    RUBRIC_ADAPTER_VERSION,
    RUBRIC_PROMPT_VERSION,
    RUBRIC_SCHEMA_VERSION,
    STRUCTURE_PROMPT_VERSION,
    HTTPRubricProvider,
    rubric_provider,
    supports_rubric,
)
from app.schemas.budget_contracts import BudgetCallQuote
from app.schemas.check_contracts import OutboundContext, OutboundText
from app.schemas.score_contracts import (
    RubricItemsRequest,
    RubricProviderRequest,
    RubricProviderRequirement,
    RubricSectionContext,
)
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

REQ_1 = UUID("00000000-0000-0000-0000-000000000001")
REQ_2 = UUID("00000000-0000-0000-0000-000000000002")
STRUCTURE_HASH = "a" * 64


class Accounting:
    def __init__(
        self, *, platform_billed=False, block_after=None, block_code="job_charge_limit_exceeded"
    ) -> None:
        self.planned: list[int] = []
        self.reservations: list[Decimal] = []
        self.quotes: list[BudgetCallQuote] = []
        self.completed = []
        self.unknown_calls = []
        self.not_sent_calls = []
        self.platform_billed = platform_billed
        self.block_after = block_after
        self.block_code = block_code

    def plan(self, first_pass_calls: int) -> None:
        self.planned.append(first_pass_calls)

    async def admit(self, quote: BudgetCallQuote):
        reserved_charge, platform_billed = quote.reserved_charge, quote.payer == "org_platform"
        assert platform_billed is self.platform_billed
        if self.block_after is not None and len(self.reservations) >= self.block_after:
            raise ProviderFailure("Synthetic budget stop", code=self.block_code)
        self.reservations.append(reserved_charge)
        self.quotes.append(quote)
        return uuid4()

    async def complete(self, call_id, usage) -> None:
        self.completed.append((call_id, usage))

    async def unknown(self, call_id) -> None:
        self.unknown_calls.append(call_id)

    async def not_sent(self, call_id) -> None:
        self.not_sent_calls.append(call_id)


def request(count=2, *, padding=0) -> RubricProviderRequest:
    return RubricProviderRequest(
        requirements=[
            RubricProviderRequirement(requirement_id=UUID(int=index), tender_ref=f"r{index}.tender")
            for index in range(1, count + 1)
        ],
        context=OutboundContext(
            texts=[
                OutboundText(
                    ref=f"r{index}.tender",
                    text=("技术部分满分 40 分。" if index == 1 else "内存 64 GB 得 5 分。")
                    + "x" * padding,
                )
                for index in range(1, count + 1)
            ]
        ),
    )


def structure_wire() -> dict:
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
                "review_domain": "technical",
                "citations": [{"ref": "r1.tender", "quote": "满分 40 分"}],
            }
        ],
        "overall_aggregation": "sum",
        "overall_rule_text": None,
        "overall_score_range": {"minimum": "0", "maximum": "40"},
        "overall_cap": None,
        "overall_citations": [{"ref": "r1.tender", "quote": "满分 40 分"}],
    }


def items_wire(req_ids=(REQ_2,)) -> dict:
    return {
        "items": [
            {
                "requirement_id": str(req_id),
                "section_key": "technical",
                "key": f"memory-{req_id.int}",
                "title": "内存",
                "rule_text": "内存 64 GB 得 5 分。",
                "order": req_id.int,
                "assessment_mode": "model_assessable",
                "score_range": {"minimum": "0", "maximum": "5"},
                "weight": None,
                "ambiguity_reason": None,
                "citations": [{"ref": f"r{req_id.int}.tender", "quote": "内存 64 GB 得 5 分"}],
            }
            for req_id in req_ids
        ]
    }


def items_request(whole=None) -> RubricItemsRequest:
    whole = whole or request()
    return RubricItemsRequest(
        **whole.model_dump(),
        sections=[
            RubricSectionContext.model_validate(
                {key: value for key, value in section.items() if key != "citations"}
            )
            for section in structure_wire()["sections"]
        ],
        structure_hash=STRUCTURE_HASH,
    )


def response(content, *, finish_reason="stop", model="synthetic-rubric"):
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {
                        "content": content
                        if isinstance(content, str)
                        else json.dumps(content, ensure_ascii=False)
                    },
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
        },
    )


def adapter(tmp_path, replies, sent, *, handler=None, platform=False, **options):
    def transport(http_request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(http_request.content))
        return response(replies.pop(0))

    settings = Settings(
        data_dir=tmp_path,
        database_url="postgresql+psycopg://unused/unused",
        encryption_key=Fernet.generate_key().decode(),
        token_key=Fernet.generate_key().decode(),
        llm_provider="openai",
        llm_model="synthetic-rubric",
        llm_api_key="synthetic-key",
        llm_base_url="https://rubric.example.test/v1",
        llm_batch_chars=options.pop("llm_batch_chars", 8000),
        llm_max_output_tokens=4096,
        **options,
    )
    llm = OpenAICompatibleExtractor(
        settings,
        httpx.MockTransport(handler or transport),
        org_owned=not platform,
        platform_model_id="synthetic-platform" if platform else None,
        sale_usd_per_mtok=(1, 2) if platform else None,
    )
    return HTTPRubricProvider(llm)


async def test_two_stage_rubric_sends_fixed_refs_and_accounts_each_call(tmp_path):
    sent = []
    provider = adapter(tmp_path, [structure_wire(), items_wire()], sent)
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        structure = await provider.extract_structure(request())
        assert structure.output is not None
        result = await provider.extract_items(items_request())
    finally:
        current_accounting.reset(token)

    assert structure.failure is result.failure is None
    assert len(result.batches) == 1
    assert result.batches[0].requested_requirement_ids == [REQ_1, REQ_2]
    assert result.batches[0].sent_refs == ["r1.tender", "r2.tender"]
    assert result.batches[0].output.items[0].requirement_id == REQ_2
    assert result.batches[0].structure_hash == STRUCTURE_HASH
    assert len(structure.usages + result.usages) == len(accounting.completed) == 2
    assert accounting.planned == [1, 2]
    assert not accounting.unknown_calls
    assert json.loads(sent[0]["messages"][1]["content"]) == request().model_dump(mode="json")
    assert json.loads(sent[1]["messages"][1]["content"]) == items_request().model_dump(mode="json")
    assert all(body["response_format"]["json_schema"]["strict"] for body in sent)
    first_schema = sent[0]["response_format"]["json_schema"]["schema"]["properties"]
    second_schema = sent[1]["response_format"]["json_schema"]["schema"]["properties"]
    assert "items" not in first_schema
    assert "overall_citations" in first_schema
    assert set(second_schema) == {"items"}
    assert provider.adapter_version == RUBRIC_ADAPTER_VERSION == "http-score-rubric-v3"
    assert provider.prompt_version == RUBRIC_PROMPT_VERSION == "score-rubric-v3"
    assert provider.schema_version == RUBRIC_SCHEMA_VERSION == "score-rubric-wire-v2"
    assert STRUCTURE_PROMPT_VERSION != ITEMS_PROMPT_VERSION
    artifact = {
        "structure": structure.model_dump(mode="json"),
        "items": result.model_dump(mode="json"),
        "sent_payloads": [json.loads(body["messages"][1]["content"]) for body in sent],
        "accounted_calls": len(accounting.completed),
    }
    (tmp_path / "two-stage-rubric.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@pytest.mark.parametrize(
    "failure_kind", ["malformed", "truncated", "empty_sections", "no_overall_citation"]
)
async def test_structure_failure_is_one_full_accounted_request_without_retry(
    tmp_path, failure_kind
):
    whole = request(padding=5000)
    candidate = structure_wire()
    if failure_kind == "empty_sections":
        candidate["sections"] = []
    if failure_kind == "no_overall_citation":
        candidate["overall_citations"] = []
    sent = []

    def handler(http_request):
        sent.append(json.loads(http_request.content))
        return response(
            "{broken" if failure_kind == "malformed" else candidate,
            finish_reason="length" if failure_kind == "truncated" else "stop",
        )

    provider = adapter(tmp_path, [], sent, handler=handler)
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.extract_structure(whole)
    finally:
        current_accounting.reset(token)
    assert result.failure is not None
    assert result.output is None
    assert len(result.usages) == len(accounting.completed) == len(sent) == 1
    assert accounting.planned == [1]
    assert json.loads(sent[0]["messages"][-1]["content"]) == whole.model_dump(mode="json")


async def test_items_batches_receive_only_own_refs_and_same_fixed_sections(tmp_path):
    sent = []

    def handler(http_request):
        body = json.loads(http_request.content)
        sent.append(body)
        payload = json.loads(body["messages"][1]["content"])
        return response(
            items_wire([UUID(entry["requirement_id"]) for entry in payload["requirements"]])
        )

    provider = adapter(tmp_path, [], sent, handler=handler, llm_batch_chars=1000)
    whole = items_request(request(3, padding=650))
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.extract_items(whole)
    finally:
        current_accounting.reset(token)
    assert result.failure is None
    assert len(result.batches) == len(sent) == len(accounting.completed) == 3
    assert accounting.planned == [4]
    for index, body in enumerate(sent, 1):
        payload = json.loads(body["messages"][1]["content"])
        assert payload["requirements"] == [whole.requirements[index - 1].model_dump(mode="json")]
        assert [text["ref"] for text in payload["context"]["texts"]] == [f"r{index}.tender"]
        assert payload["sections"] == whole.model_dump(mode="json")["sections"]
        assert payload["structure_hash"] == STRUCTURE_HASH
        assert "citations" not in json.dumps(payload["sections"])


async def test_items_malformed_batch_is_missing_and_later_batches_finish_without_retry(tmp_path):
    sent = []
    provider = adapter(
        tmp_path,
        [items_wire([REQ_1]), "{broken", items_wire([UUID(int=3)])],
        sent,
        llm_batch_chars=1000,
        llm_concurrency=1,
    )
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.extract_items(items_request(request(3, padding=650)))
    finally:
        current_accounting.reset(token)
    assert result.failure is not None
    assert result.failure.code == "invalid_provider_output"
    assert [batch.requested_requirement_ids for batch in result.batches] == [[REQ_1], [UUID(int=3)]]
    assert len(result.usages) == len(sent) == len(accounting.completed) == 3


async def test_items_admission_stop_preserves_answered_batch_and_skips_unstarted(tmp_path):
    sent = []
    provider = adapter(
        tmp_path, [items_wire([REQ_1])], sent, llm_batch_chars=1000, llm_concurrency=1
    )
    accounting = Accounting(block_after=1)
    token = current_accounting.set(accounting)
    try:
        result = await provider.extract_items(items_request(request(3, padding=650)))
    finally:
        current_accounting.reset(token)
    assert result.failure is not None
    assert result.failure.code == "job_charge_limit_exceeded"
    assert len(result.batches) == len(result.usages) == len(sent) == 1
    assert not accounting.unknown_calls


async def test_task_budget_admission_stops_third_call_and_retains_first_item_batch(tmp_path):
    sent = []
    provider = adapter(
        tmp_path,
        [structure_wire(), items_wire([REQ_1])],
        sent,
        llm_batch_chars=1000,
        llm_concurrency=1,
    )
    accounting = Accounting(block_after=2, block_code="task_budget_exceeded")
    whole = request(3, padding=650)
    token = current_accounting.set(accounting)
    try:
        structure = await provider.extract_structure(whole)
        result = await provider.extract_items(items_request(whole))
    finally:
        current_accounting.reset(token)
    assert structure.output is not None and structure.failure is None
    assert result.failure is not None and result.failure.code == "task_budget_exceeded"
    assert [batch.requested_requirement_ids for batch in result.batches] == [[REQ_1]]
    assert len(sent) == len(accounting.completed) == len(accounting.reservations) == 2
    assert len(structure.usages + result.usages) == 2


async def test_budget_stop_remains_explicit_after_an_earlier_malformed_batch(tmp_path):
    sent = []
    provider = adapter(tmp_path, ["{broken"], sent, llm_batch_chars=1000, llm_concurrency=1)
    accounting = Accounting(block_after=1, block_code="task_budget_exceeded")
    token = current_accounting.set(accounting)
    try:
        result = await provider.extract_items(items_request(request(3, padding=650)))
    finally:
        current_accounting.reset(token)
    assert result.failure is not None and result.failure.code == "task_budget_exceeded"
    assert result.batches == []
    assert len(result.usages) == len(sent) == 1


async def test_items_concurrency_stops_unstarted_but_accounts_inflight_calls(tmp_path):
    sent = []
    started = asyncio.Event()
    active = 0
    max_active = 0

    async def handler(http_request):
        nonlocal active, max_active
        body = json.loads(http_request.content)
        payload = json.loads(body["messages"][1]["content"])
        sent.append(body)
        active += 1
        max_active = max(max_active, active)
        if len(sent) == 2:
            started.set()
        await started.wait()
        active -= 1
        req_id = UUID(payload["requirements"][0]["requirement_id"])
        return response(
            items_wire([req_id]),
            model="unexpected-model" if req_id == REQ_1 else "synthetic-rubric",
        )

    provider = adapter(tmp_path, [], sent, handler=handler, llm_batch_chars=1000, llm_concurrency=2)
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.extract_items(items_request(request(4, padding=650)))
    finally:
        current_accounting.reset(token)
    assert result.failure is not None
    assert result.failure.code == "invalid_provider_model"
    assert max_active == 2
    assert len(sent) == len(result.usages) == len(accounting.completed) == 2
    assert [batch.requested_requirement_ids for batch in result.batches] == [[REQ_2]]


@pytest.mark.parametrize("stage", ["structure", "items"])
async def test_request_limit_counts_messages_schema_and_options_before_admission(tmp_path, stage):
    sent = []
    provider = adapter(tmp_path, [], sent, rubric_max_request_bytes=2000)
    whole = request() if stage == "structure" else items_request()
    assert len(whole.model_dump_json().encode()) < 2000
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        if stage == "structure":
            with pytest.raises(ProviderFailure) as blocked:
                await provider.extract_structure(whole)
            assert blocked.value.code == "rubric_context_limit"
            assert not accounting.planned
        else:
            result = await provider.extract_items(whole)
            assert result.failure is not None and result.failure.code == "rubric_context_limit"
            assert result.batches == result.usages == []
    finally:
        current_accounting.reset(token)
    assert not sent and not accounting.reservations


async def test_oversized_item_batch_is_missing_while_an_independent_batch_completes(tmp_path):
    sent = []
    provider = adapter(
        tmp_path,
        [items_wire([REQ_2])],
        sent,
        rubric_max_request_bytes=8000,
        llm_batch_chars=1000,
        llm_concurrency=1,
    )
    whole = items_request()
    whole.context.texts[0].text += "x" * 10000
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.extract_items(whole)
    finally:
        current_accounting.reset(token)
    assert result.failure is not None and result.failure.code == "rubric_context_limit"
    assert [batch.requested_requirement_ids for batch in result.batches] == [[REQ_2]]
    assert len(result.usages) == len(sent) == len(accounting.reservations) == 1


async def test_all_batch_failures_reported_when_structural_error_precedes_hard_failure(tmp_path):
    sent = []

    def handler(http_request):
        sent.append(json.loads(http_request.content))
        if len(sent) == 1:
            return response("{broken")
        return response(items_wire([REQ_2]), model="unexpected-model")

    provider = adapter(tmp_path, [], sent, handler=handler, llm_batch_chars=1000, llm_concurrency=1)
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.extract_items(items_request(request(3, padding=650)))
    finally:
        current_accounting.reset(token)
    assert [failure.code for failure in result.failures] == [
        "invalid_provider_output",
        "invalid_provider_model",
    ]
    assert result.failure is not None and result.failure.code == "invalid_provider_model"
    assert not result.batches
    assert len(result.usages) == len(sent) == len(accounting.completed) == 2


async def test_provider_requires_accounting_and_complete_two_stage_capability(tmp_path):
    sent = []
    provider = adapter(tmp_path, [], sent)
    for operation, whole in (
        (provider.extract_structure, request()),
        (provider.extract_items, items_request()),
    ):
        with pytest.raises(ProviderFailure) as blocked:
            await operation(whole)
        assert blocked.value.code == "rubric_accounting_required"
    assert not sent
    assert supports_rubric(provider.llm)
    assert isinstance(rubric_provider(provider.llm), HTTPRubricProvider)

    class LegacyRubric:
        async def extract_rubric(self, request):
            raise AssertionError("legacy single-stage capability must not be selected")

    assert not supports_rubric(LegacyRubric())
    with pytest.raises(ProviderFailure) as unsupported:
        rubric_provider(LegacyRubric())
    assert unsupported.value.code == "rubric_capability_unavailable"


@pytest.mark.parametrize(
    "invalid", ["duplicate_context", "mismatched_ref", "duplicate_requirement"]
)
async def test_invalid_request_never_calls_model(tmp_path, invalid):
    sent = []
    provider = adapter(tmp_path, [], sent)
    whole = request()
    if invalid == "duplicate_context":
        whole.context.texts.append(whole.context.texts[0])
    elif invalid == "mismatched_ref":
        whole.context.texts[0].ref = "unrelated"
    else:
        whole.requirements[1].requirement_id = REQ_1
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        with pytest.raises(ProviderFailure) as blocked:
            await provider.extract_structure(whole)
    finally:
        current_accounting.reset(token)
    assert blocked.value.code == "invalid_rubric_request"
    assert not sent and not accounting.reservations


async def test_preview_is_call_free_and_conservatively_bounds_actual_reservations(tmp_path):
    sent = []
    provider = adapter(
        tmp_path,
        [structure_wire(), items_wire([REQ_1]), items_wire([REQ_2]), items_wire([UUID(int=3)])],
        sent,
        platform=True,
        llm_batch_chars=1000,
    )
    whole = request(3, padding=650)
    input_bound, output_bound, charge_bound, first_charge = provider.preview_bounds(whole)
    assert not sent
    assert first_charge == provider.reservation(whole)
    assert output_bound == 4 * 4096
    expected_stage2_charge = (
        (Decimal(2 * provider.llm.settings.rubric_max_request_bytes + 4096) + Decimal(4096 * 2))
        / 1_000_000
    ).quantize(Decimal("0.00000001"), rounding=ROUND_CEILING)
    assert charge_bound == first_charge + 3 * expected_stage2_charge
    accounting = Accounting(platform_billed=True)
    token = current_accounting.set(accounting)
    try:
        structure = await provider.extract_structure(whole)
        items = await provider.extract_items(items_request(whole))
    finally:
        current_accounting.reset(token)
    assert structure.failure is items.failure is None
    actual_input_bound = sum(
        len(json.dumps(body, ensure_ascii=False).encode()) + 4096 for body in sent
    )
    assert input_bound >= actual_input_bound
    assert charge_bound >= sum(accounting.reservations)
    assert accounting.reservations == [provider.llm.reservation(body) for body in sent]


async def test_unknown_item_ids_are_preserved_for_service_rejection(tmp_path):
    sent = []
    provider = adapter(tmp_path, [items_wire([UUID(int=999)])], sent)
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.extract_items(items_request())
    finally:
        current_accounting.reset(token)
    assert result.batches[0].output.items[0].requirement_id == UUID(int=999)


@pytest.mark.parametrize("platform", [False, True])
async def test_service_preflight_bounds_both_stages_and_preserves_quote_accounting(
    tmp_path, monkeypatch, platform
):
    from app.models.entities import Document, Job, OrgBalance, Task, UsageRecord
    from app.services import score_generation, score_inputs
    from app.services.auth import Identity

    sent = []
    provider = adapter(
        tmp_path,
        [structure_wire(), items_wire([REQ_1]), items_wire([REQ_2]), items_wire([UUID(int=3)])],
        sent,
        platform=platform,
        llm_batch_chars=1000,
        llm_input_usd_per_mtok=1,
        llm_output_usd_per_mtok=2,
    )
    whole = request(3, padding=650)
    first = provider.llm.quote(provider.validate_request(whole))
    assert first.reserved_task_amount is not None and first.reserved_task_amount > 0
    actor = Identity(uuid4(), uuid4(), {"score:rubric:generate"}, "bidder")
    budget_limit = first.reserved_task_amount * 2
    task = Task(
        id=uuid4(),
        org_id=actor.org_id,
        budget_currency="USD",
        budget_state="active",
        budget_limit=budget_limit,
        budget_revision=1,
    )
    extraction = Job(id=uuid4())
    document = Document(id=uuid4())
    fixed = score_inputs.RubricSnapshot(
        task=task,
        extraction=extraction,
        document=document,
        requirements=[],
        input_hash="b" * 64,
        manifest={
            "org_id": str(actor.org_id),
            "task_id": str(task.id),
            "extraction_job_id": str(extraction.id),
            "document_id": str(document.id),
            "model_redaction_enabled": True,
            "model_redaction_revision": 1,
            "redaction_rule_version": "synthetic-redaction",
            "provider_source": "platform" if platform else "org",
            "model": {"model": provider.model},
            "reasoning": None,
            "requirements": [entry.model_dump(mode="json") for entry in whole.requirements],
        },
        secret={
            "outbound": {
                "redacted_requirements": [],
                "context": whole.context.model_dump(mode="json"),
                "requirements": [str(entry.requirement_id) for entry in whole.requirements],
                "refs": {
                    entry.tender_ref: {"provider_id": str(entry.requirement_id)}
                    for entry in whole.requirements
                },
            }
        },
    )

    class ReadOnlySession:
        info = {"actor": actor}
        cached_job = None

        async def get(self, model, key):
            assert model is Task and key == task.id
            return task

        async def scalar(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            if entity is OrgBalance:
                return OrgBalance(balance=Decimal(10), currency="USD")
            if entity is Job:
                if self.cached_job is not None and "jobs.cache_key =" in str(statement):
                    assert self.cached_job.cache_key in statement.compile().params.values()
                    return self.cached_job
                return None
            return Decimal(0)

        async def execute(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            return SimpleNamespace(one=lambda: (0, 0, 0) if entity is UsageRecord else (0, 0, 0, 0))

    async def access(session, selected_actor, scope):
        selected_actor.require(scope)
        return selected_actor

    async def lock_inputs(*args):
        pass

    async def snapshot(*args):
        return fixed

    async def resolve(*args):
        return provider.llm

    async def prepare(session, fixed_input, llm, reasoning, settings):
        return llm

    monkeypatch.setattr(score_inputs, "access", access)
    monkeypatch.setattr(score_inputs, "lock_inputs", lock_inputs)
    monkeypatch.setattr(score_inputs, "snapshot", snapshot)
    monkeypatch.setattr(score_generation, "resolve", resolve)
    monkeypatch.setattr(score_generation, "prepare", prepare)
    from app.schemas.score_contracts import RubricGenerateRequest

    preview, job = await score_generation._submit_rubric(
        cast(AsyncSession, ReadOnlySession()),
        actor,
        task.id,
        RubricGenerateRequest(extraction_job_id=extraction.id, dry_run=True),
        provider.llm.settings,
    )
    assert job is None and not sent
    preflight = preview["budget_preflight"]
    assert preflight["planned_calls"] == 4
    assert preflight["admission_blocker"] is None
    assert preflight["first_pass_fits"] is False
    assert preflight["next_call"] == first.model_dump(mode="json")
    assert Decimal(preflight["estimate"]["task_amount"]) > budget_limit
    assert Decimal(preflight["estimate"]["charge"]) == Decimal(preview["estimated_charge"])
    assert preflight["estimate"]["llm_tokens"] == preview["estimated_cost"]["llm_tokens"]

    accounting = Accounting(platform_billed=platform)
    token = current_accounting.set(accounting)
    try:
        structure = await provider.extract_structure(whole)
        items = await provider.extract_items(items_request(whole))
    finally:
        current_accounting.reset(token)
    assert structure.failure is items.failure is None
    assert len(sent) == len(accounting.completed) == 4
    task_reservations = []
    for quote in accounting.quotes:
        assert quote.reserved_task_amount is not None
        task_reservations.append(quote.reserved_task_amount)
    assert Decimal(preflight["estimate"]["task_amount"]) >= sum(task_reservations)
    assert not accounting.unknown_calls and not accounting.not_sent_calls
    for quote, (_, usage), body in zip(accounting.quotes, accounting.completed, sent, strict=True):
        assert quote == provider.llm.quote(body)
        assert usage.payer == quote.payer
        assert usage.price_revision == quote.price_revision
        assert usage.billing_currency == quote.currency
        assert usage.task_amount is not None and 0 < usage.task_amount <= quote.reserved_task_amount

    from app.jobs import execution
    from app.schemas.contracts import Cost

    cache_key = score_generation.drafts.digest(
        {
            "kind": "score_rubric",
            "org_id": str(actor.org_id),
            "task_id": str(task.id),
            "actor_user_id": str(actor.user_id),
            "actor_token_id": None,
            "actor_kind": "session",
            "input_hash": fixed.input_hash,
        }
    )
    cached_job = Job(
        id=uuid4(),
        status="succeeded",
        cache_key=cache_key,
        result={"submission": {"input_hash": "c" * 64, "preview_input_hash": fixed.input_hash}},
    )
    cached_session = ReadOnlySession()
    cached_session.cached_job = cached_job
    usages = [usage for _, usage in accounting.completed]

    async def cached_cost(session, job_id, currency):
        assert session is cached_session and job_id == cached_job.id and currency == "USD"
        return Cost(
            basis="actual",
            llm_tokens=sum(usage.tokens for usage in usages),
            usd=sum(usage.usd for usage in usages),
            charge=sum(usage.charge for usage in usages),
            task_amount=sum(usage.task_amount for usage in usages),
        ).model_dump(mode="json")

    monkeypatch.setattr(execution, "job_cost", cached_cost)
    cached_preview, job = await score_generation._submit_rubric(
        cast(AsyncSession, cached_session),
        actor,
        task.id,
        RubricGenerateRequest(extraction_job_id=extraction.id, dry_run=True),
        provider.llm.settings,
    )
    cached = cached_preview["budget_preflight"]
    assert job is None and len(sent) == 4
    assert cached["cached_job_id"] == str(cached_job.id)
    assert (
        cached["input_hash"]
        == fixed.input_hash
        == cached_job.result["submission"]["preview_input_hash"]
    )
    assert cached_job.result["submission"]["input_hash"] == "c" * 64
    assert cached["planned_calls"] == 0 and cached["estimate"]["basis"] == "cache_hit"
    assert cached["next_call"] is cached["admission_blocker"] is None
    assert cached["first_pass_fits"] is True
    assert Decimal(cached["estimate"]["charge"]) == Decimal(cached["estimate"]["task_amount"]) == 0
    assert cached["cached_result_cost"]["llm_tokens"] == sum(usage.tokens for usage in usages)
    (tmp_path / "preflight-accounting.json").write_text(
        json.dumps(
            {
                "preview": preview,
                "cached_preview": cached_preview,
                "quotes": [quote.model_dump(mode="json") for quote in accounting.quotes],
                "usages": [usage.model_dump(mode="json") for _, usage in accounting.completed],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
