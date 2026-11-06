"""Offline checks on a map's lighting by engine property (riftlord/build/world/lighting.py)
and the migration of older plans (riftlord/build/world/lightmigrate.py).

    python3 test_lighting.py

- Migration: copies of Repaired Mine's and Beset's lighting (a sky from another level,
  Repaired Mine's red fog colour), a level from scratch with a sky and fog colours, and an
  old `lightColours` turn into `lighting` and `lights` values that write exactly the
  lighting components the old build wrote (lightmigrate.check), with nothing lost; the old
  world keys and `lightColours` go; migrating twice changes nothing; a plan that has
  `lighting` keeps its sky without its home's lighting.
- The bake of a migrated plan: Beset's sky light and fog as the old bake took them from
  the sky's home (colour, brightness, indirect, lower hemisphere, no cubemap).
- Save round trip: records.validate_plan keeps `lighting`, `lights` and an added light's
  `light`; saving the saved plan changes nothing; a new plan stores an empty `lighting`.
- The build writes each field: every Map Settings field of each section, a sun's colour,
  intensity, direction and shadows, a deleted sun (bVisible off), the fog height, a
  post-process value with its override, a placed light's intensity, radius, mobility and
  cones, an added light's values and a level from scratch's rig; pkgcheck passes.
- Bake inputs: a Sky Light change and a changed, moved or removed Static light make the
  light maps stale, a sun or fog change does not; bake_spec carries them and lightscene's
  lights and sky light take them.

Reads the game's packages only (never the user's Missions folder). Exit 0 when every
check passes.
"""

import copy
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from riftlord.build import apply  # noqa: E402
from riftlord.build import levelbuild  # noqa: E402
from riftlord.build.lighting import lightbake  # noqa: E402
from riftlord.build.lighting import lightscene  # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.build.world import lighting  # noqa: E402
from riftlord.build.world import lightmigrate  # noqa: E402
from riftlord.core import gamefiles  # noqa: E402
from riftlord.core import pkgcheck  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402
from riftlord.missions import records  # noqa: E402

#: The lighting parts of the user's records, copied: Repaired Mine (Scenario_Ice_02 under
#: the Lava sky, Scenario_01's, with a red fog colour) and Beset (Scenario_09 under
#: Dragon Boneyard's night sky).
REPAIRED_MINE = {'level': 'Scenario_Ice_02', 'look': {'sky': 'Lava_Sky_INST'},
                 'world': {'fogColour': [0.9216, 0.1195, 0.0823, 1.0]}}
BESET = {'level': 'Scenario_09', 'look': {'sky': 'Night_Sky_Lava_INST1'}}
#: A level from scratch on Scenario_01 under the stormy sky, with its own fog colours.
SCRATCH = {'level': 'Scenario_01', 'scratch': {'version': 1, 'base': 'Scenario_01',
                                                'name': 'Lighting Test'},
           'look': {'sky': 'Stormy_Sky_INST'},
           'world': {'fogColour': [0.2747, 0.8714, 0.3712, 1.0],
                     'fogSunColour': [0.9473, 0.8714, 0.147, 1.0]}}
#: The level the build checks write into.
LEVEL = 'Scenario_09'
GREEN = '#00890c'

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def close(a, b, tol=1e-3):
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    return abs(float(a) - float(b)) <= tol


