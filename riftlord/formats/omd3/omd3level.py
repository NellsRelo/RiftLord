"""Read a shipped OMD3 level for viewing: its geometry and its gameplay wiring.

    python3 omd3level.py --level Scenario_05
    python3 omd3level.py --level Scenario_05 --json out.json

## Why

Adding a spawner to a level that already works means pointing at a place in that
level, which means drawing it. The conversion tooling reads OMD2 levels; this reads
OMD3's own, out of the shipped packages, so the editor can show the real thing.

## Where a level keeps its geometry

Not in the persistent level. `Scenario_05` holds 517 exports and almost no art; the
castle is in the `_Castle` sublevel, 979 `StaticMeshActor`s and 1,761
`StaticMeshComponent`s, plus Blueprint kit pieces (`Column_Sec_1_4_RB_C` and the
like) whose meshes live in the Blueprint rather than the level. So the sublevels are
read too, and a Blueprint actor is drawn as the widest mesh its class is built from,
which `omd3actors` already works out.

## Transforms

An actor carries no transform of its own: `RootComponent` names a component export
in the same package and that holds `RelativeLocation`, `RelativeRotation` and
`RelativeScale3D`. A `StaticMeshActor`'s mesh comes from the same component's
`StaticMesh` reference, which is an import.
"""

import argparse
import collections
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.core import gamepath  # noqa: E402
from riftlord.formats.omd3 import omd3actors  # noqa: E402
from riftlord.formats.omd3 import omd3templates  # noqa: E402
from riftlord.onslaught import onslaught  # noqa: E402
from riftlord.formats.ue.uheader import parse as parse_header  # noqa: E402

TMP = gamepath.scratch('.omd3level', gamepath.CODE_ROOT)

#: The sublevels worth drawing. `_Lighting` is in the list despite the name: it holds
#: 187 pieces on Split Stairs -- every torch, brazier and post base -- because the
#: art that carries a light is filed with the lights. Only `_Audio` and the
#: cinematics have nothing to draw.
GEOMETRY_SUBLEVELS = ('_Castle', '_Environment', '_Terrain', '_Gameplay',
                      '_Lighting', '_Foliage', '_FX', '')

#: Surfaces that are liquid or energy rather than stone. They ship with a translucent
#: material, and drawn opaque an arcane pool reads as a slab of floor -- so they are
#: flagged for the viewer to draw them as the surfaces they are.
#: Liquid surfaces, by the WORDS in a mesh name rather than substrings of it.
#:
#: `'tar' in 'Turret_Cap_Star'` is true, so every turret roof in the game was being
#: drawn as a translucent pool -- with a perfectly good `Turret_Roof_Tiles_BC` on it.
#: Names are split on case and underscores and matched whole.
LIQUID_WORDS = frozenset(('lava', 'acid', 'water', 'pool', 'arcane', 'goop',
                          'slime', 'ooze', 'tar', 'magma'))

#: `surface` only counts beside another liquid word: `Scen_05_Arcane_Surface` is one,
#: `Turret_Cap_Star` is not, and a bare `..._Surface` is too often ordinary geometry.
LIQUID_KINDS = ('lava', 'magma', 'arcane', 'acid', 'slime', 'goop', 'ooze', 'tar',
                'water', 'pool')


def _words(name):
    import re as _re
    return {w.lower() for w in _re.findall(r'[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+',
                                           str(name))}


def liquid_kind(mesh_name):
    """Which liquid a mesh is, or None. Used to tint one that names no material."""
    got = _words(mesh_name)
    for kind in LIQUID_KINDS:
        if kind in got:
            return 'water' if kind == 'pool' else kind
    return None


#: Every level places this, in its `_Lighting` sublevel, painted with a per-level
#: material instance. It is the sky.
SKYBOX_MESH = 'SM_Skybox_Mesh'
#: Not every level uses the same one. Northern Rampart places `SM_SkySphere`, and
#: because only `SM_Skybox_Mesh` was recognised its sky was drawn as ordinary lit,
#: fogged, front-faced level geometry -- so the viewport showed the clear colour and
#: the level looked as though it had no sky at all.
SKYBOX_MESHES = ('SM_Skybox_Mesh', 'SM_SkySphere', 'SM_Skybox', 'SM_SkyDome')


def is_liquid(mesh_name):
    return bool(LIQUID_WORDS & _words(mesh_name))


def sublevels(name):
    """The packages of one scenario worth reading, persistent level first.

    `navpatch.maps()` knows only the 33 playable persistent levels, so the
    sublevels are derived from the persistent level's own path and checked against
    the pak index rather than guessed at.
    """
    try:
        base = navpatch.map_path(name)
    except SystemExit:
        return []
    index = navpatch._index()
    out = [base]
    for suffix in GEOMETRY_SUBLEVELS:
        if not suffix:
            continue
        candidate = base + suffix
        if candidate + '.umap' in index and candidate + '.uexp' in index:
            out.append(candidate)
    if len(out) == 1:
        out += streamed(base)
    return out


#: Streamed sublevels with nothing to draw.
NON_GEOMETRY_SUBLEVELS = ('_Audio', 'Cinematic', '_BuiltData')


