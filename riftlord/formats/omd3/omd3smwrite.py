"""Write cooked OMD3 StaticMesh packages offline: new geometry, materials and collision.

Clone-and-replace: a shipped static mesh package (StaticMesh + BodySetup + NavCollision
exports) is the template. Its render data is replaced by ONE LOD built from a plain mesh
description, its BodySetup by authored collision (boxes, convex hulls, optional complex
trimesh) with PhysX data cooked offline by `omd3physx`, and its NavCollision by the
matching cooked nav geometry. Every derived buffer is regenerated: reversed, depth-only and
reversed depth-only index buffers, the PN-AEN adjacency buffer, BuffersSize, bounds, UV
channel densities. The codec is `omd3sm_format`; the format is in
thirdparty/STATICMESH-FORMAT.md.

    python3 omd3smwrite.py identity                 # byte-identical round trip of every shipped SM
    python3 omd3smwrite.py rewrite Cren_Shelf_1x1 --out DIR   # decode + re-encode a shipped mesh

    import omd3smwrite as W
    t = W.Template()                                # default template (TEMPLATE)
    blobs, report = W.write(t, desc, '/Game/Mods/X/SM_Y', materials=[...], collision={...})
"""

import hashlib
import json
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamefiles  # noqa: E402
from riftlord.formats.omd3 import omd3sm_format as fmt  # noqa: E402
from riftlord.core import uetags  # noqa: E402
from riftlord.formats.ue.uassetio import Package, fname_hashes  # noqa: E402

#: The default template: one material slot, box collision with CTF_UseSimpleAsComplex,
#: BodySetup + NavCollision + StaticMesh exports, 16-bit indices, no sockets, no
#: distance field, and a DefaultInstance that answers the OMDTrapPlacement channel.
TEMPLATE = '/Game/Environments/CastleOrder/Crenellation/RusticBrick/Mesh/Cren_Shelf_1x1'
#: FRawStaticIndexBuffer goes 32-bit once an index no longer fits in a uint16.
INDEX16_MAX = 0xFFFF
#: Shipped meshes keep at most 8 UV channels (MAX_STATIC_TEXCOORDS).
MAX_TEXCOORDS = 8
#: Every shipped LOD's class strip flags: ray-tracing data stripped (CDSF_RayTracingResources).
LOD_STRIP = (1, 8)
#: FStaticMeshVertexBuffer / FColorVertexBuffer strip flags on every shipped mesh.
VB_STRIP = (1, 0)
#: Distance-field strip flags on every shipped mesh (editor stripped, field kept per LOD).
DF_STRIP = (1, 0)
#: FStaticMeshSection flags on 1,261 of 1,287 shipped meshes:
#: bEnableCollision, bCastShadow, bForceOpaque, bVisibleInRayTracing.
SECTION_FLAGS = (1, 1, 0, 1)
#: FUVDensityAccumulator drops this share of triangles at each end of the density sort.
UV_DENSITY_DISCARD = 0.10
#: SMALL_NUMBER in UE: triangles with smaller world or UV area are ignored for density.
SMALL_NUMBER = 1e-8
#: Collision trace flags (ECollisionTraceFlag) by the names the BodySetup tag stores.
TRACE_FLAGS = {'default': None, 'simple_as_complex': 'CTF_UseSimpleAsComplex',
               'complex_as_simple': 'CTF_UseComplexAsSimple'}
#: Boxes thinner than this (uu) are thickened: a zero-thickness PhysX box is degenerate
#: (the Docks floor SM_Floor_interior_01 carries a 2e-13 uu thick box).
MIN_BOX_SIZE = 1.0
#: Scan cache of every StaticMesh package in the install.
LIST_CACHE = 'staticmesh-list.json'


# -- the shipped catalogue --------------------------------------------------------------

def all_static_meshes(refresh=False):
    """Every /Game (and /Engine) package holding a StaticMesh export, as pak stems."""
    from riftlord.core import gamepath
    cache = os.path.join(gamepath.scratch('gamefiles', beside=gamepath.CODE_ROOT), LIST_CACHE)
    if os.path.exists(cache) and not refresh:
        with open(cache) as fh:
            return json.load(fh)
    out = []
    for p in sorted(gamefiles.index()):
        if not p.endswith('.uasset'):
            continue
        try:
            pkg = Package(gamefiles.read(p))
        except Exception:                            # noqa: BLE001
            continue
        names = [n for n, _h in pkg.names]
        if 'StaticMesh' not in names:
            continue
        for e in pkg.exports:
            ci = struct.unpack_from('<i', e['meta'], 0)[0]
            if ci < 0 and names[struct.unpack('<7i', pkg.imports[-ci - 1])[5]] == 'StaticMesh':
                out.append(p[:-len('.uasset')])
                break
    with open(cache, 'w') as fh:
        json.dump(out, fh)
    return out


def _stem_path(path):
    """A /Game path or a pak stem -> (uasset, uexp)."""
    return gamefiles.package(path)


# -- template ---------------------------------------------------------------------------

