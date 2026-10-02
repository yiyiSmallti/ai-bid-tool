"""Human review of immutable response revisions and fixed, genuine materials."""

import asyncio
import hashlib
import re
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID, uuid4

import pymupdf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import (
    ApiToken,
    CertificateRevision,
    Chunk,
    Document,
    FeatureRevision,
    Job,
    OrgProfileRevision,
    ProductRevision,
    Requirement,
    Task,
    TaskCertificate,
    TaskFeature,
    TaskOrgProfile,
    TaskResource,
    User,
)
from app.models.response_cards import CardEvidenceLink, Evidence, ResponseCard, ResponseCardRevision
from app.providers.storage import Storage
from app.schemas.response_card_contracts import (
    CardAction,
    CardClassify,
    CardContent,
    CardCreate,
    CardUpdate,
    CardView,
    DispositionBatch,
    EvidenceView,
    TaskRedactionSet,
)
from app.services.auth import ROLE_SCOPES, SCOPES, Identity, membership, set_actor_context
from app.services.evidence_sources import require_source, source_data
from app.services.extraction import locate_quote
from app.services.resources import audit

# Only explicit scalar declaration fields can be cited. URLs and identifiers are not proof.
MATERIALS = {
    "product": (
        TaskResource,
        ProductRevision,
        "task_resource_id",
        "product_revision_id",
        "resource:read",
        {"name", "vendor", "model", "model_version", "official_url", "whitepaper_url"},
    ),
    "feature": (
        TaskFeature,
        FeatureRevision,
        "task_feature_id",
        "feature_revision_id",
        "resource:read",
        {"product_id", "name", "description", "status"},
    ),
    "certificate": (
        TaskCertificate,
        CertificateRevision,
        "task_certificate_id",
        "certificate_revision_id",
        "certificate:read",
        {"kind", "name", "number", "valid_from", "valid_until"},
    ),
    "org_profile": (
        TaskOrgProfile,
        OrgProfileRevision,
        "task_org_profile_id",
        "profile_revision_id",
        "profile:read",
        {"name", "registration_details", "performance_summary", "standard_wording"},
    ),
}
CONTENT_FIELDS = ("response_kind", "response_text", "deviation", "deviation_note")
COPY_FIELDS = (
    *CONTENT_FIELDS,
    "review_domain",
    "disposition",
    "disposition_by",
    "disposition_at",
    "suggested_disposition",
    "review_hint",
)


def fail(code: str, message: str, status: int = 400, exit_code: int = 2) -> NoReturn:
    raise ServiceError(code, message, status, exit_code)


async def access(session: AsyncSession, actor: Identity, scope: str) -> Identity:
    """Recheck live membership and intersect saved worker/token grants at every entry."""
    member = await membership(session, actor.user_id, actor.org_id)
    user = await session.get(User, actor.user_id)
    if user is None or not user.active:
        raise not_found()
    scopes = actor.scopes & ROLE_SCOPES[member.role]
    if actor.token_id is not None:
        token = await session.get(ApiToken, actor.token_id)
        if (
            token is None
            or token.user_id != actor.user_id
            or token.revoked
            or token.expires_at <= datetime.now(UTC)
        ):
            fail("forbidden", "Permission denied", 403, 4)
        scopes &= set(token.scopes) & SCOPES
    live = Identity(
        actor.user_id, actor.org_id, scopes, member.role, actor.token_id, actor.actor_kind
    )
    live.require(scope)
    live.require("task:read")
    await set_actor_context(session, live)
    return live


def human(actor: Identity, domain: str | None = None, *, admin: bool = False):
    actor.require("evidence:confirm")
    if actor.actor_kind != "session" or actor.token_id is not None:
        fail("forbidden", "Human session required", 403, 4)
    if admin:
        if actor.role != "admin":
            fail("forbidden", "Administrator required", 403, 4)
    elif domain is None:
        fail("unclassified", "Assign a review domain before human decisions")
    elif actor.role != {"commercial": "bidder", "technical": "technical"}[domain]:
        fail("forbidden", "Review role does not match the assigned domain", 403, 4)


async def task_lock(session: AsyncSession, task_id: UUID) -> Task:
    task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise not_found()
    return task


