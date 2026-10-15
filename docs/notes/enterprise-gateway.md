# Enterprise gateway

## Problem

The same server runs as a personal installation or as a company gateway
([ADR 0013](../adr/0013-enterprise-gateway.md)). A gateway started on a personal
database, or the reverse, would apply the wrong authentication and visibility
rules to existing data, so the mode a database was initialized with is stored
and checked before either process serves anything.

## Usage

- `BID_DEPLOYMENT` selects `personal` (default) or `enterprise`. Enterprise mode
  also needs `BID_PUBLIC_URL` (an `https://` origin) at startup and
  `BID_COMPANY_NAME` for `python -m app.admin init-db`. The other enterprise
  settings and their ranges are in
  [the gateway plan](../plan/enterprise-gateway.md#deployment-mode) and
  `Settings` in [config.py](../../server/app/core/config.py).
- `init-db` records the mode on first initialization. Run again with the same
  mode it is idempotent; with the other mode it exits 4 with
  `deployment_mode_mismatch` before migrating anything.
- The API and worker exit with `deployment_mode_mismatch` (API exit status 3,
  worker 4) when the configured mode differs from the stored one.
- A gateway refuses the access key over HTTP with `access_key_not_accepted`,
  both as a bearer and at `POST /auth/session`; it serves the gateway host's
  admin command line only.

## How it works

- Migration `0004` adds the singleton `installation` table: `id` is always
  `true`, `mode` is `personal` or `enterprise`, and `company_name` is set exactly
  in enterprise mode. A trigger rejects every update and delete, including the
  owner's; the runtime role may only read it.
- A database without the row is personal: databases created before `0004`, and
  test databases after truncation. `init-db` treats a database with migration
  revisions as existing and writes `personal` for it; only a database without
  revisions takes the configured mode.
- `Database.verify_role` reads the stored mode after the role checks, so the
  API lifespan and the worker share one check
  ([deployment.py](../../server/app/core/deployment.py)).

## Pitfalls

- If a first enterprise `init-db` fails after migrating and before writing the
  row, the database counts as an existing personal one and later enterprise
  runs refuse it. Drop and recreate that empty database.
- `env_ignore_empty` turns empty settings into defaults, except
  `BID_PERSONAL_MODEL_ENDPOINTS`, where an empty value means no personal models.
- The mode never changes in place; moving a project between modes uses
  [packages](../plan/enterprise-gateway.md#packages).

## Code

- [0004_installation_mode.py](../../server/migrations/versions/0004_installation_mode.py)
- [admin.py](../../server/app/admin.py) `initialize_database`, `installation_mode`
- [db.py](../../server/app/core/db.py) `Database.verify_role`
- [auth.py](../../server/app/services/auth.py) `authenticate`, `access_key_refused`
- [test_deployment_mode.py](../../server/tests/test_deployment_mode.py)
