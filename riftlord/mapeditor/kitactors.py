"""Actors of classes no shipped level places: Treasure Chests, the Stamina Potion and the
game's own pickups, as New Object items.

* **Treasure Chest** (`BP_TreasureChest_C`, Mods/TreasureChests): an Internal environmental
  trap, a child of `BP_OMDTrapInteractable_Base_C` as the Cauldron is. It is placed as a
  shipped cauldron placement retargeted to the chest (`_place_chest`): the cauldron's own
  components are left out, the ones the interactable base gives every child are kept, and
  the native components' templates name the base's default object. The plan item carries
  `chest` {group, chance, coin, coinChance, skulls, skullChance}: spots sharing a group are
  one chest (the server keeps one spot at random and removes the others).
* **Stamina Potion** (`BP_Pickup_StaminaPotion_C`, inside OrcsMustUI since 2026-10-22: read
  from the installed LogicMods/OrcsMustUI.pak, never copied into a mission) and the shipped
  pickups (`/Game/Pickups/BP`): children of `BP_Pickup_Base_C`. No shipped level places a
  pickup, so the placed actor is written from the class (`_place_pickup`): the native
  components of its default object and the components its construction scripts add, laid
  out as a shipped level lays out a placed Blueprint actor.

Neither the chest nor the potion is copied into a mission: both classes are read from the
installed mods (riftlord.core.supportedmods: the paks carrying TreasureChests and
OrcsMustUI), and a mission placing one requires that mod.

    python3 -m riftlord.kitactors --check DIR      # save, build and gate every class offline
"""

import argparse
import copy
import functools
import hashlib
import io
import json
import os
import struct
from collections.abc import Mapping
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: Kits read for the class layout and the editor's meshes, by supported mod id
#: (riftlord.core.supportedmods): never copied into a mission.
INSTALLED_KITS = ('OrcsMustUI', 'TreasureChests', 'CrystalDefense')


class _Kits(dict):
    """{kit: {'pak'}}: the supported mod's file where it is expected, resolved when read."""

    def __missing__(self, key):
        from riftlord.core import supportedmods
        if key not in INSTALLED_KITS:
            raise KeyError(key)
        return {'pak': supportedmods.file_path(key) or ''}

    def __contains__(self, key):
        return key in INSTALLED_KITS


KITS = _Kits()

CHEST = 'BP_TreasureChest_C'
POTION = 'BP_Pickup_StaminaPotion_C'
TRAPS_GROUP = 'Environmental Traps'
PICKUPS_GROUP = 'Pickups'

#: The shipped placement a chest is retargeted from, and the class both share.
CHEST_DONOR = 'BP_Cauldron_C'
CHEST_ANCESTOR = ('/Game/Traps/_Base/BP/BP_OMDTrapInteractable_Base', 'BP_OMDTrapInteractable_Base_C')

#: Every placeable class: its package, the kit that ships it (None: the game), New Object
#: name and group, and the instance properties a placed one is written with.
#: A placed pickup keeps no lifetime: `DestroyTimer` 0 (BP_Pickup_Base's BeginPlay sets
#: the life span to it, and a life span of 0 is none); the potions ship 180. The stamina
#: potion skips that BeginPlay and its toss when `Expires` is off.
CLASSES = {
    CHEST: {'package': '/Game/Mods/TreasureChests/BP_TreasureChest', 'kit': 'TreasureChests',
            'label': 'Treasure Chest', 'group': TRAPS_GROUP, 'props': ()},
    POTION: {'package': '/Game/Mods/OrcsMustUI/BP_Pickup_StaminaPotion', 'kit': 'OrcsMustUI',
             'label': 'Stamina Potion', 'group': PICKUPS_GROUP,
             'props': (('Expires', 'BoolProperty', False),)},
    'BP_Pickup_Coin_C': {'package': '/Game/Pickups/BP/BP_Pickup_Coin', 'label': 'Coin'},
    'BP_Pickup_CoinLarge_C': {'package': '/Game/Pickups/BP/BP_Pickup_CoinLarge',
                              'label': 'Large Coin'},
    'BP_Pickup_MoltenGoldCoin_C': {'package': '/Game/Pickups/BP/BP_Pickup_MoltenGoldCoin',
                                   'label': 'Molten Gold Coin'},
    'BP_Pickup_MoltenLargeGoldCoin_C': {
        'package': '/Game/Pickups/BP/BP_Pickup_MoltenLargeGoldCoin',
        'label': 'Large Molten Gold Coin'},
    'BP_Pickup_Skull_C': {'package': '/Game/Pickups/BP/BP_Pickup_Skull', 'label': 'Skull'},
    'BP_Pickup_HealthPotion_C': {'package': '/Game/Pickups/BP/BP_Pickup_HealthPotion',
                                 'label': 'Health Potion',
                                 'props': (('DestroyTimer', 'FloatProperty', 0.0),)},
    'BP_Pickup_ManaPotion_C': {'package': '/Game/Pickups/BP/BP_Pickup_ManaPotion',
                               'label': 'Mana Potion',
                               'props': (('DestroyTimer', 'FloatProperty', 0.0),)},
}
#: The Chandelier (riftlord/mapeditor/chandelier.py): carried in each mission that places it,
#: placed by its module; the Chandelier Trap compound hangs one on a chain.
from riftlord.mapeditor import chandelier as _chandelier  # noqa: E402
CLASSES[_chandelier.CLASS] = {'package': _chandelier.PACKAGE, 'label': _chandelier.NAME,
                              'group': TRAPS_GROUP, 'carried': True}
for _cls, _rec in CLASSES.items():
    _rec.setdefault('kit', None)
    _rec.setdefault('group', PICKUPS_GROUP)
    _rec.setdefault('props', ())
    _rec.setdefault('carried', False)

#: Plan key of a chest's settings, and their defaults (Mods/TreasureChests/Spots.yaml).
CHEST_KEY = 'chest'
CHEST_DEFAULTS = {'group': '', 'chance': 100.0, 'coin': [250, 750], 'coinChance': 75.0,
                  'skulls': [1, 3], 'skullChance': 25.0, 'coinLean': 0.5}
#: The chest's CoinBias range (kit 2026-10-23): the mean share of CoinMin..CoinMax paid.
#: 0.5 is uniform and is not written; the editor's Coin Lean 0..1 is clamped to this.
COIN_BIAS_RANGE = (0.05, 0.95)
COIN_LEAN_DEFAULT = 0.5
#: The chest's Blueprint variables, by plan field.
CHEST_VARS = (('group', 'Group'), ('chance', 'Chance'), ('coin', ('CoinMin', 'CoinMax')),
              ('coinChance', 'CoinChance'), ('skulls', ('SkullMin', 'SkullMax')),
              ('skullChance', 'SkullChance'))
INT_MAX = 2 ** 31 - 1
#: Group names the editor makes: "Chest 1", "Chest 2", ...
GROUP_MAX = 64

#: The chest's lid hangs here on its bottom (TreasureChest.py ReceiveBeginPlay, `Hung`).
CHEST_LID_AT = (-32.0, 0.0, 40.0)
#: The kit meshes the editor draws, by leaf name.
CHEST_MESHES = ('SM_Chest_Bottom', 'SM_Chest_Top')
POTION_MESH = 'SM_Bottle1'

#: Velocity a placed pickup's ProjectileMovement starts with: none. BP_Pickup_Base's
#: ProjectileMovement (InitialSpeed 500) would otherwise send it 500 uu/s along its facing
#: (UProjectileMovementComponent::InitializeComponent); still, it settles under gravity.
PICKUP_VELOCITY = (0.0, 0.0, 0.0)

#: Export flags of a placed actor, its native components and its construction-script
#: components (Scenario_06's cauldron placement: RF_Transactional, | RF_DefaultSubObject).
FLAGS_ACTOR, FLAGS_NATIVE, FLAGS_SCS = 0x00000008, 0x00040008, 0x00000000
CREATION_ENUM = 'EComponentCreationMethod'
CREATION_SCS = 'EComponentCreationMethod::SimpleConstructionScript'


def handles(cls):
    """Whether `cls` is placed by this module."""
    return cls in CLASSES


# -- kits ---------------------------------------------------------------------------------------

_KIT_CACHE = {}


class _PakFiles(Mapping):
    """{inner path: bytes} of a pak, each file read when first asked for (a kit's pak may be
    a merged mod pak of hundreds of MB)."""

    def __init__(self, path):
        from riftlord.formats.ue import pak
        self._pak = pak.Pak(path)
        self._keys = {k[k.index('OMD/'):] if 'OMD/' in k else k: k for k in self._pak.files}
        self._read = {}

    def __getitem__(self, inner):
        if inner not in self._read:
            self._read[inner] = self._pak.read_file(self._keys[inner])
        return self._read[inner]

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)

    def __contains__(self, inner):
        return inner in self._keys


def kit_files(key):
    """{inner path: bytes} of an installed kit pak, read again whenever the file changes;
    SystemExit when it is not installed."""
    path = KITS[key]['pak']
    try:
        st = os.stat(path)
    except OSError:
        raise SystemExit('%s is not installed (%s)' % (key, path or 'no game folder'))
    stamp = (st.st_size, st.st_mtime_ns)
    hit = _KIT_CACHE.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    files = _PakFiles(path)
    _KIT_CACHE[key] = (stamp, files)
    return files


def inner(game_path):
    """'/Game/X/Y' -> 'OMD/Content/X/Y'."""
    return 'OMD/Content' + game_path[len('/Game'):]


