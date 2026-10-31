"""Turn a fitted run into a placed OMD3 static mesh: location, rotation, scale.

    python3 emitgeo.py --selftest
    python3 emitgeo.py --scene TheCrunch --limit 20

**A piece's pivot is not its corner and not its centre.** The catalogue records each
mesh's bounding box as a centre `origin` and a half-size `extent`, both in the mesh's
own space, and they differ per piece: `Floor_Squarebrick_4x4` has origin
(599.97, -600.24, 1.24) against extent (603.76, 603.00, 18.06), so its pivot sits near
the min-x corner but the MAX-y one. There is no "+ half the size" rule -- the offset has
to be read per piece, and a rotation permutes which extent axis answers which world
axis.

So the placement is solved rather than guessed. The piece's world box centre must land
on the target box centre:

    centre = location + R . (scale * origin)   =>   location = centre - R . (scale * origin)

`check` then measures the box the placement actually produces and compares it with the
box that was asked for. That round trip is the instrument: a placement that reproduces
its target box to within a unit is right regardless of which corner the artist chose as
the pivot, and every anchoring bug shows up as a box in the wrong place rather than as
something subtly off in a level nobody has loaded yet.
"""

import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.formats.omd3 import meshcat

#: How far a placed box may sit from the box that was asked for, in uu. One unit: the
#: catalogue's own numbers carry two decimals, so anything larger is a real error.
TOLERANCE = 1.0


def rot_z(deg):
    """The 3x3 for a yaw, which is the only rotation an axis-aligned kit piece needs."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]


def apply(rot, v):
    return [sum(rot[a][b] * v[b] for b in (0, 1, 2)) for a in (0, 1, 2)]


def thin_axis(extent):
    """Which local axis a piece is flat on: the smallest half-extent."""
    return min((0, 1, 2), key=lambda a: abs(extent[a]))


def yaw_for(extent, normal):
    """The yaw that turns a piece's thin axis onto the world `normal` axis.

    A floor or ceiling is thin on z and needs no turn, whatever the wall normal is. A
    wall piece is authored thin on one horizontal axis -- `Wall_Sec_2_3_V2_RB` is
    (450, 50.69, 300), thin on y -- so facing it along x is a quarter turn.
    """
    thin = thin_axis(extent)
    if thin == 2 or normal == 2:
        #: nothing a yaw can do about a flat piece, and nothing one needs to
        return 0.0
    if thin == normal:
        return 0.0
    return 90.0


def place_at(piece, target_min, target_size, yaw, scale=(1.0, 1.0, 1.0), cat=None):
    """Like `place`, but the yaw is GIVEN rather than inferred from the thin axis.

    Inferring it fails on a lintel. `Archway_1x1x3_Short_RB` is 900 x 316 x 300, so its
    thinnest axis is its HEIGHT, not the direction it faces -- `thin_axis` called it a
    flat piece, left it unrotated, and laid a 900uu arch across a 507uu-deep opening.
    A kit piece is authored upright, so z is always height and the only question is
    which way it is turned; a caller that knows the target's shape knows that already.
    """
    cat = cat or meshcat.load()
    info = cat.get(piece)
    if not info:
        return None
    rot = rot_z(yaw)
    centre = [target_min[a] + target_size[a] / 2.0 for a in (0, 1, 2)]
    scaled = [info['origin'][a] * scale[a] for a in (0, 1, 2)]
    loc = [centre[a] - apply(rot, scaled)[a] for a in (0, 1, 2)]
    return {'mesh': piece, 'path': info.get('path'), 'location': loc,
            'rotation': [0.0, yaw, 0.0], 'scale': list(scale),
            'box': world_box(info, loc, yaw, scale)}


def place(piece, target_min, target_size, normal, scale=(1.0, 1.0, 1.0), cat=None):
    """{mesh, location, rotation, scale, box} putting `piece` on the target box.

    `target_min` and `target_size` are world-space; `normal` is the axis the piece's
    flat side faces, which decides the yaw.
    """
    cat = cat or meshcat.load()
    info = cat.get(piece)
    if not info:
        return None
    origin = info['origin']
    extent = info['extent']
    yaw = yaw_for(extent, normal)
    rot = rot_z(yaw)
    centre = [target_min[a] + target_size[a] / 2.0 for a in (0, 1, 2)]
    scaled = [origin[a] * scale[a] for a in (0, 1, 2)]
    loc = [centre[a] - apply(rot, scaled)[a] for a in (0, 1, 2)]
    return {'mesh': piece, 'path': info.get('path'), 'location': loc,
            'rotation': [0.0, yaw, 0.0], 'scale': list(scale),
            'box': world_box(info, loc, yaw, scale)}


def world_box(info, location, yaw, scale):
    """(min, max) of a placed piece in world space -- what the game will actually show."""
    rot = rot_z(yaw)
    origin = [info['origin'][a] * scale[a] for a in (0, 1, 2)]
    extent = [info['extent'][a] * scale[a] for a in (0, 1, 2)]
    centre = [location[a] + apply(rot, origin)[a] for a in (0, 1, 2)]
    reach = [sum(abs(rot[a][b]) * abs(extent[b]) for b in (0, 1, 2)) for a in (0, 1, 2)]
    return ([centre[a] - reach[a] for a in (0, 1, 2)],
            [centre[a] + reach[a] for a in (0, 1, 2)])


def check(placement, target_min, target_size, tol=TOLERANCE):
    """How far a placement's real box is from the box it was asked for, per axis."""
    lo, hi = placement['box']
    want_lo = list(target_min)
    want_hi = [target_min[a] + target_size[a] for a in (0, 1, 2)]
    return [max(abs(lo[a] - want_lo[a]), abs(hi[a] - want_hi[a])) for a in (0, 1, 2)]


