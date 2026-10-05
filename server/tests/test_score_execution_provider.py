"""DB-free score Provider, semantic boundary, and aggregation acceptance.

Failure inventory written before the phase-B implementation:

* requests may contain missing, extra, duplicate, or cross-bound refs;
* model output may omit, duplicate, or invent rubric item IDs;
* citations may use an unsent/rule ref, stitch text, quote an ambiguous span,
  quote redacted text, or borrow a draft row from another completed batch;
* prose may echo confidential values or suggest fabricated proof/price strategy;
* scores may escape confirmed bounds or omit deductions below the maximum;
* unsupported modes, thin promises, missing confirmed draft text, redaction, and
  incomplete Provider batches must remain explicitly unassessable;
* sum, weighted_sum, and capped_sum must retain exact Decimal intermediates,
  round only published values, and never promote a partial subtotal to a total.

All vendor traffic uses ``httpx.MockTransport`` and synthetic text.
"""

import asyncio
import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import Settings
from app.core.errors import ServiceError
from app.providers.base import ProviderFailure
from app.providers.calls import current_accounting
from app.providers.llm import OpenAICompatibleExtractor
from app.providers.scoring import (
    SCORE_ADAPTER_VERSION,
    SCORE_PROMPT_VERSION,
    SCORE_SCHEMA_VERSION,
    HTTPScoreProvider,
    score_provider,
    supports_score,
)
from app.schemas.check_contracts import OutboundContext, OutboundText
from app.schemas.score_contracts import ScoreProviderItem, ScoreProviderRequest, ScoreProviderResult
from app.services import redaction, score_semantic
from cryptography.fernet import Fernet
from test_check_provider import Accounting

RUBRIC_1 = UUID("00000000-0000-0000-0000-000000000101")
RUBRIC_2 = UUID("00000000-0000-0000-0000-000000000102")
REQ_1 = UUID("00000000-0000-0000-0000-000000000201")
REQ_2 = UUID("00000000-0000-0000-0000-000000000202")
DRAFT = UUID("00000000-0000-0000-0000-000000000301")
RESPONSE_1 = UUID("00000000-0000-0000-0000-000000000401")
RESPONSE_2 = UUID("00000000-0000-0000-0000-000000000402")
REVISION_1 = UUID("00000000-0000-0000-0000-000000000501")
REVISION_2 = UUID("00000000-0000-0000-0000-000000000502")
SECTION_1 = UUID("00000000-0000-0000-0000-000000000601")


def provider_request() -> ScoreProviderRequest:
    return ScoreProviderRequest(
        assessment_date=date(2026, 10, 5),
        items=[
            ScoreProviderItem(
                rubric_item_id=RUBRIC_1,
                requirement_id=REQ_1,
                section_key="technical",
                tender_ref="s1.tender",
                rule_ref="s1.rule",
                draft_refs=["d1.response_text", "d2.response_text"],
                anchor_response_item_id=RESPONSE_1,
                anchor_partition="response",
                anchor_gap_reason_codes=[],
                score_range={"minimum": "0", "maximum": "5"},
            ),
            ScoreProviderItem(
                rubric_item_id=RUBRIC_2,
                requirement_id=REQ_2,
                section_key="technical",
                tender_ref="s2.tender",
                rule_ref="s2.rule",
                draft_refs=["d1.response_text", "d2.response_text"],
                anchor_response_item_id=RESPONSE_2,
                anchor_partition="response",
                anchor_gap_reason_codes=[],
                score_range={"minimum": "0", "maximum": "5"},
            ),
        ],
        context=OutboundContext(
            texts=[
                OutboundText(ref="s1.tender", text="Memory 64 GB earns 5 points."),
                OutboundText(ref="s1.rule", text="Award 5 points for 64 GB memory."),
                OutboundText(ref="s2.tender", text="Three years support earns 5 points."),
                OutboundText(ref="s2.rule", text="Award 5 points for three years support."),
                OutboundText(ref="d1.response_text", text="We supply 64 GB memory."),
                OutboundText(ref="d2.response_text", text="We provide three years support."),
            ]
        ),
    )


