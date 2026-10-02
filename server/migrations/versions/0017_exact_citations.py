"""Exact stored citations, preserved model quotes and immutable review baselines."""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE requirements ADD COLUMN model_quote text")
    op.execute("""
        ALTER TABLE response_card_revisions ADD COLUMN quote_sha256 varchar(64)
            CHECK (quote_sha256 ~ '^[0-9a-f]{64}$')
    """)
    # No history backfill: legacy revisions use the original quote retained by
    # repair. New revisions fix their own hash, and all history gates stay active.
    op.execute(r"""
        CREATE FUNCTION response_normalize_quote(value text) RETURNS text
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET search_path=pg_catalog AS $$
          SELECT translate(normalize(value, NFKC),
            U&'\201C\201D\201E\201F\2018\2019\201A\201B\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000',
            U&'\0022\0022\0022\0022\0027\0027\0027\0027')
        $$;
        CREATE OR REPLACE FUNCTION response_citation_valid(p_org uuid,p_requirement uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
          WITH located AS (
            SELECT r.quote, CASE WHEN r.page IS NOT NULL AND r.page=c.page AND r.location IS NULL
                AND c.blocks IS NULL THEN c.text
              WHEN r.page IS NULL AND c.page IS NULL AND r.location IS NOT NULL THEN
                (SELECT b->>'text' FROM jsonb_array_elements(c.blocks) b WHERE b-'text'=r.location)
              END AS source_text
            FROM public.requirements r JOIN public.chunks c ON c.org_id=r.org_id AND c.id=r.chunk_id
            WHERE r.org_id=p_org AND r.id=p_requirement AND c.task_id=r.task_id
              AND c.document_id=r.document_id AND c.citation_verified
          ), normalized AS (
            SELECT *,public.response_normalize_quote(quote) AS needle,
              public.response_normalize_quote(source_text) AS haystack FROM located
          )
          SELECT length(needle)>0 AND position(quote in source_text)>0
            AND position(needle in haystack)>0
            AND position(needle in substring(haystack from position(needle in haystack)+1))=0
          FROM normalized
        $$;
        CREATE FUNCTION response_quote_current(p_org uuid,p_revision uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
          SELECT coalesce(v.quote_sha256,
              encode(sha256(convert_to(coalesce(r.model_quote,r.quote),'UTF8')),'hex'))
            = encode(sha256(convert_to(r.quote,'UTF8')),'hex')
          FROM public.response_card_revisions v
          JOIN public.response_cards c ON c.org_id=v.org_id AND c.id=v.card_id
          JOIN public.requirements r ON r.org_id=c.org_id AND r.id=c.requirement_id
          WHERE v.org_id=p_org AND v.id=p_revision
        $$;
        CREATE FUNCTION response_revision_quote_gate() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
        DECLARE current_hash text; previous_hash text; previous_id uuid;
        BEGIN
          SELECT encode(sha256(convert_to(r.quote,'UTF8')),'hex') INTO current_hash
            FROM public.requirements r JOIN public.response_cards c
              ON c.org_id=r.org_id AND c.requirement_id=r.id
            WHERE c.org_id=NEW.org_id AND c.id=NEW.card_id;
          SELECT v.id,coalesce(v.quote_sha256,
              encode(sha256(convert_to(coalesce(r.model_quote,r.quote),'UTF8')),'hex'))
            INTO previous_id,previous_hash
            FROM public.response_card_revisions v
            JOIN public.response_cards c ON c.org_id=v.org_id AND c.id=v.card_id
            JOIN public.requirements r ON r.org_id=c.org_id AND r.id=c.requirement_id
            WHERE v.org_id=NEW.org_id AND v.card_id=NEW.card_id AND v.revision=NEW.revision-1;
          NEW.quote_sha256 := coalesce(NEW.quote_sha256,current_hash);
          IF NEW.quote_sha256 IS DISTINCT FROM current_hash AND
             (previous_id IS NULL OR NEW.quote_sha256 IS DISTINCT FROM previous_hash) THEN
            RAISE EXCEPTION 'Invalid response citation baseline' USING ERRCODE='23514';
          END IF;
          IF NEW.state='confirmed' AND (NEW.quote_sha256 IS DISTINCT FROM current_hash
             OR previous_hash IS DISTINCT FROM current_hash) THEN
            RAISE EXCEPTION 'Response citation requires renewed review' USING ERRCODE='23514';
          END IF;
          IF previous_id IS NOT NULL AND NEW.disposition='comply_only'
             AND NEW.quote_sha256 IS DISTINCT FROM previous_hash THEN
            PERFORM public.response_require_human(NEW.org_id,NEW.review_domain);
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER response_revision_quote_gate BEFORE INSERT ON response_card_revisions
          FOR EACH ROW EXECUTE FUNCTION response_revision_quote_gate();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION response_item_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE run public.draft_runs%ROWTYPE; run_xid text; requirement public.requirements%ROWTYPE;
            card public.response_cards%ROWTYPE; revision public.response_card_revisions%ROWTYPE; target_table text;
            citation_ok boolean; material_ok boolean; quote_current boolean; expected_reasons jsonb := '[]'::jsonb;
    BEGIN
      SELECT * INTO run FROM public.draft_runs d WHERE d.org_id=NEW.org_id AND d.id=NEW.draft_id;
      SELECT d.xmin::text INTO run_xid FROM public.draft_runs d WHERE d.org_id=NEW.org_id AND d.id=NEW.draft_id;
      IF run_xid IS DISTINCT FROM pg_current_xact_id()::text THEN
        RAISE EXCEPTION 'Historical draft snapshot is closed' USING ERRCODE='42501';
      END IF;
      SELECT * INTO requirement FROM public.requirements WHERE org_id=NEW.org_id AND id=NEW.requirement_id;
      IF requirement.id IS NULL OR requirement.task_id IS DISTINCT FROM run.task_id
        OR requirement.job_id IS DISTINCT FROM run.extraction_job_id
        OR NEW.category IS DISTINCT FROM requirement.category
        OR NEW.starred IS DISTINCT FROM requirement.starred
        OR NEW.source->>'quote' IS DISTINCT FROM requirement.quote
        OR NEW.source IS DISTINCT FROM jsonb_build_object('document_id',requirement.document_id,
            'chunk_id',requirement.chunk_id,'page',requirement.page,'location',requirement.location,
            'quote',requirement.quote) THEN
        RAISE EXCEPTION 'Response item source binding invalid' USING ERRCODE='23514';
      END IF;
      IF NEW.card_id IS NOT NULL THEN
        SELECT * INTO card FROM public.response_cards WHERE org_id=NEW.org_id AND id=NEW.card_id;
        SELECT * INTO revision FROM public.response_card_revisions WHERE org_id=NEW.org_id AND id=NEW.card_revision_id;
        IF card.requirement_id IS DISTINCT FROM NEW.requirement_id OR revision.card_id IS DISTINCT FROM card.id
          OR card.current_revision_id IS DISTINCT FROM revision.id THEN
          RAISE EXCEPTION 'Response item card binding invalid' USING ERRCODE='23514';
        END IF;
      END IF;
      citation_ok := public.response_citation_valid(NEW.org_id,NEW.requirement_id);
      material_ok := NOT EXISTS (SELECT 1 FROM public.card_evidence_links l WHERE l.org_id=NEW.org_id
        AND l.revision_id=NEW.card_revision_id AND public.response_evidence_active(l.org_id,l.evidence_id) IS DISTINCT FROM true);
      quote_current := public.response_quote_current(NEW.org_id,NEW.card_revision_id);
      IF NEW.kind IN ('row','comply_only') AND
        (citation_ok IS DISTINCT FROM true OR quote_current IS DISTINCT FROM true) THEN
        RAISE EXCEPTION 'Response citation requires renewed review' USING ERRCODE='23514';
      END IF;
      IF NEW.kind='gap' THEN
        IF citation_ok AND quote_current AND (revision.disposition='comply_only' OR
          (revision.state='confirmed' AND revision.review_domain IS NOT NULL AND material_ok)) THEN
          RAISE EXCEPTION 'Eligible response cannot be hidden as gap' USING ERRCODE='23514';
        END IF;
        IF NEW.card_id IS NULL THEN expected_reasons := expected_reasons || '["missing_card"]'::jsonb;
        ELSE
          IF revision.state<>'confirmed' THEN expected_reasons := expected_reasons ||
            jsonb_build_array(CASE WHEN revision.state IN ('rejected','needs_material') THEN revision.state ELSE 'unconfirmed' END); END IF;
          IF revision.review_domain IS NULL THEN expected_reasons := expected_reasons || '["unclassified"]'::jsonb; END IF;
          IF NOT material_ok THEN expected_reasons := expected_reasons || '["stale_material"]'::jsonb; END IF;
          IF citation_ok AND quote_current IS DISTINCT FROM true THEN
            expected_reasons := expected_reasons || '["needs_reconfirmation"]'::jsonb;
          END IF;
        END IF;
        IF citation_ok IS DISTINCT FROM true THEN expected_reasons := expected_reasons || '["invalid_citation"]'::jsonb; END IF;
        IF NEW.gap_reasons IS DISTINCT FROM expected_reasons THEN
          RAISE EXCEPTION 'Gap reasons must reflect current inputs' USING ERRCODE='23514';
        END IF;
      END IF;
      IF NEW.kind='row' THEN
        target_table := CASE WHEN requirement.starred OR requirement.category='substantive' THEN 'substantive' ELSE revision.review_domain END;
        IF revision.state IS DISTINCT FROM 'confirmed' OR revision.disposition IS DISTINCT FROM 'respond'
          OR NEW."table" IS DISTINCT FROM target_table
          OR ROW(NEW.response_kind,NEW.response_text,NEW.deviation,NEW.deviation_note)
            IS DISTINCT FROM ROW(revision.response_kind,revision.response_text,revision.deviation,revision.deviation_note) THEN
          RAISE EXCEPTION 'Response row is not confirmed content' USING ERRCODE='23514';
        END IF;
      ELSIF NEW.kind='comply_only' THEN
        IF revision.disposition IS DISTINCT FROM 'comply_only' OR revision.review_domain IS NULL
          OR ROW(NEW.disposition_by,NEW.disposition_at) IS DISTINCT FROM ROW(revision.disposition_by,revision.disposition_at) THEN
          RAISE EXCEPTION 'Comply-only item lacks human disposition' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    """)


def downgrade():
    raise RuntimeError(
        "Keep original model quotes and review history; rollback is application-only"
    )
