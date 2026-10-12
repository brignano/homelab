// Build public/playlist.m3u: the channels from EPG_PLAYLISTS whose streams
// actually answer, for Jellyfin's M3U tuner to read instead of the raw lists.
//
// Why this exists
// ---------------
// iptv-org's playlists list every stream someone found once, and many of them
// no longer play from here. On 2026-10-12, with the US list as the tuner:
//   - Pluto channels behind jmp2.uk redirect to Pluto's stitcher, which
//     answered some of them 403, so FFmpeg exited with code 8 at once;
//   - WFTV's master playlist answered 200, but every variant playlist under it
//     was something FFmpeg could not parse ("Invalid data"), so playback hung;
//   - three Stirr channels on frequency.stream answered 503.
// Jellyfin shows every channel in the list whether or not it plays, so the
// guide filled with channels that only spin. Checking each stream the way a
// player would, and listing only those that pass, is what makes the channel
// list trustworthy. It cannot catch everything: a stream that plays for a
// minute and then stalls passes.
//
// The check, per channel, with the headers the playlist asks for (or the
// browser user agent Jellyfin sends):
//   1. the URL answers 2xx with an HLS playlist, or with video;
//   2. for a master playlist, its first variant answers with a media playlist;
//   3. the media playlist's first segment answers 2xx.
//
// Runs at container start (pm2.config.cjs) and at the start of every grab
// (grab-all.mjs), so it follows the nightly schedule. The file is replaced
// only when every playlist answered and a sensible share of channels passed:
// a box that has lost its internet connection would otherwise publish an
// empty channel list.
//
// Usage: node check-streams.mjs [output file]
//   EPG_PLAYLISTS        whitespace- or comma-separated playlist URLs
//   EPG_STREAM_SKIP      hosts to leave out without checking (default none)
//   EPG_REQUIRE_GUIDE    "true" also leaves out channels with no listings in
//                        public/guide.xml (default false); see below
//   EPG_LANGS            guide languages to keep, e.g. "en,es" (default all);
//                        a channel's language is its guide source's lang
//                        attribute in public/channels.xml
import fs from 'node:fs'

const outFile = process.argv[2] || 'public/playlist.m3u'
const urls = (process.env.EPG_PLAYLISTS || '').split(/[\s,]+/).filter(Boolean)
const skipHosts = (process.env.EPG_STREAM_SKIP || '').split(/[\s,]+/).filter(Boolean)
const requireGuide = process.env.EPG_REQUIRE_GUIDE === 'true'
const langs = (process.env.EPG_LANGS || '').split(/[\s,]+/).filter(Boolean)
const CONCURRENCY = 8
const TIMEOUT_MS = 10_000
// Below this share of channels passing, assume the box, not the streams, is
// the problem and keep the previous list.
const MIN_PASS_SHARE = 0.2
// The user agent Jellyfin's FFmpeg sends (its live TV log, 2026-10-12), so a
// stream that answers this check answers Jellyfin too.
const DEFAULT_UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'

function log(msg) {
  console.log(`check-streams: ${msg}`)
}

if (urls.length === 0) {
  log('EPG_PLAYLISTS is empty; keeping the existing playlist')
  process.exit(0)
}

// One entry: the lines before its URL (#EXTINF, #EXTVLCOPT ...) and the URL.
function parse(text) {
  const entries = []
  let lines = []
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith('#EXTM3U')) continue
    if (line.startsWith('#')) {
      lines.push(line)
      continue
    }
    entries.push({ lines, url: line })
    lines = []
  }
  return entries
}

function headersFor(entry) {
  const h = { 'user-agent': DEFAULT_UA }
  for (const l of entry.lines) {
    const m = /^#EXTVLCOPT:http-(user-agent|referrer)=(.+)$/i.exec(l)
    if (m) h[m[1].toLowerCase() === 'referrer' ? 'referer' : 'user-agent'] = m[2]
  }
  return h
}

// GET, keeping at most the first 256 KB: enough for any playlist, and a
// segment or raw video stream is abandoned after its first chunk.
async function get(url, headers) {
  const res = await fetch(url, { headers, redirect: 'follow', signal: AbortSignal.timeout(TIMEOUT_MS) })
  if (!res.ok) {
    res.body?.cancel().catch(() => {})
    throw new Error(`HTTP ${res.status}`)
  }
  const type = res.headers.get('content-type') || ''
  if (!res.body) return { url: res.url, type, body: Buffer.alloc(0) }
  const reader = res.body.getReader()
  const chunks = []
  let size = 0
  while (size < 256 * 1024) {
    const { done, value } = await reader.read()
    if (done) break
    chunks.push(value)
    size += value.length
    if (/^(video|audio)\//.test(type) || type.includes('mp2t')) break
  }
  reader.cancel().catch(() => {})
  return { url: res.url, type, body: Buffer.concat(chunks) }
}

const isPlaylist = (r) => r.body.toString('utf8', 0, 64).replace(/^﻿/, '').trimStart().startsWith('#EXTM3U')
const isVideo = (r) => /^(video|audio)\//.test(r.type) || r.type.includes('mp2t') || r.body[0] === 0x47

// First URI line after a tag matching `after` (or the first URI at all).
function firstUri(text, after) {
  const lines = text.split(/\r?\n/).map((l) => l.trim())
  let armed = !after
  for (const l of lines) {
    if (after && after.test(l)) armed = true
    else if (armed && l && !l.startsWith('#')) return l
  }
  return undefined
}

async function check(entry) {
  const headers = headersFor(entry)
  const top = await get(entry.url, headers)
  if (!isPlaylist(top)) {
    if (isVideo(top)) return
    throw new Error('not a playlist or video')
  }
  let media = top
  let text = top.body.toString('utf8')
  if (text.includes('#EXT-X-STREAM-INF')) {
    const v = firstUri(text, /^#EXT-X-STREAM-INF/)
    if (!v) throw new Error('master playlist without variants')
    media = await get(new URL(v, top.url).href, headers)
    if (!isPlaylist(media)) throw new Error('variant is not a playlist')
    text = media.body.toString('utf8')
  }
  if (!text.includes('#EXTINF')) throw new Error('media playlist without segments')
  const seg = firstUri(text, /^#EXTINF/)
  if (!seg) throw new Error('no segment URI')
  await get(new URL(seg, media.url).href, headers)
}

// Fetch every playlist; all must answer, as in make-channels.mjs.
const entries = []
const seen = new Set()
for (const url of urls) {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(60_000) })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    for (const e of parse(await res.text())) {
      if (seen.has(e.url)) continue
      seen.add(e.url)
      entries.push(e)
    }
  } catch (err) {
    log(`could not read ${url}: ${err.message}; keeping the existing playlist`)
    process.exit(0)
  }
}

