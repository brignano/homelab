// Grab the guide in batches, each in its own process, and merge the results
// into public/guide.xml.
//
// Why this exists
// ---------------
// The stock grab is one process for every channel, and it keeps everything it
// fetches until it writes the guide at the end. With ~600 channels its heap
// grows past any sensible limit: on 2026-10-11 it died at request 21 of 1,842
// with a 510 MB heap, and again at request 1,185 with 1.5 GB, both times with
// "JavaScript heap out of memory" and no guide written. A process per batch
// returns its memory when it exits, so the peak is one batch, not the night.
//
// The guide is replaced only after every batch has run, via a temp file and a
// rename, so Jellyfin never reads half a guide. A batch that fails is logged
// and skipped: a guide missing some channels beats yesterday's guide for all
// of them. If every batch fails, guide.xml is left alone and its age makes
// hl-epg-stale fire.
//
// Run by pm2 (pm2.config.cjs) on CRON_SCHEDULE and at startup, or by hand:
//   docker exec -d epg node /hl/grab-all.mjs
//
//   EPG_BATCH  channels per batch (default 50)
import fs from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'

const PUBLIC = 'public'
const CHANNELS = path.join(PUBLIC, 'channels.xml')
const GUIDE = path.join(PUBLIC, 'guide.xml')
const WORK = path.join(PUBLIC, '.batches')
const LOCK = path.join(PUBLIC, '.grab.lock')
const BATCH = Math.max(1, Number(process.env.EPG_BATCH) || 50)
// A grab that is still running after this long has died without cleaning up.
const STALE_LOCK_MS = 12 * 60 * 60 * 1000

function log(msg) {
  console.log(`grab-all: ${msg}`)
}

// One grab at a time. The 01:00 schedule can land while a startup or manual
// grab is still going, and two would scrape every site twice.
function lock() {
  try {
    fs.writeFileSync(LOCK, String(Date.now()), { flag: 'wx' })
    return true
  } catch {
    const age = Date.now() - fs.statSync(LOCK).mtimeMs
    if (age < STALE_LOCK_MS) return false
    log(`removing a lock left ${Math.round(age / 3600000)}h ago`)
    fs.writeFileSync(LOCK, String(Date.now()))
    return true
  }
}

if (!fs.existsSync(CHANNELS)) {
  log(`${CHANNELS} does not exist; nothing to grab`)
  process.exit(1)
}
if (!lock()) {
  log('another grab is running; not starting a second')
  process.exit(0)
}

let exitCode = 0
try {
  const channels = fs.readFileSync(CHANNELS, 'utf8').match(/<channel\b[^>]*>[^<]*<\/channel>/g) || []
  fs.rmSync(WORK, { recursive: true, force: true })
  fs.mkdirSync(WORK, { recursive: true })

  const batches = []
  for (let i = 0; i < channels.length; i += BATCH) batches.push(channels.slice(i, i + BATCH))
  log(`${channels.length} channels in ${batches.length} batches of up to ${BATCH}`)

  const parts = []
  batches.forEach((batch, n) => {
    const name = `part-${String(n + 1).padStart(3, '0')}`
    const list = path.join(WORK, `${name}.channels.xml`)
    const out = path.join(WORK, `${name}.xml`)
    fs.writeFileSync(
      list,
      `<?xml version="1.0" encoding="UTF-8"?>\n<channels>\n  ${batch.join('\n  ')}\n</channels>\n`
    )
    log(`batch ${n + 1}/${batches.length}`)
    const r = spawnSync('npm', ['run', 'grab', '--', `--channels=${list}`, `--output=${out}`], {
      stdio: 'inherit'
    })
    if (r.status === 0 && fs.existsSync(out)) parts.push(out)
    else log(`batch ${n + 1} failed (exit ${r.status ?? r.signal}); its channels are left out`)
  })

  if (parts.length === 0) {
    log('every batch failed; leaving the existing guide in place')
    exitCode = 1
  } else {
    // XMLTV puts every <channel> before any <programme>, so collect each kind
    // across the parts and write them in that order.
    const chans = []
    const progs = []
    for (const p of parts) {
      const xml = fs.readFileSync(p, 'utf8')
      chans.push(...(xml.match(/<channel\b[\s\S]*?<\/channel>/g) || []))
      progs.push(...(xml.match(/<programme\b[\s\S]*?<\/programme>/g) || []))
    }
    const tmp = `${GUIDE}.tmp`
    const fd = fs.openSync(tmp, 'w')
    fs.writeSync(fd, '<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n')
    for (const c of chans) fs.writeSync(fd, `${c}\n`)
    for (const p of progs) fs.writeSync(fd, `${p}\n`)
    fs.writeSync(fd, '</tv>\n')
    fs.closeSync(fd)
    fs.renameSync(tmp, GUIDE)
    log(`wrote ${GUIDE}: ${chans.length} channels, ${progs.length} programmes from ${parts.length}/${batches.length} batches`)
  }
} finally {
  fs.rmSync(WORK, { recursive: true, force: true })
  fs.rmSync(LOCK, { force: true })
}
process.exit(exitCode)
