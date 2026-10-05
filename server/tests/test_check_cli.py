"""Check CLI request/response snapshots at the public command boundary."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from app.api.check import create_router
from app.core.errors import ServiceError
from app.schemas.check_contracts import (
    AssessmentListData,
    CheckPreview,
    CheckReportData,
    CheckRunView,
    FindingDecisionData,
    FindingDecisionView,
)
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.client import Client, State
from bid_cli.schema import command_schema
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

IDENTIFIER = "00000000-0000-0000-0000-000000000001"
IDENTIFIER_2 = "00000000-0000-0000-0000-000000000002"
SHA = "a" * 64


class InterfaceClient(Client):
    """Exercise Client.request for both labels against one DB-free ASGI boundary."""

    def __init__(self, mode: str, server: str, state: State, application: FastAPI):
        super().__init__(mode, server, state)
        self.application = application

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.application), base_url="http://interface"
        ) as transport:
            yield transport


class InterfaceSession:
    async def commit(self):
        return None


class InterfaceDatabase:
    @asynccontextmanager
    async def transaction(self, org_id):
        yield self

    async def get(self, model, key):
        return None


class InterfaceQueue:
    async def enqueue(self, org_id: str, job_id: str):
        return 1


def check_interface(
    monkeypatch,
    *,
    fail_show: bool = False,
    fail_queue: bool = False,
    job_status: str | None = None,
    job_exit_code: int = 4,
    job_crossorg: bool = False,
) -> FastAPI:
    from app.services import check

    assessment_input = {
        "org_id": IDENTIFIER,
        "task_id": IDENTIFIER,
        "draft_id": IDENTIFIER_2,
        "extraction_job_id": IDENTIFIER,
        "document_id": IDENTIFIER,
        "input_hash": SHA,
        "draft_input_hash": SHA,
        "assessment_date": "2026-10-04",
        "scope": "confirmed_draft",
    }
    report = {
        "id": IDENTIFIER,
        "org_id": IDENTIFIER,
        "task_id": IDENTIFIER,
        "job_id": IDENTIFIER,
        "run_id": IDENTIFIER,
        "input": assessment_input,
        "mode": "rules",
        "rule_version": "check-rules-v1",
        "prompt_version": None,
        "schema_version": "check-schema-v1",
        "created_at": "2026-10-04T00:00:00Z",
        "completion": "partial",
        "validity": "current",
        "invalidation_codes": [],
        "item_count": 1,
        "finding_count": 0,
        "unassessed_count": 1,
        "limitations": ["semantic_checks_not_requested"],
        "usage_record_ids": [],
        "advisory_only": True,
    }
    decision = {
        "id": IDENTIFIER,
        "org_id": IDENTIFIER,
        "task_id": IDENTIFIER,
        "report_id": IDENTIFIER,
        "finding_id": IDENTIFIER_2,
        "revision": 2,
        "action": "dismiss",
        "reason": "Reviewed against the confirmed draft.",
        "reason_sha256": SHA,
        "decided_by": IDENTIFIER,
        "decided_at": "2026-10-04T00:00:00Z",
        "actor_kind": "session",
    }

    async def submit(session, actor, task_id, body, storage, settings):
        if body.dry_run:
            return {
                "dry_run": True,
                "input": assessment_input,
                "selected_item_ids": [],
                "estimated_cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
                "estimated_charge": "0",
                "billing_currency": "CNY",
                "cost_basis": "known",
                "cost_basis_reason": "no_model_calls",
                "estimate_kind": "first_pass_upper_bound",
                "admission_blocker": None,
                "estimated_duration_ms": None,
                "provider_config_id": None,
                "provider_source": None,
                "platform_model_id": None,
                "model_revision": None,
                "model": None,
                "reasoning": None,
                "redaction_revision": 1,
                "redaction_rule_version": "redaction-v1",
                "redacted_counts": {},
                "max_charge": None,
                "mode": "rules",
                "rule_version": "check-rules-v1",
                "prompt_version": None,
                "schema_version": "check-schema-v1",
                "rules_applicable": 1,
                "semantic_items": 0,
                "gap_requirements": 0,
                "limitations": ["semantic_checks_not_requested"],
            }, None
        job = type(
            "QueuedJob",
            (),
            {"id": UUID(IDENTIFIER), "status": "queued", "queue_id": None},
        )()
        return {"job_id": IDENTIFIER, "status": "queued", "cached": False}, job

    async def listing(session, actor, task_id, storage, settings, *, cursor=None, limit=50):
        return {"task_id": IDENTIFIER, "total": 1, "next_cursor": None}, [report]

    async def showing(session, actor, report_id, storage, settings):
        if fail_show:
            raise ServiceError("not_found", "Resource not found", 404, 4)
        return {"report": report, "coverage": [], "certificates": []}, []

    async def deciding(session, actor, report_id, finding_id, body, storage, settings):
        return {
            "finding": {
                "id": IDENTIFIER_2,
                "org_id": IDENTIFIER,
                "task_id": IDENTIFIER,
                "report_id": IDENTIFIER,
                "check_item_id": IDENTIFIER,
                "requirement_id": IDENTIFIER,
                "method": "deterministic",
                "code": "negative_deviation",
                "severity": "deduction_risk",
                "review_domain": "technical",
                "reason": "Confirmed negative deviation.",
                "source": {
                    "document_id": IDENTIFIER,
                    "chunk_id": IDENTIFIER,
                    "page": 1,
                    "location": None,
                    "quote": "Synthetic requirement.",
                },
                "citations": [],
                "status": "dismissed",
                "revision": 2,
                "latest_decision_id": IDENTIFIER,
                "advisory_only": True,
            },
            "decision": decision,
        }

    async def history(
        session,
        actor,
        report_id,
        finding_id,
        storage,
        settings,
        *,
        cursor=None,
        limit=50,
    ):
        return {"task_id": IDENTIFIER, "total": 1, "next_cursor": None}, [decision]

    monkeypatch.setattr(check, "submit_check", submit)
    monkeypatch.setattr(check, "list_checks", listing)
    monkeypatch.setattr(check, "show_check", showing)
    monkeypatch.setattr(check, "decide_finding", deciding)
    monkeypatch.setattr(check, "decision_history", history)
    actor = type("Actor", (), {"org_id": UUID(IDENTIFIER)})()

    async def context():
        yield InterfaceSession(), actor

    queue = InterfaceQueue()
    if fail_queue:

        async def unavailable(org_id: str, job_id: str):
            raise OSError("synthetic queue failure")

        queue.enqueue = unavailable  # pyright: ignore[reportAttributeAccessIssue]
    application = FastAPI()

    @application.exception_handler(ServiceError)
    async def service_error(request: Request, error: ServiceError):
        body = Result(
            ok=False,
            command=request.scope["route"].name.replace("_", " "),
            data={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "exit_code": error.exit_code,
                }
            },
        )
        return JSONResponse(status_code=error.status, content=body.model_dump(mode="json"))

    @application.get("/jobs/{job_id}", name="job_status")
    async def status(job_id: UUID):
        if job_crossorg:
            raise ServiceError("not_found", "Resource not found", 404, 4)
        state = job_status or "queued"
        error = (
            {
                "code": "synthetic_check_failure",
                "message": "Synthetic check failure",
                "exit_code": job_exit_code,
            }
            if state == "failed"
            else None
        )
        return Result(
            ok=state not in {"failed", "cancelled"},
            command="job status",
            data={
                "id": str(job_id),
                "kind": "check",
                "status": state,
                "result": {},
                "error": error,
                "attempts": 1,
                "reasoning": None,
            },
        )

    application.include_router(
        create_router(context, InterfaceDatabase(), object(), queue, object())
    )
    return application


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


def install_transport(monkeypatch, application: FastAPI) -> None:
    monkeypatch.setenv("BID_SESSION", "synthetic-interface-session")
    monkeypatch.setenv("BID_ORG", IDENTIFIER)
    monkeypatch.setattr(
        cli,
        "Client",
        lambda mode, server, state: InterfaceClient(mode, server, state, application),
    )


def test_check_cli_remote_and_local_transport_snapshots_db_free(monkeypatch, tmp_path, capsys):
    """Both mode labels cross Client.request and ASGI; PostgreSQL E2E is separate."""
    application = check_interface(monkeypatch)
    install_transport(monkeypatch, application)
    commands = {
        "check run": [
            "check",
            "run",
            "--task",
            IDENTIFIER,
            "--draft",
            IDENTIFIER_2,
            "--as-of",
            "2026-10-04",
            "--dry-run",
        ],
        "check list": ["check", "list", "--task", IDENTIFIER, "--limit", "25"],
        "check show": ["check", "show", "--id", IDENTIFIER],
        "check decide": [
            "check",
            "decide",
            "--report",
            IDENTIFIER,
            "--finding",
            IDENTIFIER_2,
            "--action",
            "dismiss",
            "--expected-revision",
            "1",
            "--expected-input-hash",
            SHA,
            "--reason",
            "Reviewed against the confirmed draft.",
        ],
        "check history": [
            "check",
            "history",
            "--report",
            IDENTIFIER,
            "--finding",
            IDENTIFIER_2,
            "--limit",
            "10",
        ],
    }
    actual = {}
    for mode in ("remote", "local"):
        actual[mode] = {}
        for name, arguments in commands.items():
            exit_code, body = invoke(
                ["--mode", mode, "--state", str(tmp_path / f"{mode}.enc"), *arguments],
                capsys,
            )
            assert exit_code == (5 if name == "check show" else 0)
            actual[mode][name] = body
        CheckPreview.model_validate(actual[mode]["check run"]["data"])
        AssessmentListData.model_validate(actual[mode]["check list"]["data"])
        [CheckRunView.model_validate(item) for item in actual[mode]["check list"]["items"]]
        CheckReportData.model_validate(actual[mode]["check show"]["data"])
        FindingDecisionData.model_validate(actual[mode]["check decide"]["data"])
        AssessmentListData.model_validate(actual[mode]["check history"]["data"])
        [
            FindingDecisionView.model_validate(item)
            for item in actual[mode]["check history"]["items"]
        ]
    snapshot = Path(__file__).with_name("snapshots") / "check-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(
            json.dumps(actual, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    assert actual == json.loads(snapshot.read_text(encoding="utf-8"))


def test_check_cli_public_exit_codes_and_schema(monkeypatch, tmp_path, capsys):
    application = check_interface(monkeypatch)
    install_transport(monkeypatch, application)
    common = ["--mode", "remote", "--state", str(tmp_path / "remote.enc")]

    code, body = invoke([*common, "check", "run", "--task", IDENTIFIER], capsys)
    assert code == 2 and body["data"]["error"]["code"] == "invalid_input"

    async def timed_out(job_id, limit_seconds):
        raise ServiceError("wait_timeout", "Synthetic wait timeout", 408, 3)

    monkeypatch.setattr(cli, "wait_for_job", timed_out)
    code, body = invoke(
        [
            *common,
            "check",
            "run",
            "--task",
            IDENTIFIER,
            "--draft",
            IDENTIFIER_2,
            "--as-of",
            "2026-10-04",
            "--expected-input-hash",
            SHA,
            "--wait",
        ],
        capsys,
    )
    assert code == 3 and body["data"]["error"]["code"] == "wait_timeout"
    assert body["data"]["job_id"] == IDENTIFIER

    fatal = check_interface(monkeypatch, fail_show=True)
    install_transport(monkeypatch, fatal)
    code, body = invoke([*common, "check", "show", "--id", IDENTIFIER], capsys)
    assert code == 4 and body["data"]["error"]["code"] == "not_found"

    schema = command_schema(cli.app)["commands"]
    assert set(name for name in schema if name.startswith("check ")) == {
        "check run",
        "check list",
        "check show",
        "check decide",
        "check history",
    }
    assert all(schema[name].get("output") for name in schema if name.startswith("check "))


def test_check_cli_queue_failure_is_retryable(monkeypatch, tmp_path, capsys):
    application = check_interface(monkeypatch, fail_queue=True)
    install_transport(monkeypatch, application)
    code, body = invoke(
        [
            "--mode",
            "local",
            "--state",
            str(tmp_path / "local.enc"),
            "check",
            "run",
            "--task",
            IDENTIFIER,
            "--draft",
            IDENTIFIER_2,
            "--as-of",
            "2026-10-04",
            "--expected-input-hash",
            SHA,
        ],
        capsys,
    )
    assert code == 3 and body["data"]["error"]["code"] == "queue_unavailable"
    assert body["data"]["job_id"] == IDENTIFIER


@pytest.mark.parametrize(
    "status,error_exit,expected_exit",
    [
        ("failed", 2, 2),
        ("failed", 3, 3),
        ("failed", 4, 4),
        ("cancelled", 2, 4),
    ],
)
def test_check_job_status_uses_terminal_error_exit(
    monkeypatch, tmp_path, capsys, status, error_exit, expected_exit
):
    application = check_interface(monkeypatch, job_status=status, job_exit_code=error_exit)
    install_transport(monkeypatch, application)
    code, body = invoke(
        [
            "--mode",
            "remote",
            "--state",
            str(tmp_path / "remote.enc"),
            "job",
            "status",
            IDENTIFIER,
        ],
        capsys,
    )
    assert code == expected_exit and body["ok"] is False
    assert body["data"]["kind"] == "check" and body["data"]["status"] == status


def test_check_job_status_cross_org_not_found_is_fatal(monkeypatch, tmp_path, capsys):
    application = check_interface(monkeypatch, job_crossorg=True)
    install_transport(monkeypatch, application)
    code, body = invoke(
        [
            "--mode",
            "local",
            "--state",
            str(tmp_path / "local.enc"),
            "job",
            "status",
            IDENTIFIER,
        ],
        capsys,
    )
    assert code == 4 and body["data"]["error"]["code"] == "not_found"


def test_check_job_status_rejects_malformed_remote_exit_code(monkeypatch, tmp_path, capsys):
    application = check_interface(monkeypatch, job_status="failed", job_exit_code=9)
    install_transport(monkeypatch, application)
    code, body = invoke(
        [
            "--mode",
            "remote",
            "--state",
            str(tmp_path / "remote.enc"),
            "job",
            "status",
            IDENTIFIER,
        ],
        capsys,
    )
    assert code == 4
    assert body["data"]["error"]["code"] == "invalid_server_response"


def test_check_run_wait_returns_job_result_and_partial_exit(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "call",
        lambda *args, **kwargs: {
            "ok": True,
            "command": "check run",
            "data": {"job_id": IDENTIFIER, "status": "queued", "cached": False},
            "items": [],
            "warnings": [],
            "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
            "duration_ms": 1,
        },
    )

    async def terminal(job_id, limit_seconds):
        assert str(job_id) == IDENTIFIER and limit_seconds == 5
        return {
            "ok": False,
            "command": "job status",
            "data": {
                "status": "succeeded",
                "result": {
                    "report_id": IDENTIFIER_2,
                    "job_id": IDENTIFIER,
                    "completion": "partial",
                    "usage_record_ids": [],
                    "charge": "0",
                    "billing_currency": "CNY",
                    "stop_reason": "unassessed_requirements",
                    "checked_requirements": 3,
                    "finding_count": 1,
                    "unassessed_requirements": 1,
                },
            },
            "items": [],
            "warnings": ["unassessed_requirements"],
            "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
            "duration_ms": 1,
        }

    monkeypatch.setattr(cli, "wait_for_job", terminal)
    code, body = invoke(
        [
            "check",
            "run",
            "--task",
            IDENTIFIER,
            "--draft",
            IDENTIFIER_2,
            "--as-of",
            "2026-10-04",
            "--expected-input-hash",
            SHA,
            "--wait",
            "--timeout",
            "5",
        ],
        capsys,
    )
    assert code == 5 and body["ok"] is False
    assert body["data"]["report_id"] == IDENTIFIER_2
    assert set(body["data"]) == {
        "report_id",
        "job_id",
        "completion",
        "usage_record_ids",
        "charge",
        "billing_currency",
        "stop_reason",
        "checked_requirements",
        "finding_count",
        "unassessed_requirements",
    }
    assert body["warnings"] == ["unassessed_requirements"]


def test_check_run_wait_rejects_invalid_job_result_as_server_error(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "call",
        lambda *args, **kwargs: {
            "ok": True,
            "command": "check run",
            "data": {"job_id": IDENTIFIER, "status": "queued", "cached": False},
            "items": [],
            "warnings": [],
            "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
            "duration_ms": 1,
        },
    )

    async def malformed(job_id, limit_seconds):
        return {
            "ok": True,
            "command": "job status",
            "data": {
                "status": "succeeded",
                "result": {"report_id": IDENTIFIER_2, "unexpected": True},
            },
            "items": [],
            "warnings": [],
            "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
            "duration_ms": 1,
        }

    monkeypatch.setattr(cli, "wait_for_job", malformed)
    code, body = invoke(
        [
            "check",
            "run",
            "--task",
            IDENTIFIER,
            "--draft",
            IDENTIFIER_2,
            "--as-of",
            "2026-10-04",
            "--expected-input-hash",
            SHA,
            "--wait",
        ],
        capsys,
    )
    assert code == 4
    assert body["data"] == {
        "error": {
            "code": "invalid_server_response",
            "message": "Server returned an invalid check job result",
            "exit_code": 4,
        },
        "job_id": IDENTIFIER,
    }


@pytest.mark.parametrize(
    "arguments,code",
    [
        (
            [
                "check",
                "run",
                "--task",
                IDENTIFIER,
                "--draft",
                IDENTIFIER_2,
                "--as-of",
                "2026-10-04",
                "--mode",
                "combined",
                "--dry-run",
            ],
            "check_mode_unavailable",
        ),
        (
            [
                "check",
                "run",
                "--task",
                IDENTIFIER,
                "--draft",
                IDENTIFIER_2,
                "--as-of",
                "2026-02-30",
                "--dry-run",
            ],
            "invalid_date",
        ),
    ],
)
def test_check_run_rejects_unavailable_mode_and_invalid_date(arguments, code, capsys):
    exit_code, body = invoke(arguments, capsys)
    assert exit_code == 2
    assert body["data"]["error"]["code"] == code
