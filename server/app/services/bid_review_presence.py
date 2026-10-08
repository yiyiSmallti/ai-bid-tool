"""Local presence derivatives, exact human grants and live dispatch checks."""

import asyncio
import hashlib
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.core.errors import ServiceError, not_found
from app.models.bid_review import BidDocumentPage
from app.models.bid_review_presence import (
    BidPresenceAuthorization,
    BidPresenceAuthorizedImage,
    BidPresenceImage,
    BidPresencePreparation,
)
from app.models.bid_review_run import (
    BidReviewPublication,
    BidReviewRun,
    BidReviewSigningRequirement,
)
from app.models.team_workflow import TaskMember
from app.schemas import bid_review_presence as c
from app.services import bid_review as bids
from app.services import bid_review_privacy as privacy
from app.services.bid_presence_blur import PROFILE, derivative
from app.services.versioned import audit


async def latest(session, model, submission_id):
    ordering = (
        model.revision.desc() if model is BidPresenceAuthorization else model.created_at.desc()
    )
    return await session.scalar(
        select(model)
        .where(model.submission_id == submission_id)
        .order_by(ordering, model.id.desc())
        .limit(1)
    )


async def required_pages(session, review_id):
    publication = await session.scalar(
        select(BidReviewPublication).where(BidReviewPublication.review_id == review_id)
    )
    if publication is None:
        bids.fail("presence_review_unpublished", "A published signing review is required")
    requirements = (
        await session.scalars(
            select(BidReviewSigningRequirement).where(
                BidReviewSigningRequirement.review_id == review_id
            )
        )
    ).all()
    return publication, requirements


def location_ids(requirements, settings):
    pages = set()
    for requirement in requirements:
        detail = privacy.open_value(settings, requirement, "details_encrypted")
        if detail.get("applicability") != requirement.applicability:
            bids.fail("bid_review_output_integrity", "Signing applicability mismatch", 409, 4)
        if requirement.applicability != "applies":
            continue
        for location in detail.get("required_locations", []):
            if location.get("page_id"):
                pages.add(UUID(str(location["page_id"])))
    return pages


async def lineage(session, submission_id, review_id, settings, provider_binding):
    review = await session.get(BidReviewRun, review_id)
    if review is None or review.submission_id != submission_id:
        raise not_found()
    publication, requirements = await required_pages(session, review_id)
    fixed = await privacy.current_snapshot(session, submission_id, settings, provider_binding)
    if fixed.blockers:
        bids.fail(fixed.blockers[0], "Presence disclosure is blocked", 409, 4)
    if review.preparation_id != fixed.publication.preparation_id:
        bids.fail("presence_preparation_changed", "Prepared pages changed")
    wanted = location_ids(requirements, settings)
    pages = [
        page
        for page in fixed.pages
        if page["page_id"] in wanted
        and page["role"] == "bid"
        and page["price_classification"] == "non_price"
    ]
    return (
        fixed,
        pages,
        {
            "submission_sha256": fixed.root.manifest_sha256,
            "preparation_input_hash": fixed.publication.input_hash,
            "source_review_id": str(review_id),
            "source_publication_id": str(publication.id),
            "privacy_manifest_sha256": fixed.manifest_sha256,
            "provider_bindings_sha256": bids.digest(provider_binding),
            "profile": PROFILE,
            "pages": [
                {"page_id": str(page["page_id"]), "source_sha256": page["row"].image["sha256"]}
                for page in sorted(pages, key=lambda p: str(p["page_id"]))
            ],
        },
    )


