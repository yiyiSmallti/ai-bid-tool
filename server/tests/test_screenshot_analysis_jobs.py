"""Screenshot analysis through the real API, job processor and accounted HTTP adapter.

Failure modes fixed before the scenarios:
- dry-run calls a deterministic preview only and creates no job, analysis row, suggestion,
  usage, audit, reservation, balance change, queue item, or vendor request;
- a missing/stale expected input hash, changed image hash, foreign task/requirement/rendition,
  or withdrawn asset cannot enqueue or publish against the preflight snapshot;
- all three purposes use only request-local refs and published suggestions remain unreviewed
  with no confirmation endpoint or writable confirmation field;
- invalid paid JSON and a response containing only unknown refs settle valid usage and fees,
  fail the job, and publish no analysis run or suggestion;
- withdrawal after submission but before worker admission stops the attempt without a vendor
  call, usage row, analysis row, or suggestion;
- analysis submission, job status and suggestion listing are hidden from the other org as 404;
- actual token usage, vendor call state, balance deduction and job cost remain consistent.

The renderer and PostgreSQL are genuine integration dependencies. The existing helpers skip
explicitly when the Rust binary or ``BID_TEST_ADMIN_URL`` is absent. Vendor traffic uses only
``httpx.MockTransport`` with labelled synthetic payloads.
"""

import hashlib
import json
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from app.api.main import create_app
from app.models.entities import AuditLog, BalanceEntry, Job, OrgBalance, UsageRecord, VendorCall
from app.models.screenshots import ScreenshotAnalysisRun, ScreenshotSuggestion
from app.providers.llm import OpenAICompatibleExtractor
from conftest import FakeQueue  # pyright: ignore[reportMissingImports]
from sqlalchemy import func, select
from test_llm_providers import settings_for  # pyright: ignore[reportMissingImports]
from test_response_cards import (  # pyright: ignore[reportMissingImports]
    TENDER_LINES,
    create_tender,
    login,
)
from test_screenshot_cards import (  # pyright: ignore[reportMissingImports]
    ingest_synthetic_screenshot,
    selected_feature,
)

MODEL = "synthetic-screenshot-vision"
CAPABILITY = {
    "verified": True,
    "max_images": 20,
    "max_pixels": 20_000_000,
    "image_input_token_bound": {
        "detail": "high",
        "base_tokens": 64,
        "tokens_per_image": 256,
        "tokens_per_megapixel": 1024,
    },
    "price_revision": "synthetic-vision-price-v1",
}


def openai_reply(content, *, prompt_tokens=100, completion_tokens=50):
    return httpx.Response(
        200,
        json={
            "model": MODEL,
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        },
    )


class VisionVendor:
    def __init__(self, *vision_replies: httpx.Response | Callable[[dict], httpx.Response]):
        self.vision_replies: list[httpx.Response | Callable[[dict], httpx.Response]] = list(
            vision_replies
        )
        self.extraction_requests = []
        self.vision_requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user_content = body["messages"][-1]["content"]
        if isinstance(user_content, list):
            self.vision_requests.append(body)
            reply = self.vision_replies.pop(0)
            if isinstance(reply, httpx.Response):
                return reply
            return reply(body)
        self.extraction_requests.append(body)
        items = []
        for value in re.findall(r'<page number="(\d+)">', user_content):
            page = int(value)
            quote = TENDER_LINES[page - 1]
            items.append(
                {
                    "category": "technical",
                    "starred": False,
                    "text": quote,
                    "ref": value,
                    "quote": quote,
                    "condition": None,
                }
            )
        return openai_reply(json.dumps({"items": items}))


def successful_vision(body):
    request = json.loads(body["messages"][1]["content"][0]["text"])
    image = request["images"][0]
    rect = image["content_rect"]
    region = {
        "x": rect["x"],
        "y": rect["y"],
        "width": min(16, rect["width"]),
        "height": min(10, rect["height"]),
    }
    items = [
        {
            "image_ref": image["ref"],
            "requirement_ref": request["requirements"][0]["ref"],
            "purpose": "match_requirements",
            "region": None,
            "suggested_text": None,
            "confidence": 0.9,
        },
        {
            "image_ref": image["ref"],
            "requirement_ref": request["requirements"][0]["ref"],
            "purpose": "propose_regions",
            "region": region,
            "suggested_text": None,
            "confidence": 0.8,
        },
        {
            "image_ref": image["ref"],
            "requirement_ref": request["requirements"][0]["ref"],
            "purpose": "read_text",
            "region": region,
            "suggested_text": "Synthetic visible workflow text",
            "confidence": 0.95,
        },
    ]
    return openai_reply(json.dumps({"items": items}))


