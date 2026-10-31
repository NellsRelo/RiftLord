"""Extend a level's walkable navgraph so it covers added ground routes.

A ground point or spawner that sits off the level's walkable polygons gets a corridor
of quads, one per route segment, joined to the existing graph on one of its boundary
edges. The level's own polygons, edge ids, flags and pointers are kept as they are and
the corridor's records are appended after them, so every existing record keeps its
offset. Both agents' graphs get a corridor.

    python3 navsurfextend.py --map Scenario_01 --route x,y,z x,y,z ...   # dry run

API: `plan_corridors(buf, chains)` -> report; `extend(uasset, uexp, chains)` ->
(uasset, uexp, report). A chain is the spawner's location followed by its route's
points, in walking order.
"""

import argparse
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.build.navigation import navsurf  # noqa: E402
from riftlord.build.navigation import navsurfgen  # noqa: E402

#: How far above and below a polygon's plane a point may sit and still count as on
#: it. Shipped spawners stand 30-207 uu above their floor and route points -14..73 uu
#: (5th-95th percentile, 82 spawners and 656 points over the shipped levels). Ground route
#: points over graph 0 (661 on the 33 Scenario levels, 2026-10-29): 653 have floor under
#: them, at -49 to 196 uu (1st-99th percentile -29 to 115) bar one at 866.
#: GRAPH_HEIGHT_SLACK: how far a regenerated graph's plane may sit off the floor it was built
#: from (recastgen DETAIL_ERROR 8 + navregen CELL_HEIGHT 5); Beset's side-door spawners stand
#: 245-250 uu over their floor and 253 uu over the regenerated plane.
GRAPH_HEIGHT_SLACK = 13.0
SURFACE_ABOVE = 250.0 + GRAPH_HEIGHT_SLACK
SURFACE_BELOW = 100.0

#: How far a spawner stands above its floor: the shipped median.
SPAWNER_ABOVE_FLOOR = 99.0

#: How far a ground route point stands above its floor (uu): the shipped median over
#: graph 0 (653 points, 33 levels).
POINT_ABOVE_FLOOR = 10.8

#: Corridor width per agent graph: the ordinary agent (radius 42) and the large one
#: (radius 80), with room either side.
CORRIDOR_WIDTH = (400.0, 480.0)

#: The shortest boundary edge a corridor may join, per agent: wider than the agent.
MIN_JOIN_EDGE = (120.0, 200.0)

#: How far from an off-floor point the joining edge may be.
MAX_JOIN_DISTANCE = 3000.0

#: Height difference allowed between a joining edge and the point it joins.
MAX_JOIN_DZ = 400.0

#: The steepest slope an agent can walk (DefaultEngine.ini AgentMaxSlope).
MAX_SLOPE_DEG = 45.0

#: How far the floor runs past a dead-end point such as a spawner.
END_MARGIN = 300.0

#: Flags byte written on corridor records (the generator's default).
CORRIDOR_FLAGS = 2

#: The widest gap a bridge quad may span between two separate pieces of floor that one
#: route leg joins (uu, between the facing edges' midpoints).
MAX_BRIDGE_GAP = 800.0


def _xy_inside(verts, x, y):
    return navsurf._inside_xy(verts, x, y)


def on_surface(polys, p):
    """Index of the polygon `p` stands on, or None."""
    best = None
    for i, verts in enumerate(polys):
        if not _xy_inside(verts, p[0], p[1]):
            continue
        dz = p[2] - navsurfgen.plane_z(verts, p[0], p[1])
        if -SURFACE_BELOW <= dz <= SURFACE_ABOVE and (best is None or abs(dz) < best[0]):
            best = (abs(dz), i)
    return best[1] if best else None


def _dist_point_segment(p, a, b):
    ax, ay = b[0] - a[0], b[1] - a[1]
    t = 0.0
    den = ax * ax + ay * ay
    if den > 0:
        t = max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / den))
    q = (a[0] + ax * t, a[1] + ay * t, a[2] + (b[2] - a[2]) * t)
    return math.hypot(p[0] - q[0], p[1] - q[1]), q


