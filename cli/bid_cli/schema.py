from app.schemas.agent_contracts import (
    AgentCancelRequest,
    AgentFailureData,
    AgentListRequest,
    AgentMessageRequest,
    AgentMessageView,
    AgentMutationData,
    AgentPageData,
    AgentPreviewData,
    AgentResumeRequest,
    AgentSessionView,
    AgentShowData,
    AgentStartRequest,
    AgentStepView,
    CardGenerateArguments,
    CardShowArguments,
    DraftArguments,
    DraftShowArguments,
    ExtractionArguments,
    JobStatusArguments,
)
from app.schemas.annotation_contracts import (
    COMMAND_PAYLOADS as ANNOTATION_PAYLOADS,
)
from app.schemas.annotation_contracts import (
    AnnotationInput,
    AnnotationReleaseRetry,
)
from app.schemas.attachment_contracts import PageData as AttachmentPageData
from app.schemas.budget_contracts import (
    BudgetPlatformModelTest,
    BudgetProviderTest,
    BudgetTaskCreate,
    LowBalancePolicySet,
    TaskBudgetSet,
)
from app.schemas.certificate_contracts import (
    CertificateCreate,
    CertificateUpdate,
    TaskCertificateSelection,
)
from app.schemas.certificate_file_contracts import CertificateFileCreate
from app.schemas.check_contracts import (
    AssessmentJobAccepted,
    AssessmentListData,
    CheckCertificateView,
    CheckItemView,
    CheckJobResult,
    CheckPreview,
    CheckReportData,
    CheckRequest,
    FindingDecisionData,
    FindingDecisionRequest,
    FindingView,
)
from app.schemas.citation_repair_contracts import CitationRepairRequest
from app.schemas.confidential_contracts import (
    ConfidentialFieldCreate,
    ConfidentialFieldUpdate,
    ConfidentialValueSet,
)
from app.schemas.console_assessments import (
    AssessmentHistoryQuery,
    AssessmentInputsData,
    AssessmentJobPageData,
    AssessmentJobQuery,
    AssessmentJobView,
    CheckPageRequest,
    CheckSummaryData,
    CitationContextData,
    CitationRequest,
    ConsoleRubricSectionView,
    Notice,
    PageData,
    RubricPageRequest,
    RubricReplacementData,
    RubricSummaryData,
    ScorePageRequest,
    ScoreSummaryData,
)
from app.schemas.contracts import (
    JobAction,
    LegacyResult,
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
from app.schemas.memory_contracts import (
    MemoryCallData,
    MemoryCandidateJobRequest,
    MemoryCandidateJobResult,
    MemoryCreate,
    MemoryData,
    MemoryDecision,
    MemoryDelete,
    MemoryDisable,
    MemoryEvalData,
    MemoryEvalDetailData,
    MemoryEvalReview,
    MemoryJobSubmissionData,
    MemoryListRequest,
    MemoryPageData,
    MemoryRetrievalData,
    MemoryRetrievalRequest,
    MemoryUpdate,
)
from app.schemas.org_signup import (
    OrgApplicationApprove,
    OrgApplicationDecision,
    OrgApplicationListQuery,
    OrgApplicationReject,
    OrgApplicationView,
)
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
from app.schemas.platform_credentials import (
    CredentialCreateInput,
    CredentialData,
    CredentialErrorData,
    CredentialImportData,
    CredentialImportItem,
    CredentialImportManifest,
    CredentialListData,
    CredentialListQuery,
    CredentialProbeData,
    CredentialRemove,
    CredentialReplace,
    CredentialSetActive,
    CredentialTest,
    CredentialView,
)
from app.schemas.profile_contracts import (
    OrgProfileCreate,
    OrgProfileUpdate,
    TaskOrgProfileSelection,
)
from app.schemas.provider_contracts import ProviderConfigInput, ProviderTest
from app.schemas.requirement_confirmation import (
    ConfirmationBatchData,
    ConfirmationReceiptItem,
    ManualEntryCreate,
    ManualEntryData,
    ManualEntryPreview,
    ManualRequirementInput,
    RejectedItemView,
    RequirementBoardData,
    RequirementBoardItem,
    RequirementBoardQuery,
    RequirementConfirmBatch,
    RequirementDecision,
    RequirementProgressData,
    RequirementReviewData,
    RequirementReviewEvent,
    RequirementReviewView,
    ReviewPageData,
    ReviewPageQuery,
)
from app.schemas.requirement_confirmation import PageData as RequirementPageData
from app.schemas.requirement_confirmation import PageQuery as RequirementPageQuery
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
    RubricItemView,
    RubricPreview,
    RubricReportData,
    RubricRequirementCoverageView,
    RubricReviseRequest,
    RubricSectionDecisionRequest,
    RubricSetDecisionRequest,
    RubricSetView,
    ScoreItemView,
    ScoreJobResult,
    ScorePreview,
    ScoreReportData,
    ScoreRequest,
    ScoreSectionSummary,
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
from app.schemas.simulation_contracts import ProductSimulationInput
from app.schemas.team_workflow import (
    AssignmentData,
    BoardActivityView,
    BoardData,
    BoardJobView,
    BoardQuery,
    BoardRow,
    CommentData,
    CommentMessageView,
    CommentReplyCreate,
    CommentThreadCreate,
    CommentThreadView,
    CoSignData,
    CoSignOpen,
    CoSignPolicyData,
    CoSignSignatureView,
    CoSignSignRequest,
    EventReplayData,
    EventReplayQuery,
    MemberCandidateView,
    PageQuery,
    RequirementAssignmentSet,
    RequirementCoSignPolicySet,
    ReviewRoundData,
    SignoffsData,
    TaskEventView,
    TaskMemberData,
    TaskMemberSet,
    TaskMemberView,
    TaskOwnerHandover,
    TaskProgressData,
    TaskProgressQuery,
    TaskRuleData,
    TaskRuleSet,
    TaskWorkflowData,
    ThreadCreatedData,
    WorkflowMutation,
)
from app.schemas.team_workflow import PageData as WorkflowPageData
from app.schemas.template_contracts import TaskTemplateSelection, TemplateCreate, TemplateUpdate
from pydantic import TypeAdapter

from bid_cli.attachments import COMMAND_DATA as ATTACHMENT_DATA
from bid_cli.attachments import COMMAND_INPUTS as ATTACHMENT_INPUTS
from bid_cli.attachments import COMMAND_ITEMS as ATTACHMENT_ITEMS
from bid_cli.bid_review import COMMAND_DATA as REVIEW_DATA
from bid_cli.bid_review import COMMAND_INPUTS as REVIEW_INPUTS
from bid_cli.bid_review import COMMAND_ITEMS as REVIEW_ITEMS
from bid_cli.management_bindings import COMMAND_DATA as BINDING_COMMAND_DATA
from bid_cli.management_bindings import COMMAND_INPUTS as BINDING_COMMAND_INPUTS
from bid_cli.management_bindings import COMMAND_ITEMS as BINDING_COMMAND_ITEMS
from bid_cli.management_certificates import COMMAND_DATA as CERTIFICATE_COMMAND_DATA
from bid_cli.management_certificates import COMMAND_INPUTS as CERTIFICATE_COMMAND_INPUTS
from bid_cli.management_certificates import COMMAND_ITEMS as CERTIFICATE_COMMAND_ITEMS
from bid_cli.management_confidential import COMMAND_DATA as CONFIDENTIAL_COMMAND_DATA
from bid_cli.management_confidential import COMMAND_INPUTS as CONFIDENTIAL_COMMAND_INPUTS
from bid_cli.management_confidential import COMMAND_ITEMS as CONFIDENTIAL_COMMAND_ITEMS
from bid_cli.management_features import COMMAND_DATA as FEATURE_COMMAND_DATA
from bid_cli.management_features import COMMAND_INPUTS as FEATURE_COMMAND_INPUTS
from bid_cli.management_features import COMMAND_ITEMS as FEATURE_COMMAND_ITEMS
from bid_cli.management_memory import COMMAND_INPUTS as MEMORY_COMMAND_INPUTS
from bid_cli.management_memory import COMMAND_ITEMS as MEMORY_COMMAND_ITEMS
from bid_cli.management_products import COMMAND_DATA, COMMAND_INPUTS, COMMAND_ITEMS
from bid_cli.management_profiles import COMMAND_DATA as PROFILE_COMMAND_DATA
from bid_cli.management_profiles import COMMAND_INPUTS as PROFILE_COMMAND_INPUTS
from bid_cli.management_profiles import COMMAND_ITEMS as PROFILE_COMMAND_ITEMS
from bid_cli.management_providers import COMMAND_DATA as PROVIDER_COMMAND_DATA
from bid_cli.management_providers import COMMAND_INPUTS as PROVIDER_COMMAND_INPUTS
from bid_cli.management_providers import COMMAND_ITEMS as PROVIDER_COMMAND_ITEMS
from bid_cli.management_templates import COMMAND_DATA as TEMPLATE_COMMAND_DATA
from bid_cli.management_templates import COMMAND_INPUTS as TEMPLATE_COMMAND_INPUTS
from bid_cli.management_templates import COMMAND_ITEMS as TEMPLATE_COMMAND_ITEMS
from bid_cli.platform_clef import COMMAND_DATA as CLEF_DATA
from bid_cli.platform_clef import COMMAND_INPUTS as CLEF_INPUTS
from bid_cli.platform_trust_anchors import COMMAND_DATA as TRUST_ANCHOR_DATA
from bid_cli.platform_trust_anchors import COMMAND_INPUTS as TRUST_ANCHOR_INPUTS
from bid_cli.platform_trust_anchors import COMMAND_ITEMS as TRUST_ANCHOR_ITEMS
from bid_cli.requirement_confirmation import RequirementProgressInvocation

# Only implemented commands are advertised; future commands are deliberately absent.
COMMANDS = {
    "assessment inputs": None,
    "assessment citation": CitationRequest,
    "assessment jobs": AssessmentJobQuery,
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
    "score run": ScoreRequest,
    "score list": None,
    "score show": None,
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
    "platform operator list": None,
    "platform org application list": OrgApplicationListQuery,
    "platform org application approve": OrgApplicationApprove,
    "platform org application reject": OrgApplicationReject,
    "platform org create": PlatformOrgCreate,
    "platform org set-active": PlatformOrgActive,
    "platform model list": None,
    "platform model set": PlatformModelSet,
    "platform model test": None,
    "platform credential list": CredentialListQuery,
    "platform credential show": None,
    "platform credential create": CredentialCreateInput,
    "platform credential replace": CredentialReplace,
    "platform credential set-active": CredentialSetActive,
    "platform credential remove": CredentialRemove,
    "platform credential test": CredentialTest,
    "platform credential import-env": CredentialImportManifest,
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
    "assessment inputs": TypeAdapter(AssessmentInputsData),
    "assessment citation": TypeAdapter(CitationContextData),
    "assessment jobs": TypeAdapter(AssessmentJobPageData),
    "check run": TypeAdapter(CheckPreview | AssessmentJobAccepted | CheckJobResult),
    "check list": TypeAdapter(AssessmentListData),
    "check show": TypeAdapter(CheckReportData),
    "check decide": TypeAdapter(FindingDecisionData),
    "check history": TypeAdapter(AssessmentListData),
}
SCORE_OUTPUTS = {
    "score run": TypeAdapter(ScorePreview | AssessmentJobAccepted | ScoreJobResult),
    "score list": TypeAdapter(AssessmentListData),
    "score show": TypeAdapter(ScoreReportData),
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
MEMORY_INPUTS = {
    "memory add": MemoryCreate,
    "memory list": MemoryListRequest,
    "memory show": None,
    "memory update": MemoryUpdate,
    "memory history": None,
    "memory approve": MemoryDecision,
    "memory reject": MemoryDecision,
    "memory disable": MemoryDisable,
    "memory delete": MemoryDelete,
    "memory retrieve": MemoryRetrievalRequest,
    "memory retrieval show": None,
    "memory used": None,
    "memory feedback list": None,
    "memory candidates run": MemoryCandidateJobRequest,
    "memory samples list": None,
    "memory samples show": None,
    "memory samples review": MemoryEvalReview,
}
COMMANDS.update(MEMORY_INPUTS)
MEMORY_OUTPUTS: dict[str, TypeAdapter] = {
    name: TypeAdapter(MemoryData)
    for name in (
        "memory add",
        "memory show",
        "memory update",
        "memory approve",
        "memory reject",
        "memory disable",
        "memory delete",
    )
}
MEMORY_OUTPUTS.update(
    {
        name: TypeAdapter(MemoryPageData)
        for name in ("memory list", "memory history", "memory feedback list", "memory samples list")
    }
)
MEMORY_OUTPUTS.update(
    {
        "memory retrieve": TypeAdapter(MemoryRetrievalData),
        "memory retrieval show": TypeAdapter(MemoryRetrievalData),
        "memory used": TypeAdapter(MemoryCallData),
        "memory candidates run": TypeAdapter(MemoryJobSubmissionData | MemoryCandidateJobResult),
        "memory samples show": TypeAdapter(MemoryEvalDetailData),
        "memory samples review": TypeAdapter(MemoryEvalData),
    }
)
CREDENTIAL_OUTPUTS = {
    "platform credential " + action: TypeAdapter(CredentialData | CredentialErrorData)
    for action in ("show", "create", "replace", "set-active", "remove")
}
CREDENTIAL_OUTPUTS.update(
    {
        "platform credential list": TypeAdapter(CredentialListData | CredentialErrorData),
        "platform credential test": TypeAdapter(CredentialProbeData | CredentialErrorData),
        "platform credential import-env": TypeAdapter(CredentialImportData | CredentialErrorData),
    }
)
AGENT_INPUTS = {
    "agent start": AgentStartRequest,
    "agent list": AgentListRequest,
    "agent show": None,
    "agent messages": AgentListRequest,
    "agent steps": AgentListRequest,
    "agent message": AgentMessageRequest,
    "agent resume": AgentResumeRequest,
    "agent cancel": AgentCancelRequest,
}
COMMANDS.update(AGENT_INPUTS)
AGENT_OUTPUTS = {
    "agent start": TypeAdapter(AgentMutationData | AgentPreviewData | AgentFailureData),
    "agent show": TypeAdapter(AgentShowData | AgentFailureData),
    **{
        "agent " + action: TypeAdapter(AgentMutationData | AgentFailureData)
        for action in ("message", "resume", "cancel")
    },
    **{
        "agent " + action: TypeAdapter(AgentPageData | AgentFailureData)
        for action in ("list", "messages", "steps")
    },
}
AGENT_ITEMS = {
    "agent list": AgentSessionView,
    "agent messages": AgentMessageView,
    "agent steps": AgentStepView,
}
INVOCATION_INPUTS = {
    "req list": ExtractionArguments,
    "card list": ExtractionArguments,
    "card show": CardShowArguments,
    "card generate": CardGenerateArguments,
    "draft": DraftArguments,
    "draft show": DraftShowArguments,
    "job status": JobStatusArguments,
}
OUTPUTS = (
    EXPORT_OUTPUTS
    | CHECK_OUTPUTS
    | SCORE_OUTPUTS
    | MEMORY_OUTPUTS
    | CREDENTIAL_OUTPUTS
    | AGENT_OUTPUTS
)


WORKFLOW_COMMANDS = {
    "task workflow": None,
    "task progress": TaskProgressQuery,
    "task member list": PageQuery,
    "task member candidates": PageQuery,
    "task member set": TaskMemberSet,
    "task member remove": WorkflowMutation,
    "task handover": TaskOwnerHandover,
    "task archive": WorkflowMutation,
    "task unarchive": WorkflowMutation,
    "task board": BoardQuery,
    "task activity": PageQuery,
    "task events": EventReplayQuery,
    "card assign": RequirementAssignmentSet,
    "card thread list": PageQuery,
    "card thread create": CommentThreadCreate,
    "card comment list": PageQuery,
    "card comment add": CommentReplyCreate,
    "task review-rule show": None,
    "task review-rule set": TaskRuleSet,
    "card policy show": None,
    "card policy set": RequirementCoSignPolicySet,
    "card signoff list": PageQuery,
    "card review-round open": CoSignOpen,
    "card signoff add": CoSignSignRequest,
}
COMMANDS.update(WORKFLOW_COMMANDS)
OUTPUTS.update(
    {
        "task workflow": TypeAdapter(TaskWorkflowData),
        "task progress": TypeAdapter(TaskProgressData),
        "task member list": TypeAdapter(WorkflowPageData),
        "task member candidates": TypeAdapter(WorkflowPageData),
        "task member set": TypeAdapter(TaskMemberData),
        "task member remove": TypeAdapter(TaskWorkflowData),
        "task handover": TypeAdapter(TaskWorkflowData),
        "task archive": TypeAdapter(TaskWorkflowData),
        "task unarchive": TypeAdapter(TaskWorkflowData),
        "task board": TypeAdapter(BoardData),
        "task activity": TypeAdapter(WorkflowPageData),
        "task events": TypeAdapter(EventReplayData),
        "card assign": TypeAdapter(AssignmentData),
        "card thread list": TypeAdapter(WorkflowPageData),
        "card thread create": TypeAdapter(ThreadCreatedData),
        "card comment list": TypeAdapter(WorkflowPageData),
        "card comment add": TypeAdapter(CommentData),
        "task review-rule show": TypeAdapter(TaskRuleData),
        "task review-rule set": TypeAdapter(TaskRuleData),
        "card policy show": TypeAdapter(CoSignPolicyData),
        "card policy set": TypeAdapter(CoSignPolicyData),
        "card signoff list": TypeAdapter(SignoffsData),
        "card review-round open": TypeAdapter(ReviewRoundData),
        "card signoff add": TypeAdapter(CoSignData),
    }
)


def console_variant(input_model, output_model, items_model=None) -> dict:
    return {
        "input": input_model.model_json_schema() if input_model else None,
        "output": TypeAdapter(output_model).json_schema(),
        "items": TypeAdapter(items_model).json_schema()
        if items_model
        else {"type": "array", "maxItems": 0},
    }


CONSOLE_VARIANTS = {
    "score list": {
        "console": console_variant(AssessmentHistoryQuery, AssessmentListData, ScoreSummaryData)
    },
    "score show": {
        "console": {
            "summary": console_variant(None, ScoreSummaryData),
            **{
                part: console_variant(ScorePageRequest, PageData, model)
                for part, model in {
                    "sections": ScoreSectionSummary,
                    "items": ScoreItemView,
                    "notices": Notice,
                }.items()
            },
        }
    },
    "score rubric list": {
        "console": console_variant(AssessmentHistoryQuery, AssessmentListData, RubricSummaryData)
    },
    "score rubric show": {
        "console": {
            "summary": console_variant(None, RubricSummaryData),
            "replacement": console_variant(None, RubricReplacementData),
            **{
                part: console_variant(RubricPageRequest, PageData, model)
                for part, model in {
                    "sections": ConsoleRubricSectionView,
                    "items": RubricItemView,
                    "coverage": RubricRequirementCoverageView,
                    "blockers": Notice,
                }.items()
            },
        }
    },
    "score rubric revise": {"console": console_variant(RubricReviseRequest, RubricSummaryData)},
    "score rubric decide": {
        "console": console_variant(RubricSetDecisionRequest, RubricSummaryData)
    },
    "check list": {
        "console": console_variant(AssessmentHistoryQuery, AssessmentListData, CheckSummaryData)
    },
    "check show": {
        "console": {
            "summary": console_variant(None, CheckSummaryData),
            **{
                part: console_variant(CheckPageRequest, PageData, model)
                for part, model in {
                    "findings": FindingView,
                    "coverage": CheckItemView,
                    "certificates": CheckCertificateView,
                    "notices": Notice,
                }.items()
            },
        }
    },
}


LEGACY_COMMANDS = dict(COMMANDS)
REQUIREMENT_COMMANDS = {
    "req review-list": ReviewPageQuery,
    "req show": None,
    "req review-history": RequirementPageQuery,
    "req rejected": RequirementPageQuery,
    "req add": ManualEntryCreate,
    "req confirm": RequirementDecision,
    "req reopen": RequirementDecision,
    "req confirm-batch": RequirementConfirmBatch,
}
COMMANDS.update(REQUIREMENT_COMMANDS)
OUTPUTS.update(
    {
        "req review-list": TypeAdapter(ReviewPageData),
        "req show": TypeAdapter(RequirementReviewData),
        "req review-history": TypeAdapter(RequirementPageData),
        "req rejected": TypeAdapter(RequirementPageData),
        "req add": TypeAdapter(ManualEntryData),
        "req confirm": TypeAdapter(RequirementReviewData),
        "req reopen": TypeAdapter(RequirementReviewData),
        "req confirm-batch": TypeAdapter(ConfirmationBatchData),
    }
)
CONSOLE_VARIANTS.update(
    {
        "req add": {"dry_run": console_variant(ManualRequirementInput, ManualEntryPreview)},
        "task board": {
            "requirement-review": console_variant(
                RequirementBoardQuery, RequirementBoardData, RequirementBoardItem
            )
        },
        "task progress": {
            "requirement-review": console_variant(
                RequirementProgressInvocation, RequirementProgressData, BoardJobView
            )
        },
    }
)
COMMANDS.update(
    {
        "task create": BudgetTaskCreate,
        "provider test": BudgetProviderTest,
        "platform model test": BudgetPlatformModelTest,
        "product simulate": ProductSimulationInput,
        "task budget show": None,
        "task budget set": TaskBudgetSet,
        "task budget history": None,
        "billing alert show": None,
        "billing alert set": LowBalancePolicySet,
        "billing notices": None,
    }
)
COMMANDS.update(COMMAND_INPUTS)
COMMANDS.update(FEATURE_COMMAND_INPUTS)
COMMANDS.update(TEMPLATE_COMMAND_INPUTS)
COMMANDS.update(BINDING_COMMAND_INPUTS)
COMMANDS.update(CERTIFICATE_COMMAND_INPUTS)
COMMANDS.update(PROFILE_COMMAND_INPUTS)
COMMANDS.update(MEMORY_COMMAND_INPUTS)
COMMANDS.update(PROVIDER_COMMAND_INPUTS)
COMMANDS.update(CONFIDENTIAL_COMMAND_INPUTS)

COMMANDS.update(ATTACHMENT_INPUTS)
COMMANDS.update(REVIEW_INPUTS)
COMMANDS.update(CLEF_INPUTS)
OUTPUTS.update({name: TypeAdapter(model) for name, model in CLEF_DATA.items()})
COMMANDS.update(TRUST_ANCHOR_INPUTS)
OUTPUTS.update({name: TypeAdapter(model) for name, model in TRUST_ANCHOR_DATA.items()})
OUTPUTS.update({name: TypeAdapter(model) for name, model in ATTACHMENT_DATA.items()})

OUTPUTS.update({name: TypeAdapter(AttachmentPageData) for name in ATTACHMENT_ITEMS})

# Registered commands and discovery share one inventory; the legacy snapshot above
# intentionally excludes this Result 4.0-only slice.

COMMANDS.update({name: None for name in ANNOTATION_PAYLOADS if "--dry-run" not in name})
COMMANDS["evidence stamp"] = AnnotationInput
COMMANDS["evidence annotation release retry"] = AnnotationReleaseRetry


def command_schema(app=None, version: str = "4.0") -> dict:
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
    from app.schemas.budget_contracts import (
        BudgetHistoryData,
        BudgetPlatformModelTest,
        BudgetProviderTest,
        BudgetTaskCreate,
        LowBalanceNoticesData,
        LowBalancePolicyData,
        LowBalancePolicySet,
        TaskBudgetData,
        TaskBudgetSet,
    )
    from app.schemas.compatibility import NEW_COMMANDS

    commands = dict(LEGACY_COMMANDS if version == "3.0" else COMMANDS)
    outputs = dict(OUTPUTS)
    outputs.update(
        {
            "platform org application approve": TypeAdapter(OrgApplicationDecision),
            "platform org application reject": TypeAdapter(OrgApplicationDecision),
        }
    )
    if version == "4.0":
        from app.schemas.management_pages import PageData as ManagementPageData

        outputs.update(
            {
                name: TypeAdapter(model)
                for name, model in (
                    COMMAND_DATA
                    | FEATURE_COMMAND_DATA
                    | TEMPLATE_COMMAND_DATA
                    | BINDING_COMMAND_DATA
                    | CERTIFICATE_COMMAND_DATA
                    | PROFILE_COMMAND_DATA
                    | PROVIDER_COMMAND_DATA
                    | CONFIDENTIAL_COMMAND_DATA
                ).items()
            }
        )
        outputs.update(
            {
                name: TypeAdapter(ManagementPageData)
                for name in (
                    COMMAND_ITEMS
                    | FEATURE_COMMAND_ITEMS
                    | TEMPLATE_COMMAND_ITEMS
                    | BINDING_COMMAND_ITEMS
                    | CERTIFICATE_COMMAND_ITEMS
                    | PROFILE_COMMAND_ITEMS
                    | MEMORY_COMMAND_ITEMS
                    | PROVIDER_COMMAND_ITEMS
                    | CONFIDENTIAL_COMMAND_ITEMS
                )
            }
        )
        commands.update(
            {
                "product simulate": ProductSimulationInput,
                "task create": BudgetTaskCreate,
                "provider test": BudgetProviderTest,
                "platform model test": BudgetPlatformModelTest,
                "task budget show": None,
                "task budget set": TaskBudgetSet,
                "task budget history": None,
                "billing alert show": None,
                "billing alert set": LowBalancePolicySet,
                "billing notices": None,
            }
        )
        outputs.update(
            {
                "task budget show": TypeAdapter(TaskBudgetData),
                "task budget set": TypeAdapter(TaskBudgetData),
                "task budget history": TypeAdapter(BudgetHistoryData),
                "billing alert show": TypeAdapter(LowBalancePolicyData),
                "billing alert set": TypeAdapter(LowBalancePolicyData),
                "billing notices": TypeAdapter(LowBalanceNoticesData),
            }
        )
    else:
        commands = {
            name: model
            for name, model in commands.items()
            if name not in NEW_COMMANDS and name not in AGENT_INPUTS
        }
        commands = {
            name: model for name, model in commands.items() if not name.startswith("assessment ")
        }
        legacy_options = {"budget", "budget_currency", "test_org", "contract_version"}
        console_options = {
            "view",
            "part",
            "extraction_job",
            "severity",
            "domain",
            "status",
            "requirement",
            "entry",
            "section",
            "group",
            "state",
            "section_key",
            "outcome",
        }
        for name, items in parameters.items():
            parameters[name] = [
                item
                for item in items
                if item["name"] not in legacy_options
                and not (name in CONSOLE_VARIANTS and item["name"] in console_options)
                and not (name == "task progress" and item["name"] == "job")
                and not (
                    name in {"check show", "score show", "score rubric show"}
                    and item["name"] in {"cursor", "limit"}
                )
                and not (name == "provider test" and item["name"] == "dry_run")
                and not (name == "platform model test" and item["name"] == "dry_run")
            ]
    schema = {
        "version": version,
        "result": (LegacyResult if version == "3.0" else Result).model_json_schema(),
        "commands": {
            name: {
                "input": TypeAdapter(model).json_schema() if model else None,
                "cli_parameters": parameters.get(name, []),
                **({"output": outputs[name].json_schema()} if name in outputs else {}),
                **(
                    {"invocation_input": INVOCATION_INPUTS[name].model_json_schema()}
                    if version == "4.0" and name in INVOCATION_INPUTS
                    else {}
                ),
                **({"items": AGENT_ITEMS[name].model_json_schema()} if name in AGENT_ITEMS else {}),
                **(
                    {"variants": CONSOLE_VARIANTS[name]}
                    if version == "4.0" and name in CONSOLE_VARIANTS
                    else {}
                ),
                **(
                    {"items": TypeAdapter(AssessmentJobView).json_schema()}
                    if name == "assessment jobs"
                    else {}
                ),
                **(
                    {"items": TypeAdapter(CredentialView).json_schema()}
                    if name == "platform credential list"
                    else {}
                ),
                **(
                    {"items": TypeAdapter(CredentialImportItem).json_schema()}
                    if name == "platform credential import-env"
                    else {}
                ),
            }
            for name, model in commands.items()
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
    workflow_items = {
        "platform org application list": OrgApplicationView,
        "req review-list": RequirementReviewView,
        "req review-history": RequirementReviewEvent,
        "req rejected": RejectedItemView,
        "req confirm-batch": ConfirmationReceiptItem,
        "task board": BoardRow,
        "task progress": BoardJobView,
        "task activity": BoardActivityView,
        "task events": TaskEventView,
        "task member list": TaskMemberView,
        "task member candidates": MemberCandidateView,
        "card thread list": CommentThreadView,
        "card comment list": CommentMessageView,
        "card signoff list": CoSignSignatureView,
    }
    for name, model in workflow_items.items():
        if name in schema["commands"]:
            schema["commands"][name]["items"] = model.model_json_schema()
    for name, model in (
        COMMAND_ITEMS
        | FEATURE_COMMAND_ITEMS
        | CERTIFICATE_COMMAND_ITEMS
        | PROFILE_COMMAND_ITEMS
        | TEMPLATE_COMMAND_ITEMS
        | BINDING_COMMAND_ITEMS
        | ATTACHMENT_ITEMS
        | MEMORY_COMMAND_ITEMS
        | PROVIDER_COMMAND_ITEMS
        | CONFIDENTIAL_COMMAND_ITEMS
    ).items():
        if name in schema["commands"]:
            schema["commands"][name]["items"] = model.model_json_schema()
    if version == "4.0":
        from app.schemas.bid_review import BidPreparePreview, BidSubmissionView, BidUploadPreview

        for name, model in REVIEW_DATA.items():
            schema["commands"][name]["output"] = TypeAdapter(model).json_schema()
        for name, model in REVIEW_ITEMS.items():
            schema["commands"][name]["items"] = TypeAdapter(model).json_schema()
        for name, model in TRUST_ANCHOR_ITEMS.items():
            schema["commands"][name]["items"] = TypeAdapter(model).json_schema()
        schema["commands"]["review upload"]["preflight"] = BidUploadPreview.model_json_schema()
        schema["commands"]["review prepare"]["preflight"] = BidPreparePreview.model_json_schema()
        schema["commands"]["review prepare"]["wait_output"] = BidSubmissionView.model_json_schema()
        from app.schemas.bid_review_run import BidReviewJobResult, BidReviewPreview

        schema["commands"]["review run"]["preflight"] = BidReviewPreview.model_json_schema()
        schema["commands"]["review run"]["wait_output"] = BidReviewJobResult.model_json_schema()
        from app.schemas.annotation_contracts import (
            COMMAND_PAYLOADS,
            AnnotationCandidateView,
            AnnotationPreflight,
            AnnotationPreflightRequest,
            AnnotationSubmit,
        )

        for name, (data_model, item_model) in COMMAND_PAYLOADS.items():
            if name.endswith(" --dry-run"):
                continue
            schema["commands"][name]["output"] = data_model.model_json_schema()
            if item_model:
                schema["commands"][name]["items"] = item_model.model_json_schema()
        from app.schemas.annotation_contracts import ENABLED_SOURCE_KINDS

        schema["commands"]["evidence stamp"]["enabled_source_kinds"] = list(ENABLED_SOURCE_KINDS)
        schema["commands"]["evidence stamp"]["wait_output"] = (
            AnnotationCandidateView.model_json_schema()
        )
        schema["commands"]["evidence stamp"]["preflight"] = AnnotationPreflight.model_json_schema()
        schema["commands"]["evidence stamp"]["http_preflight_input"] = (
            AnnotationPreflightRequest.model_json_schema()
        )
        schema["commands"]["evidence stamp"]["http_submit_input"] = (
            AnnotationSubmit.model_json_schema()
        )
    if version == "4.0":
        from app.schemas.budget_contracts import (
            BudgetPreflightData,
            LowBalanceNoticeView,
            TaskBudgetRevisionView,
        )

        costed = {
            "tender parse",
            "req extract",
            "card generate",
            "draft",
            "check run",
            "score run",
            "score rubric generate",
            "screenshot analyze",
            "screenshot annotate",
            "ui mock",
            "evidence search",
            "product simulate",
            "provider test",
            "platform model test",
            "sandbox render",
            "sandbox capture",
            "export prepare",
        }
        preflight_schema = BudgetPreflightData.model_json_schema()
        schema["$defs"] = preflight_schema.pop("$defs", {})
        schema["$defs"]["BudgetPreflightData"] = preflight_schema
        for name in costed:
            if name in schema["commands"]:
                schema["commands"][name]["budget_preflight"] = {
                    "$ref": "#/$defs/BudgetPreflightData"
                }
        schema["commands"]["task budget history"]["items"] = (
            TaskBudgetRevisionView.model_json_schema()
        )
        schema["commands"]["billing notices"]["items"] = LowBalanceNoticeView.model_json_schema()
    if version == "3.0":
        from app.schemas.contracts import LegacyCost

        schema["result"]["title"] = "Result"
        legacy_cost = LegacyCost.model_json_schema()
        legacy_cost["title"] = "Cost"
        schema["result"]["$defs"] = {"Cost": legacy_cost}
        schema["result"]["properties"]["cost"]["$ref"] = "#/$defs/Cost"

        def legacy_schema(value):
            if isinstance(value, dict):
                if "properties" in value:
                    value["properties"].pop("agent_provenance", None)
                    value["properties"].pop("requirement_review", None)
                if "$defs" in value:
                    value["$defs"].pop("AgentProvenance", None)
                    value["$defs"].pop("RubricRequirementReadiness", None)
                if "$defs" in value and "Cost" in value["$defs"]:
                    value["$defs"]["Cost"] = legacy_cost
                for child in value.values():
                    legacy_schema(child)
            elif isinstance(value, list):
                for child in value:
                    legacy_schema(child)

        legacy_schema(schema)
    return schema
