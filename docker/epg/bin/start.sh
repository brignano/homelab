#!/bin/sh
# Container entrypoint: refresh the channel list, then hand over to pm2 (the
# guide server on :3000 and the scheduled grab) with our own config, whose grab
# runs in batches (grab-all.mjs) rather than the image's one-process grab.
# make-channels.mjs never fails the start; see its header.
cd /epg || exit 1
node /hl/make-channels.mjs sites public/channels.xml || true
# ffprobe for check-streams.mjs's last step (does FFmpeg find a video stream?).
# The image has none, and the container is recreated nightly, so this runs on
# each start. A failure only skips that step.
if ! command -v ffprobe >/dev/null 2>&1; then
  apk add --no-cache ffmpeg >/dev/null 2>&1 \
    || echo "start.sh: could not install ffmpeg; streams are checked without ffprobe"
fi
# Grab at startup only when there is no guide yet. Every nightly image update
# recreates this container at 04:00, and a full grab each time would be a
# second scrape of the listings sites a night, which is how you get blocked.
# pm2.config.cjs reads this variable.
if [ -s public/guide.xml ]; then
  export RUN_AT_STARTUP=false
else
  export RUN_AT_STARTUP=true
fi
exec pm2-runtime /hl/pm2.config.cjs
