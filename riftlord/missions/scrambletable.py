"""Read the Scramble tier table (RandomModeBucketProtos) and other built proto tables.

Forked on 2026-10-23 from modifiermod (bucket_table, read_table, _sub_fields) and
omd2roster.PropDecoder, so Rift Lord does not import the Scramble modifier pipeline.
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build import missionproto  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

BUCKET_TABLE = '/Game/Data/RandomModeBucketProtos'


class PropDecoder:
    """Decode UE4.27 tagged-property payloads into Python values."""

    NATIVE = {'Guid': 16, 'Vector': 12, 'Rotator': 12, 'Vector2D': 8, 'LinearColor': 16,
              'Color': 4, 'IntPoint': 8, 'Quat': 16, 'Vector4': 16,
              'SoftClassPath': 0, 'SoftObjectPath': 0}

    def __init__(self, names):
        self.names = names

    def fname(self, b, o):
        i, n = struct.unpack_from('<ii', b, o)
        t = self.names[i] if 0 <= i < len(self.names) else '?%d' % i
        return (t if n == 0 else '%s_%d' % (t, n - 1)), o + 8

    @staticmethod
    def fstr(b, o):
        n = struct.unpack_from('<i', b, o)[0]
        o += 4
        if n == 0:
            return '', o
        if n < 0:
            return b[o:o - 2 * n].decode('utf-16-le').rstrip('\0'), o - 2 * n
        return b[o:o + n].decode('utf-8', 'replace').rstrip('\0'), o + n

    def text(self, b, o):
        o += 4
        h = struct.unpack_from('<b', b, o)[0]
        o += 1
        if h == -1:
            has = struct.unpack_from('<i', b, o)[0]
            o += 4
            return ({'text': self.fstr(b, o)[0]} if has else {'text': ''}), o
        if h == 0:
            ns, o = self.fstr(b, o)
            key, o = self.fstr(b, o)
            src, o = self.fstr(b, o)
            return {'ns': ns, 'key': key, 'text': src}, o
        if h == 11:
            table, o = self.fstr(b, o)
            key, o = self.fstr(b, o)
            return {'table': table, 'key': key}, o
        return {'history': h}, o

    def native(self, sub, b, o):
        if sub in ('SoftClassPath', 'SoftObjectPath'):
            p, o = self.fname(b, o)
            return p, self.fstr(b, o)[1]
        if sub == 'Guid':
            return b[o:o + 16].hex(), o + 16
        if sub == 'Color':
            return tuple(b[o:o + 4]), o + 4
        n = self.NATIVE[sub]
        fmt = '<' + ('i' if sub == 'IntPoint' else 'f') * (n // 4)
        return tuple(round(x, 4) for x in struct.unpack_from(fmt, b, o)), o + n

    def props(self, b, o):
        """A tagged property list up to 'None': ({name: value}, end offset)."""
        out = {}
        while True:
            name, o = self.fname(b, o)
            if name == 'None':
                return out, o
            kind, o = self.fname(b, o)
            size, idx = struct.unpack_from('<ii', b, o)
            o += 8
            sub, bval = None, None
            if kind == 'StructProperty':
                sub, o = self.fname(b, o)
                o += 16
            elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty', 'SetProperty'):
                sub, o = self.fname(b, o)
            elif kind == 'MapProperty':
                sub, o = self.fname(b, o)
                o += 8
            elif kind == 'BoolProperty':
                bval = bool(b[o])
                o += 1
            # HasPropertyGuid byte, then the optional guid
            if b[o]:
                o += 16
            o += 1
            val = bval if kind == 'BoolProperty' else self.value(kind, sub, b, o, size)
            out[name if idx == 0 else '%s[%d]' % (name, idx)] = val
            o += size

    def value(self, kind, sub, b, o, size):
        """One property payload."""
        if kind == 'FloatProperty':
            return round(struct.unpack_from('<f', b, o)[0], 5)
        if kind == 'IntProperty':
            return struct.unpack_from('<i', b, o)[0]
        if kind in ('NameProperty', 'EnumProperty'):
            return self.fname(b, o)[0]
        if kind == 'ByteProperty':
            return self.fname(b, o)[0] if size == 8 else b[o]
        if kind == 'StrProperty':
            return self.fstr(b, o)[0]
        if kind == 'TextProperty':
            return self.text(b, o)[0]
        if kind in ('SoftObjectProperty', 'SoftClassProperty'):
            p, o2 = self.fname(b, o)
            sp = self.fstr(b, o2)[0]
            return p + ('#' + sp if sp else '')
        if kind == 'ObjectProperty':
            return ('object', struct.unpack_from('<i', b, o)[0])
        if kind == 'StructProperty':
            return (self.native(sub, b, o)[0] if sub in self.NATIVE
                    else self.props(b, o)[0])
        if kind == 'ArrayProperty':
            return self.array(sub, b, o, size)
        return {'raw': bytes(b[o:o + size]).hex(), 'kind': kind}

    def array(self, sub, b, o, size):
        n = struct.unpack_from('<i', b, o)[0]
        o += 4
        items = []
        if sub == 'StructProperty':
            # inner tag: name, type, size+index, struct name, guid, guid flag
            o += 24
            ssub, o = self.fname(b, o)
            o += 17
            for _ in range(n):
                v, o = (self.native(ssub, b, o) if ssub in self.NATIVE
                        else self.props(b, o))
                items.append(v)
            return items
        for _ in range(n):
            if sub == 'FloatProperty':
                items.append(struct.unpack_from('<f', b, o)[0])
                o += 4
            elif sub == 'IntProperty':
                items.append(struct.unpack_from('<i', b, o)[0])
                o += 4
            elif sub in ('NameProperty', 'EnumProperty'):
                v, o = self.fname(b, o)
                items.append(v)
            elif sub in ('SoftObjectProperty', 'SoftClassProperty'):
                v, o = self.fname(b, o)
                o = self.fstr(b, o)[1]
                items.append(v)
            elif sub == 'ObjectProperty':
                items.append(('object', struct.unpack_from('<i', b, o)[0]))
                o += 4
            else:
                return {'raw': bytes(b[o - 4:o - 4 + size]).hex(), 'kind': 'Array' + sub}
        return items


def bucket_table(base=None):
    """The RandomModeBucketProtos table: shipped, or from a pak that replaces it."""
    if base is None:
        return missionproto.read(BUCKET_TABLE)
    from riftlord.formats.ue import pak
    p = pak.Pak(base)
    stem = '../../../OMD/Content' + BUCKET_TABLE[len('/Game'):]
    pkg = Package(p.read_file(stem + '.uasset'), p.read_file(stem + '.uexp'))
    pkg.load_payloads()
    return missionproto.Table(pkg)


def read_table(ua, ux):
    """{row: {field: value}} decoded from a built table."""
    pkg = Package(ua, ux)
    pkg.load_payloads()
    t = missionproto.Table(pkg)
    dec = PropDecoder(t.names)
    rows = {}
    for r in t.rows:
        vals = {}
        for f in r['fields']:
            b = bytes(f.payload)
            vals[f.name] = bool(f.bool_value) if f.kind == 'BoolProperty' else \
                dec.value(f.kind, f.sub, b, 0, len(b))
        rows[r['name']] = vals
    return rows


def _sub_fields(t, data):
    """[(Field, offset of its header)] of a tagged stream, and the offset of its None."""
    out, o = [], 0
    while True:
        f, used = t._read_field(data, o)
        if f is None:
            return out, o
        out.append((f, o))
        o += used
