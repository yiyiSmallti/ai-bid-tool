import json
from uuid import uuid4

import pytest
from app.schemas.contracts import Extraction, Result, TaskCreate
from bid_cli.main import app, main
from bid_cli.schema import COMMANDS, command_schema
from pydantic import ValidationError


def test_schema_tracks_actual_commands():
    from typer.main import get_command

    root = get_command(app)
    actual = set()

    def visit(command, prefix=""):
        if hasattr(command, "commands"):
            for name, child in command.commands.items():
                visit(child, (prefix + " " + name).strip())
        else:
            actual.add(prefix)

    visit(root)
    assert actual == set(COMMANDS)
    discovered = command_schema(app)
    assert discovered["version"] == "1.1"
    for name in actual:
        assert discovered["commands"][name]["cli_parameters"]


@pytest.mark.parametrize(
    "args,command",
    [
        (["login"], "login"),
        (["org", "use"], "org use"),
        (["task", "create"], "task create"),
        (["tender", "upload"], "tender upload"),
        (["tender", "parse"], "tender parse"),
        (["req", "extract"], "req extract"),
        (["req", "list"], "req list"),
        (["job", "status"], "job status"),
        (["job", "wait"], "job wait"),
        (["job", "cancel"], "job cancel"),
        (["token", "create"], "token create"),
        (["resource", "product", "add"], "resource product add"),
        (["resource", "product", "update"], "resource product update"),
        (["task", "resource", "add"], "task resource add"),
        (["task", "resource", "list"], "task resource list"),
        (["resource", "feature", "add"], "resource feature add"),
        (["resource", "feature", "update"], "resource feature update"),
        (["task", "feature", "add"], "task feature add"),
        (["task", "feature", "list"], "task feature list"),
        (["resource", "certificate", "add"], "resource certificate add"),
        (["resource", "certificate", "update"], "resource certificate update"),
        (["task", "certificate", "add"], "task certificate add"),
        (["task", "certificate", "list"], "task certificate list"),
        (["resource", "profile", "add"], "resource profile add"),
        (["resource", "profile", "update"], "resource profile update"),
        (["task", "profile", "add"], "task profile add"),
        (["task", "profile", "list"], "task profile list"),
    ],
)
def test_missing_arguments_have_json_contract(args, command, capsys):
    with pytest.raises(SystemExit) as error:
        main([*args, "--json"])
    assert error.value.code == 2
    actual = json.loads(capsys.readouterr().out)
    assert set(actual) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    assert actual["ok"] is False
    assert actual["command"] == command
    assert actual["data"]["error"]["exit_code"] == 2


def test_schema_json_and_blank_name(capsys):
    main(["schema", "--json"])
    actual = json.loads(capsys.readouterr().out)
    assert Result.model_validate(actual).ok
    with pytest.raises(ValidationError):
        TaskCreate(name="   ")


def test_source_requires_valid_page_and_rejects_extra_fields():
    with pytest.raises(ValidationError):
        Extraction.model_validate(
            {
                "items": [
                    {
                        "category": "technical",
                        "text": "x",
                        "source": {
                            "document_id": str(uuid4()),
                            "chunk_id": str(uuid4()),
                            "page": 0,
                            "quote": "x",
                        },
                    }
                ]
            }
        )
    with pytest.raises(ValidationError):
        TaskCreate(name="x", org_id=str(uuid4()))


@pytest.mark.parametrize("case", ["malformed", "invalid_model", "oversized"])
def test_metadata_cli_rejects_input_before_transport(case, tmp_path, capsys, monkeypatch):
    from bid_cli import main as cli_module

    def forbidden_transport(*args, **kwargs):
        raise AssertionError("Invalid input must never reach a server")

    monkeypatch.setattr(cli_module, "call", forbidden_transport)
    source = tmp_path / "private-input.json"
    content = {
        "malformed": "{private-secret-value",
        "invalid_model": json.dumps(
            {"data": {"name": "Synthetic", "vendor": "Synthetic", "model": "  "}}
        ),
        "oversized": "x" * (128 * 1024 + 1),
    }[case]
    source.write_text(content)
    with pytest.raises(SystemExit) as error:
        main(["--state", "task", "resource", "product", "add", "--input", str(source), "--json"])
    body = json.loads(capsys.readouterr().out)
    assert error.value.code == 2 and body["command"] == "resource product add"
    assert body["ok"] is False and "private-secret-value" not in json.dumps(body)


@pytest.mark.parametrize(
    "value", ["20260201", "2026-02-30", "2026-01-01T00:00:00", "private-secret"]
)
@pytest.mark.parametrize("group", [["resource", "certificate"], ["task", "certificate"]])
def test_certificate_inspection_date_invalid_before_transport(value, group, capsys, monkeypatch):
    from bid_cli import main as cli_module

    def forbidden_transport(*args, **kwargs):
        raise AssertionError("Invalid date must not reach transport")

    monkeypatch.setattr(cli_module, "call", forbidden_transport)
    args = [*group, "list"]
    if group[0] == "task":
        args += ["--task", str(uuid4())]
    with pytest.raises(SystemExit) as error:
        main([*args, "--as-of", value, "--json"])
    output = json.loads(capsys.readouterr().out)
    assert error.value.code == 2 and output["data"]["error"]["code"] == "invalid_date"
    assert "private-secret" not in json.dumps(output)


@pytest.mark.parametrize("case", ["malformed", "invalid_model", "oversized"])
def test_profile_invalid_input_is_redacted_before_transport(case, tmp_path, capsys, monkeypatch):
    from bid_cli import main as cli_module

    def forbidden_transport(*args, **kwargs):
        raise AssertionError("Invalid profile data must never reach transport")

    monkeypatch.setattr(cli_module, "call", forbidden_transport)
    source = tmp_path / "private-profile.json"
    source.write_text(
        {
            "malformed": "{private-secret-profile",
            "invalid_model": json.dumps(
                {"data": {"name": "Synthetic", "contract_file": "private-secret-profile"}}
            ),
            "oversized": "x" * (128 * 1024 + 1),
        }[case]
    )
    with pytest.raises(SystemExit) as error:
        main(["resource", "profile", "add", "--input", str(source), "--json"])
    body = json.loads(capsys.readouterr().out)
    assert error.value.code == 2 and body["command"] == "resource profile add"
    assert body["ok"] is False and "private-secret-profile" not in json.dumps(body)
