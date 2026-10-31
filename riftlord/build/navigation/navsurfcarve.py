"""Take a level's walkable navgraph out from under floor a Map Editor plan removes.

A removed or moved floor piece leaves the polygons it held up in the cooked graph, and
NavPower walks enemies across them. This drops, from both agents' graphs, every polygon
that stands mostly on such a piece. Nothing is clipped and no vertex is added: the
kept polygons are the shipped ones, an edge id that led to a dropped polygon becomes a
wall on both sides, and the shipped across-pointers are kept, re-aimed at the kept
records' new offsets (zero where they named a dropped one). navsurfgen rebuilds the
trees.

    python3 navsurfcarve.py --record <mission id>        # dry run on a Missions record

API: `floor_boxes(level_name, plan)` -> (gone, kept); `carve_buffer(buf, gone, kept)`
-> (buf or None, notes, stats); `carve_level(uasset, uexp, level_name, plan, chains)` ->
(uasset, uexp, notes); `broken_legs(before, after, chains)` -> (cut, detours).
"""

import argparse
import collections
import math
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navsurf  # noqa: E402
from riftlord.build.navigation import navsurfgen  # noqa: E402

#: Builds change the walkable graph for removed floor only when this is True.
ENABLED = False

#: A piece counts as floor when its box is at most this thick, in uu (kit floors are
#: 20-45; rocks, terrain and stairs are not boxes a polygon can be cut by).
FLOOR_THICKNESS = 120.0
#: ... and at least this wide on both axes, in uu.
FLOOR_MIN_SIDE = 90.0
#: Mesh names of floor-class pieces (kit floors, planks, grates, broken floors).
FLOOR_NAME = re.compile(r'floor|woodplank|drawbridge', re.I)
#: ... except these, which only look like floor by name.
NOT_FLOOR = re.compile(r'floorspike|floortemplate|fx', re.I)
#: A piece holds up a polygon whose height is within this far above the piece's top, uu.
SUPPORT_BELOW = 120.0
#: ... or within this far below its top, in uu.
SUPPORT_ABOVE = 60.0
#: A removed footprint left narrower than this after kept floor is taken off it is
#: ignored, in uu.
MIN_CUT_SIDE = 100.0
#: A polygon is dropped when at least this share of its area lies on removed floor.
DROP_SHARE = 0.5
#: Points closer than this are the same point, in uu.
EPS = 1.0


# -- which floor went ---------------------------------------------------------------

def _box(info, m):
    o, e = info['origin'], info['extent']
    pts = []
    for dx in (-1, 1):
        for dy in (-1, 1):
            for dz in (-1, 1):
                c = (o[0] + dx * e[0], o[1] + dy * e[1], o[2] + dz * e[2])
                pts.append([m[r] * c[0] + m[4 + r] * c[1] + m[8 + r] * c[2] + m[12 + r]
                            for r in range(3)])
    return ([min(p[k] for p in pts) for k in range(3)],
            [max(p[k] for p in pts) for k in range(3)])


def is_floor(mesh, box):
    """True for a floor-class mesh lying flat: its name says floor (FLOOR_NAME, less
    NOT_FLOOR) and its box is a slab."""
    lo, hi = box
    return (bool(FLOOR_NAME.search(mesh or '')) and not NOT_FLOOR.search(mesh or '')
            and hi[2] - lo[2] <= FLOOR_THICKNESS and hi[0] - lo[0] >= FLOOR_MIN_SIDE
            and hi[1] - lo[1] >= FLOOR_MIN_SIDE)


