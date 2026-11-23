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
# The Proxmox host (m5) that runs the LXC. Drives, vzdump, pct and fstrim live
# here, never in the LXC. From the host, `pct enter 100` is a shell in the LXC.
: "${HL_HOST_SSH:=root@10.0.0.200}"
# Repo path ON THE SERVER (where the compose files live; the LXC clones to /root).
: "${HL_REPO:=/root/homelab}"
# Host:port to reach services directly by IP (the *.home fallback below).
: "${HL_IP:=10.0.0.201}"
# Stacks in dependency / boot order. `tunnel` is intentionally omitted (opt-in,
# needs CLOUDFLARE_TUNNEL_TOKEN) — add it here if/when it's deployed.
: "${HL_STACKS:=core monitoring ai assistant proxy mcp desktops}"

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

# hl_kali_shell — a terminal inside the Kali container, on demand. Sablier keeps
# Kali scaled to zero for the browser desktop; this starts it if it's off, opens
# a shell as the desktop user (or runs the given command), and stops it again on
# exit — unless it was already running (the desktop is open), so a browser
# session is never cut off. Stays on demand by design: an always-on Kali would
# hold 1–2 GB that Ollama needs. Caveat: if you open the desktop while this
# shell is up, Sablier stops Kali when that desktop session expires.
hl_kali_shell() {
	if ! docker inspect kali-linux >/dev/null 2>&1; then
		echo "kali-linux doesn't exist yet: run hl-up desktops once" >&2
		return 1
	fi
	_hl_kali_was_up=0
	[ "$(docker inspect -f '{{.State.Running}}' kali-linux)" = true ] && _hl_kali_was_up=1
	if [ "$_hl_kali_was_up" = 0 ]; then
		echo "starting Kali…" >&2
		docker start kali-linux >/dev/null || return 1
		# wait for the image's init to create the desktop user (abc)
		_hl_i=0
		until docker exec kali-linux id abc >/dev/null 2>&1; do
			_hl_i=$((_hl_i + 1)); [ "$_hl_i" -ge 30 ] && break; sleep 1
		done
	fi
	_hl_tty=-i; [ -t 0 ] && _hl_tty=-it
	if [ $# -eq 0 ]; then
		docker exec $_hl_tty -u abc -w /config kali-linux bash -l
	else
		docker exec $_hl_tty -u abc -w /config kali-linux "$@"
	fi
	_hl_rc=$?
	if [ "$_hl_kali_was_up" = 0 ]; then
		echo "stopping Kali…" >&2
		docker stop kali-linux >/dev/null
	fi
	return "$_hl_rc"
}

# hl_backup          — nightly pg_dumpall script (also runnable on demand)
hl_backup() {
	sh "$HL_REPO/scripts/pg-backup.sh"
}

# hl_models          — rebuild tuned Ollama models from docker/ai/models/*.Modelfile
hl_models() {
	sh "$HL_REPO/docker/ai/load-models.sh"
}

# hl_heartbeat       — run the dead man's switch once (same as the 5-min cron)
hl_heartbeat() {
	sh "$HL_REPO/scripts/heartbeat.sh" && echo "heartbeat: pinged ok"
}

# hl_reload_prom     — reload Prometheus config with no downtime (SIGHUP)
hl_reload_prom() {
	docker kill --signal HUP prometheus
}
