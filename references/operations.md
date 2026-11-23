# Operations — refresh, staleness, sets, disk, lock

Read this when keeping a gallery up to date across builds, or when a run was slow, blocked or
crashed.

## Contents

1. refresh — one cheap command
2. status — is the gallery stale?
3. Sets are durable: sets, prune, the `before` set
4. Resuming after a crash or network error
5. Disk
6. Playwright lock and load guard
7. Cache busting

## 1. refresh

```bash
python3 $D/dcg.py refresh -c CFG --set now            # incremental
python3 $D/dcg.py refresh -c CFG --set now --force    # redo everything
python3 $D/dcg.py refresh -c CFG --set now --no-check # skip the headless smoke test
```

Order: figma (cached per view) → capture → map → composite → score (only when the config has a
`score` section) → gallery → check. Each step skips what is already up to date and says so
(`… up to date (skipped)`); the last line counts recaptured vs skipped pages.

What "up to date" means per step:

| Step | Skipped when |
|---|---|
| figma | `fig/<page>/<view>/sections.json` exists for the same frame id (`figma --refresh` re-exports) |
| capture | the set holds every view of the page and the live page has the same fingerprint (status + build id + HTML hash) as when captured |
| map | `map/<set>/<page>-<view>.json` newer than the capture JSON, the Figma export and the config |
| composite | `comp/…/index.json` newer than the map and the Figma export |
| score | same live HTML/build, same manifest, same references as the last run |

Why: refreshing a gallery for a new build once took an agent three hours of redoing everything by
hand. With unchanged pages skipped, a refresh after a one-page change costs one page.

The fingerprint is HTML-based, so it notices CMS content changes but not content fetched
client-side after load; when in doubt use `--force` for that page (`--pages p --force`).

Incremental steps compare data, not code: after the skill's scripts were updated, or after you
changed how a step works, run that step with `--force` once (`map --force` is cheap; composites and
the score then follow by content hash and rebuild only what really changed).

## 2. status

```bash
python3 $D/dcg.py status -c CFG --set now     # exit 0 up to date, 2 stale, 1 no set
```

Fetches every page now and compares with the set: `up to date` or `STALE` per page with the reason
(build id changed, HTML changed, status changed), then one line for the set, and a warning if
`index.html` is older than the set or the scoreboard. Run it before answering "why has it not
updated yet?" — a stale gallery must be obvious, not guessed.

Build id sources, first that answers: `buildId.command` (read-only shell command, e.g.
`git -C ../app rev-parse --short HEAD`), `buildId.regex` (group 1 on the HTML), Next.js
`__NEXT_DATA__.buildId` or `/_next/static/<id>/`, `<meta name=generator>`; at capture time also
`buildId.script` (a JS expression evaluated in the page). A dev server reports `development`,
which is ignored — then the HTML hash is the signal. `buildId.volatile`: extra regexes stripped
before hashing (CSP nonces are stripped by default) when a page embeds a per-request token.

## 3. Sets are durable

A set is everything under `outputDir/{cap,map,comp,score}/<set>/`. Never point a set at a folder
elsewhere — a "before fixes" set was once lost when its source folder was cleaned up, and had to be
re-captured from an old commit against today's CMS (so content fixes already looked fixed).

- Capture `before` **before the first fix**: `dcg.py capture -c CFG --set before`, then work.
- If you must rebuild `before` from an old commit, say so in its label
  (`"sets": {"before": "Before (code 8e76015, today's CMS content)"}`) — the gallery shows labels.
- `dcg.py sets -c CFG` lists sets with label, build/time, page count and size.
- `dcg.py prune -c CFG --keep before,now` prints what it would delete; add `--yes` to delete.
  `--captures` also deletes the full-page PNGs of kept sets (composites stay; a later rebuild of
  those composites then needs `capture --force`). `--regress` deletes regression runs.

## 4. Resuming

Every file is written to `<name>.tmp` and renamed, so no step ever trusts a half-written file.
Captures go to `cap/.<set>.staging/` and a page joins the set only when all its views succeeded —
a set is never half old, half new for one page. After a crash, a network error (`ENOTFOUND`) or a
killed run, run the same command again: staged views are reused while the live fingerprint is
unchanged. Navigation retries network errors twice; the Figma exporter retries and splits image
batches that Figma fails to render in time (500/503).

## 5. Disk

Capture, score and regress refuse to start when free space is below about
`page views x 15 MB + 300 MB` and say how much is needed. Free space with `prune`; override with
`DCG_NO_DISK_CHECK=1` only when you know the estimate is wrong. Regress deletes its full-page
captures after comparing (keeps JSON, report and cropped diff images) unless `--keep`.

## 6. Lock and load guard

Every browser run goes through `run_locked`:

- config `lock`: an external semaphore script (`sh lock.sh <cmd…>`; exit 4 + "slots all held" =
  busy, exit 3 + "load above" = machine loaded). Use it when other agents share the machine.
- otherwise the bundled lock: `DCG_SLOTS` (default 2) mkdir slots in `$TMPDIR`, plus a load guard
  at `DCG_MAX_LOAD` (default **4 × CPU count**). A fixed limit of 8 once blocked all work for two
  hours on a Mac whose Spotlight indexing alone held the load near 25 — the slots are what keep
  our own runs from piling up; the load guard only stops runaway situations.
- While waiting, the reason is printed once a minute (never silently).

## 7. Cache busting

`gallery` stamps a version (build time) into the page and appends `?v=<version>` to every image
URL, and prints `open: http://127.0.0.1:<port>/index.html?v=<version>`. Give the user that exact URL
after each rebuild — a browser happily shows yesterday's `index.html` and images otherwise. The
page header shows the version and, per set, its build and capture time.
