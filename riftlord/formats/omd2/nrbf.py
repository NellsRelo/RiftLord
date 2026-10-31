"""Reader for MS-NRBF, the .NET Remoting Binary Format written by BinaryFormatter.

Orcs Must Die 2's vForge ``.Layer`` files are BinaryFormatter streams, so reading
them means implementing the format rather than pattern-matching the bytes.

Reference: [MS-NRBF], "\\.NET Remoting: Binary Format Data Structure".

Usage:
    from nrbf import parse
    graph = parse('Main layer.Layer')
    for obj in graph.by_class('SaberEditorPlugin.TrapPlacementVolumeShape'):
        print(obj['_shapeName'], obj['_size'])
"""

import struct
from enum import IntEnum


class RecordType(IntEnum):
    SerializedStreamHeader = 0
    ClassWithId = 1
    SystemClassWithMembers = 2
    ClassWithMembers = 3
    SystemClassWithMembersAndTypes = 4
    ClassWithMembersAndTypes = 5
    BinaryObjectString = 6
    BinaryArray = 7
    MemberPrimitiveTyped = 8
    MemberReference = 9
    ObjectNull = 10
    MessageEnd = 11
    BinaryLibrary = 12
    ObjectNullMultiple256 = 13
    ObjectNullMultiple = 14
    ArraySinglePrimitive = 15
    ArraySingleObject = 16
    ArraySingleString = 17
    MethodCall = 21
    MethodReturn = 22


class BinaryType(IntEnum):
    Primitive = 0
    String = 1
    Object = 2
    SystemClass = 3
    Class = 4
    ObjectArray = 5
    StringArray = 6
    PrimitiveArray = 7


class PrimitiveType(IntEnum):
    Boolean = 1
    Byte = 2
    Char = 3
    Decimal = 5
    Double = 6
    Int16 = 7
    Int32 = 8
    Int64 = 9
    SByte = 10
    Single = 11
    TimeSpan = 12
    DateTime = 13
    UInt16 = 14
    UInt32 = 15
    UInt64 = 16
    Null = 17
    String = 18


class NrbfError(Exception):
    pass


class Ref:
    """A forward reference to an object id, resolved once the stream is read."""

    __slots__ = ('id',)

    def __init__(self, oid):
        self.id = oid

    def __repr__(self):
        return 'Ref(%d)' % self.id


class Instance:
    """One class record: a name plus its members."""

    __slots__ = ('id', 'cls', 'library', 'members')

    def __init__(self, oid, cls, library=None):
        self.id = oid
        self.cls = cls
        self.library = library
        self.members = {}

    def __getitem__(self, k):
        return self.members[k]

    def get(self, k, default=None):
        return self.members.get(k, default)

    def __contains__(self, k):
        return k in self.members

    def __repr__(self):
        return '<%s id=%d %d members>' % (self.cls, self.id, len(self.members))


class Array:
    """An array record. ``values`` holds elements, already reference-resolved."""

    __slots__ = ('id', 'values', 'kind')

    def __init__(self, oid, values, kind):
        self.id = oid
        self.values = values
        self.kind = kind

    def __len__(self):
        return len(self.values)

    def __iter__(self):
        return iter(self.values)

    def __getitem__(self, i):
        return self.values[i]

    def __repr__(self):
        return '<%s[%d] id=%d>' % (self.kind, len(self.values), self.id)


class _Cursor:
    def __init__(self, buf):
        self.b = buf
        self.o = 0

    def u8(self):
        v = self.b[self.o]
        self.o += 1
        return v

    def i8(self):
        return struct.unpack_from('<b', self.b, self._adv(1))[0]

    def i16(self):
        return struct.unpack_from('<h', self.b, self._adv(2))[0]

    def u16(self):
        return struct.unpack_from('<H', self.b, self._adv(2))[0]

    def i32(self):
        return struct.unpack_from('<i', self.b, self._adv(4))[0]

    def u32(self):
        return struct.unpack_from('<I', self.b, self._adv(4))[0]

    def i64(self):
        return struct.unpack_from('<q', self.b, self._adv(8))[0]

    def u64(self):
        return struct.unpack_from('<Q', self.b, self._adv(8))[0]

    def f32(self):
        return struct.unpack_from('<f', self.b, self._adv(4))[0]

    def f64(self):
        return struct.unpack_from('<d', self.b, self._adv(8))[0]

    def raw(self, n):
        return self.b[self._adv(n):self.o]

    def _adv(self, n):
        o = self.o
        self.o += n
        if self.o > len(self.b):
            raise NrbfError('read past end of stream at %d' % o)
        return o

    def length(self):
        """The 7-bit encoded length that prefixes every string."""
        n = 0
        shift = 0
        for _ in range(5):
            byte = self.u8()
            n |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return n
            shift += 7
        raise NrbfError('malformed 7-bit length prefix')

    def string(self):
        return self.raw(self.length()).decode('utf-8', 'replace')

    def eof(self):
        return self.o >= len(self.b)


