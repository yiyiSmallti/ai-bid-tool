"""Model-generated HTML prototypes rendered offline, through the real API, worker and ingest.

Failure modes enumerated before implementation:
- a dry-run must not call the vendor or create a job, and must price the one planned call;
- a paid run whose fixed inputs changed since the preview must be refused before any job;
- the vendor must receive only the redacted requirement text and the feature declaration;
- the generated HTML and PNG must be stored encrypted, bound to the sandbox receipt, and
  readable only by the same organization;
- a feature selection replaced after submission must stop the job before the vendor call;
- an unavailable sandbox must block admission instead of paying for an unrenderable page;
- the run must enter the existing prepare/ingest chain as an origin=prototype asset.

The vendor is a mock transport and the sandbox is the test-only FakeBrowser; the real
isolated renderer is exercised separately by test_sandbox_runtime. The Rust screenshot
renderer is a genuine dependency of the ingest step and is skipped when absent.
"""

import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from app.models.entities import Job, TaskFeature
from app.models.screenshots import ScreenshotPrototypeRun
from sqlalchemy import func, select, update
from test_card_generation import drafting_client  # pyright: ignore[reportMissingImports]
from test_prototype_decisions import (  # pyright: ignore[reportMissingImports]
    prepare_ingest_prototype,
    renderer_required,
)
from test_response_cards import create_tender  # pyright: ignore[reportMissingImports]
from test_sandbox import FakeBrowser  # pyright: ignore[reportMissingImports]
from test_screenshot_cards import selected_feature  # pyright: ignore[reportMissingImports]

HTML = (
    "<!doctype html><html><head><style>body{font-family:sans-serif}</style></head>"
    "<body><h1>交付流程</h1><table><tr><td>任务</td><td>状态</td></tr></table></body></html>"
)


def html_reply(sent):
    return httpx.Response(
        200,
        json={
            "model": "synthetic-model",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({"html": HTML}, ensure_ascii=False)},
                }
            ],
            "usage": {"prompt_tokens": 500, "completion_tokens": 300},
        },
    )


async def post(api, header, task, body):
    return await api.post(f"/tasks/{task}/prototype-generations", headers=header, json=body)


async def jobs(app, org):
    async with app.state.db.transaction(org) as session:
        return await session.scalar(
            select(func.count()).select_from(Job).where(Job.kind == "prototype_generate")
        )


