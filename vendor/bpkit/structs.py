"""Function and class exports: their properties, bytecode and class tables.

A UFunction or a Blueprint class does not begin with tagged properties alone.
After the UObject part (tagged properties, then bHasGuid) comes UStruct, then
UFunction or UClass, each field in a fixed order (Class.cpp, Field.cpp,
Property*.cpp in 4.27):

    UStruct    SuperStruct ref, Children (count + refs), ChildProperties
               (count + per property: type FName, then the FProperty), then
               the script: in-memory size, serialised size, bytecode
    UFunction  FunctionFlags uint32, an int16 RepOffset only with FUNC_Net,
               EventGraphFunction ref, EventGraphCallOffset int32;
               SparseDelegateFunction adds OwningClassName, DelegateName
    UUserDefinedStruct  StructFlags uint32 (UScriptStruct), then the struct's
               default values as a tagged stream
    UClass     FuncMap (count + FName/ref), ClassFlags uint32, ClassWithin
               ref, ClassConfigName FName, ClassGeneratedBy ref, Interfaces
               (count + class ref, PointerOffset int32, bImplementedByK2
               bool), bDeprecatedForceScriptOrder bool, a dummy FName, bCooked
               bool, ClassDefaultObject ref

A bool here is 4 bytes. An FProperty is FField (name FName, flags uint32; no
metadata when cooked) + ArrayDim, ElementSize, PropertyFlags uint64, RepIndex
uint16, RepNotifyFunc FName, BlueprintReplicationCondition uint8, then what
its type adds (PROPERTY_REFS, and the nested properties of containers).

Bytecode is walked with `kismet.Walker`, subclassed so that object references,
names and field paths are recorded as such and come back out as `Ref` and
`Name` fragments.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'UE Assets'))
import kismet                                                   # noqa: E402

from bpkit.build import Name, Ref                               # noqa: E402
from bpkit.props import DecodeError, _Out, _Reader, decode_props, encode_props  # noqa: E402

FUNC_NET = 0x00000040

#: Object references each property type adds after FProperty, in order.
PROPERTY_REFS = {
    'ObjectProperty': ('property_class',), 'WeakObjectProperty': ('property_class',),
    'LazyObjectProperty': ('property_class',), 'SoftObjectProperty': ('property_class',),
    'ClassProperty': ('property_class', 'meta_class'),
    'SoftClassProperty': ('property_class', 'meta_class'),
    'InterfaceProperty': ('interface_class',),
    'StructProperty': ('struct',),
    'ByteProperty': ('enum',), 'EnumProperty': ('enum',),
    'DelegateProperty': ('signature',), 'MulticastDelegateProperty': ('signature',),
    'MulticastInlineDelegateProperty': ('signature',),
    'MulticastSparseDelegateProperty': ('signature',),
}
#: Nested properties each container adds (SerializeSingleField: type FName, then it).
PROPERTY_CHILDREN = {'ArrayProperty': ('inner',), 'SetProperty': ('element',),
                     'MapProperty': ('key', 'value'), 'EnumProperty': ('underlying',)}
#: Types that add nothing after FProperty.
PLAIN = {'IntProperty', 'Int8Property', 'Int16Property', 'Int64Property', 'UInt16Property',
         'UInt32Property', 'UInt64Property', 'FloatProperty', 'DoubleProperty',
         'NameProperty', 'StrProperty', 'TextProperty'}


class Property:
    """One FProperty: its type, the common fields, and what its type adds."""

    __slots__ = ('type', 'name', 'flags', 'array_dim', 'element_size', 'property_flags',
                 'rep_index', 'rep_notify', 'rep_condition', 'refs', 'children', 'extra')

    def __init__(self, type_):
        self.type = type_
        self.refs = {}
        self.children = {}
        self.extra = None


# -- bytecode -------------------------------------------------------------

class SymWalker(kismet.Walker):
    """kismet.Walker recording references as ('obj', i), ('name', (i, n)) and,
    for a field path, ('pathlen', n) and ('owner', i)."""

    def _raw(self, fmt):
        v = struct.unpack_from(fmt, self.d, self.o)
        self.o += struct.calcsize(fmt)
        return v

    def ptr(self):
        if self._stack:
            self._stack[-1].objptrs += 1
        v = self._raw('<i')[0]
        self._rec('obj', v)
        return self.resolve(v)

    def fname(self):
        idx, num = self._raw('<II')
        self._rec('name', (idx, num))
        return self.names[idx] if idx < len(self.names) else '?name%d' % idx

    def field(self):
        n = self._raw('<i')[0]
        self._rec('pathlen', n)
        if self._stack:
            self._stack[-1].fieldpaths.append(max(n, 0))
        parts = [self.fname() for _ in range(max(n, 0))]
        self._rec('owner', self._raw('<i')[0])
        return '.'.join(parts) if parts else '(none)'


def _emit_code(nodes, names, ref_of, out):
    def write(node):
        out.raw(bytes([node.op]))
        for kind, val in node.ops:
            if kind == 'expr':
                write(val)
            elif kind == 'cstr':
                out.raw(val.encode('utf-8') + b'\0')
            elif kind in ('obj', 'owner'):
                out.ref(Ref(ref_of(val)))
            elif kind in ('name', 'pname'):        # pname: a name inside a field path
                out.name(Name(names[val[0]], val[1]))
            elif kind == 'pathlen':
                out.raw(struct.pack('<i', val))
            else:
                out.raw(struct.pack(kismet.PACK[kind], val))
    for n in nodes:
        write(n)


class Script:
    """A function's bytecode, as a walked node tree, and its in-memory size."""

    __slots__ = ('memory_size', 'nodes', 'names', 'ref_of')

    def __init__(self, memory_size, nodes, names, ref_of):
        self.memory_size, self.nodes, self.names, self.ref_of = memory_size, nodes, names, ref_of


