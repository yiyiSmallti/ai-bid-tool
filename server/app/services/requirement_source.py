"""Deterministic source pins shared by manual entry and effective review projection."""

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select

from app.core.errors import ServiceError, not_found
from app.models.entities import Chunk, Document, Requirement
from app.models.requirement_confirmation import RequirementReview
from app.schemas.contracts import Source
from app.schemas.requirement_confirmation import RequirementContent, VerifiedRequirementSource
from app.services.extraction import locate_source_citation_span, locate_spans, source_text

POLICY = "requirement-source-v1"


_FAST_JSON_ATOMS = frozenset((str, int, bool, type(None), UUID))


def _uses_standard_json_numbers(value):
    """Keep floats, Decimal and unusual keys on the frozen decimal encoder."""
    pending = [value]
    containers = set()
    while pending:
        current = pending.pop()
        kind = type(current)
        if kind in _FAST_JSON_ATOMS or isinstance(current, str):
            continue
        if kind is dict or kind is list or kind is tuple:
            # Repeated containers include cycles. Let the original encoder keep
            # its established behavior instead of looping in this fast scan.
            identity = id(current)
            if identity in containers:
                return False
            containers.add(identity)
        if kind is dict:
            if any(type(key) is not str for key in current):
                return False
            pending.extend(current.values())
        elif kind is list or kind is tuple:
            pending.extend(current)
        else:
            return False
    return True


def canonical(value) -> str:
    """Same canonical bytes, with C JSON encoding for ordinary non-float data."""
    if _uses_standard_json_numbers(value):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
        )
    return _canonical_precise(value)


def _canonical_precise(value) -> str:
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
            + ",".join(
                _canonical_precise(str(key)) + ":" + _canonical_precise(value[key])
                for key in sorted(value)
            )
            + "}"
        )
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_precise(item) for item in value) + "]"
    return _canonical_precise(str(value))


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
    shared_spans = getattr(citation_validity, "source_spans", {})
    for located, items in unresolved.items():
        spans = shared_spans.get(located, {})
        missing = {r.quote for r in items if r.quote not in spans}
        if missing:
            spans = {**spans, **locate_spans(located, missing)}
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


@dataclass
class ReviewStateSummary:
    """Board-only status; intentionally cannot supply a consumption hash or pin."""

    state: str
    revision: int
    citation_valid: bool
    confirmed_by_user_id: UUID | None = None
    confirmed_at: object = None
    stored: RequirementReview | None = None

    @property
    def confirmed(self):
        return self.state == "confirmed"


def _state_summary(value):
    return ReviewStateSummary(
        value.state,
        value.revision,
        value.citation_valid,
        value.confirmed_by_user_id,
        value.confirmed_at,
        value.stored,
    )


async def effective_review_states(session, requirements, *, citation_validity=None):
    """Project board states from one fresh review batch and caller-owned citations.

    The board supplies requirements and citation results from its repeatable-read
    snapshot. Only stored confirmations need full live input/hash verification.
    No result here can be used as a review/consumption manifest; those callers use
    effective_reviews. Without caller citations, use the complete path as before.
    """
    if not requirements:
        return {}
    if citation_validity is None:
        complete = await effective_reviews(session, requirements)
        return {key: _state_summary(value) for key, value in complete.items()}
    stored = {
        row.requirement_id: row
        for row in (
            await session.scalars(
                select(RequirementReview)
                .where(RequirementReview.requirement_id.in_([r.id for r in requirements]))
                .execution_options(populate_existing=True)
            )
        ).all()
    }
    confirmed = [r for r in requirements if r.id in stored and stored[r.id].state == "confirmed"]
    complete = await effective_reviews(session, confirmed, citation_validity=citation_validity)
    result = {}
    for req in requirements:
        if req.id in complete:
            result[req.id] = _state_summary(complete[req.id])
            continue
        row = stored.get(req.id)
        result[req.id] = ReviewStateSummary(
            row.state if row is not None else "legacy_unconfirmed",
            row.revision if row is not None else 1,
            bool(citation_validity.get(req.id, False)),
            stored=row,
        )
    return result
