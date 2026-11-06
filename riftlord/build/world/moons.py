"""A map's moons by engine property: the level's own and the ones a plan adds.

A shipped moon is a StaticMeshActor whose root StaticMeshComponent draws
/Engine/BasicShapes/Plane (100 uu square) with a moon material (MATERIALS), far out in the
sky with its face toward the level (measured on the 11 shipped levels with one, docs
MAP-EDITOR.md "Moons").

Plan data:

* `moons` {'<sublevel tag>|<actor export>': {Property: value}}: the level's own moons (the
  actor's root component). Always stored (empty when nothing is set): a plan without it is
  from before 2026-11-05 (migrate).
* `addedMoons` [{Property: value}]: moons the plan adds, lifted from DONOR; a property the
  entry does not set is the donor's.
* A level moon in `removedFx` is parked (apply.removed_fx).

Properties: RelativeLocation [x, y, z] (uu from the level's origin), RelativeRotation
[pitch, yaw, roll], RelativeScale3D [x, y, z], Material (OverrideMaterials[0], one of
MATERIALS). A moon whose RelativeLocation is set and RelativeRotation is not keeps its turn
relative to its direction from the origin (carried_rotation).

The Map Editor shows Pitch and Yaw (the direction of RelativeLocation), Distance (its
length), Scale (RelativeScale3D, uniform) and Material.

Until 2026-11-05 a sky from another level brought that level's moon (skyfog.lift_extras)
and parked the level's own (skyfog.own_extras); migrate writes that into `addedMoons` and
`removedFx`.

    python3 -m riftlord.build.world.moons Scenario_06      # the level's moons
"""

import copy
import functools
import json
import math
import re
import struct
import sys

#: Plan keys.
PLAN_KEY, ADDED_KEY = 'moons', 'addedMoons'
#: The moon material instances (/Game/Environments/Sky/Mat, children of
#: Master_SkyObjects over Sky_Moon2_df); the first three are the ones shipped levels place.
MATERIALS = ('Moon_01_Inst', 'Moon_Lava_01_Inst', 'Moon_Snow_01_Inst', 'Moon_MainUI_Inst')
MATERIAL = 'Material'
#: Property kinds.
PROPS = {'RelativeLocation': 'vector', 'RelativeRotation': 'rotator',
         'RelativeScale3D': 'vector', MATERIAL: 'material'}
TRANSFORM = ('RelativeLocation', 'RelativeRotation', 'RelativeScale3D')
#: The cooker's defaults for a transform property it does not store.
DEFAULTS = {'RelativeLocation': [0.0, 0.0, 0.0], 'RelativeRotation': [0.0, 0.0, 0.0],
            'RelativeScale3D': [1.0, 1.0, 1.0]}
#: The moon an added moon is lifted from: (level, sublevel tag, actor name). The one a sky
#: from another level brought until 2026-11-05 (skyfog.SKY_HOMES: Night_Sky_Lava_INST1).
DONOR = ('Scenario_12', 'Lighting', 'Plane_Moon2')
#: An added moon's actor name (the name the lifted moon had until 2026-11-05).
ADDED_NAME = 'RiftLordSky_Plane_Moon2'
#: An actor key ('<sublevel tag>|<actor export, 1-based>').
ACTOR_KEY = re.compile(r'^[^|]+\|[1-9][0-9]*$')
#: The farthest Distance: the shipped sky dome's radius (SM_Skybox_Mesh, extent 4096 uu
#: at scale 5000, on 10 of the 11 levels with a moon).
DISTANCE_MAX = 20480000.0
#: Degrees within which two directions are the same.
SAME_ANGLE = 0.01
#: The moon materials' emissive parameters (Master_SkyObjects).
EMISSIVE_COLOUR, EMISSIVE_BRIGHTNESS = 'EM_Color', 'EM_Brightness'

