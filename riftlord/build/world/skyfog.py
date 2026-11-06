"""A sky material's home level: the shipped level it comes from.

Choosing a sky (`look.sky`) paints the sky dome's material and nothing else: the map's
fog, suns and sky light are its Lighting section's (riftlord.build.world.lighting) and its
moons are its own (riftlord.build.world.moons). Until 2026-11-05 a sky brought its home's
lighting and moon; riftlord.build.world.lightmigrate and moons.migrate turn that into
explicit values on older plans, reading the home from here.

    sky_home(plan)    # the chosen sky's home level, or None
"""

import functools

#: The plan `world` keys the sky's colours fill, and the fog property each reads from.
SKY_FOG_KEYS = {'fogColour': 'FogInscatteringColor',
                'fogSunColour': 'DirectionalInscatteringColor'}

#: Scenario names containing these are cutscenes, never a sky's home level.
NOT_HOME = ('Intro', 'EndGame')

#: A sky's home where several levels share it and the first by name is not the one its
#: look comes from. Night_Sky_Lava_INST1 is on The Lava Pits, Dragon Boneyard and Quick
#: Path; Dragon Boneyard is the night with the moon (user, Bake B 2026-10-16). A plan's
#: `look.skyHome` names a home of its own.
SKY_HOMES = {'Night_Sky_Lava_INST1': 'Scenario_12'}


@functools.lru_cache(maxsize=1)
def homes():
    """{sky material: (scenario, {plan key: RGBA})} for every shipped sky material."""
    from riftlord.core import gamefiles
    from riftlord.formats.omd3 import omd3sky
    from riftlord.formats.ue.uassetio import Package
    from riftlord.mapeditor import leveldata
    known = set(leveldata.looks().get('sky') or [])
    out, seen = {}, {}
    for name, sky in sorted(omd3sky.load().items()):
        if any(word in name for word in NOT_HOME):
            continue
        fog = (sky.get('look') or {}).get('ExponentialHeightFogComponent') or {}
        colours = {key: fog[prop] for key, prop in SKY_FOG_KEYS.items() if prop in fog}
        if not colours:
            continue
        for path in sky.get('levels') or []:
            try:
                pkg = Package(*gamefiles.package(path))
            except Exception:                        # noqa: BLE001
                continue
            for imp_name in _import_names(pkg):
                if imp_name in known:
                    seen.setdefault(imp_name, {})[name] = colours
    for material, levels in seen.items():
        home = SKY_HOMES.get(material)
        if home not in levels:
            home = min(levels, key=lambda name: _rank(material, name))
        out[material] = (home, levels[home])
    return out


def _rank(material, scenario):
    """Sort key for a sky's home: the scenario its name cites (`Scen9`, `Scen15`), then
    campaign scenarios before DLC ones, then by name."""
    import re
    cited = re.search(r'Scen(\d+)', material)
    number = re.search(r'^Scenario_(\d+)$', scenario)
    named = bool(cited and number and int(cited.group(1)) == int(number.group(1)))
    return (not named, not scenario.startswith('Scenario_'), scenario)


def _import_names(pkg):
    from riftlord.build import levelbuild
    for k in range(len(pkg.imports)):
        try:
            yield levelbuild._import_name(pkg, k)
        except Exception:                            # noqa: BLE001
            continue


def fog_for(material):
    """(home scenario, {plan key: RGBA}) for one sky material, or (None, {})."""
    if not material:
        return None, {}
    return homes().get(material, (None, {}))


def sky_home(plan):
    """The home level of the plan's sky (`look.skyHome`, else the sky's), or None."""
    look = (plan or {}).get('look') or {}
    if not look.get('sky'):
        return None
    return look.get('skyHome') or fog_for(look['sky'])[0]

