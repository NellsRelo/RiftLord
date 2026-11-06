"""Catalogue the actors an OMD3 level is built from, not just its meshes.

    python3 omd3actors.py --build
    python3 omd3actors.py --kind rift

A converted level needs far more than geometry: a rift, spawners, the doors a horde
breaks through, teleport rifts, traps, particle emitters, ambient sound and lights.
`convert.donor_index` already finds every **Blueprint** class a shipped level
instantiates, but the emitters and sounds are engine classes (`Emitter`,
`AmbientSound`) and never appear there, so this scans for actors of any class.

For each class it records where to lift one from -- a level and an export index --
which is what `levelbuild.lift_actor` needs, and how many shipped levels use it, as
a rough measure of whether it is a normal thing to place or an oddity.
"""

import argparse
import collections
import json
import os
import re
import struct
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamefiles  # noqa: E402
from riftlord.build import levelbuild  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

CATALOGUE = gamepath.data('omd3-actors.json')

#: Maps that must not be lifted from, for the reasons in `convert.FORBIDDEN_DONORS`.
FORBIDDEN = ('ThirdPersonExampleMap', '/Maps/UI/', '/Maps/Cinematic/',
             'Engine/Content')

#: Classes that are components, data or brush internals rather than things a
#: person places.
NOT_PLACEABLE = {
    'Model', 'BodySetup', 'BrushComponent', 'Function', 'SceneComponent',
    'StaticMeshComponent', 'ParticleSystemComponent', 'AudioComponent',
    'PointLightComponent', 'SpotLightComponent', 'BoxComponent',
    'CapsuleComponent', 'SphereComponent', 'ArrowComponent', 'BillboardComponent',
    'TextRenderComponent', 'DecalComponent', 'InstancedStaticMeshComponent',
    'HierarchicalInstancedStaticMeshComponent', 'LODActor',
    'NavCollision', 'MaterialInstanceConstant', 'Level', 'World',
    'WorldSettings', 'NavigationSystemModuleConfig', 'RecastNavMesh',
    'NavMeshRenderingComponent', 'SkeletalMeshComponent',
}

#: How an actor class is grouped for the editor. Order matters -- a
#: `BP_OMDTeleportRift` is a teleporter, not a rift.
KINDS = (
    ('teleport', ('teleport', 'portal')),
    ('rift', ('rift',)),
    ('spawner', ('spawner', 'aispawn')),
    ('door', ('door', 'gate', 'portcullis')),
    ('trap', ('trap',)),
    ('emitter', ('emitter', 'particle', 'vfx', 'fx')),
    ('sound', ('sound', 'audio', 'ambient')),
    ('sky', ('sky', 'fog', 'atmosphere', 'cloud', 'skybox', 'skydome')),
    ('light', ('light', 'torch', 'brazier', 'candle', 'lamp', 'lantern',
               'fire', 'flame')),
    ('path', ('path', 'waypoint')),
    ('start', ('playerstart', 'spawnpoint')),
    ('volume', ('volume',)),
)


#: Classes that are never placed by a person, whatever their name.
NEVER = ('BlueprintGeneratedClass', 'OMDWorldSettings', 'InstancedFoliageActor')


def placeable(class_name):
    """Whether a person would ever drop one of these into a level.

    Components are the bulk of what a scan finds -- `SkyLightComponent`,
    `OMDTrapGridBoxComponent`, `TimelineComponent` -- and they belong to actors
    rather than standing alone, so anything ending in `Component` is out. The
    explicit list catches the rest: a world settings object is not furniture.
    """
    if class_name in NOT_PLACEABLE or class_name in NEVER:
        return False
    if class_name.endswith('Component'):
        return False
    # Config objects a level happens to hold; not furniture either.
    if class_name.startswith(('AISenseConfig', 'AIPerception')):
        return False
    return True


#: Split a class name into words: underscores, and the CamelCase humps, taking an
#: acronym run as one word so `BP_OMDAISpawner_C` gives `omdai` and `spawner`.
_WORDS = re.compile(r'[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|[0-9]+')


def words_of(class_name):
    return [w.lower() for w in _WORDS.findall(str(class_name))]


