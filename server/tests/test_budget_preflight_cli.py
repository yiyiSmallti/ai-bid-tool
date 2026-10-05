"""DB-free contract scenarios specified before the version/preflight implementation.

Failures covered: legacy nested Cost rejects new fields, quote sums hide unknown
liability, dynamic work is falsely guaranteed, first-call rejection is confused
with whole-pass rejection, and failed waits lose their original accounting/items.
"""

from decimal import Decimal
from uuid import uuid4

from app.schemas.budget_contracts import BudgetCallQuote
from app.schemas.compatibility import legacy_projection
from app.services.budget_preflight import estimate_quotes
from bid_cli.main import partial_completion_exit, wait_for_job


def quote(amount="2", unknown=False):
    return BudgetCallQuote(
        capability="llm",
        payer="org_direct",
        provider="synthetic",
        model="model",
        version="v1",
        price_revision="v1",
        request_sha256="a" * 64,
        currency="USD",
        reserved_charge=Decimal(0),
        reserved_task_amount=None if unknown else Decimal(amount),
        vendor_usd_upper_bound=None if unknown else Decimal(amount),
        unknown_reason="missing_price" if unknown else None,
    )


def test_quote_estimates_preserve_unknown_and_zero():
    known = estimate_quotes([quote(), quote()], "USD")
    assert known.task_amount == Decimal("4") and known.charge == 0
    assert known.basis == "first_pass_upper_bound"
    unknown = estimate_quotes([quote(), quote(unknown=True)], "USD")
    assert unknown.task_amount is None and unknown.usd is None
    assert unknown.unpriced_calls == 1
    zero = estimate_quotes([], "USD")
    assert zero.basis == "zero" and zero.task_amount == 0


def test_legacy_projection_removes_only_new_cost_fields():
    value = {
        "cost": {
            "llm_tokens": 1,
            "ocr_pages": 0,
            "usd": 2,
            "basis": "actual",
            "charge": "2",
            "task_amount": "2",
            "billing_currency": "USD",
            "unpriced_calls": 0,
            "unresolved_calls": 0,
        },
        "data": {
            "result": {
                "cost": {"usd": 2, "basis": "actual"},
                "budget": {"intervention": {"code": "task_budget_exceeded"}},
            }
        },
    }
    result = legacy_projection(value)
    assert result["cost"] == {"llm_tokens": 1, "ocr_pages": 0, "usd": 2}
    assert result["data"]["result"]["cost"] == {"usd": 2}
    assert "budget" not in result["data"]["result"]
    assert value["cost"]["basis"] == "actual"


async def test_failed_wait_keeps_result(monkeypatch):
    from bid_cli import main as module

    body = {
        "ok": False,
        "data": {
            "status": "failed",
            "error": {"exit_code": 4},
            "result": {"budget": {"intervention": {"code": "task_budget_exceeded"}}},
        },
        "cost": {"usd": 2},
        "items": [{"id": "unfinished"}],
    }

    class Stub:
        async def request(self, *args, **kwargs):
            return body

    monkeypatch.setattr(module, "client", lambda: Stub())
    assert await wait_for_job(uuid4(), 1) is body
    assert partial_completion_exit(body) == 4
    assert body["cost"]["usd"] == 2 and body["items"]
