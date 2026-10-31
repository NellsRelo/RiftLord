"""Offline checks on added spawners' facing (riftlord/mapeditor/spawnerfacing.py): the rule
on placement, a route change, a moved first point and a first point within reach; a
spawner turned by hand keeping its yaw and the save keeping the flag; the Map Editor's rule
(web/mapeditor/src/actors.js spawnerFacing, run with node) agreeing with the build's; and a
copy of Scenario_02 built with added spawners and read back: yaws written, the level's own
spawners unchanged, the warning naming a spawner turned away from its route.

    python3 test_spawnerfacing.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from riftlord.mapeditor import planactors  # noqa: E402
from riftlord.mapeditor import spawnerfacing as sf  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def near(a, b, tol=0.06):
    return a is not None and b is not None and abs(sf.off(a, b)) <= tol


# 1. the rule on a level of three points: L0 -> L1 -> L2 (the exit)
LEVEL = {'exit': 'L2', 'points': {
    'L0': {'index': 0, 'location': (0.0, 0.0, 0.0), 'next': [{'to': 'L1', 'weight': 1.0}],
           'exit': False, 'start': True},
    'L1': {'index': 1, 'location': (2000.0, 0.0, 0.0), 'next': [{'to': 'L2', 'weight': 1.0}],
           'exit': False, 'start': False, 'reachRadius': 500.0},
    'L2': {'index': 2, 'location': (2000.0, 2000.0, 0.0), 'next': [], 'exit': True,
           'start': False}}, 'spawners': {}, 'doors': {}, 'starts': []}


def plan_with(spawners, points=(), wiring=()):
    return {'version': 2, 'points': [planactors.clean_point(p, p['id']) for p in points],
            'spawners': spawners, 'playerStarts': [], 'wiring': list(wiring)}


def facing_of(plan, i=0):
    nodes, _removed, _p = planactors.graph(plan, LEVEL)
    return sf.facing(plan['spawners'][i], nodes, LEVEL)


def spawner(**kw):
    out = {'id': 1, 'at': [-1000.0, -1000.0, 99.0], 'rotation': 0.0, 'kind': 'ground',
           'index': 0, 'start': None, 'flyerStart': None}
    out.update(kw)
    return out


POINT = {'id': 9, 'at': [-1000.0, 1000.0, 0.0], 'next': [{'to': 'L1', 'weight': 1.0}]}
CASES = {
    'placed, no route yet: keeps its yaw': plan_with([spawner(rotation=30.0)]),
    'placed with its start: faces it': plan_with([spawner(start=9)], [POINT]),
    'route changed to a level point': plan_with([spawner(start='L1')], [POINT]),
    'first point moved': plan_with([spawner(start='L1')], [POINT],
                                   [{'name': 'L1', 'kind': 'point', 'at': [-3000.0, 3000.0, 0.0]}]),
    'first point within reach: the point after it': plan_with(
        [spawner(at=[1700.0, 100.0, 99.0], start='L1')], [POINT]),
    'a flyer spawner: its flyer route first': plan_with(
        [spawner(kind='flyer', start=9, flyerStart='L0')], [POINT]),
    'a ground spawner: its ground route first': plan_with(
        [spawner(start=9, flyerStart='L0')], [POINT]),
    'turned by hand: keeps its yaw': plan_with([spawner(start=9, rotation=-120.0, turned=True)],
                                               [POINT]),
    'an added point with no branch: past it to the exit': plan_with(
        [spawner(at=[-1000.0, 900.0, 99.0], start=10)],
        [{'id': 10, 'at': [-1000.0, 1000.0, 0.0], 'next': []}]),
}
WANT = {'placed, no route yet: keeps its yaw': 30.0,
        'placed with its start: faces it': 90.0,
        'route changed to a level point': 18.4,
        'first point moved': 116.6,
        'first point within reach: the point after it': 81.0,
        'a flyer spawner: its flyer route first': 45.0,
        'a ground spawner: its ground route first': 90.0,
        'turned by hand: keeps its yaw': -120.0,
        'an added point with no branch: past it to the exit': 20.1}
python_yaws = {}
for name, plan in CASES.items():
    got = facing_of(plan)
    python_yaws[name] = got
    check('rule: %s (%s)' % (name, WANT[name]), near(got, WANT[name], 0.06), got)

# 2. the save keeps the flag, and only when set
clean = planactors.clean({'spawners': [spawner(turned=True), spawner(id=2), spawner(id=3, turned=False)]})
check('the save keeps turned on a spawner turned by hand, and writes it on no other',
      [s.get('turned') for s in clean['spawners']] == [True, None, None],
      [s.get('turned') for s in clean['spawners']])

# 3. the Map Editor's rule agrees (actors.js spawnerFacing under node)
node = shutil.which('node')
if not node:
    print('SKIP  node missing: the Map Editor rule is not compared')
else:
    js_level = {'points': {n: {'export': n, 'index': p['index'], 'location': list(p['location']),
                               'exit': p['exit'],
                               'next': [{'PointIndex': LEVEL['points'][b['to']]['index'],
                                         'BranchWeight': b['weight']} for b in p['next']],
                               **({'reachRadius': p['reachRadius']} if 'reachRadius' in p else {})}
                           for n, p in LEVEL['points'].items()}}
    script = '''
import { spawnerFacing, wiringOf } from './actors.mjs'
const { level, cases } = JSON.parse(process.argv[2])
const out = {}
for (const [name, plan] of Object.entries(cases)) {
  const locate = (ref) => {
    if (ref === 'rift') ref = Object.values(level.points).find(p => p.exit).export
    if (typeof ref === 'number') { const p = plan.points.find(o => o.id === ref); return p ? p.at : null }
    const w = wiringOf(plan, ref)
    if (w && w.at) return w.at
    return (level.points[ref] || {}).location || null
  }
  const sp = plan.spawners[0]
  out[name] = sp.turned ? sp.rotation : (spawnerFacing(plan, level, sp, locate) ?? sp.rotation)
}
console.log(JSON.stringify(out))
'''
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy2(os.path.join(ROOT, 'web', 'mapeditor', 'src', 'actors.js'),
                     os.path.join(tmp, 'actors.mjs'))
        with open(os.path.join(tmp, 'check.mjs'), 'w') as fh:
            fh.write(script)
        got = subprocess.run([node, 'check.mjs', json.dumps({'level': js_level, 'cases': CASES})],
                             cwd=tmp, capture_output=True, text=True, timeout=120)
    try:
        js = json.loads(got.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        js = {}
        check('actors.js runs under node', False, got.stderr[-400:])
    for name in CASES:
        check('Map Editor agrees: %s' % name, near(js.get(name), python_yaws[name], 0.06),
              (js.get(name), python_yaws[name]))

# 4. a copy of Scenario_02 built with three added spawners, read back
from riftlord.build import apply  # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.core import gamefiles  # noqa: E402
from riftlord.formats.ue.uheader import parse as parse_header  # noqa: E402
from riftlord.onslaught import onslaught  # noqa: E402

BUILD_LEVEL = 'Scenario_02'


def own_rotations(ua, ux):
    """{export: rotation} of a level's spawners that Rift Lord did not add."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'l.umap')
        with open(path, 'wb') as fh:
            fh.write(ua)
        info = parse_header(path)
    exports = {e['name']: e for e in info['exports']}
    rep = onslaught.map_report('x', loaded=(info, ux, len(ua)))
    return {s['export']: [round(v, 3) for v in onslaught.actor_transform(
        info, ux, len(ua), exports[s['export']], {})['rotation']]
        for s in rep['spawners'] if not s['export'].startswith(sf.ADDED_PREFIX)}


