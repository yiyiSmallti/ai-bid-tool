"""DB-free drafting input identity regression, specified before the fix.

Failure inventory: a valid quote can survive changes to surrounding PDF text or
Word block content while IDs/quote/location remain unchanged; a no-card cache
must still miss. Unchanged content must keep the same key. The explicit chunk
hash must match the verified source pin, including structured block content.
Real snapshot, citation, review preparation and digest code run against a
materialized read-only graph; no database, model or network is used.
"""

import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from app.models.entities import Chunk, Requirement
from app.models.response_cards import CardGenerationRun, ResponseCard, ResponseCardRevision
from app.services import card_generation, requirement_source
from app.services.drafts import digest


class SnapshotGraph:
    def __init__(self, requirement, chunk, document):
        self.requirement, self.chunk, self.document = requirement, chunk, document
        self.card = self.revision = self.prior = None

    async def get(self, model, key):
        if model is ResponseCardRevision:
            assert self.revision is not None and key == self.revision.id
            return self.revision
        assert model is Chunk and key == self.chunk.id
        return self.chunk

    async def scalar(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is CardGenerationRun:
            return self.prior
        assert entity is ResponseCard
        return self.card

    async def scalars(self, statement):
        # No confidential fields, selected materials or certificate sources.
        return SimpleNamespace(all=lambda: [])

    async def execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is Requirement:
            rows = [(self.requirement, None)]
        else:
            assert entity is Chunk
            rows = [(self.chunk, self.document)]
        return SimpleNamespace(all=lambda: rows)


def source_graph(word):
    org, task, job, document, chunk_id, requirement_id = [UUID(int=n) for n in range(1, 7)]
    location = (
        {
            "block_id": "p1",
            "kind": "paragraph",
            "section_path": ["Synthetic requirements"],
            "paragraph": 1,
            "table": None,
            "row": None,
            "column": None,
            "label": "Synthetic paragraph 1",
        }
        if word
        else None
    )
    quote = "Synthetic delivery within thirty days."
    text = f"Synthetic surrounding text.\n{quote}\nSynthetic end."
    requirement = SimpleNamespace(
        id=requirement_id,
        org_id=org,
        task_id=task,
        job_id=job,
        document_id=document,
        chunk_id=chunk_id,
        page=None if word else 1,
        location=location,
        quote=quote,
        category="technical",
        starred=False,
        text=quote,
        condition=None,
    )
    chunk = SimpleNamespace(
        id=chunk_id,
        org_id=org,
        task_id=task,
        document_id=document,
        text=text,
        blocks=[{**location, "text": text}] if word else None,
        seq=1,
        page=None if word else 1,
        citation_verified=True,
    )
    document_row = SimpleNamespace(id=document, org_id=org, task_id=task, sha256="a" * 64)
    return SnapshotGraph(requirement, chunk, document_row)


def add_card(graph, *, origin="human", manifest=None):
    graph.revision = SimpleNamespace(
        id=UUID(int=8),
        state="draft",
        disposition="response",
        origin=origin,
        model_job_id=UUID(int=9) if origin == "model" else None,
    )
    graph.card = SimpleNamespace(current_revision_id=graph.revision.id)
    if manifest is not None:
        graph.prior = SimpleNamespace(input_manifest=manifest)


async def snapshot(graph):
    actor = SimpleNamespace(org_id=graph.requirement.org_id, principal_id=UUID(int=7))
    task = SimpleNamespace(
        id=graph.requirement.task_id,
        model_redaction_enabled=False,
        model_redaction_revision=1,
    )
    llm = SimpleNamespace(name="synthetic", model="synthetic", version="1")
    return await card_generation.snapshot(
        graph,
        actor,
        task,
        [graph.requirement],
        None,
        llm,
        None,
        None,
        scope_requirements=[graph.requirement],
    )


def cache_key(manifest, targets):
    return digest({"kind": "card_generate", "input_hash": digest(manifest), "targets": targets})


@pytest.mark.parametrize("word", [False, True], ids=["pdf-text", "word-block"])
async def test_no_card_cache_binds_source_content(word, tmp_path):
    graph = source_graph(word)
    before, secret, targets, selected, skipped = await snapshot(graph)
    first_key = cache_key(before, targets)
    cached = {first_key: "synthetic-cached-job"}
    assert targets == {str(graph.requirement.id): None}
    assert selected == [str(graph.requirement.id)] and skipped == {}
    unchanged, _, unchanged_targets, _, _ = await snapshot(graph)
    assert cached[cache_key(unchanged, unchanged_targets)] == "synthetic-cached-job"

    # Keep the quote and its exact location valid. Change only its source content.
    if word:
        graph.chunk.blocks[0]["text"] += "\nChanged synthetic block context."
    else:
        graph.chunk.text += "\nChanged synthetic PDF context."
    after, changed_secret, changed_targets, changed_selected, changed_skipped = await snapshot(
        graph
    )
    changed_key = cache_key(after, changed_targets)
    assert changed_key not in cached
    assert changed_secret == secret and changed_targets == targets
    assert changed_selected == selected and changed_skipped == skipped

    reviews = await requirement_source.effective_reviews(graph, [graph.requirement])
    pin = reviews[graph.requirement.id].source_pin
    assert pin is not None
    assert after["requirements"][0].get("chunk_sha256") == pin.chunk_sha256
    assert before["requirements"][0]["chunk_sha256"] != after["requirements"][0]["chunk_sha256"]

    # Check the direct requirements identity independently of the preparation gate.
    assert digest(before["requirements"]) != digest(after["requirements"])
    (tmp_path / "drafting-cache-identity.json").write_text(
        json.dumps(
            {
                "source_kind": "word-block" if word else "pdf-text",
                "unchanged_cache_hit": True,
                "changed_cache_miss": True,
                "original_key": first_key,
                "changed_key": changed_key,
                "source_pin_chunk_sha256": pin.chunk_sha256,
            },
            indent=2,
        )
        + "\n"
    )


@pytest.mark.parametrize("origin", ["human", "model"])
async def test_existing_card_retains_legacy_requirements_identity(origin):
    graph = source_graph(False)
    add_card(graph, origin=origin, manifest={"requirements": []})
    manifest, secret, targets, _, _ = await snapshot(graph)
    requirement = graph.requirement
    expected = {
        "requirement_id": str(requirement.id),
        "document_id": str(requirement.document_id),
        "chunk_id": str(requirement.chunk_id),
        "page": requirement.page,
        "quote_sha256": card_generation.cards.quote_hash(requirement.quote),
        "location_sha256": digest({"page": requirement.page, "block": requirement.location}),
        "sent_sha256": card_generation.cards.quote_hash(
            json.dumps(secret["requirements"][0], sort_keys=True, ensure_ascii=False)
        ),
    }
    assert manifest["requirements"] == [expected]
    unchanged, _, unchanged_targets, _, _ = await snapshot(graph)
    assert cache_key(unchanged, unchanged_targets) == cache_key(manifest, targets)


async def test_generated_card_keeps_no_card_source_identity_for_cache_reuse():
    graph = source_graph(False)
    original, _, original_targets, _, _ = await snapshot(graph)
    original_key = cache_key(original, original_targets)
    add_card(graph, origin="model", manifest=original)
    generated, _, _, _, _ = await snapshot(graph)
    # submit_generation reuses the prior target revisions when input_hash matches.
    assert generated == original
    assert cache_key(generated, original_targets) == original_key
    graph.chunk.text += "\nChanged synthetic source after generation."
    changed, _, _, _, _ = await snapshot(graph)
    assert changed["requirements"][0]["chunk_sha256"] != original["requirements"][0]["chunk_sha256"]
    assert cache_key(changed, original_targets) != original_key
