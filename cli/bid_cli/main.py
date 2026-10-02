import asyncio
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

import click
import typer
from app.core.errors import ServiceError
from app.schemas.certificate_contracts import (
    CertificateCreate,
    CertificateUpdate,
    TaskCertificateSelection,
)
from app.schemas.certificate_file_contracts import CertificateFileCreate
from app.schemas.citation_repair_contracts import CitationRepairRequest
from app.schemas.contracts import Contract, Result
from app.schemas.evidence_source_contracts import EvidenceSourceCreate
from app.schemas.feature_contracts import FeatureCreate, FeatureUpdate, TaskFeatureSelection
from app.schemas.platform_contracts import (
    CardRedeem,
    OrgLookup,
    PasswordSetup,
    PlatformBalanceAdjust,
    PlatformCardCreate,
    PlatformLogin,
    PlatformModelSet,
    PlatformOrgCreate,
)
from app.schemas.profile_contracts import (
    OrgProfileCreate,
    OrgProfileUpdate,
    TaskOrgProfileSelection,
)
from app.schemas.resource_contracts import ProductCreate, ProductUpdate, TaskProductSelection
from app.schemas.response_card_contracts import (
    CardAction,
    CardClassify,
    CardCreate,
    CardGenerateRequest,
    CardUpdate,
    DispositionBatch,
    DraftRequest,
    TaskRedactionSet,
)
from app.schemas.template_contracts import TaskTemplateSelection, TemplateCreate, TemplateUpdate
from app.services.certificate_files import read_file as read_certificate_file
from app.services.template_files import read_template
from pydantic import ValidationError

from bid_cli.client import Client, State, new_output_path, save_download
from bid_cli.export import app as export_app
from bid_cli.providers import app as provider_app
from bid_cli.schema import command_schema

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
app.add_typer(provider_app, name="provider")
app.add_typer(export_app, name="export")
org_app, task_app, tender_app, req_app, job_app, token_app = (typer.Typer() for _ in range(6))
resource_app, product_app, task_resource_app = (typer.Typer() for _ in range(3))
feature_app, task_feature_app = typer.Typer(), typer.Typer()
certificate_app, task_certificate_app = typer.Typer(), typer.Typer()
profile_app, task_profile_app = typer.Typer(), typer.Typer()
template_app, task_template_app = typer.Typer(), typer.Typer()
card_app = typer.Typer()
draft_app = typer.Typer(invoke_without_command=True)
redaction_app = typer.Typer()
for name, group in (
    ("org", org_app),
    ("task", task_app),
    ("tender", tender_app),
    ("req", req_app),
    ("job", job_app),
    ("token", token_app),
    ("resource", resource_app),
    ("card", card_app),
    ("draft", draft_app),
):
    app.add_typer(group, name=name)
resource_app.add_typer(product_app, name="product")
task_app.add_typer(task_resource_app, name="resource")
resource_app.add_typer(feature_app, name="feature")
task_app.add_typer(task_feature_app, name="feature")
resource_app.add_typer(certificate_app, name="certificate")
task_app.add_typer(task_certificate_app, name="certificate")
resource_app.add_typer(profile_app, name="profile")
task_app.add_typer(task_profile_app, name="profile")
resource_app.add_typer(template_app, name="template")
task_app.add_typer(task_template_app, name="template")
task_app.add_typer(redaction_app, name="redaction")

certificate_file_app, task_certificate_file_app = typer.Typer(), typer.Typer()
certificate_app.add_typer(certificate_file_app, name="file")
task_certificate_app.add_typer(task_certificate_file_app, name="file")

JsonOption = Annotated[
    bool, typer.Option("--json", help="Emit the versioned machine-readable result")
]
runtime: Client | None = None
started = 0.0


evidence_app, evidence_source_app = typer.Typer(), typer.Typer()
app.add_typer(evidence_app, name="evidence")
evidence_app.add_typer(evidence_source_app, name="source")


@app.callback()
def configure(
    mode: Annotated[str, typer.Option()] = "remote",
    server: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
    state: Annotated[Path, typer.Option()] = Path("data/cli-session.enc"),
):
    global runtime
    runtime = Client(mode, server, State(state))


def client() -> Client:
    if runtime is None:
        raise ServiceError("configuration_required", "Client is not configured", 400, 2)
    return runtime


def emit(body: dict, command: str, as_json: bool, exit_code: int = 0):
    body = dict(body)
    body["command"] = command
    body["duration_ms"] = int((time.monotonic() - started) * 1000)
    value = Result.model_validate(body)
    if as_json:
        typer.echo(value.model_dump_json())
    elif value.ok:
        typer.echo(
            json.dumps(
                {
                    "command": command,
                    "data": value.data,
                    "items": value.items,
                    "warnings": value.warnings,
                },
                ensure_ascii=False,
            )
        )
    else:
        typer.echo(value.data.get("error", {}).get("message", "Request failed"), err=True)
    if exit_code:
        raise SystemExit(exit_code)


def call(method: str, path: str, **kwargs) -> dict:
    return asyncio.run(client().request(method, path, **kwargs))


