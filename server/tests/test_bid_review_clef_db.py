"""HTTP → human pixels → accounted worker acceptance with fake Cloudflare I/O.

Failure inventory: data/work/bid-review-clef/failure-modes.md. Run only in the
owning DB session with --basetemp=data/work/bid-review-clef/pytest. Receipts retain
synthetic derivative hashes, fixed quotes and immutable report sections.
"""

import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import Settings
from app.models.bid_review_presence import BidPresenceAuthorizedImage
from app.models.entities import UsageRecord, VendorCall
from app.providers.clef import HTTPClefProvider
from app.providers.clef_gateway import HTTPGatewayChecker
from app.schemas.clef import ClefCheckRequest, ClefSettingsSet
from app.schemas.platform_credentials import CredentialSetActive
from app.services.auth import HUMAN_SCOPES, Identity, set_actor_context
from app.services.platform_clef import PlatformClefService
from app.services.platform_credentials import PlatformCredentialService
from conftest import seed_platform_credential
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from test_bid_review_run_db import (
    BIDDER,
    SAFE_CLAUSE,
    SIGNING,
    STAFF,
    ReviewVendor,
    authorize,
    data,
    document,
    install_resolver,
    preview,
    review_case,
    review_llm,
    submit,
    terminal,
)
from test_bid_review_upload_db import PDF, run, task, upload
from test_bid_review_upload_db import preview as prepare_preview
from test_bid_review_upload_db import submit as prepare_submit

WORKERS_TOKEN = "synthetic-clef-workers-token"
GATEWAY_TOKEN = "synthetic-clef-gateway-token"


class Gateway:
    def __init__(self, *, unsafe=False):
        self.unsafe, self.requests = unsafe, []

    async def __call__(self, request):
        assert request.method == "GET"
        assert request.url.host == "api.cloudflare.com"
        assert request.headers["Authorization"] == "Bearer " + GATEWAY_TOKEN
        self.requests.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "id": "synthetic-clef",
                    "authentication": True,
                    "collect_logs": self.unsafe,
                    "logpush": False,
                    "cache_ttl": 0,
                    "retries": 0,
                    "rate_limiting_limit": 200,
                    "rate_limiting_interval": 60,
                },
            },
        )


class ClefVendor:
    def __init__(self, *, failure=None, probability=0.02):
        self.failure, self.probability = failure, probability
        self.requests, self.image_hashes = [], []
        self.active = self.maximum_active = 0
        self.first_group: asyncio.Event | None = None

    async def __call__(self, request):
        assert request.method == "POST"
        assert str(request.url) == (
            "https://gateway.ai.cloudflare.com/v1/"
            + "a" * 32
            + "/synthetic-clef/workers-ai/@cf/cloudflare/clef"
        )
        assert request.headers["Authorization"] == "Bearer " + WORKERS_TOKEN
        assert request.headers["cf-aig-authorization"] == "Bearer " + GATEWAY_TOKEN
        assert request.headers["cf-aig-collect-log"] == "false"
        body = json.loads(request.content)
        assert set(body) == {"state", "questions", "images"}
        assert set(body["questions"]) == {"q1", "q2"}
        assert body["questions"]["q1"] == {
            "type": "noul",
            "instructions": "这一页上是否加盖了红色圆形公章（含电子签章）？",
        }
        assert body["questions"]["q2"]["type"] == "noul"
        assert len(body["images"]) == 1
        image = base64.b64decode(
            body["images"][0].removeprefix("data:image/jpeg;base64,"), validate=True
        )
        assert image.startswith(b"\xff\xd8")
        self.image_hashes.append(hashlib.sha256(image).hexdigest())
        self.requests.append(
            {
                "state": body["state"],
                "questions": body["questions"],
                "image_sha256": self.image_hashes[-1],
            }
        )
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            if self.first_group is not None:
                if self.active == 3:
                    self.first_group.set()
                await asyncio.wait_for(self.first_group.wait(), 5)
            await asyncio.sleep(0.01)
            if self.failure == "timeout":
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            if self.failure == "always_429" or self.failure == "429" and len(self.requests) == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            answers = {
                "q1": {"type": "noul", "noul": self.probability},
                "q2": {"type": "noul", "noul": 0.97},
            }
            if self.failure == "malformed":
                answers["q1"]["noul"] = 1.7
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {
                        "answers": answers,
                        "usage": {"input_tokens": 1300 + 389 * len(self.requests)},
                    },
                },
                headers={"cf-aig-event-id": "synthetic-event-1", "cf-ray": "synthetic-ray-1"},
            )
        finally:
            self.active -= 1


