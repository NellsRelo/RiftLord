"""IoStore containers for Orcs Must Die! Deathtrap (UE 5.3): `.utoc` + `.ucas` + companion `.pak`.

Laid out as the shipped containers are (measured on all 19, docs/research/DEATHTRAP-MAPS.md
"P1 writer"; the TOC fields are listed in trace_omdd's docstring):

- Chunks go into the .ucas in the order given, each starting on a 64 KiB boundary of the
  uncompressed address space, split into 64 KiB blocks. A package or bulk data block is
  LZ4-compressed (LZ4_compress_HC, level 12) when that saves at least 1,024 bytes and 5 %,
  else stored raw; shader and container header chunks are always raw. Every block starts on
  a 16-byte boundary of the .ucas, and a chunk whose blocks would cross a 1 MiB boundary of
  the .ucas starts on the next one (zero padding).
- The container header chunk (type 6, id = the container id) goes last.
- TOC entries are in perfect-hash slot order: a chunk id's bucket is FNV-1a-64(id) % seeds
  (seeds = round(chunks / 2)); a bucket of several chunks stores the seed that sends each to a
  free slot (FNV-1a-64 seeded, % chunks); a bucket of one stores -slot - 1. When the chunk
  count is a power of two a slot depends only on the seed modulo it, so two chunks can share
  a slot under every seed; such a container is written without a perfect hash (no seeds, TOC
  in .ucas order), as retoc writes every container (proven in game by S1).
- Metas: the first 20 bytes of the chunk's BLAKE3, 12 zero bytes, flags (1: compressed).
- The directory index lists files in TOC order; a new directory or file is linked at the head
  of its parent's list, names are shared in one string table in order of first use.
- The container header: package ids sorted, a store entry per package (imported package ids,
  shader map hashes), then empty optional-segment, redirect-name, localized and redirect lists.
- The companion .pak is an empty v11 pak; the game mounts a container by its .pak.

Container ids and package ids are CityHash64 of the lowercased name as UTF-16 (zenpackage).
"""

import ctypes
import ctypes.util
import functools
import hashlib
import os
import struct

from riftlord.formats.omdd import trace_omdd, zenpackage

BLOCK = 0x10000
#: A chunk that would cross a multiple of this in the .ucas starts on the next one.
CHUNK_ALIGN = 0x100000
#: A block is compressed only when it saves at least this many bytes and this percentage.
MIN_BYTES_SAVED, MIN_PERCENT_SAVED = 1024, 5
#: The LZ4 HC level the shipped containers' blocks reproduce at (LZ4HC_CLEVEL_MAX).
LZ4_LEVEL = 12
TOC_VERSION = 5
HEADER_VERSION = 3
CONTAINER_HEADER_MAGIC = 0x496F436E
FLAG_COMPRESSED, FLAG_INDEXED = 0x1, 0x8
META_COMPRESSED = 0x1
CHUNK_PACKAGE, CHUNK_BULK, CHUNK_HEADER = 1, 2, 6
#: Chunk types whose blocks may be compressed (package, bulk, optional bulk).
COMPRESSIBLE = (1, 2, 3)
NONE = 0xFFFFFFFF
M64 = 0xFFFFFFFFFFFFFFFF


def chunk_id(object_id, kind, index=0):
    """A 12-byte FIoChunkId: u64 id, big-endian u16 index, a pad byte, the chunk type."""
    return struct.pack('<Q', object_id) + struct.pack('>H', index) + bytes((0, kind))


# -- hashing and compression -----------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _libblake3():
    for name in (ctypes.util.find_library('blake3'), 'libblake3.so.0', 'libblake3.dylib',
                 'blake3.dll'):
        if not name:
            continue
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    return None


#: sizeof(blake3_hasher) is 1,912 bytes in BLAKE3 1.x; this leaves room.
HASHER_BYTES = 4096


def blake3(data):
    """BLAKE3-256 of `data` (libblake3 when present, else the Python version)."""
    lib = _libblake3()
    if lib is None:
        return _blake3_py(bytes(data))
    state = ctypes.create_string_buffer(HASHER_BYTES)
    lib.blake3_hasher_init(state)
    lib.blake3_hasher_update(state, bytes(data), ctypes.c_size_t(len(data)))
    out = ctypes.create_string_buffer(32)
    lib.blake3_hasher_finalize(state, out, ctypes.c_size_t(32))
    return out.raw


