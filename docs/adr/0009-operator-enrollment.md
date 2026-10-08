---
kind: adr
---

# 0009 Browser enrollment of allowlisted platform operators

Date: 2026-10-08. Status: **accepted.**

## Context

Platform operators need to bind an authenticator without host access or creating an
organization merely to obtain a login identity. The deployment-only TOTP requirement
in [ADR 0001](0001-platform-console-access.md#decision) makes both first enrollment
and lost-device recovery require environment changes. The deployment allowlist can
remain the authority for platform privileges while encrypted factors move to a
separate authentication table. The exceptions in
[agent.md hard rule 1](../../agent.md#hard-rules-must-never-be-violated) and
[ADR 0006](0006-platform-credentials.md) do not permit storing login factors in
the outbound-service credential table.

## Decision

The approved [operator enrollment contract](../plan/operator-enrollment.md) defines
the flow, payloads, error codes and acceptance.

- Amend ADR 0001's deployment-only TOTP sentence: operator identities remain
  exclusively in `BID_PLATFORM_ADMIN_EMAILS`, while each TOTP factor may come from
  deployment configuration or encrypted database storage. Environment factors take
  precedence and block browser enrollment for that email. The application cannot
  add operators. This also narrows ADR 0006's deployment-only TOTP-seed statement;
  authentication factors do not become platform service credentials.
- Approve the fourth global-table exception, `platform_operator_factors`, without
  `org_id` or tenant RLS. It stores only lowercased email, encrypted TOTP secret,
  encryption-envelope version, factor generation and enrollment time/issuer. Do not
  store operator allowlists, plaintext secrets, enrollment links, org content or
  recovery codes in this table.
- Use a dedicated `bid_operator_enrollment_fn` owner with `NOLOGIN NOSUPERUSER
  NOBYPASSRLS NOINHERIT`. Grant only factor read/write, required identity columns
  and append-only audit insertion. Do not extend `bid_platform_fn` or the service
  credential owner. `bid_app` has no table access; fixed `SECURITY DEFINER`
  functions provide one-email ciphertext lookup, metadata listing, identity/factor
  locking and atomic enrollment. Functions fix `search_path`, qualify table names,
  revoke PUBLIC execution and use no dynamic SQL. Application platform-session
  checks authorize list/link routes; database actor text is not authentication.
- Encrypt factors under the existing independent `BID_SECRETS_KEY` domain with an
  envelope bound to email and generation. Previous secrets keys remain decrypt-only;
  `app.admin rotate-provider-secrets` rewraps factors without changing their values
  or generations. A missing root key makes browser enrollment unavailable.
- Issue unstored 30-minute `operator-enroll` tokens from the trusted host or a
  signed-in operator for an eligible allowlisted email. Bind every link to the
  current password hash and factor generation, including absence. Start writes
  nothing and returns a new secret for browser QR rendering, plus a separate signed
  pending token carrying that secret encrypted and bound to the link/email/expiry.
- Completion serializes absent identities and locks existing identity/factor rows
  before rechecking state. An existing usable password must be confirmed through
  shared password admission and never replaced; a setup-only or absent account sets
  a password through the same bounded hashing pool. No org membership is created.
  Factor upsert, identity creation/password setup and `platform.operator.enroll`
  audit commit together. Audit records the consumed TOTP counter so login rejects
  that step. Failed code/password attempts share account/source failure windows.
- Preserve factors, identities and audit history on downgrade. Removing them
  requires an explicit recovery or migration plan.

## Tradeoffs and consequences

A signed enrollment link is a bearer invitation that must be delivered privately.
Existing-password confirmation limits takeover after link disclosure; a new identity
still relies on the issuer's private delivery. Removing an allowlist entry or changing
the account password/factor state invalidates outstanding invitations. No email
delivery, WebAuthn or recovery-code mechanism is added.

Encrypted database factors remove host access from normal enrollment while keeping
deployment factors available for recovery. A database backup needs the corresponding
secrets root for restoration; simultaneous database/root compromise exposes factors.
Environment overrides require a deployment change and restart, and disable browser
factor changes until removed. Operator accounts remain independent of org membership.