async def test_generate_render_store_ingest_and_fences(tenants, tmp_path, admin_engine):
    renderer_required()
    org = tenants["orgs"][0]
    async with drafting_client(tenants, tmp_path) as (api, app, headers, vendor, _):
        header = headers[0]
        browser = FakeBrowser()
        app.state.processor.sandbox_browser = browser

        async def respond(sent):
            return html_reply(sent)

        vendor.respond = respond
        task, _, extraction, requirements = await create_tender(api, app, header, tmp_path)
        feature = await selected_feature(api, header, task)
        body = {
            "extraction_job_id": extraction,
            "requirement_id": requirements[2]["id"],
            "task_feature_id": feature["id"],
        }

        preview = await post(api, header, task, {**body, "dry_run": True})
        assert preview.status_code == 200, preview.text
        estimate = preview.json()["data"]
        assert estimate["planned_calls"] == 1 and estimate["admission_blocker"] is None
        assert float(estimate["estimated_charge"]) > 0
        assert not vendor.drafts and await jobs(app, org) == 0

        stale = await post(api, header, task, {**body, "expected_input_hash": "0" * 64})
        assert stale.status_code == 409, stale.text
        assert stale.json()["data"]["error"]["code"] == "prototype_input_changed"
        assert await jobs(app, org) == 0

        submitted = await post(
            api, header, task, {**body, "expected_input_hash": estimate["input_hash"]}
        )
        assert submitted.status_code == 200, submitted.text
        job_id = submitted.json()["data"]["job_id"]
        await app.state.processor(header["X-Org-Id"], job_id)
        status = (await api.get(f"/jobs/{job_id}", headers=header)).json()["data"]
        assert status["status"] == "succeeded", status
        generation = status["result"]["prototype_generation"]
        assert "submission" not in status["result"]
        assert (
            generation["origin"] == "prototype" and generation["html"]["media_type"] == "text/html"
        )
        assert browser.calls == 1 and len(vendor.drafts) == 1
        sent = vendor.drafts[0]
        assert set(sent) == {"requirement", "feature"}
        assert sent["feature"]["name"] == "Synthetic delivery workflow"

        prototype_id = generation["id"]
        shown = await api.get(f"/prototype-runs/{prototype_id}", headers=header)
        assert shown.status_code == 200 and shown.json()["data"] == generation
        source = await api.get(f"/prototype-runs/{prototype_id}/source", headers=header)
        assert source.status_code == 200 and source.headers["content-type"] == "image/png"
        import hashlib

        assert hashlib.sha256(source.content).hexdigest() == generation["source_image_sha256"]
        async with app.state.db.transaction(org) as session:
            row = await session.get(ScreenshotPrototypeRun, UUID(prototype_id))
            assert row is not None
            stored = app.state.storage.path(org, row.html_storage_key).read_bytes()
            assert stored.startswith(b"BIDFILE1\n") and HTML.encode() not in stored
            assert row.sandbox_receipt_id and row.provenance["model"] == "synthetic-model"

        for path in (f"/prototype-runs/{prototype_id}", f"/prototype-runs/{prototype_id}/source"):
            assert (await api.get(path, headers=headers[1])).status_code == 404
        assert (await post(api, headers[1], task, {**body, "dry_run": True})).status_code == 404

        uploaded = await prepare_ingest_prototype(
            api,
            app,
            header,
            task,
            UUID(extraction),
            {
                "id": UUID(prototype_id),
                "source_png": source.content,
                "source_image_sha256": generation["source_image_sha256"],
            },
        )
        assert uploaded["asset"]["prototype_generation"]["id"] == prototype_id

        browser.available = False
        blocked = await post(api, header, task, {**body, "dry_run": True})
        assert blocked.json()["data"]["admission_blocker"] == "sandbox_runtime_unavailable"
        browser.available = True

        other = {**body, "requirement_id": requirements[3]["id"]}
        fresh = (await post(api, header, task, {**other, "dry_run": True})).json()["data"]
        queued = await post(
            api, header, task, {**other, "expected_input_hash": fresh["input_hash"]}
        )
        fenced_job = queued.json()["data"]["job_id"]
        async with app.state.db.transaction(org) as session:
            await session.execute(
                update(TaskFeature)
                .where(TaskFeature.id == UUID(feature["id"]))
                .values(active=False)
            )
        await app.state.processor(header["X-Org-Id"], fenced_job)
        fenced = (await api.get(f"/jobs/{fenced_job}", headers=header)).json()["data"]
        assert fenced["status"] == "failed", fenced
        assert fenced["error"]["code"] in {"inactive_selection", "prototype_input_changed"}
        assert len(vendor.drafts) == 1 and browser.calls == 1


@pytest.mark.skipif(
    os.environ.get("BID_SANDBOX_RUNTIME_TEST") != "1",
    reason="requires the independently provisioned sandbox supervisor",
)
async def test_real_sandbox_renders_generated_prototype(tenants, tmp_path):
    from app.providers.browser import create_browser_provider

    org = tenants["orgs"][0]
    async with drafting_client(tenants, tmp_path) as (api, app, headers, vendor, _):
        header = headers[0]
        app.state.processor.sandbox_browser = create_browser_provider()

        async def respond(sent):
            return html_reply(sent)

        vendor.respond = respond
        task, _, extraction, requirements = await create_tender(api, app, header, tmp_path)
        feature = await selected_feature(api, header, task)
        body = {
            "extraction_job_id": extraction,
            "requirement_id": requirements[2]["id"],
            "task_feature_id": feature["id"],
        }
        estimate = (await post(api, header, task, {**body, "dry_run": True})).json()["data"]
        assert estimate["admission_blocker"] is None, estimate
        submitted = await post(
            api, header, task, {**body, "expected_input_hash": estimate["input_hash"]}
        )
        job_id = submitted.json()["data"]["job_id"]
        await app.state.processor(header["X-Org-Id"], job_id)
        status = (await api.get(f"/jobs/{job_id}", headers=header)).json()["data"]
        assert status["status"] == "succeeded", status
        generation = status["result"]["prototype_generation"]
        source = await api.get(f"/prototype-runs/{generation['id']}/source", headers=header)
        assert source.status_code == 200
        async with app.state.db.transaction(org) as session:
            row = await session.get(ScreenshotPrototypeRun, UUID(generation["id"]))
            assert row is not None and row.provenance["runtime_versions"]
        target = Path(os.environ.get("BID_SANDBOX_ARTIFACT_DIR", "data/work/sandbox-verification"))
        await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        (target / "generated-prototype.png").write_bytes(source.content)
