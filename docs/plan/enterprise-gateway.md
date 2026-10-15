---
kind: plan
---

# Enterprise gateway

This plan delivers [ADR 0013](../adr/0013-enterprise-gateway.md). Personal
installations keep the behavior of the
[single-user plan](single-user.md); every slice below must leave them unchanged.
Slices land in order, each as its own change with its acceptance passing.

## Slice 0: deployment mode

- `BID_DEPLOYMENT` is `personal` (default) or `enterprise`, fixed at
  installation. A database records the mode it was initialized with, and the
  server refuses to start against a database of the other mode.
- Enterprise mode listens on a configured address behind TLS; personal mode
  keeps loopback only.

## Slice 1: accounts and sessions

- Members, two roles (administrator, member) and password sign-in with
  rate-limited attempts. The first administrator is created by an admin command
  on the gateway host; administrators invite and disable members.
- `POST /auth/session` accepts member credentials in enterprise mode and the
  access key in personal mode. Sessions and `bid_` tokens belong to one member.
- Writes record the acting member again, beside the existing actor kind, token,
  agent session and job attribution. Human confirmation records the confirming
  member. No separation-of-duty rule between members.
- Single sign-on (WeCom, DingTalk, OIDC) is a later slice, not a prerequisite.

## Slice 2: sharing rules

| Resource | Read | Write |
| --- | --- | --- |
| Material library (certificates, profiles, templates, attachments) | All members | Administrators and members granted library editing |
| Public memory | All members | Administrators and members granted memory editing; approval flow unchanged |
| Private memory | Its member | Its member |
| Tender project | Project members | Project members; the owner and administrators manage membership |
| Confidential values | Project members, masked as today | Administrators |
| Service credentials (search, Clef, trust anchors) | Used by the gateway; never shown | Administrators |

Memory retrieval for drafting reads public memory plus the acting member's
private memory.

## Slice 3: models

- Company models: configured by administrators, keys never returned, usable by
  every member.
- Personal models: added by a member with their own key, visible and usable only
  by that member.
- Runs choose among the models visible to the member; the default is the
  company default unless the member set their own. The
  [provider contract](provider-config.md) gains an owner (company or member) per
  saved model.
- Usage is recorded per member and per model; an administrator can cap a
  member's monthly company-model usage.

## Slice 4: client workspaces

- Settings gain a workspace switcher: personal (local) or an enterprise gateway
  address followed by sign-in. The client stores the gateway address and the
  session, never the password.
- In enterprise mode the client does not start its local server; uploads,
  downloads, previews and job progress go to the gateway.
- An unreachable gateway shows a connection state instead of local fallbacks.

## Slice 5: moving data

- Import: a personal project or material set is packaged with its sources,
  citations, confirmations and history, then imported into the gateway as a new
  project owned by the importing member. Hashes and citations are revalidated
  on import.
- Export to personal: off by default; an administrator enables it per project.

## Slice 6: deployment and operation

Docker Compose on the company server, TLS, scheduled backup with restore
drills, and upgrades that rehearse migrations on a database clone first. Reuse
[production deployment](production-deployment.md) where it still applies.

## Acceptance

- Two members on one gateway: one adds a certificate and a public memory entry;
  the other sees both and uses them in a draft of a shared project; a
  non-member cannot see the project.
- A member's personal model is invisible to the other member; company-model
  keys never appear in any response, log or client file.
- Confirmation records the confirming member; agents still cannot confirm.
- A personal installation passes its existing suites unchanged and cannot be
  started against an enterprise database, nor the reverse.
- Each acceptance run leaves a reproducible artifact under `data/work`.
