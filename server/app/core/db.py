from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import deployment
from app.core.config import Settings


class Database:
    def __init__(self, settings: Settings):
        self.engine = create_async_engine(
            settings.database_url.get_secret_value(), pool_pre_ping=True, hide_parameters=True
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.deployment = settings.deployment

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
            stored = await connection.scalar(text(deployment.MODE_QUERY))
            deployment.require_mode(deployment.stored_mode(stored), self.deployment)

    @asynccontextmanager
    async def transaction(self):
        async with self.sessions() as session, session.begin():
            yield session
