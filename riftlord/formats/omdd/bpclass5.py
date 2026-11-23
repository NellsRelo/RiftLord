"""Blueprint class, function and struct exports of Orcs Must Die! Deathtrap (UE 5.3), read
into a model and written back byte for byte.

Such an export is its unversioned properties (trace_omdd), then (measured on every shipped
export, docs/research/DEATHTRAP-MAPS.md "P2 Blueprint writer"):

    UObject    bHasGuid (int32), and a Guid when it is 1
    UStruct    SuperStruct (FPackageIndex), Children (count + FPackageIndex each),
               ChildProperties (count + per property: its type FName, then the FProperty),
               the script's in-memory size and serialized size (int32 each), the script
    UFunction  FunctionFlags u32, an int16 RepOffset only with FUNC_Net,
               EventGraphFunction (FPackageIndex), EventGraphCallOffset int32;
               a SparseDelegateFunction adds OwningClassName and DelegateName (FNames)
    UScriptStruct (ScriptStruct, UserDefinedStruct)  StructFlags u32, then the struct's
               default values (kept as bytes here)
    UClass     FuncMap (count + FName, FPackageIndex), ClassFlags u32, ClassWithin,
               ClassConfigName FName, ClassGeneratedBy, Interfaces (count + class,
               PointerOffset int32, bImplementedByK2 bool), bDeprecatedForceScriptOrder bool,
               a dummy FName, bCooked bool, ClassDefaultObject, then `tags`, a map of FName to
               FString (BlueprintType = BPTYPE_Normal on most; ImplementedInterfaces and
               BlueprintDisplayName on some); bools are 4 bytes. A Control Rig class keeps
               its VM's data after that (OWN_DATA, kept as bytes).

An FProperty: name FName, object flags u32, ArrayDim int32, ElementSize int32, PropertyFlags
u64, RepIndex u16, RepNotifyFunc FName, BlueprintReplicationCondition u8, then what its type
adds (`REFS`: object references in order; `CHILDREN`: nested properties, each written as its
type FName, or None, then itself; a BoolProperty's six layout bytes; a FieldPathProperty's
property class FName).

Names are kept as (name map index, number) and references as FPackageIndex values, as stored,
so a model writes back exactly; `ZenPackage.name_ref` and `add_import` make new ones.
"""

import struct

from riftlord.formats.omdd import kismet5

FUNC_NET = 0x40

#: Object references each property type adds after FProperty, in order.
REFS = {
    'ObjectProperty': ('property_class',), 'WeakObjectProperty': ('property_class',),
    'LazyObjectProperty': ('property_class',), 'SoftObjectProperty': ('property_class',),
    'ClassProperty': ('property_class', 'meta_class'),
    'SoftClassProperty': ('property_class', 'meta_class'),
    'InterfaceProperty': ('interface_class',), 'StructProperty': ('struct',),
    'ByteProperty': ('enum',), 'EnumProperty': ('enum',),
    'DelegateProperty': ('signature',), 'MulticastDelegateProperty': ('signature',),
    'MulticastInlineDelegateProperty': ('signature',),
    'MulticastSparseDelegateProperty': ('signature',),
}
#: Nested properties each container adds, in order.
CHILDREN = {'ArrayProperty': ('inner',), 'SetProperty': ('element',),
            'MapProperty': ('key', 'value'), 'EnumProperty': ('underlying',),
            'OptionalProperty': ('value',)}
#: Types that add nothing after FProperty.
PLAIN = {'IntProperty', 'Int8Property', 'Int16Property', 'Int64Property', 'UInt16Property',
         'UInt32Property', 'UInt64Property', 'FloatProperty', 'DoubleProperty',
         'NameProperty', 'StrProperty', 'TextProperty', 'Utf8StrProperty', 'AnsiStrProperty',
         'LargeWorldCoordinatesRealProperty'}
#: Export classes and the kind of struct export they are.
FUNCTION_CLASSES = {'Function': 'function', 'DelegateFunction': 'function',
                    'SparseDelegateFunction': 'sparse'}
