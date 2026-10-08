"""Call-free local text privacy and exact, append-only human outbound authority."""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, select

from app.core.security import Secrets
from app.models.bid_review import BidDocumentPage, BidPreparationPublication, BidSubmission
from app.models.bid_review_privacy import (
    BidOutboundAuthorization,
    BidOutboundAuthorizedPage,
    BidRedactedPage,
    BidRedactionSnapshot,
    BidReviewNameList,
)
from app.models.bid_signature import BidPDFValidation
from app.models.entities import Task
from app.models.team_workflow import TaskMember
from app.schemas import bid_review_privacy as c
from app.services import bid_review as bids
from app.services import confidential, redaction, task_workflow
from app.services.versioned import audit

POLICY_VERSION = redaction.RULE_VERSION + ":uploaded-text-v1"
PRICE_WORDS = re.compile(r"报价|开标一览表|分项报价|大写金额|小写金额|合计|单价|总价")
AMOUNTS = re.compile(r"(?<![\dA-Za-z])\d[\d,]*(?:\.\d{1,2})?(?![\dA-Za-z])")
NAME_LABELS = re.compile(
    r"(?:投标人(?:名称)?|供应商(?:名称)?|投标单位|公司名称|单位名称|法定代表人|法人代表|授权代表|委托代理人|授权代理人|联系人|项目负责人|姓名)\s*[:：=]\s*([^\n\r;；,，。:：()（）]{1,100})"
)
UNCERTAIN_LABEL = re.compile(r"投标人|供应商|法定代表人|授权代表|代理人|联系人|姓名")


def text_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def open_value(settings, row, column):
    ciphertext = getattr(row, column)
    if ciphertext is None or len(ciphertext) > 32 * 1024 * 1024:
        bids.fail("bid_redaction_integrity", "Encrypted privacy input exceeds bounds", 409, 4)
    value = json.loads(Secrets.for_data(settings).decrypt(ciphertext))
    if value.get("org_id") != str(row.org_id) or value.get("id") != str(row.id):
        bids.fail("bid_redaction_integrity", "Encrypted privacy data binding mismatch", 409, 4)
    return value["value"]


def normalize(value):
    return unicodedata.normalize("NFKC", value).strip()


def classify_price(text, kind, native):
    """Conservative deterministic classification. Unreadable/mixed pages stay excluded."""
    if kind == "price" or PRICE_WORDS.search(text):
        return "price"
    if not native or not text.strip():
        return "uncertain"
    amounts = AMOUNTS.findall(text)
    density = sum(len(item) for item in amounts) / max(len(text), 1)
    if len(amounts) >= 4 and density >= 0.08:
        return "price"
    if len(amounts) >= 3 and density >= 0.04:
        return "uncertain"
    return "non_price"


@dataclass
class PrivacySnapshot:
    root: BidSubmission
    publication: BidPreparationPublication
    manifest: dict
    manifest_sha256: str
    redaction_revision: int
    confidential_binding_sha256: str
    derived_name_lists_sha256: str
    provider_bindings_sha256: str
    pages: list[dict]
    names: dict
    blockers: list[str]
    authorization: BidOutboundAuthorization | None = None
    human_name_revision: int = 0


async def latest_grant(session, submission_id):
    return await session.scalar(
        select(BidOutboundAuthorization)
        .where(BidOutboundAuthorization.submission_id == submission_id)
        .order_by(BidOutboundAuthorization.revision.desc())
        .limit(1)
    )


