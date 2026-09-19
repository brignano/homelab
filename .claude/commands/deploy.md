# Deploy

Pull the repo onto CT 100 and make the running containers match it — including
the containers a plain `up -d` silently leaves on old config.

Run with no argument to deploy every stack, or name one: `/deploy monitoring`.

## Why this is not just `git pull && docker compose up -d`

Three separate ways a deploy here looks complete and is not. All three have
actually happened; the steps below exist one per failure.

**A single-file bind mount goes stale on every pull.** `prometheus.yml`,
`blackbox.yml`, `loki-config.yml`, `config.alloy` and the Caddyfile are mounted
as *files*, and Docker resolves a file mount to an inode when the container is
created. git does not edit files in place — it writes a new file and renames it
over the old one — so a pull gives the path a new inode and leaves the container
reading the original, now unlinked. `up -d` sees an unchanged service definition
and does nothing. `restart` reuses the same mounts. `/-/reload` returns 200 and
changes nothing. The repo looks correct, CI is green, and the container serves a
config from six days ago.

**A hardcoded list of which containers to recreate rots.** The obvious fix is
`--force-recreate prometheus blackbox-exporter`, and the obvious fix is wrong:
it is a second list of something the system already knows, which is the exact
shape `check-dashboard.sh` and `check-probes.sh` exist to guard elsewhere. It
was got wrong within a day of being written — a deploy instruction that named
`prometheus` and `node-exporter` but not `blackbox-exporter`, whose config had
also changed, which would have left the DNS probes on a module that no longer
existed and paged `#alerts` at critical severity about the house having no DNS.
So **detect** the drift instead, per step 4.

**Config read only at startup is not reloaded by a file appearing.**
`grafana/provisioning/` is a *directory* mount, so it does not have the inode
problem — but Grafana reads it once at boot. Editing a file under it does not
change the container's config hash, so `up -d` reports `Running` and nothing
happens.

## Steps

### 1. Confirm where you are

```bash
hostname && pwd && git status --short
```

Must be CT 100 with a clean tree. Uncommitted local changes stop the deploy —
`git pull --ff-only` will refuse anyway, and a dirty tree means something was
edited on the box that should have been a commit.

### 2. Pull, remembering where you started

```bash
BEFORE=$(git rev-parse HEAD)
git pull --ff-only
git --no-pager log --oneline "$BEFORE"..HEAD
```

`$BEFORE` is used in step 5 to work out what actually changed. If nothing was
pulled, still continue — the box can be behind in ways a pull does not fix.

### 3. Scheduled jobs and the metrics directory

```bash
./scripts/install-cron.sh
```

Idempotent. Creates `/var/lib/node_exporter/textfile`, which node-exporter
bind-mounts and the cron jobs write to — without it the Deployment and Scheduled
Jobs dashboards are empty, and an empty dashboard reads exactly like a healthy
lab. An entry already in the crontab is reported, never rewritten.

### 4. Bring stacks up, then recreate whatever is serving stale config

For each stack being deployed (skip `proxy` — see **Safety** below):

```bash
docker compose -f docker/<stack>/docker-compose.yml up -d
```

That handles every container whose *service definition* changed — a new volume,
a new command flag, a new image tag. It does nothing for the file-mount case, so
now find those. This asks the running system rather than consulting a list:

```bash
tmp=$(mktemp)
for c in $(docker ps --format '{{.Names}}'); do
  docker inspect -f '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}|{{.Destination}}{{"\n"}}{{end}}{{end}}' "$c" 2>/dev/null \
  | while IFS='|' read -r src dest; do
      [ -n "$src" ] && [ -n "$dest" ] || continue
      case "$src" in "$PWD"/*) ;; *) continue ;; esac   # only files this repo owns
      [ -f "$src" ] || continue                          # files, not directories
      docker cp "$c:$dest" "$tmp" >/dev/null 2>&1 || continue
      cmp -s "$tmp" "$src" || echo "$c ${src#"$PWD"/}"
    done
done | sort -u
rm -f "$tmp"
```

