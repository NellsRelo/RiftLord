"""Add walkable floor to a level's navgraph where a Map Editor plan adds floor pieces.

Added floor-class pieces (navsurfcarve.added_floor) are tiled into square cells on a
grid anchored to each touching group of pieces; a cell goes in only where no existing
walkable polygon lies at that height. Cells share edge ids with each other
(navsurfgen.allocate_edge_ids), and each cell edge that faces a boundary edge of the
existing floor across a short gap gets a join quad sharing an id with both. Existing
records are kept byte for byte apart from the edge ids and across-pointers of joined
edges; no vertex is added to them. New records go after them, so no offset moves.

API: `cells_for(polys, boxes)` -> quads; `add_buffer(buf, boxes)` -> (buf or None,
notes, stats).
"""

import collections
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navsurf  # noqa: E402
from riftlord.build.navigation import navsurfextend  # noqa: E402
from riftlord.build.navigation import navsurfgen  # noqa: E402

#: Builds add walkable floor under added floor pieces only when this is True.
ENABLED = False

#: Side of an added-floor cell, in uu (the kit grid).
CELL = 300.0
#: A cell is left out when existing floor lies under any of these points, as fractions
#: of its side (corners inset, edge midpoints, centre).
SAMPLES = (0.02, 0.5, 0.98)
#: Pieces this close in xy, with tops within a step, are one group, in uu.
TOUCH = 20.0
#: Widest gap a join quad spans between a cell edge and the existing floor, in uu.
JOIN_GAP = 330.0
#: Narrowest gap a join quad spans, in uu.
JOIN_MIN_GAP = 2.0
#: Largest angle between a cell edge and the existing edge it joins, in degrees.
JOIN_ANGLE = 15.0
#: Share of a cell edge the existing edge's projection must cover to join.
JOIN_OVERLAP = 0.5
#: Flags word of an added record (navsurfextend.CORRIDOR_FLAGS).
FLAGS = navsurfextend.CORRIDOR_FLAGS


