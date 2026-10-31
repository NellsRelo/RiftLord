"""The phase gate in one command: offline tests, the release check, and old-vs-new mission
build comparisons, with a PASS/FAIL table at the end.

    python3 tools/gate.py [--install] [--release] [--skip RELEASE,OFFLINE,COMPARE]
                          [--records ID,...] [--windows] [--cold]
    python3 tools/gate.py --only STEP[,STEP]     # named offline steps only ('static' too)
    python3 tools/gate.py --ui CASE[,CASE]       # named UI smoke cases, on a source server
    python3 tools/gate.py --explain [--tree DIR] # what a dev gate would run now, and why

A full run works on a SNAPSHOT of the tree, copied into the run folder at the start (code,
web sources, docs, .sync; no caches, node_modules or dist), so other sessions may keep
editing; gate.json records every snapshot file's sha256. `--install`, when every step
passed, installs the binaries the release check built and tested from that snapshot into
dist/ (each replaced binary kept as <name>-<date>).

**Dev gate (the default):** only the steps the change can affect run (tools/gateselect.py).
An offline step, a comparison or the certify step is skipped while everything it read at
its last pass on this machine is unchanged; a UI case while the files it is tagged with
are; any change to gateselect.SHARED runs everything. The static check, the app build and
the release check's endpoint checks always run. **Release gate (`--release`):** every
step, whatever changed; a release build uses it (`--release --install`). Both keep a
record of each step that passed; the end of the run lists what ran, why, and what was
skipped.

The records compared are the test missions (tests/fixtures/missions), never the user's
Missions; every step gets an empty Missions folder of the run's as OMD3_BUCKET.

The release check (its build first), the offline steps (a pool of OFFLINE_WORKERS, at OFFLINE_NICE) and
the comparisons (in turn) start together after the snapshot. The comparisons whose build
bakes light maps run first and hold the GPU until they are done, so the bake never waits
for the release check's UI suites, which wait for it instead. The release check runs its
main UI suite in UI_GROUPS browser groups (one: more were measured slower), each on a
server of its own. A UI case that failed only on a timing limit (ui_smoke.js checkTime)
while a heavy gate job ran (the offline pool, a comparison or the certify step) is run once
more on the same build after all of them have finished (`rerun after load`, in gate.json
`reruns`, with the jobs' spans under `load`); a second failure stands, and no other failure
is ever run again. A comparison entry declared in buildcompare.EXPECTED for a compared record is certified by
that entry's step instead (minimapscore --gate for Regenerate minimaps), run after the
comparisons; it starts from its own last pass's drawings when nothing they read has changed
(CERTIFY_CACHE). `--only` and `--ui` make a partial run on the
live tree: same logs and gate.json, marked partial, never enough to install. `--ui`
rebuilds a UI first when its sources are newer than its page.
Logs and gate.json go to the run folder printed at the start. Exit 0 when every step passed.
"""

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NEW = os.path.dirname(HERE)
sys.path.insert(0, NEW)
sys.path.insert(0, HERE)

import gateselect                                     # noqa: E402
import testservers                                    # noqa: E402

RUNS = os.path.join(testservers.SCRATCH, 'editor-gate')
#: Offline steps run at once: a quarter of the CPUs.
OFFLINE_WORKERS = max(1, (os.cpu_count() or 4) // 4)
#: Browser groups the release check splits its main UI suite into (RIFTLORD_UI_GROUPS).
#: One: measured 2026-10-29, three groups took 778 s against one group's 740 s (each case
#: ran about 3x slower beside the others) and the mapeditor case failed on timing.
UI_GROUPS = int(os.environ.get('RIFTLORD_UI_GROUPS') or 1)
#: Records compared with their baselines (buildcompare.BASELINES), with the Missions items
#: each also needs: the test missions (tests/fixtures/missions, testservers.FIXTURE_MISSIONS).
RECORDS = {'900_Fixture_Edit_P': [],
           '900_Fixture_Scratch_P': ['900_FixtureCampaign_Campaign_P']}
#: The offline tests, [name, argv] each, in this data file of the tree's (read by
#: load_steps from the tree the gate runs on): outside gateselect.SHARED, so adding or
#: changing one runs that step alone, where a list in this file ran every step.
STEPS_FILE = os.path.join('tests', 'offline', 'steps.json')
#: Offline tests picked up by name when they exist (the Fab converter's, riftlord/fab/).
OFFLINE_GLOBS = ['tests/offline/test_fab*.py']



def load_steps(tree):
    """[(name, argv)] of `tree`'s STEPS_FILE; SystemExit naming the file when it is missing
    or not a list of [name, [arg, ...]] with unique names."""
    path = os.path.join(tree, STEPS_FILE)
    try:
        with open(path) as fh:
            got = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SystemExit('the offline steps (%s) cannot be read: %s' % (path, exc))
    ok = isinstance(got, list) and all(
        isinstance(s, list) and len(s) == 2 and isinstance(s[0], str) and s[0]
        and isinstance(s[1], list) and s[1] and all(isinstance(x, str) for x in s[1])
        for s in got)
    names = [s[0] for s in got] if ok else []
    if not ok or len(set(names)) != len(names):
        raise SystemExit('the offline steps (%s) are not a list of [name, [arg, ...]] with '
                         'unique names' % path)
    return [(n, list(a)) for n, a in got]


def offline_steps():
    """STEPS_FILE's steps, then each OFFLINE_GLOBS test in TREE as `<file stem less test_>`."""
    import glob
    out = load_steps(TREE)
    for pattern in OFFLINE_GLOBS:
        for path in sorted(glob.glob(os.path.join(TREE, pattern))):
            rel = os.path.relpath(path, TREE)
            out.append((os.path.basename(rel)[5:-3].replace('_', ' '), [rel]))
    return out


#: The Missions record the UI cases open (as the release check does).
UI_RECORD = '900_Fixture_Edit_P'
NICE = ['nice', '-n', '10', 'ionice', '-c', '3']
#: The offline pool's priority: below the release check's build, which they run beside.
OFFLINE_NICE = ['nice', '-n', '15', 'ionice', '-c', '3']
#: What a snapshot copies from the tree, and the folder and file names it leaves out.
SNAPSHOT_ITEMS = ['riftlord', 'vendor', 'tools', 'tests', 'web', 'shell', 'native', 'docs',
                  '.sync', 'pyproject.toml', 'requirements.txt', 'requirements-build.txt',
                  'README.md']
SKIP_NAMES = ('__pycache__', '*.pyc', 'node_modules', 'dist', 'target', '.pytest_cache')
SNAPSHOT_SKIP = shutil.ignore_patterns(*SKIP_NAMES)
#: The tree the steps run in: NEW, or the run's snapshot.
TREE = NEW

_PRINT = threading.Lock()
#: Process groups of the steps running now, stopped when the gate is.
_RUNNING = set()


#: Set by _stop: no new step starts.
_STOPPING = threading.Event()


def _stop(*_):
    """SIGTERM: start no new step, stop every running one (SIGTERM, then SIGKILL), leave."""
    _STOPPING.set()
    for pgid in list(_RUNNING):
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(10)
    for pgid in list(_RUNNING):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    testservers.cleanup(verbose=False)
    os._exit(143)


#: Seconds the tree must go unchanged before a snapshot (RIFTLORD_GATE_QUIET overrides).
QUIET = int(os.environ.get('RIFTLORD_GATE_QUIET') or 120)
#: Seconds a gate waits for editing markers (riftlord.core.editing) before it snapshots anyway.
EDIT_CAP = 30 * 60


def _changed_since(since):
    """Snapshot files (as SNAPSHOT_ITEMS and SNAPSHOT_SKIP take them) changed after `since`."""
    import fnmatch
    out = []
    for item in SNAPSHOT_ITEMS:
        top = os.path.join(NEW, item)
        walk = os.walk(top) if os.path.isdir(top) else [(NEW, [], [item])]
        for base, dirs, names in walk:
            dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(d, p) for p in SKIP_NAMES)]
            for n in names:
                if any(fnmatch.fnmatch(n, p) for p in SKIP_NAMES):
                    continue
                path = os.path.join(base, n)
                try:
                    if os.path.getmtime(path) > since:
                        out.append(os.path.relpath(path, NEW))
                except OSError:
                    continue
    return out