class Template:
    """A static mesh package parsed strictly: the StaticMesh export plus its subobjects."""

    def __init__(self, path=TEMPLATE, blobs=None):
        self.path = path
        if blobs is None:
            ua, ux = _stem_path(path)
            blobs = {'.uasset': ua, '.uexp': ux}
        self.blobs = blobs
        self.pkg = Package(blobs['.uasset'], blobs['.uexp'])
        self.names = [n for n, _h in self.pkg.names]
        self.index = {}
        for i, e in enumerate(self.pkg.exports):
            self.index.setdefault(self._class(e), i)
        if 'StaticMesh' not in self.index:
            raise SystemExit('%s holds no StaticMesh export' % path)
        data = self.export['data']
        self.ctx = fmt.context(data, self.names)
        self.model = fmt.parse(data, self.ctx['end'])
        self.props, _end = uetags.read(data, self.names)
        self.object = self.fname_of(self.index['StaticMesh'])

    @property
    def export(self):
        return self.pkg.exports[self.index['StaticMesh']]

    def _class(self, e):
        ci = struct.unpack_from('<i', e['meta'], 0)[0]
        if ci >= 0:
            return None
        return self.names[struct.unpack('<7i', self.pkg.imports[-ci - 1])[5]]

    def fname(self, index, number=0):
        return self.names[index] if number == 0 else '%s_%d' % (self.names[index], number - 1)

    def fname_of(self, export_index):
        return self.names[struct.unpack_from('<i', self.pkg.exports[export_index]['meta'], 16)[0]]

    def import_record(self, index):
        r = struct.unpack('<7i', self.pkg.imports[-index - 1])
        return self.names[r[2]], r[4], self.names[r[5]]

    def material_paths(self):
        out = []
        for m in self.model['materials']:
            path = obj = cls = None
            if m[0] < 0:
                cls, outer, obj = self.import_record(m[0])
                if outer < 0:
                    path = self.import_record(outer)[2]
            out.append((path, obj, self.fname(m[1], m[2]), cls))
        return out

    def body_setup(self):
        """The BodySetup export: tagged props, native fields and its PhysXPC blob."""
        i = self.index.get('BodySetup')
        if i is None:
            return None
        data = self.pkg.exports[i]['data']
        props, end = uetags.read(data, self.names)
        n = parse_body_native(data, end, self.names)
        return {'index': i, 'props': props, 'end': end, 'native': n,
                'blob': n['formats'].get(omd3physx_format(), {}).get('payload')}

    def nav_collision(self):
        i = self.index.get('NavCollision')
        if i is None:
            return None
        data = self.pkg.exports[i]['data']
        props, end = uetags.read(data, self.names)
        return {'index': i, 'props': props, 'end': end,
                'native': parse_nav_native(data, end, self.names)}

    def lod0(self):
        return next(lod for lod in self.model['lods'] if 'buffers' in lod)

    def collision_trimesh(self):
        """UStaticMesh::GetPhysicsTriMeshDataCheckComplex in the editor: LODForCollision,
        sections with bEnableCollision, vertices numbered in first use, material = section
        MaterialIndex."""
        p = uetags.get(self.props, 'LODForCollision')
        lods = [lod for lod in self.model['lods'] if 'buffers' in lod]
        lod = self.model['lods'][min(p.value if p else 0, len(self.model['lods']) - 1)]
        if 'buffers' not in lod:
            lod = lods[0]
        b = lod['buffers']
        P = np.frombuffer(b['positions'][2], '<f4').reshape(-1, 3)
        I = index_array(b['index'])
        remap, verts, tris, mats = {}, [], [], []
        for s in lod['sections']:
            if not s[5]:
                continue
            for k in range(s[1], s[1] + 3 * s[2], 3):
                tri = []
                for v in I[k:k + 3]:
                    v = int(v)
                    if v not in remap:
                        remap[v] = len(verts)
                        verts.append(P[v])
                    tri.append(remap[v])
                tris.append(tri)
                mats.append(s[0])
        if not tris:
            return None
        return np.array(verts, np.float32), np.array(tris, np.uint32), np.array(mats, np.uint16)


def omd3physx_format():
    from riftlord.formats.omd3 import omd3physx
    return omd3physx.FORMAT


# -- BodySetup / NavCollision native tails ---------------------------------------------------

def parse_body_native(data, end, names):
    """UBodySetup::Serialize after the tags: object guid flag, BodySetupGuid, bCooked,
    bHasCookedCollisionData, then FFormatContainer (count, {FName, FByteBulkData})."""
    r = fmt.R(data, end)
    n = {'object_guid': r.boolean()}
    if n['object_guid']:
        n['guid'] = r.take(16)
    n['body_guid'] = r.take(16)
    n['cooked'] = r.boolean()
    n['formats'] = {}
    n['order'] = []
    if n['cooked']:
        n['has_cooked'] = r.boolean()
        for _ in range(r.count(16)):
            name = fmt._fname(data, r.o, names)
            r.take(8)
            n['formats'][name] = fmt.read_bulk_header(r)
            n['order'].append(name)
    n['rest'] = r.take(len(data) - r.o)
    return n


def build_body_native(n, pkg):
    w = fmt.W()
    w.put('I', n['object_guid'])
    if n['object_guid']:
        w.raw(n['guid'])
    w.raw(n['body_guid'])
    w.put('I', n['cooked'])
    if n['cooked']:
        w.put('I', n['has_cooked'])
        w.put('i', len(n['order']))
        for name in n['order']:
            w.put('ii', pkg.add_name(name), 0)
            fmt.write_bulk_header(w, n['formats'][name])
    w.raw(n['rest'])
    return bytes(w.b)


#: UNavCollision::Serialize's version magic and the version every shipped mesh carries.
NAV_MAGIC, NAV_VERSION = 0xA237F237, 4
#: The cooked nav format name (NAVCOLLISION_FORMAT).
NAV_FORMAT = 'NavCollision_X'


def parse_nav_native(data, end, names):
    r = fmt.R(data, end)
    n = {'object_guid': r.boolean()}
    if n['object_guid']:
        n['guid'] = r.take(16)
    n['magic'], n['version'] = r.u('Ii')
    n['nav_guid'] = r.take(16)
    n['cooked'] = r.boolean()
    n['formats'], n['order'] = {}, []
    for _ in range(r.count(16)):
        name = fmt._fname(data, r.o, names)
        r.take(8)
        n['formats'][name] = fmt.read_bulk_header(r)
        n['order'].append(name)
    n['rest'] = r.take(len(data) - r.o)         # AreaClass (object index)
    return n


def build_nav_native(n, pkg):
    w = fmt.W()
    w.put('I', n['object_guid'])
    if n['object_guid']:
        w.raw(n['guid'])
    w.put('Ii', n['magic'], n['version'])
    w.raw(n['nav_guid'])
    w.put('I', n['cooked'])
    w.put('i', len(n['order']))
    for name in n['order']:
        w.put('ii', pkg.add_name(name), 0)
        fmt.write_bulk_header(w, n['formats'][name])
    w.raw(n['rest'])
    return bytes(w.b)


#: FRecastNavMeshGenerator::ExportRigidBodyBoxElements: corner order and the 12 triangles.
BOX_CORNERS = ((-1, -1, 1), (1, -1, 1), (-1, -1, -1), (1, -1, -1),
               (-1, 1, 1), (1, 1, 1), (-1, 1, -1), (1, 1, -1))
BOX_TRIANGLES = (3, 2, 0, 3, 0, 1, 7, 3, 1, 7, 1, 5, 6, 7, 5, 6, 5, 4,
                 2, 6, 4, 2, 4, 0, 1, 0, 4, 1, 4, 5, 7, 6, 2, 7, 2, 3)


