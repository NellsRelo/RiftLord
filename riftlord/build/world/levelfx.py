"""A level's scene settings: its fog, its post-process, and the intro's colour filter.

The fog (ExponentialHeightFogComponent) and the unbound PostProcessVolume live in the
level's `_Lighting` sublevel, or in the persistent level on a few. Both are drawn every
frame, so a value written into them shows in game without a lighting rebuild. Only
properties the level already stores are written, in place.

The intro filter: Scenario_06 (The Lava Pits) and Scenario_09 (Front Lawn) carry
`Sepia_PostProcess_Inst` as a weighted blendable on a PostProcessVolume. Two
LevelSequences per level, played by the level's script during the intro, animate the
blendable's weight up and back to 0. Removing the filter nulls the blendable's
material, so the sequences animate the weight of nothing.

Plan keys live in `plan['world']`: `introFilter` ('remove') and the SCENE_FIELDS keys.
"""

import functools
import math
import struct

#: Post-process materials that make a level's intro filter.
FILTER_MATERIALS = ('Sepia_PostProcess_Inst',)

#: The plan's world setting for the filter, and its values.
PLAN_KEY = 'introFilter'
KEEP, REMOVE = 'keep', 'remove'

#: Scene settings a plan may change: key -> where the value lives, its kind, and the
#: range the editor offers (min, max, step), which spans every shipped level's value.
SCENE_FIELDS = {
    'fogDensity': {'cls': 'ExponentialHeightFogComponent', 'prop': 'FogDensity',
                   'kind': 'float', 'group': 'Fog', 'label': 'Density',
                   'range': (0.0, 1.0, 0.01)},
    'fogFalloff': {'cls': 'ExponentialHeightFogComponent', 'prop': 'FogHeightFalloff',
                   'kind': 'float', 'group': 'Fog', 'label': 'Height Falloff',
                   'range': (0.001, 1.0, 0.005)},
    'fogOpacity': {'cls': 'ExponentialHeightFogComponent', 'prop': 'FogMaxOpacity',
                   'kind': 'float', 'group': 'Fog', 'label': 'Max Opacity',
                   'range': (0.0, 1.0, 0.01)},
    'fogStart': {'cls': 'ExponentialHeightFogComponent', 'prop': 'StartDistance',
                 'kind': 'float', 'group': 'Fog', 'label': 'Start Distance',
                 'range': (0.0, 30000.0, 100.0)},
    'fogColour': {'cls': 'ExponentialHeightFogComponent', 'prop': 'FogInscatteringColor',
                  'kind': 'colour', 'group': 'Fog', 'label': 'Colour'},
    'fogSunColour': {'cls': 'ExponentialHeightFogComponent',
                     'prop': 'DirectionalInscatteringColor', 'kind': 'colour',
                     'group': 'Fog', 'label': 'Sun Colour'},
    'bloom': {'cls': 'PostProcessVolume', 'struct': 'Settings', 'prop': 'BloomIntensity',
              'kind': 'float', 'group': 'Post Process', 'label': 'Bloom',
              'range': (0.0, 8.0, 0.1)},
    'exposure': {'cls': 'PostProcessVolume', 'struct': 'Settings',
                 'prop': 'AutoExposureBias', 'kind': 'float', 'group': 'Post Process',
                 'label': 'Exposure', 'range': (-3.0, 3.0, 0.05)},
}
#: wind (riftlord.controllers); a field with a `default` is offered wherever its class is
#: stored, and written in when the level leaves it at that default
from riftlord.mapeditor.controllers import WIND_FIELDS  # noqa: E402
SCENE_FIELDS.update(WIND_FIELDS)


def _class_of(pkg, export):
    from riftlord.build import levelbuild
    return levelbuild.resolve(pkg, struct.unpack_from('<i', export['meta'], 0)[0])[1]


