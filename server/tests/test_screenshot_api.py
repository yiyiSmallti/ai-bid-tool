"""Phase A failure modes and repeatable API/processor acceptance.

Failures: multipart spooling or duplicate/missing parts; unbounded fields;
foreign task/job/asset/rendition/run/decision access; token privacy release;
wrong hash or image kind; footer-region observations; stale preflight;
withdrawal between admission/publication; unknown vendor refs; missing prices;
paid invalid model output; batch decision omissions and changed card revisions.
Database scenarios use the actual tenant API and Processor. No real vendor calls.
"""

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from app.api.screenshots import memory_upload
from app.core.errors import ServiceError
from app.schemas.screenshot_contracts import ImagePlan, ScreenshotIngest, UploadSource
from app.services import screenshots
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from test_response_cards import create_tender, phase_one_client
from test_screenshot_cards import ingest_synthetic_screenshot, selected_feature
from test_screenshot_renderer import _rgb_png


@pytest.fixture
def parser_app():
    app = FastAPI()

    @app.exception_handler(ServiceError)
    async def failed(request, exc):
        return JSONResponse({"code": exc.code}, status_code=exc.status)

    @app.post("/upload")
    async def upload(request: Request):
        body, content = await memory_upload(request)
        return {"bytes": len(content), "hash": body.reviewed_upload_sha256}

    return app


def receipt():
    sha = "a" * 64
    return {
        "extraction_job_id": str(uuid4()),
        "idempotency_key": str(uuid4()),
        "reviewed_upload_sha256": sha,
        "prepared": {
            "source": {
                "kind": "upload",
                "task_feature_id": str(uuid4()),
                "image_kind": "screenshot",
                "source_label": "Synthetic",
                "software_version": "1",
                "environment": "test",
            },
            "source_sha256": sha,
            "source_width": 1,
            "source_height": 1,
            "plan": {},
            "plan_sha256": screenshots.digest(ImagePlan().model_dump(mode="json")),
            "preparation_profile": "screenshot-privacy-v1",
            "prepared_at": "2026-10-02T00:00:00Z",
            "image": {"sha256": sha, "size_bytes": 3, "width_px": 1, "height_px": 1},
            "mapping": {
                "crop": {"x": 0, "y": 0, "width": 1, "height": 1},
                "content_offset_x": 0,
                "content_offset_y": 0,
                "content_width": 1,
                "content_height": 1,
                "footer_height": 0,
            },
        },
    }


async def test_memory_ingest_parser_never_uses_framework_temporary_files(parser_app, monkeypatch):
    def reject_spool(*args, **kwargs):
        raise AssertionError("unreviewed upload must remain in memory")

    monkeypatch.setattr("starlette.formparsers.SpooledTemporaryFile", reject_spool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=parser_app), base_url="http://test"
    ) as api:
        result = await api.post(
            "/upload",
            files={
                "file": ("redacted.png", b"png", "image/png"),
                "input": (None, json.dumps(receipt()), "application/json"),
            },
        )
        assert result.status_code == 200 and result.json()["bytes"] == 3
        for parts in (
            [("file", ("x.png", b"x"))],
            [("file", ("x.png", b"x")), ("file", ("y.png", b"y"))],
            [("secret", (None, "not accepted"))],
            [("input", (None, "x" * (128 * 1024 + 1)))],
            [("file", ("x.png", b"x")), ("input", (None, "{}"))],
        ):
            result = await api.post("/upload", files=parts)
            assert result.status_code in {400, 413}


