"""The Crystal of Crystal Defense (Mods/CrystalDefense): a map piece over the rift.

`BP_Crystal_C` (/Game/Mods/CrystalDefense/BP_Crystal, in ~mods/CrystalDefense_P.pak) is a
bpkit child of the trap base `BP_OMDTrap_Base_C` (AOMDTrap), with its own TrapProtos row.
Its package has no subobject exports and no construction-script components: every visual
is added at BeginPlay. A placed crystal is retargeted from a shipped placed child of the
trap base (a grinder pit, DONORS) as a Treasure Chest is from the cauldron
(kitactors._chest_view): the donor class's own components are left out, the trap base's
are kept, and the native components' templates name `Default__BP_OMDTrap_Base_C`.

A mission with a crystal REQUIRES the mod: nothing of it is copied into the mission pak.
The class is read from the installed pak (riftlord.core.supportedmods); without it the
Crystal card is hidden and a plan holding a crystal is refused at build. The mission's
CustomMaps.yaml entry gets `GameType: CrystalDefense`.

Plan data: one `added` item per mission,
    {kind: 'crystal', name: 'BP_Crystal_C', matrix,
     crystal: {difficultyHealth: [4], bobHeight, bobSeconds, riftGap, groups: [...]}}
`difficultyHealth` is the Crystal's fixed health per difficulty (HEALTH_KEY), edited in the
Map Editor's Crystal panel and the Onslaught Editor's Rift Tear tab (rifttear.editor_view's
`health`). Plans made before Rift Tear groups carry `rifts: [{at, spawner, unit, count}]`.
`rifts[].spawner` is the id of a plan spawner with `unstable`; its indexInLevel is read
after planactors.emit has numbered the spawners, and the spawner is written with the row's
units (SpawnProtodata, MinSpawnCount, MaxSpawnCount): the Crystal opens it and spawns
those units through the coordinator's SpawnEntityForEntity, and no wave sends units
through it. A property is written only when it
differs from the class default. The level pieces the Crystal stands in place of are parked
by riftlord.mapeditor.crystalpark.

    python3 -m riftlord.mapeditor.crystal --check DIR     # save, build and gate offline
"""

import argparse
import json
import math
import os
import struct

#: The plan kind, the class, its package and New Object label.
KIND = 'crystal'
CLASS = 'BP_Crystal_C'
PACKAGE = '/Game/Mods/CrystalDefense/BP_Crystal'
LABEL = 'Crystal'
#: The plan key of the crystal's settings.
KEY = 'crystal'
#: The supported mod and feature (riftlord.core.supportedmods) the crystal comes from.
MOD_ID = 'CrystalDefense'
FEATURE = 'crystal'
#: The CustomMaps.yaml game type of a mission with a crystal (the mod's GameTypes.yaml Id).
GAME_TYPE = 'CrystalDefense'
#: The Blueprint parent the class must have, and the shipped placed children of it a
#: crystal is retargeted from, first found first (omd3actors catalogue).
PARENT = ('/Game/Traps/_Base/BP/BP_OMDTrap_Base', 'BP_OMDTrap_Base_C')
DONORS = ('BP_GrinderPit_4X_C', 'BP_GrinderPit_3X_C', 'BP_Grinders_3X_C')
#: Export name of the placed crystal.
PLACED_NAME = 'RiftLordCrystal'

#: The class's float variables read from its default object, with the class's own defaults
#: (Mods/CrystalDefense) used when the class cannot be read.
CLASS_FLOATS = {'HealthPerRiftPoint': 20.0, 'MinHealth': 1000.0, 'BobHeight': 25.0,
                'BobSeconds': 4.0, 'RiftGap': 2.0}
#: Plan float fields and the class variables they write.
FLOAT_VARS = (('bobHeight', 'BobHeight', 25.0),
              ('bobSeconds', 'BobSeconds', 4.0),
              ('riftGap', 'RiftGap', 2.0))
#: Crystal health: one fixed value per difficulty, plan `difficultyHealth` [Apprentice, War
#: Mage, Rift Lord, Endless] (EGameDifficulty order), written whole as the per-instance
#: float[4] DIFFICULTY_HEALTH (contract with win64-64, 2026-10-24). 0 is no value: the
#: Crystal falls back to the class's MinHealth / HealthPerRiftPoint, which are never
#: written. Plans with healthPerRiftPoint / minHealth read as minHealth at every difficulty.
HEALTH_KEY = 'difficultyHealth'
DIFFICULTY_HEALTH = 'DifficultyHealth'
HEALTH_DIFFICULTIES = ('Apprentice', 'War Mage', 'Rift Lord', 'Endless')
HPP_VAR, MIN_VAR = 'HealthPerRiftPoint', 'MinHealth'
LABELS = {'bobHeight': 'Bob Height', 'bobSeconds': 'Bob Seconds',
          'riftGap': 'Gap Between Minion Rows (s)', 'health': 'Health'}
RIFT_AT, RIFT_INDEX, RIFT_WAVE = 'RiftAt', 'RiftIndex', 'RiftWave'
#: Accepted ranges of the plan fields.
RANGES = {'health': (0.0, 10000000.0),
          'bobHeight': (0.0, 1000.0), 'bobSeconds': (0.1, 600.0), 'riftGap': (0.0, 60.0),
          'at': (0.1, 99.9), 'count': (1, 100)}
#: An older rift row's count when it gives none.
COUNT_DEFAULT = 3
#: Most unstable-rift rows a crystal takes.
RIFTS_MAX = 16

#: Stand-in sizes for the editor when the mod's manifest gives no foot (crystalpark): the
#: foot (hitbox and obstruction round the actor's origin, base at it) and the floating
#: crystal (SM_Crystal), centred at CRYSTAL_CENTRE in actor space (dist/report.json and
#: Pieces.json, 2026-10-24).
FOOT_SIZE = (972.0, 1098.0, 407.9)
CRYSTAL_SIZE = (333.4, 334.1, 1223.3)
CRYSTAL_CENTRE = (51.2, 20.8, 835.2)

#: A rift-base piece (meshcat category 'rift') within this many uu of the rift, sideways,
#: carries the crystal: half RiftBase's 772 uu width plus a margin.
BASE_REACH = 450.0
#: Height of a shipped rift actor over the top of its rift base: the median over
#: Scenario_01, _02, _05, _08, _12, _18, DLC_03, Ice_01, Sand_03 (195..387 uu), measured
#: 2026-10-23. Used when no base is found.
RIFT_HOVER = 210.3
#: A route point nearer the rift than this, sideways, gives no direction.
APPROACH_MIN = 50.0


# -- the pak ----------------------------------------------------------------------------------

def pak_name():
    """The mod's file as Supported Mods expects it ('~mods/CrystalDefense_P.pak')."""
    from riftlord.core import supportedmods
    return supportedmods.BY_ID[MOD_ID].files[0]


def pak():
    """The detected mod pak the class is read from, or None."""
    from riftlord.core import supportedmods
    return supportedmods.mod_file(FEATURE)


_PACKAGE = {}


def class_package():
    """BP_Crystal's package, payloads loaded: read from the detected pak by its two
    entries, again whenever the pak changes. SystemExit when the mod is not detected."""
    from riftlord.core import paksource
    from riftlord.formats.ue.uassetio import Package
    from riftlord.mapeditor import kitactors
    path = pak()
    if not path:
        raise SystemExit('%s is not installed' % pak_name())
    st = os.stat(path)
    stamp = (path, st.st_size, st.st_mtime_ns)
    if _PACKAGE.get('stamp') != stamp:
        stem = kitactors.inner(PACKAGE)
        _PACKAGE.update(stamp=stamp, blobs=(paksource.read(path, stem + '.uasset'),
                                            paksource.read(path, stem + '.uexp')))
    pkg = Package(*_PACKAGE['blobs'])
    pkg.load_payloads()
    return pkg


