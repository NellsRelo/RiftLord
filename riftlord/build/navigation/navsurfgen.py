"""Generate a SURFACE navgraph from a set of walkable polygons.

    python3 navsurfgen.py --selftest
    python3 navsurfgen.py --demo

**What this has to achieve is much less than a correct navmesh.** Measured in game
(`surftest.py`, 2026-10-02): severing a shipped graph's connectivity did not stop a wave
-- ground units walk the authored `OMDPathPoint` chain -- while a graph the loader
REFUSES strands everything. So a converted map needs a navgraph that

1. **loads**: valid container, correct checksum, parseable records, a tree that names
   every record, and zeros where the shipped data has zeros; and
2. **covers** the walkable area, so a mover can be placed and locally steered.

An exact trapezoidal decomposition with correct adjacency is a refinement. This writes
the minimum thing that satisfies both, and `navsurf` reads back everything it writes.

## The layout it emits

One container, then TWO graphs -- the ordinary agent and the large one -- because every
shipped buffer has exactly two and the game resolves an agent to its own graph.

    container 24 bytes: 0, version 2, payload size, checksum, 0, flags 0x01000001
    graph at base G:
      G+24   65536            G+56  section A size     G+60  section B size
      G+72   56               G+80  340 + A + B        G+88  200.0 then the agent
      G+108  world bbox       G+132 2
      G+340  the 48-byte CONSTANT (identical on all 18 graphs measured) then 32 zeros
      G+420  records, A - 44 bytes
      G+420+A-44  the tree: 44-byte header then nodes
    next graph's base is G + total

**Sizes are not free parameters.** From the shipped data: the tree's `size` field is
`nodes + 4`, section B is `nodes + 32`, and the record stream is `A - 44` because the
last record carries neither its 24-byte tail nor its final vertex's 20-byte tail. Those
three relations reproduce Scenario_01's own 24520/1888/1860 exactly, which is the check
that they are right rather than merely consistent.

## Things measured the hard way, which an emitter must respect

* **The 24-byte record tail must be ZERO.** It is zero on every record of every shipped
  map; writing `0xA5A5A5A5` into one record's tail hangs and crashes the game. 24 bytes
  is three 64-bit fields -- almost certainly pointer slots.
* **The checksum is validated** per buffer: the sum of the payload's uint32 words
  (`navvol.checksum`). A stale one strands every agent that uses the buffer.
* **A record's radius is the BOUNDING radius** -- the max distance from its centre to its
  own vertices -- and that identity holds on all 27,218 shipped records. It is what
  `navsurf` validates, so an emitter that computes a mean radius produces a file its own
  reader rejects.
* **A tree leaf is a record OFFSET** from `G+80`, not an index.
* The flags byte is the index of the vertex farthest from edge 0's line in plan (`far_vertex`;
  2 for a triangle), matching 2,748/2,759 shipped records on three levels (the rest are float
  ties).
"""

import argparse
import collections
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navislands
from riftlord.build.navigation import navsurf
from riftlord.build.navigation import navvol

#: **Everything from +132 to +420 is a 288-byte CONSTANT**, byte-identical on all 18
#: graphs measured across 9 maps -- both agents of every map. Found by diffing every
#: byte of the header across graphs and keeping the ranges that never move, which is
#: also what corrected an earlier guess: the high-entropy run is 128 bytes starting at
#: +260, not 48 bytes starting at +340, and there is a `2` at +132 in front of it. It is
#: not content derived, so an emitter copies it, and that was the one unknown that could
#: have blocked generation outright.
TAIL_CONSTANT = bytes.fromhex(
    '0200000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    'bf14d67e2ddc8e6683ef574961ff698f61cdd11e9d9c167272e61df0844f4a77'
    '02d7e8392c53cbc9121e33749e0cf4d5d49fd4a4597e35cf3222f4cccfd3902d'
    '48d38f75e6d91d2ae5c0f72b788187440e5f5000d4618dbe7b0515073b33821f'
    '187092da6454ceb1853e6915f8466a0496730ed9162f6768d4f74a4ad0576876'
    '0000000000000000000000000000000000000000000000000000000000000000')

#: The five words at +88 for each of the two agents, as **exact bit patterns** rather
#: than decimal literals: 13.86 written from a Python float is 0x415dc28f and every
#: shipped graph carries 0x415dc290. One ULP, and the only way to see it is to diff the
#: generated header against a shipped one word for word -- the same trap `navvoltree`
#: documents for the tree's bbox. The floats are 200.0 (build scale, most likely the one
#: in OMD2's "runtime scale of %f does not match build scale of %f" warning), then the
#: agent: radius, max step height and height. 42/48/192 are OMD3's own AgentRadius,
#: AgentMaxStepHeight and AgentHeight from DefaultEngine.ini; 80/48/360 is the large one.
AGENT_WORDS = (
    (0x43480000, 0x415dc290, 0x42280000, 0x42400000, 0x43400000),
    (0x43480000, 0x41d33334, 0x42a00000, 0x42400000, 0x43b40000),
)
AGENTS = tuple(range(len(AGENT_WORDS)))

