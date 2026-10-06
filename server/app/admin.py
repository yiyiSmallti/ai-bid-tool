"""Explicit provisioning for a newly selected development instance.

This module is never imported by request handling or the worker. Migration
credentials are separate from application credentials; secrets stay in env.
"""

import argparse
import asyncio
import json
import os
import sys
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
    # Bootstrap credentials are optional for peer/certificate authentication, but when
    # supplied they belong only to this owner command, never a vendor credential row.
    from sqlalchemy.exc import SQLAlchemyError

    for role, variable in (
        ("bid_platform_app", "BID_PLATFORM_DATABASE_PASSWORD"),
        ("bid_credential_reader", "BID_CREDENTIAL_DATABASE_PASSWORD"),
    ):
        password = os.environ.get(variable)
        if password:
            try:
                with engine.begin() as connection:
                    statement = sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                    connection.exec_driver_sql(statement.as_string())
            except SQLAlchemyError:
                raise ValueError(f"Could not configure {variable}") from None

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
    ("requirement_workflows", "last_reason_ciphertext"),
    ("card_comments", "body_ciphertext"),
    ("agent_sessions", "start_receipt_enc"),
    ("agent_sessions", "cancel_receipt_enc"),
    ("agent_messages", "content_enc"),
    ("agent_messages", "receipt_enc"),
    ("agent_steps", "arguments_enc"),
    ("agent_steps", "result_enc"),
    ("agent_pauses", "question_enc"),
    ("agent_pauses", "receipt_enc"),
    ("check_runs", "encrypted_input"),
    ("score_rubric_sets", "encrypted_input"),
    ("score_reports", "encrypted_input"),
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


