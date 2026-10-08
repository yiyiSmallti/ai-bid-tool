"""Publication and current-authority reads for immutable compliance findings."""

import re
from collections import Counter
from uuid import uuid4

from sqlalchemy import func, select, text

from app.core.errors import not_found
from app.core.security import Secrets
from app.models.bid_review import BidDocumentPage, BidSubmission
from app.models.bid_review_findings import (
    BidReviewFinding,
    BidReviewFindingEvent,
    BidReviewFindingSource,
)
from app.models.bid_review_privacy import BidOutboundAuthorizedPage, BidRedactedPage
from app.models.bid_review_run import BidReviewObligation, BidReviewPublication, BidReviewRun
from app.models.bid_signature import BidPDFValidation
from app.schemas import bid_review_findings as c
from app.services import bid_review as bids
from app.services import bid_review_privacy as privacy
from app.services import bid_review_run as runs
from app.services import confidential, redaction, task_workflow
from app.services.versioned import audit


async def publish_bid_review_findings(session, run, findings, *, settings):
    """Validate independent source gates, then flush before the run publication fence."""
    if len(findings) > 10000:
        bids.fail("bid_review_output_limit", "Finding count exceeds run bound", 409, 4)
    values = [c.BidReviewMachineFinding.model_validate(value) for value in findings]
    if len({v.id for v in values}) != len(values) or any(
        n > 20 for n in Counter(v.obligation_id for v in values).values()
    ):
        bids.fail(
            "bid_review_output_limit",
            "Finding identities or obligation count exceed bounds",
            409,
            4,
        )
    pages = {
        p.id: p
        for p in (
            await session.scalars(
                select(BidDocumentPage).where(BidDocumentPage.preparation_id == run.preparation_id)
            )
        ).all()
    }
    documents = {d.id: d for d in await bids.rows(session, run.submission_id)}
    authorized = {
        p.page_id: p
        for p in (
            await session.scalars(
                select(BidRedactedPage)
                .join(
                    BidOutboundAuthorizedPage,
                    (BidOutboundAuthorizedPage.snapshot_id == BidRedactedPage.snapshot_id)
                    & (BidOutboundAuthorizedPage.page_id == BidRedactedPage.page_id)
                    & (BidOutboundAuthorizedPage.org_id == BidRedactedPage.org_id),
                )
                .where(BidOutboundAuthorizedPage.authorization_id == run.authorization_id)
            )
        ).all()
    }
    root = await bids.required(session, run.submission_id)
    obligation_rows = {
        row.id: row
        for row in (
            await session.scalars(
                select(BidReviewObligation).where(BidReviewObligation.review_id == run.id)
            )
        ).all()
    }
    obligation_citations = {}
    originals, cleared = {}, {}
    for ordinal, value in enumerate(values, 1):
        payload = value.model_dump(mode="json")
        if value.obligation_id not in obligation_citations:
            obligation = obligation_rows.get(value.obligation_id)
            if obligation is None:
                bids.fail(
                    "bid_review_source_invalid", "Finding obligation is outside its review", 409, 4
                )
            obligation_citations[value.obligation_id] = privacy.open_value(
                settings, obligation, "details_encrypted"
            )["citation"]
        obligation_citation = c.FindingCitation.model_validate(
            obligation_citations[value.obligation_id]
        )
        if obligation_citation not in value.tender_support:
            bids.fail(
                "bid_review_source_invalid",
                "Finding tender basis does not include its exact obligation",
                409,
                4,
            )
        session.add(
            BidReviewFinding(
                id=value.id,
                org_id=run.org_id,
                task_id=run.task_id,
                submission_id=run.submission_id,
                review_id=run.id,
                obligation_id=value.obligation_id,
                ordinal=ordinal,
                code=value.code,
                outcome=value.outcome,
                severity=value.severity,
                impact=value.impact,
                tender_support_count=len(value.tender_support),
                bid_support_count=len(value.bid_support),
                search_source_count=len(
                    value.absence_search.searched_pages
                    if value.absence_search.kind == "locations"
                    else value.absence_search.inspected_bid_document_ids
                )
                if value.absence_search
                else 0,
                rule_document_count=len(value.rule_evidence.document_ids)
                if value.rule_evidence
                else 0,
                absence_kind=value.absence_search.kind if value.absence_search else None,
                absence_coverage=value.absence_search.coverage if value.absence_search else None,
                absence_method=value.absence_search.method if value.absence_search else None,
                absence_manifest_sha256=value.absence_search.submission_manifest_sha256
                if value.absence_search
                else None,
                limitation_count=len(value.limitation_codes),
                details_encrypted=bids.seal(settings, run.org_id, value.id, payload),
                details_sha256=bids.digest(payload),
            )
        )
        await session.flush()
        for kind, supports in (
            ("tender_support", value.tender_support),
            ("bid_support", value.bid_support),
        ):
            for number, citation in enumerate(supports, 1):
                page = pages.get(citation.page_id)
                role = "tender" if kind == "tender_support" else "bid"
                if (
                    page is None
                    or page.role != role
                    or page.document_id != citation.document_id
                    or page.page != citation.page
                    or page.id not in authorized
                ):
                    bids.fail(
                        "bid_review_source_invalid",
                        "Finding quote parent or authority mismatch",
                        409,
                        4,
                    )
                if page.id not in originals:
                    originals[page.id] = privacy.open_value(settings, page, "text_encrypted")
                    cleared[page.id] = privacy.open_value(
                        settings, authorized[page.id], "sanitized_text_encrypted"
                    )
                    if (
                        privacy.text_hash(originals[page.id]) != page.text_sha256
                        or privacy.text_hash(cleared[page.id])
                        != authorized[page.id].sanitized_text_sha256
                    ):
                        bids.fail(
                            "bid_review_source_invalid", "Finding source hash mismatch", 409, 4
                        )
                original, sent = originals[page.id], cleared[page.id]
                if (
                    original.count(citation.quote) != 1
                    or sent.count(citation.quote) != 1
                    or redaction.PLACEHOLDER.search(citation.quote)
                    or redaction.SECRET_PLACEHOLDER.search(citation.quote)
                ):
                    bids.fail(
                        "bid_review_source_invalid",
                        "Finding quote is not an exact unique sent and original span",
                        409,
                        4,
                    )
                start = original.index(citation.quote)
                if citation.start_offset is not None and (
                    citation.start_offset != start
                    or citation.end_offset != start + len(citation.quote)
                ):
                    bids.fail(
                        "bid_review_source_invalid",
                        "Finding quote offsets do not match original text",
                        409,
                        4,
                    )
                document = documents[page.document_id]
                if (document.media_type == "application/pdf") != (
                    citation.page_label == "original_pdf"
                ) or (document.media_type != "application/pdf" and citation.location is None):
                    bids.fail(
                        "bid_review_source_invalid",
                        "Finding display label or Word location mismatch",
                        409,
                        4,
                    )
                session.add(
                    source(
                        run,
                        value.id,
                        kind,
                        number,
                        page.document_id,
                        page.id,
                        quote_sha256=privacy.text_hash(citation.quote),
                        start_offset=start,
                        end_offset=start + len(citation.quote),
                    )
                )
        absence = value.absence_search
        if absence and absence.kind == "locations":
            for number, ref in enumerate(absence.searched_pages, 1):
                page = pages.get(ref.page_id)
                if (
                    page is None
                    or page.role != "bid"
                    or page.document_id != ref.document_id
                    or page.page != ref.page
                    or page.id not in authorized
                ):
                    bids.fail(
                        "bid_review_source_invalid",
                        "Absence search is outside inspected authorized pages",
                        409,
                        4,
                    )
                session.add(
                    source(run, value.id, "absence_page", number, page.document_id, page.id)
                )
            all_bid = {p.id for p in pages.values() if p.role == "bid"}
            if (
                absence.coverage == "all_bid_pages"
                and {p.page_id for p in absence.searched_pages} != all_bid
            ):
                bids.fail(
                    "bid_review_source_invalid", "Whole-bid absence coverage is incomplete", 409, 4
                )
        elif absence:
            inventory = {d.id for d in documents.values() if d.role == "bid"}
            if (
                absence.submission_id != root.id
                or absence.submission_manifest_sha256 != root.manifest_sha256
                or set(absence.inspected_bid_document_ids) != inventory
            ):
                bids.fail(
                    "bid_review_source_invalid",
                    "Absence inventory does not match immutable submission",
                    409,
                    4,
                )
            for number, document_id in enumerate(absence.inspected_bid_document_ids, 1):
                session.add(source(run, value.id, "inventory_document", number, document_id))
        if value.rule_evidence:
            validations = {
                row.id: row.document_id
                for row in (
                    await session.scalars(
                        select(BidPDFValidation).where(
                            BidPDFValidation.preparation_id == run.preparation_id
                        )
                    )
                ).all()
            }
            for number, (document_id, validation_id) in enumerate(
                zip(
                    value.rule_evidence.document_ids,
                    value.rule_evidence.validation_ids,
                    strict=True,
                ),
                1,
            ):
                if (
                    validations.get(validation_id) != document_id
                    or document_id not in documents
                    or documents[document_id].role != "bid"
                ):
                    bids.fail(
                        "bid_review_source_invalid",
                        "Local PDF evidence is not bound to prepared bid",
                        409,
                        4,
                    )
                session.add(
                    source(
                        run,
                        value.id,
                        "rule_document",
                        number,
                        document_id,
                        validation_id=validation_id,
                    )
                )
        await session.flush()


