"""Memory commands share the authenticated API and approved wire contracts."""

import asyncio
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.memory_contracts import (
    MemoryCandidateJobRequest,
    MemoryCandidateJobResult,
    MemoryCreate,
    MemoryDecision,
    MemoryDelete,
    MemoryDisable,
    MemoryEvalReview,
    MemoryListRequest,
    MemoryRetrievalRequest,
    MemoryUpdate,
)

app = typer.Typer()
retrieval_app = typer.Typer()
feedback_app = typer.Typer()
candidates_app = typer.Typer()
samples_app = typer.Typer()
app.add_typer(retrieval_app, name="retrieval")
app.add_typer(feedback_app, name="feedback")
app.add_typer(candidates_app, name="candidates")
app.add_typer(samples_app, name="samples")
JsonOption = Annotated[
    bool, typer.Option("--json", help="Emit the versioned machine-readable result")
]


def helpers():
    from bid_cli import main

    return main


def page(cursor, limit):
    return {"limit": limit, **({"cursor": cursor} if cursor else {})}


def input_call(command, method, path, input, model, json_output):
    cli = helpers()
    request = cli.input_contract(input, model)
    if command in {"memory approve", "memory reject"} and request["action"] != command.split()[-1]:
        raise ServiceError("invalid_input", "Decision action must match the command", 400, 2)
    cli.emit(cli.call(method, path, json=request), command, json_output)


@app.command("add")
def add(input: Annotated[Path, typer.Option()], json_output: JsonOption = False):
    input_call("memory add", "POST", "/memories", input, MemoryCreate, json_output)


@app.command("list")
def listing(
    scope: Annotated[str, typer.Option()],
    user: Annotated[UUID | None, typer.Option()] = None,
    task: Annotated[UUID | None, typer.Option()] = None,
    status: Annotated[str | None, typer.Option()] = None,
    include_deleted: Annotated[bool, typer.Option()] = False,
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: JsonOption = False,
):
    request = MemoryListRequest.model_validate(
        {
            "target": {"scope": scope, "user_id": user, "task_id": task},
            "status": status,
            "include_deleted": include_deleted,
            "cursor": cursor,
            "limit": limit,
        }
    )
    params = {
        "scope": request.target.scope,
        "include_deleted": request.include_deleted,
        **page(cursor, limit),
    }
    for key, value in (("user_id", user), ("task_id", task), ("status", status)):
        if value is not None:
            params[key] = str(value)
    cli = helpers()
    cli.emit(cli.call("GET", "/memories", params=params), "memory list", json_output)


@app.command("show")
def show(id: Annotated[UUID, typer.Option("--id")], json_output: JsonOption = False):
    cli = helpers()
    cli.emit(cli.call("GET", f"/memories/{id}"), "memory show", json_output)