async def prepare(session, actor, submission_id, body, settings, provider_binding, storage):
    root = await privacy.human_access(session, actor, submission_id, write=True)
    fixed, pages, binding = await lineage(
        session, submission_id, body.review_id, settings, provider_binding
    )
    if not pages:
        bids.fail(
            "presence_required_locations_missing", "No eligible confirmed required-location pages"
        )
    if len(pages) > 40:
        bids.fail(
            "presence_image_limit",
            "Presence preparation exceeds 40 pages; review remains unresolved",
        )
    await bids.replay_lock(session, actor, "presence_prepare", submission_id)
    manifest_hash = bids.digest(binding)
    existing = await session.scalar(
        select(BidPresencePreparation).where(
            BidPresencePreparation.submission_id == submission_id,
            BidPresencePreparation.manifest_sha256 == manifest_hash,
        )
    )
    if existing is None:
        prepared = BidPresencePreparation(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=root.task_id,
            submission_id=root.id,
            preparation_id=fixed.publication.preparation_id,
            source_review_id=body.review_id,
            created_by=actor.user_id,
            manifest_sha256=manifest_hash,
            expected_page_ids=sorted(str(page["page_id"]) for page in pages),
        )
        prepared.details_encrypted = bids.seal(settings, actor.org_id, prepared.id, binding)
        session.add(prepared)
        await session.flush()
        for page in sorted(pages, key=lambda p: str(p["page_id"])):
            source = page["row"]
            content = await storage.read_bounded(
                actor.org_id, source.storage_key, min(source.image["size_bytes"], 32 * 1024 * 1024)
            )
            if (
                len(content) != source.image["size_bytes"]
                or hashlib.sha256(content).hexdigest() != source.image["sha256"]
            ):
                bids.fail("presence_source_integrity", "Source page image failed integrity", 409, 4)
            jpeg, meta = await asyncio.to_thread(derivative, content)
            receipt = bids.digest(
                {**meta, "page_id": str(source.id), "manifest_sha256": manifest_hash}
            )
            key = f"org/{actor.org_id}/bid-review/{root.id}/presence/{prepared.id}/{meta['sha256']}.jpg"
            await storage.put(actor.org_id, key, jpeg)
            session.add(
                BidPresenceImage(
                    id=uuid4(),
                    org_id=actor.org_id,
                    task_id=root.task_id,
                    submission_id=root.id,
                    presence_preparation_id=prepared.id,
                    page_id=source.id,
                    privacy_receipt_sha256=receipt,
                    storage_key=key,
                    **meta,
                )
            )
        await session.flush()
        audit(
            session,
            actor,
            "bid_review.presence.prepare",
            prepared.id,
            {"submission_id": str(root.id), "manifest_sha256": manifest_hash, "pages": len(pages)},
        )
    return await preview(session, actor, submission_id, settings, provider_binding)


async def descriptors(session, preparation_id):
    images = (
        await session.scalars(
            select(BidPresenceImage)
            .where(BidPresenceImage.presence_preparation_id == preparation_id)
            .order_by(BidPresenceImage.page_id)
        )
    ).all()
    prepared = await session.get(BidPresencePreparation, preparation_id)
    if prepared is None or sorted(str(image.page_id) for image in images) != sorted(
        prepared.expected_page_ids
    ):
        bids.fail("presence_image_integrity", "Presence preparation inventory mismatch", 409, 4)
    output = []
    for image in images:
        page = await session.get(BidDocumentPage, image.page_id)
        if page is None:
            bids.fail("presence_source_integrity", "Presence image source missing", 409, 4)
        metadata = {
            key: getattr(image, key)
            for key in ("sha256", "source_sha256", "size_bytes", "width_px", "height_px", "blur")
        }
        expected_receipt = bids.digest(
            {**metadata, "page_id": str(image.page_id), "manifest_sha256": prepared.manifest_sha256}
        )
        if (
            image.source_sha256 != page.image["sha256"]
            or image.privacy_receipt_sha256 != expected_receipt
        ):
            bids.fail("presence_image_integrity", "Presence image receipt mismatch", 409, 4)
        output.append(
            c.PresenceImage(
                id=image.id,
                page_id=image.page_id,
                document_id=page.document_id,
                page=page.page,
                **{
                    key: getattr(image, key)
                    for key in (
                        "sha256",
                        "source_sha256",
                        "width_px",
                        "height_px",
                        "size_bytes",
                        "blur",
                        "privacy_receipt_sha256",
                    )
                },
            )
        )
    return output


