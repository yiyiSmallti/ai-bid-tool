"""Resource version bindings, immutable history and source descriptor gates."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models import Base
from app.models.entities import (
    AuditLog,
    Certificate,
    CertificateFile,
    CertificateRevision,
    EvidenceSource,
    Feature,
    FeatureRevision,
    OrgProfile,
    OrgProfileRevision,
    Product,
    ProductRevision,
    Task,
    TaskCertificate,
    TaskFeature,
    TaskOrgProfile,
    TaskResource,
    TaskTemplate,
    Template,
    TemplateRevision,
)
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from task_fixtures import actor_context_async, finish_scope, seed_task


@pytest.fixture
def resource_rows(database):
    # `database` empties the tables first; a gateway installation row left by an earlier
    # test on the worker would otherwise reject these member-less writes.
    ids = {}
    with Session(database) as session, session.begin():
        for group in range(2):
            task = Task(id=uuid4(), name="Synthetic resource task")
            seed_task(session, task)
            product = Product(id=uuid4(), current_revision=1)
            session.add_all([task, product])
            session.flush()
            revision = ProductRevision(
                id=uuid4(), product_id=product.id, revision=1, data={"name": "Synthetic"}
            )
            session.add(revision)
            session.flush()
            feature = Feature(id=uuid4(), current_revision=1)
            session.add(feature)
            session.flush()
            feature_revision = FeatureRevision(
                id=uuid4(),
                feature_id=feature.id,
                product_id=product.id,
                revision=1,
                data={
                    "product_id": str(product.id),
                    "name": "Synthetic feature",
                    "description": "Synthetic declaration",
                    "status": "planned",
                },
            )
            session.add(feature_revision)
            session.flush()
            feature_snapshot = TaskFeature(
                id=uuid4(),
                task_id=task.id,
                feature_id=feature.id,
                feature_revision_id=feature_revision.id,
            )
            session.add(feature_snapshot)
            snapshot = TaskResource(
                id=uuid4(), task_id=task.id, product_id=product.id, product_revision_id=revision.id
            )
            event = AuditLog(id=uuid4(), action="synthetic", object_id=product.id, details={})
            session.add_all([snapshot, event])
            certificate = Certificate(id=uuid4(), current_revision=1)
            session.add(certificate)
            session.flush()
            certificate_revision = CertificateRevision(
                id=uuid4(),
                certificate_id=certificate.id,
                revision=1,
                data={
                    "kind": "qualification",
                    "name": "Synthetic",
                    "number": "SYNTHETIC-ONLY",
                    "valid_from": None,
                    "valid_until": None,
                },
            )
            session.add(certificate_revision)
            session.flush()
            certificate_file = CertificateFile(
                id=uuid4(),
                certificate_id=certificate.id,
                certificate_revision_id=certificate_revision.id,
                file={
                    "name": "synthetic.pdf",
                    "sha256": "a" * 64,
                    "size_bytes": 1,
                    "page_count": 1,
                    "media_type": "application/pdf",
                },
                storage_key=f"certificate/{certificate.id}/{certificate_revision.id}/{'a' * 64}.pdf",
            )
            session.add(certificate_file)
            certificate_snapshot = TaskCertificate(
                id=uuid4(),
                task_id=task.id,
                certificate_id=certificate.id,
                certificate_revision_id=certificate_revision.id,
            )
            session.add(certificate_snapshot)
            profile = OrgProfile(id=uuid4(), current_revision=1)
            session.add(profile)
            session.flush()
            profile_revision = OrgProfileRevision(
                id=uuid4(),
                profile_id=profile.id,
                revision=1,
                data={
                    "name": "Synthetic declaration",
                    "registration_details": None,
                    "performance_summary": None,
                    "standard_wording": None,
                },
            )
            session.add(profile_revision)
            session.flush()
            profile_snapshot = TaskOrgProfile(
                id=uuid4(),
                task_id=task.id,
                profile_id=profile.id,
                profile_revision_id=profile_revision.id,
            )
            session.add(profile_snapshot)
            template = Template(id=uuid4(), current_revision=1)
            session.add(template)
            session.flush()
            template_revision_id = uuid4()
            template_revision = TemplateRevision(
                id=template_revision_id,
                template_id=template.id,
                revision=1,
                data={"name": "Synthetic template"},
                file={
                    "name": "synthetic.docx",
                    "sha256": "a" * 64,
                    "size_bytes": 1,
                    "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                },
                storage_key=f"template/{template.id}/{template_revision_id}/{'a' * 64}.docx",
            )
            session.add(template_revision)
            session.flush()
            template_snapshot = TaskTemplate(
                id=uuid4(),
                task_id=task.id,
                template_id=template.id,
                template_revision_id=template_revision.id,
            )
            session.add(template_snapshot)
            session.flush()
            source_id = uuid4()
            source = EvidenceSource(
                id=source_id,
                task_id=task.id,
                task_certificate_id=certificate_snapshot.id,
                certificate_id=certificate.id,
                certificate_revision_id=certificate_revision.id,
                certificate_file_id=certificate_file.id,
                page=1,
                preview={
                    "name": "synthetic.png",
                    "sha256": "b" * 64,
                    "size_bytes": 1,
                    "media_type": "image/png",
                    "width_px": 1,
                    "height_px": 1,
                },
                storage_key=f"evidence-source/{source_id}/{'b' * 64}.png",
                rendered_at=datetime.now(UTC),
            )
            session.add(source)
            ids[group] = {
                "evidence_source": source.id,
                "template": template.id,
                "template_revision": template_revision.id,
                "template_snapshot": template_snapshot.id,
                "profile": profile.id,
                "profile_revision": profile_revision.id,
                "profile_snapshot": profile_snapshot.id,
                "certificate_file": certificate_file.id,
                "certificate": certificate.id,
                "certificate_revision": certificate_revision.id,
                "certificate_snapshot": certificate_snapshot.id,
                "task": task.id,
                "product": product.id,
                "revision": revision.id,
                "snapshot": snapshot.id,
                "audit": event.id,
                "feature": feature.id,
                "feature_revision": feature_revision.id,
                "feature_snapshot": feature_snapshot.id,
            }
            finish_scope(session)
    return {"ids": ids}


async def test_snapshot_rejects_another_product_revision(resource_rows):
    group = 0
    own = resource_rows["ids"][group]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                product = Product(current_revision=1)
                session.add(product)
                await session.flush()
                revision = ProductRevision(product_id=product.id, revision=1, data={})
                session.add(revision)
                await session.flush()
                session.add(
                    TaskResource(
                        task_id=own["task"],
                        product_id=own["product"],
                        product_revision_id=revision.id,
                        lot="forged",
                    )
                )
        assert error.value.orig.sqlstate == "23503"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "table,assignment",
    [
        ("evidence_sources", "preview=preview"),
        ("product_revisions", "data='{}'::jsonb"),
        ("audit_logs", "details='{}'::jsonb"),
        ("task_resources", "product_revision_id=product_revision_id"),
        ("feature_revisions", "data='{}'::jsonb"),
        ("task_features", "feature_revision_id=feature_revision_id"),
        ("certificate_revisions", "data='{}'::jsonb"),
        ("task_certificates", "certificate_revision_id=certificate_revision_id"),
        ("certificate_files", "file='{}'::jsonb"),
        ("template_revisions", "file='{}'::jsonb"),
        ("task_templates", "template_revision_id=template_revision_id"),
        ("templates", "created_at=created_at"),
        ("org_profile_revisions", "data='{}'::jsonb"),
        ("task_org_profiles", "profile_revision_id=profile_revision_id"),
        ("certificate_revisions", "data='{}'::jsonb"),
        ("task_certificates", "certificate_revision_id=certificate_revision_id"),
    ],
)
async def test_owned_revision_audit_and_snapshot_payloads_are_immutable(
    table, assignment, resource_rows
):
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                await session.execute(text(f'UPDATE "{table}" SET {assignment}'))
        assert error.value.orig.sqlstate == "42501"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("forgery", ["feature_revision", "feature_current"])
async def test_feature_keys_reject_forgery(forgery, resource_rows):
    first_group, second_group = 0, 1
    own, other = (resource_rows["ids"][first_group], resource_rows["ids"][second_group])
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                if forgery == "feature_revision":
                    session.add(
                        TaskFeature(
                            task_id=own["task"],
                            feature_id=own["feature"],
                            feature_revision_id=other["feature_revision"],
                            lot="forged",
                        )
                    )
                else:
                    await session.execute(
                        text("UPDATE features SET current_revision=999 WHERE id=:id"),
                        {"id": own["feature"]},
                    )
        assert error.value.orig.sqlstate == "23503"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "data", [{"status": "planned", "product_id": None}, {"status": "unknown"}, {"status": None}]
)
async def test_database_rejects_inconsistent_product_and_declared_status(data, resource_rows):
    group = 0
    own = resource_rows["ids"][group]
    values = {"product_id": str(own["product"]), "status": "planned", **data}
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                session.add(
                    FeatureRevision(
                        feature_id=own["feature"],
                        product_id=own["product"],
                        revision=2,
                        data=values,
                    )
                )
        assert error.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()


async def test_snapshot_rejects_another_feature_revision(resource_rows):
    group = 0
    own = resource_rows["ids"][group]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                feature = Feature(current_revision=1)
                session.add(feature)
                await session.flush()
                revision = FeatureRevision(
                    feature_id=feature.id,
                    product_id=own["product"],
                    revision=1,
                    data={"product_id": str(own["product"]), "status": "planned"},
                )
                session.add(revision)
                await session.flush()
                session.add(
                    TaskFeature(
                        task_id=own["task"],
                        feature_id=own["feature"],
                        feature_revision_id=revision.id,
                        lot="forged",
                    )
                )
        assert error.value.orig.sqlstate == "23503"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("forgery", ["revision", "current_revision"])
async def test_composite_keys_and_current_pointer_reject_forgery(forgery, resource_rows):
    first_group, second_group = 0, 1
    own, other = (resource_rows["ids"][first_group], resource_rows["ids"][second_group])
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                if forgery == "revision":
                    session.add(
                        TaskResource(
                            task_id=own["task"],
                            product_id=own["product"],
                            product_revision_id=other["revision"],
                            lot="forged",
                        )
                    )
                else:
                    await session.execute(
                        text("UPDATE products SET current_revision=999 WHERE id=:id"),
                        {"id": own["product"]},
                    )
        assert error.value.orig.sqlstate == "23503"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("forgery", ["revision", "current_revision"])
async def test_certificate_composite_keys_and_current_pointer_reject_forgery(
    forgery, resource_rows
):
    first_group, second_group = 0, 1
    own, other = (resource_rows["ids"][first_group], resource_rows["ids"][second_group])
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                if forgery == "revision":
                    session.add(
                        TaskCertificate(
                            task_id=own["task"],
                            certificate_id=own["certificate"],
                            certificate_revision_id=other["certificate_revision"],
                            lot="forged",
                        )
                    )
                else:
                    await session.execute(
                        text("UPDATE certificates SET current_revision=999 WHERE id=:id"),
                        {"id": own["certificate"]},
                    )
        assert error.value.orig.sqlstate == "23503"
    finally:
        await db.engine.dispose()


async def test_snapshot_rejects_another_certificate_revision(resource_rows):
    group = 0
    own = resource_rows["ids"][group]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                certificate = Certificate(current_revision=1)
                session.add(certificate)
                await session.flush()
                revision = CertificateRevision(
                    certificate_id=certificate.id,
                    revision=1,
                    data={"kind": "personnel", "name": "Synthetic", "number": "SYNTHETIC-ONLY"},
                )
                session.add(revision)
                await session.flush()
                session.add(
                    TaskCertificate(
                        task_id=own["task"],
                        certificate_id=own["certificate"],
                        certificate_revision_id=revision.id,
                        lot="forged",
                    )
                )
        assert error.value.orig.sqlstate == "23503"
        assert (
            error.value.orig.diag.constraint_name
            == "task_certificates_certificate_id_certificate_revisi_fkey"
        )
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "patch",
    [
        {"kind": None},
        {"kind": "invalid"},
        {"name": None},
        {"number": "  "},
        {"valid_from": "2027-01-01", "valid_until": "2026-01-01"},
    ],
)
async def test_database_rejects_invalid_certificate_declarations(patch, resource_rows):
    group = 0
    own = resource_rows["ids"][group]
    values = {"kind": "qualification", "name": "Synthetic", "number": "SYNTHETIC-ONLY", **patch}
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                session.add(
                    CertificateRevision(certificate_id=own["certificate"], revision=2, data=values)
                )
        assert error.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("forgery", ["revision", "current_revision"])
async def test_profile_composite_keys_and_current_pointer_reject_forgery(forgery, resource_rows):
    first_group, second_group = 0, 1
    own, other = (resource_rows["ids"][first_group], resource_rows["ids"][second_group])
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                if forgery == "revision":
                    session.add(
                        TaskOrgProfile(
                            task_id=own["task"],
                            profile_id=own["profile"],
                            profile_revision_id=other["profile_revision"],
                            lot="forged",
                        )
                    )
                else:
                    await session.execute(
                        text("UPDATE org_profiles SET current_revision=999 WHERE id=:id"),
                        {"id": own["profile"]},
                    )
        assert error.value.orig.sqlstate == "23503"
    finally:
        await db.engine.dispose()


async def test_snapshot_rejects_another_profile_revision(resource_rows):
    group = 0
    own = resource_rows["ids"][group]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                profile = OrgProfile(current_revision=1)
                session.add(profile)
                await session.flush()
                revision = OrgProfileRevision(
                    profile_id=profile.id, revision=1, data={"name": "Synthetic"}
                )
                session.add(revision)
                await session.flush()
                session.add(
                    TaskOrgProfile(
                        task_id=own["task"],
                        profile_id=own["profile"],
                        profile_revision_id=revision.id,
                        lot="forged",
                    )
                )
        assert error.value.orig.sqlstate == "23503"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "patch",
    [
        {"name": None},
        {"name": " "},
        {"name": 12},
        {"registration_details": " "},
        {"registration_details": 12},
        {"performance_summary": "x" * 20001},
        {"standard_wording": " "},
    ],
)
async def test_database_rejects_invalid_profile_declarations(patch, resource_rows):
    group = 0
    own = resource_rows["ids"][group]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                session.add(
                    OrgProfileRevision(
                        profile_id=own["profile"], revision=2, data={"name": "Synthetic", **patch}
                    )
                )
        assert error.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "forgery", ["template_current", "template_snapshot", "mismatched_revision"]
)
async def test_template_foreign_keys_reject_forgery(forgery, resource_rows):
    first_group, second_group = 0, 1
    own, other = (resource_rows["ids"][first_group], resource_rows["ids"][second_group])
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                if forgery == "template_current":
                    await session.execute(
                        text("UPDATE templates SET current_revision=99 WHERE id=:id"),
                        {"id": own["template"]},
                    )
                else:
                    template_id = own["template"]
                    revision_id = (
                        other["template_revision"]
                        if forgery == "template_snapshot"
                        else own["template_revision"]
                    )
                    if forgery == "mismatched_revision":
                        second = Template(id=uuid4(), current_revision=1)
                        session.add(second)
                        await session.flush()
                        identifier = uuid4()
                        revision = TemplateRevision(
                            id=identifier,
                            template_id=second.id,
                            revision=1,
                            data={"name": "Synthetic"},
                            file={
                                "name": "synthetic.docx",
                                "sha256": "a" * 64,
                                "size_bytes": 1,
                                "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            },
                            storage_key=f"template/{second.id}/{identifier}/{'a' * 64}.docx",
                        )
                        session.add(revision)
                        await session.flush()
                        revision_id = identifier
                    session.add(
                        TaskTemplate(
                            task_id=own["task"],
                            template_id=template_id,
                            template_revision_id=revision_id,
                            lot="forged",
                        )
                    )
        assert error.value.orig.sqlstate == "23503"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "patch",
    [
        {"sha256": None},
        {"sha256": "bad"},
        {"size_bytes": None},
        {"size_bytes": 0},
        {"size_bytes": 41943041},
        {"size_bytes": 1.5},
        {"media_type": None},
        {"media_type": "application/pdf"},
        {"name": None},
        {"name": ""},
    ],
)
async def test_template_file_constraints_fail_closed(patch, resource_rows):
    group = 0
    own = resource_rows["ids"][group]
    db = Database(Settings())
    file = {
        "name": "synthetic.docx",
        "sha256": "a" * 64,
        "size_bytes": 1,
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        **patch,
    }
    identifier = uuid4()
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                session.add(
                    TemplateRevision(
                        id=identifier,
                        template_id=own["template"],
                        revision=2,
                        data={"name": "Synthetic"},
                        file=file,
                        storage_key=f"template/{own['template']}/{identifier}/{'a' * 64}.docx",
                    )
                )
        assert error.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()


async def test_template_file_storage_key_cannot_point_to_another_revision(resource_rows):
    group = 0
    own = resource_rows["ids"][group]
    db = Database(Settings())
    identifier = uuid4()
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                session.add(
                    TemplateRevision(
                        id=identifier,
                        template_id=own["template"],
                        revision=2,
                        data={"name": "Synthetic"},
                        file={
                            "name": "synthetic.docx",
                            "sha256": "a" * 64,
                            "size_bytes": 1,
                            "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        },
                        storage_key=f"template/{own['template']}/{own['template_revision']}/{'a' * 64}.docx",
                    )
                )
        assert error.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("forgery", ["foreign_revision", "wrong_certificate"])
async def test_certificate_file_composite_keys_reject_forgery(forgery, resource_rows):
    first_group, second_group = 0, 1
    own = resource_rows["ids"][first_group]
    other = resource_rows["ids"][second_group]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                revision = CertificateRevision(
                    certificate_id=(
                        other["certificate"]
                        if forgery == "foreign_revision"
                        else own["certificate"]
                    ),
                    revision=2,
                    data={"kind": "qualification", "name": "Synthetic", "number": "SYNTHETIC-ONLY"},
                )
                session.add(revision)
                await session.flush()
                # A fresh revision avoids UNIQUE(revision_id) masking the binding FK.
                target_revision = revision.id
                target_certificate = (
                    other["certificate"] if forgery == "wrong_certificate" else own["certificate"]
                )
                session.add(
                    CertificateFile(
                        certificate_id=target_certificate,
                        certificate_revision_id=target_revision,
                        file={
                            "name": "synthetic.pdf",
                            "sha256": "a" * 64,
                            "size_bytes": 1,
                            "page_count": 1,
                            "media_type": "application/pdf",
                        },
                        storage_key=f"certificate/{target_certificate}/{target_revision}/{'a' * 64}.pdf",
                    )
                )
        assert error.value.orig.sqlstate == "23503"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "patch",
    [
        {"name": None},
        {"name": " "},
        {"name": "../synthetic.pdf"},
        {"name": "test.png"},
        {"sha256": None},
        {"sha256": "invalid"},
        {"size_bytes": None},
        {"size_bytes": 0},
        {"size_bytes": 41943041},
        {"size_bytes": 1.5},
        {"page_count": None},
        {"page_count": 0},
        {"page_count": 201},
        {"page_count": 1.5},
        {"media_type": None},
        {"media_type": "image/png"},
    ],
)
async def test_certificate_file_sql_descriptor_limits(patch, resource_rows):
    group = 0
    own = resource_rows["ids"][group]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError):
            async with db.transaction() as session:
                revision = CertificateRevision(
                    certificate_id=own["certificate"],
                    revision=2,
                    data={"kind": "qualification", "name": "Synthetic", "number": "SYNTHETIC-ONLY"},
                )
                session.add(revision)
                await session.flush()
                session.add(
                    CertificateFile(
                        certificate_id=own["certificate"],
                        certificate_revision_id=revision.id,
                        file={
                            "name": "synthetic.pdf",
                            "sha256": "a" * 64,
                            "size_bytes": 1,
                            "page_count": 1,
                            "media_type": "application/pdf",
                            **patch,
                        },
                        storage_key=f"certificate/{own['certificate']}/{revision.id}/{'a' * 64}.pdf",
                    )
                )
    finally:
        await db.engine.dispose()


async def test_certificate_file_sql_storage_key_binding(resource_rows):
    group = 0
    own = resource_rows["ids"][group]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                revision = CertificateRevision(
                    certificate_id=own["certificate"],
                    revision=2,
                    data={"kind": "qualification", "name": "Synthetic", "number": "SYNTHETIC-ONLY"},
                )
                session.add(revision)
                await session.flush()
                session.add(
                    CertificateFile(
                        certificate_id=own["certificate"],
                        certificate_revision_id=revision.id,
                        file={
                            "name": "synthetic.pdf",
                            "sha256": "a" * 64,
                            "size_bytes": 1,
                            "page_count": 1,
                            "media_type": "application/pdf",
                        },
                        storage_key=f"certificate/{own['certificate']}/{revision.id}/{'b' * 64}.pdf",
                    )
                )
        assert error.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "change",
    [
        "task",
        "snapshot",
        "certificate",
        "revision",
        "file",
        "page",
        "status",
        "confirmed",
        "export",
        "profile",
        "dpi",
        "dimensions",
        "pixels",
        "size",
        "hash",
        "media",
        "name",
        "key",
        "null_width",
        "null_size",
    ],
)
async def test_source_sql_binding_limits_and_permanent_unconfirmed_state(
    change, resource_rows, admin_engine
):
    group, foreign = 0, 1
    own, other = (resource_rows["ids"][group], resource_rows["ids"][foreign])
    table = Base.metadata.tables["evidence_sources"]
    with admin_engine.connect() as connection:
        values = dict(
            connection.execute(select(table).where(table.c.id == own["evidence_source"]))
            .mappings()
            .one()
        )
    fresh_snapshot = uuid4()
    foreign_snapshot = uuid4()
    values["task_certificate_id"] = fresh_snapshot
    values["id"] = uuid4()
    values["storage_key"] = f"evidence-source/{values['id']}/{'b' * 64}.png"
    key_changes = {
        "task": ("task_id", "task"),
        "snapshot": ("task_certificate_id", "certificate_snapshot"),
        "certificate": ("certificate_id", "certificate"),
        "revision": ("certificate_revision_id", "certificate_revision"),
        "file": ("certificate_file_id", "certificate_file"),
    }
    if change in key_changes:
        column, key = key_changes[change]
        # An unoccupied snapshot keeps the source identity index from masking the FK.
        values[column] = foreign_snapshot if change == "snapshot" else other[key]
    elif change == "page":
        values["page"] = 2
    elif change == "status":
        values["status"] = "confirmed"
    elif change == "confirmed":
        values["confirmed_at"] = datetime.now(UTC)
    elif change == "export":
        values["eligible_for_draft_export"] = True
    elif change == "profile":
        values["render_profile"] = "forged"
    elif change == "dpi":
        values["dpi"] = 72
    elif change == "key":
        values["storage_key"] = "foreign/preview.png"
    else:
        patches = {
            "dimensions": {"width_px": 8193},
            "pixels": {"width_px": 8192, "height_px": 8192},
            "size": {"size_bytes": 41943041},
            "hash": {"sha256": "forged"},
            "media": {"media_type": "application/pdf"},
            "name": {"name": "../forged.png"},
            "null_width": {"width_px": None},
            "null_size": {"size_bytes": None},
        }
        values["preview"] = {**values["preview"], **patches[change]}
    db = Database(Settings())
    try:
        async with db.transaction() as session:
            await actor_context_async(session)
            session.add(
                TaskCertificate(
                    id=fresh_snapshot,
                    task_id=own["task"],
                    certificate_id=own["certificate"],
                    certificate_revision_id=own["certificate_revision"],
                    lot="Synthetic SQL check",
                )
            )
            if change == "snapshot":
                session.add(
                    TaskCertificate(
                        id=foreign_snapshot,
                        task_id=other["task"],
                        certificate_id=other["certificate"],
                        certificate_revision_id=other["certificate_revision"],
                        lot="Synthetic SQL foreign snapshot",
                    )
                )
            await session.flush()
            with pytest.raises(DBAPIError) as error:
                async with session.begin_nested():
                    await session.execute(table.insert().values(**values))
            assert error.value.orig.sqlstate == ("23503" if change in key_changes else "23514")
            if change == "snapshot":
                assert (
                    error.value.orig.diag.constraint_name
                    == "evidence_sources_task_certificate_id_task_id_certif_fkey"
                )
            assert (
                await session.execute(select(table.c.id).where(table.c.task_id == own["task"]))
            ).scalars().all() == [own["evidence_source"]]
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "table",
    [
        "products",
        "product_revisions",
        "task_resources",
        "audit_logs",
        "features",
        "feature_revisions",
        "task_features",
        "certificates",
        "certificate_revisions",
        "task_certificates",
        "org_profiles",
        "org_profile_revisions",
        "task_org_profiles",
        "certificate_files",
        "templates",
        "template_revisions",
        "task_templates",
        "evidence_sources",
    ],
)
async def test_runtime_cannot_delete_resource_history(table, resource_rows):
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                await session.execute(text(f'DELETE FROM "{table}"'))
        assert error.value.orig.sqlstate == "42501"
    finally:
        await db.engine.dispose()
