"""Orcs Must Die! Unchained levels for the Trace tool (riftlord/trace.py).

Unchained (Steam `OrcsMustDieUnchained`, AppID 427270) is stock Unreal Engine 3, measured
2026-10-17 on the Steam install:

- `SpitfireGame/CookedPCConsole/*.upk`, 1,870 seek-free packages: file version 870,
  licensee 0, engine 20430, cooker 136. Package flag 0x02000000 (store compressed):
  the summary is plain, the rest is in FCompressedChunkInfo chunks (uncompressed
  offset/size, compressed offset/size) of LZO1X blocks (CompressionFlags 2), each
  chunk headed by the package tag, a block size, its totals and a (compressed,
  uncompressed) pair per block. Not encrypted. Decompressed with the system's liblzo2
  when there is one, else in Python (`_lzo1x_py`).
- Summary after the tag: version, header size, folder name, package flags, name /
  export / import counts and offsets, depends offset, import-export guids offset and
  two counts, thumbnail table offset, package guid, generations (12 bytes each),
  engine and cooker versions, compression flags, chunks.
- Names: FString then 8 flag bytes. Imports: class package, class, outer, name (28
  bytes). Exports (68 bytes with no net objects): class, super, outer, name, archetype,
  object flags u64, serial size, serial offset, export flags, net object counts array,
  package guid, package flags. An FName is (index, number); number n > 0 is `_n-1`.
- An export's serial data starts with its script state frame when it is flagged
  RF_HasStack (0x0200000000000000; 22 bytes with an empty state stack), a component's
  TemplateOwnerClass, then a NetIndex, then UE3 tagged properties (name, type, size,
  array index; StructProperty adds the struct name, BoolProperty a byte value,
  ByteProperty an enum name). A Rotator is three ints, 65536 to the turn.
- A map is `<Map>.upk` (gameplay: RTrapGrid, RRift, RDoor, RLane, spawners...) plus the
  packages its LevelStreaming* exports name in `PackageName` -- `<map>_geo` holds the
  art. 39 maps are named by having a `_Geo` or `_Audio` sublevel: PvE_*, NPE_1..5,
  NewbieTutorial, TutorialSurvival.
- The art is StaticMeshComponents, almost all inside StaticMeshCollectionActors (the
  cooker's merge of StaticMeshActors). Their `Translation`, `Rotation`, `Scale` and
  `Scale3D` are the world transform; the collection actor carries no matrices. A
  component under any other actor (RKActor, InterpActor) is relative to the actor's
  `Location`, `Rotation`, `DrawScale` and `DrawScale3D`. Components with `HiddenGame`
  or under a `bHidden` actor are left out. The levels' BSP Model is empty (156 bytes);
  there is no terrain.
- A component's StaticMesh is often an import of the persistent level's own export
  (OrderCastle_Floor.Mesh.FloorAQuad in PvE_Corridors_Geo), so meshes are resolved by
  path across all of a map's packages.
- StaticMesh render data is found by its buffers' headers (`mesh`). All 39 maps read,
  with 0 to 3 meshes per map not found.
- UE3 is Unreal's own left-handed Z-up space: no mirroring. World units are ASSUMED 1:1
  with OMD3, not measured (the Corridors kit's MetalWall stands 1,281 uu).
"""

import ctypes
import ctypes.util
import functools
import os
import re
import struct
import threading

from riftlord.core import gamepath

GAME_FOLDER = 'OrcsMustDieUnchained'
COOKED = os.path.join('SpitfireGame', 'CookedPCConsole')
#: A UE3 package's first four bytes.
TAG = 0x9E2A83C1
#: FCompressedChunkInfo.CompressionFlags: LZO.
COMPRESS_LZO = 0x02
#: Bytes a tables-only read takes from the start of a package: the summary and its chunk
#: list (a longer one is read whole).
TABLES_HEAD = 1 << 16


def install():
    return gamepath.other_game(GAME_FOLDER)


#: liblzo2's names. Looked up through a variable: PyInstaller bundles a library named
#: literally in find_library(), and liblzo2 is GPL, so the frozen app uses the user's own
#: when there is one (tools/notices.py), else the Python decoder.
LZO_NAMES = ('lzo2', 'liblzo2.so.2', 'lzo2.dll', 'liblzo2.dll')


@functools.lru_cache(maxsize=1)
def _lzo():
    """lzo1x_decompress_safe from the system's liblzo2, or None."""
    find = ctypes.util.find_library
    for name in (find(LZO_NAMES[0]),) + LZO_NAMES[1:]:
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
            fn = lib.lzo1x_decompress_safe
        except (OSError, AttributeError):
            continue
        fn.restype = ctypes.c_int
        fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p,
                       ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p]
        return fn
    return None


def lzo1x_decompress(src, size):
    """LZO1X-decompress `src` into exactly `size` bytes."""
    fn = _lzo()
    if fn:
        out = ctypes.create_string_buffer(size)
        n = ctypes.c_size_t(size)
        rc = fn(src, len(src), out, ctypes.byref(n), None)
        if rc != 0 or n.value != size:
            raise ValueError('LZO error %d' % rc)
        return out.raw
    return _lzo1x_py(src, size)


