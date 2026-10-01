"""Requirement extraction through vendor HTTP APIs, called with httpx only."""

import json
import os
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.entities import PlatformModel
from app.providers.base import ProviderFailure
from app.providers.disabled import DisabledLLM
from app.schemas.contracts import (
    Category,
    ExtractedRequirement,
    Extraction,
    LLMResult,
    ProviderUsage,
    Source,
)

ADAPTER_VERSION = "http-extract-v1"
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}

SYSTEM_PROMPT = """你是招标文件分析助手。从用户给出的招标文件页面中，抽取投标人必须响应的全部要求：
- qualification：资格要求（资质、业绩、人员、财务、信誉等）
- technical：技术参数与功能要求
- scoring：评分项与评分标准
- substantive：实质性条款，包括带 ★ 的条款、否决投标或废标条款

每条要求：
- text：用简洁中文复述这条要求。
- quote：逐字复制该要求在页面中的原文连续片段，不得改写、省略、补全或合并不同页的内容。
- page：quote 所在页的页码，必须是给出的页码之一。
- starred：原文带 ★，或属于实质性、否决投标、废标条款时为 true。
- condition：可量化的技术参数填写 param（参数名）、op（比较方式）、value（数值或文本）、unit（单位，无则为 null）；无法量化时为 null。

只抽取页面中实际写明的要求，不推测、不编造；没有要求的页面不输出。"""

CONDITION_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "null"},
        {
            "type": "object",
            "properties": {
                "param": {"type": "string"},
                "op": {
                    "type": "string",
                    "enum": ["=", "!=", ">", ">=", "<", "<=", "in", "contains"],
                },
                "value": {"type": "string"},
                "unit": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["param", "op", "value", "unit"],
            "additionalProperties": False,
        },
    ]
}

