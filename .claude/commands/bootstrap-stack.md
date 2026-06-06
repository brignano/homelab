# Bootstrap Stack

Bring up all homelab stacks in dependency order, waiting for each to be healthy before proceeding.

## Dependency order

```
core → monitoring → ai → tunnel
```

- **core** (`docker/core/`): Portainer, PostgreSQL — base services everything else depends on
- **monitoring** (`docker/monitoring/`): Prometheus, Grafana, node-exporter, cAdvisor — depends on `core_core` network
- **ai** (`docker/ai/`): Ollama, Open WebUI — standalone, but bring up after core is stable
- **tunnel** (`docker/tunnel/`): cloudflared — must come last; joins `core_core` and `ai_ai` networks to proxy upstream

## Steps

For each stack in order:

1. **Run preflight** — call `/preflight` for this stack before proceeding. Stop if any check fails.
2. **Bring up the stack:**
   ```bash
   docker compose -f docker/<stack>/docker-compose.yml up -d
   ```
3. **Wait for healthy** — poll until all containers in the stack report `healthy` or `running` (for containers without healthchecks):
   ```bash
   docker compose -f docker/<stack>/docker-compose.yml ps
   ```
   Retry every 10 seconds, timeout after 3 minutes. If a container fails to become healthy, run `docker logs <container>` and surface the tail to the user.
4. **Log the step** — note which stack came up and at what time.
5. Proceed to the next stack.

## Logging

After all stacks are up, summarize:
- Which stacks started successfully
- Any containers that took more than 60 seconds to become healthy
- Final `docker ps` output showing all running containers

Suggest running `/homelab-health-check` as a final verification.
