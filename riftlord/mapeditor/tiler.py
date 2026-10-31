"""Cover an OMD2 trap volume with OMD3 kit pieces.

OMD2 describes a placeable surface as one rectangle of cells -- a floor might be
6 x 22 -- while OMD3's kit tops out around 4 x 8. So a volume does not map to a
piece; it maps to a **tiling** of pieces, and choosing that tiling is the first
real decision the converter makes.

    python3 tiler.py --demand TheEdge
    python3 tiler.py --survey            # every OMD2 level against the kit

The algorithm is deliberately dull. Rectangles this small (the largest volume in
the campaign is a few hundred cells) do not need anything clever, and a greedy
pass that always lays the biggest piece that fits produces coverings a person
would call sensible. What matters far more than optimality is that the result is
**exact**: every cell covered once, nothing overlapping, nothing hanging over the
edge. A converter that silently leaves a hole in a floor produces a level that
looks right and drops orcs through it.

So `cover` returns placements and `check` proves them, and nothing here is
trusted without the proof.
"""

import argparse
import collections
import json
import os
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

CATALOGUE = gamepath.data('kit-catalogue.json')

#: OMD2 names a surface Floor / Wall / Ceiling; the kit names pieces the same way.
SURFACE_WORD = {'Floor': 'Floor', 'Wall': 'Wall', 'Ceiling': 'Ceiling'}


#: Families whose meshes are the flat panels a surface is actually built from.
#: Everything else in the kit may offer a trap grid without being a surface: a
#: `Column` is a column, an `ArchwayGate` is a gate, a `Ceiling_Column` is the top
#: of a column that meets the ceiling. All three carry WALL grids, because their
#: sides are wall you can build on -- which is true, and useless for filling a
#: wall run. Tiling by grid footprint alone would line a corridor with columns.
PANELS = {'Floor': ('Floor', 'FloorCeiling'),
          'Wall': ('Wall', 'wall', 'WallOuterCornerFull'),
          'Ceiling': ('Ceiling', 'FloorCeiling')}


def family(name):
    return name.split('_')[0]


def catalogue(path=CATALOGUE, panels_only=True, available=None):
    """{surface: {(w, h): [piece names]}} for whole-cell footprints.

    The surface a grid provides comes from its **orientation**, not the piece's
    name: pitch +90 faces up (Floor), -90 down (Ceiling), 0 or 180 is a Wall.
    Names describe the object, not the surface.

    `panels_only` then keeps just the families whose mesh IS that surface. The
    grid tells you where a trap can go; it says nothing about the shape of the
    thing carrying it, and the shape is what a converted level is made of.

    A piece is listed under both orientations, since a 4 x 3 laid sideways is a
    3 x 4 and the kit does not ship both.
    """
    with open(path) as fh:
        kit = json.load(fh)
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    for name, info in kit.items():
        # A piece that cannot be lifted from anywhere must not be chosen, or the
        # tiling is a plan for a level that cannot be built.
        if available is not None and name not in available:
            continue
        for grid in info['trap_grids']:
            surface = grid.get('surface')
            if not surface:
                continue
            if panels_only and family(name) not in PANELS.get(surface, ()):
                continue
            a, b = grid['cells']
            if a != int(a) or b != int(b) or a < 1 or b < 1:
                continue
            a, b = int(a), int(b)
            out[surface][(a, b)].append(name)
            if a != b:
                out[surface][(b, a)].append(name)
    for surface, sizes in out.items():
        for key in sizes:
            sizes[key].sort(key=_preference(surface))
    return out


def _preference(surface):
    """Order the pieces that offer a footprint, best first.

    A `BP_Scenario_*` is a map-specific marker rather than a tile and is reached
    for only if nothing else exists; otherwise a piece whose name agrees with its
    surface is the one an author would have picked. Alphabetical after that, so a
    level converts the same way twice.
    """
    def rank(name):
        return (name.startswith('BP_Scenario'),
                not name.startswith(surface + '_'),
                name)
    return rank


