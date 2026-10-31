"""Strict codec for the native part of a cooked OMD3 USkeletalMesh export.

Field for field from UE 4.27.2 (USkeletalMesh::Serialize, FSkeletalMeshRenderData,
FSkeletalMeshLODRenderData and the buffers it holds), checked against shipped bytes.
`parse(data, start, ctx)` turns the bytes after the tagged properties into a dict;
`build(model)` turns that dict back into the same bytes. Buffers stay raw bytes so an
unchanged model writes back byte-identical. Layout facts live in the
omd3-skeletal-mesh-writing skill.
"""

import struct

#: FStripDataFlags global bits.
STRIP_EDITOR, STRIP_SERVER = 1, 2
#: FSkeletalMeshLODRenderData class strip bits.
CDSF_ADJACENCY, CDSF_MIN_LOD = 1, 2
#: FByteBulkData flags met in these exports.
BULK_AT_END_OF_FILE = 1 << 0
BULK_SEPARATE_FILE = 1 << 8
BULK_SIZE64 = 1 << 13
#: One FMeshToMeshVertData (cloth mapping element).
CLOTH_VERT = 64


class Short(Exception):
    """The bytes do not follow the serialiser."""


class R:
    """Little-endian cursor that refuses to read past the end."""

    def __init__(self, b, o=0):
        self.b, self.o = b, o

    def take(self, n):
        if n < 0 or self.o + n > len(self.b):
            raise Short('read of %d at %d past end %d' % (n, self.o, len(self.b)))
        v = self.b[self.o:self.o + n]
        self.o += n
        return bytes(v)

    def u(self, fmt):
        n = struct.calcsize('<' + fmt)
        v = struct.unpack_from('<' + fmt, self.take(n))
        return v if len(v) > 1 else v[0]

    def i32(self):
        return self.u('i')

    def u32(self):
        return self.u('I')

    def u8(self):
        return self.u('B')

    def boolean(self):
        v = self.u32()
        if v not in (0, 1):
            raise Short('bool %d at %d' % (v, self.o - 4))
        return v

    def count(self, limit=1 << 26):
        n = self.i32()
        if not 0 <= n <= limit:
            raise Short('count %d at %d' % (n, self.o - 4))
        return n

    def array(self, each):
        """TArray of fixed-size elements: (count, raw bytes)."""
        n = self.count()
        return n, self.take(n * each)

    def bulk(self):
        """TArray::BulkSerialize: (element size, count, raw bytes)."""
        size = self.i32()
        n = self.count()
        if not 0 < size <= 256:
            raise Short('bulk element size %d at %d' % (size, self.o - 8))
        return size, n, self.take(size * n)


class W:
    """Little-endian byte builder mirroring R."""

    def __init__(self):
        self.b = bytearray()

    def put(self, fmt, *v):
        self.b += struct.pack('<' + fmt, *v)

    def raw(self, data):
        self.b += data

    def array(self, n, data):
        self.put('i', n)
        self.b += data

    def bulk(self, size, n, data):
        if len(data) != size * n:
            raise ValueError('bulk of %d x %d holds %d bytes' % (n, size, len(data)))
        self.put('ii', size, n)
        self.b += data


# -- tagged properties (top level only) ---------------------------------------------

def _fname(data, o, names):
    i, n = struct.unpack_from('<2i', data, o)
    return names[i] if n == 0 else '%s_%d' % (names[i], n - 1)


def tags(data, names, o=0):
    """[(name, type, tag offset, value offset, size, bool value)] up to None, and the end."""
    out = []
    while True:
        name = _fname(data, o, names)
        if name == 'None':
            return out, o + 8
        kind = _fname(data, o + 8, names)
        size = struct.unpack_from('<i', data, o + 16)[0]
        p = o + 24
        flag = None
        if kind == 'StructProperty':
            p += 24
        elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty', 'SetProperty'):
            p += 8
        elif kind == 'MapProperty':
            p += 16
        elif kind == 'BoolProperty':
            flag = data[p]
            p += 1
        if data[p]:
            p += 16
        p += 1
        out.append((name, kind, o, p, size, flag))
        o = p + size


def context(data, names):
    """What the native serialiser branches on, read from the tagged properties."""
    got, end = tags(data, names)
    by = {t[0]: t for t in got}
    return {'vertex_colors': bool(by.get('bHasVertexColors', (0,) * 6)[5]),
            'per_poly_collision': bool(by.get('bEnablePerPolyCollision', (0,) * 6)[5]),
            'tags': got, 'end': end}


# -- render section ---------------------------------------------------------

def read_section(r):
    """FSkelMeshRenderSection."""
    s = {}
    s['strip'] = r.u('BB')
    s['material'] = r.u('H')
    s['base_index'], s['triangles'] = r.u('II')
    s['recompute_tangent'] = r.boolean()
    s['recompute_mask'] = r.u8()
    s['cast_shadow'] = r.boolean()
    s['base_vertex'] = r.u32()
    s['cloth_mapping'] = r.array(CLOTH_VERT)
    n = r.count(65535)
    s['bone_map'] = list(struct.unpack('<%dH' % n, r.take(2 * n)))
    s['vertices'] = r.u32()
    s['max_influences'] = r.i32()
    s['cloth_asset'] = r.u('h')
    s['clothing_data'] = r.take(20)
    if not s['strip'][1] & 1:
        s['dup_verts'] = r.array(4)
        s['dup_index'] = r.array(8)
    s['disabled'] = r.boolean()
    return s