def migration():
    for name, plan in (('Repaired Mine', REPAIRED_MINE), ('Beset', BESET),
                       ('a level from scratch', SCRATCH)):
        diffs = lightmigrate.check(plan['level'], copy.deepcopy(plan))
        check('%s: the migrated values write the old lighting' % name, not diffs, diffs[:4])
        _s, _l, lost = lightmigrate.legacy_values(plan['level'], plan)
        check('%s: nothing the old build wrote is lost' % name, not lost, lost[:4])
    mine = lighting.migrate(copy.deepcopy(REPAIRED_MINE))
    fog = (mine.get('lighting') or {}).get('heightFog') or {}
    check('Repaired Mine: its red fog colour is the Height Fog\'s Fog Inscattering Color',
          close(fog.get('FogInscatteringColor') or [0], REPAIRED_MINE['world']['fogColour']),
          fog.get('FogInscatteringColor'))
    check('Repaired Mine: the old world keys are gone', 'fogColour' not in mine.get('world', {}))
    suns = [k for k in mine.get('lights') or {} if k.startswith('Lighting|')]
    check('Repaired Mine: its four suns take the sky home\'s values', len(suns) == 4, suns)
    beset = lighting.migrate(copy.deepcopy(BESET))
    check('Beset: sky light, atmosphere, height fog and post process values',
          set(beset['lighting']) == {'skyLight', 'atmosphere', 'heightFog', 'postProcess'},
          sorted(beset['lighting']))
    again = lighting.migrate(copy.deepcopy(beset))
    check('migrating a migrated plan changes nothing', again == beset)
    kept = lighting.migrated(dict(copy.deepcopy(BESET), lighting={}))
    check('a plan with `lighting` keeps its sky without its home\'s lighting',
          kept.get('lighting') == {} and not kept.get('lights'))

    # an old plan's light colours
    lv = leveldata.level('Scenario_16')
    point = next(l for l in lv['lights'] if l['kind'] == 'PointLight' and l.get('actor'))
    sun = next(l for l in lv['lights'] if l['kind'] == 'DirectionalLight')
    key = lambda l: '%s|%d' % (l.get('sub') or 'persistent', l['actor'])  # noqa: E731
    got = lighting.migrate({'level': 'Scenario_16',
                            'lightColours': {key(point): GREEN, key(sun): GREEN}})
    check('lightColours become lights\' Light Color, never on a sun',
          got.get('lights') == {key(point): {'LightColor': GREEN}} and
          'lightColours' not in got, got.get('lights'))

    # the bake: Beset's sky light and fog as the old bake took them from the sky's home
    old = lightscene.sky_light('Scenario_12')
    new = lightscene.sky_light('Scenario_09', edits=beset['lighting'].get('skyLight'))
    for field in ('colour', 'brightness', 'indirect', 'lower_black', 'lower_colour', 'cubemap'):
        check('Beset bake: the sky light\'s %s is the old bake\'s' % field,
              (old[field] == new[field]) if not isinstance(old[field], (list, float))
              else close(old[field], new[field]), (old[field], new[field]))
    home_fog = lightmigrate.legacy_world(BESET)
    want = lightbake.sky_fog('Scenario_09', beset)
    check('Beset bake: the sky is seen through the old bake\'s fog colour',
          close(want['colour'], home_fog['fogColour'][:3]), (want, home_fog.get('fogColour')))


def round_trip():
    plan = lighting.migrate(copy.deepcopy(REPAIRED_MINE))
    plan['added'] = [{'name': 'PointLight', 'kind': 'kit', 'matrix': [
        1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 300.0, 1.0],
        'light': {'Intensity': 42.0, 'LightColor': GREEN}}]
    rec = {'level': plan['level'], 'plan': plan}
    _w, cleaned = records.validate_plan(rec)
    check('save keeps `lighting`', cleaned.get('lighting') == plan['lighting'],
          sorted(cleaned.get('lighting') or {}))
    check('save keeps `lights`', cleaned.get('lights') == plan['lights'])
    check('save keeps an added light\'s `light`',
          (cleaned.get('added') or [{}])[0].get('light') == {'Intensity': 42.0,
                                                               'LightColor': GREEN})
    _w, twice = records.validate_plan({'level': plan['level'], 'plan': cleaned})
    check('saving the saved plan changes nothing', twice == cleaned)
    _w, old = records.validate_plan({'level': REPAIRED_MINE['level'],
                                     'plan': copy.deepcopy(REPAIRED_MINE)})
    check('an old plan is saved migrated', old.get('lighting') == plan['lighting'] and
          'fogColour' not in old.get('world', {}))
    check('a new plan stores an empty `lighting`',
          leveldata.empty_plan(LEVEL).get('lighting') == {})
    for bad in ({'heightFog': {'FogDensity': 'thick'}}, {'nosuch': {}},
                {'heightFog': {'NoSuchProperty': 1.0}}):
        try:
            lighting.clean(bad)
            refused = False
        except ValueError:
            refused = True
        check('a bad lighting value is refused: %s' % bad, refused)


