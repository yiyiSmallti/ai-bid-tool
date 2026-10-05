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
- Reopened cards and revoked initiators invalidate cached pages just like signed downloads,
  including changes during conversion or the read before the gate acquires its locks.
- Previews call no model provider and record no usage.
- Untrusted PDF opens happen only in the resource-limited child; child protocol, deadline
  and resource failures publish no converter output or page image.
Set BID_CONVERTER_LIVE_URL to also convert the released DOCX with a real Gotenberg.
"""

import hashlib
import os
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pymupdf
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.models.entities import Job, Membership, UsageRecord
from conftest import FakeQueue
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_exports import complete_inputs, draft, prepared, setup_template
from test_response_cards import (
    PhaseOneExtraction,
    create_tender,
    login,
    require_action,
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


@pytest.mark.parametrize("invalidation", ["completed", "converting", "reading", "initiator"])
async def test_cached_final_preview_obeys_live_download_gate(
    tenants, tmp_path, admin_engine, monkeypatch, invalidation
):
    fake = FakeConverter()
    fake.reply = two_page_pdf()
    async with preview_client(tenants, tmp_path, "http://converter.test") as (api, app, headers):
        app.state.processor.converter_transport = httpx.MockTransport(fake.handler)
        header = headers[0]
        task, body, reviewed, _ = await complete_inputs(
            api, app, header, tenants, admin_engine, tmp_path
        )
        run = await prepared(api, app, header, task, body)
        released = await api.post(
            f"/export-runs/{run['id']}/release",
            headers=header,
            json={
                "expected_input_hash": run["input_hash"],
                "expected_candidate_sha256": run["candidate_sha256"],
            },
        )
        assert released.status_code == 200, released.text
        export = released.json()["data"]
        assert export["mode"] == "final_section" and export["completion"] == "complete"
        base = f"/exports/{export['id']}"
        link = await api.get(f"{base}/download-link", headers=header)
        assert link.status_code == 200, link.text
        download_url = link.json()["data"]["url"]
        assert (await api.get(download_url, headers=header)).status_code == 200
        opened = await api.post(f"{base}/preview", headers=header)
        assert opened.status_code == 200, opened.text
        job_id = opened.json()["data"]["job_id"]

        async def reopen():
            await require_action(
                api, header, reviewed[1], "reopen", reason="Synthetic preview invalidation."
            )

        if invalidation == "converting":

            async def convert_after_reopen(request):
                await reopen()
                return fake.handler(request)

            app.state.processor.converter_transport = httpx.MockTransport(convert_after_reopen)
        await app.state.processor(header["X-Org-Id"], job_id)
        job = await api.get(f"/jobs/{job_id}", headers=header)
        assert job.json()["data"]["status"] == "succeeded", job.text
        if invalidation != "converting":
            ready = await api.get(f"{base}/preview", headers=header)
            assert ready.json()["data"]["status"] == "succeeded", ready.text
            assert is_png(await api.get(f"{base}/preview/pages/1", headers=header))

        if invalidation == "completed":
            await reopen()
        elif invalidation == "initiator":
            # A different active bidder keeps access while the original initiator is revoked.
            with Session(admin_engine) as session, session.begin():
                session.add(
                    Membership(
                        org_id=tenants["orgs"][0], user_id=tenants["users"][1], role="bidder"
                    )
                )
            set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "viewer")
            header = {**headers[1], "X-Org-Id": headers[0]["X-Org-Id"]}
        elif invalidation == "reading":
            read = app.state.storage.read
            reopened = False

            async def read_then_reopen(org_id, key):
                nonlocal reopened
                content = await read(org_id, key)
                if not reopened and "/exports/" in key:
                    reopened = True
                    await reopen()
                return content

            monkeypatch.setattr(app.state.storage, "read", read_then_reopen)

        page = await api.get(f"{base}/preview/pages/1", headers=header)
        if invalidation == "reading":
            assert reopened, "The preview must recheck the released DOCX before serving pages"
        download = await api.get(download_url, headers=header)
        assert download.status_code == 409, download.text
        assert page.status_code == download.status_code, page.text
        expected_error = download.json()["data"]["error"]
        assert expected_error["code"] == "export_input_changed"
        assert page.json()["data"]["error"] == expected_error
        for path in (f"{base}/download-link", f"{base}/preview"):
            refused = await api.request(
                "POST" if path.endswith("/preview") else "GET", path, headers=header
            )
            assert refused.status_code == download.status_code, refused.text
            assert refused.json()["data"]["error"] == expected_error
        shown = await api.get(base, headers=header)
        status = await api.get(f"{base}/preview", headers=header)
        assert shown.status_code == status.status_code == 200
        view, preview = shown.json()["data"], status.json()["data"]
        assert preview["status"] == "invalidated" and preview["page_count"] is None
        assert preview["validity"] == view["validity"] == "stale"
        assert preview["issues"] == view["issues"]
        assert preview["invalidated_requirement_ids"] == view["invalidated_requirement_ids"]
