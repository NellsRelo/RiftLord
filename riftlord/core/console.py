"""Rift Lord's console: what the editors' scripts say, kept for the app's Console panel.

Every entry has a source (which tool said it) and a level, so the panel can filter on
both. Call sites write with `write(text, source, level)` or the level helpers; text
the converter modules print lands here too, under the source a `working_as` block
names (else SERVER) at DEBUG. Bounded: the oldest lines go once there are MAX_LINES.

    import console
    console.info('saved North Wings', console.MAP_EDITOR)
    with console.working_as(console.MAP_COOKER):
        emit.write(...)              # its prints arrive as Map Cooker DEBUG lines
"""

import collections
import contextlib
import sys
import threading
import time

#: Lines kept. A Map Editor build says a few hundred; this is many builds' worth.
MAX_LINES = 5000

#: Sources, as the panel names them.
MAP_EDITOR = 'Map Editor'
ONSLAUGHT_EDITOR = 'Onslaught Editor'
MAP_COOKER = 'Map Cooker'
PACKAGING = 'Packaging'
MISSIONS = 'Missions'
FAB_KITS = 'Fab Kits'
SERVER = 'Server'
SOURCES = (MAP_EDITOR, ONSLAUGHT_EDITOR, MAP_COOKER, PACKAGING, MISSIONS, FAB_KITS, SERVER)

#: Levels, least to most severe.
LEVELS = ('debug', 'info', 'warn', 'error')

_LINES = collections.deque(maxlen=MAX_LINES)
_LOCK = threading.Lock()
_NEXT = 1
_LOCAL = threading.local()


def write(text, source='', level='info'):
    """Add text, one entry per line. `level` is one of LEVELS ('err' and 'warning'
    are accepted too)."""
    global _NEXT
    level = {'err': 'error', 'warning': 'warn'}.get(level, level)
    if level not in LEVELS:
        level = 'info'
    with _LOCK:
        for line in str(text).splitlines() or ['']:
            _LINES.append({'n': _NEXT, 't': time.time(), 'source': source or SERVER,
                           'level': level, 'text': line})
            _NEXT += 1


def debug(text, source=''):
    write(text, source, 'debug')


def info(text, source=''):
    write(text, source, 'info')


def warn(text, source=''):
    write(text, source, 'warn')


def error(text, source=''):
    write(text, source, 'error')


def notes(source, level='debug'):
    """A one-argument writer for a module's `note=` callback."""
    return lambda text: write(text, source, level)


def thread_source(source):
    """Credit what the current thread prints from now on to `source`; for a worker
    thread that does one job."""
    _LOCAL.source = source


@contextlib.contextmanager
def working_as(source):
    """Credit what this thread prints inside the block to `source`."""
    before = getattr(_LOCAL, 'source', None)
    _LOCAL.source = source
    try:
        yield
    finally:
        _LOCAL.source = before


def since(n=0):
    """{'lines': entries numbered above `n`, 'next': the number to ask from next time,
    'sources': the known sources, 'levels': the levels}."""
    with _LOCK:
        return {'lines': [e for e in _LINES if e['n'] > n], 'next': _NEXT - 1,
                'sources': list(SOURCES), 'levels': list(LEVELS)}


def clear():
    with _LOCK:
        _LINES.clear()


class _Tee:
    """A stream that still writes where it did, and copies whole lines to the console."""

    def __init__(self, stream, level):
        self.stream, self.level, self.pending = stream, level, ''

    def write(self, text):
        if self.stream is not None:
            try:
                self.stream.write(text)
            except (OSError, ValueError):
                pass
        self.pending += text
        while '\n' in self.pending:
            line, self.pending = self.pending.split('\n', 1)
            if line.strip():
                write(line, getattr(_LOCAL, 'source', None) or SERVER, self.level)
        return len(text)

    def flush(self):
        if self.stream is not None:
            try:
                self.stream.flush()
            except (OSError, ValueError):
                pass

    def __getattr__(self, name):
        return getattr(self.stream, name)


def capture():
    """Copy everything printed from now on into the console: stdout at DEBUG, stderr
    at WARN."""
    if not isinstance(sys.stdout, _Tee):
        sys.stdout = _Tee(sys.stdout, 'debug')
    if not isinstance(sys.stderr, _Tee):
        sys.stderr = _Tee(sys.stderr, 'warn')