@asynccontextmanager
async def analysis_client(tenants, tmp_path, vendor, *, balance="100"):
    settings = settings_for(
        tmp_path,
        "openai",
        llm_model=MODEL,
        llm_base_url="https://vision.example.test/v1",
        llm_max_output_tokens=2048,
        llm_concurrency=1,
    )
    provider = OpenAICompatibleExtractor(
        settings,
        httpx.MockTransport(vendor),
        platform_model_id="synthetic-vision-catalog",
        sale_usd_per_mtok=(1.0, 2.0),
    )
    provider.version = "http-extract-v5:synthetic-vision-catalog:1"
    provider.model_revision = 1
    provider.reasoning_levels = {
        "high": {
            "label": "Synthetic official high",
            "request_options": {
                "reasoning_effort": "high",
                "screenshot_vision": CAPABILITY,
            },
            "batch_chars": 8000,
        }
    }
    provider.default_reasoning = "high"
    app = create_app(settings, llm=provider, queue=FakeQueue())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        headers = [
            await login(api, org, label)
            for org, label in zip(tenants["orgs"], ("a", "b"), strict=True)
        ]
        for org in tenants["orgs"]:
            async with app.state.db.transaction(org) as session:
                amount = Decimal(balance)
                session.add(OrgBalance(org_id=org, currency="USD", balance=amount))
                session.add(
                    BalanceEntry(
                        org_id=org,
                        kind="adjust",
                        currency="USD",
                        amount=amount,
                        balance_after=amount,
                        actor="test",
                        reason="Synthetic screenshot analysis funds",
                    )
                )
        yield api, app, headers, provider


async def fixture_chain(api, app, header, tmp_path, suffix):
    task, _, extraction, requirements = await create_tender(
        api, app, header, tmp_path, suffix=suffix
    )
    feature = await selected_feature(api, header, task)
    uploaded = await ingest_synthetic_screenshot(api, header, task, extraction, feature["id"])
    return task, extraction, requirements, uploaded


def request_body(extraction, requirement, rendition, **changes):
    value = {
        "extraction_job_id": extraction,
        "requirement_ids": [requirement["id"]],
        "images": [
            {
                "rendition_id": rendition["id"],
                "expected_image_sha256": rendition["image"]["sha256"],
            }
        ],
        "purposes": ["match_requirements", "propose_regions", "read_text"],
        "reasoning": "high",
        "dry_run": True,
    }
    value.update(changes)
    return value


async def counts(app, org):
    async with app.state.db.transaction(UUID(str(org))) as session:
        return {
            "jobs": await session.scalar(
                select(func.count()).select_from(Job).where(Job.kind == "screenshot_analyze")
            ),
            "runs": await session.scalar(select(func.count()).select_from(ScreenshotAnalysisRun)),
            "suggestions": await session.scalar(
                select(func.count()).select_from(ScreenshotSuggestion)
            ),
            "usage": await session.scalar(select(func.count()).select_from(UsageRecord)),
            "calls": await session.scalar(select(func.count()).select_from(VendorCall)),
            "audits": await session.scalar(select(func.count()).select_from(AuditLog)),
            "balance": Decimal(await session.scalar(select(OrgBalance.balance))),
        }


def artifact(name: str, value: dict):
    path = Path("data/work/screenshots")
    path.mkdir(parents=True, exist_ok=True)
    safe = json.loads(json.dumps(value))
    for key in list(safe):
        if key.endswith("_id") and isinstance(safe[key], str):
            safe[key] = "uuid-sha256:" + hashlib.sha256(safe[key].encode()).hexdigest()[:16]
    (path / name).write_text(json.dumps(safe, indent=2, sort_keys=True) + "\n")


