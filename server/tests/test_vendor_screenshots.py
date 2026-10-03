"""Vendor web and whitepaper captures become image evidence through the real API.

Failure modes enumerated before implementation:
- a manifest-only web capture retains no page bytes and must not become an archive;
- a client could name a rendered DOM, the wrong capture kind, another task's run or
  another organization's artifact as its source image;
- the uploaded PNG could differ from the server's replay of the captured page and plan;
- the human review could name a different archive than the one retained;
- URLs, titles and capture time must come from the proxy receipts, not the client, and
  a redirect must resolve to the final response actually archived;
- two pages of one whitepaper must share one archive record instead of duplicating it;
- vendor images may support only hardware documentation and need the model/scope
  review before confirmation;
- a replaced product selection or altered archive bytes must stop further use.

Vendors are httpx.MockTransport responses and the sandbox is the test-only FakeBrowser;
real isolation is accepted separately. The Rust renderer is a genuine dependency.
"""

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.models.screenshots import ScreenshotVendorArchive
from app.providers.browser import ArtifactPayload, ExecutionResult
from app.providers.sandbox_fetch import FetchDenied
from app.schemas.screenshot_contracts import (
    ImagePlan,
    PixelRect,
    ScreenshotIngest,
    VendorSource,
)
from app.services import screenshots
from sandbox_fakes import synthetic_png
from sqlalchemy import select, update
from test_prototype_decisions import renderer_required  # pyright: ignore[reportMissingImports]
from test_response_cards import (  # pyright: ignore[reportMissingImports]
    PhaseOneExtraction,
    create_card,
    create_tender,
    labelled_pdf,
    login,
    require_action,
    set_role,
)
from test_sandbox import FakeBrowser, SandboxQueue  # pyright: ignore[reportMissingImports]
from test_screenshot_cards import run_draft  # pyright: ignore[reportMissingImports]
from test_screenshot_renderer import _rgb_png  # pyright: ignore[reportMissingImports]

WEB_URL = "https://vendor.example/m1"
FINAL_URL = "https://www.vendor.example/products/m1"
PDF_URL = "https://vendor.example/m1-whitepaper.pdf"
MISSING_URL = "https://vendor.example/missing.css"
PAGE = (
    b"<!doctype html><html><head><meta charset='utf-8'>"
    b"<title>Synthetic M1 \xe8\xa7\x84\xe6\xa0\xbc</title></head>"
    b"<body><p>Synthetic vendor page: 64 GB memory.</p></body></html>"
)
WHITEPAPER = labelled_pdf(["SYNTHETIC whitepaper page 1", "SYNTHETIC whitepaper page 2"])


def vendor_response(request: httpx.Request) -> httpx.Response:
    # The broker connects to the pinned address and keeps the original Host header.
    url = f"https://{request.headers['host']}{request.url.raw_path.decode()}"
    if url == WEB_URL:
        return httpx.Response(301, headers={"location": FINAL_URL})
    if url == FINAL_URL:
        return httpx.Response(200, content=PAGE, headers={"content-type": "text/html"})
    if url == PDF_URL:
        return httpx.Response(200, content=WHITEPAPER, headers={"content-type": "application/pdf"})
    return httpx.Response(404)


class VendorBrowser(FakeBrowser):
    """Fetches through the real broker and returns synthetic page pixels."""

    async def capture(self, descriptor, source_url, fetcher):
        self.calls += 1
        fetched = await fetcher.fetch(source_url)  # the broker follows redirects itself
        if descriptor.format == "web":
            # A failed stylesheet makes the capture incomplete, not unusable.
            try:
                await fetcher.fetch(MISSING_URL)
            except FetchDenied:
                result = self.output("capture_png", fetched.body, descriptor)
                return replace(result, issues=("vendor_resources_incomplete",))
            return self.output("capture_png", fetched.body, descriptor)
        pages = tuple(
            ArtifactPayload(
                kind="pdf_page_png",
                data=synthetic_png(60 + page, 80),
                width=60 + page,
                height=80,
                page=page,
            )
            for page in descriptor.pdf_pages
        )
        return ExecutionResult(
            artifacts=(ArtifactPayload(kind="source_pdf", data=fetched.body), *pages),
            metrics={
                "wall_ms": 10,
                "cpu_ms": 3,
                "peak_memory_bytes": 1000,
                "input_bytes": len(fetched.body),
                "network_bytes": 0,
                "output_bytes": len(fetched.body),
                "request_count": 0,
            },
            runtime_versions={"image": "sha256:" + "f" * 64, "browser": "test-only"},
            cleanup_state="complete",
            descriptor_hash=descriptor.digest,
        )


