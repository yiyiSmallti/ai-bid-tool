"""API/processor gate tests; vendors are synthetic and runtime is explicitly fake.

Failure inventory before implementation:
- Cross-task/document/extraction/selection/revision bindings.
- Missing success extraction, hash mismatch, non-UTF8 HTML, forged/extra input fields.
- Dry-run writes/dispatch, idempotency conflict, repeated capture causing new fetch.
- Revoked token, changed scopes/selection/policy while queued/running/downloading.
- Cancellation/lease takeover, stale attempt publication, failed cleanup, oversized output.
- Mutable archived inputs/artifacts/receipts, forged parent chain, missing manifest.
- Signed-link substitution, stale selection, raw HTML serving in application origin.
- Raw HTML/query/header canaries in jobs/audit/errors; duplicate model usage records.
- Database actor gates, immutable history and mixed-parent writes.
These fake-runtime tests prove integration gates only. Opt-in runtime tests own isolation.
"""

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.models.entities import AuditLog, UsageRecord
from app.providers.browser import ArtifactPayload, ExecutionResult
from conftest import FakeQueue
from fakes import FakeLLM
from sandbox_fakes import synthetic_png
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from test_api import create_document, run_job
from test_features import feature

TABLES = (
    "sandbox_inputs",
    "sandbox_runs",
    "sandbox_attempts",
    "sandbox_artifacts",
    "sandbox_fetch_receipts",
)
HTML = b"<!doctype html><html><body><h1>Synthetic project board</h1><p>CANARY_HTML_PRIVATE</p></body></html>"
URL = "https://vendor.example/model?version=fixed"


class FakeBrowser:
    """Lives only in tests. It does not demonstrate any process isolation."""

    available = True
    profile_digest = "f" * 64
    calls = 0
    gate = None
    entered = None
    cleanup_state = "complete"
    failure = None

    async def render_prototype(self, descriptor, html):
        self.calls += 1
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.failure is not None:
            raise self.failure
        return self.output("prototype_png", html, descriptor)

    async def capture(self, descriptor, source_url, fetcher):
        self.calls += 1
        fetched = await fetcher.fetch(source_url)
        return self.output("capture_png", fetched.body, descriptor)

    def output(self, kind, html, descriptor):
        png = synthetic_png(descriptor.viewport_width, descriptor.viewport_height)
        return ExecutionResult(
            artifacts=(
                ArtifactPayload(
                    kind=kind,
                    data=png,
                    width=descriptor.viewport_width,
                    height=descriptor.viewport_height,
                ),
                ArtifactPayload(kind="rendered_html", data=html),
            ),
            metrics={
                "wall_ms": 10,
                "cpu_ms": 3,
                "peak_memory_bytes": 1000,
                "input_bytes": len(html),
                "network_bytes": 0,
                "output_bytes": len(png) + len(html),
                "request_count": 0,
            },
            runtime_versions={"image": "sha256:" + "f" * 64, "browser": "test-only"},
            cleanup_state=self.cleanup_state,
            descriptor_hash=descriptor.digest,
        )


class SandboxQueue(FakeQueue):
    async def enqueue_sandbox(self, job_id: str):
        return await self.enqueue(job_id)


@pytest.fixture
def application(database, tmp_path, monkeypatch):
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "policies": [
                    {
                        "revision": "synthetic-v1",
                        "main_urls": [URL],
                        "rules": [{"url": URL, "methods": ["GET", "HEAD"]}],
                    }
                ],
                "revoked_revisions": [],
            }
        )
    )
    monkeypatch.setenv("BID_SANDBOX_POLICY_FILE", str(policy))
    monkeypatch.setenv("BID_SANDBOX_FETCH_QUOTA", str(tmp_path / "quota.sqlite"))
    app = create_app(Settings(data_dir=tmp_path / "objects"), llm=FakeLLM(), queue=SandboxQueue())
    app.state.processor.sandbox_browser = FakeBrowser()
    app.state.processor.sandbox_fetch_transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=b"<html><body>Public synthetic model.</body></html>",
            headers={"content-type": "text/html"},
        )
    )

    async def resolver(host):
        return ("93.184.216.34",)

    app.state.processor.sandbox_resolver = resolver
    app.state.sandbox_policy_path = policy
    return app


