---
kind: howto
---

# Build and launch the macOS developer app

Build the MyGo shell with the server, console, Python runtime, PostgreSQL,
screenshot renderer and OCR language data inside the app. Supported release
platforms, installers, distribution signing, notarization and updates are
planned in [desktop packaging](../plan/desktop-packaging.md).

## Prepare the dependencies

- Use the Go version in [desktop/go.mod](../../desktop/go.mod) and Bun with the
  dependencies locked in [desktop/bun.lock](../../desktop/bun.lock).
- Staging needs `uv`, Node.js with npm, the Xcode Command Line Tools, the Rust
  toolchain that
  [scripts/build_screenshot_renderer.sh](../../scripts/build_screenshot_renderer.sh)
  uses, and network access to download Python, wheels, the PostgreSQL source
  release and the OCR data.

The toolchain cache used by this worktree can be selected from its root:

```sh
export GOROOT="$PWD/data/work/toolchains/go"
export GOPATH="$PWD/data/work/toolchains/gopath"
export GOCACHE="$PWD/data/work/toolchains/gocache"
export PATH="$GOROOT/bin:$PWD/data/work/toolchains/bun:$PATH"
```

## Build the app

From `desktop`, stage the bundled resources, then build:

```sh
scripts/stage-resources.sh
bun install --frozen-lockfile
bun run build --platform darwin/arm64 -skip-dmg -skip-notarize
```

[The staging script](../../desktop/scripts/stage-resources.sh) fills the
gitignored `desktop/resources/darwin-arm64`, which MyGo copies into
`Contents/Resources`, and fails if a staged library links outside the bundle or
macOS. Rerun it after server, dependency or console changes. MyGo copies no
hidden files, so the script renames package `.dylibs` directories and removes
other hidden files.

Staging runs [build-postgres.sh](../../desktop/scripts/build-postgres.sh), which
compiles the pinned PostgreSQL release into `pgsql`, points its libraries at
paths relative to the tree, and starts a cluster from a moved copy before
accepting it. The tree is cached under `data/work/desktop-stage/postgres` and
rebuilt only when the release or the script changes.

The app is `desktop/build/darwin-arm64/AI Bid.app`, relative to the worktree root.
MyGo applies its default ad hoc signature for local execution. These flags make
no installer and submit nothing to a notarization or release service. The bundle
stays read-only at run time: bytecode is compiled during staging and the server
writes none, so the signature stays valid after launches.

## Launch

Use a dedicated macOS development account with no existing AI Bid installation
for disposable acceptance. An ordinary launch writes the account's normal app
data and persistent WebKit storage. The runtime overrides below select software
locations; they do **not** isolate installation data.

From the worktree root, execute the bundle's binary so the environment is
inherited:

```sh
"$PWD/desktop/build/darwin-arm64/AI Bid.app/Contents/MacOS/AI Bid"
```

To run server changes without restaging, set `BID_DESKTOP_SERVER` to a checkout
whose root holds `alembic.ini`, `server/migrations`, a built `web/dist` and a
`.venv` prepared from `pyproject.toml` and `uv.lock`; the app then runs that
checkout's `.venv/bin/python`. `BID_DESKTOP_PG_BIN` likewise selects PostgreSQL
16 binaries outside the app, such as `/opt/homebrew/opt/postgresql@16/bin`. The
bundled renderer and OCR data are used in all cases.

The main window opens the task page after the shell exchanges its generated
installation access key. Do not print the key or `installation.json`. Quit with
Command-Q; the API and worker stop before PostgreSQL shuts down.

[Backend](../../desktop/backend.go) uses
`~/Library/Application Support/AI Bid` for installation keys, files, the cluster
and database backups, and `~/Library/Logs/AI Bid` for logs.
[The shell](../../desktop/main.go) puts the PostgreSQL socket in
`~/Library/Caches/io.github.yiyismallti.aibid/pg`. MyGo's macOS webview also uses
persistent storage under the app identifier. The app owns the cluster it creates
and stops it on quit. A second launch focuses the running window, and a backend
started while another process holds the data directory's `instance.lock` exits
with an error instead of touching the cluster. Do not launch against existing
multi-org data: the single-user baseline does not convert it. Failed migrations
preserve preexisting installation-file bytes; compatible startup persists staged
keys or a changed console port only when necessary.