async def test_three_purpose_analysis_preflight_accounting_and_org_isolation(tenants, tmp_path):
    vendor = VisionVendor(successful_vision)
    async with analysis_client(tenants, tmp_path, vendor) as (api, app, headers, _):
        header = headers[0]
        task, extraction, requirements, uploaded = await fixture_chain(
            api, app, header, tmp_path, "vision-success"
        )
        rendition = uploaded["rendition"]
        before = await counts(app, tenants["orgs"][0])
        queued_before = list(app.state.queue.calls)
        preview = await api.post(
            f"/tasks/{task}/screenshot-analyses",
            headers=header,
            json=request_body(extraction, requirements[2], rendition),
        )
        assert preview.status_code == 200, preview.text
        preflight = preview.json()["data"]
        assert preflight["dry_run"] is True
        assert preflight["planned_calls"] == preflight["input_image_count"] == 1
        assert Decimal(preflight["estimated_charge"]) > 0
        assert preflight["estimated_cost"]["ocr_pages"] == 0
        assert vendor.vision_requests == []
        assert await counts(app, tenants["orgs"][0]) == before
        assert app.state.queue.calls == queued_before

        stale = await api.post(
            f"/tasks/{task}/screenshot-analyses",
            headers=header,
            json=request_body(
                extraction,
                requirements[2],
                rendition,
                dry_run=False,
                expected_input_hash="0" * 64,
            ),
        )
        assert stale.status_code == 409
        assert stale.json()["data"]["error"]["code"] == "screenshot_input_changed"
        assert vendor.vision_requests == []
        assert await counts(app, tenants["orgs"][0]) == before
        assert app.state.queue.calls == queued_before

        foreign = await api.post(
            f"/tasks/{task}/screenshot-analyses",
            headers=headers[1],
            json=request_body(extraction, requirements[2], rendition),
        )
        assert foreign.status_code == 404

        submitted = await api.post(
            f"/tasks/{task}/screenshot-analyses",
            headers=header,
            json=request_body(
                extraction,
                requirements[2],
                rendition,
                dry_run=False,
                expected_input_hash=preflight["input_hash"],
            ),
        )
        assert submitted.status_code == 200, submitted.text
        job_id = submitted.json()["data"]["job_id"]
        balance_before = (await counts(app, tenants["orgs"][0]))["balance"]
        await app.state.processor(header["X-Org-Id"], job_id)
        status = await api.get(f"/jobs/{job_id}", headers=header)
        assert status.status_code == 200, status.text
        terminal = status.json()["data"]
        assert terminal["status"] == "succeeded" and terminal["result"]["created"] == 3
        assert terminal["result"]["completion"] == "complete"
        assert terminal["result"]["cost"]["llm_tokens"] == 150
        analysis_id = terminal["result"]["analysis_run_id"]
        listed = await api.get(f"/screenshot-analyses/{analysis_id}/suggestions", headers=header)
        assert listed.status_code == 200, listed.text
        suggestions = listed.json()["items"]
        assert {row["proposal"]["purpose"] for row in suggestions} == {
            "match_requirements",
            "propose_regions",
            "read_text",
        }
        assert all(
            row["status"] == "unreviewed" and row["confirmed_by"] is None for row in suggestions
        )
        assert all(row["requirement_id"] == requirements[2]["id"] for row in suggestions)
        assert (
            await api.post(
                f"/screenshot-analyses/{analysis_id}/suggestions/{suggestions[0]['id']}/confirm",
                headers=header,
            )
        ).status_code == 404
        assert (
            await api.get(f"/screenshot-analyses/{analysis_id}/suggestions", headers=headers[1])
        ).status_code == 404
        assert (await api.get(f"/jobs/{job_id}", headers=headers[1])).status_code == 404
        after = await counts(app, tenants["orgs"][0])
        assert after["runs"] == before["runs"] + 1
        assert after["suggestions"] == before["suggestions"] + 3
        assert after["calls"] == before["calls"] + 1
        assert after["usage"] == before["usage"] + 1
        assert balance_before - after["balance"] == Decimal("0.00020000")
        artifact(
            "analysis-success.json",
            {
                "job_id": job_id,
                "analysis_id": analysis_id,
                "purposes": [row["proposal"]["purpose"] for row in suggestions],
                "charge": str(balance_before - after["balance"]),
            },
        )


