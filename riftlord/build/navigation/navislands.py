"""NavPower's island partition of a walkable graph, and the vertex tags that record it.

An island is a connected set of at most CAP records; the planner keeps a graph of islands, and
the runtime builds islands at load by flooding across shared edges that do not carry the cut
flag. The flag is bit 17 of a vertex's +12 word (the int16 -2 at +14) on the START vertex of
the cut edge, on both sides.

The rule was decoded from OMD2's builder (NavPower 3.2.3, OrcsMustDie2.exe 0x62ecb0 and the
functions it calls) and reproduces every shipped cut: 2,905/2,905 on OMD2's 50 maps and
816/816 on OMD3's 33 levels (NavPower 6.1.5). workshop/research/omd2-islands/islandsim.py is the
reference copy with the addresses.

1. Seed: records in order; each unassigned record starts an island, filled breadth-first over
   its edges in order until it holds CAP records.
2. Refine: up to PASSES passes over the records in order while anything changes. For each edge
   of record F (island A) to a record N of another island B:
   A + B <= CAP -> merge B into A; A >= B + 2 -> move F to B if A stays connected without it;
   B >= A + 2 -> move N to A if B stays connected without it. The first change ends F's turn.
3. Cut every shared edge whose two records are in different islands.

The other vertex tag, -3 (bits 15-16 = edge type 2), marks the perimeter of a SMALL HOLE: every
wall edge of a closed wall loop wound against the polygons (a hole) with at most
SMALL_HOLE_EDGES edges. NavPower 6.1.5's planner (OMD3 0x143417be0) re-plans round the far side
of small holes a path wraps; the debug setting that draws them is "small_hole_perimeters".
Measured on all 33 OMD3 levels: 635/635 holes of <=26 edges tagged, 57/57 of >=28 untagged
(workshop/research/navpower-deathtrap/FINDINGS.md). 28 shipped tagged loops are outer loops of
small floor pieces; what selects those is unknown and they are not reproduced.
"""

import collections
import math

#: Most records an island holds (shipped islands never exceed it; OMD2's pile up against it).
CAP = 50
#: Most refinement passes.
PASSES = 10
#: The tag on a cut edge's start vertex, and on every other vertex.
CUT_TAG = -2
PLAIN_TAG = -4
#: The tag on every edge of a small hole's perimeter.
HOLE_TAG = -3
#: Most edges a hole may have and still be small (none shipped has 27; 28 and up are untagged).
SMALL_HOLE_EDGES = 26
#: Vertex positions that agree to this many decimals are one corner when chaining walls.
_CORNER_DECIMALS = 1

_UNSET = -1


def _grow(seed, isl, ring, iid):
    q = collections.deque([seed])
    count = 0
    while q:
        f = q.popleft()
        if isl[f] != _UNSET:
            continue
        isl[f] = iid
        count += 1
        if count >= CAP:
            break
        q.extend(n for n in ring[f] if isl[n] == _UNSET)


def _connected(a, b, isl, ring):
    if a == b:
        return True
    want = isl[a]
    seen = {a}
    q = collections.deque([a])
    while q:
        for y in ring[q.popleft()]:
            if y in seen or isl[y] != want:
                continue
            if y == b:
                return True
            seen.add(y)
            q.append(y)
    return False


def _can_leave(f, isl, ring):
    own = isl[f]
    nbrs = [n for n in ring[f] if isl[n] == own]
    if len(nbrs) <= 1:
        return True
    isl[f] = _UNSET
    ok = all(_connected(nbrs[i], nbrs[j], isl, ring)
             for i in range(len(nbrs) - 1) for j in range(i + 1, len(nbrs)))
    isl[f] = own
    return ok


def _relabel(start, new, isl, ring):
    old = isl[start]
    q = collections.deque([start])
    while q:
        x = q.popleft()
        if isl[x] != old:
            continue
        isl[x] = new
        q.extend(y for y in ring[x] if isl[y] == old)