def wire() -> dict:
    return {
        "items": [
            {
                "rubric_item_id": str(RUBRIC_1),
                "outcome": "assessed",
                "estimated_score": "5",
                "reason_code": "supported",
                "reason": "The confirmed response supports the criterion.",
                "deduction_reasons": [],
                "strengthening_actions": [],
                "tender_citations": [{"ref": "s1.tender", "quote": "Memory 64 GB earns 5 points."}],
                "draft_citations": [{"ref": "d1.response_text", "quote": "supply 64 GB memory"}],
            },
            {
                "rubric_item_id": str(RUBRIC_2),
                "outcome": "assessed",
                "estimated_score": "5",
                "reason_code": "supported",
                "reason": "A different confirmed response supports this criterion.",
                "deduction_reasons": [],
                "strengthening_actions": [],
                "tender_citations": [
                    {"ref": "s2.tender", "quote": "Three years support earns 5 points."}
                ],
                "draft_citations": [{"ref": "d2.response_text", "quote": "three years support"}],
            },
        ]
    }


def adapter(tmp_path, replies: list[dict], sent: list[dict], *, batch_chars=100_000):
    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "synthetic-score",
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
            llm_model="synthetic-score",
            llm_api_key="synthetic-key",
            llm_base_url="https://score.example.test/v1",
            llm_batch_chars=batch_chars,
            llm_max_output_tokens=4096,
        ),
        httpx.MockTransport(transport),
        org_owned=True,
    )
    return HTTPScoreProvider(llm)


async def test_http_score_provider_sends_fixed_refs_and_accounts_call(tmp_path):
    sent: list[dict] = []
    provider = adapter(tmp_path, [wire()], sent)
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.score(provider_request())
    finally:
        current_accounting.reset(token)

    assert result.failure is None
    assert [batch.requested_rubric_item_ids for batch in result.batches] == [[RUBRIC_1, RUBRIC_2]]
    assert result.batches[0].sent_refs == [text.ref for text in provider_request().context.texts]
    assert len(result.usages) == 1
    assert result.usages[0] == accounting.completed[0][1]
    assert accounting.planned == [1]
    assert accounting.platform_billed == [False]
    payload = json.loads(sent[0]["messages"][1]["content"])
    assert payload == provider_request().model_dump(mode="json")
    assert sent[0]["response_format"]["json_schema"]["strict"] is True
    assert (provider.adapter_version, provider.prompt_version, provider.schema_version) == (
        SCORE_ADAPTER_VERSION,
        SCORE_PROMPT_VERSION,
        SCORE_SCHEMA_VERSION,
    )


async def test_score_provider_rejects_unaccounted_or_unbound_requests(tmp_path):
    sent: list[dict] = []
    provider = adapter(tmp_path, [wire()], sent)
    with pytest.raises(ProviderFailure) as unaccounted:
        await provider.score(provider_request())
    assert unaccounted.value.code == "score_accounting_required"
    assert sent == []

    invalid = provider_request().model_copy(deep=True)
    invalid.context.texts.append(OutboundText(ref="unbound", text="must not be sent"))
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        with pytest.raises(ProviderFailure) as rejected:
            await provider.score(invalid)
    finally:
        current_accounting.reset(token)
    assert rejected.value.code == "invalid_score_request"
    assert sent == []
    assert supports_score(provider.llm) is True
    assert isinstance(score_provider(provider.llm), HTTPScoreProvider)
    assert supports_score(object()) is False


