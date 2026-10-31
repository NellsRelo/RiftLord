"""The Fab Kits tab's server side: the packs in the user's own Epic vault, and the Fab
converter run on them as a child process, writing the libraries the Map Editor lists
(riftlord.mapeditor.fablibrary).

The converter is a JSON command line (vault / measure / convert / delete). It runs as
`riftlord.fab` (from source: `python3 -I` with this tree on sys.path; frozen: the server
itself, `RiftLordServer --fab ARGS`), or as the script RIFTLORD_FABCONVERT names
(development and the UI smoke suite). Each run is a separate process group at low
priority; its worker count and memory limit are the converter's defaults unless Settings
set them (passed only to a converter that reports them in `vault --json` `settings`).
Cancel kills the group. A convert or measure is a job, kept by item while the server lives,
so a reloaded page finds it again.

Converts go through a queue kept here: packs wait in order and run one at a time, so the
queue carries on while the tab is closed. The tab shows a short line for a failed job; the
converter's words and log go to the console (source Fab Kits).
"""

import collections
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
#: The folder holding the `riftlord` package (the bundle's root when frozen).
CODE_ROOT = os.path.dirname(os.path.dirname(HERE))

from riftlord.core import gamepath  # noqa: E402

#: A converter script to run instead of riftlord.fab (`python3 -I SCRIPT ARGS`).
OVERRIDE_ENV = 'RIFTLORD_FABCONVERT'
#: The switch a frozen Rift Lord server answers by running the converter (riftlord/__main__.py).
FROZEN_SWITCH = '--fab'
#: The converter's package.
MODULE = 'riftlord.fab'
#: What the tab says when it cannot convert (omd3physx.NEEDS; the detail line goes under it).
UNAVAILABLE = 'Fab conversion needs Unreal Engine 4.27 and a C++ compiler'

#: Converter exit codes.
EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, EXIT_CRASH, EXIT_LOCKED = 0, 1, 2, 3, 4
#: Low priority on POSIX.
NICE = ['nice', '-n', '10']
IONICE = ['ionice', '-c', '3']
#: Seconds a vault, delete or (cached) measure command may take.
COMMAND_TIMEOUT = 300
#: Seconds the vault listing is reused for before it is read again.
VAULT_FRESH = 2.0
#: Log lines kept per job.
LOG_LINES = 200
#: Seconds a cancel waits for the job's thread to see the group gone.
CANCEL_WAIT = 10

#: Bootstrap for a source run: `python3 -I` leaves the working folder off sys.path.
BOOTSTRAP = ('import runpy, sys; sys.path.insert(0, %r); '
             'runpy.run_module(%r, run_name="__main__", alter_sys=True)')

#: The vault pack kinds (`kind` in `vault --json`) that make level pieces; the others are
#: not listed.
PIECE_KINDS = ('building', 'props', 'mixed')
#: What the tab says for a job that failed, by kind; the details go to the console.
FAILED = {'convert': 'Conversion failed', 'measure': 'Measuring failed'}
#: What the tab says when another process holds the pack's lock (EXIT_LOCKED).
LOCKED = 'Another Rift Lord is converting this pack'
#: The settings key (gamepath.save_setting) of the Fab Kits settings.
SETTINGS_KEY = 'fabKits'
#: The least memory limit Settings accept (MB).
MIN_MEMORY_MB = 1024
#: Log lines of a failed job copied to the console.
CONSOLE_TAIL = 40

_LOCK = threading.Lock()
#: {item: Job}: the running job of each item, or its last finished one.
JOBS = {}
#: The last vault listing and when it was taken, the running Scan, the last failed scan
#: (time, message), and when a library last changed.
_VAULT = {'at': 0.0, 'data': None, 'scan': None, 'failed': None, 'forgot': 0.0}
_VAULT_LOCK = threading.Lock()
#: Packs waiting to convert, first first: [{item, scale, title, packId, added}].
QUEUE = []
_QLOCK = threading.RLock()


def _log(level, text):
    """A line in the app's console under Fab Kits."""
    from riftlord.core import console
    console.write(text, console.FAB_KITS, level)