def floor_boxes(level_name, plan, level=None):
    """(gone, cover): world boxes of floor pieces the plan removes or moves away, and of
    the floor pieces in the final level (kept, moved to their new place, and added)."""
    from riftlord.formats.omd3 import meshcat
    from riftlord.mapeditor import leveldata
    level = level if level is not None else leveldata.level(level_name)
    cat = meshcat.load()
    removed = {int(i) for i in plan.get('removed') or []}
    moved = {int(e['id']): e['matrix'] for e in plan.get('edits') or []
             if isinstance(e, dict) and 'id' in e}
    gone, cover = [], []
    for i, piece in enumerate(level.get('pieces') or []):
        mesh = piece.get('mesh')
        info = cat.get(mesh)
        if not info or piece.get('instanced'):
            continue
        pid = piece.get('id', i)
        if pid in removed or pid in moved:
            box = _box(info, piece['matrix'])
            if is_floor(mesh, box):
                gone.append(box)
        if pid not in removed:
            box = _box(info, moved.get(pid, piece['matrix']))
            if is_floor(mesh, box):
                cover.append(box)
    for item in added_floor(plan, cat):
        cover.append(item[1])
    return gone, cover


def added_floor(plan, cat=None):
    """[(name, box)] floor-class pieces the plan adds: meshes and kit pieces."""
    from riftlord.formats.omd3 import meshcat
    from riftlord.mapeditor import scratch
    cat = cat or meshcat.load()
    out = []
    for item in plan.get('added') or []:
        name = item.get('name') or item.get('cls') or ''
        try:
            if item.get('kind') == 'kit':
                box = scratch._piece_box(item, cat)
            else:
                info = cat.get(item.get('name'))
                box = _box(info, item['matrix']) if info and item.get('matrix') else None
        except Exception:                            # noqa: BLE001
            box = None
        if box and is_floor(name, box):
            out.append((name, box))
    return out


def _minus(box, other):
    """xy rectangle `box` less `other`: up to four rectangles, (lo, hi) with z of box."""
    (lo, hi), (olo, ohi) = box, other
    if olo[0] >= hi[0] or ohi[0] <= lo[0] or olo[1] >= hi[1] or ohi[1] <= lo[1]:
        return [box]
    out = []
    if olo[0] > lo[0]:
        out.append(([lo[0], lo[1], lo[2]], [olo[0], hi[1], hi[2]]))
    if ohi[0] < hi[0]:
        out.append(([ohi[0], lo[1], lo[2]], [hi[0], hi[1], hi[2]]))
    x0, x1 = max(lo[0], olo[0]), min(hi[0], ohi[0])
    if olo[1] > lo[1]:
        out.append(([x0, lo[1], lo[2]], [x1, olo[1], hi[2]]))
    if ohi[1] < hi[1]:
        out.append(([x0, ohi[1], lo[2]], [x1, hi[1], hi[2]]))
    return [b for b in out if b[1][0] - b[0][0] > EPS and b[1][1] - b[0][1] > EPS]


def cut_boxes(gone, cover):
    """The removed floor's footprints less any floor of the final level whose top is
    within a step of theirs, split so that no two overlap, each at least MIN_CUT_SIDE
    wide."""
    out = []
    for g in gone:
        parts = [g]
        for k in cover:
            if abs(k[1][2] - g[1][2]) <= navsurfgen.STEP_HEIGHT:
                parts = [p for q in parts for p in _minus(q, k)]
        for k in out:
            if abs(k[1][2] - g[1][2]) <= SUPPORT_BELOW:
                parts = [p for q in parts for p in _minus(q, k)]
        out += [p for p in parts if p[1][0] - p[0][0] >= MIN_CUT_SIDE
                and p[1][1] - p[0][1] >= MIN_CUT_SIDE]
    return out


# -- which polygons go ---------------------------------------------------------------

def _clip(poly, axis, value, keep_less):
    """Convex `poly` clipped to the half-space axis <= value (or >=)."""
    inside = (lambda p: p[axis] <= value + 1e-6) if keep_less else \
        (lambda p: p[axis] >= value - 1e-6)
    out = []
    n = len(poly)
    for k in range(n):
        a, b = poly[k], poly[(k + 1) % n]
        if inside(a):
            out.append(a)
        if inside(a) != inside(b):
            t = (value - a[axis]) / (b[axis] - a[axis])
            out.append(tuple(a[j] + t * (b[j] - a[j]) for j in range(3)))
    return out