def _blendables(pkg, names):
    """[(export index, Object offset in its data, material name)] for every weighted
    blendable on the package's PostProcessVolumes."""
    from riftlord.build import levelbuild
    from riftlord.formats.omd3 import meshcat
    from riftlord.formats.omd3 import meshhull
    out = []
    for i, ex in enumerate(pkg.exports):
        if _class_of(pkg, ex) != 'PostProcessVolume':
            continue
        data = ex['data']
        top = meshcat._walk(data, names, 0, len(data))
        if 'Settings' not in top:
            continue
        _k, at, size = top['Settings']
        settings = meshcat._walk(data, names, at, at + size)
        if 'WeightedBlendables' not in settings:
            continue
        _k, at, size = settings['WeightedBlendables']
        wb = meshcat._walk(data, names, at, at + size)
        if 'Array' not in wb:
            continue
        _k, ia, asize = wb['Array']
        count = struct.unpack_from('<i', data, ia)[0]
        o = ia + 4 + meshhull.INNER_TAG
        for _ in range(max(count, 0)):
            el, o = meshhull._walk_to_end(data, names, o, ia + asize)
            if 'Object' not in el:
                continue
            at_obj = el['Object'][1]
            ref = struct.unpack_from('<i', data, at_obj)[0]
            name = levelbuild.resolve(pkg, ref)[1] if ref else None
            out.append((i + 1, at_obj, name))
    return out


def _scene_slots(pkg, names):
    """{key: [(export index, value offset)]} for the SCENE_FIELDS this package stores."""
    from riftlord.formats.omd3 import meshcat
    out = {}
    for i, ex in enumerate(pkg.exports):
        cls = _class_of(pkg, ex)
        wanted = [(k, f) for k, f in SCENE_FIELDS.items() if f['cls'] == cls]
        if not wanted:
            continue
        data = ex['data']
        top = meshcat._walk(data, names, 0, len(data))
        if cls == 'PostProcessVolume' and 'bUnbound' not in top:
            continue
        for key, f in wanted:
            props = top
            if f.get('struct'):
                if f['struct'] not in top:
                    continue
                _k, at, size = top[f['struct']]
                props = meshcat._walk(data, names, at, at + size)
            if f['prop'] in props:
                out.setdefault(key, []).append((i + 1, props[f['prop']][1]))
            elif 'default' in f and not f.get('struct'):
                #: not stored: the class default, written in when changed
                out.setdefault(key, []).append((i + 1, None))
    return out


#: The chosen sky's home lights (skyfog.world_with_sky): SUNS is [[r, g, b, intensity]] per
#: DirectionalLightComponent in export order, SKY_LIGHT is [r, g, b, intensity].
SUNS, SKY_LIGHT = '_suns', '_skyLight'
#: The chosen sky's home level, whose lighting components are copied (riftlord.skymirror).
SKY_HOME = '_skyHome'


def _lights(pkg, names, cls):
    """[(export index, LightColor offset or None, Intensity offset or None)] for `cls`."""
    from riftlord.formats.omd3 import meshcat
    out = []
    for i, ex in enumerate(pkg.exports):
        if _class_of(pkg, ex) != cls:
            continue
        top = meshcat._walk(ex['data'], names, 0, len(ex['data']))
        out.append((i + 1, top['LightColor'][1] if 'LightColor' in top else None,
                    top['Intensity'][1] if 'Intensity' in top else None))
    return out


def _paint_light(data, colour_at, intensity_at, value):
    """LightColor (an FColor, BGRA) and Intensity from [r, g, b, intensity]."""
    if colour_at is not None:
        r, g, b = (max(0, min(255, int(round(v * 255)))) for v in value[:3])
        data[colour_at:colour_at + 3] = bytes((b, g, r))
    if intensity_at is not None:
        struct.pack_into('<f', data, intensity_at, float(value[3]))


def _home_for_each(targets, home):
    """The home light each target takes: in order when the counts match, else the
    brightest home light for the target's brightest and the first other for the rest."""
    if len(targets) == len(home):
        return list(home)
    bright = max(home, key=lambda h: h[3])
    rest = next((h for h in home if h is not bright), bright)
    top = max(range(len(targets)), key=lambda i: targets[i]) if targets else None
    return [bright if i == top else rest for i in range(len(targets))]


#: Read-only scene value: the height fog's base height (its component's Z, uu).
FOG_HEIGHT = 'fogHeight'