def kits_used(plan):
    """Sorted kit keys the plan's added items need."""
    out = set()
    for item in (plan or {}).get('added') or []:
        rec = CLASSES.get(item.get('name')) if item.get('kind') == 'kit' else None
        if rec and rec['kit']:
            out.add(rec['kit'])
    return sorted(out)


# -- class packages ---------------------------------------------------------------------------

def class_package(path, kit=None):
    """A Blueprint package by /Game path, from its kit or the game, payloads loaded."""
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    if kit:
        files = kit_files(kit)
        stem = inner(path)
        if stem + '.uasset' not in files:
            raise SystemExit('the %s kit has no %s' % (kit, path))
        pkg = Package(files[stem + '.uasset'], files[stem + '.uexp'])
    else:
        pkg = Package(*gamefiles.package(path))
    pkg.load_payloads()
    return pkg


def _walk(data, names):
    """Tagged properties of one export body: [(name, number, kind, start, payload at, size,
    end)], stopping at None."""
    out, o = [], 0
    while o + 8 <= len(data):
        ni, num = struct.unpack_from('<ii', data, o)
        if not 0 <= ni < len(names):
            raise SystemExit('bad property name at %d' % o)
        name = names[ni]
        if name == 'None':
            return out
        kind = names[struct.unpack_from('<i', data, o + 8)[0]]
        size = struct.unpack_from('<i', data, o + 16)[0]
        head = {'BoolProperty': 26, 'StructProperty': 49, 'ArrayProperty': 33,
                'ByteProperty': 33, 'EnumProperty': 33, 'SetProperty': 33,
                'MapProperty': 41}.get(kind, 25)
        if kind == 'BoolProperty':
            size = 0
        out.append((name, num, kind, o, o + head, size, o + head + size))
        o += head + size
    raise SystemExit('property stream has no None')


def _names(pkg):
    return [n for n, _h in pkg.names]


def _meta(pkg, i, at):
    return struct.unpack_from('<i', pkg.exports[i]['meta'], at)[0]


def _export_name(pkg, i):
    return pkg.names[_meta(pkg, i, 16)][0], _meta(pkg, i, 20)


def _import_rec(pkg, index):
    """(class package, class name, outer, object name, number) of import `index` (< 0)."""
    rec = pkg.imports[-index - 1]
    nm = pkg.names
    return (nm[struct.unpack_from('<i', rec, 0)[0]][0], nm[struct.unpack_from('<i', rec, 8)[0]][0],
            struct.unpack_from('<i', rec, 16)[0], nm[struct.unpack_from('<i', rec, 20)[0]][0],
            struct.unpack_from('<i', rec, 24)[0])


def _props(pkg, i):
    """{name: (kind, raw payload, number)} of export i (zero-based), first of a name wins."""
    data = pkg.exports[i]['data'] or b''
    out = {}
    for name, num, kind, _s, at, size, _e in _walk(data, _names(pkg)):
        key = name if not num else '%s_%d' % (name, num - 1)
        if kind == 'BoolProperty':
            out.setdefault(key, (kind, data[at - 2:at - 1], num))
        else:
            out.setdefault(key, (kind, data[at:at + size], num))
    return out


def _cdo(pkg):
    """Zero-based index of a Blueprint package's default object."""
    for i in range(len(pkg.exports)):
        if _export_name(pkg, i)[0].startswith('Default__'):
            return i
    raise SystemExit('no class default object')


def kit_problem(cls):
    """None when `cls` needs no supported mod or its mod is detected with a sound class
    package (riftlord.core.supportedmods), else why it cannot be read."""
    kit = CLASSES[cls]['kit']
    if not kit:
        return None
    from riftlord.core import supportedmods
    feature = supportedmods.feature_of({'kind': 'kit', 'name': cls})
    if feature and not supportedmods.available(feature):
        return '%s is not installed or is an incompatible version' % kit
    return None


def _class_stamp(cls):
    """What a class's cached reading depends on: its kit pak's size and time, or None.
    SystemExit when its kit cannot be read (kit_problem)."""
    kit = CLASSES[cls]['kit']
    if not kit:
        return None
    bad = kit_problem(cls)
    if bad:
        raise SystemExit(bad)
    kit_files(kit)
    return _stamp(kit)


def chain(cls):
    """[(package path, class name, Package)] of `cls` and its Blueprint parents, leaf first;
    the first native class ends it. Read again when the class's kit changes."""
    return _chain(cls, _class_stamp(cls))


@functools.lru_cache(maxsize=32)
def _chain(cls, _stamp_key):
    rec = CLASSES[cls]
    out = []
    path, name, kit = rec['package'], cls, rec['kit']
    for _hop in range(8):
        try:
            pkg = class_package(path, kit)
        except SystemExit:
            if not kit:
                raise
            pkg = class_package(path)
        out.append((path, name, pkg))
        tmpl = _meta(pkg, _cdo(pkg), 8)
        if tmpl >= 0:
            break
        cp, cn, outer, obj, _num = _import_rec(pkg, tmpl)
        if cp.startswith('/Script/'):
            break
        path, name, kit = cp, cn, None
    return out


#: Component classes that are not SceneComponents, for when bpkit's type cache is absent
#: (the frozen app): the ones the placeable classes' construction scripts add.
NOT_SCENE = frozenset({'ProjectileMovementComponent', 'RotatingMovementComponent',
                       'TimelineComponent', 'OMDMapDisplayComponent',
                       'OMDAbilitySystemComponent', 'OMDStatusEffectComponent',
                       'AIPerceptionComponent', 'AIPerceptionStimuliSourceComponent'})


@functools.lru_cache(maxsize=1)
def _scene_classes():
    """Every class name that is a SceneComponent (bpkit typedb), or None without its cache."""
    from bpkit import typedb
    if not os.path.exists(typedb.CACHE):
        return None
    classes = typedb.load()['classes']
    out = set()
    for name in classes:
        x, seen = name, 0
        while x and seen < 32:
            if x == 'SceneComponent':
                out.add(name)
                break
            x = (classes.get(x) or {}).get('super')
            seen += 1
    return out


def is_scene(cls):
    known = _scene_classes()
    return cls in known if known is not None else cls not in NOT_SCENE


def _ref_name(pkg, index):
    """(name, number) of an export (> 0) or import (< 0)."""
    if index > 0:
        return _export_name(pkg, index - 1)
    rec = _import_rec(pkg, index)
    return rec[3], rec[4]


def layout(cls):
    """What a placed actor of `cls` holds (_layout), read again when its kit changes."""
    return _layout(cls, _class_stamp(cls))


@functools.lru_cache(maxsize=32)
def _layout(cls, _stamp_key):
    """What a placed actor of `cls` holds, from its class packages:

    {'natives': [{name, number, cls, clsPackage, owner: (path, class), attach}],
     'root': native root name, 'pointers': [(property, component name)],
     'scs': [{name, number, cls, clsPackage, template, owner: (path, class), attach,
              socket}]}  -- construction-script components in creation order."""
    links = chain(cls)
    natives, root, pointers = [], None, []
    for path, name, pkg in links:
        cdo = _cdo(pkg)
        subs = [i for i in range(len(pkg.exports)) if _meta(pkg, i, 12) == cdo + 1]
        if not subs:
            continue
        for i in subs:
            nm, num = _export_name(pkg, i)
            ccls = _import_rec(pkg, _meta(pkg, i, 0))
            cpkg = _import_rec(pkg, ccls[2])[3] if ccls[2] < 0 else '/Script/Engine'
            attach = _props(pkg, i).get('AttachParent')
            parent = None
            if attach and attach[0] == 'ObjectProperty':
                v = struct.unpack_from('<i', attach[1])[0]
                parent = _ref_name(pkg, v) if v else None
            natives.append({'name': nm, 'number': num, 'cls': ccls[3], 'clsPackage': cpkg,
                            'owner': (path, name), 'attach': parent})
        for key, (kind, raw, _num) in _props(pkg, cdo).items():
            if kind != 'ObjectProperty' or len(raw) != 4:
                continue
            v = struct.unpack_from('<i', raw)[0]
            if v > 0 and v - 1 in subs:
                target = _export_name(pkg, v - 1)
                if key == 'RootComponent':
                    root = target
                pointers.append((key, target))
        break
    if root is None:
        raise SystemExit('%s: no native root component' % cls)
    scs = []
    by_var = {}
    for path, name, pkg in reversed(links):
        nm = _names(pkg)
        handler = [i for i in range(len(pkg.exports)) if _meta(pkg, i, 0) < 0
                   and _import_rec(pkg, _meta(pkg, i, 0))[3] == 'InheritableComponentHandler']
        for i in handler:
            if _props(pkg, i).get('Records'):
                raise SystemExit('%s overrides inherited components, which is not handled'
                                 % name)
        script = next((i for i in range(len(pkg.exports))
                       if _meta(pkg, i, 0) < 0
                       and _import_rec(pkg, _meta(pkg, i, 0))[3] == 'SimpleConstructionScript'),
                      None)
        if script is None:
            continue
        sp = _props(pkg, script)
        default_root = struct.unpack_from('<i', sp['DefaultSceneRootNode'][1])[0] \
            if 'DefaultSceneRootNode' in sp else 0

        def array(raw):
            n = struct.unpack_from('<i', raw)[0]
            return list(struct.unpack_from('<%di' % n, raw, 4)) if n else []

        def node(index, parent):
            p = _props(pkg, index - 1)
            var = p['InternalVariableName'][1]
            vname = nm[struct.unpack_from('<i', var)[0]]
            vnum = struct.unpack_from('<i', var, 4)[0]
            ccls = _import_rec(pkg, struct.unpack_from('<i', p['ComponentClass'][1])[0])
            cpkg = _import_rec(pkg, ccls[2])[3] if ccls[2] < 0 else '/Script/Engine'
            tmpl = struct.unpack_from('<i', p['ComponentTemplate'][1])[0]
            if parent is None and 'ParentComponentOrVariableName' in p:
                pv = p['ParentComponentOrVariableName'][1]
                pname = (nm[struct.unpack_from('<i', pv)[0]], struct.unpack_from('<i', pv, 4)[0])
                native = bool(p.get('bIsParentComponentNative', ('', b'\0'))[1][0])
                parent = pname if native else by_var.get(pname)
            if parent is None:
                parent = root
            socket = None
            if 'AttachToName' in p:
                sv = p['AttachToName'][1]
                socket = (nm[struct.unpack_from('<i', sv)[0]], struct.unpack_from('<i', sv, 4)[0])
            rec = {'name': vname, 'number': vnum, 'cls': ccls[3], 'clsPackage': cpkg,
                   'template': _export_name(pkg, tmpl - 1), 'owner': (path, name),
                   'attach': parent if is_scene(ccls[3]) else None, 'socket': socket}
            scs.append(rec)
            by_var[(vname, vnum)] = (vname, vnum)
            for child in array(p['ChildNodes'][1]) if 'ChildNodes' in p else []:
                node(child, (vname, vnum))

        for r in array(sp['RootNodes'][1]) if 'RootNodes' in sp else []:
            if r == default_root:
                continue                 # a native root exists, so the default root is skipped
            node(r, None)
    return {'natives': natives, 'root': root, 'pointers': pointers, 'scs': scs}