# Primitive readers, keyed by PrimitiveType. Char is UTF-8 and may be multi-byte,
# so it is read one byte at a time until it decodes.
def _read_char(c):
    buf = b''
    for _ in range(4):
        buf += bytes([c.u8()])
        try:
            return buf.decode('utf-8')
        except UnicodeDecodeError:
            continue
    raise NrbfError('malformed Char')


_PRIMITIVE = {
    PrimitiveType.Boolean: lambda c: bool(c.u8()),
    PrimitiveType.Byte: lambda c: c.u8(),
    PrimitiveType.Char: _read_char,
    PrimitiveType.Decimal: lambda c: c.string(),
    PrimitiveType.Double: lambda c: c.f64(),
    PrimitiveType.Int16: lambda c: c.i16(),
    PrimitiveType.Int32: lambda c: c.i32(),
    PrimitiveType.Int64: lambda c: c.i64(),
    PrimitiveType.SByte: lambda c: c.i8(),
    PrimitiveType.Single: lambda c: c.f32(),
    PrimitiveType.TimeSpan: lambda c: c.i64(),
    PrimitiveType.DateTime: lambda c: c.u64(),
    PrimitiveType.UInt16: lambda c: c.u16(),
    PrimitiveType.UInt32: lambda c: c.u32(),
    PrimitiveType.UInt64: lambda c: c.u64(),
    PrimitiveType.Null: lambda c: None,
    PrimitiveType.String: lambda c: c.string(),
}

# A marker the member-value reader returns when it consumed an ObjectNullMultiple
# record, which stands for a run of nulls rather than a single value.
class _NullRun:
    __slots__ = ('count',)

    def __init__(self, count):
        self.count = count


class Graph:
    """The parsed stream: every object by id, plus the root."""

    def __init__(self):
        self.objects = {}
        self.root_id = None
        self.libraries = {}

    @property
    def root(self):
        return self.objects.get(self.root_id)

    def by_class(self, name):
        """Every Instance whose class name matches, in stream order."""
        return [o for o in self.objects.values()
                if isinstance(o, Instance) and o.cls == name]

    def classes(self):
        """Class name -> count, for finding out what a file contains."""
        counts = {}
        for o in self.objects.values():
            if isinstance(o, Instance):
                counts[o.cls] = counts.get(o.cls, 0) + 1
        return counts


