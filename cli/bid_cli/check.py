"""Confirmed-draft rules check commands."""

import asyncio
from datetime import date
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.check_contracts import CheckJobResult, CheckRequest, FindingDecisionRequest

app = typer.Typer()
JsonOption = Annotated[
    bool, typer.Option("--json", help="Emit the versioned machine-readable result")
]


def _helpers():
    from bid_cli import main

    return main


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


def _page(cursor: str | None, limit: int) -> dict:
    params: dict = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    return params


def _report_exit(body: dict) -> int:
    report = body.get("data", {}).get("report", {})
    if report.get("completion") == "partial":
        body["ok"] = False
        return 5
    return 0


def check_job_exit(body: dict) -> int:
    """Map terminal check-job failures without changing other job kinds."""
    data = body.get("data", {})
    if data.get("kind") != "check":
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
            "Server returned invalid check job error metadata",
            502,
            4,
        )
    return exit_code


@app.command("run")
def check_run(
    task: Annotated[UUID, typer.Option()],
    draft: Annotated[UUID, typer.Option()],
    as_of: Annotated[str, typer.Option("--as-of")],
    mode: Annotated[str, typer.Option()] = "rules",
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
    if mode not in {"rules", "combined"}:
        raise ServiceError("invalid_input", "Mode must be rules or combined", 400, 2)
    if wait and dry_run:
        raise ServiceError("invalid_input", "A dry run does not create a job to wait for", 400, 2)
    request = CheckRequest(
        draft_id=draft,
        assessment_date=_date(as_of),
        mode=mode,  # pyright: ignore[reportArgumentType]
        reasoning=reasoning,
        dry_run=dry_run,
        retry=retry,
        expected_input_hash=expected_input_hash,
        max_charge=max_charge,  # pyright: ignore[reportArgumentType]
    )
    body = cli.call("POST", f"/tasks/{task}/checks", json=request.model_dump(mode="json"))
    if wait:
        try:
            job_id = UUID(body.get("data", {}).get("job_id", ""))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ServiceError(
                "invalid_server_response",
                "Server returned an invalid check job acceptance",
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
                    "Server returned an invalid check job status",
                    502,
                    4,
                ) from exc
            if not output.get("report_id"):
                raise ServiceError(
                    "invalid_server_response",
                    "Completed check job did not identify its report",
                    502,
                    4,
                )
            try:
                validated = CheckJobResult.model_validate(
                    {key: value for key, value in output.items() if key != "budget"}
                )
            except (TypeError, ValueError) as exc:
                raise ServiceError(
                    "invalid_server_response",
                    "Server returned an invalid check job result",
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
    cli.emit(body, "check run", json_output, cli.partial_completion_exit(body))


@app.command("list")
def check_list(
    task: Annotated[UUID, typer.Option()],
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=200)] = 50,
    json_output: JsonOption = False,
):
    cli = _helpers()
    cli.emit(
        cli.call("GET", f"/tasks/{task}/checks", params=_page(cursor, limit)),
        "check list",
        json_output,
    )


@app.command("show")
def check_show(
    id: Annotated[UUID, typer.Option("--id")],
    json_output: JsonOption = False,
):
    cli = _helpers()
    body = cli.call("GET", f"/checks/{id}")
    cli.emit(body, "check show", json_output, _report_exit(body))


@app.command("decide")
def check_decide(
    report: Annotated[UUID, typer.Option()],
    finding: Annotated[UUID, typer.Option()],
    action: Annotated[str, typer.Option()],
    expected_revision: Annotated[int, typer.Option(min=1)],
    expected_input_hash: Annotated[str, typer.Option()],
    reason: Annotated[str, typer.Option()],
    json_output: JsonOption = False,
):
    cli = _helpers()
    request = FindingDecisionRequest(
        expected_revision=expected_revision,
        expected_input_hash=expected_input_hash,
        action=action,  # pyright: ignore[reportArgumentType]
        reason=reason,
    )
    cli.emit(
        cli.call(
            "POST",
            f"/checks/{report}/findings/{finding}/decisions",
            json=request.model_dump(mode="json"),
        ),
        "check decide",
        json_output,
    )


@app.command("history")
def check_history(
    report: Annotated[UUID, typer.Option()],
    finding: Annotated[UUID, typer.Option()],
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=200)] = 50,
    json_output: JsonOption = False,
):
    cli = _helpers()
    cli.emit(
        cli.call(
            "GET",
            f"/checks/{report}/findings/{finding}/decisions",
            params=_page(cursor, limit),
        ),
        "check history",
        json_output,
    )