# -- placing ------------------------------------------------------------------------------------

def _fstring(text):
    raw = text.encode('ascii') + b'\0'
    return struct.pack('<i', len(raw)) + raw


def _import_chain(builder, path, cls):
    """(package import, class import, default object import) of a Blueprint class."""
    pkg = builder._import('/Script/CoreUObject', 'Package', 0, path)
    klass = builder._import('/Script/Engine', 'BlueprintGeneratedClass', pkg, cls)
    cdo = builder._import(path, cls, pkg, 'Default__' + cls)
    return pkg, klass, cdo


def _class_import(builder, module, cls):
    """The import of a native class `cls` in package `module` (/Script/...)."""
    owner = builder._import('/Script/CoreUObject', 'Package', 0, module)
    return builder._import('/Script/CoreUObject', 'Class', owner, cls)


def _new_export(builder, klass, template, outer, name, number, flags):
    """Append an export with an empty property stream; its package index."""
    meta = struct.pack('<iiiiiiIqqiii16sIiiiiiii', klass, 0, template, outer,
                       builder.pkg.add_name(name), number, flags, 0, 0, 0, 0, 0, b'\0' * 16,
                       0, 1, 0, -1, 0, 0, 0, 0)
    data = struct.pack('<ii', builder.pkg.add_name('None'), 0) + b'\0' * 4
    builder.pkg.exports.append({'meta': meta, 'data': data, 'size': len(data), 'offset': 0})
    return len(builder.pkg.exports)


def _set_groups(builder, index, groups):
    """Write an export's four preload groups (SbS, CbS, SbC, CbC) at the table's end."""
    pkg = builder.pkg
    meta = bytearray(pkg.exports[index - 1]['meta'])
    if any(groups):
        struct.pack_into('<i', meta, 84, len(pkg.preload))
        for g in groups:
            pkg.preload.extend(g)
    else:
        struct.pack_into('<i', meta, 84, -1)
    struct.pack_into('<4i', meta, 88, *[len(g) for g in groups])
    pkg.preload_count = len(pkg.preload)
    pkg.exports[index - 1]['meta'] = bytes(meta)


def _add_object(builder, index, name, number, target):
    """Add an ObjectProperty whose tag name carries an FName number."""
    builder.add_property(index, name, 'ObjectProperty', struct.pack('<i', target))
    if number:
        data = bytearray(builder.pkg.exports[index - 1]['data'])
        at = builder._terminator(data)
        #: the tag just spliced in ends at the terminator: its name is 29 bytes before it
        struct.pack_into('<i', data, at - 29 + 4, number)
        builder.pkg.exports[index - 1]['data'] = bytes(data)


def _place_pickup(builder, cls, rename):
    """Write a placed actor of a pickup class (no donor): its package index."""
    from riftlord.build import levelbuild
    lay = layout(cls)
    links = chain(cls)
    leaf_path = links[0][0]
    _p, klass, cdo = _import_chain(builder, leaf_path, cls)
    owners = {}

    def owner(path, name):
        if (path, name) not in owners:
            owners[(path, name)] = _import_chain(builder, path, name)
        return owners[(path, name)]
    level_index = builder._level_export()[0] + 1
    made = {}                                        # (name, number) -> export
    order = []
    for n in lay['natives']:
        ccls = _class_import(builder, n['clsPackage'], n['cls'])
        tmpl = builder._import(n['clsPackage'], n['cls'], owner(*n['owner'])[2], n['name'],
                               n['number'])
        made[(n['name'], n['number'])] = _new_export(builder, ccls, tmpl, 0, n['name'],
                                                     n['number'], FLAGS_NATIVE)
        n = dict(n, export=made[(n['name'], n['number'])], classImport=ccls, templateImport=tmpl)
        order.append(('native', n))
    for s in lay['scs']:
        ccls = _class_import(builder, s['clsPackage'], s['cls'])
        tmpl = builder._import(s['clsPackage'], s['cls'], owner(*s['owner'])[1],
                               s['template'][0], s['template'][1])
        made[(s['name'], s['number'])] = _new_export(builder, ccls, tmpl, 0, s['name'],
                                                     s['number'], FLAGS_SCS)
        order.append(('scs', dict(s, export=made[(s['name'], s['number'])], classImport=ccls,
                                  templateImport=tmpl)))
    actor = _new_export(builder, klass, cdo, level_index, rename, 0, FLAGS_ACTOR)
    #: every component's outer is the actor
    for _kind, c in order:
        meta = bytearray(builder.pkg.exports[c['export'] - 1]['meta'])
        struct.pack_into('<i', meta, levelbuild.OUTER_AT, actor)
        builder.pkg.exports[c['export'] - 1]['meta'] = bytes(meta)
    #: each component ends as its class serializes (riftlord.build.trailers)
    from riftlord.build import trailers
    written = {c['export'] - 1 for _k, c in order} | {actor - 1}
    for _kind, c in order:
        trailers.set_trailer(builder, c['export'], c['cls'], c['name'], written)
    created = []
    for kind, c in order:
        parent = made.get(tuple(c['attach'])) if c.get('attach') else None
        if c.get('attach') and not parent:
            raise SystemExit('%s: %s attaches to %s, which is not placed'
                             % (cls, c['name'], c['attach'][0]))
        if parent:
            builder.set_object(c['export'], 'AttachParent', parent)
        if kind == 'native':
            _set_groups(builder, c['export'],
                        [[], ([parent] if parent else []) + [cdo, klass],
                         [c['classImport'], c['templateImport']], [actor]])
            continue
        if c.get('socket'):
            builder.set_property(c['export'], 'AttachSocketName', 'NameProperty',
                                 struct.pack('<ii', builder.pkg.add_name(c['socket'][0]),
                                             c['socket'][1]))
        builder.add_property(c['export'], 'UCSSerializationIndex', 'IntProperty',
                             struct.pack('<i', 0))
        builder.set_bool(c['export'], 'bNetAddressable', True)
        builder.add_property(c['export'], 'CreationMethod', 'EnumProperty',
                             struct.pack('<ii', builder.pkg.add_name(CREATION_SCS), 0),
                             CREATION_ENUM)
        if c['cls'] == 'ProjectileMovementComponent':
            builder.set_vector(c['export'], 'Velocity', PICKUP_VELOCITY)
        _set_groups(builder, c['export'],
                    [[], ([parent] if parent else []) + [owner(*c['owner'])[1]],
                     [c['classImport'], c['templateImport']], [actor]])
        created.append(c['export'])
    for kind, c in order:
        if kind == 'scs':
            _add_object(builder, actor, c['name'], c['number'], c['export'])
    for prop, target in lay['pointers']:
        if prop != 'RootComponent' and tuple(target) in made:
            builder.add_property(actor, prop, 'ObjectProperty', struct.pack('<i', made[tuple(target)]))
    builder.add_property(actor, 'RootComponent', 'ObjectProperty',
                         struct.pack('<i', made[tuple(lay['root'])]))
    if created:
        builder.set_object_array(actor, 'BlueprintCreatedComponents', created)
    _set_groups(builder, actor,
                [[], [c['export'] for _k, c in order],
                 [klass, cdo] + [c['templateImport'] for k, c in order if k == 'native'],
                 [level_index]])
    builder.register_actor(actor)
    return actor


