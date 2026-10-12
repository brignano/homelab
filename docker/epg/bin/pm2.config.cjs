// Replaces the image's own pm2.config.js. Same three apps: the guide server on
// :3000, the grab on CRON_SCHEDULE, and a grab at startup when RUN_AT_STARTUP
// is true (start.sh decides). The difference is the grab command: grab-all.mjs
// runs it in batches instead of one process for every channel, which ran out
// of memory twice (see grab-all.mjs).
const grab = 'node /hl/grab-all.mjs'
// Every app runs from the image's /epg, where `public/` and `npm run grab`
// live. Stated rather than left to pm2, which may default to this file's
// directory (/hl) now that the config is not the image's own.
const cwd = '/epg'

const apps = [
  {
    name: 'serve',
    script: 'npx serve -- public',
    cwd,
    instances: 1,
    watch: false,
    autorestart: true
  },
  {
    name: 'grab',
    script: `npx chronos -e "${grab}" -p "${process.env.CRON_SCHEDULE}" -l`,
    cwd,
    instances: 1,
    watch: false,
    autorestart: true
  }
]

if (process.env.RUN_AT_STARTUP === 'true') {
  apps.push({
    name: 'grab-at-startup',
    script: grab,
    cwd,
    instances: 1,
    autorestart: false,
    watch: false,
    max_restarts: 1
  })
}

module.exports = { apps }
