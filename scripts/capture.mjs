#!/usr/bin/env node
// Capture full-page screenshots + DOM section boxes + <head> metadata for page x width x theme.
//
// Usage: node capture.mjs <config.json> <set> [pageKey,pageKey] [view,view]
//   writes <out>/cap/<set>/<page>-<width>-<theme>.png and .json (dcg.py capture stages them first)
// Env overrides (used by dcg.py capture / regress):
//   DCG_OUT_DIR=<dir>      write here instead of <out>/cap/<set>
//   DCG_BASE_URL=<url>     capture another server (regress A vs B)
//   DCG_ALL_VIEWS=1        every width x theme, even without a Figma frame
//   DCG_EXTRA_HIDE=<json>  more selectors to hide (regress.mask)
//   DCG_SKIP_EXISTING=1    resume: skip views whose .png and .json already exist in the out dir
// Run it through the lock (dcg.py does) — other agents may be driving Playwright too.
//
// Why each stabiliser exists (all learned the hard way):
// - Math.random is seeded: decorative "random" layouts and shuffles render the same every run.
// - capture.fixedTime freezes Date (footer years, "3 days ago") without stopping timers.
// - Lazy images are forced eager and the page is scrolled through: below-the-fold images
//   otherwise capture as empty boxes. document.fonts.ready is awaited.
// - requestAnimationFrame is frozen and horizontal scrollers reset to 0 after load: rAF-driven
//   carousels/marquees otherwise move between runs and between widths.
// - Videos are paused and hidden (capture.videos: "hide" | "pause" | "keep"): a frame of a
//   playing video differs on every run and never matches the Figma poster.
// - Cookie banners: set the consent key via config localStorage (preferred) or hide selectors.
// - Section tops come from the DOM (getBoundingClientRect + scrollY): announcement bars and
//   sticky headers shift every y, so never assume Figma offsets.
// - Headless Playwright with an explicit viewport: a headed Chrome window resize may not shrink
//   the layout viewport below its minimum width, so 390/440 captures would silently be wider.
// - Every file is written to .tmp and renamed: a crash never leaves a half image that a resumed
//   run would trust. Navigation retries network errors (ENOTFOUND killed long runs).
import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const req = createRequire(path.join(os.homedir(), '.claude/skills/pixel-perfect/scripts/package.json'));
const { chromium } = req('playwright');

const [cfgPath, set = 'now', onlyPages, onlyViews] = process.argv.slice(2);
if (!cfgPath) {
  console.error('usage: node capture.mjs <config.json> <set> [pages] [views]');
  process.exit(2);
}
const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
const cfgDir = path.dirname(path.resolve(cfgPath));
const home = (p) => p.replace(/^~/, os.homedir());
const outRoot = home(cfg.outputDir || 'out');
const outBase = path.isAbsolute(outRoot) ? outRoot : path.join(cfgDir, outRoot);
const outDir = process.env.DCG_OUT_DIR || path.join(outBase, 'cap', set);
fs.mkdirSync(outDir, { recursive: true });

const BASE = (process.env.DCG_BASE_URL || cfg.baseUrl).replace(/\/$/, '');
const C = { viewportHeight: 900, waitMs: 800, hide: [], videos: 'hide', scrollStep: 600, timeoutMs: 60000, noise: null, fixedTime: null, ...(cfg.capture || {}) };
const SEC = { selector: 'main > section', header: null, label: 'id', labelStrip: null, ...(cfg.sections || {}) };
const HIDE = [...(C.hide || []), ...JSON.parse(process.env.DCG_EXTRA_HIDE || '[]')];
const NOISE = C.noise ? new RegExp(C.noise, 'i') : null;
const widths = (cfg.widths || [1440, 390]).map(Number);
const themes = Object.keys(cfg.themes || { light: {} });
const pages = cfg.pages.filter((p) => !onlyPages || onlyPages.split(',').includes(p.key));
const wantViews = onlyViews ? new Set(onlyViews.split(',')) : null;
const allViews = process.env.DCG_ALL_VIEWS === '1';
const skipExisting = process.env.DCG_SKIP_EXISTING === '1';

