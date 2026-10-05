"""Console assessment CLI acceptance at the public CLI/ASGI boundary."""

import json
import os
from pathlib import Path

import pytest
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.schema import command_schema
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from test_check_cli import IDENTIFIER, IDENTIFIER_2, SHA, install_transport, invoke
from test_score_cli import write_inputs


def rubric_summary():
    return {
        "id": IDENTIFIER,
        "org_id": IDENTIFIER,
        "task_id": IDENTIFIER,
        "extraction_job_id": IDENTIFIER_2,
        "document_id": IDENTIFIER,
        "input_hash": SHA,
        "prior_rubric_id": None,
        "version": 1,
        "revision": 1,
        "state": "candidate",
        "validity": "current",
        "created_at": "2026-10-04T00:00:00Z",
        "section_count": 1,
        "item_count": 1200,
        "completeness": {
            "scoring_requirement_count": 1200,
            "covered_requirement_count": 0,
            "pending_requirements": 1200,
            "duplicate_groups": 1,
            "unconfirmed_sections": 1,
            "unconfirmed_items": 1200,
            "normalization_errors": 0,
            "section_aggregation_rules_confirmed": False,
            "overall_aggregation_rule_confirmed": False,
            "complete": False,
        },
        "overall_aggregation": "sum",
        "overall_aggregation_assessable": True,
        "overall_rule_text": None,
        "overall_score_range": {"minimum": "0", "maximum": "10"},
        "overall_cap": None,
        "actions": [],
    }


def rubric_console_interface(requests, replacement):
    application = FastAPI()

    @application.api_route("/v4/{path:path}", methods=["GET", "POST"])
    async def boundary(path: str, request: Request):
        query = dict(request.query_params)
        recorded = {"method": request.method, "path": "/" + path, "params": query}
        if request.method == "POST":
            recorded["json"] = await request.json()
        requests.append(recorded)
        items = []
        if request.method == "POST":
            command = "score rubric revise" if path.endswith("revisions") else "score rubric decide"
            data = rubric_summary()
        elif path.endswith("/score-rubrics"):
            command = "score rubric list"
            data = {"task_id": IDENTIFIER, "total": 1, "next_cursor": None}
            items = [rubric_summary()]
        else:
            command = "score rubric show"
            if query["part"] == "summary":
                data = rubric_summary()
            elif query["part"] == "replacement":
                data = {
                    "rubric_id": IDENTIFIER,
                    "prior_rubric_id": IDENTIFIER_2,
                    "snapshot_sha256": SHA,
                    "replacement": replacement,
                }
            else:
                data = {
                    "task_id": IDENTIFIER,
                    "parent_id": IDENTIFIER,
                    "part": query["part"],
                    "snapshot": "rubric-revision-1",
                    "total": 1200,
                    "filtered_total": 0,
                    "returned": 0,
                    "next_cursor": None,
                    "validity": "current",
                    "parent_revision": 1,
                    "subject_actions": [],
                }
        return Result(ok=True, command=command, data=data, items=items).model_dump(mode="json")

    return application


def score_summary(total_status="unavailable"):
    return {
        "report": report_header(),
        "rubric_id": IDENTIFIER_2,
        "rubric_version": 1,
        "assessed_items": 1,
        "unassessable_items": 1,
        "section_count": 1,
        "overall_aggregation": "sum",
        "overall_aggregation_assessable": True,
        "overall_rule_text": None,
        "overall_cap": None,
        "assessed_subtotal": "0",
        "total_status": total_status,
        "possible_range": {"minimum": "0", "maximum": "10"},
        "estimated_total": None,
    }


