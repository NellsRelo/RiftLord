"""Orcs Must Die! Deathtrap Zen packages (UE 5.3) as an editable model, written back byte for byte.

Parsing shares trace_omdd (name batches, the summary layout); this module adds the parts the
reader skips and the writer. Layout of one package chunk (measured on all shipped packages,
see docs/research/DEATHTRAP-MAPS.md "P1 writer"):

- FZenPackageSummary, 52 bytes (trace_omdd docstring).
- The name batch: count, string bytes, hash version 0xC1640000, a CityHash64 per name of its
  lowercased text (bytes for an ASCII name, UTF-16 LE otherwise), a big-endian u16 header per
  name (top bit UTF-16, low 15 bits length), then the strings.
- The bulk data map: i64 byte size, then 32-byte entries (offset, duplicate offset, size i64;
  flags u32; cooked index u8; 3 pad bytes).
- Imported public export hashes (u64), the import map (FPackageObjectIndex, u64), the export
  map (72-byte FExportMapEntry), the export bundle entries (export index, command: 0 create,
  1 serialize), one FDependencyBundleHeader per export (first entry, then the four counts
  create-before-create, serialize-before-create, create-before-serialize,
  serialize-before-serialize), the dependency bundle entries (FPackageIndex i32), and the
  imported package names (a name batch, then an i32 number per name).
- Then the export data from the summary's header size: each export at its cooked serial
  offset, in offset order, back to back.

Package ids and container ids are CityHash64 of the lowercased name as UTF-16 LE.
"""

import struct

from riftlord.formats.omdd import trace_omdd

#: FNameHash algorithm id written after a name batch's sizes.
NAME_HASH_VERSION = 0xC1640000
SUMMARY = struct.Struct('<IIIIIIiiiiiii')
EXPORT = struct.Struct('<QQIIQQQQQIB3s')
BULK = struct.Struct('<qqqIB3s')
DEP_HEADER = struct.Struct('<i4I')
#: EExportCommandType.
CREATE, SERIALIZE = 0, 1
#: FPackageObjectIndex null.
NULL_INDEX = 0xFFFFFFFFFFFFFFFF
#: FMappedName index bits; the top two bits are the name kind (0 package, 1 container).
INDEX_MASK = 0x3FFFFFFF

M64 = 0xFFFFFFFFFFFFFFFF
K0, K1, K2 = 0xc3a5c85c97cb3127, 0xb492b66fbe98f273, 0x9ae16a3b2f90404f
KMUL = 0x9ddfea08eb382d69


# -- CityHash64 (v1.1, as UE's CityHash.cpp) -------------------------------------------------

def _f64(s, i):
    return int.from_bytes(s[i:i + 8], 'little')


def _f32(s, i):
    return int.from_bytes(s[i:i + 4], 'little')


def _rot(v, n):
    return v if n == 0 else ((v >> n) | (v << (64 - n))) & M64


def _mix(v):
    return v ^ (v >> 47)


def _len16(u, v, mul=KMUL):
    a = ((u ^ v) * mul) & M64
    a ^= a >> 47
    b = ((v ^ a) * mul) & M64
    b ^= b >> 47
    return (b * mul) & M64


def _bswap(v):
    return int.from_bytes(v.to_bytes(8, 'little'), 'big')


def _weak(s, i, a, b):
    w, x, y, z = _f64(s, i), _f64(s, i + 8), _f64(s, i + 16), _f64(s, i + 24)
    a = (a + w) & M64
    b = _rot((b + a + z) & M64, 21)
    c = a
    a = (a + x + y) & M64
    b = (b + _rot(a, 44)) & M64
    return (a + z) & M64, (b + c) & M64