async def test_http_score_provider_batches_complete_items_and_accounts_each_call(tmp_path):
    replies = [
        {"items": [wire()["items"][0]]},
        {"items": [wire()["items"][1]]},
    ]
    sent: list[dict] = []
    provider = adapter(tmp_path, replies, sent, batch_chars=1000)
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.score(provider_request())
    finally:
        current_accounting.reset(token)

    assert result.failure is None
    assert [batch.requested_rubric_item_ids for batch in result.batches] == [
        [RUBRIC_1],
        [RUBRIC_2],
    ]
    assert accounting.planned == [2]
    assert len(accounting.completed) == len(result.usages) == len(sent) == 2
    for payload in sent:
        request = json.loads(payload["messages"][1]["content"])
        assert len(request["items"]) == 1
        allowed = {
            request["items"][0]["tender_ref"],
            request["items"][0]["rule_ref"],
            *request["items"][0]["draft_refs"],
        }
        assert {text["ref"] for text in request["context"]["texts"]} == allowed


async def test_concurrent_admission_error_drains_an_already_admitted_call(tmp_path):
    transport_entered = asyncio.Event()
    admission_failed = asyncio.Event()
    transport_finished = False

    class GatedAccounting(Accounting):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def admit(self, quote):
            reserved_charge, platform_billed = quote.reserved_charge, quote.payer == "org_platform"
            self.attempts += 1
            self.reservations.append(reserved_charge)
            self.platform_billed.append(platform_billed)
            if self.attempts == 1:
                return uuid4()
            await transport_entered.wait()
            admission_failed.set()
            raise ServiceError("score_input_changed", "Synthetic fixed-input fence", 409, 4)

    async def transport(request: httpx.Request) -> httpx.Response:
        nonlocal transport_finished
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        item_id = payload["items"][0]["rubric_item_id"]
        answer = next(item for item in wire()["items"] if item["rubric_item_id"] == item_id)
        transport_entered.set()
        await admission_failed.wait()
        await asyncio.sleep(0.05)
        transport_finished = True
        return httpx.Response(
            200,
            json={
                "model": "synthetic-score",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"items": [answer]})},
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
            llm_model="synthetic-score",
            llm_api_key="synthetic-key",
            llm_base_url="https://score.example.test/v1",
            llm_batch_chars=1000,
            llm_max_output_tokens=4096,
        ),
        httpx.MockTransport(transport),
        org_owned=True,
    )
    provider = HTTPScoreProvider(llm)
    accounting = GatedAccounting()
    token = current_accounting.set(accounting)
    try:
        with pytest.raises(ServiceError) as stopped:
            await provider.score(provider_request())
    finally:
        current_accounting.reset(token)

    assert stopped.value.code == "score_input_changed"
    assert transport_finished is True
    assert len(accounting.completed) == 1
    assert accounting.unknown_calls == []


