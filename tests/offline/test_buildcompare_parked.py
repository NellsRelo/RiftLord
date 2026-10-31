"""Offline checks on buildcompare's "parked actors as geomedits.park writes them" certifier
(tools/buildcompare.py content_certify, through certify_level) on a real level: the
pinned copy of Cellar Alpha 1's pak (tests/fixtures/fixturelibs.PINNED; its teleporter's
sound, its Environment sublevel's moon), or any built mission pak given.

    python3 test_buildcompare_parked.py [PAK]
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
from riftlord.core import paksource  # noqa: E402
from riftlord.mapeditor import geomedits  # noqa: E402

PAK = sys.argv[1] if len(sys.argv) > 1 else (
    fixturelibs.pinned_mod('900_Cellar_Alpha_1_P.pak') or
    os.path.join(fixturelibs.PINNED_DIR, '900_Cellar_Alpha_1_P.pak (not pinned)'))
checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def level(suffix):
    umap = next((n for n in paksource.names(PAK) if n.endswith(suffix)), None)
    return (paksource.read(PAK, umap), paksource.read(PAK, umap[:-5] + '.uexp')) if umap else None


def edited(ua, ux, fn):
    """The level after fn(builder)."""
    b = levelbuild.Builder.around(ua, ux)
    fn(b)
    return b.pkg.write()


def actor_with(ua, ux, cls, words=None):
    """The package index of the first actor owning a `cls` export (that mentions one of
    `words`), or 0."""
    got = buildcompare.level_content(ua, ux)
    b = levelbuild.Builder.around(ua, ux)
    for path, (index, c, _m, props, _t) in got['exports'].items():
        if c != cls or (words and not buildcompare._mentions(props, words)):
            continue
        actor = buildcompare.struct.unpack_from('<i', b.pkg.exports[index - 1]['meta'],
                                                levelbuild.OUTER_AT)[0]
        if actor > 0 and b._root_component(actor):
            return actor
    return 0


def park_only(b, actor):
    """geomedits.park less its silence: the old code's parking."""
    apply.place_whole(b, actor, location=geomedits.park_location(b, actor))
    geomedits.hide_markers(b, actor)


if not os.path.exists(PAK):
    print('  SKIP  no %s' % PAK)
    sys.exit(0)
persistent = level('_Map01.umap')
ua, ux = persistent
actor = actor_with(ua, ux, geomedits.AUDIO_CLASS)
if not actor:
    print('  SKIP  no actor with a sound in %s' % PAK)
    sys.exit(0)
old_ua, old_ux = edited(ua, ux, lambda b: park_only(b, actor))

# 1. a parked actor's sounds silenced is certified
new_ua, new_ux = edited(old_ua, old_ux, lambda b: geomedits.silence(b, actor))
ok, detail = buildcompare.certify_level(old_ua, old_ux, new_ua, new_ux)
check('a parked actor silenced is certified', ok and buildcompare.PARKED_REASON in detail, detail)

# 2. an actor silenced where it stands is refused
loud_ua, loud_ux = edited(ua, ux, lambda b: geomedits.silence(b, actor))
ok, detail = buildcompare.certify_level(ua, ux, loud_ua, loud_ux)
check('an actor silenced but not parked is refused', not ok, detail)

# 3. a property name the parking never writes is refused
odd_ua, odd_ux = edited(old_ua, old_ux, lambda b: [
    geomedits.silence(b, actor), b.set_bool(b._root_component(actor), 'bRiftLordTestFlag', True)])
ok, detail = buildcompare.certify_level(old_ua, old_ux, odd_ua, odd_ux)
check('a name the parking never writes is refused', not ok and 'name' in detail, detail)

env = level('_Map01_Environment.umap')
moon = actor_with(*env, 'StaticMeshComponent', ('Moon',)) if env else 0
if moon:
    # 4. a moon parked as geomedits.park writes it is certified
    report = {'parked': 0}
    m_ua, m_ux = edited(*env, lambda b: geomedits.park(b, moon, report))
    ok, detail = buildcompare.certify_level(env[0], env[1], m_ua, m_ux)
    check('a moon parked is certified', ok and 'moon parked' in detail, detail)

    # 5. any other actor parked is refused
    b0 = levelbuild.Builder.around(*env)
    other = next((i + 1 for i in range(len(b0.pkg.exports))
                  if i + 1 != moon and b0._root_component(i + 1)), 0)
    if other:
        o_ua, o_ux = edited(*env, lambda b: geomedits.park(b, other, {'parked': 0}))
        ok, detail = buildcompare.certify_level(env[0], env[1], o_ua, o_ux)
        check('an actor without a moon parked is refused', not ok, detail)
else:
    print('  SKIP  no moon in an Environment sublevel of %s' % PAK)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, ': %s' % detail if not ok else ''))
print('%d checks, %d failed' % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