def source(run, finding_id, kind, ordinal, document_id, page_id=None, **values):
    return BidReviewFindingSource(
        id=uuid4(),
        org_id=run.org_id,
        task_id=run.task_id,
        submission_id=run.submission_id,
        preparation_id=run.preparation_id,
        review_id=run.id,
        finding_id=finding_id,
        kind=kind,
        ordinal=ordinal,
        document_id=document_id,
        page_id=page_id,
        **values,
    )


async def required_run(session, actor, review_id):
    run = await session.get(BidReviewRun, review_id)
    if run is None:
        raise not_found()
    await runs.run_access(session, actor, run.task_id, write=False, lock=False)
    publication = await session.scalar(
        select(BidReviewPublication).where(BidReviewPublication.review_id == run.id)
    )
    if publication is None:
        bids.fail("bid_review_unpublished", "Finding reads require a published review", 409, 4)
    return run, publication


async def required_finding(session, run, finding_id, *, lock=False):
    query = select(BidReviewFinding).where(
        BidReviewFinding.id == finding_id, BidReviewFinding.review_id == run.id
    )
    if lock:
        # Findings are immutable and bid_app has no UPDATE privilege, so FOR UPDATE
        # is unavailable. A transaction advisory lock per finding serializes human
        # decisions; UNIQUE(org_id, finding_id, revision) still rejects a lost race.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"bid-review-finding:{finding_id}"},
        )
    finding = await session.scalar(query)
    if finding is None:
        raise not_found()
    return finding


