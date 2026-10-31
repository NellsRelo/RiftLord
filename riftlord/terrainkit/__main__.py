"""Terrain kits from the shipped maps' ground, as libraries the Map Editor lists.

    python3 -m riftlord.terrainkit run [--out DIR] [--jobs N] [--json] [--events]
    python3 -m riftlord.terrainkit list [--out DIR] [--json] [--events] [--refresh]
    python3 -m riftlord.terrainkit build MAP [--out DIR] [--jobs N] [--json] [--events]
    python3 -m riftlord.terrainkit delete MAP [--out DIR] [--json]

`run`: Setup's step (riftlord.terrainkit.setup): every map with ground built where its library
is not current, libraries of maps without ground removed, in a heavy-job slot; --events writes
{fraction, msg} lines; --json prints {ok, packs, built, problems}.
`list --json`: {ok, library, items: [{item ('terrain:<map>'), map, title, pack_id, sources,
library: {status none|current|stale|failed, build, pieces, problems}}]}; --events writes one
progress line per map while the maps are first read. `build` writes the map's library
(--events: the converter's JSON-lines stages). Exit codes as the Fab converter: 0 clean,
1 written with problems, 2 bad arguments or no such map, 3 crash, 4 locked. The libraries
belong to the user and are never bundled.
"""

import argparse
import json
import os
import shutil
import sys
import traceback

for _var in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ.setdefault(_var, '1')

#: Item prefix the Fab Kits tab uses for terrain rows.
ITEM_PREFIX = 'terrain:'
EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, EXIT_CRASH, EXIT_LOCKED = 0, 1, 2, 3, 4


def _events(done, total, msg):
    sys.stderr.write(json.dumps({'stage': 'scan', 'index': 0, 'stages': 1, 'done': done,
                                 'total': total, 'msg': msg}) + '\n')
    sys.stderr.flush()


def _map(arg):
    return arg[len(ITEM_PREFIX):] if arg.startswith(ITEM_PREFIX) else arg


def _lock(level):
    """An exclusive per-map lock file held for the build, or None when another holds it."""
    from riftlord.terrainkit import sources
    folder = os.path.join(sources.cache_dir(), 'locks')
    os.makedirs(folder, exist_ok=True)
    fh = open(os.path.join(folder, level + '.lock'), 'a+')
    try:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        pass
    except OSError:
        fh.close()
        return None
    return fh


def cmd_list(a):
    from riftlord.fab import library
    from riftlord.terrainkit import build, sources
    found = sources.discover(events=_events if a.events else None, refresh=a.refresh)
    names = sources.titles()
    out = _out(a)
    items = []
    for level in sorted(found):
        src = found[level]
        if not src:
            continue
        items.append({'item': ITEM_PREFIX + level, 'map': level, 'title': build.title(level, names),
                      'pack_id': build.pack_id(level), 'sources': len(src),
                      'library': build.status(level, src, out)})
    return {'ok': True, 'library': out, 'items': items}


def cmd_build(a):
    from riftlord.fab import library
    from riftlord.terrainkit import build, sources
    level = _map(a.map)
    found = sources.discover()
    if level not in found:
        return {'ok': False, 'error': 'no such map: %s' % level, 'exit': EXIT_USAGE}
    if not found[level]:
        return {'ok': False, 'error': '%s has no painted ground meshes' % level, 'exit': EXIT_USAGE}
    held = _lock(level)
    if held is None:
        return {'ok': False, 'error': '%s is being built by another process' % level, 'exit': EXIT_LOCKED}
    try:
        rep = build.build(level, a.out, jobs=a.jobs, events=library.json_events if a.events else None,
                          found=found)
    finally:
        held.close()
    problems = rep.get('problems') or []
    return {'ok': not problems, 'pack_id': rep.get('pack_id'), 'library': rep.get('library'),
            'problems': problems[:50], 'problem_count': len(problems), 'counts': rep.get('counts'),
            'exit': EXIT_OK if not problems else EXIT_PROBLEMS}


