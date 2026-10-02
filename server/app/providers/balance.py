"""Read-only balance probes for documented vendor endpoints, without LLM calls."""

import asyncio
from decimal import Decimal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, SecretStr, ValidationError


class BalanceInfo(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    total_balance: Decimal = Field(allow_inf_nan=False)
    granted_balance: Decimal = Field(allow_inf_nan=False)
    topped_up_balance: Decimal = Field(allow_inf_nan=False)


class DeepSeekBalance(BaseModel):
    is_available: bool = Field(strict=True)
    balance_infos: list[BalanceInfo] = Field(min_length=1, max_length=10)


def supports_balance(provider: str, base_url: str | None) -> bool:
    url = urlsplit(base_url or "")
    return (
        provider == "openai"
        and url.scheme == "https"
        and url.netloc == "api.deepseek.com"
        and url.path in {"", "/", "/v1", "/v1/"}
        and not url.query
        and not url.fragment
    )


async def provider_balance(
    provider: str, base_url: str | None, key: SecretStr, transport=None
) -> dict:
    if not supports_balance(provider, base_url):
        return {"status": "unsupported", "message": "该服务商不提供余量查询"}
    try:
        async with (
            asyncio.timeout(5),
            httpx.AsyncClient(transport=transport, timeout=5, follow_redirects=False) as client,
        ):
            response = await client.get(
                "https://api.deepseek.com/user/balance",
                headers={"Authorization": f"Bearer {key.get_secret_value()}"},
            )
        if response.status_code != 200:
            return {"status": "unavailable", "message": "暂时无法查询"}
        balance = DeepSeekBalance.model_validate(response.json())
    except (TimeoutError, httpx.TransportError, ValueError, ValidationError):
        return {"status": "unavailable", "message": "暂时无法查询"}
    return {"status": "supported", **balance.model_dump(mode="json")}