def extra_sublevels(name):
    """Sublevels the persistent level streams beyond `sublevels(name)`.

    `_Acid` on Side Entrance, `_Rain` on Cliffside, `_VFX` on Abandoned Passage: any
    package the persistent level's name table names that exists as a map. Read after
    the others, so the pieces they add come after every existing piece id.
    """
    have = set(sublevels(name))
    if not have:
        return []
    base = navpatch.map_path(name)
    try:
        info = parse_header(navpatch.fetch_map(base, TMP)['.umap'])
        index = navpatch._index()
        persistent = set(navpatch.maps())
    except (Exception, SystemExit):                  # noqa: BLE001
        return []
    found = []
    for n in info.get('names') or []:
        if isinstance(n, (list, tuple)):
            n = n[0]
        if (not isinstance(n, str) or not n.startswith('/Game/') or n in have
                or n in found or n + '.umap' not in index):
            continue
        if any(word in n for word in NON_GEOMETRY_SUBLEVELS):
            continue
        if n.rsplit('/', 1)[-1] in persistent:
            continue
        found.append(n)
    return sorted(found)


def streamed(base):
    """The geometry sublevels a persistent level streams in, read from its own package.

    A mission's copy of a level sits in its mod's folder with no sublevels beside it:
    it streams the source map's own, except any it edited, which it carries under its
    own name (`<mod>_Map01_Castle`). Its name table holds every one of those package
    paths, so they are read from there and checked against what the game would load --
    the base paks, ~mods and opened paks."""
    try:
        info = parse_header(navpatch.fetch_map(base, TMP)['.umap'])
        live = navpatch.live_index()
    except (Exception, SystemExit):                  # noqa: BLE001
        return []
    found = []
    for name in info.get('names') or []:
        if (not isinstance(name, str) or not name.startswith('/Game/') or name == base
                or name in found or name + '.umap' not in live):
            continue
        if any(name.endswith(s) for s in GEOMETRY_SUBLEVELS if s):
            found.append(name)
    # in the order the shipped levels are read, so they draw the same way
    rank = {s: i for i, s in enumerate(GEOMETRY_SUBLEVELS)}
    return sorted(found, key=lambda n: min((rank[s] for s in GEOMETRY_SUBLEVELS
                                            if s and n.endswith(s)), default=99))


def _load(package_path):
    files = navpatch.fetch_map(package_path, TMP)
    info = parse_header(files['.umap'])
    with open(files['.uexp'], 'rb') as fh:
        uexp = fh.read()
    return info, uexp, os.path.getsize(files['.umap'])


def _import_name(info, index):
    """The object name of an import, given a negative package index."""
    i = -index - 1
    imports = info.get('imports') or []
    if not 0 <= i < len(imports):
        return None
    imp = imports[i]
    if isinstance(imp, dict):
        return imp.get('name') or imp.get('object_name')
    return None


def _class_of(info, export):
    return str(export.get('class') or export.get('cls') or '')


def _matmul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)]
            for r in range(4)]


def _inverse(m):
    """The inverse of an affine 4x4 (rows), by its 3x3 part."""
    a = [row[:3] for row in m[:3]]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
           - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    if abs(det) < 1e-12:
        raise ValueError('a transform scaled flat has no inverse')
    inv = [[(a[(c + 1) % 3][(r + 1) % 3] * a[(c + 2) % 3][(r + 2) % 3]
             - a[(c + 1) % 3][(r + 2) % 3] * a[(c + 2) % 3][(r + 1) % 3]) / det
            for c in range(3)] for r in range(3)]
    t = [-sum(inv[r][k] * m[k][3] for k in range(3)) for r in range(3)]
    return [inv[0] + [t[0]], inv[1] + [t[1]], inv[2] + [t[2]], [0.0, 0.0, 0.0, 1.0]]


