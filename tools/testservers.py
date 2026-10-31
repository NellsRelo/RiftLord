"""Start Rift Lord test servers in scratch folders, find their real ports, and always stop them.

    python3 testservers.py run [--kind workshop|riftlord|onslaught] [--frozen [BIN]]
                               [--mission ID ...] [--missions-from DIR] -- COMMAND ...
    python3 testservers.py cleanup        # stop what a crashed run left, remove its folders
    python3 testservers.py list           # what the registry holds

From Python:

    import testservers
    with testservers.TestServer('workshop', frozen=True, missions=['900_Fixture_Edit_P']) as srv:
        srv.urls        # {'shell': ..., 'riftlord': ..., 'onslaught': ...} as the server reports them
        srv.get('/api/tools')                        # the shell, with its guard header
        srv.post('/api/plan/Scenario_09', plan, tool='riftlord')
        prof = testservers.browser_profile()         # removed with the server, or by cleanup

Each server runs in its own process group with XDG_DATA_HOME, XDG_CACHE_HOME,
XDG_CONFIG_HOME, TMPDIR and the Missions root (OMD3_BUCKET) in a fresh folder on scratch, so it
never reads or writes the user's own data, settings or Missions. Missions records named
by `missions` are copied in (record, pictures and built folder) from the test missions
(FIXTURE_MISSIONS, tests/fixtures/missions) or `mission_source`; the originals are only
read. The ports are read from what the server prints, never assumed: the user's own
Rift Lord may hold 8870-8872.

Every server and browser profile is written to a registry (REGISTRY) when it starts and
removed when it stops, so `cleanup` can stop a crashed run's leftovers. Processes are
stopped by process group, and only groups this module started (pid + start time match).
"""

import argparse
import atexit
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

#: The repository root: servers run from here.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: Big scratch lives on the 2 TB disk; /tmp is RAM.
SCRATCH = os.environ.get('RIFTLORD_TEST_SCRATCH') or (
    '/mnt/2tb_pro_a/tmp/rift-lord-tests' if os.path.isdir('/mnt/2tb_pro_a/tmp')
    else os.path.join(tempfile.gettempdir(), 'rift-lord-tests'))
#: One JSON file per live server or browser profile.
REGISTRY = os.path.join(os.path.expanduser('~'), '.cache', 'rift-lord', 'testservers')
#: The test missions (a Missions folder kept with the tests, never the user's): records
#: are copied from here.
FIXTURE_MISSIONS = os.path.join(ROOT, 'tests', 'fixtures', 'missions')
#: The frozen Linux server tools/build.py builds (its DIST).
FROZEN = os.path.join(ROOT, 'dist', 'RiftLordServer')
#: Each API's guard header, by tool.
GUARDS = {'shell': 'X-Workshop', 'riftlord': 'X-Rift-Lord', 'onslaught': 'X-Onslaught-Editor'}
#: Seconds to wait for a server to print its address.
START_TIMEOUT = 180
#: Seconds a server gets to stop after its stdin closes, before its group is killed.
STOP_GRACE = 15
#: Memory ceiling for a server and everything it starts (systemd scope), or None.
MEMORY_MAX = '20G'

_LIVE = []
_LOCK = threading.Lock()


