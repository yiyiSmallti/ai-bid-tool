"""PostgreSQL gates for screenshot provenance and image response evidence.

Failure modes covered before implementation:

* any of the thirteen screenshot tables lacks ENABLE/FORCE RLS, the tenant
  policy, or grants UPDATE/DELETE to the runtime role;
* a runtime transaction can read another organization or can see rows without
  an organization context;
* a token, agent, worker, missing actor, inactive member, or wrong organization
  can perform the human-only privacy release or withdrawal action;
* direct SQL can attach an image with a forged hash, asset/rendition pair,
  extraction job, selection revision, environment, claim scope, or region;
* a prototype decision can bind a different evidence, card revision,
  rendition, image hash, HTML hash, feature revision, batch selection, or
  previous decision while retaining an otherwise plausible row;
* append-only screenshot provenance can be updated or deleted through the
  runtime role even when a statement matches no rows;
* a vendor archive names bytes, a run or an entry response that differs from
  the succeeded sandbox capture it claims to retain.

The fixture uses only synthetic rows and the real migration triggers.  It does
not call a renderer, model, browser, vendor, or object store.  The suite needs
an isolated PostgreSQL database supplied by ``BID_TEST_ADMIN_URL``; absence of
that database is reported by the shared fixture as a skipped integration test.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models import Base
from app.models.entities import (
    ApiToken,
    Feature,
    FeatureRevision,
    Job,
    Membership,
    Requirement,
    TaskFeature,
    TaskResource,
)
from app.models.response_cards import (
    CardEvidenceLink,
    Evidence,
    ResponseCard,
    ResponseCardRevision,
)
from app.models.sandbox import (
    SandboxArtifact,
    SandboxAttempt,
    SandboxFetchReceipt,
    SandboxInput,
    SandboxRun,
)
from app.models.screenshots import (
    PrototypeDecisionBatch,
    PrototypeEvidenceDecision,
    ScreenshotAnalysisInput,
    ScreenshotAnalysisRun,
    ScreenshotAsset,
    ScreenshotPrivacyReview,
    ScreenshotPrototypeRun,
    ScreenshotRendition,
    ScreenshotSearchCandidate,
    ScreenshotSearchRun,
    ScreenshotSuggestion,
    ScreenshotVendorArchive,
    ScreenshotWithdrawal,
)
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_rls import seeded as seeded  # noqa: F401  # pyright: ignore[reportMissingImports]

SCREENSHOT_TABLES = (
    "screenshot_search_runs",
    "screenshot_search_candidates",
    "screenshot_vendor_archives",
    "screenshot_prototype_runs",
    "screenshot_assets",
    "screenshot_renditions",
    "screenshot_privacy_reviews",
    "screenshot_withdrawals",
    "screenshot_analysis_runs",
    "screenshot_analysis_inputs",
    "screenshot_suggestions",
    "prototype_decision_batches",
    "prototype_evidence_decisions",
)


def runtime_database():
    return Database(Settings())  # pyright: ignore[reportCallIssue]


def db_sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


def actor_context(session, org: UUID, user: UUID, *, kind="session", token=None):
    session.execute(
        text(
            "SELECT set_config('app.current_org',:org,true), "
            "set_config('app.actor_kind',:kind,true), "
            "set_config('app.actor_user_id',:user,true), "
            "set_config('app.actor_token_id',:token,true)"
        ),
        {
            "org": str(org),
            "kind": kind,
            "user": str(user),
            "token": str(token) if token else "",
        },
    )


def clone_revision(previous, user, **changes):
    values = {
        column.name: getattr(previous, column.name)
        for column in ResponseCardRevision.__table__.columns
    }
    values.update(
        id=uuid4(),
        revision=previous.revision + 1,
        created_at=datetime.now(UTC),
        actor_user_id=user,
        actor_token_id=None,
        actor_kind="session",
        origin="human",
        reason=None,
    )
    values.update(changes)
    return ResponseCardRevision(**values)


def synthetic_vendor_capture(session, org, user, task_id, extraction, selection):
    """A succeeded bundle capture written through the real sandbox gates; no network."""
    url = "https://vendor.example.invalid/product"
    url_sha256 = hashlib.sha256(url.encode()).hexdigest()
    job = Job(
        id=uuid4(),
        org_id=org,
        task_id=task_id,
        document_id=extraction.document_id,
        kind="sandbox",
        cache_key=uuid4().hex * 2,
        status="queued",
    )
    session.add(job)
    session.flush()
    capture_input = SandboxInput(
        id=uuid4(),
        org_id=org,
        task_id=task_id,
        document_id=extraction.document_id,
        extraction_job_id=extraction.id,
        purpose="vendor_capture",
        task_resource_id=selection.id,
        product_revision_id=selection.product_revision_id,
        source_field="official_url",
        source_url_sha256=url_sha256,
        encrypted_source_url="synthetic-ciphertext",
        spec={
            "purpose": "vendor_capture",
            "extraction_job_id": str(extraction.id),
            "task_resource_id": str(selection.id),
            "expected_product_revision_id": str(selection.product_revision_id),
            "source_field": "official_url",
            "expected_source_url_sha256": url_sha256,
            "format": "web",
            "pdf_pages": [],
            "viewport": {"width": 1440, "height": 900, "device_scale_factor": 1},
            "archive": "bundle",
            "capture_key": str(uuid4()),
        },
    )
    session.add(capture_input)
    session.flush()
    run = SandboxRun(
        id=uuid4(),
        org_id=org,
        input_id=capture_input.id,
        task_id=task_id,
        document_id=extraction.document_id,
        job_id=job.id,
        request_hash=uuid4().hex * 2,
        profile="vendor-capture-v1",
        policy_revision="synthetic-v1",
        policy_sha256="d" * 64,
        runtime_profile_digest="e" * 64,
        requested_by=user,
        actor_kind="session",
        scope_snapshot=[
            "sandbox:read",
            "sandbox:capture",
            "task:read",
            "resource:read",
            "job:read",
        ],
        capture_key=uuid4(),
    )
    session.add(run)
    session.flush()
    attempt_id = uuid4()
    job.status, job.run_id = "running", attempt_id
    job.lease_until = datetime.now(UTC) + timedelta(hours=1)
    session.flush()
    attempt = SandboxAttempt(
        id=uuid4(),
        org_id=org,
        sandbox_run_id=run.id,
        input_id=capture_input.id,
        task_id=task_id,
        job_id=job.id,
        attempt_id=attempt_id,
        instance_group_ref_hash="f" * 64,
        descriptor={},
        started_at=datetime.now(UTC),
    )
    session.add(attempt)
    session.flush()
    attempt.ended_at = datetime.now(UTC)
    attempt.termination_code, attempt.cleanup_state = "succeeded", "complete"
    job.status = "succeeded"
    session.flush()
    body = b"<html><title>Synthetic vendor page</title></html>"
    artifacts = {}
    for ordinal, kind in enumerate(
        ("capture_png", "rendered_html", "request_manifest", "capture_archive")
    ):
        identifier, digest = uuid4(), hashlib.sha256(kind.encode()).hexdigest()
        artifacts[kind] = SandboxArtifact(
            id=identifier,
            org_id=org,
            sandbox_run_id=run.id,
            attempt_record_id=attempt.id,
            input_id=capture_input.id,
            task_id=task_id,
            kind=kind,
            ordinal=ordinal,
            plaintext_sha256=digest,
            size_bytes=100,
            media_type="image/png" if kind == "capture_png" else "application/octet-stream",
            object_key=f"org/{org}/sandbox-artifacts/{run.id}/{attempt_id}/{identifier}/{digest}",
            provenance_manifest_hash="c" * 64,
            origin="public_web_capture",
            width=1440 if kind == "capture_png" else None,
            height=900 if kind == "capture_png" else None,
        )
    manifest_id = uuid4()
    artifacts["provenance_manifest"] = SandboxArtifact(
        id=manifest_id,
        org_id=org,
        sandbox_run_id=run.id,
        attempt_record_id=attempt.id,
        input_id=capture_input.id,
        task_id=task_id,
        kind="provenance_manifest",
        ordinal=4,
        plaintext_sha256="c" * 64,
        size_bytes=100,
        media_type="application/octet-stream",
        object_key=f"org/{org}/sandbox-artifacts/{run.id}/{attempt_id}/{manifest_id}/{'c' * 64}",
        provenance_manifest_hash="c" * 64,
        origin="public_web_capture",
    )
    session.add_all(artifacts.values())
    session.flush()
    entry = SandboxFetchReceipt(
        id=uuid4(),
        org_id=org,
        sandbox_run_id=run.id,
        attempt_record_id=attempt.id,
        input_id=capture_input.id,
        task_id=task_id,
        request_ordinal=0,
        url_sha256=url_sha256,
        encrypted_request_metadata="synthetic-ciphertext",
        response_sha256=hashlib.sha256(body).hexdigest(),
        response_bytes=len(body),
        status_code=200,
        decision_code="allowed",
        policy_revision="synthetic-v1",
        started_at=attempt.started_at,
        ended_at=attempt.ended_at,
        bundle_artifact_id=artifacts["capture_archive"].id,
    )
    session.add(entry)
    session.flush()
    return run, artifacts, entry


def vendor_archive_row(org, task_id, extraction, selection, run, artifacts, entry, **changes):
    archive = artifacts["capture_archive"]
    values = {
        "id": uuid4(),
        "org_id": org,
        "task_id": task_id,
        "extraction_job_id": extraction.id,
        "task_resource_id": selection.id,
        "product_revision_id": selection.product_revision_id,
        "content_sha256": entry.response_sha256,
        "archive_sha256": archive.plaintext_sha256,
        "storage_key": archive.object_key,
        "descriptor": {
            "sha256": archive.plaintext_sha256,
            "size_bytes": archive.size_bytes,
            "media_type": "application/zip",
        },
        "provenance": {
            "title": "Synthetic vendor page",
            "incomplete": False,
            "failed_request_count": 0,
        },
        "sandbox_run_id": run.id,
        "archive_artifact_id": archive.id,
        "entry_receipt_id": entry.id,
    }
    values.update(changes)
    return ScreenshotVendorArchive(**values)


@pytest.fixture
def screenshot_rows(seeded, admin_engine):
    """Create one genuine row in every screenshot table for both organizations."""
    rows = {table: {} for table in SCREENSHOT_TABLES}
    chains = {}
    for index, org in enumerate(seeded["orgs"]):
        user = seeded["users"][index]
        with Session(admin_engine) as session, session.begin():
            member = session.scalar(
                select(Membership).where(Membership.org_id == org, Membership.user_id == user)
            )
            assert member is not None
            member.role = "technical"
            actor_context(session, org, user)
            task_id = seeded["ids"][org]["task"]
            extraction = session.scalar(
                select(Job).where(
                    Job.org_id == org,
                    Job.task_id == task_id,
                    Job.kind == "extract",
                )
            )
            selection = session.get(TaskResource, seeded["ids"][org]["selection"])
            assert extraction is not None and selection is not None
            requirement = session.scalar(
                select(Requirement).where(
                    Requirement.org_id == org,
                    Requirement.task_id == task_id,
                    Requirement.job_id == extraction.id,
                )
            )
            assert requirement is not None

            search_job = Job(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                document_id=extraction.document_id,
                kind="screenshot_search",
                cache_key=uuid4().hex * 2,
                status="running",
                run_id=uuid4(),
            )
            prototype_job = Job(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                document_id=extraction.document_id,
                kind="prototype_generate",
                cache_key=uuid4().hex * 2,
                status="running",
                run_id=uuid4(),
            )
            analysis_job = Job(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                document_id=extraction.document_id,
                kind="screenshot_analyze",
                cache_key=uuid4().hex * 2,
                status="running",
                run_id=uuid4(),
            )
            session.add_all([search_job, prototype_job, analysis_job])
            session.flush()

            search = ScreenshotSearchRun(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                extraction_job_id=extraction.id,
                task_resource_id=selection.id,
                product_revision_id=selection.product_revision_id,
                job_id=search_job.id,
                input_hash=("1" if index == 0 else "2") * 64,
                manifest={"fixture": "synthetic search scope"},
            )
            session.add(search)
            session.flush()
            candidate = ScreenshotSearchCandidate(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                extraction_job_id=extraction.id,
                search_run_id=search.id,
                ref=f"synthetic-{index}",
                source_url=f"https://vendor-{index}.example.invalid/product",
                title="Synthetic vendor source",
            )
            session.add(candidate)
            session.flush()
            capture = synthetic_vendor_capture(session, org, user, task_id, extraction, selection)
            archive = vendor_archive_row(org, task_id, extraction, selection, *capture)
            session.add(archive)

            feature = Feature(id=uuid4(), org_id=org, created_by=user, current_revision=1)
            session.add(feature)
            session.flush()
            feature_revision = FeatureRevision(
                id=uuid4(),
                org_id=org,
                feature_id=feature.id,
                product_id=selection.product_id,
                revision=1,
                data={
                    "product_id": str(selection.product_id),
                    "name": "Synthetic prototype feature",
                    "description": "Synthetic database-gate fixture only.",
                    "status": "developing",
                },
            )
            session.add(feature_revision)
            session.flush()
            task_feature = TaskFeature(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                feature_id=feature.id,
                feature_revision_id=feature_revision.id,
            )
            session.add(task_feature)
            session.flush()

            prototype = ScreenshotPrototypeRun(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                extraction_job_id=extraction.id,
                requirement_id=requirement.id,
                task_feature_id=task_feature.id,
                feature_revision_id=feature_revision.id,
                generation_job_id=prototype_job.id,
                input_hash="5" * 64,
                html_sha256="6" * 64,
                html_storage_key=f"org/{org}/synthetic/prototype.html",
                source_image_sha256="7" * 64,
                source_image_key=f"org/{org}/synthetic/prototype.png",
                sandbox_receipt_id="synthetic-sandbox-receipt",
                sandbox_receipt_sha256="8" * 64,
                provenance={
                    "provider": "synthetic",
                    "model": "synthetic",
                    "catalog_identity": "synthetic-test-only",
                },
            )
            session.add(prototype)
            session.flush()
            asset = ScreenshotAsset(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                extraction_job_id=extraction.id,
                source_kind="prototype_render",
                image_kind="prototype",
                origin="prototype",
                task_feature_id=task_feature.id,
                feature_revision_id=feature_revision.id,
                prototype_run_id=prototype.id,
                source_sha256=prototype.source_image_sha256,
                source_hash_assurance="server_verified",
                source_width=16,
                source_height=10,
                source={"kind": "prototype_render", "prototype_run_id": str(prototype.id)},
                received_by=user,
                received_at=datetime.now(UTC),
                idempotency_key=uuid4(),
                request_hash="9" * 64,
            )
            session.add(asset)
            session.flush()
            rendition_id, privacy_id = uuid4(), uuid4()
            rendition = ScreenshotRendition(
                id=rendition_id,
                org_id=org,
                task_id=task_id,
                extraction_job_id=extraction.id,
                asset_id=asset.id,
                privacy_review_id=privacy_id,
                source_sha256=asset.source_sha256,
                upload_sha256="a" * 64,
                image_sha256="b" * 64,
                plan_sha256="c" * 64,
                plan={
                    "redact": [],
                    "crop": {"x": 0, "y": 0, "width": 16, "height": 10},
                    "boxes": [],
                },
                mapping={
                    "crop": {"x": 0, "y": 0, "width": 16, "height": 10},
                    "content_offset_x": 0,
                    "content_offset_y": 0,
                    "content_width": 16,
                    "content_height": 10,
                    "footer_height": 0,
                },
                image={
                    "sha256": "b" * 64,
                    "size_bytes": 100,
                    "width_px": 16,
                    "height_px": 10,
                    "media_type": "image/png",
                },
                profile="prototype-clean-v1",
                storage_key=f"org/{org}/synthetic/{asset.id}/{rendition_id}.png",
                actor_user_id=user,
            )
            session.add(rendition)
            session.flush()
            review = ScreenshotPrivacyReview(
                id=privacy_id,
                org_id=org,
                task_id=task_id,
                extraction_job_id=extraction.id,
                asset_id=asset.id,
                rendition_id=rendition.id,
                reviewed_upload_sha256=rendition.upload_sha256,
                stored_image_sha256=rendition.image_sha256,
                reviewed_by=user,
                rule_version="screenshot-privacy-v1",
            )
            session.add(review)

            withdrawn_asset = ScreenshotAsset(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                extraction_job_id=extraction.id,
                source_kind="upload",
                image_kind="screenshot",
                origin="user",
                task_feature_id=task_feature.id,
                feature_revision_id=feature_revision.id,
                source_sha256="d" * 64,
                source_hash_assurance="client_declared",
                source_width=16,
                source_height=10,
                source={
                    "kind": "upload",
                    "task_feature_id": str(task_feature.id),
                    "image_kind": "screenshot",
                    "environment": "test",
                },
                received_by=user,
                received_at=datetime.now(UTC),
                idempotency_key=uuid4(),
                request_hash="e" * 64,
            )
            session.add(withdrawn_asset)
            session.flush()
            withdrawal = ScreenshotWithdrawal(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                extraction_job_id=extraction.id,
                asset_id=withdrawn_asset.id,
                reason="Synthetic withdrawn fixture",
                withdrawn_by=user,
            )
            session.add(withdrawal)

            analysis = ScreenshotAnalysisRun(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                extraction_job_id=extraction.id,
                job_id=analysis_job.id,
                run_id=analysis_job.run_id,
                input_hash="f" * 64,
                input_manifest={"fixture": "synthetic analysis"},
                completion="complete",
                rejected=[],
            )
            session.add(analysis)
            session.flush()
            image_input = ScreenshotAnalysisInput(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                extraction_job_id=extraction.id,
                analysis_run_id=analysis.id,
                ref="image-1",
                asset_id=asset.id,
                rendition_id=rendition.id,
                privacy_review_id=review.id,
                content_sha256=rendition.image_sha256,
            )
            requirement_input = ScreenshotAnalysisInput(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                extraction_job_id=extraction.id,
                analysis_run_id=analysis.id,
                ref="requirement-1",
                requirement_id=requirement.id,
                content_sha256="0" * 64,
            )
            session.add_all([image_input, requirement_input])
            session.flush()
            suggestion = ScreenshotSuggestion(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                extraction_job_id=extraction.id,
                analysis_run_id=analysis.id,
                image_input_id=image_input.id,
                requirement_input_id=requirement_input.id,
                proposal={
                    "image_ref": image_input.ref,
                    "requirement_ref": requirement_input.ref,
                    "purpose": "match_requirements",
                    "suggested_text": "Synthetic match only",
                },
            )
            session.add(suggestion)

            card = session.get(ResponseCard, seeded["ids"][org]["card"])
            assert card is not None
            previous = session.get(ResponseCardRevision, card.current_revision_id)
            assert previous is not None
            evidence = Evidence(
                id=uuid4(),
                org_id=org,
                task_id=task_id,
                card_id=card.id,
                kind="image_region",
                task_feature_id=task_feature.id,
                feature_revision_id=feature_revision.id,
                quote=None,
                material_kind="prototype",
                quote_check="unreviewed_image",
                source_sha256=asset.source_sha256,
                screenshot_asset_id=asset.id,
                screenshot_rendition_id=rendition.id,
                image_sha256=rendition.image_sha256,
                region={"x": 0, "y": 0, "width": 8, "height": 8},
                claim_scope="functional_observation",
                visual_observation="The synthetic region shows a workflow control.",
            )
            session.add(evidence)
            pending = clone_revision(
                previous,
                user,
                state="pending_review",
                confirmed_by=None,
                confirmed_at=None,
                disposition=None,
                disposition_by=None,
                disposition_at=None,
            )
            session.add(pending)
            session.flush()
            session.add(
                CardEvidenceLink(
                    id=uuid4(),
                    org_id=org,
                    card_id=card.id,
                    revision_id=pending.id,
                    evidence_id=evidence.id,
                )
            )
            card.current_revision_id, card.revision = pending.id, pending.revision
            session.flush()
            session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            session.execute(text("SET CONSTRAINTS ALL DEFERRED"))

            rows["screenshot_search_runs"][org] = search.id
            rows["screenshot_search_candidates"][org] = candidate.id
            rows["screenshot_vendor_archives"][org] = archive.id
            rows["screenshot_prototype_runs"][org] = prototype.id
            rows["screenshot_assets"][org] = {asset.id, withdrawn_asset.id}
            rows["screenshot_renditions"][org] = rendition.id
            rows["screenshot_privacy_reviews"][org] = review.id
            rows["screenshot_withdrawals"][org] = withdrawal.id
            rows["screenshot_analysis_runs"][org] = analysis.id
            rows["screenshot_analysis_inputs"][org] = {
                image_input.id,
                requirement_input.id,
            }
            rows["screenshot_suggestions"][org] = suggestion.id
            chains[org] = {
                "task": task_id,
                "extraction": extraction.id,
                "card": card.id,
                "evidence": evidence.id,
                "asset": asset.id,
                "rendition": rendition.id,
                "image_sha256": rendition.image_sha256,
                "source_sha256": asset.source_sha256,
                "feature": task_feature.id,
                "feature_revision": feature_revision.id,
                "html_sha256": prototype.html_sha256,
            }

    for index, org in enumerate(seeded["orgs"]):
        user = seeded["users"][index]
        chain = chains[org]
        with Session(admin_engine) as session, session.begin():
            actor_context(session, org, user)
            card = session.get(ResponseCard, chain["card"])
            evidence = session.get(Evidence, chain["evidence"])
            assert card is not None and evidence is not None
            pending = session.get(ResponseCardRevision, card.current_revision_id)
            assert pending is not None
            now = datetime.now(UTC)
            evidence.confirmed_by = user
            evidence.confirmed_at = now
            evidence.quote_check = "human_image_review"
            confirmed = clone_revision(
                pending,
                user,
                state="confirmed",
                disposition="respond",
                disposition_by=user,
                disposition_at=now,
                confirmed_by=user,
                confirmed_at=now,
                reviewed_warning_codes=[
                    "image_visible_scope_only",
                    "prototype_delivery_obligation",
                    "image_crop_review",  # the fixture rendition is cropped
                ],
                reason="Reviewed synthetic prototype obligations and visible scope.",
            )
            session.add(confirmed)
            session.flush()
            session.add(
                CardEvidenceLink(
                    id=uuid4(),
                    org_id=org,
                    card_id=card.id,
                    revision_id=confirmed.id,
                    evidence_id=evidence.id,
                )
            )
            card.current_revision_id, card.revision = confirmed.id, confirmed.revision
            session.flush()
            session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            session.execute(text("SET CONSTRAINTS ALL DEFERRED"))

            target = {
                "evidence_id": str(evidence.id),
                "card_revision_id": str(confirmed.id),
                "rendition_id": str(chain["rendition"]),
                "image_sha256": chain["image_sha256"],
                "html_sha256": chain["html_sha256"],
                "task_feature_id": str(chain["feature"]),
                "expected_previous_decision_id": None,
            }
            batch = PrototypeDecisionBatch(
                id=uuid4(),
                org_id=org,
                task_id=chain["task"],
                extraction_job_id=chain["extraction"],
                module_label="Synthetic database gate",
                task_feature_ids=[str(chain["feature"])],
                target_manifest=[target],
                input_hash="1" * 64,
                request_hash="2" * 64,
                idempotency_key=uuid4(),
                decided_by=user,
            )
            session.add(batch)
            session.flush()
            decision = PrototypeEvidenceDecision(
                id=uuid4(),
                org_id=org,
                task_id=chain["task"],
                extraction_job_id=chain["extraction"],
                batch_id=batch.id,
                evidence_id=evidence.id,
                card_id=card.id,
                card_revision_id=confirmed.id,
                asset_id=chain["asset"],
                rendition_id=chain["rendition"],
                image_sha256=chain["image_sha256"],
                html_sha256=chain["html_sha256"],
                task_feature_id=chain["feature"],
                feature_revision_id=chain["feature_revision"],
                decision="keep",
                keep_basis="will_deliver",
                decided_by=user,
            )
            session.add(decision)
            token = ApiToken(
                id=uuid4(),
                org_id=org,
                user_id=user,
                name="synthetic screenshot token",
                digest=("3" if index == 0 else "4") * 64,
                scopes=["screenshot:read"],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            session.add(token)
            session.flush()
            session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            rows["prototype_decision_batches"][org] = batch.id
            rows["prototype_evidence_decisions"][org] = decision.id
            chain.update(
                revision=confirmed.id,
                batch=batch.id,
                decision=decision.id,
                token=token.id,
            )

    return {**seeded, "rows": rows, "prototype": chains[seeded["orgs"][0]]}


@pytest.mark.parametrize("table", SCREENSHOT_TABLES)
def test_screenshot_tables_force_tenant_rls_and_append_only_grants(table, admin_engine):
    with admin_engine.connect() as connection:
        flags = connection.execute(
            text(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                "WHERE oid=to_regclass(:table)"
            ),
            {"table": f"public.{table}"},
        ).one()
        assert flags.relrowsecurity and flags.relforcerowsecurity
        policies = (
            connection.execute(
                text(
                    "SELECT policyname,qual,with_check FROM pg_policies "
                    "WHERE schemaname='public' AND tablename=:table"
                ),
                {"table": table},
            )
            .mappings()
            .all()
        )
        assert len(policies) == 1 and policies[0]["policyname"] == "tenant_scope"
        assert "app.current_org" in policies[0]["qual"]
        assert "app.current_org" in policies[0]["with_check"]
        privileges = set(
            connection.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_schema='public' AND table_name=:table AND grantee='bid_app'"
                ),
                {"table": table},
            ).scalars()
        )
        assert privileges == {"SELECT", "INSERT"}


@pytest.mark.parametrize("table", SCREENSHOT_TABLES)
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
async def test_runtime_role_cannot_mutate_screenshot_history(table, operation, tenants):
    db = runtime_database()
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(tenants["orgs"][0]) as session:
                statement = (
                    f'UPDATE "{table}" SET org_id=org_id WHERE false'
                    if operation == "UPDATE"
                    else f'DELETE FROM "{table}" WHERE false'
                )
                await session.execute(text(statement))
        assert db_sqlstate(error.value) == "42501"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("table", SCREENSHOT_TABLES)
async def test_runtime_queries_show_only_the_selected_organization(table, screenshot_rows):
    org_a, org_b = screenshot_rows["orgs"]
    model = Base.metadata.tables[table]
    db = runtime_database()
    try:
        async with db.transaction(org_a) as session:
            expected = screenshot_rows["rows"][table][org_a]
            expected = expected if isinstance(expected, set) else {expected}
            assert set(await session.scalars(select(model.c.id))) == expected
        async with db.transaction(org_b) as session:
            expected = screenshot_rows["rows"][table][org_b]
            expected = expected if isinstance(expected, set) else {expected}
            assert set(await session.scalars(select(model.c.id))) == expected
        async with db.transaction() as session:
            assert list(await session.scalars(select(model.c.id))) == []
    finally:
        await db.engine.dispose()


async def test_token_context_cannot_create_a_human_privacy_release(screenshot_rows):
    org = screenshot_rows["orgs"][0]
    fixed = screenshot_rows["prototype"]
    db = runtime_database()
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org) as session:
                await session.execute(
                    text(
                        "SELECT set_config('app.actor_kind','token',true), "
                        "set_config('app.actor_user_id',:user,true), "
                        "set_config('app.actor_token_id',:token,true)"
                    ),
                    {
                        "user": str(screenshot_rows["users"][0]),
                        "token": str(fixed["token"]),
                    },
                )
                session.add(
                    ScreenshotPrivacyReview(
                        id=uuid4(),
                        org_id=org,
                        task_id=fixed["task"],
                        extraction_job_id=fixed["extraction"],
                        asset_id=fixed["asset"],
                        rendition_id=fixed["rendition"],
                        reviewed_upload_sha256="8" * 64,
                        stored_image_sha256=fixed["image_sha256"],
                        reviewed_by=screenshot_rows["users"][0],
                        rule_version="screenshot-privacy-v1",
                    )
                )
        assert db_sqlstate(error.value) == "42501"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "region,source_override",
    [
        ({"x": 15, "y": 0, "width": 2, "height": 1}, None),
        ({"x": 0, "y": 0, "width": 1}, None),
        ({"x": 0, "y": 0, "width": 1, "height": 1}, "0" * 64),
    ],
)
async def test_direct_sql_rejects_invalid_fixed_image_binding(
    region, source_override, screenshot_rows
):
    org = screenshot_rows["orgs"][0]
    fixed = screenshot_rows["prototype"]
    db = runtime_database()
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org) as session:
                await session.execute(
                    text(
                        "SELECT set_config('app.actor_kind','session',true), "
                        "set_config('app.actor_user_id',:user,true), "
                        "set_config('app.actor_token_id','',true)"
                    ),
                    {"user": str(screenshot_rows["users"][0])},
                )
                session.add(
                    Evidence(
                        id=uuid4(),
                        org_id=org,
                        task_id=fixed["task"],
                        card_id=fixed["card"],
                        kind="image_region",
                        task_feature_id=fixed["feature"],
                        feature_revision_id=fixed["feature_revision"],
                        quote=None,
                        material_kind="prototype",
                        quote_check="unreviewed_image",
                        source_sha256=source_override or fixed["source_sha256"],
                        screenshot_asset_id=fixed["asset"],
                        screenshot_rendition_id=fixed["rendition"],
                        image_sha256=fixed["image_sha256"],
                        region=region,
                        claim_scope="functional_observation",
                        visual_observation="This forged region crosses the content boundary.",
                    )
                )
        assert db_sqlstate(error.value) == "23514"
    finally:
        await db.engine.dispose()


async def test_prototype_decision_rejects_a_mismatched_fixed_html_hash(screenshot_rows):
    org = screenshot_rows["orgs"][0]
    fixed = screenshot_rows["prototype"]
    db = runtime_database()
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org) as session:
                await session.execute(
                    text(
                        "SELECT set_config('app.actor_kind','session',true), "
                        "set_config('app.actor_user_id',:user,true), "
                        "set_config('app.actor_token_id','',true)"
                    ),
                    {"user": str(screenshot_rows["users"][0])},
                )
                session.add(
                    PrototypeEvidenceDecision(
                        id=uuid4(),
                        org_id=org,
                        task_id=fixed["task"],
                        extraction_job_id=fixed["extraction"],
                        batch_id=fixed["batch"],
                        evidence_id=fixed["evidence"],
                        card_id=fixed["card"],
                        card_revision_id=fixed["revision"],
                        asset_id=fixed["asset"],
                        rendition_id=fixed["rendition"],
                        image_sha256=fixed["image_sha256"],
                        html_sha256="f" * 64,
                        task_feature_id=fixed["feature"],
                        feature_revision_id=fixed["feature_revision"],
                        decision="keep",
                        keep_basis="will_deliver",
                        decided_by=screenshot_rows["users"][0],
                    )
                )
        assert db_sqlstate(error.value) == "23514"
    finally:
        await db.engine.dispose()


async def test_direct_sql_rejects_keep_decision_without_a_basis(screenshot_rows):
    org = screenshot_rows["orgs"][0]
    fixed = screenshot_rows["prototype"]
    user = screenshot_rows["users"][0]
    db = runtime_database()
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org) as session:
                await session.execute(
                    text(
                        "SELECT set_config('app.actor_kind','session',true), "
                        "set_config('app.actor_user_id',:user,true), "
                        "set_config('app.actor_token_id','',true)"
                    ),
                    {"user": str(user)},
                )
                target = {
                    "evidence_id": str(fixed["evidence"]),
                    "card_revision_id": str(fixed["revision"]),
                    "rendition_id": str(fixed["rendition"]),
                    "image_sha256": fixed["image_sha256"],
                    "html_sha256": fixed["html_sha256"],
                    "task_feature_id": str(fixed["feature"]),
                    "expected_previous_decision_id": str(fixed["decision"]),
                }
                batch = PrototypeDecisionBatch(
                    id=uuid4(),
                    org_id=org,
                    task_id=fixed["task"],
                    extraction_job_id=fixed["extraction"],
                    module_label="Synthetic missing keep basis",
                    task_feature_ids=[str(fixed["feature"])],
                    target_manifest=[target],
                    input_hash="5" * 64,
                    request_hash="6" * 64,
                    idempotency_key=uuid4(),
                    decided_by=user,
                )
                session.add(batch)
                await session.flush()
                session.add(
                    PrototypeEvidenceDecision(
                        id=uuid4(),
                        org_id=org,
                        task_id=fixed["task"],
                        extraction_job_id=fixed["extraction"],
                        batch_id=batch.id,
                        evidence_id=fixed["evidence"],
                        card_id=fixed["card"],
                        card_revision_id=fixed["revision"],
                        asset_id=fixed["asset"],
                        rendition_id=fixed["rendition"],
                        image_sha256=fixed["image_sha256"],
                        html_sha256=fixed["html_sha256"],
                        task_feature_id=fixed["feature"],
                        feature_revision_id=fixed["feature_revision"],
                        previous_decision_id=fixed["decision"],
                        decision="keep",
                        keep_basis=None,
                        reason="Synthetic attempt to change a decision without a keep basis.",
                        decided_by=user,
                    )
                )
        assert db_sqlstate(error.value) == "23514"
    finally:
        await db.engine.dispose()


async def test_selection_cannot_reactivate_old_prototype_decisions(screenshot_rows):
    """Selecting a prior revision must create a new selection identity, never revive history."""
    org = screenshot_rows["orgs"][0]
    fixed = screenshot_rows["prototype"]
    db = runtime_database()
    try:
        async with db.transaction(org) as session:
            await session.execute(
                text("UPDATE task_features SET active=false WHERE id=:id"), {"id": fixed["feature"]}
            )
        with pytest.raises(DBAPIError) as rejected:
            async with db.transaction(org) as session:
                await session.execute(
                    text("UPDATE task_features SET active=true WHERE id=:id"),
                    {"id": fixed["feature"]},
                )
        assert db_sqlstate(rejected.value) == "42501"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "mutation",
    [
        "archive_sha256",
        "content_sha256",
        "storage_key",
        "archive_artifact",
        "entry_receipt",
        "search_candidate",
        "token_actor",
        "incomplete_mismatch",
        None,
    ],
)
def test_direct_sql_binds_vendor_archives_to_their_capture(mutation, screenshot_rows, admin_engine):
    org, user = screenshot_rows["orgs"][0], screenshot_rows["users"][0]
    with Session(admin_engine) as session, session.begin():
        actor_context(session, org, user)
        task_id = screenshot_rows["ids"][org]["task"]
        extraction = session.scalar(
            select(Job).where(Job.org_id == org, Job.task_id == task_id, Job.kind == "extract")
        )
        selection = session.get(TaskResource, screenshot_rows["ids"][org]["selection"])
        assert extraction is not None and selection is not None
        run, artifacts, entry = synthetic_vendor_capture(
            session, org, user, task_id, extraction, selection
        )
        candidate = session.scalar(
            select(ScreenshotSearchCandidate.id).where(ScreenshotSearchCandidate.org_id == org)
        )
        changes = {
            "archive_sha256": {"archive_sha256": "0" * 64},
            "content_sha256": {"content_sha256": "0" * 64},
            "storage_key": {"storage_key": f"org/{org}/synthetic/other.zip"},
            "archive_artifact": {"archive_artifact_id": artifacts["capture_png"].id},
            "entry_receipt": {"entry_receipt_id": uuid4()},
            "search_candidate": {"search_candidate_id": candidate},
            "incomplete_mismatch": {"provenance": {"incomplete": False, "failed_request_count": 1}},
        }.get(mutation or "", {})
        if mutation == "archive_artifact":
            png = artifacts["capture_png"]
            changes.update(archive_sha256=png.plaintext_sha256, storage_key=png.object_key)
            changes["descriptor"] = {"sha256": png.plaintext_sha256, "size_bytes": 100}
        if mutation == "token_actor":
            actor_context(session, org, user, kind="token", token=uuid4())
        row = vendor_archive_row(
            org, task_id, extraction, selection, run, artifacts, entry, **changes
        )
        if mutation is None:
            session.add(row)
            session.flush()
            return
        with pytest.raises(DBAPIError) as error:
            with session.begin_nested():
                session.add(row)
                session.flush()
        assert db_sqlstate(error.value) in {"23514", "23503", "42501"}
