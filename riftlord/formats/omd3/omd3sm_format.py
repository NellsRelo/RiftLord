"""Strict codec for the native part of a cooked OMD3 UStaticMesh export.

Field for field from UE 4.27.2 (UStaticMesh::Serialize, FStaticMeshRenderData::Serialize,
FStaticMeshLODResources::Serialize/SerializeBuffers and the vertex/index buffers),
checked against the shipped bytes. `parse(data, start)` turns the bytes after the
tagged properties into a dict; `build(model)` turns that dict back into the same bytes.
Buffers stay raw bytes so an unchanged model writes back byte-identical. The layout is
written up in thirdparty/STATICMESH-FORMAT.md.
"""

import struct

from riftlord.formats.omd3.omd3skm_format import R, W, Short, tags, _fname  # noqa: F401  (re-exported)

#: FStaticMeshLODResources class strip bits (StaticMeshResources.h).
CDSF_ADJACENCY, CDSF_MIN_LOD, CDSF_REVERSED_INDEX, CDSF_RAY_TRACING = 1, 2, 4, 8
#: FStripDataFlags global bits.
STRIP_EDITOR, STRIP_SERVER = 1, 2
#: FStaticMeshRenderData distance-field class strip bit.
DF_STRIPPED = 1
#: MAX_STATIC_MESH_LODS: ScreenSize is always serialised for this many LODs.
MAX_STATIC_MESH_LODS = 8
#: One FStaticMeshSection on disk: five uint32 then four bools as uint32.
SECTION_FMT = '<5I4I'
#: FByteBulkData flags (Bulk.h) met or refused here.
BULK_PAYLOAD_AT_END = 0x1
BULK_SIZE64 = 0x2000
BULK_SEPARATE_FILE = 0x100
BULK_FORCE_NOT_INLINE = 0x40


def context(data, names):
    """The tagged properties and where they end."""
    got, end = tags(data, names)
    return {'tags': got, 'end': end, 'names': names}


# -- pieces ------------------------------------------------------------------

def _index_buffer(r):
    b32 = r.boolean()
    bulk = _rt(r)
    return {'b32': b32, 'bulk': bulk, 'expand32': r.boolean()}


def _w_index_buffer(w, ib):
    w.put('I', ib['b32'])
    w.put('ii', ib['bulk'][0], ib['bulk'][1])
    w.raw(ib['bulk'][2])
    w.put('I', ib['expand32'])


def _sampler(r):
    np_, prob = r.array(4)
    na, alias = r.array(4)
    return (np_, prob, na, alias, r.take(4))


def _w_sampler(w, s):
    w.array(s[0], s[1])
    w.array(s[2], s[3])
    w.raw(s[4])


def read_buffers(r, strip_class, nsections):
    """FStaticMeshLODResources::SerializeBuffers."""
    b = {'strip': r.u('BB')}
    if b['strip'][1] != strip_class:
        raise Short('buffer strip class %r != LOD %r' % (b['strip'], strip_class))
    b['pos_meta'] = r.u('II')                       # Stride, NumVertices
    b['positions'] = r.bulk()
    b['vb_strip'] = r.u('BB')
    b['vb_meta'] = r.u('4I')     # NumTexCoords, NumVertices, bFullPrecisionUVs, bHighPrecisionTangents
    b['tangents'] = r.bulk()
    b['texcoords'] = r.bulk()
    b['col_strip'] = r.u('BB')
    b['col_meta'] = r.u('II')                       # Stride, NumVertices
    b['colors'] = r.bulk() if b['col_meta'][1] > 0 else None
    b['index'] = _index_buffer(r)
    global_, cls = b['strip']
    if not cls & CDSF_REVERSED_INDEX:
        b['reversed'] = _index_buffer(r)
    b['depth_only'] = _index_buffer(r)
    if not cls & CDSF_REVERSED_INDEX:
        b['reversed_depth_only'] = _index_buffer(r)
    if not global_ & STRIP_EDITOR:
        b['wireframe'] = _index_buffer(r)
    if not cls & CDSF_ADJACENCY:
        b['adjacency'] = _index_buffer(r)
    if not cls & CDSF_RAY_TRACING:
        b['ray_tracing'] = _rt(r)
    b['section_samplers'] = [_sampler(r) for _ in range(nsections)]
    b['sampler'] = _sampler(r)
    return b


def _rt(r):
    size = r.i32()
    n = r.count()
    return size, n, r.take(size * n)


