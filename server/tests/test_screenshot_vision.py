"""Screenshot vision's real HTTP boundary, accounting order and safe proposal filtering.

Failure modes covered here:
- unverified or incomplete catalog image pricing cannot dispatch;
- configured image count and pixel limits are enforced before admission;
- image token reservations use the verified pixel rule rather than base64 length;
- server-owned catalog options never reach the vendor;
- missing usage keeps the admission unknown, while malformed paid output is settled first;
- unknown refs, unusable regions and invalid purpose fields are rejected without echoing them.
"""

import base64
import binascii
import json
import struct
import zlib
from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from app.core.config import Settings
from app.providers.base import ProviderFailure
from app.providers.calls import current_accounting
from app.providers.llm import OpenAICompatibleExtractor
from app.providers.screenshot_vision import VisionImage, analyze, preview
from cryptography.fernet import Fernet

MODEL = "synthetic-vision-model"
SYNTHETIC_KEY = "synthetic-vision-key-not-real"


def _chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def png(width: int, height: int, *, noisy: bool = False) -> bytes:
    """Create a valid RGB PNG with deterministic but differently compressible pixels."""
    rows = []
    for y in range(height):
        if noisy:
            row = bytes(
                (x * 17 + y * 31 + channel * 67) % 256 for x in range(width) for channel in range(3)
            )
        else:
            row = b"\x7f\x7f\x7f" * width
        rows.append(b"\x00" + row)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + _chunk(b"IEND", b"")
    )


CAPABILITY = {
    "verified": True,
    "max_images": 2,
    "max_pixels": 2_000_000,
    "image_input_token_bound": {
        "detail": "high",
        "base_tokens": 64,
        "tokens_per_image": 256,
        "tokens_per_megapixel": 1024,
    },
    "price_revision": "glm-vision-2026-10-01",
}


def image(ref: str = "img-1", *, noisy: bool = False, width: int = 80, height: int = 80):
    return VisionImage(
        ref=ref,
        content=png(width, height, noisy=noisy),
        width=width,
        height=height,
        mapping={
            "content_offset_x": 10,
            "content_offset_y": 10,
            "content_width": width - 20,
            "content_height": height - 30,
            "footer_height": 10,
        },
    )


def response(items, *, model=MODEL, usage=True, finish_reason="stop"):
    body = {
        "model": model,
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": json.dumps({"items": items}, ensure_ascii=False)},
            }
        ],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 800, "completion_tokens": 120}
    return httpx.Response(200, json=body)


class Vendor:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []
        self.events: list[str] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.events is not None:
            self.events.append("http")
        reply = self.replies.pop(0)
        return reply(request) if callable(reply) else reply

    def transport(self):
        return httpx.MockTransport(self)


class Accounting:
    def __init__(self):
        self.events: list[str] = []
        self.planned: list[int] = []
        self.reservations: list[Decimal] = []
        self.completed = []
        self.unknown_calls = []

    def plan(self, first_pass_calls: int) -> None:
        self.planned.append(first_pass_calls)
        self.events.append("plan")

    async def admit(self, quote):
        reserved_charge, platform_billed = quote.reserved_charge, quote.payer == "org_platform"
        assert platform_billed is True
        self.events.append("admit")
        self.reservations.append(reserved_charge)
        return uuid4()

    async def complete(self, call_id, usage) -> None:
        self.events.append("complete")
        self.completed.append((call_id, usage))

    async def unknown(self, call_id) -> None:
        self.events.append("unknown")
        self.unknown_calls.append(call_id)


