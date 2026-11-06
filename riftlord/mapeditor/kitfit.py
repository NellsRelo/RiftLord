"""How a kit class's meshes sit against each of its wall trap grids: the numbers the Map
Editor's snapping decides a wall's rules by (web/mapeditor/src/kitplace.js gridPlace).

    python3 -m riftlord.mapeditor.kitfit Wall_RB_WDMTL_v1_1x2_C
    python3 -m riftlord.mapeditor.kitfit --check /mnt/2tb_pro_a/tmp/kitcensus/out/classsum.json

Per wall grid, measured on the real vertices of the class's drawn parts
(leveldata.kit_parts) in the grid's own frame, as the kit placement census measures them
(docs/research/kit-placement-census.md section 1; its classdata.py `grid_measures`):

- `below`: how far the lowest vertex lies below the grid's bottom (uu; 0 when none does).
- `reach`: the furthest a vertex stands in front of the grid's plane (along its trap
  normal) within FOOT_BAND of the grid's bottom and FOOT_SIDE of its ends: the skirting's
  reach at the floor surface. None when no vertex lies there.
- `top`: the highest vertex over the grid's bottom (uu).

A floor or ceiling grid has no fit (None). /api/kit serves these as each grid's `fit`
(server.py); nothing a build reads uses them.
"""
import argparse
import functools
import json
import sys

from riftlord.formats.omd3 import meshcat, omd3level, omd3mesh

#: The height band over a wall grid's bottom the skirting reach is measured in, in uu
#: (census classdata.py `foot_reach`).
FOOT_BAND = (-25.0, 30.0)
#: How far past a wall grid's ends along it a vertex still counts toward the reach, in uu
#: (census classdata.py `foot_reach`).
FOOT_SIDE = 5.0
#: An in-plane axis counts as horizontal when its z is below this (census classdata.py
#: grid_frame).
HORIZONTAL = 0.5
#: Values are rounded to this many decimals.
DECIMALS = 2
#: check(): a census row is a grid's when their actor origins in front of the plane agree
#: within this many uu.
MATCH_ORIGIN = 1.0


@functools.lru_cache(maxsize=None)
def _verts(mesh):
    """A mesh's vertices in its own space, as a tuple of (x, y, z); () when unreadable."""
    try:
        got = omd3mesh.read(mesh, meshcat.load())
    except Exception:                                # noqa: BLE001
        return ()
    return tuple(tuple(v) for v in (got or {}).get('verts') or ())


def _part_points(part):
    """The vertices of one drawn part in actor space."""
    m = omd3level._matrix(part.get('loc') or (0, 0, 0), part.get('rot') or (0, 0, 0),
                          part.get('scale') or (1, 1, 1))
    out = []
    for x, y, z in _verts(part.get('mesh')):
        out.append(tuple(m[r][0] * x + m[r][1] * y + m[r][2] * z + m[r][3] for r in range(3)))
    return out


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def wall_fit(grid, points):
    """{below, reach, top} of a wall grid (leveldata.kit_grids) against `points` (actor
    space), or None when the grid is not a wall or there are no points."""
    if grid.get('surface') != 'Wall' or not points or not grid.get('axes') or not grid.get('extent'):
        return None
    normal, u, v = grid['axes']
    ext = grid['extent']
    along = u if abs(u[2]) < HORIZONTAL else v
    up = (0.0, 0.0, 1.0)
    half_along = max(abs(ext[1] * _dot(u, along)), abs(ext[2] * _dot(v, along)))
    half_up = max(abs(ext[1] * _dot(u, up)), abs(ext[2] * _dot(v, up)))
    centre = grid['origin']
    lowest = None
    highest = None
    reach = None
    for p in points:
        d = (p[0] - centre[0], p[1] - centre[1], p[2] - centre[2])
        over = _dot(d, up) + half_up
        lowest = over if lowest is None else min(lowest, over)
        highest = over if highest is None else max(highest, over)
        if FOOT_BAND[0] <= over <= FOOT_BAND[1] and abs(_dot(d, along)) <= half_along + FOOT_SIDE:
            t = _dot(d, normal)
            reach = t if reach is None else max(reach, t)
    return {'below': round(max(0.0, -lowest), DECIMALS),
            'reach': None if reach is None else round(reach, DECIMALS),
            'top': round(highest, DECIMALS)}


@functools.lru_cache(maxsize=1024)
def _fits(class_name):
    from riftlord.mapeditor import leveldata
    got = leveldata.kit_parts(class_name)
    points = []
    for part in got.get('parts') or []:
        if part.get('skeletal') or part.get('hidden'):
            continue
        points.extend(_part_points(part))
    return tuple(json.dumps(wall_fit(g, points)) for g in got.get('grids') or [])


def fits(class_name):
    """[fit or None] for each of a kit class's grids, in leveldata.kit_grids order."""
    return [json.loads(f) for f in _fits(class_name)]


def with_fit(class_name, grids):
    """`grids` (leveldata.kit_grids of `class_name`) each with its `fit`."""
    got = fits(class_name)
    return [dict(g, fit=got[i] if i < len(got) else None) for i, g in enumerate(grids)]


def check(census):
    """Each wall grid of every kit class against the census's per-class modes
    (classsum.json): the classes whose `below` or `reach` differ by more than 0.5 uu."""
    from riftlord.mapeditor import leveldata
    rows = json.load(open(census))
    by = {}
    for r in rows.values():
        if r.get('surface') == 'Wall':
            by.setdefault(r['cls'], []).append(r)
    seen = 0
    bad = []
    for cls, want in sorted(by.items()):
        grids = leveldata.kit_grids(cls)
        if not grids:
            continue
        got = [(g, f) for g, f in zip(grids, fits(cls)) if f]
        for w in want:
            #: the census row's grid is the one of the class with its size and its
            #: actor origin as far in front of its plane
            match = [f for g, f in got if sorted(round(2 * e) for e in g['extent'][1:]) ==
                     sorted(round(s) for s in w['size']) and
                     abs(-_dot(g['origin'], g['axes'][0]) - w['origin_t']) <= MATCH_ORIGIN]
            if not match:
                continue
            seen += 1
            f = match[0]
            diffs = []
            for key, census_key in (('below', 'below'), ('reach', 'foot_reach')):
                a, b = f.get(key), w.get(census_key)
                if a is None or b is None:
                    if (a is None) != (b is None):
                        diffs.append('%s %s vs census %s' % (key, a, b))
                elif abs(a - b) > 0.5:
                    diffs.append('%s %.2f vs census %.2f' % (key, a, b))
            if diffs:
                bad.append('%s %s: %s' % (cls, w['comp'], '; '.join(diffs)))
    return seen, bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('classes', nargs='*')
    ap.add_argument('--check', metavar='CLASSSUM_JSON')
    args = ap.parse_args(argv)
    if args.check:
        seen, bad = check(args.check)
        print('%d wall grids compared, %d differ' % (seen, len(bad)))
        for line in bad:
            print('  ' + line)
        return 0
    for cls in args.classes:
        print(cls, json.dumps(fits(cls)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
