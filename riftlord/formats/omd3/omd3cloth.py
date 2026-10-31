"""Read and write OMD3's cooked clothing assets: UClothingAssetCommon and UClothConfigNv.

Both are plain tagged-property objects (ClothingAsset.h, ClothConfigNv.h) with one
exception: every FClothLODDataCommon in `LodData` is WithSerializer, so after its tagged
properties come two native arrays, TransitionUpSkinData and TransitionDownSkinData
(TArray<FMeshToMeshVertData>, 64 bytes each; ClothLODData.cpp). That trailer is what made
bpkit's generic reader fail inside LodData.

The codec keeps every FName as text (`FN`), so a decoded asset can be encoded into a
different package's name table; object references stay package indices (`Ref`) and are
remapped by the caller. Fixed-width arrays (floats, uint32 indices, vectors) stay one
`Pod` blob, so the 2 MB skirts decode quickly.

    asset = read_package(uasset, uexp)          # every cloth export of a SkeletalMesh package
    lod = asset.exports[i].lod(0)               # numpy views of the sim mesh, maps, collision
    python3 omd3cloth.py identity               # every shipped cloth asset, byte-identical

The writer side (new assets from a sim mesh) is `omd3clothsim.py`; binding to a skeletal
mesh is the `cloth` option of omd3skmwrite.write.
"""

import struct
import sys
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

#: Structs the engine serialises natively inside a tagged stream, and their sizes.
NATIVE = {'Vector': 12, 'Vector2D': 8, 'Vector4': 16, 'Rotator': 12, 'Quat': 16,
          'LinearColor': 16, 'Color': 4, 'Guid': 16, 'IntPoint': 8, 'IntVector': 12,
          'Box': 25, 'Plane': 16, 'Matrix': 64}
#: Property types whose value is a fixed number of bytes.
FIXED = {'IntProperty': 4, 'FloatProperty': 4, 'DoubleProperty': 8, 'Int8Property': 1,
         'Int16Property': 2, 'Int64Property': 8, 'UInt16Property': 2,
         'UInt32Property': 4, 'UInt64Property': 8}
#: WithSerializer structs: tagged properties, then a native trailer.
#: ClothLODDataCommon: TransitionUpSkinData, TransitionDownSkinData (TArray of 64-byte elements).
TRAILERS = {'ClothLODDataCommon': (64, 64)}
#: EWeightMapTargetCommon (PointWeightMap.h): the WeightMaps keys.
WEIGHT_MAP = {'MaxDistance': 1, 'BackstopDistance': 2, 'BackstopRadius': 3,
              'AnimDriveStiffness': 4, 'AnimDriveDamping': 5}
#: FClothVertBoneData::MaxTotalInfluences: BoneIndices/BoneWeights are static arrays of 12.
MAX_INFLUENCES = 12


class Bad(ValueError):
    """The bytes do not parse as the serialiser wrote them."""


class FN(tuple):
    """An FName as (text, number)."""

    def __new__(cls, text, number=0):
        return tuple.__new__(cls, (text, number))

    def __getnewargs__(self):
        return (self[0], self[1])

    @property
    def text(self):
        return self[0]

    def __str__(self):
        return self[0] if self[1] == 0 else '%s_%d' % (self[0], self[1] - 1)


class Ref(int):
    """A package index (negative import, positive export, 0 null)."""


class Pod:
    """A TArray of fixed-width elements kept as one blob."""

    __slots__ = ('type', 'count', 'data')

    def __init__(self, type_, count, data):
        self.type, self.count, self.data = type_, count, data

    def __eq__(self, o):
        return isinstance(o, Pod) and (self.type, self.count, self.data) == (o.type, o.count, o.data)


class Native:
    """A natively serialised struct value (Vector, Plane, Guid, ...)."""

    __slots__ = ('struct', 'data')

    def __init__(self, struct_name, data):
        self.struct, self.data = struct_name, data

    def __eq__(self, o):
        return isinstance(o, Native) and (self.struct, self.data) == (o.struct, o.data)


class Struct:
    """A tagged struct value; `trailer` holds a WithSerializer struct's native tail."""

    __slots__ = ('props', 'trailer')

    def __init__(self, props, trailer=None):
        self.props, self.trailer = props, trailer

    def __eq__(self, o):
        return isinstance(o, Struct) and self.props == o.props and self.trailer == o.trailer

    def get(self, name, default=None):
        for p in self.props:
            if p.name.text == name and p.index == 0:
                return p
        return default

    def all(self, name):
        return [p for p in self.props if p.name.text == name]


class Array:
    """A TArray of non-fixed elements. A struct array keeps its inner tag."""

    __slots__ = ('items', 'inner')

    def __init__(self, items, inner=None):
        self.items, self.inner = items, inner

    def __eq__(self, o):
        return isinstance(o, Array) and self.items == o.items and self.inner == o.inner


class Map:
    __slots__ = ('removed', 'entries')

    def __init__(self, removed, entries):
        self.removed, self.entries = removed, entries

    def __eq__(self, o):
        return isinstance(o, Map) and self.removed == o.removed and self.entries == o.entries


class Str:
    """An FString as stored."""

    __slots__ = ('data',)

    def __init__(self, data):
        self.data = data

    def __eq__(self, o):
        return isinstance(o, Str) and self.data == o.data


class Prop:
    """One FPropertyTag and its value. `extra`: struct_name, struct_guid, bool_val,
    enum_name, inner_type, value_type (PropertyTag.cpp)."""

    __slots__ = ('name', 'type', 'index', 'extra', 'guid', 'value')

    def __init__(self, name, type_, index=0, extra=None, guid=None, value=None):
        self.name, self.type, self.index = name, type_, index
        self.extra, self.guid, self.value = extra or {}, guid, value

    def __eq__(self, o):
        return isinstance(o, Prop) and all(getattr(self, k) == getattr(o, k) for k in self.__slots__)

    def __repr__(self):
        return 'Prop(%s %s[%d])' % (self.name, self.type, self.index)