def makes_pieces(row):
    """True when a vault row can make level pieces: any loose pack; a vault pack by its
    `kind`, or by having meshes when the converter reports no kind."""
    if row.get('source') == 'file':
        return True
    kind = row.get('kind')
    if kind is not None:
        return kind in PIECE_KINDS
    return bool(row.get('meshes'))


# -- settings --------------------------------------------------------------------------------

def max_workers():
    """The most workers Settings accept: every CPU but one."""
    return max(1, (os.cpu_count() or 2) - 1)


def settings():
    """{vaultRoot ('' = found by the converter), memoryMb, workers (None = the converter's
    default)} as Settings keep them."""
    saved = gamepath.load_settings().get(SETTINGS_KEY) or {}

    def number(key):
        v = saved.get(key)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None
    return {'vaultRoot': str(saved.get('vaultRoot') or ''), 'memoryMb': number('memoryMb'),
            'workers': number('workers')}


def set_settings(values):
    """Keep the keys of `values` that settings() has (None or '' resets one). A new vault
    root drops the listing. Returns settings_view()."""
    values = values or {}
    now = settings()
    out = dict(now)
    if 'vaultRoot' in values:
        root = str(values['vaultRoot'] or '').strip()
        if root:
            root = os.path.abspath(os.path.expanduser(root))
            if not os.path.isdir(root):
                raise SystemExit('%s is not a folder' % root)
        out['vaultRoot'] = root
    limits = {'memoryMb': ('Memory Limit', MIN_MEMORY_MB, None),
              'workers': ('Workers', 1, max_workers())}
    for key, (label, low, high) in limits.items():
        if key not in values:
            continue
        v = values[key]
        if v is None or v == '':
            out[key] = None
            continue
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise SystemExit('%s must be a whole number' % label)
        if v < low or (high is not None and v > high):
            raise SystemExit('%s must be %d or more' % (label, low) if high is None
                             else '%s must be from %d to %d' % (label, low, high))
        out[key] = v
    gamepath.save_setting(SETTINGS_KEY, out)
    if out['vaultRoot'] != now['vaultRoot']:
        with _VAULT_LOCK:
            _VAULT.update(data=None, at=0.0, failed=None, forgot=time.time())
    return settings_view(start=True)


def settings_view(start=False):
    """Settings with what the last vault listing says: `roots` {found, searched},
    `defaults` (the converter's `settings`, None when it reports none), `scanning`, and the
    limits. `start`: begin a listing when there is none."""
    available = unavailable() is None
    can = start and available
    with _VAULT_LOCK:
        data = _VAULT['data']
        if can and data is None and _VAULT['scan'] is None:
            _scan()
        scanning = _VAULT['scan'] is not None
    data = data or {}
    return dict(settings(), roots=data.get('roots'), defaults=data.get('settings'),
                scanning=scanning, listed=bool(data), maxWorkers=max_workers(),
                minMemoryMb=MIN_MEMORY_MB, available=available)


def _root_args():
    """`--root PATH` when Settings name the vault folder."""
    root = settings()['vaultRoot']
    return ['--root', root] if root else []


def _limit_args():
    """`--memory-mb N` and `--workers N` as Settings set them, for a converter whose last
    listing reported `settings` (one that takes them)."""
    with _VAULT_LOCK:
        caps = (_VAULT['data'] or {}).get('settings')
    if not isinstance(caps, dict):
        return []
    s, out = settings(), []
    if s['memoryMb'] and 'memory_mb' in caps:
        out += ['--memory-mb', str(s['memoryMb'])]
    if s['workers'] and 'workers' in caps:
        out += ['--workers', str(s['workers'])]
    return out


def _prctl():
    """libc's prctl on Linux, else None (resolved here: a forked child must not look it up)."""
    if not sys.platform.startswith('linux'):
        return None
    try:
        import ctypes
        return ctypes.CDLL(None, use_errno=True).prctl
    except (OSError, AttributeError):
        return None


