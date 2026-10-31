"""Merge OMD2 mesh instances into runs, before any of them becomes OMD3 art.

    python3 artgroup.py --scene TheCrunch
    python3 artgroup.py --selftest

**Converting one OMD2 instance to one OMD3 mesh is wrong**, and the art review said so
from three directions at once: OMD2's `WallBase4_6` equals two OMD3 `Wall_Sec_2_3`
pieces; `wallarch_topblock` should be dropped because an OMD3 archway is one or two
meshes rather than an assembly of blocks; and a railing is `RailingA` at the end of a
run and `RailingB` in the middle. All three need to know what a piece's NEIGHBOURS are.

So this groups first. Same mesh, same orientation, same plane, adjacent on OMD2's own
400-unit lattice, merged into maximal rectangles. An OMD3 family member is then chosen
for the rectangle rather than for the tile -- `Floor_Squarebrick_4x4` once instead of
nine 1.33-cell scraps.

**The lattice is measured, not assumed**: across TheCrunch's commonest meshes the modal
gap between distinct positions is 400 on both in-plane axes, for walls as well as floors
and ceilings. Smaller gaps exist and are the spacing BETWEEN runs in different rooms,
not a finer pitch -- which is why the pitch has to be read from the commonest gap and
not from the smallest, a mistake that made walls look unmergeable.
"""

import argparse
import collections
import math
import os
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

#: OMD2's art lattice. Three of these span four OMD3 trap cells (400/300), which is why
#: nothing matches cell-for-cell and why scale, not tiling, closes the gap.
OMD2_PITCH = 400.0

#: How far off the lattice an instance may sit and still be merged. OMD2's own placement
#: is not perfectly regular and a hard equality test drops rows that are plainly a run.
SNAP = 24.0


def rot3(orientation):
    """The 3x3 world rotation for an OMD2 `_orientation`, in vForge's own order.

    The triple is **(yaw, pitch, roll)** -- yaw about z, pitch about y, roll about x,
    applied Rz . Ry . Rx, which is what `omd2prefab.compose` builds and what
    `omd2mesh.decompose` reads back. Unpacking it as (pitch, yaw, roll) silently swaps
    the two angles that matter most here, because a wall is turned by yaw alone.
    """
    yaw, pitch, roll = (list(orientation) + [0.0, 0.0, 0.0])[:3]
    cr, sr = math.cos(math.radians(roll)), math.sin(math.radians(roll))
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    rx = [[1, 0, 0], [0, cr, -sr], [0, sr, cr]]
    ry = [[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]]
    rz = [[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]]
    def mul(a, b):
        return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)]
                for r in range(3)]
    return mul(rz, mul(ry, rx))


def local_box(mesh, models=None):
    """(size x, size y, size z) of a mesh in its own space, or None if unknown."""
    models = models if models is not None else _models()
    key = mesh.replace('\\', '/').lower()
    model = models.get(key)
    if model is None:
        for k, v in models.items():
            if key.endswith(k) or k.endswith(key):
                model = v
                break
    if not model or not model.get('verts'):
        return None
    vs = model['verts']
    return tuple(max(v[a] for v in vs) - min(v[a] for v in vs) for a in (0, 1, 2))


def local_bounds(mesh, models=None):
    """((lo x, lo y, lo z), (hi x, hi y, hi z)) in the mesh's own space, or None.

    **OMD2 does not centre its meshes on their own origin**, and that is load bearing:
    `floormain1` and `ground4x4` run z -100..0, so the origin is the WALKING SURFACE;
    `3widestairs`, `wall1_1` and `ceiling4x4` run 0..600 or 0..100, so the origin is the
    bottom. Treating the instance position as the box centre put every staircase 300uu
    too low -- its top landing on a floor and its bottom hanging in the void -- and every
    floor 50uu too high.
    """
    models = models if models is not None else _models()
    key = mesh.replace('\\', '/').lower()
    model = models.get(key)
    if model is None:
        for k, v in models.items():
            if key.endswith(k) or k.endswith(key):
                model = v
                break
    if not model or not model.get('verts'):
        return None
    vs = model['verts']
    return (tuple(min(v[a] for v in vs) for a in (0, 1, 2)),
            tuple(max(v[a] for v in vs) for a in (0, 1, 2)))


