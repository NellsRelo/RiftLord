"""Offline check: a Collides switch alone (riftlord/mapeditor/piececollision.py) leaves
the piece where it stands on the regenerated minimap, so its floor is neither taken out
nor walled over; a moved piece still stands on the floor it is moved onto. Repaired Mine's case: switching
Collides on for the stone trim at the top of the north-west stairs (Scenario_Ice_02
piece 1448) drew a line across the stairs.

    python3 test_minimap_collides.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.build.minimap import minimapredraw  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402

ICE = 'Scenario_Ice_02'
TRIM = 1448
checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


level = leveldata.level(ICE)
piece = next(p for p in level['pieces'] if p['id'] == TRIM)
lo, hi = [-9841.7, -2842.5, 585.9], [-8641.6, -2686.7, 766.3]


def over_trim(boxes):
    return [b for b in boxes if b[0][0] < hi[0] and b[1][0] > lo[0]
            and b[0][1] < hi[1] and b[1][1] > lo[1]]


def floor_with(matrix):
    edit = {'id': TRIM, 'mesh': piece['mesh'], 'matrix': [round(v, 4) for v in matrix],
            'collision': True}
    return minimapredraw.floor(ICE, {'level': ICE, 'edits': [edit]}, level=level)


still = floor_with(piece['matrix'])
check('a Collides switch alone walls nothing over the trim', not over_trim(still['block']),
      over_trim(still['block']))
check('a Collides switch alone takes out no floor over the trim',
      not over_trim([e[:2] for e in still['erase']]))
moved = list(piece['matrix'])
moved[13] -= 300.0
onto = floor_with(moved)
check('the trim moved 300 uu south onto the stairs stands on them as a wall',
      len(onto['block']) == 1, onto['block'])

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d/%d' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