async def prepared(api, application, header, pdf_bytes, *, vendor=False):
    task, document = await create_document(api, header, pdf_bytes)
    await run_job(api, application, header, document, "parse")
    extraction, status = await run_job(api, application, header, document, "extract")
    assert status["status"] == "succeeded"
    if vendor:
        product = (
            await api.post(
                "/resources/products",
                headers=header,
                json={
                    "data": {
                        "name": "Synthetic vendor product",
                        "vendor": "Synthetic",
                        "model": "M1",
                        "official_url": URL,
                    }
                },
            )
        ).json()["data"]
        selected = (
            await api.post(
                f"/tasks/{task}/products",
                headers=header,
                json={"product_id": product["product_id"]},
            )
        ).json()["data"]
        spec = {
            "purpose": "vendor_capture",
            "extraction_job_id": extraction,
            "task_resource_id": selected["id"],
            "expected_product_revision_id": product["id"],
            "source_field": "official_url",
            "expected_source_url_sha256": hashlib.sha256(URL.encode()).hexdigest(),
            "format": "web",
            "capture_key": str(uuid4()),
        }
    else:
        item = await feature(api, header)
        selected = (
            await api.post(
                f"/tasks/{task}/features", headers=header, json={"feature_id": item["feature_id"]}
            )
        ).json()["data"]
        spec = {
            "purpose": "prototype_offline",
            "extraction_job_id": extraction,
            "task_feature_id": selected["id"],
            "expected_feature_revision_id": item["id"],
            "html_sha256": hashlib.sha256(HTML).hexdigest(),
            "html_size_bytes": len(HTML),
        }
    return task, spec


async def post(api, header, task, body, html=HTML):
    if body["spec"]["purpose"] == "prototype_offline":
        return await api.post(
            f"/tasks/{task}/sandbox-runs",
            headers=header,
            data={"submit": json.dumps(body)},
            files={"html": ("prototype.html", html, "text/html; charset=utf-8")},
        )
    return await api.post(f"/tasks/{task}/sandbox-runs", headers=header, json=body)


async def submitted(api, header, task, spec):
    preview = await post(api, header, task, {"spec": spec, "dry_run": True})
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["ready"]
    body = {"spec": spec, "expected_request_hash": preview.json()["data"]["request_hash"]}
    reply = await post(api, header, task, body)
    assert reply.status_code == 200, reply.text
    return reply.json()["data"], body


async def completed(api, application, header, task, spec):
    run, body = await submitted(api, header, task, spec)
    await application.state.processor(run["job_id"])
    shown = await api.get(f"/sandbox-runs/{run['id']}", headers=header)
    assert shown.status_code == 200, shown.text
    final = shown.json()["data"]
    assert final["state"] == "succeeded", final
    return final, body


