"""Read a cooked UE4.27 package and write it back, so exports can be resized.

Generating a Blueprint means emitting export data of a size the original did not
have, which moves every later export and invalidates the summary. So the first
thing that has to work is a faithful read/write of the container: parse a package
into name table, imports, exports and opaque export blobs, then re-emit it.

The proof is a byte-identical round trip. Until that passes on real assets, nothing
built on top can be trusted -- a package writer that is subtly wrong produces files
the game refuses in ways that are painful to diagnose.

Export payloads stay opaque here. Understanding their contents is a separate job;
this layer only has to place them correctly.
"""

import struct

MAGIC = 0x9E2A83C1
EXPORT_ENTRY = 104          # bytes per FObjectExport in 4.27
# Within that entry: Class/Super/Template/Outer (4x int32) = 16, ObjectName FName
# = 8 -> 24, ObjectFlags uint32 -> 28, then the two int64s.
SERIAL_SIZE_AT = 28
SERIAL_OFFSET_AT = 36
IMPORT_ENTRY = 28           # bytes per FObjectImport


class Reader:
    def __init__(self, buf, off=0):
        self.b = buf
        self.o = off

    def i32(self):
        v = struct.unpack_from('<i', self.b, self.o)[0]; self.o += 4; return v

    def u32(self):
        v = struct.unpack_from('<I', self.b, self.o)[0]; self.o += 4; return v

    def i64(self):
        v = struct.unpack_from('<q', self.b, self.o)[0]; self.o += 8; return v

    def raw(self, n):
        v = self.b[self.o:self.o + n]; self.o += n; return v

    def fstring(self):
        n = self.i32()
        if n == 0:
            return ''
        if n < 0:
            return self.raw(-n * 2).decode('utf-16-le').rstrip('\0')
        return self.raw(n).decode('utf-8', 'replace').rstrip('\0')