async def latest(session, finding_id):
    return await session.scalar(
        select(BidReviewFindingEvent)
        .where(BidReviewFindingEvent.finding_id == finding_id)
        .order_by(BidReviewFindingEvent.revision.desc())
        .limit(1)
    )


async def state(session, finding):
    event = await latest(session, finding.id)
    classification = await session.scalar(
        select(BidReviewFindingEvent)
        .where(
            BidReviewFindingEvent.finding_id == finding.id,
            BidReviewFindingEvent.action == "classify",
        )
        .order_by(BidReviewFindingEvent.revision.desc())
        .limit(1)
    )
    return {
        "state": event.state if event else "open",
        "revision": event.revision if event else 1,
        "review_domain": classification.review_domain if classification else None,
        "classification_id": classification.id if classification else None,
        "latest_decision_id": event.id if event else None,
    }


def protected(actor):
    return (
        actor.actor_kind == "session"
        and actor.token_id is None
        and actor.role in {"admin", "bidder"}
        and "bid-review:original:read" in actor.scopes
    )


async def cleared_projector(session, run, settings):
    """Build a live privacy projection, including registered short values and bid names."""
    _, binding = await runs.resolve_binding(session, settings)
    snapshot = await privacy.authorized_snapshot(
        session, run.submission_id, settings, binding, authorization_id=run.authorization_id
    )
    entries = await confidential.task_entries(session, run.task_id)
    crypto = Secrets.for_data(settings)
    library = confidential.library(entries, crypto)
    seen = {item.key for item in library}
    for key, entry in entries.items():
        if key not in seen and entry.value is not None:
            value = privacy.normalize(crypto.decrypt(entry.value.encrypted_value))
            if value:
                library.append(redaction.LibraryValue(key, re.compile(re.escape(value))))
    for names in snapshot.names.values():
        for name in names:
            library.append(
                redaction.LibraryValue("bid_review_identity", re.compile(re.escape(name)))
            )

    def project(value):
        if isinstance(value, str):
            masked, _ = redaction.redact(value, True, library)
            return redaction.SECRET_PLACEHOLDER.sub("[REDACTED_CONFIDENTIAL]", masked)
        if isinstance(value, list):
            return [project(item) for item in value]
        if isinstance(value, dict):
            return {key: project(item) for key, item in value.items()}
        return value

    return project