def _matrix(loc, rot, scale):
    """A 4x4 from an Unreal transform: rotator (pitch, yaw, roll), then scale.

    This is Unreal's own `FRotationMatrix`, transposed, and not a composition of
    right-handed Rz.Ry.Rx -- which is what it was first, and which is wrong. Unreal's
    pitch and roll run the opposite way to the textbook rotations about Y and X, so a
    yaw-only piece came out perfect while anything pitched or rolled was mirrored.
    Most of the kit is yaw-only, which is why the level looked almost right: the
    brackets, angled walls and stairs were the pieces out of place.

    Unreal multiplies row vectors (v' = v M), so its rows are the transformed basis
    vectors and the matrix is transposed here for column-vector use.
    """
    pitch, yaw, roll = (math.radians(rot[0]), math.radians(rot[1]),
                        math.radians(rot[2]))
    sp, cp = math.sin(pitch), math.cos(pitch)
    sy, cy = math.sin(yaw), math.cos(yaw)
    sr, cr = math.sin(roll), math.cos(roll)
    rows = [[cp * cy, cp * sy, sp],
            [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
            [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp]]
    sx, sy_, sz = scale
    scales = (sx, sy_, sz)
    out = [[0.0] * 4 for _ in range(4)]
    for r in range(3):
        for c in range(3):
            # Transposed, and the scale applied along the column it belongs to.
            out[r][c] = rows[c][r] * scales[c]
        out[r][3] = loc[r]
    out[3][3] = 1.0
    return out


#: Six decimals, not four.
#:
#: The rotation lives in the 3x3 part as numbers around 1, and the editor sends a
#: matrix back for the emitter to decompose. Recovering yaw divides by cos(pitch), so
#: for a piece standing near-vertical the rounding error is divided by something near
#: zero -- at four decimals a pole could come back a degree out of true. Six costs
#: about 24 bytes a piece and puts the floor three orders below anything visible.
MATRIX_DECIMALS = 6


def _flat(m):
    """Column-major, which is what three.js Matrix4.fromArray wants."""
    return [round(m[r][c], MATRIX_DECIMALS) for c in range(4) for r in range(4)]


#: Light component classes worth drawing, and how each reads.
LIGHT_CLASSES = ('PointLightComponent', 'SpotLightComponent',
                 'RectLightComponent', 'DirectionalLightComponent')
#: UE's USpotLightComponent OuterConeAngle default, in degrees.
SPOT_OUTER_CONE = 44.0


def _colour_of(props):
    """A light's colour as 0..1 rgb. An FColor is four bytes, stored B, G, R, A."""
    ref = props.get('LightColor')
    if not ref:
        return [1.0, 1.0, 1.0]
    raw = ref[2]
    if not raw or len(raw) != 4:
        return [1.0, 1.0, 1.0]
    b, g, r, _a = struct.unpack('<4B', raw)
    return [round(r / 255.0, 4), round(g / 255.0, 4), round(b / 255.0, 4)]


def _float_of(props, name, default):
    ref = props.get(name)
    if not ref or not ref[2] or len(ref[2]) != 4:
        return default
    return round(struct.unpack('<f', ref[2])[0], 3)


def _override_material(props, import_name):
    """The first material a component overrides its mesh's own with, or None.

    The skybox is the reason this matters: every level places `SM_Skybox_Mesh`, whose
    own material is nothing in particular, and paints it with a per-level instance --
    `Lava_Sky_INST` on North Wing, `Arcane_Sky_INST1` on Split Stairs. Without the
    override the sky mesh draws untextured.
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


#: A skeletal mesh drawn as its STATIC twin.
#:
#: The level's doors, pennants and several traps are `SkeletalMeshActor`s, and only
#: `StaticMeshComponent` was ever emitted -- so North Wing's portcullis beside the
#: spawner was simply absent from the viewport: not drawn, not clickable, a hole in
#: an archway that is plainly there in game. 68 skeletal components across five
#: levels, 42 of them on Front Lawn alone.
#:
#: Their geometry is a different format, but most have a static counterpart that is
#: already readable: `Portcullis_Solid_2W` -> `Portcullis_Solid_2W_SM`, `PennantA` is
#: itself a static mesh. The twin is still preferred where one exists, because it is in
#: the mesh catalogue and therefore the cheaper read.
#:
#: WHAT HAPPENS WITHOUT A TWIN HAS CHANGED. `CauldronTrap_SKM`, `FallingBoomBarrel_SKM`
#: and the grinder pits have none, and the piece used to be DROPPED -- which is why
#: Split Stairs showed none of its four boom barrels and Scenario_06 none of its
#: grinder pits. `omd3skm` reads a skeletal mesh's bind pose now, so the skeletal name
#: is emitted as-is and the mesh server answers for it.
def static_twin(name, catalogue=None):
    """A drawable static mesh for a skeletal one, or None."""
    if not name:
        return None
    if catalogue is None:
        from riftlord.formats.omd3 import meshcat
        catalogue = meshcat.load()
    bare = name[:-4] if name.endswith('_SKM') else name
    for candidate in (name, name + '_SM', bare, bare + '_SM'):
        if candidate in catalogue:
            return candidate
    return None


#: Components that place one mesh many times from `PerInstanceSMData`.
INSTANCED_CLASSES = ('InstancedStaticMeshComponent',
                     'HierarchicalInstancedStaticMeshComponent',
                     'FoliageInstancedStaticMeshComponent')
#: The component class the foliage tool places; its pieces are marked `foliage`.
FOLIAGE_CLASS = 'FoliageInstancedStaticMeshComponent'
#: The sublevel tag whose pieces are all marked `foliage`.
FOLIAGE_SUBLEVEL = 'Foliage'
#: Bytes per `FInstancedStaticMeshInstanceData`: one float FMatrix.
INSTANCE_STRIDE = 64
#: Instances one component may hold before the read is treated as a misparse.
MAX_INSTANCES = 200000


def instance_matrices(info, uexp, hdr, export_index):
    """[4x4] per instance of an instanced component, relative to the component.

    `PerInstanceSMData` is bulk-serialised after the tagged properties as
    (element size 64, count, count FMatrix). Found by that header and accepted only
    when every matrix has (0, 0, 0, 1) as its last column, as an FMatrix must.
    An FMatrix is row-major with row vectors, so its 16 floats read column-major are
    the column-vector matrix used here.
    """
    e = info['exports'][export_index]
    start = e['off'] - hdr
    d = uexp[start:start + e['size']]
    head = struct.pack('<i', INSTANCE_STRIDE)
    at = d.find(head)
    while at >= 0:
        if at + 8 <= len(d):
            n = struct.unpack_from('<i', d, at + 4)[0]
            if 0 < n <= MAX_INSTANCES and at + 8 + n * INSTANCE_STRIDE <= len(d):
                out = []
                for k in range(n):
                    f = struct.unpack_from('<16f', d, at + 8 + k * INSTANCE_STRIDE)
                    if (abs(f[3]) > 1e-3 or abs(f[7]) > 1e-3 or abs(f[11]) > 1e-3
                            or abs(f[15] - 1.0) > 1e-3):
                        out = None
                        break
                    out.append([[f[c * 4 + r] for c in range(4)] for r in range(4)])
                if out:
                    return out
        at = d.find(head, at + 1)
    return []


def _nested_vector(raw, names, field):
    """A Vector field of a nested tagged struct payload, or None."""
    try:
        want = names.index(field)
    except ValueError:
        return None
    needle = struct.pack('<ii', want, 0)
    at = bytes(raw).find(needle)
    if at < 0 or at + 49 + 12 > len(raw):
        return None
    return struct.unpack_from('<3f', raw, at + 49)


def _spline_matrix(props, cat_entry, names):
    """A 4x4 placing a mesh straight along a spline mesh's start-to-end segment.

    The mesh's own X extent is mapped onto the segment; Y and Z keep their size.
    """
    sp = props.get('SplineParams')
    if not sp or not cat_entry:
        return None
    a = _nested_vector(sp[2], names, 'StartPos')
    b = _nested_vector(sp[2], names, 'EndPos')
    if not a or not b:
        return None
    o, ext = cat_entry['origin'], cat_entry['extent']
    lo, length = o[0] - ext[0], 2 * ext[0]
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    span = math.sqrt(dx * dx + dy * dy + dz * dz)
    if span < 1e-3 or length < 1e-3:
        return None
    fx, fy, fz = dx / span, dy / span, dz / span
    # side axis: horizontal, perpendicular to the segment
    sx, sy, sz = -fy, fx, 0.0
    n = math.sqrt(sx * sx + sy * sy)
    if n < 1e-6:
        sx, sy, sz, n = 0.0, 1.0, 0.0, 1.0
    sx, sy = sx / n, sy / n
    ux, uy, uz = fy * sz - fz * sy, fz * sx - fx * sz, fx * sy - fy * sx
    k = span / length
    m = [[fx * k, sx, ux, 0.0], [fy * k, sy, uy, 0.0], [fz * k, sz, uz, 0.0],
         [0.0, 0.0, 0.0, 1.0]]
    for r in range(3):
        m[r][3] = a[r] - m[r][0] * lo
    return m


def _template_for(cls, component):
    """One component's merged class template, or {}."""
    try:
        return omd3templates.templates(cls).get(component or '') or {}
    except Exception:                                # noqa: BLE001
        return {}


def _chain(info, uexp, hdr):
    """(props, template_of, own, world) of one loaded package: an export's properties by
    name, a component's class template, its own transform and its world transform
    (4x4 rows) up its AttachParent chain."""
    props_of = {}
    world_of = {}

    def props(i):
        if i not in props_of:
            props_of[i] = dict(
                (p[0], p) for p in onslaught.read_props(
                    info, uexp, hdr, info['exports'][i]))
        return props_of[i]

    def template_of(i):
        """The class template of component export `i`, when a Blueprint actor owns it."""
        outer = info['exports'][i].get('outer') or 0
        if not 0 < outer <= len(info['exports']):
            return {}
        owner = _class_of(info, info['exports'][outer - 1])
        if not owner.endswith('_C'):
            return {}
        name = str(info['exports'][i].get('name') or '')
        if name.endswith(omd3templates.GEN_SUFFIX):
            name = name[:-len(omd3templates.GEN_SUFFIX)]
        try:
            return omd3templates.templates(owner).get(name) or {}
        except Exception:                            # noqa: BLE001
            return {}

    def own(i):
        p = props(i)
        t = template_of(i)
        loc = onslaught.as_vec(p['RelativeLocation'][2]) \
            if 'RelativeLocation' in p else t.get('loc', (0.0, 0.0, 0.0))
        rot = onslaught.as_vec(p['RelativeRotation'][2]) \
            if 'RelativeRotation' in p else t.get('rot', (0.0, 0.0, 0.0))
        scale = onslaught.as_vec(p['RelativeScale3D'][2]) \
            if 'RelativeScale3D' in p else t.get('scale', (1.0, 1.0, 1.0))
        return _matrix(loc or (0.0, 0.0, 0.0), rot or (0.0, 0.0, 0.0),
                       scale or (1.0, 1.0, 1.0))

    def world(i, seen=None):
        if i in world_of:
            return world_of[i]
        seen = seen or set()
        if i in seen:
            return own(i)
        seen.add(i)
        m = own(i)
        ref = props(i).get('AttachParent')
        if ref:
            parent = onslaught.as_int(ref[2])
            if parent and 0 < parent <= len(info['exports']):
                m = _matmul(world(parent - 1, seen), m)
        world_of[i] = m
        return m

    return props, template_of, own, world


#: UBoxComponent's BoxExtent default (uu).
BOX_EXTENT = (32.0, 32.0, 32.0)


def boxes(package_path):
    """[{export, actor, name, extent, matrix (4x4 rows), body}] for every box component
    of one package: its half size (instance, else class template, else BOX_EXTENT) and its
    BodyInstance fields (instance, else class template, else {}; omd3templates.body_fields).
    `actor` is the owning actor's export (1-based)."""
    info, uexp, hdr = _load(package_path)
    props, template_of, _own, world = _chain(info, uexp, hdr)
    names = info.get('names') or []
    out = []
    for i, e in enumerate(info['exports']):
        if not _class_of(info, e).endswith('BoxComponent'):
            continue
        p, t = props(i), template_of(i)
        extent = onslaught.as_vec(p['BoxExtent'][2]) if 'BoxExtent' in p else None
        body = t.get('body') or {}
        raw = (p.get('BodyInstance') or (0, 0, None))[2]
        if isinstance(raw, (bytes, bytearray)):
            body = dict(body, **omd3templates.body_fields(bytes(raw), names))
        out.append({'export': i + 1, 'actor': e.get('outer') or None,
                    'name': str(e.get('name') or '').replace(omd3templates.GEN_SUFFIX, ''),
                    'extent': list(extent or t.get('extent') or BOX_EXTENT),
                    'matrix': world(i), 'body': body})
    return out


def read_package(package_path, actor_parts=None, actor_meshes=None, keep_local=False):
    """Everything drawable in one package: meshes, lights and particle systems.

    One pass, one load, one chain walker shared between them. A light component has
    no transform of its own any more than a mesh component does -- 253 of Split
    Stairs' point lights sit at the origin unless the chain above them is walked --
    so the same `world()` serves all three.

    `keep_local` gives each Blueprint part its transform relative to its actor
    (`local`, column-major) and its component name, as riftlord.kitdonors reads them.
    """
    info, uexp, hdr = _load(package_path)
    if actor_parts is None:
        actor_parts = omd3actors.part_index()
    if actor_meshes is None:
        actor_meshes = omd3actors.mesh_index()
    props, template_of, own, world = _chain(info, uexp, hdr)

    def import_name(index):
        i = -index - 1
        imports = info.get('imports') or []
        if 0 <= i < len(imports):
            imp = imports[i]
            if isinstance(imp, dict):
                return imp.get('name') or imp.get('object_name')
        return None

    def object_ref(p, key):
        ref = p.get(key)
        if not ref:
            return None
        idx = onslaught.as_int(ref[2])
        if idx is None:
            return None
        if idx < 0:
            return import_name(idx)
        if 0 < idx <= len(info['exports']):
            return str(info['exports'][idx - 1].get('name') or '')
        return None

    pieces, lights, vfx, actors = [], [], [], []
    #: {actor export index: {component name: overridden fields}} for the whole
    #: package, built once. Only fields a placement actually states, so anything it
    #: leaves out still falls through to the class default.
    from riftlord.formats.omd3 import meshcat
    catalogue = meshcat.load()
    overrides = {}
    #: {(actor export, component name): component export index}, for every mesh
    #: component a placed actor owns in this package.
    component_at = {}
    for j, ce in enumerate(info['exports']):
        kind = str(ce.get('class') or ce.get('cls') or '')
        if 'MeshComponent' in kind and ce.get('outer'):
            component_at[(ce['outer'], str(ce.get('name') or '')
                          .replace('_GEN_VARIABLE', ''))] = j
        if 'StaticMeshComponent' not in kind:
            continue
        outer = ce.get('outer')
        if not outer:
            continue
        cname = str(ce.get('name') or '').replace('_GEN_VARIABLE', '')
        if not cname:
            continue
        cp = props(j)
        rec = {}
        for field, k in (('RelativeLocation', 'loc'), ('RelativeRotation', 'rot'),
                         ('RelativeScale3D', 'scale')):
            if field in cp:
                v = onslaught.as_vec(cp[field][2])
                if v:
                    rec[k] = list(v)
        sm = cp.get('StaticMesh')
        if sm:
            midx = onslaught.as_int(sm[2])
            if midx is not None and midx < 0:
                nm = import_name(midx)
                if nm:
                    rec['mesh'] = nm
        #: A placement's material override, which only the plain-component path used
        #: to read. `BP_Sky_Sphere_C` is why it matters: its component inherits its
        #: mesh, so it takes the Blueprint path, and without this its material -- and
        #: therefore the sky texture -- was lost and the sky drew as a flat shell.
        mat = _override_material(cp, import_name)
        if mat:
            rec['material'] = mat
        if rec:
            overrides.setdefault(outer, {})[cname] = rec

    for i, e in enumerate(info['exports']):
        cls = str(e.get('class') or e.get('cls') or '')
        p = props(i)

        # The rift is an actor of its own. It is NOT the exit path point: North Wing's
        # exit is `OMDPathPoint6` and its rift is a `BP_OMDRift_C` somewhere near it,
        # and calling the point the rift made the tool report a path point as one.
        if 'OMDRift' in cls or cls in ('BP_OMDRift_C',):
            base = None
            ref = p.get('RootComponent')
            if ref:
                ridx = onslaught.as_int(ref[2])
                if ridx and 0 < ridx <= len(info['exports']):
                    base = world(ridx - 1)
            if base is None:
                base = own(i)
            actors.append({'kind': 'rift', 'cls': cls, 'name': e['name'],
                           'loc': [round(base[r][3], 2) for r in range(3)]})

        if cls in LIGHT_CLASSES:
            m = world(i)
            lights.append({
                'kind': cls.replace('Component', ''),
                'loc': [round(m[r][3], 2) for r in range(3)],
                'colour': _colour_of(p),
                'intensity': _float_of(p, 'Intensity', 5000.0),
                'radius': _float_of(p, 'AttenuationRadius', 1000.0),
                'export': i + 1,
                #: the actor that places it, so a light can be moved or removed whole
                'actor': e.get('outer') or None,
                #: where it points (its X axis), and a spot light's outer cone in degrees
                'dir': [round(m[r][0], 4) for r in range(3)],
                'cone': _float_of(p, 'OuterConeAngle', SPOT_OUTER_CONE),
            })
            continue

        if 'ParticleSystemComponent' in cls or cls == 'NiagaraComponent':
            m = world(i)
            #: the actor that places it, so an effect can be removed or used as a donor
            outer = e.get('outer') or 0
            owner = info['exports'][outer - 1] if 0 < outer <= len(info['exports']) else {}
            #: the placement's system, else the one its Blueprint class names
            vfx.append({
                'loc': [round(m[r][3], 2) for r in range(3)],
                'template': object_ref(p, 'Template' if cls != 'NiagaraComponent'
                                       else 'Asset')
                or template_of(i).get('particle') or 'particles',
                'export': i + 1,
                'actor': outer or None,
                'actorName': str(owner.get('name') or '') or None,
                'actorClass': _class_of(info, owner) if owner else None,
            })
            continue

        #: Skeletal components are drawn as their static twin where one exists.
        if 'SkeletalMeshComponent' in cls:
            ref = p.get('SkeletalMesh')
            if not ref:
                continue                             # inherited: the class path has it
            idx = onslaught.as_int(ref[2])
            if idx is None or idx >= 0:
                continue
            raw = import_name(idx)
            #: the twin if there is one, else the skeletal mesh itself
            twin = static_twin(raw, catalogue) or raw
            if not twin:
                continue
            entry = {'mesh': twin, 'matrix': _flat(world(i)),
                     'source': 'skeletal', 'skeletal': raw, 'export': i + 1}
            override = _override_material(p, import_name)
            if override:
                entry['material'] = override
            if not omd3templates.drawn(p, template_of(i)):
                entry['hidden'] = True
            pieces.append(entry)
            continue

        if 'StaticMeshComponent' in cls:
            ref = p.get('StaticMesh')
            if not ref:
                continue
            idx = onslaught.as_int(ref[2])
            if idx is None or idx >= 0:
                continue
            mesh = import_name(idx)
            if not mesh:
                continue
            entry = {'mesh': mesh, 'matrix': _flat(world(i)),
                     'source': 'component', 'export': i + 1}
            override = _override_material(p, import_name)
            if override:
                entry['material'] = override
            if not omd3templates.drawn(p, template_of(i)):
                entry['hidden'] = True
            if is_liquid(mesh):
                entry['liquid'] = True
                entry['liquidKind'] = liquid_kind(mesh)
            if cls == FOLIAGE_CLASS:
                entry['foliage'] = True
            pieces.append(entry)
            #: An instanced component draws once per instance: the first instance
            #: takes this entry, the rest are appended after the level's other pieces.
            if cls in INSTANCED_CLASSES:
                local = instance_matrices(info, uexp, hdr, i)
                if local:
                    base_m = world(i)
                    entry['matrix'] = _flat(_matmul(base_m, local[0]))
                    entry['instanced'] = True
                    for m in local[1:]:
                        extra = dict(entry)
                        extra['matrix'] = _flat(_matmul(base_m, m))
                        extra['late'] = True
                        pieces.append(extra)
            continue

        #: A spline mesh, drawn straight from its start to its end point.
        if cls == 'SplineMeshComponent':
            ref = p.get('StaticMesh')
            idx = onslaught.as_int(ref[2]) if ref else None
            mesh = import_name(idx) if idx is not None and idx < 0 else None
            local = _spline_matrix(
                p, catalogue.get(mesh) if mesh else None,
                [n[0] if isinstance(n, (list, tuple)) else n for n in info['names']])
            if mesh and local:
                entry = {'mesh': mesh, 'matrix': _flat(_matmul(world(i), local)),
                         'source': 'spline', 'export': i + 1, 'late': True}
                override = _override_material(p, import_name)
                if override:
                    entry['material'] = override
                pieces.append(entry)
            continue

        if not cls.endswith('_C'):
            continue
        parts = actor_parts.get(cls)
        if not parts and cls in actor_meshes:
            parts = [{'mesh': actor_meshes[cls][0], 'loc': [0.0, 0.0, 0.0],
                      'rot': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0]}]
        #: a class whose asset differs from it in case: its pieces are late (after the
        #: level's other pieces; docs/MAP-EDITOR.md, Piece ids)
        late = omd3actors.found_by_case_only(cls)
        if not parts and late:
            parts = omd3actors.late_parts(cls)
        if not parts:
            continue
        base = None
        ref = p.get('RootComponent')
        if ref:
            ridx = onslaught.as_int(ref[2])
            if ridx and 0 < ridx <= len(info['exports']):
                base = world(ridx - 1)
        if base is None:
            base = own(i)
        # WHAT THIS PLACEMENT OVERRIDES on the class's components.
        #
        # A Blueprint's components are class defaults, and the level may override any
        # of them per placement -- which for a force-field door is the whole point.
        # `BP_Door_Forcefield_C` ships a 100uu plane at scale 1 and every placed one
        # states its own `RelativeScale3D`: (10, 14, 1) here, (8, 13, 1) and
        # (10, 26, 1) on Abandoned Passage. Composing the class default drew a 100uu
        # plane in a 1400uu doorway, which reads as the door not being drawn at all.
        #
        # So an overridden component IS a separate export in the level, outered to its
        # actor -- found by outer index, never by name, because `DoorMesh` repeats for
        # every door in the level.
        ov = overrides.get(i + 1) or {}
        for part in parts:
            o = ov.get(part.get('component') or '') or {}
            #: The level's own export of the component, walked up its AttachParent
            #: chain with the class templates filling what the instance leaves out.
            j = component_at.get((i + 1, part.get('component') or ''))
            if j is not None:
                m = world(j)
            else:
                m = _matmul(base, _matrix(o.get('loc', part['loc']),
                                          o.get('rot', part['rot']),
                                          o.get('scale', part['scale'])))
            mesh_name = o.get('mesh', part['mesh'])
            #: a skeletal part is drawn as its static twin if it has one, and as
            #: itself if it does not -- it used to be skipped, which lost every
            #: environmental trap in the campaign
            if part.get('skeletal'):
                mesh_name = static_twin(mesh_name, catalogue) or mesh_name
                if not mesh_name:
                    continue
            # The actor's export, not the part's, so moving one means moving the actor.
            entry = {'mesh': mesh_name, 'matrix': _flat(m),
                     'source': 'blueprint', 'actor': cls, 'export': i + 1}
            if late:
                entry['late'] = True
            if keep_local:
                entry['local'] = _flat(_matmul(_inverse(base), m))
                entry['componentName'] = part.get('component')
                if part.get('skeletal'):
                    entry['skeletal'] = True
            #: the component's own export, which holds its light map id (riftlord.lightbake)
            if j is not None:
                entry['component'] = j + 1
            #: the placement's override first, then the CLASS's -- a force-field
            #: door states its material on the class, not per placement
            if o.get('material') or part.get('material'):
                entry['material'] = o.get('material') or part['material']
            if is_liquid(mesh_name):
                entry['liquid'] = True
                entry['liquidKind'] = liquid_kind(mesh_name)
            #: hidden in game (a trap's range cube): kept for its id, not drawn
            if not omd3templates.drawn(props(j) if j is not None else {},
                                       _template_for(cls, part.get('component'))):
                entry['hidden'] = True
            pieces.append(entry)
    #: Landscape terrain: one synthetic mesh per Landscape actor, served by
    #: omd3landscape under its own name.
    try:
        from riftlord.formats.omd3 import omd3landscape
        for land in omd3landscape.landscapes(info, uexp, hdr, props, world):
            pieces.append({'mesh': omd3landscape.mesh_name(package_path, land['export']),
                           'matrix': _flat(land['matrix']), 'source': 'landscape',
                           'export': land['export'], 'late': True,
                           'material': land['material']})
    except Exception:                                # noqa: BLE001
        pass
    return pieces, lights, vfx, actors


