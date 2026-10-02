from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings


class Database:
    def __init__(self, settings: Settings):
        self.engine = create_async_engine(
            settings.database_url.get_secret_value(), pool_pre_ping=True, hide_parameters=True
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def verify_role(self) -> None:
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
                )
            ).one()
            if row.rolsuper or row.rolbypassrls:
                raise RuntimeError("Runtime database role must not be superuser or BYPASSRLS")
            owned = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version' AND tableowner = current_user"
                )
            )
            if owned:
                raise RuntimeError("Runtime role must not own application tables")

    @asynccontextmanager
    async def transaction(self, org_id: UUID | None = None):
        async with self.sessions() as session, session.begin():
            if org_id is not None:
                await set_org(session, org_id)
            yield session


async def set_org(session: AsyncSession, org_id: UUID) -> None:
    # Transaction-local configuration cannot leak through pooled connections.
    await session.execute(
        text("SELECT set_config('app.current_org', :org, true)"), {"org": str(org_id)}
    )
