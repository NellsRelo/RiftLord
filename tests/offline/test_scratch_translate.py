"""Offline checks that a scratch level's walkable graph does not depend on where the map
sits: a rift platform 84.5 uu over a kit floor (Proving Grounds' dais, 900_Arena_P) joins
the floor through its own stairs (scratch._walk_parts), and the whole plan moved by
offsets off the 150 uu lattice gives the same cover, moved (scratch._lattice, _phase,
_turned_cells), and the same route legs joined (navregen.disconnected).

    python3 test_scratch_translate.py
"""
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests', 'fixtures'))

from makemissions import matrix, point  # noqa: E402
from riftlord.build.navigation import navregen, navsurf, navsurfgen  # noqa: E402
from riftlord.mapeditor import scratch  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


LEVEL = 'Scenario_01'
FLOOR = 'Floor_RB_3x3_C'
PLATFORM = 'BP_OMDTrapGrid_RiftPlatform_4x4_RB_C'
#: 900_Arena_P: floor tile origins, and the platform 84.5 uu over them
X0, Y0, Z0 = -972.2, -4584.0, 2018.2
PLATFORM_AT = (427.3, -5175.0, 2102.7)
EXIT_AT = (447.05, -5581.8, 2301.55)
#: whole-plan moves: the 900_Arena_P lattice fits, XY only, Z only and two odd ones
OFFSETS = [(72.14, -65.98, -18.2), (-77.86, 84.02, -18.2), (72.14, -65.98, 0.0),
           (0.0, 0.0, -18.2), (13.37, 211.11, 7.7), (151.5, -37.25, 0.0)]


def plan_at():
    tiles = [{'name': FLOOR, 'kind': 'kit', 'matrix': matrix((X0 + 900.0 * i, Y0 - 900.0 * j, Z0))}
             for i in range(4) for j in range(4)]
    #: a floor turned 30 degrees, walked on the GRID_CELL cells it covers whole
    turned = {'name': FLOOR, 'kind': 'kit', 'matrix': matrix((X0 + 4200.0, Y0 - 1500.0, Z0), yaw=30.0)}
    platform = {'name': PLATFORM, 'kind': 'kit', 'matrix': matrix(PLATFORM_AT)}
    return {
        'level': LEVEL, 'version': 2,
        'scratch': {'version': 1, 'base': LEVEL, 'name': 'Translate'},
        'world': {'storyDialog': 'remove', 'intro': 'remove'},
        'spawners': [{'id': 1, 'at': [2500.0, -7000.0, Z0 + 99.0], 'rotation': 0.0,
                      'kind': 'ground', 'index': 1, 'maxSpawnPerRow': 0, 'door': None,
                      'start': 2, 'flyerStart': None}],
        'points': [point(2, (2200.0, -6900.0, Z0 + 10.0), [3]),
                   point(3, (1096.0, -6494.0, Z0 + 10.0), ['OMDPathPoint6'])],
        'wiring': [{'name': 'BP_OMDRift_2', 'kind': 'rift', 'gone': False,
                    'at': [EXIT_AT[0], EXIT_AT[1], EXIT_AT[2] + 371.8], 'rotation': 0.0},
                   {'name': 'OMDPathPoint6', 'kind': 'point', 'gone': False, 'at': list(EXIT_AT)}]
        + [{'name': n, 'kind': k, 'gone': True} for n, k in
           [('BP_OMDAISpawner_2', 'spawner'), ('Door01', 'door')]
           + [('OMDPathPoint%d' % i, 'point') for i in range(6)]],
        'added': tiles + [turned, platform],
    }


def moved(plan, d):
    out = copy.deepcopy(plan)
    for item in out['added']:
        for k in range(3):
            item['matrix'][12 + k] += d[k]
    for key in ('spawners', 'points', 'wiring'):
        for it in out.get(key) or []:
            if isinstance(it.get('at'), list):
                it['at'] = [it['at'][k] + d[k] for k in range(3)]
    return out