async def configure(application, monkeypatch, *, unsafe=False):
    settings = Settings()
    workers = await seed_platform_credential(
        settings,
        name="clef_workers",
        purpose="clef_workers_ai",
        provider="cloudflare",
        endpoint="https://gateway.ai.cloudflare.com",
        key=WORKERS_TOKEN,
    )
    gateway = await seed_platform_credential(
        settings,
        name="clef_gateway",
        purpose="clef_gateway",
        provider="cloudflare",
        endpoint="https://api.cloudflare.com/client/v4",
        key=GATEWAY_TOKEN,
    )
    actor = Identity(scopes=set(HUMAN_SCOPES))
    fake = Gateway(unsafe=unsafe)
    service = PlatformClefService(
        settings,
        application.state.db,
        checker=HTTPGatewayChecker(transport=httpx.MockTransport(fake)),
    )
    saved = await service.set(
        actor,
        ClefSettingsSet(
            account_id="a" * 32,
            gateway_id="synthetic-clef",
            enabled=True,
            workers_credential_id=workers.id,
            gateway_credential_id=gateway.id,
        ),
    )
    assert saved.config is not None
    checked = await service.check(actor, ClefCheckRequest(expected_revision=saved.config.revision))
    assert len(fake.requests) == 1
    return actor, checked, workers, gateway


