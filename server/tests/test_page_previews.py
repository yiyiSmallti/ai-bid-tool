"""Page previews of tender, certificate and export files through the real API and worker.

Failure modes enumerated before implementation:
- Another org's or an unknown document, certificate revision, export or job is a 404 on
  every preview route; export previews are only for the people who may download it.
- A page outside the file, a Word original or an oversized zoom is refused, not guessed.
- Stored bytes that no longer match their hash are never rendered.
- A missing, failing or lying converter never publishes a partial or unreadable PDF; the
  failed job stays visible and only an explicit retry requeues it.
- Repeated opens reuse one conversion per export file; pages before success are refused.
- The generic job reader neither leaks the stored PDF location nor bypasses export access.
- Previews call no model provider and record no usage.
Set BID_CONVERTER_LIVE_URL to also convert the released DOCX with a real Gotenberg.
"""

import hashlib
import os
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pymupdf
from app.api.main import create_app
from app.core.config import Settings
from app.models.entities import Job, UsageRecord
from conftest import FakeQueue
from sqlalchemy import func, select
from test_exports import draft, prepared, setup_template
from test_response_cards import (
    PhaseOneExtraction,
    create_tender,
    login,
    select_real_materials,
    set_role,
)

PNG = b"\x89PNG\r\n\x1a\n"
LIVE_URL = os.environ.get("BID_CONVERTER_LIVE_URL")


def two_page_pdf() -> bytes:
    with pymupdf.open() as pdf:
        for text in ("Synthetic preview page one", "Synthetic preview page two"):
            pdf.new_page().insert_text((72, 72), text)
        return pdf.tobytes()


class FakeConverter:
    def __init__(self):
        self.received: list[bytes] = []
        self.reply = b"not a pdf"

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/forms/libreoffice/convert"
        boundary = request.headers["content-type"].split("boundary=")[1].encode()
        part = request.content.split(b"--" + boundary)[1]
        self.received.append(part.split(b"\r\n\r\n", 1)[1].removesuffix(b"\r\n"))
        return httpx.Response(200, content=self.reply, headers={"Content-Type": "application/pdf"})


