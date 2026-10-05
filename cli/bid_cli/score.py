"""Score execution and human-reviewed rubric commands."""

import asyncio
from datetime import date
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.console_assessments import (
    AssessmentHistoryPage,
    RubricPage,
    RubricPageRequest,
    RubricReplacementData,
    RubricSummaryData,
    ScorePage,
    ScorePageRequest,
    ScoreSummaryData,
)
from app.schemas.score_contracts import (
    RubricClassifyRequest,
    RubricCoverageDecisionRequest,
    RubricGenerateRequest,
    RubricGenerateResult,
    RubricItemDecisionRequest,
    RubricReviseRequest,
    RubricSectionDecisionRequest,
    RubricSetDecisionRequest,
    ScoreJobResult,
    ScoreRequest,
)

from bid_cli.assessments import (
    history_params,
    projection_exit,
    require_console,
    show_params,
    validated,
)

app = typer.Typer()
rubric_app = typer.Typer()
section_app = typer.Typer()
item_app = typer.Typer()
coverage_app = typer.Typer()
app.add_typer(rubric_app, name="rubric")
rubric_app.add_typer(section_app, name="section")
rubric_app.add_typer(item_app, name="item")
rubric_app.add_typer(coverage_app, name="coverage")

JsonOption = Annotated[
    bool, typer.Option("--json", help="Emit the versioned machine-readable result")
]


def _helpers():
    from bid_cli import main

    return main


def _page(cursor: str | None, limit: int) -> dict:
    params: dict = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    return params


def _date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError as exc:
        raise ServiceError(
            "invalid_date", "Use --as-of YYYY-MM-DD with a valid date", 400, 2
        ) from exc


def _job_id(body: dict) -> UUID:
    try:
        return UUID(body.get("data", {}).get("job_id", ""))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ServiceError(
            "invalid_server_response",
            "Server returned an invalid rubric job acceptance",
            502,
            4,
        ) from exc


def rubric_job_exit(body: dict) -> int:
    """Map terminal rubric-job failures without changing other job kinds."""
    data = body.get("data", {})
    if data.get("kind") != "score_rubric":
        return 0
    status = data.get("status")
    if status == "cancelled":
        body["ok"] = False
        return 4
    if status != "failed":
        return 0
    body["ok"] = False
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    exit_code = error.get("exit_code")
    if type(exit_code) is not int or exit_code not in {2, 3, 4}:
        raise ServiceError(
            "invalid_server_response",
            "Server returned invalid rubric job error metadata",
            502,
            4,
        )
    return exit_code


def score_job_exit(body: dict) -> int:
    """Map terminal score-job failures without changing other job kinds."""
    data = body.get("data", {})
    if data.get("kind") != "score":
        return 0
    status = data.get("status")
    if status == "cancelled":
        body["ok"] = False
        return 4
    if status != "failed":
        return 0
    body["ok"] = False
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    exit_code = error.get("exit_code")
    if type(exit_code) is not int or exit_code not in {2, 3, 4}:
        raise ServiceError(
            "invalid_server_response",
            "Server returned invalid score job error metadata",
            502,
            4,
        )
    return exit_code


def _score_report_exit(body: dict) -> int:
    report = body.get("data", {}).get("report", {})
    if (
        report.get("completion") == "partial"
        or report.get("unassessable_items", 0) > 0
        or report.get("total_status") != "estimated"
    ):
        body["ok"] = False
        return 5
    return 0