def fixed_secret(*, mode="model_assessable", response="We supply 64 GB memory.") -> dict:
    source_1 = {
        "document_id": "00000000-0000-0000-0000-000000000701",
        "chunk_id": "00000000-0000-0000-0000-000000000702",
        "page": 1,
        "location": None,
        "quote": "Memory 64 GB earns 5 points.",
    }
    source_2 = {
        **source_1,
        "chunk_id": "00000000-0000-0000-0000-000000000703",
        "page": 2,
        "quote": "Three years support earns 5 points.",
    }
    return {
        "draft_id": str(DRAFT),
        "assessment_date": "2026-10-05",
        "items": [
            {
                "requirement_id": str(REQ_1),
                "response_item_id": str(RESPONSE_1),
                "card_revision_id": str(REVISION_1),
                "partition": "response",
                "source": source_1,
                "tender_original": source_1["quote"],
                "response_text": response,
                "deviation_note": "",
                "response_kind": "technical",
                "deviation": "none",
                "gap_reasons": [],
            },
            {
                "requirement_id": str(REQ_2),
                "response_item_id": str(RESPONSE_2),
                "card_revision_id": str(REVISION_2),
                "partition": "response",
                "source": source_2,
                "tender_original": source_2["quote"],
                "response_text": "We provide three years support.",
                "deviation_note": "",
                "response_kind": "commitment",
                "deviation": "none",
                "gap_reasons": [],
            },
        ],
        "rubric": {
            "rubric": {
                "overall_aggregation": "sum",
                "overall_rule_text": None,
                "overall_score_range": {"minimum": "0", "maximum": "10"},
                "overall_cap": None,
            },
            "sections": [
                {
                    "id": str(SECTION_1),
                    "key": "technical",
                    "title": "Technical",
                    "aggregation": "sum",
                    "aggregation_assessable": True,
                    "aggregation_rule_text": None,
                    "score_range": {"minimum": "0", "maximum": "10"},
                    "weight": None,
                    "cap": None,
                    "included_in_overall_total": True,
                }
            ],
            "items": [
                {
                    "id": str(RUBRIC_1),
                    "section_id": str(SECTION_1),
                    "requirement_id": str(REQ_1),
                    "key": "memory",
                    "title": "Memory",
                    "rule_text": "Award 5 points for 64 GB memory.",
                    "order": 1,
                    "assessment_mode": mode,
                    "score_range": {"minimum": "0", "maximum": "5"},
                    "weight": None,
                    "ambiguity_reason": None
                    if mode == "model_assessable"
                    else "Requires manual assessment.",
                    "source": source_1,
                },
                {
                    "id": str(RUBRIC_2),
                    "section_id": str(SECTION_1),
                    "requirement_id": str(REQ_2),
                    "key": "support",
                    "title": "Support",
                    "rule_text": "Award 5 points for three years support.",
                    "order": 2,
                    "assessment_mode": "model_assessable",
                    "score_range": {"minimum": "0", "maximum": "5"},
                    "weight": None,
                    "ambiguity_reason": None,
                    "source": source_2,
                },
            ],
        },
    }


async def execute_boundary(tmp_path, secret: dict, reply: dict, *, fields=(), library=()):
    sent: list[dict] = []
    outbound = score_semantic.build_outbound(secret, list(fields), list(library))
    request = score_semantic.provider_request(outbound)
    assert request is not None
    provider = adapter(tmp_path, [reply], sent)
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.score(request)
    finally:
        current_accounting.reset(token)
    evaluated = score_semantic.accept_batches(secret, outbound, result, list(library))
    return evaluated, outbound, result, sent


async def test_outbound_http_accepts_cross_response_support_and_aggregates(tmp_path):
    candidate = wire()
    candidate["items"][0]["draft_citations"] = [
        {"ref": "d2.response_text", "quote": "three years support"}
    ]
    evaluated, outbound, result, sent = await execute_boundary(tmp_path, fixed_secret(), candidate)

    assert result.failure is None
    assert evaluated[0]["outcome"] == "assessed"
    assert evaluated[0]["response_item_ids"] == [RESPONSE_2]
    assert {citation["kind"] for citation in evaluated[0]["citations"]} == {
        "tender",
        "draft",
    }
    assert str(DRAFT) not in json.dumps(outbound["context"])
    assert str(RESPONSE_1) not in json.dumps(outbound["context"])
    assert "condition" not in json.dumps(sent)
    payload = json.loads(sent[0]["messages"][1]["content"])
    assert set(payload["context_only_refs"]) == {
        "d1.context",
        "d1.tender",
        "d2.context",
        "d2.tender",
    }
    assert set(payload["context_only_refs"]) <= {
        text["ref"] for text in payload["context"]["texts"]
    }

    sections, summary = score_semantic.aggregate(
        fixed_secret()["rubric"], evaluated, provider_complete=True
    )
    assert sections[0]["status"] == "estimated"
    assert sections[0]["assessed_subtotal"] == Decimal("10.00000000")
    assert summary["completion"] == "complete"
    assert summary["total_status"] == "estimated"
    assert summary["estimated_total"] == Decimal("10.00000000")