#: The streamed sublevel holding a level's ambient sounds; read for its sounds only.
SOUND_SUBLEVEL = '_Audio'
#: The actor class of a placed ambient sound.
SOUND_CLASS = 'AmbientSound'


def sound_sublevel(name):
    """The package path of a level's `_Audio` sublevel, or None."""
    try:
        path = navpatch.map_path(name) + SOUND_SUBLEVEL
        if path + '.umap' in navpatch._index():
            return path
        if path + '.umap' in navpatch.live_index():
            return path
    except (Exception, SystemExit):                  # noqa: BLE001
        pass
    return None


def sounds_of(package_path):
    """The placed ambient sounds of one package: [{loc, cue, export, actor, actorName,
    actorClass}], one per AmbientSound whose AudioComponent names a Sound. `export` is
    the AudioComponent and `actor` the AmbientSound (both 1-based). An AmbientSound with
    no Sound of its own (LevelMusic, MIN_GRP_*) is played by the level script and left
    out."""
    info, uexp, hdr = _load(package_path)
    exports = info['exports']
    imports = info.get('imports') or []
    out = []
    for i, e in enumerate(exports):
        if _class_of(info, e) != SOUND_CLASS:
            continue
        p = dict((q[0], q) for q in onslaught.read_props(info, uexp, hdr, e))
        root = onslaught.as_int(p['RootComponent'][2]) if 'RootComponent' in p else None
        if not root or not 0 < root <= len(exports):
            continue
        cp = dict((q[0], q) for q in onslaught.read_props(info, uexp, hdr, exports[root - 1]))
        sound = onslaught.as_int(cp['Sound'][2]) if 'Sound' in cp else None
        if sound is None or sound >= 0 or -sound - 1 >= len(imports):
            continue
        cue = imports[-sound - 1].get('name') or imports[-sound - 1].get('object_name')
        loc = onslaught.as_vec(cp['RelativeLocation'][2]) \
            if 'RelativeLocation' in cp else (0.0, 0.0, 0.0)
        out.append({'loc': [round(v, 2) for v in loc], 'cue': str(cue), 'export': root,
                    'actor': i + 1, 'actorName': str(e.get('name') or ''),
                    'actorClass': SOUND_CLASS})
    return out


