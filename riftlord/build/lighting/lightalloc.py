"""Light maps for the pieces a plan adds, and the Static lights its added actors bring.

A plan's added meshes and kit pieces are written into the persistent level copy
(apply.apply_plan). When the build bakes (lightbake.decide), each of their static mesh
components is a *receiver*: it keeps Static mobility and its LODData names a
MapBuildDataId of its own, and the bake gives that id a light map in new atlases of the
persistent level's BuiltData. An added actor's Static light components get LightGuids of
their own, a LightBuildData entry in that registry, and light the bake.

Both sides read the same list: `receivers(plan)` and `light_guid` here (main process),
`extend` and `pack` in the baker (lightbaker.bake).

Receivers:
* an added mesh (`kind` 'mesh'): the root of the lifted StaticMeshActor;
* an added kit piece (`kind` 'kit', not a kitactors class): each static mesh part
  leveldata.kit_parts lists, by component name; apply skips a part whose lifted component
  states a mobility other than Static.

A receiver's light map is LightMapResolution texels square (the mesh's own, as Lightmass
allocates it), one texel of padding inside, placed in atlases the size of the level's own
first atlas.
"""

import copy
import functools
import hashlib
import json
import os
import struct

import numpy as np

#: Added piece kinds that receive light maps.
RECEIVE_KINDS = ('mesh', 'kit')
#: Mobility a receiver and a baked light keep.
STATIC = 'EComponentMobility::Static'
#: Light component classes whose Static instances the bake lights with.
LIGHT_CLASSES = ('PointLightComponent', 'SpotLightComponent', 'RectLightComponent')
#: UStaticMesh::LightMapResolution when a mesh states none (StaticMesh.cpp).
DEFAULT_RESOLUTION = 4
#: Smallest square a receiver is given, texels: one texel inside the padding each way.
MIN_RESOLUTION = 4
#: FStaticMeshComponentLODInfo: strip flags (2 bytes), MapBuildDataId (16).
LOD_INFO_SIZE = 18
#: The strip flags every shipped LODData entry carries (Scenario_01_Castle: 1,282 of 1,282).
LOD_STRIP = b'\x01\x01'
#: Object names of the atlases a bake adds: HQ and LQ, numbered per atlas.
HQ_NAME, LQ_NAME = 'HQ_RiftLordLightmap%d', 'LQ_RiftLordLightmap%d'
#: FStaticShadowDepthMapData of a Static light: identity WorldToLight, no depth map
#: (Scenario_01_Environment: 13 of 13 entries).
STATIC_LIGHT_BUILD = {'channel': -1, 'world_to_light': struct.pack(
    '<16f', 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1), 'size': (0, 0), 'depth': b''}
#: Cache of mesh light map resolutions, beside the lightscene mesh cache.
RESOLUTION_CACHE = 'lightmap-resolutions.v1.json'


# -- ids ----------------------------------------------------------------------------

def _guid(*parts):
    return hashlib.md5('|'.join(str(p) for p in parts).encode()).digest()


def receiver_guid(item, component):
    """MapBuildDataId of added item `item`'s component `component` (None: the root)."""
    return _guid('riftlord-receiver', item, component or '')


def light_guid(item, component):
    """LightGuid of added item `item`'s light component `component`."""
    return _guid('riftlord-light', item, component)


# -- which components receive -------------------------------------------------------

def _plan_matrix(item):
    """Column-vector 4x4 world matrix of an added item (plans store column-major)."""
    return np.array(item['matrix'], np.float64).reshape(4, 4).T


