"""Review-only proposal: not imported or registered by runtime code.

No rendering, routes, permissions, command registration or persistence occurs here.
"""

from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import Field, field_validator, model_validator

from app.schemas.contracts import Contract
from app.schemas.evidence_source_contracts import EvidenceSourceArchive, EvidenceSourcePreview


class PixelRect(Contract):
    x: int = Field(strict=True, ge=0, le=8191)
    y: int = Field(strict=True, ge=0, le=8191)
    width: int = Field(strict=True, ge=1, le=8192)
    height: int = Field(strict=True, ge=1, le=8192)

    @model_validator(mode="after")
    def bounded_endpoints(self):
        if self.x + self.width > 8192 or self.y + self.height > 8192:
            raise ValueError("rectangle endpoint exceeds the profile limit")
        return self


class SourceAnnotationPlan(Contract):
    crop: PixelRect | None = None
    boxes: list[PixelRect] = Field(default_factory=list, max_length=20)


class SourceAnnotationMapping(Contract):
    original_crop: PixelRect
    content_offset_x: int = Field(ge=0, le=8192)
    content_offset_y: Literal[0] = 0
    footer_height_px: int = Field(ge=1, le=8192)


class SourceAnnotationRendering(Contract):
    annotation_profile: Literal["source-markup-v1"] = "source-markup-v1"
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file: EvidenceSourcePreview
    mapping: SourceAnnotationMapping


class SourceAnnotationReceipt(SourceAnnotationRendering):
    source: EvidenceSourceArchive
    plan: SourceAnnotationPlan
    annotated_at: datetime
    output_path: str = Field(min_length=1)
    status: Literal["unconfirmed_source"] = "unconfirmed_source"
    confirmed_by: Literal[None] = None
    eligible_for_draft_export: Literal[False] = False

    @field_validator("annotated_at")
    @classmethod
    def operation_time_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("an aware operation timestamp is required")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def consistent_mapping(self):
        crop = self.mapping.original_crop
        if crop.x + crop.width > self.source.preview.width_px:
            raise ValueError("crop exceeds the source width")
        if crop.y + crop.height > self.source.preview.height_px:
            raise ValueError("crop exceeds the source height")
        if self.plan.crop is not None and crop != self.plan.crop:
            raise ValueError("mapping differs from the approved crop")
        if self.plan.crop is None and crop != PixelRect(
            x=0, y=0,
            width=self.source.preview.width_px, height=self.source.preview.height_px,
        ):
            raise ValueError("the uncropped mapping must preserve the full source")
        for box in self.plan.boxes:
            if (box.x < crop.x or box.y < crop.y
                or box.x + box.width > crop.x + crop.width
                or box.y + box.height > crop.y + crop.height):
                raise ValueError("box must be inside the original crop")
        if self.file.width_px != max(crop.width, 1024):
            raise ValueError("the canvas width differs from the profile")
        if self.mapping.content_offset_x != (self.file.width_px - crop.width) // 2:
            raise ValueError("the content must be horizontally centered")
        if self.file.height_px != crop.height + self.mapping.footer_height_px:
            raise ValueError("the canvas height differs from the mapping")
        return self


class AnnotationProvider(Protocol):
    async def annotate(
        self, content: bytes, source: EvidenceSourceArchive, plan: SourceAnnotationPlan
    ) -> tuple[bytes, SourceAnnotationRendering]: ...
