"""A strict validator for a walkable NavPower graph: every polygon and edge defect a
generated graph could carry, counted per graph, so two builds can be compared.

    python3 navstrict.py --pak A.pak --pak B.pak       # each mission level in the paks
    python3 navstrict.py --level Scenario_09          # the shipped graph, as a baseline
"""

import argparse
import collections
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navsurf  # noqa: E402
from riftlord.build.navigation import navsurfgen  # noqa: E402

#: Shortest edge accepted (uu).
MIN_EDGE = 1.0
#: Smallest polygon area accepted (uu2).
MIN_AREA = 1.0
#: Most vertices on one polygon (the shipped maximum).
MAX_VERTS = 15
#: Largest distance of a vertex from its polygon's plane (uu).
PLANE_EPS = 1.0
#: Largest reflex turn at a vertex, as a cross product over edge lengths (sin).
CONVEX_EPS = 1e-3
#: Endpoints of two paired edges must coincide in xy within this (uu).
MEET_EPS = 0.5
#: Agent step: a paired edge whose two sides differ more than this in height (uu).
STEP = 48.0
#: A vertex within this of another polygon's edge, off its ends, is a T-junction (uu).
T_EPS = 0.5
#: Two polygons at one xy overlap when their heights are closer than this (uu).
OVERLAP_DZ = 50.0
#: Sample spacing of the overlap test (uu).
OVERLAP_CELL = 25.0
#: Steepest polygon accepted (degrees).
MAX_SLOPE = 60.0
#: The usual per-vertex tag.
PLAIN_TAG = -4
#: Defects a probe refuses: the ones round 3's freezing build had and round 2 lacked
#: (short edges, more than MAX_VERTS vertices, T-junctions), and pairs that cannot work.
FATAL = ('edge shorter than %g' % MIN_EDGE, 'more than %d vertices' % MAX_VERTS,
         'T-junction', 'paired edges do not meet', 'non-finite vertex',
         'id pairs a polygon with itself')


def records(buf, head):
    out = []
    for s in navsurf.sections(buf, head):
        n = len(s['verts'])
        tags = [None if (s['truncated'] and i == n - 1) else
                struct.unpack_from('<h', buf, s['at'] + 48 + 32 * i + 14)[0]
                for i in range(n)]
        out.append({'verts': [tuple(v) for v in s['verts']], 'ids': list(s['edge_ids']),
                    'tags': tags, 'flags': bytes.fromhex(s['flags'])[3]})
    return out


def _plane(verts):
    """(normal, d) by Newell's method."""
    nx = ny = nz = 0.0
    n = len(verts)
    for i in range(n):
        a, b = verts[i], verts[(i + 1) % n]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    nrm = (nx / length, ny / length, nz / length)
    cx = sum(v[0] for v in verts) / n
    cy = sum(v[1] for v in verts) / n
    cz = sum(v[2] for v in verts) / n
    return nrm, nrm[0] * cx + nrm[1] * cy + nrm[2] * cz


