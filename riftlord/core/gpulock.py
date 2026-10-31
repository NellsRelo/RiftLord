"""One GPU user of Rift Lord's at a time on the machine: light bakes (CUDA/OptiX or
OpenGL) and the UI smoke suite's browsers (WebGL on the GPU).

    from riftlord.core import gpulock
    with gpulock.turn('light bake Scenario_09', log):
        ...

A flock on LOCK, taken blocking (no polling). Within one process the turn is shared:
threads of a holder (the release check's two UI suites) run inside it. A holder sets ENV
for its children, which then run inside it too. The lock file names its holder. Off Linux
and macOS (no flock) it does nothing. docs/HEAVY-JOBS.md.
"""

import os
import threading
import time

LOCK = os.path.join(os.path.expanduser('~'), '.cache', 'rift-lord', 'gpu.lock')
#: Set while a process holds the turn: its children run inside it.
ENV = 'RIFTLORD_GPU_HELD'

_STATE = {'count': 0, 'fh': None}
_GUARD = threading.Lock()
_TAKE = threading.Lock()


class turn:
    """Hold the GPU turn for the block (waiting for another holder to finish)."""

    def __init__(self, name, log=print):
        self.name, self.log, self.mine = name, log, False

    def __enter__(self):
        try:
            import fcntl
        except ImportError:
            return self
        if os.environ.get(ENV) and not _STATE['count']:
            return self                              # a parent process holds it
        with _TAKE:                                  # one thread takes the flock
            with _GUARD:
                if _STATE['count']:
                    _STATE['count'] += 1
                    self.mine = True
                    return self
            os.makedirs(os.path.dirname(LOCK), exist_ok=True)
            fh = open(LOCK, 'a+')
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fh.seek(0)
                self.log('%s: waiting for the GPU (%s)' % (self.name, fh.read().strip() or
                                                          'another job'))
                fcntl.flock(fh, fcntl.LOCK_EX)
            fh.seek(0)
            fh.truncate()
            fh.write('%s pid %d since %s\n' % (self.name, os.getpid(), time.strftime('%H:%M:%S')))
            fh.flush()
            with _GUARD:
                _STATE.update(count=1, fh=fh)
                os.environ[ENV] = self.name
            self.mine = True
        return self

    def __exit__(self, *exc):
        if not self.mine:
            return
        with _GUARD:
            _STATE['count'] -= 1
            if _STATE['count']:
                return
            fh, _STATE['fh'] = _STATE['fh'], None
            os.environ.pop(ENV, None)
        fh.seek(0)
        fh.truncate()
        fh.close()                                   # closing drops the lock
