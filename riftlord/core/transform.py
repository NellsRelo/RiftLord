"""Put an OMD2 trap volume into OMD3's world: position, rotation, scale.

The tiler says which pieces a volume is made of. This says where each one goes,
which is the other half of a conversion and the half with all the conventions in
it.

    python3 transform.py --scene TheEdge
    python3 transform.py --check          # invariants over every OMD2 level

## What OMD2 gives us

A volume is a rectangle of 100-unit cells with a centre, an orientation and a
bitmask. Measured across all 6,405 volumes in the campaign, the orientation is
vForge's **(yaw, pitch, roll)** in degrees, and the roll is what the surface
*is*:

| roll | surface | share |
|---|---|---|
| 0 | Floor | 1,213 of 1,257 |
| 90 | Wall | 3,970 of 4,286 |
| 180 | Ceiling | 747 of 862 |

The rest are genuine slopes -- ramps around 21-23 degrees, angled walls near
124 -- not noise. `position` is the volume's **centre**, established when the
reader was checked against the shipped minimap.

## Getting to Unreal

Two changes, and they are easy to conflate:

* **Scale** is 1.0: both games use a 300-unit trap cell. An earlier version of
  this file said 3.0, from an assumed 100-unit OMD2 cell that was never measured,
  and every converted level came out three times too large.
* **Handedness.** Vision is right-handed and Unreal is left-handed, so one axis
  mirrors. Y is the conventional choice, and mirroring Y negates the yaw.

Then the level is **re-centred**. OMD2 world coordinates run to five figures --
TheEdge sits around x=13,000, which is 40,000 Unreal units after scaling --
while a shipped OMD3 level spans a few thousand about its own origin. A
converted level is given its own origin at the middle of its own bounding box,
so it lands where a map is expected to be rather than far out in the world.
"""

import argparse
import collections
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.mapeditor import tiler  # noqa: E402

#: OMD2's trap cell (uu). Settled 2026-10-17: open cells of flat-floor trap volumes land
#: on collision floor 99.7-100% at 200 on five levels (44-63% at 300), mask bit counts
#: fit (2hw/200)x(2hh/200), and the exe holds 200.0 / 0.005. (The old 300 came from
#: a Passages volume that stops 600 short of each side of its floor.)
OMD2_CELL = 200.0
OMD3_CELL = 300.0
#: OMD2 world units to OMD3's: 1:1 (hero heights 0.94, attack radii median 1.0). The
#: cells differ, the worlds do not; never derive this from the cell sizes.
WORLD_SCALE = 1.0
SCALE = WORLD_SCALE

#: OMD2's geometry kit tiles at 400 units (two OMD2 trap cells) while OMD3's grid is
#: 300, so three floor meshes span four OMD3 cells and the two lattices do not divide.
#: A mesh is therefore 1.333 OMD3 cells and matches no kit footprint -- which is why geometry is
#: emitted by tiling the covered AREA rather than by converting a mesh to a piece.
OMD2_ART_TILE = 400.0

#: What each surface's roll should be, for checking rather than for deciding.
#: The volume already tells us its `placement_type`; this is how we know the
#: orientation convention is being read the right way round.
EXPECTED_ROLL = {'Floor': 0.0, 'Wall': 90.0, 'Ceiling': 180.0}


def _rot_z(x, y, degrees):
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return x * c - y * s, x * s + y * c


def _rot_x(y, z, degrees):
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return y * c - z * s, y * s + z * c


def _rot_y(x, z, degrees):
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return x * c + z * s, -x * s + z * c


#: Vision is right-handed and Unreal is left-handed, so one axis mirrors. Y is the
#: conventional choice here and `to_unreal` has always done it for the OLD
#: volume-driven pipeline. The MESH-DRIVEN pipeline did not, and the result was a map
#: that is a mirror image of OMD2's -- confirmed in game 2026-10-02 from side-by-side
#: screenshots, after an earlier guess that 90-degree stair errors argued against it.
#:
#: Applied to the SOURCE, before anything is planned, so every downstream solve -- run
#: merging, footprint fitting, actor-space offsets, trap grid bases -- works in one
#: consistent right-handed world. Mirroring the output instead would leave each piece
#: offset by its own asymmetry, because an OMD2 mesh is not centred on its origin.
MIRROR_AXIS = 1


