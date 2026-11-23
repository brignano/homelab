// Ideas — every idea in the ledger, read live from GitHub on each load. The
// only call is GET /api/ledger.json, which Caddy answers by reading one JSON
// file from the ledger's repo with a read-only token it holds. The browser
// never sees the token, the repo name or the path.
//
// ledger.json is an array of ideas (or { "ideas": [...] }), one object each:
//   title, summary, stage (inbox|proposals|accepted|building|archive),
//   cls (kind, may start with an emoji), ev (E0-E4 or ""), tone
//   (good|side|bet|park|none), verdict, and optionally repo (the fleet repo
//   it is being built in) and url (its page on GitHub).
// An idea that names a repo counts as Building, whatever its stage says.
'use strict';

const PROOF = { E0: 'Just an idea', E1: 'Researched', E2: 'Someone wants it', E3: 'Someone committed', E4: 'Proven', '': 'Not scored' };
const WANTED = ['E2', 'E3', 'E4'];
const TONE = { good: ['ok', 'Go'], side: ['att', 'Look'], bet: ['info', 'Bet'], park: ['', 'Parked'], none: ['', 'Unscored'] };
const KINDS = [
  ['Income', 'Could earn money'], ['Quality of life', 'Makes my days easier'], ['People', 'For people I know'],
  ['Public good', 'Helps strangers'], ['Craft', 'Fun to build'], ['Unclassified', 'Not sorted yet'],
];
const SECTIONS = [
  ['building', 'Building', 'Being built now'],
  ['proposals', 'Proposals', 'Written up, waiting on evidence or a yes'],
  ['inbox', 'Inbox', 'Captured, not yet worked'],
];
const STAGE_NAME = { building: 'Building', proposals: 'Proposals', inbox: 'Inbox', archive: 'Archive' };
const COUNTS = [['go', 'Go: ready to move'], ['building', 'Building'], ['proposals', 'Proposals'], ['untested', 'Untested: nobody has asked yet']];
const INBOX_SHOWN = 5;

const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// Work lives beside this page: ideas.<domain> -> work.<domain>.
const WORK = location.origin.replace('//ideas.', '//work.');
$('worklink').href = WORK + '/';

let ideas = [], loadedAt = null, updating = false;
const st = { kind: 'All', proof: 'any', q: '', count: null, stage: 'All', open: null, inboxAll: false };
const CACHE = 'ideas.cache.v1';

function normalise(raw) {
  const list = Array.isArray(raw) ? raw : (raw && Array.isArray(raw.ideas) ? raw.ideas : null);
  if (!list) throw new Error('ledger.json is not a list of ideas');
  return list.map((d, i) => {
    const kind = String(d.cls || d.kind || 'Unclassified').replace(/^\S+\s(?=[A-Z])/u, '').trim() || 'Unclassified';
    let stage = String(d.stage || 'inbox').toLowerCase();
    if (stage === 'accepted') stage = 'proposals';
    if (d.repo && stage !== 'archive') stage = 'building';
    if (!STAGE_NAME[stage]) stage = 'inbox';
    const ev = PROOF[d.ev] ? d.ev : '';
    const tone = TONE[d.tone] ? d.tone : 'none';
    const url = /^https:\/\/github\.com\//.test(d.url || '') ? d.url : '';
    const repo = /^[A-Za-z0-9._-]+$/.test(d.repo || '') ? d.repo : '';
    return { id: i, title: d.title || 'Untitled', summary: d.summary || '', verdict: d.verdict || '', kind, stage, ev, tone, url, repo };
  });
}

function apply(c) { ideas = normalise(c.data); loadedAt = new Date(c.at); render(); }

async function fetchLedger() {
  const r = await fetch('/api/ledger.json', { headers: { Accept: 'application/json' }, cache: 'no-store' });
  if (r.status === 401 || r.status === 403) throw new Error('GitHub refused the token. Set READ_GITHUB_TOKEN in docker/proxy/.env (docker/ideas/README.md).');
  if (r.status === 404) throw new Error('No ledger.json yet. Set IDEAS_LEDGER_REPO in docker/proxy/.env, and have the ledger build write ledger.json (docker/ideas/README.md).');
  if (!r.ok) throw new Error('GitHub answered ' + r.status + '.');
  return { data: await r.json(), at: Date.now() };
}

