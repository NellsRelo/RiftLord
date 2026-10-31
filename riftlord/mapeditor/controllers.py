"""Level controller settings and the shipped actors placed with settings of their own.

Research: workshop/research/LEVEL-CONTROLLERS-AND-PATTERNS.md section 1 (raw census in
/mnt/2tb_pro_a/tmp/levelpatterns/q1_*). What this module holds:

* **Wind** (`WindDirectionalSourceComponent` Speed, Strength, Min/Max Gust), on 17
  levels: Map Settings fields through riftlord.levelfx (WIND_FIELDS).
* **Wall Plate** on wall torches: a plateless placement has no `WallBaseMesh` and
  `Metal_PostBase_B` components and stores both properties as null (418 plated, 100
  bare `BP_WallTorch_Normal_C`). Plan: `wallPlates` {'<sublevel>|<actor export>': bool}
  for the level's own torches, `wallPlate` on an added torch.
* **Ambience**: an added `AmbientSound` carries `cue`, and is lifted from a shipped
  placement of that cue, so it arrives with that placement's own settings.
* **Lightning**: an added `BP_LightningBolt_RainLevels_C` carries `retrigger` (min, max
  seconds) and `glow` (linear colour).
* Kill volume flags live in riftlord.volumes; environmental traps in riftlord.envtraps.

The donors and choices come from a survey of every shipped level:

    python3 -m riftlord.controllers --survey    # rebuild controller-donors.json
    python3 -m riftlord.controllers --show      # what it found
"""

import argparse
import functools
import json
import os
import struct
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: Where the survey is kept, and its format (a file of another version is ignored).
OUT = gamepath.data('controller-donors.json')
VERSION = 1

# -- wind --------------------------------------------------------------------------------

#: The wind component's class, the strongest shipped value of any of its four settings
#: (Scenario_15: Speed 22, q1_report.txt), and the editor's top end: twice that.
WIND_CLASS = 'WindDirectionalSourceComponent'
WIND_SHIPPED_MAX = 22.0
WIND_MAX = 2 * WIND_SHIPPED_MAX
WIND_STEP = 0.5
#: Map Settings fields (riftlord.levelfx SCENE_FIELDS). `default` is the class default
#: (UWindDirectionalSourceComponent's constructor, WindDirectionalSource.cpp:145-148),
#: written when a level stores the component but not that setting: Speed is stored on
#: all 17 wind levels, Strength on 14, Min Gust on 11, Max Gust on 13.
WIND_FIELDS = {
    'windSpeed': {'cls': WIND_CLASS, 'prop': 'Speed', 'kind': 'float', 'group': 'Wind',
                  'label': 'Speed', 'range': (0.0, WIND_MAX, WIND_STEP), 'default': 0.1},
    'windStrength': {'cls': WIND_CLASS, 'prop': 'Strength', 'kind': 'float',
                     'group': 'Wind', 'label': 'Strength',
                     'range': (0.0, WIND_MAX, WIND_STEP), 'default': 0.1},
    'windMinGust': {'cls': WIND_CLASS, 'prop': 'MinGustAmount', 'kind': 'float',
                    'group': 'Wind', 'label': 'Min Gust Amount',
                    'range': (0.0, WIND_MAX, WIND_STEP), 'default': 0.1},
    'windMaxGust': {'cls': WIND_CLASS, 'prop': 'MaxGustAmount', 'kind': 'float',
                    'group': 'Wind', 'label': 'Max Gust Amount',
                    'range': (0.0, WIND_MAX, WIND_STEP), 'default': 0.2},
}

# -- what the survey reads -------------------------------------------------------------

#: Wall torch classes whose placements turn the stone wall plate on or off (q1b_report.txt:
#: every class with a `WallBaseMesh` component).
WALL_PLATE_CLASSES = ('BP_WallTorch_Normal_C', 'BP_WallTorch_Normal_WS_C',
                      'BP_WallTorch_LargeRadius_C', 'BP_WallTorch_SmallRadius_C',
                      'BP_WallTorch_SmallRadius_WS_C', 'BP_WallTorch_Rain_C')
#: The two components a plated torch has and a bare one lacks, and the actor properties
#: that name them.
PLATE_COMPONENTS = ('WallBaseMesh', 'Metal_PostBase_B')
#: The torch's `WallBase` (enum `LightWallBase`) as plated and bare placements store it.
WALL_BASE_ENUM = 'LightWallBase'
WALL_BASE = {True: 'LightWallBase::NewEnumerator1', False: 'LightWallBase::NewEnumerator0'}

AMBIENT_CLASS = 'AmbientSound'
LIGHTNING_CLASS = 'BP_LightningBolt_RainLevels_C'
#: Environmental trap classes measured for riftlord.envtraps.
ENV_TRAP_CLASSES = ('BP_Cauldron_C', 'BP_FallingBoomBarrel_C', 'BP_GrinderPit_3X_C',
                    'BP_GrinderPit_4X_C', 'BP_Grinders_3X_C')
#: The cauldron's pour effect, by which its drop length is chosen (q1_report.txt).
CAULDRON_POUR = 'Low End Cauldron Pour Template'

SURVEY_CLASSES = (WALL_PLATE_CLASSES + (AMBIENT_CLASS, LIGHTNING_CLASS)
                  + ENV_TRAP_CLASSES)

