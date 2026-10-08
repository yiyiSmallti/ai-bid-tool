"""Fake HTTP gateway hardening protocol; no external calls or database services."""

from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from app.core.errors import ServiceError
from app.providers.clef_gateway import HTTPGatewayChecker
from app.schemas.clef import ClefPlatformConfig
from app.schemas.platform_credentials import CredentialSpec, ResolvedCredential
from pydantic import SecretStr, ValidationError

MODEL_ID = UUID("00000000-0000-4000-8000-000000000001")
GATEWAY_ID = UUID("00000000-0000-4000-8000-000000000002")
CONFIG = ClefPlatformConfig(
    account_id="a" * 32,
    gateway_id="presence",
    enabled=True,
    price_revision=1,
    fixed_sale_price="0.01",
    workers_credential_id=MODEL_ID,
    gateway_credential_id=GATEWAY_ID,
    workers_credential_revision=1,
    gateway_credential_revision=1,
    revision=1,
    updated_at=datetime.now(UTC),
)
GATEWAY = ResolvedCredential(
    credential_id=GATEWAY_ID,
    revision=1,
    secret_version=1,
    provider="cloudflare",
    endpoint="https://api.cloudflare.com/client/v4",
    api_key=SecretStr("synthetic-gateway-secret-1234"),
)
SAFE = {
    "id": "presence",
    "authentication": True,
    "collect_logs": False,
    "logpush": False,
    "cache_ttl": 0,
    "retries": 0,
    "rate_limiting_limit": 200,
    "rate_limiting_interval": 60,
}


@pytest.mark.asyncio
async def test_exact_fixed_metadata_request_and_no_model_call():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert (
            str(request.url)
            == f"https://api.cloudflare.com/client/v4/accounts/{'a' * 32}/ai-gateway/gateways/presence"
        )
        assert request.headers["authorization"] == "Bearer synthetic-gateway-secret-1234"
        assert "cf-aig-authorization" not in request.headers
        assert not request.content
        return httpx.Response(200, json={"success": True, "result": SAFE})

    receipt = await HTTPGatewayChecker(transport=httpx.MockTransport(handler)).check(
        CONFIG, GATEWAY
    )
    assert receipt.gateway_id == "presence"
    assert receipt.authentication is True
    assert receipt.collect_logs is False and receipt.logpush is False
    assert receipt.cache_ttl == 0 and receipt.gateway_retries is False
    assert receipt.rate_limit_requests == 200 and receipt.rate_limit_seconds == 60
    assert receipt.workers_ai_billing_mode == "unified"
    assert len(calls) == 1
    assert "secret" not in receipt.model_dump_json()


@pytest.mark.parametrize("field", list(SAFE))
@pytest.mark.asyncio
async def test_every_missing_hardening_field_blocks(field):
    payload = SAFE.copy()
    payload.pop(field)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"success": True, "result": payload})

    with pytest.raises(ServiceError) as error:
        await HTTPGatewayChecker(transport=httpx.MockTransport(handler)).check(CONFIG, GATEWAY)
    assert error.value.code == "clef_gateway_unsafe"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"authentication": False},
        {"collect_logs": True},
        {"logpush": True},
        {"cache_ttl": 1},
        {"cache_ttl": False},
        {"retries": 1},
        {"retries": "0"},
        {"rate_limiting_limit": 0},
        {"rate_limiting_interval": 0},
        {"rate_limiting_limit": True},
        {"id": "different-gateway"},
        {"workers_ai_billing_mode": "token"},
    ],
)
@pytest.mark.asyncio
async def test_unsafe_state_never_produces_a_receipt(changes):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"success": True, "result": SAFE | changes})
    )
    with pytest.raises(ServiceError) as error:
        await HTTPGatewayChecker(transport=transport).check(CONFIG, GATEWAY)
    assert error.value.code == "clef_gateway_unsafe"


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "clef_gateway_auth_failed"),
        (403, "clef_gateway_auth_failed"),
        (429, "clef_gateway_unavailable"),
        (302, "clef_gateway_unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_http_failures_never_retry_or_follow_redirect(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, headers={"location": "https://elsewhere.example.test/"}, json={}
        )

    with pytest.raises(ServiceError) as error:
        await HTTPGatewayChecker(transport=httpx.MockTransport(handler)).check(CONFIG, GATEWAY)
    assert error.value.code == code and len(calls) == 1


@pytest.mark.asyncio
async def test_timeout_is_not_a_check_and_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("synthetic-secret-must-not-be-returned", request=request)

    with pytest.raises(ServiceError) as error:
        await HTTPGatewayChecker(transport=httpx.MockTransport(handler)).check(CONFIG, GATEWAY)
    assert error.value.code == "clef_gateway_timeout" and len(calls) == 1
    assert "secret" not in error.value.message


@pytest.mark.parametrize(
    "purpose,endpoint",
    [
        ("clef_workers_ai", "https://gateway.ai.cloudflare.com"),
        ("clef_gateway", "https://api.cloudflare.com/client/v4"),
    ],
)
def test_credentials_require_fixed_purpose_and_endpoint(purpose, endpoint):
    CredentialSpec(name="synthetic", purpose=purpose, provider="cloudflare", endpoint=endpoint)
    for changed in ({"provider": "openai"}, {"endpoint": "https://elsewhere.example.test"}):
        with pytest.raises(ValidationError):
            CredentialSpec.model_validate(
                {
                    "name": "synthetic",
                    "purpose": purpose,
                    "provider": "cloudflare",
                    "endpoint": endpoint,
                }
                | changed
            )
