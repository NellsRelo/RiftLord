"""Turn OMD2 level geometry into OMD3 kit placements.

    python3 meshconvert.py --scene Passages
    python3 meshconvert.py --survey

## The idea

Both engines anchor a piece somewhere awkward and neither agrees with the other.
An OMD2 floor is centred on its tile while an OMD2 wall is corner-anchored
(`omd2mesh`); an OMD3 `Floor_4x4` spans 0..1200 from its origin while
`Floor_8x4_Wood` spans -1200..0 (`kitgeom`). Converting origin to origin means
carrying two conventions and a rotation between them, and being wrong by half a
piece wherever either is misread.

So nothing here converts origins. Each side is reduced to the **box it occupies**,
the boxes are matched, and the OMD3 actor's origin is then solved for -- placed
wherever it has to be for its own box to land on the target. The origin
conventions cancel instead of being modelled, and the result is checkable: after
placing, the piece's box must equal the box asked for.

## What is still approximate

* **Rotation is limited to yaw in 90-degree steps**, plus whatever the piece
  itself needs. OMD2's genuine slopes -- ramps near 21 degrees, angled walls near
  124 -- have no kit equivalent and are reported rather than placed.
* **A mesh becomes one piece or none.** Where OMD2 uses a mesh no kit footprint
  matches, the instance is counted as unmatched instead of being tiled out of
  smaller pieces. Tiling is `tiler`'s job and belongs on top of this.
"""

import argparse
import collections
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.mapeditor import kitgeom  # noqa: E402
from riftlord.formats.omd2 import omd2mesh  # noqa: E402
from riftlord.mapeditor import tiler  # noqa: E402
from riftlord.core import transform  # noqa: E402

#: How much of a cell two extents may differ by and still count as the same size.
TOLERANCE = 0.05

#: OMD2 names its geometry folders consistently within a level, and the folder is
#: a better surface test than the box: a floor and a ceiling are both thin in Z.
FOLDER_SURFACE = (('ceiling', 'Ceiling'), ('floor', 'Floor'), ('wall', 'Wall'))


def _rot(vec, yaw, pitch, roll):
    """OMD2's vForge rotation: roll about X, then pitch about Y, then yaw about Z."""
    x, y, z = vec
    y, z = transform._rot_x(y, z, roll)
    x, z = transform._rot_y(x, z, pitch)
    x, y = transform._rot_z(x, y, yaw)
    return (x, y, z)


def corners(lo, hi):
    return [(lo[0] if a else hi[0], lo[1] if b else hi[1], lo[2] if c else hi[2])
            for a in (0, 1) for b in (0, 1) for c in (0, 1)]


def aabb(points):
    return (tuple(min(p[i] for p in points) for i in range(3)),
            tuple(max(p[i] for p in points) for i in range(3)))


def omd2_world_box(inst, lib, models=None):
    """The axis-aligned box one OMD2 instance occupies, in OMD2 world units.

    A prefab child carries a composed `matrix` rather than angles, so that is used
    directly when present.

    Bounds come from the collision hull, falling back to the render mesh: a few
    meshes per level have no `.vcolmesh` at all -- the stair side walls are the
    common case -- and dropping them leaves a visible gap beside every staircase.
    """
    got = lib.bounds(inst['mesh'])
    if not got and models is not None:
        model = models.get(str(inst['mesh']).replace('\\', '/').lower())
        if model and model.get('verts'):
            vs = model['verts']
            got = (tuple(min(v[i] for v in vs) for i in range(3)),
                   tuple(max(v[i] for v in vs) for i in range(3)))
    if not got:
        return None
    lo, hi = got
    if inst.get('matrix'):
        m = inst['matrix']
        pts = []
        for c in corners(lo, hi):
            pts.append(tuple(
                m[r][0] * c[0] + m[r][1] * c[1] + m[r][2] * c[2] + m[r][3]
                for r in range(3)))
        return aabb(pts)
    sx, sy, sz = inst['scale']
    lo = (lo[0] * sx, lo[1] * sy, lo[2] * sz)
    hi = (hi[0] * sx, hi[1] * sy, hi[2] * sz)
    yaw, pitch, roll = inst['orientation']
    pts = [_rot(c, yaw, pitch, roll) for c in corners(lo, hi)]
    blo, bhi = aabb(pts)
    px, py, pz = inst['position']
    return ((blo[0] + px, blo[1] + py, blo[2] + pz),
            (bhi[0] + px, bhi[1] + py, bhi[2] + pz))