#: Header words that are the same on every shipped graph. **Split by whether the word
#: belongs to the graph at all**: for the second graph the bytes below +48 are the FIRST
#: graph's tree tail, not its own header -- diffing every byte across graphs shows +24
#: is never constant, and a shipped second graph carries a tree leaf word there rather
#: than 65536. Writing them for every graph silently truncates the previous graph's tree.
CONSTANTS_FIRST = {24: 65536, 32: 1, 40: 46, 44: 2}
CONSTANTS_ALL = {48: 46, 72: 56}

#: `+52` is the graph's OWN ORDINAL -- 0 for the first, 1 for the second -- which holds
#: on all 66 shipped graphs without exception. Writing 0 for both makes the large agent's
#: graph claim to be the first one, so an agent resolving to graph 1 gets graph 0's
#: decomposition or none. It is the only header word that is neither constant across
#: graphs nor a size, which is exactly why a constants-and-sizes diff slid past it.
OFF_GRAPH_INDEX = 52

#: Byte 1 of a record's flags word identifies WHICH GRAPH the record belongs to: 0x00 in
#: the first, 0x08 in the second. Measured on every record of all 33 maps -- 27,218 and
#: 11,917 -- with no exceptions. A per-graph constant hiding inside a per-record field,
#: which is why copying "the flags byte" preserved only the low byte and lost it.
RECORD_GRAPH_BYTE = (0x00, 0x08)

AXIS_TAG = (0x00000000, 0x10000000, 0x20000000)
LEAF = 0x80000000

#: The tree header's bbox is the graph header's expanded by exactly this on every side.
#: Measured on 12 graphs and exact on all of them -- and the graph header's own bbox is
#: that graph's OWN polygon extent, exactly. Writing one bbox into both graphs left the
#: large agent's polygons sticking out of their declared box, because each agent has its
#: own coarser decomposition and therefore its own extent.
TREE_BBOX_EXPAND = 0.02

#: Clips are widened by this, in uu, so the two half-spaces always overlap slightly.
#: Without it a node can have `clipA < clipB` and the gap between them is a DEAD ZONE
#: where neither child is descended: a point there finds nothing at all. Measured --
#: 27 of 503 interior points on Scenario_01 landed in one, every single one of them in a
#: polygon that is FLAT on z, where the two clips collapse onto the same value and
#: float32 rounding pushes the point outside both.
CLIP_EPSILON = 1.0
#: Shipped tree clips: a group's reach plus or minus this (uu), on every node measured.
TREE_CLIP_MARGIN = 0.02
#: Write NavPower's vertex tags on generated graphs: island cuts (-2), small-hole perimeters (-3).
ISLAND_TAGS = True
#: Axis extents closer than this count as a tie, won by the lower axis (uu).
TREE_AXIS_TIE = 0.01


def _f32(v):
    return struct.unpack('<f', struct.pack('<f', v))[0]

#: An axis whose centres spread less than this is not worth splitting on, and splitting
#: on it is what produces the degenerate clips above.
MIN_SPREAD = 1.0


def plane_z(verts, x, y):
    """The height of a polygon's own plane at (x, y)."""
    n = len(verts)
    nx = ny = nz = 0.0
    for i in range(n):
        a, b = verts[i], verts[(i + 1) % n]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    if abs(nz) < 1e-6:
        return sum(v[2] for v in verts) / float(n)
    d = nx * verts[0][0] + ny * verts[0][1] + nz * verts[0][2]
    return (d - nx * x - ny * y) / nz


def centre_and_radius(verts):
    """A record's centre and its bounding radius, **exactly** as shipped data has them.

    The centre is the **AREA CENTROID of the xy projection, lifted onto the polygon's
    own plane** -- not the vertex mean, and not a bounding-sphere centre. Measured:

    | definition | records matching the stored centre exactly |
    |---|---|
    | area centroid, z from the plane | **117/117, 755/755, 2448/2448** |
    | area centroid, z from the vertex mean | 104/117, 677/755, 1991/2448 |
    | vertex mean | 53/117 |
    | minimal bounding sphere | 0/117 |
    | perimeter centroid | 26/117 |
    | bbox centre | 24/117 |

    The radius then matches on every record of every map as well.

    **This was a bug that moved orcs.** Writing the vertex mean put a centre up to 326uu
    off, and in game the orc stepped onto the bridge, turned left towards the open side
    and attacked nothing -- a mover steering to a waypoint that was not where the polygon
    is. A triangle's area centroid IS its vertex mean, which is why the 27 triangles on
    Scenario_01 looked fine and hid the error.
    """
    n = len(verts)
    A = 0.0
    cx = cy = 0.0
    for i in range(n):
        a, b = verts[i], verts[(i + 1) % n]
        cr = a[0] * b[1] - b[0] * a[1]
        A += cr
        cx += (a[0] + b[0]) * cr
        cy += (a[1] + b[1]) * cr
    if abs(A) < 1e-9:
        centre = tuple(sum(v[i] for v in verts) / float(n) for i in (0, 1, 2))
    else:
        A *= 0.5
        x, y = cx / (6.0 * A), cy / (6.0 * A)
        centre = (x, y, plane_z(verts, x, y))
    return centre, max(math.dist(v, centre) for v in verts)


