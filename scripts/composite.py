#!/usr/bin/env python3
"""Build Figma | ours composites per mapped section, plus a scaled whole-page overview.

Usage: python3 composite.py -c design-compare.json [--set now] [--pages a,b]
Reads <out>/map/<set>/<page>-<view>.json; writes <out>/comp/<set>/<page>/<view>/
  NN-<label>[-pK].png   Figma crop (left) | 16 px #FF00FF gap | our crop (right), same CSS-px scale;
                        the shorter side is padded with the gap colour, so pink = "this one is shorter".
                        Tall pairs are cut into parts of composite.chunk px.
  00-overview[-pK].png  whole page, both columns scaled to composite.overviewColumn px wide
  index.json            one entry per image with its split geometry, read by build_gallery.py
Transparent Figma exports are flattened on the theme background (themes.<t>.background, else
the frame fill, else white) — pixelmatch and the eye both read alpha against the wrong colour.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dcglib as L  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

Image.MAX_IMAGE_PIXELS = None


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:40] or 'section'


def frame_bg(cfg, theme, fig_meta):
    if cfg['themes'].get(theme, {}).get('background'):
        return L.theme_bg(cfg, theme)
    for f in (fig_meta or {}).get('fills') or []:
        if f.get('type') == 'SOLID' and f.get('visible', True):
            c = f['color']
            return tuple(round(c[k] * 255) for k in 'rgb')
    return (255, 255, 255)


def flat(path, bg, size=None):
    im = Image.open(path).convert('RGBA')
    if size and im.size != size:
        im = im.resize(size, Image.LANCZOS)
    b = Image.new('RGBA', im.size, bg + (255,))
    b.alpha_composite(im)
    return b.convert('RGB')


def pair_image(a, b):
    H = max(a.height, b.height)
    W = a.width + L.GAP + b.width
    c = Image.new('RGB', (W, H), L.SEPARATOR)
    c.paste(a, (0, 0))
    c.paste(b, (a.width + L.GAP, 0))
    d = ImageDraw.Draw(c)
    for y in range(0, H, 100):  # 100 px ticks in the gap, for reading offsets in "side" mode
        d.line([(a.width + 4, y), (a.width + L.GAP - 5, y)], fill=(0, 0, 0))
    return c, a.width, b.width


def save_parts(c, fw, ow, base, chunk, outdir):
    n = max(1, -(-c.height // chunk))
    items = []
    for i in range(n):
        part = c.crop((0, i * chunk, c.width, min(c.height, (i + 1) * chunk)))
        name = base + (f'-p{i + 1}' if n > 1 else '') + '.png'
        L.atomic_image(part, os.path.join(outdir, name), compress_level=6)
        items.append({'file': name, 'part': i + 1, 'parts': n,
                      'g': {'W': part.width, 'H': part.height, 'o': 'h', 'pairs': [[[0, fw], [fw + L.GAP, fw + L.GAP + ow]]]}})
    return items


def build(cfg, page, view, set_, force=False):
    mp = L.out(cfg, 'map', set_, f'{page["key"]}-{view}.json')
    if not os.path.exists(mp):
        return None
    outdir = L.out(cfg, 'comp', set_, page['key'], view)
    key = {'map': digest(mp), 'fig': digest(L.out(cfg, 'fig', page['key'], view, 'sections.json')), 'composite': cfg['composite']}
    if not force and index_key(outdir) == key:
        return 'skip'
    m = json.load(open(mp))
    w, theme = m['width'], m['theme']
    fig_meta = json.load(open(L.out(cfg, 'fig', page['key'], view, 'sections.json')))
    bg = frame_bg(cfg, theme, fig_meta)
    sc = float(m.get('figmaScale') or 1)
    ours = open_capture(cfg, m['capture'])
    os.makedirs(outdir, exist_ok=True)
    clear(outdir)
    chunk = int(cfg['composite']['chunk'])
    index = []
    for p in m['pairs']:
        f = p['figma']
        box = (round(f['w'] * sc), round(f['h'] * sc))
        fim = flat(L.out(cfg, f['file']), bg, box)
        if sc != 1:
            fim = fim.resize((round(f['w']), round(f['h'])), Image.LANCZOS)
        y0, h = (f['crop'] if f['crop'] else (0, f['h']))
        fw = min(fim.width, w + 1)  # Figma mobile frames are often 440.2 wide
        a = fim.crop((0, round(y0), fw, round(y0 + h)))
        oy, oh = p['ours']['y'], p['ours']['h']
        b = ours.crop((0, oy, min(w, ours.width), min(ours.height, oy + oh)))
        c, aw, bw = pair_image(a, b)
        base = f'{p["order"]:02d}-{slug(p["label"])}'
        for it in save_parts(c, aw, bw, base, chunk, outdir):
            it.update(order=p['order'], label=p['label'], figmaId=f['id'], figmaName=f['name'], fh=round(h), oh=oh,
                      crop=f['crop'], dom=p['dom'])
            index.append(it)
    # overview: Figma sections stitched at their frame positions | our full page
    s, ochunk = overview_scale(cfg, w)
    FH = round(m['figmaFrameH'])
    fcan = Image.new('RGB', (w, FH), bg)
    for fs in m['figmaAll']:
        try:
            im = flat(L.out(cfg, fs['file']), bg, (round(fs['w'] * sc), round(fs['h'] * sc)))
        except FileNotFoundError:
            continue
        if sc != 1:
            im = im.resize((round(fs['w']), round(fs['h'])), Image.LANCZOS)
        fcan.paste(im.crop((0, 0, min(im.width, w), im.height)), (0, round(fs['y'])))
    a = fcan.resize((round(w * s), round(FH * s)), Image.LANCZOS)
    b = ours.resize((round(ours.width * s), round(ours.height * s)), Image.LANCZOS)
    c, aw, bw = pair_image(a, b)
    for it in save_parts(c, aw, bw, '00-overview', ochunk, outdir):
        it.update(order=0, label='overview', overview=True, scale=round(s, 4), fh=FH, oh=ours.height)
        index.append(it)
    index.sort(key=lambda x: (x['order'], x['part']))
    # index.json last: its presence + mtime is what marks this view as done
    L.atomic_json({'page': page['key'], 'view': view, 'set': set_, 'dir': L.rel(cfg, outdir), 'key': key, 'items': index}, os.path.join(outdir, 'index.json'))
    L.log(f'{page["key"]} {view} [{set_}]: {len(m["pairs"])} sections, {len(index)} images')
    return index


def overview_scale(cfg, w):
    """Whole-page strip: desktop columns overviewColumn px wide; mobile pages are already narrow,
    so they are shrunk to overviewMobileScale (default 0.4) and cut into taller parts — at 1:1 a
    20000 px mobile page would come out as 14 parts and show nothing the sections do not."""
    col = int(cfg['composite']['overviewColumn'])
    chunk = int(cfg['composite']['overviewChunk'])
    if w < 1000:
        return float(cfg['composite'].get('overviewMobileScale', 0.4)), chunk * 2
    return min(1.0, col / w), chunk


def digest(p):
    import hashlib
    return hashlib.sha1(open(p, 'rb').read()).hexdigest()[:12] if os.path.exists(p) else None


def index_key(outdir):
    try:
        return json.load(open(os.path.join(outdir, 'index.json'))).get('key')
    except Exception:
        return None


def open_capture(cfg, relp):
    p = L.out(cfg, relp)
    if not os.path.exists(p):
        L.die(f'{relp} is gone (pruned?) but its composites are out of date — recapture: dcg.py capture --set <set> --force')
    return Image.open(p).convert('RGB')


def clear(outdir):
    for f in os.listdir(outdir):
        if f.endswith('.png') or f == 'index.json':
            os.remove(os.path.join(outdir, f))


def build_nocomp(cfg, page, view, set_, force=False):
    """A page without a design: our sections only, one image per DOM section (no split)."""
    cp = L.out(cfg, 'cap', set_, f'{page["key"]}-{view}.json')
    if not os.path.exists(cp):
        return None
    outdir = L.out(cfg, 'comp', set_, page['key'], view)
    key = {'cap': digest(cp), 'sections': cfg['sections'], 'composite': cfg['composite']}
    if not force and index_key(outdir) == key:
        return 'skip'
    cap = json.load(open(cp))
    w = cap['width']
    ours = open_capture(cfg, f'cap/{set_}/{cap["file"]}')
    os.makedirs(outdir, exist_ok=True)
    clear(outdir)
    secs = cap['sections']
    hdr = cap.get('header')
    chunk = int(cfg['composite']['chunk'])
    index = []
    for k, s in enumerate(secs):
        y0 = s['y']
        if k == 0 and hdr and cfg['sections'].get('header') and hdr['y'] <= y0:
            y0 = hdr['y']
        y1 = s['y'] + s['h']
        im = ours.crop((0, y0, min(w, ours.width), min(ours.height, y1)))
        n = max(1, -(-im.height // chunk))
        for i in range(n):
            part = im.crop((0, i * chunk, im.width, min(im.height, (i + 1) * chunk)))
            name = f'{k + 1:02d}-{slug(s["label"])}' + (f'-p{i + 1}' if n > 1 else '') + '.png'
            L.atomic_image(part, os.path.join(outdir, name), compress_level=6)
            index.append({'file': name, 'part': i + 1, 'parts': n, 'g': None, 'order': k + 1, 'label': s['label'], 'oh': y1 - y0, 'nocomp': True})
    sc, oc = overview_scale(cfg, w)
    ov = ours.resize((round(ours.width * sc), round(ours.height * sc)), Image.LANCZOS)
    n = max(1, -(-ov.height // oc))
    for i in range(n):
        name = '00-overview' + (f'-p{i + 1}' if n > 1 else '') + '.png'
        L.atomic_image(ov.crop((0, i * oc, ov.width, min(ov.height, (i + 1) * oc))), os.path.join(outdir, name), compress_level=6)
        index.append({'file': name, 'part': i + 1, 'parts': n, 'g': None, 'order': 0, 'label': 'overview', 'overview': True, 'scale': round(sc, 4), 'oh': ours.height, 'nocomp': True})
    index.sort(key=lambda x: (x['order'], x['part']))
    L.atomic_json({'page': page['key'], 'view': view, 'set': set_, 'dir': L.rel(cfg, outdir), 'nocomp': True, 'key': key, 'items': index}, os.path.join(outdir, 'index.json'))
    L.log(f'{page["key"]} {view} [{set_}]: no design — {len(secs)} sections, {len(index)} images')
    return index


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('-c', '--config')
    ap.add_argument('--set', default='now')
    ap.add_argument('--pages')
    ap.add_argument('--force', action='store_true', help='rebuild even when newer than its inputs')
    a = ap.parse_args()
    cfg = L.load_config(a.config)
    done = skipped = 0
    for page in L.select_pages(cfg, a.pages):
        for view in L.views(cfg, page):
            r = (build_nocomp if page.get('nocomp') else build)(cfg, page, view, a.set, a.force)
            if r == 'skip':
                skipped += 1
            elif r:
                done += 1
    L.log(f'composite [{a.set}]: {done} views built, {skipped} up to date (skipped)')


if __name__ == '__main__':
    main()
