"""Orcs Must Die! Deathtrap levels for the Trace tool (riftlord/trace.py).

Deathtrap (Steam `Orcs Must Die Deathtrap`) is Unreal Engine 5.3, cooked to IoStore with
unversioned properties. Measured 2026-10-17 on the Steam install; read without any
engine tool, but the property layouts come from a `.usmap` the user dumped with UE4SS
(`OMDD/Binaries/Win64/Mappings.usmap`).

Containers (`OMDD/Content/Paks/*.utoc` + `.ucas`):

- FIoStoreTocHeader: magic `-==--==--==--==-`, version 5 (PerfectHashWithOverflow),
  header size 144, then entry count, compressed block count and entry size (12),
  compression method count and name length (32), block size (64 KiB), directory index
  size, partition count, container id, encryption guid, container flags (0x9:
  compressed + indexed, not encrypted), perfect hash seed count, partition size,
  chunks-without-hash count.
- After the header: chunk ids (12 bytes: u64 id, big-endian u16 index, pad, u8 type),
  offset/lengths (two 5-byte big-endian numbers, in the container's uncompressed
  address space), hash seeds (4 each), no-hash indices (4 each), compression blocks
  (5-byte offset in the .ucas, 3-byte compressed size, 3-byte size, method byte: 0 none,
  else 1-based into the method names; every method here is `LZ4`), method names, then
  the directory index (mount point, directories, files, strings; a file's user data is
  its chunk's TOC index).
- Chunk types used: 1 export bundle data (the package itself), 2 bulk data (`.ubulk`),
  5 script objects (in `global.utoc`). A chunk's id is its package id, so imported
  package ids resolve through every container's chunk ids.
- `global.utoc`'s script objects chunk: a name batch, then (name, global index, outer,
  CDO class) per native object, 32 bytes each, giving native class names by
  FPackageObjectIndex.

Zen packages (UE 5.3):

- FZenPackageSummary, 52 bytes: has-versioning (0 here), header size, name (mapped
  name), package flags, cooked header size, then offsets of the imported public export
  hashes, import map, export map, export bundle entries, dependency bundle headers and
  entries, imported package names. Then the name batch (count, string bytes, hash
  version, a u64 hash per name, a big-endian u16 header per name: top bit UTF-16,
  low 15 bits length, then the strings), then the bulk data map (u64 byte size, 32
  bytes an entry).
- An FPackageObjectIndex is u64: top two bits 0 export, 1 script import, 2 package
  import, 3 null; a package import is (imported package index << 32 | public export
  hash index). The imported package is named by the header's imported package names
  (a name batch at ImportedPackageNamesOffset, then a u32 number per name), and the
  export is the one in it whose PublicExportHash is that hash.
- FExportMapEntry, 72 bytes: serial offset, serial size, object name, outer, class,
  super, template, public export hash, object flags, filter flags. An export's data
  is at header size + serial offset (serial offsets start at 0, in export order).
- Package names come from container paths: `OMDD/Content/X` is `/Game/X`, a plugin's
  `<Plugin>/Content/X` is `/<Plugin>/X`, `Engine/Content/X` is `/Engine/X`.

Unversioned properties (UE 5.3):

- A u16 fragment stream: low 7 bits skip count, bit 7 has-zeroes, bit 8 last, top 7
  bits value count; then a zero mask over the has-zeroes fragments' values (u8 when
  8 or fewer bits, u16 for 16 or fewer, else u32 words); then the non-zero values in
  schema order. A class's schema index counts its OWN properties first (the usmap's
  property count, with holes for ArrayDim), then its super's, and so on up.
- A zero-masked value is all zero bits (False, 0, a zero vector, a null reference),
  not the default; a property missing from the stream is the template's.
- FVector, FRotator, FQuat are doubles (large world coordinates); a bool is one byte;
  an object reference is an i32: positive is export n - 1, negative is import map
  entry -n - 1.
- 5,747 of 5,747 StaticMeshComponents of Level_Castle_CastlePlaza_Geo read.

Levels:

- 252 `.umap`, classic sublevels (no World Partition). A map is listed when it has a
  `<map>_Geo*` sibling (17 maps); its geometry is the persistent level plus its
  `_Geo*` and `_Props*` sublevels (a sublevel whose own name is a listed map, as
  `Level_Castle_BackAlley_Interior` under `Level_Castle_BackAlley`, belongs to that
  map instead). `_Vista*` (distant backdrop, 2,103 of CastlePlaza's 7,674 placements,
  out to 147,000 uu), lighting, audio, perf and trap grid sublevels are left out.
  `_Props` holds breakable GeometryCollections, which are not drawn.
- Placements are StaticMeshComponents, InstancedStaticMeshComponents and their
  hierarchical kind; painted foliage (FoliageInstancedStaticMeshComponent) is left
  out, as the OMD3 source leaves it out. A component's properties are its template's
  (`TemplateIndex`, a Blueprint's `_GEN_VARIABLE` component or the class default) overlaid by its own; its
  world transform is its RelativeLocation / RelativeRotation / RelativeScale3D under
  its `AttachParent` chain. ISM instances (`PerInstanceSMData`, skipped by the property
  stream and bulk-serialized after it as `(128, N)` then N FMatrix of doubles) are
  relative to their component.
- StaticMesh render data is found by its buffers' headers as in OMD3 and Unchained:
  FPositionVertexBuffer `(12, N, 12, N)` then N float3, then the first index buffer
  `(32-bit flag, 1, bytes)` whose largest index is N - 1; searched in the mesh's
  export data and its `.ubulk` (streamed LODs), keeping the LOD with the most
  vertices. Nanite meshes carry their fallback mesh there. Every map reads with 0-2
  meshes not found.
- Already Unreal space (left-handed, Z up). 1:1 with OMD3, measured: Deathtrap ships
  OMD3's kit, and Portcullis_Sill_3W (1,013 x 69 x 10 uu) and Column_BaseD_CastStone
  (300 x 300 x 92.5 uu) have the same extents in both games.
"""

import ctypes
import ctypes.util
import functools
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from riftlord.core import gamepath  # noqa: E402

#: Deathtrap's Steam folder.
GAME_FOLDER = 'Orcs Must Die Deathtrap'
#: Containers and property map, inside the install.
PAKS = ('OMDD', 'Content', 'Paks')
USMAP = ('OMDD', 'Binaries', 'Win64', 'Mappings.usmap')
TOC_MAGIC = b'-==--==--==--==-'
#: EIoChunkType values used here.
CHUNK_EXPORTS, CHUNK_BULK, CHUNK_SCRIPT = 1, 2, 5
#: FPackageObjectIndex kinds (top two bits).
IDX_EXPORT, IDX_SCRIPT, IDX_PACKAGE, IDX_NULL = 0, 1, 2, 3
#: usmap magic and EPropertyType order.
USMAP_MAGIC = 0x30C4
KINDS = ('Byte', 'Bool', 'Int', 'Float', 'Object', 'Name', 'Delegate', 'Double',
         'Array', 'Struct', 'Str', 'Text', 'Interface', 'MulticastDelegate',
         'WeakObject', 'LazyObject', 'AssetObject', 'SoftObject', 'UInt64',
         'UInt32', 'UInt16', 'Int64', 'Int16', 'Int8', 'Map', 'Set', 'Enum',
         'FieldPath', 'OptionalProperty', 'Utf8Str', 'AnsiStr')
#: Sublevel name parts holding geometry (after `<map>_`).
GEO_PARTS = ('Geo', 'Props')
#: Component classes placed as meshes.
MESH_COMPONENTS = ('StaticMeshComponent', 'InstancedStaticMeshComponent',
                   'HierarchicalInstancedStaticMeshComponent')