def _refine(f, isl, ring, counts):
    for n in ring[f]:
        a, b = isl[f], isl[n]
        if a == b:
            continue
        if counts[a] + counts[b] <= CAP:
            _relabel(n, a, isl, ring)
            counts[a] += counts[b]
            counts[b] = 0
            return True
        if counts[a] >= counts[b] + 2:
            if _can_leave(f, isl, ring):
                isl[f] = b
                counts[a] -= 1
                counts[b] += 1
                return True
        elif counts[b] >= counts[a] + 2:
            if _can_leave(n, isl, ring):
                isl[n] = a
                counts[a] += 1
                counts[b] -= 1
                return True
    return False


def partition(neighbours):
    """Island id per record. `neighbours[i]` lists the record across each of record i's edges,
    in edge order, None for a wall."""
    ring = [[j for j in row if j is not None] for row in neighbours]
    isl = [_UNSET] * len(ring)
    iid = 0
    for f in range(len(ring)):
        if isl[f] == _UNSET:
            _grow(f, isl, ring, iid)
            iid += 1
    counts = collections.Counter(isl)
    for _ in range(PASSES):
        changed = False
        for f in range(len(ring)):
            changed |= _refine(f, isl, ring, counts)
        if not changed:
            break
    return isl


def tags(neighbours):
    """Per record, per vertex: CUT_TAG on the start vertex of every edge to another island."""
    isl = partition(neighbours)
    return [[CUT_TAG if j is not None and isl[i] != isl[j] else PLAIN_TAG for j in row]
            for i, row in enumerate(neighbours)]


def _corner(p):
    return tuple(round(c, _CORNER_DECIMALS) for c in p)


def _clockwise(back, here, ahead):
    """Clockwise angle at `here` from the direction to `back` round to the direction to
    `ahead`, in (0, 2pi]."""
    a = math.atan2(back[1] - here[1], back[0] - here[0])
    b = math.atan2(ahead[1] - here[1], ahead[0] - here[0])
    turn = (a - b) % (2 * math.pi)
    return turn or 2 * math.pi


def small_hole_edges(polys, walls):
    """{(record, edge)} on the perimeter of a small hole. `walls[i][k]` is True when edge k
    of polygon i (from vertex k to k+1) borders no other record."""
    edges = [(i, k) for i, row in enumerate(walls) for k, w in enumerate(row) if w]
    leaving = collections.defaultdict(list)
    for e in edges:
        leaving[_corner(polys[e[0]][e[1]])].append(e)
    used = set()
    out = set()
    for e in edges:
        if e in used:
            continue
        loop, cur, closed = [e], e, False
        used.add(e)
        first = _corner(polys[e[0]][e[1]])
        while True:
            i, k = cur
            end = _corner(polys[i][(k + 1) % len(polys[i])])
            if end == first:
                closed = True
                break
            nxt = [x for x in leaving.get(end, ()) if x not in used]
            if not nxt:
                break
            #: where loops touch at a corner, keep to the floor on the left: the next wall
            #: clockwise from the way we came (taking any other splices two loops together)
            back = polys[i][k]
            here = polys[i][(k + 1) % len(polys[i])]
            cur = min(nxt, key=lambda x: _clockwise(back, here,
                                                    polys[x[0]][(x[1] + 1) % len(polys[x[0]])]))
            used.add(cur)
            loop.append(cur)
        if not closed or len(loop) > SMALL_HOLE_EDGES:
            continue
        area = 0.0
        for i, k in loop:
            a, b = polys[i][k], polys[i][(k + 1) % len(polys[i])]
            area += a[0] * b[1] - b[0] * a[1]
        if area < 0:
            out.update(loop)
    return out


def all_tags(neighbours, polys):
    """tags() plus HOLE_TAG on small-hole perimeters."""
    out = tags(neighbours)
    walls = [[j is None for j in row] for row in neighbours]
    for i, k in small_hole_edges(polys, walls):
        out[i][k] = HOLE_TAG
    return out
