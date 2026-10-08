"""Synthetic HTTP → authorized text → accounted review worker acceptance.

Failure modes were written before implementation in
``data/work/bid-review-run/failure-modes.md``. No real documents or providers are
used. Run with ``--basetemp=data/work/bid-review-run/pytest`` to retain the
reproducible sanitized request/report receipts inside the worktree.
"""

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pymupdf
import pytest
from app.core.config import Settings
from app.core.security import TokenSigner
from app.jobs.execution import JobExecution
from app.models.bid_review_privacy import (
    BidOutboundAuthorization,
    BidOutboundAuthorizedPage,
    BidRedactedPage,
    BidRedactionSnapshot,
    BidReviewNameList,
)
from app.models.bid_review_run import (
    BidReviewObligation,
    BidReviewPublication,
    BidReviewRequiredLocation,
    BidReviewRun,
    BidReviewSigningRequirement,
)
from app.models.entities import AuditLog, Job, UsageRecord, VendorCall
from app.providers.base import ProviderFailure
from app.providers.llm import OpenAICompatibleExtractor
from app.schemas.bid_review_privacy import SanitizedPageRef, sanitized_context_sha256
from bid_signature_fixtures import signed_pdf, standard_fixture
from sqlalchemy import func, select, text
from test_bid_review_upload_db import (
    PDF,
    run,
    task,
    upload,
)
from test_bid_review_upload_db import (
    preview as prepare_preview,
)
from test_bid_review_upload_db import (
    result as free_result,
)
from test_bid_review_upload_db import (
    submit as prepare_submit,
)
from test_check_combined import install_resolver
from test_confidential_values import add_field, set_value
from test_task_budget_execution import set_limit
from test_team_workflow_membership import add_member, person, workflow

DATE = "2026-10-08"
REGISTERED = "SyntheticRegisteredIdentity943817"
BIDDER = "SyntheticBidderCompany729416"
STAFF = "SyntheticRepresentative618274"
PRICE = "937462.58"
SAFE_CLAUSE = "Delivery must occur within thirty calendar days."
SIGNING = "法定代表人签字并加盖公章，填写日期，每页电子签章。"
PRIVATE_FILENAME = "SyntheticPrivateFilename837192"
CERTIFICATE_SUBJECT = "SYNTHETIC Standard Signer"
SENSITIVE = (REGISTERED, BIDDER, STAFF, PRICE, PRIVATE_FILENAME, CERTIFICATE_SUBJECT)


