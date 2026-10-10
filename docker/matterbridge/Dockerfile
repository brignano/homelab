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

# /health on the frontend answers 200 with no login (the frontend password, if
# set, guards the API and the WebSocket, not this). start.sh writes the address
# it bound to, since that is only known at runtime. start-period covers
# Matterbridge's own startup delay: within 5 minutes of a host boot it waits
# 120s before starting.
HEALTHCHECK --interval=60s --timeout=10s --start-period=180s --retries=3 \
  CMD ["node", "-e", "const a=require('fs').readFileSync('/tmp/frontend-addr','utf8').trim();fetch('http://'+a+':8283/health').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]

ENTRYPOINT ["/usr/local/bin/homelab-start"]
