"""Explicit provisioning for a newly selected development instance.

This module is never imported by request handling or the worker. Migration
credentials are separate from application credentials; secrets stay in env.
"""

import argparse
import asyncio
import os
from uuid import uuid4

import procrastinate
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.entities import Membership, Org, User


def initialize_database():
    url = os.environ["BID_MIGRATION_DATABASE_URL"]
    engine = create_engine(url, hide_parameters=True)
    with engine.begin() as connection:
        existing = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname='bid_app'")
        ).first()
        if existing is None:
            password = os.environ.get("BID_DATABASE_PASSWORD")
            statement = sql.SQL(
                "CREATE ROLE bid_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
            )
            if password:
                statement += sql.SQL(" PASSWORD {} ").format(sql.Literal(password))
            connection.execute(text(statement.as_string()))
        elif existing.rolsuper or existing.rolbypassrls:
            raise RuntimeError("Existing runtime role is privileged; refusing to change it")
    command.upgrade(Config("alembic.ini"), "head")

    async def install_queue():
        conninfo = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
        queue = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=conninfo))
        async with queue.open_async():
            with engine.connect() as connection:
                installed = connection.scalar(
                    text("SELECT to_regclass('public.procrastinate_jobs')")
                )
            if not installed:
                await queue.schema_manager.apply_schema_async()

    asyncio.run(install_queue())
    with engine.begin() as connection:
        tables = connection.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'procrastinate_%'"
            )
        )
        for (table,) in tables:
            connection.execute(
                text(
                    sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO bid_app")
                    .format(sql.Identifier(table))
                    .as_string()
                )
            )
        sequences = connection.execute(
            text(
                "SELECT sequencename FROM pg_sequences WHERE schemaname='public' AND sequencename LIKE 'procrastinate_%'"
            )
        )
        for (sequence,) in sequences:
            connection.execute(
                text(
                    sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {} TO bid_app")
                    .format(sql.Identifier(sequence))
                    .as_string()
                )
            )
    engine.dispose()


def bootstrap(org_name: str, email: str):
    password = os.environ.get("BID_BOOTSTRAP_PASSWORD", "")
    if len(password) < 12:
        raise ValueError("BID_BOOTSTRAP_PASSWORD must have at least 12 characters")
    engine = create_engine(os.environ["BID_MIGRATION_DATABASE_URL"], hide_parameters=True)
    with Session(engine) as session, session.begin():
        if session.scalar(select(User.id).where(User.email == email.lower().strip())):
            raise ValueError(
                "User already exists; refusing to change existing account authorization"
            )
        org_id = uuid4()
        session.execute(
            text("SELECT set_config('app.current_org', :org, true)"), {"org": str(org_id)}
        )
        user = User(id=uuid4(), email=email.lower().strip(), password_hash=hash_password(password))
        session.add_all([Org(id=org_id, org_id=org_id, name=org_name), user])
        session.flush()
        session.add(Membership(org_id=org_id, user_id=user.id, role="admin"))
    engine.dispose()
    print("New organization id:", org_id)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    create = commands.add_parser("bootstrap")
    create.add_argument("--org-name", required=True)
    create.add_argument("--email", required=True)
    args = parser.parse_args()
    if args.command == "init-db":
        initialize_database()
        print("Selected development database initialized; runtime role is restricted")
    else:
        bootstrap(args.org_name, args.email)


if __name__ == "__main__":
    main()
