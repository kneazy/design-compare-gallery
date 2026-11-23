#!/usr/bin/env python3
"""Build <out>/index.html: the Figma-vs-implementation gallery.

Usage: python3 build_gallery.py -c design-compare.json [--sets before,now] [--title "..."]
Sets are capture sets under <out>/comp/<set>/ (oldest first). With two or more, the gallery shows
a set toggle (e.g. "Before fixes" / "Now") that swaps every picture; each set shows the build it
was captured from (cap/<set>/_set.json). Default: every set that has composites, ordered by
config "sets" (key -> label) then by age.

Also picked up when present:
  <out>/findings.json  [{"id": "A1", "text": "...", "severity": "high|medium|low|question|info",
                         "img": "comp/now/<page>/<view>/03-x.png"   (or any image under <out>), or
                         "crop": {"set": "now", "page": "p", "view": "1440-dark", "section": 3 | "label",
                                  "x": 380, "y": 540, "w": 520, "h": 330, "zoom": 2}}]
                       -> a "Findings" tab; comp/<set>/ images and crops are made for every set.
  <out>/score/<set>/scoreboard[--<refset>].json  -> "Pixel-perfect" tab (selector per reference set)
  <out>/regress/<id>/report.json                 -> "Regression A vs B" tab
Every image URL carries ?v=<build time> so a browser never shows a stale picture; open the
printed URL (with ?v=) after each rebuild.
Composites made by other tools are accepted too: without an index.json entry the Figma | ours
split is found by scanning for the #FF00FF separator column.
"""
import argparse
import html
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dcglib as L  # noqa: E402
from PIL import Image  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
GEO = {}


def detect(cfg, relp):
    """Find the separator band in a composite made elsewhere."""
    if relp in GEO:
        return GEO[relp]
    im = Image.open(L.out(cfg, relp)).convert('RGB')
    g = detect_image(im)
    GEO[relp] = g
    return g


def detect_image(im):
    W, H = im.size
    rows = [min(H - 1, int(H * f)) for f in (0.02, 0.15, 0.3, 0.5, 0.7, 0.85, 0.98)]
    sep = []
    for x in range(W):
        hits = sum(1 for y in rows if all(abs(a - b) < 24 for a, b in zip(im.getpixel((x, y)), L.SEPARATOR)))
        if hits >= len(rows) - 1:
            sep.append(x)
    runs = []
    for x in sep:
        if runs and x == runs[-1][1] + 1:
            runs[-1][1] = x
        else:
            runs.append([x, x])
    runs = [r for r in runs if r[0] > 20 and r[1] < W - 20 and r[1] - r[0] >= 3]
    g = {'W': W, 'H': H, 'o': 'h', 'pairs': []}
    if runs:
        r = runs[0]
        g['pairs'] = [[[0, r[0]], [r[1] + 1, W]]]
    return g


def sets_present(cfg):
    base = L.out(cfg, 'comp')
    have = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)) and not d.startswith('.')] if os.path.isdir(base) else []
    order = list(cfg.get('sets') or {})
    return sorted(have, key=lambda s: (order.index(s) if s in order else len(order), L.mtime(os.path.join(base, s))))


def section_composite(cfg, set_, page, view, section):
    """Re-assemble a section's composite (all parts) and its geometry."""
    ip = L.out(cfg, 'comp', set_, page, view, 'index.json')
    if not os.path.exists(ip):
        return None, None
    idx = json.load(open(ip))
    items = [it for it in idx['items'] if not it.get('overview') and
             (it['order'] == section if isinstance(section, int) else it['label'] == section)]
    if not items or not items[0].get('g'):
        return None, None
    ims = [Image.open(L.out(cfg, idx['dir'], it['file'])).convert('RGB') for it in sorted(items, key=lambda x: x['part'])]
    W = ims[0].width
    c = Image.new('RGB', (W, sum(i.height for i in ims)))
    y = 0
    for i in ims:
        c.paste(i, (0, y))
        y += i.height
    return c, items[0]['g']['pairs'][0]