def _out(a):
    from riftlord.mapeditor import fablibrary
    return a.out or fablibrary.terrain_root()


def _fraction(fraction, msg):
    sys.stderr.write(json.dumps({'fraction': round(fraction, 4), 'msg': msg}) + '\n')
    sys.stderr.flush()


#: Share of a run's progress the map scan takes.
SCAN_SHARE = 0.2


def cmd_run(a):
    from riftlord.core import heavyslot
    from riftlord.terrainkit import build, sources
    out = _out(a)
    tell = _fraction if a.events else (lambda f, m: None)
    with heavyslot.slot('terrain kits'):
        found = sources.discover(events=lambda d, t, m: tell(SCAN_SHARE * d / max(t, 1), m))
        maps = sorted(m for m, src in found.items() if src)
        built, problems = [], {}
        for k, level in enumerate(maps):
            share = (1.0 - SCAN_SHARE) / len(maps)
            base = SCAN_SHARE + share * k
            if build.status(level, found[level], out).get('status') == 'current':
                tell(base + share, level)
                continue
            ev = (lambda stage, done, total, msg='', fraction=None, eta_s=None, b=base, s=share, l=level:
                  tell(b + s * (fraction or 0.0), l))
            rep = build.build(level, out, jobs=a.jobs, events=ev, found=found)
            built.append(level)
            if rep.get('problems') or rep.get('error'):
                problems[level] = (rep.get('problems') or [rep.get('error')])[:10]
        packs = [build.pack_id(m) for m in maps]
        for name in sorted(os.listdir(out)) if os.path.isdir(out) else []:
            if name.startswith(build.PACK_PREFIX) and name not in packs:
                shutil.rmtree(os.path.join(out, name), ignore_errors=True)
        tell(1.0, '')
    return {'ok': not problems, 'packs': packs, 'built': built, 'problems': problems,
            'library': out, 'exit': EXIT_OK if not problems else EXIT_PROBLEMS}


def cmd_delete(a):
    from riftlord.fab import library
    from riftlord.terrainkit import build
    level = _map(a.map)
    out = _out(a)
    pid = build.pack_id(level)
    removed = []
    for path in (os.path.join(out, pid), os.path.join(library.cache_root(), pid)):
        if os.path.isdir(path):
            shutil.rmtree(path)
            removed.append(path)
    return {'ok': True, 'pack_id': pid, 'removed': removed}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd')
    ru = sub.add_parser('run')
    ru.add_argument('--out'); ru.add_argument('--jobs', '--workers', dest='jobs', type=int)
    ru.add_argument('--json', action='store_true'); ru.add_argument('--events', action='store_true')
    li = sub.add_parser('list')
    li.add_argument('--out'); li.add_argument('--json', action='store_true')
    li.add_argument('--events', action='store_true'); li.add_argument('--refresh', action='store_true')
    b = sub.add_parser('build')
    b.add_argument('map'); b.add_argument('--out'); b.add_argument('--jobs', '--workers', dest='jobs', type=int)
    b.add_argument('--memory-mb', type=int); b.add_argument('--json', action='store_true')
    b.add_argument('--events', action='store_true')
    d = sub.add_parser('delete')
    d.add_argument('map'); d.add_argument('--out'); d.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    if a.cmd is None:
        ap.print_help()
        return EXIT_USAGE
    try:
        got = {'run': cmd_run, 'list': cmd_list, 'build': cmd_build, 'delete': cmd_delete}[a.cmd](a)
    except SystemExit as exc:
        got = {'ok': False, 'error': str(exc), 'exit': EXIT_USAGE}
    except Exception as exc:                         # noqa: BLE001
        traceback.print_exc()
        got = {'ok': False, 'error': '%s: %s' % (type(exc).__name__, exc), 'exit': EXIT_CRASH}
    code = got.pop('exit', EXIT_OK if got.get('ok') else EXIT_CRASH)
    if a.json:
        print(json.dumps(got, sort_keys=True))
    else:
        print(json.dumps(got, indent=1, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
