from app.schemas.certificate_contracts import (
    CertificateCreate,
    CertificateUpdate,
    TaskCertificateSelection,
)
from app.schemas.certificate_file_contracts import CertificateFileCreate
from app.schemas.check_contracts import (
    AssessmentJobAccepted,
    AssessmentListData,
    CheckJobResult,
    CheckPreview,
    CheckReportData,
    CheckRequest,
    FindingDecisionData,
    FindingDecisionRequest,
)
from app.schemas.citation_repair_contracts import CitationRepairRequest
from app.schemas.confidential_contracts import (
    ConfidentialFieldCreate,
    ConfidentialFieldUpdate,
    ConfidentialValueSet,
)
from app.schemas.contracts import (
    CONTRACT_VERSION,
    JobAction,
    Login,
    Result,
    TaskCreate,
    TokenCreate,
)
from app.schemas.evidence_source_contracts import EvidenceSourceCreate
from app.schemas.export_contracts import (
    ExportBindingCreate,
    ExportBindingPreview,
    ExportBindingView,
    ExportDownloadResult,
    ExportPrepare,
    ExportPreview,
    ExportProvenance,
    ExportRelease,
    ExportRunView,
    ExportView,
    TemplateSample,
)
from app.schemas.feature_contracts import FeatureCreate, FeatureUpdate, TaskFeatureSelection
from app.schemas.platform_contracts import (
    CardRedeem,
    OrgLookup,
    PasswordSetup,
    PlatformBalanceAdjust,
    PlatformCardCreate,
    PlatformLogin,
    PlatformModelSet,
    PlatformOrgActive,
    PlatformOrgCreate,
)
from app.schemas.profile_contracts import (
    OrgProfileCreate,
    OrgProfileUpdate,
    TaskOrgProfileSelection,
)
from app.schemas.provider_contracts import ProviderConfigInput, ProviderTest
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
from app.schemas.sandbox_contracts import PrototypeSpec, VendorSpec
from app.schemas.score_contracts import (
    RubricClassificationView,
    RubricClassifyRequest,
    RubricCoverageDecisionRequest,
    RubricCoverageDecisionView,
    RubricDecisionView,
    RubricGenerateRequest,
    RubricGenerateResult,
    RubricItemDecisionRequest,
    RubricPreview,
    RubricReportData,
    RubricReviseRequest,
    RubricSectionDecisionRequest,
    RubricSetDecisionRequest,
    RubricSetView,
)
from app.schemas.screenshot_contracts import (
    PrototypeDecisionBatch,
    PrototypeDecisionPreviewInput,
    PrototypeGenerateInput,
    ScreenshotAnalyzeInput,
    ScreenshotAnnotate,
    ScreenshotIngest,
    ScreenshotPrepareInput,
    ScreenshotWithdraw,
    VendorSearchAdopt,
    VendorSearchInput,
)
from app.schemas.template_contracts import TaskTemplateSelection, TemplateCreate, TemplateUpdate
from pydantic import TypeAdapter