Each line is a container serving a different copy of a file than the repo has.
Copying the container's copy out with `docker cp` rather than checksumming
inside it is deliberate: half these images have no shell, let alone `md5sum`,
and a check that silently skips the containers it cannot run a binary in reports
all-clear forever.

Recreate each one by asking Docker which stack and service it is, so nothing is
typed by hand:

```bash
for c in <names from above>; do
  dir=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$c")
  svc=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$c")
  docker compose -f "$dir/docker-compose.yml" up -d --force-recreate "$svc"
done
```

Then re-run the detection block. It must come back empty. If a container still
reports drift after a recreate, stop and say so — that is a different bug, not a
deploy step to repeat.

### 5. Restart anything whose startup-only config changed

```bash
git diff --name-only "$BEFORE" HEAD
```

Anything under `docker/monitoring/grafana/provisioning/` means Grafana must be
restarted to re-read it — a directory mount shows the new files immediately, and
Grafana will not look at them until it boots:

```bash
docker compose -f docker/monitoring/docker-compose.yml restart grafana
```

Apply the same reasoning to any other config a service reads once at startup.
Recreating is also fine here; a restart is just cheaper.

Note that **provisioning upserts**: a contact point, receiver or alert rule
removed from a file is *not* deleted from Grafana's database. Deletion needs an
explicit `deleteContactPoints:` / `deleteRules:` block naming the uid. If the
diff removes one without such a directive, flag it — the resource will linger,
marked "Unused", and the UI will not let anyone delete it either.

### 6. Verify

```bash
docker compose -f docker/<stack>/docker-compose.yml ps
```

Then, for `monitoring`:

```bash
# Every scrape target healthy
curl -s localhost:9090/api/v1/targets \
  | python3 -c 'import json,sys; [print(t["health"], t["labels"]["job"], t["scrapeUrl"]) for t in json.load(sys.stdin)["data"]["activeTargets"]]'

# Every probe passing — a 0 here is what pages #alerts
curl -s 'localhost:9090/api/v1/query?query=probe_success' \
  | python3 -c 'import json,sys; [print(r["value"][1], r["metric"].get("job"), r["metric"]["instance"]) for r in json.load(sys.stdin)["data"]["result"]]'

# Job metrics are landing
ls -l /var/lib/node_exporter/textfile/
```

`heartbeat.prom` appears within five minutes. `repo_sync.prom` and
`pg_backup.prom` only after their nightly runs, so on a first deploy run both by
hand rather than waiting a day to find out whether they work:

```bash
./scripts/repo-sync.sh && ./scripts/pg-backup.sh
```

If the pull touched Grafana metric names or panels, re-run the metric-name check
in `docker/monitoring/README.md` under **Verify**. A panel that has lost its
metric renders a calm zero, not an error.

## Safety — `proxy` is never deployed unattended

`docker/proxy/` contains AdGuard, the household's DNS, and Caddy, which fronts
everything. A recreate that does not come back takes the network down until
someone notices, and every tool you would reach for to diagnose it resolves
names through the thing that is down. This is the same boundary
`HL_NO_AUTOHEAL=proxy` draws for `repo-sync.sh`.

So: **never include `proxy` in an unattended deploy.** If its config drifted,
report it with the exact command and let the user run it while watching:

```bash
docker compose -f docker/proxy/docker-compose.yml up -d --force-recreate caddy
```

Deploy it only when the user names it explicitly, and verify DNS resolves before
declaring success:

```bash
dig +short stats.home @10.0.0.201
```

## Output format

Report, in order:

- commits pulled (`<short>..<short>`, count), or "already current"
- cron jobs installed or already present
- stacks brought up
- **containers recreated for stale config, naming the file** — this is the part a
  plain `up -d` would have missed, and the reason this command exists
- services restarted for startup-only config
- verification results, with any failing target or probe named

End with anything left for the user: a `proxy` recreate to run by hand, a
provisioning deletion with no `deleteRules:` directive, a probe that came back
down. If a step failed, stop there and surface it rather than continuing — a
half-deployed monitoring stack that reports itself healthy is worse than one
that is visibly broken.