def join_edge(polys, ids, p, toward, agent, used):
    """(polygon, vertex) of the boundary edge to join `p` to, or None.

    A boundary edge carries edge id 0. It must be long enough for the agent, within
    reach, at a walkable height difference, and face `p` (its outward side).
    """
    best = None
    for i, verts in enumerate(polys):
        n = len(verts)
        for k in range(n):
            if ids[i][k] or (i, k) in used:
                continue
            a, b = verts[k], verts[(k + 1) % n]
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if length < MIN_JOIN_EDGE[agent]:
                continue
            d, q = _dist_point_segment(p, a, b)
            if d > MAX_JOIN_DISTANCE or abs(q[2] - p[2]) > MAX_JOIN_DZ:
                continue
            # counter-clockwise polygons: the outward normal of a->b is (dy, -dx)
            nx, ny = (b[1] - a[1]) / length, -(b[0] - a[0]) / length
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            if (toward[0] - mx) * nx + (toward[1] - my) * ny <= 0:
                continue
            if best is None or d < best[0]:
                best = (d, i, k)
    return (best[1], best[2]) if best else None


def _ccw(quad):
    area = 0.0
    for k in range(len(quad)):
        a, b = quad[k], quad[(k + 1) % len(quad)]
        area += a[0] * b[1] - b[0] * a[1]
    return area > 0


def _convex(quad):
    sign = 0
    n = len(quad)
    for k in range(n):
        a, b, c = quad[k], quad[(k + 1) % n], quad[(k + 2) % n]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) < 1e-6:
            return False
        s = 1 if cross > 0 else -1
        if sign and s != sign:
            return False
        sign = s
    return True


def _offsets(chain, width):
    """Left and right corridor edge points at every chain point (mitred joints)."""
    half = width / 2.0
    out = []
    n = len(chain)
    for i, p in enumerate(chain):
        dirs = []
        if i > 0:
            dirs.append((p[0] - chain[i - 1][0], p[1] - chain[i - 1][1]))
        if i < n - 1:
            dirs.append((chain[i + 1][0] - p[0], chain[i + 1][1] - p[1]))
        dx = sum(d[0] / (math.hypot(*d) or 1) for d in dirs)
        dy = sum(d[1] / (math.hypot(*d) or 1) for d in dirs)
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        out.append(((p[0] + nx * half, p[1] + ny * half, p[2]),
                    (p[0] - nx * half, p[1] - ny * half, p[2])))
    return out


def _quad(a_left, a_right, b_left, b_right):
    """A counter-clockwise quad over one segment, or None if it folds."""
    for quad in ([a_right, b_right, b_left, a_left], [a_left, b_left, b_right, a_right]):
        if _ccw(quad) and _convex(quad):
            return [tuple(v) for v in quad]
    return None


def _slope_ok(a, b):
    run = math.hypot(b[0] - a[0], b[1] - a[1])
    return run > 0 and math.degrees(math.atan2(abs(b[2] - a[2]), run)) <= MAX_SLOPE_DEG