class ReviewVendor:
    """Return exact citations from the captured request; mutate one failure at a time."""

    def __init__(
        self,
        *,
        attack=None,
        failure=None,
        applicability="unknown",
        location_rule="every_page",
        omit_signing=False,
    ):
        self.requests = []
        self.attack = attack
        self.failure = failure
        self.applicability = applicability
        self.location_rule = location_rule
        self.omit_signing = omit_signing
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def __call__(self, request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][-1]["content"])
        self.requests.append(payload)
        if self.entered is not None and self.release is not None:
            self.entered.set()
            await self.release.wait()
        if self.failure == "http_error":
            return httpx.Response(400, json={"error": {"message": REGISTERED}})
        if self.failure == "timeout":
            raise httpx.ReadTimeout(REGISTERED, request=request)
        obligations = []
        for row in payload["texts"]:
            if SAFE_CLAUSE in row["text"]:
                obligations.append(
                    {
                        "ref": row["ref"],
                        "quote": SAFE_CLAUSE,
                        "category": "substantive",
                        "starred": True,
                        "rejection_trigger": True,
                    }
                )
        signing = [
            {
                "candidate_ref": row["candidate_ref"],
                "ref": row["text_ref"],
                "quote": row["quote"],
                "applicability": self.applicability,
                "mark_types": ["company_seal", "legal_representative_signature"],
                "owner_roles": ["legal_representative"],
                "date_required": True,
                "location_rule": self.location_rule,
            }
            for row in payload["candidates"]
        ]
        if self.omit_signing:
            signing = []
        if self.attack and obligations:
            target = obligations[0]
            if self.attack == "unsent_ref":
                target["ref"] = "t999999"
            elif self.attack == "fabricated":
                target["quote"] = "Fabricated requirement without any source."
            elif self.attack == "joined":
                target["quote"] = "Delivery must occur calendar days."
            elif self.attack == "ambiguous":
                target["quote"] = "Repeated ambiguous phrase."
            elif self.attack == "placeholder":
                target["quote"] = "{{secret.review_identity}}"
            elif self.attack == "duplicate":
                obligations.append(copy.deepcopy(target))
            else:
                raise AssertionError(self.attack)
        content = json.dumps({"obligations": obligations, "signing_requirements": signing})
        if self.failure == "malformed":
            content = REGISTERED
        return httpx.Response(
            200,
            json={
                "model": "synthetic-check-model",
                "choices": [
                    {
                        "finish_reason": "length" if self.failure == "truncated" else "stop",
                        "message": {
                            "content": content,
                            **({"refusal": REGISTERED} if self.failure == "refused" else {}),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )


def review_llm(tmp_path, vendor):
    values = {
        "data_dir": tmp_path,
        "llm_provider": "openai",
        "llm_model": "synthetic-check-model",
        "llm_api_key": "synthetic-check-key",
        "llm_base_url": "https://semantic.example.test/v1",
        "llm_input_usd_per_mtok": 1,
        "llm_output_usd_per_mtok": 2,
        "llm_batch_chars": 100_000,
    }
    return OpenAICompatibleExtractor(
        Settings(**values), httpx.MockTransport(vendor), org_owned=True
    )


def document(lines, *, image=False):
    with pymupdf.open() as pdf:
        for content in lines:
            page = pdf.new_page()
            page.insert_text((35, 55), content, fontname="china-s", fontsize=10)
            if image:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
                pix.clear_with(127)
                page.insert_image(pymupdf.Rect(0, 90, 595, 842), stream=pix.tobytes("png"))
        return pdf.tobytes()


def data(response):
    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "ok",
        "command",
        "data",
        "items",
        "warnings",
        "cost",
        "duration_ms",
    }
    return response.json()["data"]


def assert_private_absent(value):
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    assert all(secret not in encoded for secret in SENSITIVE), encoded


async def prepared(
    api,
    headers,
    application,
    tenants,
    *,
    prepare=True,
    pages=1,
    mixed=False,
    signing=True,
    certificate=False,
):
    task_id = await task(api, headers[0])
    field = await add_field(api, headers[0], "review_identity", "Review identity", "other", "task")
    await set_value(api, headers[0], field, REGISTERED, task=task_id)
    # The signer certificate subject reaches the tender text only through the
    # certificate case, which proves it is masked from locally derived names.
    clauses = [SAFE_CLAUSE, *([SIGNING] if signing else [])]
    if certificate:
        clauses.append("Signed by SYNTHETIC Standard Signer.")
    tender = document(
        ["\n".join(clauses) + "\nRepeated ambiguous phrase. Repeated ambiguous phrase."] * pages
    )
    bid = document(
        [
            f"投标人：{BIDDER}\n法定代表人：{STAFF}\n{REGISTERED}\n{SAFE_CLAUSE}",
            f"报价：{PRICE} 元",
        ]
    )
    if certificate:
        _, builder = standard_fixture("ecdsa")
        bid = signed_pdf(base=bid, subfilter="adbe.pkcs7.detached", cms_builder=builder)
    files = [
        (PRIVATE_FILENAME + "-tender.pdf", PDF, tender),
        (PRIVATE_FILENAME + "-bid.pdf", PDF, bid),
    ]
    if mixed:
        files.append(
            (
                PRIVATE_FILENAME + "-mixed.pdf",
                PDF,
                document(["Native header over a scanned page."], image=True),
            )
        )
    uploaded = free_result(await upload(api, headers[0], task_id, files))
    if prepare:
        receipt = free_result(await prepare_preview(api, headers[0], task_id, uploaded["id"]))
        accepted = free_result(
            await prepare_submit(api, headers[0], task_id, uploaded["id"], receipt)
        )
        await run(application, tenants["orgs"][0], accepted["job_id"])
    return {"task": task_id, "submission": uploaded["id"], "field": field}


async def redaction(api, header, case):
    return data(
        await api.get(
            f"/v4/bid-submissions/{case['submission']}/redaction",
            headers=header,
            params={"limit": 50},
        )
    )


def authorization_body(preview, *, pages=None):
    selected = (
        pages
        if pages is not None
        else [row for row in preview["pages"] if row["outbound_eligible"]]
    )
    refs = [
        SanitizedPageRef.model_validate(
            {"page_id": row["page_id"], "sanitized_text_sha256": row["sanitized_text_sha256"]}
        )
        for row in selected
    ]
    return {
        "request_id": str(uuid4()),
        **{
            key: preview[key]
            for key in (
                "expected_revision",
                "expected_authorization_id",
                "expected_submission_manifest_sha256",
                "expected_preparation_input_hash",
                "expected_redaction_manifest_sha256",
                "provider_bindings_sha256",
            )
        },
        "authorized_sanitized_context_sha256": sanitized_context_sha256(refs),
        "pages": [row.model_dump(mode="json") for row in refs],
        "allow_external": True,
        "privacy_reviewed": True,
        "purposes": ["bid_review_text"],
        "allowed_capabilities": ["llm"],
        "reason": "Synthetic exact text review",
    }


async def authorize(api, header, case):
    cleared = await redaction(api, header, case)
    body = authorization_body(cleared)
    granted = data(
        await api.post(
            f"/v4/bid-submissions/{case['submission']}/outbound-authorizations",
            headers=header,
            json=body,
        )
    )
    return cleared, body, granted


async def revoke(api, header, case, grant):
    return await api.post(
        f"/v4/bid-submissions/{case['submission']}/outbound-authorizations/revoke",
        headers=header,
        json={
            "request_id": str(uuid4()),
            "expected_revision": grant["revision"],
            "expected_authorization_id": grant["id"],
            "reason": "Synthetic grant revocation",
        },
    )


async def preview(api, header, case, **changes):
    request_id = changes.pop("request_id", str(uuid4()))
    response = await api.post(
        f"/v4/tasks/{case['task']}/bid-reviews",
        headers=header,
        json={
            "request_id": request_id,
            "submission_id": case["submission"],
            "assessment_date": DATE,
            "dry_run": True,
            **changes,
        },
    )

    if response.status_code == 200:
        case["request_id"] = request_id
    return response


async def submit(api, header, case, receipt, **changes):
    return await api.post(
        f"/v4/tasks/{case['task']}/bid-reviews",
        headers=header,
        json={
            "request_id": case["request_id"],
            "submission_id": case["submission"],
            "assessment_date": DATE,
            "expected_input_hash": receipt["input"]["input_hash"],
            "preflight_token": receipt["preflight_token"],
            **changes,
        },
    )


async def terminal(api, header, application, org, accepted):
    async with application.state.db.transaction(org) as session:
        row = await session.scalar(
            select(BidReviewRun).where(BidReviewRun.job_id == UUID(accepted["job_id"]))
        )
        assert row is not None
        accepted["review_id"] = str(row.id)
    await run(application, org, accepted["job_id"])
    response = await api.get(f"/v4/jobs/{accepted['job_id']}", headers=header)
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def counts(application, org):
    async with application.state.db.transaction(org) as session:
        return {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (Job, AuditLog, UsageRecord, VendorCall)
        }


async def review_case(
    api, headers, application, tenants, tmp_path, monkeypatch, *, vendor=None, pages=1
):
    vendor = vendor or ReviewVendor()
    llm = review_llm(tmp_path, vendor)
    install_resolver(monkeypatch, llm)
    case = await prepared(api, headers, application, tenants, pages=pages)
    cleared, body, grant = await authorize(api, headers[0], case)
    case.update(vendor=vendor, llm=llm, cleared=cleared, body=body, grant=grant)
    return case


def artifact(tmp_path: Path, name: str, **payload):
    assert_private_absent(payload)
    target = tmp_path / f"bid-review-{name}.json"
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == json.loads(
        json.dumps(payload, default=str)
    )


async def test_authorized_sanitized_run_preview_worker_citations_and_budget(
    api, headers, tenants, application, tmp_path, monkeypatch, caplog
):
    case = await review_case(api, headers, application, tenants, tmp_path, monkeypatch)
    before = await counts(application, tenants["orgs"][0])
    storage_before = list(application.state.storage.root.rglob("*"))
    receipt = data(await preview(api, headers[0], case))
    assert receipt["admission_blockers"] == []
    assert_private_absent(case["cleared"])
    prices = [row for row in case["cleared"]["pages"] if row["price_page"]]
    assert prices and all(not row["outbound_eligible"] for row in prices)
    assert receipt["budget"]["planned_calls"] > 0
    assert case["vendor"].requests == []
    assert await counts(application, tenants["orgs"][0]) == before
    assert list(application.state.storage.root.rglob("*")) == storage_before
    accepted = data(await submit(api, headers[0], case, receipt))
    finished = await terminal(api, headers[0], application, tenants["orgs"][0], accepted)
    assert finished["status"] == "succeeded", finished
    shown = data(await api.get(f"/v4/bid-reviews/{accepted['review_id']}", headers=headers[0]))
    assert shown["run"]["completion"] == "partial"
    signing = data(
        await api.get(
            f"/v4/bid-reviews/{accepted['review_id']}",
            headers=headers[0],
            params={"section": "signing_requirements"},
        )
    )["signing_requirements"]
    assert shown["obligations"] and signing
    assert all(row["citation"]["quote"] == SAFE_CLAUSE for row in shown["obligations"])
    assert all(row["applicability"] == "unknown" for row in signing)
    assert all(row["location_rule"] == "every_page" for row in signing)
    allowed_text = {
        row["sanitized_text"] for row in case["cleared"]["pages"] if row["outbound_eligible"]
    }
    sent_text = {row["text"] for payload in case["vendor"].requests for row in payload["texts"]}
    assert sent_text and sent_text <= allowed_text
    assert_private_absent(case["vendor"].requests)
    assert_private_absent([receipt, accepted, finished, caplog.text])
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        usages = list(
            (
                await session.scalars(
                    select(UsageRecord).where(UsageRecord.job_id == UUID(accepted["job_id"]))
                )
            ).all()
        )
        calls = list(
            (
                await session.scalars(
                    select(VendorCall).where(VendorCall.job_id == UUID(accepted["job_id"]))
                )
            ).all()
        )
        assert len(usages) == len(calls) == len(case["vendor"].requests)
        assert all(row.charge == 0 and row.task_amount > 0 for row in usages)
        assert all(row.state == "completed" for row in calls)
    budget = data(await api.get(f"/v4/tasks/{case['task']}/budget", headers=headers[0]))["budget"]
    assert Decimal(budget["spent"]) > 0 and Decimal(budget["reserved"]) == 0
    artifact(
        tmp_path,
        "authorized",
        request_manifest=receipt["input"],
        requests=case["vendor"].requests,
        report=shown,
        budget=budget,
    )


@pytest.mark.parametrize("missing", ["preparation", "authorization", "provider"])
async def test_missing_prerequisites_are_write_free_preview_blockers(
    missing, api, headers, tenants, application, tmp_path, monkeypatch
):
    vendor = ReviewVendor()
    install_resolver(monkeypatch, review_llm(tmp_path, vendor))
    case = await prepared(api, headers, application, tenants, prepare=missing != "preparation")
    if missing == "provider":
        await authorize(api, headers[0], case)

        async def unavailable(*args, **kwargs):
            raise ProviderFailure(REGISTERED, code="provider_unavailable")

        monkeypatch.setattr("app.providers.llm.resolve_llm", unavailable)
    before = await counts(application, tenants["orgs"][0])
    receipt = data(await preview(api, headers[0], case))
    assert receipt["admission_blockers"]
    assert vendor.requests == [] and await counts(application, tenants["orgs"][0]) == before
    rejected = await submit(api, headers[0], case, receipt)
    assert rejected.status_code >= 400
    assert vendor.requests == []
    assert_private_absent([receipt, rejected.json()])


@pytest.mark.parametrize("mutation", ["revoke", "provider", "confidential", "names"])
async def test_queued_input_invalidation_blocks_dispatch_and_publication(
    mutation, api, headers, tenants, application, tmp_path, monkeypatch
):
    case = await review_case(api, headers, application, tenants, tmp_path, monkeypatch)
    receipt = data(await preview(api, headers[0], case))
    accepted = data(await submit(api, headers[0], case, receipt))
    if mutation == "revoke":
        data(await revoke(api, headers[0], case, case["grant"]))
    elif mutation == "provider":
        case["llm"].model = "synthetic-changed-model"
    elif mutation == "confidential":
        await set_value(api, headers[0], case["field"], REGISTERED + "Changed", task=case["task"])
    else:
        data(
            await api.post(
                f"/v4/bid-submissions/{case['submission']}/redaction/names",
                headers=headers[0],
                json={
                    "request_id": str(uuid4()),
                    "expected_revision": 0,
                    "bidder_names": [BIDDER],
                    "staff_names": [STAFF],
                },
            )
        )
    finished = await terminal(api, headers[0], application, tenants["orgs"][0], accepted)
    assert finished["status"] == "failed", finished
    assert case["vendor"].requests == []
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        assert await session.scalar(select(func.count()).select_from(BidReviewPublication)) == 0
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        for model in (UsageRecord, VendorCall):
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.job_id == UUID(accepted["job_id"]))
                )
                == 0
            )
    artifact(tmp_path, "invalidated-" + mutation, job=finished, requests=[])