def install_vendor(monkeypatch, vendor):
    original = HTTPClefProvider.__init__

    def injected(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(vendor)
        original(self, *args, **kwargs)

    monkeypatch.setattr(HTTPClefProvider, "__init__", injected)


async def source_case(
    api,
    headers,
    application,
    tmp_path,
    monkeypatch,
    *,
    bid_pages=1,
    applicability="applies",
):
    vendor = ReviewVendor(applicability=applicability)
    if bid_pages == 1:
        case = await review_case(api, headers, application, tmp_path, monkeypatch, vendor=vendor)
    else:
        task_id = await task(api, headers)
        llm = await review_llm(api, headers, tmp_path, vendor)
        install_resolver(monkeypatch, llm)
        files = [
            ("synthetic-tender.pdf", PDF, document([SAFE_CLAUSE + "\n" + SIGNING])),
            (
                "synthetic-bid.pdf",
                PDF,
                document(
                    [f"投标人：{BIDDER}\n法定代表人：{STAFF}\n{SAFE_CLAUSE}"] * bid_pages
                    + ["报价：937462.58 元"]
                ),
            ),
        ]
        uploaded = data(await upload(api, headers, task_id, files))
        prepared = data(await prepare_preview(api, headers, task_id, uploaded["id"]))
        preparation = data(await prepare_submit(api, headers, task_id, uploaded["id"], prepared))
        await run(application, preparation["job_id"])
        case = {"task": task_id, "submission": uploaded["id"], "vendor": vendor}
        await authorize(api, headers, case)
    receipt = data(await preview(api, headers, case))
    accepted = data(await submit(api, headers, case, receipt))
    finished = await terminal(api, headers, application, accepted)
    assert finished["status"] == "succeeded", finished.get("error", finished)
    case["source_review"] = accepted["review_id"]
    return case


async def authorize_images(api, header, case, *, expected_images=1, subset=False):
    prepared = data(
        await api.post(
            f"/v4/bid-submissions/{case['submission']}/presence-preparations",
            headers=header,
            json={"review_id": case["source_review"]},
        )
    )
    assert len(prepared["images"]) == expected_images  # price location remains excluded
    image = prepared["images"][0]
    content = await api.get(f"/v4/bid-presence-images/{image['id']}/content", headers=header)
    assert content.status_code == 200
    assert content.headers["Cache-Control"] == "no-store"
    assert hashlib.sha256(content.content).hexdigest() == image["sha256"]
    assert image["source_sha256"] != image["sha256"]
    grant = data(
        await api.post(
            f"/v4/bid-submissions/{case['submission']}/presence-authorizations",
            headers=header,
            json={
                "request_id": str(uuid4()),
                "expected_revision": prepared["expected_revision"],
                "expected_authorization_id": prepared["expected_authorization_id"],
                "manifest_sha256": prepared["manifest_sha256"],
                "image_ids": [image["id"] for image in prepared["images"][: 1 if subset else None]],
                "privacy_reviewed": True,
                "reason": "Synthetic exact blurred pixel approval",
            },
        )
    )
    assert grant["purpose"] == "bid_review_presence"
    case["presence"] = prepared
    return grant


async def ledger(application, accepted):
    async with application.state.db.transaction() as session:
        return (
            list(
                (
                    await session.scalars(
                        select(VendorCall).where(
                            VendorCall.job_id == UUID(accepted["job_id"]),
                            VendorCall.capability == "vision",
                        )
                    )
                ).all()
            ),
            list(
                (
                    await session.scalars(
                        select(UsageRecord).where(
                            UsageRecord.job_id == UUID(accepted["job_id"]),
                            UsageRecord.capability == "vision",
                        )
                    )
                ).all()
            ),
        )


@pytest.mark.parametrize("failure", [None, "429", "timeout", "malformed", "always_429"])
async def test_exact_blurred_gateway_call_fixed_settlement_and_human_triage(
    api, headers, application, tmp_path, monkeypatch, failure
):
    case = await source_case(api, headers, application, tmp_path, monkeypatch)
    await configure(application, monkeypatch)
    await authorize_images(api, headers, case)
    vendor = ClefVendor(failure=failure)
    install_vendor(monkeypatch, vendor)
    receipt = data(await preview(api, headers, case))
    assert receipt["input"]["clef_enabled"] is True
    assert receipt["input"]["clef"]["available"] is True
    assert "binding" not in receipt["input"]["clef"] and "images" not in receipt["input"]["clef"]
    assert "workers_credential_id" not in json.dumps(receipt)
    assert "gateway_credential_id" not in json.dumps(receipt)
    assert receipt["input"]["clef"]["planned_calls"] == 1
    assert not vendor.requests
    accepted = data(await submit(api, headers, case, receipt))
    finished = await terminal(api, headers, application, accepted)
    assert finished["status"] == "succeeded", finished.get("error", finished)
    calls, usages = await ledger(application, accepted)
    expected_calls = 40 if failure == "always_429" else 2 if failure == "429" else 1
    assert len(calls) == len(vendor.requests) == expected_calls
    assert vendor.maximum_active <= 3
    expected_completed = 0 if failure in {"timeout", "always_429"} else 1
    assert len(usages) == expected_completed
    assert set(vendor.image_hashes) == {image["sha256"] for image in case["presence"]["images"]}
    for usage in usages:
        assert usage.tokens >= 1300
        assert usage.gateway_request_id == "synthetic-event-1"
        assert usage.gateway_trace_id == "synthetic-ray-1"
    if failure == "timeout":
        assert calls[0].state == "unknown"
    if failure in {"429", "always_429"}:
        assert (
            sum(call.state == "not_sent" for call in calls) == expected_calls - expected_completed
        )
    detail = data(
        await api.get(
            f"/v4/bid-reviews/{accepted['review_id']}",
            headers=headers,
            params={"section": "signing_requirements"},
        )
    )
    locations = [
        location
        for requirement in detail["signing_requirements"]
        for location in requirement["required_locations"]
    ]
    assert all(location["status"] != "confirmed_present" for location in locations)
    if failure == "timeout":
        assert detail["run"]["coverage"]["clef_unresolved_calls"] == 1
    assert all(
        location["owner_status"] == location["date_status"] == "unresolved"
        for location in locations
    )
    if failure in {None, "429"}:
        assert any(
            location["status"] == "triage_absent" and location["human_escalation"]
            for location in locations
        )
        assert "signing_presence_escalated_to_human" in detail["run"]["uncovered_codes"]
    else:
        assert all(location["status"] == "unresolved" for location in locations)
    report = await api.get(
        f"/v4/bid-reviews/{accepted['review_id']}/report",
        headers=headers,
        params={"section": "signatures"},
    )
    assert report.status_code == 200 and "需人工确认" in report.text
    if failure is None:
        from test_bid_review_report_db import docx_text, render

        case["review"] = accepted["review_id"]
        rendered, _ = await render(api, headers, application, case)
        descriptor = next(
            item for item in rendered["result"]["artifacts"] if item["format"] == "docx"
        )
        link = data(
            await api.get(
                f"/v4/bid-review-artifacts/{descriptor['id']}/download-link", headers=headers
            )
        )
        download = await api.get(link["url"], headers=headers)
        assert download.status_code == 200
        content = docx_text(download.content)
        assert "初筛疑似缺失" in content and "需人工确认" in content
        assert "淡印、灰度、残缺、错误公司、要求位置和骑缝章尚未验证" in content
        assert hashlib.sha256(download.content).hexdigest() == descriptor["sha256"]
        (tmp_path / "clef-fixed-presence-report.docx").write_bytes(download.content)
    (tmp_path / f"clef-{failure or 'completed'}-receipt.json").write_text(
        json.dumps(
            {
                "preview": receipt,
                "calls": [call.provider_identity for call in calls],
                "requests": vendor.requests,
                "signing": detail,
                "report": report.json(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "blocked",
    [
        "gateway_unsafe",
        "workers_missing",
        "gateway_missing",
        "unconfigured",
        "unauthorized",
        "opt_out",
    ],
)
async def test_clef_unavailable_or_opt_out_preserves_run_and_explicit_coverage(
    api,
    headers,
    application,
    tmp_path,
    monkeypatch,
    blocked,
):
    case = await source_case(api, headers, application, tmp_path, monkeypatch)
    if blocked != "unconfigured":
        actor, checked, workers, gateway = await configure(
            application, monkeypatch, unsafe=blocked == "gateway_unsafe"
        )
        if blocked in {"workers_missing", "gateway_missing"}:
            target = workers if blocked == "workers_missing" else gateway
            await PlatformCredentialService(Settings(), application.state.db).set_active(
                actor,
                target.id,
                CredentialSetActive(expected_revision=1, active=False, reason="incident"),
            )
        if blocked == "opt_out":
            await authorize_images(api, headers, case)
    vendor = ClefVendor()
    install_vendor(monkeypatch, vendor)
    options = {"clef_enabled": False} if blocked == "opt_out" else {}
    receipt = data(await preview(api, headers, case, **options))
    assert not receipt["input"]["clef"]["available"]
    assert not receipt["admission_blockers"], receipt
    accepted = data(await submit(api, headers, case, receipt, **options))
    finished = await terminal(api, headers, application, accepted)
    assert finished["status"] == "succeeded", finished.get("error", finished)
    detail = data(await api.get(f"/v4/bid-reviews/{accepted['review_id']}", headers=headers))
    assert detail["run"]["coverage"]["clef"] == (
        "disabled" if blocked == "opt_out" else "unavailable"
    )
    assert not vendor.requests
    assert await ledger(application, accepted) == ([], [])


async def test_presence_pixels_are_not_disclosed_to_tokens(
    api,
    headers,
    application,
    tmp_path,
    monkeypatch,
):
    case = await source_case(api, headers, application, tmp_path, monkeypatch)
    await configure(application, monkeypatch)
    await authorize_images(api, headers, case)
    image_id = case["presence"]["images"][0]["id"]
    path = f"/v4/bid-presence-images/{image_id}/content"
    from app.models.bid_review_presence import (
        BidPresenceAuthorization,
        BidPresenceImage,
        BidPresencePreparation,
    )

    for model in (
        BidPresencePreparation,
        BidPresenceImage,
        BidPresenceAuthorization,
        BidPresenceAuthorizedImage,
    ):
        async with application.state.db.transaction() as session:
            assert (await session.scalars(select(model))).first() is not None
    issued = data(
        await api.post(
            "/v4/tokens",
            headers=headers,
            json={
                "name": "Synthetic Clef token",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "scopes": ["task:read", "job:read", "bid-review:read", "bid-review:run"],
            },
        )
    )
    token_header = {**headers, "Authorization": "Bearer " + issued["token"]}
    assert (await api.get(path, headers=token_header)).status_code == 403
    assert (
        await api.get(
            f"/v4/bid-submissions/{case['submission']}/presence-preview", headers=token_header
        )
    ).status_code == 403
    denied = await api.get("/platform/clef", headers=token_header)
    assert denied.status_code == 401
    assert denied.json()["data"]["error"]["code"] == "invalid_session"


async def test_visual_concurrency_is_three_and_token_variation_is_recorded(
    api, headers, application, tmp_path, monkeypatch
):
    case = await source_case(api, headers, application, tmp_path, monkeypatch, bid_pages=6)
    await configure(application, monkeypatch)
    await authorize_images(api, headers, case, expected_images=6)
    vendor = ClefVendor(probability=0.98)
    vendor.first_group = asyncio.Event()
    install_vendor(monkeypatch, vendor)
    receipt = data(await preview(api, headers, case))
    assert receipt["input"]["clef"]["planned_calls"] == 6
    accepted = data(await submit(api, headers, case, receipt))
    finished = await terminal(api, headers, application, accepted)
    assert finished["status"] == "succeeded", finished.get("error", finished)
    calls, usages = await ledger(application, accepted)
    assert len(calls) == len(usages) == 6
    assert vendor.maximum_active == 3
    assert len({usage.tokens for usage in usages}) > 1


async def test_grant_subset_cannot_expand_and_revoked_queued_pixels_never_dispatch(
    api,
    headers,
    application,
    tmp_path,
    monkeypatch,
):
    case = await source_case(api, headers, application, tmp_path, monkeypatch, bid_pages=3)
    await configure(application, monkeypatch)
    grant = await authorize_images(api, headers, case, expected_images=3, subset=True)
    assert len(grant["image_ids"]) == 1
    first, unauthorized = case["presence"]["images"][:2]
    from app.models.bid_review_presence import BidPresenceImage

    with pytest.raises(DBAPIError):
        async with application.state.db.transaction() as session:
            await set_actor_context(
                session,
                Identity(scopes=set(HUMAN_SCOPES)),
            )
            original = await session.get(BidPresenceImage, UUID(first["id"]))
            session.add(
                BidPresenceAuthorizedImage(
                    id=uuid4(),
                    task_id=UUID(case["task"]),
                    submission_id=UUID(case["submission"]),
                    presence_preparation_id=original.presence_preparation_id,
                    authorization_id=UUID(grant["id"]),
                    image_id=UUID(unauthorized["id"]),
                )
            )
            await session.flush()
    vendor = ClefVendor()
    install_vendor(monkeypatch, vendor)
    receipt = data(await preview(api, headers, case))
    assert receipt["input"]["clef"]["planned_calls"] == 1
    accepted = data(await submit(api, headers, case, receipt))
    revoked = data(
        await api.post(
            f"/v4/bid-submissions/{case['submission']}/presence-authorizations",
            headers=headers,
            json={
                "request_id": str(uuid4()),
                "expected_revision": grant["revision"] + 1,
                "expected_authorization_id": grant["id"],
                "manifest_sha256": grant["manifest_sha256"],
                "image_ids": grant["image_ids"],
                "privacy_reviewed": True,
                "allow_external": False,
                "reason": "Synthetic revocation before queued dispatch",
            },
        )
    )
    assert not revoked["allow_external"]
    finished = await terminal(api, headers, application, accepted)
    assert finished["status"] == "failed", finished.get("error", finished)
    assert not vendor.requests
    assert await ledger(application, accepted) == ([], [])


@pytest.mark.parametrize("applicability", ["unknown", "not_applicable"])
async def test_unconfirmed_required_locations_never_create_presence_derivatives(
    api, headers, application, tmp_path, monkeypatch, applicability
):
    from app.models.bid_review_presence import BidPresenceImage, BidPresencePreparation

    case = await source_case(
        api, headers, application, tmp_path, monkeypatch, applicability=applicability
    )
    await configure(application, monkeypatch)
    response = await api.post(
        f"/v4/bid-submissions/{case['submission']}/presence-preparations",
        headers=headers,
        json={"review_id": case["source_review"]},
    )
    assert response.status_code == 409
    assert response.json()["data"]["error"]["code"] == "presence_required_locations_missing"
    async with application.state.db.transaction() as session:
        assert (await session.scalars(select(BidPresencePreparation))).first() is None
        assert (await session.scalars(select(BidPresenceImage))).first() is None
