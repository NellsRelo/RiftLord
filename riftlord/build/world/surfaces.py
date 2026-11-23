"""A map's surface weathering: snow, ice, sand, moss, rain and stone colour.

The shipped master materials blend weathering onto surfaces by the values of one
material parameter collection, `MPC_Enviro_Level` (13 scalars, 0 by default, and 3
colours). Which master reads which value, from the base-pass pixel shaders of every
material that imports the collection (2026-11-22; the collection's buffer is the one
bound just before the material's own):

    UseSnowCastle    Master_Prop, Master_Structure, Master_Terrain_Rock, Master_SnowTrees,
                     Master_Snow_Mountains, M_Ice_Formations
    UseIceCastle     Master_Prop, Master_Structure, Master_Terrain_Rock
    UseSandCastle, UseMossCastle, UseRainCastle    Master_Prop, Master_Structure
    UseSandRock, UseMossRock, UseRainRock          Master_Terrain_Rock
    UseRainProp      Master_Foliage
    UseRainTrap      Master_Trap, Master_Trap_AlphaMask, Master_Trap_Guardian
    UseRainCharacter Master_Character, Master_Character_Optimized
    ColorSnow        Master_Prop, Master_Structure, Master_Terrain_Rock
    ColorSand        Master_Prop, Master_Structure
    ColorStoneCastle, DesaturateCastleStone        Master_Structure
    UseRainFoliage   none (not offered)

A level sets them from its persistent level's script (`SetScalarParameterValue` /
`SetVectorParameterValue` on the collection, at play). Census of the 33 shipped levels:
50 scalar calls with a constant value; 15 levels set none; ColorStoneCastle (10 levels)
and Scenario_04's DesaturateCastleStone take a value the script works out. Nothing else
in the game sets the collection but the main menu's level sequences.

Plan key: `plan['world']['surfaces']` = {name: value}, a scalar 0..1 or a colour
[r, g, b, a] (linear). The build writes each value:

* one the level's script sets as a constant: that constant, rewritten in place in the
  copied level's script (same size, so no statement or jump target moves);
* any other: the mission's surfaces actor (a bpkit Blueprint built per mission, placed in
  the level copy) sets it at BeginPlay, on every machine (a level's actors begin play on
  each one), where nothing else writes it.

A value the level's script works out is not offered on that level: the script would
write it again after the actor.
"""

import functools
import os
import struct

#: The plan's world key.
PLAN_KEY = 'surfaces'
#: The collection, and the calls that set it.
COLLECTION = 'MPC_Enviro_Level'
COLLECTION_PATH = '/Game/MasterMaterials/MaterialParamCollections/MPC_Enviro_Level'
SET_SCALAR, SET_VECTOR = 'SetScalarParameterValue', 'SetVectorParameterValue'

#: The values offered, in panel order: (name, group, label, kind, collection default).
#: Groups and labels say what reads the value (module notes).
PARAMS = [
    ('UseSnowCastle', 'Snow', 'Amount', 'scalar', 0.0),
    ('ColorSnow', 'Snow', 'Colour', 'colour', [0.9216, 0.9529, 1.0, 1.0]),
    ('UseIceCastle', 'Ice', 'Amount', 'scalar', 0.0),
    ('UseSandCastle', 'Sand', 'Walls and Props', 'scalar', 0.0),
    ('UseSandRock', 'Sand', 'Rock', 'scalar', 0.0),
    ('ColorSand', 'Sand', 'Colour', 'colour', [0.5843, 0.3725, 0.1294, 1.0]),
    ('UseMossCastle', 'Moss', 'Walls and Props', 'scalar', 0.0),
    ('UseMossRock', 'Moss', 'Rock', 'scalar', 0.0),
    ('UseRainCastle', 'Rain', 'Walls and Props', 'scalar', 0.0),
    ('UseRainRock', 'Rain', 'Rock', 'scalar', 0.0),
    ('UseRainProp', 'Rain', 'Foliage', 'scalar', 0.0),
    ('UseRainTrap', 'Rain', 'Traps', 'scalar', 0.0),
    ('UseRainCharacter', 'Rain', 'Characters', 'scalar', 0.0),
    ('ColorStoneCastle', 'Walls', 'Colour', 'colour', [1.0, 1.0, 1.0, 0.0]),
    ('DesaturateCastleStone', 'Walls', 'Desaturation', 'scalar', 0.0),
]
BY_NAME = {p[0]: p for p in PARAMS}
#: Range and step of a scalar in the panel (shipped values lie in 0..1).
RANGE = (0.0, 1.0, 0.05)
#: The surfaces actor: its class `<mod>_<CLASS_SUFFIX>` and its placed export name.
CLASS_SUFFIX = 'Surfaces'
PLACED_NAME = 'RiftLordSurfaces'
#: Where the actor stands (it draws nothing).
IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def _calls(pkg):
    """[(export, found, name, value node or None)] for every SET_SCALAR / SET_VECTOR call on
    COLLECTION in the package's scripts; the value node is the FloatConst of a constant
    scalar, None for anything worked out."""
    from riftlord.build.world import storyvo
    out = []
    for export, names, resolve, found in storyvo._scripts(pkg):
        for top in found[3].nodes:
            for fn in (SET_SCALAR, SET_VECTOR):
                for call in storyvo._calls(top, resolve, fn):
                    args = [v for k, v in call.ops if k == 'expr']
                    if (len(args) < 4 or args[1].name != 'ObjectConst'
                            or resolve(args[1].ops[0][1]) != COLLECTION
                            or args[2].name != 'NameConst'):
                        continue
                    name = names[args[2].ops[0][1]]
                    const = args[3] if fn == SET_SCALAR and args[3].name == 'FloatConst' else None
                    out.append((export, found, name, const))
    return out