_IV = (0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
       0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19)
_PERM = (2, 6, 3, 10, 7, 0, 4, 13, 1, 11, 12, 5, 9, 14, 15, 8)
_START, _END, _PARENT, _ROOT = 1, 2, 4, 8


def _compress(cv, block, counter, length, flags):
    m = list(struct.unpack('<16I', block.ljust(64, b'\0')))
    s = list(cv) + list(_IV[:4]) + [counter & NONE, counter >> 32, length, flags]

    def g(a, b, c, d, x, y):
        s[a] = (s[a] + s[b] + x) & NONE
        s[d] = ((s[d] ^ s[a]) >> 16 | (s[d] ^ s[a]) << 16) & NONE
        s[c] = (s[c] + s[d]) & NONE
        s[b] = ((s[b] ^ s[c]) >> 12 | (s[b] ^ s[c]) << 20) & NONE
        s[a] = (s[a] + s[b] + y) & NONE
        s[d] = ((s[d] ^ s[a]) >> 8 | (s[d] ^ s[a]) << 24) & NONE
        s[c] = (s[c] + s[d]) & NONE
        s[b] = ((s[b] ^ s[c]) >> 7 | (s[b] ^ s[c]) << 25) & NONE
    for r in range(7):
        g(0, 4, 8, 12, m[0], m[1])
        g(1, 5, 9, 13, m[2], m[3])
        g(2, 6, 10, 14, m[4], m[5])
        g(3, 7, 11, 15, m[6], m[7])
        g(0, 5, 10, 15, m[8], m[9])
        g(1, 6, 11, 12, m[10], m[11])
        g(2, 7, 8, 13, m[12], m[13])
        g(3, 4, 9, 14, m[14], m[15])
        m = [m[i] for i in _PERM]
    return [s[i] ^ s[i + 8] for i in range(8)]


def _blake3_py(data):
    chunks = [data[k:k + 1024] for k in range(0, len(data), 1024)] or [b'']

    def chunk_cv(k, root):
        c = chunks[k]
        blocks = [c[j:j + 64] for j in range(0, len(c), 64)] or [b'']
        cv = list(_IV)
        for j, blk in enumerate(blocks):
            flags = (_START if j == 0 else 0) | (_END if j == len(blocks) - 1 else 0)
            cv = _compress(cv, blk, k, len(blk), flags | (_ROOT if root and flags & _END else 0))
        return cv

    def tree(lo, hi, root):
        if hi - lo == 1:
            return chunk_cv(lo, root)
        left = 1 << ((hi - lo - 1).bit_length() - 1)
        block = struct.pack('<16I', *(tree(lo, lo + left, False) + tree(lo + left, hi, False)))
        return _compress(_IV, block, 0, 64, _PARENT | (_ROOT if root else 0))
    return struct.pack('<8I', *tree(0, len(chunks), True))


@functools.lru_cache(maxsize=1)
def _liblz4():
    for name in (ctypes.util.find_library('lz4'), 'liblz4.so.1', 'liblz4.dylib', 'lz4.dll'):
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
            lib.LZ4_compress_HC.restype = ctypes.c_int
            return lib
        except (OSError, AttributeError):
            continue
    return None


def lz4_block(block):
    """LZ4 HC of one block, or None when no LZ4 library is available."""
    lib = _liblz4()
    if lib is None:
        return None
    cap = len(block) + len(block) // 255 + 16
    out = ctypes.create_string_buffer(cap)
    got = lib.LZ4_compress_HC(bytes(block), out, len(block), cap, LZ4_LEVEL)
    return out.raw[:got] if got > 0 else None


def worth_it(raw, packed):
    saved = len(raw) - len(packed)
    return saved >= MIN_BYTES_SAVED and saved * 100 // len(raw) >= MIN_PERCENT_SAVED


# -- perfect hash ----------------------------------------------------------------------------

def fnv(seed, cid):
    h = seed if seed else 0xcbf29ce484222325
    for c in cid:
        h = ((h * 0x00000100000001B3) & M64) ^ c
    return h