def _chest_view(src, j, path, cls=CHEST, ancestor=CHEST_ANCESTOR):
    """A copy of donor package `src` whose actor `j` (zero-based, a placed child of the
    chest's ancestor) is the chest: class and default object the chest's, native component
    templates the ancestor's default object's, the donor class's own components left out.
    `cls` and `ancestor` ((package, class)) retarget another class the same way (the
    Crystal, riftlord/mapeditor/crystal.py). Returns (view, [dropped component names])."""
    from riftlord.build import levelbuild
    view = copy.copy(src)
    view.names = list(src.names)
    view.imports = list(src.imports)
    view.exports = [dict(e) for e in src.exports]
    view.preload = list(src.preload)
    donor_class = _meta(view, j, 0)
    donor_cdo = _meta(view, j, 8)
    if donor_class >= 0 or donor_cdo >= 0:
        raise SystemExit('the chest donor is not a placed Blueprint actor')
    closure, frontier = [], {j + 1}
    while frontier:
        nxt = set()
        for k in range(len(view.exports)):
            if k + 1 not in frontier and k not in closure and k != j \
                    and _meta(view, k, levelbuild.OUTER_AT) in frontier:
                closure.append(k)
                nxt.add(k + 1)
        frontier = nxt

    def under_donor(v):
        return v < 0 and _import_rec(view, v)[2] == donor_class

    def groups(k):
        first = _meta(view, k, 84)
        counts = struct.unpack_from('<4i', view.exports[k]['meta'], 88)
        out, at = [], first
        for n in counts:
            out.append(list(view.preload[at:at + n]) if first >= 0 else [])
            at += n
        return out
    dropped = set()
    for k in closure:
        if under_donor(_meta(view, k, 8)) or any(under_donor(v) for g in groups(k) for v in g):
            dropped.add(k)
    grew = True
    while grew:
        grew = False
        for k in closure:
            if k not in dropped and _meta(view, k, levelbuild.OUTER_AT) - 1 in dropped:
                dropped.add(k)
                grew = True
    kept = [k for k in closure if k not in dropped]
    # the chest's own imports, and the ancestor's for the native component templates
    pkg_imp = view.add_import('/Script/CoreUObject', 'Package', 0, path)
    chest_class = view.add_import('/Script/Engine', 'BlueprintGeneratedClass', pkg_imp, cls)
    chest_cdo = view.add_import(path, cls, pkg_imp, 'Default__' + cls)
    apath, aclass = ancestor
    anc_pkg = view.add_import('/Script/CoreUObject', 'Package', 0, apath)
    anc_cdo = view.add_import(apath, aclass, anc_pkg, 'Default__' + aclass)
    remap = {donor_class: chest_class, donor_cdo: chest_cdo}
    for i in range(len(src.imports)):
        cp, cn, outer, obj, num = _import_rec(view, -(i + 1))
        if outer == donor_cdo:
            got = view.add_import(cp, cn, anc_cdo, obj)
            if num:
                rec = bytearray(view.imports[-got - 1])
                struct.pack_into('<i', rec, 24, num)
                view.imports[-got - 1] = bytes(rec)
            remap[-(i + 1)] = got
    names = _names(view)
    for k in kept + [j]:
        meta = bytearray(view.exports[k]['meta'])
        for at in (0, 8):
            v = struct.unpack_from('<i', meta, at)[0]
            struct.pack_into('<i', meta, at, remap.get(v, v))
        new = []
        for gi, g in enumerate(groups(k)):
            out = []
            for v in g:
                if v > 0 and v - 1 in dropped:
                    continue
                if k == j and gi == 1 and v < 0:
                    continue             # the actor's create-before-serialize: its components
                v = remap.get(v, v)
                if under_donor(v) or v == donor_cdo:
                    raise SystemExit('the chest still depends on %s' % _ref_name(view, v)[0])
                out.append(v)
            new.append(out)
        struct.pack_into('<i', meta, 84, len(view.preload) if any(new) else -1)
        for g in new:
            view.preload.extend(g)
        struct.pack_into('<4i', meta, 88, *[len(g) for g in new])
        view.exports[k]['meta'] = bytes(meta)
        if k != j:
            data = view.exports[k]['data'] or b''
            for name, _num, kind, _s, at, size, _e in _walk(data, names):
                if kind == 'ObjectProperty' and size == 4:
                    v = struct.unpack_from('<i', data, at)[0]
                    if v > 0 and v - 1 in dropped:
                        raise SystemExit('kept %s names dropped %s'
                                         % (_export_name(view, k)[0], _export_name(view, v - 1)[0]))
    # the actor keeps its pointers to kept components and the list of what its
    # construction scripts made; the donor class's variables and OnEndPlay go
    data = view.exports[j]['data']
    body = bytearray()
    for name, num, kind, start, at, size, end in _walk(data, names):
        if kind == 'ObjectProperty' and size == 4:
            v = struct.unpack_from('<i', data, at)[0]
            if v > 0 and v - 1 in kept:
                body += data[start:end]
        elif name == 'BlueprintCreatedComponents' and kind == 'ArrayProperty':
            n = struct.unpack_from('<i', data, at)[0]
            items = [v for v in struct.unpack_from('<%di' % n, data, at + 4)
                     if v > 0 and v - 1 in kept]
            tag = bytearray(data[start:at])
            struct.pack_into('<i', tag, 16, 4 + 4 * len(items))
            body += tag + struct.pack('<i', len(items)) + b''.join(struct.pack('<i', v) for v in items)
    tail = data[_walk(data, names)[-1][6]:] if _walk(data, names) else data
    view.exports[j]['data'] = bytes(body) + bytes(tail)
    for k in dropped:
        meta = bytearray(view.exports[k]['meta'])
        struct.pack_into('<i', meta, levelbuild.OUTER_AT, 0)
        view.exports[k]['meta'] = bytes(meta)
    return view, sorted('%s' % _export_name(view, k)[0] for k in dropped)


def _place_chest(builder, rename, report):
    """Write a placed chest, retargeted from the catalogue's cauldron placement."""
    from riftlord.build import apply as applier
    got = applier.donor_for(CHEST_DONOR)
    if not got:
        raise SystemExit('no %s placement to retarget a chest from' % CHEST_DONOR)
    src, j = got
    #: the chest's class must be readable (the kit) before it is named
    chain(CHEST)
    view, dropped = _chest_view(src, j, CLASSES[CHEST]['package'])
    actor = builder.lift_actor(view, j, rename=rename)
    #: the class's own component layout: no donor instance transforms or trigger size
    applier.reset_component_transforms(builder, actor)
    for i, e in enumerate(builder.pkg.exports):
        if struct.unpack_from('<i', e['meta'], 12)[0] == actor:
            builder.clear_property(i + 1, 'BoxExtent')
    report.setdefault('kitactors', {}).setdefault('chestDropped', dropped)
    return actor


def chest_values(item):
    """A chest item's settings with the defaults filled in."""
    got = dict(CHEST_DEFAULTS)
    got.update({k: v for k, v in (item.get(CHEST_KEY) or {}).items() if k in CHEST_DEFAULTS})
    return got


def group_values(plan):
    """{group: settings} from each group's first spot in the plan (a group is one chest)."""
    out = {}
    for item in (plan or {}).get('added') or []:
        if item.get('kind') == 'kit' and item.get('name') == CHEST:
            v = chest_values(item)
            if v['group'] and v['group'] not in out:
                out[v['group']] = v
    return out


def _write_chest(builder, actor, values):
    builder.set_property(actor, 'Group', 'StrProperty', _fstring(values['group']))
    builder.set_property(actor, 'Chance', 'FloatProperty', struct.pack('<f', values['chance']))
    builder.set_property(actor, 'CoinMin', 'IntProperty', struct.pack('<i', values['coin'][0]))
    builder.set_property(actor, 'CoinMax', 'IntProperty', struct.pack('<i', values['coin'][1]))
    builder.set_property(actor, 'CoinChance', 'FloatProperty',
                         struct.pack('<f', values['coinChance']))
    builder.set_property(actor, 'SkullMin', 'IntProperty', struct.pack('<i', values['skulls'][0]))
    builder.set_property(actor, 'SkullMax', 'IntProperty', struct.pack('<i', values['skulls'][1]))
    builder.set_property(actor, 'SkullChance', 'FloatProperty',
                         struct.pack('<f', values['skullChance']))
    bias = coin_bias(values)
    if bias is not None:
        builder.set_property(actor, 'CoinBias', 'FloatProperty', struct.pack('<f', bias))


def coin_bias(values):
    """The CoinBias a chest is written with, or None for the class default (uniform)."""
    lean = float(values.get('coinLean', COIN_LEAN_DEFAULT))
    if abs(lean - COIN_LEAN_DEFAULT) < 1e-6:
        return None
    return min(COIN_BIAS_RANGE[1], max(COIN_BIAS_RANGE[0], lean))


def place(builder, item, rename, plan, report):
    """Place one added item of a CLASSES class: its package index, or None (reported)."""
    from riftlord.build import apply as applier
    cls = item['name']
    try:
        if cls == _chandelier.CLASS:
            actor = _chandelier.place(builder, item, rename, plan, report)
        elif cls == CHEST:
            actor = _place_chest(builder, rename, report)
            values = chest_values(item)
            if values['group']:
                values = group_values(plan).get(values['group'], values)
            _write_chest(builder, actor, values)
        else:
            actor = _place_pickup(builder, cls, rename)
            for name, kind, value in CLASSES[cls]['props']:
                if kind == 'BoolProperty':
                    builder.set_bool(actor, name, value)
                elif kind == 'FloatProperty':
                    builder.set_property(actor, name, kind, struct.pack('<f', value))
    except SystemExit as exc:
        from riftlord.core import buildmessage
        report['skipped'].append(buildmessage.left_out(
            'a %s' % buildmessage.piece_label({'name': cls}), str(exc), verb='place'))
        return None
    where, rot, scale = applier.transform_of(item['matrix'])
    applier.place_whole(builder, actor, location=where, rotation=rot, scale=scale)
    return actor