#: Two polygons may share an edge across a height difference up to this, which is
#: OMD3's own `AgentMaxStepHeight` from DefaultEngine.ini. Matching edges in full 3D
#: instead leaves a heightfield cover in 54 pieces with 16% of its area in the largest,
#: because neighbouring cells sit at different heights and their shared edge never
#: coincides; matching the xy projection with this tolerance is what a step actually is.
STEP_HEIGHT = 48.0


def allocate_edge_ids(polys, tol=2.0, step=STEP_HEIGHT):
    """[[id per vertex]] -- one shared id per edge two polygons actually share.

    **Zero means "this edge is a wall", and the mover obeys it.** A graph written with
    every id zero loads, places its movers and then boxes each orc inside its starting
    polygon: measured in game, they walk a little and attack as though a barricade were
    in the way. So ids are not optional for local movement, even though the long-range
    route comes from the authored path points.

    The convention, measured on Scenario_01 and unambiguous:

    * the id sits on the vertex that **starts** the edge -- vertex k owns edge k -> k+1
      -- 59 of 59 shared ids, and 0 of 59 on the vertex that ends it;
    * the shared edge appears **reversed** in the neighbour, 59 of 59, which is the
      ordinary manifold convention; and
    * every shipped polygon winds **counter-clockwise seen from above** (117 of 117).

    Only 26% of shipped edges carry an id at all; the rest really are boundaries.
    """
    #: keyed on the xy projection, so a step between two heights can still be an edge
    key = lambda v: (round(v[0] / tol), round(v[1] / tol))
    directed = {}
    for i, verts in enumerate(polys):
        n = len(verts)
        for k in range(n):
            directed.setdefault((key(verts[k]), key(verts[(k + 1) % n])), []).append(
                (i, k, (verts[k][2] + verts[(k + 1) % n][2]) / 2.0))
    ids = [[0] * len(v) for v in polys]
    nxt = 1
    for (a, b), mine in directed.items():
        for i, k, z in mine:
            if ids[i][k]:
                continue
            best = None
            for j, m, z2 in directed.get((b, a), ()):
                if j == i or ids[j][m]:
                    continue
                #: the nearest traversable neighbour, and never across a drop the agent
                #: cannot step up
                if abs(z2 - z) <= step and (best is None or abs(z2 - z) < best[0]):
                    best = (abs(z2 - z), j, m)
            if best is None:
                continue                  #: a genuine wall, or a drop: leave it zero
            _d, j, m = best
            ids[i][k] = ids[j][m] = nxt
            nxt += 1
    #: Enforce the invariant the shipped data satisfies without exception: 23,776 edge
    #: ids across 33 maps and **not one odd multiplicity**. A degenerate quad can give
    #: two of its own edges the same xy key and leave an id quoted an odd number of
    #: times, which a first grid cover did once. An edge left as a wall is a lesser
    #: wrong than a file that breaks an invariant every shipped graph keeps.
    counts = collections.Counter(e for row in ids for e in row if e)
    odd = {e for e, c in counts.items() if c % 2}
    if odd:
        for row in ids:
            for k, e in enumerate(row):
                if e in odd:
                    row[k] = 0
    return ids


#: Two edge endpoints are the same point within this (uu).
POINTER_MATCH = 0.5


def _has_edge(verts, a, b):
    """True when `verts` has the edge b -> a (the same edge, walked the other way)."""
    m = len(verts)
    for q in range(m):
        p0, p1 = verts[q], verts[(q + 1) % m]
        if all(abs(p0[c] - b[c]) <= POINTER_MATCH and abs(p1[c] - a[c]) <= POINTER_MATCH
               for c in range(3)):
            return True
    return False


def derive_pointers(edge_ids, offsets, vert_counts, polys=None):
    """(+40 per record, vertex pointers per record): the record across each shared edge.

    Every edge k that carries an id also carries a pointer to the record across it,
    stored in the slot BEFORE vertex k: `+40` for edge 0, vertex k-1's second u64 for
    edge k. The final vertex's pointer slot is always zero.

    **Edge ids are reused**: one id sits on several unrelated edges (Front Lawn's 1232
    on records 27, 37 and 38). The pointer names the record holding the SAME edge walked
    the other way, so with `polys` the candidate is chosen by geometry; without them the
    first record carrying the id is taken, which was right on 63,326 of 63,800 shared
    edges and wrong exactly where an id is reused.
    """
    where = {}
    for i, row in enumerate(edge_ids):
        for e in row:
            if e:
                where.setdefault(e, []).append(i)
    heads, verts = [], []
    for i, row in enumerate(edge_ids):
        slots = [0] * (vert_counts[i] + 1)
        for k, e in enumerate(row):
            across = [j for j in where.get(e, ()) if j != i] if e else []
            if polys is not None and len(across) > 1:
                a, b = polys[i][k], polys[i][(k + 1) % len(polys[i])]
                exact = [j for j in across if _has_edge(polys[j], a, b)]
                across = exact or across
            if across:
                slots[k] = offsets[across[0]] - navsurf.ORIGIN
        heads.append(slots[0])
        verts.append(slots[1:vert_counts[i]] + [0])
    return heads, verts


