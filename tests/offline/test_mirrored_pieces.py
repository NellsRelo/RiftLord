"""Mirrored (negative-determinant) pieces: a mirrored Fab piece resolves and keeps its
mirror (riftlord.mapeditor.fabemit.resolve), its suggested trap grids come out in a
right-handed frame on the mirrored side (world_grids, grid_problems), the written
transform reproduces the plan's matrix (apply.transform_of), Recast sees a mirrored
floor's triangles facing up (recastgen.placed_winding), and every installed Fab library
cooks a mirrored copy of each convex element (the engine's collision for a mirrored
piece). Uses a fake library in a scratch folder (RIFTLORD_FAB_LIBRARY) for the plan
checks; no user record is read or written.

    python3 tests/offline/test_mirrored_pieces.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402

TMP = tempfile.mkdtemp(prefix='mirrored-test-')
INSTALLED = os.environ.get('RIFTLORD_FAB_LIBRARY')
os.environ['RIFTLORD_FAB_LIBRARY'] = TMP

from riftlord.build import apply as applier  # noqa: E402
from riftlord.build.navigation import recastgen  # noqa: E402
from riftlord.formats.omd3 import omd3level  # noqa: E402
from riftlord.mapeditor import fabemit, fablibrary  # noqa: E402

PACK = 'FakeKeep'
BUILD = 'Bfeed1234'
checks = []


def check(name, ok, detail=''):
    checks.append(ok)
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


#: A wall 600 long on x, its trap face on +y at y = 40, 300 tall from z 0.
WALL_GRID = {'id': 'G00', 'kind': 'wall', 'bits': 2, 'center': [0.0, 40.0, 150.0],
             'axes': {'x': [0.0, 1.0, 0.0], 'y': [-1.0, 0.0, 0.0], 'z': [0.0, 0.0, 1.0]},
             'extent': [10.0, 300.0, 150.0]}


def write_library():
    folder = os.path.join(TMP, PACK)
    os.makedirs(folder, exist_ok=True)
    package = '%s%s/%s/meshes/SM_wall' % (fablibrary.GAME_ROOT, PACK, BUILD)
    row = {'name': 'SM_wall', 'status': 'converted', 'source': 'Keep/SM_wall', 'role': 'wall',
           'package': package, 'object_path': package + '.SM_wall', 'grids': [WALL_GRID]}
    cat = {'format': fablibrary.FORMAT, 'format_version': fablibrary.FORMAT_VERSION,
           'build': BUILD, 'pack': {'title': 'Fake Keep'}, 'meshes': [row]}
    with open(os.path.join(folder, 'catalogue.json'), 'w') as fh:
        json.dump(cat, fh)
    return row


def piece(row, matrix):
    return {'kind': 'fab', 'pack': PACK, 'name': fablibrary.mesh_key(row['package']),
            'object': row['object_path'], 'source': row['source'], 'grids': ['G00'],
            'matrix': matrix}


def flat(m4):
    """A row-vector 4x4 (translation in the last row) as the plan's flat column-major list."""
    return [float(v) for v in np.asarray(m4, np.float64).reshape(-1)]