@pytest.mark.parametrize(
    "attack", ["unsent_ref", "fabricated", "joined", "ambiguous", "placeholder", "duplicate"]
)
async def test_unverifiable_provider_citations_never_become_obligations(
    attack, api, headers, tenants, application, tmp_path, monkeypatch
):
    vendor = ReviewVendor(attack=attack)
    case = await review_case(
        api, headers, application, tenants, tmp_path, monkeypatch, vendor=vendor
    )
    receipt = data(await preview(api, headers[0], case))
    accepted = data(await submit(api, headers[0], case, receipt))
    finished = await terminal(api, headers[0], application, tenants["orgs"][0], accepted)
    assert finished["status"] in {"succeeded", "failed"}
    shown = await api.get(f"/v4/bid-reviews/{accepted['review_id']}", headers=headers[0])
    if shown.status_code == 200:
        report = data(shown)
        assert report["run"]["completion"] == "partial"
        if attack == "duplicate":
            assert len(report["obligations"]) == 1
        else:
            assert not report["obligations"]
        assert report["run"]["uncovered_codes"]
    else:
        assert shown.status_code == 404 and finished["status"] == "failed"
    assert_private_absent([finished, shown.json()])
    artifact(
        tmp_path, "citation-" + attack, job=finished, report=shown.json(), requests=vendor.requests
    )


