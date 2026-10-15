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
- A project is visible to administrators and its members only; anyone else
  gets `not_found` for the project and everything in it, the same answer as for
  a random identifier. The member who creates a project owns it.
- `GET /tasks/{id}/members` lists a project's members. The owner or an
  administrator adds members (`POST /tasks/{id}/members`), removes them
  (`DELETE /tasks/{id}/members/{member_id}`) and hands the project to another
  member (`POST /tasks/{id}/owner`); the owner cannot be removed
  (`owner_not_removable`). An administrator sets
  `PATCH /tasks/{id}/sharing` (`export_to_personal_allowed`). The CLI does the
  same with `bid task member list|add|remove`, `bid task owner` and
  `bid task sharing` and a `bid_` token holding `task:create`.
- Grants open the shared library: `library:edit` (materials, certificates,
  profiles, templates, attachments), `memory:edit` (memory writes and review)
  and `confidential:reveal`. A member without a grant reads but does not write.

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
- Migration `0006` adds `task_members` with one `owner` row per project
  (`task_single_owner`) and `tasks.export_to_personal_allowed`. On a gateway
  the deferred `task_owner_required` trigger rejects, at commit, a project
  without an owner; a hand-over demotes the old owner before promoting the new
  one. `task_access.seed` adds the creator as owner.
- `visible_task` ([task_access.py](../../server/app/services/task_access.py))
  loads every task with `visibility(actor)`, an `EXISTS` on `task_members` for
  a non-administrator member, and `access` and the task list go through it.
  Worker steps skip the check, so a job admitted before a removal finishes.
  Agent sessions resolve their task the same way (`session_record` in
  [agents.py](../../server/app/services/agents.py)).
- `member_scopes(role, grants)` in
  [auth.py](../../server/app/services/auth.py) removes the scopes of each
  `GRANTED_SCOPES` entry the member lacks, for sessions, tokens and agents.
- Every agent session wakes at least every 30 seconds, paused ones too. A wake
  whose member was disabled or, unless an administrator, removed from the
  project cancels the session and its own jobs and audits
  `agent.session.cancelled` with `member_removed`
  (`agents.member_removed`, `wake` in
  [agent.py](../../server/app/jobs/agent.py)).

## Pitfalls

- If a first enterprise `init-db` fails after migrating and before writing the
  row, the database counts as an existing personal one and later enterprise
  runs refuse it. Drop and recreate that empty database.
- `env_ignore_empty` turns empty settings into defaults, except
  `BID_PERSONAL_MODEL_ENDPOINTS`, where an empty value means no personal models.
- The mode never changes in place; moving a project between modes uses
  [packages](../plan/enterprise-gateway.md#packages).
- Memory has no `private` scope yet, so all memory is public and needs
  `memory:edit` to change.
- `export_to_personal_allowed` is stored for [packages](../plan/enterprise-gateway.md#packages);
  nothing reads it yet.
- An agent of a removed member can run until its next wake, up to 30 seconds.
- The limiter keys on the client address the API sees. Behind a reverse proxy
  that is the proxy, so the 30-per-source limit then applies to everyone.

## Code

- [0004_installation_mode.py](../../server/migrations/versions/0004_installation_mode.py)
- [0005_members.py](../../server/migrations/versions/0005_members.py)
- [0006_project_members.py](../../server/migrations/versions/0006_project_members.py)
- [project_members.py](../../server/app/services/project_members.py) service,
  [project_members.py](../../server/app/api/project_members.py) routes
- [members.py](../../server/app/services/members.py) service,
  [members.py](../../server/app/api/members.py) routes,
  [members.py](../../cli/bid_cli/members.py) CLI
- [admin.py](../../server/app/admin.py) `initialize_database`, `installation_mode`
- [db.py](../../server/app/core/db.py) `Database.verify_role`
- [auth.py](../../server/app/services/auth.py) `authenticate`, `access_key_refused`,
  `member_session_identity`, `agent_identity`
- [test_deployment_mode.py](../../server/tests/test_deployment_mode.py),
  [test_members.py](../../server/tests/test_members.py),
  [test_project_visibility.py](../../server/tests/test_project_visibility.py)
