---
kind: plan
---

# Organization self-service application

Status: **Approved; implemented**.

This contract covers the self-service part of F04 in the [roadmap](roadmap.md): a
prospective org (organization/tenant, 单位) applies on the public sign-in page and a
platform operator approves or rejects the application in the platform console. The
importable [Pydantic v2 contract](org-signup/org_signup_contracts.py) defines payloads,
Result data and the service Protocol. The runtime copy is [org_signup.py](../../server/app/schemas/org_signup.py);
implementation details are in [the mechanism note](../notes/org-signup.md).
Implementation follows the approved [decisions](#open-decisions) under
[agent.md](../../agent.md#workflow). Org member management is a separate contract.

## Goal and scope

Today an org exists only after an operator runs `platform org create` or the
`app.admin bootstrap` command, and the new admin receives a 24-hour password-setup
link through a channel outside the application. The application has no email
delivery. Self-service application removes the operator's data entry while keeping a
human approval gate: no org, user or membership exists until an operator approves.

In scope: a public application form, bounded abuse controls, a platform review queue,
approve/reject decisions with audit, creation of the org, its first admin user and the
admin membership on approval, and expiry of undecided applications.

Excluded: email or SMS delivery and verification, CAPTCHA services, automatic
approval, org member invitations, payment at sign-up, applicant self-service status
lookup, editing an application after submission and SSO/OIDC.

## Verified code foundations

| Current code anchor | Behavior this contract builds on |
| --- | --- |
| `platform_create_org` in [0010_platform_admin.py](../../server/migrations/versions/0010_platform_admin.py); `create_org` in [platform.py](../../server/app/services/platform.py) | Creates the org and an `admin` membership under the new org's RLS context, reusing an existing `users` row by email. A new user gets an unusable password and a setup link. |
| `PasswordAttempts` in [password_attempts.py](../../server/app/core/password_attempts.py) | Bounded PBKDF2 worker pool and source/account failure windows for sign-in. Application hashing reuses its admission so form submissions cannot exhaust CPU. |
| `setup_password` in [platform.py](../../server/app/services/platform.py); `hash_password` in [security.py](../../server/app/core/security.py) | Passwords need at least 12 characters and are stored as PBKDF2 hashes. |
| `User`, `Org`, `Membership` in [entities.py](../../server/app/models/entities.py) | `users.email` is globally unique; `orgs.active` gates org use; roles are `admin`, `bidder`, `technical`, `viewer`. |
| `PlatformOrgCreate` in [platform_contracts.py](../../server/app/schemas/platform_contracts.py); `/platform/orgs` in [platform.py](../../server/app/api/platform.py); [Orgs.vue](../../web/src/views/Orgs.vue) | Operator org creation input, route and console page that the review queue extends. |
| Uvicorn `--proxy-headers` with a single trusted proxy address | `request.client.host` is the real client address only when the deployment trusts exactly its own TLS proxy; source limits depend on it. |

## Application flow

1. The sign-in page links to `/app/apply`. The applicant enters org name, contact name,
   email, optional phone, optional purpose note and a password (entered twice in the
   form; the server receives it once).
2. `POST /auth/org-applications` validates the input, hashes the password through the
   bounded password pool and stores a `pending` application. The response is the same
   receipt whether or not the email already has an account or a pending application,
   so the form cannot be used to enumerate registered emails.
3. The page tells the applicant that, once approved, they sign in with the same email
   and password. There is no status lookup; the operator contacts the applicant through
   the supplied email or phone when needed.
4. The platform console shows a pending count on the 单位 page and a review queue with
   the application fields, whether the email already belongs to a user, and how many
   applications came from the same source in the last 24 hours.
5. **Approve** calls the existing org-creation function in one transaction:
   - If no user has the email, create the user with the stored password hash, so the
     applicant can sign in immediately. No setup link is issued.
   - If a user already has the email, approval requires `attach_existing_user=true`.
     The existing password is never replaced; the stored hash is discarded, and the
     existing user becomes admin of the new org. The console warns that the applicant
     must already own that account.
   - The operator may correct the org name before approving. The org starts active with
     a zero prepaid balance.
6. **Reject** records an internal reason. The applicant is not notified by the
   application.
7. A pending application that is not decided within 30 days becomes `expired` and
   cannot be approved.

Every terminal state (approved, rejected, expired) clears the stored password hash.
A daily worker task expires overdue applications and clears their hashes; reads also
treat an overdue `pending` row as expired.

## Abuse and privacy controls

| Control | Recommended bound |
| --- | --- |
| Feature switch | `BID_ORG_SIGNUP_ENABLED`, off by default; when off, the route returns 404 and the sign-in page hides the link. |
| Per-source window | 5 accepted submissions per client address per 24 hours; more return 429 without storing anything. Addresses are stored only as a keyed SHA-256 digest. |
| Pending cap | At most 200 pending applications platform-wide; beyond that the route returns 503 `signup_busy` until operators decide or applications expire. |
| Duplicate email | At most one pending application per email. A duplicate submission returns the same receipt and changes nothing, so the first password stays authoritative. |
| Password | 12–1,024 characters, hashed with the existing PBKDF2 parameters; never logged, audited or returned. |
| CPU admission | Hashing uses the shared password pool; when its queue is full the route returns 503 `auth_busy` and stores nothing. |
| Field limits | Org name 1–200, contact name 1–100, email 3–254, phone up to 40, note up to 500 characters; control characters rejected; text is shown escaped. |
| Audit | Submission writes `org_application.submit` with the application ID and source digest only. Decisions write platform audit rows `platform.org_application.approve` / `reject` with operator email, application ID, org ID and whether an existing user was attached. |

Application rows hold personal data of people who are not yet users. Approved rows
keep the submitted fields as the org's onboarding record; rejected and expired rows
keep them for audit until a retention policy is approved (see
[Open decisions](#open-decisions)).

## Data and authority

`org_applications` is a global table, like `users` and `platform_models`, because an
application has no org yet. It is the third such exception and needs its own ADR at
implementation time, as [ADR 0006](../adr/0006-platform-credentials.md) did for
platform credentials.

| Column group | Content |
| --- | --- |
| Identity | `id` UUID, `status` in `pending`/`approved`/`rejected`/`expired`, `created_at`, `expires_at` |
| Submitted | `org_name`, `contact_name`, `email` (lowercased), `phone`, `note`, `password_hash` (nullable, cleared on any terminal state), `source_digest` |
| Decision | `decided_at`, `decided_by` (operator email), `decision_reason`, `org_id`, `admin_user_id`, `user_created`, `attached_existing_user` |

Constraints: partial unique index on `email` where `status = 'pending'`; check that a
terminal row has no password hash and a decided row has decision fields; check that
only approved rows have `org_id`. `bid_app` reaches the table only through
`SECURITY DEFINER` functions owned by `bid_platform_fn`, matching
`platform_create_org`: submit, list, approve, reject and expire. The public route can
call only the submit function. Approval locks the application row, re-checks the
status and the email's current user state, and calls the org-creation logic in the
same transaction, so a concurrent approval or a user created in between cannot produce
two orgs or overwrite a password.

## HTTP, CLI and console

| HTTP | CLI | Result data |
| --- | --- | --- |
| `POST /auth/org-applications` (public) | none | `OrgApplicationReceipt` |
| `GET /platform/org-applications?status=&limit=&before=` | `platform org application list [--status S] [--limit N]` | items of `OrgApplicationView` |
| `POST /platform/org-applications/{id}/approve` | `platform org application approve ID [--org-name NAME] [--attach-existing-user]` | `OrgApplicationDecision` |
| `POST /platform/org-applications/{id}/reject` | `platform org application reject ID --reason TEXT` | `OrgApplicationDecision` |

Platform routes require the existing operator session (password plus TOTP). Results
use the existing seven-key Result envelope. Console additions: an `/app/apply` public
form, a link on both sign-in pages, a 待审核 badge and an 申请 tab on
`/app/platform/orgs` with approve/reject dialogs.

## Failure modes

| Condition | Behavior |
| --- | --- |
| Signup disabled | 404 on the public route; platform routes still list and decide existing rows. |
| Invalid field, weak password | 400 `invalid_input` / `weak_password`; nothing stored. |
| Source window exceeded | 429 `too_many_attempts`; nothing stored. |
| Pending cap reached or password pool busy | 503 `signup_busy` / `auth_busy`; nothing stored. |
| Decision on a non-pending or expired row | 409 `application_not_pending`; no change. |
| Email belongs to a user and attach not confirmed | 409 `existing_user_requires_attach`; application stays pending. |
| Org creation fails | Transaction rolls back; application stays pending with its hash. |

## Acceptance

End-to-end through the HTTP API and the browser console against PostgreSQL:

- Submit → approve → applicant signs in with the submitted password and sees an
  empty org as `admin`; the application row has no password hash.
- Submit with an email that already has a user → same receipt; approval without the
  attach flag fails, with it the existing password still works and the stored hash is
  gone.
- Duplicate pending email, sixth submission from one source, pending cap and busy
  password pool each return their documented response and store nothing.
- Two concurrent approvals of one application create exactly one org.
- Reject and expiry clear the hash; expired rows cannot be approved.
- Signup disabled hides the form link and returns 404.
- Public responses never differ by whether an email exists (body and status), and the
  password never appears in logs, audit rows or Result payloads.

## Open decisions

| Topic | Recommended default | Approval consequence |
| --- | --- | --- |
| Approval model | Manual operator approval of every application | No org exists without an operator decision. |
| Password at application | Applicant sets the password when applying; no setup link | Works without email delivery; the email is not verified, so the operator vets applicants. |
| Existing email | Approval requires an explicit attach flag and never changes the existing password | Prevents takeover of an existing account through an application. |
| Abuse bounds | 5 per source per 24 h, 200 pending platform-wide, 30-day expiry, one pending per email | Raising them is a configuration change after measuring real traffic. |
| CAPTCHA | None in the first slice | If spam appears, a separate decision adds a challenge provider such as Cloudflare Turnstile. |
| Feature switch | `BID_ORG_SIGNUP_ENABLED` off by default; the pilot deployment turns it on | Other deployments keep operator-only org creation. |
| Applicant notice | No email; the receipt page explains sign-in after approval | Operators contact applicants out of band when needed. |
| Retention | Keep application fields of approved, rejected and expired rows without automatic deletion in the first slice; always clear password hashes | A later policy decides deletion periods for rejected and expired applicant data. |
| Terms of service | No terms acceptance checkbox in the first slice | Add one once terms text exists. |
