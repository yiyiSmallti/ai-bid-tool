"""Input-bound human co-sign rounds and complete-round consumption gates."""

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import uuid4

from cryptography.fernet import InvalidToken
from sqlalchemy import func, literal, select, text, tuple_

from app.core.errors import not_found
from app.core.security import Secrets
from app.models.entities import Job, Requirement
from app.models.response_cards import ResponseCard, ResponseCardRevision
from app.models.team_workflow import (
    CardReviewRound,
    CardReviewSignature,
    RequirementWorkflow,
)
from app.schemas.response_card_contracts import ReviewDomain
from app.schemas.team_workflow import (
    CoSignConfirm,
    CoSignData,
    CoSignPolicyData,
    CoSignRoundView,
    CoSignSignatureView,
    CoSignSummary,
    PageQuery,
    RequirementCoSignPolicyView,
    ReviewRoundData,
    SignoffsData,
    SignoffsView,
    TaskRuleData,
    TaskRuleView,
)
from app.services import response_cards as cards
from app.services import task_discussion, task_workflow
from app.services.versioned import audit


def request_hash(body, card_id):
    return sha256(
        json.dumps(
            {"card_id": str(card_id), **body.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def change_audit(session, actor, task_id, action, **fields):
    audit(
        session,
        actor,
        "task." + action,
        task_id,
        {"task_id": str(task_id), "actor_kind": actor.actor_kind, **fields},
    )


async def requirement_scope(
    session, actor, task_id, requirement_id, extraction_job_id, *, write=False
):
    _, workflow, _ = await task_workflow.access(session, actor, task_id, lock=write)
    requirement = await session.scalar(
        select(Requirement)
        .join(Job, (Job.org_id == Requirement.org_id) & (Job.id == Requirement.job_id))
        .where(
            Requirement.org_id == actor.org_id,
            Requirement.task_id == task_id,
            Requirement.id == requirement_id,
            Requirement.job_id == extraction_job_id,
            Requirement.document_id == Job.document_id,
            Job.task_id == task_id,
            Job.kind == "extract",
            Job.status == "succeeded",
        )
    )
    if requirement is None:
        raise not_found()
    saved = await session.scalar(
        select(RequirementWorkflow)
        .where(
            RequirementWorkflow.org_id == actor.org_id,
            RequirementWorkflow.task_id == task_id,
            RequirementWorkflow.requirement_id == requirement_id,
            RequirementWorkflow.extraction_job_id == extraction_job_id,
        )
        .execution_options(populate_existing=True)
    )
    return workflow, requirement, saved


def rule_data(actor, workflow, *, affected=0, dry_run=False, proposed=None):
    return TaskRuleData(
        rule=TaskRuleView(
            org_id=actor.org_id,
            task_id=workflow.task_id,
            workflow_revision=workflow.revision,
            rule_revision=workflow.rule_revision,
            co_sign_starred=workflow.co_sign_starred if proposed is None else proposed,
        ),
        dry_run=dry_run,
        affected_requirements=affected,
    )


async def rule(session, actor, task_id, settings):
    _, workflow, _ = await task_workflow.access(session, actor, task_id)
    return rule_data(actor, workflow)


async def set_rule(session, actor, task_id, body, settings):
    _, workflow, _ = await task_workflow.access(
        session, actor, task_id, "task:review-policy", write=True, management=True
    )
    task_workflow.expected(workflow, body.expected_revision)
    affected = 0
    if body.co_sign_starred != workflow.co_sign_starred:
        affected = (
            await session.scalar(
                select(func.count())
                .select_from(Requirement)
                .where(
                    Requirement.org_id == actor.org_id,
                    Requirement.task_id == task_id,
                    Requirement.starred.is_(True),
                )
            )
            or 0
        )
    if body.dry_run:
        return rule_data(
            actor, workflow, affected=affected, dry_run=True, proposed=body.co_sign_starred
        )
    before = workflow.co_sign_starred
    if before != body.co_sign_starred:
        workflow.co_sign_starred = body.co_sign_starred
        workflow.rule_revision += 1
        workflow.revision += 1
        workflow.access_epoch += 1
        workflow.last_reason_ciphertext = Secrets.for_data(settings).encrypt(body.reason)
        change_audit(
            session,
            actor,
            task_id,
            "review_rule_changed",
            revision=workflow.rule_revision,
            before=before,
            after=workflow.co_sign_starred,
            reason_sha256=sha256(body.reason.encode()).hexdigest(),
        )
        await session.flush()
    return rule_data(actor, workflow, affected=affected)


async def policy_data(session, actor, workflow, requirement, saved):
    primary = await session.scalar(
        select(ResponseCardRevision.review_domain)
        .join(ResponseCard, ResponseCard.current_revision_id == ResponseCardRevision.id)
        .where(ResponseCard.org_id == actor.org_id, ResponseCard.requirement_id == requirement.id)
    )
    # An existing unclassified card remains unclassified, regardless of category.
    exists = await session.scalar(
        select(ResponseCard.id).where(
            ResponseCard.org_id == actor.org_id, ResponseCard.requirement_id == requirement.id
        )
    )
    if exists is None:
        primary = {"technical": "technical", "qualification": "commercial"}.get(
            requirement.category
        )
    explicit = saved.co_sign_required if saved else False
    required = (
        []
        if primary is None
        else ["commercial", "technical"]
        if explicit or (requirement.starred and workflow.co_sign_starred)
        else [primary]
    )
    return CoSignPolicyData(
        policy=RequirementCoSignPolicyView(
            org_id=actor.org_id,
            task_id=requirement.task_id,
            extraction_job_id=requirement.job_id,
            requirement_id=requirement.id,
            revision=saved.policy_revision if saved else 0,
            primary_domain=cast(ReviewDomain | None, primary),
            co_sign_required=explicit,
            starred=requirement.starred,
            co_sign_starred=workflow.co_sign_starred,
            task_rule_revision=workflow.rule_revision,
            required_domains=cast(list[ReviewDomain], required),
        )
    )


async def policy(session, actor, task_id, requirement_id, extraction_job_id, settings):
    workflow, requirement, saved = await requirement_scope(
        session, actor, task_id, requirement_id, extraction_job_id
    )
    return await policy_data(session, actor, workflow, requirement, saved)


async def set_policy(session, actor, task_id, requirement_id, extraction_job_id, body, settings):
    workflow, requirement, saved = await requirement_scope(
        session, actor, task_id, requirement_id, extraction_job_id, write=True
    )
    await task_workflow.access(
        session, actor, task_id, "task:review-policy", write=True, management=True
    )
    if (saved.policy_revision if saved else 0) != body.expected_policy_revision:
        task_workflow.fail("revision_conflict", "Review policy changed; refresh before retrying")
    if saved is None:
        saved = RequirementWorkflow(
            org_id=actor.org_id,
            task_id=task_id,
            extraction_job_id=extraction_job_id,
            requirement_id=requirement_id,
            assignment_revision=0,
            policy_revision=0,
            co_sign_required=False,
        )
        session.add(saved)
    before = saved.co_sign_required
    saved.co_sign_required = body.co_sign_required
    saved.policy_revision += 1
    saved.policy_changed_by_user_id = actor.user_id
    saved.policy_reason_ciphertext = Secrets.for_data(settings).encrypt(body.reason)
    change_audit(
        session,
        actor,
        task_id,
        "review_policy_changed",
        requirement_id=str(requirement_id),
        revision=saved.policy_revision,
        before=before,
        after=body.co_sign_required,
        reason_sha256=sha256(body.reason.encode()).hexdigest(),
    )
    await session.flush()
    return await policy_data(session, actor, workflow, requirement, saved)


async def projections(session, org_id, card_ids):
    """One bounded query for policy, current round and signature manifests.

    PostgreSQL revalidates pinned dependencies and current signer authority. The
    manifest deliberately omits mutable task access epochs so unrelated member
    changes cannot invalidate a still-authorized professional decision.
    """
    if not card_ids:
        return {}
    rows = await session.execute(
        text("""
        SELECT c.id, public.team_cosign_domains(c.org_id,c.id) AS domains,
          public.team_cosign_card_approved(c.org_id,c.id) AS approved,
          COALESCE(w.policy_revision,0) AS policy_revision, tw.rule_revision,
          r.id AS round_id, r.round_revision, r.task_rule_revision AS pinned_rule_revision, r.purpose, r.evidence_sha256,
          r.requirement_sha256,r.citation_sha256,r.content_sha256,
          CASE WHEN r.id IS NULL THEN false ELSE public.team_cosign_round_valid(c.org_id,r.id) END AS valid,
          COALESCE((SELECT jsonb_agg(jsonb_build_object('id',s.id,'domain',s.domain,
              'signer_user_id',s.signer_user_id,'request_sha256',s.request_sha256,
              'reason_sha256',s.reason_sha256) ORDER BY s.domain)
            FROM public.card_review_signatures s WHERE s.org_id=c.org_id AND s.round_id=r.id),'[]'::jsonb) AS signatures
        FROM public.response_cards c
        JOIN public.task_workflows tw ON tw.org_id=c.org_id AND tw.task_id=c.task_id
        LEFT JOIN public.requirement_workflows w ON w.org_id=c.org_id AND w.requirement_id=c.requirement_id
        LEFT JOIN public.card_review_rounds r ON r.org_id=c.org_id AND r.id=w.current_round_id
        WHERE c.org_id=:org AND c.id=ANY(CAST(:ids AS uuid[]))
    """),
        {"org": org_id, "ids": list(card_ids)},
    )
    output = {}
    for row in rows.mappings():
        required = list(row["domains"])
        # A fresh legacy single-domain disposition may supersede a retired
        # response round. Its real human decision is authoritative, with no
        # fabricated disposition signature or active round in the summary.
        persisted = row["round_id"] is not None and not (row["approved"] and not row["valid"])
        signed = (
            sorted(s["domain"] for s in row["signatures"] if s["domain"] in required)
            if row["valid"]
            else []
        )
        status = (
            "invalidated"
            if persisted and not row["valid"]
            else "complete"
            if persisted and row["approved"]
            else "partial"
            if signed
            else "pending"
            if persisted or len(required) > 1
            else "not_required"
        )
        summary = CoSignSummary(
            status=status,
            round_revision=row["round_revision"] if persisted else 0,
            required_domains=required,
            signed_domains=signed,
            pending_domains=[domain for domain in required if domain not in signed],
        )
        output[row["id"]] = {
            "approved": row["approved"],
            "summary": summary.model_dump(mode="json"),
            "manifest": {
                "policy_revision": row["policy_revision"],
                "task_rule_revision": row["pinned_rule_revision"] or row["rule_revision"],
                "round_id": str(row["round_id"]) if persisted else None,
                "round_revision": row["round_revision"] if persisted else 0,
                "purpose": row["purpose"] if persisted else None,
                "required_domains": required,
                "status": status,
                "evidence_sha256": row["evidence_sha256"],
                "requirement_sha256": row["requirement_sha256"],
                "citation_sha256": row["citation_sha256"],
                "content_sha256": row["content_sha256"],
                "signatures": row["signatures"] if persisted else [],
            },
        }
    return output


def manifest_fields(projection):
    """Leave untouched legacy inputs byte-compatible; bind every real round."""
    manifest = projection["manifest"]
    if (
        manifest["round_id"] is None
        and manifest["policy_revision"] == 0
        and len(manifest["required_domains"]) <= 1
    ):
        return {}
    return {"co_sign": manifest}


def apply_eligibility(view, projection):
    if not projection["approved"] and view["eligibility"] in {"eligible", "comply_only"}:
        view["eligibility"] = "needs_reconfirmation"
    return view


async def card_access(session, actor, card_id, *, write=False, domain=None):
    card = await session.scalar(
        select(ResponseCard).where(ResponseCard.org_id == actor.org_id, ResponseCard.id == card_id)
    )
    if card is None:
        raise not_found()
    _, workflow, _ = await task_workflow.access(
        session,
        actor,
        card.task_id,
        "card:cosign" if write else "card:read",
        write=write,
        domain=domain,
        require_member=write,
    )
    card, revision, requirement = await cards.require_card(session, card_id, lock=write)
    return card, revision, requirement, workflow


async def current_round(session, card, *, lock=False):
    query = (
        select(CardReviewRound)
        .join(
            RequirementWorkflow,
            (RequirementWorkflow.org_id == CardReviewRound.org_id)
            & (RequirementWorkflow.current_round_id == CardReviewRound.id),
        )
        .where(CardReviewRound.org_id == card.org_id, CardReviewRound.card_id == card.id)
    )
    if lock:
        query = query.with_for_update(of=CardReviewRound)
    return await session.scalar(query.execution_options(populate_existing=True))


async def round_view(session, row, settings):
    valid = await session.scalar(select(func.team_cosign_round_valid(row.org_id, row.id)))
    complete = valid and await session.scalar(select(func.team_cosign_complete(row.org_id, row.id)))
    try:
        reason = (
            Secrets.for_data(settings).decrypt(row.reason_ciphertext)
            if row.reason_ciphertext
            else None
        )
    except (InvalidToken, UnicodeError, ValueError):
        cards.fail("content_integrity", "Disposition reason failed integrity checks", 409, 4)
    if reason is not None and sha256(reason.encode()).hexdigest() != row.reason_sha256:
        cards.fail("content_integrity", "Disposition reason failed integrity checks", 409, 4)
    return CoSignRoundView.model_validate(
        {
            name: getattr(row, name)
            for name in CoSignRoundView.model_fields
            if name not in {"state", "disposition_reason"}
        }
        | {
            "state": "invalidated" if not valid else "complete" if complete else "open",
            "disposition_reason": reason,
        }
    )


def signature_view(row):
    return CoSignSignatureView.model_validate(
        {name: getattr(row, name) for name in CoSignSignatureView.model_fields}
    )


async def create_round(
    session,
    actor,
    card,
    revision,
    *,
    purpose="response",
    intended_disposition="respond",
    body=None,
    settings=None,
):
    if revision.review_domain is None:
        cards.fail("unclassified", "Assign a review domain before review")
    number = (
        await session.scalar(
            select(func.max(CardReviewRound.round_revision)).where(
                CardReviewRound.org_id == actor.org_id, CardReviewRound.card_id == card.id
            )
        )
        or 0
    ) + 1
    row = CardReviewRound(
        org_id=actor.org_id,
        task_id=card.task_id,
        card_id=card.id,
        requirement_id=card.requirement_id,
        extraction_job_id=card.extraction_job_id,
        card_revision_id=revision.id,
        card_revision=revision.revision,
        round_revision=number,
        purpose=purpose,
        intended_disposition=intended_disposition,
        prior_card_state=revision.state,
        created_by_user_id=actor.user_id,
        policy_revision=0,
        task_rule_revision=1,
        access_epoch=1,
        required_domains=[revision.review_domain],
        snapshot={},
        evidence_sha256="0" * 64,
        requirement_sha256="0" * 64,
        citation_sha256="0" * 64,
        content_sha256="0" * 64,
        reason_ciphertext=Secrets.for_data(settings).encrypt(body.reason) if body else None,
        reason_sha256=sha256(body.reason.encode()).hexdigest() if body else None,
        client_request_id=body.client_request_id if body else None,
        request_sha256=request_hash(body, card.id) if body else None,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    change_audit(
        session,
        actor,
        card.task_id,
        "review_round_opened",
        card_id=str(card.id),
        round_id=str(row.id),
        revision=row.round_revision,
        purpose=purpose,
    )
    return row


async def open_round(session, actor, card_id, body, settings, *, storage=None):
    card, revision, requirement, _ = await card_access(session, actor, card_id, write=True)
    cards.human(actor, revision.review_domain)
    await task_workflow.access(
        session, actor, card.task_id, "evidence:confirm", write=True, domain=revision.review_domain
    )
    original = await session.scalar(
        select(CardReviewRound).where(
            CardReviewRound.org_id == actor.org_id,
            CardReviewRound.task_id == card.task_id,
            CardReviewRound.created_by_user_id == actor.user_id,
            CardReviewRound.client_request_id == body.client_request_id,
        )
    )
    if original is not None:
        if original.request_sha256 != request_hash(body, card_id):
            task_workflow.fail("idempotency_conflict", "Request ID was used for different input")
        return ReviewRoundData(round=await round_view(session, original, settings))
    cards.expected(card, body.expected_revision)
    if revision.state not in {"draft", "rejected", "needs_material"}:
        task_workflow.fail("invalid_transition", "Withdraw or reopen before disposition review")
    if not await cards.citation_valid(session, requirement):
        task_workflow.fail("invalid_citation", "Repair the citation before review")
    row = await create_round(
        session,
        actor,
        card,
        revision,
        purpose="disposition",
        intended_disposition=body.intended_disposition,
        body=body,
        settings=settings,
    )
    return ReviewRoundData(round=await round_view(session, row, settings))


async def signoffs(session, actor, card_id, settings, *, cursor=None, limit=50):
    page = PageQuery(cursor=cursor, limit=limit)
    card, _, _, workflow = await card_access(session, actor, card_id)
    after = task_discussion.open_page(
        page.cursor, actor, card, workflow, settings, "signoffs", None
    )
    query = select(CardReviewSignature).where(
        CardReviewSignature.org_id == actor.org_id, CardReviewSignature.card_id == card.id
    )
    if after:
        query = query.where(
            tuple_(CardReviewSignature.created_at, CardReviewSignature.id)
            < tuple_(literal(after[0]), literal(after[1]))
        )
    rows = list(
        await session.scalars(
            query.order_by(
                CardReviewSignature.created_at.desc(), CardReviewSignature.id.desc()
            ).limit(page.limit + 1)
        )
    )
    data = task_discussion.page_data(actor, card, workflow, settings, "signoffs", rows, page.limit)
    current = await current_round(session, card)
    projection = (await projections(session, actor.org_id, [card.id]))[card.id]
    return SignoffsView(
        data=SignoffsData(
            **data.model_dump(),
            round=await round_view(session, current, settings)
            if current and projection["summary"]["status"] != "not_required"
            else None,
            summary=projection["summary"],
        ),
        items=[signature_view(row) for row in rows[: page.limit]],
    )


async def sign(session, actor, card_id, body, settings, *, storage=None):
    card, revision, requirement, _ = await card_access(
        session, actor, card_id, write=True, domain=body.selected_domain
    )
    cards.human(actor, body.selected_domain)
    actor.require("card:cosign")
    original = await session.scalar(
        select(CardReviewSignature).where(
            CardReviewSignature.org_id == actor.org_id,
            CardReviewSignature.task_id == card.task_id,
            CardReviewSignature.signer_user_id == actor.user_id,
            CardReviewSignature.client_request_id == body.client_request_id,
        )
    )
    digest = request_hash(body, card_id)
    if original is not None:
        if original.request_sha256 != digest:
            task_workflow.fail("idempotency_conflict", "Request ID was used for different input")
        return await signature_receipt(session, original)
    cards.expected(card, body.expected_revision)
    row = await current_round(session, card, lock=True)
    if row is None or row.round_revision != body.expected_round or row.purpose != body.purpose:
        task_workflow.fail("revision_conflict", "Review round changed; refresh before signing")
    if body.selected_domain not in row.required_domains:
        task_workflow.fail("forbidden", "Domain is not required for this review", 403, 4)
    if not await session.scalar(select(func.team_cosign_round_valid(actor.org_id, row.id))):
        task_workflow.fail("stale_review_round", "Review inputs or reviewer authority changed")
    signed = list(
        await session.scalars(
            select(CardReviewSignature).where(
                CardReviewSignature.org_id == actor.org_id, CardReviewSignature.round_id == row.id
            )
        )
    )
    if any(s.domain == body.selected_domain or s.signer_user_id == actor.user_id for s in signed):
        task_workflow.fail("already_signed", "Each domain requires one distinct human signer")
    # Lock current and prior signers before the first event-head-producing write.
    members = await task_discussion.active_members(
        session,
        actor.org_id,
        card.task_id,
        {actor.user_id, *(s.signer_user_id for s in signed)},
        lock=True,
    )
    for previous in signed:
        member = members.get(previous.signer_user_id)
        if (
            member is None
            or previous.domain not in member[0].review_domains
            or previous.domain not in task_workflow.domains(member[1])
            or member[0].role == "observer"
        ):
            task_workflow.fail("stale_review_round", "Reviewer authority changed")
    materials = await cards.linked_evidence(session, revision.id)
    if body.purpose == "response":
        await cards.validate_confirmation(
            session, actor, card, revision, requirement, body, storage
        )
    else:
        view = await cards.card_view(session, actor, card, revision, requirement, storage)
        if set(body.reviewed_warning_codes) != set(view["warning_codes"]):
            cards.fail(
                "warning_review_required", "Review every warning and record a handling reason"
            )
        if not await cards.citation_valid(session, requirement):
            task_workflow.fail("invalid_citation", "Repair the citation before review")
    reason = body.reason
    signature = CardReviewSignature(
        org_id=actor.org_id,
        task_id=card.task_id,
        card_id=card.id,
        round_id=row.id,
        purpose=row.purpose,
        domain=body.selected_domain,
        signer_user_id=actor.user_id,
        signer_org_role=actor.role,
        reviewed_evidence_ids=[str(value) for value in body.reviewed_evidence_ids]
        if body.purpose == "response"
        else [],
        reviewed_warning_codes=body.reviewed_warning_codes,
        reason_ciphertext=Secrets.for_data(settings).encrypt(reason) if reason else None,
        reason_sha256=sha256(reason.encode()).hexdigest() if reason else None,
        client_request_id=body.client_request_id,
        request_sha256=digest,
    )
    session.add(signature)
    await session.flush()
    change_audit(
        session,
        actor,
        card.task_id,
        "domain_signed",
        card_id=str(card.id),
        round_id=str(row.id),
        signature_id=str(signature.id),
        domain=signature.domain,
        revision=row.round_revision,
        client_request_id=str(body.client_request_id),
        reason_sha256=signature.reason_sha256,
    )
    if {s.domain for s in signed} | {signature.domain} == set(row.required_domains):
        if not await session.scalar(select(func.team_cosign_complete(actor.org_id, row.id))):
            task_workflow.fail(
                "stale_review_round", "Review inputs or authority changed during signing"
            )
        now = datetime.now(UTC)
        if row.purpose == "response":
            # All legacy content, warning, quote, material and image checks are
            # performed again on the final human request before Evidence writes.
            await cards.validate_confirmation(
                session, actor, card, revision, requirement, body, storage
            )
            for evidence in materials:
                if evidence.confirmed_by is None:
                    evidence.confirmed_by, evidence.confirmed_at = actor.user_id, now
                    if evidence.kind in {"certificate_pdf_page", "image_region"}:
                        evidence.quote_check = (
                            "human_page_review"
                            if evidence.kind == "certificate_pdf_page"
                            else "human_image_review"
                        )
            values = {
                "state": "confirmed",
                "confirmed_by": actor.user_id,
                "confirmed_at": now,
                "reviewed_warning_codes": body.reviewed_warning_codes,
                "reason": reason,
            }
            if revision.disposition is None:
                values |= {
                    "disposition": "respond",
                    "disposition_by": actor.user_id,
                    "disposition_at": now,
                }
            final = await cards.append_revision(
                session, actor, card, revision, values=values, evidence=materials, action="confirm"
            )
            from app.memory.feedback import record_feedback

            await record_feedback(session, actor, card, revision, final, "card_confirmed", reason)
        else:
            reason = Secrets.for_data(settings).decrypt(row.reason_ciphertext)
            if sha256(reason.encode()).hexdigest() != row.reason_sha256:
                cards.fail(
                    "content_integrity", "Disposition reason failed integrity checks", 409, 4
                )
            await cards.append_revision(
                session,
                actor,
                card,
                revision,
                values={
                    "state": revision.state,
                    "disposition": row.intended_disposition,
                    "disposition_by": actor.user_id,
                    "disposition_at": now,
                    "reason": reason,
                },
                evidence=materials,
                action="disposition",
            )
        change_audit(
            session,
            actor,
            card.task_id,
            "cosign_completed",
            card_id=str(card.id),
            round_id=str(row.id),
            revision=card.revision,
        )
    await session.refresh(signature)
    return await signature_receipt(session, signature)


async def signature_receipt(session, signature):
    """Reconstruct the exact committed receipt using the locked signing ordinal."""
    row = await session.get(CardReviewRound, signature.round_id)
    if row is None:
        raise not_found()
    signed = list(
        await session.scalars(
            select(CardReviewSignature.domain)
            .where(
                CardReviewSignature.org_id == signature.org_id,
                CardReviewSignature.round_id == row.id,
                CardReviewSignature.ordinal <= signature.ordinal,
            )
            .order_by(CardReviewSignature.domain)
        )
    )
    completed = len(signed) == len(row.required_domains)
    number = row.card_revision + int(completed)
    revision_id = await session.scalar(
        select(ResponseCardRevision.id).where(
            ResponseCardRevision.org_id == signature.org_id,
            ResponseCardRevision.card_id == row.card_id,
            ResponseCardRevision.revision == number,
        )
    )
    if revision_id is None:
        raise not_found()
    return CoSignData(
        summary=CoSignSummary(
            status="complete" if completed else "partial",
            round_revision=row.round_revision,
            required_domains=row.required_domains,
            signed_domains=signed,
            pending_domains=[domain for domain in row.required_domains if domain not in signed],
        ),
        signature=signature_view(signature),
        current_card_revision=number,
        current_card_revision_id=revision_id,
    )


async def legacy_confirm(session, actor, card, revision, requirement, body, storage, settings):
    required = await session.scalar(select(func.team_cosign_domains(actor.org_id, card.id)))
    if len(required or []) > 1:
        cards.fail("cosign_required", "Use card signoff add for every required domain", 409)
    row = await current_round(session, card, lock=True)
    if row is None:
        # Pending cards created before cutover open a real round on first review;
        # historical completed decisions are never backfilled or fabricated.
        row = await create_round(session, actor, card, revision)
    request = CoSignConfirm(
        **body.model_dump(),
        selected_domain=revision.review_domain,
        expected_round=row.round_revision,
        client_request_id=uuid4(),
    )
    await sign(session, actor, card.id, request, settings, storage=storage)
    current = await session.get(ResponseCardRevision, card.current_revision_id)
    return await cards.card_view(session, actor, card, current, requirement, storage)
