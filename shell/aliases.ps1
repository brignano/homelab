# shell/aliases.ps1 — homelab hl-* commands for Windows PowerShell.
#
# Dot-sourced from $PROFILE (wired in by the ai-tools installer). Windows is
# never the server, so every Docker/ops command is an SSH wrapper that invokes
# the SAME lib.sh doers on the LXC — identical names and behavior to Mac/server.
# Run `hl-help` for the full list.

# ── Config (env overrides win; defaults match shell/lib.sh) ───────────────────
$HL_SSH  = if ($env:HL_SSH)  { $env:HL_SSH }  else { "root@10.0.0.201" }
$HL_REPO = if ($env:HL_REPO) { $env:HL_REPO } else { "/root/homelab" }
$HL_IP   = if ($env:HL_IP)   { $env:HL_IP }   else { "10.0.0.201" }

# ── Docker / ops — SSH into the LXC and run the lib.sh doer ────────────────────
function _hl_rcall { param([string]$cmd) ssh    $HL_SSH ". $HL_REPO/shell/lib.sh && $cmd" }
function _hl_rtty  { param([string]$cmd) ssh -t $HL_SSH ". $HL_REPO/shell/lib.sh && $cmd" }

function hl-up          { _hl_rcall "hl_up $args" }
function hl-down        { _hl_rcall "hl_down $args" }
function hl-stack       { _hl_rcall "hl_stack $args" }
function hl-ps          { _hl_rcall "hl_ps" }
function hl-logs        { _hl_rtty  "hl_logs $args" }
function hl-restart     { _hl_rcall "hl_restart $args" }
function hl-backup      { _hl_rcall "hl_backup" }
function hl-models      { _hl_rtty  "hl_models" }
function hl-reload-prom { _hl_rcall "hl_reload_prom" }

# ── Open a service in the default browser ─────────────────────────────────────
function _hl_open { param([string]$url) Start-Process $url }
function hl-chat   { _hl_open "http://chat.home" }
function hl-stats  { _hl_open "http://stats.home" }
function hl-apps   { _hl_open "http://apps.home" }
function hl-dns    { _hl_open "http://dns.home" }
function hl-alerts { _hl_open "http://alerts.home" }
function hl-kali   { _hl_open "https://kali.home" }
# IP fallbacks for when AdGuard split-DNS isn't resolving *.home.
function hl-chat-ip   { _hl_open "http://${HL_IP}:3010" }
function hl-stats-ip  { _hl_open "http://${HL_IP}:3000" }
function hl-apps-ip   { _hl_open "http://${HL_IP}:9000" }
function hl-dns-ip    { _hl_open "http://${HL_IP}:3001" }
function hl-alerts-ip { _hl_open "http://${HL_IP}:8090" }

# ── Connectivity & access ─────────────────────────────────────────────────────
function hl-vpn      { tailscale status }
function hl-vpn-up   { tailscale up --accept-routes }
function hl-vpn-down { tailscale down }
function hl-ssh      { ssh $HL_SSH @args }

# hl-status — the "Tailscale flap != outage" check: which layer is actually down.
function hl-status {
	Write-Host "-- Tailscale (this client) --"
	if (Get-Command tailscale -ErrorAction SilentlyContinue) {
		tailscale status *> $null
		if ($LASTEXITCODE -eq 0) {
			Write-Host "  OK  tailscale UP"
		} else {
			Write-Host "  X   tailscale DOWN -> run hl-vpn-up. Usually the problem, NOT the server."
			return
		}
	} else {
		Write-Host "  -   tailscale CLI not found (skipping)"
	}

	Write-Host "-- Server reachability ($HL_IP) --"
	if (Test-Connection -Count 1 -Quiet $HL_IP) {
		Write-Host "  OK  $HL_IP reachable"
	} else {
		Write-Host "  X   $HL_IP UNREACHABLE -> subnet route not accepted, or the host is down."
		return
	}

	Write-Host "-- Grafana --"
	try {
		Invoke-WebRequest -UseBasicParsing -TimeoutSec 4 "http://stats.home" *> $null
		Write-Host "  OK  stats.home -- everything's up."
	} catch {
		try {
			Invoke-WebRequest -UseBasicParsing -TimeoutSec 4 "http://${HL_IP}:3000" *> $null
			Write-Host "  !   reachable by IP but stats.home failed -> AdGuard / split-DNS issue, not the lab."
		} catch {
			Write-Host "  X   Grafana not responding -> check the proxy/monitoring stacks (hl-ps)."
		}
	}
}

# ── Help ──────────────────────────────────────────────────────────────────────
function hl-help {
@"
homelab hl-* commands  (same names on Mac, Windows & the server)

  can't remember a name? just type 'hl'
    hl                   this help
    hl <cmd> [args]      dash-less form of any command: hl logs grafana = hl-logs grafana

  open a service
    hl-chat hl-stats hl-apps hl-dns hl-alerts hl-kali   (+ -ip twins, e.g. hl-stats-ip)
  connectivity
    hl-status            which layer is down: client / route / server
    hl-vpn[-up|-down]    tailscale status / up --accept-routes / down
    hl-ssh [cmd]         ssh into the Docker LXC
  docker / ops           (run over SSH on the server)
    hl-up [stack]        whole lab (boot order) or one stack
    hl-down [stack]      whole lab (reverse) or one stack
    hl-stack <up|down|restart> <stack>
    hl-ps                all containers
    hl-logs <svc> [n]    tail & follow logs
    hl-restart <svc>     restart one container
    hl-backup            pg_dumpall (scripts/pg-backup.sh)
    hl-models            rebuild tuned Ollama models (docker/ai/load-models.sh)
    hl-reload-prom       reload Prometheus config (SIGHUP, no downtime)
"@ | Write-Host
}

# ── hl — the only name you have to remember ───────────────────────────────────
# `hl` alone prints the cheatsheet; `hl <cmd> [args]` dispatches to hl-<cmd>,
# so `hl logs grafana` = `hl-logs grafana`. The dashed names keep working.
function hl {
	if ($args.Count -eq 0 -or $args[0] -in 'help', '-h', '--help') { hl-help; return }
	$name, $rest = $args
	$cmd = "hl-$name"
	if (Get-Command $cmd -ErrorAction SilentlyContinue) {
		& $cmd @rest
	} else {
		Write-Host "hl: unknown command '$name' - run 'hl' for the list"
	}
}
