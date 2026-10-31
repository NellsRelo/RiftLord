"""Tagged property streams, decoded to values and encoded back as payload fragments.

A cooked UObject's payload begins with its properties: a run of FPropertyTag +
value, ended by the name None (PropertyTag.cpp, UStruct::SerializeTaggedProperties).
This module decodes such a stream into `Prop` values in which every FName is a
`Name` and every object reference a `Ref` -- the tag's own name, type, struct,
enum and inner-type names included -- and encodes values back into the
fragment lists `bpkit.build` resolves. Every size field is recomputed on the
way out: the tag's Size, and the inner tag of a struct array.

Nothing here knows a class's schema. A struct's layout is decided per value:
a struct the engine serialises natively (NATIVE) is fixed bytes, a soft path
is an FName and an FString, and anything else must parse as a tagged stream
that consumes exactly the tag's Size, or decoding fails. Failing is the point:
a stream we cannot account for byte by byte is one we would silently corrupt.

    props, tail = decode_object(data, names, ref_of)
    fragments = encode_object(props, tail)
"""

import struct

from bpkit.build import Name, Ref

#: Structs serialised natively (WithSerializer / immutable) and their sizes.
#: None of them hold an FName or an object reference.
NATIVE = {'Vector': 12, 'Vector2D': 8, 'Vector4': 16, 'Rotator': 12, 'Quat': 16,
          'LinearColor': 16, 'Color': 4, 'Guid': 16, 'IntPoint': 8, 'IntVector': 12,
          'Box': 25, 'Box2D': 17, 'Plane': 16, 'Matrix': 64, 'DateTime': 8,
          'Timespan': 8, 'FrameNumber': 4,
          # InterpMode, TangentMode, TangentWeightMode, then six floats (RichCurve.cpp).
          'RichCurveKey': 27,
          # TRange<FFrameNumber>: two TRangeBound, each a uint8 type + int32 (RangeBound.h).
          'MovieSceneFrameRange': 10,
          # A uint32 (MovieSceneSequenceID.h).
          'MovieSceneSequenceID': 4}

#: Structs with a custom serialiser of variable length, kept as the tag's bytes.
#: Each is listed only after reading its serialiser and finding no FName and no
#: object reference in it -- an unlisted one fails instead, because bytes kept
#: whole would hide any name index inside them from the permutation check.
OPAQUE = {
    # TMovieSceneEvaluationTree<FEntityAndMetaDataIndex>: frame ranges and two
    # int32 per entry (MovieSceneEvaluationField.h).
    'MovieSceneEvaluationFieldEntityTree',
}

#: Structs that serialise as an FName (asset path) then an FString (sub-path).
SOFT_PATHS = {'SoftObjectPath', 'SoftClassPath', 'StringAssetReference',
              'StringClassReference'}

#: Property types whose value is a fixed number of plain bytes.
FIXED = {'IntProperty': 4, 'FloatProperty': 4, 'DoubleProperty': 8, 'Int8Property': 1,
         'Int16Property': 2, 'Int64Property': 8, 'UInt16Property': 2,
         'UInt32Property': 4, 'UInt64Property': 8}


# -- values ---------------------------------------------------------------

class Value:
    """Structural equality, so a decode can be compared with another decode."""

    __slots__ = ()

    def __eq__(self, other):
        return type(self) is type(other) and all(
            getattr(self, k) == getattr(other, k) for k in self.__slots__)

    def __repr__(self):
        return '%s(%s)' % (type(self).__name__,
                           ', '.join('%s=%r' % (k, getattr(self, k)) for k in self.__slots__))


class Prop(Value):
    """One FPropertyTag and its value.

    `extra` holds the tag fields that only some types carry, by the names
    PropertyTag.cpp uses: struct_name and struct_guid, bool_val, enum_name,
    inner_type, value_type. `guid` is the optional property Guid.
    """

    __slots__ = ('name', 'type', 'index', 'extra', 'guid', 'value')

    def __init__(self, name, type, index=0, extra=None, guid=None, value=None):
        self.name, self.type, self.index = name, type, index
        self.extra, self.guid, self.value = extra or {}, guid, value


class Struct(Value):
    """A struct value. `kind` is 'tagged' (a list of Prop), 'native' (bytes) or 'soft'."""

    __slots__ = ('kind', 'value')

    def __init__(self, kind, value):
        self.kind, self.value = kind, value


