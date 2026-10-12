"""Shared exact request encoding and immutable nonmonetary call planning."""

import hashlib
import json

from app.schemas.preflight_contracts import CallPlan, Capability


def serialized_request(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def call_plan(
    capability: Capability,
    provider: str,
    model: str,
    version: str,
    request: bytes,
    *,
    ocr_pages: int = 0,
    search_requests: int = 0,
    browser_units: int = 0,
) -> CallPlan:
    return CallPlan(
        capability=capability,
        provider=provider,
        model=model,
        version=version,
        request_sha256=hashlib.sha256(request).hexdigest(),
        ocr_pages_upper_bound=ocr_pages,
        search_requests=search_requests,
        browser_units=browser_units,
    )
