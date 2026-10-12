"""Offline request-to-admission scenarios for every currently metered capability."""

import hashlib
from uuid import uuid4

import httpx
import pytest
from app.core.config import Settings
from app.providers.base import ProviderFailure
from app.providers.calls import accounted_call, current_accounting
from app.providers.llm import OpenAICompatibleExtractor
from app.providers.local_ocr import LocalOCR
from app.providers.search import SearXNGSearch
from app.schemas.contracts import ProviderUsage
from cryptography.fernet import Fernet


class Accounting:
    def __init__(self):
        self.quotes = []
        self.usages = []
        self.unknowns = []

    def plan(self, count):
        pass

    async def admit(self, quote):
        self.quotes.append(quote)
        return uuid4()

    async def complete(self, call_id, usage):
        self.usages.append(usage)

    async def unknown(self, call_id):
        self.unknowns.append(call_id)

    async def not_sent(self, call_id):
        pass


@pytest.fixture
def accounting():
    value = Accounting()
    token = current_accounting.set(value)
    yield value
    current_accounting.reset(token)


def adapter(**options):
    return OpenAICompatibleExtractor(
        Settings(
            database_url="postgresql+psycopg://synthetic:synthetic@localhost/synthetic",
            encryption_key=Fernet.generate_key().decode(),
            token_key=Fernet.generate_key().decode(),
            llm_model="synthetic",
            **options,
        ),
        org_owned=True,
    )


def test_exact_serialization_plan_and_token_bounds():
    provider = adapter()
    body = {"model": "synthetic", "max_tokens": 3, "messages": [{"content": "中文"}]}
    quote = provider.quote(body)
    wire = provider.serialized_request(body)
    assert quote.request_sha256 == hashlib.sha256(wire).hexdigest()
    assert quote.input_tokens_upper_bound == len(wire) + 4096


async def test_no_accounting_rejects_before_dispatch():
    dispatched = False

    async def operation():
        nonlocal dispatched
        dispatched = True
        return None, ProviderUsage(provider="local", model="local", version="v1", duration_ms=0)

    quote = LocalOCR("eng", None).quote(b"synthetic-image", 1)
    with pytest.raises(ProviderFailure, match="account"):
        await accounted_call(quote, operation)
    assert not dispatched


@pytest.mark.parametrize("reported_model", ["synthetic", "synthetic-fallback"])
async def test_llm_admits_exact_sent_bytes_and_records_fixed_usage(accounting, reported_model):
    provider = adapter()
    body = {"model": "synthetic", "max_tokens": 4, "messages": [{"content": "test"}]}

    def handler(request):
        assert accounting.quotes[-1].request_sha256 == hashlib.sha256(request.content).hexdigest()
        return httpx.Response(
            200,
            json={
                "model": reported_model,
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                "choices": [],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        _, usage = await provider.post(client, "https://synthetic.test/v1", {}, body)
    assert accounting.quotes[-1].model == "synthetic"
    assert usage.model == reported_model
    assert len(accounting.usages) == 1


async def test_search_each_dispatch_settles_even_invalid_response(accounting):
    provider = SearXNGSearch(
        "http://synthetic.test",
        httpx.MockTransport(lambda request: httpx.Response(200, json={"results": "invalid"})),
    )
    with pytest.raises(ProviderFailure):
        await provider.search("synthetic query")
    assert len(accounting.quotes) == len(accounting.usages) == 1
    assert accounting.usages[0].search_requests == 1
    assert not accounting.unknowns


async def test_local_ocr_failure_still_records_usage(accounting, monkeypatch):
    import app.providers.local_ocr as local_ocr

    def fail(image):
        raise RuntimeError("synthetic OCR failure")

    monkeypatch.setattr(local_ocr.pymupdf, "Pixmap", fail)
    provider = LocalOCR("eng", None)
    for page in (1, 2):
        with pytest.raises(ProviderFailure):
            await provider.recognize(b"image", page)
    assert len(accounting.quotes) == len(accounting.usages) == 2


async def test_browser_execution_uses_call_plan_and_settles_failure(accounting, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from uuid import uuid4

    from app.providers.browser import SocketBrowserProvider
    from app.providers.sandbox_runtime import RunDescriptor, SandboxFailure

    provider = SocketBrowserProvider(SimpleNamespace(profile_digest="synthetic-profile"))

    async def fail(descriptor, data, fetcher):
        raise SandboxFailure("synthetic_failure")

    monkeypatch.setattr(provider, "_execute_unaccounted", fail)
    descriptor = RunDescriptor(
        org_id=uuid4(),
        job_id=uuid4(),
        attempt_id=uuid4(),
        input_sha256=hashlib.sha256(b"synthetic html").hexdigest(),
        purpose="prototype_offline",
    )
    with pytest.raises(SandboxFailure):
        await provider._execute(descriptor, b"synthetic html", None)
    assert len(accounting.quotes) == len(accounting.usages) == 1
    assert accounting.quotes[0].capability == "browser"
