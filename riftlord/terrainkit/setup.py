"""Terrain kits as a Setup step: every shipped map's ground built into the terrain kits'
folder (riftlord.mapeditor.fablibrary.terrain_root) on first run, and again when the game's
paks, the terrain kit code or the Fab writer change (key()).

The build runs `python3 -m riftlord.terrainkit run` (the frozen server: `--terrain run`; the
script RIFTLORD_TERRAINKIT names instead, for tests) in its own low-priority process, which
holds a heavy-job slot; its JSON-lines events give the step's progress. riftlord.types.setup
shows the step in the Setup pane and starts it with the type data.

    step()      {'key': 'terrain', 'label', 'state', 'progress', 'seconds'} for the Setup pane
    needed()    True when the kits are missing or out of date
    start()     start a build when needed and none runs
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time

#: The step's key and label in the Setup pane.
KEY, LABEL = 'terrain', 'Terrain'
#: A script to run instead of riftlord.terrainkit (`python3 -I SCRIPT ARGS`).
ENV = 'RIFTLORD_TERRAINKIT'
#: The frozen server's switch (riftlord/__main__.py).
FROZEN_SWITCH = '--terrain'
#: Where the step keeps its state (in the terrain kits' folder).
STATE = '.setup.json'
#: Seconds a needed() answer is reused for (the Setup pane asks often).
NEEDED_FRESH = 5.0
#: Log lines of a failed build copied to the console.
CONSOLE_TAIL = 20

HERE = os.path.dirname(os.path.abspath(__file__))
_LOCK = threading.Lock()
_RUN = {'proc': None, 'progress': 0.0, 'failed': None, 'started': None}
_NEEDED = {'at': 0.0, 'value': None, 'failed': None}


def folder():
    from riftlord.mapeditor import fablibrary
    return fablibrary.terrain_root()


def _state():
    try:
        with open(os.path.join(folder(), STATE)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_state(data):
    path = os.path.join(folder(), STATE)
    with open(path + '.part', 'w') as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(path + '.part', path)


def key():
    """A hash of what the kits depend on: the game's paks, the terrain kit code, the Fab
    writer's piece code and the builder (the stand-in's path in tests)."""
    from riftlord.fab import library
    from riftlord.terrainkit import build, sources
    h = hashlib.sha256()
    h.update(sources.paks_stamp().encode())
    h.update(build.code_hash().encode())
    h.update(library.logic_hash(*library.PIECE_CODE).encode())
    h.update(str(os.environ.get(ENV) or '').encode())
    return h.hexdigest()


def available():
    """True when a build can run here: a builder, the game and collision cooking."""
    if command() is None:
        return False
    if os.environ.get(ENV):
        return True
    try:
        from riftlord.core import gamepath
        gamepath.paks()
        from riftlord.formats.omd3 import omd3physx
        return omd3physx.tool_status()['pxcook'] != 'missing'
    except (Exception, SystemExit):                  # noqa: BLE001
        return False


def needed(fresh=False):
    """True when the kits are missing or out of date; False, with key_failed() set, when that
    cannot be told."""
    if not fresh and time.time() - _NEEDED['at'] < NEEDED_FRESH:
        return _NEEDED['value']
    reason = None
    try:
        s = _state()
        value = (s.get('key') != key() or any(
            not os.path.isfile(os.path.join(folder(), pack, 'report.json'))
            for pack in s.get('packs') or []))
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        value = False
        reason = '%s: cannot tell whether the kits are up to date (%s: %s)' % (LABEL, type(exc).__name__, exc)
    _NEEDED.update(at=time.time(), value=value, failed=reason)
    return value


def key_failed():
    """Why the last needed() could not compute key(), or None."""
    return _NEEDED['failed']


def running():
    proc = _RUN['proc']
    return proc is not None and proc.poll() is None


def step():
    """The Setup pane's row for the terrain kits."""
    s = _state()
    can = available()
    want = can and not running() and needed()
    if running():
        state, progress = 'running', _RUN['progress']
    elif _RUN['failed'] or (can and key_failed()):
        state, progress = 'failed', 0.0
    elif want:
        state, progress = 'waiting', 0.0
    elif s.get('key'):
        state, progress = 'done', 1.0
    else:
        state, progress = 'waiting', 0.0
    return {'key': KEY, 'label': LABEL, 'state': state, 'progress': round(progress, 3),
            'seconds': s.get('seconds'), 'available': can}