function writeAtomic(file, data) {
  fs.writeFileSync(file + '.tmp', data);
  fs.renameSync(file + '.tmp', file);
}

const browser = await chromium.launch();
let failures = 0;

async function gotoRetry(p, url) {
  for (let a = 0; ; a++) {
    try {
      return await p.goto(url, { waitUntil: 'load' });
    } catch (e) {
      const net = /ENOTFOUND|ECONNREFUSED|ECONNRESET|ERR_NAME_NOT_RESOLVED|ERR_CONNECTION|ERR_INTERNET|ERR_NETWORK|Timeout/i.test(e.message);
      if (!net || a >= 2) throw e;
      console.log(`  network error (${e.message.split('\n')[0].slice(0, 80)}), retry in ${10 * (a + 1)}s`);
      await new Promise((r) => setTimeout(r, 10000 * (a + 1)));
    }
  }
}

async function one(page, width, theme) {
  const th = cfg.themes?.[theme] || {};
  const ctxOpts = { viewport: { width, height: C.viewportHeight }, deviceScaleFactor: 1 };
  if (cfg.storageState) ctxOpts.storageState = home(cfg.storageState);
  if (th.storageState) ctxOpts.storageState = home(th.storageState);
  if (th.colorScheme) ctxOpts.colorScheme = th.colorScheme;
  const ctx = await browser.newContext(ctxOpts);
  if (cfg.cookies?.length || th.cookies?.length) await ctx.addCookies([...(cfg.cookies || []), ...(th.cookies || [])]);
  const ls = { ...(cfg.localStorage || {}), ...(th.localStorage || {}) };
  await ctx.addInitScript(([entries, htmlClass, removeClass]) => {
    try { for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, typeof v === 'string' ? v : JSON.stringify(v)); } catch {}
    let s = 0x2f6b1d3;
    Math.random = () => {
      s = (s + 0x6d2b79f5) | 0;
      let t = Math.imul(s ^ (s >>> 15), 1 | s);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    if (htmlClass || removeClass) {
      const apply = () => {
        const cl = document.documentElement.classList;
        (removeClass || '').split(/\s+/).filter(Boolean).forEach((c) => cl.remove(c));
        (htmlClass || '').split(/\s+/).filter(Boolean).forEach((c) => cl.add(c));
      };
      document.addEventListener('DOMContentLoaded', apply);
      window.__dcgApplyTheme = apply;
    }
  }, [ls, th.htmlClass || '', th.removeHtmlClass || '']);

  const p = await ctx.newPage();
  if (C.fixedTime) await p.clock.setFixedTime(new Date(C.fixedTime));
  p.setDefaultTimeout(C.timeoutMs);
  const consoleMsgs = [];
  const failed = [];
  const noisy = (s) => !!(NOISE && NOISE.test(s || ''));
  p.on('console', (m) => {
    if (m.type() === 'error') consoleMsgs.push({ text: m.text().slice(0, 300), url: m.location()?.url, noise: noisy(m.text()) || noisy(m.location()?.url) });
  });
  p.on('pageerror', (e) => consoleMsgs.push({ text: `pageerror: ${e.message.slice(0, 300)}`, noise: false }));
  p.on('response', (r) => { if (r.status() >= 400) failed.push({ s: `${r.status()} ${r.url().slice(0, 200)}`, noise: noisy(r.url()) }); });
  // net::ERR_ABORTED = the page cancelled the request itself (a paused video, a srcset candidate
  // superseded by another): timing-dependent, not a failure. Kept, but never compared.
  p.on('requestfailed', (r) => {
    const err = r.failure()?.errorText || '';
    const aborted = /ERR_ABORTED/.test(err);
    failed.push({ s: `FAILED ${err} ${r.url().slice(0, 200)}`, noise: aborted || noisy(r.url()), aborted });
  });

  const url = BASE + page.route;
  const resp = await gotoRetry(p, url);
  const status = resp ? resp.status() : null;
  if (!resp || status >= 400) {
    await ctx.close();
    return { status, url, error: `${url} answered ${status}` };
  }
  await p.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
  await p.evaluate(async (step) => {
    window.__dcgApplyTheme?.();
    document.querySelectorAll('img[loading="lazy"]').forEach((img) => { img.loading = 'eager'; });
    for (let y = 0; y < document.documentElement.scrollHeight; y += step) {
      window.scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 70));
    }
    window.scrollTo(0, 0);
  }, C.scrollStep);
  await p.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
  await p.evaluate(() => document.fonts?.ready).catch(() => {});
  await p.waitForTimeout(C.waitMs);
  await p.evaluate(async ({ videos, hide }) => {
    window.requestAnimationFrame = () => 0;
    await new Promise((r) => setTimeout(r, 150));
    document.querySelectorAll('*').forEach((el) => { if (el.scrollLeft > 0) el.scrollLeft = 0; });
    if (videos !== 'keep') {
      HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
      document.querySelectorAll('video').forEach((v) => {
        v.pause();
        try { v.currentTime = 0; } catch {}
        if (videos === 'hide') v.style.visibility = 'hidden';
      });
    }
    for (const sel of hide || []) document.querySelectorAll(sel).forEach((el) => { el.style.visibility = 'hidden'; });
    window.__dcgApplyTheme?.();
    window.scrollTo(0, 0);
    await Promise.all([...document.images].filter((i) => !i.complete).map((i) => new Promise((r) => { i.onload = i.onerror = r; setTimeout(r, 3000); })));
    await new Promise((r) => setTimeout(r, 80));
  }, { videos: C.videos, hide: HIDE });

  const metrics = await p.evaluate(({ sel, headerSel, labelMode, labelStrip, buildScript }) => {
    const sy = window.scrollY;
    const box = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y + sy), w: Math.round(r.width), h: Math.round(r.height) }; };
    const strip = labelStrip ? new RegExp(labelStrip) : null;
    const all = [...document.querySelectorAll(sel)];
    // keep outermost matches only (a matching section nested in another is part of it)
    const secs = all.filter((el) => !all.some((o) => o !== el && o.contains(el)));
    const seen = {};
    const sections = secs.map((el, i) => {
      const heading = el.querySelector('h1,h2,h3')?.innerText?.trim().replace(/\s+/g, ' ').slice(0, 60) || '';
      let label = (labelMode === 'id' && el.id) || (labelMode !== 'heading' && el.getAttribute(labelMode === 'id' ? 'aria-label' : labelMode)) || heading || `section-${i + 1}`;
      if (strip) label = label.replace(strip, '') || label;
      label = label.trim();
      seen[label] = (seen[label] || 0) + 1;
      if (seen[label] > 1) label = `${label}-${seen[label]}`;
      const n = all.indexOf(el) + 1;
      const selector = el.id ? `${el.tagName.toLowerCase()}[id="${el.id.replace(/"/g, '\\"')}"]` : `:nth-match(${sel}, ${n})`;
      return { index: i, label, id: el.id || null, heading, selector, ...box(el), hasText: !!el.querySelector('h1,h2,h3,h4,p') };
    }).filter((s) => s.h > 0);
    const header = headerSel ? document.querySelector(headerSel) : null;
    // <head> metadata for regress: what search engines and link previews read
    const attr = (q, a) => document.head.querySelector(q)?.getAttribute(a) ?? null;
    const metas = {};
    document.head.querySelectorAll('meta[property^="og:"], meta[name^="twitter:"], meta[property^="article:"]').forEach((m) => {
      const k = m.getAttribute('property') || m.getAttribute('name');
      metas[k] = metas[k] ? [].concat(metas[k], m.content) : m.content;
    });
    const sortKeys = (v) => Array.isArray(v) ? v.map(sortKeys) : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortKeys(v[k])])) : v;
    const jsonld = [...document.querySelectorAll('script[type="application/ld+json"]')].map((s) => {
      try { return JSON.stringify(sortKeys(JSON.parse(s.textContent))); } catch { return `INVALID JSON: ${s.textContent.trim().slice(0, 200)}`; }
    });
    const head = {
      title: document.title,
      description: attr('meta[name="description"]', 'content'),
      canonical: attr('link[rel="canonical"]', 'href'),
      robots: attr('meta[name="robots"]', 'content'),
      hreflang: [...document.head.querySelectorAll('link[rel="alternate"][hreflang]')].map((l) => `${l.hreflang} ${l.href}`).sort(),
      meta: metas,
      jsonld,
    };
    let nextBuild = null;
    try { nextBuild = JSON.parse(document.getElementById('__NEXT_DATA__')?.textContent || 'null')?.buildId || null; } catch {}
    let scriptBuild = null;
    // eslint-disable-next-line no-eval
    if (buildScript) { try { scriptBuild = String((0, eval)(buildScript)); } catch (e) { scriptBuild = `error: ${e.message}`; } }
    return {
      sections,
      header: header ? box(header) : null,
      docHeight: document.documentElement.scrollHeight,
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      head,
      build: { next: nextBuild, generator: attr('meta[name="generator"]', 'content'), script: scriptBuild },
      htmlClass: document.documentElement.className,
      brokenImages: [...document.images].filter((i) => i.getBoundingClientRect().width > 0 && !(i.complete && i.naturalWidth > 0)).map((i) => (i.currentSrc || i.src).slice(0, 160)).slice(0, 20),
    };
  }, { sel: SEC.selector, headerSel: SEC.header, labelMode: SEC.label, labelStrip: SEC.labelStrip, buildScript: cfg.buildId?.script || null });

  const base = `${page.key}-${width}-${theme}`;
  const png = path.join(outDir, `${base}.png`);
  await p.screenshot({ path: png + '.tmp.png', fullPage: true, animations: 'disabled', caret: 'hide', timeout: 180000 });
  fs.renameSync(png + '.tmp.png', png);
  await ctx.close();
  const rep = { page: page.key, route: page.route, url, status, width, theme, set, capturedAt: new Date().toISOString(), file: `${base}.png`,
    hidden: HIDE, videos: C.videos, fixedTime: C.fixedTime, console: consoleMsgs, failed, ...metrics };
  writeAtomic(path.join(outDir, `${base}.json`), JSON.stringify(rep, null, 1));
  const errs = consoleMsgs.filter((m) => !m.noise).length;
  const overflow = metrics.scrollWidth > metrics.clientWidth + 1 ? ` OVERFLOW scrollWidth ${metrics.scrollWidth} > ${metrics.clientWidth}` : '';
  console.log(`captured ${base}: ${metrics.sections.length} sections, ${metrics.docHeight}px tall, ${errs} console errors, ${metrics.brokenImages.length} broken images${overflow}`);
  if (!metrics.sections.length) console.log(`  no element matches sections.selector "${SEC.selector}" — fix the config`);
  return { status };
}

for (const page of pages) {
  const noComp = !page.frames || !Object.keys(page.frames).length;
  for (const width of widths) {
    for (const theme of themes) {
      const v = `${width}-${theme}`;
      if (!allViews && !noComp && !page.frames[v]) continue;
      if (wantViews && !wantViews.has(v)) continue;
      const base = path.join(outDir, `${page.key}-${v}`);
      if (skipExisting && fs.existsSync(base + '.png') && fs.existsSync(base + '.json')) {
        console.log(`have ${page.key}-${v} (resumed)`);
        continue;
      }
      try {
        const r = await one(page, width, theme);
        if (r.error) {
          failures++;
          console.log(`FAILED ${page.key}-${v}: ${r.error}`);
          writeAtomic(base + '.status.json', JSON.stringify({ page: page.key, route: page.route, url: r.url, status: r.status, width, theme, set, error: r.error, capturedAt: new Date().toISOString() }, null, 1));
        }
      } catch (e) {
        failures++;
        console.log(`FAILED ${page.key}-${v}: ${e.message.split('\n')[0]}`);
      }
    }
  }
}
await browser.close();
process.exit(failures ? 1 : 0);
