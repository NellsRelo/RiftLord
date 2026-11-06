"""Plans saved before 2026-11-05, brought to the lighting section (riftlord.build.world.lighting).

Until then a plan's lighting came in three ways, which this module still runs, only to
read what they wrote:

* a sky (`look.sky`) from another level brought that level's lighting: its fog colours,
  and every plain property of its DirectionalLight, SkyLight, AtmosphericFog,
  ExponentialHeightFog components and unbound post-process Settings (skymirror);
* `world` keys fogDensity, fogFalloff, fogOpacity, fogStart, fogColour, fogSunColour, bloom
  and exposure wrote the level's fog and post-process values where the level stored them;
* `lightColours` {actor key: '#rrggbb'} recoloured the level's own lights.

migrate(plan) runs the old writers over the level's packages in memory (the light rig for
a level from scratch), and stores every property they changed as an explicit `lighting`
section value (or `lights` value for a sun), so the plan builds the same lighting and the
Map Editor shows and edits those values. The old `world` keys and `lightColours` go.

    python3 -m riftlord.build.world.lightmigrate RECORD.json    # what migrate writes, checked
"""

import copy
import functools
import json
import struct
import sys

#: The old `world` keys: key -> (component class, property, kind, Settings struct or None).
LEGACY_FIELDS = {
    'fogDensity': ('ExponentialHeightFogComponent', 'FogDensity', 'float', None),
    'fogFalloff': ('ExponentialHeightFogComponent', 'FogHeightFalloff', 'float', None),
    'fogOpacity': ('ExponentialHeightFogComponent', 'FogMaxOpacity', 'float', None),
    'fogStart': ('ExponentialHeightFogComponent', 'StartDistance', 'float', None),
    'fogColour': ('ExponentialHeightFogComponent', 'FogInscatteringColor', 'colour', None),
    'fogSunColour': ('ExponentialHeightFogComponent', 'DirectionalInscatteringColor',
                     'colour', None),
    'bloom': ('PostProcessVolume', 'BloomIntensity', 'float', 'Settings'),
    'exposure': ('PostProcessVolume', 'AutoExposureBias', 'float', 'Settings'),
}
#: The old sky-filled world keys: the home's suns and sky light (used where skymirror had
#: nothing to copy).
SUNS, SKY_LIGHT, SKY_HOME = '_suns', '_skyLight', '_skyHome'
#: The plan `world` keys a sky's home filled, and the fog property each read from.
SKY_FOG_KEYS = {'fogColour': 'FogInscatteringColor',
                'fogSunColour': 'DirectionalInscatteringColor'}


# -- the old writers -------------------------------------------------------------------

def legacy_world(plan):
    """The plan's `world` as the old build saw it: the chosen sky's home fog colours,
    suns, sky light and home level filled in where the plan set none."""
    from riftlord.build.world import skyfog
    world = dict((plan or {}).get('world') or {})
    look = (plan or {}).get('look') or {}
    explicit = look.get('fog') if isinstance(look.get('fog'), dict) else {}
    home, colours = skyfog.fog_for(look.get('sky'))
    if look.get('sky') and look.get('skyHome'):
        home = look['skyHome']
        colours = _colours_of(home)
    for key, value in colours.items():
        if world.get(key) is None and explicit.get(key) is None:
            world[key] = list(value)
    if home:
        for key, value in lights_for(home).items():
            if world.get(key) is None:
                world[key] = value
        world[SKY_HOME] = home
    return world


def _colours_of(scenario):
    from riftlord.formats.omd3 import omd3sky
    fog = (((omd3sky.load().get(scenario) or {}).get('look') or {})
           .get('ExponentialHeightFogComponent') or {})
    return {key: fog[prop] for key, prop in SKY_FOG_KEYS.items() if prop in fog}


@functools.lru_cache(maxsize=32)
def lights_for(scenario):
    """{SUNS: [[r, g, b, intensity]] per DirectionalLight in export order, SKY_LIGHT:
    [r, g, b, intensity]} of a shipped level."""
    from riftlord.formats.omd3 import omd3sky
    from riftlord.mapeditor import leveldata
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
        out[SUNS] = [list(l['colour'][:3]) + [float(l['intensity'])] for l in suns]
    sky = ((omd3sky.load().get(scenario) or {}).get('look') or {}).get('SkyLightComponent') or {}
    if 'LightColor' in sky and 'Intensity' in sky:
        out[SKY_LIGHT] = list(sky['LightColor'][:3]) + [float(sky['Intensity'])]
    return out


def _walk(pkg, names, index):
    from riftlord.formats.omd3 import meshcat
    data = pkg.exports[index - 1]['data']
    return meshcat._walk(data, names, 0, len(data))


