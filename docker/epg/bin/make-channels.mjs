// Build public/channels.xml: the guide channels that match the playlists
// Jellyfin plays, one source site per channel.
//
// Why this exists
// ---------------
// The grabber needs a list of (site, site_id, xmltv_id) to fetch. iptv-org/epg
// ships one per site under sites/*/*.channels.xml, but no list for "the
// channels in this playlist". Grabbing whole sites is the alternative, and
// tvtv.us alone is 2,300 channels, almost all of which Jellyfin would never
// show. So the list is derived instead, from two things that are already true:
//
//   - the playlists' tvg-id values, which are the same ids the guide uses as
//     xmltv_id (e.g. "ABC.us@East"), so Jellyfin matches them without mapping;
//   - the sites/ directory baked into this image, so the list always matches
//     the scrapers the grabber actually has.
//
// A channel listed by several sites is taken from the site that covers the
// most of the playlist. Fewer sites means fewer scrapers that can break and
// fewer hosts asked for pages.
//
// Runs at container start (start.sh). A failure here keeps the previous list:
// a playlist host being down for a minute is no reason to lose the guide.
//
// Two rules choose between several entries for the same channel:
//
//   - EPG_SKIP drops sources known to be dead, so the channel falls through
//     to the next site that lists it. Entries are "<site>" or
//     "<site>/<site_id prefix>": i.mjh.nz/Flash/ is one feed of a site whose
//     other feeds (Plex, Pluto, Roku) work. Found from `docker logs epg`: a dead
//     feed returns 404 for every request.
//   - Within a site, an entry for the channel's own country wins. i.mjh.nz
//     lists NBC News NOW under Foxtel (Australia) as well as Plex/us, and file
//     order would otherwise pick Australia's.
//
// Usage: node make-channels.mjs <sites dir> <output file>
//   EPG_PLAYLISTS  whitespace- or comma-separated playlist URLs
//   EPG_SKIP       whitespace- or comma-separated sources to leave out
import fs from 'node:fs'
import path from 'node:path'

const [sitesDir = 'sites', outFile = 'public/channels.xml'] = process.argv.slice(2)
const urls = (process.env.EPG_PLAYLISTS || '').split(/[\s,]+/).filter(Boolean)
const skip = (process.env.EPG_SKIP || '').split(/[\s,]+/).filter(Boolean)

function skipped(site, siteId) {
  const key = `${site}/${siteId}`
  return skip.some((s) => site === s || key.startsWith(s))
}

// "CNN.us@SD" -> "us". The same code appears in i.mjh.nz's feed ids
// ("Plex/us#...", "SamsungTVPlus/us#..."), which is what lets a site's US entry
// be preferred over another country's.
function countryOf(id) {
  return /\.([a-z]{2})(?:@|$)/.exec(id)?.[1] || ''
}

function localTo(id, siteId) {
  const cc = countryOf(id)
  return cc !== '' && new RegExp(`(^|[/#])${cc}([/#]|$)`).test(siteId)
}

function log(msg) {
  console.log(`make-channels: ${msg}`)
}

if (urls.length === 0) {
  log('EPG_PLAYLISTS is empty; keeping the existing channel list')
  process.exit(0)
}

const wanted = new Set()
let fetched = 0
for (const url of urls) {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(60_000) })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const text = await res.text()
    for (const m of text.matchAll(/tvg-id="([^"]+)"/g)) wanted.add(m[1])
    fetched++
  } catch (err) {
    log(`could not read ${url}: ${err.message}`)
  }
}

// Every playlist must answer. A partial list would drop a whole country's
// guide until the next restart, which is worse than keeping yesterday's.
if (fetched !== urls.length || wanted.size === 0) {
  log('not every playlist answered; keeping the existing channel list')
  process.exit(0)
}

// site -> Map(xmltv_id -> [candidate entries, in file order])
const bySite = new Map()
for (const site of fs.readdirSync(sitesDir)) {
  const dir = path.join(sitesDir, site)
  if (!fs.statSync(dir).isDirectory()) continue
  for (const file of fs.readdirSync(dir)) {
    if (!file.endsWith('.channels.xml')) continue
    const xml = fs.readFileSync(path.join(dir, file), 'utf8')
    for (const m of xml.matchAll(/<channel\b[^>]*>[^<]*<\/channel>/g)) {
      const el = m[0]
      const id = /\bxmltv_id="([^"]+)"/.exec(el)?.[1]
      const s = /\bsite="([^"]+)"/.exec(el)?.[1]
      const siteId = /\bsite_id="([^"]*)"/.exec(el)?.[1] || ''
      if (!id || !s || !wanted.has(id) || skipped(s, siteId)) continue
      if (!bySite.has(s)) bySite.set(s, new Map())
      if (!bySite.get(s).has(id)) bySite.get(s).set(id, [])
      bySite.get(s).get(id).push({ el, siteId, local: localTo(id, siteId), dead: false })
    }
  }
}

