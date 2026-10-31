"""What a Python process of a gate step reads, for the gate's selection (tools/gateselect.py).

Loaded as `sitecustomize` when tools/gateinputs is on PYTHONPATH. With RIFTLORD_GATE_INPUTS
set to a folder, each event is appended to `<folder>/<pid>-<ns>.log` as it happens (so a
killed process leaves what it read), one tab-separated line per path:

    r PATH    a file opened for reading (data, native libraries, a script run by path)
    i PATH    a module's source read by the import system
    w PATH    a file opened for writing
    l PATH    a folder listed (os.listdir, os.scandir, so glob and os.walk too)
    L PATH    a folder listed by the import system, to find modules
    n NAME    a module the import system looked for and did not find
    e PATH    QUALNAME: code that ran (each function, method, class body and module body
              once per process; sys.monitoring, MONITOR_ID)
    x JSON    a program started: [executable, args, cwd]
    m PATH    at exit: the file of each loaded module
    M 1       first line when `i`, `L`, `n` and `e` are logged; without it every module
              counts as read whole and every listing as a data listing

Paths are absolute, as the process named them. Forked children write to the same log.
Without the variable it does nothing.
"""

import os
import sys

#: The sys.monitoring tool id (3 and 4 are free; 0-2 and 5 are named by Python).
MONITOR_ID = 3
#: The import system's frames: a module read is get_data called by get_code; a listing for
#: modules is _fill_cache.
_BOOTSTRAP = '<frozen importlib._bootstrap_external>'


def _path(value):
    """An absolute path from an audit argument, or None (a descriptor, no path)."""
    if isinstance(value, int) or value is None:
        return None
    try:
        value = os.fsdecode(os.fspath(value))
    except TypeError:
        return None
    return os.path.abspath(value)


def _by_import(names):
    """True when the audited call came from the import system: `names` are the function
    names of the import system's frames that must sit directly above it, innermost first."""
    try:
        f = sys._getframe(2)
    except ValueError:
        return False
    for name in names:
        if f is None or f.f_code.co_filename != _BOOTSTRAP or f.f_code.co_name != name:
            return False
        f = f.f_back
    return True


def _start():
    folder = os.environ.get('RIFTLORD_GATE_INPUTS')
    if not folder:
        return
    import time
    try:
        os.makedirs(folder, exist_ok=True)
        fd = os.open(os.path.join(folder, '%d-%d.log' % (os.getpid(), time.time_ns())),
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    except OSError:
        return
    seen = set()
    writes = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

    def put(kind, text):
        if (kind, text) in seen:
            return
        seen.add((kind, text))
        try:
            os.write(fd, ('%s\t%s\n' % (kind, text.replace('\n', ' '))).encode(
                'utf-8', 'surrogateescape'))
        except OSError:
            pass

    def hook(event, args):
        try:
            if event == 'open':
                path = _path(args[0])
                if path:
                    mode, flags = (list(args) + [None, None])[1:3]
                    wrote = (isinstance(flags, int) and flags & writes) or \
                        (isinstance(mode, str) and any(c in mode for c in 'wax+'))
                    put('w' if wrote else 'i' if path.endswith('.py') and monitored and
                        _by_import(('get_data', 'get_code')) else 'r', path)
            elif event in ('os.listdir', 'os.scandir'):
                path = _path(args[0] if args and args[0] is not None else '.')
                if path:
                    put('L' if monitored and _by_import(('_fill_cache',)) else 'l', path)
            elif event == 'ctypes.dlopen':
                if args and isinstance(args[0], str) and os.path.isabs(args[0]):
                    put('r', args[0])
            elif event == 'subprocess.Popen':
                import json
                exe, argv, cwd = args[0], args[1], args[2]
                argv = [os.fsdecode(a) if isinstance(a, (bytes, str, os.PathLike)) else str(a)
                        for a in (argv if isinstance(argv, (list, tuple)) else [argv])]
                put('x', json.dumps([os.fsdecode(exe) if exe else None, argv,
                                     _path(cwd) if cwd else os.getcwd()]))
        except Exception:                             # noqa: BLE001 - never break the process
            pass

    # the code that runs: each code object reports once, then its event is switched off
    skip = tuple({sys.prefix, sys.base_prefix, sys.exec_prefix})
    monitored = False
    try:
        mon = sys.monitoring

        def ran(code, _offset):
            f = code.co_filename
            if not f.startswith(('<',) + skip) and '-packages/' not in f:
                put('e', '%s\t%s' % (os.path.abspath(f), code.co_qualname))
            return mon.DISABLE
        mon.use_tool_id(MONITOR_ID, 'riftlord gate inputs')
        mon.register_callback(MONITOR_ID, mon.events.PY_START, ran)
        mon.set_events(MONITOR_ID, mon.events.PY_START)
        monitored = True
    except (AttributeError, ValueError):              # before 3.12, or the id is taken
        pass

    class Missing:
        """Last on sys.meta_path: logs each module no other finder found."""

        @staticmethod
        def find_spec(name, path=None, target=None):
            put('n', name)
            return None

    if monitored:
        put('M', '1')
        sys.meta_path.append(Missing)

    def modules():
        for mod in list(sys.modules.values()):
            f = getattr(mod, '__file__', None)
            if isinstance(f, str):
                put('m', os.path.abspath(f))
    import atexit
    atexit.register(modules)
    sys.addaudithook(hook)
    if sys.argv and sys.argv[0] not in ('', '-c', '-m'):
        put('r', os.path.abspath(sys.argv[0]))


_start()