async def extraction_scope(session: AsyncSession, task_id: UUID, job_id: UUID):
    if await session.get(Task, task_id) is None:
        raise not_found()
    job = await session.get(Job, job_id)
    if job is None or job.task_id != task_id or job.kind != "extract":
        raise not_found()
    if job.status != "succeeded":
        fail("invalid_extraction_job", "Choose a succeeded extraction job")
    located = list(
        (
            await session.execute(
                select(Requirement, Chunk)
                .join(Chunk, Chunk.id == Requirement.chunk_id)
                .where(
                    Requirement.job_id == job_id,
                    Requirement.task_id == task_id,
                    Requirement.document_id == job.document_id,
                )
            )
        ).all()
    )

    def position(pair):
        requirement, chunk = pair
        block_index, quote_offset = 0, chunk.text.find(requirement.quote)
        if requirement.location is not None:
            for index, block in enumerate(chunk.blocks or []):
                if block["block_id"] == requirement.location["block_id"]:
                    block_index, quote_offset = index, block["text"].find(requirement.quote)
                    break
        return chunk.seq, block_index, quote_offset, str(requirement.id)

    requirements = [requirement for requirement, _ in sorted(located, key=position)]
    if not requirements:
        fail("empty_requirements", "Extraction job has no saved requirements")
    return job, requirements