class _Parser:
    def __init__(self, buf):
        self.c = _Cursor(buf)
        self.g = Graph()
        self.class_meta = {}      # object id -> (class name, member names, member types)
        self.pending = []         # (container, key, Ref) to resolve at the end

    # -- type metadata ----------------------------------------------------

    def _member_type_info(self, count):
        """BinaryTypeEnums then the AdditionalInfos that only some of them carry."""
        kinds = [self.c.u8() for _ in range(count)]
        infos = []
        for k in kinds:
            if k == BinaryType.Primitive or k == BinaryType.PrimitiveArray:
                infos.append(self.c.u8())
            elif k == BinaryType.SystemClass:
                infos.append(self.c.string())
            elif k == BinaryType.Class:
                infos.append((self.c.string(), self.c.i32()))
            else:
                infos.append(None)
        return list(zip(kinds, infos))

    def _class_info(self):
        oid = self.c.i32()
        name = self.c.string()
        n = self.c.i32()
        members = [self.c.string() for _ in range(n)]
        return oid, name, members

    # -- values -----------------------------------------------------------

    def _typed_value(self, kind, info):
        """Read one member whose static type is already known."""
        if kind == BinaryType.Primitive:
            return _PRIMITIVE[PrimitiveType(info)](self.c)
        if kind == BinaryType.String:
            return self._record_value()
        # Object, SystemClass, Class, ObjectArray, StringArray, PrimitiveArray
        return self._record_value()

    def _record_value(self):
        """Read a value that is expressed as a record (reference, null, nested)."""
        rt = self.c.u8()
        if rt == RecordType.MemberReference:
            return Ref(self.c.i32())
        if rt == RecordType.ObjectNull:
            return None
        if rt == RecordType.ObjectNullMultiple256:
            return _NullRun(self.c.u8())
        if rt == RecordType.ObjectNullMultiple:
            return _NullRun(self.c.i32())
        if rt == RecordType.MemberPrimitiveTyped:
            pt = self.c.u8()
            return _PRIMITIVE[PrimitiveType(pt)](self.c)
        if rt == RecordType.BinaryObjectString:
            oid = self.c.i32()
            s = self.c.string()
            self.g.objects[oid] = s
            return s
        # Anything else is a full record written inline.
        self.c.o -= 1
        return self._record()

    # -- records ----------------------------------------------------------

    def _members(self, oid, name, member_names, types, library=None):
        inst = Instance(oid, name, library)
        self.g.objects[oid] = inst
        i = 0
        while i < len(member_names):
            kind, info = types[i] if types else (BinaryType.Object, None)
            v = self._typed_value(kind, info)
            if isinstance(v, _NullRun):
                # A null run fills this member and the ones that follow.
                for j in range(v.count):
                    if i + j < len(member_names):
                        inst.members[member_names[i + j]] = None
                i += v.count
                continue
            if isinstance(v, Ref):
                self.pending.append((inst.members, member_names[i], v))
            inst.members[member_names[i]] = v
            i += 1
        return inst

    def _record(self):
        c = self.c
        rt = c.u8()

        if rt == RecordType.SerializedStreamHeader:
            root = c.i32()
            c.i32()          # header id
            c.i32()          # major version
            c.i32()          # minor version
            self.g.root_id = root
            return None

        if rt == RecordType.BinaryLibrary:
            lid = c.i32()
            self.g.libraries[lid] = c.string()
            return None

        if rt == RecordType.ClassWithMembersAndTypes:
            oid, name, members = self._class_info()
            types = self._member_type_info(len(members))
            lib = c.i32()
            self.class_meta[oid] = (name, members, types)
            return self._members(oid, name, members, types, self.g.libraries.get(lib))

        if rt == RecordType.SystemClassWithMembersAndTypes:
            oid, name, members = self._class_info()
            types = self._member_type_info(len(members))
            self.class_meta[oid] = (name, members, types)
            return self._members(oid, name, members, types)

        if rt == RecordType.ClassWithMembers:
            oid, name, members = self._class_info()
            c.i32()          # library id
            self.class_meta[oid] = (name, members, None)
            return self._members(oid, name, members, None)

        if rt == RecordType.SystemClassWithMembers:
            oid, name, members = self._class_info()
            self.class_meta[oid] = (name, members, None)
            return self._members(oid, name, members, None)

        if rt == RecordType.ClassWithId:
            oid = c.i32()
            meta_id = c.i32()
            if meta_id not in self.class_meta:
                raise NrbfError('ClassWithId %d refers to unknown metadata %d' % (oid, meta_id))
            name, members, types = self.class_meta[meta_id]
            return self._members(oid, name, members, types)

        if rt == RecordType.BinaryObjectString:
            oid = c.i32()
            s = c.string()
            self.g.objects[oid] = s
            return s

        if rt == RecordType.ArraySinglePrimitive:
            oid = c.i32()
            n = c.i32()
            pt = PrimitiveType(c.u8())
            rd = _PRIMITIVE[pt]
            arr = Array(oid, [rd(c) for _ in range(n)], 'primitive[%s]' % pt.name)
            self.g.objects[oid] = arr
            return arr

        if rt == RecordType.ArraySingleString:
            oid = c.i32()
            n = c.i32()
            arr = Array(oid, [], 'string')
            self.g.objects[oid] = arr
            self._fill(arr, n, BinaryType.String, None)
            return arr

        if rt == RecordType.ArraySingleObject:
            oid = c.i32()
            n = c.i32()
            arr = Array(oid, [], 'object')
            self.g.objects[oid] = arr
            self._fill(arr, n, BinaryType.Object, None)
            return arr

        if rt == RecordType.BinaryArray:
            return self._binary_array()

        if rt == RecordType.MessageEnd:
            return 'END'

        if rt == RecordType.MemberReference:
            return Ref(c.i32())

        if rt == RecordType.ObjectNull:
            return None

        raise NrbfError('unhandled record type %d at offset %d' % (rt, c.o - 1))

    def _binary_array(self):
        c = self.c
        oid = c.i32()
        atype = c.u8()
        rank = c.i32()
        lengths = [c.i32() for _ in range(rank)]
        if atype in (3, 4, 5):          # the *Offset variants carry lower bounds
            [c.i32() for _ in range(rank)]
        kind, info = self._member_type_info(1)[0]
        total = 1
        for L in lengths:
            total *= L
        arr = Array(oid, [], 'binary')
        self.g.objects[oid] = arr
        self._fill(arr, total, kind, info)
        return arr

    def _fill(self, arr, n, kind, info):
        """Read n elements into an array, expanding null runs."""
        while len(arr.values) < n:
            v = self._typed_value(kind, info)
            if isinstance(v, _NullRun):
                arr.values.extend([None] * v.count)
                continue
            if isinstance(v, Ref):
                self.pending.append((arr.values, len(arr.values), v))
            arr.values.append(v)
        del arr.values[n:]

    # -- driver -----------------------------------------------------------

    def run(self):
        while not self.c.eof():
            if self._record() == 'END':
                break
        for container, key, ref in self.pending:
            container[key] = self.g.objects.get(ref.id, ref)
        return self.g


def parse(path_or_bytes):
    """Parse a BinaryFormatter stream from a path or a bytes object."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        buf = bytes(path_or_bytes)
    else:
        with open(path_or_bytes, 'rb') as fh:
            buf = fh.read()
    return _Parser(buf).run()