def settle(warnings):
    """Wait before the snapshot: while editing markers are held (up to EDIT_CAP, then go on
    with a warning naming them), then until the tree has been quiet for QUIET seconds.
    Says once what it waits for."""
    from riftlord.core import editing
    t, said = time.time(), False
    while True:
        held = editing.live()
        if not held:
            break
        if time.time() - t > EDIT_CAP:
            warnings.append('snapshot taken with editing markers held: %s'
                            % ', '.join(r['name'] for r in held))
            print('  warning: ' + warnings[-1], flush=True)
            break
        if not said:
            print('  waiting for editing markers: %s' % ', '.join(r['name'] for r in held),
                  flush=True)
            said = True
        time.sleep(10)
    said = False
    while True:
        changing = _changed_since(time.time() - QUIET)
        if not changing:
            return
        if not said:
            print('  waiting for %d s without changes; changing: %s' % (
                QUIET, ', '.join(changing[:6]) + (' ...' if len(changing) > 6 else '')), flush=True)
            said = True
        time.sleep(10)


def snapshot(run):
    """Copy SNAPSHOT_ITEMS into run/tree. Returns (root, {relative path: sha256})."""
    root = os.path.join(run, 'tree')
    for item in SNAPSHOT_ITEMS:
        src = os.path.join(NEW, item)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(root, item), ignore=SNAPSHOT_SKIP, symlinks=True)
        elif os.path.isfile(src):
            os.makedirs(root, exist_ok=True)
            shutil.copy2(src, os.path.join(root, item))
    files = {}
    for base, dirs, names in os.walk(root):
        dirs.sort()
        for n in sorted(names):
            path = os.path.join(base, n)
            if os.path.isfile(path) and not os.path.islink(path):
                with open(path, 'rb') as fh:
                    files[os.path.relpath(path, root)] = hashlib.sha256(fh.read()).hexdigest()
    return root, files


def report_row(rows, row, last=''):
    with _PRINT:
        rows.append(row)
        print('  %-4s %-40s %5ds  %s' % ('PASS' if row['ok'] else 'FAIL', row['step'],
                                         row['seconds'], last[:110]), flush=True)