#: The mode's minimap icon in the mod pak, as the game draws the Crystal on the minimap.
MAP_ICON = '/Game/Mods/CrystalDefense/UI/T_CrystalDefense_MapIcon'
_ICON = {}


def map_icon_png():
    """MAP_ICON as PNG bytes (RGBA), or None when the mod is not installed or it cannot be
    read; read again whenever the pak changes."""
    import io
    import tempfile
    from riftlord.core import paksource
    from riftlord.formats.ue import texture
    from riftlord.mapeditor import kitactors
    path = pak()
    if not path:
        return None
    st = os.stat(path)
    stamp = (path, st.st_size, st.st_mtime_ns)
    if _ICON.get('stamp') == stamp:
        return _ICON['png']
    png = None
    try:
        stem = kitactors.inner(MAP_ICON)
        names = set(paksource.names(path))
        with tempfile.TemporaryDirectory(prefix='crystalicon-') as tmp:
            staged = {}
            for ext in ('.uasset', '.uexp', '.ubulk'):
                if stem + ext in names:
                    staged[ext] = os.path.join(tmp, 't' + ext)
                    with open(staged[ext], 'wb') as fh:
                        fh.write(paksource.read(path, stem + ext))
            image = texture.to_image(texture.parse(staged['.uasset'], staged['.uexp'],
                                                   staged.get('.ubulk')))
        if image is not None:
            buf = io.BytesIO()
            image.convert('RGBA').save(buf, format='PNG')
            png = buf.getvalue()
    except Exception:                                # noqa: BLE001
        png = None
    _ICON.update(stamp=stamp, png=png)
    return png


def class_defaults():
    """{variable: default} of the class's float variables, from its default object (the
    FLOAT_VARS fallbacks for one it does not set, or when the class cannot be read)."""
    from riftlord.mapeditor import kitactors
    out = dict(CLASS_FLOATS)
    try:
        pkg = class_package()
    except SystemExit:
        return out
    for name, (kind, raw, _n) in kitactors._props(pkg, kitactors._cdo(pkg)).items():
        if name in out and kind == 'FloatProperty':
            out[name] = round(struct.unpack_from('<f', raw)[0], 4)
    return out


def class_problems():
    """[text]: why the class cannot be placed as built here (its default object's template
    must be the trap base's, and it may add no subobjects of its own)."""
    from riftlord.mapeditor import kitactors
    try:
        pkg = class_package()
    except SystemExit as exc:
        return [str(exc)]
    cdo = kitactors._cdo(pkg)
    tmpl = kitactors._meta(pkg, cdo, 8)
    if tmpl >= 0:
        return ['%s has no parent class' % CLASS]
    rec = kitactors._import_rec(pkg, tmpl)
    if rec[3] != 'Default__' + PARENT[1]:
        return ['%s is a child of %s, not %s' % (CLASS, rec[3][len('Default__'):], PARENT[1])]
    if any(kitactors._meta(pkg, i, 12) == cdo + 1 for i in range(len(pkg.exports))):
        return ['%s has subobjects, which are not placed' % CLASS]
    return []


def parent_components():
    """Sorted component names a placed child of the trap base carries from it: its default
    object's subobjects, its construction-script components (the default scene root left
    out: a native root exists) and its timelines' components."""
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    from riftlord.mapeditor import kitactors
    pkg = Package(*gamefiles.package(PARENT[0]))
    pkg.load_payloads()
    cdo = kitactors._cdo(pkg)
    out = set()
    for i in range(len(pkg.exports)):
        name = kitactors._export_name(pkg, i)[0]
        if kitactors._meta(pkg, i, 12) == cdo + 1:
            out.add(name)
        elif name.endswith('_GEN_VARIABLE') and not name.startswith('DefaultSceneRoot'):
            out.add(name[:-len('_GEN_VARIABLE')])
        elif name.endswith('_Template') and kitactors._import_rec(
                pkg, kitactors._meta(pkg, i, 0))[3] == 'TimelineTemplate':
            out.add(name[:-len('_Template')])
    return sorted(out)


def donor():
    """(class, (package, zero-based actor export)) of the first shipped placed child of
    the trap base in DONORS; SystemExit without one."""
    from riftlord.build import apply as applier
    for cls in DONORS:
        got = applier.donor_for(cls)
        if got:
            return cls, got
    raise SystemExit('no shipped placed %s child (%s) to place a Crystal from'
                     % (PARENT[1], ', '.join(DONORS)))


# -- the plan ---------------------------------------------------------------------------------

def piece(plan):
    """The plan's crystal item, or None."""
    return next((a for a in (plan or {}).get('added') or []
                 if isinstance(a, dict) and a.get('kind') == KIND), None)


def defaults():
    """The plan fields' defaults: the class's own."""
    got = class_defaults()
    out = {key: got[prop] for key, prop, _d in FLOAT_VARS}
    out[HEALTH_KEY] = [got[MIN_VAR]] * len(HEALTH_DIFFICULTIES)
    out['groups'] = []
    #: waves of one onslaught loop the groups' per-wave rows cover (rifttear.wave_count)
    out['waveCount'] = 0
    #: rows of plans made before Rift Tear groups (rifttear.from_rows reads them)
    out['rifts'] = []
    return out


def _old_health(raw, base):
    """difficultyHealth of settings saved before it: their minHealth at every difficulty."""
    if raw.get('minHealth') is None:
        return list(base[HEALTH_KEY])
    try:
        v = max(RANGES['health'][0], min(RANGES['health'][1], float(raw['minHealth'])))
    except (TypeError, ValueError):
        return list(base[HEALTH_KEY])
    return [round(v, 4)] * len(HEALTH_DIFFICULTIES)


def values(item):
    """A crystal item's settings with the defaults filled in (a blank health 0)."""
    out = defaults()
    raw = (item or {}).get(KEY) or {}
    out.update({k: v for k, v in raw.items() if k in out})
    if not isinstance(raw.get(HEALTH_KEY), list):
        out[HEALTH_KEY] = _old_health(raw, out)
    else:
        out[HEALTH_KEY] = [0.0 if v is None or v == '' else v for v in raw[HEALTH_KEY]]
    return out


def clean_health(raw):
    """difficultyHealth validated: one number per HEALTH_DIFFICULTIES entry, a blank 0."""
    if not isinstance(raw, list) or len(raw) != len(HEALTH_DIFFICULTIES):
        raise ValueError('Crystal Health takes %d values' % len(HEALTH_DIFFICULTIES))
    return [0.0 if v is None or v == '' else _number(v, 'health', '%s %s' % (d, LABELS['health']))
            for v, d in zip(raw, HEALTH_DIFFICULTIES)]


def _number(raw, key, label):
    lo, hi = RANGES[key]
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ValueError('%s must be a number' % label)
    if v != v or not lo <= v <= hi:
        raise ValueError('%s must be between %g and %g' % (label, lo, hi))
    return round(v, 4)