def evidence(cfg, fid, crop, sets):
    """Figma | ours crop of one region of a section composite, zoomed (default 2x, nearest
    neighbour so pixels stay pixels). Made for every set that has the section."""
    by = {}
    z = int(crop.get('zoom', 2))
    for s in sets:
        c, pair = section_composite(cfg, s, crop['page'], crop['view'], crop['section'])
        if c is None:
            continue
        (f0, _), (o0, _) = pair
        x, y, w, h = crop['x'], crop['y'], crop['w'], crop['h']
        a = c.crop((f0 + x, y, f0 + x + w, min(c.height, y + h)))
        b = c.crop((o0 + x, y, o0 + x + w, min(c.height, y + h)))
        a, b = a.resize((a.width * z, a.height * z), Image.NEAREST), b.resize((b.width * z, b.height * z), Image.NEAREST)
        out = Image.new('RGB', (a.width + L.GAP + b.width, max(a.height, b.height)), L.SEPARATOR)
        out.paste(a, (0, 0))
        out.paste(b, (a.width + L.GAP, 0))
        dst = L.out(cfg, 'evidence', f'{re.sub(r"[^A-Za-z0-9_-]+", "_", fid)}-{s}.png')
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        L.atomic_image(out, dst)
        by[s] = {'src': L.rel(cfg, dst), 'g': {'W': out.width, 'H': out.height, 'o': 'h', 'pairs': [[[0, a.width], [a.width + L.GAP, out.width]]]},
                 'width': min(1440, out.width // 2)}
    return by


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('-c', '--config')
    ap.add_argument('--sets')
    ap.add_argument('--title')
    ap.add_argument('--out-file', default='index.html')
    a = ap.parse_args()
    cfg = L.load_config(a.config)
    sets = a.sets.split(',') if a.sets else sets_present(cfg)
    if not sets:
        L.die('no composites under <out>/comp/ — run composite first')
    pages = {}
    for page in cfg['pages']:
        pv, warn = {}, {}
        for view in L.views(cfg, page):
            merged, meta = {}, {}
            w, _ = L.parse_view(view)
            for s in sets:
                ip = L.out(cfg, 'comp', s, page['key'], view, 'index.json')
                if not os.path.exists(ip):
                    continue
                idx = json.load(open(ip))
                for it in idx['items']:
                    k = (it['order'], it['part'])
                    merged.setdefault(k, {})[s] = {'src': f'{idx["dir"]}/{it["file"]}', 'g': it.get('g'), 'fh': it.get('fh'), 'oh': it.get('oh'),
                                                   'part': it['part'], 'parts': it['parts'], 'scale': it.get('scale'), 'width': w}
                    meta[k] = it
                mp = L.out(cfg, 'map', s, f'{page["key"]}-{view}.json')
                if not page.get('nocomp') and os.path.exists(mp) and s == sets[-1]:
                    m = json.load(open(mp))
                    figs = {p['figma']['id'] for p in m['pairs']}
                    fsec = json.load(open(L.out(cfg, 'fig', page['key'], view, 'sections.json')))['sections']
                    skip = re.compile(cfg['sections'].get('figmaSkip') or r'(?!)')
                    uf = [f['name'] for f in fsec if f['id'] not in figs and not skip.search(f['name']) and not f.get('missing')]
                    if m.get('unpairedDom') or uf:
                        warn[view] = html.escape(f'Not paired — DOM: {m.get("unpairedDom") or "-"} · Figma: {uf or "-"}')
            if not merged:
                continue
            pv[view] = [{'order': meta[k]['order'], 'part': meta[k]['part'], 'label': meta[k]['label'], 'overview': bool(meta[k].get('overview')),
                         'figmaId': meta[k].get('figmaId'), 'figmaName': meta[k].get('figmaName'), 'bySet': merged[k]} for k in sorted(merged)]
        pages[page['key']] = {'title': page['title'], 'url': page['route'], 'views': pv, 'warn': warn, 'nocomp': bool(page.get('nocomp'))}

    vlist = []
    for v in L.views(cfg):
        w, t = L.parse_view(v)
        vlist.append({'key': v, 'width': w, 'theme': t, 'themeLabel': cfg['themes'].get(t, {}).get('label', t)})

    find = []
    fp = L.out(cfg, 'findings.json')
    if os.path.exists(fp):
        for f in json.load(open(fp, encoding='utf-8')):
            by = {}
            if f.get('crop'):
                by = evidence(cfg, f.get('id', 'x'), f['crop'], sets)
            elif f.get('img'):
                img = f['img']
                m = re.match(r'comp/([^/]+)/(.*)$', img)
                for s, p in ([(s, f'comp/{s}/{m[2]}') for s in sets] if m else [(sets[-1], img)]):
                    if not os.path.exists(L.out(cfg, p)):
                        continue
                    g = None
                    ip = os.path.join(os.path.dirname(L.out(cfg, p)), 'index.json')
                    if os.path.exists(ip):
                        g = next((it['g'] for it in json.load(open(ip))['items'] if it['file'] == os.path.basename(p)), None)
                    if g is None:
                        g = detect(cfg, p)
                    by[s] = {'src': p, 'g': g, 'width': 1440 if g['W'] > 1600 else g['W'] // 2}
            if by:
                find.append({'id': f.get('id', ''), 'text': html.escape(f['text']), 'severity': f.get('severity'), 'sevLabel': f.get('severityLabel'),
                             'page': html.escape(f.get('page', '')), 'bySet': by})
            else:
                L.log(f'  finding {f.get("id")}: no image (img / crop not found)')

    score = {}
    rorder = list((cfg['score'].get('refSets') or {'figma': ''}).keys())
    for s in sets:
        d = L.out(cfg, 'score', s)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            m = re.fullmatch(r'scoreboard(?:--(.+))?\.json', f)
            if m:
                b = json.load(open(os.path.join(d, f)))
                score.setdefault(s, {})[m[1] or b.get('refset') or 'figma'] = b
        if s in score:  # primary reference set first, then config order
            score[s] = dict(sorted(score[s].items(), key=lambda kv: rorder.index(kv[0]) if kv[0] in rorder else len(rorder)))

    regress = []
    rdir = L.out(cfg, 'regress')
    if os.path.isdir(rdir):
        for rid in sorted(os.listdir(rdir), key=lambda x: -L.mtime(os.path.join(rdir, x))):
            rp = os.path.join(rdir, rid, 'report.json')
            if os.path.exists(rp):
                regress.append(json.load(open(rp)))

    version = time.strftime('%Y%m%d%H%M%S')
    setinfo = []
    for s in sets:
        meta = L.set_meta(cfg, s)
        setinfo.append({'key': s, 'label': (cfg.get('sets') or {}).get(s, s), 'build': L.build_label(meta), 'baseUrl': (meta or {}).get('baseUrl')})
    data = {'title': a.title or cfg.get('title') or cfg.get('name') or 'Figma vs implementation', 'lang': cfg.get('lang', 'en'),
            'subtitle': cfg.get('subtitle', ''), 'storageKey': cfg.get('name') or cfg['_path'], 'version': version,
            'built': time.strftime('%Y-%m-%d %H:%M'), 'sets': setinfo, 'views': vlist, 'pages': pages,
            'find': find, 'findNote': cfg.get('findNote'), 'score': score, 'regress': regress[:5], 'i18n': cfg.get('i18n') or {}}
    tpl = open(os.path.join(L.ASSETS, 'template.html'), encoding='utf-8').read()
    js = 'const DATA=' + json.dumps(data, ensure_ascii=False).replace('</', '<\\/') + ';'
    L.atomic_text(tpl.replace('/*DATA*/', js), L.out(cfg, a.out_file))
    L.atomic_json({'version': version, 'built': data['built'], 'sets': setinfo}, L.out(cfg, '.gallery.json'))
    n = sum(len(v) for p in pages.values() for v in p['views'].values())
    sl = ', '.join('%s (%s)' % (x['key'], x['build']) for x in setinfo)
    sc = {k: list(v) for k, v in score.items()} or 'none'
    L.log(f'gallery: {L.out(cfg, a.out_file)} — {len(pages)} pages, {n} images, sets {sl}, '
          f'{len(find)} findings, score {sc}, {len(regress)} regress report(s)')
    L.log(f'serve:   python3 -m http.server 8766 --bind 127.0.0.1 --directory {cfg["_out"]}')
    L.log(f'open:    http://127.0.0.1:8766/{a.out_file}?v={version}')


if __name__ == '__main__':
    main()
