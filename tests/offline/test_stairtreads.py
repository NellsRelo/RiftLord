"""Offline checks on stair floor in the regenerated walkable graph (recastgen.cpp
filterLedges): a stair tread one Recast cell deep sits between two risers that together
rise more than the agent's step, and Recast's steep-slope test nulled it, cutting the
flight off (Repaired Mine, 2026-11-05: its lower added Stairs_2x_05_SB, whose top tread
the upper flight overlaps by 34 uu, had no floor). Stair (ramp) floor skips that test;
plain floor keeps it, and a real ledge still stops both.

    python3 test_stairtreads.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from riftlord.build.navigation import navregen, navsurfgen, recastgen  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


#: (y0, y1, top z) of each solid block along the walk, x -300..300, bottoms at z -10: a
#: floor, four treads (risers 25, 25, 31, 25; the third tread one 20 uu cell deep), a floor
STEPS = [(-400.0, 0.0, 0.0), (0.0, 60.0, 25.0), (60.0, 100.0, 50.0), (100.0, 120.0, 81.0),
         (120.0, 180.0, 106.0), (180.0, 600.0, 131.0)]
LOW, HIGH = (0.0, -200.0, 0.0), (0.0, 400.0, 131.0)
#: the deep tread's neighbours differ by 56 uu, past the 48 uu step
assert STEPS[4][2] - STEPS[2][2] > navregen.AGENTS[0][1]


def blocks(steps, ramp):
    """(verts, tris, walk) of closed boxes, wound outward as Recast reads them; walk 2 on
    the treads with `ramp`, else 1."""
    verts, tris, walk = [], [], []
    for k, (y0, y1, top) in enumerate(steps):
        lo, hi = np.array([-300.0, y0, -10.0]), np.array([300.0, y1, top])
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                            for z in (lo[2], hi[2])])
        quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4),
                 (1, 5, 7, 3)]
        base = len(verts) * 8
        centre = corners.mean(axis=0)
        for a, b, c, d in quads:
            for t in ((a, b, c), (a, c, d)):
                p = recastgen.to_recast(corners[list(t)])
                n = np.cross(p[1] - p[0], p[2] - p[0])
                out = p.mean(axis=0) - recastgen.to_recast(centre[None])[0]
                tris.append([base + i for i in (t if n @ out > 0 else t[::-1])])
                walk.append(2 if ramp and 0 < k < len(steps) - 1 else 1)
        verts.append(corners)
    return np.vstack(verts), np.array(tris, np.int32), np.array(walk, np.uint8)


def walks(steps, ramp, agent):
    """True when the agent's graph joins LOW to HIGH."""
    polys = recastgen.run(*blocks(steps, ramp), agent)
    ids = navsurfgen.allocate_edge_ids(polys, tol=recastgen.CELL * 0.25, step=agent[1])
    return navregen.walker(polys, ids)(LOW, HIGH) is not None


try:
    recastgen.binary('upstream' if navregen.GENERATOR == 'recast-upstream' else 'ue')
except SystemExit as exc:
    print('SKIP  %s' % exc)
    sys.exit(0)
recastgen.SOURCE = 'upstream' if navregen.GENERATOR == 'recast-upstream' else 'ue'

for agent in navregen.AGENTS:
    r = int(agent[0])
    check('r%d: stairs with a one-cell tread join the floors' % r, walks(STEPS, True, agent))
    check('r%d: the same blocks as plain floor do not (Recast\'s steep-slope test)' % r,
          not walks(STEPS, False, agent))
    #: the third tread raised to a 57 uu riser: a ledge no agent climbs
    ledge = STEPS[:3] + [(100.0, 120.0, 107.0), (120.0, 180.0, 132.0), (180.0, 600.0, 157.0)]
    check('r%d: a riser past the step still cuts the stairs' % r,
          not walks(ledge, True, agent))

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d checks, %d failed' % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
