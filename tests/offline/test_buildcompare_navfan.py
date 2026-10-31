"""Offline checks on buildcompare's "walkable graph cleaned (navfan)" certifier
(tools/buildcompare.py navfan_certify) on a real level: the pinned copy of Cellar Alpha 1's
pak (tests/fixtures/fixturelibs.PINNED), or any built mission pak given.

    python3 test_buildcompare_navfan.py [PAK]
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
sys.path.insert(0, os.path.join(ROOT, 'tests', 'fixtures'))

import buildcompare  # noqa: E402
import fixturelibs  # noqa: E402
from riftlord.build.navigation import navfan  # noqa: E402
from riftlord.build.navigation import navsurf  # noqa: E402
from riftlord.build.navigation import navsurfextend  # noqa: E402
from riftlord.build.navigation import navsurfgen  # noqa: E402
from riftlord.build.navigation import navsurfinject  # noqa: E402
from riftlord.build.navigation import navvol  # noqa: E402
from riftlord.core import paksource  # noqa: E402

PAK = sys.argv[1] if len(sys.argv) > 1 else (
    fixturelibs.pinned_mod('900_Cellar_Alpha_1_P.pak') or
    os.path.join(fixturelibs.PINNED_DIR, '900_Cellar_Alpha_1_P.pak (not pinned)'))
checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def cleaned_buffer(buf):
    """`buf`'s polygons through navfan.clean, written again (ids and pointers derived)."""
    items, first = [], None
    for head in navsurf.graphs(buf):
        polys = [[tuple(v) for v in s['verts']] for s in navsurf.sections(buf, head)]
        polys, _notes = navfan.clean(polys)
        first = first or polys
        items.append((polys, navsurfgen.allocate_edge_ids(polys), [2] * len(polys),
                      None, None, None, None))
    return navsurfgen.buffer_per_agent(items, navsurfgen.bbox_of(first))


if not os.path.exists(PAK):
    print('  SKIP  no %s' % PAK)
    sys.exit(0)
umap = next(n for n in paksource.names(PAK) if n.endswith('_Map01.umap'))
old_ua, old_ux = paksource.read(PAK, umap), paksource.read(PAK, umap[:-5] + '.uexp')
old_buf = navsurfextend._surface_of(old_ua, old_ux)
new_buf = cleaned_buffer(old_buf)
new_ua, new_ux, _rows = navsurfinject.set_surface_bytes(old_ua, old_ux, new_buf)

# 1. a level whose only change is its cleaned walkable graph is certified
ok, detail = buildcompare.navfan_certify(old_ua, old_ux, new_ua, new_ux)
check('the cleaned graph is certified', ok, detail)

# 2. a byte changed outside the walkable-graph buffer fails
bad_ux = bytearray(new_ux)
bad_ux[64] ^= 0xFF
ok, detail = buildcompare.navfan_certify(old_ua, old_ux, new_ua, bytes(bad_ux))
check('a change outside the graph is refused', not ok and 'outside' in detail, detail)

# 3. a graph with fan defects fails (the old one as the new, when it has any)
if navfan.problems(old_buf):
    ok, detail = buildcompare.navfan_certify(new_ua, new_ux, old_ua, old_ux)
    check('a graph with fan defects is refused', not ok and 'fan defects' in detail, detail)

# 4. a polygon moved (not what navfan.clean makes) fails
head = navsurf.graphs(new_buf)[0]
first = navsurf.sections(new_buf, head)[0]
moved = bytearray(new_buf)
z = struct.unpack_from('<f', moved, first['at'] + 48 + 8)[0]
struct.pack_into('<f', moved, first['at'] + 48 + 8, z + 3.0)
moved = navvol.reseal(bytes(moved))
m_ua, m_ux, _rows = navsurfinject.set_surface_bytes(old_ua, old_ux, moved)
ok, detail = buildcompare.navfan_certify(old_ua, old_ux, m_ua, m_ux)
check('a polygon the clean does not make is refused', not ok and 'not the old ones' in detail,
      detail)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, ': %s' % detail if not ok else ''))
print('%d checks, %d failed' % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
