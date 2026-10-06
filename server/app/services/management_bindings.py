"""Bounded immutable binding reads using the existing human export authority."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.models.entities import TemplateRevision
from app.models.exports import ExportTemplateBinding
from app.schemas.export_contracts import ExportBindingView
from app.schemas.management_pages import BindingDetailQuery, BindingQuery, Page
from app.services import exports, templates
from app.services import management_templates as management
from app.services.auth import Identity

COMMANDS = {"browse": "export binding browse", "detail": "export binding show"}
page_result = management.page_result
detail_result = management.detail_result


def view(row: ExportTemplateBinding, actor: Identity, revision_id: UUID) -> ExportBindingView:
    if row.org_id != actor.org_id or row.template_revision_id != revision_id:
        management.fail("management_integrity_error", "Binding identity is invalid", 409, 4)
    return ExportBindingView.model_validate(exports.binding_view(row))


async def query(
    session: AsyncSession, actor: Identity, body: BindingQuery
) -> Page[ExportBindingView]:
    async with management.read_budget(session, body):
        actor = await exports.human_access(session, actor, binding="read")
        await templates.require_revision(session, actor, body.template_revision_id)
        anchor = management.open_cursor(
            session, actor, body, "binding_browse", body.template_revision_id, {}
        )
        # The authorized revision remains visible as an empty sentinel after the
        # last binding. A foreign/nonexistent parent instead has no result at all.
        relation = (ExportTemplateBinding.org_id == TemplateRevision.org_id) & (
            ExportTemplateBinding.template_revision_id == TemplateRevision.id
        )
        if anchor:
            relation &= tuple_(
                ExportTemplateBinding.reviewed_at, ExportTemplateBinding.id
            ) < tuple_(*anchor)
        records = (
            await session.execute(
                select(TemplateRevision.id, ExportTemplateBinding)
                .outerjoin(ExportTemplateBinding, relation)
                .where(
                    TemplateRevision.org_id == actor.org_id,
                    TemplateRevision.id == body.template_revision_id,
                )
                .order_by(ExportTemplateBinding.reviewed_at.desc(), ExportTemplateBinding.id.desc())
                .limit(body.limit + 1)
            )
        ).all()
        if not records:
            raise not_found()
        rows = [row[1] for row in records if row[1] is not None]
        items = [view(row, actor, body.template_revision_id) for row in rows[: body.limit]]
        anchors = [[row.reviewed_at.isoformat(), str(row.id)] for row in rows[: body.limit]]
        return management.build_page(
            session,
            actor,
            "binding_browse",
            body.template_revision_id,
            {},
            items,
            anchors,
            len(rows) > body.limit,
            datetime.now(UTC),
        )


async def detail(
    session: AsyncSession, actor: Identity, binding_id: UUID, body: BindingDetailQuery
) -> ExportBindingView:
    async with management.read_budget(session, body):
        actor = await exports.human_access(session, actor, binding="read")
        row = await session.scalar(
            select(ExportTemplateBinding).where(
                ExportTemplateBinding.org_id == actor.org_id,
                ExportTemplateBinding.id == binding_id,
                ExportTemplateBinding.template_revision_id == body.template_revision_id,
            )
        )
        if row is None:
            raise not_found()
        value = view(row, actor, body.template_revision_id)
        detail_result(
            COMMANDS["detail"],
            value,
            management.settings_for(session).billing_currency,
            9223372036854775807,
        )
        return value
