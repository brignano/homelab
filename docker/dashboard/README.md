# dashboard — the landing page

Homepage at the **bare** `$HOMELAB_DOMAIN`, which is the parent of every service
name, so it is the one URL that does not have to be remembered and every other
name is discoverable from it.

| Directory | What it is | Served at |
|---|---|---|
| `config/` | The tile list, settings, and the CSS/JS that style the page. | — |
| `icons/` | The dashboard's mark, and every tile icon. [README](icons/README.md) | `/icons/...` |
| `assets/` | The design system: vendored tokens, generated palette, Geist. [README](assets/README.md) | `/assets/...` |

Both `icons/` and `assets/` are **directory** mounts under `/app/public`, which
is the only place Homepage serves local files from. Directory rather than
single-file, so a `git pull` that replaces a file cannot leave the container on
the old inode — see the 2026-08-30 entry in `docs/setup-log.md`.

## Deploying a change

```sh
cd /root/homelab && git pull
docker compose -f docker/dashboard/docker-compose.yml up -d
```

`up -d`, not `restart`. A restart reuses the existing container spec, so a
change to the compose file — a new mount, say — comes back looking exactly like
nothing happened. `up -d` recreates when the spec changed and is a no-op when it
did not. Content changes inside `config/`, `icons/` and `assets/` need only a
restart, because all three are directory mounts.

`scripts/repo-sync.sh` does both nightly and reports what it restarted.

## If `git pull` refuses over `config/custom.css` or `custom.js`

```
error: The following untracked working tree files would be overwritten by merge:
        docker/dashboard/config/custom.css
```

That is Homepage, not you. On start it copies a **0-byte skeleton** into the
config directory for every config file it knows about — `custom.css` and
`custom.js` among them — whenever one is missing. That is why this mount has to
stay writable, and it is why the box ended up with two untracked empty files
that git then refused to overwrite with the real ones.

Check they are empty, then delete them and pull again:

```sh
wc -c docker/dashboard/config/custom.css docker/dashboard/config/custom.js
rm docker/dashboard/config/custom.css docker/dashboard/config/custom.js
git pull --ff-only
```

If either has anything in it, something wrote to it — move it aside and look at
it rather than deleting it.

`scripts/repo-sync.sh` now clears this case by itself: for each file an incoming
commit adds, an untracked *empty* copy is removed before the pull, and reported.
Anything with a byte in it still stops the pull, which is the point.

The same skeleton logic applies to `docker.yaml`, `kubernetes.yaml` and
`proxmox.yaml`, which this stack does not use and does not track. They sit in
`config/` as empty files and are harmless.

## What is deliberately not here

- **No Docker socket.** Homepage can discover services from container labels,
  but that needs root-equivalent access to save maintaining a list CI already
  keeps honest (`scripts/check-dashboard.sh`).
- **No widgets.** The ones worth having need an admin credential in this stack
  to render a number you can see by clicking through.
- **Only the proxy network.** Tiles are plain links, and the up/down checks go
  through Caddy over the same URLs a browser uses.
