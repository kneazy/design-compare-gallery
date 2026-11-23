#!/usr/bin/env node
// Smoke-test a served gallery in headless Chromium: every tab, view, mode and set; images load;
// canvases draw; no console errors. Also checks questions.html when given.
// Usage: node check_gallery.mjs http://127.0.0.1:8766/index.html [http://127.0.0.1:8766/questions.html] [--shot out.png]
// Exit 0 = clean, 1 = problems (listed). Run it through the lock (dcg.py check does).
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';

const req = createRequire(path.join(os.homedir(), '.claude/skills/pixel-perfect/scripts/package.json'));
const { chromium } = req('playwright');
const args = process.argv.slice(2);
const shotI = args.indexOf('--shot');
const shot = shotI >= 0 ? args.splice(shotI, 2)[1] : null;
const [galleryUrl, questionsUrl] = args;
if (!galleryUrl) { console.error('usage: node check_gallery.mjs <gallery url> [questions url] [--shot file]'); process.exit(2); }

const problems = [];
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
const page = await ctx.newPage();
page.on('console', (m) => { if (m.type() === 'error') problems.push(`console: ${m.text().slice(0, 200)}`); });
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message.slice(0, 200)}`));
page.on('response', (r) => { if (r.status() >= 400) problems.push(`HTTP ${r.status()} ${r.url()}`); });
await page.goto(galleryUrl, { waitUntil: 'load' });
const version = await page.evaluate(() => DATA.version);
await page.evaluate(() => localStorage.clear());
await page.reload({ waitUntil: 'load' });

async function scrollAll() {
  await page.evaluate(async () => {
    for (let y = 0; y < document.documentElement.scrollHeight; y += 700) { window.scrollTo(0, y); await new Promise((r) => setTimeout(r, 40)); }
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(250);
}
async function canvasStats() {
  return page.evaluate(async () => {
    const cvs = [...document.querySelectorAll('canvas[data-src]')];
    // force-draw every canvas (the IntersectionObserver only draws near the viewport)
    for (const cv of cvs) { if (!cv.dataset.drawn) cv.scrollIntoView(); }
    await new Promise((r) => setTimeout(r, 300));
    const imgs = [...document.querySelectorAll('main img')];
    await Promise.all(imgs.map((i) => (i.complete ? 0 : new Promise((r) => { i.onload = i.onerror = r; }))));
    return {
      canvases: cvs.length,
      drawn: cvs.filter((c) => c.dataset.drawn === '1' && c.width > 0).length,
      imgs: imgs.length,
      brokenImgs: imgs.filter((i) => !(i.naturalWidth > 0)).map((i) => i.getAttribute('src')),
      loadErrors: window.__dcgImgErrors || [],
    };
  });
}

const tabs = await page.$$eval('#tabs button', (b) => b.map((x) => x.dataset.tab));
let checked = 0;
for (const tab of tabs) {
  await page.click(`#tabs button[data-tab="${tab}"]`);
  await page.waitForTimeout(150);
  if (tab === 'score') {
    const refs = await page.$$eval('#refs button', (b) => b.map((x) => x.dataset.ref));
    for (const r of refs.length ? refs : [null]) {
      if (r) { await page.click(`#refs button[data-ref="${r}"]`); await page.waitForTimeout(150); }
      const cells = await page.$$('td.c[data-k]');
      if (!cells.length) problems.push(`score tab ${r || ''}: no cells`);
      const withImg = await page.$('td.c.PASS, td.c.WARN, td.c.FAIL');
      if (withImg) {
        await withImg.click();
        await page.waitForTimeout(400);
        const s = await canvasStats();
        if (s.brokenImgs.length) problems.push(`score trio broken: ${s.brokenImgs.join(', ')}`);
        checked += s.imgs;
      }
    }
    continue;
  }
  if (tab === 'regress') {
    const rows = await page.$$('table.score tr');
    if (rows.length < 2) problems.push('regress tab: no rows');
    await scrollAll();
    const s = await canvasStats();
    if (s.brokenImgs.length) problems.push(`regress: broken <img> ${s.brokenImgs.slice(0, 3).join(', ')}`);
    checked += rows.length + s.imgs;
    continue;
  }
  const views = tab === 'find' ? [null] : await page.$$eval('#views button:not([disabled])', (b) => b.map((x) => x.dataset.view));
  const sets = await page.$$eval('#vers button', (b) => b.map((x) => x.dataset.ver));
  for (const v of views) {
    if (v) { await page.click(`#views button[data-view="${v}"]`); await page.waitForTimeout(100); }
    for (const set of sets.length ? sets : [null]) {
      if (set) { await page.click(`#vers button[data-ver="${set}"]`); await page.waitForTimeout(100); }
      const modes = await page.$$eval('#modes button', (b) => b.map((x) => x.dataset.mode));
      for (const mode of modes.length ? modes : ['plain']) {
        if (mode !== 'plain') { await page.click(`#modes button[data-mode="${mode}"]`); await page.waitForTimeout(80); }
        // the full canvas/image sweep once per view (swipe + side); other modes reuse the same images
        if (mode === 'swipe' || mode === 'side' || mode === 'plain') {
          await scrollAll();
          const s = await canvasStats();
          const where = `${tab}/${v || '-'}/${set || '-'}/${mode}`;
          if (mode === 'swipe' && s.canvases && s.drawn < s.canvases) problems.push(`${where}: ${s.canvases - s.drawn} of ${s.canvases} canvases not drawn`);
          if (s.brokenImgs.length) problems.push(`${where}: broken <img> ${s.brokenImgs.slice(0, 3).join(', ')}`);
          if (s.loadErrors.length) problems.push(`${where}: images failed ${[...new Set(s.loadErrors)].slice(0, 3).join(', ')}`);
          if (!s.canvases && !s.imgs) problems.push(`${where}: nothing rendered`);
          checked += s.canvases + s.imgs;
        }
      }
    }
  }
}
// fade/diff drag-to-align: drag the first canvas and expect the offset label state
const nocomp = await page.evaluate(() => Object.entries(DATA.pages).filter(([, p]) => p.nocomp).map(([k]) => k));
const firstPage = tabs.find((t) => t !== 'find' && t !== 'score' && t !== 'regress' && !nocomp.includes(t));
if (firstPage) {
  await page.click(`#tabs button[data-tab="${firstPage}"]`);
  await page.click('#modes button[data-mode="diff"]');
  await page.waitForTimeout(200);
  const cv = await page.$('canvas[data-src]');
  if (cv) {
    // centre it: at the top of the page the sticky header/bar would take the mousedown
    await cv.evaluate((c) => c.scrollIntoView({ block: 'center' }));
    await page.waitForTimeout(300);
    const b = await cv.boundingBox();
    const vh = page.viewportSize().height;
    const y = Math.min(Math.max(b.y + 20, vh / 2), b.y + b.height - 20);
    await page.mouse.move(b.x + b.width / 2, y);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width / 2 + 20, y + 10, { steps: 4 });
    await page.mouse.up();
    const dx = await cv.evaluate((c) => c._dx || 0);
    if (!dx) problems.push('diff mode: drag did not move the overlay');
  }
  // state persistence: reload keeps tab + mode
  await page.reload({ waitUntil: 'load' });
  const mode = await page.$eval('#modes button.on', (b) => b.dataset.mode).catch(() => null);
  if (mode !== 'diff') problems.push(`state not persisted across reload (mode ${mode})`);
  if (shot) {
    await page.click('#modes button[data-mode="swipe"]');
    await page.waitForTimeout(400);
    await page.screenshot({ path: shot });
  }
}