def write_buffers(w, b):
    w.put('BB', *b['strip'])
    w.put('II', *b['pos_meta'])
    w.bulk(*b['positions'])
    w.put('BB', *b['vb_strip'])
    w.put('4I', *b['vb_meta'])
    w.bulk(*b['tangents'])
    w.bulk(*b['texcoords'])
    w.put('BB', *b['col_strip'])
    w.put('II', *b['col_meta'])
    if b['colors'] is not None:
        w.bulk(*b['colors'])
    _w_index_buffer(w, b['index'])
    for k in ('reversed', 'depth_only', 'reversed_depth_only', 'wireframe', 'adjacency'):
        if k in b:
            _w_index_buffer(w, b[k])
    if 'ray_tracing' in b:
        size, n, raw = b['ray_tracing']
        w.put('ii', size, n)
        w.raw(raw)
    for s in b['section_samplers']:
        _w_sampler(w, s)
    _w_sampler(w, b['sampler'])


def buffer_sizes(b):
    """FStaticMeshBuffersSize as the cooker computes it: (serialized, depth-only, reversed)
    -- the byte sizes of the GPU buffers (vertex data + index data), not of the stream."""
    total = len(b['positions'][2]) + len(b['tangents'][2]) + len(b['texcoords'][2])
    if b['colors'] is not None:
        total += len(b['colors'][2])
    total += len(b['index']['bulk'][2])
    rev = 0
    for k in ('reversed', 'reversed_depth_only'):
        if k in b:
            rev += len(b[k]['bulk'][2])
    depth = len(b['depth_only']['bulk'][2])
    total += rev + depth
    for k in ('wireframe', 'adjacency'):
        if k in b:
            total += len(b[k]['bulk'][2])
    if 'ray_tracing' in b:
        total += len(b['ray_tracing'][2])
    return total, depth, rev


def read_bulk_header(r):
    """FByteBulkData::Serialize header (cooked): flags, counts, offset, maybe payload."""
    flags = r.u32()
    if flags & BULK_SIZE64:
        count, disk = r.u('qq')
    else:
        count, disk = r.u('ii')
    offset = r.u('q')
    h = {'flags': flags, 'count': count, 'disk': disk, 'offset': offset}
    if not flags & (BULK_PAYLOAD_AT_END | BULK_SEPARATE_FILE) and disk > 0:
        h['payload_at'] = r.o            # export-relative; OffsetInFile = export offset + this
        h['payload'] = r.take(disk)
    return h


def write_bulk_header(w, h):
    w.put('I', h['flags'])
    w.put('qq' if h['flags'] & BULK_SIZE64 else 'ii', h['count'], h['disk'])
    w.put('q', h['offset'])
    if 'payload' in h:
        w.raw(h['payload'])


def read_lod(r):
    lod = {'strip': r.u('BB')}
    n = r.count(256)
    lod['sections'] = [r.u('5I4I') for _ in range(n)]
    lod['max_deviation'] = r.take(4)
    lod['cooked_out'] = r.boolean()
    lod['inlined'] = r.boolean()
    if lod['strip'][0] & STRIP_SERVER or lod['cooked_out']:
        return lod
    if lod['inlined']:
        lod['buffers'] = read_buffers(r, lod['strip'][1], n)
        lod['buffers_size'] = r.u('3I')
    else:
        lod['bulk'] = read_bulk_header(r)
        lod['availability'] = read_availability(r)
        lod['buffers_size'] = r.u('3I')
    return lod


def read_availability(r):
    """SerializeAvailabilityInfo: DepthOnlyNumTriangles, Packed, then buffer metadata."""
    a = {'depth_tris': r.u32(), 'packed': r.u32()}
    a['vb_meta'] = r.u('4I')
    a['pos_meta'] = r.u('II')
    a['col_meta'] = r.u('II')
    a['index_meta'] = r.u('II')
    a['extra_meta'] = [r.u('II') for _ in range(5)]   # reversed, depth, revdepth, wire, adj
    return a


def write_availability(w, a):
    w.put('II', a['depth_tris'], a['packed'])
    w.put('4I', *a['vb_meta'])
    w.put('II', *a['pos_meta'])
    w.put('II', *a['col_meta'])
    w.put('II', *a['index_meta'])
    for m in a['extra_meta']:
        w.put('II', *m)