def world_bounds(mesh, orientation, models=None):
    """The mesh's own box turned into world axes, as offsets from the instance position.

    Returns ((lo x, lo y, lo z), (hi x, hi y, hi z)) relative to where the instance sits,
    so a caller adds the instance position to get the world box. This is what
    `world_extent` should have been: the extent alone loses WHERE the box sits around
    the origin, which for OMD2 is never the centre.
    """
    box = local_bounds(mesh, models)
    if box is None:
        return None
    lo, hi = box
    rot = rot3(orientation)
    out_lo, out_hi = [], []
    for a in (0, 1, 2):
        #: every corner of the local box, projected onto this world axis
        vals = [sum(rot[a][b] * (lo[b] if pick & (1 << b) else hi[b])
                    for b in (0, 1, 2)) for pick in range(8)]
        out_lo.append(min(vals))
        out_hi.append(max(vals))
    return tuple(out_lo), tuple(out_hi)


def world_extent(mesh, orientation, models=None):
    """How far a placed instance reaches along each WORLD axis, in uu.

    This is the mesh's own box turned into world axes. It is used for a run's extent,
    never for its pitch -- deriving the pitch from a box made the merging worse twice,
    because a box says how big the art is and not how OMD2's designers spaced it.
    """
    box = local_box(mesh, models)
    if box is None:
        return None
    rot = rot3(orientation)
    return tuple(sum(abs(rot[a][b]) * box[b] for b in (0, 1, 2)) for a in (0, 1, 2))


def plane_of(mesh, orientation, models=None, tol=20.0):
    """Which WORLD axis a piece's face points along: 0=x, 1=y, 2=z.

    Read from the mesh's **thinnest local axis** turned by the instance's rotation, not
    from the angles alone. Measured: OMD2 authors a wall upright already -- `Wall1_1` is
    100 x 400 x 600, thin on local x -- so a wall carries no pitch and no roll and is
    aimed purely by yaw. An angles-only test therefore called every yaw-0 wall a floor
    and merged facing walls on opposite sides of a room into a single run.
    """
    box = local_box(mesh, models)
    if box is None:
        #: no model to measure: fall back to the angles, in the right order this time
        _yaw, pitch, roll = (list(orientation) + [0.0, 0.0, 0.0])[:3]
        if abs(abs(pitch) - 90.0) <= tol:
            return 0
        if abs(abs(roll) - 90.0) <= tol:
            return 1
        return 2
    thin = min((0, 1, 2), key=lambda a: box[a])
    rot = rot3(orientation)
    return max((0, 1, 2), key=lambda a: abs(rot[a][thin]))


def _rects(cells):
    """Cover a set of (i, j) cells with maximal rectangles, largest first.

    Greedy: take the biggest rectangle that fits entirely in what is left, repeat. The
    result is not minimal and does not need to be -- it needs to be EXACT, so that a
    merged run covers the same cells the instances did and no others.
    """
    left = set(cells)
    out = []
    while left:
        best = None
        for (i, j) in left:
            w = 0
            while (i + w, j) in left:
                w += 1
            #: grow the height only as far as EVERY column still fits, so the
            #: rectangle stays solid
            h = 0
            while all((i + dx, j + h) in left for dx in range(w)):
                h += 1
            while w > 0 and h > 0:
                if best is None or w * h > best[0] * best[1]:
                    best = (w, h, i, j)
                break
        if best is None:
            break
        w, h, i, j = best
        for dx in range(w):
            for dy in range(h):
                left.discard((i + dx, j + dy))
        out.append((w, h, i, j))
    return out


