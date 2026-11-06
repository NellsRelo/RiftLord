"""Offline checks on a map's moons (riftlord/build/world/moons.py).

    python3 test_moons.py

- The level's moons: the 11 shipped levels with one list it (key, material, scale as
  measured); a level without one, and a level from scratch, list none.
- Migration of plans saved before 2026-11-05: Beset (Scenario_09 under Dragon Boneyard's
  night sky) gets one added moon with the lifted moon's values; Cellar (Scenario_16 under
  the dawn sky) and Pier Pressure (Scenario_06 under the stormy sky) get their own moon in
  `removedFx`; a sky whose home is The Arena (Scenario_Sand_05) adds that moon's values;
  the level's own sky, no sky and a level from scratch's own moon change nothing; a plan
  with `moons` keeps its sky without a moon; migrating twice changes nothing.
- Turning: a moon moved without a RelativeRotation keeps the face it showed the level.
- Save round trip: records.validate_plan keeps `moons` and `addedMoons`; saving the saved
  plan changes nothing; a new plan stores an empty `moons`; bad values are refused.
- The build writes them: a level moon's direction, distance, scale and material (its
  sublevel copy passes pkgcheck, VisibilityId -1); a deleted one is parked; an added moon
  on a level with none and on a level from scratch stands where its values say, Movable,
  with its material; a migrated old plan places the moon the old build lifted.
- Map Settings' values (mapinfo `moons`): the level's moons and an added moon's values.

Reads the game's packages only (never the user's Missions folder). Exit 0 when every
check passes.
"""

import copy
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from riftlord.build import apply  # noqa: E402
from riftlord.build import levelbuild  # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.build.world import lighting  # noqa: E402
from riftlord.build.world import moons  # noqa: E402
from riftlord.core import gamefiles  # noqa: E402
from riftlord.core import pkgcheck  # noqa: E402
from riftlord.mapeditor import geomedits  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402
from riftlord.mapeditor import mapinfo  # noqa: E402
from riftlord.mapeditor import scratch  # noqa: E402
from riftlord.missions import records  # noqa: E402

#: The shipped levels with a moon: {level: (actor key, material, scale)}, measured
#: 2026-11-05 (docs/MAP-EDITOR.md "Moons").
SHIPPED = {
    'Scenario_03': ('Lighting|782', 'Moon_01_Inst', 5000.0),
    'Scenario_06': ('Lighting|1179', 'Moon_Lava_01_Inst', 5000.0),
    'Scenario_07': ('persistent|431', 'Moon_01_Inst', 5000.0),
    'Scenario_08': ('Lighting|684', 'Moon_01_Inst', 6000.0),
    'Scenario_12': ('Lighting|2547', 'Moon_Lava_01_Inst', 5000.0),
    'Scenario_14': ('Lighting|685', 'Moon_01_Inst', 5000.0),
    'Scenario_16': ('Environment|191', 'Moon_01_Inst', 5000.0),
    'Scenario_Ice_01': ('Environment|694', 'Moon_Snow_01_Inst', 4000.0),
    'Scenario_Sand_05': ('Lighting|869', 'Moon_Snow_01_Inst', 1500.0),
    'Scenario_Other_02': ('Environment|181', 'Moon_01_Inst', 5000.0),
    'Scenario_Other_03': ('Lighting|660', 'Moon_Lava_01_Inst', 5000.0),
}
#: The moon parts of the user's records, copied.
BESET = {'level': 'Scenario_09', 'look': {'sky': 'Night_Sky_Lava_INST1'}}
CELLAR = {'level': 'Scenario_16', 'look': {'sky': 'Dawn_Sky_INST'}}
PIER = {'level': 'Scenario_06', 'look': {'sky': 'Stormy_Sky_INST'}}
#: The sky whose home is The Arena (Scenario_Sand_05, a Moon_Snow_01_Inst moon).
ARENA_SKY = 'Arena_Sky_Sand01_INST'
#: A level from scratch on Scenario_01.
SCRATCH = {'level': 'Scenario_01', 'scratch': {'version': 1, 'base': 'Scenario_01',
                                                'name': 'Moon Test'}}
#: The level the build checks write into.
LEVEL = 'Scenario_06'
#: A level with no moon.
NO_MOON = 'Scenario_09'

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def close(a, b, tol=1e-2):
    if isinstance(a, (list, tuple)):
        return a is not None and b is not None and len(a) == len(b) and \
            all(close(x, y, tol) for x, y in zip(a, b))
    return abs(float(a) - float(b)) <= tol