@app.command("run")
def score_run(
    task: Annotated[UUID, typer.Option()],
    draft: Annotated[UUID, typer.Option()],
    rubric: Annotated[UUID, typer.Option()],
    as_of: Annotated[str, typer.Option("--as-of")],
    reasoning: Annotated[str | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option()] = False,
    expected_input_hash: Annotated[str | None, typer.Option()] = None,
    max_charge: Annotated[str | None, typer.Option()] = None,
    retry: Annotated[bool, typer.Option()] = False,
    wait: Annotated[bool, typer.Option()] = False,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: JsonOption = False,
):
    cli = _helpers()
    if wait and dry_run:
        raise ServiceError("invalid_input", "A dry run does not create a job to wait for", 400, 2)
    request = ScoreRequest(
        draft_id=draft,
        rubric_id=rubric,
        assessment_date=_date(as_of),
        reasoning=reasoning,
        dry_run=dry_run,
        retry=retry,
        expected_input_hash=expected_input_hash,
        max_charge=max_charge,  # pyright: ignore[reportArgumentType]
    )
    path = f"/tasks/{task}/scores/preview" if dry_run else f"/tasks/{task}/scores"
    body = cli.call("POST", path, json=request.model_dump(mode="json"))
    if wait:
        try:
            job_id = UUID(body.get("data", {}).get("job_id", ""))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ServiceError(
                "invalid_server_response",
                "Server returned an invalid score job acceptance",
                502,
                4,
            ) from exc
        try:
            terminal = asyncio.run(cli.wait_for_job(job_id, timeout))
            if terminal["data"].get("status") in {"failed", "cancelled"}:
                cli.emit(
                    cli.merge_job_result(body, terminal),
                    body["command"],
                    json_output,
                    cli.partial_completion_exit(terminal),
                )
            try:
                output = terminal["data"].get("result") or {}
            except (AttributeError, KeyError, TypeError) as exc:
                raise ServiceError(
                    "invalid_server_response",
                    "Server returned an invalid score job status",
                    502,
                    4,
                ) from exc
            try:
                validated = ScoreJobResult.model_validate(
                    {key: value for key, value in output.items() if key != "budget"}
                )
            except (TypeError, ValueError) as exc:
                raise ServiceError(
                    "invalid_server_response",
                    "Server returned an invalid score job result",
                    502,
                    4,
                ) from exc
            body["data"] = validated.model_dump(mode="json")
            if "budget" in output:
                body["data"]["budget"] = output["budget"]
            body["warnings"] = terminal.get("warnings", [])
            body["cost"] = terminal.get("cost", body["cost"])
        except ServiceError as exc:
            if exc.job_id is None:
                exc.job_id = str(job_id)
            raise
    cli.emit(body, "score run", json_output, cli.partial_completion_exit(body))


@app.command("list")
def score_list(
    task: Annotated[UUID, typer.Option()],
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=200)] = 50,
    view: Annotated[str | None, typer.Option()] = None,
    extraction_job: Annotated[UUID | None, typer.Option("--extraction-job")] = None,
    json_output: JsonOption = False,
):
    cli = _helpers()
    params = history_params(view, extraction_job, cursor, limit)
    body = cli.call("GET", f"/tasks/{task}/scores", params=params)
    if view is not None:
        validated(body, "score list", page_model=AssessmentHistoryPage[ScoreSummaryData])
    cli.emit(body, "score list", json_output, projection_exit(body) if view else 0)


@app.command("show")
def score_show(
    task: Annotated[UUID, typer.Option()],
    report: Annotated[UUID, typer.Option()],
    view: Annotated[str | None, typer.Option()] = None,
    part: Annotated[str | None, typer.Option()] = None,
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int | None, typer.Option(min=1, max=100)] = None,
    section_key: Annotated[str | None, typer.Option("--section-key")] = None,
    outcome: Annotated[str | None, typer.Option()] = None,
    requirement: Annotated[UUID | None, typer.Option("--requirement")] = None,
    entry: Annotated[UUID | None, typer.Option("--entry")] = None,
    json_output: JsonOption = False,
):
    cli = _helpers()
    params = show_params(
        view,
        part,
        ScorePageRequest,
        cursor=cursor,
        limit=limit,
        section_key=section_key,
        outcome=outcome,
        requirement_id=requirement,
        entry_id=entry,
    )
    if params is None:
        body = cli.call("GET", f"/tasks/{task}/scores/{report}")
        exit_code = _score_report_exit(body)
    else:
        if params["part"] == "replacement":
            raise ServiceError(
                "invalid_input", "Score reports do not have a replacement projection", 400, 2
            )
        body = cli.call("GET", f"/tasks/{task}/scores/{report}", params=params)
        validated(
            body,
            "score show",
            data_model=ScoreSummaryData if params["part"] == "summary" else None,
            page_model=ScorePage if params["part"] != "summary" else None,
        )
        exit_code = projection_exit(body)
    cli.emit(body, "score show", json_output, exit_code)


