"""Run the configured real LLM on one public tender (PDF or Word) and save a reviewable artifact.

Calls the vendor API and costs money. Not part of CI. Reads non-secret BID_LLM_* settings and resolves standalone credentials from PostgreSQL.

    uv run python evals/extract_tender.py --file PUBLIC_TENDER.docx --output NEW_RESULT.json
    uv run python evals/extract_tender.py --file PUBLIC_TENDER.docx --batch-chars 200000 --output WHOLE.json
"""

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app.core.config import Settings  # noqa: E402
from app.core.errors import ServiceError  # noqa: E402
from app.providers.base import ProviderFailure  # noqa: E402
from app.providers.llm import create_llm  # noqa: E402
from app.providers.local_ocr import LocalOCR  # noqa: E402
from app.schemas.contracts import Extraction, SectionText  # noqa: E402
from app.services.extraction import cited, merge_starred, validate_extraction  # noqa: E402
from app.services.parsing import parse_document  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

STAR = "★"


def settings(batch_chars: int | None) -> Settings:
    # Stored credential roots and the dedicated reader URL remain required deployment inputs.
    os.environ.setdefault("BID_DATABASE_URL", "postgresql+psycopg://unused@localhost/unused")
    os.environ.setdefault("BID_ENCRYPTION_KEY", Fernet.generate_key().decode())
    os.environ.setdefault("BID_TOKEN_KEY", Fernet.generate_key().decode())
    config = Settings.load()
    if batch_chars:
        config = config.model_copy(update={"llm_batch_chars": batch_chars})
    return config


def star_units(chunks: list[dict]) -> list[tuple[str, str]]:
    """(position, text) of every unit that carries a ★: Word blocks or PDF lines."""
    units = []
    for chunk in chunks:
        if chunk.get("blocks"):
            units += [(b["block_id"], b["text"]) for b in chunk["blocks"] if STAR in b["text"]]
        else:
            units += [
                (str(chunk["page"]), line) for line in chunk["text"].splitlines() if STAR in line
            ]
    return units


def position(item) -> str:
    return item.source.location.block_id if item.source.location else str(item.source.page)


def star_recall(items, units) -> dict:
    found = {position(item) for item in items}
    covered = sum(1 for unit_position, _ in units if unit_position in found)
    return {"star_units": len(units), "covered": covered}


async def run(name: str, content: bytes, batch_chars: int | None) -> tuple[dict, int]:
    config = settings(batch_chars)
    if config.llm_provider == "disabled":
        raise SystemExit(
            "Set BID_LLM_PROVIDER and an active standalone database credential before running this evaluation"
        )
    llm = create_llm(config)
    suffix = Path(name).suffix.lower()
    pages, _, parse_warnings = await parse_document(
        content, suffix, LocalOCR(config.ocr_language, config.ocr_data_dir), config.max_pages
    )
    document_id = uuid.uuid4()
    chunks = [
        {
            "id": uuid.uuid4(),
            "document_id": document_id,
            "page": None if isinstance(page, SectionText) else page.page,
            "text": page.text,
            "citation_verified": True if isinstance(page, SectionText) else page.citation_verified,
            "blocks": [b.model_dump() for b in page.blocks]
            if isinstance(page, SectionText)
            else None,
        }
        for page in pages
    ]
    by_id = {chunk["id"]: chunk for chunk in chunks}
    units = star_units(chunks)
    artifact: dict = {
        "file": name,
        "chunks": len(chunks),
        "characters": sum(len(c["text"]) for c in chunks),
        "batch_chars": config.llm_batch_chars,
        "provider": llm.name,
        "model": llm.model,
        "adapter_version": llm.version,
        "parse_warnings": parse_warnings,
    }
    started = time.monotonic()
    try:
        result = await llm.extract(chunks, Extraction.model_json_schema())
    except ProviderFailure as exc:
        artifact["duration_ms"] = int((time.monotonic() - started) * 1000)
        artifact["error"] = {"code": exc.code, "message": str(exc), "retryable": exc.retryable}
        artifact["usage"] = [usage.model_dump() for usage in exc.usage]
        print(f"Extraction failed: {exc.code} ({exc})")
        return artifact, 1
    finally:
        from app.core.credential_db import close_connections

        await close_connections(config)
    items = result.extraction.items
    valid = [cited(item, by_id.get(item.source.chunk_id)) for item in items]
    try:
        validate_extraction(result.extraction, chunks)
        accepted = True
    except ServiceError:
        accepted = False
    merged = merge_starred(
        Extraction(items=[i for i, ok in zip(items, valid, strict=True) if ok]), chunks
    )
    artifact |= {
        "duration_ms": int((time.monotonic() - started) * 1000),
        "usage": result.usage.model_dump(),
        "model_items": len(items),
        "verified_citations": sum(valid),
        "batch_accepted_by_citation_check": accepted,
        "star_recall_model": star_recall(items, units),
        "star_recall_with_rule": star_recall(merged.items, units),
        "items_after_rule": len(merged.items),
        "by_category": {
            category: sum(item.category.value == category for item in items)
            for category in ("qualification", "technical", "scoring", "substantive")
        },
        "items": [
            {
                **item.model_dump(mode="json", exclude={"source"}),
                "position": item.source.location.label
                if item.source.location
                else f"第 {item.source.page} 页",
                "quote": item.source.quote,
                "citation_verified": ok,
            }
            for item, ok in zip(items, valid, strict=True)
        ],
    }
    print(
        f"{len(items)} items, {sum(valid)} verified citations, accepted={accepted}, "
        f"★ model {artifact['star_recall_model']}, with rule {artifact['star_recall_with_rule']}, "
        f"tokens={result.usage.tokens}, {artifact['duration_ms'] / 1000:.0f}s"
    )
    return artifact, 0 if accepted else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, required=True, help="Public tender as .pdf or .docx")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--batch-chars", type=int, default=None, help="Characters per model request"
    )
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Choose a new output path; existing files are not overwritten")
    try:
        artifact, code = asyncio.run(run(args.file.name, args.file.read_bytes(), args.batch_chars))
    except ServiceError as exc:
        print(json.dumps({"error": {"code": exc.code}}), file=sys.stderr)
        raise SystemExit(exc.exit_code) from None
    except ValueError:
        print(json.dumps({"error": {"code": "invalid_input"}}), file=sys.stderr)
        raise SystemExit(4) from None
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))
    print(f"Artifact written to {args.output}")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