STRUCT_CLASSES = {'UserDefinedStruct': 'struct', 'ScriptStruct': 'struct'}
#: Generated classes that write their own data after the UClass part (a Control Rig's VM),
#: kept as bytes.
OWN_DATA = ('RigVMBlueprintGeneratedClass', 'ControlRigBlueprintGeneratedClass')


def kind_of_class(cls):
    """'function', 'sparse', 'struct', 'class' or 'class+data' (a class with data of its own
    after the UClass part) for an export class name, else None."""
    if cls in FUNCTION_CLASSES:
        return FUNCTION_CLASSES[cls]
    if cls in STRUCT_CLASSES:
        return 'struct'
    if cls in OWN_DATA:
        return 'class+data'
    if cls and cls.endswith('GeneratedClass'):
        return 'class'
    return None


class FormatError(ValueError):
    pass


class Cursor:
    def __init__(self, data, o=0, names=None):
        self.d, self.o, self.names = data, o, names

    def take(self, fmt):
        try:
            v = struct.unpack_from(fmt, self.d, self.o)
        except struct.error:
            raise FormatError('ran past the export at %d' % self.o) from None
        self.o += struct.calcsize(fmt)
        return v[0] if len(v) == 1 else v

    def raw(self, n):
        if self.o + n > len(self.d):
            raise FormatError('ran past the export at %d' % self.o)
        self.o += n
        return self.d[self.o - n:self.o]

    def name(self):
        return self.take('<II')

    def bool32(self):
        v = self.take('<i')
        if v not in (0, 1):
            raise FormatError('a bool reads %d at %d' % (v, self.o - 4))
        return v

    def count(self, limit=1 << 16):
        n = self.take('<i')
        if not 0 <= n <= limit:
            raise FormatError('bad count %d at %d' % (n, self.o - 4))
        return n

    def fstring(self):
        """An FString as (text, stored length): negative lengths are UTF-16."""
        n = self.take('<i')
        if n < 0:
            raw = self.raw(-2 * n)
            return raw.decode('utf-16-le', 'surrogatepass')[:-1], n
        raw = self.raw(n)
        return raw.decode('latin-1')[:-1] if n else '', n

    def text(self, name):
        """The text of a stored (index, number) name."""
        index, number = name
        base = self.names[index & 0x3FFFFFFF]
        return base if not number else '%s_%d' % (base, number - 1)


class Property:
    """One FProperty. `kind` is its type's text ('ObjectProperty'), `type_name` the stored
    FName of it; `refs` maps REFS keys to FPackageIndex values; `children` maps CHILDREN keys
    to a Property or None; `extra` is a BoolProperty's layout bytes or a FieldPathProperty's
    class FName."""
    __slots__ = ('kind', 'type_name', 'name', 'flags', 'array_dim', 'element_size',
                 'property_flags', 'rep_index', 'rep_notify', 'rep_condition', 'refs',
                 'children', 'extra')

    def __init__(self, kind, type_name=None, **kw):
        self.kind, self.type_name = kind, type_name
        self.flags, self.array_dim, self.element_size = 0, 1, 0
        self.property_flags, self.rep_index, self.rep_condition = 0, 0, 0
        self.name = self.rep_notify = None
        self.refs, self.children, self.extra = {}, {}, None
        for k, v in kw.items():
            setattr(self, k, v)


def read_property(c, type_name):
    kind = c.text(type_name)
    p = Property(kind, type_name)
    p.name = c.name()
    p.flags, p.array_dim, p.element_size, p.property_flags, p.rep_index = c.take('<IiiQH')
    p.rep_notify = c.name()
    p.rep_condition = c.take('<B')
    if kind == 'BoolProperty':
        p.extra = c.raw(6)
    elif kind == 'FieldPathProperty':
        p.extra = c.name()
    elif kind not in PLAIN and kind not in REFS and kind not in CHILDREN:
        raise FormatError('no rule for property type %s' % kind)
    for key in REFS.get(kind, ()):
        p.refs[key] = c.take('<i')
    for key in CHILDREN.get(kind, ()):
        p.children[key] = read_single(c)
    return p