_PRCTL = _prctl()
#: prctl's PR_SET_PDEATHSIG.
PR_SET_PDEATHSIG = 1


def _child_setup():
    """In the child before exec: SIGKILL it when the thread that started it ends (the job's
    thread, which lives as long as the child), so a server killed outright takes it along."""
    if _PRCTL is not None:
        _PRCTL(PR_SET_PDEATHSIG, signal.SIGKILL)


def library_root():
    """Where converted libraries go: the folder the Map Editor reads."""
    from riftlord.mapeditor import fablibrary
    return fablibrary.root()


def converter():
    """The command line that runs the converter, or None when this build has none."""
    script = os.environ.get(OVERRIDE_ENV)
    if script:
        python = shutil.which('python3') if gamepath.frozen() else sys.executable
        return [python, '-I', script] if python and os.path.isfile(script) else None
    try:
        if importlib.util.find_spec(MODULE) is None:
            return None
    except (ImportError, ValueError):
        return None
    if gamepath.frozen():
        return [sys.executable, FROZEN_SWITCH]
    return [sys.executable, '-I', '-c', BOOTSTRAP % (CODE_ROOT, MODULE)]


def unavailable():
    """None when Fab conversion can run here, else UNAVAILABLE (no converter, or no
    collision cooking)."""
    if converter() is None:
        return UNAVAILABLE
    from riftlord.formats.omd3 import omd3physx
    return UNAVAILABLE if omd3physx.tool_status()['pxcook'] == 'missing' else None


def frozen_main(args):
    """`RiftLordServer --fab ARGS`: the converter's own command line."""
    from riftlord.fab.__main__ import main
    return main(list(args))


