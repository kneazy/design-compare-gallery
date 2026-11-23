#!/usr/bin/env python3
"""design-compare-gallery — one entry point for the whole pipeline.

  dcg.py init      [-c design-compare.json]       write an example config to fill in
  dcg.py figma     -c CFG [--pages p] [--refresh]  export Figma sections (REST, FIGMA_TOKEN)
  dcg.py capture   -c CFG [--set now] [--pages p] [--views 1440-light] [--force]
  dcg.py map       -c CFG [--set now] [--force]    pair DOM sections <-> Figma sections
  dcg.py composite -c CFG [--set now] [--force]
  dcg.py gallery   -c CFG [--sets before,now]      -> <out>/index.html
  dcg.py all       -c CFG [--set now]              figma + capture + map + composite + gallery
  dcg.py refresh   -c CFG [--set now] [--force] [--no-check]
                   incremental: capture (only pages whose build/HTML changed) -> map -> composite
                   -> score (if the config has "score") -> gallery -> check
  dcg.py status    -c CFG [--set now]              live build vs the set: is the gallery stale?
  dcg.py sets      -c CFG                          capture sets with build, time, size
  dcg.py prune     -c CFG --keep before,now [--captures] [--yes]
  dcg.py score     -c CFG [--set now] [...] | --compare <setA[:refset]> <setB[:refset]>
  dcg.py regress   -c CFG --a <urlA> --b <urlB> [--pages p] [--widths 1440] [--themes light] [--id name] [--keep]
  dcg.py questions -c CFG [--data q.json]          -> <out>/questions.html
  dcg.py check     -c CFG [--port 8766] [--shot file.png]   serve <out> briefly, smoke-test headless
  dcg.py test                                      the skill's self-tests
Browser steps (capture, score, regress, check) go through the Playwright lock (config "lock").
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dcglib as L  # noqa: E402

PY = sys.executable


def opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        return argv[i + 1] if i + 1 < len(argv) else default
    return default


def flag(argv, name):
    return name in argv


def py(script, argv, check=True):
    r = subprocess.run([PY, os.path.join(L.SCRIPTS, script)] + argv)
    if r.returncode and check:
        sys.exit(r.returncode)
    return r.returncode


def need_playwright():
    if not os.path.isdir(os.path.join(L.PP_SCRIPTS, 'node_modules', 'playwright')):
        L.die('Playwright missing: cd ~/.claude/skills/pixel-perfect/scripts && npm install && npx playwright install chromium-headless-shell')


# ----------------------------------------------------------------------------- capture (staged)

def capture(cfg, set_, pages=None, views=None, force=False):
    """Capture a set, page by page, without ever leaving a half set.

    A page is skipped when the set already holds every view of it and the live server still
    serves the same build + HTML (fingerprint). Pages to capture go to cap/.<set>.staging/ first;
    only a page whose views all succeeded is moved into cap/<set>/ (and recorded in _set.json).
    A crash or network error leaves the staging folder; the next run resumes from it (as long as
    the live fingerprint has not changed). Returns (captured, skipped, rc)."""
    need_playwright()
    sel = L.select_pages(cfg, pages)
    setdir = L.out(cfg, 'cap', set_)
    stage = L.out(cfg, 'cap', f'.{set_}.staging')
    os.makedirs(setdir, exist_ok=True)
    meta = L.set_meta(cfg, set_) or {'set': set_, 'pages': {}}
    smeta_p = os.path.join(stage, '_staging.json')
    smeta = json.load(open(smeta_p)) if os.path.exists(smeta_p) else {}
    want_views = set(views.split(',')) if views else None
    todo, skipped = [], []
    live = {}
    for p in sel:
        pv = [v for v in L.views(cfg, p) if not want_views or v in want_views]
        live[p['key']] = fp = L.fingerprint(cfg, p['route'])
        if fp.get('error'):
            L.log(f'  {p["key"]}: {fp["url"]} unreachable ({fp["error"]})')
        have = all(os.path.exists(os.path.join(setdir, f'{p["key"]}-{v}.json')) and
                   os.path.exists(os.path.join(setdir, f'{p["key"]}-{v}.png')) or
                   (os.path.exists(os.path.join(setdir, f'{p["key"]}-{v}.json')) and comp_done(cfg, set_, p, v)) for v in pv)
        if not force and have and L.same_content(meta['pages'].get(p['key'], {}).get('fingerprint'), fp):
            skipped.append(p['key'])
            continue
        todo.append((p, pv))
    if not todo:
        L.log(f'capture [{set_}]: all {len(skipped)} pages unchanged since capture (same build + HTML) — skipped; --force to recapture')
        return [], skipped, 0
    L.ensure_disk(cfg, sum(len(v) for _, v in todo), 'capture')
    os.makedirs(stage, exist_ok=True)
    for p, pv in todo:  # staged files from an older build are not resumable
        if not L.same_content(smeta.get(p['key']), live[p['key']]) or force:
            for v in pv:
                for ext in ('.png', '.json'):
                    f = os.path.join(stage, f'{p["key"]}-{v}{ext}')
                    if os.path.exists(f):
                        os.remove(f)
        smeta[p['key']] = live[p['key']]
    L.atomic_json(smeta, smeta_p)
    allv = sorted({v for _, pv in todo for v in pv})
    L.log(f'capture [{set_}] from {cfg["baseUrl"]}: {len(todo)} page(s) {[p["key"] for p, _ in todo]}'
          + (f', {len(skipped)} unchanged skipped' if skipped else ''))
    rc = L.run_locked(cfg, [L.node_bin(), os.path.join(L.SCRIPTS, 'capture.mjs'), cfg['_path'], set_,
                            ','.join(p['key'] for p, _ in todo), ','.join(allv)],
                      env={'DCG_OUT_DIR': stage, 'DCG_SKIP_EXISTING': '1'})
    captured = []
    for p, pv in todo:
        files = [os.path.join(stage, f'{p["key"]}-{v}{ext}') for v in pv for ext in ('.png', '.json')]
        if not all(os.path.exists(f) for f in files):
            L.log(f'  {p["key"]}: incomplete in staging — kept out of the set; rerun to resume')
            continue
        for f in files:
            os.replace(f, os.path.join(setdir, os.path.basename(f)))
        caps = [json.load(open(os.path.join(setdir, f'{p["key"]}-{v}.json'))) for v in pv]
        fp = dict(live[p['key']])
        dom = next((c.get('build') for c in caps if c.get('build')), {}) or {}
        if not fp.get('buildId'):
            bid = dom.get('script') or (dom.get('next') if dom.get('next') != 'development' else None) or dom.get('generator')
            if bid:
                fp['buildId'], fp['buildIdSource'] = bid, 'dom'
        meta['pages'][p['key']] = {'route': p['route'], 'fingerprint': fp, 'buildId': fp.get('buildId'), 'htmlHash': fp.get('htmlHash'),
                                   'capturedAt': max(c['capturedAt'] for c in caps), 'views': pv}
        smeta.pop(p['key'], None)
        captured.append(p['key'])
    meta.update(set=set_, baseUrl=cfg['baseUrl'], label=(cfg.get('sets') or {}).get(set_, set_),
                capturedAt=max([v['capturedAt'] for v in meta['pages'].values()] or [time.strftime('%Y-%m-%dT%H:%M:%S')]))
    L.atomic_json(meta, os.path.join(setdir, '_set.json'))
    if smeta:
        L.atomic_json(smeta, smeta_p)
    else:
        shutil.rmtree(stage, ignore_errors=True)
    L.log(f'capture [{set_}]: {len(captured)} page(s) captured, {len(skipped)} skipped (unchanged); set {L.build_label(meta)}')
    return captured, skipped, rc


def comp_done(cfg, set_, page, view):
    return os.path.exists(L.out(cfg, 'comp', set_, page['key'], view, 'index.json'))


# ----------------------------------------------------------------------------- status / sets / prune

def status(cfg, set_):
    meta = L.set_meta(cfg, set_)
    if not meta:
        L.log(f'set "{set_}" does not exist yet — run capture/refresh')
        return 1
    stale = []
    for p in cfg['pages']:
        m = meta['pages'].get(p['key'])
        fp = L.fingerprint(cfg, p['route'])
        if not m:
            stale.append(p['key'])
            L.log(f'  {p["key"]:14s} NOT CAPTURED   live {fp.get("buildId") or "-"} html {fp.get("htmlHash")}')
            continue
        same = L.same_content(m.get('fingerprint'), fp)
        if not same:
            stale.append(p['key'])
        old = m.get('fingerprint') or {}
        why = '' if same else ('  build ' + f'{old.get("buildId")} -> {fp.get("buildId")}' if old.get('buildId') != fp.get('buildId') else
                               f'  HTML changed {old.get("htmlHash")} -> {fp.get("htmlHash")}' if old.get('htmlHash') != fp.get('htmlHash') else
                               f'  status {old.get("status")} -> {fp.get("status")}')
        L.log(f'  {p["key"]:14s} {"up to date" if same else "STALE":11s} captured {L.local_time(m["capturedAt"])}'
              f'  build {m.get("buildId") or "-"} html {m.get("htmlHash")}{why}')
    idx = L.out(cfg, 'index.html')
    g_old = L.mtime(idx) < max([L.mtime(L.out(cfg, 'cap', set_, '_set.json'))] + [L.mtime(L.out(cfg, 'score', set_, 'scoreboard.json'))])
    L.log(f'set "{set_}" ({L.build_label(meta)}) from {meta.get("baseUrl")}: ' +
          (f'STALE — {len(stale)} page(s) changed on the server since capture: {", ".join(stale)}. Run: dcg.py refresh -c {cfg["_path"]} --set {set_}'
           if stale else 'up to date with the live server.'))
    if g_old:
        L.log(f'gallery index.html is older than the set — run: dcg.py gallery -c {cfg["_path"]}')
    return 2 if stale else 0


def sets(cfg):
    base = L.out(cfg, 'cap')
    if not os.path.isdir(base):
        L.log('no capture sets yet')
        return
    L.log(f'{"set":12s} {"label":22s} {"build / captured":40s} {"pages":>5s} {"MB":>8s}')
    for s in sorted(os.listdir(base)):
        d = os.path.join(base, s)
        if not os.path.isdir(d):
            continue
        meta = L.set_meta(cfg, s) if not s.startswith('.') else None
        mb = sum(L.dir_mb(L.out(cfg, k, s)) for k in ('cap', 'map', 'comp', 'score'))
        L.log(f'{s:12s} {(meta or {}).get("label", "(staging)" if s.startswith(".") else "?")[:22]:22s} {L.build_label(meta)[:40]:40s} '
              f'{len((meta or {}).get("pages", {})):5d} {mb:8.1f}')
    for k in ('fig', 'regress', 'score/refs'):
        if os.path.isdir(L.out(cfg, k)):
            L.log(f'{k:12s} {"":22s} {"(shared)":40s} {"":5s} {L.dir_mb(L.out(cfg, k)):8.1f}')
    L.log(f'total {L.dir_mb(cfg["_out"]):.1f} MB in {cfg["_out"]}; free {shutil.disk_usage(cfg["_out"]).free / 2**30:.1f} GB')


def prune(cfg, argv):
    keep = set((opt(argv, '--keep') or '').split(',')) - {''}
    if not keep:
        L.die('prune needs --keep <set,set> (the sets to keep)')
    caps = flag(argv, '--captures')
    targets = []
    base = L.out(cfg, 'cap')
    for s in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        if s.startswith('.') or s not in keep:
            for k in ('cap', 'map', 'comp', 'score'):
                d = L.out(cfg, k, s)
                if os.path.exists(d):
                    targets.append((d, L.dir_mb(d)))
        elif caps:  # full-page captures of kept sets: composites keep the evidence; recapture needs --force
            for f in os.listdir(os.path.join(base, s)):
                if f.endswith('.png'):
                    p = os.path.join(base, s, f)
                    targets.append((p, os.path.getsize(p) / 2 ** 20))
    for k in ('regress',):
        if flag(argv, '--regress') and os.path.isdir(L.out(cfg, k)):
            targets.append((L.out(cfg, k), L.dir_mb(L.out(cfg, k))))
    if not targets:
        L.log('nothing to prune')
        return
    tot = sum(m for _, m in targets)
    for t, m in targets:
        L.log(f'  {"delete" if flag(argv, "--yes") else "would delete"} {L.rel(cfg, t)}  {m:.1f} MB')
    if not flag(argv, '--yes'):
        L.log(f'{len(targets)} item(s), {tot:.1f} MB — nothing deleted; re-run with --yes to delete')
        return
    for t, _ in targets:
        shutil.rmtree(t) if os.path.isdir(t) else os.remove(t)
    L.log(f'deleted {len(targets)} item(s), {tot:.1f} MB freed')


# ----------------------------------------------------------------------------- check

def free_port(p):
    with socket.socket() as so:
        return so.connect_ex(('127.0.0.1', p)) != 0


def check(cfg, argv):
    need_playwright()
    port = int(opt(argv, '--port', 8766))
    if not free_port(port):
        L.die(f'127.0.0.1:{port} is taken — pass --port')
    srv = subprocess.Popen([PY, '-m', 'http.server', str(port), '--bind', '127.0.0.1', '--directory', cfg['_out']],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if not free_port(port):
                break
            time.sleep(0.1)
        v = gallery_version(cfg)
        urls = [f'http://127.0.0.1:{port}/index.html?v={v}']
        if os.path.exists(L.out(cfg, 'questions.html')):
            urls.append(f'http://127.0.0.1:{port}/questions.html?v={v}')
        shot = opt(argv, '--shot')
        cmd = [L.node_bin(), os.path.join(L.SCRIPTS, 'check_gallery.mjs')] + urls + (['--shot', shot] if shot else [])
        rc = L.run_locked(cfg, cmd)
        L.log(f'check {"passed" if rc == 0 else "FAILED"} — open: {urls[0]}  (serve: python3 -m http.server {port} --bind 127.0.0.1 --directory {cfg["_out"]})')
        return rc
    finally:
        srv.terminate()
        srv.wait()


def gallery_version(cfg):
    try:
        return json.load(open(L.out(cfg, '.gallery.json')))['version']
    except Exception:
        return 'x'


# ----------------------------------------------------------------------------- main

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        return
    cmd, argv = sys.argv[1], sys.argv[2:]
    if cmd == 'test':
        sys.exit(subprocess.run([PY, '-W', 'ignore::ResourceWarning', '-m', 'unittest', 'discover', '-s', os.path.join(L.SCRIPTS, 'tests'), '-t', L.SCRIPTS, '-v']).returncode)
    if cmd == 'init':
        dst = opt(argv, '-c', 'design-compare.json')
        if os.path.exists(dst):
            L.die(f'{dst} exists')
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        shutil.copy(os.path.join(L.ASSETS, 'design-compare.example.json'), dst)
        L.log(f'wrote {dst} — fill in figma.fileKey, baseUrl, pages[].frames; see references/config.md')
        return
    cfgp = opt(argv, '-c') or opt(argv, '--config')
    cfg = L.load_config(cfgp)
    os.makedirs(cfg['_out'], exist_ok=True)
    rest = [a for i, a in enumerate(argv) if a not in ('-c', '--config') and (i == 0 or argv[i - 1] not in ('-c', '--config'))]
    base = ['-c', cfg['_path']]
    set_ = opt(argv, '--set', 'now')
    pages = ['--pages', opt(argv, '--pages')] if opt(argv, '--pages') else []
    force = ['--force'] if flag(argv, '--force') else []
    if cmd == 'figma':
        py('figma_export.py', base + rest)
    elif cmd == 'capture':
        _, _, rc = capture(cfg, set_, opt(argv, '--pages'), opt(argv, '--views'), flag(argv, '--force'))
        sys.exit(rc)
    elif cmd == 'map':
        py('map_sections.py', base + rest)
    elif cmd == 'composite':
        py('composite.py', base + rest)
    elif cmd == 'gallery':
        py('build_gallery.py', base + rest)
    elif cmd == 'score':
        py('score.py', base + rest)
    elif cmd == 'questions':
        py('questions.py', base + rest)
    elif cmd == 'regress':
        py('regress.py', base + rest, check=False) and sys.exit(1)
    elif cmd == 'check':
        sys.exit(check(cfg, argv))
    elif cmd == 'status':
        sys.exit(status(cfg, set_))
    elif cmd == 'sets':
        sets(cfg)
    elif cmd == 'prune':
        prune(cfg, argv)
    elif cmd == 'all':
        py('figma_export.py', base + pages)
        capture(cfg, set_, opt(argv, '--pages'), None, flag(argv, '--force'))
        py('map_sections.py', base + ['--set', set_] + pages + force)
        py('composite.py', base + ['--set', set_] + pages + force)
        py('build_gallery.py', base)
    elif cmd == 'refresh':
        t0 = time.time()
        L.log(f'== refresh set "{set_}" against {cfg["baseUrl"]}')
        py('figma_export.py', base + pages)          # cached per view; cheap when nothing changed
        cap, skipped, rc = capture(cfg, set_, opt(argv, '--pages'), None, flag(argv, '--force'))
        py('map_sections.py', base + ['--set', set_] + pages + force)
        py('composite.py', base + ['--set', set_] + pages + force)
        if 'score' in cfg['_raw']:
            py('score.py', base + ['--set', set_] + force, check=False)
        py('build_gallery.py', base)
        crc = 0
        if not flag(argv, '--no-check'):
            crc = check(cfg, argv)
        L.log(f'== refresh done in {time.time() - t0:.0f}s: {len(cap)} page(s) recaptured, {len(skipped)} unchanged and skipped; '
              f'capture rc {rc}, check rc {crc}')
        sys.exit(rc or crc)
    else:
        L.die(f'unknown command {cmd} — see dcg.py --help')


if __name__ == '__main__':
    main()