def receivers(plan):
    """[{item, component, mesh, matrix (4x4 rows), guid (hex)}] of a plan's added pieces;
    `component` None is an added mesh's root."""
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import kitactors
    from riftlord.mapeditor import leveldata
    out = []
    for n, item in enumerate((plan or {}).get('added') or []):
        kind = item.get('kind', 'mesh')
        if kind not in RECEIVE_KINDS or not item.get('name') or not item.get('matrix'):
            continue
        world = _plan_matrix(item)
        if kind == 'mesh':
            out.append({'item': n, 'component': None, 'mesh': item['name'],
                        'matrix': world.tolist(), 'guid': receiver_guid(n, None).hex()})
            continue
        if kitactors.handles(item['name']):
            continue
        seen = set()
        for part in leveldata.kit_parts(item['name']).get('parts') or []:
            comp = part.get('component')
            if part.get('skeletal') or not comp or not part.get('mesh') or comp in seen:
                continue
            seen.add(comp)
            local = np.array(omd3level._matrix(part.get('loc') or (0, 0, 0),
                                               part.get('rot') or (0, 0, 0),
                                               part.get('scale') or (1, 1, 1)), np.float64)
            out.append({'item': n, 'component': comp, 'mesh': part['mesh'],
                        'matrix': (world @ local).tolist(),
                        'guid': receiver_guid(n, comp).hex()})
    return out


def by_item(plan):
    """{item index: {component or None: guid bytes}} of `receivers(plan)`."""
    out = {}
    for r in receivers(plan):
        out.setdefault(r['item'], {})[r['component']] = bytes.fromhex(r['guid'])
    return out


@functools.lru_cache(maxsize=64)
def _template_pair(level_path):
    """(HQ export, LQ export) of a level's BuiltData atlases, or None."""
    from riftlord.build.lighting import lightmaps
    try:
        bd = lightmaps.BuiltData(*lightmaps.fetch(level_path + '_BuiltData'))
    except Exception:                                # noqa: BLE001
        return None
    return template_pair(bd)


def template_pair(bd):
    """(HQ export index, LQ export index) of the first PF_DXT5 and PF_DXT1 light map
    atlases of the same size, or None."""
    sizes = {}
    for i in sorted(bd.textures):
        if bd.kinds[i] != 'LightMapTexture2D':
            continue
        f = bd.textures[i]['formats'][0]
        sizes.setdefault((f['pixel_format'], tuple(f['data']['size'])), i)
    for (fmt, size), i in sorted(sizes.items(), key=lambda kv: kv[1]):
        if fmt == 'PF_DXT5' and ('PF_DXT1', size) in sizes:
            return i, sizes[('PF_DXT1', size)]
    return None


def active(level_name, plan):
    """Whether this build gives added pieces light maps: it bakes, the persistent level is
    baked, and that level's BuiltData has an atlas pair to copy."""
    from riftlord.build.lighting import lightbake
    from riftlord.build.navigation import navpatch
    if not (lightbake._bake_now and lightbake.BAKE_PERSISTENT):
        return False
    return _template_pair(navpatch.map_path(level_name)) is not None


# -- the level copy -----------------------------------------------------------------

def under(pkg, actor):
    """0-based indices of every export whose outer chain reaches export `actor` (1-based)."""
    from riftlord.build import levelbuild
    out = []
    for j, e in enumerate(pkg.exports):
        o = struct.unpack_from('<i', e['meta'], levelbuild.OUTER_AT)[0]
        for _hop in range(16):
            if o <= 0:
                break
            if o == actor:
                out.append(j)
                break
            o = struct.unpack_from('<i', pkg.exports[o - 1]['meta'], levelbuild.OUTER_AT)[0]
    return out


def _mobility(builder, component):
    data = builder.pkg.exports[component - 1]['data']
    span = builder._tag_span(bytearray(data), 'Mobility')
    if span is None:
        return None
    names = [n for n, _h in builder.pkg.names]
    return names[struct.unpack_from('<i', data, span[2])[0]]


def receive(builder, component, guid):
    """Point every LODData entry of a static mesh component at `guid` (one entry when it
    has none). Returns False, changing nothing, when the component states a mobility other
    than Static or its LODData carries more than ids (painted vertex colours)."""
    from riftlord.mapeditor import fabemit
    if _mobility(builder, component) not in (None, STATIC):
        return False
    pkg = builder.pkg
    data = bytearray(pkg.exports[component - 1]['data'])
    try:
        at, count = fabemit.lod_data(data, builder._terminator(data) + 8)
    except SystemExit:
        return False
    if count == 0:
        data[at + 4:at + 4] = LOD_STRIP + guid
        struct.pack_into('<i', data, at, 1)
    for k in range(max(count, 1)):
        o = at + 4 + k * LOD_INFO_SIZE + 2
        data[o:o + 16] = guid
    pkg.exports[component - 1]['data'] = bytes(data)
    pkg.exports[component - 1]['size'] = len(data)
    return True


