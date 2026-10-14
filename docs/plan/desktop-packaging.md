---
kind: plan
---

# Desktop packaging

This plan turns the [macOS developer app](../guides/desktop-development.md) into
an installation that a person without a development toolchain can download,
open and keep updated. It follows
[ADR 0012](../adr/0012-single-user-installation.md): one human per installation,
loopback-only services, and cloud model APIs as the only required outbound
dependency. The [decisions](#decisions) below are accepted.

## What the installation must contain

The [backend](../../desktop/backend.go) starts PostgreSQL, runs migrations, then
starts the API and the job worker. Each component comes from the bundle; the
[staging script](../../desktop/scripts/stage-resources.sh) builds all but
PostgreSQL, which `BID_DESKTOP_PG_BIN` still supplies.

| Component | Packaged requirement |
| --- | --- |
| Python interpreter and locked dependencies | Relocatable interpreter and installed wheels inside the bundle; no `uv`, compiler or network at run time |
| Server code, `alembic.ini`, baseline SQL | Copied into the bundle at build time |
| Console | Built into the bundle |
| PostgreSQL 16 server and client tools | Relocatable binaries and libraries inside the bundle |
| Screenshot renderer | Built for each target architecture and bundled |
| OCR language data | `chi_sim` and `eng` data bundled; see [local OCR](../guides/development.md#check-local-ocr) |

Optional services that the Compose stack provides do not exist on a desktop:
the Office converter (`BID_CONVERTER_URL`), SearXNG search and the gVisor browser
[sandbox](sandbox.md). Vendor search uses the Perplexity API instead of SearXNG,
selected by the backend. The converter and the sandbox each need an explicit
desktop decision; a feature whose service is absent must report itself
unavailable rather than fail late.

## Requirements independent of the decisions

- Installation data stays in the per-user locations the backend already uses
  and survives app replacement and uninstall of the bundle.
- Before a migration changes an existing database, the app writes a restorable
  backup and keeps the previous one; a failed migration leaves the prior
  database and installation file usable, as the backend already guarantees for
  installation-file bytes.
- A second launch focuses the running instance instead of starting a second
  cluster.
- Every nested executable and library is signed with the same identity as the
  app, so library validation needs no exception.
- Logs contain no access key, provider credential or document text.
- The packaged app is accepted on a clean macOS account or virtual machine with
  no Homebrew, Python or PostgreSQL installed, and the acceptance evidence stays
  outside `docs/`.

## Decisions

Each row records the accepted option.

| ID | Question | Options | Decision |
| --- | --- | --- | --- |
| D1 | Target platforms for the first release | macOS arm64 only; macOS arm64 and x86_64; macOS and Windows together | macOS arm64 only; add others as later slices |
| D2 | Runtime distribution | Everything inside the app bundle; small app that downloads runtimes on first launch | Everything inside the bundle: offline install, one signature and notarization unit |
| D3 | Python runtime | Standalone relocatable CPython with wheels installed at build time; frozen executable (PyInstaller-style) | Standalone relocatable CPython: keeps the module layout migrations and workers expect |
| D4 | PostgreSQL binaries | Build from source with bundle-relative library paths; repackage a vendor distribution | Build from source in CI, so the build controls library paths and signing |
| D5 | Office conversion without containers | Call a user-installed LibreOffice directly; bundle LibreOffice; mark conversion unavailable | Detect a user-installed LibreOffice through a new local converter provider; otherwise unavailable |
| D6 | Browser sandbox on desktop | Unavailable in desktop builds; require a user-managed Linux VM with gVisor | Unavailable in the first release; the sandbox's containment requirements are not relaxed |
| D7 | Installer form | Disk image with drag-to-Applications; flat installer package | Disk image, which MyGo's build already produces |
| D8 | Signing identity and notarization | Apple Developer Program account and Developer ID Application certificate owned by the project; ad hoc signature for testers only | Developer ID with hardened runtime and notarization; testers can use ad hoc builds meanwhile |
| D9 | Updates | Manual download of each release; in-app update feed | Manual download first, with the version shown in settings; an update feed needs its own signing-key plan |
| D10 | Windows | Second release after macOS acceptance; not planned | After macOS acceptance, with its own plan for PostgreSQL connection transport, code-signing certificate and installer |

## Delivery slices

1. **PostgreSQL.** Bundle relocatable PostgreSQL binaries; add pre-migration
   backup and single-instance handling.
2. **Optional services.** Implement the D5 and D6 outcomes and the console's
   unavailable states.
3. **Signing and notarization.** Sign every nested binary, notarize and staple
   the disk image; verify with `codesign --verify --deep --strict` and
   `spctl --assess` on a clean machine.
4. **Release procedure.** A runbook for building, notarizing and publishing a
   release, and the changelog entry for the first installable version.

## Out of scope

Multi-user or hosted operation, conversion of multi-org databases, and relaxing
any confirmation, citation or sandbox containment rule in
[agent.md](../../agent.md).
