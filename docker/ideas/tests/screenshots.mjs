// Renders the Ideas page against a stubbed /api/ledger.json with demo ideas and
// writes phone (390) and desktop (1280) screenshots in light and dark, plus
// checks that counts, sections, the kind menu, filters and the error states
// work. No token, no network.
//
//   node docker/ideas/tests/screenshots.mjs [outdir]
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
const WORK = join(here, '..', '..', 'work', 'site');
const ASSETS = join(here, '..', '..', 'dashboard', 'assets');
const OUT = process.argv[2] || join(here, 'out');
mkdirSync(OUT, { recursive: true });
const TYPES = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' };

// Demo ideas in ledger.json's shape (docker/ideas/README.md).
const I = (title, stage, cls, ev, tone, verdict, o = {}) => ({ title, stage, cls, ev, tone, verdict, summary: o.summary || `What ${title.toLowerCase()} would do, in a sentence.`, ...o });
const LEDGER = [
  I('Demo kit for local shops', 'building', '👥 People', 'E2', 'good', 'Exempt: under 10 hours, so building is the cheapest evidence.', { repo: 'sites' }),
  I('Diagrams that check themselves', 'proposals', '📚 Craft', 'E2', 'side', 'Over the craft cap. Commit deliberately or park.', { repo: 'driftwood' }),
  I('Shared calendar for the house', 'accepted', '🛠 Quality of life', 'E3', 'good', 'Cleared for building.'),
  I('Gift list for the family', 'proposals', '👥 People', 'E2', 'good', 'Exempt: under 10 hours.', { url: 'https://github.com/demo/ledger/blob/main/proposals/gift-list.md' }),
  I('Paid writing helper', 'proposals', '💰 Income', 'E1', 'park', 'Income ideas wait for a commitment.'),
  I('Price drop watcher', 'inbox', '🛠 Quality of life', 'E1', 'bet', 'Watcher: passes the workaround test.'),
  I('Concert ticket watcher', 'inbox', '🛠 Quality of life', 'E1', 'bet', 'Same shape as the price drop watcher.'),
  I('Game helper: idle alerts', 'inbox', '📚 Craft', 'E1', 'side', 'Side quest.'),
  I('Game helper: reminders', 'inbox', '📚 Craft', 'E1', 'side', 'Side quest.'),
  I('Food scanner', 'inbox', '🛠 Quality of life', 'E0', 'side', 'One question decides it.'),
  I('Safer roads at night', 'inbox', '❤️ Public good', 'E0', 'none', 'Not yet scored'),
  I('Old receipts finder', 'inbox', '🛠 Quality of life', 'E0', 'none', 'Not yet scored'),
  I('Trip planner agent', 'archive', '🛠 Quality of life', 'E0', 'park', 'Conversation, not software.'),
  I('Undefined thing', 'archive', 'Unclassified', '', 'park', 'Needs a sentence before it can be scored.'),
];