// Which channels have a guide, and in which language.
//
// A guide source in channels.xml is not the same as listings: on 2026-10-12,
// with EPG_REQUIRE_GUIDE on, Jellyfin still showed empty rows for A&E, 3ABN
// and others whose source grabbed nothing. So "has a guide" means the last
// grab's guide.xml has programmes for the channel. Before the first grab there
// is no guide.xml, and channels.xml is used instead.
//
// The language comes from channels.xml, where each source carries the lang it
// is grabbed in. The US playlist also lists Arabic-language channels whose
// listings come in Arabic; EPG_LANGS=en,es leaves those out.
const unescape = (s) => s.replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
let guided
let langOf
if (requireGuide || langs.length) {
  let channelsXml = ''
  try {
    channelsXml = fs.readFileSync('public/channels.xml', 'utf8')
  } catch {
    log('public/channels.xml is missing; not filtering on guide or language')
  }
  if (channelsXml) {
    langOf = new Map()
    for (const m of channelsXml.matchAll(/<channel\b[^>]*>/g)) {
      const id = /\bxmltv_id="([^"]+)"/.exec(m[0])?.[1]
      const lang = /\blang="([^"]+)"/.exec(m[0])?.[1]
      if (id) langOf.set(unescape(id), lang || '')
    }
    if (requireGuide) {
      guided = new Set()
      try {
        const xml = fs.readFileSync('public/guide.xml', 'utf8')
        for (const m of xml.matchAll(/<programme\b[^>]*\bchannel="([^"]+)"/g)) guided.add(unescape(m[1]))
      } catch {}
      if (guided.size === 0) {
        log('public/guide.xml has no programmes yet; requiring a guide source instead')
        guided = new Set(langOf.keys())
      }
    }
  }
}

const reasons = new Map()
function note(reason) {
  reasons.set(reason, (reasons.get(reason) || 0) + 1)
}

const candidates = entries.filter((e) => {
  let host = ''
  try {
    host = new URL(e.url).hostname
  } catch {
    note('bad URL')
    return false
  }
  if (skipHosts.some((s) => host === s || host.endsWith(`.${s}`))) {
    note('skipped host')
    return false
  }
  const id = /tvg-id="([^"]+)"/.exec(e.lines.join(' '))?.[1]
  if (guided && (!id || !guided.has(id))) {
    note('no guide')
    return false
  }
  if (langs.length && langOf) {
    const lang = id && langOf.get(id)
    // A channel with no guide source has no known language; keep it unless
    // EPG_REQUIRE_GUIDE already left it out.
    if (lang && !langs.includes(lang)) {
      note(`language ${lang}`)
      return false
    }
  }
  return true
})

const passed = new Array(candidates.length).fill(false)
let next = 0
async function worker() {
  while (next < candidates.length) {
    const i = next++
    try {
      await check(candidates[i])
      passed[i] = true
    } catch (err) {
      const reason = err.name === 'TimeoutError' || err.name === 'AbortError' ? 'timeout' : err.message
      note(/^HTTP \d+$/.test(reason) ? reason : reason.split(':')[0])
    }
  }
}
await Promise.all(Array.from({ length: CONCURRENCY }, worker))

const keep = candidates.filter((_, i) => passed[i])
if (keep.length === 0 || keep.length < candidates.length * MIN_PASS_SHARE) {
  log(`only ${keep.length} of ${candidates.length} streams answered; keeping the existing playlist`)
  process.exit(0)
}

const body = keep.map((e) => [...e.lines, e.url].join('\n')).join('\n')
const tmp = `${outFile}.tmp`
fs.writeFileSync(tmp, `#EXTM3U\n${body}\n`)
fs.renameSync(tmp, outFile)

const why = [...reasons.entries()]
  .sort((a, b) => b[1] - a[1])
  .map(([r, n]) => `${r} ${n}`)
  .join(', ')
log(`${keep.length} of ${entries.length} channels play; wrote ${outFile}${why ? ` (left out: ${why})` : ''}`)