def _class(pkg, index):
    from riftlord.build.world import lighting
    return lighting._class_of(pkg, index)


def _slots(pkg, names):
    """{world key: [(export, value offset)]} for the LEGACY_FIELDS the package stores."""
    from riftlord.formats.omd3 import meshcat
    out = {}
    for i in range(1, len(pkg.exports) + 1):
        cls = _class(pkg, i)
        wanted = [(k, f) for k, f in LEGACY_FIELDS.items() if f[0] == cls]
        if not wanted:
            continue
        data = pkg.exports[i - 1]['data']
        top = _walk(pkg, names, i)
        if cls == 'PostProcessVolume' and 'bUnbound' not in top:
            continue
        for key, (_c, prop, _k, sub) in wanted:
            props = top
            if sub:
                if sub not in top:
                    continue
                _t, at, size = top[sub]
                props = meshcat._walk(data, names, at, at + size)
            if prop in props:
                out.setdefault(key, []).append((i, props[prop][1]))
    return out


def _light_slots(pkg, names, cls):
    out = []
    for i in range(1, len(pkg.exports) + 1):
        if _class(pkg, i) != cls:
            continue
        top = _walk(pkg, names, i)
        out.append((i, top['LightColor'][1] if 'LightColor' in top else None,
                    top['Intensity'][1] if 'Intensity' in top else None))
    return out


def _paint_light(data, colour_at, intensity_at, value):
    if colour_at is not None:
        r, g, b = (max(0, min(255, int(round(v * 255)))) for v in value[:3])
        data[colour_at:colour_at + 3] = bytes((b, g, r))
    if intensity_at is not None:
        struct.pack_into('<f', data, intensity_at, float(value[3]))


def _home_for_each(targets, home):
    if len(targets) == len(home):
        return list(home)
    bright = max(home, key=lambda h: h[3])
    rest = next((h for h in home if h is not bright), bright)
    top = max(range(len(targets)), key=lambda i: targets[i]) if targets else None
    return [bright if i == top else rest for i in range(len(targets))]


def legacy_apply(pkg, world):
    """The old build's scene writes on one uassetio Package, in place: the sky's home
    lighting (skymirror), the old world keys, and the home suns and sky light where
    skymirror had nothing to copy."""
    from riftlord.build.world import skymirror

    class _B:
        pass
    builder = _B()
    builder.pkg = pkg
    report = {'notes': [], 'skipped': []}
    mirrored = False
    if world.get(SKY_HOME):
        mirrored = skymirror.mirror(builder, world[SKY_HOME], report) > 0
    names = [n for n, _h in pkg.names]
    touched = {}

    def edit(index, fn):
        if index not in touched:
            touched[index] = bytearray(pkg.exports[index - 1]['data'])
        fn(touched[index])
    for key, rows in _slots(pkg, names).items():
        if world.get(key) is None:
            continue
        kind, value = LEGACY_FIELDS[key][2], world[key]
        for index, at in rows:
            if kind == 'colour':
                edit(index, lambda d, at=at, v=value: struct.pack_into(
                    '<4f', d, at, *[float(x) for x in v]))
            else:
                edit(index, lambda d, at=at, v=value: struct.pack_into('<f', d, at, float(v)))
    if world.get(SUNS) and not mirrored:
        rows = _light_slots(pkg, names, 'DirectionalLightComponent')
        own = [struct.unpack_from('<f', pkg.exports[i - 1]['data'], at)[0] if at is not None
               else 0.0 for i, _c, at in rows]
        for (index, c, t), value in zip(rows, _home_for_each(own, world[SUNS])):
            if c is not None or t is not None:
                edit(index, lambda d, c=c, t=t, v=value: _paint_light(d, c, t, v))
    if world.get(SKY_LIGHT) and not mirrored:
        for index, c, t in _light_slots(pkg, names, 'SkyLightComponent'):
            edit(index, lambda d, c=c, t=t: _paint_light(d, c, t, world[SKY_LIGHT]))
    for index, data in touched.items():
        pkg.exports[index - 1]['data'] = bytes(data)
        pkg.exports[index - 1]['size'] = len(data)


def _clean_legacy(world):
    """The old world keys of a plan, as the old build validated them."""
    out = {}
    for key in LEGACY_FIELDS:
        value = (world or {}).get(key)
        if value is None:
            continue
        if LEGACY_FIELDS[key][2] == 'colour':
            if isinstance(value, (list, tuple)) and len(value) == 4:
                out[key] = [float(v) for v in value]
        else:
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                continue
    return out


