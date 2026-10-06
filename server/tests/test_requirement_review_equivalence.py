"""DB-free equivalence specified before B02 latency optimization.

Failure inventory: canonical bytes or numeric formatting drift changes immutable
hashes; optimized pins lose document/chunk/location/quote/offset/policy bindings;
shared chunks bleed locations; ambiguous or missing citations gain approval;
current rows differ from cached caller objects; source/content changes revive old
confirmation; permanent invalidation revives after change-back; metadata-only
projection silently becomes consumable. No database, Provider or network is used.

The oracle below is frozen from requirement_source.py before optimization,
SHA256 b1dc3810913a35002f68dad37a278cecb0124f8c815cfa5de721372cb0ac49a9.
Its canonical/pin/hash/effective logic must not be updated with production helpers.
Extraction's already independently tested locator remains a shared dependency;
these tests freeze review behavior, not a second locator implementation.
"""

import json
import math
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID

import pytest
from app.core.errors import ServiceError, not_found
from app.models.entities import Chunk, Document, Requirement
from app.models.requirement_confirmation import RequirementReview
from app.schemas.contracts import Source
from app.schemas.requirement_confirmation import RequirementContent, VerifiedRequirementSource
from app.services import requirement_source as production
from app.services import response_cards
from app.services.extraction import locate_source_citation_span, locate_spans, source_text
from sqlalchemy import select

POLICY = "requirement-source-v1"


def canonical(value):
    """Canonical JSON with decimal numbers shared with PostgreSQL jsonb."""
    if value is None or isinstance(value, (str, bool)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (int, float, Decimal)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Review JSON numbers must be finite")
        number = format(Decimal(str(value)), "f")
        if "." in number:
            number = number.rstrip("0").rstrip(".")
        return "0" if number in {"-0", ""} else number
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(canonical(str(key)) + ":" + canonical(value[key]) for key in sorted(value))
            + "}"
        )
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical(item) for item in value) + "]"
    return canonical(str(value))


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def raw_digest(value):
    return sha256(value.encode()).hexdigest()


def source_of(req):
    return Source(
        document_id=req.document_id,
        chunk_id=req.chunk_id,
        page=req.page,
        location=req.location,
        quote=req.quote,
    )


def raw_content(req):
    return dict(
        category=req.category,
        starred=req.starred,
        text=req.text,
        condition=req.condition,
        source=source_of(req).model_dump(mode="json"),
    )


def content_of(req):
    if len(req.text) > 20_000 or len(req.quote) > 20_000:
        raise ServiceError(
            "legacy_item_too_large", "Stored content exceeds the review content contract", 422, 2
        )
    return RequirementContent.model_validate(raw_content(req))


def chunk_data(chunk):
    return {
        name: getattr(chunk, name)
        for name in ("document_id", "text", "blocks", "seq", "page", "citation_verified")
    }


def make_pin(source, chunk, document, span, *, chunk_hash=None, location_hash=None):
    data = chunk_data(chunk)
    located = source_text(source, data)
    if located is None or span is None:
        return None
    start, end = span
    if located[start:end] != source.quote:
        return None
    body = dict(
        source=source.model_dump(mode="json"),
        document_sha256=document.sha256,
        chunk_sha256=chunk_hash or digest(data),
        location_sha256=location_hash or raw_digest(located),
        quote_sha256=raw_digest(source.quote),
        start=start,
        end=end,
        verifier_version=POLICY,
    )
    return VerifiedRequirementSource.model_validate({**body, "binding_sha256": digest(body)})


async def verify(session, actor, task_id, source):
    row = (
        await session.execute(
            select(Chunk, Document)
            .join(Document, (Document.org_id == Chunk.org_id) & (Document.id == Chunk.document_id))
            .where(
                Chunk.org_id == actor.org_id,
                Chunk.task_id == task_id,
                Chunk.id == source.chunk_id,
                Document.task_id == task_id,
                Document.id == source.document_id,
            )
        )
    ).first()
    if row is None:
        raise not_found()
    chunk, document = row
    located = source_text(source, chunk_data(chunk))
    if located is None:
        raise ServiceError("unverified_location", "Choose a verified source location", 422, 2)
    span, reason = locate_source_citation_span(located, source.quote, source.quote)
    if span is None:
        raise ServiceError(
            reason or "quote_not_at_position", "Quote cannot be uniquely located", 422, 2
        )
    pin = make_pin(source, chunk, document, span)
    if pin is None:
        raise ServiceError(
            "nonverbatim_quote", "Quote must equal the original source slice", 422, 2
        )
    return pin


