"""Read and write cooked (unversioned-free, tagged) UE 4.27 property streams as values.

`read(data, names, o)` -> (list of Prop, end offset). A Prop keeps its raw bytes so an
unchanged stream rebuilds byte-identical; `write(props, pkg)` re-emits a list of Props
(with sizes recomputed), so values can be edited or new props built with `prop()`.

Only what static meshes and body setups use is decoded; anything else stays raw.
"""

import struct

#: Struct types the engine serialises natively (no tags inside), with their byte size.
NATIVE_STRUCTS = {'Vector': 12, 'Rotator': 12, 'Quat': 16, 'Box': 25, 'Vector2D': 8,
                  'Guid': 16, 'Color': 4, 'LinearColor': 16, 'IntPoint': 8, 'Vector4': 16,
                  'IntVector': 12, 'PerPlatformFloat': 8,
                  'PerPlatformInt': 8, 'PerPlatformBool': 8}


def _fname(data, o, names):
    i, n = struct.unpack_from('<2i', data, o)
    return names[i] if n == 0 else '%s_%d' % (names[i], n - 1)


class Prop:
    """One tagged property. `value` is decoded where understood, else raw bytes."""

    def __init__(self, name, kind, value, struct_name=None, inner=None, enum=None,
                 array_index=0, bool_value=None, guid=None):
        self.name, self.kind, self.value = name, kind, value
        self.struct_name, self.inner, self.enum = struct_name, inner, enum
        self.array_index, self.bool_value, self.guid = array_index, bool_value, guid

    def __repr__(self):
        return 'Prop(%s %s %r)' % (self.name, self.kind, self.value)


def read(data, names, o=0):
    out = []
    while True:
        name = _fname(data, o, names)
        if name == 'None':
            return out, o + 8
        kind = _fname(data, o + 8, names)
        size, aidx = struct.unpack_from('<ii', data, o + 16)
        p = o + 24
        sname = inner = enum = bval = None
        if kind == 'StructProperty':
            sname = _fname(data, p, names)
            p += 8 + 16
        elif kind == 'ArrayProperty' or kind == 'SetProperty':
            inner = _fname(data, p, names)
            p += 8
        elif kind in ('ByteProperty', 'EnumProperty'):
            enum = _fname(data, p, names)
            p += 8
        elif kind == 'MapProperty':
            inner = (_fname(data, p, names), _fname(data, p + 8, names))
            p += 16
        elif kind == 'BoolProperty':
            bval = data[p]
            p += 1
        guid = None
        if data[p]:
            guid = data[p + 1:p + 17]
            p += 16
        p += 1
        raw = data[p:p + size]
        value = _decode(kind, raw, names, sname, inner, enum)
        out.append(Prop(name, kind, value, sname, inner, enum, aidx, bval, guid))
        o = p + size


def _decode(kind, raw, names, sname, inner, enum):
    if kind == 'FloatProperty':
        return struct.unpack('<f', raw)[0]
    if kind == 'IntProperty':
        return struct.unpack('<i', raw)[0]
    if kind == 'ObjectProperty':
        return struct.unpack('<i', raw)[0]
    if kind == 'NameProperty':
        return _fname(raw, 0, names)
    if kind in ('ByteProperty', 'EnumProperty'):
        if len(raw) == 8:
            return _fname(raw, 0, names)
        return raw[0] if len(raw) == 1 else raw
    if kind == 'BoolProperty':
        return None
    if kind == 'StructProperty':
        if sname in NATIVE_STRUCTS:
            return raw
        got, end = read(raw, names, 0)
        return got
    if kind == 'ArrayProperty':
        n = struct.unpack_from('<i', raw, 0)[0]
        if inner == 'StructProperty':
            # inner tag: name, type, size, index, struct name, guid, has-guid
            sname2 = _fname(raw, 4 + 24, names)
            body = raw[4 + 24 + 8 + 16 + 1:]
            if sname2 in NATIVE_STRUCTS:
                k = NATIVE_STRUCTS[sname2]
                return ('structs', sname2, [body[i * k:(i + 1) * k] for i in range(n)],
                        raw[4:4 + 24 + 8 + 16 + 1])
            elems, o = [], 0
            for _ in range(n):
                got, o = read(body, names, o)
                elems.append(got)
            return ('structs', sname2, elems, raw[4:4 + 24 + 8 + 16 + 1])
        if inner == 'IntProperty':
            return list(struct.unpack_from('<%di' % n, raw, 4))
        if inner == 'FloatProperty':
            return list(struct.unpack_from('<%df' % n, raw, 4))
        return raw
    return raw


