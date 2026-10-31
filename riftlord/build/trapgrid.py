"""Convert OMD2 trap-placement volumes into OMD3 trap grids.

    python3 trapgrid.py --scene TheCrunch --check

Without trap grids a converted map cannot be played at all: there is nowhere to put
a trap. Shipped OMD3 maps hang their grids off kit-piece Blueprints
(`Column_Tall_1_RBFB_C`, `Wall_RB_4x4_C`) as construction-script components, which is
exactly why the mesh-driven emitter produces none -- it places plain
`StaticMeshActor`s. `BP_OMDTrapGrid_Blank_C` is a standalone grid whose `BoxExtent`
and `TrapPlacementTypes` are per-instance overrides, so one is lifted per rectangle.

**An OMD3 trap grid has NO per-cell mask.** Every cell inside the box is buildable,
while an OMD2 volume carries a `W|H|bitmask` saying exactly which cells are open. So
a volume becomes a SET of boxes: `rectangles` covers its open cells with maximal
rectangles, biggest first. TheCrunch's 273 volumes hold 7,196 open cells and come out
as 441 boxes, 209 of the volumes needing only one.

**`TrapPlacementTypes` is a bitmask**, measured across 866 trap boxes in 532 kit
Blueprints by cross-tabulating against each box's own pitch:

| surface | pitch | value | meaning |
|---|---|---|---|
| Floor | +90 | **9** | `Floor` \\| `WarMachineFloor` |
| Wall | 0 | **2** | `Wall` |
| Ceiling | -90 | **4** | `Ceiling` |

Floor is 9, not the 1 that `UOMDTrapGridBoxComponent`'s constructor defaults to --
a floor accepts war machines as well as traps.

**Every box in the kit is authored thin on its LOCAL X**, half-extent 10, whatever
surface it is; the rotation is what makes it a floor, a wall or a ceiling. So the
rotation is not cased on the surface here. The volume's own plane gives an
orthonormal basis -- the world directions of its local +x and +y, and their cross
product as the normal -- and the actor's rotator is read back out of that matrix by
`transform_of`. Hand-deriving angle conventions per surface is how the earlier
quarter-turn bugs happened; a basis cannot be got round the wrong way.

**Space.** The mesh-driven emitter works in RAW OMD2 coordinates -- no mirror, no
scale -- so these boxes do too, plus whatever anchor shift the build applies. That is
measured, not assumed: the source instances span y -8413..9613 and the emitted
geometry -8200..9462, not negated. `transform.to_unreal` belongs to the OLD
volume-driven pipeline and must NOT be used here.

**The cell is 200 world units** (settled 2026-10-17; it was 300 here, which made every
box 1.5x too large and pushed rectangles 1.5x too far from their volume's centre).
`mSize` is 100 per cell, a half extent. See `transform.OMD2_CELL`.
"""

import argparse
import collections
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import transform  # noqa: E402
from riftlord.formats.omd2 import trapgrids  # noqa: E402

#: Half-extent of a trap box on its thin axis -- how thick the permitted slab is.
#:
#: The kit uses 10 everywhere (866 boxes) and the blank-grid instance in Scenario_05
#: uses 32, but a CONVERTED level cannot use 10: its floors do not land at exactly the
#: height OMD2's trap plane implies. Measured on 2,778 floor cells over TheCrunch and
#: TwistedHalls, as the gap between a trap cell and the nearest converted floor
#: beneath it, a half-extent of 10 would admit **8%** of them; 25 admits 43%, 40
#: admits 88% and 50 admits 90%, after which it flattens.
#:
#: No offset is applied along the normal, deliberately. The gap's median is -12.9 on
#: TheCrunch and -32.7 on TwistedHalls, so it is conversion noise rather than a
#: convention, and shifting by either level's median would be fitting to that level;
#: the table shows an offset buys nothing once the slab is thick enough (offset 0 at
#: half-extent 50 gives 90%, offset -15 gives 88%).
#:
#: The remaining 10% are cells whose nearest converted floor is genuinely far away --
#: geometry the conversion has not built yet, not a placement error.
THIN = 50.0