async function load() {
  updating = true;
  if (!loadedAt) {
    try { const c = JSON.parse(localStorage.getItem(CACHE) || 'null'); if (c && c.data) apply(c); } catch (_) {}
  }
  if (loadedAt) render();
  try {
    const c = await fetchLedger();
    updating = false;
    apply(c);
    try { localStorage.setItem(CACHE, JSON.stringify(c)); } catch (_) {}
  } catch (e) {
    updating = false;
    if (loadedAt) { render(); $('sub').textContent += ' · could not refresh: ' + e.message; return; }
    $('sub').textContent = 'Could not read the ledger';
    $('lists').innerHTML = '<div class="state err" role="alert">' + esc(e.message) + '</div>';
  }
}

// ── Rendering ───────────────────────────────────────────────────────────────

const isGo = d => d.tone === 'good' && d.stage !== 'archive';
const isWanted = d => WANTED.includes(d.ev);
const COUNT_TEST = {
  go: isGo,
  building: d => d.stage === 'building',
  proposals: d => d.stage === 'proposals',
  untested: d => d.stage !== 'archive' && !isWanted(d),
};

function visible() {
  const q = st.q.trim().toLowerCase();
  return ideas.filter(d =>
    (st.kind === 'All' || d.kind === st.kind) &&
    (st.proof === 'any' || isWanted(d)) &&
    (!st.count || COUNT_TEST[st.count](d)) &&
    (st.stage === 'All' || d.stage === st.stage) &&
    (!q || (d.title + ' ' + d.summary + ' ' + d.verdict).toLowerCase().includes(q)));
}

function row(d) {
  const [pc, pw] = TONE[d.tone];
  const open = st.open === d.id;
  let h = '<div class="row" role="button" tabindex="0" data-id="' + d.id + '" aria-expanded="' + open + '">' +
    '<span class="t">' + esc(d.title) + '<span class="chev">' + (open ? '⌄' : '›') + '</span></span>' +
    '<span class="pill ' + pc + '">' + pw + '</span>' +
    '<div class="facts"><span class="proof' + (isWanted(d) ? '' : ' weak') + '"><span class="fl">Proof</span>' + esc(PROOF[d.ev]) + '</span>' +
    '<span class="kind"><span class="fl">Kind</span>' + esc(d.kind) + '</span>' +
    (d.repo ? '<a class="repo" href="' + esc(WORK + '/#repo=' + encodeURIComponent(d.repo)) + '">Building in ' + esc(d.repo) + ' →</a>' : '') + '</div>' +
    '<div class="verdict">' + esc(d.verdict) + '</div>';
  if (open) {
    h += '<div class="det">' + (d.summary ? '<div><h3>What it is</h3><p>' + esc(d.summary) + '</p></div>' : '') +
      (d.url ? '<div class="acts"><a class="btn" href="' + esc(d.url) + '" target="_blank" rel="noopener">Open in GitHub</a></div>' : '') + '</div>';
  }
  return h + '</div>';
}