def mirror_point(p):
    """A position with the mirrored axis negated."""
    return tuple(-v if k == MIRROR_AXIS else v for k, v in enumerate(p[:3]))


def mirror_orientation(o):
    """vForge (yaw, pitch, roll) through the mirror: only the yaw turns the other way.

    Pitch and roll describe the surface itself and carry across, which is the same rule
    `rotator` states for the old pipeline.
    """
    yaw, pitch, roll = (list(o) + [0.0, 0.0, 0.0])[:3]
    return (-yaw, pitch, roll)


def mirror_matrix(m):
    """S . M . S for a row-major 4x4, which keeps it a rotation rather than a reflection.

    Negating only the translation would mirror positions but leave every piece facing
    the way it did, so a stair or an archway would point into the wall it came from.
    """
    s = [1.0, 1.0, 1.0, 1.0]
    s[MIRROR_AXIS] = -1.0
    return [[m[r][c] * s[r] * s[c] for c in range(4)] for r in range(4)]


def mirror_instance(inst):
    """One OMD2 mesh instance, mirrored in place. Returns it for convenience."""
    if inst.get('matrix'):
        inst['matrix'] = mirror_matrix(inst['matrix'])
    if inst.get('position'):
        inst['position'] = list(mirror_point(inst['position']))
    if inst.get('orientation'):
        inst['orientation'] = list(mirror_orientation(inst['orientation']))
    return inst


def local_to_omd2(volume, lx, ly):
    """A point on the volume's own plane, in OMD2 world coordinates.

    The plane is the volume's XY before rolling: the grid is laid out flat, then
    rolled about X to stand it up (or flip it), pitched, and finally yawed about
    the world's vertical.
    """
    yaw, pitch, roll = (volume['orientation'] or [0.0, 0.0, 0.0])[:3]
    x, y, z = lx, ly, 0.0
    y, z = _rot_x(y, z, roll)
    x, z = _rot_y(x, z, pitch)
    x, y = _rot_z(x, y, yaw)
    px, py, pz = volume['position']
    return px + x, py + y, pz + z


def to_unreal(x, y, z, origin=(0.0, 0.0, 0.0)):
    """OMD2 world -> Unreal world: scale, mirror Y, re-centre."""
    return ((x - origin[0]) * SCALE,
            -(y - origin[1]) * SCALE,
            (z - origin[2]) * SCALE)


def rotator(volume):
    """The Unreal (pitch, yaw, roll) for a piece lying on this volume.

    Mirroring Y flips the direction of rotation about the vertical, so the yaw
    negates. Pitch and roll describe the surface itself and carry across.
    """
    yaw, pitch, roll = (volume['orientation'] or [0.0, 0.0, 0.0])[:3]
    return (pitch, -yaw, roll)


def cell_origin(volume, col, row):
    """The centre of one cell of the grid, on the volume's own plane."""
    w, h = volume['width'], volume['height']
    sx, sy = (volume['scale'] or [1.0, 1.0, 1.0])[:2]
    lx = (col - (w - 1) / 2.0) * OMD2_CELL * sx
    ly = (row - (h - 1) / 2.0) * OMD2_CELL * sy
    return lx, ly


def piece_placements(volume, kit=None, origin=(0.0, 0.0, 0.0)):
    """Every kit piece this volume becomes, with an Unreal transform each.

    A tile covers cells [x, x+w) x [y, y+h) of the grid. Its centre is the
    midpoint of that block, which is what Unreal wants because a kit piece's own
    origin is a corner -- so the corner offset is applied on the piece's own
    plane before the volume's rotation carries it into the world.
    """
    kit = kit or tiler.catalogue()
    #: THE BITMASK IS HONOURED HERE. An OMD3 trap grid box has no per-cell mask --
    #: `UOMDTrapGridBoxComponent` records which KINDS of trap a box takes, not which
    #: cells -- so the only way to deny a cell is to leave it outside every box. The
    #: pieces therefore cover the OPEN cells only, and the closed ones (16% of
    #: TheCrunch's 8,574, usually stairs, the square behind a door, the ring round a
    #: spawner) are left for grid-less geometry to stand on.
    plan, why = tiler.plan(volume['placement_type'], volume['width'],
                           volume['height'], kit, mask=volume.get('mask'))
    if plan is None:
        return [], why
    out = []
    for w, h, x, y, name in plan:
        # midpoint of the covered block, in cells, then on the plane
        lx0, ly0 = cell_origin(volume, x, y)
        lx1, ly1 = cell_origin(volume, x + w - 1, y + h - 1)
        cx, cy = (lx0 + lx1) / 2.0, (ly0 + ly1) / 2.0
        wx, wy, wz = local_to_omd2(volume, cx, cy)
        out.append({'piece': name, 'cells': (w, h),
                    'location': to_unreal(wx, wy, wz, origin),
                    'rotation': rotator(volume),
                    'surface': volume['placement_type']})
    return out, None


