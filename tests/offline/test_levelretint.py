"""Offline checks on the level's own lights recoloured and effects swapped (plan `lights`
LightColor, riftlord/build/world/lighting.py, from an older plan's `lightColours`;
`fxSwaps`: riftlord/build/world/levelretint.py), on The Cellar (Scenario_16).

    python3 test_levelretint.py

- The plan's fields are checked: an old plan's colours '#rrggbb' on actor keys, swaps to a
  surveyed Emitter system only (a Niagara one or an unknown one is refused); save_plan turns
  `lightColours` into `lights` and keeps `fxSwaps`.
- A light colour reaches every light component of the actor; an old `lightColours` entry on
  a DirectionalLight is dropped; `fxSwaps` reaches the Emitter's ParticleSystemComponent; a
  swap back to the shipped system writes nothing.
- apply.sublevel_edits hands them to the sublevel that holds them; the written Lighting
  copy stores the new LightColor (bytes BGRA) on a light that states one and on a torch
  that leaves it to its class, and leaves other lights alone; the written Environment copy's
  emitter names the new system (a ParticleSystem import under its package) in Template and
  in its preload list in place of the old one, and pkgcheck passes both.
- A recolour makes the build bake (lightbake.needs), and the bake spec carries the plan's
  colour by light actor (the baker puts it over the liquid lights' colour).

Reads the game's packages only. Exit 0 when every check passes.
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from riftlord.build import apply  # noqa: E402
from riftlord.build import levelbuild  # noqa: E402
from riftlord.build.lighting import lightbake  # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.build.world import levelretint  # noqa: E402
from riftlord.build.world import lighting  # noqa: E402
from riftlord.build.world import liquidlights  # noqa: E402
from riftlord.core import gamefiles  # noqa: E402
from riftlord.core import pkgcheck  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402
from riftlord.missions import records  # noqa: E402

LEVEL = 'Scenario_16'
#: The green the checks write, and a system to swap to (a surveyed Emitter one).
GREEN = '#00890c'
TEMPLATE = 'P_Acid_Wide_Idle'
#: A Niagara system a level places (fx-donors.json), which an Emitter cannot play.
NIAGARA = 'NS_Snow_Down'

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def key(row):
    return '%s|%d' % (row.get('sub') or 'persistent', row['actor'])


def written(ua, ux, name):
    """A written package re-read as a Builder, and pkgcheck's problems with it."""
    return levelbuild.Builder.around(ua, ux, name=name), pkgcheck.problems(ua, ux, name)


def owner_class(builder, export):
    """The class name of the actor that owns component `export`."""
    pkg = builder.pkg
    outer = struct.unpack_from('<i', pkg.exports[export - 1]['meta'], levelbuild.OUTER_AT)[0]
    cls = struct.unpack_from('<i', pkg.exports[outer - 1]['meta'], levelbuild.CLASS_AT)[0]
    return levelbuild.resolve(pkg, cls)[1]


def colour_of(builder, export):
    """(r, g, b) of a component's stored LightColor, or None."""
    data = builder.pkg.exports[export - 1]['data']
    span = builder._tag_span(data, 'LightColor')
    if not span:
        return None
    b, g, r, _a = struct.unpack_from('<4B', data, span[2])
    return (r, g, b)