# -- decoding ---------------------------------------------------------------------

class _R:
    def __init__(self, data, names, o=0, end=None):
        self.d, self.o, self.names = data, o, names
        self.end = len(data) if end is None else end

    def take(self, n):
        if n < 0 or self.o + n > self.end:
            raise Bad('read of %d at %d past %d' % (n, self.o, self.end))
        v = self.d[self.o:self.o + n]
        self.o += n
        return bytes(v)

    def i32(self):
        return struct.unpack('<i', self.take(4))[0]

    def fname(self):
        i, n = struct.unpack('<ii', self.take(8))
        if not 0 <= i < len(self.names):
            raise Bad('name index %d at %d' % (i, self.o - 8))
        return FN(self.names[i], n)

    def fstring(self):
        at = self.o
        n = self.i32()
        self.take(-2 * n if n < 0 else n)
        return Str(self.d[at:self.o])


def decode_props(r):
    out = []
    while True:
        name = r.fname()
        if name == ('None', 0):
            return out
        t = r.fname()
        size, index = struct.unpack('<ii', r.take(8))
        x = {}
        if t.text == 'StructProperty':
            x['struct_name'] = r.fname()
            x['struct_guid'] = r.take(16)
        elif t.text == 'BoolProperty':
            x['bool_val'] = r.take(1)[0]
        elif t.text in ('ByteProperty', 'EnumProperty'):
            x['enum_name'] = r.fname()
        elif t.text in ('ArrayProperty', 'SetProperty'):
            x['inner_type'] = r.fname()
        elif t.text == 'MapProperty':
            x['inner_type'] = r.fname()
            x['value_type'] = r.fname()
        guid = r.take(16) if r.take(1)[0] else None
        start = r.o
        sub = _R(r.d, r.names, start, start + size)
        try:
            v = _value(sub, t.text, size, x)
        except (Bad, struct.error) as ex:
            raise Bad('%s > %s' % (name, ex)) from None
        if sub.o != start + size:
            raise Bad('%s (%s) read %d of %d bytes' % (name, t.text, sub.o - start, size))
        r.o = start + size
        out.append(Prop(name, t, index, x, guid, v))


def _value(r, t, size, x):
    if t == 'BoolProperty':
        return None
    if t in FIXED:
        return r.take(FIXED[t])
    if t == 'ByteProperty':
        return r.take(1) if size == 1 else r.fname()
    if t in ('EnumProperty', 'NameProperty'):
        return r.fname()
    if t == 'StrProperty':
        return r.fstring()
    if t in ('ObjectProperty', 'ClassProperty', 'WeakObjectProperty', 'LazyObjectProperty',
             'InterfaceProperty'):
        return Ref(r.i32())
    if t == 'SoftObjectProperty':
        return (r.fname(), r.fstring())
    if t == 'StructProperty':
        return _struct(r, x['struct_name'].text)
    if t == 'ArrayProperty':
        return _array(r, x['inner_type'].text, size)
    if t == 'MapProperty':
        kt, vt = x['inner_type'].text, x['value_type'].text
        removed = [_element(r, kt) for _ in range(r.i32())]
        entries = [(_element(r, kt), _element(r, vt)) for _ in range(r.i32())]
        return Map(removed, entries)
    raise Bad('no rule for %s' % t)


def _struct(r, sname):
    if sname in NATIVE:
        return Native(sname, r.take(NATIVE[sname]))
    props = decode_props(r)
    trailer = None
    if sname in TRAILERS:
        trailer = []
        for width in TRAILERS[sname]:
            n = r.i32()
            if n < 0:
                raise Bad('%s trailer count %d' % (sname, n))
            trailer.append((n, r.take(n * width)))
    return Struct(props, trailer)


def _element(r, t):
    if t == 'StructProperty':
        return Struct(decode_props(r))
    if t == 'BoolProperty':
        return r.take(1)
    return _value(r, t, None, {})


def _array(r, inner, size):
    n = r.i32()
    if inner in FIXED:
        return Pod(inner, n, r.take(n * FIXED[inner]))
    if inner != 'StructProperty':
        if inner == 'ByteProperty' and n and (size - 4) // n == 1:
            return Pod(inner, n, r.take(n))
        return Array([_element(r, inner) for _ in range(n)])
    tag = {'name': r.fname(), 'type': r.fname()}
    esize, tag['index'] = struct.unpack('<ii', r.take(8))
    tag['struct_name'] = r.fname()
    tag['struct_guid'] = r.take(16)
    tag['guid'] = r.take(16) if r.take(1)[0] else None
    sname = tag['struct_name'].text
    at = r.o
    if sname in NATIVE:
        # an array of natively serialised structs (TArray<FVector>): one blob
        data = r.take(n * NATIVE[sname])
        items = Pod('Struct:' + sname, n, data)
    else:
        items = [_struct(r, sname) for _ in range(n)]
    if r.o - at != esize:
        raise Bad('struct array %s: elements took %d, inner tag says %d' % (sname, r.o - at, esize))
    return Array(items, tag)


def decode(data, names):
    """(props, tail) of an export payload."""
    r = _R(data, names)
    props = decode_props(r)
    return props, data[r.o:]


# -- encoding ---------------------------------------------------------------------

class _W:
    def __init__(self, name_index):
        self.b = bytearray()
        self.ni = name_index

    def fname(self, n):
        self.b += struct.pack('<ii', self.ni(n[0]), n[1])


