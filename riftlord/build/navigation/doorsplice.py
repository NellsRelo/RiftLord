"""The shipped walkable polygons kept at each door crossing of a regenerated graph.

Each door the final level keeps (its own, or one the plan adds where the level had one)
gets a crossing zone: an axis-aligned box around the door's drawn parts. Inside the
zone the shipped graph's polygons are kept, clipped to the box, with their vertex tags
and flags byte; outside it the regenerated polygons are kept, clipped to the rest.
Edges on the box sides are split where the other side has a vertex, so every shared
edge matches exactly, and edge ids are allocated again (navsurfgen J1 pointers follow).

    python3 doorsplice.py --level Scenario_09     # report the zones and what was kept
"""

import argparse
import collections
import math
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navsurf  # noqa: E402
from riftlord.build.navigation import navsurfgen  # noqa: E402

#: Margin around a door's drawn parts making its crossing zone (uu).
ZONE_PAD = 300.0
#: Half size of the zone of a plan door with no shipped door at its place (uu).
ZONE_HALF = 600.0
#: Zone height below and above the door's parts or location (uu).
ZONE_BELOW = 600.0
ZONE_ABOVE = 400.0
#: A plan door within this of a shipped door takes that door's zone (uu).
SAME_PLACE = 300.0
#: A shipped polygon is kept only where the regenerated floor is within this of it, so
#: floor the plan removed does not come back (uu). Shipped floor at Scenario_09's second
#: force field sits up to 110 uu off the terrain's collision.
SUPPORT_DZ = 150.0
#: Largest height step joined across a zone's side, where shipped floor meets
#: regenerated floor (uu): the agent's step, so no edge asks a mover to climb a wall.
ZONE_STEP = 48.0
#: Vertex tag of a vertex the clipping makes.
NEW_TAG = -4
#: Points on a box side closer than this are one point (uu).
EPS = 0.5
#: Vertices this close to a box side are moved onto it, and points along a side this
#: close together become one (uu).
SNAP = 1.0
#: Shortest edge kept (uu).
MIN_EDGE = 1.0
#: Height window within which a vertex on another polygon's edge is a T-junction (uu).
STEP_Z = 150.0
#: Most vertices on one polygon: the shipped maximum on every level.
MAX_VERTS = 15
#: Smallest polygon area kept after clipping (uu2).
MIN_AREA = 1.0
#: Two edges quoting one shipped id are the same edge when their midpoints are this
#: close (shipped graphs reuse ids for unrelated edges) (uu).
EDGE_MEET = 600.0


def _mesh_box(piece):
    from riftlord.build.navigation import navregen
    got = navregen._mesh(piece['mesh'])
    if got is None:
        return None
    m = np.array(piece['matrix'], dtype=np.float64).reshape(4, 4)
    w = (np.c_[got[0], np.ones(len(got[0]))] @ m)[:, :3]
    return w.min(0), w.max(0)


def zones(level_name, plan=None):
    """[(lo, hi)] crossing boxes of the final level's doors, overlapping ones merged."""
    from riftlord.mapeditor import leveldata
    plan = plan or {}
    level = leveldata.level(level_name)
    wiring = {w['name']: w for w in plan.get('wiring') or []}
    shipped = {}
    for name, d in (level.get('doors') or {}).items():
        boxes = [_mesh_box(p) for p in level['pieces']
                 if (p.get('sub') or 'persistent') == 'persistent'
                 and p.get('export') == d.get('index')]
        boxes = [b for b in boxes if b is not None]
        loc = np.array(d['location'], dtype=np.float64)
        if boxes:
            lo = np.min([b[0] for b in boxes], axis=0) - [ZONE_PAD, ZONE_PAD, ZONE_BELOW]
            hi = np.max([b[1] for b in boxes], axis=0) + [ZONE_PAD, ZONE_PAD, ZONE_ABOVE]
        else:
            lo = loc - [ZONE_HALF, ZONE_HALF, ZONE_BELOW]
            hi = loc + [ZONE_HALF, ZONE_HALF, ZONE_ABOVE]
        shipped[name] = (loc, lo, hi)
    out = []
    for name, (loc, lo, hi) in shipped.items():
        w = wiring.get(name) or {}
        if w.get('gone') or w.get('at'):
            continue
        out.append((lo, hi))
    for d in plan.get('doors') or []:
        at = np.array(d['at'], dtype=np.float64)
        near = [v for v in shipped.values() if math.dist(v[0][:2], at[:2]) <= SAME_PLACE]
        if near:
            out.append((near[0][1], near[0][2]))
    merged = []
    for lo, hi in out:
        lo, hi = np.array(lo), np.array(hi)
        again = True
        while again:
            again = False
            for k, (a, b) in enumerate(merged):
                if np.all(lo <= b) and np.all(a <= hi):
                    lo, hi = np.minimum(lo, a), np.maximum(hi, b)
                    del merged[k]
                    again = True
                    break
        merged.append((lo, hi))
    return merged