def _popen(args):
    """The converter with `args`, in a process group of its own at low priority."""
    base = converter()
    if base is None:
        raise SystemExit(UNAVAILABLE)
    argv = base + list(args)
    kw = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE,
          'cwd': gamepath.scratch('fabkits')}
    if os.name == 'nt':
        kw['creationflags'] = (subprocess.BELOW_NORMAL_PRIORITY_CLASS
                               | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        argv = NICE + (IONICE if shutil.which('ionice') else []) + argv
        kw['start_new_session'] = True
        kw['preexec_fn'] = _child_setup
    return subprocess.Popen(argv, **kw)


def _kill_group(proc):
    """Kill a child and everything it started."""
    if os.name == 'nt':
        if proc.poll() is None:
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _summary(text):
    """The last JSON object line of a command's stdout, or None."""
    for line in reversed((text or '').strip().splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def run(args, timeout=COMMAND_TIMEOUT, failed='The Fab converter failed'):
    """One short converter command (`--json` added): its JSON summary, with `exit`. When it
    gives none, its stderr goes to the console and SystemExit(`failed`) is raised."""
    proc = _popen(list(args) + ['--json'])
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        proc.communicate()
        _log('error', 'fabconvert %s: no answer within %d s' % (args[0], timeout))
        raise SystemExit(failed)
    got = _summary(out.decode('utf-8', 'replace'))
    if got is None:
        tail = err.decode('utf-8', 'replace').strip().splitlines()[-CONSOLE_TAIL:] or ['no output']
        _log('error', 'fabconvert %s failed (exit %s)' % (args[0], proc.returncode))
        _log('debug', '\n'.join(tail))
        raise SystemExit(failed)
    got.setdefault('exit', proc.returncode)
    return got


# -- the vault --------------------------------------------------------------------------------

class Scan:
    """One `vault --events` listing run in its own thread: {done, total, msg} per item."""

    #: What the tab says when the vault could not be read; the details go to the console.
    FAILED = 'Could not read the Epic vault'

    def __init__(self):
        self.started = time.time()
        self.progress, self.data, self.error, self.proc = None, None, None, None
        self.root = settings()['vaultRoot']
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self._run, name='fab-vault', daemon=True)

    def _run(self):
        # the child is started here: its death signal follows this thread, which lives as long
        try:
            self.proc = _popen(['vault', '--out', library_root(), '--json', '--events']
                               + (['--root', self.root] if self.root else []))
        except (Exception, SystemExit) as exc:        # noqa: BLE001
            _log('error', 'fabconvert vault did not start: %s' % exc)
            self.error = str(exc) if isinstance(exc, SystemExit) else self.FAILED
            return self._finish()
        out = []
        reader = threading.Thread(target=lambda: out.append(self.proc.stdout.read()), daemon=True)
        reader.start()
        tail = collections.deque(maxlen=CONSOLE_TAIL)
        for raw in self.proc.stderr:
            line = raw.decode('utf-8', 'replace').rstrip('\n')
            if line.startswith('{'):
                try:
                    ev = json.loads(line)
                    self.progress = {k: ev.get(k) for k in ('done', 'total', 'msg')}
                    continue
                except ValueError:
                    pass
            if line.strip():
                tail.append(line)
        self.proc.wait()
        reader.join()
        self.proc.stdout.close()
        self.proc.stderr.close()
        self.data = _summary(b''.join(out).decode('utf-8', 'replace'))
        if self.data is None:
            _log('error', 'fabconvert vault failed (exit %s)' % self.proc.returncode)
            if tail:
                _log('debug', '\n'.join(tail))
            self.error = self.FAILED
        else:
            self.data['scannedAt'] = self.started
        self._finish()

    def _finish(self):
        with _VAULT_LOCK:
            if self.root != settings()['vaultRoot']:
                pass                                   # the vault folder changed meanwhile
            elif self.data is not None:
                # a library changed while this scan ran: kept, but read again next time
                stale = _VAULT['forgot'] > self.started
                _VAULT.update(data=self.data, at=0.0 if stale else time.time(), failed=None)
            else:
                _VAULT['failed'] = (time.time(), self.error)
            if _VAULT['scan'] is self:
                _VAULT['scan'] = None
        self.finished.set()

    def view(self):
        p = self.progress or {}
        total = int(p.get('total') or 0)
        return {'pid': self.proc.pid if self.proc else None, 'started': self.started,
                'done': int(p.get('done') or 0), 'total': total, 'msg': p.get('msg') or '',
                'percent': round(100.0 * int(p.get('done') or 0) / total, 1) if total else 0.0}


def _scan():
    """The running scan, or a new one (called holding _VAULT_LOCK)."""
    if _VAULT['scan'] is None:
        _VAULT['scan'] = Scan()
        _VAULT['scan'].thread.start()
    return _VAULT['scan']


def vault_status(max_age=VAULT_FRESH):
    """What the tab reads: the listing (`scanning` False) while it is younger than `max_age`
    seconds and no scan runs; else the running scan, started if none runs, with the last
    listing's items while there is one (`scanning` True)."""
    with _VAULT_LOCK:
        data = _VAULT['data']
        if data is not None and _VAULT['scan'] is None and time.time() - _VAULT['at'] < max_age:
            return dict(data, scanning=False)
        failed = _VAULT['failed']
        if _VAULT['scan'] is None and failed and max_age and time.time() - failed[0] < max_age:
            raise SystemExit(failed[1])
        out = {'scanning': True, 'scan': _scan().view(), 'library': library_root()}
        if data is not None:
            out.update(items=data.get('items'), roots=data.get('roots'),
                       scannedAt=data.get('scannedAt'))
        return out


def vault(max_age=VAULT_FRESH):
    """The vault listing, waiting for a scan when the last one is older than `max_age`
    seconds or a scan runs (None: any listing at all will do)."""
    with _VAULT_LOCK:
        data = _VAULT['data']
        if data is not None and (max_age is None or (
                _VAULT['scan'] is None and time.time() - _VAULT['at'] < max_age)):
            return data
        scan = _scan()
    scan.finished.wait()
    if scan.data is None:
        raise SystemExit(scan.error or 'the vault could not be read')
    return scan.data


def scan_view():
    with _VAULT_LOCK:
        return _VAULT['scan'].view() if _VAULT['scan'] else None


def _forget_vault():
    """A library changed: the next read scans again (the last listing stays on screen)."""
    with _VAULT_LOCK:
        _VAULT.update(at=0.0, forgot=time.time())


def _forget_library(pack_id, cache=False):
    """Drop what the Map Editor holds of one library (open pak, catalogue, meshes)."""
    if not pack_id:
        return
    from riftlord.mapeditor import fablibrary
    try:
        fablibrary.forget(pack_id, cache=cache)
    except SystemExit:
        pass


def _vault_row(item):
    for row in (vault(max_age=None).get('items') or []):
        if row.get('item') == item:
            return row
    raise SystemExit('no such vault pack: %s' % item)


# -- jobs ----------------------------------------------------------------------------------------

class Job:
    """One convert or measure of one vault item, run in a thread."""

    def __init__(self, item, kind, args, pack_id=None, scale=None, title=None):
        self.item, self.kind, self.args = item, kind, list(args)
        self.pack_id, self.scale = pack_id, scale
        self.title = title or os.path.basename(os.path.normpath(item))
        self.state = 'running'
        self.started, self.ended = time.time(), None
        self.progress = None
        self.percent = 0.0
        #: seconds left as the converter last estimated them (`eta_s`), or None
        self.eta = None
        self.log = collections.deque(maxlen=LOG_LINES)
        self.result, self.exit, self.error, self.failure = None, None, None, None
        self.proc = None
        self.cancelled = False
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name='fab-%s-%s' % (kind, item),
                                       daemon=True)

    def start(self):
        self.thread.start()
        self._ready.wait()
        if self.error and self.proc is None:
            raise SystemExit(self.error)
        return self

    def _event(self, ev):
        self.progress = {k: ev.get(k) for k in ('stage', 'index', 'stages', 'done', 'total', 'msg')}
        number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
        eta = ev.get('eta_s')
        self.eta = float(eta) if number(eta) and eta >= 0 else None
        frac = ev.get('fraction')
        if number(frac):
            self.percent = max(self.percent, min(100.0, 100.0 * max(0.0, frac)))
            return
        try:
            stages = max(1, int(ev.get('stages') or 1))
            frac = (int(ev.get('done') or 0) / max(1, int(ev.get('total') or 0))
                    if ev.get('total') else 0.0)
            self.percent = max(self.percent,
                               min(100.0, 100.0 * (int(ev.get('index') or 0) + frac) / stages))
        except (TypeError, ValueError):
            pass

    def fail(self, error, exit=None):
        """End as failed: `error` (the converter's words) to the console, a short line for the
        tab."""
        self.state, self.ended, self.error, self.exit = 'failed', time.time(), error, exit
        self.failure = LOCKED if exit == EXIT_LOCKED else FAILED.get(self.kind, FAILED['convert'])
        _log('error', '%s: %s (exit %s): %s' % (self.title, self.failure.lower(), exit, error))
        if self.log:
            _log('debug', '\n'.join(list(self.log)[-CONSOLE_TAIL:]))

    def _run(self):
        # the child is started here: its death signal follows this thread, which lives as long
        try:
            self.proc = _popen(self.args)
        except (Exception, SystemExit) as exc:        # noqa: BLE001
            self.fail(str(exc))
            self._ready.set()
            _advance()
            return
        self._ready.set()
        out = []
        reader = threading.Thread(target=lambda: out.append(self.proc.stdout.read()), daemon=True)
        reader.start()
        for raw in self.proc.stderr:
            line = raw.decode('utf-8', 'replace').rstrip('\n')
            if line.startswith('{'):
                try:
                    self._event(json.loads(line))
                    continue
                except ValueError:
                    pass
            if line.strip():
                self.log.append(line)
        self.proc.wait()
        reader.join()
        self.exit = self.proc.returncode
        self.result = _summary(b''.join(out).decode('utf-8', 'replace'))
        if self.pack_id is None and (self.result or {}).get('library'):
            # a loose pack's first convert: its pack id is its library folder's name
            self.pack_id = os.path.basename(os.path.normpath(self.result['library']))
        problems = (self.result or {}).get('problems') or []
        if self.cancelled:
            self.state = 'cancelled'
            _log('info', '%s: cancelled' % self.title)
        elif self.exit == EXIT_OK:
            self.state = 'done'
            if self.kind == 'convert':
                _log('info', '%s: converted' % self.title)
        elif self.exit == EXIT_PROBLEMS:
            self.state = 'problems'
            _log('warn', '%s: converted with %d problem(s)' % (
                self.title, (self.result or {}).get('problem_count') or len(problems)))
            if problems:
                _log('debug', '\n'.join(str(p) for p in problems))
        else:
            self.fail((self.result or {}).get('error')
                      or (self.log[-1] if self.log else 'no output'), self.exit)
        if self.state in ('done', 'problems'):
            self.percent = 100.0
        self.eta = None
        self.ended = time.time()
        self.proc.stdout.close()
        self.proc.stderr.close()
        _forget_vault()
        if self.kind == 'convert':
            _forget_library(self.pack_id)
            trim_cache()
        _advance()

    def running(self):
        return self.state == 'running'

    def cancel(self):
        if not self.running() or self.proc is None:
            return
        self.cancelled = True
        _kill_group(self.proc)
        self.thread.join(CANCEL_WAIT)

    def view(self):
        res = self.result or {}
        return {'item': self.item, 'kind': self.kind, 'state': self.state, 'title': self.title,
                'pid': self.proc.pid if self.proc else None, 'scale': self.scale,
                'packId': self.pack_id, 'started': self.started, 'ended': self.ended,
                'progress': self.progress, 'percent': round(self.percent, 1),
                'eta': round(self.eta) if self.eta is not None and self.running() else None,
                'failure': self.failure, 'exit': self.exit,
                'problems': res.get('problems') or [], 'problemCount': res.get('problem_count') or 0,
                'measure': ({'scale': res.get('scale'), 'sChar': res.get('s_char'),
                             'evidence': res.get('evidence') or []}
                            if self.kind == 'measure' and res.get('ok') else None),
                'log': list(self.log)[-5:]}