def _area(poly):
    return navsurf.polygon_area(poly) if len(poly) >= 3 else 0.0


def _inside_area(poly, box):
    """Area of convex `poly` inside the xy rectangle `box`."""
    lo, hi = box
    p = _clip(poly, 0, lo[0], False)
    p = _clip(p, 0, hi[0], True) if p else p
    p = _clip(p, 1, lo[1], False) if p else p
    p = _clip(p, 1, hi[1], True) if p else p
    return _area(p) if p else 0.0


def _holds(poly, box):
    """True when `box` overlaps `poly` in xy and its top is at the polygon's height."""
    lo, hi = box
    xs, ys = [v[0] for v in poly], [v[1] for v in poly]
    if min(xs) >= hi[0] or max(xs) <= lo[0] or min(ys) >= hi[1] or max(ys) <= lo[1]:
        return False
    z = sum(v[2] for v in poly) / len(poly)
    return z - SUPPORT_BELOW <= hi[2] <= z + SUPPORT_ABOVE


def doomed(polys, boxes):
    """Indices of the polygons standing mostly on the removed footprints `boxes` (which
    do not overlap one another: cut_boxes splits them apart)."""
    out = set()
    for i, poly in enumerate(polys):
        total = _area(poly)
        if total <= 0:
            continue
        on = sum(_inside_area(poly, b) for b in boxes if _holds(poly, b))
        if on >= DROP_SHARE * total:
            out.add(i)
    return out


def _record_bytes(buf, sec, ids, head_ptr, nbrs, last):
    """One shipped record re-emitted with its edge ids and across-pointers replaced;
    everything else (centre, radius, flags, vertex tags, tail) is the shipped bytes."""
    at, n = sec['at'], len(sec['verts'])
    out = bytearray(buf[at:at + navsurf.SECTION_FIXED])
    struct.pack_into('<Q', out, 40, head_ptr)
    for k in range(n):
        vat = at + navsurf.SECTION_FIXED + navsurf.VERTEX_STRIDE * k
        if last and k == n - 1:
            out += buf[vat:vat + 12]
            break
        if sec['truncated'] and k == n - 1:
            # the shipped last record no longer last: its final vertex gets the fields
            # every other vertex carries, and the record its zero tail
            out += buf[vat:vat + 12] + struct.pack('<2h2Q', 0, -4, ids[k], nbrs[k])
            out += b'\x00' * navsurf.SECTION_TAIL
            return bytes(out)
        v = bytearray(buf[vat:vat + navsurf.VERTEX_STRIDE])
        struct.pack_into('<2Q', v, 16, ids[k], nbrs[k])
        out += v
    if not last:
        tail = at + navsurf.SECTION_FIXED + navsurf.VERTEX_STRIDE * n
        out += buf[tail:tail + navsurf.SECTION_TAIL]
    return bytes(out)