def step(rows, name, argv, log, env, timeout, cmd=None, nice=NICE):
    if _STOPPING.is_set():
        return 'stopped'
    t = time.time()
    with open(log, 'wb') as out:
        argv = nice + (cmd or [sys.executable, '-B'] + argv)
        p = subprocess.Popen(testservers.dies_with_parent(argv), cwd=TREE, env=env,
                             stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        _RUNNING.add(p.pid)
        try:
            code = p.wait(timeout)
        except subprocess.TimeoutExpired:
            code = 'timeout'
        finally:
            if p.poll() is None:
                os.kill(p.pid, signal.SIGTERM)       # lets it stop what it started
                try:
                    p.wait(60)
                except subprocess.TimeoutExpired:
                    pass
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            _RUNNING.discard(p.pid)
            testservers.cleanup(verbose=False)       # servers and browsers a killed step left
    with open(log, 'rb') as fh:
        tail = fh.read().decode('utf-8', 'replace').strip().splitlines()[-3:]
    report_row(rows, {'step': name, 'ok': code == 0, 'seconds': round(time.time() - t),
                      'log': log, 'tail': tail}, (tail or [''])[-1])
    return code


#: Known undefined names the static check reports but does not fail on: (file, name).
KNOWN_UNDEFINED = set()


def static_check(rows, run):
    """pyflakes over the package, tools and tests (the build venv's): fails on an
    undefined name not in KNOWN_UNDEFINED."""
    import build
    py = build.venv_python() or sys.executable
    t = time.time()
    got = subprocess.run([py, '-m', 'pyflakes', 'riftlord', 'tools', 'tests/offline'], cwd=TREE,
                         capture_output=True, text=True)
    log = os.path.join(run, 'static.log')
    with open(log, 'w') as fh:
        fh.write(got.stdout + got.stderr)
    bad = []
    for line in (got.stdout + got.stderr).splitlines():
        if 'undefined name' in line or 'invalid syntax' in line:
            name = line.split("'")[1] if "'" in line else ''
            if (line.split(':')[0], name) not in KNOWN_UNDEFINED:
                bad.append(line)
    report_row(rows, {'step': 'static: undefined names', 'ok': not bad and
                      'No module named pyflakes' not in got.stderr,
                      'seconds': round(time.time() - t), 'log': log, 'tail': bad[:3]},
               '; '.join(bad[:2]))


def stale_uis():
    """The UIs whose sources (or web/shared) are newer than their built page."""
    import build

    def newest(folder):
        out = 0
        for base, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in ('node_modules', 'dist')]
            out = max([out] + [os.path.getmtime(os.path.join(base, f)) for f in files])
        return out
    shared = newest(build.WEBSHARED)
    stale = []
    for ui, web in build.UIS.items():
        page = os.path.join(web, 'dist', 'index.html')
        if not os.path.exists(page) or max(newest(web), shared) > os.path.getmtime(page):
            stale.append(ui)
    return stale


def ui_cases(rows, run, env, cases):
    """The named UI smoke cases against a source server (the release check's record)."""
    import build
    stale = stale_uis()
    if stale:
        t = time.time()
        with _PRINT:
            print('  rebuilding UI: %s' % ', '.join(stale), flush=True)
        build.build_uis()
        report_row(rows, {'step': 'ui: rebuild ' + ', '.join(stale), 'ok': True,
                          'seconds': round(time.time() - t)})
    out = os.path.join(run, 'ui_smoke.json')
    if 'setup' not in cases:                          # the setup case wants a first run
        import release_check
        types = release_check.prepare_types()
        if types:
            env = dict(env, RIFTLORD_TYPES=types)
    from riftlord.core import gpulock
    with gpulock.turn('gate ui smoke', lambda m: print('  ' + m, flush=True)):  # WebGL
        #: the source server runs inside the turn: a bake a case asks for (a light bake) would
        #: otherwise wait for the turn this suite holds until the suite ends
        env = dict(env, **{gpulock.ENV: os.environ.get(gpulock.ENV) or 'gate ui smoke'})
        step(rows, 'ui smoke: ' + ','.join(cases), [], os.path.join(run, 'ui_smoke.log'), env,
             3600, cmd=[sys.executable, '-B', os.path.join(HERE, 'testservers.py'), 'run',
                        '--mission', UI_RECORD, '--', 'node',
                        os.path.join(NEW, 'tests', 'ui_smoke', 'ui_smoke.js'),
                        '--mission', UI_RECORD, '--cases', ','.join(cases), '--json', out])
    try:
        with open(out) as fh:
            for r in json.load(fh)['results']:
                report_row(rows, {'step': 'ui: ' + r['case'], 'ok': r['ok'],
                                  'seconds': round(r['seconds'] or 0), 'tail': r['steps'][-2:]},
                           r['error'] or '; '.join(s[5:] for s in r['steps'][-1:]))
    except (OSError, ValueError, KeyError):
        pass


def step_environment(rows, env, run):
    """`env` with the steps' own data, never the user's: RIFTLORD_TYPES, type data prepared
    for test servers (riftlord.types.setup --prepare, as the release check's servers use),
    and RIFTLORD_SUPPORTED_PAKS, a Paks folder of the run's own (tests/fixtures/fixturelibs
    run_paks: the fixture minion mod, and each supported mod's pinned real copy, else its
    stand-in), so no step reads the game's ~mods or LogicMods. A row says what each is;
    None when one cannot be made."""
    import release_check
    out = dict(env)
    for name, cmd, key in (
            ('types: the steps\' type data',
             ['-m', 'riftlord.types.setup', '--prepare', release_check.TYPES_CACHE], 'folder'),
            ('fixture Paks folder (no ~mods or LogicMods of the game\'s)',
             ['tests/fixtures/fixturelibs.py', os.path.join(testservers.SCRATCH,
                                                            'fixture-libraries'),
              '--run-paks', os.path.join(run, 'paks')], 'RIFTLORD_SUPPORTED_PAKS')):
        t = time.time()
        got = subprocess.run([sys.executable, '-B'] + cmd, cwd=TREE, env=env,
                             capture_output=True, text=True, timeout=3600)
        try:
            value = json.loads(got.stdout.strip().splitlines()[-1])[key]
        except (ValueError, IndexError, KeyError):
            value = None
        report_row(rows, {'step': name, 'ok': bool(value), 'seconds': round(time.time() - t),
                          'tail': (got.stdout + got.stderr).strip().splitlines()[-3:]},
                   value or (got.stdout + got.stderr).strip()[-110:])
        if not value:
            return None
        out['RIFTLORD_TYPES' if key == 'folder' else key] = value
    return out


