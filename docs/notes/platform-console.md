# Platform console

## Problem

The platform operator provisions and disables orgs, maintains the paid model
catalog, and reviews usage and charges across orgs. None of that may expose an
org's business data, and nothing in the application may be able to grant
itself operator rights or read other orgs through ordinary queries.

## Usage

Operators sign in at `/app/platform/login` or with `bid platform login`, using a
password and a TOTP code. Setup steps are in
[development.md](../guides/development.md#run-the-platform-console); CLI
commands are in [cli.md](../guides/cli.md#operate-the-platform). Why the
cross-org access is shaped this way is recorded in
[ADR 0001](../adr/0001-platform-console-access.md).

## How it works

Operators are the emails in `BID_PLATFORM_ADMIN_EMAILS`; each needs an entry in
`BID_PLATFORM_TOTP_SECRETS`, or startup fails. `python -m app.admin platform-totp`
prints a new secret and its provisioning URI. Sign-in checks the password and
an RFC 6238 code with one step of drift. The shared password admission and
atomic TOTP consumption rules are described below. A platform session is a Fernet token of
kind `platform`, valid for 30 minutes and re-checked against the configured
list on every request. Org routes accept only `session` tokens and `bid_`
API tokens, so the two kinds never cross.

### Password admission and TOTP consumption

`/auth/orgs`, `/auth/login` and `/platform/auth/login` use `PasswordAttempts`
in [password_attempts.py](../../server/app/core/password_attempts.py). The
account key is the email after trimming whitespace and lowercasing. Five
failed attempts in a rolling 15-minute window block every entry point,
regardless of source or org. The existing `platform.login` failures count
alongside `auth.password` failures; a successful password check does not clear
either. A platform attempt with invalid or replayed TOTP also counts.

When the ASGI request provides a client address, 30 failures from that source
in the same window additionally block attempts across accounts. Source failure
records use a SHA-256-derived actor key and the `auth.source` action, so both
dimensions use the existing audit actor/action/time index. The routes use
`request.client.host`, never parse `X-Forwarded-For` themselves, and keep the
account limit when a client address is unavailable. Missing, inactive and
setup-only accounts follow the same failure and dummy PBKDF2 path. Invalid
credentials return the same `401 invalid_login`; limits return the same
`429 too_many_attempts`, without an identity-existence check.

PostgreSQL transaction advisory locks are taken in source-then-account order
before counting failures. The authentication transaction explicitly uses
`READ COMMITTED`, so a waiter sees the preceding attempt's committed writes.
Password verification, TOTP verification and audit
inserts run under those locks. Failures commit before the API raises the
credential error. For TOTP, `login.consume_totp` in
[platform.py](../../server/app/services/platform.py) re-reads the last accepted
counter inside that transaction and rejects a counter no greater than it.
The success row commits before session issuance, so concurrent API workers
cannot issue two sessions for the same counter. No schema change is needed.

Each API application admits at most four active attempts and eight queued
attempts. Waiting for local admission or a database advisory lock has a
one-second timeout. Four additional transaction advisory slots limit password
verification across all API processes using the same database; acquiring a
slot never waits. PBKDF2 runs in a dedicated four-thread executor, not the
shared asyncio executor. Full queues, expired waits and unavailable database
slots return `503 auth_busy` with `Retry-After: 1` and CLI exit code 3. A lockout
returns exit code 3 and a conservative `Retry-After: 900`. Neither rejection
performs PBKDF2 or appends another failure. Cancelled HTTP requests retain
their admission until their authentication task finishes and commits; shutdown
drains those tasks before disposing of the database and executor.

Migration `0010` adds `orgs.active`, the global `platform_models` and
`platform_audit_logs` tables, usage columns for input and output tokens,
`platform_model_id` and `charge`, and the role `bid_platform_fn` that owns
`platform_org_summaries`, `platform_usage_summary`, `platform_create_org` and
`platform_set_org_active`. `membership()` in
[auth.py](../../server/app/services/auth.py) rejects a disabled org, so login,
sessions and tokens of that org fail at once with `org_inactive`.

Provisioning creates the admin account with the unusable hash `!setup` when the
email is new. The response carries `/app/setup-password#token=...`: a 24-hour
signed token bound to a fingerprint of the current hash, so setting a password
invalidates it. The token sits in the URL fragment, which browsers do not send
to the server or in a `Referer`. Migration `0011` grants `bid_app` the
column update this needs.

Catalog adapters use the key in `BID_PLATFORM_CREDENTIAL_<NAME>`. Tenant selection,
fallback order and queued-job identity are defined in
[provider-config.md](provider-config.md#resolution-and-cache). Platform usage stores
vendor cost in `usd` and the sale-price amount, in
`BID_BILLING_CURRENCY`, in `charge`; the charge is deducted from the prepaid
balance described in [prepaid-billing.md](prepaid-billing.md). Legacy environment-adapter
usage counts as `unbilled`. A default whose credential is missing fails extraction
with `provider_unavailable` instead of falling back. Catalog models may list
the vendor's official reasoning levels; see
[reasoning-levels.md](reasoning-levels.md).

The API serves the built console from `BID_WEB_DIR` under `/app`, returning
`index.html` for client routes and never a file outside the build, with a
strict Content-Security-Policy and `Referrer-Policy: no-referrer`.

## Pitfalls

- The summary functions are executable by `bid_app`; the operator check in
  [platform.py](../../server/app/api/platform.py) is what keeps org users out.
  Every new platform route must depend on `operator`.
- A new column in a summary function's result is a disclosure decision. Keep
  business tables out of `bid_platform_fn`'s policies.
- Catalog models cannot be deleted, only disabled, so usage keeps its reference.
- Catalog edits can stop queued jobs whose fixed model identity no longer matches;
  a fresh submission uses the new catalog revision.
- The model test button makes a real vendor call inside a database
  transaction and is billed by the vendor; it records cost in the audit log only.
- The limits above also affect ordinary org users. Accounts recover when fewer
  than five failures remain in the rolling window; locked requests do not
  extend it. Users behind one NAT or proxy share the source budget. Configure
  the ASGI server's trusted proxies correctly; untrusted forwarding headers
  must not determine its client address.
- All API workers must run the shared authentication implementation; old
  workers do not acquire its advisory locks. The local queue bound applies
  per application, while the PBKDF2 slot bound applies per database.
- Authentication failures add append-only audit rows, including source
  counters. Retention must preserve the active failure window and the last
  accepted TOTP counter; deleting those rows resets that protection.

## Code

- [0010_platform_admin.py](../../server/migrations/versions/0010_platform_admin.py), [0011_password_setup.py](../../server/migrations/versions/0011_password_setup.py)
- [server/app/services/platform.py](../../server/app/services/platform.py), [server/app/api/platform.py](../../server/app/api/platform.py), [server/app/core/totp.py](../../server/app/core/totp.py)
- [server/app/core/password_attempts.py](../../server/app/core/password_attempts.py), [server/app/services/auth.py](../../server/app/services/auth.py)
- [server/app/schemas/platform_contracts.py](../../server/app/schemas/platform_contracts.py), `mount_console` in [server/app/api/main.py](../../server/app/api/main.py)
- [web/src/](../../web/src/main.js) and [web/e2e/platform.spec.js](../../web/e2e/platform.spec.js)
- Tests: [test_platform_db.py](../../server/tests/test_platform_db.py), [test_platform_auth.py](../../server/tests/test_platform_auth.py), [test_auth_limits.py](../../server/tests/test_auth_limits.py), [test_platform_api.py](../../server/tests/test_platform_api.py), [test_console.py](../../server/tests/test_console.py)