async def view(session, finding, settings, *, safe=False, project=None):
    values = {
        "id": finding.id,
        "task_id": finding.task_id,
        "review_id": finding.review_id,
        "obligation_id": finding.obligation_id,
        "code": finding.code,
        "outcome": finding.outcome,
        "severity": finding.severity,
        "impact": finding.impact,
        **await state(session, finding),
    }
    if safe:
        return c.BidReviewSafeFinding.model_validate(values)
    payload = privacy.open_value(settings, finding, "details_encrypted")
    if bids.digest(payload) != finding.details_sha256:
        bids.fail("bid_review_output_integrity", "Finding payload hash mismatch", 409, 4)
    if project:
        # Only content fields are transformed; IDs, fixed codes and source coordinates stay stable.
        for key in ("title", "explanation", "remediation"):
            payload[key] = project(payload[key])
        for key in ("tender_support", "bid_support"):
            for citation in payload[key]:
                citation["quote"] = project(citation["quote"])
                if citation.get("location"):
                    citation["location"] = project(citation["location"])
        if (payload.get("absence_search") or {}).get("required_document_description"):
            payload["absence_search"]["required_document_description"] = project(
                payload["absence_search"]["required_document_description"]
            )
    reviewed_basis = (
        {"kind": "human_reviewed", "human_decision_id": values["latest_decision_id"]}
        if values["state"] == "confirmed"
        else None
    )
    return c.BidReviewFindingView.model_validate(
        {**payload, **values, "reviewed_basis": reviewed_basis}
    )


