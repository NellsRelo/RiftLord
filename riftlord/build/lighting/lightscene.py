"""What the lightmap baker bakes: a level's static meshes, Static lights and sky light.

    python3 lightscene.py Scenario_09            # summary of the scene the baker sees

Step 3 of the lightmap baker (workshop/research/LIGHTMAP-BAKER.md). Lightmass bakes
only primitives with static lighting, and every one of them has a `MapBuildDataId`
in its level's `_BuiltData` registry. So the scene is read from the registries:

* **Receivers and occluders** are the components whose MapBuildDataId is in one of
  the level's registries (persistent and every sublevel), each with its mesh, world
  matrix, and its FLightMap2D atlas placement. A component that states
  `CastShadow` or `bCastStaticShadow` false receives but does not occlude.
* **Lights** are the light components whose `LightGuid` the registries list in
  `LightBuildData` with no shadow-map channel: Static lights (Stationary lights
  have a channel; Movable ones are not listed). Fields not stated on the placement
  come from the Blueprint's component template, then the engine defaults.
* **Sky light**: the Static `SkyLightComponent` with its cubemap
  (`SLS_SpecifiedCubemap`), read as Lightmass reads it.

Values are converted the way the editor exports them to Lightmass
(Editor/UnrealEd/Private/Lightmass/Lightmass.cpp `Copy`, `WriteLights`): colours
from sRGB FColor to linear, brightness through `ComputeLightBrightness`.

Meshes are read once per mesh (`mesh(name)`) and cached as .npz on the 2 TB scratch
(`LIGHTSCENE_CACHE`, default beside LIGHTMAPS_CACHE): positions, vertex normals,
lightmap UVs and triangles of the first inline LOD.
"""

import argparse
import bisect
import functools
import math
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

#: Mesh cache folder (env), defaulting to a `lightscene` folder beside LIGHTMAPS_CACHE.
CACHE_ENV = 'LIGHTSCENE_CACHE'
#: Bumped when the cached mesh record changes.
MESH_CACHE_VERSION = 1
#: Suffix of a level package's built-data package.
BUILT_DATA = '_BuiltData'
#: Engine defaults of the light properties the baker reads (LightComponent.cpp,
#: LocalLightComponent.cpp, PointLightComponent.cpp, SpotLightComponent.cpp,
#: RectLightComponent.cpp, SkyLightComponent.cpp in UE 4.27).
DEFAULTS = {
    'Intensity': 5000.0, 'AttenuationRadius': 1000.0, 'LightFalloffExponent': 8.0,
    'bUseInverseSquaredFalloff': True, 'SourceRadius': 0.0, 'SourceLength': 0.0,
    'InnerConeAngle': 0.0, 'OuterConeAngle': 44.0, 'SourceWidth': 64.0,
    'SourceHeight': 64.0, 'CastShadows': True, 'CastStaticShadows': True,
    'IndirectLightingIntensity': 1.0, 'IntensityUnits': 'ELightUnits::Unitless',
    'LightColor': (255, 255, 255, 255),
}
#: SkyLightComponent defaults.
SKY_DEFAULTS = {'Intensity': 1.0, 'LightColor': (255, 255, 255, 255),
                'bLowerHemisphereIsBlack': True, 'LowerHemisphereColor': (0.0, 0.0, 0.0, 1.0),
                'SourceCubemapAngle': 0.0, 'IndirectLightingIntensity': 1.0,
                'SourceType': 'SLS_CapturedScene', 'CastShadows': True}
#: The light classes Lightmass bakes from, by our kind.
LIGHT_KINDS = {'PointLightComponent': 'point', 'SpotLightComponent': 'spot',
               'RectLightComponent': 'rect'}
#: Meshes never taken as occluders: the sky dome sits millions of units out and
#: does not cast shadows in the shipped levels.
NON_OCCLUDER_MESHES = ('SM_Skybox_Mesh', 'SM_SkySphere', 'SM_Skybox', 'SM_SkyDome')
#: Mip of the sky cubemap the baker samples: a face this many texels across.
SKY_FACE_SIZE = 32


