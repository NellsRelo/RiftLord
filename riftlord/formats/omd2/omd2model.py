"""Read an OMD2 (Vision Engine 8.1) skinned `.model`: mesh, skin, skeleton, materials.

    python3 omd2model.py art/horde/orc/Orc01.model      # summary of one model
    python3 omd2model.py --check                        # validate all 1,160 models

Coordinates are Vision/3ds Max object space: right-handed, Z up, units of
centimetres. Quaternions are (x, y, z, w). The format is documented in the
`omd2-model-anim-format` skill.
"""

import argparse
import collections
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

STEAM = '/home/nellsrelo/.local/share/Steam/steamapps/common'
DATA_ZIP = os.path.join(STEAM, 'Orcs Must Die 2', 'data.zip')

RECORD = bytes([0x0b, 0x0a, 0x02, 0x01])   # header record marker inside VMSH
VMSH_MAGIC = 0x4455abcd
#: Vertex descriptor slots, in order, after the stride.
DESC_SLOTS = ['pos', 'color', 'normal'] + ['tex%d' % i for i in range(16)] + ['color2']
#: Descriptor format nibble -> number of float32 components.
FLOAT_FORMATS = {1: 1, 2: 2, 3: 3, 4: 4}

_zip = None


def archive():
    """The shared data.zip handle."""
    global _zip
    if _zip is None:
        _zip = zipfile.ZipFile(DATA_ZIP)
    return _zip


def load(path):
    """Bytes of a data.zip entry; backslashes and case are normalised."""
    z = archive()
    path = path.replace('\\', '/').lstrip('/')
    try:
        return z.read(path)
    except KeyError:
        low = _lower_index().get(path.lower())
        if low is None:
            raise
        return z.read(low)


_lower = None


def _lower_index():
    global _lower
    if _lower is None:
        _lower = {n.lower(): n for n in archive().namelist()}
    return _lower


def exists(path):
    """True when data.zip holds this entry (case-insensitive)."""
    return path.replace('\\', '/').lstrip('/').lower() in _lower_index()


# ---------------------------------------------------------------- container

class Chunk:
    """One VBIN chunk: tag (forward spelling), body span, nesting level, children."""

    def __init__(self, tag, start, size, level, after):
        self.tag, self.start, self.size = tag, start, size
        self.level, self.after = level, after
        self.kids = []

    @property
    def end(self):
        return self.start + self.size

    def find(self, tag):
        """First descendant with this tag, or None."""
        for k in self.kids:
            if k.tag == tag:
                return k
            got = k.find(tag)
            if got:
                return got
        return None

    def __repr__(self):
        return '<%s @%d +%d>' % (self.tag, self.start, self.size)


def _is_tag(b):
    return len(b) == 4 and all(65 <= c <= 90 for c in b)


def _chunk_at(buf, o, limit):
    """The chunk opening at `o`, validated by its closing tag, or None."""
    tag = buf[o:o + 4]
    if not _is_tag(tag) or o + 8 > limit:
        return None
    size, = struct.unpack_from('<I', buf, o + 4)
    close = o + 8 + size + 4
    if close + 4 > len(buf) or buf[close:close + 4] != tag:
        return None
    level, = struct.unpack_from('<I', buf, close - 4)
    after = struct.unpack_from('<i', buf, close + 4)[0] if close + 8 <= len(buf) else -1
    return Chunk(tag[::-1].decode(), o + 8, size, level, after), close + 8


def _siblings(buf, o, limit, out):
    while True:
        got = _chunk_at(buf, o, limit)
        if not got:
            return o
        ch, nxt = got
        out.append(ch)
        p = ch.start                         # children: the first valid chunk in the body
        while p < ch.end - 8:
            if _is_tag(buf[p:p + 4]) and _chunk_at(buf, p, ch.end + 12):
                _siblings(buf, p, ch.end + 12, ch.kids)
                break
            p += 1
        o = nxt


def chunks(buf):
    """Top-level chunks of a VBIN file as a list of Chunk trees."""
    if buf[:4] != b'VBIN':
        raise ValueError('not a VBIN file')
    out = []
    _siblings(buf, 12, len(buf), out)
    return out


