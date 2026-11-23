# design-compare.json — reference

One file per project, kept in the project's scratch folder (e.g. `.design-compare/design-compare.json`)
and out of git with `.git/info/exclude`. Paths in it are relative to the file. `dcg.py init` writes
`assets/design-compare.example.json` as a starting point.

## Contents

1. Top level
2. figma
3. themes and page state (localStorage, cookies, storage state, class, colour scheme)
4. capture
5. sections
6. pages, frames and map overrides
7. composite, score, sets, lock, buildId, regress
8. Output layout
9. findings.json

## 1. Top level

| Key | Default | Meaning |
|---|---|---|
| `name` | — | Short id; also the localStorage key the gallery keeps its state under |
| `title`, `subtitle` | — | Gallery heading; subtitle may hold simple HTML (what was captured, when, from where) |
| `lang` | `"en"` | UI language of the gallery and questions page: `"en"` or `"ru"` |
| `i18n` | `{}` | Override single UI strings (keys in `assets/template.html` → `I18N.en`) |
| `outputDir` | `"out"` | Where everything is written; also the HTTP server root |
| `baseUrl` | required | Running app, e.g. `http://127.0.0.1:3000`. Read-only use |
| `widths` | `[1440, 390]` | CSS widths; must match the Figma frame widths you list |

## 2. figma

```json
"figma": { "fileKey": "<FIGMA_FILE_KEY>", "token": "env:FIGMA_TOKEN", "scale": 1 }
```

- `fileKey`: from `figma.com/design/<fileKey>/…`.
- `token`: `FIGMA_TOKEN` in the environment always wins. Otherwise `"env:NAME"`, `"file:~/path"` (file
  holding only the token) or `"json:~/.claude.json#mcpServers.figma-mcp.env.FIGMA_API_KEY"`. Never put
  the token itself in the config, and never echo it — export it in the shell that runs the scripts.
- `scale`: export scale for the gallery. Keep `1`: captures are taken at device scale 1, so 1 Figma px
  = 1 CSS px and the two halves of a composite line up. (The scoreboard uses its own scale, `score.scale`.)

## 3. themes and page state

```json
"themes": {
  "light": { "localStorage": { "theme": "light" }, "background": "#ffffff", "label": "light" },
  "dark":  { "localStorage": { "theme": "dark" },  "background": "#0b0c0e", "label": "dark",
             "colorScheme": "dark", "htmlClass": "dark", "removeHtmlClass": "light",
             "cookies": [], "storageState": null }
},
"localStorage": { "cookieConsent": "rejected" },
"cookies": [{ "name": "consent", "value": "no", "domain": "127.0.0.1", "path": "/" }],
"storageState": null
```

- The theme keys are the theme names used in view keys (`1440-dark`).
- Set the theme the way the site reads it. Prefer `localStorage` / `cookies` (they work for both the
  gallery capture and the pixel-perfect scoreboard). `htmlClass` / `colorScheme` work for the capture
  only — pixel-perfect has no hook for them, so those themes are skipped by `score`.
- `background`: the page colour transparent Figma exports are flattened on. Without it the frame's
  own fill is used, then white. A wrong background shows as a coloured halo around every exported
  rounded corner and icon.
- Top-level `localStorage` / `cookies` apply to every theme — put the cookie-consent key here so the
  banner never covers a section. `hide` (below) is the fallback for banners with no storage switch.
- `storageState`: a Playwright storage-state file (signed-in session). It holds tokens: keep it
  outside the repo, delete it afterwards.

## 4. capture

| Key | Default | Why |
|---|---|---|
| `viewportHeight` | `900` | Height of the layout viewport. Full-page screenshots ignore it, but `100vh` heroes do not — use the height the design assumes |
| `waitMs` | `800` | Settle time after the scroll-through, for fonts and late images |
| `hide` | `[]` | Selectors made `visibility:hidden` before the shot (chat widgets, banners without a storage key) |
| `videos` | `"hide"` | `"hide"` / `"pause"` / `"keep"`. A playing video never matches the Figma poster and differs run to run |
| `scrollStep` | `600` | Scroll-through step that triggers lazy loading |
| `noise` | — | Regex for console errors / failed requests to ignore (captcha, analytics) |
| `fixedTime` | `null` | ISO time Date is frozen at (footer year, "3 days ago"); timers keep running. Needed when regress A = B is not identical because of a clock |

## 5. sections

```json
"sections": { "selector": "section[id]", "header": "header", "label": "id", "labelStrip": "^.*service-", "figmaSkip": "^Footer$" }
```

- `selector`: what counts as a page section in the DOM. Only outermost matches are kept. The DOM
  order must follow the Figma frame's top-level children order.
- `header`: the site header selector. When given, the first section's box is extended up to the
  header's top — most marketing comps draw the nav inside the hero frame. Leave `null` if the Figma
  file has a separate header frame (then skip it with `figmaSkip` or pair it explicitly).
- `label`: `"id"` (id → aria-label → first heading), `"heading"`, or an attribute name (`"data-section"`).
- `labelStrip`: regex removed from labels (`^.*service-` turns `redesign-sections.service-hero` into `hero`).
- `figmaSkip`: regex on Figma child names never paired (footer, annotations, sticky notes).

## 6. pages, frames and map overrides

```json
{ "key": "teams", "title": "Dedicated Teams", "route": "/services/dedicated-development-teams",
  "frames": { "1440-light": "9225:236850", "1440-dark": "9303:264069", "440-light": "9265:262558", "440-dark": "9265:265051" },
  "figmaContainer": { "440-light": "9265:262559" },
  "map": {
    "skipDom":   { "*": ["newsletter"] },
    "skipFigma": { "dark": ["Annotations"] },
    "merge":     { "440": [["offerings", "tech-stack"]] },
    "split":     { "1440": { "9225:237282": [[0, 1442], [1442, 644]] } },
    "pairs":     { "440-dark": [{ "dom": ["hero"], "figma": "9265:265053" }] }
  },
  "scoreNotes": { "hero": "Comp draws an arc we have not built (recorded).", "faq@440": "Mobile FAQ opens item 2 in the comp." } }
```