# -- the plan -----------------------------------------------------------------------------------

def _number(raw, lo, hi, label, whole=False):
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ValueError('%s must be a number' % label)
    if v != v or not lo <= v <= hi:
        raise ValueError('%s must be between %g and %g' % (label, lo, hi))
    return int(round(v)) if whole else round(v, 4)


def clean(item, raw):
    """Copy a chest item's settings from `raw` into the saved `item`, validated."""
    if item.get('name') != CHEST or not isinstance(raw.get(CHEST_KEY), dict):
        return item
    r = raw[CHEST_KEY]
    out = {}
    group = str(r.get('group') or '').strip()
    if len(group) > GROUP_MAX or not all(32 <= ord(c) < 127 for c in group):
        raise ValueError('a chest group name is up to %d plain characters' % GROUP_MAX)
    out['group'] = group
    out['chance'] = _number(r.get('chance', CHEST_DEFAULTS['chance']), 0, 100, 'Chance')
    for key, label in (('coin', 'Coin'), ('skulls', 'Skull')):
        pair = r.get(key, CHEST_DEFAULTS[key])
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError('%s needs a min and a max' % label)
        lo = _number(pair[0], 0, INT_MAX, label + ' Min', True)
        hi = _number(pair[1], 0, INT_MAX, label + ' Max', True)
        out[key] = [min(lo, hi), max(lo, hi)]
    out['coinChance'] = _number(r.get('coinChance', CHEST_DEFAULTS['coinChance']), 0, 100,
                                'Coin %')
    out['skullChance'] = _number(r.get('skullChance', CHEST_DEFAULTS['skullChance']), 0, 100,
                                 'Skull %')
    out['coinLean'] = _number(r.get('coinLean', COIN_LEAN_DEFAULT), 0, 1, 'Coin Lean')
    item[CHEST_KEY] = out
    return item


# -- the editor's view ---------------------------------------------------------------------

def _kit_mesh(kit, leaf):
    """The /Game path of a kit mesh by its leaf name, or None."""
    for k in kit_files(kit):
        if k.endswith('/%s.uasset' % leaf) and '/Meshes/' in k:
            return '/Game' + k[len('OMD/Content'):-len('.uasset')]
    return None


def mesh_key(kit, game_path):
    """The `/api/mesh/` name of a kit mesh: `kit/<Kit>/<path under /Game/>`."""
    return 'kit/%s/%s' % (kit, game_path[len('/Game/'):])


def view_parts(cls):
    """parts(cls), and for a shipped pickup its particle visual (pickup_visual: its mesh
    particles, else its sprite as a `sprite` part): what the Map Editor draws (/api/kit)
    and previews. Builds, minimaps and outlines use parts(), which has no particles."""
    if cls in CLASSES and not CLASSES[cls]['kit'] and not CLASSES[cls]['carried']:
        try:
            return copy.deepcopy(pickup_visual(cls))
        except (Exception, SystemExit):              # noqa: BLE001
            return []
    return parts(cls)


def parts(cls):
    """[{mesh, component, loc, rot, scale}] of a class's meshes: the chest's two meshes and
    the stamina potion's bottle; a shipped pickup has none (view_parts draws it). [] when
    its kit cannot be read (kit_problem) or does not parse."""
    if kit_problem(cls):
        return []
    if CLASSES.get(cls, {}).get('carried'):
        return _chandelier.parts()
    try:
        if cls == CHEST:
            bottom, top = (_kit_mesh('TreasureChests', m) for m in CHEST_MESHES)
            out = []
            if bottom:
                out.append({'mesh': mesh_key('TreasureChests', bottom), 'component': 'Bottom',
                            'loc': [0.0, 0.0, 0.0], 'rot': [0.0, 0.0, 0.0], 'scale': [1.0] * 3})
            if top:
                out.append({'mesh': mesh_key('TreasureChests', top), 'component': 'Lid',
                            'loc': list(CHEST_LID_AT), 'rot': [0.0, 0.0, 0.0],
                            'scale': [1.0] * 3})
            return out
        if cls == POTION:
            bottle = _kit_mesh('OrcsMustUI', POTION_MESH)
            props = _props(chain(POTION)[0][2], _cdo(chain(POTION)[0][2]))
            scale = struct.unpack_from('<f', props['Scale'][1])[0] if 'Scale' in props else 1.0
            return [{'mesh': mesh_key('OrcsMustUI', bottle), 'component': 'Bottle',
                     'loc': [0.0, 0.0, 0.0], 'rot': [0.0, 0.0, 0.0],
                     'scale': [scale] * 3}] if bottle else []
    except (Exception, SystemExit):                  # noqa: BLE001
        return []
    return []


def catalogue_rows():
    """New Object rows for every class here (leveldata.catalogue); each card's preview is
    /api/thumb/kit:<class> (thumbnail). A class from a supported mod carries `feature`
    (riftlord.core.supportedmods): the editor shows its card only while that is on."""
    from riftlord.core import supportedmods
    from riftlord.mapeditor import kitdonors
    rows = []
    for cls, rec in CLASSES.items():
        row = {'name': cls, 'kind': 'kit', 'category': 'actors', 'group': rec['group'],
               'label': rec['label'], 'thumb': kitdonors.THUMB_NAME + cls,
               'cells': [0, 0, 0], 'trapgrid': False, 'parts': len(parts(cls)),
               'surfaces': [], 'lights': []}
        feature = supportedmods.feature_of({'kind': 'kit', 'name': cls})
        if feature:
            row['feature'] = feature
        rows.append(row)
    return rows


# -- pickup visuals ---------------------------------------------------------------------------
#
# A shipped pickup (/Game/Pickups/BP) has no mesh component: what the player sees is its
# ParticleSystemComponent's Cascade system (P_PickupCoin, P_PickupPotionHealth, ...). The
# editor draws LOD 0 of that system: every mesh emitter (ParticleModuleTypeDataMesh: the
# potions' Jar) as its mesh, scaled by ParticleModuleSize StartSize; a system with no mesh
# emitter (coins, skull) as its first sprite emitter, a camera-facing card StartSize X by
# Y uu of its material's texture (the first texture the material instance assigns, else
# the first the material imports). A sprite is tinted by ParticleModuleColorOverLife
# times the material's EmissiveColor parameter, scaled so its largest channel is 1. Each
# range (StartSize, StartLocation, ColorOverLife) is taken at the middle of its cooked
# MinValueVec..MaxValueVec. Placed at the component's RelativeLocation plus the emitter's
# StartLocation, times the component's RelativeScale3D.

#: Bump when pickup_visual or a preview changes (the preview cache names carry it).
VISUAL_VERSION = 2
#: Preview size in pixels and colours (omd3thumb's).
THUMB_SIZE = 96
THUMB_BACKGROUND = (27, 31, 39)
THUMB_TINT = (196, 170, 128)
THUMB_TINT_TOP = max(THUMB_TINT)
#: Share of the preview a sprite fills.
SPRITE_FILL = 0.9


def _vec(raw):
    return list(struct.unpack_from('<3f', raw)) if len(raw) >= 12 else [0.0, 0.0, 0.0]


def _objects(raw):
    n = struct.unpack_from('<i', raw)[0] if len(raw) >= 4 else 0
    return list(struct.unpack_from('<%di' % n, raw, 4)) if n > 0 else []


def _middle(pkg, raw):
    """The middle of a cooked RawDistributionVector's MinValueVec..MaxValueVec."""
    lo = hi = [0.0, 0.0, 0.0]
    for name, _n, kind, _s, at, size, _e in _walk(raw, _names(pkg)):
        if name == 'MinValueVec':
            lo = _vec(raw[at:at + size])
        elif name == 'MaxValueVec':
            hi = _vec(raw[at:at + size])
    return [(a + b) / 2.0 for a, b in zip(lo, hi)]


def _import_path(pkg, index):
    """'/Game/...' package path of import `index` (< 0): its outermost import's name."""
    rec = _import_rec(pkg, index)
    while rec[2] < 0:
        rec = _import_rec(pkg, rec[2])
    return rec[3]


@functools.lru_cache(maxsize=32)
def _emitters(path):
    """[{mesh, material, size, at, colour}] of LOD 0 of each emitter of a particle system:
    `mesh` the mesh emitter's mesh leaf name or None, `material` its RequiredModule
    material's package path or None, `colour` ColorOverLife or None."""
    pkg = class_package(path)
    klass = lambda i: _import_rec(pkg, _meta(pkg, i, 0))[3] if _meta(pkg, i, 0) < 0 else ''  # noqa: E731
    system = next((i for i in range(len(pkg.exports)) if klass(i) == 'ParticleSystem'), None)
    if system is None:
        return []
    out = []
    obj = lambda raw: struct.unpack_from('<i', raw)[0] if len(raw) >= 4 else 0  # noqa: E731
    for em in _objects(_props(pkg, system).get('Emitters', ('', b'', 0))[1]):
        if em <= 0:
            continue
        lods = _objects(_props(pkg, em - 1).get('LODLevels', ('', b'', 0))[1])
        if not lods or lods[0] <= 0:
            continue
        lod = _props(pkg, lods[0] - 1)
        got = {'mesh': None, 'material': None, 'size': [1.0, 1.0, 1.0], 'at': [0.0, 0.0, 0.0],
               'colour': None}
        req = obj(lod.get('RequiredModule', ('', b'', 0))[1])
        if req > 0:
            mat = obj(_props(pkg, req - 1).get('Material', ('', b'', 0))[1])
            if mat < 0:
                got['material'] = _import_path(pkg, mat)
        td = obj(lod.get('TypeDataModule', ('', b'', 0))[1])
        if td > 0 and klass(td - 1) == 'ParticleModuleTypeDataMesh':
            mesh = obj(_props(pkg, td - 1).get('Mesh', ('', b'', 0))[1])
            if mesh < 0:
                got['mesh'] = _import_rec(pkg, mesh)[3]
        for mi in _objects(lod.get('Modules', ('', b'', 0))[1]):
            if mi <= 0:
                continue
            kind, mp = klass(mi - 1), _props(pkg, mi - 1)
            if kind == 'ParticleModuleSize' and 'StartSize' in mp:
                got['size'] = _middle(pkg, mp['StartSize'][1])
            elif kind == 'ParticleModuleLocation' and 'StartLocation' in mp:
                got['at'] = _middle(pkg, mp['StartLocation'][1])
            elif kind == 'ParticleModuleColorOverLife' and 'ColorOverLife' in mp:
                got['colour'] = _middle(pkg, mp['ColorOverLife'][1])
        out.append(got)
    return out