def chunk_map(buf):
    """{tag: Chunk} for the top level (first of each tag)."""
    out = {}
    for c in chunks(buf):
        out.setdefault(c.tag, c)
    return out


class Reader:
    """Little-endian cursor over a bytes object."""

    def __init__(self, buf, pos=0):
        self.buf, self.pos = buf, pos

    def take(self, fmt):
        v = struct.unpack_from('<' + fmt, self.buf, self.pos)
        self.pos += struct.calcsize('<' + fmt)
        return v if len(v) > 1 else v[0]

    def string(self):
        n = self.take('I')
        s = self.buf[self.pos:self.pos + n].decode('latin1')
        self.pos += n
        return s

    def floats(self, n):
        a = np.frombuffer(self.buf, '<f4', n, self.pos).astype(np.float64)
        self.pos += 4 * n
        return a


# ---------------------------------------------------------------- math

def conj(q):
    """Stored Vision quaternion(s) -> standard active (x, y, z, w): negate x, y, z."""
    q = np.array(q, np.float64)
    q[..., :3] *= -1
    return q


def quat_to_matrix(q):
    """3x3 rotation of unit quaternion(s) (x, y, z, w); column-vector convention."""
    q = np.asarray(q, np.float64)
    x, y, z, w = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    m = np.empty(q.shape[:-1] + (3, 3))
    m[..., 0, 0] = 1 - 2 * (y * y + z * z)
    m[..., 0, 1] = 2 * (x * y - z * w)
    m[..., 0, 2] = 2 * (x * z + y * w)
    m[..., 1, 0] = 2 * (x * y + z * w)
    m[..., 1, 1] = 1 - 2 * (x * x + z * z)
    m[..., 1, 2] = 2 * (y * z - x * w)
    m[..., 2, 0] = 2 * (x * z - y * w)
    m[..., 2, 1] = 2 * (y * z + x * w)
    m[..., 2, 2] = 1 - 2 * (x * x + y * y)
    return m


def trs(t, q, s=None):
    """4x4 matrix(es) from translation, quaternion (x,y,z,w) and optional scale."""
    t = np.asarray(t, np.float64)
    r = quat_to_matrix(q)
    if s is not None:
        r = r * np.asarray(s, np.float64)[..., None, :]
    m = np.zeros(t.shape[:-1] + (4, 4))
    m[..., :3, :3] = r
    m[..., :3, 3] = t
    m[..., 3, 3] = 1
    return m


# ---------------------------------------------------------------- pieces

def read_skeleton(buf, ch):
    """Bones of a SKEL chunk: [{'name','parent','local_t','local_q','inv_t','inv_q'}]."""
    r = Reader(buf, ch.start)
    r.take('H')
    count = r.take('H')
    bones = []
    for _ in range(count):
        # u32 length + name, i16 parent, inverse bind (t3, q4), local bind (t3, q4)
        name = r.string()
        parent = r.take('h')
        inv_t, inv_q = r.floats(3), conj(r.floats(4))
        loc_t, loc_q = r.floats(3), conj(r.floats(4))
        bones.append({'name': name, 'parent': parent, 'local_t': loc_t, 'local_q': loc_q,
                      'inv_t': inv_t, 'inv_q': inv_q})
    if r.pos > ch.end:
        raise ValueError('SKEL overruns its chunk')
    return bones


