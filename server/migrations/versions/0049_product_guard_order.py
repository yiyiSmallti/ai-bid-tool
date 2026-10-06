"""Preserve tenant and composite-FK rejection before product business guards."""

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(GUARD_SQL)


def downgrade():
    raise RuntimeError("Preserve product guards and repair forward")


GUARD_SQL = r"""
CREATE OR REPLACE FUNCTION public.management_product_root_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE v tsvector;
BEGIN
 -- BEFORE triggers precede WITH CHECK. Let the actual tenant policy reject a
 -- foreign or absent org context without exposing any business-state decision.
 -- Privileged maintenance that bypasses RLS must still pass the guards below.
 IF pg_catalog.row_security_active(TG_RELID)
    AND (NEW.org_id=nullif(current_setting('app.current_org',true),'')::uuid) IS NOT TRUE THEN
  RETURN NEW;
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new product requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.org_id IS DISTINCT FROM OLD.org_id OR NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'product identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_product_human(NEW.org_id,nullif(current_setting('app.actor_user_id',true),'')::uuid);
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.org_id=NEW.org_id
      AND e.product_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_user_id=nullif(current_setting('app.actor_user_id',true),'')::uuid)
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'product lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_product_vector(r.data) INTO v FROM public.product_revisions r
  WHERE r.org_id=NEW.org_id AND r.product_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $$;

-- Nondeferrable RI_ConstraintTrigger_* checks sort before this AFTER trigger.
-- Every row that passes the real composite FKs still runs authority/lifecycle
-- checks in this statement. A BEFORE NOT EXISTS shortcut could miss a parent
-- that becomes visible to the FK through concurrent or same-statement insertion.
-- The existing BEFORE archive guard retains the task-first lock order.
DROP TRIGGER management_product_selection_guard ON public.task_resources;
CREATE TRIGGER management_product_selection_guard AFTER INSERT OR UPDATE ON public.task_resources
 FOR EACH ROW EXECUTE FUNCTION public.management_product_selection_guard();
"""