def write_section(w, s):
    w.put('BB', *s['strip'])
    w.put('H', s['material'])
    w.put('II', s['base_index'], s['triangles'])
    w.put('I', s['recompute_tangent'])
    w.put('B', s['recompute_mask'])
    w.put('I', s['cast_shadow'])
    w.put('I', s['base_vertex'])
    w.array(*s['cloth_mapping'])
    w.put('i', len(s['bone_map']))
    w.put('%dH' % len(s['bone_map']), *s['bone_map'])
    w.put('I', s['vertices'])
    w.put('i', s['max_influences'])
    w.put('h', s['cloth_asset'])
    w.raw(s['clothing_data'])
    if not s['strip'][1] & 1:
        w.array(*s['dup_verts'])
        w.array(*s['dup_index'])
    w.put('I', s['disabled'])


# -- streamed buffers ---------------------------------------------------------

def read_streamed(r, lod, ctx):
    """FSkeletalMeshLODRenderData::SerializeStreamedData."""
    b = {}
    b['strip'] = r.u('BB')
    b['index_width'] = r.u8()
    b['indices'] = r.bulk()
    if b['indices'][0] != b['index_width']:
        raise Short('index element %d vs width %d' % (b['indices'][0], b['index_width']))
    b['pos_meta'] = r.u('II')
    b['positions'] = r.bulk()
    b['vb_strip'] = r.u('BB')
    b['vb_meta'] = r.u('iiII')        # NumTexCoords, NumVertices, full UVs, hi-prec tangents
    b['tangents'] = r.bulk()
    b['texcoords'] = r.bulk()
    b['sw_strip'] = r.u('BB')
    b['sw_meta'] = r.u('IIIII')       # variable, max infl, NumBones, NumVertices, 16-bit
    b['weights'] = r.bulk()
    b['lk_strip'] = r.u('BB')
    b['lk_vertices'] = r.u32()
    b['lookup'] = r.bulk()
    if ctx.get('vertex_colors'):
        b['col_strip'] = r.u('BB')
        b['col_meta'] = r.u('II')
        b['colors'] = r.bulk() if b['col_meta'][1] else None
    if not b['strip'][1] & CDSF_ADJACENCY:
        b['adj_width'] = r.u8()
        b['adjacency'] = r.bulk()
    if any(s['cloth_mapping'][0] for s in lod['sections']):
        b['cloth_strip'] = r.u('BB')
        b['cloth'] = r.bulk()
        b['cloth_index_mapping'] = r.array(8)
    n = r.count(64)
    b['weight_profiles'] = n
    if n:
        raise Short('skin weight profiles present (%d); not modelled' % n)
    b['ray_tracing'] = r.array(1)
    return b


def write_streamed(w, b, lod, ctx):
    w.put('BB', *b['strip'])
    w.put('B', b['index_width'])
    w.bulk(*b['indices'])
    w.put('II', *b['pos_meta'])
    w.bulk(*b['positions'])
    w.put('BB', *b['vb_strip'])
    w.put('iiII', *b['vb_meta'])
    w.bulk(*b['tangents'])
    w.bulk(*b['texcoords'])
    w.put('BB', *b['sw_strip'])
    w.put('IIIII', *b['sw_meta'])
    w.bulk(*b['weights'])
    w.put('BB', *b['lk_strip'])
    w.put('I', b['lk_vertices'])
    w.bulk(*b['lookup'])
    if ctx.get('vertex_colors'):
        w.put('BB', *b['col_strip'])
        w.put('II', *b['col_meta'])
        if b['col_meta'][1]:
            w.bulk(*b['colors'])
    if not b['strip'][1] & CDSF_ADJACENCY:
        w.put('B', b['adj_width'])
        w.bulk(*b['adjacency'])
    if any(s['cloth_mapping'][0] for s in lod['sections']):
        w.put('BB', *b['cloth_strip'])
        w.bulk(*b['cloth'])
        w.array(*b['cloth_index_mapping'])
    w.put('i', b['weight_profiles'])
    w.array(*b['ray_tracing'])


# -- one LOD ----------------------------------------------------------------

def read_lod(r, ctx):
    """FSkeletalMeshLODRenderData::Serialize, cooked."""
    lod = {'strip': r.u('BB')}
    lod['cooked_out'] = r.boolean()
    lod['inlined'] = r.boolean()
    n = r.count(65535)
    lod['required_bones'] = list(struct.unpack('<%dH' % n, r.take(2 * n)))
    if lod['strip'][0] & STRIP_SERVER or lod['cooked_out']:
        return lod
    lod['sections'] = [read_section(r) for _ in range(r.count(256))]
    n = r.count(65535)
    lod['active_bones'] = list(struct.unpack('<%dH' % n, r.take(2 * n)))
    lod['buffers_size'] = r.u32()
    if lod['inlined']:
        at = r.o
        lod['buffers'] = read_streamed(r, lod, ctx)
        lod['streamed_size'] = r.o - at
    else:
        raise Short('streamed (non-inlined) LOD not modelled')
    return lod