def _srgb_to_linear(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_colour(fcolor):
    """FLinearColor(FColor): sRGB bytes (r, g, b) to linear, as the engine converts."""
    return [float(v) for v in _srgb_to_linear(np.asarray(fcolor[:3], np.float64))]


# ------------------------------------------------------------------ properties

def _names(info):
    return [n[0] if isinstance(n, (list, tuple)) else n for n in info['names']]


def decode_props(info, uexp, hdr, export):
    """{name: value} of one export's tagged properties, decoded where the baker
    needs it: floats, bools, ints, vectors, FColor (r, g, b, a), LinearColor,
    Guid bytes, enum names; anything else stays raw bytes."""
    from riftlord.onslaught import onslaught
    names = _names(info)
    out = {}
    for name, kind, raw in onslaught.read_props(info, uexp, hdr, export):
        if kind == 'BoolProperty':
            out[name] = bool(raw)
        elif kind == 'FloatProperty' and len(raw) == 4:
            out[name] = struct.unpack('<f', raw)[0]
        elif kind == 'IntProperty' and len(raw) == 4:
            out[name] = struct.unpack('<i', raw)[0]
        elif kind in ('ByteProperty', 'EnumProperty') and len(raw) == 8:
            idx = struct.unpack_from('<i', raw)[0]
            out[name] = names[idx] if 0 <= idx < len(names) else idx
        elif kind == 'ObjectProperty' and len(raw) == 4:
            out[name] = ('object', struct.unpack('<i', raw)[0])
        elif kind == 'StructProperty' and name == 'LightColor' and len(raw) == 4:
            b, g, r, a = struct.unpack('<4B', raw)
            out[name] = (r, g, b, a)
        elif kind == 'StructProperty' and len(raw) == 16 and name in (
                'LowerHemisphereColor',):
            out[name] = struct.unpack('<4f', raw)
        elif kind == 'StructProperty' and len(raw) == 12:
            out[name] = struct.unpack('<3f', raw)
        else:
            out[name] = raw
    return out


@functools.lru_cache(maxsize=512)
def _class_templates(class_name):
    """{component name: props} stated by a Blueprint class and its ancestors, the leaf
    winning per field (object references dropped: they index the class's package)."""
    from riftlord.formats.omd3 import omd3actors
    merged = {}
    for cls in reversed(omd3actors.ancestry(class_name)):
        got = omd3actors._header_of(cls)
        if not got:
            continue
        info, ux, hdr = got
        for e in info['exports']:
            kind = str(e.get('cls') or e.get('class') or '')
            if not kind.endswith('Component'):
                continue
            name = str(e.get('name') or '')
            if name.endswith('_GEN_VARIABLE'):
                name = name[:-len('_GEN_VARIABLE')]
            props = {k: v for k, v in decode_props(info, ux, hdr, e).items()
                     if not (isinstance(v, tuple) and v and v[0] == 'object')}
            merged.setdefault(name, {}).update(props)
    return merged


class Package:
    """One level package: exports, merged component properties and world matrices."""

    def __init__(self, path):
        from riftlord.formats.omd3 import omd3level
        self.path = path
        self.info, self.uexp, self.hdr = omd3level._load(path)
        self.exports = self.info['exports']
        self._props = {}
        self._world = {}
        spans = sorted((e['off'] - self.hdr, i) for i, e in enumerate(self.exports))
        self._starts = [s for s, _i in spans]
        self._order = [i for _s, i in spans]

    def own_props(self, i):
        if i not in self._props:
            self._props[i] = decode_props(self.info, self.uexp, self.hdr, self.exports[i])
        return self._props[i]

    def template(self, i):
        """The class template props of component export `i`, when a Blueprint owns it."""
        outer = self.exports[i].get('outer') or 0
        if not 0 < outer <= len(self.exports):
            return {}
        owner = str(self.exports[outer - 1].get('cls') or '')
        if not owner.endswith('_C'):
            return {}
        name = str(self.exports[i].get('name') or '')
        if name.endswith('_GEN_VARIABLE'):
            name = name[:-len('_GEN_VARIABLE')]
        return _class_templates(owner).get(name) or {}

    def props(self, i):
        """Template props overlaid with the placement's own."""
        merged = dict(self.template(i))
        merged.update(self.own_props(i))
        return merged

    def world(self, i, seen=None):
        """Column-vector 4x4 world matrix of component export `i` (AttachParent chain)."""
        from riftlord.formats.omd3 import omd3level
        if i in self._world:
            return self._world[i]
        seen = seen or set()
        p = self.props(i)
        m = np.array(omd3level._matrix(p.get('RelativeLocation', (0.0, 0.0, 0.0)),
                                       p.get('RelativeRotation', (0.0, 0.0, 0.0)),
                                       p.get('RelativeScale3D', (1.0, 1.0, 1.0))))
        if i not in seen:
            seen.add(i)
            ref = self.own_props(i).get('AttachParent')
            if isinstance(ref, tuple) and ref[0] == 'object' and 0 < ref[1] <= len(self.exports):
                m = self.world(ref[1] - 1, seen) @ m
        self._world[i] = m
        return m

    def export_at(self, offset):
        """Index of the export whose serial data holds uexp byte `offset`."""
        k = bisect.bisect_right(self._starts, offset) - 1
        return self._order[k] if k >= 0 else None


# ------------------------------------------------------------------- the level

def packages(level):
    """The level's packages Lightmass baked: persistent first, then sublevels."""
    from riftlord.formats.omd3 import omd3level
    return omd3level.sublevels(level)


def tag_of(level, package_path):
    leaf = package_path.rsplit('/', 1)[-1]
    return leaf[len(level):].lstrip('_') or 'persistent' if leaf.startswith(level) else leaf


def registry(package_path):
    """The package's decoded BuiltData (lightmaps.BuiltData), or None."""
    from riftlord.build.lighting import lightmaps
    try:
        blobs = lightmaps.fetch(package_path + BUILT_DATA)
    except Exception:                                # noqa: BLE001
        return None
    if not blobs[0]:
        return None
    return lightmaps.BuiltData(*blobs)


def components(level, package_path, bd=None, pkg=None):
    """[component] of one package that have a light map entry in its registry.

    A component: {'guid', 'export', 'mesh', 'matrices' [4x4 per instance],
    'occluder', 'lightmap' (registry FLightMap2D), 'instances' (per-instance UV
    biases or None), 'mesh_index' (index in the registry's mesh list)}.
    """
    from riftlord.formats.omd3 import omd3level
    bd = bd or registry(package_path)
    if bd is None:
        return []
    pkg = pkg or Package(package_path)
    pieces, _lights, _vfx, _act = omd3level.read_package(package_path)
    by_component = {}
    for pc in pieces:
        c = pc.get('component') or (pc['export'] if pc.get('source') in
                                    ('component', 'spline', 'skeletal') else None)
        if c:
            by_component.setdefault(c, []).append(pc)
    out = []
    for n, (guid, entry) in enumerate(bd.registry['meshes']):
        at = pkg.uexp.find(bytes(guid))
        if at < 0:
            continue
        ex = pkg.export_at(at) + 1
        got = by_component.get(ex)
        if not got:
            continue
        props = pkg.props(ex - 1)
        occluder = (props.get('CastShadow', True) and props.get('bCastStaticShadow', True)
                    and got[0]['mesh'] not in NON_OCCLUDER_MESHES and not got[0].get('hidden'))
        mats = [np.array(pc['matrix'], np.float64).reshape(4, 4).T for pc in got]
        out.append({'guid': bytes(guid), 'export': ex, 'mesh': got[0]['mesh'],
                    'matrices': mats, 'occluder': bool(occluder),
                    'lightmap': entry['lightmap'], 'mesh_index': n,
                    'instances': entry.get('instances') or None,
                    'package': package_path,
                    # foliage materials are two-sided: back-face hits on them are not
                    # "inside geometry" (Lightmass IsTwoSided)
                    'two_sided': bool(got[0].get('foliage')) or
                    tag_of(level, package_path) == FOLIAGE_TAG})
    return out


#: Tag of the foliage sublevel, whose meshes count as two-sided.
FOLIAGE_TAG = 'Foliage'
#: Meshes that are lava on the shipped levels (North Wing's river, falls and pillow).
LAVA_MESHES = ('Scenario_01_Lava', 'LavaFall_01', 'LavaFall_02', 'SM_LSC_LavaPillow_C')


def lava_piece_ids(level, plan=None):
    """leveldata piece ids of the level's open lava surfaces: shipped lava meshes
    (LAVA_MESHES, or liquid pieces of kind lava) and the planes the plan turns into lava
    (riftlord.lavafx._lava_planes), minus pieces the plan removes."""
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import leveldata
    removed = set((plan or {}).get('removed') or [])
    out = set()
    for p in leveldata.level(level)['pieces']:
        if p.get('hidden') or p['id'] in removed:
            continue
        if p['mesh'] in LAVA_MESHES or (p.get('liquid') and (
                omd3level.liquid_kind(p.get('material') or '') == 'lava'
                or omd3level.liquid_kind(p['mesh']) == 'lava')):
            out.add(p['id'])
    if plan:
        from riftlord.build.world import lavafx
        for piece, _box in lavafx._lava_planes(level, plan):
            if piece['id'] not in removed:
                out.add(piece['id'])
    return sorted(out)


def mark_emitters(level, comps_by_path, piece_ids, radiance):
    """Give the components holding these leveldata pieces an emissive `radiance`
    ([r, g, b] linear, per uu^2 sr as Lightmass gathers it). An emitter always takes part
    in ray tracing. Pieces without a light-mapped component are added (key EMITTERS) as
    emit-and-occlude geometry. Returns the number of instances marked."""
    from riftlord.mapeditor import leveldata
    want = set(piece_ids)
    pieces = [p for p in leveldata.level(level)['pieces'] if p['id'] in want]
    found = set()
    for path, comps in comps_by_path.items():
        if path in (ADDED, EMITTERS):
            continue
        tag = tag_of(level, path)
        for c in comps:
            for p in pieces:
                comp = p.get('component') or p['export']
                if p.get('sub', 'persistent') != tag or comp != c['export']:
                    continue
                c['emitter'] = [float(v) for v in radiance]
                c['occluder'] = True
                found.add(p['id'])
    extra = []
    for p in pieces:
        if p['id'] in found:
            continue
        extra.append({'guid': None, 'export': None, 'mesh': p['mesh'],
                      'matrices': [np.array(p['matrix'], np.float64).reshape(4, 4).T],
                      'occluder': True, 'lightmap': None, 'mesh_index': None,
                      'instances': None, 'package': EMITTERS,
                      'emitter': [float(v) for v in radiance]})
    comps_by_path[EMITTERS] = extra
    return len(found) + len(extra)


#: Key of emitter geometry that has no light-mapped component.
EMITTERS = 'emitters'


#: Decimals of a placement's translation used to find it among the level's pieces.
PIECE_KEY_DECIMALS = 1
#: Package tag of components a plan adds (occluders only: they have no light map).
ADDED = 'added'
#: Kinds of added piece a bake lays in as occluders (apply_plan): shipped meshes by name.
#: Added meshes and kit pieces that get light maps are laid in as receivers instead
#: (lightalloc); Fab pieces are not read by the baker.
OCCLUDING_KINDS = ('mesh',)


def _piece_key(tag, component, translation):
    return (tag, component, tuple(round(float(v), PIECE_KEY_DECIMALS) for v in translation))


def apply_plan(level, comps_by_path, plan, received=()):
    """Lay a Rift Lord plan over the components: removed pieces are dropped, moved ones
    take their new matrix, added meshes become occluders (key ADDED), except the items
    in `received`, which lightalloc lays in as receivers. Pieces are found by sublevel,
    component export and translation among riftlord.leveldata's pieces, whose ids the
    plan uses. Returns {'removed', 'moved', 'added', 'unmatched'} counts."""
    from riftlord.mapeditor import leveldata
    removed = set(plan.get('removed') or [])
    moved = {e['id']: e['matrix'] for e in (plan.get('edits') or []) if 'id' in e}
    index = {}
    for p in leveldata.level(level)['pieces']:
        comp = p.get('component') or (p['export'] if p.get('source') in
                                      ('component', 'spline', 'skeletal') else None)
        index[_piece_key(p.get('sub') or 'persistent', comp, p['matrix'][12:15])] = p['id']
    counts = {'removed': 0, 'moved': 0, 'added': 0, 'unmatched': 0}
    for path, comps in comps_by_path.items():
        tag = tag_of(level, path)
        for c in comps:
            keep, mats, inst = [], [], []
            for k, m in enumerate(c['matrices']):
                pid = index.get(_piece_key(tag, c['export'], m[:3, 3]))
                if pid is None:
                    counts['unmatched'] += 1
                if pid in removed:
                    counts['removed'] += 1
                    continue
                if pid in moved:
                    m = np.array(moved[pid], np.float64).reshape(4, 4).T
                    counts['moved'] += 1
                mats.append(m)
                keep.append(k)
            if c['instances']:
                c['instances'] = [c['instances'][k] for k in keep if k < len(c['instances'])]
            c['matrices'] = mats
            c['removed'] = not mats
    added = []
    received = set(received)
    for n, a in enumerate(plan.get('added') or []):
        if a.get('kind', 'mesh') not in OCCLUDING_KINDS or not a.get('name'):
            continue
        counts['added'] += 1
        if n in received:
            continue
        added.append({'guid': None, 'export': None, 'mesh': a['name'],
                      'matrices': [np.array(a['matrix'], np.float64).reshape(4, 4).T],
                      'occluder': True, 'lightmap': None, 'mesh_index': None,
                      'instances': None, 'package': ADDED, 'item': n})
    comps_by_path[ADDED] = added
    return counts


def drop_host(comps_by_path):
    """Take every component of the level's own out of the scene, as a level that streams
    none of its host's geometry and parks the rest has them (riftlord.mapeditor.scratch).
    Returns the count taken out."""
    gone = 0
    for path, comps in comps_by_path.items():
        if path in (ADDED, EMITTERS):
            continue
        for c in comps:
            if c['matrices']:
                gone += 1
            c['matrices'], c['removed'] = [], True
            if c['instances']:
                c['instances'] = []
    return gone


def static_light_guids(bd):
    """GUIDs the registry lists as Static lights (LightBuildData, no shadow channel)."""
    out = set()
    for guid, data in bd.registry.get('lights') or []:
        if data.get('channel', data.get('shadow_map_channel', -1)) == -1:
            out.add(bytes(guid))
    return out


def _axis(m, k):
    v = np.asarray(m[:3, k], np.float64)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _brightness(kind, p):
    """ComputeLightBrightness for a point, spot or rect light."""
    b = float(p['Intensity'])
    units = p['IntensityUnits']
    if kind == 'rect':
        if units.endswith('Candelas'):
            return b * 100.0 * 100.0
        if units.endswith('Lumens'):
            return b * 100.0 * 100.0 / math.pi
        return b * 16.0
    if not p['bUseInverseSquaredFalloff']:
        return b
    if units.endswith('Candelas'):
        return b * 100.0 * 100.0
    if units.endswith('Lumens'):
        if kind == 'spot':
            cos_half = math.cos(math.radians(max(min(p['OuterConeAngle'], 89.0), 0.0)))
            return b * 100.0 * 100.0 / 2.0 / math.pi / (1.0 - cos_half)
        return b * 100.0 * 100.0 / 4.0 / math.pi
    return b * 16.0


def light_record(kind, guid, p, m, package, export):
    """One light (lights()) from its merged properties `p` and world matrix `m`."""
    rec = {'kind': kind, 'guid': guid, 'package': package, 'export': export,
           'position': [float(v) for v in m[:3, 3]],
           'direction': [float(v) for v in _axis(m, 0)],
           'tangent': [float(v) for v in _axis(m, 2)],
           'colour': linear_colour(p['LightColor']),
           'brightness': _brightness(kind, p),
           'radius': float(p['AttenuationRadius']),
           'falloff': float(p['LightFalloffExponent']),
           'inverse_squared': bool(p['bUseInverseSquaredFalloff']) or kind == 'rect',
           'cast_shadows': bool(p['CastShadows']) and bool(p['CastStaticShadows']),
           'indirect': float(p['IndirectLightingIntensity'])}
    if kind == 'rect':
        rec['source_radius'] = 0.5 * float(p['SourceWidth'])
        rec['source_length'] = 0.5 * float(p['SourceHeight'])
    else:
        rec['source_radius'] = max(1.0, float(p['SourceRadius']))
        rec['source_length'] = float(p['SourceLength'])
    if kind == 'spot':
        outer = min(max(float(p['OuterConeAngle']), 0.0), 89.0)
        rec['outer'] = outer
        rec['inner'] = min(max(float(p['InnerConeAngle']), 0.0), outer)
    return rec


def edit_props(p, edits, defaults):
    """Put a plan's light values (riftlord.build.world.lighting: '#rrggbb' colours, lists,
    None for the class default) into merged props `p`, in this module's form: FColor
    (r, g, b, a), tuples; positions left out. Returns `p`."""
    for prop, value in (edits or {}).items():
        if prop.startswith('Relative'):
            continue
        if value is None:
            if prop in defaults:
                p[prop] = defaults[prop]
            else:
                p.pop(prop, None)
            continue
        p.update(scene_props({prop: value}))
    return p


def scene_props(edits):
    """A plan's light values (riftlord.build.world.lighting: '#rrggbb' colours, lists)
    as this module's props: FColor (r, g, b, a), tuples; positions and None left out."""
    out = {}
    for prop, value in (edits or {}).items():
        if value is None or prop.startswith('Relative'):
            continue
        if isinstance(value, str) and value.startswith('#'):
            rgb = [int(value[k:k + 2], 16) for k in (1, 3, 5)]
            out[prop] = tuple(rgb) + ((int(value[7:9], 16),) if len(value) == 9 else (255,))
        elif isinstance(value, list):
            out[prop] = tuple(float(np.float32(v)) for v in value)
        elif isinstance(value, float):
            #: as the package stores it, so a value read back from one bakes the same
            out[prop] = float(np.float32(value))
        else:
            out[prop] = value
    return out


def lights(level, static_guids, packages_=None, edits=None, moves=None, removed=()):
    """[light] for every Static point, spot and rect light of the level, with a plan's
    `edits` ({actor key: light values}, riftlord.build.world.lighting), `moves` ({actor
    key: [dx, dy, dz]}) and `removed` actor keys; a light the plan makes Movable is left
    out.

    A light: {'kind', 'guid', 'position', 'direction' (X axis), 'tangent' (Z axis),
    'colour' (linear rgb), 'brightness', 'radius', 'falloff', 'inverse_squared',
    'source_radius', 'source_length', 'inner', 'outer' (cone half angles, degrees),
    'cast_shadows', 'indirect', 'package', 'export'}."""
    out = []
    gone = set(removed or ())
    for path in packages_ or packages(level):
        pkg = Package(path)
        tag = tag_of(level, path)
        for i, e in enumerate(pkg.exports):
            kind = LIGHT_KINDS.get(str(e.get('cls') or ''))
            if not kind:
                continue
            own = pkg.own_props(i)
            guid = own.get('LightGuid')
            if not isinstance(guid, bytes) or guid not in static_guids:
                continue
            key = '%s|%d' % (tag, int(e.get('outer') or 0))
            if key in gone:
                continue
            p = dict(DEFAULTS)
            p.update(pkg.props(i))
            edit_props(p, (edits or {}).get(key), DEFAULTS)
            if p.get('Mobility', 'EComponentMobility::Static') != 'EComponentMobility::Static':
                continue
            m = pkg.world(i)
            shift = (moves or {}).get(key)
            if shift:
                m = m.copy()
                m[:3, 3] += np.asarray(shift, np.float64)
            out.append(light_record(kind, guid, p, m, path, i + 1))
    return out


# ------------------------------------------------------------------- sky light

def sky_light(level, packages_=None, edits=None):
    """The level's Static sky light with a plan's sky light values (`edits`,
    riftlord.build.world.lighting): {'colour' (linear), 'brightness', 'indirect',
    'lower_black', 'lower_colour', 'cubemap' (/Game path or None), 'angle',
    'cast_shadows'}, or None."""
    for path in packages_ or packages(level):
        pkg = Package(path)
        for i, e in enumerate(pkg.exports):
            if str(e.get('cls') or '') != 'SkyLightComponent':
                continue
            p = dict(SKY_DEFAULTS)
            p.update(pkg.props(i))
            edit_props(p, edits, SKY_DEFAULTS)
            if p.get('Mobility', 'EComponentMobility::Static') != 'EComponentMobility::Static':
                continue
            cube = None
            ref = pkg.own_props(i).get('Cubemap')
            if isinstance(ref, tuple) and ref[1] < 0:
                imports = pkg.info['imports']
                imp = imports[-ref[1] - 1]
                outer = imp.get('outer', 0)
                if outer < 0:
                    cube = imports[-outer - 1]['name']
            return {'colour': linear_colour(p['LightColor']),
                    'brightness': float(p['Intensity']),
                    'indirect': float(p['IndirectLightingIntensity']),
                    'lower_black': bool(p['bLowerHemisphereIsBlack']),
                    'lower_colour': [float(v) for v in p['LowerHemisphereColor'][:3]],
                    'cubemap': cube if str(p['SourceType']).endswith('SpecifiedCubemap') else None,
                    'source_type': str(p['SourceType']),
                    'angle': float(p['SourceCubemapAngle']),
                    'cast_shadows': bool(p['CastShadows'])}
    return None


def _half_floats(raw):
    return np.frombuffer(raw, '<f2').astype(np.float32)


def cubemap_faces(path, face=SKY_FACE_SIZE):
    """(6, face, face, 3) float32 linear radiance of a cooked PF_FloatRGBA TextureCube,
    faces in UE order (+X, -X, +Y, -Y, +Z, -Z), the mip `face` texels across."""
    from riftlord.build.lighting import lightmaps
    from riftlord.formats.ue.uassetio import Package as UPackage
    ua, ux, ub = lightmaps.fetch(path)
    pkg = UPackage(ua, ux)
    names = [n for n, _h in pkg.names]
    data = pkg.exports[0]['data']
    r = lightmaps.Reader(data, lightmaps.tags_end(data, names))
    if r.u32():
        r.guid()
    r.raw(4)                                         # UTexture, UTextureCube strip flags
    r.u32()                                          # bCooked
    fmt = names[r.take('<2i')[0]]
    if fmt != 'PF_FloatRGBA':
        raise ValueError('%s is %s; only PF_FloatRGBA cubemaps are read' % (path, fmt))
    r.i64()                                          # SkipOffset
    size = r.take('<2i')
    packed = r.u32()
    r.fstring_raw()
    if packed & (1 << 30):                           # HasOptData
        r.raw(8)
    r.i32()                                          # FirstMipToSerialize
    for k in range(r.i32()):
        r.u32()
        flags = r.u32()
        count, disk = r.take('<2i')
        offset = r.i64()
        if flags & lightmaps.BULK_SEPARATE_FILE:
            payload = ub[offset:offset + disk]
        else:
            payload = r.raw(disk)
        dims = r.take('<3i')
        if dims[0] == face:
            px = _half_floats(payload).reshape(6, face, face, 4)
            return px[..., :3].copy()
    raise ValueError('%s has no %d-texel mip (top %r)' % (path, face, size))


# ------------------------------------------------------------ the sky texture

#: Size the sky material's texture is read at (longest side) for the sky dome.
SKY_TEXTURE_SIZE = 1024
#: Material parameters of Master_Skybox_Main read for the sky's colour: the texture is
#: desaturated by `Desaturation` (0 none, 1 grey) and tinted by `Color`.
SKY_DESATURATION = 'Desaturation'
SKY_TINT = 'Color'
#: Apply `Color` as a multiplier. Off since 2026-10-16: on Stormy_Sky_Scen15_INST it is
#: amber (0.70, 0.29, 0.05) and turned every sky-lit surface sandy brown (Beset Bake B
#: turrets); the master graph is not in the cooked data, so its role is unknown, and the
#: desaturated texture alone gives the grey the sky reads as in game.
APPLY_SKY_TINT = False


def _material_parameters(material):
    """{name: float or (r, g, b, a)} scalar and vector parameters a material instance
    sets."""
    from riftlord.core import gamefiles
    from riftlord.formats.omd3 import omd3actors
    from riftlord.core import uetags
    from riftlord.formats.ue.uassetio import Package as UPackage
    path = omd3actors._asset_for(material)
    if not path:
        return {}
    pkg = UPackage(*gamefiles.package(path.replace('../../../OMD/Content/', '/Game/')))
    names = [n for n, _h in pkg.names]
    props, _end = uetags.read(pkg.exports[0]['data'], names)
    out = {}
    for p in props:
        if p.name not in ('ScalarParameterValues', 'VectorParameterValues'):
            continue
        for item in p.value[2]:
            d = {q.name: q.value for q in item}
            name = [q.value for q in d['ParameterInfo'] if q.name == 'Name'][0]
            v = d['ParameterValue']
            out[name] = struct.unpack('<4f', v) if isinstance(v, bytes) else v
    return out


def _cube_directions(face):
    """(6, face, face, 3) unit directions of each cube texel centre, faces and UVs as
    Lightmass's GetCubeFaceAndUVFromDirection (and lightbaker's sky lookup) read them."""
    c = (np.arange(face) + 0.5) / face * 2.0 - 1.0
    v, u = np.meshgrid(c, c, indexing='ij')
    one = np.ones_like(u)
    dirs = np.stack([np.stack([one, -v, -u], -1), np.stack([-one, -v, u], -1),
                     np.stack([u, one, v], -1), np.stack([u, -one, -v], -1),
                     np.stack([u, -v, one], -1), np.stack([-u, -v, -one], -1)])
    return dirs / np.linalg.norm(dirs, axis=-1, keepdims=True)


def sky_texture_cube(material, level, face=SKY_FACE_SIZE):
    """(6, face, face, 3) linear radiance pattern of a sky material's own texture, as the
    level's skybox mesh shows it: a ray from the skybox centre along each cube direction
    finds the skybox triangle it meets, whose UV0 (the material samples UV channel 0)
    reads the texture; then the material's Desaturation and Color are applied. Unscaled:
    the caller sets its brightness. None when the texture or skybox cannot be read."""
    import io
    from riftlord.formats.omd3 import meshcat
    from riftlord.mapeditor import omd3serve
    from riftlord.formats.omd3 import omd3smwrite
    from PIL import Image
    from riftlord.mapeditor import leveldata
    tex = omd3serve.material_texture(material)
    png = omd3serve.texture_png(tex, SKY_TEXTURE_SIZE) if tex else None
    sky = leveldata.level(level).get('sky') or {}
    if not png or not sky.get('mesh') or not sky.get('matrix'):
        return None
    img = np.asarray(Image.open(io.BytesIO(png)).convert('RGB'), np.float64)
    img = _srgb_to_linear(img)
    info = meshcat.load().get(sky['mesh'])
    d = omd3smwrite.decode(omd3smwrite.Template(info['path']))
    m = np.array(sky['matrix'], np.float64).reshape(4, 4).T
    P = d['positions'].astype(np.float64) @ m[:3, :3].T + m[:3, 3]
    T = d['triangles']
    uv = d['uvs'][0].astype(np.float64)
    centre = m[:3, 3]
    v0 = P[T[:, 0]] - centre
    e1, e2 = P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]
    dirs = _cube_directions(face).reshape(-1, 3)
    out = np.zeros((len(dirs), 3))
    for k, dd in enumerate(dirs):
        pv = np.cross(dd, e2)
        det = (e1 * pv).sum(1)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s_ = -v0
        a = (s_ * pv).sum(1) * inv
        q = np.cross(s_, e1)
        b = (q @ dd) * inv
        t = (e2 * q).sum(1) * inv
        hit = ok & (a >= 0) & (b >= 0) & (a + b <= 1) & (t > 0)
        if not hit.any():
            continue
        j = np.argmin(np.where(hit, t, np.inf))
        tuv = uv[T[j, 0]] * (1 - a[j] - b[j]) + uv[T[j, 1]] * a[j] + uv[T[j, 2]] * b[j]
        x = int(np.clip(tuv[0] % 1.0 * img.shape[1], 0, img.shape[1] - 1))
        y = int(np.clip(tuv[1] % 1.0 * img.shape[0], 0, img.shape[0] - 1))
        out[k] = img[y, x]
    params = _material_parameters(material)
    lum = out @ np.array([0.3, 0.59, 0.11])
    desat = float(params.get(SKY_DESATURATION, 0.0))
    out = out * (1.0 - desat) + lum[:, None] * desat
    tint = params.get(SKY_TINT) if APPLY_SKY_TINT else None
    if isinstance(tint, tuple):
        out = out * np.asarray(tint[:3])
    return out.reshape(6, face, face, 3)