#: Largest vertex count a mesh may claim before its header is taken as noise.
MAX_VERTS = 4000000


def install():
    """Deathtrap's install folder, or None."""
    return gamepath.other_game(GAME_FOLDER)


def usmap_path():
    """The dumped property map, or None."""
    folder = install()
    path = os.path.join(folder, *USMAP) if folder else None
    return path if path and os.path.isfile(path) else None


def missing():
    """Why Deathtrap cannot be read ('' when it can)."""
    if not install():
        return 'Orcs Must Die! Deathtrap is not installed'
    if not usmap_path():
        return ('no property map: dump Mappings.usmap from the running game with UE4SS '
                'into OMDD/Binaries/Win64 (workshop/research/TRACE-SOURCES.md)')
    return ''


# -- LZ4 ----------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _liblz4():
    name = ctypes.util.find_library('lz4')
    for cand in ([name] if name else []) + ['liblz4.so.1', 'liblz4.dylib', 'lz4.dll']:
        try:
            lib = ctypes.CDLL(cand)
            lib.LZ4_decompress_safe.restype = ctypes.c_int
            lib.LZ4_decompress_safe.argtypes = (ctypes.c_char_p, ctypes.c_char_p,
                                                ctypes.c_int, ctypes.c_int)
            return lib
        except OSError:
            continue
    return None


def lz4(src, size):
    lib = _liblz4()
    if lib is None:
        from riftlord.formats.ue import lz4block
        return lz4block.decompress(bytes(src), size)
    out = ctypes.create_string_buffer(size)
    got = lib.LZ4_decompress_safe(bytes(src), out, len(src), size)
    if got != size:
        raise SystemExit('bad LZ4 block (%d of %d bytes)' % (got, size))
    return out.raw


# -- containers ---------------------------------------------------------------------

def _be(b):
    return int.from_bytes(b, 'big')


def _fstring(b, o):
    n, = struct.unpack_from('<i', b, o)
    o += 4
    if n == 0:
        return '', o
    if n < 0:
        return b[o:o - 2 * n].decode('utf-16-le', 'replace').rstrip('\0'), o - 2 * n
    return b[o:o + n].decode('latin-1').rstrip('\0'), o + n


class Container:
    """One .utoc/.ucas pair: chunk ids, chunk reads and file paths."""

    def __init__(self, utoc):
        self.utoc = utoc
        self.ucas = utoc[:-5] + '.ucas'
        b = open(utoc, 'rb').read()
        if b[:16] != TOC_MAGIC:
            raise SystemExit('not an IoStore TOC: %s' % utoc)
        (hsize, n, nblocks, bsize, ncomp, complen, self.block,
         dirsize, _parts) = struct.unpack_from('<9I', b, 20)
        flags = b[80]
        seeds, = struct.unpack_from('<I', b, 84)
        nohash, = struct.unpack_from('<I', b, 96)
        if flags & 0x2:
            raise SystemExit('encrypted container: %s' % utoc)
        o = hsize
        ids = b[o:o + 12 * n]
        o += 12 * n
        self.ids = [ids[12 * i:12 * i + 12] for i in range(n)]
        ol = b[o:o + 10 * n]
        o += 10 * n
        self.spans = [(_be(ol[10 * i:10 * i + 5]), _be(ol[10 * i + 5:10 * i + 10]))
                      for i in range(n)]
        o += 4 * seeds + 4 * nohash
        bl = b[o:o + bsize * nblocks]
        o += bsize * nblocks
        self.blocks = [(int.from_bytes(bl[12 * i:12 * i + 5], 'little'),
                        int.from_bytes(bl[12 * i + 5:12 * i + 8], 'little'),
                        int.from_bytes(bl[12 * i + 8:12 * i + 11], 'little'),
                        bl[12 * i + 11]) for i in range(nblocks)]
        self.methods = [b[o + complen * i:o + complen * (i + 1)].split(b'\0')[0].decode()
                        for i in range(ncomp)]
        o += complen * ncomp
        if flags & 0x4:
            hs, = struct.unpack_from('<i', b, o)
            o += 4 + 2 * hs + 20 * nblocks
        self.files = {}
        if flags & 0x8 and dirsize:
            self._directory(b[o:o + dirsize])
        # chunk lookup: (package id, type) -> toc index
        self.by_id = {}
        for i, cid in enumerate(self.ids):
            self.by_id.setdefault((cid[:8], cid[11]), i)

    def _directory(self, d):
        mount, p = _fstring(d, 0)
        nd, = struct.unpack_from('<i', d, p)
        p += 4
        dirs = [struct.unpack_from('<4I', d, p + 16 * i) for i in range(nd)]
        p += 16 * nd
        nf, = struct.unpack_from('<i', d, p)
        p += 4
        fls = [struct.unpack_from('<3I', d, p + 12 * i) for i in range(nf)]
        p += 12 * nf
        ns, = struct.unpack_from('<i', d, p)
        p += 4
        strs = []
        for _ in range(ns):
            s, p = _fstring(d, p)
            strs.append(s)
        none = 0xFFFFFFFF
        stack = [(0, mount)]
        while stack:
            di, prefix = stack.pop()
            name, child, sib, f = dirs[di]
            here = prefix + ('' if name == none else strs[name] + '/')
            while f != none:
                fn, nxt, user = fls[f]
                self.files[here + strs[fn]] = user
                f = nxt
            c = child
            while c != none:
                stack.append((c, here))
                c = dirs[c][2]

    def read(self, index):
        """One chunk's bytes, decompressed."""
        off, size = self.spans[index]
        if not size:
            return b''
        first = off // self.block
        last = (off + size - 1) // self.block
        out = []
        with open(self.ucas, 'rb') as fh:
            for bi in range(first, last + 1):
                coff, csize, usize, meth = self.blocks[bi]
                fh.seek(coff)
                raw = fh.read(csize)
                if meth == 0:
                    out.append(raw[:usize])
                elif self.methods[meth - 1].lower() == 'lz4':
                    out.append(lz4(raw, usize))
                else:
                    raise SystemExit('unsupported compression %r in %s'
                                     % (self.methods[meth - 1], self.utoc))
        data = b''.join(out)
        start = off - first * self.block
        return data[start:start + size]


def name_batch(b, o):
    """([names], end offset) of a Zen name batch at `o`."""
    n, = struct.unpack_from('<i', b, o)
    if n <= 0:
        return [], o + 4
    o += 16 + 8 * n
    heads = struct.unpack_from('>%dH' % n, b, o)
    o += 2 * n
    out = []
    for h in heads:
        ln = h & 0x7FFF
        if h & 0x8000:
            out.append(b[o:o + 2 * ln].decode('utf-16-le', 'replace'))
            o += 2 * ln
        else:
            out.append(b[o:o + ln].decode('latin-1'))
            o += ln
    return out, o


def package_name(path):
    """The package name ('/Game/...') of a container file path, or None."""
    p = path.replace('\\', '/')
    while p.startswith('../'):
        p = p[3:]
    p = os.path.splitext(p)[0]
    parts = p.split('/')
    if 'Content' not in parts:
        return None
    i = parts.index('Content')
    if i == 1 and parts[0] == 'OMDD':
        root = 'Game'
    elif i == 1 and parts[0] == 'Engine':
        root = 'Engine'
    elif i >= 2:
        root = parts[i - 1]                          # a plugin's own mount point
    else:
        return None
    return '/' + '/'.join([root] + parts[i + 1:])