#: New Object card names (leveldata.placeable_actors), and previews: the lightning's only
#: mesh is its 10,000 uu ambient shell, so its card shows a dot.
ROW_LABELS = {AMBIENT_CLASS: 'Ambient Sound', LIGHTNING_CLASS: 'Lightning',
              'BP_Cauldron_C': 'Cauldron', 'BP_FallingBoomBarrel_C': 'Falling Boom Barrel',
              'BP_GrinderPit_3X_C': 'Grinder Pit 3x', 'BP_GrinderPit_4X_C': 'Grinder Pit 4x',
              'BP_Grinders_3X_C': 'Grinders 3x'}
ROW_THUMBS = {LIGHTNING_CLASS: ''}


def _level_packages(level):
    """Every map package in a level's folder, persistent level first."""
    from riftlord.build.navigation import navpatch
    base = navpatch.map_path(level)
    folder = base.rsplit('/', 1)[0]
    index = navpatch._index()
    persistent = set(navpatch.maps(include_mods=False).values())
    out = [base]
    for key in sorted(index):
        if key.startswith(folder + '/') and key.endswith('.umap'):
            path = key[:-5]
            if path not in out and path not in persistent:
                out.append(path)
    return out


def _tag_of(level, path):
    """The leveldata sublevel tag of a package ('persistent', 'Lighting', 'Audio', ...)."""
    from riftlord.build.navigation import navpatch
    base = navpatch.map_path(level)
    if path == base:
        return 'persistent'
    tail = path.rsplit('/', 1)[-1]
    head = base.rsplit('/', 1)[-1]
    return tail[len(head) + 1:] if tail.startswith(head + '_') else tail


def _import_path(info, index):
    """'/Game/.../Package.Object' of a negative package index, or its bare name."""
    imports = info.get('imports') or []
    i = -index - 1
    if not 0 <= i < len(imports):
        return None
    name = imports[i]['name']
    outer = imports[i].get('outer') or 0
    if outer < 0 and 0 <= -outer - 1 < len(imports):
        return '%s.%s' % (imports[-outer - 1]['name'], name)
    return name


def _props(info, uexp, hdr, export):
    from riftlord.onslaught import onslaught
    return {name: (kind, raw) for name, kind, raw in
            onslaught.read_props(info, uexp, hdr, export)}


def _enum_text(info, raw):
    names = info['names']
    i = struct.unpack_from('<i', raw, 0)[0] if len(raw) >= 4 else -1
    return names[i] if 0 <= i < len(names) else None


def _vector(raw, n=3):
    return [round(v, 4) for v in struct.unpack_from('<%df' % n, raw, 0)] \
        if len(raw) >= 4 * n else None


def _survey_package(level, path, out, log):
    from riftlord.formats.omd3 import omd3level
    try:
        info, uexp, hdr = omd3level._load(path)
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        log('%s: %s' % (path, exc))
        return
    exports = info['exports']
    kids = {}
    for j, e in enumerate(exports):
        kids.setdefault(e.get('outer'), []).append(j)
    tag = _tag_of(level, path)
    for j, e in enumerate(exports):
        cls = str(e.get('cls') or '')
        if cls not in SURVEY_CLASSES or e.get('outername') != 'PersistentLevel':
            continue
        props = _props(info, uexp, hdr, e)
        comps = {}
        for k in kids.get(j + 1, []):
            comps[str(exports[k]['name'])] = (k, str(exports[k].get('cls') or ''),
                                             _props(info, uexp, hdr, exports[k]))
        rec = {'level': level, 'package': path, 'tag': tag, 'export': j,
               'name': str(e['name'])}
        root = props.get('RootComponent')
        if root and len(root[1]) >= 4:
            ri = struct.unpack_from('<i', root[1], 0)[0]
            if 0 < ri <= len(exports):
                rp = comps.get(str(exports[ri - 1]['name']), (0, '', {}))[2]
                for field in ('RelativeLocation', 'RelativeRotation', 'RelativeScale3D'):
                    if field in rp:
                        rec[field] = _vector(rp[field][1])
        if cls in WALL_PLATE_CLASSES:
            rec['plate'] = all(c in comps for c in PLATE_COMPONENTS)
            if 'WallBase' in props:
                rec['wallBase'] = _enum_text(info, props['WallBase'][1])
        elif cls == AMBIENT_CLASS:
            audio = next((c for c in comps.values() if c[1] == 'AudioComponent'), None)
            cprops = audio[2] if audio else {}
            if 'Sound' in cprops and len(cprops['Sound'][1]) >= 4:
                rec['sound'] = _import_path(info, struct.unpack_from('<i', cprops['Sound'][1])[0])
            if 'bAutoActivate' in cprops:
                rec['auto'] = bool(cprops['bAutoActivate'][1])
            for field in ('VolumeMultiplier', 'PitchMultiplier'):
                if field in cprops and len(cprops[field][1]) == 4:
                    rec[field] = round(struct.unpack_from('<f', cprops[field][1])[0], 4)
            rec['overrides'] = sorted(k for k in cprops
                                      if k not in ('RelativeLocation', 'RelativeRotation',
                                                   'RelativeScale3D', 'Sound'))
        elif cls == LIGHTNING_CLASS:
            for field, n in (('RetriggerTime', 2), ('GlowColor', 4), ('LineColor', 4)):
                if field in props:
                    rec[field] = _vector(props[field][1], n)
            for field in ('LightIntensity',):
                if field in props and len(props[field][1]) == 4:
                    rec[field] = round(struct.unpack_from('<f', props[field][1])[0], 2)
        elif cls == 'BP_Cauldron_C':
            if CAULDRON_POUR in props and len(props[CAULDRON_POUR][1]) >= 4:
                rec['pour'] = _import_path(info, struct.unpack_from(
                    '<i', props[CAULDRON_POUR][1])[0])
            for comp, fields in (('TriggerBox', ('BoxExtent', 'RelativeLocation')),
                                 ('P_CauldronFloor', ('RelativeLocation',))):
                cprops = comps.get(comp, (0, '', {}))[2]
                for field in fields:
                    if field in cprops:
                        rec['%s.%s' % (comp, field)] = _vector(cprops[field][1])
        out.setdefault(cls, []).append(rec)