- `key`: lowercase slug, used in file names. `frames`: view key `<width>-<theme>` → Figma frame node id
  (`9265:283595` or URL form `9265-283595`). A page may lack some views.
- **A page without a design**: leave out `frames` (`{"key": "about", "route": "/about"}`). It is
  captured at every width × theme, the gallery shows our sections only in a tab marked "no design",
  and it is never scored. Useful to review new pages by eye next to the designed ones, and for regress.
- `figmaContainer`: only when auto-descent picks the wrong node. The exporter walks down through a
  single child that covers ≥ 90 % of its parent (mobile frames often wrap everything in one frame).
- Override keys inside `map` are view specs: `"*"`, a width (`"440"`), a theme (`"dark"`) or a view
  (`"440-dark"`); all matching specs apply, most specific last.
  - `merge`: consecutive DOM labels drawn as one Figma frame → one composite. The scoreboard skips
    merged pairs (one selector cannot capture two sections).
  - `split`: one Figma frame drawn as several DOM sections → crops `[y, h]` in CSS px inside that frame,
    paired in order. Read the y of the inner child from the Figma node data, not from a screenshot.
  - `pairs`: a full manual list for the matching view; nothing else is paired there.
- `scoreNotes`: `"<label>"` or `"<label>@<view spec>"` → a note shown in the scoreboard (recorded
  decisions, draft comps). This is where "known, not a bug" lives.

## 7. composite, score, sets, lock

- `composite.chunk` (2400): tall pairs are cut into parts of this many px — browsers cap canvas size
  and very tall images are unusable. `overviewColumn` (480): column width of the desktop whole-page
  strip; `overviewMobileScale` (0.4): mobile strips are shrunk to this scale instead.
- `score` — its presence makes `refresh` run the scoreboard. `viewportHeight` (9000): pixel-perfect
  clamps the capture clip to the viewport, so a 900 px viewport would cut every taller section.
  `scale` (2): reference and capture scale. `hover` (true): hover the section's first heading first,
  which scrolls it into view so its lazy images load. `pages` / `themes`: limit what is scored.
  `refSets` (`{"figma": "score/refs"}`): reference sets, first = primary; see
  `regress-and-scoring.md` for corrected references.
- `buildId`: `{"command": "...", "regex": "...", "script": "...", "volatile": ["regex"]}` — how a set
  records which build it came from; see `operations.md` §2. All optional.
- `regress`: `{"mask": ["selector"], "keepCaptures": false}` — extra selectors hidden in regress runs
  (listed in the report) and whether to keep the full-page captures.
- `sets`: label per capture set, in display order (`{"before": "Before fixes", "now": "Now"}`). If a
  set was rebuilt from an old commit, say so in its label (operations.md §3).
- `lock`: path to a machine-wide Playwright semaphore script with the protocol
  `sh lock.sh <cmd…>` → exit 4 + "slots all held" (busy) / exit 3 + "load above" (loaded), otherwise
  runs the command. Without it the scripts use a bundled slot lock in `$TMPDIR` (`DCG_SLOTS`, default 2;
  `DCG_MAX_LOAD`, default 4 × CPU count).

## 8. Output layout (under `outputDir`)

```
fig/<page>/<view>/sections.json, NN-<node>.png      Figma sections (box-sized, scale 1)
cap/<set>/_set.json                                  set metadata: base URL, per page build id, HTML hash, capture time
cap/<set>/<page>-<width>-<theme>.png/.json          full-page capture + DOM section boxes + <head> + console
cap/.<set>.staging/                                  in-progress capture (resumable; joins the set when complete)
map/<set>/<page>-<view>.json                         pairs
comp/<set>/<page>/<view>/NN-<label>[-pK].png, index.json   composites (+ 00-overview)
score/refs/<page>-<theme>/                           pixel-perfect references (shared by sets)
score/<set>/manifests|raw|cap|img|scoreboard.json|scoreboard.md, raw--<refset>/, scoreboard--<refset>.json
regress/<id>/report.json, report.md, diff/, a/, b/  build-vs-build runs
evidence/<finding>-<set>.png                          zoomed crops for findings with "crop"
index.html, .gallery.json (version), questions.html, findings.json, questions.json, q/
```

## 9. findings.json (optional "Findings" tab)

```json
[{ "id": "A1", "severity": "high", "severityLabel": "высокая", "page": "API · dark",
   "text": "gRPC logo nearly invisible in dark theme",
   "img": "comp/now/api/1440-dark/06-comparison.png" }]
```

`severity`: `high` · `medium` · `low` · `question` · `info`. An `img` under `comp/<set>/` is swapped
for the same file in every other set, so the before/after toggle works on findings too. Any other
image under `outputDir` is shown as is.

Instead of `img`, give a `crop` and the builder cuts the evidence itself — Figma | ours of the same
region of one section's composite, zoomed 2× (nearest neighbour, pixels stay pixels), for every set:

```json
{ "id": "A3", "severity": "medium", "text": "Hero arc missing under the copy (dark)",
  "crop": { "set": "now", "page": "dashboard", "view": "1440-dark", "section": 1,
            "x": 380, "y": 540, "w": 520, "h": 330, "zoom": 2 } }
```

`section` is the composite's order number (as in the gallery, `01 · hero`) or its label; x/y/w/h
are CSS px inside that section. Crops survive re-captures, hand-made crop images do not.
