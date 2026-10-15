"""The deployment mode a database was initialized with.

One row, written by `python -m app.admin init-db`; a database without it is a
personal database. The row is immutable: moving data between modes uses
packages, not an update.
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
CREATE TABLE public.installation (
    id boolean NOT NULL DEFAULT true,
    mode character varying(20) NOT NULL,
    company_name character varying(100),
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT installation_pkey PRIMARY KEY (id),
    CONSTRAINT installation_singleton CHECK (id),
    CONSTRAINT installation_mode CHECK (((mode)::text = ANY ((ARRAY['personal'::character varying, 'enterprise'::character varying])::text[]))),
    CONSTRAINT installation_company CHECK (((mode)::text = 'enterprise'::text) = (company_name IS NOT NULL))
);
CREATE FUNCTION public.installation_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
  RAISE EXCEPTION 'The deployment mode is immutable' USING ERRCODE='42501';
END $$;
CREATE TRIGGER installation_immutable BEFORE UPDATE OR DELETE ON public.installation
    FOR EACH ROW EXECUTE FUNCTION public.installation_immutable();

GRANT SELECT ON TABLE public.installation TO bid_app;
"""
    )


def downgrade():
    raise NotImplementedError("The deployment mode has no earlier representation")