def written(level, tag, edits):
    """(Builder over the written package, pkgcheck problems) of one sublevel."""
    base = navpatch.map_path(level)
    ua, ux = gamefiles.package(base if tag == 'persistent' else '%s_%s' % (base, tag))
    a, x, report = apply.apply_to_sublevel(ua, ux, 'LightTest_Map01_%s' % tag, edits)
    return levelbuild.Builder.around(a, x, name='back'), pkgcheck.problems(
        a, x, 'LightTest_Map01_%s' % tag), report


def values_of(pkg, index, settings=False):
    names = [n for n, _h in pkg.names]
    data = pkg.exports[index - 1]['data']
    if settings:
        a, b = lighting._settings_span(data, names)
        return lighting.values(data, names, a + 49, b)
    return lighting.values(data, names)


def build_writes():
    state = lighting.state(LEVEL)
    plan = {'level': LEVEL, 'lighting': {}, 'lights': {}}
    want = {}
    for section, fields in lighting.FIELDS.items():
        if section not in lighting.SECTIONS or section not in state['sections']:
            continue
        props = {}
        for f in fields:
            kind = f['kind']
            if kind == 'float':
                props[f['prop']] = round((f['min'] + f['max']) / 3.0 + 0.125, 3)
            elif kind == 'color':
                props[f['prop']] = GREEN
            elif kind in ('linear', 'vector4'):
                props[f['prop']] = [0.25, 0.5, 0.75, 1.0]
            elif kind == 'bool':
                props[f['prop']] = not state['sections'][section].get(f['prop'])
        plan['lighting'][section] = props
        want[section] = props
    suns = state['suns']
    plan['lights'][suns[0]['key']] = {'LightColor': GREEN, 'Intensity': 2.5,
                                      'RelativeRotation': [-30.0, 45.0, 0.0],
                                      'CastShadows': False}
    plan['removedFx'] = [suns[1]['key']]
    lv = leveldata.level(LEVEL)
    point = next(l for l in lv['lights'] if l['kind'] == 'PointLight' and l.get('actor') and
                 (l.get('sub') or 'persistent') != 'persistent')
    pkey = '%s|%d' % (point['sub'], point['actor'])
    plan['lights'][pkey] = {'Intensity': 77.0, 'AttenuationRadius': 1234.0,
                            'Mobility': lighting.MOVABLE, 'CastShadows': False}
    edits = apply.sublevel_edits(LEVEL, plan)
    tags = {t for t, e in edits.items() if e.get('lighting')}
    check('sublevel_edits hands the lighting to the packages holding it', 'Lighting' in tags, tags)
    backs = {}
    for tag in sorted(tags - {'persistent'}):
        back, problems, _report = written(LEVEL, tag, edits[tag])
        backs[tag] = back
        check('%s copy passes pkgcheck' % tag, not problems, problems[:3])
    for tag, pkg in lighting.level_packages(LEVEL):
        if tag not in backs:
            continue
        back = backs[tag].pkg
        found = lighting.survey(pkg)
        for section, exports in found['sections'].items():
            for index in exports:
                got = values_of(back, index, section == 'postProcess')
                for prop, value in want.get(section, {}).items():
                    if prop == lighting.FOG_HEIGHT:
                        ok = close(got['RelativeLocation'][2], value)
                    else:
                        ok = close(got.get(prop), value) if isinstance(value, (float, list)) \
                            else got.get(prop) == value
                    check('%s %s %s written' % (tag, section, prop), ok, (got.get(prop), value))
                if section == 'postProcess':
                    check('%s post process: Saturation\'s override on' % tag,
                          got.get('bOverride_ColorSaturation') is True)
        for actor, comps in found['lights'].items():
            key = '%s|%d' % (tag, actor)
            for index, cls in comps:
                got = values_of(back, index)
                if key == suns[0]['key']:
                    check('the sun\'s colour, intensity, direction and shadows',
                          got.get('LightColor') == GREEN and close(got.get('Intensity'), 2.5)
                          and close(got.get('RelativeRotation'), [-30.0, 45.0, 0.0])
                          and got.get('CastShadows') is False, got)
                elif key == suns[1]['key']:
                    check('a deleted sun is off', got.get('bVisible') is False, got)
                elif key == pkey:
                    check('a placed light\'s intensity, radius, mobility and shadows',
                          close(got.get('Intensity'), 77.0) and
                          close(got.get('AttenuationRadius'), 1234.0) and
                          got.get('Mobility') == lighting.MOVABLE and
                          got.get('CastShadows') is False, got)

    # a spot light's cones, on a level that places one
    for level in ('ScenarioDLC_01', 'ScenarioDLC_02'):
        spot = next((l for l in leveldata.level(level)['lights'] if l['kind'] == 'SpotLight'
                     and l.get('actor')), None)
        if spot:
            break
    skey = '%s|%d' % (spot.get('sub') or 'persistent', spot['actor'])
    sedits = apply.sublevel_edits(level, {'level': level, 'lighting': {},
                                          'lights': {skey: {'InnerConeAngle': 12.0,
                                                            'OuterConeAngle': 33.0}}})
    tag = spot.get('sub') or 'persistent'
    if tag == 'persistent':
        back = levelbuild.Builder.around(*gamefiles.package(navpatch.map_path(level)), name='s')
        lighting.write(back, sedits[tag]['lighting'], {'notes': [], 'skipped': []})
    else:
        back, _p, _r = written(level, tag, sedits[tag])
    got = values_of(back.pkg, spot['export'])
    check('a spot light\'s cones (%s %s)' % (level, skey),
          close(got.get('InnerConeAngle'), 12.0) and close(got.get('OuterConeAngle'), 33.0), got)

    # an added light's values
    builder = levelbuild.Builder.around(*gamefiles.package(navpatch.map_path(LEVEL)), name='a')
    donor = apply.donor_for('PointLight')
    actor = builder.lift_actor(donor[0], donor[1], rename='LightTestLamp')
    report = {'notes': [], 'skipped': []}
    lighting.write_added(builder, [{'kind': 'kit', 'name': 'PointLight',
                                    'light': {'Intensity': 42.0, 'LightColor': GREEN}}],
                         {0: actor}, report)
    comps = [i for i in range(1, len(builder.pkg.exports) + 1)
             if lighting._outer(builder.pkg, i) == actor and
             lighting._class_of(builder.pkg, i) in lighting.LIGHT_CLASSES]
    got = values_of(builder.pkg, comps[0]) if comps else {}
    check('an added light\'s values', close(got.get('Intensity', 0), 42.0) and
          got.get('LightColor') == GREEN, got)

    # a level from scratch: its light rig
    rig_state = lighting.state('Scenario_01', SCRATCH)
    rkey = rig_state['suns'][0]['key']
    rplan = dict(copy.deepcopy(SCRATCH), lighting={'heightFog': {'FogDensity': 0.33}},
                 lights={rkey: {'Intensity': 4.5}})
    (ua, ux), _r = __import__('riftlord.build.authorlevel', fromlist=['x']).lighting('LightTest')
    rig = levelbuild.Builder.around(ua, ux, name='LightTest_Map01_Lighting')
    lighting.write_rig(rig, rplan, {'notes': [], 'skipped': []})
    fog = lighting.survey(rig.pkg)['sections']['heightFog'][0]
    sun = next(i for i, c in lighting.survey(rig.pkg)['lights'][int(rkey.split('|')[1])])
    check('a level from scratch: its rig\'s fog and sun',
          close(values_of(rig.pkg, fog).get('FogDensity'), 0.33) and
          close(values_of(rig.pkg, sun).get('Intensity'), 4.5))