const browser = await chromium.launch();
let failed = 0;
for (const [name, width, scheme] of [['phone', 390, 'light'], ['phone', 390, 'dark'], ['desktop', 1280, 'light'], ['desktop', 1280, 'dark']]) {
  const page = await browser.newPage({ viewport: { width, height: 900 }, deviceScaleFactor: 2, colorScheme: scheme });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  let status = 200;
  // Ideas is served from Work's origin at /ideas/ (homelab#414), as the
  // Caddyfile's work. block does: /ideas/* from this site, the rest from Work.
  await page.route('http://work.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/ledger.json') return status === 200 ? route.fulfill({ contentType: 'application/json', body: JSON.stringify(LEDGER) }) : route.fulfill({ status, body: '{}' });
    const file = url.pathname.startsWith('/assets/') ? join(ASSETS, url.pathname.slice(8))
      : url.pathname.startsWith('/ideas/') ? join(SITE, url.pathname === '/ideas/' ? 'index.html' : url.pathname.slice(7))
      : join(WORK, url.pathname.slice(1));
    if (!existsSync(file)) return route.fulfill({ status: 404, body: 'not found' });
    return route.fulfill({ contentType: TYPES[extname(file)] || 'application/octet-stream', body: readFileSync(file) });
  });
  const expect = (ok, msg) => { if (!ok) { failed++; console.error(`FAIL ${name}/${scheme}: ${msg}`); } };

  // No token / no ledger yet: the page says what to set, not a blank screen.
  for (const [code, words] of [[401, 'READ_GITHUB_TOKEN'], [404, 'IDEAS_LEDGER_REPO']]) {
    status = code;
    await page.goto('http://work.test/ideas/');
    await page.waitForSelector('.state.err');
    const t = await page.textContent('.state.err');
    expect(t.includes(words), `${code} did not name ${words}: ${t}`);
  }
  status = 200;
  await page.goto('http://work.test/ideas/');
  await page.waitForSelector('.row');
  await page.waitForTimeout(300);

  const got = await page.evaluate(() => ({
    counts: [...document.querySelectorAll('.count b')].map(b => +b.textContent),
    sections: [...document.querySelectorAll('#lists section h2')].map(h => h.textContent.replace(/\s+/g, ' ').trim()),
    work: document.querySelector('nav.switch a:not([aria-current])').href,
    repoLink: (document.querySelector('a.repo') || {}).href || '',
    select: document.querySelectorAll('select').length,
    scroll: document.documentElement.scrollWidth,
  }));
  // Go: calendar (accepted -> proposals), gift list, demo kit. Building: the two
  // with a repo. Proposals: calendar, gift list, writing helper. Untested: the
  // E0/E1 ideas still in play.
  expect(JSON.stringify(got.counts) === '[3,2,3,8]', 'counts were ' + JSON.stringify(got.counts) + ', expected [3,2,3,8]');
  expect(JSON.stringify(got.sections) === '["Building 2","Proposals 3","Inbox 7"]', 'sections were ' + JSON.stringify(got.sections));
  expect(got.work === 'http://work.test/', 'Work link was ' + got.work);
  expect(got.repoLink === 'http://work.test/#repo=sites', 'repo link was ' + got.repoLink);
  expect(got.select === 0, 'a native <select> is on the page');
  expect(got.scroll <= width, 'page scrolls sideways: ' + got.scroll);

  // Inbox shows five, then "Show all".
  expect(await page.locator('#inboxall').count() === 1, 'inbox has no Show all');

  // Kind menu: a real menu, and picking a kind filters.
  await page.click('#kindbtn');
  expect(await page.isVisible('#kindmenu'), 'kind menu did not open');
  await page.screenshot({ path: join(OUT, `${name}-${scheme}-menu.png`) });
  await page.click('#kindmenu [data-k="People"]');
  const people = await page.locator('#lists .row').count();
  expect(people === 2, 'People filter showed ' + people + ' rows, expected 2');
  await page.click('#kindbtn');
  await page.click('#kindmenu [data-k="All"]');

  // Proof: "Someone wants it" keeps E2+ only.
  await page.click('#proof [data-v=wanted]');
  const wanted = await page.locator('#lists .row').count();
  expect(wanted === 4, '"Someone wants it" showed ' + wanted + ' rows, expected 4');
  await page.click('#proof [data-v=any]');

  // Second visit draws the cached copy at once.
  await page.reload();
  const early = await page.evaluate(() => document.querySelectorAll('.row').length);
  expect(early > 0, 'cached copy not drawn at once');
  await page.waitForFunction(() => !/updating/.test(document.getElementById('sub').textContent), null, { timeout: 5000 });

  await page.click('.row[data-id="3"] .t');
  expect(await page.locator('.det a[href*="gift-list"]').count() === 1, 'expanded row has no GitHub link');
  expect(!errors.length, 'page errors: ' + errors.join('; '));
  await page.screenshot({ path: join(OUT, `${name}-${scheme}.png`), fullPage: true });
  await page.evaluate(() => { try { localStorage.clear(); } catch (_) {} });
  await page.close();
}
await browser.close();
console.log(failed ? `${failed} check(s) failed` : 'all checks passed', '— screenshots in', OUT);
process.exit(failed ? 1 : 0);
