"""Allowlisted authentication metadata only; never generation or paid search."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import httpx

from app.core.errors import ServiceError
from app.providers.sandbox_fetch import _public_address, system_resolver
from app.schemas.platform_credentials import CredentialProbeView, ProbeOutcome, ResolvedCredential

# Official model-list endpoints are metadata operations. Compatible/custom hosts do not
# inherit that claim: they remain unsupported until their own capability is reviewed.
ENDPOINTS = {
    ("openai", "https://api.openai.com/v1"): "https://api.openai.com/v1/models",
    ("anthropic", "https://api.anthropic.com"): "https://api.anthropic.com/v1/models",
}


class MetadataCredentialProbe:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Callable[[str], Awaitable[tuple[str, ...]]] = system_resolver,
    ):
        self.transport = transport
        self.resolver = resolver

    async def authenticate(
        self, credential: ResolvedCredential, *, probe_id: UUID
    ) -> CredentialProbeView:
        started = time.monotonic()
        url = ENDPOINTS.get((credential.provider, credential.endpoint))
        outcome: ProbeOutcome = "unsupported"
        if url is not None:
            try:
                async with asyncio.timeout(5):
                    outcome = await self._request(credential, url)
            except (TimeoutError, httpx.TimeoutException):
                outcome = "timeout"
            except (httpx.HTTPError, OSError, ValueError, UnicodeError):
                outcome = "unavailable"
        return CredentialProbeView(
            probe_id=probe_id,
            credential_id=credential.credential_id,
            tested_revision=credential.revision,
            secret_version=credential.secret_version,
            outcome=outcome,
            duration_ms=int((time.monotonic() - started) * 1000),
            checked_at=datetime.now(UTC),
        )

    async def _request(self, credential: ResolvedCredential, url: str) -> ProbeOutcome:
        target = httpx.URL(url)
        answers = await self.resolver(target.host)
        if not answers or any(not _public_address(answer) for answer in answers):
            raise ServiceError(
                "credential_probe_endpoint_rejected",
                "Credential probe endpoint is not permitted",
                422,
                4,
            )
        headers = {"Host": target.host, "Accept": "application/json", "Accept-Encoding": "identity"}
        if credential.provider == "openai":
            headers["Authorization"] = "Bearer " + credential.api_key.get_secret_value()
        else:
            headers["x-api-key"] = credential.api_key.get_secret_value()
            headers["anthropic-version"] = "2023-06-01"
        request = httpx.Request(
            "GET",
            target.copy_with(host=answers[0]),
            headers=headers,
            extensions={
                "sni_hostname": target.host,
                "timeout": {kind: 5.0 for kind in ("connect", "read", "write", "pool")},
            },
        )
        transport = self.transport or httpx.AsyncHTTPTransport(
            trust_env=False,
            retries=0,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )
        try:
            response = await transport.handle_async_request(request)
            try:
                if response.status_code in (401, 403):
                    return "auth_failed"
                if response.status_code == 429:
                    return "rate_limited"
                if response.status_code != 200:
                    return "unavailable"
                if response.headers.get("content-encoding", "identity") != "identity":
                    return "unavailable"
                raw = bytearray()
                if response.is_stream_consumed:
                    raw.extend(response.content)
                else:
                    async for part in response.aiter_raw():
                        raw.extend(part)
                        if len(raw) > 65536:
                            return "unavailable"
                if len(raw) > 65536:
                    return "unavailable"
                payload = json.loads(raw)
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    return "unavailable"
                if credential.provider == "openai":
                    valid = payload.get("object") == "list" and all(
                        isinstance(item, dict)
                        and item.get("object") == "model"
                        and isinstance(item.get("id"), str)
                        for item in payload["data"]
                    )
                else:
                    valid = isinstance(payload.get("has_more"), bool) and all(
                        isinstance(item, dict)
                        and item.get("type") == "model"
                        and isinstance(item.get("id"), str)
                        for item in payload["data"]
                    )
                return "passed" if valid else "unavailable"
            finally:
                await response.aclose()
        finally:
            if self.transport is None:
                await transport.aclose()
