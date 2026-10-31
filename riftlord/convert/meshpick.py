"""Choose the OMD3 mesh for a merged OMD2 run, and decide scale against overhang.

    python3 meshpick.py --scene TheCrunch
    python3 meshpick.py --selftest

`artgroup` merges OMD2 instances into runs; this turns one run into the OMD3 static
meshes that cover it. The appearance decision was made by a human (`mesh-map.json`
names a FAMILY per OMD2 mesh, so stone stays stone and wood stays wood); the size
decision is arithmetic and lives here.

**One axis already matches and the other never will.** Measured across every mapped
OMD2 mesh in TheCrunch: the horizontal art lattice is 400uu, which is 1.33 OMD3 cells,
but the vertical dimensions are 600uu for a wall, 300 for a half wall and 100 for a
floor slab -- exact multiples of the 300uu cell. So heights are reproduced exactly and
only the horizontal axes need fitting. That split is why a converted level can have
every floor and every wall-top at a correct z even though no horizontal seam lines up.

**Fitting policy**, from the reviewer's rule that overhang is right where the player
cannot see it and scaling is right where they can:

* An axis that comes out to a whole number of cells is placed at scale 1.0. Every
  vertical axis does, as does any horizontal run of 3, 6, 9... tiles (3 x 400 = 4 x 300).
* Otherwise the run is covered by `round(length / 300)` cells and everything on that
  axis is scaled by one shared factor, so the pieces stay flush with each other and the
  run keeps the exact extent OMD2 gave it. The factor is worst for a single tile (1.33)
  and falls away fast: 0.89 at two tiles, 1.07 at four, 0.97 at five, 1.0 at six.
* When that factor would exceed `SCALE_LIMIT` the run is covered at scale 1.0 instead
  and the surplus becomes overhang, but only for a role whose surplus is hidden -- a
  wall backs into solid, a ceiling into the space above the room. A floor is walked on
  and in full view, so a floor is always scaled and never overhung.

**A family name in the map is a prefix, not a catalogue key.** The reviewer wrote
`Railing`, `Archway` and `Cren`; the catalogue holds `RailingA`, `RailingB`,
`RailingC_3W`, `Archway_Pointed_B_SM`, `Crenellation_1_5_RB`. Members are gathered by
prefix and then sorted by footprint, which is also what makes "any of the archways,
match on shape and size" executable.
"""

import argparse
import collections
import json
import os
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.formats.omd3 import meshcat

#: OMD3's trap cell, measured. Every catalogue footprint is quoted in these.
CELL = 300.0

#: How far a shared scale factor may stray from 1.0 before overhang is preferred for a
#: role that can hide it. 0.15 keeps the 2-tile case (0.89) and the 4-tile case (1.07)
#: scaled, and sends the single-tile case (1.33) to overhang.
SCALE_LIMIT = 0.15

#: Footprint sizes are rounded to this before they are compared. The catalogue measures
#: nominally identical pieces slightly differently -- the same 600uu wall depth appears
#: as 600.0, 600.04 and 600.1, and 300uu as 300.02 and 300.6 -- so an exact comparison
#: treats one size as three. That split a wall family's widths across three buckets,
#: left one width per depth, and emitted a single piece stretched 1.33x where two
#: unscaled pieces fitted.
SIZE_BUCKET = 5.0

#: Within this much of a perfect fit, place at scale 1.0 and let the difference be
#: overhang. OMD3's own kit is not exact -- `Floor_Squarebrick_4x4` measures 1206uu for
#: a nominal 4 x 300 -- and vanilla levels overlap freely, so insisting on an exact fit
#: writes a 0.5% scale onto every piece for nothing.
EXACT_ENOUGH = 0.02