def survey(levels=None, log=print):
    """{class: [placement]} of every SURVEY_CLASSES actor in every shipped level."""
    from riftlord.build.navigation import navpatch
    names = levels or sorted(k for k in navpatch.maps(include_mods=False)
                             if k.startswith('Scenario'))
    found = {}
    for level in names:
        for path in _level_packages(level):
            _survey_package(level, path, found, log)
        log('%s: %d so far' % (level, sum(len(v) for v in found.values())))
    return found


def summarise(found):
    """The donors and choices the editor and the build use, from `survey`'s placements."""
    out = {'version': VERSION, 'torches': {}, 'ambience': {}, 'lightning': [],
           'envtraps': {}}
    for cls in WALL_PLATE_CLASSES:
        rows = found.get(cls) or []
        plated = [r for r in rows if r.get('plate')]
        bare = [r for r in rows if not r.get('plate')]
        out['torches'][cls] = {
            'plated': len(plated), 'bare': len(bare),
            'platedDonor': [plated[0]['package'], plated[0]['export']] if plated else None,
            'bareDonor': [bare[0]['package'], bare[0]['export']] if bare else None}
    cues = {}
    for r in found.get(AMBIENT_CLASS) or []:
        if not r.get('sound'):
            continue
        cues.setdefault(r['sound'], []).append(r)
    for sound, rows in sorted(cues.items()):
        #: a placement that plays on its own, with the fewest overrides, is the donor
        rows = sorted(rows, key=lambda r: (r.get('auto') is False, len(r['overrides'])))
        name = sound.rsplit('.', 1)[-1]
        out['ambience'][name] = {
            'sound': sound, 'count': len(rows),
            'levels': sorted({r['level'] for r in rows}),
            'autoActivate': sum(1 for r in rows if r.get('auto') is not False),
            'donor': [rows[0]['package'], rows[0]['export']]}
    out['lightning'] = found.get(LIGHTNING_CLASS) or []
    for cls in ENV_TRAP_CLASSES:
        out['envtraps'][cls] = found.get(cls) or []
    return out


def write(summary, path=OUT):
    with open(path, 'w') as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)


@functools.lru_cache(maxsize=1)
def load(path=OUT):
    """The survey summary, or {} when the file is missing or of another version."""
    try:
        with open(path) as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return {}
    return got if got.get('version') == VERSION else {}


# -- donors -------------------------------------------------------------------------------

@functools.lru_cache(maxsize=32)
def _package(path):
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(*gamefiles.package(path))
    pkg.load_payloads()
    return pkg


def package_donor(path, export):
    """(package, zero-based export index) of a surveyed placement, or None."""
    try:
        return _package(path), int(export)
    except Exception:                                # noqa: BLE001
        return None


#: Plan keys an added item may carry, by class (save_plan keeps them: clean_item).
CUE_KEY, PLATE_KEY, RETRIGGER_KEY, GLOW_KEY = 'cue', 'wallPlate', 'retrigger', 'glow'
#: Where the plan keeps the level's own torches' plates: {'<sublevel>|<export>': bool}.
PLATES_KEY = 'wallPlates'
#: Retrigger Time's limits (s): shipped bolts all use (5, 8); up to ten times the longer.
RETRIGGER_MAX = 10 * 8.0
#: Glow Color's limit per channel: shipped bolts reach 5.0 (GlowColor (0.5, 2, 5, 1)),
#: and twice that is offered.
GLOW_MAX = 2 * 5.0


def default_cue():
    """The cue a new ambience emitter plays: the most-placed shipped one."""
    cues = load().get('ambience') or {}
    return max(cues, key=lambda k: cues[k]['count']) if cues else None


def default_plate(cls):
    """Whether a new torch of `cls` has its wall plate: as most shipped placements do."""
    rec = (load().get('torches') or {}).get(cls) or {}
    return rec.get('plated', 1) >= rec.get('bare', 0)


def lightning_donor_values():
    """{retrigger, glow, scale} of the catalogue's lightning donor placement."""
    from riftlord.formats.omd3 import omd3actors
    info = omd3actors.load().get(LIGHTNING_CLASS) or {}
    path = (info.get('level') or '').replace('../../../OMD/Content', '/Game')
    rows = load().get('lightning') or []
    rec = next((r for r in rows if r['package'] == path and r['export'] == info.get('export')),
               rows[0] if rows else {})
    return {'retrigger': rec.get('RetriggerTime') or [5.0, 8.0],
            'glow': rec.get('GlowColor') or [1.0, 1.0, 1.0, 1.0],
            'scale': rec.get('RelativeScale3D') or [1.0, 1.0, 1.0]}


