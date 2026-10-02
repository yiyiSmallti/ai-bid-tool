"""Real HTTP extraction adapters driven end to end through the API and job processor.

Vendor endpoints are simulated with httpx.MockTransport; no external service is called.
"""

import asyncio
import json
import re
import time
from uuid import uuid4

import httpx
import pymupdf
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.models.entities import Requirement, UsageRecord
from app.providers.base import ProviderFailure
from app.providers.llm import AnthropicExtractor, OpenAICompatibleExtractor
from conftest import FakeQueue
from pydantic import ValidationError
from sqlalchemy import select
from test_api import create_document, run_job
from test_job_boundaries import session_for

SYNTHETIC_KEY = "synthetic-test-key-not-real"
GOOD_ITEMS = [
    {
        "category": "technical",
        "starred": False,
        "text": "内存不低于 64 GB",
        "ref": "1",
        "quote": "Minimum memory is 64 GB.",
        "condition": {"param": "memory", "op": ">=", "value": "64", "unit": "GB"},
    },
    {
        "category": "qualification",
        "starred": False,
        "text": "提供有效证书",
        "ref": "2",
        "quote": "A valid certificate must be provided.",
        "condition": None,
    },
]


def anthropic_reply(items, stop="end_turn", status=200):
    body = {
        "model": "claude-opus-5-5",
        "stop_reason": stop,
        "content": [
            {"type": "thinking", "thinking": ""},
            {"type": "text", "text": json.dumps({"items": items}, ensure_ascii=False)},
        ],
        "usage": {"input_tokens": 1200, "output_tokens": 300},
    }
    return httpx.Response(status, json=body)


