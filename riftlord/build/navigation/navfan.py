"""Vertex fans of a walkable graph: NavPower's fan walk, checked offline, and the writer-side
clean-up that keeps every pair of neighbouring polygons on exactly one shared edge.

NavPower walks the polygons round a vertex (exe 0x143415110, mirror 0x1434150b0): from
(polygon, edge) it takes the neighbour across the edge, finds the FIRST of the neighbour's
edges that points back (0x143439d60; none, or a null pointer, is a border and stops), moves to
that edge's next (mirror: previous) edge, and repeats until it is back on the start polygon.
A walk that neither returns nor reaches a border never ends, and the game freezes on it
(Crystal Probe A, 2026-10-24). Two polygons sharing two edges break it, because the back-edge
lookup always finds the first. Shipped graphs: 66 graphs, 39,135 polygons, 0 such defects.

    python3 -m riftlord.build.navigation.navfan --pak PAK      # a built mission
    python3 -m riftlord.build.navigation.navfan --selftest     # every shipped map: 0
"""

import argparse
import collections
import math
import struct
import sys

from riftlord.build.navigation import navsurf

#: Two vertices closer than this are one (uu).
POS_EPS = 0.01
#: A polygon with less xy area than this is degenerate and dropped (uu^2).
ZERO_AREA = 1.0
#: A vertex within this of the line through its neighbours is on that line (uu).
LINE_EPS = 0.5
#: Most steps a fan walk takes before it counts as never closing: per polygon of the graph.
STEPS_PER_POLY = 4


# -- the writer side ---------------------------------------------------------------------

def _same(a, b):
    return abs(a[0] - b[0]) <= POS_EPS and abs(a[1] - b[1]) <= POS_EPS \
        and abs(a[2] - b[2]) <= POS_EPS


def _area_xy(poly):
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))


def _off_line(a, m, c):
    """Distance of m from the 3D line a-c, or None when m is not between a and c."""
    d = [c[i] - a[i] for i in range(3)]
    size = math.sqrt(sum(x * x for x in d))
    if size <= POS_EPS:
        return None
    t = sum((m[i] - a[i]) * d[i] for i in range(3)) / (size * size)
    if not 0.0 < t < 1.0:
        return None
    return math.sqrt(sum((a[i] + t * d[i] - m[i]) ** 2 for i in range(3)))


def _simplified(poly):
    """`poly` without repeated vertices and without back-tracking spikes (a vertex whose
    two edges run back along each other); the same list when it has neither."""
    out = list(poly)
    changed = True
    while changed and len(out) >= 3:
        changed = False
        n = len(out)
        for k in range(n):
            if _same(out[k], out[(k + 1) % n]):
                del out[(k + 1) % n]
                changed = True
                break
            a, b, c = out[k - 1], out[k], out[(k + 1) % n]
            if _same(a, c):
                del out[k]
                changed = True
                break
            ab = (b[0] - a[0], b[1] - a[1])
            bc = (c[0] - b[0], c[1] - b[1])
            cross = ab[0] * bc[1] - ab[1] * bc[0]
            dot = ab[0] * bc[0] + ab[1] * bc[1]
            base = math.hypot(c[0] - a[0], c[1] - a[1]) or 1.0
            if dot < 0 and abs(cross) / base <= LINE_EPS:
                del out[k]
                changed = True
                break
    return poly if len(out) == len(poly) else out


def _key(v):
    return (round(v[0] / POS_EPS), round(v[1] / POS_EPS), round(v[2] / POS_EPS))


def _merge_shared_runs(polys):
    """`polys` with each vertex M removed where two polygons share both edges at M along one
    straight line and no other polygon uses M; the same list when there is none."""
    out = list(polys)
    while True:
        users = collections.defaultdict(set)
        for i, p in enumerate(out):
            for v in p:
                users[_key(v)].add(i)
        drop = None
        for i, p in enumerate(out):
            n = len(p)
            for k in range(n):
                m = p[k]
                who = users[_key(m)]
                if len(who) != 2:
                    continue
                (j,) = who - {i}
                a, c = p[k - 1], p[(k + 1) % n]
                q = out[j]
                if n <= 3 or len(q) <= 3:
                    continue
                at = next(x for x, v in enumerate(q) if _key(v) == _key(m))
                around = {_key(q[at - 1]), _key(q[(at + 1) % len(q)])}
                if around != {_key(a), _key(c)}:
                    continue
                off = _off_line(a, m, c)
                if off is None or off > LINE_EPS:
                    continue
                drop = (i, j, _key(m))
                break
            if drop:
                break
        if drop is None:
            return out
        i, j, km = drop
        for x in (i, j):
            out[x] = [v for v in out[x] if _key(v) != km]


def clean(polys):
    """(polys, notes): repeated vertices and back-tracking spikes removed, zero-area
    polygons dropped, and every straight run two polygons share joined into one edge, so
    every pair of neighbours shares one edge. `polys` itself when nothing needs it."""
    notes = []
    out = []
    changed = False
    for i, p in enumerate(polys):
        q = _simplified(p)
        if q is not p:
            changed = True
            notes.append('polygon %d: %d repeated or back-tracking vertex(es) removed at %s'
                         % (i, len(p) - len(q), _at(p[0])))
        if len(q) < 3 or abs(_area_xy(q)) < ZERO_AREA:
            changed = True
            notes.append('polygon %d: zero area, dropped at %s' % (i, _at(p[0])))
            continue
        out.append(q)
    merged = _merge_shared_runs(out)
    if merged != out:
        changed = True
        notes.append('%d vertex(es) inside straight runs two polygons share removed'
                     % sum(len(a) - len(b) for a, b in zip(out, merged)))
    return (merged if changed else polys), notes


