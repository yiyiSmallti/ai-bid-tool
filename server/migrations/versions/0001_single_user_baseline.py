"""Single-user baseline schema.

Replaces the multi-org migration history (ADR 0012). The runtime role `bid_app`
must exist before this revision runs; `python -m app.admin init-db` creates it.
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

BASELINE = Path(__file__).parent.parent / "baseline"


def upgrade():
    # The DBAPI cursor runs in Alembic's transaction. Without parameters psycopg sends
    # each file as one query, so dollar-quoted bodies keep their semicolons and `%`.
    cursor = op.get_bind().connection.cursor()
    # Functions are created before the tables their SQL-language bodies read.
    cursor.execute("SET LOCAL check_function_bodies = false")
    for path in sorted(BASELINE.glob("*.sql")):
        cursor.execute(path.read_text())


def downgrade():
    raise NotImplementedError("The baseline has no earlier revision")
