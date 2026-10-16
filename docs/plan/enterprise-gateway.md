---
kind: plan
---

# Enterprise gateway specification

This specification delivers [ADR 0013](../adr/0013-enterprise-gateway.md): one
privately deployed gateway per company shares materials, tender projects, public
memory and company models among its members, while personal installations keep
the behavior of the [single-user plan](single-user.md). Contracts below are
proposals for confirmation under [the agent workflow](../../agent.md#workflow);
names may change in review, behavior may not change silently.

## Terms

| Term | Meaning |
| --- | --- |
| Personal installation | The desktop client with its bundled local server and database, one human, access-key authentication ([ADR 0012](../adr/0012-single-user-installation.md)) |
| Gateway | The same server deployed on a company server in enterprise mode |
| Member | A gateway account of one human; role `admin` or `member` |
| Grant | An extra capability given to a member: `library:edit`, `memory:edit`, `confidential:reveal` |
| Project member | A member added to one tender task; one of them is its owner |
| Company model | A saved model whose owner is the company; configured by administrators |
| Personal model | A saved model whose owner is one member; visible only to that member |
| Workspace | What the desktop client is connected to: its personal installation or one gateway |

## Invariants

1. A personal installation behaves exactly as before: no member rows, no
   sign-in, no project membership checks, all existing suites pass unchanged.
2. A gateway serves one company. There is no tenant column and no cross-company
   view.
3. The gateway database is the only authoritative copy of enterprise data.
   Clients keep no persistent copy of it.
4. Human confirmation stays human-only and records the confirming member.
   Agents and `bid_` tokens never receive human-only scopes. No
   separation-of-duty rule applies between members.
5. Secrets never leave the server in any response, log, package or client file:
   provider keys, service credentials, confidential plaintext (except an
   authorized reveal), passwords and session secrets.
6. A non-member of a project cannot learn that the project exists: every
   project-scoped request by a non-member returns `not_found`.

## Deployment mode

`Settings.deployment: Literal["personal", "enterprise"] = "personal"`, from
`BID_DEPLOYMENT`.

A new singleton table records the mode the database was initialized with:

| Column | Type | Rule |
| --- | --- | --- |
| `id` | `boolean` primary key | Always `true` (one row) |
| `mode` | `varchar(20)` | `personal` or `enterprise`; immutable |
| `company_name` | `varchar(100)` | Required in enterprise mode, null in personal |
| `created_at` | `timestamptz` | |

`app.admin init-db` writes the row on first initialization; an existing
database without the row is a personal database. The API and worker refuse to
start when the configured mode differs from the stored one (exit with
`deployment_mode_mismatch`). The mode cannot be changed in place; moving data
between modes uses [packages](#packages).

Enterprise-only settings:

| Setting | Default | Rule |
| --- | --- | --- |
| `BID_PUBLIC_URL` | required | `https://` origin that clients use; signed links use it |
| `BID_COMPANY_NAME` | required at init | Written into the singleton row |
| `BID_SESSION_HOURS` | 12 | Absolute session lifetime, 1–72 |
| `BID_SESSION_IDLE_MINUTES` | 120 | Idle expiry, 10–720 |
| `BID_PERSONAL_MODEL_ENDPOINTS` | vendor-template endpoints | Comma-separated HTTPS origins personal models may use; empty disables personal models |
| `BID_PASSWORD_MIN_LENGTH` | 12 | 12–128 |

In enterprise mode `BID_ACCESS_KEY` is still required for the admin command
line on the gateway host but is not accepted over HTTP.

## Data model

One migration adds the following to both modes; in a personal database the new
tables stay empty and the new nullable columns stay null.

### New tables

**`members`**

| Column | Type | Rule |
| --- | --- | --- |
| `id` | `uuid` | Primary key |
| `login` | `varchar(64)` | Unique, case-insensitive (`lower(login)` unique index), `^[a-z0-9._-]{3,64}$` |
| `display_name` | `varchar(60)` | Required |
| `role` | `varchar(10)` | `admin` or `member` |
| `password_hash` | `text` | `pbkdf2$600000$salt$digest`, as before ADR 0012 |
| `must_change_password` | `boolean` | True after creation and reset |
| `status` | `varchar(10)` | `active` or `disabled` |
| `created_at`, `disabled_at`, `password_changed_at` | `timestamptz` | |

At least one active administrator always exists: a trigger rejects the update
that would disable or demote the last one.

**`member_grants`**: (`member_id`, `grant`) primary key; `grant` in
`library:edit`, `memory:edit`, `confidential:reveal`. Administrators hold all
grants implicitly.

**`member_sessions`**

| Column | Type | Rule |
| --- | --- | --- |
| `id` | `uuid` | Primary key; carried in the signed session token |
| `member_id` | `uuid` | References `members` |
| `created_at`, `expires_at`, `last_seen_at` | `timestamptz` | `last_seen_at` updated at most once a minute |
| `revoked_at` | `timestamptz` | Set by sign-out, password change, reset, disable or admin revoke |
| `client_label` | `varchar(100)` | Client-supplied, display only |

**`login_attempts`**: login digest, source digest, outcome and time. Limits:
5 failures per login and 30 per source address in 15 minutes, password checks
on a bounded worker pool, as the pre-0012 `password_attempts.py` did. Unknown
logins verify against a dummy hash so timing does not reveal them.

**`task_members`**: (`task_id`, `member_id`) primary key, `role` in `owner` or
`member`, `added_at`. A partial unique index allows one owner per task; a
trigger rejects removing the owner without naming a new one.

**`member_settings`**: `member_id` primary key, `default_slot` (nullable,
references a visible saved model).

**`member_quotas`**: `member_id` primary key, `monthly_company_tokens`
(nullable means unlimited), `updated_at`.

### Changed tables

| Table | Change |
| --- | --- |
| `tasks` | `export_to_personal_allowed boolean not null default false` |
| `provider_slots` | `owner_member_id uuid null`; null is the company (or the single user in personal mode) |
| `memories` | `scope` admits `private`; `owner_member_id uuid null`; check `scope = 'private'` ⇔ `owner_member_id is not null`; `task_id` stays null for both |
| `memory_scope_epochs` | Key becomes (`scope`, `owner_member_id`) so private memory invalidates per member |
| `api_tokens` | `member_id uuid null`; required in enterprise mode |
| `agent_principals` | `member_id uuid null`; the member who started the agent; required in enterprise mode |
| `vendor_calls`, `usage_records` | `member_id uuid null`; the member on whose behalf the call ran |
| `audit_logs` | `actor_member_id uuid null` |
| Human decision tables | `actor_member_id uuid null` (see below) |

Human decision tables are every table that records a human confirmation,
review or decision, including `requirement_review_events`,
`response_card_revisions`, `check_decisions`, `score_rubric_decisions`,
`score_rubric_coverage_decisions`, `memory_revisions`,
`prototype_evidence_decisions`, `attachment_reviews`, `bid_review_finding_events`
and `export_runs`. The implementation enumerates them from the triggers that
read `app.actor_kind`; the list is checked by a schema test, not maintained by
hand.

### Actor attribution

`set_actor_context` sets a new transaction-local `app.actor_member_id`. One
trigger function fills `actor_member_id` on insert into every human decision
table and `audit_logs`, and in enterprise mode rejects a human decision
(`app.actor_kind = 'session'`) without a member. Worker writes inherit the
member of the job that runs them, so job, agent and token writes carry the
member who caused them.

## Authentication

```python
class MemberSignIn(BaseModel):
    login: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=256)
    client_label: str | None = Field(default=None, max_length=100)

class SessionView(BaseModel):
    session: str            # signed token; returned once
    expires_in: int
    member: "MemberView"
    must_change_password: bool

class MemberView(BaseModel):
    id: UUID
    login: str
    display_name: str
    role: Literal["admin", "member"]
    grants: list[Literal["library:edit", "memory:edit", "confidential:reveal"]]
    status: Literal["active", "disabled"]

class PasswordChange(BaseModel):
    current: SecretStr
    new: SecretStr          # length per BID_PASSWORD_MIN_LENGTH
```

- `POST /auth/session` takes `SessionCreate` (access key) in personal mode and
  `MemberSignIn` in enterprise mode; the other body is `invalid_input`.
  Wrong login and wrong password return the same `invalid_login`.
- The session token is `TokenSigner.issue({"kind": "session", "sid", "mid"})`.
  `authenticate` opens it, then loads the `member_sessions` row and the member
  in the same transaction; a revoked, expired, idle or disabled one is
  `invalid_session`. Disabling a member or changing a password therefore takes
  effect on the next request.
- A session with `must_change_password` may call only `GET /auth/session`,
  `POST /account/password` and `DELETE /auth/session`.
- `bid_` tokens belong to the member who created them. Their effective scopes
  are the token scopes intersected with the member's current scopes; a disabled
  member's tokens fail.
- Agent principals created by a member are revalidated against that member on
  every step, as `agent_identity` already revalidates grants.
- The CLI authenticates to a gateway with a `bid_` token only (`BID_API_URL`,
  `BID_TOKEN`); there is no CLI password sign-in.
- `GET /installation` (unauthenticated) returns `mode`, `company_name`,
  `server_version` and `contract_version`, so the client can probe an address
  before sign-in.

## Authorization

`Identity` gains `member_id`, `member_role` and `grants`. In personal mode they
are absent and every check below is skipped.

### Scopes by role

| Scope group | Member | Administrator |
| --- | --- | --- |
| Task, requirement, card, draft, check, score, export, bid review, job | Yes, inside projects they belong to | Yes, in every project |
| `task:create` | Yes; the creator becomes owner | Yes |
| Library read (`resource:read`, `certificate:read`, `profile:read`, `template:read`, attachments read) | Yes | Yes |
| Library write and lifecycle | With `library:edit` | Yes |
| `memory:read`, `memory:retrieve` | Public plus own private | Public plus own private |
| `memory:write`, `memory:approve`, `memory:manage` on public memory | With `memory:edit` | Yes |
| Same on own private memory | Yes | Yes |
| `confidential:read` (masked) | Inside their projects | Yes |
| `confidential:reveal` | With `confidential:reveal` | Yes |
| `confidential:write` | No | Yes |
| `provider:write` on company models | No | Yes |
| `provider:write` on own personal models | Yes, if personal models are enabled | Yes |
| `credential:manage`, `clef:manage`, `trust-anchor:manage` | No | Yes |
| `member:manage` (new) | No | Yes |
| `token:create` | Yes, for themselves | Yes, for themselves |
| `agent:run`, `agent:read`, `agent:cancel` | Inside their projects | Yes |

### Project visibility

Every task-scoped service resolves its task through one function,
`visible_task(session, identity, task_id)`, which returns the task only to an
administrator or a project member and otherwise raises `not_found`. Lists of
tasks, jobs, exports, events and usage filter through the same predicate.
Library and memory rows are not task-scoped and follow the scope table.

Removing a member from a project, or disabling a member: queued and running
jobs already admitted finish, new requests are denied, and that member's agent
sessions on the project are cancelled with reason `member_removed`.

## Models

`ProviderConfigInput` gains `owner: Literal["company", "personal"] = "company"`.
In personal mode only `company` is valid and means the single user, as today.

- **Visibility.** A member sees company models and their own personal models.
  An administrator sees company models and their own personal models; other
  members' personal models are never listed, including to administrators.
- **Default.** A run without a model uses the member's `default_slot` when it
  is still visible, otherwise the company default.
- **Keys.** Keys are encrypted at rest and never returned. Key reuse on save
  ("omitted key keeps the saved one for the same provider and endpoint") is
  limited to the same owner; a personal model can never reuse a company key.
- **Personal endpoints.** A personal model's endpoint must be on
  `BID_PERSONAL_MODEL_ENDPOINTS`; otherwise `provider_endpoint_not_allowed`.
- **Quota.** Before admitting a call on a company model, admission sums the
  member's company-model tokens for the current calendar month from the
  existing `vendor_calls`/`usage_records` ledger, under the same lock that
  serializes admission today. Exceeding `monthly_company_tokens` fails the job
  with `member_quota_exceeded`. Personal models are not capped.
- **Usage view.** `GET /members/{id}/usage?month=YYYY-MM` returns tokens and
  call counts by model owner and model; members may read only their own.

## Memory

Memory keeps its current lifecycle (candidates, approval, revisions, feedback,
evaluation). Scope `library` is public company memory. Scope `private` belongs
to one member. Drafting retrieval reads public memory plus the acting member's
private memory; for a job, the acting member is the job's member. Private
memory is never readable by other members or administrators, and is not
included in packages.

## API additions

All responses use the existing seven-field Result envelope.

| Route | Scope | Behavior |
| --- | --- | --- |
| `GET /installation` | none | Mode, company, versions |
| `POST /auth/session` | none | Sign in (see above) |
| `GET /auth/session` | session | Current member, role, grants, mode |
| `DELETE /auth/session` | session | Revoke the current session |
| `POST /account/password` | session | Change own password; revokes other sessions |
| `GET /account/sessions` | session | Own active sessions |
| `DELETE /account/sessions/{id}` | session | Revoke one own session |
| `GET /members` | `member:manage` | Members with role, grants, status |
| `POST /members` | `member:manage` | Create; returns a one-time initial password |
| `PATCH /members/{id}` | `member:manage` | Display name, role, grants, status |
| `POST /members/{id}/password-reset` | `member:manage` | New one-time password; revokes sessions and tokens' sessions |
| `DELETE /members/{id}/sessions` | `member:manage` | Revoke all sessions of a member |
| `PUT /members/{id}/quota` | `member:manage` | Set or clear the monthly company-model cap |
| `GET /members/{id}/usage` | self or `member:manage` | Monthly usage |
| `GET /tasks/{id}/members` | project member | Project members and owner |
| `POST /tasks/{id}/members` | owner or admin | Add members |
| `DELETE /tasks/{id}/members/{member_id}` | owner or admin | Remove a member (not the owner) |
| `POST /tasks/{id}/owner` | owner or admin | Hand ownership to another project member |
| `PATCH /tasks/{id}/sharing` | admin | `export_to_personal_allowed` |
| `PUT /account/default-model` | session | Set or clear own default model |
| `POST /packages/export` | project member | Start a package export job for one task |
| `POST /packages/import` | `task:create` | Upload a package; starts an import job |
| `GET /packages/{job_id}` | job owner | Import or export status and report |
| `GET /packages/{job_id}/file` | job owner | Download a finished export package |

`POST /members` and password reset return the initial password once in the
response body; it is never stored in plaintext or logged.

### CLI additions

| Command | Route |
| --- | --- |
| `installation show` | `GET /installation` |
| `member list`, `member create`, `member update`, `member reset-password`, `member revoke-sessions` | `/members` routes |
| `member quota`, `member usage` | quota and usage routes |
| `task member list`, `task member add`, `task member remove`, `task owner` | `/tasks/{id}/members`, `/tasks/{id}/owner` |
| `task sharing` | `PATCH /tasks/{id}/sharing` |
| `package export`, `package import`, `package status`, `package download` | `/packages` routes |

Each command is added to `COMMANDS` in `cli/bid_cli/schema.py` and to the CLI
snapshot. Agent tools are unchanged.

## Packages

A package moves one tender project between installations: personal to gateway,
or gateway to personal when `export_to_personal_allowed` is set (always
allowed from a personal installation).

Format `bid-package/1`, a zip archive:

```text
manifest.json        format, contract_version, source mode, created_at,
                     task summary, file list with sha256 and size,
                     table list with row counts and sha256
files/<sha256>       original documents, certificate files, attachments
data/<table>.jsonl   rows of the included tables, one JSON object per line
```

Included: the task, its documents and parse results (chunks), extractions and
requirements with review events, response cards with revisions and evidence
links, drafts, check runs and decisions, score rubrics and reports, and the
library items the task binds (certificates, profiles, templates, products,
features, attachments) with their revisions.

Excluded: provider configuration and keys, service credentials, private and
public memory, API tokens, agent sessions, vendor call receipts, confidential
plaintext. Confidential values appear only as their field identifiers.

Import rules:

- Verify every file hash and table hash against the manifest; any mismatch
  rejects the whole package with `package_integrity_failed`.
- The package's `contract_version` must be one the server can read.
- Row identifiers are kept, so row hashes and encryption envelopes stay valid;
  only ciphertext is re-encrypted with the installation's key. The task becomes
  a project owned by the importing member. A package whose task already exists
  on the installation fails with `package_task_exists`; the existing project
  must be deleted first.
- A library row whose identifier already exists is reused; others are
  created. On a gateway, creating library items requires `library:edit`;
  without it the import fails before writing.
- Consistency triggers still check every imported row; only the lease and
  actor checks are passed by the import authority.
- Every citation must resolve to an imported chunk, and every other reference
  to an imported row. Confirmations and decisions keep their original
  timestamps; the `package.imported` audit entry, attributed to the importing
  member, marks the project as imported and records the source mode, export
  time and package hash in `details.imported_from`.
- Confidential fields referenced by the project are listed in the import
  report as `needs_value`; drafts and exports that need them stay blocked until
  an administrator supplies values.
- An import is idempotent per package hash and member: a repeat returns the
  first import's result.
- The whole import commits in one transaction or not at all.
- Rows pass the workflow guards through the scoped import authority of
  [ADR 0014](../adr/0014-package-import-authority.md).

## Desktop client

### Workspaces

`workspaces.json` in the client data directory lists workspaces:

```json
{"current": "<id>", "workspaces": [
  {"id": "personal", "kind": "personal", "name": "本机"},
  {"id": "<uuid>", "kind": "enterprise", "name": "<company_name>",
   "url": "https://gateway.example.com", "pin_sha256": null}
]}
```

- The session token of an enterprise workspace is stored in the macOS Keychain
  (Windows Credential Manager later), never in a file. Passwords are never
  stored.
- Adding a gateway: the user enters an address; the client calls
  `GET /installation`, shows the company name and server version, and refuses
  a personal-mode server, a non-HTTPS address or an unreadable contract version.
  A gateway with a certificate the system does not trust can be added only by
  confirming its SHA-256 fingerprint, which is pinned in `pin_sha256`.
- Switching to an enterprise workspace stops the local server and database;
  switching back starts them. Local data is never modified by switching.
- Sign-in page: login and password; a forced password change follows when
  `must_change_password` is set. An expired session returns to sign-in.

### Pages

| Page | Personal | Enterprise member | Enterprise admin |
| --- | --- | --- | --- |
| Workspace switcher (sidebar header) | Yes | Yes | Yes |
| Sign-in, change password, my sessions | No | Yes | Yes |
| Members, roles, grants, quotas | No | No | Yes |
| Project members and owner (task page) | No | Yes | Yes |
| Model settings: company and personal sections | Single list as today | Company read-only, own personal editable | Both editable |
| Memory: public and private tabs | Public only | Both | Both |
| Usage | As today | Own | Everyone |
| Import package, export package | Yes | Import; export when allowed | Yes |

### Network behavior

- Requests carry no total timeout for uploads and downloads; they fail after
  60 seconds without progress. Other requests keep the 90-second limit.
- An unreachable gateway shows a connection banner with retry; nothing falls
  back to local data.
- The client refuses redirects (as today) and sends the session only to the
  workspace origin.
- On sign-in the client compares `contract_version`; an incompatible gateway
  shows "请升级客户端" or "请联系管理员升级企业服务" instead of failing on
  individual pages.

## Gateway deployment

- **Composition**: Docker Compose with Caddy (TLS termination), API, worker,
  PostgreSQL 16, Gotenberg and the screenshot renderer. The sandbox node is
  optional; without it, vendor capture and sandboxed rendering report
  `unavailable`.
- **Storage**: local volume or S3-compatible storage through the existing
  `storage` setting.
- **Database roles**: owner and runtime roles as in personal installations; the
  runtime role is not superuser and does not own tables.
- **Bootstrap**: `python -m app.admin init-db` with `BID_DEPLOYMENT=enterprise`,
  then `python -m app.admin member create --role admin --login <login>`, which
  prints a one-time password to the operator's terminal only.
- **Backup**: nightly `pg_dump` plus file storage snapshot, retained 14 days;
  `python -m app.admin restore --verify <backup>` restores into a scratch
  database and checks row counts and file hashes.
- **Upgrade**: take a backup, rehearse migrations on a clone, drop
  rehearsal-created roles, then migrate the live database
  (see [production deployment](production-deployment.md) and
  [gateway operations](../guides/gateway-operations.md)).
- **Logs**: no tender text, keys, passwords, session tokens or confidential
  values; request logs carry member id, route and status.

## Failure modes

Each item names what must not happen and the acceptance check that proves it.

| Failure | Guard | Check |
| --- | --- | --- |
| A non-member reads or writes a project through some route | `visible_task` choke point | Route sweep: every route with a task, document, job, card, draft, export or run identifier returns `not_found` for a non-member |
| A personal model reuses a company key | Same-owner key reuse | Save a personal model with a company endpoint and no key: `provider_key_required` |
| Tender text leaves through an arbitrary personal endpoint | Endpoint allowlist | Save a personal model with an unlisted endpoint: `provider_endpoint_not_allowed` |
| A disabled member keeps access through a session, token or agent | Per-request member check; agent revalidation | After disabling: session, token and running agent step all fail |
| Old sessions survive a password change or reset | Revocation on change | Other sessions fail after the change |
| The last administrator is removed | Trigger | Disabling or demoting the last admin is rejected |
| A project loses its owner | Trigger | Removing the owner without a successor is rejected |
| A human decision without a member on a gateway | Attribution trigger | Direct insert with `actor_kind = 'session'` and no member fails |
| An agent confirms on behalf of a member | Existing human-only gates | Agent confirmation attempts still fail |
| Quota bypass by concurrent jobs | Admission under the ledger lock | Parallel jobs at the cap: exactly the calls within the cap are admitted |
| A forged or altered package | Manifest hashes; citation resolution | Altered file, altered row and dangling citation each reject the import with no writes |
| A package leaks secrets | Exclusion list | Exported package contains no key, credential, token or confidential plaintext (byte scan against known values) |
| A gateway starts on a personal database, or the reverse | Stored mode | Mismatched start exits with `deployment_mode_mismatch` |
| A client talks to an incompatible gateway | `contract_version` probe | Old client against a newer gateway shows the upgrade message |
| A session token is sent to another host | Origin-bound client | Redirect and cross-origin links do not carry the session |
| Private memory leaks to another member or an administrator | Scope filter | Other members and admins see no private rows in list, retrieval or drafting inputs |
| Password guessing | Attempt limiter | Sixth failure in 15 minutes is rejected without a password check |

## Delivery slices

Each slice is one change; a slice merges only when its acceptance and the full
personal-mode suites pass.
Slices 0 to 6 are implemented; their mechanism is in
[the enterprise gateway note](../notes/enterprise-gateway.md).

| Slice | Content | Acceptance |
| --- | --- | --- |
| 0 | Deployment mode, singleton row, startup check, enterprise settings | Mode mismatch both ways; personal suites unchanged |
| 1 | Members, sessions, sign-in, password change and reset, attempt limiter, actor attribution, `/installation` | Failure rows for sessions, passwords, last admin, attribution |
| 2 | Scope table, `visible_task`, project members and owner, library and confidential grants, memory `private` scope | Route sweep; private memory; owner trigger; removal cancels agents |
| 3 | Company and personal models, default model per member, key reuse by owner, endpoint allowlist, quotas, usage | Model failure rows; quota race |
| 4 | Client workspaces, gateway probe and pinning, sign-in, Keychain session, enterprise pages, network behavior | Desktop snapshots of each page at 960x640; scripted switch between workspaces leaves local data byte-identical |
| 5 | Packages: export, import, report, idempotency | Package failure rows; round trip personal → gateway → personal keeps citations and confirmations |
| 6 | Compose deployment, bootstrap, backup, restore verify, upgrade script | Fresh install, backup, restore verify and one upgrade on a test host |

## End-to-end acceptance

One scripted run against a gateway started from the Compose files under
`data/work/enterprise-e2e/`, with two members (A, B) and one administrator:

1. The administrator creates A and B, gives A `library:edit` and `memory:edit`,
   sets B's quota.
2. A adds a certificate and a public memory entry, creates a project, uploads a
   tender, extracts with a company model and adds B to the project.
3. B sees the certificate and memory, drafts in the shared project with a
   personal model, and cannot see A's private memory or personal model.
4. A non-member C gets `not_found` on every project route of the sweep.
5. B confirms a response card; the decision records B. The agent started by A
   cannot confirm.
6. A personal installation exports a project package; A imports it; citations
   and confirmations verify; the administrator enables export and B exports it
   back to a personal installation.
7. Disabling B ends B's session, token and running agent.

The run writes `report.json` with each step's request, status and assertion,
plus row counts before and after, under `data/work/enterprise-e2e/<timestamp>/`.

## Out of scope

Single sign-on (WeCom, DingTalk, OIDC), a hosted multi-company service,
offline editing of enterprise data, synchronization between installations,
separation of duty between members, and email delivery of passwords.