def check_graph(recs, shipped=None):
    """{defect: [examples]} for one graph's records."""
    bad = collections.defaultdict(list)
    shipped_keys = set()
    for r in shipped or ():
        shipped_keys.add(tuple(sorted((round(v[0], 1), round(v[1], 1)) for v in r['verts'])))
    edges = []
    for i, r in enumerate(recs):
        v = r['verts']
        n = len(v)
        if any(not math.isfinite(c) for p in v for c in p):
            bad['non-finite vertex'].append(i)
        if n > MAX_VERTS:
            bad['more than %d vertices' % MAX_VERTS].append((i, n))
        area = 0.5 * sum(v[k][0] * v[(k + 1) % n][1] - v[(k + 1) % n][0] * v[k][1]
                         for k in range(n))
        if area < MIN_AREA:
            bad['area below %g (or clockwise)' % MIN_AREA].append((i, round(area, 3)))
        for k in range(n):
            a, b = v[k], v[(k + 1) % n]
            if math.dist(a[:2], b[:2]) < MIN_EDGE:
                bad['edge shorter than %g' % MIN_EDGE].append((i, k, [round(c, 2) for c in a]))
            o = v[k - 1]
            e1 = (a[0] - o[0], a[1] - o[1])
            e2 = (b[0] - a[0], b[1] - a[1])
            l1, l2 = math.hypot(*e1), math.hypot(*e2)
            if l1 and l2 and (e1[0] * e2[1] - e1[1] * e2[0]) / (l1 * l2) < -CONVEX_EPS:
                bad['reflex vertex'].append((i, k))
            edges.append((i, k, a, b, r['ids'][k]))
        nrm, d = _plane(v)
        off = max(abs(nrm[0] * p[0] + nrm[1] * p[1] + nrm[2] * p[2] - d) for p in v)
        if off > PLANE_EPS:
            bad['non-planar'].append((i, round(off, 2)))
        if abs(nrm[2]) < math.cos(math.radians(MAX_SLOPE)):
            bad['steeper than %g deg' % MAX_SLOPE].append(
                (i, round(math.degrees(math.acos(min(1.0, abs(nrm[2])))), 1)))
        plain = shipped is not None and tuple(sorted(
            (round(p[0], 1), round(p[1], 1)) for p in v)) not in shipped_keys
        if plain and any(t not in (None, PLAIN_TAG) for t in r['tags']):
            bad['corner tag on a non-shipped polygon'].append(i)
    # -- pairs
    by_id = collections.defaultdict(list)
    for e in edges:
        if e[4]:
            by_id[e[4]].append(e)
    for eid, members in by_id.items():
        if len(members) != 2:
            bad['id quoted %d times' % len(members)].append(eid)
            continue
        (i, k, a, b, _), (j, m, c, d, _) = members
        if i == j:
            bad['id pairs a polygon with itself'].append(eid)
        if math.dist(a[:2], d[:2]) > MEET_EPS or math.dist(b[:2], c[:2]) > MEET_EPS:
            bad['paired edges do not meet'].append((eid, i, j))
        za, zb = (a[2] + b[2]) / 2.0, (c[2] + d[2]) / 2.0
        if abs(za - zb) > STEP or abs(a[2] - d[2]) > STEP or abs(b[2] - c[2]) > STEP:
            bad['paired step over %g' % STEP].append((eid, round(abs(za - zb), 1)))
    # -- T-junctions and unpaired abutting edges, through a spatial hash
    cell = 200.0
    grid = collections.defaultdict(list)
    for e in edges:
        a, b = e[2], e[3]
        for gx in range(int(min(a[0], b[0]) // cell), int(max(a[0], b[0]) // cell) + 1):
            for gy in range(int(min(a[1], b[1]) // cell), int(max(a[1], b[1]) // cell) + 1):
                grid[(gx, gy)].append(e)
    for i, r in enumerate(recs):
        for p in r['verts']:
            for e in grid.get((int(p[0] // cell), int(p[1] // cell)), ()):
                if e[0] == i:
                    continue
                a, b = e[2], e[3]
                dx, dy = b[0] - a[0], b[1] - a[1]
                L2 = dx * dx + dy * dy
                if not L2:
                    continue
                t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
                L = math.sqrt(L2)
                if t * L <= T_EPS or (1 - t) * L <= T_EPS:
                    continue
                dist = abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / L
                z = a[2] + t * (b[2] - a[2])
                if dist <= T_EPS and abs(z - p[2]) <= STEP:
                    bad['T-junction'].append((i, e[0], [round(c) for c in p]))
    keyed = {}
    for e in edges:
        keyed[(round(e[2][0] / MEET_EPS), round(e[2][1] / MEET_EPS),
               round(e[3][0] / MEET_EPS), round(e[3][1] / MEET_EPS))] = e
    for key, e in keyed.items():
        mate = keyed.get((key[2], key[3], key[0], key[1]))
        if mate and mate[0] != e[0] and not e[4] and \
                abs((e[2][2] + e[3][2]) / 2 - (mate[2][2] + mate[3][2]) / 2) <= STEP:
            bad['abutting edge left unpaired'].append((e[0], mate[0]))
    # -- overlaps
    samples = collections.defaultdict(list)
    for i, r in enumerate(recs):
        v = r['verts']
        xs, ys = [p[0] for p in v], [p[1] for p in v]
        for gx in range(int(min(xs) // OVERLAP_CELL), int(max(xs) // OVERLAP_CELL) + 1):
            for gy in range(int(min(ys) // OVERLAP_CELL), int(max(ys) // OVERLAP_CELL) + 1):
                x, y = (gx + 0.5) * OVERLAP_CELL, (gy + 0.5) * OVERLAP_CELL
                if navsurf._inside_xy(v, x, y):
                    samples[(gx, gy)].append((navsurfgen.plane_z(v, x, y), i))
    for key, zs in samples.items():
        zs.sort()
        for (z1, i1), (z2, i2) in zip(zs, zs[1:]):
            if z2 - z1 < OVERLAP_DZ:
                bad['overlapping polygons'].append((i1, i2))
    tags = collections.Counter(t for r in recs for t in r['tags'] if t is not None)
    return bad, tags


def report(label, buf, shipped_buf=None):
    out = {}
    heads = navsurf.graphs(buf)
    sheads = navsurf.graphs(shipped_buf) if shipped_buf else [None] * len(heads)
    for g, (h, sh) in enumerate(zip(heads, sheads)):
        recs = records(buf, h)
        bad, tags = check_graph(recs, records(shipped_buf, sh) if sh else None)
        out[g] = {k: len(v) for k, v in bad.items()}
        print('%s graph %d: %d polygons, tags %s' % (label, g, len(recs), dict(tags)))
        for k, v in sorted(bad.items()):
            print('   %-40s %6d   e.g. %s' % (k, len(v), v[:3]))
    return out


def pak_buffers(pak):
    from riftlord.build.navigation import navsurfextend
    from riftlord.build import leveltitle
    from riftlord.core import paksource
    files = paksource.entries(pak)
    #: a mission's own level, whatever its Working Title names it (leveltitle.is_level)
    for name in sorted(files):
        if leveltitle.is_level(name) and name[:-5] + '.uexp' in files:
            yield name, navsurfextend._surface_of(files[name], files[name[:-5] + '.uexp'])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--pak', action='append', default=[])
    ap.add_argument('--level', help='the shipped level the paks were built from')
    args = ap.parse_args(argv)
    shipped = navsurf.buffer(args.level) if args.level else None
    if shipped and not args.pak:
        report('shipped %s' % args.level, shipped)
    for pak in args.pak:
        for name, buf in pak_buffers(pak):
            report(os.path.basename(pak), buf, shipped)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
