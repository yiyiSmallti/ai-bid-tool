import os

from alembic import context
from app.models import Base
from sqlalchemy import create_engine

target_metadata = Base.metadata


def run_migrations():
    # Migration credentials are separate; runtime roles must never own tables.
    url = os.environ["BID_MIGRATION_DATABASE_URL"]
    with create_engine(url, hide_parameters=True).connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