def encode_props(props, w):
    for p in props:
        body = _W(w.ni)
        _encode(p.value, body)
        w.fname(p.name)
        w.fname(p.type)
        w.b += struct.pack('<ii', len(body.b), p.index)
        x = p.extra
        if 'struct_name' in x:
            w.fname(x['struct_name'])
            w.b += x['struct_guid']
        if 'bool_val' in x:
            w.b.append(x['bool_val'])
        if 'enum_name' in x:
            w.fname(x['enum_name'])
        if 'inner_type' in x:
            w.fname(x['inner_type'])
        if 'value_type' in x:
            w.fname(x['value_type'])
        w.b += (b'\x01' + p.guid) if p.guid is not None else b'\x00'
        w.b += body.b
    w.fname(FN('None'))


def _encode(v, w):
    if v is None:
        return
    if isinstance(v, (bytes, bytearray)):
        w.b += v
    elif isinstance(v, FN):
        w.fname(v)
    elif isinstance(v, Ref):
        w.b += struct.pack('<i', int(v))
    elif isinstance(v, Str):
        w.b += v.data
    elif isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], FN):   # soft object path
        w.fname(v[0])
        w.b += v[1].data
    elif isinstance(v, Native):
        w.b += v.data
    elif isinstance(v, Pod):
        w.b += struct.pack('<i', v.count) + v.data
    elif isinstance(v, Struct):
        encode_props(v.props, w)
        for n, data in v.trailer or ():
            w.b += struct.pack('<i', n) + data
    elif isinstance(v, Map):
        w.b += struct.pack('<i', len(v.removed))
        for k in v.removed:
            _encode(k, w)
        w.b += struct.pack('<i', len(v.entries))
        for k, val in v.entries:
            _encode(k, w)
            _encode(val, w)
    elif isinstance(v, Array):
        if v.inner is None:
            w.b += struct.pack('<i', len(v.items))
            for item in v.items:
                _encode(item, w)
            return
        tag = v.inner
        if isinstance(v.items, Pod):
            count, elems = v.items.count, v.items.data
        else:
            sub = _W(w.ni)
            for item in v.items:
                _encode(item, sub)
            count, elems = len(v.items), bytes(sub.b)
        w.b += struct.pack('<i', count)
        w.fname(tag['name'])
        w.fname(tag['type'])
        w.b += struct.pack('<ii', len(elems), tag['index'])
        w.fname(tag['struct_name'])
        w.b += tag['struct_guid']
        w.b += (b'\x01' + tag['guid']) if tag['guid'] is not None else b'\x00'
        w.b += elems
    else:
        raise TypeError('cannot encode %r' % (v,))


def encode(props, tail, name_index):
    """Payload bytes; name_index(text) -> index in the target package's name table."""
    w = _W(name_index)
    encode_props(props, w)
    return bytes(w.b) + tail


def name_indexer(pkg):
    """name_index for a uassetio.Package: existing names by text, new ones appended."""
    at = {}
    for i, (s, _h) in enumerate(pkg.names):
        at.setdefault(s, i)

    def ni(text):
        if text not in at:
            at[text] = pkg.add_name(text)
        return at[text]
    return ni


# -- typed views ------------------------------------------------------------------

def vec_array(prop):
    """(N,3) float32 from a TArray<FVector> property."""
    pod = prop.value.items
    return np.frombuffer(pod.data, '<f4').reshape(-1, 3)


def pod_array(prop, dtype):
    return np.frombuffer(prop.value.data, dtype)


def scalar(prop, fmt):
    return struct.unpack('<' + fmt, prop.value)[0]


class ClothLOD:
    """Numpy views of one FClothLODDataCommon (read-only; for inspection and checks)."""

    def __init__(self, s):
        self.struct = s
        pm = s.get('PhysicalMeshData').value
        self.pm = pm
        self.vertices = vec_array(pm.get('Vertices'))
        self.normals = vec_array(pm.get('Normals'))
        self.indices = pod_array(pm.get('Indices'), '<u4')
        self.inverse_masses = pod_array(pm.get('InverseMasses'), '<f4') if pm.get('InverseMasses') else None
        self.maps = {}
        wm = pm.get('WeightMaps')
        if wm:
            for k, v in wm.value.entries:
                key = struct.unpack('<I', k)[0]
                vals = v.get('Values')
                self.maps[key] = pod_array(vals, '<f4') if vals else np.zeros(0, np.float32)
        self.bone_data = []
        bd = pm.get('BoneData')
        if bd:
            for e in bd.value.items:
                n = e.get('NumInfluences')
                idx = [0xffff] * MAX_INFLUENCES
                wt = [0.0] * MAX_INFLUENCES
                for p in e.all('BoneIndices'):
                    idx[p.index] = struct.unpack('<H', p.value)[0]
                for p in e.all('BoneWeights'):
                    wt[p.index] = struct.unpack('<f', p.value)[0]
                self.bone_data.append((scalar(n, 'i') if n else 0, idx, wt))
        self.collision = s.get('CollisionData').value if s.get('CollisionData') else None
        self.trailer = s.trailer


class ClothExport:
    def __init__(self, index, cls, name, props, tail):
        self.index, self.cls, self.name = index, cls, name
        self.props, self.tail = props, tail

    def get(self, name):
        for p in self.props:
            if p.name.text == name and p.index == 0:
                return p
        return None

    def lods(self):
        p = self.get('LodData')
        return [ClothLOD(s) for s in p.value.items] if p else []

    def guid(self):
        p = self.get('AssetGuid')
        return p.value.data if p else None