def upper_mean_luminance(cube):
    """Mean luminance of a (6, f, f, 3) cube over the upper hemisphere (texel weighted)."""
    dirs = _cube_directions(cube.shape[1])
    up = dirs[..., 2] > 0
    return float((cube[up] @ np.array([0.3, 0.59, 0.11])).mean())


# --------------------------------------------------------------------- meshes

def _cache_dir():
    root = os.environ.get(CACHE_ENV)
    if not root:
        from riftlord.core import gamepath
        base = os.environ.get('LIGHTMAPS_CACHE') or gamepath.scratch('lightmaps', beside=gamepath.CODE_ROOT)
        root = os.path.join(os.path.dirname(base.rstrip('/')), 'lightscene')
    os.makedirs(root, exist_ok=True)
    return root


def mesh(name):
    """{'positions' (n,3) f32, 'normals' (n,3) f32, 'uv' (n,2) f32 lightmap UVs,
    'triangles' (m,3) i32, 'lod' (index of the inline LOD read), 'uv_index'} or None."""
    path = os.path.join(_cache_dir(), '%s.v%d.npz' % (name, MESH_CACHE_VERSION))
    if os.path.exists(path):
        with np.load(path) as z:
            return {k: z[k] for k in z.files}
    from riftlord.formats.omd3 import meshcat
    from riftlord.formats.omd3 import omd3smwrite
    from riftlord.core import uetags
    info = meshcat.load().get(name)
    if not info:
        return None
    try:
        t = omd3smwrite.Template(info['path'])
        d = omd3smwrite.decode(t)
    except (Exception, SystemExit):                  # noqa: BLE001
        return None
    p = uetags.get(t.props, 'LightMapCoordinateIndex')
    uv_index = int(p.value) if p is not None else 0
    uv_index = min(uv_index, len(d['uvs']) - 1)
    lod = next(k for k, lod in enumerate(t.model['lods']) if 'buffers' in lod)
    rec = {'positions': d['positions'].astype(np.float32),
           'normals': d['normals'].astype(np.float32),
           'uv': d['uvs'][uv_index].astype(np.float32),
           'triangles': d['triangles'].astype(np.int32),
           'lod': np.int32(lod), 'uv_index': np.int32(uv_index)}
    np.savez(path + '.part.npz', **rec)
    os.replace(path + '.part.npz', path)
    return rec


# ------------------------------------------------------------------------ CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('level')
    a = ap.parse_args(argv)
    guids = set()
    total = 0
    for path in packages(a.level):
        bd = registry(path)
        if bd is None:
            continue
        guids |= static_light_guids(bd)
        comps = components(a.level, path, bd)
        total += len(comps)
        print('%-14s %5d components, %d occluders, %d meshes' % (
            tag_of(a.level, path), len(comps), sum(c['occluder'] for c in comps),
            len({c['mesh'] for c in comps})))
    ls = lights(a.level, guids)
    print('%d static lights of %d listed: %s' % (len(ls), len(guids), {
        k: sum(1 for l in ls if l['kind'] == k) for k in ('point', 'spot', 'rect')}))
    print('sky:', sky_light(a.level))


if __name__ == '__main__':
    main()
