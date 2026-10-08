"""Explicit privacy projections of encrypted signature and clause observations."""

import json

from sqlalchemy import func, select

from app.core.errors import ServiceError
from app.core.security import Secrets
from app.models.bid_signature import BidPDFValidation, BidSigningCandidate
from app.schemas import bid_review as c


def protected(actor) -> bool:
    return (
        actor.actor_kind == "session"
        and actor.token_id is None
        and actor.role in {"admin", "bidder"}
        and "bid-review:original:read" in actor.scopes
    )


def open_detail(settings, actor, row):
    value = json.loads(Secrets.for_data(settings).decrypt(row.details_encrypted))
    if value.get("org_id") != str(actor.org_id) or value.get("id") != str(row.id):
        raise ServiceError("bid_signature_integrity", "Signature evidence binding mismatch", 409, 4)
    return value["value"]


async def validations(session, actor, submission_id, settings):
    rows = (
        await session.scalars(
            select(BidPDFValidation)
            .where(
                BidPDFValidation.submission_id == submission_id,
                BidPDFValidation.org_id == actor.org_id,
            )
            .order_by(BidPDFValidation.document_id)
        )
    ).all()
    output = []
    for row in rows:
        detail = open_detail(settings, actor, row)
        observations = []
        for signature in detail["signatures"]:
            # Select fields from the public contract; raw field/subject names never
            # pass through dictionary expansion from a validator response.
            public = {
                key: signature[key]
                for key in c.BidSignatureObservation.model_fields
                if key in signature and key != "certificate"
            }
            certificate = signature.get("certificate")
            if certificate:
                allowed = ["fingerprint_sha256", "not_before", "not_after"]
                if protected(actor):
                    allowed += ["subject", "issuer"]
                public["certificate"] = {
                    key: certificate[key] for key in allowed if key in certificate
                }
            observations.append(c.BidSignatureObservation.model_validate(public))
        output.append(
            c.BidPDFValidationView(
                document_id=row.document_id,
                original_sha256=row.original_sha256,
                trust_store_sha256=row.trust_store_sha256,
                validator_identity=row.validator_identity,
                status=detail["status"],
                validation_time=detail["validation_time"],
                signatures=observations,
                final_revision=c.BidSignatureFinalRevision.model_validate(
                    {
                        key: value
                        for key, value in detail["final_revision"].items()
                        if key in c.BidSignatureFinalRevision.model_fields
                    }
                ),
            )
        )
    return output


async def candidates(session, actor, submission_id, settings, *, cursor=0, limit=20):
    query = select(BidSigningCandidate).where(
        BidSigningCandidate.org_id == actor.org_id,
        BidSigningCandidate.submission_id == submission_id,
    )
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = list(
        (
            await session.scalars(
                query.where(BidSigningCandidate.ordinal > cursor)
                .order_by(BidSigningCandidate.ordinal)
                .limit(limit + 1)
            )
        ).all()
    )
    output = []
    for row in rows[:limit]:
        safe = {
            key: getattr(row, key)
            for key in (
                "id",
                "document_id",
                "page_id",
                "page",
                "ordinal",
                "candidate_kind",
                "applicability",
            )
        }
        if protected(actor):
            detail = open_detail(settings, actor, row)
            safe.update(
                {
                    key: detail[key]
                    for key in (
                        "quote",
                        "start_offset",
                        "end_offset",
                        "mark_types",
                        "owner_roles",
                        "date_required",
                        "location_hint",
                    )
                }
            )
        output.append(c.BidSigningCandidateView.model_validate(safe))
    return c.BidSigningCandidatesPage(
        items=output,
        total=total or 0,
        next_cursor=rows[limit - 1].ordinal if len(rows) > limit else None,
    )