async def list_findings(
    session,
    actor,
    review_id,
    settings,
    *,
    cursor=None,
    limit=50,
    severity=None,
    state=None,
    outcome=None,
):
    run, publication = await required_run(session, actor, review_id)
    valid = await runs.privacy_validity(session, run, settings)
    safe = actor.token_id is not None or actor.actor_kind != "session"
    project = None
    if not safe:
        actor.require("bid-review:report:read")
        if not protected(actor):
            if not valid:
                bids.fail("bid_review_input_changed", "Cleared report privacy is stale", 409, 4)
            project = await cleared_projector(session, run, settings)
    revision_count = await session.scalar(
        select(func.count())
        .select_from(BidReviewFindingEvent)
        .where(BidReviewFindingEvent.review_id == run.id)
    )
    section = "findings:" + bids.digest(
        {
            "severity": severity,
            "state": state,
            "outcome": outcome,
            "events": revision_count,
            "output_hash": publication.output_hash,
        }
    )
    start = runs.offset(actor, review_id, section, cursor, settings)
    query = select(BidReviewFinding).where(BidReviewFinding.review_id == run.id)
    if severity:
        query = query.where(BidReviewFinding.severity == severity)
    if outcome:
        query = query.where(BidReviewFinding.outcome == outcome)
    if state:
        current_state = (
            select(BidReviewFindingEvent.state)
            .where(BidReviewFindingEvent.finding_id == BidReviewFinding.id)
            .order_by(BidReviewFindingEvent.revision.desc())
            .limit(1)
            .scalar_subquery()
        )
        query = query.where(func.coalesce(current_state, "open") == state)
    rows = list(
        (
            await session.scalars(
                query.order_by(BidReviewFinding.ordinal).offset(start).limit(limit + 1)
            )
        ).all()
    )
    items, size = [], 0
    for finding in rows[:limit]:
        item = await view(session, finding, settings, safe=safe, project=project)
        item_size = len(item.model_dump_json().encode())
        if item_size > 950000:
            bids.fail("bid_review_output_limit", "Finding exceeds response bound", 400, 4)
        if size + item_size > 950000:
            break
        size += item_size
        items.append(item)
    return c.BidReviewFindingsData(
        review_id=run.id,
        input_hash=run.input_hash,
        validity="current" if valid else "stale",
        next_cursor=runs.next_cursor(actor, review_id, section, start + len(items), settings)
        if len(rows) > len(items)
        else None,
    ), items


async def event_view(row, settings, *, project=None):
    reason = privacy.open_value(settings, row, "reason_encrypted")
    if privacy.text_hash(reason) != row.reason_sha256:
        bids.fail("bid_review_output_integrity", "Decision reason hash mismatch", 409, 4)
    return c.BidReviewEventView(
        id=row.id,
        review_id=row.review_id,
        task_id=row.task_id,
        finding_id=row.finding_id,
        prior_decision_id=row.prior_decision_id,
        revision=row.revision,
        action=row.action,
        review_domain=row.review_domain,
        state=row.state,
        reason=project(reason) if project else reason,
        reason_sha256=row.reason_sha256,
        decided_by=row.decided_by,
        decided_at=row.created_at,
    )


