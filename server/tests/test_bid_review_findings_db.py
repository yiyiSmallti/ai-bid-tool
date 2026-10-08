"""Slice 3b HTTP → real worker acceptance with synthetic, captured provider traffic.

Failure modes precede implementation in data/work/bid-review-findings/failure-modes.md.
The main session runs this module against its isolated PostgreSQL runtime; this
module never starts a service or reaches an external provider. Use the documented
--basetemp to retain repeatable sanitized request/coverage receipts.
"""

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from app.jobs.execution import JobExecution
from app.models.bid_review import BidDocumentPage
from app.models.bid_review_findings import (
    BidReviewFinding,
    BidReviewFindingEvent,
    BidReviewFindingSource,
)
from app.models.bid_review_run import BidReviewPublication
from app.models.entities import UsageRecord
from app.services import bid_review_privacy
from bid_signature_fixtures import gm_fixture, signed_pdf
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from test_bid_review_run_db import (
    BIDDER,
    PRICE,
    PRIVATE_FILENAME,
    REGISTERED,
    STAFF,
    artifact,
    assert_private_absent,
    authorization_body,
    data,
    document,
    preview,
    redaction,
    review_llm,
    submit,
    terminal,
)
from test_bid_review_signatures_db import mutation
from test_bid_review_upload_db import PDF, metadata, run, task, upload
from test_bid_review_upload_db import preview as prepare_preview
from test_bid_review_upload_db import submit as prepare_submit
from test_check_combined import install_resolver
from test_confidential_values import add_field, set_value
from test_task_budget_execution import set_limit
from test_team_workflow_membership import add_member, person, workflow

CLAUSES = (
    "★ Delivery must occur within thirty calendar days.",
    "▲ Technical support must be available every day.",
)
BID_RESPONSE = "Delivery will occur within thirty calendar days."
TECH_RESPONSE = "Technical support is available on weekdays only."
DOCUMENT_CLAUSE = "★ 必须提交资格证明文件，否则投标无效。"
SIGNATURE_CLAUSE = "★ 投标文件须有有效的电子签名，否则投标无效。"