def blocked_bits(width, height, mask):
    """The closed cells of an OMD2 volume, as a bitmask in `cover`'s own bit order.

    An OMD2 volume is a rectangle plus a string of '0'/'1' saying which cells accept
    a trap, and **about one cell in seven is closed** -- 16.1% of TheCrunch's 8,574,
    across 139 of its 273 volumes. Tiling the whole rectangle therefore hands the
    player buildable space OMD2 deliberately denied: the floor under a stair, the
    square behind a door, the ring around a spawner.

    **Row-major**, `mask[row * width + col]`, which is the order `validate.py` already
    uses and the order `cover` indexes its own bitmask in. Measured rather than
    assumed: read row-major, TheCrunch's closed cells form 198 connected regions
    averaging 6.81 cells; read column-major, 378 regions averaging 3.57. Real closed
    regions are blobs -- a pillar, a stairway -- so the more contiguous reading is the
    right one, by a factor of two.
    """
    bits = 0
    for row in range(height):
        for col in range(width):
            i = row * width + col
            if i >= len(mask) or mask[i] != '1':
                bits |= 1 << i
    return bits


def cover(width, height, sizes, limit=400000, blocked=0):
    """Tile a width x height rectangle with the given footprints.

    Greedy is not enough, and the failure is easy to miss. A 5 x 1 floor strip is
    `3x1 + 2x1`, but a greedy pass lays the biggest piece first -- 4 x 1 -- and
    strands a single cell the floor kit has no piece for. Across the campaign
    that alone accounted for most of the volumes that "could not be tiled".

    So this searches. Depth-first from the first uncovered cell, largest piece
    first so the common case still terminates immediately, backtracking when a
    choice paints itself into a corner. States already proven hopeless are
    remembered, because the same leftover shape is reached by many orders.

    Returns [(w, h, x, y)], or None if the rectangle genuinely cannot be built
    from these pieces -- which does happen: there is no 1 x 1 floor.
    """
    order = sorted(set(sizes), key=lambda wh: (-(wh[0] * wh[1]), -max(wh)))
    full = (1 << (width * height)) - 1
    #: CLOSED CELLS START FILLED, which is the whole trick. `fits` already refuses to
    #: lay a piece over anything already covered, so seeding the search with the
    #: blocked cells makes every covering avoid them for free -- and `full` is still
    #: the whole rectangle, so the search succeeds exactly when every OPEN cell is
    #: covered and no closed one is.
    blocked &= full
    dead = set()
    budget = [limit]

    def first_gap(filled):
        i = 0
        while filled >> i & 1:
            i += 1
        return i % width, i // width

    def fits(filled, w, h, x, y):
        if x + w > width or y + h > height:
            return 0
        bits = 0
        for dy in range(h):
            row = ((1 << w) - 1) << ((y + dy) * width + x)
            if filled & row:
                return 0
            bits |= row
        return bits

    def walk(filled, out):
        if filled == full:
            return True
        if filled in dead or budget[0] <= 0:
            return False
        budget[0] -= 1
        x, y = first_gap(filled)
        for (w, h) in order:
            bits = fits(filled, w, h, x, y)
            if not bits:
                continue
            out.append((w, h, x, y))
            if walk(filled | bits, out):
                return True
            out.pop()
        dead.add(filled)
        return False

    out = []
    if not walk(blocked, out):
        return None
    return out