if (questionsUrl) {
  const q = await ctx.newPage();
  q.on('console', (m) => { if (m.type() === 'error') problems.push(`questions console: ${m.text().slice(0, 200)}`); });
  q.on('pageerror', (e) => problems.push(`questions pageerror: ${e.message.slice(0, 200)}`));
  await q.goto(questionsUrl, { waitUntil: 'load' });
  const chip = await q.$('.chip');
  if (chip) {
    await chip.click();
    const done = await q.$eval('#done', (e) => e.textContent);
    if (done !== '1') problems.push(`questions: answering a chip did not count (done=${done})`);
  }
  const ta = await q.$('textarea.free');
  if (ta) await ta.fill('test comment');
  await q.click('#copy');
  await q.waitForTimeout(200);
  const text = await q.$eval('#pvt', (e) => e.value);
  if (chip && !/№1/.test(text)) problems.push('questions: copied text lacks the answer');
  await q.evaluate(async () => { for (const i of document.images) { i.loading = 'eager'; } await new Promise((r) => setTimeout(r, 500)); });
  const broken = await q.$$eval('img[src]', (is) => is.filter((i) => i.complete && !(i.naturalWidth > 0)).map((i) => i.getAttribute('src')));
  if (broken.length) problems.push(`questions: broken images ${broken.slice(0, 3).join(', ')}`);
}
await browser.close();
console.log(JSON.stringify({ version, tabs, elementsChecked: checked, problems: [...new Set(problems)] }, null, 1));
process.exit(problems.length ? 1 : 0);