class SoftPath(Value):
    __slots__ = ('asset', 'sub')

    def __init__(self, asset, sub):
        self.asset, self.sub = asset, sub


class Text(Value):
    """FText: flags, history type, and that history's fields in order."""

    __slots__ = ('flags', 'history', 'fields')

    def __init__(self, flags, history, fields):
        self.flags, self.history, self.fields = flags, history, fields


class Array(Value):
    """An array. A struct array keeps its inner tag: name, struct name, Guids."""

    __slots__ = ('items', 'inner_tag')

    def __init__(self, items, inner_tag=None):
        self.items, self.inner_tag = items, inner_tag


class MapValue(Value):
    __slots__ = ('removed', 'entries')

    def __init__(self, removed, entries):
        self.removed, self.entries = removed, entries


class FieldPath(Value):
    """FFieldPath: the path as names, then the owning struct (FieldPath.cpp)."""

    __slots__ = ('path', 'owner')

    def __init__(self, path, owner):
        self.path, self.owner = path, owner


class Delegate(Value):
    __slots__ = ('object', 'function')

    def __init__(self, obj, function):
        self.object, self.function = obj, function


class Raw(Value):
    """Bytes kept as bytes: a fixed-width number, or an FString as stored."""

    __slots__ = ('data',)

    def __init__(self, data):
        self.data = data


class DecodeError(ValueError):
    pass


# -- decoding -------------------------------------------------------------

class _Reader:
    def __init__(self, data, names, ref_of, end=None):
        self.d, self.o = data, 0
        self.end = len(data) if end is None else end
        self.names, self.ref_of = names, ref_of

    def take(self, n):
        if self.o + n > self.end:
            raise DecodeError('read past the end at %d' % self.o)
        v = self.d[self.o:self.o + n]
        self.o += n
        return v

    def i32(self):
        return struct.unpack('<i', self.take(4))[0]

    def fname(self):
        index, number = struct.unpack('<ii', self.take(8))
        if not 0 <= index < len(self.names):
            raise DecodeError('name index %d out of range at %d' % (index, self.o - 8))
        return Name(self.names[index], number)

    def ref(self):
        return Ref(self.ref_of(self.i32()))

    def fstring(self):
        """An FString, kept as its stored bytes so ANSI/UTF-16 survive exactly."""
        start = self.o
        n = self.i32()
        self.take(-n * 2 if n < 0 else n)
        return Raw(self.d[start:self.o])


def decode_props(r):
    """Read tags until None. Returns [Prop]."""
    out = []
    while True:
        name = r.fname()
        if name.text == 'None' and name.number == 0:
            return out
        type_ = r.fname()
        size, index = struct.unpack('<ii', r.take(8))
        extra = {}
        t = type_.text if type_.number == 0 else None
        if t == 'StructProperty':
            extra['struct_name'] = r.fname()
            extra['struct_guid'] = r.take(16)
        elif t == 'BoolProperty':
            extra['bool_val'] = r.take(1)[0]
        elif t in ('ByteProperty', 'EnumProperty'):
            extra['enum_name'] = r.fname()
        elif t in ('ArrayProperty', 'SetProperty'):
            extra['inner_type'] = r.fname()
        elif t == 'MapProperty':
            extra['inner_type'] = r.fname()
            extra['value_type'] = r.fname()
        guid = r.take(16) if r.take(1)[0] else None
        start = r.o
        if start + size > r.end:
            raise DecodeError('%s runs past its container' % name.text)
        sub = _Reader(r.d, r.names, r.ref_of, start + size)
        sub.o = start
        try:
            value = _value(sub, t, size, extra, name.text)
        except (DecodeError, struct.error) as ex:
            # Name the path down to the failure: "Prop<Struct> > Inner: why".
            where = name.text
            if 'struct_name' in extra:
                where += '<%s>' % extra['struct_name'].text
            elif 'inner_type' in extra:
                where += '[%s]' % extra['inner_type'].text
            raise DecodeError('%s > %s' % (where, ex)) from None
        if sub.o != start + size:
            raise DecodeError('%s (%s) read %d of %d bytes'
                              % (name.text, t, sub.o - start, size))
        r.o = start + size
        out.append(Prop(name, type_, index, extra, guid, value))


