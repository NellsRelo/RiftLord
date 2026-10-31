"""Read a cooked package back the way the engine does, and refuse a bad one.

Every writer here patches bytes in place, and a patch that is wrong by one byte
still produces a file that opens, parses and lists the right rows -- our own
readers skip by the size fields, so they step over exactly the damage they
caused. The game does not skip. It parses each value and checks it finished
where the tag said it would, and a file that fails that check is reported as
corrupt data and takes the game down on start.

So this module deliberately does not reuse the readers it is checking. It walks
the property stream by parsing values, insists every tag consumes exactly its
own size, and resolves every FName index against the name table it was written
with.

    python3 pkgcheck.py Some.uasset Some.uexp --table

What it catches, all of which shipped at one time or another:

* an array grown without updating the element tag's own size field
* a nested struct copied from another package with its FNames left pointing at
  the other package's name table
* a header whose offsets no longer agree with the file's length

What it cannot catch is meaning: a row that names a map that does not exist is
structurally perfect.
"""

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.formats.ue.uassetio import fname_hashes  # noqa: E402

MAGIC = 0x9E2A83C1
EXPORT_ENTRY = 104
IMPORT_ENTRY = 28

#: Structs the engine serialises itself, and the bytes each one occupies.
#: Anything not listed is a tagged property stream ending in None.
NATIVE = {'Guid': 16, 'Vector': 12, 'Vector2D': 8, 'Vector4': 16,
          'Rotator': 12, 'LinearColor': 16, 'Color': 4, 'IntPoint': 8,
          'Quat': 16, 'Box': 25, 'Box2D': 20, 'DateTime': 8, 'Timespan': 8,
          'FrameNumber': 4, 'Int32Interval': 12, 'FloatInterval': 12,
          'PerPlatformFloat': 8, 'PerPlatformInt': 8, 'PerPlatformBool': 8}

#: Native structs of variable size: a function that returns the bytes they occupy.
NATIVE_SIZED = {
    # FSkeletalMeshSamplingLODBuiltData: TArray<float> Prob, TArray<int32> Alias, float
    'SkeletalMeshSamplingLODBuiltData': lambda b, o: (
        lambda n1: (lambda n2: 4 + 4 * n1 + 4 + 4 * n2 + 4)(
            struct.unpack_from('<i', b, o + 4 + 4 * n1)[0]))(struct.unpack_from('<i', b, o)[0]),
    # FSoftObjectPath / FSoftClassPath: the asset path FName, then the sub-path FString
    'SoftObjectPath': lambda b, o: 12 + _fstring_bytes(struct.unpack_from('<i', b, o + 8)[0]),
    'SoftClassPath': lambda b, o: 12 + _fstring_bytes(struct.unpack_from('<i', b, o + 8)[0]),
}


def _fstring_bytes(n):
    """Bytes of an FString's characters after its length field."""
    return -2 * n if n < 0 else n

#: Property values of a fixed width, as they appear inside an array.
WIDTH = {'IntProperty': 4, 'FloatProperty': 4, 'DoubleProperty': 8,
         'Int64Property': 8, 'UInt64Property': 8, 'UInt32Property': 4,
         'Int16Property': 2, 'UInt16Property': 2, 'Int8Property': 1,
         'ByteProperty': 1, 'ObjectProperty': 4, 'LazyObjectProperty': 4,
         'InterfaceProperty': 4, 'NameProperty': 8, 'BoolProperty': 1}

#: Bytes of FPropertyTag before the value: name, type, size, index, then the
#: HasPropertyGuid byte that is easy to forget and costs one byte per property.
TAG = 24 + 1


class Corrupt(Exception):
    """The stream does not read back as what was written."""


class Walk:
    def __init__(self, buf, names, o=0):
        self.buf, self.names, self.o = buf, names, o

    def i32(self):
        v = struct.unpack_from('<i', self.buf, self.o)[0]
        self.o += 4
        return v

    def u8(self):
        v = self.buf[self.o]
        self.o += 1
        return v

    def name(self):
        at = self.o
        i = self.i32()
        number = self.i32()
        if not 0 <= i < len(self.names):
            raise Corrupt('at %d: name index %d, the table holds %d'
                          % (at, i, len(self.names)))
        base = self.names[i]
        return base if number == 0 else '%s_%d' % (base, number - 1)