async def decide(session, actor, review_id, finding_id, body, settings, *, classify=False):
    run, _ = await required_run(session, actor, review_id)
    finding = await required_finding(session, run, finding_id)
    if actor.actor_kind != "session" or actor.token_id is not None:
        bids.fail("human_session_required", "Finding decisions require a human session", 403, 4)
    scope = "bid-review:classify" if classify else "bid-review:decide"
    current = await state(session, finding)
    if not classify and current["review_domain"] is None:
        bids.fail("unclassified", "Classify this finding before deciding", 409, 2)
    await task_workflow.access(
        session,
        actor,
        run.task_id,
        scope=scope,
        write=True,
        management=classify,
        domain=None if classify else current["review_domain"],
    )
    if classify and actor.role != "admin":
        bids.fail("forbidden", "Finding classification requires an org administrator", 403, 4)
    finding = await required_finding(session, run, finding_id, lock=True)
    current = await state(session, finding)
    domain = body.review_domain if classify else current["review_domain"]
    if not classify and actor.role != {"commercial": "bidder", "technical": "technical"}.get(
        domain
    ):
        bids.fail("forbidden", "Finding domain requires its authorized human reviewer", 403, 4)
    payload_hash = bids.digest(
        {
            "review_id": str(run.id),
            "finding_id": str(finding.id),
            "classify": classify,
            "body": body.model_dump(mode="json"),
        }
    )
    await bids.replay_lock(session, actor, "bid_review_decision", body.request_id)
    replay = await session.scalar(
        select(BidReviewFindingEvent).where(
            BidReviewFindingEvent.decided_by == actor.user_id,
            BidReviewFindingEvent.request_id == body.request_id,
        )
    )
    if replay:
        if replay.payload_hash != payload_hash:
            bids.fail(
                "request_conflict",
                "Decision request ID was already used for another payload",
                409,
                2,
            )
        return await event_view(
            replay,
            settings,
            project=None if protected(actor) else await cleared_projector(session, run, settings),
        )
    root = await session.get(BidSubmission, run.submission_id)
    if (
        root.state != "uploaded"
        or body.expected_input_hash != run.input_hash
        or not await runs.privacy_validity(session, run, settings)
    ):
        bids.fail("bid_review_input_changed", "Only a current input may receive a decision", 409, 2)
    if (
        current["revision"] != body.expected_revision
        or current["latest_decision_id"] != body.expected_decision_id
    ):
        bids.fail("revision_conflict", "Read the current finding before deciding", 409, 2)
    action = "classify" if classify else body.action
    before = current["state"]
    if (action in {"classify", "confirm", "dismiss"} and before != "open") or (
        action == "reopen" and before == "open"
    ):
        bids.fail("invalid_transition", "Finding disposition does not allow this action", 409, 2)
    new_state = {"confirm": "confirmed", "dismiss": "dismissed", "reopen": "open"}.get(
        action, before
    )
    row = BidReviewFindingEvent(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=run.task_id,
        submission_id=run.submission_id,
        review_id=run.id,
        finding_id=finding.id,
        request_id=body.request_id,
        payload_hash=payload_hash,
        prior_decision_id=current["latest_decision_id"],
        revision=current["revision"] + 1,
        action=action,
        review_domain=domain,
        state=new_state,
        expected_input_hash=run.input_hash,
        reason_sha256=privacy.text_hash(body.reason),
        reason_encrypted="",
        decided_by=actor.user_id,
    )
    row.reason_encrypted = bids.seal(settings, actor.org_id, row.id, body.reason)
    session.add(row)
    await session.flush()
    audit(
        session,
        actor,
        "bid_review." + action,
        finding.id,
        {
            "task_id": str(run.task_id),
            "review_id": str(run.id),
            "finding_id": str(finding.id),
            "decision_id": str(row.id),
            "revision": row.revision,
            "input_hash": run.input_hash,
            "reason_sha256": row.reason_sha256,
        },
    )
    return await event_view(
        row,
        settings,
        project=None if protected(actor) else await cleared_projector(session, run, settings),
    )


async def history(
    session, actor, review_id, finding_id, settings, *, cursor=None, limit=50, classification=False
):
    run, _ = await required_run(session, actor, review_id)
    finding = await required_finding(session, run, finding_id)
    if actor.actor_kind != "session" or actor.token_id is not None:
        bids.fail("human_session_required", "Decision history requires a human session", 403, 4)
    actor.require("bid-review:report:read")
    valid = await runs.privacy_validity(session, run, settings)
    if not protected(actor) and not valid:
        bids.fail("bid_review_input_changed", "Cleared decision history privacy is stale", 409, 4)
    project = None if protected(actor) else await cleared_projector(session, run, settings)
    current = await state(session, finding)
    section = f"finding_history:{finding.id}:{current['revision']}:{classification}"
    start = runs.offset(actor, review_id, section, cursor, settings)
    query = select(BidReviewFindingEvent).where(BidReviewFindingEvent.finding_id == finding.id)
    if classification:
        query = query.where(BidReviewFindingEvent.action == "classify")
    rows = list(
        (
            await session.scalars(
                query.order_by(BidReviewFindingEvent.revision).offset(start).limit(limit + 1)
            )
        ).all()
    )
    items, size = [], 0
    for row in rows[:limit]:
        item = await event_view(row, settings, project=project)
        item_size = len(item.model_dump_json().encode())
        if item_size > 950000:
            bids.fail("bid_review_output_limit", "Decision exceeds response bound", 400, 4)
        if size + item_size > 950000:
            break
        size += item_size
        items.append(item)
    return c.BidReviewFindingsData(
        review_id=run.id,
        input_hash=run.input_hash,
        validity="current" if valid else "stale",
        next_cursor=runs.next_cursor(actor, review_id, section, start + len(items), settings)
        if len(rows) > len(items)
        else None,
    ), items