@pytest.mark.parametrize("failure", ["http_error", "timeout", "refused", "truncated", "malformed"])
async def test_provider_failure_keeps_usage_or_unknown_holds_without_false_completion(
    failure, api, headers, tenants, application, tmp_path, monkeypatch, caplog
):
    vendor = ReviewVendor(failure=failure)
    case = await review_case(
        api, headers, application, tenants, tmp_path, monkeypatch, vendor=vendor
    )
    receipt = data(await preview(api, headers[0], case))
    accepted = data(await submit(api, headers[0], case, receipt))
    finished = await terminal(api, headers[0], application, tenants["orgs"][0], accepted)
    assert finished["status"] in {"failed", "succeeded"}
    shown = await api.get(f"/v4/bid-reviews/{accepted['review_id']}", headers=headers[0])
    if shown.status_code == 200:
        assert data(shown)["run"]["completion"] == "partial"
        assert not data(shown)["obligations"]
    else:
        assert shown.status_code == 404
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        usages = await session.scalar(
            select(func.count())
            .select_from(UsageRecord)
            .where(UsageRecord.job_id == UUID(accepted["job_id"]))
        )
        unresolved = await session.scalar(
            select(func.count())
            .select_from(VendorCall)
            .where(VendorCall.job_id == UUID(accepted["job_id"]), VendorCall.state == "unknown")
        )
        if failure == "timeout":
            assert unresolved > 0
        elif failure in {"refused", "truncated", "malformed"}:
            assert usages == len(vendor.requests) > 0
    assert_private_absent([finished, shown.json(), caplog.text])
    artifact(
        tmp_path, "failure-" + failure, job=finished, report=shown.json(), requests=vendor.requests
    )


