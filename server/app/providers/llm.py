"""Requirement extraction through vendor HTTP APIs, called with httpx only."""

import asyncio
import json
import logging
import os
import re
import ssl
import time
from decimal import ROUND_CEILING, Decimal
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError, log_unexpected
from app.models.entities import PlatformModel
from app.providers.base import MalformedOutput, ProviderFailure, TruncatedOutput
from app.providers.calls import accounted_call, plan_calls
from app.providers.disabled import DisabledLLM
from app.schemas.contracts import (
    Category,
    ExtractedRequirement,
    Extraction,
    GapFill,
    LLMResult,
    ProviderUsage,
    Source,
)
from app.services.extraction import cited, fingerprint, locate_span, location_of, normalize

ADAPTER_VERSION = "http-extract-v4"
logger = logging.getLogger(__name__)
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
# Exhausted quota, unpaid accounts and expired plans do not recover within the retry
# window. Zhipu reports them as HTTP 429 with these codes; 1302 and 1305 are plain
# rate limits and stay retryable.
QUOTA_CODES = {"1113", "1308", "1309", "1310", "1311", "1313", "1314", "1315"} | {
    str(code) for code in range(1316, 1322)
}
QUOTA_TYPES = {"insufficient_quota", "billing_error"}
RESET_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?")

SYSTEM_PROMPT = """你是招标文件分析助手。从用户给出的招标文件内容中，抽取投标人必须响应的全部要求：
- qualification：资格要求（资质、业绩、人员、财务、信誉等）
- technical：技术参数与功能要求
- scoring：评分项与评分标准
- substantive：实质性条款，包括带 ★ 的条款、否决投标或废标条款

每条要求：
- text：用简洁中文复述这条要求，保留全部参数名、数值、比较符、单位、范围及限定条件，不得用“等”、省略号或概括性文字省略原文细节。
- ref：要求所在位置。内容以 <page number="N"> 给出时填页码 N；以 <block id="…"> 给出时填块标识，如 p37 或 t5r3c2。
- quote：逐字复制该位置中的原文连续片段，必须完整出现在同一个 page 或 block 内，不得改写、省略、补全或跨块合并。
- starred：原文带 ★，或属于实质性、否决投标、废标条款时为 true。
- condition：可量化的技术参数填写 param（参数名）、op（比较方式）、value（数值或文本）、unit（单位，无则为 null）；无法量化时为 null。

同一段落、表格单元格或页面内用“；”、";"、换行分隔的硬件或软件参数清单，必须逐个参数输出独立要求，不得把多个参数合并成一条或只选部分参数。
每个参数的 quote 必须引用该参数自己的原文，不能用整个清单的引用代替逐项引用；非数值参数（如面板类型：IPS 技术）也必须逐项抽取。
只抽取内容中实际写明的要求，不推测、不编造；没有要求的位置不输出。只有标题而没有要求内容的条目（如“★3.合同的终止：”），即使带 ★ 也不输出。"""

PARAMETER_COMPARISON = re.compile(r"[≥≤><≯≮]|不少于|不低于|不超过|不高于|大于|小于|至少|至多")
PARAMETER_UNIT = re.compile(
    r"(?<![a-z])(?:[kmgt]?b|[kmg]?hz|[mun]?m|kg|g|v|w|a|db|dpi|ppm|fps|bit)(?![a-z])"
    r"|%|°c|英寸|毫米|厘米|米|千克|公斤|克|毫秒|秒|分钟|小时|瓦|伏|安培|像素|核|线程|页/分",
    re.IGNORECASE,
)
# Applied to original text, so full-width forms such as （1） and 1． count as numbering too.
PARAMETER_PREFIX = re.compile(r"^[★☆\s]*(?:[(（]\d+[)）]|\d+(?:[、)）]|[.．](?!\d)))?[★☆\s]*")