def rotate_provider_secrets() -> dict:
    """Rewrap platform and all historical BYOK rows through owner-only CAS functions.

    Counts change only after commit. A failed row or scope scan contributes a fixed
    diagnostic and cannot erase previously committed progress from the report.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from app.core.config import Settings
    from app.core.errors import ServiceError
    from app.core.provider_secrets import ProviderSecrets
    from app.schemas.platform_credentials import CredentialSpec, RotationReport

    settings = Settings.load()
    if not settings.secrets_key_previous:
        raise ValueError("Set BID_SECRETS_KEY_PREVIOUS to the retired keys first")
    cipher = ProviderSecrets(settings)
    engine = create_engine(os.environ["BID_MIGRATION_DATABASE_URL"], hide_parameters=True)
    counts = {
        "platform_checked": 0,
        "platform_rewritten": 0,
        "org_revisions_checked": 0,
        "org_revisions_rewritten": 0,
        "failed": 0,
    }
    permanent_failed, successes = False, 0

    def transient(exc: Exception) -> bool:
        if isinstance(exc, ServiceError):
            return exc.exit_code == 3
        state = getattr(getattr(exc, "orig", None), "sqlstate", None)
        return bool(
            getattr(exc, "connection_invalidated", False)
            or (state and (state.startswith("08") or state in {"40001", "40P01"}))
        )

    def record_failure(kind: str, row_id, exc: Exception) -> None:
        nonlocal permanent_failed
        retryable = transient(exc)
        counts["failed"] += 1
        permanent_failed |= not retryable
        # Restricted diagnostics contain stable identities and fixed classes only.
        print(
            json.dumps(
                {
                    "scope": "provider-secrets",
                    "kind": kind,
                    "id": str(row_id),
                    "code": "rewrap_conflict" if retryable else "rewrap_failed",
                }
            ),
            file=sys.stderr,
        )

    def conflict() -> ServiceError:
        return ServiceError(
            "credential_rotation_conflict", "Credential changed during rewrap", 409, 3
        )

    try:
        try:
            with engine.connect() as connection:
                platform_ids = connection.scalars(
                    text(
                        "SELECT id FROM public.platform_credentials WHERE state <> 'removed' ORDER BY id"
                    )
                ).all()
        except SQLAlchemyError as exc:
            record_failure("platform_scan", "platform_scan", exc)
            platform_ids = []
        for credential_id in platform_ids:
            counts["platform_checked"] += 1
            did_rewrite = False
            try:
                with engine.begin() as connection:
                    row = (
                        connection.execute(
                            text(
                                "SELECT id, name, purpose, provider, endpoint, secret_version, encrypted_key "
                                "FROM public.platform_credentials WHERE id=:id AND state <> 'removed'"
                            ),
                            {"id": credential_id},
                        )
                        .mappings()
                        .first()
                    )
                    if row is None:
                        counts["platform_checked"] -= 1
                        continue
                    spec = CredentialSpec(
                        **{name: row[name] for name in ("name", "purpose", "provider", "endpoint")}
                    )
                    value = cipher.rewrap_platform(
                        row["encrypted_key"],
                        expected=spec,
                        credential_id=row["id"],
                        secret_version=row["secret_version"],
                    )
                    if value is not None:
                        if not connection.scalar(
                            text(
                                "SELECT public.platform_credential_rewrap(:id, :old, :new, :actor)"
                            ),
                            {
                                "id": row["id"],
                                "old": row["encrypted_key"],
                                "new": value,
                                "actor": "maintenance@localhost",
                            },
                        ):
                            raise conflict()
                        did_rewrite = True
                counts["platform_rewritten"] += int(did_rewrite)
                successes += 1
            except (ServiceError, ValueError, SQLAlchemyError) as exc:
                record_failure("platform", credential_id, exc)
        try:
            with engine.connect() as connection:
                orgs = connection.scalars(
                    text("SELECT id FROM public.platform_org_summaries()")
                ).all()
        except SQLAlchemyError as exc:
            record_failure("org_scan", "org_scan", exc)
            orgs = []
        for org_id in orgs:
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text("SELECT set_config('app.current_org', :org, true)"),
                        {"org": str(org_id)},
                    )
                    config_ids = connection.scalars(
                        text(
                            "SELECT id FROM public.provider_configs WHERE org_id=:org AND source='org' ORDER BY revision"
                        ),
                        {"org": org_id},
                    ).all()
            except SQLAlchemyError as exc:
                record_failure("org_revision_scan", org_id, exc)
                continue
            for config_id in config_ids:
                counts["org_revisions_checked"] += 1
                did_rewrite = False
                try:
                    with engine.begin() as connection:
                        connection.execute(
                            text("SELECT set_config('app.current_org', :org, true)"),
                            {"org": str(org_id)},
                        )
                        row = connection.execute(
                            text(
                                "SELECT encrypted_key FROM public.provider_configs WHERE org_id=:org AND id=:id"
                            ),
                            {"org": org_id, "id": config_id},
                        ).first()
                        if row is None:
                            counts["org_revisions_checked"] -= 1
                            continue
                        value = cipher.rewrap_org(
                            row.encrypted_key, org_id=org_id, config_id=config_id
                        )
                        if value is not None:
                            if not connection.scalar(
                                text(
                                    "SELECT public.provider_credential_rewrap(:org, :id, :old, :new)"
                                ),
                                {
                                    "org": org_id,
                                    "id": config_id,
                                    "old": row.encrypted_key,
                                    "new": value,
                                },
                            ):
                                raise conflict()
                            did_rewrite = True
                    counts["org_revisions_rewritten"] += int(did_rewrite)
                    successes += 1
                except (ServiceError, ValueError, SQLAlchemyError) as exc:
                    record_failure("org_revision", config_id, exc)
        code = 0 if not counts["failed"] else 5 if successes else 4 if permanent_failed else 3
        return RotationReport.model_validate({**counts, "exit_code": code}).model_dump()
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    create = commands.add_parser("bootstrap")
    create.add_argument("--org-name", required=True)
    create.add_argument("--email", required=True)
    rotate = commands.add_parser("rotate-encryption")
    rotate.add_argument("--scope", choices=("data", "provider-secrets"), default="data")
    workflow_parser = commands.add_parser("team-workflow")
    workflow_parser.add_argument("action", choices=("preflight", "import", "cutover"))
    workflow_parser.add_argument("--org-id", required=True)
    workflow_parser.add_argument("--mapping")
    workflow_parser.add_argument("--workers-drained", action="store_true")
    totp_parser = commands.add_parser("platform-totp")
    totp_parser.add_argument("--email", required=True)
    args = parser.parse_args()
    if args.command == "init-db":
        initialize_database()
        print("Selected development database initialized; runtime role is restricted")
    elif args.command == "rotate-encryption":
        if args.scope == "provider-secrets":
            from sqlalchemy.exc import SQLAlchemyError

            from app.core.errors import ServiceError

            try:
                report = rotate_provider_secrets()
            except ServiceError as exc:
                print(json.dumps({"error": exc.code}), file=sys.stderr)
                raise SystemExit(exc.exit_code) from None
            except (ValueError, KeyError):
                print(
                    json.dumps({"error": "credential_rotation_configuration_invalid"}),
                    file=sys.stderr,
                )
                raise SystemExit(4) from None
            except SQLAlchemyError:
                print(json.dumps({"error": "credential_backend_unavailable"}), file=sys.stderr)
                raise SystemExit(3) from None
        else:
            report = rotate_encryption()
        print(json.dumps(report))
        raise SystemExit(report.get("exit_code", 0))
    elif args.command == "team-workflow":
        from uuid import UUID

        from app.team_workflow_admin import output, run

        engine = create_engine(os.environ["BID_MIGRATION_DATABASE_URL"], hide_parameters=True)
        try:
            output(
                run(
                    engine,
                    UUID(args.org_id),
                    mapping_path=args.mapping,
                    apply=args.action == "import",
                    cutover=args.action == "cutover",
                    workers_drained=args.workers_drained,
                )
            )
        finally:
            engine.dispose()
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
