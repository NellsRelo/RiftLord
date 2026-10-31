"""Markers a worker sets around a multi-file edit of the tree, so a gate does not snapshot it
half done (tools/gate.py waits for them, up to a cap). docs/HEAVY-JOBS.md.

    python3 -m riftlord.core.editing hold NAME [--minutes N]   # until release, or N minutes (30)
    python3 -m riftlord.core.editing release NAME
    python3 -m riftlord.core.editing list

    from riftlord.core import editing
    with editing.held('crystal'):          # held while this process lives and the block runs
        ...

A marker is FOLDER/<name> (JSON): a context manager's names its process and lasts while it
lives; a command line's lasts until `release` or its minutes run out (the command itself
ends at once).
"""

import argparse
import contextlib
import json
import os
import re
import sys
import time

FOLDER = os.path.join(os.path.expanduser('~'), '.cache', 'rift-lord', 'editing')
#: Minutes a command-line hold lasts when not released.
MINUTES = 30


def _path(name):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', name or ''):
        raise SystemExit('a marker name is letters, digits, _ . - (got %r)' % name)
    return os.path.join(FOLDER, name)


def hold(name, minutes=MINUTES, pid=None):
    """Set the marker: for this `pid`'s life, or (no pid) for `minutes`."""
    os.makedirs(FOLDER, exist_ok=True)
    rec = {'name': name, 'since': time.strftime('%H:%M:%S')}
    if pid:
        rec['pid'] = pid
    else:
        rec['until'] = time.time() + minutes * 60
    part = _path(name) + '.%d.part' % os.getpid()
    with open(part, 'w') as fh:
        json.dump(rec, fh)
    os.replace(part, _path(name))


def release(name):
    try:
        os.remove(_path(name))
    except OSError:
        pass


@contextlib.contextmanager
def held(name):
    """The marker for the block, tied to this process (a crash leaves it dead, not held)."""
    hold(name, pid=os.getpid())
    try:
        yield
    finally:
        release(name)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def live():
    """[{name, since, ...}] of the markers in force; dead or expired ones are removed."""
    out = []
    for name in sorted(os.listdir(FOLDER)) if os.path.isdir(FOLDER) else []:
        if '.part' in name:
            continue
        path = os.path.join(FOLDER, name)
        try:
            with open(path) as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        on = _alive(rec['pid']) if rec.get('pid') else time.time() < rec.get('until', 0)
        if on:
            out.append(rec)
        else:
            try:
                os.remove(path)
            except OSError:
                pass
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    h = sub.add_parser('hold')
    h.add_argument('name')
    h.add_argument('--minutes', type=float, default=MINUTES)
    r = sub.add_parser('release')
    r.add_argument('name')
    sub.add_parser('list')
    args = ap.parse_args(argv)
    if args.cmd == 'hold':
        hold(args.name, args.minutes)
    elif args.cmd == 'release':
        release(args.name)
    else:
        for rec in live():
            print('%s since %s%s' % (rec['name'], rec['since'], ' (pid %d)' % rec['pid']
                                     if rec.get('pid') else ' (until %s)' % time.strftime(
                                         '%H:%M', time.localtime(rec['until']))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
