"""Solution revisions: figure jobs write model revisions.

A `solution_figures` job publishes the chapter's drawn figures as a new model
revision, under the same owned-job rule as a `solution_draft` job.
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        r"""
CREATE OR REPLACE FUNCTION public.solution_revision_gate() RETURNS trigger LANGUAGE plpgsql SET search_path TO 'pg_catalog' AS $$
DECLARE chapter public.solution_chapters%ROWTYPE; previous public.solution_revisions%ROWTYPE; section jsonb;
BEGIN
 IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Solution revisions are immutable' USING ERRCODE='23514'; END IF;
 PERFORM public.response_check_actor(NEW.actor_token_id,NEW.actor_kind);
 IF NEW.agent_session_id IS DISTINCT FROM NULLIF(current_setting('app.agent_session_id',true),'')::uuid
 OR NEW.job_id IS DISTINCT FROM NULLIF(current_setting('app.execution_job_id',true),'')::uuid THEN
 RAISE EXCEPTION 'Solution attribution mismatch' USING ERRCODE='42501'; END IF;
 SELECT * INTO chapter FROM public.solution_chapters WHERE id=NEW.chapter_id FOR UPDATE;
 IF NEW.revision IS DISTINCT FROM chapter.current_revision+1 THEN
 RAISE EXCEPTION 'Solution revision conflict' USING ERRCODE='23514'; END IF;
 IF NEW.origin='human' OR NEW.state='confirmed' THEN PERFORM public.response_require_human(); END IF;
 IF NEW.origin='model' AND (NEW.actor_kind<>'worker' OR NEW.job_id IS NULL OR NOT EXISTS(
 SELECT 1 FROM public.jobs j WHERE j.id=NEW.job_id AND j.kind IN ('solution_draft','solution_figures') AND j.task_id=chapter.task_id
 AND j.status='running' AND j.run_id=NULLIF(current_setting('app.execution_run_id',true),'')::uuid
 AND j.lease_until>clock_timestamp())) THEN
 RAISE EXCEPTION 'Solution model revision requires an owned job' USING ERRCODE='42501'; END IF;
 IF NEW.state='confirmed' THEN
 SELECT * INTO previous FROM public.solution_revisions WHERE chapter_id=NEW.chapter_id AND revision=chapter.current_revision;
 IF previous.id IS NULL OR previous.sections IS DISTINCT FROM NEW.sections OR previous.state<>'draft' THEN
 RAISE EXCEPTION 'Only the current unchanged draft can be confirmed' USING ERRCODE='23514'; END IF;
 END IF;
 FOR section IN SELECT value FROM jsonb_array_elements(NEW.sections) LOOP
 IF jsonb_typeof(section)<>'object' OR jsonb_typeof(section->'heading') IS DISTINCT FROM 'string'
 OR length(btrim(section->>'heading'))=0 OR jsonb_typeof(section->'paragraphs') IS DISTINCT FROM 'array'
 OR jsonb_array_length(section->'paragraphs')=0 OR EXISTS (SELECT 1 FROM jsonb_array_elements(section->'paragraphs') p
 WHERE jsonb_typeof(p)<>'string' OR length(btrim(p#>>'{}'))=0) THEN
 RAISE EXCEPTION 'Invalid solution sections' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
"""
    )


def downgrade():
    raise NotImplementedError("Figure revisions have no earlier representation")