def receive_kit(builder, actor, guids, report):
    """Give a lifted kit actor's static mesh components their MapBuildDataIds
    (`guids`: {component name: guid}). Returns how many."""
    from riftlord.build import apply
    done = 0
    for j in under(builder.pkg, actor):
        if apply._class_at(builder.pkg, j) != 'StaticMeshComponent':
            continue
        guid = guids.get(full_name(builder.pkg, j))
        if guid is not None and receive(builder, j + 1, guid):
            done += 1
    report['light_receivers'] = report.get('light_receivers', 0) + done
    return done


def light_actor(builder, actor, item_index, report):
    """New LightGuids (light_guid) on a lifted actor's Static light components. Returns
    how many."""
    from riftlord.build import apply
    done = 0
    for j in under(builder.pkg, actor):
        if apply._class_at(builder.pkg, j) not in LIGHT_CLASSES:
            continue
        data = bytearray(builder.pkg.exports[j]['data'])
        span = builder._tag_span(data, 'LightGuid')
        if span is None:
            continue
        data[span[2]:span[2] + 16] = light_guid(item_index, full_name(builder.pkg, j))
        builder.pkg.exports[j]['data'] = bytes(data)
        done += 1
    report['baked_lights'] = report.get('baked_lights', 0) + done
    return done


# -- the lights the added actors bring ----------------------------------------------

def _donor(item):
    """(package, 0-based export) the build lifts an added kit item from (apply)."""
    from riftlord.build import apply
    from riftlord.mapeditor import controllers
    return controllers.donor(item) or apply._compound_donor(item) or apply.donor_for(item['name'])


def added_lights(plan):
    """[light record (lightscene.lights), guid as hex] of the Static point, spot and rect
    light components of the plan's added kit actors, where the build places them."""
    from riftlord.build import apply
    from riftlord.build.lighting import lightscene
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import kitactors
    out = []
    for n, item in enumerate((plan or {}).get('added') or []):
        if item.get('kind') != 'kit' or not item.get('name') or not item.get('matrix'):
            continue
        if kitactors.handles(item['name']):
            continue
        donor = _donor(item)
        if not donor:
            continue
        pkg, actor = donor
        names = [s for s, _h in pkg.names]
        kids = under(pkg, actor + 1)
        lights = [j for j in kids if apply._class_at(pkg, j) in LIGHT_CLASSES]
        if not lights:
            continue
        templates = lightscene._class_templates(apply._class_at(pkg, actor))

        def props(j):
            data = pkg.exports[j]['data']
            own = lightscene.decode_props({'names': names}, data, 0, {'off': 0, 'size': len(data)})
            merged = dict(lightscene.DEFAULTS)
            merged.update(templates.get(_component_name(pkg, j)) or {})
            merged.update(own)
            return own, merged

        world = {}

        def at(j, depth=0):
            if j in world:
                return world[j]
            own, p = props(j)
            m = np.array(omd3level._matrix(p.get('RelativeLocation', (0.0, 0.0, 0.0)),
                                           p.get('RelativeRotation', (0.0, 0.0, 0.0)),
                                           p.get('RelativeScale3D', (1.0, 1.0, 1.0))))
            parent = own.get('AttachParent')
            if depth < 16 and isinstance(parent, tuple) and 0 < parent[1] <= len(pkg.exports):
                m = at(parent[1] - 1, depth + 1) @ m
            world[j] = m
            return m
        root = _root_of(pkg, actor, names)
        if root is None:
            continue
        rel = np.linalg.inv(at(root))
        place = _plan_matrix(item)
        for j in lights:
            own, p = props(j)
            if p.get('Mobility', None) != STATIC:
                continue
            kind = lightscene.LIGHT_KINDS[apply._class_at(pkg, j)]
            rec = lightscene.light_record(kind, light_guid(n, full_name(pkg, j)), p,
                                          place @ rel @ at(j),
                                          'added', n + 1)
            rec['guid'] = rec['guid'].hex()
            out.append(rec)
    return out


