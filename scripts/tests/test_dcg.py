"""Self-tests for the deterministic parts of design-compare-gallery (no browser, no network).
Run: python3 ~/.claude/skills/design-compare-gallery/scripts/dcg.py test
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import dcglib as L  # noqa: E402
import build_gallery  # noqa: E402
import map_sections  # noqa: E402
import regress  # noqa: E402
import score  # noqa: E402
from PIL import Image  # noqa: E402


def cfg_in(tmp, pages, **extra):
    raw = {'baseUrl': 'http://127.0.0.1:9', 'outputDir': '.', 'figma': {'fileKey': 'K'}, 'widths': [1440], 'themes': {'light': {}},
           'sections': {'selector': 'section', 'header': 'header', 'figmaSkip': '^Footer$'}, 'pages': pages}
    raw.update(extra)
    p = os.path.join(tmp, 'design-compare.json')
    json.dump(raw, open(p, 'w'))
    return L.load_config(p)


class Separator(unittest.TestCase):
    def test_detects_gap(self):
        im = Image.new('RGB', (300, 120), (240, 240, 240))
        im.paste(Image.new('RGB', (16, 120), L.SEPARATOR), (140, 0))
        g = build_gallery.detect_image(im)
        self.assertEqual(g['pairs'], [[[0, 140], [156, 300]]])

    def test_no_gap(self):
        self.assertEqual(build_gallery.detect_image(Image.new('RGB', (100, 50), (0, 0, 0)))['pairs'], [])


class Regress(unittest.TestCase):
    def test_one_level_one_channel_counts(self):
        a = Image.new('RGB', (10, 10), (100, 100, 100))
        b = a.copy()
        b.putpixel((3, 4), (100, 101, 100))  # L conversion would round this away
        self.assertEqual(regress.count(regress.changed_mask(a, b)), 1)
        self.assertEqual(regress.count(regress.changed_mask(a, a.copy())), 0)

    def test_head_diff(self):
        ha = {'title': 'x', 'meta': {'og:title': 'a'}, 'jsonld': ['{"a":1}']}
        hb = {'title': 'x', 'meta': {'og:title': 'b'}, 'jsonld': ['{"a":2}']}
        fields = [d['field'] for d in regress.head_diff(ha, hb)]
        self.assertEqual(fields, ['JSON-LD #1', 'meta og:title'])
        self.assertEqual(regress.head_diff(ha, json.loads(json.dumps(ha))), [])


class Mapping(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_order_merge_header_and_skip(self):
        page = {'key': 'p', 'route': '/', 'frames': {'1440-light': '1:1'}, 'map': {'merge': {'*': [['b', 'c']]}}}
        cfg = cfg_in(self.tmp, [page])
        fig = {'frameBox': {'w': 1440, 'h': 3000}, 'scale': 1, 'sections': [
            {'id': '1:2', 'name': 'Hero', 'file': '01.png', 'x': 0, 'y': 0, 'w': 1440, 'h': 900},
            {'id': '1:3', 'name': 'Services', 'file': '02.png', 'x': 0, 'y': 900, 'w': 1440, 'h': 1500},
            {'id': '1:4', 'name': 'Footer', 'file': '03.png', 'x': 0, 'y': 2400, 'w': 1440, 'h': 600}]}
        cap = {'docHeight': 3000, 'header': {'y': 40, 'h': 60}, 'sections': [
            {'label': 'a', 'selector': '#a', 'y': 100, 'h': 800}, {'label': 'b', 'selector': '#b', 'y': 900, 'h': 700},
            {'label': 'c', 'selector': '#c', 'y': 1600, 'h': 800}]}
        os.makedirs(L.out(cfg, 'fig', 'p', '1440-light'))
        json.dump(fig, open(L.out(cfg, 'fig', 'p', '1440-light', 'sections.json'), 'w'))
        os.makedirs(L.out(cfg, 'cap', 'now'))
        json.dump(cap, open(L.out(cfg, 'cap', 'now', 'p-1440-light.json'), 'w'))
        m = map_sections.build(cfg, cfg['pages'][0], '1440-light', 'now')
        self.assertEqual([p['label'] for p in m['pairs']], ['a', 'b+c'])
        self.assertEqual(m['pairs'][0]['ours'], {'y': 40, 'h': 860})       # header absorbed
        self.assertTrue(m['pairs'][0]['withHeader'])
        self.assertEqual(m['pairs'][0]['headerH'], 60)                     # only the part above the section
        self.assertEqual(m['pairs'][1]['ours'], {'y': 900, 'h': 1500})
        self.assertIsNone(m['pairs'][1]['selector'])                       # merged: not scoreable
        self.assertEqual(map_sections.build(cfg, cfg['pages'][0], '1440-light', 'now'), 'skip')  # incremental

    def test_page_without_frames(self):
        cfg = cfg_in(self.tmp, [{'key': 'x', 'route': '/x'}], widths=[1440, 390], themes={'light': {}, 'dark': {}})
        self.assertTrue(cfg['pages'][0]['nocomp'])
        self.assertEqual(L.views(cfg, cfg['pages'][0]), ['1440-light', '1440-dark', '390-light', '390-dark'])
        self.assertEqual(L.comp_pages(cfg), [])


class Incremental(unittest.TestCase):
    def test_fresh(self):
        d = tempfile.mkdtemp()
        try:
            i, o = os.path.join(d, 'in'), os.path.join(d, 'out')
            open(i, 'w').write('1')
            self.assertFalse(L.fresh([o], [i]))
            time.sleep(0.01)
            open(o, 'w').write('2')
            self.assertTrue(L.fresh([o], [i]))
            os.utime(i, (time.time() + 5, time.time() + 5))
            self.assertFalse(L.fresh([o], [i]))
        finally:
            shutil.rmtree(d)

    def test_atomic_json_leaves_no_tmp(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, 'x.json')
            L.atomic_json({'a': 1}, p)
            self.assertEqual(os.listdir(d), ['x.json'])
        finally:
            shutil.rmtree(d)

    def test_same_content(self):
        a = {'status': 200, 'buildId': None, 'htmlHash': 'abc'}
        self.assertTrue(L.same_content(a, dict(a)))
        self.assertFalse(L.same_content(a, dict(a, htmlHash='abd')))
        self.assertFalse(L.same_content(dict(a, buildId='1'), dict(a, buildId='2')))
        self.assertFalse(L.same_content(None, a))


class Boards(unittest.TestCase):
    def board(self, cells):
        return {'pages': [{'page': 'p', 'sections': [{'section': s, 'cells': {'1440-light': c}} for s, c in cells.items()]}]}

    def test_verdict_vs_moved(self):
        A = self.board({'hero': {'verdict': 'FAIL', 'diff': 9.0}, 'faq': {'verdict': 'FAIL', 'diff': 5.0}, 'cta': {'verdict': 'PASS', 'diff': 0.1}})
        B = self.board({'hero': {'verdict': 'WARN', 'diff': 1.5}, 'faq': {'verdict': 'FAIL', 'diff': 3.0}, 'cta': {'verdict': 'PASS', 'diff': 0.12}})
        verdict, moved, same, new, gone = score.diff_boards(A, B)
        self.assertEqual([v[1] for v in verdict], ['hero'])
        self.assertEqual([m[1] for m in moved], ['faq'])
        self.assertEqual(same, 1)
        self.assertEqual((new, gone), ([], {}))


if __name__ == '__main__':
    unittest.main()