@pytest.mark.parametrize("vendor", [False, True])
async def test_api_processor_encrypted_transfer_idempotency_and_download(
    api, headers, application, pdf_bytes, tmp_path, vendor
):
    task, spec = await prepared(api, application, headers, pdf_bytes, vendor=vendor)
    before = len(application.state.queue.calls)
    preview = await post(api, headers, task, {"spec": spec, "dry_run": True})
    assert preview.json()["data"]["ready"]
    assert len(application.state.queue.calls) == before
    async with application.state.db.transaction() as session:
        assert await session.scalar(text("SELECT count(*) FROM sandbox_runs")) == 0
        usage_before = await session.scalar(select(func.count()).select_from(UsageRecord))
    final, body = await completed(api, application, headers, task, spec)
    assert final["cleanup_state"] == "complete"
    assert application.state.processor.sandbox_browser.calls == 1
    again = await post(api, headers, task, body)
    assert again.json()["data"]["id"] == final["id"]
    await application.state.processor(final["job_id"])
    assert application.state.processor.sandbox_browser.calls == 1
    kinds = {a["kind"] for a in final["artifacts"]}
    assert {"rendered_html", "provenance_manifest"} <= kinds
    assert ("capture_png" if vendor else "prototype_png") in kinds
    payloads = {}
    for artifact in final["artifacts"]:
        linked = await api.get(
            f"/sandbox-artifacts/{artifact['id']}/download-link", headers=headers
        )
        link = linked.json()["data"]["url"]
        download = await api.get(link, headers=headers)
        assert download.status_code == 200
        assert len(download.content) == artifact["size_bytes"]
        assert hashlib.sha256(download.content).hexdigest() == artifact["sha256"]
        assert download.headers["x-content-type-options"] == "nosniff"
        assert "attachment" in download.headers["content-disposition"]
        if artifact["kind"] == "rendered_html":
            assert download.headers["content-type"] == "application/octet-stream"
        payloads[artifact["kind"]] = download.content
    provenance = json.loads(payloads["provenance_manifest"])
    assert provenance["origin"] == ("public_web_capture" if vendor else "prototype")
    assert provenance["generating_model"] is None
    assert all(p["kind"] != "provenance_manifest" for p in provenance["artifacts"])
    if not vendor:
        assert payloads["rendered_html"] == HTML
        assert provenance["html_sha256"] == hashlib.sha256(HTML).hexdigest()
    async with application.state.db.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(UsageRecord)) == usage_before
        audits = (
            await session.scalars(select(AuditLog).where(AuditLog.action.like("sandbox.%")))
        ).all()
        assert {
            "sandbox.submitted",
            "sandbox.started",
            "sandbox.completed",
            "sandbox.artifact_download_link_issued",
            "sandbox.download_served",
        } <= {a.action for a in audits}
        assert "CANARY_HTML_PRIVATE" not in json.dumps([a.details for a in audits])
        assert "version=fixed" not in json.dumps([a.details for a in audits])
    stored = [p.read_bytes() for p in application.state.storage.root.rglob("*") if p.is_file()]
    assert stored and all(b"CANARY_HTML_PRIVATE" not in b for b in stored)
    artifact_dir = Path("data/work/sandbox-verification")
    await asyncio.to_thread(artifact_dir.mkdir, parents=True, exist_ok=True)
    (artifact_dir / f"api-{'vendor' if vendor else 'prototype'}.json").write_text(
        json.dumps(
            {
                "run": final,
                "hashes": {k: hashlib.sha256(v).hexdigest() for k, v in payloads.items()},
            },
            sort_keys=True,
        )
    )


async def test_missing_routes_and_input_bindings(api, headers, application, pdf_bytes):
    task_a, spec_a = await prepared(api, application, headers, pdf_bytes)
    for target in (str(uuid4()),):
        assert (
            await post(api, headers, target, {"spec": spec_a, "dry_run": True})
        ).status_code == 404
    other, other_spec = await prepared(api, application, headers, pdf_bytes)
    assert (await post(api, headers, other, {"spec": spec_a, "dry_run": True})).status_code == 404
    assert (
        await post(
            api,
            headers,
            task_a,
            {
                "spec": {**spec_a, "extraction_job_id": other_spec["extraction_job_id"]},
                "dry_run": True,
            },
        )
    ).status_code == 404


async def test_hash_utf8_size_and_immutable_spec_gates(api, headers, application, pdf_bytes):
    task, spec = await prepared(api, application, headers, pdf_bytes)
    assert (await post(api, headers, task, {"spec": spec})).status_code == 422
    assert (
        await post(api, headers, task, {"spec": spec, "expected_request_hash": "0" * 64})
    ).status_code == 409
    assert (
        await post(api, headers, task, {"spec": spec, "dry_run": True}, b"wrong")
    ).status_code == 409
    bad = {**spec, "html_size_bytes": 1, "html_sha256": hashlib.sha256(b"\xff").hexdigest()}
    assert (
        await post(api, headers, task, {"spec": bad, "dry_run": True}, b"\xff")
    ).status_code == 422
    for extra in (
        {"generation_job_id": str(uuid4())},
        {"network": "host"},
        {"confirmed_by": str(uuid4())},
    ):
        assert (
            await post(api, headers, task, {"spec": {**spec, **extra}, "dry_run": True})
        ).status_code == 422
    assert (
        await post(api, headers, task, {"spec": spec, "dry_run": True, "retry": True})
    ).status_code == 422


