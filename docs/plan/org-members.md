---
kind: plan
---

# Organization member management

Status: **Approved; implemented**.

This contract covers the member-management part of F04 in the [roadmap](roadmap.md): an
org (organization/tenant, 单位) admin adds colleagues, changes their roles and
deactivates them in the org console, without platform operators or database access. The
importable [Pydantic v2 contract](org-members/org_members_contracts.py) defines payloads,
Result data and the service Protocol; it registers nothing. Implementation follows the
approved [decisions](#open-decisions) under [agent.md](../../agent.md#workflow).

## Goal and scope

An org exists with one admin after [self-service application](org-signup.md) or platform
creation, and nothing in the product can add a second person. Task membership
(`/tasks/{task_id}/members`) assigns existing org members to tasks; it cannot create them.

In scope: list members, add a member by email with a role, issue a one-time invitation
link, change a role, deactivate and reactivate, and audit. Excluded: email delivery,
SSO/OIDC, self-service join requests, seat limits or billing per seat, transferring an
org, deleting users, and changing the four built-in roles or their scopes.

## Verified code foundations

| Current code anchor | Behavior this contract builds on |
| --- | --- |
| `User`, `Membership` in [entities.py](../../server/app/models/entities.py) | Global `users` with unique email; `memberships` per org with `role` in admin/bidder/technical/viewer and `active`, under org RLS. |
| `ROLE_SCOPES`, `HUMAN_ONLY_SCOPES` in [auth.py](../../server/app/services/auth.py) | Role scopes and the scopes API tokens may never hold. |
| `setup_token`, `setup_password` in [platform.py](../../server/app/services/platform.py); [SetupPassword.vue](../../web/src/views/SetupPassword.vue) | Signed, unstored setup links bound to the password-hash fingerprint, 24-hour lifetime, dead after use. |
| `platform_create_org` in [0059_org_signup.py](../../server/migrations/versions/0059_org_signup.py) | User rows are inserted by restricted functions; an existing user is attached by email without touching the password. |
| Task members in [task_workflow.py](../../server/app/services/task_workflow.py) and [OrgTaskMembers.vue](../../web/src/views/OrgTaskMembers.vue) | Task participation and review domains for active org members. |

## Behavior

1. **List.** Admins see every membership of the current org: email, role, active,
   whether the account has set a password, added by/at and the membership revision.
   Other roles see active members' emails and roles only, which task assignment needs.
2. **Add.** An admin enters an email and a role. If the email has no account, a user is
   created with an unusable password; if it has one, only the membership is created and
   the existing password and other memberships are untouched. Either way the response
   is the same shape and always contains a one-time **invitation link**: for an account
   without a password it opens the existing set-password page; for an account with one
   it opens the org sign-in page. The admin gives the link to the colleague out of band.
   Adding an email that is already a member of this org returns a conflict.
3. **Re-issue link.** An admin can issue a fresh link for a member who has not set a
   password yet; the previous link stops working once any password is set.
4. **Change role.** Revision-checked (CAS). The org must always keep at least one active
   admin: demoting or deactivating the last active admin is refused, including oneself.
5. **Deactivate / reactivate.** Deactivation blocks the member's next request in this
   org (membership is checked per request), revokes their API tokens for this org, and
   keeps their task history, decisions and audit attribution. Reactivation restores the
   same membership; revoked tokens stay revoked.
6. **Audit.** Every add, link issue, role change and activation change writes an org
   audit row with actor, target user id, old and new values; links are never logged.

## Data and authority

No new table. `memberships` gains `revision`, `created_by` and `updated_at` columns
(migration with backfill: revision 1, created_by NULL for existing rows). Inserting a
user goes through a fixed `SECURITY DEFINER` function owned by the existing
`bid_platform_fn` role (as `platform_create_org` does), callable by `bid_app`, which
creates or finds the user by email and inserts the membership under the current org's
RLS context. A new human-only scope `member:manage` is granted to the admin role only;
API tokens can never hold it.

## HTTP, CLI and console

| HTTP | CLI | Result data |
| --- | --- | --- |
| `GET /org/members` | `bid org member list` | items of `OrgMemberView` |
| `POST /org/members` | `bid org member add --email E --role R` | `OrgMemberInvited` |
| `POST /org/members/{user_id}/role` | `bid org member role --user U --role R --expected-revision N` | `OrgMemberView` |
| `POST /org/members/{user_id}/active` | `bid org member set-active --user U --active true\|false --expected-revision N` | `OrgMemberView` |
| `POST /org/members/{user_id}/invitation` | `bid org member invite --user U` | `OrgMemberInvited` |

Console: a 成员 page in the org console with the member table, 添加成员 (email + role),
a one-time link dialog with a copy button, role select and 停用/启用 actions with
confirmation, and the last-admin rule explained inline.

## Failure modes

| Condition | Behavior |
| --- | --- |
| Caller is not an active admin | 403 `forbidden`; tokens always 403. |
| Email already a member of this org | 409 `member_exists`. |
| Stale revision | 409 `revision_conflict`. |
| Would leave no active admin | 409 `last_admin_required`. |
| Invitation for a member who already set a password | 409 `password_already_set`; they sign in normally. |
| Invalid email or role | 400 `invalid_input`. |

## Acceptance

End-to-end through HTTP and the browser against PostgreSQL: add a new email, open the
link, set a password, sign in with the assigned role; add an existing user of another
org and confirm their password and other membership are unchanged and they can switch
to this org; change roles with CAS; refuse demoting or deactivating the last admin;
deactivate a member and show their next request and API tokens are refused while their
task history remains; reactivate; confirm non-admins and tokens cannot manage members
and that RLS keeps another org's members invisible.

## Open decisions

| Topic | Recommended default | Approval consequence |
| --- | --- | --- |
| Who manages members | Org admins only (`member:manage`, human-only) | Bidder/technical/viewer cannot add or change people. |
| Joining | Admin adds by email and passes a one-time link; no email delivery | Same out-of-band pattern as org creation and operator enrollment. |
| Existing accounts | Attach the membership; never change their password or reveal other orgs | One person can belong to several orgs with one password. |
| Last admin | At least one active admin always | An org cannot lock itself out. |
| Deactivation | Blocks access and revokes this org's API tokens; history kept; reversible | No deletion of users or attribution. |
| Visibility | Admins see full member records; other roles see active members' emails and roles | Task assignment keeps working for owners who are not admins. |
| Seats | No limit | Billing per seat would need its own contract. |
