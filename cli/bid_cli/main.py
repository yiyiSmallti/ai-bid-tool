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
from app.services.certificate_files import EXTENSIONS as CERTIFICATE_EXTENSIONS
from app.services.certificate_files import read_file as read_certificate_file
from app.services.template_files import read_template
from pydantic import ValidationError

from bid_cli.agent import app as agent_app
from bid_cli.annotation import register as register_annotation_commands
from bid_cli.assessments import app as assessment_app
from bid_cli.attachments import COMMAND_INPUTS as ATTACHMENT_COMMANDS
from bid_cli.attachments import register as register_attachment_commands
from bid_cli.bid_review import app as review_app
from bid_cli.check import app as check_app
from bid_cli.check import check_job_exit
from bid_cli.client import Client
from bid_cli.confidential import register as register_confidential_commands
from bid_cli.export import app as export_app
from bid_cli.management_bindings import register as register_management_binding_commands
from bid_cli.management_certificates import register as register_management_certificate_commands
from bid_cli.management_features import register as register_management_feature_commands
from bid_cli.management_memory import register as register_management_memory_commands
from bid_cli.management_products import register as register_management_product_commands
from bid_cli.management_profiles import register as register_management_profile_commands
from bid_cli.management_providers import register as register_management_provider_commands
from bid_cli.management_templates import register as register_management_template_commands
from bid_cli.members import register as register_member_commands
from bid_cli.memory import app as memory_app
from bid_cli.memory import memory_job_exit
from bid_cli.packages import register as register_package_commands
from bid_cli.platform_clef import app as platform_clef_app
from bid_cli.platform_credentials import app as platform_credential_app
from bid_cli.platform_trust_anchors import app as platform_trust_anchor_app
from bid_cli.providers import app as provider_app
from bid_cli.requirement_confirmation import register as register_requirement_confirmation_commands
from bid_cli.sandbox import sandbox_app, sandbox_job_exit
from bid_cli.schema import command_schema
from bid_cli.score import app as score_app
from bid_cli.score import rubric_job_exit, score_job_exit
from bid_cli.screenshots import register as register_screenshot_commands
from bid_cli.task_board import register as register_task_board_commands

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
app.add_typer(provider_app, name="provider")
app.add_typer(export_app, name="export")
app.add_typer(sandbox_app, name="sandbox")
app.add_typer(check_app, name="check")
app.add_typer(assessment_app, name="assessment")
app.add_typer(review_app, name="review")
app.add_typer(memory_app, name="memory")
app.add_typer(agent_app, name="agent")
app.add_typer(score_app, name="score")
task_app, tender_app, req_app, job_app, token_app = (typer.Typer() for _ in range(5))
source_app = typer.Typer()
resource_app, product_app, task_resource_app = (typer.Typer() for _ in range(3))
feature_app, task_feature_app = typer.Typer(), typer.Typer()
certificate_app, task_certificate_app = typer.Typer(), typer.Typer()
profile_app, task_profile_app = typer.Typer(), typer.Typer()
template_app, task_template_app = typer.Typer(), typer.Typer()
card_app = typer.Typer()
draft_app = typer.Typer(invoke_without_command=True)
redaction_app = typer.Typer()
for name, group in (
    ("task", task_app),
    ("tender", tender_app),
    ("req", req_app),
    ("source", source_app),
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
output_contract_version = "5.0"
started = 0.0


evidence_app, evidence_source_app = typer.Typer(), typer.Typer()
app.add_typer(evidence_app, name="evidence")
evidence_app.add_typer(evidence_source_app, name="source")


@app.callback()
def configure(
    mode: Annotated[str, typer.Option()] = "remote",
    server: Annotated[str, typer.Option()] = "http://127.0.0.1:8000",
    contract_version: Annotated[str, typer.Option(help="5.0")] = "5.0",
):
    global runtime, output_contract_version
    output_contract_version = contract_version
    if contract_version not in {"5.0"}:
        raise ServiceError("invalid_input", "Only contract 5.0 is supported", 400, 2)
    runtime = Client(mode, server)
    runtime.contract_version = contract_version


def client() -> Client:
    if runtime is None:
        raise ServiceError("configuration_required", "Client is not configured", 400, 2)
    return runtime


def emit(body: dict, command: str, as_json: bool, exit_code: int = 0):
    body = dict(body)
    body["command"] = command
    body["duration_ms"] = int((time.monotonic() - started) * 1000)
    if command in ATTACHMENT_COMMANDS and "error" in body.get("data", {}):
        from app.schemas.attachment_contracts import AttachmentErrorData

        error = body["data"]["error"]
        body["data"] = AttachmentErrorData(code=error["code"], message=error["message"]).model_dump(
            mode="json", exclude_none=True
        )
    if command.startswith("agent ") and "error" in body.get("data", {}):
        from app.schemas.agent_contracts import AgentErrorData, AgentFailureData

        error = body["data"]["error"]
        body["data"] = AgentFailureData(
            error=AgentErrorData(
                code=error["code"], message=error["message"], retryable=exit_code == 3
            ),
            session_id=body["data"].get("session_id"),
            job_id=body["data"].get("job_id"),
        ).model_dump(mode="json")
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
        typer.echo(value.data.get("error", value.data).get("message", "Request failed"), err=True)
    if exit_code:
        raise SystemExit(exit_code)


def call(method: str, path: str, **kwargs) -> dict:
    return asyncio.run(client().request(method, path, **kwargs))


@task_app.command("create")
def task_create(
    name: Annotated[str, typer.Option()],
    tender: Annotated[Path | None, typer.Option()] = None,
    tender_number: Annotated[str | None, typer.Option()] = None,
    deadline: Annotated[datetime | None, typer.Option(formats=["%Y-%m-%dT%H:%M:%S%z"])] = None,
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


simulation_app = typer.Typer()
app.add_typer(simulation_app, name="product")


@simulation_app.command("simulate")
def product_simulate(
    task: Annotated[UUID, typer.Option()],
    job: Annotated[UUID, typer.Option()],
    expected_input_hash: Annotated[str | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option()] = False,
    retry: Annotated[bool, typer.Option()] = False,
    wait: Annotated[bool, typer.Option()] = False,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: JsonOption = False,
):
    from app.schemas.simulation_contracts import ProductSimulationInput

    request = ProductSimulationInput(
        extraction_job_id=job, expected_input_hash=expected_input_hash, dry_run=dry_run, retry=retry
    )
    body = call("POST", f"/tasks/{task}/product-simulations", json=request.model_dump(mode="json"))
    if wait and not dry_run:
        terminal = asyncio.run(wait_for_job(UUID(body["data"]["job_id"]), timeout))
        body = merge_job_result(body, terminal)
    emit(body, "product simulate", json_output, partial_completion_exit(body))


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


def certificate_media_type(path: Path) -> str:
    return CERTIFICATE_EXTENSIONS.get(path.suffix.lower(), "application/octet-stream")


@certificate_file_app.command("add")
def certificate_file_add(
    id: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    file: Annotated[
        list[Path],
        typer.Option(help="PDF, PNG or JPEG; repeat to compose one original in this order"),
    ],
    json_output: JsonOption = False,
):
    body = input_contract(input, CertificateFileCreate)
    files = [
        ("file", (path.name, read_certificate_file(path), certificate_media_type(path)))
        for path in file
    ]
    emit(
        call(
            "POST",
            f"/resources/certificates/{id}/file-revisions",
            data={"metadata": json.dumps(body)},
            files=files,
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
    if data.get("status") in {"failed", "cancelled"}:
        body["ok"] = False
        error = data.get("error") or result.get("error") or {}
        return error.get("exit_code", 4)
    if not body.get("ok", True):
        return (data.get("error") or {}).get("exit_code", 4)
    return 0


def merge_job_result(body: dict, terminal: dict) -> dict:
    """Keep terminal usage and domain items when waiting."""
    result = terminal["data"].get("result") or {}
    body["data"].update(result)
    body["data"]["status"] = terminal["data"]["status"]
    if terminal["data"].get("error"):
        body["data"]["error"] = terminal["data"]["error"]
    body["ok"] = terminal.get("ok", True)
    body["cost"] = terminal.get("cost", result.get("cost", body.get("cost", {})))
    body["warnings"] = terminal.get("warnings", result.get("warnings", []))
    body["items"] = terminal.get("items", result.get("items", []))
    return body


@card_app.command("generate")
def card_generate(
    task: Annotated[UUID, typer.Option()],
    job: Annotated[UUID, typer.Option()],
    requirement: Annotated[list[UUID] | None, typer.Option("--requirement")] = None,
    reasoning: Annotated[
        str | None, typer.Option(help="One of the model's official reasoning levels")
    ] = None,
    model: Annotated[
        UUID | None, typer.Option(help="A saved model's slot; omitted means the default model")
    ] = None,
    expect_input_hash: Annotated[
        str | None, typer.Option(help="input_hash from the dry run this run must match")
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
        model=model,
        dry_run=dry_run,
        retry=retry,
        expected_input_hash=expect_input_hash,
    )
    payload = request.model_dump(mode="json")
    if model is None:
        del payload["model"]
    body = call("POST", f"/tasks/{task}/cards/generations", json=payload)
    if wait and not dry_run:
        terminal = asyncio.run(wait_for_job(UUID(body["data"]["job_id"]), timeout))
        body = merge_job_result(body, terminal)
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
        if partial_completion_exit(terminal):
            emit(
                merge_job_result(body, terminal),
                "draft",
                json_output,
                partial_completion_exit(terminal),
            )
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
    model: UUID | None = None,
):
    command = "tender parse" if kind == "parse" else "req extract"
    request: dict = {"dry_run": dry_run, "retry": retry}
    if reasoning is not None:
        request["reasoning"] = reasoning
    if model is not None:
        request["model"] = str(model)
    body = call("POST", f"/documents/{document}/{kind}", json=request)
    if wait and not dry_run:
        terminal = asyncio.run(wait_for_job(UUID(body["data"]["job_id"]), timeout))
        body = merge_job_result(body, terminal)
        if partial_completion_exit(body):
            emit(body, command, as_json, partial_completion_exit(body))
        if kind == "parse":
            body["items"] = call("GET", f"/documents/{document}/chunks")["items"]
        else:
            # The requirements of this extraction, not the task's latest of every document.
            task = call("GET", f"/documents/{document}")["data"]["task_id"]
            job = body["data"]["job_id"]
            body["items"] = call("GET", f"/tasks/{task}/requirements", params={"job": job})["items"]
    emit(body, command, as_json, partial_completion_exit(body))


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
    model: Annotated[
        UUID | None, typer.Option(help="A saved model's slot; omitted means the default model")
    ] = None,
    json_output: JsonOption = False,
):
    process(document, "extract", wait, dry_run, timeout, json_output, retry, reasoning, model)


@req_app.command("list")
def req_list(
    task: Annotated[UUID, typer.Option()],
    job: Annotated[UUID | None, typer.Option(help="Show this extraction instead")] = None,
    json_output: JsonOption = False,
):
    params = {"job": str(job)} if job else None
    emit(call("GET", f"/tasks/{task}/requirements", params=params), "req list", json_output)


@source_app.command("search")
def source_search(
    document: Annotated[UUID, typer.Option()],
    query: Annotated[str, typer.Option(help="Terms that must all appear in one line")],
    limit: Annotated[int, typer.Option(min=1, max=50)] = 50,
    json_output: JsonOption = False,
):
    params = {"query": query, "limit": limit}
    response = call("GET", f"/documents/{document}/source/search", params=params)
    emit(response, "source search", json_output)


@source_app.command("read")
def source_read(
    chunk: Annotated[UUID, typer.Option()],
    block: Annotated[str | None, typer.Option(help="One Word block of the chunk")] = None,
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=8000)] = 8000,
    json_output: JsonOption = False,
):
    params: dict = {"offset": offset, "limit": limit}
    if block is not None:
        params["block"] = block
    emit(call("GET", f"/chunks/{chunk}/source", params=params), "source read", json_output)


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


def explicit_job_id(job_id: UUID | None, selected_id: UUID | None) -> UUID:
    if (job_id is None) == (selected_id is None):
        raise ServiceError(
            "invalid_input", "Provide one job UUID, positionally or with --id", 400, 2
        )
    if job_id is not None:
        return job_id
    if selected_id is None:
        raise ServiceError("invalid_input", "A job UUID is required", 400, 2)
    return selected_id


@job_app.command("status")
def job_status(
    job_id: Annotated[UUID | None, typer.Argument()] = None,
    id: Annotated[UUID | None, typer.Option("--id")] = None,
    json_output: JsonOption = False,
):
    body = call("GET", f"/jobs/{explicit_job_id(job_id, id)}")
    emit(
        body,
        "job status",
        json_output,
        sandbox_job_exit(body)
        or check_job_exit(body)
        or rubric_job_exit(body)
        or score_job_exit(body)
        or memory_job_exit(body)
        or partial_completion_exit(body),
    )


@job_app.command("wait")
def job_wait(
    job_id: Annotated[UUID | None, typer.Argument()] = None,
    id: Annotated[UUID | None, typer.Option("--id")] = None,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: JsonOption = False,
):
    body = asyncio.run(wait_for_job(explicit_job_id(job_id, id), timeout))
    emit(body, "job wait", json_output, partial_completion_exit(body))


@job_app.command("cancel")
def job_cancel(
    job_id: Annotated[UUID | None, typer.Argument()] = None,
    id: Annotated[UUID | None, typer.Option("--id")] = None,
    json_output: JsonOption = False,
):
    emit(call("POST", f"/jobs/{explicit_job_id(job_id, id)}/cancel"), "job cancel", json_output)


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
    body = call(
        "POST",
        "/tokens",
        json={"name": name, "scopes": scope, "expires_at": expires_at.isoformat()},
    )
    secret = body["data"].pop("token")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(secret)
    body["data"]["token_file"] = str(output)
    emit(body, "token create", json_output)


platform_app = typer.Typer()
app.add_typer(platform_app, name="platform")
platform_app.add_typer(platform_clef_app, name="clef")
platform_app.add_typer(platform_credential_app, name="credential")
platform_app.add_typer(platform_trust_anchor_app, name="trust-anchor")


register_task_board_commands(task_app)
register_member_commands(app, task_app)
register_package_commands(app)
register_management_product_commands(product_app)
register_management_feature_commands(feature_app)
register_management_template_commands(template_app)
register_management_binding_commands()
register_management_certificate_commands(certificate_app)
register_management_profile_commands(profile_app)
register_management_memory_commands(memory_app)
register_management_provider_commands(provider_app)
register_attachment_commands(resource_app, profile_app, task_app, evidence_app)
register_requirement_confirmation_commands(req_app)
register_annotation_commands(evidence_app)


@app.command("schema")
def schema_command(json_output: JsonOption = False):
    emit(
        Result(
            ok=True, command="schema", data=command_schema(app, client().contract_version)
        ).model_dump(mode="json"),
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
    global started, runtime, output_contract_version
    runtime = None
    output_contract_version = "5.0"
    started = time.monotonic()
    arguments = list(sys.argv[1:] if args is None else args)
    try:
        app(args=arguments, standalone_mode=False)
    except ServiceError as exc:
        data: dict = {
            "error": {"code": exc.code, "message": exc.message, "exit_code": exc.exit_code}
        }
        if exc.job_id is not None:
            data["job_id"] = exc.job_id
        emit(
            Result(
                ok=False,
                command=command_name(arguments),
                data=data,
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
    except Exception as exc:
        # Only the exception type is reported: its text can carry local data.
        emit(
            Result(
                ok=False,
                command=command_name(arguments),
                data={
                    "error": {
                        "code": "internal_error",
                        "message": f"Unexpected CLI error ({type(exc).__name__})",
                        "exit_code": 4,
                    }
                },
            ).model_dump(mode="json"),
            command_name(arguments),
            "--json" in arguments,
            4,
        )


register_screenshot_commands(app)
register_confidential_commands(app)


if __name__ == "__main__":
    main()
