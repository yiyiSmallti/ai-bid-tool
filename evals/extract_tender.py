"""Run the configured real LLM on one public tender PDF and save a reviewable artifact.

Calls the vendor API and costs money. Not part of CI. Reads BID_LLM_* settings from
the environment; the database is not touched.

    uv run python evals/extract_tender.py --pdf PUBLIC_TENDER.pdf --output NEW_RESULT.json
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
from app.schemas.contracts import Extraction  # noqa: E402
from app.services.extraction import merge_starred, normalize, validate_extraction  # noqa: E402
from app.services.parsing import parse_document  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402


def settings() -> Settings:
    # Only the LLM and OCR settings matter here; storage and database stay unused.
    os.environ.setdefault("BID_DATABASE_URL", "postgresql+psycopg://unused@localhost/unused")
    os.environ.setdefault("BID_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return Settings.load()


async def run(name: str, content: bytes) -> tuple[dict, int]:
    config = settings()
    if config.llm_provider == "disabled":
        raise SystemExit("Set BID_LLM_PROVIDER and its key before running this evaluation")
    llm = create_llm(config)
    started = time.monotonic()
    pages, _, parse_warnings = await parse_document(
        content, ".pdf", LocalOCR(config.ocr_language, config.ocr_data_dir), config.max_pages
    )
    document_id = uuid.uuid4()
    chunks = [
        {
            "id": uuid.uuid4(),
            "document_id": document_id,
            "page": page.page,
            "text": page.text,
            "citation_verified": page.citation_verified,
        }
        for page in pages
    ]
    by_id = {chunk["id"]: chunk for chunk in chunks}
    artifact: dict = {
        "pdf": name,
        "pages": len(pages),
        "provider": llm.name,
        "model": llm.model,
        "adapter_version": llm.version,
        "parse_warnings": parse_warnings,
    }
    try:
        result = await llm.extract(chunks, Extraction.model_json_schema())
    except ProviderFailure as exc:
        artifact["error"] = {"code": exc.code, "message": str(exc), "retryable": exc.retryable}
        artifact["usage"] = [usage.model_dump() for usage in exc.usage]
        print(f"Extraction failed: {exc.code}")
        return artifact, 1
    items = result.extraction.items
    verbatim = [
        normalize(item.source.quote) in normalize(by_id[item.source.chunk_id]["text"])
        for item in items
    ]
    try:
        validate_extraction(result.extraction, chunks)
        merged = merge_starred(result.extraction, chunks)
        accepted = True
    except ServiceError:
        merged = result.extraction
        accepted = False
    artifact |= {
        "duration_ms": int((time.monotonic() - started) * 1000),
        "usage": result.usage.model_dump(),
        "model_items": len(items),
        "verbatim_quotes": sum(verbatim),
        "batch_accepted_by_citation_check": accepted,
        "rule_starred_added": len(merged.items) - len(items),
        "by_category": {
            category: sum(item.category.value == category for item in items)
            for category in ("qualification", "technical", "scoring", "substantive")
        },
        "items": [
            {
                **item.model_dump(mode="json", exclude={"source"}),
                "page": item.source.page,
                "quote": item.source.quote,
                "quote_is_verbatim": ok,
            }
            for item, ok in zip(items, verbatim, strict=True)
        ],
    }
    print(
        f"{len(items)} items, {sum(verbatim)} verbatim quotes, "
        f"accepted={accepted}, tokens={result.usage.tokens}, usd={result.usage.usd}"
    )
    return artifact, 0 if accepted else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Choose a new output path; existing files are not overwritten")
    artifact, code = asyncio.run(run(args.pdf.name, args.pdf.read_bytes()))
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))
    print(f"Artifact written to {args.output}")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