class Vendor:
    """Replays scripted responses and records every request it receives."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def transport(self):
        return httpx.MockTransport(self)


def settings_for(tmp_path, provider, **overrides):
    values = {
        "data_dir": tmp_path,
        "llm_provider": provider,
        "llm_api_key": SYNTHETIC_KEY,
        "llm_model": None if provider == "anthropic" else "synthetic-model",
    }
    return Settings(**(values | overrides))


async def usage_rows(app, tenants):
    async with app.state.db.transaction(tenants["orgs"][0]) as session:
        return (
            await session.scalars(select(UsageRecord).where(UsageRecord.provider != "tesseract"))
        ).all()


async def requirement_rows(app, tenants):
    async with app.state.db.transaction(tenants["orgs"][0]) as session:
        return (await session.scalars(select(Requirement).order_by(Requirement.page))).all()


async def test_anthropic_extraction_saves_cited_requirements_and_cost(tenants, tmp_path, pdf_bytes):
    vendor = Vendor(anthropic_reply(GOOD_ITEMS))
    settings = settings_for(
        tmp_path, "anthropic", llm_input_usd_per_mtok=4.0, llm_output_usd_per_mtok=20.0
    )
    llm = AnthropicExtractor(settings, transport=vendor.transport())
    app = create_app(settings, llm=llm, queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        assert (await api.get("/health")).json()["data"]["real_llm_configured"] is True
        _, document = await create_document(api, header, pdf_bytes)
        await run_job(api, app, header, document, "parse")
        _, status = await run_job(api, app, header, document, "extract")

        assert status["status"] == "succeeded", status
        assert status["result"]["created"] == 2
        assert status["result"]["cost"] == {"llm_tokens": 1500, "ocr_pages": 0, "usd": 0.0108}
        rows = await requirement_rows(app, tenants)
        assert [(r.page, r.quote) for r in rows] == [
            (1, "Minimum memory is 64 GB."),
            (2, "A valid certificate must be provided."),
        ]
        assert rows[0].condition == {"param": "memory", "op": ">=", "value": "64", "unit": "GB"}
        assert rows[1].condition == {}
        [usage] = await usage_rows(app, tenants)
        assert (usage.provider, usage.model, usage.tokens) == ("anthropic", "claude-opus-5-5", 1500)
        assert float(usage.usd) == pytest.approx(0.0108)

    [request] = vendor.requests
    body = json.loads(request.content)
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == SYNTHETIC_KEY
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert request.headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["effort"] == "high"
    assert "tool_choice" not in body and "thinking" not in body
    assert '<page number="1">' in body["messages"][0]["content"]
    assert SYNTHETIC_KEY not in json.dumps(status)


async def test_openai_compatible_extraction_without_prices_records_unknown_cost(
    tenants, tmp_path, pdf_bytes
):
    reply = {
        "model": "synthetic-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps({"items": GOOD_ITEMS}, ensure_ascii=False)},
            }
        ],
        "usage": {"prompt_tokens": 900, "completion_tokens": 100},
    }
    vendor = Vendor(httpx.Response(200, json=reply))
    settings = settings_for(tmp_path, "openai", llm_base_url="https://llm.example.test/v1")
    llm = OpenAICompatibleExtractor(settings, transport=vendor.transport())
    app = create_app(settings, llm=llm, queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        _, document = await create_document(api, header, pdf_bytes)
        await run_job(api, app, header, document, "parse")
        _, status = await run_job(api, app, header, document, "extract")
        assert status["status"] == "succeeded", status
        assert status["result"]["cost"] == {"llm_tokens": 1000, "ocr_pages": 0, "usd": None}
        assert len(await requirement_rows(app, tenants)) == 2

    [request] = vendor.requests
    body = json.loads(request.content)
    assert str(request.url) == "https://llm.example.test/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {SYNTHETIC_KEY}"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True


FAILURES = {
    "rate_limited": (
        httpx.Response(429, json={"error": {"type": "rate_limit_error"}}),
        "queued",
        "provider_unavailable",
        3,
        0,
    ),
    "overloaded": (
        httpx.Response(529, json={"error": {"type": "overloaded_error"}}),
        "queued",
        "provider_unavailable",
        3,
        0,
    ),
    "timeout": (httpx.ReadTimeout("synthetic"), "queued", "provider_unavailable", 3, 0),
    "bad_key": (
        httpx.Response(401, json={"error": {"type": "authentication_error"}}),
        "failed",
        "provider_unavailable",
        4,
        0,
    ),
    "refused": (anthropic_reply([], stop="refusal"), "failed", "provider_refused", 4, 1),
    # Zhipu rate limits stay retryable; its quota and plan codes do not recover in time.
    "zhipu_rate_limited": (
        httpx.Response(429, json={"error": {"code": "1302", "message": "rate limited"}}),
        "queued",
        "provider_unavailable",
        3,
        0,
    ),
    "zhipu_quota_used_up": (
        httpx.Response(429, json={"error": {"code": "1308", "message": "limit reached"}}),
        "failed",
        "provider_quota_exhausted",
        4,
        0,
    ),
    "openai_insufficient_quota": (
        httpx.Response(429, json={"error": {"type": "insufficient_quota"}}),
        "failed",
        "provider_quota_exhausted",
        4,
        0,
    ),
    "payment_required": (
        httpx.Response(402, json={"error": {"message": "Insufficient Balance"}}),
        "failed",
        "provider_quota_exhausted",
        4,
        0,
    ),
    "unknown_page": (
        anthropic_reply([GOOD_ITEMS[0] | {"ref": "9"}]),
        "failed",
        "invalid_provider_output",
        4,
        1,
    ),
    "altered_quote": (
        anthropic_reply([GOOD_ITEMS[0] | {"quote": "Memory must be 64GB"}]),
        "failed",
        "invalid_citation",
        4,
        1,
    ),
}


@pytest.mark.parametrize("case", FAILURES)
async def test_vendor_failures_map_to_job_states(case, tenants, tmp_path, pdf_bytes):
    reply, job_status, code, exit_code, billed_calls = FAILURES[case]
    settings = settings_for(tmp_path, "anthropic")
    llm = AnthropicExtractor(settings, transport=Vendor(reply).transport())
    app = create_app(settings, llm=llm, queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        _, document = await create_document(api, header, pdf_bytes)
        await run_job(api, app, header, document, "parse")
        if job_status == "queued":
            with pytest.raises(ProviderFailure):
                await run_job(api, app, header, document, "extract")
            job = (
                await api.post(f"/documents/{document}/extract", headers=header, json={})
            ).json()["data"]["job_id"]
            status = (await api.get(f"/jobs/{job}", headers=header)).json()["data"]
        else:
            _, status = await run_job(api, app, header, document, "extract")

        assert status["status"] == job_status
        assert (status["error"]["code"], status["error"]["exit_code"]) == (code, exit_code)
        assert SYNTHETIC_KEY not in json.dumps(status)
        assert await requirement_rows(app, tenants) == []
        assert len(await usage_rows(app, tenants)) == billed_calls


async def test_failure_in_later_batch_keeps_usage_of_finished_batches(tenants, tmp_path):
    with pymupdf.open() as pdf:
        for line in ("Minimum memory is 64 GB.", "A valid certificate must be provided."):
            page = pdf.new_page()
            page.insert_textbox(pdf[-1].rect + (40, 40, -40, -40), (line + " ") * 40)
        content = pdf.tobytes()
    vendor = Vendor(anthropic_reply([]), httpx.Response(500, json={"error": {"type": "api_error"}}))
    settings = settings_for(tmp_path, "anthropic", llm_batch_chars=1000)
    llm = AnthropicExtractor(settings, transport=vendor.transport())
    app = create_app(settings, llm=llm, queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        _, document = await create_document(api, header, content)
        await run_job(api, app, header, document, "parse")
        with pytest.raises(ProviderFailure):
            await run_job(api, app, header, document, "extract")
        assert len(vendor.requests) == 2
        [usage] = await usage_rows(app, tenants)
        assert usage.tokens == 1500


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_provider": "anthropic"},
        {"llm_provider": "openai", "llm_api_key": SYNTHETIC_KEY},
        {"llm_provider": "openai", "llm_model": "synthetic-model"},
        {"llm_provider": "unknown"},
    ],
)
def test_incomplete_provider_settings_refuse_to_start(overrides, tmp_path):
    with pytest.raises(ValidationError):
        Settings(data_dir=tmp_path, **overrides)


async def test_endless_keep_alive_hits_the_total_deadline(tmp_path):
    async def blank_lines():
        # What a busy vendor sends instead of an answer: keep-alive bytes forever.
        while True:
            await asyncio.sleep(0.05)
            yield b"\n"

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=blank_lines()))
    settings = settings_for(tmp_path, "anthropic", llm_timeout_seconds=0.5)
    llm = AnthropicExtractor(settings, transport=transport)
    chunk = {
        "id": uuid4(),
        "document_id": uuid4(),
        "page": 1,
        "text": "x",
        "citation_verified": True,
    }
    started = time.monotonic()
    with pytest.raises(ProviderFailure) as failure:
        await llm.extract([chunk], {})
    assert failure.value.retryable and time.monotonic() - started < 3


async def test_batches_run_concurrently_in_order_and_stop_after_a_failure(tmp_path):
    in_flight, peak, seen = 0, 0, []

    async def handler(request):
        nonlocal in_flight, peak
        page = int(
            json.loads(request.content)["messages"][0]["content"].split('number="')[1].split('"')[0]
        )
        seen.append(page)
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.1)
        in_flight -= 1
        if page == 99:
            return httpx.Response(500, json={"error": {"type": "api_error"}})
        quote = f"Requirement on page {page}"
        return anthropic_reply(
            [
                {
                    "category": "technical",
                    "starred": False,
                    "text": quote,
                    "ref": str(page),
                    "quote": quote,
                    "condition": None,
                }
            ]
        )

    def chunk(page):
        return {
            "id": uuid4(),
            "document_id": uuid4(),
            "page": page,
            "text": "x" * 900 + f"\nRequirement on page {page}",
            "citation_verified": True,
        }

    settings = settings_for(tmp_path, "anthropic", llm_batch_chars=1000, llm_concurrency=2)
    llm = AnthropicExtractor(settings, transport=httpx.MockTransport(handler))
    result = await llm.extract([chunk(page) for page in range(1, 6)], {})
    assert peak == 2
    assert [item.source.page for item in result.extraction.items] == [1, 2, 3, 4, 5]
    assert result.usage.tokens == 5 * 1500

    seen.clear()
    pages = [99, 1, 2, 3, 4, 5]
    with pytest.raises(ProviderFailure) as failure:
        await llm.extract([chunk(page) for page in pages], {})
    # The failing batch stops batches that had not started; finished ones are billed.
    assert len(seen) < len(pages)
    assert len(failure.value.usage) == len(seen) - 1


async def test_request_options_reach_the_vendor_without_overriding_core_fields(tmp_path):
    vendor = Vendor(anthropic_reply([]))
    settings = settings_for(
        tmp_path,
        "anthropic",
        llm_request_options='{"thinking": {"type": "disabled"}, "model": "not-this-one"}',
    )
    llm = AnthropicExtractor(settings, transport=vendor.transport())
    chunk = {
        "id": uuid4(),
        "document_id": uuid4(),
        "page": 1,
        "text": "x",
        "citation_verified": True,
    }
    await llm.extract([chunk], {})
    body = json.loads(vendor.requests[0].content)
    assert body["thinking"] == {"type": "disabled"}
    assert body["model"] == "claude-opus-5-5"
    for invalid in ("not json", "[1, 2]"):
        with pytest.raises(ValidationError):
            settings_for(tmp_path, "anthropic", llm_request_options=invalid)


async def test_truncation_down_to_a_single_line_fails_and_bills_each_call(tenants, tmp_path):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text(
            (40, 60), "Line one is required.\nLine two is required.\nLine three.\nLine four."
        )
        content = pdf.tobytes()
    requests: list[str] = []

    def vendor(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content)["messages"][0]["content"])
        return anthropic_reply(GOOD_ITEMS[:1], stop="max_tokens")

    settings = settings_for(tmp_path, "anthropic", llm_concurrency=1)
    app = create_app(
        settings,
        llm=AnthropicExtractor(settings, transport=httpx.MockTransport(vendor)),
        queue=FakeQueue(),
    )
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        _, document = await create_document(api, header, content)
        await run_job(api, app, header, document, "parse")
        _, status = await run_job(api, app, header, document, "extract")
        assert (status["status"], status["error"]["code"]) == ("failed", "invalid_provider_output")
        assert "single line" in status["error"]["message"]
        assert await requirement_rows(app, tenants) == []
        assert len(await usage_rows(app, tenants)) == len(requests)

    # The page, then halves of its lines, each still presented as page 1, down to one line.
    shown = [re.findall(r'<page number="(\d+)">\n(.*?)\n</page>', r, re.S) for r in requests]
    assert all(len(pages) == 1 and pages[0][0] == "1" for pages in shown)
    lines = [len(pages[0][1].splitlines()) for pages in shown]
    assert lines[0] == 4 and lines[-1] == 1 and lines == sorted(lines, reverse=True)


async def test_quota_message_names_the_reset_time_but_no_other_vendor_text(
    tenants, tmp_path, pdf_bytes
):
    vendor_message = "已达到 5 小时的使用上限。您的限额将在 2026-10-02 14:01:58 重置。PRIVATE-ECHO"
    reply = httpx.Response(429, json={"error": {"code": "1308", "message": vendor_message}})
    settings = settings_for(tmp_path, "openai", llm_base_url="https://llm.example.test/v1")
    llm = OpenAICompatibleExtractor(settings, transport=Vendor(reply).transport())
    app = create_app(settings, llm=llm, queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        _, document = await create_document(api, header, pdf_bytes)
        await run_job(api, app, header, document, "parse")
        _, status = await run_job(api, app, header, document, "extract")
    assert status["status"] == "failed" and status["attempts"] == 1
    message = status["error"]["message"]
    assert "resets at 2026-10-02 14:01:58" in message
    assert "Contact your system administrator" in message
    assert "PRIVATE-ECHO" not in message and "使用上限" not in message


def not_json():
    body = {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "not json"}],
        "usage": {"input_tokens": 5, "output_tokens": 1},
    }
    return httpx.Response(200, json=body)


@pytest.mark.parametrize("recovers", [True, False])
async def test_malformed_output_is_retried_in_smaller_parts(recovers, tenants, tmp_path, pdf_bytes):
    requests: list[str] = []

    def vendor(request: httpx.Request) -> httpx.Response:
        content = json.loads(request.content)["messages"][0]["content"]
        requests.append(content)
        pages = re.findall(r'<page number="(\d+)">', content)
        if len(pages) > 1 or not recovers:
            return not_json()
        return anthropic_reply([GOOD_ITEMS[int(pages[0]) - 1]])

    settings = settings_for(tmp_path, "anthropic", llm_concurrency=1)
    app = create_app(
        settings,
        llm=AnthropicExtractor(settings, transport=httpx.MockTransport(vendor)),
        queue=FakeQueue(),
    )
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        _, document = await create_document(api, header, pdf_bytes)
        await run_job(api, app, header, document, "parse")
        _, status = await run_job(api, app, header, document, "extract")
        # Every call, including the malformed ones, is billed.
        if recovers:
            assert status["status"] == "succeeded", status
            assert len(requests) == 3 and len(await requirement_rows(app, tenants)) == 2
            # One malformed call (6 tokens) plus two good ones (1,500 each), summed.
            assert status["result"]["cost"]["llm_tokens"] == 6 + 2 * 1500
        else:
            assert len(await usage_rows(app, tenants)) == len(requests)
            assert (status["status"], status["error"]["code"]) == (
                "failed",
                "invalid_provider_output",
            )
            assert await requirement_rows(app, tenants) == []


async def test_items_with_an_empty_quote_are_rejected_not_fatal(tenants, tmp_path, pdf_bytes):
    reply = anthropic_reply([GOOD_ITEMS[0], GOOD_ITEMS[1] | {"quote": "  "}])
    settings = settings_for(tmp_path, "anthropic")
    llm = AnthropicExtractor(settings, transport=Vendor(reply).transport())
    app = create_app(settings, llm=llm, queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        _, document = await create_document(api, header, pdf_bytes)
        await run_job(api, app, header, document, "parse")
        _, status = await run_job(api, app, header, document, "extract")
        assert status["status"] == "succeeded", status
        assert [r.quote for r in await requirement_rows(app, tenants)] == [GOOD_ITEMS[0]["quote"]]
    assert status["result"]["rejected"] == [
        {"position": "第 2 页", "quote": "  ", "reason": "empty_quote"}
    ]
    assert any("quote was empty" in warning for warning in status["result"]["warnings"])


async def test_unexpected_failure_keeps_billed_usage_and_logs_no_message(
    tenants, tmp_path, pdf_bytes, monkeypatch, caplog
):
    secret = "SECRET-" + "TENDER-TEXT"  # built at runtime so the source line does not hold it

    def broken(self, wire, batch, usages):
        raise RuntimeError(secret)

    monkeypatch.setattr(AnthropicExtractor, "attach", broken)
    settings = settings_for(tmp_path, "anthropic")
    llm = AnthropicExtractor(settings, transport=Vendor(anthropic_reply(GOOD_ITEMS)).transport())
    app = create_app(settings, llm=llm, queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        _, document = await create_document(api, header, pdf_bytes)
        await run_job(api, app, header, document, "parse")
        _, status = await run_job(api, app, header, document, "extract")
        assert (status["status"], status["error"]["code"]) == ("failed", "processing_failed")
        # The vendor call was billed and is recorded although nothing was saved.
        assert len(await usage_rows(app, tenants)) == 1
        assert await requirement_rows(app, tenants) == []
    assert "RuntimeError" in caplog.text and "SECRET-TENDER-TEXT" not in caplog.text
    assert "SECRET-TENDER-TEXT" not in json.dumps(status)