def write_lod(w, lod, ctx):
    w.put('BB', *lod['strip'])
    w.put('II', lod['cooked_out'], lod['inlined'])
    w.put('i', len(lod['required_bones']))
    w.put('%dH' % len(lod['required_bones']), *lod['required_bones'])
    if lod['strip'][0] & STRIP_SERVER or lod['cooked_out']:
        return
    w.put('i', len(lod['sections']))
    for s in lod['sections']:
        write_section(w, s)
    w.put('i', len(lod['active_bones']))
    w.put('%dH' % len(lod['active_bones']), *lod['active_bones'])
    inner = W()
    write_streamed(inner, lod['buffers'], lod, ctx)
    w.put('I', lod['buffers_size'])
    w.raw(inner.b)


def streamed_size(lod, ctx):
    """Bytes SerializeStreamedData writes: what BuffersSize must state."""
    inner = W()
    write_streamed(inner, lod['buffers'], lod, ctx)
    return len(inner.b)


# -- the whole native block ------------------------------------------------------

def read_material(r):
    """FSkeletalMaterial, cooked: 40 bytes unless the imported slot name is kept."""
    m = {'object': r.i32(), 'slot': r.u('ii'), 'keep_imported': r.boolean()}
    if m['keep_imported']:
        m['imported'] = r.u('ii')
    m['uv_init'] = r.boolean()
    m['uv_override'] = r.boolean()
    m['uv_density'] = r.u('4f')
    return m


def write_material(w, m):
    w.put('i', m['object'])
    w.put('ii', *m['slot'])
    w.put('I', m['keep_imported'])
    if m['keep_imported']:
        w.put('ii', *m['imported'])
    w.put('II', m['uv_init'], m['uv_override'])
    w.put('4f', *m['uv_density'])


def parse(data, start, ctx):
    """The native tail of a SkeletalMesh export, from `start` (end of tags) to the end."""
    r = R(data, start)
    m = {'object_guid': r.boolean()}
    if m['object_guid']:
        m['guid'] = r.take(16)
    m['strip'] = r.u('BB')
    m['bounds'] = r.u('7f')
    m['materials'] = [read_material(r) for _ in range(r.count(256))]
    n = r.count(65535)
    m['bones'] = [r.u('iii') for _ in range(n)]          # FName (index, number), parent
    n = r.count(65535)
    m['pose'] = [r.u('10f') for _ in range(n)]           # quat xyzw, translation, scale
    n = r.count(65535)
    m['name_map'] = [r.u('iii') for _ in range(n)]       # FName, bone index
    m['cooked'] = r.boolean()
    if ctx.get('min_mobile_lod'):
        m['min_mobile_lod'] = r.i32()
    m['lods'] = [read_lod(r, ctx) for _ in range(r.count(16))]
    m['inlined_lods'], m['non_optional_lods'] = r.u('BB')
    m['dummy_objects'] = r.array(4)
    if ctx.get('per_poly_collision'):
        m['body_setup'] = r.i32()
    m['rest'] = r.take(len(data) - r.o)
    return m


def parse_skeleton(data, start):
    """USkeleton's native block: its FReferenceSkeleton as (bones, pose, name map)."""
    r = R(data, start)
    if r.boolean():
        r.take(16)
    bones = [r.u('iii') for _ in range(r.count(65535))]
    pose = [r.u('10f') for _ in range(r.count(65535))]
    name_map = [r.u('iii') for _ in range(r.count(65535))]
    return bones, pose, name_map


def build(m, ctx):
    """Bytes of the native tail for a model from `parse`."""
    w = W()
    w.put('I', m['object_guid'])
    if m['object_guid']:
        w.raw(m['guid'])
    w.put('BB', *m['strip'])
    w.put('7f', *m['bounds'])
    w.put('i', len(m['materials']))
    for mat in m['materials']:
        write_material(w, mat)
    w.put('i', len(m['bones']))
    for b in m['bones']:
        w.put('iii', *b)
    w.put('i', len(m['pose']))
    for p in m['pose']:
        w.put('10f', *p)
    w.put('i', len(m['name_map']))
    for e in m['name_map']:
        w.put('iii', *e)
    w.put('I', m['cooked'])
    if ctx.get('min_mobile_lod'):
        w.put('i', m['min_mobile_lod'])
    w.put('i', len(m['lods']))
    for lod in m['lods']:
        write_lod(w, lod, ctx)
    w.put('BB', m['inlined_lods'], m['non_optional_lods'])
    w.array(*m['dummy_objects'])
    if ctx.get('per_poly_collision'):
        w.put('i', m['body_setup'])
    w.raw(m['rest'])
    return bytes(w.b)