def read_single(c):
    """A nested property: its type FName (None for no property), then the property."""
    type_name = c.name()
    if c.text(type_name) == 'None':
        return Property('None', type_name)
    return read_property(c, type_name)


def write_fstring(v):
    """An FString from (text, stored length)."""
    text, n = v
    if n < 0:
        return struct.pack('<i', n) + text.encode('utf-16-le', 'surrogatepass') + b'\0\0'
    return struct.pack('<i', n) + (text.encode('latin-1') + b'\0' if n else b'')


def write_property(p):
    out = [struct.pack('<II', *p.name),
           struct.pack('<IiiQH', p.flags, p.array_dim, p.element_size, p.property_flags,
                       p.rep_index),
           struct.pack('<II', *p.rep_notify), struct.pack('<B', p.rep_condition)]
    if p.kind == 'BoolProperty':
        out.append(p.extra)
    elif p.kind == 'FieldPathProperty':
        out.append(struct.pack('<II', *p.extra))
    out += [struct.pack('<i', p.refs[k]) for k in REFS.get(p.kind, ())]
    for key in CHILDREN.get(p.kind, ()):
        child = p.children[key]
        out.append(struct.pack('<II', *child.type_name))
        if child.kind != 'None':
            out.append(write_property(child))
    return b''.join(out)


class StructExport:
    """The native part of a function, struct or class export (after its properties).
    `fields` holds the UFunction, UScriptStruct or UClass part by name."""

    def __init__(self, kind):
        self.kind = kind
        self.guid = None
        self.super = 0
        self.children = []
        self.properties = []
        self.script_memory = 0
        self.script = b''
        self.fields = {}

    def nodes(self):
        """The script walked into kismet5 nodes."""
        nodes, used = kismet5.read(self.script)
        if used != len(self.script):
            raise FormatError('the script walked %d of %d bytes' % (used, len(self.script)))
        return nodes

    def set_nodes(self, nodes):
        """Replace the script with nodes, updating both sizes."""
        self.script = kismet5.write(nodes)
        self.script_memory = kismet5.sizes(nodes)[1]


def read_struct(data, at, kind, names):
    """StructExport of the bytes of an export from offset `at` (just after its unversioned
    properties) to the end. `kind` as kind_of_class; `names` the package's name map."""
    c = Cursor(data, at, names)
    x = StructExport(kind)
    if c.bool32():
        x.guid = c.raw(16)
    x.super = c.take('<i')
    x.children = [c.take('<i') for _ in range(c.count())]
    x.properties = [read_property(c, c.name()) for _ in range(c.count())]
    x.script_memory, size = c.take('<ii')
    x.script = c.raw(size)
    f = x.fields
    if kind in ('function', 'sparse'):
        f['function_flags'] = c.take('<I')
        if f['function_flags'] & FUNC_NET:
            f['rep_offset'] = c.take('<h')
        f['event_graph_function'] = c.take('<i')
        f['event_graph_call_offset'] = c.take('<i')
        if kind == 'sparse':
            f['owning_class_name'] = c.name()
            f['delegate_name'] = c.name()
    elif kind == 'struct':
        f['struct_flags'] = c.take('<I')
        f['defaults'] = data[c.o:]
        c.o = len(data)
    elif kind in ('class', 'class+data'):
        f['func_map'] = [(c.name(), c.take('<i')) for _ in range(c.count())]
        f['class_flags'] = c.take('<I')
        f['class_within'] = c.take('<i')
        f['class_config_name'] = c.name()
        f['class_generated_by'] = c.take('<i')
        f['interfaces'] = [(c.take('<i'), c.take('<i'), c.bool32()) for _ in range(c.count())]
        f['deprecated_force_script_order'] = c.bool32()
        f['dummy'] = c.name()
        f['cooked'] = c.bool32()
        f['class_default_object'] = c.take('<i')
        f['tags'] = [(c.name(), c.fstring()) for _ in range(c.count())]
        if kind == 'class+data':
            f['data'] = data[c.o:]
            c.o = len(data)
    else:
        raise FormatError('unknown struct export kind %r' % (kind,))
    if c.o != len(data):
        raise FormatError('%d bytes left after the %s' % (len(data) - c.o, kind))
    return x