def _lzo1x_py(src, size):
    """LZO1X decompression in Python (the format of lzo1x_d.ch)."""
    out = bytearray()
    ip = 0
    n = len(src)

    def length(base, mask_bits):
        nonlocal ip
        t = src[ip] & mask_bits
        ip += 1
        if t == 0:
            t = 0
            while src[ip] == 0:
                t += 255
                ip += 1
            t += base + src[ip]
            ip += 1
        return t

    t = src[ip]
    state = 0
    if t > 17:
        ip += 1
        t -= 17
        out += src[ip:ip + t]
        ip += t
        state = 4 if t >= 4 else t
    while ip < n:
        t = src[ip]
        ip += 1
        if t < 16:
            if state == 0:
                if t == 0:
                    while src[ip] == 0:
                        t += 255
                        ip += 1
                    t += 15 + src[ip]
                    ip += 1
                t += 3
                out += src[ip:ip + t]
                ip += t
                state = 4
                continue
            if state < 4:
                dist = 1 + (t >> 2) + (src[ip] << 2)
                ip += 1
                ln = 2
            else:
                dist = 1 + 0x800 + (t >> 2) + (src[ip] << 2)
                ip += 1
                ln = 3
        elif t >= 64:
            dist = 1 + ((t >> 2) & 7) + (src[ip] << 3)
            ip += 1
            ln = (t >> 5) + 1
        elif t >= 32:
            ip -= 1
            ln = length(31, 31) + 2
            dist = 1 + ((src[ip] | (src[ip + 1] << 8)) >> 2)
            t = src[ip]
            ip += 2
        else:
            ip -= 1
            hi = (t & 8) << 11
            ln = length(7, 7) + 2
            v = src[ip] | (src[ip + 1] << 8)
            t = src[ip]
            ip += 2
            dist = hi + (v >> 2)
            if dist == 0:
                break
            dist += 0x4000
        start = len(out) - dist
        for k in range(ln):
            out.append(out[start + k])
        state = t & 3
        if state:
            out += src[ip:ip + state]
            ip += state
    if len(out) != size:
        raise ValueError('LZO: %d bytes, expected %d' % (len(out), size))
    return bytes(out)


class Reader:
    def __init__(self, buf, pos=0):
        self.buf = buf
        self.pos = pos

    def u8(self):
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def i32(self):
        v, = struct.unpack_from('<i', self.buf, self.pos)
        self.pos += 4
        return v

    def u32(self):
        v, = struct.unpack_from('<I', self.buf, self.pos)
        self.pos += 4
        return v

    def u64(self):
        v, = struct.unpack_from('<Q', self.buf, self.pos)
        self.pos += 8
        return v

    def f32(self):
        v, = struct.unpack_from('<f', self.buf, self.pos)
        self.pos += 4
        return v

    def fstring(self):
        n = self.i32()
        if n == 0:
            return ''
        if n < 0:
            raw = self.buf[self.pos:self.pos - 2 * n]
            self.pos += -2 * n
            return raw.decode('utf-16-le').rstrip('\0')
        raw = self.buf[self.pos:self.pos + n]
        self.pos += n
        return raw.decode('latin-1').rstrip('\0')


