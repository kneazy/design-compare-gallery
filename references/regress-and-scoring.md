# Regress (build vs build) and the scoreboard (reference sets, compare)

## Contents

1. regress — is build B exactly build A?
2. Scoreboard — scope, incremental runs
3. Two reference sets (corrected references)
4. Comparing two scoreboards

## 1. regress

Use it for: local vs staging after a release, before/after a CMS migration, a rollback check,
"did this refactor change anything visible?". It is strict on purpose — the gallery tolerates
design-vs-build differences, regress tolerates nothing.

```bash
python3 $D/dcg.py regress -c CFG --a http://127.0.0.1:3000 --b https://staging.example.com \
        [--pages home,about] [--widths 1440,390] [--themes light] [--id release-42] [--keep]
python3 $D/dcg.py gallery -c CFG        # adds the "Regression A vs B" tab
```

- Captures every selected page × width × theme from A, then from B, with the same freezing as the
  gallery capture (pages without a Figma frame included).
- **Pixels exactly**: threshold 0, no anti-aliasing forgiveness; a 1-level change in one channel of
  one pixel counts. Full page and per DOM section (a section whose box moved is reported as moved).
  Size changes are reported, the overlap is still compared.
- **`<head>`**: title, meta description, canonical, robots, hreflang, `og:*` / `twitter:*` /
  `article:*` metas, every JSON-LD block parsed with sorted keys (formatting does not count).
- HTTP status, console errors (minus `capture.noise`), broken images, failed requests — compared
  between A and B. Requests the page aborted itself (`net::ERR_ABORTED`: a paused video, a
  superseded `srcset` candidate) are kept in the capture JSON but not compared — which ones get
  aborted varies from run to run.
- Exit 0 only when everything is identical. Output: `regress/<id>/report.json`, `report.md`,
  `diff/<page>-<view>.png` (A | B | changed pixels in red, cropped to the changed area).
- Masks: `capture.hide` + `regress.mask` selectors are hidden on both sides and **listed in the
  report** — a masked region was not compared, say so when reporting "identical".

Sanity check before trusting a regress: run it with A = B (the same server). It must report
0 changed pixels everywhere. If it does not, the page has motion the freezing does not catch
(timer-driven carousels, a clock, a captcha widget) — add `capture.fixedTime` or a mask, and
re-run until A = B is identical. Only then compare two different builds.

Cross-origin A/B: theme/consent localStorage keys are set per origin by the init script, so they
apply on both. A signed-in area needs `storageState` valid for both origins.

## 2. Scoreboard scope and incremental runs

```json
"score": { "pages": ["home"], "themes": ["light"], "viewportHeight": 9000, "scale": 2,
           "refSets": { "figma": "score/refs", "corrected": "score/refs-corrected" } }
```

- `pages` / `themes` limit what `score` (and `refresh`) scores; `--pages` / `--themes` override.
- A page × theme is skipped while its live HTML/build, manifest and references are unchanged since
  its last run; `--force` re-runs, `--sections a,b` re-runs only those sections (the rest keep their
  last score), `--refs` re-exports every reference, `--only-board` rebuilds the board.

## 3. Two reference sets

Some Figma exports are wrong as references: a stale string in one mobile frame, a node exported
with a missing layer, a blend mode the renderer drops. Do not "fix" the design silently and do not
lower the bar — keep the Figma export as the primary reference set and add a corrected set:

- `score.refSets`: name → folder under `outputDir`. The first entry is primary: `figma-refs.mjs`
  exports into it and `pixel-diff.mjs` captures against it. Every other set holds only the
  replaced references, `<folder>/<page>-<theme>/<state>.png`, same size as the original.
- Every corrected reference cites why in `<folder>/<page>-<theme>/_notes.json`:
  `{"hero-440": "mobile comp still shows the old CTA string; ref re-cut from the desktop frame"}`.
  The board warns about corrected refs without a reason.
- `score` re-diffs the same captures against each extra set (`rediff.mjs`, the pixel-perfect skill's
  own `diffImages` + `classifyDiff`, no new browser run) and writes `scoreboard--<name>.json`.
- The gallery's Pixel-perfect tab gets a selector per reference set; cells scored against a
  corrected reference are marked ¹ with the reason on hover. **Report both totals** ("Figma refs
  12/9/27, corrected refs 15/10/23") — never only the nicer one.

## 4. Comparing two scoreboards

```bash
python3 $D/dcg.py score -c CFG --compare before now                 # primary refs
python3 $D/dcg.py score -c CFG --compare now:figma now:corrected     # same set, two ref sets
```

Prints totals per page × width, then the cells whose **verdict changed**, then the cells whose
verdict is the same but whose % moved (> 0.05), then the unchanged count. This answers "FAIL went
124 → 45, or did it not update?": when no verdict changed and only percentages moved, the line
says so explicitly.
