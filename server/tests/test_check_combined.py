"""B09 combined-check acceptance through the public API and durable worker.

The synthetic model uses ``httpx.MockTransport``.  These scenarios keep the
semantic capability inside the normal resolver, accounting, lease and database
boundaries while making every outbound string and response reproducible.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import Settings
from app.jobs.execution import JobExecution
from app.models.check import CheckFinding, CheckFindingCitation, CheckItem, CheckRun
from app.models.entities import AuditLog, Job, UsageRecord, VendorCall
from app.providers.llm import OpenAICompatibleExtractor
from app.schemas.contracts import ProviderUsage
from app.services import drafts
from bid_cli import main as cli
from sqlalchemy import func, select, update
from test_check import ASSESSMENT_DATE, LiveCheckClient, invoke_live_cli, publish_draft
from test_check import check_case as check_case
from test_response_cards import create_card, create_tender, phase_one_client, require_action


class SemanticVendor:
    """Answer from the exact local IDs and refs sent by the check service."""

    def __init__(self, failures: dict[int, str] | None = None, *, attack: str | None = None):
        self.requests: list[dict] = []
        self.failures = failures or {}
        self.attack = attack
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        payload = json.loads(body["messages"][-1]["content"])
        self.requests.append(payload)
        number = len(self.requests)
        if self.entered is not None and self.release is not None:
            self.entered.set()
            await self.release.wait()
        failure = self.failures.get(number)
        if failure == "refused":
            return self.reply([], refusal="Synthetic refusal")
        if failure == "truncated":
            return self.reply([], finish_reason="length")
        refs = {row["ref"]: row["text"] for row in payload["context"]["texts"]}
        items = []
        for local in payload["requested_requirement_ids"]:
            tender_ref = f"{local}.tender"
            response_ref = f"{local}.response_text"
            if response_ref not in refs:
                items.append(
                    {"requirement_id": local, "status": "unknown", "citations": [], "findings": []}
                )
                continue
            items.append(
                {
                    "requirement_id": local,
                    "status": "no_risk_found",
                    "citations": [
                        {"ref": tender_ref, "quote": refs[tender_ref]},
                        {"ref": response_ref, "quote": refs[response_ref]},
                    ],
                    "findings": [],
                }
            )
        if self.attack and "r2" in payload["requested_requirement_ids"]:
            target = next(item for item in items if item["requirement_id"] == "r2")
            if self.attack == "missing":
                items.remove(target)
            elif self.attack == "duplicate":
                items.append(copy.deepcopy(target))
            elif self.attack == "unknown_id":
                unknown = copy.deepcopy(target)
                unknown["requirement_id"] = "r999"
                items.append(unknown)
            elif self.attack == "unsent_ref":
                target["citations"][1]["ref"] = "r2.unsent"
            elif self.attack == "wrong_ref":
                target["citations"][1]["quote"] = refs["r2.tender"]
            elif self.attack == "stitched_quote":
                target["citations"][1]["quote"] = "The supplied certificate is current"
            elif self.attack == "ambiguous_quote":
                target["citations"][1] = {"ref": "r2.e1", "quote": "certificate"}
            elif self.attack == "placeholder_quote":
                target["citations"][1]["quote"] = "{{secret.price}}"
            elif self.attack == "other_requirement":
                target["citations"][1] = {
                    "ref": "r3.response_text",
                    "quote": refs["r3.response_text"],
                }
            else:
                raise AssertionError(f"Unknown semantic attack: {self.attack}")
        return self.reply(items)

    @staticmethod
    def reply(items, *, refusal: str | None = None, finish_reason: str = "stop"):
        return httpx.Response(
            200,
            json={
                "model": "synthetic-check-model",
                "choices": [
                    {
                        "finish_reason": finish_reason,
                        "message": {
                            "content": json.dumps({"items": items}),
                            **({"refusal": refusal} if refusal else {}),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )


def semantic_llm(tmp_path, vendor: SemanticVendor, **changes):
    values = {
        "data_dir": tmp_path.data_dir if isinstance(tmp_path, Settings) else tmp_path,
        "llm_provider": "openai",
        "llm_model": "synthetic-check-model",
        "llm_api_key": "synthetic-check-key",
        "llm_base_url": "https://semantic.example.test/v1",
        "llm_batch_chars": 100000,
    }
    if isinstance(tmp_path, Settings):
        values.update(database_url=tmp_path.database_url, access_key=tmp_path.access_key)
    settings = Settings(**values | changes)
    return OpenAICompatibleExtractor(settings, httpx.MockTransport(vendor))


def install_resolver(monkeypatch, llm):

    async def resolved(session, settings, transport=None, job=None):
        return llm

    monkeypatch.setattr("app.providers.llm.resolve_llm", resolved)


async def combined_counts(case) -> dict[str, int | Decimal | None]:
    async with case["app"].state.db.transaction() as session:
        counts = {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (
                Job,
                AuditLog,
                UsageRecord,
                VendorCall,
                CheckRun,
                CheckItem,
                CheckFinding,
                CheckFindingCitation,
            )
        }
        return counts


def write_combined_artifact(tmp_path: Path, manifest: dict, report: dict, requests: list) -> Path:
    configured = os.environ.get("BID_CHECK_COMBINED_ACCEPTANCE_DIR")
    output = Path(configured) if configured else tmp_path / "check-combined-acceptance"
    output.mkdir(parents=True, exist_ok=True)
    files = {
        "input-manifest.json": manifest,
        "report.json": report,
        "provider-requests.json": requests,
    }
    for name, value in files.items():
        (output / name).write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    report_id = report["data"]["report"]["id"]
    input_hash = report["data"]["report"]["input"]["input_hash"]
    (output / "commands.txt").write_text(
        "\n".join(
            [
                f"bid check run --task {manifest['task_id']} --draft {manifest['draft_id']} --as-of {manifest['assessment_date']} --mode combined --reasoning high --dry-run --json",
                f"bid check run --task {manifest['task_id']} --draft {manifest['draft_id']} --as-of {manifest['assessment_date']} --mode combined --reasoning high --expected-input-hash {input_hash} --wait --json",
                f"bid check show --id {report_id} --json",
                ".venv/bin/python -m pytest -q -p no:cacheprovider server/tests/test_check_combined.py",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return output


async def combined_preview(case, **changes) -> tuple[httpx.Response, dict]:
    response = await case["api"].post(
        f"/tasks/{case['task']}/checks",
        headers=case["header"],
        json={
            "draft_id": case["draft"]["id"],
            "assessment_date": ASSESSMENT_DATE,
            "mode": "combined",
            "dry_run": True,
            **changes,
        },
    )
    return (response, response.json().get("data", {}))


async def submit_combined(case, preview: dict, **changes) -> httpx.Response:
    return await case["api"].post(
        f"/tasks/{case['task']}/checks",
        headers=case["header"],
        json={
            "draft_id": case["draft"]["id"],
            "assessment_date": ASSESSMENT_DATE,
            "mode": "combined",
            "expected_input_hash": preview["input"]["input_hash"],
            **changes,
        },
    )


async def run_combined(case, receipt: dict) -> dict:
    await case["app"].state.processor(receipt["job_id"])
    response = await case["api"].get(f"/jobs/{receipt['job_id']}", headers=case["header"])
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.fixture
async def combined_scope_case(database, admin_engine, tmp_path):
    async with phase_one_client(database, tmp_path) as (api, app, headers, _):
        header = headers
        task, _, extraction, requirements = await create_tender(
            api, app, header, tmp_path, suffix="check-scope", confirmed=True
        )
        disposition = await api.post(
            f"/tasks/{task}/cards/dispositions",
            headers=header,
            json={
                "extraction_job_id": extraction,
                "items": [
                    {
                        "requirement_id": requirements[3]["id"],
                        "expected_revision": None,
                        "disposition": "comply_only",
                        "reason": "The procedure is handled by a human disposition.",
                    }
                ],
            },
        )
        assert disposition.status_code == 200, disposition.text
        yield {
            "api": api,
            "app": app,
            "header": header,
            "headers": headers,
            "task": task,
            "extraction": extraction,
            "requirements": requirements,
        }


async def scope_draft(case, *, response: bool):
    if response:
        card = await create_card(
            case["api"],
            case["header"],
            case["task"],
            case["extraction"],
            case["requirements"][2],
            {
                "response_kind": "commitment",
                "response_text": "We deliver within thirty days.",
                "deviation": "none",
                "deviation_note": "The delivery commitment meets the required thirty calendar days.",
                "evidence": [],
            },
        )
        card = await require_action(case["api"], case["header"], card, "submit")
        await require_action(case["api"], case["header"], card, "confirm", reviewed_evidence_ids=[])
    result = await publish_draft(
        case["api"], case["app"], case["header"], case["task"], case["extraction"]
    )
    case["draft"] = {"id": result["draft_id"]}


@pytest.mark.parametrize("response", [True, False], ids=["mixed", "no-response"])
async def test_combined_scope_only_sends_confirmed_responses(
    combined_scope_case, tmp_path, monkeypatch, response
):
    case = combined_scope_case
    await scope_draft(case, response=response)
    vendor = SemanticVendor()
    install_resolver(
        monkeypatch, semantic_llm(tmp_path, vendor) if response else semantic_llm(tmp_path, vendor)
    )
    plans = []
    original_plan = JobExecution.plan

    def track_plan(self, calls):
        plans.append(calls)
        original_plan(self, calls)

    monkeypatch.setattr(JobExecution, "plan", track_plan)
    preview_response, preview = await combined_preview(case)
    assert preview_response.status_code == 200, preview_response.text
    assert preview["semantic_items"] == int(response)
    assert preview["selected_item_ids"] == [row["id"] for row in case["requirements"]]
    assert vendor.requests == []
    if not response:
        assert preview["estimated_cost"] == {
            "calls": 0,
            "llm_tokens": 0,
            "ocr_pages": 0,
            "search_requests": 0,
            "browser_units": 0,
            "unresolved_calls": 0,
        }
    queued = await submit_combined(case, preview)
    assert queued.status_code == 200, queued.text
    terminal = await run_combined(case, queued.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    result = terminal["result"]
    assert result["completion"] == "complete" and result["unassessed_requirements"] == 0
    shown = await case["api"].get(f"/checks/{result['report_id']}", headers=case["header"])
    assert shown.status_code == 200, shown.text
    assert shown.json()["ok"] is True
    coverage = shown.json()["data"]["coverage"]
    assert len(coverage) == len(case["requirements"])
    assert {row["partition"] for row in coverage} == (
        {"gap", "comply_only", "response"} if response else {"gap", "comply_only"}
    )
    for row in coverage:
        if row["partition"] == "response":
            assert row["semantic_status"] == "assessed"
            assert row["semantic_outcome"] == "no_risk_found"
        else:
            assert row["semantic_status"] == "not_requested"
            assert row["semantic_outcome"] is None and row["semantic_reason_code"] is None
            assert row["semantic_citations"] == []
    assert any(row["code"] == "mandatory_response_missing" for row in shown.json()["items"])
    assert plans[0] == max(plans) == int(response)
    assert len(vendor.requests) == len(result["usage_record_ids"]) == int(response)
    if response:
        payload = vendor.requests[0]
        assert payload["requested_requirement_ids"] == ["r3"]
        assert {row["ref"].split(".")[0] for row in payload["context"]["texts"]} == {"r3"}
    async with case["app"].state.db.transaction() as session:
        job_id = UUID(result["job_id"])
        for model in (VendorCall, UsageRecord):
            assert await session.scalar(
                select(func.count()).select_from(model).where(model.job_id == job_id)
            ) == int(response)
        stored = await session.get(CheckRun, UUID(result["report_id"]))
        assert stored is not None
        artifact = tmp_path / f"check-scope-{response}.json"
        artifact.write_text(
            json.dumps(
                {
                    "preview": preview,
                    "manifest": stored.input_manifest,
                    "report": shown.json(),
                    "requests": vendor.requests,
                    "planned_calls": plans,
                },
                indent=2,
            )
        )
        assert json.loads(artifact.read_text())["report"] == shown.json()


@pytest.mark.parametrize("partition", ["gap", "comply_only"])
@pytest.mark.parametrize("mutation", ["assessed", "unassessed", "finding", "citation"])
async def test_combined_nonresponse_storage_rejects_semantic_content(
    combined_scope_case, tmp_path, monkeypatch, partition, mutation
):
    from app.services import check

    case = combined_scope_case
    await scope_draft(case, response=True)
    install_resolver(monkeypatch, semantic_llm(tmp_path, SemanticVendor()))
    response, preview = await combined_preview(case)
    assert response.status_code == 200, response.text
    original_publish = check.publish

    async def forged_publish(session, actor, job, fixed, evaluated, settings, stop_reason):
        target = next(row for row in evaluated if row["item"]["partition"] == partition)
        assessed = next(row for row in evaluated if row["item"]["partition"] == "response")
        if mutation == "assessed":
            target.update(
                semantic_status="assessed",
                semantic_outcome="no_risk_found",
                semantic_reason_code=None,
            )
        elif mutation == "unassessed":
            target.update(
                semantic_status="unassessed",
                semantic_outcome="unknown",
                semantic_reason_code="semantic_unknown",
                unassessed=True,
            )
        elif mutation == "finding":
            target["findings"].append(
                {
                    "method": "semantic",
                    "code": "insufficient_support",
                    "severity": "info",
                    "reason": "Forged semantic finding.",
                    "citations": [],
                }
            )
        else:
            target["semantic_citations"] = copy.deepcopy(assessed["semantic_citations"])
        return await original_publish(session, actor, job, fixed, evaluated, settings, stop_reason)

    monkeypatch.setattr(check, "publish", forged_publish)
    queued = await submit_combined(case, preview)
    assert queued.status_code == 200, queued.text
    terminal = await run_combined(case, queued.json()["data"])
    assert terminal["status"] == "failed", terminal
    async with case["app"].state.db.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(CheckRun)) == 0


async def test_combined_old_pending_scope_requires_new_preview(
    combined_scope_case, tmp_path, monkeypatch
):
    case = combined_scope_case
    await scope_draft(case, response=True)
    vendor = SemanticVendor()
    install_resolver(monkeypatch, semantic_llm(tmp_path, vendor))
    response, preview = await combined_preview(case)
    assert response.status_code == 200, response.text
    queued = await submit_combined(case, preview)
    assert queued.status_code == 200, queued.text
    job_id = UUID(queued.json()["data"]["job_id"])
    before = await combined_counts(case)
    async with case["app"].state.db.transaction() as session:
        prerequisite_calls = (await session.scalars(select(VendorCall))).all()
        assert len(prerequisite_calls) == 1
        assert prerequisite_calls[0].job_id == UUID(case["extraction"])
        assert prerequisite_calls[0].state == "completed"
        job = await session.get(Job, job_id)
        assert job is not None
        submitted = copy.deepcopy(job.result["submission"])
        submitted["input_manifest"]["rule_version"] = "check-rules-v1"
        submitted["input_hash"] = drafts.digest(submitted["input_manifest"])
        job.result = {"submission": submitted}
    terminal = await run_combined(case, queued.json()["data"])
    assert terminal["status"] == "failed", terminal
    assert terminal["error"]["code"] == "check_input_changed"
    assert vendor.requests == []
    after = await combined_counts(case)
    for key in ("vendor_calls", "usage_records"):
        assert after[key] == before[key], key
    async with case["app"].state.db.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(CheckRun)) == 0
        for model in (VendorCall, UsageRecord):
            assert (
                await session.scalar(
                    select(func.count()).select_from(model).where(model.job_id == job_id)
                )
                == 0
            )
        retained = (await session.scalars(select(VendorCall))).all()
        assert [call.id for call in retained] == [call.id for call in prerequisite_calls]
        assert retained[0].state == "completed"
    artifact = tmp_path / "rejected-check-accounting.json"
    artifact.write_text(
        json.dumps(
            {
                "job_id": str(job_id),
                "prerequisite_call_ids": [str(call.id) for call in prerequisite_calls],
                "error_code": terminal["error"]["code"],
                "vendor_requests": vendor.requests,
                "before": {key: str(before[key]) for key in ("vendor_calls", "usage_records")},
                "after": {key: str(after[key]) for key in ("vendor_calls", "usage_records")},
            },
            indent=2,
        )
        + "\n"
    )


async def test_combined_dry_run_worker_verified_outcomes_and_usage(
    check_case, database, tmp_path, monkeypatch
):
    case = check_case
    vendor = SemanticVendor()
    llm = semantic_llm(tmp_path, vendor)
    llm.reasoning_levels = {
        "high": {"label": "Synthetic high", "request_options": {"reasoning_effort": "high"}}
    }
    llm.default_reasoning = "high"
    install_resolver(monkeypatch, llm)
    before = await combined_counts(case)
    response, preview = await combined_preview(case, reasoning="high")
    assert response.status_code == 200, response.text
    after_preview = await combined_counts(case)
    assert after_preview == before
    assert vendor.requests == []
    assert preview["mode"] == "combined"
    assert preview["semantic_items"] == 2
    assert preview["admission_blocker"] is None
    assert preview["provider_source"] == "local"
    assert preview["reasoning"] == "high"
    mismatch = await case["api"].post(
        f"/tasks/{case['task']}/checks",
        headers=case["header"],
        json={
            "draft_id": case["draft"]["id"],
            "assessment_date": ASSESSMENT_DATE,
            "mode": "combined",
            "expected_input_hash": "0" * 64,
        },
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["data"]["error"]["code"] == "check_input_changed"
    assert await combined_counts(case) == before
    queued = await submit_combined(case, preview, reasoning="high")
    assert queued.status_code == 200, queued.text
    terminal = await run_combined(case, queued.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    result = terminal["result"]
    assert result["completion"] == "partial"
    assert result["unassessed_requirements"] == 1
    assert len(result["usage_record_ids"]) == len(vendor.requests) == 1
    shown = await case["api"].get(f"/checks/{result['report_id']}", headers=case["header"])
    assert shown.status_code == 200, shown.text
    coverage = shown.json()["data"]["coverage"]
    assessed = [row for row in coverage if row["semantic_status"] == "assessed"]
    deterministic = [row for row in coverage if row["semantic_status"] == "not_requested"]
    assert len(assessed) == 2 and len(deterministic) == 3
    assert {row["requirement_id"] for row in deterministic} == {
        case["requirements"][index]["id"] for index in (0, 3, 4)
    }
    rules_unknown_ids = {
        row["requirement_id"]
        for row in coverage
        if any(rule["outcome"] == "unknown" for rule in row["rules"])
    }
    commercial_id = case["requirements"][1]["id"]
    assert rules_unknown_ids == {commercial_id}
    commercial = next(row for row in coverage if row["requirement_id"] == commercial_id)
    assert commercial["semantic_outcome"] == "no_risk_found"
    assert any(
        rule["code"] == "certificate_date_unknown"
        and rule["outcome"] == "unknown"
        and (rule["reason_code"] == "declared_dates_unknown")
        and (rule["task_certificate_id"] == case["unknown_certificate"]["id"])
        for rule in commercial["rules"]
    )
    assert (
        len(rules_unknown_ids)
        == result["unassessed_requirements"]
        == shown.json()["data"]["report"]["unassessed_count"]
        == 1
    )
    assert {row["semantic_outcome"] for row in assessed} == {"no_risk_found"}
    assert all(
        {citation["kind"] for citation in row["semantic_citations"]} == {"tender", "draft"}
        for row in assessed
    )
    assert all(
        row["semantic_outcome"] is None
        and row["semantic_reason_code"] is None
        and (row["semantic_citations"] == [])
        for row in deterministic
    )
    async with case["app"].state.db.transaction() as session:
        usage = list(
            (
                await session.scalars(
                    select(UsageRecord).where(UsageRecord.job_id == UUID(result["job_id"]))
                )
            ).all()
        )
        assert len(usage) == 1
        stored = await session.get(CheckRun, UUID(result["report_id"]))
        assert stored is not None
        fixed_manifest = stored.input_manifest
        assert drafts.digest(fixed_manifest) == stored.input_hash == preview["input"]["input_hash"]
    request_count = len(vendor.requests)
    cached = await submit_combined(case, preview, reasoning="high")
    assert cached.status_code == 200, cached.text
    assert cached.json()["data"] == {
        "job_id": result["job_id"],
        "status": "succeeded",
        "cached": True,
    }
    assert len(vendor.requests) == request_count
    artifact = write_combined_artifact(tmp_path, fixed_manifest, shown.json(), vendor.requests)
    artifact_files = {
        artifact / "input-manifest.json",
        artifact / "report.json",
        artifact / "provider-requests.json",
        artifact / "commands.txt",
    }
    assert all(path.is_file() for path in artifact_files)
    assert json.loads((artifact / "input-manifest.json").read_text()) == fixed_manifest
    assert json.loads((artifact / "report.json").read_text()) == shown.json()
    artifact_text = "\n".join(path.read_text(encoding="utf-8") for path in artifact_files)
    assert case["header"]["Authorization"] not in artifact_text
    assert case["header"]["Authorization"].removeprefix("Bearer ") not in artifact_text
    assert case["task"] in artifact_text and result["report_id"] in artifact_text
    cli_settings = Settings(data_dir=tmp_path)
    monkeypatch.setenv("BID_ACCESS_KEY", case["header"]["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(
        cli, "Client", lambda mode, server: LiveCheckClient(mode, server, cli_settings)
    )
    for mode in ("remote", "local"):
        common = ["--mode", mode]
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
                "--mode",
                "combined",
                "--reasoning",
                "high",
                "--dry-run",
            ],
        )
        assert run_code == 0, run_body
        assert run_body["data"]["input"]["input_hash"] == preview["input"]["input_hash"]
        assert run_body["data"]["mode"] == "combined"
        show_code, show_body = await asyncio.to_thread(
            invoke_live_cli, [*common, "check", "show", "--id", result["report_id"]]
        )
        assert show_code == 5, show_body
        assert show_body["data"]["report"]["mode"] == "combined"
        assert show_body["data"]["coverage"] == coverage
    assert len(vendor.requests) == request_count


async def test_combined_requires_redaction_without_calls_or_writes(
    check_case, tmp_path, monkeypatch
):
    case = check_case
    vendor = SemanticVendor()
    install_resolver(monkeypatch, semantic_llm(tmp_path, vendor))
    changed = await case["api"].put(
        f"/tasks/{case['task']}/model-redaction",
        headers=case["header"],
        json={"expected_revision": 1, "model_redaction_enabled": False},
    )
    assert changed.status_code == 200, changed.text
    before = await combined_counts(case)
    response, preview = await combined_preview(case)
    assert response.status_code == 200, response.text
    assert preview["admission_blocker"] == "redaction_required"
    assert vendor.requests == [] and await combined_counts(case) == before
    denied = await submit_combined(case, preview)
    assert denied.status_code in {400, 409, 422}
    assert denied.json()["data"]["error"]["code"] == "redaction_required"
    assert vendor.requests == [] and await combined_counts(case) == before


@pytest.mark.parametrize("replacement", [{"llm_model": "synthetic-check-model-v2"}])
async def test_combined_preview_rejects_model_identity_change(
    check_case, tmp_path, monkeypatch, replacement
):
    case = check_case
    vendor = SemanticVendor()
    current = {"llm": semantic_llm(tmp_path, vendor)}

    async def resolved(session, settings, transport=None, job=None):
        return current["llm"]

    monkeypatch.setattr("app.providers.llm.resolve_llm", resolved)
    response, preview = await combined_preview(case)
    assert response.status_code == 200, response.text
    before = await combined_counts(case)
    current["llm"] = semantic_llm(tmp_path, vendor, **replacement)
    denied = await submit_combined(case, preview)
    assert denied.status_code == 409, denied.text
    assert denied.json()["data"]["error"]["code"] == "check_input_changed"
    assert vendor.requests == [] and await combined_counts(case) == before


@pytest.mark.parametrize(
    "attack,reason",
    [
        ("missing", "missing_requirement_id"),
        ("duplicate", "duplicate_requirement_id"),
        ("unknown_id", "unknown_requirement_id"),
        ("unsent_ref", "ref_not_sent"),
        ("wrong_ref", "quote_not_at_position"),
        ("stitched_quote", "quote_not_at_position"),
        ("ambiguous_quote", "ambiguous_quote"),
        ("placeholder_quote", "redacted_input_unassessable"),
        ("other_requirement", "cross_requirement_citation"),
    ],
)
async def test_combined_rejects_adversarial_conclusions_at_publication(
    check_case, tmp_path, monkeypatch, attack, reason
):
    case = check_case
    vendor = SemanticVendor(attack=attack)
    install_resolver(monkeypatch, semantic_llm(tmp_path, vendor))
    response, preview = await combined_preview(case)
    assert response.status_code == 200, response.text
    queued = await submit_combined(case, preview)
    assert queued.status_code == 200, queued.text
    terminal = await run_combined(case, queued.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    result = terminal["result"]
    assert result["completion"] == "partial"
    shown = await case["api"].get(f"/checks/{result['report_id']}", headers=case["header"])
    assert shown.status_code == 200, shown.text
    target = next(
        row
        for row in shown.json()["data"]["coverage"]
        if row["requirement_id"] == case["requirements"][1]["id"]
    )
    assert target["semantic_status"] == "unassessed"
    assert target["semantic_outcome"] is None
    assert target["semantic_reason_code"] == reason
    assert target["semantic_citations"] == []


@pytest.mark.parametrize("failure", ["refused", "truncated"])
async def test_combined_later_batch_failure_keeps_one_bill_and_partial_report(
    check_case, database, tmp_path, monkeypatch, failure
):
    case = check_case
    vendor = SemanticVendor({2: failure})
    llm = semantic_llm(tmp_path, vendor, llm_batch_chars=1200)
    install_resolver(monkeypatch, llm)
    response, preview = await combined_preview(case)
    assert response.status_code == 200, response.text
    assert preview["admission_blocker"] is None
    queued = await submit_combined(case, preview)
    assert queued.status_code == 200, queued.text
    terminal = await run_combined(case, queued.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    result = terminal["result"]
    assert result["completion"] == "partial"
    assert result["stop_reason"] == (
        "provider_refused" if failure == "refused" else "provider_output_truncated"
    )
    assert [request["requested_requirement_ids"] for request in vendor.requests] == [["r2"], ["r3"]]
    async with case["app"].state.db.transaction() as session:
        usages = list(
            (
                await session.scalars(
                    select(UsageRecord).where(UsageRecord.job_id == UUID(result["job_id"]))
                )
            ).all()
        )
        calls = list(
            (
                await session.scalars(
                    select(VendorCall).where(VendorCall.job_id == UUID(result["job_id"]))
                )
            ).all()
        )
        assert len(usages) == len(calls) == 2
        assert len({row.call_id for row in usages}) == 2


@pytest.mark.parametrize("failure", ["refused", "truncated"])
async def test_combined_local_provider_failure_settles_exactly_once(
    check_case, database, tmp_path, monkeypatch, admin_engine, failure
):
    case = check_case
    vendor = SemanticVendor({1: failure})
    install_resolver(monkeypatch, semantic_llm(case["app"].state.processor.settings, vendor))
    before = await combined_counts(case)
    response, preview = await combined_preview(case)
    assert response.status_code == 200, response.text
    assert preview["admission_blocker"] is None
    assert await combined_counts(case) == before and vendor.requests == []
    queued = await submit_combined(case, preview)
    assert queued.status_code == 200, queued.text
    terminal = await run_combined(case, queued.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    assert terminal["result"]["completion"] == "partial"
    assert terminal["result"]["stop_reason"] == (
        "provider_refused" if failure == "refused" else "provider_output_truncated"
    )
    assert len(vendor.requests) == 1
    job_id = UUID(queued.json()["data"]["job_id"])
    async with case["app"].state.db.transaction() as session:
        usage = list(
            (await session.scalars(select(UsageRecord).where(UsageRecord.job_id == job_id))).all()
        )
        calls = list(
            (await session.scalars(select(VendorCall).where(VendorCall.job_id == job_id))).all()
        )
        assert len(usage) == len(calls) == 1
        job = await session.get(Job, job_id)
        assert job is not None and job.run_id is not None
        run_id, call_id = (job.run_id, calls[0].id)
        replay = ProviderUsage(
            **{key: getattr(usage[0], key) for key in ProviderUsage.model_fields}
        )
    execution = JobExecution(
        case["app"].state.processor.settings, case["app"].state.db, job_id, run_id
    )
    await execution.complete(call_id, replay)
    async with case["app"].state.db.transaction() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(UsageRecord).where(UsageRecord.job_id == job_id)
            )
            == 1
        )


async def test_combined_hard_stop_overrides_call_limit_and_blocks_publication(
    check_case, database, tmp_path, monkeypatch
):
    case = check_case
    vendor = SemanticVendor()
    install_resolver(monkeypatch, semantic_llm(tmp_path, vendor))
    response, preview = await combined_preview(case)
    assert response.status_code == 200, response.text
    queued = await submit_combined(case, preview)
    assert queued.status_code == 200, queued.text
    job_id = UUID(queued.json()["data"]["job_id"])

    async def hard_after_call_limit(self, quote):
        self.stop("job_call_limit_exceeded", "Synthetic call ceiling")
        raise self.stop("job_heartbeat_failed", "Synthetic heartbeat failure")

    monkeypatch.setattr(JobExecution, "admit", hard_after_call_limit)
    terminal = await run_combined(case, queued.json()["data"])
    assert terminal["status"] == "failed", terminal
    assert terminal["error"]["code"] == "job_heartbeat_failed"
    assert vendor.requests == []
    async with case["app"].state.db.transaction() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(CheckRun).where(CheckRun.job_id == job_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(UsageRecord).where(UsageRecord.job_id == job_id)
            )
            == 0
        )


async def test_combined_per_call_admission_stop_after_submit_publishes_fixed_partial(
    check_case, database, tmp_path, monkeypatch, admin_engine
):
    case = check_case
    vendor = SemanticVendor()
    install_resolver(monkeypatch, semantic_llm(case["app"].state.processor.settings, vendor))
    response, preview = await combined_preview(case)
    assert response.status_code == 200, response.text
    assert preview["admission_blocker"] is None
    queued = await submit_combined(case, preview)
    assert queued.status_code == 200, queued.text
    job_id = UUID(queued.json()["data"]["job_id"])
    reason = "job_call_limit_exceeded"

    async def stop_at_call_limit(execution, plan):
        raise execution.stop(reason, "Synthetic call ceiling")

    monkeypatch.setattr(JobExecution, "admit", stop_at_call_limit)
    terminal = await run_combined(case, queued.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    result = terminal["result"]
    assert result["completion"] == "partial" and result["stop_reason"] == reason
    assert vendor.requests == []
    async with case["app"].state.db.transaction() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(UsageRecord).where(UsageRecord.job_id == job_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(VendorCall).where(VendorCall.job_id == job_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(CheckRun).where(CheckRun.job_id == job_id)
            )
            == 1
        )


async def test_combined_input_change_during_call_retains_usage_without_publication(
    check_case, database, tmp_path, monkeypatch
):
    case = check_case
    vendor = SemanticVendor()
    vendor.entered, vendor.release = (asyncio.Event(), asyncio.Event())
    install_resolver(monkeypatch, semantic_llm(tmp_path, vendor))
    response, preview = await combined_preview(case)
    assert response.status_code == 200, response.text
    queued = await submit_combined(case, preview)
    assert queued.status_code == 200, queued.text
    job_id = queued.json()["data"]["job_id"]
    running = asyncio.create_task(case["app"].state.processor(job_id))
    await asyncio.wait_for(vendor.entered.wait(), 5)
    changed = await case["api"].put(
        f"/tasks/{case['task']}/model-redaction",
        headers=case["header"],
        json={"expected_revision": 1, "model_redaction_enabled": False},
    )
    assert changed.status_code == 200, changed.text
    vendor.release.set()
    await running
    status = (await case["api"].get(f"/jobs/{job_id}", headers=case["header"])).json()["data"]
    assert status["status"] == "failed", status
    assert status["error"]["code"] == "check_input_changed"
    async with case["app"].state.db.transaction() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageRecord)
                .where(UsageRecord.job_id == UUID(job_id))
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(VendorCall)
                .where(VendorCall.job_id == UUID(job_id))
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(CheckRun).where(CheckRun.job_id == UUID(job_id))
            )
            == 0
        )


async def test_combined_accounting_failure_marks_call_unknown_and_blocks_publication(
    check_case, database, tmp_path, monkeypatch
):
    case = check_case
    vendor = SemanticVendor()
    install_resolver(monkeypatch, semantic_llm(tmp_path, vendor))
    response, preview = await combined_preview(case)
    assert response.status_code == 200, response.text
    queued = await submit_combined(case, preview)
    assert queued.status_code == 200, queued.text
    job_id = UUID(queued.json()["data"]["job_id"])

    async def fail_accounting(self, call_id, usage):
        raise self.stop("usage_accounting_failed", "Synthetic accounting failure")

    monkeypatch.setattr(JobExecution, "complete", fail_accounting)
    terminal = await run_combined(case, queued.json()["data"])
    assert terminal["status"] == "failed", terminal
    assert terminal["error"]["code"] == "usage_accounting_failed"
    assert len(vendor.requests) == 1
    async with case["app"].state.db.transaction() as session:
        calls = list(
            (await session.scalars(select(VendorCall).where(VendorCall.job_id == job_id))).all()
        )
        assert len(calls) == 1 and calls[0].state == "unknown"
        assert (
            await session.scalar(
                select(func.count()).select_from(UsageRecord).where(UsageRecord.job_id == job_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(CheckRun).where(CheckRun.job_id == job_id)
            )
            == 0
        )


async def test_combined_changed_run_takeover_retains_late_usage_and_fences_old_attempt(
    check_case, database, tmp_path, monkeypatch
):
    case = check_case
    vendor = SemanticVendor()
    vendor.entered, vendor.release = (asyncio.Event(), asyncio.Event())
    install_resolver(monkeypatch, semantic_llm(tmp_path, vendor))
    response, preview = await combined_preview(case)
    assert response.status_code == 200, response.text
    queued = await submit_combined(case, preview)
    assert queued.status_code == 200, queued.text
    job_id = UUID(queued.json()["data"]["job_id"])
    running = asyncio.create_task(case["app"].state.processor(str(job_id)))
    await asyncio.wait_for(vendor.entered.wait(), 5)
    takeover_run = uuid4()
    async with case["app"].state.db.transaction() as session:
        job = await session.get(Job, job_id)
        assert job is not None and job.run_id is not None
        old_run = job.run_id
        job.run_id = takeover_run
        job.lease_until = datetime.now(UTC) + timedelta(minutes=5)
    vendor.release.set()
    await running
    async with case["app"].state.db.transaction() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.status == "running" and job.run_id == takeover_run
        usages = list(
            (await session.scalars(select(UsageRecord).where(UsageRecord.job_id == job_id))).all()
        )
        calls = list(
            (await session.scalars(select(VendorCall).where(VendorCall.job_id == job_id))).all()
        )
        assert len(usages) == len(calls) == 1
        assert usages[0].run_id == calls[0].run_id == old_run
        assert (
            await session.scalar(
                select(func.count()).select_from(CheckRun).where(CheckRun.job_id == job_id)
            )
            == 0
        )


async def test_combined_cancelled_call_is_settled_once_and_cannot_publish(
    check_case, database, tmp_path, monkeypatch
):
    case = check_case
    vendor = SemanticVendor()
    vendor.entered, vendor.release = (asyncio.Event(), asyncio.Event())
    install_resolver(monkeypatch, semantic_llm(tmp_path, vendor))
    response, preview = await combined_preview(case)
    assert response.status_code == 200, response.text
    queued = await submit_combined(case, preview)
    assert queued.status_code == 200, queued.text
    job_id = queued.json()["data"]["job_id"]
    running = asyncio.create_task(case["app"].state.processor(job_id))
    await asyncio.wait_for(vendor.entered.wait(), 5)
    cancelled = await case["api"].post(f"/jobs/{job_id}/cancel", headers=case["header"])
    assert cancelled.status_code == 200, cancelled.text
    vendor.release.set()
    await running
    status = (await case["api"].get(f"/jobs/{job_id}", headers=case["header"])).json()["data"]
    assert status["status"] == "cancelled"
    async with case["app"].state.db.transaction() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageRecord)
                .where(UsageRecord.job_id == UUID(job_id))
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(VendorCall)
                .where(VendorCall.job_id == UUID(job_id))
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(CheckRun).where(CheckRun.job_id == UUID(job_id))
            )
            == 0
        )


async def test_combined_expired_attempt_bills_response_but_old_run_cannot_publish(
    check_case, database, tmp_path, monkeypatch
):
    case = check_case
    vendor = SemanticVendor()
    vendor.entered, vendor.release = (asyncio.Event(), asyncio.Event())
    install_resolver(monkeypatch, semantic_llm(tmp_path, vendor))
    response, preview = await combined_preview(case)
    assert response.status_code == 200, response.text
    queued = await submit_combined(case, preview)
    job_id = queued.json()["data"]["job_id"]
    running = asyncio.create_task(case["app"].state.processor(job_id))
    await asyncio.wait_for(vendor.entered.wait(), 5)
    async with case["app"].state.db.transaction() as session:
        await session.execute(
            update(Job)
            .where(Job.id == UUID(job_id))
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    vendor.release.set()
    await running
    async with case["app"].state.db.transaction() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageRecord)
                .where(UsageRecord.job_id == UUID(job_id))
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(CheckRun).where(CheckRun.job_id == UUID(job_id))
            )
            == 0
        )