def filler_placements(volume, kit=None, origin=(0.0, 0.0, 0.0)):
    """The grid-less meshes that fill the cells OMD2 SHUT, with a transform each.

    The other half of the bitmask. `piece_placements` covers the open cells with kit
    pieces, whose trap grids then land exactly where a trap is allowed; this covers
    what is left, with plain meshes that carry no grid at all.

    Each entry is one COMPONENT of a cheap kit piece, so the transform is the tile's
    own placement composed with the component's offset inside its Blueprint. That
    composition is why this returns a full rotator and a scale rather than a yaw: a
    component sits at an angle inside its piece, and `riftlord.apply.transform_of`
    turns the composed matrix back into something `place` can write.
    """
    kit = kit or tiler.catalogue()
    plan, why = tiler.plan_filler(volume['placement_type'], volume['width'],
                                  volume['height'], volume.get('mask'), kit)
    if plan is None:
        return [], why
    from riftlord.formats.omd3 import omd3level
    from riftlord.build import apply as _apply
    out = []
    for w, h, x, y, piece, parts in plan:
        lx0, ly0 = cell_origin(volume, x, y)
        lx1, ly1 = cell_origin(volume, x + w - 1, y + h - 1)
        cx, cy = (lx0 + lx1) / 2.0, (ly0 + ly1) / 2.0
        wx, wy, wz = local_to_omd2(volume, cx, cy)
        base = omd3level._matrix(to_unreal(wx, wy, wz, origin), rotator(volume),
                                 (1.0, 1.0, 1.0))
        for part in parts:
            local = omd3level._matrix(part.get('loc') or (0.0, 0.0, 0.0),
                                      part.get('rot') or (0.0, 0.0, 0.0),
                                      part.get('scale') or (1.0, 1.0, 1.0))
            flat = omd3level._flat(omd3level._matmul(base, local))
            loc, rot, scale = _apply.transform_of(flat)
            out.append({'mesh': part['mesh'], 'piece': piece, 'cells': (w, h),
                        'location': loc, 'rotation': rot, 'scale': scale,
                        'surface': volume['placement_type'], 'filler': True})
    return out, None


def level_origin(volumes):
    """The middle of a level's own bounding box, in OMD2 coordinates."""
    xs = [v['position'][0] for v in volumes]
    ys = [v['position'][1] for v in volumes]
    zs = [v['position'][2] for v in volumes]
    if not xs:
        return (0.0, 0.0, 0.0)
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0, min(zs))


def convert(volumes, kit=None):
    """Every placement for a level, re-centred on its own bounding box."""
    kit = kit or tiler.catalogue()
    origin = level_origin(volumes)
    placed, failed = [], []
    for v in volumes:
        got, why = piece_placements(v, kit, origin)
        if why:
            failed.append((v, why))
        placed.extend(got)
    return placed, failed, origin


# -- checking ---------------------------------------------------------------