def provider(tmp_path, vendor: Vendor, capability=CAPABILITY, **settings_overrides):
    options = {
        "reasoning_effort": "high",
        "screenshot_vision": capability,
    }
    settings = Settings(
        database_url="postgresql+psycopg://synthetic:synthetic@localhost/synthetic",
        encryption_key=Fernet.generate_key().decode(),
        token_key=Fernet.generate_key().decode(),
        data_dir=tmp_path,
        llm_provider="openai",
        llm_model=MODEL,
        llm_api_key=SYNTHETIC_KEY,
        llm_base_url="https://vision.example.test/v1",
        llm_max_output_tokens=2048,
        llm_request_options=json.dumps(options),
        **settings_overrides,
    )
    return OpenAICompatibleExtractor(
        settings,
        vendor.transport(),
        platform_model_id="catalog-vision",
        sale_usd_per_mtok=(2.0, 8.0),
    )


async def run(provider, accounting, *, images=None, purposes=None):
    token = current_accounting.set(accounting)
    try:
        return await analyze(
            provider,
            [{"ref": "req-1", "text": "支持审计日志", "private": "must-not-leave"}],
            images or [image()],
            purposes or ["match_requirements", "propose_regions", "read_text"],
        )
    finally:
        current_accounting.reset(token)


async def test_analyze_sends_only_fixed_refs_png_and_vendor_options(tmp_path):
    items = [
        {
            "image_ref": "img-1",
            "requirement_ref": "req-1",
            "purpose": "match_requirements",
            "region": None,
            "suggested_text": None,
            "confidence": 0.8,
        },
        {
            "image_ref": "img-1",
            "requirement_ref": None,
            "purpose": "propose_regions",
            "region": {"x": 10, "y": 10, "width": 20, "height": 20},
            "suggested_text": None,
            "confidence": 0.7,
        },
        {
            "image_ref": "img-1",
            "requirement_ref": "req-1",
            "purpose": "read_text",
            "region": {"x": 12, "y": 12, "width": 20, "height": 20},
            "suggested_text": "审计日志",
            "confidence": 0.95,
        },
    ]
    vendor = Vendor(response(items))
    accounting = Accounting()
    vendor.events = accounting.events

    result = await run(provider(tmp_path, vendor), accounting)

    assert [proposal.purpose for proposal in result.proposals] == [
        "match_requirements",
        "propose_regions",
        "read_text",
    ]
    assert result.rejected == []
    assert result.usage and result.usage.tokens == 920
    assert result.price_revision == CAPABILITY["price_revision"]
    assert accounting.planned == [1]
    assert accounting.events == ["plan", "admit", "http", "complete"]
    billed = accounting.completed[0][1]
    assert billed.image_count == 1
    assert billed.image_price_revision == CAPABILITY["price_revision"]
    assert billed.image_input_sha256 is not None and len(billed.image_input_sha256) == 64
    [request] = vendor.requests
    body = json.loads(request.content)
    assert str(request.url) == "https://vision.example.test/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {SYNTHETIC_KEY}"
    assert body["reasoning_effort"] == "high"
    assert "screenshot_vision" not in body
    assert "must-not-leave" not in request.content.decode()
    outbound = json.loads(body["messages"][1]["content"][0]["text"])
    assert outbound["requirements"] == [{"ref": "req-1", "text": "支持审计日志"}]
    assert outbound["images"] == [
        {
            "ref": "img-1",
            "width": 80,
            "height": 80,
            "content_rect": {"x": 10, "y": 10, "width": 60, "height": 50},
        }
    ]
    image_part = body["messages"][1]["content"][1]["image_url"]
    assert image_part["detail"] == "high"
    assert base64.b64decode(image_part["url"].split(",", 1)[1]) == image().content
    assert set(
        body["response_format"]["json_schema"]["schema"]["$defs"]["_WireProposal"]["required"]
    ) == {
        "image_ref",
        "requirement_ref",
        "purpose",
        "region",
        "suggested_text",
        "confidence",
    }


