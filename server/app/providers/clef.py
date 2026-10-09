"""Authenticated Gateway presence triage with fixed-price accounting per dispatch."""

import asyncio
import base64
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from weakref import WeakKeyDictionary

import httpx
from pydantic import ValidationError

from app.core.errors import ServiceError
from app.providers.base import ProviderFailure
from app.providers.calls import current_accounting
from app.schemas.budget_contracts import BudgetCallQuote
from app.schemas.clef import (
    CLEF_CONTEXT_TOKENS,
    CLEF_REQUEST_BYTE_LIMIT,
    ClefChoiceAnswer,
    ClefFixedCallQuote,
    ClefNoulAnswer,
    ClefTriageResult,
)
from app.schemas.contracts import ProviderUsage

MODEL = "@cf/cloudflare/clef"
VERSION = "clef-presence-v1"
_semaphores = WeakKeyDictionary()


def concurrency_gate():
    loop = asyncio.get_running_loop()
    if loop not in _semaphores:
        _semaphores[loop] = asyncio.Semaphore(3)
    return _semaphores[loop]


def jpeg_dimensions(image):
    """Read bounded JPEG frame headers before allocating decoded pixel memory."""
    offset = 2
    while offset + 4 <= len(image):
        if image[offset] != 255:
            break
        while offset < len(image) and image[offset] == 255:
            offset += 1
        if offset >= len(image):
            break
        marker = image[offset]
        offset += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        length = int.from_bytes(image[offset : offset + 2], "big")
        if length < 2 or offset + length > len(image):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if length < 8:
                break
            return int.from_bytes(image[offset + 5 : offset + 7], "big"), int.from_bytes(
                image[offset + 3 : offset + 5], "big"
            )
        offset += length
    raise ProviderFailure("Clef JPEG frame is invalid", code="clef_image_integrity")


def wire_body(request, images):
    if len(images) != len(request.images) or not images:
        raise ProviderFailure("Clef images do not match receipts", code="clef_image_integrity")
    for descriptor, image in zip(request.images, images, strict=True):
        if (
            descriptor.size_bytes != len(image)
            or descriptor.sha256 != hashlib.sha256(image).hexdigest()
            or descriptor.media_type != "image/jpeg"
            or not image.startswith(b"\xff\xd8")
            or descriptor.price_page
            or jpeg_dimensions(image) != (descriptor.width_px, descriptor.height_px)
        ):
            raise ProviderFailure("Clef image receipt changed", code="clef_image_integrity")
    questions = {}
    for question in request.questions:
        if question.type not in {"noul", "choice"}:
            raise ProviderFailure(
                "Clef question is not supported", code="clef_question_unsupported"
            )
        value = {"type": question.type, "instructions": question.question}
        if question.type == "choice":
            value["choices"] = question.choices
        questions[question.ref] = value
    body = {
        "state": request.state
        if isinstance(request.state, str)
        else json.dumps(request.state, ensure_ascii=False, separators=(",", ":")),
        "questions": questions,
        "images": [
            "data:image/jpeg;base64," + base64.b64encode(image).decode() for image in images
        ],
    }
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    text_bound = len(body["state"].encode()) + len(json.dumps(questions).encode())
    # UTF-8 bytes conservatively bound text tokens; reserve 8k per image plus
    # bounded output. Base64 byte size is independently checked below.
    if (
        len(encoded) > CLEF_REQUEST_BYTE_LIMIT
        or text_bound + len(images) * 8192 + 2048 > CLEF_CONTEXT_TOKENS
    ):
        raise ProviderFailure("Clef request exceeds limits", code="clef_request_limit")
    return body, encoded


def retry_after(value):
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return seconds if 0 <= seconds <= 60 else None


def telemetry_id(value):
    return value if value and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) else None


