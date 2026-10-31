"""A cooked UE 4.27 package as a structured model, read and written field by field.

`uassetio.Package` keeps half the summary as an opaque blob and patches offsets
inside it. That is right for editing a shipped package, and wrong for building
one: a package we author has no donor to copy a blob from. So this module
parses EVERY field of `FPackageFileSummary` (PackageFileSummary.cpp, operator<<)
and every header section, and the writer derives every offset, count and size
from the model alone.

Layout of a cooked header, in the order SavePackage.cpp writes it:

    summary
    name map              FString + two uint16 hashes per name
    gatherable text       absent in cooked packages (count 0, offset 0)
    import map            28 bytes each
    export map            104 bytes each
    depends map           one empty TArray (int32 0) per export when cooking
    asset registry data   int32 object count; 0 when PKG_FilterEditorOnly
    world tile info       maps only, kept as bytes
    preload dependencies  int32 FPackageIndex each; the header ends here

Soft package references, searchable names and thumbnails are only written when
not filtering editor-only data, which no cooked package does; the reader
refuses a package that has them rather than dropping them.

The export payloads live in the .uexp, back to back in export order, followed
by the 4-byte package tag.

    python3 -m bpkit.package Some.uasset [Some.uexp]
"""

import struct
import zlib

TAG = 0x9E2A83C1
IMPORT_SIZE = 28
EXPORT_SIZE = 104
PKG_FILTER_EDITOR_ONLY = 0x80000000


class Reader:
    def __init__(self, buf, off=0):
        self.b = buf
        self.o = off

    def take(self, fmt):
        v = struct.unpack_from(fmt, self.b, self.o)
        self.o += struct.calcsize(fmt)
        return v if len(v) > 1 else v[0]

    def raw(self, n):
        v = bytes(self.b[self.o:self.o + n])
        if len(v) != n:
            raise ValueError('read past the end at %d' % self.o)
        self.o += n
        return v

    def fstring(self):
        """An FString, remembering whether it was stored wide."""
        n = self.take('<i')
        if n == 0:
            return FString('', False, True)
        if n < 0:
            data = self.raw(-n * 2)
            return FString(data[:-2].decode('utf-16-le'), True, data[-2:] == b'\0\0')
        data = self.raw(n)
        return FString(data[:-1].decode('latin-1'), False, data[-1:] == b'\0')


class FString(str):
    """A str that remembers how it was stored, so a round trip is exact.

    The engine writes a string as ANSI when every character fits, else as
    UTF-16, and always with a terminator; an empty string is a bare zero
    length. `wide` and `terminated` only matter for strings that were read.
    """

    def __new__(cls, text, wide=None, terminated=True):
        s = str.__new__(cls, text)
        s.wide = any(ord(c) > 127 for c in text) if wide is None else wide
        s.terminated = terminated
        return s