def to_unreal_box(box, origin):
    """An OMD2 world box -> an Unreal world box, via `transform.to_unreal`.

    Mirroring Y swaps which face is the minimum, so the corners are re-bounded
    rather than transformed in place.
    """
    lo, hi = box
    pts = [transform.to_unreal(*c, origin) for c in corners(lo, hi)]
    return aabb(pts)


def surface_of(inst, box):
    """'Floor', 'Wall' or 'Ceiling' for one instance.

    The folder decides where it can: OMD2 keeps floors, walls and ceilings apart
    and a box cannot tell a floor from a ceiling. Otherwise the thinnest axis
    does -- thin vertically is a horizontal surface, thin horizontally is a wall.
    """
    path = inst['mesh'].replace('\\', '/').lower()
    for word, surface in FOLDER_SURFACE:
        if word in path:
            return surface
    lo, hi = box
    ext = [hi[i] - lo[i] for i in range(3)]
    return 'Wall' if min(ext) in (ext[0], ext[1]) and ext[2] > min(ext) else 'Floor'


def face_of(box, surface):
    """(thin axis, axis i, axis j, extent i in cells, extent j in cells).

    The thin axis is taken from the surface, not inferred from the extents. A wall
    one cell thick and one cell wide is 300uu on two axes at once, and letting the
    smaller extent win broke the tie by axis index -- which chose the wall's width
    as its thickness and then matched nothing. A floor or ceiling is flat in Z; a
    wall is flat in whichever horizontal axis is thinner, and its rows run up Z.

    Axis identity has to survive this too: reducing a box to a sorted pair of
    extents is enough to look a piece up, but not to cut it into sub-boxes.
    """
    lo, hi = box
    ext = [hi[i] - lo[i] for i in range(3)]
    if surface == 'Wall':
        ta = 0 if ext[0] <= ext[1] else 1
        i, j = (1 if ta == 0 else 0), 2
    else:
        ta = 2
        i, j = 0, 1
    return (ta, i, j,
            ext[i] / transform.OMD3_CELL,
            ext[j] / transform.OMD3_CELL)


def sub_box(box, ta, i, j, x, y, w, h):
    """The part of `box` covering cells [x, x+w) along i and [y, y+h) along j."""
    lo, hi = box
    nlo, nhi = list(lo), list(hi)
    nlo[i] = lo[i] + x * transform.OMD3_CELL
    nhi[i] = lo[i] + (x + w) * transform.OMD3_CELL
    nlo[j] = lo[j] + y * transform.OMD3_CELL
    nhi[j] = lo[j] + (y + h) * transform.OMD3_CELL
    return (tuple(nlo), tuple(nhi))


def oriented_table(surface, available=None):
    """{(cols, rows): [(piece, grid index)]} for one surface, in placeable terms.

    Built with the same `face_of` the target boxes go through, so a footprint that
    this offers is a footprint `choose` can find. Deriving the sizes one way and
    looking them up another left wall gaps that looked like missing kit pieces and
    were really two tables disagreeing about which axis was which.

    A wall's height cannot be yawed into its width -- `Wall_RB_4x8` is 3 wide and
    8 tall and no rotation makes it 8 wide and 3 tall while it stays a wall -- so
    walls are listed one way round only. Floors and ceilings are flat, so for those
    the axes do swap.
    """
    geo = geometry()['pieces']
    out = {}
    for name, info in geo.items():
        if not name.startswith(surface):
            continue
        # A piece that cannot be lifted from any level must not be chosen, or the
        # plan is a plan for a level that cannot be built.
        if available is not None and name not in available:
            continue
        for gi, g in enumerate(info['grids']):
            box = (tuple(g['min']), tuple(g['max']))
            _ta, _i, _j, ei, ej = face_of(box, surface)
            a, b = int(round(ei)), int(round(ej))
            if abs(ei - a) > TOLERANCE or abs(ej - b) > TOLERANCE:
                continue
            if a < 1 or b < 1:
                continue
            out.setdefault((a, b), []).append((name, gi))
            if surface != 'Wall' and a != b:
                out.setdefault((b, a), []).append((name, gi))
    for key in out:
        out[key].sort()
    return out


