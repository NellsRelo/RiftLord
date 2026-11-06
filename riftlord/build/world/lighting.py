"""A map's lighting by engine property: suns, sky light, atmospheric fog, height fog,
post process, and the level's own placed lights.

Plan data (values below; None removes the property, so the class default applies):

* `lighting` {section: {Property: value}}: every component of the section's class in the
  level (SECTIONS: SkyLight, AtmosphericFog, ExponentialHeightFog, the unbound
  PostProcessVolumes' Settings) takes the values. `FogHeight` sets the fog components' Z.
* `lights` {'<sublevel tag>|<actor export>': {Property: value}}: every light component of
  that actor (DirectionalLight, PointLight, SpotLight, RectLight, a torch's lights).
  `RelativeRotation` turns a sun.
* `added[n].light` {Property: value}: the light components of an added light actor.
* A level DirectionalLight in `removedFx` is switched off (`bVisible` false).

Values: numbers, bools, '#rrggbb' for an FColor (sRGB bytes, as the level stores it),
[r, g, b, a] for a LinearColor, [x, y, z, w] for a Vector4, [pitch, yaw, roll] for a
Rotator, [x, y, z] for a Vector, the enum entry's name for an enum. A post-process value
also turns its `bOverride_` on, unless the plan states that override itself.

Plans saved before 2026-11-05 have no `lighting`; lightmigrate.migrate turns what their
sky and world settings used to bring into these values (load_plan, save_plan, the build).

    python3 -m riftlord.build.world.lighting Scenario_09    # the level's lighting values
"""

import functools
import json
import math
import re
import struct
import sys

#: Plan keys.
PLAN_KEY, LIGHTS_KEY, ADDED_KEY = 'lighting', 'lights', 'light'
#: Level-wide sections: plan section -> component class.
SECTIONS = {'skyLight': 'SkyLightComponent', 'atmosphere': 'AtmosphericFogComponent',
            'heightFog': 'ExponentialHeightFogComponent', 'postProcess': 'PostProcessVolume'}
#: The post-process section's values live in this struct of an unbound PostProcessVolume.
SETTINGS = 'Settings'
#: Light component classes, by the kind the panel shows.
LIGHT_CLASSES = {'DirectionalLightComponent': 'sun', 'PointLightComponent': 'point',
                 'SpotLightComponent': 'spot', 'RectLightComponent': 'rect'}
SUN_CLASS = 'DirectionalLightComponent'
#: The fog components' height (their RelativeLocation Z), uu.
FOG_HEIGHT = 'FogHeight'
#: An actor key ('<sublevel tag>|<actor export, 1-based>').
ACTOR_KEY = re.compile(r'^[^|]+\|[1-9][0-9]*$')
#: Prefix of a post-process setting's override switch.
OVERRIDE = 'bOverride_'