def legacy_inputs(plan):
    """What the old writers read of a plan, or None when they wrote nothing."""
    plan = plan or {}
    world = legacy_world(plan)
    legacy = _clean_legacy(world)
    keys = {k: world[k] for k in (SUNS, SKY_LIGHT, SKY_HOME) if world.get(k)}
    if not legacy and not keys:
        return None
    from riftlord.mapeditor import scratch
    if scratch.is_scratch(plan) and not ((plan.get('look') or {}).get('sky') or
                                         _clean_legacy(plan.get('world'))):
        return None
    keys.update(legacy)
    return keys


# -- reading what they wrote ---------------------------------------------------------

def decode(data, names, start=0, end=None):
    """{property: value} of every tag of a stream (nested structs as dicts), to compare."""
    from riftlord.build.world import lighting
    out = {}
    for name, typ, a, b in lighting.tags(data, names, start, end):
        idx = struct.unpack_from('<i', data, a + 20)[0]
        key = name if idx == 0 else '%s[%d]' % (name, idx)
        value = lighting.read_value(data, names, typ, a, b)
        if value is None and typ == 'StructProperty':
            value = decode(data, names, a + 49, b)
        elif value is None:
            value = ('raw', typ, bytes(data[a + 24:b]).hex())
        out[key] = value
    return out


def components(pkg):
    """{export: {property: value}} of a package's lighting components (a post-process
    volume: its unbound Settings), as decode reads them."""
    from riftlord.build.world import lighting
    names = [n for n, _h in pkg.names]
    found = lighting.survey(pkg)
    out = {}
    for section, exports in found['sections'].items():
        for i in exports:
            data = pkg.exports[i - 1]['data']
            if section == 'postProcess':
                a, b = lighting._settings_span(data, names)
                out[i] = decode(data, names, a + 49, b)
            else:
                out[i] = decode(data, names, 0, lighting.terminator(data, names))
    for comps in found['lights'].values():
        for i, _cls in comps:
            data = pkg.exports[i - 1]['data']
            out[i] = decode(data, names, 0, lighting.terminator(data, names))
    return out


def _copy(pkg):
    from riftlord.formats.ue.uassetio import Package
    return copy.deepcopy(pkg) if not hasattr(Package, 'clone') else pkg.clone()


def changed(before, after):
    """{property: new value or None (removed)} between two decoded components."""
    out = {}
    for prop in set(before) | set(after):
        if before.get(prop, Ellipsis) != after.get(prop, Ellipsis):
            out[prop] = after.get(prop)
    return out


@functools.lru_cache(maxsize=16)
def _legacy_values(level, scratch_plan, inputs_json):
    """(lighting, lights, lost) the old writers produced, by section and sun key; `lost`
    names changed properties no lighting value can carry."""
    from riftlord.build.world import lighting
    inputs = json.loads(inputs_json)
    plan = {'scratch': {'name': 'x'}} if scratch_plan else None
    sections, suns, lost = {}, {}, []
    for tag, pkg in lighting.level_packages(level, plan):
        found = lighting.survey(pkg)
        before = components(pkg)
        if not before:
            continue
        work = _copy(pkg)
        legacy_apply(work, inputs)
        after = components(work)
        section_of = {i: s for s, exports in found['sections'].items() for i in exports}
        actor_of = {i: a for a, comps in found['lights'].items() for i, c in comps
                    if c == lighting.SUN_CLASS}
        for i, old in before.items():
            diff = changed(old, after.get(i, {}))
            if not diff:
                continue
            for prop, value in sorted(diff.items()):
                if lighting.kind_of(prop) is None:
                    lost.append('%s export %d %s' % (tag, i, prop))
                    continue
                if i in section_of:
                    target = sections.setdefault(section_of[i], {})
                elif i in actor_of:
                    target = suns.setdefault('%s|%d' % (tag, actor_of[i]), {})
                else:
                    lost.append('%s export %d %s' % (tag, i, prop))
                    continue
                if prop in target and target[prop] != value:
                    lost.append('%s export %d %s: another component takes %r'
                                % (tag, i, prop, target[prop]))
                    continue
                target[prop] = value
            if section_of.get(i) == 'postProcess':
                #: a changed setting keeps the override the old build left on it
                target = sections['postProcess']
                for prop in list(target):
                    switch = lighting.OVERRIDE + prop
                    if not prop.startswith(lighting.OVERRIDE) and switch in lighting.PROPS \
                            and switch not in target:
                        target[switch] = after.get(i, {}).get(switch)
    return sections, suns, lost


def legacy_values(level, plan):
    """(lighting, lights, lost) for a plan's old sky and world settings ({}, {}, [] when
    they wrote nothing)."""
    from riftlord.mapeditor import scratch
    inputs = legacy_inputs(plan)
    if not inputs:
        return {}, {}, []
    got = _legacy_values(level, bool(scratch.is_scratch(plan)),
                         json.dumps(inputs, sort_keys=True))
    return copy.deepcopy(got)


