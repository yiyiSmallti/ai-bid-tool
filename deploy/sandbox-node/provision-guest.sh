#!/bin/sh
# Provision the sandbox execution identity inside the dedicated Colima VM.
# Run as root inside the guest. Idempotent; no host mounts are used.
set -eux

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y uidmap dbus-user-session slirp4netns fuse-overlayfs python3-venv \
    curl ca-certificates gnupg docker-ce-rootless-extras

# Service identities: bidsbx owns the rootless daemon and runs the supervisor.
getent group bidctl >/dev/null || groupadd --system bidctl
id bidsbx >/dev/null 2>&1 || useradd --create-home --shell /bin/bash bidsbx
usermod -aG bidctl,docker bidsbx
grep -q '^bidsbx:' /etc/subuid || echo 'bidsbx:231072:65536' >> /etc/subuid
grep -q '^bidsbx:' /etc/subgid || echo 'bidsbx:231072:65536' >> /etc/subgid

# Delegate every controller the supervisor's preflight requires to user sessions.
mkdir -p /etc/systemd/system/user@.service.d
printf '[Service]\nDelegate=cpu cpuset io memory pids\n' > /etc/systemd/system/user@.service.d/delegate.conf
systemctl daemon-reload
loginctl enable-linger bidsbx

# gVisor runsc from the official apt repository.
if ! command -v runsc >/dev/null; then
    curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor --yes -o /usr/share/keyrings/gvisor-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
        > /etc/apt/sources.list.d/gvisor.list
    apt-get update
    apt-get install -y runsc
fi
runsc --version

# Administrator-owned sandbox configuration and private supervisor state.
install -d -m 0755 /etc/bid-sandbox /etc/bid-sandbox/tls
install -d -o bidsbx -g bidsbx -m 0700 /var/lib/bid-sandbox