def facing(values):
    """Degrees between the plane's face (its Z axis) and the way to the origin."""
    z = moons.rotation_rows(values['RelativeRotation'])[2]
    d = moons._unit(values['RelativeLocation'])
    return math.degrees(math.acos(max(-1.0, min(1.0, -moons._dot(z, d)))))


def shipped():
    for level, (key, material, scale) in sorted(SHIPPED.items()):
        got = moons.level_moons(level)
        ok = len(got) == 1 and got[0]['key'] == key and \
            got[0]['values']['Material'] == material and \
            close(got[0]['values']['RelativeScale3D'][0], scale)
        check('%s: its moon' % level, ok, [(m['key'], m['values']['Material'],
                                            m['values']['RelativeScale3D'][0]) for m in got])
    check('%s has no moon' % NO_MOON, moons.level_moons(NO_MOON) == [])
    check('a level from scratch has no moon', moons.level_moons('Scenario_01', SCRATCH) == [])
    info = mapinfo.info(LEVEL)
    m = info.get('moons') or {}
    check('mapinfo gives the level\'s moons, the fields and an added moon\'s values',
          [x['key'] for x in m.get('moons') or []] == [SHIPPED[LEVEL][0]] and
          m.get('added') and m.get('fields') and m.get('materials') == list(moons.MATERIALS),
          sorted(m))


def migration():
    beset = moons.migrate(copy.deepcopy(BESET))
    check('Beset: one added moon, the lifted one (the donor\'s values)',
          beset.get('addedMoons') == [{}] and beset.get('moons') == {} and
          not beset.get('removedFx'), beset)
    cellar = moons.migrate(copy.deepcopy(CELLAR))
    check('Cellar: its own moon goes into removedFx',
          cellar.get('removedFx') == [SHIPPED['Scenario_16'][0]] and
          not cellar.get('addedMoons'), cellar)
    pier = moons.migrate(dict(copy.deepcopy(PIER), removedFx=['Audio|13']))
    check('Pier Pressure: its own moon joins its removedFx',
          pier.get('removedFx') == sorted(['Audio|13', SHIPPED['Scenario_06'][0]]), pier)
    arena = moons.migrate({'level': NO_MOON, 'look': {'sky': ARENA_SKY}})
    sand = moons.level_moons('Scenario_Sand_05')[0]['values']
    entry = (arena.get('addedMoons') or [{}])[0]
    check('a sky from The Arena adds The Arena\'s moon',
          {**moons.donor_values(), **entry} == sand, entry)
    own = (leveldata.level(LEVEL).get('sky') or {}).get('material')
    got = moons.migrate({'level': 'Scenario_12', 'look': {'sky': own}})
    check('the level\'s own sky changes nothing',
          not got.get('addedMoons') and not got.get('removedFx'), got)
    got = moons.migrate({'level': LEVEL, 'look': {}})
    check('no sky changes nothing', got == {'level': LEVEL, 'look': {}, 'moons': {}}, got)
    got = moons.migrate(dict(copy.deepcopy(SCRATCH), look={'sky': 'Dawn_Sky_INST'}))
    check('a level from scratch parks nothing of its own', not got.get('removedFx'), got)
    kept = lighting.migrated(dict(copy.deepcopy(BESET), lighting={}, moons={}))
    check('a plan with `moons` keeps its sky without a moon',
          kept.get('moons') == {} and not kept.get('addedMoons'))
    again = moons.migrate(copy.deepcopy(beset))
    check('migrating a migrated plan changes nothing', again == beset)
    both = lighting.migrated(copy.deepcopy(BESET))
    check('lighting.migrated migrates the moons too',
          both.get('addedMoons') == [{}] and 'lighting' in both)
    check('lighting.migrated leaves the plan it is given as it is',
          'moons' not in BESET and 'addedMoons' not in BESET)


def turning():
    base = moons.level_moons(LEVEL)[0]['values']
    pitch, yaw, dist = moons.direction(base['RelativeLocation'])
    new = moons.location_of(20.0, yaw - 60.0, dist * 0.75)
    got = moons.resolve(base, {'RelativeLocation': new})
    after = dict(base, **got)
    check('a moved moon is turned with it', 'RelativeRotation' in got, got)
    check('a moved moon keeps the face it showed the level',
          close(facing(after), facing(base), 1e-3), (facing(base), facing(after)))
    check('the shipped values write nothing', moons.resolve(base, dict(base)) == {})
    check('a set RelativeRotation is written as set',
          moons.resolve(base, {'RelativeLocation': new, 'RelativeRotation': [1.0, 2.0, 3.0]})
          .get('RelativeRotation') == [1.0, 2.0, 3.0])
    check('a distance alone keeps the turn',
          'RelativeRotation' not in moons.resolve(
              base, {'RelativeLocation': [v * 0.5 for v in base['RelativeLocation']]}))


