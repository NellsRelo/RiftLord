"""Setup: bpkit's type data built into the user's cache from the user's own installs, once,
and again when an input changes.

    Engine types   the UE 4.27 headers (riftlord.types.engine)       -> engine.json
    Game types     the game's exe (riftlord.types.exe)                -> game.json
    Blueprints     the game's paks (riftlord.types.blueprints)        -> blueprints.json
    then           the three merged (riftlord.types.merge)            -> typedb.json,
                                                     replicated.json, native_struct_sizes.json

Each step's cache is keyed by its inputs (paths, sizes, modification times) and the code that
makes it; state.json holds the keys, each step's seconds and what it reported.

    setup.status()        {'state', 'message', 'steps': [...], ...} for the app's Setup pane,
                          the terrain kits' step (riftlord.terrainkit.setup) last
    setup.start()         run the steps a changed input needs, in a low-priority process
                          holding a heavy-job slot (riftlord.core.heavyslot)
    setup.ready()         True when the merged caches are there
    setup.point(module)   aim bpkit.typedb / replication / structsize at the caches

    python3 -m riftlord.types.setup [--run] [--force] [--status]
    python3 -m riftlord.types.setup --prepare ROOT   # test servers: RIFTLORD_TYPES=<printed>
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time

#: The steps, in order: (key, label, output file).
STEPS = [('engine', 'Engine types', 'engine.json'), ('game', 'Game types', 'game.json'),
         ('blueprints', 'Blueprints', 'blueprints.json')]
#: What the merge writes, and the bpkit module attribute each one is.
MERGED = {'typedb.json': ('bpkit.typedb', 'CACHE'),
          'native_struct_sizes.json': ('bpkit.typedb', 'NATIVE_SIZES'),
          'replicated.json': ('bpkit.replication', 'CACHE')}
#: The environment variable that moves the cache folder.
ENV = 'RIFTLORD_TYPES'
#: What Mission Events say while Setup cannot run.
NEEDS_UE = 'Mission Events need Unreal Engine 4.27'
#: The share of the CPUs a setup run may use, and its memory ceiling (bytes; POSIX).
CPU_SHARE = 0.25
MEMORY_LIMIT = 3 << 30
#: The frozen server's switch that runs a setup (riftlord/__main__.py).
FROZEN_SWITCH = '--types'
HERE = os.path.dirname(os.path.abspath(__file__))
#: The code each step's output depends on: this package's modules and bpkit's.
CODE = {'engine': ['engine.py', 'bpkit:headers.py'],
        'game': ['exe.py'],
        'blueprints': ['blueprints.py', 'bpkit:typedb.py', 'bpkit:structs.py',
                       'bpkit:package.py', 'bpkit:check_structs.py'],
        'merge': ['merge.py', 'bpkit:typedb.py']}

_LOCK = threading.Lock()
_RUN = {'proc': None, 'thread': None}


def folder():
    """The cache folder: RIFTLORD_TYPES, else <user cache>/RiftLord/types."""
    path = os.environ.get(ENV)
    if not path:
        if os.name == 'nt':
            base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        else:
            base = os.environ.get('XDG_CACHE_HOME') or \
                os.path.join(os.path.expanduser('~'), '.cache')
        path = os.path.join(base, 'RiftLord', 'types')
    return path


def user_folder():
    """The user's own cache folder, whatever RIFTLORD_TYPES says."""
    saved = os.environ.pop(ENV, None)
    try:
        return folder()
    finally:
        if saved is not None:
            os.environ[ENV] = saved


def _path(name):
    return os.path.join(folder(), name)


