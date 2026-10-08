---
kind: reference
---

# Platform operator browser enrollment

## Problem

An allowlisted platform operator needs a password and an authenticator without
creating an org or editing a TOTP seed on the host. The authentication-table and
restricted-owner authority is defined by
[ADR 0009](../adr/0009-operator-enrollment.md); request shapes and failure codes
belong to the [operator enrollment contract](../plan/operator-enrollment.md).

## Usage

The host command `python -m app.admin platform-enroll EMAIL` or the platform console's
平台管理员 page issues a private, short-lived enrollment link. The recipient opens
the link, sets or confirms the password, scans the authenticator QR code or enters the
displayed secret manually, and submits its current code. Setup and the deployment
break-glass alternative are in
[Run the platform console](../guides/development.md#run-the-platform-console).
`bid platform operator list` lists allowlisted identities and factor metadata.

## How it works

The deployment allowlist remains the sole authority for operator access. Factor
lookup selects a deployment secret first, otherwise decrypts the matching database
record. A deployment-managed email cannot use browser enrollment.

Links are signed `operator-enroll` tokens bound to email and a fingerprint of the
password hash and factor generation, including absent state. Tokens are never
stored. Start checks this binding and writes nothing. It returns a fresh TOTP secret
only for authenticator setup and a separate signed pending token whose encrypted
payload is bound to that link, email and expiry.

Completion takes the shared account/source authentication locks and the enrollment
identity lock, then locks existing user/factor rows and rechecks state. The restricted
function creates a missing login identity without an org, sets a setup-only password,
or preserves an existing password after shared-pool confirmation. A concurrent user
created elsewhere wins its identity and cannot have its password overwritten by
enrollment. Factor replacement increments generation, invalidating other links.

The factor, identity change and `platform.operator.enroll` audit share one transaction.
The success audit carries the TOTP counter used; platform login includes enrollment
events when rejecting replay. Wrong password/code attempts use the existing account
and source failure windows. Invalid link causes share one response. Tokens, pending
blobs, passwords and factor secrets are absent from audit and error details.

Factors use the existing secrets root and retired-key ring with a typed envelope
bound to email and factor generation. The database `key_version` identifies the
envelope format, not the active root key. Root rotation re-encrypts each factor
without changing its generation, enrollment metadata or secret. The migration owner
performs maintenance; the runtime cannot query the factor table directly.

The public page removes the URL fragment before its start request. QR rendering uses
the provisioning URI locally. Completion and unmount clear the component's password,
secret, URI, QR data and pending token; the success view links to platform sign-in.

## Pitfalls

- The issuer must deliver links privately; link issuance does not send email or
  prove ownership of an email address. An existing account still needs its password.
- The enrollment code's step is already consumed. Wait for a later authenticator
  code before signing in after enrollment.
- Any password change or completed enrollment invalidates other outstanding links.
  A rejected or expired link needs reissuance; starting enrollment does not reserve
  or change account state.
- Browser recovery requires another signed-in operator or the trusted host. A
  deployment factor overrides database storage until explicitly removed.
- Missing `BID_SECRETS_KEY` returns `enrollment_unavailable`. Retain retired roots
  while online rows and recoverable backups still require them.
- Account/source lockouts are shared with ordinary sign-in. Failed code/password
  retries cannot bypass a blocked account by changing the enrollment link.
- Preserve factor and audit history during recovery. Removing accepted-counter
  audit rows resets replay protection.

## Code

- [0060_operator_enrollment.py](../../server/migrations/versions/0060_operator_enrollment.py):
  global factor table, restricted function owner, locking and atomic completion.
- [operator_enrollment.py schemas](../../server/app/schemas/operator_enrollment.py):
  runtime copies of the approved wire contract.
- [operator_enrollment.py service](../../server/app/services/operator_enrollment.py):
  signed links, encrypted pending state and enrollment/factor lookup.
- [platform.py](../../server/app/services/platform.py): platform login and replay checks.
- [password_attempts.py](../../server/app/core/password_attempts.py): bounded shared
  password work and authentication failure windows. The supported unusable hash
  marker is `!setup`; other stored hashes must be confirmed and are never replaced.
- [operator_secrets.py](../../server/app/core/operator_secrets.py),
  [provider_secrets.py](../../server/app/core/provider_secrets.py) and
  [admin.py](../../server/app/admin.py): encryption roots and maintenance rotation.
- [platform.py API](../../server/app/api/platform.py) and
  [main.py CLI](../../cli/bid_cli/main.py): HTTP and operator CLI entry points.
- [OperatorEnrollment.vue](../../web/src/views/OperatorEnrollment.vue) and
  [Operators.vue](../../web/src/views/Operators.vue): public enrollment and operator listing.
- [test_operator_enrollment_db.py](../../server/tests/test_operator_enrollment_db.py)
  and [operator-enrollment.spec.js](../../web/e2e/operator-enrollment.spec.js): HTTP
  database and browser acceptance coverage.
