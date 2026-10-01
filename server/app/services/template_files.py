"""Validate original DOCX bytes without executing code or retrieving external relations."""

import hashlib
import io
import zipfile
from pathlib import Path

from docx import Document

from app.core.errors import ServiceError
from app.schemas.template_contracts import TemplateFile

MAX_TEMPLATE_BYTES = 40 * 1024 * 1024
WARNINGS = [
    "Template metadata is a declaration; chapter matching and export adaptation have not been verified"
]


def validate_template(content: bytes, name: str) -> TemplateFile:
    try:
        if (
            not content
            or len(content) > MAX_TEMPLATE_BYTES
            or Path(name).suffix.lower() != ".docx"
            or not name.strip()
            or len(name) > 200
            or any(char in name for char in "/\\")
            or any(ord(char) < 32 or ord(char) == 127 for char in name)
        ):
            raise ValueError("invalid file")
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if sum(entry.file_size for entry in entries) > 100 * 1024 * 1024:
                raise ValueError("oversized archive")
            names = [entry.filename for entry in entries]
            if len(set(names)) != len(names):
                raise ValueError("duplicate members")
            for entry in entries:
                path = Path(entry.filename)
                if (
                    entry.flag_bits & 1
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in entry.filename
                    or "vbaproject" in entry.filename.casefold()
                    or "vbadata" in entry.filename.casefold()
                ):
                    raise ValueError("unsupported member")
            content_types = archive.read("[Content_Types].xml").lower()
            if b"macroenabled" in content_types or b"vba" in content_types:
                raise ValueError("macros are unsupported")
        Document(io.BytesIO(content))
        return TemplateFile(
            name=name,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )
    except Exception as exc:
        raise ServiceError(
            "invalid_template_file",
            "Template must be a readable DOCX within limits, without macros or unsafe package members",
            400,
            2,
        ) from exc


def read_template(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_TEMPLATE_BYTES + 1)
    except OSError as exc:
        raise ServiceError("invalid_template_input", "Cannot read template file", 400, 2) from exc
    validate_template(content, path.name)
    return content
