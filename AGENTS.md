# Repository instructions

Read [agent.md](agent.md) and its referenced design document before changing
this project. `agent.md` is the authoritative project instruction file.
Remaining scope, known defects, and open decisions are in
[docs/plan/roadmap.md](docs/plan/roadmap.md); local commands are in
[docs/guides/development.md](docs/guides/development.md).

## Documentation convention

Markdown in this repository follows the Seiso Convention 0.2.0:
https://seiso.fog.moe/0.2.0/convention

- Every document has one `kind`, declared in frontmatter or assigned by the
  path mapping in [seiso.toml](seiso.toml): readme, howto, reference, runbook,
  agents, adr, plan, or changelog; `generated` is assigned only by mapping.
  Hold only what that kind is for; a how-to gives steps, a reference gives
  definitions, an ADR gives the reasons.
- Each fact has one home. Link to it instead of restating it.
- Long-lived pages state requirements and point to sources. They do not
  record the current version, deployment state, commit id, or count.
- A pointer names a file, symbol, section, or document, never "the source"
  or a repository root.
- Do not address the person who asked for the document or describe how it
  was written.
- Where the checker in use reports a finding that does not apply, write
  `<!-- seiso: allow CODE -- reason -->` with that finding's complete code
  and a reason a reviewer can evaluate. Without a checker, or without a
  finding to name, satisfy the requirement instead.
- Record what shipped in [docs/changelog.md](docs/changelog.md). Do not add
  verification logs, screenshots, or evidence files under `docs/`.