def clean_piece(raw, matrix):
    """The crystal as a plan keeps it, its settings validated."""
    r = raw.get(KEY) if isinstance(raw.get(KEY), dict) else {}
    base = defaults()
    out = {}
    for key, _prop, _d in FLOAT_VARS:
        out[key] = _number(r.get(key, base[key]), key, LABELS[key])
    out[HEALTH_KEY] = clean_health(r[HEALTH_KEY] if isinstance(r.get(HEALTH_KEY), list)
                                   else _old_health(r, base))
    rows = []
    for row in (r.get('rifts') or [])[:RIFTS_MAX]:
        if not isinstance(row, dict):
            continue
        at = _number(row.get('at'), 'at', 'At health %')
        sp = row.get('spawner')
        unit = str(row.get('unit') or '').strip() or None
        count = int(_number(row.get('count', COUNT_DEFAULT), 'count', 'Count'))
        rows.append({'at': at, 'spawner': int(sp) if isinstance(sp, (int, float)) else None,
                     'unit': unit, 'count': count})
    #: Rift Tear groups (riftlord.mapeditor.rifttear); older rows become groups of one
    from riftlord.mapeditor import rifttear
    out['groups'] = rifttear.clean_groups(
        r.get('groups') if r.get('groups') else
        rifttear.from_rows(sorted(rows, key=lambda x: -x['at'])))
    if rifttear.wave_count(r):
        out['waveCount'] = rifttear.wave_count(r)
    #: the user's hitbox half extents (riftlord/mapeditor/crystalfit.py); none: fitted at build
    from riftlord.mapeditor import crystalfit
    for key, prop, _axis in crystalfit.FIELDS:
        v = crystalfit.clean(r.get(key), prop)
        if v is not None:
            out[key] = v
    v = crystalfit.clean_drop(r.get(crystalfit.DROP_KEY))
    if v is not None:
        out[crystalfit.DROP_KEY] = v
    piece = {'name': CLASS, 'kind': KIND, 'matrix': matrix, KEY: out}
    if isinstance(raw.get(LEVEL_KEY), str) and raw[LEVEL_KEY].strip():
        piece[LEVEL_KEY] = raw[LEVEL_KEY].strip()[:64]
    return piece


def unstable_spawners(plan):
    """{plan spawner id: spawner} of the plan's unstable rifts."""
    return {int(sp['id']): sp for sp in (plan or {}).get('spawners') or []
            if isinstance(sp, dict) and sp.get('unstable') and sp.get('id') is not None}


def rift_ids(plan):
    """Plan spawner ids the crystal opens: every rift of its Rift Tear groups."""
    from riftlord.mapeditor import rifttear
    item = piece(plan)
    return rifttear.member_ids(values(item)) if item else []


def rift_indices(plan):
    """indexInLevel of each unstable rift the crystal opens, where the plan gives one."""
    ups = unstable_spawners(plan)
    return sorted({int(ups[i].get('index') or 0) for i in rift_ids(plan) if i in ups} - {0})


def unit_guids():
    """{proto row: (4 ints)} of every AI character a rift row may send (shipped and
    installed mods' minions, onslaught.protos)."""
    from riftlord.onslaught import onslaught
    return onslaught.protos()


def problems(plan, spec=None):
    """[text]: what keeps the plan's crystal from building as asked: one Crystal; its Rift
    Tear groups each with placed unstable rifts, known minions and a
    trigger; each of those rifts with a start point (StartingPathPoint, the route its units
    walk); no wave of `spec` (the mission's onslaught) sends units through a rift the
    Crystal opens."""
    from riftlord.mapeditor import rifttear
    items = [a for a in (plan or {}).get('added') or []
             if isinstance(a, dict) and a.get('kind') == KIND]
    if not items:
        return []
    out = []
    if len(items) > 1:
        out.append('a mission takes one Crystal, not %d' % len(items))
    #: Crystal Defense is always one crystal over one rift (user, 2026-10-30): more rifts are
    #: Rift Defense only
    rifts = rifts_in_play(plan)
    if rifts > 1:
        out.append('Crystal Defense takes one rift, not %d: remove the extra rifts or the '
                   'Crystal' % rifts)
    v = values(items[0])
    ups = unstable_spawners(plan)
    for sid in rift_ids(plan):
        if sid in ups and ups[sid].get('start') is None:
            out.append('Unstable Rift %d, which the Crystal opens, has no start point'
                       % (ups[sid].get('index') or sid))
    out += rifttear.problems(v, plan, spec, unit_guids() if rifttear.groups(v) else {})
    #: a missing mod is refused by supportedmods.build_problems
    if pak():
        out += class_problems()
    return out


def rifts_in_play(plan):
    """How many rifts a mission with this plan has: the level's own (less any the plan
    removes) and the plan's placed ones."""
    from riftlord.mapeditor import leveldata
    plan = plan or {}
    name = plan.get('level') or (plan.get('scratch') or {}).get('base')
    try:
        own = [r.get('name') for r in leveldata.level(name).get('rifts') or []] if name else []
    except (Exception, SystemExit):                  # noqa: BLE001
        own = []
    gone = {w.get('name') for w in plan.get('wiring') or []
            if w.get('kind') == 'rift' and w.get('gone')}
    return len([n for n in own if n not in gone]) + len(plan.get('rifts') or [])


def game_type(plan):
    """The CustomMaps.yaml GameType of a mission with this plan, or ''."""
    return GAME_TYPE if piece(plan) else ''


# -- where it goes ----------------------------------------------------------------------------

def spot(level_name, rift=None):
    """{'at': [x, y, z], 'yaw': degrees, 'matrix'}: on the rift's base, turned so its own
    pieces line up with the level's (crystalpark.aligned_yaw), else facing the routes that
    reach the rift. `rift` is where the rift stands now (default: the level's own)."""
    from riftlord.formats.omd3 import meshcat
    from riftlord.mapeditor import leveldata
    from riftlord.mapeditor import planactors
    lv = leveldata.level(level_name)
    if rift is None:
        rifts = lv.get('rifts') or []
        rift = rifts[0]['loc'] if rifts else None
    if rift is None:
        raise SystemExit('%s has no rift' % level_name)
    x, y, z = (float(v) for v in rift[:3])
    cat = meshcat.load()
    tops = []
    for p in lv.get('pieces') or []:
        info = cat.get(p.get('mesh'))
        m = p.get('matrix')
        if not info or info.get('category') != 'rift' or not m:
            continue
        if math.hypot(m[12] - x, m[13] - y) > BASE_REACH:
            continue
        up = math.sqrt(m[8] ** 2 + m[9] ** 2 + m[10] ** 2)
        tops.append(m[14] + up * (info['origin'][2] + info['extent'][2]))
    acts = planactors.level_actors(level_name)
    points = acts.get('points') or {}
    exit_name = acts.get('exit')
    exit_pt = points.get(exit_name) if exit_name else None
    if tops:
        floor = max(tops)
    elif exit_pt and math.hypot(exit_pt['location'][0] - x, exit_pt['location'][1] - y) \
            <= BASE_REACH:
        floor = float(exit_pt['location'][2])
    else:
        floor = z - RIFT_HOVER
    ux = uy = 0.0
    for name, p in points.items():
        if not any(b.get('to') == exit_name for b in p.get('next') or []):
            continue
        dx, dy = p['location'][0] - x, p['location'][1] - y
        d = math.hypot(dx, dy)
        if d > APPROACH_MIN:
            ux, uy = ux + dx / d, uy + dy / d
    yaw = round(math.degrees(math.atan2(uy, ux)) if math.hypot(ux, uy) > 1e-6 else 0.0, 2)
    at = [round(x, 2), round(y, 2), round(floor, 2)]
    #: turned so its own pieces stand where the level's do (riftlord.mapeditor.crystalpark)
    from riftlord.mapeditor import crystalpark
    try:
        yaw = round(crystalpark.aligned_yaw(level_name, at, yaw), 2)
    except (Exception, SystemExit):                  # noqa: BLE001
        pass
    return {'at': at, 'yaw': yaw, 'matrix': yaw_matrix(at, yaw)}


def yaw_matrix(at, yaw):
    """The plan matrix (column-major, as apply.transform_of reads it) of a piece at `at`
    turned `yaw` degrees."""
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return [round(v, 6) for v in (c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0)] + \
        [at[0], at[1], at[2], 1]


#: The floating crystal's mesh in the mod pak, as the Map Editor reads it (kitactors.mesh),
#: and the texture it draws with: its emissive, which is the colour the game shows (its base
#: colour is near black).
CRYSTAL_MESH = 'kit/CrystalDefense/Mods/CrystalDefense/Art/SM_Crystal'
CRYSTAL_TEXTURE = '/kit/CrystalDefense/Mods/CrystalDefense/Art/T_Crystal_Mat0_EM'
#: The New Object card's preview name (/api/thumb/crystal:) and its cache version.
THUMB_NAME = 'crystal:'
THUMB_VERSION = 1