def _read_json(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.part'
    with open(tmp, 'w') as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


# -- inputs and keys ----------------------------------------------------------------------

def _bpkit_dir():
    from riftlord.core import bpkitlink
    if getattr(sys, 'frozen', False):
        import importlib.util
        spec = importlib.util.find_spec('bpkit')
        return os.path.dirname(spec.origin) if spec and spec.origin else bpkitlink.VENDORED
    return bpkitlink.VENDORED


def _code_hash(step):
    """A hash of the source text of the code `step` depends on, the same in a source run and
    in the frozen app (which carries these files as text at the same places: tools/build.py
    types_code_files), so a cache one made is current for the other."""
    h = hashlib.sha256()
    for name in CODE[step]:
        bpkit = name.startswith('bpkit:')
        path = os.path.join(_bpkit_dir(), name[6:]) if bpkit else os.path.join(HERE, name)
        try:
            with open(path, 'rb') as fh:
                h.update(name.encode() + b'\0' + fh.read())
        except OSError:
            h.update(name.encode() + b'\0missing')
    return h.hexdigest()


def _stat(path):
    """[real path, size, mtime]: the real path, so the same file found by another spelling
    (Steam's ~/.steam/steam link, a saved install) gives the same key."""
    try:
        st = os.stat(path)
        return [os.path.realpath(path), st.st_size, int(st.st_mtime)]
    except OSError:
        return [os.path.realpath(path), None, None]


def exe_path():
    """The game's exe, or None without an install."""
    from riftlord.core import gamepath
    install = gamepath.find()
    if not install:
        return None
    path = os.path.join(install, 'OMD', 'Binaries', 'Win64', 'OMD-Win64-Shipping.exe')
    return path if os.path.isfile(path) else None


def ue_root():
    from riftlord.core import ueroot
    return ueroot.find()


def inputs(step):
    """What `step` reads, as a list a key is made of; None when an input is missing."""
    if step == 'engine':
        from riftlord.types import engine
        root = ue_root()
        if not root or engine.missing_required(root):
            return None
        files = []
        for _m, _d, path, present in engine.folders(root):
            if not present:
                files.append([os.path.realpath(path), 'absent'])
                continue
            for base, dirs, names in os.walk(path):
                dirs.sort()
                files += [_stat(os.path.join(base, n)) for n in sorted(names) if n.endswith('.h')]
        return [os.path.realpath(root)] + files
    if step == 'game':
        exe = exe_path()
        return [_stat(exe)] if exe else None
    if step == 'blueprints':
        from riftlord.core import gamepath
        paks = gamepath.paks()
        if not paks:
            return None
        return [_stat(os.path.join(paks, n)) for n in sorted(os.listdir(paks))
                if n.endswith('.pak')]
    raise ValueError(step)


def key(step, given=None):
    """The cache key of `step`: its inputs and code, hashed; None when an input is missing."""
    got = inputs(step) if given is None else given
    if got is None:
        return None
    return hashlib.sha256(json.dumps([got, _code_hash(step)]).encode()).hexdigest()


def merge_key(keys):
    return hashlib.sha256(json.dumps([keys, _code_hash('merge')]).encode()).hexdigest()


def cache_tag():
    """A short name for the caches today's inputs and code make (the merged files' key), or
    None when an input is missing."""
    keys = {}
    for step, _label, _name in STEPS:
        keys[step] = key(step)
        if keys[step] is None:
            return None
    return merge_key(keys)[:16]


def prepare(root, log=None):
    """A ready cache folder for test servers: <root>/<cache_tag()>, its files copied from
    the user's cache where their keys match, the rest built (in a heavy-job slot). Returns
    the folder; point a server at it with RIFTLORD_TYPES. The folder is shared by every run
    of the same inputs and code, so it keeps no run's progress.json once it is ready."""
    log = log or (lambda _s: None)
    message = missing_message()
    if message:
        raise SystemExit(message)
    target = os.path.join(root, cache_tag())
    saved = os.environ.get(ENV)
    os.environ[ENV] = target
    _NEEDED['at'] = 0.0
    try:
        if not needed(fresh=True):
            try:
                os.remove(_path('progress.json'))
            except OSError:
                pass
            return target
        src = user_folder()
        theirs = _read_json(os.path.join(src, 'state.json'), {}) or {}
        mine = state()
        mine.setdefault('keys', {})
        mine.setdefault('seconds', {})
        mine.setdefault('reports', {})
        os.makedirs(target, exist_ok=True)
        keys = {}
        for step, _label, name in STEPS:
            keys[step] = key(step)
            if theirs.get('keys', {}).get(step) == keys[step] and \
                    os.path.isfile(os.path.join(src, name)):
                shutil.copyfile(os.path.join(src, name), os.path.join(target, name))
                mine['keys'][step] = keys[step]
                mine['seconds'][step] = (theirs.get('seconds') or {}).get(step)
                mine['reports'][step] = (theirs.get('reports') or {}).get(step)
                log('%s: copied from %s' % (step, src))
        if theirs.get('merged') == merge_key(keys) and \
                all(os.path.isfile(os.path.join(src, n)) for n in MERGED):
            for n in MERGED:
                shutil.copyfile(os.path.join(src, n), os.path.join(target, n))
            mine['merged'] = theirs['merged']
            mine['seconds']['merge'] = (theirs.get('seconds') or {}).get('merge')
        _write_json(os.path.join(target, 'state.json'), mine)
        _NEEDED['at'] = 0.0
        if needed(fresh=True):
            from riftlord.core import heavyslot
            with heavyslot.slot('setup'):
                run(log=log)
        return target
    finally:
        if saved is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = saved
        _NEEDED['at'] = 0.0


# -- state --------------------------------------------------------------------------------

def state():
    return _read_json(_path('state.json'), {}) or {}


def ready():
    """True when the merged caches are in the folder (built by a finished setup)."""
    s = state()
    return bool(s.get('merged')) and all(os.path.isfile(_path(n)) for n in MERGED)


#: Seconds a needed() answer is reused for (the Setup pane asks often).
NEEDED_FRESH = 5.0
_NEEDED = {'at': 0.0, 'value': None}


def needed(fresh=False):
    """[step key] whose cache is missing or out of date, in order; 'merge' last when the
    merged files must be written again. None when a step cannot run (an input is missing)."""
    if not fresh and time.time() - _NEEDED['at'] < NEEDED_FRESH:
        return _NEEDED['value']
    _NEEDED['value'] = _needed()
    _NEEDED['at'] = time.time()
    return _NEEDED['value']


def _needed():
    s = state()
    out, keys = [], {}
    for step, _label, name in STEPS:
        k = key(step)
        if k is None:
            return None
        keys[step] = k
        if s.get('keys', {}).get(step) != k or not os.path.isfile(_path(name)):
            out.append(step)
    if out or s.get('merged') != merge_key(keys) or not ready():
        out.append('merge')
    return out


def missing_message():
    """The one line saying what Setup needs, or None when it can run."""
    if exe_path() is None:
        return 'Mission Events need Orcs Must Die 3'
    root = ue_root()
    if not root:
        return NEEDS_UE
    from riftlord.types import engine
    if engine.missing_required(root):
        return NEEDS_UE
    return None


def unavailable():
    """None when Mission Events can build, else the one line saying why."""
    if ready():
        return None
    return missing_message() or 'Mission Events need Setup to finish'


def status():
    """The Setup pane's data: {'state': 'ready' | 'needed' | 'running' | 'failed' |
    'unavailable', 'message': one line or None, 'steps': [{'key', 'label', 'state',
    'progress', 'seconds'}]}."""
    s = state()
    progress = _read_json(_path('progress.json'), {}) or {}
    running = running_now()
    message = missing_message()
    todo = None if message else needed()
    steps = []
    for step, label, _name in STEPS:
        p = progress.get(step) or {}
        if running:
            st = p.get('state') or 'waiting'
        elif todo is not None and step in todo:
            st = 'waiting'
        else:
            st = 'done' if s.get('keys', {}).get(step) else 'waiting'
        steps.append({'key': step, 'label': label, 'state': st,
                      'progress': p.get('progress', 1.0 if st == 'done' else 0.0),
                      'seconds': (s.get('seconds') or {}).get(step)})
    if running:
        overall = 'running'
    elif progress.get('failed') and not ready():
        overall = 'failed'
    elif message:
        overall = 'ready' if ready() else 'unavailable'
    elif todo:
        overall = 'failed' if progress.get('failed') else 'needed'
    else:
        overall = 'ready'
    # a type-data failure is the error only while the type data is not current
    error = progress.get('failed') if overall == 'failed' else None
    from riftlord.terrainkit import setup as terrain
    row = terrain.step()
    steps.append(row)
    if overall != 'unavailable':
        if row['state'] == 'running':
            overall = 'running'
        elif row['state'] == 'failed' and overall != 'running':
            overall, error = 'failed', error or terrain.failed()
        elif row['state'] == 'waiting' and row['available'] and overall == 'ready':
            overall = 'needed'
    return {'state': overall, 'message': message if not ready() else None,
            'error': error, 'steps': steps, 'folder': folder(),
            'skipped': (s.get('reports') or {}).get('engine', {}).get('skipped', [])}


# -- running --------------------------------------------------------------------------------

def running_now():
    proc = _RUN['proc']
    return proc is not None and proc.poll() is None


def command(force=False):
    """The command line that runs a setup in its own process."""
    from riftlord.core import gamepath
    extra = ['--run'] + (['--force'] if force else [])
    if gamepath.frozen():
        return [sys.executable, FROZEN_SWITCH] + extra
    root = os.path.dirname(os.path.dirname(HERE))
    boot = ('import runpy, sys; sys.path.insert(0, %r); '
            'runpy.run_module("riftlord.types.setup", run_name="__main__", alter_sys=True)' % root)
    return [sys.executable, '-I', '-c', boot] + extra


def _prctl():
    """libc's prctl on Linux, else None (looked up here: a forked child must not)."""
    if not sys.platform.startswith('linux'):
        return None
    try:
        import ctypes
        return ctypes.CDLL(None, use_errno=True).prctl
    except (OSError, AttributeError):
        return None


_PRCTL = _prctl()
#: prctl's PR_SET_PDEATHSIG, and SIGKILL.
PR_SET_PDEATHSIG, SIGKILL = 1, 9


def _limits():
    """In the child before exec: a memory ceiling, and SIGKILL when the thread that
    started it (the run's own thread) ends."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
    except (ImportError, ValueError, OSError):
        pass
    if _PRCTL is not None:
        _PRCTL(PR_SET_PDEATHSIG, SIGKILL)


def start(force=False):
    """Start a setup run in a low-priority process unless one runs or nothing is needed, and
    the terrain kits' build when it is needed. Returns status()."""
    from riftlord.terrainkit import setup as terrain
    terrain.start()
    with _LOCK:
        if running_now():
            return status()
        if missing_message():
            return status()
        if not force and not needed(fresh=True):
            return status()
        try:
            os.remove(_path('progress.json'))
        except OSError:
            pass
        kw = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL,
              'stderr': subprocess.PIPE}
        argv = command(force)
        if os.name == 'nt':
            kw['creationflags'] = subprocess.BELOW_NORMAL_PRIORITY_CLASS
        else:
            argv = ['nice', '-n', '10'] + (['ionice', '-c', '3'] if shutil.which('ionice')
                                           else []) + argv
            kw['preexec_fn'] = _limits
        started = threading.Event()

        def own():
            try:
                proc = subprocess.Popen(argv, **kw)
            except OSError as exc:
                _Progress().fail('engine', str(exc))
                started.set()
                return
            _RUN['proc'] = proc
            started.set()
            _out, err = proc.communicate()
            _NEEDED['at'] = 0.0
            if proc.returncode:
                from riftlord.core import console
                tail = (err or b'').decode('utf-8', 'replace').strip().splitlines()[-1:]
                console.error('Setup failed: %s' % (tail[0] if tail else proc.returncode),
                              console.SERVER)
        _RUN['thread'] = threading.Thread(target=own, daemon=True)
        _RUN['thread'].start()
        started.wait(10)
    return status()