# Only implemented commands are advertised; future commands are deliberately absent.
COMMANDS = {
    "provider list": None,
    "provider history": None,
    "provider set": ProviderConfigInput,
    "provider test": ProviderTest,
    "sandbox render": PrototypeSpec,
    "sandbox capture": VendorSpec,
    "sandbox list": None,
    "sandbox show": None,
    "sandbox download": None,
    "card list": None,
    "card show": None,
    "card create": CardCreate,
    "card generate": CardGenerateRequest,
    "card update": CardUpdate,
    "card classify": CardClassify,
    "card disposition": DispositionBatch,
    "card submit": CardAction,
    "card withdraw": CardAction,
    "card confirm": CardAction,
    "card reject": CardAction,
    "card needs-material": CardAction,
    "card reopen": CardAction,
    "task redaction set": TaskRedactionSet,
    "draft": DraftRequest,
    "draft show": None,
    "draft list": None,
    "check run": CheckRequest,
    "check list": None,
    "check show": None,
    "check decide": FindingDecisionRequest,
    "check history": None,
    "score rubric generate": RubricGenerateRequest,
    "score rubric list": None,
    "score rubric show": None,
    "score rubric revise": RubricReviseRequest,
    "score rubric classify": RubricClassifyRequest,
    "score rubric section decide": RubricSectionDecisionRequest,
    "score rubric item decide": RubricItemDecisionRequest,
    "score rubric coverage decide": RubricCoverageDecisionRequest,
    "score rubric decide": RubricSetDecisionRequest,
    "score rubric history": None,
    "evidence source add": EvidenceSourceCreate,
    "evidence source list": None,
    "evidence source download": None,
    "export binding create": ExportBindingCreate,
    "export binding list": None,
    "export prepare": ExportPrepare,
    "export run show": None,
    "export release": ExportRelease,
    "export list": None,
    "export show": None,
    "export download": None,
    "export provenance": None,
    "export template-sample": None,
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
    "req history": None,
    "req repair-citations": CitationRepairRequest,
    "job status": None,
    "job wait": None,
    "job cancel": None,
    "token create": TokenCreate,
    "platform login": PlatformLogin,
    "platform org list": None,
    "platform org create": PlatformOrgCreate,
    "platform org set-active": PlatformOrgActive,
    "platform model list": None,
    "platform model set": PlatformModelSet,
    "platform model test": None,
    "platform usage": None,
    "platform audit": None,
    "auth setup-password": PasswordSetup,
    "auth orgs": OrgLookup,
    "platform org balance": PlatformBalanceAdjust,
    "platform card create": PlatformCardCreate,
    "platform card list": None,
    "platform card void": None,
    "billing balance": None,
    "billing redeem": CardRedeem,
    "schema": None,
    "screenshot prepare": ScreenshotPrepareInput,
    "screenshot add": ScreenshotIngest,
    "screenshot list": None,
    "screenshot show": None,
    "screenshot annotate": ScreenshotAnnotate,
    "screenshot preview": None,
    "screenshot withdraw": ScreenshotWithdraw,
    "screenshot analyze": ScreenshotAnalyzeInput,
    "ui mock": PrototypeGenerateInput,
    "evidence search": VendorSearchInput,
    "evidence candidates": None,
    "evidence adopt": VendorSearchAdopt,
    "screenshot suggestions": None,
    "screenshot prototype-decisions preview": PrototypeDecisionPreviewInput,
    "screenshot prototype-decisions apply": PrototypeDecisionBatch,
    "screenshot prototype-decisions list": None,
    "confidential field add": ConfidentialFieldCreate,
    "confidential field list": None,
    "confidential field update": ConfidentialFieldUpdate,
    "confidential set": ConfidentialValueSet,
    "confidential list": None,
    "confidential history": None,
}

EXPORT_OUTPUTS = {
    "export binding create": TypeAdapter(ExportBindingPreview | ExportBindingView),
    "export binding list": TypeAdapter(ExportBindingView),
    "export prepare": TypeAdapter(ExportPreview | ExportRunView),
    "export run show": TypeAdapter(ExportRunView),
    "export release": TypeAdapter(ExportView),
    "export list": TypeAdapter(ExportView),
    "export show": TypeAdapter(ExportView),
    "export download": TypeAdapter(ExportDownloadResult),
    "export provenance": TypeAdapter(ExportProvenance),
    "export template-sample": TypeAdapter(TemplateSample),
}

CHECK_OUTPUTS = {
    "check run": TypeAdapter(CheckPreview | AssessmentJobAccepted | CheckJobResult),
    "check list": TypeAdapter(AssessmentListData),
    "check show": TypeAdapter(CheckReportData),
    "check decide": TypeAdapter(FindingDecisionData),
    "check history": TypeAdapter(AssessmentListData),
}
SCORE_OUTPUTS = {
    "score rubric generate": TypeAdapter(
        RubricPreview | AssessmentJobAccepted | RubricGenerateResult
    ),
    "score rubric list": TypeAdapter(AssessmentListData),
    "score rubric show": TypeAdapter(RubricReportData),
    "score rubric revise": TypeAdapter(RubricReportData),
    "score rubric classify": TypeAdapter(RubricClassificationView),
    "score rubric section decide": TypeAdapter(RubricDecisionView),
    "score rubric item decide": TypeAdapter(RubricDecisionView),
    "score rubric coverage decide": TypeAdapter(RubricCoverageDecisionView),
    "score rubric decide": TypeAdapter(RubricSetView),
    "score rubric history": TypeAdapter(AssessmentListData),
}
OUTPUTS = EXPORT_OUTPUTS | CHECK_OUTPUTS | SCORE_OUTPUTS


def command_schema(app=None) -> dict:
    parameters = {}
    if app is not None:
        import click
        from typer.main import get_command

        def visit(command, prefix=""):
            if isinstance(command, click.Group):
                if prefix and command.invoke_without_command:
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
                **({"output": OUTPUTS[name].json_schema()} if name in OUTPUTS else {}),
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