def fit_scale(piece, target_size, normal, cat=None):
    """The per-axis scale that makes `piece` exactly fill `target_size`.

    Only the axes the target constrains are scaled: a wall's thickness and a floor's
    depth are left at 1.0, because squashing a 100uu-thick OMD2 slab onto an 18uu OMD3
    one would thin the art for no visible gain, and the surplus is buried either way.
    """
    cat = cat or meshcat.load()
    info = cat.get(piece)
    if not info:
        return None
    yaw = yaw_for(info['extent'], normal)
    rot = rot_z(yaw)
    size = [2.0 * sum(abs(rot[a][b]) * abs(info['extent'][b]) for b in (0, 1, 2))
            for a in (0, 1, 2)]
    out = []
    for a in (0, 1, 2):
        if a == normal or not target_size[a] or not size[a]:
            out.append(1.0)
        else:
            out.append(target_size[a] / size[a])
    return out


def selftest():
    """Round-trip every catalogue family member: placed box == requested box."""
    bad = []
    cat = meshcat.load()
    names = [n for n in ('Floor_Squarebrick_4x4', 'Floor_Squarebrick_1x2',
                         'Wall_Sec_2_3_V2_RB', 'Wall_1_2_FB', 'Ceiling_4x4',
                         'WallTrim_Top_1_4', 'RailingA', 'ColumnTrim_CastStone_600W')
             if n in cat]
    if len(names) < 6:
        bad.append('catalogue is missing the pieces the round trip is built on')
    for name in names:
        info = cat[name]
        for normal in (0, 1, 2):
            yaw = yaw_for(info['extent'], normal)
            rot = rot_z(yaw)
            size = [2.0 * sum(abs(rot[a][b]) * abs(info['extent'][b])
                              for b in (0, 1, 2)) for a in (0, 1, 2)]
            at = [1234.5, -678.25, 300.0]
            p = place(name, at, size, normal, cat=cat)
            off = check(p, at, size)
            if max(off) > TOLERANCE:
                bad.append('%s normal=%d placed %.2fuu from its target box'
                           % (name, normal, max(off)))
    #: an explicit yaw must round-trip too, including the quarter turn
    for name in names:
        info = cat[name]
        for yaw in (0.0, 90.0, 180.0, 270.0):
            rot = rot_z(yaw)
            size = [2.0 * sum(abs(rot[a][b]) * abs(info['extent'][b])
                              for b in (0, 1, 2)) for a in (0, 1, 2)]
            at = [-500.0, 250.0, 75.0]
            p = place_at(name, at, size, yaw, cat=cat)
            off = check(p, at, size)
            if max(off) > TOLERANCE:
                bad.append('%s at yaw %.0f placed %.2fuu from its target box'
                           % (name, yaw, max(off)))
    #: and with a scale, which is where an unscaled pivot offset would show
    for name in names:
        info = cat[name]
        normal = thin_axis(info['extent'])
        want = [1200.0, 1200.0, 1200.0]
        want[normal] = 2.0 * abs(info['extent'][normal])
        scale = fit_scale(name, want, normal, cat)
        p = place(name, [0.0, 0.0, 0.0], want, normal, scale, cat)
        off = check(p, [0.0, 0.0, 0.0], want)
        if max(off) > TOLERANCE:
            bad.append('%s scaled to %s placed %.2fuu from its target box'
                       % (name, [round(v, 2) for v in scale], max(off)))
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--piece', default='Floor_Squarebrick_4x4')
    args = ap.parse_args(argv)
    if args.selftest:
        bad = selftest()
        for b in bad:
            print('FAILED  %s' % b)
        print('emitgeo selftest: %s'
              % ('passed' if not bad else '%d problem(s)' % len(bad)))
        return 1 if bad else 0
    cat = meshcat.load()
    info = cat.get(args.piece)
    if not info:
        print('no such piece: %s' % args.piece)
        return 1
    print('%s  origin %s  extent %s' % (args.piece, info['origin'], info['extent']))
    for normal in (0, 1, 2):
        size = [1200.0, 1200.0, 1200.0]
        size[normal] = 2.0 * abs(info['extent'][thin_axis(info['extent'])])
        p = place(args.piece, [0.0, 0.0, 0.0], size, normal, cat=cat)
        print('  normal=%d yaw=%.0f location %s  box off by %s'
              % (normal, p['rotation'][1], [round(v, 2) for v in p['location']],
                 [round(v, 2) for v in check(p, [0.0, 0.0, 0.0], size)]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