#: Each certify step's output folder from its last pass (<kind>/, with key.json): the next
#: run starts from it when certify_key is unchanged, so minimapscore redraws nothing and
#: only scores again what its own digests say changed. Never the minimap harness's state
#: (minimapscore.OUT): that keys a drawing by DRAW_FILES alone, not by all it read. Beside
#: the records (gateselect.RECORDS), as part of the gate's own data.
CERTIFY_CACHE = os.path.join(os.path.dirname(gateselect.RECORDS), 'certify-cache')
#: Tree files a certify step reads only to judge its drawings, never to make them: a
#: change to one keeps the cached drawings.
CERTIFY_JUDGES = {'regenerate': ['docs/research/minimap-baseline.json',
                                 'docs/research/minimap-best.json']}


def certify_key(plan, kind, argv):
    """The key of a certify step's drawings: its argv and the signature of everything its
    record holds (gateselect signature) less CERTIFY_JUDGES. None without a record."""
    sig = plan.sel.signature('certify', kind, CERTIFY_JUDGES.get(kind, ())) if plan.sel \
        else None
    return sig and hashlib.sha256(json.dumps([argv, sig]).encode()).hexdigest()[:24]


def certify_cached(kind, key):
    """The cached output folder of the certify step `kind` when its key is `key`, or None."""
    folder = os.path.join(CERTIFY_CACHE, kind)
    try:
        with open(os.path.join(folder, 'key.json')) as fh:
            return folder if key and json.load(fh).get('key') == key else None
    except (OSError, ValueError):
        return None


def certify_keep(kind, key, out):
    """Keep `out`, a passed certify step's output folder, as its cache under `key`."""
    folder = os.path.join(CERTIFY_CACHE, kind)
    part = folder + '.part'
    shutil.rmtree(part, ignore_errors=True)
    shutil.copytree(out, part)
    with open(os.path.join(part, 'key.json'), 'w') as fh:
        json.dump({'key': key, 'from': out}, fh)
    shutil.rmtree(folder, ignore_errors=True)
    os.replace(part, folder)


def record_path(rec):
    """A test mission's record in the tree the steps run in."""
    return os.path.join(TREE, os.path.relpath(testservers.FIXTURE_MISSIONS, NEW), '.riftlord',
                        'records', rec + '.json')


def bakes_light(rec):
    """True when the test mission's build bakes its light maps (lightbake.needs): its
    comparison then takes the GPU before the release check's UI suites do."""
    from riftlord.build.lighting import lightbake
    try:
        with open(record_path(rec)) as fh:
            return lightbake.needs(json.load(fh).get('plan'))
    except (OSError, ValueError):
        return False


def built_checks(rows, run, env, rec, out):
    """Checks on the paks the comparison built from the new code: every texture mip's size
    (riftlord.build.texsizes, all paks), and the written and lifted components' trailers
    against the record's plan (riftlord.build.trailers, the mission's pak). True when
    every check passed."""
    try:
        with open(os.path.join(out, 'report.json')) as fh:
            paks = (json.load(fh).get('new') or {}).get('pak', {})
    except (OSError, ValueError):
        paks = {}
    if not paks:
        return False                                   # the comparison failed: it says why
    record = record_path(rec)
    mission = paks.get(rec + '.pak')
    checks = [('texture mip sizes', ['-m', 'riftlord.build.texsizes'] + sorted(paks.values()))]
    if mission:
        checks.append(('trailers', ['-m', 'riftlord.build.trailers', mission, '--plan', record]))
    codes = [step(rows, '%s: %s' % (name, rec), argv,
                  os.path.join(run, '%s-%s.log' % (name.replace(' ', '-'), rec)), env, 1800)
             for name, argv in checks]
    return all(c == 0 for c in codes)


def record_kinds(rows, env, records):
    """The EXPECTED kinds the records have (buildcompare --kinds), or None (a row says why)."""
    got = subprocess.run([sys.executable, '-B', 'tools/buildcompare.py', ','.join(records),
                          '--kinds'], cwd=TREE, env=env, capture_output=True, text=True)
    try:
        return {k for v in json.loads(got.stdout).values() for k in v}
    except ValueError:
        report_row(rows, {'step': 'compare: record kinds', 'ok': False, 'seconds': 0,
                          'tail': got.stderr.strip().splitlines()[-3:]}, got.stderr[-110:])
        return None


def compare_argv(rec):
    """A comparison's argv (less --out)."""
    return ['tools/buildcompare.py', rec] + sum((['--with', w] for w in RECORDS.get(rec, [])), [])


def minimap_certify(rows, run, env, records, plan):
    """EXPECTED's certifying steps for the kinds the compared records have. Each is a
    recorded step (`certify <kind>`); it starts from its last pass's output (CERTIFY_CACHE)
    when nothing its drawings read has changed (certify_key), else from an empty folder,
    and a pass is kept as the next run's start."""
    import buildcompare
    kinds = record_kinds(rows, env, records)
    if kinds is None:
        return
    for kind, _patterns, _reason, argv in buildcompare.EXPECTED:
        if kind not in kinds:
            continue
        extra = {'argv': argv}
        if not plan.why('certify', kind, extra):
            continue
        out = os.path.join(run, 'certify-' + kind)
        cached = certify_cached(kind, certify_key(plan, kind, argv))
        if cached:
            shutil.copytree(cached, out, ignore=shutil.ignore_patterns('key.json'))
            with _PRINT:
                print('  certify %s: from the last pass\'s drawings (%s): nothing they read '
                      'changed' % (kind, cached), flush=True)
            with plan.lock:
                for r in plan.ran:
                    if r['kind'] == 'certify' and r['name'] == kind:
                        r['why'] += '; the last pass\'s drawings reused'
        folder = plan.folder('certify', kind)
        t = time.time()
        code = step(rows, 'certify %s: %s %s' % (kind, argv[1].rsplit('.', 1)[-1],
                                                 ' '.join(argv[2:])),
                    argv + ['--out', out], os.path.join(run, 'certify-%s.log' % kind),
                    plan.env(env, folder), 4 * 3600)
        plan.done('certify', kind, code == 0, folder, t, extra)
        key = certify_key(plan, kind, argv) if code == 0 else None
        if key:
            certify_keep(kind, key, out)