async def test_withdrawal_after_submission_stops_before_vendor_call(tenants, tmp_path):
    vendor = VisionVendor(successful_vision)
    async with analysis_client(tenants, tmp_path, vendor) as (api, app, headers, _):
        header = headers[0]
        task, extraction, requirements, uploaded = await fixture_chain(
            api, app, header, tmp_path, "vision-withdraw"
        )
        rendition, asset = uploaded["rendition"], uploaded["asset"]
        preview = (
            await api.post(
                f"/tasks/{task}/screenshot-analyses",
                headers=header,
                json=request_body(extraction, requirements[2], rendition),
            )
        ).json()["data"]
        submitted = await api.post(
            f"/tasks/{task}/screenshot-analyses",
            headers=header,
            json=request_body(
                extraction,
                requirements[2],
                rendition,
                dry_run=False,
                expected_input_hash=preview["input_hash"],
            ),
        )
        assert submitted.status_code == 200
        job_id = submitted.json()["data"]["job_id"]
        before = await counts(app, tenants["orgs"][0])
        withdrawn = await api.post(
            f"/screenshots/{asset['id']}/withdrawals",
            headers=header,
            json={"reason": "Synthetic withdrawal before worker admission"},
        )
        assert withdrawn.status_code == 200
        await app.state.processor(header["X-Org-Id"], job_id)
        status = (await api.get(f"/jobs/{job_id}", headers=header)).json()["data"]
        assert status["status"] == "failed"
        assert status["error"]["code"] == "withdrawn_image"
        after = await counts(app, tenants["orgs"][0])
        assert vendor.vision_requests == []
        assert after["usage"] == before["usage"] and after["calls"] == before["calls"]
        assert after["runs"] == before["runs"] and after["suggestions"] == before["suggestions"]


@pytest.mark.parametrize("case", ["invalid_json", "unknown_ref"])
async def test_paid_invalid_or_unknown_only_output_publishes_nothing(tenants, tmp_path, case):
    if case == "invalid_json":
        reply = openai_reply("not-json")
        expected = "invalid_provider_output"
    else:
        reply = openai_reply(
            json.dumps(
                {
                    "items": [
                        {
                            "image_ref": "private-unknown-image-ref",
                            "requirement_ref": "requirement_0",
                            "purpose": "match_requirements",
                            "region": None,
                            "suggested_text": "private-invalid-output",
                            "confidence": 0.5,
                        }
                    ]
                }
            )
        )
        expected = "invalid_vision_suggestions"
    vendor = VisionVendor(reply)
    async with analysis_client(tenants, tmp_path, vendor) as (api, app, headers, _):
        header = headers[0]
        task, extraction, requirements, uploaded = await fixture_chain(
            api, app, header, tmp_path, f"vision-{case}"
        )
        rendition = uploaded["rendition"]
        preview = (
            await api.post(
                f"/tasks/{task}/screenshot-analyses",
                headers=header,
                json=request_body(extraction, requirements[2], rendition),
            )
        ).json()["data"]
        submitted = await api.post(
            f"/tasks/{task}/screenshot-analyses",
            headers=header,
            json=request_body(
                extraction,
                requirements[2],
                rendition,
                dry_run=False,
                expected_input_hash=preview["input_hash"],
            ),
        )
        job_id = submitted.json()["data"]["job_id"]
        before = await counts(app, tenants["orgs"][0])
        await app.state.processor(header["X-Org-Id"], job_id)
        terminal = (await api.get(f"/jobs/{job_id}", headers=header)).json()["data"]
        assert terminal["status"] == "failed" and terminal["error"]["code"] == expected
        assert "private-unknown-image-ref" not in json.dumps(terminal)
        assert "private-invalid-output" not in json.dumps(terminal)
        after = await counts(app, tenants["orgs"][0])
        assert after["usage"] == before["usage"] + 1
        assert after["calls"] == before["calls"] + 1
        assert after["balance"] < before["balance"]
        assert after["runs"] == before["runs"] and after["suggestions"] == before["suggestions"]
        artifact(
            f"analysis-{case}.json",
            {"job_id": job_id, "error": terminal["error"], "charged": True},
        )