def _value(node):
    return next(v for k, v in node.ops if k == 'f32')


def scan(pkg):
    """{'set': {name: value}, 'worked': [name]}: the constants the package's scripts set
    (the first call of each) and the values they work out."""
    got, worked = {}, set()
    for _e, _f, name, node in _calls(pkg):
        if node is None:
            worked.add(name)
        else:
            got.setdefault(name, round(_value(node), 4))
    return {'set': got, 'worked': sorted(worked)}


@functools.lru_cache(maxsize=64)
def _survey(level):
    from riftlord.core import gamefiles
    from riftlord.build.minimap import mapdata
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(*gamefiles.package(mapdata.map_path(level)))
    pkg.load_payloads()
    return scan(pkg)


def survey(level):
    """scan() of the level's shipped persistent level."""
    got = _survey(level)
    return {'set': dict(got['set']), 'worked': list(got['worked'])}


def shown(level):
    """{name: value} of every value the panel offers on the level: its script's constant,
    else the collection's default. A value the script works out is left out."""
    got = survey(level)
    out = {}
    for name, _g, _l, _kind, default in PARAMS:
        if name in got['worked']:
            continue
        out[name] = got['set'].get(name, default)
    return out


def fields():
    """PARAMS for the editor: [{key, group, label, kind, min, max, step, default}]."""
    lo, hi, step = RANGE
    return [{'key': name, 'group': group, 'label': label, 'kind': kind,
             'min': lo, 'max': hi, 'step': step, 'default': default}
            for name, group, label, kind, default in PARAMS]


def clean(raw):
    """A plan's `world['surfaces']`, validated: {name: scalar in RANGE or [r, g, b, a]},
    or None."""
    if not isinstance(raw, dict):
        return None
    lo, hi, _step = RANGE
    out = {}
    for key, value in raw.items():
        spec = BY_NAME.get(key)
        if spec is None or isinstance(value, bool):
            continue
        if spec[3] == 'colour':
            if not isinstance(value, (list, tuple)) or len(value) != 4:
                raise ValueError('%s is not a colour' % key)
            value = [round(min(1.0, max(0.0, float(c))), 4) for c in value]
            if any(c != c for c in value):
                raise ValueError('%s is not a colour' % key)
            out[key] = value
            continue
        value = float(value)
        if value != value:
            raise ValueError('%s is not a number' % key)
        out[key] = round(min(hi, max(lo, value)), 4)
    return out or None


def wanted(plan):
    """{name: value} the plan sets."""
    return clean(((plan or {}).get('world') or {}).get(PLAN_KEY)) or {}


def split(level, plan):
    """({name: value} the level's script sets as a constant, {name: value} the actor sets,
    [name the script works out]) of the plan's values."""
    values = wanted(plan)
    got = survey(level)
    patched = {k: v for k, v in values.items() if k in got['set']}
    worked = sorted(k for k in values if k in got['worked'])
    actor = {k: v for k, v in values.items() if k not in patched and k not in worked}
    return patched, actor, worked


def write(pkg, values):
    """Set the scripts' constant calls of `values`' scalars in place. Returns
    ({name: calls rewritten}, [name the package does not set as a constant])."""
    from riftlord.formats.ue import kismet
    done = {}
    scripts = {}
    for export, found, name, node in _calls(pkg):
        if name not in values or node is None or BY_NAME[name][3] != 'scalar':
            continue
        node.ops = [('f32', float(values[name])) if k == 'f32' else (k, v) for k, v in node.ops]
        scripts[id(export)] = (export, found)
        done[name] = done.get(name, 0) + 1
    for export, (off, mem, size, walker) in scripts.values():
        script = kismet.emit(walker.nodes)
        if len(script) != size:
            raise SystemExit('surface weathering edit changed the script size; not written')
        data = bytes(export['data'])
        export['data'] = (data[:off] + struct.pack('<ii', mem, size) + script
                          + data[off + 8 + size:])
        export['size'] = len(export['data'])
    return done, sorted(k for k in values if k not in done)


def class_name(mod):
    return '%s_%s' % (mod, CLASS_SUFFIX)