function render() {
  const vis = visible();
  const live = ideas.filter(d => d.stage !== 'archive');
  const when = loadedAt ? loadedAt.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '';
  $('sub').textContent = ideas.length + ' ideas · ' + live.length + ' still in play' + (when ? ' · read ' + when : '') + (updating ? ' · updating…' : '');

  $('counts').innerHTML = COUNTS.map(([k, l]) => '<button type="button" class="count' + (k === 'go' ? ' go' : '') + '" data-c="' + k + '" aria-pressed="' + (st.count === k) + '"><b>' + ideas.filter(COUNT_TEST[k]).length + '</b><span>' + l + '</span></button>').join('');

  const stageRow = (s, list) => '<tr tabindex="0" data-s="' + s + '"' + (st.stage === s ? ' class="sel"' : '') + '><td>' + (s === 'All' ? 'All' : STAGE_NAME[s]) + '</td><td' + (list.length ? '' : ' class="z"') + '>' + list.length + '</td><td' + (list.filter(isWanted).length ? '' : ' class="z"') + '>' + list.filter(isWanted).length + '</td><td' + (list.filter(isGo).length ? '' : ' class="z"') + '>' + list.filter(isGo).length + '</td></tr>';
  $('stagetab').innerHTML = stageRow('All', ideas) + Object.keys(STAGE_NAME).map(s => stageRow(s, ideas.filter(d => d.stage === s))).join('');

  $('kindlbl').textContent = st.kind === 'All' ? 'All kinds' : st.kind;
  $('kindmenu').innerHTML = '<button type="button" role="menuitemradio" data-k="All" aria-checked="' + (st.kind === 'All') + '"><b>All kinds</b></button>' +
    KINDS.filter(([k]) => ideas.some(d => d.kind === k)).map(([k, hint]) => '<button type="button" role="menuitemradio" data-k="' + esc(k) + '" aria-checked="' + (st.kind === k) + '"><b>' + esc(k) + '</b><small>' + hint + '</small></button>').join('');
  document.querySelectorAll('#proof button').forEach(b => b.setAttribute('aria-pressed', b.dataset.v === st.proof));

  const f = $('filt');
  const parts = [];
  if (st.count) parts.push(COUNTS.find(c => c[0] === st.count)[1]);
  if (st.stage !== 'All') parts.push(STAGE_NAME[st.stage]);
  f.hidden = !parts.length;
  if (parts.length) f.innerHTML = 'Only ' + esc(parts.join(', ')) + ' <button type="button" id="clearf" aria-label="Clear this filter">×</button>';

  const sections = SECTIONS.filter(([k]) => st.stage === 'All' || st.stage === k).map(([k, l, why]) => {
    const list = vis.filter(d => d.stage === k);
    if (!list.length && st.stage === 'All' && (st.count || st.q || st.kind !== 'All' || st.proof !== 'any')) return '';
    const cut = k === 'inbox' && !st.inboxAll && list.length > INBOX_SHOWN + 1 ? INBOX_SHOWN : list.length;
    return '<section><h2>' + l + ' <span class="n">' + list.length + '</span></h2><p class="why">' + why + '</p><div class="list">' +
      (list.length ? list.slice(0, cut).map(row).join('') : '<div class="empty">Nothing here.</div>') +
      (cut < list.length ? '<button class="more" type="button" id="inboxall">Show all ' + list.length + '</button>' : '') + '</div></section>';
  }).join('');
  const arch = vis.filter(d => d.stage === 'archive');
  $('lists').innerHTML = sections ? '<div class="grid3">' + sections + '</div>' : (arch.length ? '' : '<div class="state">No ideas match. Clear a filter or the search to see more.</div>');
  $('archbox').hidden = !arch.length;
  $('archsum').textContent = 'Archive · ' + arch.length + ' parked with a reason';
  $('arch').innerHTML = arch.map(row).join('');
  if (st.stage === 'archive') $('archbox').open = true;
}

// ── Events ──────────────────────────────────────────────────────────────────

function closeMenu() { $('kindmenu').hidden = true; $('kindbtn').setAttribute('aria-expanded', 'false'); }

document.addEventListener('click', e => {
  const t = e.target;
  if (t.closest('#kindbtn')) {
    const m = $('kindmenu'); m.hidden = !m.hidden; $('kindbtn').setAttribute('aria-expanded', String(!m.hidden));
    if (!m.hidden) (m.querySelector('[aria-checked=true]') || m.querySelector('button')).focus();
    return;
  }
  const k = t.closest('#kindmenu [data-k]');
  if (k) { st.kind = k.dataset.k; closeMenu(); render(); $('kindbtn').focus(); return; }
  if (!t.closest('.menu')) closeMenu();
  const p = t.closest('#proof [data-v]');
  if (p) { st.proof = p.dataset.v; render(); return; }
  const c = t.closest('.count[data-c]');
  if (c) { st.count = st.count === c.dataset.c ? null : c.dataset.c; render(); return; }
  const s = t.closest('#stagetab [data-s]');
  if (s) { st.stage = s.dataset.s; render(); return; }
  if (t.closest('#clearf')) { st.count = null; st.stage = 'All'; render(); return; }
  if (t.closest('#inboxall')) { st.inboxAll = true; render(); return; }
  if (t.closest('#refresh')) { load(); return; }
  if (t.closest('a, .det')) return;
  const r = t.closest('.row[data-id]');
  if (r) { const id = Number(r.dataset.id); st.open = st.open === id ? null : id; render(); }
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !$('kindmenu').hidden) { closeMenu(); $('kindbtn').focus(); return; }
  if (!$('kindmenu').hidden && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
    const items = [...$('kindmenu').querySelectorAll('button')];
    const i = items.indexOf(document.activeElement);
    items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
    e.preventDefault();
    return;
  }
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('.row[data-id], #stagetab tr')) { e.preventDefault(); e.target.click(); }
});

$('q').addEventListener('input', e => { st.q = e.target.value; render(); });

load();
