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
from task_fixtures import finish_scope, seed_task

TABLES = (
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
)


@pytest.fixture
def resource_rows(tenants, admin_engine):
    ids = {}
    with Session(admin_engine) as session, session.begin():
        for org, user in zip(tenants["orgs"], tenants["users"], strict=True):
            task = Task(id=uuid4(), org_id=org, created_by=user, name="Synthetic RLS task")
            seed_task(session, task)
            product = Product(id=uuid4(), org_id=org, created_by=user, current_revision=1)
            session.add_all([task, product])
            session.flush()
            revision = ProductRevision(
                id=uuid4(),
                org_id=org,
                product_id=product.id,
                revision=1,
                data={"name": "Synthetic"},
            )
            session.add(revision)
            session.flush()
            feature = Feature(id=uuid4(), org_id=org, created_by=user, current_revision=1)
            session.add(feature)
            session.flush()
            feature_revision = FeatureRevision(
                id=uuid4(),
                org_id=org,
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
                org_id=org,
                task_id=task.id,
                feature_id=feature.id,
                feature_revision_id=feature_revision.id,
            )
            session.add(feature_snapshot)
            snapshot = TaskResource(
                id=uuid4(),
                org_id=org,
                task_id=task.id,
                product_id=product.id,
                product_revision_id=revision.id,
            )
            event = AuditLog(
                id=uuid4(),
                org_id=org,
                actor_user_id=user,
                action="synthetic",
                object_id=product.id,
                details={},
            )
            session.add_all([snapshot, event])
            certificate = Certificate(id=uuid4(), org_id=org, created_by=user, current_revision=1)
            session.add(certificate)
            session.flush()
            certificate_revision = CertificateRevision(
                id=uuid4(),
                org_id=org,
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
                org_id=org,
                certificate_id=certificate.id,
                certificate_revision_id=certificate_revision.id,
                created_by=user,
                file={
                    "name": "synthetic.pdf",
                    "sha256": "a" * 64,
                    "size_bytes": 1,
                    "page_count": 1,
                    "media_type": "application/pdf",
                },
                storage_key=f"org/{org}/certificate/{certificate.id}/{certificate_revision.id}/{'a' * 64}.pdf",
            )
            session.add(certificate_file)
            certificate_snapshot = TaskCertificate(
                id=uuid4(),
                org_id=org,
                task_id=task.id,
                certificate_id=certificate.id,
                certificate_revision_id=certificate_revision.id,
            )
            session.add(certificate_snapshot)
            profile = OrgProfile(id=uuid4(), org_id=org, created_by=user, current_revision=1)
            session.add(profile)
            session.flush()
            profile_revision = OrgProfileRevision(
                id=uuid4(),
                org_id=org,
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
                org_id=org,
                task_id=task.id,
                profile_id=profile.id,
                profile_revision_id=profile_revision.id,
            )
            session.add(profile_snapshot)
            template = Template(id=uuid4(), org_id=org, created_by=user, current_revision=1)
            session.add(template)
            session.flush()
            template_revision_id = uuid4()
            template_revision = TemplateRevision(
                id=template_revision_id,
                org_id=org,
                template_id=template.id,
                revision=1,
                data={"name": "Synthetic template"},
                file={
                    "name": "synthetic.docx",
                    "sha256": "a" * 64,
                    "size_bytes": 1,
                    "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                },
                storage_key=f"org/{org}/template/{template.id}/{template_revision_id}/{'a' * 64}.docx",
            )
            session.add(template_revision)
            session.flush()
            template_snapshot = TaskTemplate(
                id=uuid4(),
                org_id=org,
                task_id=task.id,
                template_id=template.id,
                template_revision_id=template_revision.id,
            )
            session.add(template_snapshot)
            session.flush()
            source_id = uuid4()
            source = EvidenceSource(
                id=source_id,
                org_id=org,
                task_id=task.id,
                task_certificate_id=certificate_snapshot.id,
                certificate_id=certificate.id,
                certificate_revision_id=certificate_revision.id,
                certificate_file_id=certificate_file.id,
                created_by=user,
                page=1,
                preview={
                    "name": "synthetic.png",
                    "sha256": "b" * 64,
                    "size_bytes": 1,
                    "media_type": "image/png",
                    "width_px": 1,
                    "height_px": 1,
                },
                storage_key=f"org/{org}/evidence-source/{source_id}/{'b' * 64}.png",
                rendered_at=datetime.now(UTC),
            )
            session.add(source)
            ids[org] = {
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
    return {**tenants, "ids": ids}


@pytest.mark.parametrize("table", TABLES)
async def test_new_tables_fail_closed_and_reject_foreign_inserts(
    table, resource_rows, admin_engine
):
    org_a, org_b = resource_rows["orgs"]
    db = Database(Settings())
    try:
        async with db.transaction(org_a) as session:
            assert set(
                (await session.execute(text(f'SELECT org_id FROM "{table}"'))).scalars()
            ) == {org_a}
        async with db.transaction() as session:
            assert (await session.execute(text(f'SELECT id FROM "{table}"'))).all() == []
        model = Base.metadata.tables[table]
        with admin_engine.connect() as connection:
            row = dict(
                connection.execute(select(model).where(model.c.org_id == org_b)).mappings().one()
            )
            flags = connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname=:table"
                ),
                {"table": table},
            ).one()
            assert flags.relrowsecurity and flags.relforcerowsecurity
            assert (
                connection.scalar(
                    text(
                        "SELECT is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name=:table AND column_name='org_id'"
                    ),
                    {"table": table},
                )
                == "NO"
            )
        row["id"] = uuid4()
        # Generated columns cannot be written; leave them out so RLS answers the insert.
        row = {key: value for key, value in row.items() if model.c[key].computed is None}
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org_a) as session:
                await session.execute(model.insert().values(**row))
        assert error.value.orig.sqlstate == "42501"
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                await session.execute(model.insert().values(**row))
        assert error.value.orig.sqlstate == "42501"
        # Even owned history must never be destructively deleted by the runtime.
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org_a) as session:
                await session.execute(
                    text(f'DELETE FROM "{table}" WHERE org_id=:org'), {"org": org_a}
                )
        assert error.value.orig.sqlstate == "42501"
        if table in (
            "products",
            "task_resources",
            "features",
            "task_features",
            "certificates",
            "task_certificates",
            "org_profiles",
            "task_org_profiles",
            "templates",
            "task_templates",
        ):
            assignment = (
                "current_revision=current_revision"
                if table in ("products", "features", "certificates", "org_profiles", "templates")
                else "active=active"
            )
            async with db.transaction(org_a) as session:
                result = await session.execute(
                    text(f'UPDATE "{table}" SET {assignment} WHERE org_id=:org'), {"org": org_b}
                )
                assert result.rowcount == 0
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org_a) as session:
                await session.execute(text(f'UPDATE "{table}" SET org_id=:org'), {"org": org_b})
        assert error.value.orig.sqlstate == "42501"
    finally:
        await db.engine.dispose()


