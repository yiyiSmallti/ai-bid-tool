"""Solution chapters with immutable revisions and human-only confirmation."""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(r"""
CREATE TABLE public.solution_chapters (
 id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES public.tasks(id),
 document_id uuid NOT NULL, title varchar(300) NOT NULL, score_range varchar(100) NOT NULL,
 current_revision integer NOT NULL DEFAULT 0 CHECK (current_revision >= 0),
 FOREIGN KEY (document_id,task_id) REFERENCES public.documents(id,task_id),
 UNIQUE (document_id,title,score_range)
);
CREATE TABLE public.solution_revisions (
 id uuid PRIMARY KEY, chapter_id uuid NOT NULL REFERENCES public.solution_chapters(id),
 revision integer NOT NULL CHECK (revision>0), state varchar(20) NOT NULL CHECK (state IN ('draft','confirmed')),
 origin varchar(20) NOT NULL CHECK (origin IN ('model','human')),
 sections jsonb NOT NULL CHECK (jsonb_typeof(sections)='array' AND jsonb_array_length(sections)>0),
 actor_kind varchar(20) NOT NULL CHECK (actor_kind IN ('session','token','agent','worker')),
 actor_token_id uuid REFERENCES public.api_tokens(id), agent_session_id uuid REFERENCES public.agent_sessions(id),
 job_id uuid REFERENCES public.jobs(id), created_at timestamptz NOT NULL DEFAULT now(), confirmed_at timestamptz,
 UNIQUE (chapter_id,revision), CHECK ((state='confirmed')=(confirmed_at IS NOT NULL))
);
ALTER TABLE public.api_tokens ADD CONSTRAINT token_forbidden_solution_confirmation
 CHECK (NOT (scopes ?| ARRAY['solution:confirm','solution:edit']));
CREATE FUNCTION public.solution_revision_gate() RETURNS trigger LANGUAGE plpgsql SET search_path TO 'pg_catalog' AS $$
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
 SELECT 1 FROM public.jobs j WHERE j.id=NEW.job_id AND j.kind='solution_draft' AND j.task_id=chapter.task_id
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
CREATE TRIGGER solution_revision_gate BEFORE INSERT OR UPDATE OR DELETE ON public.solution_revisions
 FOR EACH ROW EXECUTE FUNCTION public.solution_revision_gate();
CREATE FUNCTION public.solution_chapter_gate() RETURNS trigger LANGUAGE plpgsql SET search_path TO 'pg_catalog' AS $$
BEGIN
 IF TG_OP='DELETE' OR (TG_OP='UPDATE' AND (NEW.id IS DISTINCT FROM OLD.id OR NEW.task_id IS DISTINCT FROM OLD.task_id
 OR NEW.document_id IS DISTINCT FROM OLD.document_id OR NEW.title IS DISTINCT FROM OLD.title OR NEW.score_range IS DISTINCT FROM OLD.score_range
 OR NEW.current_revision IS DISTINCT FROM OLD.current_revision+1)) THEN
 RAISE EXCEPTION 'Solution chapter binding is immutable' USING ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' AND NEW.current_revision<>0 THEN
 RAISE EXCEPTION 'New solution must start at revision zero' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER solution_chapter_gate BEFORE INSERT OR UPDATE OR DELETE ON public.solution_chapters
 FOR EACH ROW EXECUTE FUNCTION public.solution_chapter_gate();
CREATE FUNCTION public.solution_current_gate() RETURNS trigger LANGUAGE plpgsql SET search_path TO 'pg_catalog' AS $$
DECLARE chapter_uuid uuid; current_number integer; last_number integer;
BEGIN
 IF TG_TABLE_NAME='solution_chapters' THEN chapter_uuid=NEW.id; ELSE chapter_uuid=NEW.chapter_id; END IF;
 SELECT current_revision INTO current_number FROM public.solution_chapters WHERE id=chapter_uuid;
 SELECT max(revision) INTO last_number FROM public.solution_revisions WHERE chapter_id=chapter_uuid;
 IF last_number IS NULL OR current_number IS DISTINCT FROM last_number THEN
 RAISE EXCEPTION 'Solution current revision must be the latest stored revision' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER solution_current_gate AFTER INSERT OR UPDATE ON public.solution_chapters
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.solution_current_gate();
CREATE CONSTRAINT TRIGGER solution_current_gate AFTER INSERT ON public.solution_revisions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.solution_current_gate();
GRANT SELECT,INSERT,UPDATE,DELETE ON public.solution_chapters,public.solution_revisions TO bid_app;
""")


def downgrade():
    raise NotImplementedError("Solution review history must be retained")