def cityhash64(s):
    """CityHash64 of bytes `s`."""
    n = len(s)
    if n <= 16:
        if n >= 8:
            mul = (K2 + n * 2) & M64
            a = (_f64(s, 0) + K2) & M64
            b = _f64(s, n - 8)
            c = (_rot(b, 37) * mul + a) & M64
            d = ((_rot(a, 25) + b) * mul) & M64
            return _len16(c, d, mul)
        if n >= 4:
            mul = (K2 + n * 2) & M64
            return _len16((n + (_f32(s, 0) << 3)) & M64, _f32(s, n - 4), mul)
        if n:
            y = (s[0] + (s[n >> 1] << 8)) & 0xFFFFFFFF
            z = (n + (s[n - 1] << 2)) & 0xFFFFFFFF
            return (_mix(((y * K2) ^ (z * K0)) & M64) * K2) & M64
        return K2
    if n <= 32:
        mul = (K2 + n * 2) & M64
        a = (_f64(s, 0) * K1) & M64
        b = _f64(s, 8)
        c = (_f64(s, n - 8) * mul) & M64
        d = (_f64(s, n - 16) * K2) & M64
        return _len16((_rot((a + b) & M64, 43) + _rot(c, 30) + d) & M64,
                      (a + _rot((b + K2) & M64, 18) + c) & M64, mul)
    if n <= 64:
        mul = (K2 + n * 2) & M64
        a = (_f64(s, 0) * K2) & M64
        b = _f64(s, 8)
        c = _f64(s, n - 24)
        d = _f64(s, n - 32)
        e = (_f64(s, 16) * K2) & M64
        f = (_f64(s, 24) * 9) & M64
        g = _f64(s, n - 8)
        h = (_f64(s, n - 16) * mul) & M64
        u = (_rot((a + g) & M64, 43) + (_rot(b, 30) + c) * 9) & M64
        v = ((((a + g) & M64) ^ d) + f + 1) & M64
        w = (_bswap(((u + v) * mul) & M64) + h) & M64
        x = (_rot((e + f) & M64, 42) + c) & M64
        y = ((_bswap(((v + w) * mul) & M64) + g) * mul) & M64
        z = (e + f + c) & M64
        a = (_bswap(((x + z) * mul + y) & M64) + b) & M64
        b = (_mix(((z + a) * mul + d + h) & M64) * mul) & M64
        return (b + x) & M64
    x = _f64(s, n - 40)
    y = (_f64(s, n - 16) + _f64(s, n - 56)) & M64
    z = _len16((_f64(s, n - 48) + n) & M64, _f64(s, n - 24))
    v = _weak(s, n - 64, n, z)
    w = _weak(s, n - 32, (y + K1) & M64, x)
    x = (x * K1 + _f64(s, 0)) & M64
    left = (n - 1) & ~63
    i = 0
    while True:
        x = (_rot((x + y + v[0] + _f64(s, i + 8)) & M64, 37) * K1) & M64
        y = (_rot((y + v[1] + _f64(s, i + 48)) & M64, 42) * K1) & M64
        x ^= w[1]
        y = (y + v[0] + _f64(s, i + 40)) & M64
        z = (_rot((z + w[0]) & M64, 33) * K1) & M64
        v = _weak(s, i, (v[1] * K1) & M64, (x + w[0]) & M64)
        w = _weak(s, i + 32, (z + w[1]) & M64, (y + _f64(s, i + 16)) & M64)
        z, x = x, z
        i += 64
        left -= 64
        if not left:
            break
    return _len16((_len16(v[0], w[0]) + _mix(y) * K1 + z) & M64,
                  (_len16(v[1], w[1]) + x) & M64)


def _ascii_lower(text):
    return ''.join(c.lower() if c < '\x80' else c for c in text)


def package_id(name):
    """FPackageId of a package name ('/Game/...'): CityHash64 of it lowercased, UTF-16 LE."""
    return cityhash64(_ascii_lower(name).encode('utf-16-le'))


container_id = package_id

#: FPackageObjectIndex kinds (top two bits).
KIND_EXPORT, KIND_SCRIPT, KIND_PACKAGE = 0, 1, 2


def export_hash(path):
    """Public export hash of a package-relative object path ('Asset', 'Asset/Child'):
    CityHash64 of it lowercased, UTF-16 LE."""
    return cityhash64(_ascii_lower(path).encode('utf-16-le'))


def script_import(path):
    """FPackageObjectIndex of a native object ('/Script/Engine.StaticMesh'): CityHash64 of
    the path lowercased with '.' and ':' as '/', top two bits cleared, kind script."""
    low = _ascii_lower(path).replace('.', '/').replace(':', '/')
    return KIND_SCRIPT << 62 | cityhash64(low.encode('utf-16-le')) & ~(3 << 62)