def shipped_graph(level_name, graph):
    """[(verts, edge ids, tags, flags byte)] of the shipped graph `graph`."""
    buf = navsurf.buffer(level_name)
    head = navsurf.graphs(buf)[graph]
    out = []
    for s in navsurf.sections(buf, head):
        n = len(s['verts'])
        tags = [NEW_TAG if (s['truncated'] and i == n - 1) else
                struct.unpack_from('<h', buf, s['at'] + 48 + 32 * i + 14)[0]
                for i in range(n)]
        out.append(([tuple(v) for v in s['verts']], list(s['edge_ids']), tags,
                    bytes.fromhex(s['flags'])[3]))
    return out


# -- clipping -------------------------------------------------------------------------

def clip(ring, axis, value, keep_less):
    """One half-plane of a convex ring [(vertex, tag, source id)]: source id is the
    shipped id of the edge leaving that vertex, None on an edge the clip made."""
    def inside(v):
        return v[axis] <= value if keep_less else v[axis] >= value
    out = []
    n = len(ring)
    for k in range(n):
        p, q = ring[k], ring[(k + 1) % n]
        pin, qin = inside(p[0]), inside(q[0])
        if pin:
            out.append(p if qin else (p[0], p[1], p[2]))
        if pin != qin:
            t = (value - p[0][axis]) / (q[0][axis] - p[0][axis])
            cut = tuple(p[0][i] + t * (q[0][i] - p[0][i]) for i in range(3))
            cut = tuple(value if i == axis else cut[i] for i in range(3))
            if pin:
                out.append((cut, NEW_TAG, None))
            else:
                out.append((cut, NEW_TAG, p[2]))
    if out and not all(inside(r[0]) for r in ring):
        #: the edge leaving the last vertex before the cut now runs along the clip
        for k in range(len(out)):
            here, nxt = out[k], out[(k + 1) % len(out)]
            if abs(here[0][axis] - value) < 1e-6 and abs(nxt[0][axis] - value) < 1e-6:
                out[k] = (here[0], here[1], None)
    return out


def _tidy(ring):
    out = []
    for r in ring:
        if not out or math.dist(out[-1][0][:2], r[0][:2]) >= MIN_EDGE:
            out.append(r)
    while len(out) > 1 and math.dist(out[0][0][:2], out[-1][0][:2]) < MIN_EDGE:
        out.pop()
    if len(out) < 3:
        return None
    xy = [r[0] for r in out]
    area = 0.5 * sum(xy[i][0] * xy[(i + 1) % len(xy)][1] - xy[(i + 1) % len(xy)][0]
                     * xy[i][1] for i in range(len(xy)))
    return out if area >= MIN_AREA else None


def inside_box(ring, lo, hi):
    for axis in (0, 1):
        ring = clip(ring, axis, lo[axis], False)
        if not ring:
            return None
        ring = clip(ring, axis, hi[axis], True)
        if not ring:
            return None
    return _tidy(ring)


def outside_box(ring, lo, hi):
    """Up to four convex pieces of a convex ring outside the box's footprint."""
    pieces = []
    left = clip(ring, 0, lo[0], True)
    pieces.append(left)
    rest = clip(ring, 0, lo[0], False)
    pieces.append(clip(rest, 0, hi[0], False) if rest else None)
    mid = clip(rest, 0, hi[0], True) if rest else None
    if mid:
        pieces.append(clip(mid, 1, lo[1], True))
        pieces.append(clip(mid, 1, hi[1], False))
    return [t for t in (_tidy(p) for p in pieces if p) if t]


# -- the splice -----------------------------------------------------------------------

def _in_zone(poly, lo, hi):
    """True when a polygon overlaps the zone's footprint at the zone's height."""
    xs = [v[0] for v in poly]
    ys = [v[1] for v in poly]
    zc = sum(v[2] for v in poly) / len(poly)
    return (min(xs) < hi[0] and max(xs) > lo[0] and min(ys) < hi[1] and max(ys) > lo[1]
            and lo[2] <= zc <= hi[2])


def _supported(poly, ours):
    centre, _r = navsurfgen.centre_and_radius(poly)
    for p in ours:
        if navsurf._inside_xy(p, centre[0], centre[1]) and \
                abs(navsurfgen.plane_z(p, centre[0], centre[1]) - centre[2]) <= SUPPORT_DZ:
            return True
    return False