#: `ETrapPlacementFlags` as a bitmask: Floor=1<<0, Wall=1<<1, Ceiling=1<<2, then the
#: WarMachine variants. Measured values, not the enum ordinals.
PLACEMENT = {'Floor': 9, 'Wall': 2, 'Ceiling': 4}

#: The standalone grid actor, and the component inside it that carries the box.
GRID_CLASS = 'BP_OMDTrapGrid_Blank_C'
BOX_COMPONENT = 'OMDTrapGridBox'


def rectangles(mask, width, height):
    """[(col0, row0, col1, row1)] covering every open cell, biggest first.

    The mask is row major, `row * width + col` -- confirmed by drawing volumes out:
    an 8x2 wall reads as one contiguous run and a 4x6 as a vertical strip, where a
    transposed read scrambles both.
    """
    grid = [[mask[row * width + col] == '1' for col in range(width)]
            for row in range(height)]
    out = []
    while True:
        area, c0, r0, c1, r1 = _largest(grid, width, height)
        if area <= 0:
            return out
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                grid[r][c] = False
        out.append((c0, r0, c1, r1))


def _largest(grid, width, height):
    """(area, col0, row0, col1, row1) of the biggest all-open rectangle, inclusive."""
    best = (0, 0, 0, -1, -1)
    heights = [0] * width
    for row in range(height):
        for c in range(width):
            heights[c] = heights[c] + 1 if grid[row][c] else 0
        stack = []
        for c in range(width + 1):
            cur = heights[c] if c < width else 0
            start = c
            while stack and stack[-1][1] >= cur:
                s, hh = stack.pop()
                area = hh * (c - s)
                if area > best[0]:
                    best = (area, s, row - hh + 1, c - 1, row)
                start = s
            stack.append((start, cur))
    return best


def _sub(a, b):
    return tuple(a[k] - b[k] for k in (0, 1, 2))


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v) if n > 1e-9 else (0.0, 0.0, 0.0)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def basis(volume):
    """(along local +x, along local +y, plane normal) as world unit vectors."""
    o = transform.local_to_omd2(volume, 0.0, 0.0)
    u = _unit(_sub(transform.local_to_omd2(volume, 100.0, 0.0), o))
    w = _unit(_sub(transform.local_to_omd2(volume, 0.0, 100.0), o))
    return u, w, _unit(_cross(u, w))


def boxes(volume, shift=(0.0, 0.0, 0.0)):
    """One dict per rectangle: centre, half extents, basis and placement bitmask."""
    width, height, mask = volume['width'], volume['height'], volume['mask']
    if len(mask) != width * height:
        return []
    u, w, n = basis(volume)
    sx, sy = (volume['scale'] or [1.0, 1.0, 1.0])[:2]
    out = []
    for c0, r0, c1, r1 in rectangles(mask, width, height):
        lx0, ly0 = transform.cell_origin(volume, c0, r0)
        lx1, ly1 = transform.cell_origin(volume, c1, r1)
        centre = transform.local_to_omd2(volume, (lx0 + lx1) / 2.0, (ly0 + ly1) / 2.0)
        half_u = (c1 - c0 + 1) * transform.OMD2_CELL * abs(sx) / 2.0
        half_w = (r1 - r0 + 1) * transform.OMD2_CELL * abs(sy) / 2.0
        out.append({
            'centre': tuple(centre[k] + shift[k] for k in (0, 1, 2)),
            'half': (THIN, half_u, half_w),
            'basis': (n, u, w),
            'cells': (c1 - c0 + 1, r1 - r0 + 1),
            'surface': volume['placement_type'],
            'placement': PLACEMENT.get(volume['placement_type'], PLACEMENT['Floor']),
            'volume': volume['name'],
        })
    return out


