"""Catalogue the skies a converted OMD3 level can be given.

    python3 omd3sky.py --build
    python3 omd3sky.py --show Scenario_05

A sky is not one asset. It is a **set** of actors that only look right together: a
`BP_Sky_Sphere_C` carrying the sky material, a `SkyLight` for the ambient bounce, a
`DirectionalLight` for the sun at a particular angle, and a fog actor tinting the
distance. Lift a sky sphere on its own into a level lit for a dungeon and the
result is a bright cutout over black geometry.

So this records whole sets, one per shipped level that has one, and the converter
lifts the set. The levels themselves are the presets -- OMD3's skies were authored
per scenario and there is no library of them to draw on, which is also why the
editor offers "the sky from Cliffside Cove" rather than a colour picker.

Sky actors live in a level's `_Lighting` sublevel rather than its persistent level,
so this scans sublevels too and files each set under the scenario it belongs to.
"""

import argparse
import collections
import json
import os
import struct
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamefiles  # noqa: E402
from riftlord.build import hostpatch  # noqa: E402
from riftlord.build import levelbuild  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

CATALOGUE = gamepath.data('omd3-skies.json')

#: The actors that together make a sky. Each is optional -- an indoor scenario has
#: a SkyLight and fog and no sphere at all, which is itself a usable preset.
SKY_CLASSES = ('BP_Sky_Sphere_C', 'SkyLight', 'AtmosphericFog',
               'ExponentialHeightFog', 'SkyAtmosphere', 'DirectionalLight',
               'BP_Sky_Sphere_Snow_C', 'BP_Sky_Sphere_Desert_C')

#: A sky with no sphere and no fog is not a sky, just a stray light.
NEEDED = ('BP_Sky_Sphere_C', 'AtmosphericFog', 'ExponentialHeightFog',
          'SkyAtmosphere', 'BP_Sky_Sphere_Snow_C', 'BP_Sky_Sphere_Desert_C')


def scenario_of(map_path):
    """The scenario a level or sublevel belongs to.

    `.../Scenario_05/Scenario_05_Lighting` and `.../Scenario_05/Scenario_05` are
    the same place, and the sky of the one is the sky of the other.
    """
    base = map_path.rsplit('/', 1)[-1]
    for tail in ('_Lighting', '_Environment', '_Castle', '_Foliage', '_Audio',
                 '_Gameplay', '_VFX', '_Terrain'):
        if base.endswith(tail):
            return base[:-len(tail)]
    return base


#: The components carrying the numbers that make a sky look like itself, and the
#: properties worth keeping from each. A cooked package serialises only what was
#: changed from the class default, so a missing property means "the default".
LOOK = {
    'DirectionalLightComponent': ('RelativeRotation', 'LightColor', 'Intensity'),
    'SkyLightComponent': ('LightColor', 'Intensity', 'LowerHemisphereColor',
                          'IndirectLightingIntensity'),
    'ExponentialHeightFogComponent': ('FogInscatteringColor', 'FogDensity',
                                      'DirectionalInscatteringColor',
                                      'FogHeightFalloff', 'FogMaxOpacity',
                                      'StartDistance'),
}


def _value(uexp, kind, at, size):
    """One property as a number, a colour or a vector; None if not one of those.

    A colour is four bytes and an FLinearColor is four floats, and both are a
    StructProperty, so the size decides which. Returned 0..1 either way so the
    caller never has to ask which one it got.
    """
    if kind == 'FloatProperty' and size == 4:
        return round(struct.unpack_from('<f', uexp, at)[0], 4)
    if kind == 'IntProperty' and size == 4:
        return struct.unpack_from('<i', uexp, at)[0]
    if kind == 'StructProperty' and size == 4:
        b, g, r, a = struct.unpack_from('<4B', uexp, at)
        return [round(r / 255.0, 4), round(g / 255.0, 4),
                round(b / 255.0, 4), round(a / 255.0, 4)]
    if kind == 'StructProperty' and size in (12, 16):
        n = size // 4
        return [round(v, 4) for v in struct.unpack_from('<%df' % n, uexp, at)]
    return None


def read_look(levels):
    """The sun, sky and fog numbers from a scenario's lighting sublevels.

    These are read rather than merely pointed at because the editor has to SHOW a
    sky before anyone commits to it. A name like `ScenarioDLC_04` says nothing;
    a warm gold sky light over orange fog at a ten-degree sun says sunset.
    """
    look = {}
    for lvl in levels:
        try:
            ua, ux = gamefiles.package(lvl)
            pkg = Package(ua, ux)
            pkg.load_payloads()
        except Exception:                            # noqa: BLE001
            continue
        names = [n for n, _h in pkg.names]
        for e in pkg.exports:
            cls = levelbuild.resolve(
                pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] or ''
            wanted = LOOK.get(cls)
            if not wanted or cls in look:
                continue
            base = struct.unpack_from('<q', e['meta'], 36)[0] - len(ua)
            if base < 0 or base >= len(ux):
                continue
            got = {}
            for name, kind, at, size in hostpatch._properties(
                    ux, names, base, len(e['data'])):
                if name in wanted:
                    val = _value(ux, kind, at, size)
                    if val is not None:
                        got[name] = val
            if got:
                look[cls] = got
    return look


