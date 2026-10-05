"""Independent, least-privilege connections; cache connections, never credential state."""

from contextlib import asynccontextmanager

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings
from app.core.errors import ServiceError


class CredentialPool:
    def __init__(self, url: SecretStr | None, role: str):
        self.role = role
        self.engine: AsyncEngine | None = None
        if url:
            self.engine = create_async_engine(
                url.get_secret_value(),
                pool_pre_ping=True,
                hide_parameters=True,
                isolation_level="READ COMMITTED",
            )

    @asynccontextmanager
    async def transaction(self):
        if self.engine is None:
            raise ServiceError(
                "credential_backend_unavailable", "Credential database is unavailable", 503, 3
            )
        async with self.engine.begin() as connection:
            # Validate on checkout, so a URL cannot silently substitute an owner or org pool.
            row = (
                await connection.execute(
                    text(
                        "SELECT rolname, rolsuper, rolbypassrls, rolinherit, rolcreaterole, rolcreatedb, rolreplication, session_user=current_user AS direct_login "
                        "FROM pg_catalog.pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
            if (
                row.rolname != self.role
                or any(
                    (
                        row.rolsuper,
                        row.rolbypassrls,
                        row.rolinherit,
                        row.rolcreaterole,
                        row.rolcreatedb,
                        row.rolreplication,
                    )
                )
                or not row.direct_login
            ):
                raise ServiceError(
                    "credential_backend_unavailable", "Credential database role is invalid", 503, 4
                )
            if await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m "
                    "JOIN pg_catalog.pg_roles r ON r.oid=m.member WHERE r.rolname=current_user)"
                )
            ):
                raise ServiceError(
                    "credential_backend_unavailable", "Credential database role is invalid", 503, 4
                )
            if await connection.scalar(
                text(
                    "SELECT has_schema_privilege(current_user,'public','CREATE') OR EXISTS ("
                    "SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relkind IN ('r','p') AND ("
                    "has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                    "OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))"
                )
            ):
                raise ServiceError(
                    "credential_backend_unavailable", "Credential database role is invalid", 503, 4
                )
            yield connection

    async def verify(self):
        async with self.transaction():
            pass

    async def close(self):
        if self.engine is not None:
            await self.engine.dispose()


class CredentialConnections:
    def __init__(self, settings: Settings):
        self.management = CredentialPool(settings.platform_database_url, "bid_platform_app")
        self.reader = CredentialPool(settings.credential_database_url, "bid_credential_reader")

    async def close(self):
        await self.management.close()
        await self.reader.close()


def get_connections(settings: Settings) -> CredentialConnections:
    existing = settings._credential_connections
    if not isinstance(existing, CredentialConnections):
        existing = CredentialConnections(settings)
        settings._credential_connections = existing
    return existing


async def close_connections(settings: Settings) -> None:
    existing = settings._credential_connections
    if isinstance(existing, CredentialConnections):
        await existing.close()
        settings._credential_connections = None