async def test_official_reasoning_copy_supplies_vision_capability(tmp_path):
    vendor = Vendor(response([]))
    base = provider(tmp_path, vendor)
    base.reasoning_levels = {
        "high": {
            "request_options": {
                "reasoning_effort": "high",
                "screenshot_vision": deepcopy(CAPABILITY),
            },
            "batch_chars": 8000,
        }
    }
    base.default_reasoning = "high"
    selected = base.at_reasoning("high")
    accounting = Accounting()

    result = await run(selected, accounting)

    assert result.price_revision == CAPABILITY["price_revision"]
    assert selected.reasoning == "high"
    body = json.loads(vendor.requests[0].content)
    assert body["reasoning_effort"] == "high"
    assert "screenshot_vision" not in body


@pytest.mark.parametrize(
    "capability",
    [
        None,
        CAPABILITY | {"verified": False},
        {key: value for key, value in CAPABILITY.items() if key != "price_revision"},
        CAPABILITY
        | {
            "image_input_token_bound": {
                "detail": "high",
                "base_tokens": 0,
                "tokens_per_image": 0,
                "tokens_per_megapixel": 0,
            }
        },
    ],
)
async def test_missing_or_unverified_image_bounds_fail_before_dispatch(tmp_path, capability):
    vendor = Vendor(response([]))
    accounting = Accounting()
    selected = deepcopy(CAPABILITY) if capability is CAPABILITY else capability
    if selected is None:
        selected = {"verified": True}

    with pytest.raises(ProviderFailure) as caught:
        await run(provider(tmp_path, vendor, selected), accounting)

    assert caught.value.code == "billing_bound_unavailable"
    assert vendor.requests == []
    assert accounting.events == accounting.planned == accounting.reservations == []


@pytest.mark.parametrize("limit", ["count", "pixels"])
async def test_catalog_image_limits_fail_before_admission(tmp_path, limit):
    capability = deepcopy(CAPABILITY)
    images = [image("img-1")]
    if limit == "count":
        capability["max_images"] = 1
        images.append(image("img-2"))
    else:
        capability["max_pixels"] = 100
    vendor = Vendor(response([]))
    accounting = Accounting()

    with pytest.raises(ProviderFailure) as caught:
        await run(provider(tmp_path, vendor, capability), accounting, images=images)

    assert caught.value.code == "invalid_vision_input"
    assert vendor.requests == []
    assert accounting.events == []


async def test_image_reservation_uses_pixels_not_base64_length(tmp_path):
    vendor = Vendor(response([]), response([]))
    accounting = Accounting()
    adapter = provider(tmp_path, vendor)
    plain = image(noisy=False)
    noisy = image(noisy=True)
    assert len(plain.content) != len(noisy.content)

    await run(adapter, accounting, images=[plain])
    await run(adapter, accounting, images=[noisy])

    assert accounting.reservations[0] == accounting.reservations[1]
    assert len(vendor.requests[0].content) != len(vendor.requests[1].content)
    assert accounting.reservations[0] > Decimal("0")


def test_preview_builds_the_same_bound_without_http_or_admission(tmp_path):
    vendor = Vendor(response([]))
    adapter = provider(tmp_path, vendor)

    result = preview(
        adapter,
        [{"ref": "req-1", "text": "支持审计日志"}],
        [image()],
        ["match_requirements"],
    )

    assert result == {
        "estimated_charge": result["estimated_charge"],
        "input_tokens": result["input_tokens"],
        "output_tokens": 2048,
        "price_revision": CAPABILITY["price_revision"],
        "planned_calls": 1,
        "catalog_identity": adapter.version,
    }
    assert result["estimated_charge"] > Decimal("0")
    assert result["input_tokens"] > 4096
    assert vendor.requests == []