def rotations():
    """Candidate actor rotations: yaw in quarter turns, upright and flipped."""
    for yaw in (0.0, 90.0, 180.0, 270.0):
        yield (0.0, yaw, 0.0)


def thin_axis(lo, hi):
    """Which axis a box is flattest on."""
    ext = [hi[i] - lo[i] for i in range(3)]
    return min(range(3), key=lambda i: ext[i])


#: Where a piece's grid plane belongs on the target box's thin axis.
#: A floor is walked on its top, a ceiling is seen from below, and a wall panel is
#: put down the middle of the slab so its collision stays inside the wall.
FACE = {'Floor': 'max', 'Ceiling': 'min', 'Wall': 'mid'}


def solve(grid, target, rotation, surface, tol=TOLERANCE):
    """Where to put the actor so its grid lands on `target`, or None.

    Thickness is deliberately not matched. An OMD2 floor is a solid slab 300uu
    thick once scaled, while an OMD3 trap grid is a 20uu plane sitting on the
    surface -- comparing the two rejects every correct piece. So the two face
    extents must agree and the thin axes must be the same axis, and the thin axis
    is then used to seat the plane on the right face of the slab.
    """
    glo, ghi = tuple(grid['min']), tuple(grid['max'])
    pitch, yaw, roll = rotation
    pts = [kitgeom._rotate(c, pitch, yaw, roll) for c in corners(glo, ghi)]
    rlo, rhi = aabb(pts)

    tlo, thi = target
    ta = face_of(target, surface)[0]
    ga = face_of((rlo, rhi), surface)[0]
    if ta != ga:
        return None

    err = 0.0
    for i in range(3):
        if i == ta:
            continue
        want = thi[i] - tlo[i]
        got = rhi[i] - rlo[i]
        err = max(err, abs(want - got) / transform.OMD3_CELL)
    if err > tol:
        return None

    location = [0.0, 0.0, 0.0]
    for i in range(3):
        if i == ta:
            continue
        location[i] = tlo[i] - rlo[i]
    mid = (rlo[ta] + rhi[ta]) / 2.0
    where = FACE.get(surface, 'mid')
    seat = thi[ta] if where == 'max' else (
        tlo[ta] if where == 'min' else (tlo[ta] + thi[ta]) / 2.0)
    location[ta] = seat - mid
    return tuple(location), err


def choose(surface, target, table, key, tol=TOLERANCE):
    """Pick a kit piece and rotation whose footprint matches `target`.

    Ranked by how closely the faces match, then by name so a level converts the
    same way twice.
    """
    best = None
    for name, gi in table.get(key, ()):
        if not name.startswith(surface):
            continue
        grid = geometry()['pieces'][name]['grids'][gi]
        for rotation in rotations():
            got = solve(grid, target, rotation, surface, tol)
            if got is None:
                continue
            location, err = got
            score = (round(err, 4), name)
            if best is None or score < best[0]:
                best = (score, {'piece': name, 'grid': gi, 'location': location,
                                'rotation': rotation, 'error': err,
                                'surface': surface})
    return best[1] if best else None


def cover_box(surface, target, table, tol=TOLERANCE):
    """Pieces covering one target box: one if the kit has that footprint, else a tiling.

    OMD2's main wall panel is 4 cells long and 6 tall and the kit has no 4x6 wall,
    so the commonest single piece in a castle level matches nothing on its own. It
    does tile -- 4x4 plus 4x2 -- and so do most of the other gaps, which is why a
    miss falls back to `tiler` rather than being dropped.
    """
    ta, i, j, ei, ej = face_of(target, surface)
    cols, rows = int(round(ei)), int(round(ej))
    if abs(ei - cols) > tol or abs(ej - rows) > tol or cols < 1 or rows < 1:
        return None, 'not a whole number of cells (%.2f x %.2f)' % (ei, ej)

    one = choose(surface, target, table, (cols, rows), tol)
    if one:
        return [one], None

    sizes = sorted(table)
    if not sizes:
        return None, 'no %s pieces at all' % surface
    plan = tiler.cover(cols, rows, sizes)
    if plan is None:
        return None, 'no %s tiling of %dx%d' % (surface, cols, rows)
    out = []
    for w, h, x, y in plan:
        piece = choose(surface, sub_box(target, ta, i, j, x, y, w, h),
                       table, (w, h), tol)
        if piece is None:
            return None, 'no %s piece for a %dx%d part of %dx%d' % (
                surface, w, h, cols, rows)
        out.append(piece)
    return out, None