# The model cites pages by number; UUIDs are attached afterwards so it never has
# to reproduce identifiers. The final Extraction is validated by the processor.
WIRE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": [c.value for c in Category]},
                    "starred": {"type": "boolean"},
                    "text": {"type": "string"},
                    "page": {"type": "integer"},
                    "quote": {"type": "string"},
                    "condition": CONDITION_SCHEMA,
                },
                "required": ["category", "starred", "text", "page", "quote", "condition"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


class WireCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    param: str
    op: str
    value: str
    unit: str | None


class WireItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category
    starred: bool
    text: str
    page: int
    quote: str
    condition: WireCondition | None


class WireOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[WireItem]


def batches(chunks: list[dict], budget: int) -> list[list[dict]]:
    # A page larger than the budget gets its own batch; pages are never truncated.
    groups: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for chunk in chunks:
        length = len(chunk["text"])
        if current and size + length > budget:
            groups.append(current)
            current, size = [], 0
        current.append(chunk)
        size += length
    if current:
        groups.append(current)
    return groups


def render_pages(batch: list[dict]) -> str:
    pages = "\n\n".join(f'<page number="{c["page"]}">\n{c["text"]}\n</page>' for c in batch)
    return f"以下是招标文件的部分页面：\n\n{pages}\n\n按要求抽取这些页面中的全部要求。"


class HTTPExtractor:
    """Shared batching, error mapping and usage accounting for both vendors."""

    name = "http"
    version = ADAPTER_VERSION
    test_only = False

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        platform_model_id: str | None = None,
        sale_usd_per_mtok: tuple[float, float] | None = None,
    ):
        if not settings.llm_model:
            raise ValueError("BID_LLM_MODEL is required")
        self.settings = settings
        self.model = settings.llm_model
        self.transport = transport
        self.platform_model_id = platform_model_id
        self.sale = sale_usd_per_mtok

    async def extract(self, chunks: list[dict], schema: dict) -> LLMResult:
        usages: list[ProviderUsage] = []
        items: list[ExtractedRequirement] = []
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(self.settings.llm_timeout_seconds, connect=10),
            follow_redirects=False,
        ) as client:
            for batch in batches(chunks, self.settings.llm_batch_chars):
                try:
                    wire, usage = await self.call(client, batch)
                except ProviderFailure as exc:
                    exc.usage = usages + exc.usage
                    raise
                usages.append(usage)
                items.extend(self.attach(wire, batch, usages))
        return LLMResult(extraction=Extraction(items=items), usage=self.total(usages))

    def attach(
        self, wire: WireOutput, batch: list[dict], usages: list[ProviderUsage]
    ) -> list[ExtractedRequirement]:
        by_page = {chunk["page"]: chunk for chunk in batch}
        output = []
        for item in wire.items:
            chunk = by_page.get(item.page)
            if chunk is None:
                raise ProviderFailure(
                    "Model cited a page that was not in the request",
                    code="invalid_provider_output",
                    usage=list(usages),
                )
            output.append(
                ExtractedRequirement(
                    category=item.category,
                    starred=item.starred,
                    text=item.text,
                    source=Source(
                        document_id=chunk["document_id"],
                        chunk_id=chunk["id"],
                        page=item.page,
                        quote=item.quote,
                    ),
                    condition=item.condition.model_dump() if item.condition else {},
                )
            )
        return output

    def total(self, usages: list[ProviderUsage]) -> ProviderUsage:
        return ProviderUsage(
            provider=self.name,
            model=self.model,
            version=self.version,
            duration_ms=sum(u.duration_ms for u in usages),
            tokens=sum(u.tokens for u in usages),
            input_tokens=sum(u.input_tokens for u in usages),
            output_tokens=sum(u.output_tokens for u in usages),
            usd=None if any(u.usd is None for u in usages) else sum(u.usd or 0 for u in usages),
            platform_model_id=self.platform_model_id,
            charge_usd=None if self.sale is None else sum(u.charge_usd or 0 for u in usages),
        )

    def usage(self, started: float, model: str, input_tokens: int, output_tokens: int):
        prices = (self.settings.llm_input_usd_per_mtok, self.settings.llm_output_usd_per_mtok)
        usd = (
            None
            if prices[0] is None or prices[1] is None
            else (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000
        )
        charge = (
            None
            if self.sale is None
            else (input_tokens * self.sale[0] + output_tokens * self.sale[1]) / 1_000_000
        )
        return ProviderUsage(
            provider=self.name,
            model=model,
            version=self.version,
            duration_ms=int((time.monotonic() - started) * 1000),
            tokens=input_tokens + output_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usd=usd,
            platform_model_id=self.platform_model_id,
            charge_usd=charge,
        )

    async def post(self, client: httpx.AsyncClient, url: str, headers: dict, body: dict) -> dict:
        try:
            response = await client.post(url, headers=headers, json=body)
        except (httpx.TimeoutException, httpx.TransportError):
            raise ProviderFailure(
                "LLM service is unreachable or timed out", retryable=True
            ) from None
        if response.status_code != 200:
            try:
                kind = response.json().get("error", {}).get("type") or "unknown"
            except ValueError:
                kind = "unknown"
            # Only the status and vendor error type are kept; bodies may echo input.
            raise ProviderFailure(
                f"LLM request failed with HTTP {response.status_code} ({kind})",
                retryable=response.status_code in RETRYABLE_STATUS,
            )
        try:
            return response.json()
        except ValueError:
            raise ProviderFailure(
                "LLM service returned a non-JSON response", code="invalid_provider_output"
            ) from None

    @staticmethod
    def parse(text: str, usage: ProviderUsage) -> WireOutput:
        try:
            return WireOutput.model_validate(json.loads(text))
        except (ValueError, ValidationError):
            raise ProviderFailure(
                "Model output did not match the extraction schema",
                code="invalid_provider_output",
                usage=[usage],
            ) from None

    async def call(
        self, client: httpx.AsyncClient, batch: list[dict]
    ) -> tuple[WireOutput, ProviderUsage]:
        raise NotImplementedError


class AnthropicExtractor(HTTPExtractor):
    name = "anthropic"

    async def call(self, client, batch):
        settings = self.settings
        assert settings.llm_api_key is not None
        headers = {
            "x-api-key": settings.llm_api_key.get_secret_value(),
            "anthropic-version": "2023-06-01",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": settings.llm_max_output_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": render_pages(batch)}],
            "output_config": {"format": {"type": "json_schema", "schema": WIRE_SCHEMA}},
        }
        if settings.llm_effort:
            body["output_config"]["effort"] = settings.llm_effort
        if settings.llm_anthropic_fallback:
            # A safety decline is retried server-side on the model Anthropic recommends.
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
            body["fallbacks"] = "default"
        started = time.monotonic()
        base = (settings.llm_base_url or "https://api.anthropic.com").rstrip("/")
        payload = await self.post(client, f"{base}/v1/messages", headers, body)
        tokens = payload.get("usage") or {}
        usage = self.usage(
            started,
            payload.get("model") or self.model,
            int(tokens.get("input_tokens") or 0),
            int(tokens.get("output_tokens") or 0),
        )
        stop = payload.get("stop_reason")
        if stop == "refusal":
            raise ProviderFailure(
                "Model declined the request", refused=True, code="provider_refused", usage=[usage]
            )
        if stop == "max_tokens":
            raise ProviderFailure(
                "Model output was truncated; lower BID_LLM_BATCH_CHARS or raise BID_LLM_MAX_OUTPUT_TOKENS",
                code="invalid_provider_output",
                usage=[usage],
            )
        text = "".join(
            block.get("text", "")
            for block in payload.get("content") or []
            if block.get("type") == "text"
        )
        return self.parse(text, usage), usage


