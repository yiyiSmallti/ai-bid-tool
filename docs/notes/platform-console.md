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
an RFC 6238 code with one step of drift. The audit table supplies both the last
accepted time step, so a code works once, and the failure count, so five
failures in 15 minutes lock the email. A platform session is a Fernet token of
kind `platform`, valid for 30 minutes and re-checked against the configured
list on every request. Org routes accept only `session` tokens and `bid_`
API tokens, so the two kinds never cross.

Migration `0010` adds `orgs.active`, the global `platform_models` and
`platform_audit_logs` tables, usage columns for input and output tokens,
`platform_model_id` and `charge_usd`, and the role `bid_platform_fn` that owns
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

When the catalog has an enabled default model for `llm_extract`, `resolve_llm`
in [llm.py](../../server/app/providers/llm.py) builds the adapter from it with
the key in `BID_PLATFORM_CREDENTIAL_<NAME>`, for both job submission and
processing. Usage then stores vendor cost in `usd` and the sale-price amount in
`charge_usd`. Without a default, the `BID_LLM_*` fallback is used and its usage
counts as `unbilled`. A default whose credential is missing fails extraction
with `provider_unavailable` instead of falling back.

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
- Changing the default model between job submission and processing makes the
  worker use the new model under the old cache key.
- The model test button makes a real vendor call inside a database
  transaction and is billed by the vendor; it records cost in the audit log only.
- Repeated wrong passwords for an operator email lock that email for 15 minutes.

## Code

- [0010_platform_admin.py](../../server/migrations/versions/0010_platform_admin.py), [0011_password_setup.py](../../server/migrations/versions/0011_password_setup.py)
- [server/app/services/platform.py](../../server/app/services/platform.py), [server/app/api/platform.py](../../server/app/api/platform.py), [server/app/core/totp.py](../../server/app/core/totp.py)
- [server/app/schemas/platform_contracts.py](../../server/app/schemas/platform_contracts.py), `mount_console` in [server/app/api/main.py](../../server/app/api/main.py)
- [web/src/](../../web/src/main.js) and [web/e2e/platform.spec.js](../../web/e2e/platform.spec.js)
- Tests: [test_platform_db.py](../../server/tests/test_platform_db.py), [test_platform_auth.py](../../server/tests/test_platform_auth.py), [test_platform_api.py](../../server/tests/test_platform_api.py), [test_console.py](../../server/tests/test_console.py)