def _groups(boxes):
    """Boxes grouped by touching in xy with tops within a step."""
    parent = list(range(len(boxes)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, (alo, ahi) in enumerate(boxes):
        for j in range(i + 1, len(boxes)):
            blo, bhi = boxes[j]
            if (alo[0] - TOUCH <= bhi[0] and blo[0] - TOUCH <= ahi[0]
                    and alo[1] - TOUCH <= bhi[1] and blo[1] - TOUCH <= ahi[1]
                    and abs(ahi[2] - bhi[2]) <= navsurfgen.STEP_HEIGHT):
                parent[find(i)] = find(j)
    out = collections.defaultdict(list)
    for i in range(len(boxes)):
        out[find(i)].append(boxes[i])
    return list(out.values())


def cells_for(polys, boxes):
    """Counter-clockwise quads over added floor that no existing polygon covers."""
    out = []
    for group in _groups(boxes):
        ox = min(lo[0] for lo, _hi in group)
        oy = min(lo[1] for lo, _hi in group)
        x1 = max(hi[0] for _lo, hi in group)
        y1 = max(hi[1] for _lo, hi in group)
        for i in range(int(math.ceil((x1 - ox) / CELL))):
            for j in range(int(math.ceil((y1 - oy) / CELL))):
                cx, cy = ox + (i + 0.5) * CELL, oy + (j + 0.5) * CELL
                tops = [hi[2] for lo, hi in group
                        if lo[0] <= cx <= hi[0] and lo[1] <= cy <= hi[1]]
                if not tops:
                    continue
                z = max(tops)
                x0, y0 = ox + i * CELL, oy + j * CELL
                covered = any(
                    navsurfextend.on_surface(polys, (x0 + fx * CELL, y0 + fy * CELL, z))
                    is not None for fx in SAMPLES for fy in SAMPLES)
                if covered:
                    continue
                cell = [(x0, y0, z), (x0 + CELL, y0, z), (x0 + CELL, y0 + CELL, z),
                        (x0, y0 + CELL, z)]
                if not overlaps(cell, polys):
                    out.append(cell)
    return out


def _joins(polys, ids, cells, cell_ids, agent):
    """[(quad, (poly, edge), (cell, edge))] join quads from cell edges to existing
    boundary edges they face across a short gap."""
    used, out = set(), []
    min_edge = navsurfextend.MIN_JOIN_EDGE[agent]
    for c, cell in enumerate(cells):
        for m in range(4):
            if cell_ids[c][m]:
                continue
            p, q = cell[m], cell[(m + 1) % 4]
            ln = math.dist(p[:2], q[:2])
            ux, uy = (q[0] - p[0]) / ln, (q[1] - p[1]) / ln
            nx, ny = uy, -ux                   # outward normal of a CCW edge
            best = None
            for i, verts in enumerate(polys):
                n = len(verts)
                for k in range(n):
                    if ids[i][k] or (i, k) in used:
                        continue
                    a, b = verts[k], verts[(k + 1) % n]
                    el = math.dist(a[:2], b[:2])
                    if el < min_edge or el > ln + 2 * JOIN_GAP:
                        continue
                    ex, ey = (b[0] - a[0]) / el, (b[1] - a[1]) / el
                    # opposite direction, near parallel
                    if -(ex * ux + ey * uy) < math.cos(math.radians(JOIN_ANGLE)):
                        continue
                    gap_a = (a[0] - p[0]) * nx + (a[1] - p[1]) * ny
                    gap_b = (b[0] - p[0]) * nx + (b[1] - p[1]) * ny
                    if not (JOIN_MIN_GAP <= min(gap_a, gap_b)
                            and max(gap_a, gap_b) <= JOIN_GAP):
                        continue
                    if abs((a[2] + b[2]) / 2 - p[2]) > navsurfgen.STEP_HEIGHT:
                        continue
                    ta = (a[0] - p[0]) * ux + (a[1] - p[1]) * uy
                    tb = (b[0] - p[0]) * ux + (b[1] - p[1]) * uy
                    over = min(max(ta, tb), ln) - max(min(ta, tb), 0.0)
                    if over < JOIN_OVERLAP * ln:
                        continue
                    quad = [tuple(b), tuple(a), tuple(q), tuple(p)]
                    if not (navsurfextend._ccw(quad) and navsurfextend._convex(quad)):
                        continue
                    centre = tuple(sum(v[j] for v in quad) / 4 for j in range(3))
                    if navsurfextend.on_surface(polys, centre) is not None \
                            or overlaps(quad, polys) or overlaps(quad, cells):
                        continue
                    score = (gap_a + gap_b) / 2
                    if best is None or score < best[0]:
                        best = (score, quad, (i, k))
            if best:
                used.add(best[2])
                out.append((best[1], best[2], (c, m)))
    return out


def add_graph(buf, head, boxes, agent):
    """(polys, record bytes, stats) for one agent's graph with added floor, or
    (None, None, stats) when there is none to add."""
    from riftlord.build.navigation.navsurfcarve import _record_bytes
    secs = navsurf.sections(buf, head)
    polys = [list(s['verts']) for s in secs]
    ids = [list(s['edge_ids']) for s in secs]
    cells = cells_for(polys, boxes)
    if not cells:
        return None, None, {'cells': 0, 'joins': 0}
    base = 1 + max([e for row in ids for e in row] or [0])
    cell_ids = [[e + base - 1 if e else 0 for e in row]
                for row in navsurfgen.allocate_edge_ids(cells)]
    nxt = 1 + max([e for row in ids + cell_ids for e in row] or [0])
    joins = _joins(polys, ids, cells, cell_ids, agent)
    quads, quad_ids = [], []
    for quad, (i, k), (c, m) in joins:
        ids[i][k] = nxt
        cell_ids[c][m] = nxt + 1
        quads.append(quad)
        quad_ids.append([nxt, 0, nxt + 1, 0])
        nxt += 2
    new_polys = cells + quads
    new_ids = cell_ids + quad_ids
    # the last record's final edge has no room for an id; its partner becomes a wall
    if new_ids[-1][-1]:
        lost = new_ids[-1][-1]
        new_ids = [[0 if e == lost else e for e in row] for row in new_ids]
        ids = [[0 if e == lost else e for e in row] for row in ids]
    all_ids = ids + new_ids
    sizes = [len(p) for p in polys + new_polys]
    offsets, at = [], navsurf.RECORDS_BEGIN
    for n, size in enumerate(sizes):
        offsets.append(at)
        at += navsurfgen._record_size(size, last=(n == len(sizes) - 1))
    d_heads, d_nbrs = navsurfgen.derive_pointers(all_ids, offsets, sizes)
    recs = b''
    for i, s in enumerate(secs):
        head_ptr = struct.unpack_from('<Q', buf, s['at'] + 40)[0]
        nbrs = list(s['neighbours'])
        for k in range(len(polys[i])):
            if ids[i][k] != s['edge_ids'][k]:
                where, slot = navsurfgen.pointer_slot(k)
                ptr = d_heads[i] if where == 'head' else d_nbrs[i][slot]
                if where == 'head':
                    head_ptr = ptr
                else:
                    nbrs[slot] = ptr
        recs += _record_bytes(buf, s, ids[i], head_ptr, nbrs, False)
    for n, verts in enumerate(new_polys):
        j = len(secs) + n
        recs += navsurfgen._record(verts, None, flags=FLAGS, last=(j == len(sizes) - 1),
                                   edge_ids=all_ids[j], neighbours=d_nbrs[j],
                                   head_pointer=d_heads[j], graph=agent)
    return polys + new_polys, recs, {'cells': len(cells), 'joins': len(quads)}


#: Shortest edge an added record may have, in uu (shipped graphs have few under 5).
MIN_EDGE = 5.0
#: Largest overlap an added polygon may have with any other at its height, in uu^2.
MAX_OVERLAP = 25.0


def _overlap(a, b):
    """xy area of convex `a` inside convex counter-clockwise `b`."""
    from riftlord.build.navigation import navobstruct
    return navobstruct._area2(navobstruct._clip_convex([(v[0], v[1]) for v in a],
                                                       [(v[0], v[1]) for v in b]))


def overlaps(poly, others):
    """True when convex `poly` overlaps any of `others` at its height by more than
    MAX_OVERLAP."""
    z = sum(v[2] for v in poly) / len(poly)
    xs, ys = [v[0] for v in poly], [v[1] for v in poly]
    for other in others:
        if abs(sum(v[2] for v in other) / len(other) - z) > navsurfgen.STEP_HEIGHT:
            continue
        if (min(v[0] for v in other) >= max(xs) or max(v[0] for v in other) <= min(xs)
                or min(v[1] for v in other) >= max(ys)
                or max(v[1] for v in other) <= min(ys)):
            continue
        if _overlap(poly, other) > MAX_OVERLAP:
            return True
    return False


def check(before, after):
    """Problems with a graph `add_buffer` wrote, against the M1b invariants: the shipped
    records keep their bytes except the edge ids and across-pointers of joined edges
    (and the old last record's added tail); each id is quoted an even number of times;
    every non-zero pointer sits in its edge's slot (navsurfgen.pointer_slot) and names a
    record carrying the same id; added polygons are convex, counter-clockwise, have no
    edge under MIN_EDGE and overlap nothing at their height."""
    from riftlord.build.navigation import navsurfcarve
    out = list(navsurfcarve.check(after))
    for agent, (hb, ha) in enumerate(zip(navsurf.graphs(before), navsurf.graphs(after))):
        old, new = navsurf.sections(before, hb), navsurf.sections(after, ha)
        tag = 'agent %d' % agent
        if len(new) < len(old):
            out.append('%s: records lost' % tag)
            continue
        for i, (s, t) in enumerate(zip(old, new)):
            if s['at'] - hb['origin'] != t['at'] - ha['origin']:
                out.append('%s: record %d moved' % (tag, i))
                break
            if s['verts'] != t['verts']:
                out.append('%s: record %d vertices changed' % (tag, i))
            a = before[s['at']:s['at'] + navsurf.SECTION_FIXED]
            b = after[t['at']:t['at'] + navsurf.SECTION_FIXED]
            if a[:40] != b[:40]:
                out.append('%s: record %d header changed' % (tag, i))
            n = len(s['verts'])
            for k in range(n - (1 if s['truncated'] else 0)):
                va = s['at'] + navsurf.SECTION_FIXED + navsurf.VERTEX_STRIDE * k
                vb = t['at'] + navsurf.SECTION_FIXED + navsurf.VERTEX_STRIDE * k
                if before[va:va + 16] != after[vb:vb + 16]:
                    out.append('%s: record %d vertex %d tag changed' % (tag, i, k))
            for k in range(n):
                if s['edge_ids'][k] != t['edge_ids'][k] and s['edge_ids'][k]:
                    out.append('%s: record %d shipped edge id %d replaced'
                               % (tag, i, k))
        offs = {t['at'] - ha['origin']: j for j, t in enumerate(new)}
        for j, t in enumerate(new):
            n = len(t['verts'])
            head = struct.unpack_from('<Q', after, t['at'] + 40)[0]
            slots = [head] + list(t['neighbours'][:n - 1])
            for k in range(n):
                ptr, e = slots[k], t['edge_ids'][k]
                if not ptr:
                    continue
                if ptr not in offs:
                    out.append('%s: record %d edge %d pointer resolves to no record'
                               % (tag, j, k))
                elif j >= len(old) or t['edge_ids'][k] != old[j]['edge_ids'][k]:
                    target = new[offs[ptr]]
                    if not e or e not in target['edge_ids']:
                        out.append('%s: record %d edge %d pointer names a record without '
                                   'its id' % (tag, j, k))
        for j in range(len(old), len(new)):
            verts = new[j]['verts']
            if not (navsurfextend._ccw(verts) and navsurfextend._convex(verts)):
                out.append('%s: added record %d not convex and counter-clockwise'
                           % (tag, j))
            short = [k for k in range(len(verts))
                     if math.dist(verts[k][:2], verts[(k + 1) % len(verts)][:2]) < MIN_EDGE]
            if short:
                out.append('%s: added record %d has %d edge(s) under %.0f uu'
                           % (tag, j, len(short), MIN_EDGE))
            z = sum(v[2] for v in verts) / len(verts)
            for i, other in enumerate(new):
                if i == j:
                    continue
                oz = sum(v[2] for v in other['verts']) / len(other['verts'])
                if abs(oz - z) > navsurfgen.STEP_HEIGHT:
                    continue
                got = _overlap(verts, other['verts'])
                if got > MAX_OVERLAP:
                    out.append('%s: added record %d overlaps record %d by %.0f uu^2'
                               % (tag, j, i, got))
    return out


def add_buffer(buf, boxes):
    """(new buffer or None when nothing is added, notes, [stats per agent])."""
    if not boxes:
        return None, [], []
    heads = navsurf.graphs(buf)
    graphs, stats = [], []
    for agent, h in enumerate(heads):
        polys, recs, st = add_graph(buf, h, boxes, agent)
        if polys is None:
            secs = navsurf.sections(buf, h)
            polys = [list(s['verts']) for s in secs]
            recs = bytes(buf[secs[0]['at']:h['tree_at']])
        graphs.append((polys, None, None, recs))
        stats.append(st)
    if not any(s['cells'] for s in stats):
        return None, [], stats
    out = navsurfgen.buffer_per_agent(graphs, list(heads[0]['bbox']))
    notes = ['walkable floor added under %d added floor piece(s): %d cell(s), %d join(s)'
             % (len(boxes), stats[0]['cells'], stats[0]['joins'])]
    return out, notes, stats