async def current_snapshot(session, submission_id, settings, provider_binding):
    """Recompute every input on reads/admission; no persisted preview, no provider call."""
    root = await bids.required(session, submission_id)
    task = await session.get(Task, root.task_id)
    publication = await session.scalar(
        select(BidPreparationPublication).where(BidPreparationPublication.submission_id == root.id)
    )
    if publication is None:
        bids.fail("bid_preparation_missing", "Local preparation must complete before text review")
    documents = {row.id: row for row in await bids.rows(session, root.id)}
    rows = list(
        (
            await session.scalars(
                select(BidDocumentPage)
                .where(
                    BidDocumentPage.submission_id == root.id,
                    BidDocumentPage.preparation_id == publication.preparation_id,
                )
                .order_by(BidDocumentPage.document_id, BidDocumentPage.page)
            )
        ).all()
    )
    if len(rows) != publication.page_count or len(rows) > 1000:
        bids.fail("bid_redaction_integrity", "Prepared page inventory is incomplete", 409, 4)
    original = {}
    names = {"bidder_names": set(), "staff_names": set()}
    uncertain_names = set()
    for page in rows:
        text = open_value(settings, page, "text_encrypted") if page.text_encrypted else ""
        if page.text_status == "native" and text_hash(text) != page.text_sha256:
            bids.fail("bid_redaction_integrity", "Prepared page text hash mismatch", 409, 4)
        original[page.id] = text
        # Cover/declaration discovery is local. Other bid pages are also scanned
        # for explicit labelled names, without guessing arbitrary prose names.
        if page.role == "bid":
            for match in NAME_LABELS.finditer(normalize(text)):
                value = match[1].strip()
                if value and not re.fullmatch(r"[_\s\-—]+", value):
                    key = (
                        "bidder_names"
                        if re.search(r"投标人|供应商|单位|公司", match[0])
                        else "staff_names"
                    )
                    names[key].add(value)
                    if len(value) <= 2:
                        uncertain_names.add(value)
    validations = (
        await session.scalars(
            select(BidPDFValidation).where(
                BidPDFValidation.submission_id == root.id,
                BidPDFValidation.preparation_id == publication.preparation_id,
            )
        )
    ).all()
    for row in validations:
        if documents[row.document_id].role != "bid":
            continue
        for signature in open_value(settings, row, "details_encrypted").get("signatures", []):
            subject = (signature.get("certificate") or {}).get("subject", "")
            for match in re.finditer(r"(?:^|,)\s*(CN|O|OU)=((?:\\.|[^,])+)", subject):
                value = normalize(re.sub(r"\\(.)", r"\1", match[2]))
                if value:
                    names["bidder_names" if match[1] == "O" else "staff_names"].add(value)
                    if len(value) <= 2:
                        uncertain_names.add(value)
    human = await session.scalar(
        select(BidReviewNameList)
        .where(BidReviewNameList.submission_id == root.id)
        .order_by(BidReviewNameList.revision.desc())
        .limit(1)
    )
    if human:
        for key, values in open_value(settings, human, "names_encrypted").items():
            names[key].update(values)
    names = {key: sorted(values) for key, values in names.items()}
    if sum(len(values) for values in names.values()) > 1000:
        bids.fail(
            "bid_name_list_limit", "Local identity discovery exceeds the bounded name list", 409, 4
        )
    names_hash = bids.digest({"derived": names, "human_revision": human.revision if human else 0})
    entries = await confidential.task_entries(session, root.task_id)
    crypto = Secrets.for_data(settings)
    library = confidential.library(entries, crypto)
    seen = {item.key for item in library}
    confidential_notes = False
    # Even a one-character registered secret must not leak. Human review exposes
    # the over-masking/uncertainty rather than silently treating it as safe.
    for key, entry in entries.items():
        if entry.value is not None and key not in seen:
            value = normalize(crypto.decrypt(entry.value.encrypted_value))
            if value:
                library.append(redaction.LibraryValue(key, re.compile(re.escape(value))))
                confidential_notes = True
    confidential_hash = bids.digest(
        [
            {
                "field_id": str(entry.field.id),
                "field_revision": entry.field.revision,
                "value_id": str(entry.value.id) if entry.value else None,
            }
            for _, entry in sorted(entries.items())
        ]
    )
    for values in names.values():
        for name in values:
            library.append(
                redaction.LibraryValue("bid_review_identity", re.compile(re.escape(name)))
            )
    pages = []
    for page in rows:
        text = original[page.id]
        sanitized, counts = redaction.redact(text, True, library)
        # Internal confidential keys/name labels do not identify outbound people.
        sanitized = sanitized.replace("{{secret.bid_review_identity}}", "[REDACTED_NAME]")
        # Registered field keys may themselves contain identities. Review never
        # needs reversible secret references, so no key leaves the local service.
        sanitized = redaction.SECRET_PLACEHOLDER.sub("[REDACTED_CONFIDENTIAL]", sanitized)
        classification = classify_price(
            text,
            documents[page.document_id].kind,
            page.text_status == "native" and page.page_kind == "text",
        )
        notes = []
        if any(name in normalize(text) for name in uncertain_names) or any(
            len(name) <= 2 and name in normalize(text)
            for values in names.values()
            for name in values
        ):
            notes.append("short_name_requires_human_review")
        if page.role == "bid" and UNCERTAIN_LABEL.search(text):
            notes.append("identity_area_requires_human_review")
        if confidential_notes:
            notes.append("short_confidential_value_requires_human_review")
        if page.text_status != "native" or page.page_kind != "text":
            notes.append("text_unavailable_human_only")
        if classification == "uncertain":
            notes.append("uncertain_price_page_excluded")
        elif classification == "price":
            notes.append("price_page_excluded")
        pages.append(
            {
                "page_id": page.id,
                "document_id": page.document_id,
                "page": page.page,
                "role": page.role,
                "sanitized_text": sanitized,
                "sanitized_text_sha256": text_hash(sanitized),
                "price_page": classification != "non_price",
                "price_classification": classification,
                "notes": notes,
                "redaction_counts": counts,
                "outbound_eligible": classification == "non_price"
                and bool(sanitized.strip())
                and page.text_status == "native"
                and page.page_kind == "text",
            }
        )
    blockers = []
    if not provider_binding:
        blockers.append("provider_unavailable")
    if not task.model_redaction_enabled:
        blockers.append("redaction_required")
    if root.state != "uploaded":
        blockers.append("bid_submission_withdrawn")
    provider_hash = bids.digest(provider_binding)
    manifest = {
        "submission_revision": root.revision,
        "submission_manifest_sha256": root.manifest_sha256,
        "preparation_id": str(publication.preparation_id),
        "preparation_input_hash": publication.input_hash,
        "redaction_revision": task.model_redaction_revision,
        "redaction_enabled": task.model_redaction_enabled,
        "redaction_policy_version": POLICY_VERSION,
        "confidential_binding_sha256": confidential_hash,
        "derived_name_lists_sha256": names_hash,
        "provider_bindings_sha256": provider_hash,
        "pages": [
            {
                key: str(p[key]) if key.endswith("_id") else p[key]
                for key in (
                    "page_id",
                    "document_id",
                    "page",
                    "role",
                    "sanitized_text_sha256",
                    "price_page",
                    "price_classification",
                    "notes",
                )
            }
            for p in pages
        ],
    }
    for page in pages:
        source = next(row for row in rows if row.id == page["page_id"])
        page["original_text"] = original[source.id]
        page["structure"] = (
            open_value(settings, source, "structure_encrypted")
            if source.structure_encrypted
            else None
        )
        page["row"] = source
    return PrivacySnapshot(
        root,
        publication,
        manifest,
        bids.digest(manifest),
        task.model_redaction_revision,
        confidential_hash,
        names_hash,
        provider_hash,
        pages,
        names,
        blockers,
        human_name_revision=human.revision if human else 0,
    )


