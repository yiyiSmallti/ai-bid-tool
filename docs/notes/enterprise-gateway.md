# Enterprise gateway

## Problem

The same server runs as a personal installation or as a company gateway
([ADR 0013](../adr/0013-enterprise-gateway.md)). A gateway started on a personal
database, or the reverse, would apply the wrong authentication and visibility
rules to existing data, so the mode a database was initialized with is stored
and checked before either process serves anything. On a gateway every person
signs in as a member, and every human decision records which member made it.

## Usage

- `BID_DEPLOYMENT` selects `personal` (default) or `enterprise`. Enterprise mode
  also needs `BID_PUBLIC_URL` (an `https://` origin) at startup and
  `BID_COMPANY_NAME` for `python -m app.admin init-db`. The other enterprise
  settings and their ranges are in
  [the gateway plan](../plan/enterprise-gateway.md#deployment-mode) and
  `Settings` in [config.py](../../server/app/core/config.py).
- `init-db` records the mode on first initialization. Run again with the same
  mode it is idempotent; with the other mode it exits 4 with
  `deployment_mode_mismatch` before migrating anything.
- The API and worker exit with `deployment_mode_mismatch` (API exit status 3,
  worker 4) when the configured mode differs from the stored one.
- A gateway refuses the access key over HTTP with `access_key_not_accepted`,
  both as a bearer and at `POST /auth/session`; it serves the gateway host's
  admin command line only.
- The first administrator is created on the gateway host with
  `python -m app.admin member create --role admin --login <login>`. It prints
  the one-time password to that terminal only.
- Members sign in with `POST /auth/session` and `{"login", "password"}`. An
  initial or reset password must be changed with `POST /account/password`
  before any other route; until then the session reaches only
  `GET /auth/session`, `DELETE /auth/session` and the password route.
- Administrators manage members over `/members` (create, update, password
  reset, session revocation). The CLI does the same with
  `bid member list|create|update|reset-password|revoke-sessions` and an
  administrator's `bid_` token holding `member:manage`; a one-time password
  goes to the new owner-only file named by `--output`.
- `GET /installation` (and `bid installation show`) is unauthenticated and
  returns the mode, company name, server version and contract version.

## How it works

- Migration `0004` adds the singleton `installation` table: `id` is always
  `true`, `mode` is `personal` or `enterprise`, and `company_name` is set exactly
  in enterprise mode. A trigger rejects every update and delete, including the
  owner's; the runtime role may only read it.
- A database without the row is personal: databases created before `0004`, and
  test databases after truncation. `init-db` treats a database with migration
  revisions as existing and writes `personal` for it; only a database without
  revisions takes the configured mode.
- `Database.verify_role` reads the stored mode after the role checks, so the
  API lifespan and the worker share one check
  ([deployment.py](../../server/app/core/deployment.py)).
- Migration `0005` adds `members`, `member_grants`, `member_sessions` and
  `login_attempts`. A trigger refuses deleting members and any update that
  leaves no active administrator (`last_admin`), serialized by an advisory
  lock.
- A member session token carries `sid` and `mid`. `authenticate` reloads the
  session and member on every request and refuses a revoked, expired, idle or
  disabled one, so a password change (other sessions), reset, disable or
  revocation takes effect on the next request. `last_seen_at` is written at
  most once a minute.
- `bid_` tokens and agent principals belong to the member who created them.
  Their scopes are intersected with the member's current role scopes, and they
  fail once the member is disabled; an agent step then ends with
  `agent_authority_expired`. Agents never hold `member:manage`; a token may hold
  it only when bound to a member (`token_forbidden_member_scope`).
- Sign-in runs in `PasswordAttempts`
  ([password_attempts.py](../../server/app/core/password_attempts.py)): under
  advisory locks on the source and the login, five failures per login or 30 per
  source within 15 minutes reject the next attempt with `too_many_attempts`
  before any password check. Unknown and disabled logins verify against a dummy
  hash, so every refusal is the same `invalid_login`. PBKDF2 runs on a bounded
  thread pool; a full queue answers `auth_busy`.
- Each request sets `app.actor_member_id` beside `app.actor_kind`. Every table
  whose insert trigger checks for a human actor has `actor_member_id`, filled by
  the `actor_member_attribution` trigger; on a gateway it rejects a human write
  without a member. `0005` finds these tables with `DECISION_TABLES`, and
  [test_members.py](../../server/tests/test_members.py) reruns that query, so a
  later decision table without the column fails there.

## Pitfalls

- If a first enterprise `init-db` fails after migrating and before writing the
  row, the database counts as an existing personal one and later enterprise
  runs refuse it. Drop and recreate that empty database.
- `env_ignore_empty` turns empty settings into defaults, except
  `BID_PERSONAL_MODEL_ENDPOINTS`, where an empty value means no personal models.
- The mode never changes in place; moving a project between modes uses
  [packages](../plan/enterprise-gateway.md#packages).
- Member grants are stored but not yet enforced; until project visibility
  ships, a member holds every human scope except the administrator-only ones
  (`ADMIN_ONLY_SCOPES` in [auth.py](../../server/app/services/auth.py)).
- The limiter keys on the client address the API sees. Behind a reverse proxy
  that is the proxy, so the 30-per-source limit then applies to everyone.

## Code

- [0004_installation_mode.py](../../server/migrations/versions/0004_installation_mode.py)
- [0005_members.py](../../server/migrations/versions/0005_members.py)
- [members.py](../../server/app/services/members.py) service,
  [members.py](../../server/app/api/members.py) routes,
  [members.py](../../cli/bid_cli/members.py) CLI
- [admin.py](../../server/app/admin.py) `initialize_database`, `installation_mode`
- [db.py](../../server/app/core/db.py) `Database.verify_role`
- [auth.py](../../server/app/services/auth.py) `authenticate`, `access_key_refused`,
  `member_session_identity`, `agent_identity`
- [test_deployment_mode.py](../../server/tests/test_deployment_mode.py),
  [test_members.py](../../server/tests/test_members.py)
