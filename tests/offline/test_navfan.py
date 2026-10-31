"""Offline checks on vertex fans (riftlord/build/navigation/navfan.py): the writer clean-up on
the two measured defect shapes, the fan-walk check on a written buffer, and every shipped
map's graphs (which must have no defect).

    python3 test_navfan.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.build.navigation import navfan  # noqa: E402
from riftlord.build.navigation import navsurfgen  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def buffer_of(polys):
    """A sealed two-graph surface buffer of `polys` (both agents), ids and pointers derived."""
    ids = navsurfgen.allocate_edge_ids(polys)
    item = (polys, ids, [2] * len(polys), None, None, None, None)
    return navsurfgen.buffer_per_agent([item, item], navsurfgen.bbox_of(polys))


# 1. Crystal Probe A's shape: a zero-area polygon folded back along two edges of its neighbour
p70 = [(1800.0, 3400.0, 460.0), (2313.9, 3325.0, 460.0), (2173.9, 4605.0, 460.0)]
p71 = [(2173.9, 4605.0, 460.0), (2313.9, 3325.0, 460.0), (2333.9, 3145.0, 460.0),
       (2313.9, 3325.0, 460.0)]
p72 = [(2173.9, 4605.0, 460.0), (2313.9, 3325.0, 460.0), (2333.9, 3145.0, 460.0),
       (3673.9, 3325.0, 460.0)]
got, notes = navfan.clean([p70, p71, p72])
check('a folded zero-area polygon is dropped', len(got) == 2 and p71 not in got, notes)

# 2. Cellar Alpha 1's shape: two polygons sharing a straight run split by a vertex only they use
A, B = (-1476.8, -6366.3, 1959.0), (-1356.8, -7046.3, 1974.0)
M, C, D = (-1150.1, -7046.3, 1974.0), (-736.8, -7046.3, 1974.0), (-1150.1, -7179.6, 2039.0)
two = [[A, B, M, C], [C, M, B, D]]
bad = navfan.problems(buffer_of(two))
check('two polygons on two edges are refused, naming the polygon',
      any('shares more than one edge with polygon' in b and 'polygon' in b for b in bad), bad)
got, notes = navfan.clean(two)
check('the run is joined into one edge', got == [[A, B, C], [C, B, D]], got)
check('the joined pair passes the fan walk', navfan.problems(buffer_of(got)) == [],
      navfan.problems(buffer_of(got)))

# 3. a clean mesh is returned as it is (built graphs stay byte-identical)
grid = [[(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0)],
        [(100.0, 0.0, 0.0), (200.0, 0.0, 0.0), (200.0, 100.0, 0.0), (100.0, 100.0, 0.0)]]
got, notes = navfan.clean(grid)
check('a clean mesh is left alone', got is grid and not notes, notes)
check('a clean mesh passes the fan walk', navfan.problems(buffer_of(grid)) == [])

# 4. the walk itself: a cycle that excludes the start polygon never closes
ptr = [[1, None, None], [2, 2, 0], [1, 1, None]]
check('a fan cycle that excludes the start never closes',
      navfan.walk(ptr, 0, 0, 1, 50) == 'never')
check('a fan that reaches a border stops', navfan.walk(ptr, 0, 1, 1, 50) == 'border')
check('a closed fan returns to its start',
      navfan.walk([[1, 1], [0, 0]], 0, 0, 1, 50) == 'closed')

# 5. every shipped map: no defect
graphs, polys, defects = navfan.selftest(log=lambda s: None)
check('shipped graphs have no fan defect (%d graphs, %d polygons)' % (graphs, polys),
      graphs > 0 and defects == 0, defects)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d checks, %d failed' % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
