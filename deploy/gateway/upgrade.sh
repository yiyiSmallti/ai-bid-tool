#!/bin/sh
# Upgrade a gateway to NEW_IMAGE (docs/guides/gateway-operations.md#upgrade):
# stop the API and worker, back up and verify the backup, rehearse the migrations
# on a clone of the database, drop the roles the rehearsal created, then migrate
# the live database and start NEW_IMAGE. Before the live migration any failure
# restarts the current image unchanged.
set -eu
[ $# -eq 1 ] || { echo "usage: $0 NEW_IMAGE" >&2; exit 2; }
new=$1
here=$(cd "$(dirname "$0")" && pwd)
env_file=${BID_GATEWAY_ENV:-$here/gateway.env}
compose() { docker compose -f "$here/compose.yml" --env-file "$env_file" "$@"; }
sql() { compose exec -T postgres psql -U bid_owner -d postgres -v ON_ERROR_STOP=1 -Atc "$1"; }
step() { echo "upgrade: $*" >&2; }
# A subshell: sh keeps assignments written before a function call after it returns.
with_new() (export BID_IMAGE="$new" && "$@")

docker image inspect "$new" >/dev/null 2>&1 || { echo "upgrade: image $new is not built" >&2; exit 2; }
rehearsal=bid_rehearsal
restart() {
  status=$?
  sql "DROP DATABASE IF EXISTS $rehearsal WITH (FORCE)" >/dev/null 2>&1 || true
  step "failed; starting the current image again"
  compose start server worker >/dev/null 2>&1 || true
  exit "$status"
}
trap restart EXIT INT TERM

step "stopping server and worker"
compose stop server worker
# The new image's client tools take the backup; the dump does not depend on the schema.
step "backing up"
backup=$(with_new compose run --rm -T backup | tail -n 1)
path=$(printf '%s' "$backup" | sed -n 's/^{"backup": "\([^"]*\)".*/\1/p')
[ -n "$path" ] || { echo "upgrade: backup reported no path" >&2; exit 4; }
step "verifying $path"
with_new compose run --rm -T backup python -m app.admin restore --verify "$path" >/dev/null

step "rehearsing migrations on a clone"
roles=$(sql "SELECT rolname FROM pg_roles ORDER BY 1")
sql "DROP DATABASE IF EXISTS $rehearsal WITH (FORCE)" >/dev/null
sql "CREATE DATABASE $rehearsal TEMPLATE bid" >/dev/null
(export BID_MIGRATION_DB=$rehearsal && with_new compose run --rm --no-deps -T migrate)
sql "DROP DATABASE $rehearsal WITH (FORCE)" >/dev/null
# Roles are cluster-wide: one a rehearsal created would already exist for the live run.
for role in $(sql "SELECT rolname FROM pg_roles ORDER BY 1"); do
  printf '%s\n' "$roles" | grep -qx "$role" && continue
  step "dropping rehearsal role $role"
  sql "DROP ROLE \"$role\"" >/dev/null
done

# From here the live schema changes; the current image must not start again.
trap - EXIT INT TERM
step "migrating the live database"
if grep -q '^BID_IMAGE=' "$env_file"; then
  sed -i.bak "s|^BID_IMAGE=.*|BID_IMAGE=$new|" "$env_file" && rm -f "$env_file.bak"
else
  printf 'BID_IMAGE=%s\n' "$new" >>"$env_file"
fi
compose up -d
step "done: $new, backup $path"