async def scope_warnings(session: AsyncSession, job_id: UUID) -> list[str]:
    job = await session.get(Job, job_id)
    if job is None:
        raise not_found()
    latest = await session.scalar(
        select(Job.id)
        .where(Job.document_id == job.document_id, Job.kind == "extract", Job.status == "succeeded")
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    warnings = [f"historical_extraction:{job_id}"] if latest != job_id else []
    if job.result.get("rejected"):
        warnings.append(f"extraction_rejected:{len(job.result['rejected'])}")
    parsed = await session.scalar(
        select(Job)
        .where(Job.document_id == job.document_id, Job.kind == "parse", Job.status == "succeeded")
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    if parsed is not None and parsed.result.get("warnings"):
        warnings.append(f"parse_warnings:{parsed.id}")
    return warnings


def source(requirement: Requirement) -> dict:
    return {
        "document_id": str(requirement.document_id),
        "chunk_id": str(requirement.chunk_id),
        "page": requirement.page,
        "location": requirement.location,
        "quote": requirement.quote,
    }


async def citation_valid(session: AsyncSession, requirement: Requirement) -> bool:
    chunk = await session.get(Chunk, requirement.chunk_id)
    if (
        chunk is None
        or not chunk.citation_verified
        or chunk.task_id != requirement.task_id
        or chunk.document_id != requirement.document_id
    ):
        return False
    if requirement.page is not None:
        return (
            chunk.page == requirement.page
            and requirement.location is None
            and requirement.quote in chunk.text
            and locate_quote(chunk.text, requirement.quote)[0] is not None
        )
    if not requirement.location or not chunk.blocks:
        return False
    return any(
        {key: value for key, value in block.items() if key != "text"} == requirement.location
        and requirement.quote in block["text"]
        and locate_quote(block["text"], requirement.quote)[0] is not None
        for block in chunk.blocks
    )


async def location_label(session: AsyncSession, requirement: Requirement) -> str:
    document = await session.get(Document, requirement.document_id)
    if document is None:
        raise not_found()
    if requirement.page is not None:
        position = f"第 {requirement.page} 页"
    elif requirement.location is not None:
        position = requirement.location["label"]
    else:
        fail("invalid_citation", "Requirement has no verifiable location")
    return f"{document.name} · {position}"


def warnings_for(requirement: Requirement) -> list[str]:
    quote = requirement.quote
    if re.search(r"提供.*(?:证书|检测报告|截图|说明书|证明|复印件)", quote, re.S) or re.search(
        r"(?:certificate|report|screenshot|proof).*(?:provid|attach)|(?:provid|attach).*(?:certificate|report|screenshot|proof)",
        quote,
        re.I | re.S,
    ):
        return ["proof_material_required"]
    return []


async def require_card(session: AsyncSession, card_id: UUID, *, lock: bool = False):
    card = await session.get(ResponseCard, card_id)
    if card is None:
        raise not_found()
    if lock:
        await task_lock(session, card.task_id)
        card = await session.scalar(
            select(ResponseCard)
            .where(ResponseCard.id == card_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    if card is None:
        raise not_found()
    revision = await session.get(ResponseCardRevision, card.current_revision_id)
    requirement = await session.get(Requirement, card.requirement_id)
    if revision is None or requirement is None:
        raise not_found()
    return card, revision, requirement


def expected(card: ResponseCard, value: int | None):
    if value != card.revision:
        fail("revision_conflict", "Read the current revision before writing", 409)


async def linked_evidence(session: AsyncSession, revision_id: UUID) -> list[Evidence]:
    return list(
        (
            await session.scalars(
                select(Evidence)
                .join(CardEvidenceLink, CardEvidenceLink.evidence_id == Evidence.id)
                .where(CardEvidenceLink.revision_id == revision_id)
                .order_by(Evidence.created_at, Evidence.id)
            )
        ).all()
    )


def page_text(content: bytes, page: int) -> str:
    with pymupdf.open(stream=content, filetype="pdf") as pdf:
        if page < 1 or page > len(pdf):
            fail("invalid_source_page", "Page is outside original PDF")
        return pdf[page - 1].get_textpage().extractText()


async def resolve_material(
    session: AsyncSession, actor: Identity, task_id: UUID, item, storage: Storage
):
    if item.kind == "certificate_pdf_page":
        archived, selected, original = await require_source(session, actor, item.evidence_source_id)
        if archived.task_id != task_id:
            raise not_found()
        if not selected.active:
            fail("stale_material", "Selected material has been replaced", 409)
        content = await storage.read(actor.org_id, original.storage_key)
        if (
            len(content) != original.file["size_bytes"]
            or hashlib.sha256(content).hexdigest() != original.file["sha256"]
        ):
            fail("source_original_integrity", "Original failed integrity checks", 502, 4)
        actual = await asyncio.to_thread(page_text, content, archived.page)
        if not actual.strip():
            fail("page_text_unavailable", "Page has no locally extractable text")
        if item.quote not in actual:
            fail(
                "invalid_evidence_quote", "Quote is not present at the specified material position"
            )
        return dict(
            task_certificate_id=selected.id,
            certificate_revision_id=selected.certificate_revision_id,
            evidence_source_id=archived.id,
            source_sha256=archived.preview["sha256"],
            page=archived.page,
            material_kind="user_supplied_pdf_page",
            quote_check="unreviewed_page",
        )
    selection_model, revision_model, selection_field, revision_field, scope, fields = MATERIALS[
        item.kind
    ]
    actor.require(scope)
    if item.field_path not in fields:
        fail("invalid_evidence_field", "Field is not a citable declaration field")
    selected = await session.get(selection_model, item.selection_id)
    if selected is None or selected.task_id != task_id:
        raise not_found()
    if not selected.active:
        fail("stale_material", "Selected material has been replaced", 409)
    revision = await session.get(revision_model, getattr(selected, revision_field))
    if revision is None:
        raise not_found()
    value = revision.data.get(item.field_path)
    if not isinstance(value, str) or item.quote not in value:
        fail("invalid_evidence_quote", "Quote is not present at the specified material position")
    return {
        selection_field: selected.id,
        revision_field: revision.id,
        "field_path": item.field_path,
        "material_kind": "declaration",
        "quote_check": "exact_field_match",
    }


async def evidence_view(session: AsyncSession, actor: Identity, row: Evidence) -> dict:
    archive = None
    if row.kind == "certificate_pdf_page":
        if row.evidence_source_id is None:
            raise not_found()
        archived, selected, original = await require_source(session, actor, row.evidence_source_id)
        archive = source_data(archived, selected, original)
        selection_id, revision_id = selected.id, selected.certificate_revision_id
        item = {"kind": row.kind, "evidence_source_id": str(archived.id), "quote": row.quote}
    else:
        model, _, selection_field, revision_field, scope, _ = MATERIALS[row.kind]
        actor.require(scope)
        selected = await session.get(model, getattr(row, selection_field))
        if selected is None:
            raise not_found()
        selection_id, revision_id = selected.id, getattr(row, revision_field)
        item = {
            "kind": row.kind,
            "selection_id": str(selection_id),
            "field_path": row.field_path,
            "quote": row.quote,
        }
    return EvidenceView.model_validate(
        dict(
            id=row.id,
            org_id=row.org_id,
            task_id=row.task_id,
            card_id=row.card_id,
            input=item,
            selection_id=selection_id,
            resource_revision_id=revision_id,
            material_kind=row.material_kind,
            quote_check=row.quote_check,
            source_archive=archive,
            confirmed_by=row.confirmed_by,
            confirmed_at=row.confirmed_at,
            active_selection=selected.active,
        )
    ).model_dump(mode="json")


def quote_hash(quote: str) -> str:
    return hashlib.sha256(quote.encode()).hexdigest()


def revision_quote_hash(revision: ResponseCardRevision, requirement: Requirement) -> str:
    # Pre-0017 revisions are immutable. Repair retains their original quote in
    # model_quote instead of rewriting those historical rows to backfill a hash.
    return revision.quote_sha256 or quote_hash(
        requirement.model_quote if requirement.model_quote is not None else requirement.quote
    )


async def card_view(
    session: AsyncSession,
    actor: Identity,
    card: ResponseCard,
    revision: ResponseCardRevision,
    requirement: Requirement,
) -> dict:
    evidence = [
        await evidence_view(session, actor, row)
        for row in await linked_evidence(session, revision.id)
    ]
    if not await citation_valid(session, requirement):
        eligibility = "invalid_citation"
    elif revision_quote_hash(revision, requirement) != quote_hash(requirement.quote):
        eligibility = "needs_reconfirmation"
    elif revision.disposition == "comply_only":
        eligibility = "comply_only"
    elif any(not row["active_selection"] for row in evidence):
        eligibility = "stale_material"
    elif revision.review_domain is None:
        eligibility = "unclassified"
    elif revision.state != "confirmed":
        eligibility = "unconfirmed"
    else:
        eligibility = "eligible"
    return CardView.model_validate(
        dict(
            id=card.id,
            org_id=card.org_id,
            task_id=card.task_id,
            extraction_job_id=card.extraction_job_id,
            requirement_id=card.requirement_id,
            revision=revision.revision,
            revision_id=revision.id,
            state=revision.state,
            review_domain=revision.review_domain,
            disposition=revision.disposition,
            disposition_by=revision.disposition_by,
            disposition_at=revision.disposition_at,
            suggested_disposition=revision.suggested_disposition,
            origin=revision.origin,
            actor_kind=revision.actor_kind,
            model_job_id=revision.model_job_id,
            review_hint=revision.review_hint,
            source=source(requirement),
            content={
                **{key: getattr(revision, key) for key in CONTENT_FIELDS},
                "evidence": [row["input"] for row in evidence],
            },
            evidence=evidence,
            confirmed_by=revision.confirmed_by,
            confirmed_at=revision.confirmed_at,
            warning_codes=warnings_for(requirement),
            reason=revision.reason,
            reviewed_warning_codes=revision.reviewed_warning_codes,
            eligibility=eligibility,
        )
    ).model_dump(mode="json")


async def append_revision(
    session: AsyncSession,
    actor: Identity,
    card: ResponseCard,
    previous: ResponseCardRevision | None,
    *,
    values: dict,
    evidence: list[Evidence],
    action: str,
    correlation_id: UUID | None = None,
):
    number = previous.revision + 1 if previous else 1
    requirement = await session.get(Requirement, card.requirement_id)
    if requirement is None:
        raise not_found()
    quote_sha256 = (
        quote_hash(requirement.quote)
        if previous is None or action in {"update", "disposition", "reopen", "withdraw"}
        else revision_quote_hash(previous, requirement)
    )
    revision = ResponseCardRevision(
        id=uuid4(),
        org_id=actor.org_id,
        card_id=card.id,
        revision=number,
        quote_sha256=quote_sha256,
        **({key: getattr(previous, key) for key in COPY_FIELDS} | values if previous else values),
        origin="human" if actor.actor_kind == "session" else "agent",
        actor_user_id=actor.user_id,
        actor_token_id=actor.token_id,
        actor_kind=actor.actor_kind,
    )
    session.add(revision)
    await session.flush()
    for material in evidence:
        session.add(
            CardEvidenceLink(
                org_id=actor.org_id,
                card_id=card.id,
                revision_id=revision.id,
                evidence_id=material.id,
            )
        )
    card.current_revision_id, card.revision = revision.id, number
    await session.flush()
    audit(
        session,
        actor,
        f"card.{action}",
        card.id,
        {
            "revision_id": str(revision.id),
            "revision": number,
            "actor_kind": actor.actor_kind,
            "old_state": previous.state if previous else None,
            "new_state": revision.state,
            "old_disposition": previous.disposition if previous else None,
            "new_disposition": revision.disposition,
            "correlation_id": str(correlation_id or uuid4()),
        },
    )
    return revision


async def new_card(
    session: AsyncSession, actor: Identity, task_id: UUID, job_id: UUID, requirement: Requirement
):
    found = await session.scalar(
        select(ResponseCard).where(ResponseCard.requirement_id == requirement.id)
    )
    if found:
        fail("revision_conflict", "A card already exists for this requirement", 409)
    card = ResponseCard(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=task_id,
        extraction_job_id=job_id,
        requirement_id=requirement.id,
        current_revision_id=uuid4(),
        revision=1,
    )
    session.add(card)
    await session.flush()
    return card


async def build_evidence(
    session: AsyncSession,
    actor: Identity,
    card: ResponseCard,
    content: CardContent,
    storage: Storage,
):
    rows = []
    for item in content.evidence:
        fixed = await resolve_material(session, actor, card.task_id, item, storage)
        row = Evidence(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=card.task_id,
            card_id=card.id,
            kind=item.kind,
            quote=item.quote,
            **fixed,
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


async def create_card(
    session: AsyncSession, actor: Identity, task_id: UUID, body: CardCreate, storage: Storage
):
    actor = await access(session, actor, "card:write")
    await task_lock(session, task_id)
    _, requirements = await extraction_scope(session, task_id, body.extraction_job_id)
    requirement = next((row for row in requirements if row.id == body.requirement_id), None)
    if requirement is None:
        raise not_found()
    card = await new_card(session, actor, task_id, body.extraction_job_id, requirement)
    materials = await build_evidence(session, actor, card, body.content, storage)
    revision = await append_revision(
        session,
        actor,
        card,
        None,
        action="create",
        evidence=materials,
        values={
            **body.content.model_dump(exclude={"evidence"}),
            "state": "draft",
            "review_domain": {"technical": "technical", "qualification": "commercial"}.get(
                requirement.category
            ),
        },
    )
    return await card_view(session, actor, card, revision, requirement)


async def update_card(
    session: AsyncSession, actor: Identity, card_id: UUID, body: CardUpdate, storage: Storage
):
    actor = await access(session, actor, "card:write")
    card, previous, requirement = await require_card(session, card_id, lock=True)
    expected(card, body.expected_revision)
    await card_view(session, actor, card, previous, requirement)
    if (
        previous.state not in {"draft", "rejected", "needs_material"}
        or previous.disposition == "comply_only"
    ):
        fail("invalid_transition", "Card cannot be edited in its current state", 409)
    materials = await build_evidence(session, actor, card, body.content, storage)
    revision = await append_revision(
        session,
        actor,
        card,
        previous,
        evidence=materials,
        action="update",
        values={
            **body.content.model_dump(exclude={"evidence"}),
            "state": "draft",
            "review_hint": None,
        },
    )
    return await card_view(session, actor, card, revision, requirement)


async def show_card(
    session: AsyncSession, actor: Identity, card_id: UUID, *, history: bool = False
):
    actor = await access(session, actor, "card:read")
    card, current, requirement = await require_card(session, card_id)
    data = await card_view(session, actor, card, current, requirement)
    if not history:
        return data
    revisions = (
        await session.scalars(
            select(ResponseCardRevision)
            .where(ResponseCardRevision.card_id == card.id)
            .order_by(ResponseCardRevision.revision)
        )
    ).all()
    return {
        "card": data,
        "history": [await card_view(session, actor, card, row, requirement) for row in revisions],
    }


async def list_cards(session: AsyncSession, actor: Identity, task_id: UUID, job_id: UUID):
    actor = await access(session, actor, "card:read")
    _, requirements = await extraction_scope(session, task_id, job_id)
    slots = []
    for requirement in requirements:
        card = await session.scalar(
            select(ResponseCard).where(ResponseCard.requirement_id == requirement.id)
        )
        view = None
        if card:
            revision = await session.get(ResponseCardRevision, card.current_revision_id)
            if revision is None:
                raise not_found()
            view = await card_view(session, actor, card, revision, requirement)
        slots.append(
            {
                "requirement_id": str(requirement.id),
                "source": source(requirement),
                "card": view,
                "status": view["state"] if view else "missing_card",
            }
        )
    return {"task_id": str(task_id), "extraction_job_id": str(job_id)}, slots


async def classify_card(session: AsyncSession, actor: Identity, card_id: UUID, body: CardClassify):
    actor = await access(session, actor, "evidence:confirm")
    human(actor, admin=True)
    card, previous, requirement = await require_card(session, card_id, lock=True)
    expected(card, body.expected_revision)
    await card_view(session, actor, card, previous, requirement)
    if previous.state != "draft" or previous.review_domain is not None:
        fail("invalid_transition", "Only an unclassified draft can be classified", 409)
    revision = await append_revision(
        session,
        actor,
        card,
        previous,
        action="classify",
        evidence=await linked_evidence(session, previous.id),
        values={
            "state": "draft",
            "review_domain": body.review_domain,
            "reason": body.reason,
        },
    )
    return await card_view(session, actor, card, revision, requirement)


async def card_action(
    session: AsyncSession, actor: Identity, card_id: UUID, body: CardAction, storage: Storage
):
    decision = body.action in {"confirm", "reject", "needs_material", "reopen"}
    actor = await access(session, actor, "evidence:confirm" if decision else "card:write")
    card, previous, requirement = await require_card(session, card_id, lock=True)
    expected(card, body.expected_revision)
    view = await card_view(session, actor, card, previous, requirement)
    if decision:
        human(actor, previous.review_domain)
    transitions = {
        "submit": ("draft", "pending_review"),
        "withdraw": ("pending_review", "draft"),
        "confirm": ("pending_review", "confirmed"),
        "reject": ("pending_review", "rejected"),
        "needs_material": ("pending_review", "needs_material"),
        "reopen": ("confirmed", "draft"),
    }
    before, after = transitions[body.action]
    if previous.state != before or previous.disposition == "comply_only":
        fail("invalid_transition", "Action is unavailable in the current state", 409)
    materials = await linked_evidence(session, previous.id)
    values = {"state": after, "reason": body.reason}
    correlation_id = uuid4()
    if body.action == "confirm":
        if view["eligibility"] in {"stale_material", "invalid_citation", "needs_reconfirmation"}:
            fail(view["eligibility"], "Card inputs are no longer valid", 409)
        if not all(getattr(previous, name) for name in CONTENT_FIELDS):
            fail(
                "incomplete_response", "Response kind, text, deviation and explanation are required"
            )
        if previous.deviation_note == "满足":
            fail("incomplete_response", "Explain the correspondence or concrete difference")
        if previous.response_kind == "evidence" and not materials:
            fail("missing_evidence", "Evidence responses require at least one material")
        if previous.response_kind == "commitment" and materials:
            fail("unexpected_evidence", "Commitments cannot contain evidence")
        if set(body.reviewed_evidence_ids) != {row.id for row in materials}:
            fail("review_mismatch", "Review every linked evidence item explicitly")
        warning_codes = set(view["warning_codes"])
        if set(body.reviewed_warning_codes) != warning_codes or (warning_codes and not body.reason):
            fail("warning_review_required", "Review every warning and record a handling reason")
        now = datetime.now(UTC)
        for row, evidence in zip(materials, view["evidence"], strict=True):
            from pydantic import TypeAdapter

            from app.schemas.response_card_contracts import EvidenceInput

            item = TypeAdapter(EvidenceInput).validate_python(evidence["input"])
            await resolve_material(session, actor, card.task_id, item, storage)
            if row.confirmed_by is None:
                row.confirmed_by, row.confirmed_at = actor.user_id, now
                if row.kind == "certificate_pdf_page":
                    row.quote_check = "human_page_review"
        values |= {
            "confirmed_by": actor.user_id,
            "confirmed_at": now,
            "reviewed_warning_codes": body.reviewed_warning_codes,
        }
        if previous.disposition is None:
            values |= {
                "disposition": "respond",
                "disposition_by": actor.user_id,
                "disposition_at": now,
            }
    revision = await append_revision(
        session,
        actor,
        card,
        previous,
        action=body.action,
        evidence=materials,
        values=values,
        correlation_id=correlation_id,
    )
    if body.action == "confirm" and previous.disposition is None:
        audit(
            session,
            actor,
            "card.disposition",
            card.id,
            {
                "old_disposition": None,
                "new_disposition": "respond",
                "action": "confirm",
                "revision_id": str(revision.id),
                "actor_kind": actor.actor_kind,
                "correlation_id": str(correlation_id),
            },
        )
    return await card_view(session, actor, card, revision, requirement)


async def dispose_cards(
    session: AsyncSession, actor: Identity, task_id: UUID, body: DispositionBatch
):
    actor = await access(session, actor, "evidence:confirm")
    # Reject a nonhuman before even empty cards can be written.
    if actor.actor_kind != "session" or actor.token_id is not None:
        fail("forbidden", "Human session required", 403, 4)
    await task_lock(session, task_id)
    _, requirements = await extraction_scope(session, task_id, body.extraction_job_id)
    by_id = {row.id: row for row in requirements}
    cards = {
        row.requirement_id: row
        for row in (
            await session.scalars(
                select(ResponseCard)
                .where(ResponseCard.extraction_job_id == body.extraction_job_id)
                .order_by(ResponseCard.id)
                .with_for_update()
            )
        ).all()
    }
    correlation = uuid4()
    output = {}
    for item in sorted(body.items, key=lambda value: str(value.requirement_id)):
        requirement = by_id.get(item.requirement_id)
        if requirement is None:
            raise not_found()
        card = cards.get(requirement.id)
        previous = None
        if card:
            expected(card, item.expected_revision)
            previous = await session.get(ResponseCardRevision, card.current_revision_id)
            if previous is None:
                raise not_found()
            await card_view(session, actor, card, previous, requirement)
            if previous.state not in {"draft", "rejected", "needs_material"}:
                fail("invalid_transition", "Withdraw or reopen before changing disposition", 409)
            domain = previous.review_domain
        else:
            if item.expected_revision is not None:
                fail("revision_conflict", "No card exists at the requested revision", 409)
            domain = {"technical": "technical", "qualification": "commercial"}.get(
                requirement.category
            )
        human(actor, domain)
        if card is None:
            card = await new_card(session, actor, task_id, body.extraction_job_id, requirement)
        revision = await append_revision(
            session,
            actor,
            card,
            previous,
            action="disposition",
            correlation_id=correlation,
            evidence=await linked_evidence(session, previous.id) if previous else [],
            values={
                "state": previous.state if previous else "draft",
                "review_domain": domain,
                "disposition": item.disposition,
                "disposition_by": actor.user_id,
                "disposition_at": datetime.now(UTC),
                "reason": item.reason,
            },
        )
        output[item.requirement_id] = await card_view(session, actor, card, revision, requirement)
    return {
        "task_id": str(task_id),
        "extraction_job_id": str(body.extraction_job_id),
        "correlation_id": str(correlation),
        "cards": [output[item.requirement_id] for item in body.items],
    }


async def set_redaction(
    session: AsyncSession, actor: Identity, task_id: UUID, body: TaskRedactionSet
):
    actor = await access(session, actor, "evidence:confirm")
    human(actor, admin=True)
    task = await task_lock(session, task_id)
    if task.model_redaction_revision != body.expected_revision:
        fail("revision_conflict", "Read the current setting revision before writing", 409)
    task.model_redaction_enabled = body.model_redaction_enabled
    task.model_redaction_revision += 1
    task.model_redaction_by = actor.user_id
    audit(
        session,
        actor,
        "task.redaction.set",
        task.id,
        {
            "model_redaction_enabled": task.model_redaction_enabled,
            "revision": task.model_redaction_revision,
            "actor_kind": actor.actor_kind,
            "correlation_id": str(uuid4()),
        },
    )
    await session.flush()
    return {
        "task_id": str(task.id),
        "model_redaction_enabled": task.model_redaction_enabled,
        "revision": task.model_redaction_revision,
        "changed_by": str(task.model_redaction_by),
    }
