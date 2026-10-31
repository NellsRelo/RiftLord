"""A stand-in terrain kit builder for the UI smoke suite's `setup` case and the offline tests:
the JSON command line of riftlord.terrainkit (run / list / build / delete) over three made-up
maps, with progress events, writing small libraries to --out. `run` (Setup's step) takes
RUN_SECONDS and fails when $RIFTLORD_FAKETERRAIN_FAIL is set.

    RIFTLORD_TERRAINKIT=tests/ui_smoke/faketerrain.py python3 -m riftlord ...

Its state (each command's arguments in args.jsonl) is kept in $RIFTLORD_FAKETERRAIN_STATE, else
$XDG_CACHE_HOME/faketerrain.
"""

import argparse
import json
import os
import shutil
import sys
import time

STAGES = ('scan', 'materials', 'bake', 'textures', 'meshes', 'pack', 'checks')
#: map -> (mission title, ground meshes)
MAPS = {'Scenario_09': ('Front Lawn', 1), 'ScenarioDLC_03': ('Canyon Keep', 4),
        'Scenario_18': ('Order Temple', 4)}
#: Pieces each build writes.
PIECES = 10
#: Seconds between events.
STEP = 0.15
#: Seconds a `run` takes.
RUN_SECONDS = 3.0
#: Builds that end with problems (exit 1).
PROBLEMS = {'Scenario_18': ['Grass_Ramp_4x4: collision hull not cooked']}
PREFIX = 'terrain:'


def state_dir():
    d = os.environ.get('RIFTLORD_FAKETERRAIN_STATE') or os.path.join(
        os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'faketerrain')
    os.makedirs(d, exist_ok=True)
    return d


def library(out):
    return out or os.environ.get('RIFTLORD_FAB_LIBRARY') or os.path.join(state_dir(), 'library')


def pack_id(level):
    return 'Terrain_' + level


def status(out, level):
    lib = os.path.join(library(out), pack_id(level))
    try:
        with open(os.path.join(lib, 'report.json')) as fh:
            rep = json.load(fh)
    except (OSError, ValueError):
        return {'status': 'none'}
    return {'status': 'failed' if rep.get('problems') else 'current', 'build': 'fake',
            'pieces': PIECES, 'problems': len(rep.get('problems') or [])}


def event(stage, done, total, msg='', fraction=None, eta=None):
    sys.stderr.write(json.dumps({'stage': stage, 'index': STAGES.index(stage), 'stages': len(STAGES),
                                 'done': done, 'total': total, 'msg': msg, 'fraction': fraction,
                                 'eta_s': eta}) + '\n')
    sys.stderr.flush()


def main(argv=None):
    ap = argparse.ArgumentParser()
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
    with open(os.path.join(state_dir(), 'args.jsonl'), 'a') as fh:
        fh.write(json.dumps(sys.argv[1:] if argv is None else argv) + '\n')
    if a.cmd == 'run':
        steps = 20
        for k in range(steps + 1):
            if a.events:
                sys.stderr.write(json.dumps({'fraction': k / steps, 'msg': 'fake'}) + '\n')
                sys.stderr.flush()
            time.sleep(RUN_SECONDS / steps)
        if os.environ.get('RIFTLORD_FAKETERRAIN_FAIL'):
            sys.stderr.write('fake terrain build failed\n')
            print(json.dumps({'ok': False, 'error': 'the stand-in was told to fail'}))
            return 3
        for m in MAPS:
            write_library(os.path.join(library(a.out), pack_id(m)), m, [])
        print(json.dumps({'ok': True, 'packs': [pack_id(m) for m in sorted(MAPS)],
                          'built': sorted(MAPS), 'problems': {}, 'library': library(a.out)}))
        return 0
    if a.cmd == 'list':
        names = sorted(MAPS)
        for k, level in enumerate(names):
            if a.events:
                sys.stderr.write(json.dumps({'stage': 'scan', 'done': k, 'total': len(names), 'msg': level}) + '\n')
                sys.stderr.flush()
            time.sleep(STEP)
        items = [{'item': PREFIX + m, 'map': m, 'title': 'Terrain: %s' % MAPS[m][0], 'pack_id': pack_id(m),
                  'sources': MAPS[m][1], 'library': status(a.out, m)} for m in names]
        print(json.dumps({'ok': True, 'library': library(a.out), 'items': items}))
        return 0
    level = a.map[len(PREFIX):] if a.map.startswith(PREFIX) else a.map
    if level not in MAPS:
        print(json.dumps({'ok': False, 'error': 'no such map: %s' % level}))
        return 2
    lib = os.path.join(library(a.out), pack_id(level))
    if a.cmd == 'delete':
        removed = [lib] if os.path.isdir(lib) else []
        shutil.rmtree(lib, ignore_errors=True)
        print(json.dumps({'ok': True, 'pack_id': pack_id(level), 'removed': removed}))
        return 0
    steps = [('scan', 2), ('meshes', PIECES), ('pack', 2), ('checks', 1)]
    total = sum(n for _s, n in steps)
    done = 0
    for stage, n in steps:
        for k in range(n + 1):
            if a.events:
                event(stage, k, n, '', round(min(1.0, (done + k) / total), 4), int((total - done - k) * STEP))
            time.sleep(STEP)
        done += n
    problems = PROBLEMS.get(level, [])
    write_library(lib, level, problems)
    print(json.dumps({'ok': not problems, 'pack_id': pack_id(level), 'library': lib, 'problems': problems,
                      'problem_count': len(problems)}))
    return 1 if problems else 0


def write_library(lib, level, problems):
    os.makedirs(lib, exist_ok=True)
    with open(os.path.join(lib, 'catalogue.json'), 'w') as fh:
        json.dump({'format': 'rift-lord-fab-library', 'format_version': 1, 'build': 'fake',
                   'pack': {'id': pack_id(level), 'title': 'Terrain: %s' % MAPS[level][0], 'kind': 'terrain'},
                   'pak': pack_id(level) + '.pak', 'meshes': []}, fh)
    open(os.path.join(lib, pack_id(level) + '.pak'), 'wb').close()
    with open(os.path.join(lib, 'report.json'), 'w') as fh:
        json.dump({'problems': problems}, fh)


if __name__ == '__main__':
    sys.exit(main())
