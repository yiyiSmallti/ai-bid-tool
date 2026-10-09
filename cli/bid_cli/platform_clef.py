"""Noninteractive, secret-free platform Clef settings and gateway hardening check."""

from pathlib import Path
from typing import Annotated

import typer
from app.core.errors import ServiceError
from app.schemas.clef import ClefCheckRequest, ClefSettingsData, ClefSettingsSet
from app.schemas.contracts import Result

app = typer.Typer()
JsonOption = Annotated[bool, typer.Option("--json")]
COMMAND_INPUTS = {
    "platform clef show": None,
    "platform clef set": ClefSettingsSet,
    "platform clef check": ClefCheckRequest,
}
COMMAND_DATA = {command: ClefSettingsData for command in COMMAND_INPUTS}
ERRORS = {
    "invalid_input": ("Invalid Clef configuration input", 2),
    "invalid_session": ("Platform login required", 4),
    "forbidden": ("Platform operator authorization required", 4),
    "revision_conflict": ("Clef settings changed; refresh before retrying", 2),
    "credential_reference_mismatch": ("Clef credentials do not match their purposes", 2),
    "clef_configuration_unavailable": ("Clef configuration is unavailable", 3),
    "credential_backend_unavailable": ("Platform credential backend is unavailable", 3),
}


def safe_result(body: dict, command: str) -> dict:
    try:
        value = Result.model_validate(body)
        if value.command != command or value.items:
            raise ValueError("Unexpected Clef result")
        if value.ok:
            value.data = ClefSettingsData.model_validate(value.data).model_dump(mode="json")
        else:
            code = value.data.get("error", {}).get("code")
            message, exit_code = ERRORS.get(code, ("Platform Clef operation failed", 4))
            value.data = {
                "error": {
                    "code": code if code in ERRORS else "clef_failed",
                    "message": message,
                    "exit_code": exit_code,
                }
            }
        value.warnings = []
        return value.model_dump(mode="json")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ServiceError(
            "invalid_server_response", "Server returned invalid Clef metadata", 502, 4
        ) from None


def perform(action: str, method: str, path: str, json_output: bool, **kwargs):
    from bid_cli.main import call, emit

    command = "platform clef " + action
    body = safe_result(call(method, "/platform/clef" + path, platform=True, **kwargs), command)
    emit(body, command, json_output, 0 if body["ok"] else body["data"]["error"]["exit_code"])


@app.command("show")
def show(json_output: JsonOption = False):
    perform("show", "GET", "", json_output)


@app.command("set")
def set_settings(input: Annotated[Path, typer.Option()], json_output: JsonOption = False):
    from bid_cli.main import input_contract

    body = input_contract(input, ClefSettingsSet)
    perform("set", "PUT", "", json_output, json=body)


@app.command("check")
def check(expected_revision: Annotated[int, typer.Option(min=1)], json_output: JsonOption = False):
    body = ClefCheckRequest(expected_revision=expected_revision)
    perform("check", "POST", "/check", json_output, json=body.model_dump(mode="json"))