def pointer_slot(k):
    """('head', None) or ('vertex', k - 1): where edge k's across-pointer is stored."""
    return ('head', None) if k == 0 else ('vertex', k - 1)


def drop_last_edge_id(edge_ids):
    """Clear the last record's final edge id, and its partner, to keep the invariant.

    **The last record's final vertex is written as a bare position** -- it carries
    neither the `(0, -4)` tag nor its two 64-bit fields, which is exactly why the record
    stream is `A - 44` bytes. So an edge id belonging to that vertex cannot be stored:
    it would be written once, by the partner polygon, and the shipped invariant of **zero
    odd multiplicities across 23,776 ids** would be broken by the one edge the format has
    no room for.

    Both halves are cleared, so that edge becomes an honest wall.
    """
    out = [list(row) for row in edge_ids]
    if not out or not out[-1]:
        return out
    doomed = out[-1][-1]
    if not doomed:
        return out
    for row in out:
        for k, e in enumerate(row):
            if e == doomed:
                row[k] = 0
    return out


def _record_size(n, last=False):
    """How many bytes a record of `n` vertices occupies.

    A normal record is the 48-byte fixed part, `n` vertices of 32, and a 24-byte tail.
    The LAST record is 44 bytes shorter: its final vertex is written as a bare position
    (12 bytes rather than 32) and it carries no tail at all, because the tree header
    follows it immediately. That is why the stream is `A - 44`.
    """
    if not last:
        return 48 + 32 * n + 24
    return 48 + 32 * (n - 1) + 12


def neighbour_pointers(edge_ids, offsets):
    """DO NOT USE: puts edge k's pointer in slot k, one slot late. `derive_pointers`
    has the measured rule.

    **Writing this field crashed the game at the load screen.** The guess was that it
    names the polygon across the same edge, derived from the edge ids. It does not:
    measured on Scenario_01, **0 of the 89 resolving pointers target the partner of its
    own vertex's edge id**, and the two fields sit on nearly disjoint slots -- 123
    vertices carry an id and no pointer, 81 a pointer and no id, only 9 both. One shipped
    pointer does not resolve to a record at all.

    So its values resolve to records (89 of 90) and its links are a subset of the
    edge-id graph, and yet it is not a per-edge neighbour. It stays **unknown**, and an
    emitter writes **zero** -- which is what shipped data has on 290 of 503 slots and
    what the build that loaded and walked had everywhere.

    The lesson is the expensive one: a field whose values look meaningful is not a field
    whose meaning is known, and filling one in on a guess is worse than leaving it zero.
    """
    where = {}
    for i, row in enumerate(edge_ids):
        for k, e in enumerate(row):
            if e:
                where.setdefault(e, []).append((i, k))
    out = [[0] * len(row) for row in edge_ids]
    for _e, members in where.items():
        if len(members) != 2:
            continue
        (i, k), (j, m) = members
        out[i][k] = offsets[j] - navsurf.ORIGIN
        out[j][m] = offsets[i] - navsurf.ORIGIN
    return out


def _record(verts, offsets, flags=2, last=False, edge_ids=None, neighbours=None,
            head_pointer=0, graph=0, tags=None):
    """One section record. `offsets` is unused except to keep the signature honest."""
    centre, radius = centre_and_radius(verts)
    n = len(verts)
    out = struct.pack('<fff', *centre) + struct.pack('<f', radius)
    out += struct.pack('<2I', 0xFFFFFFFF, 0)
    out += bytes([0x80 | n]) + bytes.fromhex('ffff01')
    out += struct.pack('<I', (flags << 24) | 0x00110000
                       | (RECORD_GRAPH_BYTE[graph] << 8))
    #: +32 is always zero; +40 is a RECORD POINTER, non-zero on about half of shipped
    #: records and resolving to a record start on 2,539 of 2,539 measured. It was
    #: documented as part of "four zero words" and written as zero, which is one of the
    #: two fields a regenerated graph was missing.
    out += struct.pack('<Q', 0) + struct.pack('<Q', head_pointer)
    for i, v in enumerate(verts):
        out += struct.pack('<fff', *v)
        if last and i == n - 1:
            break                      #: the final vertex keeps only its position
        #: per-vertex tag: -4 on most shipped vertices, -3 and -2 on some corners
        out += struct.pack('<2h', 0, tags[i] if tags else -4)
        #: the edge id for the edge STARTING at this vertex, then the pointer to the
        #: record across the NEXT edge (see derive_pointers)
        out += struct.pack('<2Q', (edge_ids[i] if edge_ids else 0),
                           (neighbours[i] if neighbours else 0))
    if not last:
        out += b'\x00' * 24            #: MUST be zero -- garbage here crashes the game
    return out


