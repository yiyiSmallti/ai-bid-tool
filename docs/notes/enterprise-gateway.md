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
  `PATCH /tasks/{id}/sharing` (`export_to_personal_allowed`), which
  `GET /tasks/{id}` returns. The CLI does the
  same with `bid task member list|add|remove`, `bid task owner` and
  `bid task sharing` and a `bid_` token holding `task:create`.
- Grants open the shared library: `library:edit` (materials, certificates,
  profiles, templates, attachments), `memory:edit` (public memory writes and
  review) and `confidential:reveal`. A member without a grant reads but does
  not write.
- Memory written with `"target": {"scope": "private"}` (`bid memory add`)
  belongs to the member who wrote it; `bid memory list --scope private` and
  `bid memory browse` with `"scope": "private"` list it.
  Only that member sees, approves and changes it, without `memory:edit`;
  anyone else, administrators included, gets `not_found`. Retrieval with
  `"scopes": ["library", "private"]` and a member's drafting read public memory
  plus the member's own private memory. A personal installation refuses the
  scope with `memory_scope_unavailable`.
- On a gateway only an administrator saves, tests, removes or picks the
  default among company models. A member saves a personal model with
  `"owner": "personal"` in `POST /providers` from a signed-in session (a `bid_`
  token never holds `provider:write`), with their own key: a key is reused only from a model of the same owner, so a personal
  model on a company endpoint without a key fails with
  `provider_key_required`. A personal endpoint must be an origin on
  `BID_PERSONAL_MODEL_ENDPOINTS` (`provider_endpoint_not_allowed`); a personal
  installation refuses the owner with `provider_owner_invalid`.
- `GET /providers` lists the company models plus the caller's own personal
  models; to anyone else, administrators included, a personal model is
  `provider_model_unavailable`. `PUT /account/default-model` with `{"slot"}`
  (or `null`) sets the model a member's runs use when none is named; a default
  that is no longer visible falls back to the company default.
- `PUT /members/{id}/quota` with `{"monthly_company_tokens"}` (`bid member
  quota --tokens N | --clear`) caps a member's company-model tokens per UTC
  calendar month; a job whose next call would pass the cap fails with
  `member_quota_exceeded`. Personal models are never capped.
  `GET /members/{id}/usage?month=YYYY-MM` (`bid member usage`) returns calls
  and tokens by owner and model to the member and to `member:manage`, which
  sees another member's personal models without their slot, provider or model.
- `POST /packages/export` with `{"task_id"}` (`bid package export --task`)
  starts a `package_export` job for a project the caller can read with
  `package:export`; on a gateway the project must have
  `export_to_personal_allowed` (`package_export_not_allowed`).
  `GET /packages/{job_id}` (`bid package status`) shows the job and its
  report, and `GET /packages/{job_id}/file` (`bid package download --output`)
  returns the zip to the member who started it, checking the setting and the
  stored hash again. The CLI saves it only after its size and sha256 match the
  report.
- `POST /packages/import` with a multipart `file` (`bid package import
  --file`) needs `package:import` and starts a `package_import` job; `bid
  package status` shows its report: the task, row counts per table, files,
  reused library items and `needs_value`, the confidential keys the project
  names that this installation has no value for. A file or table hash that
  does not match the manifest is refused at once (`package_integrity_failed`);
  `package_task_exists`, `package_row_conflict`, `package_library_forbidden`,
  `package_citation_unresolved` and `package_reference_unresolved` fail the
  job with nothing written. A repeat of the same package by the same member
  returns the first import unless it failed. The imported project starts with
  `export_to_personal_allowed` cleared, so the receiving administrator decides
  whether it may leave again.

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
- Migration `0007` adds `memories.owner_member_id`, set exactly for
  `private` memory, and keys `memory_scope_epochs` by scope and owner, so a
  private change advances only its owner's epoch. Its triggers require the
  owner to be `app.actor_member_id` for a new private memory or revision,
  check approval conflicts within one owner, and require a manifest's epochs
  to be `library` or `library` plus the reading member's `private`.
  `memory.access` ([access.py](../../server/app/memory/access.py)) holds the
  read predicate (`readable`, `visible`) and the public-memory grant check
  (`changeable`); `memory:edit` no longer removes `memory:write`,
  `memory:approve` or `memory:manage`, so members keep them for private memory.