def parameter_segments(text: str) -> list[str]:
    segments = []
    for part in re.split(r"[；;\r\n]+", text):
        part = part.strip()
        normalized = normalize(part)
        name, colon, value = normalized.partition(":")
        if PARAMETER_COMPARISON.search(normalized) or (
            name and colon and value and (re.search(r"\d", value) or PARAMETER_UNIT.search(value))
        ):
            segments.append(part)
    return segments


def uncovered_parameters(
    chunks: list[dict], items: list[ExtractedRequirement]
) -> tuple[list[dict], int]:
    """Render only gaps, retaining original positions and leaving stored chunks untouched."""
    available = {str(chunk["id"]): chunk for chunk in chunks}
    quotes: dict[tuple[str, str | int | None], list[str]] = {}
    for item in items:
        if not cited(item, available.get(str(item.source.chunk_id))):
            continue  # Invalid citations cannot hide gaps; the processor reports them later.
        source = item.source
        position = source.location.block_id if source.location else source.page
        quotes.setdefault((str(source.chunk_id), position), []).append(source.quote)

    partials, count = [], 0
    for chunk in chunks:
        blocks = []
        page_text = ""
        for unit in chunk.get("blocks") or [chunk]:
            position = unit["block_id"] if chunk.get("blocks") else chunk["page"]
            covered = quotes.get((str(chunk["id"]), position), [])
            text = unit["text"]
            segments = parameter_segments(text)
            # Work in original offsets, locating quotes as extraction saved them, so a
            # parameter is not covered by another whose name contains its own (5mm, 3.5mm).
            intervals = []
            cursor = 0
            for segment in segments:
                start = text.index(segment, cursor)
                cursor = start + len(segment)
                key = PARAMETER_PREFIX.sub("", segment).rstrip("。. ")
                key_start = start + segment.index(key)
                intervals.append((key_start, key_start + len(key)))
            # A list-sized quote is not evidence of per-parameter extraction. Resend
            # every segment it spans, even when its summary mentions one parameter.
            covered_indexes = set()
            for quote in covered:
                span, _ = locate_span(text, quote)
                if span is None:
                    continue
                quote_start, quote_end = span
                matches = [
                    index
                    for index, (start, end) in enumerate(intervals)
                    if quote_start <= start and end <= quote_end
                ]
                if len(matches) == 1:
                    covered_indexes.add(matches[0])
            missing = []
            for index, segment in enumerate(segments):
                if index not in covered_indexes:
                    missing.append(segment)
            if not missing:
                continue
            count += len(missing)
            rendered = "\n".join(missing)
            if chunk.get("blocks"):
                blocks.append({**unit, "text": rendered})
            else:
                page_text = rendered
        if blocks:
            partials.append(
                {**chunk, "blocks": blocks, "text": "\n".join(b["text"] for b in blocks)}
            )
        elif page_text:
            partials.append({**chunk, "text": page_text})
    return partials, count


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
                    "ref": {"type": "string"},
                    "quote": {"type": "string"},
                    "condition": CONDITION_SCHEMA,
                },
                "required": ["category", "starred", "text", "ref", "quote", "condition"],
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
    ref: str | int
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


def split_lines(text: str) -> tuple[str, str] | None:
    # Cut between lines near the middle of the text; a single line stays whole.
    lines = text.split("\n")
    if len(lines) < 2:
        return None
    cut, running = 1, len(lines[0]) + 1
    while cut < len(lines) - 1 and running * 2 < len(text):
        running += len(lines[cut]) + 1
        cut += 1
    return "\n".join(lines[:cut]), "\n".join(lines[cut:])


