"""Build a walkable cover from a CONVERTED level's own floor pieces.

    python3 navsurffloors.py --scene TheCrunch
    python3 navsurffloors.py --selftest

`navsurfgen` writes a surface navgraph and `regen` proved in game that the writer is
right. What a converted map still needs is the **cover**: which polygons to put in it.

**Approximating an existing level was always a proxy, and its failures were artefacts of
the approximation rather than of the format.** Flattening each grid cell to one height
cannot represent a ramp, so Scenario_01's spawner ledge -- 230uu above its route floor
-- could not be linked without a step tolerance so generous that it also linked the
bridge to the lava pit 680uu below. Measured in game: orcs walked the wrong way and fell
in.

A converted level has no such problem, because its floors are pieces **we placed**. Their
heights, extents and slopes are known exactly, so the cover can be built from them
instead of inferred from a finished graph.

## What counts as floor

A placement whose map entry has the `floor` role, plus the stair and ramp roles, since
those are the surfaces that join heights. The piece's own world box gives the top face;
`emitgeo` already computes that box for every placement and `navsurf` validates the
result the same way it validates a shipped graph.

## Why rasterise instead of using the pieces directly

OMD3 floors overlap -- vanilla levels do it constantly and the conversion emits overlaps
deliberately. Overlapping coplanar polygons make point location ambiguous: two records
claim the same spot and a mover can be told it is in either. So the pieces' top faces
are rasterised onto a grid and one quad is emitted per cell per height band, which
yields a cover with no overlaps and no gaps where floors abut.
"""

import argparse
import bisect
import collections
import os
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navsurf
from riftlord.build.navigation import navsurfgen

#: Roles whose pieces are walkable surface. Stairs and ramps are included because they
#: are what joins two heights -- the thing a flat-cell approximation could not express.
FLOOR_ROLES = frozenset(('floor', 'stair', 'ramp'))

#: Grid pitch for the cover, in uu. 300 is OMD3's trap cell, so a cover on this pitch
#: lines up with the grid the level's own geometry is placed against.
CELL = 300.0

#: Two heights within this are the same walkable surface; OMD3's AgentMaxStepHeight.
STEP = 48.0

#: A piece counts as walkable only if it is actually HORIZONTAL: its vertical extent must
#: be no more than this fraction of its smaller horizontal one.
#:
#: The role comes from the MESH, and OMD2 reuses floor meshes as walls and ceilings by
#: rotating them. Trusting the role alone put `floormain1` pieces at every height from
#: z -1000 to +4500 on converted TheCrunch -- more of them at -800 than at the room's
#: real floor height of 100 -- which is walkable surface hanging in mid-air. The piece's
#: own box says which way it ended up facing, and that is what decides.
MAX_THICKNESS_RATIO = 0.5


#: Roles whose pieces RISE across their own footprint, so a single flat quad at the top
#: of the box is wrong: a staircase emitted that way leaves its bottom unreachable and
#: the cover falls into one component per landing. Measured on converted TheCrunch: 43
#: components with 36% of the area in the largest.
SLOPED_ROLES = frozenset(('stair', 'ramp'))


def floor_boxes(result, mesh_map=None):
    """[(lo, hi, sloped, yaw)] world boxes of every walkable piece."""
    import json
    if mesh_map is None:
        with open(gamepath.data('mesh-map.json')) as fh:
            mesh_map = {k.replace('\\', '/').lower(): v
                        for k, v in json.load(fh)['map'].items()}
    out = []
    for p in result['placements']:
        src = str(p.get('from') or '').replace('\\', '/').lower()
        entry = next((v for k, v in mesh_map.items() if src.endswith(k)), None)
        role = (entry or {}).get('role')
        if role not in FLOOR_ROLES:
            continue
        lo, hi = p['box']
        dx, dy, dz = (hi[k] - lo[k] for k in (0, 1, 2))
        flat = min(dx, dy) > 1.0 and dz <= MAX_THICKNESS_RATIO * min(dx, dy)
        if not (flat or role in SLOPED_ROLES):
            continue                   #: a rotated floor mesh is a wall, not a floor
        out.append((list(lo), list(hi), role in SLOPED_ROLES,
                    (p.get('rotation') or [0.0, 0.0, 0.0])[1]))
    return out


