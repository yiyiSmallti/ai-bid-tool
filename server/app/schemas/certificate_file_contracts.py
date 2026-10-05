"""Approved immutable certificate originals and retained task file contracts.

An original is one PDF. Several uploaded images or PDFs are composed into it in
order, one page per image; the uploaded files are kept as its parts."""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.certificate_contracts import CertificateData, TaskCertificateSnapshot
from app.schemas.contracts import Contract

MAX_PARTS = 20
PartMediaType = Literal["application/pdf", "image/png", "image/jpeg"]


def safe_name(value: str, suffixes: tuple[str, ...]) -> str:
    if (
        not value.strip()
        or not value.lower().endswith(suffixes)
        or any(char in value for char in "/\\")
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("a safe display name with a supported extension is required")
    return value


class CertificatePartOptions(Contract):
    """Per uploaded file, in upload order. Rotation turns pages clockwise."""

    rotation: Literal[0, 90, 180, 270] = 0


class CertificateFileCreate(Contract):
    expected_revision: int = Field(ge=1)
    data: CertificateData
    # Empty, or one entry per uploaded file.
    parts: list[CertificatePartOptions] = Field(default_factory=list, max_length=MAX_PARTS)


class CertificatePart(Contract):
    ordinal: int = Field(ge=1, le=MAX_PARTS)
    name: str = Field(min_length=1, max_length=200)
    media_type: PartMediaType
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0, le=40 * 1024 * 1024)
    page_start: int = Field(ge=1, le=200)
    page_count: int = Field(ge=1, le=200)
    rotation: Literal[0, 90, 180, 270]

    @field_validator("name")
    @classmethod
    def safe_display_name(cls, value: str) -> str:
        return safe_name(value, (".pdf", ".png", ".jpg", ".jpeg"))


class CertificateScanFile(Contract):
    name: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0, le=40 * 1024 * 1024)
    media_type: Literal["application/pdf"] = "application/pdf"
    page_count: int = Field(ge=1, le=200)

    @field_validator("name")
    @classmethod
    def safe_display_name(cls, value: str) -> str:
        return safe_name(value, (".pdf",))


class CertificateFileRevision(Contract):
    id: UUID
    org_id: UUID
    certificate_id: UUID
    certificate_revision_id: UUID
    revision: int = Field(ge=1)
    data: CertificateData
    file: CertificateScanFile
    # Empty when the original is the single uploaded PDF itself.
    parts: list[CertificatePart] = Field(default_factory=list)


class TaskCertificateFileSnapshot(TaskCertificateSnapshot):
    certificate_file_id: UUID | None
    file: CertificateScanFile | None

    @model_validator(mode="after")
    def coherent_file_presence(self):
        if (self.certificate_file_id is None) != (self.file is None):
            raise ValueError("file and file id must be present together")
        return self


class CertificateFileDownloadReceipt(Contract):
    certificate_revision_id: UUID
    output_path: str
    file: CertificateScanFile


class CertificateFileListData(Contract):
    history: bool
    current_revisions: dict[UUID, int]


class TaskCertificateFileListData(Contract):
    history: bool
    active_snapshot_ids: list[UUID]


class CertificateFileDownloadLink(Contract):
    url: str
    expires_in: Literal[300] = 300