def _descriptor(words):
    """(stride, {slot: (offset, float count)}, {slots packed as 4 x u8}).

    Format 0 gives no size; the slot runs to the next slot's offset. In `.vmesh`
    files a 4-byte format-0 `color` is packed RGBA bytes and a 12-byte format-0
    `normal` is float3.
    """
    stride = words[0]
    used = []
    for name, w in zip(DESC_SLOTS, words[1:]):
        if w == 0xffff:
            continue
        fmt, off = w >> 12, w & 0xfff
        if (fmt and fmt not in FLOAT_FORMATS) or off >= stride:
            raise ValueError('vertex slot %s has unknown format word %#x' % (name, w))
        used.append((name, fmt, off))
    ends = sorted({off for _, _, off in used} | {stride})
    out, packed = {}, set()
    for name, fmt, off in used:
        if fmt:
            out[name] = (off, FLOAT_FORMATS[fmt])
            continue
        span = min(e for e in ends if e > off) - off
        if span % 4:
            raise ValueError('vertex slot %s spans %d bytes' % (name, span))
        out[name] = (off, span // 4)
        if span == 4:
            packed.add(name)
    return stride, out, packed


def read_vmsh(buf, ch):
    """Vertex streams and triangle indices of the VMSH chunk."""
    b = ch.start
    if buf[b:b + 4] != b'HSMV':
        raise ValueError('VMSH body does not start with its sub-tag')
    _ver, magic, _streams = struct.unpack_from('<3I', buf, b + 4)
    if magic != VMSH_MAGIC:
        raise ValueError('VMSH magic %#x' % magic)
    # 'HSMV' u32 version, u32 magic, u32 streams; then a 48-byte descriptor record
    m1 = b + 16
    if buf[m1:m1 + 4] != RECORD:
        raise ValueError('no vertex descriptor record')
    dlen, = struct.unpack_from('<I', buf, m1 + 4)
    stride, slots, packed = _descriptor(struct.unpack_from('<%dH' % (dlen // 2), buf, m1 + 8))
    # count record: nverts @+4, nindices @+13, index bits @+17, nprims @+21; data @+36
    m2 = buf.find(RECORD, m1 + 8 + dlen, ch.end)
    nv, = struct.unpack_from('<I', buf, m2 + 4)
    ni, ibits, nprim = struct.unpack_from('<3I', buf, m2 + 13)
    if ibits not in (16, 32):
        raise ValueError('index width %d' % ibits)
    v0 = m2 + 36
    raw = np.frombuffer(buf, np.uint8, nv * stride, v0).reshape(nv, stride) if nv else \
        np.zeros((0, stride), np.uint8)
    streams = {}
    for name, (off, n) in slots.items():
        if name in packed:
            streams[name] = raw[:, off:off + 4].astype(np.float64) / 255.0
        else:
            streams[name] = raw[:, off:off + 4 * n].copy().view('<f4').reshape(nv, n).astype(np.float64)
    i0 = v0 + nv * stride
    idx = np.frombuffer(buf, '<u2' if ibits == 16 else '<u4', ni, i0).astype(np.int64)
    tail = ch.end - (i0 + ni * ibits // 8)
    if tail != 28:
        raise ValueError('VMSH has %d bytes after the indices, expected 28' % tail)
    return {'stride': stride, 'slots': slots, 'nverts': nv, 'nindices': ni,
            'index_bits': ibits, 'nprims': nprim, 'streams': streams,
            'indices': idx, 'bbox': np.frombuffer(buf, '<f4', 6, i0 + ni * ibits // 8).astype(float)}


def read_weights(buf, ch, nverts):
    """(bone index (N,4), weight (N,4)) from WGHT; up to 4 influences, zero-padded."""
    r = Reader(buf, ch.start)
    r.take('I')
    bi = np.zeros((nverts, 4), np.int64)
    bw = np.zeros((nverts, 4), np.float64)
    for v in range(nverts):
        # u16 count, then count x (u16 bone, u16 weight / 65535)
        n = r.take('H')
        if n > 4:
            raise ValueError('vertex %d has %d influences' % (v, n))
        for j in range(n):
            bi[v, j], w = r.take('2H')
            bw[v, j] = w / 65535.0
    if r.pos != ch.end:
        raise ValueError('WGHT ends at %d, chunk at %d' % (r.pos, ch.end))
    return bi, bw


def read_submeshes(buf, ch):
    """SUBM records: index range, vertex range, material index, bbox."""
    r = Reader(buf, ch.start)
    count = r.take('i')
    version = 0
    if count == -1:                      # versioned layout: -1, version, count
        version = r.take('I')
        count = r.take('I')
    subs = []
    for _ in range(count):
        # the second index/vertex pairs repeat or narrow the first; meaning unconfirmed
        first, nidx, _f2, _n2, vfirst, vcount, _v2, _c2 = r.take('8I')
        bbox = r.floats(6)
        material = r.take('i')
        if version >= 1:
            r.take('2i')
            r.take('2B')
            r.take('2I')
        subs.append({'first_index': first, 'index_count': nidx, 'vertex_first': vfirst,
                     'vertex_count': vcount, 'material': material, 'bbox': bbox})
    if r.pos != ch.end:
        raise ValueError('SUBM ends at %d, chunk at %d' % (r.pos, ch.end))
    return subs


def _strings(buf, start, end):
    """Every printable length-prefixed string in [start, end)."""
    out, p = [], start
    while p + 4 <= end:
        n, = struct.unpack_from('<I', buf, p)
        if 0 < n < 260 and p + 4 + n <= end:
            s = buf[p + 4:p + 4 + n]
            if all(32 <= c < 127 for c in s):
                out.append(s.decode('latin1'))
                p += 4 + n
                continue
        p += 1
    return out


def read_materials(buf, ch):
    """MTRL records in order: {'name', 'textures'} (texture paths as stored)."""
    out = []
    for k in ch.kids:
        if k.tag != 'MTRL':
            continue
        r = Reader(buf, k.start)
        r.take('H')
        name = r.string()
        tex = [s for s in _strings(buf, r.pos, k.end) if '.' in s]
        out.append({'name': name, 'textures': tex})
    return out


def read_bone_props(buf, ch):
    """CBPR: one exporter property string per bone."""
    r = Reader(buf, ch.start)
    n = r.take('H')
    return [r.string() for _ in range(n)]


def read_bone_boxes(buf, ch):
    """BBBX: (count, 6) per-bone boxes (min xyz, max xyz) in bone space."""
    n, = struct.unpack_from('<H', buf, ch.start)
    return np.frombuffer(buf, '<f4', n * 6, ch.start + 2).reshape(n, 6).astype(float)


def material_xml(path):
    """[{attr...}] from <model>.model_data/materials.xml, or [] when absent."""
    x = path.replace('\\', '/') + '_data/materials.xml'
    if not exists(x):
        return []
    root = ET.fromstring(re.sub(r'<!--.*?-->', '', load(x).decode('latin1'), flags=re.S))
    return [dict(m.attrib) for m in root.iter('Material')]


# ---------------------------------------------------------------- model

class Model:
    """One `.model`. Arrays are numpy; see the skill for conventions."""

    def __init__(self, path, buf=None):
        self.path = path.replace('\\', '/')
        buf = load(path) if buf is None else buf
        top = chunk_map(buf)
        self.chunk_tags = list(top)
        vm = read_vmsh(buf, top['VMSH'])
        s = vm['streams']
        self.positions = s['pos']             # (N,3) bind pose, model space, cm
        self.normals = s.get('normal')        # (N,3) unit, or None
        self.uv0 = s.get('tex0')              # (N,2) diffuse UV, v down (DDS row order)
        self.uv1 = s.get('tex1')              # (N,2) lightmap UV, or None
        self.tangents = s.get('tex2')         # (N,3) tangent, no handedness sign
        self.vertex_slots = vm['slots']
        self.index_bits = vm['index_bits']
        self.triangles = vm['indices'].reshape(-1, 3)   # (T,3) counter-clockwise about the normal
        self.submeshes = read_submeshes(buf, top['SUBM'])
        self.materials = read_materials(buf, top['MTRS'])
        self.bones = read_skeleton(buf, top['SKEL']) if 'SKEL' in top else []
        if 'WGHT' in top:
            # (N,4) indices into self.bones, (N,4) weights summing to 1
            self.bone_index, self.bone_weight = read_weights(buf, top['WGHT'], len(self.positions))
        else:
            self.bone_index = self.bone_weight = None
        self.bone_props = read_bone_props(buf, top['CBPR']) if 'CBPR' in top else []
        self.bone_boxes = read_bone_boxes(buf, top['BBBX']) if 'BBBX' in top else None
        self.bounds = np.frombuffer(buf, '<f4', 6, top['BNDS'].start).astype(float)

    # skeleton --------------------------------------------------------
    @property
    def bone_names(self):
        return [b['name'] for b in self.bones]

    def bone(self, name):
        """Index of a bone by name, or -1."""
        try:
            return self.bone_names.index(name)
        except ValueError:
            return -1

    def local_matrices(self):
        """(B,4,4) bind-pose local transforms (parent space)."""
        if not self.bones:
            return np.zeros((0, 4, 4))
        return trs(np.array([b['local_t'] for b in self.bones]),
                   np.array([b['local_q'] for b in self.bones]))

    def inverse_bind(self):
        """(B,4,4) stored inverse bind matrices (model space -> bone space)."""
        if not self.bones:
            return np.zeros((0, 4, 4))
        return trs(np.array([b['inv_t'] for b in self.bones]),
                   np.array([b['inv_q'] for b in self.bones]))

    def bind_world(self):
        """(B,4,4) bind-pose model-space bone matrices composed from the locals."""
        return compose(self.bones, self.local_matrices())

    def skin_bind_world(self):
        """(B,4,4) model-space bone matrices the mesh was skinned in: inverse(inverse_bind)."""
        return np.linalg.inv(self.inverse_bind())

    # validation ------------------------------------------------------
    def check(self):
        """{metric: value} of self-consistency measurements."""
        out = {'verts': len(self.positions), 'tris': len(self.triangles),
               'bones': len(self.bones)}
        tri = self.triangles
        if len(tri):
            p = self.positions
            area = 0.5 * np.linalg.norm(np.cross(p[tri[:, 1]] - p[tri[:, 0]],
                                                 p[tri[:, 2]] - p[tri[:, 0]]), axis=1)
            out['degenerate'] = int((area < 1e-6).sum())
            out['index_max_ok'] = bool(tri.max() < len(p))
            if self.normals is not None:
                fn = np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])
                vn = self.normals[tri].sum(1)
                good = area > 1e-6
                out['winding_ccw'] = float((np.einsum('ij,ij->i', fn, vn)[good] > 0).mean()) \
                    if good.any() else 1.0
        if self.normals is not None and len(self.normals):
            err = np.abs(np.linalg.norm(self.normals, axis=1) - 1)
            out['bad_normals'] = int((err > 0.01).sum())
            out['normal_len_err'] = float(err[err <= 0.01].max()) if (err <= 0.01).any() else 0.0
        if self.bone_weight is not None and len(self.positions):
            out['weight_sum_err'] = float(np.abs(self.bone_weight.sum(1) - 1).max())
            used = self.bone_weight > 0
            out['bone_index_ok'] = bool((self.bone_index[used] < len(self.bones)).all())
        if self.bones:
            parents = [b['parent'] for b in self.bones]
            out['roots'] = sum(1 for q in parents if q < 0)
            out['parents_first'] = all(q < i for i, q in enumerate(parents))
            ident = np.einsum('bij,bjk->bik', self.inverse_bind(), self.bind_world())
            err = np.abs(ident - np.eye(4)).max((1, 2))
            out['invbind_err'] = float(err.max())
            out['invbind_off_bones'] = [self.bones[i]['name'] for i in np.nonzero(err > 0.01)[0]]
            if self.bone_weight is not None and len(self.positions):
                moved = skin(self, self.bind_world())[0] - self.positions
                out['bind_skin_err'] = float(np.linalg.norm(moved, axis=1).max())
            qn = np.linalg.norm([b['local_q'] for b in self.bones], axis=1)
            out['quat_len_err'] = float(np.abs(qn - 1).max())
        if self.submeshes:
            out['submesh_cover'] = sum(s['index_count'] for s in self.submeshes) == 3 * len(tri)
            out['material_ok'] = all(0 <= s['material'] < max(1, len(self.materials))
                                     for s in self.submeshes)
        xml = material_xml(self.path)
        if xml:
            out['materials_xml_ok'] = {m['name'] for m in self.materials} <= {m.get('name') for m in xml}
        return out


def compose(bones, local):
    """(B,4,4) model-space matrices from parent-relative ones; parents precede children."""
    world = np.empty_like(local)
    for i, b in enumerate(bones):
        p = b['parent']
        world[i] = local[i] if p < 0 else world[p] @ local[i]
    return world


def skin(model, world):
    """Linear-blend skin the bind vertices with (B,4,4) model-space bone matrices.

    Returns (positions (N,3), normals (N,3) or None).
    """
    if model.bone_weight is None or not len(world):
        return model.positions.copy(), None if model.normals is None else model.normals.copy()
    sk = np.einsum('bij,bjk->bik', world, model.inverse_bind())
    blend = np.einsum('nk,nkij->nij', model.bone_weight, sk[model.bone_index])
    p = np.einsum('nij,nj->ni', blend[:, :3, :3], model.positions) + blend[:, :3, 3]
    n = None
    if model.normals is not None:
        n = np.einsum('nij,nj->ni', blend[:, :3, :3], model.normals)
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
    return p, n


def read(path):
    """Model for a data.zip path."""
    return Model(path)


def read_rxl(path):
    """Ragdoll bones of an `.rxl`: [{attr...}] per <bone>."""
    root = ET.fromstring(load(path))
    return [dict(b.attrib) for b in root.iter('bone')]


# ---------------------------------------------------------------- CLI

def _check_all():
    names = [n for n in archive().namelist() if n.lower().endswith('.model')]
    fails = collections.Counter()
    worst = collections.defaultdict(lambda: (0, ''))
    flags = collections.Counter()
    ok = 0
    totals = collections.Counter()
    for n in names:
        try:
            m = Model(n)
            c = m.check()
        except Exception as e:      # noqa: BLE001 - census groups every failure
            fails[type(e).__name__ + ': ' + str(e)[:60]] += 1
            continue
        ok += 1
        totals['verts'] += c['verts']
        totals['tris'] += c['tris']
        totals['degenerate'] += c.get('degenerate', 0)
        totals['bad_normals'] += c.get('bad_normals', 0)
        if c.get('invbind_off_bones'):
            flags['models with invbind != bind (>0.01)'] += 1
            totals['bones invbind != bind'] += len(c['invbind_off_bones'])
        if 'materials_xml_ok' in c:
            flags['materials.xml match' if c['materials_xml_ok'] else 'materials.xml MISMATCH'] += 1
        if c.get('bind_skin_err', 0) > 0.05:
            flags['bind skin err > 0.05'] += 1
        for k in ('normal_len_err', 'weight_sum_err', 'invbind_err', 'quat_len_err', 'bind_skin_err'):
            if k in c and c[k] > worst[k][0]:
                worst[k] = (c[k], n)
        for k in ('index_max_ok', 'bone_index_ok', 'parents_first', 'submesh_cover', 'material_ok'):
            if k in c and not c[k]:
                flags[k] += 1
        if 'roots' in c:
            flags['roots=%d' % c['roots']] += 1
        if 'winding_ccw' in c:
            flags['winding ccw>0.9' if c['winding_ccw'] > 0.9 else
                  'winding cw>0.9' if c['winding_ccw'] < 0.1 else 'winding mixed'] += 1
        flags['index%d' % m.index_bits] += 1
        flags['stride%d' % (len(m.vertex_slots) and max(o + 4 * k for o, k in m.vertex_slots.values()))] += 1
    print('models read %d / %d' % (ok, len(names)))
    for k, v in fails.most_common():
        print('  FAIL %4d  %s' % (v, k))
    print('totals', dict(totals))
    for k, (v, n) in sorted(worst.items()):
        print('  worst %-15s %.3g  %s' % (k, v, n))
    for k, v in sorted(flags.items()):
        print('  %-20s %d' % (k, v))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('model', nargs='?')
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args()
    if a.check:
        _check_all()
        return
    m = Model(a.model)
    print(m.path, m.chunk_tags)
    for k, v in m.check().items():
        print('  %-15s %s' % (k, v))
    for s in m.submeshes:
        print('  submesh', {k: v for k, v in s.items() if k != 'bbox'})
    for mt in m.materials:
        print('  material', mt)
    for i, b in enumerate(m.bones):
        print('  bone %2d %-24s parent %d' % (i, b['name'], b['parent']))


if __name__ == '__main__':
    sys.exit(main())