def bake_inputs():
    lv = leveldata.level(LEVEL)
    rows = leveldata.light_rig(LEVEL)
    rig = {l['key']: l for l in rows}
    #: a Static point light that is its actor's only light
    static = next(k for k, l in rig.items() if str(l['mobility']).endswith('Static')
                  and l['kind'] == 'point' and sum(1 for r in rows if r['key'] == k) == 1)
    sun = lighting.state(LEVEL)['suns'][0]['key']
    base = {'level': LEVEL, 'lighting': {}}
    stale = lambda **kw: lightbake.stale(dict(base, **kw))  # noqa: E731
    check('a Sky Light change bakes', 'sky light' in stale(lighting={'skyLight': {'Intensity': 2.0}}))
    check('a changed Static light bakes',
          '1 changed light' in stale(lights={static: {'Intensity': 9.0}}), stale(lights={static: {'Intensity': 9.0}}))
    check('a moved Static light bakes', '1 changed light' in stale(actorMoves={static: [10.0, 0.0, 0.0]}))
    check('a removed Static light bakes', '1 changed light' in stale(removedFx=[static]))
    check('a sun change does not bake', stale(lights={sun: {'Intensity': 9.0}}) == [])
    check('a fog change does not bake', stale(lighting={'heightFog': {'FogDensity': 0.5}}) == [])
    plan = dict(base, lighting={'skyLight': {'Intensity': 2.0}},
                lights={static: {'Intensity': 9.0, 'LightColor': GREEN}},
                actorMoves={static: [10.0, 0.0, 0.0]})
    spec = lightbake.bake_spec(LEVEL, plan)
    check('bake_spec carries the lights\' values, moves and the Sky Light',
          spec['light_edits'].get(static) == plan['lights'][static] and
          spec['light_moves'].get(static) == [10.0, 0.0, 0.0] and
          spec['sky_light_edit'] == {'Intensity': 2.0})
    shipped = lightscene.sky_light(LEVEL)
    edited = lightscene.sky_light(LEVEL, edits=spec['sky_light_edit'])
    check('the baker\'s sky light takes the Intensity', close(edited['brightness'], 2.0),
          (shipped['brightness'], edited['brightness']))
    guids = set()
    for path in lightscene.packages(LEVEL):
        bd = lightscene.registry(path)
        if bd is not None:
            guids |= lightscene.static_light_guids(bd)
    before = {(l['package'], l['export']): l for l in lightscene.lights(LEVEL, guids)}
    after = {(l['package'], l['export']): l for l in lightscene.lights(
        LEVEL, guids, edits=spec['light_edits'], moves=spec['light_moves'])}
    tag, actor = static.split('|')
    mine = [k for k in before if lightscene.tag_of(LEVEL, k[0]) == tag and
            lightscene.Package(k[0]).exports[k[1] - 1].get('outer') == int(actor)]
    check('the baker sees the light', bool(mine), static)
    for k in mine:
        a, b = before[k], after[k]
        check('the baker\'s light takes the colour, the brightness and the move',
              close(b['colour'], lightscene.linear_colour((0x00, 0x89, 0x0c))) and
              close(b['brightness'] / a['brightness'], 9.0 / rig[static]['values']['Intensity'])
              and close(b['position'][0] - a['position'][0], 10.0),
              (b['colour'], a['brightness'], b['brightness'], b['position'][0] - a['position'][0]))
    gone = lightscene.lights(LEVEL, guids, edits={static: {'Mobility': lighting.MOVABLE}})
    check('a light made Movable leaves the bake', len(gone) == len(before) - len(mine))
    gone = lightscene.lights(LEVEL, guids, removed=[static])
    check('a removed light leaves the bake', len(gone) == len(before) - len(mine))
    check('the level\'s lighting reads: 4 suns and every section',
          len(lighting.state(LEVEL)['suns']) == 4 and
          set(lighting.state(LEVEL)['sections']) == set(lighting.SECTIONS),
          sorted(lighting.state(LEVEL)['sections']))
    _ = lv


def run():
    migration()
    round_trip()
    build_writes()
    bake_inputs()
    bad = [c for c in checks if not c[1]]
    print('%d of %d checks pass' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(run())
