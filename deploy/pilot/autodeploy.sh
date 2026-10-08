#!/bin/bash
# Pull-based continuous deployment for a single Docker Compose host.
#
# Deploys the newest main commit whose required CI checks passed. The host needs no
# repository credentials and GitHub holds no host credentials. Layout under $ROOT:
#   compose.override.yml, secrets/bid.env   host configuration (never replaced)
#   releases/<sha>/                         extracted sources, src -> current release
#   web/dist/                               console assets served by the API
#   backups/                                pg_dump before every migrating deploy
#   deploy-state/{deployed,failed,sandbox-attention}
set -euo pipefail
umask 022

ROOT=${BID_DEPLOY_ROOT:-/opt/bid-tool}
REPO=${BID_DEPLOY_REPO:-yiyiSmallti/ai-bid-tool}
CHECKS=${BID_DEPLOY_CHECKS:-python web}
HEALTH_URL=${BID_DEPLOY_HEALTH_URL:-https://bid.smallti.com:8443/health}
HEALTH_RESOLVE=${BID_DEPLOY_HEALTH_RESOLVE:-bid.smallti.com:8443:127.0.0.1}
KEEP_RELEASES=${BID_DEPLOY_KEEP_RELEASES:-4}
KEEP_BACKUPS=${BID_DEPLOY_KEEP_BACKUPS:-14}
# Host-local package mirrors; empty values leave the sources unchanged.
PYPI_FILES_MIRROR=${BID_DEPLOY_PYPI_FILES_MIRROR-https://mirrors.cloud.tencent.com/pypi/}
PIP_INDEX=${BID_DEPLOY_PIP_INDEX-https://mirrors.cloud.tencent.com/pypi/simple}
# Paths built into the sandbox image or the supervisor on the separate execution node.
SANDBOX_PATHS="deploy/sandbox.Dockerfile deploy/sandbox-seccomp.json scripts/sandbox_supervisor.py
server/app/sandbox server/app/providers/sandbox_runtime.py server/app/core/errors.py
server/app/core/pdf_raster.py"

cd "$ROOT"
STATE=$ROOT/deploy-state
mkdir -p "$STATE" releases backups
exec 9>"$STATE/lock"
flock -n 9 || exit 0

log() { echo "$(date -Is) $*"; }
compose() {
  sudo -n docker compose -p bidtool -f "$1/deploy/docker-compose.yml" -f compose.override.yml \
    --env-file secrets/bid.env "${@:2}"
}

deployed=$(cat "$STATE/deployed" 2>/dev/null || true)
sha=$(curl -fsS -m 30 -H "Accept: application/vnd.github.sha" "https://api.github.com/repos/$REPO/commits/main")
[[ $sha =~ ^[0-9a-f]{40}$ ]] || { log "unexpected main ref: ${sha:0:80}"; exit 1; }
[ "$sha" = "$deployed" ] && exit 0
# A failed commit is not retried; the next commit on main gets a fresh attempt.
[ "$sha" = "$(cat "$STATE/failed" 2>/dev/null || true)" ] && exit 0

# CI runs on pull requests only, so a main commit is deployable when it is the merge
# commit of a pull request whose final head passed the required checks. Direct pushes
# to main have no such pull request and are never deployed.
head=$(curl -fsS -m 30 "https://api.github.com/repos/$REPO/commits/$sha/pulls" | python3 -c '
import json, sys
sha = sys.argv[1]
for pr in json.load(sys.stdin):
    if pr.get("merged_at") and pr.get("merge_commit_sha") == sha:
        print(pr["head"]["sha"])
        break
' "$sha")
[[ $head =~ ^[0-9a-f]{40}$ ]] || { log "${sha:0:12} is not a merged pull request commit; not deploying"; exit 0; }
runs=$(curl -fsS -m 30 "https://api.github.com/repos/$REPO/commits/$head/check-runs?per_page=100")
ci=$(python3 -c '
import json, sys
runs = {}
for run in json.loads(sys.argv[1])["check_runs"]:
    runs.setdefault(run["name"], run)  # newest first
states = []
for name in sys.argv[2].split():
    run = runs.get(name)
    if run is None or run["status"] != "completed":
        states.append("pending")
    else:
        states.append("success" if run["conclusion"] == "success" else "failure")
print("failure" if "failure" in states else "pending" if "pending" in states else "success")
' "$runs" "$CHECKS")
case $ci in
  pending) log "waiting for CI on ${sha:0:12}"; exit 0 ;;
  failure) log "CI failed on ${sha:0:12}; not deploying"; echo "$sha" > "$STATE/failed"; exit 0 ;;
esac

fail() {
  log "deploy of ${sha:0:12} failed: $*"
  echo "$sha" > "$STATE/failed"
  exit 1
}

log "deploying ${sha:0:12} (was ${deployed:0:12})"
rel=releases/$sha
rm -rf "$rel" "$rel.tmp"
mkdir -p "$rel.tmp"
curl -fsSL -m 900 --retry 3 "https://codeload.github.com/$REPO/tar.gz/$sha" \
  | tar -xz -C "$rel.tmp" --strip-components=1 || fail "source download"
mv "$rel.tmp" "$rel"

# Host-local mirror rewrites keep hashes intact: uv verifies every file against uv.lock.
if [ -n "$PYPI_FILES_MIRROR" ]; then
  sed -i "s#https://files.pythonhosted.org/#$PYPI_FILES_MIRROR#g" "$rel/uv.lock"
fi
if [ -n "$PIP_INDEX" ]; then
  sed -i "s#pip install --no-cache-dir uv==#pip install --no-cache-dir -i $PIP_INDEX uv==#" "$rel/deploy/Dockerfile"
  grep -q -- "-i $PIP_INDEX uv==" "$rel/deploy/Dockerfile" || fail "Dockerfile mirror rewrite did not apply"
fi

previous=
[ -n "$deployed" ] && [ -d "releases/$deployed" ] && previous=releases/$deployed
new_migrations=
sandbox_changed=
if [ -n "$previous" ]; then
  new_migrations=$(comm -13 <(ls "$previous/server/migrations/versions") \
    <(ls "$rel/server/migrations/versions") | tr '\n' ' ')
  for path in $SANDBOX_PATHS; do
    diff -rq "$previous/$path" "$rel/$path" >/dev/null 2>&1 || sandbox_changed+="$path "
  done
else
  new_migrations=unknown
fi

log "building console"
(cd "$rel/web" && npm ci --no-audit --no-fund --loglevel=error && npm run build >/dev/null) \
  || fail "console build"

log "building images"
for service in migrate server worker; do
  if sudo -n docker image inspect "bidtool-$service:latest" >/dev/null 2>&1; then
    sudo -n docker tag "bidtool-$service:latest" "bidtool-$service:prev"
  fi
done
compose "$rel" build migrate server worker >"$STATE/build.log" 2>&1 \
  || fail "image build (see deploy-state/build.log)"

if [ -n "$new_migrations" ]; then
  backup=backups/$(date +%Y%m%dT%H%M%S)-${deployed:0:12}.dump
  log "backing up database to $backup before migrations: $new_migrations"
  compose "$rel" exec -T postgres pg_dump -U bid_owner -d bid -Fc > "$backup" || fail "database backup"
  [ -s "$backup" ] || fail "empty database backup"
fi

rollback() {
  if [ -n "$new_migrations" ] || [ -z "$previous" ]; then
    fail "$1; not rolled back because the schema may have changed (backup kept)"
  fi
  log "rolling back to ${deployed:0:12}: $1"
  for service in migrate server worker; do
    sudo -n docker tag "bidtool-$service:prev" "bidtool-$service:latest"
  done
  compose "$previous" up -d --no-build migrate server worker >/dev/null 2>&1 || true
  rsync -a --delete "$previous/web/dist/" web/dist/ 2>/dev/null || true
  fail "$1; rolled back"
}

compose "$rel" up -d --no-build migrate server worker >"$STATE/up.log" 2>&1 \
  || rollback "compose up (see deploy-state/up.log)"
rsync -a --delete "$rel/web/dist/" web/dist/

healthy=
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 --resolve "$HEALTH_RESOLVE" "$HEALTH_URL" || true)
  if [ "$code" = 200 ]; then healthy=1; break; fi
  sleep 3
done
[ -n "$healthy" ] || rollback "health check"
sleep 10
for service in server worker; do
  state=$(sudo -n docker inspect -f '{{.State.Status}}' "bidtool-$service-1" 2>/dev/null || echo missing)
  [ "$state" = running ] || rollback "$service is $state"
done

ln -sfn "$rel" src.next && mv -T src.next src
echo "$sha" > "$STATE/deployed"
rm -f "$STATE/failed"
if [ -n "$sandbox_changed" ]; then
  # The execution node runs its own image and supervisor; it is updated separately.
  echo "$sha $sandbox_changed" >> "$STATE/sandbox-attention"
  log "sandbox node sources changed (update the node): $sandbox_changed"
fi

# Retention: newest releases and backups only; dangling build layers are pruned.
newest_first() { find "$1" -mindepth 1 -maxdepth 1 "${@:2}" -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2-; }
newest_first releases -type d ! -name '*.tmp' | tail -n +$((KEEP_RELEASES + 1)) | while read -r old; do
  [ "$old" = "$rel" ] || [ "$old" = "$previous" ] || rm -rf "$old"
done
newest_first backups -type f -name '*.dump' | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f
sudo -n docker image prune -f >/dev/null
log "deployed ${sha:0:12}"
