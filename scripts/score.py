#!/usr/bin/env python3
"""Per-section pixel-perfect scoreboard: drives the pixel-perfect skill's own scripts.

Usage: python3 score.py -c design-compare.json [--set now] [--pages a,b] [--themes light]
                        [--sections hero,faq] [--refs] [--force] [--only-board]
       python3 score.py -c design-compare.json --compare <setA[:refset]> <setB[:refset]>

Per page x theme (one pixel-perfect manifest each, one state per section per width):
  1. writes <out>/score/<set>/manifests/<page>-<theme>.figma.json from the section map
     (capture = the DOM section selector, bleed 0; node = the paired Figma section)
  2. exports references with ~/.claude/skills/pixel-perfect/scripts/figma-refs.mjs into the
     primary reference set (score.refSets, first entry; default score/refs/<page>-<theme>/),
     then fixes them: render-bounds bleed -> re-export with use_absolute_bounds; split crops /
     a site header the Figma hero draws -> cropped rows
  3. runs pixel-diff.mjs through the Playwright lock with a storage-state file per theme
     (config localStorage + theme localStorage), viewport height score.viewportHeight
     (the skill clamps the capture clip to the viewport, so it must be taller than any section)
  4. re-diffs the same captures against every other reference set (rediff.mjs) — e.g. hand-
     corrected references where a Figma export is wrong; each corrected ref cites why in
     <refset>/<page>-<theme>/_notes.json {"<state>": "why"}
  5. writes <out>/score/<set>/scoreboard[--<refset>].json/.md and gallery-sized images
Incremental: a page x theme is not re-run while the live page fingerprint, the manifest and the
references are unchanged since its last run (--force re-runs). Scope: score.pages / score.themes
in the config, or --pages / --themes.
Merged pairs (several DOM sections vs one Figma frame) are skipped: one selector cannot capture
them. Themes set only by htmlClass / colorScheme cannot be scored (no pixel-perfect hook) -> SKIP.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dcglib as L  # noqa: E402
from PIL import Image  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
VERD = {'PASS': 'PASS', 'WARNING': 'WARN', 'FAIL': 'FAIL'}


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:40] or 'section'


def png_size(p):
    try:
        with open(p, 'rb') as f:
            h = f.read(24)
        return list(struct.unpack('>II', h[16:24]))
    except Exception:
        return None


def refsets(cfg):
    rs = cfg['score'].get('refSets') or {'figma': 'score/refs'}
    return list(rs.items())  # [(name, dir relative to out)], first = primary


def note_for(page, label, view):
    out = []
    for k, v in (page.get('scoreNotes') or {}).items():
        lab, _, spec = k.partition('@')
        if lab == label and (not spec or L.view_match(spec, view)):
            out.append(v)
    return ' '.join(out)


def storage_state(cfg, theme, d):
    u = urllib.parse.urlparse(cfg['baseUrl'])
    origin = f'{u.scheme}://{u.netloc}'
    base = {'cookies': [], 'origins': []}
    if cfg.get('storageState'):
        base = json.load(open(os.path.expanduser(cfg['storageState'])))
    th = cfg['themes'].get(theme, {})
    ls = dict(cfg.get('localStorage') or {}, **(th.get('localStorage') or {}))
    o = next((x for x in base['origins'] if x['origin'] == origin), None)
    if not o:
        o = {'origin': origin, 'localStorage': []}
        base['origins'].append(o)
    o['localStorage'] = [e for e in o['localStorage'] if e['name'] not in ls] + \
        [{'name': k, 'value': v if isinstance(v, str) else json.dumps(v)} for k, v in ls.items()]
    for c in (cfg.get('cookies') or []) + (th.get('cookies') or []):
        c = dict({'domain': u.hostname, 'path': '/', 'expires': -1, 'httpOnly': False, 'secure': False, 'sameSite': 'Lax'}, **c)
        c.pop('url', None)
        base['cookies'].append(c)
    p = os.path.join(d, f'{theme}.json')
    L.atomic_json(base, p)
    return p


def manifests(cfg, set_, pages, themes, sections):
    sd = L.out(cfg, 'score', set_, 'manifests')
    os.makedirs(sd, exist_ok=True)
    vh = int(cfg['score']['viewportHeight'])
    made = []
    for page in L.comp_pages(cfg, pages):
        for theme in themes:
            states, extra, labels = [], [], {}
            for view in L.views(cfg, page):
                w, t = L.parse_view(view)
                if t != theme:
                    continue
                mp = L.out(cfg, 'map', set_, f'{page["key"]}-{view}.json')
                if not os.path.exists(mp):
                    continue
                for p in json.load(open(mp))['pairs']:
                    lab = slug(p['label'])
                    if sections and lab not in sections and p['label'] not in sections:
                        continue
                    name = f'{lab}-{w}'
                    f = p['figma']
                    if len(p['dom']) > 1:
                        extra.append({'name': name, 'label': p['label'], 'view': view, 'skip': 'merged pair (several DOM sections vs one Figma frame)'})
                        continue
                    sel = p['domSelector']
                    crop = f['crop']
                    if p.get('withHeader') and p.get('headerH'):
                        y0, h0 = crop or (0, f['h'])
                        crop = [y0 + p['headerH'], h0 - p['headerH']]
                    h = crop[1] if crop else f['h']
                    st = {'name': name, 'node': f['id'], 'viewport': {'width': w, 'height': vh},
                          'capture': {'selector': sel, 'bleed': 0}, 'pixel': True,
                          'figmaBox': {'name': f['name'], 'width': f['w'], 'height': round(h, 2), 'nodeHeight': f['h']},
                          'expect': [{'target': sel, 'styles': {'boxHeight': f'{round(h, 2)}px'},
                                      'note': f'Figma {f["id"]} "{f["name"]}" height' + (f' (crop y={round(crop[0])})' if crop else '')}]}
                    if cfg['score'].get('hover', True) and p.get('hasText'):
                        # hovering the first heading/paragraph scrolls the section into view and waits,
                        # so lazy images inside it load before the capture
                        st['interaction'] = [{'type': 'hover', 'target': f'{sel} :is(h1,h2,h3,h4,p)'}]
                    if crop:
                        st['refCrop'] = {'y': crop[0], 'h': crop[1]}
                    n = ' '.join(x for x in [note_for(page, p['label'], view),
                                             f'Reference cropped {p["headerH"]:g}px at the top: the part of the site header above the section, which the Figma frame draws.' if p.get('withHeader') and p.get('headerH') else ''] if x)
                    if n:
                        st['note'] = n
                    labels[name] = p['label']
                    states.append(st)
            if not states:
                continue
            man = {'component': f'{page["key"]}-{theme}', 'figma': {'fileKey': cfg['figma']['fileKey'], 'scale': int(cfg['score']['scale'])},
                   'defaults': {'route': page['route'], 'capture': {'bleed': 0}},
                   'notes': f'Generated by design-compare-gallery score.py from map/{set_}; one state per section per width.',
                   'states': states, 'x-labels': labels, 'x-skipped': extra}
            mf = os.path.join(sd, f'{page["key"]}-{theme}.figma.json')
            text = json.dumps(man, indent=1, ensure_ascii=False)
            if not os.path.exists(mf) or open(mf, encoding='utf-8').read() != text:
                L.atomic_text(text, mf)  # untouched when identical: its hash drives the incremental skip
            made.append((page, theme, mf, man))
    return made


def fix_refs(cfg, man, refdir, scale):
    meta_p = os.path.join(refdir, '_meta.json')
    meta = json.load(open(meta_p)) if os.path.exists(meta_p) else {}
    redo = []
    for s in man['states']:
        p = os.path.join(refdir, f'{s["name"]}.png')
        if not os.path.exists(p):
            continue
        e = meta.get(s['name'])
        if e and e.get('done') and e.get('node') == s['node'] and e.get('refCrop') == s.get('refCrop'):
            continue
        W, H = png_size(p)
        ew, eh = round(s['figmaBox']['width'] * scale), round(s['figmaBox']['nodeHeight'] * scale)
        meta[s['name']] = {'node': s['node'], 'exported': [W, H], 'expected': [ew, eh], 'refCrop': s.get('refCrop')}
        if abs(W - ew) > 2 or abs(H - eh) > 2:
            redo.append(s)
    if redo:
        ids = sorted({s['node'] for s in redo})
        d = L.figma_get(cfg, f'/v1/images/{cfg["figma"]["fileKey"]}', {'ids': ','.join(ids), 'scale': str(scale), 'format': 'png', 'use_absolute_bounds': 'true'})
        for s in redo:
            u = (d.get('images') or {}).get(s['node'])
            if u:
                L.download(u, os.path.join(refdir, f'{s["name"]}.png'))
                meta[s['name']]['absoluteBounds'] = png_size(os.path.join(refdir, f'{s["name"]}.png'))
        L.log(f'  {len(redo)} references re-exported at box size (render-bounds bleed)')
    for s in man['states']:
        e = meta.get(s['name'])
        if not e or e.get('done'):
            continue
        if s.get('refCrop'):
            p = os.path.join(refdir, f'{s["name"]}.png')
            im = Image.open(p)
            y0 = round(s['refCrop']['y'] * scale)
            h = min(round(s['refCrop']['h'] * scale), im.height - y0)
            L.atomic_image(im.crop((0, y0, im.width, y0 + h)), p)
            e['cropped'] = [y0, h]
        e['done'] = True
    old = json.load(open(meta_p)) if os.path.exists(meta_p) else None
    if old != json.loads(json.dumps(meta)):
        L.atomic_json(meta, meta_p)
    return meta


def digest(*paths):
    h = hashlib.sha1()
    for p in paths:
        if os.path.isdir(p):
            for f in sorted(os.listdir(p)):
                if f.endswith('.png'):  # not _meta.json: bookkeeping, rewritten every run
                    h.update(f.encode())
                    h.update(str(os.path.getsize(os.path.join(p, f))).encode())
                    h.update(str(int(L.mtime(os.path.join(p, f)))).encode())
        elif os.path.exists(p):
            h.update(open(p, 'rb').read())
    return h.hexdigest()[:12]


def run(cfg, set_, made, refs, force):
    root = L.out(cfg, 'score')
    stdir = os.path.join(root, 'state')
    os.makedirs(stdir, exist_ok=True)
    scale = int(cfg['score']['scale'])
    rsets = refsets(cfg)
    prim = L.out(cfg, rsets[0][1])
    done = skipped = 0
    for page, theme, mf, man in made:
        th = cfg['themes'].get(theme, {})
        if (th.get('htmlClass') or th.get('colorScheme')) and not th.get('localStorage') and not th.get('storageState'):
            L.log(f'{page["key"]}-{theme}: theme is set by class/colorScheme only — pixel-perfect cannot apply it; skipped')
            continue
        refdir = os.path.join(prim, f'{page["key"]}-{theme}')
        os.makedirs(refdir, exist_ok=True)
        meta_p = os.path.join(refdir, '_meta.json')
        meta = json.load(open(meta_p)) if os.path.exists(meta_p) else {}
        stale = [s for s in man['states'] if not os.path.exists(os.path.join(refdir, f'{s["name"]}.png'))
                 or meta.get(s['name'], {}).get('node') != s['node'] or meta.get(s['name'], {}).get('refCrop') != s.get('refCrop')]
        if refs or stale:
            for s in (man['states'] if refs else stale):
                meta.pop(s['name'], None)
                try:
                    os.remove(os.path.join(refdir, f'{s["name"]}.png'))
                except FileNotFoundError:
                    pass
            L.atomic_json(meta, meta_p)
            env = dict(os.environ, FIGMA_TOKEN=L.figma_token(cfg))
            sub = dict(man, states=man['states'] if refs else stale)
            tmp = os.path.join(root, set_, 'manifests', f'.refs-{page["key"]}-{theme}.figma.json')
            json.dump(sub, open(tmp, 'w'))
            r = subprocess.run([L.node_bin(), os.path.join(L.PP_SCRIPTS, 'figma-refs.mjs'), f'{set_}/manifests', '--root', root,
                                '--manifest', tmp, '--out-dir', refdir, '--json'], env=env, capture_output=True, text=True)
            os.remove(tmp)
            if r.returncode:
                L.log(f'  figma-refs exit {r.returncode}: {(r.stderr or r.stdout)[-400:]}')
            L.log(f'{page["key"]}-{theme}: {len(sub["states"])} references exported to {L.rel(cfg, refdir)}')
        fix_refs(cfg, man, refdir, scale)
        capdir = os.path.join(root, set_, 'cap', f'{page["key"]}-{theme}')
        rawdir = os.path.join(root, set_, 'raw')
        os.makedirs(capdir, exist_ok=True)
        os.makedirs(rawdir, exist_ok=True)
        outj = os.path.join(rawdir, f'{page["key"]}-{theme}.json')
        fp = L.fingerprint(cfg, page['route'])
        key = {'html': fp.get('htmlHash'), 'build': fp.get('buildId'), 'manifest': digest(mf), 'refs': digest(refdir)}
        old = json.load(open(outj)) if os.path.exists(outj) else None
        if not force and old and old.get('x-key') == key and not refs:
            skipped += 1
            L.log(f'{page["key"]}-{theme}: unchanged since last score (same HTML, manifest, references) — skipped')
        else:
            L.ensure_disk(cfg, max(1, len(man['states']) // 4), 'score')
            env = {'PIXEL_PERFECT_STORAGE_STATE': storage_state(cfg, theme, stdir), 'DCG_OUT': outj + '.run'}
            L.log(f'{time.strftime("%H:%M:%S")} pixel-diff {page["key"]}-{theme} ({len(man["states"])} states)')
            cmd = ['sh', '-c', 'exec "$0" "$@" > "$DCG_OUT" 2> "$DCG_OUT.err"', L.node_bin(), os.path.join(L.PP_SCRIPTS, 'pixel-diff.mjs'),
                   f'{set_}/manifests', '--root', root, '--manifest', mf, '--figma-dir', refdir, '--out-dir', capdir,
                   '--base-url', cfg['baseUrl'], '--json']
            rc = L.run_locked(cfg, cmd, env=env)
            if rc > 1:
                err = open(outj + '.run.err').read()[-500:] if os.path.exists(outj + '.run.err') else ''
                L.log(f'  pixel-diff exit {rc}: {err}')
                continue
            new = json.load(open(outj + '.run'))
            if old and len(man['states']) < len(old.get('meta', {}).get('states', [])):
                # partial run (--sections): keep the other sections' last score
                keep = {s['name']: s for s in old['meta']['states']}
                keep.update({s['name']: s for s in new['meta']['states']})
                new['meta']['states'] = list(keep.values())
            new['x-key'] = key
            new['x-fingerprint'] = fp
            L.atomic_json(new, outj)
            for x in (outj + '.run', outj + '.run.err'):
                if os.path.exists(x):
                    os.remove(x)
            done += 1
        # other reference sets: re-diff the same captures, no browser needed
        for name, rdir in rsets[1:]:
            alt = L.out(cfg, rdir, f'{page["key"]}-{theme}')
            if not os.path.isdir(alt):
                continue
            ad = os.path.join(root, set_, f'raw--{name}')
            os.makedirs(ad, exist_ok=True)
            subprocess.run([L.node_bin(), os.path.join(L.SCRIPTS, 'rediff.mjs'), root, outj, alt,
                            os.path.join(root, set_, f'cap--{name}', f'{page["key"]}-{theme}'), os.path.join(ad, f'{page["key"]}-{theme}.json')], check=False)
    L.log(f'score [{set_}]: {done} page x theme run(s), {skipped} unchanged (skipped)')


def board(cfg, set_, pages, refset=None):
    """Build the scoreboard of one set against one reference set (None = primary)."""
    root = L.out(cfg, 'score')
    sd = os.path.join(root, set_)
    rsets = refsets(cfg)
    rname = refset or rsets[0][0]
    rawd = os.path.join(sd, 'raw' if not refset else f'raw--{refset}')
    if not os.path.isdir(rawd):
        return None
    imgd = os.path.join(sd, 'img' if not refset else f'img--{refset}')
    altdir = None if not refset else L.out(cfg, dict(rsets)[refset])
    cols = [v for v in L.views(cfg) if any(v in L.views(cfg, p) for p in L.comp_pages(cfg))]
    res = {}
    for f in os.listdir(rawd):
        if f.endswith('.json'):
            d = json.load(open(os.path.join(rawd, f)))
            for s in d.get('meta', {}).get('states', []):
                res[(f[:-5], s['name'])] = s
    B = {'generated': time.strftime('%Y-%m-%d %H:%M'), 'set': set_, 'refset': rname, 'refsets': [r for r, _ in rsets],
         'baseUrl': cfg['baseUrl'], 'scale': int(cfg['score']['scale']), 'columns': cols,
         'thresholds': {'PASS': '<0.5%', 'WARN': '<2%', 'FAIL': '>=2%'}, 'pages': []}
    allc = []
    missing_why = []
    for page in L.comp_pages(cfg, pages):
        rows, order = {}, []
        for theme in cfg['themes']:
            mf = os.path.join(sd, 'manifests', f'{page["key"]}-{theme}.figma.json')
            if not os.path.exists(mf):
                continue
            man = json.load(open(mf))
            labels = man.get('x-labels', {})
            notes = {}
            if altdir and os.path.exists(os.path.join(altdir, f'{page["key"]}-{theme}', '_notes.json')):
                notes = json.load(open(os.path.join(altdir, f'{page["key"]}-{theme}', '_notes.json')))
            for s in man['states'] + man.get('x-skipped', []):
                w = int(s['name'].rsplit('-', 1)[1])
                view = f'{w}-{theme}'
                lab = labels.get(s['name']) or s.get('label') or s['name'].rsplit('-', 1)[0]
                if lab not in rows:
                    rows[lab] = {}
                    order.append(lab)
                if 'skip' in s:
                    rows[lab][view] = {'verdict': 'SKIP', 'diff': None, 'note': s['skip']}
                    continue
                c = {'node': s['node'], 'note': s.get('note'), 'state': s['name']}
                r = res.get((f'{page["key"]}-{theme}', s['name']))
                if not r:
                    c.update(verdict='NOT RUN', diff=None)
                elif r.get('diffPercent') is None:
                    c.update(verdict='ERROR', diff=None, error=r.get('error'))
                else:
                    ref = os.path.join(root, r['referencePath']) if r.get('referencePath') else None
                    cap = os.path.join(root, r['screenshotPath']) if r.get('screenshotPath') else None
                    dif = os.path.join(root, r['diffImagePath']) if r.get('diffImagePath') else None
                    sc = B['scale']
                    fs = [round(v / sc, 1) for v in png_size(ref)] if ref and png_size(ref) else None
                    os_ = [round(v / sc, 1) for v in png_size(cap)] if cap and png_size(cap) else None
                    c.update(verdict=VERD.get(r['status'], r['status']), diff=r['diffPercent'], figma=fs, ours=os_,
                             dw=round(os_[0] - fs[0], 1) if fs and os_ else None, dh=round(os_[1] - fs[1], 1) if fs and os_ else None)
                    if r.get('refset') == 'alt':
                        c['refset'] = rname
                        c['refNote'] = notes.get(s['name'])
                        if not c['refNote']:
                            missing_why.append(f'{page["key"]}-{theme}/{s["name"]}')
                    if ref and cap and dif and os.path.exists(dif):
                        od = os.path.join(imgd, f'{page["key"]}-{theme}')
                        os.makedirs(od, exist_ok=True)
                        for src, suf in ((cap, 'cap.jpg'), (ref, 'ref.jpg')):
                            dst = os.path.join(od, f'{s["name"]}.{suf}')
                            if L.mtime(dst) < L.mtime(src):
                                im = Image.open(src).convert('RGB')
                                L.atomic_image(im.resize((max(1, im.width // sc), max(1, im.height // sc)), Image.LANCZOS), dst, quality=85)
                        dd = os.path.join(od, f'{s["name"]}.diff.png')
                        if L.mtime(dd) < L.mtime(dif):
                            shutil.copy(dif, dd)
                        base = L.rel(cfg, os.path.join(od, s['name']))
                        c['img'] = {'capture': base + '.cap.jpg', 'ref': base + '.ref.jpg', 'diff': base + '.diff.png'}
                rows[lab][view] = c
                allc.append((page, lab, view, c))
        if not order:
            continue
        tot = {}
        for v in cols:
            cs = [rows[r][v] for r in order if v in rows[r]]
            if not cs:
                continue
            cnt = {k: sum(1 for c in cs if c['verdict'] == k) for k in ('PASS', 'WARN', 'FAIL', 'ERROR', 'NOT RUN', 'SKIP')}
            ds = [c['diff'] for c in cs if c['diff'] is not None]
            cnt['meanDiff'] = round(sum(ds) / len(ds), 2) if ds else None
            tot[v] = cnt
        B['pages'].append({'page': page['key'], 'title': page['title'], 'route': page['route'],
                           'sections': [{'section': r, 'cells': rows[r]} for r in order], 'totals': tot})
    if not B['pages']:
        return None
    best = {}
    for page, lab, view, c in allc:
        if c.get('diff') is None:
            continue
        k = (page['key'], lab, view.split('-')[0])
        if k not in best or c['diff'] > best[k][1]['diff']:
            best[k] = (view, c)
    B['worst'] = [{'page': k[0], 'section': k[1], 'view': v[0], 'diff': v[1]['diff'], 'figma': v[1].get('figma'), 'ours': v[1].get('ours'), 'note': v[1].get('note')}
                  for k, v in sorted(best.items(), key=lambda kv: -kv[1][1]['diff'])[:10]]
    summ = {}
    for p in B['pages']:
        for v, t in p['totals'].items():
            a = summ.setdefault(f'{p["title"]} {v.split("-")[0]}', {'PASS': 0, 'WARN': 0, 'FAIL': 0, 'ERROR': 0})
            for k in a:
                a[k] += t.get(k, 0)
    B['summary'] = summ
    B['total'] = {k: sum(v[k] for v in summ.values()) for k in ('PASS', 'WARN', 'FAIL', 'ERROR')}
    name = 'scoreboard' + (f'--{refset}' if refset else '')
    L.atomic_json(B, os.path.join(sd, name + '.json'))

    def cell(c):
        if not c:
            return '—'
        if c.get('diff') is None:
            return c['verdict']
        s = f"{c['diff']:.2f}% {c['verdict']}"
        if abs(c.get('dh') or 0) >= 1 or abs(c.get('dw') or 0) >= 1:
            s += f" ({c['dh']:+g}h" + (f", {c['dw']:+g}w" if abs(c.get('dw') or 0) >= 1 else '') + ')'
        return s + (' ¹' if c.get('refset') and refset else '')
    md = [f'# Pixel-perfect scoreboard — set {set_}, references "{rname}"', '',
          f'Generated {B["generated"]} against {B["baseUrl"]} by pixel-diff (pixel-perfect skill), scale {B["scale"]}, '
          'one state per section per width, PASS <0.5% · WARN <2% · FAIL ≥2%. Δ in CSS px, ours minus Figma. '
          'pixel-diff aligns top-left: one taller element shifts everything below it, so use the % to rank and track and judge on the diff image.', '',
          f'Total: {B["total"]["PASS"]} PASS / {B["total"]["WARN"]} WARN / {B["total"]["FAIL"]} FAIL' + (' — ¹ = corrected reference' if refset else ''), '']
    for p in B['pages']:
        md += [f'## {p["title"]} — `{p["route"]}`', '', '| Section | ' + ' | '.join(cols) + ' |', '|---' * (len(cols) + 1) + '|']
        for r in p['sections']:
            md.append(f'| {r["section"]} | ' + ' | '.join(cell(r['cells'].get(v)) for v in cols) + ' |')
        md.append('')
    L.atomic_text('\n'.join(md), os.path.join(sd, name + '.md'))
    L.log(f'scoreboard [{set_} / {rname}]: {B["total"]["PASS"]}P/{B["total"]["WARN"]}W/{B["total"]["FAIL"]}F  ' +
          ', '.join(f"{k}: {v['PASS']}P/{v['WARN']}W/{v['FAIL']}F" for k, v in summ.items()))
    if missing_why:
        L.log(f'  ! {len(missing_why)} corrected reference(s) without a reason in _notes.json: {", ".join(missing_why[:6])}')
    return B


def load_board(cfg, spec):
    set_, _, rs = spec.partition(':')
    prim = refsets(cfg)[0][0]
    name = 'scoreboard' + (f'--{rs}' if rs and rs != prim else '')
    p = L.out(cfg, 'score', set_, name + '.json')
    if not os.path.exists(p):
        L.die(f'no scoreboard {L.rel(cfg, p)} — run score for set {set_}')
    return json.load(open(p))


def diff_boards(A, B, eps=0.05):
    """(verdict changed, same verdict but % moved > eps, unchanged count, only in B, only in A)."""
    idx = {(p['page'], r['section'], v): c for p in A['pages'] for r in p['sections'] for v, c in r['cells'].items()}
    verdict, moved, same, new = [], [], 0, []
    for p in B['pages']:
        for r in p['sections']:
            for v, c in r['cells'].items():
                o = idx.pop((p['page'], r['section'], v), None)
                if o is None:
                    new.append((p['page'], r['section'], v, c))
                    continue
                d0, d1 = o.get('diff'), c.get('diff')
                if o.get('verdict') != c.get('verdict'):
                    verdict.append((p['page'], r['section'], v, o, c))
                elif d0 is not None and d1 is not None and abs(d1 - d0) > eps:
                    moved.append((p['page'], r['section'], v, o, c))
                else:
                    same += 1
    return verdict, moved, same, new, idx


def compare(cfg, a, b, eps=0.05):
    """Which cells changed verdict, which only moved %. Answers "FAIL 124 -> 45 — did it update?"."""
    A, B = load_board(cfg, a), load_board(cfg, b)
    ta, tb = A['total'], B['total']
    L.log(f'== {a} -> {b}')
    L.log(f'total PASS/WARN/FAIL: {ta["PASS"]}/{ta["WARN"]}/{ta["FAIL"]} -> {tb["PASS"]}/{tb["WARN"]}/{tb["FAIL"]}')
    for k in sorted(set(A['summary']) | set(B['summary'])):
        x, y = A['summary'].get(k), B['summary'].get(k)
        f = lambda z: f'{z["PASS"]}/{z["WARN"]}/{z["FAIL"]}' if z else '—'  # noqa: E731
        L.log(f'  {k:28s} {f(x):>10s} -> {f(y)}')
    verdict, moved, same, new, idx = diff_boards(A, B, eps)
    fmtc = lambda c: f'{c["diff"]:.2f}% {c["verdict"]}' if c.get('diff') is not None else c.get('verdict')  # noqa: E731
    L.log(f'verdict changed: {len(verdict)}')
    for pg, s, v, o, c in verdict:
        L.log(f'  {pg:14s} {s:24s} {v:11s} {fmtc(o):>16s} -> {fmtc(c)}')
    L.log(f'same verdict, % moved > {eps}: {len(moved)}')
    for pg, s, v, o, c in sorted(moved, key=lambda t: t[4]['diff'] - t[3]['diff']):
        L.log(f'  {pg:14s} {s:24s} {v:11s} {fmtc(o):>16s} -> {fmtc(c)}  ({c["diff"] - o["diff"]:+.2f})')
    L.log(f'unchanged: {same}; only in {b}: {len(new)}; only in {a}: {len(idx)}')
    if not verdict and moved:
        L.log('=> no verdict changed — only percentages moved. A totals line that looks "not updated" is exactly this.')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('-c', '--config')
    ap.add_argument('--set', default='now')
    ap.add_argument('--pages')
    ap.add_argument('--themes')
    ap.add_argument('--sections', help='section labels or slugs to (re)run; others keep their last score')
    ap.add_argument('--refs', action='store_true', help='re-export every Figma reference')
    ap.add_argument('--force', action='store_true', help='re-run even when nothing changed')
    ap.add_argument('--only-board', action='store_true')
    ap.add_argument('--compare', nargs=2, metavar=('A', 'B'), help='compare two boards: set or set:refset')
    a = ap.parse_args()
    cfg = L.load_config(a.config)
    if a.compare:
        sys.exit(compare(cfg, *a.compare))
    if not os.path.exists(os.path.join(L.PP_SCRIPTS, 'pixel-diff.mjs')):
        L.die('the pixel-perfect skill is not installed at ~/.claude/skills/pixel-perfect')
    pages = L.select_pages(cfg, a.pages or cfg['score'].get('pages'))
    themes = (a.themes.split(',') if a.themes else None) or cfg['score'].get('themes') or list(cfg['themes'])
    secs = set(a.sections.split(',')) if a.sections else None
    if not a.only_board:
        made = manifests(cfg, a.set, pages, themes, secs)
        if not made:
            L.die(f'no section maps for set "{a.set}" — run capture + map first')
        run(cfg, a.set, made, a.refs, a.force or bool(secs))
        if secs:  # the board needs the full manifests back
            manifests(cfg, a.set, pages, themes, None)
    allp = L.select_pages(cfg, cfg['score'].get('pages'))
    board(cfg, a.set, allp)
    for name, _ in refsets(cfg)[1:]:
        board(cfg, a.set, allp, name)


if __name__ == '__main__':
    main()