def perfect_hash(ids):
    """(slots: index into `ids` per TOC slot, seeds), buckets placed largest first; None
    when a bucket has no seed (module docstring)."""
    n = len(ids)
    nseeds = max(1, int(n / 2.0 + 0.5))
    buckets = [[] for _ in range(nseeds)]
    for i, cid in enumerate(ids):
        buckets[fnv(0, cid) % nseeds].append(i)
    seeds, free, slots = [0] * nseeds, [True] * n, [None] * n
    order = sorted(range(nseeds), key=lambda b: -len(buckets[b]))
    # with a power-of-two count, seeds past the count repeat the slots of earlier ones
    last = n if n & (n - 1) == 0 else 0x7FFFFFFF
    for b in order:
        bucket = buckets[b]
        if len(bucket) < 2:
            break
        seed = 1
        while True:
            want = [fnv(seed, ids[i]) % n for i in bucket]
            if len(set(want)) == len(want) and all(free[s] for s in want):
                break
            seed += 1
            if seed > last:
                return None
        seeds[b] = seed
        for i, s in zip(bucket, want):
            free[s], slots[s] = False, i
    left = iter(s for s in range(n) if free[s])
    for b in order:
        if len(buckets[b]) == 1:
            s = next(left)
            seeds[b], slots[s] = -s - 1, buckets[b][0]
    return slots, seeds


def lookup(ids, seeds, cid):
    """The TOC index of a chunk id by the perfect hash, or None."""
    seed = seeds[fnv(0, cid) % len(seeds)] if seeds else 0
    if seed == 0:
        return None
    slot = -seed - 1 if seed < 0 else fnv(seed, cid) % len(ids)
    return slot if ids[slot] == cid else None


# -- directory index -------------------------------------------------------------------------