async def test_all_screenshot_routes_reject_foreign_org(tenants, tmp_path):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, extraction, requirements = await create_tender(
            api, app, headers[1], tmp_path, suffix="foreign-images"
        )
        feature = await selected_feature(api, headers[1], task)
        stored = await ingest_synthetic_screenshot(api, headers[1], task, extraction, feature["id"])
        asset, rendition = stored["asset"], stored["rendition"]
        for method, path, payload in (
            ("GET", f"/tasks/{task}/screenshots?job={extraction}", None),
            ("GET", f"/screenshots/{asset['id']}", None),
            (
                "POST",
                f"/screenshots/{asset['id']}/withdrawals",
                {"reason": "Synthetic isolation check"},
            ),
            ("POST", f"/screenshot-renditions/{rendition['id']}/preview-link", None),
            ("GET", f"/screenshot-renditions/{rendition['id']}/content?signature=invalid", None),
            (
                "POST",
                f"/screenshots/{asset['id']}/renditions",
                {
                    "parent_rendition_id": rendition["id"],
                    "expected_image_sha256": rendition["image"]["sha256"],
                    "plan": {},
                    "dry_run": True,
                },
            ),
            (
                "POST",
                f"/tasks/{task}/prototype-decisions/preview",
                {
                    "extraction_job_id": extraction,
                    "module_label": "Synthetic",
                    "task_feature_ids": [feature["id"]],
                },
            ),
            ("GET", f"/tasks/{task}/prototype-decisions?job={extraction}", None),
            ("GET", f"/screenshot-analyses/{uuid4()}/suggestions", None),
        ):
            response = await api.request(method, path, headers=headers[0], json=payload)
            assert response.status_code == 404, (path, response.text)
        # A fully valid foreign receipt must also fail at the task/source boundary.
        png, prepared = await screenshots.prepare(
            _rgb_png(2, 2, [(10, 20, 30)] * 4),
            UploadSource(
                kind="upload",
                task_feature_id=UUID(feature["id"]),
                image_kind="diagram",
                source_label="Synthetic",
                software_version="1",
                environment="test",
            ),
            ImagePlan(),
        )
        body = ScreenshotIngest(
            extraction_job_id=UUID(extraction),
            prepared=prepared,
            reviewed_upload_sha256=prepared.image.sha256,
            idempotency_key=uuid4(),
        )
        denied = await api.post(
            f"/tasks/{task}/screenshots",
            headers=headers[0],
            files={"file": ("safe.png", png, "image/png"), "input": (None, body.model_dump_json())},
        )
        assert denied.status_code == 404
        artifact = Path("data/work/screenshots/api-isolation.json")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            artifact.write_text,
            json.dumps(
                {
                    "task_id": task,
                    "asset_id": asset["id"],
                    "image_sha256": rendition["image"]["sha256"],
                    "foreign_access": "rejected",
                },
                indent=2,
            ),
        )


async def test_image_jobs_preview_download_derivation_and_withdrawal(tenants, tmp_path):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        task, _, extraction, _ = await create_tender(
            api, app, header, tmp_path, suffix="render-job"
        )
        feature = await selected_feature(api, header, task)
        stored = await ingest_synthetic_screenshot(api, header, task, extraction, feature["id"])
        asset, root = stored["asset"], stored["rendition"]
        link = await api.post(f"/screenshot-renditions/{root['id']}/preview-link", headers=header)
        assert link.status_code == 200
        path = link.json()["data"]["url"]
        download = await api.get(path, headers=header)
        assert download.status_code == 200 and download.headers["cache-control"] == "no-store"
        assert hashlib.sha256(download.content).hexdigest() == root["image"]["sha256"]
        plan = {
            "parent_rendition_id": root["id"],
            "expected_image_sha256": root["image"]["sha256"],
            "plan": {
                "crop": {"x": 0, "y": 0, "width": 16, "height": 10},
                "redact": [{"x": 2, "y": 2, "width": 3, "height": 3}],
            },
        }
        preview = await api.post(
            f"/screenshots/{asset['id']}/renditions", headers=header, json=plan | {"dry_run": True}
        )
        assert preview.status_code == 200 and preview.json()["data"]["estimated_charge"] == 0
        response = await api.post(
            f"/screenshots/{asset['id']}/renditions", headers=header, json=plan
        )
        assert response.status_code == 200, response.text
        job_id = response.json()["data"]["job_id"]
        await app.state.processor(header["X-Org-Id"], job_id)
        completed = (await api.get(f"/jobs/{job_id}", headers=header)).json()["data"]
        assert completed["status"] == "succeeded", completed
        derived = completed["result"]["rendition"]
        assert (
            derived["privacy_review_id"] == root["privacy_review_id"]
            and derived["parent_rendition_id"] == root["id"]
        )
        assert derived["image"]["sha256"] != root["image"]["sha256"]
        assert completed["result"]["cost"] == {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0}
        revoked = await api.post(
            f"/screenshots/{asset['id']}/withdrawals",
            headers=header,
            json={"reason": "Synthetic privacy omission"},
        )
        assert revoked.status_code == 200
        assert (await api.get(path, headers=header)).status_code == 409
        assert (
            await api.post(f"/screenshot-renditions/{derived['id']}/preview-link", headers=header)
        ).status_code == 409
        artifact = Path("data/work/screenshots/render-job.json")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            artifact.write_text,
            json.dumps({"root": root, "derived": derived, "job_id": job_id}, indent=2),
        )