# -- name batches ----------------------------------------------------------------------------

def _wide(name):
    return any(ord(c) > 0x7F for c in name)


def name_hash(name):
    if _wide(name):
        return cityhash64(_ascii_lower(name).encode('utf-16-le'))
    return cityhash64(name.encode('ascii').lower())


def write_name_batch(names):
    """A Zen name batch of `names`."""
    if not names:
        return struct.pack('<I', 0)
    text = [n.encode('utf-16-le') if _wide(n) else n.encode('ascii') for n in names]
    out = [struct.pack('<IIQ', len(names), sum(len(t) for t in text), NAME_HASH_VERSION),
           struct.pack('<%dQ' % len(names), *map(name_hash, names)),
           struct.pack('>%dH' % len(names), *[len(t) // 2 | 0x8000 if _wide(n) else len(t)
                                              for n, t in zip(names, text)])]
    return b''.join(out + text)


def split_name(text):
    """(base, number) of an FName written 'Base_N' (number N + 1; 0 when none), as UE
    and retoc split it: the tail must be a plain non-negative integer without leading zeros."""
    base, _, tail = text.rpartition('_')
    if base and tail.isdigit() and str(int(tail)) == tail:
        return base, int(tail) + 1
    return text, 0


def join_name(base, number):
    return base if not number else '%s_%d' % (base, number - 1)


# -- the package model -----------------------------------------------------------------------

class Export:
    """One FExportMapEntry and its serialized data. `offset` is the parsed cooked serial
    offset (None for a new export); the writer recomputes offsets from the layout."""
    __slots__ = ('name', 'number', 'outer', 'cls', 'super', 'template', 'hash', 'flags',
                 'filter', 'pad', 'data', 'offset')

    def __init__(self, **kw):
        self.number, self.pad, self.filter, self.offset = 0, b'\0\0\0', 0, None
        self.outer = self.cls = self.super = self.template = NULL_INDEX
        self.hash = self.flags = 0
        self.data = b''
        for k, v in kw.items():
            setattr(self, k, v)