ua, ux = gamefiles.package(navpatch.map_path(BUILD_LEVEL))
plan = {'level': BUILD_LEVEL, 'version': 2, 'rifts': [], 'doors': [], 'waves': [], 'edits': [],
        'added': [], 'removed': [], 'wiring': [], 'playerStarts': [],
        'points': [{'id': 9, 'at': [3000.0, 6800.0, 465.0], 'next': []},
                   {'id': 10, 'at': [2000.0, 6000.0, 465.0], 'next': [{'to': 9, 'weight': 1}]}],
        'spawners': [
            {'id': 1, 'at': [2000.0, 6800.0, 560.0], 'rotation': 0.0, 'kind': 'ground',
             'index': 0, 'start': 9},
            {'id': 2, 'at': [2100.0, 6050.0, 560.0], 'rotation': 0.0, 'kind': 'ground',
             'index': 0, 'start': 10, 'unstable': True},
            {'id': 3, 'at': [2000.0, 7800.0, 560.0], 'rotation': 135.0, 'kind': 'ground',
             'index': 0, 'start': 9, 'turned': True}]}
plan.update(planactors.clean(plan))
ua2, ux2, report = apply.apply_plan(ua, ux, BUILD_LEVEL, '/Game/Mods/Try/Maps/TryMap',
                                    'TryMap', plan=plan)
back = {r['name']: r for r in sf.read_level(ua2, ux2)}
for name, sp, want in (('RiftLordSpawner0', plan['spawners'][0], 0.0),
                       ('RiftLordSpawner1', plan['spawners'][1], 39.8),
                       ('RiftLordSpawner2', plan['spawners'][2], 135.0)):
    r = back.get(name) or {}
    check('built %s (%s) reads back at yaw %s' % (
        name, 'turned by hand' if sp.get('turned') else 'unstable rift' if sp.get('unstable')
        else 'ground', want), near(r.get('yaw'), want, 0.06), r)
    check('built %s has no pitch or roll' % name, r and r['pitch'] == 0 and r['roll'] == 0, r)
check('the build counts the spawners it faced', report.get('spawners_faced') == 1,
      report.get('spawners_faced'))
before, after = own_rotations(ua, ux), own_rotations(ua2, ux2)
check('the level\'s own spawners keep their rotation (%d)' % len(before),
      before and before == after, (before, after))
warned = sf.level_warnings(ua2, ux2)
want_off = round(sf.off(135.0, back.get('RiftLordSpawner2', {}).get('want') or 0.0))
check('one warning, naming the spawner turned away from its route and its angle (%d)'
      % want_off, len(warned) == 1 and 'RiftLordSpawner2' in warned[0]
      and ('%d degrees' % want_off) in warned[0], warned)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d of %d checks passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
