# TSD: AI Homelab Assistant (read-only telemetry tool)

**Status:** 🗄 shelved — better served by Claude on this hardware
**Date:** 2026-06-07
**Owner:** Anthony

## Problem
The lab has solid monitoring (Prometheus, Loki, Grafana, ntfy), but answering
"is anything wrong right now?" means opening Grafana and reading panels. A
conversational layer — "any containers down? how's RAM trending? errors in Caddy
logs?" — would be faster for quick checks, using a local model in Open WebUI.

## Goals
- Ask natural-language questions about live system state in Open WebUI.
- **Strictly read-only.** No shell, no writes, no container control, no config endpoints.
- Zero new always-on infrastructure (no extra container).

## Non-goals
- Replacing Grafana/Prometheus alerting — that stays the source of truth.
- Proactive/scheduled alerting (an LLM only runs when prompted).
- Any mutating action (restart, scale, edit).

## Proposed design (for the record)
An Open WebUI **Tool** (Python, runs inside the Open WebUI container) exposing
read-only functions the model can call:

| Function | Hits | Returns |
|----------|------|---------|
| `query_metrics(promql)` | Prometheus `GET /api/v1/query` | instant metric value(s) |
| `query_range(promql, dur)` | Prometheus `/api/v1/query_range` | short time series |
| `query_logs(logql, limit)` | Loki `/loki/api/v1/query_range` | last N matching log lines |
| `homelab_health()` | curated set of the above | one-shot up/down + CPU/RAM/disk + recent errors |

**Security boundary:** functions only build calls to the *query* endpoints (take a
PromQL/LogQL string + fixed target, never an arbitrary URL — closes the
SSRF/prompt-injection path); no admin/config/write APIs reachable; result size
capped. Worst case from injection = the model reads a metric. Negligible.

**Connectivity:** add `open-webui` to the `monitoring` network for internal DNS
(`prometheus:9090`, `loki:3100`), no host-port dependency.

**RAM:** ~zero — runs in the existing Open WebUI container; only marginal context
growth per query.

## Why shelved
- **Tool-calling is unreliable on a 3B**, and a 7B is impractically slow on this
  CPU-only / 16 GB box (memory-bandwidth bound). The only version that works on
  local is a canned `homelab_health()` where the tool does all the work and the
  model just narrates numbers — low value for the effort.
- For real, open-ended homelab investigation, **Claude reading Grafana/Prometheus
  is simply better** (reliable reasoning + current data) — same conclusion as the
  rest of [`../ai-strategy.md`](../ai-strategy.md).
- Grafana dashboards + ntfy alerts already cover actual monitoring.

## Revisit if
- The lab gets a GPU-capable / larger-RAM machine (makes a capable local model
  with reliable tool-calling viable), **or**
- a concrete, high-frequency need emerges for the canned `homelab_health()`
  summary specifically (then build just that, no open-ended querying).
