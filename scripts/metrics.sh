#!/usr/bin/env sh
#
# Write Prometheus metrics from a shell script, via the node_exporter textfile
# collector. Sourced, not executed:  . "$(dirname "$0")/metrics.sh"
#
# Why this exists
# ---------------
# Every failure this lab has actually had lived in the deployment and scheduling
# layer — a cron entry that was never installed, a working tree three weeks
# behind main, a container serving a config file that had been unlinked for six
# days. All three were found by hand. None of them were visible in Grafana,
# because the scripts that detect them report to Discord and then throw the
# result away.
#
# Discord is the right place for "something needs you now". It is the wrong
# place for "is this still true?", which is a question you ask by looking, not
# by scrolling back. That question needs a time series, and the cheapest way for
# a cron job to produce one is the textfile collector: write a .prom file, and
# node_exporter serves its contents on the next scrape.
#
# The rule this imposes on callers, and it matters: emitting metrics must never
# be able to fail the job. A backup that ran fine and could not write a metric
# is a successful backup. So every function here degrades to a no-op — an absent
# directory, an unwritable path, a missing mv all just mean no metrics this run,
# which the staleness alerts will notice on their own.
#
# Usage:
#   . "$REPO/scripts/metrics.sh"
#   metrics_open pg_backup                       # -> pg_backup.prom
#   metric_help homelab_pg_backup_success gauge "1 if the last dump succeeded"
#   metric homelab_pg_backup_success 1
#   metric homelab_stack_running 1 "stack=$(metric_label "$stack")"
#   metrics_close
#
# Scraped as `node_*`-adjacent series on the existing `node` job, so nothing in
# prometheus.yml needs a new target.

# Where node_exporter reads .prom files from. Must match the
# --collector.textfile.directory flag in docker/monitoring/docker-compose.yml.
HL_TEXTFILE_DIR="${HL_TEXTFILE_DIR:-/var/lib/node_exporter/textfile}"

# Set by metrics_open; empty means "not collecting", which every function below
# treats as a reason to do nothing rather than a reason to fail.
_M_TMP=""
_M_OUT=""

# Start a metrics file. Never fails: if the directory is not there (the collector
# was never wired up, or this is a dev box), collection is simply off.
metrics_open() {
  _M_TMP=""
  _M_OUT=""
  [ -d "$HL_TEXTFILE_DIR" ] || return 0
  [ -w "$HL_TEXTFILE_DIR" ] || return 0
  _M_OUT="$HL_TEXTFILE_DIR/$1.prom"
  # The temp file must live in the same directory as the target: the collector
  # reads whole files and mv is only atomic within a filesystem, so a rename
  # from /tmp could be a copy, and a copy can be scraped half-written.
  #
  # `.` prefix because the collector globs *.prom — a temp file named
  # something.prom.tmp is ignored anyway, but a dotfile is ignored by more
  # things than that.
  _M_TMP="$HL_TEXTFILE_DIR/.$1.$$.tmp"
  : > "$_M_TMP" 2>/dev/null || { _M_TMP=""; _M_OUT=""; return 0; }
}

# HELP and TYPE for the metric that follows. Worth the two extra lines: these
# show up in Grafana's metric browser, and a homelab_* series with no help text
# is one nobody remembers the meaning of six months later.
metric_help() {
  [ -n "$_M_TMP" ] || return 0
  printf '# HELP %s %s\n# TYPE %s %s\n' "$1" "$3" "$1" "$2" >> "$_M_TMP" 2>/dev/null || true
}

# metric <name> <value> [label-pairs...]
# Label pairs are pre-formatted `key="value"` strings — build the value side
# with metric_label so a stray quote cannot produce an unparseable file.
metric() {
  [ -n "$_M_TMP" ] || return 0
  _name=$1
  _value=$2
  shift 2
  if [ "$#" -gt 0 ]; then
    _labels=""
    for _l in "$@"; do
      [ -n "$_l" ] || continue
      if [ -n "$_labels" ]; then _labels="$_labels,$_l"; else _labels="$_l"; fi
    done
    [ -z "$_labels" ] || _name="$_name{$_labels}"
  fi
  printf '%s %s\n' "$_name" "$_value" >> "$_M_TMP" 2>/dev/null || true
}

# Append pre-built sample lines in one go.
#
# For callers that compute several families inside one loop: the text format
# wants each family's samples contiguous under its own HELP/TYPE, and a parser
# that meets a new HELP line closes the family it was reading — so interleaving
# A,B,A,B leaves both sets of samples untyped and strips their help text. The
# fix is to collect the lines during the loop and replay them here, grouped.
# Blank lines are stripped: a buffer built by appending "\n<line>" in a loop
# starts with an empty one, and while the exposition format ignores those, a
# .prom file with stray gaps in it reads like something went wrong.
metric_raw() {
  [ -n "$_M_TMP" ] || return 0
  [ -n "$1" ] || return 0
  printf '%s\n' "$1" | sed '/^[[:space:]]*$/d' >> "$_M_TMP" 2>/dev/null || true
}

# Escape a string for use inside a label value, per the exposition format:
# backslash, double quote and newline. A container name or a file path is
# normally boring, but "normally" is not a guarantee, and one bad line makes
# node_exporter reject the entire file — taking every other metric in it down
# with it.
metric_label() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\n' ' '
}

# Convenience: `name="escaped value"` in one call.
metric_kv() {
  printf '%s="%s"' "$1" "$(metric_label "$2")"
}

# Publish. The rename is the only moment the file is visible to a scrape, so a
# half-written file is never served.
metrics_close() {
  [ -n "$_M_TMP" ] || return 0
  # World-readable: node_exporter runs as nobody inside its container, and a
  # 0600 file written by root's cron is one it cannot read. This file holds
  # timestamps and counts, never secrets.
  chmod 644 "$_M_TMP" 2>/dev/null || true
  mv -f "$_M_TMP" "$_M_OUT" 2>/dev/null || rm -f "$_M_TMP" 2>/dev/null || true
  _M_TMP=""
  _M_OUT=""
}