def _split_sides(rings, boxes, meta):
    """Rings whose vertices near a box side are snapped onto it, at positions shared by
    every ring (clusters within SNAP), and whose edges along a side are split at every
    other ring's vertex there, so each shared edge matches exactly."""
    lines = sorted({(axis, float(value)) for lo, hi in boxes for axis in (0, 1)
                    for value in (lo[axis], hi[axis])})
    along = collections.defaultdict(list)
    for ring in rings:
        for r in ring:
            for axis, value in lines:
                if abs(r[0][axis] - value) < SNAP:
                    along[(axis, value)].append(r[0][1 - axis])
    reps = {}
    for line, vals in along.items():
        vals.sort()
        clusters = []
        for v in vals:
            if clusters and v - clusters[-1][-1] < SNAP:
                clusters[-1].append(v)
            else:
                clusters.append([v])
        reps[line] = [c[0] for c in clusters]

    def snap(v, poly):
        x, y = v[0], v[1]
        for axis, value in lines:
            if abs((x, y)[axis] - value) < SNAP:
                other = (x, y)[1 - axis]
                rep = min(reps[(axis, value)], key=lambda c: abs(c - other))
                x, y = (value, rep) if axis == 0 else (rep, value)
        return (x, y, navsurfgen.plane_z(poly, x, y))

    out, out_meta = [], []
    for ring, tag_meta in zip(rings, meta):
        poly = [r[0] for r in ring]
        snapped = [(snap(r[0], poly), r[1], r[2]) for r in ring]
        snapped = _tidy(snapped)
        if not snapped:
            continue
        new = []
        n = len(snapped)
        for k in range(n):
            p, q = snapped[k], snapped[(k + 1) % n]
            new.append(p)
            for axis, value in lines:
                if p[0][axis] == value and q[0][axis] == value:
                    a, b = p[0][1 - axis], q[0][1 - axis]
                    mids = sorted((c for c in reps.get((axis, value), ())
                                   if min(a, b) < c < max(a, b)), reverse=(b < a))
                    for m in mids:
                        x, y = (value, m) if axis == 0 else (m, value)
                        new.append(((x, y, navsurfgen.plane_z(poly, x, y)), NEW_TAG, p[2]))
                    break
        out.append(new)
        out_meta.append(tag_meta)
    out = _mend(out)
    capped, capped_meta = [], []
    for ring, tag_meta in zip(out, out_meta):
        pieces = _cap(ring)
        capped += pieces
        capped_meta += [tag_meta] * len(pieces)
    return capped, capped_meta