class Package:
    """One UE3 package, decompressed into `data` (offsets as the tables give them)."""

    def __init__(self, path, tables_only=False):
        """`tables_only`: read and decompress only the summary and the chunks holding the
        name, import and export tables (export data is then not readable)."""
        with open(path, 'rb') as fh:
            if not tables_only:
                self._load(fh.read(), False, None)
                return
            head = fh.read(TABLES_HEAD)
            try:
                self._load(head, True, fh)
            except (ValueError, struct.error, IndexError):
                fh.seek(0)
                self._load(fh.read(), True, None)

    def _load(self, raw, tables_only, fh):
        """Parse the package from `raw` (the whole file, or with an open file `fh` only its
        first TABLES_HEAD bytes, the table chunks then read from `fh`)."""
        r = Reader(raw)
        if r.u32() != TAG:
            raise ValueError('not a UE3 package')
        self.version = r.u32() & 0xffff
        self.header_size = r.i32()
        self.folder = r.fstring()
        self.flags = r.u32()
        self.name_count, self.name_offset = r.i32(), r.i32()
        self.export_count, self.export_offset = r.i32(), r.i32()
        self.import_count, self.import_offset = r.i32(), r.i32()
        self.depends_offset = r.i32()
        self.import_export_guids_offset = r.i32()
        r.i32()
        r.i32()
        self.thumbnail_offset = r.i32()
        r.pos += 16
        gens = r.i32()
        r.pos += gens * 12
        self.engine_version = r.u32()
        self.cooker_version = r.u32()
        self.compression = r.u32()
        chunks = [(r.i32(), r.i32(), r.i32(), r.i32()) for _ in range(r.i32())]
        self.chunks = chunks
        if chunks and self.compression != COMPRESS_LZO:
            raise ValueError('compression %d is not LZO' % self.compression)
        tables_end = max(self.name_offset, self.import_offset, self.export_offset,
                         self.depends_offset)
        if chunks:
            used = [c for c in chunks if not (tables_only and c[0] >= tables_end)]
            size = max([self.header_size] + [u_off + u_size for u_off, u_size, _c, _s in used])
            if fh is not None and len(raw) < self.header_size:
                fh.seek(0)
                raw = fh.read(self.header_size)
            data = bytearray(size)
            data[:self.header_size] = raw[:self.header_size]
            for u_off, u_size, c_off, c_size in used:
                if fh is None:
                    data[u_off:u_off + u_size] = self._chunk(raw, c_off, u_size)
                else:
                    fh.seek(c_off)
                    data[u_off:u_off + u_size] = self._chunk(fh.read(c_size), 0, u_size)
            self.data = bytes(data)
        elif fh is not None:
            fh.seek(0)
            self.data = fh.read(max(self.header_size, tables_end) + TABLES_HEAD)
        else:
            self.data = raw
        self._tables()

    def _chunk(self, raw, at, want):
        r = Reader(raw, at)
        if r.u32() != TAG:
            raise ValueError('bad chunk tag at %d' % at)
        block = r.i32()
        _c_total, u_total = r.i32(), r.i32()
        blocks = []
        left = u_total
        while left > 0:
            c, u = r.i32(), r.i32()
            blocks.append((c, u))
            left -= u
        out = []
        pos = r.pos
        for c, u in blocks:
            out.append(lzo1x_decompress(raw[pos:pos + c], u))
            pos += c
        got = b''.join(out)
        if len(got) != want:
            raise ValueError('chunk at %d: %d bytes, expected %d (block %d)'
                             % (at, len(got), want, block))
        return got

    def _tables(self):
        r = Reader(self.data, self.name_offset)
        self.names = []
        for _ in range(self.name_count):
            self.names.append(r.fstring())
            r.u64()
        r.pos = self.import_offset
        self.imports = []
        for _ in range(self.import_count):
            cpkg, cname = self.fname(r), self.fname(r)
            outer = r.i32()
            name = self.fname(r)
            self.imports.append({'classPackage': cpkg, 'class': cname, 'outer': outer,
                                 'name': name})
        r.pos = self.export_offset
        self.exports = []
        for _ in range(self.export_count):
            e = {'classIndex': r.i32(), 'super': r.i32(), 'outer': r.i32(),
                 'name': self.fname(r), 'archetype': r.i32(), 'objectFlags': r.u64(),
                 'size': r.i32(), 'offset': r.i32(), 'exportFlags': r.u32()}
            nets = r.i32()
            r.pos += 4 * nets + 16 + 4
            self.exports.append(e)
        for e in self.exports:
            e['class'] = self.object_name(e['classIndex']) or 'Class'

    def fname(self, r):
        i, n = r.i32(), r.i32()
        base = self.names[i] if 0 <= i < len(self.names) else '?%d' % i
        return base if n == 0 else '%s_%d' % (base, n - 1)

    def object_name(self, index):
        if index > 0:
            return self.exports[index - 1]['name']
        if index < 0:
            return self.imports[-index - 1]['name']
        return None

    def path(self, index):
        out = []
        while index:
            if index > 0:
                e = self.exports[index - 1]
                out.append(e['name'])
                index = e['outer']
            else:
                e = self.imports[-index - 1]
                out.append(e['name'])
                index = e['outer']
        return '.'.join(reversed(out))

    def blob(self, export):
        return self.data[export['offset']:export['offset'] + export['size']]

    def props(self, export):
        """(tagged properties {name: value}, offset after them) of one export.

        Values: ObjectProperty an object index, IntProperty an int, FloatProperty a
        float, BoolProperty a bool, NameProperty a name, Vector/Rotator structs a tuple
        (a Rotator's are UE3 ints, 65536 to the turn), anything else its raw bytes.
        """
        b = self.blob(export)
        return self.tagged(b, self._lead(export, b))

    def tagged(self, b, pos):
        """(tagged properties {name: value}, offset after them) read from `b` at `pos`."""
        r = Reader(b, pos)
        out = {}
        while r.pos + 8 <= len(b):
            name = self.fname(r)
            if name == 'None':
                break
            typ = self.fname(r)
            size = r.i32()
            index = r.i32()
            struct_name = None
            value = None
            if typ == 'StructProperty':
                struct_name = self.fname(r)
            elif typ == 'BoolProperty':
                value = bool(r.u8())
            elif typ == 'ByteProperty':
                self.fname(r)
            raw = b[r.pos:r.pos + size]
            r.pos += size
            if typ == 'ObjectProperty' and size == 4:
                value = struct.unpack('<i', raw)[0]
            elif typ == 'IntProperty' and size == 4:
                value = struct.unpack('<i', raw)[0]
            elif typ == 'FloatProperty' and size == 4:
                value = struct.unpack('<f', raw)[0]
            elif typ == 'NameProperty' and size == 8:
                value = self.fname(Reader(raw))
            elif struct_name == 'Vector' and size == 12:
                value = struct.unpack('<3f', raw)
            elif struct_name == 'Rotator' and size == 12:
                value = struct.unpack('<3i', raw)
            elif value is None:
                value = raw
            out[name if index == 0 else '%s[%d]' % (name, index)] = value
        return out, r.pos

    def struct_array(self, raw):
        """The elements of an ArrayProperty of structs (a count, then each element's
        tagged properties), as [{name: value}]."""
        if not isinstance(raw, bytes) or len(raw) < 4:
            return []
        n, = struct.unpack_from('<i', raw, 0)
        out, pos = [], 4
        for _ in range(max(0, min(n, 4096))):
            got, pos = self.tagged(raw, pos)
            out.append(got)
        return out

    @staticmethod
    def object_array(raw):
        """The object indices of an ArrayProperty of objects."""
        if not isinstance(raw, bytes) or len(raw) < 4:
            return []
        n, = struct.unpack_from('<i', raw, 0)
        if n < 0 or 4 + 4 * n > len(raw):
            return []
        return list(struct.unpack_from('<%di' % n, raw, 4))

    def _lead(self, export, b):
        """Bytes before an export's tagged properties.

        In order: the script state frame of an object flagged RF_HasStack (Node,
        StateNode, ProbeMask u32, LatentAction u16, StateStack count and 12 bytes an
        entry, and the code Offset when Node is set: 22 bytes with an empty stack); a
        component's TemplateOwnerClass (and its TemplateName, for a template); then
        the NetIndex. Checked against the first tag, and found by scanning the first
        LEAD_SCAN bytes when the layout does not hold.
        """
        pos = 0
        try:
            if export['objectFlags'] & RF_HAS_STACK:
                node, = struct.unpack_from('<i', b, 0)
                count, = struct.unpack_from('<i', b, 14)
                pos = 18 + 12 * max(0, count) + (4 if node else 0)
            if export['class'].endswith('Component'):
                owner, = struct.unpack_from('<i', b, pos)
                pos += 12 if owner and (export['objectFlags'] & RF_TEMPLATE) else 4
            pos += 4
        except struct.error:
            return len(b)
        if self._tag_at(b, pos):
            return pos
        for off in range(0, min(LEAD_SCAN, len(b) - 16)):
            if self._tag_at(b, off):
                return off
        return pos

    def _tag_at(self, b, pos):
        """True when a property tag (or the closing None) starts at `pos`."""
        if pos + 8 > len(b):
            return False
        n, num = struct.unpack_from('<2i', b, pos)
        if not (0 <= n < len(self.names)) or num != 0:
            return False
        if self.names[n] == 'None':
            return True
        if pos + 16 > len(b):
            return False
        t, tnum = struct.unpack_from('<2i', b, pos + 8)
        return 0 <= t < len(self.names) and tnum == 0 and self.names[t] in PROPERTY_TYPES