def write_lod(w, lod):
    w.put('BB', *lod['strip'])
    w.put('i', len(lod['sections']))
    for s in lod['sections']:
        w.put('5I4I', *s)
    w.raw(lod['max_deviation'])
    w.put('II', lod['cooked_out'], lod['inlined'])
    if 'buffers' in lod:
        write_buffers(w, lod['buffers'])
        w.put('3I', *lod['buffers_size'])
    elif 'bulk' in lod:
        write_bulk_header(w, lod['bulk'])
        write_availability(w, lod['availability'])
        w.put('3I', *lod['buffers_size'])


def read_distance_field(r):
    n = r.count(1 << 30)
    d = {'volume': (n, r.take(n))}
    d['size'] = r.u('3i')
    d['box'] = r.take(25)                 # FBox: Min 3f, Max 3f, IsValid u8
    d['minmax'] = r.u('2f')
    d['flags'] = r.u('3I')                # bMeshWasClosed, bBuiltAsIfTwoSided, bMeshWasPlane
    return d


def write_distance_field(w, d):
    w.array(*d['volume'])
    w.put('3i', *d['size'])
    w.raw(d['box'])
    w.put('2f', *d['minmax'])
    w.put('3I', *d['flags'])


# -- the export ----------------------------------------------------------------

def parse(data, start, min_mobile_lod=False):
    """The native tail of a StaticMesh export, from `start` (end of tags) to the end."""
    r = R(data, start)
    m = {'object_guid': r.boolean()}
    if m['object_guid']:
        m['guid'] = r.take(16)
    m['strip'] = r.u('BB')
    m['cooked'] = r.boolean()
    if not m['cooked']:
        raise Short('uncooked static mesh')
    m['body_setup'] = r.i32()
    m['nav_collision'] = r.i32()
    m['lighting_guid'] = r.take(16)
    m['sockets'] = [r.i32() for _ in range(r.count(4096))]
    if min_mobile_lod:
        m['min_mobile_lod'] = r.i32()
    m['lods'] = [read_lod(r) for _ in range(r.count(MAX_STATIC_MESH_LODS))]
    m['inlined_lods'] = r.u8()
    m['df_strip'] = r.u('BB')
    m['distance_fields'] = []
    if not (m['df_strip'][0] & STRIP_SERVER or m['df_strip'][1] & DF_STRIPPED):
        for _ in m['lods']:
            valid = r.boolean()
            m['distance_fields'].append(read_distance_field(r) if valid else None)
    m['bounds'] = r.u('7f')
    m['lods_share_lighting'] = r.boolean()
    m['screen_size'] = [r.u('If') for _ in range(MAX_STATIC_MESH_LODS)]
    m['occluder'] = r.boolean()
    if m['occluder']:
        m['occluder_vertices'] = _rt(r)
        m['occluder_indices'] = _rt(r)
    m['speedtree'] = r.boolean()
    if m['speedtree']:
        raise Short('SpeedTree wind data is not decoded')
    n = r.count(256)
    m['materials'] = [r.u('iiiII4f') for _ in range(n)]   # object, FName, UVChannelData
    m['rest'] = r.take(len(data) - r.o)
    return m


def build(m):
    """Bytes of the native tail for a model from `parse`."""
    w = W()
    w.put('I', m['object_guid'])
    if m['object_guid']:
        w.raw(m['guid'])
    w.put('BB', *m['strip'])
    w.put('I', m['cooked'])
    w.put('ii', m['body_setup'], m['nav_collision'])
    w.raw(m['lighting_guid'])
    w.put('i', len(m['sockets']))
    for s in m['sockets']:
        w.put('i', s)
    if 'min_mobile_lod' in m:
        w.put('i', m['min_mobile_lod'])
    w.put('i', len(m['lods']))
    for lod in m['lods']:
        write_lod(w, lod)
    w.put('B', m['inlined_lods'])
    w.put('BB', *m['df_strip'])
    if not (m['df_strip'][0] & STRIP_SERVER or m['df_strip'][1] & DF_STRIPPED):
        for d in m['distance_fields']:
            w.put('I', d is not None)
            if d is not None:
                write_distance_field(w, d)
    w.put('7f', *m['bounds'])
    w.put('I', m['lods_share_lighting'])
    for s in m['screen_size']:
        w.put('If', *s)
    w.put('I', m['occluder'])
    if m['occluder']:
        for k in ('occluder_vertices', 'occluder_indices'):
            size, n, raw = m[k]
            w.put('ii', size, n)
            w.raw(raw)
    w.put('I', m['speedtree'])
    w.put('i', len(m['materials']))
    for mat in m['materials']:
        w.put('iiiII4f', *mat)
    w.raw(m['rest'])
    return bytes(w.b)