def halve(batch: list[dict]) -> list[list[dict]] | None:
    """Split a batch whose output was truncated: by chunk, then block, then line.

    Every part keeps its chunk ID and block ID, and citations are checked against the
    stored full text, so a quote must still sit inside the whole page or block.
    """
    if len(batch) > 1:
        middle = len(batch) // 2
        return [batch[:middle], batch[middle:]]
    [chunk] = batch
    blocks = chunk.get("blocks") or []
    if len(blocks) > 1:
        middle = len(blocks) // 2
        return [
            [{**chunk, "blocks": part, "text": "\n".join(block["text"] for block in part)}]
            for part in (blocks[:middle], blocks[middle:])
        ]
    if blocks:
        # One long block, such as a table cell listing dozens of requirements.
        halves = split_lines(blocks[0]["text"])
        if halves is None:
            return None
        return [
            [{**chunk, "blocks": [{**blocks[0], "text": part}], "text": part}] for part in halves
        ]
    halves = split_lines(chunk["text"])
    if halves is None:
        return None  # a single line cannot be split further
    return [[{**chunk, "text": part}] for part in halves]


def render_pages(batch: list[dict]) -> str:
    parts = []
    for chunk in batch:
        if not chunk.get("blocks"):
            parts.append(f'<page number="{chunk["page"]}">\n{chunk["text"]}\n</page>')
            continue
        # Word: blocks grouped under their heading path; the model cites block ids.
        current = None
        for block in chunk["blocks"]:
            path = " > ".join(block["section_path"]).replace('"', "＂")
            if path != current:
                if current is not None:
                    parts.append("</section>")
                parts.append(f'<section path="{path}">')
                current = path
            parts.append(f'<block id="{block["block_id"]}">\n{block["text"]}\n</block>')
        parts.append("</section>")
    body = "\n".join(parts)
    return f"以下是招标文件的部分内容：\n\n{body}\n\n按要求抽取这些内容中的全部要求。"