def round_trip():
    key = SHIPPED[LEVEL][0]
    plan = leveldata.empty_plan(LEVEL)
    plan['moons'] = {key: {'RelativeScale3D': [3000.0, 3000.0, 3000.0],
                           'Material': 'Moon_Snow_01_Inst'}}
    plan['addedMoons'] = [{'RelativeLocation': [1000.0, 2000.0, 900000.0]}, {}]
    _w, cleaned = records.validate_plan({'level': LEVEL, 'plan': plan})
    check('save keeps `moons`', cleaned.get('moons') == plan['moons'], cleaned.get('moons'))
    check('save keeps `addedMoons`', cleaned.get('addedMoons') == plan['addedMoons'],
          cleaned.get('addedMoons'))
    _w, twice = records.validate_plan({'level': LEVEL, 'plan': cleaned})
    check('saving the saved plan changes nothing', twice == cleaned)
    _w, old = records.validate_plan({'level': CELLAR['level'], 'plan': copy.deepcopy(CELLAR)})
    check('an old plan is saved migrated', old.get('moons') == {} and
          SHIPPED['Scenario_16'][0] in (old.get('removedFx') or []), old.get('removedFx'))
    check('a new plan stores an empty `moons`', leveldata.empty_plan(LEVEL).get('moons') == {})
    for bad in ({key: {'Material': 'Lava_Sky_INST'}}, {key: {'RelativeLocation': [1, 2]}},
                {key: {'NoSuchProperty': 1.0}}):
        try:
            moons.clean(bad)
            refused = False
        except ValueError:
            refused = True
        check('a bad moon value is refused: %s' % bad, refused)


def root_values(pkg, actor):
    root = levelbuild.Builder.__new__(levelbuild.Builder)
    root.pkg = pkg
    comp = root._root_component(actor)
    names = [n for n, _h in pkg.names]
    data = pkg.exports[comp - 1]['data']
    vis = None
    mobility = None
    for name, typ, a, b in lighting.tags(data, names):
        if name == 'VisibilityId':
            vis = struct.unpack_from('<i', data, a + 25)[0]
        if name == 'Mobility':
            mobility = lighting.read_value(data, names, typ, a, b)
    return comp, moons.component_values(pkg, comp), vis, mobility


def actor_named(pkg, name):
    return next((i for i in range(1, len(pkg.exports) + 1)
                 if levelbuild._name_of(pkg, i - 1) == name), None)