async def live_preparation(session, prepared, settings, provider_binding):
    _, _, binding = await lineage(
        session, prepared.submission_id, prepared.source_review_id, settings, provider_binding
    )
    if (
        bids.digest(binding) != prepared.manifest_sha256
        or privacy.open_value(settings, prepared, "details_encrypted") != binding
    ):
        bids.fail(
            "presence_authorization_stale", "Presence privacy or provider binding changed", 409, 4
        )


async def preview(session, actor, submission_id, settings, provider_binding):
    await privacy.human_access(session, actor, submission_id)
    prepared = await latest(session, BidPresencePreparation, submission_id)
    grant = await latest(session, BidPresenceAuthorization, submission_id)
    result = c.PresencePreview(
        submission_id=submission_id,
        expected_revision=grant.revision + 1 if grant else 1,
        expected_authorization_id=grant.id if grant else None,
    )
    if grant is not None and grant.allow_external:
        result.revocable_authorization = await grant_view(session, grant)
    if prepared is None:
        result.blockers = ["presence_preparation_required"]
        return result
    result.source_review_id, result.manifest_sha256 = (
        prepared.source_review_id,
        prepared.manifest_sha256,
    )
    result.images = await descriptors(session, prepared.id)
    try:
        await live_preparation(session, prepared, settings, provider_binding)
    except ServiceError as error:
        result.blockers.append(error.code)
    if (
        grant
        and grant.allow_external
        and grant.presence_preparation_id == prepared.id
        and not result.blockers
    ):
        result.authorization_id, result.current = grant.id, True
    return result


async def grant_view(session, grant):
    ids = (
        await session.scalars(
            select(BidPresenceAuthorizedImage.image_id)
            .where(BidPresenceAuthorizedImage.authorization_id == grant.id)
            .order_by(BidPresenceAuthorizedImage.image_id)
        )
    ).all()
    if sorted(str(value) for value in ids) != sorted(grant.image_ids):
        bids.fail("presence_image_integrity", "Presence authorization inventory mismatch", 409, 4)
    return c.PresenceAuthorizationView(
        id=grant.id,
        revision=grant.revision,
        manifest_sha256=grant.manifest_sha256,
        image_ids=list(ids),
        allow_external=grant.allow_external,
    )


async def authorize(session, actor, submission_id, body, settings, provider_binding):
    root = await privacy.human_access(session, actor, submission_id, write=True)
    await bids.replay_lock(session, actor, "presence_authorize", submission_id)
    payload_hash = bids.digest(body.model_dump(mode="json"))
    replay = await session.scalar(
        select(BidPresenceAuthorization).where(
            BidPresenceAuthorization.authorized_by == actor.user_id,
            BidPresenceAuthorization.request_id == body.request_id,
        )
    )
    if replay:
        if replay.submission_id != submission_id or replay.payload_hash != payload_hash:
            bids.fail("idempotency_conflict", "Request ID has different input")
        return await grant_view(session, replay)
    prior = await latest(session, BidPresenceAuthorization, submission_id)
    if body.expected_revision != (
        prior.revision + 1 if prior else 1
    ) or body.expected_authorization_id != (prior.id if prior else None):
        bids.fail("presence_revision_conflict", "Presence authorization changed")
    prepared = await session.scalar(
        select(BidPresencePreparation).where(
            BidPresencePreparation.submission_id == submission_id,
            BidPresencePreparation.manifest_sha256 == body.manifest_sha256,
        )
    )
    if prepared is None:
        raise not_found()
    if body.allow_external:
        await live_preparation(session, prepared, settings, provider_binding)
    elif prior is None or prepared.id != prior.presence_preparation_id:
        bids.fail("presence_revision_conflict", "Revocation must bind the latest authorization")
    images = await descriptors(session, prepared.id)
    allowed = {image.id for image in images}
    if not set(body.image_ids) <= allowed:
        raise not_found()
    grant = BidPresenceAuthorization(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=root.task_id,
        submission_id=root.id,
        presence_preparation_id=prepared.id,
        revision=body.expected_revision,
        prior_authorization_id=body.expected_authorization_id,
        request_id=body.request_id,
        authorized_by=actor.user_id,
        payload_hash=payload_hash,
        manifest_sha256=prepared.manifest_sha256,
        image_ids=sorted(str(value) for value in body.image_ids),
        allow_external=body.allow_external,
    )
    grant.reason_encrypted = bids.seal(settings, actor.org_id, grant.id, body.reason)
    session.add(grant)
    await session.flush()
    for identifier in body.image_ids:
        session.add(
            BidPresenceAuthorizedImage(
                id=uuid4(),
                org_id=actor.org_id,
                task_id=root.task_id,
                submission_id=root.id,
                presence_preparation_id=prepared.id,
                authorization_id=grant.id,
                image_id=identifier,
            )
        )
    await session.flush()
    audit(
        session,
        actor,
        "bid_review.presence.authorize" if body.allow_external else "bid_review.presence.revoke",
        grant.id,
        {
            "submission_id": str(root.id),
            "revision": grant.revision,
            "manifest_sha256": prepared.manifest_sha256,
            "reason_sha256": privacy.text_hash(body.reason),
        },
    )
    return await grant_view(session, grant)


