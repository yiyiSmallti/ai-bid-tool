"""Opening an export page preview is an audited human export event like a download."""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

GATE = """
    CREATE OR REPLACE FUNCTION export_audit_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE j public.jobs%ROWTYPE;
    BEGIN
      IF NEW.action NOT LIKE 'export.%' THEN RETURN NEW; END IF;
      IF NEW.org_id IS DISTINCT FROM NULLIF(current_setting('app.current_org',true),'')::uuid
        OR NEW.actor_user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
        OR NEW.actor_token_id IS NOT NULL
        OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
        OR NEW.details->>'actor_kind' IS DISTINCT FROM current_setting('app.actor_kind',true) THEN
        RAISE EXCEPTION 'Export audit actor context mismatch' USING ERRCODE='42501';
      END IF;
      IF NEW.action IN ('export.render_completed','export.render_failed') THEN
        SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id
          AND id=(NEW.details->>'render_job_id')::uuid;
        IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
          OR j.id IS NULL OR j.kind<>'export_render'
          OR j.run_id IS DISTINCT FROM (NEW.details->>'attempt_id')::uuid
          OR NEW.object_id IS DISTINCT FROM (NEW.details->>'run_id')::uuid
          OR NEW.details->>'run_id' IS DISTINCT FROM current_setting('app.export_run_id',true)
          OR NEW.details->>'attempt_id' IS DISTINCT FROM current_setting('app.export_attempt_id',true)
          OR j.result->'submission'->>'export_run_id' IS DISTINCT FROM NEW.object_id::text
          OR j.result->'submission'->>'actor_user_id' IS DISTINCT FROM NEW.actor_user_id::text THEN
          RAISE EXCEPTION 'Export worker audit is not bound to this attempt' USING ERRCODE='42501';
        END IF;
        IF NEW.action='export.render_completed' AND
          (public.export_worker(NEW.org_id,NEW.object_id,j.id,NEW.actor_user_id) IS DISTINCT FROM true
            OR NOT EXISTS (SELECT 1 FROM public.export_render_candidates c WHERE c.org_id=NEW.org_id
              AND c.run_id=NEW.object_id AND c.attempt_id=j.run_id)) THEN
          RAISE EXCEPTION 'Export completion audit requires a current candidate' USING ERRCODE='23514';
        END IF;
      ELSIF NEW.action='export.binding_created' THEN
        PERFORM public.response_require_human(NEW.org_id,'admin');
        IF NOT EXISTS (SELECT 1 FROM public.export_template_bindings b WHERE b.org_id=NEW.org_id
          AND b.id=NEW.object_id AND b.reviewed_by=NEW.actor_user_id) THEN
          RAISE EXCEPTION 'Export binding audit has no binding' USING ERRCODE='23514';
        END IF;
      ELSIF NEW.action IN ('export.prepared','export.released','export.render_cancelled',
        'export.download_link_issued','export.download_served','export.preview_opened') THEN
        PERFORM public.response_require_human(NEW.org_id,'commercial');
        IF NEW.action IN ('export.prepared','export.render_cancelled') THEN
          IF NOT EXISTS (SELECT 1 FROM public.export_runs r WHERE r.org_id=NEW.org_id AND r.id=NEW.object_id) THEN
            RAISE EXCEPTION 'Export run audit has no run' USING ERRCODE='23514';
          END IF;
        ELSE
          IF NOT EXISTS (SELECT 1 FROM public.exports e WHERE e.org_id=NEW.org_id AND e.id=NEW.object_id) THEN
            RAISE EXCEPTION 'Export file audit has no release' USING ERRCODE='23514';
          END IF;
        END IF;
      ELSE RAISE EXCEPTION 'Unknown export audit event' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
"""

PREVIOUS_GATE = """
    CREATE OR REPLACE FUNCTION export_audit_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE j public.jobs%ROWTYPE;
    BEGIN
      IF NEW.action NOT LIKE 'export.%' THEN RETURN NEW; END IF;
      IF NEW.org_id IS DISTINCT FROM NULLIF(current_setting('app.current_org',true),'')::uuid
        OR NEW.actor_user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
        OR NEW.actor_token_id IS NOT NULL
        OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
        OR NEW.details->>'actor_kind' IS DISTINCT FROM current_setting('app.actor_kind',true) THEN
        RAISE EXCEPTION 'Export audit actor context mismatch' USING ERRCODE='42501';
      END IF;
      IF NEW.action IN ('export.render_completed','export.render_failed') THEN
        SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id
          AND id=(NEW.details->>'render_job_id')::uuid;
        IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
          OR j.id IS NULL OR j.kind<>'export_render'
          OR j.run_id IS DISTINCT FROM (NEW.details->>'attempt_id')::uuid
          OR NEW.object_id IS DISTINCT FROM (NEW.details->>'run_id')::uuid
          OR NEW.details->>'run_id' IS DISTINCT FROM current_setting('app.export_run_id',true)
          OR NEW.details->>'attempt_id' IS DISTINCT FROM current_setting('app.export_attempt_id',true)
          OR j.result->'submission'->>'export_run_id' IS DISTINCT FROM NEW.object_id::text
          OR j.result->'submission'->>'actor_user_id' IS DISTINCT FROM NEW.actor_user_id::text THEN
          RAISE EXCEPTION 'Export worker audit is not bound to this attempt' USING ERRCODE='42501';
        END IF;
        IF NEW.action='export.render_completed' AND
          (public.export_worker(NEW.org_id,NEW.object_id,j.id,NEW.actor_user_id) IS DISTINCT FROM true
            OR NOT EXISTS (SELECT 1 FROM public.export_render_candidates c WHERE c.org_id=NEW.org_id
              AND c.run_id=NEW.object_id AND c.attempt_id=j.run_id)) THEN
          RAISE EXCEPTION 'Export completion audit requires a current candidate' USING ERRCODE='23514';
        END IF;
      ELSIF NEW.action='export.binding_created' THEN
        PERFORM public.response_require_human(NEW.org_id,'admin');
        IF NOT EXISTS (SELECT 1 FROM public.export_template_bindings b WHERE b.org_id=NEW.org_id
          AND b.id=NEW.object_id AND b.reviewed_by=NEW.actor_user_id) THEN
          RAISE EXCEPTION 'Export binding audit has no binding' USING ERRCODE='23514';
        END IF;
      ELSIF NEW.action IN ('export.prepared','export.released','export.render_cancelled',
        'export.download_link_issued','export.download_served') THEN
        PERFORM public.response_require_human(NEW.org_id,'commercial');
        IF NEW.action IN ('export.prepared','export.render_cancelled') THEN
          IF NOT EXISTS (SELECT 1 FROM public.export_runs r WHERE r.org_id=NEW.org_id AND r.id=NEW.object_id) THEN
            RAISE EXCEPTION 'Export run audit has no run' USING ERRCODE='23514';
          END IF;
        ELSE
          IF NOT EXISTS (SELECT 1 FROM public.exports e WHERE e.org_id=NEW.org_id AND e.id=NEW.object_id) THEN
            RAISE EXCEPTION 'Export file audit has no release' USING ERRCODE='23514';
          END IF;
        END IF;
      ELSE RAISE EXCEPTION 'Unknown export audit event' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
"""


def upgrade():
    op.execute(GATE)


def downgrade():
    # Recorded preview audits stay; the previous gate only checks new inserts.
    op.execute(PREVIOUS_GATE)