@functools.lru_cache(maxsize=32)
def _sprite_texture(material):
    """'/Game/...' texture a sprite material draws: the first texture its instance
    assigns, else the first Texture2D its package imports; or None."""
    from riftlord.formats.omd3 import omd3mesh
    for _param, tex in omd3mesh.texture_parameters(material):
        if tex and str(tex).startswith('/Game/'):
            return str(tex)
    pkg = class_package(material)
    for k in range(len(pkg.imports)):
        rec = _import_rec(pkg, -k - 1)
        if rec[1] == 'Texture2D':
            return _import_path(pkg, -k - 1)
    return None


def _tint(material, colour):
    """A sprite's colour (module comment), each channel 0..1."""
    from riftlord.formats.omd3 import omd3matlook
    rgb = list(colour or [1.0, 1.0, 1.0])
    try:
        emissive = (omd3matlook.explain(material)[0].get('vectors') or {}).get('EmissiveColor')
    except Exception:                                # noqa: BLE001
        emissive = None
    if emissive:
        rgb = [a * b for a, b in zip(rgb, emissive[:3])]
    top = max(rgb)
    return [round(max(0.0, v / top), 4) for v in rgb] if top > 0 else [1.0, 1.0, 1.0]


def _template_props(cls, comp):
    """The tagged properties of a construction-script component's template."""
    pkg = next(p for path, name, p in chain(cls) if (path, name) == tuple(comp['owner']))
    i = next(i for i in range(len(pkg.exports)) if _export_name(pkg, i) == tuple(comp['template']))
    return pkg, _props(pkg, i)


def light_tint(cls):
    """The colour (0..1 RGB, largest channel 1) of a class's PointLight component, the
    glow a mesh pickup casts (health red, mana blue), or None without one."""
    for comp in layout(cls)['scs']:
        if comp['cls'] != 'PointLightComponent':
            continue
        _pkg, props = _template_props(cls, comp)
        raw = props.get('LightColor', ('', b'', 0))[1]
        if len(raw) >= 3:
            rgb = [raw[2], raw[1], raw[0]]
            top = max(rgb)
            return [round(v / top, 4) for v in rgb] if top else None
    return None


@functools.lru_cache(maxsize=16)
def pickup_visual(cls):
    """[part] a shipped pickup class draws as (module comment): mesh parts `{component,
    mesh, loc, rot, scale, tint}` (tint: light_tint, or None), or one sprite part
    `{component, mesh: None, sprite, tint, loc, rot, scale: [width, height, 1]}`; each
    with `particle` true: drawn only, never painted."""
    lay = layout(cls)
    out, sprites = [], []
    glow = light_tint(cls)
    for comp in lay['scs']:
        if comp['cls'] != 'ParticleSystemComponent':
            continue
        pkg, props = _template_props(cls, comp)
        tmpl = props.get('Template')
        ref = struct.unpack_from('<i', tmpl[1])[0] if tmpl and len(tmpl[1]) >= 4 else 0
        if ref >= 0:
            continue
        loc = _vec(props['RelativeLocation'][1]) if 'RelativeLocation' in props else [0.0] * 3
        rot = _vec(props['RelativeRotation'][1]) if 'RelativeRotation' in props else [0.0] * 3
        scale = _vec(props['RelativeScale3D'][1]) if 'RelativeScale3D' in props else [1.0] * 3
        for em in _emitters(_import_path(pkg, ref)):
            at = [round(loc[k] + scale[k] * em['at'][k], 4) for k in range(3)]
            if em['mesh']:
                out.append({'component': comp['name'], 'mesh': em['mesh'], 'loc': at,
                            'rot': [round(v, 4) for v in rot],
                            'scale': [round(scale[k] * em['size'][k], 5) for k in range(3)],
                            'tint': glow, 'particle': True})
            elif em['material'] and not sprites:
                tex = _sprite_texture(em['material'])
                if tex:
                    sprites.append({'component': comp['name'], 'mesh': None, 'sprite': tex,
                                    'particle': True,
                                    'tint': _tint(em['material'], em['colour']), 'loc': at,
                                    'rot': [0.0, 0.0, 0.0],
                                    'scale': [round(scale[0] * em['size'][0], 4),
                                              round(scale[1] * em['size'][1], 4), 1.0]})
    return out or sprites


def sprites():
    """Every texture a pickup's sprite part names (sprite_png serves only these)."""
    out = set()
    for cls, rec in CLASSES.items():
        if not rec['kit']:
            out.update(p['sprite'] for p in view_parts(cls) if p.get('sprite'))
    return out


def _sprite_image(path, size):
    """A sprite texture as RGBA, largest edge `size`: its own alpha, or (a texture with
    none) its brightness as alpha over white, so the part's tint colours it."""
    from riftlord.core import gamefiles
    from riftlord.formats.ue import texture
    from PIL import Image
    import tempfile
    stem = path.replace('/Game/', '../../../OMD/Content/')
    with tempfile.TemporaryDirectory(prefix='sprite-') as tmp:
        staged = {}
        for ext in ('.uasset', '.uexp', '.ubulk'):
            try:
                data = gamefiles.read(stem + ext)
            except Exception:                        # noqa: BLE001
                continue
            staged[ext] = os.path.join(tmp, 't' + ext)
            with open(staged[ext], 'wb') as fh:
                fh.write(data)
        if '.uasset' not in staged or '.uexp' not in staged:
            return None
        image = texture.to_image(texture.parse(staged['.uasset'], staged['.uexp'],
                                               staged.get('.ubulk')))
    if image is None:
        return None
    image = image.convert('RGBA')
    if image.getextrema()[3][0] == 255:
        grey = image.convert('L')
        image = Image.new('RGBA', image.size, (255, 255, 255, 255))
        image.putalpha(grey)
    if max(image.size) > size:
        k = size / float(max(image.size))
        image = image.resize((max(1, int(image.width * k)), max(1, int(image.height * k))),
                             Image.LANCZOS)
    return image


#: The largest edge a sprite is served at.
SPRITE_MAX = 256


def sprite_png(path, size=SPRITE_MAX):
    """`/api/pickup/sprite?path=<texture>`: a pickup sprite as RGBA PNG, cached; None for
    a texture no pickup sprite names."""
    if path not in sprites():
        return None
    size = max(16, min(SPRITE_MAX, int(size)))
    tag = hashlib.sha1(repr((path, size, VISUAL_VERSION)).encode()).hexdigest()[:20]
    cached = os.path.join(gamepath.scratch('kitview'), 'sprites', tag + '.png')
    if os.path.exists(cached):
        with open(cached, 'rb') as fh:
            return fh.read()
    image = _sprite_image(path, size)
    if image is None:
        return None
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    png = buf.getvalue()
    os.makedirs(os.path.dirname(cached), exist_ok=True)
    with open(cached + '.part', 'wb') as fh:
        fh.write(png)
    os.replace(cached + '.part', cached)
    return png


def _part_model(part):
    """One mesh part in actor space (its loc, rot and scale) with its materials, tinted by
    its `tint` as the viewport tints it (omd3thumb.place), or None."""
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import omd3serve
    from riftlord.mapeditor import omd3thumb
    key = part.get('mesh')
    m = mesh(key) if str(key or '').startswith('kit/') else omd3serve.mesh(key) if key else None
    if not m or not m.get('verts') or not m.get('tris'):
        return None
    t = omd3level._matrix(part['loc'], part['rot'], part['scale'])
    #: the part's tint is sRGB (main.js tintPart); place takes a linear multiplier
    tint = [c ** omd3thumb.GAMMA for c in part['tint']] if part.get('tint') else None
    return omd3thumb.place(m, t, tint=tint)