#: The fields the Map Editor shows. Pitch, Yaw and Distance are RelativeLocation's
#: direction (FVector::Rotation) and length; Scale sets RelativeScale3D on every axis.
FIELDS = [
    {'prop': 'Pitch', 'label': 'Pitch', 'kind': 'float', 'min': -90.0, 'max': 90.0,
     'step': 1.0, 'of': 'RelativeLocation'},
    {'prop': 'Yaw', 'label': 'Yaw', 'kind': 'float', 'min': -180.0, 'max': 180.0,
     'step': 1.0, 'of': 'RelativeLocation'},
    {'prop': 'Distance', 'label': 'Distance', 'kind': 'float', 'min': 0.0,
     'max': DISTANCE_MAX, 'step': 1000.0, 'of': 'RelativeLocation'},
    {'prop': 'Scale', 'label': 'Scale', 'kind': 'float', 'min': 0.0, 'max': None,
     'step': 100.0, 'of': 'RelativeScale3D'},
    {'prop': MATERIAL, 'label': 'Material', 'kind': 'enum', 'options': list(MATERIALS)},
]


# -- values --------------------------------------------------------------------------

def f32(value):
    from riftlord.build.world import lighting
    return lighting.f32(value)


def clean_props(raw):
    """One moon's plan values, checked: {Property: value}. Raises ValueError."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for prop, value in raw.items():
        kind = PROPS.get(prop)
        if kind is None:
            raise ValueError('no such moon property: %r' % (prop,))
        if value is None:
            continue
        if kind == 'material':
            if value not in MATERIALS:
                raise ValueError('%s must be one of %s' % (prop, ', '.join(MATERIALS)))
            out[prop] = str(value)
            continue
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ValueError('%s needs 3 numbers' % prop)
        got = [float(v) for v in value]
        if any(not math.isfinite(v) for v in got):
            raise ValueError('%s must be numbers' % prop)
        out[prop] = [f32(v) for v in got]
    return out


def clean(raw):
    """A plan's `moons`, checked: {actor key: {Property: value}}. Raises ValueError."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for key, props in raw.items():
        if not ACTOR_KEY.match(str(key)):
            continue
        got = clean_props(props)
        if got:
            out[str(key)] = got
    return out


def clean_added(raw):
    """A plan's `addedMoons`, checked: [{Property: value}]. Raises ValueError."""
    if not isinstance(raw, list):
        return []
    return [clean_props(item) for item in raw if isinstance(item, dict)]


# -- turning -------------------------------------------------------------------------