#: Object flags that make a component a template (RF_ArchetypeObject, RF_ClassDefaultObject).
RF_TEMPLATE = 0x400 | 0x200
#: Object flag of an object serialized with its script state frame.
RF_HAS_STACK = 0x0200000000000000
#: Bytes searched for the first property tag when the expected layout does not hold.
LEAD_SCAN = 96
#: Property type names a tag may carry.
PROPERTY_TYPES = frozenset(('ByteProperty', 'IntProperty', 'BoolProperty', 'FloatProperty',
                            'ObjectProperty', 'NameProperty', 'StrProperty', 'StructProperty',
                            'ArrayProperty', 'ClassProperty', 'ComponentProperty',
                            'DelegateProperty', 'InterfaceProperty', 'MapProperty'))
#: Unreal rotator units per turn.
ROTATOR_TURN = 65536.0
#: Name suffixes of streamed packages with no geometry worth tracing.
SKIP_STREAMED = ('_audio', 'cinematic', '_kismet')


def mesh(package, export):
    """{verts, tris, uvs, elements} of a cooked UE3 StaticMesh's LOD 0, or None.

    Found by the buffers' own headers rather than by walking FStaticMeshRenderData:
    FPositionVertexBuffer is `Stride, NumVertices` then its bulk array's
    `ElementSize, Count` -- (12, N, 12, N) -- and N float3; the render index buffer is
    the first bulk array after it of `ElementSize 2, Count M` with M a multiple of 3
    whose largest index is N - 1 (the wireframe and adjacency buffers follow it).
    Measured on PvE_Corridors_Geo: 98 of 98 meshes, no degenerate triangles.

    UVs: FStaticMeshVertexBuffer follows the positions as `NumTexCoords, Stride, N,
    bUseFullPrecisionUVs, Stride, N`, then N vertices of two packed tangents (8 bytes)
    and NumTexCoords UVs (half floats, or floats with full precision); the first set is
    the texture's. Elements (`elements`): the FStaticMeshElement array just before the
    positions (`_elements`), as [(material object index, first triangle, triangles)].
    """
    import numpy as np
    b = package.blob(export)
    i = 0
    tag = struct.pack('<I', 12)
    while True:
        i = b.find(tag, i)
        if i < 0 or i + 16 > len(b):
            return None
        s, n, s2, n2 = struct.unpack_from('<4I', b, i)
        if s == 12 and s2 == 12 and n == n2 and 3 <= n <= MAX_VERTS and i + 16 + 12 * n <= len(b):
            break
        i += 1
    verts = np.frombuffer(b, np.float32, 3 * n, i + 16).reshape(-1, 3)
    j = i + 16 + 12 * n
    uvs = _uvs(b, j, n)
    tag = struct.pack('<I', 2)
    while True:
        j = b.find(tag, j)
        if j < 0 or j + 8 > len(b):
            return None
        es, m = struct.unpack_from('<2I', b, j)
        if es == 2 and m and m % 3 == 0 and j + 8 + 2 * m <= len(b):
            idx = np.frombuffer(b, np.uint16, m, j + 8)
            if int(idx.max()) == n - 1:
                return {'verts': verts, 'tris': idx.astype(np.int64).reshape(-1, 3),
                        'uvs': uvs, 'elements': _elements(b, i, m // 3, n)}
        j += 1


#: Bytes before the position buffer searched for the element array, and the largest
#: gap between that array's end and the position buffer.
ELEMENT_SCAN = 16384
ELEMENT_GAP = 16
#: The most elements a mesh may have.
MAX_ELEMENTS = 64
#: An element array's start: a count of 1..MAX_ELEMENTS, a material and three u32
#: bools (a lookahead, so overlapping starts are all found).
_ELEMENT_HEAD = re.compile(rb'(?=[\x01-\x40]\x00\x00\x00.{4}[\x00\x01]\x00\x00\x00'
                           rb'[\x00\x01]\x00\x00\x00[\x00\x01]\x00\x00\x00)', re.DOTALL)


def _uvs(b, at, n):
    """The first UV set of the FStaticMeshVertexBuffer at `at`, (N, 2), or None."""
    import numpy as np
    if at + 24 > len(b):
        return None
    k, stride, nv, full, es, count = struct.unpack_from('<6I', b, at)
    if not (1 <= k <= 8 and nv == n and count == n and es == stride and full in (0, 1)
            and stride == 8 + k * (8 if full else 4) and at + 24 + stride * n <= len(b)):
        return None
    raw = np.frombuffer(b, np.uint8, stride * n, at + 24).reshape(n, stride)
    if full:
        return raw[:, 8:16].copy().view('<f4').reshape(n, 2).astype(np.float64)
    return raw[:, 8:12].copy().view('<f2').reshape(n, 2).astype(np.float64)


def _elements(b, pos_at, ntri, nverts):
    """[(material, first triangle, triangles)] of the FStaticMeshElement array ending
    just before the position buffer at `pos_at`, or []: a count, then per element
    Material (object index), three u32 bools, FirstIndex, NumTriangles, MinVertexIndex,
    MaxVertexIndex, MaterialIndex, a fragment array (count, then BaseIndex and
    NumPrimitives each) and a u8 (LoadPS3Data, 0). Accepted only when the elements
    together cover the index buffer exactly (trace_omdd.covers)."""
    start = max(0, pos_at - ELEMENT_SCAN)
    for hit in _ELEMENT_HEAD.finditer(b, start, pos_at):
        o = hit.start()
        c, = struct.unpack_from('<I', b, o)
        p, out = o + 4, []
        try:
            for _ in range(c):
                mat, b1, b2, b3, first, count, lo, hi, _mi, nf = struct.unpack_from('<i9I', b, p)
                if b1 > 1 or b2 > 1 or b3 > 1 or first % 3 or nf > 4096 \
                        or (count and (lo > hi or hi >= nverts)) or b[p + 40 + 8 * nf]:
                    break
                p += 41 + 8 * nf
                out.append((mat, first // 3, count))
        except (struct.error, IndexError):
            continue
        if len(out) == c and 0 <= pos_at - p <= ELEMENT_GAP:
            from riftlord.formats.omdd.trace_omdd import covers
            out = [e for e in out if e[2]]
            if covers(out, ntri):
                return out
    return []


#: Largest vertex count a mesh may claim before its header is taken as noise.
MAX_VERTS = 2000000


def _transform(location, rotation, scale3d):
    """A row-major 4x4 from a UE3 location, rotator (ints) and scale."""
    import numpy as np
    from riftlord.formats.omd3 import omd3level
    rot = [v * 360.0 / ROTATOR_TURN for v in (rotation or (0, 0, 0))]
    return np.asarray(omd3level._matrix(location or (0.0, 0.0, 0.0), rot,
                                        scale3d or (1.0, 1.0, 1.0)), np.float64)


def _scaled(props, uniform, vector):
    s = props.get(uniform, 1.0)
    v = props.get(vector) or (1.0, 1.0, 1.0)
    return (v[0] * s, v[1] * s, v[2] * s)


#: Classes the export index keeps: what a map's components and materials reach.
INDEX_CLASSES = frozenset(('StaticMesh', 'Material', 'MaterialInstanceConstant',
                           'MaterialInstanceTimeVarying', 'Texture2D'))
#: Bump when the export index's contents change.
INDEX_VERSION = 1


_INDEX_LOCK = threading.Lock()
_INDEX = []


def export_index():
    """{lower-case object path: package name} for every StaticMesh, material and
    Texture2D any cooked package exports; of several packages, the smallest file.
    Built from the packages' tables alone, once per process (requests share it), and
    cached (gamepath.scratch('trace')) against the folder's file count and newest
    change."""
    with _INDEX_LOCK:
        if not _INDEX:
            _INDEX.append(_build_index())
        return _INDEX[0]


def _build_index():
    import json
    folder = os.path.join(install(), COOKED)
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith('.upk'))
    sizes = {f: os.path.getsize(os.path.join(folder, f)) for f in files}
    stamp = [INDEX_VERSION, len(files),
             max(os.path.getmtime(os.path.join(folder, f)) for f in files) if files else 0]
    path = os.path.join(gamepath.scratch('trace'), 'omdu-exports.json')
    try:
        with open(path, encoding='utf-8') as fh:
            got = json.load(fh)
        if got.get('stamp') == stamp:
            return got['index']
    except (OSError, ValueError):
        pass
    index = {}
    for f in sorted(files, key=lambda f: sizes[f]):
        try:
            pkg = Package(os.path.join(folder, f), tables_only=True)
        except (OSError, ValueError, struct.error, IndexError):
            continue
        for i, e in enumerate(pkg.exports):
            if e['class'] in INDEX_CLASSES:
                index.setdefault(pkg.path(i + 1).lower(), f[:-4])
    tmp = '%s.%d.part' % (path, os.getpid())
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump({'stamp': stamp, 'index': index}, fh)
    os.replace(tmp, path)
    return index


@functools.lru_cache(maxsize=6)
def package(name):
    folder = install()
    if not folder:
        raise SystemExit('Orcs Must Die! Unchained is not installed')
    path = os.path.join(folder, COOKED, name + '.upk')
    if not os.path.isfile(path):
        low = {f.lower(): f for f in os.listdir(os.path.join(folder, COOKED))}
        real = low.get(name.lower() + '.upk')
        if not real:
            raise SystemExit('no such Unchained package: %r' % (name,))
        path = os.path.join(folder, COOKED, real)
    return Package(path)


def levels():
    """Unchained's maps: packages that stream a `_Geo` or `_Audio` sublevel."""
    folder = install()
    if not folder:
        return []
    names = {f[:-4] for f in os.listdir(os.path.join(folder, COOKED)) if f.endswith('.upk')}
    low = {n.lower() for n in names}
    return sorted(n for n in names if n.lower() + '_audio' in low or n.lower() + '_geo' in low)


def streamed(pkg):
    """Package names a persistent level streams (LevelStreaming*.PackageName)."""
    out = []
    for e in pkg.exports:
        if e['class'].startswith('LevelStreaming'):
            got = pkg.props(e)[0].get('PackageName')
            if got and not any(s in got.lower() for s in SKIP_STREAMED):
                out.append(got)
    return out


def level_instances(pkg):
    """[(mesh object index, 4x4, [material object index or 0 per element])] for every
    visible StaticMeshComponent placed in one package; the materials are the
    component's `Materials` overrides."""
    out = []
    for e in pkg.exports:
        if e['class'] != 'StaticMeshComponent' or e['objectFlags'] & RF_TEMPLATE:
            continue
        if 'PersistentLevel' not in pkg.path(e['outer']):
            continue
        props, _ = pkg.props(e)
        if props.get('HiddenGame') or not props.get('StaticMesh'):
            continue
        local = _transform(props.get('Translation'), props.get('Rotation'),
                           _scaled(props, 'Scale', 'Scale3D'))
        owner = pkg.exports[e['outer'] - 1] if e['outer'] > 0 else None
        world = local
        if owner is not None and owner['class'] != 'StaticMeshCollectionActor':
            op, _ = pkg.props(owner)
            if op.get('bHidden'):
                continue
            world = _transform(op.get('Location'), op.get('Rotation'),
                               _scaled(op, 'DrawScale', 'DrawScale3D')) @ local
        out.append((props['StaticMesh'], world, pkg.object_array(props.get('Materials'))))
    return out


# -- textures -----------------------------------------------------------------------
#
# A material's base colour map: a MaterialInstanceConstant's TextureParameterValues
# (ParameterName, ParameterValue), then its Parent's; a Material's texture sample
# expressions (Expressions). Of several, the best by `base_score`: a parameter named for
# the diffuse map wins whatever its texture is called (WoodenFloors_inst's DiffuseMap is
# WoodenFloors_sp), then a texture named as one (Column3base_mat_inst's BaseAOMap is
# Column3_base_df); detail, normal, specular, mask and emissive maps lose.
#
# A cooked Texture2D (file version 870): tagged properties (SizeX, SizeY, Format as an
# EPixelFormat name, TextureFileCacheName), then the SourceArt bulk data header (empty
# when cooked), then the mips: a count, then per mip a bulk data header (flags,
# element count, size on disk, offset in file), its payload when it is inline, SizeX
# and SizeY. Flag 0x01: the payload is in `<TextureFileCacheName>.tfc` at the offset;
# 0x10: the payload is LZO compressed in the package chunk format; 0x20: no payload.

#: A parameter name for a base colour map; a texture name for one (its suffix); words
#: in either that rule one out.
BASE_PARAM = re.compile(r'(diff|albedo|base ?colou?r|^colou?r|^bc(_\d+)?$)', re.I)
BASE_TEXTURE = re.compile(r'(_df|_da|_db|_d|_bc|_c|_diff|_diffuse|_albedo|_color|_colour)'
                          r'(_clean|_light|_dark|_\d+)?$', re.I)
NOT_BASE_PARAM = re.compile(r'(norm|spec|mask|emis|rough|metal|ao(map)?$|^ao|occlu|height|'
                            r'bump|noise|cube|lightmap|overlay|^mra|^mrh|^orm|^nm|^mk|^em$)',
                            re.I)
NOT_BASE_TEXTURE = re.compile(r'(_nm|_n|_normal|_sp|_s|_spec|_mk\d*|_mask|_em|_emissive|_xf|'
                              r'_op|_mra|_mrh|_orm|_ao|_h|_height|_rough|_roughness|_metal|'
                              r'_metallic|_noise|_cube)(_\d+)?$', re.I)
DETAIL = re.compile(r'detail|_hi_', re.I)
#: A parameter that holds an ambient occlusion map or a mask: never a base colour,
#: whatever its texture is called (the castle kits' BaseAOMap holds `<piece>_df`, a flat
#: grey; RGBMask holds `blank_df`).
NEVER_PARAM = re.compile(r'ao(map)?$|^ao|occlu|mask', re.I)
#: A mesh-paint layer (Red/Green/Blue DiffuseMap, blended by vertex colour): ranked below
#: a detail map, which every placement shows.
PAINT_LAYER = re.compile(r'^(red|green|blue|alpha)(diffuse|colou?r)', re.I)
#: A placeholder texture (`mastertextures.texture.blank_df`, plain white).
BLANK = re.compile(r'(^|[._])blank', re.I)
#: base_score of a NEVER_PARAM parameter.
NEVER = -10
#: The scalar parameter of the tiling of a detail map, in repeats per uu: the master
#: materials sample DetailDiffuseMap at world scale (DetailScale 0.005..0.0075 per uu,
#: far below one repeat per UV).
DETAIL_SCALE = 'DetailScale'
#: Bulk data flags.
BULK_SEPARATE = 0x01
BULK_LZO = 0x10
BULK_UNUSED = 0x20
#: Longest side of the mip read for the Trace tool's textures.
TEXTURE_MAX = 256
#: Unchained pixel formats as texture.py names them.
PIXEL_FORMATS = {'PF_DXT1': 'PF_DXT1', 'PF_DXT3': 'PF_DXT3', 'PF_DXT5': 'PF_DXT5',
                 'PF_A8R8G8B8': 'PF_B8G8R8A8', 'PF_G8': 'PF_G8'}


def base_score(param, name):
    """How strongly (parameter name, texture name) reads as a base colour map; below 0
    is never one."""
    if NEVER_PARAM.search(param or ''):
        return NEVER
    by_param = bool(BASE_PARAM.search(param or ''))
    by_name = bool(BASE_TEXTURE.search(name or ''))
    score = 3 * by_param + 2 * by_name
    if NOT_BASE_TEXTURE.search(name or '') and not by_param:
        score -= 5
    if NOT_BASE_PARAM.search(param or '') and not by_name:
        score -= 5
    if DETAIL.search(param or '') or DETAIL.search(name or ''):
        score -= 5
    if PAINT_LAYER.search(param or '') or BLANK.search(name or ''):
        score -= 6
    return score


#: An ambient occlusion parameter (fallback_rank).
AO_PARAM = re.compile(r'ao(map)?$|^ao|occlu', re.I)


def fallback_rank(param, name):
    """With no candidate scoring 0 or more: a detail map (4), a paint layer (3), an
    ambient occlusion map (2), a placeholder (1); 0 is never used."""
    param, name = param or '', name or ''
    if BLANK.search(name):
        return 1 if BASE_TEXTURE.search(name) else 0
    if DETAIL.search(param) or DETAIL.search(name):
        return 4
    if PAINT_LAYER.search(param):
        return 3
    if AO_PARAM.search(param):
        return 2
    return 0


def pick_base(candidates, fallback=True):
    """The base colour map of [(parameter name, texture name, value)], or None: the best
    `base_score`, the first of equals; with none at 0 or more, the best fallback_rank
    (with `fallback`)."""
    scored = [(base_score(p, n), -i, v) for i, (p, n, v) in enumerate(candidates)]
    good = [s for s in scored if s[0] >= 0]
    if good:
        return max(good, key=lambda s: (s[0], s[1]))[2]
    if not fallback:
        return None
    ranked = [(fallback_rank(p, n), -i, v) for i, (p, n, v) in enumerate(candidates)]
    ranked = [r for r in ranked if r[0] > 0]
    return max(ranked, key=lambda r: (r[0], r[1]))[2] if ranked else None


class Objects:
    """Objects across one map's packages, resolving an import by its path."""

    def __init__(self, pkgs):
        self.where = {}
        self.paths = {}
        for pkg in pkgs:
            self.add(pkg)

    def path(self, hit):
        """The lower-case path of a (package, export)."""
        return self.paths[id(hit[1])]

    def add(self, pkg):
        for i, e in enumerate(pkg.exports):
            path = pkg.path(i + 1).lower()
            self.where.setdefault(path, (pkg, e))
            self.paths.setdefault(id(e), path)

    def get(self, pkg, index):
        """(package, export) for an object index read in `pkg`, or None. An import no
        package of the map exports is looked up in the export index (`export_index`)."""
        if not index:
            return None
        if index > 0:
            return (pkg, pkg.exports[index - 1]) if index <= len(pkg.exports) else None
        if -index > len(pkg.imports):
            return None
        path = pkg.path(index).lower()
        if path not in self.where:
            other = export_index().get(path)
            if other:
                try:
                    self.add(package(other))
                except (SystemExit, OSError, ValueError):
                    pass
            self.where.setdefault(path, None)
        return self.where.get(path)

    def material_texture(self, pkg, index):
        """(package, Texture2D export) a material object uses as its base colour."""
        got = self.material_base(pkg, index)
        return got[0] if got else None

    def material_base(self, pkg, index):
        """((package, Texture2D export), parameter name) of a material object's base
        colour, or None; the parameter is '' for a bare texture or sample. The first
        level of the parent chain with a candidate scoring 0 or more decides; with none
        anywhere, the best fallback_rank over the whole chain, nearest level first."""
        got = self._base(pkg, index, 0, None)
        if got:
            return got
        every = []
        self._base(pkg, index, 0, every)
        return pick_base(every)

    def _base(self, pkg, index, depth, every):
        """material_base's first pass; with a list `every`, collects every candidate
        into it instead and returns None."""
        hit = self.get(pkg, index)
        if not hit or depth > 8:
            return None
        p, e = hit
        if e['class'] == 'Texture2D':
            if every is not None:
                every.append(('', e['name'], (hit, '')))
                return None
            return hit, ''
        props, _ = p.props(e)
        cands = []
        for s in p.struct_array(props.get('TextureParameterValues')):
            t = self.get(p, s.get('ParameterValue')) if isinstance(s.get('ParameterValue'), int) else None
            if t and t[1]['class'] == 'Texture2D':
                param = str(s.get('ParameterName') or '')
                cands.append((param, t[1]['name'], (t, param)))
        got = pick_base(cands, fallback=False) if every is None else every.extend(cands)
        if got:
            return got
        if isinstance(props.get('Parent'), int) and props['Parent']:
            got = self._base(p, props['Parent'], depth + 1, every)
            if got:
                return got
        cands = []
        for x in p.object_array(props.get('Expressions')):
            ex = self.get(p, x)
            if not ex or 'TextureSample' not in ex[1]['class']:
                continue
            xp, _ = ex[0].props(ex[1])
            t = self.get(ex[0], xp.get('Texture')) if isinstance(xp.get('Texture'), int) else None
            if t and t[1]['class'] == 'Texture2D':
                param = str(xp.get('ParameterName') or '')
                cands.append((param, t[1]['name'], (t, param)))
        if every is not None:
            every.extend(cands)
            return None
        return pick_base(cands, fallback=False)

    def scalar(self, pkg, index, name, depth=0):
        """A material object's scalar parameter `name`: its own value, else its parent's,
        else the master material's default; None when no level of it names one."""
        hit = self.get(pkg, index)
        if not hit or depth > 8:
            return None
        p, e = hit
        props, _ = p.props(e)
        for s in p.struct_array(props.get('ScalarParameterValues')):
            if str(s.get('ParameterName') or '') == name and isinstance(
                    s.get('ParameterValue'), float):
                return s['ParameterValue']
        if isinstance(props.get('Parent'), int) and props['Parent']:
            got = self.scalar(p, props['Parent'], name, depth + 1)
            if got is not None:
                return got
        for x in p.object_array(props.get('Expressions')):
            ex = self.get(p, x)
            if not ex or ex[1]['class'] != 'MaterialExpressionScalarParameter':
                continue
            xp, _ = ex[0].props(ex[1])
            if str(xp.get('ParameterName') or '') == name:
                v = xp.get('DefaultValue')
                return float(v) if isinstance(v, float) else 0.0
        return None


def texture_image(pkg, export, why=None):
    """A PIL image of a cooked Texture2D's largest mip within TEXTURE_MAX, or None; with
    a list `why`, the reason each mip tried was refused is appended to it (format,
    mip table, .tfc, LZO, short data, decode)."""
    from riftlord.formats.ue import texture as uetex
    why = why if why is not None else []
    props, pos = pkg.props(export)
    fmt = props.get('Format')
    fmt = pkg.fname(Reader(fmt)) if isinstance(fmt, bytes) and len(fmt) == 8 else None
    if not PIXEL_FORMATS.get(fmt):
        why.append('format %s not decoded' % fmt)
        return None
    fmt = PIXEL_FORMATS[fmt]
    b = pkg.blob(export)
    r = Reader(b, pos)
    flags, _n, size, _off = struct.unpack_from('<4i', b, r.pos)
    r.pos += 16
    if not flags & (BULK_SEPARATE | BULK_UNUSED) and size > 0:
        r.pos += size
    count = r.i32()
    if not 0 < count <= 16:
        why.append('mip table unreadable (%d mips)' % count)
        return None
    mips = []
    for _ in range(count):
        flags, elements, size, offset = struct.unpack_from('<4i', b, r.pos)
        r.pos += 16
        inline = None
        if not flags & (BULK_SEPARATE | BULK_UNUSED) and size > 0:
            inline = r.pos
            r.pos += size
        w, h = r.i32(), r.i32()
        mips.append((w, h, flags, elements, size, offset, inline))
    order = sorted(mips, key=lambda m: (max(m[0], m[1]) > TEXTURE_MAX, -max(m[0], m[1])
                                        if max(m[0], m[1]) <= TEXTURE_MAX else max(m[0], m[1])))
    cache = props.get('TextureFileCacheName')
    for w, h, flags, elements, size, offset, inline in order:
        if flags & BULK_UNUSED or size <= 0 or w <= 0 or h <= 0:
            continue
        mip = '%dx%d' % (w, h)
        if inline is not None:
            raw = b[inline:inline + size]
        elif cache:
            try:
                raw = _tfc(str(cache), offset, size)
            except OSError as exc:
                why.append('%s: %s.tfc not read (%s)' % (mip, cache, exc))
                continue
        else:
            why.append('%s: stored outside the package and no .tfc named' % mip)
            continue
        if flags & BULK_LZO:
            try:
                raw = pkg._chunk(raw, 0, elements)
            except (OSError, ValueError, struct.error, IndexError, SystemExit) as exc:
                why.append('%s: LZO failed (%s)' % (mip, exc))
                continue
        want = uetex.mip_bytes(w, h, fmt)
        if want is None or len(raw) < want:
            why.append('%s: %d bytes, %s needed' % (mip, len(raw), want))
            continue
        try:
            return uetex.to_image({'width': w, 'height': h, 'format': fmt, 'data': raw[:want]})
        except Exception as exc:                     # noqa: BLE001
            why.append('%s: decode failed (%s)' % (mip, exc))
            continue
    if not why:
        why.append('no stored mip')
    return None


def _tfc(name, offset, size):
    """`size` bytes at `offset` of the texture file cache `<name>.tfc`."""
    folder = os.path.join(install(), COOKED)
    path = os.path.join(folder, name + '.tfc')
    if not os.path.isfile(path):
        low = {f.lower(): f for f in os.listdir(folder)}
        path = os.path.join(folder, low.get(name.lower() + '.tfc', name + '.tfc'))
    with open(path, 'rb') as fh:
        fh.seek(offset)
        return fh.read(size)


def uu_per_uv(got, first, count):
    """Mesh units per UV unit over triangles first..first+count of a `mesh` result: the
    square root of their total area over their total UV area; None without UVs."""
    import numpy as np
    uvs = got.get('uvs')
    if uvs is None:
        return None
    t = got['tris'][first:first + count]
    if not len(t):
        return None
    v, u = np.asarray(got['verts'], np.float64), np.asarray(uvs, np.float64)
    area = np.linalg.norm(np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]]), axis=1).sum()
    a, b = u[t[:, 1]] - u[t[:, 0]], u[t[:, 2]] - u[t[:, 0]]
    uv_area = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum()
    if uv_area <= 0 or area <= 0:
        return None
    return float(np.sqrt(area / uv_area))