async def planned(session, submission_id, settings, provider_binding, authorization_id=None):
    result = {
        "authorization_id": None,
        "manifest_sha256": None,
        "source_review_id": None,
        "images": [],
        "blockers": [],
        "planned_calls": 0,
    }
    grant = await latest(session, BidPresenceAuthorization, submission_id)
    if grant is None or not grant.allow_external:
        result["blockers"] = ["presence_authorization_required"]
        return result
    if authorization_id is not None and str(grant.id) != str(authorization_id):
        result["blockers"] = ["presence_authorization_changed"]
        return result
    prepared = await session.get(BidPresencePreparation, grant.presence_preparation_id)
    try:
        if prepared is None:
            raise not_found()
        await live_preparation(session, prepared, settings, provider_binding)
        live = await session.scalar(
            select(func.bid_review_actor_live(grant.org_id, grant.task_id, grant.authorized_by))
        )
        owner = await session.scalar(
            select(TaskMember.id).where(
                TaskMember.task_id == grant.task_id,
                TaskMember.user_id == grant.authorized_by,
                TaskMember.active.is_(True),
                TaskMember.role == "owner",
            )
        )
        if not live or owner is None:
            bids.fail(
                "presence_authority_expired", "Presence grant human authority expired", 409, 4
            )
        refs = (await grant_view(session, grant)).image_ids
        images = [
            image.model_dump(mode="json")
            for image in await descriptors(session, prepared.id)
            if image.id in refs
        ]
        if not images or len(images) != len(refs):
            bids.fail("presence_image_integrity", "Authorized image inventory mismatch", 409, 4)
        result.update(
            authorization_id=str(grant.id),
            manifest_sha256=prepared.manifest_sha256,
            source_review_id=str(prepared.source_review_id),
            images=images,
            planned_calls=len(images),
        )
    except ServiceError as error:
        result["blockers"] = [error.code]
    return result


async def read_image(session, image, storage):
    content = await storage.read_bounded(image.org_id, image.storage_key, image.size_bytes)
    if len(content) != image.size_bytes or hashlib.sha256(content).hexdigest() != image.sha256:
        bids.fail("presence_image_integrity", "Presence image failed integrity", 409, 4)
    return content


async def image_content(session, actor, image_id, settings, provider_binding, storage):
    image = await session.get(BidPresenceImage, image_id)
    if image is None:
        raise not_found()
    await privacy.human_access(session, actor, image.submission_id)
    prepared = await session.get(BidPresencePreparation, image.presence_preparation_id)
    await live_preparation(session, prepared, settings, provider_binding)
    return await read_image(session, image, storage)


async def authorized_images(
    session, submission_id, settings, provider_binding, authorization_id, storage
):
    scope = await planned(session, submission_id, settings, provider_binding, authorization_id)
    if scope["blockers"]:
        bids.fail(scope["blockers"][0], "Presence disclosure is unavailable", 409, 4)
    output = []
    for descriptor in scope["images"]:
        row = await session.get(BidPresenceImage, UUID(descriptor["id"]))
        content = await read_image(session, row, storage)
        output.append({**descriptor, "image": content})
    return output