def rotation_rows(rot):
    """The X, Y and Z axes of a rotator [pitch, yaw, roll] (Unreal's FRotationMatrix)."""
    p, y, r = (math.radians(float(v)) for v in rot)
    sp, cp, sy, cy, sr, cr = (math.sin(p), math.cos(p), math.sin(y), math.cos(y),
                              math.sin(r), math.cos(r))
    return [[cp * cy, cp * sy, sp],
            [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
            [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp]]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def rows_rotator(rows):
    """The rotator of three axes (Unreal's FMatrix::Rotator)."""
    x, y, z = rows
    pitch = math.degrees(math.atan2(x[2], math.hypot(x[0], x[1])))
    yaw = math.degrees(math.atan2(x[1], x[0]))
    sy = rotation_rows([pitch, yaw, 0.0])[1]
    roll = math.degrees(math.atan2(_dot(z, sy), _dot(y, sy)))
    return [pitch, yaw, roll]


def _unit(v):
    n = math.sqrt(_dot(v, v))
    return [c / n for c in v] if n > 0 else None


def _turn(v, axis, angle):
    """`v` turned by `angle` radians about unit `axis` (Rodrigues)."""
    c, s = math.cos(angle), math.sin(angle)
    cross = [axis[1] * v[2] - axis[2] * v[1], axis[2] * v[0] - axis[0] * v[2],
             axis[0] * v[1] - axis[1] * v[0]]
    k = _dot(axis, v) * (1 - c)
    return [v[i] * c + cross[i] * s + axis[i] * k for i in range(3)]


def direction(location):
    """(pitch, yaw, distance) of a location from the origin (FVector::Rotation, Size)."""
    x, y, z = (float(v) for v in location)
    dist = math.sqrt(x * x + y * y + z * z)
    if dist <= 0:
        return 0.0, 0.0, 0.0
    return (math.degrees(math.asin(max(-1.0, min(1.0, z / dist)))),
            math.degrees(math.atan2(y, x)), dist)


def location_of(pitch, yaw, distance):
    """The location `distance` from the origin in direction (pitch, yaw)."""
    p, y = math.radians(pitch), math.radians(yaw)
    return [distance * math.cos(p) * math.cos(y), distance * math.cos(p) * math.sin(y),
            distance * math.sin(p)]


def carried_rotation(rotation, was, now):
    """`rotation` turned by the least turn that takes direction `was` to `now` (both
    locations from the origin): a moon moved keeps the face it showed the level."""
    a, b = _unit(was), _unit(now)
    if a is None or b is None:
        return list(rotation)
    cos = max(-1.0, min(1.0, _dot(a, b)))
    angle = math.acos(cos)
    if math.degrees(angle) < SAME_ANGLE:
        return list(rotation)
    axis = _unit([a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
                  a[0] * b[1] - a[1] * b[0]])
    if axis is None:
        #: opposite directions: any axis square to them
        axis = _unit([-a[1], a[0], 0.0]) or [1.0, 0.0, 0.0]
    rows = [_turn(r, axis, angle) for r in rotation_rows(rotation)]
    return rows_rotator(rows)


def resolve(base, own):
    """{Property: value} a moon's plan values write over its `base` values (only what
    differs): a set RelativeLocation without a RelativeRotation carries the base turn."""
    own = clean_props(own)
    out = {}
    for prop in (MATERIAL,) + TRANSFORM:
        if prop in own and own[prop] != base.get(prop):
            out[prop] = own[prop]
    if 'RelativeLocation' in out and 'RelativeRotation' not in own:
        turned = [f32(v) for v in carried_rotation(base['RelativeRotation'],
                                                   base['RelativeLocation'],
                                                   out['RelativeLocation'])]
        if turned != base['RelativeRotation']:
            out['RelativeRotation'] = turned
    return out


# -- the level -----------------------------------------------------------------------

def _class_of(pkg, index):
    from riftlord.build.world import lighting
    return lighting._class_of(pkg, index)


def component_values(pkg, comp):
    """{Property: value} of a moon component (1-based export): its transform (defaults
    where the cooker stored none) and material."""
    from riftlord.build import levelbuild
    from riftlord.build.world import lighting
    names = [n for n, _h in pkg.names]
    data = pkg.exports[comp - 1]['data']
    out = {prop: list(DEFAULTS[prop]) for prop in TRANSFORM}
    for name, typ, a, b in lighting.tags(data, names):
        if name in TRANSFORM and typ == 'StructProperty':
            got = lighting.read_value(data, names, typ, a, b)
            if got is not None:
                out[name] = got
        elif name == 'OverrideMaterials' and typ == 'ArrayProperty':
            if struct.unpack_from('<i', data, a + 33)[0] > 0:
                ref = struct.unpack_from('<i', data, a + 37)[0]
                out[MATERIAL] = levelbuild.resolve(pkg, ref)[1]
    return out


def survey(pkg):
    """[(actor, component)] (1-based exports) of the moons in one package: actors whose
    root StaticMeshComponent's first material is one of MATERIALS."""
    from riftlord.build.world import lighting
    names = [n for n, _h in pkg.names]
    out = []
    if not set(names) & set(MATERIALS):
        return out
    for comp in range(1, len(pkg.exports) + 1):
        try:
            if _class_of(pkg, comp) != 'StaticMeshComponent':
                continue
        except Exception:                            # noqa: BLE001
            continue
        if component_values(pkg, comp).get(MATERIAL) not in MATERIALS:
            continue
        actor = lighting._outer(pkg, comp)
        if actor <= 0:
            continue
        data = pkg.exports[actor - 1]['data']
        root = None
        for name, typ, a, _b in lighting.tags(data, names):
            if name == 'RootComponent' and typ == 'ObjectProperty':
                root = struct.unpack_from('<i', data, a + 25)[0]
        if root == comp:
            out.append((actor, comp))
    return out


def level_moons(level, plan=None):
    """[{key, name, tag, actor, component, values}] of the level's own moons (none on a
    level from scratch, whose light rig has none)."""
    from riftlord.build.world import lighting
    out = []
    for tag, pkg in lighting.level_packages(level, plan):
        for actor, comp in survey(pkg):
            out.append({'key': '%s|%d' % (tag, actor), 'name': lighting._name_of(pkg, actor),
                        'tag': tag, 'actor': actor, 'component': comp,
                        'values': component_values(pkg, comp)})
    return out


@functools.lru_cache(maxsize=1)
def _donor():
    """(package, actor export 1-based, component export, values) of DONOR."""
    from riftlord.build.world import lighting
    level, tag, name = DONOR
    for got_tag, pkg in lighting.level_packages(level):
        if got_tag != tag:
            continue
        for actor, comp in survey(pkg):
            if lighting._name_of(pkg, actor) == name:
                return pkg, actor, comp, component_values(pkg, comp)
    raise SystemExit('the moon an added moon is lifted from (%s %s %s) was not found'
                     % DONOR)


def donor_values():
    """The values an added moon has where its entry sets none."""
    return copy.deepcopy(_donor()[3])


@functools.lru_cache(maxsize=1)
def looks():
    """{material: {'texture': its texture's game path, 'colour': EM_Color times
    EM_Brightness, linear}}: what the Map Editor's view draws a moon with."""
    from riftlord.build.lighting import lightscene
    from riftlord.mapeditor import leveldata
    out = {}
    for material in MATERIALS:
        try:
            params = lightscene._material_parameters(material)
        except Exception:                            # noqa: BLE001
            params = {}
        colour = params.get(EMISSIVE_COLOUR) or (1.0, 1.0, 1.0, 1.0)
        bright = float(params.get(EMISSIVE_BRIGHTNESS) or 1.0)
        out[material] = {'texture': leveldata.look_texture(material),
                         'colour': [round(float(c) * bright, 4) for c in colour[:3]]}
    return out


def state(level, plan=None):
    """What the Map Editor shows: {'moons': [{key, name, values}], 'added': the values of
    an added moon, 'fields', 'materials', 'looks'}."""
    moons = [{'key': m['key'], 'name': m['name'], 'values': m['values']}
             for m in level_moons(level, plan)]
    try:
        added = donor_values()
    except (Exception, SystemExit):                  # noqa: BLE001
        added = None
    return {'moons': moons, 'added': added, 'fields': FIELDS, 'materials': list(MATERIALS),
            'looks': looks()}


@functools.lru_cache(maxsize=16)
def _state_cached(level, is_scratch):
    plan = {'scratch': {'name': 'x'}} if is_scratch else None
    return state(level, plan)


def shown(level, is_scratch=False):
    """state() cached per level (and the light rig)."""
    return copy.deepcopy(_state_cached(level, bool(is_scratch)))


# -- the build -----------------------------------------------------------------------

def package_edits(level, plan):
    """{sublevel tag: {component export: {Property: value}}} the plan writes into the
    level's own moons (a removed one is parked instead)."""
    plan = plan or {}
    want = clean(plan.get(PLAN_KEY))
    if not want:
        return {}
    gone = {str(k) for k in plan.get('removedFx') or []}
    out = {}
    for moon in level_moons(level, plan):
        key = moon['key']
        if key not in want or key in gone:
            continue
        props = resolve(moon['values'], want[key])
        if props:
            out.setdefault(moon['tag'], {})[moon['component']] = props
    return out


def _write(builder, actor, comp, props, report):
    """Write resolved moon values into a moon actor of builder.pkg."""
    from riftlord.build import apply
    from riftlord.build.world import lavafx
    from riftlord.mapeditor import geomedits
    moved = {prop: props[prop] for prop in TRANSFORM if prop in props}
    if moved:
        apply.place_whole(builder, actor, location=moved.get('RelativeLocation'),
                          rotation=moved.get('RelativeRotation'),
                          scale=moved.get('RelativeScale3D'))
        geomedits.forget_visibility(builder, actor)
    if MATERIAL in props:
        ref = lavafx._material_import(builder, props[MATERIAL])
        if ref is None:
            report.setdefault('skipped', []).append('moon: no asset for %s' % props[MATERIAL])
        else:
            builder.set_object_array(comp, 'OverrideMaterials', [ref])


def write(builder, edits, report):
    """Write one package's moon edits ({component export: values}, as package_edits gives
    them) into builder.pkg. Returns the count."""
    from riftlord.build.world import lighting
    done = 0
    for comp, props in sorted((edits or {}).items()):
        _write(builder, lighting._outer(builder.pkg, int(comp)), int(comp), props, report)
        done += 1
    if done:
        report.setdefault('notes', []).append('moons: %d set' % done)
    return done


def place_added(builder, plan, free_name, report):
    """Lift one DONOR moon per `addedMoons` entry into builder.pkg (the persistent level)
    and write its values. Returns the count."""
    from riftlord.mapeditor import fabemit
    from riftlord.mapeditor import geomedits
    entries = clean_added((plan or {}).get(ADDED_KEY))
    if not entries:
        return 0
    pkg, actor, _comp, base = _donor()
    done = 0
    for own in entries:
        at = builder.lift_actor(pkg, actor - 1, rename=free_name(ADDED_NAME))
        root = builder._root_component(at)
        #: Movable: a lifted mesh keeps its donor's MapBuildDataId (see lavafx.apply)
        if root:
            builder.set_property(root, 'Mobility', 'ByteProperty',
                                 struct.pack('<ii', builder.pkg.add_name(
                                     fabemit.PIECE_MOBILITY), 0),
                                 struct_name='EComponentMobility')
        geomedits.forget_visibility(builder, at)
        props = resolve(base, own)
        if props:
            _write(builder, at, root, props, report)
        done += 1
    if done:
        report.setdefault('notes', []).append('%d added moon(s)' % done)
    return done


# -- plans saved before 2026-11-05 -----------------------------------------------------

def _differs(values, base):
    """`values` less what equals `base`."""
    return {k: v for k, v in values.items() if base.get(k) != v}


def legacy(plan):
    """(added entries, removed keys) the sky of a plan saved before 2026-11-05 brought:
    the sky's home level's moons (skyfog.lift_extras) and the level's own moons parked
    (skyfog.own_extras)."""
    from riftlord.build.world import lighting
    from riftlord.build.world import skyfog
    from riftlord.mapeditor import leveldata
    from riftlord.mapeditor import scratch
    plan = plan or {}
    level = plan.get('level')
    sky = (plan.get('look') or {}).get('sky')
    home = skyfog.sky_home(plan)
    if not level or not sky or not home or home == level:
        return [], []
    base = donor_values()
    added = []
    for tag, pkg in lighting.level_packages(home):
        for _actor, comp in survey(pkg):
            added.append(_differs(component_values(pkg, comp), base))
    removed = []
    if not scratch.is_scratch(plan) and \
            sky != (leveldata.level(level).get('sky') or {}).get('material'):
        removed = [m['key'] for m in level_moons(level, plan)]
    return added, removed


def migrate(plan):
    """A plan without `moons` given the moons its sky used to bring, in place. A plan with
    `moons` is left as it is."""
    if not isinstance(plan, dict) or PLAN_KEY in plan:
        return plan
    try:
        added, removed = legacy(plan)
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        added, removed = [], []
        plan.setdefault('lightingNotes', []).append(
            'the old moon could not be read: %s: %s' % (type(exc).__name__, exc))
    plan[PLAN_KEY] = {}
    if added:
        plan[ADDED_KEY] = list(plan.get(ADDED_KEY) or []) + added
    if removed:
        plan['removedFx'] = sorted(set(plan.get('removedFx') or []) | set(removed))
    return plan


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print(__doc__)
        return 2
    got = state(argv[0])
    print(json.dumps({'moons': got['moons'], 'added': got['added']}, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
