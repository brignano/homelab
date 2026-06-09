#!/usr/bin/env sh
# shell/aliases.sh — homelab `hl-*` commands for zsh (Mac) and bash (server).
#
# Sourced from your shell profile (wired in by the ai-tools installer). One file
# for both Unix shells: it AUTO-DETECTS whether it's running on the server (the
# Docker LXC) and adapts:
#   • on the server  → calls the doer functions in lib.sh locally
#   • anywhere else   → calls the SAME doers over SSH (ssh $HL_SSH)
# URL-opener / Tailscale / status commands always run locally — they don't touch
# Docker. Run `hl-help` for the full list.

# Where this homelab checkout lives (the ai-tools hook exports this; default for
# direct sourcing on the Mac). Used to find lib.sh and detect server mode.
: "${HOMELAB_DIR:=$HOME/Projects/homelab}"

# Are we ON the server (the Docker LXC)? Default is "no" (the common case: a
# client that SSHes in). Auto-detect requires LINUX + docker + the compose files
# right here — the `uname` guard is what stops a Mac with Docker Desktop and a
# local checkout from falsely matching. Override with HL_SERVER=1 / HL_SERVER=0.
if [ "${HL_SERVER:-}" = 1 ]; then
	_hl_local=1
elif [ "${HL_SERVER:-}" = 0 ]; then
	_hl_local=0
elif [ "$(uname -s)" = "Linux" ] && command -v docker >/dev/null 2>&1 \
     && [ -f "$HOMELAB_DIR/docker/core/docker-compose.yml" ]; then
	_hl_local=1
else
	_hl_local=0
fi
# On the server, the doers act on this local checkout wherever it's cloned.
[ "$_hl_local" = 1 ] && : "${HL_REPO:=$HOMELAB_DIR}"

# Config + doer functions (sourcing is side-effect-free; nothing runs Docker).
[ -f "$HOMELAB_DIR/shell/lib.sh" ] && . "$HOMELAB_DIR/shell/lib.sh"

# ── Docker / ops: local calls on the server, SSH wrappers everywhere else ─────
if [ "$_hl_local" = 1 ]; then
	hl-up()          { hl_up "$@"; }
	hl-down()        { hl_down "$@"; }
	hl-stack()       { hl_stack "$@"; }
	hl-ps()          { hl_ps; }
	hl-logs()        { hl_logs "$@"; }
	hl-restart()     { hl_restart "$@"; }
	hl-backup()      { hl_backup; }
	hl-models()      { hl_models; }
	hl-reload-prom() { hl_reload_prom; }
else
	# Invoke the same lib.sh doers on the server. Args here are simple tokens
	# (stack / service names), so word-splitting them into the remote command
	# is intentional. hl-logs needs a TTY so Ctrl-C cleanly stops the follow.
	_hl_rcall() { ssh    "$HL_SSH" ". $HL_REPO/shell/lib.sh && $*"; }
	_hl_rtty()  { ssh -t "$HL_SSH" ". $HL_REPO/shell/lib.sh && $*"; }
	hl-up()          { _hl_rcall "hl_up $*"; }
	hl-down()        { _hl_rcall "hl_down $*"; }
	hl-stack()       { _hl_rcall "hl_stack $*"; }
	hl-ps()          { _hl_rcall "hl_ps"; }
	hl-logs()        { _hl_rtty  "hl_logs $*"; }
	hl-restart()     { _hl_rcall "hl_restart $*"; }
	hl-backup()      { _hl_rcall "hl_backup"; }
	hl-models()      { _hl_rtty  "hl_models"; }
	hl-reload-prom() { _hl_rcall "hl_reload_prom"; }
fi

