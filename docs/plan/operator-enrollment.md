---
kind: plan
---

# Platform operator enrollment in the browser

Status: **Pending approval, not implemented**.

This contract lets an allowlisted platform operator set a password and bind a TOTP
authenticator in the browser from a one-time link, instead of running
`app.admin bootstrap` and `app.admin platform-totp` and editing the deployment
environment. It amends one decision of
[ADR 0001](../adr/0001-platform-console-access.md#decision): TOTP secrets may live
encrypted in the database. The operator allowlist stays deployment configuration, so
the application still cannot create a platform operator. The importable
[Pydantic v2 contract](operator-enrollment/operator_enrollment_contracts.py) defines
payloads and the service Protocol; it registers nothing. The
[Open decisions](#open-decisions) are recommended defaults for approval, not decisions
already adopted.

## Goal and scope

Today a platform operator needs three things set up on the host: a `users` row with a
password (created through `app.admin bootstrap`, which also creates an org), the email
in `BID_PLATFORM_ADMIN_EMAILS`, and a secret in `BID_PLATFORM_TOTP_SECRETS` printed by
`app.admin platform-totp`. Each change needs a shell and a restart.

In scope: one-time enrollment links for allowlisted emails, a public enrollment page
that sets or confirms the password and binds a new TOTP secret, encrypted storage of
that secret, login reading it, re-enrollment after a lost device, and audit.

Excluded: adding or removing operators from the application, WebAuthn or recovery
codes, email delivery of links, and changes to org sign-in.

## Verified code foundations

| Current code anchor | Behavior this contract builds on |
| --- | --- |
| `Settings.platform_admins`, `Settings.platform_totp` in [config.py](../../server/app/core/config.py) | Allowlist and `email:BASE32` secrets parsed from the environment; malformed secrets stop startup. |
| `login`, `setup_token`, `setup_password` in [platform.py](../../server/app/services/platform.py) | Login requires allowlist, password and an unused TOTP step recorded in platform audit rows. Setup links are signed tokens bound to the current password-hash fingerprint, never stored. |
| [totp.py](../../server/app/core/totp.py) | RFC 6238 secret generation, ±1 step verification and `otpauth://` URIs. |
| `platform_totp`, `bootstrap` in [admin.py](../../server/app/admin.py) | Current shell-only setup commands. |
| `users` grants in [0001](../../server/migrations/versions/0001_tenant_foundation.py) and [0011](../../server/migrations/versions/0011_password_setup.py); `platform_create_org` in [0010](../../server/migrations/versions/0010_platform_admin.py) | `bid_app` reads users and updates only `password_hash`; user rows are inserted by `bid_platform_fn` functions. |
| [ADR 0006](../adr/0006-platform-credentials.md) | Pattern for encrypting platform-held secrets with `BID_SECRETS_KEY` behind restricted functions. |

## Flow

1. **Issue a link.** The deployment runs
   `python -m app.admin platform-enroll EMAIL`, or a signed-in operator uses
   **生成开通链接** on a new 平台管理员 page for another allowlisted email. The email must
   be in `BID_PLATFORM_ADMIN_EMAILS`. The result is
   `/app/platform/enroll#token=…`, valid for 30 minutes. The token is signed, not
   stored, and bound to the email plus a fingerprint of the email's current enrollment
   state (password hash and factor generation), so any completed enrollment or
   password change invalidates every outstanding link for that email.
2. **Start.** The page posts the token (from the URL fragment, never sent as a query
   string). The server checks signature, expiry, allowlist and state fingerprint, then
   returns the email, whether a password must be set or confirmed, a freshly generated
   TOTP secret with its `otpauth://` URI for the QR code, and a signed, encrypted
   `pending` blob carrying that secret. Nothing is written yet.
3. **Complete.** The page posts the token, the `pending` blob, the password and a
   6-digit code. The server re-checks the token state, verifies the code against the
   pending secret, then in one transaction:
   - no `users` row: creates one (no org membership) with the new password;
   - a row with an unusable password: sets the new password;
   - a row with a usable password: verifies the submitted password against it through
     the shared password pool and never changes it;
   - stores the secret encrypted, increments the factor generation, and writes a
     `platform.operator.enroll` audit row including the TOTP step used, so that step
     cannot also be used to sign in.
4. The operator signs in at `/app/platform/login` as before. An operator who also
   needs an org creates one in the platform console with their own email; the
   existing account is attached as its admin.

Re-enrollment after a lost device uses a new link and replaces the stored secret.

## Secret source and precedence

| Source | Rule |
| --- | --- |
| Allowlist | Only `BID_PLATFORM_ADMIN_EMAILS`. Removing an email blocks login and enrollment immediately after restart, whatever factor is stored. |
| `BID_PLATFORM_TOTP_SECRETS` | Still accepted. An email with an environment secret uses it and cannot enroll in the browser; this keeps existing deployments and gives a break-glass path. |
| Database factor | Used when the email has no environment secret. Encrypted with `BID_SECRETS_KEY`; enrollment is unavailable without it. Covered by `app.admin rotate-provider-secrets`. |

## Data and authority

`platform_operator_factors` is a global table without `org_id` or RLS: `email`
(primary key, lowercased), `secret_ciphertext`, `key_version`, `generation`,
`enrolled_at`, `enrolled_by` (`link` or the issuing operator's email). It is the fourth
global-table exception and needs its own ADR, amending the TOTP sentence of ADR 0001.
`bid_app` has no table privileges. Fixed `SECURITY DEFINER` functions owned by a
dedicated `NOLOGIN` role read one email's ciphertext and generation, list enrollment
status without ciphertext, and perform the enrollment transaction (factor upsert plus
the user insert or password update). Functions fix `search_path`, revoke PUBLIC
execute and contain no dynamic SQL.

## HTTP, CLI and console

| HTTP | CLI | Result data |
| --- | --- | --- |
| `POST /platform/operators/{email}/enrollment-links` (operator session) | `python -m app.admin platform-enroll EMAIL` (host only) | `EnrollmentLink` |
| `GET /platform/operators` (operator session) | `bid platform operator list` | items of `OperatorStatus` |
| `POST /platform/enrollment/start` (public, token in body) | none | `EnrollmentStart` |
| `POST /platform/enrollment/complete` (public, token in body) | none | `EnrollmentResult` |

Console additions: `/app/platform/enroll` (public: password fields, QR code rendered
in the browser from the URI, code field) and a 平台管理员 page listing allowlisted
emails with their factor source and a 生成开通链接 action that shows the link once
with a copy button.

## Abuse and failure handling

| Condition | Behavior |
| --- | --- |
| Bad signature, expired, changed state, email no longer allowlisted | 400 `invalid_enrollment_link`; same response for every cause. |
| Email has an environment secret | 409 `factor_managed_by_deployment`. |
| `BID_SECRETS_KEY` missing | 503 `enrollment_unavailable`. |
| Wrong code or wrong existing password | 401 `invalid_enrollment`; counts toward the existing per-account and per-source failure windows used by sign-in (5 per account and 30 per source per 15 minutes). |
| Weak new password | 400 `weak_password` (12–1,024 characters). |
| Concurrent completion of two links | The state fingerprint is re-checked under a row lock; the second gets `invalid_enrollment_link`. |

Tokens, pending blobs, passwords and secrets never appear in logs, audit rows or error
payloads. The link is shown once to the issuer; the audit row records only issuer,
target email and expiry.

## Acceptance

End-to-end against PostgreSQL through HTTP and the browser: issue a link by CLI and by
console; enroll a brand-new email, then sign in with password and a later TOTP code;
re-enroll an existing operator, keeping the password and invalidating the old secret
and the other outstanding link; reject expired, reused, tampered and non-allowlisted
links identically; reject the enrollment code's TOTP step at sign-in; show that an
environment secret wins and blocks browser enrollment; show `bid_app` cannot read the
factor table; confirm no token, secret or password in logs, audit rows or responses.

## Open decisions

| Topic | Recommended default | Approval consequence |
| --- | --- | --- |
| Secret storage | Encrypted database factor with `BID_SECRETS_KEY`; environment secrets still win | Amends ADR 0001; operators enroll without host access. |
| Allowlist | Stays in `BID_PLATFORM_ADMIN_EMAILS` | The application still cannot create operators; changing it needs a deployment change and restart. |
| Link lifetime and binding | 30 minutes, signed, unstored, bound to password-hash and factor generation | A used or superseded link is dead; links travel through whoever issues them. |
| Who issues links | Host command, and any signed-in operator for another allowlisted email | An operator who lost the device is recovered by another operator or the host. |
| Accounts | Enrollment can create a user without an org; orgs are created separately in the console | `app.admin bootstrap` is no longer needed for the first operator. |
| Existing password | Confirmed, never replaced, when the account already has one | A leaked link cannot take over an account that has a password. |
| Second factor options | TOTP only | WebAuthn and recovery codes need a later contract. |