#: Every property a value may be written to, by kind (measured on every shipped level's
#: lighting components and unbound post-process Settings; FogMultiplier, DefaultLightColor,
#: ColorContrast, FilmBlackClip and bVisible on lights added from UE 4.27's classes).
PROPS = {
    # lights
    'AtmosphereSunLightIndex': 'int', 'AttenuationRadius': 'float', 'BarnDoorAngle': 'float',
    'BarnDoorLength': 'float', 'BloomScale': 'float', 'CastDynamicShadows': 'bool',
    'CastShadows': 'bool', 'CastStaticShadows': 'bool', 'CastTranslucentShadows': 'bool',
    'DistanceFieldShadowDistance': 'float', 'DynamicShadowCascades': 'int',
    'DynamicShadowDistanceMovableLight': 'float',
    'DynamicShadowDistanceStationaryLight': 'float', 'IndirectLightingIntensity': 'float',
    'InnerConeAngle': 'float', 'Intensity': 'float', 'IntensityUnits': 'enum:ELightUnits',
    'LightColor': 'color', 'LightFalloffExponent': 'float', 'Mobility': 'byte:EComponentMobility',
    'OuterConeAngle': 'float', 'RelativeLocation': 'vector', 'RelativeRotation': 'rotator',
    'ShadowAmount': 'float', 'ShadowBias': 'float', 'SoftSourceRadius': 'float',
    'SourceHeight': 'float', 'SourceLength': 'float', 'SourceRadius': 'float',
    'SourceWidth': 'float', 'SpecularScale': 'float', 'Temperature': 'float',
    'VolumetricScatteringIntensity': 'float', 'bAffectGlobalIllumination': 'bool',
    'bAffectReflection': 'bool', 'bAffectTranslucentLighting': 'bool', 'bAffectsWorld': 'bool',
    'bCastRaytracedShadow': 'bool', 'bCastVolumetricShadow': 'bool',
    'bUseInverseSquaredFalloff': 'bool', 'bUseRayTracedDistanceFieldShadows': 'bool',
    'bUseTemperature': 'bool', 'bUsedAsAtmosphereSunLight': 'bool', 'bVisible': 'bool',
    # sky light
    'LowerHemisphereColor': 'linear', 'OcclusionMaxDistance': 'float',
    'SourceCubemapAngle': 'float', 'SourceType': 'byte:ESkyLightSourceType',
    'bLowerHemisphereIsBlack': 'bool',
    # atmospheric fog
    'AltitudeScale': 'float', 'DefaultBrightness': 'float', 'DefaultLightColor': 'color',
    'DensityMultiplier': 'float', 'DensityOffset': 'float', 'DistanceOffset': 'float',
    'DistanceScale': 'float', 'FogMultiplier': 'float', 'GroundOffset': 'float',
    'StartDistance': 'float', 'SunDiscScale': 'float', 'SunMultiplier': 'float',
    'bAtmosphereAffectsSunIlluminance': 'bool', 'bDisableSunDisk': 'bool',
    # height fog
    'DirectionalInscatteringColor': 'linear', 'DirectionalInscatteringExponent': 'float',
    'DirectionalInscatteringStartDistance': 'float', 'FogCutoffDistance': 'float',
    'FogDensity': 'float', 'FogHeightFalloff': 'float', 'FogInscatteringColor': 'linear',
    'FogMaxOpacity': 'float', 'VolumetricFogAlbedo': 'color', FOG_HEIGHT: 'float',
    # post process Settings
    'AmbientCubemapIntensity': 'float', 'AmbientOcclusionIntensity': 'float',
    'AmbientOcclusionMipThreshold': 'float', 'AmbientOcclusionPower': 'float',
    'AmbientOcclusionRadius': 'float', 'AmbientOcclusionStaticFraction': 'float',
    'AutoExposureBias': 'float', 'AutoExposureBiasBackup': 'float',
    'AutoExposureMaxBrightness': 'float', 'AutoExposureMinBrightness': 'float',
    'BloomDirtMaskIntensity': 'float', 'BloomIntensity': 'float',
    'ChromaticAberrationStartOffset': 'float', 'ColorContrast': 'vector4',
    'ColorGain': 'vector4', 'ColorGamma': 'vector4', 'ColorOffsetShadows': 'vector4',
    'ColorSaturation': 'vector4', 'FilmBlackClip': 'float', 'FilmShoulder': 'float',
    'FilmSlope': 'float', 'FilmToe': 'float', 'FilmWhiteClip': 'float',
    'LensFlareBokehSize': 'float', 'LensFlareIntensity': 'float', 'MotionBlurAmount': 'float',
    'SceneFringeIntensity': 'float', 'ScreenSpaceReflectionMaxRoughness': 'float',
    'VignetteIntensity': 'float',
}
#: The post-process overrides (`bOverride_<setting>`) are bools.
OVERRIDABLE = ('AmbientOcclusionFadeDistance', 'AmbientOcclusionFadeRadius',
               'AmbientOcclusionIntensity', 'AmbientOcclusionMipBlend',
               'AmbientOcclusionMipScale', 'AmbientOcclusionQuality', 'AmbientOcclusionRadius',
               'AutoExposureBias', 'AutoExposureBiasBackup', 'AutoExposureMaxBrightness',
               'AutoExposureMinBrightness', 'AutoExposureSpeedDown', 'BloomIntensity',
               'BloomThreshold', 'ColorContrast', 'ColorGain', 'ColorGamma',
               'ColorOffsetShadows', 'ColorSaturation', 'FilmBlackClip', 'FilmShoulder',
               'FilmSlope', 'FilmToe', 'FilmWhiteClip', 'LPVDirectionalOcclusionFadeRange',
               'LPVFadeRange', 'LPVVplInjectionBias', 'MotionBlurAmount',
               'SceneFringeIntensity', 'ScreenSpaceReflectionMaxRoughness',
               'VignetteIntensity')
PROPS.update({OVERRIDE + name: 'bool' for name in OVERRIDABLE})
#: The entries of the enums a value may name.
ENUMS = {'EComponentMobility': ['EComponentMobility::Static', 'EComponentMobility::Stationary',
                                'EComponentMobility::Movable'],
         'ESkyLightSourceType': ['SLS_CapturedScene', 'SLS_SpecifiedCubemap'],
         'ELightUnits': ['ELightUnits::Unitless', 'ELightUnits::Candelas',
                         'ELightUnits::Lumens']}
STATIC, MOVABLE = 'EComponentMobility::Static', 'EComponentMobility::Movable'
STATIONARY = 'EComponentMobility::Stationary'