async def test_duplicate_submission_receipt_gates_and_two_org_task_token_projection(
    api, headers, tenants, application, tmp_path, monkeypatch
):
    case = await review_case(api, headers, application, tenants, tmp_path, monkeypatch)
    receipt = data(await preview(api, headers[0], case))
    other = await task(api, headers[0])
    before = await counts(application, tenants["orgs"][0])
    for changes in ({"expected_input_hash": "0" * 64}, {"preflight_token": "invalid"}):
        assert (await submit(api, headers[0], case, receipt, **changes)).status_code >= 400
    signer = TokenSigner.for_tokens(application.state.processor.settings)
    opened = signer.open(receipt["preflight_token"])
    expired = signer.issue({key: value for key, value in opened.items() if key != "exp"}, -1)
    assert (
        await submit(api, headers[0], case, receipt, preflight_token=expired)
    ).status_code >= 400
    assert (await preview(api, headers[0], {**case, "task": other})).status_code == 404
    assert (await preview(api, headers[1], case)).status_code == 404
    assert await counts(application, tenants["orgs"][0]) == before
    request_id = case["request_id"]
    accepted = data(await submit(api, headers[0], case, receipt, request_id=request_id))
    replay = data(await submit(api, headers[0], case, receipt, request_id=request_id))
    assert replay["job_id"] == accepted["job_id"] and replay["cached"]
    assert (
        await submit(
            api, headers[0], case, receipt, request_id=request_id, assessment_date="2026-10-09"
        )
    ).status_code == 409
    issued = free_result(
        await api.post(
            "/v4/tokens",
            headers=headers[0],
            json={
                "name": "Synthetic review token",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "scopes": ["task:read", "job:read", "bid-review:read", "bid-review:run"],
            },
        )
    )
    token_header = {**headers[0], "Authorization": "Bearer " + issued["token"]}
    assert (
        await api.get(f"/v4/bid-submissions/{case['submission']}/redaction", headers=token_header)
    ).status_code == 403
    assert (
        await api.post(
            f"/v4/bid-submissions/{case['submission']}/outbound-authorizations",
            headers=token_header,
            json=case["body"],
        )
    ).status_code == 403
    finished = await terminal(api, headers[0], application, tenants["orgs"][0], accepted)
    token_view = await api.get(f"/v4/bid-reviews/{accepted['review_id']}", headers=token_header)
    assert token_view.status_code == 200, token_view.text
    assert_private_absent(token_view.json())
    assert SAFE_CLAUSE not in token_view.text and SIGNING not in token_view.text
    assert (
        await api.get(f"/v4/bid-reviews/{accepted['review_id']}", headers=headers[1])
    ).status_code == 404
    assert (
        await api.get(f"/v4/tasks/{case['task']}/bid-reviews", headers=headers[1])
    ).status_code == 404
    async with application.state.db.transaction(tenants["orgs"][1]) as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Job).where(Job.id == UUID(accepted["job_id"]))
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageRecord)
                .where(UsageRecord.job_id == UUID(accepted["job_id"]))
            )
            == 0
        )
    artifact(tmp_path, "duplicate-isolation", job=finished, safe_projection=token_view.json())


async def test_cancel_after_dispatch_retains_usage_but_publishes_no_review(
    api, headers, tenants, application, tmp_path, monkeypatch
):
    vendor = ReviewVendor()
    vendor.entered, vendor.release = asyncio.Event(), asyncio.Event()
    case = await review_case(
        api, headers, application, tenants, tmp_path, monkeypatch, vendor=vendor
    )
    receipt = data(await preview(api, headers[0], case))
    accepted = data(await submit(api, headers[0], case, receipt))
    worker = asyncio.create_task(run(application, tenants["orgs"][0], accepted["job_id"]))
    try:
        await asyncio.wait_for(vendor.entered.wait(), 15)
        data(await api.post(f"/v4/jobs/{accepted['job_id']}/cancel", headers=headers[0]))
    finally:
        vendor.release.set()
        await worker
    finished = data(await api.get(f"/v4/jobs/{accepted['job_id']}", headers=headers[0]))
    assert finished["status"] == "cancelled"
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        assert await session.scalar(select(func.count()).select_from(BidReviewPublication)) == 0
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageRecord)
                .where(UsageRecord.job_id == UUID(accepted["job_id"]))
            )
            == 1
        )
    artifact(tmp_path, "cancelled", job=finished, requests=vendor.requests)


