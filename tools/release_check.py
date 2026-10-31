"""Build Rift Lord and check the build before it is installed: one command.

    python3 tools/release_check.py [--skip-build] [--windows] [--install]
                                   [--bake RECORD] [--saves] [--record ID] [--no-ui]
                                   [--cases CASE,...] [--ui-groups N] [--from-build DIR]
                                   [--ui-only]
    python3 workshop/release_check.py --prune

1. Build (unless --skip-build) the UIs, the frozen server and the window with
   tools/build.py, into a staging folder -- never straight into the dist folder. With
   --windows the .exe pair is built too (Wine + llvm-mingw).
2. Start the frozen server (`--sidecar`, stdin a pipe) through testservers.py: scratch
   XDG folders, TMPDIR and Missions, with a copy of the record from the test missions
   (tests/fixtures/missions; default 900_Fixture_Edit_P), on whatever ports are free.
   The ports are read from the server, never assumed.
3. Endpoint checks: the Onslaught Editor's first page load (its two first requests
   together), /api/tools, catalogue fx rows, materials, a plan round trip
   keeping fx items and look.paints, the mission settings preview and a live preview
   POST, the music endpoint (an Ogg ffprobe reads, cached under RiftLord's cache), and
   Map Settings' minimap preview completing within its time limits (a shipped level
   regenerated, quick then full, then the record's own), and Regenerate's scores against
   their baseline from the last minimap harness run (riftlord.minimapscore; a warning
   when that run is older than the drawing code).
4. The UI smoke suite (tests/ui_smoke/ui_smoke.js), or the `--cases` named: the SEPARATE
   cases on servers of their own, one after another, and the rest split into
   `--ui-groups` browser groups of about equal time (by the last run's case times), group
   0 on the same server after the endpoint checks and each other group on a server of
   its own, all side by side from the moment the server is up.
5. --bake RECORD: build that record through /api/bucket/build (stages polled), package
   it, build the same record from source, and compare the two paks (pakinspect.py):
   entry list, effect counts, preview creatures, registry_problems.
6. The server stops when its stdin closes; that it stopped cleanly is a check.
7. --saves: the onslaught save regression suite (python3 -m riftlord.onslaught.selftest --saves).
8. --windows: the Windows server under Wine, started as the window starts it (stdin a
   Windows pipe): endpoints, music, and a clean stop when the pipe closes.
9. The user's Missions, ~/.local/share/RiftLord and ~mods are unchanged.
10. --install, only when every check passed: each binary in the dist folder is kept as
   <name>-<its date><ext> and the new build replaces it. Only the newest KEEP_COPIES kept
   copies of each binary stay; older ones are deleted, and listed. --prune does only that.

Prints a pass/fail table and writes report.json beside the logs (the run folder is
printed). Exit 0 only when every check passed. Everything it starts is stopped by PID /
process group, and its browser profiles removed, however it ends.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, ROOT)

import build as build_app                             # noqa: E402
import testservers                                    # noqa: E402

DIST = build_app.DIST
#: Each run's logs, staged builds and report.
RUNS = os.path.join(testservers.SCRATCH, 'editor-release-check')
DEFAULT_RECORD = '900_Fixture_Edit_P'
DEFAULT_TRACK = 'Battle_03'
#: The binaries a build produces, per platform.
LINUX_FILES = ['RiftLord', 'RiftLordServer', 'RiftLordServer.baker.txt', 'THIRD-PARTY-NOTICES.txt']
WINDOWS_FILES = ['RiftLord.exe', 'RiftLordServer.exe', 'RiftLordServer.exe.baker.txt']
#: Seconds allowed per step.
BUILD_TIMEOUT = 3 * 3600
UI_TIMEOUT = 1800
#: The largest load factor the UI suite's budgets are multiplied by (load_scale).
UI_SCALE_MAX = 4.0
SAVES_TIMEOUT = 2 * 3600
BAKE_TIMEOUT = 2 * 3600
PREVIEW_TIMEOUT = 900
WINE_READY_TIMEOUT = 300
WINE_STOP_TIMEOUT = 60
#: Wine: the build prefix's Windows Python runs the test parent; the server runs in a
#: prefix of its own so its caches start empty and never touch the build prefix.
WIN_BUILD_PREFIX = os.path.join(os.path.expanduser('~'), '.cache', 'rift-lord', 'winebuild')
WIN_TEST_PREFIX = os.path.join(os.path.expanduser('~'), '.cache', 'rift-lord', 'winetest')
WIN_PYTHON = os.path.join(WIN_BUILD_PREFIX, 'drive_c', 'Program Files', 'Python312', 'python.exe')
USER_DATA = os.path.join(os.path.expanduser('~'), '.local', 'share', 'RiftLord')
#: The game's Win64 folder: RIFTLORD_WIN64 when this runs from a copy (the gate's snapshot).
WIN64 = os.environ.get('RIFTLORD_WIN64') or os.path.dirname(ROOT)
MODS = os.path.normpath(os.path.join(WIN64, '..', '..', 'Content', 'Paks', '~mods'))
NICE = ['nice', '-n', '10', 'ionice', '-c', '3']

#: Started subprocess groups, killed however the run ends.
_GROUPS = []
#: Rows are added from the separate UI cases' thread too.
REPORT_LOCK = threading.Lock()


class Report:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.rows = []
        self.info = {}

    def add(self, name, ok, detail='', seconds=None, warn=False):
        with REPORT_LOCK:
            return self._add(name, ok, detail, seconds, warn)

    def _add(self, name, ok, detail='', seconds=None, warn=False):
        row = {'check': name, 'ok': bool(ok), 'warn': bool(warn), 'detail': str(detail),
               'seconds': None if seconds is None else round(seconds, 1)}
        self.rows.append(row)
        mark = 'PASS' if ok else 'FAIL'
        if ok and warn:
            mark = 'WARN'
        print('  %-4s %-34s %s%s' % (mark, name, row['detail'][:160],
                                     '' if seconds is None else '  (%.1f s)' % seconds), flush=True)
        return ok

    def ok(self):
        return bool(self.rows) and all(r['ok'] for r in self.rows)

    def table(self):
        w = max(len(r['check']) for r in self.rows) if self.rows else 10
        lines = ['%-4s  %-*s  %8s  %s' % ('', w, 'check', 'seconds', 'detail')]
        for r in self.rows:
            mark = ('WARN' if r['warn'] else 'PASS') if r['ok'] else 'FAIL'
            lines.append('%-4s  %-*s  %8s  %s' % (mark, w, r['check'],
                                                  '' if r['seconds'] is None else '%.1f' % r['seconds'],
                                                  r['detail'][:120]))
        return '\n'.join(lines)

    def write(self):
        path = os.path.join(self.run_dir, 'report.json')
        with open(path, 'w') as fh:
            json.dump({'ok': self.ok(), 'checks': self.rows, **self.info}, fh, indent=1)
        return path


def timed(report, name, fn, *a, **kw):
    """Run fn -> (ok, detail[, warn]) as one row; an exception fails the row."""
    t = time.time()
    try:
        got = fn(*a, **kw)
        ok, detail = got[0], got[1]
        warn = got[2] if len(got) > 2 else False
    except urllib.error.HTTPError as exc:
        body = exc.read()[:300].decode('utf-8', 'replace')
        ok, detail, warn = False, 'HTTP %d %s' % (exc.code, body), False
    except Exception as exc:                          # noqa: BLE001
        ok, detail, warn = False, '%s: %s' % (type(exc).__name__, exc), False
    return report.add(name, ok, detail, time.time() - t, warn)


def run_logged(cmd, log_path, env=None, cwd=ROOT, timeout=None):
    """Run cmd in its own process group with output to log_path; returns the exit code.
    The group is killed if the run is interrupted or times out."""
    with open(log_path, 'ab') as log:
        log.write(('\n$ %s\n' % ' '.join(cmd)).encode())
        log.flush()
        p = subprocess.Popen(testservers.dies_with_parent(cmd), cwd=cwd, env=env, stdout=log,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
        _GROUPS.append(p.pid)
        try:
            return p.wait(timeout)
        except subprocess.TimeoutExpired:
            log.write(b'\n[release_check] timed out\n')
            return None
        finally:
            _kill_group(p.pid)
            _GROUPS.remove(p.pid)


def _kill_group(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def scratch_env(run_dir):
    tmp = os.path.join(run_dir, 'tmp')
    os.makedirs(tmp, exist_ok=True)
    return dict(os.environ, TMPDIR=tmp, TEMP=tmp, TMP=tmp, PYTHONDONTWRITEBYTECODE='1')


def build_python():
    return build_app.venv_python() or sys.executable


def tail(path, n=12):
    try:
        with open(path, 'rb') as fh:
            return fh.read().decode('utf-8', 'replace').strip().splitlines()[-n:]
    except OSError:
        return []


# -- 1. build -----------------------------------------------------------------------

def build(report, stage, run_dir, windows):
    env = scratch_env(run_dir)
    py = build_python()
    log = os.path.join(run_dir, 'build-linux.log')
    t = time.time()
    code = run_logged(NICE + [py, os.path.join(HERE, 'build.py'), '--dist', stage],
                      log, env, timeout=BUILD_TIMEOUT)
    missing = [f for f in LINUX_FILES if not os.path.exists(os.path.join(stage, f))]
    ok = code == 0 and not missing
    report.add('build: Linux (UIs, server, window)', ok,
               'built %s' % ', '.join(LINUX_FILES[:2]) if ok else
               'exit %s, missing %s; %s' % (code, missing, ' | '.join(tail(log, 3))), time.time() - t)
    if not windows or not ok:
        return ok
    log = os.path.join(run_dir, 'build-windows.log')
    t = time.time()
    code = run_logged(NICE + [py, os.path.join(HERE, 'build.py'), '--windows', '--skip-ui',
                              '--dist', stage], log, env, timeout=BUILD_TIMEOUT)
    missing = [f for f in WINDOWS_FILES if not os.path.exists(os.path.join(stage, f))]
    ok = code == 0 and not missing
    report.add('build: Windows (Wine, llvm-mingw)', ok,
               'built %s' % ', '.join(WINDOWS_FILES[:2]) if ok else
               'exit %s, missing %s; %s' % (code, missing, ' | '.join(tail(log, 3))), time.time() - t)
    return ok


# -- 3. endpoint checks ---------------------------------------------------------------

def check_onslaught_first_load(srv):
    """The Onslaught Editor page asks for maps and campaigns at once on a cold server."""
    paths = ['/api/maps', '/api/campaigns', '/api/units', '/api/defaults']
    got = {}

    def hit(path):
        try:
            got[path] = srv.get(path, tool='onslaught', raw=True)[0]
        except urllib.error.HTTPError as exc:
            got[path] = 'HTTP %d %s' % (exc.code, exc.read()[:120].decode('utf-8', 'replace'))
    threads = [threading.Thread(target=hit, args=(p,)) for p in paths]
    [t.start() for t in threads]
    [t.join() for t in threads]
    bad = {p: v for p, v in got.items() if v != 200}
    return not bad, ('all %d answered 200' % len(paths)) if not bad else json.dumps(bad)


def check_tools(srv):
    got = srv.get('/api/tools')
    tools = got.get('tools') or {}
    ok = {'riftlord', 'onslaught'} <= set(tools)
    return ok, 'version %s; %s' % (got.get('version'), ', '.join('%s %s' % kv for kv in sorted(srv.urls.items())))


#: The stand-in Fab converter the test servers run (RIFTLORD_FABCONVERT).
FAKE_FAB = os.path.join(ROOT, 'tests', 'ui_smoke', 'fakefab.py')
#: The stand-in terrain kit builder the test servers run (RIFTLORD_TERRAINKIT).
FAKE_TERRAIN = os.path.join(ROOT, 'tests', 'ui_smoke', 'faketerrain.py')


def check_native(srv, windows=False):
    """/api/tools' native status. On Linux recastgen runs (carried); pxcook is ready or
    buildable where this machine's UE 4.27 and a compiler are (omd3physx.tool_status),
    else says exactly omd3physx.unavailable_message(), and Fab conversion then
    fabkits.UNAVAILABLE (the UI case `fab` shows it). On Windows (UNTESTED there: no
    native tools are built for it yet, and no engine under Wine) each tool may only say it
    is unavailable, worded for a person."""
    from riftlord.app import fabkits
    from riftlord.formats.omd3 import omd3physx
    native = srv.get('/api/tools').get('native')
    if not isinstance(native, dict) or not {'pxcook', 'recastgen', 'fab'} <= set(native):
        return False, 'no native status: %r' % (native,)
    said = '; '.join('%s %s' % (k, native[k] or 'available') for k in ('pxcook', 'recastgen', 'fab'))
    if windows:
        return (native['pxcook'] is None or native['pxcook'].startswith(omd3physx.NEEDS)) and \
            native['fab'] in (None, fabkits.UNAVAILABLE) and \
            (native['recastgen'] is None or 'unavailable in this build' in native['recastgen']), said
    status = omd3physx.tool_status()                  # this machine's, as the server sees it
    cooks = status['pxcook'] != 'missing'
    want = None if cooks else omd3physx.unavailable_message(status)
    # the server runs the stand-in converter, so Fab conversion stands or falls with cooking
    fab = None if cooks else fabkits.UNAVAILABLE
    got = (native.get('tools') or {}).get('pxcook') or {}
    return native['recastgen'] is None and native['pxcook'] == want and \
        native['fab'] == fab and got.get('pxcook') in ('ready', 'buildable', 'missing'), \
        said + '; pxcook %s: %s' % (got.get('pxcook'), got.get('detail'))


#: The fake vault's one pack (as tests/offline/test_fab.py makes it): no meshes.
FAKE_VAULT_ITEM = 'FakePack0123V1'


def fake_vault(run_dir):
    """A vault root in the run folder with FAKE_VAULT_ITEM: a vault scan is then instant
    (the user's real vault takes minutes on a fresh cache). Returns the root."""
    root = os.path.join(run_dir, 'fake-vault')
    item = os.path.join(root, FAKE_VAULT_ITEM)
    os.makedirs(os.path.join(item, 'data', 'Content', 'Fake_Pack', 'Meshes'), exist_ok=True)
    with open(os.path.join(item, 'vault.json'), 'w') as fh:
        json.dump({'title': 'Fake Pack'}, fh)
    with open(os.path.join(item, 'manifest.json'), 'w') as fh:
        fh.write('{}')
    return root


def _fab_env(run_dir):
    env = dict(scratch_env(run_dir))
    for var in ('XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME'):
        env[var] = os.path.join(run_dir, 'fab-' + var.lower())  # never the user's own folders
    return env


def _fab_json(got):
    try:
        return json.loads(got.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def check_fab_frozen(server_bin, run_dir):
    """`RiftLordServer --fab vault --json --root FAKE`, when the build carries riftlord.fab:
    a JSON answer listing the fake vault's one pack, exit 0."""
    if not os.path.isdir(os.path.join(ROOT, 'riftlord', 'fab')):
        return True, 'riftlord.fab not in this tree yet: skipped'
    got = subprocess.run([server_bin, '--fab', 'vault', '--json', '--root', fake_vault(run_dir)],
                         capture_output=True, text=True, env=_fab_env(run_dir), timeout=300)
    data = _fab_json(got)
    if data is None:
        return False, 'exit %d, no JSON: %s' % (got.returncode, (got.stdout + got.stderr)[-300:])
    items = [i.get('item') for i in data.get('items') or []]
    return got.returncode == 0 and items == [FAKE_VAULT_ITEM], 'exit %d; items %s' % (
        got.returncode, items)


#: --fab-real: the smallest art pack the frozen convert check converts from the user's vault.
FAB_CHECK_PACK = 'Stylized0bce0f98767dV3'


def check_fab_convert_frozen(server_bin, run_dir, real=False):
    """`RiftLordServer --fab convert PACK --jobs 2`: the converter's worker pool in the frozen
    binary (fork on Linux; spawn and freeze_support on Windows). PACK is the fake vault's:
    no meshes, so the run goes through the pool and ends on its one expected problem (an
    empty pak). With `real`, FAB_CHECK_PACK from the user's vault (only read), which must
    convert cleanly."""
    if not os.path.isdir(os.path.join(ROOT, 'riftlord', 'fab')):
        return True, 'riftlord.fab not in this tree yet: skipped'
    env = _fab_env(run_dir)
    if real:
        got = subprocess.run([server_bin, '--fab', 'vault', '--json'], capture_output=True,
                             text=True, env=env, timeout=1800)
        items = (_fab_json(got) or {}).get('items') or []
        pack = next((i['path'] for i in items if i.get('item') == FAB_CHECK_PACK), None)
        if not pack:
            return True, '%s not in this vault: skipped' % FAB_CHECK_PACK, True
        extra = []
    else:
        root = fake_vault(run_dir)
        pack, extra = os.path.join(root, FAKE_VAULT_ITEM), ['--root', root]
    t = time.time()
    got = subprocess.run([server_bin, '--fab', 'convert', pack, '--out',
                          os.path.join(run_dir, 'fab-library'), '--jobs', '2', '--json'] + extra,
                         capture_output=True, text=True, env=env, timeout=1800)
    res = _fab_json(got)
    if res is None:
        return False, 'exit %d, no JSON: %s' % (got.returncode, (got.stdout + got.stderr)[-300:])
    problems = res.get('problems') or []
    if real:
        ok = got.returncode == 0 and res.get('ok') and os.path.isfile(res.get('pak') or '')
    else:
        ok = len(problems) == 1 and 'expected one mount folder' in problems[0]
    return ok, 'exit %d in %.0f s (%s); %d problem(s)%s; pak %s bytes' % (
        got.returncode, time.time() - t, 'real pack' if real else 'fake pack', len(problems),
        (': ' + problems[0][:120]) if problems and not ok else '',
        (res.get('sizes') or {}).get('pak_bytes'))


def check_notices(stage):
    """The build's THIRD-PARTY-NOTICES.txt.json (tools/notices.py): no problems; the
    components counted by kind, and the waived files named."""
    import collections
    try:
        with open(os.path.join(stage, 'THIRD-PARTY-NOTICES.txt.json')) as fh:
            got = json.load(fh)
    except (OSError, ValueError) as exc:
        return False, 'no notices summary: %s' % exc
    kinds = collections.Counter(c['kind'] for c in got['components'])
    detail = '%d components (%s)' % (len(got['components']), ', '.join(
        '%d %s' % (n, k) for k, n in sorted(kinds.items())))
    if got.get('waived'):
        detail += '; waived: %s' % ', '.join(w['file'] for w in got['waived'])
    if got['problems']:
        detail += '; problems: %s' % ' | '.join(got['problems'][:5])
    return not got['problems'], detail, bool(got.get('waived'))


def check_catalogue(srv):
    cat = srv.get('/api/catalogue', tool='riftlord')
    rows = cat if isinstance(cat, list) else next((v for v in cat.values() if isinstance(v, list)), [])
    kinds = {}
    for r in rows:
        if isinstance(r, dict):
            kinds[r.get('kind')] = kinds.get(r.get('kind'), 0) + 1
    fx = kinds.get('fx', 0)
    return fx > 0, '%d rows, %d fx; %s' % (len(rows), fx, kinds)


def check_materials(srv):
    mats = srv.get('/api/materials', tool='riftlord')
    n = len(mats) if isinstance(mats, (list, dict)) else 0
    return n > 0, '%d materials' % n


def check_plan_round_trip(srv, rec):
    level = rec.get('level')
    plan = rec.get('plan') or {}

    def fx(p):
        return [a for a in p.get('added', []) if isinstance(a, dict) and a.get('kind') == 'fx']
    look = plan.get('look') or {}
    srv.post('/api/plan/%s' % level, plan, tool='riftlord')
    back = srv.get('/api/plan/%s' % level, tool='riftlord')
    bl = back.get('look') or {}
    same_fx = [a.get('fx') for a in fx(back)] == [a.get('fx') for a in fx(plan)]
    same_paints = bl.get('paints') == look.get('paints')
    return same_fx and same_paints, 'fx items %d -> %d (%s), paints %d -> %d (%s)' % (
        len(fx(plan)), len(fx(back)), 'same' if same_fx else 'DIFFERENT',
        len(look.get('paints') or []), len(bl.get('paints') or []),
        'same' if same_paints else 'DIFFERENT')


def _image_ok(v):
    return isinstance(v, str) and v.startswith('data:image/') and len(v) > 1000


def check_live_preview(srv, ident):
    opts = {'ground': 6, 'flyers': 1}
    p = srv.post('/api/bucket/preview', {'id': ident, 'options': opts}, timeout=PREVIEW_TIMEOUT)
    ok = _image_ok(p.get('image')) and p.get('options') == opts
    return ok, 'options %s, image %d chars, showing %s' % (
        p.get('options'), len(p.get('image') or ''), ', '.join(p.get('showing') or []))


#: Map Settings' minimap preview (riftlord.minimapredraw.preview): the quick first
#: picture and the full one of a shipped level, each within its limit, in seconds; and
#: the record's own (Regenerate, cut at doors, on the test mission) within
#: MINIMAP_RECORD_LIMIT. The worst case: empty
#: caches (every mesh pulled out of the paks) on a machine other jobs load. Measured
#: idle: quick 5 s, full 40-49 s, Beset 6-11 s; the limits leave room for load.
MINIMAP_QUICK_LIMIT = 30
MINIMAP_FULL_LIMIT = 180
MINIMAP_RECORD_LIMIT = 120
MINIMAP_LEVEL = 'Scenario_13'


def check_minimap_preview(srv, rec):
    """The panel's minimap preview completes in the frozen app: a shipped level
    regenerated (quick, then full, cold caches), then the record's own plan."""
    def ask(level, plan, quick, limit):
        t = time.time()
        got = srv.post('/api/minimap/' + level, {'plan': plan, 'quick': quick},
                       tool='riftlord', timeout=limit)
        secs = time.time() - t
        ok = not got.get('error') and _image_ok(got.get('image')) and secs <= limit
        return ok, '%s %s %.1fs%s' % (level, 'quick' if quick else 'full', secs,
                                      (' error: %s' % got['error']) if got.get('error') else '')
    plan = {'level': MINIMAP_LEVEL, 'minimap': {'mode': 'regenerate'}}
    scale = load_scale()                              # the limits are for an idle machine
    rows = [ask(MINIMAP_LEVEL, plan, True, MINIMAP_QUICK_LIMIT * scale),
            ask(MINIMAP_LEVEL, plan, False, MINIMAP_FULL_LIMIT * scale)]
    rplan = (rec or {}).get('plan') or {}
    rlevel = (rec or {}).get('level') or rplan.get('level')
    if rlevel:
        rows.append(ask(rlevel, rplan, False, MINIMAP_RECORD_LIMIT * scale))
    return all(r[0] for r in rows), '; '.join(r[1] for r in rows)


def check_minimap_gate():
    """Regenerate's scores against its baseline (riftlord.minimapscore), read from the
    last harness run without drawing; a warning, not a failure, when that run is older
    than the drawing code."""
    from riftlord.build.minimap import minimapscore
    ok, detail, fresh = minimapscore.gate_from_state()
    if not fresh:
        return True, detail + ' (run python3 -m riftlord.minimapscore)', True
    return ok, detail


def ffprobe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                          'format=format_name,duration:stream=codec_name,sample_rate,channels',
                          '-of', 'json', path], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        return None, out.stderr.strip()[:200]
    return json.loads(out.stdout), ''


def check_music(srv, track, run_dir, cache_root):
    st, ctype, blob = srv.get('/api/music/' + track, tool='riftlord', raw=True)
    out = os.path.join(run_dir, 'music-%s.ogg' % track)
    with open(out, 'wb') as fh:
        fh.write(blob)
    info, err = ffprobe(out)
    os.remove(out)
    if info is None:
        return False, 'HTTP %s %s, %d bytes; ffprobe: %s' % (st, ctype, len(blob), err)
    fmt = info.get('format') or {}
    stream = (info.get('streams') or [{}])[0]
    cached = []
    for base, _d, files in os.walk(cache_root):
        cached += [os.path.relpath(os.path.join(base, f), cache_root) for f in files
                   if f.endswith('.ogg')]
    ok = st == 200 and fmt.get('format_name') == 'ogg' and any('RiftLord' in c for c in cached)
    return ok, '%s %d bytes, %s %s Hz %s ch %.1f s; cached %s' % (
        ctype, len(blob), stream.get('codec_name'), stream.get('sample_rate'),
        stream.get('channels'), float(fmt.get('duration') or 0), ', '.join(cached) or 'NOWHERE')


# -- 4. UI smoke -----------------------------------------------------------------------

def load_scale():
    """The UI suite's timeout factor: the 1-minute load average per half of the CPUs,
    between 1 and UI_SCALE_MAX."""
    try:
        load = os.getloadavg()[0]
    except OSError:
        return 1.0
    return round(min(UI_SCALE_MAX, max(1.0, load / max(1, (os.cpu_count() or 2) / 2))), 2)


def ui_budget(scale):
    """Seconds the whole suite may take: its cases' budgets (ui_smoke.js CASE_TIMEOUT)
    times `scale`."""
    with open(os.path.join(ROOT, 'tests', 'ui_smoke', 'ui_smoke.js')) as fh:
        text = fh.read()
    table = re.search(r'const CASE_TIMEOUT = \{([^}]*)\}', text, re.S)
    total = sum(int(v) for v in re.findall(r':\s*(\d+)', table.group(1))) if table else UI_TIMEOUT
    return int(total * scale) + 120


#: UI cases that run on test servers of their own, beside the rest of the suite, one after
#: another: {group: (cases, the server's extra environment, {name: run-folder subfolder})}.
SEPARATE = {'reference': (['reference'], {}),
            # stand-in mod paks the case writes (tests/ui_smoke/fakemods.py), never the game's
            'supportedmods': (['supportedmods'], {'RIFTLORD_SUPPORTED_PAKS': 'fake-paks'}),
            # a stand-in Scramble tier mod the case writes (tests/ui_smoke/faketiers.py)
            'scrambletiers': (['scrambletiers'], {'RIFTLORD_SUPPORTED_PAKS': 'fake-tier-paks'}),
            # the Setup page's first run: empty type data and terrain kit folders of its own
            # (a gate gives the release check prepared type data in RIFTLORD_TYPES)
            'setup': (['setup'], {'RIFTLORD_TYPES': 'setup-types',
                                  'RIFTLORD_TERRAIN_LIBRARY': 'setup-terrain'})}
SEPARATE_CASES = [c for cases, _env in SEPARATE.values() for c in cases]


def trace_cache(cold):
    """A trace cache kept between runs, one per trace FORMAT_VERSION and TEXTURED_VERSION
    (riftlord.mapeditor.trace), or None for `cold` (a fresh one, as a new user has)."""
    if cold:
        return None
    with open(os.path.join(ROOT, 'riftlord', 'mapeditor', 'trace.py')) as fh:
        text = fh.read()
    versions = [re.search(r'^%s = (\d+)' % k, text, re.M) for k in ('FORMAT_VERSION', 'TEXTURED_VERSION')]
    tag = 'f%s-t%s' % tuple(v.group(1) if v else 'x' for v in versions)
    return os.path.join(testservers.SCRATCH, 'trace-cache', tag)


#: The test servers' type data, kept between runs (riftlord.types.setup --prepare keys it).
TYPES_CACHE = os.path.join(testservers.SCRATCH, 'types-cache')


def prepare_types():
    """The keyed folder of prepared type data for test servers (RIFTLORD_TYPES), or None."""
    got = subprocess.run([sys.executable, '-B', '-m', 'riftlord.types.setup', '--prepare',
                          TYPES_CACHE], cwd=ROOT, capture_output=True, text=True, timeout=3600)
    try:
        return json.loads(got.stdout.strip().splitlines()[-1])['folder']
    except (ValueError, IndexError, KeyError):
        print('  types --prepare failed: %s' % (got.stdout + got.stderr).strip()[-300:], flush=True)
        return None


def suite_cases():
    """The UI smoke suite's cases in its order (ui_smoke.js CASES)."""
    with open(os.path.join(ROOT, 'tests', 'ui_smoke', 'ui_smoke.js')) as fh:
        text = fh.read()
    body = re.search(r'^const CASES = \{(.*?)\}', text, re.S | re.M).group(1)
    return [item.split(':')[0].strip() for item in body.split(',') if item.strip()]


def last_seconds():
    """{case: seconds} from the newest release check report that ran UI cases, or {}."""
    import glob
    for path in sorted(glob.glob(os.path.join(RUNS, '*', 'report.json')), reverse=True)[:20]:
        try:
            with open(path) as fh:
                rows = json.load(fh).get('checks') or []
        except (OSError, ValueError):
            continue
        got = {r['check'][4:]: r['seconds'] for r in rows
               if r['check'].startswith('ui: ') and r.get('seconds')}
        if len(got) > 5:
            return got
    return {}


#: Seconds the endpoint checks take before the first group can start on the main server.
ENDPOINT_SECONDS = 70


def ui_groups(cases, n, seconds=None):
    """`cases` split into `n` groups of about equal time (longest first onto the least
    loaded group; group 0 starts ENDPOINT_SECONDS late), each in suite order. A case
    without a time counts 30 s."""
    seconds = last_seconds() if seconds is None else seconds
    order = {c: i for i, c in enumerate(cases)}
    loads, groups = [ENDPOINT_SECONDS] + [0] * (n - 1), [[] for _ in range(n)]
    for case in sorted(cases, key=lambda c: -(seconds.get(c) or 30)):
        i = loads.index(min(loads))
        loads[i] += seconds.get(case) or 30
        groups[i].append(case)
    return [sorted(g, key=order.get) for g in groups if g]


def ui_lane(report, server_bin, ident, run_dir, env, cases, name):
    """`cases` on a frozen test server of their own (an extra UI group)."""
    srv = testservers.TestServer('workshop', frozen=server_bin, missions=[ident], env=env,
                                 log=os.path.join(run_dir, 'server-%s.log' % name))
    t = time.time()
    try:
        srv.start()
    except Exception as exc:                          # noqa: BLE001
        report.add('ui: %s (own server)' % name, False, str(exc)[:300], time.time() - t)
        return
    try:
        ui_smoke(report, srv, ident, run_dir, cases=cases, name=name)
    finally:
        OWN_NAMES.update(own_names(srv.dirs['Missions']))
        srv.stop()


def ui_separate(report, server_bin, ident, run_dir, env, chosen=None):
    """Each SEPARATE group (with a case in `chosen`, all when None) on a frozen test server
    of its own, one after another; an environment value naming a folder is made inside the
    run folder."""
    for group, (cases, extra) in SEPARATE.items():
        cases = [c for c in cases if chosen is None or c in chosen]
        if not cases:
            continue
        own_env = dict(env)
        for key, sub in extra.items():
            if sub is None:
                own_env.pop(key, None)
                continue
            own_env[key] = os.path.join(run_dir, sub)
            os.makedirs(own_env[key], exist_ok=True)
        srv = testservers.TestServer('workshop', frozen=server_bin, missions=[ident], env=own_env,
                                     log=os.path.join(run_dir, 'server-%s.log' % group))
        t = time.time()
        try:
            srv.start()
        except Exception as exc:                      # noqa: BLE001
            report.add('ui: %s (own server)' % ','.join(cases), False, str(exc)[:300],
                       time.time() - t)
            continue
        try:
            ui_smoke(report, srv, ident, run_dir, cases=cases, name='ui_' + group)
        finally:
            OWN_NAMES.update(own_names(srv.dirs['Missions']))
            srv.stop()


def ui_smoke(report, srv, ident, run_dir, cases=None, exclude=(), name='ui_smoke'):
    out = os.path.join(run_dir, name + '.json')
    log = os.path.join(run_dir, name + '.log')
    scale = load_scale()
    report.info['ui_timeout_scale'] = scale
    t = time.time()
    picks = (['--cases', ','.join(cases)] if cases else []) + \
        (['--except', ','.join(exclude)] if exclude else [])
    from riftlord.core import gpulock
    t_gpu = time.time()
    with gpulock.turn(name, lambda m: print('  ' + m, flush=True)):   # WebGL on the GPU
        report.info.setdefault('gpu_wait', {})[name] = round(time.time() - t_gpu)
        code = run_logged(['node', os.path.join(ROOT, 'tests', 'ui_smoke', 'ui_smoke.js'),
                           '--shell', srv.urls['shell'], '--mission', ident, '--json', out] + picks,
                          log, dict(scratch_env(run_dir), RL_MISSIONS=srv.dirs['Missions'],
                                    RL_TIMEOUT_SCALE=str(scale)),
                          timeout=ui_budget(scale))
    testservers.cleanup(verbose=False)                # a browser the killed suite left
    try:
        with open(out) as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        report.add('ui smoke', False, 'exit %s, no report; %s' % (code, ' | '.join(tail(log, 3))),
                   time.time() - t)
        return
    for r in got['results']:
        steps = '; '.join(s[5:] for s in r['steps'])
        detail = ('%s -- %s' % (r['error'], steps)) if r['error'] else steps
        if r['pageErrors']:
            detail += ' | page errors: ' + ' / '.join(r['pageErrors'])[:300]
        report.add('ui: %s' % r['case'], r['ok'], detail, r['seconds'])
        if r.get('timingOnly'):                      # failed only on a timing limit
            report.info.setdefault('ui_timing_only', {})[r['case']] = dict(
                r['timing'], started=r.get('started'))


# -- 5. bake and pak diff ----------------------------------------------------------------

def bake_frozen(srv, ident, run_dir):
    srv.post('/api/bucket/build', {'ids': [ident]})
    t0 = time.time()
    stages, last = [], None
    while time.time() - t0 < BAKE_TIMEOUT:
        time.sleep(2)
        st = srv.get('/api/bucket/build')
        stage = (st.get('stages') or {}).get(ident)
        if stage and stage != last:
            stages.append('%s@%ds' % (stage, time.time() - t0))
            last = stage
        job = st.get('job') or {}
        if not job.get('running', True):
            break
    else:
        return None, 'timed out after %d s; stages %s' % (BAKE_TIMEOUT, stages)
    if job.get('failed') or ident not in job.get('built', []):
        return None, 'build failed: %s; stages %s' % (job.get('failed'), stages)
    zpath = os.path.join(run_dir, 'frozen-%s.zip' % ident)
    srv.post('/api/bucket/package', {'ids': [ident], 'path': zpath})
    out = os.path.join(run_dir, 'frozen-pak')
    with zipfile.ZipFile(zpath) as z:
        paks = [n for n in z.namelist() if n.endswith('.pak')]
        z.extractall(out, paks)
    return os.path.join(out, paks[0]), 'stages %s' % ', '.join(stages)


SOURCE_BUILD = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from riftlord.missions import bucket
bucket.activate()
got = bucket.build([sys.argv[2]])
print('BUILD', json.dumps(got), flush=True)
if got['built']:
    print('PACKAGE', json.dumps(bucket.package([sys.argv[2]], sys.argv[3])), flush=True)
'''


def bake_source(ident, run_dir):
    root = os.path.join(run_dir, 'source-build')
    dirs = {k: os.path.join(root, k) for k in ('data', 'cache', 'config', 'Missions', 'tmp')}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    testservers.copy_mission(ident, dirs['Missions'], built=False)
    env = dict(os.environ, XDG_DATA_HOME=dirs['data'], XDG_CACHE_HOME=dirs['cache'],
               XDG_CONFIG_HOME=dirs['config'], OMD3_BUCKET=dirs['Missions'], TMPDIR=dirs['tmp'],
               PYTHONDONTWRITEBYTECODE='1')
    zpath = os.path.join(run_dir, 'source-%s.zip' % ident)
    log = os.path.join(run_dir, 'source-build.log')
    code = run_logged(NICE + [sys.executable, '-B', '-c', SOURCE_BUILD, ROOT, ident, zpath],
                      log, env, timeout=BAKE_TIMEOUT)
    shutil.rmtree(root, ignore_errors=True)           # scratch Missions and caches; the zip stays
    if code != 0 or not os.path.exists(zpath):
        return None, 'source build exit %s; %s' % (code, ' | '.join(tail(log, 3)))
    out = os.path.join(run_dir, 'source-pak')
    with zipfile.ZipFile(zpath) as z:
        paks = [n for n in z.namelist() if n.endswith('.pak')]
        z.extractall(out, paks)
    return os.path.join(out, paks[0]), 'built from source'


def compare_paks(frozen_pak, source_pak, run_dir):
    import pakinspect
    a = pakinspect.inspect(frozen_pak)
    b = pakinspect.inspect(source_pak)
    d = pakinspect.diff(a, b)
    with open(os.path.join(run_dir, 'pak-diff.json'), 'w') as fh:
        json.dump({'frozen': pakinspect.public(a), 'source': pakinspect.public(b), 'diff': d},
                  fh, indent=1)
    problems = a['registry_problems'] + b['registry_problems']
    fx_same = not any('fx' in v for v in d['levels'].values())
    ok = d['same_layout'] and not problems and fx_same and 'preview' not in d
    detail = '%d vs %d entries; only frozen %s; only source %s; fx %s; preview %s; ' \
             'large size changes %d; registry problems %s' % (
                 a['entries'], b['entries'], d['only_in_this'][:4], d['only_in_other'][:4],
                 'same' if fx_same else 'DIFFERENT',
                 'same' if 'preview' not in d else 'DIFFERENT', len(d['size_changed']),
                 problems or 'none')
    return ok, detail


# -- 8. Windows under Wine --------------------------------------------------------------

WIN_PARENT = r'''
"""Start the frozen Windows server as the window does (stdin a Windows pipe); close the
pipe when the stop file appears, and report whether the server stopped."""
import os, subprocess, sys, time
exe, log, stop = sys.argv[1:4]
with open(log, 'wb') as out:
    p = subprocess.Popen([exe, '--sidecar'], stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT)
    while not os.path.exists(stop) and p.poll() is None:
        time.sleep(1)
    p.stdin.close()
    t = time.time()
    try:
        print('SERVER_EXIT', p.wait(30), round(time.time() - t, 1), flush=True)
    except subprocess.TimeoutExpired:
        print('SERVER_STUCK', flush=True)
        p.kill()
'''


def winpath(path):
    return 'Z:' + os.path.abspath(path).replace('/', '\\')


def windows_checks(report, exe, ident, run_dir, track):
    if not os.path.exists(WIN_PYTHON):
        report.add('wine: server', False, 'no Windows Python at %s (build with --windows once)' % WIN_PYTHON)
        return
    root = os.path.join(run_dir, 'wine')
    missions = os.path.join(root, 'Missions')
    os.makedirs(missions, exist_ok=True)
    testservers.copy_mission(ident, missions)
    parent = os.path.join(root, 'winparent.py')
    with open(parent, 'w') as fh:
        fh.write(WIN_PARENT)
    log, stop, out = (os.path.join(root, n) for n in ('server.log', 'stop', 'parent.log'))
    # a clean server cache each run: the test prefix holds nothing else of value
    for sub in ('Local', 'Roaming'):
        for app in ('RiftLord', 'OnslaughtEditor'):
            for users in [os.path.join(WIN_TEST_PREFIX, 'drive_c', 'users')]:
                for u in (os.listdir(users) if os.path.isdir(users) else []):
                    shutil.rmtree(os.path.join(users, u, 'AppData', sub, app), ignore_errors=True)
    os.makedirs(WIN_TEST_PREFIX, exist_ok=True)
    install = os.path.normpath(os.path.join(WIN64, '..', '..', '..'))
    env = dict(scratch_env(run_dir), WINEPREFIX=WIN_TEST_PREFIX, WINEDEBUG='err+all,fixme-all',
               WINEDLLOVERRIDES='mscoree,mshtml=', OMD3_INSTALL=winpath(install),
               OMD3_BUCKET=winpath(missions))
    fh = open(out, 'wb')
    p = subprocess.Popen(NICE + ['wine', winpath(WIN_PYTHON), '-B', winpath(parent), winpath(exe),
                                 winpath(log), winpath(stop)],
                         env=env, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    _GROUPS.append(p.pid)
    t0 = time.time()
    try:
        shell = None
        while time.time() - t0 < WINE_READY_TIMEOUT and p.poll() is None:
            for line in tail(log, 50):
                if 'RIFTLORD_READY http://' in line:
                    shell = 'http://' + line.split('http://', 1)[1].split()[0].rstrip('/')
            if shell:
                break
            time.sleep(1)
        if not report.add('wine: server ready', bool(shell),
                          shell or 'no RIFTLORD_READY; %s' % ' | '.join(tail(log, 4)), time.time() - t0):
            return
        srv = testservers.TestServer.__new__(testservers.TestServer)
        srv.kind, srv.urls = 'workshop', {'shell': shell}
        for name, u in srv.get('/api/tools')['tools'].items():
            srv.urls[name] = u.rstrip('/')
        report.add('wine: /api/tools', True, ', '.join('%s %s' % kv for kv in sorted(srv.urls.items())))
        timed(report, 'wine: native tools unavailable, not a crash', check_native, srv, True)
        timed(report, 'wine: onslaught first load', check_onslaught_first_load, srv)
        timed(report, 'wine: live preview POST', check_live_preview, srv, ident)
        timed(report, 'wine: music', check_music, srv, track, run_dir,
              os.path.join(WIN_TEST_PREFIX, 'drive_c', 'users'))
    finally:
        open(stop, 'w').close()
        t = time.time()
        try:
            p.wait(WINE_STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass
        fh.close()
        said = ' '.join(tail(out, 40))
        stuck = said.count('RtlpWaitForCriticalSection') + ' '.join(tail(log, 400)).count(
            'RtlpWaitForCriticalSection')
        if 'SERVER_EXIT' in said:
            parts = said.split('SERVER_EXIT', 1)[1].split()
            report.add('wine: clean stop (pipe closed)', parts[0] == '0',
                       'exit %s %s s after the pipe closed; critical-section waits %d'
                       % (parts[0], parts[1], stuck), time.time() - t)
        elif shell:
            report.add('wine: clean stop (pipe closed)', False,
                       'did not stop: %s' % said[-200:], time.time() - t)
        _kill_group(p.pid)
        _GROUPS.remove(p.pid)
        subprocess.run(['wineserver', '-k'], env=dict(os.environ, WINEPREFIX=WIN_TEST_PREFIX),
                       capture_output=True, timeout=60)
        shutil.rmtree(missions, ignore_errors=True)   # the record's copy; logs stay


# -- 9. isolation -----------------------------------------------------------------------

def snapshot(*roots):
    out = {}
    for root in roots:
        for base, _dirs, files in os.walk(root):
            for f in files:
                p = os.path.join(base, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out[p] = (st.st_size, st.st_mtime_ns)
    return out


#: Stems of everything this run's server made in its scratch Missions (records, builds,
#: saved-as missions, campaigns), filled in before it stops: a change in the user's data or
#: ~mods counts against this run only when the file carries one of these names.
OWN_NAMES = set()


def own_names(missions):
    """Stems of the files and folders in a Missions folder and its records."""
    out = set()
    for folder in (missions, os.path.join(missions, '.riftlord', 'records')):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        out |= {n.split('.')[0] for n in names if n and not n.startswith('.')}
    return out


def _ours(path):
    return any(os.path.basename(path).startswith(stem) or (os.sep + stem + os.sep) in path
               for stem in OWN_NAMES)


def compare_snapshots(before, after, user_app_running):
    every = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    changed = [p for p in every if _ours(p)]
    others = [p for p in every if not _ours(p)]
    detail = 'none of this run\'s %d names changed (%d files watched)' % (
        len(OWN_NAMES), len(before)) if not changed else \
        '%d changed: %s' % (len(changed), ', '.join(changed[:4]))
    if others:
        detail += '; changed by others (not this run\'s names): %s' % ', '.join(
            os.path.basename(p) for p in others[:4])
    if changed and user_app_running:
        return True, detail + ' -- the user\'s own Rift Lord holds port 8870, so it may have', True
    return not changed, detail


# -- 10. install --------------------------------------------------------------------------

def install(report, stage, files):
    done = []
    for name in files:
        src = os.path.join(stage, name)
        dst = os.path.join(DIST, name)
        if not os.path.exists(src):
            continue
        if os.path.exists(dst) and not name.endswith('.txt'):
            stamp = datetime.datetime.fromtimestamp(os.path.getmtime(dst)).strftime('%Y-%m-%d-%H%M')
            base, ext = (name[:-4], '.exe') if name.endswith('.exe') else (name, '')
            keep = os.path.join(DIST, '%s-%s%s' % (base, stamp, ext))
            n = 2
            while os.path.exists(keep):
                keep = os.path.join(DIST, '%s-%s-%d%s' % (base, stamp, n, ext))
                n += 1
            shutil.copy2(dst, keep)
            done.append('%s kept as %s' % (name, os.path.basename(keep)))
        part = dst + '.part'
        shutil.copy2(src, part)
        os.replace(part, dst)                       # works while the old one is running
        done.append('%s installed' % name)
    report.add('install into %s' % DIST, True, '; '.join(done))


#: Kept copies of each installed binary left in the dist folder; older ones are deleted.
KEEP_COPIES = 2
#: A kept copy's name: the binary's, then -<date>[-<hhmm>][-<n>], then its extension.
KEPT_RE = re.compile(r'^(RiftLord|RiftLordServer)-\d{4}-\d{2}-\d{2}(?:-\d{4})?(?:-\d+)?(\.exe)?$')


def prune_kept(keep=KEEP_COPIES, dist=DIST):
    """Delete all but the newest `keep` kept copies of each binary in `dist`. The
    installed binaries themselves are never touched. Returns the names deleted."""
    groups = {}
    for name in os.listdir(dist):
        m = KEPT_RE.match(name)
        path = os.path.join(dist, name)
        if m and os.path.isfile(path):
            groups.setdefault((m.group(1), m.group(2) or ''), []).append(path)
    gone = []
    for paths in groups.values():
        paths.sort(key=os.path.getmtime, reverse=True)
        for path in paths[keep:]:
            os.remove(path)
            gone.append(os.path.basename(path))
    return sorted(gone)


# -- main ---------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--skip-build', action='store_true',
                    help='check the builds already in the dist folder')
    ap.add_argument('--windows', action='store_true', help='build and check the Windows pair too')
    ap.add_argument('--install', action='store_true',
                    help='install the new build into the dist folder when every check passed')
    ap.add_argument('--bake', metavar='RECORD',
                    help='also build RECORD in the frozen server and from source, and diff the paks')
    ap.add_argument('--saves', action='store_true',
                    help='also run the onslaught save suite (riftlord.onslaught.selftest --saves)')
    ap.add_argument('--record', default=DEFAULT_RECORD,
                    help='the Missions record the checks use (default %s)' % DEFAULT_RECORD)
    ap.add_argument('--music', default=DEFAULT_TRACK, help='music track to fetch')
    ap.add_argument('--no-ui', action='store_true', help='skip the UI smoke suite')
    ap.add_argument('--cases', help='only these UI smoke cases (comma list; empty: none)')
    ap.add_argument('--from-build', metavar='DIR',
                    help='check the build in DIR (another run\'s staged build) without building')
    ap.add_argument('--ui-only', action='store_true',
                    help='only the UI smoke cases (and the server start and stop, isolation): '
                         'no endpoint checks')
    ap.add_argument('--ui-groups', type=int, default=1,
                    help='split the main UI suite into this many browser groups, each on a '
                         'server of its own (default 1)')
    ap.add_argument('--fab-real', action='store_true',
                    help='the frozen Fab convert check converts %s from the user\'s own vault '
                         '(only read; minutes on a fresh cache) instead of a fake pack'
                         % FAB_CHECK_PACK)
    ap.add_argument('--cold', action='store_true',
                    help='start the trace cache empty, as a new user has it (when trace '
                         'readers change)')
    ap.add_argument('--prune', action='store_true',
                    help='only delete all but the newest %d kept copies of each binary in '
                         'the dist folder, and list them' % KEEP_COPIES)
    args = ap.parse_args(argv)
    if args.prune:
        gone = prune_kept()
        print('\n'.join('deleted %s' % g for g in gone) or 'nothing to delete')
        return 0
    if args.from_build:
        args.skip_build = True
    if args.install and args.skip_build:
        ap.error('--install installs a new build; it cannot go with --skip-build')

    testservers.install_signal_handlers()
    testservers.cleanup(verbose=True)                  # what a killed run left
    stamp = time.strftime('%Y-%m-%d-%H%M%S')
    run_dir = os.path.join(RUNS, stamp)
    os.makedirs(run_dir, exist_ok=True)
    report = Report(run_dir)
    report.info.update(started=stamp, args=vars(args), run_dir=run_dir)
    stage = args.from_build or (DIST if args.skip_build else os.path.join(run_dir, 'dist'))
    print('release check: %s\n  logs and report in %s' % (stamp, run_dir), flush=True)
    t_all = time.time()
    try:
        with open(os.path.join(testservers.FIXTURE_MISSIONS, '.riftlord', 'records',
                               args.record + '.json')) as fh:
            rec = json.load(fh)
        watched = [USER_DATA, MODS]
        before = snapshot(*watched)
        built = True
        if not args.skip_build:
            built = build(report, stage, run_dir, args.windows)
            timed(report, 'notices: every bundled component has an allowed licence',
                  check_notices, stage)
        server_bin = os.path.join(stage, 'RiftLordServer')
        if built:
            missions = [args.record] + ([args.bake] if args.bake and args.bake != args.record else [])
            cache = trace_cache(args.cold)
            trace_env = {'RIFTLORD_TRACE_CACHE': cache} if cache else {}
            # the UI case `fab` against the stand-in converter, never the real one
            trace_env['RIFTLORD_FABCONVERT'] = FAKE_FAB
            trace_env['RIFTLORD_TERRAINKIT'] = FAKE_TERRAIN
            t = time.time()
            types = os.environ.get('RIFTLORD_TYPES') or prepare_types()   # the gate's, or ours
            report.add('types: test servers\' type data', bool(types), types or 'prepare failed',
                       time.time() - t)
            if types:
                trace_env['RIFTLORD_TYPES'] = types
            report.info['trace_cache'] = cache or 'cold'
            srv = testservers.TestServer('workshop', frozen=server_bin, missions=missions,
                                         env=trace_env, log=os.path.join(run_dir, 'server.log'))
            t = time.time()
            try:
                srv.start()
            except Exception as exc:                  # noqa: BLE001
                report.add('server: start (--sidecar)', False, str(exc)[:300], time.time() - t)
                srv = None
            if srv is not None:
                user_app = not srv.urls['shell'].endswith(':8870')
                report.add('server: start (--sidecar)', True, 'shell %s%s' % (
                    srv.urls['shell'], ' (8870 is taken: another Rift Lord is running)' if user_app else ''),
                    time.time() - t)
                threads, groups = [], []
                try:
                    chosen = None if args.cases is None else [
                        c.strip() for c in args.cases.split(',') if c.strip()]
                    if not args.no_ui:
                        # the separate groups and UI groups 1.. start now, on servers of their
                        # own; group 0 runs on this server after the endpoint checks
                        main_cases = [c for c in suite_cases() if c not in SEPARATE_CASES and
                                      (chosen is None or c in chosen)]
                        groups = ui_groups(main_cases, max(1, args.ui_groups)) if main_cases else []
                        report.info['ui_groups'] = groups
                        if [c for c in SEPARATE_CASES if chosen is None or c in chosen]:
                            threads.append(threading.Thread(target=ui_separate, args=(
                                report, server_bin, args.record, run_dir, trace_env, chosen)))
                        for i, group in enumerate(groups[1:], 1):
                            threads.append(threading.Thread(target=ui_lane, args=(
                                report, server_bin, args.record, run_dir, trace_env, group,
                                'ui_group%d' % i)))
                        for th in threads:
                            th.start()
                    if not args.ui_only:
                        timed(report, 'onslaught: first load', check_onslaught_first_load, srv)
                        timed(report, 'api: tools', check_tools, srv)
                        timed(report, 'api: native tools', check_native, srv)
                        timed(report, 'fab: frozen --fab vault --json', check_fab_frozen, server_bin,
                              run_dir)
                        timed(report, 'fab: frozen convert with workers', check_fab_convert_frozen,
                              server_bin, run_dir, args.fab_real)
                        timed(report, 'map editor: catalogue fx rows', check_catalogue, srv)
                        timed(report, 'map editor: materials', check_materials, srv)
                        timed(report, 'map editor: plan round trip', check_plan_round_trip, srv, rec)
                        timed(report, 'missions: live preview POST', check_live_preview, srv, args.record)
                        timed(report, 'map editor: minimap preview completes', check_minimap_preview,
                              srv, rec)
                        timed(report, 'map editor: music', check_music, srv, args.music, run_dir,
                              srv.dirs['cache'])
                    if groups:
                        ui_smoke(report, srv, args.record, run_dir, cases=groups[0])
                    for th in threads:
                        th.join()
                    if args.bake:
                        t = time.time()
                        frozen_pak, why = bake_frozen(srv, args.bake, run_dir)
                        report.add('bake: frozen build + package', frozen_pak is not None, why, time.time() - t)
                        if frozen_pak:
                            t = time.time()
                            source_pak, why = bake_source(args.bake, run_dir)
                            report.add('bake: source build + package', source_pak is not None, why,
                                       time.time() - t)
                            if source_pak:
                                timed(report, 'bake: pak diff + registry', compare_paks,
                                      frozen_pak, source_pak, run_dir)
                finally:
                    for th in threads:
                        if th.ident:
                            th.join()
                    OWN_NAMES.update(own_names(srv.dirs['Missions']))
                    t = time.time()
                    code = srv.stop()
                    report.add('server: clean stop (stdin closed)', code == 0,
                               'exit %s' % code if code is not None else 'had to be killed',
                               time.time() - t)
        if not args.ui_only:
            timed(report, 'map editor: minimap scores against the baseline', check_minimap_gate)
        if args.saves:
            log = os.path.join(run_dir, 'saves.log')
            t = time.time()
            code = run_logged(NICE + [sys.executable, '-B', '-m', 'riftlord.onslaught.selftest', '--saves'],
                              log, scratch_env(run_dir), timeout=SAVES_TIMEOUT)
            summary = [line for line in tail(log, 40) if 'passed' in line and 'failed' in line]
            fails = [line.strip() for line in tail(log, 400) if line.startswith('FAIL')]
            report.add('onslaught save suite', code == 0, '; '.join(summary[-1:] + fails[:3]) or
                       'exit %s; %s' % (code, ' | '.join(tail(log, 3))), time.time() - t)
        if args.windows and built:
            exe = os.path.join(stage, 'RiftLordServer.exe')
            if os.path.exists(exe):
                windows_checks(report, exe, args.record, run_dir, args.music)
            else:
                report.add('wine: server', False, 'no %s' % exe)
        after = snapshot(*watched)
        user_app = any('8870 is taken' in r['detail'] for r in report.rows)
        timed(report, 'isolation: user data and ~mods', lambda: compare_snapshots(before, after, user_app))
        if args.install:
            if report.ok():
                install(report, stage, LINUX_FILES + (WINDOWS_FILES if args.windows else []))
                gone = prune_kept()
                report.add('prune kept copies in the dist folder', True,
                           'deleted %s' % ', '.join(gone) if gone else 'nothing to delete')
            else:
                print('  not installing: a check failed', flush=True)
                report.info['install'] = 'refused: a check failed'
    finally:
        for pgid in list(_GROUPS):
            _kill_group(pgid)
        testservers.stop_all()
        shutil.rmtree(os.path.join(run_dir, 'tmp'), ignore_errors=True)
        if args.install and report.ok() and not args.skip_build:
            for f in LINUX_FILES + WINDOWS_FILES:      # installed copies live in dist now
                try:
                    os.remove(os.path.join(stage, f))
                except OSError:
                    pass
        report.info['seconds'] = round(time.time() - t_all, 1)
        path = report.write()
        print('\n' + report.table())
        print('\n%s in %.0f s; report %s' % ('ALL PASSED' if report.ok() else 'FAILED',
                                             time.time() - t_all, path))
    return 0 if report.ok() else 1


def limited_main(argv=None):
    """main() inside a heavy-job slot (riftlord.core.heavyslot), unless a gate holds one."""
    from riftlord.core import heavyslot
    argv = sys.argv[1:] if argv is None else argv
    install = '--install' in argv
    with heavyslot.slot('release check', reserved=install):
        return main(argv)


if __name__ == '__main__':
    raise SystemExit(limited_main())
