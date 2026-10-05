"""Shared exact request encoding and explicit zero-liability quote construction."""

import hashlib
import json
import os
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from app.providers.calls import current_accounting
from app.schemas.budget_contracts import BudgetCallQuote, Capability, Payer

MONEY_QUANTUM = Decimal("0.00000001")


def serialized_request(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def token_cost(inputs: int, outputs: int, prices, *, reservation: bool) -> Decimal | None:
    if prices is None or any(price is None for price in prices):
        return None
    values = tuple(Decimal(str(price)) for price in prices)
    if any(not value.is_finite() or value < 0 for value in values):
        return None
    amount = (inputs * values[0] + outputs * values[1]) / 1_000_000
    return amount.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING if reservation else ROUND_HALF_UP)


def zero_quote(
    capability: Capability,
    payer: Payer,
    provider: str,
    model: str,
    version: str,
    request: bytes,
    *,
    currency: str | None = None,
    ocr_pages: int = 0,
    search_requests: int = 0,
) -> BudgetCallQuote:
    accounting = current_accounting.get()
    settings = getattr(accounting, "settings", None)
    billing_currency = currency or (
        settings.billing_currency
        if settings is not None
        else os.environ.get("BID_BILLING_CURRENCY", "USD")
    )
    return BudgetCallQuote(
        capability=capability,
        payer=payer,
        provider=provider,
        model=model,
        version=version,
        price_revision=version,
        request_sha256=hashlib.sha256(request).hexdigest(),
        currency=billing_currency,
        reserved_charge=Decimal(0),
        reserved_task_amount=Decimal(0),
        vendor_usd_upper_bound=Decimal(0) if payer == "local_free" else None,
        ocr_pages_upper_bound=ocr_pages,
        search_requests=search_requests,
    )