class Plan:
    """Which steps this gate runs and why (gateselect), and what became of them. `sel` is
    None for a partial run on the live tree: every step it names runs, nothing is kept."""

    def __init__(self, sel, release, run, rows=None):
        self.sel, self.release, self.run, self.rows = sel, release, run, rows
        self.ran, self.skipped, self.ui = [], [], {}
        self.lock = threading.Lock()

    def why(self, kind, name, extra=None):
        """The reason the step runs (noted), or None when it is skipped (noted)."""
        if self.sel is None:
            why = 'partial run'
        elif self.release:
            why = 'release gate'
        elif kind == 'ui':
            why = self.sel.ui_reason(name)
        else:
            why = self.sel.reason(kind, name, extra)
        rec = None if why or self.sel is None else self.sel.load(kind, name)
        with self.lock:
            if why:
                self.ran.append({'kind': kind, 'name': name, 'why': why})
            else:
                self.skipped.append({'kind': kind, 'name': name, 'passed': rec.get('passed'),
                                     'run': os.path.basename(rec.get('run') or '')})
        return why

    def folder(self, kind, name):
        return os.path.join(self.run, 'inputs', gateselect.slug(kind, name))

    def env(self, env, folder):
        return gateselect.recording_env(env, folder) if self.sel else env

    def done(self, kind, name, ok, folder, started, extra=None):
        """Keep the step's record when it passed; drop it when it did not, or when it read
        the game's ~mods or LogicMods (then a failed row names what it read)."""
        if self.sel is None:
            return
        if ok:
            rec = self.sel.passed(kind, name, folder, started, time.time() - started, extra)
            read = gateselect.game_mod_reads(rec)
            if not read:
                return
            report_row(self.rows if self.rows is not None else [],
                       {'step': 'game mods unread: %s %s' % (kind, name), 'ok': False,
                        'seconds': 0, 'tail': read[:5]}, 'read %s' % read[0])
        self.sel.forget(kind, name)

    def seconds(self, kind, name, default=30):
        got = self.sel.seconds(kind, name) if self.sel else None
        return default if got is None else got

    def summary(self, rows):
        """Lines saying what ran (and why, how long), and what was skipped."""
        secs = {r['step']: r['seconds'] for r in rows}
        label = {'offline': 'offline: %s', 'compare': 'compare: %s', 'certify': 'certify %s',
                 'ui': 'ui: %s'}
        lines = ['%s gate: what ran and why' % ('release' if self.release else 'dev')]
        for kind in ('offline', 'ui', 'compare', 'certify'):
            ran = [r for r in self.ran if r['kind'] == kind]
            skipped = [r for r in self.skipped if r['kind'] == kind]
            if not ran and not skipped:
                continue
            lines.append('  %s: %d ran, %d skipped (unchanged inputs since their last pass)'
                         % (kind, len(ran), len(skipped)))
            for r in ran:
                if kind == 'ui':
                    ok, s = self.ui.get(r['name'], (None, None))
                    state = 'not run' if ok is None else ('PASS' if ok else 'FAIL')
                else:
                    s = next((v for k, v in secs.items() if k.startswith(
                        label[kind] % r['name'])), None)
                    state = ''
                lines.append('    ran  %-40s %5s  %-4s %s' % (r['name'][:40], '' if s is None
                                                              else '%ds' % s, state, r['why']))
            if skipped:
                lines.append('    skipped: ' + ', '.join(r['name'] for r in skipped))
        return lines

    def as_json(self):
        return {'mode': 'release' if self.release else 'dev', 'ran': self.ran,
                'skipped': self.skipped, 'ui': {k: list(v) for k, v in self.ui.items()}}


#: Log lines that say what failed, best first: a check's FAIL line, then an exception.
FAILURE_MARKS = (('FAIL', lambda ln: ln.lstrip().startswith('FAIL')),
                 ('error', lambda ln: ln.split(':', 1)[0].strip().endswith(('Error', 'Exception'))
                  and ' ' not in ln.split(':', 1)[0].strip()),
                 ('timed out', lambda ln: 'timed out' in ln or 'timeout' in ln.lower()))


def first_failure(log):
    """The first line of `log` that says what failed (FAILURE_MARKS), else its last line."""
    try:
        with open(log, encoding='utf-8', errors='replace') as fh:
            lines = [ln.rstrip() for ln in fh if ln.strip()]
    except (OSError, TypeError):
        return ''
    for _kind, test in FAILURE_MARKS:
        hit = next((ln for ln in lines if test(ln)), None)
        if hit:
            return hit.strip()
    return lines[-1].strip() if lines else ''


def failures(rows):
    """[{step, what, log}] of every failed row; the release check's failed checks each as
    `release check: <check>`, with its report."""
    out = []
    for r in rows:
        if r['ok']:
            continue
        if r['step'] == 'release check' and release_report([r]):
            report = release_report([r])
            try:
                with open(report) as fh:
                    bad = [c for c in json.load(fh)['checks'] if not c['ok']]
            except (OSError, ValueError, KeyError):
                bad = []
            for c in bad:
                out.append({'step': 'release check: ' + c['check'],
                            'what': c['detail'][:300], 'log': report})
            if bad:
                continue
        what = first_failure(r.get('log')) if r.get('log') else ''
        out.append({'step': r['step'], 'what': (what or ' / '.join(
            str(t) for t in r.get('tail') or []))[:300], 'log': r.get('log')})
    return out


