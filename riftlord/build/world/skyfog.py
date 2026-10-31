"""A sky's fog colours: the fog its home level pairs it with.

The skybox sits about 20M uu out, behind height fog whose `FogMaxOpacity` is 0.9 on
every shipped level, so most of the sky's colour on screen is the fog's. Choosing a
sky therefore also brings its home level's fog colours, unless the plan's world
settings name a fog colour of their own.

    world_with_sky(plan)   # plan['world'] plus the chosen sky's fog colours, suns, sky light
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
#: Pieces of the home level with a material named with one of these come with its sky.
SKY_EXTRA_WORDS = ('Moon',)


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


def world_with_sky(plan):
    """The plan's world settings with the chosen sky's fog colours filled in where the
    plan sets none, in `world` or in Map Settings' `look.fog` (same keys)."""
    world = dict((plan or {}).get('world') or {})
    look = (plan or {}).get('look') or {}
    explicit = look.get('fog') if isinstance(look.get('fog'), dict) else {}
    home, colours = fog_for(look.get('sky'))
    if look.get('sky') and look.get('skyHome'):
        home = look['skyHome']
        colours = _colours_of(home)
    for key, value in colours.items():
        if world.get(key) is None and explicit.get(key) is None:
            world[key] = list(value)
    #: the home level's suns and sky light come with its sky too: a night sky over Front
    #: Lawn's four daytime suns left half of Beset sunlit (2026-10-15)
    if home:
        lights = lights_for(home)
        for key, value in lights.items():
            if world.get(key) is None:
                world[key] = value
        #: and the rest of its lighting, property by property (riftlord.skymirror)
        from riftlord.build.world import levelfx
        world[levelfx.SKY_HOME] = home
    return world


def _colours_of(scenario):
    from riftlord.formats.omd3 import omd3sky
    fog = (((omd3sky.load().get(scenario) or {}).get('look') or {})
           .get('ExponentialHeightFogComponent') or {})
    return {key: fog[prop] for key, prop in SKY_FOG_KEYS.items() if prop in fog}


def extras(home):
    """[(package path, zero-based actor export, actor name)] of the home level's sky
    pieces (its moon) for a build to lift."""
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import leveldata
    out = []
    try:
        lv = leveldata.level(home)
    except Exception:                                # noqa: BLE001
        return out
    subs = lv.get('subPackages') or {}
    for p in lv.get('pieces') or []:
        if p.get('source') != 'component':
            continue
        if not any(w in str(p.get('material') or '') for w in SKY_EXTRA_WORDS):
            continue
        path = subs.get(p.get('sub')) or lv.get('level')
        try:
            info, _u, _h = omd3level._load(path)
            #: pieces carry the 1-based component export; its outer is the actor (1-based)
            outer = info['exports'][p['export'] - 1].get('outer')
            out.append((path, outer - 1, str(info['exports'][outer - 1].get('name'))))
        except Exception:                            # noqa: BLE001
            continue
    return out


def own_extras(level, plan):
    """{sublevel tag: [piece export]} of the level's own sky pieces (its moon) that a sky
    from another home replaces: parked by the build, as the home's are lifted in
    (`lift_extras`). {} when the plan keeps the level's sky or the sky's home is the
    level."""
    from riftlord.build.world import levelfx
    from riftlord.mapeditor import leveldata
    sky = ((plan or {}).get('look') or {}).get('sky')
    if not sky:
        return {}
    home = world_with_sky(plan).get(levelfx.SKY_HOME)
    if not home or home == level:
        return {}
    out = {}
    try:
        lv = leveldata.level(level)
    except Exception:                                # noqa: BLE001
        return out
    if sky == (lv.get('sky') or {}).get('material'):
        return out
    pieces = lv.get('pieces') or []
    for p in pieces:
        if p.get('source') != 'component':
            continue
        if not any(w in str(p.get('material') or '') for w in SKY_EXTRA_WORDS):
            continue
        rows = out.setdefault(p.get('sub') or 'persistent', [])
        if p['export'] not in rows:
            rows.append(p['export'])
    return out


@functools.lru_cache(maxsize=32)
def lights_for(scenario):
    """{levelfx.SUNS: [[r, g, b, intensity]] per DirectionalLight in export order,
    levelfx.SKY_LIGHT: [r, g, b, intensity]} of a shipped level."""
    from riftlord.formats.omd3 import omd3sky
    from riftlord.mapeditor import leveldata
    from riftlord.build.world import levelfx
    out = {}
    try:
        lv = leveldata.level(scenario)
    except Exception:                                # noqa: BLE001
        return out
    lights = lv.get('lights') or []
    lights = lights if isinstance(lights, list) else list(lights.values())
    suns = sorted((l for l in lights if l.get('kind') == 'DirectionalLight'),
                  key=lambda l: l.get('export') or 0)
    if suns:
        out[levelfx.SUNS] = [list(l['colour'][:3]) + [float(l['intensity'])] for l in suns]
    sky = ((omd3sky.load().get(scenario) or {}).get('look') or {}).get('SkyLightComponent') or {}
    if 'LightColor' in sky and 'Intensity' in sky:
        out[levelfx.SKY_LIGHT] = list(sky['LightColor'][:3]) + [float(sky['Intensity'])]
    return out


def lift_extras(builder, level, plan, report):
    """Lift the sky's home pieces (its moon) into the level being built, where they
    stand in the home level. Returns the count."""
    from riftlord.core import gamefiles
    import struct
    from riftlord.formats.ue.uassetio import Package
    from riftlord.mapeditor import fabemit
    from riftlord.mapeditor import geomedits
    from riftlord.build.world import levelfx
    home = world_with_sky(plan).get(levelfx.SKY_HOME)
    if not home or home == level:
        return 0
    done = 0
    for path, index, name in extras(home):
        try:
            pkg = Package(*gamefiles.package(path))
            pkg.load_payloads()
            at = builder.lift_actor(pkg, index, rename='RiftLordSky_%s' % name)
        except Exception as exc:                     # noqa: BLE001
            report['skipped'].append('%s from %s could not be lifted: %s' % (name, home, exc))
            continue
        #: Movable: a lifted mesh keeps its donor's MapBuildDataId (see lavafx.apply)
        root = builder._root_component(at)
        if root:
            builder.set_property(root, 'Mobility', 'ByteProperty',
                                 struct.pack('<ii', builder.pkg.add_name(
                                     fabemit.PIECE_MOBILITY), 0),
                                 struct_name='EComponentMobility')
        geomedits.forget_visibility(builder, at)
        done += 1
    if done:
        report.setdefault('notes', []).append('%d sky piece(s) from %s' % (done, home))
    return done
