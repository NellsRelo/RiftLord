"""Read Unreal's .usmap property mappings -- the only record of ENGINE classes here.

The UHT header dump covers OMD's 3,596 headers but carries no Engine module, so a
stock UE class like `UNavigationSystemConfig` appears in these tools only as a
cross-module include comment. `Mappings.usmap` has the real property layout of every
class in the build, and this game's copy is version 0 and uncompressed.

Format, version 0: magic `0xC430`, a version byte, a compression byte, the compressed
and decompressed sizes, then the body -- a name table, an enum table, and a struct
table. A name is a byte length then ASCII. A property's type is a tag byte with
recursive inner types for arrays, sets and maps.

    python3 usmap.py NavigationSystemConfig
"""
import struct
import sys

MAGIC = 0x30C4

#: EPropertyType, in the order the cooker writes it.
KINDS = [
    'Byte', 'Bool', 'Int', 'Float', 'Object', 'Name', 'Delegate', 'Double',
    'Array', 'Struct', 'Str', 'Text', 'Interface', 'MulticastDelegate',
    'WeakObject', 'LazyObject', 'AssetObject', 'SoftObject', 'UInt64',
    'UInt32', 'UInt16', 'Int64', 'Int16', 'Int8', 'Map', 'Set', 'Enum',
    'FieldPath',
]


class R:
    def __init__(self, data, off=0):
        self.d, self.o = data, off

    def u8(self):
        v = self.d[self.o]
        self.o += 1
        return v

    def u16(self):
        v = struct.unpack_from('<H', self.d, self.o)[0]
        self.o += 2
        return v

    def i32(self):
        v = struct.unpack_from('<i', self.d, self.o)[0]
        self.o += 4
        return v

    def name(self):
        n = self.u8()
        s = self.d[self.o:self.o + n].decode('utf-8', 'replace')
        self.o += n
        return s


def _type(r, names):
    """One property type, recursively. Returns a printable string."""
    kind = KINDS[r.u8()] if r.d[r.o] < len(KINDS) else 'Kind%d' % r.u8()
    if kind in ('Array', 'Set'):
        return '%s<%s>' % (kind, _type(r, names))
    if kind == 'Map':
        k = _type(r, names)
        v = _type(r, names)
        return 'Map<%s, %s>' % (k, v)
    if kind in ('Struct', 'Enum'):
        idx = r.i32()
        inner = names[idx] if 0 <= idx < len(names) else '?'
        if kind == 'Enum':
            # an enum property carries its underlying type as well
            under = _type(r, names)
            return 'Enum<%s:%s>' % (inner, under)
        return 'Struct<%s>' % inner
    return kind


def load(path):
    """{class name: (super name, [(index, name, type)])}"""
    b = open(path, 'rb').read()
    magic, ver, comp = struct.unpack_from('<HBB', b, 0)
    if magic != MAGIC:
        raise SystemExit('not a usmap: magic %#x' % magic)
    if comp != 0:
        raise SystemExit('compressed usmap (method %d) not supported' % comp)
    csize, dsize = struct.unpack_from('<II', b, 4)
    body = b[12:12 + dsize]
    r = R(body)

    names = [r.name() for _ in range(r.i32())]

    for _ in range(r.i32()):                       # enums, skipped
        r.i32()
        for _ in range(r.u8()):
            r.i32()

    out = {}
    for _ in range(r.i32()):
        cls = names[r.i32()]
        sup_i = r.i32()
        sup = names[sup_i] if 0 <= sup_i < len(names) else None
        r.u16()                                    # property count, with holes
        # Both counts are uint16. Reading the second as int32 swallowed two bytes of
        # the first property and every name index after it went out of range -- the
        # error surfaced 40 structs later, nowhere near the cause.
        entries = r.u16()
        props = []
        for _ in range(entries):
            index = r.u16()
            span = r.u8()
            pname = names[r.i32()]
            props.append((index, pname, _type(r, names), span))
        out[cls] = (sup, props)
    return out


def main(argv):
    path = ('/home/nellsrelo/.local/share/Steam/steamapps/common/Orcs Must Die 3'
            '/OMD/Binaries/Win64/Mappings.usmap')
    table = load(path)
    print('%d classes' % len(table))
    for want in argv or ['NavigationSystemConfig']:
        hits = [c for c in table if want.lower() in c.lower()]
        for c in sorted(hits):
            sup, props = table[c]
            print('\n=== %s : %s ===' % (c, sup))
            for index, pname, ptype, span in props:
                print('   [%3d] %-34s %s%s' % (index, pname, ptype,
                                               '' if span == 1 else ' x%d' % span))


if __name__ == '__main__':
    main(sys.argv[1:])