class OpenAICompatibleExtractor(HTTPExtractor):
    name = "openai-compatible"

    async def call(self, client, batch):
        settings = self.settings
        headers = {}
        if settings.llm_api_key is not None:
            headers["Authorization"] = f"Bearer {settings.llm_api_key.get_secret_value()}"
        system = SYSTEM_PROMPT
        if settings.llm_json_mode == "json_schema":
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {"name": "requirements", "schema": WIRE_SCHEMA, "strict": True},
            }
        else:
            # Services without schema support get the schema in the prompt instead.
            response_format = {"type": "json_object"}
            system += "\n\n只输出一个符合以下 JSON Schema 的 JSON 对象：\n" + json.dumps(
                WIRE_SCHEMA, ensure_ascii=False
            )
        body = {
            "model": self.model,
            "max_tokens": settings.llm_max_output_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": render_pages(batch)},
            ],
            "response_format": response_format,
        }
        started = time.monotonic()
        base = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        payload = await self.post(client, f"{base}/chat/completions", headers, body)
        tokens = payload.get("usage") or {}
        usage = self.usage(
            started,
            payload.get("model") or self.model,
            int(tokens.get("prompt_tokens") or 0),
            int(tokens.get("completion_tokens") or 0),
        )
        choices = payload.get("choices") or []
        if not choices:
            raise ProviderFailure(
                "LLM response had no choices", code="invalid_provider_output", usage=[usage]
            )
        choice = choices[0]
        message = choice.get("message") or {}
        if message.get("refusal"):
            raise ProviderFailure(
                "Model declined the request", refused=True, code="provider_refused", usage=[usage]
            )
        if choice.get("finish_reason") == "length":
            raise ProviderFailure(
                "Model output was truncated; lower BID_LLM_BATCH_CHARS or raise BID_LLM_MAX_OUTPUT_TOKENS",
                code="invalid_provider_output",
                usage=[usage],
            )
        return self.parse(message.get("content") or "", usage), usage


def create_llm(settings: Settings):
    if settings.llm_provider == "anthropic":
        return AnthropicExtractor(settings)
    if settings.llm_provider == "openai":
        return OpenAICompatibleExtractor(settings)
    return DisabledLLM()


class UnavailablePlatformModel(DisabledLLM):
    """The catalog default exists but its credential is missing from the deployment."""

    def __init__(self, entry: PlatformModel):
        self.name, self.model = entry.provider, entry.model
        self.version = f"{ADAPTER_VERSION}:{entry.id}:{entry.revision}"

    async def extract(self, chunks: list[dict], schema: dict) -> LLMResult:
        raise ProviderFailure("The platform model's credential is not configured")


def credential_value(name: str) -> str | None:
    return os.environ.get(f"BID_PLATFORM_CREDENTIAL_{name.upper()}") or None


def platform_llm(settings: Settings, entry: PlatformModel, transport=None):
    key = credential_value(entry.credential)
    if key is None and (entry.provider == "anthropic" or not entry.base_url):
        return UnavailablePlatformModel(entry)
    configured = settings.model_copy(
        update={
            "llm_provider": entry.provider,
            "llm_model": entry.model,
            "llm_api_key": SecretStr(key) if key else None,
            "llm_base_url": entry.base_url,
            "llm_input_usd_per_mtok": float(entry.vendor_input_usd_per_mtok),
            "llm_output_usd_per_mtok": float(entry.vendor_output_usd_per_mtok),
        }
    )
    extractor = AnthropicExtractor if entry.provider == "anthropic" else OpenAICompatibleExtractor
    llm = extractor(
        configured,
        transport,
        platform_model_id=entry.id,
        sale_usd_per_mtok=(
            float(entry.sale_input_usd_per_mtok),
            float(entry.sale_output_usd_per_mtok),
        ),
    )
    # Editing the catalog entry changes the job cache key.
    llm.version = f"{ADAPTER_VERSION}:{entry.id}:{entry.revision}"
    return llm


async def resolve_llm(session: AsyncSession, settings: Settings, fallback, transport=None):
    """Use the platform default model when one is set, else the BID_LLM_* fallback."""
    entry = await session.scalar(
        select(PlatformModel).where(
            PlatformModel.capability == "llm_extract",
            PlatformModel.is_default.is_(True),
            PlatformModel.enabled.is_(True),
        )
    )
    return fallback if entry is None else platform_llm(settings, entry, transport)