def score_console_interface(requests, total_status="unavailable"):
    application = FastAPI()

    @application.get("/v4/{path:path}")
    async def boundary(path: str, request: Request):
        query = dict(request.query_params)
        requests.append({"method": request.method, "path": "/" + path, "params": query})
        items = []
        if path.endswith("/scores"):
            command = "score list"
            data = {"task_id": IDENTIFIER, "total": 1, "next_cursor": None}
            items = [score_summary(total_status)]
        else:
            command = "score show"
            if query["part"] == "summary":
                data = score_summary(total_status)
            else:
                data = {
                    "task_id": IDENTIFIER,
                    "parent_id": IDENTIFIER,
                    "part": query["part"],
                    "snapshot": "score-snapshot",
                    "total": 1200,
                    "filtered_total": 0,
                    "returned": 0,
                    "next_cursor": None,
                    "validity": "stale",
                    "parent_revision": None,
                    "subject_actions": [],
                }
        return Result(
            ok=False, command=command, data=data, items=items, warnings=["unassessable_items"]
        ).model_dump(mode="json")

    return application


def report_header():
    return {
        "id": IDENTIFIER,
        "org_id": IDENTIFIER,
        "task_id": IDENTIFIER,
        "job_id": IDENTIFIER,
        "extraction_job_id": IDENTIFIER_2,
        "document_id": IDENTIFIER,
        "draft_id": IDENTIFIER_2,
        "input_hash": SHA,
        "assessment_date": "2026-10-04",
        "created_at": "2026-10-04T00:00:00Z",
        "completion": "partial",
        "validity": "stale",
        "invalidation_codes": ["draft_changed"],
        "notice_count": 1,
        "advisory_only": True,
    }


def check_summary():
    return {
        "report": report_header(),
        "mode": "rules",
        "item_count": 1200,
        "finding_count": 0,
        "unassessed_count": 1,
        "certificate_count": 0,
        "groups": [],
    }


def console_interface(requests, *, error_exit=None):
    application = FastAPI()

    @application.api_route("/v4/{path:path}", methods=["GET", "POST"])
    async def boundary(path: str, request: Request):
        query = dict(request.query_params)
        requests.append({"method": request.method, "path": "/" + path, "params": query})
        if error_exit is not None:
            return JSONResponse(
                status_code=409,
                content=Result(
                    ok=False,
                    command="check show",
                    data={
                        "error": {
                            "code": "assessment_view_changed",
                            "message": "Refresh",
                            "exit_code": error_exit,
                        }
                    },
                ).model_dump(mode="json"),
            )
        items = []
        ok = True
        if path.endswith("/assessment-inputs"):
            command = "assessment inputs"
            data = {
                "org_id": IDENTIFIER,
                "task_id": IDENTIFIER,
                "extraction_job_id": IDENTIFIER_2,
                "document_id": IDENTIFIER,
                "latest_draft": None,
                "current_draft": None,
                "redaction_enabled": True,
                "redaction_revision": 1,
                "task_budget": {
                    "org_id": IDENTIFIER,
                    "task_id": IDENTIFIER,
                    "revision": 1,
                    "limit": None,
                    "currency": "CNY",
                    "state": "active",
                    "spent": "0",
                    "reserved": "0",
                    "available": None,
                    "unpriced_calls": 0,
                    "unresolved_calls": 0,
                    "history_complete": True,
                    "as_of": "2026-10-04T00:00:00Z",
                },
                "actions": [],
            }
        elif path.endswith("/assessment-citation"):
            command = "assessment citation"
            data = {
                "parent_id": IDENTIFIER,
                "entry_id": IDENTIFIER_2,
                "kind": "tender",
                "verified": True,
                "document_id": IDENTIFIER,
                "page": 1,
                "text_kind": "context",
                "window": {
                    "text": "合成引用",
                    "offset": 0,
                    "total_characters": 8,
                    "next_offset": 4,
                },
                "quote_start": 0,
                "quote_end": 4,
                "fix": {
                    "task_id": IDENTIFIER,
                    "extraction_job_id": IDENTIFIER_2,
                    "requirement_id": IDENTIFIER,
                    "current_card_id": None,
                    "historical_card_revision_id": None,
                },
            }
        elif path.endswith("/jobs"):
            command = "assessment jobs"
            data = {"task_id": IDENTIFIER, "kind": query["kind"], "total": 0, "next_cursor": None}
        elif path == f"tasks/{IDENTIFIER}/checks":
            command = "check list"
            data = {"task_id": IDENTIFIER, "total": 1200, "next_cursor": "next-page"}
            items = [check_summary()]
        else:
            command = "check show"
            ok = False
            if query["part"] == "summary":
                data = check_summary()
            else:
                data = {
                    "task_id": IDENTIFIER,
                    "parent_id": IDENTIFIER,
                    "part": query["part"],
                    "snapshot": "synthetic-snapshot",
                    "total": 1200,
                    "filtered_total": 0,
                    "returned": 0,
                    "next_cursor": None,
                    "validity": "stale",
                    "parent_revision": None,
                    "subject_actions": [],
                }
        return Result(
            ok=ok, command=command, data=data, items=items, warnings=["synthetic"] if not ok else []
        ).model_dump(mode="json")

    return application


