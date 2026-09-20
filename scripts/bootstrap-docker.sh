#!/usr/bin/env bash
# Bootstrap Docker CE on a fresh Debian/Ubuntu host.
# Run as root or with sudo.
set -euo pipefail

DOCKER_GPG=/etc/apt/keyrings/docker.asc
DOCKER_LIST=/etc/apt/sources.list.d/docker.list
DOCKER_DAEMON=/etc/docker/daemon.json

if [[ $EUID -ne 0 ]]; then
  echo "Re-running with sudo…"
  exec sudo "$0" "$@"
fi

echo "==> Updating package index"
apt-get update -qq

echo "==> Installing prerequisites"
apt-get install -y -qq \
  ca-certificates \
  curl \
  gnupg \
  lsb-release

echo "==> Adding Docker GPG key"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/$(. /etc/os-release && echo "$ID")/gpg \
  -o "$DOCKER_GPG"
chmod a+r "$DOCKER_GPG"

echo "==> Adding Docker apt repository"
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=$DOCKER_GPG] \
  https://download.docker.com/linux/$(. /etc/os-release && echo "$ID") \
  $(lsb_release -cs) stable" \
  > "$DOCKER_LIST"

echo "==> Capping container log growth"
# Docker's default log driver is json-file and its default max-size is
# UNLIMITED. `restart: unless-stopped` means nothing ever truncates the file
# either, so every container in this lab writes
# /var/lib/docker/containers/<id>/<id>-json.log forever. That is the one disk
# consumer here with no ceiling of any kind: Prometheus keeps 30d, Loki keeps
# 30d, images are pruned nightly by repo-sync.sh, and container logs just grow.
# A chatty container — Caddy's access log, AdGuard's per-query log — is GB/week.
#
# Shipping logs to Loki does not help. Alloy reads them through the Docker API
# (loki.source.docker), which does not truncate what it reads, so the copy in
# Loki ages out at 30 days while the original never does.
#
# Daemon-level rather than a `logging:` block in each compose file, because the
# containers this most needs to cover are the ones no compose file of ours
# starts: the Kali webtop that Sablier creates on demand, and anything run by
# hand. 10m x 3 is 30 MB per container, ~600 MB across the lab at worst.
#
# Only NEW containers pick this up — the driver and its options are fixed at
# create time. Applying it to a running lab is a recreate, not a restart; see
# "Disk" in AGENTS.md.
if [[ -f "$DOCKER_DAEMON" ]]; then
  if grep -q '"max-size"' "$DOCKER_DAEMON"; then
    echo "    $DOCKER_DAEMON already caps log size — leaving it alone."
  else
    # Merging JSON from bash is how a config file gets corrupted. Say what is
    # missing and let a person add it.
    echo "    WARNING: $DOCKER_DAEMON exists and does not set a log size cap."
    echo "    Container logs will grow without limit. Add:"
    echo '      "log-driver": "json-file",'
    echo '      "log-opts": { "max-size": "10m", "max-file": "3" }'
  fi
else
  install -m 0755 -d /etc/docker
  cat > "$DOCKER_DAEMON" <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
JSON
  chmod 0644 "$DOCKER_DAEMON"
  echo "    Wrote $DOCKER_DAEMON (10m x 3 per container)."
fi

# After the daemon.json, deliberately: the daemon reads it at startup, and the
# apt install is what starts it. Writing the file afterwards would need a
# restart that `systemctl enable --now` does not perform on an already-running
# service, and the first containers would be created uncapped.
echo "==> Installing Docker CE"
apt-get update -qq
apt-get install -y -qq \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

echo "==> Adding current user to docker group (re-login required)"
SUDO_USER="${SUDO_USER:-$USER}"
usermod -aG docker "$SUDO_USER"

echo "==> Enabling Docker on boot"
systemctl enable --now docker

echo ""
echo "Done. Docker $(docker --version) is installed."
echo "Log out and back in (or run 'newgrp docker') to use Docker without sudo."