def _models(cache={}):
    if not cache:
        import json
        with open(gamepath.data('omd2-models.json')) as fh:
            cache.update(json.load(fh)['models'])
    return cache


def observed_pitch(positions, default=OMD2_PITCH, snap=SNAP):
    """The spacing these instances ACTUALLY use, as the commonest gap between them.

    **Measured, not derived.** Two attempts to compute the pitch from a mesh's bounding
    box both made the merging worse -- the first indexed a local box by a world axis
    (5.6x down to 2.0x on `WallBase4_6`), the second rotated the box correctly and was
    worse still. A bounding box says how big the art is; it does not say how OMD2's
    designers spaced it, and they overlap and inset pieces freely.

    So the lattice is read off the placements: sort the distinct coordinates, take the
    commonest gap, fall back to 400 when there is only one position or no gap repeats.
    """
    vals = sorted({round(v / 1.0) for v in positions})
    if len(vals) < 3:
        return default
    gaps = collections.Counter()
    for a, b in zip(vals, vals[1:]):
        d = b - a
        if d >= 50:
            gaps[round(d / 50.0) * 50] += 1
    if not gaps:
        return default
    best, n = gaps.most_common(1)[0]
    #: one repeat is not a lattice; two placements that happen to share a gap would
    #: otherwise define the pitch for the whole run
    return float(best) if n >= 2 else default


def runs(instances, pitch=None, snap=SNAP):
    """[{mesh, orientation, axis, offset, cells, origin, count}] for merged runs."""
    models = _models()
    buckets = collections.defaultdict(list)
    for inst in instances:
        orient = [round(v, 1) for v in (inst.get('orientation') or [0, 0, 0])]
        axis = plane_of(inst['mesh'], orient, models)
        pos = inst['position']
        offset = round(pos[axis] / snap) * snap
        buckets[(inst['mesh'], tuple(orient), axis, offset)].append(inst)
    out = []
    stacked = 0
    for (mesh, orient, axis, offset), group in buckets.items():
        ax = [a for a in (0, 1, 2) if a != axis]
        base = [min(g['position'][a] for g in group) for a in ax]
        if pitch:
            p = [pitch, pitch] if isinstance(pitch, float) else list(pitch)[:2]
        else:
            p = [observed_pitch([g['position'][ax[k]] for g in group])
                 for k in (0, 1)]
        ext = world_extent(mesh, list(orient), models)
        #: a run reaches (count - 1) pitches plus ONE PIECE, which is not count pitches:
        #: a lone 800uu `Wall1_2` on a 400 lattice would otherwise be emitted half length
        reach = [ext[ax[0]], ext[ax[1]]] if ext else [p[0], p[1]]
        #: where the piece's box sits AROUND the instance
        #: position, which for OMD2 is never the centre --
        #: see `local_bounds`
        rel = world_bounds(mesh, list(orient), models)
        cells = {}
        for g in group:
            ij = tuple(int(round((g['position'][ax[k]] - base[k]) / p[k]))
                       for k in (0, 1))
            #: two instances in one cell is a STACK, not a tiling -- OMD2 layers
            #: decorative floors over structural ones. Counted rather than dropped.
            if ij in cells:
                stacked += 1
                continue
            cells[ij] = g
        for w, h, i, j in _rects(set(cells)):
            origin = [0.0, 0.0, 0.0]
            origin[axis] = offset
            origin[ax[0]] = base[0] + i * p[0]
            origin[ax[1]] = base[1] + j * p[1]
            out.append({'mesh': mesh, 'orientation': list(orient), 'axis': axis,
                        'offset': offset, 'cells': (w, h), 'origin': origin,
                        'count': w * h, 'pitch': p,
                        'offset_lo': list(rel[0]) if rel else None,
                        'offset_hi': list(rel[1]) if rel else None,
                        'span': (max((w - 1) * p[0] + reach[0], reach[0]),
                                 max((h - 1) * p[1] + reach[1], reach[1]))})
    runs.stacked = stacked
    return out