class ClothPackage:
    """Every ClothingAssetCommon / ClothConfigNv export in a package, decoded."""

    CLASSES = ('ClothingAssetCommon', 'ClothConfigNv')

    def __init__(self, uasset, uexp):
        from riftlord.formats.ue.uassetio import Package
        self.pkg = Package(uasset, uexp)
        self.names = [n for n, _h in self.pkg.names]
        self.exports = []
        for i, e in enumerate(self.pkg.exports):
            cls = self.class_name(e)
            if cls in self.CLASSES:
                props, tail = decode(e['data'], self.names)
                self.exports.append(ClothExport(i, cls, self.object_name(e), props, tail))

    def class_name(self, e):
        ci = struct.unpack_from('<i', e['meta'], 0)[0]
        if ci >= 0:
            return None
        return self.names[struct.unpack('<7i', self.pkg.imports[-ci - 1])[5]]

    def object_name(self, e):
        i, n = struct.unpack_from('<ii', e['meta'], 16)
        return str(FN(self.names[i], n))

    def assets(self):
        return [x for x in self.exports if x.cls == 'ClothingAssetCommon']

    def configs(self):
        return [x for x in self.exports if x.cls == 'ClothConfigNv']

    def rebuild(self):
        """Encode every decoded export back into the package; (uasset, uexp)."""
        ni = name_indexer(self.pkg)
        for x in self.exports:
            self.pkg.exports[x.index]['data'] = encode(x.props, x.tail, ni)
        return self.pkg.write()


def read_package(uasset, uexp):
    return ClothPackage(uasset, uexp)


# -- mesh-to-mesh skinning (ClothingMeshUtils.cpp) ------------------------------------

#: FMeshToMeshVertData: three float4 (position, normal-end, tangent-end bary + distance),
#: four uint16 (sim triangle, then 0 or 0xFFFF for "skinned only"), float weight, uint32 pad.
MESH_TO_MESH = np.dtype([('pos', '<f4', 4), ('nrm', '<f4', 4), ('tan', '<f4', 4),
                         ('idx', '<u2', 4), ('weight', '<f4'), ('pad', '<u4')])
#: FPointWeightMap::AreAllBelowThreshold default: sim triangles whose three MaxDistance values
#: are all at or below this are skinned only (SourceMeshVertIndices[3] = 0xFFFF).
FIXED_THRESHOLD = 0.1
#: UE's SMALL_NUMBER: degenerate triangles; kinematic particles (MaxDistance < this) get
#: InverseMass 0 (UClothingAssetCommon::CalculateInverseMasses keeps SMALL_NUMBER there).
SMALL_NUMBER = 1e-8


def _closest_on_triangles(p, A, B, C):
    """Squared distance from p to each triangle (FMath::ClosestPointOnTriangleToPoint)."""
    ab, ac, ap = B - A, C - A, p - A
    d1, d2 = (ab * ap).sum(1), (ac * ap).sum(1)
    bp = p - B
    d3, d4 = (ab * bp).sum(1), (ac * bp).sum(1)
    cp = p - C
    d5, d6 = (ab * cp).sum(1), (ac * cp).sum(1)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    denom = va + vb + vc
    with np.errstate(divide='ignore', invalid='ignore'):
        v = vb / denom
        w = vc / denom
    q = A + ab * v[:, None] + ac * w[:, None]
    # region tests, in the order of the reference implementation
    cases = [
        ((d1 <= 0) & (d2 <= 0), A),
        ((d3 >= 0) & (d4 <= d3), B),
        ((d6 >= 0) & (d5 <= d6), C),
    ]
    with np.errstate(divide='ignore', invalid='ignore'):
        e_ab = A + ab * (d1 / (d1 - d3))[:, None]
        e_ac = A + ac * (d2 / (d2 - d6))[:, None]
        e_bc = B + (C - B) * ((d4 - d3) / ((d4 - d3) + (d5 - d6)))[:, None]
    edge = [
        ((vc <= 0) & (d1 >= 0) & (d3 <= 0), e_ab),
        ((vb <= 0) & (d2 >= 0) & (d6 <= 0), e_ac),
        ((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), e_bc),
    ]
    done = np.zeros(len(A), bool)
    out = q.copy()
    for mask, val in cases + edge:
        m = mask & ~done
        out[m] = val[m]
        done |= m
    return ((out - p) ** 2).sum(1)


def bary_and_dist(A, B, C, point):
    """GetPointBaryAndDist: barycentrics of the point projected on the triangle plane, and its
    signed distance from FPlane(A, B, C) (normal (B-A)x(C-A), normalised)."""
    n = np.cross(B - A, C - A)
    n = n / np.linalg.norm(n)
    dist = float(np.dot(point, n) - np.dot(A, n))
    q = point - dist * n
    # FMath::ComputeBaryCentric2D
    tri = np.cross(B - A, C - A)
    nn = tri / np.linalg.norm(tri)
    area = np.dot(tri, nn)
    a = np.dot(np.cross(B - q, C - q), nn) / area
    b = np.dot(np.cross(C - q, A - q), nn) / area
    return (a, b, 1.0 - a - b, dist)


def mesh_to_mesh(rpos, rnrm, rtan, V, I, max_distance=None):
    """FMeshToMeshVertData per render vertex against the sim mesh (single influence, as
    UClothingAssetCommon::BindToSkeletalMesh with bUseMultipleInfluences off).
    rpos/rnrm/rtan: render vertices (component space, bind pose); V, I: the sim mesh."""
    rpos = np.asarray(rpos, np.float64)
    V = np.asarray(V, np.float64)
    T = np.asarray(I, np.int64).reshape(-1, 3)
    A, B, C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    if (np.linalg.norm(np.cross(B - A, C - A), axis=1) ** 2 < SMALL_NUMBER).any():
        raise ValueError('the sim mesh has a degenerate triangle')
    out = np.zeros(len(rpos), MESH_TO_MESH)
    md = None if max_distance is None else np.asarray(max_distance, np.float64)
    for k, p in enumerate(rpos):
        t = int(np.argmin(_closest_on_triangles(p, A, B, C)))
        a, b, c = A[t], B[t], C[t]
        out['pos'][k] = bary_and_dist(a, b, c, p)
        out['nrm'][k] = bary_and_dist(a, b, c, p + rnrm[k])
        out['tan'][k] = bary_and_dist(a, b, c, p + rtan[k])
        ia, ib, ic = T[t]
        fixed = md is not None and len(md) and max(md[ia], md[ib], md[ic]) <= FIXED_THRESHOLD
        out['idx'][k] = (ia, ib, ic, 0xFFFF if fixed else 0)
        out['weight'][k] = 1.0
    return out


