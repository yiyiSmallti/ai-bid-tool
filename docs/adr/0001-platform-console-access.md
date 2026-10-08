---
kind: adr
---

# 0001 Cross-org access for the platform operator console (平台运营后台)

Date: 2026-10-01. Status: accepted.

## Context

Platform operators need to provision and disable orgs (organizations/tenants, 单位), maintain platform-billed models, and view usage and receivables by org.
[agent.md](../../agent.md#hard-rules-must-never-be-violated) requires `org_id` and mandatory RLS on every business table and forbids database roles that bypass RLS.
The [design document](../design.md#multi-tenancy-and-permissions) requires platform administrators to have no access to org business data.

## Decision

- Cross-org reads use the `NOLOGIN NOSUPERUSER NOBYPASSRLS` role `bid_platform_fn`. It has unrestricted `FOR SELECT` policies only on `orgs`, `memberships`, and `usage_records`, and serves only as the owner of four `SECURITY DEFINER` functions: org summaries, usage summaries, org provisioning, and org enable/disable. Functions return only fixed summary columns. The runtime role `bid_app` can execute these functions; its own queries remain subject to org policies.
- Provisioning and enable/disable writes set `app.current_org` to the target org inside the function, use ordinary org policies, and clear the context afterward.
- Add two global tables: `platform_models` (runtime read/write, no deletion; disable instead of deleting so usage records can always reference a model) and `platform_audit_logs` (append and read only).
- The platform administrator allowlist and each administrator's TOTP secret come only from deployment configuration. [ADR 0009](0009-operator-enrollment.md#decision) amends the TOTP source requirement to permit encrypted database factors while retaining the deployment-only allowlist. The application cannot elevate its own privileges. Platform sessions, org sessions, and API tokens are not interchangeable; platform sessions last 30 minutes.
- TOTP replay prevention and login rate limiting use the platform audit table without another table. Password-setting links are signed tokens bound to a fingerprint of the current password hash. They become invalid after use and are not stored in the database.

## Tradeoffs

- Superuser-owned functions and `BYPASSRLS` roles were rejected: they can read everything, and exposure depends on consistently correct function implementations. A dedicated role with policies per table and command limits cross-org access to reads on three tables.
- Direct cross-org policies on `bid_app` were rejected because a defect in any org request could expose other orgs.
- Functions are executable by `bid_app`, so application-level platform identity checks remain necessary. Every platform operation is audited for investigation.
- Keeping administrators in configuration rather than the database requires a restart to add or remove administrators, but neither application code nor database writes can create a platform administrator.
- Audit-based rate limiting means failed attempts against an administrator's email can temporarily lock that account for 15 minutes. This is accepted as a cost of brute-force protection.