_GEO = None


def geometry():
    global _GEO
    if _GEO is None:
        _GEO = kitgeom.load()
    return _GEO


def convert(scene, zip_path=None, want_collision=True, available=None):
    """Every OMD2 instance of one level, matched to a kit piece where possible."""
    geometry()
    tables = {s: oriented_table(s, available)
              for s in ('Floor', 'Wall', 'Ceiling')}
    lib = omd2mesh.Library(zip_path)
    insts = omd2mesh.scene_meshes(scene, zip_path)

    boxes = []
    for inst in insts:
        if want_collision and not inst['collides']:
            continue
        box = omd2_world_box(inst, lib)
        if box is None:
            continue
        boxes.append((inst, box))

    if not boxes:
        return {'scene': scene, 'placements': [], 'skipped': collections.Counter(),
                'origin': (0.0, 0.0, 0.0), 'instances': len(insts)}

    xs = [b[1][0][0] for b in boxes] + [b[1][1][0] for b in boxes]
    ys = [b[1][0][1] for b in boxes] + [b[1][1][1] for b in boxes]
    zs = [b[1][0][2] for b in boxes]
    origin = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0, min(zs))

    placed, skipped = [], collections.Counter()
    tiled = collections.Counter()
    unmatched = collections.Counter()
    for inst, box in boxes:
        target = to_unreal_box(box, origin)
        surface = surface_of(inst, box)
        got, why = cover_box(surface, target, tables[surface])
        if got is None:
            skipped[why] += 1
            unmatched[inst['mesh']] += 1
            continue
        for piece in got:
            piece['mesh'] = inst['mesh']
            piece['name'] = inst['name']
        placed.extend(got)
        tiled[len(got)] += 1
    return {'scene': scene, 'placements': placed, 'skipped': skipped,
            'origin': origin, 'instances': len(insts), 'considered': len(boxes),
            'unmatched_meshes': unmatched, 'matched': sum(tiled.values()),
            'pieces_per_mesh': tiled}



# -- area tiling ------------------------------------------------------------
#
# OMD2's art tiles at 400 units and its trap grid at 300, so three floor meshes
# span four trap cells and no mesh is a whole number of cells. Converting one mesh
# to one kit piece therefore cannot work at the true scale. Instead every mesh is
# rasterised onto OMD3's 300uu grid, the covered cells are pooled per surface and
# per plane, and each plane is tiled.
#
# Pooling is what removes the z-fighting as well: OMD2 stacks decorative floor
# layers (Passages lays FloorMainDesign04 over FloorMain1 in 518 places) and as
# separate pieces those came out coplanar and flickered. As cells they are the
# same cells, and a set collapses them.

CELL = transform.OMD3_CELL

#: How near two surfaces must be to count as the same plane. OMD2's floors are not
#: perfectly co-planar and a decorative layer sits a few units above its base.
PLANE_SNAP = 60.0

#: A cell counts as covered when a box spans at least this much of it on both
#: axes. Half means the converted floor has about the area of the original rather
#: than growing a cell at every edge.
COVER_SHARE = 0.5


def plane_of(surface, box):
    """(plane axis, plane value) for the surface this box presents.

    A floor is walked on its top and a ceiling seen from its underside, so those
    are the faces that matter; a wall is represented by the middle of its slab.
    """
    lo, hi = box
    if surface == 'Floor':
        return 2, hi[2]
    if surface == 'Ceiling':
        return 2, lo[2]
    ta = 0 if (hi[0] - lo[0]) <= (hi[1] - lo[1]) else 1
    return ta, (lo[ta] + hi[ta]) / 2.0