def nav_blob(boxes=(), hulls=(), trimesh=None):
    """FDerivedDataNavCollisionCooker::Build: u8 endian, trimesh verts/indices (only for
    ComplexAsSimple), convex verts/indices, ConvexShapeIndices. Convex hulls come first,
    then boxes, as ExportRigidBodyGeometry orders them."""
    cv, ci, shapes = [], [], []
    for hull_verts, _tris, polys in hulls:
        # ExportPxConvexMesh: every polygon's vertices appended in turn, fanned (0, j+2, j+1)
        # against the hull's first vertex slot (StartVertOffset), as UE 4.27 does
        start = len(cv)
        shapes.append(start)
        for poly in polys:
            for k in poly:
                cv.append(tuple(np.asarray(hull_verts[k], np.float32)))
            for j in range(len(poly) - 2):
                ci.extend((start, start + j + 2, start + j + 1))
            start += len(poly)
    for b in boxes:
        base = len(cv)
        shapes.append(base)
        cv.extend(box_corners(b))
        ci.extend(base + k for k in BOX_TRIANGLES)
    tv, ti = ([], []) if trimesh is None else (list(map(tuple, trimesh[0])), list(np.asarray(trimesh[1]).ravel()))
    out = bytearray(b'\1')
    for verts, idx in ((tv, ti), (cv, ci)):
        out += struct.pack('<i', len(verts)) + np.asarray(verts, '<f4').reshape(-1, 3).tobytes()
        out += struct.pack('<i', len(idx)) + np.asarray(idx, '<i4').tobytes()
    out += struct.pack('<i', len(shapes)) + np.asarray(shapes, '<i4').tobytes()
    return bytes(out)


def box_corners(b):
    """World corners of a box spec {'center', 'size', 'rotation' (pitch, yaw, roll deg)}."""
    c = np.asarray(b['center'], np.float64)
    e = np.asarray(b['size'], np.float64) * 0.5
    R = rotator_matrix(b.get('rotation', (0, 0, 0)))
    return [tuple((R @ (np.array(k) * e) + c).astype(np.float32)) for k in BOX_CORNERS]


def rotator_matrix(rot):
    """FRotationMatrix for FRotator(pitch, yaw, roll) in degrees (columns = axes)."""
    p, y, r = np.radians(rot)
    sp, cp, sy, cy, sr, cr = np.sin(p), np.cos(p), np.sin(y), np.cos(y), np.sin(r), np.cos(r)
    m = np.array([[cp * cy, cp * sy, sp],
                  [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
                  [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp]])
    return m.T


# -- buffers --------------------------------------------------------------------------------

def index_array(ib):
    return np.frombuffer(ib['bulk'][2], '<u4' if ib['b32'] else '<u2').astype(np.int64)


def _ib(indices, b32):
    raw = np.asarray(indices, '<u4' if b32 else '<u2').tobytes()
    return {'b32': int(b32), 'bulk': (1, len(raw), raw), 'expand32': 0}


def pack_normal(v, w=None):
    """FPackedNormal: signed int8 round(x * 127); W = 127, or -127 for a negative sign."""
    out = np.empty((len(v), 4), np.int8)
    out[:, :3] = np.clip(np.rint(np.asarray(v, np.float64) * 127.0), -127, 127)
    out[:, 3] = 127 if w is None else np.where(np.asarray(w) < 0, -127, 127)
    return out


def _unit(v, fallback):
    v = np.asarray(v, np.float64)
    ln = np.linalg.norm(v, axis=1, keepdims=True)
    out = v / np.where(ln < 1e-12, 1, ln)
    bad = ln[:, 0] < 1e-12
    if bad.any():
        fb = np.asarray(fallback, np.float64)
        out[bad] = fb[bad] if fb.ndim == 2 else fb
    return out


def _given(v, fallback):
    """Caller-supplied unit vectors are packed as given (re-normalising a decoded packed
    normal moves it by one step); only zero-length ones are replaced."""
    v = np.asarray(v, np.float64).copy()
    bad = np.linalg.norm(v, axis=1) < 1e-6
    v[bad] = fallback
    return v


def weld_keys(pos):
    """An id per distinct position (exact float32 equality)."""
    _u, inv = np.unique(np.ascontiguousarray(pos, np.float32).view('V12').ravel(),
                        return_inverse=True)
    return inv.ravel()


def vertex_normals(pos, tris):
    """Area-weighted smooth normals in UE's winding (front face = (p2-p0) x (p1-p0))."""
    p0, p1, p2 = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    fn = np.cross(p2 - p0, p1 - p0)
    key = weld_keys(pos)
    acc = np.zeros((key.max() + 1, 3))
    for c in range(3):
        np.add.at(acc, key[tris[:, c]], fn)
    return _unit(acc[key], np.array([0.0, 0.0, 1.0]))


def tangent_frame(pos, normals, uv, tris):
    """(TangentX, binormal sign) per vertex from UV0, Gram-Schmidt against the normal.
    The sign is UE's GetBasisDeterminantSign(TangentX, TangentY, TangentZ)."""
    p0, p1, p2 = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    t0, t1, t2 = uv[tris[:, 0]], uv[tris[:, 1]], uv[tris[:, 2]]
    e1, e2 = p1 - p0, p2 - p0
    d1, d2 = t1 - t0, t2 - t0
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(det) > 1e-20, 1.0 / np.where(det == 0, 1, det), 0.0)[:, None]
    tu = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) * r
    tv = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) * r
    acc_u = np.zeros((len(pos), 3))
    acc_v = np.zeros((len(pos), 3))
    for c in range(3):
        np.add.at(acc_u, tris[:, c], tu)
        np.add.at(acc_v, tris[:, c], tv)
    n = np.asarray(normals, np.float64)
    tx = acc_u - n * (acc_u * n).sum(1, keepdims=True)
    fallback = np.cross(n, np.where(np.abs(n[:, 2:3]) < 0.9, [[0, 0, 1.0]], [[1.0, 0, 0]]))
    tx = _unit(tx, _unit(fallback, np.array([1.0, 0, 0])))
    # TangentY = (Z x X) * sign in UE; sign = det [X Y Z]
    sign = np.where((np.cross(n, tx) * acc_v).sum(1) < 0, -1.0, 1.0)
    return tx.astype(np.float32), sign.astype(np.float32)


def adjacency(idx, pos, uv0):
    """PN-AEN dominant-corner adjacency (12 indices per triangle) -- the skeletal writer's
    reproduction of nvtess, which matches the shipped static buffers exactly (measured)."""
    from riftlord.formats.omd3 import omd3skmwrite
    return omd3skmwrite.adjacency(idx, pos, uv0)


def depth_only(idx, pos, sections):
    """BuildDepthOnlyIndexBuffer: every index remapped to the lowest-numbered vertex at the
    same position, section by section (UE then cache-optimises; we keep index order)."""
    key = weld_keys(pos)
    first = np.full(key.max() + 1, len(pos), np.int64)
    np.minimum.at(first, key, np.arange(len(pos)))
    out = []
    for s in sections:
        out.append(first[key[idx[s[1]:s[1] + 3 * s[2]]]])
    return np.concatenate(out) if out else np.zeros(0, np.int64)