def kind_of(class_name):
    """Which group a class belongs to, matched on whole words.

    Substring matching gets this wrong in a way that is easy to miss:
    `OMDPlayerOutdoorAudioVolume` contains "door" inside "Outdoor", so it filed
    itself as a door the horde breaks through. Matching word by word -- a word may
    still be a prefix, so "door" catches "Doorway" -- keeps that apart while
    `AmbientSound` still reads as sound.
    """
    parts = words_of(class_name)
    for kind, terms in KINDS:
        for term in terms:
            if any(w.startswith(term) for w in parts):
                return kind
    return 'other'


def build(out_path=CATALOGUE, limit=None):
    """Scan every shipped level and record where each actor class can be lifted."""
    maps = [m for m in sorted(gamefiles.find(ext='.umap'))
            if '/Maps/' in m and not any(bad in m for bad in FORBIDDEN)]
    if limit:
        maps = maps[:limit]
    found = {}
    levels = collections.Counter()
    for m in maps:
        try:
            ua, ux = gamefiles.package(m[:-5])
            pkg = Package(ua, ux)
            pkg.load_payloads()
        except Exception:                            # noqa: BLE001
            continue
        here = set()
        for i, e in enumerate(pkg.exports):
            cls = levelbuild.resolve(
                pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1]
            if not cls or not placeable(cls):
                continue
            here.add(cls)
            if cls not in found:
                found[cls] = {'class': cls, 'level': m[:-5], 'export': i,
                              'kind': kind_of(cls)}
        for cls in here:
            levels[cls] += 1
    for cls, info in found.items():
        info['levels'] = levels[cls]
    with open(out_path, 'w') as fh:
        json.dump({'actors': found}, fh, indent=1, sort_keys=True)
    return found


MESHES = gamepath.data('omd3-actor-meshes.json')


_BY_NAME = None


def _asset_for(class_name):
    """The Blueprint asset behind a generated class, or None.

    A cooked class is named after its asset with `_C` on the end, so
    `Rift_Dais_SAND_C` comes from `Rift_Dais_SAND.uasset`. The name index is built
    once: looking 714 classes up one linear scan at a time is 29 million string
    comparisons for an answer a dict gives straight away.
    """
    global _BY_NAME
    if _BY_NAME is None:
        _BY_NAME = {}
        for a in gamefiles.find(ext='.uasset'):
            _BY_NAME.setdefault(a.rsplit('/', 1)[-1][:-len('.uasset')],
                                a[:-len('.uasset')])
        #: by lower-cased name too: a class's case may differ from its asset's
        #: (`Wall_RB_MT_v3_1x4_Angle_C` is `Wall_RB_MT_v3_1x4_angle.uasset`)
        for name, path in list(_BY_NAME.items()):
            _BY_NAME.setdefault(name.lower(), path)
    base = class_name[:-2] if class_name.endswith('_C') else class_name
    return _BY_NAME.get(base) or _BY_NAME.get(base.lower())


def found_by_case_only(class_name):
    """True when a class's asset is found only by its lower-cased name
    (`Wall_RB_MT_v3_1x4_Angle_C`, asset `Wall_RB_MT_v3_1x4_angle`)."""
    _asset_for(class_name)
    base = class_name[:-2] if class_name.endswith('_C') else class_name
    return base not in _BY_NAME and base.lower() in _BY_NAME


_LATE_PARTS = {}


def late_parts(class_name):
    """parts_of(class_name) for a class found_by_case_only, else []: the classes the
    stored part index (PARTS) has no entry for."""
    if class_name not in _LATE_PARTS:
        _LATE_PARTS[class_name] = parts_of(class_name) if found_by_case_only(class_name) \
            else []
    return _LATE_PARTS[class_name]