def span_cells(lo, hi):
    """Indices of the grid cells this interval covers.

    The edges are **rounded** to the nearest cell boundary rather than tested for
    a share of overlap. A share threshold systematically shrinks a surface: OMD2's
    400-unit art tile is 1.333 cells, so an isolated tile covered one cell and lost
    a quarter of its width, and every surface came out slightly small. Rounding
    centres that error instead of always losing.

    At least one cell is always returned, so a surface thinner than a cell becomes
    a cell rather than disappearing.
    """
    first = int(round(lo / CELL))
    last = int(round(hi / CELL))
    if last <= first:
        last = first + 1
    return list(range(first, last))


def axes_for(plane_axis):
    return [k for k in range(3) if k != plane_axis]


#: How far an instance's orientation may sit off a right angle and still be
#: rasterised onto axis-aligned cells.
SQUARE_TOLERANCE = 1.0


def is_axis_aligned(inst, tol=SQUARE_TOLERANCE):
    """Whether every angle of this instance is a multiple of 90 degrees."""
    for a in inst['orientation']:
        r = abs(a) % 90.0
        if min(r, 90.0 - r) > tol:
            return False
    return True


def rasterize(instances, lib, origin):
    """{(surface, plane axis, snapped plane): set of (i, j)}, and what was skipped.

    Off-axis instances are left out. A rotated piece's axis-aligned bounding box is
    not its shape -- OMD2's 45 degree wall `Wall45_1` has a 1500 x 1200 x 1800 box,
    so treating it as a wall makes its *thickness* 1200 and emits a fat slab facing
    the wrong way. That is worse than leaving it out, which is a gap a person reads
    as missing rather than as broken. OMD3 does ship angled pieces
    (`Wall_FB_2x2_Angle`, `Floor_RB_2x2_Angled`) so these can be done properly
    later.
    """
    planes = collections.defaultdict(set)
    skipped = collections.Counter()
    for inst in instances:
        box = omd2_world_box(inst, lib)
        if box is None:
            continue
        if is_stair(inst, box):
            continue
        if not is_axis_aligned(inst):
            skipped['off-axis instance'] += 1
            continue
        surface = surface_of(inst, box)
        target = to_unreal_box(box, origin)
        pa, value = plane_of(surface, target)
        a, b = axes_for(pa)
        cols = span_cells(target[0][a], target[1][a])
        rows = span_cells(target[0][b], target[1][b])
        if not cols or not rows:
            continue
        key = (surface, pa, round(value / PLANE_SNAP) * PLANE_SNAP)
        cells = planes[key]
        for i in cols:
            for j in rows:
                cells.add((i, j))
    return planes, skipped


def tile_cells(cells, sizes):
    """Cover a set of cells with the given footprints.

    Returns (tiles, patched, leftover). Greedy, largest first, from the lowest
    cell -- these are arbitrary shapes rather than rectangles, so `tiler.cover`'s
    exact search does not apply. What matters is that nothing overlaps and nothing
    is invented: a footprint is only laid where every cell it needs is part of the
    surface.

    A second pass then patches what greedy strands. The floor kit has no 1x1, so a
    lone cell has no piece that fits it exactly, and leaving it is a hole in a
    floor that orcs drop through. The patch lays the smallest piece that covers the
    cell and is allowed to hang over the edge, which is the lesser fault -- but
    never over a piece already placed, since overlapping coplanar floors are what
    made the ground flicker in the first place.
    """
    order = sorted(sizes, key=lambda wh: (-(wh[0] * wh[1]), -max(wh)))
    todo = set(cells)
    covered = set()
    tiles = []
    stranded = []
    while todo:
        x, y = min(todo)
        for w, h in order:
            block = [(x + dx, y + dy) for dx in range(w) for dy in range(h)]
            if all(c in todo for c in block):
                tiles.append((w, h, x, y))
                todo.difference_update(block)
                covered.update(block)
                break
        else:
            stranded.append((x, y))
            todo.discard((x, y))

    smallest = sorted(sizes, key=lambda wh: (wh[0] * wh[1], max(wh)))
    patched = leftover = 0
    for (x, y) in stranded:
        if (x, y) in covered:
            continue
        done = False
        for w, h in smallest:
            for ax in range(x - w + 1, x + 1):
                for ay in range(y - h + 1, y + 1):
                    block = [(ax + dx, ay + dy)
                             for dx in range(w) for dy in range(h)]
                    if any(c in covered for c in block):
                        continue
                    tiles.append((w, h, ax, ay))
                    covered.update(block)
                    patched += 1
                    done = True
                    break
                if done:
                    break
            if done:
                break
        if not done:
            leftover += 1
    return tiles, patched, leftover


