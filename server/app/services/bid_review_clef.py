"""Presence-only visual stage; every required occurrence stays human work."""

import asyncio
from decimal import Decimal

from app.providers.base import ProviderFailure
from app.providers.clef import MODEL, VERSION, HTTPClefProvider
from app.schemas.budget_contracts import BudgetCallQuote
from app.schemas.clef import ClefNoulQuestion, ClefQuestion, ClefRedactedImage, ClefTriageRequest
from app.services import bid_review as bids

UNVALIDATED_CASES = [
    "faint_marks",
    "greyscale_marks",
    "partial_marks",
    "wrong_company",
    "required_position",
    "seam_seals",
    "signature_identity",
    "date_filled",
]
STATE = "仅观察模糊页面上的印章和签名墨迹是否存在；不判断身份、日期、位置或最终符合性。"
QUESTIONS: list[ClefQuestion] = [
    ClefNoulQuestion(ref="q1", question="这一页上是否加盖了红色圆形公章（含电子签章）？"),
    ClefNoulQuestion(ref="q2", question="这一页上是否有手写签名或电子签名的墨迹形状？"),
]


def preview_quote(image, config) -> BudgetCallQuote:
    return BudgetCallQuote(
        capability="vision",
        payer="org_platform",
        provider="cloudflare",
        model=MODEL,
        version=VERSION,
        platform_model_id=config.platform_model_id,
        price_revision=str(config.price_revision),
        request_sha256=bids.digest(
            {
                "image": image["sha256"],
                "state": STATE,
                "questions": [q.model_dump(mode="json") for q in QUESTIONS],
            }
        ),
        currency=config.currency,
        reserved_charge=config.fixed_sale_price,
        reserved_task_amount=config.fixed_sale_price,
        vendor_usd_upper_bound=None,
        image_count=1,
        image_price_revision=str(config.price_revision),
    )


def presence_request(image) -> ClefTriageRequest:
    descriptor = ClefRedactedImage(
        ref="image1",
        sha256=image["sha256"],
        size_bytes=image["size_bytes"],
        width_px=image["width_px"],
        height_px=image["height_px"],
        media_type="image/jpeg",
        privacy_receipt_sha256=image["privacy_receipt_sha256"],
    )
    return ClefTriageRequest(
        purpose="seal_present",
        state=STATE,
        questions=QUESTIONS,
        images=[descriptor],
        outbound_sha256=image["sha256"],
        text_redaction_sha256=image["privacy_receipt_sha256"],
    )


def apply_presence(signing, page_id, answers):
    probabilities = {"company_seal": answers["q1"], "signature": answers["q2"]}
    for requirement in signing.values():
        if requirement["applicability"] != "applies":
            continue
        types = requirement["mark_types"]
        relevant = []
        if any(
            mark in types for mark in ("company_seal", "every_page_electronic_seal", "seam_seal")
        ):
            relevant.append(probabilities["company_seal"])
        if any(mark.endswith("signature") and mark != "pdf_digital_signature" for mark in types):
            relevant.append(probabilities["signature"])
        for location in requirement["required_locations"]:
            if location.get("page_id") != page_id or not relevant:
                continue
            # Thresholds label the observed page only. They never establish a
            # required position, seal holder, signer identity or date.
            probability = min(relevant)
            status = (
                "triage_absent"
                if probability <= 0.1
                else "triage_present"
                if all(p >= 0.9 for p in relevant)
                else "triage_uncertain"
            )
            if requirement["location_rule"] == "seam_group" or "seam_seal" in types:
                status = "triage_uncertain"
            location.update(
                status=status,
                probability_yes=probability,
                presence_probabilities=probabilities,
                needs_human_confirmation=True,
                human_escalation=status != "triage_present",
                owner_status="unresolved",
                date_status="unresolved",
                position_status="unresolved",
                reason_code="clef_presence_requires_human_confirmation",
            )


