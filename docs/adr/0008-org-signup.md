---
kind: adr
---

# 0008 Global organization applications and manual provisioning

Date: 2026-10-07. Status: **accepted.**

## Context

Prospective orgs (organizations/tenants, 单位) need to submit contact information and
request access before an org or membership exists. An application cannot carry an
org context without inventing a tenant or creating an org before human approval.
[agent.md hard rule 1](../../agent.md#hard-rules-must-never-be-violated) therefore
requires an explicit exception, following [ADR 0006](0006-platform-credentials.md).
The existing platform operator boundary in [ADR 0001](0001-platform-console-access.md)
already provides password-and-TOTP sessions and a restricted function owner.

## Decision

The [organization application contract](../plan/org-signup.md) defines submitted
fields, decision states, abuse bounds, entry points and acceptance.

- Approve the third global-table exception, `org_applications`, without tenant
  `org_id` or RLS. It stores only prospective org onboarding fields, a keyed source
  digest, temporary password hash and decision metadata. Its nullable `org_id` is
  an approval result, not a tenant authority field. It must not store org business
  content, plaintext passwords or raw client addresses.
- `bid_app` receives no direct table privileges. Submit, list, approve, reject and
  expire use fixed `SECURITY DEFINER` functions owned by `bid_platform_fn`, with a
  fixed `search_path`, qualified table names, no dynamic SQL, revoked PUBLIC access
  and EXECUTE grants to `bid_app`. This owner remains `NOLOGIN NOSUPERUSER
  NOBYPASSRLS`; no cross-org policy is added. The public route invokes only submit;
  list and decisions require the existing platform session at the API boundary.
  Database actor text or `SET app.*` values are not platform authentication.
- Every approval locks and rechecks its application, then calls the shared
  `platform_create_org` function under the new org's ordinary RLS context. That
  function resolves a concurrent unique-email insertion without replacing a
  password. Approval rechecks the returned creation result: if an existing user
  won the race and attachment was not confirmed, its nested org creation rolls
  back and the application remains pending. An approved existing user retains
  their password and requires explicit operator attachment confirmation.
- Keep the original pending password hash on duplicate submission. Return one
  receipt without an application ID for new, duplicate and existing-user emails;
  perform bounded password hashing for each admitted request. Source-window and
  pending-cap decisions are independent of email existence and serialized across
  API replicas. The feature switch defaults off.
- A pending application expires after the contracted interval. Reads report
  overdue rows as expired immediately; expiry processing clears hashes. Every
  approved, rejected or expired row has a null hash, with decision and org-shape
  constraints enforced in the database.
- Application insertion and its `org_application.submit` audit share a
  transaction. The audit records only application ID and source digest, with a
  fixed system actor. A duplicate writes neither an application nor an audit.
  Decisions and their `platform.org_application.approve` or `reject` audit share
  a transaction and record operator, application, org and attachment outcome.
  Passwords, hashes and submitted contact fields are absent from audit details.
- Retain onboarding and decision rows until a separate retention policy is
  approved. Downgrade must preserve applications and audit history; removal
  requires a deliberate recovery or migration plan.

## Tradeoffs and consequences

A global queue permits manual vetting before tenant provisioning and adds a
narrow exception to tenant isolation. Operators can see prospective applicants'
contact data; this authority does not grant access to existing org business
content. Unverified email ownership requires out-of-band vetting before
attachment, and existing accounts must sign in with their existing passwords.

The initial flow uses no email delivery, CAPTCHA or status lookup. Abuse bounds
protect storage and CPU, while the keyed source digest avoids retaining raw
addresses. The shared submission lock serializes short admission writes at the
bounded queue size. Deployment must trust only its own proxy for client-address
resolution; arbitrary forwarded headers cannot define the source identity.
