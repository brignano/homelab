#!/bin/sh
# Container entrypoint: refresh the channel list, then hand over to the image's
# own process manager (pm2: the guide server on :3000 and the scheduled grab).
# make-channels.mjs never fails the start; see its header.
cd /epg || exit 1
node /hl/make-channels.mjs sites public/channels.xml || true
# Grab at startup only when there is no guide yet. Every nightly image update
# recreates this container at 04:00, and a full grab each time would be a
# second scrape of the listings sites a night, which is how you get blocked.
# pm2.config.js reads this variable.
if [ -s public/guide.xml ]; then
  export RUN_AT_STARTUP=false
else
  export RUN_AT_STARTUP=true
fi
exec pm2-runtime pm2.config.js