def carve_graph(buf, head, boxes):
    """(polys, record bytes, stats) for one agent's graph without the polygons on
    removed floor; (None, None, stats) when nothing goes."""
    secs = navsurf.sections(buf, head)
    polys = [list(s['verts']) for s in secs]
    gone = doomed(polys, boxes)
    if not gone:
        return None, None, {'removed': 0, 'walled': 0}
    keep = [i for i in range(len(secs)) if i not in gone]
    old_at = {s['at'] - head['origin']: i for i, s in enumerate(secs)}
    new_off, at = {}, navsurf.RECORDS_BEGIN
    for n, i in enumerate(keep):
        new_off[i] = at
        at += navsurfgen._record_size(len(polys[i]), last=(n == len(keep) - 1))

    def aim(ptr):
        # a pointer is the target record's offset from the graph's origin
        j = old_at.get(ptr) if ptr else None
        return new_off[j] - navsurf.ORIGIN if j in new_off else 0
    dead = {e for i in gone for e in secs[i]['edge_ids'] if e}
    ids = [[0 if e in dead else e for e in secs[i]['edge_ids']] for i in keep]
    walled = sum(1 for i in keep for e in secs[i]['edge_ids'] if e and e in dead)
    # the last record's final edge has no room for an id; its partner becomes a wall
    if ids[-1][-1]:
        dead.add(ids[-1][-1])
        ids = [[0 if e in dead else e for e in row] for row in ids]
    counts = collections.Counter(e for row in ids for e in row if e)
    ids = [[0 if e and counts[e] % 2 else e for e in row] for row in ids]
    recs = b''
    for n, i in enumerate(keep):
        s = secs[i]
        recs += _record_bytes(buf, s, ids[n],
                              aim(struct.unpack_from('<Q', buf, s['at'] + 40)[0]),
                              [aim(p) for p in s['neighbours']], n == len(keep) - 1)
    return [polys[i] for i in keep], recs, {'removed': len(gone), 'walled': walled}


def carve_buffer(buf, gone, cover):
    """(new buffer or None when nothing changes, notes, [stats per agent])."""
    boxes = cut_boxes(gone, cover)
    if not boxes:
        return None, [], []
    heads = navsurf.graphs(buf)
    graphs, stats = [], []
    for h in heads:
        polys, recs, st = carve_graph(buf, h, boxes)
        if polys is None:
            secs = navsurf.sections(buf, h)
            polys = [list(s['verts']) for s in secs]
            start = secs[0]['at']
            recs = bytes(buf[start:h['tree_at']])
        if not polys:
            raise ValueError('the removed floor takes out the whole walkable graph')
        graphs.append((polys, None, None, recs))
        stats.append(st)
    if not any(s['removed'] for s in stats):
        return None, [], stats
    out = navsurfgen.buffer_per_agent(graphs, list(heads[0]['bbox']))
    notes = ['walkable floor taken out under %d removed or moved floor piece(s): %d '
             'polygon(s)' % (len(gone), stats[0]['removed'])]
    return out, notes, stats


def check(buf):
    """Problems with a carved buffer: it parses, trees find points, ids pair up."""
    out = []
    heads = navsurf.graphs(buf)
    if len(heads) != 2:
        return ['expected two agent graphs, found %d' % len(heads)]
    for h in heads:
        got = navsurf.sections(buf, h)
        if not got:
            out.append('a graph does not parse')
            continue
        out += navsurf.check_sections(buf, h, got)
        out += navsurf.check_tree(buf, h, got)
        counts = collections.Counter(e for s in got for e in s['edge_ids'] if e)
        odd = [e for e, c in counts.items() if c % 2]
        if odd:
            out.append('%d edge id(s) quoted an odd number of times' % len(odd))
    return out


#: A route leg whose walk over the floor grows past this many times its length before
#: the cut is reported as a detour ...
DETOUR_RATIO = 2.0
#: ... when it also grows by at least this much, in uu.
DETOUR_MIN = 2000.0


def _walker(buf):
    """(polygons, walk(a, b) -> distance over the first agent's floor or None)."""
    import heapq
    from riftlord.build.navigation import navsurfextend
    head = navsurf.graphs(buf)[0]
    secs = navsurf.sections(buf, head)
    polys = [list(s['verts']) for s in secs]
    adj = navsurf.adjacency(buf, head, secs)
    cen = [tuple(sum(v[k] for v in p) / len(p) for k in range(3)) for p in polys]

    def walk(a, b):
        sa, sb = navsurfextend.on_surface(polys, a), navsurfextend.on_surface(polys, b)
        if sa is None or sb is None:
            return None
        best, todo = {sa: 0.0}, [(0.0, sa)]
        while todo:
            d, i = heapq.heappop(todo)
            if i == sb:
                return d
            if d > best[i]:
                continue
            for j in adj[i]:
                nd = d + math.dist(cen[i], cen[j])
                if nd < best.get(j, 1e18):
                    best[j] = nd
                    heapq.heappush(todo, (nd, j))
        return None
    return polys, walk


