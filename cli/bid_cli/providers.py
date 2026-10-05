"""Provider commands; credentials enter only through environment or private files."""

import os
import stat
from pathlib import Path
from typing import Annotated

import typer
from app.core.errors import ServiceError
from app.schemas.budget_contracts import BudgetProviderTest
from app.schemas.provider_contracts import ProviderConfigInput

app = typer.Typer()
JsonOption = Annotated[
    bool, typer.Option("--json", help="Emit the versioned machine-readable result")
]


def private_key(path: Path | None) -> str | None:
    env = os.environ.get("BID_PROVIDER_KEY")
    if path is None:
        return env
    if env:
        raise ServiceError("invalid_input", "Use either BID_PROVIDER_KEY or --key-file", 400, 2)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
            or info.st_size > 4096
        ):
            raise ServiceError(
                "invalid_input",
                "Key file must be owned by the current user, mode 0600, and at most 4096 bytes",
                400,
                2,
            )
        value = stream.read(4097).strip()
        if not value or len(value) > 4096:
            raise ServiceError("invalid_input", "Key file is empty or too large", 400, 2)
        return value


@app.command("list")
def provider_list(json_output: JsonOption = False):
    from bid_cli.main import call, emit

    emit(call("GET", "/providers"), "provider list", json_output)


@app.command("history")
def provider_history(json_output: JsonOption = False):
    from bid_cli.main import call, emit

    emit(call("GET", "/providers", params={"history": True}), "provider history", json_output)


@app.command("set")
def provider_set(
    input: Annotated[Path, typer.Option()],
    key_file: Annotated[Path | None, typer.Option()] = None,
    json_output: JsonOption = False,
):
    from bid_cli.main import call, emit, input_contract

    body = input_contract(input, ProviderConfigInput)
    key = private_key(key_file)
    if body["source"] == "platform" and key is not None:
        raise ServiceError(
            "invalid_input", "Platform selection does not accept a provider key", 400, 2
        )
    if key is not None:
        body["api_key"] = key
    emit(call("POST", "/providers", json=body), "provider set", json_output)


@app.command("test")
def provider_test(
    capability: Annotated[str, typer.Option()] = "llm_extract",
    reasoning: Annotated[str | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    from bid_cli.main import call, emit

    body = BudgetProviderTest.model_validate(
        {"capability": capability, "reasoning": reasoning, "dry_run": dry_run}
    )
    result = call("POST", "/providers/test", json=body.model_dump(mode="json"))
    code = result.get("data", {}).get("error", {}).get("exit_code", 4) if not result["ok"] else 0
    emit(result, "provider test", json_output, code)
