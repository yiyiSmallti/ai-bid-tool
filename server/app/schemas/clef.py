"""Clef bounded triage and secret-free platform configuration contracts."""

import json
from dataclasses import dataclass
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, StringConstraints, model_validator

from app.schemas.budget_contracts import BudgetCallQuote, Currency, Money
from app.schemas.contracts import Contract, ProviderUsage
from app.schemas.screenshot_contracts import Sha256

CLEF_IMAGE_LIMIT = 4
CLEF_IMAGE_BYTE_LIMIT = 4 * 1024 * 1024
CLEF_IMAGES_BYTE_LIMIT = 8 * 1024 * 1024
CLEF_IMAGE_PIXEL_LIMIT = 16_000_000
CLEF_REQUEST_BYTE_LIMIT = 13 * 1024 * 1024
CLEF_CONTEXT_TOKENS = 65_536
CLEF_VISUAL_CALL_LIMIT = 40
CLEF_CONCURRENCY = 3
CLEF_PLATFORM_MODEL_ID = "bid-review-clef"
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
Revision = Annotated[int, Field(strict=True, ge=1)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class ClefNoulQuestion(Contract):
    type: Literal["noul"] = "noul"
    ref: str = Field(pattern=r"^q[1-9][0-9]{0,2}$")
    question: NonBlank


class ClefChoiceQuestion(Contract):
    type: Literal["choice"] = "choice"
    ref: str = Field(pattern=r"^q[1-9][0-9]{0,2}$")
    question: NonBlank
    choices: list[NonBlank] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def unique_choices(self) -> Self:
        if len(set(self.choices)) != len(self.choices):
            raise ValueError("choice alternatives must be unique")
        return self


class ClefScoreQuestion(Contract):
    type: Literal["score"] = "score"
    ref: str = Field(pattern=r"^q[1-9][0-9]{0,2}$")
    question: NonBlank
    # Ordered criterion labels define the ordinal scale 0 .. len(criteria)-1.
    criteria: list[NonBlank] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def unique_criteria(self) -> Self:
        if len(set(self.criteria)) != len(self.criteria):
            raise ValueError("score criterion labels must be unique and ordered")
        return self


ClefQuestion = Annotated[
    ClefNoulQuestion | ClefChoiceQuestion | ClefScoreQuestion, Field(discriminator="type")
]


class ClefRedactedImage(Contract):
    ref: str = Field(pattern=r"^image[1-4]$")
    sha256: Sha256
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    size_bytes: int = Field(strict=True, gt=0, le=CLEF_IMAGE_BYTE_LIMIT)
    width_px: int = Field(strict=True, ge=1, le=8192)
    height_px: int = Field(strict=True, ge=1, le=8192)
    privacy_receipt_sha256: Sha256
    identifying_qr_redacted: Literal[True] = True
    identity_cards_redacted: Literal[True] = True
    seal_text_redacted_for_presence: Literal[True] = True
    price_page: Literal[False] = False

    @model_validator(mode="after")
    def clef_pixel_bound(self) -> Self:
        if self.width_px * self.height_px > CLEF_IMAGE_PIXEL_LIMIT:
            raise ValueError("Clef image exceeds its 16 MP limit")
        return self


class ClefTriageRequest(Contract):
    """Internal request; adapter emits local refs/state and redacted image bytes only."""

    model: Literal["@cf/cloudflare/clef"] = "@cf/cloudflare/clef"
    purpose: Literal[
        "seal_present",
        "signature_present",
        "date_filled",
        "page_document_type",
        "certificate_or_not",
        "image_supports_claim",
    ]
    state: (
        Annotated[str, Field(min_length=1, max_length=200_000)]
        | dict[str, JsonValue]
        | list[JsonValue]
    )
    questions: list[ClefQuestion] = Field(min_length=1, max_length=64)
    images: list[ClefRedactedImage] = Field(default_factory=list, max_length=CLEF_IMAGE_LIMIT)
    outbound_sha256: Sha256
    text_redaction_sha256: Sha256
    triage_only: Literal[True] = True

    @model_validator(mode="after")
    def unique_refs_and_image_budget(self) -> Self:
        try:
            state_bytes = json.dumps(self.state, ensure_ascii=False, allow_nan=False).encode()
        except (ValueError, RecursionError) as error:
            raise ValueError("triage state must be finite bounded JSON") from error
        if len(state_bytes) > 200_000:
            raise ValueError("triage state exceeds the serialized text budget")
        refs = [question.ref for question in self.questions]
        images = [image.ref for image in self.images]
        if len(set(refs)) != len(refs) or len(set(images)) != len(images):
            raise ValueError("triage refs must be unique")
        if sum(image.size_bytes for image in self.images) > CLEF_IMAGES_BYTE_LIMIT:
            raise ValueError("Clef decoded images exceed the aggregate 8 MiB limit")
        return self


class ClefNoulAnswer(Contract):
    type: Literal["noul"] = "noul"
    ref: str = Field(pattern=r"^q[1-9][0-9]{0,2}$")
    # Normalized internal projection of the vendor's single yes probability.
    probability_yes: Probability


class ClefChoiceAnswer(Contract):
    type: Literal["choice"] = "choice"
    ref: str = Field(pattern=r"^q[1-9][0-9]{0,2}$")
    probabilities: list[Probability] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def normalized_probabilities(self) -> Self:
        if abs(sum(self.probabilities) - 1) > 1e-6:
            raise ValueError("choice probabilities must sum to one")
        return self


class ClefScoreAnswer(Contract):
    type: Literal["score"] = "score"
    ref: str = Field(pattern=r"^q[1-9][0-9]{0,2}$")
    criteria_probabilities: list[Probability] = Field(min_length=2, max_length=20)
    mean: float = Field(ge=0, le=19, allow_inf_nan=False)

    @model_validator(mode="after")
    def probability_weighted_mean(self) -> Self:
        if abs(sum(self.criteria_probabilities) - 1) > 1e-6:
            raise ValueError("score criterion probabilities must sum to one")
        expected = sum(
            index * probability for index, probability in enumerate(self.criteria_probabilities)
        )
        if abs(self.mean - expected) > 1e-6:
            raise ValueError("score mean must derive from ordered criterion probabilities")
        return self


ClefAnswer = Annotated[
    ClefNoulAnswer | ClefChoiceAnswer | ClefScoreAnswer, Field(discriminator="type")
]


class ClefFixedCallQuote(Contract):
    """Fixed catalog charge, settled per dispatched call independent of usage tokens."""

    quote: BudgetCallQuote
    charge_basis: Literal["fixed_per_call"] = "fixed_per_call"
    platform_config_revision: Revision
    # Returned input_tokens remain telemetry and never recalculate this amount.
    returned_tokens_affect_charge: Literal[False] = False

    @model_validator(mode="after")
    def pinned_platform_quote(self) -> Self:
        if (
            self.quote.capability != "vision"
            or self.quote.payer != "org_platform"
            or self.quote.model != "@cf/cloudflare/clef"
            or self.quote.platform_model_id is None
            or self.quote.reserved_task_amount is None
            or self.quote.image_count > CLEF_IMAGE_LIMIT
            or self.quote.unknown_reason is not None
        ):
            raise ValueError("Clef requires a known explicit platform vision call quote")
        return self


class ClefGatewayCheck(Contract):
    """AI Gateway state read through its API at configuration/test time; dispatch requires it."""

    gateway_id: str = Field(min_length=1, max_length=64)
    authentication: Literal[True]
    collect_logs: Literal[False]
    logpush: Literal[False]
    cache_ttl: Literal[0]
    # The application owns retries; each retry is a separately admitted call.
    gateway_retries: Literal[False]
    rate_limit_requests: int = Field(gt=0)
    rate_limit_seconds: int = Field(gt=0)
    workers_ai_billing_mode: Literal["unified"]
    checked_at: AwareDatetime


class ClefTriageResult(Contract):
    """Normalized internal response, not Cloudflare vendor wire JSON."""

    request_sha256: Sha256
    answers: list[ClefAnswer] = Field(min_length=1, max_length=64)
    usage: ProviderUsage
    fixed_quote: ClefFixedCallQuote
    triage_only: Literal[True] = True
    contains_reasons_or_citations: Literal[False] = False

    @model_validator(mode="after")
    def quote_matches_result(self) -> Self:
        if self.request_sha256 != self.fixed_quote.quote.request_sha256:
            raise ValueError("triage result must bind the admitted request")
        if len({answer.ref for answer in self.answers}) != len(self.answers):
            raise ValueError("duplicate triage answers are invalid")
        return self


class ClefSettingsSet(Contract):
    expected_revision: int | None = Field(default=None, strict=True, ge=1)
    account_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    gateway_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    enabled: bool = True
    price_revision: Revision
    fixed_sale_price: Money
    currency: Currency = "USD"
    workers_credential_id: UUID
    gateway_credential_id: UUID

    @model_validator(mode="after")
    def distinct_credentials(self) -> Self:
        if self.workers_credential_id == self.gateway_credential_id:
            raise ValueError("Clef requires separate model and gateway credentials")
        return self


class ClefPlatformConfig(ClefSettingsSet):
    platform_model_id: Literal["bid-review-clef"] = "bid-review-clef"
    revision: Revision
    workers_credential_revision: Revision
    gateway_credential_revision: Revision
    gateway_check: ClefGatewayCheck | None = None
    updated_at: AwareDatetime

    @property
    def price_version(self) -> int:
        return self.price_revision


class ClefSettingsData(Contract):
    config: ClefPlatformConfig | None = None
    blockers: list[str] = Field(default_factory=list, max_length=20)


class ClefCheckRequest(Contract):
    expected_revision: Revision


@dataclass(frozen=True)
class ClefResolution:
    config: ClefPlatformConfig | None
    blockers: list[str]
