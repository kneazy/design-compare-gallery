#!/usr/bin/env python3
"""Build <out>/questions.html — designer questions with answer chips, free text and "copy all".

Usage: python3 questions.py -c design-compare.json [--data <out>/questions.json] [--out-file questions.html]

Data file (JSON; text may use `backticks` for UI strings, rendered as <code>):
{
  "title": "Questions on the mockups", "storageKey": "proj-questions-v1",
  "intro": ["paragraph", "..."],
  "groups": [
    {"id": "a", "title": "A. Needs your answer", "desc": "...", "cards": [
      {"title": "Mobile CTA: `Book a call` or `Get a consultation`", "pages": ["Dashboard"], "src": "#12",
       "problem": "...", "details": ["..."], "now": "What the build does today",
       "options": ["Keep `Book a call`", "Use `Get a consultation`"], "currentOption": 0,
       "figma": [["Dashboard mobile CTA", "9270:309214"]],          // node id or full URL
       "images": [{"src": "comp/now/dashboard/440-light/10-cta.png", "cap": "Figma | ours"}],
       "facts": [{"kind": "placeholder|claim|note", "text": "[N] projects", "where": "Hero"}]}]},
    {"id": "b", "title": "B. Decided ourselves", "answer": false,
     "rows": [{"title": "...", "decision": "...", "pages": ["API"]}]}
  ]
}
Images are paths under <out> (or absolute); ones wider than 1600 px get a JPEG thumbnail in <out>/q/,
the lightbox opens the original. Answers persist in the browser (localStorage) per storageKey.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dcglib as L  # noqa: E402
from PIL import Image  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
THUMB_W = 1600


def resolve(cfg, src):
    p = src if os.path.isabs(src) else L.out(cfg, src)
    if not os.path.exists(p):
        L.log(f'  image not found: {src}')
        return None
    qd = L.out(cfg, 'q')
    os.makedirs(qd, exist_ok=True)
    if not os.path.abspath(p).startswith(os.path.abspath(cfg['_out']) + os.sep):
        dst = os.path.join(qd, hashlib.md5(p.encode()).hexdigest()[:10] + os.path.splitext(p)[1])
        shutil.copy(p, dst)
        p = dst
    im = Image.open(p)
    full = L.rel(cfg, p)
    w, h = im.size
    if w > THUMB_W:
        t = os.path.join(qd, hashlib.md5(full.encode()).hexdigest()[:10] + '.jpg')
        nh = round(h * THUMB_W / w)
        im.convert('RGB').resize((THUMB_W, nh), Image.LANCZOS).save(t, 'JPEG', quality=85)
        return {'web': L.rel(cfg, t), 'full': full, 'w': THUMB_W, 'h': nh}
    return {'web': full, 'full': full, 'w': w, 'h': h}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('-c', '--config')
    ap.add_argument('--data')
    ap.add_argument('--out-file', default='questions.html')
    a = ap.parse_args()
    cfg = L.load_config(a.config)
    dp = a.data or cfg.get('questions') or L.out(cfg, 'questions.json')
    if not os.path.isabs(dp) and not os.path.exists(dp):
        dp = os.path.join(cfg['_dir'], dp)
    D = json.load(open(dp, encoding='utf-8'))
    fk = cfg['figma']['fileKey']
    nq = 0
    for g in D['groups']:
        for c in g.get('cards', []):
            nq += 1
            c['figma'] = [[lab, u if u.startswith('http') else f'https://www.figma.com/design/{fk}/?node-id={L.norm_node(u).replace(":", "-")}']
                          for lab, u in c.get('figma', [])]
            imgs = []
            for i in c.get('images', []):
                r = resolve(cfg, i['src'])
                if r:
                    imgs.append(dict(r, cap=i.get('cap', '')))
            c['images'] = imgs
    D.setdefault('lang', cfg.get('lang', 'en'))
    tpl = open(os.path.join(L.ASSETS, 'questions.html'), encoding='utf-8').read()
    js = 'const DATA=' + json.dumps(D, ensure_ascii=False).replace('</', '<\\/') + ';'
    outp = L.out(cfg, a.out_file)
    open(outp, 'w', encoding='utf-8').write(tpl.replace('/*DATA*/', js))
    L.log(f'questions: {outp} — {nq} cards in {len(D["groups"])} groups')


if __name__ == '__main__':
    main()