def jobs():
    with _LOCK:
        return {item: job.view() for item, job in JOBS.items()}


def queue_view():
    """The packs waiting to convert, in order: [{item, title, scale, packId, position}]."""
    with _QLOCK:
        return [dict(e, position=k + 1) for k, e in enumerate(QUEUE)]


def state():
    """{available, message, needs, jobs, queue}: what the tab polls; `needs` is the detail
    line under the message (omd3physx.tool_status: what was not found)."""
    why = unavailable()
    from riftlord.formats.omd3 import omd3physx
    status = omd3physx.tool_status()
    needs = status['detail'] if why and status['pxcook'] == 'missing' else None
    return {'available': why is None, 'message': why, 'needs': needs,
            'library': library_root(), 'jobs': jobs(), 'queue': queue_view(),
            'scan': scan_view()}


def _start(item, kind, args, pack_id=None, scale=None, title=None):
    with _LOCK:
        held = JOBS.get(item)
        if held and held.running():
            raise SystemExit('%s is already being %s' % (title or item, 'measured'
                                                         if held.kind == 'measure' else 'converted'))
        if kind == 'convert' and any(j.running() and j.kind == 'convert' for j in JOBS.values()):
            raise SystemExit('another pack is being converted')
        job = Job(item, kind, args, pack_id=pack_id, scale=scale, title=title)
        JOBS[item] = job
    return job.start().view()