async def human_access(session, actor, submission_id, *, write=False):
    root = await bids.required(session, submission_id)
    _, _, member = await task_workflow.access(
        session,
        actor,
        root.task_id,
        scope="bid-review:outbound:authorize",
        write=write,
        require_member=True,
    )
    if actor.role not in {"admin", "bidder"} or member is None or member.role != "owner":
        bids.fail("forbidden", "Human admin/bidder task owner is required", 403, 4)
    return root


async def preview(session, actor, submission_id, settings, provider_binding, *, cursor=0, limit=25):
    await human_access(session, actor, submission_id)
    fixed = await current_snapshot(session, submission_id, settings, provider_binding)
    latest = await latest_grant(session, submission_id)
    views, size = [], 0
    for page in fixed.pages[cursor : cursor + limit]:
        public = c.BidSanitizedPage.model_validate(
            {key: page[key] for key in c.BidSanitizedPage.model_fields}
        )
        page_size = len(public.model_dump_json().encode())
        if page_size > 950000:
            bids.fail(
                "bid_review_output_limit",
                "Sanitized page exceeds the review response bound",
                409,
                4,
            )
        if size + page_size > 950000:
            break
        size += page_size
        views.append(public)
    return c.BidRedactionPreview(
        submission_id=submission_id,
        expected_revision=(latest.revision if latest else 0) + 1,
        expected_authorization_id=latest.id if latest else None,
        expected_submission_manifest_sha256=fixed.root.manifest_sha256,
        expected_preparation_input_hash=fixed.publication.input_hash,
        expected_redaction_manifest_sha256=fixed.manifest_sha256,
        provider_bindings_sha256=fixed.provider_bindings_sha256,
        redaction_revision=fixed.redaction_revision,
        redaction_rule_version=POLICY_VERSION,
        confidential_binding_sha256=fixed.confidential_binding_sha256,
        derived_name_lists_sha256=fixed.derived_name_lists_sha256,
        pages=views,
        human_name_revision=fixed.human_name_revision,
        total=len(fixed.pages),
        next_cursor=cursor + len(views) if cursor + len(views) < len(fixed.pages) else None,
        blockers=fixed.blockers,
    )