def donor(item):
    """(package, export) to lift an added item from, or None for the class's catalogue
    donor (apply.donor_for)."""
    cls = item.get('name')
    if cls == AMBIENT_CLASS:
        rec = (load().get('ambience') or {}).get(item.get(CUE_KEY) or default_cue())
        return package_donor(*rec['donor']) if rec else None
    if cls in WALL_PLATE_CLASSES:
        rec = (load().get('torches') or {}).get(cls) or {}
        want = item.get(PLATE_KEY, default_plate(cls))
        got = rec.get('platedDonor' if want else 'bareDonor')
        return package_donor(*got) if got else None
    from riftlord.mapeditor import envtraps
    return envtraps.donor(item)


def _child(builder, actor, name):
    from riftlord.build import levelbuild
    pkg = builder.pkg
    for i, e in enumerate(pkg.exports):
        if struct.unpack_from('<i', e['meta'], levelbuild.OUTER_AT)[0] == actor and \
                levelbuild._name_of(pkg, i) == name:
            return i + 1
    return None


def _class_name(pkg, index):
    from riftlord.build import levelbuild
    return levelbuild.resolve(pkg, struct.unpack_from('<i', pkg.exports[index - 1]['meta'],
                                                      levelbuild.CLASS_AT)[0])[1]


def after_add(builder, index, item, report):
    """Settings an added item carries beyond its donor, written on the lifted actor."""
    cls = item.get('name')
    if cls == AMBIENT_CLASS:
        #: an added emitter plays from the start: bAutoActivate back to its default (true)
        for i, e in enumerate(builder.pkg.exports):
            if _class_name(builder.pkg, i + 1) == 'AudioComponent' and \
                    struct.unpack_from('<i', e['meta'], 12)[0] == index:
                builder.clear_property(i + 1, 'bAutoActivate')
        report['notes'].append('ambience emitter %s placed' % (item.get(CUE_KEY) or default_cue()))
    elif cls == LIGHTNING_CLASS:
        if item.get(RETRIGGER_KEY):
            builder.set_property(index, 'RetriggerTime', 'StructProperty',
                                 struct.pack('<2f', *item[RETRIGGER_KEY]), 'Vector2D')
        if item.get(GLOW_KEY):
            builder.set_property(index, 'GlowColor', 'StructProperty',
                                 struct.pack('<4f', *item[GLOW_KEY]), 'LinearColor')
        #: the plan's scale is relative to the shipped bolt's (11.16 on its donor)
        root = builder._root_component(index)
        if root:
            from riftlord.build import apply as applier
            _loc, _rot, scale = applier.transform_of(item['matrix'])
            shipped = lightning_donor_values()['scale']
            builder.set_vector(root, 'RelativeScale3D',
                               [a * b for a, b in zip(scale, shipped)])
    elif cls in WALL_PLATE_CLASSES and PLATE_KEY in item:
        if has_plate(builder, index) != bool(item[PLATE_KEY]):
            set_wall_plate(builder, index, bool(item[PLATE_KEY]), report)


# -- wall plates ------------------------------------------------------------------------

def _object_value(builder, index, name):
    """An ObjectProperty's package index on one export, or None when not stored."""
    data = builder.pkg.exports[index - 1]['data']
    span = builder._tag_span(data, name)
    return struct.unpack_from('<i', data, span[2])[0] if span else None


def has_plate(builder, actor):
    """Whether a placed torch shows its wall plate: the actor names a WallBaseMesh
    component, and that component has a mesh (a taken-away plate's has none)."""
    comp = _object_value(builder, actor, PLATE_COMPONENTS[0])
    if not comp or comp < 0:
        return False
    return _object_value(builder, comp, 'StaticMesh') != 0


def set_wall_plate(builder, actor, on, report):
    """Give a placed torch its wall plate, or take it away, as shipped placements store
    each: a bare one has neither component and both properties null; WallBase follows."""
    cls = _class_name(builder.pkg, actor)
    if cls not in WALL_PLATE_CLASSES:
        report['skipped'].append('export %d is a %s, not a wall torch' % (actor, cls))
        return False
    if has_plate(builder, actor) == on:
        return False
    if on:
        if not _graft_plate(builder, actor, cls, report):
            return False
    else:
        comps = [_child(builder, actor, name) for name in PLATE_COMPONENTS]
        for name, comp in zip(PLATE_COMPONENTS, comps):
            builder.set_object(actor, name, 0)
            #: the export stays (removing one renumbers the package): it draws nothing
            #: and has no collision without a mesh
            builder.set_object(comp, 'StaticMesh', 0)
        made = builder.object_array(actor, 'BlueprintCreatedComponents')
        if made:
            builder.set_object_array(actor, 'BlueprintCreatedComponents',
                                     [c for c in made if c not in comps])
    builder.set_property(actor, 'WallBase', 'EnumProperty',
                         struct.pack('<ii', builder.pkg.add_name(WALL_BASE[on]), 0),
                         WALL_BASE_ENUM)
    return True