async def test_live_task_budget_stop_preserves_completed_call_and_partial_coverage(
    api, headers, tenants, application, tmp_path, monkeypatch
):
    case = await review_case(api, headers, application, tenants, tmp_path, monkeypatch, pages=2)
    receipt = data(await preview(api, headers[0], case))
    accepted = data(await submit(api, headers[0], case, receipt))
    original = JobExecution.admit
    admissions = 0

    async def exhausted(self, quote):
        nonlocal admissions
        admissions += 1
        if admissions == 2:
            await set_limit(api, headers[0], case["task"], "0.00014")
        return await original(self, quote)

    monkeypatch.setattr(JobExecution, "admit", exhausted)
    finished = await terminal(api, headers[0], application, tenants["orgs"][0], accepted)
    assert len(case["vendor"].requests) == 1
    shown = data(await api.get(f"/v4/bid-reviews/{accepted['review_id']}", headers=headers[0]))
    assert shown["run"]["completion"] == "partial" and shown["obligations"]
    assert "task_budget_exceeded" in json.dumps(finished)
    budget = data(await api.get(f"/v4/tasks/{case['task']}/budget", headers=headers[0]))["budget"]
    assert Decimal(budget["spent"]) == Decimal("0.00014") and Decimal(budget["reserved"]) == 0
    artifact(
        tmp_path,
        "budget-stop",
        job=finished,
        report=shown,
        budget=budget,
        requests=case["vendor"].requests,
    )


async def test_shared_call_ceiling_cannot_publish_complete_review(
    api, headers, tenants, application, tmp_path, monkeypatch
):
    application.state.processor.settings.job_max_vendor_calls = 1
    case = await review_case(api, headers, application, tenants, tmp_path, monkeypatch, pages=2)
    receipt = data(await preview(api, headers[0], case))
    accepted = data(await submit(api, headers[0], case, receipt))
    finished = await terminal(api, headers[0], application, tenants["orgs"][0], accepted)
    assert len(case["vendor"].requests) == 1
    shown = await api.get(f"/v4/bid-reviews/{accepted['review_id']}", headers=headers[0])
    assert shown.status_code == 404 or data(shown)["run"]["completion"] == "partial"
    assert "limit" in json.dumps(finished)
    artifact(
        tmp_path,
        "call-ceiling",
        job=finished,
        report=shown.json(),
        requests=case["vendor"].requests,
    )


@pytest.mark.parametrize("role", ["bidder", "technical", "viewer"])
async def test_nonowner_task_participants_cannot_inspect_or_authorize_text(
    role, api, headers, tenants, application, tmp_path, monkeypatch, admin_engine
):
    case = await review_case(api, headers, application, tenants, tmp_path, monkeypatch)
    user, participant = await person(api, admin_engine, tenants["orgs"][0], role=role)
    current = await workflow(api, headers[0], case["task"])
    response = await add_member(api, headers[0], case["task"], user, current["revision"])
    assert response.status_code == 200, response.text
    for method, suffix, body in (
        ("get", "redaction", None),
        ("post", "outbound-authorizations", case["body"]),
        (
            "post",
            "redaction/names",
            {
                "request_id": str(uuid4()),
                "expected_revision": 0,
                "bidder_names": [BIDDER],
                "staff_names": [],
            },
        ),
        (
            "post",
            "outbound-authorizations/revoke",
            {
                "request_id": str(uuid4()),
                "expected_revision": case["grant"]["revision"],
                "expected_authorization_id": case["grant"]["id"],
                "reason": "Synthetic unauthorized revocation",
            },
        ),
    ):
        kwargs = {"json": body} if body is not None else {}
        rejected = await api.request(
            method,
            f"/v4/bid-submissions/{case['submission']}/{suffix}",
            headers=participant,
            **kwargs,
        )
        assert rejected.status_code == 403, rejected.text
        assert_private_absent(rejected.json())
    assert case["vendor"].requests == []