#: UE 4.27's class defaults (the component constructors, the native light actors'
#: Stationary mobility, FPostProcessSettings), shown for a property the level does not
#: store. A Blueprint's light component is Movable unless its class says otherwise.
DEFAULTS = {
    'DirectionalLightComponent': {'Intensity': 10.0, 'LightColor': '#ffffff',
                                  'RelativeRotation': [0.0, 0.0, 0.0], 'CastShadows': True,
                                  'Mobility': STATIONARY},
    'PointLightComponent': {'Intensity': 5000.0, 'LightColor': '#ffffff',
                            'AttenuationRadius': 1000.0, 'CastShadows': True,
                            'Mobility': STATIONARY},
    'SpotLightComponent': {'Intensity': 5000.0, 'LightColor': '#ffffff',
                           'AttenuationRadius': 1000.0, 'InnerConeAngle': 0.0,
                           'OuterConeAngle': 44.0, 'CastShadows': True,
                           'Mobility': STATIONARY},
    'RectLightComponent': {'Intensity': 5000.0, 'LightColor': '#ffffff',
                           'AttenuationRadius': 1000.0, 'CastShadows': True,
                           'Mobility': STATIONARY},
    'SkyLightComponent': {'LightColor': '#ffffff', 'Intensity': 1.0,
                          'LowerHemisphereColor': [0.0, 0.0, 0.0, 1.0],
                          'bLowerHemisphereIsBlack': True, 'IndirectLightingIntensity': 1.0,
                          'SourceType': 'SLS_CapturedScene', 'Mobility': STATIONARY},
    'AtmosphericFogComponent': {'SunMultiplier': 1.0, 'FogMultiplier': 1.0,
                                'DensityMultiplier': 1.0, 'DensityOffset': 0.0,
                                'DefaultBrightness': 50.0, 'DefaultLightColor': '#ffffff'},
    'ExponentialHeightFogComponent': {'FogDensity': 0.02, 'FogHeightFalloff': 0.2,
                                      'FogMaxOpacity': 1.0, 'StartDistance': 0.0,
                                      'FogInscatteringColor': [0.447, 0.638, 1.0, 1.0],
                                      'DirectionalInscatteringColor': [0.25, 0.25, 0.125, 1.0],
                                      FOG_HEIGHT: 0.0},
    'PostProcessVolume': {'AutoExposureBias': 0.0, 'BloomIntensity': 0.675,
                          'ColorSaturation': [1.0, 1.0, 1.0, 1.0],
                          'ColorContrast': [1.0, 1.0, 1.0, 1.0],
                          'ColorGamma': [1.0, 1.0, 1.0, 1.0],
                          'ColorGain': [1.0, 1.0, 1.0, 1.0]},
}


def _f(prop, label, lo=None, hi=None, step=None, **more):
    row = {'prop': prop, 'label': label, 'kind': PROPS[prop]}
    if lo is not None:
        row.update(min=lo, max=hi, step=step)
    if PROPS[prop].startswith(('byte:', 'enum:')):
        row['options'] = ENUMS[PROPS[prop].split(':', 1)[1]]
    row.update(more)
    return row


#: The fields the Map Editor shows, by section ('sun' and 'light' are per actor), with the
#: engine's names. `Pitch` and `Yaw` edit a sun's RelativeRotation.
FIELDS = {
    'sun': [_f('LightColor', 'Light Color'), _f('Intensity', 'Intensity', 0.0, 50.0, 0.05),
            {'prop': 'Pitch', 'label': 'Pitch', 'kind': 'float', 'min': -90.0, 'max': 90.0,
             'step': 1.0, 'of': 'RelativeRotation', 'axis': 0},
            {'prop': 'Yaw', 'label': 'Yaw', 'kind': 'float', 'min': -180.0, 'max': 180.0,
             'step': 1.0, 'of': 'RelativeRotation', 'axis': 1},
            _f('CastShadows', 'Cast Shadows')],
    'light': [_f('LightColor', 'Light Color'), _f('Intensity', 'Intensity', 0.0, 200000.0, 10.0),
              _f('AttenuationRadius', 'Attenuation Radius', 0.0, 50000.0, 10.0),
              _f('InnerConeAngle', 'Inner Cone Angle', 0.0, 80.0, 1.0, kinds=['spot']),
              _f('OuterConeAngle', 'Outer Cone Angle', 1.0, 80.0, 1.0, kinds=['spot']),
              _f('Mobility', 'Mobility'), _f('CastShadows', 'Cast Shadows')],
    'skyLight': [_f('LightColor', 'Light Color'),
                 _f('LowerHemisphereColor', 'Lower Hemisphere Color'),
                 _f('Intensity', 'Intensity', 0.0, 20.0, 0.05),
                 _f('IndirectLightingIntensity', 'Indirect Lighting Intensity', 0.0, 50.0, 0.1),
                 _f('bLowerHemisphereIsBlack', 'Lower Hemisphere Is Black')],
    'atmosphere': [_f('DefaultLightColor', 'Default Light Color'),
                   _f('DefaultBrightness', 'Default Brightness', 0.0, 1000.0, 1.0),
                   _f('SunMultiplier', 'Sun Multiplier', 0.0, 1000.0, 0.05),
                   _f('FogMultiplier', 'Fog Multiplier', 0.0, 1000.0, 0.05),
                   _f('DensityMultiplier', 'Density Multiplier', 0.001, 1000.0, 0.05),
                   _f('DensityOffset', 'Density Offset', -1.0, 1.0, 0.01)],
    'heightFog': [_f('FogInscatteringColor', 'Fog Inscattering Color'),
                  _f('DirectionalInscatteringColor', 'Directional Inscattering Color'),
                  _f('FogDensity', 'Fog Density', 0.0, 10.0, 0.01),
                  _f('FogHeightFalloff', 'Fog Height Falloff', 0.0, 2.0, 0.005),
                  _f('FogMaxOpacity', 'Fog Max Opacity', 0.0, 1.0, 0.01),
                  _f('StartDistance', 'Start Distance', 0.0, 100000.0, 100.0),
                  _f(FOG_HEIGHT, 'Fog Height', -100000.0, 100000.0, 10.0)],
    'postProcess': [_f('AutoExposureBias', 'Exposure Compensation', -15.0, 15.0, 0.05),
                    _f('BloomIntensity', 'Bloom Intensity', 0.0, 8.0, 0.05),
                    _f('ColorSaturation', 'Saturation', 0.0, 2.0, 0.01),
                    _f('ColorContrast', 'Contrast', 0.0, 2.0, 0.01),
                    _f('ColorGamma', 'Gamma', 0.0, 2.0, 0.01),
                    _f('ColorGain', 'Gain', 0.0, 2.0, 0.01)],
}
#: Section titles.
TITLES = {'sun': 'Sun', 'skyLight': 'Sky Light', 'atmosphere': 'Atmospheric Fog',
          'heightFog': 'Height Fog', 'postProcess': 'Post Process', 'light': 'Light'}