def broken_legs(before, after, chains):
    """([(a, b)] route legs with no walkable floor left between them,
    [(a, b, before, after)] legs whose walk now detours)."""
    _pb, walk_b = _walker(before)
    _pa, walk_a = _walker(after)
    cut, detours = [], []
    for chain in chains:
        for a, b in zip(chain, chain[1:]):
            db = walk_b(a, b)
            if db is None:
                continue
            da = walk_a(a, b)
            if da is None:
                cut.append((tuple(a), tuple(b)))
            elif da > DETOUR_RATIO * db and da - db > DETOUR_MIN:
                detours.append((tuple(a), tuple(b), db, da))
    return cut, detours


def active():
    """True when a build changes the walkable graph for floor the plan removes or adds."""
    from riftlord.build.navigation import navsurfadd
    return ENABLED or navsurfadd.ENABLED


def carve_level(uasset, uexp, level_name, plan, chains=()):
    """(uasset, uexp, notes) with the walkable graph cut under removed floor (ENABLED)
    and added under added floor (navsurfadd.ENABLED). Raises ValueError when a cut
    would cut a ground route off."""
    from riftlord.build.navigation import navsurfadd
    from riftlord.build.navigation import navsurfextend
    from riftlord.build.navigation import navsurfinject
    buf = navsurfextend._surface_of(uasset, uexp)
    out, notes = buf, []
    if ENABLED:
        gone, cover = floor_boxes(level_name, plan)
        if gone:
            got, more, _stats = carve_buffer(out, gone, cover)
            if got is not None:
                out, notes = got, notes + more
    if navsurfadd.ENABLED:
        boxes = [box for _name, box in added_floor(plan)]
        got, more, _stats = navsurfadd.add_buffer(out, boxes)
        if got is not None:
            bad = navsurfadd.check(out, got)
            if bad:
                raise ValueError('the added walkable floor breaks the graph\'s rules: %s'
                                 % '; '.join(bad[:5]))
            out, notes = got, notes + more
    if out is buf:
        return uasset, uexp, []
    problems = check(out)
    if problems:
        raise ValueError('the changed walkable graph failed its checks: %s'
                         % '; '.join(problems))
    broken, detours = broken_legs(buf, out, chains)
    if broken:
        raise ValueError(
            'removed floor cuts a ground route: no walkable floor is left between %s'
            % '; '.join('(%.0f, %.0f, %.0f) and (%.0f, %.0f, %.0f)' % (a + b)
                        for a, b in broken))
    for a, b, db, da in detours:
        notes.append('removed floor makes enemies walk %.0f uu instead of %.0f between '
                     '(%.0f, %.0f, %.0f) and (%.0f, %.0f, %.0f)' % ((da, db) + a + b))
    ua, ux, checks = navsurfinject.set_surface_bytes(uasset, uexp, out)
    bad = [name for name, ok in checks if not ok]
    if bad:
        raise ValueError('writing the changed walkable graph failed: %s' % '; '.join(bad))
    return ua, ux, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--record', required=True, help='a Missions record id')
    args = ap.parse_args(argv)
    from riftlord.missions import bucket
    rec = bucket.load(args.record)
    gone, kept = floor_boxes(rec['level'], rec['plan'])
    buf = navsurf.buffer(rec['level'])
    out, notes, stats = carve_buffer(buf, gone, kept)
    print('%d floor piece(s) gone, %d kept' % (len(gone), len(kept)))
    for line in notes:
        print(line)
    for agent, s in enumerate(stats):
        print('agent %d: %s' % (agent, s))
    if out is not None:
        print('problems:', check(out) or 'none')


if __name__ == '__main__':
    main()