def check_tiling(cells, tiles):
    """Complaints about a tiling. Empty means it is sound.

    Two faults matter and they have opposite symptoms: an overlap puts two
    coplanar pieces in the same place, which flickers, and a gap leaves a hole.
    """
    seen = collections.Counter()
    for w, h, x, y in tiles:
        for dx in range(w):
            for dy in range(h):
                seen[(x + dx, y + dy)] += 1
    bad = []
    over = sum(1 for c, n in seen.items() if n > 1)
    missed = sum(1 for c in cells if c not in seen)
    if over:
        bad.append('%d cell(s) covered more than once' % over)
    if missed:
        bad.append('%d cell(s) of the surface left uncovered' % missed)
    return bad



# -- stairs -----------------------------------------------------------------
#
# Stairs cannot go through the rasteriser. A flight is a slope, and rasterising it
# onto horizontal planes turns one staircase into a dozen one-cell floor planes --
# walkable, but it is not a staircase and it inflates the plane count. OMD3 ships
# stair and ramp pieces, so a flight becomes a piece.

#: OMD2 keeps its stairs in `art/Act2/Stairs` and names them accordingly.
STAIR_WORDS = ('stair', 'step')

#: The kit families that are a slope rather than a surface.
STAIR_FAMILIES = ('Stair', 'Ramp')

#: How tall a stair mesh must be, in cells, before it counts as a flight.
#: `MiniStep1A` and friends are 100 units high -- a single lip at the edge of a
#: floor, not a staircase. Matching those to a kit slope turned a 100-unit step
#: into a 1,200-unit flight; they belong in the floor pool, where they rasterise
#: to the floor they edge.
MIN_STAIR_RISE = 0.5


def is_stair(inst, box=None):
    """Whether this instance is a flight of stairs rather than a step or a floor."""
    path = inst['mesh'].replace('\\', '/').lower()
    if not any(w in path for w in STAIR_WORDS):
        return False
    if box is None:
        return True
    lo, hi = box
    return (hi[2] - lo[2]) / transform.OMD3_CELL >= MIN_STAIR_RISE


def stair_pieces(available=None):
    """[(name, grid index, run, width, rise)] for every measured slope piece.

    Run and rise are in cells. Only pieces with a measured grid are usable, so the
    `StairGroup_*` whose grids live in a parent Blueprint are absent -- see
    [kit-geometry] for the 126 pieces that measurement still misses.
    """
    geo = geometry()['pieces']
    out = []
    for name, info in sorted(geo.items()):
        if not name.startswith(STAIR_FAMILIES):
            continue
        if available is not None and name not in available:
            continue
        for gi, g in enumerate(info['grids']):
            lo, hi = tuple(g['min']), tuple(g['max'])
            ext = [(hi[k] - lo[k]) / CELL for k in range(3)]
            horiz = sorted(ext[:2], reverse=True)
            out.append((name, gi, horiz[0], horiz[1], ext[2]))
    return out