def _fstring(text):
    if not text:
        return struct.pack('<i', 0)
    if any(ord(c) > 0x7F for c in text):
        b = text.encode('utf-16-le') + b'\0\0'
        return struct.pack('<i', -(len(b) // 2)) + b
    b = text.encode('ascii') + b'\0'
    return struct.pack('<i', len(b)) + b


def directory_index(mount, files):
    """FIoDirectoryIndexResource of [(path relative to the mount, TOC index)] in TOC order."""
    if not files:
        return b''
    dirs = [[NONE, NONE, NONE, NONE]]                # name, first child, next sibling, first file
    entries, strings, index = [], [], {}

    def name(text):
        if text not in index:
            index[text] = len(strings)
            strings.append(text)
        return index[text]
    for path, user in files:
        parts = path.split('/')
        d = 0
        for part in parts[:-1]:
            c = dirs[d][1]
            while c != NONE and strings[dirs[c][0]] != part:
                c = dirs[c][2]
            if c == NONE:
                c = len(dirs)
                dirs.append([name(part), NONE, dirs[d][1], NONE])
                dirs[d][1] = c
            d = c
        f = dirs[d][3]
        while f != NONE and strings[entries[f][0]] != parts[-1]:
            f = entries[f][1]
        if f == NONE:
            f = len(entries)
            entries.append([name(parts[-1]), dirs[d][3], user])
            dirs[d][3] = f
        entries[f][2] = user
    out = [_fstring(mount), struct.pack('<i', len(dirs))]
    out += [struct.pack('<4I', *x) for x in dirs]
    out += [struct.pack('<i', len(entries))] + [struct.pack('<3I', *x) for x in entries]
    out += [struct.pack('<i', len(strings))] + [_fstring(s) for s in strings]
    return b''.join(out)


# -- container header ------------------------------------------------------------------------

class ContainerHeader:
    """FIoContainerHeader (version NoExportInfo). `packages` maps a package id to
    (imported package ids, shader map hashes as 20-byte strings)."""

    def __init__(self, container_id, packages=None):
        self.container_id = container_id
        self.packages = dict(packages or {})
        self.optional_ids, self.optional_entries = [], b''
        self.redirect_names, self.localized, self.redirects = [], [], []

    @classmethod
    def parse(cls, b):
        magic, version, cid = struct.unpack_from('<IIQ', b, 0)
        if magic != CONTAINER_HEADER_MAGIC or version != HEADER_VERSION:
            raise ValueError('not a version %d container header' % HEADER_VERSION)
        self = cls(cid)
        o = 16
        n, = struct.unpack_from('<i', b, o)
        ids = struct.unpack_from('<%dQ' % n, b, o + 4)
        o += 4 + 8 * n
        size, = struct.unpack_from('<i', b, o)
        buf = b[o + 4:o + 4 + size]
        o += 4 + size
        for k, pid in enumerate(ids):
            ni, oi, ns, os_ = struct.unpack_from('<4I', buf, 16 * k)
            imports = list(struct.unpack_from('<%dQ' % ni, buf, 16 * k + oi)) if ni else []
            at = 16 * k + 8 + os_
            hashes = [buf[at + 20 * j:at + 20 * j + 20] for j in range(ns)]
            self.packages[pid] = (imports, hashes)
        n, = struct.unpack_from('<i', b, o)
        self.optional_ids = list(struct.unpack_from('<%dQ' % n, b, o + 4))
        o += 4 + 8 * n
        size, = struct.unpack_from('<i', b, o)
        self.optional_entries = b[o + 4:o + 4 + size]
        o += 4 + size
        self.redirect_names, o = trace_omdd.name_batch(b, o)
        n, = struct.unpack_from('<i', b, o)
        self.localized = [struct.unpack_from('<QII', b, o + 4 + 16 * k) for k in range(n)]
        o += 4 + 16 * n
        n, = struct.unpack_from('<i', b, o)
        self.redirects = [struct.unpack_from('<QQII', b, o + 4 + 24 * k) for k in range(n)]
        o += 4 + 24 * n
        if o != len(b):
            raise ValueError('%d bytes after the container header' % (len(b) - o))
        return self

    def to_bytes(self):
        ids = sorted(self.packages)
        entries, arrays = [], []
        at = 16 * len(ids)
        for k, pid in enumerate(ids):
            imports, hashes = self.packages[pid]
            view = []
            for data, num, member in ((struct.pack('<%dQ' % len(imports), *imports), len(imports), 0),
                                      (b''.join(hashes), len(hashes), 8)):
                view += [num, at - 16 * k - member if num else 0]
                arrays.append(data)
                at += len(data)
            entries.append(struct.pack('<4I', *view))
        buf = b''.join(entries + arrays)
        out = [struct.pack('<IIQ', CONTAINER_HEADER_MAGIC, HEADER_VERSION, self.container_id),
               struct.pack('<i%dQ' % len(ids), len(ids), *ids), struct.pack('<i', len(buf)), buf,
               struct.pack('<i%dQ' % len(self.optional_ids), len(self.optional_ids), *self.optional_ids),
               struct.pack('<i', len(self.optional_entries)), self.optional_entries,
               zenpackage.write_name_batch(self.redirect_names),
               struct.pack('<i', len(self.localized))]
        out += [struct.pack('<QII', *x) for x in self.localized]
        out += [struct.pack('<i', len(self.redirects))] + [struct.pack('<QQII', *x) for x in self.redirects]
        return b''.join(out)


def store_entry(pkg):
    """(imported package ids, []) of a ZenPackage, for its container header entry."""
    return ([zenpackage.package_id(zenpackage.join_name(n, k)) for n, k in pkg.imported_packages], [])


# -- the container ---------------------------------------------------------------------------

class Chunk:
    """One chunk to write: 12-byte id, bytes, and its path relative to the mount point
    ('OMDD/Content/....uasset') or None."""
    __slots__ = ('id', 'data', 'path')

    def __init__(self, cid, data, path=None):
        self.id, self.data, self.path = cid, bytes(data), path


def write_container(utoc, chunks, header=None, mount='../../../', compress=True, hashing=None,
                    header_align=1):
    """Write `utoc`, its .ucas and its .pak. `chunks` in .ucas order; `header` (a
    ContainerHeader) is appended as the last chunk, zero-padded to `header_align` bytes
    (1 as shipped; retoc pads to 16). `hashing` is (slots, seeds) to keep a TOC order
    already made (`perfect_hash` otherwise). Returns the TOC entries as
    [(chunk id, offset, length, meta flags, BLAKE3 prefix)] in TOC order."""
    base = os.path.splitext(utoc)[0]
    cid = zenpackage.container_id(os.path.basename(base))
    chunks = list(chunks)
    if header is not None:
        if header.container_id != cid:
            raise ValueError('the container header is for another container')
        data = header.to_bytes()
        chunks.append(Chunk(chunk_id(cid, CHUNK_HEADER), data + bytes(-len(data) % header_align)))
    ids = [c.id for c in chunks]
    if len(set(ids)) != len(ids):
        raise ValueError('two chunks share an id')
    blocks, spans, metas, any_lz4 = [], [], [], False
    with open(base + '.ucas.part', 'wb') as cas:
        for c in chunks:
            spans.append((BLOCK * len(blocks), len(c.data)))
            bodies = []
            for k in range(0, len(c.data), BLOCK):
                raw = c.data[k:k + BLOCK]
                packed = lz4_block(raw) if compress and c.id[11] in COMPRESSIBLE else None
                use = packed is not None and worth_it(raw, packed)
                bodies.append((packed if use else raw, len(raw), use))
            size = sum(len(b) + -len(b) % 16 for b, _n, _u in bodies)
            pos = cas.tell()
            if size and pos // CHUNK_ALIGN != (pos + size - 1) // CHUNK_ALIGN:
                cas.write(bytes(-pos % CHUNK_ALIGN))
            for body, n, use in bodies:
                blocks.append((cas.tell(), len(body), n, 1 if use else 0))
                cas.write(body + bytes(-len(body) % 16))
            lz4 = any(u for _b, _n, u in bodies)
            any_lz4 |= lz4
            metas.append(blake3(c.data)[:20] + bytes(12) + bytes((META_COMPRESSED if lz4 else 0,)))
    slots, seeds = hashing or perfect_hash(ids) or (list(range(len(ids))), [])
    files = [(chunks[i].path, toc) for toc, i in enumerate(slots) if chunks[i].path]
    dindex = directory_index(mount, files)
    methods = [b'LZ4'] if any_lz4 else []
    flags = FLAG_INDEXED | (FLAG_COMPRESSED if any_lz4 else 0)
    head = (trace_omdd.TOC_MAGIC + struct.pack('<BBHIIIIIIIIIQ', TOC_VERSION, 0, 0, 144, len(chunks),
                                               len(blocks), 12, len(methods), 32, BLOCK, len(dindex),
                                               1, cid)
            + bytes(16) + struct.pack('<BBHIQII', flags, 0, 0, len(seeds), M64, 0, 0) + bytes(40))
    out = [head]
    out += [chunks[i].id for i in slots]
    out += [spans[i][0].to_bytes(5, 'big') + spans[i][1].to_bytes(5, 'big') for i in slots]
    out += [struct.pack('<%di' % len(seeds), *seeds)]
    out += [struct.pack('<Q', o)[:5] + struct.pack('<I', cs)[:3] + struct.pack('<I', us)[:3]
            + bytes((m,)) for o, cs, us, m in blocks]
    out += [m.ljust(32, b'\0') for m in methods] + [dindex] + [metas[i] for i in slots]
    with open(utoc + '.part', 'wb') as fh:
        fh.write(b''.join(out))
    with open(base + '.pak.part', 'wb') as fh:
        fh.write(empty_pak(mount))
    for ext in ('.ucas', '.utoc', '.pak'):
        os.replace(base + ext + '.part', base + ext)
    return [(chunks[i].id, spans[i][0], spans[i][1], metas[i][32], metas[i][:20]) for i in slots]


def empty_pak(mount='../../../'):
    """An empty v11 .pak (no files, path hash seed 0): index, path hash index, full directory
    index, then the footer (no encryption, magic, version, index offset, size and SHA-1, five
    empty compression method names)."""
    phi, fdi = bytes(8), bytes(4)
    head = _fstring(mount) + struct.pack('<iQ', 0, 0)
    size = len(head) + 2 * (4 + 8 + 8 + 20) + 8
    index = (head + struct.pack('<iqq', 1, size, len(phi)) + hashlib.sha1(phi).digest()
             + struct.pack('<iqq', 1, size + len(phi), len(fdi)) + hashlib.sha1(fdi).digest()
             + struct.pack('<ii', 0, 0))
    footer = (bytes(16) + b'\0' + struct.pack('<IIqq', 0x5A6F12E1, 11, 0, len(index))
              + hashlib.sha1(index).digest() + bytes(5 * 32))
    return index + phi + fdi + footer