def inverse_masses(V, I, max_distance):
    """(InverseMasses, NumFixedVerts) as UClothingAssetCommon::CalculateInverseMasses builds them."""
    V = np.asarray(V, np.float32)
    T = np.asarray(I, np.int64).reshape(-1, 3)
    inv = np.zeros(len(V), np.float32)
    area = np.linalg.norm(np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]]), axis=1).astype(np.float32)
    for c in range(3):
        np.add.at(inv, T[:, c], area)
    md = np.asarray(max_distance, np.float32)
    fixed = md < SMALL_NUMBER if len(md) else np.zeros(len(V), bool)
    inv[fixed] = 0.0
    mass_sum = np.float32(inv.sum(dtype=np.float32))
    if mass_sum > 0:
        scale = np.float32(len(V) - int(fixed.sum())) / mass_sum
        nz = inv != 0
        inv[nz] = np.float32(1.0) / (inv[nz] * scale)
    return inv, int(fixed.sum())


def self_collision_indices(V, max_distance, radius):
    """FClothPhysicalMeshData::BuildSelfCollisionData: free particles, culled so none of
    those kept lies within `radius` of an earlier one in the rest pose."""
    if radius <= 0:
        return []
    V = np.asarray(V, np.float32)
    md = np.asarray(max_distance, np.float32)
    keep = [i for i in range(len(V)) if not (i < len(md) and md[i] <= FIXED_THRESHOLD)]
    r2 = np.float32(radius) * np.float32(radius)
    gone = set()
    for a in range(len(keep)):
        if keep[a] in gone:
            continue
        for b in range(a + 1, len(keep)):
            if keep[b] in gone:
                continue
            d = V[keep[b]] - V[keep[a]]
            if np.float32((d * d).sum()) < r2:
                gone.add(keep[b])
    return [i for i in keep if i not in gone]


# -- writing new assets ------------------------------------------------------------

#: Where a ClothConfigNv is copied from when a spec names none (the user's tip: Vorwick's
#: Desert Nomad belt flaps are the closest shipped analogue of ribbons hanging from a belt).
DEFAULT_CONFIG_SOURCE = ('/Game/Characters/Heroes/Vorwick/Mesh/Vanity/Sand/Vorwick_Sand_SKM',
                         'Vorwick_Sand_Flaps')
#: EObjectFlags of a shipped cloth subobject export (RF_Transactional).
SUBOBJECT_FLAGS = 0x8
#: FObjectExport tail of a shipped cloth export: bNotAlwaysLoadedForEditorGame 1, bIsAsset 0.
NOT_ALWAYS_LOADED_FOR_EDITOR_GAME = 1
ZERO_GUID = b'\0' * 16
INDEX_NONE = 0xFFFF


def _p(name, type_, value, index=0, **extra):
    return Prop(FN(name), FN(type_), index, extra, None, value)


def _p_int(name, v, index=0):
    return _p(name, 'IntProperty', struct.pack('<i', int(v)), index)


def _p_float(name, v, index=0):
    return _p(name, 'FloatProperty', struct.pack('<f', float(v)), index)


def _p_vector(name, v):
    return _p(name, 'StructProperty', Native('Vector', struct.pack('<3f', *map(float, v))),
              struct_name=FN('Vector'), struct_guid=ZERO_GUID)


def _p_plane(name, v):
    return _p(name, 'StructProperty', Native('Plane', struct.pack('<4f', *map(float, v))),
              struct_name=FN('Plane'), struct_guid=ZERO_GUID)


def _inner(name, struct_name):
    return {'name': FN(name), 'type': FN('StructProperty'), 'index': 0,
            'struct_name': FN(struct_name), 'struct_guid': ZERO_GUID, 'guid': None}


def _p_struct_array(name, struct_name, items):
    return _p(name, 'ArrayProperty', Array(items, _inner(name, struct_name)),
              inner_type=FN('StructProperty'))


def _vec_pod(arr):
    a = np.ascontiguousarray(np.asarray(arr, '<f4').reshape(-1, 3))
    return Pod('Struct:Vector', len(a), a.tobytes())


def _set(struct_or_props, name, value):
    props = struct_or_props.props if isinstance(struct_or_props, Struct) else struct_or_props
    for p in props:
        if p.name.text == name and p.index == 0:
            p.value = value
            return p
    raise KeyError(name)


def bone_data_items(influences, weights):
    """FClothVertBoneData elements, every field written (as the cooker writes array elements)."""
    items = []
    for inf, wt in zip(influences, weights):
        pairs = [(int(b), float(w)) for b, w in zip(inf, wt) if w > 0]
        if len(pairs) > MAX_INFLUENCES:
            raise ValueError('a sim vertex has %d influences; the limit is %d' % (len(pairs), MAX_INFLUENCES))
        props = [_p_int('NumInfluences', len(pairs))]
        for k in range(MAX_INFLUENCES):
            props.append(_p('BoneIndices', 'UInt16Property',
                            struct.pack('<H', pairs[k][0] if k < len(pairs) else INDEX_NONE), k))
        for k in range(MAX_INFLUENCES):
            props.append(_p_float('BoneWeights', pairs[k][1] if k < len(pairs) else 0.0, k))
        items.append(Struct(props))
    return items


