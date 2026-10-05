"""Platform-only credential commands with private file input and safe wire output."""

import json
import os
import re
import stat
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from app.schemas.platform_credentials import (
    CredentialCreate,
    CredentialCreateInput,
    CredentialData,
    CredentialErrorData,
    CredentialImportData,
    CredentialImportItem,
    CredentialImportManifest,
    CredentialImportRequest,
    CredentialListData,
    CredentialListQuery,
    CredentialProbeData,
    CredentialRemove,
    CredentialReplace,
    CredentialSetActive,
    CredentialTest,
    CredentialView,
    Reason,
)
from pydantic import SecretStr, ValidationError

app = typer.Typer()
JsonOption = Annotated[bool, typer.Option("--json")]
ReasonOption = Annotated[Reason, typer.Option()]
RevisionOption = Annotated[int, typer.Option(min=1)]


def invalid_input() -> ServiceError:
    return ServiceError(
        "invalid_input", "Invalid credential parameters or protected input file", 400, 2
    )


def private_bytes(path: Path, maximum: int) -> bytes:
    """Open every path component without symlinks, checking the opened file itself."""
    descriptors = []
    try:
        absolute = path.absolute()
        current = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(current)
        for part in absolute.parts[1:-1]:
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            descriptors.append(current)
        descriptor = os.open(
            absolute.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current
        )
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid()
                or info.st_size > maximum
            ):
                raise invalid_input()
            value = stream.read(maximum + 1)
            if len(value) > maximum:
                raise invalid_input()
            return value
    except (OSError, ValueError):
        raise invalid_input() from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def private_key(path: Path) -> str:
    try:
        value = private_bytes(path, 4097).decode("ascii")
    except UnicodeError:
        raise invalid_input() from None
    # A single LF is accepted; CRLF, internal whitespace and repeated LF are rejected.
    return value[:-1] if value.endswith("\n") else value


def read_metadata(path: Path, model):
    try:
        if path.stat().st_size > 512 * 1024:
            raise invalid_input()
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError):
        raise invalid_input() from None