def band(values, drop=0.02):
    """[low, high] with the outermost `drop` of the values discarded.

    A level's true bounds are useless for framing a camera on it. One `Plane` sits at
    z=976,002 in Scenario_14 and z=891,226 in Scenario_12, so the true bounds are two
    hundred times the play area and the level opens as a speck. Split Stairs is really
    about 8,000 units across where its bounds claim 49,000.
    """
    if not values:
        return [0.0, 0.0]
    ordered = sorted(values)
    n = len(ordered)
    lo = ordered[min(n - 1, int(n * drop))]
    hi = ordered[max(0, int(n * (1 - drop)) - 1)]
    if hi <= lo:
        return [ordered[0], ordered[-1]]
    return [round(lo, 2), round(hi, 2)]


def _scenario_key(name, sky_module):
    """The sky catalogue's key for a level. A `_WS` level is filed without it."""
    skies = sky_module.load()
    for candidate in (name, name.replace('_WS', '')):
        if candidate in skies:
            return candidate
    return name


def packages(name):
    """[(sublevel tag, package path, extra)] of a level's packages in the order `read` reads
    them, persistent level first; `extra` for one of extra_sublevels. A tag two packages
    would share takes the leaf name."""
    extras = extra_sublevels(name)
    out, seen = [], {}
    for pkg in sublevels(name) + extras:
        leaf = pkg.rsplit('/', 1)[-1]
        if pkg in extras and not leaf.startswith(name):
            leaf = name + '_' + leaf.rsplit('_', 1)[-1]
        # A mission's level streams the source map's sublevels, whose leaves
        # start with the source's name, not the mission's: take the tag from
        # whichever known suffix the leaf ends with.
        tag = leaf[len(name):].lstrip('_') if leaf.startswith(name) else ''
        if not tag and leaf != name:
            tag = next((suffix.lstrip('_') for suffix in GEOMETRY_SUBLEVELS
                        if suffix and leaf.endswith(suffix)), '')
        tag = tag or 'persistent'
        if seen.get(tag, pkg) != pkg:
            tag = leaf
        seen[tag] = pkg
        out.append((tag, pkg, pkg in extras))
    return out