def check(width, height, placements, blocked=0):
    """Prove a covering is exact. Returns a list of complaints, empty if sound.

    With `blocked`, exact means every OPEN cell covered once and every CLOSED cell
    covered not at all.
    """
    if placements is None:
        return ['no covering was found']
    seen = [[0] * width for _ in range(height)]
    bad = []
    for w, h, x, y in placements:
        if x < 0 or y < 0 or x + w > width or y + h > height:
            bad.append('piece %dx%d at (%d,%d) hangs over the edge' % (w, h, x, y))
            continue
        for dy in range(h):
            for dx in range(w):
                seen[y + dy][x + dx] += 1
    overlaps = sum(1 for row in seen for c in row if c > 1)
    holes = covered_closed = 0
    for y in range(height):
        for x in range(width):
            shut = bool(blocked >> (y * width + x) & 1)
            if shut and seen[y][x]:
                covered_closed += 1
            elif not shut and not seen[y][x]:
                holes += 1
    if overlaps:
        bad.append('%d cell(s) covered more than once' % overlaps)
    if holes:
        bad.append('%d open cell(s) left uncovered' % holes)
    if covered_closed:
        bad.append('%d closed cell(s) were covered' % covered_closed)
    return bad


def plan(surface, width, height, kit=None, mask=None):
    """(placements, reason) for one OMD2 volume, tiled with grid-carrying pieces.

    **An OMD3 trap grid has NO per-cell mask.** This docstring used to claim it did
    -- "a trap grid is a box with a bitmask and so is an OMD2 volume, so that half of
    the conversion is 1:1 and needs no tiling whatsoever" -- and that is false.
    `UOMDTrapGridBoxComponent` carries `TrapPlacementTypes` and
    `TrapWarMachineTypes`, which say which KINDS of trap a box accepts, and a
    `TrapGridRevealPlane`. Every cell inside the box is buildable; there is nowhere to
    record that one of them is not.

    So OMD2's bitmask can only be honoured **geometrically**: cover the open cells
    with grid-carrying pieces and leave the closed ones outside every box. Pass
    `mask` and that is what this does.

    The closed cells still need something to stand on -- the floor is solid whether or
    not a trap may go there -- but that something must NOT carry a grid. Stairs, the
    square behind a door and the ring around a spawner are the shapes OMD2 closes, and
    they are exactly the places a converted level should put plain, grid-less
    geometry.
    """
    kit = kit or catalogue()
    sizes = kit.get(surface)
    if not sizes:
        return None, 'no panel pieces for a %s' % surface
    blocked = blocked_bits(width, height, mask) if mask else 0
    if blocked == (1 << (width * height)) - 1:
        return [], None                  # every cell shut: nothing buildable here
    placements = cover(width, height, list(sizes), blocked=blocked)
    problems = check(width, height, placements, blocked=blocked)
    if problems:
        return None, '; '.join(problems)
    named = [(w, h, x, y, sizes[(w, h)][0]) for w, h, x, y in placements]
    return named, None


#: How many mesh components a piece may have to be used as grid-less FILLER.
#:
#: Filler stands in the cells OMD2 shut, so it needs to be solid and plausible and
#: nothing more. A decorated wall runs to 24-27 components -- `Wall_Half_FB_1Trap` has
#: 24 -- and expanding one into that many `StaticMeshActor`s per tile would multiply a
#: level's actor count for scenery nobody can build on. Floors and ceilings are mostly
#: one component, so a cheap cap costs little: 17 of 27 floor footprints, 18 of 20
#: ceiling and 11 of 22 wall footprints have a one-mesh piece.
FILLER_MAX_PARTS = 3


def filler_table(kit=None, max_parts=FILLER_MAX_PARTS, cache={}):
    """{surface: {(w, h): (piece, [component meshes])}} for cheap, grid-less filling.

    The MESHES, not the kit piece. A kit piece carries a trap grid 76% of the time and
    filler must carry none -- the whole point of the closed cells is that no trap goes
    there -- so the piece is expanded into its component meshes and those are placed as
    plain `StaticMeshActor`s. Same appearance, no grid.
    """
    key = (max_parts,)
    if key in cache:
        return cache[key]
    from riftlord.formats.omd3 import omd3actors
    kit = kit or catalogue()
    out = {}
    for surface, sizes in kit.items():
        got = {}
        for cells, names in sizes.items():
            best = None
            for name in names:
                try:
                    parts = omd3actors.parts_of(name + '_C') or []
                except BaseException:                # noqa: BLE001
                    continue
                parts = [p for p in parts if p.get('mesh') and not p.get('skeletal')]
                if not parts or len(parts) > max_parts:
                    continue
                if best is None or len(parts) < len(best[1]):
                    best = (name, parts)
            if best:
                got[cells] = best
        if got:
            out[surface] = got
    cache[key] = out
    return out