class _Node(object):
    __slots__ = ('axis', 'clip', 'kids', 'body', 'first')


def _build_tree(items, depth=0):
    """A kd-tree over (offset, centre, lo, hi) items. Leaves name record offsets.

    **The clips bound the children's EXTENTS, not their centres.** That distinction is
    the whole difference between a tree that works and one that strands movers, and it
    cost four in-game runs to find:

    | tree | polygon centres located | interior points located |
    |---|---|---|
    | shipped | 117/117 | **503/503** |
    | clips from centres (wrong) | 117/117 | **58/503** |

    A polygon has extent, so it straddles split planes. Bounding the centres makes the
    two clips disjoint, exactly one child is ever descended, and a point inside a
    polygon but on the far side of the plane from that polygon's centre never finds it.
    A mover standing there has no surface under it: in game the orc walked onto the
    bridge, turned to the open side and attacked the air.

    Bounding the extents makes `clipA > clipB` wherever children overlap, both branches
    are searched there, and shipped data shows the same signature -- 3.4 leaves visited
    per query against 1.0 for the broken version.

    **Querying centres cannot catch this.** A centre is on the correct side by
    construction, so the broken tree scored 100% on it. Sample points across a
    polygon's area.
    """
    if len(items) == 1:
        return ('leaf', items[0][0])
    #: NavPower's rule, measured on every node of Scenario_01, 09 and DLC_02 (2,381):
    #: split the axis where the items' combined box is widest (a near-tie goes to the
    #: lower axis), sort by box midpoint, the left gets half rounded down, and each clip
    #: is the group's reach widened by TREE_CLIP_MARGIN.
    lo_all = [min(lo[ax] for _o, _c, lo, _hi in items) for ax in (0, 1, 2)]
    hi_all = [max(hi[ax] for _o, _c, _lo, hi in items) for ax in (0, 1, 2)]
    ext = [hi_all[ax] - lo_all[ax] for ax in (0, 1, 2)]
    axis = next(ax for ax in (0, 1, 2) if ext[ax] >= max(ext) - TREE_AXIS_TIE)
    items = sorted(items, key=lambda t: (t[2][axis] + t[3][axis]) * 0.5)
    half = len(items) // 2
    left, right = items[:half], items[half:]
    clip_a = _f32(_f32(max(hi[axis] for _o, _c, _lo, hi in left)) + TREE_CLIP_MARGIN)
    clip_b = _f32(_f32(min(lo[axis] for _o, _c, lo, _hi in right)) - TREE_CLIP_MARGIN)
    return ('node', axis, clip_a, clip_b,
            _build_tree(left, depth + 1), _build_tree(right, depth + 1))


def _serialise(node, origin):
    """(bytes, child word) for a subtree, in the volumetric graph's own encoding.

    A child is a leaf word or a link. A link carries the SPLIT AXIS OF THE NODE IT
    POINTS AT and the distance from that node's start to just past its own second child
    word -- `16 + len(first child's inline subtree)` -- which is the jump that skips the
    left subtree.
    """
    if node[0] == 'leaf':
        return b'', LEAF | (node[1] - origin)
    _k, axis, clip_a, clip_b, left, right = node
    b1, w1 = _serialise(left, origin)
    b2, w2 = _serialise(right, origin)
    body = struct.pack('<ff', clip_a, clip_b) + struct.pack('<I', w1) + b1
    body += struct.pack('<I', w2) + b2
    return body, AXIS_TAG[axis] | (16 + len(b1))


def far_vertex(verts):
    """A record's flags byte: the vertex farthest from edge 0's line in plan (the first on a
    tie); 2 for a triangle. Measured on Unchained and OMD3 (99.7%; the rest are float ties)."""
    (ax, ay), (bx, by) = verts[0][:2], verts[1][:2]
    reach = [abs((bx - ax) * (v[1] - ay) - (by - ay) * (v[0] - ax)) for v in verts]
    return reach.index(max(reach))


