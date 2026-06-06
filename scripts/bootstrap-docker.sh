#!/usr/bin/env bash
# Bootstrap Docker CE on a fresh Debian/Ubuntu host.
# Run as root or with sudo.
set -euo pipefail

DOCKER_GPG=/etc/apt/keyrings/docker.asc
DOCKER_LIST=/etc/apt/sources.list.d/docker.list

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
