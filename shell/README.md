# `hl-*` shell commands

One memorable command set for the homelab, identical on **Mac (zsh)**, **Windows
(PowerShell)**, and the **server (bash, the Docker LXC)**. The only name to
remember is `hl`: bare, it lists everything — a fuzzy picker if `fzf` is
installed (Mac/server), the cheatsheet otherwise. Every command also works
dash-less through it.

```
hl                # forgot the name? fuzzy picker (fzf) or the cheatsheet
hl stats          # open Grafana            (≡ hl-stats)
hl status         # is it me or the lab? (Tailscale / route / server)
hl logs grafana   # tail a container's logs (runs on the server, over SSH from a client)
hl up monitoring  # bring a stack up
```

## How it's wired

| File | Role |
|------|------|
| [`lib.sh`](lib.sh) | **Single source of truth** — config (SSH target, repo path, stack order) + the functions that actually run `docker compose`. Edit *only this* to add a stack or change a port. |
| [`aliases.sh`](aliases.sh) | The `hl-*` commands for zsh + bash. Auto-detects the server: runs `lib.sh` locally there, SSH-wraps the *same* functions from any client. |
| [`aliases.ps1`](aliases.ps1) | Windows equivalents (always SSH wrappers). |

The same command behaves correctly everywhere because the Docker logic lives once in
`lib.sh`. On a client, `hl-logs grafana` SSHes to the LXC and runs `lib.sh`'s `hl_logs`;
on the server it calls it directly.

## Install

The [`ai-tools`](https://github.com/brignano/ai-tools) installer wires this into each
machine's shell profile automatically (one guarded `source` line). After cloning this
repo and running the ai-tools installer, open a new shell and run `hl-help`.

To wire it by hand instead:

- **Mac** `~/.zshrc` / **server** `~/.bashrc`:
  ```sh
  HOMELAB_DIR="${HOMELAB_DIR:-$HOME/Projects/homelab}"   # server: set to /root/homelab
  if [ -f "$HOMELAB_DIR/shell/aliases.sh" ]; then . "$HOMELAB_DIR/shell/aliases.sh"; fi
  ```
- **Windows** `$PROFILE`:
  ```powershell
  . "$HOME\Projects\homelab\shell\aliases.ps1"
  ```

Update later with `git pull` — the profile sources the live file, no re-install needed.

## Config

Defaults live at the top of [`lib.sh`](lib.sh) and can be overridden with env vars
(`HL_SSH`, `HL_REPO`, `HL_IP`, `HL_STACKS`, `HOMELAB_DIR`):

| Var | Default | Meaning |
|-----|---------|---------|
| `HL_SSH` | `root@10.0.0.201` | SSH target — the Docker LXC (LAN + Tailscale subnet route). |
| `HL_REPO` | `/root/homelab` | Repo path **on the server**. |
| `HL_IP` | `10.0.0.201` | Host for the `*-ip` URL fallbacks. |
| `HL_STACKS` | `core monitoring ai proxy mcp desktops` | Boot order for `hl-up`/`hl-down`. `tunnel` is opt-in. |
| `HOMELAB_DIR` | `~/Projects/homelab` | Local checkout path (where these files live). |
| `HL_SERVER` | _(auto)_ | Force server mode: `1` runs Docker locally, `0` always SSHes. Auto-detected as Linux + docker + compose files present, so the Mac stays a client. |
