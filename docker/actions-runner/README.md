# actions-runner: GitHub Actions on the homelab

Four self-hosted runners (`myoung34/github-runner`), one per private repo:
driftwood, guidebook, sites, receipts. Actions minutes are a single quota shared
by all repos, and it ran out for November 2026 (#430). Jobs on these runners cost
nothing.

```
GitHub  <-- outbound HTTPS only --  runner-<repo> (CT 100)
```

## Rules

- **Private repos only.** Public repos (homelab, ai-tools, design, brignano.io,
  breakeven, card) run free on GitHub's hosted runners. A public repo's fork PRs
  would run code on this box, so never add one. If a repo here turns public,
  delete its service first.
- **Outbound only.** No ports, no Caddy route, no Docker socket, no host mounts.
  Its own network, joined by nothing else. It can still reach the LAN through
  Docker's NAT, so only put repos you write on it.
- **Ephemeral.** One job per runner, then it deregisters and restarts clean. The
  work folder is tmpfs (2 GB, counted in the 3 GB memory cap).
- **Capped:** 2 CPUs, 3 GB each. Four idle runners use about 0.5 GB total.
- **No Docker jobs.** `services:`, `container:` and image builds stay on
  `ubuntu-latest`.

## Setup (once)

1. Make the token. GitHub, Settings, Developer settings, Fine-grained tokens:
   resource owner `brignano`, selected repos driftwood, guidebook, sites and
   receipts, permission **Repository, Administration: read and write**, nothing
   else. Expiry 1 year.
2. On CT 100: `cp docker/actions-runner/.env.example docker/actions-runner/.env`,
   put the token in `RUNNER_GITHUB_TOKEN`, `chmod 600` it.
3. `/deploy actions-runner` (or `docker compose -f docker/actions-runner/docker-compose.yml up -d`).
4. Each repo, Settings, Actions, Runners, shows `homelab-<repo>` as Idle.

Check without printing the token: `test -s docker/actions-runner/.env && echo ok`.

## Using it

In a workflow, change `runs-on: ubuntu-latest` to:

```yaml
runs-on: [self-hosted, homelab]
```

The runner has Ubuntu 24.04 and the usual build tools. `actions/setup-node`,
`setup-python` and the like work as on GitHub's runners.

## When it is down

Jobs wait in the queue and never fall back by themselves. Check
`docker ps --filter name=runner-` and `docker logs runner-<repo>`. To go back to
GitHub's runners, set `runs-on: ubuntu-latest` again (only possible when minutes
are available).

## Token expiry

A failing runner whose log says 401 or 403 means the token expired or lost a
repo. Make a new one, replace the line in `.env`, then `docker compose -f
docker/actions-runner/docker-compose.yml up -d --force-recreate`.

## Adding or removing a repo

Copy a service block, change `REPO_URL`, `RUNNER_NAME`, `container_name`, and
add the repo to the token. Removal is the reverse.