#: How far a piece may reach past the run it fills, as a multiple of the run's own
#: length on that axis. Overhang used to be unbounded AND free -- `fit_axis` returned
#: scale 1.0 for it whatever the surplus -- so the band search actively PREFERRED the
#: most absurd option: it laid a 2,400uu `WallTrim_Top_1_8` across a 105uu-deep trim run
#: and emitted that piece 7,020 times in one level. At 1.0 a piece may at most double
#: the run, which still admits the case worth having (a 600uu wall piece on a 400uu
#: tile, 200 of hidden surplus) and rejects reaching twenty times past the target.
#: How far a piece may hang past the run it covers, as a fraction of its own size.
#:
#: MEASURED against `fidelity.py`, which counts the cells where our geometry sits and
#: OMD2's does not -- the thing that reads in game as walls jutting out and slabs in the
#: sky. On TheCrunch, with everything else fixed:
#:
#:     overhang   covered   missing   INVENTED
#:     1.00        73050     14418      17309
#:     0.50        73070     14398      17451
#:     0.25        71369     16099      14154
#:     0.00        71368     16100      14371
#:
#: 0.25 WAS WRONG AND BROKE THE MAP. Judged on invented cells alone a quarter looked
#: optimal, but invented cells are not the only cost: at 0.25 a 600uu wall can no longer
#: cover a 400uu run (200 of overhang is a third of itself), so the fitter drops to
#: smaller pieces and `Wall_1_2_FB` -- the most-placed piece in the level and its biggest
#: trappable Blueprint class -- collapsed from **1,028 to 352**, taking 739 trappable
#: Blueprints with it. In game that read as the walls disappearing.
#:
#: Measured across the whole trade rather than one number:
#:
#:     overhang  pieces  trappable BPs  Wall_1_2_FB  invented  missing
#:     0.25        8755           2342          352     15310    15733
#:     0.60        9514           3075          976     18843    13899
#:     1.00        9532           3081         1028     18627    13942
#:
#: The cliff is between 0.40 and 0.60. At 0.6 the big walls come back and trappability
#: is BETTER than it ever was (3,075 against 2,858 before any of this work), while
#: invented geometry stays far below where it started (18,843 against 26,723).
#:
#: The lesson is the tuning method, not the value: a constant swept against ONE metric
#: will find the optimum of that metric and wreck whatever it does not measure.
OVERHANG_MAX = 0.6

#: Roles whose surplus geometry is out of sight: a wall's extra length goes into the
#: adjoining wall or the rock behind it, a ceiling's goes above the room, a trim's into
#: the wall it sits on. A floor is absent on purpose -- see the module docstring.
HIDES_OVERHANG = frozenset(('wall', 'ceiling', 'trim', 'arch'))

#: Roles placed as one object per run rather than as a tiled surface. Measured: these
#: are 91%, 74% and 67% single-tile runs in TheCrunch because each instance IS one
#: object -- a cornerstone is a column, not a patch of floor.
SINGLE_OBJECT = frozenset(('column', 'arch', 'railing'))


def inventory(family, cat=None, category=None):
    """Catalogue members of a family, with footprints in cells.

    A family name reaches its members **two different ways**, and both are needed:

    * as a prefix -- the reviewer wrote `Railing`, `Archway` and `Cren`, and the
      catalogue holds `RailingA`, `Archway_Pointed_B_SM`, `Crenellation_1_5_RB`.
    * as a prefix **plus a tail**, because the size can sit in the MIDDLE of the name:
      the family `Wall_Sec_V2_RB` is built from `Wall_Sec_1_2_V2_RB`, so a prefix test
      alone matches nothing and the family silently emits no geometry at all.

    `meshmatch.families` already groups the catalogue the second way, so the exact key
    is looked up there and the prefix match is unioned in.
    """
    from riftlord.convert import meshmatch
    cat = cat or meshcat.load()
    out = []
    low = family.lower()
    exact = set()
    for name, _cells in (meshmatch.families(cat, categories=None).get(family) or ()):
        exact.add(name)
    for name, info in cat.items():
        if name not in exact and not name.lower().startswith(low):
            continue
        if category and info.get('category') != category:
            continue
        cells = info.get('cells')
        if not cells:
            continue
        out.append((name, tuple(cells), info))
    out.sort(key=lambda t: (-t[1][0] * t[1][1], t[0]))
    return out