class FindingsVendor:
    """One pinned extraction and explicit compliance outcome per supplied obligation."""

    def __init__(self, clauses=CLAUSES, *, outcome="responded", attack=None):
        self.clauses = clauses
        self.outcome = outcome
        self.attack = attack
        self.requests = []
        self.entered = None
        self.release = None

    async def __call__(self, request):
        envelope = json.loads(request.content)
        payload = json.loads(envelope["messages"][-1]["content"])
        self.requests.append(payload)
        if payload.get("operation") != "compliance":
            wire = {
                "obligations": [
                    {
                        "ref": row["ref"],
                        "quote": clause,
                        "category": "technical" if "Technical" in clause else "commercial",
                        "starred": "★" in clause,
                        "rejection_trigger": "无效" in clause or "★" in clause,
                    }
                    for row in payload["texts"]
                    for clause in self.clauses
                    if clause in row["text"]
                ],
                "signing_requirements": [
                    {
                        "candidate_ref": row["candidate_ref"],
                        "ref": row["text_ref"],
                        "quote": row["quote"],
                        "applicability": "applies",
                        "mark_types": ["pdf_digital_signature"],
                        "owner_roles": ["company"],
                        "date_required": False,
                        "location_rule": "specified",
                    }
                    for row in payload["candidates"]
                ],
                "observations": [],
            }
        else:
            if self.entered is not None:
                self.entered.set()
                await self.release.wait()
            obligation = payload["obligation"]
            quote = TECH_RESPONSE if "Technical" in obligation["quote"] else BID_RESPONSE
            located = next(
                (
                    row
                    for row in payload["texts"]
                    if row["ref"].startswith("b") and quote in row["text"]
                ),
                None,
            )
            outcome = self.outcome if located else "missing"
            answer = {
                "obligation_ref": obligation["ref"],
                "outcome": outcome,
                "confidence": 0.91,
                "explanation": "Synthetic finding based only on supplied pages.",
                "tender_references": [
                    {"ref": obligation["tender_ref"], "quote": obligation["quote"]}
                ],
                "bid_references": (
                    [{"ref": located["ref"], "quote": quote}]
                    if located and outcome not in {"missing", "unknown"}
                    else []
                ),
            }
            if self.attack == "fabricated":
                answer["bid_references"] = [
                    {"ref": located["ref"], "quote": "Fabricated evidence."}
                ]
            elif self.attack == "unsent":
                answer["bid_references"] = [{"ref": "b999999", "quote": quote}]
            elif self.attack == "role_swap":
                answer["bid_references"] = copy.deepcopy(answer["tender_references"])
            elif self.attack == "wrong_obligation":
                answer["obligation_ref"] = "o999999"
            elif self.attack == "no_bid_quote":
                answer["bid_references"] = []
            elif self.attack == "placeholder":
                answer["bid_references"] = [
                    {"ref": located["ref"], "quote": "{{secret.review_identity}}"}
                ]
            elif self.attack == "anchor_overflow":
                answer["bid_references"] = answer["bid_references"] * 21
            elif self.attack == "joined":
                answer["bid_references"] = [
                    {"ref": located["ref"], "quote": "Delivery will calendar days."}
                ]
            elif self.attack == "ambiguous":
                answer["bid_references"] = [{"ref": located["ref"], "quote": "Repeated fragment."}]
            wire = {"obligations": [], "signing_requirements": [], "observations": [answer]}
            if self.attack == "duplicate":
                wire["observations"].append(copy.deepcopy(answer))
        return httpx.Response(
            200,
            json={
                "model": "synthetic-check-model",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(wire)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )


async def make_case(
    api,
    headers,
    application,
    tenants,
    tmp_path,
    monkeypatch,
    *,
    vendor=None,
    bid_text=None,
    price=False,
    selected="all",
    signature=None,
    identity=True,
):
    vendor = vendor or FindingsVendor()
    install_resolver(monkeypatch, await review_llm(api, headers[0], tmp_path, vendor))
    task_id = await task(api, headers[0])
    field = await add_field(
        api, headers[0], "review_identity_" + uuid4().hex[:8], "Review identity", "other", "task"
    )
    await set_value(api, headers[0], field, REGISTERED, task=task_id)
    bid_text = bid_text if bid_text is not None else f"{BID_RESPONSE}\n{TECH_RESPONSE}"
    private_prefix = f"投标人：{BIDDER}\n法定代表人：{STAFF}\n{REGISTERED}\n" if identity else ""
    bid = document(
        [
            f"{private_prefix}{bid_text}\nRepeated fragment. Repeated fragment.",
            *([f"报价：{PRICE} 元"] if price else []),
        ]
    )
    if signature:
        fixture = gm_fixture(
            signer_purposes=("1.3.6.1.5.5.7.3.2",) if signature == "not_for_signing" else None
        )
        bid = signed_pdf(
            fixture,
            base=bid,
            subfilter="GM.sm2cms.detached",
            wrong_signature=signature == "invalid",
        )
        if signature == "modified":
            bid = mutation(bid, "incremental")
    files = [
        (PRIVATE_FILENAME + "-tender.pdf", PDF, document(["\n".join(vendor.clauses)])),
        (PRIVATE_FILENAME + "-bid.pdf", PDF, bid),
    ]
    uploaded = data(await upload(api, headers[0], task_id, files, metadata(files)))
    prep = data(await prepare_preview(api, headers[0], task_id, uploaded["id"]))
    queued = data(await prepare_submit(api, headers[0], task_id, uploaded["id"], prep))
    await run(application, tenants["orgs"][0], queued["job_id"])
    case = {"task": task_id, "submission": uploaded["id"], "vendor": vendor, "field": field}
    cleared = await redaction(api, headers[0], case)
    pages = [
        p
        for p in cleared["pages"]
        if p["outbound_eligible"] and (selected == "all" or p["role"] == "tender")
    ]
    grant = data(
        await api.post(
            f"/v4/bid-submissions/{case['submission']}/outbound-authorizations",
            headers=headers[0],
            json=authorization_body(cleared, pages=pages),
        )
    )
    case.update(cleared=cleared, grant=grant)
    return case


async def execute(api, headers, application, tenants, case):
    receipt = data(await preview(api, headers[0], case))
    assert not receipt["admission_blockers"], receipt
    accepted = data(await submit(api, headers[0], case, receipt))
    finished = await terminal(api, headers[0], application, tenants["orgs"][0], accepted)
    assert finished["status"] == "succeeded", finished
    case.update(receipt=receipt, accepted=accepted, finished=finished, review=accepted["review_id"])
    response = await api.get(f"/v4/bid-reviews/{case['review']}/findings", headers=headers[0])
    data(response)
    case["findings"] = response.json()["items"]
    return case


async def test_exact_authorized_inputs_and_locally_verified_two_sided_citations(
    api,
    headers,
    application,
    tenants,
    tmp_path,
    monkeypatch,
):
    case = await make_case(api, headers, application, tenants, tmp_path, monkeypatch, price=True)
    await execute(api, headers, application, tenants, case)
    calls = case["vendor"].requests
    assert any(p.get("operation") == "compliance" for p in calls)
    allowed = {p["sanitized_text"] for p in case["cleared"]["pages"] if p["outbound_eligible"]}
    for payload in calls:
        assert all(
            p["text"] in allowed
            if p["ref"].startswith("b")
            else any(p["text"] in authorized for authorized in allowed)
            for p in payload["texts"]
        )
        assert all(set(p) <= {"ref", "text", "role"} for p in payload["texts"])
        assert_private_absent(payload)
    assert case["findings"]
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        for finding in case["findings"]:
            for side, role in (("tender_support", "tender"), ("bid_support", "bid")):
                for citation in finding[side]:
                    page = await session.get(BidDocumentPage, UUID(citation["page_id"]))
                    assert page is not None and page.role == role
                    original = bid_review_privacy.open_value(
                        application.state.processor.settings, page, "text_encrypted"
                    )
                    assert (
                        original[citation["start_offset"] : citation["end_offset"]]
                        == citation["quote"]
                    )
                    assert citation["page"] == page.page
                    assert citation["page_id"] in {
                        p["page_id"] for p in case["cleared"]["pages"] if p["outbound_eligible"]
                    }
        assert await session.scalar(
            select(func.count())
            .select_from(UsageRecord)
            .where(UsageRecord.job_id == UUID(case["accepted"]["job_id"]))
        ) == len(calls)
    assert all(f["basis"]["kind"] in {"rule", "model"} for f in case["findings"])
    assert all(
        f["basis"]["confidence"] is not None
        for f in case["findings"]
        if f["basis"]["kind"] == "model"
    )
    artifact(
        tmp_path,
        "findings-citations",
        requests=calls,
        findings=case["findings"],
        job=case["finished"],
    )


@pytest.mark.parametrize(
    "attack",
    [
        "fabricated",
        "unsent",
        "role_swap",
        "wrong_obligation",
        "no_bid_quote",
        "placeholder",
        "joined",
        "ambiguous",
        "duplicate",
        "anchor_overflow",
    ],
)
async def test_invalid_or_unbound_bid_quotes_cannot_pass(
    attack,
    api,
    headers,
    application,
    tenants,
    tmp_path,
    monkeypatch,
):
    vendor = FindingsVendor(clauses=CLAUSES[:1], attack=attack)
    case = await make_case(api, headers, application, tenants, tmp_path, monkeypatch, vendor=vendor)
    await execute(api, headers, application, tenants, case)
    assert case["findings"] and all(f["outcome"] != "responded" for f in case["findings"])
    assert any(f["outcome"] == "unknown" for f in case["findings"])
    assert all(not f["bid_support"] for f in case["findings"])
    assert any(
        f["code"] == "starred_response_not_located" and f["basis"]["kind"] == "rule"
        for f in case["findings"]
    )
    assert "Fabricated evidence." not in json.dumps(case["findings"])
    assert case["finished"]["result"]["completion"] == "partial"
    artifact(tmp_path, "findings-quote-" + attack, findings=case["findings"])


@pytest.mark.parametrize(
    "price,selected,expected",
    [(False, "all", "missing"), (True, "all", "unknown"), (False, "tender", "unknown")],
)
async def test_absence_is_a_separate_exact_inventory_and_partial_search_stays_unknown(
    price,
    selected,
    expected,
    api,
    headers,
    application,
    tenants,
    tmp_path,
    monkeypatch,
):
    vendor = FindingsVendor(clauses=CLAUSES[:1], outcome="missing")
    case = await make_case(
        api,
        headers,
        application,
        tenants,
        tmp_path,
        monkeypatch,
        vendor=vendor,
        bid_text="No located delivery response.",
        price=price,
        selected=selected,
        identity=False,
    )
    await execute(api, headers, application, tenants, case)
    assert case["findings"]
    assert all(f["outcome"] == expected for f in case["findings"])
    for finding in case["findings"]:
        assert not finding["bid_support"]
        assert finding["absence_search"] is not None
        assert "quote" not in finding["absence_search"]
        if expected == "unknown":
            assert finding["absence_search"]["coverage"] == "partial"
            assert finding["absence_search"]["limitation_codes"]
    if selected == "tender":
        assert all(p.get("operation") != "compliance" for p in vendor.requests)
    artifact(tmp_path, "findings-absence-" + selected + str(price), findings=case["findings"])


async def test_required_document_kind_rule_uses_submission_manifest(
    api,
    headers,
    application,
    tenants,
    tmp_path,
    monkeypatch,
):
    vendor = FindingsVendor(clauses=(DOCUMENT_CLAUSE,), outcome="missing")
    case = await make_case(api, headers, application, tenants, tmp_path, monkeypatch, vendor=vendor)
    await execute(api, headers, application, tenants, case)
    found = next(f for f in case["findings"] if f["code"] == "required_document_missing")
    assert found["basis"]["kind"] == "rule" and found["severity"] == "fatal"
    assert found["absence_search"]["kind"] == "submission_inventory"
    assert found["absence_search"]["submission_id"] == case["submission"]
    assert found["absence_search"]["coverage"] == "complete_inventory"
    assert found["absence_search"]["inspected_bid_document_ids"] and not found["bid_support"]


async def test_cited_deviations_preserve_star_triangle_and_other_risk_grades(
    api,
    headers,
    application,
    tenants,
    tmp_path,
    monkeypatch,
):
    vendor = FindingsVendor(
        clauses=(*CLAUSES, "Warranty support must be included."), outcome="deviation"
    )
    case = await make_case(api, headers, application, tenants, tmp_path, monkeypatch, vendor=vendor)
    await execute(api, headers, application, tenants, case)
    models = [row for row in case["findings"] if row["basis"]["kind"] == "model"]
    assert [row["severity"] for row in models] == ["fatal", "high", "medium"]
    assert all(row["outcome"] == "deviation" and row["bid_support"] for row in models)
    report = data(await api.get(f"/v4/bid-reviews/{case['review']}", headers=headers[0]))
    assert report["run"]["coverage"]["compliance_obligations_assessed"] == 3
    assert report["run"]["coverage"]["bid_compliance"] == "assessed"
    artifact(tmp_path, "findings-risk-grades", findings=case["findings"], report=report)


@pytest.mark.parametrize("signature", ["invalid", "modified", "not_for_signing"])
async def test_signed_file_local_validation_rules_retain_tender_basis(
    signature,
    api,
    headers,
    application,
    tenants,
    tmp_path,
    monkeypatch,
):
    vendor = FindingsVendor(clauses=(SIGNATURE_CLAUSE,), outcome="missing")
    case = await make_case(
        api,
        headers,
        application,
        tenants,
        tmp_path,
        monkeypatch,
        vendor=vendor,
        signature=signature,
    )
    await execute(api, headers, application, tenants, case)
    found = [f for f in case["findings"] if f["code"] == "signature_validation_invalid"]
    assert found and all(f["basis"]["kind"] == "rule" for f in found)
    assert all(f["tender_support"][0]["quote"] == SIGNATURE_CLAUSE for f in found)
    assert_private_absent(case["vendor"].requests)


async def test_signature_defect_without_related_clause_does_not_invent_tender_basis(
    api,
    headers,
    application,
    tenants,
    tmp_path,
    monkeypatch,
):
    case = await make_case(
        api, headers, application, tenants, tmp_path, monkeypatch, signature="invalid"
    )
    await execute(api, headers, application, tenants, case)
    report = data(await api.get(f"/v4/bid-reviews/{case['review']}", headers=headers[0]))
    assert "signature_defect_without_cited_obligation" in report["run"]["uncovered_codes"]
    assert report["run"]["completion"] == "partial"
    assert all(row["code"] != "signature_validation_invalid" for row in case["findings"])


@pytest.mark.parametrize("stop", ["budget", "ceiling"])
async def test_stop_between_obligations_keeps_partial_results_and_unknown_unfinished(
    stop,
    api,
    headers,
    application,
    tenants,
    tmp_path,
    monkeypatch,
):
    if stop == "ceiling":
        application.state.processor.settings.job_max_vendor_calls = 2
    case = await make_case(api, headers, application, tenants, tmp_path, monkeypatch)
    if stop == "budget":
        original = JobExecution.admit
        admissions = 0

        async def exhausted(self, quote):
            nonlocal admissions
            admissions += 1
            if admissions == 3:
                await set_limit(api, headers[0], case["task"], "0.00028")
            return await original(self, quote)

        monkeypatch.setattr(JobExecution, "admit", exhausted)
    await execute(api, headers, application, tenants, case)
    assert len(case["vendor"].requests) == 2
    assert any(f["outcome"] == "responded" for f in case["findings"])
    assert any(f["outcome"] == "unknown" for f in case["findings"])
    shown = data(await api.get(f"/v4/bid-reviews/{case['review']}", headers=headers[0]))
    assert shown["run"]["completion"] == "partial"
    assert shown["run"]["uncovered_codes"]
    artifact(
        tmp_path,
        "findings-stop-" + stop,
        findings=case["findings"],
        report=shown,
        requests=case["vendor"].requests,
    )


@pytest.mark.parametrize("bound", ["FINDING_LIMIT", "PER_OBLIGATION_LIMIT"])
async def test_finding_overflow_rejects_whole_groups_with_explicit_coverage(
    bound,
    api,
    headers,
    application,
    tenants,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.bid_review_compliance." + bound, 1)
    vendor = FindingsVendor(outcome="missing" if bound == "PER_OBLIGATION_LIMIT" else "responded")
    case = await make_case(
        api, headers, application, tenants, tmp_path, monkeypatch, vendor=vendor, identity=False
    )
    await execute(api, headers, application, tenants, case)
    report = data(await api.get(f"/v4/bid-reviews/{case['review']}", headers=headers[0]))
    assert report["run"]["completion"] == "partial"
    assert "bid_review_finding_limit" in report["run"]["uncovered_codes"]
    assert report["run"]["coverage"]["compliance_unassessed_obligation_ids"]
    assert len(case["findings"]) <= 1
    artifact(tmp_path, "findings-bound-" + bound, report=report, findings=case["findings"])


async def test_cancellation_inside_compliance_fences_all_findings_but_accounts_call(
    api,
    headers,
    application,
    tenants,
    tmp_path,
    monkeypatch,
):
    vendor = FindingsVendor()
    vendor.entered, vendor.release = asyncio.Event(), asyncio.Event()
    case = await make_case(api, headers, application, tenants, tmp_path, monkeypatch, vendor=vendor)
    receipt = data(await preview(api, headers[0], case))
    accepted = data(await submit(api, headers[0], case, receipt))
    pending = asyncio.create_task(run(application, tenants["orgs"][0], accepted["job_id"]))
    try:
        await asyncio.wait_for(vendor.entered.wait(), 20)
        data(await api.post(f"/v4/jobs/{accepted['job_id']}/cancel", headers=headers[0]))
    finally:
        vendor.release.set()
        await pending
    finished = data(await api.get(f"/v4/jobs/{accepted['job_id']}", headers=headers[0]))
    assert finished["status"] == "cancelled"
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        assert await session.scalar(select(func.count()).select_from(BidReviewPublication)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageRecord)
                .where(UsageRecord.job_id == UUID(accepted["job_id"]))
            )
            == 2
        )
    artifact(tmp_path, "findings-cancelled", job=finished, requests=vendor.requests)