def mission_names():
    """{scenario: display name} from MissionProtos, for labelling the presets.

    A TextProperty keeps its namespace, key and source string one after another,
    and the source string is the last of the three.
    """
    import re
    try:
        from riftlord.build import missionproto
        table = missionproto.read()
    except Exception:                                # noqa: BLE001
        return {}
    out = {}
    for row in table.rows:
        try:
            field = table.field(row, 'MissionName')
        except Exception:                            # noqa: BLE001
            continue
        if field is None or field.kind != 'TextProperty':
            continue
        strings = re.findall(rb'[\x20-\x7e]{4,}', bytes(field.payload))
        if strings:
            out[row['name']] = strings[-1].decode('ascii', 'replace')
    return out


def title_for(scenario, titles):
    """A scenario's mission name, allowing for how the two sides spell it.

    The level lives in a folder called `ScenarioDLC_01` and MissionProtos calls the
    same mission `DLCScenario_01`, and a `_WS` suffix on the level does not appear
    on the row. An Intro or Endgame level has no mission at all, and returns ''.
    """
    for key in (scenario, scenario + '_WS',
                scenario.replace('ScenarioDLC_', 'DLCScenario_'),
                scenario.replace('ScenarioDLC_', 'DLCScenario_') + '_WS'):
        if key in titles:
            return titles[key]
    return ''


def build(out_path=CATALOGUE, limit=None):
    """Find every sky actor in every shipped level, grouped by scenario."""
    maps = [m for m in sorted(gamefiles.find(ext='.umap'))
            if '/Maps/' in m and 'ThirdPerson' not in m and '/Maps/UI/' not in m]
    if limit:
        maps = maps[:limit]
    skies = collections.defaultdict(lambda: {'parts': {}, 'levels': []})
    for m in maps:
        try:
            ua, ux = gamefiles.package(m[:-5])
            pkg = Package(ua, ux)
            pkg.load_payloads()
        except Exception:                            # noqa: BLE001
            continue
        hits = {}
        for i, e in enumerate(pkg.exports):
            cls = levelbuild.resolve(
                pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1]
            if cls in SKY_CLASSES and cls not in hits:
                hits[cls] = i
        if not hits:
            continue
        name = scenario_of(m[:-5])
        sky = skies[name]
        sky['levels'].append(m[:-5])
        for cls, i in hits.items():
            # First sublevel to offer a part wins; they are interchangeable.
            sky['parts'].setdefault(cls, {'level': m[:-5], 'export': i})
    titles = mission_names()
    #: by each row's MissionLevel first: row names and levels disagree on a few
    #: (Scenario_12 is Dragon Boneyard, Scenario_15 Mage Tower; by row name the two swap)
    try:
        from riftlord.build.minimap import mapdata
        by_level = mapdata.mission_names()
    except Exception:                                # noqa: BLE001
        by_level = {}
    out = {}
    for name, sky in skies.items():
        if not any(c in sky['parts'] for c in NEEDED):
            continue
        levels = sorted(sky['levels'])
        title = by_level.get(name) or title_for(name, titles)
        out[name] = {'name': name,
                     'title': title,
                     'parts': sky['parts'],
                     'levels': levels,
                     'look': read_look(levels)}
    with open(out_path, 'w') as fh:
        json.dump({'skies': out}, fh, indent=1, sort_keys=True)
    return out


def load(path=CATALOGUE):
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return json.load(fh)['skies']


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--show', metavar='SCENARIO')
    ap.add_argument('--limit', type=int)
    args = ap.parse_args(argv)

    if args.build:
        out = build(limit=args.limit)
        print('%d sky set(s)' % len(out))
        kinds = collections.Counter()
        for sky in out.values():
            kinds[tuple(sorted(sky['parts']))] += 1
        for parts, n in kinds.most_common():
            print('  x%-3d %s' % (n, ', '.join(parts)))
        return 0

    skies = load()
    if args.show:
        sky = skies.get(args.show)
        if not sky:
            print('no sky for %s; have %s' % (args.show, ', '.join(sorted(skies))))
            return 1
        print('%s %s' % (sky['name'], sky.get('title') and
                         '(%s)' % sky['title'] or ''))
        for cls, where in sorted(sky['parts'].items()):
            print('  %-26s %s #%d'
                  % (cls, where['level'].rsplit('/', 1)[-1], where['export']))
        for cls, props in sorted(sky.get('look', {}).items()):
            print('  %s' % cls)
            for k, v in sorted(props.items()):
                print('      %-30s %s' % (k, v))
        return 0

    for name in sorted(skies):
        sky = skies[name]
        print('%-24s %-18s %s' % (name, sky.get('title', ''),
                                  ', '.join(sorted(sky['parts']))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