def fit_axis(length, options, limit=SCALE_LIMIT, may_overhang=False,
             overhang_max=OVERHANG_MAX):
    """Cover `length` uu with whole pieces from `options` (sizes in uu), largest first.

    Returns (parts, scale), where `parts` are the piece sizes in order and `scale` is
    the one factor every part on this axis takes. `scale` is exactly 1.0 whenever the
    pieces already sum to the length, which is every vertical axis. `fit_overhang`
    gives the surplus the same call produced, because a caller choosing between fits
    has to be able to see it -- see `OVERHANG_MAX`.
    """
    fit_axis.last_overhang = 0.0
    if length <= 0 or not options:
        return [], 1.0
    sizes = sorted({round(o, 3) for o in options if o > 1.0}, reverse=True)
    if not sizes:
        return [], 1.0

    def greedy(target):
        parts, left = [], target
        for s in sizes:
            while left >= s - 0.5:
                parts.append(s)
                left -= s
        return parts, left

    #: exact first: if whole pieces land on the length, nothing is scaled
    parts, left = greedy(length)
    if parts and abs(left) < 0.5:
        return parts, 1.0

    #: otherwise aim at the nearest whole covering and share one factor across it
    best = None
    for extra in (0, 1):
        cand, _ = greedy(length + extra * sizes[-1])
        if not cand:
            continue
        span = sum(cand)
        scale = length / span
        if best is None or abs(scale - 1.0) < abs(best[1] - 1.0):
            best = (cand, scale)
    if best is None:
        return [sizes[-1]], length / sizes[-1]
    cand, scale = best
    if abs(scale - 1.0) <= EXACT_ENOUGH:
        return cand, 1.0
    if abs(scale - 1.0) > limit and may_overhang:
        #: keep the art undistorted and let the surplus run past the end of the run,
        #: but only as far as OVERHANG_MAX allows -- otherwise scale after all
        over, _ = greedy(length)
        if not over:
            over = [sizes[-1]]
        if sum(over) < length:
            over.append(sizes[-1])
        surplus = sum(over) - length
        if surplus <= length * overhang_max:
            fit_axis.last_overhang = max(surplus, 0.0)
            return over, 1.0
    fit_axis.last_overhang = max(sum(cand) * scale - length, 0.0)
    return cand, scale


def selftest():
    """Check the arithmetic against the OMD2 dimensions measured from TheCrunch."""
    bad = []
    #: a wall is 600uu tall, which is exactly two 300 cells -- must not be scaled
    parts, scale = fit_axis(600.0, [600.0, 300.0])
    if scale != 1.0:
        bad.append('600uu wall height scaled by %.3f, should be exact' % scale)
    #: three OMD2 tiles are exactly four OMD3 cells
    parts, scale = fit_axis(1200.0, [1200.0, 900.0, 600.0, 300.0])
    if scale != 1.0 or sum(parts) != 1200.0:
        bad.append('3-tile run (1200uu) scaled by %.3f' % scale)
    #: one OMD2 tile against cells: 400/300 -> either 1.33 scale or overhang
    parts, scale = fit_axis(400.0, [600.0, 300.0], may_overhang=False)
    if abs(scale - 400.0 / 300.0) > 1e-6 or parts != [300.0]:
        bad.append('single tile gave parts=%s scale=%.3f' % (parts, scale))
    parts, scale = fit_axis(400.0, [600.0, 300.0], may_overhang=True)
    if scale != 1.0 or sum(parts) < 400.0:
        bad.append('single tile with overhang gave parts=%s scale=%.3f' % (parts, scale))
    #: two tiles = 800uu -> three cells at 0.889, inside the limit, so still scaled
    parts, scale = fit_axis(800.0, [600.0, 300.0], may_overhang=True)
    if abs(scale - 800.0 / 900.0) > 1e-6:
        bad.append('two tiles gave scale=%.3f, expected 0.889' % scale)
    #: an overhang bigger than the run itself is refused, however undistorted it is:
    #: this is the 2,400uu trim on a 105uu run that emitted one mesh 7,020 times
    parts, scale = fit_axis(105.0, [2400.0], may_overhang=True)
    placed = sum(parts) * scale
    if placed > 105.0 * (1.0 + OVERHANG_MAX) + 1.0:
        bad.append('a %gx overhang was accepted on a 105uu run' % (placed / 105.0))
    #: but the useful case survives: a 600uu piece on a 400uu tile, hidden in solid
    parts, scale = fit_axis(400.0, [600.0], may_overhang=True)
    if scale != 1.0 or parts != [600.0]:
        bad.append('a 200uu hidden overhang was refused: parts=%s scale=%.3f'
                   % (parts, scale))
    #: a fit this close is left alone rather than scaled
    parts, scale = fit_axis(1200.0, [1206.0])
    if scale != 1.0:
        bad.append('a 0.5%% misfit was scaled by %.4f instead of left alone' % scale)
    #: a covering never leaves a gap
    for length in (300.0, 400.0, 800.0, 1200.0, 2000.0, 4000.0):
        for over in (False, True):
            parts, scale = fit_axis(length, [1200.0, 900.0, 600.0, 300.0],
                                    may_overhang=over)
            got = sum(parts) * scale
            if got + 0.6 < length:
                bad.append('gap: %.0fuu covered only %.1f (overhang=%s)'
                           % (length, got, over))
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--family', action='append', default=[])
    args = ap.parse_args(argv)
    if args.selftest:
        bad = selftest()
        for b in bad:
            print('FAILED  %s' % b)
        print('meshpick selftest: %s' % ('passed' if not bad else '%d problem(s)' % len(bad)))
        return 1 if bad else 0
    cat = meshcat.load()
    fams = args.family or sorted({v['family'] for v in
                                  json.load(open(gamepath.data('mesh-map.json')))['map'].values()
                                  if v.get('family')})
    for f in fams:
        members = inventory(f, cat)
        print('%-28s %d member(s)' % (f, len(members)))
        for name, cells, _ in members[:6]:
            print('      %-42s %.2f x %.2f x %.2f cells' % ((name,) + cells))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