def corridor(polys, ids, run, before, after, agent, used):
    """Quads for one off-floor run, plus the join edges. Raises ValueError with a
    plain reason when the run cannot be joined to the floor."""
    width = CORRIDOR_WIDTH[agent]
    edges = {}
    for end, neighbour in (('start', before), ('end', after)):
        if neighbour is None:
            continue
        p = run[0] if end == 'start' else run[-1]
        edge = join_edge(polys, ids, p, p, agent, used)
        if edge is None:
            raise ValueError('%s is off the walkable floor and no edge of the floor '
                             'within %d uu can be joined to it'
                             % (_fmt(p), MAX_JOIN_DISTANCE))
        i, k = edge
        a, b = polys[i][k], polys[i][(k + 1) % len(polys[i])]
        if not (_slope_ok(p, a) and _slope_ok(p, b)):
            raise ValueError('%s is too far above or below the floor it would join'
                             % _fmt(p))
        edges[end] = (i, k, a, b)
        used.add(edge)
    # the join edges' midpoints steer the corridor's ends
    mid = lambda e: ((e[2][0] + e[3][0]) / 2, (e[2][1] + e[3][1]) / 2,
                     (e[2][2] + e[3][2]) / 2)
    steer = ([mid(edges['start'])] if 'start' in edges else []) + list(run) + \
            ([mid(edges['end'])] if 'end' in edges else [])
    sides = _offsets(steer, width)
    if 'start' in edges:
        sides = sides[1:]
    if 'end' in edges:
        sides = sides[:-1]
    # an end with no join is a dead end: carry the floor past the point
    for end in ('start', 'end'):
        if end in edges or len(steer) < 2:
            continue
        here, there = (steer[0], steer[1]) if end == 'start' else (steer[-1], steer[-2])
        dx, dy = here[0] - there[0], here[1] - there[1]
        length = math.hypot(dx, dy) or 1.0
        push = (dx / length * END_MARGIN, dy / length * END_MARGIN)
        k = 0 if end == 'start' else -1
        sides[k] = tuple((v[0] + push[0], v[1] + push[1], v[2]) for v in sides[k])
    quads, joins = [], []
    for i in range(len(run) - 1):
        if not _slope_ok(run[i], run[i + 1]):
            raise ValueError('the slope between %s and %s is steeper than an enemy '
                             'can walk' % (_fmt(run[i]), _fmt(run[i + 1])))
        q = _quad(sides[i][0], sides[i][1], sides[i + 1][0], sides[i + 1][1])
        if q is None:
            raise ValueError('the route turns too sharply at %s' % _fmt(run[i + 1]))
        quads.append(q)
    for end, (i, k, a, b) in edges.items():
        p = run[0] if end == 'start' else run[-1]
        side = sides[0] if end == 'start' else sides[-1]
        # the shared edge runs b -> a in the corridor quad
        q = None
        for left, right in ((side[0], side[1]), (side[1], side[0])):
            cand = [tuple(b), tuple(a), left, right]
            if _ccw(cand) and _convex(cand):
                q = cand
                break
        if q is None:
            raise ValueError('%s cannot be joined to the floor without the corridor '
                             'folding; move it so open floor is in front of it'
                             % _fmt(p))
        joins.append((len(quads), 0, i, k))
        quads.append(q)
    return quads, joins


#: {(x, y) rounded: [(z, label)]} for the route points of the current extend() call;
#: matched by plan position, since a point is dropped to its floor before it is joined.
_NAMES = {}


def _key(p):
    return tuple(int(round(v)) for v in p[:2])


def _name_of(p):
    got = _NAMES.get(_key(p))
    return min(got, key=lambda zl: abs(zl[0] - p[2]))[1] if got else None


def _fmt(p):
    at = '(%.0f, %.0f, %.0f)' % tuple(p[:3])
    name = _name_of(p)
    return '%s at %s' % (name, at) if name else at


def runs_off_floor(polys, chain):
    """[(run, before, after)] for each stretch of the chain that is off the floor."""
    on = [on_surface(polys, p) is not None for p in chain]
    out, i = [], 0
    while i < len(chain):
        if on[i]:
            i += 1
            continue
        j = i
        while j < len(chain) and not on[j]:
            j += 1
        out.append((chain[i:j], chain[i - 1] if i > 0 else None,
                    chain[j] if j < len(chain) else None))
        i = j
    return out