def invariants(volumes, kit=None, tolerance=1.0):
    """Complaints about a conversion. Empty means the maths holds together.

    The useful test is a **round trip**, not a guess at what angles look right.
    An earlier version asserted that floors are flat and walls upright, and it
    complained about angled walls at 143 degrees and ramps -- which are real
    content, not errors -- while catching nothing that was actually wrong.

    So instead: take each piece's world position, undo the whole transform, and
    check it lands back on the cell-block centre it came from. That exercises the
    rotation order, the axis mirror, the scale and the re-centring together, and
    it fails if any one of them is wrong.
    """
    kit = kit or tiler.catalogue()
    origin = level_origin(volumes)
    bad = []
    for v in volumes:
        plan, why = tiler.plan(v['placement_type'], v['width'], v['height'], kit)
        if plan is None:
            continue
        yaw, pitch, roll = (v['orientation'] or [0.0, 0.0, 0.0])[:3]
        for w, h, x, y, _name in plan:
            lx0, ly0 = cell_origin(v, x, y)
            lx1, ly1 = cell_origin(v, x + w - 1, y + h - 1)
            want = ((lx0 + lx1) / 2.0, (ly0 + ly1) / 2.0)

            wx, wy, wz = local_to_omd2(v, *want)
            ux, uy, uz = to_unreal(wx, wy, wz, origin)

            # undo: re-centre, mirror, scale, then the rotations in reverse
            bx = ux / SCALE + origin[0]
            by = -uy / SCALE + origin[1]
            bz = uz / SCALE + origin[2]
            bx -= v['position'][0]
            by -= v['position'][1]
            bz -= v['position'][2]
            bx, by = _rot_z(bx, by, -yaw)
            bx, bz = _rot_y(bx, bz, -pitch)
            by, bz = _rot_x(by, bz, -roll)

            # Scale is deliberately checked separately. A round trip divides by
            # the same constant it multiplied by, so it is blind to the scale
            # being wrong -- proven by setting it to 2 and watching the trip
            # still pass. The independent fact is that one OMD2 cell must come
            # out as one OMD3 cell, which is 300 Unreal units.
            if v['width'] >= 2:
                a = to_unreal(*local_to_omd2(v, *cell_origin(v, 0, 0)), origin)
                b = to_unreal(*local_to_omd2(v, *cell_origin(v, 1, 0)), origin)
                step = math.dist(a, b)
                if abs(step - OMD3_CELL) > 1.0:
                    bad.append('adjacent cells are %.1f apart in Unreal units, '
                               'expected %.0f' % (step, OMD3_CELL))
                    break

            if (abs(bx - want[0]) > tolerance or abs(by - want[1]) > tolerance
                    or abs(bz) > tolerance):
                bad.append('%s %dx%d: a piece round-tripped to (%.1f, %.1f, %.1f), '
                           'expected (%.1f, %.1f, 0)'
                           % (v['placement_type'], v['width'], v['height'],
                              bx, by, bz, want[0], want[1]))
                break
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--scene')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--limit', type=int, default=8)
    args = ap.parse_args(argv)
    from riftlord.formats.omd2 import trapgrids

    zip_path, scenes = tiler.omd2_scenes()
    kit = tiler.catalogue()

    if args.check:
        worst = []
        for scene in scenes:
            try:
                data = trapgrids.extract(zip_path, scene)
            except Exception:                        # noqa: BLE001
                continue
            placed, failed, origin = convert(data['volumes'], kit)
            bad = invariants(data['volumes'], kit)
            xs = [p['location'][0] for p in placed] or [0]
            ys = [p['location'][1] for p in placed] or [0]
            zs = [p['location'][2] for p in placed] or [0]
            print('%-28s %5d piece(s)  extent %6.0f x %6.0f x %6.0f  %s'
                  % (scene, len(placed), max(xs) - min(xs), max(ys) - min(ys),
                     max(zs) - min(zs),
                     ('%d volume(s) untiled' % len(failed)) if failed else ''))
            worst.extend(bad)
        print('\ninvariant complaints: %d' % len(worst))
        for b in worst[:6]:
            print('   ' + b)
        return 1 if worst else 0

    if args.scene:
        data = trapgrids.extract(zip_path, args.scene)
        placed, failed, origin = convert(data['volumes'], kit)
        print('%s: %d volume(s) -> %d piece(s), origin %s'
              % (args.scene, len(data['volumes']), len(placed),
                 tuple(round(o) for o in origin)))
        for p in placed[:args.limit]:
            print('   %-28s %-8s loc=(%8.0f,%8.0f,%7.0f) rot=(%5.0f,%5.0f,%5.0f)'
                  % (p['piece'], p['surface'], *p['location'], *p['rotation']))
        for v, why in failed[:3]:
            print('   untiled: %s %dx%d -- %s' % (v['placement_type'],
                  v['width'], v['height'], why))
        return 0

    ap.error('need --scene NAME or --check')


if __name__ == '__main__':
    raise SystemExit(main())
