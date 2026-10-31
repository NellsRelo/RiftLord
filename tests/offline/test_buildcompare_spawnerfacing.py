"""Offline checks on buildcompare's "added spawners face their routes (spawnerfacing)"
certifier (tools/buildcompare.py spawnerfacing_apply, certify_level) on a real level: the
pinned copy of Cellar Alpha 1's pak (tests/fixtures/fixturelibs.PINNED), or any built
mission pak with added spawners given.

    python3 test_buildcompare_spawnerfacing.py [PAK]
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
sys.path.insert(0, os.path.join(ROOT, 'tests', 'fixtures'))

import buildcompare  # noqa: E402
import fixturelibs  # noqa: E402
from riftlord.build import apply  # noqa: E402
from riftlord.build import levelbuild  # noqa: E402
from riftlord.build.navigation import navfan  # noqa: E402
from riftlord.build.navigation import navsurf  # noqa: E402
from riftlord.build.navigation import navsurfextend  # noqa: E402
from riftlord.build.navigation import navsurfgen  # noqa: E402
from riftlord.build.navigation import navsurfinject  # noqa: E402
from riftlord.core import paksource  # noqa: E402
from riftlord.mapeditor import spawnerfacing  # noqa: E402

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


def turned(ua, ux, yaws):
    """The level with each added spawner {name: yaw} given that root yaw."""
    b = levelbuild.Builder.around(ua, ux)
    for name, yaw in yaws.items():
        apply.place_whole(b, b.index_of(name), rotation=(0.0, yaw, 0.0))
    return b.pkg.write()


if not os.path.exists(PAK):
    print('  SKIP  no %s' % PAK)
    sys.exit(0)
umap = next(n for n in paksource.names(PAK) if n.endswith('_Map01.umap'))
ua, ux = paksource.read(PAK, umap), paksource.read(PAK, umap[:-5] + '.uexp')
rows = spawnerfacing.read_level(ua, ux)
if not rows:
    print('  SKIP  no added spawner with a route in %s' % PAK)
    sys.exit(0)
old_ua, old_ux = turned(ua, ux, {r['name']: 0.0 for r in rows})
facing = {r['name']: r['want'] for r in spawnerfacing.read_level(old_ua, old_ux)}
new_ua, new_ux = turned(old_ua, old_ux, facing)

# 1. added spawners turned from yaw 0 to the rule are certified
ok, detail = buildcompare.certify_level(old_ua, old_ux, new_ua, new_ux)
check('spawners facing their routes are certified',
      ok and detail.startswith(buildcompare.SPAWNERFACING_REASON), detail)

# 2. a yaw that is not the rule fails
name = sorted(facing)[0]
off_ua, off_ux = turned(old_ua, old_ux, dict(facing, **{name: facing[name] + 10.0}))
ok, detail = buildcompare.certify_level(old_ua, old_ux, off_ua, off_ux)
check('a yaw off the rule is refused', not ok and 'not spawnerfacing' in detail, detail)

# 3. an old spawner that was turned fails
was_ua, was_ux = turned(old_ua, old_ux, {name: 30.0})
ok, detail = buildcompare.certify_level(was_ua, was_ux, new_ua, new_ux)
check('a spawner turned in the old level is refused', not ok and 'turned in the old' in detail,
      detail)

# 4. a byte changed elsewhere fails
bad_ux = bytearray(new_ux)
bad_ux[64] ^= 0xFF
ok, detail = buildcompare.certify_level(old_ua, old_ux, new_ua, bytes(bad_ux))
check('a change outside the spawners is refused', not ok, detail)

# 5. facing spawners and a cleaned walkable graph in the same level are certified
both_ua, both_ux, _rows = navsurfinject.set_surface_bytes(
    new_ua, new_ux, cleaned_buffer(navsurfextend._surface_of(new_ua, new_ux)))
ok, detail = buildcompare.certify_level(old_ua, old_ux, both_ua, both_ux)
check('facing spawners with a cleaned graph are certified', ok and
      buildcompare.SPAWNERFACING_REASON in detail and
      (buildcompare.NAVFAN_REASON in detail or (both_ua, both_ux) == (new_ua, new_ux)), detail)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, ': %s' % detail if not ok else ''))
print('%d checks, %d failed' % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