def cover(boxes, cell=CELL, step=STEP, lines=None):
    """Quads tiling the walkable surface, grouped into SURFACES first.

    `lines` = (xs, ys), ascending, replaces the uniform `cell` lattice with a rectilinear
    one: cell (i, j) spans xs[i]..xs[i+1] by ys[j]..ys[j+1].

    Rasterised rather than taken piece by piece, because OMD3 floors overlap constantly
    and two coplanar records claiming one spot make point location ambiguous.

    **A corner's height has to be resolved per SURFACE, and three cheaper schemes all
    fail.** The problem is that one cell can sit under several floors and a slope changes
    height faster than an agent can step:

    | scheme | converted TheCrunch, largest component by area |
    |---|---|
    | flat quad at the cell's own height | 36% -- a staircase's cells differ by 150uu, three times the 48uu step, so none of them link |
    | corner heights, each cell picking the nearest band | 45% -- two neighbours pick DIFFERENT bands for a shared corner, so 2,900 edges differ by 70-300uu and are correctly refused |
    | corner heights by band INDEX | 23% -- corners have different band counts, so the indices misalign and the quads are dropped |
    | **surfaces first, then corners per surface** | see below |

    So: every piece's cells become a patch at that piece's own height; patches merge when
    they share a cell at a similar height or sit adjacent within a step; and each
    resulting surface resolves its own corner heights. Two cells of one surface then
    share a corner height **exactly**, so their edge matches in full 3D and a slope is
    continuous by construction.
    """
    if not boxes:
        return []
    #: A sloped piece's RISE DIRECTION is not recorded anywhere, so it is inferred from
    #: the floors the piece actually meets: orient it so its two ends agree with the
    #: flat surface at each end. Guessing instead ("rises away from its origin corner")
    #: gets it wrong about half the time, and a staircase pointing the wrong way
    #: connects nothing -- which is most of what kept converted TheCrunch fragmented.
    boxes = _orient_slopes(boxes, cell)

    if lines is None:
        def at_x(i):
            return i * cell
        at_y = at_x

        def span(a, b, axis):
            return range(int(a // cell), int((b - 1e-6) // cell) + 1)
    else:
        xs, ys = list(lines[0]), list(lines[1])

        def at_x(i):
            return xs[i]

        def at_y(j):
            return ys[j]

        def span(a, b, axis):
            ls = xs if axis == 0 else ys
            first = max(0, bisect.bisect_right(ls, a + 1e-6) - 1)
            last = min(len(ls) - 1, bisect.bisect_left(ls, b - 1e-6))
            return range(first, last)

    def mid_x(i):
        return (at_x(i) + at_x(i + 1)) / 2.0

    def mid_y(j):
        return (at_y(j) + at_y(j + 1)) / 2.0

    #: patches: one per piece, as {(cell): height}, with the piece's box alongside
    patches = []
    geom = []
    for box in boxes:
        lo, hi = box[0], box[1]
        sloped = box[2] if len(box) > 2 else False
        yaw = box[3] if len(box) > 3 else 0.0
        patch = {}
        for i in span(lo[0], hi[0], 0):
            for j in span(lo[1], hi[1], 1):
                x, y = mid_x(i), mid_y(j)
                patch[(i, j)] = _height_at(box, x, y)
        if patch:
            #: the box travels with the patch so its height can be read anywhere, not
            #: only at a cell centre -- see `_height_at`
            patches.append(patch)
            geom.append((lo, hi, sloped, yaw))
    if not patches:
        return []

    parent = list(range(len(patches)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    #: which patches touch each cell, so only plausible pairs are compared
    at_cell = collections.defaultdict(list)
    for n, patch in enumerate(patches):
        for c in patch:
            at_cell[c].append(n)
    for c, members in at_cell.items():
        x, y = mid_x(c[0]), mid_y(c[1])
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                za = _height_at(geom[members[a]], x, y)
                zb = _height_at(geom[members[b]], x, y)
                if abs(za - zb) <= step:
                    union(members[a], members[b])
    #: Patches that merely abut, compared AT THE SHARED BOUNDARY rather than at cell
    #: centres. A ramp's first cell centre already sits half a cell up the slope -- 75uu
    #: on a 45-degree test ramp, 150uu on real OMD3 stairs -- so comparing centres says
    #: the ramp does not meet the floor it physically touches, and every staircase ends
    #: up its own surface.
    for (i, j), members in at_cell.items():
        for di, dj in ((1, 0), (0, 1)):
            other = at_cell.get((i + di, j + dj))
            if not other:
                continue
            #: midpoint of the edge the two cells share
            bx = at_x(i + 1) if di else mid_x(i)
            by = at_y(j + 1) if dj else mid_y(j)
            for a in members:
                for b in other:
                    if find(a) == find(b):
                        continue
                    if abs(_height_at(geom[a], bx, by)
                           - _height_at(geom[b], bx, by)) <= step:
                        union(a, b)

    #: a surface is {(cell): height}, taking the HIGHEST reading where patches overlap
    surfaces = collections.defaultdict(dict)
    for n, patch in enumerate(patches):
        surf = surfaces[find(n)]
        for c, z in patch.items():
            if c not in surf or z > surf[c]:
                surf[c] = z

    #: Two surfaces can claim one cell a few tens of uu apart, because OMD2 layers
    #: DECORATIVE floors over structural ones and the conversion emits both. Left in,
    #: they become two nearly-coincident sheets -- 260 cells of converted TheCrunch had
    #: a pair 40uu apart -- and the graph then has two components where the level has one
    #: floor, with a mover able to be told it is on either. One floor per cell per step.
    best = {}
    for key, surf in surfaces.items():
        for c, z in surf.items():
            prev = best.get(c)
            if prev is None or z > prev[0]:
                best[c] = (z, key)
    for key, surf in list(surfaces.items()):
        for c in list(surf):
            z, owner = best[c]
            if owner != key and abs(surf[c] - z) <= step:
                del surf[c]
        if not surf:
            del surfaces[key]

    out = []
    for surf in surfaces.values():
        #: each corner's height is the mean of the surface's cells touching it, so two
        #: cells of one surface agree on their shared corners exactly
        corner = collections.defaultdict(list)
        for (i, j), z in surf.items():
            for ci in (i, i + 1):
                for cj in (j, j + 1):
                    corner[(ci, cj)].append(z)
        height = {c: sum(v) / len(v) for c, v in corner.items()}
        for (i, j) in sorted(surf):
            at = ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1))
            out.append([(at_x(c[0]), at_y(c[1]), height[c]) for c in at])
    return out


def _orient_slopes(boxes, cell, step=STEP):
    """Flip each sloped piece if the other orientation fits its neighbours better."""
    flat = [b for b in boxes if not (len(b) > 2 and b[2])]
    out = []
    for box in boxes:
        if not (len(box) > 2 and box[2]):
            out.append(box)
            continue
        lo, hi, _sl, yaw = box[0], box[1], box[2], box[3]
        turned = int(round((yaw % 360.0) / 90.0)) % 2 == 1
        axis = 0 if turned else 1
        best = None
        for flip in (False, True):
            #: the two ends of the slope, just outside the piece
            err = 0.0
            for end, want in ((lo[axis] - cell * 0.5, hi[2] if flip else lo[2]),
                              (hi[axis] + cell * 0.5, lo[2] if flip else hi[2])):
                px = end if axis == 0 else (lo[0] + hi[0]) / 2.0
                py = end if axis == 1 else (lo[1] + hi[1]) / 2.0
                near = [f[1][2] for f in flat
                        if f[0][0] - cell <= px <= f[1][0] + cell
                        and f[0][1] - cell <= py <= f[1][1] + cell]
                if near:
                    err += min(abs(z - want) for z in near)
                else:
                    err += step          #: no neighbour there: no evidence either way
            if best is None or err < best[0]:
                best = (err, flip)
        out.append((lo, hi, True, yaw, best[1]))
    return out


def _height_at(box, x, y):
    """A piece's own surface height above (x, y), clamped to its footprint."""
    lo, hi, sloped, yaw = box[0], box[1], box[2], box[3]
    flip = box[4] if len(box) > 4 else False
    return _slope_z(lo, hi, yaw, x, y, flip) if sloped else hi[2]


def _slope_z(lo, hi, yaw, x, y, flip=False):
    """The height of a rising piece's surface above (x, y).

    The rise runs along the piece's own depth, which a yaw turns into a world axis. The
    fraction is clamped, so a cell that only partly overlaps the piece still gets a
    height on its surface rather than beyond either end.
    """
    turned = int(round((yaw % 360.0) / 90.0)) % 2 == 1
    axis = 0 if turned else 1
    span = hi[axis] - lo[axis]
    if span <= 1e-6:
        return hi[2]
    at = x if axis == 0 else y
    f = min(max((at - lo[axis]) / span, 0.0), 1.0)
    if flip:
        f = 1.0 - f
    return lo[2] + (hi[2] - lo[2]) * f


def _bands(zs, step):
    """One representative height per cluster of values within `step` of each other."""
    out = []
    for z in zs:
        if not out or z - out[-1][-1] > step:
            out.append([z])
        else:
            out[-1].append(z)
    return [max(g) for g in out]


def graph_for(scene, cell=CELL):
    """(buffer, polygons) -- a surface navgraph for a converted scene."""
    from riftlord.convert import emitplan
    result = emitplan.plan(scene)
    boxes = floor_boxes(result)
    polys = cover(boxes, cell)
    if not polys:
        raise SystemExit('%s produced no walkable cover' % scene)
    bbox = navsurfgen.bbox_of(polys)
    return navsurfgen.buffer(polys, bbox), polys


def selftest():
    """A two-level cover must tile without overlap and connect along its ramp."""
    bad = []
    #: a 1200x600 floor at z=0, a ramp up to z=300, and a landing -- the shape a
    #: flat-cell approximation could not express
    boxes = [([0.0, 0.0, -10.0], [1200.0, 600.0, 0.0], False, 0.0),
             ([1200.0, 0.0, 0.0], [1800.0, 600.0, 300.0], True, 90.0),
             ([1800.0, 0.0, 290.0], [3000.0, 600.0, 300.0], False, 0.0)]
    polys = cover(boxes, cell=300.0)
    if not polys:
        bad.append('the cover is empty')
        return bad
    #: no two quads may claim the same cell at the same height
    seen = collections.Counter()
    for q in polys:
        seen[(round(q[0][0]), round(q[0][1]), round(q[0][2]))] += 1
    if any(v > 1 for v in seen.values()):
        bad.append('two quads claim the same cell and height')
    buf = navsurfgen.buffer(polys, navsurfgen.bbox_of(polys))
    for h in navsurf.graphs(buf):
        got = navsurf.sections(buf, h)
        probs = navsurf.check_sections(buf, h, got)
        probs += navsurf.check_tree(buf, h, got, navsurf.tree(buf, h))
        bad.extend('graph +%d: %s' % (h['base'], p) for p in probs)
    #: and the ramp must join the two levels into one component
    h = navsurf.graphs(buf)[0]
    got = navsurf.sections(buf, h)
    comps = navsurf.components(got, navsurf.adjacency(buf, h, got))
    total = sum(a for _n, a in comps) or 1.0
    if comps and comps[0][1] / total < 0.95:
        bad.append('the ramp did not connect the levels: largest component is %.0f%%'
                   % (100.0 * comps[0][1] / total))
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--scene', default='TheCrunch')
    ap.add_argument('--cell', type=float, default=CELL)
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args(argv)
    if args.selftest:
        bad = selftest()
        for b in bad:
            print('FAILED  %s' % b)
        print('navsurffloors selftest: %s'
              % ('passed' if not bad else '%d problem(s)' % len(bad)))
        return 1 if bad else 0
    buf, polys = graph_for(args.scene, args.cell)
    print('%s: %d walkable quad(s) -> %d byte buffer' % (args.scene, len(polys), len(buf)))
    for h in navsurf.graphs(buf):
        got = navsurf.sections(buf, h)
        adj = navsurf.adjacency(buf, h, got)
        comps = navsurf.components(got, adj)
        total = sum(a for _n, a in comps) or 1.0
        probs = (navsurf.check_sections(buf, h, got)
                 + navsurf.check_tree(buf, h, got, navsurf.tree(buf, h)))
        print('   graph +%-7d agent %-4.0f %5d records | %3d component(s), largest '
              '%3.0f%% of area | %s'
              % (h['base'], h['agent'][1], len(got), len(comps),
                 100.0 * comps[0][1] / total, probs[0][:40] if probs else 'PASS'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