// The entry a site would use for a channel: the first live one for the
// channel's own country, else the first live one.
function pick(entries) {
  const live = entries.filter((e) => !e.dead)
  return live.find((e) => e.local) || live[0]
}

// i.mjh.nz ids go stale. Its sites/ list is generated from the feeds at one
// moment, and a feed can renumber afterwards: on 2026-10-12 every Plex/us id
// in the list began 5e20b730...- while the live feed used 6a1610be...-, the
// part after the dash unchanged, and 109 channels grabbed 0 programmes without
// an error. So each i.mjh.nz feed this list would use is read once here:
// an id the feed still has is kept, one that only matches on the part after
// the last dash is rewritten to the live id, and one that is gone is marked
// dead so the channel falls through to the next entry or site. A feed that
// cannot be read is left as it is.
const IMJH = 'i.mjh.nz'
const imjhBase = (() => {
  try {
    const cfg = fs.readFileSync(path.join(sitesDir, IMJH, `${IMJH}.config.js`), 'utf8')
    return /API_ENDPOINT\s*=\s*'([^']+)'/.exec(cfg)?.[1]
  } catch {
    return undefined
  }
})()
const feedIds = new Map() // feed path -> Set of live channel ids, or null if unreadable
let repaired = 0
let dropped = 0

async function readFeed(feed) {
  try {
    const res = await fetch(`${imjhBase}/${feed}.xml`, { signal: AbortSignal.timeout(120_000) })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const text = await res.text()
    return new Set([...text.matchAll(/<channel id="([^"]+)"/g)].map((m) => m[1]))
  } catch (err) {
    log(`could not check ${IMJH} feed ${feed}: ${err.message}; using its ids as listed`)
    return null
  }
}

function check(entry, live) {
  const [feed, chId] = entry.siteId.split('#')
  if (live.has(chId)) return
  const tail = chId.slice(chId.lastIndexOf('-') + 1)
  const matches = [...live].filter((l) => l.slice(l.lastIndexOf('-') + 1) === tail)
  if (chId.includes('-') && matches.length === 1) {
    entry.siteId = `${feed}#${matches[0]}`
    entry.el = entry.el.replace(/\bsite_id="[^"]*"/, `site_id="${entry.siteId}"`)
    repaired++
  } else {
    entry.dead = true
    dropped++
  }
}

// Check the feeds the current picks use; a dead pick can promote another
// entry from a feed not yet checked, so repeat until nothing changes.
if (imjhBase && bySite.has(IMJH)) {
  for (;;) {
    const pending = new Set()
    for (const entries of bySite.get(IMJH).values()) {
      const e = pick(entries)
      if (e && !feedIds.has(e.siteId.split('#')[0])) pending.add(e.siteId.split('#')[0])
    }
    if (pending.size === 0) break
    for (const feed of pending) {
      const live = await readFeed(feed)
      feedIds.set(feed, live)
      if (!live) continue
      for (const entries of bySite.get(IMJH).values()) {
        for (const e of entries) {
          if (!e.dead && e.siteId.startsWith(`${feed}#`)) check(e, live)
        }
      }
    }
  }
}

const chosen = new Map()
const used = new Map()
const coverage = (channels) => [...channels.values()].filter((entries) => pick(entries)).length
const sites = [...bySite.entries()].sort(
  (a, b) => coverage(b[1]) - coverage(a[1]) || a[0].localeCompare(b[0])
)
for (const [site, channels] of sites) {
  for (const [id, entries] of channels) {
    if (chosen.has(id)) continue
    const e = pick(entries)
    if (!e) continue
    chosen.set(id, e.el)
    used.set(site, (used.get(site) || 0) + 1)
  }
}

if (chosen.size === 0) {
  log(`no guide source for any of ${wanted.size} playlist channels; keeping the existing list`)
  process.exit(0)
}

const body = [...chosen.values()].map((el) => `  ${el}`).join('\n')
const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<channels>\n${body}\n</channels>\n`

// Write then rename, so a grab starting at the same moment never reads half a file.
const tmp = `${outFile}.tmp`
fs.writeFileSync(tmp, xml)
fs.renameSync(tmp, outFile)

const summary = [...used.entries()].map(([s, n]) => `${s} ${n}`).join(', ')
const skipNote = skip.length ? `; skipping ${skip.join(', ')}` : ''
const imjhNote = repaired || dropped ? `; ${IMJH}: ${repaired} stale ids repaired, ${dropped} gone` : ''
log(`${chosen.size} of ${wanted.size} playlist channels have a guide source (${summary})${skipNote}${imjhNote}`)