def plan_filler(surface, width, height, mask, kit=None, max_parts=FILLER_MAX_PARTS):
    """(placements, reason) covering the cells OMD2 SHUT, with grid-less geometry.

    The floor is solid whether or not a trap may go there, so the closed cells still
    need something to stand on. This is the other half of respecting the bitmask: the
    open cells get grid-carrying kit pieces (`plan`), and the closed ones get this.

    Returns [(w, h, x, y, piece, parts)]. Empty when the volume shuts nothing.
    """
    table = filler_table(kit, max_parts)
    sizes = table.get(surface)
    if not sizes:
        return None, 'no cheap %s pieces to fill with' % surface
    shut = blocked_bits(width, height, mask) if mask else 0
    if not shut:
        return [], None                      # nothing closed, nothing to fill
    full = (1 << (width * height)) - 1
    #: the OPEN cells are what filler must avoid, so they are what is blocked here
    placements = cover(width, height, list(sizes), blocked=full & ~shut)
    problems = check(width, height, placements, blocked=full & ~shut)
    if problems:
        return None, '; '.join(problems)
    return [(w, h, x, y) + sizes[(w, h)] for w, h, x, y in placements], None


def selftest():
    """Prove the masked tiling: open cells covered once, closed cells untouched."""
    bad = []
    #: a 4x2 with the two outer cells of the bottom row shut -- the open region is an
    #: L, which no single rectangle covers, so this exercises the search
    sizes = [(1, 1), (2, 1), (4, 1), (2, 2)]
    mask = '1111' + '0110'
    blocked = blocked_bits(4, 2, mask)
    got = cover(4, 2, sizes, blocked=blocked)
    if got is None:
        bad.append('the L-shaped open region was not covered')
    else:
        bad += ['L region: ' + c for c in check(4, 2, got, blocked=blocked)]
    #: a fully shut volume must tile to nothing rather than fail
    got, why = plan('Floor', 2, 2, {'Floor': {(1, 1): ['X'], (2, 2): ['Y']}},
                    mask='0000')
    if got != [] or why is not None:
        bad.append('a fully shut volume should plan to [], got %r / %r' % (got, why))
    #: and with no mask the whole rectangle is still covered, as before
    got = cover(2, 2, [(2, 2)], blocked=0)
    bad += ['unmasked 2x2: ' + c for c in check(2, 2, got or [], blocked=0)]
    #: a closed cell must never be covered, even when a big piece would fit
    blocked = blocked_bits(2, 2, '1110')
    got = cover(2, 2, [(2, 2), (1, 1), (2, 1)], blocked=blocked)
    if got is None:
        bad.append('a 2x2 with one corner shut was not covered')
    else:
        bad += ['one-corner: ' + c for c in check(2, 2, got, blocked=blocked)]
    #: FILLER covers the complement: the shut cells and only those
    table = filler_table()
    if not table.get('Floor'):
        bad.append('no cheap Floor filler pieces were found')
    got, why = plan_filler('Floor', 4, 1, '1001')
    if got is None:
        bad.append('filler for a 4x1 with the middle shut: %s' % why)
    else:
        cells = sum(w * h for w, h, _x, _y, _p, _m in got)
        if cells != 2:
            bad.append('filler covered %d cell(s), expected the 2 shut ones' % cells)
    got, why = plan_filler('Floor', 2, 2, '1111')
    if got != [] or why is not None:
        bad.append('a volume with nothing shut should need no filler, got %r' % (got,))
    return bad


