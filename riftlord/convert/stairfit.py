"""Measure which way each kit staircase climbs, and cache it.

    python3 stairfit.py --build

**A staircase's ascent is not in its bounding box.** OMD3's kit stairs climb along their
local **-y**; OMD2's climb along local **+x**. Placing a kit stair at its source's own yaw
therefore puts the climb a quarter turn off, which is the "wide staircase linking the wrong
two sides" the author reported -- and no box-based check can see it, because the box is the
same either way.

`emitplan` reads the cache this writes and turns each stair so its climb matches the
direction the source climbed in world space.
"""

import argparse
import collections
import json
import math
import os
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.formats.omd3 import meshcat

CACHE = gamepath.data('stair-ascent.json')

#: How flat a face must be to count as a tread, as |n.z|. OMD3's cooked meshes do not all
#: wind the same way, so the sign is dropped: reading only +z faces found ZERO treads on a
#: 21,824-vertex staircase.
TREAD = 0.8

#: How much a mesh must climb across its own footprint before it is a staircase at all, in
#: uu. `StairsSideCap_01` is a flat side panel and must not be turned like a stair.
MIN_RISE = 40.0


def ascent(verts, tris, tread=TREAD):
    """(degrees the mesh climbs toward, rise in uu), or None when it is flat.

    Fitted over the TREADS rather than the whole mesh, because a staircase's sides and
    underside would otherwise average the climb away.
    """
    up = []
    for t in range(0, len(tris) - 2, 3):
        try:
            p, q, r = verts[tris[t]], verts[tris[t + 1]], verts[tris[t + 2]]
        except IndexError:
            continue
        u = [q[k] - p[k] for k in (0, 1, 2)]
        v = [r[k] - p[k] for k in (0, 1, 2)]
        n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0]]
        L = math.sqrt(sum(c * c for c in n))
        if L < 1e-6 or abs(n[2]) / L < tread:
            continue
        up.append([(p[k] + q[k] + r[k]) / 3.0 for k in (0, 1, 2)])
    if len(up) < 8:
        return None
    best = None
    for axis in (0, 1):
        lo = min(c[axis] for c in up)
        hi = max(c[axis] for c in up)
        if hi - lo < 1.0:
            continue
        bins = collections.defaultdict(list)
        for c in up:
            bins[int((c[axis] - lo) / (hi - lo + 1e-9) * 7.999)].append(c[2])
        prof = [sorted(v)[len(v) // 2] for _k, v in sorted(bins.items())]
        rise = prof[-1] - prof[0]
        if best is None or abs(rise) > abs(best[1]):
            best = (axis, rise)
    if best is None or abs(best[1]) < MIN_RISE:
        return None
    axis, rise = best
    if axis == 0:
        deg = 0.0 if rise > 0 else 180.0
    else:
        deg = 90.0 if rise > 0 else 270.0
    return (deg, abs(rise))


def build(cat=None, out=CACHE):
    """Measure every catalogue mesh whose name says it is a stair, and write the cache."""
    from riftlord.formats.omd3 import omd3mesh
    cat = cat or meshcat.load()
    got, flat = {}, []
    for name in sorted(cat):
        low = name.lower()
        if 'stair' not in low and 'step' not in low:
            continue
        try:
            m = omd3mesh.read(name)
        except Exception:                                   # noqa: BLE001
            m = None
        if not m:
            continue
        vs = m.get('verts') or m.get('vertices')
        ts = m.get('tris') or m.get('triangles')
        if not vs or not ts:
            continue
        a = ascent(vs, ts)
        if a:
            got[name] = {'degrees': a[0], 'rise': round(a[1], 1)}
        else:
            flat.append(name)
    with open(out, 'w') as fh:
        json.dump({'ascent': got, 'flat': sorted(flat)}, fh, indent=1, sort_keys=True)
    return got, flat


def load(path=CACHE):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {'ascent': {}, 'flat': []}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--build', action='store_true')
    args = ap.parse_args(argv)
    if args.build:
        got, flat = build()
        print('measured %d climbing stair mesh(es), %d flat' % (len(got), len(flat)))
        by = collections.Counter(v['degrees'] for v in got.values())
        print('   climb directions: %s' % dict(sorted(by.items())))
        for n, v in sorted(got.items())[:40]:
            print('   %-34s %5.0f deg  rise %6.1f' % (n, v['degrees'], v['rise']))
        print('   flat: %s' % ', '.join(flat[:10]))
        return 0
    d = load()
    print('%d cached' % len(d['ascent']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
