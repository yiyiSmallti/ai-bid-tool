---
kind: adr
---

# 0012 Single-user local installation

Status: accepted. Date: 2026-10-12. Personal installations only since
[ADR 0013](0013-enterprise-gateway.md) added enterprise gateways.

## Context

The product was built as multi-org SaaS: orgs (单位), global users, memberships,
roles, password login, a platform operator console, org self-service signup and
team collaboration inside a task. The hosted pilot is shut down. The product is
now a desktop application that one person installs and runs on their own
computer, with cloud model APIs as the only outbound dependency. Tenancy,
accounts and team workflow add schema, triggers, policies and code paths that
protect boundaries that no longer exist.

## Decision

One installation serves exactly one human user. Remove:

- orgs, users, memberships, roles, invitations and password login;
- the platform console, operators, operator enrollment, org applications and
  cross-org usage and audit views;
- task membership, ownership handover, task archival by members, requirement
  assignment, card discussion threads, comments, mentions and co-signing;
- `org_id` on every table, row-level security, org composite foreign keys and
  the `app.current_org` setting.

Supersede [ADR 0001](0001-platform-console-access.md),
[ADR 0006](0006-platform-credentials.md),
[ADR 0008](0008-org-signup.md) and [ADR 0009](0009-operator-enrollment.md).
[ADR 0010](0010-offline-signature-trust.md) keeps offline signature validation;
its trust anchors become local settings managed by the user instead of a
platform global table.

Authentication uses an installation access key. A request that presents it, or a
console session minted from it, acts as the human. Agents keep their own
identity: the built-in agent and `bid_` API tokens created by the human never
receive human-only scopes, and the database still records `app.actor_kind` and
the token, agent session and job attribution for every write. Human confirmation
remains a human-only action. With one human, separation-of-duty rules between
different people (distinct co-signers, reviewer differs from creator, domain
roles) no longer apply.

Human attribution is implicit. Columns that named a user record the decision
by its timestamp and the actor kind instead. Model configuration uses the
user's own provider credentials only; there is no platform model catalog.
Search, Clef and other service credentials are encrypted local settings.

Storage keys have no org prefix. The database keeps two roles: an owner that
runs migrations and owns every object, and a runtime role that cannot alter
tables or disable triggers. The application listens on loopback; the operating
system account is the isolation boundary.

The migration history is replaced by a new single-user baseline. Existing
multi-org data is not converted.

## Consequences

The confirmation gates, citation rules, provider layer, CLI contract and
fabrication prohibitions in [agent.md](../../agent.md) are unchanged. Two-org
isolation tests are removed; human-versus-agent gate tests remain. A process
running as the same operating system user can read the access key and act as
the human; the product does not defend against the user's own account.
Multi-person operation is decided by [ADR 0013](0013-enterprise-gateway.md). Historical
migrations, changelog entries and superseded ADRs remain in version control as
records of earlier decisions.
