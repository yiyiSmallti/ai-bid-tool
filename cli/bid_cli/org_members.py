"""Org member management over the shared authenticated API."""

from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.contracts import Contract, Result
from app.schemas.org_members import (
    OrgMemberActiveChange,
    OrgMemberAdd,
    OrgMemberInvited,
    OrgMemberRoleChange,
    OrgMemberView,
)


class OrgMemberListData(Contract):
    """The member list has no data fields; records are returned in items."""


COMMAND_INPUTS = {
    "org member list": None,
    "org member add": OrgMemberAdd,
    "org member role": OrgMemberRoleChange,
    "org member set-active": OrgMemberActiveChange,
    "org member invite": None,
}
COMMAND_DATA = {
    "org member list": OrgMemberListData,
    "org member add": OrgMemberInvited,
    "org member role": OrgMemberView,
    "org member set-active": OrgMemberView,
    "org member invite": OrgMemberInvited,
}
COMMAND_ITEMS = {"org member list": OrgMemberView}


def validated(body: dict, command: str) -> dict:
    """Validate the shared output without adding fields to restricted member views."""
    try:
        if set(body) != {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}:
            raise ValueError("invalid envelope")
        result = Result.model_validate(body)
        if not result.ok or result.command != command:
            raise ValueError("invalid command result")
        COMMAND_DATA[command].model_validate(result.data)
        if command in COMMAND_ITEMS:
            for item in result.items:
                COMMAND_ITEMS[command].model_validate(item)
        elif result.items:
            raise ValueError("unexpected items")
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceError(
            "invalid_server_response", "Server returned an invalid org member result", 502, 4
        ) from exc
    return body


def register(org_app: typer.Typer):
    from bid_cli import main as cli

    member_app = typer.Typer()
    org_app.add_typer(member_app, name="member")
    prefix = "/org/members"

    def send(method, path, action, json_output, **kwargs):
        command = "org member " + action
        cli.emit(
            validated(cli.call(method, prefix + path, **kwargs), command), command, json_output
        )

    @member_app.command("list")
    def list_members(json_output: cli.JsonOption = False):
        send("GET", "", "list", json_output)

    @member_app.command("add")
    def add(
        email: Annotated[str, typer.Option()],
        role: Annotated[str, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        body = OrgMemberAdd.model_validate({"email": email, "role": role})
        send("POST", "", "add", json_output, json=body.model_dump(mode="json"))

    @member_app.command("role")
    def change_role(
        user: Annotated[UUID, typer.Option()],
        role: Annotated[str, typer.Option()],
        expected_revision: Annotated[int, typer.Option(min=1)],
        json_output: cli.JsonOption = False,
    ):
        body = OrgMemberRoleChange.model_validate(
            {"role": role, "expected_revision": expected_revision}
        )
        send("POST", f"/{user}/role", "role", json_output, json=body.model_dump(mode="json"))

    @member_app.command("set-active")
    def set_active(
        user: Annotated[UUID, typer.Option()],
        active: Annotated[str, typer.Option(help="true or false")],
        expected_revision: Annotated[int, typer.Option(min=1)],
        json_output: cli.JsonOption = False,
    ):
        if active not in {"true", "false"}:
            raise ServiceError("invalid_input", "Active must be true or false", 400, 2)
        body = OrgMemberActiveChange(active=active == "true", expected_revision=expected_revision)
        send(
            "POST", f"/{user}/active", "set-active", json_output, json=body.model_dump(mode="json")
        )

    @member_app.command("invite")
    def invite(
        user: Annotated[UUID, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        send("POST", f"/{user}/invitation", "invite", json_output)