@pytest.mark.parametrize(
    "attack,reason",
    [
        ("missing", "missing_rubric_item_id"),
        ("duplicate", "duplicate_rubric_item_id"),
        ("unknown_id", "unknown_rubric_item_id"),
        ("unsent", "ref_not_sent"),
        ("rule_ref", "invalid_citation_kind"),
        ("stitched", "quote_not_at_position"),
        ("ambiguous", "ambiguous_quote"),
        ("out_of_bounds", "score_out_of_bounds"),
        ("missing_deduction", "missing_deduction_reason"),
        ("unsafe_action", "unsafe_strengthening_action"),
        ("pure_commitment", "thin_promise_unassessable"),
        ("proof_incidental_number", "thin_promise_unassessable"),
        ("negative_maximum", "negative_deviation_conflict"),
        ("cross_item_tender", "invalid_citation_kind"),
        ("wrong_fixed_location", "quote_not_at_position"),
        ("placeholder_quote", "redacted_input_unassessable"),
        ("redacted_quote", "redacted_input_unassessable"),
        ("sensitive_reason", "sensitive_model_output"),
        ("unknown_reason_placeholder", "unknown_placeholder"),
    ],
)
async def test_untrusted_score_output_becomes_unassessable(tmp_path, attack, reason):
    secret = fixed_secret(
        response="We supply 64 GB memory. We supply 64 GB memory."
        if attack == "ambiguous"
        else "We fully comply."
        if attack == "pure_commitment"
        else "We will provide the certificate within 30 days."
        if attack == "proof_incidental_number"
        else "We supply {{secret.memory}} memory."
        if attack == "placeholder_quote"
        else "We supply [REDACTED_PHONE] memory."
        if attack == "redacted_quote"
        else "We supply 64 GB memory."
    )
    candidate = wire()
    first = candidate["items"][0]
    if attack == "missing":
        candidate["items"] = [candidate["items"][1]]
    elif attack == "duplicate":
        candidate["items"].insert(1, deepcopy(first))
    elif attack == "unknown_id":
        first["rubric_item_id"] = str(uuid4())
    elif attack == "unsent":
        first["draft_citations"][0]["ref"] = "d999.response_text"
    elif attack == "rule_ref":
        first["tender_citations"][0]["ref"] = "s1.rule"
    elif attack == "stitched":
        first["draft_citations"][0]["quote"] = "supply memory"
    elif attack == "ambiguous":
        first["draft_citations"][0]["quote"] = "supply 64 GB memory"
    elif attack == "out_of_bounds":
        first["estimated_score"] = "6"
    elif attack == "missing_deduction":
        first["estimated_score"] = "4"
    elif attack == "unsafe_action":
        first["strengthening_actions"] = ["Fabricate a certificate for the evaluator."]
    elif attack == "pure_commitment":
        first["draft_citations"][0]["quote"] = "fully comply"
    elif attack == "proof_incidental_number":
        secret["rubric"]["items"][0]["rule_text"] = "Provide a certificate to earn 5 points."
        first["draft_citations"][0]["quote"] = "certificate within 30 days"
    elif attack == "negative_maximum":
        secret["items"][0]["deviation"] = "negative"
    elif attack == "cross_item_tender":
        first["tender_citations"][0] = {
            "ref": "s2.tender",
            "quote": "Three years support earns 5 points.",
        }
    elif attack == "wrong_fixed_location":
        secret["items"][0]["tender_original"] = "A different fixed source block."
    elif attack == "placeholder_quote":
        first["draft_citations"][0]["quote"] = "{{secret.memory}}"
    elif attack == "redacted_quote":
        first["draft_citations"][0]["quote"] = "[REDACTED_PHONE]"
    elif attack == "sensitive_reason":
        first["reason"] = "Contact Synthetic Secret for the score."
    elif attack == "unknown_reason_placeholder":
        first["reason"] = "Use {{secret.unknown}} for the score."

    library = (
        [redaction.library_value("contact", "contact", "Synthetic Secret")]
        if attack == "sensitive_reason"
        else []
    )
    evaluated, _, _, _ = await execute_boundary(
        tmp_path,
        secret,
        candidate,
        library=[entry for entry in library if entry is not None],
    )

    assert evaluated[0]["outcome"] == "unassessable"
    assert evaluated[0]["reason_code"] == reason
    assert evaluated[0]["estimated_score"] is None
    assert evaluated[0]["response_item_ids"] == []
    assert evaluated[0]["citations"] == []


