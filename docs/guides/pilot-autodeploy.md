---
kind: howto
---

# Deploy main automatically on a single Compose host

[autodeploy.sh](../../deploy/pilot/autodeploy.sh) deploys the newest `main` commit
that a pull request merged after its `python` and `web` checks passed. The host polls
GitHub anonymously, so neither side holds the other's credentials. A commit pushed to
`main` without a pull request is never deployed.

Each run downloads the commit's source archive, builds the console and the `migrate`,
`server` and `worker` images, backs up the database with `pg_dump` when the commit adds
migrations, starts the services and checks `/health`. A failed deploy without new
migrations rolls back to the previous images and console. A failed deploy with new
migrations stops with the backup kept, because the schema is not rolled back
automatically. A failed commit is not retried; the next merge gets a new attempt.

## Prepare the host

1. Run the stack once by hand under `/opt/bid-tool` with Docker Compose project
   `bidtool`, a host `compose.override.yml` and `secrets/bid.env`. Give the deploy
   user passwordless `sudo` for `docker`, and install `curl`, `rsync`, `python3`,
   Node.js and npm.
2. Put the running sources under `releases/<commit sha>/`, make `src` a symbolic link
   to it, and write that sha to `deploy-state/deployed`. Copy the served console to
   `releases/<commit sha>/web/dist` as well.
3. Install the script as `/opt/bid-tool/autodeploy.sh` (mode 0755) and the
   [service](../../deploy/pilot/bid-autodeploy.service) and
   [timer](../../deploy/pilot/bid-autodeploy.timer) units under `/etc/systemd/system/`.
4. Run one deploy by hand with `sudo systemctl start bid-autodeploy.service` and read
   `journalctl -u bid-autodeploy.service`. Then enable polling with
   `sudo systemctl enable --now bid-autodeploy.timer`.

Environment variables prefixed `BID_DEPLOY_` override the repository, required checks,
health URL, retention counts and package mirrors. The default mirrors rewrite PyPI file
URLs in `uv.lock` and the `pip` index in `deploy/Dockerfile` to Tencent Cloud mirrors;
`uv` still verifies every file against its locked hash. Set both mirror variables to
empty strings on hosts that reach PyPI directly.

## Operate it

| Need | Action |
| --- | --- |
| See what is live | `cat /opt/bid-tool/deploy-state/deployed` |
| Read the last run | `journalctl -u bid-autodeploy.service -n 50`; build and start output is in `deploy-state/build.log` and `deploy-state/up.log` |
| Retry a failed commit | Delete `deploy-state/failed`; the next timer run retries it |
| Pause deploys | `sudo systemctl stop bid-autodeploy.timer` |
| Restore after a failed migrating deploy | Restore the newest file in `backups/` with `pg_restore`, then point `src` and `deploy-state/deployed` at the previous release and start it |

The sandbox execution node runs its own image and supervisor, which this script does
not update. When a deploy changes their sources, the script appends the commit and
paths to `deploy-state/sandbox-attention`; update the node with
[the sandbox runtime guide](sandbox-runtime.md) and then clear that file.
