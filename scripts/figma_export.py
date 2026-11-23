#!/usr/bin/env python3
"""Export every page frame's top-level sections from Figma over REST.

Usage: python3 figma_export.py -c design-compare.json [--pages a,b] [--views 1440-light] [--refresh]

For each page x view writes <out>/fig/<page>/<view>/:
  sections.json   frame box, file version, one entry per visible top-level child
                  (id, name, type, x/y/w/h relative to the frame, render-bounds bleed, image file)
  NN-<node>.png   that child exported at figma.scale (default 1 = CSS px), box-sized

Why per section and not the whole frame: Figma caps an export at 16384 px per side, so a tall
mobile frame comes back scaled down; per-section exports stay at true size.
Why box-sized: Figma exports a node at its render bounds (shadows, ribbons that paint outside
the frame), so the PNG is larger and offset from the box the DOM section corresponds to. Nodes
whose export does not match box x scale are re-exported with use_absolute_bounds=true.
Mobile frames often wrap all sections in one full-height frame; the script descends through a
single child that covers >= 90% of the parent height (override: page.figmaContainer[view]).
The token comes from FIGMA_TOKEN or figma.token and is never printed.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dcglib as L  # noqa: E402
from PIL import Image  # noqa: E402

BATCH = 6


def fetch_node(cfg, nid, depth=2):
    d = L.figma_get(cfg, f'/v1/files/{cfg["figma"]["fileKey"]}/nodes', {'ids': nid, 'depth': depth, 'geometry': 'omit'})
    n = (d.get('nodes') or {}).get(nid)
    if not n or not n.get('document'):
        L.die(f'Figma node {nid} not found in file {cfg["figma"]["fileKey"]}')
    return n['document'], d.get('version'), d.get('lastModified')


def visible(n):
    return n.get('visible', True) and n.get('opacity', 1) != 0


def container(cfg, page, view, root):
    """Descend through single full-height wrappers to the node whose children are the sections."""
    forced = (page.get('figmaContainer') or {}).get(view)
    if forced:
        node, _, _ = fetch_node(cfg, L.norm_node(forced))
        return node
    node = root
    for _ in range(4):
        kids = [c for c in node.get('children') or [] if visible(c)]
        H = (node.get('absoluteBoundingBox') or {}).get('height') or 1
        if len(kids) == 1 and kids[0].get('children') is not None and (kids[0].get('absoluteBoundingBox') or {}).get('height', 0) >= 0.9 * H:
            node, _, _ = fetch_node(cfg, kids[0]['id'])
            continue
        if len(kids) == 1 and 'children' not in kids[0] and kids[0].get('type') in ('FRAME', 'GROUP', 'INSTANCE', 'COMPONENT', 'SECTION'):
            node, _, _ = fetch_node(cfg, kids[0]['id'])
            continue
        break
    return node


def export_images(cfg, ids, scale, absolute=False):
    """Render node ids in batches. Figma answers 500/503 when a batch takes too long to render
    (tall mobile sections, heavy blurs), so a failing batch is split in half and retried."""
    urls = {}
    queue = [ids[i:i + BATCH] for i in range(0, len(ids), BATCH)]
    done = 0
    while queue:
        chunk = queue.pop(0)
        params = {'ids': ','.join(chunk), 'scale': str(scale), 'format': 'png'}
        if absolute:
            params['use_absolute_bounds'] = 'true'
        L.log(f'  rendering {len(chunk)} node(s), {done}/{len(ids)} done' + (' (box size)' if absolute else ''))
        try:
            d = L.figma_get(cfg, f'/v1/images/{cfg["figma"]["fileKey"]}', params, soft=len(chunk) > 1)
        except L.FigmaRenderError as e:
            half = len(chunk) // 2
            L.log(f'  {e} on a batch of {len(chunk)} — splitting')
            queue[:0] = [chunk[:half], chunk[half:]]
            continue
        if d.get('err'):
            L.die(f'Figma images error: {d["err"]}')
        urls.update(d.get('images') or {})
        done += len(chunk)
    return urls


def one(cfg, page, view, refresh):
    nid = page['frames'][view]
    d = L.out(cfg, 'fig', page['key'], view)
    meta_p = os.path.join(d, 'sections.json')
    if os.path.exists(meta_p) and not refresh:
        m = json.load(open(meta_p))
        if m.get('frame') == nid and all(os.path.exists(os.path.join(d, s['file'])) for s in m['sections']):
            L.log(f'{page["key"]} {view}: cached ({len(m["sections"])} sections) — --refresh to re-export')
            return m
    os.makedirs(d, exist_ok=True)
    scale = float(cfg['figma'].get('scale', 1))
    root, version, modified = fetch_node(cfg, nid)
    fb = root['absoluteBoundingBox']
    cont = container(cfg, page, view, root)
    secs = []
    for i, c in enumerate([c for c in cont.get('children') or [] if visible(c)]):
        b = c.get('absoluteBoundingBox')
        if not b or b['height'] < 1:
            continue
        rb = c.get('absoluteRenderBounds') or b
        secs.append({
            'index': len(secs), 'id': c['id'], 'name': c.get('name', ''), 'type': c.get('type'),
            'x': round(b['x'] - fb['x'], 2), 'y': round(b['y'] - fb['y'], 2), 'w': round(b['width'], 2), 'h': round(b['height'], 2),
            'bleed': [round(b['x'] - rb['x'], 2), round(b['y'] - rb['y'], 2), round(rb['width'] - b['width'], 2), round(rb['height'] - b['height'], 2)],
            'file': f'{len(secs) + 1:02d}-{L.nid_file(c["id"])}.png',
        })
    if not secs:
        L.die(f'{page["key"]} {view}: frame {nid} has no visible children — set page.figmaContainer["{view}"]')
    ids = [s['id'] for s in secs]
    urls = export_images(cfg, ids, scale)
    redo = []
    for s in secs:
        u = urls.get(s['id'])
        if not u:
            L.log(f'  no render for {s["id"]} "{s["name"]}" (empty or hidden node)')
            s['missing'] = True
            continue
        p = os.path.join(d, s['file'])
        L.download(u, p)
        W, H = Image.open(p).size
        ew, eh = round(s['w'] * scale), round(s['h'] * scale)
        s['exported'] = [W, H]
        if abs(W - ew) > 2 or abs(H - eh) > 2:
            redo.append(s)
    if redo:
        urls = export_images(cfg, [s['id'] for s in redo], scale, absolute=True)
        for s in redo:
            u = urls.get(s['id'])
            if not u:
                continue
            p = os.path.join(d, s['file'])
            L.download(u, p)
            s['absoluteBounds'] = list(Image.open(p).size)
    for s in secs:
        if s.get('missing'):
            continue
        W, H = Image.open(os.path.join(d, s['file'])).size
        ew, eh = round(s['w'] * scale), round(s['h'] * scale)
        if abs(W - ew) > 2 or abs(H - eh) > 2:
            # Still off: the 16384 px cap scaled it down, or bounds Figma would not honour.
            s['resizedFrom'] = [W, H]
    m = {'page': page['key'], 'view': view, 'frame': nid, 'frameName': root.get('name'), 'container': cont['id'],
         'frameBox': {'w': round(fb['width'], 2), 'h': round(fb['height'], 2)}, 'scale': scale,
         'version': version, 'lastModified': modified, 'fills': root.get('fills'), 'background': root.get('backgroundColor'),
         'sections': secs}
    L.atomic_json(m, meta_p)  # written last: an interrupted view is simply redone next run
    L.log(f'{page["key"]} {view}: {len(secs)} sections from "{root.get("name")}" ({len(redo)} re-exported at box size'
          + (f', {sum(1 for s in secs if s.get("resizedFrom"))} still off-size' if any(s.get('resizedFrom') for s in secs) else '') + ')')
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('-c', '--config')
    ap.add_argument('--pages')
    ap.add_argument('--views')
    ap.add_argument('--refresh', action='store_true')
    a = ap.parse_args()
    cfg = L.load_config(a.config)
    want = set(a.views.split(',')) if a.views else None
    for page in L.comp_pages(cfg, L.select_pages(cfg, a.pages)):
        for view in L.views(cfg, page):
            if want and view not in want:
                continue
            one(cfg, page, view, a.refresh)


if __name__ == '__main__':
    main()