def _layout(polys, edge_ids=None, neighbours=None, flags=None, heads=None, graph=0,
            tags=None):
    """(records, tree nodes, root word, A, B, total) for one graph's content."""
    #: record offsets have to be known before the pointers that name them, so the
    #: stream is laid out twice: once to measure, once to write
    offsets, at = [], navsurf.RECORDS_BEGIN
    for i, verts in enumerate(polys):
        offsets.append(at)
        at += _record_size(len(verts), last=(i == len(polys) - 1))
    #: pointers are DERIVED unless a caller supplies them, because writing none of them
    #: left an orc turning off a bridge while the same tree with shipped pointers walked
    #: the route normally
    if neighbours is None and edge_ids is not None:
        heads, neighbours = derive_pointers(
            edge_ids, offsets, [len(v) for v in polys], polys)
    #: island cuts and small-hole perimeters (navislands): computed unless a caller supplies
    #: tags, as NavPower's builder does; without them every connected floor is one island
    #: and the planner's small-hole re-plan never runs
    if ISLAND_TAGS and neighbours is not None and not any(t for t in (tags or [])):
        index = {off - navsurf.ORIGIN: i for i, off in enumerate(offsets)}
        across = [[index.get(p) if p else None
                   for p in [heads[i] if heads else 0] + list(neighbours[i][:len(polys[i]) - 1])]
                  for i in range(len(polys))]
        tags = navislands.all_tags(across, polys)
    recs = b''
    items = []
    for i, verts in enumerate(polys):
        centre, _r = centre_and_radius(verts)
        lo = tuple(min(v[k] for v in verts) for k in (0, 1, 2))
        hi = tuple(max(v[k] for v in verts) for k in (0, 1, 2))
        items.append((offsets[i], centre, lo, hi))
        recs += _record(verts, None, last=(i == len(polys) - 1),
                        flags=(flags[i] if flags else far_vertex(verts)),
                        edge_ids=(edge_ids[i] if edge_ids else None),
                        neighbours=(neighbours[i] if neighbours else None),
                        head_pointer=(heads[i] if heads else 0), graph=graph,
                        tags=(tags[i] if tags else None))
    a_size = len(recs) + navsurf.LAST_RECORD_SHORT
    tree = _build_tree(items)
    if tree[0] == 'leaf':
        nodes, root = b'', LEAF | (tree[1] - navsurf.ORIGIN)
    else:
        nodes, root = _serialise(tree, navsurf.ORIGIN)
    #: these three relations come straight out of the shipped data and reproduce
    #: Scenario_01's own 24520 / 1888 / 1860 exactly
    b_size = len(nodes) + 32
    return recs, nodes, root, a_size, b_size, 340 + a_size + b_size


def bbox_of(polys):
    """A graph's own bbox: the exact extent of its own polygons."""
    lo = [min(v[k] for p in polys for v in p) for k in (0, 1, 2)]
    hi = [max(v[k] for p in polys for v in p) for k in (0, 1, 2)]
    return lo + hi


def _write_graph(buf, base, polys, bbox, agent, parts, first):
    """Write one graph's header, records and tree into `buf` at `base`.

    `bbox` is ignored in favour of this graph's OWN polygon extent, which is what every
    shipped graph carries -- exactly, to the float.
    """
    bbox = bbox_of(polys)
    recs, nodes, root, a_size, b_size, total = parts
    def w(off, val):
        struct.pack_into('<I', buf, base + off, val)
    def f(off, val):
        struct.pack_into('<f', buf, base + off, val)
    for off, val in CONSTANTS_ALL.items():
        w(off, val)
    w(OFF_GRAPH_INDEX, agent)
    if first:
        for off, val in CONSTANTS_FIRST.items():
            w(off, val)
    w(navsurf.OFF_SECTION_A, a_size)
    w(navsurf.OFF_SECTION_B, b_size)
    w(navsurf.OFF_SECTION_TOTAL, total)
    for i, word in enumerate(AGENT_WORDS[agent]):
        w(88 + 4 * i, word)
    for i, v in enumerate(bbox):
        f(108 + 4 * i, v)
    buf[base + 132:base + 420] = TAIL_CONSTANT
    at = base + navsurf.RECORDS_BEGIN
    buf[at:at + len(recs)] = recs
    t = base + navsurf.RECORDS_BEGIN + a_size - navsurf.LAST_RECORD_SHORT
    #: the tree's own box is the header's, expanded -- see TREE_BBOX_EXPAND
    tbox = [bbox[k] - TREE_BBOX_EXPAND for k in (0, 1, 2)] + \
           [bbox[3 + k] + TREE_BBOX_EXPAND for k in (0, 1, 2)]
    head = struct.pack('<3I', 0xFFFC0000, 0, 0) + struct.pack('<6f', *tbox)
    head += struct.pack('<I', len(nodes) + 4) + struct.pack('<I', root)
    buf[t:t + len(head)] = head
    buf[t + len(head):t + len(head) + len(nodes)] = nodes


def buffer_per_agent(graphs_in, bbox):
    """A buffer from a PER-AGENT list of (polys, edge_ids, flags, record_bytes).

    Every shipped buffer gives each agent its **own** polygon set -- Scenario_01 has 117
    for the ordinary agent and 65 for the large one, because a fatter agent gets a
    coarser decomposition. Writing one set into both graphs is unfaithful and inflates
    the buffer by a third, which matters because a size change is the one thing the
    injector cannot be a no-op for.

    `record_bytes`, when given, is used verbatim instead of serialising the polygons --
    which is how a graph can be rebuilt with ONLY its tree replaced.
    """
    parts = []
    for item in graphs_in:
        polys, ids, flags, recs = item[0], item[1], item[2], item[3]
        heads = item[4] if len(item) > 4 else None
        nbrs = item[5] if len(item) > 5 else None
        if recs is not None:
            parts.append(_layout_from_records(polys, recs))
        else:
            ids = drop_last_edge_id(ids if ids is not None
                                    else allocate_edge_ids(polys))
            tags = item[6] if len(item) > 6 else None
            parts.append(_layout(polys, ids, flags=flags, heads=heads,
                                 neighbours=nbrs, graph=len(parts), tags=tags))
    size = sum(p[5] for p in parts) + 48
    buf = bytearray(size)
    base = 0
    for i, (item, part) in enumerate(zip(graphs_in, parts)):
        _write_graph(buf, base, item[0], bbox, i, part, first=(i == 0))
        base += part[5]
    struct.pack_into('<6I', buf, 0, 0, 2, len(buf) - 24, 0, 0, 0x01000001)
    struct.pack_into('<I', buf, 28, len(buf) - 36)
    return navvol.reseal(bytes(buf))


