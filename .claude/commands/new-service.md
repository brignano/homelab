Scaffold a new Docker Compose service stack in this homelab repo.

Ask me for:
1. Service name (e.g. "jellyfin")
2. Docker image and tag
3. Network exposure: Tailscale-only or Cloudflare Tunnel (public)
4. Any required environment variables or volumes

Then:
- Create `docker/<name>/docker-compose.yml` following the existing stack pattern:
  - Named volumes, `restart: unless-stopped`, ports bound to `127.0.0.1`
  - Required secrets use `${VAR:?required}` syntax
  - A dedicated bridge network named after the service
- Create `docker/<name>/.env.example` with all variables and placeholder values
- Update the stack table in `CLAUDE.md`
- If the service should be public, add a note about wiring it into `docker/tunnel/`
- Suggest a `docs/setup-log.md` entry for me to fill in