def reversed_indices(idx, sections):
    out = idx.copy()
    for s in sections:
        f, n = s[1], 3 * s[2]
        out[f:f + n] = idx[f:f + n][::-1]
    return out


def uv_densities(pos, idx, uvs, sections, slots):
    """FStaticMaterial.UVChannelData.LocalUVDensities per slot: per section and UV channel,
    sqrt(world area / uv area) weighted by sqrt(world area), the lowest and highest 10%
    discarded (FUVDensityAccumulator), then summed over the slot's sections."""
    acc = {s: np.zeros((4, 2)) for s in range(slots)}
    for s in sections:
        tri = idx[s[1]:s[1] + 3 * s[2]].reshape(-1, 3)
        p0, p1, p2 = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
        area = np.linalg.norm(np.cross(p1 - p0, p2 - p0), axis=1)
        for ch in range(min(4, len(uvs))):
            u = uvs[ch]
            a, b, c = u[tri[:, 0]], u[tri[:, 1]], u[tri[:, 2]]
            ab, ac = b - a, c - a
            uva = np.abs(ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0])
            ok = (area > SMALL_NUMBER) & (uva > SMALL_NUMBER)
            dens = np.sqrt(area[ok] / uva[ok])
            wt = np.sqrt(area[ok])
            order = np.argsort(dens, kind='stable')
            k = int(np.floor(UV_DENSITY_DISCARD * len(order)))
            keep = order[k:len(order) - k]
            acc[s[0]][ch] += ((dens[keep] * wt[keep]).sum(), wt[keep].sum())
    out = []
    for s in range(slots):
        a = acc[s]
        out.append([float(a[c, 0] / a[c, 1]) if a[c, 1] > SMALL_NUMBER else 0.0 for c in range(4)])
    return out


def bounds_of(P):
    """FBoxSphereBounds as UE builds it: AABB centre and extent; radius = farthest vertex."""
    P = np.asarray(P, np.float64)
    lo, hi = P.min(0), P.max(0)
    c, e = (lo + hi) / 2, (hi - lo) / 2
    rad = float(np.sqrt(((P - c) ** 2).sum(1)).max())
    return tuple(c.tolist() + e.tolist() + [rad])


# -- decode a shipped LOD into a description --------------------------------------------------

