"""Offline checks on floor a plan builds past the shipped play area (Repaired Mine,
2026-11-04): the regenerated walkable graph gathers geometry wherever the plan's ground
routes go (navregen.gather_region), an exclusion volume gives way to a route leg it would
cut (recastgen.ROUTES_CROSS_EXCLUSIONS), and a gap the route crosses names the moved
pieces with collision off standing in it (navregen.walkless_pieces,
navsurfextend._ghosts_in).

The layout is Repaired Mine's, on Scenario_Ice_02: an added upper floor (four
Floor_RB_4x3_C at z 1327, 3,800 uu past the NavMeshBoundsVolume) joined to an added lower
floor by an added StairGroup_1x3_RB_C in place of the level's own (removed), which climbs
through NavPowerExclusionVolume2 (every floor above z 900).

    python3 test_walkfloor.py [--no-build]      # --no-build: skip the regenerated graph
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests', 'fixtures'))

import numpy as np  # noqa: E402

from makemissions import matrix, point  # noqa: E402
from riftlord.build.navigation import navregen, navsurfextend  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


LEVEL = 'Scenario_Ice_02'
FLOOR = 'Floor_RB_4x3_C'
#: the record's floor heights (navregen.item_walk_top of the pieces it placed)
UPPER_Z, LOWER_Z = 1326.679, 746.0706
#: the level's own stair group: its four components, and the actor's transform (the
#: Stairs_1x1 part turns 90 degrees inside it)
STAIR_PIECES = [1125, 1126, 1127, 1128]
STAIRS = {'name': 'StairGroup_1x3_RB_C', 'kind': 'kit',
          'matrix': matrix((-5090.0, 9520.0, 750.0), yaw=90.0)}
#: its footprint (the shipped Stairs_3x_4_SB's drawn box)
STAIR_LO, STAIR_HI = (-5990.0, 9514.0), (-5090.0, 10726.0)
SPAWNER = (-4305.09, 11017.86, 1498.0)
UPPER, LOWER = (-5550.0, 10813.59, 1337.5), (-5591.0, 9361.0, 756.9)

plan = {
    'level': LEVEL, 'version': 2, 'removed': STAIR_PIECES,
    'added': [{'name': FLOOR, 'kind': 'kit', 'matrix': matrix((x, 10644.9542, UPPER_Z))}
              for x in (-7220.0, -6316.8234, -5416.8234, -4516.8234)]
    + [{'name': FLOOR, 'kind': 'kit', 'matrix': matrix((x, 8393.0, LOWER_Z))}
       for x in (-6316.8234, -5416.8234)] + [STAIRS],
    'spawners': [{'id': 1, 'at': list(SPAWNER), 'rotation': -177.0, 'kind': 'ground',
                  'index': 4, 'maxSpawnPerRow': 0, 'door': None, 'start': 4,
                  'flyerStart': None}],
    'points': [point(4, UPPER, [6]), point(6, LOWER, ['OMDPathPoint7_13'])],
}
spawner_floor = (SPAWNER[0], SPAWNER[1], SPAWNER[2] - navsurfextend.SPAWNER_ABOVE_FLOOR)

# 1. where geometry is gathered
shipped = navregen.region(LEVEL)
check('the upper floor lies past the shipped bounds and their margin',
      10644.9542 > shipped[1][1] + navregen.REGION_MARGIN, shipped[1])
lo, hi = navregen.gather_region(LEVEL, plan)
check('the gathered region holds the spawner, with the margin past it',
      hi[1] >= SPAWNER[1] + navregen.REGION_MARGIN - 1.0, hi)
check('and still holds the shipped bounds',
      np.all(lo <= shipped[0] - navregen.REGION_MARGIN + 1.0)
      and np.all(hi >= shipped[1] + navregen.REGION_MARGIN - 1.0), (lo, hi))
check('an added piece alone does not grow it',
      navregen.gather_region(LEVEL, {'level': LEVEL, 'added': [
          {'name': FLOOR, 'kind': 'kit', 'matrix': matrix((-5000.0, 30000.0, 0.0))}]})[1][1]
      < 30000.0)

# 2. the stairs climb through an exclusion volume
vols = navregen.nav_volumes(LEVEL, (navregen.EXCLUSION,))
mid = np.array([[(STAIR_LO[0] + STAIR_HI[0]) / 2, (STAIR_LO[1] + STAIR_HI[1]) / 2,
                 (UPPER_Z + LOWER_Z) / 2 + 100.0]])
check('the stairs\' upper half lies in an exclusion volume',
      any(navregen.inside_volume(v, mid)[0] for v in vols))

# 3. a gap's note names a moved piece with collision off (Repaired Mine's stair 2689)
moved = {'level': LEVEL, 'edits': [{'id': 2689, 'mesh': 'Stairs_2x_05_SB', 'matrix': [
    1.2, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.9, 0.0,
    -5902.8604, 8455.0761, 755.0, 1.0]}]}
ghosts = navregen.walkless_pieces(LEVEL, moved)
check('a moved level piece with collision off is listed',
      [g[0] for g in ghosts] == ['Stairs_2x_05_SB (level piece 2689)'], ghosts)
navsurfextend._GHOSTS[:] = ghosts
named = navsurfextend._ghosts_in([(-5800.0, 8246.0, 810.0), (-5300.0, 8466.0, 755.0)])
far = navsurfextend._ghosts_in([(-5800.0, 2246.0, 810.0), (-5300.0, 2466.0, 755.0)])
del navsurfextend._GHOSTS[:]
check('a gap over it names it', 'Stairs_2x_05_SB (level piece 2689) stands in it' in named,
      named)
check('a gap elsewhere names nothing', far == '', far)
check('an added piece with collision off is listed',
      [g[0] for g in navregen.walkless_pieces(LEVEL, {'level': LEVEL, 'added': [
          dict(STAIRS, collision=False)]})] == ['StairGroup_1x3_RB_C (added piece)'])

# 4. the regenerated graph walks the route from the upper floor down the stairs
if '--no-build' not in sys.argv:
    from riftlord.build.navigation import recastgen
    try:
        recastgen.binary('upstream' if navregen.GENERATOR == 'recast-upstream' else 'ue')
    except SystemExit as exc:
        print('SKIP  the regenerated graph: %s' % exc)
    else:
        #: the first agent's graph is enough; the geometry is gathered once
        navregen.AGENTS = navregen.AGENTS[:1]
        gathered = recastgen.geometry(LEVEL, plan)
        recastgen.geometry = lambda *_a: gathered
        got = {}
        for flag in (True, False):
            recastgen.ROUTES_CROSS_EXCLUSIONS = flag
            polys, ids = navregen.graphs_for(LEVEL, plan, log=lambda *_a: None)[0][:2]
            got[flag] = (polys, ids, navregen.walker(polys, ids))
        recastgen.ROUTES_CROSS_EXCLUSIONS = True
        polys, ids, walk = got[True]
        check('the spawner\'s floor is in the graph', walk.at(spawner_floor) is not None)
        check('the upper floor joins the spawner to the upper point',
              walk(spawner_floor, UPPER) is not None)
        check('the stairs join the upper point to the lower one', walk(UPPER, LOWER) is not None)
        path = walk.path(UPPER, LOWER) or []
        check('a walk\'s path runs from the polygon under its start to the one under its end',
              path and path[0] == walk.at(UPPER) and path[-1] == walk.at(LOWER), path)
        check('with exclusion volumes cutting routes, the stairs are gone',
              got[False][2](UPPER, LOWER) is None)
        #: every Recast polygon the route brings back lies on the stairs' footprint
        raw = recastgen.run(*gathered, navregen.AGENTS[0])
        legs, _exits = navregen.route_legs(LEVEL, plan)
        kept = recastgen.outside_exclusions(raw, LEVEL, legs, navregen.AGENTS[0][1])
        recastgen.ROUTES_CROSS_EXCLUSIONS = False
        cut = {id(p) for p in recastgen.outside_exclusions(raw, LEVEL, legs,
                                                           navregen.AGENTS[0][1])}
        recastgen.ROUTES_CROSS_EXCLUSIONS = True
        back = [np.mean(p, axis=0) for p in kept if id(p) not in cut]
        check('the polygons the route brings back lie on the stairs',
              back and all(STAIR_LO[0] - navregen.CELL <= c[0] <= STAIR_HI[0] + navregen.CELL
                           and STAIR_LO[1] - navregen.CELL <= c[1]
                           <= STAIR_HI[1] + navregen.CELL for c in back),
              [c.round().tolist() for c in back])

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d checks, %d failed' % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