def test_check_console_cli_local_remote_snapshots(monkeypatch, tmp_path, capsys):
    requests = []
    install_transport(monkeypatch, console_interface(requests))
    commands = {
        "assessment jobs": [
            "assessment",
            "jobs",
            "--task",
            IDENTIFIER,
            "--kind",
            "check",
            "--extraction-job",
            IDENTIFIER_2,
            "--limit",
            "25",
        ],
        "check list console": [
            "check",
            "list",
            "--task",
            IDENTIFIER,
            "--view",
            "console",
            "--extraction-job",
            IDENTIFIER_2,
            "--cursor",
            "first-page",
            "--limit",
            "25",
        ],
        "check summary": [
            "check",
            "show",
            "--id",
            IDENTIFIER,
            "--view",
            "console",
            "--part",
            "summary",
        ],
        "check findings": [
            "check",
            "show",
            "--id",
            IDENTIFIER,
            "--view",
            "console",
            "--part",
            "findings",
            "--severity",
            "deduction_risk",
            "--domain",
            "technical",
            "--status",
            "dismissed",
            "--requirement",
            IDENTIFIER_2,
            "--entry",
            IDENTIFIER,
            "--cursor",
            "first-page",
            "--limit",
            "25",
        ],
    }
    citation_input = tmp_path / "citation.json"
    citation_input.write_text(
        json.dumps(
            {
                "parent_kind": "check",
                "parent_id": IDENTIFIER,
                "part": "finding",
                "entry_id": IDENTIFIER_2,
            }
        ),
        encoding="utf-8",
    )
    commands.update(
        {
            "assessment inputs": [
                "assessment",
                "inputs",
                "--task",
                IDENTIFIER,
                "--extraction-job",
                IDENTIFIER_2,
            ],
            "assessment citation": [
                "assessment",
                "citation",
                "--task",
                IDENTIFIER,
                "--input",
                str(citation_input),
            ],
            **{
                f"check {part}": [
                    "check",
                    "show",
                    "--id",
                    IDENTIFIER,
                    "--view",
                    "console",
                    "--part",
                    part,
                ]
                for part in ("coverage", "certificates", "notices")
            },
        }
    )
    actual = {}
    for mode in ("local", "remote"):
        actual[mode] = {}
        for name, arguments in commands.items():
            code, body = invoke(
                ["--mode", mode, "--state", str(tmp_path / f"{mode}.enc"), *arguments], capsys
            )
            assert code == (5 if name.startswith("check ") and name != "check list console" else 0)
            actual[mode][name] = body
    assert requests[: len(commands)] == requests[len(commands) :]
    assert requests[0] == {
        "method": "GET",
        "path": f"/tasks/{IDENTIFIER}/jobs",
        "params": {"kind": "check", "extraction_job_id": IDENTIFIER_2, "limit": "25"},
    }
    assert requests[1]["params"] == {
        "view": "console",
        "extraction_job_id": IDENTIFIER_2,
        "cursor": "first-page",
        "limit": "25",
    }
    assert requests[2]["params"] == {"view": "console", "part": "summary"}
    assert requests[3]["params"] == {
        "view": "console",
        "part": "findings",
        "severity": "deduction_risk",
        "domain": "technical",
        "status": "dismissed",
        "requirement_id": IDENTIFIER_2,
        "entry_id": IDENTIFIER,
        "cursor": "first-page",
        "limit": "25",
    }
    snapshot = Path(__file__).with_name("snapshots") / "console-check-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(
            json.dumps(actual, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    assert actual == json.loads(snapshot.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "arguments",
    [
        ["check", "list", "--task", IDENTIFIER, "--view", "console", "--limit", "101"],
        ["check", "list", "--task", IDENTIFIER, "--extraction-job", IDENTIFIER_2],
        ["check", "show", "--id", IDENTIFIER, "--part", "findings"],
        [
            "check",
            "show",
            "--id",
            IDENTIFIER,
            "--view",
            "console",
            "--part",
            "summary",
            "--limit",
            "10",
        ],
        [
            "check",
            "show",
            "--id",
            IDENTIFIER,
            "--view",
            "console",
            "--part",
            "findings",
            "--severity",
            "unknown",
        ],
        ["assessment", "jobs", "--task", IDENTIFIER, "--kind", "parse"],
        ["assessment", "jobs", "--task", IDENTIFIER, "--kind", "check", "--limit", "101"],
        [
            "check",
            "show",
            "--id",
            IDENTIFIER,
            "--view",
            "console",
            "--part",
            "coverage",
            "--severity",
            "info",
        ],
        [
            "check",
            "show",
            "--id",
            IDENTIFIER,
            "--view",
            "console",
            "--part",
            "notices",
            "--requirement",
            IDENTIFIER_2,
        ],
    ],
)
def test_console_cli_rejects_invalid_filters_without_request(monkeypatch, capsys, arguments):
    requests = []
    install_transport(monkeypatch, console_interface(requests))
    code, body = invoke(arguments, capsys)
    assert code == 2 and body["ok"] is False
    assert requests == []


@pytest.mark.parametrize("exit_code", [2, 3, 4])
def test_console_cli_keeps_explicit_service_exit(monkeypatch, capsys, exit_code):
    install_transport(monkeypatch, console_interface([], error_exit=exit_code))
    code, body = invoke(["check", "show", "--id", IDENTIFIER, "--view", "console"], capsys)
    assert code == exit_code
    assert body["data"]["error"]["exit_code"] == exit_code


def test_console_cli_schema_has_registered_typed_variants():
    schema = command_schema(cli.app)
    commands = schema["commands"]
    assert commands["assessment inputs"]["output"]["title"] == "AssessmentInputsData"
    assert commands["assessment citation"]["input"]["title"] == "CitationRequest"
    assert commands["assessment citation"]["output"]["title"] == "CitationContextData"
    assert commands["assessment jobs"]["input"]["title"] == "AssessmentJobQuery"
    assert commands["assessment jobs"]["items"]["title"] == "AssessmentJobView"
    variants = commands["check show"]["variants"]["console"]
    assert variants["summary"]["output"]["title"] == "CheckSummaryData"
    assert variants["findings"]["input"]["title"] == "CheckPageRequest"
    assert variants["findings"]["items"]["title"] == "FindingView"
    assert commands["check list"]["variants"]["console"]["items"]["title"] == "CheckSummaryData"


def test_assessment_inputs_and_citation_cli_typed_queries(monkeypatch, tmp_path, capsys):
    requests = []
    install_transport(monkeypatch, console_interface(requests))
    query = {
        "parent_kind": "check",
        "parent_id": IDENTIFIER,
        "part": "finding",
        "entry_id": IDENTIFIER_2,
    }
    path = tmp_path / "citation.json"
    path.write_text(json.dumps(query), encoding="utf-8")
    for mode in ("local", "remote"):
        code, body = invoke(
            [
                "--mode",
                mode,
                "assessment",
                "inputs",
                "--task",
                IDENTIFIER,
                "--extraction-job",
                IDENTIFIER_2,
            ],
            capsys,
        )
        assert code == 0 and body["data"]["current_draft"] is None
        code, body = invoke(
            ["--mode", mode, "assessment", "citation", "--task", IDENTIFIER, "--input", str(path)],
            capsys,
        )
        assert code == 0 and body["data"]["window"]["next_offset"] == 4
    assert requests[:2] == requests[2:]
    assert requests[0]["params"] == {"job": IDENTIFIER_2}
    assert requests[1]["params"] == {
        **query,
        "origin": "source",
        "citation_index": "0",
        "text": "context",
        "offset": "0",
        "limit": "4000",
    }
    query["parent_kind"] = "score"
    path.write_text(json.dumps(query), encoding="utf-8")
    code, body = invoke(
        ["assessment", "citation", "--task", IDENTIFIER, "--input", str(path)], capsys
    )
    assert code == 2 and body["ok"] is False
    assert len(requests) == 4


def test_console_variants_require_v4_and_are_absent_from_legacy_schema(monkeypatch, capsys):
    requests = []
    install_transport(monkeypatch, console_interface(requests))
    for arguments in (
        ["assessment", "inputs", "--task", IDENTIFIER, "--extraction-job", IDENTIFIER_2],
        ["check", "show", "--id", IDENTIFIER, "--view=console"],
    ):
        code, body = invoke(["--contract-version", "3.0", *arguments], capsys)
        assert code == 2 and body["data"]["error"]["code"] == "invalid_input"
    assert requests == []
    commands = command_schema(cli.app, "3.0")["commands"]
    assert not any(name.startswith("assessment ") for name in commands)
    assert "variants" not in commands["check show"]
    assert not any(
        parameter["name"] in {"view", "extraction_job"}
        for parameter in commands["check list"]["cli_parameters"]
    )


def test_rubric_console_cli_local_remote_snapshots(monkeypatch, tmp_path, capsys):
    paths = write_inputs(tmp_path)
    replacement = json.loads(paths["revise"].read_text())
    requests = []
    install_transport(monkeypatch, rubric_console_interface(requests, replacement))
    shared = ["--task", IDENTIFIER, "--rubric", IDENTIFIER]
    commands = {
        "rubric list": [
            "score",
            "rubric",
            "list",
            "--task",
            IDENTIFIER,
            "--view",
            "console",
            "--extraction-job",
            IDENTIFIER_2,
        ],
        **{
            f"rubric {part}": [
                "score",
                "rubric",
                "show",
                *shared,
                "--view",
                "console",
                "--part",
                part,
            ]
            for part in ("summary", "sections", "items", "coverage", "blockers", "replacement")
        },
        "rubric revise": [
            "score",
            "rubric",
            "revise",
            *shared,
            "--input",
            str(paths["revise"]),
            "--view",
            "console",
        ],
        "rubric decide": [
            "score",
            "rubric",
            "decide",
            *shared,
            "--input",
            str(paths["set"]),
            "--view",
            "console",
        ],
    }
    commands["rubric items"] += [
        "--domain",
        "technical",
        "--state",
        "candidate",
        "--section",
        IDENTIFIER_2,
        "--requirement",
        IDENTIFIER,
        "--entry",
        IDENTIFIER,
        "--cursor",
        "items-page",
        "--limit",
        "25",
    ]
    commands["rubric blockers"] += ["--group", SHA, "--cursor", "group-page", "--limit", "25"]
    actual = {}
    for mode in ("local", "remote"):
        actual[mode] = {}
        for name, arguments in commands.items():
            code, body = invoke(["--mode", mode, *arguments], capsys)
            assert code == 0, body
            actual[mode][name] = body
    assert requests[: len(commands)] == requests[len(commands) :]
    item_query = requests[3]["params"]
    assert item_query == {
        "view": "console",
        "part": "items",
        "domain": "technical",
        "state": "candidate",
        "section_id": IDENTIFIER_2,
        "requirement_id": IDENTIFIER,
        "entry_id": IDENTIFIER,
        "cursor": "items-page",
        "limit": "25",
    }
    assert requests[7]["params"] == requests[8]["params"] == {"view": "console"}
    assert requests[7]["json"]["expected_input_hash"] == SHA
    assert requests[8]["json"]["expected_revision"] == 1
    snapshot = Path(__file__).with_name("snapshots") / "console-rubric-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(
            json.dumps(actual, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    assert actual == json.loads(snapshot.read_text(encoding="utf-8"))


def test_rubric_console_schema_registers_exact_parts():
    commands = command_schema(cli.app)["commands"]
    parts = commands["score rubric show"]["variants"]["console"]
    assert parts["summary"]["output"]["title"] == "RubricSummaryData"
    assert parts["replacement"]["output"]["title"] == "RubricReplacementData"
    assert parts["sections"]["items"]["title"] == "ConsoleRubricSectionView"
    assert parts["blockers"]["items"]["title"] == "Notice"
    assert parts["items"]["input"]["title"] == "RubricPageRequest"
    assert (
        commands["score rubric list"]["variants"]["console"]["items"]["title"]
        == "RubricSummaryData"
    )
    for action in ("revise", "decide"):
        assert (
            commands[f"score rubric {action}"]["variants"]["console"]["output"]["title"]
            == "RubricSummaryData"
        )


def test_score_console_cli_local_remote_snapshots(monkeypatch, capsys):
    requests = []
    shared = ["--task", IDENTIFIER, "--report", IDENTIFIER]
    commands = {
        "score list": [
            "score",
            "list",
            "--task",
            IDENTIFIER,
            "--view",
            "console",
            "--extraction-job",
            IDENTIFIER_2,
            "--limit",
            "25",
        ],
        **{
            f"score {part}": ["score", "show", *shared, "--view", "console", "--part", part]
            for part in ("summary", "sections", "items", "notices")
        },
    }
    commands["score items"] += [
        "--section-key",
        "technical",
        "--outcome",
        "unassessable",
        "--requirement",
        IDENTIFIER_2,
        "--entry",
        IDENTIFIER,
        "--cursor",
        "score-page",
        "--limit",
        "25",
    ]
    actual = {}
    for total_status in ("unavailable", "range_only"):
        install_transport(monkeypatch, score_console_interface(requests, total_status))
        actual[total_status] = {}
        for mode in ("local", "remote"):
            actual[total_status][mode] = {}
            for name, arguments in commands.items():
                code, body = invoke(["--mode", mode, *arguments], capsys)
                assert code == 5 and body["ok"] is False
                actual[total_status][mode][name] = body
                if name == "score summary":
                    assert body["data"]["assessed_subtotal"] == "0"
                    assert body["data"]["estimated_total"] is None
    assert requests[:5] == requests[5:10] == requests[10:15] == requests[15:]
    assert requests[3]["params"] == {
        "view": "console",
        "part": "items",
        "section_key": "technical",
        "outcome": "unassessable",
        "requirement_id": IDENTIFIER_2,
        "entry_id": IDENTIFIER,
        "cursor": "score-page",
        "limit": "25",
    }
    snapshot = Path(__file__).with_name("snapshots") / "console-score-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(
            json.dumps(actual, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    assert actual == json.loads(snapshot.read_text(encoding="utf-8"))


def test_score_console_schema_registers_exact_parts():
    commands = command_schema(cli.app)["commands"]
    parts = commands["score show"]["variants"]["console"]
    assert parts["summary"]["output"]["title"] == "ScoreSummaryData"
    assert parts["sections"]["items"]["title"] == "ScoreSectionSummary"
    assert parts["items"]["items"]["title"] == "ScoreItemView"
    assert parts["items"]["input"]["title"] == "ScorePageRequest"
    assert parts["notices"]["items"]["title"] == "Notice"
    assert commands["score list"]["variants"]["console"]["items"]["title"] == "ScoreSummaryData"