def extend_graph(secs, chains, agent):
    """(polys, ids, flags, heads, neighbours, notes) for one agent's graph."""
    polys = [list(s['verts']) for s in secs]
    ids = [list(s['edge_ids']) for s in secs]
    flags = [int(s['flags'][-2:], 16) for s in secs]
    heads = [struct.unpack_from('<Q', bytes(s['raw_head']), 0)[0] for s in secs]
    nbrs = [list(s['neighbours']) for s in secs]
    notes, used = [], set()
    next_id = 1 + max([e for row in ids for e in row] or [0])
    first_new = len(polys)
    for chain in chains:
        for run, before, after in runs_off_floor(polys, chain):
            if after is None and before is None:
                raise ValueError('the route from %s never reaches the walkable floor'
                                 % _fmt(run[0]))
            quads, joins = corridor(polys, ids, run, before, after, agent, used)
            q_ids, next_id = _link_by_geometry(quads, next_id)
            for qi, vk, pi, pk in joins:
                ids[pi][pk] = next_id
                q_ids[qi][vk] = next_id
                next_id += 1
            # added at once, so a later chain can join this corridor
            polys.extend(quads)
            ids.extend(q_ids)
            notes.append('%d walkable quad(s) added for the stretch from %s'
                         % (len(quads), _fmt(run[0])))
    unbridged = set()
    while split_legs(polys, ids, chains, unbridged):
        a, b = split_legs(polys, ids, chains, unbridged)[0]
        got = bridge(polys, ids, a, b, agent, used)
        if got is None:
            unbridged.add((a, b))
            notes.append('the route from %s to %s crosses a gap in the walkable floor '
                         'that could not be joined; enemies stop at it'
                         % (_fmt(a), _fmt(b)))
            continue
        quad, joins = got
        q_ids, next_id = _link_by_geometry([quad], next_id)
        for qi, vk, pi, pk in joins:
            ids[pi][pk] = next_id
            q_ids[qi][vk] = next_id
            next_id += 1
        polys.append(quad)
        ids.extend(q_ids)
        notes.append('floor joined between %s and %s, where the level\'s walkable '
                     'floor had a gap' % (_fmt(a), _fmt(b)))
    if len(polys) == first_new:
        return polys, ids, flags, heads, nbrs, notes
    # pointers for the new records and the polygons they join
    offsets, at = [], navsurf.RECORDS_BEGIN
    for i, verts in enumerate(polys):
        offsets.append(at)
        at += navsurfgen._record_size(len(verts), last=(i == len(polys) - 1))
    new_heads, new_nbrs = navsurfgen.derive_pointers(
        ids, offsets, [len(v) for v in polys], polys)
    for i in range(first_new, len(polys)):
        heads.append(new_heads[i])
        nbrs.append(new_nbrs[i])
        flags.append(CORRIDOR_FLAGS)
    # the existing polygons a corridor joins point across the joined edge
    for a in range(first_new):
        for k, e in enumerate(ids[a]):
            if not e:
                continue
            across = [b for b in range(first_new, len(polys)) if e in ids[b]]
            if not across:
                continue
            ptr = offsets[across[0]] - navsurf.ORIGIN
            where, slot = navsurfgen.pointer_slot(k)
            if where == 'head':
                heads[a] = ptr
            else:
                nbrs[a][slot] = ptr
    return polys, ids, flags, heads, nbrs, notes


def _link_by_geometry(quads, next_id):
    """(ids, next free id): one shared id per edge two corridor quads share."""
    q_ids = [[0] * len(q) for q in quads]
    key = lambda v: (round(v[0], 2), round(v[1], 2))
    edges = {}
    for i, q in enumerate(quads):
        for k in range(len(q)):
            edges[(key(q[k]), key(q[(k + 1) % len(q)]))] = (i, k)
    for (a, b), (i, k) in edges.items():
        other = edges.get((b, a))
        if other and not q_ids[i][k]:
            j, m = other
            q_ids[i][k] = q_ids[j][m] = next_id
            next_id += 1
    return q_ids, next_id


def _sections_with_heads(buf, head):
    secs = navsurf.sections(buf, head)
    for s in secs:
        s['raw_head'] = buf[s['at'] + 40:s['at'] + 48]
    return secs


def plan_corridors(buf, chains):
    """(graphs input for navsurfgen.buffer_per_agent, notes). Raises ValueError."""
    heads = navsurf.graphs(buf)
    per_agent, notes = [], []
    for agent, h in enumerate(heads):
        secs = _sections_with_heads(buf, h)
        polys, ids, flags, ptrs, nbrs, n = extend_graph(secs, chains, agent)
        per_agent.append((polys, ids, flags, None, ptrs, nbrs))
        if agent == 0:
            notes += n
    return per_agent, notes, heads


