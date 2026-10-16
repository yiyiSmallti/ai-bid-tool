"""Company and personal models, each member's default model and monthly quota.

A personal model names its owner in `provider_slots.owner_member_id`; a company
model has none. Only the owner writes a personal model, and on a gateway only an
administrator writes a company model, checked here through
`app.actor_member_id` as well as in the service. Only company models can be
the company default. `member_settings` holds a member's own default model and
`member_quotas` the monthly cap on company-model tokens.
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        r"""
ALTER TABLE public.provider_slots
    ADD COLUMN owner_member_id uuid REFERENCES public.members(id);
ALTER TABLE public.provider_slots ADD CONSTRAINT provider_slot_default_company
    CHECK ((NOT is_default) OR (owner_member_id IS NULL));
CREATE INDEX provider_slot_owner ON public.provider_slots USING btree (owner_member_id)
    WHERE (owner_member_id IS NOT NULL);

-- A missing slot row is a company model saved before slots existed.
CREATE FUNCTION public.provider_slot_writable(model uuid) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE owner uuid := (SELECT owner_member_id FROM public.provider_slots WHERE slot=model);
BEGIN
  IF owner IS NOT NULL AND owner IS DISTINCT FROM public.actor_member() THEN
    RAISE EXCEPTION 'A personal model belongs to the acting member' USING ERRCODE='42501';
  END IF;
  IF owner IS NULL AND EXISTS (SELECT 1 FROM public.installation WHERE mode='enterprise')
     AND NOT EXISTS (
       SELECT 1 FROM public.members WHERE id=public.actor_member() AND role='admin'
     ) THEN
    RAISE EXCEPTION 'A company model requires an administrator' USING ERRCODE='42501';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION public.provider_slot_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Provider models are removed, not deleted' USING ERRCODE='42501'; END IF;
  PERFORM public.provider_require_admin();
  IF TG_OP='UPDATE' AND (NEW.slot,NEW.capability,NEW.owner_member_id)
     IS DISTINCT FROM (OLD.slot,OLD.capability,OLD.owner_member_id) THEN
    RAISE EXCEPTION 'Provider model identity is immutable' USING ERRCODE='42501'; END IF;
  IF TG_OP='UPDATE' AND OLD.removed_at IS NOT NULL AND NEW.removed_at IS DISTINCT FROM OLD.removed_at THEN
    RAISE EXCEPTION 'Removed provider models stay removed' USING ERRCODE='42501'; END IF;
  IF TG_OP='INSERT' AND NEW.owner_member_id IS NOT NULL
     AND NEW.owner_member_id IS DISTINCT FROM public.actor_member() THEN
    RAISE EXCEPTION 'A personal model belongs to the acting member' USING ERRCODE='42501'; END IF;
  IF TG_OP='UPDATE' OR NEW.owner_member_id IS NULL THEN
    PERFORM public.provider_slot_writable(NEW.slot);
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION public.provider_config_owner_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
  -- The re-encryption path changes no model and runs as the table owner.
  IF TG_OP='INSERT' THEN PERFORM public.provider_slot_writable(NEW.slot); END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER provider_config_owner_gate BEFORE INSERT ON public.provider_configs
    FOR EACH ROW EXECUTE FUNCTION public.provider_config_owner_gate();

CREATE TABLE public.member_settings (
    member_id uuid NOT NULL REFERENCES public.members(id),
    default_slot uuid REFERENCES public.provider_slots(slot),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT member_settings_pkey PRIMARY KEY (member_id)
);

CREATE TABLE public.member_quotas (
    member_id uuid NOT NULL REFERENCES public.members(id),
    monthly_company_tokens bigint,
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT member_quotas_pkey PRIMARY KEY (member_id),
    CONSTRAINT member_quota_tokens CHECK ((monthly_company_tokens IS NULL) OR (monthly_company_tokens >= 0))
);

-- Admission sums one member's month without scanning every call.
CREATE INDEX vendor_call_member_month ON public.vendor_calls USING btree (member_id, created_at)
    WHERE (member_id IS NOT NULL);
CREATE INDEX usage_record_member_month ON public.usage_records USING btree (member_id, created_at)
    WHERE (member_id IS NOT NULL);

GRANT SELECT,INSERT,UPDATE ON TABLE public.member_settings TO bid_app;
GRANT SELECT,INSERT,UPDATE ON TABLE public.member_quotas TO bid_app;
"""
    )


def downgrade():
    raise NotImplementedError("Member models have no earlier representation")