def review_hash(req, pin):
    return digest(
        dict(
            version="requirement-review-v1",
            org_id=str(req.org_id),
            task_id=str(req.task_id),
            extraction_job_id=str(req.job_id),
            requirement_id=str(req.id),
            content=raw_content(req),
            source_binding_sha256=pin.binding_sha256 if pin else None,
        )
    )


@dataclass
class EffectiveReview:
    state: str
    revision: int
    review_hash: str
    source_pin: VerifiedRequirementSource | None
    confirmed_by_user_id: UUID | None = None
    confirmed_at: object = None
    stored: RequirementReview | None = None

    @property
    def confirmed(self):
        return self.state == "confirmed"

    @property
    def citation_valid(self):
        return self.source_pin is not None


async def effective_reviews(session, requirements, *, citation_validity=None):
    """Load dependencies in batches; reuse verified stored offsets when hashes match.

    Callers may pass existing citation validity. A matching complete chunk pin makes
    locating again unnecessary; changed bytes always get a fresh batched locator.
    """
    if not requirements:
        return {}
    ids = [r.id for r in requirements]
    # Raw SQL/source triggers may have changed objects in the identity map.
    # Refresh requirements with their review in one set query. Load distinct
    # chunks with their documents separately so shared large text is not repeated
    # once per requirement in a wide join, and the graph stays bounded to two reads.
    rows = list(
        (
            await session.execute(
                select(Requirement, RequirementReview)
                .outerjoin(
                    RequirementReview,
                    (RequirementReview.org_id == Requirement.org_id)
                    & (RequirementReview.requirement_id == Requirement.id),
                )
                .where(Requirement.id.in_(ids))
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    requirements = [requirement for requirement, _ in rows]
    if len(requirements) != len(set(ids)):
        raise not_found()
    stored = {review.requirement_id: review for _, review in rows if review is not None}
    parents = list(
        (
            await session.execute(
                select(Chunk, Document)
                .outerjoin(
                    Document, (Document.org_id == Chunk.org_id) & (Document.id == Chunk.document_id)
                )
                .where(Chunk.id.in_({r.chunk_id for r in requirements}))
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    chunks = {chunk.id: chunk for chunk, _ in parents}
    documents = {document.id: document for _, document in parents if document is not None}
    chunk_hashes = {key: digest(chunk_data(chunk)) for key, chunk in chunks.items()}
    location_hashes = {}
    pins, unresolved = {}, defaultdict(list)
    for req in requirements:
        chunk, document = chunks.get(req.chunk_id), documents.get(req.document_id)
        if (
            not chunk
            or not document
            or (chunk.org_id, chunk.task_id, chunk.document_id)
            != (req.org_id, req.task_id, req.document_id)
            or document.task_id != req.task_id
        ):
            pins[req.id] = None
            continue
        source = source_of(req)
        located = source_text(source, chunk_data(chunk))
        old = stored.get(req.id)
        if located is not None and located not in location_hashes:
            location_hashes[located] = raw_digest(located)
        if (
            located is None
            or citation_validity is not None
            and not citation_validity.get(req.id, False)
        ):
            pins[req.id] = None
        elif old and old.source_pin and old.source_pin.get("verifier_version") == POLICY:
            pin = make_pin(
                source,
                chunk,
                document,
                (old.source_pin["start"], old.source_pin["end"]),
                chunk_hash=chunk_hashes[chunk.id],
                location_hash=location_hashes[located],
            )
            if pin and pin.model_dump(mode="json") == old.source_pin:
                pins[req.id] = pin
            else:
                unresolved[located].append(req)
        else:
            unresolved[located].append(req)
    for located, items in unresolved.items():
        spans = locate_spans(located, [r.quote for r in items])
        for req in items:
            pins[req.id] = make_pin(
                source_of(req),
                chunks[req.chunk_id],
                documents[req.document_id],
                spans[req.quote][0],
                chunk_hash=chunk_hashes[req.chunk_id],
                location_hash=location_hashes[located],
            )
    result = {}
    for req in requirements:
        old, pin = stored.get(req.id), pins.get(req.id)
        current_hash = review_hash(req, pin)
        state = old.state if old else "legacy_unconfirmed"
        if state == "confirmed" and (pin is None or old is None or old.review_hash != current_hash):
            state = "invalidated"
        result[req.id] = EffectiveReview(
            state,
            old.revision if old else 1,
            current_hash,
            pin,
            old.confirmed_by_user_id if old and state == "confirmed" else None,
            old.confirmed_at if old and state == "confirmed" else None,
            old,
        )
    return result


BASE_ID = UUID("00000000-0000-0000-0000-000000000001")
WHEN = datetime(2026, 10, 6, tzinfo=UTC)


def uid(index):
    return UUID(int=index)


class GraphRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class GraphSession:
    """Materialized ORM graph adapter; executes no SQL and enforces read families."""

    def __init__(self, requirements, reviews, chunks, documents):
        self.requirements = requirements
        self.reviews = {row.requirement_id: row for row in reviews}
        self.chunks = chunks
        self.documents = {row.id: row for row in documents}
        self.info = {}
        self.calls = 0

    def subset(self, statement, rows, key):
        for value in statement.compile().params.values():
            if (
                isinstance(value, (list, tuple, set))
                and value
                and all(isinstance(item, UUID) for item in value)
            ):
                ids = set(value)
                return [row for row in rows if getattr(row, key) in ids]
        return list(rows)

    async def execute(self, statement):
        self.calls += 1
        entity = statement.column_descriptions[0]["entity"]
        if entity is Requirement:
            selected = self.subset(statement, self.requirements, "id")
            rows = [(row, self.reviews.get(row.id)) for row in selected]
        elif entity is Chunk:
            selected = self.subset(statement, self.chunks, "id")
            rows = [(row, self.documents.get(row.document_id)) for row in selected]
        else:
            raise AssertionError(f"Unexpected graph query entity: {entity}")
        return GraphRows(rows)

    async def scalars(self, statement):
        self.calls += 1
        entity = statement.column_descriptions[0]["entity"]
        if entity is RequirementReview:
            rows = self.subset(statement, self.reviews.values(), "requirement_id")
        elif entity is Requirement:
            rows = self.subset(statement, self.requirements, "id")
        else:
            raise AssertionError(f"Unexpected scalar query entity: {entity}")
        return GraphRows(rows)


def graph(*, quote="原文 ‘引用’：≥１６ GB", word=False, text=None):
    org, task, job, document_id, chunk_id, requirement_id = [uid(i) for i in range(1, 7)]
    location = (
        {
            "block_id": "p1",
            "kind": "paragraph",
            "section_path": ["技术要求", "说明"],
            "paragraph": 1,
            "table": None,
            "row": None,
            "column": None,
            "label": "说明 · 段落 1",
        }
        if word
        else None
    )
    located = text if text is not None else f"合成说明。\n{quote}\n结束。"
    chunk = SimpleNamespace(
        id=chunk_id,
        org_id=org,
        task_id=task,
        document_id=document_id,
        text=located,
        blocks=[{**location, "text": located}] if word else None,
        seq=1,
        page=None if word else 1,
        citation_verified=True,
    )
    document = SimpleNamespace(id=document_id, org_id=org, task_id=task, sha256="a" * 64)
    requirement = SimpleNamespace(
        id=requirement_id,
        org_id=org,
        task_id=task,
        job_id=job,
        document_id=document_id,
        chunk_id=chunk_id,
        page=None if word else 1,
        location=location,
        quote=quote,
        category="technical",
        starred=True,
        text="合成要求",
        condition={},
    )
    return requirement, chunk, document


def saved_review(requirement, chunk, document, *, state="confirmed", revision=2):
    source = source_of(requirement)
    located = source_text(source, chunk_data(chunk))
    span, _ = locate_source_citation_span(located, source.quote, source.quote)
    pin = make_pin(source, chunk, document, span)
    return SimpleNamespace(
        requirement_id=requirement.id,
        revision=revision,
        state=state,
        review_hash=review_hash(requirement, pin),
        source_pin=pin.model_dump(mode="json") if pin else None,
        confirmed_by_user_id=uid(10) if state == "confirmed" else None,
        confirmed_at=WHEN if state == "confirmed" else None,
    )


def view(review):
    return {
        "state": review.state,
        "revision": review.revision,
        "review_hash": review.review_hash,
        "source_pin": review.source_pin.model_dump(mode="json") if review.source_pin else None,
        "confirmed_by_user_id": review.confirmed_by_user_id,
        "confirmed_at": review.confirmed_at,
        "confirmed": review.confirmed,
        "citation_valid": review.citation_valid,
        "stored": vars(review.stored) if review.stored is not None else None,
    }


def state_view(review):
    return {
        key: view(review)[key]
        for key in (
            "state",
            "revision",
            "citation_valid",
            "confirmed",
            "confirmed_by_user_id",
            "confirmed_at",
            "stored",
        )
    }


CANONICAL_CASES = [
    None,
    True,
    False,
    "中文 😀 e\u0301 각 \u1100\u1161",
    '"quoted" \\ path\n\t\r\b\f\u001f',
    "\u2028\u2029 / </script>",
    0,
    -1,
    10**40,
    1.0,
    1.25,
    1e-7,
    1e21,
    -0.0,
    Decimal("-0.000"),
    Decimal("1.23000"),
    Decimal("1E+21"),
    Decimal("1E-7"),
    [1, 1.0, 1e-7, Decimal("2.500"), None, '转义"'],
    {"z": [False, {"中文": '引号"\\', "number": 1e-9}], "a": -0.0, "b": 123456789},
    {"nested": {"condition": {"minimum": Decimal("123.45600"), "enabled": True}}},
    ("tuple", 1, Decimal("0.10")),
    uid(91),
    WHEN,
]


@pytest.mark.parametrize("value", CANONICAL_CASES)
def test_canonical_bytes_and_hash_equal_frozen_reference(value):
    assert production.canonical(value) == canonical(value)
    assert production.digest(value) == digest(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_float_rejection_is_preserved(value):
    with pytest.raises(ValueError):
        canonical(value)
    with pytest.raises(ValueError):
        production.canonical(value)


@pytest.mark.parametrize("word", [False, True])
@pytest.mark.parametrize(
    "state", [None, "legacy_unconfirmed", "unconfirmed", "confirmed", "invalidated"]
)
async def test_effective_graph_and_metadata_states_equal_frozen_reference(word, state):
    requirement, chunk, document = graph(word=word)
    reviews = [] if state is None else [saved_review(requirement, chunk, document, state=state)]
    inputs = ([requirement], reviews, [chunk], [document])
    expected = await effective_reviews(GraphSession(*inputs), [requirement])
    actual = await production.effective_reviews(GraphSession(*inputs), [requirement])
    assert {key: view(value) for key, value in actual.items()} == {
        key: view(value) for key, value in expected.items()
    }
    citations = response_cards.citation_validity_batch([requirement], {chunk.id: chunk})
    metadata_session = GraphSession(*inputs)
    metadata = await production.effective_review_states(
        metadata_session, [requirement], citation_validity=citations
    )
    assert metadata_session.calls == (3 if state == "confirmed" else 1)
    for key, item in metadata.items():
        assert {
            name: getattr(item, name) for name in state_view(expected[key]) if name != "stored"
        } == {name: value for name, value in state_view(expected[key]).items() if name != "stored"}
        assert item.stored is expected[key].stored
        assert not hasattr(item, "source_pin") and not hasattr(item, "review_hash")


MUTATIONS = [
    "chunk",
    "document_hash",
    "quote",
    "location",
    "text",
    "condition",
    "category",
    "starred",
    "unverified",
    "missing_chunk",
    "missing_document",
    "foreign_task",
    "foreign_document",
    "ambiguous",
    "missing_quote",
    "stored_span",
    "stored_binding",
    "stored_policy",
    "stored_hash",
]


@pytest.mark.parametrize("mutation", MUTATIONS)
@pytest.mark.parametrize("word", [False, True])
async def test_raw_graph_differences_do_not_keep_or_revive_confirmation(word, mutation):
    requirement, chunk, document = graph(word=word)
    old = saved_review(requirement, chunk, document)
    if mutation == "chunk":
        chunk.text += " 新说明"
        if word:
            chunk.blocks[0]["text"] += " 新说明"
    elif mutation == "document_hash":
        document.sha256 = "b" * 64
    elif mutation == "quote":
        requirement.quote = "不存在的引用"
    elif mutation == "location":
        if word:
            requirement.location = {**requirement.location, "section_path": ["别处"]}
        else:
            requirement.page = 2
    elif mutation == "text":
        requirement.text += " 改变意义"
    elif mutation == "condition":
        requirement.condition = {"value": 1e-7, "nested": [Decimal("-0"), '引号"']}
    elif mutation == "category":
        requirement.category = "substantive"
    elif mutation == "starred":
        requirement.starred = False
    elif mutation == "unverified":
        chunk.citation_verified = False
    elif mutation == "foreign_task":
        chunk.task_id = uid(98)
    elif mutation == "foreign_document":
        document.task_id = uid(98)
    elif mutation in {"ambiguous", "missing_quote"}:
        new_text = (
            f"{requirement.quote}；{requirement.quote}"
            if mutation == "ambiguous"
            else "无引用合成材料"
        )
        chunk.text = new_text
        if word:
            chunk.blocks[0]["text"] = new_text
    elif mutation == "stored_span":
        old.source_pin["start"] += 1
    elif mutation == "stored_binding":
        old.source_pin["binding_sha256"] = "c" * 64
    elif mutation == "stored_policy":
        old.source_pin["verifier_version"] = "old-policy"
    elif mutation == "stored_hash":
        old.review_hash = "d" * 64
    inputs = (
        [requirement],
        [old],
        [] if mutation == "missing_chunk" else [chunk],
        [] if mutation == "missing_document" else [document],
    )
    expected = await effective_reviews(GraphSession(*inputs), [requirement])
    actual = await production.effective_reviews(GraphSession(*inputs), [requirement])
    assert view(actual[requirement.id]) == view(expected[requirement.id])
    citations = response_cards.citation_validity_batch(
        [requirement], {row.id: row for row in inputs[2]}
    )
    metadata = (
        await production.effective_review_states(
            GraphSession(*inputs), [requirement], citation_validity=citations
        )
    )[requirement.id]
    assert metadata.state == expected[requirement.id].state
    assert metadata.citation_valid == expected[requirement.id].citation_valid
    assert metadata.confirmed == expected[requirement.id].confirmed


@pytest.mark.parametrize("word", [False, True])
async def test_permanent_invalidated_state_stays_invalidated_after_change_back(word):
    requirement, chunk, document = graph(word=word)
    old = saved_review(requirement, chunk, document)
    old.state, old.confirmed_by_user_id, old.confirmed_at = "invalidated", None, None
    inputs = ([requirement], [old], [chunk], [document])
    expected = await effective_reviews(GraphSession(*inputs), [requirement])
    actual = await production.effective_reviews(GraphSession(*inputs), [requirement])
    assert view(actual[requirement.id]) == view(expected[requirement.id])
    assert not actual[requirement.id].confirmed


async def test_refreshed_raw_rows_override_stale_caller_objects_and_missing_ids_fail_closed():
    requirement, chunk, document = graph()
    old = saved_review(requirement, chunk, document)
    stale = deepcopy(requirement)
    requirement.text = "数据库已经更新的合成要求"
    inputs = ([requirement], [old], [chunk], [document])
    actual = await production.effective_reviews(GraphSession(*inputs), [stale])
    expected = await effective_reviews(GraphSession(*inputs), [stale])
    assert view(actual[requirement.id]) == view(expected[requirement.id])
    assert not actual[requirement.id].confirmed
    for fn in (effective_reviews, production.effective_reviews):
        with pytest.raises(ServiceError) as error:
            await fn(GraphSession([], [], [], []), [requirement])
        assert error.value.code == "not_found"


class SpanValidity(dict):
    def __init__(self, values, spans):
        super().__init__(values)
        self.source_spans = spans


@pytest.mark.parametrize("word", [False, True])
async def test_shared_locator_results_preserve_full_pin_and_citation_override(word):
    requirement, chunk, document = graph(word=word)
    old = saved_review(requirement, chunk, document)
    located = source_text(source_of(requirement), chunk_data(chunk))
    shared = SpanValidity(
        {requirement.id: True}, {located: locate_spans(located, [requirement.quote])}
    )
    inputs = ([requirement], [old], [chunk], [document])
    expected = await effective_reviews(
        GraphSession(*inputs), [requirement], citation_validity=shared
    )
    actual = await production.effective_reviews(
        GraphSession(*inputs), [requirement], citation_validity=shared
    )
    assert view(actual[requirement.id]) == view(expected[requirement.id])
    shared[requirement.id] = False
    expected = await effective_reviews(
        GraphSession(*inputs), [requirement], citation_validity=shared
    )
    actual = await production.effective_reviews(
        GraphSession(*inputs), [requirement], citation_validity=shared
    )
    assert view(actual[requirement.id]) == view(expected[requirement.id])


@pytest.mark.parametrize("word", [False, True])
@pytest.mark.parametrize("case", ["missing_review", "changed_source"])
async def test_precomputed_citation_spans_are_reused_on_unresolved_pin_paths(
    word, case, monkeypatch
):
    requirement, chunk, document = graph(word=word)
    reviews = [] if case == "missing_review" else [saved_review(requirement, chunk, document)]
    if case == "changed_source":
        chunk.text += "\nSynthetic changed context"
        if word:
            chunk.blocks[0]["text"] += "\nSynthetic changed context"
    inputs = ([requirement], reviews, [chunk], [document])
    citations = response_cards.citation_validity_batch([requirement], {chunk.id: chunk})
    expected = await effective_reviews(
        GraphSession(*inputs), [requirement], citation_validity=citations
    )

    def forbidden_scan(*args, **kwargs):
        raise AssertionError("The review path repeated an already completed source scan")

    monkeypatch.setattr(production, "locate_spans", forbidden_scan)
    actual = await production.effective_reviews(
        GraphSession(*inputs), [requirement], citation_validity=citations
    )
    assert view(actual[requirement.id]) == view(expected[requirement.id])
    metadata = (
        await production.effective_review_states(
            GraphSession(*inputs), [requirement], citation_validity=citations
        )
    )[requirement.id]
    assert metadata.state == expected[requirement.id].state
    assert metadata.confirmed == expected[requirement.id].confirmed


@pytest.mark.parametrize("word", [False, True])
async def test_partial_shared_span_cache_scans_only_the_missing_quote(word, monkeypatch):
    requirement, chunk, document = graph(word=word)
    second = deepcopy(requirement)
    second.id = uid(30)
    second.quote = "第二条独立合成要求"
    chunk.text += "\n" + second.quote
    if word:
        chunk.blocks[0]["text"] += "\n" + second.quote
    requirements = [requirement, second]
    located = source_text(source_of(requirement), chunk_data(chunk))
    citations = SpanValidity(
        {row.id: True for row in requirements},
        {located: locate_spans(located, [requirement.quote])},
    )
    inputs = (requirements, [], [chunk], [document])
    expected = await effective_reviews(
        GraphSession(*inputs), requirements, citation_validity=citations
    )
    calls = []
    original = production.locate_spans

    def counted_scan(text, quotes, **kwargs):
        calls.append((text, set(quotes)))
        return original(text, quotes, **kwargs)

    monkeypatch.setattr(production, "locate_spans", counted_scan)
    actual = await production.effective_reviews(
        GraphSession(*inputs), requirements, citation_validity=citations
    )
    assert {key: view(value) for key, value in actual.items()} == {
        key: view(value) for key, value in expected.items()
    }
    assert calls == [(located, {second.quote})]


@pytest.mark.parametrize("kind", ["list", "dict"])
def test_cyclic_nonjson_containers_keep_the_frozen_failure(kind):
    value = [] if kind == "list" else {}
    if kind == "list":
        value.append(value)
    else:
        value["cycle"] = value
    with pytest.raises(RecursionError):
        canonical(value)
    with pytest.raises(RecursionError):
        production.canonical(value)