def meshes_of(class_name):
    """The static meshes one actor class is built from, largest first.

    An actor has no appearance of its own to draw -- a rift is a Blueprint, not a
    mesh -- but the meshes it assembles are named in its import table, and drawing
    those is the difference between placing a labelled ball and placing a rift you
    can line up against the floor it sits on.
    """
    path = _asset_for(class_name)
    if not path:
        return []
    try:
        ua, ux = gamefiles.package(path)
        pkg = Package(ua, ux)
        pkg.load_payloads()
    except Exception:                                # noqa: BLE001
        return []
    names = [n for n, _h in pkg.names]

    def name_at(record, offset):
        # An FName is an index and a number, and a number of N reads as `_N-1`.
        i, number = struct.unpack_from('<ii', record, offset)
        if not 0 <= i < len(names):
            return ''
        base = names[i]
        return base if number == 0 else '%s_%d' % (base, number - 1)

    found = []
    for imp in pkg.imports:
        if len(imp) < 28:
            continue
        if name_at(imp, 8) == 'StaticMesh':
            got = name_at(imp, 20)
            if got and got not in found:
                found.append(got)
    # Ranked by ground footprint rather than volume, because the reason to draw one
    # of these is to line the actor up on the grid: the piece that says where it
    # sits is the widest one, not the tallest. By volume, a rift dais was
    # represented by its archway ring instead of its base disc.
    try:
        from riftlord.formats.omd3 import meshcat
        cat = meshcat.load()
        found.sort(key=lambda n: -_footprint(cat.get(n)))
    except Exception:                                # noqa: BLE001
        pass
    return found


def _footprint(info):
    if not info:
        return 0.0
    s = info.get('size') or [0, 0, 0]
    return float(s[0]) * float(s[1])


PARTS = gamepath.data('omd3-actor-parts.json')


def _header_of(class_name):
    """(header info, uexp bytes, header size) for a Blueprint class, or None."""
    path = _asset_for(class_name)
    if not path:
        return None
    try:
        import tempfile
        from riftlord.formats.ue.uheader import parse as parse_header
        ua = gamefiles.read(path + '.uasset')
        ux = gamefiles.read(path + '.uexp')
        root = tempfile.mkdtemp(prefix='omd3parts-')
        staged = os.path.join(root, 'a.uasset')
        with open(staged, 'wb') as fh:
            fh.write(ua)
        return parse_header(staged), ux, len(ua)
    except Exception:                                # noqa: BLE001
        return None


def parent_of(class_name):
    """The Blueprint class this one derives from, or None.

    Read from the generated class export's **SuperIndex**, which is the only
    authoritative answer. Two cheaper heuristics both looked right and were wrong:
    the first `BlueprintGeneratedClass` import made `BP_Door_Forcefield_C` a child of
    `BP_OMDGameState_C`, and requiring the parent's CDO import as well made exactly
    the same mistake, because the forcefield imports that CDO to call a function on
    it. Inheriting components from an unrelated class is silent and wrong.
    """
    path = _asset_for(class_name)
    if not path:
        return None
    try:
        import struct as _struct
        from riftlord.formats.ue.uassetio import Package
        pkg = Package(gamefiles.read(path + '.uasset'),
                      gamefiles.read(path + '.uexp'))
    except Exception:                                # noqa: BLE001
        return None

    def nm(i):
        #: a name table entry is (name, hash); comparing the tuple never matches
        return str(pkg.names[i][0]) if 0 <= i < len(pkg.names) else ''

    def ref(idx):
        if idx >= 0:
            return None                              # an export, so not our parent
        ent = pkg.imports[-idx - 1]
        return nm(_struct.unpack_from('<i', ent, 20)[0])

    for e in pkg.exports:
        meta = bytes(e['meta'])
        cidx, sidx = _struct.unpack_from('<ii', meta, 0)
        if ref(cidx) != 'BlueprintGeneratedClass':
            continue
        got = ref(sidx)
        #: A native parent (`OMDDoor`, `Actor`) has no components to read here, and is
        #: not a Blueprint, so it ends the walk.
        return got if got and got.endswith('_C') else None
    return None


def ancestry(class_name, limit=12):
    """`class_name` and the classes it derives from, leaf first."""
    chain, seen = [], set()
    cur = class_name
    while cur and cur not in seen and len(chain) < limit:
        seen.add(cur)
        chain.append(cur)
        cur = parent_of(cur)
    return chain


def _override_material(props, import_name):
    """The first material a component overrides its mesh's own with, or None.

    Same array as `omd3level._override_material` reads: an int32 count then one
    FPackageIndex per slot.
    """
    ref = props.get('OverrideMaterials')
    if not ref or not ref[2] or len(ref[2]) < 8:
        return None
    raw = ref[2]
    try:
        count = struct.unpack_from('<i', raw, 0)[0]
    except Exception:                                # noqa: BLE001
        return None
    if not 1 <= count <= 32:
        return None
    for k in range(count):
        at = 4 + k * 4
        if at + 4 > len(raw):
            break
        idx = struct.unpack_from('<i', raw, at)[0]
        if idx < 0:
            got = import_name(idx)
            if got:
                return got
    return None