def footprints(members, normal, bucket=SIZE_BUCKET):
    """{(size along u, size along v): [(member, yaw)]} for a family, in world uu.

    `u` and `v` are the axes a run spans, in `artgroup`'s order: for a floor or ceiling
    (normal z) they are x and y, and for a wall (normal x or y) `u` is the remaining
    horizontal and `v` is **z**.

    **A quarter turn is only free on a horizontal plane.** Offering both orders of a
    piece's two in-plane sizes regardless of the plane was wrong twice over: on a wall
    it proposes swapping a piece's width with its HEIGHT, which is a roll and not a yaw,
    and nothing downstream applied the turn at all -- so `plan_run` picked
    `Ceiling_1x4` for a 1200 x 400 run believing it could lay its 1200 side along u,
    and emission left it unturned and scaled it 4x on one axis to compensate. Each
    footprint now carries the yaw that realises it, and only a flat piece is offered
    turned.
    """
    from riftlord.convert import emitgeo
    out = collections.defaultdict(list)
    for name, cells, info in members:
        extent = info['extent']
        full = [2.0 * abs(extent[a]) for a in (0, 1, 2)]
        if normal == 2:
            #: flat: both horizontals span the plane and a yaw swaps them
            pairs = ((full[0], full[1], 0.0), (full[1], full[0], 90.0))
        else:
            #: Upright, and there is exactly ONE right orientation: the piece's thin
            #: horizontal faces along the run's normal and its long horizontal spans the
            #: run, with z staying on v.
            #:
            #: Offering the other turn as well put a wall's THICKNESS forward as a span.
            #: `Wall_Sec_2_2_V2_RB` is 100uu thick, four of those fill a 400uu tile
            #: exactly, so an edge-on tiling scored a perfect 1.0 and beat the single
            #: correct piece -- a wall built from four slices seen end-on.
            thin_h = 0 if full[0] <= full[1] else 1
            long_h = 1 - thin_h
            #: u is the in-plane horizontal: y when the wall faces x, x when it faces y
            u_axis = 1 if normal == 0 else 0
            yaw = 0.0 if long_h == u_axis else 90.0
            pairs = ((full[long_h], full[2], yaw),)
        for su, sv, yaw in pairs:
            if min(su, sv) <= 1.0:
                continue
            key = (round(su / bucket) * bucket, round(sv / bucket) * bucket)
            if (name, yaw) not in out[key]:
                out[key].append((name, yaw))
    return out