def full_name(pkg, j):
    """Export `j`'s (0-based) object name with its FName number (`Name_N`)."""
    from riftlord.build import levelbuild
    bare = levelbuild._name_of(pkg, j)
    number = struct.unpack_from('<i', pkg.exports[j]['meta'], levelbuild.NAME_AT + 4)[0]
    return bare if number == 0 else '%s_%d' % (bare, number - 1)


def _component_name(pkg, j):
    """Export `j`'s object name as its class template names the component."""
    name = full_name(pkg, j)
    return name[:-len('_GEN_VARIABLE')] if name.endswith('_GEN_VARIABLE') else name


def _root_of(pkg, actor, names):
    """0-based export of an actor's RootComponent, or None."""
    from riftlord.build.lighting import lightscene
    data = pkg.exports[actor]['data']
    own = lightscene.decode_props({'names': names}, data, 0, {'off': 0, 'size': len(data)})
    ref = own.get('RootComponent')
    if isinstance(ref, tuple) and 0 < ref[1] <= len(pkg.exports):
        return ref[1] - 1
    return None


# -- the baker side -----------------------------------------------------------------

def _cache_path():
    from riftlord.build.lighting import lightscene
    return os.path.join(lightscene._cache_dir(), RESOLUTION_CACHE)


_resolutions = None


def resolution(mesh):
    """A static mesh's LightMapResolution (DEFAULT_RESOLUTION when unstated), or None
    when the mesh cannot be read."""
    global _resolutions
    if _resolutions is None:
        try:
            with open(_cache_path()) as fh:
                _resolutions = json.load(fh)
        except (OSError, ValueError):
            _resolutions = {}
    if mesh in _resolutions:
        return _resolutions[mesh]
    from riftlord.core import uetags
    from riftlord.formats.omd3 import meshcat
    from riftlord.formats.omd3 import omd3smwrite
    info = meshcat.load().get(mesh)
    got = None
    if info:
        try:
            p = uetags.get(omd3smwrite.Template(info['path']).props, 'LightMapResolution')
            got = int(p.value) if p is not None else DEFAULT_RESOLUTION
        except (Exception, SystemExit):              # noqa: BLE001
            got = None
    _resolutions[mesh] = got
    part = _cache_path() + '.part%d' % os.getpid()
    with open(part, 'w') as fh:
        json.dump(_resolutions, fh)
    os.replace(part, _cache_path())
    return got


def _pow2(n):
    p = 1
    while p < n:
        p *= 2
    return p


def pack(sizes, width, height):
    """[(atlas, x, y)] for squares of `sizes` texels (each at most `height`), in an atlas
    `width` x `height` (a power of two each): every square in a power-of-two cell, larger
    first, the order of `sizes` among equals."""
    order = sorted(range(len(sizes)), key=lambda k: (-_pow2(sizes[k]), k))
    free = {}                                    # cell size -> [(atlas, x, y)]
    atlases = 0
    out = [None] * len(sizes)

    def take(cell):
        nonlocal atlases
        if free.get(cell):
            return free[cell].pop(0)
        if cell >= height:
            atlas = atlases
            atlases += 1
            for x in range(height, width, height):
                free.setdefault(height, []).append((atlas, x, 0))
            return atlas, 0, 0
        atlas, x, y = take(cell * 2)
        free.setdefault(cell, []).extend([(atlas, x + cell, y), (atlas, x, y + cell),
                                          (atlas, x + cell, y + cell)])
        free[cell].sort()
        return atlas, x, y
    for k in order:
        out[k] = take(_pow2(sizes[k]))
    return out