def _graft_plate(builder, actor, cls, report):
    """Copy the plate components of a plated shipped placement of `cls` onto `actor`."""
    from riftlord.build import levelbuild
    from riftlord.build import apply as applier
    from riftlord.mapeditor import geomedits
    rec = (load().get('torches') or {}).get(cls) or {}
    got = package_donor(*rec['platedDonor']) if rec.get('platedDonor') else None
    if not got:
        report['skipped'].append('no plated %s to copy a wall plate from' % cls)
        return False
    src, j = got
    root = builder._root_component(actor)
    names = [n for n, _h in src.names]
    data = src.exports[j]['data']
    from riftlord.formats.omd3 import meshcat
    top = meshcat._walk(data, names, 0, len(data))
    src_root = struct.unpack_from('<i', data, top['RootComponent'][1])[0] \
        if 'RootComponent' in top else 0
    if not root or src_root <= 0:
        report['skipped'].append('%s has no root component to hang a wall plate on' % cls)
        return False
    wanted = [k for k, e in enumerate(src.exports)
              if struct.unpack_from('<i', e['meta'], levelbuild.OUTER_AT)[0] == j + 1
              and levelbuild._name_of(src, k) in PLATE_COMPONENTS]
    mapping = {j: actor, src_root - 1: root}
    builder._missed = []
    for k in wanted:
        builder.pkg.exports.append({'meta': bytes(src.exports[k]['meta']),
                                    'data': bytes(src.exports[k]['data'] or b''),
                                    'size': 0, 'offset': 0})
        mapping[k] = len(builder.pkg.exports)
    for k in wanted:
        builder._fixup(src, k, mapping[k], mapping)
    if builder._missed:
        raise SystemExit('wall plate copy lost %s' % ', '.join(n for _i, n in builder._missed))
    made = builder.object_array(actor, 'BlueprintCreatedComponents')
    for k in wanted:
        name = levelbuild._name_of(src, k)
        builder.set_object(actor, name, mapping[k])
        applier.add_preload(builder, actor, applier.CREATE_BEFORE_SERIALIZE, mapping[k])
        geomedits.forget_visibility(builder, mapping[k])
        made.append(mapping[k])
    builder.set_object_array(actor, 'BlueprintCreatedComponents', made)
    return True


def clean_plates(raw):
    """The plan's `wallPlates`, validated: {'<sublevel>|<actor export>': bool}."""
    from riftlord.mapeditor import leveldata
    out = {}
    for key, on in (raw or {}).items() if isinstance(raw, dict) else ():
        if leveldata.ACTOR_KEY.match(str(key)) and isinstance(on, bool):
            out[str(key)] = on
    return out


def plate_edits(plan):
    """{sublevel tag: {actor export: bool}} of the plan's `wallPlates`."""
    out = {}
    for key, on in clean_plates((plan or {}).get(PLATES_KEY)).items():
        tag, _bar, export = key.rpartition('|')
        out.setdefault(tag, {})[int(export)] = on
    return out


def apply_package(builder, edits, report):
    """The controller edits riftlord.apply.sublevel_edits hands one package."""
    done = 0
    for actor, on in sorted(((edits or {}).get(PLATES_KEY) or {}).items()):
        if actor > len(builder.pkg.exports):
            report['skipped'].append('torch export %d is not in this package' % actor)
            continue
        if set_wall_plate(builder, actor, on, report):
            done += 1
    if done:
        report['notes'].append('%d wall plate(s) changed' % done)
    return done


# -- plan items -----------------------------------------------------------------------------

def _floats(raw, n, lo, hi, label):
    if not isinstance(raw, (list, tuple)) or len(raw) != n:
        raise ValueError('%s needs %d numbers' % (label, n))
    got = [round(float(v), 4) for v in raw]
    if any(not lo <= v <= hi for v in got):
        raise ValueError('%s must be between %g and %g' % (label, lo, hi))
    return got


def clean_item(item, raw):
    """Copy an added item's own settings from `raw` into the saved `item`, validated."""
    cls = item.get('name')
    if cls == AMBIENT_CLASS and raw.get(CUE_KEY):
        if raw[CUE_KEY] not in (load().get('ambience') or {}):
            raise ValueError('no such ambience cue: %r' % (raw[CUE_KEY],))
        item[CUE_KEY] = str(raw[CUE_KEY])
    elif cls in WALL_PLATE_CLASSES and isinstance(raw.get(PLATE_KEY), bool):
        item[PLATE_KEY] = raw[PLATE_KEY]
    elif cls == LIGHTNING_CLASS:
        if raw.get(RETRIGGER_KEY) is not None:
            got = _floats(raw[RETRIGGER_KEY], 2, 0.0, RETRIGGER_MAX, 'Retrigger Time')
            item[RETRIGGER_KEY] = sorted(got)
        if raw.get(GLOW_KEY) is not None:
            item[GLOW_KEY] = _floats(raw[GLOW_KEY], 4, 0.0, GLOW_MAX, 'Glow Color')
    else:
        from riftlord.mapeditor import envtraps
        envtraps.clean(item, raw)
        #: a treasure chest's group and rewards (riftlord/kitactors.py)
        from riftlord.mapeditor import kitactors
        kitactors.clean(item, raw)
    return item


# -- the editor's view --------------------------------------------------------------------

def _label(cue):
    words = cue
    for prefix in ('AmbEmitter_', 'AmbEmit_', 'AmbBed_'):
        if words.startswith(prefix):
            words = words[len(prefix):]
    for suffix in ('_LP_SC', '_LP', '_SC'):
        if words.endswith(suffix):
            words = words[:-len(suffix)]
    return words.replace('_', ' ')