def read(name):
    """(models, insts, None, texture) for one map in Unreal space (trace.py's reader
    protocol). A model is keyed by its mesh's path, plus `|` and the material paths
    when the component overrides them; a texture by its Texture2D's path. A section
    whose base colour is a detail map carries `uvScale`, its UVs' multiplier for the
    map's world tiling (DETAIL_SCALE)."""
    from riftlord.mapeditor import trace
    if name not in levels():
        raise SystemExit('no such Unchained level: %r' % (name,))
    root = package(name)
    pkgs = [root]
    for pname in streamed(root):
        try:
            pkgs.append(package(pname))
        except SystemExit:
            continue
    # a sublevel's meshes are often imports of the persistent level's own exports
    objects = Objects(pkgs)
    raw, models, insts, textures = {}, {}, [], {}

    def texture_of(pkg, material):
        """(texture key, detail tiling in repeats per uu or None) of a material."""
        got = objects.material_base(pkg, material)
        if not got:
            return None, None
        hit, param = got
        key = objects.path(hit)
        textures.setdefault(key, hit)
        tiling = objects.scalar(pkg, material, DETAIL_SCALE) if DETAIL.search(param) else None
        return key, tiling or None

    for pkg in pkgs:
        for index, m, overrides in level_instances(pkg):
            mpath = pkg.path(index).lower()
            hit = objects.get(pkg, index)
            if hit is None or hit[1]['class'] != 'StaticMesh':
                insts.append((mpath, m))
                continue
            if mpath not in raw:
                raw[mpath] = mesh(*hit)
            got = raw[mpath]
            over = [(pkg, o) for o in overrides]
            key = mpath
            if any(o for _p, o in over):
                key = '%s|%s' % (mpath, ','.join(pkg.path(o).lower() if o else '' for _p, o in over))
            if got is not None and key not in models:
                sections = []
                for k, (mat, first, count) in enumerate(got['elements']):
                    src = over[k] if k < len(over) and over[k][1] else (hit[0], mat)
                    tex, tiling = texture_of(*src) if src[1] else (None, None)
                    sec = {'first': first, 'tris': count, 'texture': tex}
                    if tiling:
                        span = uu_per_uv(got, first, count)
                        if span:
                            sec['uvScale'] = span * tiling
                    sections.append(sec)
                models[key] = trace._model(got['verts'], got['tris'], got['uvs'], sections)
            insts.append((key, m))

    def texture(key):
        hit = textures.get(key)
        image = texture_image(*hit) if hit else None
        return trace._png(image) if image is not None else None

    def reason(key):
        """Why texture(key) gives nothing (trace.check)."""
        hit = textures.get(key)
        if not hit:
            return 'not found in the map\'s packages'
        why = []
        texture_image(*hit, why=why)
        return '; '.join(why[-2:])
    texture.reason = reason
    return models, insts, None, texture