def model_parts():
    """The Crystal as the Map Editor draws it, in actor space: [{mesh, loc, rot, scale}] of
    the pieces it brings (the mod's manifest: shipped meshes, by catalogue name), then its
    floating crystal (CRYSTAL_MESH) with `fit` {size, centre}: drawn scaled into that box."""
    from riftlord.mapeditor import crystalpark
    out = []
    for p in ((crystalpark.manifest() or {}).get('pieces') or []):
        name = str(p.get('mesh') or '').rsplit('/', 1)[-1].split('.')[0]
        if name:
            out.append({'mesh': name, 'loc': list(p.get('location') or [0, 0, 0]),
                        'rot': list(p.get('rotation') or [0, 0, 0]),
                        'scale': list(p.get('scale') or [1, 1, 1])})
    out.append({'mesh': CRYSTAL_MESH, 'loc': [0, 0, 0], 'rot': [0, 0, 0], 'scale': [1, 1, 1],
                'texture': CRYSTAL_TEXTURE,
                'fit': {'size': list(CRYSTAL_SIZE), 'centre': list(CRYSTAL_CENTRE)}})
    return out


def _part_matrix(part):
    """3x4 rows of a model part's transform: loc, rot [pitch, yaw, roll] (Unreal's, so pitch
    and roll turn the opposite way to the right hand: Rz(yaw) Ry(-pitch) Rx(-roll)), scale."""
    p, y, r = (math.radians(v) for v in part['rot'])
    p, r = -p, -r
    cy, sy, cp, sp, cr, sr = math.cos(y), math.sin(y), math.cos(p), math.sin(p), math.cos(r), \
        math.sin(r)
    rot = [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
           [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
           [-sp, cp * sr, cp * cr]]
    s = part['scale']
    return [[rot[i][0] * s[0], rot[i][1] * s[1], rot[i][2] * s[2], part['loc'][i]]
            for i in range(3)]


def preview_model():
    """The Crystal as model_parts draws it, textured (omd3thumb.merge), for the card's
    preview, or None when nothing reads."""
    from riftlord.mapeditor import kitactors
    from riftlord.mapeditor import omd3serve
    from riftlord.mapeditor import omd3thumb
    placed = []
    for part in model_parts():
        got = kitactors.mesh(part['mesh']) if part['mesh'].startswith('kit/') \
            else omd3serve.mesh(part['mesh'])
        if not got or not got.get('verts') or not got.get('tris'):
            continue
        if part.get('fit'):
            lo = [min(v[i] for v in got['verts']) for i in range(3)]
            hi = [max(v[i] for v in got['verts']) for i in range(3)]
            k = part['fit']['size'][2] / (hi[2] - lo[2]) if hi[2] > lo[2] else 1.0
            m = [[k if c == i else 0.0 for c in range(3)] +
                 [part['fit']['centre'][i] - (lo[i] + hi[i]) / 2 * k] for i in range(3)]
        else:
            m = _part_matrix(part)
        placed.append(omd3thumb.place(got, m, texture=part.get('texture')))
    return omd3thumb.merge(placed)


def thumbnail(size=None):
    """The New Object card's preview of the Crystal as PNG, drawn as every mesh preview is
    (omd3thumb), kept on disk per mod pak; None without the mod."""
    import hashlib
    import io
    from riftlord.mapeditor import kitdonors
    from riftlord.mapeditor import omd3serve
    from riftlord.mapeditor import omd3thumb
    path = pak()
    if not path:
        return None
    st = os.stat(path)
    size = size or kitdonors.THUMB_SIZE
    tag = hashlib.sha1(json.dumps([THUMB_VERSION, omd3thumb.STYLE, size, st.st_size,
                                   st.st_mtime_ns]).encode()
                       ).hexdigest()[:12]
    cached = os.path.join(omd3serve.PREVIEWS, 'crystal__%s.png' % tag)
    if os.path.exists(cached):
        with open(cached, 'rb') as fh:
            return fh.read()
    got = preview_model()
    image = omd3thumb.render(got, size) if got else None
    if image is None:
        return None
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    png = buf.getvalue()
    omd3serve.keep(cached, png)
    return png


def crystalfit_fields():
    from riftlord.mapeditor import crystalfit
    return crystalfit.FIELDS


def editor_info(level_name, rift=None):
    """What the Map Editor's Crystal card and panel need: {spot, available, defaults,
    standIn: {foot, crystal: {size, centre}}}."""
    try:
        where = spot(level_name, rift)
    except SystemExit:
        where = None
    try:
        from riftlord.onslaught import gamedata
        units = [{'name': u['name'], 'label': u['label'], 'family': u['family']}
                 for u in gamedata.units() if not u['scramble']]
    except (Exception, SystemExit):                  # noqa: BLE001
        units = []
    from riftlord.mapeditor import crystalpark
    try:
        half = crystalpark.foot_half()
    except (Exception, SystemExit):                  # noqa: BLE001
        half = None
    foot = [2 * v for v in half] if half else list(FOOT_SIZE)
    return {'spot': where, 'available': bool(pak()), 'units': units, 'parts': model_parts(),
            'levelRifts': level_rifts(level_name),
            'labels': LABELS, 'fields': [k for k, _p, _d in FLOAT_VARS],
            'healthDifficulties': list(HEALTH_DIFFICULTIES),
            'healthRange': list(RANGES['health']),
            'count': COUNT_DEFAULT, 'countRange': list(RANGES['count']),
            'pak': pak_name(), 'defaults': defaults(), 'label': LABEL,
            'hitFields': [{'key': k, 'label': prop} for k, prop, _a in crystalfit_fields()] +
            [{'key': 'hitDrop', 'label': 'HitDrop'}],
            'standIn': {'foot': foot,
                        'crystal': {'size': list(CRYSTAL_SIZE), 'centre': list(CRYSTAL_CENTRE)}}}


# -- the build ---------------------------------------------------------------------------------

def note_build(plan, report):
    """A mission build's report: the game type of a mission with a crystal (the mod it
    needs is supportedmods.note_build's)."""
    if piece(plan):
        report['game_type'] = GAME_TYPE


def _place(builder, rename, report):
    """Write the placed crystal, retargeted from a shipped placed trap-base child (a grinder
    pit) as a chest is from the cauldron. Its package index."""
    from riftlord.build import apply as applier
    from riftlord.mapeditor import kitactors as K
    bad = class_problems()
    if bad:
        raise SystemExit('; '.join(bad))
    donor_cls, (src, j) = donor()
    view, dropped = K._chest_view(src, j, PACKAGE, cls=CLASS, ancestor=PARENT)
    actor = builder.lift_actor(view, j, rename=rename)
    #: the class's own component layout: no donor instance transforms or trigger size
    applier.reset_component_transforms(builder, actor)
    for i, e in enumerate(builder.pkg.exports):
        if struct.unpack_from('<i', e['meta'], 12)[0] == actor:
            builder.clear_property(i + 1, 'BoxExtent')
    report.setdefault('crystal', {}).update(donor=donor_cls, dropped=dropped)
    return actor


def float_properties(item):
    """{variable: value} of the Crystal's plan floats that differ from the class default."""
    got = values(item)
    base = class_defaults()
    want = {prop: float(got[key]) for key, prop, _d in FLOAT_VARS}
    return {prop: round(v, 4) for prop, v in want.items()
            if abs(v - float(base[prop])) > 1e-4}


def health_array(item):
    """{DIFFICULTY_HEALTH: [health per difficulty, 0 = none]}, all four always."""
    return {DIFFICULTY_HEALTH: [round(float(v or 0), 4) for v in values(item)[HEALTH_KEY]]}


def written_properties(plan, item, guids=None):
    """{variable: value} the placed crystal is written with: its floats that differ from the
    class default, its health per difficulty (health_array), and its Rift Tear arrays
    (rifttear.arrays: groups resolved to the plan's numbered spawners; trigger arrays at
    full length)."""
    from riftlord.mapeditor import rifttear
    out = float_properties(item)
    out.update(health_array(item))
    v = values(item)
    if uses_rows(item):
        out.update(rows_arrays(plan, item))
    elif rifttear.groups(v):
        out.update(rifttear.arrays(v, plan, unit_guids() if guids is None else guids))
    return out


# -- older rift rows: one rift per entry, the units on its spawner --------------------------
#
# A plan whose Crystal holds `rifts` rows and no groups (made before Rift Tear groups; Crystal
# Probe A) is written as the Crystal read it then: RiftAt / RiftWave / RiftIndex per entry
# (GroupStart left empty), and each rift's units on its spawner as SpawnProtodata,
# MinSpawnCount and MaxSpawnCount. Every other spawner field keeps the class default
# (MaxAliveCount, MinRespawnDelay 0: the Crystal sends its units once).

#: The spawner fields an older row writes (AOMDAISpawner, OMDAISpawner.h).
SPAWN_PROTO, SPAWN_MIN, SPAWN_MAX = 'SpawnProtodata', 'MinSpawnCount', 'MaxSpawnCount'
#: Spawner fields left at their class defaults, which the gate checks are not written.
SPAWN_DEFAULTS = ('MaxAliveCount', 'MinRespawnDelay')
PROTO_STRUCT = 'OMDSoftProtoPtr'


def uses_rows(item):
    """Whether a Crystal item carries older rift rows and no groups."""
    raw = (item or {}).get(KEY) or {}
    return not raw.get('groups') and bool(raw.get('rifts'))


def rows_resolved(plan, item):
    """[(at, indexInLevel, unit, count)] of the older rows, highest first, with the plan's
    numbered spawners; a row naming no unstable rift is left out."""
    ups = unstable_spawners(plan)
    out = []
    for r in ((item or {}).get(KEY) or {}).get('rifts') or []:
        index = int((ups.get(r.get('spawner')) or {}).get('index') or 0)
        if index > 0:
            out.append((round(float(r['at']), 4), index, r.get('unit'),
                        int(r.get('count') or COUNT_DEFAULT)))
    return sorted(out, key=lambda x: -x[0])


def rows_arrays(plan, item):
    """{RiftAt, RiftWave, RiftIndex} of the older rows (RiftWave 0: no wave condition)."""
    rows = rows_resolved(plan, item)
    if not rows:
        return {}
    return {'RiftAt': [r[0] for r in rows], 'RiftWave': [0 for _r in rows],
            'RiftIndex': [r[1] for r in rows]}


def rows_spawner_fields(plan, item, guids):
    """{indexInLevel: (Guid, count)} the older rows write on their spawners (the first row
    naming a spawner wins)."""
    out = {}
    for _at, index, unit, count in rows_resolved(plan, item):
        if unit in guids and index not in out:
            out[index] = (tuple(guids[unit]), count)
    return out


def write_rows_spawners(builder, plan, item, report):
    """Write each older row's units on its placed spawner: SpawnProtodata, MinSpawnCount,
    MaxSpawnCount, nothing else. {index: count}."""
    from riftlord.mapeditor import controllers
    from riftlord.mapeditor import planactors
    want = rows_spawner_fields(plan, item, unit_guids())
    by_index = {}
    for a in builder.actor_list():
        if a > 0 and controllers._class_name(builder.pkg, a) == planactors.UNSTABLE_CLASS:
            by_index.setdefault(_read_props(builder.pkg, a).get('indexInLevel', 1), a)
    out = {}
    for index, (guid, count) in sorted(want.items()):
        spawner = by_index.get(index)
        if not spawner:
            report['skipped'].append('the Crystal\'s rift %d is not placed' % index)
            continue
        builder.set_property(spawner, SPAWN_PROTO, 'StructProperty',
                             proto_ptr(builder, guid), PROTO_STRUCT)
        builder.set_property(spawner, SPAWN_MIN, 'IntProperty', struct.pack('<i', count))
        builder.set_property(spawner, SPAWN_MAX, 'IntProperty', struct.pack('<i', count))
        out[index] = count
    return out


def spawner_fields(level, index):
    """{SpawnProtodata: Guid, MinSpawnCount, MaxSpawnCount, and any SPAWN_DEFAULTS field
    written} of the unstable rift with indexInLevel `index` in a built level, read back."""
    from riftlord.mapeditor import controllers
    from riftlord.mapeditor import kitactors as K
    from riftlord.mapeditor import planactors
    pkg = level.pkg
    names = K._names(pkg)
    for a in level.actor_list():
        if a <= 0 or controllers._class_name(pkg, a) != planactors.UNSTABLE_CLASS:
            continue
        data = pkg.exports[a - 1]['data']
        props = _read_props(pkg, a)
        if props.get('indexInLevel', 1) != index:
            continue
        out = {k: props[k] for k in (SPAWN_MIN, SPAWN_MAX) + SPAWN_DEFAULTS if k in props}
        for name, _num, kind, _s, at, size, _e in K._walk(data, names):
            if name == SPAWN_PROTO and kind == 'StructProperty' and size >= 49 + 16:
                out[SPAWN_PROTO] = struct.unpack_from('<4i', data, at + 49)
        return out
    return None


#: The plan key naming the level's own placed Crystal a plan item stands for (adopt).
LEVEL_KEY = 'level'


def adopt(level_name):
    """The plan item for the Crystal a built level places (a level opened without its record),
    or None: its transform, its settings read back (rifttear.level_view) with each Unstable
    Rift as the level's own (spawner -indexInLevel), and LEVEL_KEY naming it."""
    from riftlord.mapeditor import rifttear
    from riftlord.onslaught import onslaught
    try:
        pieces = onslaught.map_report(level_name).get('pieces') or []
    except (Exception, SystemExit):                  # noqa: BLE001
        return None
    p = next((x for x in pieces if x.get('cls') == CLASS), None)
    if not p or not p.get('location'):
        return None
    view = rifttear.level_view(p.get('arrays') or {}, p.get('scalars') or {},
                               p.get('spawnerUnits'))
    for g in view['groups']:
        for r in g['rifts']:
            r['spawner'] = -abs(int(r['spawner']))
    settings = {'groups': view['groups'], HEALTH_KEY: view['health']['values'],
                'riftGap': view['riftGap']}
    if view.get('waveCount'):
        settings['waveCount'] = view['waveCount']
    yaw = (p.get('rotation') or [0, 0, 0])[1]
    return {'kind': KIND, 'name': CLASS, LEVEL_KEY: p['export'],
            'matrix': yaw_matrix([round(v, 2) for v in p['location']], yaw), KEY: settings}


def level_rifts(level_name):
    """[{id: -indexInLevel, label}] of a level's own Unstable Rifts, as an adopted Crystal's
    groups name them."""
    from riftlord.mapeditor import planactors
    from riftlord.onslaught import onslaught
    try:
        rep = onslaught.map_report(level_name)
    except (Exception, SystemExit):                  # noqa: BLE001
        return []
    return [{'id': -int(sp['index']), 'label': 'Unstable Rift %d' % sp['index']}
            for sp in sorted(rep['spawners'], key=lambda s: s['index'])
            if sp.get('cls') == planactors.UNSTABLE_CLASS
            and (sp.get('location') or (0, 0, 0))[2] > planactors.PARKED[2] / 2]


def place(builder, item, rename, plan, report):
    """Place the plan's crystal (called after planactors.emit): its package index, or None
    (reported)."""
    from riftlord.build import apply as applier
    from riftlord.mapeditor import rifttear
    if item.get(LEVEL_KEY):
        #: the level's own Crystal (adopt): written over in place, its earlier arrays cleared
        actor = next((a for a, p in placed(builder) if p.get('_name') == item[LEVEL_KEY]), None)
        if actor is None:
            report['skipped'].append('%s not placed: the level has no %s'
                                     % (LABEL, item[LEVEL_KEY]))
            return None
        for name, _k in rifttear.ARRAYS:
            builder.clear_property(actor, name)
        builder.clear_property(actor, rifttear.WAVE_COUNT)
        rename = item[LEVEL_KEY]
    else:
        try:
            actor = _place(builder, rename, report)
        except SystemExit as exc:
            report['skipped'].append('%s not placed: %s' % (LABEL, exc))
            return None
    for prop, value in float_properties(item).items():
        builder.set_property(actor, prop, 'FloatProperty', struct.pack('<f', value))
    #: the user's HitX / HitY / HitDrop; a fitted one is written after the walkable graph is final
    #: (crystalfit.install)
    from riftlord.mapeditor import crystalfit
    for prop, value in crystalfit.user_half(item).items():
        builder.set_property(actor, prop, 'FloatProperty', struct.pack('<f', value))
    for prop, vals in health_array(item).items():
        builder.set_property(actor, prop, 'ArrayProperty', struct.pack('<i', len(vals)) +
                             b''.join(struct.pack('<f', x) for x in vals), 'FloatProperty')
    v = values(item)
    if uses_rows(item):
        got = rows_arrays(plan, item)
        report.setdefault('crystal', {})['spawners'] = write_rows_spawners(builder, plan, item,
                                                                          report)
    else:
        got = rifttear.arrays(v, plan, unit_guids()) if rifttear.groups(v) else {}
        for line in rifttear.warnings(v):
            report.setdefault('warnings', []).append(line)
    rifttear.write(builder, actor, got)
    where, rot, _scale = applier.transform_of(item['matrix'])
    applier.place_whole(builder, actor, location=where, rotation=rot)
    report.setdefault('crystal', {})['placed'] = rename
    report['crystal']['groups'] = len(got.get('GroupStart') or [])
    return actor


def proto_ptr(builder, guid):
    """An FOMDSoftProtoPtr payload: its tagged Guid (a 16-byte native struct) and None."""
    raw = struct.pack('<4i', *guid)
    tag = struct.pack('<ii', builder.pkg.add_name('Guid'), 0)
    tag += struct.pack('<ii', builder.pkg.add_name('StructProperty'), 0)
    tag += struct.pack('<ii', len(raw), 0)
    tag += struct.pack('<ii', builder.pkg.add_name('Guid'), 0) + b'\0' * 16 + b'\0'
    return tag + raw + struct.pack('<ii', builder.pkg.add_name('None'), 0)




# -- gates -------------------------------------------------------------------------------------

def _read_props(pkg, index):
    """{name: value} of one export's floats, ints and float/int arrays."""
    from riftlord.mapeditor import kitactors as K
    names = K._names(pkg)
    data = pkg.exports[index - 1]['data']
    out = {}
    for name, _num, kind, start, at, size, _e in K._walk(data, names):
        if kind == 'FloatProperty':
            out[name] = round(struct.unpack_from('<f', data, at)[0], 4)
        elif kind == 'IntProperty':
            out[name] = struct.unpack_from('<i', data, at)[0]
        elif kind == 'ArrayProperty':
            inner = names[struct.unpack_from('<i', data, start + 24)[0]]
            n = struct.unpack_from('<i', data, at)[0]
            if inner == 'FloatProperty':
                out[name] = [round(v, 4) for v in struct.unpack_from('<%df' % n, data, at + 4)]
            elif inner == 'IntProperty':
                out[name] = list(struct.unpack_from('<%di' % n, data, at + 4))
    return out


def placed(level):
    """[(actor index, {property: value})] of the crystals placed in a built level (a
    levelbuild.Builder)."""
    from riftlord.build import levelbuild
    from riftlord.mapeditor import controllers
    pkg = level.pkg
    out = []
    for actor in level.actor_list():
        if actor > 0 and controllers._class_name(pkg, actor) == CLASS:
            out.append((actor, _read_props(pkg, actor)))
            out[-1][1]['_name'] = levelbuild._name_of(pkg, actor - 1)
    return out


def spawner_indices(level):
    """{export name: (class name, indexInLevel)} of the spawners in a built level."""
    from riftlord.build import levelbuild
    from riftlord.mapeditor import controllers
    pkg = level.pkg
    out = {}
    for actor in level.actor_list():
        if actor <= 0:
            continue
        cls = controllers._class_name(pkg, actor) or ''
        if 'AISpawner' in cls:
            out[levelbuild._name_of(pkg, actor - 1)] = (cls, _read_props(pkg, actor).get(
                'indexInLevel', 1))
    return out


def notes_game_type(notes_text, mission_row):
    """The GameType the notes give `mission_row`, or ''."""
    from riftlord.missions import customcampaign
    for e in customcampaign.parse_notes(notes_text or '').get('Maps') or []:
        if e.get('Mission') == mission_row:
            return e.get('GameType') or ''
    return ''


def gates(files_, plan, mission_row=None):
    """[(name, ok, detail)]: the crystal is placed once with its class preloaded and its
    natives its own, is in the Level's create-before-serialize group, carries the plan's
    properties, opens rifts at the indices the level gave them, needs (not carries) the mod,
    and the notes give the mission its game type."""
    from riftlord.mapeditor import kitactors as K
    from riftlord.mapeditor import controllers
    from riftlord.mapeditor import planactors
    item = piece(plan)
    if not item:
        return []
    out = []
    _name, level = K._level_of(files_)
    if level is None:
        return [('Crystal: the pak has a level', False, 'no Maps/<name>/<name>.umap')]
    got = placed(level)
    bad = []
    for actor, _vals in got:
        bad += ['%d: %s' % (actor, p) for p in controllers._actor_problems(level, actor)]
        bad += ['%d: %s' % (actor, p) for p in K._component_problems(level, actor)]
    out.append(('Crystal placed (%d of 1), class and default object preloaded, components '
                'belong to it' % len(got), len(got) == 1 and not bad, '; '.join(bad[:3])))
    if not got:
        return out
    actor, vals = got[0]
    lmeta = level.pkg.exports[level._level_export()[0]]['meta']
    first = struct.unpack_from('<i', lmeta, 84)[0]
    counts = struct.unpack_from('<4i', lmeta, 88)
    cbs = level.pkg.preload[first + counts[0]:first + counts[0] + counts[1]] if first >= 0 else []
    out.append(('Crystal in the Level\'s create-before-serialize group', actor in cbs,
                vals['_name']))
    natives = [levelbuild_name(level, i + 1) for i, e in enumerate(level.pkg.exports)
               if struct.unpack_from('<i', e['meta'], 12)[0] == actor]
    want_natives = parent_components()
    out.append(('Crystal components are the trap base\'s (%d)' % len(natives),
                sorted(natives) == want_natives,
                '' if sorted(natives) == want_natives else 'have %s; want %s' % (
                    ', '.join(sorted(natives)), ', '.join(want_natives))))
    spawners = spawner_indices(level)
    rift_cls = planactors.UNSTABLE_CLASS
    #: each plan spawner's indexInLevel as the built level gives it (planactors.emit names
    #: plan spawner n RiftLordSpawner<n>)
    order = {int(sp['id']): n for n, sp in enumerate(plan.get('spawners') or [])
             if isinstance(sp, dict) and sp.get('id') is not None}

    def built_index(sid):
        n = order.get(sid)
        got = spawners.get('RiftLordSpawner%d' % n) if n is not None else None
        return got[1] if got and got[0] == rift_cls else None
    from riftlord.mapeditor import rifttear
    v = values(item)
    members = rifttear.member_ids(v)
    unbuilt = [sid for sid in members if built_index(sid) is None]
    out.append(('every Rift Tear rift is a placed unstable rift (%d)' % len(members),
                not unbuilt, 'not placed: plan spawner %s' % ', '.join(map(str, unbuilt))
                if unbuilt else ''))
    #: what the build should have written, with the indexInLevel the built level gave
    built = dict(plan, spawners=[dict(sp, index=built_index(int(sp['id'])) or 0)
                                 if isinstance(sp, dict) and sp.get('id') is not None else sp
                                 for sp in plan.get('spawners') or []])
    guids = unit_guids()
    want = written_properties(built, item, guids)
    want = {k: x for k, x in want.items() if not isinstance(x, list) or x}
    shown = {k: x for k, x in vals.items()
             if k in {p for _k, p, _d in FLOAT_VARS} | {MIN_VAR, HPP_VAR, DIFFICULTY_HEALTH}}
    shown.update({k: x for k, x in rifttear.read(level.pkg, actor).items() if x})
    protos = ('UnitProto', 'RowProto')
    for key in protos:
        if key in shown:
            shown[key] = [tuple(g) for g in shown[key]]
    out.append(('Crystal properties and Rift Tear arrays read back (%s)' % (', '.join(
        '%s=%s' % (k, want[k]) for k in sorted(want) if k not in protos) or 'class defaults'),
        shown == want, '' if shown == want else 'read %s' % json.dumps(
            {k: [list(g) for g in x] if k in protos else x for k, x in shown.items()})))
    #: HitX / HitY: the user's, else the fit on the built level's walkable graph (crystalfit)
    from riftlord.mapeditor import crystalfit
    out.append(crystalfit.gate(files_[_name], files_[_name[:-5] + '.uexp'], item, vals))
    groups_ = rifttear.groups(v)
    out.append(('every Rift Tear group has rifts and minions (%d)' % len(groups_),
                all(g['rifts'] and rifttear.sends_minions(g) for g in groups_),
                ', '.join(g['name'] for g in groups_
                          if not (g['rifts'] and rifttear.sends_minions(g)))))
    indices = sorted({i for i in (built_index(s) for s in members) if i})
    from riftlord.missions import missionevents
    closed = missionevents.rifts_in_level(files_[_name], files_[_name[:-5] + '.uexp'])
    opened = [i for i in indices if closed.get(i, True)]
    out.append(('Crystal\'s unstable rifts placed closed', not opened,
                ', '.join('%d %s' % (i, 'missing' if i not in closed else 'starts open')
                          for i in opened)))
    #: MaxAliveCount above 0 keeps that many alive, respawning at once: never on these
    alive = []
    for a in level.actor_list():
        if a > 0 and controllers._class_name(level.pkg, a) == rift_cls:
            props = _read_props(level.pkg, a)
            if props.get('indexInLevel', 1) in indices and props.get('MaxAliveCount', 0) > 0:
                alive.append('%d: MaxAliveCount %d' % (props.get('indexInLevel', 1),
                                                       props['MaxAliveCount']))
    out.append(('Crystal\'s unstable rifts keep MaxAliveCount 0', not alive, ', '.join(alive)))
    starts, bad = start_points(_name, level, indices)
    out.append(('Crystal\'s unstable rifts start at a placed path point of this level (%s)'
                % (', '.join('%d %s' % (i, starts[i]) for i in sorted(starts)) or 'none'),
                not bad, '; '.join(bad[:3])))
    if uses_rows(item):
        #: older rows: each rift's units on its spawner, and nothing else written there
        wrong, read = [], []
        for index, (guid, count) in sorted(rows_spawner_fields(built, item, guids).items()):
            got_f = spawner_fields(level, index) or {}
            want_f = {SPAWN_PROTO: guid, SPAWN_MIN: count, SPAWN_MAX: count}
            read.append('%d: %s' % (index, ', '.join('%s=%s' % (k, got_f[k]) for k in sorted(got_f)
                                                     if k != SPAWN_PROTO)))
            if got_f != want_f:
                wrong.append('%d: read %s' % (index, got_f))
        out.append(('Crystal rifts\' spawners carry their units and leave %s at the class '
                    'default (%s)' % ('/'.join(SPAWN_DEFAULTS), '; '.join(read) or 'none'),
                    not wrong, '; '.join(wrong[:3])))
    #: the onslaught sends nothing through a rift the Crystal opens
    through = []
    coord = next((k for k in files_ if k.endswith('_Onslaught01.uasset')), None)
    if coord and indices:
        from riftlord.onslaught import flowsim
        ran = flowsim.units_run(blobs=(files_[coord], files_[coord[:-7] + '.uexp']),
                                spawners=max(indices) + 1, named=False)
        through = sorted('wave %d rift %d' % k for k in ran if k[1] in indices)
    out.append(('no onslaught spawn call through a rift the Crystal opens', not through,
                ', '.join(through[:6])))
    carried = sorted(k for k in files_ if k.startswith('OMD/Content/Mods/CrystalDefense/'))
    out.append(('Crystal Defense not carried (the mission requires %s)' % pak_name(),
                not carried, ', '.join(carried[:3])))
    out.append(('%s installed' % pak_name(), bool(pak()), '' if pak() else 'not detected'))
    from riftlord.build import makemod
    notes = makemod.omui_files(files_).get('CustomMaps.yaml')
    text = files_.get(notes, b'').decode('utf-8', 'replace') if notes else ''
    for row in ([mission_row] if mission_row else _mission_rows(files_)) or [None]:
        gt = notes_game_type(text, row)
        out.append(('CustomMaps.yaml gives %s GameType %s' % (row, GAME_TYPE), gt == GAME_TYPE,
                    'GameType %r' % gt))
    return out


START_PROP = 'StartingPathPoint'
POINT_CLASS = 'OMDPathPoint'


def _soft_path(pkg, data, at):
    """(asset path, sub-path) of a SoftObjectProperty payload at `at`."""
    from riftlord.build import makelevel
    idx, number = struct.unpack_from('<ii', data, at)
    n = struct.unpack_from('<i', data, at + 8)[0]
    sub = data[at + 12:at + 12 + n - 1].decode('ascii', 'replace') if n > 0 else ''
    return makelevel._full(pkg.names[idx][0], number), sub


def start_points(umap_key, level, indices):
    """({indexInLevel: path point export}, [problems]): the StartingPathPoint of each unstable
    rift in `indices`, which must name this level's World (`<package>.<leaf>`, the name
    makelevel gives it, or the kept name of a numbered World; makelevel.self_ref_problems
    checks the World export carries it) and a placed OMDPathPoint in its PersistentLevel."""
    from riftlord.build import levelbuild
    from riftlord.build import makelevel
    from riftlord.mapeditor import controllers
    from riftlord.mapeditor import kitactors as K
    from riftlord.mapeditor import planactors
    pkg = level.pkg
    names = K._names(pkg)
    own = '/Game/' + umap_key[len('OMD/Content/'):-len('.umap')]
    base, number = makelevel.world_name(pkg)
    world = '%s.%s' % (own, makelevel._full(base, number) if number else own.rsplit('/', 1)[-1])
    actors = [a for a in level.actor_list() if a > 0]
    points = {levelbuild._name_of(pkg, a - 1) for a in actors
              if controllers._class_name(pkg, a) == POINT_CLASS}
    found, bad = {}, []
    for a in actors:
        if controllers._class_name(pkg, a) != planactors.UNSTABLE_CLASS:
            continue
        index = _read_props(pkg, a).get('indexInLevel', 1)
        if index not in indices:
            continue
        data = pkg.exports[a - 1]['data']
        ref = next((_soft_path(pkg, data, at) for name, _n, kind, _s, at, _z, _e
                    in K._walk(data, names) if name == START_PROP
                    and kind == 'SoftObjectProperty'), None)
        if ref is None:
            bad.append('%d: no %s' % (index, START_PROP))
            continue
        asset, sub = ref
        head, _sep, point = sub.partition('.')
        if asset != world:
            bad.append('%d: names %s, not %s' % (index, asset, world))
        elif head != 'PersistentLevel' or point not in points:
            bad.append('%d: %s is not a placed %s' % (index, sub, POINT_CLASS))
        else:
            found[index] = point
    for index in sorted(set(indices) - set(found) - {int(b.split(':')[0]) for b in bad}):
        bad.append('%d: not placed' % index)
    return found, bad


def levelbuild_name(level, index):
    from riftlord.build import levelbuild
    return levelbuild._name_of(level.pkg, index - 1)


def _mission_rows(files_):
    """The mission row names of a mission pak (its MissionProtos table's rows: the mission
    and its Endless twin)."""
    from riftlord.missions import customcampaign
    for key in sorted(files_):
        if key.endswith('_MissionProtos.uasset') and '/Tables/' in key \
                and key[:-7] + '.uexp' in files_:
            t = customcampaign._table(files_[key], files_[key[:-7] + '.uexp'])
            if t.rows:
                return [r['name'] for r in t.rows]
    return []


def check_pak(path, plan, mission_row=None):
    """gates() on a built pak file."""
    from riftlord.core import paksource
    return gates(paksource.entries(path), plan, mission_row)


# -- offline check -----------------------------------------------------------------------------

#: The level `--check` builds on.
CHECK_LEVEL = 'Scenario_02'
#: The plan path point id the check plan's rifts start at.
CHECK_POINT = 9


def check_plan():
    """A plan with two unstable rifts starting at an added path point on to the exit, and a
    crystal over the rift with a Rift Tear group of both: a co-op-only rift, two minion
    rows, a health trigger opening one and a wave trigger (with health, both) opening two
    with launch timing, repeating every 2 waves; the floats off their defaults."""
    matrix = spot(CHECK_LEVEL)['matrix']

    def rift(sid, at):
        return {'id': sid, 'at': at, 'rotation': 0.0, 'kind': 'ground', 'index': 0,
                'maxSpawnPerRow': 0, 'door': None, 'start': CHECK_POINT, 'flyerStart': None,
                'unstable': True}
    group = {'name': 'North-west', 'difficulty': 5, 'players': 0,
             'rifts': [{'spawner': 7, 'difficulty': 5, 'players': 0},
                       {'spawner': 8, 'difficulty': 3, 'players': 2}],
             'minions': [{'unit': 'OrcLight', 'count': 3, 'addCount': 0},
                         {'unit': 'OrcMedium', 'count': 2, 'addCount': 1}],
             'triggers': [{'at': 75.0, 'wave': None, 'alive': 0.0, 'both': False, 'open': 1,
                           'launchAfter': 0.0, 'launchAlive': 0.0, 'repeat': 0},
                          {'at': 40.0, 'wave': 3, 'alive': 0.0, 'both': True, 'open': 2,
                           'launchAfter': 20.0, 'launchAlive': 30.0, 'repeat': 2}]}
    return {'level': CHECK_LEVEL, 'version': 2,
            'points': [{'id': CHECK_POINT, 'at': [3000.0, 6800.0, 465.0], 'reachRadius': 300.0,
                        'formationSize': 3, 'formationSpacing': 0.0, 'flightHeight': 0.0,
                        'flyersOnly': False, 'groundOnly': False, 'next': []}],
            'spawners': [rift(7, [2150.0, 6950.0, 537.0]), rift(8, [3850.0, 6950.0, 527.0])],
            'added': [{'kind': KIND, 'name': CLASS, 'matrix': matrix,
                       KEY: {HEALTH_KEY: [800.0, 1500.0, 2500.0, 3000.0],
                             'bobHeight': 40.0, 'bobSeconds': 6.0,
                             'riftGap': 3.0, 'groups': [group]}}]}


def check(out_dir, log=print, plan=None):
    """Save the check plan (or `plan`) and read it back, build it into a copy of CHECK_LEVEL
    (out_dir/CrystalCheck_Map01.umap and .uexp), and run pkgcheck, the import gate, the
    Level preload gate and the crystal's own gates (all but the notes'). Returns failures."""
    import tempfile
    from riftlord.build.navigation import navpatch
    from riftlord.core import pkgcheck
    from riftlord.build import apply as applier
    from riftlord.build import levelbuild
    from riftlord.mapeditor import leveldata
    from riftlord.mapeditor import probe
    fails = []
    plan = plan or check_plan()
    keep = leveldata.PLANS
    leveldata.PLANS = tempfile.mkdtemp(prefix='plans-', dir=out_dir)
    try:
        leveldata.save_plan(CHECK_LEVEL, plan)
        back = leveldata.load_plan(CHECK_LEVEL)
    finally:
        leveldata.PLANS = keep
    want = clean_piece(plan['added'][0], plan['added'][0]['matrix'])
    got = piece(back)
    if not got or got.get(KEY) != want[KEY]:
        fails.append('save changed the crystal: %s' % json.dumps(got)[:200])
    log('save round trip: %s' % ('ok' if not fails else '; '.join(fails)))
    stem = 'OMD/Content/Mods/CrystalCheck/Maps/CrystalCheck_Map01'
    fetched = navpatch.fetch_map(navpatch.map_path(CHECK_LEVEL), out_dir)
    with open(fetched['.umap'], 'rb') as fh:
        ua0 = fh.read()
    with open(fetched['.uexp'], 'rb') as fh:
        ux0 = fh.read()
    ua, ux, report = applier.apply_plan(ua0, ux0, CHECK_LEVEL,
                                        '/Game/Mods/CrystalCheck/Maps/CrystalCheck_Map01',
                                        'CrystalCheck_Map01', plan=back)
    files_ = {stem + '.umap': ua, stem + '.uexp': ux}
    for line in report['skipped']:
        log('  skipped: %s' % line)
        fails.append('skipped: %s' % line)
    bad = pkgcheck.problems(ua, ux, 'CrystalCheck_Map01')
    log('pkgcheck: %s' % ('ok' if not bad else bad[:3]))
    fails += ['pkgcheck: %s' % b for b in bad]
    checked, missing = probe.import_problems(files_, stem + '.umap')
    log('%s  every import names an export, string and number (%d)%s'
        % ('PASS' if not missing else 'FAIL', checked,
           (': ' + '; '.join(missing[:3])) if missing else ''))
    fails += ['imports: %s' % m for m in missing]
    lb = levelbuild.Builder.around(ua, ux)
    gaps = lb.level_preload_gaps()
    log('%s  the Level preloads every listed actor' % ('PASS' if not gaps else 'FAIL'))
    if gaps:
        fails.append('%d listed actors not preloaded' % len(gaps))
    sp = (back.get('spawners') or [{}])[0]
    log('  unstable rift numbered %s' % sp.get('index'))
    from riftlord.build import trailers
    for name, ok, detail in gates(files_, back) + trailers.gates(files_, back):
        if name.startswith('CustomMaps.yaml'):
            continue                                 # no notes outside a mission build
        log('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + detail) if detail else ''))
        if not ok:
            fails.append(name)
    with open(os.path.join(out_dir, 'CrystalCheck_Map01.umap'), 'wb') as fh:
        fh.write(ua)
    with open(os.path.join(out_dir, 'CrystalCheck_Map01.uexp'), 'wb') as fh:
        fh.write(ux)
    return fails


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--check', metavar='DIR', help='build a crystal into a copy of %s offline'
                    % CHECK_LEVEL)
    ap.add_argument('--spot', metavar='LEVEL', help='print where a crystal goes on LEVEL')
    ap.add_argument('--gates', nargs=2, metavar=('PAK', 'PLAN'),
                    help='run the gates on a built mission pak against its plan (.json)')
    args = ap.parse_args(argv)
    if args.spot:
        print(json.dumps(editor_info(args.spot), indent=1))
    if args.gates:
        with open(args.gates[1], encoding='utf-8') as fh:
            plan = json.load(fh)
        rows = check_pak(args.gates[0], plan.get('plan', plan))
        for name, ok, detail in rows:
            print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + detail) if detail else ''))
        return 0 if all(r[1] for r in rows) else 1
    if args.check:
        os.makedirs(args.check, exist_ok=True)
        fails = check(args.check)
        print('%d failure(s)' % len(fails))
        return 1 if fails else 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