def own_components(class_name):
    """{component name: {field: value}} for this class ALONE.

    Only the fields the class actually sets. A field it leaves out is NOT a default --
    it is inherited, so recording a default here would overwrite the parent's value
    with a zero. Components with no mesh of their own are kept for the same reason:
    `BP_Door_Interior_3W_C`'s DoorMesh sets nothing but `AttachParent`, and dropping it
    loses the only link to the portcullis its base declares.
    """
    got = _header_of(class_name)
    if not got:
        return {}
    info, ux, hdr = got
    from riftlord.onslaught import onslaught
    imports = info.get('imports') or []

    def import_name(index):
        i = -index - 1
        if 0 <= i < len(imports):
            imp = imports[i]
            if isinstance(imp, dict):
                return imp.get('name') or imp.get('object_name')
        return None

    out = {}
    for e in info['exports']:
        kind = str(e.get('class') or e.get('cls') or '')
        #: SKELETAL components too. A level's doors, pennants, cauldrons and barrel
        #: traps are skeletal, and reading only static ones left every one of them
        #: out of the viewport entirely.
        skeletal = 'SkeletalMeshComponent' in kind
        if not skeletal and 'StaticMeshComponent' not in kind:
            continue
        name = str(e.get('name') or e.get('object_name') or '')
        if not name:
            continue
        props = dict((pr[0], pr) for pr in onslaught.read_props(info, ux, hdr, e))
        rec = {'skeletal': True} if skeletal else {}
        ref = props.get('SkeletalMesh' if skeletal else 'StaticMesh')
        if ref:
            idx = onslaught.as_int(ref[2])
            if idx is not None and idx < 0:
                mesh = import_name(idx)
                if mesh:
                    rec['mesh'] = mesh
        for field, key in (('RelativeLocation', 'loc'),
                           ('RelativeRotation', 'rot'),
                           ('RelativeScale3D', 'scale')):
            if field in props:
                rec[key] = list(onslaught.as_vec(props[field][2]))
        #: A component may paint its mesh with a material of its own, and for the
        #: force-field door that IS the door: `BP_Door_Forcefield_C`'s DoorMesh
        #: overrides a plain plane with `M_DoorForceField_Inst`. Without it the door
        #: drew as an untextured quad, which is indistinguishable from not drawing.
        mat = _override_material(props, import_name)
        if mat:
            rec['material'] = mat
        #: `_GEN_VARIABLE` is the cooker's suffix for a component promoted to a
        #: variable; the parent declares the same component without it, so the two
        #: must merge as one or a child override becomes a second floating mesh.
        out[name.replace('_GEN_VARIABLE', '')] = rec
    return out


def parts_of(class_name, inherit=True):
    """Every mesh one Blueprint places, with its transform inside the Blueprint.

    A kit piece is not one mesh. `ArchwayGate_1x1x3_RB_C` is ten -- an archway, two
    column sections, door trim, plates, brackets, a portcullis sill -- each held at
    its own `RelativeLocation` and `RelativeRotation` by a component template in the
    Blueprint. Drawing the class as a single mesh at the actor's origin puts nine of
    them in the wrong place and the tenth at the wrong offset, which is what made a
    level look subtly scrambled.

    **Inheritance is per component AND per property**, which the doors show plainly:

    * `BP_Door_Interior_3W_C`'s DoorMesh sets nothing, so both its mesh and its
      transform come from `BP_Door_Interior_Base_C` -- read alone it has no meshes
      at all, which is why a door drew as nothing.
    * `BP_Door_Exterior_3W_C` overrides DoorMesh's *mesh* but not its transform, and
      its force-field plane's *location and scale* but not its mesh or rotation.

    So appending a child's components to its parent's duplicates them, and replacing
    them wholesale loses whichever half the child did not state. Merge by component
    name, field by field, ancestor first.
    """
    merged = {}
    chain = ancestry(class_name) if inherit else [class_name]
    for cls in reversed(chain):                      # ancestor first, leaf wins
        for name, rec in own_components(cls).items():
            merged.setdefault(name, {}).update(rec)

    out = []
    for name, rec in merged.items():
        if not rec.get('mesh'):
            continue                                 # a component that never got one
        part = {'component': name, 'mesh': rec['mesh'],
                'skeletal': bool(rec.get('skeletal')),
                'loc': rec.get('loc', [0.0, 0.0, 0.0]),
                'rot': rec.get('rot', [0.0, 0.0, 0.0]),
                'scale': rec.get('scale', [1.0, 1.0, 1.0])}
        if rec.get('material'):
            part['material'] = rec['material']
        out.append(part)
    return out