async def grant_refs(session, grant):
    rows = (
        await session.scalars(
            select(BidOutboundAuthorizedPage)
            .where(BidOutboundAuthorizedPage.authorization_id == grant.id)
            .order_by(BidOutboundAuthorizedPage.page_id)
        )
    ).all()
    return [
        c.SanitizedPageRef(page_id=row.page_id, sanitized_text_sha256=row.sanitized_text_sha256)
        for row in rows
    ]


async def grant_view(session, row, fixed=None, latest=None):
    values = {
        key: getattr(row, key)
        for key in c.OutboundAuthorizationView.model_fields
        if hasattr(row, key)
    }
    values.update(
        authorized_at=row.created_at,
        purposes=["bid_review_text"],
        allowed_capabilities=["llm"],
        pages=await grant_refs(session, row),
        current=bool(
            fixed is not None
            and latest is not None
            and row.id == latest.id
            and row.allow_external
            and row.redaction_manifest_sha256 == fixed.manifest_sha256
            and not fixed.blockers
        ),
    )
    return c.OutboundAuthorizationView.model_validate(values)


async def authorize(session, actor, submission_id, body, settings, provider_binding):
    root = await human_access(session, actor, submission_id, write=True)
    payload_hash = bids.digest(body.model_dump(mode="json"))
    replay = await session.scalar(
        select(BidOutboundAuthorization).where(
            BidOutboundAuthorization.authorized_by == actor.user_id,
            BidOutboundAuthorization.request_id == body.request_id,
        )
    )
    if replay:
        if replay.submission_id != root.id or replay.payload_hash != payload_hash:
            bids.fail("idempotency_conflict", "Request ID has different input")
        return await grant_view(session, replay)
    fixed = await current_snapshot(session, root.id, settings, provider_binding)
    previous = await latest_grant(session, root.id)
    expected = {
        "expected_revision": (previous.revision if previous else 0) + 1,
        "expected_authorization_id": previous.id if previous else None,
        "expected_submission_manifest_sha256": root.manifest_sha256,
        "expected_preparation_input_hash": fixed.publication.input_hash,
        "expected_redaction_manifest_sha256": fixed.manifest_sha256,
        "provider_bindings_sha256": fixed.provider_bindings_sha256,
    }
    if any(getattr(body, key) != value for key, value in expected.items()):
        bids.fail(
            "bid_outbound_input_changed", "Privacy scope changed; review the sanitized pages again"
        )
    if not body.allow_external:
        bids.fail("bid_outbound_use_revoke", "Use the explicit revoke command")
    if fixed.blockers:
        bids.fail(fixed.blockers[0], "External text disclosure is blocked", 409, 4)
    selected = {item.page_id: item.sanitized_text_sha256 for item in body.pages}
    current = {item["page_id"]: item for item in fixed.pages}
    for page_id, expected_hash in selected.items():
        page = current.get(page_id)
        if page is None or page["sanitized_text_sha256"] != expected_hash:
            bids.fail(
                "bid_outbound_page_changed", "Authorized page/hash does not match the reviewed text"
            )
        if not page["outbound_eligible"]:
            bids.fail(
                "bid_price_page_excluded" if page["price_page"] else "bid_text_unavailable",
                "Page cannot be transmitted externally in this review slice",
                409,
                4,
            )
    if c.sanitized_context_sha256(body.pages) != body.authorized_sanitized_context_sha256:
        bids.fail("bid_outbound_context_mismatch", "Authorized page scope digest does not match")
    snapshot = await session.scalar(
        select(BidRedactionSnapshot).where(
            BidRedactionSnapshot.submission_id == root.id,
            BidRedactionSnapshot.manifest_sha256 == fixed.manifest_sha256,
        )
    )
    if snapshot is None:
        snapshot = BidRedactionSnapshot(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=root.task_id,
            submission_id=root.id,
            preparation_id=fixed.publication.preparation_id,
            redaction_revision=fixed.redaction_revision,
            policy_version=POLICY_VERSION,
            manifest_sha256=fixed.manifest_sha256,
            confidential_binding_sha256=fixed.confidential_binding_sha256,
            derived_name_lists_sha256=fixed.derived_name_lists_sha256,
            provider_bindings_sha256=fixed.provider_bindings_sha256,
            created_by=actor.user_id,
        )
        snapshot.details_encrypted = bids.seal(
            settings, actor.org_id, snapshot.id, {"manifest": fixed.manifest, "names": fixed.names}
        )
        session.add(snapshot)
        await session.flush()
        for page in fixed.pages:
            identifier = uuid4()
            session.add(
                BidRedactedPage(
                    id=identifier,
                    org_id=actor.org_id,
                    task_id=root.task_id,
                    submission_id=root.id,
                    preparation_id=fixed.publication.preparation_id,
                    snapshot_id=snapshot.id,
                    page_id=page["page_id"],
                    sanitized_text_sha256=page["sanitized_text_sha256"],
                    sanitized_text_encrypted=bids.seal(
                        settings, actor.org_id, identifier, page["sanitized_text"]
                    ),
                    price_page=page["price_page"],
                    classification=page["price_classification"],
                    notes_encrypted=bids.seal(
                        settings,
                        actor.org_id,
                        identifier,
                        {"notes": page["notes"], "counts": page["redaction_counts"]},
                    ),
                )
            )
        await session.flush()
    grant = BidOutboundAuthorization(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=root.task_id,
        submission_id=root.id,
        preparation_id=fixed.publication.preparation_id,
        snapshot_id=snapshot.id,
        revision=body.expected_revision,
        prior_authorization_id=body.expected_authorization_id,
        request_id=body.request_id,
        payload_hash=payload_hash,
        submission_manifest_sha256=root.manifest_sha256,
        preparation_input_hash=fixed.publication.input_hash,
        redaction_manifest_sha256=fixed.manifest_sha256,
        authorized_sanitized_context_sha256=body.authorized_sanitized_context_sha256,
        provider_bindings_sha256=fixed.provider_bindings_sha256,
        allow_external=True,
        authorized_by=actor.user_id,
        reason_sha256=text_hash(body.reason),
    )
    grant.reason_encrypted = bids.seal(settings, actor.org_id, grant.id, body.reason)
    session.add(grant)
    await session.flush()
    for item in body.pages:
        session.add(
            BidOutboundAuthorizedPage(
                id=uuid4(),
                org_id=actor.org_id,
                task_id=root.task_id,
                submission_id=root.id,
                snapshot_id=snapshot.id,
                authorization_id=grant.id,
                **item.model_dump(),
            )
        )
    await session.flush()
    audit(
        session,
        actor,
        "bid_review.outbound.authorize",
        grant.id,
        {
            "submission_id": str(root.id),
            "revision": grant.revision,
            "redaction_manifest_sha256": fixed.manifest_sha256,
            "reason_sha256": grant.reason_sha256,
            "authorized_sanitized_context_sha256": grant.authorized_sanitized_context_sha256,
        },
    )
    return await grant_view(session, grant, fixed, grant)


