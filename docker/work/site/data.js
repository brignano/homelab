// Work data — the GitHub reads and the fleet status rules, shared by the
// Work page (app.js) and the home page (docker/dashboard/site), so both count
// the same items the same way (homelab#395). Classic script: its top-level
// names are globals the page's own script uses. Needs no DOM.
'use strict';

const OWNER = 'brignano';
const API = '/api/graphql';
const IDLE_DAYS = 30;      // matches Fleet backlog health
const STALL_HOURS = 24;    // agent:working with nothing since then

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

// Everything open in the fleet repos, plus what closed in the last week.
// Resolves to the object the Work page caches: { at, repos, nodes, done }.
async function fetchWork() {
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
  return c;
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