def _converting():
    with _LOCK:
        return any(j.kind == 'convert' and j.running() for j in JOBS.values())


def _busy(item):
    with _LOCK:
        job = JOBS.get(item)
        return job is not None and job.running()


#: The converter cache trim's thread while it moves packs aside (converts wait for it).
_TRIM = {'thread': None}


def _trimming():
    return _TRIM['thread'] is not None


def trim_cache():
    """Empty the converter's working folders of packs not converted for a day
    (riftlord.fab.cachetrim) in a thread, logging what it frees; at app start and after each
    convert. Not while a job runs; running and queued packs are left alone, and converts wait
    until the folders are moved aside. True when a trim started."""
    try:
        if importlib.util.find_spec('riftlord.fab.cachetrim') is None:
            return False
    except (ImportError, ValueError):
        return False
    with _QLOCK, _LOCK:
        if _trimming() or any(j.running() for j in JOBS.values()):
            return False
        busy = {e['packId'] for e in QUEUE if e.get('packId')}
        with _VAULT_LOCK:
            rows = list((_VAULT['data'] or {}).get('items') or [])

        def moved():
            _TRIM['thread'] = None
            _advance()

        def run():
            from riftlord.fab import cachetrim
            try:
                got = cachetrim.trim(busy=busy, rows=rows, library=library_root(),
                                     log=lambda text: _log('info', text), on_moved=moved)
                if got.get('stopped'):
                    _log('debug', 'Fab converter cache not trimmed: %s' % got['stopped'])
            except Exception as exc:                  # noqa: BLE001 - a trim never stops the app
                _log('warn', 'Fab converter cache trim failed: %s' % exc)
                if _trimming():
                    moved()

        _TRIM['thread'] = threading.Thread(target=run, name='fab-trim', daemon=True)
        _TRIM['thread'].start()
    return True


