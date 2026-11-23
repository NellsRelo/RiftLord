"""Offline checks on exclusion volumes (NavPowerExclusionVolume): listed with the level's
volumes, never moved out of a route's way, and the walkable-graph rebuild leaving out the
plan's removed ones and taking its moved ones where the plan puts them. Repaired Mine
(Scenario_Ice_02) is the case: its NavPowerExclusionVolume2 takes every floor above z 900,
cutting off the stairs and rooms its plan opens upstairs, and NavPowerExclusionVolume10_2
takes the east part of the hall north of the rift.

    python3 test_exclusion_volumes.py
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.build.navigation import navregen  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402
from riftlord.mapeditor import volumes  # noqa: E402

ICE = 'Scenario_Ice_02'
#: every floor above z 900, over the whole level
HIGH = 'NavPowerExclusionVolume2'
#: the east part of the hall north of the rift (x -4335..-2985, y 1415..2965, z 225..875)
HALL = 'NavPowerExclusionVolume10_2'
#: a floor point in that part of the hall, and one on the room over the hall (z 1750)
HALL_FLOOR = (-3700.0, 2100.0, 460.0)
UPSTAIRS = (-5500.0, 2500.0, 1760.0)
#: the one in the Castle sublevel
CASTLE = 'Castle|396'

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def inside(vols, p):
    return any(navregen.inside_volume(v, np.array([p]))[0] for v in vols)


listed = [v for v in volumes.level_volumes(ICE) if v['cls'] == navregen.EXCLUSION]
names = {v['name'] for v in listed}
check('Repaired Mine lists its 10 exclusion volumes, one in the Castle sublevel',
      len(listed) == 10 and {HIGH, HALL, CASTLE} <= names, sorted(names))
check('labelled as NavPower names them, keeping enemies off their floor',
      all(v['label'] == 'Exclusion volume' and v['stops'] == 'exclusion' for v in listed), '')
check('/api/level carries them', sum(v['cls'] == navregen.EXCLUSION
                                     for v in leveldata.level(ICE)['volumes']) == 10, '')
check('never in a route\'s way', 'exclusion' not in volumes.IN_THE_WAY, '')

shipped = navregen.exclusion_volumes(ICE)
check('as shipped: the hall floor and the room upstairs are inside one',
      len(shipped) == 10 and inside(shipped, HALL_FLOOR) and inside(shipped, UPSTAIRS), '')

plan = {'volumes': [{'name': HIGH, 'gone': True}, {'name': HALL, 'at': [0.0, 0.0, -9000.0]},
                    {'name': CASTLE, 'gone': True}]}
edited = navregen.exclusion_volumes(ICE, plan)
check('a removed one is left out, a sublevel one too', len(edited) == 8, len(edited))
check('the room upstairs is outside every one once the plan removes it',
      not inside(edited, UPSTAIRS), '')
check('a moved one takes its floor with it',
      not inside(edited, HALL_FLOOR) and inside(edited, (0.0, 0.0, -9000.0)), '')
turned = volumes.package_volumes(ICE)
hall = next(v for v in turned if v['name'] == HALL)
m = np.array(hall['matrix'], dtype=np.float64).reshape(4, 4).T
m[2, 3] -= 5000.0
moved = navregen.exclusion_volumes(
    ICE, {'volumes': [{'name': HALL, 'matrix': m.T.ravel().tolist()}]})
check('a matrix edit (moved, turned or resized) places it',
      not inside(moved, HALL_FLOOR)
      and inside(moved, (HALL_FLOOR[0], HALL_FLOOR[1], HALL_FLOOR[2] - 5000.0)), '')
check('the plan keeps the edits', volumes.clean(plan['volumes']) == plan['volumes'], '')

failed = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, ('  (%s)' % (detail,)) if not ok and
                       detail != '' else ''))
print('%d of %d checks passed' % (len(checks) - len(failed), len(checks)))
sys.exit(1 if failed else 0)