def release_report(rows):
    """The release check's report.json path, from its row, or None."""
    row = next((r for r in rows if r['step'] == 'release check'), None)
    return next((ln.rsplit('report ', 1)[1].strip() for ln in (row or {}).get('tail') or []
                 if 'report ' in ln), None)


@contextlib.contextmanager
def under_load(load, job):
    """Adds {job, start, end} to `load` around the block: a heavy gate job that runs beside
    the release check (the offline pool, a comparison, the certify step)."""
    span = {'job': job, 'start': time.time(), 'end': None}
    try:
        yield
    finally:
        span['end'] = time.time()
        load.append(span)


def load_reruns(info, load):
    """The UI cases to run once more: each the release check reports as failed only on a
    timing limit (its report's `ui_timing_only`) that started while a heavy gate job was
    running (inside a span of `load`, under_load). [(case, {what, seconds, limit, under})],
    `under` naming those jobs."""
    out = []
    for case, t in sorted((info.get('ui_timing_only') or {}).items()):
        at = t.get('started') or 0
        under = [s['job'] for s in load if s['start'] <= at < s['end']]
        if under:
            out.append((case, dict(t, under=under)))
    return out


def release_rescued(checks, passed_again):
    """True when every failed check of the release check is a UI case in `passed_again`
    (it failed only on time under the pool and passed when run again)."""
    failed = [c['check'] for c in checks if not c['ok']]
    return bool(failed) and all(f.startswith('ui: ') and f[4:] in passed_again for f in failed)


def rerun_after_load(rows, run, env, plan, load):
    """Run again, once, on the release check's build, each UI case that failed only on a
    timing limit while a heavy gate job ran (load_reruns), now that every one has
    finished; a second failure stands. The release check's row passes when those were its
    only failures. Returns [{case, first, limit, under, ok, report}] (gate.json `reruns`)."""
    report = release_report(rows)
    try:
        with open(report) as fh:
            first = json.load(fh)
    except (OSError, ValueError, TypeError):
        return []
    todo = load_reruns(first, load)
    if not todo:
        return []
    cases = [c for c, _t in todo]
    stage = os.path.join(first['run_dir'], 'dist')
    log = os.path.join(run, 'rerun-after-load.log')
    step(rows, 'rerun after load: ' + ', '.join(
        '%s (%.1f s > %.1f s under %s)' % (c, t['seconds'], t['limit'], ', '.join(t['under']))
        for c, t in todo),
        ['tools/release_check.py', '--from-build', stage, '--ui-only', '--cases', ','.join(cases)],
        log, env, 3600)
    again = release_report([{'step': 'release check', 'tail': rows[-1].get('tail')}])
    try:
        with open(again) as fh:
            got = {c['check'][4:]: c['ok'] for c in json.load(fh)['checks']
                   if c['check'].startswith('ui: ')}
    except (OSError, ValueError, TypeError):
        got = {}
    out = []
    for case, t in todo:
        ok = bool(got.get(case))
        out.append({'case': case, 'first': t['what'], 'seconds': t['seconds'],
                    'limit': t['limit'], 'under': t['under'], 'ok': ok, 'report': again})
        print('  rerun after load: %s (%.1f s > %.1f s under %s): %s' % (
            case, t['seconds'], t['limit'], ', '.join(t['under']),
            'passed on the rerun' if ok else 'FAILED again'), flush=True)
        if plan.sel:
            if ok:
                plan.sel.ui_passed(case, t['seconds'])
            else:
                plan.sel.forget('ui', case)
        plan.ui[case] = (ok, t['seconds'])
    passed = {r['case'] for r in out if r['ok']}
    row = next(r for r in rows if r['step'] == 'release check')
    if not row['ok'] and release_rescued(first['checks'], passed):
        row['ok'] = True
        row['tail'] = list(row.get('tail') or []) + ['passed: %s passed on a rerun after load'
                                                    % ', '.join(sorted(passed))]
    return out


def record_ui(plan, rows, cases):
    """Keep each UI case that passed in the release check's report; drop the rest."""
    try:
        with open(release_report(rows)) as fh:
            got = {r['check'][4:]: r for r in json.load(fh)['checks']
                   if r['check'].startswith('ui: ')}
    except (OSError, ValueError, TypeError, KeyError):
        got = {}
    for case in cases:
        r = got.get(case)
        ok = bool(r and r['ok'])
        plan.ui[case] = (ok if r else None, r and r['seconds'])
        if plan.sel is None:
            continue
        if ok:
            plan.sel.ui_passed(case, r['seconds'])
        else:
            plan.sel.forget('ui', case)


def install_tested(rows, run):
    """Install the binaries the release check built and tested (from the snapshot) into
    the live tree's dist/, as release_check --install does. Returns the installed names."""
    import release_check
    with open(release_report(rows)) as fh:
        stage = os.path.join(json.load(fh)['run_dir'], 'dist')

    class Rows:
        def __init__(self):
            self.rows = []

        def add(self, name, ok, detail='', seconds=None, warn=False):
            self.rows.append({'step': name, 'ok': ok, 'seconds': round(seconds or 0),
                              'tail': [detail]})
    got = Rows()
    t = time.time()
    release_check.install(got, stage, release_check.LINUX_FILES)
    gone = release_check.prune_kept()
    detail = got.rows[-1]['tail'][0] + ('; pruned %s' % ', '.join(gone) if gone else '')
    report_row(rows, {'step': 'install into %s' % release_check.DIST, 'ok': True,
                      'seconds': round(time.time() - t), 'tail': [detail], 'stage': stage},
               detail)
    return stage