def plate_meshes(cls):
    """The meshes of a torch class's wall plate components, as the editor draws them."""
    from riftlord.formats.omd3 import omd3actors
    from riftlord.mapeditor import kitdonors
    parts = kitdonors.parts(cls) or omd3actors.part_index().get(cls) or []
    return sorted({p['mesh'] for p in parts if p.get('component') in PLATE_COMPONENTS})


@functools.lru_cache(maxsize=1)
def choices():
    """GET /api/controllers: what the Map Editor offers for controller settings."""
    from riftlord.mapeditor import envtraps
    got = load()
    cues = got.get('ambience') or {}
    return {
        'torches': {cls: {'plateMeshes': plate_meshes(cls), 'plateComponents':
                          list(PLATE_COMPONENTS), 'default': default_plate(cls)}
                    for cls in WALL_PLATE_CLASSES},
        'ambience': sorted(({'key': k, 'label': _label(k), 'count': v['count'],
                             'bed': k.startswith('AmbBed_')} for k, v in cues.items()),
                           key=lambda r: r['label'].lower()),
        'defaultCue': default_cue(),
        'ambientClass': AMBIENT_CLASS,
        'lightning': dict(lightning_donor_values(), cls=LIGHTNING_CLASS,
                          retriggerMax=RETRIGGER_MAX, glowMax=GLOW_MAX,
                          hiddenParts=['AmbientSphere']),
        'envtraps': envtraps.choices(),
        'kitactors': _kit_choices(),
    }


def _kit_choices():
    """riftlord.kitactors.choices, or None when a kit cannot be read."""
    from riftlord.mapeditor import kitactors
    try:
        return kitactors.choices()
    except (Exception, SystemExit):                  # noqa: BLE001
        return None


# -- checks on a built pak ------------------------------------------------------------------

def _read(files, name):
    from riftlord.build import levelbuild
    stem = name[:-5]
    return levelbuild.Builder.around(files[name], files[stem + '.uexp'])


def _actor_problems(builder, actor):
    """What is wrong with one placed Blueprint actor: its class and CDO serialized before
    it is created (placed-bp-actor-preloads), every listed component its own, no null
    preload."""
    from riftlord.build import levelbuild
    pkg = builder.pkg
    meta = pkg.exports[actor - 1]['meta']
    first = struct.unpack_from('<i', meta, 84)[0]
    counts = struct.unpack_from('<4i', meta, 88)
    groups, at = [], first
    for n in counts:
        groups.append(list(pkg.preload[at:at + n]) if first >= 0 else [])
        at += n
    out = []
    cls = struct.unpack_from('<i', meta, levelbuild.CLASS_AT)[0]
    template = struct.unpack_from('<i', meta, 8)[0]
    if cls < 0 and cls not in groups[2]:
        out.append('class not serialized before create')
    if template < 0 and template not in groups[2]:
        out.append('class default object not serialized before create')
    if any(v == 0 for g in groups for v in g):
        out.append('null preload')
    for comp in builder.object_array(actor, 'BlueprintCreatedComponents'):
        if comp > 0 and struct.unpack_from('<i', pkg.exports[comp - 1]['meta'],
                                           levelbuild.OUTER_AT)[0] != actor:
            out.append('listed component %s belongs to another actor' % levelbuild._name_of(pkg, comp - 1))
    return out


