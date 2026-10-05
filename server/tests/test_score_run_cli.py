"""Score execution CLI snapshots at the DB-free HTTP transport boundary."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from app.schemas.check_contracts import AssessmentListData
from app.schemas.contracts import Result
from app.schemas.score_contracts import (
    ScoreJobResult,
    ScorePreview,
    ScoreReportData,
    ScoreRequest,
    ScoreRunView,
)
from bid_cli import main as cli
from bid_cli.client import Client, State
from bid_cli.schema import command_schema

ORG = "00000000-0000-0000-0000-000000000001"
TASK = "00000000-0000-0000-0000-000000000002"
DRAFT = "00000000-0000-0000-0000-000000000003"
EXTRACTION = "00000000-0000-0000-0000-000000000004"
DOCUMENT = "00000000-0000-0000-0000-000000000005"
RUBRIC = "00000000-0000-0000-0000-000000000006"
ITEM = "00000000-0000-0000-0000-000000000007"
REQUIREMENT = "00000000-0000-0000-0000-000000000008"
RESPONSE = "00000000-0000-0000-0000-000000000009"
REPORT = "00000000-0000-0000-0000-000000000010"
JOB = "00000000-0000-0000-0000-000000000011"
RUN = "00000000-0000-0000-0000-000000000012"
SHA = "a" * 64
DRAFT_SHA = "b" * 64
ASSESSMENT_DATE = "2026-10-05"
NOW = "2026-10-05T00:00:00Z"

ASSESSMENT_INPUT = {
    "org_id": ORG,
    "task_id": TASK,
    "draft_id": DRAFT,
    "extraction_job_id": EXTRACTION,
    "document_id": DOCUMENT,
    "input_hash": SHA,
    "draft_input_hash": DRAFT_SHA,
    "assessment_date": ASSESSMENT_DATE,
    "scope": "confirmed_draft",
}
PREVIEW = {
    "dry_run": True,
    "input": ASSESSMENT_INPUT,
    "selected_item_ids": [ITEM],
    "estimated_cost": {"llm_tokens": 900, "ocr_pages": 0, "usd": "0.02"},
    "estimated_charge": "0.04",
    "billing_currency": "CNY",
    "cost_basis": "known",
    "cost_basis_reason": "configured_price",
    "estimate_kind": "first_pass_upper_bound",
    "admission_blocker": None,
    "estimated_duration_ms": None,
    "provider_config_id": None,
    "provider_source": "platform",
    "platform_model_id": "synthetic-score",
    "model_revision": 1,
    "model": "synthetic-model",
    "reasoning": "high",
    "redaction_revision": 1,
    "redaction_rule_version": "redaction-v1",
    "redacted_counts": {},
    "max_charge": "1.25",
    "rubric_id": RUBRIC,
    "rubric_version": 1,
    "rubric_input_hash": SHA,
    "prompt_version": "score-prompt-v1",
    "schema_version": "score-wire-v1",
    "scoring_rule_version": "score-aggregation-v1",
    "preflight_unassessable_item_ids": [],
    "limitations": ["advisory_only"],
}
REPORT_VIEW = {
    "id": REPORT,
    "org_id": ORG,
    "task_id": TASK,
    "job_id": JOB,
    "run_id": RUN,
    "input": ASSESSMENT_INPUT,
    "rubric_id": RUBRIC,
    "rubric_version": 1,
    "rubric_input_hash": SHA,
    "prompt_version": "score-prompt-v1",
    "schema_version": "score-wire-v1",
    "scoring_rule_version": "score-aggregation-v1",
    "assessment_date": ASSESSMENT_DATE,
    "created_at": NOW,
    "completion": "partial",
    "validity": "current",
    "invalidation_codes": [],
    "assessed_items": 0,
    "unassessable_items": 1,
    "assessed_subtotal": "0",
    "overall_aggregation": "sum",
    "overall_aggregation_assessable": True,
    "overall_rule_text": None,
    "overall_cap": None,
    "possible_range": {"minimum": "0", "maximum": "10"},
    "total_status": "range_only",
    "estimated_total": None,
    "limitations": ["missing_confirmed_support"],
    "usage_record_ids": [],
    "advisory_only": True,
}
REPORT_DATA = {
    "report": REPORT_VIEW,
    "sections": [
        {
            "section_key": "technical",
            "title": "Technical response",
            "aggregation": "sum",
            "aggregation_assessable": True,
            "aggregation_rule_text": None,
            "cap": None,
            "configured_range": {"minimum": "0", "maximum": "10"},
            "assessed_items": 0,
            "unassessable_items": 1,
            "assessed_subtotal": "0",
            "possible_range": {"minimum": "0", "maximum": "10"},
            "status": "range_only",
            "estimated_score": None,
        }
    ],
    "items": [
        {
            "id": ITEM,
            "org_id": ORG,
            "task_id": TASK,
            "report_id": REPORT,
            "rubric_item_id": ITEM,
            "requirement_id": REQUIREMENT,
            "anchor_response_item_id": RESPONSE,
            "response_item_ids": [],
            "section_key": "technical",
            "anchor_partition": "gap",
            "outcome": "unassessable",
            "score_range": {"minimum": "0", "maximum": "10"},
            "estimated_score": None,
            "reason_code": "missing_confirmed_support",
            "reason": "No confirmed response text supports this scoring item.",
            "deduction_reasons": [],
            "strengthening_actions": ["Confirm response material for this scoring item."],
            "citations": [],
            "advisory_only": True,
        }
    ],
}


def result(*, data=None, items=None, warnings=None) -> dict:
    return Result(
        ok=True,
        command="score interface",
        data=data or {},
        items=items or [],
        warnings=warnings or [],
    ).model_dump(mode="json")


class MockClient(Client):
    """Run both CLI modes through the same deterministic HTTP mock."""

    def __init__(self, mode: str, server: str, state: State, handler):
        super().__init__(mode, server, state)
        self.handler = handler

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler), base_url="http://interface"
        ) as transport:
            yield transport


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    query = dict(request.url.params)
    payload = json.loads(request.content) if request.content else None
    if path == f"/tasks/{TASK}/scores/preview":
        parsed = ScoreRequest.model_validate(payload)
        assert request.method == "POST" and parsed.dry_run is True
        assert parsed.draft_id == UUID(DRAFT) and parsed.rubric_id == UUID(RUBRIC)
        body = result(data=ScorePreview.model_validate(PREVIEW).model_dump(mode="json"))
    elif path == f"/tasks/{TASK}/scores" and request.method == "POST":
        parsed = ScoreRequest.model_validate(payload)
        assert parsed.dry_run is False and parsed.expected_input_hash == SHA
        body = result(data={"job_id": JOB, "status": "queued", "cached": False})
    elif path == f"/tasks/{TASK}/scores":
        assert request.method == "GET" and query == {"limit": "20"}
        body = result(
            data={"task_id": TASK, "total": 1, "next_cursor": None},
            items=[ScoreRunView.model_validate(REPORT_VIEW).model_dump(mode="json")],
        )
    elif path == f"/tasks/{TASK}/scores/{REPORT}":
        assert request.method == "GET"
        body = result(
            data=ScoreReportData.model_validate(REPORT_DATA).model_dump(mode="json"),
            warnings=["missing_confirmed_support"],
        )
    else:
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")
    return httpx.Response(200, json=body)


def install_transport(monkeypatch) -> None:
    monkeypatch.setenv("BID_SESSION", "synthetic-interface-session")
    monkeypatch.setenv("BID_ORG", ORG)
    monkeypatch.setattr(
        cli,
        "Client",
        lambda mode, server, state: MockClient(mode, server, state, handler),
    )


def invoke(arguments: list[str], capsys) -> tuple[int, dict]:
    exit_code = 0
    try:
        cli.main([*arguments, "--json"])
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
    body = json.loads(capsys.readouterr().out)
    body["duration_ms"] = 0
    assert set(body) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    return exit_code, body


def test_score_run_cli_remote_and_local_snapshots_db_free(monkeypatch, tmp_path, capsys):
    install_transport(monkeypatch)
    commands = {
        "score run": [
            "score",
            "run",
            "--task",
            TASK,
            "--draft",
            DRAFT,
            "--rubric",
            RUBRIC,
            "--as-of",
            ASSESSMENT_DATE,
            "--reasoning",
            "high",
            "--max-charge",
            "1.25",
            "--dry-run",
        ],
        "score list": ["score", "list", "--task", TASK, "--limit", "20"],
        "score show": ["score", "show", "--task", TASK, "--report", REPORT],
    }
    actual = {}
    for mode in ("remote", "local"):
        actual[mode] = {}
        for name, arguments in commands.items():
            exit_code, body = invoke(
                ["--mode", mode, "--state", str(tmp_path / f"{mode}.enc"), *arguments],
                capsys,
            )
            assert exit_code == (5 if name == "score show" else 0)
            actual[mode][name] = body
        ScorePreview.model_validate(actual[mode]["score run"]["data"])
        AssessmentListData.model_validate(actual[mode]["score list"]["data"])
        [ScoreRunView.model_validate(item) for item in actual[mode]["score list"]["items"]]
        ScoreReportData.model_validate(actual[mode]["score show"]["data"])
    snapshot = Path(__file__).with_name("snapshots") / "score-run-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(
            json.dumps(actual, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    assert actual == json.loads(snapshot.read_text(encoding="utf-8"))


def test_score_run_wait_partial_exit_and_schema(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "call",
        lambda *args, **kwargs: result(data={"job_id": JOB, "status": "queued", "cached": False}),
    )

    async def terminal(job_id, limit_seconds):
        assert str(job_id) == JOB and limit_seconds == 5
        output = ScoreJobResult(
            report_id=REPORT,
            job_id=JOB,
            completion="partial",
            usage_record_ids=[],
            charge="0.04",
            billing_currency="CNY",
            stop_reason="unassessable_items",
            assessed_items=0,
            unassessable_items=1,
            assessed_subtotal="0",
            total_status="range_only",
            estimated_total=None,
        )
        return result(
            data={"status": "succeeded", "result": output.model_dump(mode="json")},
            warnings=["unassessable_items"],
        )

    monkeypatch.setattr(cli, "wait_for_job", terminal)
    code, body = invoke(
        [
            "score",
            "run",
            "--task",
            TASK,
            "--draft",
            DRAFT,
            "--rubric",
            RUBRIC,
            "--as-of",
            ASSESSMENT_DATE,
            "--expected-input-hash",
            SHA,
            "--wait",
            "--timeout",
            "5",
        ],
        capsys,
    )
    assert code == 5 and body["ok"] is False
    assert body["data"]["report_id"] == REPORT
    assert body["warnings"] == ["unassessable_items"]

    schema = command_schema(cli.app)["commands"]
    for name in ("score run", "score list", "score show"):
        assert name in schema and schema[name].get("output")


@pytest.mark.parametrize(
    "arguments,error_code",
    [
        (
            [
                "score",
                "run",
                "--task",
                TASK,
                "--draft",
                DRAFT,
                "--rubric",
                RUBRIC,
                "--as-of",
                "2026-02-30",
                "--dry-run",
            ],
            "invalid_date",
        ),
        (
            [
                "score",
                "run",
                "--task",
                TASK,
                "--draft",
                DRAFT,
                "--rubric",
                RUBRIC,
                "--as-of",
                ASSESSMENT_DATE,
            ],
            "invalid_input",
        ),
        (
            [
                "score",
                "run",
                "--task",
                TASK,
                "--draft",
                DRAFT,
                "--rubric",
                RUBRIC,
                "--as-of",
                ASSESSMENT_DATE,
                "--dry-run",
                "--wait",
            ],
            "invalid_input",
        ),
    ],
)
def test_score_run_rejects_invalid_cli_inputs(arguments, error_code, capsys):
    code, body = invoke(arguments, capsys)
    assert code == 2 and body["data"]["error"]["code"] == error_code


@pytest.mark.parametrize(
    "status,error_exit,expected_exit",
    [("failed", 2, 2), ("failed", 3, 3), ("failed", 4, 4), ("cancelled", 2, 4)],
)
def test_score_job_status_uses_terminal_error_exit(
    monkeypatch, capsys, status, error_exit, expected_exit
):
    monkeypatch.setattr(
        cli,
        "call",
        lambda *args, **kwargs: result(
            data={
                "id": JOB,
                "kind": "score",
                "status": status,
                "result": {},
                "error": {
                    "code": "synthetic_score_failure",
                    "message": "Synthetic score failure",
                    "exit_code": error_exit,
                },
            }
        ),
    )
    code, body = invoke(["job", "status", JOB], capsys)
    assert code == expected_exit and body["ok"] is False
