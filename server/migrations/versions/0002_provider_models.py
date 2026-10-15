"""Several saved extraction models, one of them the default.

Each model is a slot with its own immutable revision chain; revision numbers stay
one sequence per capability, so the revision gate is unchanged. Rows saved before
this revision belong to the legacy slot, which becomes the default model.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

LEGACY_SLOT = "00000000-0000-4000-8000-000000000001"


def upgrade():
    op.execute(
        f"""
ALTER TABLE public.provider_configs
    ADD COLUMN slot uuid NOT NULL DEFAULT '{LEGACY_SLOT}'::uuid;
CREATE INDEX provider_config_slot ON public.provider_configs
    USING btree (capability, slot, revision DESC);

CREATE TABLE public.provider_slots (
    slot uuid NOT NULL,
    capability character varying(40) NOT NULL DEFAULT 'llm_extract',
    is_default boolean NOT NULL DEFAULT false,
    removed_at timestamp with time zone,
    CONSTRAINT provider_slots_pkey PRIMARY KEY (slot),
    CONSTRAINT provider_slot_capability CHECK (((capability)::text = 'llm_extract'::text)),
    CONSTRAINT provider_slot_default_live CHECK ((NOT is_default) OR (removed_at IS NULL))
);
CREATE UNIQUE INDEX provider_slot_default ON public.provider_slots
    USING btree (capability) WHERE is_default;

INSERT INTO public.provider_slots (slot, is_default)
    SELECT '{LEGACY_SLOT}'::uuid, true
    WHERE EXISTS (SELECT 1 FROM public.provider_configs);
-- Created after the legacy row: migrations run without a human actor.
CREATE FUNCTION public.provider_slot_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Provider models are removed, not deleted' USING ERRCODE='42501'; END IF;
  PERFORM public.provider_require_admin();
  IF TG_OP='UPDATE' AND (NEW.slot,NEW.capability) IS DISTINCT FROM (OLD.slot,OLD.capability) THEN
    RAISE EXCEPTION 'Provider model identity is immutable' USING ERRCODE='42501'; END IF;
  IF TG_OP='UPDATE' AND OLD.removed_at IS NOT NULL AND NEW.removed_at IS DISTINCT FROM OLD.removed_at THEN
    RAISE EXCEPTION 'Removed provider models stay removed' USING ERRCODE='42501'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER provider_slot_gate BEFORE INSERT OR UPDATE OR DELETE ON public.provider_slots
    FOR EACH ROW EXECUTE FUNCTION public.provider_slot_gate();

GRANT SELECT,INSERT,UPDATE ON TABLE public.provider_slots TO bid_app;

"""
    )


def downgrade():
    raise NotImplementedError("Provider models have no earlier representation")