class HTTPExtractor:
    """Shared batching, error mapping and usage accounting for both vendors."""

    name = "http"
    version = ADAPTER_VERSION
    test_only = False
    records_calls = True
    # Official reasoning levels of a catalog model, by name, and the level this copy runs at.
    reasoning_levels: dict[str, dict] = {}
    default_reasoning: str | None = None
    reasoning: str | None = None
    # Seconds to wait before retrying a batch after a transient failure such as a
    # dropped connection; a long job should not restart because one call broke.
    retry_delays: tuple[float, ...] = (10, 30)

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

    def at_reasoning(self, name: str) -> "HTTPExtractor":
        """A copy that sends this level's vendor options and uses its batch size."""
        level = self.reasoning_levels[name]
        settings = self.settings.model_copy(
            update={
                "llm_request_options": json.dumps(level.get("request_options") or {}),
                "llm_batch_chars": level.get("batch_chars") or self.settings.llm_batch_chars,
                "llm_effort": level.get("effort") or self.settings.llm_effort,
            }
        )
        copy = type(self)(
            settings,
            self.transport,
            platform_model_id=self.platform_model_id,
            sale_usd_per_mtok=self.sale,
        )
        copy.version = self.version
        copy.reasoning_levels, copy.default_reasoning = (
            self.reasoning_levels,
            self.default_reasoning,
        )
        copy.reasoning = name
        return copy

    async def extract(self, chunks: list[dict], schema: dict) -> LLMResult:
        limit = asyncio.Semaphore(max(1, self.settings.llm_concurrency))
        failed = asyncio.Event()
        # Every finished call was billed by the vendor, including truncated ones.
        usages: list[ProviderUsage] = []
        gap_fill = GapFill()
        filling = False

        async def gather(client, groups) -> list[tuple[list[dict], WireOutput]]:
            results = await asyncio.gather(
                *(run(client, batch) for batch in groups), return_exceptions=True
            )
            answered: list[tuple[list[dict], WireOutput]] = []
            for result in results:
                if isinstance(result, BaseException):
                    raise result
                answered.extend(result)
            return answered

        async def call(client, batch: list[dict]) -> tuple[WireOutput, ProviderUsage]:
            # Transient failures (dropped connections, timeouts, rate limits) are retried
            # here so one broken call does not requeue a long job from the start.
            for delay in (*self.retry_delays, None):
                try:
                    if filling:
                        gap_fill.calls += 1
                    return await self.call(client, batch)
                except ProviderFailure as exc:
                    if not exc.retryable or delay is None or failed.is_set():
                        raise
                    usages.extend(exc.usage)
                    exc.usage = []
                await asyncio.sleep(delay)
            raise AssertionError("unreachable")

        async def run(client, batch: list[dict]) -> list[tuple[list[dict], WireOutput]]:
            async with limit:
                # Batches not yet started are skipped once another batch has failed.
                if failed.is_set():
                    return []
                try:
                    wire, usage = await call(client, batch)
                except ProviderFailure as exc:
                    usages.extend(exc.usage)
                    exc.usage = []
                    # Truncated or malformed output is retried in smaller parts.
                    splittable = isinstance(exc, TruncatedOutput | MalformedOutput)
                    parts = halve(batch) if splittable else None
                    if parts is None:
                        failed.set()
                        raise
                else:
                    usages.append(usage)
                    return [(batch, wire)]
            # Retry the halves outside the semaphore so they can take free slots.
            return await gather(client, parts)

        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(self.settings.llm_timeout_seconds, connect=10),
            follow_redirects=False,
        ) as client:
            try:
                groups = batches(chunks, self.settings.llm_batch_chars)
                plan_calls(len(groups))
                answered = await gather(client, groups)
                items: list[ExtractedRequirement] = []
                rejected: list[dict[str, str]] = []
                for batch, wire in answered:
                    kept, dropped = self.attach(wire, batch)
                    items.extend(kept)
                    rejected.extend(dropped)
                partials, gap_fill.segments = uncovered_parameters(chunks, items)
                if partials:
                    available = {str(chunk["id"]): chunk for chunk in chunks}
                    first_pass = {
                        fingerprint(item)
                        for item in items
                        if cited(item, available.get(str(item.source.chunk_id)))
                    }
                    filling = True
                    # A single sweep: retries split these partials, never rescan their output.
                    answered = await gather(
                        client, batches(partials, self.settings.llm_batch_chars)
                    )
                    for batch, wire in answered:
                        kept, dropped = self.attach(wire, batch)
                        items.extend(kept)
                        rejected.extend(dropped)
                        gap_fill.fingerprints.update(fingerprint(item) for item in kept)
                    gap_fill.fingerprints.difference_update(first_pass)
                return LLMResult(
                    extraction=Extraction(items=items),
                    usage=self.total(usages),
                    usages=usages,
                    gap_fill=gap_fill,
                    rejected=rejected,
                )
            except ProviderFailure as exc:
                exc.usage = usages
                raise
            except Exception as exc:
                # The vendor billed every finished call, so a bug here must not lose them.
                log_unexpected(logger, "Extraction", exc)
                raise ProviderFailure(
                    "Extraction failed unexpectedly; the calls made so far were recorded",
                    code="processing_failed",
                    usage=usages,
                ) from exc

    def attach(
        self, wire: WireOutput, batch: list[dict]
    ) -> tuple[list[ExtractedRequirement], list[dict[str, str]]]:
        by_page = {str(chunk["page"]): chunk for chunk in batch if chunk.get("page")}
        by_block = {
            block["block_id"]: (chunk, block)
            for chunk in batch
            for block in chunk.get("blocks") or []
        }
        output, rejected = [], []
        for item in wire.items:
            ref = str(item.ref).strip()
            if ref in by_block:
                chunk, block = by_block[ref]
                # Location fields come from the parsed block, never from the model.
                page, location = None, location_of(block)
            elif ref in by_page:
                chunk = by_page[ref]
                page, location = chunk["page"], None
            else:
                rejected.append(
                    {
                        "position": str(item.ref),
                        "quote": item.quote[:200],
                        "reason": "unknown_position",
                    }
                )
                continue
            if not item.text.strip() or not item.quote.strip():
                # Nothing to save or verify; reported like an uncited item.
                rejected.append(
                    {
                        "position": location.label if location else f"第 {page} 页",
                        "quote": item.quote[:200],
                        "reason": "empty_quote" if not item.quote.strip() else "empty_text",
                    }
                )
                continue
            output.append(
                ExtractedRequirement(
                    category=item.category,
                    starred=item.starred,
                    text=item.text,
                    model_quote=item.quote,
                    source=Source(
                        document_id=chunk["document_id"],
                        chunk_id=chunk["id"],
                        quote=item.quote,
                        page=page,
                        location=location,
                    ),
                    condition=item.condition.model_dump() if item.condition else {},
                )
            )
        return output, rejected

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
            charge=None if self.sale is None else sum(u.charge or 0 for u in usages),
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
            charge=charge,
        )

    def reservation(self, body: dict) -> Decimal:
        if self.platform_model_id is None:
            return Decimal(0)
        if self.sale is None or any(not Decimal(str(p)).is_finite() or p < 0 for p in self.sale):
            raise ProviderFailure(
                "Platform model prices are unavailable", code="billing_price_unavailable"
            )
        # Byte-level tokenizers cannot emit more content tokens than UTF-8 bytes.
        # Count the entire request (including schema) plus conservative framing space.
        input_bound = len(json.dumps(body, ensure_ascii=False).encode("utf-8")) + 4096
        output_bound = body.get("max_completion_tokens", body["max_tokens"])
        copies = body.get("n", 1)
        if (
            type(output_bound) is not int
            or output_bound < 1
            or type(copies) is not int
            or copies < 1
        ):
            raise ProviderFailure("Model output limit is invalid", code="billing_bound_unavailable")
        return (
            (
                input_bound * Decimal(str(self.sale[0]))
                + output_bound * copies * Decimal(str(self.sale[1]))
            )
            / 1_000_000
        ).quantize(Decimal("0.00000001"), rounding=ROUND_CEILING)

    def response_usage(self, payload: dict, started: float) -> ProviderUsage:
        tokens = payload.get("usage")
        if not isinstance(tokens, dict):
            raise ProviderFailure("Vendor response has no usage", code="invalid_provider_usage")

        def count(name: str, *, optional: bool = False) -> int:
            value = tokens.get(name, 0) if optional else tokens.get(name)
            if type(value) is not int or value < 0:
                raise ProviderFailure(
                    "Vendor token usage is invalid", code="invalid_provider_usage"
                )
            return value

        if self.name == "anthropic":
            inputs = (
                count("input_tokens")
                + count("cache_creation_input_tokens", optional=True)
                + count("cache_read_input_tokens", optional=True)
            )
            outputs = count("output_tokens")
        else:
            inputs, outputs = count("prompt_tokens"), count("completion_tokens")
        return self.usage(started, payload.get("model") or self.model, inputs, outputs)

    async def post(
        self, client: httpx.AsyncClient, url: str, headers: dict, body: dict
    ) -> tuple[dict, ProviderUsage]:
        async def request():
            started = time.monotonic()
            response = await self.send(client, url, headers, body)
            try:
                payload = response.json()
            except ValueError:
                if response.status_code != 200:
                    self.check_status(response)
                raise ProviderFailure(
                    "LLM service returned a non-JSON response", code="invalid_provider_output"
                ) from None
            if not isinstance(payload, dict):
                raise ProviderFailure(
                    "LLM response is not an object", code="invalid_provider_output"
                )
            if response.status_code != 200 and "usage" not in payload:
                self.check_status(response)
            return (response, payload), self.response_usage(payload, started)

        (response, payload), usage = await accounted_call(
            self.reservation(body), self.platform_model_id is not None, request
        )
        # Account error envelopes that include usage before applying their error/retry policy.
        self.check_status(response, [usage])
        return payload, usage

    async def send(
        self, client: httpx.AsyncClient, url: str, headers: dict, body: dict
    ) -> httpx.Response:
        try:
            # httpx timeouts restart on every received byte; vendors under load keep the
            # connection alive with blank lines, so a total deadline is enforced here.
            async with asyncio.timeout(self.settings.llm_timeout_seconds):
                response = await client.post(url, headers=headers, json=body)
        except (TimeoutError, httpx.TimeoutException, httpx.TransportError, ssl.SSLError):
            # httpx leaves a TLS connection dropped mid-response as a raw ssl.SSLError.
            raise ProviderFailure(
                "LLM service is unreachable or timed out", retryable=True
            ) from None
        return response

    @staticmethod
    def check_status(response: httpx.Response, usages: list[ProviderUsage] | None = None) -> None:
        if response.status_code != 200:
            try:
                error = response.json().get("error") or {}
                if not isinstance(error, dict):
                    error = {}
            except ValueError:
                error = {}
            # OpenAI and Anthropic send a type; Zhipu and others send a vendor code.
            kind = str(error.get("type") or error.get("code") or "unknown")[:40]
            codes = {str(error.get("type")), str(error.get("code"))}
            if response.status_code == 402 or codes & (QUOTA_CODES | QUOTA_TYPES):
                # Only a reset timestamp is taken from the vendor message.
                reset = RESET_TIME.search(str(error.get("message") or ""))
                raise ProviderFailure(
                    f"Model quota is used up or the plan is unavailable ({kind})"
                    + (f"; it resets at {reset.group()}" if reset else "")
                    + ". Contact your system administrator.",
                    code="provider_quota_exhausted",
                    usage=usages,
                )
            # Only the status and vendor error type are kept; bodies may echo input.
            raise ProviderFailure(
                f"LLM request failed with HTTP {response.status_code} ({kind})",
                retryable=response.status_code in RETRYABLE_STATUS,
                usage=usages,
            )

    @staticmethod
    def parse(text: str, usage: ProviderUsage) -> WireOutput:
        try:
            return WireOutput.model_validate(json.loads(text))
        except (ValueError, ValidationError):
            raise MalformedOutput([usage]) from None

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
        base = (settings.llm_base_url or "https://api.anthropic.com").rstrip("/")
        body = {**self.settings.request_options(), **body}
        payload, usage = await self.post(client, f"{base}/v1/messages", headers, body)
        stop = payload.get("stop_reason")
        if stop == "refusal":
            raise ProviderFailure(
                "Model declined the request", refused=True, code="provider_refused", usage=[usage]
            )
        if stop == "max_tokens":
            raise TruncatedOutput([usage])
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
        base = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        body = {**self.settings.request_options(), **body}
        payload, usage = await self.post(client, f"{base}/chat/completions", headers, body)
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
            raise TruncatedOutput([usage])
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
            float(entry.sale_input_per_mtok),
            float(entry.sale_output_per_mtok),
        ),
    )
    # Editing the catalog entry changes the job cache key.
    llm.version = f"{ADAPTER_VERSION}:{entry.id}:{entry.revision}"
    llm.reasoning_levels = {level["name"]: level for level in entry.reasoning or []}
    llm.default_reasoning = entry.default_reasoning
    return llm


def with_reasoning(llm, requested: str | None):
    """Return (provider at the level, level name or None, warnings) for an extraction."""
    levels = getattr(llm, "reasoning_levels", None) or {}
    if not levels:
        warnings = (
            ["The current model has no reasoning levels configured; the level was ignored."]
            if requested
            else []
        )
        return llm, None, warnings
    name = requested or llm.default_reasoning
    if name not in levels:
        raise ServiceError(
            "unsupported_reasoning",
            f"Reasoning level {name} is not available for this model; "
            f"choose one of: {', '.join(levels)}",
            400,
            2,
        )
    return llm.at_reasoning(name), name, []


def reasoning_choices(llm) -> list[dict]:
    """The model's levels as shown to users: name, label, and whether it is the default."""
    levels = getattr(llm, "reasoning_levels", None) or {}
    return [
        {"name": name, "label": level.get("label"), "default": name == llm.default_reasoning}
        for name, level in levels.items()
    ]


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