## Restore a database backup

Before init-db migrates an existing database, the backend dumps it to
`backups/bid-<UTC time>-<revision>.dump` in the data directory and checks that
`pg_restore` can read the dump; if the backup fails, the launch stops before the
migration. The newest backup and the newest one taken at an earlier revision are
kept, so retrying a failed update replaces only its own backup. Files under
`files` are not in the dump; migrations do not change them.

To return to the app version a backup was taken for, quit the app, copy the data
directory if the current database must be kept, then recreate the database from
the dump with the bundle's PostgreSQL. Dropping the database discards every
change made after the backup.

```sh
pg="/path/to/AI Bid.app/Contents/Resources/pgsql/bin"
data="$HOME/Library/Application Support/AI Bid"
socket="$HOME/Library/Caches/io.github.yiyismallti.aibid/pg"
"$pg/pg_ctl" -D "$data/pg" -l "$HOME/Library/Logs/AI Bid/restore.log" -w start
"$pg/dropdb" -h "$socket" -p 55440 -U bid_owner bid
"$pg/pg_restore" -h "$socket" -p 55440 -U bid_owner --create -d postgres \
  "$data/backups/bid-<UTC time>-<revision>.dump"
"$pg/pg_ctl" -D "$data/pg" -m fast -w stop
```

Then launch the app version whose newest migration is the dump's revision. A
later version migrates the restored database again and backs it up first.

## Optional services

Vendor search uses the Perplexity API: add a Perplexity credential under
服务凭据 in the console; until then search reports itself unavailable. Screenshot
annotation and local OCR use the bundled renderer and language data. The
developer app configures no Office converter and no browser sandbox, so export
page previews and sandbox captures are unavailable; their desktop outcomes are
decisions D5 and D6 in [desktop packaging](../plan/desktop-packaging.md#decisions).

## Verify without using default account data

The normal app has no data-root override. A same-account acceptance build must
use a temporary physical source copy with a distinct app name and identifier,
pre-main `App.SetPath` overrides for data/log/cache/temp, the shell's socket path
redirected to that cache, and a nonpersistent `WKWebsiteDataStore`. Require a
fresh private root before backend startup, inspect markers in the packaged
binary, and run a preflight that exits before creating the cluster. Go overlays
passed in `GOFLAGS` are insufficient: MyGo's frontend build supplies its own
`-overlay` flag.

A temporary acceptance hook can use MyGo's native page evaluation to require
`/app/org/tasks`, an empty URL fragment, successful authenticated session/task
requests and the rendered task table, then call `App.Quit`. Confirm clean child
shutdown and remove the temporary source, bundle and synthetic data. This checks
an instrumented packaged app; it does not establish that the normal bundle was
launched with isolation. Keep acceptance evidence outside `docs/`.

For the backend regressions alone, stage the resources, then run from
`desktop` with a checkout prepared as for `BID_DESKTOP_SERVER`:

```sh
BID_DESKTOP_TEST_SERVER=/absolute/path/to/ai-bid-tool \
BID_DESKTOP_TEST_PG_BIN="$PWD/resources/darwin-arm64/pgsql/bin" \
go test -v -count=1 .
```

[The tests](../../desktop/backend_test.go) create disposable clusters under
`/tmp`. They check that incompatible migrations retain installation bytes and
are not backed up, and that compatible startup saves a generated access key with
a reused port while an unchanged restart retains the file's bytes and
modification time. With probe revisions added to a copy of the migrations, they
check that a second backend on the same data directory is refused, that a failed
migration keeps the prior revision and one backup, that backups rotate across
updates, and that the restore steps above bring back the previous version's
data. The `desktop` job in
[the check workflow](../../.github/workflows/check.yml) runs them on macOS with
PostgreSQL built from source.