def collision_props(template_collision, collision, bone_slot):
    """FClothCollisionData with spheres, capsules (sphere connections) and convexes.
    collision: {'spheres': [(bone name, radius, (x, y, z))], 'capsules': [(i, j)],
    'convexes': [{'planes': [(nx, ny, nz, w)], 'points': [(x, y, z)], 'bone': name}]}
    with positions in the named bone's space and planes in component space (NvCloth ignores a
    convex's bone: ClothingSimulationNv.cpp transforms planes by the root bone only)."""
    s = Struct([Prop(p.name, p.type, p.index, dict(p.extra), p.guid, p.value) for p in template_collision.props])
    spheres = [Struct([_p_int('BoneIndex', bone_slot(b)), _p_float('Radius', r), _p_vector('LocalPosition', pos)])
               for b, r, pos in collision.get('spheres', ())]
    caps = [Struct([_p_int('SphereIndices', i, 0), _p_int('SphereIndices', j, 1)])
            for i, j in collision.get('capsules', ())]
    convexes = []
    for c in collision.get('convexes', ()):
        faces = [Struct([_p_plane('Plane', pl),
                         _p('Indices', 'ArrayProperty', Pod('IntProperty', 0, b''), inner_type=FN('IntProperty'))])
                 for pl in c['planes']]
        convexes.append(Struct([
            _p_struct_array('Faces', 'ClothCollisionPrim_ConvexFace', faces),
            _p('SurfacePoints', 'ArrayProperty', Array(_vec_pod(c.get('points', np.zeros((0, 3)))), _inner('SurfacePoints', 'Vector')),
               inner_type=FN('StructProperty')),
            _p_int('BoneIndex', bone_slot(c['bone']) if c.get('bone') else -1)]))
    for name, items in (('Spheres', spheres), ('SphereConnections', caps), ('Convexes', convexes)):
        p = s.get(name)
        p.value = Array(items, dict(p.value.inner))
    return s


def asset_props(template, spec, config_ref):
    """Tagged properties of a new UClothingAssetCommon, shaped like `template` (a shipped
    ClothExport): one LOD, no physics asset, the spec's sim mesh, maps and collision.

    spec: name, guid (16 bytes), vertices, normals, indices (UE winding: the normal follows
    (c-a)x(b-a)), bones (UsedBoneNames), bone_indices (UsedBoneIndices in the mesh),
    influences/weights (per sim vertex, into `bones`), max_distance, backstop_distance,
    backstop_radius, self_collision_radius, collision (see collision_props)."""
    import copy
    lod_t = template.get('LodData').value.items[0]
    lod = copy.deepcopy(lod_t)
    pm = lod.get('PhysicalMeshData').value
    V = np.asarray(spec['vertices'], np.float32)
    n = len(V)
    md = np.asarray(spec['max_distance'], np.float32)
    inv, fixed = inverse_masses(V, spec['indices'], md)
    _set(pm, 'Vertices', Array(_vec_pod(V), dict(pm.get('Vertices').value.inner)))
    _set(pm, 'Normals', Array(_vec_pod(spec['normals']), dict(pm.get('Normals').value.inner)))
    idx = np.asarray(spec['indices'], '<u4').ravel()
    _set(pm, 'Indices', Pod('UInt32Property', len(idx), idx.tobytes()))
    maps = {WEIGHT_MAP['MaxDistance']: md,
            WEIGHT_MAP['BackstopDistance']: np.asarray(spec['backstop_distance'], np.float32),
            WEIGHT_MAP['BackstopRadius']: np.asarray(spec['backstop_radius'], np.float32),
            WEIGHT_MAP['AnimDriveStiffness']: np.zeros(0, np.float32)}
    wm = pm.get('WeightMaps').value
    entries = []
    for key, val in wm.entries:
        k = struct.unpack('<I', key)[0]
        v = copy.deepcopy(val)
        values = np.ascontiguousarray(maps.pop(k, np.zeros(0, np.float32)), '<f4')
        if len(values) not in (0, n):
            raise ValueError('weight map %d holds %d values for %d vertices' % (k, len(values), n))
        _set(v, 'Values', Pod('FloatProperty', len(values), values.tobytes()))
        entries.append((key, v))
    if maps:
        raise ValueError('the template has no weight map for targets %s' % sorted(maps))
    _set(pm, 'WeightMaps', Map([], entries))
    _set(pm, 'InverseMasses', Pod('FloatProperty', n, np.ascontiguousarray(inv, '<f4').tobytes()))
    bd = pm.get('BoneData').value
    _set(pm, 'BoneData', Array(bone_data_items(spec['influences'], spec['weights']), dict(bd.inner)))
    wts = np.asarray(spec['weights'])
    _set(pm, 'MaxBoneWeights', struct.pack('<i', int((wts > 0).sum(1).max())))
    _set(pm, 'NumFixedVerts', struct.pack('<i', fixed))
    sci = np.asarray(self_collision_indices(V, md, spec.get('self_collision_radius', 0.0)), '<u4')
    _set(pm, 'SelfCollisionIndices', Pod('UInt32Property', len(sci), sci.tobytes()))
    bones = list(spec['bones'])
    slot = {b: i for i, b in enumerate(bones)}
    lod.get('CollisionData').value = collision_props(lod_t.get('CollisionData').value,
                                                     spec.get('collision') or {}, lambda b: slot[b])
    for p in lod.props:
        if p.name.text == 'bUseMultipleInfluences':
            p.extra['bool_val'] = 0
    # one LOD: no transition data to neighbouring LODs
    lod.trailer = [(0, b''), (0, b'')]
    out = []
    for p in template.props:
        q = copy.deepcopy(p)
        name = p.name.text
        if name == 'PhysicsAsset':
            if spec.get('physics_asset') is None:
                continue
            q.value = Ref(spec['physics_asset'])
        elif name == 'ClothConfigs':
            q.value = Map([], [(FN('ClothConfigNv'), Ref(config_ref))])
        elif name == 'LodData':
            q.value = Array([lod], dict(p.value.inner))
        elif name == 'LodMap':
            q.value = Pod('IntProperty', 1, struct.pack('<i', 0))
        elif name == 'UsedBoneNames':
            q.value = Array([FN(b) for b in bones])
        elif name == 'UsedBoneIndices':
            ui = np.asarray(spec['bone_indices'], '<i4')
            q.value = Pod('IntProperty', len(ui), ui.tobytes())
        elif name == 'AssetGuid':
            q.value = Native('Guid', bytes(spec['guid']))
        out.append(q)
    return out