class Game:
    """Every container of the install, indexed by package name, plus native objects."""

    def __init__(self, folder):
        paks = os.path.join(folder, *PAKS)
        self.containers = [Container(os.path.join(paks, f)) for f in sorted(os.listdir(paks))
                           if f.endswith('.utoc')]
        self.files = {}                              # package name lower -> (container, index)
        self.maps = {}                               # map name -> package name
        for c in self.containers:
            for path, index in c.files.items():
                if not path.endswith(('.uasset', '.umap')):
                    continue
                name = package_name(path)
                if name:
                    self.files.setdefault(name.lower(), (c, index))
                    if path.endswith('.umap'):
                        self.maps.setdefault(name.rsplit('/', 1)[1], name)
        self.script = {}
        for c in self.containers:
            i = c.by_id.get((bytes(8), CHUNK_SCRIPT))
            if i is not None:
                self._script(c.read(i))
        self._packages = {}
        self.types = Usmap(usmap_path())

    def _script(self, b):
        names, o = name_batch(b, 0)
        n, = struct.unpack_from('<i', b, o)
        o += 4
        for k in range(n):
            ni, _num, gidx = struct.unpack_from('<IIQ', b, o + 32 * k)
            self.script[gidx] = names[ni & 0x3FFFFFFF]

    def package(self, name):
        """The Package named `name`, or None when no container holds it."""
        key = name.lower()
        if key not in self._packages:
            hit = self.files.get(key)
            self._packages[key] = Package(self, name, *hit) if hit else None
        return self._packages[key]

    def bulk(self, pkg):
        """The package's `.ubulk` chunk, or b''."""
        cid = pkg.container.ids[pkg.index][:8]
        for c in [pkg.container] + self.containers:
            i = c.by_id.get((cid, CHUNK_BULK))
            if i is not None:
                return c.read(i)
        return b''


class Ref:
    """An object reference (FPackageIndex) read in one package."""
    __slots__ = ('pkg', 'index')

    def __init__(self, pkg, index):
        self.pkg, self.index = pkg, index

    def resolve(self):
        """(package, export index) or None."""
        if self.index > 0:
            return self.pkg, self.index - 1
        if self.index < 0:
            return self.pkg.import_target(-self.index - 1)
        return None