def _layout_from_records(polys, recs):
    """Lay out a graph whose RECORD BYTES are given, building only the tree."""
    offsets, at = [], navsurf.RECORDS_BEGIN
    for i, verts in enumerate(polys):
        offsets.append(at)
        at += _record_size(len(verts), last=(i == len(polys) - 1))
    if at - navsurf.RECORDS_BEGIN != len(recs):
        raise ValueError('record bytes are %d, the polygons imply %d'
                         % (len(recs), at - navsurf.RECORDS_BEGIN))
    items = []
    for i, verts in enumerate(polys):
        centre, _r = centre_and_radius(verts)
        lo = tuple(min(v[k] for v in verts) for k in (0, 1, 2))
        hi = tuple(max(v[k] for v in verts) for k in (0, 1, 2))
        items.append((offsets[i], centre, lo, hi))
    a_size = len(recs) + navsurf.LAST_RECORD_SHORT
    tree = _build_tree(items)
    if tree[0] == 'leaf':
        nodes, root = b'', LEAF | (tree[1] - navsurf.ORIGIN)
    else:
        nodes, root = _serialise(tree, navsurf.ORIGIN)
    return recs, nodes, root, a_size, len(nodes) + 32, 340 + a_size + len(nodes) + 32


def buffer(polys, bbox, agents=AGENTS, edge_ids=None, flags=None):
    """A whole surface buffer: the container, both agents' graphs, sealed checksum.

    **The container is not prepended -- it IS the first 24 bytes of graph 1's 340-byte
    header.** Prepending one shifts every field by 24 bytes, which a diff against a
    shipped header catches instantly and which nothing else would: the emitted file is
    perfectly self-consistent and its own reader accepts it, because the reader derives
    the base from the same shifted fields.

    Consecutive graphs overlap by 48 bytes: graph 1's tree data ends at `G2+48`, and
    graph 2's earliest header field is the `46` at `G2+48`. So the second graph's base
    sits inside the first graph's tree tail, and the two abut exactly.
    """
    if not polys:
        raise ValueError('a graph needs at least one polygon')
    if edge_ids is None:
        edge_ids = allocate_edge_ids(polys)
    edge_ids = drop_last_edge_id(edge_ids)
    parts = [_layout(polys, edge_ids, flags=flags, graph=i)
             for i, _a in enumerate(agents)]
    totals = [p[5] for p in parts]
    #: the final graph's tree runs 48 bytes past its own base + total
    size = sum(totals) + 48
    buf = bytearray(size)
    base = 0
    for i, (agent, part) in enumerate(zip(agents, parts)):
        _write_graph(buf, base, polys, bbox, agent, part, first=(i == 0))
        base += part[5]
    #: The container and the inner size, written last because they overlap graph 1's
    #: header and because both are functions of the FINISHED buffer. Measured on every
    #: shipped map: +8 is `len - 24` (the payload after the 24-byte container) and +28
    #: is `len - 36` (that payload less 12).
    #:
    #: Getting these wrong is what made the first generated graphs load and strand every
    #: orc. They were the two fields a header diff could not catch, because a diff of
    #: sizes against a different-sized file has to be skipped -- so the FORMULA has to be
    #: checked against shipped data instead of the value. `+28` was being written as the
    #: first graph's total, 17,012 bytes short of the truth on a 34KB buffer.
    struct.pack_into('<6I', buf, 0, 0, 2, len(buf) - 24, 0, 0, 0x01000001)
    struct.pack_into('<I', buf, 28, len(buf) - 36)
    return navvol.reseal(bytes(buf))


def grid_cover(bbox, z, cell=600.0, agents=AGENTS):
    """A demo cover: axis-aligned quads tiling the bbox footprint at height `z`."""
    polys = []
    x = bbox[0]
    while x < bbox[3] - 1.0:
        y = bbox[1]
        x2 = min(x + cell, bbox[3])
        while y < bbox[4] - 1.0:
            y2 = min(y + cell, bbox[4])
            polys.append([(x, y, z), (x2, y, z), (x2, y2, z), (x, y2, z)])
            y = y2
        x = x2
    return polys


