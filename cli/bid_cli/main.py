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
from app.schemas.contracts import Contract, Result
from app.schemas.evidence_source_contracts import EvidenceSourceCreate
from app.schemas.feature_contracts import FeatureCreate, FeatureUpdate, TaskFeatureSelection
from app.schemas.profile_contracts import (
    OrgProfileCreate,
    OrgProfileUpdate,
    TaskOrgProfileSelection,
)
from app.schemas.resource_contracts import ProductCreate, ProductUpdate, TaskProductSelection
from app.schemas.template_contracts import TaskTemplateSelection, TemplateCreate, TemplateUpdate
from app.services.certificate_files import read_file as read_certificate_file
from app.services.template_files import read_template
from pydantic import ValidationError

from bid_cli.client import Client, State
from bid_cli.schema import command_schema

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
org_app, task_app, tender_app, req_app, job_app, token_app = (typer.Typer() for _ in range(6))
resource_app, product_app, task_resource_app = (typer.Typer() for _ in range(3))
feature_app, task_feature_app = typer.Typer(), typer.Typer()
certificate_app, task_certificate_app = typer.Typer(), typer.Typer()
profile_app, task_profile_app = typer.Typer(), typer.Typer()
template_app, task_template_app = typer.Typer(), typer.Typer()
for name, group in (
    ("org", org_app),
    ("task", task_app),
    ("tender", tender_app),
    ("req", req_app),
    ("job", job_app),
    ("token", token_app),
    ("resource", resource_app),
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


def input_contract(path: Path, model: type[Contract]) -> dict:
    if path.stat().st_size > 128 * 1024:
        raise ServiceError("input_too_large", "Metadata input exceeds the limit", 400, 2)
    return model.model_validate(json.loads(path.read_text(encoding="utf-8"))).model_dump(
        mode="json"
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


def process(
    document: UUID,
    kind: str,
    wait: bool,
    dry_run: bool,
    timeout: float,
    as_json: bool,
    retry: bool = False,
):
    command = "tender parse" if kind == "parse" else "req extract"
    body = call("POST", f"/documents/{document}/{kind}", json={"dry_run": dry_run, "retry": retry})
    if wait and not dry_run:
        terminal = asyncio.run(wait_for_job(UUID(body["data"]["job_id"]), timeout))
        body["data"].update(terminal["data"]["result"])
        body["data"]["status"] = "succeeded"
        body["warnings"] = terminal["data"]["result"].get("warnings", [])
        body["cost"] = terminal["data"]["result"].get("cost", body["cost"])
        if kind == "parse":
            body["items"] = call("GET", f"/documents/{document}/chunks")["items"]
        else:
            task = call("GET", f"/documents/{document}")["data"]["task_id"]
            body["items"] = call("GET", f"/tasks/{task}/requirements")["items"]
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
    json_output: JsonOption = False,
):
    process(document, "extract", wait, dry_run, timeout, json_output, retry)


@req_app.command("list")
def req_list(task: Annotated[UUID, typer.Option()], json_output: JsonOption = False):
    emit(call("GET", f"/tasks/{task}/requirements"), "req list", json_output)


@job_app.command("status")
def job_status(job_id: UUID, json_output: JsonOption = False):
    emit(call("GET", f"/jobs/{job_id}"), "job status", json_output)


@job_app.command("wait")
def job_wait(
    job_id: UUID,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: JsonOption = False,
):
    emit(asyncio.run(wait_for_job(job_id, timeout)), "job wait", json_output)


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
