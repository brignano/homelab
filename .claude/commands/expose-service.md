Add a service to the Cloudflare Tunnel so it's publicly accessible.

Ask me for:
1. The service name and which stack it lives in (e.g. "jellyfin" in `docker/jellyfin/`)
2. The public hostname it should be reachable at (e.g. `jellyfin.yourdomain.com`)
3. Confirmation that this service should be public (double-check: never expose Portainer, Grafana, PostgreSQL, or Prometheus)

Then:
- Remind me to configure the Public Hostname route in the Cloudflare Zero Trust dashboard:
  - Cloudflare Zero Trust → Networks → Tunnels → your tunnel → Public Hostname → Add
  - Service URL: `http://<container-name>:<internal-port>`
  - Hostname: the public domain they provided
- Check that `docker/tunnel/docker-compose.yml` includes the service's network as an external network
  - If missing, add it following the existing pattern and note that I need to re-run `docker compose up -d` for the tunnel stack
- Suggest enabling Cloudflare Access in front of the hostname if the service has no built-in auth (e.g. a raw app preview)
- If Access is going in front of it, settle the denied-visitor experience at the same time — see
  [`docker/tunnel/README.md`](../../docker/tunnel/README.md):
  - Set a **session duration** that matches the service (long for read-mostly, 24h for anything that
    can change the box's state) rather than leaving the 24-hour default
  - Set the **block page** message so a forwarded stranger is pointed at the public site
    (free plan: the custom message string; Pay-as-you-go: `docker/tunnel/access/block-page.html`)
  - For non-browser endpoints, turn on **401 Response for Service Auth policies** so clients get a
    status code instead of HTML
- Write a setup-log entry draft for me to review