def _fog_height(pkg, names):
    """The first ExponentialHeightFogComponent's Z, or None when the package has none."""
    from riftlord.formats.omd3 import meshcat
    for ex in pkg.exports:
        if _class_of(pkg, ex) != 'ExponentialHeightFogComponent':
            continue
        data = ex['data']
        top = meshcat._walk(data, names, 0, len(data))
        if 'RelativeLocation' not in top:
            return 0.0
        return round(struct.unpack_from('<3f', data, top['RelativeLocation'][1])[2], 1)
    return None


def _read(data, at, kind):
    if kind == 'colour':
        return [round(v, 4) for v in struct.unpack_from('<4f', data, at)]
    return round(struct.unpack_from('<f', data, at)[0], 4)


def _write(data, at, kind, value):
    if kind == 'colour':
        struct.pack_into('<4f', data, at, *[float(v) for v in value])
    else:
        struct.pack_into('<f', data, at, float(value))


@functools.lru_cache(maxsize=16)
def _survey(level):
    """{package path: {'filter': [material], 'slots': {key: count}, 'values': {key: v}}}."""
    from riftlord.core import gamefiles
    from riftlord.formats.omd3 import omd3level
    from riftlord.formats.ue.uassetio import Package
    out = {}
    for path in omd3level.sublevels(level):
        try:
            pkg = Package(*gamefiles.package(path))
        except Exception:                            # noqa: BLE001
            continue
        pkg.load_payloads()
        names = [n for n, _h in pkg.names]
        found = [m for _i, _o, m in _blendables(pkg, names) if m in FILTER_MATERIALS]
        slots = _scene_slots(pkg, names)
        values = {k: (SCENE_FIELDS[k]['default'] if rows[0][1] is None else
                      _read(pkg.exports[rows[0][0] - 1]['data'], rows[0][1],
                            SCENE_FIELDS[k]['kind'])) for k, rows in slots.items()}
        height = _fog_height(pkg, names)
        if height is not None:
            values[FOG_HEIGHT] = height
        lights = [k for k, cls in ((SUNS, 'DirectionalLightComponent'),
                                   (SKY_LIGHT, 'SkyLightComponent'))
                  if any(_class_of(pkg, ex) == cls for ex in pkg.exports)]
        from riftlord.build.world import skymirror
        if skymirror.holds(pkg):
            lights.append(SKY_HOME)
        if found or slots or lights:
            out[path] = {'filter': found, 'slots': {k: len(v) for k, v in slots.items()},
                         'values': values, 'lights': lights}
    return out


def filters(level):
    """{package path: [material name]} for the packages of `level` with an intro filter."""
    return {p: r['filter'] for p, r in _survey(level).items() if r['filter']}


def scene(level):
    """{key: shipped value} for the SCENE_FIELDS the level stores; a key it does not
    store is absent (the engine default applies and the editor cannot change it)."""
    out = {}
    for rec in _survey(level).values():
        for k, v in rec['values'].items():
            out.setdefault(k, v)
    return out


def fields():
    """SCENE_FIELDS for the editor: [{key, group, label, kind, min, max, step}]."""
    out = []
    for key, f in SCENE_FIELDS.items():
        row = {'key': key, 'group': f['group'], 'label': f['label'], 'kind': f['kind']}
        if 'range' in f:
            row['min'], row['max'], row['step'] = f['range']
        out.append(row)
    return out


def clean(raw):
    """The SCENE_FIELDS keys of a plan's `world`, validated."""
    out = {}
    for key, f in SCENE_FIELDS.items():
        value = (raw or {}).get(key)
        if value is None:
            continue
        if f['kind'] == 'colour':
            if not isinstance(value, (list, tuple)) or len(value) != 4:
                raise ValueError('%s needs four numbers' % f['label'])
            value = [float(v) for v in value]
            if any(not math.isfinite(v) or v < 0 for v in value):
                raise ValueError('%s out of range' % f['label'])
        else:
            value = float(value)
            lo, hi, _s = f['range']
            if not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError('%s must be between %g and %g' % (f['label'], lo, hi))
        out[key] = value
    return out


def _tag_of(level, path):
    from riftlord.mapeditor import leveldata
    by_path = {p: t for t, p in (leveldata.level(level).get('subPackages') or {}).items()}
    return by_path.get(path, 'persistent')


