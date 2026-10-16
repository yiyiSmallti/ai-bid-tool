"""Import branch for the memory confirmation gate (ADR 0014).

A card confirmed from a model proposal was checked against the library memory of
the installation that confirmed it. The importing installation's library has its
own memory epochs, so an imported confirmation is not compared with them.
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        r"""
CREATE OR REPLACE FUNCTION public.memory_card_confirmation_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE prior public.response_card_revisions%ROWTYPE;
BEGIN
 -- An imported confirmation was current against its source's library memory.
 IF public.package_imported((SELECT task_id FROM public.response_cards WHERE id=NEW.card_id)) THEN RETURN NULL; END IF;
 SELECT * INTO prior FROM public.response_card_revisions WHERE card_id=NEW.card_id AND revision=NEW.revision-1;
 IF NEW.state='confirmed' AND prior.state IS DISTINCT FROM 'confirmed' AND NEW.model_job_id IS NOT NULL
  AND public.memory_generation_current(NEW.model_job_id) IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'Memory input changed; edit or regenerate before confirmation' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $function$;
"""
    )


def downgrade():
    raise NotImplementedError("Package import branches have no earlier representation")
