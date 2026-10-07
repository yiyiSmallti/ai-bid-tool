"""Category serialization through the HTTP extraction and worker postprocessing path.

Failure modes: vendor JSON strings may fail to become Category members; PDF and
Word source attachment may lose their types; citation copies or starred-clause
merging may introduce strings; JSON serialization may emit enum warnings or
change the public category values.
"""

import json
from uuid import uuid4

import httpx
import pytest
from app.core.config import Settings
from app.providers.calls import standalone_evaluation
from app.providers.llm import AnthropicExtractor, OpenAICompatibleExtractor
from app.schemas.contracts import Category, Extraction
from app.schemas.response_card_contracts import DraftView, ResponseRow
from app.services.extraction import merge_starred, split_cited, validate_extraction
from cryptography.fernet import Fernet


@pytest.mark.parametrize("category", ["technical", "scoring"])
@pytest.mark.parametrize("enum_input", [False, True])
def test_draft_response_row_preserves_category_and_serializes_without_warnings(
    category, enum_input, recwarn
):
    row = ResponseRow(
        requirement_id=uuid4(),
        category=Category(category) if enum_input else category,
        starred=False,
        card_id=uuid4(),
        card_revision_id=uuid4(),
        table="technical",
        tender_clause={
            "document_id": uuid4(),
            "chunk_id": uuid4(),
            "page": 1,
            "quote": "The bidder shall provide an implementation schedule.",
        },
        location_label=" Page 1 ",
        response_kind="commitment",
        response_text=" We will provide an implementation schedule. ",
        deviation="none",
        deviation_note=" No deviation. ",
    )
    payload = row.model_dump(mode="json")
    assert payload["category"] == category
    assert json.loads(row.model_dump_json()) == payload
    draft = DraftView(
        id=uuid4(),
        org_id=uuid4(),
        task_id=uuid4(),
        extraction_job_id=uuid4(),
        generation_job_id=uuid4(),
        completion="complete",
        validity="current",
        input_hash="a" * 64,
        tables={"substantive": [], "commercial": [], "technical": [row]},
        comply_only=[],
        gaps=[],
        invalidated_requirements=[],
    )
    assert draft.model_dump(mode="json")["tables"]["technical"][0] == payload
    assert json.loads(draft.model_dump_json())["tables"]["technical"][0] == payload
    assert not [warning for warning in recwarn if issubclass(warning.category, UserWarning)]
    assert row.category is Category(category)
    assert row.response_text == "We will provide an implementation schedule."
    assert row.location_label == "Page 1"
    assert row.deviation_note == "No deviation."


@pytest.mark.parametrize("category", ["technical", "scoring"])
@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("document_kind", ["pdf", "word"])
async def test_http_extraction_category_serializes_without_warnings(
    category, provider, document_kind, tmp_path, recwarn
):
    quote = "The bidder shall provide an implementation schedule."
    chunk = {
        "id": uuid4(),
        "document_id": uuid4(),
        "page": 1 if document_kind == "pdf" else None,
        "text": quote,
        "citation_verified": True,
    }
    if document_kind == "word":
        chunk["blocks"] = [
            {
                "block_id": "p1",
                "kind": "paragraph",
                "section_path": ["Requirements"],
                "paragraph": 1,
                "label": "Requirements / paragraph 1",
                "text": quote,
            }
        ]
    wire = {
        "items": [
            {
                "category": category,
                "starred": False,
                "text": quote,
                "ref": "1" if document_kind == "pdf" else "p1",
                "quote": quote,
                "condition": None,
            }
        ]
    }
    calls = []

    def reply(request):
        calls.append(request)
        if provider == "anthropic":
            body = {
                "model": "synthetic-category-model",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": json.dumps(wire)}],
                "usage": {"input_tokens": 10, "output_tokens": 10},
            }
        else:
            body = {
                "model": "synthetic-category-model",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(wire)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            }
        return httpx.Response(200, json=body)

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_url="postgresql+psycopg://localhost/bid_test_unused",
        encryption_key=Fernet.generate_key().decode(),
        token_key=Fernet.generate_key().decode(),
        llm_provider=provider,
        llm_api_key="synthetic-category-key",
        llm_model="synthetic-category-model",
    )
    adapter = AnthropicExtractor if provider == "anthropic" else OpenAICompatibleExtractor
    llm = adapter(settings, transport=httpx.MockTransport(reply))
    with standalone_evaluation():
        output = await llm.extract([chunk], Extraction.model_json_schema())

    assert len(calls) == 1
    assert output.extraction.items[0].category is Category(category)
    kept, rejected = split_cited(output.extraction, [chunk])
    assert rejected == []
    merged = merge_starred(kept, [chunk])
    validate_extraction(merged, [chunk])
    assert merged.items[0].category is Category(category)
    assert output.model_dump(mode="json")["extraction"]["items"][0]["category"] == category
    payload = merged.model_dump(mode="json")
    assert payload["items"][0]["category"] == category
    assert json.loads(merged.model_dump_json()) == payload
    assert not [warning for warning in recwarn if issubclass(warning.category, UserWarning)]