def write_fstring(s):
    if s == '':
        return struct.pack('<i', 0)
    wide = getattr(s, 'wide', any(ord(c) > 127 for c in s))
    if wide:
        data = s.encode('utf-16-le') + b'\0\0'
        return struct.pack('<i', -(len(data) // 2)) + data
    return struct.pack('<i', len(s) + 1) + s.encode('latin-1') + b'\0'


#: UE's CRCTable_DEPRECATED, for the non-case-preserving half of a name's hash.
_CRC = []
for _i in range(256):
    _c = (_i << 24) & 0xFFFFFFFF
    for _ in range(8):
        _c = ((_c << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if _c & 0x80000000 else (_c << 1) & 0xFFFFFFFF
    _CRC.append(_c)


def _ascii_upper(ch):
    """TChar::ToUpper, which only folds a-z."""
    return chr(ord(ch) - 32) if 'a' <= ch <= 'z' else ch


def name_hashes(text):
    """The two uint16 hashes the name map stores after each name.

    The first is FCrc::Strihash_DEPRECATED over the ASCII-uppercased name. A
    name stored wide (any character above 127) feeds each character as two
    bytes, low then high; an ANSI one feeds one. The second is FCrc::StrCrc32,
    a CRC-32 over each character as a uint32.
    """
    wide = any(ord(c) > 127 for c in text)
    h = 0
    for ch in map(_ascii_upper, text):
        for byte in ((ord(ch) & 255, ord(ch) >> 8) if wide else (ord(ch),)):
            h = (((h >> 8) & 0x00FFFFFF) ^ _CRC[(h ^ byte) & 0xFF]) & 0xFFFFFFFF
    units = b''.join(struct.pack('<I', ord(c)) for c in text)
    return struct.pack('<HH', h & 0xFFFF, zlib.crc32(units) & 0xFFFF)


def package_source(asset_name):
    """FPackageFileSummary.PackageSource: FCrc::StrCrc_DEPRECATED of the
    uppercased file name, which feeds each character as two bytes, low then high
    (SavePackage.cpp, "create the package source")."""
    crc = 0xFFFFFFFF
    for ch in asset_name.upper():
        for byte in (ord(ch) & 255, (ord(ch) >> 8) & 255):
            crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC[(crc >> 24) ^ byte]
    return ~crc & 0xFFFFFFFF


def authored_summary(package_path, flags=PKG_FILTER_EDITOR_ONLY):
    """The summary a package we build carries, as every cooked OMD3 package does.

    Measured across the game's Blueprints and the four installed Blueprint
    mods: unversioned (file version 0, no custom versions), folder "None",
    PKG_FilterEditorOnly only, empty engine versions, no chunk ids. The engine
    gives each cook a random Guid; this one is derived from the path instead,
    so rebuilding the same mod gives the same bytes. Add
    PKG_RequiresLocalizationGather (0x00040000) to `flags` when a payload holds
    localizable text.
    """
    import hashlib
    s = Summary()
    s.flags = flags
    s.legacy_ue3_version = 0
    s.guid = hashlib.md5(package_path.encode('utf-8')).digest()
    s.package_source = package_source(package_path.rsplit('/', 1)[-1])
    return s


class EngineVersion:
    """FEngineVersion: three uint16, a uint32 changelist and a branch FString."""

    __slots__ = ('major', 'minor', 'patch', 'changelist', 'branch')

    def __init__(self, major=0, minor=0, patch=0, changelist=0, branch=''):
        self.major, self.minor, self.patch = major, minor, patch
        self.changelist, self.branch = changelist, branch

    @classmethod
    def read(cls, r):
        major, minor, patch, cl = r.take('<HHHI')
        return cls(major, minor, patch, cl, r.fstring())

    def write(self):
        return struct.pack('<HHHI', self.major, self.minor, self.patch,
                           self.changelist) + write_fstring(self.branch)

    def __eq__(self, other):
        return all(getattr(self, k) == getattr(other, k) for k in self.__slots__)


class Summary:
    """Every field of FPackageFileSummary that a cooked 4.27 package carries.

    Offsets and counts that the writer derives are read for checking but never
    used to write; the rest is content.
    """

    def __init__(self):
        self.legacy_version = -7
        self.legacy_ue3_version = 864
        self.file_version = 0
        self.licensee_version = 0
        self.custom_versions = []          # [(guid bytes, version)]
        self.folder = FString('None')
        self.flags = PKG_FILTER_EDITOR_ONLY
        self.guid = b'\0' * 16
        self.generations = None            # None: derive one from the counts
        self.saved_by = EngineVersion()
        self.compatible_with = EngineVersion()
        self.compression_flags = 0
        self.package_source = 0
        self.additional_packages = []
        self.chunk_ids = []
        self.read_offsets = {}             # what the file said, for checking


class Import:
    """FObjectImport: ClassPackage, ClassName, Outer, ObjectName (FNames as (index, number))."""

    __slots__ = ('class_package', 'class_name', 'outer', 'object_name')

    def __init__(self, class_package, class_name, outer, object_name):
        self.class_package, self.class_name = class_package, class_name
        self.outer, self.object_name = outer, object_name

    @classmethod
    def read(cls, r):
        cp, cpn, cn, cnn, outer, on, onn = r.take('<iiiiiii')
        return cls((cp, cpn), (cn, cnn), outer, (on, onn))

    def write(self):
        return struct.pack('<iiiiiii', *self.class_package, *self.class_name,
                           self.outer, *self.object_name)


class Export:
    """FObjectExport as 4.27 cooks it, plus the export's payload.

    `serial_size` and `serial_offset` are not stored: the writer derives them
    from `data` and the layout. The preload dependencies are held per export,
    as the four lists the engine keeps, so nothing outside the export has to
    agree on table positions.
    """

    __slots__ = ('class_index', 'super_index', 'template_index', 'outer_index',
                 'object_name', 'object_flags', 'forced_export', 'not_for_client',
                 'not_for_server', 'package_guid', 'package_flags',
                 'not_always_loaded_for_editor_game', 'is_asset', 'preload', 'data',
                 'read_serial', 'read_first_dependency')

    #: The preload dependency kinds, in the order their counts are stored.
    KINDS = ('serialization_before_serialization', 'create_before_serialization',
             'serialization_before_create', 'create_before_create')

    def __init__(self):
        self.class_index = self.super_index = self.template_index = self.outer_index = 0
        self.object_name = (0, 0)
        self.object_flags = 0
        self.forced_export = self.not_for_client = self.not_for_server = 0
        self.package_guid = b'\0' * 16
        self.package_flags = 0
        self.not_always_loaded_for_editor_game = 0
        self.is_asset = 0
        self.preload = {k: [] for k in self.KINDS}
        self.data = b''
        self.read_serial = None
        self.read_first_dependency = None

    @classmethod
    def read(cls, r):
        e = cls()
        (e.class_index, e.super_index, e.template_index, e.outer_index,
         name, number, e.object_flags, size, offset, e.forced_export,
         e.not_for_client, e.not_for_server) = r.take('<iiiiiiIqqiii')
        e.object_name = (name, number)
        e.package_guid = r.raw(16)
        (e.package_flags, e.not_always_loaded_for_editor_game, e.is_asset,
         first, a, b, c, d) = r.take('<Iiiiiiii')
        e.read_serial = (size, offset)
        e.read_first_dependency = (first, (a, b, c, d))
        return e

    def write(self, size, offset, first):
        counts = [len(self.preload[k]) for k in self.KINDS]
        return (struct.pack('<iiiiiiIqqiii', self.class_index, self.super_index,
                            self.template_index, self.outer_index, *self.object_name,
                            self.object_flags, size, offset, self.forced_export,
                            self.not_for_client, self.not_for_server)
                + self.package_guid
                + struct.pack('<Iiii4i', self.package_flags,
                              self.not_always_loaded_for_editor_game, self.is_asset,
                              first, *counts))


class Package:
    """A whole cooked package: summary, names, imports, exports, the rest of the header."""

    def __init__(self):
        self.summary = Summary()
        self.names = []          # [FString]
        self.name_hashes = []    # [bytes] as read; None entries are recomputed
        self.imports = []
        self.exports = []
        self.asset_registry = []  # [(path, class, [(key, value)])]
        self.world_tile = b''
        self.trailer = struct.pack('<I', TAG)

    # -- reading ---------------------------------------------------------
    @classmethod
    def read(cls, uasset, uexp=b''):
        p = cls()
        s = p.summary
        r = Reader(uasset)
        if r.take('<I') != TAG:
            raise ValueError('not a UE package')
        s.legacy_version = r.take('<i')
        if s.legacy_version != -7:
            raise ValueError('legacy version %d, only -7 is handled' % s.legacy_version)
        s.legacy_ue3_version, s.file_version, s.licensee_version = r.take('<iii')
        s.custom_versions = [(r.raw(16), r.take('<i')) for _ in range(r.take('<i'))]
        offs = s.read_offsets
        offs['total_header_size'] = r.take('<i')
        s.folder = r.fstring()
        s.flags = r.take('<I')
        if not s.flags & PKG_FILTER_EDITOR_ONLY:
            raise ValueError('not a cooked package (no PKG_FilterEditorOnly)')
        offs['name_count'], offs['name_offset'] = r.take('<ii')
        offs['gatherable_count'], offs['gatherable_offset'] = r.take('<ii')
        offs['export_count'], offs['export_offset'] = r.take('<ii')
        offs['import_count'], offs['import_offset'] = r.take('<ii')
        offs['depends_offset'] = r.take('<i')
        offs['soft_count'], offs['soft_offset'] = r.take('<ii')
        offs['searchable_offset'] = r.take('<i')
        offs['thumbnail_offset'] = r.take('<i')
        s.guid = r.raw(16)
        s.generations = [r.take('<ii') for _ in range(r.take('<i'))]
        s.saved_by = EngineVersion.read(r)
        s.compatible_with = EngineVersion.read(r)
        s.compression_flags = r.take('<I')
        if r.take('<i'):
            raise ValueError('package-level compressed chunks are not handled')
        s.package_source = r.take('<I')
        s.additional_packages = [r.fstring() for _ in range(r.take('<i'))]
        offs['asset_registry_offset'] = r.take('<i')
        offs['bulk_start'] = r.take('<q')
        offs['world_tile_offset'] = r.take('<i')
        s.chunk_ids = [r.take('<i') for _ in range(r.take('<i'))]
        offs['preload_count'], offs['preload_offset'] = r.take('<ii')
        offs['summary_end'] = r.o

        for key in ('gatherable_count', 'soft_count'):
            if offs[key]:
                raise ValueError('%s is %d; editor-only sections are not handled'
                                 % (key, offs[key]))
        for key in ('searchable_offset', 'thumbnail_offset'):
            if offs[key]:
                raise ValueError('%s is set; editor-only sections are not handled' % key)

        r.o = offs['name_offset']
        for _ in range(offs['name_count']):
            p.names.append(r.fstring())
            p.name_hashes.append(r.raw(4))
        offs['names_end'] = r.o

        r.o = offs['import_offset']
        p.imports = [Import.read(r) for _ in range(offs['import_count'])]
        r.o = offs['export_offset']
        p.exports = [Export.read(r) for _ in range(offs['export_count'])]
        offs['exports_end'] = r.o

        r.o = offs['depends_offset']
        for i in range(offs['export_count']):
            if r.take('<i'):
                raise ValueError('export %d has a non-empty depends list' % i)
        offs['depends_end'] = r.o

        r.o = offs['asset_registry_offset']
        for _ in range(r.take('<i')):
            path, klass = r.fstring(), r.fstring()
            tags = [(r.fstring(), r.fstring()) for _ in range(r.take('<i'))]
            p.asset_registry.append((path, klass, tags))
        offs['asset_registry_end'] = r.o

        if offs['world_tile_offset']:
            # Kept as bytes: only a level has one, and it runs to the preload table.
            p.world_tile = r.raw(offs['preload_offset'] - offs['world_tile_offset'])

        r.o = offs['preload_offset']
        count = max(offs['preload_count'], 0)
        table = list(struct.unpack_from('<%di' % count, uasset, r.o)) if count else []
        offs['preload_end'] = r.o + 4 * count

        for i, e in enumerate(p.exports):
            first, counts = e.read_first_dependency
            at = first
            for kind, n in zip(Export.KINDS, counts):
                if n and first < 0:
                    raise ValueError('export %d has dependencies but no first index' % i)
                e.preload[kind] = table[at:at + n]
                at += n

        hdr = len(uasset)
        if uexp:
            for e in p.exports:
                size, offset = e.read_serial
                e.data = uexp[offset - hdr:offset - hdr + size]
            used = max(((e.read_serial[1] - hdr) + e.read_serial[0] for e in p.exports),
                       default=0)
            p.trailer = uexp[used:]
        return p

    # -- writing ---------------------------------------------------------
    def _summary_bytes(self, o):
        """The summary with the offsets in `o`. Its length never depends on them."""
        s = self.summary
        out = bytearray()
        out += struct.pack('<Iiiii', TAG, s.legacy_version, s.legacy_ue3_version,
                           s.file_version, s.licensee_version)
        out += struct.pack('<i', len(s.custom_versions))
        for guid, version in s.custom_versions:
            out += guid + struct.pack('<i', version)
        out += struct.pack('<i', o['total_header_size'])
        out += write_fstring(s.folder)
        out += struct.pack('<I', s.flags)
        out += struct.pack('<ii', len(self.names), o['name_offset'])
        out += struct.pack('<ii', 0, 0)                      # gatherable text
        out += struct.pack('<ii', len(self.exports), o['export_offset'])
        out += struct.pack('<ii', len(self.imports), o['import_offset'])
        out += struct.pack('<i', o['depends_offset'])
        out += struct.pack('<iii', 0, 0, 0)                  # soft refs, searchable names
        out += struct.pack('<i', 0)                          # thumbnails
        out += s.guid
        gens = (s.generations if s.generations is not None
                else [(len(self.exports), len(self.names))])
        out += struct.pack('<i', len(gens))
        for g in gens:
            out += struct.pack('<ii', *g)
        out += s.saved_by.write() + s.compatible_with.write()
        out += struct.pack('<Ii', s.compression_flags, 0)
        out += struct.pack('<I', s.package_source)
        out += struct.pack('<i', len(s.additional_packages))
        for a in s.additional_packages:
            out += write_fstring(a)
        out += struct.pack('<iqi', o['asset_registry_offset'], o['bulk_start'],
                           o['world_tile_offset'])
        out += struct.pack('<i', len(s.chunk_ids))
        for c in s.chunk_ids:
            out += struct.pack('<i', c)
        out += struct.pack('<ii', o['preload_count'], o['preload_offset'])
        return bytes(out)

    def layout(self):
        """Every offset and count, derived from the model. Returns a dict."""
        zero = dict.fromkeys(('total_header_size', 'name_offset', 'export_offset',
                              'import_offset', 'depends_offset', 'asset_registry_offset',
                              'bulk_start', 'world_tile_offset', 'preload_count',
                              'preload_offset'), 0)
        o = dict(zero)
        at = len(self._summary_bytes(zero))
        o['name_offset'] = at
        at += sum(len(write_fstring(n)) + 4 for n in self.names)
        o['import_offset'] = at
        at += IMPORT_SIZE * len(self.imports)
        o['export_offset'] = at
        at += EXPORT_SIZE * len(self.exports)
        o['depends_offset'] = at
        at += 4 * len(self.exports)
        o['asset_registry_offset'] = at
        at += len(self._asset_registry_bytes())
        if self.world_tile:
            o['world_tile_offset'] = at
            at += len(self.world_tile)
        o['preload_offset'] = at
        o['preload_count'] = sum(len(v) for e in self.exports for v in e.preload.values())
        at += 4 * o['preload_count']
        o['total_header_size'] = at
        o['bulk_start'] = at + sum(len(e.data) for e in self.exports)
        return o

    def _asset_registry_bytes(self):
        out = bytearray(struct.pack('<i', len(self.asset_registry)))
        for path, klass, tags in self.asset_registry:
            out += write_fstring(path) + write_fstring(klass)
            out += struct.pack('<i', len(tags))
            for k, v in tags:
                out += write_fstring(k) + write_fstring(v)
        return bytes(out)

    def write(self):
        """(uasset, uexp) bytes."""
        o = self.layout()
        head = bytearray(self._summary_bytes(o))
        for i, n in enumerate(self.names):
            stored = self.name_hashes[i] if i < len(self.name_hashes) else None
            head += write_fstring(n) + (stored if stored is not None else name_hashes(n))
        for imp in self.imports:
            head += imp.write()
        offset = o['total_header_size']
        first = 0
        table = []
        for e in self.exports:
            deps = [d for k in Export.KINDS for d in e.preload[k]]
            head += e.write(len(e.data), offset, first if deps else -1)
            table += deps
            first += len(deps)
            offset += len(e.data)
        head += b'\0' * (4 * len(self.exports))
        head += self._asset_registry_bytes()
        head += self.world_tile
        head += struct.pack('<%di' % len(table), *table)
        assert len(head) == o['total_header_size'], 'layout disagrees with the writer'
        body = b''.join(e.data for e in self.exports) + self.trailer
        return bytes(head), body

    # -- names and references --------------------------------------------
    def name(self, ref):
        """The text of an (index, number) FName, as the engine displays it."""
        index, number = ref
        text = self.names[index]
        return text if number == 0 else '%s_%d' % (text, number - 1)

    def object_name(self, index):
        """The name of the object at a package index: export, import or null."""
        if index > 0:
            return self.name(self.exports[index - 1].object_name)
        if index < 0:
            return self.name(self.imports[-index - 1].object_name)
        return None

    def path(self, index):
        """Full path of a package index, by following outers."""
        parts = []
        while index:
            parts.append(self.object_name(index))
            index = (self.exports[index - 1].outer_index if index > 0
                     else self.imports[-index - 1].outer)
        return '.'.join(reversed(parts))


def main(argv=None):
    import sys
    args = sys.argv[1:] if argv is None else argv
    ua = open(args[0], 'rb').read()
    ux = open(args[1], 'rb').read() if len(args) > 1 else b''
    p = Package.read(ua, ux)
    print('%d names, %d imports, %d exports' % (len(p.names), len(p.imports), len(p.exports)))
    for i, e in enumerate(p.exports, 1):
        print('  %3d  %-28s %-40s %6d' % (i, p.object_name(e.class_index) or 'Class',
                                         p.name(e.object_name), len(e.data)))
    head, body = p.write()
    same = head == ua and (not ux or body == ux)
    print('round trip:', 'identical' if same else 'DIFFERS')


if __name__ == '__main__':
    main()
