#!/usr/bin/env sh
# shell/lib.sh — single source of truth for the homelab `hl-*` commands.
#
# This file holds the CONFIG and the DOER functions (the ones that actually run
# `docker compose`). It is written to run ON THE SERVER (the Docker LXC), where
# the `docker` CLI lives. `aliases.sh` decides whether to call these functions
# locally (when sourced on the server) or over SSH (Mac/other clients).
#
# To change a stack, port, or target, edit ONLY this file.

# ── Config ───────────────────────────────────────────────────────────────────
# Direct SSH to the Docker LXC. The same 10.0.0.201 IP is reachable on the LAN
# and over the Tailscale subnet route, so one target works from every device.
: "${HL_SSH:=root@10.0.0.201}"
# Repo path ON THE SERVER (where the compose files live; the LXC clones to /root).
: "${HL_REPO:=/root/homelab}"
# Host:port to reach services directly by IP (the *.home fallback below).
: "${HL_IP:=10.0.0.201}"
# Stacks in dependency / boot order. `tunnel` is intentionally omitted (opt-in,
# needs CLOUDFLARE_TUNNEL_TOKEN) — add it here if/when it's deployed.
: "${HL_STACKS:=core monitoring ai proxy mcp desktops}"

# compose -f path for a stack name
_hl_compose() {
	echo "docker compose -f $HL_REPO/docker/$1/docker-compose.yml"
}

# ── Doers (run on the server) ────────────────────────────────────────────────

# hl_up [stack]      — no arg: whole lab in HL_STACKS order; arg: one stack
hl_up() {
	if [ -n "$1" ]; then
		eval "$(_hl_compose "$1") up -d"
	else
		for s in $HL_STACKS; do
			echo "==> $s up"
			eval "$(_hl_compose "$s") up -d" || return 1
		done
	fi
}

# hl_down [stack]    — no arg: whole lab in REVERSE order; arg: one stack
hl_down() {
	if [ -n "$1" ]; then
		eval "$(_hl_compose "$1") down"
	else
		# reverse HL_STACKS so dependents come down before their deps
		_rev=''
		for s in $HL_STACKS; do _rev="$s $_rev"; done
		for s in $_rev; do
			echo "==> $s down"
			eval "$(_hl_compose "$s") down"
		done
	fi
}

# hl_stack <up|down|restart> <stack>
hl_stack() {
	_action="$1"; _stack="$2"
	[ -n "$_stack" ] || { echo "usage: hl-stack <up|down|restart> <stack>" >&2; return 2; }
	case "$_action" in
		up)      eval "$(_hl_compose "$_stack") up -d" ;;
		down)    eval "$(_hl_compose "$_stack") down" ;;
		restart) eval "$(_hl_compose "$_stack") restart" ;;
		*)       echo "usage: hl-stack <up|down|restart> <stack>" >&2; return 2 ;;
	esac
}

# hl_ps              — all homelab containers, formatted
hl_ps() {
	docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
}

# hl_logs <service> [lines]  — tail and follow a container's logs
hl_logs() {
	[ -n "$1" ] || { echo "usage: hl-logs <service> [lines]" >&2; return 2; }
	docker logs -f --tail "${2:-100}" "$1"
}

# hl_restart <service>       — restart one container
hl_restart() {
	[ -n "$1" ] || { echo "usage: hl-restart <service>" >&2; return 2; }
	docker restart "$1"
}

# hl_backup          — nightly pg_dumpall script (also runnable on demand)
hl_backup() {
	sh "$HL_REPO/scripts/pg-backup.sh"
}

# hl_models          — rebuild tuned Ollama models from docker/ai/models/*.Modelfile
hl_models() {
	sh "$HL_REPO/docker/ai/load-models.sh"
}

# hl_reload_prom     — reload Prometheus config with no downtime (SIGHUP)
hl_reload_prom() {
	docker kill --signal HUP prometheus
}
