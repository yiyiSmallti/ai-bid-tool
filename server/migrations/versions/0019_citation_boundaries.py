"""Match Python citation spans and boundaries in confirmation and draft gates."""

import unicodedata

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    # PostgreSQL has NFKC but no equivalent of Python's combining(). Freeze the
    # nonzero combining characters from the application's Unicode database in
    # the function, rather than approximating them with a Unicode category.
    combining = "".join(chr(code) for code in range(0x110000) if unicodedata.combining(chr(code)))
    op.execute(
        r"""
        CREATE FUNCTION response_locate_quote(source_text text, quote text) RETURNS text
        LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE SET search_path=pg_catalog AS $$
        DECLARE
          combining_chars constant text := '__COMBINING__';
          separators constant text := U&'\FF1B;\FF0C,\3002\FF1A:\3001\FF01!\FF1F?\FF08\FF09()\3010\3011[]\300A\300B\201C\201D\2018\2019\0022\0027\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000';
          needle text := public.response_normalize_quote(quote);
          haystack text := '';
          starts integer[] := '{}'; ends integer[] := '{}';
          source_length integer := length(source_text);
          unit_start integer := 1; idx integer; part_index integer;
          previous text; character text; part text;
          search_from integer := 1; relative_match integer; match_start integer;
          span_start integer; span_end integer; candidate text;
          matches integer := 0; bounded_matches integer := 0;
          chosen text; bounded_chosen text;
        BEGIN
          IF length(needle)=0 OR source_length=0 THEN RETURN NULL; END IF;
          -- Mirror normalized_spans: retain whole combining/composition units,
          -- including Hangul and compatibility expansions, with original offsets.
          FOR idx IN 2..source_length+1 LOOP
            previous := substring(source_text from unit_start for idx-unit_start);
            character := substring(source_text from idx for 1);
            IF idx<=source_length AND
              (position(character in combining_chars)>0 OR
               normalize(previous || character,NFKC) <>
                 normalize(previous,NFKC) || normalize(character,NFKC)) THEN
              CONTINUE;
            END IF;
            part := public.response_normalize_quote(previous);
            haystack := haystack || part;
            FOR part_index IN 1..length(part) LOOP
              starts := array_append(starts,unit_start);
              ends := array_append(ends,idx-1);
            END LOOP;
            unit_start := idx;
          END LOOP;
          LOOP
            relative_match := position(needle in substring(haystack from search_from));
            EXIT WHEN relative_match=0;
            match_start := search_from+relative_match-1;
            span_start := starts[match_start];
            span_end := ends[match_start+length(needle)-1];
            candidate := substring(source_text from span_start for span_end-span_start+1);
            -- An inner match in a compatibility expansion is not an original span.
            IF public.response_normalize_quote(candidate)=needle THEN
              matches := matches+1;
              chosen := candidate;
              IF (span_start=1 OR
                    position(substring(source_text from span_start-1 for 1) in separators)>0)
                AND (span_end=source_length OR
                    position(substring(source_text from span_end+1 for 1) in separators)>0) THEN
                bounded_matches := bounded_matches+1;
                bounded_chosen := candidate;
              END IF;
            END IF;
            search_from := match_start+1;
          END LOOP;
          IF matches=1 THEN RETURN chosen; END IF;
          IF matches>1 AND bounded_matches=1 THEN RETURN bounded_chosen; END IF;
          RETURN NULL;
        END $$;
        """.replace("__COMBINING__", combining.replace("'", "''"))
    )
    op.execute("""
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
          )
          SELECT length(quote)>0 AND position(quote in source_text)>0
            AND public.response_locate_quote(source_text,quote) IS NOT NULL
          FROM located
        $$;
    """)


def downgrade():
    raise RuntimeError("Keep reviewed citation semantics; rollback is application-only")
