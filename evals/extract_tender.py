"""Evaluate tender extraction with a real provider or a fixed offline acceptance fixture.

The real-provider mode calls the configured vendor API and costs money. It must be
explicitly enabled with --real-provider. The offline acceptance script exercises
the parser and citation/coverage diagnostics without making provider calls.

    uv run python evals/extract_tender.py --real-provider --file PUBLIC_TENDER.docx --output NEW_RESULT.json
    uv run python evals/extract_tender.py --real-provider --file PUBLIC_TENDER.docx --batch-chars 200000 --output WHOLE.json
    uv run python evals/offline_extract_acceptance.py --output evals/artifacts/offline-extract.json
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app.core.config import Settings  # noqa: E402
from app.core.errors import ServiceError  # noqa: E402
from app.providers.base import ProviderFailure  # noqa: E402
from app.providers.calls import standalone_evaluation  # noqa: E402
from app.providers.llm import create_llm  # noqa: E402
from app.providers.local_ocr import LocalOCR  # noqa: E402
from app.schemas.contracts import Extraction, SectionText  # noqa: E402
from app.services.extraction import (  # noqa: E402
    cited,
    merge_starred,
    normalize,
    validate_extraction,
)
from app.services.parsing import parse_document  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

STAR = "★"
STAR_UNIT_BOUNDARIES = re.compile(r"[；;。！？!?]|\n{2,}")


def settings(batch_chars: int | None) -> Settings:
    # Stored credential roots and the dedicated reader URL remain required deployment inputs.
    os.environ.setdefault("BID_DATABASE_URL", "postgresql+psycopg://unused@localhost/unused")
    os.environ.setdefault("BID_ENCRYPTION_KEY", Fernet.generate_key().decode())
    os.environ.setdefault("BID_TOKEN_KEY", Fernet.generate_key().decode())
    config = Settings.load()
    if batch_chars:
        config = config.model_copy(update={"llm_batch_chars": batch_chars})
    return config


def star_units(chunks: list[dict]) -> list[dict]:
    """Return each source clause introduced by ★ with its exact citation position."""
    units = []
    for chunk in chunks:
        if chunk.get("blocks"):
            sources = [(b["text"], b) for b in chunk["blocks"]]
        else:
            sources = [(chunk["text"], None)]
        for text, block in sources:
            for match in re.finditer(re.escape(STAR), text):
                boundary = STAR_UNIT_BOUNDARIES.search(text, match.end())
                next_star = text.find(STAR, match.end())
                ends = [
                    end
                    for end in (boundary.end() if boundary else None, next_star)
                    if end is not None and end > match.start()
                ]
                end = min(ends) if ends else len(text)
                quote = text[match.start() : end].strip()
                units.append(
                    {
                        "chunk_id": str(chunk["id"]),
                        "position": block["block_id"] if block else str(chunk["page"]),
                        "quote": quote,
                        "location": (
                            {key: value for key, value in block.items() if key != "text"}
                            if block
                            else None
                        ),
                        "page": None if block else chunk["page"],
                    }
                )
    return units


def same_source_position(item, unit: dict) -> bool:
    if str(item.source.chunk_id) != unit["chunk_id"]:
        return False
    if unit["location"] is None:
        return item.source.location is None and item.source.page == unit["page"]
    return item.source.location is not None and item.source.location.block_id == unit["position"]


def clause_text(text: str) -> str:
    """Compare source clauses while treating the star marker as a separate label."""
    return normalize(text.replace(STAR, "").replace("☆", "")).strip()


def star_coverage(items, units: list[dict], chunks: list[dict]) -> dict:
    """Report source-clause coverage and star labels; this is not human-annotated recall."""
    by_id = {str(chunk["id"]): chunk for chunk in chunks}
    valid_items = [item for item in items if cited(item, by_id.get(str(item.source.chunk_id)))]
    covered, labelled = 0, 0
    for unit in units:
        matches = [
            item
            for item in valid_items
            if same_source_position(item, unit)
            and clause_text(item.source.quote) == clause_text(unit["quote"])
        ]
        if matches:
            covered += 1
            if any(item.starred for item in matches):
                labelled += 1
    count = len(units)
    return {
        "source_star_clauses": count,
        "covered_clauses": covered,
        "star_labelled_clauses": labelled,
        "clause_coverage": covered / count if count else None,
        "star_label_coverage": labelled / count if count else None,
        "metric_note": "Parser-derived ★ clause diagnostic; not recall against human annotations.",
    }


async def run(name: str, content: bytes, batch_chars: int | None) -> tuple[dict, int]:
    with standalone_evaluation():
        return await _run(name, content, batch_chars)


async def _run(name: str, content: bytes, batch_chars: int | None) -> tuple[dict, int]:
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
        artifact["usage"] = [usage.model_dump(mode="json") for usage in exc.usage]
        print(f"Extraction failed: {exc.code} ({exc})")
        return artifact, 1
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
        "usage": result.usage.model_dump(mode="json"),
        "model_items": len(items),
        "verified_citations": sum(valid),
        "batch_accepted_by_citation_check": accepted,
        "star_coverage_model": star_coverage(items, units, chunks),
        "star_coverage_with_rule": star_coverage(merged.items, units, chunks),
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
        f"★ coverage model {artifact['star_coverage_model']['clause_coverage']}, "
        f"with rule {artifact['star_coverage_with_rule']['clause_coverage']}, "
        f"tokens={result.usage.tokens}, {artifact['duration_ms'] / 1000:.0f}s"
    )
    return artifact, 0 if accepted else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--real-provider", action="store_true", help="Explicitly enable a billable provider call"
    )
    parser.add_argument("--file", type=Path, required=True, help="Public tender as .pdf or .docx")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--batch-chars", type=int, default=None, help="Characters per model request"
    )
    args = parser.parse_args()
    if not args.real_provider:
        parser.error("real provider calls require --real-provider")
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