async def test_price_page_and_wrong_scope_hash_cannot_be_authorized(
    api, headers, tenants, application, tmp_path, monkeypatch
):
    case = await review_case(api, headers, application, tenants, tmp_path, monkeypatch)
    cleared = await redaction(api, headers[0], case)
    price = next(row for row in cleared["pages"] if row["price_page"])
    body = authorization_body(cleared, pages=[price])
    before = await counts(application, tenants["orgs"][0])
    rejected = await api.post(
        f"/v4/bid-submissions/{case['submission']}/outbound-authorizations",
        headers=headers[0],
        json=body,
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["data"]["error"]["code"] == "bid_price_page_excluded"
    mismatch = authorization_body(cleared)
    mismatch["authorized_sanitized_context_sha256"] = "0" * 64
    rejected = await api.post(
        f"/v4/bid-submissions/{case['submission']}/outbound-authorizations",
        headers=headers[0],
        json=mismatch,
    )
    assert rejected.status_code == 409, rejected.text
    assert await counts(application, tenants["orgs"][0]) == before
    assert case["vendor"].requests == []


@pytest.mark.parametrize(
    "applicability,location_rule,omit_signing",
    [
        ("applies", "every_page", False),
        ("applies", "seam_group", False),
        ("unknown", "every_page", False),
        ("alternative", "specified", False),
        ("not_applicable", "every_page", False),
        ("unknown", "unknown", True),
    ],
)
async def test_signing_coverage_never_turns_unchecked_locations_into_presence(
    applicability,
    location_rule,
    omit_signing,
    api,
    headers,
    tenants,
    application,
    tmp_path,
    monkeypatch,
):
    vendor = ReviewVendor(
        applicability=applicability, location_rule=location_rule, omit_signing=omit_signing
    )
    case = await review_case(
        api, headers, application, tenants, tmp_path, monkeypatch, vendor=vendor
    )
    receipt = data(await preview(api, headers[0], case))
    accepted = data(await submit(api, headers[0], case, receipt))
    finished = await terminal(api, headers[0], application, tenants["orgs"][0], accepted)
    report = data(
        await api.get(
            f"/v4/bid-reviews/{accepted['review_id']}",
            headers=headers[0],
            params={"section": "signing_requirements"},
        )
    )
    if applicability == "unknown" or location_rule == "specified" or omit_signing:
        assert report["run"]["completion"] == "partial"
    else:
        assert report["run"]["completion"] == "complete"
    assert report["run"]["coverage"]["signature_presence"] == "not_checked"
    assert report["run"]["coverage"]["bid_compliance"] == "not_implemented"
    requirements = report["signing_requirements"]
    assert requirements
    bid_pages = {row["page_id"] for row in case["cleared"]["pages"] if row["role"] == "bid"}
    for requirement in requirements:
        assert requirement["applicability"] == ("unknown" if omit_signing else applicability)
        assert all(row["status"] == "unresolved" for row in requirement["required_locations"])
        if applicability == "applies" and not omit_signing:
            assert {row["page_id"] for row in requirement["required_locations"]} == bid_pages
            if location_rule == "seam_group":
                assert all(row["group_id"] for row in requirement["required_locations"])
        elif applicability == "not_applicable":
            assert requirement["required_locations"] == []
        elif omit_signing:
            assert requirement["reason_code"] == "candidate_not_assessed"
    artifact(
        tmp_path,
        "signing-" + applicability + "-" + location_rule + str(omit_signing),
        job=finished,
        report=report,
        requests=vendor.requests,
    )


async def test_all_new_privacy_review_tables_hide_rows_without_org_or_under_org_b(
    api, headers, tenants, application, tmp_path, monkeypatch
):
    vendor = ReviewVendor(applicability="applies")
    case = await review_case(
        api, headers, application, tenants, tmp_path, monkeypatch, vendor=vendor
    )
    data(
        await api.post(
            f"/v4/bid-submissions/{case['submission']}/redaction/names",
            headers=headers[0],
            json={
                "request_id": str(uuid4()),
                "expected_revision": 0,
                "bidder_names": [],
                "staff_names": [],
            },
        )
    )
    await authorize(api, headers[0], case)
    receipt = data(await preview(api, headers[0], case))
    accepted = data(await submit(api, headers[0], case, receipt))
    finished = await terminal(api, headers[0], application, tenants["orgs"][0], accepted)
    assert finished["status"] == "succeeded"
    models = (
        BidReviewNameList,
        BidRedactionSnapshot,
        BidRedactedPage,
        BidOutboundAuthorization,
        BidOutboundAuthorizedPage,
        BidReviewRun,
        BidReviewObligation,
        BidReviewSigningRequirement,
        BidReviewRequiredLocation,
        BidReviewPublication,
    )
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        for model in models:
            assert await session.scalar(select(func.count()).select_from(model)) > 0, (
                model.__tablename__
            )
        assert_private_absent((await session.scalars(select(AuditLog.details))).all())
    for context in (None, tenants["orgs"][1]):
        async with application.state.db.transaction(context) as session:
            if context is None:
                await session.execute(text("SELECT set_config('app.current_org','',true)"))
            for model in models:
                assert await session.scalar(select(func.count()).select_from(model)) == 0, (
                    model.__tablename__
                )
    artifact(
        tmp_path, "table-isolation", job=finished, tables=[model.__tablename__ for model in models]
    )


async def test_native_header_over_scanned_page_does_not_authorize_mixed_pixels(
    api, headers, tenants, application, tmp_path, monkeypatch
):
    vendor = ReviewVendor()
    install_resolver(monkeypatch, review_llm(tmp_path, vendor))
    case = await prepared(api, headers, application, tenants, mixed=True)
    cleared = await redaction(api, headers[0], case)
    mixed = next(
        row
        for row in cleared["pages"]
        if "Native header over a scanned page." in row["sanitized_text"]
    )
    assert not mixed["outbound_eligible"]
    rejected = await api.post(
        f"/v4/bid-submissions/{case['submission']}/outbound-authorizations",
        headers=headers[0],
        json=authorization_body(cleared, pages=[mixed]),
    )
    assert rejected.status_code == 409, rejected.text
    assert vendor.requests == []
    assert_private_absent(rejected.json())


async def test_complete_clause_extraction_declares_later_review_stages_unavailable(
    api, headers, tenants, application, tmp_path, monkeypatch
):
    vendor = ReviewVendor()
    install_resolver(monkeypatch, review_llm(tmp_path, vendor))
    case = await prepared(api, headers, application, tenants, signing=False)
    await authorize(api, headers[0], case)
    receipt = data(await preview(api, headers[0], case))
    accepted = data(await submit(api, headers[0], case, receipt))
    finished = await terminal(api, headers[0], application, tenants["orgs"][0], accepted)
    report = data(await api.get(f"/v4/bid-reviews/{accepted['review_id']}", headers=headers[0]))
    assert report["run"]["completion"] == "complete"
    assert len(report["obligations"]) == 1
    coverage = report["run"]["coverage"]
    assert coverage["tender_pages_assessed"] == coverage["tender_pages_total"] == 1
    assert coverage["bid_compliance"] == "not_implemented"
    assert coverage["signature_presence"] == "not_checked"
    assert coverage["scoring"] == "not_requested" and coverage["clef"] == "not_implemented"
    artifact(tmp_path, "complete-extraction", job=finished, report=report, requests=vendor.requests)


async def test_local_signature_subject_is_masked_in_authorized_tender_context(
    api, headers, tenants, application, tmp_path, monkeypatch
):
    vendor = ReviewVendor()
    install_resolver(monkeypatch, review_llm(tmp_path, vendor))
    case = await prepared(api, headers, application, tenants, certificate=True)
    cleared, _, _ = await authorize(api, headers[0], case)
    assert_private_absent(cleared)
    assert any(
        "[REDACTED_NAME]" in row["sanitized_text"]
        for row in cleared["pages"]
        if row["role"] == "tender"
    )
    receipt = data(await preview(api, headers[0], case))
    accepted = data(await submit(api, headers[0], case, receipt))
    finished = await terminal(api, headers[0], application, tenants["orgs"][0], accepted)
    assert finished["status"] == "succeeded"
    assert_private_absent(vendor.requests)
    artifact(tmp_path, "certificate-subject", job=finished, requests=vendor.requests)


async def test_token_executes_only_current_human_authorized_snapshot_and_cannot_rebind_receipt(
    api, headers, tenants, application, tmp_path, monkeypatch
):
    vendor = ReviewVendor()
    install_resolver(monkeypatch, review_llm(tmp_path, vendor))
    case = await prepared(api, headers, application, tenants)
    issued = free_result(
        await api.post(
            "/v4/tokens",
            headers=headers[0],
            json={
                "name": "Synthetic authorized execution token",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "scopes": ["task:read", "job:read", "bid-review:read", "bid-review:run"],
            },
        )
    )
    token_header = {**headers[0], "Authorization": "Bearer " + issued["token"]}
    unauthorized = data(await preview(api, token_header, case))
    assert "outbound_authorization_required" in unauthorized["admission_blockers"]
    assert (await submit(api, token_header, case, unauthorized)).status_code == 409
    assert vendor.requests == []
    await authorize(api, headers[0], case)
    human_case = dict(case)
    human_receipt = data(await preview(api, headers[0], human_case))
    rebound = await submit(api, token_header, human_case, human_receipt)
    assert rebound.status_code == 409
    assert rebound.json()["data"]["error"]["code"] == "bid_preflight_mismatch"
    token_receipt = data(await preview(api, token_header, case))
    assert token_receipt["admission_blockers"] == []
    accepted = data(await submit(api, token_header, case, token_receipt))
    finished = await terminal(api, token_header, application, tenants["orgs"][0], accepted)
    assert finished["status"] == "succeeded", finished
    report = data(await api.get(f"/v4/bid-reviews/{accepted['review_id']}", headers=token_header))
    assert report["obligations"] == report["signing_requirements"] == []
    assert SAFE_CLAUSE not in json.dumps([token_receipt, finished, report])
    assert_private_absent([token_receipt, finished, report, vendor.requests])
    artifact(tmp_path, "token-authorized", job=finished, report=report, requests=vendor.requests)