#: Light kinds whose mobility the build can change (Static bakes; a level light shipped
#: Movable or Stationary has no light build data, so it may only become Movable).
MOBILITY_CHOICES = (STATIC, MOVABLE)

#: Struct names of the struct kinds.
STRUCTS = {'color': 'Color', 'linear': 'LinearColor', 'vector4': 'Vector4',
           'vector': 'Vector', 'rotator': 'Rotator'}
#: Float counts of the float struct kinds.
FLOATS = {'linear': 4, 'vector4': 4, 'vector': 3, 'rotator': 3}
#: Tag header length by property type (levelbuild.Builder.HEAD).
HEAD = {'BoolProperty': 26, 'StructProperty': 49, 'ArrayProperty': 33,
        'ByteProperty': 33, 'EnumProperty': 33}
HEX = re.compile(r'^#[0-9a-f]{6}([0-9a-f]{2})?$')


# -- values --------------------------------------------------------------------------

def f32(value):
    """The shortest decimal that is the same float32 as `value`: what the package stores,
    written back exactly."""
    raw = struct.pack('<f', float(value))
    for digits in range(1, 10):
        got = float('%.*g' % (digits, struct.unpack('<f', raw)[0]))
        if struct.pack('<f', got) == raw:
            return got
    return struct.unpack('<f', raw)[0]


def kind_of(prop):
    """A property's kind, or None when no value may be written to it."""
    return PROPS.get(prop)


def clean_value(prop, value):
    """A plan value checked against its property's kind; None stays None. Raises
    ValueError."""
    kind = kind_of(prop)
    if kind is None:
        raise ValueError('no such lighting property: %r' % (prop,))
    if value is None:
        return None
    if kind == 'float':
        value = float(value)
        if not math.isfinite(value):
            raise ValueError('%s must be a number' % prop)
        return f32(value)
    if kind == 'int':
        return int(value)
    if kind == 'bool':
        if not isinstance(value, bool):
            raise ValueError('%s must be true or false' % prop)
        return value
    if kind == 'color':
        value = str(value).strip().lower()
        if not HEX.match(value):
            raise ValueError('%s must be #rrggbb' % prop)
        return value
    if kind in FLOATS:
        if not isinstance(value, (list, tuple)) or len(value) != FLOATS[kind]:
            raise ValueError('%s needs %d numbers' % (prop, FLOATS[kind]))
        out = [float(v) for v in value]
        if any(not math.isfinite(v) for v in out):
            raise ValueError('%s must be numbers' % prop)
        return [f32(v) for v in out]
    enum = kind.split(':', 1)[1]
    if value not in ENUMS[enum]:
        raise ValueError('%s must be one of %s' % (prop, ', '.join(ENUMS[enum])))
    return value


def _clean_props(raw):
    out = {}
    if not isinstance(raw, dict):
        return out
    for prop, value in raw.items():
        out[str(prop)] = clean_value(str(prop), value)
    return out


