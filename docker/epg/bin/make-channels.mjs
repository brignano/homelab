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

// site -> Map(xmltv_id -> the <channel> element, verbatim)
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
      const have = bySite.get(s).get(id)
      // First entry wins, unless a later one is for the channel's own country
      // and the first was not.
      if (!have || (!have.local && localTo(id, siteId))) {
        bySite.get(s).set(id, { el, local: localTo(id, siteId) })
      }
    }
  }
}

const sites = [...bySite.entries()].sort((a, b) => b[1].size - a[1].size || a[0].localeCompare(b[0]))
const chosen = new Map()
const used = new Map()
for (const [site, channels] of sites) {
  for (const [id, { el }] of channels) {
    if (chosen.has(id)) continue
    chosen.set(id, el)
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
log(`${chosen.size} of ${wanted.size} playlist channels have a guide source (${summary})${skipNote}`)
