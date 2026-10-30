#!/usr/bin/env bash
# Fleet standard and global context in cloud sessions (brignano/ai-tools fleet/).
# Never fails the session.
set -uo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
if [ -x "$HOME/.ai-tools/fleet/session-start.sh" ]; then
  "$HOME/.ai-tools/fleet/session-start.sh"
else
  git clone -q --depth 1 https://github.com/brignano/ai-tools "$HOME/.ai-tools" 2>/dev/null \
    && bash "$HOME/.ai-tools/fleet/session-start.sh"
fi
exit 0
