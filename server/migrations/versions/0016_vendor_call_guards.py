"""Durable vendor-call reservations and idempotent per-attempt usage."""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE vendor_calls (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES orgs(id),
            job_id uuid NOT NULL,
            run_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            reserved_charge numeric(18,8) NOT NULL CHECK (reserved_charge >= 0),
            charge numeric(18,8) CHECK (charge >= 0),
            state varchar(20) NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'completed', 'unknown')),
            CHECK ((state = 'completed') = (charge IS NOT NULL)),
            UNIQUE (org_id, id),
            UNIQUE (org_id, job_id, run_id, id),
            FOREIGN KEY (org_id, job_id) REFERENCES jobs(org_id, id)
        )
    """)
    op.execute("CREATE INDEX vendor_calls_job ON vendor_calls(org_id, job_id)")
    op.execute("CREATE INDEX ix_vendor_calls_org_id ON vendor_calls(org_id)")
    op.execute("CREATE INDEX vendor_calls_unsettled ON vendor_calls(org_id, state)")
    op.execute("ALTER TABLE vendor_calls ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE vendor_calls FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON vendor_calls
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE ON vendor_calls TO bid_app")
    op.execute("""
        ALTER TABLE usage_records
            ADD COLUMN job_id uuid,
            ADD COLUMN run_id uuid,
            ADD COLUMN call_id uuid,
            ADD CONSTRAINT usage_call_identity CHECK
                (call_id IS NULL OR (job_id IS NOT NULL AND run_id IS NOT NULL)),
            ADD CONSTRAINT usage_job_fk FOREIGN KEY (org_id, job_id)
                REFERENCES jobs(org_id, id),
            ADD CONSTRAINT usage_call_fk FOREIGN KEY (org_id, job_id, run_id, call_id)
                REFERENCES vendor_calls(org_id, job_id, run_id, id),
            ADD CONSTRAINT usage_call_once UNIQUE (org_id, job_id, run_id, call_id)
    """)
    op.execute("CREATE INDEX usage_records_job ON usage_records(org_id, job_id)")
    op.execute("""
        CREATE UNIQUE INDEX balance_entries_usage_once
        ON balance_entries(org_id, usage_record_id) WHERE usage_record_id IS NOT NULL
    """)


def downgrade():
    raise RuntimeError("Keep call reservations and billed usage; rollback is application-only")