def clean(raw):
    """A plan's `lighting`, checked: {section: {Property: value}}. Raises ValueError."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for section, props in raw.items():
        if section not in SECTIONS:
            raise ValueError('no such lighting section: %r' % (section,))
        got = _clean_props(props)
        if got:
            out[section] = got
    return out


def clean_lights(raw):
    """A plan's `lights`, checked: {actor key: {Property: value}}. Raises ValueError."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for key, props in raw.items():
        if not ACTOR_KEY.match(str(key)):
            continue
        got = _clean_props(props)
        if got:
            out[str(key)] = got
    return out


def clean_added(raw):
    """An added item's `light`, checked (None for none)."""
    got = _clean_props(raw)
    return got or None


def hex_colour(raw):
    """'#rrggbb' (and 'aa' when not opaque) for FColor bytes (b, g, r, a)."""
    b, g, r, a = raw[:4]
    return '#%02x%02x%02x' % (r, g, b) + ('' if a == 255 else '%02x' % a)


def colour_bytes(value):
    """FColor bytes (b, g, r, a) for '#rrggbb[aa]'."""
    r, g, b = (int(value[i:i + 2], 16) for i in (1, 3, 5))
    a = int(value[7:9], 16) if len(value) == 9 else 255
    return bytes((b, g, r, a))


def payload(kind, value, add_name):
    """(property type, type header bytes after the index, payload) for one value."""
    if kind == 'float':
        return 'FloatProperty', b'\0', struct.pack('<f', float(value))
    if kind == 'int':
        return 'IntProperty', b'\0', struct.pack('<i', int(value))
    if kind == 'bool':
        return 'BoolProperty', bytes((1 if value else 0, 0)), b''
    if kind in STRUCTS:
        raw = colour_bytes(value) if kind == 'color' else struct.pack(
            '<%df' % FLOATS[kind], *[float(v) for v in value])
        return ('StructProperty', struct.pack('<ii', add_name(STRUCTS[kind]), 0) + b'\0' * 17,
                raw)
    typ, enum = kind.split(':', 1)
    return (('ByteProperty' if typ == 'byte' else 'EnumProperty'),
            struct.pack('<ii', add_name(enum), 0) + b'\0',
            struct.pack('<ii', add_name(value), 0))


def tag(prop, kind, value, add_name):
    """One property tag's bytes."""
    typ, head, raw = payload(kind, value, add_name)
    return (struct.pack('<ii', add_name(prop), 0) + struct.pack('<ii', add_name(typ), 0)
            + struct.pack('<ii', len(raw), 0) + head + raw)


# -- reading tags --------------------------------------------------------------------

def tags(data, names, start=0, end=None):
    """[(name, type, start, end)] of a property stream, up to its None."""
    end = len(data) if end is None else end
    out, o = [], start
    while o + 25 <= end:
        ni = struct.unpack_from('<i', data, o)[0]
        if not 0 <= ni < len(names) or names[ni] == 'None':
            break
        typ = names[struct.unpack_from('<i', data, o + 8)[0]]
        size = 0 if typ == 'BoolProperty' else struct.unpack_from('<i', data, o + 16)[0]
        size_end = o + HEAD.get(typ, 25) + size
        out.append((names[ni], typ, o, size_end))
        o = size_end
    return out


def terminator(data, names, start=0, end=None):
    """Where a stream's None sits."""
    got = tags(data, names, start, end)
    return got[-1][3] if got else start


def read_value(data, names, typ, a, b):
    """A tag's value as plan data, or None for a kind this module does not write."""
    if typ == 'FloatProperty':
        return f32(struct.unpack_from('<f', data, a + 25)[0])
    if typ == 'IntProperty':
        return struct.unpack_from('<i', data, a + 25)[0]
    if typ == 'BoolProperty':
        return bool(data[a + 24])
    if typ in ('ByteProperty', 'EnumProperty'):
        if b - a - 33 != 8:
            return None
        return names[struct.unpack_from('<i', data, a + 33)[0]]
    if typ == 'StructProperty':
        sname = names[struct.unpack_from('<i', data, a + 24)[0]]
        raw = data[a + 49:b]
        if sname == 'Color' and len(raw) == 4:
            return hex_colour(raw)
        kind = {v: k for k, v in STRUCTS.items()}.get(sname)
        if kind in FLOATS and len(raw) == 4 * FLOATS[kind]:
            return [f32(v) for v in struct.unpack('<%df' % FLOATS[kind], raw)]
    return None


def values(data, names, start=0, end=None):
    """{Property: plan value} of the writable properties a stream stores."""
    out = {}
    for name, typ, a, b in tags(data, names, start, end):
        if name in PROPS and struct.unpack_from('<i', data, a + 20)[0] == 0:
            got = read_value(data, names, typ, a, b)
            if got is not None:
                out[name] = got
    return out


# -- writing -------------------------------------------------------------------------