async def test_snapshot_rejects_another_product_revision_in_same_org(resource_rows):
    org = resource_rows["orgs"][0]
    own = resource_rows["ids"][org]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org) as session:
                product = Product(
                    org_id=org, created_by=resource_rows["users"][0], current_revision=1
                )
                session.add(product)
                await session.flush()
                revision = ProductRevision(org_id=org, product_id=product.id, revision=1, data={})
                session.add(revision)
                await session.flush()
                session.add(
                    TaskResource(
                        org_id=org,
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
        ("templates", "created_by=created_by"),
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
            async with db.transaction(resource_rows["orgs"][0]) as session:
                await session.execute(text(f'UPDATE "{table}" SET {assignment}'))
        assert error.value.orig.sqlstate == "42501"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "forgery", ["feature_product", "feature_task", "feature_revision", "feature_current"]
)
async def test_feature_keys_reject_forgery(forgery, resource_rows):
    org_a, org_b = resource_rows["orgs"]
    own, other = resource_rows["ids"][org_a], resource_rows["ids"][org_b]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org_a) as session:
                if forgery == "feature_product":
                    session.add(
                        FeatureRevision(
                            org_id=org_a,
                            feature_id=own["feature"],
                            product_id=other["product"],
                            revision=2,
                            data={"product_id": str(other["product"]), "status": "planned"},
                        )
                    )
                elif forgery in ("feature_task", "feature_revision"):
                    session.add(
                        TaskFeature(
                            org_id=org_a,
                            task_id=other["task"] if forgery == "feature_task" else own["task"],
                            feature_id=own["feature"],
                            feature_revision_id=other["feature_revision"]
                            if forgery == "feature_revision"
                            else own["feature_revision"],
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
    org = resource_rows["orgs"][0]
    own = resource_rows["ids"][org]
    values = {"product_id": str(own["product"]), "status": "planned", **data}
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org) as session:
                session.add(
                    FeatureRevision(
                        org_id=org,
                        feature_id=own["feature"],
                        product_id=own["product"],
                        revision=2,
                        data=values,
                    )
                )
        assert error.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()


async def test_snapshot_rejects_another_feature_revision_in_same_org(resource_rows):
    org = resource_rows["orgs"][0]
    own = resource_rows["ids"][org]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org) as session:
                feature = Feature(
                    org_id=org, created_by=resource_rows["users"][0], current_revision=1
                )
                session.add(feature)
                await session.flush()
                revision = FeatureRevision(
                    org_id=org,
                    feature_id=feature.id,
                    product_id=own["product"],
                    revision=1,
                    data={"product_id": str(own["product"]), "status": "planned"},
                )
                session.add(revision)
                await session.flush()
                session.add(
                    TaskFeature(
                        org_id=org,
                        task_id=own["task"],
                        feature_id=own["feature"],
                        feature_revision_id=revision.id,
                        lot="forged",
                    )
                )
        assert error.value.orig.sqlstate == "23503"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("forgery", ["product", "task", "revision", "actor", "current_revision"])