@app.command("login")
def login_command(
    email: Annotated[str, typer.Option()],
    org: Annotated[UUID, typer.Option()],
    json_output: JsonOption = False,
):
    password = os.environ.get("BID_PASSWORD")
    if not password:
        raise ServiceError(
            "password_required",
            "Set BID_PASSWORD; interactive password prompts are unsupported",
            400,
            2,
        )
    # Validate persistence before sending authentication, without printing a token.
    client().state.cipher()
    body = call(
        "POST",
        "/auth/login",
        authenticated=False,
        json={"email": email, "password": password, "org_id": str(org)},
    )
    client().state.save(body["data"])
    body["data"].pop("session", None)
    body["data"]["authenticated"] = True
    emit(body, "login", json_output)


@org_app.command("use")
def org_use(org_id: UUID, json_output: JsonOption = False):
    body = call("GET", "/org/current", org=org_id)
    saved = client().state.load()
    saved["org_id"] = str(org_id)
    client().state.save(saved)
    emit(body, "org use", json_output)


@task_app.command("create")
def task_create(
    name: Annotated[str, typer.Option()],
    tender: Annotated[Path | None, typer.Option()] = None,
    tender_number: Annotated[str | None, typer.Option()] = None,
    deadline: Annotated[datetime | None, typer.Option(formats=["%Y-%m-%dT%H:%M:%S%z"])] = None,
    budget_usd: Annotated[float | None, typer.Option()] = None,
    json_output: JsonOption = False,
):
    if tender is not None and not tender.is_file():
        raise ServiceError("missing_file", "Upload file does not exist", 400, 2)
    body = call(
        "POST",
        "/tasks",
        json={
            "name": name,
            "tender_number": tender_number,
            "deadline": deadline.isoformat() if deadline else None,
            "budget_usd": budget_usd,
        },
    )
    if tender is not None:
        try:
            uploaded = asyncio.run(client().upload(UUID(body["data"]["id"]), tender))
            body["data"]["document_id"] = uploaded["data"]["id"]
            parsed = call(
                "POST", f"/documents/{uploaded['data']['id']}/parse", json={"dry_run": False}
            )
            body["data"]["job_id"] = parsed["data"]["job_id"]
        except ServiceError as exc:
            body["ok"] = False
            body["warnings"] = ["Task was created, but subsequent file processing failed."]
            body["data"]["error"] = {"code": exc.code, "message": exc.message, "exit_code": 5}
            emit(body, "task create", json_output, 5)
    emit(body, "task create", json_output)


@task_app.command("list")
def task_list(json_output: JsonOption = False):
    emit(call("GET", "/tasks"), "task list", json_output)


def input_contract(path: Path, model: type[Contract], max_bytes: int = 128 * 1024) -> dict:
    if path.stat().st_size > max_bytes:
        raise ServiceError("input_too_large", "JSON input exceeds the command limit", 400, 2)
    return model.model_validate(json.loads(path.read_text(encoding="utf-8"))).model_dump(
        mode="json"
    )


@card_app.command("list")
def card_list(
    task: Annotated[UUID, typer.Option()],
    job: Annotated[UUID, typer.Option()],
    json_output: JsonOption = False,
):
    emit(
        call("GET", f"/tasks/{task}/cards", params={"job": str(job)}),
        "card list",
        json_output,
    )