@asynccontextmanager
async def vendor_client(tenants, tmp_path: Path, monkeypatch, *, live=False):
    policy = tmp_path / "policy.json"
    urls = (WEB_URL, FINAL_URL, PDF_URL, MISSING_URL)
    rules = [{"url": url, "methods": ["GET", "HEAD"]} for url in urls]
    exact = {"revision": "synthetic-v1", "main_urls": [WEB_URL, PDF_URL], "rules": rules}
    opened = {"revision": "dev-open-v1", "open_public_https": True}
    policy.write_text(
        json.dumps({"policies": [opened if live else exact], "revoked_revisions": []})
    )
    monkeypatch.setenv("BID_SANDBOX_POLICY_FILE", str(policy))
    monkeypatch.setenv("BID_SANDBOX_FETCH_QUOTA", str(tmp_path / "quota.sqlite"))
    app = create_app(
        Settings(data_dir=tmp_path / "objects"), llm=PhaseOneExtraction(), queue=SandboxQueue()
    )
    if live:
        from app.providers.browser import create_browser_provider

        monkeypatch.setenv("BID_SANDBOX_DEV_OPEN_EGRESS", "1")
        app.state.processor.sandbox_browser = create_browser_provider()
    else:
        app.state.processor.sandbox_browser = VendorBrowser()
        app.state.processor.sandbox_fetch_transport = httpx.MockTransport(vendor_response)

        async def resolver(host):
            return ("93.184.216.34",)

        app.state.processor.sandbox_resolver = resolver
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        headers = [
            await login(api, org, label)
            for org, label in zip(tenants["orgs"], ("a", "b"), strict=True)
        ]
        yield api, app, headers


async def select_product(api, header, task, web_url=WEB_URL, pdf_url=PDF_URL):
    product = (
        await api.post(
            "/resources/products",
            headers=header,
            json={
                "data": {
                    "name": "Synthetic M1",
                    "vendor": "Synthetic vendor",
                    "model": "M1",
                    "official_url": web_url,
                    "whitepaper_url": pdf_url,
                }
            },
        )
    ).json()["data"]
    selected = await api.post(
        f"/tasks/{task}/products", headers=header, json={"product_id": product["product_id"]}
    )
    assert selected.status_code == 200, selected.text
    return product, selected.json()["data"]


async def capture(api, app, header, task, extraction, product, selection, **spec):
    field = "whitepaper_url" if spec.get("format") == "pdf" else "official_url"
    url = product["data"][field]
    body = {
        "purpose": "vendor_capture",
        "extraction_job_id": extraction,
        "task_resource_id": selection["id"],
        "expected_product_revision_id": product["id"],
        "source_field": field,
        "expected_source_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
        "format": "web",
        "capture_key": str(uuid4()),
        **spec,
    }
    preview = await api.post(
        f"/tasks/{task}/sandbox-runs", headers=header, json={"spec": body, "dry_run": True}
    )
    assert preview.status_code == 200 and preview.json()["data"]["ready"], preview.text
    submitted = await api.post(
        f"/tasks/{task}/sandbox-runs",
        headers=header,
        json={"spec": body, "expected_request_hash": preview.json()["data"]["request_hash"]},
    )
    assert submitted.status_code == 200, submitted.text
    run = submitted.json()["data"]
    await app.state.processor(header["X-Org-Id"], run["job_id"])
    shown = (await api.get(f"/sandbox-runs/{run['id']}", headers=header)).json()["data"]
    job = (await api.get(f"/jobs/{run['job_id']}", headers=header)).json()["data"]
    assert shown["state"] == "succeeded", (shown["issues"], shown["cleanup_state"], job)
    return {artifact["kind"]: artifact for artifact in shown["artifacts"]}, shown


async def download(api, header, artifact):
    link = (
        await api.get(f"/sandbox-artifacts/{artifact['id']}/download-link", headers=header)
    ).json()["data"]["url"]
    content = (await api.get(link, headers=header)).content
    assert hashlib.sha256(content).hexdigest() == artifact["sha256"]
    return content


