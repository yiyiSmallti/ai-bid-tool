"""Authentication metadata probes: hostile responses, bounded IO and endpoint refusal.

Failure modes include reflected secrets, HTML success, redirects, private DNS, oversized
responses, auth denial and unsupported adapters accidentally issuing content requests.
"""

import json
from uuid import uuid4

import httpx
import pytest
from app.providers.credential_probe import MetadataCredentialProbe
from app.schemas.platform_credentials import ResolvedCredential
from pydantic import SecretStr

SECRET = "synthetic-metadata-key-123456789"


def credential(provider="openai", endpoint="https://api.openai.com/v1"):
    return ResolvedCredential(uuid4(), 2, 2, provider, endpoint, SecretStr(SECRET))


async def public_dns(host):
    return ("93.184.216.34",)


@pytest.mark.parametrize(
    "status,body,outcome",
    [
        (200, {"object": "list", "data": [{"id": SECRET, "object": "model"}]}, "passed"),
        (200, {"secret": SECRET}, "unavailable"),
        (401, {"error": SECRET}, "auth_failed"),
        (429, {"error": SECRET}, "rate_limited"),
        (302, {"error": SECRET}, "unavailable"),
    ],
)
async def test_metadata_probe_only_safe_result(status, body, outcome, caplog):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET" and request.url.path == "/v1/models"
        assert request.headers["Authorization"] == "Bearer " + SECRET
        return httpx.Response(
            status,
            json=body,
            headers={"x-leak": SECRET, "location": "https://elsewhere.test/" + SECRET},
        )

    probe = MetadataCredentialProbe(transport=httpx.MockTransport(respond), resolver=public_dns)
    result = await probe.authenticate(credential(), probe_id=uuid4())
    assert result.outcome == outcome and len(requests) == 1
    assert SECRET not in result.model_dump_json() + caplog.text


async def test_unsupported_private_and_large_probe_never_pass():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=b"x" * 65537)

    probe = MetadataCredentialProbe(transport=httpx.MockTransport(respond), resolver=public_dns)
    assert (
        await probe.authenticate(
            credential("perplexity", "https://api.perplexity.ai"), probe_id=uuid4()
        )
    ).outcome == "unsupported"
    assert requests == []
    assert (await probe.authenticate(credential(), probe_id=uuid4())).outcome == "unavailable"
    assert len(requests) == 1

    async def private_dns(host):
        return ("127.0.0.1",)

    probe = MetadataCredentialProbe(transport=httpx.MockTransport(respond), resolver=private_dns)
    with pytest.raises(Exception) as caught:
        await probe.authenticate(credential(), probe_id=uuid4())
    assert caught.value.code == "credential_probe_endpoint_rejected"
    assert len(requests) == 1


async def test_probe_does_not_accept_public_health_page_or_follow_redirect():
    probe = MetadataCredentialProbe(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="<html>healthy</html>")),
        resolver=public_dns,
    )
    result = await probe.authenticate(credential(), probe_id=uuid4())
    assert result.outcome == "unavailable"
    assert "html" not in json.dumps(result.model_dump(mode="json"))
