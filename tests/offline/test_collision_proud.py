"""Fab collision proud of the drawing (riftlord/mapeditor/fabcollision.py): the raster of a
known shape, and on the installed libraries (when present) the Medieval Village curved double
stairs flagged (its one stair-ramp hull fills the open space between the flights: the Arena
record's invisible block, 2026-10-28), a straight stair and a wall that collides as drawn not
flagged, and the gate naming the flagged piece of a plan.

    python3 tests/offline/test_collision_proud.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402

from riftlord.mapeditor import fabcollision, fablibrary  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append(ok)
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def raster_checks():
    #: a 200 x 100 rectangle at z 50, as two triangles
    quad = np.array([[[0, 0, 50], [200, 0, 50], [200, 100, 50]],
                     [[0, 0, 50], [200, 100, 50], [0, 100, 50]]], np.float64)
    top = fabcollision._top(quad, np.array([0.0, 0.0, 0.0]), 25.0, (10, 6))
    #: cells 0..7 x 0..3 lie inside; cell 8 / 4 hold the far edge; further cells nothing
    check('a flat quad rasterises to its height over every cell it covers, and nowhere else',
          np.all(top[:8, :4] == 50.0) and np.all(~np.isfinite(top[9:, :]))
          and np.all(~np.isfinite(top[:, 5:])), top.tolist())
    box = fabcollision._rotator(0.0, 90.0, 0.0)
    check('a yaw of 90 turns x onto y', np.allclose(box[:, 0], [0, 1, 0], atol=1e-9))


def library_checks():
    root = fablibrary.root()
    if not os.path.isdir(os.path.join(root, 'MedievalVillage')):
        print('SKIP  installed libraries: no MedievalVillage at %s' % root)
        return
    cat = fablibrary.catalogue('MedievalVillage')
    by = cat['_by_source']
    stairs = by['Medieval_Village/meshes/building/SM_central_stairs']
    wall = by['Medieval_Village/meshes/building/SM_wall_11']
    got = fabcollision.measure('MedievalVillage', stairs)
    #: a library converted before the stair split carries one hull over both flights; a
    #: library converted since carries one hull per run of treads
    stale = got['proudArea'] > 500e4
    if stale:
        check('Medieval Village SM_central_stairs is flagged (one hull over two curved flights)',
              fabcollision.flagged(got, stairs), fabcollision.describe(stairs, got))
    else:
        check('Medieval Village SM_central_stairs, converted with split stair hulls, is not flagged',
              not fabcollision.flagged(got, stairs), fabcollision.describe(stairs, got))
    got = fabcollision.measure('MedievalVillage', wall)
    check('a wall that collides as drawn is never proud', not fabcollision.proud(got), got['trace'])
    if os.path.isdir(os.path.join(root, 'WinterCity')):
        one = fablibrary.catalogue('WinterCity')['_by_source'][
            'StylizedWinterCity/StaticMeshes/Modular/Steps/SM_Steps_1']
        got = fabcollision.measure('WinterCity', one)
        check('a straight stair (WinterCity SM_Steps_1) is not flagged', not fabcollision.flagged(got, one),
              fabcollision.describe(one, got))
    plan = {'added': [{'kind': 'fab', 'pack': 'MedievalVillage', 'object': stairs['object_path'],
                       'source': stairs['source'], 'matrix': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0,
                                                              0, 0, 0, 1]}]}
    name, ok, detail = fabcollision.gate(plan)
    if stale:
        check('the gate fails a plan placing it, naming the piece',
              not ok and 'SM_central_stairs' in detail, detail)
    else:
        check('the gate passes a plan placing it', ok, detail)


def main():
    raster_checks()
    library_checks()
    print('%d checks, %d failed' % (len(checks), checks.count(False)))
    return 0 if all(checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