def import_request(manifest: Path, env_file: Path, dry_run: bool) -> CredentialImportRequest:
    metadata = read_metadata(manifest, CredentialImportManifest)
    try:
        text = private_bytes(env_file, 512 * 1024).decode("utf-8")
        wanted = {entry.source_env for entry in metadata.entries}
        values = {}
        seen = set()
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = re.fullmatch(r"(?:export )?([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
            if not match:
                raise invalid_input()
            name, value = match.groups()
            if name in seen:
                raise invalid_input()
            seen.add(name)
            # This is a literal assignment grammar, not a shell parser. Reject expansion,
            # escaping, comments after values and ambiguous quoting even in unrelated lines.
            if value.startswith(("'", '"')):
                quote = value[0]
                if len(value) < 2 or value[-1] != quote or quote in value[1:-1]:
                    raise invalid_input()
                value = value[1:-1]
            elif not re.fullmatch(r"[!-~]*", value) or any(c in value for c in "'\"#;"):
                raise invalid_input()
            if any(c in value for c in "$`\\") or any(ord(c) < 32 or ord(c) > 126 for c in value):
                raise invalid_input()
            if name in wanted:
                values[name] = value
        if set(values) != wanted:
            raise invalid_input()
        entries = [
            entry.model_dump() | {"api_key": values[entry.source_env], "reason": "migration"}
            for entry in metadata.entries
        ]
        return CredentialImportRequest.model_validate({"entries": entries, "dry_run": dry_run})
    except (UnicodeError, ValidationError, ValueError):
        raise invalid_input() from None


def secret_wire(model) -> dict:
    """Only the HTTP writer can extract a write-only SecretStr field."""
    return model.model_dump(mode="json") | {"api_key": model.api_key.get_secret_value()}


def safe_result(body: dict, command: str) -> dict:
    """Validate credential replies before allowing them into CLI output, including probes."""
    try:
        if set(body) != {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}:
            raise ValueError("invalid envelope")
        result = Result.model_validate(body)
        if result.command != command or result.warnings:
            raise ValueError("invalid command or warnings")
        if not result.ok:
            data = CredentialErrorData.model_validate(result.data)
            # A remote error message is never trusted to contain only safe text.
            data.error.message = "Credential operation failed: " + data.error.code
            result.data = data.model_dump(mode="json", exclude_none=True)
            if result.items:
                raise ValueError("error items")
        elif command.endswith(" list"):
            result.data = CredentialListData.model_validate(result.data).model_dump(mode="json")
            result.items = [
                CredentialView.model_validate(item).model_dump(mode="json") for item in result.items
            ]
        elif command.endswith(" import-env"):
            result.data = CredentialImportData.model_validate(result.data).model_dump(mode="json")
            result.items = [
                CredentialImportItem.model_validate(item).model_dump(mode="json")
                for item in result.items
            ]
        else:
            model = CredentialProbeData if command.endswith(" test") else CredentialData
            result.data = model.model_validate(result.data).model_dump(mode="json")
            if result.items:
                raise ValueError("unexpected items")
        return result.model_dump(mode="json")
    except (KeyError, TypeError, ValidationError, ValueError):
        raise ServiceError(
            "invalid_server_response", "Server returned invalid credential metadata", 502, 4
        ) from None


def perform(action: str, method: str, path: str, json_output: bool, **kwargs):
    from bid_cli.main import call, emit

    command = "platform credential " + action
    result = safe_result(
        call(method, "/platform/credentials" + path, platform=True, **kwargs), command
    )
    exit_code = result["data"]["error"]["exit_code"] if not result["ok"] else 0
    emit(result, command, json_output, exit_code)


@app.command("list")
def list_credentials(
    state: Annotated[Literal["active", "disabled", "removed"] | None, typer.Option()] = None,
    purpose: Annotated[
        Literal["catalog_llm", "standalone_llm", "vendor_search"] | None, typer.Option()
    ] = None,
    after_name: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 100,
    json_output: JsonOption = False,
):
    query = CredentialListQuery(state=state, purpose=purpose, after_name=after_name, limit=limit)
    perform("list", "GET", "", json_output, params=query.model_dump(exclude_none=True))


@app.command("show")
def show(credential_id: Annotated[UUID, typer.Option("--id")], json_output: JsonOption = False):
    perform("show", "GET", f"/{credential_id}", json_output)


@app.command("create")
def create(
    input: Annotated[Path, typer.Option()],
    key_file: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    metadata = read_metadata(input, CredentialCreateInput)
    body = CredentialCreate.model_validate(
        metadata.model_dump() | {"api_key": private_key(key_file)}
    )
    perform("create", "POST", "", json_output, json=secret_wire(body))


@app.command("replace")
def replace(
    credential_id: Annotated[UUID, typer.Option("--id")],
    expected_revision: RevisionOption,
    reason: ReasonOption,
    key_file: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = CredentialReplace(
        expected_revision=expected_revision, reason=reason, api_key=SecretStr(private_key(key_file))
    )
    perform("replace", "POST", f"/{credential_id}/replace", json_output, json=secret_wire(body))


@app.command("set-active")
def set_active(
    credential_id: Annotated[UUID, typer.Option("--id")],
    expected_revision: RevisionOption,
    reason: ReasonOption,
    active: Annotated[bool, typer.Option("--active/--inactive")],
    json_output: JsonOption = False,
):
    body = CredentialSetActive(expected_revision=expected_revision, reason=reason, active=active)
    perform(
        "set-active",
        "POST",
        f"/{credential_id}/active",
        json_output,
        json=body.model_dump(mode="json"),
    )


@app.command("remove")
def remove(
    credential_id: Annotated[UUID, typer.Option("--id")],
    expected_revision: RevisionOption,
    reason: Annotated[Literal["vendor_revoked", "incident", "retired"], typer.Option()],
    json_output: JsonOption = False,
):
    body = CredentialRemove(expected_revision=expected_revision, reason=reason)
    perform(
        "remove", "POST", f"/{credential_id}/remove", json_output, json=body.model_dump(mode="json")
    )


@app.command("test")
def test(
    credential_id: Annotated[UUID, typer.Option("--id")],
    expected_revision: RevisionOption,
    json_output: JsonOption = False,
):
    body = CredentialTest(expected_revision=expected_revision)
    perform(
        "test", "POST", f"/{credential_id}/test", json_output, json=body.model_dump(mode="json")
    )


@app.command("import-env")
def import_env(
    manifest: Annotated[Path, typer.Option()],
    env_file: Annotated[Path, typer.Option()],
    dry_run: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    body = import_request(manifest, env_file, dry_run)
    wire = {"entries": [secret_wire(entry) for entry in body.entries], "dry_run": body.dry_run}
    if len(json.dumps(wire).encode()) > 512 * 1024:
        raise invalid_input()
    perform("import-env", "POST", "/import-env", json_output, json=wire)