async def test_explicit_commitment_rule_and_negative_deduction_remain_assessable(tmp_path):
    commitment = fixed_secret(response="We will provide three years support.")
    commitment["rubric"]["items"][0]["rule_text"] = (
        "A written commitment to provide three years support earns 5 points."
    )
    commitment_wire = wire()
    commitment_wire["items"][0]["draft_citations"] = [
        {"ref": "d1.response_text", "quote": "will provide three years support"}
    ]
    evaluated, _, _, _ = await execute_boundary(tmp_path, commitment, commitment_wire)
    assert evaluated[0]["outcome"] == "assessed"

    negative = fixed_secret()
    negative["items"][0]["deviation"] = "negative"
    negative_wire = wire()
    negative_wire["items"][0]["estimated_score"] = "4"
    negative_wire["items"][0]["deduction_reasons"] = [
        "The confirmed negative deviation loses one point."
    ]
    evaluated, _, _, _ = await execute_boundary(tmp_path, negative, negative_wire)
    assert evaluated[0]["outcome"] == "assessed"
    assert evaluated[0]["estimated_score"] == Decimal("4")


@pytest.mark.parametrize(
    "mode,rule,response,reason",
    [
        (
            "price_comparison",
            "Award points from the benchmark price.",
            "Quoted price 10.",
            "price_comparison",
        ),
        ("external_comparison", "Rank all bidders.", "We rank first.", "external_comparison"),
        ("unsupported_formula", "Use formula X.", "Formula input is 1.", "unsupported_formula"),
        (
            "ambiguous",
            "The rule is ambiguous.",
            "We comply.",
            "ambiguous",
        ),
    ],
)
def test_preflight_prohibited_modes(mode, rule, response, reason):
    secret = fixed_secret(mode=mode, response=response)
    secret["rubric"]["items"][0]["rule_text"] = rule

    outbound = score_semantic.build_outbound(secret, [], [])

    assert outbound["preflight"][str(RUBRIC_1)]["reason_code"] == reason
    request = score_semantic.provider_request(outbound)
    assert request is not None
    assert [item.rubric_item_id for item in request.items] == [RUBRIC_2]


async def test_thin_anchor_can_use_substantive_cross_response_but_not_a_thin_citation(tmp_path):
    secret = fixed_secret(response="We fully comply and can provide it.")
    secret["rubric"]["items"][0]["rule_text"] = "Provide a certificate to earn 5 points."
    candidate = wire()
    candidate["items"][0]["draft_citations"] = [
        {"ref": "d1.response_text", "quote": "fully comply"}
    ]

    evaluated, _, _, _ = await execute_boundary(tmp_path, secret, candidate)

    assert evaluated[0]["outcome"] == "unassessable"
    assert evaluated[0]["reason_code"] == "thin_promise_unassessable"
    assert evaluated[0]["strengthening_actions"]


@pytest.mark.parametrize(
    "setup,reason",
    [
        ("manual_only", "manual_only"),
        ("missing_bounds", "missing_score_bounds"),
    ],
)
async def test_preflight_item_and_assessable_item_share_full_http_acceptance(
    tmp_path, setup, reason
):
    secret = fixed_secret()
    if setup == "manual_only":
        secret["rubric"]["items"][0] |= {
            "assessment_mode": "manual_only",
            "ambiguity_reason": "A human decision is required.",
        }
    else:
        secret["rubric"]["items"][0]["score_range"] = None
    candidate = {"items": [wire()["items"][1]]}

    evaluated, _, result, sent = await execute_boundary(tmp_path, secret, candidate)

    assert len(sent) == len(result.batches) == 1
    assert evaluated[0]["outcome"] == "unassessable"
    assert evaluated[0]["reason_code"] == reason
    assert evaluated[1]["outcome"] == "assessed"


