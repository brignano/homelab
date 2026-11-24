#!/usr/bin/env sh
#
# Load the hl-* commands in every new shell on this machine. Idempotent.
#
# Adds one guarded line to ~/.bashrc that sources shell/aliases.sh from this
# checkout, so `hl-kali-shell`, `hl-logs` and friends work as soon as you log in
# to CT 100, without running the ai-tools installer there. A line that already
# sources aliases.sh (from the installer or by hand) is left alone.
#
# Usage, on the Docker LXC:
#   ./scripts/install-shell.sh           # add the line if it's missing
#   ./scripts/install-shell.sh --check   # report only; non-zero if missing
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RC="${HL_SHELL_RC:-$HOME/.bashrc}"
LINE="[ -f \"$REPO/shell/aliases.sh\" ] && . \"$REPO/shell/aliases.sh\"  # hl-* commands (homelab scripts/install-shell.sh)"

if [ -f "$RC" ] && grep -q 'shell/aliases\.sh' "$RC"; then
	echo "install-shell: $RC already loads shell/aliases.sh"
	exit 0
fi
if [ "${1:-}" = --check ]; then
	echo "install-shell: $RC does not load shell/aliases.sh" >&2
	exit 1
fi
printf '\n%s\n' "$LINE" >>"$RC"
echo "install-shell: added to $RC; open a new shell (or run: . $RC)"