def decode(t, lod_index=0):
    lod = [lod for lod in t.model['lods'] if 'buffers' in lod][lod_index]
    b = lod['buffers']
    n = b['pos_meta'][1]
    P = np.frombuffer(b['positions'][2], '<f4').reshape(-1, 3)
    T = np.frombuffer(b['tangents'][2], 'i1').reshape(n, 2, 4).astype(np.float32) / 127.0
    nt, _n, full, _hp = b['vb_meta']
    uv = np.frombuffer(b['texcoords'][2], '<f4' if full else '<f2').reshape(n, nt, 2).astype(np.float32)
    I = index_array(b['index'])
    mats = np.zeros(len(I) // 3, np.int64)
    for s in lod['sections']:
        mats[s[1] // 3:s[1] // 3 + s[2]] = s[0]
    cols = None
    if b['colors'] is not None:
        bgra = np.frombuffer(b['colors'][2], 'u1').reshape(-1, 4)
        cols = bgra[:, [2, 1, 0, 3]].copy()
    return {'positions': P.copy(), 'normals': T[:, 1, :3].copy(), 'tangents': T[:, 0, :3].copy(),
            'signs': np.sign(T[:, 1, 3]).astype(np.float32),
            'uvs': [uv[:, k].copy() for k in range(nt)], 'colors': cols,
            'triangles': I.reshape(-1, 3), 'materials': mats}


# -- build one LOD --------------------------------------------------------------------------------

def build_lod(desc, template_lod, nslots, lightmap_index=None):
    """The LOD dict (fmt layout) for a description, sections per slot in slot order.
    Each section gets a contiguous vertex range (vertices shared across slots are duplicated)."""
    P = np.asarray(desc['positions'], np.float32).reshape(-1, 3)
    tris = np.asarray(desc['triangles'], np.int64).reshape(-1, 3)
    mats = np.asarray(desc.get('materials', np.zeros(len(tris))), np.int64)
    if len(mats) != len(tris):
        raise ValueError('%d triangle materials for %d triangles' % (len(mats), len(tris)))
    if mats.max() >= nslots:
        raise ValueError('a triangle names slot %d; the mesh has %d' % (mats.max(), nslots))
    uvs = [np.asarray(u, np.float32).reshape(-1, 2) for u in desc['uvs']]
    if not uvs:
        raise ValueError('at least one UV channel is required')
    want = max(len(uvs), (lightmap_index or 0) + 1)
    while len(uvs) < want:
        uvs.append(uvs[0])                     # a missing lightmap channel copies UV0
    if len(uvs) > MAX_TEXCOORDS:
        raise ValueError('%d UV channels; at most %d' % (len(uvs), MAX_TEXCOORDS))
    N = desc.get('normals')
    N = vertex_normals(P.astype(np.float64), tris) if N is None else _given(N, (0, 0, 1.0))
    if desc.get('tangents') is None:
        TX, S = tangent_frame(P.astype(np.float64), N, uvs[0].astype(np.float64), tris)
    else:
        TX = _given(desc['tangents'], (1.0, 0, 0))
        S = np.asarray(desc['signs'] if desc.get('signs') is not None else np.ones(len(P)), np.float32)
    C = desc.get('colors')

    # order: sections by slot, each with its own contiguous vertex block
    order, sections, remap_all, first = [], [], [], 0
    vbase = 0
    new_idx = []
    for slot in range(nslots):
        sel = np.nonzero(mats == slot)[0]
        if not len(sel):
            continue
        st = tris[sel]
        used, inv = np.unique(st.ravel(), return_inverse=True)
        remap_all.append(used)
        new_idx.append(inv.reshape(-1, 3) + vbase)
        sections.append([slot, first, len(sel), vbase, vbase + len(used) - 1] + list(SECTION_FLAGS))
        first += 3 * len(sel)
        vbase += len(used)
    src = np.concatenate(remap_all)
    I = np.concatenate(new_idx).ravel()
    P2, N2, TX2, S2 = P[src], N[src], TX[src], S[src]
    UV2 = [u[src] for u in uvs]
    nv = len(src)
    b32 = nv - 1 > INDEX16_MAX

    tangents = np.stack([pack_normal(TX2), pack_normal(N2, S2)], 1).tobytes()
    tex = np.stack(UV2, 1).astype('<f2').tobytes()
    b = {'strip': LOD_STRIP,
         'pos_meta': (12, nv), 'positions': (12, nv, P2.astype('<f4').tobytes()),
         'vb_strip': VB_STRIP, 'vb_meta': (len(UV2), nv, 0, 0),
         'tangents': (8, nv, tangents), 'texcoords': (4, nv * len(UV2), tex),
         'col_strip': VB_STRIP}
    if C is not None:
        C2 = np.asarray(C, np.uint8).reshape(-1, 4)[src]
        b['col_meta'] = (4, nv)
        b['colors'] = (4, nv, C2[:, [2, 1, 0, 3]].tobytes())      # FColor is BGRA in memory
    else:
        b['col_meta'] = (0, 0)
        b['colors'] = None
    sec = [tuple(s) for s in sections]
    D = depth_only(I, P2, sec)
    b['index'] = _ib(I, b32)
    b['reversed'] = _ib(reversed_indices(I, sec), b32)
    b['depth_only'] = _ib(D, b32)
    b['reversed_depth_only'] = _ib(D[::-1], b32)
    b['adjacency'] = _ib(adjacency(I, P2, UV2[0].astype(np.float32)), b32)
    empty_sampler = template_lod['buffers']['sampler']
    b['section_samplers'] = [empty_sampler for _ in sec]
    b['sampler'] = empty_sampler
    lod = {'strip': LOD_STRIP, 'sections': sec, 'max_deviation': b'\0\0\0\0',
           'cooked_out': 0, 'inlined': 1, 'buffers': b,
           'buffers_size': fmt.buffer_sizes(b)}
    geo = {'positions': P2, 'indices': I, 'uvs': UV2, 'sections': sec, 'source_vertex': src}
    return lod, geo


# -- tagged-property surgery ----------------------------------------------------------------------

def _set(props, name, kind, value, **kw):
    p = uetags.get(props, name)
    if p is None:
        props.append(uetags.prop(name, kind, value, **kw))
    else:
        p.value = value
    return props


def _vector(v):
    return struct.pack('<3f', *v)


def _bounds_props(bounds):
    return [uetags.prop('Origin', 'StructProperty', _vector(bounds[:3]), struct_name='Vector'),
            uetags.prop('BoxExtent', 'StructProperty', _vector(bounds[3:6]), struct_name='Vector'),
            uetags.prop('SphereRadius', 'FloatProperty', bounds[6])]


def _static_material_props(obj, slot, densities):
    # the shipped cooker writes all four densities, zeros included, after both flags
    uvc = [uetags.prop('bInitialized', 'BoolProperty', None, bool_value=1),
           uetags.prop('bOverrideDensities', 'BoolProperty', None, bool_value=0)]
    for k, d in enumerate(densities):
        uvc.append(uetags.prop('LocalUVDensities', 'FloatProperty', float(d), array_index=k))
    return [uetags.prop('MaterialInterface', 'ObjectProperty', obj),
            uetags.prop('MaterialSlotName', 'NameProperty', slot),
            uetags.prop('ImportedMaterialSlotName', 'NameProperty', slot),
            uetags.prop('UVChannelData', 'StructProperty', uvc, struct_name='MeshUVChannelInfo')]


def _material_import(pkg, path, obj, cls='MaterialInstanceConstant'):
    """Package index of a material import, added (with its Package outer) when new."""
    names = [n for n, _h in pkg.names]
    for i, rec in enumerate(pkg.imports):
        r = struct.unpack('<7i', rec)
        if names[r[5]] == obj and r[4] < 0 and \
                names[struct.unpack('<7i', pkg.imports[-r[4] - 1])[5]] == path:
            return -(i + 1), False
    outer = None
    for i, rec in enumerate(pkg.imports):
        r = struct.unpack('<7i', rec)
        if names[r[2]] == 'Package' and names[r[5]] == path:
            outer = -(i + 1)
    if outer is None:
        outer = pkg.add_import('/Script/CoreUObject', 'Package', 0, path)
    return pkg.add_import('/Script/Engine', cls, outer, obj), True


def rename(pkg, old_path, new_path):
    """Point a cloned package at a new path; the asset takes the path's leaf name."""
    old_obj, new_obj = old_path.rsplit('/', 1)[-1], new_path.rsplit('/', 1)[-1]
    hits = 0
    for i, (s, _h) in enumerate(pkg.names):
        if s == old_path:
            pkg.names[i] = (new_path, fname_hashes(new_path))
            hits += 1
        elif s == old_obj:
            pkg.names[i] = (new_obj, fname_hashes(new_obj))
            hits += 1
    if hits != 2:
        raise SystemExit('%s does not name itself as expected (%d hits)' % (old_path, hits))


def _guid(text):
    return hashlib.md5(text.encode()).digest()


# -- collision --------------------------------------------------------------------------------------

def box_elem_props(b):
    size = [max(float(s), MIN_BOX_SIZE) for s in b['size']]
    rot = b.get('rotation', (0, 0, 0))
    return [uetags.prop('Center', 'StructProperty', _vector(b['center']), struct_name='Vector'),
            uetags.prop('Rotation', 'StructProperty', _vector(rot), struct_name='Rotator'),
            uetags.prop('X', 'FloatProperty', size[0]),
            uetags.prop('Y', 'FloatProperty', size[1]),
            uetags.prop('Z', 'FloatProperty', size[2]),
            uetags.prop('RestOffset', 'FloatProperty', 0.0),
            uetags.prop('Name', 'NameProperty', 'None'),
            uetags.prop('bContributeToMass', 'BoolProperty', None, bool_value=1),
            uetags.prop('CollisionEnabled', 'ByteProperty', 'ECollisionEnabled::QueryAndPhysics',
                        enum='ECollisionEnabled')]


def convex_elem_props(points, tris):
    pts = np.asarray(points, np.float32).reshape(-1, 3)
    lo, hi = pts.min(0), pts.max(0)
    box = struct.pack('<6fB', *lo, *hi, 1)
    return [uetags.prop('VertexData', 'ArrayProperty',
                        ('structs', 'Vector', [struct.pack('<3f', *p) for p in pts], None),
                        inner='StructProperty'),
            uetags.prop('IndexData', 'ArrayProperty', [int(k) for k in np.asarray(tris).ravel()],
                        inner='IntProperty'),
            uetags.prop('ElemBox', 'StructProperty', box, struct_name='Box'),
            uetags.prop('Transform', 'StructProperty', [
                uetags.prop('Rotation', 'StructProperty', struct.pack('<4f', 0, 0, 0, 1), struct_name='Quat'),
                uetags.prop('Translation', 'StructProperty', _vector((0, 0, 0)), struct_name='Vector'),
                uetags.prop('Scale3D', 'StructProperty', _vector((1, 1, 1)), struct_name='Vector')],
                struct_name='Transform'),
            uetags.prop('RestOffset', 'FloatProperty', 0.0),
            uetags.prop('Name', 'NameProperty', 'None'),
            uetags.prop('bContributeToMass', 'BoolProperty', None, bool_value=1),
            uetags.prop('CollisionEnabled', 'ByteProperty', 'ECollisionEnabled::QueryAndPhysics',
                        enum='ECollisionEnabled')]


def hull_triangles(points):
    """The convex hull PhysX itself builds from `points` (via pxcook): its vertices and its
    polygons fanned into triangles (FKConvexElem VertexData/IndexData), plus the polygons
    for the nav export."""
    from riftlord.formats.omd3 import omd3physx
    verts, polys = omd3physx.hull(points)
    tris = [(p[0], p[j + 2], p[j + 1]) for p in polys for j in range(len(p) - 2)]
    return verts.astype(np.float32), np.array(tris, np.int64).reshape(-1, 3), polys


def build_collision(t, collision, P, I, sections, pkg):
    """New BodySetup and NavCollision export bytes for a collision spec:
      {'boxes': [{'center', 'size', 'rotation'}], 'convex': [points, ...],
       'trace': 'simple_as_complex' | 'default' | 'complex_as_simple'}
    default: simple shapes for simple queries, the render LOD0 as cooked trimesh for complex.
    """
    from riftlord.formats.omd3 import omd3physx
    bs = t.body_setup()
    props = list(bs['props'])
    boxes = list(collision.get('boxes', ()))
    for b in boxes:
        b['size'] = [max(float(s), MIN_BOX_SIZE) for s in b['size']]
    hulls = [hull_triangles(h) for h in collision.get('convex', ())]
    trace = collision.get('trace', 'simple_as_complex')
    agg = []
    if hulls:
        agg.append(uetags.prop('ConvexElems', 'ArrayProperty',
                               ('structs', 'KConvexElem', [convex_elem_props(v, f) for v, f, _p in hulls], None),
                               inner='StructProperty'))
    if boxes:
        agg.append(uetags.prop('BoxElems', 'ArrayProperty',
                               ('structs', 'KBoxElem', [box_elem_props(b) for b in boxes], None),
                               inner='StructProperty'))
    props = [p for p in props if p.name not in ('AggGeom', 'CollisionTraceFlag', 'bDoubleSidedGeometry')]
    out = []
    if agg:
        out.append(uetags.prop('AggGeom', 'StructProperty', agg, struct_name='KAggregateGeom'))
    out += props
    flag = TRACE_FLAGS[trace]
    if flag:
        out.append(uetags.prop('CollisionTraceFlag', 'ByteProperty', flag, enum='ECollisionTraceFlag'))
    if collision.get('double_sided'):
        # the cooked trimesh answers queries from behind too (UE: PxMeshGeometryFlag::eDOUBLE_SIDED)
        out.append(uetags.prop('bDoubleSidedGeometry', 'BoolProperty', None, bool_value=1))
    trimesh = None
    if trace != 'simple_as_complex':
        tri_v, tri_t, tri_m = [], [], []
        remap = {}
        for s in sections:
            for k in range(s[1], s[1] + 3 * s[2], 3):
                row = []
                for v in I[k:k + 3]:
                    v = int(v)
                    if v not in remap:
                        remap[v] = len(tri_v)
                        tri_v.append(P[v])
                    row.append(remap[v])
                tri_t.append(row)
                tri_m.append(s[0])
        trimesh = (np.array(tri_v, np.float32), np.array(tri_t, np.uint32), np.array(tri_m, np.uint16))
    cook_hulls = [] if trace == 'complex_as_simple' else [v for v, _f, _p in hulls]
    blob, inflated = omd3physx.blob(cook_hulls, trimesh)
    native = dict(bs['native'])
    native['body_guid'] = _guid('body:' + collision.get('_path', ''))
    native['has_cooked'] = 1
    native['order'] = [omd3physx.FORMAT]
    native['formats'] = {omd3physx.FORMAT: {'flags': omd3physx.BULK_FLAGS, 'count': len(blob),
                                            'disk': len(blob), 'offset': 0, 'payload': blob}}
    body = uetags.write(out, pkg) + build_body_native(native, pkg)
    report = {'boxes': len(boxes), 'convex': len(hulls), 'trace': trace,
              'physx_blob': omd3physx.parse_blob(blob), 'inflated': inflated}
    nav = t.nav_collision()
    navbytes = None
    if nav is not None:
        nn = dict(nav['native'])
        nb = nav_blob(boxes, hulls if trace != 'complex_as_simple' else (),
                      (trimesh[0], trimesh[1]) if trace == 'complex_as_simple' else None)
        nn['formats'] = {NAV_FORMAT: {'flags': omd3physx.BULK_FLAGS, 'count': len(nb),
                                      'disk': len(nb), 'offset': 0, 'payload': nb}}
        nn['order'] = [NAV_FORMAT]
        navbytes = uetags.write(nav['props'], pkg) + build_nav_native(nn, pkg)
        report['nav_blob'] = len(nb)
    return body, navbytes, report


def boxes_from_mesh(P, max_boxes=1):
    """UCX-style fallback: the mesh's own axis-aligned box."""
    lo, hi = np.min(P, 0), np.max(P, 0)
    return [{'center': ((lo + hi) / 2).tolist(), 'size': (hi - lo).tolist()}]


# -- the writer ---------------------------------------------------------------------------------------

def write(template, desc, new_path, materials=None, collision=None, lightmap_index=None,
          lightmap_resolution=None, out_dir=None):
    """Write `desc` as a single-LOD StaticMesh cloned from `template`.

    desc: {'positions' (N,3), 'normals' (N,3)|None, 'tangents' (N,3)|None, 'signs' (N,)|None,
           'uvs': [(N,2), ...], 'colors' (N,4) RGBA u8|None, 'triangles' (T,3) in UE winding
           (front face = (p2-p0) x (p1-p0)), 'materials' (T,) slot per triangle}
    materials: one per slot, an int (template slot to reuse) or
           {'path': '/Game/.../X_INST', 'object': 'X_INST', 'slot': 'SlotName'}.
    collision: see build_collision; None = one box around the mesh, simple-as-complex.
    Returns ({ext: bytes}, report).
    """
    t = Template(template.path, template.blobs) if isinstance(template, Template) else Template(template)
    pkg = t.pkg
    m = dict(t.model)
    nslots = len(materials) if materials is not None else len(m['materials'])
    lmi = lightmap_index
    if lmi is None:
        p = uetags.get(t.props, 'LightMapCoordinateIndex')
        lmi = p.value if p else 0
    lod, geo = build_lod(desc, t.lod0(), nslots, lmi)
    m['lods'] = [lod]
    m['inlined_lods'] = 1
    m['distance_fields'] = [None]
    m['df_strip'] = DF_STRIP
    m['occluder'] = 0
    m['sockets'] = []
    m['screen_size'] = [(1, 1.0)] + [(1, 0.0)] * (fmt.MAX_STATIC_MESH_LODS - 1)
    m['lighting_guid'] = _guid('lighting:' + new_path)
    m['bounds'] = bounds_of(geo['positions'])
    dens = uv_densities(geo['positions'].astype(np.float64), geo['indices'],
                        [u.astype(np.float64) for u in geo['uvs']], geo['sections'], nslots)

    # materials: native array + the StaticMaterials tag
    tmpl_mats = m['materials']
    mats, tag_elems = [], []
    for k in range(nslots):
        spec = materials[k] if materials is not None else k
        if isinstance(spec, int):
            obj = tmpl_mats[spec][0]
            slot = t.fname(tmpl_mats[spec][1], tmpl_mats[spec][2])
        else:
            obj, new = _material_import(pkg, spec['path'], spec['object'],
                                        spec.get('class', 'MaterialInstanceConstant'))
            if new:
                pkg.add_preload_dependency(t.index['StaticMesh'], obj, 'create_before_serialization')
            slot = spec.get('slot', spec['object'])
        slot_name = pkg.add_name(slot)
        mats.append((obj, slot_name, 0, 1, 0) + tuple(dens[k]))
        tag_elems.append(_static_material_props(obj, slot, dens[k]))
    m['materials'] = mats

    props = list(t.props)
    sm = uetags.get(props, 'StaticMaterials')
    sm.value = ('structs', 'StaticMaterial', tag_elems, sm.value[3])
    _set(props, 'LightMapCoordinateIndex', 'IntProperty', int(lmi))
    if lightmap_resolution:
        _set(props, 'LightMapResolution', 'IntProperty', int(lightmap_resolution))
    lm = uetags.get(props, 'LightmapUVDensity')
    if lm is not None:
        lm.value = float(max((d[lmi] if lmi < 4 else 0.0) for d in dens))
    eb = uetags.get(props, 'ExtendedBounds')
    eb.value = _bounds_props(m['bounds'])
    props = [p for p in props if p.name not in ('LODForCollision', 'MinLOD', 'Sockets')]

    # collision
    coll = dict(collision or {'boxes': boxes_from_mesh(geo['positions'])})
    coll['_path'] = new_path
    body, nav, creport = build_collision(t, coll, geo['positions'], geo['indices'], geo['sections'], pkg)
    pkg.exports[t.index['BodySetup']]['data'] = body
    if nav is not None:
        pkg.exports[t.index['NavCollision']]['data'] = nav

    tagged = uetags.write(props, pkg)
    native = fmt.build(m)
    pkg.exports[t.index['StaticMesh']]['data'] = tagged + native
    if new_path != t.path:
        rename(pkg, t.path, new_path)
    ua, ux = pkg.write()
    blobs = fix_bulk_offsets({'.uasset': ua, '.uexp': ux})
    report = {'path': new_path, 'vertices': int(len(geo['positions'])),
              'triangles': int(len(geo['indices']) // 3),
              'sections': [list(s[:5]) for s in geo['sections']], 'uv_channels': len(geo['uvs']),
              'index32': bool(lod['buffers']['index']['b32']), 'bounds': m['bounds'],
              'uv_densities': dens, 'collision': creport, 'export_size': len(tagged) + len(native)}
    if out_dir:
        leaf = new_path.rsplit('/', 1)[-1]
        os.makedirs(out_dir, exist_ok=True)
        for e, b in blobs.items():
            with open(os.path.join(out_dir, leaf + e), 'wb') as fh:
                fh.write(b)
    return blobs, report


def _inline_bulks(t):
    """(export index, bulk header) of every inline FByteBulkData in the subobjects."""
    out = []
    for get in (t.body_setup, t.nav_collision):
        x = get()
        if x is not None:
            out += [(x['index'], h) for h in x['native']['formats'].values() if 'payload_at' in h]
    return out


def fix_bulk_offsets(blobs):
    """Inline bulk payloads record their absolute file offset (BulkDataOffsetInFile =
    export SerialOffset + position in the export; measured on shipped BodySetup and
    NavCollision exports). Exports move when a package is rewritten, so patch them."""
    t = Template('/Game/_fix', blobs)
    hdr = len(blobs['.uasset'])
    ux = bytearray(blobs['.uexp'])
    for i, h in _inline_bulks(t):
        e = t.pkg.exports[i]
        at = e['offset'] - hdr + h['payload_at'] - 8
        struct.pack_into('<q', ux, at, e['offset'] + h['payload_at'])
    return {'.uasset': blobs['.uasset'], '.uexp': bytes(ux)}


# -- checks ---------------------------------------------------------------------------------------------

def strict_check(blobs, label='mesh'):
    """Read a written package back with the strict codec and check its derived data."""
    out = []
    try:
        t = Template('/Game/_check', blobs)
    except Exception as e:                       # noqa: BLE001
        return ['%s: does not parse: %s' % (label, e)]
    data = t.export['data']
    if data[:t.ctx['end']] + fmt.build(t.model) != data:
        out.append('%s: native block does not rebuild byte-identical' % label)
    for k, lod in enumerate(t.model['lods']):
        if 'buffers' not in lod:
            continue
        b = lod['buffers']
        if tuple(lod['buffers_size']) != fmt.buffer_sizes(b):
            out.append('%s: LOD%d BuffersSize %r != %r' % (label, k, lod['buffers_size'], fmt.buffer_sizes(b)))
        n = b['pos_meta'][1]
        I = index_array(b['index'])
        if I.max(initial=0) >= n:
            out.append('%s: index %d past %d vertices' % (label, I.max(), n))
        if b['index']['b32'] != (n - 1 > INDEX16_MAX):
            out.append('%s: index width disagrees with the vertex count' % label)
        for s in lod['sections']:
            seg = I[s[1]:s[1] + 3 * s[2]]
            if len(seg) and (seg.min() < s[3] or seg.max() > s[4]):
                out.append('%s: section %r indices outside its vertex range' % (label, s[:5]))
            if s[0] >= len(t.model['materials']):
                out.append('%s: section material %d past %d slots' % (label, s[0], len(t.model['materials'])))
        if len(index_array(b['adjacency'])) != 4 * len(I):
            out.append('%s: adjacency is not 12 per triangle' % label)
        if not np.array_equal(index_array(b['reversed']), reversed_indices(I, lod['sections'])):
            out.append('%s: reversed index buffer stale' % label)
        if b['vb_meta'][1] != n or b['texcoords'][1] != n * b['vb_meta'][0]:
            out.append('%s: vertex buffer counts disagree' % label)
        P = np.frombuffer(b['positions'][2], '<f4').reshape(-1, 3)
        if k == 0 and not np.allclose(bounds_of(P), t.model['bounds'], atol=1e-3):
            out.append('%s: bounds are not the vertex box' % label)
    sm = uetags.get(t.props, 'StaticMaterials')
    if sm and len(sm.value[2]) != len(t.model['materials']):
        out.append('%s: StaticMaterials tag has %d slots, native %d' % (label, len(sm.value[2]), len(t.model['materials'])))
    for i, h in _inline_bulks(t):
        if h['offset'] != t.pkg.exports[i]['offset'] + h['payload_at']:
            out.append('%s: export %d bulk OffsetInFile %d, payload at %d'
                       % (label, i, h['offset'], t.pkg.exports[i]['offset'] + h['payload_at']))
    bs = t.body_setup()
    if bs is not None:
        from riftlord.formats.omd3 import omd3physx
        blob = bs['blob']
        if blob is None:
            out.append('%s: BodySetup has no %s data' % (label, omd3physx.FORMAT))
        else:
            info = omd3physx.parse_blob(blob)
            if info['convex_streams'] < info['convex'] + info['mirrored'] or info['mesh_streams'] < info['trimesh']:
                out.append('%s: PhysX blob counts disagree with its streams: %r' % (label, info))
    return out


def imports_resolve(blobs, ours=None):
    """Every import must name an export that exists: in `ours` ({/Game path: blobs}) or in
    the installed game. Returns problems."""
    pkg = Package(blobs['.uasset'], blobs['.uexp'])
    nm = [n for n, _h in pkg.names]
    probs = []
    for r in pkg.imports:
        _cp, _n1, cls, _n2, outer, obj, _n3 = struct.unpack('<7i', r)
        if outer >= 0:
            continue
        o = struct.unpack('<7i', pkg.imports[-outer - 1])
        target = nm[o[5]]
        if nm[o[2]] != 'Package' or target.startswith('/Script/'):
            continue
        if ours and target in ours:
            tb = ours[target]
        else:
            try:
                ua, ux = gamefiles.package(target)
            except KeyError:
                probs.append('import %s.%s: no such package in the game' % (target, nm[obj]))
                continue
            tb = {'.uasset': ua, '.uexp': ux}
        tp = Package(tb['.uasset'])
        tn = [n for n, _h in tp.names]
        exports = {}
        for e in tp.exports:
            ci = struct.unpack_from('<i', e['meta'], 0)[0]
            cname = tn[struct.unpack('<7i', tp.imports[-ci - 1])[5]] if ci < 0 else None
            exports[tn[struct.unpack_from('<i', e['meta'], 16)[0]]] = cname
        if nm[obj] not in exports:
            probs.append('import %s.%s: the package has no such export' % (target, nm[obj]))
        elif exports[nm[obj]] != nm[cls]:
            probs.append('import %s.%s: class %s, the export is %s' % (target, nm[obj], nm[cls], exports[nm[obj]]))
    return probs


def pkgcheck_problems(blobs, label='mesh'):
    from riftlord.core import pkgcheck
    return pkgcheck.problems(blobs['.uasset'], blobs['.uexp'], label)


# -- CLI ----------------------------------------------------------------------------------------------

def identity(paths=None, workers=16):
    """Byte-identical round trip (strict parse + rebuild) of every shipped static mesh."""
    from multiprocessing import Pool
    paths = paths or all_static_meshes()
    with Pool(workers) as pool:
        res = pool.map(_identity_one, paths, chunksize=16)
    bad = [r for r in res if r[1] is not True]
    for p, why in bad[:20]:
        print('FAIL', p, why)
    print('byte-identical: %d/%d' % (len(res) - len(bad), len(res)))
    return len(res) - len(bad), len(res)


def _identity_one(p):
    try:
        ua, ux = gamefiles.read(p + '.uasset'), gamefiles.read(p + '.uexp')
        t = Template(p, {'.uasset': ua, '.uexp': ux})
        d = t.export['data']
        if d[:t.ctx['end']] + fmt.build(t.model) != d or t.model['rest']:
            return p, 'native differs'
        for i, e in enumerate(t.pkg.exports):
            props, end = uetags.read(e['data'], t.names)
            if uetags.write(props, t.pkg) != e['data'][:end]:
                return p, 'tags of export %d differ' % i
        bs = t.body_setup()
        if bs:
            data = t.pkg.exports[bs['index']]['data']
            if data[:bs['end']] + build_body_native(bs['native'], t.pkg) != data:
                return p, 'BodySetup native differs'
        nav = t.nav_collision()
        if nav:
            data = t.pkg.exports[nav['index']]['data']
            if data[:nav['end']] + build_nav_native(nav['native'], t.pkg) != data:
                return p, 'NavCollision native differs'
        ua2, ux2 = t.pkg.write()
        if (ua2, ux2) != (ua, ux):
            return p, 'package differs'
        return p, True
    except Exception as e:                       # noqa: BLE001
        return p, 'error %s' % e


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('identity')
    rw = sub.add_parser('rewrite')
    rw.add_argument('mesh')
    rw.add_argument('--path')
    rw.add_argument('--out', required=True)
    rw.add_argument('--collision', help='JSON collision spec (default: one box, simple-as-complex)')
    a = ap.parse_args(argv)
    if a.cmd == 'identity':
        ok, n = identity()
        return 0 if ok == n else 1
    src = a.mesh if a.mesh.startswith('/') else next(
        '/Game' + p[len('../../../OMD/Content'):] for p in all_static_meshes()
        if p.rsplit('/', 1)[-1] == a.mesh)
    t = Template(src)
    desc = decode(t)
    path = a.path or '/Game/Mods/SMWrite/%s_W' % t.object
    blobs, rep = write(Template(), desc, path, materials=[
        {'path': p, 'object': o, 'slot': s, 'class': c} for p, o, s, c in t.material_paths()],
        collision=a.collision and json.loads(a.collision), out_dir=a.out)
    print(json.dumps({k: v for k, v in rep.items() if k != 'uv_densities'}, indent=1, default=str))
    probs = strict_check(blobs) + pkgcheck_problems(blobs) + imports_resolve(blobs)
    print('\n'.join(probs) or 'checks clean')
    return 1 if probs else 0


if __name__ == '__main__':
    raise SystemExit(main())