def place_stairs(instances, lib, origin, pieces):
    """One kit slope per OMD2 stair instance, matched on run then rise.

    Matched by shape rather than tiled: a flight is a single object and cutting it
    into cells is what we are avoiding. The piece's box minimum is put on the
    target's box minimum, so the foot of the stairs lands where OMD2's foot was.
    """
    placed = []
    missed = collections.Counter()
    for inst in instances:
        box = omd2_world_box(inst, lib)
        if box is None:
            continue
        target = to_unreal_box(box, origin)
        tlo, thi = target
        ext = [(thi[k] - tlo[k]) / CELL for k in range(3)]
        run_axis = 0 if ext[0] >= ext[1] else 1
        run, width, rise = max(ext[0], ext[1]), min(ext[0], ext[1]), ext[2]
        if not pieces:
            missed['no slope pieces in the kit'] += 1
            continue
        # Run and rise are weighed together. Ranking on run first picked a piece
        # whose rise was four cells for a flight that climbs one third of one.
        best = min(pieces,
                   key=lambda p: (abs(p[2] - run) + abs(p[4] - rise), p[0]))
        name, gi, prun, _pw, prise = best
        grid = geometry()['pieces'][name]['grids'][gi]
        glo, ghi = tuple(grid['min']), tuple(grid['max'])
        # yaw so the piece's own run lies along the target's run
        gext = [ghi[k] - glo[k] for k in range(3)]
        piece_run_axis = 0 if gext[0] >= gext[1] else 1
        yaw = 0.0 if piece_run_axis == run_axis else 90.0
        pts = [kitgeom._rotate(c, 0.0, yaw, 0.0) for c in corners(glo, ghi)]
        rlo, _rhi = aabb(pts)
        location = tuple(tlo[k] - rlo[k] for k in range(3))
        placed.append({'piece': name, 'grid': gi, 'location': location,
                       'rotation': (0.0, yaw, 0.0), 'error': 0.0,
                       'surface': 'Stair', 'mesh': inst['mesh'],
                       'name': 'Stair_%d' % len(placed)})
        if abs(prun - run) > 0.5 or abs(prise - rise) > 0.5:
            missed['slope approximated (run %.1f->%.1f, rise %.1f->%.1f)'
                   % (run, prun, rise, prise)] += 1
    return placed, missed



def ground_at(planes, x, y, z=None, reach=4000.0, search=0):
    """The height of the converted floor under (x, y), or None.

    Gameplay actors cannot use OMD2's Z. That value is the entity's own pivot in a
    different engine, and it put the rift 2,275 units under the map and a player
    start below every floor. What they want is the surface beneath them, which the
    rasterised planes already know.

    The nearest floor at or below `z` wins, so an actor in a stairwell lands on the
    storey it belonged to rather than the roof. With no `z`, the highest floor
    under the point is used.

    `search` widens the hunt in rings of cells when nothing is directly underneath.
    OMD2 puts its spawners **outside** the level, behind the doors the horde breaks
    through, so there is genuinely no floor under them -- and an OMD3 spawner left
    at OMD2's height hangs in the air. Taking the height of the nearest floor is
    not where a door would be, but it is the right storey.
    """
    cell = CELL
    i0, j0 = int(math.floor(x / cell)), int(math.floor(y / cell))
    floors = [(pa, value, cells) for (surface, pa, value), cells in planes.items()
              if surface == 'Floor' and pa == 2]

    def best_at(i, j):
        best = None
        for _pa, value, cells in floors:
            if (i, j) not in cells:
                continue
            if z is not None and (value > z + cell or z - value > reach):
                continue
            if best is None or value > best:
                best = value
        return best

    got = best_at(i0, j0)
    if got is not None or search <= 0:
        return got
    for r in range(1, search + 1):
        found = []
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                if max(abs(di), abs(dj)) != r:
                    continue
                at = best_at(i0 + di, j0 + dj)
                if at is not None:
                    found.append(at)
        if found:
            return max(found)
    return None