async def ingest(api, header, task, extraction, source, png, plan, archive_sha256):
    prepared_png, receipt = await screenshots.prepare(png, source, plan)
    body = ScreenshotIngest(
        extraction_job_id=extraction,
        prepared=receipt,
        reviewed_upload_sha256=receipt.image.sha256,
        reviewed_archive_sha256=archive_sha256,
        idempotency_key=uuid4(),
    )
    return await api.post(
        f"/tasks/{task}/screenshots",
        headers=header,
        files={
            "file": ("SYNTHETIC-VENDOR.png", prepared_png, "image/png"),
            "input": (None, body.model_dump_json(), "application/json"),
        },
    )


def code(response):
    return response.json()["data"]["error"]["code"]


async def test_vendor_web_and_pdf_capture_to_confirmed_draft(
    tenants, tmp_path, admin_engine, monkeypatch
):
    renderer_required()
    org = tenants["orgs"][0]
    async with vendor_client(tenants, tmp_path, monkeypatch) as (api, app, headers):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(api, app, header, tmp_path)
        product, selection = await select_product(api, header, task)

        # Manifest-only captures keep receipts but not page bytes; they cannot be archived.
        manifest_only, _ = await capture(api, app, header, task, extraction, product, selection)
        thin = VendorSource(
            kind="vendor_web", sandbox_artifact_id=manifest_only["capture_png"]["id"]
        )
        thin_png = await download(api, header, manifest_only["capture_png"])
        refused = await ingest(
            api, header, task, UUID(extraction), thin, thin_png, ImagePlan(), "0" * 64
        )
        assert refused.status_code == 409 and code(refused) == "vendor_archive_required"

        web, web_run = await capture(
            api, app, header, task, extraction, product, selection, archive="bundle"
        )
        page_png = await download(api, header, web["capture_png"])
        source = VendorSource(kind="vendor_web", sandbox_artifact_id=web["capture_png"]["id"])
        plan = ImagePlan(redact=[PixelRect(x=0, y=0, width=40, height=20)])
        archive_sha = web["capture_archive"]["sha256"]

        wrong_kind = VendorSource(kind="vendor_pdf", sandbox_artifact_id=source.sandbox_artifact_id)
        response = await ingest(
            api, header, task, UUID(extraction), wrong_kind, page_png, plan, archive_sha
        )
        assert response.status_code == 400 and code(response) == "vendor_source_mismatch"
        dom = VendorSource(kind="vendor_web", sandbox_artifact_id=web["rendered_html"]["id"])
        response = await ingest(
            api, header, task, UUID(extraction), dom, page_png, plan, archive_sha
        )
        assert response.status_code == 400 and code(response) == "vendor_source_mismatch"
        response = await ingest(
            api, header, task, UUID(extraction), source, synthetic_png(1440, 899), plan, archive_sha
        )
        assert response.status_code == 409 and code(response) == "source_image_mismatch"
        # A receipt that names the real source hash but carries other pixels fails the replay.
        other = _rgb_png(1440, 900, [(200, 30, 30)] * (1440 * 900))
        forged_png, forged = await screenshots.prepare(other, source, plan)
        forged_body = ScreenshotIngest(
            extraction_job_id=UUID(extraction),
            prepared=forged.model_copy(update={"source_sha256": web["capture_png"]["sha256"]}),
            reviewed_upload_sha256=forged.image.sha256,
            reviewed_archive_sha256=archive_sha,
            idempotency_key=uuid4(),
        )
        response = await api.post(
            f"/tasks/{task}/screenshots",
            headers=header,
            files={
                "file": ("SYNTHETIC-VENDOR.png", forged_png, "image/png"),
                "input": (None, forged_body.model_dump_json(), "application/json"),
            },
        )
        assert response.status_code == 409 and code(response) == "source_image_mismatch"
        response = await ingest(
            api, header, task, UUID(extraction), source, page_png, plan, "0" * 64
        )
        assert response.status_code == 409 and code(response) == "archive_hash_mismatch"
        foreign = await ingest(
            api, headers[1], task, UUID(extraction), source, page_png, plan, archive_sha
        )
        assert foreign.status_code == 404, foreign.text

        accepted = await ingest(
            api, header, task, UUID(extraction), source, page_png, plan, archive_sha
        )
        assert accepted.status_code == 200, accepted.text
        asset = accepted.json()["data"]["asset"]
        archive = asset["vendor_archive"]
        assert asset["origin"] == "vendor" and asset["image_kind"] == "vendor_page"
        assert asset["source"] == source.model_dump(mode="json")
        assert asset["source_hash_assurance"] == "server_verified"
        assert asset["source_sha256"] == web["capture_png"]["sha256"]
        assert archive["sandbox_run_id"] == web_run["id"]
        assert archive["format"] == "web" and archive["source_field"] == "official_url"
        assert archive["title"] == "Synthetic M1 规格"
        assert archive["final_origin"] == "https://www.vendor.example"
        assert archive["source_url_sha256"] == hashlib.sha256(WEB_URL.encode()).hexdigest()
        assert archive["final_url_sha256"] == hashlib.sha256(FINAL_URL.encode()).hexdigest()
        assert archive["content_sha256"] == hashlib.sha256(PAGE).hexdigest()
        assert archive["archive"]["sha256"] == archive_sha
        assert archive["archive"]["media_type"] == "application/zip"
        assert archive["incomplete"] is True and archive["failed_request_count"] == 1
        assert "vendor.example/m1" not in json.dumps(asset)
        rendition = accepted.json()["data"]["rendition"]
        assert rendition["profile"] == "screenshot-markup-v1"

        pdf, pdf_run = await capture(
            api, app, header, task, extraction, product, selection, format="pdf", pdf_pages=[1, 2]
        )
        page_ids = {}
        for artifact in [a for a in pdf_run["artifacts"] if a["kind"] == "pdf_page_png"]:
            png = await download(api, header, artifact)
            page = VendorSource(kind="vendor_pdf", sandbox_artifact_id=artifact["id"])
            response = await ingest(
                api,
                header,
                task,
                UUID(extraction),
                page,
                png,
                ImagePlan(),
                pdf["source_pdf"]["sha256"],
            )
            assert response.status_code == 200, response.text
            page_ids[artifact["page"]] = response.json()["data"]["asset"]
        assert page_ids[1]["vendor_archive_id"] == page_ids[2]["vendor_archive_id"]
        whitepaper = page_ids[2]["vendor_archive"]
        assert whitepaper["format"] == "pdf" and whitepaper["title"] is None
        assert whitepaper["incomplete"] is False and whitepaper["failed_request_count"] == 0
        assert whitepaper["archive"]["media_type"] == "application/pdf"
        assert whitepaper["content_sha256"] == hashlib.sha256(WHITEPAPER).hexdigest()
        async with app.state.db.transaction(org) as session:
            archives = (await session.scalars(select(ScreenshotVendorArchive))).all()
            assert len(archives) == 2

        evidence = {
            "kind": "image_region",
            "asset_id": asset["id"],
            "rendition_id": rendition["id"],
            "expected_image_sha256": rendition["image"]["sha256"],
            "region": {"x": 50, "y": 40, "width": 200, "height": 60},
            "claim_scope": "hardware_documentation",
            "visual_observation": "The vendor page lists 64 GB memory for model M1.",
        }
        content = {
            "response_kind": "evidence",
            "response_text": "The selected M1 model provides 64 GB memory.",
            "deviation": "none",
            "deviation_note": "The archived vendor page states the memory size.",
            "evidence": [evidence],
        }
        misuse = await api.post(
            f"/tasks/{task}/cards",
            headers=header,
            json={
                "extraction_job_id": extraction,
                "requirement_id": requirements[2]["id"],
                "content": content
                | {"evidence": [evidence | {"claim_scope": "functional_observation"}]},
            },
        )
        assert misuse.status_code == 400 and code(misuse) == "invalid_image_claim"
        card = await create_card(api, header, task, extraction, requirements[2], content)
        set_role(admin_engine, org, tenants["users"][0], "technical")
        pending = await require_action(api, header, card, "submit")
        assert {"vendor_model_scope", "vendor_capture_incomplete"} <= set(pending["warning_codes"])
        confirmed = await require_action(
            api,
            header,
            pending,
            "confirm",
            reviewed_evidence_ids=[pending["evidence"][0]["id"]],
            reviewed_warning_codes=pending["warning_codes"],
            reason="Checked the vendor, exact model and the page scope of the memory claim.",
        )
        assert confirmed["evidence"][0]["material_kind"] == "vendor_web"
        draft = await run_draft(api, app, header, task, extraction)
        row = next(
            row
            for rows in draft["tables"].values()
            for row in rows
            if row["requirement_id"] == requirements[2]["id"]
        )
        assert row["evidence"][0]["image_sha256"] == rendition["image"]["sha256"]

        # Altered retained bytes stop any further use of the image.
        async with app.state.db.transaction(org) as session:
            stored = await session.get(ScreenshotVendorArchive, UUID(archive["id"]))
            assert stored is not None
            key = stored.storage_key
        path = app.state.storage.path(org, key)
        await asyncio.to_thread(path.write_bytes, path.read_bytes()[:-8] + b"tampered")
        broken = await api.post(
            f"/tasks/{task}/cards",
            headers=header,
            json={
                "extraction_job_id": extraction,
                "requirement_id": requirements[3]["id"],
                "content": content,
            },
        )
        assert broken.status_code == 409 and code(broken) == "vendor_provenance_integrity"

        # Replacing the product selection stops new ingests of its captures.
        from app.models.entities import TaskResource

        async with app.state.db.transaction(org) as session:
            await session.execute(
                update(TaskResource)
                .where(TaskResource.id == UUID(selection["id"]))
                .values(active=False)
            )
        stale = await ingest(
            api, header, task, UUID(extraction), source, page_png, ImagePlan(), archive_sha
        )
        assert stale.status_code == 409, stale.text

        artifact_dir = Path("data/work/vendor-screenshots")
        await asyncio.to_thread(artifact_dir.mkdir, parents=True, exist_ok=True)
        (artifact_dir / "vendor-chain.json").write_text(
            json.dumps(
                {
                    "web_asset": asset,
                    "pdf_assets": page_ids,
                    "card": confirmed["id"],
                    "draft_row": row,
                },
                sort_keys=True,
                ensure_ascii=False,
                indent=2,
            )
        )