def decision_body(case, current, action="dismiss", **changes):
    return {
        "request_id": str(uuid4()),
        "action": action,
        "reason": "Synthetic responsible-human review reason.",
        "expected_revision": current["revision"],
        "expected_input_hash": case["receipt"]["input"]["input_hash"],
        "expected_decision_id": current.get("latest_decision_id", current.get("id")),
        **changes,
    }


async def classify(api, header, case, finding, domain="commercial"):
    body = decision_body(case, finding)
    del body["action"]
    body["review_domain"] = domain
    return await api.post(
        f"/v4/bid-reviews/{case['review']}/findings/{finding['id']}/classification",
        headers=header,
        json=body,
    )


async def reviewer(api, headers, tenants, admin_engine, case, role, *, domains=None):
    user, header = await person(api, admin_engine, tenants["orgs"][0], role=role)
    current = await workflow(api, headers[0], case["task"])
    response = await add_member(
        api,
        headers[0],
        case["task"],
        user,
        current["revision"],
        role="reviewer",
        domains=domains or ["technical" if role == "technical" else "commercial"],
    )
    data(response)
    return header


async def test_classification_responsible_domains_append_only_decisions_history_and_cas(
    api,
    headers,
    application,
    tenants,
    tmp_path,
    monkeypatch,
    admin_engine,
):
    case = await make_case(api, headers, application, tenants, tmp_path, monkeypatch)
    await execute(api, headers, application, tenants, case)
    finding = case["findings"][0]
    base = f"/v4/bid-reviews/{case['review']}/findings/{finding['id']}"
    bidder = await reviewer(api, headers, tenants, admin_engine, case, "bidder")
    technical = await reviewer(api, headers, tenants, admin_engine, case, "technical")
    unclassified = await api.post(
        base + "/decisions", headers=bidder, json=decision_body(case, finding)
    )
    assert unclassified.status_code in {403, 409}
    assert (await classify(api, bidder, case, finding)).status_code == 403
    first = data(await classify(api, headers[0], case, finding))
    assert first["action"] == "classify" and first["revision"] == 2
    current = {"revision": first["revision"], "latest_decision_id": first["id"]}
    for unauthorized in (headers[0], technical):
        denied = await api.post(
            base + "/decisions", headers=unauthorized, json=decision_body(case, current)
        )
        assert denied.status_code == 403, denied.text
    blank = await api.post(
        base + "/decisions", headers=bidder, json=decision_body(case, current, reason="   ")
    )
    assert blank.status_code in {400, 422}
    for stale in (
        {"expected_revision": 1},
        {"expected_input_hash": "0" * 64},
        {"expected_decision_id": None},
    ):
        response = await api.post(
            base + "/decisions", headers=bidder, json=decision_body(case, current, **stale)
        )
        assert response.status_code == 409, response.text
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        machine = await session.get(BidReviewFinding, UUID(finding["id"]))
        before = (
            machine.details_encrypted,
            machine.details_sha256,
            machine.severity,
            machine.outcome,
        )
    bodies = [
        decision_body(case, current, reason=f"Human reason {REGISTERED} {BIDDER} {STAFF}")
        for _ in range(2)
    ]
    raced = await asyncio.gather(
        *[api.post(base + "/decisions", headers=bidder, json=body) for body in bodies]
    )
    assert sorted(response.status_code for response in raced) == [200, 409]
    winner = next(response for response in raced if response.status_code == 200)
    dismissed = data(winner)
    assert dismissed["state"] == "dismissed" and dismissed["revision"] == 3
    winner_body = bodies[raced.index(winner)]
    replay = data(await api.post(base + "/decisions", headers=bidder, json=winner_body))
    assert replay["id"] == dismissed["id"]
    reopened = data(
        await api.post(
            base + "/decisions", headers=bidder, json=decision_body(case, dismissed, "reopen")
        )
    )
    confirmed = data(
        await api.post(
            base + "/decisions", headers=bidder, json=decision_body(case, reopened, "confirm")
        )
    )
    assert confirmed["state"] == "confirmed"
    history = await api.get(base + "/decisions", headers=bidder, params={"limit": 2})
    info = data(history)
    events = history.json()["items"]
    assert info["next_cursor"]
    next_page = await api.get(
        base + "/decisions", headers=bidder, params={"limit": 2, "cursor": info["next_cursor"]}
    )
    data(next_page)
    events += next_page.json()["items"]
    assert [e["action"] for e in events] == ["classify", "dismiss", "reopen", "confirm"]
    assert [e["revision"] for e in events] == [2, 3, 4, 5]
    assert all(e["immutable"] and e["actor_kind"] == "session" for e in events)
    cleared_history = await api.get(base + "/decisions", headers=technical)
    data(cleared_history)
    assert_private_absent(cleared_history.json())
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        machine = await session.get(BidReviewFinding, UUID(finding["id"]))
        assert (
            machine.details_encrypted,
            machine.details_sha256,
            machine.severity,
            machine.outcome,
        ) == before
    technical_finding = case["findings"][-1]
    classified = data(await classify(api, headers[0], case, technical_finding, "technical"))
    technical_base = f"/v4/bid-reviews/{case['review']}/findings/{technical_finding['id']}"
    allowed = await api.post(
        technical_base + "/decisions",
        headers=technical,
        json=decision_body(case, classified, "confirm"),
    )
    assert allowed.status_code == 200, allowed.text
    denied = await api.post(
        technical_base + "/decisions", headers=bidder, json=decision_body(case, classified)
    )
    assert denied.status_code == 403
    filtered = await api.get(
        f"/v4/bid-reviews/{case['review']}/findings",
        headers=bidder,
        params={"state": "confirmed", "severity": finding["severity"]},
    )
    data(filtered)
    assert filtered.json()["items"] and all(
        f["state"] == "confirmed" for f in filtered.json()["items"]
    )
    artifact(
        tmp_path,
        "findings-human-history",
        events=[{key: event[key] for key in ("id", "action", "revision")} for event in events],
        machine_hash=before[1],
    )