def selftest():
    bad = []
    #: a 3x2 block of floor tiles must come out as ONE run, not six
    insts = [{'mesh': 'f', 'orientation': [0, 0, 0],
              'position': [i * 400.0, j * 400.0, 0.0]}
             for i in range(3) for j in range(2)]
    got = runs(insts)
    if len(got) != 1 or got[0]['cells'] != (3, 2):
        bad.append('a 3x2 block merged to %r, expected one 3x2'
                   % [g['cells'] for g in got])
    #: an L shape cannot be one rectangle, and must still cover every cell exactly
    cells = {(0, 0), (1, 0), (2, 0), (0, 1)}
    insts = [{'mesh': 'f', 'orientation': [0, 0, 0],
              'position': [i * 400.0, j * 400.0, 0.0]} for i, j in cells]
    got = runs(insts)
    covered = sum(g['count'] for g in got)
    if covered != len(cells):
        bad.append('an L of %d cells was covered by %d' % (len(cells), covered))
    #: two planes at different heights never merge together
    insts = [{'mesh': 'f', 'orientation': [0, 0, 0], 'position': [0.0, 0.0, 0.0]},
             {'mesh': 'f', 'orientation': [0, 0, 0], 'position': [0.0, 0.0, 600.0]}]
    if len(runs(insts)) != 2:
        bad.append('two heights merged into one run')
    #: a wall (pitched 90) is grouped in its own plane, not the floor's
    #: a wall mesh is thin on local x and upright, so yaw alone aims it: yaw 0 faces x,
    #: yaw 90 faces y. An angles-only reading called the first of these a floor.
    wall = {'art/test/wall.vmesh': {'verts': [[0, 0, 0], [100, 400, 600]]}}
    floor = {'art/test/floor.vmesh': {'verts': [[0, 0, 0], [400, 400, 100]]}}
    if plane_of('art/test/wall.vmesh', [0.0, 0.0, 0.0], wall) != 0:
        bad.append('a yaw-0 wall was not read as facing x')
    if plane_of('art/test/wall.vmesh', [90.0, 0.0, 0.0], wall) != 1:
        bad.append('a yaw-90 wall was not read as facing y')
    if plane_of('art/test/floor.vmesh', [37.0, 0.0, 0.0], floor) != 2:
        bad.append('a yawed floor was not read as facing z')
    #: with no model to measure, the angles are the fallback -- in vForge's order
    if plane_of('art/test/absent.vmesh', [0.0, 90.0, 0.0], {}) != 0:
        bad.append('the angles-only fallback misread a pitched piece')
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--scene', default='TheCrunch')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--top', type=int, default=12)
    args = ap.parse_args(argv)
    if args.selftest:
        bad = selftest()
        for b in bad:
            print('FAILED  %s' % b)
        print('artgroup selftest: %s' % ('passed' if not bad else '%d problem(s)' % len(bad)))
        return 1 if bad else 0
    from riftlord.formats.omd2 import trapgrids
    from riftlord.formats.omd2 import omd2mesh
    insts = omd2mesh.scene_meshes(args.scene, trapgrids.DEFAULT_DATA_ZIP)
    got = runs(insts)
    merged = sum(r['count'] for r in got)
    print('%s: %d instance(s) -> %d run(s), %d cell(s) accounted for'
          % (args.scene, len(insts), len(got), merged))
    big = collections.Counter()
    for r in got:
        big[r['mesh'].rsplit('\\', 1)[-1]] += 1
    sizes = collections.Counter(r['cells'] for r in got)
    print('  commonest run sizes: %s'
          % ', '.join('%dx%d x%d' % (c[0], c[1], n) for c, n in sizes.most_common(8)))
    bigr = max(got, key=lambda r: r['count'])
    print('  biggest run: %dx%d' % bigr['cells'])
    print()
    print('%-34s %7s %7s  %s' % ('mesh', 'insts', 'runs', 'saving'))
    per = collections.Counter()
    for i in insts:
        per[i['mesh']] += 1
    for mesh, nruns in big.most_common(args.top):
        full = next((v for k, v in per.items() if k.endswith(mesh)), 0)
        print('%-34s %7d %7d  %4.1fx' % (mesh, full, nruns, full / max(nruns, 1)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#: A mesh's dominant vertical face direction, in its own local frame, as a signed angle.
#:
#: `plane_of` answers which AXIS a piece faces and throws the sign away, so a wall facing
#: +x and one facing -x are indistinguishable to it. Measured on TheCrunch: every
#: directional wall mesh is placed at both a yaw and its opposite (`Wall1_2` at 0 and
#: 180, `Wall45_1` at 90 and -90), and 2,925 emitted pieces come from such a source -- so
#: half of them had a finished face pointing into the rock. `misfacing` cannot see this:
#: it tests which axis a box is thinnest on, which is identical either way round.
#:
#: The share is returned too, because a symmetric piece has no front and must not be
#: turned on the strength of a coin toss.
_FRONT = {}


def front_of(mesh, models=None):
    """(angle in degrees, share of vertical face area) for a mesh's finished face.

    None when the mesh has no vertical faces at all. Read from the render mesh's own
    triangles -- a bounding box cannot carry a facing, which is the same reason
    `DIRECTIONAL_ROLES` exists for stairs.
    """
    key = str(mesh).replace('\\', '/').lower()
    if key in _FRONT:
        return _FRONT[key]
    out = None
    try:
        from riftlord.formats.omd2 import omd2vmesh
        global _LIB
        try:
            _LIB
        except NameError:
            _LIB = omd2vmesh.Library()
        m = _LIB.read(mesh)
        if m:
            vs, tris = m['verts'], m['tris']
            w = collections.Counter()
            for t in range(0, len(tris) - 2, 3):
                p, q, r = vs[tris[t]], vs[tris[t + 1]], vs[tris[t + 2]]
                u = [q[k] - p[k] for k in (0, 1, 2)]
                v = [r[k] - p[k] for k in (0, 1, 2)]
                n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                     u[0] * v[1] - u[1] * v[0]]
                L = math.sqrt(sum(c * c for c in n))
                if L < 1e-6 or abs(n[2] / L) > 0.8:
                    continue
                a = round((math.degrees(math.atan2(n[1], n[0])) % 360.0) / 5.0) * 5 % 360
                w[a] += L / 2.0
            if w:
                total = sum(w.values())
                ang, area = w.most_common(1)[0]
                out = (float(ang), area / total)
    except Exception:                                    # noqa: BLE001
        out = None
    _FRONT[key] = out
    return out

#: A source mesh's own climb direction, in its local frame -- the other half of what
#: `front_of` does for a wall.
#:
#: OMD2's staircases climb toward local +x (0 deg); OMD3's kit stairs climb toward local
#: -y (270 deg), measured over the whole kit by `stairfit`. Placing a kit stair at its
#: source's own yaw therefore puts the climb a quarter turn off, and no box-based check
#: can see it because the box is identical either way.
_ASCENT = {}


def ascent_of(mesh, models=None):
    """(degrees the mesh climbs toward, rise in uu) in its own frame, or None if flat."""
    key = str(mesh).replace('\\', '/').lower()
    if key in _ASCENT:
        return _ASCENT[key]
    out = None
    try:
        from riftlord.formats.omd2 import omd2vmesh
        from riftlord.convert import stairfit
        global _LIB
        try:
            _LIB
        except NameError:
            _LIB = omd2vmesh.Library()
        m = _LIB.read(mesh)
        if m:
            out = stairfit.ascent(m['verts'], m['tris'])
    except Exception:                                      # noqa: BLE001
        out = None
    _ASCENT[key] = out
    return out