def build_writes():
    key = SHIPPED[LEVEL][0]
    tag, actor = key.split('|')[0], int(key.split('|')[1])
    base = moons.level_moons(LEVEL)[0]['values']
    _p, yaw, dist = moons.direction(base['RelativeLocation'])
    where = [moons.f32(v) for v in moons.location_of(30.0, yaw + 45.0, dist * 0.8)]
    plan = {'level': LEVEL, 'lighting': {},
            'moons': {key: {'RelativeLocation': where,
                            'RelativeScale3D': [2500.0, 2500.0, 2500.0],
                            'Material': 'Moon_01_Inst'}}}
    edits = apply.sublevel_edits(LEVEL, plan)
    check('sublevel_edits hands the moon to its sublevel',
          actor in ((edits.get(tag) or {}).get('moons') and
                    [lighting._outer(lighting._load(navpatch.map_path(LEVEL) + '_' + tag), c)
                     for c in edits[tag]['moons']] or []), sorted(edits))
    path = '%s_%s' % (navpatch.map_path(LEVEL), tag)
    ua, ux = gamefiles.package(path)
    a, x, report = apply.apply_to_sublevel(ua, ux, 'MoonTest_Map01_%s' % tag, edits[tag])
    problems = pkgcheck.problems(a, x, 'MoonTest_Map01_%s' % tag)
    check('the %s copy passes pkgcheck' % tag, not problems, problems[:3])
    back = levelbuild.Builder.around(a, x, name='back')
    _c, got, vis, _m = root_values(back.pkg, actor)
    check('a level moon\'s direction and distance', close(got['RelativeLocation'], where, 1.0),
          got['RelativeLocation'])
    check('a level moon\'s scale', close(got['RelativeScale3D'], [2500.0] * 3))
    check('a level moon\'s material', got['Material'] == 'Moon_01_Inst', got['Material'])
    check('a level moon keeps its face to the level',
          close(facing(got), facing(base), 1e-2), (facing(base), facing(got)))
    check('a moved level moon\'s VisibilityId is -1', vis == geomedits.NO_VISIBILITY_ID, vis)

    gone = apply.sublevel_edits(LEVEL, {'level': LEVEL, 'lighting': {}, 'moons': {},
                                        'removedFx': [key]})
    check('a deleted level moon is parked', actor in ((gone.get(tag) or {}).get('parked') or []),
          gone.get(tag))
    check('a deleted level moon is not written',
          not (gone.get(tag) or {}).get('moons'))

    # an added moon on a level with none, and two from one entry list
    level_path = navpatch.map_path(NO_MOON)
    added = {'RelativeLocation': [100000.0, -200000.0, 800000.0],
             'RelativeScale3D': [3000.0, 3000.0, 3000.0], 'Material': 'Moon_Snow_01_Inst'}
    plan = dict(leveldata.empty_plan(NO_MOON), addedMoons=[added, {}])
    ua, ux = gamefiles.package(level_path)
    a, x, report = apply.apply_plan(ua, ux, NO_MOON, level_path, 'MoonTest_Map01', plan)
    problems = pkgcheck.problems(a, x, 'MoonTest_Map01')
    check('the level with added moons passes pkgcheck', not problems, problems[:3])
    back = levelbuild.Builder.around(a, x, name='back')
    first = actor_named(back.pkg, moons.ADDED_NAME)
    second = actor_named(back.pkg, moons.ADDED_NAME + 'A')
    check('two added moons, named apart', first and second, (first, second))
    if first:
        _c, got, vis, mob = root_values(back.pkg, first)
        donor = moons.donor_values()
        check('an added moon stands where its values say',
              close(got['RelativeLocation'], added['RelativeLocation'], 1.0) and
              close(got['RelativeScale3D'], added['RelativeScale3D']) and
              got['Material'] == 'Moon_Snow_01_Inst', got)
        check('an added moon moved from the donor keeps the donor\'s face to the level',
              close(facing(got), facing(donor), 1e-2), (facing(donor), facing(got)))
        check('an added moon is Movable, VisibilityId -1',
              mob == lighting.MOVABLE and vis == geomedits.NO_VISIBILITY_ID, (mob, vis))
    if second:
        _c, got, _v, _m = root_values(back.pkg, second)
        check('an added moon with no values is the donor\'s', got == moons.donor_values(), got)

    # a migrated old plan places the moon the old build lifted (skyfog.lift_extras)
    old = dict(leveldata.empty_plan(NO_MOON), look=dict(BESET['look']))
    del old['moons']
    old = lighting.migrated(old)
    ua, ux = gamefiles.package(level_path)
    a, x, report = apply.apply_plan(ua, ux, NO_MOON, level_path, 'MoonTest_Map01', old)
    back = levelbuild.Builder.around(a, x, name='back')
    lifted = actor_named(back.pkg, moons.ADDED_NAME)
    check('Beset migrated: the old lifted moon, as Scenario_12 ships it',
          lifted and root_values(back.pkg, lifted)[1] ==
          moons.level_moons('Scenario_12')[0]['values'], report.get('notes'))

    # an added moon on a level from scratch
    splan = scratch.new_plan('Moon Test')
    splan['addedMoons'] = [added]
    host = splan['level']
    host_path = navpatch.map_path(host)
    ua, ux = gamefiles.package(host_path)
    a, x, report = apply.apply_plan(ua, ux, host, host_path, 'MoonTest_Map01', splan)
    back = levelbuild.Builder.around(a, x, name='back')
    there = actor_named(back.pkg, moons.ADDED_NAME)
    check('a level from scratch takes an added moon',
          there and close(root_values(back.pkg, there)[1]['RelativeLocation'],
                          added['RelativeLocation'], 1.0), report.get('skipped'))
    check('a level from scratch\'s moon is not parked with the host\'s decoration',
          there and root_values(back.pkg, there)[1]['RelativeLocation'][2] > 0)


def main():
    print('shipped moons')
    shipped()
    print('migration')
    migration()
    print('turning')
    turning()
    print('save round trip')
    round_trip()
    print('the build')
    build_writes()
    bad = [c for c in checks if not c[1]]
    print('%d of %d checks pass' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