def _new_texture(bd, template, name):
    """Append a blank copy of texture export `template` named `name`; its index."""
    pkg = bd.pkg
    meta = bytearray(pkg.exports[template]['meta'])
    struct.pack_into('<ii', meta, 16, pkg.add_name(name), 0)
    first = struct.unpack_from('<i', meta, 84)[0]
    counts = struct.unpack_from('<4i', meta, 88)
    deps = list(pkg.preload[first:first + sum(counts)]) if first >= 0 else []
    struct.pack_into('<i', meta, 84, len(pkg.preload))
    struct.pack_into('<4i', meta, 88, 0, 0, 0, 0)
    pkg.exports.append({'meta': bytes(meta), 'data': pkg.exports[template]['data'],
                        'size': pkg.exports[template]['size'], 'offset': 0})
    new = len(pkg.exports) - 1
    kinds = ('serialization_before_serialization', 'create_before_serialization',
             'serialization_before_create', 'create_before_create')
    k = 0
    for slot, count in enumerate(counts):
        for dep in deps[k:k + count]:
            pkg.add_preload_dependency(new, dep, kinds[slot])
        k += count
    tex = copy.deepcopy(bd.textures[template])
    for mip in tex['formats'][0]['data']['mips']:
        mip['payload'] = bytes(len(mip['payload']))
    bd.textures[new] = tex
    bd.kinds.append(bd.kinds[template])
    pkg.add_preload_dependency(bd.registry_index, new + 1, 'create_before_serialization')
    bd.names = [s for s, _h in pkg.names]
    return new


def extend(bd, path, receivers_, lights=()):
    """Add the receivers' light maps (new atlases copied blank from the level's own pair,
    registry entries) and the lights' LightBuildData to the persistent level's BuiltData
    `bd`. Returns [component] (lightscene.components) for the receivers whose mesh reads,
    each holding its registry entry's light map."""
    pair = template_pair(bd)
    if pair is None:
        raise SystemExit('lightbake: %s has no light map atlas to copy for added pieces'
                         % path.rsplit('/', 1)[-1])
    dims = bd.textures[pair[0]]['formats'][0]['data']['mips'][0]['dims']
    width, height = dims[0], dims[1] // 2
    rows = []
    for r in receivers_:
        res = resolution(r['mesh'])
        if res:
            rows.append((r, min(max(int(res), MIN_RESOLUTION), height)))
    places = pack([s for _r, s in rows], width, height)
    textures = {}
    out = []
    known = {bytes(g) for g, _m in bd.registry['meshes']}
    for (r, size), (atlas, x, y) in zip(rows, places):
        guid = bytes.fromhex(r['guid'])
        if guid in known:
            continue
        if atlas not in textures:
            textures[atlas] = (_new_texture(bd, pair[0], HQ_NAME % atlas),
                               _new_texture(bd, pair[1], LQ_NAME % atlas))
        hq, lq = textures[atlas]
        inner = size - 2
        lm = {'type': 2, 'light_guids': [], 'textures': [hq + 1, lq + 1],
              'sky_occlusion': 0, 'ao_mask': 0,
              'scale': [(0.0, 0.0, 0.0, 0.0)] * 4, 'add': [(0.0, 0.0, 0.0, 0.0)] * 4,
              'coord_scale': (inner / width, inner / height),
              'coord_bias': ((x + 1) / width, (y + 1) / height),
              'shadow_valid': (0, 0, 0, 0), 'inv_penumbra': (0.0, 0.0, 0.0, 1.0),
              'virtual_textures': [0, 0]}
        entry = {'lightmap': lm, 'shadowmap': {'type': 0}, 'irrelevant': [],
                 'instance_elem': 16, 'instances': []}
        bd.registry['meshes'].append((guid, entry))
        known.add(guid)
        out.append({'guid': guid, 'export': None, 'mesh': r['mesh'],
                    'matrices': [np.array(r['matrix'], np.float64)], 'occluder': True,
                    'lightmap': lm, 'mesh_index': len(bd.registry['meshes']) - 1,
                    'instances': None, 'package': path, 'two_sided': False,
                    'item': r['item']})
    have = {bytes(g) for g, _d in bd.registry['lights']}
    for light in lights:
        guid = light['guid'] if isinstance(light['guid'], bytes) else bytes.fromhex(light['guid'])
        if guid not in have:
            bd.registry['lights'].append((guid, dict(STATIC_LIGHT_BUILD)))
            have.add(guid)
    return out
