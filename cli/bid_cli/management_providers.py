"""Metadata-only provider reads over the authenticated Result 4.0 API."""

from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.contracts import Cost, Result
from app.schemas.management_pages import Page, PageQuery
from app.schemas.management_providers import (
    PlatformModelChoice,
    ProviderCatalogQuery,
    ProviderRevisionMetadata,
    ProviderSettingsData,
)

COMMAND_INPUTS = {
    "provider show": None,
    "provider revision show": None,
    "provider history-page": PageQuery,
    "provider catalog": ProviderCatalogQuery,
}
COMMAND_DATA = {
    "provider show": ProviderSettingsData,
    "provider revision show": ProviderRevisionMetadata,
}
COMMAND_ITEMS = {
    "provider history-page": ProviderRevisionMetadata,
    "provider catalog": PlatformModelChoice,
}

ERROR_CODES = {
    "invalid_input",
    "invalid_session",
    "forbidden",
    "not_found",
    "revision_conflict",
    "rate_limit",
    "unavailable",
    "network_unavailable",
    "login_required",
    "cli_key_required",
    "insecure_server",
    "invalid_server_response",
    "page_too_large",
    "provider_unavailable",
    "server_error",
    "internal_error",
    "management_cursor_invalid",
    "management_cursor_expired",
    "management_result_too_large",
    "management_query_timeout",
    "management_integrity_error",
}


def safe_failure(exc: ServiceError) -> ServiceError:
    """Remote errors are untrusted text, including errors raised by Client.request."""
    if exc.code not in ERROR_CODES or exc.exit_code not in {2, 3, 4}:
        return ServiceError(
            "invalid_server_response", "Server returned invalid provider metadata", 502, 4
        )
    return ServiceError(
        exc.code, "Provider metadata operation failed: " + exc.code, exc.status, exc.exit_code
    )


def validated(body: dict, command: str, org_id: UUID, config_id: UUID | None = None) -> dict:
    """Rebuild from allowlisted models; never emit arbitrary warnings or error text."""
    try:
        if set(body) != {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}:
            raise ValueError("invalid envelope")
        result = Result.model_validate(body)
        if (
            not result.ok
            or result.command != command
            or result.warnings
            or result.cost != Cost(billing_currency=result.cost.billing_currency)
        ):
            raise ValueError("invalid metadata result")
        if command in COMMAND_DATA:
            detail = COMMAND_DATA[command].model_validate(result.data)
            if detail.org_id != org_id or result.items:
                raise ValueError("wrong org or detail items")
            if (
                isinstance(detail, ProviderSettingsData)
                and detail.billing_currency != result.cost.billing_currency
            ):
                raise ValueError("inconsistent billing currency")
            if isinstance(detail, ProviderRevisionMetadata) and detail.id != config_id:
                raise ValueError("wrong exact revision")
            result.data = detail.model_dump(mode="json")
        else:
            page = Page[COMMAND_ITEMS[command]].model_validate(
                {"data": result.data, "items": result.items}
            )
            if page.data.org_id != org_id:
                raise ValueError("wrong page org")
            result.data = page.data.model_dump(mode="json")
            result.items = [row.model_dump(mode="json") for row in page.items]
        return result.model_dump(mode="json")
    except (KeyError, TypeError, ValueError):
        raise ServiceError(
            "invalid_server_response", "Server returned invalid provider metadata", 502, 4
        ) from None


def register(provider_app: typer.Typer):
    from bid_cli import main as cli

    prefix = "/management/providers"
    revision_app = typer.Typer()
    provider_app.add_typer(revision_app, name="revision")

    def send(method, path, command, json_output, config_id=None, **kwargs):
        try:
            org_id = UUID(cli.client().state.load()["org_id"])
            result = validated(cli.call(method, path, **kwargs), command, org_id, config_id)
        except ServiceError as exc:
            raise safe_failure(exc) from None
        cli.emit(result, command, json_output)

    @provider_app.command("show")
    def show(json_output: cli.JsonOption = False):
        send("GET", prefix, "provider show", json_output)

    @revision_app.command("show")
    def revision_show(
        id: Annotated[UUID, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        send("GET", f"{prefix}/revisions/{id}", "provider revision show", json_output, id)

    @provider_app.command("history-page")
    def history_page(
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 25,
        json_output: cli.JsonOption = False,
    ):
        query = PageQuery(cursor=cursor, limit=limit)
        send(
            "POST",
            prefix + "/history/query",
            "provider history-page",
            json_output,
            json=query.model_dump(mode="json", exclude_none=True),
        )

    @provider_app.command("catalog")
    def catalog(
        q: Annotated[str | None, typer.Option(help="Catalog ID prefix")] = None,
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 25,
        json_output: cli.JsonOption = False,
    ):
        query = ProviderCatalogQuery(q=q, cursor=cursor, limit=limit)
        send(
            "POST",
            prefix + "/catalog/query",
            "provider catalog",
            json_output,
            json=query.model_dump(mode="json", exclude_none=True),
        )