- Migration `0008` adds `provider_slots.owner_member_id` (null for a company
  model), `member_settings` (the default model) and `member_quotas`. Its
  `provider_slot_writable` check, run for slot and revision inserts and slot
  updates, requires the owner to be `app.actor_member_id` for a personal model
  and an administrator for a company model on a gateway; only a company model
  can be the default (`provider_slot_default_company`). `hidden_slots` in
  [configured.py](../../server/app/providers/configured.py) is the visibility
  predicate, and `effective_default` resolves a run without a model.
- Admission (`JobExecution.member_quota` in
  [execution.py](../../server/app/jobs/execution.py)) takes a per-member
  advisory lock and admits a company-model call only when used tokens, the
  upper bounds of the member's unsettled calls and this call's upper bound fit
  the cap, so parallel jobs never overrun it. Admission and receipt set
  `app.actor_member_id` to the job's member, so `vendor_calls.member_id` and
  `usage_records.member_id` attribute every call.
- Every agent session wakes at least every 30 seconds, paused ones too. A wake
  whose member was disabled or, unless an administrator, removed from the
  project cancels the session and its own jobs and audits
  `agent.session.cancelled` with `member_removed`
  (`agents.member_removed`, `wake` in
  [agent.py](../../server/app/jobs/agent.py)).