def convert_areas(scene, zip_path=None, available=None, want_collision=True,
                  origin=None):
    """Convert one OMD2 level by tiling its surfaces, not by matching its meshes.

    `origin` pins the point the placements are measured from. Left out, it is the
    centre of the level's own bounds -- but a caller that is drawing the same level
    beside the result has already chosen one, and the two must agree or every piece
    comes out shifted by the difference between two bounding boxes.
    """
    geometry()
    tables = {s: oriented_table(s, available)
              for s in ('Floor', 'Wall', 'Ceiling')}
    lib = omd2mesh.Library(zip_path)
    insts = [i for i in omd2mesh.scene_meshes(scene, zip_path)
             if i['collides'] or not want_collision]

    boxes = []
    for inst in insts:
        box = omd2_world_box(inst, lib)
        if box is not None:
            boxes.append(box)
    if not boxes:
        return {'scene': scene, 'placements': [], 'origin': (0.0, 0.0, 0.0),
                'instances': len(insts), 'considered': 0, 'matched': 0,
                'skipped': collections.Counter(), 'planes': 0, 'cells': 0,
                'stranded': 0}

    if origin is None:
        xs = [b[0][0] for b in boxes] + [b[1][0] for b in boxes]
        ys = [b[0][1] for b in boxes] + [b[1][1] for b in boxes]
        zs = [b[0][2] for b in boxes]
        origin = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0, min(zs))
    origin = tuple(float(v) for v in origin)

    planes, raster_skipped = rasterize(insts, lib, origin)
    slopes, slope_missed = place_stairs(
        [i for i in insts
         if is_stair(i, omd2_world_box(i, lib))], lib, origin,
        stair_pieces(available))
    placed, skipped = [], collections.Counter()
    skipped.update(raster_skipped)
    skipped.update(slope_missed)
    placed.extend(slopes)
    total_cells = stranded = filled = 0

    for (surface, pa, value), cells in sorted(planes.items()):
        total_cells += len(cells)
        table = tables[surface]
        if not table:
            skipped['no %s pieces at all' % surface] += len(cells)
            continue
        tiles, patched, lost = tile_cells(cells, sorted(table))
        stranded += lost
        filled += patched
        for complaint in check_tiling(cells, tiles):
            skipped['%s plane: %s' % (surface, complaint)] += 1
        if lost:
            skipped['%s cell with no piece that fits' % surface] += lost
        a, b = axes_for(pa)
        for w, h, x, y in tiles:
            lo = [0.0, 0.0, 0.0]
            hi = [0.0, 0.0, 0.0]
            lo[pa] = hi[pa] = value
            lo[a], hi[a] = x * CELL, (x + w) * CELL
            lo[b], hi[b] = y * CELL, (y + h) * CELL
            got = choose(surface, (tuple(lo), tuple(hi)), table, (w, h))
            if got is None:
                skipped['no %s piece %dx%d' % (surface, w, h)] += 1
                continue
            got['mesh'] = ''
            got['name'] = '%s_%d' % (surface, len(placed))
            placed.append(got)

    return {'scene': scene, 'placements': placed, 'origin': origin,
            'instances': len(insts), 'considered': len(boxes),
            'matched': len(placed), 'skipped': skipped,
            'planes': len(planes), 'cells': total_cells, 'stranded': stranded,
            'patched': filled, 'slopes': len(slopes), 'plane_cells': planes,
            'unmatched_meshes': collections.Counter()}


def check(result, tol=TOLERANCE):
    """Complaints about a conversion. Empty means every piece landed where asked."""
    bad = []
    for p in result['placements']:
        if p['error'] > tol:
            bad.append('%s: box off by %.2f cell(s)' % (p['piece'], p['error']))
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--scene')
    ap.add_argument('--survey', action='store_true')
    ap.add_argument('--limit', type=int, default=10)
    args = ap.parse_args(argv)

    scenes = ([args.scene] if args.scene
              else ['Passages', 'TheCrunch', 'UpAndDown', 'TwistedHalls', 'TheEdge'])
    print('%-16s %9s %9s %8s  %s'
          % ('scene', 'considered', 'placed', 'share', 'commonest miss'))
    for scene in scenes:
        try:
            r = convert(scene)
        except Exception as exc:                      # noqa: BLE001
            print('%-16s  failed: %s' % (scene, exc))
            continue
        n = r['considered'] or 1
        miss = r['skipped'].most_common(1)
        print('%-16s %9d %9d %7.0f%%  %s'
              % (scene, r['considered'], r['matched'],
                 100.0 * r['matched'] / n,
                 ('%s (x%d)' % miss[0]) if miss else '-'))
        bad = check(r)
        if bad:
            print('      MISPLACED: %d, e.g. %s' % (len(bad), bad[0]))
        if args.scene:
            for s, c in r['skipped'].most_common(args.limit):
                print('      missed %5d  %s' % (c, s))
            print('      top unmatched meshes:')
            for m, c in r['unmatched_meshes'].most_common(6):
                print('        %5d  %s' % (c, m.replace('\\', '/')[4:]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