def part_index(path=PARTS):
    """{class: [parts]}, built once and kept."""
    if os.path.exists(path):
        try:
            with open(path) as fh:
                return json.load(fh)['actor_parts']
        except Exception:                            # noqa: BLE001
            pass
    return {}


def build_parts(out_path=PARTS, limit=None):
    """Work out the meshes and offsets of every placeable Blueprint class."""
    found = {}
    classes = [c for c in sorted(load()) if c.endswith('_C')]
    if limit:
        classes = classes[:limit]
    for cls in classes:
        got = parts_of(cls)
        if got:
            found[cls] = got
    with open(out_path, 'w') as fh:
        json.dump({'actor_parts': found}, fh, indent=1, sort_keys=True)
    return found


def mesh_index(path=MESHES):
    """{class: [mesh names]}, built once and kept."""
    if os.path.exists(path):
        try:
            with open(path) as fh:
                return json.load(fh)['actor_meshes']
        except Exception:                            # noqa: BLE001
            pass
    return {}


def build_meshes(out_path=MESHES, limit=None):
    """Work out which meshes every placeable actor class is made of."""
    found = {}
    classes = sorted(load())
    if limit:
        classes = classes[:limit]
    for cls in classes:
        got = meshes_of(cls)
        if got:
            found[cls] = got
    with open(out_path, 'w') as fh:
        json.dump({'actor_meshes': found}, fh, indent=1, sort_keys=True)
    return found


def load(path=CATALOGUE):
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return json.load(fh)['actors']


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--build-meshes', action='store_true')
    ap.add_argument('--build-parts', action='store_true')
    ap.add_argument('--parts-of')
    ap.add_argument('--meshes-of')
    ap.add_argument('--kind')
    ap.add_argument('--limit', type=int)
    args = ap.parse_args(argv)

    if args.build:
        found = build(limit=args.limit)
        kinds = collections.Counter(v['kind'] for v in found.values())
        print('%d actor class(es)' % len(found))
        for kind, n in kinds.most_common():
            print('   %-10s %4d' % (kind, n))
        return 0

    if args.build_meshes:
        found = build_meshes(limit=args.limit)
        print('%d class(es) with meshes, %d mesh reference(s)'
              % (len(found), sum(len(v) for v in found.values())))
        for cls in sorted(found)[:10]:
            print('   %-38s %s' % (cls, ', '.join(found[cls][:4])))
        return 0

    if args.build_parts:
        found = build_parts(limit=args.limit)
        print('%d class(es), %d part(s) in total'
              % (len(found), sum(len(v) for v in found.values())))
        for cls in sorted(found, key=lambda c: -len(found[c]))[:8]:
            print('   %-42s %d part(s)' % (cls, len(found[cls])))
        return 0

    if args.parts_of:
        for part in parts_of(args.parts_of):
            print('   %-28s at %s rot %s' % (part['mesh'],
                  [round(v) for v in part['loc']], [round(v) for v in part['rot']]))
        return 0

    if args.meshes_of:
        got = meshes_of(args.meshes_of)
        print('%s -> %s' % (args.meshes_of, ', '.join(got) if got else 'no meshes'))
        return 0

    if args.kind:
        found = load()
        rows = sorted((v for v in found.values() if v['kind'] == args.kind),
                      key=lambda v: -v['levels'])
        print('%d %s actor(s)' % (len(rows), args.kind))
        for r in rows[:25]:
            print('   %-44s in %3d level(s)' % (r['class'], r['levels']))
        return 0

    ap.error('need --build or --kind')


if __name__ == '__main__':
    raise SystemExit(main())