def needs_extending(buf, chains):
    """The chain points that are off the walkable floor, in the first agent's graph."""
    heads = navsurf.graphs(buf)
    polys = [list(s['verts']) for s in navsurf.sections(buf, heads[0])]
    return [p for chain in chains for p in chain if on_surface(polys, p) is None]


def _bridgeable(buf, chains):
    """True when a leg of a chain crosses a gap in the first agent's floor that a
    bridge quad can join."""
    secs = navsurf.sections(buf, navsurf.graphs(buf)[0])
    polys = [list(s['verts']) for s in secs]
    ids = [list(s['edge_ids']) for s in secs]
    return any(bridge(polys, ids, a, b, 0, set()) is not None
               for a, b in split_legs(polys, ids, chains))


def _components(ids):
    """A component number per polygon: polygons sharing an edge id are joined."""
    where = {}
    for i, row in enumerate(ids):
        for e in row:
            if e:
                where.setdefault(e, []).append(i)
    comp = [-1] * len(ids)
    for start in range(len(ids)):
        if comp[start] >= 0:
            continue
        comp[start] = start
        todo = [start]
        while todo:
            i = todo.pop()
            for e in ids[i]:
                for j in where.get(e, ()) if e else ():
                    if comp[j] < 0:
                        comp[j] = start
                        todo.append(j)
    return comp


def split_legs(polys, ids, chains, skip=()):
    """[(a, b)] consecutive chain points both on the floor but on separate pieces of it."""
    comp = _components(ids)
    out = []
    for chain in chains:
        for a, b in zip(chain, chain[1:]):
            pa, pb = on_surface(polys, a), on_surface(polys, b)
            if pa is None or pb is None or comp[pa] == comp[pb]:
                continue
            leg = (tuple(a), tuple(b))
            if leg not in skip and leg not in out:
                out.append(leg)
    return out


def bridge(polys, ids, a, b, agent, used):
    """(quad, joins) joining the floor under `a` to the floor under `b`, or None.

    The quad spans two facing boundary edges, one from each piece, at most
    MAX_BRIDGE_GAP apart and within MAX_JOIN_DZ of each other; of the candidates the
    one nearest the leg a-b wins.
    """
    comp = _components(ids)
    ca, cb = comp[on_surface(polys, a)], comp[on_surface(polys, b)]

    def edges(c):
        for i, verts in enumerate(polys):
            if comp[i] != c:
                continue
            for k in range(len(verts)):
                if ids[i][k] or (i, k) in used:
                    continue
                p, q = verts[k], verts[(k + 1) % len(verts)]
                length = math.hypot(q[0] - p[0], q[1] - p[1])
                if length >= MIN_JOIN_EDGE[agent]:
                    yield i, k, p, q, length

    def faces(p, q, length, target):
        nx, ny = (q[1] - p[1]) / length, -(q[0] - p[0]) / length
        mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
        return (target[0] - mx) * nx + (target[1] - my) * ny > 0

    mid = lambda p, q: ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, (p[2] + q[2]) / 2)
    side_b = list(edges(cb))
    best = None
    for i, k, p, q, ln in edges(ca):
        ma = mid(p, q)
        for j, m, r, s, ln2 in side_b:
            mb = mid(r, s)
            gap = math.hypot(mb[0] - ma[0], mb[1] - ma[1])
            if gap > MAX_BRIDGE_GAP or abs(mb[2] - ma[2]) > MAX_JOIN_DZ \
                    or not _slope_ok(ma, mb):
                continue
            if not (faces(p, q, ln, mb) and faces(r, s, ln2, ma)):
                continue
            quad = [tuple(q), tuple(p), tuple(s), tuple(r)]
            if not (_ccw(quad) and _convex(quad)):
                continue
            centre = mid(ma, mb)
            off, _q = _dist_point_segment(centre, a, b)
            score = gap + off
            if best is None or score < best[0]:
                best = (score, quad, i, k, j, m)
    if best is None:
        return None
    _s, quad, i, k, j, m = best
    used.add((i, k))
    used.add((j, m))
    # quad edge 0 runs q -> p (shared with polys[i] edge k), edge 2 runs s -> r
    return quad, [(0, 0, i, k), (0, 2, j, m)]