async def test_capture_key_policy_revocation_and_signed_download_binding(
    api, headers, application, pdf_bytes
):
    task, spec = await prepared(api, application, headers, pdf_bytes, vendor=True)
    final, body = await completed(api, application, headers, task, spec)
    changed = {**spec, "viewport": {"width": 1000, "height": 700}}
    preview = await post(api, headers, task, {"spec": changed, "dry_run": True})
    collision = await post(
        api,
        headers,
        task,
        {"spec": changed, "expected_request_hash": preview.json()["data"]["request_hash"]},
    )
    assert collision.status_code == 409
    first, second = final["artifacts"][:2]
    url = (
        await api.get(f"/sandbox-artifacts/{first['id']}/download-link", headers=headers)
    ).json()["data"]["url"]
    assert (
        await api.get(url.replace(first["id"], second["id"]), headers=headers)
    ).status_code == 404
    policy = json.loads(application.state.sandbox_policy_path.read_text())
    policy["revoked_revisions"] = ["synthetic-v1"]
    application.state.sandbox_policy_path.write_text(json.dumps(policy))
    assert (await api.get(url, headers=headers)).status_code == 403
    assert (
        await api.get(f"/sandbox-artifacts/{first['id']}/download-link", headers=headers)
    ).status_code == 403


async def test_cleanup_failure_never_publishes_or_retries(api, headers, application, pdf_bytes):
    task, spec = await prepared(api, application, headers, pdf_bytes)
    browser = application.state.processor.sandbox_browser
    browser.cleanup_state = "failed"
    run, body = await submitted(api, headers, task, spec)
    await application.state.processor(run["job_id"])
    final = (await api.get(f"/sandbox-runs/{run['id']}", headers=headers)).json()["data"]
    assert final["state"] == "cleanup_pending" and final["artifacts"] == []
    retry = await post(api, headers, task, {**body, "retry": True})
    assert retry.status_code == 409


async def test_cancel_and_authorization_loss_fence_publication(
    api, headers, application, pdf_bytes
):
    task, spec = await prepared(api, application, headers, pdf_bytes)
    browser = application.state.processor.sandbox_browser
    browser.entered, browser.gate = asyncio.Event(), asyncio.Event()
    run, _ = await submitted(api, headers, task, spec)
    working = asyncio.create_task(application.state.processor(run["job_id"]))
    await asyncio.wait_for(browser.entered.wait(), 5)
    assert (await api.post(f"/jobs/{run['job_id']}/cancel", headers=headers)).status_code == 200
    browser.gate.set()
    await working
    final = (await api.get(f"/sandbox-runs/{run['id']}", headers=headers)).json()["data"]
    assert final["state"] == "cancelled" and not final["artifacts"]