def _advance():
    """Start the first waiting pack that is not being measured, while no convert or cache
    trim runs. A pack that cannot start ends as a failed job and the next one is tried."""
    with _QLOCK:
        while QUEUE and not _converting() and not _trimming():
            entry = next((e for e in QUEUE if not _busy(e['item'])), None)
            if entry is None:
                return
            QUEUE.remove(entry)
            args = ['convert', entry['item'], '--out', library_root(), '--json', '--events']
            args += _root_args() + _limit_args()
            if entry['scale'] is not None:
                args += ['--scale', repr(entry['scale'])]
            _forget_library(entry['packId'])
            t = time.time()
            try:
                _start(entry['item'], 'convert', args, pack_id=entry['packId'],
                       scale=entry['scale'], title=entry['title'])
            except SystemExit as exc:
                with _LOCK:
                    held = JOBS.get(entry['item'])
                    if held is not None and held.kind == 'convert' and held.started >= t:
                        continue                       # it started and failed: already said
                job = Job(entry['item'], 'convert', args, pack_id=entry['packId'],
                          scale=entry['scale'], title=entry['title'])
                job.fail(str(exc))
                with _LOCK:
                    JOBS[entry['item']] = job


def _item(item):
    """A vault item's folder name, or a loose pack's absolute path (a .zip or a folder)."""
    item = str(item or '')
    if os.path.isabs(item):
        return os.path.normpath(item)
    if not item or '/' in item or '\\' in item or item.startswith('.'):
        raise SystemExit('no such vault pack: %r' % item)
    return item


def _file_source(item):
    """A loose pack's path, checked to be a .zip or a folder; SystemExit otherwise."""
    if os.path.isdir(item) or (os.path.isfile(item) and item.lower().endswith('.zip')):
        return item
    raise SystemExit('%s is not a .zip or a folder' % item if os.path.exists(item)
                     else '%s is missing' % item)


def _row_or_file(item):
    """The vault row of an item; for a loose pack not listed yet, a stand-in row."""
    try:
        row = _vault_row(item)
    except SystemExit:
        if not os.path.isabs(item):
            raise
        return {'item': item, 'title': os.path.basename(item), 'pack_id': None, 'meshes': None,
                'source': 'file', 'exists': os.path.exists(item)}
    if row.get('source') == 'file' and not row.get('exists', True):
        raise SystemExit('%s is missing' % item)
    return row