def thumbnail(cls, size=THUMB_SIZE):
    """A class's New Object preview as PNG, kept on disk: its mesh parts drawn as every
    mesh preview is (omd3thumb), or its sprite in its tint; None when it has neither."""
    from riftlord.mapeditor import omd3serve
    from riftlord.mapeditor import omd3thumb
    from PIL import Image
    if kit_problem(cls):
        return None
    stamp = _stamp(CLASSES[cls]['kit']) if CLASSES[cls]['kit'] else None
    if CLASSES[cls]['carried']:
        stamp = repr(parts(cls))
    tag = hashlib.sha1(repr((cls, size, stamp, VISUAL_VERSION, omd3thumb.STYLE)).encode()
                       ).hexdigest()[:12]
    cached = os.path.join(omd3serve.PREVIEWS, 'kitactor__%s__%s.png'
                          % (omd3serve.safe_name(cls), tag))
    if os.path.exists(cached):
        with open(cached, 'rb') as fh:
            return fh.read()
    got = view_parts(cls)
    image = None
    models = omd3thumb.merge([_part_model(p) for p in got if p.get('mesh')])
    if models:
        glow = next((p['tint'] for p in got if p.get('mesh') and p.get('tint')), None)
        #: an untextured glowing pickup in its light's colour, as bright as the usual tint
        tint = tuple(int(THUMB_TINT_TOP * c) for c in glow) if glow else THUMB_TINT
        image = omd3thumb.render(models, size, tint=tint)
    else:
        sprite = next((p for p in got if p.get('sprite')), None)
        art = _sprite_image(sprite['sprite'], size) if sprite else None
        if art is not None:
            w, h = sprite['scale'][0], sprite['scale'][1]
            fit = SPRITE_FILL * size / max(w, h, 1e-6)
            art = art.resize((max(1, int(w * fit)), max(1, int(h * fit))), Image.LANCZOS)
            r, g, b, a = art.split()
            body = Image.merge('RGB', [Image.eval(ch, lambda v, k=k: int(v * k))
                                       for ch, k in zip((r, g, b), sprite['tint'])])
            image = Image.new('RGB', (size, size), THUMB_BACKGROUND)
            image.paste(body, ((size - art.width) // 2, (size - art.height) // 2), a)
    if image is None:
        return None
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    png = buf.getvalue()
    omd3serve.keep(cached, png)
    return png


@functools.lru_cache(maxsize=1)
def _chest_width():
    """The chest bottom's extent along the actor's Y axis (uu), from its mesh."""
    m = mesh(parts(CHEST)[0]['mesh']) if parts(CHEST) else None
    if not m or not m.get('verts'):
        return 0.0
    ys = [v[1] for v in m['verts']]
    return max(ys) - min(ys)


def choices():
    """What the Map Editor offers for these classes (GET /api/controllers `kitactors`)."""
    return {'chest': CHEST, 'potion': POTION, 'defaults': dict(CHEST_DEFAULTS),
            'groupMax': GROUP_MAX, 'intMax': INT_MAX,
            #: an added spot stands this far along the chest's Y axis: twice its width
            'spotOffset': round(2 * _chest_width(), 1),
            'classes': {cls: {'label': rec['label'], 'group': rec['group']}
                        for cls, rec in CLASSES.items()}}


def _kit_of_key(key):
    rest = str(key)
    if not rest.startswith('kit/'):
        return None, None
    kit, _sep, rel = rest[len('kit/'):].partition('/')
    if kit not in KITS or not rel or '..' in rel.split('/'):
        return None, None
    return kit, '/Game/' + rel


@functools.lru_cache(maxsize=64)
def _mesh_cached(kit, game_path, stamp):
    import numpy as np
    from riftlord.formats.omd3 import omd3smwrite
    files = kit_files(kit)
    stem = inner(game_path)
    t = omd3smwrite.Template(path=game_path, blobs={'.uasset': files[stem + '.uasset'],
                                                    '.uexp': files[stem + '.uexp']})
    d = omd3smwrite.decode(t)
    lod = t.lod0()
    slots = [p for p, _o, _s, _c in t.material_paths()]
    tex = [_bc_texture(kit, p) for p in slots]
    secs = [{'slot': int(s[0]), 'first': int(s[1]), 'tris': int(s[2])} for s in lod['sections']]
    uv = d['uvs'][0] if d['uvs'] else None
    return {'fab': True, 'kit': kit, 'package': game_path,
            'verts': np.round(d['positions'].astype(np.float64), 3).tolist(),
            'tris': d['triangles'].reshape(-1).astype(int).tolist(),
            'uvs': np.round(uv.astype(np.float64), 5).tolist() if uv is not None else None,
            'sections': secs, 'slots': slots, 'slotTextures': tex,
            'texture': next((x for x in tex if x), None),
            'triangles': int(len(d['triangles'])), 'vertices': int(len(d['positions']))}


def _bc_texture(kit, material):
    """`/kit/<Kit>/<path>` of a kit material's base-colour texture, or None."""
    from riftlord.formats.ue.uassetio import Package
    if not material:
        return None
    files = kit_files(kit)
    head = files.get(inner(material) + '.uasset')
    if not head:
        return None
    pkg = Package(head)
    nm = _names(pkg)
    for rec in pkg.imports:
        _cp, _n1, cls, _n2, outer, obj, _n3 = struct.unpack('<7i', rec)
        if outer == 0 and nm[cls] == 'Package' and nm[obj].endswith('_BC'):
            return '/kit/%s/%s' % (kit, nm[obj][len('/Game/'):])
    return None


def _stamp(kit):
    st = os.stat(KITS[kit]['pak'])
    return (st.st_size, st.st_mtime_ns)


def mesh(key):
    """`/api/mesh/kit/<Kit>/<path>`: geometry, UV0, sections and slot textures, or None."""
    kit, game_path = _kit_of_key(key)
    if not kit:
        return None
    try:
        return _mesh_cached(kit, game_path, _stamp(kit))
    except (Exception, SystemExit):                  # noqa: BLE001
        return None


#: The largest edge a kit texture is served at.
TEXTURE_MAX = 2048


def texture_png(path, size):
    """`/api/texture/kit/<Kit>/<path>` as PNG bytes, largest edge `size`, or None."""
    import tempfile
    kit, game_path = _kit_of_key(str(path).lstrip('/'))
    if not kit:
        return None
    size = max(16, min(TEXTURE_MAX, int(size)))
    try:
        from riftlord.core import gamepath
        stamp = _stamp(kit)
        tag = hashlib.sha1(repr((game_path, size, stamp)).encode()).hexdigest()[:20]
        cached = os.path.join(gamepath.scratch('kitview'), kit, tag + '.png')
        if os.path.exists(cached):
            with open(cached, 'rb') as fh:
                return fh.read()
        from riftlord.formats.ue import texture
        files = kit_files(kit)
        stem = inner(game_path)
        with tempfile.TemporaryDirectory(prefix='kittex-') as tmp:
            staged = {}
            for ext in ('.uasset', '.uexp', '.ubulk'):
                if stem + ext in files:
                    staged[ext] = os.path.join(tmp, 't' + ext)
                    with open(staged[ext], 'wb') as fh:
                        fh.write(files[stem + ext])
            image = texture.to_image(texture.parse(staged['.uasset'], staged['.uexp'],
                                                   staged.get('.ubulk')))
        if image is None:
            return None
        image = image.convert('RGB')
        if max(image.size) > size:
            k = size / max(image.size)
            image = image.resize((max(1, int(image.width * k)), max(1, int(image.height * k))))
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        png = buf.getvalue()
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        with open(cached + '.part', 'wb') as fh:
            fh.write(png)
        os.replace(cached + '.part', cached)
        return png
    except (Exception, SystemExit):                  # noqa: BLE001
        return None


# -- checks on a built pak ------------------------------------------------------------------

def _level_of(files):
    """(entry, levelbuild.Builder) of a built mission's own level, whatever its Working
    Title names it (leveltitle.level_entry); (None, None) when it has none."""
    from riftlord.build import levelbuild, leveltitle
    name = leveltitle.level_entry(files)
    if not name:
        return None, None
    return name, levelbuild.Builder.around(files[name], files[name[:-5] + '.uexp'])


def _string(raw):
    n = struct.unpack_from('<i', raw)[0]
    return raw[4:4 + n - 1].decode('ascii', 'replace') if n > 0 else ''


def placed(files):
    """{class: [(actor index, {property: value})]} of the RiftLordKit_ actors of CLASSES
    classes in a built level."""
    from riftlord.build import levelbuild
    from riftlord.mapeditor import controllers
    _name, level = _level_of(files)
    if level is None:
        return {}
    pkg = level.pkg
    listed = set(level.actor_list())
    out = {}
    for i in range(len(pkg.exports)):
        if i + 1 not in listed or not levelbuild._name_of(pkg, i).startswith('RiftLordKit'):
            continue
        cls = controllers._class_name(pkg, i + 1)
        if cls not in CLASSES:
            continue
        vals = {}
        data = pkg.exports[i]['data']
        for name, _num, kind, _s, at, size, _e in _walk(data, _names(pkg)):
            if kind == 'StrProperty':
                vals[name] = _string(data[at:at + size])
            elif kind == 'FloatProperty':
                vals[name] = round(struct.unpack_from('<f', data, at)[0], 4)
            elif kind == 'IntProperty':
                vals[name] = struct.unpack_from('<i', data, at)[0]
            elif kind == 'BoolProperty':
                vals[name] = bool(data[at - 2])
        out.setdefault(cls, []).append((i + 1, vals))
    return out


def _component_problems(builder, actor):
    """A placed actor's components: each owned by it, each with its class and template
    serialized before it is created, the root one of them; a ProjectileMovement at rest."""
    from riftlord.build import levelbuild
    pkg = builder.pkg
    out = []
    root = builder._root_component(actor)
    comps = [i + 1 for i, e in enumerate(pkg.exports)
             if struct.unpack_from('<i', e['meta'], levelbuild.OUTER_AT)[0] == actor]
    if not root or root not in comps:
        out.append('root component belongs to another actor')
    for c in comps:
        meta = pkg.exports[c - 1]['meta']
        first = struct.unpack_from('<i', meta, 84)[0]
        counts = struct.unpack_from('<4i', meta, 88)
        sbc = list(pkg.preload[first + counts[0] + counts[1]:
                               first + counts[0] + counts[1] + counts[2]]) if first >= 0 else []
        cls, tmpl = struct.unpack_from('<i', meta, 0)[0], struct.unpack_from('<i', meta, 8)[0]
        if cls not in sbc or tmpl not in sbc:
            out.append('%s: class or template not serialized before create'
                       % levelbuild._name_of(pkg, c - 1))
    return out


def gates(files, plan):
    """[(name, ok, detail)]: every kit class item is placed with its preloads, components,
    instance properties and group values, and every kit entry is in the pak byte for byte."""
    from riftlord.mapeditor import controllers
    plan = plan or {}
    want = {}
    for item in plan.get('added') or []:
        if item.get('kind') == 'kit' and item.get('name') in CLASSES:
            want.setdefault(item['name'], []).append(item)
    if not want:
        return []
    out = []
    _name, level = _level_of(files)
    got = placed(files)
    groups = group_values(plan)
    for cls, items in sorted(want.items()):
        mine = got.get(cls) or []
        bad = []
        for actor, _vals in mine:
            bad += ['%d: %s' % (actor, p) for p in controllers._actor_problems(level, actor)]
            bad += ['%d: %s' % (actor, p) for p in _component_problems(level, actor)]
        out.append(('%s placed (%d of %d), class and default object preloaded, components '
                    'belong to it' % (cls, len(mine), len(items)),
                    len(mine) == len(items) and not bad, '; '.join(bad[:3])))
        if cls == CHEST:
            wrong = []
            spots = sorted(chest_values(i)['group'] or '(own)' for i in items)
            for (actor, vals), item in zip(mine, items):
                v = chest_values(item)
                v = groups.get(v['group'], v) if v['group'] else v
                expect = {'Group': v['group'], 'Chance': v['chance'], 'CoinMin': v['coin'][0],
                          'CoinMax': v['coin'][1], 'CoinChance': v['coinChance'],
                          'SkullMin': v['skulls'][0], 'SkullMax': v['skulls'][1],
                          'SkullChance': v['skullChance']}
                if coin_bias(v) is not None:
                    expect['CoinBias'] = coin_bias(v)
                miss =['%s %r != %r' % (k, vals.get(k), e) for k, e in expect.items()
                        if vals.get(k) != (round(e, 4) if isinstance(e, float) else e)]
                if miss:
                    wrong.append('%d: %s' % (actor, ', '.join(miss)))
            out.append(('chest groups and rewards written (%s)' % ', '.join(spots),
                        not wrong, '; '.join(wrong[:3])))
        else:
            wrong = []
            for actor, vals in mine:
                for name, _kind, value in CLASSES[cls]['props']:
                    if vals.get(name) != value:
                        wrong.append('%d %s=%r' % (actor, name, vals.get(name)))
            if CLASSES[cls]['props']:
                out.append(('%s %s written' % (cls, ', '.join(
                    '%s=%r' % (n, v) for n, _k, v in CLASSES[cls]['props'])),
                    not wrong, ', '.join(wrong[:3])))
    from riftlord.core import paksource
    for key in kits_used(plan):
        path = KITS[key]['pak']
        try:
            held = set(paksource.names(path))
        except (Exception, SystemExit):              # noqa: BLE001
            held = set()
        carried = sorted(k for k in files if k in held)
        out.append(('%s not carried (the mission requires %s)'
                    % (key, os.path.relpath(path, os.path.dirname(os.path.dirname(path)))
                       if path else key), not carried, ', '.join(carried[:3])))
    return out


# -- offline check ----------------------------------------------------------------------------

#: The level `--check` builds on (a small shipped level, no cauldron of its own).
CHECK_LEVEL = 'Scenario_02'


def check_plan(at=None):
    """A plan placing a chest group of three spots, a second chest, and one of every
    pickup, around `at` (default the level's first player start)."""
    from riftlord.mapeditor import leveldata
    if at is None:
        starts = leveldata.level(CHECK_LEVEL).get('player_starts') or []
        at = starts[0]['location'] if starts and starts[0].get('location') else [0, 0, 0]

    def m(dx, dy, dz=0.0, yaw=0.0):
        import math
        c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        return [c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, at[0] + dx, at[1] + dy, at[2] + dz, 1]
    coin = {'group': 'Chest 1', 'chance': 100, 'coin': [250, 750], 'coinChance': 100,
            'skulls': [1, 3], 'skullChance': 0, 'coinLean': 0.25}
    skull = {'group': 'Chest 2', 'chance': 100, 'coin': [250, 750], 'coinChance': 0,
             'skulls': [1, 3], 'skullChance': 100}
    added = [{'kind': 'kit', 'name': CHEST, 'matrix': m(400, -300 + 300 * k), CHEST_KEY: dict(coin)}
             for k in range(3)]
    added.append({'kind': 'kit', 'name': CHEST, 'matrix': m(700, 0), CHEST_KEY: dict(skull)})
    #: the Chandelier has its own check (riftlord.mapeditor.chandelier --check)
    pickups = [c for c in CLASSES if c != CHEST and not CLASSES[c]['carried']]
    for k, cls in enumerate(pickups):
        added.append({'kind': 'kit', 'name': cls, 'matrix': m(-300, -600 + 150 * k)})
    return {'level': CHECK_LEVEL, 'added': added}


def check(out_dir, log=print):
    """Save the check plan and read it back, build it into a copy of CHECK_LEVEL with the
    kits, and run pkgcheck, the import gate and `gates`. Returns failures."""
    import tempfile
    from riftlord.build.navigation import navpatch
    from riftlord.core import pkgcheck
    from riftlord.build import apply as applier
    from riftlord.mapeditor import leveldata
    from riftlord.mapeditor import probe
    fails = []
    plan = check_plan()
    keep = leveldata.PLANS
    leveldata.PLANS = tempfile.mkdtemp(prefix='plans-', dir=out_dir)
    try:
        leveldata.save_plan(CHECK_LEVEL, plan)
        back = leveldata.load_plan(CHECK_LEVEL)
    finally:
        leveldata.PLANS = keep
    if len(back.get('added') or []) != len(plan['added']):
        fails.append('save kept %d of %d items' % (len(back.get('added') or []), len(plan['added'])))
    for i, (want, got) in enumerate(zip(plan['added'], back.get('added') or [])):
        if got.get('name') != want['name'] or got.get(CHEST_KEY) != (
                clean({'name': want['name']}, want).get(CHEST_KEY)):
            fails.append('save changed added[%d]: %s' % (i, json.dumps(got)[:160]))
    log('save round trip: %s' % ('ok' if not fails else '; '.join(fails)))
    stem = 'OMD/Content/Mods/KitCheck/Maps/KitCheck_Map01'
    got = navpatch.fetch_map(navpatch.map_path(CHECK_LEVEL), out_dir)
    ua, ux, report = applier.apply_plan(
        open(got['.umap'], 'rb').read(), open(got['.uexp'], 'rb').read(), CHECK_LEVEL,
        '/Game/Mods/KitCheck/Maps/KitCheck_Map01', 'KitCheck_Map01', plan=back)
    files = {stem + '.umap': ua, stem + '.uexp': ux}
    for line in report['notes']:
        log('  note: %s' % line)
    for line in report['skipped']:
        log('  skipped: %s' % line)
        fails.append('skipped: %s' % line)
    log('  chest leaves out: %s' % ', '.join((report.get('kitactors') or {}).get('chestDropped') or []))
    for name in sorted(files):
        stem_, ext = os.path.splitext(name)
        if ext in ('.umap', '.uasset') and stem_ + '.uexp' in files:
            bad = pkgcheck.problems(files[name], files[stem_ + '.uexp'], os.path.basename(stem_))
            if bad or ext == '.umap':
                log('pkgcheck %s: %s' % (os.path.basename(name), 'ok' if not bad else bad[:3]))
            fails += ['pkgcheck %s: %s' % (name, b) for b in bad]
    with open(os.path.join(out_dir, 'KitCheck_Map01.umap'), 'wb') as fh:
        fh.write(ua)
    with open(os.path.join(out_dir, 'KitCheck_Map01.uexp'), 'wb') as fh:
        fh.write(ux)
    checked, missing = probe.import_problems(files, stem + '.umap')
    log('%s  every import names an export, string and number (%d)%s'
        % ('PASS' if not missing else 'FAIL', checked, (': ' + '; '.join(missing[:3])) if missing else ''))
    if missing:
        fails.append('imports: %s' % '; '.join(missing[:3]))
    from riftlord.build import levelbuild
    lb = levelbuild.Builder.around(ua, ux)
    gaps = lb.level_preload_gaps()
    log('%s  the Level preloads every listed actor' % ('PASS' if not gaps else 'FAIL'))
    if gaps:
        fails.append('%d listed actors not preloaded' % len(gaps))
    for name, ok, detail in gates(files, back):
        log('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + detail) if detail else ''))
        if not ok:
            fails.append(name)
    return fails


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--check', metavar='DIR', help='build every class into a copy of %s offline'
                    % CHECK_LEVEL)
    ap.add_argument('--layout', metavar='CLASS', help='print what a placed CLASS holds')
    args = ap.parse_args(argv)
    if args.layout:
        print(json.dumps(layout(args.layout), indent=1, default=list))
    if args.check:
        os.makedirs(args.check, exist_ok=True)
        fails = check(args.check)
        print('%d failure(s)' % len(fails))
        return 1 if fails else 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