class Package:
    """One Zen package: names, imports, exports and their data."""

    def __init__(self, game, name, container, index):
        self.game, self.name, self.container, self.index = game, name, container, index
        b = self.data = container.read(index)
        (_ver, self.header, _n, _ni, _flags, self.cooked_header, hashes_at, imports_at,
         exports_at, bundles_at, dep_at, _dep2, impnames_at) = struct.unpack_from(
             '<IIIIIIiiiiiii', b, 0)
        self.names, o = name_batch(b, 52)
        #: the bulk data map: (offset in the .ubulk, duplicate offset, size, flags)
        self.bulk_map = []
        if o + 8 <= hashes_at:
            size, = struct.unpack_from('<Q', b, o)
            if o + 8 + size <= hashes_at:
                self.bulk_map = [struct.unpack_from('<qqqI', b, o + 8 + 32 * k)
                                 for k in range(size // 32)]
        nh = (imports_at - hashes_at) // 8
        self.hashes = struct.unpack_from('<%dQ' % nh, b, hashes_at)
        ni = (exports_at - imports_at) // 8
        self.imports = struct.unpack_from('<%dQ' % ni, b, imports_at)
        self.exports = []
        for k in range((bundles_at - exports_at) // 72):
            (off, size, oname, onum, outer, cls, _sup, tmpl, phash, flags,
             _ff) = struct.unpack_from('<QQIIQQQQQIB', b, exports_at + 72 * k)
            self.exports.append({'size': size, 'name': self.names[oname & 0x3FFFFFFF],
                                 'outer': outer, 'class': cls, 'template': tmpl,
                                 'hash': phash, 'flags': flags, 'at': self.header + off})
        self.imported, _ = name_batch(b, impnames_at)
        self.by_hash = {e['hash']: i for i, e in enumerate(self.exports) if e['hash']}

    def fname(self, b, o):
        i, num = struct.unpack_from('<II', b, o)
        s = self.names[i & 0x3FFFFFFF] if (i & 0x3FFFFFFF) < len(self.names) else '?'
        return s if not num else '%s_%d' % (s, num - 1)

    def blob(self, i):
        e = self.exports[i]
        return self.data[e['at']:e['at'] + e['size']]

    def object_index(self, v):
        """(package, export index) for an FPackageObjectIndex naming an export, or None;
        ('script', name) for a native object."""
        kind = v >> 62
        if kind == IDX_EXPORT:
            return self, v
        if kind == IDX_SCRIPT:
            return 'script', self.game.script.get(v)
        if kind == IDX_PACKAGE:
            pi = (v & 0x3FFFFFFFFFFFFFFF) >> 32
            hi = v & 0xFFFFFFFF
            if pi >= len(self.imported) or hi >= len(self.hashes):
                return None
            other = self.game.package(self.imported[pi])
            if other is None:
                return None
            j = other.by_hash.get(self.hashes[hi])
            return (other, j) if j is not None else None
        return None

    def import_target(self, k):
        return self.object_index(self.imports[k]) if k < len(self.imports) else None

    def class_name(self, i):
        got = self.object_index(self.exports[i]['class'])
        if not got:
            return None
        if got[0] == 'script':
            return got[1]
        pkg, j = got
        return pkg.exports[j]['name']

    def props(self, i):
        """The export's own unversioned properties, by name."""
        return self.props_end(i)[0]

    def props_end(self, i):
        """(the export's own unversioned properties, the offset in `data` after them)."""
        cls = self.class_name(i)
        e = self.exports[i]
        p = Props(self, self.data, e['at'], e['at'] + e['size'])
        return p.struct(cls), p.o


# -- the property map ---------------------------------------------------------------

class Usmap:
    """A .usmap (version 0..3, uncompressed or Brotli/Zstd): struct schemas by name."""

    def __init__(self, path):
        b = open(path, 'rb').read()
        magic, ver, = struct.unpack_from('<HB', b, 0)
        if magic != USMAP_MAGIC:
            raise SystemExit('not a usmap: %s' % path)
        o = 3
        if ver >= 1:                                 # PackageVersioning: a bool, then data
            has = struct.unpack_from('<i', b, o)[0]
            o += 4
            if has:
                raise SystemExit('usmap with versioning info is not supported')
        comp = b[o]
        csize, dsize = struct.unpack_from('<II', b, o + 1)
        body = b[o + 9:o + 9 + csize]
        if comp == 1:
            raise SystemExit('the usmap is Oodle-compressed and no Oodle decoder is '
                             'available: dump it again uncompressed')
        if comp == 2:
            import brotli
            body = brotli.decompress(body)
        elif comp == 3:
            body = _zstd(body, dsize)
        self.ver = ver
        self._read(body)
        self._schemas = {}

    def _read(self, b):
        o = [0]

        def u8():
            o[0] += 1
            return b[o[0] - 1]

        def i32():
            o[0] += 4
            return struct.unpack_from('<i', b, o[0] - 4)[0]

        def u16():
            o[0] += 2
            return struct.unpack_from('<H', b, o[0] - 2)[0]

        names = []
        for _ in range(i32()):
            n = u8() if self.ver < 2 else u16()
            names.append(b[o[0]:o[0] + n].decode('utf-8', 'replace'))
            o[0] += n
        for _ in range(i32()):
            i32()
            for _ in range(u8() if self.ver < 3 else u16()):
                i32()

        def typ():
            kind = KINDS[u8()]
            if kind in ('Array', 'Set', 'OptionalProperty'):
                return (kind, typ())
            if kind == 'Map':
                return (kind, typ(), typ())
            if kind == 'Struct':
                return (kind, names[i32()])
            if kind == 'Enum':
                inner = typ()
                return (kind, names[i32()], inner)
            return (kind,)

        self.structs = {}
        for _ in range(i32()):
            name = names[i32()]
            sup = i32()
            count = u16()
            props = []
            for _ in range(u16()):
                index = u16()
                span = u8()
                pname = names[i32()]
                props.append((index, span, pname, typ()))
            self.structs[name] = (names[sup] if 0 <= sup < len(names) else None,
                                  count, props)

    def schema(self, name):
        """{schema index: (name, type)} for a class or struct, its supers included."""
        if name in self._schemas:
            return self._schemas[name]
        out = {}
        base = 0
        s, seen = name, set()
        while s and s in self.structs and s not in seen:
            seen.add(s)
            sup, count, props = self.structs[s]
            for index, span, pname, t in props:
                for k in range(span):
                    out[base + index + k] = (pname, t)
            base += count
            s = sup
        self._schemas[name] = out if name in self.structs else None
        return self._schemas[name]

    def derives(self, name, base):
        seen = set()
        while name and name not in seen:
            if name == base:
                return True
            seen.add(name)
            name = self.structs.get(name, (None,))[0]
        return False


def _zstd(src, size):
    lib = ctypes.CDLL(ctypes.util.find_library('zstd') or 'libzstd.so.1')
    out = ctypes.create_string_buffer(size)
    got = lib.ZSTD_decompress(out, ctypes.c_size_t(size), src, ctypes.c_size_t(len(src)))
    if got != size:
        raise SystemExit('bad Zstd usmap')
    return out.raw


# -- unversioned properties ---------------------------------------------------------

class PropError(Exception):
    pass


#: Structs serialized natively (fixed bytes): name -> struct format.
NATIVE = {
    'Vector': '<3d', 'Vector2D': '<2d', 'Vector4': '<4d', 'Rotator': '<3d',
    'Quat': '<4d', 'Plane': '<4d', 'Matrix': '<16d', 'Vector3f': '<3f',
    'Vector2f': '<2f', 'Vector4f': '<4f', 'Rotator3f': '<3f', 'Quat4f': '<4f',
    'Matrix44f': '<16f', 'IntPoint': '<2i', 'IntVector': '<3i', 'IntVector4': '<4i',
    'Color': '<4B', 'LinearColor': '<4f', 'Guid': '<4I', 'DateTime': '<q',
    'Timespan': '<q', 'FrameNumber': '<i', 'FrameRate': '<2i', 'NavAgentSelector': '<I',
    'Box': '<6dB', 'Box2D': '<4dB', 'Box3f': '<6fB', 'Sphere': '<4d',
    'BoxSphereBounds': '<7d', 'PerPlatformFloat': '<if', 'PerPlatformInt': '<ii',
    'PerPlatformBool': '<ii', 'GameplayTag': '<II', 'SimpleCurveKey': '<2f',
    'RichCurveKey': '<3B6f', 'TwoVectors': '<6d', 'Vector_NetQuantize': '<3d',
    'Vector_NetQuantize10': '<3d', 'Vector_NetQuantize100': '<3d',
    'Vector_NetQuantizeNormal': '<3d', 'SectionEvaluationDataTree': None,
}
SIZES = {'Byte': '<B', 'Bool': '<B', 'Int': '<i', 'Float': '<f', 'Double': '<d',
         'UInt64': '<Q', 'UInt32': '<I', 'UInt16': '<H', 'Int64': '<q', 'Int16': '<h',
         'Int8': '<b'}


class Props:
    """Reads unversioned property data from one package's bytes."""

    def __init__(self, pkg, b, o, end):
        self.pkg, self.b, self.o, self.end = pkg, b, o, end
        self.types = pkg.game.types

    def _u(self, fmt):
        v = struct.unpack_from(fmt, self.b, self.o)
        self.o += struct.calcsize(fmt)
        if self.o > self.end:
            raise PropError('ran past the export')
        return v

    def _str(self):
        n, = self._u('<i')
        if n < 0:
            s = self.b[self.o:self.o - 2 * n].decode('utf-16-le', 'replace')
            self.o -= 2 * n
        else:
            s = self.b[self.o:self.o + n].decode('latin-1')
            self.o += n
        if self.o > self.end or abs(n) > 1 << 20:
            raise PropError('bad string')
        return s.rstrip('\0')

    def _name(self):
        s = self.pkg.fname(self.b, self.o)
        self.o += 8
        return s

    def struct(self, name):
        """{property: value} of one unversioned struct or class at the cursor."""
        schema = self.types.schema(name)
        if schema is None:
            raise PropError('no schema for %r' % (name,))
        frags = []
        while True:
            v, = self._u('<H')
            frags.append((v & 0x7F, bool(v & 0x80), v >> 9))
            if v & 0x100:
                break
        zbits = sum(n for _s, z, n in frags if z)
        mask = 0
        if zbits:
            if zbits <= 8:
                mask, = self._u('<B')
            elif zbits <= 16:
                mask, = self._u('<H')
            else:
                words = self._u('<%dI' % ((zbits + 31) // 32))
                for k, w in enumerate(words):
                    mask |= w << (32 * k)
        out = {}
        i = bit = 0
        for skip, hasz, num in frags:
            i += skip
            for _ in range(num):
                zero = False
                if hasz:
                    zero = bool(mask >> bit & 1)
                    bit += 1
                got = schema.get(i)
                if got is None:
                    raise PropError('%s has no property %d' % (name, i))
                out[got[0]] = self.zero(got[1]) if zero else self.value(got[1])
                i += 1
        return out

    def zero(self, t):
        """A zero-masked property's value (all bytes zero, not the default)."""
        if t[0] in ('Object', 'Interface', 'WeakObject'):
            return Ref(self.pkg, 0)
        if t[0] == 'Struct':
            fmt = NATIVE.get(t[1])
            return tuple([0] * len(struct.unpack(fmt, bytes(struct.calcsize(fmt))))) \
                if fmt else {}
        if t[0] in ('Array', 'Set', 'Map'):
            return []
        if t[0] in ('Name', 'Str', 'Text'):
            return ''
        return 0

    def value(self, t):
        kind = t[0]
        fmt = SIZES.get(kind)
        if fmt:
            return self._u(fmt)[0]
        if kind == 'Enum':
            return self.value(t[2]) if t[2][0] in SIZES else self._u('<B')[0]
        if kind in ('Object', 'Interface', 'WeakObject'):
            return Ref(self.pkg, self._u('<i')[0])
        if kind == 'Name':
            return self._name()
        if kind in ('Str', 'Utf8Str', 'AnsiStr'):
            return self._str()
        if kind in ('SoftObject', 'AssetObject'):
            return (self._name(), self._name(), self._str())
        if kind == 'LazyObject':
            return self._u('<4I')
        if kind == 'Delegate':
            self._u('<i')
            return self._name()
        if kind == 'MulticastDelegate':
            for _ in range(self._u('<i')[0]):
                self._u('<i')
                self._name()
            return None
        if kind == 'FieldPath':
            for _ in range(self._u('<i')[0]):
                self._name()
            self._u('<i')
            return None
        if kind == 'Text':
            return self._text()
        if kind == 'Array':
            n, = self._u('<i')
            if n < 0 or n > 1 << 22:
                raise PropError('bad array length')
            return [self.value(t[1]) for _ in range(n)]
        if kind == 'Set':
            for _ in range(self._u('<i')[0]):
                self.value(t[1])
            return [self.value(t[1]) for _ in range(self._u('<i')[0])]
        if kind == 'Map':
            for _ in range(self._u('<i')[0]):
                self.value(t[1])
            return [(self.value(t[1]), self.value(t[2])) for _ in range(self._u('<i')[0])]
        if kind == 'OptionalProperty':
            return self.value(t[1]) if self._u('<i')[0] else None
        if kind == 'Struct':
            return self._struct_value(t[1])
        raise PropError('unsupported property type %r' % (kind,))

    def _text(self):
        _flags, = self._u('<I')
        hist, = self._u('<b')
        if hist == -1:
            if self._u('<i')[0]:
                return self._str()
            return ''
        if hist == 0:
            self._str()
            self._str()
            return self._str()
        if hist == 11:
            self._name()
            return self._str()
        raise PropError('unsupported FText history %d' % hist)

    def _struct_value(self, name):
        fmt = NATIVE.get(name, '')
        if fmt:
            return self._u(fmt)
        if name in ('SoftObjectPath', 'SoftClassPath'):
            return (self._name(), self._name(), self._str())
        if name == 'GameplayTagContainer':
            return [self._name() for _ in range(self._u('<i')[0])]
        if name in ('PerQualityLevelInt', 'PerQualityLevelFloat'):
            self._u('<i')
            self._u('<i')
            for _ in range(self._u('<i')[0]):
                self._u('<2i')
            return None
        if fmt is None:
            raise PropError('unsupported native struct %r' % (name,))
        return self.struct(name)


# -- levels -------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def game():
    why = missing()
    if why:
        raise SystemExit(why)
    return Game(install())


def _listed(maps):
    """Persistent map names: those with a `<map>_Geo*` sibling."""
    return sorted(m for m in maps
                  if any(o.startswith(m + '_Geo') and maps[o].rsplit('/', 1)[0]
                         == maps[m].rsplit('/', 1)[0] for o in maps))


def levels():
    """Deathtrap's maps, or [] when it cannot be read."""
    if missing():
        return []
    return _listed(game().maps)


def sublevels(name):
    """Package names of one map's geometry: itself and its _Geo/_Props/_Vista sublevels."""
    maps = game().maps
    listed = _listed(maps)
    out = [maps[name]]
    for other in sorted(maps):
        if not other.startswith(name + '_'):
            continue
        rest = other[len(name) + 1:]
        if not rest.startswith(GEO_PARTS):
            continue
        if any(m != name and m.startswith(name + '_') and other.startswith(m + '_')
               for m in listed):
            continue
        out.append(maps[other])
    return out


class Level:
    """Merged properties and world transforms of one level package's components."""

    def __init__(self, pkg):
        self.pkg = pkg
        self._props = {}
        self._world = {}
        self.failed = 0

    def props(self, pkg, i, depth=0):
        """The export's properties over its template's, or {} when unreadable."""
        key = (id(pkg), i)
        if key in self._props:
            return self._props[key]
        out = {}
        tmpl = pkg.object_index(pkg.exports[i]['template'])
        if tmpl and tmpl[0] != 'script' and depth < 8:
            out.update(self.props(tmpl[0], tmpl[1], depth + 1))
        try:
            out.update(pkg.props(i))
        except (PropError, struct.error, IndexError, KeyError):
            self.failed += 1
        self._props[key] = out
        return out

    def world(self, pkg, i, depth=0):
        """The component's world 4x4 (column vectors)."""
        import numpy as np
        key = (id(pkg), i)
        if key in self._world:
            return self._world[key]
        p = self.props(pkg, i)
        local = _matrix(p.get('RelativeLocation'), p.get('RelativeRotation'),
                        p.get('RelativeScale3D', (1.0, 1.0, 1.0)))
        parent = p.get('AttachParent')
        hit = parent.resolve() if isinstance(parent, Ref) else None
        if hit and hit[0] is self.pkg and depth < 32:
            local = self.world(hit[0], hit[1], depth + 1) @ local
        self._world[key] = np.asarray(local)
        return self._world[key]


def _matrix(loc, rot, scale):
    import numpy as np
    from riftlord.formats.omd3 import omd3level
    return np.asarray(omd3level._matrix(tuple(loc or (0.0, 0.0, 0.0)),
                                        tuple(rot or (0.0, 0.0, 0.0)),
                                        tuple(scale or (1.0, 1.0, 1.0))), np.float64)


def _ism_matrices(blob):
    """Instance matrices of an ISM's bulk-serialized PerInstanceSMData: the first
    `(128, N)` array (FInstancedStaticMeshInstanceData is one FMatrix of doubles) whose
    every row-vector matrix ends (0, 0, 0, 1) in its last column. Row-major, so
    returned transposed for column vectors."""
    import numpy as np
    tag = struct.pack('<i', 128)
    i = 0
    while True:
        i = blob.find(tag, i)
        if i < 0 or i + 8 > len(blob):
            return []
        n, = struct.unpack_from('<i', blob, i + 4)
        if 0 < n and i + 8 + 128 * n <= len(blob):
            m = np.frombuffer(blob, np.float64, 16 * n, i + 8).reshape(n, 4, 4)
            if np.allclose(m[:, :, 3], [0, 0, 0, 1]) and np.isfinite(m).all():
                return list(m.transpose(0, 2, 1))
        i += 1


def mesh(pkg, i):
    """{verts, tris, uvs, sections} of a cooked StaticMesh's richest LOD, or None
    (module docstring). `uvs` is the first UV set or None (`_uvs`); `sections` the
    LOD's [(material index, first triangle, triangles)] (`_sections`), or []."""
    import numpy as np
    best = None
    export = pkg.blob(i)
    for b in (export, game().bulk(pkg)):
        tag = struct.pack('<I', 12)
        k = 0
        while True:
            k = b.find(tag, k)
            if k < 0 or k + 16 > len(b):
                break
            s, n, s2, n2 = struct.unpack_from('<4I', b, k)
            if not (s == 12 and s2 == 12 and n == n2 and 3 <= n <= MAX_VERTS
                    and k + 16 + 12 * n <= len(b)):
                k += 1
                continue
            got = _indices(b, k + 16 + 12 * n, n)
            if got is None and _uvs(b, k + 16 + 12 * n, n) is not None:
                got = _indices_before(b, k, n)
            if got is not None and (best is None or n > len(best['verts'])):
                best = {'verts': np.frombuffer(b, np.float32, 3 * n, k + 16).reshape(-1, 3),
                        'tris': got, 'uvs': _uvs(b, k + 16 + 12 * n, n),
                        'sections': _sections(export, len(got), n)}
            k += 16 + 12 * n
    return best


#: Bytes allowed between an index buffer's end and the position buffer it precedes.
INDEX_GAP = 16


def _indices_before(b, k, n):
    """The FRawStaticIndexBuffer ending within INDEX_GAP bytes before the position
    buffer at `k` whose largest index is n - 1, as (M, 3) int64, or None. A
    GeometryCollection's render data (FGeometryCollectionMeshResources) holds its index
    buffer before its positions: Wood_Barrel_A_GeoCol's 89,154 16-bit indices end 4
    bytes before its 38,040 positions."""
    import numpy as np
    for flag, width, dt in ((1, 4, np.uint32), (0, 2, np.uint16)):
        tag = struct.pack('<II', flag, 1)
        end = k
        while True:
            j = b.rfind(tag, 0, end)
            if j < 0:
                break
            end = j
            if j + 12 > len(b):
                continue
            size, = struct.unpack_from('<I', b, j + 8)
            stop = j + 12 + size
            if size and size % (3 * width) == 0 and k - INDEX_GAP <= stop <= k:
                idx = np.frombuffer(b, dt, size // width, j + 12)
                if int(idx.max()) == n - 1:
                    return idx.astype(np.int64).reshape(-1, 3)
            if k - j > INDEX_REACH:
                break
    return None


#: Furthest (bytes) an index buffer may start before the positions it precedes.
INDEX_REACH = 64 << 20


def _uvs(b, at, n):
    """The first UV set of the FStaticMeshVertexBuffer at `at`, (N, 2), or None.

    Measured on Deathtrap: 2 bytes of strip flags, then NumTexCoords, NumVertices,
    bUseFullPrecisionUVs, bUseHighPrecisionTangentBasis (u32 each), the tangents as a
    bulk array (element size 8, or 16 with high precision, then N) and the UVs as one
    (4 or 8 bytes a UV, vertex after vertex: element size a UV and count N times
    NumTexCoords, or element size a vertex and count N)."""
    import numpy as np
    for strip in (2, 0):
        o = at + strip
        if o + 24 > len(b):
            continue
        k, nv, full, high, tes, tcount = struct.unpack_from('<6I', b, o)
        if not (1 <= k <= 8 and nv == n and full in (0, 1) and high in (0, 1)
                and tes == (16 if high else 8) and tcount == n):
            continue
        u = o + 24 + tes * n
        if u + 8 > len(b):
            return None
        es, count = struct.unpack_from('<2I', b, u)
        uv = 8 if full else 4
        if not ((es == uv and count == n * k) or (es == uv * k and count == n)):
            return None
        if u + 8 + uv * k * n > len(b):
            return None
        raw = np.frombuffer(b, np.uint8, uv * k * n, u + 8).reshape(n, uv * k)[:, :uv].copy()
        return raw.view('<f4' if full else '<f2').reshape(n, 2).astype(np.float64)
    return None


def _sections(b, ntri, nverts):
    """[(material index, first triangle, triangles)] of the FStaticMeshSection array in
    `b` whose sections together cover exactly `ntri` triangles, or []: a count, then
    per section MaterialIndex, FirstIndex, NumTriangles, MinVertexIndex, MaxVertexIndex
    and five u32 bools (40 bytes). The sections need not be in index order
    (Base_Column_Square_05_15_Stone_SM's start at indices 0, 3255, 2163)."""
    for hit in _SECTION_HEAD.finditer(b):
        o = hit.start()
        c, = struct.unpack_from('<I', b, o)
        out = []
        for r in range(c):
            p = o + 4 + SECTION_BYTES * r
            if p + SECTION_BYTES > len(b):
                break
            mat, first, count, lo, hi = struct.unpack_from('<iIIII', b, p)
            flags = struct.unpack_from('<5I', b, p + 20)
            if not (0 <= mat < 256 and first % 3 == 0 and all(f in (0, 1) for f in flags)
                    and (not count or lo <= hi < nverts)):
                break
            out.append((mat, first // 3, count))
        if len(out) == c and any(s[2] for s in out):
            out = [s for s in out if s[2]]
            if covers(out, ntri):
                return out
    return []


def covers(sections, ntri):
    """True when [(material, first, count)] tile triangles 0..ntri-1 exactly."""
    at = 0
    for _m, first, count in sorted(sections, key=lambda s: s[1]):
        if first != at:
            return False
        at += count
    return at == ntri


#: One FStaticMeshSection's bytes.
SECTION_BYTES = 40
#: A section array's start: a count of 1..64, then a first section with a small
#: material index, four u32s and five u32 bools.
_SECTION_HEAD = re.compile(rb'(?=[\x01-\x40]\x00\x00\x00.\x00\x00\x00.{16}'
                           rb'(?:[\x00\x01]\x00\x00\x00){5})', re.DOTALL)


def _indices(b, j, n):
    """The first FRawStaticIndexBuffer after `j` (32-bit flag, byte array of element
    size 1) whose largest index is n - 1, as (M, 3) int64, or None."""
    import numpy as np
    for flag, width, dt in ((1, 4, np.uint32), (0, 2, np.uint16)):
        tag = struct.pack('<II', flag, 1)
        k = j
        while True:
            k = b.find(tag, k)
            if k < 0 or k + 12 > len(b):
                break
            size, = struct.unpack_from('<I', b, k + 8)
            if size and size % (3 * width) == 0 and k + 12 + size <= len(b):
                idx = np.frombuffer(b, dt, size // width, k + 12)
                if int(idx.max()) == n - 1:
                    return idx.astype(np.int64).reshape(-1, 3)
            k += 1
    return None


# -- textures -----------------------------------------------------------------------
#
# A section's material is the component's OverrideMaterials entry, else the mesh's
# StaticMaterials[MaterialIndex].MaterialInterface. Its base colour map: a
# MaterialInstanceConstant's TextureParameterValues (ParameterInfo.Name, ParameterValue;
# `BC` on 116 of BackAlley's 141 materials), chosen as Unchained's are
# (trace_omdu.pick_base), then its Parent's.
#
# A cooked Texture2D (UE 5.3), after its properties: the UObject guid flag (u32), two
# strip flags (2 bytes each), bCooked (u32), then per pixel format: its FName, a skip
# offset (i64) and the platform data -- 16 bytes, SizeX, SizeY, PackedData (slices;
# bit 30: 8 bytes of OptData follow), the format as an FString (`PF_DXT1`), the first
# mip serialized, the mip count, the mips, then bIsVirtual (u32) and, when set, the
# virtual texture: every base colour of BackAlley is one.
#
# A virtual texture (FVirtualTextureBuiltData): NumLayers, WidthInBlocks,
# HeightInBlocks, ?, TileSize, TileBorderSize, TileDataOffsetPerLayer (u32 array:
# the end of each layer in a tile's record), NumMips, Width, Height, ChunkIndexPerMip
# and BaseOffsetPerMip (u32 arrays), TileOffsetData (per mip: tiles across, down, the
# address count, Addresses and Offsets as u32 arrays), three empty arrays, then per
# layer its format (FString) and fallback colour (4 floats), then the chunks: a count,
# then per chunk a 20-byte hash, SizeInBytes, CodecPayloadSize, per layer a codec byte
# and a u32 codec payload offset, and its bulk data index. Chunk n is the package's
# bulk data map entry n, in the `.ubulk`. A tile at (x, y) of mip m has address
# Morton(x, y); the run of Addresses at or below it gives its index (Offsets[run] +
# address - Addresses[run]); its record is at BaseOffsetPerMip[m] + index * record
# size in chunk ChunkIndexPerMip[m]. A tile is (TileSize + 2 * border) square, its
# border cut off when tiles are put together. Measured on
# Wall_Sewer_StoneBrick_Large_BC: 1,024 square at LODBias 1, 11 mips, 128 px tiles
# with 4 px borders, DXT1, 9,248 bytes a tile, mip 0 in chunk 0 (64 tiles), mips 1-10
# in chunk 1.

#: Longest side of the mip read for the Trace tool's textures.
TEXTURE_MAX = 256
#: Formats decoded (as texture.py names them; BC7 through a DX10 DDS header).
DECODED = ('PF_DXT1', 'PF_DXT3', 'PF_DXT5', 'PF_BC4', 'PF_BC5', 'PF_B8G8R8A8', 'PF_G8',
           'PF_BC7')
#: DXGI format of BC7 (UNORM) in a DX10 DDS header.
DXGI_BC7 = 98


def _morton(x, y):
    out = 0
    for bit in range(16):
        out |= ((x >> bit) & 1) << (2 * bit) | ((y >> bit) & 1) << (2 * bit + 1)
    return out


def decode(fmt, w, h, data):
    """A PIL image of one mip's bytes, or None."""
    import io
    from riftlord.formats.ue import texture as uetex
    from PIL import Image
    if fmt == 'PF_BC7':
        size = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16
        if len(data) < size:
            return None
        hdr = bytearray(uetex.dds_header(w, h, 'PF_DXT5', size))
        hdr[84:88] = b'DX10'
        dx10 = struct.pack('<5I', DXGI_BC7, 3, 0, 1, 0)
        return Image.open(io.BytesIO(bytes(hdr) + dx10 + data[:size])).convert('RGBA')
    want = uetex.mip_bytes(w, h, fmt)
    if want is None or len(data) < want:
        return None
    return uetex.to_image({'width': w, 'height': h, 'format': fmt, 'data': data[:want]})


class _Cursor:
    def __init__(self, b, o, end):
        self.b, self.o, self.end = b, o, end

    def many(self, fmt):
        v = struct.unpack_from(fmt, self.b, self.o)
        self.o += struct.calcsize(fmt)
        if self.o > self.end:
            raise ValueError('ran past the export')
        return v

    def u(self, fmt='<I'):
        return self.many(fmt)[0]

    def array(self):
        n = self.u()
        if n > 1 << 20:
            raise ValueError('bad array')
        return list(self.many('<%dI' % n)) if n else []

    def fstring(self):
        n = self.u('<i')
        if not 0 < n < 256:
            raise ValueError('bad string')
        s = self.b[self.o:self.o + n].rstrip(b'\0').decode('latin-1')
        self.o += n
        return s


def texture_image(pkg, i):
    """A PIL image of a cooked Texture2D within TEXTURE_MAX, or None (section comment)."""
    _props, end = pkg.props_end(i)
    e = pkg.exports[i]
    stop = e['at'] + e['size']
    b = pkg.data
    at = b.find(b'PF_', end, min(stop, end + 512))
    if at < 8:
        return None
    c = _Cursor(b, at - 4, stop)
    sx, sy, packed = struct.unpack_from('<3I', b, at - 16)
    fmt = c.fstring()
    if packed & (1 << 30):
        c.u('<2I')
    _first, nmips = c.u('<i'), c.u('<i')
    if nmips:
        return _mips(pkg, c, fmt, nmips)
    if not c.u():
        return None
    return _virtual(pkg, c)


#: Bulk data map flag: the payload is in the package's `.ubulk`; else it follows inline.
BULK_SEPARATE = 0x100
#: Bulk data map flag: the payload is in the optional `.uptnl` (not read).
BULK_OPTIONAL = 0x800


def _mips(pkg, c, fmt, nmips):
    """A plain mip chain at the cursor: per mip its bulk data map index, the payload
    when it is inline, SizeX, SizeY, SizeZ. The largest mip within TEXTURE_MAX, else
    the smallest above it, as a PIL image."""
    if fmt not in DECODED or nmips > 20:
        return None
    mips = []
    for _ in range(nmips):
        idx = c.u('<i')
        entry = pkg.bulk_map[idx] if 0 <= idx < len(pkg.bulk_map) else None
        data = None
        if entry is not None and not entry[3] & BULK_SEPARATE:
            data = (c.o, entry[2])
            c.o += entry[2]
        elif entry is not None and not entry[3] & BULK_OPTIONAL:
            data = ('bulk', entry[0], entry[2])
        w, h, _d = c.u('<i'), c.u('<i'), c.u('<i')
        mips.append((w, h, data))
    order = sorted((m for m in mips if m[2]), key=lambda m: (max(m[0], m[1]) > TEXTURE_MAX,
                   -max(m[0], m[1]) if max(m[0], m[1]) <= TEXTURE_MAX else max(m[0], m[1])))
    for w, h, data in order:
        if data[0] == 'bulk':
            raw = game().bulk(pkg)[data[1]:data[1] + data[2]]
        else:
            raw = c.b[data[0]:data[0] + data[1]]
        try:
            img = decode(fmt, w, h, raw)
        except Exception:                            # noqa: BLE001
            img = None
        if img is not None:
            return img
    return None


def _virtual(pkg, c):
    """The virtual texture at the cursor, put together from the tiles of its largest
    mip within TEXTURE_MAX, as a PIL image (section comment)."""
    from PIL import Image
    layers = c.u()
    c.u('<3I')
    tile, border = c.u(), c.u()
    layer_end = c.array()
    nmips, width, height = c.u(), c.u(), c.u()
    chunk_of, base_of = c.array(), c.array()
    offsets = []
    for _ in range(c.u()):
        across, down, _count = c.u(), c.u(), c.u()
        offsets.append((across, down, c.array(), c.array()))
    for _ in range(3):
        c.array()
    formats = []
    for _ in range(layers):
        formats.append(c.fstring())
        c.u('<4f')
    if not (layers and nmips and len(chunk_of) == nmips == len(base_of) == len(offsets)):
        return None
    fmt = formats[0]
    if fmt not in DECODED:
        return None
    record = layer_end[-1] if layer_end else 0
    mip = next((m for m in range(nmips) if max(width >> m, height >> m) <= TEXTURE_MAX),
               nmips - 1)
    across, down, addresses, starts = offsets[mip]
    nchunks = c.u()
    bulk = game().bulk(pkg)
    ci = chunk_of[mip]
    if ci >= len(pkg.bulk_map) or ci >= nchunks:
        return None
    chunk_at = pkg.bulk_map[ci][0]
    side = tile + 2 * border
    mw, mh = max(1, width >> mip), max(1, height >> mip)
    out = Image.new('RGBA', (across * tile, down * tile))
    for y in range(down):
        for x in range(across):
            a = _morton(x, y)
            run = max((k for k, v in enumerate(addresses) if v <= a), default=None)
            if run is None:
                continue
            index = starts[run] + a - addresses[run]
            o = chunk_at + base_of[mip] + index * record
            img = decode(fmt, side, side, bulk[o:o + (layer_end[0] if layer_end else record)])
            if img is None:
                return None
            out.paste(img.crop((border, border, border + tile, border + tile)), (x * tile, y * tile))
    return out.crop((0, 0, min(mw, out.width), min(mh, out.height)))


def material_texture(hit, depth=0):
    """(package, export index) of the Texture2D a material uses as base colour, or None."""
    from riftlord.formats.omdu.trace_omdu import pick_base
    if not hit or hit[0] == 'script' or depth > 8:
        return None
    pkg, i = hit
    cls = pkg.class_name(i) or ''
    if cls == 'Texture2D':
        return hit
    try:
        p = pkg.props(i)
    except (PropError, struct.error, IndexError, KeyError):
        return None
    cands = []
    for t in p.get('TextureParameterValues') or []:
        ref = t.get('ParameterValue')
        got = ref.resolve() if isinstance(ref, Ref) else None
        if got and got[0] != 'script' and got[0].class_name(got[1]) == 'Texture2D':
            cands.append(((t.get('ParameterInfo') or {}).get('Name') or '',
                          got[0].exports[got[1]]['name'], got))
    got = pick_base(cands)
    if got:
        return got
    parent = p.get('Parent')
    return material_texture(parent.resolve() if isinstance(parent, Ref) else None, depth + 1)


def level_instances(level):
    """[{mesh: (package, export), matrix: 4x4, materials: [(package, export) or None per
    slot], spline: (params, export id) or None}] for every visible mesh component of
    one package. `materials` are the component's OverrideMaterials; a component with
    no scale (a trap's TriggerDisplay) is left out."""
    import numpy as np
    types = level.pkg.game.types
    pkg = level.pkg
    out = []
    for i, e in enumerate(pkg.exports):
        if e['flags'] & 0x30:                        # class default / archetype
            continue
        cls = pkg.class_name(i)
        if not cls:
            continue
        collection = types.derives(cls, 'GeometryCollectionComponent')
        if not collection and (not types.derives(cls, 'StaticMeshComponent')
                               or types.derives(cls, 'FoliageInstancedStaticMeshComponent')):
            continue
        p = level.props(pkg, i)
        if p.get('bHiddenInGame') or p.get('bVisible', 1) == 0:
            continue
        ref = p.get('RestCollection' if collection else 'StaticMesh')
        hit = ref.resolve() if isinstance(ref, Ref) else None
        if not hit or hit[0] == 'script':
            continue
        world = level.world(pkg, i)
        overrides = [r.resolve() if isinstance(r, Ref) else None
                     for r in (p.get('OverrideMaterials') or [])]
        spline = None
        if types.derives(cls, 'SplineMeshComponent'):
            spline = ({k: p.get(k) for k in SPLINE_KEYS}, '%s#%d' % (pkg.name, i))
        mats = _ism_matrices(pkg.blob(i)) if types.derives(cls, 'InstancedStaticMeshComponent') \
            else [np.eye(4)]
        for m in mats:
            w = world @ m
            if abs(np.linalg.det(w[:3, :3])) < 1e-9:
                continue
            out.append({'mesh': hit, 'matrix': w, 'materials': overrides, 'spline': spline})
    return out


#: A SplineMeshComponent's properties the deformation reads (`spline_deform`).
SPLINE_KEYS = ('SplineParams', 'ForwardAxis', 'SplineUpDir', 'SplineBoundaryMin',
               'SplineBoundaryMax', 'bSmoothInterpRollScale')


def spline_deform(verts, props):
    """`verts` bent along a SplineMeshComponent's spline (USplineMeshComponent's
    CalcSliceTransform): a vertex's position along the forward axis (X unless
    ForwardAxis says otherwise), as a share of the boundary (SplineBoundaryMin..Max, or
    the mesh's own extent along it), picks a point and direction on the Hermite curve
    StartPos/StartTangent -> EndPos/EndTangent; its other two coordinates, scaled by
    StartScale..EndScale and turned by StartRoll..EndRoll about the curve, are laid out
    across it (the slice's X from SplineUpDir x direction)."""
    import numpy as np
    sp = props.get('SplineParams') or {}

    def vec(k, default):
        v = sp.get(k)
        return np.asarray(v if isinstance(v, (tuple, list)) and len(v) == len(default)
                          else default, np.float64)
    p0, t0 = vec('StartPos', (0, 0, 0)), vec('StartTangent', (0, 0, 0))
    p1, t1 = vec('EndPos', (0, 0, 0)), vec('EndTangent', (0, 0, 0))
    s0, s1 = vec('StartScale', (1, 1)), vec('EndScale', (1, 1))
    o0, o1 = vec('StartOffset', (0, 0)), vec('EndOffset', (0, 0))
    r0, r1 = float(sp.get('StartRoll') or 0.0), float(sp.get('EndRoll') or 0.0)
    f = int(props.get('ForwardAxis') or 0) % 3
    ax, ay = (f + 1) % 3, (f + 2) % 3
    up = props.get('SplineUpDir')
    up = np.asarray(up if isinstance(up, (tuple, list)) and len(up) == 3 and any(up)
                    else (0, 0, 1), np.float64)
    lo, hi = float(props.get('SplineBoundaryMin') or 0.0), float(props.get('SplineBoundaryMax') or 0.0)
    v = np.asarray(verts, np.float64)
    if hi - lo < 1e-6:
        lo, hi = float(v[:, f].min()), float(v[:, f].max())
    a = np.clip((v[:, f] - lo) / max(hi - lo, 1e-6), 0.0, 1.0)[:, None]
    h = a * a * (3 - 2 * a) if props.get('bSmoothInterpRollScale') else a
    pos = (2 * a ** 3 - 3 * a ** 2 + 1) * p0 + (a ** 3 - 2 * a ** 2 + a) * t0 + \
        (-2 * a ** 3 + 3 * a ** 2) * p1 + (a ** 3 - a ** 2) * t1
    d = (6 * a ** 2 - 6 * a) * p0 + (3 * a ** 2 - 4 * a + 1) * t0 + \
        (-6 * a ** 2 + 6 * a) * p1 + (3 * a ** 2 - 2 * a) * t1
    d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)
    bx = np.cross(up, d)
    bx /= np.maximum(np.linalg.norm(bx, axis=1, keepdims=True), 1e-9)
    by = np.cross(d, bx)
    by /= np.maximum(np.linalg.norm(by, axis=1, keepdims=True), 1e-9)
    off = o0 + (o1 - o0) * h
    pos = pos + off[:, :1] * bx + off[:, 1:] * by
    roll = r0 + (r1 - r0) * h
    xv = np.cos(roll) * bx - np.sin(roll) * by
    yv = np.cos(roll) * by + np.sin(roll) * bx
    scale = s0 + (s1 - s0) * h
    return pos + xv * (scale[:, :1] * v[:, ax:ax + 1]) + yv * (scale[:, 1:] * v[:, ay:ay + 1])


def _slots(hit):
    """A mesh's material slots as Refs: a StaticMesh's StaticMaterials, a
    GeometryCollection's Materials."""
    try:
        p = hit[0].props(hit[1])
    except (PropError, struct.error, IndexError, KeyError):
        return []
    if 'StaticMaterials' in p:
        return [s.get('MaterialInterface') for s in p.get('StaticMaterials') or []]
    return list(p.get('Materials') or [])


def _ref_key(hit):
    pkg, i = hit
    return '%s.%s' % (pkg.name, pkg.exports[i]['name'])


def read(name):
    """(models, insts, None, texture) for one map in Unreal space (trace.py's reader
    protocol). A model is keyed by its mesh's path, plus `|` and the override materials
    when a component overrides them, plus `@` and the component for a spline mesh; a
    texture by its Texture2D's path."""
    from riftlord.mapeditor import trace
    if name not in levels():
        raise SystemExit('no such Deathtrap level: %r' % (name,))
    g = game()
    insts, models, raw, textures, mat_tex = [], {}, {}, {}, {}

    def texture_of(material):
        if material is None:
            return None
        k = _ref_key(material)
        if k not in mat_tex:
            hit = material_texture(material)
            mat_tex[k] = _ref_key(hit) if hit else None
            if hit:
                textures.setdefault(mat_tex[k], hit)
        return mat_tex[k]

    for pname in sublevels(name):
        pkg = g.package(pname)
        if pkg is None:
            continue
        for inst in level_instances(Level(pkg)):
            hit = inst['mesh']
            mkey = _ref_key(hit)
            key = mkey
            if any(inst['materials']):
                key += '|' + ','.join(_ref_key(m) if m else '' for m in inst['materials'])
            if inst['spline']:
                key += '@' + inst['spline'][1]
            if key not in models:
                if mkey not in raw:
                    raw[mkey] = mesh(*hit)
                got = raw[mkey]
                if got is not None:
                    slots = _slots(hit)
                    sections = []
                    # no section table found: the whole mesh in its first slot
                    for mi, first, count in got['sections'] or [(0, 0, len(got['tris']))]:
                        mat = inst['materials'][mi] if mi < len(inst['materials']) else None
                        if mat is None and mi < len(slots) and isinstance(slots[mi], Ref):
                            mat = slots[mi].resolve()
                        sections.append({'first': first, 'tris': count,
                                         'texture': texture_of(mat if mat and mat[0] != 'script'
                                                               else None)})
                    verts = got['verts']
                    if inst['spline']:
                        verts = spline_deform(verts, inst['spline'][0])
                    models[key] = trace._model(verts, got['tris'], got['uvs'], sections)
            insts.append((key, inst['matrix']))

    def texture(key):
        hit = textures.get(key)
        image = texture_image(*hit) if hit else None
        return trace._png(image) if image is not None else None
    return models, insts, None, texture