def read(name, want_geometry=True):
    """One shipped level: its drawable pieces and its spawners, doors and routes."""
    pieces, lights, vfx, rifts, subs, sounds = [], [], [], [], [], []
    #: {sublevel tag: package path}; a tag that two packages would share takes the leaf name
    sub_packages = {}
    if want_geometry:
        actor_parts = omd3actors.part_index()
        actor_meshes = omd3actors.mesh_index()
        for tag, pkg, extra in packages(name):
            sub_packages[tag] = pkg
            subs.append(tag)
            try:
                got, lit, fx, act = read_package(pkg, actor_parts, actor_meshes)
            except Exception:                        # noqa: BLE001
                continue
            for item in got + lit + fx + act:
                item['sub'] = tag
            if tag == FOLIAGE_SUBLEVEL or tag.endswith('_' + FOLIAGE_SUBLEVEL):
                for item in got:
                    item['foliage'] = True
            if extra:
                for item in got:
                    item['late'] = True
            pieces.extend(got)
            lights.extend(lit)
            vfx.extend(fx)
            rifts.extend(act)
            try:
                sounds.extend(dict(s, sub=tag) for s in sounds_of(pkg))
            except Exception:                        # noqa: BLE001
                pass
        #: the `_Audio` sublevel, read for its sounds only (it draws nothing)
        audio = sound_sublevel(name)
        if audio:
            try:
                sounds.extend(dict(s, sub=SOUND_SUBLEVEL.lstrip('_'))
                              for s in sounds_of(audio))
            except Exception:                        # noqa: BLE001
                pass
        omd3templates.save()
        #: Pieces added by later readers (further instances, splines, terrain, extra
        #: sublevels) go after the rest, so ids saved in plans keep their pieces.
        pieces = ([pc for pc in pieces if not pc.get('late')]
                  + [pc for pc in pieces if pc.get('late')])
        for pc in pieces:
            pc.pop('late', None)
    wiring = onslaught.map_report(name)
    sky = {}
    skybox = next((pc for pc in pieces if pc['mesh'] in SKYBOX_MESHES), None)
    try:
        from riftlord.formats.omd3 import omd3sky
        found = omd3sky.load().get(_scenario_key(name, omd3sky))
        if found:
            sky = {'name': found.get('name'), 'title': found.get('title'),
                   'look': found.get('look', {})}
    except Exception:                                # noqa: BLE001
        sky = {}
    # The sky is a mesh with a per-level material, not only a fog colour, so its
    # texture is resolved here and the front end can draw the real thing.
    if skybox:
        sky['mesh'] = skybox['mesh']
        sky['matrix'] = skybox['matrix']
        sky['material'] = skybox.get('material')
        if skybox.get('material'):
            try:
                from riftlord.mapeditor import omd3serve
                sky['texture'] = omd3serve.material_texture(skybox['material'])
            except Exception:                        # noqa: BLE001
                pass
    # The translation of a column-major 4x4 sits at 12, 13, 14.
    xs = [p['matrix'][12] for p in pieces] or [0.0]
    ys = [p['matrix'][13] for p in pieces] or [0.0]
    zs = [p['matrix'][14] for p in pieces] or [0.0]
    # A stable id per placement, so an edit can name the piece it moved. The order
    # is the order the packages were read, which is the same every time.
    for n, piece in enumerate(pieces):
        piece['id'] = n
    return {
        'level': name,
        'sky': sky,
        'sublevels': subs,
        'subPackages': sub_packages,
        'rifts': rifts,
        'lights': lights,
        'vfx': vfx,
        'sounds': sounds,
        'pieces': pieces,
        'bounds': [[min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]],
        'focus': [band(xs, 0.02), band(ys, 0.02), band(zs, 0.02)],
        'spawners': wiring['spawners'],
        'doors': wiring['doors'],
        'points': wiring['points'],
        #: {material: base-colour texture} for every material a PIECE carries.
        #:
        #: A mesh is often painted by the actor that places it rather than carrying a
        #: material itself -- `Scenario_13_Ground` has none of its own and is painted
        #: `Ground_Dirt_Rock_Mud_MP` by its Blueprint -- and the renderer had no way
        #: to know, so it drew those untextured.
        'materials': _piece_materials(pieces),
    }


