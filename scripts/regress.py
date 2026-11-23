#!/usr/bin/env python3
"""Strict build-vs-build comparison: is B exactly A? (release check, CMS migration, rollback).

Usage: python3 regress.py -c design-compare.json --a <urlA> --b <urlB>
                          [--pages p,q] [--widths 1440,390] [--themes light] [--id name] [--keep]

Captures every selected page x width x theme from A and from B with the same freezing as the
gallery capture (pages without a Figma frame included), then compares:
  - pixels, exactly: threshold 0, no anti-aliasing forgiveness — any channel of any pixel that
    differs counts. Full page and per DOM section (sections whose box moved are reported as such).
  - <head>: title, meta description, canonical, robots, hreflang, og:/twitter:/article: metas and
    every JSON-LD block (parsed, keys sorted, so formatting does not count).
  - HTTP status, console errors (minus capture.noise), broken images, failed requests.
Masks: capture.hide + regress.mask selectors are hidden in both captures; the report lists them,
because a masked region is a region that was NOT compared.
Output: <out>/regress/<id>/report.json + report.md + diff/<page>-<view>.png (A | B | changed pixels
in red, cropped to the changed area), shown in the gallery as a "Regression A vs B" tab after
`dcg.py gallery`. Full-page captures are deleted afterwards unless --keep / regress.keepCaptures
(disk). Exit 0 only when everything is identical.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dcglib as L  # noqa: E402
from PIL import Image, ImageChops, ImageDraw  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
PAD = 40
MAX_CROP_H = 2400


def changed_mask(a, b):
    """1-bit-ish L image: 255 where any RGB channel differs (L conversion alone would round a
    1-level difference in one channel away)."""
    d = ImageChops.difference(a, b)
    r, g, bb = d.split()
    m = ImageChops.lighter(ImageChops.lighter(r, g), bb)
    return m.point(lambda v: 255 if v else 0)


def count(mask):
    return mask.histogram()[255]


def compare_images(pa, pb):
    a = Image.open(pa).convert('RGB')
    b = Image.open(pb).convert('RGB')
    res = {'sizeA': list(a.size), 'sizeB': list(b.size)}
    W, H = min(a.width, b.width), min(a.height, b.height)
    ac, bc = a.crop((0, 0, W, H)), b.crop((0, 0, W, H))
    m = changed_mask(ac, bc)
    res['changed'] = count(m)
    res['total'] = W * H
    res['bbox'] = list(m.getbbox()) if res['changed'] else None
    res['sameSize'] = a.size == b.size
    return a, b, m, res


def section_diffs(ja, jb, m):
    out = []
    bl = {s['label']: s for s in jb.get('sections', [])}
    for s in ja.get('sections', []):
        t = bl.get(s['label'])
        e = {'label': s['label'], 'boxA': [s['y'], s['h']]}
        if not t:
            e.update(status='missing in B')
        else:
            e['boxB'] = [t['y'], t['h']]
            if (s['y'], s['h']) != (t['y'], t['h']):
                e['status'] = 'moved/resized'
            y0, y1 = s['y'], min(s['y'] + s['h'], m.height)
            n = count(m.crop((0, y0, m.width, y1))) if y1 > y0 else 0
            e['changed'] = n
            e.setdefault('status', 'changed' if n else 'identical')
        out.append(e)
    for lab in bl:
        if lab not in {s['label'] for s in ja.get('sections', [])}:
            out.append({'label': lab, 'status': 'only in B'})
    return out


def triptych(a, b, m, bbox, dst):
    x0, y0, x1, y1 = bbox
    x0, y0 = max(0, x0 - PAD), max(0, y0 - PAD)
    x1, y1 = min(m.width, x1 + PAD), min(m.height, y1 + PAD, y0 + MAX_CROP_H)
    ca, cb, cm = a.crop((x0, y0, x1, y1)), b.crop((x0, y0, x1, y1)), m.crop((x0, y0, x1, y1))
    hi = Image.blend(ca, Image.new('RGB', ca.size, (255, 255, 255)), 0.75)
    hi.paste(Image.new('RGB', ca.size, (255, 0, 0)), (0, 0), cm)
    w, h = ca.size
    c = Image.new('RGB', (3 * w + 2 * L.GAP, h + 22), L.SEPARATOR)
    d = ImageDraw.Draw(c)
    for i, (im, t) in enumerate(((ca, 'A'), (cb, 'B'), (hi, 'changed px (red)'))):
        c.paste(im, (i * (w + L.GAP), 22))
        d.text((i * (w + L.GAP) + 4, 5), f'{t}  crop x{x0}-{x1} y{y0}-{y1}', fill=(255, 255, 255))
    L.atomic_image(c, dst, compress_level=6)


def head_diff(ha, hb):
    out = []
    for k in sorted(set(ha or {}) | set(hb or {})):
        va, vb = (ha or {}).get(k), (hb or {}).get(k)
        if k == 'meta':
            for mk in sorted(set(va or {}) | set(vb or {})):
                if (va or {}).get(mk) != (vb or {}).get(mk):
                    out.append({'field': f'meta {mk}', 'a': (va or {}).get(mk), 'b': (vb or {}).get(mk)})
        elif k == 'jsonld':
            la, lb = va or [], vb or []
            if la != lb:
                for i in range(max(len(la), len(lb))):
                    x, y = (la[i] if i < len(la) else None), (lb[i] if i < len(lb) else None)
                    if x != y:
                        out.append({'field': f'JSON-LD #{i + 1}', 'a': (x or '')[:400], 'b': (y or '')[:400]})
        elif va != vb:
            out.append({'field': k, 'a': va, 'b': vb})
    return out


def errs(j, cfg):
    return sorted({c['text'] for c in j.get('console', []) if not c.get('noise')})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('-c', '--config')
    ap.add_argument('--a', required=True)
    ap.add_argument('--b', required=True)
    ap.add_argument('--pages')
    ap.add_argument('--widths')
    ap.add_argument('--themes')
    ap.add_argument('--id')
    ap.add_argument('--keep', action='store_true', help='keep the full-page captures')
    a = ap.parse_args()
    cfg = L.load_config(a.config)
    pages = L.select_pages(cfg, a.pages)
    widths = [int(w) for w in a.widths.split(',')] if a.widths else cfg['widths']
    themes = a.themes.split(',') if a.themes else list(cfg['themes'])
    views = [f'{w}-{t}' for w in widths for t in themes]
    rid = a.id or time.strftime('%Y%m%d-%H%M%S')
    rd = L.out(cfg, 'regress', rid)
    os.makedirs(os.path.join(rd, 'diff'), exist_ok=True)
    L.ensure_disk(cfg, 2 * len(pages) * len(views), 'regress', per_view_mb=8)
    mask = list(cfg['capture'].get('hide') or []) + list(cfg['regress'].get('mask') or [])
    for side, url in (('a', a.a), ('b', a.b)):
        L.log(f'regress {rid}: capturing {side.upper()} = {url} ({len(pages)} pages x {len(views)} views)')
        rc = L.run_locked(cfg, [L.node_bin(), os.path.join(L.SCRIPTS, 'capture.mjs'), cfg['_path'], f'regress-{side}',
                                ','.join(p['key'] for p in pages), ','.join(views)],
                          env={'DCG_OUT_DIR': os.path.join(rd, side), 'DCG_BASE_URL': url, 'DCG_ALL_VIEWS': '1',
                               'DCG_EXTRA_HIDE': json.dumps(cfg['regress'].get('mask') or []), 'DCG_SKIP_EXISTING': '1'})
        if rc:
            L.log(f'  capture {side.upper()} exit {rc} (failures are reported below)')
    rows = []
    identical = True
    for p in pages:
        for v in views:
            base = f'{p["key"]}-{v}'
            row = {'page': p['key'], 'title': p['title'], 'route': p['route'], 'view': v}
            j = {}
            for side in 'ab':
                jp = os.path.join(rd, side, base + '.json')
                sp = os.path.join(rd, side, base + '.status.json')
                j[side] = json.load(open(jp)) if os.path.exists(jp) else (json.load(open(sp)) if os.path.exists(sp) else {'error': 'not captured'})
            row['statusA'], row['statusB'] = j['a'].get('status'), j['b'].get('status')
            row['errorA'], row['errorB'] = j['a'].get('error'), j['b'].get('error')
            pa, pb = os.path.join(rd, 'a', base + '.png'), os.path.join(rd, 'b', base + '.png')
            if os.path.exists(pa) and os.path.exists(pb):
                A, B, m, r = compare_images(pa, pb)
                row.update(r)
                row['sections'] = section_diffs(j['a'], j['b'], m)
                if r['changed'] or not r['sameSize']:
                    if r['bbox']:
                        triptych(A, B, m, r['bbox'], os.path.join(rd, 'diff', base + '.png'))
                        row['diffImage'] = L.rel(cfg, os.path.join(rd, 'diff', base + '.png'))
            else:
                row['changed'] = None
            row['head'] = head_diff(j['a'].get('head'), j['b'].get('head'))
            row['consoleA'], row['consoleB'] = errs(j['a'], cfg), errs(j['b'], cfg)
            row['brokenA'], row['brokenB'] = j['a'].get('brokenImages', []), j['b'].get('brokenImages', [])
            row['failedA'] = sorted({f['s'] for f in j['a'].get('failed', []) if not f.get('noise')})
            row['failedB'] = sorted({f['s'] for f in j['b'].get('failed', []) if not f.get('noise')})
            same = (row.get('changed') == 0 and row.get('sameSize') and not row['head'] and row['statusA'] == row['statusB']
                    and row['consoleA'] == row['consoleB'] and row['brokenA'] == row['brokenB'] and row['failedA'] == row['failedB'])
            row['identical'] = bool(same)
            identical &= bool(same)
            rows.append(row)
            px = '—' if row.get('changed') is None else f'{row["changed"]} px' + ('' if row.get('sameSize') else f' (size {row["sizeA"]} vs {row["sizeB"]})')
            L.log(f'  {base:36s} {"IDENTICAL" if same else "DIFFERS":9s} status {row["statusA"]}/{row["statusB"]}  pixels {px}'
                  f'  head {len(row["head"])} diff(s)  console {len(row["consoleA"])}/{len(row["consoleB"])}')
    rep = {'id': rid, 'a': a.a, 'b': a.b, 'generated': time.strftime('%Y-%m-%d %H:%M'), 'identical': identical,
           'rules': 'pixels: threshold 0, any channel difference counts; head: normalised; masks hide regions from both sides',
           'masked': mask, 'videos': cfg['capture'].get('videos', 'hide'), 'fixedTime': cfg['capture'].get('fixedTime'),
           'pages': [p['key'] for p in pages], 'views': views, 'rows': rows}
    L.atomic_json(rep, os.path.join(rd, 'report.json'))
    md = [f'# Regression {rid}: A {a.a} vs B {a.b}', '', f'Generated {rep["generated"]}. Result: **{"IDENTICAL" if identical else "DIFFERENT"}**.', '',
          f'Rules: {rep["rules"]}. Masked (not compared): {", ".join(mask) or "nothing"}; videos: {rep["videos"]}.', '',
          '| Page · view | Status A/B | Changed px | Size | Head | Console A/B | Broken img A/B |', '|---|---|---|---|---|---|---|']
    for r in rows:
        md.append(f'| {r["page"]} · {r["view"]} | {r["statusA"]}/{r["statusB"]} | {r.get("changed")} | {'same' if r.get('sameSize') else str(r.get('sizeA')) + ' vs ' + str(r.get('sizeB'))} '
                  f'| {len(r["head"])} | {len(r["consoleA"])}/{len(r["consoleB"])} | {len(r["brokenA"])}/{len(r["brokenB"])} |')
    for r in rows:
        if r['head']:
            md += ['', f'### {r["page"]} · {r["view"]} — head', ''] + [f'- {h["field"]}: `{h["a"]}` → `{h["b"]}`' for h in r['head']]
        ch = [s for s in r.get('sections', []) if s['status'] != 'identical']
        if ch:
            md += ['', f'### {r["page"]} · {r["view"]} — sections', ''] + [f'- {s["label"]}: {s["status"]} {s.get("changed", "")}' for s in ch]
    L.atomic_text('\n'.join(md) + '\n', os.path.join(rd, 'report.md'))
    if not (a.keep or cfg['regress'].get('keepCaptures')):
        for side in 'ab':
            for f in os.listdir(os.path.join(rd, side)):
                if f.endswith('.png'):
                    os.remove(os.path.join(rd, side, f))
    L.log(f'regress {rid}: {"IDENTICAL — 0 changed pixels, same head/status/console" if identical else "DIFFERENT"}'
          f' ({sum(1 for r in rows if r["identical"])}/{len(rows)} page views identical). Masked: {mask or "nothing"}')
    L.log(f'  report: {os.path.join(rd, "report.md")}  (dcg.py gallery adds the "Regression" tab)')
    sys.exit(0 if identical else 1)


if __name__ == '__main__':
    main()
