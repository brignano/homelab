// Home page — the landing page at the bare homelab domain (homelab#395).
//
// Plain files served by Caddy; no container. Every number comes from three
// read-only routes on this same origin (docker/proxy/Caddyfile):
//   /api/prom/api/v1/query(_range)  Prometheus: node-exporter, cAdvisor,
//                                   blackbox probes, media-usage.sh
//   /api/graphql + /work-data.js    the Work page's own GitHub reads and
//                                   status rules, so the totals match it
//   /api/home.json                  weather coordinates, Healthchecks project
// and the tile list in services.json beside this file.
'use strict';

const DOMAIN = location.hostname;
const GRAFANA = 'https://stats.' + DOMAIN;
const WORK = 'https://work.' + DOMAIN;
const REFRESH_S = 30;          // box stats and probes
const WORK_REFRESH_S = 300;    // GitHub: a handful of searches, so not every 30 s
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} },
};

// ── Theme: follows the device until the toggle is used ──────────────────────
(function theme() {
  const saved = store.get('home.theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
  $('theme').addEventListener('click', () => {
    const r = document.documentElement;
    const dark = r.getAttribute('data-theme') ? r.getAttribute('data-theme') === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    r.setAttribute('data-theme', dark ? 'light' : 'dark');
    store.set('home.theme', dark ? 'light' : 'dark');
    renderTiles();
  });
})();
const isDark = () => {
  const t = document.documentElement.getAttribute('data-theme');
  return t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
};

// ── Formatting ──────────────────────────────────────────────────────────────
function bytes(n, digits) {
  if (n == null || !isFinite(n)) return ['–', ''];
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (Math.abs(n) >= 1000 && i < u.length - 1) { n /= 1000; i++; }
  const d = digits != null ? digits : (n >= 100 ? 0 : n >= 10 ? 1 : 1);
  return [n.toFixed(d).replace(/\.0$/, ''), u[i]];
}
const b = (n, d) => bytes(n, d).join(' ');
const pct = n => n == null || !isFinite(n) ? '–' : Math.round(n);
function duration(s) {
  if (s == null || !isFinite(s) || s <= 0) return null;
  const d = s / 86400;
  if (d >= 14) return 'about ' + Math.round(d / 7) + ' weeks';
  if (d >= 2) return 'about ' + Math.round(d) + ' days';
  return 'about ' + Math.max(1, Math.round(s / 3600)) + ' hours';
}
const clock = t => t.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

// ── Prometheus ──────────────────────────────────────────────────────────────
async function prom(query) {
  const r = await fetch('/api/prom/api/v1/query?query=' + encodeURIComponent(query));
  if (!r.ok) throw new Error('Prometheus answered ' + r.status);
  const j = await r.json();
  return j.data.result.map(x => ({ m: x.metric, v: +x.value[1] }));
}
async function promRange(query, seconds, step) {
  const end = Math.floor(Date.now() / 1000);
  const r = await fetch('/api/prom/api/v1/query_range?query=' + encodeURIComponent(query) + '&start=' + (end - seconds) + '&end=' + end + '&step=' + step);
  if (!r.ok) throw new Error('Prometheus answered ' + r.status);
  const j = await r.json();
  const s = j.data.result[0];
  return s ? s.values.map(v => +v[1]) : [];
}
const one = rows => rows && rows.length ? rows[0].v : null;

const NET = 'job="node",device!~"lo|veth.*|docker.*|br-.*|tailscale.*"';
const CPU_CHIP = 'chip=~".*(k10temp|coretemp|cpu|acpitz).*"';
const ROOT_FS = 'job="node",mountpoint=~"/|/rootfs"';
const MEDIA_FS = 'job="node",mountpoint=~"(/rootfs)?/media"';
const Q = {
  cpu: '100 * (1 - avg(rate(node_cpu_seconds_total{job="node",mode="idle"}[5m])))',
  cores: 'count(node_cpu_seconds_total{job="node",mode="idle"})',
  load1: 'node_load1{job="node"}',
  load5: 'node_load5{job="node"}',
  load15: 'node_load15{job="node"}',
  topCpu: 'topk(3, sum by (name) (rate(container_cpu_usage_seconds_total{name!=""}[5m])) * 100)',
  memTotal: 'node_memory_MemTotal_bytes{job="node"}',
  memAvail: 'node_memory_MemAvailable_bytes{job="node"}',
  memCache: 'node_memory_Cached_bytes{job="node"} + node_memory_Buffers_bytes{job="node"}',
  swap: 'node_memory_SwapTotal_bytes{job="node"} - node_memory_SwapFree_bytes{job="node"}',
  topMem: 'topk(3, sum by (name) (container_memory_working_set_bytes{name!=""}))',
  fsSize: 'max by (mountpoint) (node_filesystem_size_bytes{' + ROOT_FS + '} or node_filesystem_size_bytes{' + MEDIA_FS + '})',
  fsAvail: 'max by (mountpoint) (node_filesystem_avail_bytes{' + ROOT_FS + '} or node_filesystem_avail_bytes{' + MEDIA_FS + '})',
  fsGrow: 'max by (mountpoint) (-deriv(node_filesystem_avail_bytes{' + ROOT_FS + '}[3d]) or -deriv(node_filesystem_avail_bytes{' + MEDIA_FS + '}[3d]))',
  folders: 'homelab_media_folder_bytes',
  tempCpu: 'max(node_hwmon_temp_celsius{' + CPU_CHIP + '}) or max(node_hwmon_temp_celsius)',
  tempSsd: 'max(node_hwmon_temp_celsius{chip=~".*nvme.*"})',
  tempPeak: 'max_over_time((max(node_hwmon_temp_celsius{' + CPU_CHIP + '}) or max(node_hwmon_temp_celsius))[24h:5m])',
  rx: 'sum(rate(node_network_receive_bytes_total{' + NET + '}[5m]))',
  tx: 'sum(rate(node_network_transmit_bytes_total{' + NET + '}[5m]))',
  rxDay: 'sum(increase(node_network_receive_bytes_total{' + NET + '}[24h]))',
  txDay: 'sum(increase(node_network_transmit_bytes_total{' + NET + '}[24h]))',
  vpn: 'probe_success{service="gluetun"}',
  uptime: 'time() - node_boot_time_seconds{job="node"}',
  containers: 'count(count by (name) (container_last_seen{name!=""} > time() - 120))',
  probeOk: 'probe_success',
  probeMs: 'probe_duration_seconds',
  running: 'container_last_seen{name!=""} > time() - 120',
};

// ── State ───────────────────────────────────────────────────────────────────
let SERVICES = null, CONF = {}, M = {}, SPARK = { cpu: [], net: [] }, open = store.get('home.open'), weather = null;
let lastOk = null, lastErr = null;

async function loadBox() {
  const keys = Object.keys(Q);
  const res = await Promise.allSettled(keys.map(k => prom(Q[k])));
  const next = {};
  let failed = 0;
  keys.forEach((k, i) => { if (res[i].status === 'fulfilled') next[k] = res[i].value; else { next[k] = []; failed++; } });
  if (failed === keys.length) { lastErr = res[0].reason; return; }
  M = next;
  lastOk = new Date(); lastErr = null;
  const [cpu, net] = await Promise.allSettled([
    promRange(Q.cpu, 3600, 120),
    promRange('sum(rate(node_network_receive_bytes_total{' + NET + '}[5m]))', 3600, 120),
  ]);
  if (cpu.status === 'fulfilled') SPARK.cpu = cpu.value;
  if (net.status === 'fulfilled') SPARK.net = net.value;
}

// ── Box stats ───────────────────────────────────────────────────────────────
function fs() {
  const out = { sys: {}, media: {} };
  for (const [k, field] of [['fsSize', 'size'], ['fsAvail', 'avail'], ['fsGrow', 'grow']]) {
    for (const r of M[k] || []) out[/media$/.test(r.m.mountpoint) ? 'media' : 'sys'][field] = r.v;
  }
  return out;
}
function spark(vals, cls) {
  if (vals.length < 2) return '<svg class="spark ' + cls + '" viewBox="0 0 100 28" aria-hidden="true"></svg>';
  const max = Math.max(...vals, 1e-9), min = Math.min(...vals);
  const span = max - min || max || 1;
  const pts = vals.map((v, i) => (i / (vals.length - 1) * 100).toFixed(1) + ' ' + (26 - (v - min) / span * 24).toFixed(1));
  return '<svg class="spark ' + cls + '" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true"><path d="M' + pts.join(' L') + '"/></svg>';
}
const bar = (p, warnAt, badAt) => '<div class="bar"><i class="' + (p >= badAt ? 'bad' : p >= warnAt ? 'warn' : '') + '" style="width:' + Math.max(2, Math.min(100, p || 0)) + '%"></i></div>';

function libraryFolders() {
  // Children of the media drive, with the Jellyfin folder opened up into its
  // own folders, biggest first. Paths come from media-usage.sh (du, 2 deep).
  const rows = (M.folders || []).map(r => ({ path: r.m.path, v: r.v }));
  if (!rows.length) return [];
  const depth = p => p.split('/').filter(Boolean).length;
  const top = Math.min(...rows.map(r => depth(r.path)));
  const kids = p => rows.filter(r => depth(r.path) === depth(p) + 1 && r.path.startsWith(p + '/'));
  const out = [];
  for (const r of rows.filter(x => depth(x.path) === top)) {
    const k = /jellyfin|library/i.test(r.path) ? kids(r.path) : [];
    if (k.length) out.push(...k); else out.push(r);
  }
  const NAME = [[/movie|film/i, 'Movies'], [/^(tv|shows?|series)$/i, 'Shows'], [/download/i, 'Downloading'], [/photo|icloud/i, 'Photos'], [/home|video|clip/i, 'Home videos'], [/music/i, 'Music'], [/record/i, 'Recordings']];
  return out.map(r => {
    const base = r.path.split('/').pop();
    const hit = NAME.find(([re]) => re.test(base));
    return { name: hit ? hit[1] : base, v: r.v };
  }).sort((x, y) => y.v - x.v).slice(0, 6);
}

function stats() {
  const f = fs();
  const memT = one(M.memTotal), memA = one(M.memAvail), memU = memT != null && memA != null ? memT - memA : null;
  const sysP = f.sys.size ? (1 - f.sys.avail / f.sys.size) * 100 : null;
  const medP = f.media.size ? (1 - f.media.avail / f.media.size) * 100 : null;
  const t = one(M.tempCpu);
  const rx = one(M.rx);
  const [rxN, rxU] = bytes(rx, 1);
  const memV = bytes(memU, 1), memTV = bytes(memT, 0);
  return [
    { id: 'cpu', k: 'CPU', v: pct(one(M.cpu)) + '<small>%</small>', viz: spark(SPARK.cpu, 'teal') },
    { id: 'mem', k: 'Memory', v: memV[0] + '<small> / ' + memTV.join(' ') + '</small>', viz: bar(memT ? memU / memT * 100 : 0, 85, 95) },
    { id: 'sys', k: 'System disk', v: pct(sysP) + '<small>%</small>', viz: bar(sysP, 80, 92) },
    { id: 'media', k: 'Media drive', v: pct(medP) + '<small>%</small>', viz: bar(medP, 80, 92) },
    { id: 'temp', k: 'Temperature', v: (t == null ? '–' : Math.round(t)) + '<small>°C</small>', viz: bar(t, 75, 88) },
    { id: 'net', k: 'Network', v: rxN + '<small> ' + (rxU ? rxU + '/s' : '') + '</small>', viz: spark(SPARK.net, 'violet') },
  ];
}

function details(id) {
  const f = fs();
  const g = (uid) => GRAFANA + '/d/' + uid;
  const rows = (list, fmt) => list.map(r => ({ name: r.m.name || r.name, v: r.v, label: fmt(r.v) }));
  switch (id) {
    case 'cpu': {
      const load = ['load1', 'load5', 'load15'].map(k => one(M[k])).filter(v => v != null).map(v => v.toFixed(2));
      return { t: 'CPU', link: g('homelab-capacity'), kv: [['Now', pct(one(M.cpu)) + '%'], ['Load (1 / 5 / 15 min)', load.length ? load.join(' · ') : '–'], ['Cores', one(M.cores) || '–']],
        top: 'Busiest apps', rows: rows(M.topCpu || [], v => (v < 1 ? v.toFixed(1) : Math.round(v)) + '%') };
    }
    case 'mem': {
      const T = one(M.memTotal), A = one(M.memAvail);
      return { t: 'Memory', link: g('homelab-capacity'), kv: [['Used', b(T - A)], ['Cache', b(one(M.memCache))], ['Free', b(A)], ['Swap used', b(one(M.swap))]],
        top: 'Biggest apps', rows: rows(M.topMem || [], v => b(v)) };
    }
    case 'sys': {
      const s = f.sys, grow = s.grow > 0 ? b(s.grow * 604800) + ' a week' : 'not growing';
      return { t: 'System disk', link: g('homelab-capacity'), kv: [['Size', b(s.size)], ['Used', b(s.size - s.avail)], ['Free', b(s.avail)], ['Growing', grow]] };
    }
    case 'media': {
      const s = f.media, full = s.grow > 0 ? duration(s.avail / s.grow) : null;
      const lib = libraryFolders();
      return { t: 'Media drive', link: g('homelab-capacity'), kv: [['Size', b(s.size)], ['Used', b(s.size - s.avail)], ['Free', b(s.avail)], ['Full in', full ? full + ' at this pace' : 'not filling up']],
        top: "What's on it", rows: lib.map(r => ({ name: r.name, v: r.v, label: b(r.v) })), none: 'Measured once a day; the first count appears after scripts/media-usage.sh runs.' };
    }
    case 'temp': {
      const c = one(M.tempCpu), s = one(M.tempSsd), p = one(M.tempPeak);
      const deg = v => v == null ? '–' : Math.round(v) + '°C';
      return { t: 'Temperature', link: g('homelab-capacity'), kv: [['CPU', deg(c)], ['SSD', deg(s)], ['Hottest in 24 h', deg(p)]], none: c == null ? 'No temperature sensor is reporting to Prometheus.' : '' };
    }
    case 'net': {
      const vpn = one(M.vpn);
      return { t: 'Network', link: g('homelab-capacity'), kv: [['Down', b(one(M.rx)) + '/s'], ['Up', b(one(M.tx)) + '/s'], ['Last 24 h', b(one(M.rxDay)) + ' down · ' + b(one(M.txDay)) + ' up'], ['VPN for downloads', vpn == null ? 'Not set up' : vpn ? 'Connected' : 'Down']] };
    }
  }
  return null;
}

function renderStats() {
  $('stats').innerHTML = stats().map(s => '<button type="button" class="stat" data-d="' + s.id + '" aria-expanded="' + (open === s.id) + '" aria-controls="detail"><span class="k">' + s.k + '</span><span class="v">' + s.v + '</span>' + s.viz + '</button>').join('');
  const det = $('detail');
  const d = open && details(open);
  if (!d) { det.hidden = true; return; }
  let h = '<h3>' + esc(d.t) + '<a class="more" href="' + esc(d.link) + '">Open in Grafana →</a></h3><div class="kv">' + d.kv.map(([k, v]) => '<div><span>' + esc(k) + '</span><b>' + esc(v) + '</b></div>').join('') + '</div>';
  if (d.top) {
    if (d.rows && d.rows.length) {
      const max = Math.max(...d.rows.map(r => r.v));
      h += '<div class="top3"><span>' + esc(d.top) + '</span>' + d.rows.map(r => '<div class="r"><span>' + esc(r.name) + '</span>' + bar(r.v / max * 100, 101, 101) + '<em>' + esc(r.label) + '</em></div>').join('') + '</div>';
    } else if (d.none) h += '<div class="empty">' + esc(d.none) + '</div>';
  } else if (d.none) h += '<div class="empty">' + esc(d.none) + '</div>';
  det.innerHTML = h;
  det.hidden = false;
}
$('stats').addEventListener('click', e => {
  const c = e.target.closest('.stat');
  if (!c) return;
  open = open === c.dataset.d ? null : c.dataset.d;
  store.set('home.open', open);
  renderStats();
  const again = document.querySelector('.stat[data-d="' + c.dataset.d + '"]');
  if (again) again.focus();
});

// ── Hero ────────────────────────────────────────────────────────────────────
function moviesAndShows() {
  const lib = libraryFolders().filter(r => r.name === 'Movies' || r.name === 'Shows');
  return lib.length ? lib.reduce((a, r) => a + r.v, 0) : null;
}
function renderHero() {
  const facts = [];
  const up = one(M.uptime);
  if (up != null) facts.push('Up <b>' + (up >= 172800 ? Math.floor(up / 86400) + ' days' : Math.floor(up / 3600) + ' hours') + '</b>');
  const n = one(M.containers);
  if (n != null) facts.push('<b>' + n + '</b> containers');
  const ms = moviesAndShows();
  if (ms != null) facts.push('<b>' + esc(b(ms)) + '</b> of movies and shows');
  if (weather) facts.push('<b>' + Math.round(weather.t) + '°F</b> ' + esc(weather.words));
  $('facts').innerHTML = facts.map(f => '<span class="fact">' + f + '</span>').join('');

  const probed = tiles().filter(t => t.probe);
  const known = probed.filter(t => probeOf(t).ok != null);
  const ok = known.filter(t => probeOf(t).ok).length;
  const o = $('orbit');
  if (!known.length) { $('upn').textContent = '–'; $('upword').textContent = lastErr ? 'no data' : 'checking'; return; }
  const frac = ok / known.length;
  $('upn').innerHTML = ok + '<small>/' + known.length + '</small>';
  $('upword').innerHTML = frac === 1 ? '● <i class="full">all services </i>up' : (known.length - ok) + ' down';
  o.classList.toggle('warn', frac < 1 && frac >= .8);
  o.classList.toggle('bad', frac < .8);
  o.setAttribute('aria-label', ok + ' of ' + known.length + ' services up');
  $('arc').style.strokeDashoffset = String(653.45 * (1 - frac));
}

// ── Tiles ───────────────────────────────────────────────────────────────────
const tiles = () => SERVICES ? SERVICES.sections.flatMap(s => s.tiles) : [];
function probeOf(t) {
  const okRow = (M.probeOk || []).find(r => r.m.instance === t.probe);
  const msRow = (M.probeMs || []).find(r => r.m.instance === t.probe);
  return { ok: okRow ? okRow.v === 1 : null, ms: msRow ? Math.round(msRow.v * 1000) : null };
}
const href = h => h.replace('{domain}', DOMAIN).replace('{healthchecks}', CONF.healthchecks || '');
const INK = {};
async function inkIcon(file) {
  // A mark drawn in one ink (brignano.svg) is inlined and recoloured with the
  // text colour, so it shows on a dark tile and a light one alike.
  if (INK[file] !== undefined) return;
  INK[file] = '';
  try {
    const t = await (await fetch('/icons/' + file)).text();
    const svg = t.slice(t.indexOf('<svg'));
    INK[file] = svg.replace(/stroke="#[0-9a-fA-F]{3,8}"/g, 'stroke="currentColor"').replace(/fill="#[0-9a-fA-F]{3,8}"/g, 'fill="currentColor"').replace('<svg', '<svg aria-hidden="true"');
    renderTiles();
  } catch (_) {}
}
function icon(t) {
  if (t.inkIcon) { inkIcon(t.icon); if (INK[t.icon]) return INK[t.icon]; }
  const file = t.iconLight && !isDark() ? t.iconLight : t.icon;
  return '<img alt="" src="/icons/' + esc(file) + '" loading="lazy">';
}
function status(t) {
  if (t.container) {
    const run = (M.running || []).some(r => r.m.name === t.container);
    return '<span class="st"><span class="pill' + (run ? ' up' : '') + '">' + (run ? 'Running' : M.running ? 'Asleep' : '…') + '</span></span>';
  }
  if (!t.probe) return '';
  const p = probeOf(t);
  if (p.ok == null) return '<span class="st"><span class="pill">' + (lastOk ? 'No check' : '…') + '</span></span>';
  return '<span class="st"><span class="pill ' + (p.ok ? 'up' : 'down') + '">' + (p.ok ? 'Up' : 'Down') + '</span>' + (p.ok && p.ms != null ? '<span class="ms">' + p.ms + ' ms</span>' : '') + '</span>';
}
function renderTiles() {
  if (!SERVICES) return;
  $('groups').innerHTML = SERVICES.sections.map(s => '<section><h2>' + esc(s.name) + '</h2><div class="list">' + s.tiles.map(t =>
    '<a class="svc" href="' + esc(href(t.href)) + '"><span class="ic">' + icon(t) + '</span><span><div class="n">' + esc(t.name) + '</div><div class="d">' + esc(t.desc) + '</div></span>' + status(t) + '</a>'
  ).join('') + '</div></section>').join('');
  const links = (SERVICES.links || []).filter(l => !/\{healthchecks\}/.test(l.href) || CONF.healthchecks);
  $('links').innerHTML = links.map(l => '<a href="' + esc(href(l.href)) + '">' + esc(l.name) + '</a>').join('');
}

// ── Work totals ─────────────────────────────────────────────────────────────
let work = store.get('home.work.v1'), workErr = null;
function renderWork() {
  $('worklink').href = WORK + '/';
  const L = [['you', 'Waiting on me'], ['doing', 'In progress'], ['next', 'Up next']];
  if (!work) {
    $('work').innerHTML = L.map(([s, l]) => '<a class="wk' + (s === 'you' ? ' you' : '') + '" href="' + WORK + '/#' + s + '"><b>–</b><span>' + l + '</span></a>').join('') + (workErr ? '<div class="note">' + esc(workErr) + '</div>' : '');
    return;
  }
  $('work').innerHTML = L.map(([s, l]) => '<a class="wk' + (s === 'you' ? ' you' : '') + '" href="' + WORK + '/#' + s + '"><b>' + (work.counts[s] || 0) + '</b><span>' + l + '</span></a>').join('') + (workErr ? '<div class="note">Could not refresh: ' + esc(workErr) + '</div>' : '');
}
async function loadWork() {
  if (typeof fetchWork !== 'function') { workErr = 'The Work page code did not load.'; return renderWork(); }
  try {
    const c = await fetchWork();
    const counts = {};
    for (const it of classify(c.nodes)) counts[it.s] = (counts[it.s] || 0) + 1;
    work = { at: Date.now(), counts };
    workErr = null;
    store.set('home.work.v1', work);
  } catch (e) { workErr = e.message; }
  renderWork();
}

// ── Weather (Open-Meteo, no key) ────────────────────────────────────────────
const WMO = [[0, 'clear'], [2, 'partly cloudy'], [3, 'cloudy'], [48, 'foggy'], [57, 'drizzle'], [67, 'rain'], [77, 'snow'], [82, 'showers'], [86, 'snow showers'], [99, 'thunderstorms']];
async function loadWeather() {
  if (!CONF.lat || !CONF.lon) return;
  try {
    const r = await fetch('https://api.open-meteo.com/v1/forecast?latitude=' + encodeURIComponent(CONF.lat) + '&longitude=' + encodeURIComponent(CONF.lon) + '&current=temperature_2m,weather_code&temperature_unit=fahrenheit');
    const j = await r.json();
    const code = j.current.weather_code;
    weather = { t: j.current.temperature_2m, words: (WMO.find(([max]) => code <= max) || [0, ''])[1] };
    renderHero();
  } catch (_) {}
}

// ── Loop ────────────────────────────────────────────────────────────────────
function renderAll() {
  renderHero(); renderStats(); renderTiles(); renderWork();
  $('foot').textContent = lastErr && !lastOk ? 'Could not reach Prometheus: ' + lastErr.message
    : lastOk ? 'Updated ' + clock(lastOk) + ' · refreshes every ' + REFRESH_S + ' seconds' + (lastErr ? ' · last refresh failed' : '') : 'Loading…';
}
async function tick() {
  try { await loadBox(); } catch (e) { lastErr = e; }
  renderAll();
}

(async function start() {
  $('host').textContent = DOMAIN;
  renderWork();
  const [svc, conf] = await Promise.allSettled([fetch('/services.json').then(r => r.json()), fetch('/api/home.json').then(r => r.json())]);
  if (svc.status === 'fulfilled') SERVICES = svc.value;
  if (conf.status === 'fulfilled') CONF = conf.value || {};
  renderAll();
  tick();
  setInterval(() => { if (!document.hidden) tick(); }, REFRESH_S * 1000);
  if (!work || Date.now() - work.at > WORK_REFRESH_S * 1000) loadWork();
  setInterval(() => { if (!document.hidden) loadWork(); }, WORK_REFRESH_S * 1000);
  loadWeather();
  setInterval(loadWeather, 15 * 60 * 1000);
})();
