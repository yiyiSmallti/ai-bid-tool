---
kind: reference
---

# Organization self-service application

## Problem

Prospective organizations need an onboarding path before they have a tenant or a
membership. Manual platform review authorizes creation; submitting an application
confers no tenant access. The approved behavior is defined in
[the signup contract](../plan/org-signup.md), with the global-table exception in
[ADR 0008](../adr/0008-org-signup.md).

## Usage

When enabled, both sign-in pages link to `/app/apply`. Applicants provide contact
information and a password, then receive the same receipt regardless of account or
pending-application existence. Operators review the 申请 tab on `/app/platform/orgs`.
Approval optionally corrects the organization name; existing accounts require an
explicit attachment confirmation and retain their existing password. Rejection
requires an internal reason. There is no email delivery or applicant status lookup.

The equivalent operator commands are `bid platform org application list`,
`bid platform org application approve ID` and
`bid platform org application reject ID --reason TEXT`. All support `--json` and use
the existing password-plus-TOTP platform session. Activation steps are in
[the development guide](../guides/development.md#enable-organization-applications).

## How it works

Runtime models in [org_signup.py](../../server/app/schemas/org_signup.py) implement
the approved wire contract. The service derives a domain-separated HMAC-SHA256 key
from the token root, then digests `request.client.host`; it never stores a raw
address. Unknown client addresses share one digest. Source and pending-cap checks
run before hashing and again under a database lock before insertion. Every admitted
submission hashes through the shared password worker pool, including duplicates and
registered emails. A duplicate preserves the original row and produces no audit
write. Public receipt bodies carry no identifiers and a fixed zero `duration_ms`;
actual request time still depends on normal network and worker scheduling.

[Migration 0059](../../server/migrations/versions/0059_org_signup.py) gives `bid_app`
only EXECUTE on fixed SECURITY DEFINER functions owned by `bid_platform_fn`, with
PUBLIC access revoked. Approval locks the application, rechecks expiry and account
state, and calls `platform_create_org` in the same transaction. The new organization
uses ordinary RLS for its admin membership. Submission and decision audit writes
commit with their state changes. Terminal transitions clear the password hash.

The daily `bid.org_application_expire` task invokes only the global expiry function;
list reads already project overdue pending rows as expired, and approval refuses
them regardless of whether the worker has run. Existing queue reviews remain
available when public signup is disabled. The approved view model requires
`source_submissions_24h >= 1`; retained rows whose source has no recent submissions
therefore use 1 as the display minimum, rather than treating it as an exact zero count.

## Pitfalls

Source limits depend on deployment trusting only its own reverse proxy; arbitrary
forwarded headers must never override the socket peer in application code. Token-key
rotation changes source digests as well as invalidating sessions. The form does not
verify email ownership: operators must vet applications, especially attachment to
existing accounts. Stored contact information remains after rejection and expiry
until a separate retention policy is approved. Password hashes are never retained
in terminal applications. Downgrade refuses destructive history deletion; repair
forward or recover from an approved backup.

## Code

- [OrgSignupService](../../server/app/services/org_signup.py): admission, digest and function adapters.
- [PasswordAttempts](../../server/app/core/password_attempts.py): shared hashing and login admission.
- [Platform routes](../../server/app/api/platform.py): public submission and operator decisions.
- [Queue](../../server/app/jobs/queue.py): periodic expiry.
- [CLI commands](../../cli/bid_cli/main.py): platform application command group.
- [Apply.vue](../../web/src/views/Apply.vue) and [Orgs.vue](../../web/src/views/Orgs.vue): applicant and review entry points.
- [HTTP integration scenarios](../../server/tests/test_org_signup_db.py): onboarding, abuse limits, identity and database authority.