async def revoke(session, actor, submission_id, body, settings, provider_binding=None):
    root = await human_access(session, actor, submission_id, write=True)
    payload_hash = bids.digest({"revoke": body.model_dump(mode="json")})
    replay = await session.scalar(
        select(BidOutboundAuthorization).where(
            BidOutboundAuthorization.authorized_by == actor.user_id,
            BidOutboundAuthorization.request_id == body.request_id,
        )
    )
    if replay:
        if replay.submission_id != root.id or replay.payload_hash != payload_hash:
            bids.fail("idempotency_conflict", "Request ID has different input")
        return await grant_view(session, replay)
    previous = await latest_grant(session, root.id)
    if previous is None or (previous.id, previous.revision) != (
        body.expected_authorization_id,
        body.expected_revision,
    ):
        bids.fail("bid_outbound_revision_conflict", "Outbound authorization changed; read it again")
    grant = BidOutboundAuthorization(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=root.task_id,
        submission_id=root.id,
        preparation_id=previous.preparation_id,
        snapshot_id=previous.snapshot_id,
        revision=previous.revision + 1,
        prior_authorization_id=previous.id,
        request_id=body.request_id,
        payload_hash=payload_hash,
        allow_external=False,
        authorized_by=actor.user_id,
        reason_sha256=text_hash(body.reason),
        **{
            key: getattr(previous, key)
            for key in (
                "submission_manifest_sha256",
                "preparation_input_hash",
                "redaction_manifest_sha256",
                "authorized_sanitized_context_sha256",
                "provider_bindings_sha256",
            )
        },
    )
    grant.reason_encrypted = bids.seal(settings, actor.org_id, grant.id, body.reason)
    session.add(grant)
    await session.flush()
    for item in await grant_refs(session, previous):
        session.add(
            BidOutboundAuthorizedPage(
                id=uuid4(),
                org_id=actor.org_id,
                task_id=root.task_id,
                submission_id=root.id,
                snapshot_id=grant.snapshot_id,
                authorization_id=grant.id,
                **item.model_dump(),
            )
        )
    await session.flush()
    audit(
        session,
        actor,
        "bid_review.outbound.revoke",
        grant.id,
        {
            "submission_id": str(root.id),
            "revision": grant.revision,
            "reason_sha256": grant.reason_sha256,
        },
    )
    return await grant_view(session, grant)


