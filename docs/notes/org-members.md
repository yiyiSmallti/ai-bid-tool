---
kind: reference
---

# Organization member management

## Problem

Organization administrators need to add colleagues and maintain their organization
roles without platform operators or database access. Global login identities must
retain their password and other memberships when attached to another organization.
The approved behavior and HTTP/CLI table live in
[the member plan](../plan/org-members.md); task participation is separately defined
in [team workflow](team-workflow.md).

## Usage

Open `/app/org/members` through 成员 in the organization console. Administrators can
add an email with a built-in role, copy the returned invitation link, change roles,
and deactivate or reactivate members. The link dialog shows the link only for that
operation; close it after sharing out of band. Other roles receive a read-only list
of active members. There is no email delivery.

Use `bid org member list`, `add --email E --role R`,
`role --user U --role R --expected-revision N`,
`set-active --user U --active true|false --expected-revision N`, and
`invite --user U` with the existing organization session. All support `--json`.
Refresh the member list after a revision conflict, review the current state and then
submit a new explicit change. Conflicted writes are never automatically retried.

## How it works

[OrgMemberService](../../server/app/services/org_members.py) implements the
[shared runtime contract](../../server/app/schemas/org_members.py) under the request's
organization RLS context. An organization advisory transaction lock serializes member
writes. The service rechecks live human administrator authority after acquiring it,
checks the expected revision and last active administrator, then performs a conditional
update. The database row guard requires the expected revision and a one-step increment
for runtime writes; it preserves member identity and creation attribution.

[The member migration](../../server/migrations/versions/0068_org_members.py) adds
revision and attribution metadata with a preserving backfill. Its fixed
`org_add_member` function is owned by `bid_platform_fn`, fixes its search path,
revokes PUBLIC execution and grants execution only to `bid_app`. The function validates
current organization and actor context, explicitly scopes its reads despite the owner's
platform read policy, and inserts under ordinary tenant RLS. A unique email race attaches
the winning user without updating the password. Runtime callers cannot directly insert
or delete memberships.

Invitation setup links reuse [setup_token](../../server/app/services/platform.py),
with the existing password-hash fingerprint and expiry. Accounts with a usable password
receive the ordinary organization login URL. Invitation reissue is available only before
password setup. Member mutations and their audit rows commit together; audit metadata
contains target identity and before/after role, active state and revision, never links.

[authenticate](../../server/app/services/auth.py) checks active organization membership
on every session and API-token request. Deactivation also revokes all of the target's
tokens in this organization in the same transaction. Reactivation retains membership and
history and does not restore revoked tokens. `member:manage` is an admin-only human
scope excluded from token issuance and constrained out of stored token scopes.

## Pitfalls

At least one active administrator must remain, including when changing oneself. A
successful self-demotion immediately removes management controls; self-deactivation
signs out the organization console. Task ownership, decisions and historical attribution
remain intact; any lost task authority is evaluated by the existing workflow gates.

A setup link is bound to the global account, so setting a password invalidates every
outstanding setup link for that account. Issuing another link does not itself invalidate
older unconsumed links. An already configured account must sign in with its existing
password and select the new organization. Existing memberships cannot be added again,
including deactivated ones; reactivate that membership instead. Migration downgrade
refuses destructive rollback; preserve attribution and audit history and repair forward.

## Code

- [Membership and ApiToken](../../server/app/models/entities.py): metadata and scope constraints.
- [Member HTTP routes](../../server/app/api/org_members.py): Result envelopes and management gates.
- [Member CLI](../../cli/bid_cli/org_members.py): commands and contract validation.
- [OrgMembers.vue](../../web/src/views/OrgMembers.vue): administrator and read-only views.
- [HTTP/PostgreSQL acceptance](../../server/tests/test_org_members_db.py): onboarding, concurrency, revocation and RLS.
- [Mocked browser acceptance](../../web/e2e/org-members.spec.js): console actions and conflict recovery.