def run():
    lv = leveldata.level(LEVEL)
    plain = [l for l in lv['lights'] if l['sub'] == 'Lighting' and l['kind'] == 'PointLight'
             and l.get('actor') and abs(l['colour'][2] - 1.0) < 1e-3 and l['colour'][0] < 0.2]
    rect = next(l for l in lv['lights'] if l['sub'] == 'Lighting' and l['kind'] == 'RectLight')
    sun = next(l for l in lv['lights'] if l['kind'] == 'DirectionalLight')
    pool = next(f for f in lv['vfx'] if f['template'] == 'FX_ArcanePool')
    other = next(f for f in lv['vfx'] if f['template'] == 'FX_ArcanePool' and f is not pool)
    light, untouched = plain[0], plain[1]

    # -- the plan's fields --------------------------------------------------------------
    got = levelretint.clean_colours({key(light): '#00890C', 'Lighting|0': GREEN,
                                     'nobar': GREEN, key(rect): 'green', key(sun): '#123'})
    check('colours: a valid key and hex kept (lower case), the rest dropped',
          got == {key(light): GREEN}, got)
    check('swaps: an Emitter system kept', levelretint.clean_swaps({key(pool): TEMPLATE}) ==
          {key(pool): TEMPLATE})
    for bad in (NIAGARA, 'P_NoSuchSystem'):
        try:
            levelretint.clean_swaps({key(pool): bad})
            refused = False
        except ValueError:
            refused = True
        check('swaps: %s refused' % bad, refused)
    rec = {'level': LEVEL, 'plan': {'level': LEVEL, 'lightColours': {key(light): GREEN},
                                    'fxSwaps': {key(pool): TEMPLATE}}}
    _warnings, cleaned = records.validate_plan(rec)
    check('save_plan turns lightColours into lights and keeps fxSwaps',
          cleaned.get('lights') == {key(light): {'LightColor': GREEN}}
          and 'lightColours' not in cleaned
          and cleaned.get('fxSwaps') == {key(pool): TEMPLATE},
          {k: cleaned.get(k) for k in ('lights', 'lightColours', 'fxSwaps')})

    # -- what they reach ----------------------------------------------------------------
    base = navpatch.map_path(LEVEL)
    ua, ux = gamefiles.package(base + '_Lighting')
    shipped = levelbuild.Builder.around(ua, ux, name='shipped')
    torch = next(l for l in lv['lights'] if l['sub'] == 'Lighting' and l['kind'] == 'PointLight'
                 and l.get('actor') and 'WallTorch' in owner_class(shipped, l['export']))
    plan = {'lightColours': {key(light): GREEN, key(rect): GREEN, key(sun): GREEN,
                             key(torch): GREEN},
            'fxSwaps': {key(pool): TEMPLATE, key(other): other['template']}}
    migrated = lighting.migrated(dict(plan, level=LEVEL))
    lights = {t: {i: e['props'] for i, e in rows.items()}
              for t, rows in lighting.package_edits(LEVEL, migrated).items()}
    torch_parts = {l['export'] for l in lv['lights']
                   if l['sub'] == 'Lighting' and l.get('actor') == torch['actor']}
    want = {light['export'], rect['export']} | torch_parts
    check('lights: the point and rect lights\' components and every one of the '
          'torch\'s (%d), not the sun' % len(torch_parts),
          set(lights.get('Lighting') or {}) == want, lights)
    check('lights: the colour as the level stores it',
          (lights.get('Lighting') or {}).get(light['export']) == {'LightColor': GREEN})
    swaps = levelretint.swap_edits(LEVEL, plan)
    check('swap_edits: the emitter\'s component, not one swapped to its own system',
          swaps == {'Environment': {pool['export']: TEMPLATE}}, swaps)
    edits = apply.sublevel_edits(LEVEL, plan)
    check('sublevel_edits hands the colours to Lighting',
          set((edits.get('Lighting') or {}).get('lighting') or {}) == want)
    check('sublevel_edits hands the swap to Environment',
          (edits.get('Environment') or {}).get('fxSwaps') == {pool['export']: TEMPLATE})

    # -- the written packages -----------------------------------------------------------
    before_torch = colour_of(shipped, torch['export'])
    out_a, out_x, report = apply.apply_to_sublevel(ua, ux, 'RetintTest_Map01_Lighting',
                                                   edits['Lighting'])
    back, problems = written(out_a, out_x, 'RetintTest_Map01_Lighting')
    rgb = tuple(int(GREEN[i:i + 2], 16) for i in (1, 3, 5))
    check('a light that states a colour stores the new one',
          colour_of(back, light['export']) == rgb, colour_of(back, light['export']))
    check('the rect light stores the new one', colour_of(back, rect['export']) == rgb)
    check('a torch that leaves its colour to its class gets one',
          before_torch is None and colour_of(back, torch['export']) == rgb,
          (before_torch, colour_of(back, torch['export'])))
    check('a light left alone keeps its colour',
          colour_of(back, untouched['export']) == colour_of(shipped, untouched['export']))
    check('Lighting copy passes pkgcheck', not problems, problems[:3])
    check('the report counts them', 'lighting: %d component(s) set' % len(want) in report['notes'],
          report['notes'])

    ua, ux = gamefiles.package(base + '_Environment')
    out_a, out_x, report = apply.apply_to_sublevel(ua, ux, 'RetintTest_Map01_Environment',
                                                   edits['Environment'])
    back, problems = written(out_a, out_x, 'RetintTest_Map01_Environment')
    data = back.pkg.exports[pool['export'] - 1]['data']
    ref = struct.unpack_from('<i', data, back._tag_span(data, 'Template')[2])[0]
    rec = back.pkg.imports[-ref - 1]
    names = back.pkg.names
    cls = names[struct.unpack_from('<I', rec, 8)[0]][0]
    outer = struct.unpack_from('<i', rec, 16)[0]
    owner = names[struct.unpack_from('<I', back.pkg.imports[-outer - 1], 20)[0]][0]
    check('the emitter plays the new system', levelbuild._import_name(back.pkg, -ref - 1)
          == TEMPLATE and cls == 'ParticleSystem', (levelbuild._import_name(back.pkg, -ref - 1), cls))
    check('its import sits under the system\'s package',
          owner == levelretint.template_path(TEMPLATE), owner)
    meta = back.pkg.exports[pool['export'] - 1]['meta']
    first = struct.unpack_from('<i', meta, 84)[0]
    count = sum(struct.unpack_from('<4i', meta, 88))
    listed = [levelbuild._import_name(back.pkg, -p - 1) for p in back.pkg.preload[first:first + count]
              if p < 0]
    check('its preload list names the new system, not the old',
          TEMPLATE in listed and 'FX_ArcanePool' not in listed, listed)
    data = back.pkg.exports[other['export'] - 1]['data']
    ref = struct.unpack_from('<i', data, back._tag_span(data, 'Template')[2])[0]
    check('another emitter keeps its system',
          levelbuild._import_name(back.pkg, -ref - 1) == 'FX_ArcanePool')
    check('Environment copy passes pkgcheck', not problems, problems[:3])

    # -- the bake -----------------------------------------------------------------------
    check('a recolour makes the build bake', lightbake.needs({'lightColours': {key(light): GREEN}}))
    check('no recolour, no bake', not lightbake.needs({'lightColours': {}}))
    pools = liquidlights.edits(LEVEL, {'look': {'liquid': 'Acid_Surface_Inst'}})
    over = next(iter(pools.get('Lighting') or {}), None)
    owner = next(l for l in lv['lights'] if l['sub'] == 'Lighting' and l['export'] == over)
    spec = lightbake.bake_spec(LEVEL, {'look': {'liquid': 'Acid_Surface_Inst'},
                                       'lightColours': {key(light): GREEN, key(owner): GREEN}})
    edits = spec['light_edits']
    check('the bake spec carries the plan\'s colour by light actor',
          edits.get(key(light)) == {'LightColor': GREEN}, edits.get(key(light)))
    check('beside the liquid light\'s colour for the same light, which the baker puts under it',
          edits.get(key(owner)) == {'LightColor': GREEN}
          and 'Lighting|%d' % over in spec['light_colours'],
          (pools['Lighting'][over], edits.get(key(owner))))

    bad = [c for c in checks if not c[1]]
    print('%d of %d checks pass' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(run())
