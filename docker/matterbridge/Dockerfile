# Matterbridge with its Home Assistant plugin baked in. See README.md.
#
# Built here rather than run as the upstream image, because upstream installs
# plugins at runtime (from its web UI, with npm, as root, into the container's
# own filesystem). That leaves the plugin's version out of git, and a recreate
# loses it. Here both versions are pinned and Renovate proposes bumps together
# (renovate.json5 → "matterbridge"): the plugin declares the minimum
# Matterbridge it needs, so the two move as one PR.
FROM luligu/matterbridge:3.10.13@sha256:56bf894ad9f80cd660bc55b79efe52eaaa8fc93d1cff28b6db9e26ce21671699

ARG MATTERBRIDGE_HASS_VERSION=1.5.0
RUN npm install -g --no-fund --no-audit "matterbridge-hass@${MATTERBRIDGE_HASS_VERSION}" \
  && npm cache clean --force

COPY start.sh /usr/local/bin/homelab-start
RUN chmod 0755 /usr/local/bin/homelab-start

# /health on the frontend answers 200 with no login (the frontend password
# guards the API and the WebSocket, not this). The frontend is on loopback
# (start.sh), which on host networking is CT 100's loopback, so `docker ps`
# shows healthy/unhealthy and nothing else can ask.
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:8283/health').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]

ENTRYPOINT ["/usr/local/bin/homelab-start"]