def plan(scene, shift=(0.0, 0.0, 0.0), zip_path=None, mirror=True):
    """(boxes, report) for a whole scene, mirrored to match the geometry."""
    data = trapgrids.extract(zip_path or trapgrids.DEFAULT_DATA_ZIP, scene)
    out, skipped = [], collections.Counter()
    for v in data['volumes']:
        #: the same handedness change the geometry gets, or the grids land in the
        #: mirror image of the floors they belong to
        if mirror:
            v['position'] = list(transform.mirror_point(v['position']))
            v['orientation'] = list(transform.mirror_orientation(v['orientation'] or
                                                                 [0.0, 0.0, 0.0]))
        got = boxes(v, shift)
        if not got:
            skipped['no open cell' if v['cells_open'] == 0 else 'unusable mask'] += 1
        out.extend(got)
    report = {
        'scene': scene,
        'volumes': len(data['volumes']),
        'open_cells': sum(v['cells_open'] for v in data['volumes']),
        'boxes': len(out),
        'by_surface': collections.Counter(b['surface'] for b in out),
        'skipped': skipped,
    }
    return out, report


def world_aabb(box):
    """The axis-aligned bounds the oriented box occupies, from its basis and extents."""
    lo = [float('inf')] * 3
    hi = [float('-inf')] * 3
    for sa in (-1, 1):
        for sb in (-1, 1):
            for sc in (-1, 1):
                for k in (0, 1, 2):
                    v = (box['centre'][k]
                         + sa * box['half'][0] * box['basis'][0][k]
                         + sb * box['half'][1] * box['basis'][1][k]
                         + sc * box['half'][2] * box['basis'][2][k])
                    lo[k] = min(lo[k], v)
                    hi[k] = max(hi[k], v)
    return tuple(lo), tuple(hi)


def check(scene, zip_path=None, tolerance=0.5):
    """Prove each box covers exactly the cells it was built from. Returns problems.

    The oracle is independent of the box: every open cell of the rectangle has its
    centre computed straight from `cell_origin`/`local_to_omd2`, and must fall inside
    the box's own bounds. A rotation convention got the wrong way round fails this.
    """
    data = trapgrids.extract(zip_path or trapgrids.DEFAULT_DATA_ZIP, scene)
    problems = []
    checked = 0
    for v in data['volumes']:
        width, height, mask = v['width'], v['height'], v['mask']
        if len(mask) != width * height:
            continue
        u, w, n = basis(v)
        for rect, box in zip(rectangles(mask, width, height), boxes(v)):
            c0, r0, c1, r1 = rect
            lo, hi = world_aabb(box)
            for row in range(r0, r1 + 1):
                for col in range(c0, c1 + 1):
                    lx, ly = transform.cell_origin(v, col, row)
                    p = transform.local_to_omd2(v, lx, ly)
                    checked += 1
                    for k in (0, 1, 2):
                        if not (lo[k] - tolerance <= p[k] <= hi[k] + tolerance):
                            problems.append(
                                '%s cell (%d,%d) at %s is outside the box %s..%s on %s'
                                % (v['name'], col, row, [round(c) for c in p],
                                   [round(c) for c in lo], [round(c) for c in hi],
                                   'XYZ'[k]))
                            break
    return problems, checked


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--scene', default='TheCrunch')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)
    got, rep = plan(args.scene)
    print('%s: %d volume(s), %d open cell(s) -> %d trap grid box(es)'
          % (rep['scene'], rep['volumes'], rep['open_cells'], rep['boxes']))
    print('   by surface: %s' % dict(rep['by_surface']))
    if rep['skipped']:
        print('   skipped: %s' % dict(rep['skipped']))
    sizes = collections.Counter(b['cells'] for b in got)
    print('   commonest box sizes (cells): %s' % sizes.most_common(6))
    if args.check:
        problems, checked = check(args.scene)
        print('   checked %d cell centre(s) against their box: %d problem(s)'
              % (checked, len(problems)))
        for p in problems[:8]:
            print('      %s' % p)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