def write_struct(x):
    """The bytes of a StructExport (to follow the export's properties)."""
    out = [struct.pack('<i', 1) + x.guid if x.guid is not None else struct.pack('<i', 0),
           struct.pack('<ii', x.super, len(x.children)),
           struct.pack('<%di' % len(x.children), *x.children),
           struct.pack('<i', len(x.properties))]
    for p in x.properties:
        out += [struct.pack('<II', *p.type_name), write_property(p)]
    out += [struct.pack('<ii', x.script_memory, len(x.script)), x.script]
    f = x.fields
    if x.kind in ('function', 'sparse'):
        out.append(struct.pack('<I', f['function_flags']))
        if 'rep_offset' in f:
            out.append(struct.pack('<h', f['rep_offset']))
        out.append(struct.pack('<ii', f['event_graph_function'], f['event_graph_call_offset']))
        if x.kind == 'sparse':
            out.append(struct.pack('<IIII', *f['owning_class_name'], *f['delegate_name']))
    elif x.kind == 'struct':
        out += [struct.pack('<I', f['struct_flags']), f['defaults']]
    else:
        out.append(struct.pack('<i', len(f['func_map'])))
        out += [struct.pack('<IIi', *n, ref) for n, ref in f['func_map']]
        out.append(struct.pack('<Ii', f['class_flags'], f['class_within']))
        out.append(struct.pack('<IIi', *f['class_config_name'], f['class_generated_by']))
        out.append(struct.pack('<i', len(f['interfaces'])))
        out += [struct.pack('<iii', *i) for i in f['interfaces']]
        out.append(struct.pack('<iIIii', f['deprecated_force_script_order'], *f['dummy'],
                               f['cooked'], f['class_default_object']))
        out.append(struct.pack('<i', len(f['tags'])))
        out += [struct.pack('<II', *n) + write_fstring(v) for n, v in f['tags']]
        out.append(f.get('data', b''))
    return b''.join(out)


# -- Blueprint class schemas -----------------------------------------------------------------

#: Property types -> the usmap type tuple trace_omdd reads values by.
USMAP_KIND = {'IntProperty': 'Int', 'Int8Property': 'Int8', 'Int16Property': 'Int16',
              'Int64Property': 'Int64', 'UInt16Property': 'UInt16', 'UInt32Property': 'UInt32',
              'UInt64Property': 'UInt64', 'FloatProperty': 'Float', 'DoubleProperty': 'Double',
              'LargeWorldCoordinatesRealProperty': 'Double', 'BoolProperty': 'Bool',
              'NameProperty': 'Name', 'StrProperty': 'Str', 'TextProperty': 'Text',
              'Utf8StrProperty': 'Utf8Str', 'AnsiStrProperty': 'AnsiStr',
              'ObjectProperty': 'Object', 'ClassProperty': 'Object',
              'WeakObjectProperty': 'WeakObject', 'LazyObjectProperty': 'LazyObject',
              'SoftObjectProperty': 'SoftObject', 'SoftClassProperty': 'SoftObject',
              'InterfaceProperty': 'Interface', 'DelegateProperty': 'Delegate',
              'MulticastDelegateProperty': 'MulticastDelegate',
              'MulticastInlineDelegateProperty': 'MulticastDelegate',
              'MulticastSparseDelegateProperty': 'MulticastDelegate',
              'FieldPathProperty': 'FieldPath'}


