"""Read-only Cloudflare gateway hardening check; no model request or hidden retry."""

from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from app.core.errors import ServiceError
from app.schemas.clef import ClefGatewayCheck, ClefPlatformConfig
from app.schemas.platform_credentials import ResolvedCredential


def parse_gateway(payload: dict, config: ClefPlatformConfig) -> ClefGatewayCheck:
    """Require explicit gateway properties; omitted unsafe fields never become defaults."""
    if not isinstance(payload, dict):
        raise ValueError("Invalid gateway metadata")
    result = payload.get("result")
    if payload.get("success") is not True or not isinstance(result, dict):
        raise ValueError("Invalid gateway metadata")
    if result.get("id") != config.gateway_id:
        raise ValueError("Gateway identity mismatch")
    if (
        result.get("authentication") is not True
        or result.get("collect_logs") is not False
        or result.get("logpush") is not False
        or type(result.get("cache_ttl")) is not int
        or result["cache_ttl"] != 0
        or "retries" not in result
        or (
            result["retries"] is not False
            and (type(result["retries"]) is not int or result["retries"] != 0)
        )
        or result.get("workers_ai_billing_mode", "unified") != "unified"
    ):
        raise ValueError("Unsafe gateway settings")
    limit, interval = result.get("rate_limiting_limit"), result.get("rate_limiting_interval")
    if type(limit) is not int or type(interval) is not int or limit <= 0 or interval <= 0:
        raise ValueError("Missing gateway rate limit")
    # The fixed workers-ai AI Gateway route uses platform unified billing. The
    # metadata endpoint does not supply a billing-mode field on every API version.
    return ClefGatewayCheck(
        gateway_id=config.gateway_id,
        authentication=True,
        collect_logs=False,
        logpush=False,
        cache_ttl=0,
        gateway_retries=False,
        rate_limit_requests=limit,
        rate_limit_seconds=interval,
        workers_ai_billing_mode="unified",
        checked_at=datetime.now(UTC),
    )


class HTTPGatewayChecker:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def check(
        self, config: ClefPlatformConfig, gateway: ResolvedCredential
    ) -> ClefGatewayCheck:
        if (
            gateway.provider != "cloudflare"
            or gateway.endpoint != "https://api.cloudflare.com/client/v4"
        ):
            raise ServiceError("clef_gateway_unsafe", "Clef gateway credential is invalid", 409, 4)
        url = (
            f"https://api.cloudflare.com/client/v4/accounts/{config.account_id}"
            f"/ai-gateway/gateways/{config.gateway_id}"
        )
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=5,
            ) as client:
                async with client.stream(
                    "GET",
                    url,
                    headers={
                        "Authorization": "Bearer " + gateway.api_key.get_secret_value(),
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                ) as response:
                    if response.status_code in (401, 403):
                        raise ServiceError(
                            "clef_gateway_auth_failed", "Gateway authentication failed", 409, 4
                        )
                    if (
                        response.status_code != 200
                        or response.headers.get("content-encoding", "identity") != "identity"
                    ):
                        raise ServiceError(
                            "clef_gateway_unavailable", "Gateway metadata is unavailable", 503, 3
                        )
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 65536:
                            raise ValueError("Gateway metadata exceeds limit")
                    import json

                    return parse_gateway(json.loads(raw), config)
        except (httpx.TimeoutException, TimeoutError):
            raise ServiceError(
                "clef_gateway_timeout", "Gateway metadata check timed out", 503, 3
            ) from None
        except (httpx.HTTPError, OSError):
            raise ServiceError(
                "clef_gateway_unavailable", "Gateway metadata is unavailable", 503, 3
            ) from None
        except (ValueError, TypeError, KeyError, ValidationError):
            raise ServiceError(
                "clef_gateway_unsafe", "Gateway hardening check failed", 409, 4
            ) from None
