"""Read-only preflight, actor-bound admission and immutable review sections."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.errors import ServiceError, not_found
from app.core.security import TokenSigner
from app.models.bid_review import BidDocumentPage, BidPreparedDocument
from app.models.bid_review_run import (
    BidReviewObligation,
    BidReviewPublication,
    BidReviewRun,
    BidReviewSigningRequirement,
)
from app.models.bid_signature import BidSigningCandidate
from app.models.entities import AuditLog, Job, VendorCall
from app.providers import llm as llm_providers
from app.providers.base import ProviderFailure
from app.providers.bid_reviewing import VERSION, review_provider
from app.providers.configured import model_identity
from app.providers.llm import HTTPExtractor, with_reasoning
from app.schemas import bid_review_run as c
from app.schemas.budget_contracts import BudgetPreflightData
from app.schemas.check_contracts import AssessmentJobAccepted
from app.services import bid_review as bids
from app.services import bid_review_privacy as privacy
from app.services import bid_review_text as text_review
from app.services import budget_preflight, check_semantic, task_workflow
from app.services.auth import Identity
from app.services.versioned import audit


async def resolve_binding(session, settings):
    llm = await llm_providers.resolve_llm(session, settings)
    if not isinstance(llm, HTTPExtractor):
        return llm, None
    return llm, {"model": model_identity(llm), "price": check_semantic.prices(llm)}


async def run_access(session, actor, task_id, *, write=True, lock=True):
    if actor.actor_kind not in {"session", "token", "worker"}:
        raise not_found()
    return await task_workflow.access(
        session,
        actor,
        task_id,
        scope="bid-review:run" if write else "bid-review:read",
        write=write,
        lock=lock,
        require_member=write,
    )


def worker(job):
    return Identity(
        job.actor_user_id,
        job.org_id,
        set(job.actor_scopes),
        "viewer",
        job.actor_token_id,
        "worker",
        job_id=job.id,
        run_id=job.run_id,
    )


@dataclass
class Snapshot:
    root: object
    manifest: dict
    input_hash: str
    llm: object
    requests: list
    refs: dict
    candidate_refs: dict
    candidates: list
    bid_pages: list
    blockers: list[str]
    uncovered: list[str]


async def snapshot(session, actor, task_id, body, settings):
    root = await bids.required(session, body.submission_id, task_id)
    blockers, uncovered = [], []
    llm, binding = None, None
    try:
        llm, binding = await resolve_binding(session, settings)
        if binding is None:
            blockers.append("provider_unavailable")
    except (ServiceError, ProviderFailure) as error:
        blockers.append(error.code)
    manifest = {
        "org_id": str(actor.org_id),
        "task_id": str(task_id),
        "submission_id": str(root.id),
        "submission_revision": root.revision,
        "submission_manifest_sha256": root.manifest_sha256,
        "assessment_date": body.assessment_date.isoformat(),
        "scope": "uploaded_bid",
        "review_slice": "compliance",
        "rule_version": VERSION,
        "prompt_version": VERSION,
        "schema_version": VERSION,
        "provider_binding": binding,
        "reasoning": body.reasoning,
        "clef_enabled": False,
        "limits": {
            "llm_calls": min(100, settings.job_max_vendor_calls),
            "obligations": 2000,
            "required_locations": 10000,
        },
        "max_charge": str(body.max_charge) if body.max_charge is not None else None,
    }
    requests, refs, candidate_refs, candidates, bid_pages = [], {}, {}, [], []
    fixed = None
    try:
        fixed = await privacy.current_snapshot(session, root.id, settings, binding)
    except ServiceError as error:
        if error.code != "bid_preparation_missing":
            raise
        blockers.append(error.code)
    if fixed is not None:
        manifest.update(
            preparation_id=str(fixed.publication.preparation_id),
            preparation_input_hash=fixed.publication.input_hash,
            redaction_manifest_sha256=fixed.manifest_sha256,
            redaction_revision=fixed.redaction_revision,
            confidential_binding_sha256=fixed.confidential_binding_sha256,
            derived_name_lists_sha256=fixed.derived_name_lists_sha256,
            provider_bindings_sha256=fixed.provider_bindings_sha256,
            privacy_manifest=fixed.manifest,
        )
        blockers += fixed.blockers
        selected = None
        try:
            selected = await privacy.authorized_snapshot(session, root.id, settings, binding)
        except ServiceError as error:
            if error.code in {"bid_redaction_integrity", "bid_file_integrity"}:
                raise
            blockers.append(error.code)
        originals = {
            str(p.id): p
            for p in (
                await session.scalars(
                    select(BidDocumentPage).where(BidDocumentPage.submission_id == root.id)
                )
            ).all()
        }
        prepared = {
            str(p.document_id): p
            for p in (
                await session.scalars(
                    select(BidPreparedDocument).where(BidPreparedDocument.submission_id == root.id)
                )
            ).all()
        }
        bid_pages = [
            {"page_id": str(p.id), "document_id": str(p.document_id), "page": p.page}
            for p in originals.values()
            if p.role == "bid"
        ]
        documents = await bids.rows(session, root.id)
        doc_order = {str(d.id): d.ordinal for d in documents}
        bid_pages.sort(key=lambda p: (doc_order[p["document_id"]], p["page"]))
        manifest["tender_document_ids"] = [str(d.id) for d in documents if d.role == "tender"]
        manifest["bid_document_ids"] = [str(d.id) for d in documents if d.role == "bid"]
        manifest["page_count"] = len(originals)
        manifest["tender_page_count"] = sum(p.role == "tender" for p in originals.values())
        manifest["bid_page_count"] = len(bid_pages)
        for candidate in (
            await session.scalars(
                select(BidSigningCandidate)
                .where(BidSigningCandidate.submission_id == root.id)
                .order_by(BidSigningCandidate.ordinal)
            )
        ).all():
            candidates.append(
                {
                    "id": str(candidate.id),
                    "ordinal": candidate.ordinal,
                    "page_id": str(candidate.page_id),
                    "detail": privacy.open_value(settings, candidate, "details_encrypted"),
                }
            )
        manifest["signing_candidates_sha256"] = bids.digest(candidates)
        pages = []
        if selected is not None and selected.authorization is not None:
            manifest["outbound_authorization_id"] = str(selected.authorization.id)
            manifest["authorized_sanitized_context_sha256"] = (
                selected.authorization.authorized_sanitized_context_sha256
            )
            for page in selected.pages:
                if page["role"] != "tender" or page["price_page"] or not page["outbound_eligible"]:
                    continue
                source = originals[str(page["page_id"])]
                mapped = {
                    "page_id": str(source.id),
                    "document_id": str(source.document_id),
                    "page": source.page,
                    "text": page["sanitized_text"],
                    "original": privacy.open_value(settings, source, "text_encrypted"),
                }
                document = prepared[str(source.document_id)]
                if document.citation_mode == "block":
                    structure = privacy.open_value(settings, document, "structure_encrypted")
                    mapping = {
                        b["block_id"]
                        for b in structure["block_page_mapping"]
                        if b["page"] == source.page and b["mapping_status"] == "exact_text"
                    }
                    mapped["word_blocks"] = [
                        {
                            "text": b["text"],
                            "location": {
                                **{
                                    key: b[key]
                                    for key in (
                                        "block_id",
                                        "kind",
                                        "paragraph",
                                        "table",
                                        "row",
                                        "column",
                                    )
                                    if key in b
                                },
                                "section_path": [
                                    heading if heading in mapped["text"] else "[已遮挡章节]"
                                    for heading in b.get("section_path", [])
                                ],
                                "label": b["block_id"],
                            },
                        }
                        for section in structure["sections"]
                        for b in section["blocks"]
                        if b["block_id"] in mapping
                    ]
                pages.append(mapped)
        if not pages:
            blockers.append("outbound_authorization_required")
        if isinstance(llm, HTTPExtractor):
            llm, reasoning, warnings = with_reasoning(llm, body.reasoning)
            manifest["reasoning"] = reasoning
            uncovered += warnings
            requests, refs, candidate_refs, oversize = text_review.build_requests(
                pages, candidates, llm.settings.llm_batch_chars
            )
            if oversize:
                blockers.append("bid_review_page_context_limit")
        if len(pages) < manifest["tender_page_count"]:
            uncovered.append("tender_pages_excluded")
        manifest["authorized_tender_page_ids"] = [p["page_id"] for p in pages]
        manifest["request_hashes"] = [
            bids.digest(request.model_dump(mode="json")) for request in requests
        ]
        manifest["prepared_warnings"] = sorted(
            {warning for doc in prepared.values() for warning in doc.parsing_warnings}
        )
        if manifest["prepared_warnings"]:
            uncovered.append("preparation_coverage_partial")
    manifest["required_calls"] = len(requests)
    manifest["planned_calls"] = min(len(requests), manifest["limits"]["llm_calls"])
    if len(requests) > manifest["planned_calls"]:
        uncovered.append("llm_call_ceiling_will_limit_coverage")
    return Snapshot(
        root,
        manifest,
        bids.digest(manifest),
        llm,
        requests,
        refs,
        candidate_refs,
        candidates,
        bid_pages,
        sorted(set(blockers)),
        sorted(set(uncovered)),
    )


def receipt_binding(actor, task_id, body, input_hash):
    return {
        "purpose": "bid_review",
        "org_id": str(actor.org_id),
        "task_id": str(task_id),
        "actor_user_id": str(actor.user_id),
        "actor_token_id": str(actor.token_id) if actor.token_id else None,
        "actor_kind": actor.actor_kind,
        "request_id": str(body.request_id),
        "input_hash": input_hash,
    }


async def submit(session, actor, task_id, body, queue, settings):
    await run_access(session, actor, task_id, lock=not body.dry_run)
    fixed = await snapshot(session, actor, task_id, body, settings)
    blockers = list(fixed.blockers)
    quotes = []
    if isinstance(fixed.llm, HTTPExtractor):
        adapter = review_provider(fixed.llm)
        try:
            quotes = [
                fixed.llm.quote(adapter.request_body(request))
                for request in fixed.requests[: fixed.manifest["planned_calls"]]
            ]
        except ProviderFailure as error:
            blockers.append(error.code)
    attached = await budget_preflight.attach(
        session,
        {},
        command="review run",
        task_id=task_id,
        input_hash=fixed.input_hash,
        currency=settings.billing_currency,
        settings=settings,
        quotes=quotes,
        planned_calls=fixed.manifest["planned_calls"] if not blockers else None,
        max_charge=body.max_charge,
    )
    budget = BudgetPreflightData.model_validate(attached["budget_preflight"])
    budget.maximum_calls = fixed.manifest["limits"]["llm_calls"]
    budget.as_of = budget.as_of.replace(microsecond=0)
    if budget.admission_blocker:
        blockers.append(budget.admission_blocker)
    expiry = budget.as_of + timedelta(seconds=900)
    signer = TokenSigner.for_tokens(settings)
    binding = receipt_binding(actor, task_id, body, fixed.input_hash)
    if body.dry_run:
        return c.BidReviewPreview(
            input={**fixed.manifest, "input_hash": fixed.input_hash},
            budget=budget,
            expires_at=expiry,
            preflight_token=signer.issue(binding, 900, expires_at=int(expiry.timestamp())),
            admission_blockers=sorted(set(blockers)),
            uncovered_codes=fixed.uncovered,
        )
    if body.expected_input_hash != fixed.input_hash:
        bids.fail("bid_review_input_changed", "Review inputs changed; preview again")
    try:
        signed = signer.open(body.preflight_token)
    except ServiceError:
        bids.fail("bid_preflight_expired", "Review receipt is invalid or expired; preview again")
    if any(signed.get(key) != value for key, value in binding.items()):
        bids.fail("bid_preflight_mismatch", "Review receipt does not match the actor or request")
    if blockers:
        bids.fail(blockers[0], "Review is blocked; inspect the current preview", 409, 4)
    await bids.replay_lock(session, actor, "review", body.request_id)
    payload_hash = bids.digest(binding | {"retry": body.retry})
    prior = await session.scalar(
        select(AuditLog).where(
            AuditLog.action == "bid_review.submit",
            AuditLog.actor_user_id == actor.user_id,
            AuditLog.details["request_id"].astext == str(body.request_id),
        )
    )
    if prior is not None and prior.details.get("payload_hash") != payload_hash:
        bids.fail("idempotency_conflict", "Request ID has different input")
    cache = bids.digest(
        {
            "kind": "bid_review",
            "input_hash": fixed.input_hash,
            "actor": {
                key: binding[key]
                for key in ("org_id", "task_id", "actor_user_id", "actor_token_id", "actor_kind")
            },
        }
    )
    job = await session.scalar(select(Job).where(Job.cache_key == cache).with_for_update())
    cached = job is not None
    if job is not None:
        if body.retry and prior is None:
            if job.status not in {"failed", "cancelled"}:
                bids.fail("bid_retry_not_terminal", "Only failed or cancelled work can be retried")
            holds = await session.scalar(
                select(VendorCall.id)
                .where(VendorCall.job_id == job.id, VendorCall.state.in_(["pending", "unknown"]))
                .limit(1)
            )
            if holds:
                bids.fail(
                    "bid_review_unresolved_usage", "Reconcile unknown usage before retry", 409, 4
                )
            job.status, job.error, job.finished_at, job.queue_id = "queued", None, None, None
            job.run_id, job.lease_until = None, None
    else:
        if body.retry:
            bids.fail("bid_retry_missing", "No previous review exists")
        identifier = uuid4()
        job = Job(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task_id,
            document_id=None,
            bid_submission_document_id=UUID(fixed.manifest["tender_document_ids"][0]),
            kind="bid_review",
            cache_key=cache,
            status="queued",
            actor_user_id=actor.user_id,
            actor_token_id=actor.token_id,
            actor_kind=actor.actor_kind,
            actor_scopes=sorted(actor.scopes),
            provider_identity=model_identity(fixed.llm),
            provider_config_id=getattr(fixed.llm, "provider_config_id", None),
            reasoning=fixed.manifest["reasoning"],
            result={
                "submission": {
                    "review_id": str(identifier),
                    "submission_id": str(body.submission_id),
                    "input_hash": fixed.input_hash,
                    "input_manifest": fixed.manifest,
                    "request": body.model_dump(
                        mode="json", exclude={"preflight_token", "expected_input_hash"}
                    ),
                    "max_charge": fixed.manifest["max_charge"],
                }
            },
        )
        session.add(job)
        await session.flush()
        session.add(
            BidReviewRun(
                id=identifier,
                org_id=actor.org_id,
                task_id=task_id,
                submission_id=body.submission_id,
                preparation_id=UUID(fixed.manifest["preparation_id"]),
                tender_document_id=job.bid_submission_document_id,
                authorization_id=UUID(fixed.manifest["outbound_authorization_id"]),
                job_id=job.id,
                request_id=body.request_id,
                created_by=actor.user_id,
                input_hash=fixed.input_hash,
                payload_hash=payload_hash,
                manifest=fixed.manifest,
            )
        )
        await session.flush()
    await bids.enqueue(session, queue, job)
    if prior is None:
        audit(
            session,
            actor,
            "bid_review.submit",
            job.id,
            {
                "task_id": str(task_id),
                "job_id": str(job.id),
                "request_id": str(body.request_id),
                "input_hash": fixed.input_hash,
                "payload_hash": payload_hash,
            },
        )
    return AssessmentJobAccepted.model_validate(
        {"job_id": job.id, "status": job.status, "cached": cached}
    )


async def job_access(session, actor, job, *, cancel=False):
    row = await session.scalar(
        select(BidReviewRun).where(
            BidReviewRun.job_id == job.id, BidReviewRun.task_id == job.task_id
        )
    )
    if row is None:
        raise not_found()
    await run_access(session, actor, row.task_id, write=cancel, lock=cancel)
    if cancel and (job.actor_user_id != actor.user_id or job.actor_token_id != actor.token_id):
        raise not_found()


async def run_view(session, row):
    job = await session.get(Job, row.job_id)
    publication = await session.scalar(
        select(BidReviewPublication).where(BidReviewPublication.review_id == row.id)
    )
    return c.BidReviewRunView(
        id=row.id,
        task_id=row.task_id,
        submission_id=row.submission_id,
        job_id=row.job_id,
        status=job.status,
        completion=publication.completion if publication else None,
        input_hash=row.input_hash,
        created_at=row.created_at,
        coverage={
            key: value
            for key, value in publication.coverage.items()
            if not key.endswith("_page_ids")
        }
        if publication
        else {},
        uncovered_codes=publication.uncovered_codes if publication else [],
    )


def cursor_binding(actor, resource, section):
    return {
        "purpose": "bid_review_cursor",
        "org_id": str(actor.org_id),
        "user_id": str(actor.user_id),
        "token_id": str(actor.token_id) if actor.token_id else None,
        "actor_kind": actor.actor_kind,
        "resource": str(resource),
        "section": section,
    }


def offset(actor, resource, section, cursor, settings):
    if cursor is None:
        return 0
    payload = TokenSigner.for_tokens(settings).open(cursor)
    if (
        any(payload.get(k) != v for k, v in cursor_binding(actor, resource, section).items())
        or not isinstance(payload.get("offset"), int)
        or payload["offset"] < 0
    ):
        bids.fail("invalid_cursor", "Review cursor does not match this view", 400, 2)
    return payload["offset"]


def next_cursor(actor, resource, section, value, settings):
    return TokenSigner.for_tokens(settings).issue(
        {**cursor_binding(actor, resource, section), "offset": value}, 900
    )


async def list_runs(session, actor, task_id, settings, *, cursor=None, limit=50):
    await run_access(session, actor, task_id, write=False, lock=False)
    start = offset(actor, task_id, "list", cursor, settings)
    rows = list(
        (
            await session.scalars(
                select(BidReviewRun)
                .where(BidReviewRun.task_id == task_id)
                .order_by(BidReviewRun.created_at.desc(), BidReviewRun.id.desc())
                .offset(start)
                .limit(limit + 1)
            )
        ).all()
    )
    views, validity = [], {}
    for row in rows[:limit]:
        key = (row.submission_id, row.authorization_id, row.manifest["redaction_manifest_sha256"])
        if key not in validity:
            validity[key] = await privacy_validity(session, row, settings)
        view = await run_view(session, row)
        view.validity = "current" if validity[key] else "stale"
        views.append(view)
    return c.BidReviewListData(
        task_id=task_id,
        next_cursor=next_cursor(actor, task_id, "list", start + limit, settings)
        if len(rows) > limit
        else None,
    ), views


async def privacy_validity(session, row, settings):
    try:
        _, binding = await resolve_binding(session, settings)
        current = await privacy.authorized_snapshot(
            session, row.submission_id, settings, binding, authorization_id=row.authorization_id
        )
        return current.manifest_sha256 == row.manifest["redaction_manifest_sha256"]
    except (ServiceError, ProviderFailure):
        return False


async def show(
    session, actor, review_id, settings, *, section="obligations", cursor=None, limit=50
):
    row = await session.get(BidReviewRun, review_id)
    if row is None:
        raise not_found()
    await run_access(session, actor, row.task_id, write=False, lock=False)
    view = await run_view(session, row)
    # Historical protected records remain readable; cleared readers cannot recover
    # text that a newer identity list or confidentiality setting now protects.
    privacy_current = await privacy_validity(session, row, settings)
    view.validity = "current" if privacy_current else "stale"
    data = c.BidReviewDetail(run=view)
    if view.completion is None:
        return data
    # Tokens receive metadata only. Human readers receive only verified, already sanitized excerpts.
    if actor.token_id is not None or actor.actor_kind != "session":
        return data
    actor.require("bid-review:source:read")
    if not privacy_current and not (
        actor.role in {"admin", "bidder"} and "bid-review:original:read" in actor.scopes
    ):
        return data
    start = offset(actor, review_id, section, cursor, settings)
    model = BidReviewObligation if section == "obligations" else BidReviewSigningRequirement
    rows = list(
        (
            await session.scalars(
                select(model)
                .where(model.review_id == row.id)
                .order_by(model.ordinal)
                .offset(start)
                .limit(limit + 1)
            )
        ).all()
    )
    values, size = [], 0
    for item in rows[:limit]:
        value = privacy.open_value(settings, item, "details_encrypted")
        if (
            isinstance(item, BidReviewSigningRequirement)
            and value.get("applicability") != item.applicability
        ):
            bids.fail(
                "bid_review_output_integrity", "Signing applicability binding mismatch", 409, 4
            )
        cls = (
            c.BidReviewObligation if section == "obligations" else c.BidConfirmedSigningRequirement
        )
        public = cls.model_validate({key: value[key] for key in cls.model_fields if key in value})
        item_size = len(public.model_dump_json().encode())
        if item_size > 950000:
            bids.fail("bid_review_output_limit", "Review item exceeds response bound", 400, 4)
        if size + item_size > 950000:
            break
        size += item_size
        values.append(public)
    setattr(data, section, values)
    if len(rows) > len(values):
        data.next_cursor = next_cursor(actor, review_id, section, start + len(values), settings)
    return data