async def test_unknown_and_out_of_content_refs_are_safely_rejected(tmp_path):
    items = [
        {
            "image_ref": "img-1",
            "requirement_ref": "req-1",
            "purpose": "match_requirements",
            "region": None,
            "suggested_text": None,
            "confidence": 0.9,
        },
        {
            "image_ref": "unknown-image-secret",
            "requirement_ref": "req-1",
            "purpose": "match_requirements",
            "region": None,
            "suggested_text": "secret model output",
            "confidence": 0.5,
        },
        {
            "image_ref": "img-1",
            "requirement_ref": "unknown-requirement-secret",
            "purpose": "match_requirements",
            "region": None,
            "suggested_text": None,
            "confidence": 0.5,
        },
        {
            "image_ref": "img-1",
            "requirement_ref": None,
            "purpose": "read_text",
            "region": {"x": 0, "y": 0, "width": 20, "height": 20},
            "suggested_text": "outside",
            "confidence": 0.5,
        },
        {
            "image_ref": "img-1",
            "requirement_ref": None,
            "purpose": "read_text",
            "region": {"x": 10, "y": 10, "width": 20, "height": 20},
            "suggested_text": "   ",
            "confidence": 0.5,
        },
        {
            "image_ref": "img-1",
            "requirement_ref": None,
            "purpose": "propose_regions",
            "region": {"x": 10, "y": 10, "width": 20, "height": 20},
            "suggested_text": None,
            "confidence": 0.5,
        },
        {
            "image_ref": "img-1",
            "requirement_ref": "req-1",
            "purpose": "read_text",
            "region": {"x": 10, "y": 10, "width": 20, "height": 20},
            "suggested_text": "审计日志",
            "confidence": 0.95,
        },
    ]
    vendor = Vendor(response(items))
    accounting = Accounting()

    result = await run(
        provider(tmp_path, vendor),
        accounting,
        purposes=["match_requirements", "read_text"],
    )

    assert [(item.index, item.reason) for item in result.rejected] == [
        (1, "unknown_image_ref"),
        (2, "unknown_requirement_ref"),
        (3, "region_out_of_bounds"),
        (4, "suggested_text_required"),
        (5, "unrequested_purpose"),
    ]
    assert [item.suggested_text for item in result.proposals] == [None, "审计日志"]
    safe_receipt = repr(result.rejected)
    assert "unknown-image-secret" not in safe_receipt
    assert "unknown-requirement-secret" not in safe_receipt
    assert "secret model output" not in safe_receipt


async def test_malformed_paid_output_is_settled_before_parsing(tmp_path):
    vendor = Vendor(
        httpx.Response(
            200,
            json={
                "model": MODEL,
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                "choices": [{"finish_reason": "stop", "message": {"content": "not-json"}}],
            },
        )
    )
    accounting = Accounting()
    vendor.events = accounting.events

    with pytest.raises(ProviderFailure) as caught:
        await run(provider(tmp_path, vendor), accounting)

    assert caught.value.code == "invalid_provider_output"
    assert len(caught.value.usage) == 1
    assert accounting.events == ["plan", "admit", "http", "complete"]
    assert accounting.unknown_calls == []


async def test_missing_usage_keeps_reservation_unknown(tmp_path):
    vendor = Vendor(response([], usage=False))
    accounting = Accounting()
    vendor.events = accounting.events

    with pytest.raises(ProviderFailure) as caught:
        await run(provider(tmp_path, vendor), accounting)

    assert caught.value.code == "invalid_provider_usage"
    assert accounting.events == ["plan", "admit", "http", "unknown"]
    assert accounting.completed == []
    assert len(accounting.unknown_calls) == 1


async def test_untrusted_vendor_model_is_settled_then_hidden(tmp_path):
    vendor = Vendor(response([], model="secret-model-echo-from-input"))
    accounting = Accounting()
    vendor.events = accounting.events

    with pytest.raises(ProviderFailure) as caught:
        await run(provider(tmp_path, vendor), accounting)

    assert caught.value.code == "invalid_provider_model"
    assert "secret-model-echo-from-input" not in str(caught.value)
    assert accounting.events == ["plan", "admit", "http", "complete"]
    assert caught.value.usage[0].model == "unverified-model"