@card_app.command("show")
def card_show(
    id: Annotated[UUID, typer.Option("--id")],
    history: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    emit(
        call("GET", f"/cards/{id}", params={"history": str(history).lower()}),
        "card show",
        json_output,
    )


@card_app.command("create")
def card_create(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, CardCreate, 4 * 1024 * 1024)
    emit(call("POST", f"/tasks/{task}/cards", json=body), "card create", json_output)


@card_app.command("update")
def card_update(
    id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, CardUpdate, 4 * 1024 * 1024)
    emit(call("PUT", f"/cards/{id}", json=body), "card update", json_output)


@card_app.command("classify")
def card_classify(
    id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, CardClassify)
    emit(
        call("POST", f"/cards/{id}/classification", json=body),
        "card classify",
        json_output,
    )


@card_app.command("disposition")
def card_disposition(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, DispositionBatch, 12 * 1024 * 1024)
    emit(
        call("POST", f"/tasks/{task}/cards/dispositions", json=body),
        "card disposition",
        json_output,
    )


def card_action_request(
    card_id: UUID,
    expected_revision: int,
    action: str,
    evidence: list[UUID],
    reviewed_warning: list[str],
    reason: str | None,
    as_json: bool,
):
    request = CardAction.model_validate(
        {
            "expected_revision": expected_revision,
            "action": action,
            "reviewed_evidence_ids": evidence,
            "reviewed_warning_codes": reviewed_warning,
            "reason": reason,
        }
    ).model_dump(mode="json")
    command = f"card {action.replace('_', '-')}"
    emit(call("POST", f"/cards/{card_id}/actions", json=request), command, as_json)


@card_app.command("submit")
def card_submit(
    id: Annotated[UUID, typer.Option("--id")],
    expected_revision: Annotated[int, typer.Option(min=1)],
    json_output: JsonOption = False,
):
    card_action_request(id, expected_revision, "submit", [], [], None, json_output)


@card_app.command("withdraw")
def card_withdraw(
    id: Annotated[UUID, typer.Option("--id")],
    expected_revision: Annotated[int, typer.Option(min=1)],
    reason: Annotated[str, typer.Option()],
    json_output: JsonOption = False,
):
    card_action_request(id, expected_revision, "withdraw", [], [], reason, json_output)


@card_app.command("confirm")
def card_confirm(
    id: Annotated[UUID, typer.Option("--id")],
    expected_revision: Annotated[int, typer.Option(min=1)],
    evidence: Annotated[list[UUID] | None, typer.Option("--evidence")] = None,
    reviewed_warning: Annotated[list[str] | None, typer.Option("--reviewed-warning")] = None,
    reason: Annotated[str | None, typer.Option()] = None,
    json_output: JsonOption = False,
):
    card_action_request(
        id,
        expected_revision,
        "confirm",
        evidence or [],
        reviewed_warning or [],
        reason,
        json_output,
    )


@card_app.command("reject")
def card_reject(
    id: Annotated[UUID, typer.Option("--id")],
    expected_revision: Annotated[int, typer.Option(min=1)],
    reason: Annotated[str, typer.Option()],
    json_output: JsonOption = False,
):
    card_action_request(id, expected_revision, "reject", [], [], reason, json_output)


@card_app.command("needs-material")
def card_needs_material(
    id: Annotated[UUID, typer.Option("--id")],
    expected_revision: Annotated[int, typer.Option(min=1)],
    reason: Annotated[str, typer.Option()],
    json_output: JsonOption = False,
):
    card_action_request(id, expected_revision, "needs_material", [], [], reason, json_output)


@card_app.command("reopen")
def card_reopen(
    id: Annotated[UUID, typer.Option("--id")],
    expected_revision: Annotated[int, typer.Option(min=1)],
    reason: Annotated[str, typer.Option()],
    json_output: JsonOption = False,
):
    card_action_request(id, expected_revision, "reopen", [], [], reason, json_output)


@redaction_app.command("set")
def task_redaction_set(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, TaskRedactionSet)
    emit(
        call("PUT", f"/tasks/{task}/model-redaction", json=body),
        "task redaction set",
        json_output,
    )


@product_app.command("add")
def product_add(input: Annotated[Path, typer.Option()], json_output: JsonOption = False):
    body = input_contract(input, ProductCreate)
    emit(call("POST", "/resources/products", json=body), "resource product add", json_output)


@product_app.command("list")
def product_list(
    product_id: Annotated[UUID | None, typer.Option("--id")] = None,
    history: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    params = {"history": str(history).lower()}
    if product_id is not None:
        params["product_id"] = str(product_id)
    emit(call("GET", "/resources/products", params=params), "resource product list", json_output)


@product_app.command("update")
def product_update(
    product_id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, ProductUpdate)
    emit(
        call("POST", f"/resources/products/{product_id}/revisions", json=body),
        "resource product update",
        json_output,
    )


@task_resource_app.command("add")
def task_resource_add(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, TaskProductSelection)
    emit(call("POST", f"/tasks/{task}/products", json=body), "task resource add", json_output)


@task_resource_app.command("list")
def task_resource_list(
    task: Annotated[UUID, typer.Option()],
    history: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    emit(
        call("GET", f"/tasks/{task}/products", params={"history": str(history).lower()}),
        "task resource list",
        json_output,
    )


@feature_app.command("add")
def feature_add(input: Annotated[Path, typer.Option()], json_output: JsonOption = False):
    body = input_contract(input, FeatureCreate)
    emit(call("POST", "/resources/features", json=body), "resource feature add", json_output)


@feature_app.command("list")
def feature_list(
    feature_id: Annotated[UUID | None, typer.Option("--id")] = None,
    history: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    params = {"history": str(history).lower()}
    if feature_id is not None:
        params["feature_id"] = str(feature_id)
    emit(call("GET", "/resources/features", params=params), "resource feature list", json_output)


@feature_app.command("update")
def feature_update(
    feature_id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, FeatureUpdate)
    emit(
        call("POST", f"/resources/features/{feature_id}/revisions", json=body),
        "resource feature update",
        json_output,
    )


@task_feature_app.command("add")
def task_feature_add(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, TaskFeatureSelection)
    emit(call("POST", f"/tasks/{task}/features", json=body), "task feature add", json_output)


@task_feature_app.command("list")
def task_feature_list(
    task: Annotated[UUID, typer.Option()],
    history: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    emit(
        call("GET", f"/tasks/{task}/features", params={"history": str(history).lower()}),
        "task feature list",
        json_output,
    )


def inspection_params(history: bool, as_of: str | None) -> dict:
    params = {"history": str(history).lower()}
    if as_of is not None:
        try:
            parsed = date.fromisoformat(as_of)
            if parsed.isoformat() != as_of:
                raise ValueError
        except ValueError as exc:
            raise ServiceError(
                "invalid_date", "Use --as-of YYYY-MM-DD with a valid date", 400, 2
            ) from exc
        params["as_of"] = parsed.isoformat()
    return params


@certificate_app.command("add")
def certificate_add(input: Annotated[Path, typer.Option()], json_output: JsonOption = False):
    body = input_contract(input, CertificateCreate)
    emit(
        call("POST", "/resources/certificates", json=body), "resource certificate add", json_output
    )


@certificate_app.command("list")
def certificate_list(
    certificate_id: Annotated[UUID | None, typer.Option("--id")] = None,
    history: Annotated[bool, typer.Option()] = False,
    as_of: Annotated[
        str | None, typer.Option("--as-of", help="Inspect declared dates as of YYYY-MM-DD")
    ] = None,
    json_output: JsonOption = False,
):
    params = inspection_params(history, as_of)
    if certificate_id is not None:
        params["certificate_id"] = str(certificate_id)
    emit(
        call("GET", "/resources/certificates", params=params),
        "resource certificate list",
        json_output,
    )


@certificate_app.command("update")
def certificate_update(
    certificate_id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, CertificateUpdate)
    emit(
        call("POST", f"/resources/certificates/{certificate_id}/revisions", json=body),
        "resource certificate update",
        json_output,
    )


@task_certificate_app.command("add")
def task_certificate_add(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, TaskCertificateSelection)
    emit(
        call("POST", f"/tasks/{task}/certificates", json=body), "task certificate add", json_output
    )


@task_certificate_app.command("list")
def task_certificate_list(
    task: Annotated[UUID, typer.Option()],
    history: Annotated[bool, typer.Option()] = False,
    as_of: Annotated[
        str | None, typer.Option("--as-of", help="Inspect declared dates as of YYYY-MM-DD")
    ] = None,
    json_output: JsonOption = False,
):
    emit(
        call("GET", f"/tasks/{task}/certificates", params=inspection_params(history, as_of)),
        "task certificate list",
        json_output,
    )


@profile_app.command("add")
def profile_add(input: Annotated[Path, typer.Option()], json_output: JsonOption = False):
    body = input_contract(input, OrgProfileCreate)
    emit(call("POST", "/resources/profiles", json=body), "resource profile add", json_output)


@profile_app.command("list")
def profile_list(
    profile_id: Annotated[UUID | None, typer.Option("--id")] = None,
    history: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    params = {"history": str(history).lower()}
    if profile_id is not None:
        params["profile_id"] = str(profile_id)
    emit(call("GET", "/resources/profiles", params=params), "resource profile list", json_output)


@profile_app.command("update")
def profile_update(
    profile_id: Annotated[UUID, typer.Option("--id")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, OrgProfileUpdate)
    emit(
        call("POST", f"/resources/profiles/{profile_id}/revisions", json=body),
        "resource profile update",
        json_output,
    )


@task_profile_app.command("add")
def task_profile_add(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, TaskOrgProfileSelection)
    emit(call("POST", f"/tasks/{task}/profiles", json=body), "task profile add", json_output)


@task_profile_app.command("list")
def task_profile_list(
    task: Annotated[UUID, typer.Option()],
    history: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    emit(
        call("GET", f"/tasks/{task}/profiles", params={"history": str(history).lower()}),
        "task profile list",
        json_output,
    )


@certificate_file_app.command("add")
def certificate_file_add(
    id: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    file: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, CertificateFileCreate)
    content = read_certificate_file(file)
    emit(
        call(
            "POST",
            f"/resources/certificates/{id}/file-revisions",
            data={"metadata": json.dumps(body)},
            files={"file": (file.name, content, "application/pdf")},
        ),
        "resource certificate file add",
        json_output,
    )


@certificate_file_app.command("list")
def certificate_file_list(
    id: Annotated[UUID | None, typer.Option()] = None,
    history: Annotated[bool, typer.Option()] = False,
    revision: Annotated[UUID | None, typer.Option()] = None,
    json_output: JsonOption = False,
):
    if revision is not None and (id is not None or history):
        raise ServiceError(
            "invalid_filter", "Revision cannot be combined with id or history", 400, 2
        )
    params: dict = {"history": history}
    if id is not None:
        params["certificate_id"] = str(id)
    if revision is not None:
        params["revision_id"] = str(revision)
    emit(
        call("GET", "/resources/certificates/files", params=params),
        "resource certificate file list",
        json_output,
    )


@task_certificate_file_app.command("list")
def task_certificate_file_list(
    task: Annotated[UUID, typer.Option()],
    history: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    emit(
        call("GET", f"/tasks/{task}/certificate-files", params={"history": history}),
        "task certificate file list",
        json_output,
    )


@certificate_file_app.command("download")
def certificate_file_download(
    revision: Annotated[UUID, typer.Option()],
    output: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    emit(
        asyncio.run(client().download_certificate_file(revision, output)),
        "resource certificate file download",
        json_output,
    )


@template_app.command("add")
def template_add(
    input: Annotated[Path, typer.Option()],
    file: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, TemplateCreate)
    content = read_template(file)
    emit(
        call(
            "POST",
            "/resources/templates",
            data={"metadata": json.dumps(body)},
            files={
                "file": (
                    file.name,
                    content,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        ),
        "resource template add",
        json_output,
    )


@template_app.command("list")
def template_list(
    id: Annotated[UUID | None, typer.Option()] = None,
    history: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    params: dict = {"history": history}
    if id is not None:
        params["template_id"] = str(id)
    emit(call("GET", "/resources/templates", params=params), "resource template list", json_output)


@template_app.command("update")
def template_update(
    id: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    file: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    body = input_contract(input, TemplateUpdate)
    content = read_template(file)
    emit(
        call(
            "POST",
            f"/resources/templates/{id}/revisions",
            data={"metadata": json.dumps(body)},
            files={
                "file": (
                    file.name,
                    content,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        ),
        "resource template update",
        json_output,
    )


@task_template_app.command("add")
def task_template_add(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    emit(
        call("POST", f"/tasks/{task}/templates", json=input_contract(input, TaskTemplateSelection)),
        "task template add",
        json_output,
    )


@task_template_app.command("list")
def task_template_list(
    task: Annotated[UUID, typer.Option()],
    history: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    emit(
        call("GET", f"/tasks/{task}/templates", params={"history": history}),
        "task template list",
        json_output,
    )


@template_app.command("download")
def template_download(
    revision: Annotated[UUID, typer.Option()],
    output: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    emit(
        asyncio.run(client().download_template(revision, output)),
        "resource template download",
        json_output,
    )


@evidence_source_app.command("add")
def evidence_source_add(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    emit(
        call(
            "POST",
            f"/tasks/{task}/evidence-sources",
            json=input_contract(input, EvidenceSourceCreate),
        ),
        "evidence source add",
        json_output,
    )


@evidence_source_app.command("list")
def evidence_source_list(
    task: Annotated[UUID, typer.Option()],
    history: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    emit(
        call("GET", f"/tasks/{task}/evidence-sources", params={"history": history}),
        "evidence source list",
        json_output,
    )


@evidence_source_app.command("download")
def evidence_source_download(
    id: Annotated[UUID, typer.Option()],
    output: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    emit(
        asyncio.run(client().download_evidence_source(id, output)),
        "evidence source download",
        json_output,
    )


@tender_app.command("upload")
def tender_upload(
    task: Annotated[UUID, typer.Option()],
    file: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    if not file.is_file():
        raise ServiceError("missing_file", "Upload file does not exist", 400, 2)
    emit(asyncio.run(client().upload(task, file)), "tender upload", json_output)


async def wait_for_job(job_id: UUID, limit_seconds: float) -> dict:
    deadline = time.monotonic() + limit_seconds
    while time.monotonic() < deadline:
        body = await client().request("GET", f"/jobs/{job_id}")
        status = body["data"]["status"]
        if status in {"succeeded", "failed", "cancelled"}:
            if status != "succeeded":
                error = body["data"].get("error") or {
                    "code": "cancelled",
                    "message": "Job was cancelled",
                    "exit_code": 4,
                }
                raise ServiceError(error["code"], error["message"], 400, error["exit_code"])
            return body
        await asyncio.sleep(0.3)
    raise ServiceError(
        "wait_timeout", "Job is still active; query its status or cancel explicitly", 408, 3
    )


def partial_completion_exit(body: dict) -> int:
    data = body.get("data", {})
    result = data.get("result") if isinstance(data.get("result"), dict) else data
    if result.get("completion") == "partial":
        body["ok"] = False
        return 5
    return 0


@card_app.command("generate")
def card_generate(
    task: Annotated[UUID, typer.Option()],
    job: Annotated[UUID, typer.Option()],
    requirement: Annotated[list[UUID] | None, typer.Option("--requirement")] = None,
    reasoning: Annotated[
        str | None, typer.Option(help="One of the model's official reasoning levels")
    ] = None,
    dry_run: Annotated[bool, typer.Option()] = False,
    retry: Annotated[bool, typer.Option()] = False,
    wait: Annotated[bool, typer.Option()] = False,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: JsonOption = False,
):
    request = CardGenerateRequest(
        extraction_job_id=job,
        requirement_ids=requirement or None,
        reasoning=reasoning,
        dry_run=dry_run,
        retry=retry,
    )
    body = call("POST", f"/tasks/{task}/cards/generations", json=request.model_dump(mode="json"))
    if wait and not dry_run:
        terminal = asyncio.run(wait_for_job(UUID(body["data"]["job_id"]), timeout))
        output = terminal["data"]["result"]
        body["data"].update(output)
        body["data"]["status"] = terminal["data"]["status"]
        body["cost"] = output["cost"]
        body["warnings"] = output.get("warnings", [])
    emit(body, "card generate", json_output, partial_completion_exit(body))


@draft_app.callback()
def draft_run(
    ctx: typer.Context,
    task: Annotated[UUID | None, typer.Option()] = None,
    job: Annotated[UUID | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option()] = False,
    retry: Annotated[bool, typer.Option()] = False,
    wait: Annotated[bool, typer.Option()] = False,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: JsonOption = False,
):
    if ctx.invoked_subcommand is not None:
        return
    if task is None or job is None:
        raise ServiceError("invalid_input", "Draft requires --task and --job", 400, 2)
    request = DraftRequest(
        extraction_job_id=job,
        dry_run=dry_run,
        retry=retry,
    ).model_dump(mode="json")
    body = call("POST", f"/tasks/{task}/drafts", json=request)
    if wait and not dry_run:
        terminal = asyncio.run(wait_for_job(UUID(body["data"]["job_id"]), timeout))
        result = terminal["data"].get("result") or {}
        draft_id = result.get("draft_id")
        if not draft_id:
            raise ServiceError(
                "invalid_server_response",
                "Completed draft job did not identify its assembled draft",
                502,
                4,
            )
        body = call("GET", f"/drafts/{UUID(draft_id)}")
    exit_code = partial_completion_exit(body)
    emit(body, "draft", json_output, exit_code)


@draft_app.command("show")
def draft_show(
    id: Annotated[UUID, typer.Option("--id")],
    json_output: JsonOption = False,
):
    body = call("GET", f"/drafts/{id}")
    emit(body, "draft show", json_output, partial_completion_exit(body))


@draft_app.command("list")
def draft_list(
    task: Annotated[UUID, typer.Option()],
    job: Annotated[UUID, typer.Option()],
    json_output: JsonOption = False,
):
    emit(
        call("GET", f"/tasks/{task}/drafts", params={"job": str(job)}),
        "draft list",
        json_output,
    )


def process(
    document: UUID,
    kind: str,
    wait: bool,
    dry_run: bool,
    timeout: float,
    as_json: bool,
    retry: bool = False,
    reasoning: str | None = None,
):
    command = "tender parse" if kind == "parse" else "req extract"
    request: dict = {"dry_run": dry_run, "retry": retry}
    if reasoning is not None:
        request["reasoning"] = reasoning
    body = call("POST", f"/documents/{document}/{kind}", json=request)
    if wait and not dry_run:
        terminal = asyncio.run(wait_for_job(UUID(body["data"]["job_id"]), timeout))
        body["data"].update(terminal["data"]["result"])
        body["data"]["status"] = "succeeded"
        body["warnings"] = terminal["data"]["result"].get("warnings", [])
        body["cost"] = terminal["data"]["result"].get("cost", body["cost"])
        if kind == "parse":
            body["items"] = call("GET", f"/documents/{document}/chunks")["items"]
        else:
            # The requirements of this extraction, not the task's latest of every document.
            task = call("GET", f"/documents/{document}")["data"]["task_id"]
            job = body["data"]["job_id"]
            body["items"] = call("GET", f"/tasks/{task}/requirements", params={"job": job})["items"]
    emit(body, command, as_json)


@tender_app.command("parse")
def tender_parse(
    document: Annotated[UUID, typer.Option()],
    wait: Annotated[bool, typer.Option()] = False,
    dry_run: Annotated[bool, typer.Option()] = False,
    retry: Annotated[bool, typer.Option()] = False,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: JsonOption = False,
):
    process(document, "parse", wait, dry_run, timeout, json_output, retry)


@req_app.command("extract")
def req_extract(
    document: Annotated[UUID, typer.Option()],
    wait: Annotated[bool, typer.Option()] = False,
    dry_run: Annotated[bool, typer.Option()] = False,
    retry: Annotated[bool, typer.Option()] = False,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    reasoning: Annotated[
        str | None, typer.Option(help="One of the model's official reasoning levels")
    ] = None,
    json_output: JsonOption = False,
):
    process(document, "extract", wait, dry_run, timeout, json_output, retry, reasoning)


@req_app.command("list")
def req_list(
    task: Annotated[UUID, typer.Option()],
    job: Annotated[UUID | None, typer.Option(help="Show this extraction instead")] = None,
    json_output: JsonOption = False,
):
    params = {"job": str(job)} if job else None
    emit(call("GET", f"/tasks/{task}/requirements", params=params), "req list", json_output)


@req_app.command("history")
def req_history(
    task: Annotated[UUID, typer.Option()],
    document: Annotated[UUID | None, typer.Option()] = None,
    json_output: JsonOption = False,
):
    params = {"document": str(document)} if document else None
    emit(call("GET", f"/tasks/{task}/extractions", params=params), "req history", json_output)


@req_app.command("repair-citations")
def req_repair_citations(
    task: Annotated[UUID, typer.Option()],
    job: Annotated[UUID, typer.Option()],
    execute: Annotated[bool, typer.Option(help="Apply an explicitly reviewed preview")] = False,
    expected_preview: Annotated[
        str | None, typer.Option(help="Hash from the read-only preview")
    ] = None,
    reason: Annotated[str | None, typer.Option()] = None,
    json_output: JsonOption = False,
):
    path = f"/tasks/{task}/requirements/repair"
    if execute:
        if expected_preview is None or reason is None:
            raise ServiceError(
                "invalid_input", "Execution requires --expected-preview and --reason", 400, 2
            )
        body = CitationRepairRequest(
            extraction_job_id=job, expected_preview=expected_preview, reason=reason
        )
        response = call("POST", path, json=body.model_dump(mode="json"))
    else:
        if expected_preview is not None or reason is not None:
            raise ServiceError(
                "invalid_input", "Use --execute with --expected-preview and --reason", 400, 2
            )
        response = call("GET", path, params={"job": str(job)})
    emit(response, "req repair-citations", json_output)


@job_app.command("status")
def job_status(job_id: UUID, json_output: JsonOption = False):
    body = call("GET", f"/jobs/{job_id}")
    emit(body, "job status", json_output, partial_completion_exit(body))


@job_app.command("wait")
def job_wait(
    job_id: UUID,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: JsonOption = False,
):
    body = asyncio.run(wait_for_job(job_id, timeout))
    emit(body, "job wait", json_output, partial_completion_exit(body))


@job_app.command("cancel")
def job_cancel(job_id: UUID, json_output: JsonOption = False):
    emit(call("POST", f"/jobs/{job_id}/cancel"), "job cancel", json_output)


@token_app.command("create")
def token_create(
    name: Annotated[str, typer.Option()],
    scope: Annotated[list[str], typer.Option()],
    expires_at: Annotated[datetime, typer.Option(formats=["%Y-%m-%dT%H:%M:%S%z"])],
    output: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    if output.exists():
        raise ServiceError("existing_token_file", "Token output file already exists", 400, 2)
    cipher = client().state.cipher()
    body = call(
        "POST",
        "/tokens",
        json={"name": name, "scopes": scope, "expires_at": expires_at.isoformat()},
    )
    secret = body["data"].pop("token")
    encrypted = cipher.encrypt(secret)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(encrypted)
    body["data"]["encrypted_token_file"] = str(output)
    emit(body, "token create", json_output)


platform_app, platform_org_app, platform_model_app, auth_app = (typer.Typer() for _ in range(4))
app.add_typer(platform_app, name="platform")
app.add_typer(auth_app, name="auth")
platform_app.add_typer(platform_org_app, name="org")
platform_app.add_typer(platform_model_app, name="model")


def env_secret(name: str, purpose: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ServiceError(
            f"{purpose}_required", f"Set {name}; interactive prompts are unsupported", 400, 2
        )
    return value


@platform_app.command("login")
def platform_login(
    email: Annotated[str, typer.Option()],
    totp: Annotated[str, typer.Option(help="Current six-digit authenticator code")],
    json_output: JsonOption = False,
):
    password = env_secret("BID_PASSWORD", "password")
    body = PlatformLogin.model_validate({"email": email, "password": password, "totp": totp})
    client().state.cipher()
    result = call("POST", "/platform/auth/login", authenticated=False, json=body.model_dump())
    client().state.save_platform(result["data"].pop("session"))
    result["data"]["authenticated"] = True
    emit(result, "platform login", json_output)


@platform_org_app.command("list")
def platform_org_list(json_output: JsonOption = False):
    emit(call("GET", "/platform/orgs", platform=True), "platform org list", json_output)


@platform_org_app.command("create")
def platform_org_create(
    name: Annotated[str, typer.Option()],
    admin_email: Annotated[str, typer.Option()],
    json_output: JsonOption = False,
):
    body = PlatformOrgCreate.model_validate({"name": name, "admin_email": admin_email})
    emit(
        call("POST", "/platform/orgs", platform=True, json=body.model_dump()),
        "platform org create",
        json_output,
    )


@platform_org_app.command("set-active")
def platform_org_set_active(
    org_id: Annotated[UUID, typer.Option("--id")],
    active: Annotated[bool, typer.Option("--active/--inactive")],
    json_output: JsonOption = False,
):
    emit(
        call("POST", f"/platform/orgs/{org_id}/active", platform=True, json={"active": active}),
        "platform org set-active",
        json_output,
    )


@platform_model_app.command("list")
def platform_model_list(json_output: JsonOption = False):
    emit(call("GET", "/platform/models", platform=True), "platform model list", json_output)


@platform_model_app.command("set")
def platform_model_set(input: Annotated[Path, typer.Option()], json_output: JsonOption = False):
    body = input_contract(input, PlatformModelSet)
    emit(
        call("POST", "/platform/models", platform=True, json=body),
        "platform model set",
        json_output,
    )


@platform_model_app.command("test")
def platform_model_test(
    model_id: Annotated[str, typer.Option("--id")], json_output: JsonOption = False
):
    emit(
        call("POST", f"/platform/models/{model_id}/test", platform=True),
        "platform model test",
        json_output,
    )


@platform_app.command("usage")
def platform_usage(
    start: Annotated[str | None, typer.Option("--from", help="First month, YYYY-MM")] = None,
    end: Annotated[str | None, typer.Option("--to", help="Last month, YYYY-MM")] = None,
    json_output: JsonOption = False,
):
    params = {key: value for key, value in (("from", start), ("to", end)) if value}
    emit(
        call("GET", "/platform/usage", platform=True, params=params), "platform usage", json_output
    )


@platform_app.command("audit")
def platform_audit(
    limit: Annotated[int, typer.Option(min=1, max=500)] = 100, json_output: JsonOption = False
):
    emit(
        call("GET", "/platform/audit", platform=True, params={"limit": limit}),
        "platform audit",
        json_output,
    )


platform_card_app, billing_app = typer.Typer(), typer.Typer()
platform_app.add_typer(platform_card_app, name="card")
app.add_typer(billing_app, name="billing")


@platform_card_app.command("create")
def platform_card_create(
    count: Annotated[int, typer.Option(min=1, max=500)],
    face_value: Annotated[float, typer.Option()],
    output: Annotated[Path, typer.Option(help="New CSV file that receives the codes")],
    expires_at: Annotated[datetime | None, typer.Option(formats=["%Y-%m-%dT%H:%M:%S%z"])] = None,
    note: Annotated[str | None, typer.Option()] = None,
    json_output: JsonOption = False,
):
    target = new_output_path(output)
    body = PlatformCardCreate.model_validate(
        {"count": count, "face_value": face_value, "expires_at": expires_at, "note": note}
    )
    result = call("POST", "/platform/cards", platform=True, json=body.model_dump(mode="json"))
    cards = result["data"].pop("cards")
    lines = ["code,last4,face_value,currency,batch_id,expires_at"] + [
        ",".join(
            [
                card["code"],
                card["last4"],
                str(result["data"]["face_value"]),
                result["data"]["currency"],
                result["data"]["batch_id"],
                result["data"]["expires_at"] or "",
            ]
        )
        for card in cards
    ]
    # Codes go only to the new 0600 file, never to stdout or JSON.
    save_download(target, ("\n".join(lines) + "\n").encode())
    result["data"]["output_path"] = str(target)
    emit(result, "platform card create", json_output)


@platform_card_app.command("list")
def platform_card_list(
    batch: Annotated[UUID | None, typer.Option()] = None,
    status: Annotated[str | None, typer.Option(help="active, redeemed or void")] = None,
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 200,
    json_output: JsonOption = False,
):
    params: dict = {"limit": limit}
    if batch:
        params["batch_id"] = str(batch)
    if status:
        params["status"] = status
    emit(
        call("GET", "/platform/cards", platform=True, params=params),
        "platform card list",
        json_output,
    )


@platform_card_app.command("void")
def platform_card_void(
    card_id: Annotated[UUID, typer.Option("--id")], json_output: JsonOption = False
):
    emit(
        call("POST", f"/platform/cards/{card_id}/void", platform=True),
        "platform card void",
        json_output,
    )


@platform_org_app.command("balance")
def platform_org_balance(
    org_id: Annotated[UUID, typer.Option("--id")],
    reason: Annotated[str, typer.Option()],
    add: Annotated[float | None, typer.Option("--add")] = None,
    set_to: Annotated[float | None, typer.Option("--set")] = None,
    json_output: JsonOption = False,
):
    if (add is None) == (set_to is None):
        raise ServiceError("invalid_input", "Give exactly one of --add or --set", 400, 2)
    mode, amount = ("add", add) if add is not None else ("set", set_to)
    body = PlatformBalanceAdjust.model_validate({"mode": mode, "amount": amount, "reason": reason})
    emit(
        call("POST", f"/platform/orgs/{org_id}/balance", platform=True, json=body.model_dump()),
        "platform org balance",
        json_output,
    )


@billing_app.command("balance")
def billing_balance(json_output: JsonOption = False):
    emit(call("GET", "/billing"), "billing balance", json_output)


@billing_app.command("redeem")
def billing_redeem(json_output: JsonOption = False):
    # The card code comes from the environment so it never appears in argv.
    body = CardRedeem.model_validate({"code": env_secret("BID_CARD_CODE", "card_code")})
    emit(call("POST", "/billing/redeem", json=body.model_dump()), "billing redeem", json_output)


@auth_app.command("orgs")
def auth_orgs(email: Annotated[str, typer.Option()], json_output: JsonOption = False):
    body = OrgLookup.model_validate(
        {"email": email, "password": env_secret("BID_PASSWORD", "password")}
    )
    emit(
        call("POST", "/auth/orgs", authenticated=False, json=body.model_dump()),
        "auth orgs",
        json_output,
    )


@auth_app.command("setup-password")
def auth_setup_password(json_output: JsonOption = False):
    # Token and password come from the environment so neither appears in argv.
    body = PasswordSetup.model_validate(
        {
            "token": env_secret("BID_SETUP_TOKEN", "setup_token"),
            "password": env_secret("BID_PASSWORD", "password"),
        }
    )
    emit(
        call("POST", "/auth/setup-password", authenticated=False, json=body.model_dump()),
        "auth setup-password",
        json_output,
    )


@app.command("schema")
def schema_command(json_output: JsonOption = False):
    emit(
        Result(ok=True, command="schema", data=command_schema(app)).model_dump(mode="json"),
        "schema",
        json_output,
    )


def command_name(arguments: list[str]) -> str:
    from typer.main import get_command

    root = get_command(app)
    index = 0
    while index < len(arguments) and arguments[index].startswith("-"):
        option = arguments[index].split("=", 1)[0]
        parameter = next(
            (
                item
                for item in root.params
                if isinstance(item, click.Option) and option in item.opts
            ),
            None,
        )
        if parameter is None:
            return "bid"
        index += 1
        if (
            isinstance(parameter, click.Option)
            and not parameter.is_flag
            and "=" not in arguments[index - 1]
        ):
            index += parameter.nargs
    command, names = root, []
    while isinstance(command, click.Group) and index < len(arguments):
        name = arguments[index]
        if name not in command.commands:
            break
        names.append(name)
        command = command.commands[name]
        index += 1
    return " ".join(names) or "bid"


def main(args: list[str] | None = None):
    global started
    started = time.monotonic()
    arguments = list(sys.argv[1:] if args is None else args)
    try:
        app(args=arguments, standalone_mode=False)
    except ServiceError as exc:
        emit(
            Result(
                ok=False,
                command=command_name(arguments),
                data={
                    "error": {"code": exc.code, "message": exc.message, "exit_code": exc.exit_code}
                },
            ).model_dump(mode="json"),
            command_name(arguments),
            "--json" in arguments,
            exc.exit_code,
        )
    except (click.ClickException, ValidationError, ValueError, OSError):
        error = ServiceError(
            "invalid_input", "Invalid or missing command parameters or local configuration", 400, 2
        )
        emit(
            Result(
                ok=False,
                command=command_name(arguments),
                data={"error": {"code": error.code, "message": error.message, "exit_code": 2}},
            ).model_dump(mode="json"),
            command_name(arguments),
            "--json" in arguments,
            2,
        )
    except KeyboardInterrupt:
        raise SystemExit(3) from None


if __name__ == "__main__":
    main()