def stop():
    """Stop a running setup (the app is closing)."""
    proc = _RUN['proc']
    if proc is not None and proc.poll() is None:
        proc.kill()
    from riftlord.terrainkit import setup as terrain
    terrain.stop()


class _Progress:
    """progress.json, written as the steps go."""

    def __init__(self):
        self.data = {}

    def set(self, step, **kw):
        self.data.setdefault(step, {}).update(kw)
        self.flush()

    def fail(self, step, message):
        self.data['failed'] = '%s: %s' % (dict((k, v) for k, v, _n in STEPS).get(step, step),
                                          message)
        self.set(step, state='failed')

    def flush(self):
        try:
            _write_json(_path('progress.json'), self.data)
        except OSError:
            pass


def run(force=False, log=None):
    """Run every step a changed input needs (all with `force`), then the merge. Returns
    the new state."""
    log = log or (lambda _s: None)
    message = missing_message()
    if message:
        raise SystemExit(message)
    s = state()
    s.setdefault('keys', {})
    s.setdefault('seconds', {})
    s.setdefault('reports', {})
    todo = [k for k, _l, _n in STEPS] + ['merge'] if force else needed(fresh=True)
    prog = _Progress()
    for step, _label, _name in STEPS:
        prog.set(step, state='waiting' if step in todo else 'done',
                 progress=0.0 if step in todo else 1.0)
    keys = {}
    for step, label, name in STEPS:
        given = inputs(step)
        keys[step] = key(step, given)
        if step not in todo:
            continue
        prog.set(step, state='running', progress=0.0)
        t0 = time.time()
        try:
            data, report = _step(step, log, lambda done, total, st=step: prog.set(
                st, progress=round(done / float(total or 1), 3)))
        except (Exception, SystemExit) as exc:         # noqa: BLE001
            prog.fail(step, str(exc) or type(exc).__name__)
            raise
        _write_json(_path(name), data)
        s['keys'][step] = keys[step]
        s['seconds'][step] = round(time.time() - t0, 1)
        s['reports'][step] = report
        s.pop('merged', None)
        _write_json(_path('state.json'), s)
        prog.set(step, state='done', progress=1.0)
        log('%s: %.1f s' % (label, s['seconds'][step]))
    if 'merge' in todo or s.get('merged') != merge_key(keys):
        t0 = time.time()
        from riftlord.types import merge
        engine = _read_json(_path('engine.json'))
        game = _read_json(_path('game.json'))
        bp = _read_json(_path('blueprints.json'))
        db, replicated, sizes = merge.build(engine, game, bp)
        _write_json(_path('typedb.json'), db)
        _write_json(_path('replicated.json'), replicated)
        _write_json(_path('native_struct_sizes.json'), sizes)
        s['merged'] = merge_key(keys)
        s['seconds']['merge'] = round(time.time() - t0, 1)
        _write_json(_path('state.json'), s)
        log('merged: %.1f s' % s['seconds']['merge'])
    return s