def _sun_actors(level):
    """The actor keys of a level's DirectionalLights, which `lightColours` never coloured."""
    if not level:
        return set()
    from riftlord.mapeditor import leveldata
    try:
        lights = leveldata.level(level).get('lights') or []
    except Exception:                                # noqa: BLE001
        return set()
    return {'%s|%d' % (l.get('sub') or 'persistent', l['actor']) for l in lights
            if l.get('kind') == 'DirectionalLight' and l.get('actor')}


def migrate(plan):
    """A plan without `lighting` given the explicit values its sky, world settings and
    `lightColours` used to bring, in place. A plan with `lighting` is left as it is."""
    from riftlord.build.world import lighting
    from riftlord.build.world import levelretint
    if not isinstance(plan, dict) or lighting.PLAN_KEY in plan:
        return plan
    level = plan.get('level')
    lights = {}
    suns = _sun_actors(level)
    for key, value in levelretint.clean_colours(plan.get('lightColours')).items():
        if key not in suns:
            lights.setdefault(key, {})['LightColor'] = value
    sections, suns, lost = {}, {}, []
    if level:
        try:
            sections, suns, lost = legacy_values(level, plan)
        except (Exception, SystemExit) as exc:       # noqa: BLE001
            lost = ['the old lighting could not be read: %s: %s' % (type(exc).__name__, exc)]
    for key, props in suns.items():
        lights.setdefault(key, {}).update(props)
    plan.pop('lightColours', None)
    world = plan.get('world')
    if isinstance(world, dict):
        for key in LEGACY_FIELDS:
            world.pop(key, None)
    plan[lighting.PLAN_KEY] = sections
    if lights:
        plan[lighting.LIGHTS_KEY] = {**lights, **(plan.get(lighting.LIGHTS_KEY) or {})}
    if lost:
        plan.setdefault('lightingNotes', []).extend(lost)
    return plan


# -- the check -----------------------------------------------------------------------

def _legacy_recolour(pkg, index, colour):
    """levelretint.paint's write of one `lightColours` value: the colour bytes in place
    (the stored alpha kept), or a new opaque LightColor."""
    from riftlord.build.world import lighting
    data = bytearray(pkg.exports[index - 1]['data'])
    names = [n for n, _h in pkg.names]
    for name, _typ, a, b in lighting.tags(data, names):
        if name == 'LightColor' and b - a - 49 == 4:
            data[a + 49:a + 52] = lighting.colour_bytes(colour)[:3]
            pkg.exports[index - 1]['data'] = bytes(data)
            return
    lighting.write_export(pkg, index, {'LightColor': colour})


def check(level, old_plan):
    """[difference] between the lighting components the old writers produced for
    `old_plan` and what the migrated plan's values write ([] when they agree)."""
    from riftlord.build.world import lighting
    new_plan = migrate(copy.deepcopy(old_plan))
    inputs = legacy_inputs(old_plan) or {}
    out = []
    from riftlord.build.world import levelretint
    recolour = levelretint.clean_colours((old_plan or {}).get('lightColours'))
    for tag, pkg in lighting.level_packages(level, old_plan):
        old = _copy(pkg)
        if inputs:
            legacy_apply(old, inputs)
        for actor, comps in lighting.survey(old)['lights'].items():
            colour = recolour.get('%s|%d' % (tag, actor))
            for i, cls in comps:
                if colour and cls != lighting.SUN_CLASS:
                    _legacy_recolour(old, i, colour)
        new = _copy(pkg)
        for index, edit in lighting.edits_for(new, tag, new_plan).items():
            lighting.write_export(new, index, edit['props'], edit.get('settings'))
        a, b = components(old), components(new)
        for i in sorted(set(a) | set(b)):
            for prop, value in sorted(changed(a.get(i, {}), b.get(i, {})).items()):
                out.append('%s export %d %s: old %r, new %r'
                           % (tag, i, prop, a.get(i, {}).get(prop), value))
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print(__doc__)
        return 2
    with open(argv[0], encoding='utf-8') as fh:
        rec = json.load(fh)
    plan = rec.get('plan', rec)
    got = migrate(copy.deepcopy(plan))
    print(json.dumps({k: got.get(k) for k in ('lighting', 'lights', 'lightingNotes')
                      if got.get(k)}, indent=1))
    diffs = check(plan.get('level') or rec.get('level'), plan)
    print('check: %s' % ('the migrated values build the old lighting' if not diffs
                         else '%d difference(s)' % len(diffs)))
    for line in diffs:
        print('   ', line)
    return 1 if diffs else 0


if __name__ == '__main__':
    raise SystemExit(main())