async def list_authorizations(
    session, actor, submission_id, settings, provider_binding, *, cursor=0, limit=25
):
    await human_access(session, actor, submission_id)
    fixed = await current_snapshot(session, submission_id, settings, provider_binding)
    latest = await latest_grant(session, submission_id)
    query = select(BidOutboundAuthorization).where(
        BidOutboundAuthorization.submission_id == submission_id
    )
    rows = (
        await session.scalars(
            query.order_by(BidOutboundAuthorization.revision.desc()).offset(cursor).limit(limit)
        )
    ).all()
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    items, size = [], 0
    for row in rows:
        item = await grant_view(session, row, fixed, latest)
        item_size = len(item.model_dump_json().encode())
        if item_size > 950000:
            bids.fail("bid_review_output_limit", "Authorization exceeds the response bound", 409, 4)
        if size + item_size > 950000:
            break
        items.append(item)
        size += item_size
    end = cursor + len(items)
    return {"total": total, "next_cursor": end if end < total else None}, items


async def authorized_snapshot(
    session, submission_id, settings, provider_binding, authorization_id=None
):
    fixed = await current_snapshot(session, submission_id, settings, provider_binding)
    grant = await latest_grant(session, submission_id)
    if grant is None or not grant.allow_external:
        bids.fail(
            "bid_outbound_authorization_missing",
            "Exact human outbound authorization is required",
            409,
            4,
        )
    live = await session.scalar(
        select(func.bid_review_actor_live(grant.org_id, grant.task_id, grant.authorized_by))
    )
    owner = await session.scalar(
        select(TaskMember.id).where(
            TaskMember.org_id == grant.org_id,
            TaskMember.task_id == grant.task_id,
            TaskMember.user_id == grant.authorized_by,
            TaskMember.active.is_(True),
            TaskMember.role == "owner",
        )
    )
    if not live or owner is None:
        bids.fail(
            "bid_outbound_authority_expired",
            "Human outbound grant origin no longer has task authority",
            409,
            4,
        )
    if authorization_id is not None and grant.id != authorization_id:
        bids.fail(
            "bid_outbound_authorization_changed", "Human outbound authorization changed", 409, 4
        )
    if fixed.blockers:
        bids.fail(fixed.blockers[0], "External text review is blocked", 409, 4)
    if (grant.redaction_manifest_sha256, grant.provider_bindings_sha256) != (
        fixed.manifest_sha256,
        fixed.provider_bindings_sha256,
    ):
        bids.fail(
            "bid_outbound_authorization_stale",
            "Reviewed redaction or provider binding changed",
            409,
            4,
        )
    refs = await grant_refs(session, grant)
    current = {item["page_id"]: item for item in fixed.pages}
    selected = []
    for ref in refs:
        page = current.get(ref.page_id)
        if (
            page is None
            or page["sanitized_text_sha256"] != ref.sanitized_text_sha256
            or not page["outbound_eligible"]
        ):
            bids.fail("bid_outbound_authorization_stale", "Authorized text page changed", 409, 4)
        selected.append(page)
    if (
        not selected
        or c.sanitized_context_sha256(refs) != grant.authorized_sanitized_context_sha256
    ):
        bids.fail("bid_redaction_integrity", "Authorized context receipt mismatch", 409, 4)
    fixed.pages, fixed.authorization = selected, grant
    return fixed


