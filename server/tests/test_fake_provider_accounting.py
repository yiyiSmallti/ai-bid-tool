"""Offline fake-provider boundary scenarios, specified before changing the helpers.

Failures: fixture overrides bypass admission; completed malformed/refused responses lose
usage or settle twice; absent usage clears a hold; cancellation loses settled usage;
OCR overrides or direct test-only calls lose their original fixture behavior.
"""

import asyncio
from uuid import uuid4

import pytest
from app.providers.base import MalformedOutput, ProviderFailure, TruncatedOutput
from app.providers.calls import current_accounting
from app.schemas.contracts import ProviderUsage
from fakes import FakeLLM, FakeOCR
from test_budget_provider_metering import Accounting
from test_response_cards import PhaseOneExtraction


def chunks():
    return [
        {
            "id": uuid4(),
            "document_id": uuid4(),
            "page": 1,
            "text": "The offered appliance memory shall be at least 64 GB.",
        }
    ]


@pytest.fixture
def accounting():
    value = Accounting()
    token = current_accounting.set(value)
    yield value
    current_accounting.reset(token)


async def test_standalone_fixture_inherits_single_metered_boundary(accounting):
    provider = PhaseOneExtraction()
    result = await provider.extract(chunks(), {})
    assert provider.records_calls and provider.calls == 1
    assert len(accounting.quotes) == len(accounting.usages) == 1
    assert result.usage == result.usages[0] == accounting.usages[0]
    assert result.usage.payer == "local_free" and result.usage.task_amount == 0
    assert result.extraction.items[0].starred


@pytest.mark.parametrize("failure_type", [ProviderFailure, MalformedOutput, TruncatedOutput])
async def test_response_failure_settles_known_usage_then_preserves_error(accounting, failure_type):
    usage = ProviderUsage(
        provider="synthetic",
        model="synthetic",
        version="v1",
        duration_ms=1,
        tokens=10,
        usd=0,
        test_only=True,
    )
    failure = (
        ProviderFailure("synthetic refusal", refused=True, usage=[usage])
        if failure_type is ProviderFailure
        else failure_type([usage])
    )

    class Failed(FakeLLM):
        async def _extract(self, chunks, schema):
            raise failure

    with pytest.raises(failure_type) as caught:
        await Failed().extract(chunks(), {})
    assert caught.value is failure
    assert len(accounting.quotes) == len(accounting.usages) == 1
    assert caught.value.usage == accounting.usages
    assert accounting.usages[0].payer == "local_free"
    assert not accounting.unknowns


async def test_failure_without_usage_keeps_unknown_hold(accounting):
    class Failed(FakeLLM):
        async def _extract(self, chunks, schema):
            raise ProviderFailure("synthetic timeout", retryable=True)

    with pytest.raises(ProviderFailure, match="timeout"):
        await Failed().extract(chunks(), {})
    assert len(accounting.quotes) == len(accounting.unknowns) == 1
    assert not accounting.usages


async def test_cancelled_fake_drains_one_admitted_call_and_keeps_cancellation(accounting):
    entered, release = asyncio.Event(), asyncio.Event()

    class Slow(FakeLLM):
        async def _extract(self, chunks, schema):
            entered.set()
            await release.wait()
            return await super()._extract(chunks, schema)

    pending = asyncio.create_task(Slow().extract(chunks(), {}))
    await entered.wait()
    pending.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert len(accounting.quotes) == len(accounting.usages) == 1
    assert not accounting.unknowns


async def test_ocr_hook_preserves_content_and_single_usage(accounting):
    class OCR(FakeOCR):
        async def _recognize(self, image, page):
            result = await super()._recognize(image, page)
            return result.model_copy(update={"text": "custom synthetic page"})

    result = await OCR().recognize(b"synthetic image", 3)
    assert result.text == "custom synthetic page"
    assert len(accounting.quotes) == len(accounting.usages) == 1
    assert result.usage == accounting.usages[0]
    assert result.usage.ocr_pages == 1 and result.usage.task_amount == 0


async def test_fake_direct_calls_remain_explicitly_test_only():
    provider = FakeLLM()
    result = await provider.extract(chunks(), {})
    assert provider.test_only and result.usage.test_only and provider.calls == 1
    assert (await FakeOCR().recognize(b"synthetic", 1)).usage.test_only