def _value(r, t, size, extra, label):
    if t == 'BoolProperty':
        return None                                  # the value is in the tag
    if t in FIXED:
        return Raw(r.take(FIXED[t]))
    if t == 'ByteProperty':
        return Raw(r.take(1)) if size == 1 else r.fname()
    if t in ('EnumProperty', 'NameProperty'):
        return r.fname()
    if t == 'StrProperty':
        return r.fstring()
    if t == 'TextProperty':
        return _text(r)
    if t in ('ObjectProperty', 'ClassProperty', 'InterfaceProperty', 'WeakObjectProperty',
             'LazyObjectProperty'):
        return r.ref()
    if t in ('SoftObjectProperty', 'SoftClassProperty'):
        return SoftPath(r.fname(), r.fstring())
    if t == 'DelegateProperty':
        return Delegate(r.ref(), r.fname())
    if t == 'FieldPathProperty':
        return FieldPath([r.fname() for _ in range(r.i32())], r.ref())
    if t in ('MulticastDelegateProperty', 'MulticastInlineDelegateProperty',
             'MulticastSparseDelegateProperty'):
        return Array([Delegate(r.ref(), r.fname()) for _ in range(r.i32())])
    if t == 'StructProperty':
        return _struct(r, extra['struct_name'].text, size)
    if t == 'ArrayProperty':
        return _array(r, extra['inner_type'].text, size, label)
    if t == 'SetProperty':
        removed = [_element(r, extra['inner_type'].text, None) for _ in range(r.i32())]
        items = [_element(r, extra['inner_type'].text, None) for _ in range(r.i32())]
        return MapValue(removed, items)
    if t == 'MapProperty':
        kt, vt = extra['inner_type'].text, extra['value_type'].text
        removed = [_element(r, kt, None) for _ in range(r.i32())]
        entries = [(_element(r, kt, None), _element(r, vt, None)) for _ in range(r.i32())]
        return MapValue(removed, entries)
    raise DecodeError('no rule for %s %s' % (t, label))


def _struct(r, struct_name, size):
    if struct_name in SOFT_PATHS:
        return Struct('soft', SoftPath(r.fname(), r.fstring()))
    if struct_name in NATIVE:
        return Struct('native', r.take(NATIVE[struct_name]))
    if struct_name == 'MovieSceneFloatChannel':
        return Struct('native', _float_channel(r))
    if struct_name in OPAQUE:
        if size is None:
            raise DecodeError('%s inside an array has no size to keep' % struct_name)
        return Struct('native', r.take(size))
    return Struct('tagged', decode_props(r))


def _float_channel(r):
    """FMovieSceneFloatChannel::Serialize: numbers only, read field by field.

    Pre/post infinity extrapolation (uint8 each), the key times and the key
    values each as element size + count + raw elements, DefaultValue (float),
    bHasDefaultValue (bool, 4 bytes), TickResolution (two int32).
    """
    start = r.o
    r.take(2)
    for _ in range(2):
        width, count = struct.unpack('<ii', r.take(8))
        r.take(width * count)
    r.take(4 + 4 + 8)
    return r.d[start:r.o]


def _text(r):
    flags = r.take(4)
    history = struct.unpack('<b', r.take(1))[0]
    if history == -1:
        has = r.take(4)
        fields = [Raw(has)] + ([r.fstring()] if struct.unpack('<i', has)[0] else [])
    elif history == 0:
        fields = [r.fstring(), r.fstring(), r.fstring()]
    elif history == 11:
        fields = [r.fname(), r.fstring()]
    else:
        raise DecodeError('FText history type %d has no rule' % history)
    return Text(Raw(flags), history, fields)


def _element(r, t, width):
    """One array element of type `t`; `width` is the per-element size when known."""
    if t == 'BoolProperty':
        return Raw(r.take(1))
    if t == 'ByteProperty':
        return Raw(r.take(1)) if width in (None, 1) else r.fname()
    if t == 'StructProperty':
        # A set or map element: its tag names no struct, so it can only be read
        # as a tagged stream; a native struct here fails the decode.
        return Struct('tagged', decode_props(r))
    return _value(r, t, width, {}, 'element')


def _array(r, inner, size, label):
    count = r.i32()
    if inner != 'StructProperty':
        width = (size - 4) // count if count else None
        return Array([_element(r, inner, width) for _ in range(count)])
    tag = {'name': r.fname(), 'type': r.fname()}
    tag['size'] = r.i32()
    tag['index'] = r.i32()
    tag['struct_name'] = r.fname()
    tag['struct_guid'] = r.take(16)
    tag['guid'] = r.take(16) if r.take(1)[0] else None
    start = r.o
    sname = tag['struct_name'].text
    items = [_struct(r, sname, None) for _ in range(count)]
    if r.o - start != tag['size']:
        raise DecodeError('%s: struct array elements took %d, inner tag says %d'
                          % (label, r.o - start, tag['size']))
    del tag['size']
    return Array(items, tag)


