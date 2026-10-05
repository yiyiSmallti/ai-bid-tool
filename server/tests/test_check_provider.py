"""DB-free semantic-check provider transport contract tests.

Failure cases enumerated before implementation:

* an unconfigured or non-HTTP model adapter must fail explicitly rather than
  silently falling back to another provider;
* a semantic call outside the job accounting context must fail before HTTP;
* malformed, refused, truncated, or extra-field output must use the shared
  structured-call failure mapping and retain any vendor-reported usage;
* one requested batch must result in exactly one admitted HTTP call, with no
  provider-side retry, split, truncation, or invented usage;
* local requirement IDs and refs must be sent without database identifiers,
  and duplicate, missing, or unknown response IDs must survive wire parsing so
  the service can reject them against the request;
* reservation must be calculated from the exact body that is sent, including
  the strict schema and immutable prompt/adapter versions.

All vendor traffic below uses ``httpx.MockTransport`` and synthetic text.
"""

import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from app.core.config import Settings
from app.providers.base import ProviderFailure
from app.providers.calls import accounted_call, current_accounting, plan_calls
from app.providers.checking import (
    CHECK_ADAPTER_VERSION,
    CHECK_PROMPT_VERSION,
    CHECK_SCHEMA_VERSION,
    CheckProviderRequest,
    CheckProviderResult,
    CheckWireOutput,
    HTTPCheckProvider,
    check_provider,
    supports_check,
)
from app.providers.llm import OpenAICompatibleExtractor
from app.providers.quotes import serialized_request, zero_quote
from app.schemas.check_contracts import OutboundContext, OutboundText
from cryptography.fernet import Fernet
from pydantic import ValidationError


class Accounting:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.planned: list[int] = []
        self.reservations: list[Decimal] = []
        self.platform_billed: list[bool] = []
        self.completed = []
        self.unknown_calls = []

    def plan(self, first_pass_calls: int) -> None:
        self.events.append("plan")
        self.planned.append(first_pass_calls)

    async def admit(self, quote):
        reserved_charge, platform_billed = quote.reserved_charge, quote.payer == "org_platform"
        self.events.append("admit")
        self.reservations.append(reserved_charge)
        self.platform_billed.append(platform_billed)
        return uuid4()

    async def complete(self, call_id, usage) -> None:
        self.events.append("complete")
        self.completed.append((call_id, usage))

    async def unknown(self, call_id) -> None:
        self.events.append("unknown")
        self.unknown_calls.append(call_id)


class Vendor:
    def __init__(self, output: dict) -> None:
        self.output = output
        self.requests: list[httpx.Request] = []
        self.events: list[str] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.events is not None:
            self.events.append("http")
        return httpx.Response(
            200,
            json={
                "model": "synthetic-check-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(self.output, ensure_ascii=False)},
                    }
                ],
                "usage": {"prompt_tokens": 420, "completion_tokens": 80},
            },
        )


class FakeCheckProvider:
    """Synthetic protocol implementation that still crosses the accounting boundary."""

    name = "test-fake"
    model = "synthetic-check"
    version = "test-check-v1"
    test_only = True

    def __init__(self, wire: dict) -> None:
        self.wire = CheckWireOutput.model_validate(wire)
        self.calls = 0

    def request_body(self, value: CheckProviderRequest) -> dict:
        return {"synthetic_request": value.model_dump(mode="json")}

    def reservation(self, value: CheckProviderRequest) -> Decimal:
        self.request_body(value)
        return Decimal(0)

    async def check(self, value: CheckProviderRequest) -> CheckProviderResult:
        plan_calls(1)

        async def operation():
            self.calls += 1
            usage = self.synthetic_usage()
            return self.wire, usage

        quote = zero_quote(
            "llm",
            "local_free",
            self.name,
            self.model,
            self.version,
            serialized_request(self.request_body(value)),
        )
        wire, usage = await accounted_call(quote, operation)
        return CheckProviderResult(wire=wire, usages=[usage])

    def synthetic_usage(self):
        from app.schemas.contracts import ProviderUsage

        return ProviderUsage(
            provider=self.name,
            model=self.model,
            version=self.version,
            duration_ms=1,
            tokens=3,
            input_tokens=2,
            output_tokens=1,
            usd=0,
            charge=0,
            test_only=True,
        )


def request() -> CheckProviderRequest:
    return CheckProviderRequest(
        requested_requirement_ids=["r1", "r2"],
        context=OutboundContext(
            texts=[
                OutboundText(ref="r1.tender", text="内存不得低于 64 GB。"),
                OutboundText(ref="r1.response_text", text="所投设备内存为 32 GB。"),
                OutboundText(ref="r1.e1", text="设备规格：32 GB 内存。"),
                OutboundText(ref="r2.tender", text="应提供三年质保。"),
                OutboundText(ref="r2.response_text", text="承诺提供三年质保。"),
            ]
        ),
    )


def output() -> dict:
    return {
        "items": [
            {
                "requirement_id": "r1",
                "status": "risk",
                "citations": [
                    {"ref": "r1.tender", "quote": "内存不得低于 64 GB"},
                    {"ref": "r1.response_text", "quote": "内存为 32 GB"},
                ],
                "findings": [
                    {
                        "code": "semantic_contradiction",
                        "severity": "disqualification_risk",
                        "reason": "响应数值低于要求。",
                        "citations": [
                            {"ref": "r1.tender", "quote": "不得低于 64 GB"},
                            {"ref": "r1.response_text", "quote": "32 GB"},
                        ],
                    }
                ],
            },
            {
                "requirement_id": "r2",
                "status": "no_risk_found",
                "citations": [
                    {"ref": "r2.tender", "quote": "三年质保"},
                    {"ref": "r2.response_text", "quote": "三年质保"},
                ],
                "findings": [],
            },
        ]
    }