def _step(step, log, progress):
    """(data, report) of one step."""
    if step == 'engine':
        from riftlord.types import engine
        raw, report = engine.parse(ue_root(), log)
        return {'raw': raw, 'report': report}, report
    if step == 'game':
        from riftlord.types import exe
        progress(0, 1)
        got = exe.read(exe_path(), log)
        report = {k: len(got[k]) for k in ('classes', 'structs', 'enums', 'functions',
                                           'delegates')}
        report['problems'] = got['problems'][:20]
        return got, report
    if step == 'blueprints':
        from riftlord.types import blueprints
        got = blueprints.read(log, progress, refresh=True)
        report = {k: len(got[k]) for k in ('classes', 'structs', 'functions', 'struct_sizes')}
        return got, report
    raise ValueError(step)


# -- bpkit ---------------------------------------------------------------------------------

def point(module):
    """Aim a freshly loaded bpkit module at the merged caches when they are ready:
    bpkit.typedb's CACHE and NATIVE_SIZES, bpkit.replication's CACHE, and bpkit.structsize's
    EXE (the game's own exe)."""
    name = module.__name__
    if name == 'bpkit.structsize':
        exe = exe_path()
        if exe:
            module.EXE = exe
        return
    if not ready():
        return
    for file, (mod, attr) in MERGED.items():
        if mod == name:
            setattr(module, attr, _path(file))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--run', action='store_true', help='run the steps a changed input needs')
    ap.add_argument('--force', action='store_true', help='with --run: every step')
    ap.add_argument('--status', action='store_true', help='print status() as JSON')
    ap.add_argument('--prepare', metavar='ROOT',
                    help='make ROOT/<cache tag> ready for test servers; print {"folder": ...}')
    args = ap.parse_args(argv)
    if args.prepare:
        got = prepare(args.prepare, log=lambda s: print(s, file=sys.stderr, flush=True))
        print(json.dumps({'folder': got, 'env': {ENV: got}}))
        return 0
    if args.run:
        from riftlord.core import heavyslot
        with heavyslot.slot('setup'):
            run(force=args.force, log=lambda s: print(s, file=sys.stderr, flush=True))
    if args.status or not args.run:
        print(json.dumps(status(), indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