def quads(plan, d=(0.0, 0.0, 0.0)):
    return sorted(tuple(tuple(round(v[k] - d[k], 1) for k in range(3)) for v in q)
                  for q in scratch.cover(plan))


def cut(plan):
    buf, _note = scratch.surface_buffer(plan)
    return navregen.disconnected(buf, LEVEL, plan)


base = plan_at()

# 1. the platform is a flat top and four stair flights, its corner flights left to the floor
parts = scratch._walk_parts(base['added'][-1])
flat, ramps = parts or (None, [])
check('the platform has a flat top and four stair flights', flat and len(ramps) == 4, parts)
top = navregen.item_walk_top(base['added'][-1])
check('its stairs climb from within a step of the floor to its top',
      all(lo[2] - (Z0 - 0.8) <= navsurfgen.STEP_HEIGHT and abs(hi[2] - top) <= navregen.CELL_HEIGHT
          for lo, hi, _y, _f in ramps), (ramps, top))
inward = []
for lo, hi, yaw, falling in ramps if flat else ():
    axis = 0 if yaw == 90.0 else 1
    centre = (lo[axis] + hi[axis]) / 2.0
    middle = (flat[0][axis] + flat[1][axis]) / 2.0
    inward.append(falling == (centre > middle))
check('each flight rises toward the platform\'s top', inward and all(inward), ramps)
floors = scratch.floor_boxes(base)
check('the platform\'s floor box is its flat top only',
      flat and sum(1 for b in floors if not b[2] and abs(b[1][2] - top) < 0.01) == 1
      and all(b[0][0] >= flat[0][0] - 0.01 for b in floors if not b[2] and abs(b[1][2] - top) < 0.01),
      [b for b in floors if b[1][2] > Z0 + 40])

# 2. the cover joins the platform to the floor through its stairs, no corridor or bridge
got = cut(base)
check('every route leg of the platform plan is joined by the cover alone', not got, got)
buf, _note = scratch.surface_buffer(base)
polys = [list(s['verts']) for s in navsurf.sections(buf, navsurf.graphs(buf)[0])]
walk = navregen.walker(polys, [list(s['edge_ids']) for s in navsurf.sections(buf, navsurf.graphs(buf)[0])])
path = walk.path((1096.0, -6494.0, Z0), EXIT_AT)
check('the walk from the floor to the exit climbs a stair flight',
      path and any(max(v[2] for v in polys[i]) - min(v[2] for v in polys[i]) > 20.0 for i in path),
      path)
flat_only = copy.deepcopy(base)
flat_only['added'][-1] = {'name': 'Floor_RB_3x3_C', 'kind': 'kit',
                          'matrix': matrix((PLATFORM_AT[0] - 485.0, PLATFORM_AT[1] + 35.0,
                                            Z0 + 90.0))}
check('a raised floor with no stairs stays cut off from the floor', cut(flat_only), cut(flat_only))

# 3. the same cover and the same joins wherever the map sits
want = quads(base)
for d in OFFSETS:
    other = moved(base, d)
    got = quads(other, d)
    diff = sum(1 for a, b in zip(want, got)
               if max(abs(x - y) for va, vb in zip(a, b) for x, y in zip(va, vb)) > 0.15)
    check('moved by %s: the same %d cover quads, moved' % (d, len(want)),
          len(got) == len(want) and not diff, (len(got), diff))
    check('moved by %s: every route leg joined' % (d,), not cut(other), cut(other))
edges = [X0 + 900.0 * i for i in range(5)] + [123.4, 4000.0]
check('the lattice phase moves with the floors',
      abs(scratch._phase([e + 72.14 for e in edges]) - (scratch._phase(edges) + 72.14)) < 1e-6,
      (scratch._phase(edges),))

failed = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d checks, %d failed' % (len(checks), len(failed)))
sys.exit(1 if failed else 0)
