"""Shared helpers for the design-compare-gallery scripts: config loading, output paths,
Figma REST access (token never printed), and the Playwright run lock.

Python 3 standard library + Pillow only. Every script inserts its own folder into sys.path
and imports this module, so they can be run with any working directory.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(SKILL_DIR, 'scripts')
ASSETS = os.path.join(SKILL_DIR, 'assets')
PP_SCRIPTS = os.path.expanduser('~/.claude/skills/pixel-perfect/scripts')

SEPARATOR = (255, 0, 255)  # composite gap colour; the gallery splits Figma | ours on it
GAP = 16


def log(*a):
    print(*a, flush=True)


def die(msg, code=2):
    print(f'error: {msg}', file=sys.stderr, flush=True)
    sys.exit(code)


# ----------------------------------------------------------------------------- config

DEFAULTS = {
    'lang': 'en',
    'outputDir': 'out',
    'widths': [1440, 390],
    'themes': {'light': {}},
    'localStorage': {},
    'cookies': [],
    'storageState': None,
    'capture': {'viewportHeight': 900, 'waitMs': 800, 'hide': [], 'videos': 'hide', 'scrollStep': 600, 'timeoutMs': 60000},
    'sections': {'selector': 'main > section', 'header': None, 'label': 'id', 'labelStrip': None, 'figmaSkip': '^(Footer|footer)$'},
    'composite': {'chunk': 2400, 'overviewColumn': 480, 'overviewChunk': 1500, 'overviewMobileScale': 0.4},
    'score': {'viewportHeight': 9000, 'scale': 2, 'hover': True, 'refSets': {'figma': 'score/refs'}, 'pages': None, 'themes': None},
    'regress': {'mask': [], 'keepCaptures': False},
    'buildId': {},
    'lock': None,
    'sets': {},
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config(path):
    if not path:
        for cand in ('design-compare.json', '.design-compare/design-compare.json'):
            if os.path.exists(cand):
                path = cand
                break
    if not path or not os.path.exists(path):
        die('no config: pass -c <design-compare.json> (see references/config.md)')
    path = os.path.abspath(path)
    raw = json.load(open(path, encoding='utf-8'))
    cfg = _merge(DEFAULTS, raw)
    cfg['_raw'] = raw
    cfg['_path'] = path
    cfg['_dir'] = os.path.dirname(path)
    out = os.path.expanduser(cfg['outputDir'])
    cfg['_out'] = os.path.normpath(out if os.path.isabs(out) else os.path.join(cfg['_dir'], out))
    if not cfg.get('baseUrl'):
        die('config: baseUrl is required')
    cfg['baseUrl'] = cfg['baseUrl'].rstrip('/')
    if not cfg.get('pages'):
        die('config: pages[] is required')
    if not (cfg.get('figma') or {}).get('fileKey'):
        die('config: figma.fileKey is required')
    keys = set()
    for p in cfg['pages']:
        p.setdefault('frames', {})
        p['nocomp'] = not p['frames']  # a page without a design: our sections only, no score
        for k in ('key', 'route'):
            if k not in p:
                die(f'config: page {p.get("key", "?")} lacks "{k}"')
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', p['key']):
            die(f'config: page key "{p["key"]}" must be lowercase letters, digits, - or _')
        if p['key'] in keys:
            die(f'config: duplicate page key {p["key"]}')
        keys.add(p['key'])
        p.setdefault('title', p['key'])
        for v, nid in p['frames'].items():
            parse_view(v)
            p['frames'][v] = norm_node(nid)
    cfg['widths'] = [int(w) for w in cfg['widths']]
    return cfg


def parse_view(v):
    m = re.fullmatch(r'(\d+)-([a-z0-9_]+)', v)
    if not m:
        die(f'view key "{v}" must be <width>-<theme>, e.g. 1440-light')
    return int(m[1]), m[2]


def views(cfg, page=None):
    """Views in config order (widths x themes). A page with frames has the views it has frames
    for; a page without a design (no frames) has every width x theme."""
    allv = [f'{w}-{t}' for w in cfg['widths'] for t in cfg['themes']]
    if page is not None:
        return allv if page.get('nocomp') else [v for v in allv if v in page['frames']]
    have = set()
    for p in cfg['pages']:
        have |= set(views(cfg, p))
    return [v for v in allv if v in have]


def comp_pages(cfg, pages=None):
    return [p for p in (pages or cfg['pages']) if not p.get('nocomp')]


def norm_node(nid):
    return str(nid).replace('-', ':')


def nid_file(nid):
    return norm_node(nid).replace(':', '_')


def theme_bg(cfg, theme, fallback=None):
    t = cfg['themes'].get(theme, {})
    if t.get('background'):
        return hex_rgb(t['background'])
    return fallback or (255, 255, 255)


def hex_rgb(h):
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def out(cfg, *parts):
    return os.path.join(cfg['_out'], *parts)


def rel(cfg, p):
    return os.path.relpath(p, cfg['_out']).replace(os.sep, '/')


def select_pages(cfg, only):
    if not only:
        return cfg['pages']
    want = only.split(',') if isinstance(only, str) else only
    bad = [w for w in want if w not in {p['key'] for p in cfg['pages']}]
    if bad:
        die(f'unknown page(s): {", ".join(bad)}')
    return [p for p in cfg['pages'] if p['key'] in want]


def view_match(spec, view):
    """Override keys: "*" (every view), "1440" (a width), "dark" (a theme) or "1440-dark"."""
    if spec == '*' or spec == view:
        return True
    w, t = view.split('-', 1)
    return spec == w or spec == t


# ----------------------------------------------------------------------------- Figma

def figma_token(cfg):
    """FIGMA_TOKEN env first; else config figma.token: "env:NAME", "file:<path>" (file holds the
    token) or "json:<path>#a.b.c" (a key inside a JSON file). The value is never printed."""
    tok = os.environ.get('FIGMA_TOKEN')
    if tok:
        return tok.strip()
    spec = (cfg.get('figma') or {}).get('token')
    if not spec:
        die('no Figma token: export FIGMA_TOKEN in this shell, or set figma.token in the config '
            '("env:NAME", "file:<path>", "json:<path>#dot.path")')
    kind, _, val = spec.partition(':')
    if kind == 'env':
        tok = os.environ.get(val)
    elif kind == 'file':
        tok = open(os.path.expanduser(val), encoding='utf-8').read()
    elif kind == 'json':
        fp, _, dotted = val.partition('#')
        o = json.load(open(os.path.expanduser(fp), encoding='utf-8'))
        for k in dotted.split('.'):
            o = o.get(k) if isinstance(o, dict) else None
        tok = o
    if not tok:
        die(f'figma.token "{kind}:…" did not yield a token')
    return str(tok).strip()


class FigmaRenderError(Exception):
    pass


def figma_get(cfg, path, params=None, tries=5, soft=False):
    """GET the Figma REST API with retries. 429 waits for Retry-After. With soft=True a 5xx or a
    timeout raises FigmaRenderError after two tries instead of dying, so a caller can split an
    image batch that Figma fails to render in time."""
    url = 'https://api.figma.com' + path + ('?' + urllib.parse.urlencode(params) if params else '')
    req = urllib.request.Request(url, headers={'X-Figma-Token': figma_token(cfg)})
    for a in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                die(f'Figma {e.code} for {path} (token lacks access, or wrong file key / node id)')
            if soft and e.code >= 500 and a >= 1:
                raise FigmaRenderError(f'Figma {e.code}')
            wait = int(e.headers.get('Retry-After') or 0) or 10 * (a + 1)
            log(f'  figma {e.code}, retry in {wait}s')
            time.sleep(min(wait, 120))
        except Exception as e:  # network hiccup, timeout
            if soft and a >= 1:
                raise FigmaRenderError(type(e).__name__)
            log(f'  figma error {type(e).__name__}, retry')
            time.sleep(5 * (a + 1))
    die(f'Figma request failed after {tries} tries: {path}')


def download(url, dest, tries=4):
    for a in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=300) as r:
                data = r.read()
            atomic_bytes(data, dest)
            return
        except Exception:
            time.sleep(5 * (a + 1))
    die(f'download failed: {dest}')


# ----------------------------------------------------------------------------- atomic files
# A crash or a network error mid-run must never leave a half-written file that a later run
# trusts: every output is written to <name>.tmp and renamed into place.

def atomic_bytes(data, path):
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def atomic_json(obj, path, indent=1):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False)
    os.replace(tmp, path)


def atomic_text(text, path):
    atomic_bytes(text.encode('utf-8'), path)


def atomic_image(im, path, **kw):
    fmt = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG'}[os.path.splitext(path)[1].lower()]
    tmp = path + '.tmp'
    im.save(tmp, fmt, **kw)
    os.replace(tmp, path)


def mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def fresh(outputs, inputs):
    """True when every output exists and is newer than every input (incremental skip)."""
    outs = [mtime(o) for o in outputs]
    return bool(outs) and min(outs) > 0 and min(outs) >= max([mtime(i) for i in inputs] or [0])


# ----------------------------------------------------------------------------- disk

MB_PER_VIEW = 15  # full-page capture + composites of one page view, measured on marketing pages


def ensure_disk(cfg, n_views, what, per_view_mb=MB_PER_VIEW):
    """Refuse to start a step that would fill the disk (it filled twice in real use)."""
    if os.environ.get('DCG_NO_DISK_CHECK'):
        return
    os.makedirs(cfg['_out'], exist_ok=True)
    free = shutil.disk_usage(cfg['_out']).free / 2 ** 20
    need = n_views * per_view_mb + 300  # + headroom for the browser and temp files
    if free < need:
        die(f'{what}: {free:.0f} MB free, about {need:.0f} MB needed ({n_views} page views x ~{per_view_mb} MB + 300). '
            'Free space first: dcg.py sets / dcg.py prune --keep <sets> --yes (or DCG_NO_DISK_CHECK=1 to override).')


def dir_mb(p):
    t = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                t += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return t / 2 ** 20


# ----------------------------------------------------------------------------- build fingerprint

NEXT_RE = re.compile(r'"buildId"\s*:\s*"([^"]+)"')
NEXT_STATIC_RE = re.compile(r'/_next/static/([^/"\']+)/_(?:buildManifest|ssgManifest)\.js')
GEN_RE = re.compile(r'<meta[^>]+name=["\']generator["\'][^>]*content=["\']([^"\']+)', re.I)
VOLATILE = [r'nonce="[^"]*"', r'nonce=\'[^\']*\'']


def fingerprint(cfg, route, base=None):
    """What build and what content a page serves right now: HTTP status, a build id and a hash of
    the HTML (volatile parts such as CSP nonces stripped; config buildId.volatile adds regexes).

    Build id, first that answers: config buildId.command (a read-only shell command, e.g.
    `git -C ../app rev-parse --short HEAD`), config buildId.regex (group 1 on the HTML),
    Next.js __NEXT_DATA__.buildId or /_next/static/<id>/ (except "development"), <meta generator>.
    A dev server has no real build id — then the HTML hash is the only staleness signal, and it
    also catches CMS content changes. Client-side-fetched content is invisible to it."""
    bc = cfg.get('buildId') or {}
    url = (base or cfg['baseUrl']).rstrip('/') + route
    out = {'url': url, 'status': None, 'buildId': None, 'buildIdSource': None, 'htmlHash': None}
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'design-compare-gallery'})
        with urllib.request.urlopen(req, timeout=60) as r:
            out['status'] = r.status
            html_ = r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        out['status'] = e.code
        return out
    except Exception as e:
        out['error'] = f'{type(e).__name__}: {e}'
        return out
    norm = html_
    for v in VOLATILE + list(bc.get('volatile') or []):
        norm = re.sub(v, '', norm)
    out['htmlHash'] = hashlib.sha1(norm.encode('utf-8')).hexdigest()[:12]
    if bc.get('command'):
        try:
            out['buildId'] = subprocess.run(bc['command'], shell=True, capture_output=True, text=True, timeout=30).stdout.strip()[:40] or None
            out['buildIdSource'] = 'command'
        except Exception:
            pass
    if not out['buildId'] and bc.get('regex'):
        m = re.search(bc['regex'], html_)
        if m:
            out['buildId'], out['buildIdSource'] = m.group(1), 'regex'
    if not out['buildId']:
        for rx, src in ((NEXT_RE, 'next'), (NEXT_STATIC_RE, 'next-static'), (GEN_RE, 'generator')):
            m = rx.search(html_)
            if m and m.group(1) != 'development':
                out['buildId'], out['buildIdSource'] = m.group(1), src
                break
    return out


def same_content(a, b):
    """Two fingerprints show the same build + content."""
    if not a or not b or a.get('status') != b.get('status'):
        return False
    if a.get('buildId') and b.get('buildId') and a['buildId'] != b['buildId']:
        return False
    return a.get('htmlHash') is not None and a.get('htmlHash') == b.get('htmlHash')


def set_meta(cfg, set_):
    p = out(cfg, 'cap', set_, '_set.json')
    return json.load(open(p)) if os.path.exists(p) else None


def local_time(iso):
    """'2026-11-06T08:12:03.120Z' (UTC, from the browser) -> '2026-11-06 10:12' local time."""
    if not iso:
        return ''
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return iso[:16].replace('T', ' ')


def build_label(meta):
    """Short build description of a capture set: build id (or html hash) + capture time."""
    if not meta:
        return ''
    ids = sorted({p.get('buildId') for p in meta.get('pages', {}).values() if p.get('buildId')})
    hashes = sorted({p.get('htmlHash') for p in meta.get('pages', {}).values() if p.get('htmlHash')})
    b = ('build ' + ','.join(i[:12] for i in ids)) if ids else (f'html {hashes[0][:7]}' + (f' +{len(hashes) - 1}' if len(hashes) > 1 else '') if hashes else 'build ?')
    return f'{b}, {local_time(meta.get("capturedAt"))}'


# ----------------------------------------------------------------------------- Playwright lock

BUSY_RE = re.compile(r'(slots all held|lock held|DCG-LOCK-BUSY)', re.I)
LOAD_RE = re.compile(r'(load above|DCG-LOCK-LOAD)', re.I)


def _load1():
    try:
        return os.getloadavg()[0]
    except OSError:
        return 0.0


def default_max_load():
    """4 x CPU count. A fixed 8 blocked all work for hours on a Mac where Spotlight alone holds
    the load near 25; what matters is that our own runs do not pile up, which the slots ensure."""
    return float(os.environ.get('DCG_MAX_LOAD') or 4 * (os.cpu_count() or 2))


class _Waiter:
    """Say why we are waiting once a minute instead of either spamming or staying silent."""

    def __init__(self):
        self.last = 0.0

    def say(self, why):
        if time.time() - self.last >= 60:
            log(f'  waiting: {why} ({time.strftime("%H:%M:%S")})')
            self.last = time.time()


def run_locked(cfg, cmd, env=None, max_load=None):
    """Run one Playwright command at a time on the machine.

    Config "lock": path to an external semaphore script with the protocol
    `sh lock.sh <cmd...>` -> exit 4 + "slots all held" when busy, exit 3 + "load above" when the
    machine is loaded; otherwise it runs the command. Without it a bundled mkdir slot lock is used
    (DCG_SLOTS, default 2) plus a load guard (DCG_MAX_LOAD, default 4 x CPU count).
    Output streams through; returns the exit code.
    """
    max_load = float(max_load or default_max_load())
    env = dict(os.environ, **(env or {}))
    ext = cfg.get('lock')
    w = _Waiter()
    while True:
        if ext:
            rc, head, refused = _stream(['sh', os.path.expanduser(ext)] + cmd, env)
            if refused and rc == 4 and BUSY_RE.search(head):
                w.say('external lock busy — ' + head.strip().splitlines()[0][:160])
                time.sleep(5)
                continue
            if refused and rc == 3 and LOAD_RE.search(head):
                w.say('external lock: ' + head.strip().splitlines()[0][:160])
                time.sleep(30)
                continue
            return rc
        if _load1() > max_load:
            w.say(f'1-minute load {_load1():.1f} > {max_load:g} (DCG_MAX_LOAD)')
            time.sleep(15)
            continue
        slot = _take_slot()
        if not slot:
            w.say('all bundled Playwright slots held (DCG_SLOTS)')
            time.sleep(3)
            continue
        try:
            rc, _, _ = _stream(cmd, env)
            return rc
        finally:
            _release(slot)


def _take_slot():
    base = os.path.join(tempfile.gettempdir(), 'dcg-pw.lock')
    for i in range(1, int(os.environ.get('DCG_SLOTS', 2)) + 1):
        d = f'{base}.{i}'
        try:
            os.mkdir(d)
            open(os.path.join(d, 'owner'), 'w').write(f'{os.getpid()} {time.strftime("%H:%M:%S")}\n')
            return d
        except FileExistsError:
            try:  # stale slot: owner process gone
                pid = int(open(os.path.join(d, 'owner')).read().split()[0])
                os.kill(pid, 0)
            except (ProcessLookupError, ValueError, FileNotFoundError):
                _release(d)
            except PermissionError:
                pass
    return None


def _release(d):
    try:
        for f in os.listdir(d):
            os.remove(os.path.join(d, f))
        os.rmdir(d)
    except FileNotFoundError:
        pass


def _stream(cmd, env):
    """Stream a command's output; returns (rc, first lines, refused) where refused means the
    first line was a lock refusal (then nothing is printed — the caller reports the wait)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True, bufsize=1)
    head = []
    refused = False
    for line in p.stdout:
        if not head and (BUSY_RE.search(line) or LOAD_RE.search(line)):
            refused = True
        if len(head) < 5:
            head.append(line)
        if not refused:
            sys.stdout.write(line)
            sys.stdout.flush()
    return p.wait(), ''.join(head), refused


def node_bin():
    return os.environ.get('NODE', 'node')