def properties(w, where=''):
    """Read tagged properties up to None. Every tag must consume its size."""
    out = []
    while True:
        if w.o + 8 > len(w.buf):
            raise Corrupt('%s: ran out of bytes looking for None' % where)
        nm = w.name()
        if nm == 'None':
            return out
        kind = w.name()
        size = w.i32()
        w.i32()                                     # array index
        if size < 0:
            raise Corrupt('%s/%s: size %d' % (where, nm, size))
        _value(w, kind, size, '%s/%s' % (where, nm))
        out.append((nm, kind, size))


def _value(w, kind, size, where):
    if kind == 'BoolProperty':
        w.u8()                                      # the value itself
        w.u8()                                      # HasPropertyGuid
        if size != 0:
            raise Corrupt('%s: a BoolProperty of size %d' % (where, size))
        return
    if kind == 'StructProperty':
        sname = w.name()
        w.o += 16 + 1                               # struct guid, HasPropertyGuid
        start = w.o
        _struct_body(w, sname, where)
        if w.o - start != size:
            raise Corrupt('%s: struct %s read %d bytes, its tag says %d'
                          % (where, sname, w.o - start, size))
        return
    if kind == 'ArrayProperty':
        inner = w.name()
        w.u8()
        start = w.o
        count = w.i32()
        if count < 0:
            raise Corrupt('%s: array count %d' % (where, count))
        if inner == 'StructProperty':
            w.name()                                # the element tag's own name
            if w.name() != 'StructProperty':
                raise Corrupt('%s: the element tag is not a struct' % where)
            declared = w.i32()
            w.i32()
            sname = w.name()
            w.o += 16 + 1
            body = w.o
            for k in range(count):
                _struct_body(w, sname, '%s[%d]' % (where, k))
            if w.o - body != declared:
                raise Corrupt('%s: %d element(s) of %s occupy %d bytes, the '
                              'element tag says %d -- an array grown without '
                              'updating it'
                              % (where, count, sname, w.o - body, declared))
        elif inner in ('ByteProperty', 'EnumProperty'):
            # An enum element is an FName (8 bytes); a plain byte is one. The
            # array tag does not name the enum, so the width follows from size.
            width = 8 if inner == 'EnumProperty' else ((size - 4) // count if count else 1)
            if width not in (1, 8) or 4 + width * count != size:
                raise Corrupt('%s: %d %s element(s) cannot fill %d bytes'
                              % (where, count, inner, size))
            for k in range(count):
                if width == 8:
                    w.name()
                else:
                    w.u8()
        else:
            for k in range(count):
                _element(w, inner, '%s[%d]' % (where, k))
        if w.o - start != size:
            raise Corrupt('%s: the array read %d bytes, its tag says %d'
                          % (where, w.o - start, size))
        return
    if kind in ('ByteProperty', 'EnumProperty'):
        w.name()                                    # the enum's name
        w.u8()
        if size == 8:
            # A byte property whose enum was cooked as a name stores the name,
            # not an index -- eight bytes where the type otherwise takes one.
            start = w.o
            w.name()
            if w.o - start != size:
                raise Corrupt('%s: enum name read %d bytes, its tag says %d'
                              % (where, w.o - start, size))
            return
    elif kind in ('MapProperty', 'SetProperty'):
        w.name()
        if kind == 'MapProperty':
            w.name()
        w.u8()
        w.o += size                                 # not parsed, only stepped over
        return
    else:
        w.u8()
    start = w.o
    _element(w, kind, where, size)
    if w.o - start != size:
        raise Corrupt('%s: %s read %d bytes, its tag says %d'
                      % (where, kind, w.o - start, size))


def _element(w, kind, where, size=None):
    """One value with no tag of its own: an array element, or a tag's payload."""
    if kind == 'SoftObjectProperty' or kind == 'SoftClassProperty':
        w.name()                                    # the whole asset path
        n = w.i32()                                 # the sub-path FString
        w.o += (-n * 2) if n < 0 else n
        return
    if kind == 'StrProperty':
        n = w.i32()
        w.o += (-n * 2) if n < 0 else n
        return
    if kind == 'TextProperty':
        if size is None:
            raise Corrupt('%s: a TextProperty inside an array' % where)
        w.o += size
        return
    width = WIDTH.get(kind)
    if width is None:
        if size is None:
            raise Corrupt('%s: %s has no known width' % (where, kind))
        w.o += size
        return
    w.o += width


def _struct_body(w, sname, where):
    if sname in NATIVE_SIZED:
        w.o += NATIVE_SIZED[sname](w.buf, w.o)
        return
    width = NATIVE.get(sname)
    if width is None:
        properties(w, where)
    else:
        w.o += width


# -- the package around it -------------------------------------------------

def _skip(r, each):
    """Step over a counted array. The count is read first, then the elements."""
    count = r.i32()
    r.o += count * each


def _summary(ua):
    r = Walk(ua, [])
    if struct.unpack_from('<I', ua, 0)[0] != MAGIC:
        raise Corrupt('not a cooked package')
    r.o = 4
    r.o += 16                                       # the four version fields
    _skip(r, 20)                                    # custom versions
    s = {'total': r.i32()}
    n = r.i32()
    r.o += (-n * 2) if n < 0 else n                 # folder name
    r.i32()                                         # package flags
    s['name_count'], s['name_off'] = r.i32(), r.i32()
    r.o += 8                                        # gatherable text
    s['export_count'], s['export_off'] = r.i32(), r.i32()
    s['import_count'], s['import_off'] = r.i32(), r.i32()
    s['depends_off'] = r.i32()
    r.i32(); r.i32()                                # soft package references
    r.i32(); r.i32()                                # searchable names, thumbnails
    r.o += 16                                       # package guid
    _skip(r, 8)                                     # generations
    for _ in range(2):                              # saved by, compatible with
        r.o += 10
        n = r.i32()
        r.o += (-n * 2) if n < 0 else n
    r.i32(); r.i32(); r.i32(); r.i32()              # compression, source, extras
    s['asset_registry_off'] = r.i32()
    s['bulk_start'] = struct.unpack_from('<q', ua, r.o)[0]
    r.o += 8
    s['world_tile_off'] = r.i32()
    _skip(r, 4)                                     # chunk ids
    s['preload_count'], s['preload_off'] = r.i32(), r.i32()
    return s


def header(uasset, uexp, label='package'):
    """Structural problems: offsets, table bounds, payload placement, hashes."""
    bad = []
    try:
        s = _summary(uasset)
    except (Corrupt, struct.error) as e:
        return ['%s: %s' % (label, e)]

    if s['total'] != len(uasset):
        bad.append('%s: the header claims %d bytes, the .uasset is %d'
                   % (label, s['total'], len(uasset)))
    w = Walk(uasset, [], s['name_off'])
    names = []
    try:
        for _ in range(s['name_count']):
            n = w.i32()
            raw = uasset[w.o:w.o + ((-n * 2) if n < 0 else n)]
            w.o += len(raw)
            text = (raw.decode('utf-16-le') if n < 0
                    else raw.decode('utf-8', 'replace')).rstrip('\0')
            names.append((text, bytes(uasset[w.o:w.o + 4])))
            w.o += 4
    except (IndexError, struct.error):
        return bad + ['%s: the name table does not parse' % label]
    if w.o != s['import_off']:
        bad.append('%s: the name table ends at %d, imports start at %d'
                   % (label, w.o, s['import_off']))
    wrong = [t for t, h in names if h != fname_hashes(t)]
    if wrong:
        bad.append('%s: %d name hash(es) are wrong, such as %s'
                   % (label, len(wrong), ', '.join(wrong[:3])))
    if s['import_off'] + s['import_count'] * IMPORT_ENTRY != s['export_off']:
        bad.append('%s: the import table does not end where exports start'
                   % label)
    if s['export_off'] + s['export_count'] * EXPORT_ENTRY != s['depends_off']:
        bad.append('%s: the export table does not end at DependsOffset' % label)
    if s['preload_off'] and \
            s['preload_off'] + s['preload_count'] * 4 > len(uasset):
        bad.append('%s: the preload table runs past the end of the .uasset'
                   % label)

    #: An export's four package-index fields and its name. A package index is 0 for
    #: null, +n for export n-1 and -n for import n-1, so anything outside that range
    #: is a corrupt reference. Worth checking because writing a value at the wrong
    #: offset in the export table lands HERE: it crashes the game on load while every
    #: offset, size and hash check still passes. That is exactly how a SerialSize
    #: written at offset 8 instead of 28 reached the game as a crash.
    INDEX_FIELDS = (('ClassIndex', 0), ('SuperIndex', 4),
                    ('TemplateIndex', 8), ('OuterIndex', 12))
    for i in range(s['export_count']):
        at = s['export_off'] + i * EXPORT_ENTRY
        for fname, fo in INDEX_FIELDS:
            v = struct.unpack_from('<i', uasset, at + fo)[0]
            if v > s['export_count'] or v < -s['import_count']:
                bad.append('%s: export %d has %s = %d, which resolves to no import '
                           'or export (%d imports, %d exports)'
                           % (label, i, fname, v, s['import_count'],
                              s['export_count']))
        ni = struct.unpack_from('<i', uasset, at + 16)[0]
        if not 0 <= ni < s['name_count']:
            bad.append('%s: export %d has an ObjectName index of %d, outside the '
                       '%d-entry name table' % (label, i, ni, s['name_count']))

    cursor = s['total']
    for i in range(s['export_count']):
        at = s['export_off'] + i * EXPORT_ENTRY
        size = struct.unpack_from('<q', uasset, at + 28)[0]
        off = struct.unpack_from('<q', uasset, at + 36)[0]
        first = struct.unpack_from('<i', uasset, at + 84)[0]
        counts = struct.unpack_from('<4i', uasset, at + 88)
        if off != cursor:
            bad.append('%s: export %d starts at %d, the one before it ends at '
                       '%d' % (label, i, off, cursor))
        cursor = off + size
        if off - s['total'] < 0 or cursor - s['total'] > len(uexp):
            bad.append('%s: export %d lies outside the .uexp' % (label, i))
        if first >= 0 and first + sum(counts) > s['preload_count']:
            bad.append('%s: export %d names preload entries past the table'
                       % (label, i))
    if cursor - s['total'] + 4 != len(uexp):
        bad.append('%s: the exports fill %d bytes and the .uexp is %d'
                   % (label, cursor - s['total'], len(uexp)))
    if len(uexp) >= 4 and struct.unpack_from('<I', uexp, len(uexp) - 4)[0] != MAGIC:
        bad.append('%s: the .uexp does not end with the package tag' % label)
    if s['bulk_start'] and s['bulk_start'] != cursor:
        bad.append('%s: BulkDataStartOffset is %d, the exports end at %d'
                   % (label, s['bulk_start'], cursor))
    return bad


def _names_of(uasset):
    s = _summary(uasset)
    w = Walk(uasset, [], s['name_off'])
    out = []
    for _ in range(s['name_count']):
        n = w.i32()
        raw = uasset[w.o:w.o + ((-n * 2) if n < 0 else n)]
        w.o += len(raw) + 4
        out.append((raw.decode('utf-16-le') if n < 0
                    else raw.decode('utf-8', 'replace')).rstrip('\0'))
    return s, out


def contents(uasset, uexp, label='package', kind='object'):
    """Property-level problems in the last export: the table, or the CDO."""
    try:
        s, names = _names_of(uasset)
    except (Corrupt, struct.error, IndexError) as e:
        return ['%s: %s' % (label, e)]
    if not s['export_count']:
        return []
    at = s['export_off'] + (s['export_count'] - 1) * EXPORT_ENTRY
    size = struct.unpack_from('<q', uasset, at + 28)[0]
    off = struct.unpack_from('<q', uasset, at + 36)[0] - s['total']
    if off < 0 or off + size > len(uexp):
        return ['%s: the last export lies outside the .uexp' % label]
    data = uexp[off:off + size]
    w = Walk(data, names)
    try:
        properties(w, label)
        if kind == 'table':
            w.o += 4                        # the int32 before the row count
            rows = w.i32()
            for i in range(rows):
                at_row = w.o
                row = w.name()
                try:
                    properties(w, '%s:%s' % (label, row))
                except Corrupt as e:
                    return ['%s: row %d (%s) at %d: %s'
                            % (label, i, row, at_row, e)]
        if kind == 'table' and w.o != len(data):
            return ['%s: read %d of the export\'s %d bytes'
                    % (label, w.o, len(data))]
    except Corrupt as e:
        return ['%s: %s' % (label, e)]
    return []


def problems(uasset, uexp, label='package', kind='object'):
    """Everything wrong with a package, structural first."""
    bad = header(uasset, uexp, label)
    return bad if bad else contents(uasset, uexp, label, kind)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('uasset')
    ap.add_argument('uexp')
    ap.add_argument('--table', action='store_true',
                    help='the last export is a DataTable, so read its rows')
    args = ap.parse_args()
    with open(args.uasset, 'rb') as fh:
        ua = fh.read()
    with open(args.uexp, 'rb') as fh:
        ux = fh.read()
    bad = problems(ua, ux, os.path.basename(args.uasset),
                   'table' if args.table else 'object')
    for line in bad:
        print(line)
    print('%s: %s' % (os.path.basename(args.uasset),
                      'reads back clean' if not bad else 'CORRUPT'))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
