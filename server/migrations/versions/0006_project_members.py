"""Gateway project members, one owner per project, and the personal-export flag.

The table stays empty in a personal database. On a gateway, a deferred trigger
requires every project to have an owner at commit: a new task without one, or
removing or demoting the owner without naming a new one, fails with
`task_owner_required`. A hand-over demotes the old owner before promoting the
new one, so the single-owner index holds at every statement.
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
CREATE TABLE public.task_members (
    task_id uuid NOT NULL REFERENCES public.tasks(id) ON DELETE CASCADE,
    member_id uuid NOT NULL REFERENCES public.members(id),
    role character varying(10) NOT NULL,
    added_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT task_members_pkey PRIMARY KEY (task_id, member_id),
    CONSTRAINT task_member_role CHECK (((role)::text = ANY ((ARRAY['owner'::character varying, 'member'::character varying])::text[])))
);
CREATE UNIQUE INDEX task_single_owner ON public.task_members USING btree (task_id) WHERE ((role)::text = 'owner'::text);
CREATE INDEX task_member_member ON public.task_members USING btree (member_id);

CREATE FUNCTION public.task_owner_required() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
-- Through jsonb, since a branch on the other table's row type fails to plan.
DECLARE project uuid := CASE WHEN TG_TABLE_NAME = 'tasks' THEN (to_jsonb(NEW)->>'id')::uuid
                             ELSE (to_jsonb(OLD)->>'task_id')::uuid END;
BEGIN
  IF EXISTS (SELECT 1 FROM public.installation WHERE mode='enterprise')
     AND EXISTS (SELECT 1 FROM public.tasks WHERE id=project)
     AND NOT EXISTS (
       SELECT 1 FROM public.task_members WHERE task_id=project AND role='owner'
     ) THEN
    RAISE EXCEPTION 'task_owner_required: a project needs an owner'
      USING ERRCODE='23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER task_owner_required AFTER INSERT ON public.tasks
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.task_owner_required();
CREATE CONSTRAINT TRIGGER task_owner_required AFTER UPDATE OR DELETE ON public.task_members
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.task_owner_required();

ALTER TABLE public.tasks
    ADD COLUMN export_to_personal_allowed boolean NOT NULL DEFAULT false;

GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE public.task_members TO bid_app;
"""
    )


def downgrade():
    raise NotImplementedError("Project members have no earlier representation")