@rubric_app.command("generate")
def rubric_generate(
    task: Annotated[UUID, typer.Option()],
    extraction_job: Annotated[UUID, typer.Option("--extraction-job")],
    reasoning: Annotated[str | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option()] = False,
    expected_input_hash: Annotated[str | None, typer.Option()] = None,
    max_charge: Annotated[str | None, typer.Option()] = None,
    retry: Annotated[bool, typer.Option()] = False,
    wait: Annotated[bool, typer.Option()] = False,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: JsonOption = False,
):
    cli = _helpers()
    if wait and dry_run:
        raise ServiceError("invalid_input", "A dry run does not create a job to wait for", 400, 2)
    request = RubricGenerateRequest(
        extraction_job_id=extraction_job,
        reasoning=reasoning,
        dry_run=dry_run,
        retry=retry,
        expected_input_hash=expected_input_hash,
        max_charge=max_charge,  # pyright: ignore[reportArgumentType]
    )
    path = f"/tasks/{task}/score-rubrics/preview" if dry_run else f"/tasks/{task}/score-rubrics"
    body = cli.call("POST", path, json=request.model_dump(mode="json"))
    if wait:
        job_id = _job_id(body)
        try:
            terminal = asyncio.run(cli.wait_for_job(job_id, timeout))
            if terminal["data"].get("status") in {"failed", "cancelled"}:
                cli.emit(
                    cli.merge_job_result(body, terminal),
                    body["command"],
                    json_output,
                    cli.partial_completion_exit(terminal),
                )
            try:
                output = terminal["data"].get("result") or {}
            except (AttributeError, KeyError, TypeError) as exc:
                raise ServiceError(
                    "invalid_server_response",
                    "Server returned an invalid rubric job status",
                    502,
                    4,
                ) from exc
            try:
                validated = RubricGenerateResult.model_validate(
                    {key: value for key, value in output.items() if key != "budget"}
                )
            except (TypeError, ValueError) as exc:
                raise ServiceError(
                    "invalid_server_response",
                    "Server returned an invalid rubric job result",
                    502,
                    4,
                ) from exc
            body["data"] = validated.model_dump(mode="json")
            if "budget" in output:
                body["data"]["budget"] = output["budget"]
            body["warnings"] = terminal.get("warnings", [])
            body["cost"] = terminal.get("cost", body["cost"])
        except ServiceError as exc:
            if exc.job_id is None:
                exc.job_id = str(job_id)
            raise
    cli.emit(body, "score rubric generate", json_output, cli.partial_completion_exit(body))


@rubric_app.command("list")
def rubric_list(
    task: Annotated[UUID, typer.Option()],
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=200)] = 50,
    view: Annotated[str | None, typer.Option()] = None,
    extraction_job: Annotated[UUID | None, typer.Option("--extraction-job")] = None,
    json_output: JsonOption = False,
):
    cli = _helpers()
    params = history_params(view, extraction_job, cursor, limit)
    body = cli.call("GET", f"/tasks/{task}/score-rubrics", params=params)
    if view is not None:
        validated(body, "score rubric list", page_model=AssessmentHistoryPage[RubricSummaryData])
    cli.emit(body, "score rubric list", json_output, projection_exit(body) if view else 0)


@rubric_app.command("show")
def rubric_show(
    task: Annotated[UUID, typer.Option()],
    rubric: Annotated[UUID, typer.Option()],
    view: Annotated[str | None, typer.Option()] = None,
    part: Annotated[str | None, typer.Option()] = None,
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int | None, typer.Option(min=1, max=100)] = None,
    state: Annotated[str | None, typer.Option()] = None,
    domain: Annotated[str | None, typer.Option()] = None,
    section: Annotated[UUID | None, typer.Option()] = None,
    requirement: Annotated[UUID | None, typer.Option("--requirement")] = None,
    entry: Annotated[UUID | None, typer.Option("--entry")] = None,
    group: Annotated[str | None, typer.Option()] = None,
    json_output: JsonOption = False,
):
    cli = _helpers()
    params = show_params(
        view,
        part,
        RubricPageRequest,
        cursor=cursor,
        limit=limit,
        state=state,
        domain=domain,
        section_id=section,
        requirement_id=requirement,
        entry_id=entry,
        group_id=group,
    )
    if params is None:
        body = cli.call("GET", f"/tasks/{task}/score-rubrics/{rubric}")
    else:
        body = cli.call("GET", f"/tasks/{task}/score-rubrics/{rubric}", params=params)
        single = {"summary": RubricSummaryData, "replacement": RubricReplacementData}.get(
            params["part"]
        )
        validated(
            body,
            "score rubric show",
            data_model=single,
            page_model=RubricPage if single is None else None,
        )
    cli.emit(body, "score rubric show", json_output, projection_exit(body) if view else 0)