def omd2_scenes():
    """Every OMD2 level name, and the data.zip they live in."""
    import zipfile
    from riftlord.formats.omd2 import trapgrids  # noqa: E402
    with zipfile.ZipFile(trapgrids.DEFAULT_DATA_ZIP) as zf:
        return trapgrids.DEFAULT_DATA_ZIP, trapgrids.list_scenes(zf)


def survey(scenes=None):
    """Try every volume of every OMD2 level against the kit."""
    from riftlord.formats.omd2 import trapgrids  # noqa: E402
    kit = catalogue()
    zip_path, found = omd2_scenes()
    names = scenes or found
    rows = []
    for scene in names:
        try:
            data = trapgrids.extract(zip_path, scene)
        except Exception as exc:                    # noqa: BLE001
            rows.append((scene, 0, 0, 0, 0, 'could not read: %s' % exc))
            continue
        ok = failed = pieces = shut = 0
        why = collections.Counter()
        for vol in data['volumes']:
            #: WITH THE MASK. Surveying the whole rectangle flattered the kit: it
            #: measured a tiling the converter must not use, because the pieces'
            #: grids would then cover cells OMD2 had shut.
            got, reason = plan(vol['placement_type'], vol['width'],
                               vol['height'], kit, mask=vol['mask'])
            if got is None:
                failed += 1
                why[reason] += 1
            elif not got:
                shut += 1
            else:
                ok += 1
                pieces += len(got)
        rows.append((scene, ok, failed, pieces, shut,
                     '; '.join('%s (x%d)' % (r, n) for r, n in why.most_common(2))))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--demand', metavar='SCENE',
                    help='tile one OMD2 level and show the plan')
    ap.add_argument('--survey', action='store_true',
                    help='every OMD2 level against the kit')
    ap.add_argument('--selftest', action='store_true',
                    help='prove the masked tiling covers open cells only')
    args = ap.parse_args(argv)

    if args.selftest:
        bad = selftest()
        for line in bad:
            print('FAILED  %s' % line)
        print('tiler selftest: %s' % ('passed' if not bad else '%d problem(s)' % len(bad)))
        return 1 if bad else 0

    if args.survey:
        rows = survey()
        print('%-28s %6s %6s %8s %6s  %s'
              % ('scene', 'tiled', 'failed', 'pieces', 'shut', 'why not'))
        tot_ok = tot_bad = tot_pieces = tot_shut = 0
        for scene, ok, bad, pieces, shut, why in rows:
            print('%-28s %6d %6d %8d %6d  %s'
                  % (scene, ok, bad, pieces, shut, why))
            tot_ok += ok
            tot_bad += bad
            tot_pieces += pieces
            tot_shut += shut
        print('\n%d volume(s) tiled, %d not, %d entirely shut, %d kit pieces in total'
              % (tot_ok, tot_bad, tot_shut, tot_pieces))
        return 1 if tot_bad else 0

    if args.demand:
        from riftlord.formats.omd2 import trapgrids
        kit = catalogue()
        zip_path, _ = omd2_scenes()
        data = trapgrids.extract(zip_path, args.demand)
        for vol in data['volumes']:
            got, reason = plan(vol['placement_type'], vol['width'],
                               vol['height'], kit)
            head = '%-8s %2dx%-2d' % (vol['placement_type'], vol['width'],
                                      vol['height'])
            if got is None:
                print('%s  CANNOT TILE: %s' % (head, reason))
                continue
            used = collections.Counter('%dx%d' % (w, h) for w, h, _x, _y, _n in got)
            print('%s -> %2d piece(s): %s' % (head, len(got),
                  ', '.join('%s x%d' % (k, v) for k, v in used.most_common())))
        return 0

    ap.error('need --demand SCENE or --survey')


if __name__ == '__main__':
    raise SystemExit(main())