def selftest():
    """Write a graph, then read it back with `navsurf` and run its own checks."""
    bad = []
    bbox = [-1200.0, -1200.0, -50.0, 1200.0, 1200.0, 50.0]
    polys = grid_cover(bbox, 0.0, cell=600.0)
    if len(polys) < 4:
        bad.append('the demo cover produced only %d polygon(s)' % len(polys))
    buf = buffer(polys, bbox)
    if not navvol.verify(buf):
        bad.append('the emitted buffer does not satisfy its checksum')
    heads = navsurf.graphs(buf)
    if len(heads) != 2:
        bad.append('expected 2 graphs in the buffer, read %d' % len(heads))
    for h in heads:
        got = navsurf.sections(buf, h)
        if len(got) != len(polys):
            bad.append('graph +%d: wrote %d records, read %d'
                       % (h['base'], len(polys), len(got)))
            continue
        probs = navsurf.check_sections(buf, h, got)
        probs += navsurf.check_tree(buf, h, got, navsurf.tree(buf, h))
        bad.extend('graph +%d: %s' % (h['base'], p) for p in probs)
        #: the vertices must survive the round trip, not merely the counts
        for i, (want, back) in enumerate(zip(polys, got)):
            for v, w in zip(want, back['verts']):
                if max(abs(v[k] - w[k]) for k in (0, 1, 2)) > 0.01:
                    bad.append('graph +%d record %d: vertex moved' % (h['base'], i))
                    break
    #: no edge id may have an odd multiplicity -- the invariant shipped data keeps
    import collections as _c
    for h in heads:
        got = navsurf.sections(buf, h)
        counts = _c.Counter(e for s2 in got for e in s2['edge_ids'] if e)
        odd = [e for e, c in counts.items() if c % 2]
        if odd:
            bad.append('graph +%d: %d edge id(s) with an odd multiplicity'
                       % (h['base'], len(odd)))
    #: the 288-byte constant must land exactly where every shipped graph carries it
    for h in heads:
        if buf[h['base'] + 132:h['base'] + 420] != TAIL_CONSTANT:
            bad.append('graph +%d: the 288-byte constant is misplaced' % h['base'])
    #: The size FORMULAS, checked against shipped data rather than against a value.
    #: A header diff must skip these fields because the files differ in length, which is
    #: exactly the blind spot that shipped two broken graphs.
    if struct.unpack_from('<I', buf, 8)[0] != len(buf) - 24:
        bad.append('+8 payload size is %d, every shipped buffer uses len - 24 = %d'
                   % (struct.unpack_from('<I', buf, 8)[0], len(buf) - 24))
    if struct.unpack_from('<I', buf, 28)[0] != len(buf) - 36:
        bad.append('+28 inner size is %d, every shipped buffer uses len - 36 = %d'
                   % (struct.unpack_from('<I', buf, 28)[0], len(buf) - 36))
    #: and the header must agree with a SHIPPED one word for word wherever the word is
    #: a constant -- the check that caught the container being prepended
    try:
        ship = navsurf.buffer('Scenario_01')
    except BaseException:
        ship = None
    if ship:
        for off in sorted(CONSTANTS_FIRST) + sorted(CONSTANTS_ALL) + [132]:
            a = struct.unpack_from('<I', ship, off)[0]
            b = struct.unpack_from('<I', buf, off)[0]
            if a != b:
                bad.append('header +%d is 0x%08x, shipped graphs carry 0x%08x'
                           % (off, b, a))
        #: the agent block must match BIT FOR BIT, not merely to a few decimals
        for off in range(88, 108, 4):
            a = struct.unpack_from('<I', ship, off)[0]
            b = struct.unpack_from('<I', buf, off)[0]
            if a != b:
                bad.append('agent word +%d is 0x%08x, shipped graphs carry 0x%08x'
                           % (off, b, a))
    #: and every record tail must be zero, which is the one that crashed the game
    h = heads[0]
    for s in navsurf.sections(buf, h)[:-1]:
        tail = buf[s['at'] + 48 + 32 * len(s['verts']):s['at'] + s['size']]
        if any(tail):
            bad.append('record +%d has a non-zero tail' % s['at'])
            break
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--demo', action='store_true')
    ap.add_argument('--cell', type=float, default=600.0)
    args = ap.parse_args(argv)
    if args.selftest:
        bad = selftest()
        for b in bad:
            print('FAILED  %s' % b)
        print('navsurfgen selftest: %s'
              % ('passed' if not bad else '%d problem(s)' % len(bad)))
        return 1 if bad else 0
    if args.demo:
        bbox = [-3000.0, -3000.0, -100.0, 3000.0, 3000.0, 100.0]
        polys = grid_cover(bbox, 0.0, cell=args.cell)
        buf = buffer(polys, bbox)
        print('%d polygon(s) at %.0fuu -> %d byte buffer, checksum %s'
              % (len(polys), args.cell, len(buf),
                 'ok' if navvol.verify(buf) else 'BAD'))
        for h in navsurf.graphs(buf):
            got = navsurf.sections(buf, h)
            t = navsurf.tree(buf, h)
            probs = (navsurf.check_sections(buf, h, got)
                     + navsurf.check_tree(buf, h, got, t))
            print('   graph +%-7d agent %-5.0f records %5d  leaves %5d  %s'
                  % (h['base'], h['agent'][1], len(got), len(t['leaves']),
                     'verifies' if not probs else probs[0]))
        return 0
    ap.print_help()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
