#!/usr/bin/env sh
#
# Read, validate and use a Healthchecks ping URL. Sourced, not executed, and
# after metrics.sh:
#
#   . "$(dirname "$0")/metrics.sh"
#   . "$(dirname "$0")/healthchecks.sh"
#
# Why this exists
# ---------------
# On 2026-09-19 the nightly pg_dumpall's dead man's switch was configured — in
# the sense that the variable had a value. The value was
# `https://hc-ping.com/your-uuid`, copied out of .env.example and never
# replaced, and every layer agreed it was fine:
#
#   - pg-backup.sh warned only when the variable was EMPTY, and a placeholder
#     is not empty, so it said nothing;
#   - the ping was `curl -fsS ... || true`, so the 404 went nowhere;
#   - Healthchecks had no such check, so no check was ever late.
#
# The one job standing between this lab and an unrecoverable mistake had a dead
# man's switch that pinged into the void, and it was indistinguishable from one
# that worked. Same shape as the cron entry that was never installed and the
# three weeks behind main: the thing that would have told you was the thing
# that was not running.
#
# So a switch here is armed only if it can be shown to be armed:
#
#   - a value that is not a plausible ping URL counts as UNSET, names the
#     variable and says why, rather than being pinged into the dark;
#   - a ping that fails is reported instead of swallowed;
#   - and both outcomes are left behind as a metric, because a line in a cron
#     log is not a thing anybody reads. `hl-hc-ping-failing` alerts on it, so a
#     switch that stops arming pages the same way anything else here does.
#
# Ping URLs are masked in every message. They are capability URLs: anyone
# holding one can check the job in, which is precisely how you would hide a
# box that had stopped.

# The URL the last hc_url call accepted, and why it rejected one otherwise.
#
# Both are variables rather than stdout, and that is the whole interface
# decision: `URL=$(hc_url ...)` would run the function in a SUBSHELL, where
# HC_REASON is set, returned from, and thrown away with the subshell — leaving
# every caller reporting an empty reason. Which is this file's own failure mode
# wearing a different hat, and is how it was found: by testing the rejection
# path rather than the happy one.
#
# Callers differ in what they do about a rejection — heartbeat exits, pg-backup
# warns, repo-sync puts it in the Discord report — but all three want the
# reason, so it has to survive the call.
HC_VALUE=""
HC_REASON=""

# hc_mask <url> -> scheme, host, and up to the first 8 characters of the path.
# Enough to tell two checks apart in a log without writing the credential into
# it. Matches the shape the README suggests for eyeballing .env.
hc_mask() {
  printf '%s' "${1:-}" | sed -e 's#\(://[^/]*/[A-Za-z0-9._-]\{0,8\}\).*#\1…#'
}

# hc_url <VAR_NAME> <env-file>
# Sets HC_VALUE and returns 0, or sets HC_REASON and returns 1. Call it
# directly — never in a command substitution, see above.
hc_url() {
  _hc_var=$1
  _hc_file=$2
  HC_VALUE=""
  HC_REASON=""

  # Read only the key we need; avoids sourcing a file full of other secrets.
  # `tail -n1` because a duplicated key is a real thing that happens, and the
  # last assignment is the one a shell sourcing this file would end up with.
  _hc_u=$(sed -n "s/^$_hc_var=//p" "$_hc_file" 2>/dev/null | tail -n1 | tr -d '"'"'"' \r' || true)

  if [ -z "$_hc_u" ]; then
    HC_REASON="$_hc_var is unset in $_hc_file"
    return 1
  fi

  # A scheme, and a path with something in it. `https://hc-ping.com` on its own
  # serves the site's front page and answers 200 — a ping that succeeds forever
  # while arming nothing, which is worse than one that fails.
  case "$_hc_u" in
    http://*/?*|https://*/?*) ;;
    *)
      HC_REASON="$_hc_var in $_hc_file is not a ping URL ($(hc_mask "$_hc_u"))"
      return 1
      ;;
  esac

  # The placeholder .env.example ships, and the shapes a half-finished
  # copy-paste leaves behind. Listed explicitly because every one of them
  # satisfies the check above while pinging nothing at all.
  case "$_hc_u" in
    *your-uuid*|*'<'*|*'>'*|*changeme*|*replace*|*REPLACE*|*example.com*)
      HC_REASON="$_hc_var in $_hc_file still holds a placeholder ($(hc_mask "$_hc_u")) — paste the check's real ping URL"
      return 1
      ;;
  esac

  HC_VALUE=$_hc_u
}

# The outcome, as a series. Written to a file of its own rather than the
# caller's, because two of the three callers ping from an EXIT trap — after
# they have closed their own metrics file — and metrics.sh keeps one open at a
# time. Degrades to a no-op when metrics.sh was never sourced, for the same
# reason everything in metrics.sh does: emitting a metric must never be able to
# fail the job it is describing.
hc_metric() {
  command -v metrics_open >/dev/null 2>&1 || return 0
  metrics_open "healthchecks_$(printf '%s' "$1" | tr -c 'a-zA-Z0-9_' '_')"
  metric_help homelab_healthchecks_ping_success gauge \
    "1 if this job's dead man's switch is configured and its last ping was accepted"
  metric homelab_healthchecks_ping_success "$2" "$(metric_kv job "$1")"
  metrics_close
}

# hc_unarmed <job>
# Record that this job has no usable switch. The caller reports HC_REASON in
# whichever channel it owns; this is the half that survives being unread.
hc_unarmed() {
  hc_metric "$1" 0
}

# hc_ping <url> <job> [body]
# Ping, report a failure rather than swallowing it, and leave the outcome
# behind. Returns curl's verdict, so a job whose entire purpose is the ping
# (heartbeat) still fails when it cannot deliver one — while a job pinging from
# an EXIT trap should discard it with `|| true`, so its own exit code survives.
hc_ping() {
  _hc_target=$1
  _hc_job=$2
  _hc_body=${3:-}
  _hc_rc=0

  # --retry rides out a brief network blip so a flaky uplink does not page you;
  # -m caps the whole attempt, so a ping can never pile up under cron.
  if [ -n "$_hc_body" ]; then
    _hc_err=$(curl -fsS -m 20 --retry 3 --retry-delay 5 \
      --data-raw "$_hc_body" "$_hc_target" 2>&1) || _hc_rc=$?
  else
    _hc_err=$(curl -fsS -m 20 --retry 3 --retry-delay 5 "$_hc_target" 2>&1) || _hc_rc=$?
  fi

  if [ "$_hc_rc" -eq 0 ]; then
    hc_metric "$_hc_job" 1
    return 0
  fi

  # A 404 here means the check was deleted or the id is wrong: the switch is
  # not armed, the job is running fine, and without this line nothing anywhere
  # would ever say so.
  echo "healthchecks: ping failed for $_hc_job ($(hc_mask "$_hc_target")): ${_hc_err:-curl exit $_hc_rc}" >&2
  hc_metric "$_hc_job" 0
  return "$_hc_rc"
}