class BlueprintTypes:
    """The usmap's schemas plus those of Blueprint classes and user structs, read from their
    exports, so that instances of Blueprint classes (a widget tree's user widgets, a class
    default object) read and write with trace_omdd and unversioned_write.

    A Blueprint class's schema counts its own properties first (ArrayDim indices each), then
    its super's, as a native class's does. Schemas are registered by object name, as the
    property reader names them (`W_ButtonBasic_C`)."""

    def __init__(self, game):
        self.game, self.usmap = game, game.types
        self.extra = {}                              # name -> (super name, count, [props])
        self._schemas = {}

    def schema(self, name):
        if name not in self.extra:
            return self.usmap.schema(name)
        if name not in self._schemas:
            sup, _count, props = self.extra[name]
            out, i = {}, 0
            for pname, dim, t in props:
                for _ in range(dim):
                    out[i] = (pname, t)
                    i += 1
            for k, v in (self.schema(sup) or {}).items() if sup else ():
                out[i + k] = v
            self._schemas[name] = out
        return self._schemas[name]

    def count(self, name):
        if name not in self.extra:
            return self.usmap.count(name)
        sup, count, _props = self.extra[name]
        return count + (self.count(sup) if sup else 0)

    def derives(self, name, base):
        seen = set()
        while name and name not in seen:
            if name == base:
                return True
            seen.add(name)
            name = self.extra[name][0] if name in self.extra else \
                self.usmap.structs.get(name, (None,))[0]
        return False

    # -- registering

    def _target(self, pkg, ref):
        """(package, export index) or ('script', name) of an FPackageIndex, or None."""
        if ref > 0:
            return pkg, ref - 1
        if ref < 0:
            return pkg.import_target(-ref - 1)
        return None

    def _name_of(self, pkg, ref, register=True):
        """The name of a referenced class, struct or enum; a Blueprint class or user struct
        is registered (`register`)."""
        got = self._target(pkg, ref)
        if not got:
            return None
        if got[0] == 'script':
            return got[1]
        other, j = got
        if register:
            self.register(other, j)
        return other.exports[j]['name']

    def register(self, pkg, k):
        """Add the schema of export `k` of a trace_omdd.Package (a Blueprint class or a user
        struct), its Blueprint supers and the user structs its properties hold. Returns its
        name. Native classes and structs are the usmap's and are not registered."""
        e = pkg.exports[k]
        name = e['name']
        if name in self.extra:
            return name
        kind = kind_of_class(pkg.class_name(k))
        if kind not in ('class', 'class+data', 'struct'):
            raise FormatError('%s is not a Blueprint class or struct' % name)
        _fields, end = pkg.props_end(k, raw=True)
        x = read_struct(pkg.blob(k), end - e['at'], kind, pkg.names)
        self.extra[name] = (None, 0, [])                  # placeholder against cycles
        sup = self._name_of(pkg, x.super) if x.super else None
        c = Cursor(b'', 0, pkg.names)
        props = [(c.text(p.name), p.array_dim, self.type_of(pkg, p)) for p in x.properties]
        self.extra[name] = (sup, sum(p.array_dim for p in x.properties), props)
        self._schemas.pop(name, None)
        return name

    def type_of(self, pkg, p):
        """The usmap type tuple of a Property read in `pkg`."""
        if p.kind in ('ByteProperty',):
            enum = self._name_of(pkg, p.refs['enum'], False) if p.refs['enum'] else None
            return ('Enum', enum, ('Byte',)) if enum else ('Byte',)
        if p.kind == 'EnumProperty':
            return ('Enum', self._name_of(pkg, p.refs['enum'], False),
                    self.type_of(pkg, p.children['underlying']))
        if p.kind == 'StructProperty':
            return ('Struct', self._name_of(pkg, p.refs['struct']))
        if p.kind in ('ArrayProperty', 'SetProperty'):
            inner = p.children['inner' if p.kind == 'ArrayProperty' else 'element']
            return ('Array' if p.kind == 'ArrayProperty' else 'Set', self.type_of(pkg, inner))
        if p.kind == 'MapProperty':
            return ('Map', self.type_of(pkg, p.children['key']),
                    self.type_of(pkg, p.children['value']))
        if p.kind == 'OptionalProperty':
            return ('OptionalProperty', self.type_of(pkg, p.children['value']))
        if p.kind in USMAP_KIND:
            return (USMAP_KIND[p.kind],)
        raise FormatError('no usmap type for %s' % p.kind)

    def register_class_of(self, pkg, k):
        """Register the class of export `k` when it is a Blueprint class; its name."""
        ref = pkg.exports[k]['class']
        got = pkg.object_index(ref)
        if got and got[0] != 'script':
            other, j = got
            return self.register(other, j)
        return got[1] if got else None
