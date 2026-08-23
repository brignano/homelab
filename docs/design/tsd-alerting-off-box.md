# TSD: Alerting that survives the box going down

**Status:** ✅ approved / shipped
**Date:** 2026-08-23
**Owner:** Anthony

## Problem

Every alerting path in this lab runs on the machine it watches.

```
Grafana (CT 100) ──webhook──▶ ntfy (CT 100) ──▶ phone
Grafana (CT 100) ──rules────▶ evaluated on CT 100
assistant (CT 100) ─────────▶ #digest
```

Grafana evaluates the rules, ntfy delivers the push, the assistant posts the
digest — all inside the same LXC. When the box goes down, all three go with it
and **nothing tells you**. The single failure most worth hearing about is the
one guaranteed to be silent.

This was found the only way it could be: the box went down, and nothing arrived.

A monitoring system cannot report its own death. Nothing that runs on CT 100
can close this — not a better alert rule, not a second container.

## Goals

- Hear about it when the whole machine is unreachable.
- One place to read everything: alerts and digests together.
- No inbound exposure — no public endpoint, no port, no tunnel.
- No new always-on infrastructure on the box (that would inherit the same flaw).

## Non-goals

- Replacing Grafana as the source of alert rules. It stays authoritative for
  everything that can be observed *from* the box.
- High availability. The aim is to *know*, not to stay up.
- Sub-minute detection. A homelab does not need it, and tight windows produce
  false alarms on a domestic uplink.

## Design

Two independent additions, deliberately not sharing a failure mode.

### 1. Discord webhook as a second delivery for Grafana

Grafana's contact point gains a `discord` receiver alongside the existing ntfy
webhook. One contact point, two receivers, so the notification policy is
unchanged.

A Discord **webhook** — not the bot. It is write-only, needs no bot process,
and is therefore unaffected by the `ai` stack or the assistant container being
down. `#alerts` is fed by webhooks only; nothing the assistant does can silence it.

### 2. Dead man's switch, off-box

[`scripts/heartbeat.sh`](../../scripts/heartbeat.sh) on a 5-minute cron pings
[Healthchecks.io](https://healthchecks.io). If the pings **stop**, Healthchecks
posts to the same Discord webhook.

The inversion is the entire point. A system that must *send* an alert to warn
you cannot warn you about being dead; a system where **silence is the signal**
can. It also needs no inbound access — the ping goes outward, so there is still
no port, no tunnel, and no public endpoint.

It checks more than connectivity. Pinging unconditionally would only prove cron
ran. It pings only while `grafana` and `prometheus` are actually running, which
also catches *"host is up, Docker is wedged"* — a state Grafana obviously cannot
report on. If either is missing it pings `/fail` rather than going quiet, so
that case alerts immediately instead of waiting out the grace period.

| Failure | Caught by | How |
|---------|-----------|-----|
| Service down, disk full, endpoint unreachable | Grafana | rules → ntfy + Discord |
| Docker wedged, monitoring stack down | heartbeat | `/fail` ping |
| **Whole box down / network down** | **heartbeat** | **pings stop → Healthchecks alerts** |
| Assistant bot down | Grafana | Discord webhook is independent of the bot |
| Healthchecks itself down | nothing | accepted; see below |

## Decisions

**Healthchecks.io rather than self-hosting it.** Self-hosting the watchdog on
the watched machine is the bug, restated. It has to be somewhere else, and no
other machine here is reliably on. The free tier covers this, and the same
account later covers backup-job monitoring — which is what
[`tsd-backups-and-monitoring.md`](tsd-backups-and-monitoring.md) already
identified as its only net-new component.

**ntfy stays.** Discord is now the place to read things, but keeping ntfy costs
nothing and preserves a delivery path that does not depend on Discord being up
or on having an internet connection at all. Redundancy at the notification
layer is cheap; losing it to tidiness is not worth it.

**A webhook, not the bot, feeds `#alerts`.** Routing alerts through the
assistant would reintroduce exactly the dependency this TSD removes.

**5-minute period, 15-minute grace.** Two consecutive misses before alerting.
Tighter windows page on domestic-uplink hiccups; the credibility cost of false
alarms is higher than four extra minutes of detection latency.

## Rejected alternatives

| Option | Why not |
|--------|---------|
| A second container on CT 100 watching the first | Same machine, same death. This is the bug. |
| UptimeRobot / BetterStack polling an endpoint | Needs a publicly reachable URL, so `cloudflared` must be deployed and inbound surface added. The dead man's switch needs none. |
| Cloudflare Worker on a cron | Viable, and fully self-owned — but more code to maintain and still needs a public health endpoint. Reconsider if a Cloudflare Tunnel gets deployed for other reasons. |
| Alert from the Proxmox host instead of the LXC | Better than nothing, but the host and CT share power, disk and network. Most real outages take both. |
| Push the digest more often instead | Confuses cadence with coverage. A missing 07:30 digest is noticed at 09:00, if at all. |

## Revisit if

- **A second always-on machine appears.** It could host the watchdog and remove
  the SaaS dependency.
- **Healthchecks' free tier stops fitting** — a Cloudflare Worker is the
  documented fallback above.
- **False alarms appear.** Widen the grace period before weakening the check;
  an alert you learn to ignore is worse than no alert.