def package_path(mod):
    return '/Game/Mods/%s/%s' % (mod, class_name(mod))


def script(values):
    """The surfaces actor's bpkit script: each value set at BeginPlay."""
    lines = ['@override', 'def ReceiveBeginPlay():',
             '    Collection = Asset("MaterialParameterCollection\'%s.%s\'")'
             % (COLLECTION_PATH, COLLECTION)]
    for name, _g, _l, kind, _d in PARAMS:
        if name not in values:
            continue
        v = values[name]
        if kind == 'colour':
            lines.append('    SetVectorParameterValue(self, Collection, "%s", MakeColor(%r, %r, %r, %r))'
                         % ((name,) + tuple(float(c) for c in v)))
        else:
            lines.append('    SetScalarParameterValue(self, Collection, "%s", %r)'
                         % (name, float(v)))
    return '\n'.join(lines) + '\n'


def package(level, mod, plan):
    """{package path: (uasset, uexp)} of the surfaces actor's Blueprint, or {} when the
    level's script sets every value the plan changes."""
    _patched, actor, _worked = split(level, plan)
    if not actor:
        return {}
    from riftlord.missions import missionscript
    clash = [i.get('name') for i in missionscript.pieces(plan) if i.get('name') == CLASS_SUFFIX]
    if clash:
        raise SystemExit('a mission script is named %s, the name of the surfaces actor'
                         % CLASS_SUFFIX)
    from riftlord.core import gamepath
    from riftlord.types import setup
    if setup.unavailable():
        raise SystemExit(setup.unavailable())
    from bpkit import actor as bpactor, widget
    from bpkit.typedb import add_package
    folder = os.path.join(gamepath.scratch('surfaces'), mod)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, CLASS_SUFFIX + '.py'), 'w', encoding='utf-8') as fh:
        fh.write(script(actor))
    path = package_path(mod)
    desc = {'name': class_name(mod), 'path': path, 'script': CLASS_SUFFIX + '.py',
            '_dir': folder}
    widget.declare([desc])
    pkg = bpactor.compile(desc)
    problems = widget.verify(pkg, desc['name'], path)
    if problems:
        raise SystemExit('surfaces actor not built: %s' % '; '.join(problems))
    add_package(widget.db(), pkg, path)
    return {path: pkg.write()}


def apply(builder, plan, report, level=None):
    """Write the plan's surface values into the level being built: the script's constants,
    and the surfaces actor placed for the rest."""
    values = wanted(plan)
    if not values:
        return
    from riftlord.core import buildmessage
    level = level or (plan or {}).get('level')
    patched, actor, worked = split(level, plan)
    done, _rest = write(builder.pkg, patched)
    if actor:
        mod = (plan or {}).get('_modKey')
        if not mod:
            report['skipped'].append(buildmessage.left_out(
                'the surface settings', 'no _modKey in the plan'))
        else:
            from riftlord.missions import missionevents
            missionevents.place_actor(builder, IDENTITY, package_path(mod), class_name(mod),
                                      PLACED_NAME)
    said = [k for k in (list(done) + list(actor))]
    if said:
        report['notes'].append('surfaces: %s' % ', '.join(
            '%s %s' % (BY_NAME[k][1], BY_NAME[k][2]) for k in said))
    if worked:
        report['skipped'].append(buildmessage.left_out(
            'the surface settings %s' % ', '.join('%s %s' % BY_NAME[k][1:3] for k in worked),
            fix='the level works them out as it plays, so Reset them'))


def check_files(files, plan, level=None):
    """[{name, ok, detail}]: with values the actor sets, its Blueprint is in the pak and
    the level places it with its class preloaded."""
    _p, actor, _w = split(level or (plan or {}).get('level'), plan)
    if not actor:
        return []
    from riftlord.formats.ue.uassetio import Package
    from riftlord.missions import missionevents
    mod = (plan or {}).get('_modKey')
    base = 'OMD/Content' + package_path(mod)[len('/Game'):] if mod else None
    ok = bool(base and base + '.uasset' in files and base + '.uexp' in files)
    if ok:
        names = [n for n, _h in Package(files[base + '.uasset']).names]
        ok = (class_name(mod) + '_C' in names and COLLECTION in names
              and all(k in names for k in actor))
    out = [{'name': 'surfaces actor Blueprint in the pak, naming its values', 'ok': ok,
            'detail': base or 'no mod folder'}]
    placed, detail = False, 'the level does not place it'
    for key, data in files.items():
        if not key.endswith('.umap') or key[:-5] + '.uexp' not in files:
            continue
        pkg = Package(data, files[key[:-5] + '.uexp'])
        lnames = [n for n, _h in pkg.names]
        if PLACED_NAME not in lnames:
            continue
        placed, detail = missionevents.placed_ok(pkg, lnames, class_name(mod), PLACED_NAME)
        break
    out.append({'name': 'surfaces actor placed with its class preloaded', 'ok': placed,
                'detail': detail})
    return out
