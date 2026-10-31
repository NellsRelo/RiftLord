# Generated from OMD2-Converter/thirdparty/survey-tools/meshgeo.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Read LOD0 source geometry from an EDITOR StaticMesh package.
UE 4.20 (FRawMesh bulk), UE 4.22-4.27 (FMeshDescription bulk, zlib), UE5 (MeshDescription in
an FCompressedBuffer payload, located by its magic)."""
import struct, zlib, re, os, ctypes
import numpy as np
from riftlord.fab.survey.pkgread import R, Package, find

ED_GUID = 'E4B068EDF49442E9A231DA0B2E46BB41'


ED_RAWMESH_DEPRECATED = 28
ED_REMOVED_HOLES = 30
ED_TRIANGLES = 35
ED_NEW_ATTR = 26


def custom(p, guid):
    for k, v in p.custom.items():
        a = struct.unpack('<4I', bytes.fromhex(k))
        if '%08X%08X%08X%08X' % a == guid:
            return v
    return None


def bulk_header(r, p):
    flags = r.u32()
    if flags & 0x2000:            # BULKDATA_Size64Bit
        count = r.i64(); disk = r.i64()
    else:
        count = r.i32(); disk = r.i32()
    off = r.i64()
    inline_at = None
    if not (flags & 0x1):          # not PayloadAtEndOfFile: inline
        inline_at = r.o
        r.o += disk
    return dict(flags=flags, count=count, disk=disk, off=off, inline_at=inline_at)


def bulk_bytes(p, h):
    d = p.d
    if h['inline_at'] is not None:
        start = h['inline_at']
    else:
        start = h['off'] + (0 if h['flags'] & 0x10000 else p.bulk_start)   # NoOffsetFixUp
    raw = d[start:start + h['disk']]
    if h['flags'] & 0x2:           # SerializeCompressedZLIB
        r = R(raw)
        tag = r.i64(); chunk = r.i64(); csz = r.i64(); usz = r.i64()
        chunks = []
        tot = 0
        while tot < usz:
            c = r.i64(); u = r.i64(); chunks.append((c, u)); tot += u
        out = bytearray()
        for c, u in chunks:
            out += zlib.decompress(r.raw(c))
        return bytes(out)
    return raw


def tarr(r, dt, w=1):
    n = r.i32()
    if n < 0 or n > 50_000_000:
        raise ValueError('bad array %d' % n)
    sz = np.dtype(dt).itemsize * w
    a = np.frombuffer(r.b, dt, n * w, r.o); r.o += n * sz
    return a.reshape(n, w) if w > 1 else a


def raw_mesh(b):
    r = R(b)
    ver = r.i32(); lic = r.i32()
    fmi = tarr(r, '<i4'); r.o += 0
    smooth = tarr(r, '<u4')
    pos = tarr(r, '<f4', 3)
    wedge = tarr(r, '<u4')
    tx = tarr(r, '<f4', 3); ty = tarr(r, '<f4', 3); tz = tarr(r, '<f4', 3)
    uvs = [tarr(r, '<f4', 2) for _ in range(8)]
    cols = tarr(r, '<u4')
    nuv = sum(1 for u in uvs if len(u))
    tri = wedge.reshape(-1, 3)
    groups = {}
    for m in np.unique(fmi):
        groups[int(m)] = int((fmi == m).sum())
    return dict(fmt='RawMesh', positions=pos, tri_vertex=tri, uv_channels=nuv,
                nwedges=len(wedge), groups=groups, has_colors=bool(len(cols)),
                has_normals=bool(len(tz)))


def bitarray(r):
    nb = r.i32()
    nw = (nb + 31) // 32
    words = np.frombuffer(r.b, '<u4', nw, r.o); r.o += 4 * nw
    bits = np.unpackbits(words.view(np.uint8), bitorder='little')[:nb]
    return np.nonzero(bits)[0]


TYPESZ = {0: 16, 1: 12, 2: 8, 3: 4, 4: 4, 5: 4}   # bool default serializes as uint32


def attr_set(r, new_format=True):
    nel = r.i32()
    n = r.i32()
    out = {}
    for _ in range(n):
        key = r.fstr().strip()
        ty = r.u32()
        ne = r.i32()
        nidx = r.i32()
        arrs = []
        for _ in range(nidx):
            if ty == 6:
                m = r.i32(); arrs.append([r.fstr() for _ in range(m)])
            else:
                esz = r.i32(); cnt = r.i32()
                arrs.append((r.o, esz, cnt)); r.o += esz * cnt
        if ty == 6:
            r.fstr()
        else:
            r.o += TYPESZ[ty]
        r.u32()
        out[key] = (ty, arrs)
    return nel, out


def mesh_description_ue4(b, edver):
    r = R(b)
    verts = bitarray(r)
    vi_ids = bitarray(r)
    vi_vert = np.frombuffer(b, '<i4', len(vi_ids), r.o).copy(); r.o += 4 * len(vi_ids)
    ed = bitarray(r); r.o += 8 * len(ed)
    pids = bitarray(r)
    poly_group = np.empty(len(pids), np.int32)
    poly_contour = []
    for k in range(len(pids)):
        c = tarr(r, '<i4')
        poly_contour.append(c)
        if edver < ED_REMOVED_HOLES:
            nh = r.i32()
            for _ in range(nh):
                tarr(r, '<i4')
        poly_group[k] = r.i32()
    pg = bitarray(r)
    sets = [attr_set(r) for _ in range(5)]
    tri = None
    tri_poly = None
    if edver >= ED_TRIANGLES:
        tids = bitarray(r)
        t = np.frombuffer(b, '<i4', 4 * len(tids), r.o).reshape(-1, 4); r.o += 16 * len(tids)
        tri_wedges = t[:, :3]; tri_poly = t[:, 3]
    vattr = sets[0][1]
    o, esz, cnt = vattr['Position'][1][0]
    pos = np.frombuffer(b, '<f4', cnt * 3, o).reshape(-1, 3)
    via = sets[1][1]
    nuv = len(via['TextureCoordinate'][1]) if 'TextureCoordinate' in via else 0
    # map vertex-instance id -> vertex id (ids are sparse indices)
    vi_map = np.full(int(vi_ids.max()) + 1 if len(vi_ids) else 0, -1, np.int64)
    vi_map[vi_ids] = vi_vert
    pmap = np.full(int(pids.max()) + 1 if len(pids) else 0, -1, np.int64)
    pmap[pids] = poly_group
    if tri_poly is None:
        tw = []; tp = []
        for k, c in enumerate(poly_contour):
            for j in range(1, len(c) - 1):
                tw.append((c[0], c[j], c[j + 1])); tp.append(pids[k])
        tri_wedges = np.array(tw, np.int64).reshape(-1, 3); tri_poly = np.array(tp, np.int64)
    tri = vi_map[tri_wedges]
    grp = pmap[tri_poly]
    groups = {int(g): int((grp == g).sum()) for g in np.unique(grp)}
    pga = sets[4][1]
    slots = pga.get('ImportedMaterialSlotName', (6, [[]]))[1][0]
    return dict(fmt='MeshDescription4', positions=pos, tri_vertex=tri, uv_channels=nuv,
                nwedges=len(vi_ids), groups=groups, slot_names=slots,
                has_colors='Color' in via, has_normals='Normal' in via,
                attributes=sorted(vattr) + sorted(via))


# ---------------------------------------------------------------- UE5
_OO = None


def _oodle():
    global _OO
    if _OO is None:
        _OO = ctypes.CDLL(os.path.expanduser('~/.cache/rift-lord/liboo2.so'))
    return _OO


def compressed_buffer(d, start):
    magic, crc, meth, comp, lvl, bse, nb, raw, csz = struct.unpack_from('>IIBBBBIQQ', d, start)
    assert magic == 0xb7756362
    p = start + 64
    if meth == 0:
        return d[p:p + raw], csz + 64
    sizes = struct.unpack_from('>%dI' % nb, d, p); p += 4 * nb
    bs = 1 << bse; out = bytearray(); left = raw
    for s in sizes:
        rr = min(bs, left)
        if s == rr:
            out += d[p:p + s]
        else:
            buf = ctypes.create_string_buffer(rr)
            got = _oodle().oo_decompress(d[p:p + s], s, buf, rr)
            assert got == rr, (got, rr)
            out += buf.raw
        p += s; left -= rr
    return bytes(out), p - start


def ue5_payloads(d):
    out = []
    for m in re.finditer(re.escape(struct.pack('>I', 0xb7756362)), d):
        try:
            raw, n = compressed_buffer(d, m.start())
            out.append((m.start(), raw))
        except Exception:
            pass
    return out


def _labels(b):
    out = {}
    for m in re.finditer(rb'[\x04-\x40]\x00\x00\x00([A-Za-z][A-Za-z ]{2,62})\x00', b):
        lab = m.group(1)
        if len(lab) + 1 != m.group(0)[0]:
            continue
        out.setdefault(lab.decode().strip(), []).append(m.end())
    return out


DT5 = {0: ('<f4', 4), 1: ('<f4', 3), 2: ('<f4', 2), 3: ('<f4', 1), 4: ('<i4', 1), 5: ('u1', 1)}


def mesh_description_ue5(b):
    labs = _labels(b)

    def regular(name, k=0):
        p = labs[name][k]
        t, ext, n, nch, ext2, esz, cnt = struct.unpack_from('<7I', b, p)
        dt, w = DT5[t]
        a = np.frombuffer(b, dt, cnt * esz // np.dtype(dt).itemsize, p + 28)
        return (a.reshape(n, -1) if ext * w > 1 else a), nch

    pos, _ = regular('Position')
    if pos.dtype != np.float32 or pos.ndim != 2:
        raise ValueError('position')
    wv, _ = regular('VertexIndex', 0)
    tw, _ = regular('VertexInstanceIndex')
    tg, _ = regular('PolygonGroupIndex')
    tw = tw.reshape(-1, 3)
    tri = wv[tw]
    groups = {int(g): int((tg == g).sum()) for g in np.unique(tg)}
    nuv = 0
    if 'TextureCoordinate' in labs:
        _, nuv = regular('TextureCoordinate')
    return dict(fmt='MeshDescription5', positions=pos, tri_vertex=tri, uv_channels=int(nuv),
                nwedges=len(wv), groups=groups, has_colors='Color' in labs,
                has_normals='Normal' in labs, attributes=sorted(labs))


# ---------------------------------------------------------------- entry
def read_static_mesh(path, p=None):
    p = p or Package(path)
    sm = [e for e in p.exports if p.class_of(e) == 'StaticMesh' and e['outer'] == 0]
    if not sm:
        raise ValueError('no StaticMesh export')
    e = sm[0]
    if p.ue5:
        pl = ue5_payloads(p.d)
        if not pl:
            raise ValueError('no compressed payload')
        errs = []
        for at, raw in sorted(pl, key=lambda x: -len(x[1])):
            try:
                return mesh_description_ue5(raw)
            except Exception as ex:
                errs.append(repr(ex))
        raise ValueError('ue5 parse: ' + '; '.join(errs[:3]))
    props, end = p.props(e)
    nsm = find(props, 'SourceModels')
    edver = custom(p, ED_GUID) or 0
    r = R(p.d, end)
    r.i32()                        # object guid flag
    r.raw(2)                       # strip flags
    r.i32(); r.i32(); r.i32()      # bCooked, BodySetup, NavCollision
    r.fstr(); r.u32()              # HighResSourceMeshName, CRC
    r.raw(16)                      # lighting guid
    ns = r.i32(); r.raw(4 * ns)    # sockets
    if edver < ED_RAWMESH_DEPRECATED:
        h = bulk_header(r, p)
        return raw_mesh(bulk_bytes(p, h))
    valid = r.i32()
    if not valid:
        raise ValueError('no mesh description')
    h = bulk_header(r, p)
    return mesh_description_ue4(bulk_bytes(p, h), edver)


def summary(g):
    pos = g['positions']
    tri = g['tri_vertex']
    used = pos[np.unique(tri)] if len(tri) else pos
    lo = used.min(0); hi = used.max(0)
    return dict(fmt=g['fmt'], verts=int(len(pos)), wedges=int(g['nwedges']), tris=int(len(tri)),
                uv_channels=int(g['uv_channels']), sections={str(k): v for k, v in g['groups'].items()},
                bmin=[round(float(x), 2) for x in lo], bmax=[round(float(x), 2) for x in hi],
                colors=bool(g.get('has_colors')))