async def test_safe_tokens_cross_org_cross_task_stale_input_and_rls_immutability(
    api,
    headers,
    application,
    tenants,
    tmp_path,
    monkeypatch,
    admin_engine,
):
    case = await make_case(api, headers, application, tenants, tmp_path, monkeypatch)
    await execute(api, headers, application, tenants, case)
    finding = case["findings"][0]
    base = f"/v4/bid-reviews/{case['review']}"
    first = data(await classify(api, headers[0], case, finding))
    bidder = await reviewer(api, headers, tenants, admin_engine, case, "bidder")
    issued = data(
        await api.post(
            "/v4/tokens",
            headers=headers[0],
            json={
                "name": "Synthetic finding metadata token",
                "scopes": ["bid-review:read", "task:read"],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
    )
    token = {**headers[0], "Authorization": "Bearer " + issued["token"]}
    safe = await api.get(base + "/findings", headers=token)
    data(safe)
    assert safe.json()["items"]
    assert all(
        not (
            {"tender_support", "bid_support", "explanation", "reason", "absence_search", "title"}
            & set(f)
        )
        for f in safe.json()["items"]
    )
    for clause in (*CLAUSES, BID_RESPONSE, TECH_RESPONSE):
        assert clause not in safe.text
    assert (
        await api.get(base + f"/findings/{finding['id']}/decisions", headers=token)
    ).status_code == 403
    assert (
        await api.post(
            base + f"/findings/{finding['id']}/decisions",
            headers=token,
            json=decision_body(case, first),
        )
    ).status_code == 403
    for scope in ("bid-review:decide", "bid-review:classify"):
        denied = await api.post(
            "/v4/tokens",
            headers=headers[0],
            json={
                "name": "Forbidden human grant",
                "scopes": ["bid-review:read", scope],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        assert denied.status_code >= 400
    for suffix in ("/findings", f"/findings/{finding['id']}/decisions"):
        assert (await api.get(base + suffix, headers=headers[1])).status_code == 404
    for suffix, body in (
        ("decisions", decision_body(case, first)),
        (
            "classification",
            {
                k: v
                for k, v in decision_body(case, first, review_domain="commercial").items()
                if k != "action"
            },
        ),
    ):
        assert (
            await api.post(
                base + f"/findings/{finding['id']}/{suffix}", headers=headers[1], json=body
            )
        ).status_code == 404
    other = await make_case(api, headers, application, tenants, tmp_path, monkeypatch)
    await execute(api, headers, application, tenants, other)
    cross = await api.get(
        f"/v4/bid-reviews/{other['review']}/findings/{finding['id']}/decisions", headers=headers[0]
    )
    assert cross.status_code == 404
    tables = (BidReviewFinding, BidReviewFindingSource, BidReviewFindingEvent)
    async with application.state.db.transaction(tenants["orgs"][1]) as session:
        for model in tables:
            assert await session.scalar(select(func.count()).select_from(model)) == 0
    for model in tables:
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            own = await session.scalar(select(model).where(model.review_id == UUID(case["review"])))
            assert own is not None
            values = {column.name: getattr(own, column.name) for column in model.__table__.columns}
        for verb in ("UPDATE", "DELETE"):
            with pytest.raises(DBAPIError):
                async with application.state.db.transaction(tenants["orgs"][0]) as session:
                    statement = (
                        f"UPDATE {model.__tablename__} SET id=id WHERE id=:id"
                        if verb == "UPDATE"
                        else f"DELETE FROM {model.__tablename__} WHERE id=:id"
                    )
                    await session.execute(text(statement), {"id": values["id"]})
        values["id"], values["org_id"] = uuid4(), tenants["orgs"][1]
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction(tenants["orgs"][1]) as session:
                await session.execute(model.__table__.insert().values(**values))
    await set_value(api, headers[0], case["field"], REGISTERED + "Changed", task=case["task"])
    stale = await api.post(
        base + f"/findings/{finding['id']}/decisions",
        headers=bidder,
        json=decision_body(case, first),
    )
    assert stale.status_code == 409
    artifact(
        tmp_path, "findings-isolation", safe=safe.json(), tables=[m.__tablename__ for m in tables]
    )