# -- decoding -------------------------------------------------------------

def _bool(r):
    v = r.i32()
    if v not in (0, 1):
        raise DecodeError('a bool reads %d' % v)
    return v


def _property(r, type_name):
    p = Property(type_name)
    p.name = r.fname()
    p.flags = struct.unpack('<I', r.take(4))[0]
    p.array_dim, p.element_size = struct.unpack('<ii', r.take(8))
    p.property_flags = struct.unpack('<Q', r.take(8))[0]
    p.rep_index = struct.unpack('<H', r.take(2))[0]
    p.rep_notify = r.fname()
    p.rep_condition = r.take(1)[0]
    t = type_name.text
    if t == 'BoolProperty':
        # FieldSize, ByteOffset, ByteMask, FieldMask, BoolSize, NativeBool.
        p.extra = r.take(6)
    elif t == 'FieldPathProperty':
        p.extra = r.fname()                     # FFieldClass*: its name
    elif t not in PLAIN and t not in PROPERTY_REFS and t not in PROPERTY_CHILDREN:
        raise DecodeError('no rule for property type %s' % t)
    for key in PROPERTY_REFS.get(t, ()):
        p.refs[key] = r.ref()
    for key in PROPERTY_CHILDREN.get(t, ()):
        p.children[key] = _single_field(r)
    return p


def _single_field(r):
    type_name = r.fname()
    if type_name.text == 'None' and type_name.number == 0:
        return None
    return _property(r, type_name)


class StructExport:
    """A UFunction, user-defined struct or Blueprint class export, in serialisation order."""

    def __init__(self):
        self.kind = None          # 'function', 'struct' or 'class'
        self.props = []           # the UObject part's tagged properties
        self.guid = None          # bHasGuid's Guid, if any
        self.super = None
        self.children = []
        self.properties = []
        self.script = None
        self.fields = {}          # the UFunction or UClass part, by name