# ── Open a service (local browser; prints the URL on a headless server) ───────
_hl_open() {
	case "$(uname -s)" in
		Darwin) open "$1" ;;
		Linux)  command -v xdg-open >/dev/null 2>&1 && xdg-open "$1" >/dev/null 2>&1 || echo "$1" ;;
		*)      echo "$1" ;;
	esac
}
hl-chat()   { _hl_open "http://chat.home"; }
hl-stats()  { _hl_open "http://stats.home"; }
hl-apps()   { _hl_open "http://apps.home"; }
hl-dns()    { _hl_open "http://dns.home"; }
hl-alerts() { _hl_open "http://alerts.home"; }
hl-kali()   { _hl_open "https://kali.home"; }
# IP fallbacks for when you're on the tailnet but not using AdGuard split-DNS.
hl-chat-ip()   { _hl_open "http://$HL_IP:3010"; }
hl-stats-ip()  { _hl_open "http://$HL_IP:3000"; }
hl-apps-ip()   { _hl_open "http://$HL_IP:9000"; }
hl-dns-ip()    { _hl_open "http://$HL_IP:3001"; }
hl-alerts-ip() { _hl_open "http://$HL_IP:8090"; }

# ── Connectivity & access ────────────────────────────────────────────────────
hl-vpn()      { tailscale status; }
hl-vpn-up()   { tailscale up --accept-routes; }
hl-vpn-down() { tailscale down; }
hl-ssh()      { ssh "$HL_SSH" "$@"; }

# hl-status — the "Tailscale flap ≠ outage" check. Tells you WHICH layer broke
# (your client's Tailscale vs the route vs the server) before you blame the lab.
hl-status() {
	echo "── Tailscale (this client) ──"
	if command -v tailscale >/dev/null 2>&1; then
		if tailscale status >/dev/null 2>&1; then
			echo "  ✅ tailscale UP"
		else
			echo "  ❌ tailscale DOWN → run hl-vpn-up. This is usually the problem, NOT the server."
			return 0
		fi
	else
		echo "  – tailscale CLI not found (skipping)"
	fi

	echo "── Server reachability ($HL_IP) ──"
	if { case "$(uname -s)" in Darwin) ping -c1 -t2 "$HL_IP";; *) ping -c1 -W2 "$HL_IP";; esac; } >/dev/null 2>&1; then
		echo "  ✅ $HL_IP reachable"
	else
		echo "  ❌ $HL_IP UNREACHABLE → subnet route not accepted, or the host is down."
		return 0
	fi

	echo "── Grafana ──"
	if curl -fsS -o /dev/null --max-time 4 "http://stats.home"; then
		echo "  ✅ stats.home OK — everything's up."
	elif curl -fsS -o /dev/null --max-time 4 "http://$HL_IP:3000"; then
		echo "  ⚠️  reachable by IP but stats.home failed → AdGuard / split-DNS issue, not the lab."
	else
		echo "  ❌ Grafana not responding → check the proxy/monitoring stacks (hl-ps)."
	fi
}

# ── Help ─────────────────────────────────────────────────────────────────────
hl-help() {
	cat <<'EOF'
homelab hl-* commands  (same names on Mac, Windows & the server)

  open a service
    hl-chat hl-stats hl-apps hl-dns hl-alerts hl-kali   (+ -ip twins, e.g. hl-stats-ip)
  connectivity
    hl-status            which layer is down: client / route / server
    hl-vpn[-up|-down]    tailscale status / up --accept-routes / down
    hl-ssh [cmd]         ssh into the Docker LXC
  docker / ops           (run locally on the server, over SSH from a client)
    hl-up [stack]        whole lab (boot order) or one stack
    hl-down [stack]      whole lab (reverse) or one stack
    hl-stack <up|down|restart> <stack>
    hl-ps                all containers
    hl-logs <svc> [n]    tail & follow logs
    hl-restart <svc>     restart one container
    hl-backup            pg_dumpall (scripts/pg-backup.sh)
    hl-models            rebuild tuned Ollama models (docker/ai/load-models.sh)
    hl-reload-prom       reload Prometheus config (SIGHUP, no downtime)
EOF
}