def failed():
    """The failed build's line, or why key() could not be computed, or None."""
    return _RUN['failed'] or (key_failed() if available() else None)


def command():
    """The command line that runs the builder, or None."""
    from riftlord.core import gamepath
    script = os.environ.get(ENV)
    if script:
        python = shutil.which('python3') if gamepath.frozen() else sys.executable
        return [python, '-I', script] if python and os.path.isfile(script) else None
    if gamepath.frozen():
        return [sys.executable, FROZEN_SWITCH]
    root = os.path.dirname(os.path.dirname(HERE))
    return [sys.executable, '-I', '-c',
            'import runpy, sys; sys.path.insert(0, %r); '
            'runpy.run_module("riftlord.terrainkit", run_name="__main__", alter_sys=True)' % root]


def start(force=False):
    """Start a build in a low-priority process when one is needed (or `force`), the builder can
    run here and none runs. Returns step()."""
    with _LOCK:
        if running() or not available() or not (force or needed(fresh=True)):
            return step()
        argv = command() + ['run', '--out', folder(), '--json', '--events']
        kw = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE}
        if os.name == 'nt':
            kw['creationflags'] = subprocess.BELOW_NORMAL_PRIORITY_CLASS
        else:
            argv = ['nice', '-n', '10'] + (['ionice', '-c', '3'] if shutil.which('ionice')
                                           else []) + argv
            from riftlord.types import setup as types_setup
            kw['preexec_fn'] = types_setup._limits
        started = threading.Event()
        _RUN.update(progress=0.0, failed=None, started=time.time())
        want = key()

        def own():
            try:
                proc = subprocess.Popen(argv, **kw)
            except OSError as exc:
                _RUN['failed'] = '%s: %s' % (LABEL, exc)
                started.set()
                return
            _RUN['proc'] = proc
            started.set()
            out = []
            reader = threading.Thread(target=lambda: out.append(proc.stdout.read()), daemon=True)
            reader.start()
            tail = []
            for raw in proc.stderr:
                line = raw.decode('utf-8', 'replace').rstrip('\n')
                if line.startswith('{'):
                    try:
                        ev = json.loads(line)
                        if isinstance(ev.get('fraction'), (int, float)):
                            _RUN['progress'] = max(_RUN['progress'], min(1.0, float(ev['fraction'])))
                        continue
                    except ValueError:
                        pass
                if line.strip():
                    tail = (tail + [line])[-CONSOLE_TAIL:]
            proc.wait()
            reader.join()
            summary = None
            for line in reversed(b''.join(out).decode('utf-8', 'replace').strip().splitlines()):
                if line.startswith('{'):
                    try:
                        summary = json.loads(line)
                        break
                    except ValueError:
                        continue
            _NEEDED['at'] = 0.0
            if proc.returncode == 0 and summary and summary.get('ok'):
                _write_state({'key': want, 'packs': summary.get('packs') or [],
                              'seconds': round(time.time() - _RUN['started'], 1)})
                _RUN['progress'] = 1.0
                return
            _RUN['failed'] = '%s: %s' % (LABEL, (summary or {}).get('error') or
                                         (tail[-1] if tail else 'exit %s' % proc.returncode))
            from riftlord.core import console
            console.error('Setup failed: %s' % _RUN['failed'], console.SERVER)
            if tail:
                console.write('\n'.join(tail), console.SERVER, 'debug')
        threading.Thread(target=own, daemon=True).start()
        started.wait(10)
    return step()


def stop():
    proc = _RUN['proc']
    if proc is not None and proc.poll() is None:
        proc.kill()
