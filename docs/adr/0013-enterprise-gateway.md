---
kind: adr
---

# 0013 Enterprise gateway beside personal installations

Status: accepted. Date: 2026-10-15.

## Context

[ADR 0012](0012-single-user-installation.md) made every installation serve one
person with data on that person's computer. Companies that bid as a team need
one shared library of materials, shared tender projects and shared public
memory, which a set of separate personal installations cannot provide. Personal
users still want their data to stay local.

Three ways to share were considered:

- **Local-first replication.** Every client keeps a full copy and synchronizes.
  Human confirmation, audit chains and verbatim citation checks assume one
  authoritative copy of each record; merging concurrent edits would weaken all
  three. Rejected.
- **One hosted service for many companies.** Restores `org_id`, row-level
  security and a platform operator console, which ADR 0012 removed. Worth its
  cost only for a hosted multi-company product. Not now.
- **One private deployment per company.** The same server runs either inside
  the desktop client for one person or on a company server for its members.
  Chosen.

## Decision

The product has two deployment modes of one server code base:

- **Personal.** Unchanged from ADR 0012: one person, local data, access-key
  authentication, loopback only.
- **Enterprise.** The company deploys a gateway privately on its own server.
  One gateway serves exactly one company; there is no tenant column and no
  cross-company console. Members sign in with gateway accounts. Materials,
  tender projects, public memory, service credentials and background jobs live
  on the gateway, which is the single authoritative copy. Enterprise mode has no
  offline editing.

The desktop client selects a workspace: the local personal installation or one
enterprise gateway. Switching workspaces leaves the local data untouched.

Models: the gateway administrator configures company models whose keys members
never see, and each member may add personal models with their own keys. Both
kinds are dispatched by the gateway and recorded per member. Personal models
are visible only to the member who added them.

Human confirmation stays a human-only action and records which member confirmed.
Separation of duty between members (distinct co-signers, reviewer differs from
creator, domain roles) is not restored. Agents keep their own identity and
never receive human-only scopes.

Data moves from personal to enterprise by importing a project or material
package. Exporting an enterprise project to a personal installation is off by
default; the administrator may allow it per project.

## Consequences

ADR 0012 still governs personal installations. Enterprise mode brings back
accounts, sessions, two member roles (administrator and member), project
membership and actor attribution columns, gated by deployment mode so personal
installations keep the single-user schema behavior. The provider layer, CLI
contract, citation rules and fabrication prohibitions in
[agent.md](../../agent.md) are unchanged. Deployment, TLS, backup and upgrade of
a gateway become product concerns again. A hosted multi-company service would
require a new decision.