def _start_time(pid):
    """The kernel's start time of `pid` (clock ticks since boot), or None if gone."""
    try:
        with open('/proc/%d/stat' % pid) as fh:
            return int(fh.read().rsplit(')', 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _group_members(pgid):
    """PIDs whose process group is `pgid`."""
    out = []
    for stat in glob.glob('/proc/[0-9]*/stat'):
        try:
            with open(stat) as fh:
                fields = fh.read().rsplit(')', 1)[1].split()
            if int(fields[2]) == pgid:
                out.append(int(stat.split('/')[2]))
        except (OSError, ValueError, IndexError):
            continue
    return out


#: A program that runs a command with a parent-death signal (util-linux), or None.
SETPRIV = shutil.which('setpriv') if sys.platform.startswith('linux') else None


def dies_with_parent(cmd):
    """`cmd` run so that it is killed when the process that starts it dies, however that
    one ends (setpriv --pdeathsig KILL; the signal passes through exec, so wrappers such as
    nice keep it). Unchanged where setpriv is missing. The thread that starts it must wait
    for it: the signal comes when that thread ends."""
    return [SETPRIV, '--pdeathsig', 'KILL'] + list(cmd) if SETPRIV else list(cmd)


def _kill_group(pgid, sig=signal.SIGKILL):
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _browsers_using(profile):
    """PIDs of this user's processes started with --user-data-dir=`profile` (Chromium
    rewrites its command line space-separated; profile paths hold no spaces)."""
    flag = ('--user-data-dir=' + profile).encode()
    out = []
    for cmdline in glob.glob('/proc/[0-9]*/cmdline'):
        try:
            if os.stat(cmdline).st_uid != os.getuid():
                continue
            with open(cmdline, 'rb') as fh:
                if flag in fh.read().replace(b'\0', b' ').split(b' '):
                    out.append(int(cmdline.split('/')[2]))
        except (OSError, ValueError):
            continue
    return out


def _stop_browsers(profile, timeout=10):
    """Stop a browser left on `profile` (puppeteer starts it in its own process group, so
    a killed test driver leaves it running): SIGTERM, then SIGKILL. Returns the count."""
    pids = _browsers_using(profile) if profile else []
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        end = time.time() + timeout
        while [p for p in pids if _alive(p)] and time.time() < end:
            time.sleep(0.2)
        if not [p for p in pids if _alive(p)]:
            break
    return len(pids)


def _register(entry):
    os.makedirs(REGISTRY, exist_ok=True)
    path = os.path.join(REGISTRY, '%s-%d-%d.json' % (entry['type'], os.getpid(),
                                                     int(time.time() * 1000)))
    with open(path, 'w') as fh:
        json.dump(entry, fh, indent=1)
    return path


def _unregister(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _wait_group_gone(pgid, timeout=10):
    end = time.time() + timeout
    while _group_members(pgid) and time.time() < end:
        time.sleep(0.2)


def _safe_remove_tree(path):
    """Remove a folder this module made: only under SCRATCH. Retried, because a process
    killed a moment ago may still hold a file open in it."""
    if not (path and os.path.realpath(path).startswith(os.path.realpath(SCRATCH) + os.sep)):
        return
    for _ in range(10):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(0.5)


def browser_profile(prefix='chrome'):
    """A fresh browser profile folder on scratch, removed by stop_all() or cleanup."""
    os.makedirs(SCRATCH, exist_ok=True)
    path = tempfile.mkdtemp(prefix=prefix + '-', dir=SCRATCH)
    reg = _register({'type': 'profile', 'path': path, 'owner': os.getpid()})
    with _LOCK:
        _LIVE.append(('profile', path, reg))
    return path


def remove_profile(path):
    with _LOCK:
        for item in list(_LIVE):
            if item[0] == 'profile' and item[1] == path:
                _LIVE.remove(item)
                _unregister(item[2])
    _stop_browsers(path)
    _safe_remove_tree(path)


def copy_mission(ident, missions_root, source=FIXTURE_MISSIONS, built=True):
    """Copy a Missions record (json + pictures) and, with `built`, its built folder and
    CustomMaps notes from `source` into `missions_root` (for a test mission, its pinned
    build as `<id>.pak`). `source` is only read."""
    rec_dir = os.path.join(source, '.riftlord', 'records')
    files = sorted(glob.glob(os.path.join(glob.escape(rec_dir), glob.escape(ident) + '.*')))
    if not any(f.endswith('.json') for f in files):
        raise SystemExit('no Missions record %s in %s' % (ident, rec_dir))
    dest = os.path.join(missions_root, '.riftlord', 'records')
    os.makedirs(dest, exist_ok=True)
    for f in files:
        shutil.copy2(f, dest)
    if built:
        folder = os.path.join(source, ident)
        if os.path.isdir(folder):
            shutil.copytree(folder, os.path.join(missions_root, ident), symlinks=False)
        elif os.path.abspath(source) == os.path.abspath(FIXTURE_MISSIONS):
            # a test mission's build: its pinned comparison baseline (buildcompare.BASELINES)
            import buildcompare
            pak = buildcompare.baseline_pak(ident)
            if pak:
                shutil.copy2(pak, os.path.join(missions_root, ident + '.pak'))
        for side in glob.glob(os.path.join(glob.escape(source), glob.escape(ident) + '.*')):
            if os.path.isfile(side):
                shutil.copy2(side, missions_root)


class TestServer:
    """One server: 'workshop' (the app's server: shell + both editors, `--sidecar`),
    'riftlord' (the Map Editor alone) or 'onslaught' (the Onslaught Editor alone).
    `frozen`: True for FROZEN, a path for another binary, False
    for source (`python -m ...`). `missions`: record ids copied into its Missions."""

    def __init__(self, kind='workshop', frozen=False, missions=(), root=None, env=None,
                 python=None, low_priority=True, memory_max=MEMORY_MAX, keep=False,
                 mission_source=FIXTURE_MISSIONS, copy_built=True, log=None):
        if kind not in ('workshop', 'riftlord', 'onslaught'):
            raise ValueError('kind must be workshop, riftlord or onslaught')
        if frozen and kind != 'workshop':
            raise ValueError('only the workshop server is frozen')
        self.kind = kind
        self.frozen = FROZEN if frozen is True else frozen
        self.missions = list(missions)
        self.mission_source = mission_source
        self.copy_built = copy_built
        self.extra_env = dict(env or {})
        self.python = python or sys.executable
        self.low_priority = low_priority
        self.memory_max = memory_max
        self.keep = keep
        self.made_root = root is None
        if root is None:
            os.makedirs(SCRATCH, exist_ok=True)
            root = tempfile.mkdtemp(prefix='%s-%s-' % (kind, time.strftime('%m%d-%H%M%S')),
                                    dir=SCRATCH)
        self.root = root
        self.dirs = {k: os.path.join(root, k) for k in ('data', 'cache', 'config', 'Missions', 'tmp')}
        self.log_path = log or os.path.join(root, 'server.log')
        self.proc = None
        self.urls = {}
        self.reg = None
        self.started = None
        self.exit_code = None

    # -- life cycle -------------------------------------------------------------------
    def command(self):
        if self.kind == 'workshop':
            cmd = [self.frozen] if self.frozen else [self.python, '-B', '-m', 'riftlord']
            cmd.append('--sidecar')
        elif self.kind == 'riftlord':
            cmd = [self.python, '-B', '-m', 'riftlord.mapeditor', '--port', '0', '--no-browser']
        else:
            cmd = [self.python, '-B', '-m', 'riftlord.onslaught', '--port', '0', '--no-window']
        if self.low_priority:
            cmd = ['nice', '-n', '10', 'ionice', '-c', '3'] + cmd
        if self.memory_max and shutil.which('systemd-run'):
            # --scope execs the command in this process, so the PID stays ours
            cmd = ['systemd-run', '--user', '--scope', '-q', '-p',
                   'MemoryMax=%s' % self.memory_max] + cmd
        return cmd

    def environment(self):
        env = dict(os.environ)
        for k in self.dirs:
            os.makedirs(self.dirs[k], exist_ok=True)
        env.update(XDG_DATA_HOME=self.dirs['data'], XDG_CACHE_HOME=self.dirs['cache'],
                   XDG_CONFIG_HOME=self.dirs['config'], OMD3_BUCKET=self.dirs['Missions'],
                   #: the Map Editor's plan files (riftlord.leveldata.PLANS), off the checkout
                   RIFTLORD_PLANS=os.path.join(self.dirs['data'], 'riftlord-plans'),
                   TMPDIR=self.dirs['tmp'], TEMP=self.dirs['tmp'], TMP=self.dirs['tmp'],
                   PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1')
        env.update(self.extra_env)
        return env

    def start(self, timeout=START_TIMEOUT):
        env = self.environment()
        for ident in self.missions:
            copy_mission(ident, self.dirs['Missions'], self.mission_source, self.copy_built)
        self._log = open(self.log_path, 'wb')
        self.proc = subprocess.Popen(dies_with_parent(self.command()), cwd=ROOT, env=env,
                                     stdin=subprocess.PIPE, stdout=self._log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        self.started = time.time()
        self.reg = _register({'type': 'server', 'kind': self.kind, 'pid': self.proc.pid,
                              'pgid': self.proc.pid, 'start': _start_time(self.proc.pid),
                              'root': self.root if (self.made_root and not self.keep) else None,
                              'owner': os.getpid(), 'cmd': self.command()})
        with _LOCK:
            _LIVE.append(('server', self, self.reg))
        try:
            self._wait_ready(timeout)
        except BaseException:
            self.stop()
            raise
        return self

    def _wait_ready(self, timeout):
        marks = {'workshop': 'RIFTLORD_READY ', 'riftlord': 'Map Editor on ',
                 'onslaught': ' on http://'}
        mark = marks[self.kind]
        end = time.time() + timeout
        while time.time() < end:
            text = self.log_text()
            for line in text.splitlines():
                if mark in line and 'http://' in line:
                    url = 'http://' + line.split('http://', 1)[1].split()[0]
                    url = url.rstrip('/')
                    if self.kind == 'workshop':
                        self.urls['shell'] = url
                        tools = self.get('/api/tools')['tools']
                        for name, u in tools.items():
                            self.urls[name] = u.rstrip('/')
                    else:
                        self.urls[self.kind] = url
                    return
            if self.proc.poll() is not None:
                raise RuntimeError('%s server exited (%s) before it was ready:\n%s'
                                   % (self.kind, self.proc.returncode, text[-3000:]))
            time.sleep(0.5)
        raise TimeoutError('%s server printed no address within %ds:\n%s'
                           % (self.kind, timeout, self.log_text()[-3000:]))

    def stop(self, grace=STOP_GRACE):
        """Close stdin (how the app window stops it), wait, then kill its process group.
        Returns the exit code, or None if it had to be killed."""
        if self.proc is None:
            return self.exit_code
        code = None
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            if self.kind != 'workshop':                  # no stdin watch: ask politely
                _kill_group(self.proc.pid, signal.SIGTERM)
            try:
                code = self.proc.wait(grace)
            except subprocess.TimeoutExpired:
                code = None
        else:
            code = self.proc.returncode
        _kill_group(self.proc.pid)                       # anything it left (bakers)
        _wait_group_gone(self.proc.pid)
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            pass
        self._log.close()
        self.exit_code = code
        with _LOCK:
            for item in list(_LIVE):
                if item[1] is self:
                    _LIVE.remove(item)
        _unregister(self.reg)
        self.proc = None
        if self.made_root and not self.keep:
            _safe_remove_tree(self.root)
        return code

    def __enter__(self):
        return self.start() if self.proc is None else self

    def __exit__(self, *exc):
        self.stop()

    # -- talking to it ----------------------------------------------------------------
    def log_text(self):
        try:
            with open(self.log_path, 'rb') as fh:
                return fh.read().decode('utf-8', 'replace')
        except OSError:
            return ''

    def url(self, tool=None):
        return self.urls[tool or ('shell' if self.kind == 'workshop' else self.kind)]

    def request(self, path, body=None, tool=None, timeout=600, raw=False, method=None):
        """GET (body None) or POST JSON to `path` on `tool`'s server with its guard
        header. Returns parsed JSON, or (status, content type, bytes) with `raw`."""
        tool = tool or ('shell' if self.kind == 'workshop' else self.kind)
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.urls[tool] + path, data=data, method=method or (
            'GET' if body is None else 'POST'), headers={GUARDS[tool]: '1',
                                                         'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            blob = r.read()
            if raw:
                return r.status, r.headers.get('Content-Type'), blob
            return json.loads(blob or b'{}')

    def get(self, path, tool=None, **kw):
        return self.request(path, None, tool, **kw)

    def post(self, path, body, tool=None, **kw):
        return self.request(path, body if body is not None else {}, tool, **kw)


def stop_all():
    """Stop every server and remove every profile this process started."""
    with _LOCK:
        items = list(_LIVE)
    for kind, obj, reg in items:
        if kind == 'server':
            obj.stop()
        else:
            remove_profile(obj)


atexit.register(stop_all)


def _on_signal(sig, frame):
    stop_all()
    raise SystemExit(128 + sig)


def install_signal_handlers():
    """Stop everything on SIGTERM/SIGHUP too (atexit alone misses them)."""
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _on_signal)


def entries():
    out = []
    for path in sorted(glob.glob(os.path.join(REGISTRY, '*.json'))):
        try:
            with open(path) as fh:
                out.append((path, json.load(fh)))
        except (OSError, ValueError):
            out.append((path, {}))
    return out


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def cleanup(verbose=True):
    """Stop servers and remove profiles whose owning run has ended. Returns a count."""
    n = 0
    for path, e in entries():
        owner = e.get('owner')
        if owner and (owner == os.getpid() or _alive(owner)):
            continue                                     # this run's own, or a live run's
        if e.get('type') == 'server':
            pgid, pid = e.get('pgid'), e.get('pid')
            leader_ours = pid and e.get('start') is not None and _start_time(pid) == e['start']
            members = _group_members(pgid) if pgid else []
            if members and (leader_ours or not _alive(pid)):
                # the leader is ours, or gone with its group left behind (bakers)
                _kill_group(pgid)
                _wait_group_gone(pgid)
                if verbose:
                    print('stopped %s server group %d (%d processes)'
                          % (e.get('kind'), pgid, len(members)))
            _safe_remove_tree(e.get('root'))
        elif e.get('type') == 'profile':
            stopped = _stop_browsers(e.get('path'))
            _safe_remove_tree(e.get('path'))
            if verbose:
                print('removed browser profile %s%s' % (
                    e.get('path'), ' (stopped %d browser processes)' % stopped if stopped else ''))
        _unregister(path)
        n += 1
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    run = sub.add_parser('run', help='start a server, run COMMAND with its addresses in '
                         'RL_SHELL / RL_RIFTLORD / RL_ONSLAUGHT (and RL_MISSIONS), stop it')
    run.add_argument('--kind', default='workshop', choices=['workshop', 'riftlord', 'onslaught'])
    run.add_argument('--frozen', nargs='?', const=True, default=False,
                     help='run the frozen server (default %s)' % FROZEN)
    run.add_argument('--mission', action='append', default=[],
                     help='copy this Missions record in (repeatable)')
    run.add_argument('--missions-from', default=FIXTURE_MISSIONS,
                     help='the Missions folder --mission records are copied from (only read; '
                          'default the test missions)')
    run.add_argument('--keep', action='store_true', help='keep the scratch folder')
    run.add_argument('command', nargs=argparse.REMAINDER)
    sub.add_parser('cleanup', help="stop a crashed run's servers, remove its folders")
    sub.add_parser('list', help='show the registry')
    args = ap.parse_args(argv)
    if args.cmd == 'cleanup':
        print('%d registry entries cleaned' % cleanup())
        return 0
    if args.cmd == 'list':
        for path, e in entries():
            print(os.path.basename(path), json.dumps(e))
        return 0
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        ap.error('run needs a COMMAND after --')
    install_signal_handlers()
    with TestServer(args.kind, frozen=args.frozen, missions=args.mission, keep=args.keep,
                    mission_source=args.missions_from) as srv:
        env = dict(os.environ, RL_MISSIONS=srv.dirs['Missions'], RL_ROOT=srv.root)
        for name, u in srv.urls.items():
            env['RL_' + name.upper()] = u
        print('servers: %s' % json.dumps(srv.urls), flush=True)
        code = subprocess.call(command, env=env)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
