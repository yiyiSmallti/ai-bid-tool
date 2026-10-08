"""Child-only bounded DOCX inspection; no external relationships or active content."""

import io
import re
import stat
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree

from app.core.errors import ServiceError

MAX_MEMBERS = 4096
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_PART_BYTES = 32 * 1024 * 1024
DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
ACTIVE_FIELDS = re.compile(
    r"\b(?:DDEAUTO|DDE|INCLUDEPICTURE|INCLUDETEXT|LINK|HYPERLINK)\b", re.IGNORECASE
)


def invalid() -> ServiceError:
    return ServiceError(
        "bid_docx_invalid",
        "DOCX must have bounded readable parts without external relationships or active content",
        400,
        2,
    )


def inspect_docx(content: bytes, parse: bool) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            names = {entry.filename for entry in members}
            if (
                len(members) > MAX_MEMBERS
                or len(names) != len(members)
                or not {"[Content_Types].xml", "word/document.xml", "_rels/.rels"} <= names
                or sum(entry.file_size for entry in members) > MAX_UNCOMPRESSED_BYTES
            ):
                raise invalid()
            content_types = None
            for entry in members:
                path = PurePosixPath(entry.filename)
                if (
                    entry.flag_bits & 1
                    or entry.file_size > MAX_PART_BYTES
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in entry.filename
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or any(
                        part in entry.filename.lower()
                        for part in ("vbaproject", "activex/", "embeddings/")
                    )
                ):
                    raise invalid()
                # Reading validates CRC too, without extracting archive-controlled paths.
                part = archive.read(entry)
                if not entry.filename.endswith((".xml", ".rels")):
                    continue
                upper = part.upper().replace(b"\x00", b"")
                if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
                    raise invalid()
                root = ElementTree.fromstring(part)
                # Office can fetch fields even without a package relationship.
                # Ordinary page/date/reference fields remain inert local inputs.
                instructions = "".join(
                    node.text or ""
                    for node in root.iter()
                    if node.tag.rsplit("}", 1)[-1] == "instrText"
                )
                if ACTIVE_FIELDS.search(instructions) or any(
                    ACTIVE_FIELDS.search(value)
                    for node in root.iter()
                    for name, value in node.attrib.items()
                    if name.rsplit("}", 1)[-1] == "instr"
                ):
                    raise invalid()
                if entry.filename == "[Content_Types].xml":
                    content_types = root
                if entry.filename.endswith(".rels") and any(
                    item.get("TargetMode", "").lower() == "external"
                    or item.get("Type", "").lower().endswith(("/oleobject", "/package", "/afchunk"))
                    for item in root
                ):
                    raise invalid()
            if (
                content_types is None
                or not any(
                    item.get("PartName") == "/word/document.xml"
                    and item.get("ContentType") == DOCX_CONTENT_TYPE
                    for item in content_types
                )
                or any(
                    "macroenabled" in item.get("ContentType", "").lower() for item in content_types
                )
            ):
                raise invalid()
        if not parse:
            return {"valid": True}
        from app.services.docx_blocks import parse_docx

        sections, details = parse_docx(content)
        warnings = []
        if any(message.startswith("No heading styles") for message in details):
            warnings.append("docx_heading_inferred")
        if any(message.startswith("Word content not parsed") for message in details):
            warnings.append("docx_structural_content_skipped")
        if not sections:
            warnings.append("docx_no_structural_text")
        return {
            "sections": [section.model_dump(mode="json") for section in sections],
            "warnings": warnings,
        }
    except (zipfile.BadZipFile, RuntimeError, ValueError, KeyError, ElementTree.ParseError) as exc:
        raise invalid() from exc
