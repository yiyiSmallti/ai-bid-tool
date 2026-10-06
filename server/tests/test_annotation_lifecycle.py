"""DB/API lifecycle failure inventory for the approved B05 job contract.

Cancellation can be persisted while a renderer is blocked. Rendering timeout or
attempt deadline must leave no material, billed call or changed card; explicit
retry must retain the exact request/PNG bytes. Card revision, requirement meaning,
source integrity and source selection changes between admission and publication
must fence late output. Exhausted funds/budget must not charge or write on preview.
A token, reviewer/observer or wrong task/source must not create an annotation.
These cases require an externally provisioned isolated PostgreSQL fixture; this
module never starts or configures database or application services.
"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import UUID

import pytest
from annotation_test_renderer import FakeAnnotationRenderer, configure_renderer
from app.models.annotations import AnnotationMaterial, AnnotationRelease, AnnotationRequest
from app.models.entities import (
    AuditLog,
    BalanceEntry,
    EvidenceSource,
    Job,
    OrgBalance,
    TaskBudgetRevision,
    UsageRecord,
    VendorCall,
)
from app.models.requirement_confirmation import RequirementReviewEvent
from app.models.screenshots import ScreenshotAsset, ScreenshotPrivacyReview, ScreenshotRendition
from app.models.team_workflow import TaskEvent
from app.providers.base import ProviderFailure
from app.services import annotation_jobs, annotations
from sqlalchemy import func, select, text
from test_annotation_api import preview_submit, seed_annotation
from test_response_cards import phase_one_client
from test_team_workflow_membership import add_member, person, workflow

ARTIFACT = Path(__file__).resolve().parents[2] / "data/work/annotation-lifecycle"
STATE_TABLES = (
    Job,
    UsageRecord,
    VendorCall,
    AuditLog,
    AnnotationRequest,
    AnnotationMaterial,
    AnnotationRelease,
    ScreenshotAsset,
    ScreenshotRendition,
    ScreenshotPrivacyReview,
    RequirementReviewEvent,
    TaskBudgetRevision,
    BalanceEntry,
    TaskEvent,
)


class ControlledRenderer(FakeAnnotationRenderer):
    """Block real byte-valid rendering or inject a timeout after producing bytes."""

    def __init__(self, *, block=False, retryable_failures=0):
        super().__init__()
        self.block = block
        self.retryable_failures = retryable_failures
        self.started = asyncio.Event()
        self.resume = asyncio.Event()
        self.stopped = asyncio.Event()
        self.cancelled = False
        self.outputs = []
        self.requests = []
        self.input_hashes = []

    async def render(self, content, request):
        self.started.set()
        try:
            if self.block:
                await self.resume.wait()
            png, receipt = await super().render(content, request)
            self.outputs.append(png)
            self.requests.append(request.model_dump(mode="json"))
            self.input_hashes.append(hashlib.sha256(content).hexdigest())
            if self.retryable_failures:
                self.retryable_failures -= 1
                raise ProviderFailure(
                    "Synthetic renderer timeout",
                    retryable=True,
                    code="annotation_renderer_failure",
                )
            return png, receipt
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.stopped.set()


def install_control(monkeypatch, **options):
    # Keep API and Processor on the same identity/seam as the shared harness.
    configure_renderer(monkeypatch)
    renderer = ControlledRenderer(**options)
    monkeypatch.setattr(annotations, "renderer_for", lambda processor=None: renderer)
    return renderer


def _record(name, value):
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    (ARTIFACT / f"{name}.json").write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


async def job_status(api, auth, job_id):
    response = await api.get(f"/v4/jobs/{job_id}", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def state_snapshot(app, org_id):
    async with app.state.db.transaction(org_id) as session:
        counts = {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in STATE_TABLES
        }
        balance = await session.get(OrgBalance, org_id)
        counts["org_balance"] = str(balance.balance) if balance else None
        return counts


async def assert_no_publication(app, org_id, task_id, job_id):
    async with app.state.db.transaction(org_id) as session:
        for model in (AnnotationMaterial, AnnotationRelease):
            assert (
                await session.scalar(
                    select(func.count()).select_from(model).where(model.job_id == UUID(job_id))
                )
                == 0
            )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ScreenshotRendition)
                .where(
                    ScreenshotRendition.task_id == UUID(task_id),
                    ScreenshotRendition.profile.in_(annotations.PROFILES),
                )
            )
            == 0
        )
        for model in (VendorCall, UsageRecord):
            assert (
                await session.scalar(
                    select(func.count()).select_from(model).where(model.job_id == UUID(job_id))
                )
                == 0
            )


async def test_persisted_cancel_stops_blocked_render_within_one_second(
    tenants, tmp_path, monkeypatch
):
    renderer = install_control(monkeypatch, block=True)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, card, _, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        _, _, accepted = await preview_submit(api, headers[0], task, target)
        job_id = accepted["job_id"]
        worker = asyncio.create_task(app.state.processor(str(tenants["orgs"][0]), job_id))
        try:
            await asyncio.wait_for(renderer.started.wait(), 3)
            cancelled = await api.post(f"/v4/jobs/{job_id}/cancel", headers=headers[0])
            assert cancelled.status_code == 200, cancelled.text
            started = monotonic()
            await asyncio.wait_for(renderer.stopped.wait(), 1)
            elapsed = monotonic() - started
            await asyncio.wait_for(worker, 1)
            assert renderer.cancelled and elapsed <= 1
            status = await job_status(api, headers[0], job_id)
            assert status["status"] == "cancelled"
            await assert_no_publication(app, tenants["orgs"][0], task, job_id)
            current = (await api.get(f"/cards/{card['id']}", headers=headers[0])).json()["data"]
            assert current["revision"] == card["revision"] and current["evidence"] == []
            await asyncio.to_thread(
                _record,
                "persisted-cancel",
                {
                    "job_id": job_id,
                    "status": status["status"],
                    "renderer_cancelled": renderer.cancelled,
                    "seconds_after_persisted_cancel": elapsed,
                    "published": False,
                },
            )
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


async def test_renderer_timeout_exhausts_retries_then_explicit_retry_retains_bytes(
    tenants, tmp_path, monkeypatch
):
    renderer = install_control(monkeypatch, retryable_failures=3)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, card, _, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        preview, body, accepted = await preview_submit(api, headers[0], task, target)
        job_id = accepted["job_id"]
        for attempt in range(1, 4):
            if attempt < 3:
                with pytest.raises(ProviderFailure) as error:
                    await app.state.processor(str(tenants["orgs"][0]), job_id)
                assert error.value.retryable
            else:
                await app.state.processor(str(tenants["orgs"][0]), job_id)
            status = await job_status(api, headers[0], job_id)
            assert status["status"] == ("queued" if attempt < 3 else "failed")
            assert status["attempts"] == attempt
            await assert_no_publication(app, tenants["orgs"][0], task, job_id)
        retry = await api.post(
            f"/v4/tasks/{task}/annotations", headers=headers[0], json={**body, "retry": True}
        )
        assert retry.status_code == 200, retry.text
        assert retry.json()["data"]["job_id"] == job_id and retry.json()["data"]["duplicate"]
        await app.state.processor(str(tenants["orgs"][0]), job_id)
        status = await job_status(api, headers[0], job_id)
        assert status["status"] == "succeeded" and status["attempts"] == 1
        assert len(renderer.outputs) == 4 and len(set(renderer.outputs)) == 1
        assert renderer.requests == [renderer.requests[0]] * 4
        assert renderer.input_hashes == [renderer.input_hashes[0]] * 4
        unchanged = (await api.get(f"/cards/{card['id']}", headers=headers[0])).json()["data"]
        assert unchanged["revision"] == card["revision"] and not unchanged["evidence"]
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            persisted_job = await session.get(Job, UUID(job_id))
            assert persisted_job is not None
            assert preview["input_hash"] == persisted_job.result["submission"]["input_hash"]
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AnnotationRequest)
                    .where(AnnotationRequest.job_id == UUID(job_id))
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AnnotationMaterial)
                    .where(AnnotationMaterial.job_id == UUID(job_id))
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(VendorCall)
                    .where(VendorCall.job_id == UUID(job_id))
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(UsageRecord)
                    .where(UsageRecord.job_id == UUID(job_id))
                )
                == 0
            )
        await asyncio.to_thread(
            _record,
            "fixed-retry",
            {
                "job_id": job_id,
                "attempt_image_hashes": [
                    hashlib.sha256(png).hexdigest() for png in renderer.outputs
                ],
                "input_hashes": renderer.input_hashes,
                "status": status["status"],
                "final_attempts": status["attempts"],
            },
        )


async def test_attempt_deadline_cancels_renderer_and_fences_publication(
    tenants, tmp_path, monkeypatch
):
    renderer = install_control(monkeypatch, block=True)
    monkeypatch.setattr(annotation_jobs, "ATTEMPT_DEADLINE_SECONDS", 1.0)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, _, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        _, _, accepted = await preview_submit(api, headers[0], task, target)
        job_id = accepted["job_id"]
        with pytest.raises(ProviderFailure) as error:
            await app.state.processor(str(tenants["orgs"][0]), job_id)
        assert error.value.retryable and renderer.cancelled and renderer.stopped.is_set()
        status = await job_status(api, headers[0], job_id)
        assert status["status"] == "queued" and status["error"]["code"] == "annotation_deadline"
        await assert_no_publication(app, tenants["orgs"][0], task, job_id)
        await asyncio.to_thread(
            _record,
            "attempt-deadline",
            {
                "job_id": job_id,
                "status": status["status"],
                "error": status["error"]["code"],
                "renderer_cancelled": renderer.cancelled,
                "published": False,
            },
        )


@pytest.mark.parametrize(
    "change", ["card_revision", "requirement_meaning", "source_bytes", "source_selection"]
)
async def test_changed_inputs_during_render_discard_late_output(
    tenants, tmp_path, monkeypatch, admin_engine, change
):
    renderer = install_control(monkeypatch, block=True)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, card, source, selection, target = await seed_annotation(
            api, app, headers[0], tmp_path
        )
        _, _, accepted = await preview_submit(api, headers[0], task, target)
        job_id = accepted["job_id"]
        storage = app.state.processor.storage
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            source_row = await session.get(EvidenceSource, UUID(source["id"]))
            assert source_row is not None
            source_key = source_row.storage_key
        original_read = storage.read_bounded
        corrupted = False

        async def read(org_id, key, max_bytes):
            content = await original_read(org_id, key, max_bytes)
            return (
                content[:-1] + bytes([content[-1] ^ 1])
                if corrupted and key == source_key
                else content
            )

        monkeypatch.setattr(storage, "read_bounded", read)
        worker = asyncio.create_task(app.state.processor(str(tenants["orgs"][0]), job_id))
        try:
            await asyncio.wait_for(renderer.started.wait(), 3)
            if change == "card_revision":
                response = await api.put(
                    f"/cards/{card['id']}",
                    headers=headers[0],
                    json={
                        "expected_revision": card["revision"],
                        "content": {
                            **card["content"],
                            "response_text": "A later human edit must win.",
                        },
                    },
                )
                assert response.status_code == 200, response.text
            elif change == "source_bytes":
                corrupted = True
            else:
                # Simulate a genuine upstream data/selection change after admission;
                # the real publication resolver and RLS runtime still perform all checks.
                with admin_engine.begin() as connection:
                    if change == "requirement_meaning":
                        connection.execute(
                            text(
                                "UPDATE requirements SET text=text || ' Changed interpretation.' WHERE id=:id"
                            ),
                            {"id": UUID(card["requirement_id"])},
                        )
                    else:
                        connection.execute(
                            text("UPDATE task_certificates SET active=false WHERE id=:id"),
                            {"id": UUID(selection["id"])},
                        )
            renderer.resume.set()
            await asyncio.wait_for(worker, 3)
            status = await job_status(api, headers[0], job_id)
            assert status["status"] == "failed", status
            assert status["error"]["code"] in {
                "card_revision_changed",
                "annotation_input_changed",
                "source_integrity_failure",
                "source_inactive",
                "requirement_review_not_current",
            }
            await assert_no_publication(app, tenants["orgs"][0], task, job_id)
            assert len(renderer.outputs) == 1
            await asyncio.to_thread(
                _record,
                f"changed-{change}",
                {
                    "job_id": job_id,
                    "status": status["status"],
                    "error": status["error"]["code"],
                    "late_output_sha256": hashlib.sha256(renderer.outputs[0]).hexdigest(),
                    "published": False,
                },
            )
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


async def test_zero_cost_preview_with_exhausted_budget_creates_no_state(
    tenants, tmp_path, monkeypatch, admin_engine
):
    configure_renderer(monkeypatch)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, card, _, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        budget = (await api.get(f"/v4/tasks/{task}/budget", headers=headers[0])).json()["data"][
            "budget"
        ]
        changed = await api.put(
            f"/v4/tasks/{task}/budget",
            headers=headers[0],
            json={
                "expected_revision": budget["revision"],
                "limit": "0",
                "currency": "USD",
                "reason": "Synthetic exhausted budget preview acceptance",
            },
        )
        assert changed.status_code == 200, changed.text
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT * FROM platform_adjust_balance(:org,'set',0,'Synthetic zero balance','acceptance','USD')"
                ),
                {"org": tenants["orgs"][0]},
            )
        before = await state_snapshot(app, tenants["orgs"][0])
        queued_before = list(app.state.processor.queue.calls)
        response = await api.post(
            f"/v4/tasks/{task}/annotations",
            headers=headers[0],
            json={"dry_run": True, "input": target},
        )
        assert response.status_code == 200, response.text
        preview = response.json()["data"]
        liability = preview["budget_preflight"]
        assert liability["planned_calls"] == 0 and liability["next_call"] is None
        assert liability["admission_blocker"] is None
        for key in (
            "charge",
            "task_amount",
            "usd",
            "llm_tokens",
            "ocr_pages",
            "unpriced_calls",
            "unresolved_calls",
        ):
            assert float(liability["estimate"][key]) == 0
        assert await state_snapshot(app, tenants["orgs"][0]) == before
        assert app.state.processor.queue.calls == queued_before
        current = (await api.get(f"/cards/{card['id']}", headers=headers[0])).json()["data"]
        assert current["revision"] == card["revision"] and not current["evidence"]
        await asyncio.to_thread(
            _record,
            "zero-cost-preview",
            {
                "task_id": task,
                "counts_before": before,
                "counts_after": before,
                "input_hash": preview["input_hash"],
                "budget_preflight": liability,
            },
        )


@pytest.mark.parametrize(
    "task_role,org_role,domains,status",
    [
        ("observer", "bidder", [], 403),
        ("reviewer", "bidder", ["commercial"], 403),
        ("contributor", "bidder", ["commercial"], 200),
        ("contributor", "technical", ["technical"], 200),
    ],
)
async def test_annotation_create_task_role_matrix(
    tenants, tmp_path, monkeypatch, admin_engine, task_role, org_role, domains, status
):
    configure_renderer(monkeypatch)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, _, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        user, member_headers = await person(api, admin_engine, tenants["orgs"][0], org_role)
        current = await workflow(api, headers[0], task)
        added = await add_member(
            api, headers[0], task, user, current["revision"], task_role, domains
        )
        assert added.status_code == 200, added.text
        before = await state_snapshot(app, tenants["orgs"][0])
        response = await api.post(
            f"/v4/tasks/{task}/annotations",
            headers=member_headers,
            json={"dry_run": True, "input": target},
        )
        assert response.status_code == status, response.text
        if status == 403:
            assert await state_snapshot(app, tenants["orgs"][0]) == before
        else:
            _, _, accepted = await preview_submit(api, member_headers, task, target)
            assert accepted["kind"] == "annotation_render" and accepted["status"] == "queued"


async def test_tokens_cannot_request_human_annotation_scope_or_create(
    tenants, tmp_path, monkeypatch
):
    from uuid import uuid4

    configure_renderer(monkeypatch)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, _, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        forbidden = await api.post(
            "/tokens",
            headers=headers[0],
            json={
                "name": "Synthetic forbidden annotator",
                "scopes": ["evidence:annotate"],
                "expires_at": expiry,
            },
        )
        assert forbidden.status_code == 403, forbidden.text
        issued = await api.post(
            "/tokens",
            headers=headers[0],
            json={
                "name": "Synthetic read token",
                "scopes": ["task:read", "card:read"],
                "expires_at": expiry,
            },
        )
        assert issued.status_code == 200, issued.text
        token_headers = {**headers[0], "Authorization": "Bearer " + issued.json()["data"]["token"]}
        preview = await api.post(
            f"/v4/tasks/{task}/annotations",
            headers=headers[0],
            json={"dry_run": True, "input": target},
        )
        assert preview.status_code == 200, preview.text
        value = preview.json()["data"]
        submission = {
            "input": target,
            "request_id": str(uuid4()),
            "expected_input_hash": value["input_hash"],
            "reviewed_source_png_sha256": value["manifest"]["source"]["source_png"]["sha256"],
        }
        before = await state_snapshot(app, tenants["orgs"][0])
        for body in ({"dry_run": True, "input": target}, submission):
            denied = await api.post(
                f"/v4/tasks/{task}/annotations", headers=token_headers, json=body
            )
            assert denied.status_code == 403, denied.text
        # A02 rolls back the denied business transaction but preserves one
        # command.failed security receipt for the non-dry token invocation.
        assert await state_snapshot(app, tenants["orgs"][0]) == {
            **before,
            "audit_logs": before["audit_logs"] + 1,
        }
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            receipts = list(
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.actor_token_id == UUID(issued.json()["data"]["id"])
                    )
                )
            )
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.action == "command.failed" and receipt.actor_kind == "token"
        assert receipt.actor_user_id == tenants["users"][0]
        assert receipt.details == {"command": "evidence stamp", "reason_code": "forbidden"}


async def test_same_org_foreign_task_card_source_and_filters_are_404(
    tenants, tmp_path, monkeypatch
):
    configure_renderer(monkeypatch)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task_a, _, _, _, target_a = await seed_annotation(api, app, headers[0], tmp_path)
        task_b, card_b, source_b, _, target_b = await seed_annotation(
            api, app, headers[0], tmp_path
        )
        before = await state_snapshot(app, tenants["orgs"][0])
        cases = [
            (task_b, target_a),
            (task_a, {**target_a, "card_id": card_b["id"]}),
            (
                task_a,
                {
                    **target_a,
                    "source": {"kind": "certificate_page", "evidence_source_id": source_b["id"]},
                },
            ),
            (task_a, {**target_a, "extraction_job_id": target_b["extraction_job_id"]}),
        ]
        for task, target in cases:
            denied = await api.post(
                f"/v4/tasks/{task}/annotations",
                headers=headers[0],
                json={"dry_run": True, "input": target},
            )
            assert (
                denied.status_code == 404 and denied.json()["data"]["error"]["code"] == "not_found"
            ), denied.text
        wrong_filter = await api.get(
            f"/v4/tasks/{task_a}/annotations", headers=headers[0], params={"card_id": card_b["id"]}
        )
        assert wrong_filter.status_code == 404, wrong_filter.text
        assert await state_snapshot(app, tenants["orgs"][0]) == before


async def test_same_org_nonmember_cannot_use_annotation_or_signed_routes(
    tenants, tmp_path, monkeypatch, admin_engine
):
    configure_renderer(monkeypatch)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task_a, _, _, _, target_a = await seed_annotation(api, app, headers[0], tmp_path)
        task_b, _, _, _, _ = await seed_annotation(api, app, headers[0], tmp_path)
        user, other_headers = await person(api, admin_engine, tenants["orgs"][0], "bidder")
        current = await workflow(api, headers[0], task_b)
        added = await add_member(api, headers[0], task_b, user, current["revision"], "observer", [])
        assert added.status_code == 200, added.text
        _, _, accepted = await preview_submit(api, headers[0], task_a, target_a)
        job_id = accepted["job_id"]
        await app.state.processor(str(tenants["orgs"][0]), job_id)
        status = await job_status(api, headers[0], job_id)
        assert status["status"] == "succeeded", status
        annotation_id = status["result"]["annotation_id"]
        preview = await api.get(f"/v4/annotations/{annotation_id}/preview", headers=headers[0])
        assert preview.status_code == 200, preview.text
        signed_path = preview.json()["data"]["url"]
        before = await state_snapshot(app, tenants["orgs"][0])
        routes = [
            ("list", "GET", f"/v4/tasks/{task_a}/annotations", None),
            ("show", "GET", f"/v4/annotations/{annotation_id}", None),
            ("preview", "GET", f"/v4/annotations/{annotation_id}/preview", None),
            ("signed_content", "GET", signed_path, None),
            ("release_history", "GET", f"/v4/annotations/{annotation_id}/releases", None),
            ("job_status", "GET", f"/v4/jobs/{job_id}", None),
            ("job_cancel", "POST", f"/v4/jobs/{job_id}/cancel", None),
            (
                "preflight",
                "POST",
                f"/v4/tasks/{task_a}/annotations",
                {"dry_run": True, "input": target_a},
            ),
        ]
        statuses = {}
        for label, method, path, body in routes:
            denied = await api.request(method, path, headers=other_headers, json=body)
            assert denied.status_code == 404, (label, denied.status_code)
            assert denied.json()["data"]["error"]["code"] == "not_found"
            statuses[label] = denied.status_code
        assert await state_snapshot(app, tenants["orgs"][0]) == before
        await asyncio.to_thread(
            _record,
            "same-org-route-isolation",
            {
                "org_id": str(tenants["orgs"][0]),
                "visible_task_id": task_b,
                "inaccessible_task_id": task_a,
                "routes": statuses,
                "state_unchanged": True,
            },
        )


@pytest.mark.parametrize(
    "invalid_plan",
    [
        {"boxes": [{"x": 0, "y": 0, "width": 1, "height": 1}] * 21},
        {"crop": {"x": True, "y": 0, "width": 10, "height": 10}},
        {"crop": {"x": 0.5, "y": 0, "width": 10, "height": 10}},
        {"crop": {"x": 8192, "y": 0, "width": 1, "height": 10}},
        {"watermark": "CONFIRMED"},
        {
            "crop": {"x": 0, "y": 0, "width": 10, "height": 10},
            "boxes": [{"x": 9, "y": 9, "width": 2, "height": 2}],
        },
    ],
)
async def test_api_plan_limits_reject_without_state(tenants, tmp_path, monkeypatch, invalid_plan):
    configure_renderer(monkeypatch)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, _, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        before = await state_snapshot(app, tenants["orgs"][0])
        rejected = await api.post(
            f"/v4/tasks/{task}/annotations",
            headers=headers[0],
            json={"dry_run": True, "input": {**target, "plan": invalid_plan}},
        )
        assert rejected.status_code == 422, rejected.text
        assert await state_snapshot(app, tenants["orgs"][0]) == before


async def test_http_json_and_configured_png_byte_limits(tenants, tmp_path, monkeypatch):
    configure_renderer(monkeypatch)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, source, _, target = await seed_annotation(api, app, headers[0], tmp_path)
        before = await state_snapshot(app, tenants["orgs"][0])
        too_large = await api.post(
            f"/v4/tasks/{task}/annotations",
            headers={**headers[0], "Content-Type": "application/json"},
            content=b" " * (128 * 1024 + 1),
        )
        assert too_large.status_code == 413, too_large.text
        monkeypatch.setattr(
            app.state.processor.settings, "max_upload_bytes", source["preview"]["size_bytes"] - 1
        )
        rejected = await api.post(
            f"/v4/tasks/{task}/annotations",
            headers=headers[0],
            json={"dry_run": True, "input": target},
        )
        assert rejected.status_code == 413, rejected.text
        assert await state_snapshot(app, tenants["orgs"][0]) == before
