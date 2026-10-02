"""Prepaid balances, recharge cards and a configurable billing currency."""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

NEW_FUNCTIONS = (
    "platform_org_summaries()",
    "platform_usage_summary(date, date)",
    "redeem_card(text, uuid, uuid, text)",
    "platform_adjust_balance(uuid, text, numeric, text, text, text)",
    "user_org_memberships(uuid)",
)


CREATE_ORG = """
CREATE OR REPLACE FUNCTION platform_create_org(p_name text, p_admin_email text, p_password_hash text)
RETURNS TABLE (new_org_id uuid, admin_user_id uuid, user_created boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
  v_org uuid := gen_random_uuid();
  v_email text := lower(btrim(p_admin_email));
  v_user uuid;
  v_created boolean := false;
  v_previous text := coalesce(current_setting('app.current_org', true), '');
BEGIN
  IF length(btrim(p_name)) = 0 OR length(p_name) > 200 OR v_email !~ '^[^@\\s]+@[^@\\s]+$' THEN
    RAISE EXCEPTION 'invalid organization input' USING ERRCODE = 'check_violation';
  END IF;
  SELECT u.id INTO v_user FROM users u WHERE u.email = v_email;
  IF v_user IS NULL THEN
    v_user := gen_random_uuid();
    INSERT INTO users (id, email, password_hash, active) VALUES (v_user, v_email, p_password_hash, true);
    v_created := true;
  END IF;
  PERFORM set_config('app.current_org', v_org::text, true);
  INSERT INTO orgs (id, org_id, name, active) VALUES (v_org, v_org, btrim(p_name), true);
  INSERT INTO memberships (id, org_id, user_id, role, active)
    VALUES (gen_random_uuid(), v_org, v_user, 'admin', true);
  PERFORM set_config('app.current_org', v_previous, true);
  RETURN QUERY SELECT v_org, v_user, v_created;
END
$$
"""

SET_ACTIVE = """
CREATE OR REPLACE FUNCTION platform_set_org_active(p_org uuid, p_active boolean)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
  v_count integer;
  v_previous text := coalesce(current_setting('app.current_org', true), '');
BEGIN
  PERFORM set_config('app.current_org', p_org::text, true);
  UPDATE orgs SET active = p_active WHERE id = p_org;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  PERFORM set_config('app.current_org', v_previous, true);
  RETURN v_count = 1;
END
$$
"""