def sublevel_tags(level, world):
    """The streamed-sublevel tags (leveldata `subPackages` keys) the plan's world
    settings write into."""
    world = world or {}
    keys = set(clean(world))
    out = []
    for path, rec in _survey(level).items():
        touched = ((world.get(PLAN_KEY) == REMOVE and rec['filter'])
                   or keys & set(rec['slots'])
                   or any(world.get(k) for k in rec.get('lights') or ()))
        tag = _tag_of(level, path)
        if touched and tag != 'persistent' and tag not in out:
            out.append(tag)
    return out


def wanted_removed(plan):
    """True when the plan removes the level's intro filter."""
    return ((plan or {}).get('world') or {}).get(PLAN_KEY) == REMOVE


def apply(builder, world, report):
    """Write the plan's scene settings into the package being built. Returns the count
    of values written (filter blendables included)."""
    world = world or {}
    pkg = builder.pkg
    names = [n for n, _h in pkg.names]
    done = 0
    touched = {}
    #: first: it rewrites whole exports, and the edits below are offsets into them
    mirrored = False
    if world.get(SKY_HOME):
        from riftlord.build.world import skymirror
        mirrored = skymirror.mirror(builder, world[SKY_HOME], report) > 0
        names = [n for n, _h in pkg.names]

    def edit(index, fn):
        if index not in touched:
            touched[index] = bytearray(pkg.exports[index - 1]['data'])
        fn(touched[index])

    if world.get(PLAN_KEY) == REMOVE:
        for index, at, name in _blendables(pkg, names):
            if name in FILTER_MATERIALS:
                edit(index, lambda d, at=at: struct.pack_into('<i', d, at, 0))
                done += 1
    want = clean(world)
    #: (export, property, value) of settings the level leaves at the class default
    inserts = []
    for key, rows in _scene_slots(pkg, names).items():
        if key not in want:
            continue
        for index, at in rows:
            if at is None:
                inserts.append((index, SCENE_FIELDS[key]['prop'], want[key]))
                done += 1
                continue
            edit(index, lambda d, at=at, key=key: _write(
                d, at, SCENE_FIELDS[key]['kind'], want[key]))
            done += 1
    if world.get(SUNS) and not mirrored:
        rows = _lights(pkg, names, 'DirectionalLightComponent')
        own = [struct.unpack_from('<f', pkg.exports[i - 1]['data'], at)[0] if at is not None
               else 0.0 for i, _c, at in rows]
        for (index, colour_at, intensity_at), value in zip(
                rows, _home_for_each(own, world[SUNS])):
            if colour_at is None and intensity_at is None:
                report.setdefault('skipped', []).append(
                    'sun export %d stores no colour or intensity' % index)
                continue
            edit(index, lambda d, c=colour_at, t=intensity_at, v=value: _paint_light(d, c, t, v))
            done += 1
    if world.get(SKY_LIGHT) and not mirrored:
        for index, colour_at, intensity_at in _lights(pkg, names, 'SkyLightComponent'):
            edit(index, lambda d, c=colour_at, t=intensity_at: _paint_light(
                d, c, t, world[SKY_LIGHT]))
            done += 1
    for index, data in touched.items():
        pkg.exports[index - 1]['data'] = bytes(data)
    #: after the in-place writes, whose offsets an inserted property would move
    for index, prop, value in inserts:
        builder.set_property(index, prop, 'FloatProperty', struct.pack('<f', float(value)))
    return done


def notes(level, world):
    """Save notes for the plan's scene settings, and the ones the level cannot take."""
    world = world or {}
    out, skipped = [], []
    if world.get(PLAN_KEY) == REMOVE:
        out.append('intro filter removed' if filters(level)
                   else 'this level has no intro filter to remove')
    have = scene(level)
    for key, value in clean(world).items():
        f = SCENE_FIELDS[key]
        if key in have:
            out.append('%s %s set to %s (shipped %s)'
                       % (f['group'].lower(), f['label'].lower(), value, have[key]))
        else:
            skipped.append('%s %s: this level does not store it, so it is left as is'
                           % (f['group'].lower(), f['label'].lower()))
    return out, skipped
