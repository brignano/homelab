// Work — open issues and PRs across the fleet repos, read live from GitHub on
// every load (homelab#386). Read-only: the only call is POST /api/graphql,
// which Caddy forwards to api.github.com with a read-only token it holds. The
// browser never sees the token.
//
// Status follows the fleet standard's table ("Status, priority and blocked" in
// brignano/ai-tools fleet/README.md), computed from labels here instead of by
// the fleet-board Action. Which repos count comes from ai-tools fleet/board.yml
// at load, so a repo added there shows up with no change to this page.
'use strict';

const OWNER = 'brignano';
const API = '/api/graphql';
const IDLE_DAYS = 30;      // matches Fleet backlog health
const STALL_HOURS = 24;    // agent:working with nothing since then
const SEC = { you: 'Waiting on you', doing: 'In progress', next: 'Up next', held: 'Blocked or held' };
const COUNT_LABEL = { you: 'Waiting on you', doing: 'In progress', next: 'Ready', held: 'Blocked or held' };
const SORTS = { fleet: 'Fleet order', prio: 'Priority', new: 'Newest', old: 'Oldest', upd: 'Recently updated', idle: 'Longest idle' };

// Optional repo -> Claude project URL map for Start's "Open project" link.
// Lives in config.json beside this file; a missing file or repo falls back to
// claude.ai/code.
let PROJECTS = {};

let items = [], done = [], boardRepos = [], loadedAt = null;
let st = { repo: 'All', group: 'status', sort: 'fleet', status: null, open: null, q: '' };
try { Object.assign(st, JSON.parse(localStorage.getItem('work.prefs') || '{}'), { open: null, q: '' }); } catch (_) {}
const save = () => { try { localStorage.setItem('work.prefs', JSON.stringify({ repo: st.repo, group: st.group, sort: st.sort, status: st.status })); } catch (_) {} };

const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const days = iso => Math.max(0, Math.floor((Date.now() - new Date(iso)) / 86400000));
const hours = iso => (Date.now() - new Date(iso)) / 3600000;
const ago = n => n === 0 ? 'today' : n + 'd';

// ── GitHub ──────────────────────────────────────────────────────────────────

async function gql(query, variables) {
  const r = await fetch(API, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query, variables }) });
  let body = null;
  try { body = await r.json(); } catch (_) {}
  if (r.status === 401) throw new Error('GitHub refused the token (401). Check WORK_GITHUB_TOKEN in docker/proxy/.env, then recreate caddy.');
  if (!r.ok) throw new Error('GitHub answered ' + r.status + (body && body.message ? ': ' + body.message : '') + '.');
  if (body && body.errors && !body.data) throw new Error(body.errors.map(e => e.message).join('; '));
  return body.data;
}

