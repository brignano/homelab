#!/bin/sh
# Entrypoint for the matterbridge container. It does four things before handing
# over to Matterbridge:
#
#   1. Refuses to start without IPv6. Matter runs on IPv6 (mDNS on [::]:5353),
#      and without it Matterbridge dies with "MdnsService unavailable ...
#      EAFNOSUPPORT", which does not say what to fix.
#   2. Works out where to bind the web UI: docker0's address (172.17.0.1 by
#      default), not every interface. See README → "The web UI".
#   3. Writes the Home Assistant plugin's config from the environment. Git owns
#      it: the token comes from .env, and the label filter and domain
#      blacklist come from here. Edits made in the web UI are overwritten on
#      the next start, which is intended. What Alexa can reach is a security
#      decision, so it lives in a reviewed file.
#   4. Registers the plugin (a no-op after the first run).
set -eu

if [ ! -s /proc/net/if_inet6 ]; then
  echo "matterbridge: this host has no IPv6, and Matter needs it." >&2
  echo "  Check on CT 100: cat /proc/sys/net/ipv6/conf/all/disable_ipv6  (must be 0)" >&2
  echo "  See docker/matterbridge/README.md → Before you start." >&2
  exit 1
fi

BIND="${MATTERBRIDGE_BIND:-}"
if [ -z "$BIND" ]; then
  BIND=$(node -e "const i=require('os').networkInterfaces().docker0||[];const a=i.find(x=>x.family==='IPv4');process.stdout.write(a?a.address:'')")
fi
if [ -z "$BIND" ]; then
  echo "matterbridge: no docker0 IPv4 address to bind the web UI to." >&2
  echo "  Set MATTERBRIDGE_BIND in docker/matterbridge/.env. Never 0.0.0.0: see README → The web UI." >&2
  exit 1
fi
printf '%s\n' "$BIND" > /tmp/frontend-addr

CONFIG_DIR=/root/.matterbridge
mkdir -p "$CONFIG_DIR"
umask 077
# Written by node so the token can never break the JSON, whatever it contains.
node -e '
const fs = require("fs");
const config = {
  name: "matterbridge-hass",
  type: "DynamicPlatform",
  version: "1.0.0",
  host: process.env.HA_URL,
  token: process.env.HA_TOKEN,
  certificatePath: "",
  rejectUnauthorized: false,
  reconnectTimeout: 60,
  // Effectively forever: one retry a minute for about two years. HA restarts
  // after every update, and the plugin default (10 tries) would leave the
  // bridge dark after ten minutes of HA being down. 0 means NEVER retry.
  reconnectRetries: 1000000,
  // Only what carries this HA label reaches Alexa. Opt-in, per entity.
  filterByLabel: process.env.HA_LABEL,
  whiteList: [],
  blackList: [],
  entityWhiteList: [],
  // Never exposed, label or not:
  //   lock, cover, valve  doors, the garage, water. Anyone near an Echo, or a
  //                       TV saying the right words, is a user.
  //   alarm_control_panel the alarm. (The plugin does not map it today; this
  //                       keeps it out if a release starts to.)
  //   button, remote, media_player
  //                       each carries an on/off switch that stops Alexa
  //                       merging a device'"'"'s entities (plugin changelog).
  entityBlackList: ["lock", "cover", "valve", "alarm_control_panel", "button", "remote", "media_player"],
  deviceEntityBlackList: {},
  splitEntities: [],
  splitByLabel: "",
  splitNameStrategy: "Entity name",
  controllerStrategy: "Merge",
  namePostfix: "",
  postfix: "",
  airQualityRegex: "",
  enableServerRvc: true,
  discardHiddenEntities: true,
  virtualControlLabel: "",
  debug: false,
  unregisterOnShutdown: false,
};
const path = "/root/.matterbridge/matterbridge-hass.config.json";
fs.writeFileSync(path + ".tmp", JSON.stringify(config, null, 2) + "\n", { mode: 0o600 });
fs.renameSync(path + ".tmp", path);
'

matterbridge --add matterbridge-hass --no-ansi >/dev/null

# --bridge: one Matter bridge with every exposed entity behind it, so Alexa
# pairs once. --frontend 8283 is the default, named so the README, the
# healthcheck and prometheus.yml have something to agree with.
exec matterbridge --docker --bridge --bind "$BIND" --frontend 8283 --no-ansi
