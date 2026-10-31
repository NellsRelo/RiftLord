# Generated from OMD2-Converter/thirdparty/fabconvert/fbxpack.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Loose model packs: binary FBX files plus texture images (no Unreal packages), as many Fab and
other store packs ship. Each FBX mesh object becomes one source mesh in the same shape
meshconvert.read gives (Unreal axes and centimetres), and each texture set (BaseColor / Normal /
OcclusionRoughnessMetallic images of one name) becomes a material the converter bakes like any
other: matconvert.analyse and bake hand '/FabFbx/<set>' paths here.

Mesh paths are '<fbx file>::<object name>'; material paths '/FabFbx/<set name>'.
"""

import os
import re

import numpy as np

from riftlord.fab import fbxread as F

SEP = '::'
MAT_ROOT = '/FabFbx/'
IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.tga', '.bmp')
#: Texture roles by file-name word (lower case, after the set name or before it).
ROLE_WORDS = {
    'BC': ('basecolor', 'basecolour', 'base_color', 'base', 'albedo', 'diffuse', 'color', 'colour', 'basecolor_texture'),
    'NM': ('normal', 'normalgl', 'normaldx', 'nrm', 'nor'),
    'ORM': ('occlusionroughnessmetallic', 'orm', 'aorm', 'arm'),
    'R': ('roughness', 'rough', 'rogh'),
    'AO': ('ambientocclusion', 'occlusion', 'ao'),
    'M': ('metallic', 'metalness', 'metal'),
}
#: Names of images that are no material channel (colour-variation masks, decals) stay unused.
IGNORE_WORDS = ('decal', 'decall', 'varieation', 'variation', 'hdri_')
#: A normal map is read as OpenGL (green up, Blender's and most DCC exports) unless its name
#: says DirectX; Unreal and OMD3 read DirectX (green down), so OpenGL maps get green flipped.
DIRECTX_WORDS = ('dx', 'directx', 'normaldx')
#: An image counts as cut out (masked) when this share of its texels has alpha under CLIP.
MASK_SHARE = 0.02
CLIP = 0.5
#: Texture size: the image's own, limited like the Unreal-pack bakes (matconvert SIZE_*).
SIZE_DEFAULT, SIZE_LARGE, SIZE_MIN = 1024, 2048, 64
#: Collision objects in Unreal's FBX convention, named for the mesh they belong to.
COLLISION_PREFIXES = ('UCX_', 'UBX_', 'USP_', 'UCP_')


# -- the scene -----------------------------------------------------------------------------------------

def _euler(rx, ry, rz):
    """FBX Lcl Rotation (degrees, XYZ order: R = Rz Ry Rx) as a 3x3 matrix."""
    a, b, c = np.radians([rx, ry, rz])
    X = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    Y = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    Z = np.array([[np.cos(c), -np.sin(c), 0], [np.sin(c), np.cos(c), 0], [0, 0, 1]])
    return Z @ Y @ X


def _axis_matrix(gs):
    """3x3 taking the file's coordinates to Unreal's (x forward, y right, z up, left-handed,
    centimetres): the file's axes to a right-handed Z-up frame, then y mirrored, as Unreal's FBX
    importer does."""
    def ax(key, default):
        return int(gs.get(key, [default])[0]), int(gs.get(key + 'Sign', [1])[0])
    up, ups = ax('UpAxis', 1)
    front, fronts = ax('FrontAxis', 2)
    coord, coords = ax('CoordAxis', 0)
    unit = float(gs.get('UnitScaleFactor', [1.0])[0])      # centimetres per file unit
    # rows: where each target axis (x, y, z right-handed Z-up) reads from in the file
    M = np.zeros((3, 3))
    M[0, coord] = coords                                   # x = the file's coord (right) axis
    M[1, front] = -fronts                                  # y = the file's front axis, negated (front = -y)
    M[2, up] = ups                                         # z = the file's up axis
    mirror = np.diag([1.0, -1.0, 1.0])                     # right-handed -> Unreal's left-handed
    return mirror @ M * unit


class Scene:
    """One FBX file: its mesh objects (name -> geometry, rotation/scale, materials) and the
    connections between textures and materials."""

    def __init__(self, path):
        self.path = path
        root = F.read(path)
        self.axes = _axis_matrix(F.properties70(root.find('GlobalSettings')))
        objs = root.find('Objects')
        byid = {o.props[0]: o for o in objs.children}
        self.names = {o.props[0]: (o.props[1].split('\x00')[0] if len(o.props) > 1 else '') for o in objs.children}
        children = {}
        parents = {}
        tex_of_mat = {}
        for c in root.find('Connections').children:
            kind, a, b = c.props[0], c.props[1], c.props[2]
            children.setdefault(b, []).append(a)
            if kind == 'OO':
                parents[a] = b
            if kind == 'OP' and a in byid and b in byid and byid[a].name == 'Texture' and byid[b].name == 'Material':
                tex_of_mat.setdefault(b, []).append((c.props[3] if len(c.props) > 3 else '', a))
        self.models = {}
        for oid, o in byid.items():
            if o.name != 'Model' or (len(o.props) > 2 and o.props[2] != 'Mesh'):
                continue
            geo = next((byid[c] for c in children.get(oid, []) if c in byid and byid[c].name == 'Geometry'), None)
            mats = [c for c in children.get(oid, []) if c in byid and byid[c].name == 'Material']
            if geo is None:
                continue
            self.models[self.names[oid]] = {'id': oid, 'geometry': geo, 'materials': mats,
                                            'transform': self._world_linear(oid, byid, parents)}
        self.materials = {}
        for mid in {m for v in self.models.values() for m in v['materials']}:
            files = []
            for prop, tid in tex_of_mat.get(mid, []):
                t = byid[tid]
                fn = t.find('RelativeFilename') or t.find('FileName')
                if fn is not None and fn.props:
                    files.append((prop, fn.props[0]))
            self.materials[mid] = {'name': self.names[mid], 'textures': files}

    def _world_linear(self, oid, byid, parents):
        """Rotation and scale of a model with its parents (translation dropped: the piece's own
        origin is its pivot)."""
        M = np.eye(3)
        cur = oid
        for _ in range(64):
            o = byid.get(cur)
            if o is None or o.name != 'Model':
                break
            p = F.properties70(o)
            pre = _euler(*p.get('PreRotation', [0, 0, 0])[:3])
            rot = _euler(*p.get('Lcl Rotation', [0, 0, 0])[:3])
            post = _euler(*p.get('PostRotation', [0, 0, 0])[:3])
            sca = np.diag(p.get('Lcl Scaling', [1, 1, 1])[:3])
            M = pre @ rot @ np.linalg.inv(post) @ sca @ M
            cur = parents.get(cur)
        return M


_SCENES = {}


def scene(path):
    if path not in _SCENES:
        _SCENES[path] = Scene(path)
    return _SCENES[path]


def list_meshes(roots):
    """'<fbx>::<object>' for every mesh object of every FBX file under the roots (collision
    objects left out: they belong to the mesh they name)."""
    out = []
    for root in roots:
        for dp, _dn, fns in os.walk(root):
            for f in sorted(fns):
                if f.lower().endswith('.fbx'):
                    p = os.path.join(dp, f)
                    try:
                        sc = scene(p)
                    except (ValueError, KeyError, IndexError, AttributeError):
                        continue
                    for name in sorted(sc.models):
                        if not name.startswith(COLLISION_PREFIXES):
                            out.append(p + SEP + name)
    return sorted(out)


def is_mesh_path(path):
    return SEP in path and path.split(SEP, 1)[0].lower().endswith('.fbx')


def source_rel(path, content):
    """The catalogue `source` of a mesh path: '<fbx relative path without .fbx>/<object>'."""
    fbx, name = path.split(SEP, 1)
    return os.path.splitext(os.path.relpath(fbx, content))[0] + '/' + name


# -- geometry ------------------------------------------------------------------------------------------

def _layer(geo, kind, data_name, index_name, n_corners, poly_of_corner, corner_vertex):
    """Per corner values of a LayerElement (Normal, UV, ...), or None."""
    el = geo.find(kind)
    if el is None:
        return None
    data = el.find(data_name)
    if data is None:
        return None
    width = 2 if kind == 'LayerElementUV' else 3
    vals = np.asarray(data.props[0], np.float64).reshape(-1, width)
    mapping = (el.find('MappingInformationType').props[0] if el.find('MappingInformationType') else 'ByPolygonVertex')
    ref = (el.find('ReferenceInformationType').props[0] if el.find('ReferenceInformationType') else 'Direct')
    idx = el.find(index_name)
    if mapping in ('ByPolygonVertex',):
        k = np.arange(n_corners)
    elif mapping in ('ByVertice', 'ByVertex'):
        k = corner_vertex
    elif mapping == 'ByPolygon':
        k = poly_of_corner
    elif mapping == 'AllSame':
        k = np.zeros(n_corners, np.int64)
    else:
        return None
    if ref in ('IndexToDirect', 'Index') and idx is not None:
        k = np.asarray(idx.props[0], np.int64)[k]
    return vals[np.clip(k, 0, len(vals) - 1)]


def mesh_src(path, set_of_material=None):
    """meshconvert.read's shape for one FBX mesh object ('<fbx>::<object>'). Material slots are
    the object's materials in connection order; each names a texture set ('/FabFbx/<set>')
    when set_of_material gives one, else None (the converter's neutral grey)."""
    fbx, name = path.split(SEP, 1)
    sc = scene(fbx)
    m = sc.models[name]
    geo = m['geometry']
    V = np.asarray(geo.find('Vertices').props[0], np.float64).reshape(-1, 3)
    pvi = np.asarray(geo.find('PolygonVertexIndex').props[0], np.int64)
    end = pvi < 0
    corner_vertex = np.where(end, ~pvi, pvi)
    n = len(pvi)
    poly_of_corner = np.concatenate([[0], np.cumsum(end)[:-1]]).astype(np.int64)
    L = sc.axes @ m['transform']                           # file object space -> Unreal (cm)
    P = V @ L.T
    # fan-triangulate each polygon over its corners
    starts = np.concatenate([[0], np.nonzero(end)[0][:-1] + 1])
    tris = []
    for s, e in zip(starts, np.nonzero(end)[0]):
        for k in range(s + 1, e):
            tris.append((s, k, k + 1))
    tris = np.array(tris, np.int64).reshape(-1, 3)
    normals = _layer(geo, 'LayerElementNormal', 'Normals', 'NormalsIndex', n, poly_of_corner, corner_vertex)
    if normals is not None:
        Nm = np.linalg.inv(L).T                             # normals take the inverse transpose
        normals = normals @ Nm.T
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    uv_layers = []
    for el in geo.findall('LayerElementUV'):
        data = el.find('UV')
        if data is None:
            continue
        vals = np.asarray(data.props[0], np.float64).reshape(-1, 2)
        idx = el.find('UVIndex')
        mapping = el.find('MappingInformationType').props[0] if el.find('MappingInformationType') else 'ByPolygonVertex'
        k = np.arange(n) if mapping == 'ByPolygonVertex' else corner_vertex
        if idx is not None and (el.find('ReferenceInformationType').props[0] if el.find('ReferenceInformationType') else '') != 'Direct':
            k = np.asarray(idx.props[0], np.int64)[k]
        uv = vals[np.clip(k, 0, len(vals) - 1)].copy()
        uv[:, 1] = 1.0 - uv[:, 1]                          # Unreal's V runs down the image
        uv_layers.append(uv.astype(np.float32))
    mat_el = geo.find('LayerElementMaterial')
    if mat_el is not None and mat_el.find('Materials') is not None:
        mvals = np.asarray(mat_el.find('Materials').props[0], np.int64)
        mapping = mat_el.find('MappingInformationType').props[0] if mat_el.find('MappingInformationType') else 'AllSame'
        poly_mat = mvals if mapping == 'ByPolygon' else np.full(int(end.sum()), mvals[0] if len(mvals) else 0)
    else:
        poly_mat = np.zeros(int(end.sum()), np.int64)
    tri_group = poly_mat[poly_of_corner[tris[:, 0]]] if len(tris) else np.zeros(0, np.int64)
    # mirroring flips handedness: keep faces facing their normals (UE front = (p2-p0) x (p1-p0))
    if len(tris) and normals is not None:
        a, b, c = P[corner_vertex[tris[:, 0]]], P[corner_vertex[tris[:, 1]]], P[corner_vertex[tris[:, 2]]]
        fn = np.cross(c - a, b - a)
        if np.einsum('ij,ij->i', fn, normals[tris].mean(1)).mean() < 0:
            tris = tris[:, ::-1]
    nslots = max(len(m['materials']), int(tri_group.max()) + 1 if len(tri_group) else 1)
    slots = []
    for k in range(nslots):
        mid = m['materials'][k] if k < len(m['materials']) else None
        mname = sc.materials[mid]['name'] if mid in sc.materials else 'Slot%d' % k
        mat = (set_of_material or {}).get((path, k))
        slots.append((mname, (MAT_ROOT + mat) if mat else None))
    md = {'positions': P[corner_vertex].astype(np.float32), 'inst_vertex': np.arange(n, dtype=np.int64),
          'tri_inst': tris, 'tri_group': tri_group.astype(np.int64),
          'normal': None if normals is None else normals.astype(np.float32),
          'tangent': None, 'sign': None, 'color': None, 'uvs': uv_layers,
          'edge_vertices': None, 'edge_hard': None, 'slot_names': [s[0] for s in slots]}
    return {'md': md, 'slots': slots, 'collision': _collision(sc, name), 'path': path,
            'settings': {'BuildScale3D': (1.0, 1.0, 1.0), 'bRecomputeNormals': normals is None,
                         'bRecomputeTangents': True},
            'lightmap_index': None}


def _collision(sc, name):
    """Unreal-convention collision objects (UCX_<name>_NN) as convex hulls, in the mesh's space."""
    hulls = []
    for other, m in sc.models.items():
        if other.startswith(COLLISION_PREFIXES) and re.sub(r'^U[CBSP][XP]_', '', other).rsplit('_', 1)[0] in (name, other[4:]):
            V = np.asarray(m['geometry'].find('Vertices').props[0], np.float64).reshape(-1, 3)
            hulls.append(V @ (sc.axes @ m['transform']).T)
    # without authored collision a piece collides as drawn: one bounding box would close an
    # arch's doorway (Mediterranean brickportal)
    return {'boxes': [], 'convex': hulls, 'sphyls': 0, 'trace': 'default' if hulls else 'complex_as_simple'}


# -- texture sets ----------------------------------------------------------------------------------------

def _words(s):
    s = re.sub(r'(?<=[a-z])(?=[A-Z])', '_', s)
    return [w for w in re.split(r'[^a-z0-9]+', s.lower()) if w and not w.isdigit()]


def _role(stem):
    """(role, set name) of an image file stem, or None: the channel word is the last word
    (Name_BaseColor) or the first (normal_name, rogh_name)."""
    low = stem.lower()
    if any(w in low for w in IGNORE_WORDS):
        return None
    parts = re.split(r'[_\-. ]+', stem)
    for role, words in ROLE_WORDS.items():
        if parts and parts[-1].lower() in words:
            return role, '_'.join(parts[:-1])
    for role, words in ROLE_WORDS.items():
        if len(parts) > 1 and parts[0].lower() in words:
            return role, '_'.join(parts[1:])
    return None


def texture_sets(roots):
    """{set name: {role: image path}} for every image under the roots. A set made only of
    roughness or normal images joins the set whose name contains it (sand_stone_floor joins
    sand_stone_floor_LP_rood)."""
    sets = {}
    for root in roots:
        for dp, _dn, fns in os.walk(root):
            for f in sorted(fns):
                stem, ext = os.path.splitext(f)
                if ext.lower() not in IMAGE_EXT:
                    continue
                stem = os.path.splitext(stem)[0] if os.path.splitext(stem)[1].lower() in IMAGE_EXT else stem
                got = _role(stem)
                if got:
                    role, name = got
                    sets.setdefault(name, {}).setdefault(role, os.path.join(dp, f))
    for name in sorted(sets):
        if 'BC' not in sets[name]:
            host = next((o for o in sorted(sets) if o != name and name.lower() in o.lower() and 'BC' in sets[o]), None)
            if host:
                for role, p in sets.pop(name).items():
                    sets[host].setdefault(role, p)
    return {n: s for n, s in sets.items() if 'BC' in s}


def match(roots):
    """{(mesh path, slot): (set name, reason)} for every mesh slot of the pack: the set a
    material's own texture connection names; else the set sharing most name words with the
    material, the FBX file and the object (whole words count double); else the set most other
    objects of the same file took; else None."""
    sets = texture_sets(roots)
    by_file = {}
    for name, roles in sets.items():
        for p in roles.values():
            by_file[os.path.basename(p).lower()] = name
    words = {n: set(_words(n)) for n in sets}
    out = {}
    for mesh in list_meshes(roots):
        fbx, obj = mesh.split(SEP, 1)
        sc = scene(fbx)
        m = sc.models[obj]
        nslots = max(1, len(m['materials']))
        for k in range(nslots):
            mid = m['materials'][k] if k < len(m['materials']) else None
            mat = sc.materials.get(mid, {'name': '', 'textures': []})
            hit = None
            for _prop, f in mat['textures']:
                base = re.split(r'[\\/]', f)[-1].lower()
                if base in by_file:
                    hit = (by_file[base], 'texture connected to material %s' % mat['name'])
                    break
            if hit is None:
                query = [(w, 3) for w in _words(mat['name'])] + [(w, 2) for w in _words(os.path.splitext(os.path.basename(fbx))[0])] \
                    + [(w, 1) for w in _words(obj)]
                best, best_score = None, 0.0
                for n in sorted(sets, key=lambda n: (-len(sets[n]), n)):
                    sc_ = sum(wt * (1.0 if w in words[n] else 0.5 if any(w in x or x in w for x in words[n] if len(x) > 2 and len(w) > 2) else 0)
                              for w, wt in query)
                    if sc_ > best_score:
                        best, best_score = n, sc_
                if best:
                    hit = (best, 'texture set chosen by name (%s)' % ', '.join(sorted(set(w for w, _ in query) & words[best])
                                                                             or ['partial words']))
            out[(mesh, k)] = hit
    # unmatched slots take the set most matched objects of the same file took
    for (mesh, k), hit in list(out.items()):
        if hit is None:
            fbx = mesh.split(SEP, 1)[0]
            peers = [h[0] for (m2, _k2), h in out.items() if h and m2.split(SEP, 1)[0] == fbx]
            if peers:
                common = max(sorted(set(peers)), key=peers.count)
                out[(mesh, k)] = (common, 'no name match: the set the other objects of %s took' % os.path.basename(fbx))
    # still unmatched: the set of a file whose name this file's name contains (plantsfillup.fbx
    # takes what plants.fbx took)
    stems = {}
    for (m2, _k2), h in out.items():
        if h:
            st = os.path.splitext(os.path.basename(m2.split(SEP, 1)[0]))[0].lower()
            stems.setdefault(st, []).append(h[0])
    for (mesh, k), hit in list(out.items()):
        if hit is None:
            st = os.path.splitext(os.path.basename(mesh.split(SEP, 1)[0]))[0].lower()
            donor = max((s for s in stems if s != st and s in st), key=len, default=None)
            if donor:
                peers = stems[donor]
                out[(mesh, k)] = (max(sorted(set(peers)), key=peers.count),
                                  'no name match: the set %s.fbx took' % donor)
    return out, sets


# -- the pack being converted (set by library.convert and each worker) -----------------------------

_PACK = {'roots': None, 'match': None, 'sets': None}


def configure(roots):
    """Remember the pack roots; the texture matching is done once, on first use."""
    if _PACK['roots'] != list(roots):
        _PACK.update(roots=list(roots), match=None, sets=None)


def _matched():
    if _PACK['match'] is None:
        got, sets = match(_PACK['roots'] or [])
        _PACK['match'] = {key: hit[0] for key, hit in got.items() if hit}
        _PACK['reasons'] = {key: hit[1] for key, hit in got.items() if hit}
        _PACK['sets'] = sets
    return _PACK['match']


def sets():
    _matched()
    return _PACK['sets']


def match_reasons():
    """{(mesh path, slot): why its texture set was chosen} for the report."""
    _matched()
    return dict(_PACK.get('reasons') or {})


def read(path):
    """meshconvert.read for an FBX mesh path, with the pack's texture-set matching."""
    return mesh_src(path, _matched())


# -- materials (matconvert hands '/FabFbx/<set>' plans here) ---------------------------------------

def is_material(gpath):
    return bool(gpath) and gpath.startswith(MAT_ROOT)


def _load(path):
    from PIL import Image
    im = Image.open(path)
    im.load()
    return im.convert('RGBA')


def analyse(sets, gpath, large=False):
    """A matconvert plan for a texture set."""
    name = gpath[len(MAT_ROOT):]
    roles = sets.get(name)
    plan = {'source': gpath, 'vc': None, 'reasons': [], 'route': None, 'grade': None, 'two_sided_mesh': False,
            'drop': False, 'master': 'texture set', 'frame': None, 'header': {'ShadingModel': 'MSM_DefaultLit'},
            'samples': [], 'overrides': {}, 'vc_read': []}
    if not roles:
        plan.update(route='fallback', grade='fallback', reasons=['texture set %s not found: neutral grey' % name])
        return plan
    bc = _load(roles['BC'])
    a = np.asarray(bc)[..., 3]
    masked = float((a < CLIP * 255).mean()) >= MASK_SHARE
    reasons = ['loose texture set %s (%s)' % (name, ', '.join(sorted(roles)))]
    if 'NM' in roles and not any(w in os.path.basename(roles['NM']).lower() for w in DIRECTX_WORDS):
        reasons.append('normal map read as OpenGL (green up) and flipped for OMD3')
    if 'ORM' not in roles and not ({'R', 'AO', 'M'} & set(roles)):
        reasons.append('no roughness/metal image: rough 0.5, metal 0')
    budget = SIZE_LARGE if large else SIZE_DEFAULT
    size = SIZE_MIN
    while size < max(bc.size) and size < budget:
        size *= 2
    plan.update(size=int(size), masked=masked, two_sided=masked, clip=CLIP,
                route='alphamask' if masked else 'prop', two_sided_mesh=masked)
    if masked:
        reasons.append('alpha cut out at %.2f (Master_Trap_AlphaMask), two-sided by duplicated faces' % CLIP)
    plan['reasons'] = reasons
    plan['grade'] = 'close'
    plan['_fbx'] = roles
    return plan


def bake(plan):
    """matconvert.bake's {pin: (N, N, C)} for a texture-set plan: the images themselves."""
    from riftlord.fab import matgraph as mg
    from PIL import Image
    N = plan['size']
    roles = plan['_fbx']

    def img(role):
        if role not in roles:
            return None
        return np.asarray(_load(roles[role]).resize((N, N), Image.LANCZOS)).astype(np.float32) / 255.0
    out = {}
    bc = img('BC')
    out['BaseColor'] = mg.srgb_to_linear(bc[..., :3])
    if plan.get('masked'):
        out['OpacityMask'] = bc[..., 3:4]
    nm = img('NM')
    if nm is not None:
        n = nm[..., :3] * 2.0 - 1.0
        if not any(w in os.path.basename(roles['NM']).lower() for w in DIRECTX_WORDS):
            n[..., 1] = -n[..., 1]
        out['Normal'] = n
    orm = img('ORM')
    if orm is not None:
        out['AmbientOcclusion'], out['Roughness'], out['Metallic'] = orm[..., 0:1], orm[..., 1:2], orm[..., 2:3]
    for role, pin in (('R', 'Roughness'), ('AO', 'AmbientOcclusion'), ('M', 'Metallic')):
        x = img(role)
        if x is not None:
            out[pin] = x[..., 0:1]
    return out