def _mend(rings):
    """Rings with every vertex that lies inside another ring's edge (within EPS in xy,
    STEP_Z in height) inserted into that edge: no T-junctions."""
    cell = 200.0
    grid = collections.defaultdict(set)
    for ri, ring in enumerate(rings):
        for r in ring:
            grid[(int(r[0][0] // cell), int(r[0][1] // cell))].add(ri)
    out = []
    for ri, ring in enumerate(rings):
        poly = [r[0] for r in ring]
        new = []
        n = len(ring)
        for k in range(n):
            p, q = ring[k], ring[(k + 1) % n]
            new.append(p)
            a, b = p[0], q[0]
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            if not length:
                continue
            near = set()
            for gx in range(int(min(a[0], b[0]) // cell), int(max(a[0], b[0]) // cell) + 1):
                for gy in range(int(min(a[1], b[1]) // cell),
                                int(max(a[1], b[1]) // cell) + 1):
                    near |= grid.get((gx, gy), set())
            found = {}
            for oi in near:
                if oi == ri:
                    continue
                for r in rings[oi]:
                    v = r[0]
                    t = ((v[0] - a[0]) * dx + (v[1] - a[1]) * dy) / (length * length)
                    if t * length <= EPS or (1 - t) * length <= EPS:
                        continue
                    if abs((v[0] - a[0]) * dy - (v[1] - a[1]) * dx) / length > EPS:
                        continue
                    if abs(a[2] + t * (b[2] - a[2]) - v[2]) > STEP_Z:
                        continue
                    found.setdefault(round(t * length / EPS), (t, v))
            for t, v in sorted(found.values()):
                new.append(((v[0], v[1], navsurfgen.plane_z(poly, v[0], v[1])), NEW_TAG, p[2]))
        out.append(new)
    return out


def _cap(ring):
    """A convex ring cut along diagonals until no piece has more than MAX_VERTS."""
    if len(ring) <= MAX_VERTS:
        return [ring]
    half = len(ring) // 2
    first = ring[:half + 1]
    second = ring[half:] + ring[:1]
    #: the diagonal is a new edge on both pieces
    first[-1] = (first[-1][0], first[-1][1], None)
    second[-1] = (second[-1][0], second[-1][1], None)
    return _cap(first) + _cap(second)


def splice(polys, ids, level_name, plan, graph, step):
    """(polys, ids, tags, flags, note): `polys` with each door zone's floor replaced by
    the shipped graph's polygons there."""
    from riftlord.build.navigation import navregen
    boxes = zones(level_name, plan)
    if not boxes:
        return polys, ids, [None] * len(polys), [navregen.DEFAULT_FLAGS] * len(polys), \
            'no door zones'
    shipped = shipped_graph(level_name, graph)
    rings, flags = [], []
    kept = 0
    for verts, sids, tags, flag in shipped:
        for lo, hi in boxes:
            if not _in_zone(verts, lo, hi) or not _supported(verts, polys):
                continue
            ring = inside_box([(v, t, e or None) for v, t, e in zip(verts, tags, sids)],
                              lo, hi)
            if ring:
                if [r[0] for r in ring] != list(verts):
                    #: a clipped polygon's corners are no longer the shipped corners
                    ring = [(r[0], NEW_TAG, r[2]) for r in ring]
                rings.append(ring)
                flags.append(flag)
                kept += 1
    first_ours = len(rings)
    for verts in polys:
        pieces = [[(v, NEW_TAG, None) for v in verts]]
        for lo, hi in boxes:
            nxt = []
            for ring in pieces:
                if _in_zone([r[0] for r in ring], lo, hi):
                    nxt += outside_box(ring, lo, hi)
                else:
                    nxt.append(ring)
            pieces = nxt
        rings += pieces
        flags += [navregen.DEFAULT_FLAGS] * len(pieces)
    meta = [(f, i < first_ours) for i, f in enumerate(flags)]
    rings, meta = _split_sides(rings, boxes, meta)
    flags = [m[0] for m in meta]
    shipped_ring = [m[1] for m in meta]
    out_polys = [[r[0] for r in ring] for ring in rings]
    out_ids = navsurfgen.allocate_edge_ids(out_polys, step=step)
    #: edges on a zone side still unpaired join across up to ZONE_STEP
    nxt = max([e for row in out_ids for e in row] + [0]) + 1
    sides = {(axis, value) for lo, hi in boxes for axis in (0, 1)
             for value in (lo[axis], hi[axis])}
    open_edges = {}
    for i, poly in enumerate(out_polys):
        n = len(poly)
        for k in range(n):
            a, b = poly[k], poly[(k + 1) % n]
            if out_ids[i][k] or not any(abs(a[ax] - v) < EPS and abs(b[ax] - v) < EPS
                                        for ax, v in sides):
                continue
            key = (round(a[0] / EPS), round(a[1] / EPS), round(b[0] / EPS),
                   round(b[1] / EPS))
            back = (key[2], key[3], key[0], key[1])
            mate = open_edges.get(back)
            z = (a[2] + b[2]) / 2.0
            if mate and mate[0] != i and abs(mate[2] - z) <= ZONE_STEP \
                    and not out_ids[mate[0]][mate[1]]:
                out_ids[i][k] = out_ids[mate[0]][mate[1]] = nxt
                nxt += 1
                del open_edges[back]
            else:
                open_edges[key] = (i, k, z)
    #: shipped pairs inside a zone that do not meet exactly keep their pairing
    by_src = collections.defaultdict(list)
    for i, ring in enumerate(rings):
        if not shipped_ring[i]:
            continue
        n = len(ring)
        for k, r in enumerate(ring):
            if r[2] and not out_ids[i][k]:
                a, b = ring[k][0], ring[(k + 1) % n][0]
                by_src[r[2]].append((i, k, ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)))
    nxt = max([e for row in out_ids for e in row] + [0]) + 1
    for members in by_src.values():
        if len(members) == 2 and math.dist(members[0][2], members[1][2]) <= EDGE_MEET:
            for i, k, _m in members:
                out_ids[i][k] = nxt
            nxt += 1
    tags = [[r[1] for r in ring] for ring in rings]
    tags = [t if shipped_ring[i] else None for i, t in enumerate(tags)]
    note = '%d door zone(s), %d shipped polygon(s) kept' % (len(boxes), kept)
    return out_polys, out_ids, tags, flags, note


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--level', required=True)
    args = ap.parse_args(argv)
    for lo, hi in zones(args.level):
        print('zone', [round(v) for v in lo], [round(v) for v in hi])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
