"""Stored context summaries for long agent sessions.

A summary is an append-only agent message that records the message ordinals it
replaces in later decision requests; the originals stay stored. The summary
call itself is a counted step of its own kind so its ledger and recovery match
a decision's.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
ALTER TABLE public.agent_steps DROP CONSTRAINT agent_step_state;
ALTER TABLE public.agent_steps ADD CONSTRAINT agent_step_state CHECK (((ordinal >= 1) AND (revision >= 1) AND ((kind)::text = ANY ((ARRAY['decision'::character varying, 'tool'::character varying, 'summary'::character varying])::text[])) AND ((state)::text = ANY ((ARRAY['planned'::character varying, 'submitted'::character varying, 'waiting_job'::character varying, 'completed'::character varying, 'failed'::character varying, 'uncertain'::character varying])::text[]))));

ALTER TABLE public.agent_messages
    ADD COLUMN covers_from integer,
    ADD COLUMN covers_to integer;
ALTER TABLE public.agent_messages DROP CONSTRAINT agent_message_shape;
ALTER TABLE public.agent_messages ADD CONSTRAINT agent_message_shape CHECK (((ordinal >= 1) AND ((role)::text = ANY ((ARRAY['human'::character varying, 'assistant'::character varying, 'tool'::character varying, 'system'::character varying, 'summary'::character varying])::text[])) AND (length(content) <= 8000)));
ALTER TABLE public.agent_messages ADD CONSTRAINT agent_message_summary CHECK ((((role)::text = 'summary'::text) = (covers_from IS NOT NULL)) AND ((covers_from IS NULL) = (covers_to IS NULL)) AND ((covers_from IS NULL) OR ((covers_from >= 1) AND (covers_to >= covers_from) AND (covers_to < ordinal) AND (step_id IS NOT NULL))));
"""
    )


def downgrade():
    raise NotImplementedError("Stored summaries have no earlier representation")