class ZenPackage:
    """A parsed Zen package. Lists are the package's own tables, in file order:
    names (str), bulk (BULK tuples), imported_hashes (u64), imports (u64 FPackageObjectIndex),
    exports (Export), bundle ((export index, command)), dep_headers (DEP_HEADER tuples, one
    per export), dep_entries (i32 FPackageIndex), imported_packages ((name, number))."""

    def __init__(self, data):
        b = bytes(data)
        (versioned, header_size, self.name_index, self.name_number, self.flags,
         self.cooked_header_size, hashes_at, imports_at, exports_at, bundle_at, dep_at,
         dep_entries_at, impnames_at) = SUMMARY.unpack_from(b, 0)
        if versioned:
            raise ValueError('versioned Zen packages are not supported')
        self.names, o = trace_omdd.name_batch(b, SUMMARY.size)
        size, = struct.unpack_from('<q', b, o)
        o += 8
        self.bulk = [BULK.unpack_from(b, o + BULK.size * k) for k in range(size // BULK.size)]
        o += size
        if o != hashes_at:
            raise ValueError('%d bytes between the bulk data map and the export hashes'
                             % (hashes_at - o))
        self.imported_hashes = list(struct.unpack_from('<%dQ' % ((imports_at - hashes_at) // 8),
                                                       b, hashes_at))
        self.imports = list(struct.unpack_from('<%dQ' % ((exports_at - imports_at) // 8),
                                               b, imports_at))
        self.exports = []
        for k in range((bundle_at - exports_at) // EXPORT.size):
            (off, sz, ni, num, outer, cls, sup, tmpl, phash, flags, filt,
             pad) = EXPORT.unpack_from(b, exports_at + EXPORT.size * k)
            at = header_size + off
            self.exports.append(Export(name=ni, number=num, outer=outer, cls=cls, super=sup,
                                       template=tmpl, hash=phash, flags=flags, filter=filt,
                                       pad=pad, offset=off, data=b[at:at + sz]))
        self.bundle = [struct.unpack_from('<II', b, bundle_at + 8 * k)
                       for k in range((dep_at - bundle_at) // 8)]
        self.dep_headers = [DEP_HEADER.unpack_from(b, dep_at + DEP_HEADER.size * k)
                            for k in range((dep_entries_at - dep_at) // DEP_HEADER.size)]
        self.dep_entries = list(struct.unpack_from('<%di' % ((impnames_at - dep_entries_at) // 4),
                                                   b, dep_entries_at))
        names, o = trace_omdd.name_batch(b, impnames_at)
        nums = struct.unpack_from('<%di' % len(names), b, o)
        self.imported_packages = list(zip(names, nums))
        o += 4 * len(names)
        #: bytes between the imported package names and the header size (none when shipped)
        self.header_tail = b[o:header_size]
        ends = [e.offset + len(e.data) for e in self.exports]
        self.tail = b[header_size + max(ends, default=0):]

    # -- names

    @property
    def name(self):
        return join_name(self.names[self.name_index & INDEX_MASK], self.name_number)

    def name_ref(self, text):
        """(name map index, number) of an FName, adding its base to the name map if new."""
        base, number = split_name(text)
        if base not in self.names:
            self.names.append(base)
        return self.names.index(base), number

    def export_name(self, export):
        return join_name(self.names[export.name & INDEX_MASK], export.number)

    # -- edits

    def rename_name(self, old, new):
        """Change a name map entry: every FName of this package that uses it changes."""
        k = self.names.index(old)
        if new in self.names:
            raise ValueError('%r is already in the name map' % (new,))
        self.names[k] = new

    def export_path(self, k):
        """Package-relative path of export `k` ('Asset/Child'), from its outer chain."""
        parts, seen = [], set()
        while k not in seen:
            seen.add(k)
            e = self.exports[k]
            parts.append(self.export_name(e))
            if e.outer == NULL_INDEX or e.outer >> 62 != KIND_EXPORT:
                break
            k = e.outer
        return '/'.join(reversed(parts))

    def rehash_exports(self):
        """Give every public export (hash not 0) the hash of its path."""
        for k, e in enumerate(self.exports):
            if e.hash:
                e.hash = export_hash(self.export_path(k))

    def move(self, new_name):
        """Move the package to `new_name` ('/Game/Path/Asset'): the package name entry, and
        the asset's name when an export is named as the old package's last path part, then
        the public export hashes. Returns the new package id."""
        old = self.name
        if self.name_number or split_name(new_name)[1]:
            raise ValueError('package names with a number are not supported')
        old_asset, new_asset = old.rsplit('/', 1)[1], new_name.rsplit('/', 1)[1]
        self.names[self.name_index & INDEX_MASK] = new_name
        if old_asset != new_asset and old_asset in self.names:
            self.rename_name(old_asset, new_asset)
        self.rehash_exports()
        return package_id(new_name)

    def add_import(self, package, path):
        """Import `path` ('Asset' or 'Asset/Child') of package `package`; returns its
        FPackageIndex (negative). An import already there is reused."""
        ref = split_name(package)
        if ref not in self.imported_packages:
            self.imported_packages.append(ref)
        h = export_hash(path)
        if h not in self.imported_hashes:
            self.imported_hashes.append(h)
        index = (KIND_PACKAGE << 62 | self.imported_packages.index(ref) << 32
                 | self.imported_hashes.index(h))
        return self._import_slot(index)

    def add_script_import(self, path):
        """Import a native object ('/Script/Engine.StaticMesh'); returns its FPackageIndex."""
        return self._import_slot(script_import(path))

    def _import_slot(self, index):
        if index not in self.imports:
            self.imports.append(index)
        return -self.imports.index(index) - 1

    def drop_import(self, ref):
        """Make an import (FPackageIndex, negative) null. Indices stay as they are, so
        references in export data need no rewriting; a null import resolves to nothing."""
        self.imports[-ref - 1] = NULL_INDEX

    def object_index(self, ref):
        """The FPackageObjectIndex (export map form) of an FPackageIndex."""
        if ref > 0:
            return ref - 1
        return self.imports[-ref - 1] if ref < 0 else NULL_INDEX

    def add_export(self, name, data, cls, outer=0, super_=0, template=0, flags=0, public=False):
        """Append an export: `cls`, `outer`, `super_`, `template` are FPackageIndex values
        (0 for none), `data` its serialized bytes. It is created and serialized last, with
        no dependencies. Returns its FPackageIndex (positive)."""
        ni, number = self.name_ref(name)
        e = Export(name=ni, number=number, cls=self.object_index(cls),
                   outer=self.object_index(outer), super=self.object_index(super_),
                   template=self.object_index(template), flags=flags, data=bytes(data))
        self.exports.append(e)
        k = len(self.exports) - 1
        if public:
            e.hash = export_hash(self.export_path(k))
        self.bundle += [(k, CREATE), (k, SERIALIZE)]
        self.dep_headers.append((len(self.dep_entries), 0, 0, 0, 0))
        return k + 1

    def remove_export(self, ref):
        """Remove an export (FPackageIndex, positive) that nothing in the export map, the
        export bundle's dependencies or the other exports' outers refers to. Later exports
        move down one; references to them in the tables are renumbered, references in
        export data are not (the caller rewrites those, as with property edits)."""
        k = ref - 1
        for j, e in enumerate(self.exports):
            if j != k and k in (e.outer, e.cls, e.super, e.template):
                raise ValueError('export %d refers to export %d' % (j, k))
        if ref in self.dep_entries:
            raise ValueError('a dependency refers to export %d' % k)
        first, *counts = self.dep_headers[k]
        n = sum(counts)
        del self.dep_entries[first:first + n]
        self.dep_headers = [(f - n if f > first else f, *c) for j, (f, *c) in
                            enumerate(self.dep_headers) if j != k]
        del self.exports[k]
        shift = lambda v: v - 1 if v != NULL_INDEX and v >> 62 == KIND_EXPORT and v > k else v
        for e in self.exports:
            e.outer, e.cls, e.super, e.template = map(shift, (e.outer, e.cls, e.super, e.template))
        self.dep_entries = [v - 1 if v > ref else v for v in self.dep_entries]
        self.bundle = [(j - 1 if j > k else j, c) for j, c in self.bundle if j != k]

    # -- writing

    def layout(self):
        """Exports in data order: by their parsed offset, new ones (offset None) last."""
        return sorted(self.exports, key=lambda e: (e.offset is None, e.offset or 0))

    def to_bytes(self):
        """The package chunk. Export serial offsets follow `layout`, back to back."""
        names = write_name_batch(self.names)
        bulk = struct.pack('<q', BULK.size * len(self.bulk)) + b''.join(
            BULK.pack(*e) for e in self.bulk)
        hashes_at = SUMMARY.size + len(names) + len(bulk)
        imports_at = hashes_at + 8 * len(self.imported_hashes)
        exports_at = imports_at + 8 * len(self.imports)
        bundle_at = exports_at + EXPORT.size * len(self.exports)
        dep_at = bundle_at + 8 * len(self.bundle)
        dep_entries_at = dep_at + DEP_HEADER.size * len(self.dep_headers)
        impnames_at = dep_entries_at + 4 * len(self.dep_entries)
        impnames = write_name_batch([n for n, _ in self.imported_packages]) + struct.pack(
            '<%di' % len(self.imported_packages), *[k for _, k in self.imported_packages])
        header_size = impnames_at + len(impnames) + len(self.header_tail)
        offsets, at = {}, 0
        for e in self.layout():
            offsets[id(e)] = at
            at += len(e.data)
        out = [SUMMARY.pack(0, header_size, self.name_index, self.name_number, self.flags,
                            self.cooked_header_size, hashes_at, imports_at, exports_at, bundle_at,
                            dep_at, dep_entries_at, impnames_at),
               names, bulk,
               struct.pack('<%dQ' % len(self.imported_hashes), *self.imported_hashes),
               struct.pack('<%dQ' % len(self.imports), *self.imports)]
        out += [EXPORT.pack(offsets[id(e)], len(e.data), e.name, e.number, e.outer, e.cls,
                            e.super, e.template, e.hash, e.flags, e.filter, e.pad)
                for e in self.exports]
        out += [struct.pack('<II', *x) for x in self.bundle]
        out += [DEP_HEADER.pack(*x) for x in self.dep_headers]
        out += [struct.pack('<%di' % len(self.dep_entries), *self.dep_entries), impnames,
                self.header_tail]
        out += [e.data for e in self.layout()] + [self.tail]
        return b''.join(out)
