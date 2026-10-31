"""Find the best RIGID placement of a donor flight graph over a route.

Reshaping a donor's volumes onto a route covers it perfectly and crashes the level:
the trailing index still describes the donor's geometry. A rigid translation does not
crash -- measured, the `displace` test moved a whole graph 20000uu and the level still
loaded -- because the index stays internally consistent, merely offset.

So: keep every volume's size and the whole topology, and search over donors and
translations for the placement that covers the most of the route. No index work, no
reshaping, nothing that has been seen to crash.

Coverage is scored on points sampled ALONG the route, not just at its corners. A graph
that happens to contain the ten authored points while missing the spans between them
would score perfectly and fly nothing, because NavPower snaps the flyer onto the graph
and then has to get it from one point to the next.

    python3 navvolfit.py --target RiftLordNorthWingSouthWings_Map01
"""

import argparse
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.build.navigation import navvol  # noqa: E402
from riftlord.build.navigation import navvolinject  # noqa: E402

STEP = 200.0          # how finely the route is sampled, in uu
COARSE = 500.0        # first-pass translation grid
REFINE = 100.0        # second pass around the best coarse offset


def sample(route, step=STEP):
    """Points every `step` along the route, so spans count and not only corners."""
    out = []
    for i in range(len(route) - 1):
        a, b = route[i], route[i + 1]
        d = [b[k] - a[k] for k in range(3)]
        span = sum(x * x for x in d) ** 0.5
        n = max(int(span // step), 1)
        for j in range(n):
            f = j / float(n)
            out.append(tuple(a[k] + d[k] * f for k in range(3)))
    out.append(tuple(route[-1]))
    return out


def covered(boxes, pts, delta=(0.0, 0.0, 0.0)):
    """How many sampled points fall inside some volume, with the graph shifted."""
    n = 0
    for p in pts:
        q = (p[0] - delta[0], p[1] - delta[1], p[2] - delta[2])
        for lo, hi in boxes:
            if (lo[0] <= q[0] <= hi[0] and lo[1] <= q[1] <= hi[1]
                    and lo[2] <= q[2] <= hi[2]):
                n += 1
                break
    return n


def best_delta(boxes, pts, span=4000.0):
    """Search translations for the one covering the most sampled route.

    Two passes: a coarse grid over a window around the offset that aligns the two
    centroids, then a fine grid around the winner.
    """
    bcentre = [sum((lo[k] + hi[k]) / 2.0 for lo, hi in boxes) / len(boxes)
               for k in range(3)]
    pcentre = [sum(p[k] for p in pts) / len(pts) for k in range(3)]
    base = [pcentre[k] - bcentre[k] for k in range(3)]

    best = (None, -1)
    for grid, width in ((COARSE, span), (REFINE, COARSE * 2)):
        centre = best[0] or base
        rng = [int(width // grid)] * 3
        cand = []
        for ix in range(-rng[0], rng[0] + 1):
            for iy in range(-rng[1], rng[1] + 1):
                for iz in range(-rng[2], rng[2] + 1):
                    cand.append((centre[0] + ix * grid,
                                 centre[1] + iy * grid,
                                 centre[2] + iz * grid))
        for d in cand:
            c = covered(boxes, pts, d)
            if c > best[1]:
                best = (list(d), c)
    return best


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--target', required=True)
    ap.add_argument('--span', type=float, default=4000.0)
    a = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix='navvolfit')
    route = navvolinject.route_of(a.target)
    pts = sample(route)
    print('route: %d authored point(s), %d sampled every %guu'
          % (len(route), len(pts), STEP))

    rows = []
    for donor in sorted(navpatch.maps(include_mods=False)):
        d = navvol.for_map(donor, tmp)
        if not d:
            continue
        boxes = [v['box'] for v in d['volumes'] if v['box']]
        if not boxes:
            continue
        delta, c = best_delta(boxes, pts, a.span)
        rows.append((c, donor, delta, len(boxes), d['complete']))

    rows.sort(reverse=True)
    print()
    print('%-20s %8s %9s  %-28s %s'
          % ('donor', 'volumes', 'coverage', 'best translation', 'decode'))
    for c, donor, delta, nb, complete in rows:
        print('%-20s %8d %8.0f%%  %-28s %s'
              % (donor, nb, 100.0 * c / len(pts),
                 '%.0f %.0f %.0f' % tuple(delta),
                 'complete' if complete else 'PARTIAL'))
    print()
    c, donor, delta, nb, complete = rows[0]
    print('best: %s at %s covers %.0f%% of the sampled route'
          % (donor, ['%.0f' % x for x in delta], 100.0 * c / len(pts)))
    print('A partial decode means some volumes were not found, so the real graph')
    print('covers at least this much -- the score is a floor, not a ceiling.')


if __name__ == '__main__':
    main()