@app.command("update")
def update(
    id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    input_call("memory update", "PUT", f"/memories/{id}", input, MemoryUpdate, json_output)


@app.command("history")
def history(
    id: Annotated[UUID, typer.Option("--id")],
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: JsonOption = False,
):
    cli = helpers()
    cli.emit(
        cli.call("GET", f"/memories/{id}/history", params=page(cursor, limit)),
        "memory history",
        json_output,
    )


@app.command("approve")
def approve(
    id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    input_call(
        "memory approve", "POST", f"/memories/{id}/decisions", input, MemoryDecision, json_output
    )


@app.command("reject")
def reject(
    id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    input_call(
        "memory reject", "POST", f"/memories/{id}/decisions", input, MemoryDecision, json_output
    )


@app.command("disable")
def disable(
    id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    input_call(
        "memory disable", "POST", f"/memories/{id}/disable", input, MemoryDisable, json_output
    )


@app.command("delete")
def delete(
    id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    input_call("memory delete", "DELETE", f"/memories/{id}", input, MemoryDelete, json_output)


@app.command("retrieve")
def retrieve(
    input: Annotated[Path, typer.Option()],
    dry_run: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    cli = helpers()
    body = cli.input_contract(input, MemoryRetrievalRequest)
    cli.emit(
        cli.call("POST", "/memories/retrieve", params={"preview": dry_run}, json=body),
        "memory retrieve",
        json_output,
    )


@retrieval_app.command("show")
def retrieval_show(id: Annotated[UUID, typer.Option("--id")], json_output: JsonOption = False):
    cli = helpers()
    cli.emit(cli.call("GET", f"/memory-retrievals/{id}"), "memory retrieval show", json_output)


@app.command("used")
def used(job: Annotated[UUID, typer.Option()], json_output: JsonOption = False):
    cli = helpers()
    cli.emit(cli.call("GET", f"/jobs/{job}/memory"), "memory used", json_output)


@feedback_app.command("list")
def feedback_list(
    task: Annotated[UUID, typer.Option()],
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: JsonOption = False,
):
    cli = helpers()
    cli.emit(
        cli.call("GET", f"/tasks/{task}/memory-feedback", params=page(cursor, limit)),
        "memory feedback list",
        json_output,
    )


@candidates_app.command("run")
def candidates_run(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    dry_run: Annotated[bool, typer.Option()] = False,
    retry: Annotated[bool, typer.Option()] = False,
    wait: Annotated[bool, typer.Option()] = False,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: JsonOption = False,
):
    cli = helpers()
    if wait and dry_run:
        raise ServiceError("invalid_input", "Dry runs do not create a job to wait for", 400, 2)
    request = cli.input_contract(input, MemoryCandidateJobRequest)
    request["action"]["dry_run"] = dry_run or request["action"].get("dry_run", False)
    request["action"]["retry"] = retry or request["action"].get("retry", False)
    if wait and request["action"]["dry_run"]:
        raise ServiceError("invalid_input", "Dry runs do not create a job to wait for", 400, 2)
    body = cli.call("POST", f"/tasks/{task}/memory-candidates", json=request)
    if wait:
        try:
            job_id = UUID(body["data"]["job_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceError(
                "invalid_server_response",
                "Server returned invalid candidate job acceptance",
                502,
                4,
            ) from exc
        try:
            terminal = asyncio.run(cli.wait_for_job(job_id, timeout))
            validated = MemoryCandidateJobResult.model_validate(terminal["data"]["result"])
        except ServiceError as exc:
            exc.job_id = str(job_id)
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceError(
                "invalid_server_response", "Server returned invalid candidate job result", 502, 4
            ) from exc
        body["data"] = validated.model_dump(mode="json")
        body["cost"] = terminal.get("cost", body["cost"])
        body["warnings"] = terminal.get("warnings", [])
    cli.emit(body, "memory candidates run", json_output, cli.partial_completion_exit(body))


@samples_app.command("list")
def samples_list(
    task: Annotated[UUID, typer.Option()],
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: JsonOption = False,
):
    cli = helpers()
    cli.emit(
        cli.call("GET", f"/tasks/{task}/memory-evaluations", params=page(cursor, limit)),
        "memory samples list",
        json_output,
    )


@samples_app.command("show")
def samples_show(id: Annotated[UUID, typer.Option("--id")], json_output: JsonOption = False):
    cli = helpers()
    cli.emit(cli.call("GET", f"/memory-evaluations/{id}"), "memory samples show", json_output)


@samples_app.command("review")
def samples_review(
    id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    input_call(
        "memory samples review",
        "POST",
        f"/memory-evaluations/{id}/review",
        input,
        MemoryEvalReview,
        json_output,
    )


def memory_job_exit(body: dict) -> int:
    """Preserve memory job terminal failure codes in shared status commands."""
    data = body.get("data", {})
    if data.get("kind") != "memory_candidate":
        return 0
    if data.get("status") == "cancelled":
        body["ok"] = False
        return 4
    if data.get("status") != "failed":
        return 0
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    exit_code = error.get("exit_code")
    if type(exit_code) is not int or exit_code not in {2, 3, 4}:
        raise ServiceError(
            "invalid_server_response",
            "Server returned invalid candidate job error metadata",
            502,
            4,
        )
    body["ok"] = False
    return exit_code