#: UE's CRCTable_DEPRECATED: the non-reflected 0x04C11DB7 polynomial, each
#: entry seeded with its index shifted into the top byte.
_DEPRECATED_CRC = []
for _i in range(256):
    _c = (_i << 24) & 0xFFFFFFFF
    for _ in range(8):
        _c = (((_c << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if _c & 0x80000000
              else (_c << 1) & 0xFFFFFFFF)
    _DEPRECATED_CRC.append(_c)


def fname_hashes(text):
    """The 4 hash bytes stored after a name in the name table.

    Two uint16: FName's non-case-preserving hash, which is
    FCrc::Strihash_DEPRECATED over the uppercased string, and its
    case-preserving hash, which is a standard CRC-32 over the string as UTF-32
    code units. Verified against all 781 names of a mod-patched MissionProtos,
    including the 66 that mod added.
    """
    import zlib
    h = 0
    for ch in text.upper():
        h = (((h >> 8) & 0x00FFFFFF) ^ _DEPRECATED_CRC[(h ^ ord(ch)) & 0xFF]) & 0xFFFFFFFF
    wide = b''.join(struct.pack('<I', ord(c)) for c in text)
    return struct.pack('<HH', h & 0xFFFF, zlib.crc32(wide) & 0xFFFF)


def write_fstring(s):
    if s == '':
        return struct.pack('<i', 0)
    b = s.encode('utf-8') + b'\0'
    return struct.pack('<i', len(b)) + b


class Package:
    """A cooked package split into its parts, with export payloads kept opaque."""

    def __init__(self, uasset_bytes, uexp_bytes=None):
        self.uasset = uasset_bytes
        self.uexp = uexp_bytes
        r = Reader(uasset_bytes)
        if r.u32() != MAGIC:
            raise ValueError('not a UE package')

        self.legacy = r.i32()
        self.ue3 = r.i32()
        self.file_version = r.i32()
        self.licensee_version = r.i32()
        ncv = r.i32()
        self.custom_versions = r.raw(ncv * 20)
        self.total_header_size_off = r.o
        self.total_header_size = r.i32()
        self.folder = r.fstring()
        self.package_flags = r.u32()

        name_count = r.i32(); name_off = r.i32()
        # Unversioned cooked packages carry no LocalizationId.
        self.gatherable = r.raw(8)
        export_count = r.i32(); export_off = r.i32()
        import_count = r.i32(); import_off = r.i32()
        self.depends_off = r.i32()
        self.rest_of_summary = bytearray(uasset_bytes[r.o:name_off])
        self._parse_summary_tail()

        nr = Reader(uasset_bytes, name_off)
        self.names = []
        for _ in range(name_count):
            s = nr.fstring()
            self.names.append((s, nr.raw(4)))      # keep the two hashes verbatim

        self.imports = [uasset_bytes[import_off + i * IMPORT_ENTRY:
                                     import_off + (i + 1) * IMPORT_ENTRY]
                        for i in range(import_count)]

        self.exports = []
        for i in range(export_count):
            base = export_off + i * EXPORT_ENTRY
            e = bytearray(uasset_bytes[base:base + EXPORT_ENTRY])
            size = struct.unpack_from('<q', e, SERIAL_SIZE_AT)[0]
            offset = struct.unpack_from('<q', e, SERIAL_OFFSET_AT)[0]
            self.exports.append({'meta': e, 'size': size, 'offset': offset,
                                 'data': None})

        self.tail = uasset_bytes[export_off + export_count * EXPORT_ENTRY:]
        self.tail_start = export_off + export_count * EXPORT_ENTRY
        self._name_off = name_off
        self._export_off = export_off
        self._import_off = import_off

        if uexp_bytes is not None:
            self.load_payloads()

    def add_name(self, text):
        """Index of a name, appending it with correct hashes if it is new."""
        for i, (s, _h) in enumerate(self.names):
            if s == text:
                return i
        self.names.append((text, fname_hashes(text)))
        return len(self.names) - 1

    def _parse_summary_tail(self):
        """Locate the absolute offsets stored after DependsOffset.

        Everything from SoftPackageReferencesOffset onward is copied verbatim on
        write, so any offset in it that points into the header goes stale the
        moment the header changes size -- which adding a name or an import does.
        These are the fields that must be shifted.
        """
        rs = self.rest_of_summary
        r = Reader(rs)
        r.i32(); r.i32()                      # soft package references
        r.i32(); r.i32()                      # searchable names, thumbnails
        r.raw(16)                             # package guid
        gens = r.i32(); r.raw(gens * 8)
        for _ in range(2):                    # saved-by and compatible-with
            r.raw(6); r.u32(); r.fstring()
        r.u32(); r.i32()                      # compression flags, chunks
        r.u32(); r.i32()                      # package source, extra packages
        self.asset_registry_at = r.o; self.asset_registry_off = r.i32()
        self.bulk_start_at = r.o; self.bulk_start_off = r.i64()
        self.world_tile_at = r.o; self.world_tile_off = r.i32()
        chunk_ids = r.i32(); r.raw(chunk_ids * 4)
        self.preload_count_at = r.o
        self.preload_count = r.i32()
        self.preload_off_at = r.o
        self.preload_off = r.i32()
        self.summary_tail_parsed = (r.o == len(rs))

        self.preload = []
        self.original_preload_count = self.preload_count
        if self.preload_off and self.preload_count:
            self.preload = list(struct.unpack_from(
                '<%di' % self.preload_count, self.uasset, self.preload_off))

    def add_preload_dependency(self, export_index, package_index,
                               kind='create_before_serialization'):
        """Declare that an export needs another object ready before it loads.

        A cooked export records four counts at meta offset 88 and an index into
        the package's preload table. An object referenced by an export's
        serialised data but absent from that table is used before the loader has
        created it, which crashes on load rather than failing gracefully.
        """
        order = ('serialization_before_serialization',
                 'create_before_serialization',
                 'serialization_before_create',
                 'create_before_create')
        if kind not in order:
            raise ValueError('unknown dependency kind %r' % kind)
        metas = [bytearray(e['meta']) for e in self.exports]
        firsts = [struct.unpack_from('<i', m, 84)[0] for m in metas]
        counts = [list(struct.unpack_from('<4i', m, 88)) for m in metas]
        slot = order.index(kind)
        at = firsts[export_index] + sum(counts[export_index][:slot + 1])
        self.preload.insert(at, package_index)
        counts[export_index][slot] += 1
        for i in range(len(metas)):
            if firsts[i] >= at and i != export_index:
                firsts[i] += 1
        for i, m in enumerate(metas):
            struct.pack_into('<i', m, 84, firsts[i])
            struct.pack_into('<4i', m, 88, *counts[i])
            self.exports[i]['meta'] = bytes(m)
        self.preload_count = len(self.preload)
        return at

    def add_import(self, class_package, class_name, outer, object_name):
        """Append an FObjectImport and return its FPackageIndex (negative).

        28 bytes: ClassPackage FName, ClassName FName, int32 Outer, ObjectName
        FName. A datatable takes two -- a /Script/CoreUObject Package named by
        its full path, then a /Script/Engine DataTable under it.
        """
        rec = struct.pack('<iiiiiii',
                          self.add_name(class_package), 0,
                          self.add_name(class_name), 0,
                          outer,
                          self.add_name(object_name), 0)
        self.imports.append(rec)
        return -len(self.imports)

    def load_payloads(self):
        """Slice each export's bytes out of the .uexp."""
        hdr = len(self.uasset)
        for e in self.exports:
            start = e['offset'] - hdr
            e['data'] = self.uexp[start:start + e['size']]
        used = max((e['offset'] - hdr) + e['size'] for e in self.exports) \
            if self.exports else 0
        self.uexp_trailer = self.uexp[used:]     # the 4-byte package tag

    def write(self):
        """Re-emit (uasset, uexp), recomputing offsets from current payload sizes."""
        names = b''.join(write_fstring(s) + h for s, h in self.names)
        imports = b''.join(self.imports)

        # Header length does not depend on payload sizes, so it can be settled first.
        head = bytearray()
        head += struct.pack('<I', MAGIC)
        head += struct.pack('<iiii', self.legacy, self.ue3,
                            self.file_version, self.licensee_version)
        head += struct.pack('<i', len(self.custom_versions) // 20)
        head += self.custom_versions
        total_at = len(head)
        head += struct.pack('<i', 0)                    # total header size, patched below
        head += write_fstring(self.folder)
        head += struct.pack('<I', self.package_flags)

        name_off_at = len(head) + 4
        head += struct.pack('<ii', len(self.names), 0)  # name count/offset, patched
        head += self.gatherable
        export_off_at = len(head) + 4
        head += struct.pack('<ii', len(self.exports), 0)
        import_off_at = len(head) + 4
        head += struct.pack('<ii', len(self.imports), 0)
        depends_at = len(head)
        head += struct.pack('<i', 0)
        head += self.rest_of_summary

        name_off = len(head)
        head += names
        import_off = len(head)
        head += imports
        export_off = len(head)
        head += b'\0' * (len(self.exports) * EXPORT_ENTRY)   # placeholder
        # The preload table lives in the tail; rebuild it so added dependencies
        # are written and the rest of the tail keeps its shape.
        tail = bytearray(self.tail)
        if self.preload_off and self.preload:
            start = self.preload_off - self.tail_start
            old = self.original_preload_count * 4
            tail[start:start + old] = struct.pack('<%di' % len(self.preload),
                                                  *self.preload)
        head += tail
        header_size = len(head)

        struct.pack_into('<i', head, total_at, header_size)
        struct.pack_into('<i', head, name_off_at, name_off)
        struct.pack_into('<i', head, export_off_at, export_off)
        struct.pack_into('<i', head, import_off_at, import_off)
        # Offsets into the header are rebased on where the tail now starts, not
        # shifted by one delta: the preload table is the last thing in the file,
        # so growing IT must not move its own start.
        tail_at = export_off + len(self.exports) * EXPORT_ENTRY
        shift = tail_at - self.tail_start
        struct.pack_into('<i', head, depends_at, self.depends_off + shift)
        rs_at = depends_at + 4
        for at, value in ((self.asset_registry_at, self.asset_registry_off),
                          (self.world_tile_at, self.world_tile_off),
                          (self.preload_off_at, self.preload_off)):
            if value:
                struct.pack_into('<i', head, rs_at + at, value + shift)
        struct.pack_into('<i', head, rs_at + self.preload_count_at,
                         len(self.preload))

        bulk_at = rs_at + self.bulk_start_at
        # Now lay the payloads out and write the export table over its placeholder.
        body = bytearray()
        for i, e in enumerate(self.exports):
            data = e['data'] if e['data'] is not None else b''
            meta = bytearray(e['meta'])
            struct.pack_into('<q', meta, SERIAL_SIZE_AT, len(data))
            struct.pack_into('<q', meta, SERIAL_OFFSET_AT, header_size + len(body))
            head[export_off + i * EXPORT_ENTRY:
                 export_off + (i + 1) * EXPORT_ENTRY] = meta
            body += data
        # BulkDataStartOffset is where bulk data would begin: the end of the
        # exports, NOT the end of the file -- the .uexp's trailing package tag
        # sits after it and is not counted.
        if self.bulk_start_off:
            struct.pack_into('<q', head, bulk_at, header_size + len(body))
        body += getattr(self, 'uexp_trailer', b'')
        return bytes(head), bytes(body)