// board.yml is two flat lists; this reads exactly that shape and no more.
function parseBoard(text) {
  const out = { repos: [], never: [] };
  let cur = null;
  for (const raw of text.split('\n')) {
    const line = raw.replace(/#.*$/, '').trimEnd();
    if (!line.trim()) continue;
    const key = line.match(/^([a-z_]+):\s*$/);
    if (key) { cur = out[key[1]] ? key[1] : null; continue; }
    if (/^\S/.test(line)) { cur = null; continue; }
    const item = line.match(/^\s+-\s+([A-Za-z0-9._-]+)\s*$/);
    if (item && cur) out[cur].push(item[1]);
  }
  return out;
}

const FIELDS = `
  __typename
  ... on Issue {
    number title url body createdAt updatedAt
    author { login } authorAssociation
    repository { name }
    labels(first: 30) { nodes { name } }
    comments(last: 1) { nodes { author { login } bodyText createdAt } }
    closedByPullRequestsReferences(first: 5, includeClosedPrs: false) { nodes { number url } }
  }
  ... on PullRequest {
    number title url body createdAt updatedAt isDraft reviewDecision
    author { login } authorAssociation
    repository { name }
    labels(first: 30) { nodes { name } }
    comments(last: 1) { nodes { author { login } bodyText createdAt } }
    commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
    closingIssuesReferences(first: 5) { nodes { number repository { name } } }
  }`;

const Q_BOARD = `query { repository(owner: "${OWNER}", name: "ai-tools") { object(expression: "HEAD:fleet/board.yml") { ... on Blob { text } } } }`;
const Q_OPEN = `query($q: String!, $after: String) { search(type: ISSUE, query: $q, first: 50, after: $after) { issueCount pageInfo { hasNextPage endCursor } nodes { ${FIELDS} } } }`;
const Q_DONE = `query($q: String!, $after: String) { search(type: ISSUE, query: $q, first: 50, after: $after) { pageInfo { hasNextPage endCursor } nodes { __typename ... on Issue { number title url closedAt repository { name } } ... on PullRequest { number title url closedAt merged repository { name } } } } }`;

// The last good load is kept in this browser and drawn at once on the next
// visit, then replaced when GitHub answers (about 2-3 s). It is this viewer's
// copy of what the token already shows them; nothing is cached server-side.
const CACHE = 'work.cache.v1';
let updating = false;

function apply(c) {
  boardRepos = c.repos;
  items = classify(c.nodes);
  done = c.done;
  loadedAt = new Date(c.at);
  render();
}

async function load() {
  updating = true;
  let shown = false;
  if (!loadedAt) {
    try { const c = JSON.parse(localStorage.getItem(CACHE) || 'null'); if (c && c.nodes) { apply(c); shown = true; } } catch (_) {}
  }
  if (!shown && !loadedAt) $('sub').textContent = 'Reading GitHub…';
  else render();
  try {
    // Short queries, filtered here: a repo: qualifier per repo would hit
    // search's 256-character limit as the fleet grows. GitHub's issue search
    // now returns NOTHING unless the query says is:issue or is:pr (found on
    // the first live load, 2026-11-22), so each kind is its own search.
    // Every search runs at once; only a search's own pages are in sequence.
    const pages = async (query, q) => {
      const out = [];
      let after = null;
      for (let page = 0; page < 10; page++) {
        const d = await gql(query, { q, after });
        out.push(...d.search.nodes);
        if (!d.search.pageInfo.hasNextPage) break;
        after = d.search.pageInfo.endCursor;
      }
      return out;
    };
    const open = `user:${OWNER} is:open archived:false`;
    const since = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
    const shut = `user:${OWNER} is:closed closed:>=${since}`;
    const [b, oi, op, ci, cp] = await Promise.all([
      gql(Q_BOARD),
      pages(Q_OPEN, open + ' is:issue'), pages(Q_OPEN, open + ' is:pr'),
      pages(Q_DONE, shut + ' is:issue'), pages(Q_DONE, shut + ' is:pr'),
    ]);
    const text = b.repository && b.repository.object && b.repository.object.text;
    if (!text) throw new Error('Could not read fleet/board.yml from ai-tools. The token needs Contents: read on ai-tools.');
    const board = parseBoard(text);
    const never = new Set(board.never);
    const repos = board.repos.filter(r => !never.has(r));
    const want = new Set(repos);
    const mine = n => n && n.repository && want.has(n.repository.name);
    const c = {
      at: Date.now(), repos,
      nodes: oi.concat(op).filter(mine),
      done: ci.concat(cp).filter(mine).sort((x, y) => new Date(y.closedAt) - new Date(x.closedAt)),
    };
    updating = false;
    apply(c);
    try { localStorage.setItem(CACHE, JSON.stringify(c)); } catch (_) {}
  } catch (e) {
    updating = false;
    if (loadedAt) { render(); $('sub').textContent += ' · could not refresh: ' + e.message; return; }
    $('sub').textContent = 'Could not read GitHub';
    $('lists').innerHTML = '<div class="state err" role="alert">' + esc(e.message) + '</div>';
  }
}

// ── Status, per the fleet table ─────────────────────────────────────────────

function section(body, name) {
  if (!body) return '';
  const re = new RegExp('^##\\s+' + name + '\\s*$', 'im');
  const m = body.match(re);
  if (!m) return '';
  const rest = body.slice(m.index + m[0].length);
  const end = rest.search(/^##\s+/m);
  return (end === -1 ? rest : rest.slice(0, end)).trim();
}

const checkWord = s => ({ SUCCESS: 'checks passing', FAILURE: 'checks failing', ERROR: 'checks failing', PENDING: 'checks running', EXPECTED: 'checks running' }[s] || '');
const BOT = a => !a || /\[bot\]$/.test(a.login) || a.login === 'renovate' || a.login === 'dependabot';

function classify(nodes) {
  const prs = nodes.filter(n => n.__typename === 'PullRequest');
  const prCheck = pr => { const c = pr.commits.nodes[0]; return c && c.commit.statusCheckRollup ? c.commit.statusCheckRollup.state : null; };
  // A PR that closes an issue we show rides on that issue's row instead of
  // getting one of its own (the fleet table puts both in "In review").
  const prByIssue = {};
  for (const pr of prs) for (const i of pr.closingIssuesReferences.nodes) {
    prByIssue[i.repository.name + '#' + i.number] = pr;
  }
  const out = [];
  for (const n of nodes) {
    const labels = n.labels.nodes.map(l => l.name);
    const has = l => labels.includes(l);
    const pl = labels.find(l => /^priority:p[123]$/.test(l));
    const sz = labels.find(l => /^size:/.test(l));
    const ty = labels.find(l => /^type:/.test(l));
    const it = {
      kind: n.__typename === 'PullRequest' ? 'PR' : 'Issue',
      repo: n.repository.name, n: n.number, id: n.repository.name + '#' + n.number,
      title: n.title, url: n.url, body: n.body || '', labels,
      p: pl ? +pl.slice(-1) : 4, size: sz ? sz.slice(5).toUpperCase() : '', type: ty ? ty.slice(5) : '',
      c: days(n.createdAt), u: days(n.updatedAt), uh: hours(n.updatedAt),
      last: n.comments.nodes[0] || null, meta: [], pill: null, s: 'next', ask: '',
    };
    if (it.kind === 'PR') {
      const chk = prCheck(n);
      if (n.closingIssuesReferences.nodes.some(i => nodes.some(x => x.__typename === 'Issue' && x.repository.name === i.repository.name && x.number === i.number))) continue;
      if (has('needs:anthony')) {
        it.s = 'you';
        it.pill = chk === 'SUCCESS' && n.reviewDecision !== 'CHANGES_REQUESTED' ? ['Ready to merge', 'ok'] : ['Needs you', 'att'];
      } else if (BOT(n.author)) {
        it.s = 'held'; it.pill = ['Bot', '']; it.meta.push('from ' + (n.author ? n.author.login : 'a bot'));
      } else if (n.isDraft) {
        it.s = 'doing'; it.pill = ['Draft', ''];
      } else {
        it.s = 'doing';
        it.pill = chk === 'FAILURE' || chk === 'ERROR' ? ['Checks failing', 'bad'] : ['In review', 'info'];
      }
      if (checkWord(chk) && it.pill[0] !== 'Checks failing') it.meta.push(checkWord(chk));
      if (n.reviewDecision === 'APPROVED') it.meta.push('approved');
    } else {
      const pr = prByIssue[it.id] || (n.closedByPullRequestsReferences.nodes[0] && prs.find(p => p.url === n.closedByPullRequestsReferences.nodes[0].url));
      const blockedBy = (it.body.match(/Blocked by\s+([\w.-]+\/[\w.-]+#\d+|[\w.-]+#\d+|#\d+)/i) || [])[1];
      const decided = /^##\s+Decisions\s*$/im.test(it.body);
      const outsider = BOT(n.author) || !['OWNER', 'MEMBER', 'COLLABORATOR'].includes(n.authorAssociation);
      const filedSinceRule = new Date(n.createdAt) >= new Date('2026-11-02');
      if (has('needs:anthony')) {
        it.s = 'you'; it.pill = [has('blocked') ? 'Blocked' : 'Needs you', 'att'];
      } else if (has('blocked')) {
        it.s = 'held'; it.pill = ['Blocked', '']; if (blockedBy) it.meta.push('by ' + blockedBy.replace(OWNER + '/', ''));
      } else if (has('agent:pause')) {
        it.s = 'held'; it.pill = ['Paused', '']; it.meta.push('paused');
      } else if (has('lead')) {
        it.s = 'held'; it.pill = ['Lead', ''];
      } else if (has('agent:working')) {
        it.s = 'doing';
        if (pr) {
          const chk = prCheck(pr);
          it.pill = chk === 'FAILURE' || chk === 'ERROR' ? ['Checks failing', 'bad'] : ['In review', 'info'];
          it.meta.push('PR #' + pr.number);
          if (checkWord(chk) && it.pill[0] !== 'Checks failing') it.meta.push(checkWord(chk));
          it.prUrl = pr.url;
        } else if (it.uh >= STALL_HOURS) {
          it.pill = ['Stalled', 'att']; it.stalled = true; it.meta.push('no activity for ' + ago(it.u));
        } else {
          it.pill = ['Building', ''];
        }
      } else if (/^Release plan:/i.test(n.title) || outsider || (filedSinceRule && !decided)) {
        it.s = 'held'; it.pill = ['Proposed', ''];
        it.meta.push(/^Release plan:/i.test(n.title) ? 'release plan' : outsider ? 'from ' + (n.author ? n.author.login : 'outside') : 'no Decisions yet');
      }
    }
    if (it.s === 'you' && it.last) it.ask = firstSentence(it.last.bodyText);
    out.push(it);
  }
  return out;
}

function firstSentence(t) {
  const s = (t || '').replace(/\s+/g, ' ').replace(/---.*$/, '').trim();
  const m = s.match(/^(.{20,200}?[.?!])(\s|$)/);
  return m ? m[1] : s.slice(0, 160) + (s.length > 160 ? '…' : '');
}

// ── Rendering ───────────────────────────────────────────────────────────────

const SZ = { S: 0, M: 1 };
function sorter(a, b) {
  switch (st.sort) {
    case 'prio': return a.p - b.p || b.c - a.c;
    case 'new': return a.c - b.c;
    case 'old': return b.c - a.c;
    case 'upd': return a.uh - b.uh;
    case 'idle': return b.uh - a.uh;
    default: return a.p - b.p || (SZ[a.size] ?? 2) - (SZ[b.size] ?? 2) || b.c - a.c;
  }
}
// Waiting on you: PRs ready to merge first, whatever the sort.
const youSort = (a, b) => (a.pill && a.pill[0] === 'Ready to merge' ? 0 : 1) - (b.pill && b.pill[0] === 'Ready to merge' ? 0 : 1) || sorter(a, b);

// A small Markdown subset: paragraphs, bullet lists, `code`. Everything is
// escaped first, so a body can never inject markup.
function md(text) {
  const blocks = [];
  let list = null, para = [];
  const inline = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
  const flush = () => { if (para.length) { blocks.push('<p>' + inline(para.join(' ')) + '</p>'); para = []; } if (list) { blocks.push('<ul>' + list.map(li => '<li>' + inline(li) + '</li>').join('') + '</ul>'); list = null; } };
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    const li = line.match(/^[-*]\s+(?:\[[ xX]\]\s+)?(.*)$/);
    if (!line || /^<!--/.test(line)) { flush(); continue; }
    if (li) { if (para.length) { blocks.push('<p>' + inline(para.join(' ')) + '</p>'); para = []; } (list = list || []).push(li[1]); continue; }
    if (list) flush();
    para.push(line.replace(/^#+\s*/, ''));
  }
  flush();
  return blocks.join('');
}

function details(d) {
  const prob = section(d.body, 'Problem');
  const ac = section(d.body, 'Acceptance criteria');
  let html = '';
  if (prob || ac) {
    if (prob) html += '<div><h3>Problem</h3>' + md(prob) + '</div>';
    if (ac) html += '<div><h3>Acceptance criteria</h3>' + md(ac) + '</div>';
  } else if (d.body.trim()) {
    const first = d.body.split(/\n\s*\n/).filter(p => p.trim() && !/^<!--/.test(p.trim())).slice(0, 2).join('\n\n');
    html += '<div><h3>Description</h3>' + md(first) + '</div>';
  } else {
    html += '<div><p class="q">No description.</p></div>';
  }
  if (d.last) {
    const who = d.last.author ? d.last.author.login : 'someone';
    const lt = d.last.bodyText.replace(/\s+/g, ' ').trim();
    html += '<div><h3>Last comment · ' + esc(who) + ' · ' + esc(ago(days(d.last.createdAt))) + (days(d.last.createdAt) ? ' ago' : '') + '</h3><p class="q">' + esc(lt.length > 400 ? lt.slice(0, 400) + '…' : lt) + '</p></div>';
  }
  if (d.labels.length) html += '<div class="labels">' + d.labels.map(l => '<span>' + esc(l) + '</span>').join('') + '</div>';
  html += '<div class="acts"><a class="btn" href="' + esc(d.url) + '" target="_blank" rel="noopener">Open on GitHub</a>' +
    (d.prUrl ? '<a class="btn" href="' + esc(d.prUrl) + '" target="_blank" rel="noopener">Open PR</a>' : '') +
    (d.s === 'next' ? '<button class="start" type="button" data-ref="' + esc(d.id) + '">Start</button>' : '') + '</div>';
  return '<div class="det">' + html + '</div>';
}

function row(d, showStatus) {
  const open = st.open === d.id;
  const right = d.s === 'next' ? '<button class="start" type="button" data-ref="' + esc(d.id) + '">Start</button>'
    : '<span class="pill ' + (d.pill ? d.pill[1] : '') + '">' + esc(d.pill ? d.pill[0] : '') + '</span>';
  const meta = [
    showStatus ? SEC[d.s] : '', d.id, d.kind === 'PR' ? '' : (d.p < 4 ? 'p' + d.p : 'no priority') + (d.size ? ' · ' + d.size : ''),
    ...d.meta, 'opened ' + ago(d.c),
  ].filter(Boolean).map(m => '<span>' + esc(m) + '</span>').join('') + (d.u >= IDLE_DAYS ? '<span class="stale">idle ' + d.u + 'd</span>' : '');
  return '<div class="row" data-id="' + esc(d.id) + '" aria-expanded="' + open + '">' +
    '<button class="t" type="button">' + (d.kind === 'PR' ? '<span class="k">PR</span>' : '') + esc(d.title) + '<span class="chev" aria-hidden="true">' + (open ? '−' : '+') + '</span></button>' +
    right + (d.ask ? '<div class="ask">' + esc(d.ask) + '</div>' : '') + '<div class="meta">' + meta + '</div>' + (open ? details(d) : '') + '</div>';
}

function block(title, list, cls, showStatus, cap) {
  const shown = cap && !st.showAll ? list.slice(0, cap) : list;
  const more = cap && list.length > cap ? '<button class="more" type="button" id="showall">' + (st.showAll ? 'Show top ' + cap : 'Show all ' + list.length) + '</button>' : '';
  return '<section><h2>' + esc(title) + ' <span class="n">' + list.length + '</span></h2><div class="list ' + (cls || '') + '">' +
    (shown.length ? shown.map(d => row(d, showStatus)).join('') : '<div class="empty">Nothing here.</div>') + more + '</div></section>';
}

function counts(r) {
  const z = items.filter(d => r === 'All' || d.repo === r);
  const c = s => z.filter(d => d.s === s).length;
  return { you: c('you'), doing: c('doing'), next: c('next'), held: c('held'), n: z.length };
}

function render() {
  if (!loadedAt) return;
  const active = [...new Set(items.map(d => d.repo))].sort((a, b) => { const x = counts(a), y = counts(b); return y.you - x.you || y.doing - x.doing || y.n - x.n || a.localeCompare(b); });
  const quiet = boardRepos.filter(r => !active.includes(r)).sort();
  if (st.repo !== 'All' && !boardRepos.includes(st.repo)) st.repo = 'All';
  const vis = items.filter(d => st.repo === 'All' || d.repo === st.repo);

  $('sub').textContent = boardRepos.length + ' repos · ' + (updating ? 'showing ' : 'read from GitHub at ') + loadedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + (updating ? ', updating…' : '');

  $('counts').innerHTML = ['you', 'doing', 'next', 'held'].map(s => '<button type="button" class="count' + (s === 'you' ? ' you' : '') + '" data-s="' + s + '" aria-pressed="' + (st.status === s) + '"><b>' + vis.filter(d => d.s === s).length + '</b><span>' + COUNT_LABEL[s] + '</span></button>').join('');
  const f = $('filt'); f.hidden = !st.status;
  if (st.status) f.innerHTML = 'Only ' + esc(COUNT_LABEL[st.status]) + ' <button type="button" id="clearf" aria-label="Show every status">×</button>';

  // Repo menu (phone) and repo table (desktop): busiest first, quiet ones apart.
  const q = st.q.toLowerCase(), match = r => !q || r.toLowerCase().includes(q);
  const sub = r => { const c = counts(r); return [c.you ? '<span class="y">' + c.you + ' you</span>' : '', c.doing ? c.doing + ' doing' : '', c.next ? c.next + ' next' : '', c.held ? c.held + ' held' : ''].filter(Boolean).join(' · '); };
  $('repoitems').innerHTML =
    '<button type="button" role="menuitemradio" data-r="All" aria-checked="' + (st.repo === 'All') + '"><b>All repos</b><small>' + items.length + ' open across ' + active.length + ' repos</small></button>' +
    active.filter(match).map(r => '<button type="button" role="menuitemradio" data-r="' + esc(r) + '" aria-checked="' + (st.repo === r) + '"><b>' + esc(r) + '</b><small>' + sub(r) + '</small></button>').join('') +
    (quiet.filter(match).length ? '<div class="sep">Nothing open</div>' + quiet.filter(match).map(r => '<button type="button" class="quiet" role="menuitemradio" data-r="' + esc(r) + '" aria-checked="' + (st.repo === r) + '"><b>' + esc(r) + '</b></button>').join('') : '');
  $('repolbl').textContent = st.repo === 'All' ? 'All repos' : st.repo;
  const td = (v, y) => '<td class="' + (v ? (y ? 'you' : '') : 'z') + '">' + v + '</td>';
  const tr = r => { const c = counts(r); return '<tr data-r="' + esc(r) + '" class="' + (st.repo === r ? 'sel' : '') + '" tabindex="0"><td>' + esc(r) + '</td>' + td(c.you, 1) + td(c.doing) + td(c.next) + td(c.held) + '</tr>'; };
  const sq = ($('sidesearch').value || '').toLowerCase();
  $('repotab').innerHTML = tr('All') + active.filter(r => !sq || r.toLowerCase().includes(sq)).map(tr).join('');
  $('sidefoot').textContent = 'Busiest first.' + (quiet.length ? ' ' + quiet.length + ' repo' + (quiet.length > 1 ? 's' : '') + ' with nothing open ' + (quiet.length > 1 ? 'are' : 'is') + ' hidden.' : '') + ' life, people and ideas are never shown.';

  let h = '';
  if (st.group === 'status') {
    const g = s => vis.filter(d => d.s === s).sort(s === 'you' ? youSort : sorter);
    if (st.status) h += block(COUNT_LABEL[st.status], g(st.status), st.status === 'you' ? 'you' : '');
    else h += block(SEC.you, g('you'), 'you') + '<div class="grid3">' + block(SEC.doing, g('doing')) + block(SEC.next, g('next'), '', false, 5) + block(SEC.held, g('held')) + '</div>';
  } else {
    const v2 = vis.filter(d => !st.status || d.s === st.status);
    const repos = active.filter(r => v2.some(d => d.repo === r));
    h += repos.length ? '<div class="reposec">' + repos.map(r => block(r, v2.filter(d => d.repo === r).sort(sorter), '', true)).join('') + '</div>' : '<div class="state">Nothing open here.</div>';
  }
  $('lists').innerHTML = h;

  const idle = vis.filter(d => d.u >= IDLE_DAYS).length;
  const stalled = vis.filter(d => d.stalled).length;
  const noPrio = vis.filter(d => d.kind === 'Issue' && d.p === 4).length;
  const p1 = active.filter(r => items.filter(d => d.repo === r && d.kind === 'Issue' && d.p === 1).length > 2);
  const tidy = [
    idle ? '<button type="button" class="lnk" data-sort="idle">' + idle + ' idle ' + IDLE_DAYS + '+ days</button>' : '',
    stalled ? '<button type="button" class="lnk" data-s="doing">' + stalled + ' claimed but stalled</button>' : '',
    noPrio ? '<span>' + noPrio + ' with no priority</span>' : '',
    ...p1.map(r => '<button type="button" class="lnk" data-r="' + esc(r) + '">' + esc(r) + ' has ' + items.filter(d => d.repo === r && d.p === 1).length + ' p1s (limit 2)</button>'),
  ].filter(Boolean);
  $('tidy').innerHTML = tidy.length ? '<span>Tidy-up:</span>' + tidy.join('') : '';

  const dv = done.filter(x => st.repo === 'All' || x.repository.name === st.repo);
  $('donebox').hidden = !dv.length;
  $('donesum').textContent = 'Done this week · ' + dv.length;
  $('done').innerHTML = dv.map(x => '<a class="row" href="' + esc(x.url) + '" target="_blank" rel="noopener"><span class="t">' + (x.__typename === 'PullRequest' ? '<span class="k">PR</span>' : '') + esc(x.title) + '</span><span class="pill ok">' + (x.merged ? 'Merged' : 'Done') + '</span><div class="meta"><span>' + esc(x.repository.name + '#' + x.number) + '</span><span>' + esc(ago(days(x.closedAt))) + (days(x.closedAt) ? ' ago' : '') + '</span></div></a>').join('');

  document.querySelectorAll('#group button').forEach(b => b.setAttribute('aria-pressed', b.dataset.v === st.group));
  document.querySelectorAll('#sortmenu button').forEach(b => b.setAttribute('aria-checked', b.dataset.v === st.sort));
  $('sortlbl').textContent = SORTS[st.sort] || SORTS.fleet;
  save();
}

// ── Interaction ─────────────────────────────────────────────────────────────

function closeMenus() {
  for (const [m, b] of [['sortmenu', 'sortbtn'], ['repomenu', 'repobtn']]) { $(m).hidden = true; $(b).setAttribute('aria-expanded', 'false'); }
}
function toggleMenu(m, b, focus) {
  const opening = $(m).hidden;
  closeMenus();
  if (opening) { $(m).hidden = false; $(b).setAttribute('aria-expanded', 'true'); const el = focus(); if (el) el.focus(); }
}

document.addEventListener('click', e => {
  const t = e.target;
  const start = t.closest('.start');
  if (start) {
    const ref = start.dataset.ref, cmd = 'build ' + ref, repo = ref.split('#')[0];
    try { navigator.clipboard.writeText(cmd).catch(() => {}); } catch (_) {}
    $('tcmd').textContent = cmd; $('tproj').textContent = repo;
    $('topen').href = PROJECTS[repo] || 'https://claude.ai/code';
    const toast = $('toast'); toast.hidden = false; clearTimeout(window.__toast); window.__toast = setTimeout(() => { toast.hidden = true; }, 5000);
    return;
  }
  if (t.closest('#sortbtn')) return toggleMenu('sortmenu', 'sortbtn', () => $('sortmenu').querySelector('[aria-checked=true]'));
  if (t.closest('#repobtn')) return toggleMenu('repomenu', 'repobtn', () => $('reposearch'));
  const si = t.closest('#sortmenu [data-v]');
  if (si) { st.sort = si.dataset.v; closeMenus(); return render(); }
  const ri = t.closest('#repomenu [data-r]');
  if (ri) { st.repo = ri.dataset.r; st.showAll = false; closeMenus(); return render(); }
  if (!t.closest('.menu')) closeMenus();
  if (t.closest('#clearf')) { st.status = null; return render(); }
  if (t.closest('#showall')) { st.showAll = !st.showAll; return render(); }
  const cs = t.closest('.count, .lnk[data-s]');
  if (cs) { st.status = cs.classList.contains('lnk') ? cs.dataset.s : (st.status === cs.dataset.s ? null : cs.dataset.s); return render(); }
  const ls = t.closest('.lnk[data-sort]');
  if (ls) { st.sort = ls.dataset.sort; st.status = null; return render(); }
  const rr = t.closest('#repotab [data-r], .lnk[data-r]');
  if (rr) { st.repo = rr.dataset.r; st.showAll = false; return render(); }
  const g = t.closest('#group button');
  if (g) { st.group = g.dataset.v; return render(); }
  if (t.closest('.det')) return;
  const r = t.closest('#lists .row');
  if (r) { st.open = st.open === r.dataset.id ? null : r.dataset.id; render(); }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeMenus();
  const tr = e.target.closest && e.target.closest('#repotab tr');
  if (tr && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); st.repo = tr.dataset.r; render(); }
});
$('reposearch').addEventListener('input', e => { st.q = e.target.value; render(); });
$('sidesearch').addEventListener('input', () => render());
$('refresh').addEventListener('click', () => load());

fetch('/config.json').then(r => r.ok ? r.json() : {}).then(c => { PROJECTS = (c && c.projects) || {}; }).catch(() => {});
load();
