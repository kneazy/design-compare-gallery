#!/usr/bin/env python3
"""Pair DOM sections with Figma top-level sections, by order, with per-page overrides.

Usage: python3 map_sections.py -c design-compare.json [--set now] [--pages a,b]
Reads  <out>/fig/<page>/<view>/sections.json and <out>/cap/<set>/<page>-<view>.json
Writes <out>/map/<set>/<page>-<view>.json and prints one table per view:
  #  DOM label  (ours h)  <->  Figma name (id, h)  delta-h
A delta over 25% (or a count mismatch) is flagged: that is usually a mapping problem, not drift.

Overrides live in page.map, keyed by a view spec ("*", "1440", "dark", "1440-dark"):
  "skipDom":   {"*": ["cookie-bar"]}                    DOM labels with no Figma counterpart
  "skipFigma": {"*": ["Footer", "123:456"]}             Figma names / ids (figmaSkip regex applies too)
  "merge":     {"440": [["offerings", "tech-stack"]]}   consecutive DOM sections drawn as one Figma frame
  "split":     {"1440": {"9225:237282": [[0, 1442], [1442, 644]]}}
               one Figma frame drawn as several DOM sections: crops [y, h] in CSS px
  "pairs":     {"440-dark": [{"dom": ["hero"], "figma": "1:2", "crop": [0, 900]}, ...]}
               full manual list for that view (everything else ignored)
The first DOM section absorbs the site header (sections.header) when the Figma hero frame
draws the navigation bar inside it — the usual case for marketing pages.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dcglib as L  # noqa: E402


def ov(page, kind, view):
    """Union of the override lists whose spec matches this view (most specific last)."""
    spec = (page.get('map') or {}).get(kind) or {}
    order = sorted(spec, key=lambda k: (k != '*', '-' in k))
    if kind == 'split':
        outd = {}
        for k in order:
            if L.view_match(k, view):
                outd.update({L.norm_node(a): b for a, b in spec[k].items()})
        return outd
    if kind == 'pairs':
        hit = [spec[k] for k in order if L.view_match(k, view)]
        return hit[-1] if hit else None
    res = []
    for k in order:
        if L.view_match(k, view):
            res += spec[k]
    return res


def build(cfg, page, view, set_, force=False):
    w, theme = L.parse_view(view)
    fig_p = L.out(cfg, 'fig', page['key'], view, 'sections.json')
    cap_p = L.out(cfg, 'cap', set_, f'{page["key"]}-{view}.json')
    out_p = L.out(cfg, 'map', set_, f'{page["key"]}-{view}.json')
    if not force and L.fresh([out_p], [fig_p, cap_p, cfg['_path']]):
        return 'skip'
    if not os.path.exists(fig_p):
        L.log(f'{page["key"]} {view}: no Figma export (run figma first)')
        return None
    if not os.path.exists(cap_p):
        L.log(f'{page["key"]} {view}: no capture in set "{set_}"')
        return None
    fig = json.load(open(fig_p))
    cap = json.load(open(cap_p))
    skip_re = re.compile(cfg['sections'].get('figmaSkip') or r'(?!)')
    skipF = set(ov(page, 'skipFigma', view))
    fsecs = [s for s in fig['sections'] if not s.get('missing') and not skip_re.search(s['name'])
             and s['id'] not in skipF and s['name'] not in skipF]
    skipD = set(ov(page, 'skipDom', view))
    dsecs = [s for s in cap['sections'] if s['label'] not in skipD]
    header = cap.get('header')
    if dsecs and header and cfg['sections'].get('header') and header['y'] < dsecs[0]['y']:
        # only the part of the header ABOVE the first section is added: many sites lay the header
        # over the hero, which then already contains it
        ext = dsecs[0]['y'] - header['y']
        dsecs[0] = dict(dsecs[0], h=dsecs[0]['y'] + dsecs[0]['h'] - header['y'], y=header['y'], selector=None, withHeader=True, headerExt=ext)

    def figref(s, crop=None):
        return {'id': s['id'], 'name': s['name'], 'file': f'fig/{page["key"]}/{view}/{s["file"]}', 'w': s['w'], 'h': s['h'],
                'crop': crop, 'bleed': s.get('bleed'), 'absoluteBounds': bool(s.get('absoluteBounds'))}

    manual = ov(page, 'pairs', view)
    units = []  # list of DOM groups
    if manual:
        byl = {s['label']: s for s in cap['sections']}
        byf = {s['id']: s for s in fig['sections']}
        pairs = []
        for m in manual:
            ds = [byl[x] for x in m['dom']]
            f = byf[L.norm_node(m['figma'])]
            pairs.append((ds, figref(f, m.get('crop'))))
    else:
        merges = ov(page, 'merge', view)
        i = 0
        while i < len(dsecs):
            grp = [dsecs[i]]
            for mg in merges:
                if mg and mg[0] == dsecs[i]['label'] and [s['label'] for s in dsecs[i:i + len(mg)]] == mg:
                    grp = dsecs[i:i + len(mg)]
                    break
            units.append(grp)
            i += len(grp)
        splits = ov(page, 'split', view)
        frefs = []
        for s in fsecs:
            if s['id'] in splits:
                frefs += [figref(s, c) for c in splits[s['id']]]
            else:
                frefs.append(figref(s))
        pairs = list(zip(units, frefs))
        if len(units) != len(frefs):
            L.log(f'  ! {page["key"]} {view}: {len(units)} DOM sections vs {len(frefs)} Figma sections — paired by order up to '
                  f'{len(pairs)}; add map overrides. Unpaired DOM: {[u[0]["label"] for u in units[len(pairs):]]} '
                  f'Figma: {[f["name"] for f in frefs[len(pairs):]]}')
    out_pairs = []
    L.log(f'{page["key"]} {view} [{set_}]')
    for k, (grp, f) in enumerate(pairs):
        y0 = grp[0]['y']
        y1 = grp[-1]['y'] + grp[-1]['h']
        fh = f['crop'][1] if f['crop'] else f['h']
        dh = (y1 - y0) - fh
        flag = ' <-- check mapping' if fh and abs(dh) / fh > 0.25 else ''
        label = '+'.join(s['label'] for s in grp)
        L.log(f'  {k + 1:2d} {label[:34]:34s} {y1 - y0:6.0f}  <->  {f["name"][:30]:30s} {f["id"]:>13s} {fh:7.0f}  {dh:+6.0f}{flag}')
        sel = grp[0]['selector'] if len(grp) == 1 and not grp[0].get('withHeader') else None
        out_pairs.append({'order': k + 1, 'label': label, 'dom': [s['label'] for s in grp],
                          'selector': sel,
                          'domSelector': next((s['selector'] for s in cap['sections'] if s['label'] == grp[0]['label']), None),
                          'withHeader': bool(grp[0].get('withHeader')), 'headerH': grp[0].get('headerExt', 0),
                          'hasText': all(s.get('hasText', True) for s in grp),
                          'ours': {'y': y0, 'h': y1 - y0}, 'figma': f})
    m = {'page': page['key'], 'view': view, 'width': w, 'theme': theme, 'set': set_,
         'capture': f'cap/{set_}/{page["key"]}-{view}.png', 'capturedAt': cap.get('capturedAt'), 'docHeight': cap['docHeight'],
         'figmaAll': [dict(file=f'fig/{page["key"]}/{view}/{s["file"]}', y=s['y'], h=s['h'], w=s['w']) for s in fig['sections'] if not s.get('missing')],
         'figmaFrameH': fig['frameBox']['h'], 'figmaScale': fig.get('scale', 1), 'pairs': out_pairs,
         'unpairedDom': [u[0]['label'] for u in units[len(pairs):]] if units else [],
         'unpairedFigma': [], 'console': [c for c in cap.get('console', []) if not c.get('noise')][:20]}
    if os.path.exists(out_p) and json.load(open(out_p)) == json.loads(json.dumps(m)):
        os.utime(out_p)  # same pairs (e.g. an unrelated config edit): keep the file, composites stay valid
        return 'same'
    L.atomic_json(m, out_p)
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('-c', '--config')
    ap.add_argument('--set', default='now')
    ap.add_argument('--pages')
    ap.add_argument('--force', action='store_true', help='rebuild even when newer than its inputs')
    a = ap.parse_args()
    cfg = L.load_config(a.config)
    done = skipped = 0
    for page in L.comp_pages(cfg, L.select_pages(cfg, a.pages)):
        for view in L.views(cfg, page):
            r = build(cfg, page, view, a.set, a.force)
            if r in ('skip', 'same'):
                skipped += 1
            elif r:
                done += 1
    L.log(f'map [{a.set}]: {done} built, {skipped} up to date (skipped)')


if __name__ == '__main__':
    main()
