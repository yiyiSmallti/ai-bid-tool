from app.schemas.certificate_contracts import (
    CertificateCreate,
    CertificateUpdate,
    TaskCertificateSelection,
)
from app.schemas.certificate_file_contracts import CertificateFileCreate
from app.schemas.contracts import (
    CONTRACT_VERSION,
    JobAction,
    Login,
    Result,
    TaskCreate,
    TokenCreate,
)
from app.schemas.evidence_source_contracts import EvidenceSourceCreate
from app.schemas.feature_contracts import FeatureCreate, FeatureUpdate, TaskFeatureSelection
from app.schemas.profile_contracts import (
    OrgProfileCreate,
    OrgProfileUpdate,
    TaskOrgProfileSelection,
)
from app.schemas.resource_contracts import ProductCreate, ProductUpdate, TaskProductSelection
from app.schemas.template_contracts import TaskTemplateSelection, TemplateCreate, TemplateUpdate

# Only implemented commands are advertised; future commands are deliberately absent.
COMMANDS = {
    "evidence source add": EvidenceSourceCreate,
    "evidence source list": None,
    "evidence source download": None,
    "login": Login,
    "org use": None,
    "task create": TaskCreate,
    "task list": None,
    "resource product add": ProductCreate,
    "resource product list": None,
    "resource product update": ProductUpdate,
    "task resource add": TaskProductSelection,
    "task resource list": None,
    "resource feature add": FeatureCreate,
    "resource feature list": None,
    "resource feature update": FeatureUpdate,
    "task feature add": TaskFeatureSelection,
    "task feature list": None,
    "resource certificate add": CertificateCreate,
    "resource certificate list": None,
    "resource certificate update": CertificateUpdate,
    "task certificate add": TaskCertificateSelection,
    "task certificate list": None,
    "resource profile add": OrgProfileCreate,
    "resource profile list": None,
    "resource profile update": OrgProfileUpdate,
    "task profile add": TaskOrgProfileSelection,
    "task profile list": None,
    "resource certificate file add": CertificateFileCreate,
    "resource certificate file list": None,
    "task certificate file list": None,
    "resource certificate file download": None,
    "resource template add": TemplateCreate,
    "resource template list": None,
    "resource template update": TemplateUpdate,
    "task template add": TaskTemplateSelection,
    "task template list": None,
    "resource template download": None,
    "tender upload": None,
    "tender parse": JobAction,
    "req extract": JobAction,
    "req list": None,
    "job status": None,
    "job wait": None,
    "job cancel": None,
    "token create": TokenCreate,
    "schema": None,
}


def command_schema(app=None) -> dict:
    parameters = {}
    if app is not None:
        import click
        from typer.main import get_command

        def visit(command, prefix=""):
            if isinstance(command, click.Group):
                for name, child in command.commands.items():
                    visit(child, (prefix + " " + name).strip())
            else:
                parameters[prefix] = [
                    {
                        "name": param.name,
                        "options": param.opts,
                        "required": param.required,
                        "type": param.type.name,
                        "multiple": param.multiple,
                        "default": str(param.default) if param.default is not None else None,
                    }
                    for param in command.params
                ]

        root = get_command(app)
        visit(root)
        parameters["global"] = [
            {
                "name": param.name,
                "options": param.opts,
                "required": param.required,
                "type": param.type.name,
            }
            for param in root.params
        ]
    return {
        "version": CONTRACT_VERSION,
        "result": Result.model_json_schema(),
        "commands": {
            name: {
                "input": model.model_json_schema() if model else None,
                "cli_parameters": parameters.get(name, []),
            }
            for name, model in COMMANDS.items()
        },
        "global_parameters": parameters.get("global", []),
        "exit_codes": {
            "0": "success",
            "2": "invalid_input",
            "3": "retryable",
            "4": "fatal",
            "5": "partial",
        },
    }