def get(props, name):
    for p in props:
        if p.name == name:
            return p
    return None


# -- writing -----------------------------------------------------------------------

def prop(name, kind, value, **kw):
    return Prop(name, kind, value, **kw)


def _encode(p, pkg):
    k = p.kind
    v = p.value
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if k == 'FloatProperty':
        return struct.pack('<f', v)
    if k in ('IntProperty', 'ObjectProperty'):
        return struct.pack('<i', v)
    if k == 'NameProperty' or (k in ('ByteProperty', 'EnumProperty') and isinstance(v, str)):
        return _name(pkg, v)
    if k == 'ByteProperty' and isinstance(v, int):
        return bytes([v])
    if k == 'BoolProperty':
        return b''
    if k == 'StructProperty':
        return write(v, pkg)
    if k == 'ArrayProperty':
        if isinstance(v, tuple) and v[0] == 'structs':
            _tag, sname, elems, inner_tag = v
            body = b''.join(e if isinstance(e, (bytes, bytearray)) else write(e, pkg)
                            for e in elems)
            if inner_tag is None:
                inner_tag = (struct.pack('<ii', pkg.add_name(p.name), 0)
                             + struct.pack('<ii', pkg.add_name('StructProperty'), 0)
                             + struct.pack('<ii', 0, 0) + _name(pkg, sname) + b'\0' * 17)
            tag = bytearray(inner_tag)
            struct.pack_into('<i', tag, 16, len(body))
            return struct.pack('<i', len(elems)) + bytes(tag) + body
        if p.inner == 'IntProperty':
            return struct.pack('<i%di' % len(v), len(v), *v)
        if p.inner == 'FloatProperty':
            return struct.pack('<i%df' % len(v), len(v), *v)
    raise ValueError('cannot encode %r' % p)


def _name(pkg, text):
    """An FName: an existing name as is, else `Base_N` as (Base, N+1) when Base exists."""
    have = {n for n, _h in pkg.names}
    if text not in have and '_' in text:
        base, _sep, num = text.rpartition('_')
        if num.isdigit() and base in have and not (len(num) > 1 and num[0] == '0'):
            return struct.pack('<ii', pkg.add_name(base), int(num) + 1)
    return struct.pack('<ii', pkg.add_name(text), 0)


def write(props, pkg):
    """Bytes of a tagged property stream (with the closing None)."""
    out = bytearray()
    for p in props:
        body = _encode(p, pkg)
        out += _name(pkg, p.name) + _name(pkg, p.kind)
        out += struct.pack('<ii', len(body), p.array_index)
        if p.kind == 'StructProperty':
            out += _name(pkg, p.struct_name) + b'\0' * 16
        elif p.kind in ('ArrayProperty', 'SetProperty'):
            out += _name(pkg, p.inner)
        elif p.kind in ('ByteProperty', 'EnumProperty'):
            out += _name(pkg, p.enum or 'None')
        elif p.kind == 'MapProperty':
            out += _name(pkg, p.inner[0]) + _name(pkg, p.inner[1])
        elif p.kind == 'BoolProperty':
            out += bytes([1 if p.bool_value else 0])
        if p.guid:
            out += b'\1' + p.guid
        else:
            out += b'\0'
        out += body
    out += _name(pkg, 'None')
    return bytes(out)