def edit_stream(data, names, start, end, props, add_name, settings=False):
    """The property stream [start, end) with `props` written (None: removed), WITHOUT its
    None. In a post-process Settings stream (`settings`) a value also turns its
    `bOverride_` on, unless `props` states that override."""
    props = dict(props)
    if settings:
        for prop, value in list(props.items()):
            switch = OVERRIDE + prop
            if value is not None and not prop.startswith(OVERRIDE) and switch in PROPS \
                    and switch not in props:
                props[switch] = True
    out = bytearray()
    for name, _typ, a, b in tags(data, names, start, end):
        if name not in props:
            out += data[a:b]
    for prop, value in props.items():
        if value is not None:
            out += tag(prop, PROPS[prop], value, add_name)
    return bytes(out)


def _class_of(pkg, index):
    """Class name of export `index` (1-based)."""
    from riftlord.build import levelbuild
    ref = struct.unpack_from('<i', pkg.exports[index - 1]['meta'], 0)[0]
    return levelbuild.resolve(pkg, ref)[1]


def _outer(pkg, index):
    from riftlord.build import levelbuild
    return struct.unpack_from('<i', pkg.exports[index - 1]['meta'], levelbuild.OUTER_AT)[0]


def _settings_span(data, names):
    """(start, end) of an unbound PostProcessVolume's Settings tag, or None."""
    top = {n: (a, b) for n, _t, a, b in tags(data, names)}
    if 'bUnbound' not in top or SETTINGS not in top:
        return None
    return top[SETTINGS]


def _resolve(data, names, props):
    """`props` with FogHeight as the RelativeLocation it sets (X and Y kept)."""
    if FOG_HEIGHT not in props:
        return props
    props = dict(props)
    height = props.pop(FOG_HEIGHT)
    if height is None:
        return props
    at = values(data, names).get('RelativeLocation') or [0.0, 0.0, 0.0]
    props['RelativeLocation'] = [at[0], at[1], float(height)]
    return props


def write_export(pkg, index, props, settings=False):
    """Write `props` into export `index` (1-based) of a uassetio Package: into its
    component properties, or into its unbound Settings (`settings`). Returns whether it
    wrote."""
    ex = pkg.exports[index - 1]
    data = ex['data']
    names = [n for n, _h in pkg.names]
    add = pkg.add_name
    if settings:
        span = _settings_span(data, names)
        if not span:
            return False
        a, b = span
        inner = edit_stream(data, names, a + 49, b, props, add, settings=True)
        inner += struct.pack('<ii', add('None'), 0)
        head = bytearray(data[a:a + 49])
        struct.pack_into('<i', head, 16, len(inner))
        new = data[:a] + bytes(head) + inner + data[b:]
    else:
        end = terminator(data, names)
        body = edit_stream(data, names, 0, end, _resolve(data, names, props), add)
        new = body + data[end:]
    ex['data'] = bytes(new)
    ex['size'] = len(new)
    return True


def write(builder, edits, report):
    """Write one package's lighting edits ({export: {'props', 'settings'}}, as
    package_edits gives them) into builder.pkg. Returns the count of exports written."""
    done = 0
    for index, edit in sorted((edits or {}).items()):
        if write_export(builder.pkg, int(index), edit['props'], edit.get('settings')):
            done += 1
        else:
            report.setdefault('skipped', []).append(
                'lighting: export %d has nothing to write into' % int(index))
    if done:
        report.setdefault('notes', []).append('lighting: %d component(s) set' % done)
    return done


# -- the level -----------------------------------------------------------------------

def _packages(level):
    """[(sublevel tag, package path)] of a level, persistent first."""
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import leveldata
    by_path = {p: t for t, p in (leveldata.level(level).get('subPackages') or {}).items()}
    return [(by_path.get(path, 'persistent'), path) for path in omd3level.sublevels(level)]


@functools.lru_cache(maxsize=64)
def _load(path):
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(*gamefiles.package(path))
    pkg.load_payloads()
    return pkg


#: The tag a level from scratch's light rig is written under.
RIG_TAG = 'Lighting'


@functools.lru_cache(maxsize=1)
def rig():
    """The light rig a level from scratch streams (authorlevel.lighting), as a Package."""
    from riftlord.build import authorlevel
    from riftlord.formats.ue.uassetio import Package
    (ua, ux), _report = authorlevel.lighting('RiftLordRig')
    pkg = Package(ua, ux)
    pkg.load_payloads()
    return pkg


def survey(pkg):
    """{'sections': {section: [export]}, 'lights': {actor export: [(export, class)]}} of one
    package (exports 1-based; post-process volumes only when unbound)."""
    names = [n for n, _h in pkg.names]
    out = {'sections': {}, 'lights': {}}
    by_class = {cls: sec for sec, cls in SECTIONS.items()}
    for i in range(1, len(pkg.exports) + 1):
        try:
            cls = _class_of(pkg, i)
        except Exception:                            # noqa: BLE001
            continue
        if cls in by_class:
            if cls == 'PostProcessVolume' and not _settings_span(pkg.exports[i - 1]['data'],
                                                                 names):
                continue
            out['sections'].setdefault(by_class[cls], []).append(i)
        elif cls in LIGHT_CLASSES:
            out['lights'].setdefault(_outer(pkg, i), []).append((i, cls))
    return out