def extend(uasset, uexp, chains, names=None):
    """(uasset, uexp, notes) with the walkable graph extended to cover `chains`.

    Returns the package unchanged, with no notes, when every point is already on the
    floor. Raises ValueError with a plain reason when a stretch cannot be joined; a
    point in `names` ({(x, y, z): label}) is named by its label in that reason.
    """
    _NAMES.clear()
    for k, v in (names or {}).items():
        _NAMES.setdefault(_key(k), []).append((k[2], v))
    try:
        return _extend(uasset, uexp, chains)
    finally:
        _NAMES.clear()


def _extend(uasset, uexp, chains):
    from riftlord.build.navigation import navsurfinject
    buf = _surface_of(uasset, uexp)
    if not needs_extending(buf, chains) and not _bridgeable(buf, chains):
        return uasset, uexp, []
    per_agent, notes, heads = plan_corridors(buf, chains)
    out = navsurfgen.buffer_per_agent(per_agent, list(heads[0]['bbox']))
    problems = check_buffer(out, chains)
    if problems:
        raise ValueError('the extended walkable graph failed its checks: %s'
                         % '; '.join(problems))
    ua, ux, checks = navsurfinject.set_surface_bytes(uasset, uexp, out)
    bad = [name for name, ok in checks if not ok]
    if bad:
        raise ValueError('writing the extended walkable graph failed: %s'
                         % '; '.join(bad))
    return ua, ux, notes


def _surface_of(uasset, uexp):
    """The level's surface buffer, read from the package bytes."""
    import struct as _st
    from riftlord.build.navigation import navvolinject
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(uasset, uexp)
    idx, _mesh, props = navvolinject.nav_export_index(pkg)
    at = {nm: (st, en) for nm, _t, st, en in props}
    data = pkg.exports[idx]['data']
    mesh = _st.unpack_from('<I', data, at['NavMeshBufferSize'][1] - 4)[0]
    vol = (_st.unpack_from('<I', data, at['NavVolumesBufferSize'][1] - 4)[0]
           if 'NavVolumesBufferSize' in at else 0)
    start = len(data) - vol - mesh
    return bytes(data[start:start + mesh])


def check_buffer(buf, chains):
    """Problems with an extended buffer: it parses, its trees find interior points,
    edge ids pair up, and every chain point is now on the floor."""
    import collections
    out = []
    heads = navsurf.graphs(buf)
    if len(heads) != 2:
        return ['expected two agent graphs, found %d' % len(heads)]
    for h in heads:
        got = navsurf.sections(buf, h)
        if not got:
            out.append('graph %d does not parse' % h.get('index', 0))
            continue
        out += navsurf.check_sections(buf, h, got)
        out += navsurf.check_tree(buf, h, got)
        counts = collections.Counter(e for s in got for e in s['edge_ids'] if e)
        odd = [e for e, c in counts.items() if c % 2]
        if odd:
            out.append('%d edge id(s) quoted an odd number of times' % len(odd))
    off = needs_extending(buf, chains)
    if off:
        out.append('still off the floor: %s' % ', '.join(_fmt(p) for p in off))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--map', required=True)
    ap.add_argument('--route', nargs='+', required=True,
                    help='x,y,z points: the spawner first, then the route')
    args = ap.parse_args(argv)
    chain = [tuple(float(v) for v in p.split(',')) for p in args.route]
    buf = navsurf.buffer(args.map)
    print('off the floor: %s' % [_fmt(p) for p in needs_extending(buf, [chain])])
    per_agent, notes, heads = plan_corridors(buf, [chain])
    out = navsurfgen.buffer_per_agent(per_agent, list(heads[0]['bbox']))
    for n in notes:
        print(n)
    problems = check_buffer(out, [chain])
    print('checks: %s' % (problems or 'ok'))
    print('%d -> %d bytes' % (len(buf), len(out)))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