#: How many times thicker than the surface it replaces a piece may be.
#:
#: `plan_run` fitted only the two IN-PLANE sizes, so nothing ever compared a piece's
#: thickness with the thing it stood for: a 155uu base trim was covered by a 600uu wall
#: and a 112uu arch stone by a 300uu archway. Measured over 7,230 placements, the ratio
#: of piece thickness to source thickness is **median 1.00 and then p75 5.05** -- pieces
#: are either exactly right or several times too thick, with only 4.6% between 1.5 and
#: 2.0. Two is inside that empty band, so it separates the two populations without
#: discarding any real fit.
THICKNESS_LIMIT = 2.0

#: How tall a piece must be relative to the source mesh it stands for, as a fraction.
#:
#: This is the orientation rule. Family inventories mix UPRIGHT members with LYING ones
#: -- `ColumnTrim_CastStone` ships both `_900W` (104x103x900, a column) and `_900W_CutF`
#: (900x70x40, a strip) -- and fitting only the in-plane footprint picks the strip,
#: laying a 602uu corner stone flat 506 times. "Longest axis" cannot separate them,
#: because OMD3 walls are authored long on X as well.
#:
#: Height does. Measured over 6,771 placements: pieces that are correctly oriented have
#: a height ratio of **median 0.75** against the source, while the 1,068 that are turned
#: the wrong way sit at **median 0.12**, p90 0.59. A floor of a third keeps 5,504
#: placements and removes **872 of the 1,068** turned pieces.
HEIGHT_FLOOR = 0.33
HEIGHT_CEILING = 3.0


def upright_matches(info, source):
    """True when a piece stands up if and only if its source mesh does.

    The height floor alone is not enough: `RailingEndCap_Tall` is 240 tall against a
    186-tall railing section, a ratio of 1.29 that passes, yet the source LIES (403 long,
    186 tall) and the piece STANDS (89 wide, 240 tall). Both tests are needed, and they
    catch different things -- height catches a strip offered for a column, this catches
    a post offered for a rail.
    """
    if not source:
        return True
    own = [2.0 * abs(v) for v in info['extent']]
    src_tall = source[2] > max(source[0], source[1]) * 1.5
    own_tall = own[2] > max(own[0], own[1]) * 1.5
    return src_tall == own_tall


def height_ok(info, source_height, floor=HEIGHT_FLOOR, ceiling=HEIGHT_CEILING):
    """True when a piece is a plausible stand-in for something `source_height` tall."""
    if not source_height or source_height <= 0.0:
        return True
    own = 2.0 * abs(info['extent'][2])
    if own <= 0.0:
        return True
    return floor <= own / source_height <= ceiling


def thick_enough(info, thickness, limit=THICKNESS_LIMIT):
    """True when a piece is no more than `limit` times as thick as its target.

    A piece's thickness is its own thinnest dimension, because the yaw that places it
    puts that axis on the run's normal.
    """
    if not thickness or thickness <= 0.0:
        return True
    own = 2.0 * min(abs(v) for v in info['extent'])
    return own <= thickness * limit


#: How much thinner than its next-thinnest axis a piece must be for "which way is it
#: flat" to have an answer. A column (`ColumnTrim_CastStone_900W` is 104x103x900) is
#: equally thin on both horizontals, so neither is its facing; judging those is what
#: polluted three earlier orientation metrics, so they are left alone.
UNAMBIGUOUS = 0.6


def lies_flat(info, limit=UNAMBIGUOUS):
    """True unless the piece is unambiguously thin on a HORIZONTAL axis.

    A surface whose normal is z may only be covered by pieces that are flat on z.
    `footprints`' flat branch assumed that of every family member -- "both horizontals
    span the plane and a yaw swaps them" -- but a kit ships wall panels as thin as
    `Wall_Sec_1_3_V2_RB` (900 x 10.5 x 300), which is thin on **y**. Offered to a
    horizontal run it stands on edge, and because its 10.5uu thickness divides a 2400uu
    span almost exactly, it scored a near-perfect band depth and won: 226 paper-thin
    sheets standing in a row where a ramp belonged.

    This is the same mistake the upright branch already refuses in the other direction
    (a wall's thickness offered as a span); a horizontal run needs the same rule.
    """
    own = [2.0 * abs(info['extent'][a]) for a in (0, 1, 2)]
    order = sorted((0, 1, 2), key=lambda a: own[a])
    thin, next_thin = order[0], order[1]
    if own[next_thin] <= 0.0 or own[thin] / own[next_thin] > limit:
        #: no clear thin axis -- a column or a cube, which this rule cannot judge
        return True
    return thin == 2