def level_packages(level, plan=None):
    """[(tag, Package)] the plan's lighting reads and writes: the level's packages, or the
    light rig for a level from scratch."""
    from riftlord.mapeditor import scratch
    if scratch.is_scratch(plan):
        return [(RIG_TAG, rig())]
    return [(tag, _load(path)) for tag, path in _packages(level)]


def edits_for(pkg, tag, plan):
    """{export: {'props', 'settings'}} the plan writes into one package."""
    plan = plan or {}
    want = clean(plan.get(PLAN_KEY))
    lights = clean_lights(plan.get(LIGHTS_KEY))
    gone = {str(k) for k in plan.get('removedFx') or []}
    found = survey(pkg)
    out = {}
    for section, exports in found['sections'].items():
        if want.get(section):
            for i in exports:
                out[i] = {'props': dict(want[section]), 'settings': section == 'postProcess'}
    for actor, comps in found['lights'].items():
        key = '%s|%d' % (tag, actor)
        props = dict(lights.get(key) or {})
        for i, cls in comps:
            mine = dict(props)
            if key in gone and cls == SUN_CLASS:
                mine['bVisible'] = False
            if mine:
                out[i] = {'props': mine, 'settings': False}
    return out


def package_edits(level, plan):
    """{sublevel tag: {export: {'props', 'settings'}}} of a plan's lighting on a level it
    streams whole (a level from scratch writes its rig in rig_edits)."""
    from riftlord.mapeditor import scratch
    if scratch.is_scratch(plan) or not _touches(plan):
        return {}
    out = {}
    for tag, pkg in level_packages(level, plan):
        got = edits_for(pkg, tag, plan)
        if got:
            out[tag] = got
    return out


def _touches(plan):
    plan = plan or {}
    if clean(plan.get(PLAN_KEY)) or clean_lights(plan.get(LIGHTS_KEY)):
        return True
    return any(str(k).rpartition('|')[0] for k in plan.get('removedFx') or [])


def write_rig(builder, plan, report):
    """Write the plan's lighting into a level from scratch's light rig (builder.pkg).
    Returns the count of exports written."""
    return write(builder, edits_for(builder.pkg, RIG_TAG, plan), report)


def write_added(builder, added, placed, report):
    """Write each added item's `light` into the light components of the actor placed for
    it (`placed`: {item index: actor export, 1-based})."""
    done = 0
    for n, item in enumerate(added or []):
        props = clean_added((item or {}).get(ADDED_KEY))
        actor = (placed or {}).get(n)
        if not props or not actor:
            continue
        for i in range(1, len(builder.pkg.exports) + 1):
            if _outer(builder.pkg, i) == actor and _class_of(builder.pkg, i) in LIGHT_CLASSES:
                write_export(builder.pkg, i, props)
                done += 1
    if done:
        report.setdefault('notes', []).append('lighting: %d added light component(s) set'
                                              % done)
    return done


# -- what the Map Editor shows -------------------------------------------------------

def _name_of(pkg, index):
    from riftlord.build import levelbuild
    return levelbuild._name_of(pkg, index - 1)


def _shown(cls, stored, settings=False):
    """The values the panel shows for one component: its class defaults under what it
    stores (a post-process setting shows its default unless overridden)."""
    out = dict(DEFAULTS.get(cls) or {})
    for prop, value in stored.items():
        if settings and not prop.startswith(OVERRIDE) and OVERRIDE + prop in PROPS \
                and not stored.get(OVERRIDE + prop):
            continue
        out[prop] = value
    return out


def state(level, plan=None):
    """The lighting a plan's level ships with, as the Map Editor shows it:
    {'suns': [{key, name, values, location}], 'sections': {section: values},
     'stored': {section: values the level stores}, 'fields', 'titles'}. A section the
    level has no component for is absent."""
    suns, sections, stored = [], {}, {}
    for tag, pkg in level_packages(level, plan):
        names = [n for n, _h in pkg.names]
        found = survey(pkg)
        for section, exports in found['sections'].items():
            if section in sections:
                continue
            i = exports[0]
            data = pkg.exports[i - 1]['data']
            if section == 'postProcess':
                a, b = _settings_span(data, names)
                own = values(data, names, a + 49, b)
            else:
                own = values(data, names)
            if section == 'heightFog':
                own[FOG_HEIGHT] = f32((own.get('RelativeLocation') or [0, 0, 0])[2])
            stored[section] = own
            sections[section] = _shown(SECTIONS[section], own, section == 'postProcess')
        for actor, comps in sorted(found['lights'].items()):
            for i, cls in comps:
                if cls != SUN_CLASS:
                    continue
                own = values(pkg.exports[i - 1]['data'], names)
                suns.append({'key': '%s|%d' % (tag, actor), 'name': _name_of(pkg, actor),
                             'values': _shown(cls, own), 'stored': own})
    return {'suns': suns, 'sections': sections, 'stored': stored, 'fields': FIELDS,
            'titles': TITLES}