def _piece_materials(pieces):
    """{material name: texture path} for the materials the pieces name."""
    try:
        from riftlord.mapeditor import omd3serve
    except Exception:                                # noqa: BLE001
        return {}
    out = {}
    for pc in pieces:
        name = pc.get('material')
        if not name or name in out:
            continue
        try:
            out[name] = omd3serve.material_texture(name)
        except Exception:                            # noqa: BLE001
            out[name] = None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--level', required=True)
    ap.add_argument('--json')
    ap.add_argument('--no-geometry', action='store_true')
    args = ap.parse_args(argv)
    got = read(args.level, want_geometry=not args.no_geometry)
    print('%s: %d drawable piece(s) from %d sublevel(s)'
          % (got['level'], len(got['pieces']), len(sublevels(args.level))))
    kinds = collections.Counter(p['source'] for p in got['pieces'])
    print('   ', dict(kinds))
    used = collections.Counter(p['mesh'] for p in got['pieces'])
    print('    commonest meshes:', used.most_common(6))
    print('    bounds %s .. %s'
          % ([round(v) for v in got['bounds'][0]], [round(v) for v in got['bounds'][1]]))
    print('    %d spawner(s), %d door(s), %d path point(s)'
          % (len(got['spawners']), len(got['doors']), len(got['points'])))
    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(got, fh)
        print('    wrote', args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