def config_props(source_config, overrides=None):
    """A ClothConfigNv's properties copied from a shipped one; overrides: {name: float}."""
    import copy
    props = copy.deepcopy(source_config.props)
    for name, v in (overrides or {}).items():
        p = [q for q in props if q.name.text == name]
        if not p or p[0].type.text != 'FloatProperty':
            raise KeyError('the source config has no float %s' % name)
        p[0].value = struct.pack('<f', float(v))
    return props


def guid_for(text):
    """A stable FGuid (16 bytes) from a name, so rebuilds stay byte-identical."""
    import hashlib
    return hashlib.md5(('omd3cloth:' + text).encode()).digest()


def config_source(source=None):
    """(template ClothExport for the asset, ClothExport of its config) from a shipped package."""
    from riftlord.core import gamefiles
    path, asset = source or DEFAULT_CONFIG_SOURCE
    cp = ClothPackage(*gamefiles.package(path))
    a = [x for x in cp.assets() if x.name.rsplit('_', 1)[0] == asset or x.name == asset]
    if not a:
        raise SystemExit('%s holds no clothing asset %s' % (path, asset))
    a = a[0]
    cref = a.get('ClothConfigs').value.entries[0][1]
    cfg = [x for x in cp.exports if x.index == cref - 1][0]
    return a, cfg


def _ensure_import(pkg, class_package, class_name, outer, name):
    names = [s for s, _h in pkg.names]
    for i, rec in enumerate(pkg.imports):
        r = struct.unpack('<7i', rec)
        if (names[r[0]], names[r[2]], r[4], names[r[5]]) == (class_package, class_name, outer, name):
            return -(i + 1)
    return pkg.add_import(class_package, class_name, outer, name)


def _class_imports(pkg, module, cls):
    """(class, default object) imports for a /Script class, created as the cooker lays them out."""
    outer = _ensure_import(pkg, '/Script/CoreUObject', 'Package', 0, '/Script/' + module)
    klass = _ensure_import(pkg, '/Script/CoreUObject', 'Class', outer, cls)
    default = _ensure_import(pkg, '/Script/' + module, cls, outer, 'Default__' + cls)
    return klass, default


def _add_export(pkg, klass, template, outer, name, number, data):
    """Append an FObjectExport (a subobject, laid out like the shipped cloth exports) and
    grow the DependsMap that follows the export table; returns its 0-based index."""
    from riftlord.formats.ue.uassetio import EXPORT_ENTRY
    meta = bytearray(EXPORT_ENTRY)
    struct.pack_into('<4i', meta, 0, klass, 0, template, outer)
    struct.pack_into('<ii', meta, 16, pkg.add_name(name), number)
    struct.pack_into('<I', meta, 24, SUBOBJECT_FLAGS)
    struct.pack_into('<I', meta, 76, NOT_ALWAYS_LOADED_FOR_EDITOR_GAME)
    struct.pack_into('<i', meta, 84, len(pkg.preload))      # its dependencies go at the end
    pkg.exports.append({'meta': bytes(meta), 'size': len(data), 'offset': 0, 'data': data})
    # the cooked DependsMap is one empty TArray (int32 0) per export, right after the table
    at = pkg.depends_off - pkg.tail_start + 4 * (len(pkg.exports) - 1)
    tail = bytearray(pkg.tail)
    if bytes(tail[at - 4 * (len(pkg.exports) - 1):at]) != b'\0' * (4 * (len(pkg.exports) - 1)):
        raise SystemExit('the DependsMap is not the cooked all-empty shape')
    tail[at:at] = b'\0\0\0\0'
    pkg.tail = bytes(tail)
    # header offsets that point past the insertion move with it
    for attr in ('asset_registry_off', 'world_tile_off', 'preload_off'):
        v = getattr(pkg, attr)
        if v and v >= pkg.tail_start + at:
            setattr(pkg, attr, v + 4)
    return len(pkg.exports) - 1


def _array_of_objects_tag(pkg, name, refs):
    body = struct.pack('<i', len(refs)) + b''.join(struct.pack('<i', r) for r in refs)
    return (struct.pack('<iiiiii', pkg.add_name(name), 0, pkg.add_name('ArrayProperty'), 0, len(body), 0)
            + struct.pack('<ii', pkg.add_name('ObjectProperty'), 0) + b'\0' + body)


