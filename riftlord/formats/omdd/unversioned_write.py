"""Unversioned property writer for Orcs Must Die! Deathtrap (UE 5.3): the inverse of
trace_omdd.Props in raw mode (the format is described there).

The header is built as UE's FUnversionedHeaderBuilder builds it, walking every schema index
of the struct: a fragment holds at most 127 skipped and 127 serialized properties, a fragment
without zeroes drops its bits from the zero mask, and trailing skip-only fragments are dropped
except the first (so a struct with nothing serialized skips min(127, schema size)). The values
follow in schema order, zero-masked ones left out.
"""

import struct

from riftlord.formats.omdd import trace_omdd

#: Largest skip and value count of one header fragment (7 bits each).
SKIP_MAX = VALUE_MAX = 127


def header(entries, total):
    """The fragment stream and zero mask of [(schema index, zero-masked)] in index order, for
    a struct of `total` schema indices."""
    frags, zeros = [[0, 0, False]], []

    def close(f):
        if not f[2]:
            del zeros[len(zeros) - f[1]:]

    included = dict(entries)
    for i in range(max(total, max(included, default=-1) + 1)):
        f = frags[-1]
        if i in included:
            if f[1] == VALUE_MAX:
                close(f)
                f = [0, 0, False]
                frags.append(f)
            f[1] += 1
            f[2] |= bool(included[i])
            zeros.append(bool(included[i]))
        else:
            if f[1] or f[0] == SKIP_MAX:
                close(f)
                f = [0, 0, False]
                frags.append(f)
            f[0] += 1
    close(frags[-1])
    while len(frags) > 1 and not frags[-1][1]:
        frags.pop()
    words = [skip | (0x80 if hasz else 0) | num << 9 for skip, num, hasz in frags]
    words[-1] |= 0x100
    out = struct.pack('<%dH' % len(words), *words)
    if zeros:
        mask = sum(1 << k for k, z in enumerate(zeros) if z)
        n = len(zeros)
        out += mask.to_bytes(1 if n <= 8 else 2 if n <= 16 else 4 * ((n + 31) // 32), 'little')
    return out


class Writer:
    """Encodes raw-mode property trees. `name_ref(text)` gives (name map index, number) for
    a name that is a plain str (an edited value); FName values carry their own. With
    `stored_headers` False every header is built, never copied."""

    def __init__(self, types, name_ref=None, stored_headers=True):
        self.types, self.name_ref, self.stored_headers = types, name_ref, stored_headers

    def struct(self, name, fields):
        """One unversioned struct or class: header, then the non-zero values. The header
        is the one read when the entries' layout is unchanged, else built (`header`)."""
        schema = self.types.schema(name)
        if schema is None:
            raise trace_omdd.PropError('no schema for %r' % (name,))
        layout = fields.layout()
        if self.stored_headers and fields.header and getattr(fields, 'read_layout', None) == layout:
            out = [fields.header]
        else:
            out = [header(layout, self.types.count(name))]
        out += [self.value(schema[i][1], v) for i, z, v in fields.entries if not z]
        return b''.join(out)

    def name(self, v):
        if isinstance(v, trace_omdd.FName):
            return struct.pack('<II', v.index, v.number)
        return struct.pack('<II', *self.name_ref(v))

    @staticmethod
    def fstring(v):
        stored = v.length if isinstance(v, trace_omdd.FStr) else None
        if not v:
            if stored in (1, -1):
                return struct.pack('<i', stored) + b'\0' * (2 if stored < 0 else 1)
            return struct.pack('<i', 0)
        wide = stored < 0 if stored else any(ord(c) > 0x7F for c in v)
        if wide:
            b = v.encode('utf-16-le') + b'\0\0'
            return struct.pack('<i', -(len(b) // 2)) + b
        b = v.encode('latin-1') + b'\0'
        return struct.pack('<i', len(b)) + b

    def text(self, v):
        if not isinstance(v, trace_omdd.FText):
            v = trace_omdd.FText(v, 0, -1, (1, v))
        out = struct.pack('<Ib', v.flags, v.history)
        p = v.parts
        if v.history == -1:
            return out + struct.pack('<i', p[0]) + (self.fstring(p[1]) if p[0] else b'')
        if v.history == 0:
            return out + b''.join(self.fstring(s) for s in p)
        if v.history == 11:
            return out + self.name(p[0]) + self.fstring(p[1])
        raise trace_omdd.PropError('unsupported FText history %d' % v.history)

    def value(self, t, v):
        kind = t[0]
        fmt = trace_omdd.SIZES.get(kind)
        if fmt:
            return struct.pack(fmt, v)
        if kind == 'Enum':
            return self.value(t[2], v) if t[2][0] in trace_omdd.SIZES else struct.pack('<B', v)
        if kind in ('Object', 'Interface', 'WeakObject'):
            return struct.pack('<i', v.index)
        if kind == 'Name':
            return self.name(v)
        if kind in ('Str', 'Utf8Str', 'AnsiStr'):
            return self.fstring(v)
        if kind in ('SoftObject', 'AssetObject'):
            return self.name(v[0]) + self.name(v[1]) + self.fstring(v[2])
        if kind == 'LazyObject':
            return struct.pack('<4I', *v)
        if kind == 'Delegate':
            return struct.pack('<i', v[0].index) + self.name(v[1])
        if kind == 'MulticastDelegate':
            return struct.pack('<i', len(v)) + b''.join(
                struct.pack('<i', obj.index) + self.name(fn) for obj, fn in v)
        if kind == 'FieldPath':
            path, owner = v
            return (struct.pack('<i', len(path)) + b''.join(self.name(n) for n in path)
                    + struct.pack('<i', owner.index))
        if kind == 'Text':
            return self.text(v)
        if kind == 'Array':
            return struct.pack('<i', len(v)) + b''.join(self.value(t[1], x) for x in v)
        if kind in ('Set', 'Map'):
            removed = getattr(v, 'removed', [])
            out = [struct.pack('<i', len(removed))] + [self.value(t[1], x) for x in removed]
            out.append(struct.pack('<i', len(v)))
            if kind == 'Set':
                out += [self.value(t[1], x) for x in v]
            else:
                out += [self.value(t[1], k) + self.value(t[2], x) for k, x in v]
            return b''.join(out)
        if kind == 'OptionalProperty':
            return struct.pack('<i', 0) if v is None else struct.pack('<i', 1) + self.value(t[1], v)
        if kind == 'Struct':
            return self.struct_value(t[1], v)
        raise trace_omdd.PropError('unsupported property type %r' % (kind,))

    def struct_value(self, name, v):
        fmt = trace_omdd.NATIVE.get(name, '')
        if fmt:
            return struct.pack(fmt, *v)
        if name in ('SoftObjectPath', 'SoftClassPath'):
            return self.name(v[0]) + self.name(v[1]) + self.fstring(v[2])
        if name == 'GameplayTagContainer':
            return struct.pack('<i', len(v)) + b''.join(self.name(n) for n in v)
        if name in ('PerQualityLevelInt', 'PerQualityLevelFloat'):
            head, pairs = v
            return struct.pack('<2ii', *head, len(pairs)) + b''.join(
                struct.pack('<2i', *p) for p in pairs)
        if fmt is None:
            raise trace_omdd.PropError('unsupported native struct %r' % (name,))
        return self.struct(name, v)


# -- editing an export's properties ----------------------------------------------------------

def read_properties(game, pkg, k):
    """(raw Fields, class name, the export's bytes after its property block) of export `k`
    of a zenpackage.ZenPackage, read with `game` (trace_omdd.Game) resolving its class."""
    view = trace_omdd.Package(game, pkg.name, None, None, pkg.to_bytes())
    fields, end = view.props_end(k, raw=True)
    e = view.exports[k]
    return fields, view.class_name(k), view.data[end:e['at'] + e['size']]


def write_properties(game, pkg, k, fields, cls, rest):
    """Re-encode export `k`'s properties from `fields`, keeping `rest` after them."""
    pkg.exports[k].data = Writer(game.types, pkg.name_ref).struct(cls, fields) + rest


def set_field(types, fields, name, value, element=0, zero=False):
    """Set property `name` (element `element` of a static array) of raw Fields, adding it
    when it is not serialized yet; `zero` writes it zero-masked (all bits zero)."""
    schema = types.schema(fields.struct)
    index = sorted(i for i, (n, _t) in schema.items() if n == name)[element]
    entries = [x for x in fields.entries if x[0] != index] + [(index, zero, value)]
    fields.entries = sorted(entries, key=lambda x: x[0])
    fields[name] = value