async def test_composite_keys_and_current_pointer_reject_forgery(forgery, resource_rows):
    org_a, org_b = resource_rows["orgs"]
    own, other = resource_rows["ids"][org_a], resource_rows["ids"][org_b]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org_a) as session:
                if forgery == "product":
                    session.add(
                        ProductRevision(
                            org_id=org_a, product_id=other["product"], revision=2, data={}
                        )
                    )
                elif forgery in ("task", "revision"):
                    session.add(
                        TaskResource(
                            org_id=org_a,
                            task_id=other["task"] if forgery == "task" else own["task"],
                            product_id=own["product"],
                            product_revision_id=other["revision"]
                            if forgery == "revision"
                            else own["revision"],
                            lot="forged",
                        )
                    )
                elif forgery == "actor":
                    session.add(
                        AuditLog(
                            org_id=org_a,
                            actor_user_id=resource_rows["users"][1],
                            action="forged",
                            object_id=own["product"],
                            details={},
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


@pytest.mark.parametrize("forgery", ["certificate", "task", "revision", "current_revision"])
async def test_certificate_composite_keys_and_current_pointer_reject_forgery(
    forgery, resource_rows
):
    org_a, org_b = resource_rows["orgs"]
    own, other = resource_rows["ids"][org_a], resource_rows["ids"][org_b]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org_a) as session:
                if forgery == "certificate":
                    session.add(
                        CertificateRevision(
                            org_id=org_a,
                            certificate_id=other["certificate"],
                            revision=2,
                            data={
                                "kind": "personnel",
                                "name": "Synthetic",
                                "number": "SYNTHETIC-ONLY",
                            },
                        )
                    )
                elif forgery in ("task", "revision"):
                    session.add(
                        TaskCertificate(
                            org_id=org_a,
                            task_id=other["task"] if forgery == "task" else own["task"],
                            certificate_id=own["certificate"],
                            certificate_revision_id=other["certificate_revision"]
                            if forgery == "revision"
                            else own["certificate_revision"],
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


async def test_snapshot_rejects_another_certificate_revision_in_same_org(resource_rows):
    org = resource_rows["orgs"][0]
    own = resource_rows["ids"][org]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org) as session:
                certificate = Certificate(
                    org_id=org, created_by=resource_rows["users"][0], current_revision=1
                )
                session.add(certificate)
                await session.flush()
                revision = CertificateRevision(
                    org_id=org,
                    certificate_id=certificate.id,
                    revision=1,
                    data={"kind": "personnel", "name": "Synthetic", "number": "SYNTHETIC-ONLY"},
                )
                session.add(revision)
                await session.flush()
                session.add(
                    TaskCertificate(
                        org_id=org,
                        task_id=own["task"],
                        certificate_id=own["certificate"],
                        certificate_revision_id=revision.id,
                        lot="forged",
                    )
                )
        assert error.value.orig.sqlstate == "23503"
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
    org = resource_rows["orgs"][0]
    own = resource_rows["ids"][org]
    values = {"kind": "qualification", "name": "Synthetic", "number": "SYNTHETIC-ONLY", **patch}
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org) as session:
                session.add(
                    CertificateRevision(
                        org_id=org, certificate_id=own["certificate"], revision=2, data=values
                    )
                )
        assert error.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("forgery", ["profile", "task", "revision", "current_revision"])
async def test_profile_composite_keys_and_current_pointer_reject_forgery(forgery, resource_rows):
    org_a, org_b = resource_rows["orgs"]
    own, other = resource_rows["ids"][org_a], resource_rows["ids"][org_b]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org_a) as session:
                if forgery == "profile":
                    session.add(
                        OrgProfileRevision(
                            org_id=org_a,
                            profile_id=other["profile"],
                            revision=2,
                            data={"name": "Synthetic"},
                        )
                    )
                elif forgery in ("task", "revision"):
                    session.add(
                        TaskOrgProfile(
                            org_id=org_a,
                            task_id=other["task"] if forgery == "task" else own["task"],
                            profile_id=own["profile"],
                            profile_revision_id=other["profile_revision"]
                            if forgery == "revision"
                            else own["profile_revision"],
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


async def test_snapshot_rejects_another_profile_revision_in_same_org(resource_rows):
    org = resource_rows["orgs"][0]
    own = resource_rows["ids"][org]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org) as session:
                profile = OrgProfile(
                    org_id=org, created_by=resource_rows["users"][0], current_revision=1
                )
                session.add(profile)
                await session.flush()
                revision = OrgProfileRevision(
                    org_id=org,
                    profile_id=profile.id,
                    revision=1,
                    data={"name": "Synthetic"},
                )
                session.add(revision)
                await session.flush()
                session.add(
                    TaskOrgProfile(
                        org_id=org,
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
    org = resource_rows["orgs"][0]
    own = resource_rows["ids"][org]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org) as session:
                session.add(
                    OrgProfileRevision(
                        org_id=org,
                        profile_id=own["profile"],
                        revision=2,
                        data={"name": "Synthetic", **patch},
                    )
                )
        assert error.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "forgery",
    [
        "template_owner",
        "template_revision",
        "template_current",
        "template_task",
        "template_snapshot",
        "same_org_revision",
    ],
)
async def test_template_foreign_keys_reject_forgery(forgery, resource_rows):
    own_org, other_org = resource_rows["orgs"]
    own, other = resource_rows["ids"][own_org], resource_rows["ids"][other_org]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(own_org) as session:
                if forgery == "template_owner":
                    session.add(
                        Template(
                            org_id=own_org, created_by=resource_rows["users"][1], current_revision=1
                        )
                    )
                elif forgery == "template_current":
                    await session.execute(
                        text("UPDATE templates SET current_revision=99 WHERE id=:id"),
                        {"id": own["template"]},
                    )
                elif forgery == "template_revision":
                    identifier = uuid4()
                    session.add(
                        TemplateRevision(
                            id=identifier,
                            org_id=own_org,
                            template_id=other["template"],
                            revision=2,
                            data={"name": "Synthetic"},
                            file={
                                "name": "synthetic.docx",
                                "sha256": "a" * 64,
                                "size_bytes": 1,
                                "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            },
                            storage_key=f"org/{own_org}/template/{other['template']}/{identifier}/{'a' * 64}.docx",
                        )
                    )
                else:
                    template_id = own["template"]
                    revision_id = (
                        other["template_revision"]
                        if forgery == "template_snapshot"
                        else own["template_revision"]
                    )
                    if forgery == "same_org_revision":
                        second = Template(
                            id=uuid4(),
                            org_id=own_org,
                            created_by=resource_rows["users"][0],
                            current_revision=1,
                        )
                        session.add(second)
                        await session.flush()
                        identifier = uuid4()
                        revision = TemplateRevision(
                            id=identifier,
                            org_id=own_org,
                            template_id=second.id,
                            revision=1,
                            data={"name": "Synthetic"},
                            file={
                                "name": "synthetic.docx",
                                "sha256": "a" * 64,
                                "size_bytes": 1,
                                "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            },
                            storage_key=f"org/{own_org}/template/{second.id}/{identifier}/{'a' * 64}.docx",
                        )
                        session.add(revision)
                        await session.flush()
                        revision_id = identifier
                    session.add(
                        TaskTemplate(
                            org_id=own_org,
                            task_id=other["task"] if forgery == "template_task" else own["task"],
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
    org = resource_rows["orgs"][0]
    own = resource_rows["ids"][org]
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
            async with db.transaction(org) as session:
                session.add(
                    TemplateRevision(
                        id=identifier,
                        org_id=org,
                        template_id=own["template"],
                        revision=2,
                        data={"name": "Synthetic"},
                        file=file,
                        storage_key=f"org/{org}/template/{own['template']}/{identifier}/{'a' * 64}.docx",
                    )
                )
        assert error.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()


async def test_template_file_storage_key_cannot_point_to_another_revision(resource_rows):
    org = resource_rows["orgs"][0]
    own = resource_rows["ids"][org]
    db = Database(Settings())
    identifier = uuid4()
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org) as session:
                session.add(
                    TemplateRevision(
                        id=identifier,
                        org_id=org,
                        template_id=own["template"],
                        revision=2,
                        data={"name": "Synthetic"},
                        file={
                            "name": "synthetic.docx",
                            "sha256": "a" * 64,
                            "size_bytes": 1,
                            "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        },
                        storage_key=f"org/{org}/template/{own['template']}/{own['template_revision']}/{'a' * 64}.docx",
                    )
                )
        assert error.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("forgery", ["foreign_revision", "wrong_certificate", "foreign_creator"])
async def test_certificate_file_composite_keys_reject_forgery(forgery, resource_rows):
    org_a, org_b = resource_rows["orgs"]
    own = resource_rows["ids"][org_a]
    other = resource_rows["ids"][org_b]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org_a) as session:
                revision = CertificateRevision(
                    org_id=org_a,
                    certificate_id=own["certificate"],
                    revision=2,
                    data={"kind": "qualification", "name": "Synthetic", "number": "SYNTHETIC-ONLY"},
                )
                session.add(revision)
                await session.flush()
                target_revision = (
                    other["certificate_revision"] if forgery == "foreign_revision" else revision.id
                )
                target_certificate = (
                    other["certificate"] if forgery == "wrong_certificate" else own["certificate"]
                )
                session.add(
                    CertificateFile(
                        org_id=org_a,
                        certificate_id=target_certificate,
                        certificate_revision_id=target_revision,
                        created_by=resource_rows["users"][1]
                        if forgery == "foreign_creator"
                        else resource_rows["users"][0],
                        file={
                            "name": "synthetic.pdf",
                            "sha256": "a" * 64,
                            "size_bytes": 1,
                            "page_count": 1,
                            "media_type": "application/pdf",
                        },
                        storage_key=f"org/{org_a}/certificate/{target_certificate}/{target_revision}/{'a' * 64}.pdf",
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
    org = resource_rows["orgs"][0]
    own = resource_rows["ids"][org]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError):
            async with db.transaction(org) as session:
                revision = CertificateRevision(
                    org_id=org,
                    certificate_id=own["certificate"],
                    revision=2,
                    data={"kind": "qualification", "name": "Synthetic", "number": "SYNTHETIC-ONLY"},
                )
                session.add(revision)
                await session.flush()
                session.add(
                    CertificateFile(
                        org_id=org,
                        certificate_id=own["certificate"],
                        certificate_revision_id=revision.id,
                        created_by=resource_rows["users"][0],
                        file={
                            "name": "synthetic.pdf",
                            "sha256": "a" * 64,
                            "size_bytes": 1,
                            "page_count": 1,
                            "media_type": "application/pdf",
                            **patch,
                        },
                        storage_key=f"org/{org}/certificate/{own['certificate']}/{revision.id}/{'a' * 64}.pdf",
                    )
                )
    finally:
        await db.engine.dispose()


async def test_certificate_file_sql_storage_key_binding(resource_rows):
    org = resource_rows["orgs"][0]
    own = resource_rows["ids"][org]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org) as session:
                revision = CertificateRevision(
                    org_id=org,
                    certificate_id=own["certificate"],
                    revision=2,
                    data={"kind": "qualification", "name": "Synthetic", "number": "SYNTHETIC-ONLY"},
                )
                session.add(revision)
                await session.flush()
                session.add(
                    CertificateFile(
                        org_id=org,
                        certificate_id=own["certificate"],
                        certificate_revision_id=revision.id,
                        created_by=resource_rows["users"][0],
                        file={
                            "name": "synthetic.pdf",
                            "sha256": "a" * 64,
                            "size_bytes": 1,
                            "page_count": 1,
                            "media_type": "application/pdf",
                        },
                        storage_key=f"org/{org}/certificate/{own['certificate']}/{revision.id}/{'b' * 64}.pdf",
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
        "actor",
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
    org, foreign = resource_rows["orgs"]
    own, other = resource_rows["ids"][org], resource_rows["ids"][foreign]
    table = Base.metadata.tables["evidence_sources"]
    with admin_engine.connect() as connection:
        values = dict(
            connection.execute(select(table).where(table.c.org_id == org)).mappings().one()
        )
    fresh_snapshot = uuid4()
    values["task_certificate_id"] = fresh_snapshot
    values["id"] = uuid4()
    values["storage_key"] = f"org/{org}/evidence-source/{values['id']}/{'b' * 64}.png"
    key_changes = {
        "task": ("task_id", "task"),
        "snapshot": ("task_certificate_id", "certificate_snapshot"),
        "certificate": ("certificate_id", "certificate"),
        "revision": ("certificate_revision_id", "certificate_revision"),
        "file": ("certificate_file_id", "certificate_file"),
    }
    if change in key_changes:
        column, key = key_changes[change]
        values[column] = other[key]
    elif change == "actor":
        values["created_by"] = resource_rows["users"][1]
    elif change == "page":
        values["page"] = 2
    elif change == "status":
        values["status"] = "confirmed"
    elif change == "confirmed":
        values["confirmed_by"] = resource_rows["users"][0]
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
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org) as session:
                session.add(
                    TaskCertificate(
                        id=fresh_snapshot,
                        org_id=org,
                        task_id=own["task"],
                        certificate_id=own["certificate"],
                        certificate_revision_id=own["certificate_revision"],
                        lot="Synthetic SQL check",
                    )
                )
                await session.flush()
                await session.execute(table.insert().values(**values))
        assert error.value.orig.sqlstate == (
            "23503" if change in key_changes or change == "actor" else "23514"
        )
        async with db.transaction(org) as session:
            assert (await session.execute(select(table.c.id))).scalars().all() == [
                own["evidence_source"]
            ]
    finally:
        await db.engine.dispose()
