"""Platform trust-store maintenance using bounded certificate files."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.bid_signature import TrustAnchorAddResult, TrustAnchorView
from app.schemas.contracts import Result
from pydantic import BaseModel, ConfigDict, Field

app = typer.Typer()
JsonOption = Annotated[bool, typer.Option("--json")]


class TrustAnchorAddInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=100)
    certificate: str


class TrustAnchorListData(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrustAnchorDisableInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID


COMMAND_INPUTS = {
    "platform trust-anchor list": None,
    "platform trust-anchor add": TrustAnchorAddInput,
    "platform trust-anchor disable": TrustAnchorDisableInput,
}
COMMAND_DATA = {
    "platform trust-anchor list": TrustAnchorListData,
    "platform trust-anchor add": TrustAnchorAddResult,
    "platform trust-anchor disable": TrustAnchorView,
}
COMMAND_ITEMS = {"platform trust-anchor list": TrustAnchorView}


def safe_result(body: dict, command: str) -> dict:
    try:
        value = Result.model_validate(body)
        if value.command != command:
            raise ValueError("unexpected command")
        if value.ok:
            value.data = COMMAND_DATA[command].model_validate(value.data).model_dump(mode="json")
            if command in COMMAND_ITEMS:
                if len(value.items) > 128:
                    raise ValueError("oversize list")
                value.items = [
                    TrustAnchorView.model_validate(item).model_dump(mode="json")
                    for item in value.items
                ]
            elif value.items:
                raise ValueError("unexpected items")
        else:
            code = value.data.get("error", {}).get("code")
            messages = {
                "invalid_input": "Invalid trust-anchor input",
                "trust_anchor_invalid": "Invalid CA certificate",
                "invalid_trust_anchor": "Invalid CA certificate",
                "trust_anchor_not_ca": "Certificate is not a CA",
                "trust_anchor_limit": "Certificate exceeds the upload limit",
                "trust_anchor_capacity": "Trust anchor store has reached its retention limit",
                "not_found": "Trust anchor is not accessible",
                "forbidden": "Platform operator authorization required",
                "invalid_session": "Platform login required",
                "trust_store_unavailable": "Trust store is unavailable; retry later",
            }
            value.data = {
                "error": {
                    "code": code if code in messages else "trust_anchor_failed",
                    "message": messages.get(code, "Trust-anchor operation failed"),
                    "exit_code": 3
                    if code == "trust_store_unavailable"
                    else 2
                    if code
                    in {
                        "invalid_input",
                        "invalid_trust_anchor",
                        "trust_anchor_invalid",
                        "trust_anchor_not_ca",
                        "trust_anchor_limit",
                        "trust_anchor_capacity",
                    }
                    else 4,
                }
            }
            value.items = []
        value.warnings = []
        return value.model_dump(mode="json")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ServiceError(
            "invalid_server_response", "Server returned invalid trust metadata", 502, 4
        ) from exc


def perform(action: str, method: str, path: str, json_output: bool, **kwargs):
    from bid_cli.main import call, emit

    command = "platform trust-anchor " + action
    body = safe_result(
        call(method, "/platform/trust-anchors" + path, platform=True, **kwargs), command
    )
    emit(body, command, json_output, 0 if body["ok"] else body["data"]["error"]["exit_code"])


@app.command("list")
def list_anchors(json_output: JsonOption = False):
    perform("list", "GET", "", json_output)


@app.command("add")
def add(
    certificate: Annotated[Path, typer.Option("--certificate")],
    label: Annotated[str, typer.Option()],
    json_output: JsonOption = False,
):
    label = label.strip()
    if not label or len(label) > 100:
        raise ServiceError("invalid_input", "A trust-anchor label is required", 400, 2)
    try:
        with certificate.open("rb") as handle:
            content = handle.read(65537)
    except OSError as exc:
        raise ServiceError("invalid_input", "Cannot read certificate file", 400, 2) from exc
    if not content or len(content) > 65536:
        raise ServiceError("invalid_input", "Certificate must be between 1 and 65536 bytes", 400, 2)
    perform(
        "add",
        "POST",
        "",
        json_output,
        data={"label": label},
        files={"certificate": ("certificate", content, "application/octet-stream")},
    )


@app.command("disable")
def disable(anchor_id: Annotated[UUID, typer.Option("--id")], json_output: JsonOption = False):
    perform("disable", "POST", f"/{anchor_id}/disable", json_output, json={})
