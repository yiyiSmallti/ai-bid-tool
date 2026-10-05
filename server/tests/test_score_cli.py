"""Score rubric CLI snapshots at the public, DB-free transport boundary."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from app.core.errors import ServiceError
from app.schemas.check_contracts import AssessmentListData
from app.schemas.contracts import Result
from app.schemas.score_contracts import (
    RubricClassificationView,
    RubricClassifyRequest,
    RubricCoverageDecisionRequest,
    RubricCoverageDecisionView,
    RubricDecisionView,
    RubricGenerateRequest,
    RubricGenerateResult,
    RubricHistoryItem,
    RubricItemDecisionRequest,
    RubricPreview,
    RubricReportData,
    RubricReviseRequest,
    RubricSectionDecisionRequest,
    RubricSetDecisionRequest,
    RubricSetView,
)
from bid_cli import main as cli
from bid_cli.client import Client, State
from bid_cli.schema import command_schema
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter

ORG = "00000000-0000-0000-0000-000000000001"
TASK = "00000000-0000-0000-0000-000000000002"
EXTRACTION = "00000000-0000-0000-0000-000000000003"
DOCUMENT = "00000000-0000-0000-0000-000000000004"
RUBRIC = "00000000-0000-0000-0000-000000000005"
SECTION = "00000000-0000-0000-0000-000000000006"
ITEM = "00000000-0000-0000-0000-000000000007"
REQUIREMENT = "00000000-0000-0000-0000-000000000008"
ACTOR = "00000000-0000-0000-0000-000000000009"
JOB = "00000000-0000-0000-0000-000000000010"
SHA = "a" * 64
NOW = "2026-10-04T00:00:00Z"

SOURCE = {
    "document_id": DOCUMENT,
    "chunk_id": DOCUMENT,
    "page": 7,
    "location": None,
    "quote": "Technical response quality: 0 to 10 points.",
}
COMPLETENESS = {
    "scoring_requirement_count": 1,
    "covered_requirement_count": 0,
    "pending_requirement_ids": [REQUIREMENT],
    "unresolved_duplicate_fingerprint_groups": [],
    "unconfirmed_section_ids": [SECTION],
    "unconfirmed_item_ids": [ITEM],
    "normalization_errors": [],
    "section_aggregation_rules_confirmed": False,
    "overall_aggregation_rule_confirmed": False,
    "complete": False,
}
RUBRIC_VIEW = {
    "id": RUBRIC,
    "org_id": ORG,
    "task_id": TASK,
    "extraction_job_id": EXTRACTION,
    "document_id": DOCUMENT,
    "version": 1,
    "input_hash": SHA,
    "normalization_rule_version": "rubric-normalization-v1",
    "prompt_version": "rubric-prompt-v1",
    "schema_version": "rubric-wire-v1",
    "state": "candidate",
    "revision": 1,
    "overall_aggregation": "sum",
    "overall_aggregation_assessable": True,
    "overall_rule_text": None,
    "overall_score_range": {"minimum": "0", "maximum": "10"},
    "overall_cap": None,
    "completeness": COMPLETENESS,
    "confirmed_by": None,
    "confirmed_at": None,
    "created_at": NOW,
}
SECTION_VIEW = {
    "id": SECTION,
    "org_id": ORG,
    "task_id": TASK,
    "rubric_id": RUBRIC,
    "key": "technical",
    "title": "Technical response",
    "order": 1,
    "aggregation": "sum",
    "aggregation_assessable": True,
    "aggregation_rule_text": None,
    "score_range": {"minimum": "0", "maximum": "10"},
    "weight": None,
    "cap": None,
    "included_in_overall_total": True,
    "ambiguity_reason": None,
    "review_domain": None,
    "source": SOURCE,
    "state": "candidate",
    "revision": 1,
    "confirmed_by": None,
    "confirmed_at": None,
}
ITEM_VIEW = {
    "id": ITEM,
    "org_id": ORG,
    "task_id": TASK,
    "rubric_id": RUBRIC,
    "section_id": SECTION,
    "requirement_id": REQUIREMENT,
    "category": "scoring",
    "key": "technical-response",
    "title": "Technical response quality",
    "rule_text": "Award 0 to 10 points.",
    "order": 1,
    "assessment_mode": "model_assessable",
    "score_range": {"minimum": "0", "maximum": "10"},
    "weight": None,
    "ambiguity_reason": None,
    "source": SOURCE,
    "fingerprint": SHA,
    "review_domain": None,
    "state": "candidate",
    "revision": 1,
    "confirmed_by": None,
    "confirmed_at": None,
}
COVERAGE_VIEW = {
    "id": REQUIREMENT,
    "org_id": ORG,
    "task_id": TASK,
    "rubric_id": RUBRIC,
    "requirement_id": REQUIREMENT,
    "source": SOURCE,
    "disposition": "pending",
    "rubric_item_ids": [],
    "canonical_requirement_id": None,
    "reason": None,
    "decided_by": None,
    "decided_at": None,
    "revision": 1,
}
RUBRIC_REPORT = {
    "rubric": RUBRIC_VIEW,
    "sections": [SECTION_VIEW],
    "items": [ITEM_VIEW],
    "coverage": [COVERAGE_VIEW],
}
CLASSIFICATION = {
    "kind": "classification",
    "id": ACTOR,
    "org_id": ORG,
    "task_id": TASK,
    "rubric_id": RUBRIC,
    "section_id": SECTION,
    "item_id": None,
    "revision": 2,
    "review_domain": "technical",
    "reason": "This section evaluates technical content.",
    "decided_by": ACTOR,
    "decided_at": NOW,
    "actor_kind": "session",
}
ITEM_CLASSIFICATION = {
    **CLASSIFICATION,
    "section_id": None,
    "item_id": ITEM,
    "reason": "This item evaluates technical content.",
}
SECTION_DECISION = {
    "kind": "decision",
    "id": ACTOR,
    "org_id": ORG,
    "task_id": TASK,
    "rubric_id": RUBRIC,
    "section_id": SECTION,
    "item_id": None,
    "revision": 2,
    "action": "confirm",
    "reason": "The aggregation rule matches the tender text.",
    "decided_by": ACTOR,
    "decided_at": NOW,
    "actor_kind": "session",
}
ITEM_DECISION = {
    **SECTION_DECISION,
    "section_id": None,
    "item_id": ITEM,
    "reason": "The item bounds match the tender text.",
}
COVERAGE_DECISION = {
    "kind": "coverage_decision",
    "id": ACTOR,
    "org_id": ORG,
    "task_id": TASK,
    "rubric_id": RUBRIC,
    "requirement_id": REQUIREMENT,
    "revision": 2,
    "action": "mapped",
    "rubric_item_ids": [ITEM],
    "canonical_requirement_id": None,
    "reason": "The requirement maps to this score item.",
    "decided_by": ACTOR,
    "decided_at": NOW,
    "actor_kind": "session",
}
REVISION = {
    "kind": "revision",
    "id": ACTOR,
    "org_id": ORG,
    "task_id": TASK,
    "rubric_id": RUBRIC,
    "prior_rubric_id": SECTION,
    "version": 2,
    "input_hash": SHA,
    "reason": "Corrected the item title and bounds.",
    "revised_by": ACTOR,
    "revised_at": NOW,
    "actor_kind": "session",
}


class InterfaceClient(Client):
    """Send both CLI mode labels through Client.request and the same ASGI app."""

    def __init__(self, mode: str, server: str, state: State, application: FastAPI):
        super().__init__(mode, server, state)
        self.application = application

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.application), base_url="http://interface"
        ) as transport:
            yield transport


def _result(*, data=None, items=None, warnings=None) -> dict:
    return Result(
        ok=True,
        command="score rubric interface",
        data=data or {},
        items=items or [],
        warnings=warnings or [],
    ).model_dump(mode="json")


def score_interface(*, fail_show: bool = False, fail_submit: bool = False) -> FastAPI:
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
                },
                **({"job_id": error.job_id} if error.job_id is not None else {}),
            },
        )
        return JSONResponse(status_code=error.status, content=body.model_dump(mode="json"))

    @application.post("/tasks/{task_id}/score-rubrics/preview", name="score_rubric_generate")
    async def generate_preview(task_id: UUID, request: RubricGenerateRequest):
        assert request.dry_run is True and str(task_id) == TASK
        preview = {
            "dry_run": True,
            "input": {
                "org_id": ORG,
                "task_id": TASK,
                "extraction_job_id": EXTRACTION,
                "document_id": DOCUMENT,
                "input_hash": SHA,
                "scope": "scoring_requirements",
            },
            "selected_item_ids": [REQUIREMENT],
            "estimated_cost": {"llm_tokens": 800, "ocr_pages": 0, "usd": 0.02},
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
            "reasoning": request.reasoning,
            "redaction_revision": 1,
            "redaction_rule_version": "redaction-v1",
            "redacted_counts": {},
            "max_charge": request.max_charge,
            "scoring_requirement_ids": [REQUIREMENT],
            "prompt_version": "rubric-prompt-v1",
            "schema_version": "rubric-wire-v1",
            "normalization_rule_version": "rubric-normalization-v1",
        }
        return _result(data=RubricPreview.model_validate(preview).model_dump(mode="json"))

    @application.post("/tasks/{task_id}/score-rubrics", name="score_rubric_generate")
    async def generate(task_id: UUID, request: RubricGenerateRequest):
        assert request.dry_run is False and str(task_id) == TASK
        if fail_submit:
            raise ServiceError(
                "queue_unavailable",
                "Rubric job was saved, but the queue is unavailable",
                503,
                3,
                job_id=JOB,
            )
        return _result(data={"job_id": JOB, "status": "queued", "cached": False})

    @application.get("/tasks/{task_id}/score-rubrics", name="score_rubric_list")
    async def listing(task_id: UUID, cursor: str | None = None, limit: int = 50):
        assert str(task_id) == TASK and cursor is None and limit in {20, 50}
        return _result(
            data={"task_id": TASK, "total": 1, "next_cursor": None},
            items=[RUBRIC_VIEW],
        )

    @application.get("/tasks/{task_id}/score-rubrics/{rubric_id}", name="score_rubric_show")
    async def showing(task_id: UUID, rubric_id: UUID):
        if fail_show:
            raise ServiceError("not_found", "Resource not found", 404, 4)
        assert str(task_id) == TASK and str(rubric_id) == RUBRIC
        return _result(data=RUBRIC_REPORT)

    @application.post(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/revisions",
        name="score_rubric_revise",
    )
    async def revise(task_id: UUID, rubric_id: UUID, request: RubricReviseRequest):
        assert str(task_id) == TASK and str(rubric_id) == RUBRIC and request.reason
        return _result(data=RUBRIC_REPORT)

    @application.post(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/sections/{section_id}/classification",
        name="score_rubric_classify",
    )
    async def classify(
        task_id: UUID,
        rubric_id: UUID,
        section_id: UUID,
        request: RubricClassifyRequest,
    ):
        assert request.review_domain == "technical"
        assert (str(task_id), str(rubric_id), str(section_id)) == (TASK, RUBRIC, SECTION)
        return _result(data=CLASSIFICATION)

    @application.post(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/items/{item_id}/classification",
        name="score_rubric_classify",
    )
    async def classify_item(
        task_id: UUID,
        rubric_id: UUID,
        item_id: UUID,
        request: RubricClassifyRequest,
    ):
        assert request.review_domain == "technical"
        assert (str(task_id), str(rubric_id), str(item_id)) == (TASK, RUBRIC, ITEM)
        return _result(data=ITEM_CLASSIFICATION)

    @application.post(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/sections/{section_id}/decisions",
        name="score_rubric_section_decide",
    )
    async def decide_section(
        task_id: UUID,
        rubric_id: UUID,
        section_id: UUID,
        request: RubricSectionDecisionRequest,
    ):
        assert request.action == "confirm" and str(section_id) == SECTION
        return _result(data=SECTION_DECISION)

    @application.post(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/items/{item_id}/decisions",
        name="score_rubric_item_decide",
    )
    async def decide_item(
        task_id: UUID,
        rubric_id: UUID,
        item_id: UUID,
        request: RubricItemDecisionRequest,
    ):
        assert request.action == "confirm" and str(item_id) == ITEM
        return _result(data=ITEM_DECISION)

    @application.post(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/coverage/{requirement_id}/decisions",
        name="score_rubric_coverage_decide",
    )
    async def decide_coverage(
        task_id: UUID,
        rubric_id: UUID,
        requirement_id: UUID,
        request: RubricCoverageDecisionRequest,
    ):
        assert request.rubric_item_ids == [UUID(ITEM)] and str(requirement_id) == REQUIREMENT
        return _result(data=COVERAGE_DECISION)

    @application.post(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/decisions",
        name="score_rubric_decide",
    )
    async def decide_set(task_id: UUID, rubric_id: UUID, request: RubricSetDecisionRequest):
        assert request.action == "confirm"
        confirmed = {
            **RUBRIC_VIEW,
            "state": "confirmed",
            "revision": 2,
            "completeness": {
                **COMPLETENESS,
                "covered_requirement_count": 1,
                "pending_requirement_ids": [],
                "unconfirmed_section_ids": [],
                "unconfirmed_item_ids": [],
                "section_aggregation_rules_confirmed": True,
                "overall_aggregation_rule_confirmed": True,
                "complete": True,
            },
            "confirmed_by": ACTOR,
            "confirmed_at": NOW,
        }
        return _result(data=RubricSetView.model_validate(confirmed).model_dump(mode="json"))

    @application.get(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/history",
        name="score_rubric_history",
    )
    async def history(task_id: UUID, rubric_id: UUID, cursor: str | None = None, limit: int = 50):
        assert cursor is None and limit in {10, 50}
        return _result(
            data={"task_id": TASK, "total": 2, "next_cursor": None},
            items=[CLASSIFICATION, REVISION],
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
    monkeypatch.setenv("BID_ORG", ORG)
    monkeypatch.setattr(
        cli,
        "Client",
        lambda mode, server, state: InterfaceClient(mode, server, state, application),
    )


def write_inputs(tmp_path: Path) -> dict[str, Path]:
    values = {
        "revise": {
            "expected_revision": 1,
            "expected_input_hash": SHA,
            "sections": [
                {
                    "source_section_id": SECTION,
                    "requirement_id": REQUIREMENT,
                    "key": "technical",
                    "title": "Technical response",
                    "order": 1,
                    "aggregation": "sum",
                    "score_range": {"minimum": "0", "maximum": "10"},
                    "included_in_overall_total": True,
                }
            ],
            "items": [
                {
                    "source_item_id": ITEM,
                    "requirement_id": REQUIREMENT,
                    "section_key": "technical",
                    "key": "technical-response",
                    "title": "Technical response quality",
                    "rule_text": "Award 0 to 10 points.",
                    "order": 1,
                    "assessment_mode": "model_assessable",
                    "score_range": {"minimum": "0", "maximum": "10"},
                }
            ],
            "coverage": [
                {
                    "requirement_id": REQUIREMENT,
                    "disposition": "mapped",
                    "rubric_item_keys": ["technical-response"],
                }
            ],
            "overall_aggregation": "sum",
            "overall_score_range": {"minimum": "0", "maximum": "10"},
            "reason": "Corrected the item title and bounds.",
        },
        "classify": {
            "expected_revision": 1,
            "expected_input_hash": SHA,
            "review_domain": "technical",
            "reason": "This section evaluates technical content.",
        },
        "section": {
            "expected_revision": 1,
            "expected_input_hash": SHA,
            "action": "confirm",
            "reason": "The aggregation rule matches the tender text.",
        },
        "item": {
            "expected_revision": 1,
            "expected_input_hash": SHA,
            "action": "confirm",
            "reason": "The item bounds match the tender text.",
        },
        "coverage": {
            "expected_revision": 1,
            "expected_input_hash": SHA,
            "action": "mapped",
            "rubric_item_ids": [ITEM],
            "reason": "The requirement maps to this score item.",
        },
        "set": {
            "expected_revision": 1,
            "expected_input_hash": SHA,
            "action": "confirm",
            "reason": "The rubric is complete.",
        },
    }
    paths = {}
    for name, value in values.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = path
    return paths


def test_score_rubric_cli_remote_and_local_snapshots_db_free(monkeypatch, tmp_path, capsys):
    application = score_interface()
    install_transport(monkeypatch, application)
    inputs = write_inputs(tmp_path)
    commands = {
        "score rubric generate": [
            "score",
            "rubric",
            "generate",
            "--task",
            TASK,
            "--extraction-job",
            EXTRACTION,
            "--reasoning",
            "high",
            "--max-charge",
            "1.25",
            "--dry-run",
        ],
        "score rubric list": [
            "score",
            "rubric",
            "list",
            "--task",
            TASK,
            "--limit",
            "20",
        ],
        "score rubric show": [
            "score",
            "rubric",
            "show",
            "--task",
            TASK,
            "--rubric",
            RUBRIC,
        ],
        "score rubric revise": [
            "score",
            "rubric",
            "revise",
            "--task",
            TASK,
            "--rubric",
            RUBRIC,
            "--input",
            str(inputs["revise"]),
        ],
        "score rubric classify": [
            "score",
            "rubric",
            "classify",
            "--task",
            TASK,
            "--rubric",
            RUBRIC,
            "--section",
            SECTION,
            "--input",
            str(inputs["classify"]),
        ],
        "score rubric classify item": [
            "score",
            "rubric",
            "classify",
            "--task",
            TASK,
            "--rubric",
            RUBRIC,
            "--item",
            ITEM,
            "--input",
            str(inputs["classify"]),
        ],
        "score rubric section decide": [
            "score",
            "rubric",
            "section",
            "decide",
            "--task",
            TASK,
            "--rubric",
            RUBRIC,
            "--section",
            SECTION,
            "--input",
            str(inputs["section"]),
        ],
        "score rubric item decide": [
            "score",
            "rubric",
            "item",
            "decide",
            "--task",
            TASK,
            "--rubric",
            RUBRIC,
            "--item",
            ITEM,
            "--input",
            str(inputs["item"]),
        ],
        "score rubric coverage decide": [
            "score",
            "rubric",
            "coverage",
            "decide",
            "--task",
            TASK,
            "--rubric",
            RUBRIC,
            "--requirement",
            REQUIREMENT,
            "--input",
            str(inputs["coverage"]),
        ],
        "score rubric decide": [
            "score",
            "rubric",
            "decide",
            "--task",
            TASK,
            "--rubric",
            RUBRIC,
            "--input",
            str(inputs["set"]),
        ],
        "score rubric history": [
            "score",
            "rubric",
            "history",
            "--task",
            TASK,
            "--rubric",
            RUBRIC,
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
            assert exit_code == 0
            actual[mode][name] = body
        RubricPreview.model_validate(actual[mode]["score rubric generate"]["data"])
        AssessmentListData.model_validate(actual[mode]["score rubric list"]["data"])
        [RubricSetView.model_validate(item) for item in actual[mode]["score rubric list"]["items"]]
        RubricReportData.model_validate(actual[mode]["score rubric show"]["data"])
        RubricReportData.model_validate(actual[mode]["score rubric revise"]["data"])
        RubricClassificationView.model_validate(actual[mode]["score rubric classify"]["data"])
        RubricClassificationView.model_validate(actual[mode]["score rubric classify item"]["data"])
        RubricDecisionView.model_validate(actual[mode]["score rubric section decide"]["data"])
        RubricDecisionView.model_validate(actual[mode]["score rubric item decide"]["data"])
        RubricCoverageDecisionView.model_validate(
            actual[mode]["score rubric coverage decide"]["data"]
        )
        RubricSetView.model_validate(actual[mode]["score rubric decide"]["data"])
        AssessmentListData.model_validate(actual[mode]["score rubric history"]["data"])
        [
            TypeAdapter(RubricHistoryItem).validate_python(item)
            for item in actual[mode]["score rubric history"]["items"]
        ]
    snapshot = Path(__file__).with_name("snapshots") / "score-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(
            json.dumps(actual, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    assert actual == json.loads(snapshot.read_text(encoding="utf-8"))


def test_score_rubric_cli_exit_codes_and_schema(monkeypatch, tmp_path, capsys):
    application = score_interface()
    install_transport(monkeypatch, application)
    common = ["--mode", "remote", "--state", str(tmp_path / "remote.enc")]

    code, body = invoke([*common, "score", "rubric", "generate", "--task", TASK], capsys)
    assert code == 2 and body["data"]["error"]["code"] == "invalid_input"

    code, body = invoke(
        [
            *common,
            "score",
            "rubric",
            "generate",
            "--task",
            TASK,
            "--extraction-job",
            EXTRACTION,
        ],
        capsys,
    )
    assert code == 2 and body["data"]["error"]["code"] == "invalid_input"

    classify = write_inputs(tmp_path)["classify"]
    code, body = invoke(
        [
            *common,
            "score",
            "rubric",
            "classify",
            "--task",
            TASK,
            "--rubric",
            RUBRIC,
            "--input",
            str(classify),
        ],
        capsys,
    )
    assert code == 2 and body["data"]["error"]["code"] == "invalid_input"

    fatal = score_interface(fail_show=True)
    install_transport(monkeypatch, fatal)
    code, body = invoke(
        [
            *common,
            "score",
            "rubric",
            "show",
            "--task",
            TASK,
            "--rubric",
            RUBRIC,
        ],
        capsys,
    )
    assert code == 4 and body["data"]["error"]["code"] == "not_found"

    schema = command_schema(cli.app)["commands"]
    names = {name for name in schema if name.startswith("score ")}
    assert names == {
        "score rubric generate",
        "score rubric list",
        "score rubric show",
        "score rubric revise",
        "score rubric classify",
        "score rubric section decide",
        "score rubric item decide",
        "score rubric coverage decide",
        "score rubric decide",
        "score rubric history",
        "score run",
        "score list",
        "score show",
    }
    assert all(schema[name].get("output") for name in names)


def test_score_rubric_queue_failure_preserves_job_id(monkeypatch, tmp_path, capsys):
    application = score_interface(fail_submit=True)
    install_transport(monkeypatch, application)
    code, body = invoke(
        [
            "--mode",
            "local",
            "--state",
            str(tmp_path / "local.enc"),
            "score",
            "rubric",
            "generate",
            "--task",
            TASK,
            "--extraction-job",
            EXTRACTION,
            "--expected-input-hash",
            SHA,
        ],
        capsys,
    )
    assert code == 3 and body["data"]["error"]["code"] == "queue_unavailable"
    assert body["data"]["job_id"] == JOB


@pytest.mark.parametrize(
    "status,error_exit,expected_exit",
    [
        ("failed", 2, 2),
        ("failed", 3, 3),
        ("failed", 4, 4),
        ("cancelled", 2, 4),
    ],
)
def test_score_rubric_job_status_uses_terminal_error_exit(
    monkeypatch, capsys, status, error_exit, expected_exit
):
    monkeypatch.setattr(
        cli,
        "call",
        lambda *args, **kwargs: {
            "ok": False,
            "command": "job status",
            "data": {
                "id": JOB,
                "kind": "score_rubric",
                "status": status,
                "result": {},
                "error": {
                    "code": "synthetic_rubric_failure",
                    "message": "Synthetic rubric failure",
                    "exit_code": error_exit,
                },
            },
            "items": [],
            "warnings": [],
            "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
            "duration_ms": 1,
        },
    )
    code, body = invoke(["job", "status", JOB], capsys)
    assert code == expected_exit and body["ok"] is False


def test_score_rubric_job_status_rejects_malformed_remote_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "call",
        lambda *args, **kwargs: {
            "ok": False,
            "command": "job status",
            "data": {
                "id": JOB,
                "kind": "score_rubric",
                "status": "failed",
                "result": {},
                "error": {
                    "code": "synthetic_rubric_failure",
                    "message": "Synthetic rubric failure",
                    "exit_code": 9,
                },
            },
            "items": [],
            "warnings": [],
            "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
            "duration_ms": 1,
        },
    )
    code, body = invoke(["job", "status", JOB], capsys)
    assert code == 4
    assert body["data"]["error"]["code"] == "invalid_server_response"


def test_score_rubric_generate_wait_returns_partial_exit(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "call",
        lambda *args, **kwargs: _result(data={"job_id": JOB, "status": "queued", "cached": False}),
    )

    async def terminal(job_id, limit_seconds):
        assert str(job_id) == JOB and limit_seconds == 5
        result = RubricGenerateResult(
            rubric_id=RUBRIC,
            job_id=JOB,
            completion="partial",
            version=1,
            scoring_requirements=3,
            candidate_items=2,
            unresolved_requirements=1,
            usage_record_ids=[],
            charge="0.04",
            billing_currency="CNY",
            stop_reason="unresolved_requirements",
        )
        return _result(
            data={"status": "succeeded", "result": result.model_dump(mode="json")},
            warnings=["unresolved_requirements"],
        )

    monkeypatch.setattr(cli, "wait_for_job", terminal)
    code, body = invoke(
        [
            "score",
            "rubric",
            "generate",
            "--task",
            TASK,
            "--extraction-job",
            EXTRACTION,
            "--expected-input-hash",
            SHA,
            "--wait",
            "--timeout",
            "5",
        ],
        capsys,
    )
    assert code == 5 and body["ok"] is False
    assert body["data"]["rubric_id"] == RUBRIC
    assert body["warnings"] == ["unresolved_requirements"]
