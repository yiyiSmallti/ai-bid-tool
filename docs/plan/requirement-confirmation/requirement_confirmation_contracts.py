"""Approved B02 contract exports from the implemented runtime schema.

The runtime schema is the single definition of strict payloads and protocols.
Importing this reference registers no routes, jobs or database objects.
"""

from app.schemas.requirement_confirmation import (
    RESULT_CONTRACT_VERSION as RESULT_CONTRACT_VERSION,
)
from app.schemas.requirement_confirmation import (
    CitationFailure as CitationFailure,
)
from app.schemas.requirement_confirmation import (
    ConfirmationBatchData as ConfirmationBatchData,
)
from app.schemas.requirement_confirmation import (
    ConfirmationReceiptItem as ConfirmationReceiptItem,
)
from app.schemas.requirement_confirmation import (
    ConfirmationTarget as ConfirmationTarget,
)
from app.schemas.requirement_confirmation import (
    DecisionAction as DecisionAction,
)
from app.schemas.requirement_confirmation import (
    EntryOrigin as EntryOrigin,
)
from app.schemas.requirement_confirmation import (
    ManualEntryCreate as ManualEntryCreate,
)
from app.schemas.requirement_confirmation import (
    ManualEntryData as ManualEntryData,
)
from app.schemas.requirement_confirmation import (
    ManualEntryPreview as ManualEntryPreview,
)
from app.schemas.requirement_confirmation import (
    ManualRequirementInput as ManualRequirementInput,
)
from app.schemas.requirement_confirmation import (
    Nonblank as Nonblank,
)
from app.schemas.requirement_confirmation import (
    PageData as PageData,
)
from app.schemas.requirement_confirmation import (
    PageQuery as PageQuery,
)
from app.schemas.requirement_confirmation import (
    RejectedItemRef as RejectedItemRef,
)
from app.schemas.requirement_confirmation import (
    RejectedItemView as RejectedItemView,
)
from app.schemas.requirement_confirmation import (
    RequirementActivityView as RequirementActivityView,
)
from app.schemas.requirement_confirmation import (
    RequirementActorHint as RequirementActorHint,
)
from app.schemas.requirement_confirmation import (
    RequirementBoardCounts as RequirementBoardCounts,
)
from app.schemas.requirement_confirmation import (
    RequirementBoardData as RequirementBoardData,
)
from app.schemas.requirement_confirmation import (
    RequirementBoardItem as RequirementBoardItem,
)
from app.schemas.requirement_confirmation import (
    RequirementBoardOverlay as RequirementBoardOverlay,
)
from app.schemas.requirement_confirmation import (
    RequirementBoardQuery as RequirementBoardQuery,
)
from app.schemas.requirement_confirmation import (
    RequirementCitationVerifier as RequirementCitationVerifier,
)
from app.schemas.requirement_confirmation import (
    RequirementConfirmBatch as RequirementConfirmBatch,
)
from app.schemas.requirement_confirmation import (
    RequirementConsumptionEntry as RequirementConsumptionEntry,
)
from app.schemas.requirement_confirmation import (
    RequirementConsumptionManifest as RequirementConsumptionManifest,
)
from app.schemas.requirement_confirmation import (
    RequirementContent as RequirementContent,
)
from app.schemas.requirement_confirmation import (
    RequirementDecision as RequirementDecision,
)
from app.schemas.requirement_confirmation import (
    RequirementProgressData as RequirementProgressData,
)
from app.schemas.requirement_confirmation import (
    RequirementResult as RequirementResult,
)
from app.schemas.requirement_confirmation import (
    RequirementReviewData as RequirementReviewData,
)
from app.schemas.requirement_confirmation import (
    RequirementReviewEvent as RequirementReviewEvent,
)
from app.schemas.requirement_confirmation import (
    RequirementReviewService as RequirementReviewService,
)
from app.schemas.requirement_confirmation import (
    RequirementReviewView as RequirementReviewView,
)
from app.schemas.requirement_confirmation import (
    RequirementSetView as RequirementSetView,
)
from app.schemas.requirement_confirmation import (
    ReviewBucket as ReviewBucket,
)
from app.schemas.requirement_confirmation import (
    ReviewNextAction as ReviewNextAction,
)
from app.schemas.requirement_confirmation import (
    ReviewPageData as ReviewPageData,
)
from app.schemas.requirement_confirmation import (
    ReviewPageQuery as ReviewPageQuery,
)
from app.schemas.requirement_confirmation import (
    ReviewState as ReviewState,
)
from app.schemas.requirement_confirmation import (
    ReviewSummary as ReviewSummary,
)
from app.schemas.requirement_confirmation import (
    ScopeOrigin as ScopeOrigin,
)
from app.schemas.requirement_confirmation import (
    VerifiedRequirementSource as VerifiedRequirementSource,
)