def decode_export(data, kind, names, ref_of, sparse_delegate=False):
    """Decode a struct export. `kind` is 'function', 'struct' or 'class'."""
    r = _Reader(data, names, ref_of)
    x = StructExport()
    x.kind = kind
    x.props = decode_props(r)
    if _bool(r):
        x.guid = r.take(16)
    x.super = r.ref()
    x.children = [r.ref() for _ in range(r.i32())]
    x.properties = [_property(r, r.fname()) for _ in range(r.i32())]
    memory, size = struct.unpack('<ii', r.take(8))
    code = r.take(size)
    w = SymWalker(code, names, lambda i: i).run()
    if w.o != size or (size and w.out[-1][2] != 'EndOfScript'):
        raise DecodeError('bytecode walked %d of %d bytes' % (w.o, size))
    x.script = Script(memory, w.nodes, names, ref_of)
    f = x.fields
    if kind == 'struct':
        f['struct_flags'] = struct.unpack('<I', r.take(4))[0]
        f['defaults'] = decode_props(r) if r.o < len(data) else None
    elif kind == 'function':
        f['function_flags'] = struct.unpack('<I', r.take(4))[0]
        if f['function_flags'] & FUNC_NET:
            f['rep_offset'] = r.take(2)
        f['event_graph_function'] = r.ref()
        f['event_graph_call_offset'] = r.i32()
        if sparse_delegate:
            f['owning_class_name'] = r.fname()
            f['delegate_name'] = r.fname()
    else:
        f['func_map'] = [(r.fname(), r.ref()) for _ in range(r.i32())]
        f['class_flags'] = struct.unpack('<I', r.take(4))[0]
        f['class_within'] = r.ref()
        f['class_config_name'] = r.fname()
        f['class_generated_by'] = r.ref()
        f['interfaces'] = [(r.ref(), r.i32(), _bool(r)) for _ in range(r.i32())]
        f['deprecated_force_script_order'] = _bool(r)
        f['dummy'] = r.fname()
        f['cooked'] = _bool(r)
        f['class_default_object'] = r.ref()
    if r.o != len(data):
        raise DecodeError('%d bytes left after the %s' % (len(data) - r.o, kind))
    return x


# -- encoding -------------------------------------------------------------

def _encode_property(p, out):
    out.name(p.name)
    out.raw(struct.pack('<Iii', p.flags, p.array_dim, p.element_size))
    out.raw(struct.pack('<QH', p.property_flags, p.rep_index))
    out.name(p.rep_notify)
    out.raw(bytes([p.rep_condition]))
    if isinstance(p.extra, Name):
        out.name(p.extra)
    elif p.extra is not None:
        out.raw(p.extra)
    for key in PROPERTY_REFS.get(p.type.text, ()):
        out.ref(p.refs[key])
    for key in PROPERTY_CHILDREN.get(p.type.text, ()):
        child = p.children[key]
        if child is None:
            out.name(Name('None'))
        else:
            out.name(child.type)
            _encode_property(child, out)


def encode_export(x):
    """Payload fragments for `bpkit.build`."""
    out = _Out()
    encode_props(x.props, out)
    out.raw(struct.pack('<i', 1) + x.guid if x.guid is not None else struct.pack('<i', 0))
    out.ref(x.super)
    out.raw(struct.pack('<i', len(x.children)))
    for c in x.children:
        out.ref(c)
    out.raw(struct.pack('<i', len(x.properties)))
    for p in x.properties:
        out.name(p.type)
        _encode_property(p, out)
    code = _Out()
    _emit_code(x.script.nodes, x.script.names, x.script.ref_of, code)
    out.raw(struct.pack('<ii', x.script.memory_size, code.size))
    out.extend(code)
    f = x.fields
    if x.kind == 'struct':
        out.raw(struct.pack('<I', f['struct_flags']))
        if f['defaults'] is not None:
            encode_props(f['defaults'], out)
    elif x.kind == 'function':
        out.raw(struct.pack('<I', f['function_flags']))
        if 'rep_offset' in f:
            out.raw(f['rep_offset'])
        out.ref(f['event_graph_function'])
        out.raw(struct.pack('<i', f['event_graph_call_offset']))
        if 'owning_class_name' in f:
            out.name(f['owning_class_name'])
            out.name(f['delegate_name'])
    else:
        out.raw(struct.pack('<i', len(f['func_map'])))
        for name, ref in f['func_map']:
            out.name(name)
            out.ref(ref)
        out.raw(struct.pack('<I', f['class_flags']))
        out.ref(f['class_within'])
        out.name(f['class_config_name'])
        out.ref(f['class_generated_by'])
        out.raw(struct.pack('<i', len(f['interfaces'])))
        for ref, offset, k2 in f['interfaces']:
            out.ref(ref)
            out.raw(struct.pack('<ii', offset, k2))
        out.raw(struct.pack('<i', f['deprecated_force_script_order']))
        out.name(f['dummy'])
        out.raw(struct.pack('<i', f['cooked']))
        out.ref(f['class_default_object'])
    return [bytes(f) if isinstance(f, bytearray) else f for f in out.frags]
