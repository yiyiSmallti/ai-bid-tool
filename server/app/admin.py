"""Explicit provisioning for a newly selected development instance.

This module is never imported by request handling or the worker. Migration
credentials are separate from application credentials; secrets stay in env.
"""

import argparse
import asyncio
import json
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


# Every column written with Secrets.encrypt. A new encrypted column must be added here.
ENCRYPTED_COLUMNS = (
    ("check_runs", "encrypted_input"),
    ("score_rubric_sets", "encrypted_input"),
    ("card_generation_runs", "encrypted_input"),
    ("confidential_values", "encrypted_value"),
    ("sandbox_inputs", "encrypted_source_url"),
    ("sandbox_fetch_receipts", "encrypted_request_metadata"),
)


def rotate_encryption() -> dict:
    """Rewrite data encrypted under BID_ENCRYPTION_KEY_PREVIOUS with BID_ENCRYPTION_KEY.

    Runs as the migration owner, one org at a time: row security binds an owner without
    BYPASSRLS, and the explicit org filter keeps the same scope for one that has it. Values already under the current key are left alone, so an
    interrupted run can simply be repeated.
    """
    from app.core.config import Settings
    from app.core.security import Secrets
    from app.providers.storage import create_storage

    settings = Settings.load()
    if not settings.encryption_key_previous:
        raise ValueError("Set BID_ENCRYPTION_KEY_PREVIOUS to the retired keys first")
    data, storage = Secrets.for_data(settings), create_storage(settings)
    engine = create_engine(os.environ["BID_MIGRATION_DATABASE_URL"], hide_parameters=True)
    report = {"orgs": 0, "fields_checked": 0, "fields_rewritten": 0}
    report |= {"objects_checked": 0, "objects_rewritten": 0}
    with engine.connect() as connection:
        orgs = connection.scalars(text("SELECT id FROM platform_org_summaries()")).all()
    for org_id in orgs:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.current_org', :org, true)"), {"org": str(org_id)}
            )
            for table, column in ENCRYPTED_COLUMNS:
                rows = connection.execute(
                    text(
                        sql.SQL("SELECT id, {} FROM {} WHERE org_id = :org AND {} IS NOT NULL")
                        .format(
                            sql.Identifier(column), sql.Identifier(table), sql.Identifier(column)
                        )
                        .as_string()
                    ),
                    {"org": org_id},
                ).all()
                update = text(
                    sql.SQL("UPDATE {} SET {} = :value WHERE org_id = :org AND id = :id")
                    .format(sql.Identifier(table), sql.Identifier(column))
                    .as_string()
                )
                for row_id, value in rows:
                    report["fields_checked"] += 1
                    rotated = data.rotate(value)
                    if rotated is not None:
                        connection.execute(update, {"value": rotated, "org": org_id, "id": row_id})
                        report["fields_rewritten"] += 1
        checked, rewritten = asyncio.run(storage.rotate(org_id))
        report["objects_checked"] += checked
        report["objects_rewritten"] += rewritten
        report["orgs"] += 1
    engine.dispose()
    return report


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    create = commands.add_parser("bootstrap")
    create.add_argument("--org-name", required=True)
    create.add_argument("--email", required=True)
    commands.add_parser("rotate-encryption")
    totp_parser = commands.add_parser("platform-totp")
    totp_parser.add_argument("--email", required=True)
    args = parser.parse_args()
    if args.command == "init-db":
        initialize_database()
        print("Selected development database initialized; runtime role is restricted")
    elif args.command == "rotate-encryption":
        print(json.dumps(rotate_encryption()))
    elif args.command == "platform-totp":
        platform_totp(args.email)
    else:
        bootstrap(args.org_name, args.email)


def platform_totp(email: str):
    """Print a new secret for one operator; it goes into BID_PLATFORM_TOTP_SECRETS."""
    from app.core.totp import generate_secret, provisioning_uri

    email = email.strip().lower()
    secret = generate_secret()
    print("Add to BID_PLATFORM_TOTP_SECRETS:", f"{email}:{secret}")
    print("Scan in an authenticator app:", provisioning_uri(email, secret))


if __name__ == "__main__":
    main()