async def triage(execution, fixed, signing, storage):
    from app.services import bid_review_presence

    clef = fixed.manifest["clef"]
    coverage = {
        "clef": "disabled" if not clef["enabled"] else "unavailable",
        "clef_calls": 0,
        "clef_cost": "0",
        "clef_completed_calls": 0,
        "clef_unresolved_calls": 0,
        "clef_reserved_amount": "0",
        "clef_currency": clef.get("currency", execution.settings.billing_currency),
        "clef_unvalidated_cases": UNVALIDATED_CASES,
        "clef_triage_only": True,
        "clef_human_confirmation_required": True,
    }
    if not clef["available"] or execution.stopped is not None:
        return coverage, [], ["clef_disabled" if not clef["enabled"] else "clef_unavailable"]
    async with execution.db.transaction(execution.org_id) as session:
        images = await bid_review_presence.authorized_images(
            session,
            fixed.root.id,
            execution.settings,
            clef["binding"],
            authorization_id=clef["authorization_id"],
            storage=storage,
        )
        from sqlalchemy import func, select

        from app.models.entities import VendorCall

        used = await session.scalar(
            select(func.count())
            .select_from(VendorCall)
            .where(
                VendorCall.job_id == execution.job_id,
                VendorCall.capability == "vision",
            )
        )
    required = {
        location["page_id"]
        for requirement in signing.values()
        if requirement["applicability"] == "applies"
        for location in requirement["required_locations"]
        if location.get("page_id")
    }
    selected = [image for image in images if str(image["page_id"]) in required]
    adapter = HTTPClefProvider(
        fixed.clef_config, execution.settings, remaining_calls=max(0, 40 - used)
    )

    async def before_send():
        async with execution.db.transaction(execution.org_id) as session:
            if execution.before_admit is not None:
                await execution.before_admit(session)
            # The separate image grant and exact storage hashes are checked again
            # after admission and immediately before external network I/O.
            await bid_review_presence.authorized_images(
                session,
                fixed.root.id,
                execution.settings,
                clef["binding"],
                authorization_id=clef["authorization_id"],
                storage=storage,
            )

    adapter.before_send = before_send
    reported, errors, assessed = [], [], []

    async def page(image):
        try:
            output = await adapter.triage(presence_request(image), [image["image"]])
        except ProviderFailure as error:
            if error.code in {
                "job_attempt_stopped",
                "job_heartbeat_failed",
                "usage_accounting_failed",
                "call_charge_bound_exceeded",
            }:
                raise
            reported.extend(error.usage)
            errors.append(error.code)
            return
        reported.append(output.usage)
        apply_presence(
            signing,
            str(image["page_id"]),
            {
                answer.ref: answer.probability_yes
                for answer in output.answers
                if answer.type == "noul"
            },
        )
        assessed.append(str(image["page_id"]))

    # Bounded task groups limit application concurrency as well as the provider's
    # process-wide gate. The remaining suffix stays unresolved at the ceiling.
    for index in range(0, min(len(selected), 40), 3):
        tasks = [asyncio.create_task(page(image)) for image in selected[index : index + 3]]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        if execution.stopped is not None or any(
            code in errors for code in {"provider_timeout", "clef_call_limit"}
        ):
            break
    if len(assessed) < len(required):
        errors.append("clef_required_locations_uncovered")
    if any(
        location.get("human_escalation")
        for value in signing.values()
        for location in value["required_locations"]
    ):
        errors.append("signing_presence_escalated_to_human")
    async with execution.db.transaction(execution.org_id) as session:
        calls = (
            await session.scalars(
                select(VendorCall).where(
                    VendorCall.job_id == execution.job_id, VendorCall.capability == "vision"
                )
            )
        ).all()
    unresolved = [call for call in calls if call.state in {"pending", "unknown"}]
    coverage.update(
        clef="triaged" if len(assessed) == len(required) and not errors else "partial",
        clef_calls=len(calls),
        clef_cost=str(
            sum(
                (call.charge or Decimal(0) for call in calls if call.state == "completed"),
                Decimal(0),
            )
        ),
        clef_completed_calls=sum(call.state == "completed" for call in calls),
        clef_unresolved_calls=len(unresolved),
        clef_reserved_amount=str(sum((call.reserved_charge for call in unresolved), Decimal(0))),
        clef_assessed_page_ids=assessed,
        clef_required_pages=len(required),
    )
    return coverage, reported, sorted(set(errors))
