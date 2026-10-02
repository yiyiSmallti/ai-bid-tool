"""Official reasoning levels end to end: catalog, submission, per-level requests, history."""

import json
import re

import httpx
import pymupdf
import pytest
from app.api.main import create_app
from app.core.config import Settings
from conftest import FakeQueue
from fakes import FakeLLM
from sqlalchemy import text
from test_api import create_document
from test_job_boundaries import session_for
from test_llm_providers import GOOD_ITEMS
from test_platform_api import org_header
from test_platform_auth import platform_settings, sign_in

LEVELS = [
    {
        "name": "low",
        "label": "轻度推理",
        "request_options": {"thinking": {"type": "enabled"}, "reasoning_effort": "low"},
        "batch_chars": 8000,
    },
    {
        "name": "high",
        "request_options": {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
    },
    {
        "name": "max",
        "label": "深度推理",
        "request_options": {"thinking": {"type": "enabled"}, "reasoning_effort": "max"},
        "batch_chars": 1000,
    },
]
GLM = {
    "id": "glm",
    "capability": "llm_extract",
    "provider": "openai",
    "model": "glm-synthetic",
    "base_url": "https://llm.example.test/v1",
    "credential": "main",
    "vendor_input_usd_per_mtok": 1.0,
    "vendor_output_usd_per_mtok": 2.0,
    "sale_input_per_mtok": 1.0,
    "sale_output_per_mtok": 2.0,
    "default": True,
    "enabled": True,
    "reasoning": LEVELS,
    "default_reasoning": "max",
}


def two_page_tender() -> bytes:
    # Each page is about 1,000 characters, so a 1,000-character budget sends them apart.
    with pymupdf.open() as pdf:
        for line in ("Minimum memory is 64 GB.", "A valid certificate must be provided."):
            page = pdf.new_page()
            page.insert_textbox(pdf[-1].rect + (40, 40, -40, -40), (line + " ") * 40)
        return pdf.tobytes()


class Vendor:
    """Answers every page in the request with its fixture item and records request bodies."""

    def __init__(self):
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        pages = re.findall(r'<page number="(\d+)">', body["messages"][1]["content"])
        items = [GOOD_ITEMS[int(page) - 1] for page in pages]
        reply = {
            "model": "glm-synthetic",
            "choices": [
                {"finish_reason": "stop", "message": {"content": json.dumps({"items": items})}}
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        return httpx.Response(200, json=reply)


@pytest.fixture
async def glm(operator, tmp_path, monkeypatch, tenants):
    monkeypatch.setenv("BID_PLATFORM_CREDENTIAL_MAIN", "synthetic-platform-key")
    vendor = Vendor()
    app = create_app(
        platform_settings(tmp_path), queue=FakeQueue(), llm_transport=httpx.MockTransport(vendor)
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        ops = {"Authorization": f"Bearer {(await sign_in(api)).json()['data']['session']}"}
        saved = await api.post("/platform/models", headers=ops, json=GLM)
        assert saved.status_code == 200, saved.text
        org = tenants["orgs"][0]
        async with app.state.db.transaction() as session:
            await session.execute(
                text("SELECT * FROM platform_adjust_balance(:o, 'add', 10, 'seed', 'ops', 'USD')"),
                {"o": org},
            )
        header = await org_header(api, org, "a@example.test")
        task, document = await create_document(api, header, two_page_tender())
        parsed = await api.post(f"/documents/{document}/parse", headers=header, json={})
        await app.state.processor(str(org), parsed.json()["data"]["job_id"])
        yield api, app, vendor, header, ops, task, document


async def extract(api, app, header, document, **body):
    response = await api.post(f"/documents/{document}/extract", headers=header, json=body)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    await app.state.processor(header["X-Org-Id"], data["job_id"])
    return data


async def test_each_level_sends_its_options_and_keeps_its_own_results(glm):
    api, app, vendor, header, _, task, document = glm

    planned = (
        await api.post(f"/documents/{document}/extract", headers=header, json={"dry_run": True})
    ).json()["data"]
    assert planned["reasoning"] == "max"
    assert planned["reasoning_levels"] == [
        {"name": "low", "label": "轻度推理", "default": False},
        {"name": "high", "label": None, "default": False},
        {"name": "max", "label": "深度推理", "default": True},
    ]
    assert vendor.bodies == []

    # Omitted level: the official default, max, with its 1,000-character batches.
    default = await extract(api, app, header, document)
    assert default["reasoning"] == "max"
    assert [b["reasoning_effort"] for b in vendor.bodies] == ["max", "max"]
    assert all(b["thinking"] == {"type": "enabled"} for b in vendor.bodies)
    assert all(b["model"] == "glm-synthetic" for b in vendor.bodies)

    # Another level is another job, sent in one 8,000-character batch.
    vendor.bodies.clear()
    low = await extract(api, app, header, document, reasoning="low")
    assert low["job_id"] != default["job_id"] and low["reasoning"] == "low"
    assert [b["reasoning_effort"] for b in vendor.bodies] == ["low"]

    # The same level again returns the same job without calling the vendor.
    vendor.bodies.clear()
    again = await extract(api, app, header, document, reasoning="low")
    assert (again["job_id"], again["cached"]) == (low["job_id"], True)
    assert vendor.bodies == []

    status = (await api.get(f"/jobs/{low['job_id']}", headers=header)).json()["data"]
    assert (status["reasoning"], status["result"]["reasoning"]) == ("low", "low")
    assert status["result"]["model"] == "glm-synthetic"

    latest = (await api.get(f"/tasks/{task}/requirements", headers=header)).json()["items"]
    assert [(r["job_id"], r["reasoning"]) for r in latest] == [(low["job_id"], "low")] * 2
    older = (
        await api.get(
            f"/tasks/{task}/requirements", headers=header, params={"job": default["job_id"]}
        )
    ).json()["items"]
    assert [(r["job_id"], r["reasoning"]) for r in older] == [(default["job_id"], "max")] * 2
    assert {r["id"] for r in latest}.isdisjoint({r["id"] for r in older})

    history = (await api.get(f"/tasks/{task}/extractions", headers=header)).json()["items"]
    assert [(h["job_id"], h["reasoning"], h["latest"]) for h in history] == [
        (low["job_id"], "low", True),
        (default["job_id"], "max", False),
    ]
    assert [(h["saved"], h["rejected"], h["tokens"], h["status"]) for h in history] == [
        (2, 0, 150, "succeeded"),
        (2, 0, 300, "succeeded"),
    ]
    filtered = await api.get(
        f"/tasks/{task}/extractions", headers=header, params={"document": document}
    )
    assert len(filtered.json()["items"]) == 2


async def test_unknown_levels_and_parse_levels_are_rejected(glm):
    api, _, vendor, header, _, task, document = glm
    unknown = await api.post(
        f"/documents/{document}/extract", headers=header, json={"reasoning": "medium"}
    )
    error = unknown.json()["data"]["error"]
    assert unknown.status_code == 400 and (error["code"], error["exit_code"]) == (
        "unsupported_reasoning",
        2,
    )
    assert "low, high, max" in error["message"]
    parse = await api.post(
        f"/documents/{document}/parse", headers=header, json={"reasoning": "low"}
    )
    assert parse.status_code == 400 and parse.json()["data"]["error"]["code"] == "invalid_input"
    foreign = await api.get(
        f"/tasks/{task}/requirements",
        headers=header,
        params={"job": "00000000-0000-0000-0000-000000000001"},
    )
    assert foreign.status_code == 404
    assert vendor.bodies == []


async def test_catalog_validates_levels_and_tests_each_one(glm):
    api, _, vendor, _, ops, _, _ = glm
    for broken in (
        {**GLM, "default_reasoning": "medium"},
        {**GLM, "reasoning": [LEVELS[0], LEVELS[0]], "default_reasoning": "low"},
        {**GLM, "reasoning": [], "default_reasoning": "max"},
        {**GLM, "default_reasoning": None},
    ):
        response = await api.post("/platform/models", headers=ops, json=broken)
        assert response.status_code == 422, broken

    listed = (await api.get("/platform/models", headers=ops)).json()["items"]
    [model] = [m for m in listed if m["id"] == "glm"]
    assert [level["name"] for level in model["reasoning"]] == ["low", "high", "max"]
    assert model["default_reasoning"] == "max"

    tested = (await api.post("/platform/models/glm/test", headers=ops)).json()["data"]
    assert tested["passed"] is True
    assert [(level["reasoning"], level["passed"]) for level in tested["levels"]] == [
        ("low", True),
        ("high", True),
        ("max", True),
    ]
    assert sorted(b["reasoning_effort"] for b in vendor.bodies) == ["high", "low", "max"]


async def test_models_without_levels_ignore_the_level_with_a_warning(tenants, tmp_path, pdf_bytes):
    app = create_app(Settings(data_dir=tmp_path), llm=FakeLLM(), queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        _, document = await create_document(api, header, pdf_bytes)
        parsed = await api.post(f"/documents/{document}/parse", headers=header, json={})
        await app.state.processor(header["X-Org-Id"], parsed.json()["data"]["job_id"])
        response = await api.post(
            f"/documents/{document}/extract", headers=header, json={"reasoning": "high"}
        )
        body = response.json()
        assert response.status_code == 200 and body["data"]["reasoning"] is None
        assert any("no reasoning levels" in warning for warning in body["warnings"])
        await app.state.processor(header["X-Org-Id"], body["data"]["job_id"])
        status = (await api.get(f"/jobs/{body['data']['job_id']}", headers=header)).json()["data"]
        assert status["status"] == "succeeded" and status["reasoning"] is None
