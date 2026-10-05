"""Thin human-only agent management commands with the shared Result envelope."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.agent_contracts import (
    AgentCancelRequest,
    AgentListRequest,
    AgentMessageRequest,
    AgentResumeRequest,
    AgentStartRequest,
)

app = typer.Typer()
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the machine-readable Result")]


def helpers():
    from bid_cli import main

    if main.client().contract_version != "4.0":
        raise ServiceError("invalid_input", "Agent commands require contract 4.0", 400, 2)
    return main


def emit(cli, body, command, json_output):
    state = body.get("data", {}).get("session", {}).get("state")
    exit_code = (
        5
        if state == "partial"
        else 3
        if state == "failed" and "agent_retryable_failure" in body.get("warnings", [])
        else 4
        if state == "failed" or (state == "cancelled" and command != "agent cancel")
        else 0
    )
    if exit_code:
        body["ok"] = False
    cli.emit(body, command, json_output, exit_code)


def page(cursor, limit):
    request = AgentListRequest(cursor=cursor, limit=limit)
    return request.model_dump(mode="json", exclude_none=True)


def input_call(action, session_id, source, model, json_output):
    cli = helpers()
    body = cli.input_contract(source, model)
    path = "messages" if action == "message" else action
    emit(
        cli,
        cli.call("POST", f"/agent-sessions/{session_id}/{path}", json=body),
        f"agent {action}",
        json_output,
    )


@app.command("start")
def start(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    dry_run: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    cli = helpers()
    body = cli.input_contract(input, AgentStartRequest)
    body["dry_run"] = dry_run or body["dry_run"]
    emit(
        cli,
        cli.call("POST", f"/tasks/{task}/agent-sessions", json=body),
        "agent start",
        json_output,
    )


@app.command("list")
def listing(
    task: Annotated[UUID, typer.Option()],
    cursor: Annotated[UUID | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: JsonOption = False,
):
    cli = helpers()
    emit(
        cli,
        cli.call("GET", f"/tasks/{task}/agent-sessions", params=page(cursor, limit)),
        "agent list",
        json_output,
    )


@app.command("show")
def show(id: Annotated[UUID, typer.Option("--id")], json_output: JsonOption = False):
    cli = helpers()
    emit(cli, cli.call("GET", f"/agent-sessions/{id}"), "agent show", json_output)


@app.command("messages")
def messages(
    id: Annotated[UUID, typer.Option("--id")],
    cursor: Annotated[UUID | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: JsonOption = False,
):
    cli = helpers()
    emit(
        cli,
        cli.call("GET", f"/agent-sessions/{id}/messages", params=page(cursor, limit)),
        "agent messages",
        json_output,
    )


@app.command("steps")
def steps(
    id: Annotated[UUID, typer.Option("--id")],
    cursor: Annotated[UUID | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: JsonOption = False,
):
    cli = helpers()
    emit(
        cli,
        cli.call("GET", f"/agent-sessions/{id}/steps", params=page(cursor, limit)),
        "agent steps",
        json_output,
    )


@app.command("message")
def message(
    id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    input_call("message", id, input, AgentMessageRequest, json_output)


@app.command("resume")
def resume(
    id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    input_call("resume", id, input, AgentResumeRequest, json_output)


@app.command("cancel")
def cancel(
    id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    input_call("cancel", id, input, AgentCancelRequest, json_output)
