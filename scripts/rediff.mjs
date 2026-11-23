#!/usr/bin/env node
// Re-diff an existing pixel-diff run against a second reference set, without re-capturing.
// Uses the pixel-perfect skill's own diffImages + classifyDiff, so the numbers are comparable.
//
// Usage: node rediff.mjs <root> <raw.json> <refDir> <diffOutDir> <out.json>
//   <root>       the --root pixel-diff ran with (paths in raw.json are relative to it)
//   <raw.json>   pixel-diff --json output of the primary run
//   <refDir>     alternative references, <state>.png (only the states that have one are re-diffed)
//   <out.json>   same shape as raw.json; re-diffed states get refset: "alt" and the new paths
// States without an alternative reference are copied unchanged (refset: "primary").
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';

const S = path.join(os.homedir(), '.claude/skills/pixel-perfect/scripts');
const { PNG } = createRequire(path.join(S, 'package.json'))('pngjs');
const { diffImages, parseHexColor } = await import(path.join(S, 'lib/image-diff.mjs'));
const { classifyDiff } = await import(path.join(S, 'pixel-diff.mjs'));

const [root, rawPath, refDir, diffDir, outPath] = process.argv.slice(2);
const d = JSON.parse(fs.readFileSync(rawPath, 'utf8'));
fs.mkdirSync(diffDir, { recursive: true });
let n = 0;
for (const s of d.meta?.states || []) {
  const alt = path.join(refDir, `${s.name}.png`);
  if (!fs.existsSync(alt) || !s.screenshotPath) { s.refset = 'primary'; continue; }
  const ref = PNG.sync.read(fs.readFileSync(alt));
  const cap = PNG.sync.read(fs.readFileSync(path.join(root, s.screenshotPath)));
  const diff = diffImages(ref, cap, { threshold: 0.1, align: 'top-left', background: parseHexColor(s.background || '#ffffff') });
  const dp = path.join(diffDir, `${s.name}.diff.png`);
  fs.writeFileSync(dp + '.tmp', PNG.sync.write(diff.diffImage));
  fs.renameSync(dp + '.tmp', dp);
  const { status, requiresReview } = classifyDiff(diff.diffPercent);
  Object.assign(s, { diffPercent: diff.diffPercent, diffPixels: diff.diffPixels, status, requiresReview, sizeMismatch: diff.sizeMismatch,
    diffImagePath: path.relative(root, dp), referencePath: path.relative(root, alt), refset: 'alt' });
  n++;
}
fs.writeFileSync(outPath + '.tmp', JSON.stringify(d, null, 1));
fs.renameSync(outPath + '.tmp', outPath);
console.log(`  ${n} state(s) re-diffed against ${refDir}`);