class HTTPClefProvider:
    def __init__(self, config, settings, *, transport=None, remaining_calls=40):
        self.config, self.settings, self.transport = config, settings, transport
        self.remaining_calls = remaining_calls
        self.attempts = 0
        self.before_send: Callable[[], Awaitable[None]] | None = None
        self._attempt_lock = asyncio.Lock()

    def quote(self, request, redacted_images):
        _, encoded = wire_body(request, redacted_images)
        return ClefFixedCallQuote(
            platform_config_revision=self.config.revision,
            quote=BudgetCallQuote(
                capability="vision",
                payer="org_platform",
                provider="cloudflare",
                model=MODEL,
                version=VERSION,
                platform_model_id="bid-review-clef",
                price_revision=str(self.config.price_revision),
                request_sha256=hashlib.sha256(encoded).hexdigest(),
                currency=self.config.currency,
                reserved_charge=self.config.fixed_sale_price,
                reserved_task_amount=self.config.fixed_sale_price,
                vendor_usd_upper_bound=None,
                image_count=len(redacted_images),
                image_price_revision=str(self.config.price_revision),
            ),
        )

    async def _attempt(self, request, redacted_images, body, fixed, accounting):
        from app.services import platform_clef

        call_id = await accounting.admit(fixed.quote)
        sent, settled = False, False
        started = time.monotonic()
        try:
            try:
                workers, gateway = await platform_clef.resolve_credentials(
                    self.settings, self.config
                )
            except (ServiceError, ProviderFailure):
                raise ProviderFailure(
                    "Clef configuration or authority changed", code="clef_credentials_unavailable"
                ) from None
            if self.before_send is not None:
                await self.before_send()
            url = f"https://gateway.ai.cloudflare.com/v1/{self.config.account_id}/{self.config.gateway_id}/workers-ai/{MODEL}"
            raw, invalid_envelope = bytearray(), False
            sent = True
            async with httpx.AsyncClient(
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(self.settings.llm_timeout_seconds, connect=10),
            ) as client:
                async with client.stream(
                    "POST",
                    url,
                    json=body,
                    headers={
                        "Authorization": "Bearer " + workers.api_key.get_secret_value(),
                        "cf-aig-authorization": "Bearer " + gateway.api_key.get_secret_value(),
                        "cf-aig-collect-log": "false",
                        "Accept-Encoding": "identity",
                    },
                ) as response:
                    if response.status_code == 429:
                        await accounting.not_sent(call_id)
                        settled = True
                        delay = retry_after(response.headers.get("Retry-After"))
                        if delay is None:
                            raise ProviderFailure(
                                "Clef rate limit requires retry",
                                code="clef_rate_limited",
                                retryable=True,
                            )
                        return delay
                    if response.status_code != 200:
                        raise ProviderFailure("Clef dispatch failed", code="clef_dispatch_failed")
                    if response.headers.get("content-encoding", "identity") != "identity":
                        invalid_envelope = True
                    else:
                        async for chunk in response.aiter_bytes():
                            if len(raw) + len(chunk) > 256 * 1024:
                                invalid_envelope = True
                                break
                            raw.extend(chunk)
                    response_headers = response.headers
            try:
                payload = json.loads(raw) if not invalid_envelope else {}
            except (ValueError, UnicodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            result = payload.get("result")
            telemetry = result.get("usage") if isinstance(result, dict) else None
            tokens = telemetry.get("input_tokens") if isinstance(telemetry, dict) else None
            valid_tokens = type(tokens) is int and 0 <= tokens <= 2**31 - 1
            reported_tokens = tokens if isinstance(tokens, int) and valid_tokens else 0
            usage = ProviderUsage(
                gateway_request_id=telemetry_id(response_headers.get("cf-aig-event-id")),
                gateway_trace_id=telemetry_id(response_headers.get("cf-ray")),
                provider="cloudflare",
                model=MODEL,
                version=VERSION,
                tokens=reported_tokens,
                input_tokens=reported_tokens,
                duration_ms=int((time.monotonic() - started) * 1000),
                usd=None,
                platform_model_id="bid-review-clef",
                charge=self.config.fixed_sale_price,
                capability="vision",
                payer="org_platform",
                billing_currency=self.config.currency,
                task_amount=self.config.fixed_sale_price,
                price_revision=str(self.config.price_revision),
                image_count=len(redacted_images),
                image_price_revision=str(self.config.price_revision),
                image_input_sha256=request.images[0].sha256,
            )
            await accounting.complete(call_id, usage)
            settled = True
            try:
                if (
                    payload.get("success") is not True
                    or not isinstance(result, dict)
                    or not valid_tokens
                ):
                    raise ValueError("invalid completion")
                answers = result["answers"]
                if not isinstance(answers, dict) or set(answers) != {
                    question.ref for question in request.questions
                }:
                    raise ValueError("answer refs changed")
                normalized = []
                for question in request.questions:
                    answer = answers[question.ref]
                    if answer["type"] != question.type:
                        raise ValueError("answer type changed")
                    if question.type == "noul":
                        normalized.append(
                            ClefNoulAnswer(ref=question.ref, probability_yes=answer["noul"])
                        )
                    else:
                        if (
                            set(answer["probabilities"]) != set(question.choices)
                            or answer["choice"] not in question.choices
                        ):
                            raise ValueError("choice changed")
                        normalized.append(
                            ClefChoiceAnswer(
                                ref=question.ref,
                                probabilities=[
                                    answer["probabilities"][label] for label in question.choices
                                ],
                            )
                        )
                return ClefTriageResult(
                    request_sha256=fixed.quote.request_sha256,
                    answers=normalized,
                    usage=usage,
                    fixed_quote=fixed,
                )
            except (KeyError, TypeError, ValueError, ValidationError):
                raise ProviderFailure(
                    "Clef answers are invalid", code="invalid_provider_output", usage=[usage]
                ) from None
        except (httpx.TimeoutException, httpx.TransportError):
            raise ProviderFailure("Clef outcome is unknown", code="provider_timeout") from None
        finally:
            if not settled:
                if sent:
                    await accounting.unknown(call_id)
                else:
                    await accounting.not_sent(call_id)

    async def triage(self, request, redacted_images):
        accounting = current_accounting.get()
        if accounting is None:
            raise ProviderFailure("Clef requires accounting", code="provider_accounting_required")
        body, _ = wire_body(request, redacted_images)
        async with concurrency_gate():
            while True:
                async with self._attempt_lock:
                    if self.attempts >= min(40, self.remaining_calls):
                        raise ProviderFailure(
                            "Clef visual call ceiling reached", code="clef_call_limit"
                        )
                    self.attempts += 1
                fixed = self.quote(request, redacted_images)
                pending = asyncio.create_task(
                    self._attempt(request, redacted_images, body, fixed, accounting)
                )
                try:
                    output = await asyncio.shield(pending)
                except asyncio.CancelledError as cancelled:
                    # Drain the already-admitted operation and its settlement;
                    # cancellation can never start another retry or erase liability.
                    while not pending.done():
                        try:
                            await asyncio.shield(pending)
                        except asyncio.CancelledError:
                            continue
                        except BaseException:
                            break
                    if not pending.cancelled():
                        try:
                            pending.result()
                        except BaseException:
                            pass
                    raise cancelled
                if isinstance(output, ClefTriageResult):
                    return output
                await asyncio.sleep(output)
