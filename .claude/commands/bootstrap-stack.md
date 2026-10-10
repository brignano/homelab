# Bootstrap Stack

Bring up all homelab stacks in dependency order, waiting for each to be healthy before proceeding.

## Dependency order — derived, not listed

The order comes from the compose files, not from this page. A stack that joins
another stack's network declares it `external: true`, and Compose will not
create an external network — `up` fails with *"network X declared as external,
but could not be found"* until the owning stack is up. So the `networks:`
blocks under `docker/*/docker-compose.yml` already are the dependency graph.
[`scripts/bootstrap-plan.py`](../../scripts/bootstrap-plan.py) reads them and
prints the order:

```bash
./scripts/bootstrap-plan.py                  # every stack under docker/
./scripts/bootstrap-plan.py core ai proxy …  # only the stacks you name
```

Each non-`#` line is one step: `<stack>` means bring the whole stack up;
`<stack> <service>…` means bring up only those services. `#` lines say why. It
exits 1, naming the missing network and its owner, if the stacks you named
cannot all come up — including when you left out the stack that owns a network
someone needs. It needs PyYAML (`python3-yaml`, installed by
`bootstrap-docker.sh`). CI runs it, so a compose change that makes the stacks
unbootable fails the PR.

Do not copy its output into this page. The previous hardcoded order
(`core → monitoring → ai → tunnel`) missed six stacks and would have failed at
`monitoring` as soon as `proxy` existed.

**Why some steps are partial.** The stacks are not acyclic: `monitoring`'s
blackbox-exporter joins `proxy_proxy` (it probes through Caddy) and `proxy`'s
Caddy joins `monitoring_monitoring` (it routes to Grafana). Neither can come up
whole first. The plan brings up the part of one stack that can start — which
creates that stack's own network — and finishes it once the other side is up.
This relies on `docker compose up <service>…` touching only the networks those
services use, which was checked on CT 100 (Compose v5.1.4) on 2026-10-10.

## Steps

0. **Decide which stacks.** A stack with no `docker/<stack>/.env` is not
   deployed (`tunnel`, until cloudflared is set up). Ask the user whether to set
   it up or leave it out, then run the planner naming the stacks that are in.
   If it exits 1, stop and show its output — leaving out a stack whose network
   another one needs is not something to work around.

For each step the planner prints, in order:

1. **Run preflight** — the first time a stack appears, call `/preflight` for it.
   Stop if any check fails. Its external-network check will flag the networks a
   *partial* step deliberately leaves out; that is expected for those services
   only, and the planner's `#` line names them.
2. **Bring it up:**
   ```bash
   docker compose -f docker/<stack>/docker-compose.yml up -d              # whole-stack step
   docker compose -f docker/<stack>/docker-compose.yml up -d <svc> <svc>  # partial step
   ```
3. **Wait for healthy** — poll until the containers that step started report
   `healthy` or `running` (for containers without healthchecks):
   ```bash
   docker compose -f docker/<stack>/docker-compose.yml ps
   ```
   Retry every 10 seconds, timeout after 3 minutes. If a container fails to
   become healthy, run `docker logs <container>` and surface the tail to the
   user.
4. **Log the step** — note which stack came up and at what time.
5. Proceed to the next step.

### `proxy` takes the household's DNS with it

`proxy` runs AdGuard Home, the resolver for the LAN and the tailnet. Bringing it
up from nothing is fine, but on a box where it is **already running**, an
`up -d` that recreates it (changed compose file, new image) drops DNS for every
device in the house for a few seconds. `proxy` is on `HL_NO_AUTOHEAL` for that
reason. If it is already up and the step would recreate it, say so and ask
before running it. Afterwards, verify DNS with the retry loop in
`.claude/commands/deploy.md` — a single `dig` fired while AdGuard is still
binding `:53` answers `connection refused` and looks exactly like an outage.

## Logging

After all steps are done, summarize:
- Which stacks started successfully, and which were left out and why
- Any containers that took more than 60 seconds to become healthy
- Final `docker ps` output showing all running containers

Suggest running `/homelab-health-check` as a final verification.