def adapter(tmp_path, vendor: Vendor) -> HTTPCheckProvider:
    settings = Settings(
        database_url="postgresql+psycopg://synthetic:synthetic@localhost/synthetic",
        encryption_key=Fernet.generate_key().decode(),
        token_key=Fernet.generate_key().decode(),
        data_dir=tmp_path,
        llm_provider="openai",
        llm_model="synthetic-check-model",
        llm_api_key="synthetic-test-key-not-real",
        llm_base_url="https://check.example.test/v1",
        llm_max_output_tokens=2048,
    )
    llm = OpenAICompatibleExtractor(
        settings,
        httpx.MockTransport(vendor),
        platform_model_id="synthetic-check-catalog",
        sale_usd_per_mtok=(2.0, 8.0),
    )
    llm.version = "http-extract-v5:synthetic-check-catalog:7"
    return HTTPCheckProvider(llm)


async def run_check(provider: HTTPCheckProvider, accounting: Accounting):
    token = current_accounting.set(accounting)
    try:
        return await provider.check(request())
    finally:
        current_accounting.reset(token)


async def test_http_check_sends_one_exact_accounted_structured_request(tmp_path):
    vendor = Vendor(output())
    provider = adapter(tmp_path, vendor)
    accounting = Accounting()
    vendor.events = accounting.events

    result = await run_check(provider, accounting)

    assert result.wire.items[0].findings[0].code == "semantic_contradiction"
    assert len(result.usages) == 1
    assert result.usages[0].tokens == 500
    assert result.usages[0] == accounting.completed[0][1]
    assert accounting.planned == [1]
    assert accounting.events == ["plan", "admit", "http", "complete"]
    assert accounting.unknown_calls == []
    assert len(vendor.requests) == 1

    exact_body = provider.request_body(request())
    assert accounting.reservations == [provider.reservation(request())]
    assert accounting.platform_billed == [True]
    assert accounting.reservations[0] == provider.llm.reservation(exact_body)
    sent = json.loads(vendor.requests[0].content)
    assert sent == exact_body
    assert str(vendor.requests[0].url) == "https://check.example.test/v1/chat/completions"
    outbound = json.loads(sent["messages"][1]["content"])
    assert outbound == request().model_dump(mode="json")
    assert set(outbound["requested_requirement_ids"]) == {"r1", "r2"}
    assert [item["ref"] for item in outbound["context"]["texts"]] == [
        "r1.tender",
        "r1.response_text",
        "r1.e1",
        "r2.tender",
        "r2.response_text",
    ]
    schema = sent["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["CheckWireItem"]["additionalProperties"] is False
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert (provider.adapter_version, provider.prompt_version, provider.schema_version) == (
        CHECK_ADAPTER_VERSION,
        CHECK_PROMPT_VERSION,
        CHECK_SCHEMA_VERSION,
    )


def test_wire_preserves_duplicate_missing_and_unknown_ids_for_service_rejection():
    candidate = output()
    candidate["items"] = [
        candidate["items"][0],
        candidate["items"][0],
        {
            "requirement_id": "r999",
            "status": "unknown",
            "citations": [],
            "findings": [],
        },
    ]

    wire = CheckWireOutput.model_validate(candidate)

    assert [item.requirement_id for item in wire.items] == ["r1", "r1", "r999"]
    assert "r2" not in [item.requirement_id for item in wire.items]


def test_wire_contract_forbids_extra_fields():
    candidate = output()
    candidate["items"][0]["vendor_explanation"] = "untrusted"

    with pytest.raises(ValidationError):
        CheckWireOutput.model_validate(candidate)


async def test_check_requires_supported_http_adapter_and_accounting(tmp_path):
    with pytest.raises(ProviderFailure) as unsupported:
        HTTPCheckProvider(object())
    assert unsupported.value.code == "unsupported_check_provider"

    vendor = Vendor(output())
    provider = adapter(tmp_path, vendor)
    with pytest.raises(ProviderFailure) as unaccounted:
        await provider.check(request())
    assert unaccounted.value.code == "check_accounting_required"
    assert vendor.requests == []


async def test_factory_wraps_http_and_reuses_accounted_protocol_fake(tmp_path):
    vendor = Vendor(output())
    http = adapter(tmp_path, vendor)
    assert supports_check(http.llm) is True
    assert isinstance(check_provider(http.llm), HTTPCheckProvider)

    fake = FakeCheckProvider(output())
    assert supports_check(fake) is True
    assert check_provider(fake) is fake
    accounting = Accounting()
    result = await run_check(fake, accounting)
    assert fake.calls == 1
    assert result.usages[0].test_only is True
    assert accounting.planned == [1]
    assert accounting.events == ["plan", "admit", "complete"]
    assert accounting.reservations == [Decimal(0)]
    assert accounting.platform_billed == [False]


def test_factory_rejects_unsupported_capability_with_fixed_code():
    assert supports_check(object()) is False
    with pytest.raises(ProviderFailure) as unsupported:
        check_provider(object())
    assert unsupported.value.code == "check_capability_unavailable"
