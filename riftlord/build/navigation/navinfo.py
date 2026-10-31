"""Report what is inside a NavPower navgraph: version, build params, world bounds.

The 24-byte header is followed by a block of build parameters and then the graph's
**world bounding box** -- six floats, min then max. That box is what decides whether
a transplanted graph can possibly be useful: if it does not cover the place the AI
actually spawns, movers will have no graph under them whatever the version gate did.

    python3 navinfo.py --omd2            # every OMD2 .path
    python3 navinfo.py --omd3            # every OMD3 campaign map
    python3 navinfo.py --overlap Scenario_18
"""

import argparse
import os
import struct
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navpatch  # noqa: E402

# Offsets within the graph (after the 24-byte header). OMD2 and OMD3 put the same
# fields in the same order but OMD2's parameter block is two floats shorter.
LAYOUTS = {
    'omd2': {'params': 10, 'bbox': 15},
    'omd3': {'params': 16, 'bbox': 21},
}


def describe(graph, kind):
    """(version, params, bbox_min, bbox_max) for one graph blob."""
    words = struct.unpack_from('<8I', graph, 0)
    lay = LAYOUTS[kind]
    floats = struct.unpack_from('<30f', graph, 24)
    params = list(floats[lay['params']:lay['bbox']])
    bbox = floats[lay['bbox']:lay['bbox'] + 6]
    return {
        'version': words[1],
        'payload': words[2],
        'flags': words[5],
        'params': params,
        'min': list(bbox[0:3]),
        'max': list(bbox[3:6]),
    }


def omd2_all():
    out = []
    for g in navpatch.omd2_graphs():
        d = describe(g['graph'], 'omd2')
        d['name'] = g['scene']
        d['bytes'] = len(g['graph'])
        out.append(d)
    return out


def omd3_map(map_name, tmp):
    files = navpatch.fetch_map(navpatch.map_path(map_name), tmp)
    found = navpatch.nav_export(files['.umap'], files['.uexp'])
    if not found:
        return None
    _e, off, size = found
    with open(files['.uexp'], 'rb') as fh:
        graph = fh.read()[off:off + size]
    d = describe(graph, 'omd3')
    d['name'] = map_name
    d['bytes'] = size
    return d


def overlaps(a, b):
    """Overlap volume fraction of the smaller box, 0 if disjoint."""
    lo = [max(a['min'][i], b['min'][i]) for i in range(3)]
    hi = [min(a['max'][i], b['max'][i]) for i in range(3)]
    if any(hi[i] <= lo[i] for i in range(3)):
        return 0.0
    inter = 1.0
    for i in range(3):
        inter *= hi[i] - lo[i]
    vols = []
    for box in (a, b):
        v = 1.0
        for i in range(3):
            v *= max(box['max'][i] - box['min'][i], 1e-6)
        vols.append(v)
    return inter / min(vols)


def show(d):
    return ('%-24s v%-2d %9d B  params %-34s  min %9.0f %9.0f %8.0f  max %9.0f %9.0f %8.0f'
            % (d['name'][:24], d['version'], d['bytes'],
               ','.join('%g' % p for p in d['params']),
               d['min'][0], d['min'][1], d['min'][2],
               d['max'][0], d['max'][1], d['max'][2]))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--omd2', action='store_true')
    ap.add_argument('--omd3', action='store_true')
    ap.add_argument('--overlap', metavar='MAP',
                    help='rank donor graphs by how much they cover this map')
    ap.add_argument('--points', metavar='SCENE',
                    help="emit NavProbe's teleport candidates for this OMD2 scene")
    ap.add_argument('--tmp', default=gamepath.beside_code(gamepath.CODE_ROOT, '.navinfo'))
    args = ap.parse_args()

    if args.points:
        text, pts = lua_points(args.points)
        print(text)
        print('\n-- %d points, Z bands: %s' % (
            len(pts), sorted({round(p[2] / 50) * 50 for p in pts})))
        return

    if args.omd2:
        for d in omd2_all():
            print(show(d))
    if args.omd3 or args.overlap:
        maps = list(navpatch.maps())
        got = []
        for m in maps:
            try:
                d = omd3_map(m, args.tmp)
            except SystemExit:
                continue
            if d:
                got.append(d)
                if args.omd3:
                    print(show(d))
        if args.overlap:
            target = next((d for d in got if d['name'] == args.overlap), None)
            if not target:
                raise SystemExit('no navgraph for %s' % args.overlap)
            print('target %s' % show(target))
            print('\ndonors ranked by overlap with the target box:')
            rows = [(overlaps(target, d), d) for d in got if d['name'] != args.overlap]
            rows += [(overlaps(target, d), d) for d in omd2_all()]
            for frac, d in sorted(rows, key=lambda r: -r[0])[:12]:
                tag = 'OMD3' if d['version'] == 2 and d['flags'] else 'OMD2'
                print('  %5.1f%%  %-6s %s' % (frac * 100, tag, show(d)))




def sample_points(graph, kind='omd2', want=40):
    """Plausible on-graph coordinates, spread across the graph's own space.

    A blind scan for float triples lying inside the graph's declared bounding box
    finds node positions among other things. Individual hits may be false, but
    picking one per occupied grid cell gives a spread of points that are very
    likely navigable -- far better than sweeping a blind grid through empty air.
    """
    import collections
    info = describe(graph, kind)
    lo, hi = info['min'], info['max']
    hits = []
    for off in range(24, len(graph) - 12, 4):
        x, y, z = struct.unpack_from('<3f', graph, off)
        if (lo[0] <= x <= hi[0] and lo[1] <= y <= hi[1] and lo[2] <= z <= hi[2]
                and abs(x) + abs(y) + abs(z) > 1.0):
            hits.append((x, y, z))
    if not hits:
        return []
    # Keep the dominant floor bands: heights where many points agree.
    bands = collections.Counter(round(h[2] / 50) * 50 for h in hits)
    keep = {b for b, n in bands.most_common(6)}
    hits = [h for h in hits if round(h[2] / 50) * 50 in keep]
    # One point per occupied cell, so the sample spans the level.
    cell = max((hi[0] - lo[0]), (hi[1] - lo[1])) / 8.0
    chosen = {}
    for x, y, z in hits:
        key = (round(x / cell), round(y / cell), round(z / 50) * 50)
        chosen.setdefault(key, (x, y, z))
    out = list(chosen.values())
    out.sort(key=lambda p: (p[2], p[0], p[1]))
    if len(out) > want:
        step = len(out) / float(want)
        out = [out[int(i * step)] for i in range(want)]
    return out


def lua_points(scene, want=40):
    """Emit the candidate list for NavProbe's teleport probe."""
    g = {x['scene']: x for x in navpatch.omd2_graphs()}[scene]
    pts = sample_points(g['graph'], 'omd2', want)
    lines = ['-- Sampled from %s.path by navinfo.py --points' % scene,
             'local DONOR_POINTS = {']
    for x, y, z in pts:
        lines.append('    { %.0f, %.0f, %.0f },' % (x, y, z))
    lines.append('}')
    return '\n'.join(lines), pts


if __name__ == '__main__':
    main()