def test_no_confirmed_draft_support_completes_preflight_without_http():
    secret = fixed_secret()
    for item in secret["items"]:
        item["partition"] = "gap"
        item["response_text"] = None
        item["deviation_note"] = None
    outbound = score_semantic.build_outbound(secret, [], [])

    assert score_semantic.provider_request(outbound) is None
    evaluated = score_semantic.accept_batches(
        secret,
        outbound,
        ScoreProviderResult(batches=[], usages=[]),
    )
    assert {row["reason_code"] for row in evaluated} == {"no_confirmed_draft_support"}
    assert all(row["outcome"] == "unassessable" for row in evaluated)


def test_redacted_input_and_incomplete_provider_never_gain_a_numeric_estimate():
    secret = fixed_secret(response="Contact Synthetic Secret about 64 GB memory.")
    library = [redaction.library_value("contact", "contact", "Synthetic Secret")]
    outbound = score_semantic.build_outbound(
        secret,
        [{"placeholder": "{{secret.contact}}", "label": "Contact", "kind": "contact"}],
        [entry for entry in library if entry is not None],
    )
    assert outbound["preflight"][str(RUBRIC_1)]["reason_code"] == "redacted_input_unassessable"

    evaluated = score_semantic.accept_batches(
        secret,
        outbound,
        score_semantic.empty_result("provider_stopped"),
        [entry for entry in library if entry is not None],
    )
    assert all(row["outcome"] == "unassessable" for row in evaluated)
    assert evaluated[1]["reason_code"] == "provider_incomplete"
    sections, summary = score_semantic.aggregate(
        secret["rubric"], evaluated, provider_complete=False
    )
    assert sections[0]["assessed_subtotal"] == Decimal("0E-8")
    assert sections[0]["estimated_score"] is None
    assert summary["completion"] == "partial"
    assert summary["estimated_total"] is None


def test_decimal_aggregation_rounds_only_published_values_and_propagates_unavailable():
    secret = fixed_secret()
    rubric = secret["rubric"]
    rubric["sections"][0] |= {
        "aggregation": "weighted_sum",
        "score_range": {"minimum": "0", "maximum": "1"},
    }
    rubric["items"][0]["weight"] = "0.33333333"
    rubric["items"][1]["weight"] = "0.66666667"
    rubric["items"][0]["score_range"] = {"minimum": "0", "maximum": "1"}
    rubric["items"][1]["score_range"] = {"minimum": "0", "maximum": "1"}
    rows = [
        {
            "rubric_item_id": RUBRIC_1,
            "outcome": "assessed",
            "estimated_score": Decimal("0.00000001"),
        },
        {
            "rubric_item_id": RUBRIC_2,
            "outcome": "assessed",
            "estimated_score": Decimal("0.00000001"),
        },
    ]

    sections, summary = score_semantic.aggregate(rubric, rows, provider_complete=True)

    assert sections[0]["estimated_score"] == Decimal("0.00000001")
    assert summary["estimated_total"] == Decimal("0.00000001")

    rubric["items"][0]["weight"] = "0.20000000"
    rubric["items"][1]["weight"] = "0.70000000"
    sections, summary = score_semantic.aggregate(rubric, rows, provider_complete=True)
    assert sections[0]["status"] == "unavailable"
    assert sections[0]["estimated_score"] is None
    assert summary["total_status"] == "unavailable"

    rubric["sections"][0] |= {
        "aggregation": "formula",
        "aggregation_assessable": False,
        "aggregation_rule_text": "Use an unsupported external formula.",
    }
    rubric["items"][0]["weight"] = None
    rubric["items"][1]["weight"] = None
    sections, summary = score_semantic.aggregate(rubric, rows, provider_complete=True)
    assert sections[0]["status"] == "unavailable"
    assert sections[0]["estimated_score"] is None
    assert summary["total_status"] == "unavailable"
    assert summary["estimated_total"] is None
