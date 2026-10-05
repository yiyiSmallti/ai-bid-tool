"""B09 rules-only check acceptance through the real API, worker and PostgreSQL.

Failure modes covered here are intentionally at the public workflow boundary:

* a dry run must not create a job, audit, usage row or check row and must not
  invoke the extraction provider again;
* submission is bound to the preview hash, while ``combined`` remains an
  explicit phase-two mode rather than a silent rules fallback;
* the published report covers starred gaps, commercial and technical negative
  deviations, unconfirmed response gaps, inclusive certificate dates and an
  unmapped certificate with unknown dates;
* candidate response text never reaches a manifest, job result, report or
  acceptance artifact;
* cancellation, explicit retry, live and expired leases, input changes and a
  stale attempt cannot publish a report outside the durable job boundary;
* only the responsible human session may append dismiss/reopen decisions;
  admins, tokens, workers, stale revisions and stale reports are rejected;
* every check route, including job status/cancel, hides another organization's
  resources as not found; and
* both PDF page citations and Word block citations survive API-to-worker
  publication without inventing a page number for Word.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import threading
from contextlib import asynccontextmanager, redirect_stdout
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.core.errors import ServiceError
from app.core.security import Secrets
from app.models.check import (
    CheckCertificate,
    CheckCertificateItem,
    CheckDecision,
    CheckFinding,
    CheckFindingCitation,
    CheckItem,
    CheckRun,
)
from app.models.entities import AuditLog, Chunk, Job, Requirement, UsageRecord
from app.models.exports import ExportRun
from app.models.response_cards import DraftRun, ResponseCardRevision
from app.schemas.check_contracts import FindingDecisionRequest
from app.schemas.contracts import Category
from app.services import check as check_service
from app.services import check_rules, drafts
from app.services.auth import ROLE_SCOPES, Identity
from bid_cli import main as cli
from bid_cli.client import Client, State
from conftest import FakeQueue
from fakes import FakeLLM
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from test_card_generation import token_header
from test_docx_extraction import word_tender
from test_response_cards import (
    CERTIFICATE_PAGE,
    TENDER_LINES,
    PhaseOneExtraction,
    card_action,
    create_card,
    create_tender,
    labelled_pdf,
    login,
    require_action,
    run_document_job,
    set_role,
)

ASSESSMENT_DATE = "2026-10-04"
CANDIDATE_MARKER = "PRIVATE UNCONFIRMED CANDIDATE MUST NOT LEAK"


class CheckExtraction(PhaseOneExtraction):
    """Make the synthetic fixture include substantive and second starred rules."""

    async def extract(self, chunks, schema):
        result = await super().extract(chunks, schema)
        for item in result.extraction.items:
            if item.source.page == 4:
                item.category = Category.substantive
                item.starred = False
            if item.source.page == 3:
                item.starred = True
        return result


@asynccontextmanager
async def check_client(tenants, tmp_path):
    provider = CheckExtraction()
    app = create_app(Settings(data_dir=tmp_path), llm=provider, queue=FakeQueue())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        headers = [
            await login(api, org, label)
            for org, label in zip(tenants["orgs"], ("a", "b"), strict=True)
        ]
        yield api, app, headers, provider


class LiveCheckClient(Client):
    """Route either CLI mode label to a fresh app using the real PostgreSQL state."""

    def __init__(self, mode: str, server: str, state: State, settings: Settings):
        super().__init__(mode, server, state)
        self.settings = settings

    @asynccontextmanager
    async def transport(self):
        application = create_app(self.settings, llm=FakeLLM(), queue=FakeQueue())
        async with (
            application.router.lifespan_context(application),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
                base_url="http://live-check",
            ) as transport,
        ):
            yield transport


def invoke_live_cli(arguments: list[str]) -> tuple[int, dict]:
    output = io.StringIO()
    exit_code = 0
    with redirect_stdout(output):
        try:
            cli.main([*arguments, "--json"])
        except SystemExit as exc:
            exit_code = int(exc.code)
    return exit_code, json.loads(output.getvalue())


async def select_certificate(
    api,
    header,
    task_id: str,
    tmp_path: Path,
    *,
    suffix: str,
    valid_from: str | None,
    valid_until: str | None,
    with_page: bool,
) -> tuple[dict, dict | None]:
    """Select a synthetic certificate and optionally archive one genuine PDF page."""
    data = {
        "kind": "qualification",
        "name": f"Synthetic {suffix} certificate declaration",
        "number": f"SYN-{suffix.upper()}",
        "valid_from": valid_from,
        "valid_until": valid_until,
    }
    created = await api.post("/resources/certificates", headers=header, json={"data": data})
    assert created.status_code == 200, created.text
    certificate = created.json()["data"]
    if with_page:
        path = tmp_path / f"synthetic-{suffix}-certificate.pdf"
        path.write_bytes(labelled_pdf([CERTIFICATE_PAGE]))
        uploaded = await api.post(
            f"/resources/certificates/{certificate['certificate_id']}/file-revisions",
            headers=header,
            data={"metadata": json.dumps({"expected_revision": 1, "data": data})},
            files={"file": (path.name, path.read_bytes(), "application/pdf")},
        )
        assert uploaded.status_code == 200, uploaded.text
    selected = await api.post(
        f"/tasks/{task_id}/certificates",
        headers=header,
        json={"certificate_id": certificate["certificate_id"]},
    )
    assert selected.status_code == 200, selected.text
    selection = selected.json()["data"]
    if not with_page:
        return selection, None
    source = await api.post(
        f"/tasks/{task_id}/evidence-sources",
        headers=header,
        json={"task_certificate_id": selection["id"], "page": 1},
    )
    assert source.status_code == 200, source.text
    return selection, source.json()["data"]["source"]


async def publish_draft(api, app, header, task_id: str, extraction_id: str) -> dict:
    queued = await api.post(
        f"/tasks/{task_id}/drafts",
        headers=header,
        json={"extraction_job_id": extraction_id},
    )
    assert queued.status_code == 200, queued.text
    job_id = queued.json()["data"]["job_id"]
    await app.state.processor(header["X-Org-Id"], job_id)
    status = await api.get(f"/jobs/{job_id}", headers=header)
    assert status.status_code == 200, status.text
    result = status.json()["data"]
    assert result["status"] == "succeeded", result
    return result["result"]


async def build_check_case(api, app, headers, provider, tenants, admin_engine, tmp_path):
    """Create one current draft containing every phase-one rule boundary."""
    header = headers[0]
    task, document, extraction, requirements = await create_tender(
        api, app, header, tmp_path, suffix="check"
    )
    assert requirements[0]["category"] == "scoring" and requirements[0]["starred"] is True
    assert requirements[2]["category"] == "technical" and requirements[2]["starred"] is True
    assert requirements[3]["category"] == "substantive" and requirements[3]["starred"] is False
    boundary_certificate, boundary_page = await select_certificate(
        api,
        header,
        task,
        tmp_path,
        suffix="boundary",
        valid_from="2026-01-01",
        valid_until=ASSESSMENT_DATE,
        with_page=True,
    )
    unknown_certificate, mapped_unknown_page = await select_certificate(
        api,
        header,
        task,
        tmp_path,
        suffix="mapped-unknown",
        valid_from=None,
        valid_until=None,
        with_page=True,
    )
    expired_certificate, expired_page = await select_certificate(
        api,
        header,
        task,
        tmp_path,
        suffix="expired",
        valid_from="2025-01-01",
        valid_until="2026-10-03",
        with_page=True,
    )
    future_certificate, future_page = await select_certificate(
        api,
        header,
        task,
        tmp_path,
        suffix="future",
        valid_from="2026-10-05",
        valid_until="2027-10-05",
        with_page=True,
    )
    unmapped_certificate, _ = await select_certificate(
        api,
        header,
        task,
        tmp_path,
        suffix="unmapped-unknown",
        valid_from=None,
        valid_until=None,
        with_page=False,
    )
    assert boundary_page is not None
    assert mapped_unknown_page is not None
    assert expired_page is not None
    assert future_page is not None

    commercial = await create_card(
        api,
        header,
        task,
        extraction,
        requirements[1],
        {
            "response_kind": "evidence",
            "response_text": "The supplied synthetic certificate is current through the check date.",
            "deviation": "negative",
            "deviation_note": "The certificate declaration does not establish every requested qualification.",
            "evidence": [
                {
                    "kind": "certificate_pdf_page",
                    "evidence_source_id": boundary_page["id"],
                    "quote": CERTIFICATE_PAGE,
                },
                {
                    "kind": "certificate_pdf_page",
                    "evidence_source_id": mapped_unknown_page["id"],
                    "quote": CERTIFICATE_PAGE,
                },
                {
                    "kind": "certificate_pdf_page",
                    "evidence_source_id": expired_page["id"],
                    "quote": CERTIFICATE_PAGE,
                },
                {
                    "kind": "certificate_pdf_page",
                    "evidence_source_id": future_page["id"],
                    "quote": CERTIFICATE_PAGE,
                },
            ],
        },
    )
    technical = await create_card(
        api,
        header,
        task,
        extraction,
        requirements[2],
        {
            "response_kind": "commitment",
            "response_text": "We commit to delivery within forty calendar days.",
            "deviation": "negative",
            "deviation_note": "The offered forty days exceeds the required thirty days.",
            "evidence": [],
        },
    )
    candidate = await create_card(
        api,
        header,
        task,
        extraction,
        requirements[3],
        {
            "response_kind": "commitment",
            "response_text": CANDIDATE_MARKER,
            "deviation": "none",
            "deviation_note": "Candidate content has not passed human review.",
            "evidence": [],
        },
    )
    reconfirmation = await create_card(
        api,
        header,
        task,
        extraction,
        requirements[4],
        {
            "response_kind": "commitment",
            "response_text": "We will provide a named support escalation contact.",
            "deviation": "none",
            "deviation_note": "The response follows the support obligation in the tender.",
            "evidence": [],
        },
    )
    assert candidate["state"] == "draft"

    set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
    commercial = await require_action(api, header, commercial, "submit")
    commercial = await require_action(
        api,
        header,
        commercial,
        "confirm",
        reviewed_evidence_ids=[row["id"] for row in commercial["evidence"]],
        reviewed_warning_codes=commercial["warning_codes"],
        reason="The synthetic certificate page was compared with its retained original.",
    )

    set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
    technical = await require_action(api, header, technical, "submit")
    technical = await require_action(api, header, technical, "confirm", reviewed_evidence_ids=[])
    reconfirmation = await require_action(api, header, reconfirmation, "submit")
    reconfirmation = await require_action(
        api, header, reconfirmation, "confirm", reviewed_evidence_ids=[]
    )
    repaired_quote = "support team shall provide a named escalation contact."
    assert repaired_quote in TENDER_LINES[4]
    # Reproduce the persisted outcome of citation repair without repeating that
    # feature's own API suite: the historical confirmed revision keeps its old
    # quote hash, while the new exact source remains uniquely locatable.
    with Session(admin_engine) as session, session.begin():
        requirement = session.get(Requirement, UUID(requirements[4]["id"]))
        assert requirement is not None
        requirement.quote = repaired_quote
    requirements[4]["source"]["quote"] = repaired_quote

    set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "admin")
    draft = await publish_draft(api, app, header, task, extraction)
    shown = await api.get(f"/drafts/{draft['draft_id']}", headers=header)
    assert shown.status_code == 200, shown.text
    draft_view = shown.json()["data"]
    assert draft_view["validity"] == "current"
    assert {row["requirement_id"] for row in draft_view["gaps"]} == {
        requirements[0]["id"],
        requirements[3]["id"],
        requirements[4]["id"],
    }
    gap_reasons = {row["requirement_id"]: row["reasons"] for row in draft_view["gaps"]}
    assert "unconfirmed" in gap_reasons[requirements[3]["id"]]
    assert "needs_reconfirmation" in gap_reasons[requirements[4]["id"]]
    assert CANDIDATE_MARKER not in shown.text
    return {
        "api": api,
        "app": app,
        "headers": headers,
        "provider": provider,
        "header": header,
        "task": task,
        "document": document,
        "extraction": extraction,
        "requirements": requirements,
        "draft": draft_view,
        "commercial_card": commercial,
        "technical_card": technical,
        "reconfirmation_card": reconfirmation,
        "candidate_card": candidate,
        "boundary_certificate": boundary_certificate,
        "unknown_certificate": unknown_certificate,
        "unmapped_certificate": unmapped_certificate,
        "expired_certificate": expired_certificate,
        "future_certificate": future_certificate,
    }


@pytest.fixture
async def check_case(tenants, tmp_path, admin_engine):
    async with check_client(tenants, tmp_path) as (api, app, headers, provider):
        yield await build_check_case(api, app, headers, provider, tenants, admin_engine, tmp_path)


async def row_counts(app, org_id: UUID) -> dict[str, int]:
    models = (
        Job,
        UsageRecord,
        AuditLog,
        CheckRun,
        CheckItem,
        CheckCertificate,
        CheckCertificateItem,
        CheckFinding,
        CheckFindingCitation,
        CheckDecision,
    )
    async with app.state.db.transaction(org_id) as session:
        return {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in models
        }


async def preview_check(case, assessment_date: str = ASSESSMENT_DATE) -> dict:
    response = await case["api"].post(
        f"/tasks/{case['task']}/checks",
        headers=case["header"],
        json={
            "draft_id": case["draft"]["id"],
            "assessment_date": assessment_date,
            "mode": "rules",
            "dry_run": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["command"] == "check run"
    return response.json()["data"]


async def submit_check(case, preview: dict, *, retry: bool = False):
    return await case["api"].post(
        f"/tasks/{case['task']}/checks",
        headers=case["header"],
        json={
            "draft_id": case["draft"]["id"],
            "assessment_date": preview["input"]["assessment_date"],
            "mode": "rules",
            "expected_input_hash": preview["input"]["input_hash"],
            "retry": retry,
        },
    )


async def process_check(case, receipt: dict) -> dict:
    job_id = receipt["job_id"]
    await case["app"].state.processor(case["header"]["X-Org-Id"], job_id)
    response = await case["api"].get(f"/jobs/{job_id}", headers=case["header"])
    assert response.status_code == 200, response.text
    return response.json()


def write_acceptance_artifact(tmp_path: Path, manifest: dict, report: dict) -> Path:
    configured = os.environ.get("BID_CHECK_ACCEPTANCE_DIR")
    output = Path(configured) if configured else tmp_path / "check-acceptance"
    output.mkdir(parents=True, exist_ok=True)
    (output / "input-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
    )
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    )
    report_id = report["data"]["report"]["id"]
    input_hash = report["data"]["report"]["input"]["input_hash"]
    (output / "commands.txt").write_text(
        "\n".join(
            [
                f"bid check run --task {manifest['task_id']} --draft {manifest['draft_id']} --as-of {manifest['assessment_date']} --dry-run --json",
                f"bid check run --task {manifest['task_id']} --draft {manifest['draft_id']} --as-of {manifest['assessment_date']} --expected-input-hash {input_hash} --wait --json",
                f"bid check show --id {report_id} --json",
                ".venv/bin/python -m pytest -q -p no:cacheprovider server/tests/test_check.py",
            ]
        )
        + "\n"
    )
    return output


async def test_rules_report_dry_run_hash_findings_certificates_and_pdf_artifact(
    check_case, tenants, tmp_path, monkeypatch, admin_engine
):
    case = check_case
    before = await row_counts(case["app"], tenants["orgs"][0])
    queue_calls = len(case["app"].state.queue.calls)
    provider_calls = case["provider"].calls
    preview = await preview_check(case)
    after = await row_counts(case["app"], tenants["orgs"][0])

    assert after == before
    assert len(case["app"].state.queue.calls) == queue_calls
    assert case["provider"].calls == provider_calls
    assert preview["mode"] == "rules"
    assert preview["estimated_cost"] == {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0}
    assert Decimal(preview["estimated_charge"]) == 0
    assert preview["cost_basis_reason"] == "no_model_calls"
    assert preview["provider_config_id"] is preview["model"] is preview["reasoning"] is None
    assert preview["gap_requirements"] == 3
    assert len(preview["selected_item_ids"]) == len(case["requirements"]) == 5
    assert "semantic_not_checked" in preview["limitations"]
    assert "unmapped_certificate_dates" in preview["limitations"]
    assert CANDIDATE_MARKER not in json.dumps(preview, ensure_ascii=False)

    invalid_requests = [
        {
            "draft_id": case["draft"]["id"],
            "assessment_date": ASSESSMENT_DATE,
            "mode": "rules",
        },
        {
            "draft_id": case["draft"]["id"],
            "assessment_date": ASSESSMENT_DATE,
            "mode": "rules",
            "reasoning": "high",
            "dry_run": True,
        },
        {
            "draft_id": case["draft"]["id"],
            "assessment_date": ASSESSMENT_DATE,
            "mode": "rules",
            "max_charge": "1.00",
            "dry_run": True,
        },
    ]
    for invalid_body in invalid_requests:
        invalid = await case["api"].post(
            f"/tasks/{case['task']}/checks",
            headers=case["header"],
            json=invalid_body,
        )
        assert invalid.status_code == 422, invalid.text
        assert invalid.json()["data"]["error"]["code"] == "invalid_input"
        assert invalid.json()["data"]["error"]["exit_code"] == 2

    combined = await case["api"].post(
        f"/tasks/{case['task']}/checks",
        headers=case["header"],
        json={
            "draft_id": case["draft"]["id"],
            "assessment_date": ASSESSMENT_DATE,
            "mode": "combined",
            "dry_run": True,
        },
    )
    assert combined.status_code == 400, combined.text
    assert combined.json()["data"]["error"]["code"] == "check_mode_unavailable"

    changed = await case["api"].post(
        f"/tasks/{case['task']}/checks",
        headers=case["header"],
        json={
            "draft_id": case["draft"]["id"],
            "assessment_date": ASSESSMENT_DATE,
            "mode": "rules",
            "expected_input_hash": "0" * 64,
        },
    )
    assert changed.status_code == 409, changed.text
    assert changed.json()["data"]["error"]["code"] == "check_input_changed"
    assert await row_counts(case["app"], tenants["orgs"][0]) == before

    submitted = await submit_check(case, preview)
    assert submitted.status_code == 200, submitted.text
    receipt = submitted.json()["data"]
    terminal = await process_check(case, receipt)
    status = terminal["data"]
    assert status["status"] == "succeeded", status
    result = status["result"]
    assert terminal["ok"] is False
    assert terminal["cost"] == {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0}
    assert result["completion"] == "partial"
    assert result["unassessed_requirements"] == 1
    assert result["usage_record_ids"] == []
    assert Decimal(result["charge"]) == 0
    assert CANDIDATE_MARKER not in json.dumps(terminal, ensure_ascii=False)
    assert case["provider"].calls == provider_calls
    queue_after_publish = len(case["app"].state.queue.calls)
    cached_response = await submit_check(case, preview)
    assert cached_response.status_code == 200, cached_response.text
    cached = cached_response.json()["data"]
    assert cached["cached"] is True and cached["job_id"] == receipt["job_id"]
    assert cached["status"] == "succeeded"
    assert len(case["app"].state.queue.calls) == queue_after_publish

    shown = await case["api"].get(f"/checks/{result['report_id']}", headers=case["header"])
    assert shown.status_code == 200, shown.text
    payload = shown.json()
    assert payload["ok"] is False and payload["command"] == "check show"
    assert set(payload) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    report = payload["data"]["report"]
    assert report["validity"] == "current" and report["mode"] == "rules"
    assert report["completion"] == "partial" and report["unassessed_count"] == 1
    assert report["usage_record_ids"] == [] and report["advisory_only"] is True
    assert len(payload["data"]["coverage"]) == 5
    assert all(row["semantic_status"] == "not_requested" for row in payload["data"]["coverage"])
    commercial_coverage = next(
        row
        for row in payload["data"]["coverage"]
        if row["requirement_id"] == case["requirements"][1]["id"]
    )
    certificate_outcomes = {
        observation["task_certificate_id"]: observation["outcome"]
        for observation in commercial_coverage["rules"]
        if observation.get("task_certificate_id")
    }
    assert certificate_outcomes == {
        case["boundary_certificate"]["id"]: "clear",
        case["unknown_certificate"]["id"]: "unknown",
        case["expired_certificate"]["id"]: "risk",
        case["future_certificate"]["id"]: "risk",
    }
    listed = await case["api"].get(
        f"/tasks/{case['task']}/checks", headers=case["header"], params={"limit": 1}
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"] == {
        "task_id": case["task"],
        "total": 1,
        "next_cursor": None,
    }
    assert [row["id"] for row in listed.json()["items"]] == [result["report_id"]]

    by_code: dict[str, list[dict]] = {}
    for finding in payload["items"]:
        by_code.setdefault(finding["code"], []).append(finding)
    assert {row["requirement_id"] for row in by_code["mandatory_response_missing"]} == {
        case["requirements"][0]["id"],
        case["requirements"][3]["id"],
    }
    assert {row["requirement_id"] for row in by_code["negative_deviation"]} == {
        case["requirements"][1]["id"],
        case["requirements"][2]["id"],
    }
    starred_negative = next(
        row
        for row in by_code["negative_deviation"]
        if row["requirement_id"] == case["requirements"][2]["id"]
    )
    assert starred_negative["severity"] == "disqualification_risk"
    assert {row["review_domain"] for row in by_code["negative_deviation"]} == {
        "commercial",
        "technical",
    }
    assert {row["requirement_id"] for row in by_code["unconfirmed_evidence"]} == {
        case["requirements"][3]["id"],
        case["requirements"][4]["id"],
    }
    assert {row["requirement_id"] for row in by_code["certificate_date_unknown"]} == {
        case["requirements"][1]["id"]
    }
    assert {row["requirement_id"] for row in by_code["certificate_expired"]} == {
        case["requirements"][1]["id"]
    }
    assert {row["requirement_id"] for row in by_code["certificate_not_yet_valid"]} == {
        case["requirements"][1]["id"]
    }
    assert all(finding["source"]["page"] for finding in payload["items"])
    negative_citations = [
        citation["citation"]
        for finding in by_code["negative_deviation"]
        for citation in finding["citations"]
    ]
    assert {citation["kind"] for citation in negative_citations} == {"tender", "draft"}
    assert {
        citation.get("field") for citation in negative_citations if citation["kind"] == "draft"
    } == {"deviation_note"}

    certificates = {row["task_certificate_id"]: row for row in payload["data"]["certificates"]}
    boundary = certificates[case["boundary_certificate"]["id"]]
    unknown = certificates[case["unknown_certificate"]["id"]]
    unmapped = certificates[case["unmapped_certificate"]["id"]]
    expired = certificates[case["expired_certificate"]["id"]]
    future = certificates[case["future_certificate"]["id"]]
    assert boundary["date_status"] == "valid"
    assert boundary["assessment_date"] == ASSESSMENT_DATE
    assert boundary["requirement_ids"] == [case["requirements"][1]["id"]]
    assert unknown["date_status"] == "unknown"
    assert unknown["requirement_ids"] == [case["requirements"][1]["id"]]
    assert unmapped["date_status"] == "unknown" and unmapped["requirement_ids"] == []
    assert expired["date_status"] == "expired"
    assert expired["requirement_ids"] == [case["requirements"][1]["id"]]
    assert future["date_status"] == "not_yet_valid"
    assert future["requirement_ids"] == [case["requirements"][1]["id"]]

    serialized = json.dumps(payload, ensure_ascii=False)
    assert CANDIDATE_MARKER not in serialized
    async with case["app"].state.db.transaction(tenants["orgs"][0]) as session:
        stored_run = await session.get(CheckRun, UUID(result["report_id"]))
        assert stored_run is not None
        fixed_manifest = stored_run.input_manifest
        stored_input_hash = stored_run.input_hash
        stored_job = await session.get(Job, UUID(receipt["job_id"]))
        assert stored_job is not None
        encrypted_input = stored_job.result["submission"]["encrypted_input"]
    decrypted_input = Secrets.for_data(case["app"].state.processor.settings).decrypt(
        encrypted_input
    )
    assert CANDIDATE_MARKER not in decrypted_input
    assert drafts.digest(fixed_manifest) == stored_input_hash
    assert payload["data"]["report"]["input"]["input_hash"] == stored_input_hash
    artifact = write_acceptance_artifact(tmp_path, fixed_manifest, payload)
    artifact_files = [
        artifact / "input-manifest.json",
        artifact / "report.json",
        artifact / "commands.txt",
    ]
    assert all(path.is_file() for path in artifact_files)
    saved_manifest = json.loads(artifact_files[0].read_text())
    saved_report = json.loads(artifact_files[1].read_text())
    saved_hash = saved_report["data"]["report"]["input"]["input_hash"]
    assert drafts.digest(saved_manifest) == saved_hash == stored_input_hash
    assert saved_manifest == fixed_manifest
    artifact_text = "\n".join(path.read_text() for path in artifact_files)
    assert CANDIDATE_MARKER not in artifact_text
    assert "Authorization" not in artifact_text
    assert case["header"]["Authorization"] not in artifact_text
    assert case["header"]["Authorization"].removeprefix("Bearer ") not in artifact_text
    assert case["task"] in artifact_text and result["report_id"] in artifact_text

    cli_settings = Settings(data_dir=tmp_path)
    monkeypatch.setenv("BID_SESSION", case["header"]["Authorization"].removeprefix("Bearer "))
    monkeypatch.setenv("BID_ORG", case["header"]["X-Org-Id"])
    monkeypatch.setattr(
        cli,
        "Client",
        lambda mode, server, state: LiveCheckClient(mode, server, state, cli_settings),
    )
    for mode in ("remote", "local"):
        common = ["--mode", mode, "--state", str(tmp_path / f"check-{mode}.enc")]
        run_code, run_body = await asyncio.to_thread(
            invoke_live_cli,
            [
                *common,
                "check",
                "run",
                "--task",
                case["task"],
                "--draft",
                case["draft"]["id"],
                "--as-of",
                ASSESSMENT_DATE,
                "--dry-run",
            ],
        )
        assert run_code == 0, run_body
        assert run_body["data"]["input"]["input_hash"] == preview["input"]["input_hash"]
        list_code, list_body = await asyncio.to_thread(
            invoke_live_cli,
            [*common, "check", "list", "--task", case["task"]],
        )
        assert list_code == 0, list_body
        assert [row["id"] for row in list_body["items"]] == [result["report_id"]]
        show_code, show_body = await asyncio.to_thread(
            invoke_live_cli,
            [*common, "check", "show", "--id", result["report_id"]],
        )
        assert show_code == 5, show_body
        assert show_body["data"]["report"]["completion"] == "partial"

    cli_finding = next(
        row
        for row in payload["items"]
        if row["code"] == "negative_deviation" and row["review_domain"] == "commercial"
    )
    set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
    remote = ["--mode", "remote", "--state", str(tmp_path / "check-remote.enc")]
    dismiss_code, dismiss_body = await asyncio.to_thread(
        invoke_live_cli,
        [
            *remote,
            "check",
            "decide",
            "--report",
            result["report_id"],
            "--finding",
            cli_finding["id"],
            "--action",
            "dismiss",
            "--expected-revision",
            "1",
            "--expected-input-hash",
            stored_input_hash,
            "--reason",
            "Synthetic remote CLI reviewer decision.",
        ],
    )
    assert dismiss_code == 0, dismiss_body
    assert dismiss_body["data"]["decision"]["revision"] == 2
    remote_history_code, remote_history = await asyncio.to_thread(
        invoke_live_cli,
        [
            *remote,
            "check",
            "history",
            "--report",
            result["report_id"],
            "--finding",
            cli_finding["id"],
        ],
    )
    assert remote_history_code == 0, remote_history
    assert [row["revision"] for row in remote_history["items"]] == [2]

    local = ["--mode", "local", "--state", str(tmp_path / "check-local.enc")]
    reopen_code, reopen_body = await asyncio.to_thread(
        invoke_live_cli,
        [
            *local,
            "check",
            "decide",
            "--report",
            result["report_id"],
            "--finding",
            cli_finding["id"],
            "--action",
            "reopen",
            "--expected-revision",
            "2",
            "--expected-input-hash",
            stored_input_hash,
            "--reason",
            "Synthetic local CLI reviewer decision.",
        ],
    )
    assert reopen_code == 0, reopen_body
    assert reopen_body["data"]["decision"]["revision"] == 3
    local_history_code, local_history = await asyncio.to_thread(
        invoke_live_cli,
        [
            *local,
            "check",
            "history",
            "--report",
            result["report_id"],
            "--finding",
            cli_finding["id"],
        ],
    )
    assert local_history_code == 0, local_history
    assert [row["revision"] for row in local_history["items"]] == [2, 3]


async def test_decisions_are_human_domain_cas_stale_safe_and_tenant_isolated(
    check_case, tenants, admin_engine
):
    case = check_case
    preview = await preview_check(case)
    submitted = await submit_check(case, preview)
    assert submitted.status_code == 200, submitted.text
    status = (await process_check(case, submitted.json()["data"]))["data"]
    report_id = status["result"]["report_id"]
    shown = (await case["api"].get(f"/checks/{report_id}", headers=case["header"])).json()
    technical = next(
        row
        for row in shown["items"]
        if row["code"] == "negative_deviation" and row["review_domain"] == "technical"
    )
    commercial = next(
        row
        for row in shown["items"]
        if row["code"] == "negative_deviation" and row["review_domain"] == "commercial"
    )
    draft_before_decisions = (
        await case["api"].get(f"/drafts/{case['draft']['id']}", headers=case["header"])
    ).json()["data"]
    async with case["app"].state.db.transaction(tenants["orgs"][0]) as session:
        gate_rows_before = {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (DraftRun, ResponseCardRevision, ExportRun)
        }
    decision = {
        "expected_revision": 1,
        "expected_input_hash": preview["input"]["input_hash"],
        "action": "dismiss",
        "reason": "The responsible reviewer checked the synthetic deviation.",
    }

    # An org administrator can run checks but does not inherit either professional domain.
    admin = await case["api"].post(
        f"/checks/{report_id}/findings/{technical['id']}/decisions",
        headers=case["header"],
        json=decision,
    )
    assert admin.status_code == 403, admin.text

    for forbidden_scope in ("check:decide", "evidence:confirm", "export"):
        forbidden_token = await case["api"].post(
            "/tokens",
            headers=case["header"],
            json={
                "name": f"Synthetic forbidden {forbidden_scope} token",
                "scopes": [forbidden_scope],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        assert forbidden_token.status_code == 403
    agent = await token_header(
        case["api"],
        case["header"],
        scopes=[
            "task:read",
            "draft:read",
            "card:read",
            "check:read",
            "check:run",
            "certificate:read",
            "certificate:file:read",
            "evidence:source:read",
            "resource:read",
        ],
    )
    assert (await case["api"].get(f"/checks/{report_id}", headers=agent)).status_code == 200
    token_decision = await case["api"].post(
        f"/checks/{report_id}/findings/{technical['id']}/decisions",
        headers=agent,
        json=decision,
    )
    assert token_decision.status_code == 403

    worker = Identity(
        tenants["users"][0],
        tenants["orgs"][0],
        set(ROLE_SCOPES["technical"]),
        "technical",
        actor_kind="worker",
    )
    async with case["app"].state.db.transaction(tenants["orgs"][0]) as session:
        with pytest.raises(ServiceError) as rejected:
            await check_service.decide_finding(
                session,
                worker,
                UUID(report_id),
                UUID(technical["id"]),
                FindingDecisionRequest.model_validate(decision),
                case["app"].state.storage,
                case["app"].state.processor.settings,
            )
    assert rejected.value.code == "human_session_required"

    set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
    wrong_domain = await case["api"].post(
        f"/checks/{report_id}/findings/{technical['id']}/decisions",
        headers=case["header"],
        json=decision,
    )
    assert wrong_domain.status_code == 403
    concurrent = await asyncio.gather(
        *[
            case["api"].post(
                f"/checks/{report_id}/findings/{commercial['id']}/decisions",
                headers=case["header"],
                json=decision,
            )
            for _ in range(2)
        ]
    )
    assert sorted(response.status_code for response in concurrent) == [200, 409]
    commercial_decision = next(response for response in concurrent if response.status_code == 200)
    assert commercial_decision.status_code == 200, commercial_decision.text
    decided = commercial_decision.json()["data"]
    assert decided["finding"]["status"] == "dismissed"
    assert decided["decision"]["revision"] == 2
    assert decided["decision"]["actor_kind"] == "session"

    conflict = next(response for response in concurrent if response.status_code == 409)
    assert conflict.json()["data"]["error"]["code"] == "revision_conflict"
    reopened_decision = await case["api"].post(
        f"/checks/{report_id}/findings/{commercial['id']}/decisions",
        headers=case["header"],
        json={**decision, "expected_revision": 2, "action": "reopen"},
    )
    assert reopened_decision.status_code == 200, reopened_decision.text
    assert reopened_decision.json()["data"]["finding"]["status"] == "open"
    assert reopened_decision.json()["data"]["decision"]["revision"] == 3
    history = await case["api"].get(
        f"/checks/{report_id}/findings/{commercial['id']}/decisions",
        headers=case["header"],
    )
    assert history.status_code == 200
    assert [row["revision"] for row in history.json()["items"]] == [2, 3]
    draft_after_decisions = (
        await case["api"].get(f"/drafts/{case['draft']['id']}", headers=case["header"])
    ).json()["data"]
    assert draft_after_decisions == draft_before_decisions
    async with case["app"].state.db.transaction(tenants["orgs"][0]) as session:
        gate_rows_after = {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (DraftRun, ResponseCardRevision, ExportRun)
        }
    assert gate_rows_after == gate_rows_before

    other = case["headers"][1]
    for method, path, body in (
        (
            "post",
            f"/tasks/{case['task']}/checks",
            {"draft_id": case["draft"]["id"], "assessment_date": ASSESSMENT_DATE, "dry_run": True},
        ),
        ("get", f"/tasks/{case['task']}/checks", None),
        ("get", f"/checks/{report_id}", None),
        (
            "post",
            f"/checks/{report_id}/findings/{commercial['id']}/decisions",
            {**decision, "expected_revision": 3, "action": "dismiss"},
        ),
        ("get", f"/checks/{report_id}/findings/{commercial['id']}/decisions", None),
        ("get", f"/jobs/{status['id']}", None),
        ("post", f"/jobs/{status['id']}/cancel", None),
    ):
        response = await getattr(case["api"], method)(
            path, headers=other, **({"json": body} if body is not None else {})
        )
        assert response.status_code == 404, (path, response.text)

    # A historical report remains readable after its draft inputs change, but
    # its findings can no longer receive decisions.
    set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
    reopened = await card_action(
        case["api"],
        case["header"],
        case["technical_card"],
        "reopen",
        reason="Change the current draft after publishing the check report.",
    )
    assert reopened.status_code == 200, reopened.text
    stale = await case["api"].get(f"/checks/{report_id}", headers=case["header"])
    assert stale.status_code == 200, stale.text
    assert stale.json()["data"]["report"]["validity"] == "stale"
    assert "check_input_changed" in stale.json()["warnings"]
    stale_decision = await case["api"].post(
        f"/checks/{report_id}/findings/{technical['id']}/decisions",
        headers=case["header"],
        json=decision,
    )
    assert stale_decision.status_code == 409
    assert stale_decision.json()["data"]["error"]["code"] == "check_input_changed"


async def test_check_job_cancel_retry_live_lease_takeover_and_changed_input(
    check_case, tenants, admin_engine
):
    case = check_case

    cancelled_preview = await preview_check(case, "2026-10-05")
    cancelled_response = await submit_check(case, cancelled_preview)
    assert cancelled_response.status_code == 200, cancelled_response.text
    cancelled = cancelled_response.json()["data"]
    cancel = await case["api"].post(f"/jobs/{cancelled['job_id']}/cancel", headers=case["header"])
    assert cancel.status_code == 200 and cancel.json()["data"]["status"] == "cancelled"
    await case["app"].state.processor(case["header"]["X-Org-Id"], cancelled["job_id"])
    retried_response = await submit_check(case, cancelled_preview, retry=True)
    assert retried_response.status_code == 200, retried_response.text
    retried = retried_response.json()["data"]
    assert retried["job_id"] == cancelled["job_id"] and retried["status"] == "queued"
    terminal = (await process_check(case, retried))["data"]
    assert terminal["status"] == "succeeded"

    lease_preview = await preview_check(case, "2026-10-06")
    lease_response = await submit_check(case, lease_preview)
    assert lease_response.status_code == 200, lease_response.text
    lease = lease_response.json()["data"]
    live_run = uuid4()
    async with case["app"].state.db.transaction(tenants["orgs"][0]) as session:
        job = await session.get(Job, UUID(lease["job_id"]))
        assert job is not None
        job.status = "running"
        job.run_id = live_run
        job.lease_until = datetime.now(UTC) + timedelta(minutes=5)
    queue_calls = len(case["app"].state.queue.calls)
    live_response = await submit_check(case, lease_preview, retry=True)
    assert live_response.status_code == 200, live_response.text
    assert live_response.json()["data"]["status"] == "running"
    assert len(case["app"].state.queue.calls) == queue_calls
    await case["app"].state.processor(case["header"]["X-Org-Id"], lease["job_id"])
    async with case["app"].state.db.transaction(tenants["orgs"][0]) as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CheckRun)
                .where(CheckRun.job_id == UUID(lease["job_id"]))
            )
            == 0
        )
        job = await session.get(Job, UUID(lease["job_id"]))
        assert job is not None
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    takeover_response = await submit_check(case, lease_preview, retry=True)
    assert takeover_response.status_code == 200, takeover_response.text
    takeover = takeover_response.json()["data"]
    assert takeover["status"] == "queued"
    takeover_terminal = (await process_check(case, takeover))["data"]
    assert takeover_terminal["status"] == "succeeded"

    changed_preview = await preview_check(case, "2026-10-07")
    changed_response = await submit_check(case, changed_preview)
    assert changed_response.status_code == 200, changed_response.text
    changed = changed_response.json()["data"]
    set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
    reopened = await card_action(
        case["api"],
        case["header"],
        case["technical_card"],
        "reopen",
        reason="Invalidate the fixed input before worker publication.",
    )
    assert reopened.status_code == 200, reopened.text
    changed_terminal = (await process_check(case, changed))["data"]
    assert changed_terminal["status"] == "failed"
    assert changed_terminal["error"]["code"] == "check_input_changed"
    async with case["app"].state.db.transaction(tenants["orgs"][0]) as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CheckRun)
                .where(CheckRun.job_id == UUID(changed["job_id"]))
            )
            == 0
        )


async def test_submit_recomputes_a_real_change_after_preview(check_case, tenants):
    case = check_case
    preview = await preview_check(case, "2026-10-08")
    provider_calls = case["provider"].calls
    async with case["app"].state.db.transaction(tenants["orgs"][0]) as session:
        jobs_before = await session.scalar(select(func.count()).select_from(Job))
        reports_before = await session.scalar(select(func.count()).select_from(CheckRun))
    changed = await case["api"].put(
        f"/tasks/{case['task']}/model-redaction",
        headers=case["header"],
        json={"expected_revision": 1, "model_redaction_enabled": False},
    )
    assert changed.status_code == 200, changed.text
    stale_submit = await submit_check(case, preview)
    assert stale_submit.status_code == 409, stale_submit.text
    assert stale_submit.json()["data"]["error"]["code"] == "check_input_changed"
    async with case["app"].state.db.transaction(tenants["orgs"][0]) as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == jobs_before
        assert await session.scalar(select(func.count()).select_from(CheckRun)) == reports_before
    assert case["provider"].calls == provider_calls


@pytest.mark.parametrize("change", ["card", "redaction", "confidential"])
async def test_worker_rechecks_changes_after_rules_evaluation(
    check_case, tenants, admin_engine, monkeypatch, change
):
    case = check_case
    field = None
    if change == "card":
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
    elif change == "confidential":
        created = await case["api"].post(
            "/confidential-fields",
            headers=case["header"],
            json={
                "key": "check_race_value",
                "label": "Synthetic check race value",
                "kind": "other",
                "scope": "task",
            },
        )
        assert created.status_code == 200, created.text
        field = created.json()["data"]
        value = await case["api"].post(
            f"/confidential-fields/{field['id']}/values",
            headers=case["header"],
            json={"value": "SYNTHETIC-FIRST-VALUE", "task_id": case["task"]},
        )
        assert value.status_code == 200, value.text

    preview = await preview_check(case, "2026-10-09")
    submitted = await submit_check(case, preview)
    assert submitted.status_code == 200, submitted.text
    receipt = submitted.json()["data"]
    provider_calls = case["provider"].calls
    entered, release = threading.Event(), threading.Event()
    original_evaluate = check_rules.evaluate

    def pause_after_evaluation(secret, draft_id):
        evaluated = original_evaluate(secret, draft_id)
        entered.set()
        assert release.wait(10), "test did not release the rules evaluator"
        return evaluated

    monkeypatch.setattr(check_rules, "evaluate", pause_after_evaluation)
    running = asyncio.create_task(
        case["app"].state.processor(case["header"]["X-Org-Id"], receipt["job_id"])
    )
    assert await asyncio.to_thread(entered.wait, 10), "worker did not reach rules evaluation"
    try:
        if change == "card":
            mutated = await card_action(
                case["api"],
                case["header"],
                case["technical_card"],
                "reopen",
                reason="Mutate a confirmed card after the worker snapshot.",
            )
        elif change == "redaction":
            mutated = await case["api"].put(
                f"/tasks/{case['task']}/model-redaction",
                headers=case["header"],
                json={"expected_revision": 1, "model_redaction_enabled": False},
            )
        else:
            assert field is not None
            mutated = await case["api"].post(
                f"/confidential-fields/{field['id']}/values",
                headers=case["header"],
                json={"value": "SYNTHETIC-SECOND-VALUE", "task_id": case["task"]},
            )
        assert mutated.status_code == 200, mutated.text
    finally:
        release.set()
    await running

    terminal = await case["api"].get(f"/jobs/{receipt['job_id']}", headers=case["header"])
    assert terminal.status_code == 200, terminal.text
    assert terminal.json()["data"]["status"] == "failed"
    assert terminal.json()["data"]["error"]["code"] == "check_input_changed"
    async with case["app"].state.db.transaction(tenants["orgs"][0]) as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CheckRun)
                .where(CheckRun.job_id == UUID(receipt["job_id"]))
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageRecord)
                .where(UsageRecord.job_id == UUID(receipt["job_id"]))
            )
            == 0
        )
    assert case["provider"].calls == provider_calls


async def test_unconfirmed_evidence_corruption_is_a_hard_input_failure(
    check_case, tenants, admin_engine
):
    case = check_case
    evidence_id = case["commercial_card"]["evidence"][0]["id"]
    with admin_engine.begin() as connection:
        # Simulate storage corruption that bypasses every gate trigger, including deferred ones.
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text(
                "UPDATE evidence SET confirmed_by=NULL, confirmed_at=NULL, "
                "quote_check='unreviewed_page' WHERE id=:id"
            ),
            {"id": UUID(evidence_id)},
        )

    before = await row_counts(case["app"], tenants["orgs"][0])
    provider_calls = case["provider"].calls
    rejected = await case["api"].post(
        f"/tasks/{case['task']}/checks",
        headers=case["header"],
        json={
            "draft_id": case["draft"]["id"],
            "assessment_date": "2026-10-10",
            "dry_run": True,
        },
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["data"]["error"]["code"] == "check_input_integrity"
    assert await row_counts(case["app"], tenants["orgs"][0]) == before
    assert case["provider"].calls == provider_calls


async def create_gap_document(api, app, header, tmp_path, *, kind: str):
    task = (
        await api.post("/tasks", headers=header, json={"name": f"Synthetic ambiguous {kind}"})
    ).json()["data"]["id"]
    if kind == "pdf":
        name = "ambiguous.pdf"
        content = labelled_pdf(["Synthetic citation target appears once before corruption."])
        content_type = "application/pdf"
    else:
        name = "ambiguous.docx"
        content = word_tender()
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    path = tmp_path / name
    path.write_bytes(content)
    uploaded = await api.post(
        f"/tasks/{task}/documents",
        headers=header,
        files={"file": (name, path.read_bytes(), content_type)},
    )
    assert uploaded.status_code == 200, uploaded.text
    document = uploaded.json()["data"]["id"]
    await run_document_job(api, app, header, document, "parse")
    extraction = await run_document_job(api, app, header, document, "extract")
    requirements = (
        await api.get(f"/tasks/{task}/requirements", headers=header, params={"job": extraction})
    ).json()["items"]
    assert requirements
    draft = await publish_draft(api, app, header, task, extraction)
    return task, requirements[0], draft["draft_id"]


async def test_pdf_and_word_ambiguous_sources_fail_before_report(tenants, tmp_path, admin_engine):
    provider = FakeLLM()
    app = create_app(Settings(data_dir=tmp_path), llm=provider, queue=FakeQueue())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        header = await login(api, tenants["orgs"][0], "a")
        cases = [
            await create_gap_document(api, app, header, tmp_path, kind=kind)
            for kind in ("pdf", "word")
        ]
        with Session(admin_engine) as session, session.begin():
            for _, public_requirement, _ in cases:
                requirement = session.get(Requirement, UUID(public_requirement["id"]))
                assert requirement is not None
                chunk = session.get(Chunk, requirement.chunk_id)
                assert chunk is not None
                if requirement.page is not None:
                    chunk.text = f"{chunk.text}\n{requirement.quote}"
                else:
                    blocks = [dict(block) for block in chunk.blocks]
                    target = next(
                        block
                        for block in blocks
                        if {key: value for key, value in block.items() if key != "text"}
                        == requirement.location
                    )
                    target["text"] = f"{target['text']}\n{requirement.quote}"
                    chunk.blocks = blocks

        before = await row_counts(app, tenants["orgs"][0])
        provider_calls = provider.calls
        for task, _, draft_id in cases:
            rejected = await api.post(
                f"/tasks/{task}/checks",
                headers=header,
                json={
                    "draft_id": draft_id,
                    "assessment_date": ASSESSMENT_DATE,
                    "dry_run": True,
                },
            )
            assert rejected.status_code == 409, rejected.text
            assert rejected.json()["data"]["error"]["code"] == "invalid_input_citation"
        assert await row_counts(app, tenants["orgs"][0]) == before
        assert provider.calls == provider_calls


async def test_word_tender_findings_keep_block_citations(tenants, tmp_path):
    provider = FakeLLM()
    app = create_app(Settings(data_dir=tmp_path), llm=provider, queue=FakeQueue())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        header = await login(api, tenants["orgs"][0], "a")
        task = (
            await api.post("/tasks", headers=header, json={"name": "Synthetic Word check"})
        ).json()["data"]["id"]
        uploaded = await api.post(
            f"/tasks/{task}/documents",
            headers=header,
            files={
                "file": (
                    "synthetic-tender.docx",
                    word_tender(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        document = uploaded.json()["data"]["id"]
        await run_document_job(api, app, header, document, "parse")
        extraction = await run_document_job(api, app, header, document, "extract")
        requirements = (
            await api.get(f"/tasks/{task}/requirements", headers=header, params={"job": extraction})
        ).json()["items"]
        starred = next(row for row in requirements if row["starred"])
        assert starred["source"]["page"] is None
        assert starred["source"]["location"]["kind"] == "cell"

        draft = await publish_draft(api, app, header, task, extraction)
        preview_response = await api.post(
            f"/tasks/{task}/checks",
            headers=header,
            json={
                "draft_id": draft["draft_id"],
                "assessment_date": ASSESSMENT_DATE,
                "dry_run": True,
            },
        )
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()["data"]
        submitted = await api.post(
            f"/tasks/{task}/checks",
            headers=header,
            json={
                "draft_id": draft["draft_id"],
                "assessment_date": ASSESSMENT_DATE,
                "expected_input_hash": preview["input"]["input_hash"],
            },
        )
        assert submitted.status_code == 200, submitted.text
        job_id = submitted.json()["data"]["job_id"]
        await app.state.processor(header["X-Org-Id"], job_id)
        status = (await api.get(f"/jobs/{job_id}", headers=header)).json()["data"]
        assert status["status"] == "succeeded", status
        shown = await api.get(f"/checks/{status['result']['report_id']}", headers=header)
        assert shown.status_code == 200, shown.text
        finding = next(
            row
            for row in shown.json()["items"]
            if row["requirement_id"] == starred["id"]
            and row["code"] == "mandatory_response_missing"
        )
        assert finding["source"]["page"] is None
        assert finding["source"]["location"] == starred["source"]["location"]
        [citation] = finding["citations"]
        assert citation["citation"]["kind"] == "tender"
        assert citation["citation"]["source"]["page"] is None
        assert citation["citation"]["source"]["location"] == starred["source"]["location"]
        assert provider.calls == 1  # extraction only; rules check makes no provider call
