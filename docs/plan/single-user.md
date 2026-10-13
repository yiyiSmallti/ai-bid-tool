---
kind: plan
---

# Single-user local installation

This plan delivers [ADR 0012](../adr/0012-single-user-installation.md). The
three slices land as one change: the multi-org schema enforces co-signing and
review rounds in triggers, so removing the collaboration features without the
new schema leaves confirmation unusable. The change merges once the full test
suite and the browser suite pass.

## Slice 1: remove multi-person surfaces

Remove the API routes, services, CLI commands, console pages, navigation entries,
schemas and tests for:

- org self-service applications and the `/apply` page;
- platform org management, operators, operator enrollment, platform usage and
  platform audit;
- org member management, invitations and `/setup-password`;
- task member management, ownership handover, task archival, review-policy
  changes, requirement assignment, card discussion and co-signing.

## Slice 2: access key instead of login

- `BID_ACCESS_KEY` is a required setting of at least 32 characters.
  `POST /auth/session` exchanges it for a console session and
  `GET /auth/session` describes the caller; a bearer request that presents the
  key directly acts as the human. Comparison is constant-time.
- The console replaces the org login page with a single access-key field. The
  desktop shell opens `/app/#key=...`; the console exchanges the fragment and
  removes it from history.
- The CLI reads `BID_ACCESS_KEY`, an owner-only file named by
  `BID_ACCESS_KEY_FILE`, or a `bid_` token in `BID_TOKEN`. `bid login`, org
  selection, `X-Org-Id`, `BID_ORG` and the encrypted CLI session file are
  removed.
- Model configuration offers only the user's own provider credentials. Service
  credentials, Clef and trust anchors move to console settings pages managed by
  the human, without operator TOTP. The platform model catalog is removed.

## Slice 3: single-user baseline schema

Replace `server/migrations/versions` with one baseline revision whose SQL lives
in `server/migrations/baseline/`:

- drop `orgs`, `users`, `memberships`, `org_applications`, operator factors,
  platform audit and every collaboration table removed in slice 1;
- drop `org_id`, its indexes, RLS policies, `FORCE ROW LEVEL SECURITY` and the
  org parts of composite keys and foreign keys;
- rewrite functions and triggers that read `app.current_org` or check membership
  and roles; actor checks become checks of `app.actor_kind`;
- drop user-reference columns. A human decision keeps its `*_at` timestamp and is
  written only when `public.actor_is_human()` holds; run and audit rows keep
  `actor_kind`, token, agent session and job attribution;
- the memory and confidential-field `org` scope becomes `library`;
- platform credentials, Clef configuration and trust anchors become plain tables
  readable by the runtime role; the platform, credential and function-owner roles
  are dropped. The roles are the owner and `bid_app`.

Storage keys drop the `org/{org_id}/` prefix. Code, CLI, console and tests drop
org and user identifiers. Fixtures create no orgs; isolation tests between orgs
are deleted and human-versus-agent gate tests remain.

## Documentation

The change updates [agent.md](../../agent.md), [design.md](../design.md), the
mechanism notes and plans it touches, and the [changelog](../changelog.md).
Notes and plans for removed features are deleted.

## Out of scope

Desktop packaging, bundled runtimes and signing are a separate plan. Existing
multi-org databases are not converted.