def upgrade():
    # Sale prices and charges are in the configured billing currency, not always USD.
    op.execute("ALTER TABLE usage_records RENAME COLUMN charge_usd TO charge")
    op.execute(
        "ALTER TABLE platform_models RENAME COLUMN sale_input_usd_per_mtok TO sale_input_per_mtok"
    )
    op.execute(
        "ALTER TABLE platform_models RENAME COLUMN sale_output_usd_per_mtok TO sale_output_per_mtok"
    )
    op.execute(
        "ALTER TABLE api_tokens DROP CONSTRAINT token_forbidden_scopes, ADD CONSTRAINT token_forbidden_scopes "
        "CHECK (NOT (scopes ? 'evidence:confirm') AND NOT (scopes ? 'export') AND NOT (scopes ? 'billing:redeem'))"
    )

    op.execute(
        """
        CREATE TABLE org_balances (
          org_id uuid PRIMARY KEY REFERENCES orgs (id),
          currency char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
          balance numeric(18, 8) NOT NULL DEFAULT 0,
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE balance_entries (
          id uuid PRIMARY KEY,
          org_id uuid NOT NULL REFERENCES orgs (id),
          created_at timestamptz NOT NULL DEFAULT now(),
          kind varchar(10) NOT NULL CHECK (kind IN ('redeem', 'adjust', 'usage')),
          currency char(3) NOT NULL,
          amount numeric(18, 8) NOT NULL CHECK (amount <> 0),
          balance_after numeric(18, 8) NOT NULL,
          card_id uuid,
          usage_record_id uuid,
          actor varchar(254) NOT NULL,
          reason varchar(500),
          UNIQUE (org_id, id),
          FOREIGN KEY (org_id, usage_record_id) REFERENCES usage_records (org_id, id),
          CONSTRAINT entry_reference CHECK (
            (kind = 'redeem' AND card_id IS NOT NULL AND amount > 0)
            OR (kind = 'usage' AND usage_record_id IS NOT NULL AND amount < 0)
            OR (kind = 'adjust' AND length(btrim(coalesce(reason, ''))) > 0)
          )
        )
        """
    )
    op.execute("CREATE INDEX balance_entries_org_time ON balance_entries (org_id, created_at DESC)")
    policy = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"
    for table in ("org_balances", "balance_entries"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_scope ON {table} USING ({policy}) WITH CHECK ({policy})")
    op.execute("GRANT SELECT, INSERT ON org_balances, balance_entries TO bid_app")
    op.execute("GRANT UPDATE (balance, updated_at) ON org_balances TO bid_app")

    # Global like platform_models: a card belongs to no org until it is redeemed.
    op.execute(
        """
        CREATE TABLE platform_cards (
          id uuid PRIMARY KEY,
          code_hash char(64) NOT NULL UNIQUE,
          last4 char(4) NOT NULL,
          face_value numeric(18, 8) NOT NULL CHECK (face_value > 0),
          currency char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
          batch_id uuid NOT NULL,
          note varchar(200),
          expires_at timestamptz,
          status varchar(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'redeemed', 'void')),
          created_by varchar(254) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          redeemed_org_id uuid REFERENCES orgs (id),
          redeemed_by uuid REFERENCES users (id),
          redeemed_at timestamptz,
          CONSTRAINT card_redemption CHECK (
            (status = 'redeemed') = (redeemed_at IS NOT NULL AND redeemed_org_id IS NOT NULL)
          )
        )
        """
    )
    op.execute("CREATE INDEX platform_cards_batch ON platform_cards (batch_id, status)")
    op.execute(
        """
        CREATE FUNCTION platform_cards_final_status() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          -- Only an active card can change; redeemed and void are final.
          IF OLD.status <> 'active' THEN
            RAISE EXCEPTION 'card status is final' USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER platform_cards_final BEFORE UPDATE ON platform_cards "
        "FOR EACH ROW EXECUTE FUNCTION platform_cards_final_status()"
    )
    # bid_app may create and void cards; only redeem_card can mark one redeemed.
    op.execute("GRANT SELECT, INSERT ON platform_cards TO bid_app")
    op.execute("GRANT UPDATE (status) ON platform_cards TO bid_app")

    op.execute("GRANT SELECT, UPDATE ON platform_cards TO bid_platform_fn")
    op.execute("GRANT SELECT, INSERT, UPDATE ON org_balances TO bid_platform_fn")
    op.execute("GRANT INSERT ON balance_entries TO bid_platform_fn")
    op.execute(
        "CREATE POLICY platform_read ON org_balances FOR SELECT TO bid_platform_fn USING (true)"
    )

    op.execute("DROP FUNCTION platform_org_summaries()")
    op.execute("DROP FUNCTION platform_usage_summary(date, date)")
    op.execute(
        """
        CREATE FUNCTION platform_org_summaries()
        RETURNS TABLE (id uuid, name text, active boolean, created_at timestamptz,
                       member_count bigint, admin_emails text[], currency text, balance numeric)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
          SELECT o.id, o.name::text, o.active, o.created_at,
                 count(m.id) FILTER (WHERE m.active),
                 coalesce(array_agg(u.email::text ORDER BY u.email)
                          FILTER (WHERE m.active AND m.role = 'admin'), '{}'),
                 b.currency::text, coalesce(b.balance, 0)
          FROM orgs o
          LEFT JOIN memberships m ON m.org_id = o.id
          LEFT JOIN users u ON u.id = m.user_id
          LEFT JOIN org_balances b ON b.org_id = o.id
          GROUP BY o.id, b.currency, b.balance
          ORDER BY o.created_at, o.id
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform_usage_summary(from_month date, to_month date)
        RETURNS TABLE (org_id uuid, month date, billing text, provider text, model text,
                       calls bigint, tokens bigint, input_tokens bigint, output_tokens bigint,
                       ocr_pages bigint, vendor_usd numeric, unpriced_calls bigint,
                       charge numeric)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
          SELECT r.org_id,
                 date_trunc('month', r.created_at AT TIME ZONE 'UTC')::date,
                 CASE WHEN r.platform_model_id IS NULL THEN 'unbilled' ELSE 'platform' END,
                 r.provider::text, r.model::text, count(*),
                 sum(r.tokens), sum(r.input_tokens), sum(r.output_tokens), sum(r.ocr_pages),
                 coalesce(sum(r.usd), 0), count(*) FILTER (WHERE r.usd IS NULL),
                 coalesce(sum(r.charge), 0)
          FROM usage_records r
          WHERE NOT r.test_only
            AND r.created_at >= (date_trunc('month', from_month)::timestamp AT TIME ZONE 'UTC')
            AND r.created_at < ((date_trunc('month', to_month) + interval '1 month')::timestamp
                                AT TIME ZONE 'UTC')
          GROUP BY 1, 2, 3, 4, 5
          ORDER BY 2, 1, 3, 4, 5
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION redeem_card(p_hash text, p_org uuid, p_user uuid, p_currency text)
        RETURNS TABLE (card_id uuid, amount numeric, balance numeric)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
          v_card platform_cards%ROWTYPE;
          v_balance org_balances%ROWTYPE;
          v_after numeric;
          v_previous text := coalesce(current_setting('app.current_org', true), '');
        BEGIN
          PERFORM set_config('app.current_org', p_org::text, true);
          -- Lock the balance before the card so concurrent redemptions queue per org.
          SELECT * INTO v_balance FROM org_balances b WHERE b.org_id = p_org FOR UPDATE;
          SELECT * INTO v_card FROM platform_cards c WHERE c.code_hash = p_hash FOR UPDATE;
          -- Every rejection returns no row so callers cannot tell the reasons apart.
          IF NOT FOUND OR v_card.status <> 'active' OR v_card.currency <> p_currency
             OR (v_card.expires_at IS NOT NULL AND v_card.expires_at <= now())
             OR (v_balance.org_id IS NOT NULL AND v_balance.currency <> p_currency) THEN
            PERFORM set_config('app.current_org', v_previous, true);
            RETURN;
          END IF;
          UPDATE platform_cards c SET status = 'redeemed', redeemed_org_id = p_org,
                 redeemed_by = p_user, redeemed_at = now()
           WHERE c.id = v_card.id;
          INSERT INTO org_balances AS b (org_id, currency, balance)
            VALUES (p_org, p_currency, v_card.face_value)
            ON CONFLICT (org_id) DO UPDATE SET balance = b.balance + EXCLUDED.balance, updated_at = now()
            RETURNING b.balance INTO v_after;
          INSERT INTO balance_entries (id, org_id, kind, currency, amount, balance_after, card_id, actor)
            VALUES (gen_random_uuid(), p_org, 'redeem', p_currency, v_card.face_value, v_after,
                    v_card.id, p_user::text);
          PERFORM set_config('app.current_org', v_previous, true);
          RETURN QUERY SELECT v_card.id, v_card.face_value, v_after;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform_adjust_balance(p_org uuid, p_mode text, p_amount numeric,
                                                p_reason text, p_actor text, p_currency text)
        RETURNS TABLE (delta numeric, balance numeric)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
          v_current numeric;
          v_currency text;
          v_delta numeric;
          v_previous text := coalesce(current_setting('app.current_org', true), '');
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM orgs o WHERE o.id = p_org) THEN
            RETURN;
          END IF;
          IF p_mode NOT IN ('add', 'set') OR length(btrim(coalesce(p_reason, ''))) = 0 THEN
            RAISE EXCEPTION 'invalid adjustment' USING ERRCODE = 'check_violation';
          END IF;
          PERFORM set_config('app.current_org', p_org::text, true);
          INSERT INTO org_balances (org_id, currency, balance) VALUES (p_org, p_currency, 0)
            ON CONFLICT (org_id) DO NOTHING;
          SELECT b.balance, b.currency INTO v_current, v_currency
            FROM org_balances b WHERE b.org_id = p_org FOR UPDATE;
          IF v_currency <> p_currency THEN
            RAISE EXCEPTION 'balance currency differs' USING ERRCODE = 'check_violation';
          END IF;
          v_delta := CASE WHEN p_mode = 'set' THEN p_amount - v_current ELSE p_amount END;
          IF v_delta <> 0 THEN
            UPDATE org_balances b SET balance = b.balance + v_delta, updated_at = now()
             WHERE b.org_id = p_org;
            INSERT INTO balance_entries (id, org_id, kind, currency, amount, balance_after, actor, reason)
              VALUES (gen_random_uuid(), p_org, 'adjust', p_currency, v_delta, v_current + v_delta,
                      p_actor, btrim(p_reason));
          END IF;
          PERFORM set_config('app.current_org', v_previous, true);
          RETURN QUERY SELECT v_delta, v_current + v_delta;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION user_org_memberships(p_user uuid)
        RETURNS TABLE (org_id uuid, name text, role text, org_active boolean)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
          SELECT o.id, o.name::text, m.role::text, o.active
          FROM memberships m JOIN orgs o ON o.id = m.org_id
          WHERE m.user_id = p_user AND m.active
          ORDER BY o.name, o.id
        $$
        """
    )
    # The 0010 functions also restore, rather than clear, the caller's org context.
    for body in (CREATE_ORG, SET_ACTIVE):
        op.execute(body)
    op.execute("GRANT CREATE ON SCHEMA public TO bid_platform_fn")
    for function in NEW_FUNCTIONS:
        op.execute(f"ALTER FUNCTION {function} OWNER TO bid_platform_fn")
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO bid_app")
    op.execute("REVOKE CREATE ON SCHEMA public FROM bid_platform_fn")


def downgrade():
    raise RuntimeError("Data-preserving rollback required; retain balances, ledger and cards")