def convert(item, scale=None):
    """Queue a vault item, or a loose pack by its path, to convert into the library root; it
    starts at once when nothing else converts. Returns {item, job, queue}."""
    if unavailable():
        raise SystemExit(UNAVAILABLE)
    item = _item(item)
    row = _row_or_file(item)
    title = row.get('title') or os.path.basename(item)
    if os.path.isabs(item):
        _file_source(item)
    elif not makes_pieces(row):
        raise SystemExit('%s has no level pieces to convert' % title)
    if scale is not None:
        scale = float(scale)
        if not scale > 0:
            raise SystemExit('The scale must be above 0')
    with _QLOCK:
        if any(e['item'] == item for e in QUEUE):
            raise SystemExit('%s is already queued' % title)
        with _LOCK:
            held = JOBS.get(item)
            if held and held.running() and held.kind == 'convert':
                raise SystemExit('%s is already being converted' % title)
        QUEUE.append({'item': item, 'title': title, 'scale': scale,
                      'packId': row.get('pack_id'), 'added': time.time()})
    _advance()
    return {'item': item, 'job': jobs().get(item), 'queue': queue_view()}


def move(item, index):
    """Move a waiting pack to `index` (0 = next) in the queue. Returns queue_view()."""
    item = _item(item)
    with _QLOCK:
        entry = next((e for e in QUEUE if e['item'] == item), None)
        if entry is None:
            raise SystemExit('%s is not queued' % item)
        QUEUE.remove(entry)
        QUEUE.insert(max(0, min(len(QUEUE), int(index))), entry)
    return queue_view()


def measure(item):
    """Start measuring a vault item's scale (cached by the converter after the first time)."""
    if unavailable():
        raise SystemExit(UNAVAILABLE)
    item = _item(item)
    row = _row_or_file(item)
    if os.path.isabs(item):
        _file_source(item)
    return _start(item, 'measure', ['measure', item, '--json'] + _root_args(),
                  title=row.get('title') or os.path.basename(item))


def cancel(item):
    """Take a waiting pack off the queue, or stop an item's running job. Returns
    {item, job, queue}."""
    item = _item(item)
    with _QLOCK:
        entry = next((e for e in QUEUE if e['item'] == item), None)
        if entry is not None:
            QUEUE.remove(entry)
            _log('info', '%s: taken off the queue' % entry['title'])
            return {'item': item, 'job': jobs().get(item), 'queue': queue_view()}
    with _LOCK:
        job = JOBS.get(item)
    if job is None or not job.running():
        raise SystemExit('nothing is running for %s' % item)
    job.cancel()
    return {'item': item, 'job': job.view(), 'queue': queue_view()}


def delete(item):
    """Delete a vault item's or loose pack's library (and the converter's cache of it); a
    loose pack leaves the list with it."""
    if unavailable():
        raise SystemExit(UNAVAILABLE)
    item = _item(item)
    with _QLOCK:
        if any(e['item'] == item for e in QUEUE):
            raise SystemExit('%s is queued; cancel that first' % item)
    with _LOCK:
        job = JOBS.get(item)
        if job and job.running():
            raise SystemExit('%s is being %s; cancel that first'
                             % (item, 'measured' if job.kind == 'measure' else 'converted'))
    row = _vault_row(item)
    title = row.get('title') or item
    failed = 'Could not delete the Fab Kit for %s' % title
    _forget_library(row.get('pack_id'), cache=True)
    got = run(['delete', item, '--out', library_root()] + _root_args(), failed=failed)
    _forget_vault()
    if not got.get('ok'):
        _log('error', '%s: delete failed: %s' % (title, got.get('error') or got))
        raise SystemExit(failed)
    _log('info', '%s: Fab Kit deleted' % title)
    with _LOCK:
        if JOBS.get(item) and not JOBS[item].running():
            del JOBS[item]
    return {'ok': True, 'item': item, 'packId': got.get('pack_id') or row.get('pack_id'),
            'removed': got.get('removed') or []}


def stop_all():
    """Empty the queue and kill every running job's process group (the server is stopping)."""
    with _QLOCK:
        del QUEUE[:]
    with _LOCK:
        running = [j for j in JOBS.values() if j.running()]
    for job in running:
        job.cancelled = True
        if job.proc is not None:
            _kill_group(job.proc)
    with _VAULT_LOCK:
        scan = _VAULT['scan']
    if scan is not None and scan.proc is not None:
        _kill_group(scan.proc)