def tree_digests(root):
    """{relative path: sha256} of the files a snapshot of `root` would hold (SNAPSHOT_ITEMS
    less SKIP_NAMES), read in place."""
    import fnmatch
    out = {}
    for item in SNAPSHOT_ITEMS:
        top = os.path.join(root, item)
        walk = os.walk(top) if os.path.isdir(top) else [(root, [], [item])]
        for base, dirs, names in walk:
            dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(d, p) for p in SKIP_NAMES)]
            for n in names:
                path = os.path.join(base, n)
                if any(fnmatch.fnmatch(n, p) for p in SKIP_NAMES) or not os.path.isfile(path) \
                        or os.path.islink(path):
                    continue
                with open(path, 'rb') as fh:
                    out[os.path.relpath(path, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def explain(tree):
    """Print what a dev gate on `tree` (read in place, as its snapshot would be) would run
    and why, and what it would skip, from the records; nothing runs. The certify step is
    listed whatever kinds the compared records have (a gate asks buildcompare --kinds)."""
    import buildcompare
    t = time.time()
    plan = Plan(gateselect.Selection(tree, tree_digests(tree), None), False, None)
    for name, a in offline_steps():
        plan.why('offline', name, {'argv': a})
    for rec in RECORDS:
        plan.why('compare', rec, {'argv': compare_argv(rec)})
    for kind, _patterns, _reason, argv in buildcompare.EXPECTED:
        if argv and plan.why('certify', kind, {'argv': argv}):
            if certify_cached(kind, certify_key(plan, kind, argv)):
                plan.ran[-1]['why'] += '; the last pass\'s drawings would be reused'
    for case in gateselect.ui_cases(tree):
        plan.why('ui', case)
    print('dev gate on %s (records %s), %.1f s' % (tree, gateselect.RECORDS, time.time() - t))
    for kind in ('offline', 'ui', 'compare', 'certify'):
        ran = [r for r in plan.ran if r['kind'] == kind]
        skipped = [r['name'] for r in plan.skipped if r['kind'] == kind]
        print('  %s: %d would run, %d skipped' % (kind, len(ran), len(skipped)))
        for r in ran:
            print('    run  %-40s %s' % (r['name'][:40], r['why']))
        if skipped:
            print('    skipped: ' + ', '.join(skipped))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--skip', default='', help='comma list of RELEASE, OFFLINE, COMPARE')
    ap.add_argument('--records', help='comma list; default %s' % ', '.join(RECORDS))
    ap.add_argument('--windows', action='store_true', help='release check --windows too')
    ap.add_argument('--cold', action='store_true',
                    help='release check --cold: an empty trace cache (when trace readers change)')
    ap.add_argument('--only', help='only these offline steps (comma list; names as in '
                                   'OFFLINE, or static)')
    ap.add_argument('--ui', help='only these UI smoke cases (comma list), on a source server')
    ap.add_argument('--install', action='store_true',
                    help='when every step passed, install the tested binaries into dist/')
    ap.add_argument('--release', action='store_true',
                    help='the release gate: every step, whatever changed (a release build)')
    ap.add_argument('--explain', action='store_true',
                    help='say what a dev gate would run now and why; run nothing')
    ap.add_argument('--tree', help='with --explain: the tree to explain (default the live one)')
    args = ap.parse_args(argv)
    global TREE
    if args.explain:
        TREE = os.path.abspath(args.tree or NEW)
        return explain(TREE)
    signal.signal(signal.SIGTERM, _stop)
    skip = {s.strip().upper() for s in args.skip.split(',') if s.strip()}
    only = [s.strip() for s in (args.only or '').split(',') if s.strip()]
    partial = bool(only or args.ui or skip)
    if args.install and partial:
        ap.error('--install needs the full gate (no --only, --ui or --skip)')
    known = {'static'} | {n for n, _a in offline_steps()}
    if [s for s in only if s not in known]:
        ap.error('unknown step %s; steps: %s' % ([s for s in only if s not in known],
                                                  ', '.join(sorted(known))))
    run = os.path.join(RUNS, time.strftime('%Y-%m-%d-%H%M%S'))
    os.makedirs(os.path.join(run, 'tmp', 'Missions'), exist_ok=True)
    #: OMD3_BUCKET: an empty Missions folder of the run's, so no step reads the user's
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', TMPDIR=os.path.join(run, 'tmp'),
               RIFTLORD_WIN64=os.path.dirname(NEW),
               OMD3_BUCKET=os.path.join(run, 'tmp', 'Missions'))
    print('gate run %s%s' % (run, ' (partial)' if partial else ''), flush=True)
    testservers.cleanup(verbose=True)                  # what a killed run left
    rows = []
    t_all = time.time()
    #: load: the heavy jobs beside the release check, as spans of time (under_load); a UI
    #: case that failed only on time inside one is run again once all have finished
    snap, warnings, plan, files, reruns, load = {}, [], None, {}, [], []
    if not partial:
        settle(warnings)
        TREE, files = snapshot(run)
        snap = {'root': TREE, 'files': len(files), 'sha256': hashlib.sha256(
            json.dumps(files, sort_keys=True).encode()).hexdigest(), 'digests': files}
        print('snapshot %s: %d files, sha256 %s' % (TREE, len(files), snap['sha256'][:16]),
              flush=True)
    if only or args.ui:
        if 'static' in only:
            static_check(rows, run)
        for name, a in offline_steps():
            if name in only:
                step(rows, 'offline: ' + name, a, os.path.join(run, name.replace(' ', '-') + '.log'),
                     env, 3600)
        if args.ui:
            ui_cases(rows, run, env, [c.strip() for c in args.ui.split(',') if c.strip()])
    else:
        env = step_environment(rows, env, run) or env
        sel = gateselect.Selection(TREE, files, run) if snap else None
        plan = Plan(sel, args.release, run, rows)
        jobs = []
        #: set once the comparisons hold the GPU for their light bakes (or need none)
        gpu_taken = threading.Event()
        if 'COMPARE' not in skip:
            records = args.records.split(',') if args.records else list(RECORDS)

            chosen = [r for r in records if plan.why('compare', r, {'argv': compare_argv(r)})]
            bakes = [r for r in chosen if bakes_light(r)]
            chosen = bakes + [r for r in chosen if r not in bakes]

            def compares():
                from riftlord.core import gpulock
                turn = None
                try:
                    if bakes:
                        turn = gpulock.turn('gate comparisons with a light bake',
                                            lambda m: print('  ' + m, flush=True))
                        turn.__enter__()
                finally:
                    gpu_taken.set()
                try:
                    for rec in chosen:
                        out = os.path.join(run, 'compare-' + rec)
                        folder, t = plan.folder('compare', rec), time.time()
                        own = plan.env(dict(env, **{gpulock.ENV: 'gate'}) if turn else env, folder)
                        with under_load(load, 'compare: ' + rec):
                            code = step(rows, 'compare: ' + rec, compare_argv(rec) + ['--out', out],
                                        os.path.join(run, 'compare-%s.log' % rec), own, 3 * 3600)
                            if turn and rec == bakes[-1]:
                                turn.__exit__(None, None, None)
                                turn = None
                            checked = built_checks(rows, run, plan.env(env, folder), rec, out)
                        plan.done('compare', rec, code == 0 and checked, folder, t,
                                  {'argv': compare_argv(rec)})
                    with under_load(load, 'certify'):
                        minimap_certify(rows, run, env, records, plan)
                finally:
                    if turn:
                        turn.__exit__(None, None, None)
            jobs.append(threading.Thread(target=compares))
        else:
            gpu_taken.set()
        if 'RELEASE' not in skip:
            cases = gateselect.ui_cases(TREE)
            picked = [c for c in cases if plan.why('ui', c)]

            def release():
                gpu_taken.wait()
                step(rows, 'release check', ['tools/release_check.py', '--ui-groups',
                                             str(UI_GROUPS)] +
                     (['--cases', ','.join(picked)] if len(picked) < len(cases) else []) +
                     (['--windows'] if args.windows else []) + (['--cold'] if args.cold else []),
                     os.path.join(run, 'release.log'), env, 4 * 3600)
                record_ui(plan, rows, picked)
            jobs.append(threading.Thread(target=release))
        if 'OFFLINE' not in skip:
            def offline(name, argv):
                folder, t = plan.folder('offline', name), time.time()
                code = step(rows, 'offline: ' + name, argv,
                            os.path.join(run, name.replace(' ', '-') + '.log'),
                            plan.env(env, folder), 3600, nice=OFFLINE_NICE)
                plan.done('offline', name, code == 0, folder, t, {'argv': argv})

            def offline_pool():
                import concurrent.futures
                chosen = [(n, a) for n, a in offline_steps() if plan.why('offline', n, {'argv': a})]
                chosen.sort(key=lambda s: -plan.seconds('offline', s[0]))   # longest first
                with under_load(load, 'offline pool'), \
                        concurrent.futures.ThreadPoolExecutor(OFFLINE_WORKERS) as pool:
                    done = [pool.submit(static_check, rows, run)]
                    done += [pool.submit(offline, n, a) for n, a in chosen]
                    for f in done:
                        f.result()
            jobs.append(threading.Thread(target=offline_pool))
        for j in jobs:
            j.start()
        for j in jobs:
            j.join()
        if 'RELEASE' not in skip:
            reruns = rerun_after_load(rows, run, env, plan, load)
        summary = plan.summary(rows)
        print('\n' + '\n'.join(summary), flush=True)
    ok = bool(rows) and all(r['ok'] for r in rows)
    installed = None
    if ok and args.install:
        installed = install_tested(rows, run)
    elif args.install:
        print('  not installing: a step failed', flush=True)
    failed = failures(rows)
    if failed:
        print('\nwhat failed (%d):' % len(failed), flush=True)
        for f in failed:
            print('  %s\n      %s\n      log %s' % (f['step'], f['what'] or '(no message)',
                                                  f['log'] or '-'), flush=True)
    with open(os.path.join(run, 'gate.json'), 'w') as fh:
        json.dump({'ok': ok, 'partial': partial, 'seconds': round(time.time() - t_all),
                   'installed_from': installed, 'warnings': warnings, 'steps': rows,
                   'failures': failed, 'reruns': reruns, 'load': load,
                   'selection': plan.as_json() if plan else None,
                   'snapshot': snap}, fh, indent=1)
    verdict = ('PARTIAL RUN PASSED (not a gate: install needs the full gate)' if partial
               else 'GATE PASSED') if ok else ('PARTIAL RUN FAILED' if partial else 'GATE FAILED')
    print('\n%s in %d s; %s' % (verdict, time.time() - t_all, os.path.join(run, 'gate.json')))
    return 0 if ok else 1


def limited_main(argv=None):
    """main() inside a heavy-job slot (riftlord.core.heavyslot): its steps run in it."""
    from riftlord.core import heavyslot
    argv = sys.argv[1:] if argv is None else argv
    if '--explain' in argv:                           # runs nothing: no slot
        return main(argv)
    install = '--install' in argv                    # an install gate: the reserved slot too
    with heavyslot.slot('gate-install' if install else 'gate', reserved=install):
        return main(argv)


if __name__ == '__main__':
    raise SystemExit(limited_main())
