---
kind: howto
---

# Install, back up and upgrade a company gateway

The gateway runs from [compose.yml](../../deploy/gateway/compose.yml) on one
Docker host. Only Caddy publishes ports; the API, worker, PostgreSQL and
Gotenberg share a private network. Requirements are in
[Gateway deployment](../plan/enterprise-gateway.md#gateway-deployment).

## Install

1. On the gateway host, check out this repository and build the image:
   `docker build -f deploy/Dockerfile -t bid-gateway:<release> .`
2. Copy [gateway.env.example](../../deploy/gateway/gateway.env.example) to
   `deploy/gateway/gateway.env`, set its mode to `0600`, set `BID_IMAGE` to the
   image just built, and fill every value. Generate the passwords with
   `openssl rand -hex 24`; they are placed in database URLs unencoded.
3. Point the DNS name in `BID_GATEWAY_HOST` at the host, open ports 80 and 443,
   and set `BID_TLS` to the ACME contact email. Without public DNS, set
   `BID_TLS=internal` and give clients Caddy's root certificate from
   `/data/caddy/pki/authorities/local/root.crt` in the `caddy` container, or pin
   it in the client.
4. Start: `docker compose --env-file gateway.env up -d` from `deploy/gateway`.
   The `migrate` service creates the schema and the runtime role, then exits;
   its message "Selected development database initialized" is printed for
   every installation.
5. Create the first administrator:
   `docker compose --env-file gateway.env run --rm --no-deps server python -m app.admin member create --role admin --login <login>`.
   The one-time password is printed to this terminal only; the administrator
   replaces it at first sign-in and creates the other members in the client.

## Back up

`docker compose --env-file gateway.env run --rm backup` writes
`bid-backup-<UTC time>` to the `backups` volume and removes backups older than
14 days. Install
[bid-gateway-backup.service](../../deploy/gateway/bid-gateway-backup.service)
and [bid-gateway-backup.timer](../../deploy/gateway/bid-gateway-backup.timer)
under `/etc/systemd/system`, correct `WorkingDirectory`, and run
`systemctl enable --now bid-gateway-backup.timer` for a nightly run. Copy the
volume off the host; a backup holds encrypted files, and restoring them needs
the keys from `gateway.env`.

## Verify a backup

`docker compose --env-file gateway.env run --rm backup python -m app.admin restore --verify /backups/<backup>`
restores the dump into a scratch database, compares row counts, migration
revisions and file hashes with the manifest, and drops the scratch database.
Exit code 4 with `backup_verify_failed` lists what differs.

## Upgrade

1. Build the new image under a new tag, as in Install.
2. Run `deploy/gateway/upgrade.sh bid-gateway:<new release>`. It stops the API
   and worker, takes and verifies a backup, runs the new migrations on a clone
   of the database, drops the roles the rehearsal created, then sets
   `BID_IMAGE` in `gateway.env` and starts the new image, which migrates the
   live database.
3. If any step before the live migration fails, the script starts the current
   image again and leaves `gateway.env` unchanged. After a failed live
   migration, keep the API stopped; the backup the script named holds the
   state before the upgrade.

## Code

- [compose.yml](../../deploy/gateway/compose.yml), with the Caddy site under
  `configs`, and [upgrade.sh](../../deploy/gateway/upgrade.sh).
- [gateway_deploy_acceptance.py](../../scripts/gateway_deploy_acceptance.py)
  installs a release, bootstraps it, upgrades it and verifies a backup on an
  isolated Docker daemon.