- The desktop client keeps its workspaces in `workspaces.json` in its user
  data directory, owner-only, with the shape in
  [the gateway plan](../plan/enterprise-gateway.md#desktop-client).
  `WorkspaceRunner` in [workspace_runner.go](../../desktop/workspace_runner.go)
  opens one at a time: the personal workspace starts the local server and
  database, and an enterprise workspace stops them and talks to its gateway
  only. An enterprise session is kept in the login keychain under the service
  `io.github.yiyismallti.aibid.session` and the workspace id; the password is
  never kept.
- `ProbeGateway` in [gateway.go](../../desktop/gateway.go) accepts an `https://`
  origin without path, reads `GET /installation` and refuses a personal
  installation or an unreadable contract version. A certificate the system
  does not trust fails with its SHA-256 fingerprint; once confirmed it is
  stored as `pin_sha256`, and that workspace then accepts that certificate
  only. Opening and signing in check the contract's major version against the
  client's: a newer gateway asks for a newer client, an older one for an
  upgraded gateway.
- The sidebar's workspace switcher in
  [native_workspaces.go](../../desktop/native_workspaces.go) lists every
  workspace and adds a gateway. An enterprise workspace signs in with login and
  password instead of the access key; while `must_change_password` is set the
  password page is shown full screen, since the gateway answers nothing else.
  Its sidebar adds 我的登录 and 修改密码 and ends with 退出登录, which revokes
  the session on the gateway and removes it from the keychain. Any 401 except
  a refused current password on `POST /account/password` returns to sign-in
  and removes the stored session.
- An administrator's sidebar adds 成员与权限
  ([native_members.go](../../desktop/native_members.go)): create a member,
  change name, role, grants, status and the monthly company-model quota,
  reset a password, and sign a member out everywhere. A created or reset
  password is shown once on that page and kept nowhere else. The task sidebar
  of every member adds 项目成员, where the owner or an administrator hands the
  project over and removes members.
- On a gateway the model page
  ([native_settings_models.go](../../desktop/native_settings_models.go))
  splits 公司模型 from 我的个人模型. Only an administrator adds, edits, tests
  or sets the default of a company model; any member saves personal models
  and picks 我的默认 through `PUT /account/default-model`. The memory page has
  公共记忆 and 我的私有记忆 tabs; a member without `memory:edit` cannot add
  public memory. 我的用量 ([native_usage.go](../../desktop/native_usage.go))
  shows the member's monthly usage, and an administrator's 用量 picks any
  member.
- Uploads and downloads go through `APIClient.transfer` in
  [native_client.go](../../desktop/native_client.go): no total timeout, and
  a failure once neither side moves for 60 seconds; every other request keeps
  the 90-second limit. A gateway that cannot be reached, at opening or on any
  page, sets `Offline` instead of an error: the page stays, a banner offers
  重试连接, and retrying checks the contract and session again before
  reloading. Nothing falls back to the local stack.
- The client's package pages are in
  [native_packages.go](../../desktop/native_packages.go). 导入项目包 on the
  task list uploads a zip and follows the job through `GET /packages/{id}`,
  which an import reads before it has a task; the report lists row counts,
  reused library items and the confidential keys to fill in. 导出项目包 on
  the task page shows only while the project may leave the installation:
  always in a personal one, on a gateway while `export_to_personal_allowed`
  is set, which an administrator switches on the project members page. The
  saved zip must match the report's size and sha256 before it is written,
  owner-only, through a temporary file. Package refusals stay on the page with
  their reason instead of closing it as inaccessible.
- An export ([packages.py](../../server/app/services/packages.py)) reads
  the live schema: foreign keys from `pg_constraint` and NOT NULL columns
  decide the row closure from the task and the library items it binds, and
  the import order is a topological sort that skips deferrable and self
  references. A row whose required reference falls outside the package is
  dropped and a nullable one is cleared; both are counted in the manifest's
  `omitted_rows` and `cleared`. Installation-bound columns (members, tokens,
  provider configuration, agents, queue leases) are nulled, agent-initiated
  jobs become `legacy_unknown`, sealed columns are written as plaintext and
  listed in `sealed_columns`, and stored run inputs are replaced by
  `{"omitted": "bid-package/1"}`. Entries use a fixed timestamp and sorted
  order, so the same data gives the same bytes.
- An import ([ADR 0014](../adr/0014-package-import-authority.md)) stores the
  zip, then its job binds a `package_imports` row to its running execution
  and sets `app.package_import_id`; `package_import_task()` returns the task
  only while that row is `importing` and the job holds its lease. Each
  included table's guards admit inserts for that task in migrations `0010` to
  `0015`. The job writes every row with its original id in one transaction
  without savepoints, because guards compare a parent's `xmin` with
  `pg_current_xact_id()`; sealed columns are encrypted with this
  installation's key, and created library rows are touched once so their
  root guard builds the search vector. The importing member becomes the
  project owner on a gateway. Exporting the imported project gives the same
  table hashes as the package.

## Pitfalls

- If a first enterprise `init-db` fails after migrating and before writing the
  row, the database counts as an existing personal one and later enterprise
  runs refuse it. Drop and recreate that empty database.
- `env_ignore_empty` turns empty settings into defaults, except
  `BID_PERSONAL_MODEL_ENDPOINTS`, where an empty value means no personal models.
- The mode never changes in place; moving a project between modes uses
  [packages](../plan/enterprise-gateway.md#packages).
- A saved retrieval that read private memory holds its text, so
  `GET /memory-retrievals/{id}` answers `not_found` to anyone but the owner.
  Job memory references (`GET /jobs/{id}/memory`) and card memory lineage show
  other viewers only the public references.
- A duplicate conflict key across public and private memory omits both from
  retrieval (`same_priority_conflict`); private memory does not shadow public.
- Withdrawing `export_to_personal_allowed` blocks downloading a package that
  was already built; a file already downloaded stays valid.
- Evidence that points at a screenshot asset is left out of a package, with
  its links, until screenshot assets are packaged.
- An imported project must be deleted before the same project can be imported
  again; a copy of a package with another digest fails with
  `package_task_exists`.
- Confidential values never travel; `needs_value` lists what an administrator
  must fill before drafts and exports that use them can finish.
- An agent of a removed member can run until its next wake, up to 30 seconds.
- An admitted call reserves its full upper bound until its receipt, so a
  member near the cap can be refused while earlier calls are still running.
- The client never uses a system proxy for a gateway, so a gateway reachable
  only through one cannot be added.
- A pinned certificate that the gateway renews must be confirmed again.
- An upload counts the wait for the gateway's answer as a stall, so an upload
  route that works for more than 60 seconds after the last byte fails on the
  client.
- Each saved model's `configuration` carries its `owner` in personal
  installations too; the client rejects a model without it.
- An administrator's model page and private-memory tab never show another
  member's personal models or private memory; usage lists them only as
  personal.
- `GET /members` needs `member:manage`, so only an administrator can add
  project members from the client; an owner who is not one can hand over and
  remove but not add.
- A wrong current password on `POST /account/password` answers 401
  `invalid_login`; the client keeps the session for that route only.
- The limiter keys on the client address the API sees. Behind a reverse proxy
  that is the proxy, so the 30-per-source limit then applies to everyone.

## Code

- [0004_installation_mode.py](../../server/migrations/versions/0004_installation_mode.py)
- [0005_members.py](../../server/migrations/versions/0005_members.py)
- [0006_project_members.py](../../server/migrations/versions/0006_project_members.py)
- [0008_member_models.py](../../server/migrations/versions/0008_member_models.py),
  [member_models.py](../../server/app/services/member_models.py) quota, usage
  and default model, [provider_configs.py](../../server/app/services/provider_configs.py)
  ownership
- [project_members.py](../../server/app/services/project_members.py) service,
  [project_members.py](../../server/app/api/project_members.py) routes
- [members.py](../../server/app/services/members.py) service,
  [members.py](../../server/app/api/members.py) routes,
  [members.py](../../cli/bid_cli/members.py) CLI
- [0009_package_jobs.py](../../server/migrations/versions/0009_package_jobs.py),
  [0010_package_imports.py](../../server/migrations/versions/0010_package_imports.py)
  to [0015_package_import_library.py](../../server/migrations/versions/0015_package_import_library.py),
  [packages.py](../../server/app/services/packages.py) service,
  [packages.py](../../server/app/api/packages.py) routes,
  [packages.py](../../cli/bid_cli/packages.py) CLI
- [admin.py](../../server/app/admin.py) `initialize_database`, `installation_mode`
- [db.py](../../server/app/core/db.py) `Database.verify_role`
- [auth.py](../../server/app/services/auth.py) `authenticate`, `access_key_refused`,
  `member_session_identity`, `agent_identity`
- [workspaces.go](../../desktop/workspaces.go), [gateway.go](../../desktop/gateway.go),
  [keychain.go](../../desktop/keychain.go),
  [workspace_runner.go](../../desktop/workspace_runner.go),
  [workspace_switch_test.go](../../desktop/workspace_switch_test.go),
  [native_workspaces.go](../../desktop/native_workspaces.go),
  [native_signin_test.go](../../desktop/native_signin_test.go),
  [native_members.go](../../desktop/native_members.go),
  [native_usage.go](../../desktop/native_usage.go),
  [native_packages.go](../../desktop/native_packages.go),
  [native_admin_test.go](../../desktop/native_admin_test.go) and
  [native_packages_test.go](../../desktop/native_packages_test.go) against a
  real enterprise server
- [test_deployment_mode.py](../../server/tests/test_deployment_mode.py),
  [test_members.py](../../server/tests/test_members.py),
  [test_project_visibility.py](../../server/tests/test_project_visibility.py),
  [test_member_models.py](../../server/tests/test_member_models.py),
  [test_package_export.py](../../server/tests/test_package_export.py),
  [test_package_import.py](../../server/tests/test_package_import.py)
