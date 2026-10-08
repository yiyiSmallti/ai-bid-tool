"""Immutable uploaded-bid submissions and explicit local preparation commands."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas import bid_review as models
from app.schemas import bid_review_findings as findings_models
from app.schemas import bid_review_privacy as privacy
from app.schemas import bid_review_report as reports
from app.schemas import bid_review_run as runs
from app.schemas.check_contracts import AssessmentJobAccepted, AssessmentListData
from app.schemas.contracts import Result
from pydantic import TypeAdapter

app = typer.Typer()
submissions = typer.Typer()
outbound = typer.Typer()
report = typer.Typer(invoke_without_command=True)
app.add_typer(submissions, name="submission")
app.add_typer(outbound, name="outbound")
app.add_typer(report, name="report")
JsonOption = Annotated[bool, typer.Option("--json")]
COMMAND_INPUTS = {
    "review upload": models.BidSubmissionCreate,
    "review prepare": models.BidPrepareRequest,
    "review submission list": models.BidReviewListQuery,
    "review submission show": None,
    "review outbound authorize": privacy.OutboundAuthorizationRequest,
    "review outbound revoke": privacy.OutboundRevokeRequest,
    "review outbound list": privacy.BidPrivacyListQuery,
    "review run": runs.BidReviewRequest,
    "review list": models.BidReviewListQuery,
    "review show": None,
    "review findings": findings_models.BidReviewFindingsQuery,
    "review decide": findings_models.BidReviewDecisionRequest,
    "review history": models.BidReviewListQuery,
    "review classify": findings_models.BidReviewClassificationRequest,
    "review report": reports.BidReportRenderRequest,
    "review report download": None,
}
COMMAND_DATA = {
    "review upload": models.BidSubmissionUploaded | models.BidUploadPreview,
    "review prepare": models.BidPreparePreview | AssessmentJobAccepted,
    "review submission list": AssessmentListData,
    "review submission show": models.BidSubmissionDetail,
    "review outbound authorize": privacy.OutboundAuthorizationView,
    "review outbound revoke": privacy.OutboundAuthorizationView,
    "review outbound list": privacy.OutboundAuthorizationListData,
    "review run": runs.BidReviewPreview | AssessmentJobAccepted,
    "review list": runs.BidReviewListData,
    "review show": runs.BidReviewDetail,
    "review findings": findings_models.BidReviewFindingsData,
    "review decide": findings_models.BidReviewEventView,
    "review history": findings_models.BidReviewFindingsData,
    "review classify": findings_models.BidReviewEventView,
    "review report": reports.BidReportRenderPreview | AssessmentJobAccepted,
    "review report download": reports.BidReportDownloadReceipt,
}
COMMAND_ITEMS = {
    "review submission list": models.BidSubmissionUploaded | models.BidSubmissionView,
    "review outbound list": privacy.OutboundAuthorizationView,
    "review list": runs.BidReviewRunView,
    "review findings": findings_models.BidReviewFindingView | findings_models.BidReviewSafeFinding,
    "review history": findings_models.BidReviewEventView,
}


def _helpers():
    from bid_cli import main

    return main


def _input(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            content = handle.read(128 * 1024 + 1)
        if len(content) > 128 * 1024:
            raise ValueError("oversize metadata")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("object required")
        return data
    except (OSError, ValueError, UnicodeError) as exc:
        raise ServiceError("invalid_input", "Cannot read bounded JSON metadata", 400, 2) from exc


def validated(body: dict, command: str) -> dict:
    try:
        value = Result.model_validate(body)
        if value.command != command:
            raise ValueError("unexpected result")
        data = TypeAdapter(COMMAND_DATA[command]).validate_python(value.data)
        if not value.ok and not (command == "review show" and data.run.completion == "partial"):
            raise ValueError("unexpected failure")
        if command in COMMAND_ITEMS:
            if len(value.items) > 100:
                raise ValueError("oversize page")
            for item in value.items:
                TypeAdapter(COMMAND_ITEMS[command]).validate_python(item)
        elif value.items:
            raise ValueError("unexpected items")
    except (ValueError, TypeError, KeyError) as exc:
        raise ServiceError(
            "invalid_server_response", "Server returned invalid submission metadata", 502, 4
        ) from exc
    return body


@app.command("upload")
def upload(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    file: Annotated[list[Path], typer.Option()],
    dry_run: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    cli = _helpers()
    raw = _input(input)
    raw["dry_run"] = dry_run or raw.get("dry_run", False)
    try:
        body = models.BidSubmissionCreate.model_validate(raw)
    except ValueError as exc:
        raise ServiceError("invalid_input", "Invalid submission metadata", 400, 2) from exc
    if len(file) != len(body.files):
        raise ServiceError("invalid_input", "Files must match ordered metadata entries", 400, 2)
    uploads = []
    total = 0
    for path, descriptor in zip(file, body.files, strict=True):
        expected_suffix = ".pdf" if descriptor.media_type == "application/pdf" else ".docx"
        if path.suffix.lower() != expected_suffix:
            raise ServiceError("invalid_document", "File type differs from metadata", 400, 2)
        try:
            with path.open("rb") as handle:
                content = handle.read(descriptor.size_bytes + 1)
        except OSError as exc:
            raise ServiceError("invalid_input", "Cannot read an input file", 400, 2) from exc
        total += len(content)
        if (
            len(content) != descriptor.size_bytes
            or hashlib.sha256(content).hexdigest() != descriptor.sha256
            or total > models.SUBMISSION_BYTE_LIMIT
        ):
            raise ServiceError("invalid_input", "File bytes differ from metadata", 400, 2)
        uploads.append(("files", (path.name, content, descriptor.media_type)))
    result = cli.call(
        "POST",
        f"/tasks/{task}/bid-submissions",
        data={"metadata": body.model_dump_json()},
        files=uploads,
    )
    cli.emit(validated(result, "review upload"), "review upload", json_output)


@app.command("prepare")
def prepare(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    dry_run: Annotated[bool, typer.Option()] = False,
    expected_input_hash: Annotated[str | None, typer.Option()] = None,
    preflight_token: Annotated[str | None, typer.Option()] = None,
    retry: Annotated[bool, typer.Option()] = False,
    wait: Annotated[bool, typer.Option()] = False,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: JsonOption = False,
):
    cli = _helpers()
    raw = _input(input)
    raw["dry_run"] = dry_run or raw.get("dry_run", False)
    if expected_input_hash is not None:
        raw["expected_input_hash"] = expected_input_hash
    if preflight_token is not None:
        raw["preflight_token"] = preflight_token
    raw["retry"] = retry or raw.get("retry", False)
    try:
        body = models.BidPrepareRequest.model_validate(raw)
    except ValueError as exc:
        raise ServiceError(
            "invalid_input", "Invalid preparation metadata or receipt", 400, 2
        ) from exc
    if body.dry_run and wait:
        raise ServiceError("invalid_input", "A preview creates no job to wait for", 400, 2)
    result = validated(
        cli.call(
            "POST",
            f"/tasks/{task}/bid-submissions/{body.submission_id}/prepare",
            json=body.model_dump(mode="json"),
        ),
        "review prepare",
    )
    if wait:
        job_id = AssessmentJobAccepted.model_validate(result["data"]).job_id
        try:
            terminal = asyncio.run(cli.wait_for_job(job_id, timeout))
        except ServiceError as exc:
            exc.job_id = str(job_id)
            raise
        if terminal["data"].get("status") in {"failed", "cancelled"}:
            code = (terminal["data"].get("error") or {}).get("exit_code", 4)
            code = code if type(code) is int and code in {2, 3, 4} else 4
            cli.emit(terminal, "review prepare", json_output, code)
        result = validated(
            cli.call("GET", f"/bid-submissions/{body.submission_id}"), "review submission show"
        )
        try:
            result["data"] = models.BidSubmissionView.model_validate(
                result["data"]["submission"]
            ).model_dump(mode="json")
        except (KeyError, ValueError) as exc:
            raise ServiceError(
                "invalid_response", "Completed preparation has no fixed inventory", 502, 4
            ) from exc
        result["cost"] = terminal["cost"]
    cli.emit(result, "review prepare", json_output)


@submissions.command("list")
def list_submissions(
    task: Annotated[UUID, typer.Option()],
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: JsonOption = False,
):
    cli = _helpers()
    params = models.BidReviewListQuery(cursor=cursor, limit=limit).model_dump(exclude_none=True)
    body = cli.call("GET", f"/tasks/{task}/bid-submissions", params=params)
    cli.emit(validated(body, "review submission list"), "review submission list", json_output)


@submissions.command("show")
def show(
    id: Annotated[UUID, typer.Option("--id")],
    json_output: JsonOption = False,
):
    cli = _helpers()
    body = cli.call("GET", f"/bid-submissions/{id}")
    cli.emit(validated(body, "review submission show"), "review submission show", json_output)


def _outbound_mutation(submission: UUID, input: Path, revoke: bool, json_output: bool):
    cli = _helpers()
    model = privacy.OutboundRevokeRequest if revoke else privacy.OutboundAuthorizationRequest
    try:
        body = model.model_validate(_input(input))
    except ValueError as exc:
        raise ServiceError("invalid_input", "Invalid exact outbound authorization", 400, 2) from exc
    command = "review outbound revoke" if revoke else "review outbound authorize"
    path = f"/bid-submissions/{submission}/outbound-authorizations"
    if revoke:
        path += "/revoke"
    result = cli.call("POST", path, json=body.model_dump(mode="json"))
    cli.emit(validated(result, command), command, json_output)


@outbound.command("authorize")
def authorize(
    submission: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    _outbound_mutation(submission, input, False, json_output)


@outbound.command("revoke")
def revoke(
    submission: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    _outbound_mutation(submission, input, True, json_output)


@outbound.command("list")
def list_authorizations(
    submission: Annotated[UUID, typer.Option()],
    cursor: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: JsonOption = False,
):
    cli = _helpers()
    params = privacy.BidPrivacyListQuery(cursor=cursor, limit=limit).model_dump(exclude_none=True)
    result = cli.call(
        "GET", f"/bid-submissions/{submission}/outbound-authorizations", params=params
    )
    cli.emit(validated(result, "review outbound list"), "review outbound list", json_output)


@app.command("run")
def run_review(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    dry_run: Annotated[bool, typer.Option()] = False,
    expected_input_hash: Annotated[str | None, typer.Option()] = None,
    preflight_token: Annotated[str | None, typer.Option()] = None,
    retry: Annotated[bool, typer.Option()] = False,
    wait: Annotated[bool, typer.Option()] = False,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: JsonOption = False,
):
    cli = _helpers()
    raw = _input(input)
    raw["dry_run"] = dry_run or raw.get("dry_run", False)
    raw["retry"] = retry or raw.get("retry", False)
    if expected_input_hash is not None:
        raw["expected_input_hash"] = expected_input_hash
    if preflight_token is not None:
        raw["preflight_token"] = preflight_token
    try:
        body = runs.BidReviewRequest.model_validate(raw)
    except ValueError as exc:
        raise ServiceError("invalid_input", "Invalid review metadata or receipt", 400, 2) from exc
    if body.dry_run and wait:
        raise ServiceError("invalid_input", "A preview creates no job to wait for", 400, 2)
    result = validated(
        cli.call("POST", f"/tasks/{task}/bid-reviews", json=body.model_dump(mode="json")),
        "review run",
    )
    if wait:
        job_id = AssessmentJobAccepted.model_validate(result["data"]).job_id
        try:
            terminal = asyncio.run(cli.wait_for_job(job_id, timeout))
            if terminal["data"].get("status") in {"failed", "cancelled"}:
                cli.emit(
                    cli.merge_job_result(result, terminal),
                    "review run",
                    json_output,
                    cli.partial_completion_exit(terminal),
                )
            output = terminal["data"].get("result")
            if not isinstance(output, dict):
                raise ValueError("missing review job result")
            result["data"] = runs.BidReviewJobResult.model_validate(
                {key: value for key, value in output.items() if key not in {"submission", "budget"}}
            ).model_dump(mode="json")
            result["warnings"] = terminal.get("warnings", [])
            result["cost"] = terminal.get("cost", result["cost"])
        except ServiceError as exc:
            exc.job_id = str(job_id)
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceError(
                "invalid_server_response",
                "Invalid completed review result",
                502,
                4,
                job_id=str(job_id),
            ) from exc
    cli.emit(result, "review run", json_output, cli.partial_completion_exit(result))


@app.command("list")
def list_reviews(
    task: Annotated[UUID, typer.Option()],
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: JsonOption = False,
):
    cli = _helpers()
    params = models.BidReviewListQuery(cursor=cursor, limit=limit).model_dump(exclude_none=True)
    result = cli.call("GET", f"/tasks/{task}/bid-reviews", params=params)
    cli.emit(validated(result, "review list"), "review list", json_output)


@app.command("show")
def show_review(
    id: Annotated[UUID, typer.Option("--id")],
    section: Annotated[str, typer.Option()] = "obligations",
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: JsonOption = False,
):
    if section not in {"obligations", "signing_requirements"}:
        raise ServiceError("invalid_input", "Unknown review section", 400, 2)
    cli = _helpers()
    params = models.BidReviewListQuery(cursor=cursor, limit=limit).model_dump(exclude_none=True)
    params["section"] = section
    result = cli.call("GET", f"/bid-reviews/{id}", params=params)
    result = validated(result, "review show")
    partial = result["data"]["run"]["completion"] == "partial"
    if partial:
        result["ok"] = False
    cli.emit(result, "review show", json_output, 5 if partial else 0)


@app.command("findings")
def list_findings(
    id: Annotated[UUID, typer.Option("--id")],
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    severity: Annotated[str | None, typer.Option()] = None,
    state: Annotated[str | None, typer.Option()] = None,
    outcome: Annotated[str | None, typer.Option()] = None,
    json_output: JsonOption = False,
):
    cli = _helpers()
    try:
        params = findings_models.BidReviewFindingsQuery.model_validate(
            {
                "cursor": cursor,
                "limit": limit,
                "severity": severity,
                "state": state,
                "outcome": outcome,
            }
        ).model_dump(exclude_none=True)
    except ValueError as exc:
        raise ServiceError("invalid_input", "Invalid finding filters", 400, 2) from exc
    result = cli.call("GET", f"/bid-reviews/{id}/findings", params=params)
    cli.emit(_finding_result(result, "review findings", id), "review findings", json_output)


def _finding_result(result: dict, command: str, review: UUID, finding: UUID | None = None):
    validated(result, command)
    data = result["data"]
    rows = result["items"] if command in {"review findings", "review history"} else [data]
    if (
        str(data["review_id"]) != str(review)
        or any(
            str(row["review_id"]) != str(review)
            or (finding is not None and str(row["finding_id"]) != str(finding))
            for row in rows
        )
        or len({row["id"] for row in rows}) != len(rows)
    ):
        raise ServiceError(
            "invalid_server_response", "Server returned a different finding parent", 502, 4
        )
    return result


def _finding_mutation(review: UUID, finding: UUID, input: Path, classify: bool, json_output: bool):
    cli = _helpers()
    model = (
        findings_models.BidReviewClassificationRequest
        if classify
        else findings_models.BidReviewDecisionRequest
    )
    try:
        body = model.model_validate(_input(input))
    except ValueError as exc:
        raise ServiceError("invalid_input", "Invalid finding action or revision", 400, 2) from exc
    command = "review classify" if classify else "review decide"
    result = cli.call(
        "POST",
        f"/bid-reviews/{review}/findings/{finding}/{'classification' if classify else 'decisions'}",
        json=body.model_dump(mode="json"),
    )
    cli.emit(_finding_result(result, command, review, finding), command, json_output)


@app.command("decide")
def decide_finding(
    id: Annotated[UUID, typer.Option("--id")],
    finding: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    _finding_mutation(id, finding, input, False, json_output)


@app.command("classify")
def classify_finding(
    id: Annotated[UUID, typer.Option("--id")],
    finding: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    _finding_mutation(id, finding, input, True, json_output)


@app.command("history")
def finding_history(
    id: Annotated[UUID, typer.Option("--id")],
    finding: Annotated[UUID, typer.Option()],
    classification: Annotated[bool, typer.Option()] = False,
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: JsonOption = False,
):
    cli = _helpers()
    params = models.BidReviewListQuery(cursor=cursor, limit=limit).model_dump(exclude_none=True)
    result = cli.call(
        "GET",
        f"/bid-reviews/{id}/findings/{finding}/{'classification' if classification else 'decisions'}",
        params=params,
    )
    cli.emit(_finding_result(result, "review history", id, finding), "review history", json_output)


@report.callback()
def render_report(
    ctx: typer.Context,
    id: Annotated[UUID | None, typer.Option("--id")] = None,
    input: Annotated[Path | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option()] = False,
    expected_input_hash: Annotated[str | None, typer.Option()] = None,
    preflight_token: Annotated[str | None, typer.Option()] = None,
    retry: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    """Preview or explicitly enqueue an immutable console and Word report."""
    if ctx.invoked_subcommand is not None:
        return
    if id is None or input is None:
        raise ServiceError("invalid_input", "Report requires --id and --input", 400, 2)
    cli = _helpers()
    raw = _input(input)
    raw["dry_run"] = dry_run or raw.get("dry_run", False)
    raw["retry"] = retry or raw.get("retry", False)
    if expected_input_hash is not None:
        raw["expected_input_hash"] = expected_input_hash
    if preflight_token is not None:
        raw["preflight_token"] = preflight_token
    try:
        body = reports.BidReportRenderRequest.model_validate(raw)
        if body.report_id != id:
            raise ValueError("report parent differs from --id")
    except ValueError as exc:
        raise ServiceError("invalid_input", "Invalid report metadata or receipt", 400, 2) from exc
    result = validated(
        cli.call("POST", f"/bid-reviews/{id}/artifacts", json=body.model_dump(mode="json")),
        "review report",
    )
    try:
        if body.dry_run:
            preview = reports.BidReportRenderPreview.model_validate(result["data"])
            if (
                preview.report_id != id
                or preview.decisions_snapshot_sha256 != body.expected_decisions_snapshot_sha256
            ):
                raise ValueError("report snapshot mismatch")
        else:
            AssessmentJobAccepted.model_validate(result["data"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceError(
            "invalid_server_response",
            "Server returned a different report snapshot or result",
            502,
            4,
        ) from exc
    cli.emit(result, "review report", json_output)


@report.command("download")
def download_report(
    artifact: Annotated[UUID, typer.Option()],
    output: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    from bid_cli.bid_review_report_client import download_report_artifact

    cli = _helpers()
    result = asyncio.run(download_report_artifact(cli.client(), artifact, output))
    cli.emit(validated(result, "review report download"), "review report download", json_output)
