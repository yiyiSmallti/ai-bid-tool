import hashlib
import re

from app.core.errors import ServiceError
from app.schemas.contracts import Category, ExtractedRequirement, Extraction, Source

PROMPT_VERSION = "req-v1"


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def validate_extraction(extraction: Extraction, chunks: list[dict]) -> None:
    available = {str(chunk["id"]): chunk for chunk in chunks}
    for item in extraction.items:
        chunk = available.get(str(item.source.chunk_id))
        if (
            not chunk
            or str(item.source.document_id) != str(chunk["document_id"])
            or item.source.page != chunk["page"]
            or not chunk["citation_verified"]
            or normalize(item.source.quote) not in normalize(chunk["text"])
        ):
            raise ServiceError(
                "invalid_citation",
                "Provider returned an unverified source citation; no requirements were saved",
                400,
                4,
            )


def merge_starred(extraction: Extraction, chunks: list[dict]) -> Extraction:
    items = list(extraction.items)
    for chunk in chunks:
        for line in chunk["text"].splitlines():
            quote = line.strip()
            if not quote or not re.search(r"[★☆]|实质性要求|否决投标|废标", quote):
                continue
            matching = next(
                (
                    item
                    for item in items
                    if item.source.chunk_id == chunk["id"]
                    and normalize(quote) in normalize(item.source.quote)
                ),
                None,
            )
            if matching:
                matching.starred = True
            else:
                items.append(
                    ExtractedRequirement(
                        category=Category.substantive,
                        starred=True,
                        text=quote,
                        source=Source(
                            document_id=chunk["document_id"],
                            chunk_id=chunk["id"],
                            page=chunk["page"],
                            quote=quote,
                        ),
                    )
                )
    return Extraction(items=items)


def fingerprint(item: ExtractedRequirement) -> str:
    position = item.source.location.block_id if item.source.location else item.source.page
    return hashlib.sha256(
        f"{item.source.document_id}:{position}:{normalize(item.source.quote)}".encode()
    ).hexdigest()