def fab_checks():
    row = write_library()
    turn = np.asarray(omd3level._matrix((1000.0, 2000.0, 100.0), (0.0, 30.0, 0.0),
                                        (1.0, 1.0, 1.0)))
    mirror_y = np.diag([1.0, -1.0, 1.0, 1.0])                 # mirrored across the wall's face
    mirror_x = np.diag([-1.0, 1.0, 1.0, 1.0])                 # mirrored along the wall
    plan = {'added': [piece(row, flat(turn.T)),
                      piece(row, flat((turn @ mirror_y).T)),
                      piece(row, flat((turn @ mirror_x).T))]}
    placed, problems = fabemit.resolve(plan)
    check('mirrored Fab pieces resolve (no refusal)', len(placed) == 3 and not problems, problems)
    check('the mirror is kept in the written matrix',
          [round(float(np.linalg.det(np.asarray(m).reshape(4, 4)[:3, :3]))) for _n, _e, _r, m in placed]
          == [1, -1, -1])
    notes = []
    grids = fabemit.world_grids(plan, placed, notes)
    check('every piece keeps its suggested grid', len(grids) == 3 and not notes, notes)
    bad = [(g['source'], fabemit.grid_problems(g)) for g in grids if fabemit.grid_problems(g)]
    check('every grid passes grid_problems (right-handed, square, unit)', not bad, bad)
    n0, n1, n2 = (np.asarray(g['axes'][0]) for g in grids)
    c0, c1, c2 = (np.asarray(g['center']) for g in grids)
    check('mirrored across its face: the grid sits on the other face (80 uu back), facing out',
          np.allclose(n1, -n0, atol=1e-6) and abs(float((c1 - c0) @ n0) + 80.0) < 1e-3,
          (n1.round(3).tolist(), (c1 - c0).round(3).tolist()))
    check('mirrored along its length: same face, same normal', np.allclose(n2, n0, atol=1e-6)
          and np.allclose(c2, c0, atol=1e-3), (n2.round(3).tolist(), (c2 - c0).round(3).tolist()))
    for k, (_n, _e, _r, m) in enumerate(placed):
        loc, rot, scale = applier.transform_of(m)
        back = np.asarray(omd3level._matrix(loc, rot, scale)).T.reshape(-1)
        check('piece %d: location, rotator and scale reproduce the plan matrix' % k,
              np.allclose(back, np.asarray(m, np.float64), atol=1e-4), scale)


def winding_checks():
    quad = np.array([[0.0, 0.0, 0.0], [100.0, 0.0, 0.0], [100.0, 100.0, 0.0], [0.0, 100.0, 0.0]])
    #: wound as UE render meshes are: clockwise seen from above (front face up)
    tri = np.array([[0, 2, 1], [0, 3, 2]])

    def up(M):
        w = (np.c_[quad, np.ones(4)] @ M)[:, :3]
        t = recastgen.placed_winding(tri, M)
        r = recastgen.to_recast(w)
        n = np.cross(r[t[:, 1]] - r[t[:, 0]], r[t[:, 2]] - r[t[:, 0]])
        return bool(np.all(n[:, 1] > 0))                     # Recast's up is +y
    plain = np.eye(4)
    mirrored = np.diag([-1.0, 1.0, 1.0, 1.0])
    check('Recast: a plain floor faces up', up(plain))
    check('Recast: a mirrored floor still faces up', up(mirrored))
    check('Recast: a mirrored floor turned and moved still faces up',
          up(mirrored @ np.asarray(omd3level._matrix((5.0, 6.0, 7.0), (0.0, 77.0, 0.0),
                                                     (2.0, 1.0, 1.0))).T))


def library_checks():
    """Every installed library's convex elements have mirrored cooks (PhysXPC header)."""
    from riftlord.formats.omd3 import omd3physx
    from riftlord.formats.omd3 import omd3smwrite
    root = INSTALLED or os.path.join(os.path.expanduser('~'), '.local', 'share', 'RiftLord',
                                     'FabLibrary')
    if not os.path.isdir(root):
        print('SKIP  installed Fab libraries: none at %s' % root)
        return
    os.environ['RIFTLORD_FAB_LIBRARY'] = root
    seen, bad = 0, []
    for pack in sorted(os.listdir(root)):
        try:
            cat = fablibrary.catalogue(pack)
        except SystemExit:
            continue
        for m in cat.get('meshes') or []:
            if m.get('status') != 'converted' or not (m.get('collision') or {}).get('convex'):
                continue
            blobs = fablibrary.read_package(pack, m['package'])
            t = omd3smwrite.Template(m['package'], {'.uasset': blobs['.uasset'],
                                                    '.uexp': blobs['.uexp']})
            info = omd3physx.parse_blob(t.body_setup()['blob'])
            seen += 1
            if info['mirrored'] != info['convex']:
                bad.append('%s/%s: %d convex, %d mirrored' % (pack, m['name'], info['convex'],
                                                              info['mirrored']))
            if seen >= 200:
                break
        if seen >= 200:
            break
    os.environ['RIFTLORD_FAB_LIBRARY'] = TMP
    check('installed Fab libraries cook a mirrored copy of every convex element (%d meshes)'
          % seen, not bad, bad[:3])


def main():
    fab_checks()
    winding_checks()
    library_checks()
    print('%d checks, %d failed' % (len(checks), checks.count(False)))
    return 0 if all(checks) else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