@asynccontextmanager
async def preview_client(tenants, tmp_path, converter_url):
    app = create_app(
        Settings(data_dir=tmp_path, converter_url=converter_url),
        llm=PhaseOneExtraction(),
        queue=FakeQueue(),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        headers = [
            await login(api, org, label)
            for org, label in zip(tenants["orgs"], ("a", "b"), strict=True)
        ]
        yield api, app, headers


def is_png(response: httpx.Response) -> bool:
    return (
        response.status_code == 200
        and response.headers["content-type"] == "image/png"
        and response.content.startswith(PNG)
    )


async def released_export(api, app, header, task, extraction, tenants, admin_engine):
    selected, binding = await setup_template(api, header, task)
    set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
    draft_id = await draft(api, app, header, task, extraction)
    run = await prepared(
        api,
        app,
        header,
        task,
        {
            "draft_id": draft_id,
            "task_template_id": selected["id"],
            "binding_id": binding["id"],
            "mode": "review_copy",
        },
    )
    released = await api.post(
        f"/export-runs/{run['id']}/release",
        headers=header,
        json={
            "expected_input_hash": run["input_hash"],
            "expected_candidate_sha256": run["candidate_sha256"],
        },
    )
    assert released.status_code == 200, released.text
    return released.json()["data"]


async def test_previews_are_bounded_isolated_and_publish_only_readable_pdfs(
    tenants, tmp_path, admin_engine
):
    fake = FakeConverter()
    async with preview_client(tenants, tmp_path, LIVE_URL or "http://converter.test") as (
        api,
        app,
        headers,
    ):
        if not LIVE_URL:
            app.state.processor.converter_transport = httpx.MockTransport(fake.handler)
        header, foreign = headers
        task, document, extraction, _ = await create_tender(api, app, header, tmp_path)
        _, _, _, _, source = await select_real_materials(api, header, task, tmp_path)
        revision = source["certificate_revision_id"]

        # Tender and certificate originals render page by page for anyone who may read them.
        page_routes = [
            f"/documents/{document}/pages/{{page}}/preview",
            f"/resources/certificates/revisions/{revision}/file/pages/{{page}}/preview",
        ]
        for route in page_routes:
            assert is_png(await api.get(route.format(page=1), headers=header))
            assert is_png(await api.get(route.format(page=1), headers=header, params={"zoom": 2}))
            assert (await api.get(route.format(page=99), headers=header)).status_code == 400
            assert (await api.get(route.format(page=0), headers=header)).status_code == 400
            zoom = await api.get(route.format(page=1), headers=header, params={"zoom": 3})
            assert zoom.status_code == 422
            assert (await api.get(route.format(page=1), headers=foreign)).status_code == 404
        unknown = await api.get(f"/documents/{UUID(int=7)}/pages/1/preview", headers=header)
        assert unknown.status_code == 404

        export = await released_export(api, app, header, task, extraction, tenants, admin_engine)
        base = f"/exports/{export['id']}/preview"
        before = await api.get(base, headers=header)
        assert before.json()["data"]["status"] == "not_requested"
        opened = await api.post(base, headers=header)
        assert opened.status_code == 200, opened.text
        job_id = opened.json()["data"]["job_id"]
        assert opened.json()["data"]["status"] == "queued"
        again = await api.post(base, headers=header)
        assert again.json()["data"]["job_id"] == job_id
        assert (await api.get(f"{base}/pages/1", headers=header)).status_code == 409

        # A foreign member with the right role still cannot tell the export exists.
        set_role(admin_engine, tenants["orgs"][1], tenants["users"][1], "bidder")
        for method, path in [
            ("POST", base),
            ("GET", base),
            ("GET", f"{base}/pages/1"),
            ("GET", f"/jobs/{job_id}"),
            ("POST", f"/jobs/{job_id}/cancel"),
        ]:
            response = await api.request(method, path, headers=foreign)
            assert response.status_code == 404, (method, path, response.text)

        if not LIVE_URL:
            # An unreadable reply fails the job and publishes nothing.
            await app.state.processor(header["X-Org-Id"], job_id)
            failed = await api.get(base, headers=header)
            assert failed.json()["data"]["status"] == "failed"
            assert failed.json()["data"]["error"]["code"] == "converter_invalid_output"
            assert (await api.get(f"{base}/pages/1", headers=header)).status_code == 409
            reused = await api.post(base, headers=header)
            assert reused.json()["data"]["status"] == "failed"
            retried = await api.post(base, headers=header, params={"retry": "true"})
            assert retried.json()["data"]["job_id"] == job_id
            assert retried.json()["data"]["status"] == "queued"
            fake.reply = two_page_pdf()

        await app.state.processor(header["X-Org-Id"], job_id)
        ready = (await api.get(base, headers=header)).json()["data"]
        assert ready["status"] == "succeeded", ready
        assert ready["page_count"] >= (1 if LIVE_URL else 2)
        assert is_png(await api.get(f"{base}/pages/1", headers=header))
        assert is_png(await api.get(f"{base}/pages/{ready['page_count']}", headers=header))
        overflow = await api.get(f"{base}/pages/{ready['page_count'] + 1}", headers=header)
        assert overflow.status_code == 400
        if not LIVE_URL:
            assert [hashlib.sha256(body).hexdigest() for body in fake.received] == [
                export["file"]["sha256"]
            ] * 2

        status = await api.get(f"/jobs/{job_id}", headers=header)
        assert status.status_code == 200, status.text
        public = status.json()["data"]["result"]
        assert "submission" not in public
        assert public["preview"] == {"page_count": ready["page_count"]}
        for role in ("admin", "technical", "viewer"):
            set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], role)
            for method, path in [
                ("POST", base),
                ("GET", f"{base}/pages/1"),
                ("GET", f"/jobs/{job_id}"),
            ]:
                response = await api.request(method, path, headers=header)
                assert response.status_code == 403, (role, method, path, response.text)

        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            job = await session.get(Job, UUID(job_id))
            assert job is not None and job.kind == "export_preview"
            usage = await session.scalar(
                select(func.count()).select_from(UsageRecord).where(UsageRecord.job_id == job.id)
            )
            assert usage == 0