def _at(v):
    return '(%.0f, %.0f, %.0f)' % tuple(v[:3])


# -- the check on a built buffer ---------------------------------------------------------

def pointers(buf, head):
    """(sections, ptr): ptr[i][k] is the record edge k of record i points at, None for a
    null pointer, -1 for a value that lands on no record start."""
    secs = navsurf.sections(buf, head)
    starts = {s['at'] - head['origin']: i for i, s in enumerate(secs)}
    ptr = []
    for s in secs:
        n = len(s['verts'])
        raw = [struct.unpack_from('<Q', buf, s['at'] + 40)[0]] + list(s['neighbours'][:n - 1])
        ptr.append([None if v == 0 else starts.get(v, -1) for v in raw])
    return secs, ptr


def _twin(ptr, p, e):
    q = ptr[p][e]
    if q is None or q < 0:
        return None
    for j, back in enumerate(ptr[q]):
        if back == p:
            return q, j
    return None


def walk(ptr, p0, e0, step, cap):
    """'closed', 'border' or 'never' for NavPower's fan walk from (p0, e0)."""
    p, e = p0, e0
    for _n in range(cap):
        t = _twin(ptr, p, e)
        if t is None:
            return 'border'
        p, j = t
        e = (j + step) % len(ptr[p])
        if p == p0:
            return 'closed'
    return 'never'


def graph_problems(buf, head, g):
    """[text] for one agent graph: fan walks that never close, two edges to one neighbour,
    asymmetric and dangling pointers, each naming the polygon and where it is."""
    secs, ptr = pointers(buf, head)
    out = []

    def where(i, k=0):
        return 'graph %d polygon %d at %s' % (g, i, _at(secs[i]['verts'][k % len(ptr[i])]))
    for i, row in enumerate(ptr):
        for k, q in enumerate(row):
            if q == -1:
                out.append('%s: edge %d points at no polygon' % (where(i, k), k))
            elif q is not None and i not in ptr[q]:
                out.append('%s: edge %d points at polygon %d, which does not point back'
                           % (where(i, k), k, q))
        twice = sorted(q for q, c in collections.Counter(
            x for x in row if x is not None and x >= 0).items() if c > 1)
        for q in twice:
            out.append('%s: shares more than one edge with polygon %d' % (where(i), q))
    cap = STEPS_PER_POLY * len(secs) + 16
    seen = set()
    for step in (1, -1):
        for i, row in enumerate(ptr):
            for k in range(len(row)):
                if walk(ptr, i, k, step, cap) == 'never':
                    v = k if step == 1 else k + 1
                    spot = _at(secs[i]['verts'][v % len(row)])
                    if spot not in seen:
                        seen.add(spot)
                        out.append('%s: the fan round vertex %s never closes'
                                   % (where(i, v), spot))
    return out


def problems(buf):
    """[text] for every agent graph of a surface buffer."""
    out = []
    for g, head in enumerate(navsurf.graphs(buf)):
        out += graph_problems(buf, head, g)
    return out


def level_problems(uasset, uexp):
    from riftlord.build.navigation import navsurfextend
    return problems(navsurfextend._surface_of(uasset, uexp))


def files_problems(files):
    """[text] for each mission's own level in {inner path: bytes}, whatever its Working
    Title names it (leveltitle.is_level)."""
    from riftlord.build import leveltitle
    out = []
    for name in files:
        if leveltitle.is_level(name) and name[:-5] + '.uexp' in files:
            out += level_problems(files[name], files[name[:-5] + '.uexp'])
    return out


def build_problems(source):
    """[text] for the level of a written mission (a loose build folder or a pak;
    leveltitle.level_files)."""
    from riftlord.build import leveltitle
    level = leveltitle.level_files(source)
    if not level:
        return []
    umap = next(n for n in level if n.endswith('.umap'))
    return level_problems(level[umap], level[umap[:-5] + '.uexp'])


def selftest(log=print):
    """(graphs, polygons, defects) over every shipped map; defects must be 0."""
    from riftlord.mapeditor import leveldata
    graphs = polys = 0
    bad = []
    for lv in leveldata.levels():
        name = lv['name'] if isinstance(lv, dict) else lv
        if not name.startswith('Scenario'):
            continue
        buf = navsurf.buffer(name)
        if not buf:
            continue
        for g, head in enumerate(navsurf.graphs(buf)):
            graphs += 1
            polys += len(navsurf.sections(buf, head))
            bad += ['%s %s' % (name, p) for p in graph_problems(buf, head, g)]
    log('%d shipped graphs, %d polygons, %d defects' % (graphs, polys, len(bad)))
    for line in bad[:10]:
        log('  ' + line)
    return graphs, polys, len(bad)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--pak', help='a built mission pak or loose build folder')
    ap.add_argument('--selftest', action='store_true', help='every shipped map must be clean')
    args = ap.parse_args(argv)
    if args.selftest:
        return 0 if selftest()[2] == 0 else 1
    bad = build_problems(args.pak)
    for line in bad:
        print('  ' + line)
    print('%d fan defect(s)' % len(bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
