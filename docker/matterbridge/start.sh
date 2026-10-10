#!/bin/sh
# Entrypoint for the matterbridge container. It does four things before handing
# over to Matterbridge:
#
#   1. Refuses to start without IPv6. Matter runs on IPv6 (mDNS on [::]:5353),
#      and without it Matterbridge dies with "MdnsService unavailable ...
#      EAFNOSUPPORT", which does not say what to fix.
#   2. Binds the web UI to loopback only. See README → "The web UI".
#   3. Writes the Home Assistant plugin's config from the environment. Git owns
#      it: the token comes from .env, and the label filter and domain
#      blacklist come from here. Edits made in the web UI are overwritten on
#      the next start, which is intended. What Alexa can reach is a security
#      decision, so it lives in a reviewed file.
#   4. Registers the plugin (a no-op after the first run).
set -eu

# `grep -q .`, not `[ -s ]`: files in /proc report a size of 0 however much
# they hold, so `-s` called every host IPv6-less, the first deploy included.
if ! grep -q . /proc/net/if_inet6 2>/dev/null; then
  echo "matterbridge: this host has no IPv6, and Matter needs it." >&2
  echo "  Check on CT 100: cat /proc/net/if_inet6  (must list eth0)" >&2
  echo "  See docker/matterbridge/README.md → Before you start." >&2
  exit 1
fi

# Loopback, and deliberately not configurable: the UI can install npm packages
# as root on host networking and shows the HA token. Reach it over SSH.
BIND=127.0.0.1

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
# pairs once. --frontend 8283 is the default, named so the README and the
# healthcheck have something to agree with.
# --novirtual: without it Matterbridge adds its own Restart and Update
# switches to the bridge, and Alexa would offer them. "Update" runs npm inside
# the container, around the versions the Dockerfile pins.
exec matterbridge --docker --bridge --novirtual --bind "$BIND" --frontend 8283 --no-ansi