def attach(pkg, mesh_index, tagged, specs, after_tag='PhysicsAsset'):
    """Add one UClothingAssetCommon (+ its UClothConfigNv) per spec to a SkeletalMesh package.

    pkg: uassetio.Package being written; mesh_index: the SkeletalMesh export (0-based);
    tagged: the mesh's tagged-property bytes (ending in None). Adds the class imports, the
    exports (appended, so no existing index moves), their preload dependencies as the shipped
    packages declare them, the mesh's create-before-serialization dependency on each asset,
    and the MeshClothingAssets tag. Returns (new tagged bytes, [asset export index])."""
    from riftlord.formats.omd3 import omd3skm_format as fmt
    if not specs:
        return tagged, []
    names = [s for s, _h in pkg.names]
    if 'MeshClothingAssets' in [g[0] for g in fmt.tags(tagged, names)[0]]:
        raise SystemExit('the mesh already carries MeshClothingAssets')
    a_cls, a_def = _class_imports(pkg, 'ClothingSystemRuntimeCommon', 'ClothingAssetCommon')
    c_cls, c_def = _class_imports(pkg, 'ClothingSystemRuntimeNv', 'ClothConfigNv')
    ni = name_indexer(pkg)
    made = []
    for spec in specs:
        template, source_cfg = config_source(spec.get('config_source'))
        # the asset first (its config's outer), then the config; data filled once both exist
        ai = _add_export(pkg, a_cls, a_def, mesh_index + 1, spec['name'], 0, b'')
        ci = _add_export(pkg, c_cls, c_def, ai + 1, 'ClothConfigNv', 0, b'')
        pkg.exports[ci]['data'] = encode(config_props(source_cfg, spec.get('config_overrides')),
                                         source_cfg.tail, ni)
        pkg.exports[ai]['data'] = encode(asset_props(template, spec, ci + 1), template.tail, ni)
        # shipped shape: config sbc [class, CDO], cbc [its asset];
        # asset cbs [physics asset?, config], sbc [class, CDO], cbc [the mesh]
        pkg.add_preload_dependency(ci, c_cls, 'serialization_before_create')
        pkg.add_preload_dependency(ci, c_def, 'serialization_before_create')
        pkg.add_preload_dependency(ci, ai + 1, 'create_before_create')
        if spec.get('physics_asset') is not None:
            pkg.add_preload_dependency(ai, spec['physics_asset'], 'create_before_serialization')
        pkg.add_preload_dependency(ai, ci + 1, 'create_before_serialization')
        pkg.add_preload_dependency(ai, a_cls, 'serialization_before_create')
        pkg.add_preload_dependency(ai, a_def, 'serialization_before_create')
        pkg.add_preload_dependency(ai, mesh_index + 1, 'create_before_create')
        pkg.add_preload_dependency(mesh_index, ai + 1, 'create_before_serialization')
        made.append(ai)
    names = [s for s, _h in pkg.names]
    got, _end = fmt.tags(tagged, names)
    tag = _array_of_objects_tag(pkg, 'MeshClothingAssets', [i + 1 for i in made])
    by = {g[0]: g for g in got}
    if after_tag in by:
        g = by[after_tag]
        at = g[3] + g[4]
    else:
        at = len(tagged) - 8
    return tagged[:at] + tag + tagged[at:], made


# -- identity over the shipped data ----------------------------------------------------

def shipped_cloth_packages():
    """Every shipped package path (pak-internal, no extension) holding a ClothingAssetCommon."""
    from riftlord.core import gamefiles
    out = []
    for name in sorted(gamefiles.index()):
        if name.endswith('.uasset'):
            ua = gamefiles.read(name)
            if b'ClothingAssetCommon' in ua:
                out.append(name[:-len('.uasset')])
    return out


def identity(paths=None, verbose=True):
    """Decode + re-encode every cloth export; whole packages compared byte for byte.
    Returns {path: (ok, assets, configs, detail)}."""
    from riftlord.core import gamefiles
    out = {}
    for stem in paths or shipped_cloth_packages():
        ua = gamefiles.read(stem + '.uasset')
        ux = gamefiles.read(stem + '.uexp')
        detail = ''
        try:
            cp = ClothPackage(ua, ux)
            # a decode that also reads every LOD view, so the typed accessors are exercised
            for a in cp.assets():
                for lod in a.lods():
                    if len(lod.vertices) != len(lod.normals):
                        detail += ' %s: %d vertices vs %d normals' % (a.name, len(lod.vertices), len(lod.normals))
            nua, nux = cp.rebuild()
            ok = (nua == ua and nux == ux) and not detail
            if nua != ua or nux != ux:
                detail += ' differs (uasset %s, uexp %s)' % (nua == ua, nux == ux)
            out[stem] = (ok, len(cp.assets()), len(cp.configs()), detail.strip())
        except (Bad, struct.error) as ex:
            out[stem] = (False, 0, 0, 'decode failed: %s' % ex)
        if verbose:
            ok, na, nc, d = out[stem]
            print('%-4s %d assets %d configs  %s %s' % ('ok' if ok else 'FAIL', na, nc, stem.split('Content/')[-1], d))
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('identity', help='round-trip every shipped cloth asset byte-identical')
    d = sub.add_parser('dump', help='summarise the cloth assets of one package')
    d.add_argument('path')
    args = ap.parse_args(argv)
    if args.cmd == 'identity':
        got = identity()
        bad = [k for k, v in got.items() if not v[0]]
        print('%d packages, %d cloth assets, %d configs, %d failures'
              % (len(got), sum(v[1] for v in got.values()), sum(v[2] for v in got.values()), len(bad)))
        return 1 if bad else 0
    from riftlord.core import gamefiles
    cp = ClothPackage(*gamefiles.package(args.path))
    for x in cp.exports:
        print(x.index, x.cls, x.name, [str(p.name) + ('[%d]' % p.index if p.index else '') for p in x.props])
        for li, lod in enumerate(x.lods()):
            print('  LOD%d verts %d tris %d maps %s fixed-masses %s trailer %s' % (
                li, len(lod.vertices), len(lod.indices) // 3,
                {k: (len(v), float(v.min()) if len(v) else None, float(v.max()) if len(v) else None) for k, v in lod.maps.items()},
                int((lod.inverse_masses == 0).sum()) if lod.inverse_masses is not None else None,
                [n for n, _d in lod.trailer]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