def plan_run(members, size_u, size_v, normal, limit=SCALE_LIMIT, may_overhang=False,
             thickness=None, source_size=None):
    """Cover a run: [(name, offset u, offset v, size u, size v, yaw)].

    Banded rather than freely packed: pick a band depth along `v`, then fill each band
    along `u`. OMD3's own kits are built this way -- `Floor_Squarebrick` ships 1x2
    through 4x4 and `Wall_Sec_V2_RB` every height against every width -- so bands
    reproduce how the art was meant to go together.

    **The depth is chosen together with its widths, not before them.** A depth that fits
    `v` beautifully is worthless if the only piece at that depth is a narrow strip: for
    a 2000 x 800 floor, picking the depth first chose the 912-deep band whose sole width
    is 308, and tiled the run with seven thin strips at 0.93 x 0.88. So every depth the
    family offers is tried, and the one with the least total distortion wins, with
    overhang counted in that cost and fewer, larger pieces breaking ties.
    """
    if normal == 2:
        flat = [m for m in members if lies_flat(m[2])]
        #: never empty the family -- keep the flattest so the surface is still built
        members = flat or [min(members, key=lambda m: abs(m[2]['extent'][2]))]
    if source_size:
        kept = [m for m in members
                if height_ok(m[2], source_size[2]) and upright_matches(m[2], source_size)]
        #: never empty the family -- keep the tallest so the surface is still built
        members = kept or [max(members, key=lambda m: abs(m[2]['extent'][2]))]
    if thickness:
        fitted = [m for m in members if thick_enough(m[2], thickness)]
        #: never empty the family -- if nothing is thin enough, keep the thinnest so the
        #: surface is still built, and the oversize report names it
        members = fitted or [min(members,
                                 key=lambda m: min(abs(v) for v in m[2]['extent']))]
    feet = footprints(members, normal)
    if not feet:
        return [], (1.0, 1.0)
    best = None
    for depth in sorted({f[1] for f in feet}, reverse=True):
        widths = sorted({f[0] for f in feet
                         if abs(f[1] - depth) <= SIZE_BUCKET / 2.0}, reverse=True)
        if not widths:
            continue
        bands, scale_v = fit_axis(size_v, [depth], limit, may_overhang)
        over_v = getattr(fit_axis, 'last_overhang', 0.0)
        if not bands:
            continue
        cols, scale_u = fit_axis(size_u, widths, limit, may_overhang)
        over_u = getattr(fit_axis, 'last_overhang', 0.0)
        if not cols:
            continue
        pieces = len(bands) * len(cols)
        #: overhang is part of the cost, not free. Scored on scale alone, a band that
        #: overhangs twenty times its target reads as a perfect fit because its scale
        #: is exactly 1.0, and it wins every comparison.
        cost = (abs(scale_u - 1.0) + abs(scale_v - 1.0)
                + over_u / max(size_u, 1.0) + over_v / max(size_v, 1.0), pieces)
        if best is None or cost < best[0]:
            best = (cost, depth, bands, cols, scale_u, scale_v)
    if best is None:
        return [], (1.0, 1.0)
    _cost, depth, bands, cols, scale_u, scale_v = best
    out = []
    at_v = 0.0
    for band in bands:
        at_u = 0.0
        for width in cols:
            key = (round(width / SIZE_BUCKET) * SIZE_BUCKET,
                   round(depth / SIZE_BUCKET) * SIZE_BUCKET)
            choices = feet.get(key) or []
            if not choices:
                raise AssertionError('no member is %gx%g, yet its width came from '
                                     'that bucket' % (width, depth))
            name, yaw = sorted(choices)[0]
            out.append((name, at_u * scale_u, at_v * scale_v, width, band, yaw))
            at_u += width
        at_v += band
    return out, (scale_u, scale_v)