@functools.lru_cache(maxsize=16)
def _state_cached(level, is_scratch):
    plan = {'scratch': {'name': 'x'}} if is_scratch else None
    return state(level, plan)


def shown(level, is_scratch=False):
    """state() cached per level (and the light rig)."""
    import copy
    return copy.deepcopy(_state_cached(level, bool(is_scratch)))


def light_values(pkg, index, cls, template=None):
    """The panel values of one light component: class defaults, its Blueprint template's
    values, then its own."""
    names = [n for n, _h in pkg.names]
    out = dict(DEFAULTS.get(cls) or {})
    out.update(template or {})
    out.update(values(pkg.exports[index - 1]['data'], names))
    return out


def from_scene(props, cls, blueprint=False):
    """The panel values of a light whose properties lightscene merged (its class
    template's and its own; FColor as (r, g, b, a))."""
    out = dict(DEFAULTS.get(cls) or {})
    if blueprint:
        out['Mobility'] = MOVABLE
    for prop, value in (props or {}).items():
        kind = PROPS.get(prop)
        if kind is None or isinstance(value, bytes):
            continue
        if kind == 'color' and isinstance(value, (tuple, list)) and len(value) >= 3:
            r, g, b = value[:3]
            a = value[3] if len(value) > 3 else 255
            out[prop] = '#%02x%02x%02x' % (int(r), int(g), int(b)) + (
                '' if a == 255 else '%02x' % int(a))
        elif kind in FLOATS and isinstance(value, (tuple, list)):
            out[prop] = [f32(v) for v in value]
        elif kind == 'float' and isinstance(value, (int, float)):
            out[prop] = f32(value)
        elif isinstance(value, (bool, int, str)):
            out[prop] = value
    return out


@functools.lru_cache(maxsize=128)
def _kit_values(cls):
    from riftlord.build import apply
    from riftlord.build.lighting import lightalloc
    from riftlord.build.lighting import lightscene
    donor = apply.donor_for(cls)
    if not donor:
        return []
    pkg, actor = donor
    names = [n for n, _h in pkg.names]
    templates = lightscene._class_templates(cls) if cls.endswith('_C') else {}
    out = []
    for j in lightalloc.under(pkg, actor + 1):
        kind = apply._class_at(pkg, j)
        if kind not in LIGHT_CLASSES:
            continue
        data = pkg.exports[j]['data']
        own = lightscene.decode_props({'names': names}, data, 0,
                                      {'off': 0, 'size': len(data)})
        merged = dict(templates.get(lightalloc._component_name(pkg, j)) or {})
        merged.update(own)
        out.append({'kind': LIGHT_CLASSES[kind],
                    'values': from_scene(merged, kind, cls.endswith('_C'))})
    return out


def kit_values(cls):
    """[{kind, values}] of the light components of the actor the build lifts for an
    added `cls` (its donor placement), as the Light section shows them."""
    import copy
    try:
        return copy.deepcopy(_kit_values(cls))
    except Exception:                                # noqa: BLE001
        return []


# -- the bake ------------------------------------------------------------------------

def touched_sections(plan):
    """The sections whose values a plan changes."""
    return sorted(clean((plan or {}).get(PLAN_KEY)))


def bake_inputs(level, plan):
    """What lightbaker needs of the plan's lighting: {'sky_light': props, 'fog': props,
    'lights': {actor key: props}, 'moves': {actor key: [dx, dy, dz]}, 'removed': [keys]}."""
    from riftlord.mapeditor import leveldata
    plan = plan or {}
    want = clean(plan.get(PLAN_KEY))
    return {'sky_light': want.get('skyLight') or {}, 'fog': want.get('heightFog') or {},
            'lights': clean_lights(plan.get(LIGHTS_KEY)),
            'moves': leveldata.clean_actor_moves(plan.get('actorMoves')),
            'removed': sorted(str(k) for k in plan.get('removedFx') or [])}


def migrate(plan):
    """A plan saved before the lighting section or the moons, brought to them in place
    (lightmigrate, moons.migrate)."""
    from riftlord.build.world import lightmigrate
    from riftlord.build.world import moons
    return moons.migrate(lightmigrate.migrate(plan))


def migrated(plan, level=None):
    """The plan itself when it has `lighting` and `moons`, else a migrated copy (the plan
    is left as it is); `level` stands in for a plan that names none."""
    import copy
    from riftlord.build.world import moons
    if not isinstance(plan, dict) or (PLAN_KEY in plan and moons.PLAN_KEY in plan):
        return plan
    got = copy.deepcopy(plan)
    if level and not got.get('level'):
        got['level'] = level
    return migrate(got)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print(__doc__)
        return 2
    got = state(argv[0])
    print(json.dumps({'suns': got['suns'], 'sections': got['sections']}, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