def decode_object(data, names, ref_of):
    """(props, tail): a payload's tagged properties and whatever follows them."""
    r = _Reader(data, names, ref_of)
    props = decode_props(r)
    return props, data[r.o:]


# -- encoding -------------------------------------------------------------

class _Out:
    """Fragments plus their resolved length (a Name is 8 bytes, a Ref 4)."""

    def __init__(self):
        self.frags = []
        self.size = 0

    def raw(self, b):
        if b:
            if self.frags and isinstance(self.frags[-1], bytearray):
                self.frags[-1] += b
            else:
                self.frags.append(bytearray(b))
            self.size += len(b)

    def name(self, n):
        self.frags.append(n)
        self.size += 8

    def ref(self, r):
        self.frags.append(r)
        self.size += 4

    def extend(self, other):
        for f in other.frags:
            if isinstance(f, bytearray):
                self.raw(f)
            elif isinstance(f, Name):
                self.name(f)
            else:
                self.ref(f)


def encode_props(props, out):
    for p in props:
        body = _Out()
        _encode_value(p.value, p.type.text, p.extra, body)
        out.name(p.name)
        out.name(p.type)
        out.raw(struct.pack('<ii', body.size, p.index))
        x = p.extra
        if 'struct_name' in x:
            out.name(x['struct_name'])
            out.raw(x['struct_guid'])
        if 'bool_val' in x:
            out.raw(bytes([x['bool_val']]))
        if 'enum_name' in x:
            out.name(x['enum_name'])
        if 'inner_type' in x:
            out.name(x['inner_type'])
        if 'value_type' in x:
            out.name(x['value_type'])
        out.raw(b'\x01' + p.guid if p.guid is not None else b'\x00')
        out.extend(body)
    out.name(Name('None'))


def _encode_value(v, t, extra, out):
    if v is None:
        return
    if isinstance(v, Raw):
        out.raw(v.data)
    elif isinstance(v, Name):
        out.name(v)
    elif isinstance(v, Ref):
        out.ref(v)
    elif isinstance(v, SoftPath):
        out.name(v.asset)
        out.raw(v.sub.data)
    elif isinstance(v, Delegate):
        out.ref(v.object)
        out.name(v.function)
    elif isinstance(v, FieldPath):
        out.raw(struct.pack('<i', len(v.path)))
        for n in v.path:
            out.name(n)
        out.ref(v.owner)
    elif isinstance(v, Text):
        out.raw(v.flags.data)
        out.raw(struct.pack('<b', v.history))
        for f in v.fields:
            _encode_value(f, None, None, out)
    elif isinstance(v, Struct):
        if v.kind == 'tagged':
            encode_props(v.value, out)
        elif v.kind == 'native':
            out.raw(v.value)
        else:
            _encode_value(v.value, None, None, out)
    elif isinstance(v, MapValue):
        out.raw(struct.pack('<i', len(v.removed)))
        for item in v.removed:
            _encode_value(item, None, None, out)
        out.raw(struct.pack('<i', len(v.entries)))
        for entry in v.entries:
            for item in (entry if isinstance(entry, tuple) else (entry,)):
                _encode_value(item, None, None, out)
    elif isinstance(v, Array):
        out.raw(struct.pack('<i', len(v.items)))
        if v.inner_tag is None:
            for item in v.items:
                _encode_value(item, None, None, out)
            return
        elements = _Out()
        for item in v.items:
            _encode_value(item, None, None, elements)
        tag = v.inner_tag
        out.name(tag['name'])
        out.name(tag['type'])
        out.raw(struct.pack('<ii', elements.size, tag['index']))
        out.name(tag['struct_name'])
        out.raw(tag['struct_guid'])
        out.raw(b'\x01' + tag['guid'] if tag['guid'] is not None else b'\x00')
        out.extend(elements)
    else:
        raise TypeError('cannot encode %r' % (v,))


def encode_object(props, tail=b''):
    """Payload fragments for `bpkit.build`: the properties, None, then `tail`."""
    out = _Out()
    encode_props(props, out)
    out.raw(tail)
    return [bytes(f) if isinstance(f, bytearray) else f for f in out.frags]