@pytest.mark.skipif(
    os.environ.get("BID_SANDBOX_RUNTIME_TEST") != "1"
    or not (os.environ.get("BID_VENDOR_LIVE_WEB_URL") or os.environ.get("BID_VENDOR_LIVE_PDF_URL")),
    reason="requires the sandbox supervisor and an explicit public vendor URL",
)
async def test_real_sandbox_captures_a_public_vendor_page_and_pdf(
    tenants, tmp_path, admin_engine, monkeypatch
):
    """Development acceptance over the open egress policy; never runs in CI."""
    renderer_required()
    web_url = os.environ.get("BID_VENDOR_LIVE_WEB_URL") or WEB_URL
    pdf_url = os.environ.get("BID_VENDOR_LIVE_PDF_URL") or PDF_URL
    async with vendor_client(tenants, tmp_path, monkeypatch, live=True) as (api, app, headers):
        header = headers[0]
        task, _, extraction, _ = await create_tender(api, app, header, tmp_path)
        product, selection = await select_product(api, header, task, web_url, pdf_url)
        target = Path(os.environ.get("BID_VENDOR_LIVE_DIR", "data/work/vendor-live"))
        await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        results = {}
        specs = [{"archive": "bundle"}] if os.environ.get("BID_VENDOR_LIVE_WEB_URL") else []
        if os.environ.get("BID_VENDOR_LIVE_PDF_URL"):
            specs.append({"format": "pdf", "pdf_pages": [1]})
        for spec in specs:
            artifacts, run = await capture(
                api, app, header, task, extraction, product, selection, **spec
            )
            page = artifacts.get("capture_png") or artifacts["pdf_page_png"]
            png = await download(api, header, page)
            kind = "vendor_pdf" if "pdf_page_png" in artifacts else "vendor_web"
            archive = artifacts.get("capture_archive") or artifacts["source_pdf"]
            response = await ingest(
                api,
                header,
                task,
                UUID(extraction),
                VendorSource(kind=kind, sandbox_artifact_id=page["id"]),
                png,
                ImagePlan(),
                archive["sha256"],
            )
            assert response.status_code == 200, response.text
            asset = response.json()["data"]["asset"]
            (target / f"{kind}.png").write_bytes(png)
            results[kind] = {
                "vendor_archive": asset["vendor_archive"],
                "requests": run["metrics"]["request_count"],
                "network_bytes": run["metrics"]["network_bytes"],
            }
        (target / "live-capture.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True)
        )
