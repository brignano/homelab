# TSD: Keeping images current, and noticing when they aren't

**Status:** 🚧 partially shipped — §1 (watchdog) and §3 (`--pull`) are live in
[`scripts/repo-sync.sh`](../../scripts/repo-sync.sh); §2 (update policy) and §4
(digest capture) are still proposed
**Date:** 2026-09-19
**Owner:** Anthony

## Problem

Every container image in this lab is out of date, and nothing has ever said so.

Measured on CT 100 on 2026-09-19, by image creation time:

| Image | Age | Tag style |
|---|---|---|
| `sablierapp/sablier:1.8.1` | **23 months** | pinned |
| `ghcr.io/gethomepage/homepage:v1.5.0` | **12 months** | pinned |
| `prom/blackbox-exporter:latest` | 9 months | floating |
| `gcr.io/cadvisor/cadvisor:latest` | 8 months | floating |
| `quay.io/prometheuscommunity/postgres-exporter:latest` | 6 months | floating |
| `prom/node-exporter:latest` | 5 months | floating |
| everything else | 3–4 months | floating |

Two different mechanisms, drifting for two opposite reasons.

**The floating tags drifted because nothing pulls.** `:latest` is a name, not an
instruction — Compose's default pull policy is `missing`, so `docker compose
up -d` checks whether an image carrying that tag is on disk and stops there. It
never asks the registry whether something newer now answers to the same name.
Nothing in this repo runs `docker compose pull`. So `:latest` produces the
appearance of currency and guarantees the opposite, and because the tag floats,
nothing anywhere records which version is actually running.

**The pinned tags drifted because nothing bumps.** Pinning fixes the record and
fixes the pull — a tag that changes is a tag that isn't on disk, so Compose
fetches it — but only if something is opening the PRs. Nothing was. That is why
the two *pinned* images are the two *stalest* images by a wide margin, and why
Sablier has been sitting at `1.8.1` for nearly two years.

`docker build` has the same property one level down, and this is the part the
existing tooling is structurally blind to. `docker/proxy/Dockerfile` is `FROM
caddy:2-alpine`, and a build reuses a cached base unless told `--pull`. On
2026-09-19 `caddy-sablier:local` was 22 minutes old, built on a `caddy:2-alpine`
that was three months old. `repo-sync.sh` measures locally-built stacks by image
creation time — so it reads 22 minutes, calls `proxy` fresh, and says nothing.
The same applies to `python:3.12-slim` under `docker/assistant/`.

### This is the third time

The mechanism differs each time; the failure class does not.

- The cron job that was never installed looked exactly like a run of quiet,
  healthy days.
- The working tree that was three weeks behind `main` looked deployed, because
  `git log` on GitHub said it had shipped.
- Now: images months to years stale, on a repo whose CI is green and whose
  drift report is silent.

Each was invisible for the same reason — **no signal existed that would ever
have mentioned it** — and the first two were fixed the same way, by making
silence itself the alarm (`heartbeat.sh`, and the Healthchecks ping in
`repo-sync.sh`).

That is the lesson this TSD applies. The interesting question is not which
updater to adopt. It is what notices when the updater, whichever one it is, has
quietly stopped working.

## Goals

- Drift becomes impossible to hold silently. Any image past a staleness
  threshold is named, whatever the reason it got there.
- The low-risk stacks stay current with no recurring human effort.
- The stacks that can take the house offline never update without a person.
- Rolling back a bad image is one command, not an archaeology session.
- No new always-on infrastructure, and no new inbound exposure.

## Non-goals

- Tracking upstream *availability*. Knowing that Grafana shipped 13.2.2 is not
  the point; knowing that this box runs something from June is.
- Zero-downtime updates. A restart is fine; this is a household, not a fleet.
- Automatic major-version migrations. Homepage v1 → v2 and Postgres 16 → 17 are
  deliberate projects, not tag bumps.
- Reproducible builds by digest. Worth revisiting; not what is broken today.

## Design

Three parts, in the order they matter.

### 1. The staleness watchdog — the load-bearing piece

Extend `repo-sync.sh` with a second drift axis. It already compares *git against
running containers*; add *running images against a maximum age*, and fold the
result into the same Discord message and the same Healthchecks ping.

Report anything older than `HL_MAX_IMAGE_AGE_DAYS` (default 90). Silence keeps
its existing meaning: everything deployed, everything current.

Deliberately offline — image creation time comes from local metadata, so this
needs no registry calls, no API tokens, no rate limits, and it keeps working on
the day the internet is what broke. That costs some precision: an image can be
current and old, if upstream simply hasn't shipped. That is an acceptable false
positive and a rare one. The inverse error — a 23-month-old image nobody
mentions — is the one that actually happened.

**This is the part that answers "don't drift again," and it is the only part
that is independent of how updating is done.** Watching the updater catches one
failure mode: the updater stopped. Watching staleness catches every one of them
— Renovate stalled, PRs ignored, a pull failing, a stack not running, a tag
pinned and forgotten, a build base cached. It does not require predicting which.

Had this existed, Sablier would have been named 21 months ago, and the reason
would not have mattered.

### 2. Split updating by blast radius

Reuse the boundary this repo has already drawn. `HL_NO_AUTOHEAL=proxy` exists
because AdGuard is the household's resolver, and a 4am restart that does not come
back takes the network down along with every tool you would reach for to
diagnose it. That judgment transfers to updates unchanged.

| Stacks | Tags | How they update |
|---|---|---|
| `monitoring`, `ai`, `core`, `mcp`, `dashboard`, `desktops` | floating | `repo-sync.sh` pulls, restarts, reports what changed |
| `proxy` | pinned | Renovate opens a PR; a human deploys it |
| `postgres` (in `core`) | pinned to major | never automatic — see below |

Worst case in the first row is that Grafana looks wrong for an afternoon and a
revert fixes it. These stacks fail visibly and recover cheaply, their image
versions were never gated by CI anyway, and the status quo has already cost nine
months on `blackbox-exporter` with nobody noticing.

`proxy` is the one place review earns its keep, and confining Renovate to it
keeps the PR volume low enough to actually be read — which is the failure mode
of putting Renovate everywhere (see rejected alternatives).

Postgres is pinned to `16-alpine` and stays there. Patch releases within 16 are
safe and arrive with the pull; a major is a dump-and-restore, never a tag change.

### 3. `--pull` on the built stacks

`repo-sync.sh` runs `up -d --build` for `proxy` and `assistant`. Add `--pull` so
the base image is refreshed rather than served from cache. One flag, and it
closes the hole the drift report cannot see.

Note the ordering constraint this creates: `proxy` is on `HL_NO_AUTOHEAL`, so its
rebuild stays manual, which is correct and should stay that way.

### 4. Rollback insurance

Before pulling, `repo-sync.sh` records the current `RepoDigests` of everything it
is about to replace, to a file on the box. A bad morning becomes one paste rather
than a guess at which version was good. Cheap, and it is the mitigation that
makes part 2's automatic pulls defensible.

## Decisions

**The watchdog measures age, not availability.** Comparing against upstream
means network calls, credentials for some registries, and rate limits — and it
answers a narrower question. "Is there something newer" has to be asked per
registry and can fail per registry. "Is this older than 90 days" always answers,
offline, and is the question that would have caught every drift observed here.

**Automatic pulls are accepted for the low-risk stacks.** The trade is a
possible unannounced breaking change against certain permanent staleness. The
status quo has already lost that bet on six images. Visible occasional breakage
beats invisible permanent drift, and part 4 bounds the cost.

**Renovate is scoped to one stack, on purpose.** Not because it is bad, but
because its failure mode is silent and volume-driven. See below.

**The floating tags stay floating.** Pinning everything sounds tidier and is the
configuration that produced the two worst offenders in this lab. Pinning is only
better than floating when something is opening PRs; where nothing is, floating
drifts more slowly. Pin where there is review, float where there is automation,
and let the watchdog cover both.

**Sablier and the Caddy plugin move together.** `docker/proxy/Dockerfile` builds
`sablier-caddy-plugin@v1.0.2`, which talks to the Sablier API in
`docker/desktops/`. A jump from `1.8.1` to `1.18.0` is a two-file change with a
compatibility question in it, not a version bump, and it belongs in its own PR.

## Rejected alternatives

**Watchtower.** Updates containers in place, from the box, outside git. That is
precisely the property `repo-sync.sh` was written to eliminate — it would restore
a state where the repo cannot tell you what is running, on the host that serves
the household's DNS. It also solves the easy half (pulling) and none of the hard
half (noticing).

**Renovate on everything.** Twenty-odd images across actively released projects
is a steady stream of PRs, most of them patch bumps nobody reads. The realistic
end state is a tab of unread Renovate PRs, which is `1.8.1` again with more
ceremony. Worse, if Renovate stops, *every* stack drifts and nothing says so —
whereas under the split above, a dead Renovate costs one stack and the watchdog
still reports it.

**Pinning everything with digests, no automation.** This is the current state of
`sablier` and `homepage`, generalised to the whole lab. It maximises
reproducibility and guarantees drift.

**`pull_policy: always` in the compose files.** Equivalent to part 2 for the
floating stacks, but it also fires on every unrelated `up -d`, including the ones
`repo-sync.sh` runs to heal an unrelated change, turning every restart into a
potential version change at an unpredictable moment. Pulling explicitly, once a
day, in the job that already reports, is the same effect at a time of our
choosing.

**A separate cron job for image checks.** A second schedule, a second thing that
can silently not be installed — which is the exact failure this repo has already
had. The drift report already runs daily, already reaches Discord, and already
has a dead man's switch. Adding an axis to it is strictly less machinery than
adding a peer to it.

## Revisit if

- The 90-day threshold proves noisy — projects that release rarely will sit just
  over it. Per-image overrides before lowering the signal.
- A second node appears, at which point staggered updates become worth having.
- Renovate on `proxy` goes unread for a quarter, which would mean review is not
  actually happening there either and the split should collapse toward automation
  plus the watchdog.
- Digest pinning becomes worth it — most likely the first time an upstream
  retags an existing version in place.

## Out of scope, tracked separately

- **Homepage v1.5.0 → v2.x.** Two majors, a breaking auth change, and the
  `config/custom.css` design-token bridge that CI diffs. Its own migration.
- **Portainer STS → LTS.** `portainer-ce:latest` tracks the short-term stream;
  LTS is the right choice for this box. A stream change, not a bump.
- **Orphan images.** `searxng`, `ntfy` (both removed services), `hello-world`
  (the Docker-in-LXC bootstrap test) and `httpd:alpine` (unreferenced anywhere in
  this repo) still sit on disk. Housekeeping, not risk.