def gates(files, plan):
    """[(name, ok, detail)] for the probe: every controller setting the plan makes is in
    the built packages (riftlord/probe.py)."""
    from riftlord.build import levelbuild, leveltitle
    plan = plan or {}
    out = []
    #: the mission's own level, whatever its Working Title names it
    level_name = leveltitle.level_entry(files)
    if not level_name:
        return out
    level = _read(files, level_name)
    pkg = level.pkg
    by_class = {}
    for i in range(len(pkg.exports)):
        by_class.setdefault(_class_name(pkg, i + 1), []).append(i + 1)
    listed = set(level.actor_list())
    managed = (AMBIENT_CLASS, LIGHTNING_CLASS) + WALL_PLATE_CLASSES + ENV_TRAP_CLASSES
    wanted = {}
    for item in plan.get('added') or []:
        if item.get('kind') == 'kit' and item.get('name') in managed:
            wanted.setdefault(item['name'], []).append(item)
    for cls, items in sorted(wanted.items()):
        mine = [a for a in by_class.get(cls, []) if a in listed and
                levelbuild._name_of(pkg, a - 1).startswith('RiftLordKit')]
        bad = []
        for a in mine:
            bad += ['%s: %s' % (levelbuild._name_of(pkg, a - 1), p)
                    for p in _actor_problems(level, a)]
        out.append(('%s placed (%d of %d), preloads and components' % (cls, len(mine),
                                                                       len(items)),
                    len(mine) == len(items) and not bad, '; '.join(bad[:3])))
        #: import object names as the game spells them: `P_CauldronPour_650` is stored
        #: as `P_CauldronPour` with FName number 651
        names = set()
        for rec in pkg.imports:
            text = pkg.names[struct.unpack_from('<I', rec, 20)[0]][0]
            number = struct.unpack_from('<i', rec, 24)[0]
            names.add('%s_%d' % (text, number - 1) if number else text)
        if cls == AMBIENT_CLASS:
            cues = {i.get(CUE_KEY) or default_cue() for i in items}
            missing = sorted(c for c in cues if c not in names)
            out.append(('ambience cues imported (%s)' % ', '.join(sorted(cues)), not missing,
                        ', '.join(missing)))
        if cls == 'BP_Cauldron_C':
            from riftlord.mapeditor import envtraps
            pours = {i.get(envtraps.POUR_KEY) or envtraps.default_pour() for i in items}
            missing = sorted(p for p in pours if p not in names)
            out.append(('cauldron pours imported (%s)' % ', '.join(sorted(pours)),
                        not missing, ', '.join(missing)))
    for tag, by_actor in plate_edits(plan).items():
        name = level_name if tag == 'persistent' else leveltitle.sublevel_entry(files, tag)
        if name is None:
            out.append(('wall plates in %s' % tag, False, 'the sublevel was not copied'))
            continue
        sub = level if tag == 'persistent' else _read(files, name)
        wrong = ['%d' % a for a, on in by_actor.items() if has_plate(sub, a) != on]
        #: a taken-away plate: both components without a mesh
        wrong += ['%d %s' % (a, n) for a, on in by_actor.items() if not on
                  for n in PLATE_COMPONENTS
                  if _child(sub, a, n) and _object_value(sub, _child(sub, a, n),
                                                         'StaticMesh') != 0]
        out.append(('wall plates in %s (%d)' % (tag, len(by_actor)), not wrong,
                    'wrong on ' + ', '.join(wrong) if wrong else ''))
    from riftlord.mapeditor import volumes
    kill = {e['name']: e for e in volumes.clean(plan.get('volumes'))
            if any(k in e for k in volumes.KILL_FLAGS)}
    if kill:
        from riftlord.formats.omd3 import meshcat
        names = [n for n, _h in pkg.names]
        wrong = []
        for i in by_class.get(volumes.KILL_CLASS, []):
            shown = levelbuild._name_of(pkg, i - 1)
            number = struct.unpack_from('<i', pkg.exports[i - 1]['meta'], levelbuild.NAME_AT + 4)[0]
            if number:
                shown = '%s_%d' % (shown, number - 1)
            e = kill.get(shown)
            if not e:
                continue
            data = pkg.exports[i - 1]['data']
            top = meshcat._walk(data, names, 0, len(data))
            for key, prop in volumes.KILL_FLAGS.items():
                have = bool(data[top[prop][1] - 2]) if prop in top else True
                if key in e and have != e[key]:
                    wrong.append('%s %s' % (shown, prop))
            kill.pop(shown)
        out.append(('kill volume switches written', not wrong and not kill,
                    ', '.join(wrong + ['%s not found' % k for k in kill])))
    from riftlord.build.world import levelfx
    want = {k: v for k, v in levelfx.clean(plan.get('world')).items() if k in WIND_FIELDS}
    if want:
        from riftlord.formats.omd3 import meshcat
        seen = {}
        for name in leveltitle.level_maps(files):
            b = level if name == level_name else _read(files, name)
            names = [n for n, _h in b.pkg.names]
            for i, ex in enumerate(b.pkg.exports):
                if _class_name(b.pkg, i + 1) != WIND_CLASS:
                    continue
                top = meshcat._walk(ex['data'], names, 0, len(ex['data']))
                for key, f in WIND_FIELDS.items():
                    if f['prop'] in top:
                        seen[key] = round(struct.unpack_from('<f', ex['data'],
                                                             top[f['prop']][1])[0], 3)
        wrong = ['%s %s != %s' % (k, seen.get(k), v) for k, v in want.items()
                 if seen.get(k) != round(v, 3)]
        out.append(('wind written (%s)' % ', '.join('%s %g' % kv for kv in sorted(want.items())),
                    not wrong, '; '.join(wrong)))
    return out


# -- offline check ---------------------------------------------------------------------------

#: The level `--check` builds on: it has no environmental traps, a plated wall torch
#: (Lighting export 86, BP_WallTorch_SmallRadius_C), a bare one (Lighting 68,
#: BP_WallTorch_Normal_C), two kill volumes and wind in the persistent level.
CHECK_LEVEL = 'Scenario_03'
CHECK_PLATES = {'Lighting|86': False, 'Lighting|68': True}
CHECK_KILL = 'OMDCharacterKillVolume_1'
#: The strongest shipped wind (Scenario_15, q1_report.txt).
CHECK_WIND = {'windSpeed': 22.0, 'windStrength': 20.0, 'windMinGust': 15.0,
              'windMaxGust': 20.0}


def check_plan():
    """A plan using every setting this module and riftlord.envtraps add."""
    from riftlord.mapeditor import envtraps

    def at(x, y, z):
        return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, x, y, z, 1.0]
    added = [
        {'kind': 'kit', 'name': AMBIENT_CLASS, 'cue': 'AmbEmit_Ocean_Close_Dist_LP_SC',
         'matrix': at(450, 3300, 200)},
        {'kind': 'kit', 'name': LIGHTNING_CLASS, 'retrigger': [3.0, 6.0],
         'glow': [5.0, 1.0, 0.5, 1.0], 'matrix': at(450, 6000, 10000)},
        {'kind': 'kit', 'name': envtraps.CAULDRON, 'pour': 'P_CauldronPour_650',
         'matrix': at(137, 1700, 620)},
        {'kind': 'kit', 'name': envtraps.BARREL, 'matrix': at(437, 2600, 950)},
        {'kind': 'kit', 'name': 'BP_WallTorch_Normal_C', 'wallPlate': False,
         'matrix': at(0, 3000, 300)},
        {'kind': 'kit', 'name': 'BP_WallTorch_Normal_C', 'wallPlate': True,
         'matrix': at(0, 3400, 300)},
    ] + [{'kind': 'kit', 'name': cls, 'matrix': at(437, 500 + 1200 * k, 20)}
         for k, cls in enumerate(envtraps.GRINDERS)]
    return {'level': CHECK_LEVEL, 'added': added, PLATES_KEY: dict(CHECK_PLATES),
            'volumes': [{'name': CHECK_KILL, 'killPlayers': False}],
            'world': dict(CHECK_WIND)}


