// Renders the Work page against a stubbed GitHub API with demo data and writes
// phone (390) and desktop (1280) screenshots in light and dark, plus a sanity
// check that each status lands in the right section. No token, no network.
//
//   node docker/work/tests/screenshots.mjs [outdir]
//
// Needs Playwright (preinstalled in cloud sessions; `npx playwright` locally).
import { createRequire } from 'node:module';
import { readFileSync, mkdirSync, existsSync } from 'node:fs';
import { join, dirname, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const here = dirname(fileURLToPath(import.meta.url));
const SITE = join(here, '..', 'site');
const ASSETS = join(here, '..', '..', 'dashboard', 'assets');
const OUT = process.argv[2] || join(here, 'out');
mkdirSync(OUT, { recursive: true });

const now = Date.now();
const d = n => new Date(now - n * 86400000).toISOString();
const h = n => new Date(now - n * 3600000).toISOString();
const L = (...n) => ({ nodes: n.map(name => ({ name })) });
const C = (login, text, at) => ({ nodes: [{ author: { login }, bodyText: text, createdAt: at }] });
const spec = (p, ac) => `## Problem\n\n${p}\n\n## Acceptance criteria\n\n${ac.map(a => '- ' + a).join('\n')}\n\n## Decisions\n\n- Demo.`;
const issue = (repo, number, title, labels, o = {}) => ({
  __typename: 'Issue', number, title, url: `https://github.com/demo/${repo}/issues/${number}`,
  body: o.body ?? spec(o.p || 'Demo problem.', o.ac || ['Demo criterion']),
  createdAt: d(o.c ?? 3), updatedAt: o.u || d(o.c ?? 3), author: { login: 'demo-owner' }, authorAssociation: 'OWNER',
  repository: { name: repo }, labels: L(...labels), comments: o.last || { nodes: [] },
  closedByPullRequestsReferences: { nodes: o.pr ? [{ number: o.pr.number, url: o.pr.url }] : [] },
});
const pr = (repo, number, title, labels, o = {}) => ({
  __typename: 'PullRequest', number, title, url: `https://github.com/demo/${repo}/pull/${number}`, body: 'Demo PR.',
  createdAt: d(o.c ?? 1), updatedAt: o.u || d(o.c ?? 1), isDraft: false, reviewDecision: o.review || null,
  author: { login: o.author || 'demo-owner' }, authorAssociation: o.author ? 'NONE' : 'OWNER',
  repository: { name: repo }, labels: L(...labels), comments: o.last || { nodes: [] },
  commits: { nodes: [{ commit: { statusCheckRollup: { state: o.checks || 'SUCCESS' } } }] },
  closingIssuesReferences: { nodes: (o.closes || []).map(n => ({ number: n, repository: { name: repo } })) },
});

const pr384 = pr('homelab', 384, 'Kick the board sync every 3 hours', [], { checks: 'PENDING', closes: [383] });
const NODES = [
  pr('ai-tools', 112, 'Run the board sync by hand only', ['needs:anthony'], { review: 'APPROVED', c: 3, last: C('reviewer', 'Reviewer: APPROVE at the head commit. Ready to merge.', h(2)) }),
  issue('homelab', 386, 'Choose where the photo review page lives', ['needs:anthony', 'priority:p2', 'size:s', 'type:feature'], { c: 4, last: C('claude', 'Pick one of the two hosting options in this comment. I recommend the subdomain.', d(1)) }),
  issue('driftwood', 58, 'Spec the AWS provider', ['needs:anthony', 'priority:p2', 'size:m', 'type:feature'], { c: 8, last: C('claude', 'Read proposal PR #59 and accept or ask for changes.', d(3)) }),
  issue('homelab', 383, 'Kick the board sync every 3 hours', ['agent:working', 'priority:p1', 'size:s', 'type:chore'], { c: 1, u: h(1) }),
  pr384,
  issue('design', 41, 'Chart colour tokens', ['agent:working', 'priority:p2', 'size:m', 'type:feature'], { c: 10, u: h(1) }),
  issue('ai-tools', 87, '/triage fleet command', ['agent:working', 'priority:p2', 'size:s', 'type:feature'], { c: 15, u: d(3) }),
  pr('sites', 23, 'Show checks on preview links', [], { checks: 'FAILURE', c: 0 }),
  issue('homelab', 372, 'Write the restore drill checklist', ['priority:p1', 'size:s', 'type:chore'], { c: 12, p: 'The restore drill has no written steps, so it depends on memory.', ac: ['Checklist in `docs/`', 'Covers B2 and vzdump restores', 'Says how long each step took'], last: C('demo-owner', 'Do this before the next drill.', d(12)) }),
  issue('driftwood', 61, 'Validate CI output format', ['priority:p1', 'size:m', 'type:feature'], { c: 5 }),
  issue('ai-tools', 104, 'Lint skill descriptions', ['priority:p2', 'size:s', 'type:chore'], { c: 9 }),
  issue('design', 44, 'Focus ring on dark cards', ['priority:p2', 'size:s', 'type:bug'], { c: 4 }),
  issue('breakeven', 19, 'Sort payees by last used', ['priority:p2', 'size:s', 'type:feature'], { c: 2 }),
  issue('homelab', 253, 'Free disk space when CT 100 crosses a threshold', ['priority:p3', 'size:m', 'type:feature'], { c: 52, u: d(41) }),
  issue('guidebook', 66, 'Keyboard shortcuts for tours', ['priority:p3', 'size:s', 'type:feature'], { c: 38 }),
  issue('homelab', 352, 'Calendar through the gateway', ['blocked', 'priority:p2', 'size:s', 'type:feature'], { c: 13, u: d(2), body: 'Blocked by brignano/homelab#362\n\n' + spec('Duet cannot read the calendar yet.', ['Read-only calendar through the gateway']) }),
  issue('guidebook', 70, 'Record a demo tour', ['agent:pause', 'priority:p3', 'size:s', 'type:chore'], { c: 30, u: d(9) }),
  issue('card', 8, 'Card back design', ['priority:p3'], { c: 1, body: 'Just an idea, no spec yet.' }),
  pr('homelab', 390, 'Bump grafana to 12.4', [], { author: 'renovate[bot]', c: 1 }),
  issue('life', 1, 'Must never appear', ['priority:p1']),
  issue('not-in-fleet', 2, 'Must never appear either', ['priority:p1']),
];
const DONE = [
  { __typename: 'PullRequest', number: 381, title: 'Build the secure gateway', url: 'https://github.com/demo/homelab/pull/381', closedAt: d(2), merged: true, repository: { name: 'homelab' } },
  { __typename: 'Issue', number: 39, title: 'Status pill contrast', url: 'https://github.com/demo/design/issues/39', closedAt: d(4), repository: { name: 'design' } },
];
const BOARD = `owner: brignano\nproject: 1\nrepos:\n  - driftwood\n  - guidebook\n  - sites\n  - brignano.io\n  - homelab\n  - design\n  - ai-tools\n  - breakeven\n  - card\nnever:\n  - life\n  - people\n  - ideas\n`;

function answer(body) {
  const { query, variables } = JSON.parse(body);
  if (query.includes('board.yml')) return { data: { repository: { object: { text: BOARD } } } };
  // Like GitHub since 2025: no is:issue / is:pr, no results.
  const q = (variables && variables.q) || '';
  const kind = /\bis:issue\b/.test(q) ? 'Issue' : /\bis:pr\b/.test(q) ? 'PullRequest' : null;
  const empty = { pageInfo: { hasNextPage: false, endCursor: null }, nodes: [] };
  if (!kind) return { data: { search: { issueCount: 0, ...empty } } };
  if (query.includes('closedAt')) return { data: { search: { ...empty, nodes: DONE.filter(n => n.__typename === kind) } } };
  const mine = NODES.filter(n => n.__typename === kind);
  // Two pages for issues, so paging is exercised.
  if (kind === 'Issue' && !variables.after) return { data: { search: { issueCount: mine.length, pageInfo: { hasNextPage: true, endCursor: 'p2' }, nodes: mine.slice(0, 8) } } };
  if (kind === 'Issue') return { data: { search: { issueCount: mine.length, ...empty, nodes: mine.slice(8) } } };
  return { data: { search: { issueCount: mine.length, ...empty, nodes: mine } } };
}

const TYPES = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' };
const browser = await chromium.launch();
let failed = 0;
for (const [name, width, scheme] of [['phone', 390, 'light'], ['phone', 390, 'dark'], ['desktop', 1280, 'light'], ['desktop', 1280, 'dark']]) {
  const page = await browser.newPage({ viewport: { width, height: 900 }, deviceScaleFactor: 2, colorScheme: scheme });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  let slow = 0, fail = false;
  await page.route('http://work.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/graphql' && fail) return route.fulfill({ status: 502, body: 'down' });
    if (url.pathname === '/api/graphql' && slow) await new Promise(r => setTimeout(r, slow));
    if (url.pathname === '/api/graphql') return route.fulfill({ contentType: 'application/json', body: JSON.stringify(answer(route.request().postData())) });
    const file = url.pathname.startsWith('/assets/') ? join(ASSETS, url.pathname.slice(8)) : join(SITE, url.pathname === '/' ? 'index.html' : url.pathname.slice(1));
    if (!existsSync(file)) return route.fulfill({ status: 404, body: 'not found' });
    return route.fulfill({ contentType: TYPES[extname(file)] || 'application/octet-stream', body: readFileSync(file) });
  });
  await page.goto('http://work.test/');
  await page.waitForSelector('.count');
  await page.waitForTimeout(400);

  const got = await page.evaluate(() => ({
    counts: [...document.querySelectorAll('.count b')].map(b => +b.textContent),
    text: document.body.innerText,
    scroll: document.documentElement.scrollWidth,
  }));
  const expect = (ok, msg) => { if (!ok) { failed++; console.error(`FAIL ${name}/${scheme}: ${msg}`); } };
  expect(JSON.stringify(got.counts) === '[3,4,7,4]', 'counts were ' + JSON.stringify(got.counts) + ', expected [3,4,7,4]');
  expect(!got.text.includes('Must never appear'), 'a repo outside board.yml was shown');
  expect(got.scroll <= width, 'page scrolls sideways: ' + got.scroll);
  expect(!errors.length, 'page errors: ' + errors.join('; '));

  // Second visit: the last load is drawn from this browser's copy before
  // GitHub answers, then replaced.
  slow = 3000;
  await page.reload();
  await page.waitForSelector('.count', { timeout: 1000 }).catch(() => {});
  const early = await page.evaluate(() => ({ n: document.querySelectorAll('.count b').length, sub: document.getElementById('sub').textContent }));
  expect(early.n === 4 && /updating/.test(early.sub), 'cached copy not drawn at once: ' + JSON.stringify(early));
  await page.waitForFunction(() => !/updating/.test(document.getElementById('sub').textContent), null, { timeout: 10000 });
  slow = 0;

  // An old copy plus a failed refresh must say its date, not just a time.
  await page.evaluate(() => { const c = JSON.parse(localStorage.getItem('work.cache.v1')); c.at -= 3 * 86400000; localStorage.setItem('work.cache.v1', JSON.stringify(c)); });
  fail = true;
  await page.reload();
  await page.waitForFunction(() => /could not refresh/.test(document.getElementById('sub').textContent), null, { timeout: 5000 }).catch(() => {});
  const stale = await page.evaluate(() => document.getElementById('sub').textContent);
  await page.screenshot({ path: join(OUT, `${name}-${scheme}-stale.png`) });
  expect(/could not refresh/.test(stale) && /[A-Z][a-z]{2} \d/.test(stale), 'old cached copy did not show its date: ' + stale);
  fail = false;
  await page.reload();
  await page.waitForFunction(() => /read from GitHub at/.test(document.getElementById('sub').textContent), null, { timeout: 10000 });

  await page.click('.row[data-id="homelab#372"] .t');
  await page.screenshot({ path: join(OUT, `${name}-${scheme}.png`), fullPage: true });
  await page.click('#group button[data-v=repo]');
  await page.screenshot({ path: join(OUT, `${name}-${scheme}-by-repo.png`), fullPage: true });
  await page.evaluate(() => { try { localStorage.clear(); } catch (_) {} });
  await page.close();
}
await browser.close();
console.log(failed ? `${failed} check(s) failed` : 'all checks passed', '— screenshots in', OUT);
process.exit(failed ? 1 : 0);
