Diagnose a failing or unhealthy Docker container in this homelab.

Ask me for the container name if I haven't provided it.

Then run through this diagnostic sequence and report findings at each step:
1. `docker ps -a --filter name=<container>` — is it running, exited, restarting?
2. `docker logs --tail 50 <container>` — last 50 lines of logs
3. `docker inspect <container>` — check health status, restart count, mounts, env (redact secret values)
4. Check the relevant `docker/<stack>/docker-compose.yml` for misconfiguration
5. Check that the `.env` file exists and all required variables are set

Based on findings, suggest the most likely fix. If the fix involves editing a compose file or env file, make the edit. After any fix, tell me the exact command to restart the service:
  `docker compose -f docker/<stack>/docker-compose.yml up -d --force-recreate <service>`