def check(out_dir, log=print):
    """Save the check plan and read it back, build it into a copy of CHECK_LEVEL and its
    Lighting sublevel, and run pkgcheck and `gates` on the result. Returns failures."""
    import tempfile
    from riftlord.build.navigation import navpatch
    from riftlord.core import pkgcheck
    from riftlord.build import apply as applier
    from riftlord.mapeditor import leveldata
    fails = []
    plan = check_plan()
    keep = leveldata.PLANS
    leveldata.PLANS = tempfile.mkdtemp(prefix='plans-', dir=out_dir)
    try:
        leveldata.save_plan(CHECK_LEVEL, plan)
        back = leveldata.load_plan(CHECK_LEVEL)
    finally:
        leveldata.PLANS = keep
    for i, (want, got) in enumerate(zip(plan['added'], back.get('added') or [])):
        for key in (CUE_KEY, PLATE_KEY, RETRIGGER_KEY, GLOW_KEY, 'pour'):
            if key in want and got.get(key) != want[key]:
                fails.append('save dropped added[%d].%s' % (i, key))
    if back.get(PLATES_KEY) != plan[PLATES_KEY]:
        fails.append('save dropped %s' % PLATES_KEY)
    if (back.get('volumes') or [{}])[0].get('killPlayers') is not False:
        fails.append('save dropped the kill volume switch')
    if any(back.get('world', {}).get(k) != v for k, v in CHECK_WIND.items()):
        fails.append('save dropped the wind')
    log('save round trip: %s' % ('ok' if not fails else '; '.join(fails)))
    files = {}
    stem = 'OMD/Content/Mods/ControllerCheck/Maps/ControllerCheck_Map01'
    got = navpatch.fetch_map(navpatch.map_path(CHECK_LEVEL), out_dir)
    ua, ux, report = applier.apply_plan(
        open(got['.umap'], 'rb').read(), open(got['.uexp'], 'rb').read(), CHECK_LEVEL,
        '/Game/Mods/ControllerCheck/Maps/ControllerCheck_Map01', 'ControllerCheck_Map01',
        plan=back)
    files[stem + '.umap'], files[stem + '.uexp'] = ua, ux
    for tag, edits in applier.sublevel_edits(CHECK_LEVEL, back).items():
        if tag == 'persistent':
            continue
        path = leveldata.level(CHECK_LEVEL)['subPackages'][tag]
        sub = navpatch.fetch_map(path, out_dir)
        sa, sx, sreport = applier.apply_to_sublevel(
            open(sub['.umap'], 'rb').read(), open(sub['.uexp'], 'rb').read(),
            'ControllerCheck_Map01_%s' % tag, edits)
        files['%s_%s.umap' % (stem, tag)], files['%s_%s.uexp' % (stem, tag)] = sa, sx
        report['notes'] += sreport['notes']
        report['skipped'] += sreport['skipped']
    for line in report['notes']:
        log('  note: %s' % line)
    for line in report['skipped']:
        log('  skipped: %s' % line)
        fails.append('skipped: %s' % line)
    for name in sorted(files):
        if name.endswith('.umap'):
            bad = pkgcheck.problems(files[name], files[name[:-5] + '.uexp'],
                                    os.path.basename(name[:-5]))
            log('pkgcheck %s: %s' % (os.path.basename(name), 'ok' if not bad else bad[:3]))
            fails += ['pkgcheck %s: %s' % (name, b) for b in bad]
            with open(os.path.join(out_dir, os.path.basename(name)), 'wb') as fh:
                fh.write(files[name])
            with open(os.path.join(out_dir, os.path.basename(name[:-5]) + '.uexp'), 'wb') as fh:
                fh.write(files[name[:-5] + '.uexp'])
    for name, ok, detail in gates(files, back):
        log('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + detail) if detail else ''))
        if not ok:
            fails.append(name)
    return fails


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--check', metavar='DIR',
                    help='build every setting into a copy of %s offline, in DIR' % CHECK_LEVEL)
    ap.add_argument('--survey', action='store_true', help='rebuild controller-donors.json')
    ap.add_argument('--show', action='store_true', help='print what the survey found')
    ap.add_argument('--level', action='append', help='survey only these levels')
    args = ap.parse_args(argv)
    if args.check:
        os.makedirs(args.check, exist_ok=True)
        fails = check(args.check)
        print('%d failure(s)' % len(fails))
        return 1 if fails else 0
    if args.survey:
        found = survey(args.level)
        summary = summarise(found)
        write(summary)
        load.cache_clear()
        print('wrote %s' % OUT)
    if args.show:
        got = load()
        print(json.dumps({k: (v if k != 'lightning' else len(v)) for k, v in got.items()
                          if k not in ('envtraps',)}, indent=1)[:6000])
        for cls, rows in (got.get('envtraps') or {}).items():
            print(cls, len(rows))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
