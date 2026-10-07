"""Neutral PDF byte validation, used by certificate and attachment processing."""

import hashlib

import pymupdf

from app.core.errors import ServiceError
from app.schemas.certificate_file_contracts import CertificateScanFile

MAX_FILE_BYTES = 40 * 1024 * 1024


def validate_file(content: bytes, name: str) -> CertificateScanFile:
    try:
        if (
            not content
            or len(content) > MAX_FILE_BYTES
            or not content.lstrip().startswith(b"%PDF-")
        ):
            raise ValueError("invalid PDF")
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if (
                not pdf.is_pdf
                or pdf.is_repaired
                or pdf.is_encrypted
                or pdf.needs_pass
                or pdf.xref_get_key(-1, "Encrypt")[0] != "null"
                or not 1 <= len(pdf) <= 200
            ):
                raise ValueError("unsupported PDF")
            for page in pdf:
                if page.rect.is_empty:
                    raise ValueError("invalid page")
            return CertificateScanFile(
                name=name,
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                page_count=len(pdf),
            )
    except Exception as exc:
        raise ServiceError(
            "invalid_certificate_file",
            "File must be a readable unencrypted PDF within file/page limits",
            400,
            2,
        ) from exc