@rubric_app.command("revise")
def rubric_revise(
    task: Annotated[UUID, typer.Option()],
    rubric: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    view: Annotated[str | None, typer.Option()] = None,
    json_output: JsonOption = False,
):
    cli = _helpers()
    console = require_console(view)
    request = cli.input_contract(input, RubricReviseRequest, max_bytes=512 * 1024)
    body = cli.call(
        "POST",
        f"/tasks/{task}/score-rubrics/{rubric}/revisions",
        json=request,
        **({"params": {"view": "console"}} if console else {}),
    )
    if console:
        validated(body, "score rubric revise", data_model=RubricSummaryData)
    cli.emit(body, "score rubric revise", json_output, projection_exit(body) if console else 0)


@rubric_app.command("classify")
def rubric_classify(
    task: Annotated[UUID, typer.Option()],
    rubric: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    section: Annotated[UUID | None, typer.Option()] = None,
    item: Annotated[UUID | None, typer.Option()] = None,
    json_output: JsonOption = False,
):
    cli = _helpers()
    if (section is None) == (item is None):
        raise ServiceError(
            "invalid_input",
            "Use exactly one of --section or --item",
            400,
            2,
        )
    request = cli.input_contract(input, RubricClassifyRequest)
    kind, identifier = ("sections", section) if section is not None else ("items", item)
    cli.emit(
        cli.call(
            "POST",
            f"/tasks/{task}/score-rubrics/{rubric}/{kind}/{identifier}/classification",
            json=request,
        ),
        "score rubric classify",
        json_output,
    )


def _decide(
    *,
    command: str,
    path: str,
    input: Path,
    model,
    json_output: bool,
    view: str | None = None,
):
    cli = _helpers()
    console = require_console(view)
    request = cli.input_contract(input, model)
    body = cli.call(
        "POST", path, json=request, **({"params": {"view": "console"}} if console else {})
    )
    if console:
        validated(body, command, data_model=RubricSummaryData)
    cli.emit(body, command, json_output, projection_exit(body) if console else 0)


@section_app.command("decide")
def rubric_section_decide(
    task: Annotated[UUID, typer.Option()],
    rubric: Annotated[UUID, typer.Option()],
    section: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    _decide(
        command="score rubric section decide",
        path=f"/tasks/{task}/score-rubrics/{rubric}/sections/{section}/decisions",
        input=input,
        model=RubricSectionDecisionRequest,
        json_output=json_output,
    )


@item_app.command("decide")
def rubric_item_decide(
    task: Annotated[UUID, typer.Option()],
    rubric: Annotated[UUID, typer.Option()],
    item: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    _decide(
        command="score rubric item decide",
        path=f"/tasks/{task}/score-rubrics/{rubric}/items/{item}/decisions",
        input=input,
        model=RubricItemDecisionRequest,
        json_output=json_output,
    )


@coverage_app.command("decide")
def rubric_coverage_decide(
    task: Annotated[UUID, typer.Option()],
    rubric: Annotated[UUID, typer.Option()],
    requirement: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    _decide(
        command="score rubric coverage decide",
        path=(f"/tasks/{task}/score-rubrics/{rubric}/coverage/{requirement}/decisions"),
        input=input,
        model=RubricCoverageDecisionRequest,
        json_output=json_output,
    )


@rubric_app.command("decide")
def rubric_decide(
    task: Annotated[UUID, typer.Option()],
    rubric: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    view: Annotated[str | None, typer.Option()] = None,
    json_output: JsonOption = False,
):
    _decide(
        command="score rubric decide",
        path=f"/tasks/{task}/score-rubrics/{rubric}/decisions",
        input=input,
        model=RubricSetDecisionRequest,
        json_output=json_output,
        view=view,
    )


@rubric_app.command("history")
def rubric_history(
    task: Annotated[UUID, typer.Option()],
    rubric: Annotated[UUID, typer.Option()],
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=200)] = 50,
    json_output: JsonOption = False,
):
    cli = _helpers()
    cli.emit(
        cli.call(
            "GET",
            f"/tasks/{task}/score-rubrics/{rubric}/history",
            params=_page(cursor, limit),
        ),
        "score rubric history",
        json_output,
    )