async def test_tokens_need_new_explicit_scopes_for_jobs_and_runs(
    api, headers, application, pdf_bytes
):
    task, spec = await prepared(api, application, headers, pdf_bytes)
    run, _ = await submitted(api, headers, task, spec)
    token = (
        await api.post(
            "/tokens",
            headers=headers,
            json={
                "name": "synthetic-old",
                "scopes": ["task:read", "job:read", "resource:read", "job:cancel"],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
    ).json()["data"]["token"]
    old = {**headers, "Authorization": "Bearer " + token}
    for method, path in (
        ("GET", f"/sandbox-runs/{run['id']}"),
        ("GET", f"/jobs/{run['job_id']}"),
        ("POST", f"/jobs/{run['job_id']}/cancel"),
    ):
        assert (await api.request(method, path, headers=old)).status_code == 403
    assert (await post(api, old, task, {"spec": spec, "dry_run": True})).status_code == 403


async def test_selection_replacement_blocks_download_but_retains_history(
    api, headers, application, pdf_bytes
):
    task, spec = await prepared(api, application, headers, pdf_bytes)
    final, _ = await completed(api, application, headers, task, spec)
    artifact = final["artifacts"][0]
    link = (
        await api.get(f"/sandbox-artifacts/{artifact['id']}/download-link", headers=headers)
    ).json()["data"]["url"]
    async with application.state.db.transaction() as session:
        await session.execute(
            text("UPDATE task_features SET active=false WHERE id=:id"),
            {"id": UUID(spec["task_feature_id"])},
        )
    view = (await api.get(f"/sandbox-runs/{final['id']}", headers=headers)).json()["data"]
    assert view["selection_active"] is False
    assert view["artifacts"]
    assert (await api.get(link, headers=headers)).status_code == 409


async def test_sql_same_task_parent_mix_and_terminal_attempt_mutation_rejected(
    api, headers, application, pdf_bytes
):
    from app.models import Base

    finals = []
    for _ in range(2):
        task, spec = await prepared(api, application, headers, pdf_bytes, vendor=True)
        final, _ = await completed(api, application, headers, task, spec)
        finals.append(final)
    for table, changed in (
        ("sandbox_inputs", "task_id"),
        ("sandbox_runs", "input_id"),
        ("sandbox_attempts", "job_id"),
        ("sandbox_artifacts", "input_id"),
        ("sandbox_fetch_receipts", "attempt_record_id"),
    ):
        model = Base.metadata.tables[table]
        async with application.state.db.transaction() as session:
            rows = (
                (await session.execute(select(model).order_by(model.c.created_at))).mappings().all()
            )
            first = dict(rows[0])
            last = next(dict(row) for row in rows if row[changed] != first[changed])
        first["id"] = uuid4()
        first[changed] = last[changed]
        if table == "sandbox_runs":
            first["request_hash"] = "1" * 64
            first["capture_key"] = uuid4()
        if table == "sandbox_attempts":
            first["attempt_id"] = uuid4()
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction() as session:
                await session.execute(model.insert().values(**first))
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction() as session:
            await session.execute(text("UPDATE sandbox_attempts SET metrics='{}'::jsonb"))


async def test_cancellation_before_dispatch_and_queue_age_gate(
    api, headers, application, pdf_bytes
):
    task, spec = await prepared(api, application, headers, pdf_bytes)
    run, _ = await submitted(api, headers, task, spec)
    await api.post(f"/jobs/{run['job_id']}/cancel", headers=headers)
    await application.state.processor(run["job_id"])
    assert application.state.processor.sandbox_browser.calls == 0
    assert (await api.get(f"/sandbox-runs/{run['id']}", headers=headers)).json()["data"][
        "state"
    ] == "cancelled"


@pytest.mark.parametrize("vendor", [False, True])
@pytest.mark.skipif(
    os.environ.get("BID_SANDBOX_RUNTIME_TEST") != "1",
    reason="requires PostgreSQL and an independently provisioned accepted sandbox supervisor",
)
async def test_opt_in_real_api_processor_container_and_storage(
    api, headers, application, pdf_bytes, vendor
):
    """End-to-end gates: real runtime completion, exact PDF source bytes, explicit
    page selection and encrypted artifact transfer.
    Vendor HTTP is MockTransport; this gate never accesses a real vendor.
    """
    from app.providers.browser import create_browser_provider

    application.state.processor.sandbox_browser = create_browser_provider()
    task, spec = await prepared(api, application, headers, pdf_bytes, vendor=vendor)
    if vendor:
        spec = {**spec, "format": "pdf", "pdf_pages": [2], "archive": "bundle"}
        application.state.processor.sandbox_fetch_transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=pdf_bytes, headers={"content-type": "application/pdf"}
            )
        )
    final, _ = await completed(api, application, headers, task, spec)
    assert final["cleanup_state"] == "complete"
    if vendor:
        assert [item["page"] for item in final["artifacts"] if item["kind"] == "pdf_page_png"] == [
            2
        ]
        original = next(item for item in final["artifacts"] if item["kind"] == "source_pdf")
        assert original["sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    for artifact in final["artifacts"]:
        linked = (
            await api.get(f"/sandbox-artifacts/{artifact['id']}/download-link", headers=headers)
        ).json()["data"]
        actual = await api.get(linked["url"], headers=headers)
        assert actual.status_code == 200
        assert hashlib.sha256(actual.content).hexdigest() == artifact["sha256"]
    target = Path("data/work/sandbox-verification/runtime")
    await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
    (target / f"api-{'pdf' if vendor else 'prototype'}.json").write_text(
        json.dumps(final, sort_keys=True)
    )