async def add_names(session, actor, submission_id, body, settings):
    root = await human_access(session, actor, submission_id, write=True)
    names = {
        key: sorted({normalize(item) for item in getattr(body, key)})
        for key in ("bidder_names", "staff_names")
    }
    if any(len(item) > 200 for values in names.values() for item in values):
        bids.fail("bid_name_limit", "A name exceeds the local name-list bound", 422)
    hashed = bids.digest(names)
    replay = await session.scalar(
        select(BidReviewNameList).where(
            BidReviewNameList.created_by == actor.user_id,
            BidReviewNameList.request_id == body.request_id,
        )
    )
    if replay:
        if (
            replay.submission_id != root.id
            or replay.names_sha256 != hashed
            or replay.revision != body.expected_revision + 1
        ):
            bids.fail("idempotency_conflict", "Request ID has different input")
        row = replay
    else:
        current = (
            await session.scalar(
                select(func.max(BidReviewNameList.revision)).where(
                    BidReviewNameList.submission_id == root.id
                )
            )
            or 0
        )
        if current != body.expected_revision:
            bids.fail(
                "bid_name_revision_conflict",
                "Human name additions changed; read the current revision",
            )
        row = BidReviewNameList(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=root.task_id,
            submission_id=root.id,
            revision=current + 1,
            request_id=body.request_id,
            created_by=actor.user_id,
            names_sha256=hashed,
        )
        row.names_encrypted = bids.seal(settings, actor.org_id, row.id, names)
        session.add(row)
        await session.flush()
        audit(
            session,
            actor,
            "bid_review.redaction.names",
            row.id,
            {"submission_id": str(root.id), "revision": row.revision, "names_sha256": hashed},
        )
    return c.BidNameListView(
        id=row.id,
        revision=row.revision,
        names_sha256=row.names_sha256,
        bidder_name_count=len(names["bidder_names"]),
        staff_name_count=len(names["staff_names"]),
    )
