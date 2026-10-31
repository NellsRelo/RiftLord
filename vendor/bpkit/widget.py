"""Widget Blueprints: a plain description of a widget tree, compiled to a cooked package.

The description is what an author writes -- no tag types, struct names, table
positions or flags. Everything else is derived: property tags from the type
database, the generated class and its CDO, the WidgetTree, the slot objects,
the imports, the export order and the preload dependencies.

    name: LargeMap_UMG
    path: /Game/UI/PlayerHUD/LargeMap_UMG
    parent: /Script/UMG.UserWidget
    root:
      name: Root
      class: /Script/UMG.CanvasPanel
      children:
        - slot: {name: CanvasPanelSlot_7, LayoutData: {...}}
          widget:
            name: Border_56
            class: /Script/UMG.Border
            properties: {Padding: {Left: 2.0, Right: 2.0}}
            children: [...]

A widget's `variable` says whether the class exposes it; it defaults to the
widget class's own default (false for panels, true otherwise). Values are YAML:
numbers, booleans, strings; a native struct is a list (a Vector2D is [x, y]),
a Guid 32 hex digits;
a tagged struct a mapping; an enum "EName::Value"; an object reference the
editor's text form, Texture2D'/Game/Path/Asset.Asset'; text a string, or
{namespace, key, source} for localised text.

    desc = extract(package_bytes, path)       # a dict
    package = compile(desc)                   # a bpkit.package.Package

`extract` exists to prove `compile`: every widget Blueprint without functions
in the game must survive extract -> compile byte-identically
(check_widget.py).
"""

import re
import struct

from bpkit.build import Builder, ImportRef, Name, Ref, split_name
from bpkit.deps import derive_preload
from bpkit.package import authored_summary
from bpkit.props import (NATIVE, Array, Prop, Raw, SoftPath, Struct, Text,
                         decode_object, encode_object)
from bpkit.structs import Property, Script, StructExport, decode_export, encode_export

# -- constants the editor writes for every widget Blueprint (measured) ---------
CLASS_FLAGS = 10754064            # CLASS_CompiledFromBlueprint | ... (every widget BP)
CLASS_CONFIG = 'Engine'
#: The flags of the property a variable widget becomes on the class.
VARIABLE_PROPERTY_FLAGS = 0x200008008000c
VARIABLE_FIELD_FLAGS = 0x200001
#: Set on a package holding localizable text (an FText with a namespace and key).
PKG_REQUIRES_LOCALIZATION_GATHER = 0x00040000
#: Object flags: class (Public | Transactional), CDO (Public | CDO | Archetype),
#: everything in the tree (Transactional).
CLASS_OBJECT_FLAGS = 0x9
CDO_OBJECT_FLAGS = 0x31
TREE_OBJECT_FLAGS = 0x8
#: What a CDO always records when the Blueprint implements neither event.
CDO_DEFAULTS = (('bHasScriptImplementedTick', False), ('bHasScriptImplementedPaint', False))

#: UWidget::bIsVariable as each UMG widget's constructor sets it (UMG/Private/
#: Components/*.cpp); a class not listed inherits its nearest listed ancestor's.
VARIABLE_DEFAULTS = {
    'Widget': True, 'BackgroundBlur': False, 'Border': False, 'CanvasPanel': False,
    'GridPanel': False, 'HorizontalBox': False, 'ExpandableArea': True, 'NamedSlot': True,
    'ListViewBase': True, 'NativeWidgetHost': False, 'ScrollBox': False, 'ScaleBox': False,
    'ScrollBar': False, 'TileView': True, 'Spacer': False, 'SizeBox': False, 'WrapBox': False,
    'UniformGridPanel': False, 'TextBlock': False, 'Viewport': True, 'WindowTitleBarArea': False,
    'WidgetSwitcher': True, 'Overlay': False, 'VerticalBox': False}

#: Native struct layouts, as the description writes them.
NATIVE_FORMAT = {'Vector2D': '<2f', 'Vector': '<3f', 'Vector4': '<4f', 'Rotator': '<3f',
                 'Quat': '<4f', 'LinearColor': '<4f', 'Color': '<4B', 'IntPoint': '<2i',
                 'IntVector': '<3i', 'Plane': '<4f'}

def db():
    from bpkit.typedb import shared
    return shared()


# -- object references as text ---------------------------------------------------

def import_text(p, index):
    """An import as the editor's text form: Class'/Package/Path.Object'."""
    chain = []
    i = index
    while i:
        chain.append(i)
        i = p.imports[-i - 1].outer
    chain.reverse()
    names = [p.name(p.imports[-k - 1].object_name) for k in chain]
    path = names[0] + ''.join(('.' if n == 1 else ':') + x for n, x in enumerate(names[1:], 1))
    return "%s'%s'" % (p.name(p.imports[-index - 1].class_name), path)


def parse_object_text(text):
    """Class'/Package/Path.Object:Sub' -> (class, package, [object names])."""
    m = re.match(r"(\w+)'([^.']+)(?:\.([^']*))?'$", text)
    if not m:
        raise ValueError('not an object reference: %r' % text)
    rest = re.split(r'[.:]', m.group(3)) if m.group(3) else []
    return m.group(1), m.group(2), rest


# -- types ---------------------------------------------------------------------

def class_key(path):
    """The type database's key for a class path: a native's short name, a
    Blueprint's lowercased path."""
    return path.split('.')[-1] if path.startswith('/Script/') else path.lower()


def find_property(owner, name):
    """(canonical type, C++ type) of `name` on class or struct `owner` or its ancestors."""
    seen = set()
    while owner and owner not in seen:
        seen.add(owner)
        entry = db()['classes'].get(owner) or db()['structs'].get(owner)
        if entry is None:
            break
        lower = {k.lower(): k for k in entry.get('properties', {})}
        if name.lower() in lower:
            real = lower[name.lower()]                  # FNames ignore case
            return entry['properties'][real], entry.get('cpp', {}).get(real)
        owner = entry.get('super') or entry.get('usmap_super')
    raise KeyError('%s has no property %s' % (owner, name))


def tag_of(canonical, cpp):
    """(tag type, extra) for a property of this type, as FPropertyTag records it."""
    simple = {'bool': 'BoolProperty', 'int': 'IntProperty', 'int8': 'Int8Property',
              'int16': 'Int16Property', 'int64': 'Int64Property', 'uint16': 'UInt16Property',
              'uint32': 'UInt32Property', 'uint64': 'UInt64Property', 'float': 'FloatProperty',
              'double': 'DoubleProperty', 'name': 'NameProperty', 'str': 'StrProperty',
              'text': 'TextProperty', 'byte': 'ByteProperty'}
    if canonical in simple:
        extra = {'enum_name': Name('None')} if canonical == 'byte' else {}
        return simple[canonical], extra
    kind, _, arg = canonical.partition(':')
    arg = arg.rsplit('.', 1)[-1] if arg.startswith('/') else arg   # a Blueprint type's name
    if kind == 'enum':
        as_byte = cpp is None or cpp.startswith('TEnumAsByte') or cpp.strip() == 'uint8'
        return ('ByteProperty' if as_byte else 'EnumProperty'), {'enum_name': Name(arg)}
    if kind == 'struct':
        return 'StructProperty', {'struct_name': Name(arg), 'struct_guid': b'\0' * 16}
    tags = {'object': 'ObjectProperty', 'class': 'ClassProperty', 'softobject': 'SoftObjectProperty',
            'softclass': 'SoftClassProperty', 'weakobject': 'WeakObjectProperty',
            'interface': 'InterfaceProperty'}
    if kind in tags:
        return tags[kind], {}
    if canonical.startswith('array<'):
        inner = canonical[6:-1]
        inner_tag, _ = tag_of(inner, None)
        return 'ArrayProperty', {'inner_type': Name(inner_tag)}
    raise ValueError('no tag for type %s' % canonical)


# -- extraction ----------------------------------------------------------------

def _plain(p, prop, refs):
    """A decoded property value as description data."""
    v = prop.value
    t = prop.type.text
    if t == 'BoolProperty':
        return bool(prop.extra['bool_val'])
    return _plain_value(p, v, t, prop.extra, refs)


def _plain_value(p, v, t, extra, refs):
    if isinstance(v, Raw):
        if t in ('IntProperty', 'Int8Property', 'Int16Property', 'Int64Property',
                 'UInt16Property', 'UInt32Property', 'UInt64Property'):
            fmt = {1: '<b', 2: '<h', 4: '<i', 8: '<q'}[len(v.data)]
            return struct.unpack(fmt, v.data)[0]
        if t in ('FloatProperty', 'DoubleProperty'):
            return struct.unpack('<f' if len(v.data) == 4 else '<d', v.data)[0]
        if t == 'ByteProperty':
            return v.data[0]
        if t == 'StrProperty':
            return _fstring(v.data)
        raise ValueError('raw %s' % t)
    if isinstance(v, Name):
        return v.display()
    if isinstance(v, Ref):
        return refs(v.target)
    if isinstance(v, Text):
        return _text(v)
    if isinstance(v, SoftPath):
        return {'soft': v.asset.display(), 'sub': _fstring(v.sub.data)}
    if isinstance(v, Struct):
        sname = extra.get('struct_name').text if extra.get('struct_name') else None
        if v.kind == 'tagged':
            return {q.name.display(): _plain(p, q, refs) for q in v.value}
        if v.kind == 'native' and sname in NATIVE_FORMAT:
            return list(struct.unpack(NATIVE_FORMAT[sname], v.value))
        if v.kind == 'native' and sname == 'Guid':
            return v.value.hex().upper()
        if v.kind == 'soft':
            return {'soft': v.value.asset.display(), 'sub': _fstring(v.value.sub.data)}
        return {'bytes': v.value.hex()}
    if isinstance(v, Array):
        inner = extra.get('inner_type').text if extra.get('inner_type') else None
        if v.inner_tag is not None:
            sx = {'struct_name': v.inner_tag['struct_name']}
            return [_plain_value(p, item, 'StructProperty', sx, refs) for item in v.items]
        return [_plain_value(p, item, inner, {}, refs) for item in v.items]
    raise ValueError('cannot describe %r' % (v,))


def _fstring(data):
    n = struct.unpack_from('<i', data)[0]
    if n == 0:
        return ''
    if n < 0:
        return data[4:-2].decode('utf-16-le')
    return data[4:-1].decode('latin-1')


#: ETextFlag::CultureInvariant, which every invariant text holding a string carries.
TEXT_CULTURE_INVARIANT = 0x2


def _text(t):
    flags = struct.unpack('<i', t.flags.data)[0]
    if t.history == -1:
        if len(t.fields) == 1:
            return {'empty': True, 'flags': flags}
        if flags == TEXT_CULTURE_INVARIANT:
            return _fstring(t.fields[1].data)
        return {'text': _fstring(t.fields[1].data), 'flags': flags}
    if t.history == 0:
        ns, key, src = (_fstring(f.data) for f in t.fields)
        return {'namespace': ns, 'key': key, 'source': src, 'flags': struct.unpack('<i', t.flags.data)[0]}
    if t.history == 11:
        return {'table': t.fields[0].display(), 'key': _fstring(t.fields[1].data)}
    raise ValueError('text history %d' % t.history)


def extract(uasset, uexp, path, tree_only=False):
    """A description of a widget Blueprint. `tree_only` reads the tree of one
    that has functions too (for borrowing its layout); its script is not read."""
    """A Widget Blueprint package -> its description. Raises if it has functions."""
    from bpkit.package import Package
    from bpkit.check_structs import kind_of
    p = Package.read(uasset, uexp)
    names = [str(n) for n in p.names]
    decoded = {}
    cls = cdo = tree = None
    for i, e in enumerate(p.exports, 1):
        kind = kind_of(p.object_name(e.class_index) or 'Class')
        if kind and kind[0] == 'function':
            if not tree_only:
                raise ValueError('has functions: not a pure layout')
            continue
        if kind:
            decoded[i] = decode_export(e.data, kind[0], names, lambda v: v, kind[1])
            cls = i
        else:
            decoded[i] = decode_object(e.data, names, lambda v: v)
            if e.object_flags & 0x10:
                cdo = i
            elif p.object_name(e.class_index) == 'WidgetTree':
                tree = i

    def refs(target):
        if target < 0:
            return import_text(p, target)
        if target == 0:
            return None
        return '#' + p.object_name(target)

    x = decoded[cls]
    super_path = import_text(p, p.exports[cls - 1].super_index)
    desc = {'name': p.object_name(cls)[:-2], 'path': path,
            'parent': parse_object_text(super_path)[1] + '.' + parse_object_text(super_path)[2][0]}
    class_props = {q.name.display(): _plain(p, q, refs) for q in x.props
                   if q.name.text != 'WidgetTree'}
    if class_props:
        desc['class_properties'] = class_props
    standard = dict(CDO_DEFAULTS)
    defaults = {}
    for q in decoded[cdo][0]:
        value = _plain(p, q, refs)
        if standard.get(q.name.display()) is value:
            continue                              # the compiler always writes these
        defaults[q.name.display()] = value
    if defaults:
        desc['defaults'] = defaults
    if p.exports[cdo - 1].object_flags != cdo_flags(defaults):
        desc['cdo_flags'] = p.exports[cdo - 1].object_flags
    check = {'class_flags': CLASS_FLAGS, 'class_config_name': Name(CLASS_CONFIG)}
    for k, want in check.items():
        if x.fields[k] != want:
            desc.setdefault('class_fields', {})[k] = x.fields[k] if not isinstance(
                x.fields[k], Name) else x.fields[k].display()

    tree_props = {q.name.text: q for q in decoded[tree][0]}
    root = tree_props['RootWidget'].value.target
    variables = [q.name.display() for q in x.properties]

    def widget(i):
        e = p.exports[i - 1]
        props, _tail = decoded[i]
        klass = refs(e.class_index)
        ctext = parse_object_text(klass)
        w = {'name': p.object_name(i), 'class': ctext[1] + '.' + ctext[2][0]}
        own = {}
        children = []
        for q in props:
            n = q.name.text
            if n == 'Slot':
                continue
            if n == 'Slots':
                for s in q.value.items:
                    slot_props, _ = decoded[s.target]
                    slot = {'name': p.object_name(s.target)}
                    content = None
                    for sq in slot_props:
                        if sq.name.text == 'Parent':
                            continue
                        if sq.name.text == 'Content':
                            content = sq.value.target
                            continue
                        slot[sq.name.display()] = _plain(p, sq, refs)
                    sclass = parse_object_text(refs(p.exports[s.target - 1].class_index))
                    slot['class'] = sclass[1] + '.' + sclass[2][0]
                    children.append({'slot': slot, 'widget': widget(content)})
                continue
            if n == 'bIsVariable':
                w['variable'] = bool(q.extra['bool_val'])
                continue
            if n == 'NamedSlotBindings':
                # Widgets placed in a user widget's named slots: no slot object,
                # the binding names the slot and the content widget.
                w['named_slots'] = {}
                for item in q.value.items:
                    fields = {f.name.text: f for f in item.value}
                    content = fields['Content'].value.target
                    w['named_slots'][fields['Name'].value.display()] = widget(content)
                continue
            own[q.name.display()] = _plain(p, q, refs)
        if own:
            w['properties'] = own
        if children:
            w['children'] = children
        return w

    desc['root'] = widget(root)
    desc['_variables'] = variables      # for the gate: compile must derive the same
    return desc


# -- compilation ---------------------------------------------------------------

class Links:
    """Package handles the logic compiler asks for (see bpkit.script.Unit)."""

    GENERATED_PACKAGE = {'WidgetBlueprintGeneratedClass': '/Script/UMG',
                         'BlueprintGeneratedClass': '/Script/Engine',
                         'AnimBlueprintGeneratedClass': '/Script/Engine',
                         # shipped abilities cooked as OMD's own class (Crossbow_Secondary_PBP)
                         'OMDAbilityBlueprintGeneratedClass': '/Script/OMD'}

    def __init__(self, compiler, own_class, own_key, functions):
        self.c = compiler
        self.own_class, self.own_key, self.functions = own_class, own_key, functions

    def own_function(self, name):
        return self.functions[name]

    def module(self, table, name):
        entry = db()[table].get(name) or {}
        if 'module' not in entry:
            raise ValueError('no module known for %s' % name)
        return '/Script/' + entry['module']

    def cls(self, key):
        if key == 'Class':
            return self.c.native_class('/Script/CoreUObject.Class')
        if key.startswith('/'):
            entry = db()['classes'][key.lower()]
            pkg, name = entry['path'].rsplit('.', 1)
            return self.c.b.import_object(self.GENERATED_PACKAGE[entry['generated']],
                                          entry['generated'], name, self.c.package(pkg))
        return self.c.native_class('%s.%s' % (self.module('classes', key), key))

    def struct(self, name):
        if name.startswith('/'):
            pkg, short = db()['structs'][name.lower()]['path'].rsplit('.', 1)
            return self.c.b.import_object('/Script/Engine', 'UserDefinedStruct', short,
                                          self.c.package(pkg))
        return self.c.b.import_object('/Script/CoreUObject', 'ScriptStruct', name,
                                      self.c.package(self.module('structs', name)))

    def enum(self, name):
        return self.c.b.import_object('/Script/CoreUObject', 'Enum', name,
                                      self.c.package(self.module('enums', name)))

    def function(self, owner, name):
        return self.c.b.import_object('/Script/CoreUObject', 'Function', name, self.cls(owner))

    def package_function(self, package, name):
        """A function whose outer is a package: a native delegate's signature."""
        return self.c.b.import_object('/Script/CoreUObject', 'Function', name,
                                      self.c.package(package))

    def object(self, text):
        """An asset named as a description names one: Class'/Package/Path.Object'."""
        return self.c.object_ref(text)

    def cdo(self, owner):
        if owner.startswith('/'):
            return self.c.class_default(db()['classes'][owner.lower()]['path'])
        module = self.module('classes', owner)
        return self.c.b.import_object(module, owner, 'Default__' + owner, self.c.package(module))


class Compiler:
    def __init__(self, desc):
        self.d = desc
        self.b = Builder(summary=authored_summary(desc['path']))
        self.pkg_path = desc['path']
        self.class_name = desc['name'] + '_C'

    # imports
    def package(self, path):
        return self.b.import_package(path)

    def native_class(self, script_path):
        module, name = script_path.split('.')
        return self.b.import_object('/Script/CoreUObject', 'Class', name, self.package(module))

    def class_ref(self, path):
        """A class by path: '/Script/UMG.Image' or '/Game/X/Y.Y_C'."""
        if path.startswith('/Script/'):
            return self.native_class(path)
        pkg, name = path.split('.')
        return self.b.import_object('/Script/UMG', 'WidgetBlueprintGeneratedClass', name,
                                    self.package(pkg))

    def class_default(self, path):
        """The CDO of a class by path, as a template import."""
        pkg, name = path.split('.')
        cls = self.class_ref(path)
        return self.b.import_object(pkg if path.startswith('/Script/') else pkg, name,
                                    'Default__' + name, self.package(pkg))

    def object_ref(self, text):
        klass, pkg, names = parse_object_text(text)
        cls_pkg = '/Script/' + db()['classes'].get(klass, {}).get('module', 'Engine') \
            if not klass.endswith('_C') else None
        outer = self.package(pkg)
        for n in names[:-1]:
            outer = self.b.import_object('/Script/CoreUObject', 'Object', n, outer)
        return self.b.import_object(cls_pkg or pkg, klass, names[-1] if names else pkg, outer)

    # values
    def value(self, owner, name, data):
        canonical, cpp = find_property(owner, name)
        return self.prop(name, canonical, cpp, data)

    def prop(self, name, canonical, cpp, data):
        tag, extra = tag_of(canonical, cpp)
        pname = Name(*split_name(name))
        if tag == 'BoolProperty':
            return Prop(pname, Name(tag), 0, {'bool_val': 1 if data else 0}, None, None)
        value = self.encode(canonical, data, extra)
        if isinstance(value, Array) and value.inner_tag is not None:
            value.inner_tag['name'] = pname            # a struct array's inner tag repeats it
        return Prop(pname, Name(tag), 0, extra, None, value)

    def encode(self, canonical, data, extra):
        fixed = {'int': '<i', 'int8': '<b', 'int16': '<h', 'int64': '<q', 'uint16': '<H',
                 'uint32': '<I', 'uint64': '<Q', 'float': '<f', 'double': '<d'}
        if canonical in fixed:
            return Raw(struct.pack(fixed[canonical], data))
        if canonical == 'byte':
            return Raw(bytes([data]))
        if canonical == 'name':
            return Name(*split_name(data))
        if canonical == 'str':
            return Raw(fstring(data))
        if canonical == 'text':
            t = text_value(data)
            if t.history == 0:
                self.localized = True               # gatherable: a namespace and key
            return t
        kind, _, arg = canonical.partition(':')
        if kind == 'enum':
            return Name(*split_name(data)) if isinstance(data, str) else Raw(bytes([data]))
        if kind in ('object', 'class', 'interface', 'weakobject'):
            if data is None:
                return Ref(None)
            if data.startswith('#'):
                return Ref(self.exports[data[1:]])
            return Ref(self.object_ref(data))
        if kind in ('softobject', 'softclass'):
            return SoftPath(Name(*split_name(data['soft'])), Raw(fstring(data['sub'])))
        if kind == 'struct':
            if arg in NATIVE_FORMAT:
                return Struct('native', struct.pack(NATIVE_FORMAT[arg], *data))
            if arg == 'Guid':
                return Struct('native', bytes.fromhex(data))
            if isinstance(data, dict) and 'bytes' in data:
                return Struct('native', bytes.fromhex(data['bytes']))
            return Struct('tagged', [self.value(arg, k, v) for k, v in data.items()])
        if canonical.startswith('array<'):
            inner = canonical[6:-1]
            if inner.startswith('struct:'):
                sname = inner[7:]
                items = [self.encode(inner, item, {}) for item in data]
                tag = {'name': Name('None'), 'type': Name('StructProperty'),
                       'index': 0, 'struct_name': Name(sname), 'struct_guid': b'\0' * 16,
                       'guid': None}
                return Array(items, tag)
            return Array([self.encode(inner, item, {}) for item in data])
        raise ValueError('cannot encode %s' % canonical)

    def props(self, owner, mapping):
        return [self.value(owner, k, v) for k, v in mapping.items()]

    # the tree
    def compile(self):
        d, b = self.d, self.b
        parent = d['parent']
        parent_cls = self.class_ref(parent)
        parent_cdo = self.class_default(parent)
        wbpgc = self.native_class('/Script/UMG.WidgetBlueprintGeneratedClass')
        wbpgc_cdo = self.class_default('/Script/UMG.WidgetBlueprintGeneratedClass')
        tree_cls = self.native_class('/Script/UMG.WidgetTree')
        tree_cdo = self.class_default('/Script/UMG.WidgetTree')
        object_cls = self.native_class('/Script/CoreUObject.Object')

        cls = b.export_object(self.class_name, wbpgc, None, super=parent_cls, template=wbpgc_cdo,
                              object_flags=CLASS_OBJECT_FLAGS, is_asset=1)
        cdo = b.export_object('Default__' + self.class_name, cls, None, template=parent_cdo,
                              object_flags=d.get('cdo_flags', cdo_flags(d.get('defaults'))))
        # A widget with no root of its own (kind: widget, a child of another
        # widget Blueprint) has no tree: the engine shows its nearest parent's
        # (UWidgetBlueprintGeneratedClass::FindWidgetTreeOwningClass).
        rootless = 'root' not in d
        tree = None if rootless else b.export_object('WidgetTree', tree_cls, cls, template=tree_cdo,
                                                     object_flags=TREE_OBJECT_FLAGS)
        self.exports = {}
        self.slots = {}
        self.localized = False
        widgets = []        # (desc, export, slot export or None, parent export)

        def declare(w, slot_desc=None, parent_export=None):
            wcls = self.class_ref(w['class'])
            e = b.export_object(w['name'], wcls, tree, template=self.class_default(w['class']),
                                object_flags=TREE_OBJECT_FLAGS)
            self.exports[w['name']] = e
            slot = None
            if slot_desc is not None:
                # Slot names are unique only within their panel, so slots are
                # tracked by their place in the description, not by name.
                slot = b.export_object(slot_desc['name'], self.class_ref(slot_desc['class']),
                                       parent_export,
                                       template=self.class_default(slot_desc['class']),
                                       object_flags=TREE_OBJECT_FLAGS)
                self.slots[id(slot_desc)] = slot
            widgets.append((w, e, slot, parent_export, slot_desc))
            for child in w.get('children', ()):
                declare(child['widget'], child['slot'], e)
            for content in w.get('named_slots', {}).values():
                declare(content)
        if not rootless:
            declare(d['root'])

        # payloads, now every export has a handle
        variables = []
        for w, e, slot, parent_export, slot_desc in widgets:
            wclass = class_key(w['class'])
            props = self.props(wclass, w.get('properties', {}))
            children = w.get('children', ())
            if children:
                slots = Prop(Name('Slots'), Name('ArrayProperty'), 0,
                             {'inner_type': Name('ObjectProperty')}, None,
                             Array([Ref(self.slots[id(c['slot'])]) for c in children]))
                props = self.ordered(wclass, props + [slots])
            if w.get('named_slots'):
                bindings = [Struct('tagged', [
                    Prop(Name('Name'), Name('NameProperty'), 0, {}, None, Name(*split_name(n))),
                    Prop(Name('Content'), Name('ObjectProperty'), 0, {}, None,
                         Ref(self.exports[c['name']]))]) for n, c in w['named_slots'].items()]
                tag = {'name': Name('NamedSlotBindings'), 'type': Name('StructProperty'),
                       'index': 0, 'struct_name': Name('NamedSlotBinding'),
                       'struct_guid': b'\0' * 16, 'guid': None}
                props = self.ordered(wclass, props + [Prop(
                    Name('NamedSlotBindings'), Name('ArrayProperty'), 0,
                    {'inner_type': Name('StructProperty')}, None, Array(bindings, tag))])
            if slot is not None:
                props = self.ordered(wclass, props + [Prop(Name('Slot'), Name('ObjectProperty'), 0,
                                                           {}, None, Ref(slot))])
            default_variable = self.variable_default(wclass)
            variable = w.get('variable', default_variable) or bool(w.get('events'))
            if variable != default_variable:
                props = self.ordered(wclass, props + [Prop(Name('bIsVariable'), Name('BoolProperty'), 0,
                                                           {'bool_val': int(variable)}, None, None)])
            if variable:
                variables.append((w['name'], self.class_ref(w['class'])))
            e.data = encode_object(props, b'\0\0\0\0')
            if slot is not None:
                sclass = class_key(slot_desc['class'])
                sprops = self.props(sclass, {k: v for k, v in slot_desc.items()
                                             if k not in ('name', 'class')})
                sprops = self.ordered(sclass, sprops + [
                    Prop(Name('Parent'), Name('ObjectProperty'), 0, {}, None, Ref(parent_export)),
                    Prop(Name('Content'), Name('ObjectProperty'), 0, {}, None, Ref(e))])
                slot.data = encode_object(sprops, b'\0\0\0\0')
        if not rootless:
            tree.data = encode_object([Prop(Name('RootWidget'), Name('ObjectProperty'), 0, {}, None,
                                            Ref(self.exports[d['root']['name']]))], b'\0\0\0\0')
        own = self.props(class_key(parent), d.get('defaults', {}))
        given = {q.name.text.lower() for q in own}
        implemented = {'bHasScriptImplementedTick': 'Tick', 'bHasScriptImplementedPaint': 'OnPaint'}
        overrides = self.script_overrides(d)
        standard = [Prop(Name(k), Name('BoolProperty'), 0, {'bool_val': int(v)}, None, None)
                    for k, v in CDO_DEFAULTS
                    if k.lower() not in given and implemented[k] not in overrides]
        self.cdo_props = self.ordered(class_key(parent), own + standard)
        cdo.data = encode_object(self.cdo_props)
        # the script: functions and variables of the class
        functions, script_vars, overridden = self.script(d, cls, parent, variables)
        bindings = self.bindings(widgets, cls)
        # The CDO again, now that the script's variables are known: the class's
        # own variables come before its parent's.
        if getattr(self, 'script_defaults', None):
            own_defaults = [self.prop(n, t, None, v) for n, (t, v) in self.script_defaults.items()]
            cdo.fields['object_flags'] = d.get('cdo_flags', cdo_flags(True))
            cdo.data = encode_object(own_defaults + self.cdo_props)
        # the class
        x = StructExport()
        x.kind = 'class'
        cprops = [] if rootless else [Prop(Name('WidgetTree'), Name('ObjectProperty'), 0, {}, None, Ref(tree))]
        cprops += self.props('WidgetBlueprintGeneratedClass', d.get('class_properties', {}))
        if bindings is not None:
            cprops.append(Prop(Name('DynamicBindingObjects'), Name('ArrayProperty'), 0,
                               {'inner_type': Name('ObjectProperty')}, None, Array([Ref(bindings)])))
        x.props = self.ordered('WidgetBlueprintGeneratedClass', cprops)
        x.guid = None
        x.super = Ref(parent_cls)
        # Children: functions, most recently created first; FuncMap: by name.
        x.children = [Ref(e) for e in reversed(functions)]
        # The class's variables are sorted as FNames: text, then number.
        variables.sort(key=lambda v: (split_name(v[0])[0].lower(), split_name(v[0])[1]))
        x.properties = [self.variable_property(n, c) for n, c in variables] + script_vars
        x.script = Script(0, [], [], lambda v: v)
        fields = d.get('class_fields', {})
        func_map = sorted(((e.name, e) for e in functions), key=lambda f: f[0].lower())
        x.fields = {'func_map': [(Name(*split_name(n)), Ref(e)) for n, e in func_map],
                    'class_flags': fields.get('class_flags', CLASS_FLAGS),
                    'class_within': Ref(object_cls),
                    'class_config_name': Name(fields.get('class_config_name', CLASS_CONFIG)),
                    'class_generated_by': Ref(None), 'interfaces': [],
                    'deprecated_force_script_order': 0, 'dummy': Name('None'), 'cooked': 1,
                    'class_default_object': Ref(cdo)}
        cls.data = encode_export(x)
        self.variables = [n for n, _ in variables]
        # export order: the class's functions (Children order), the class, its
        # CDO, then the rest by full name
        rest = [e for e in b.exports if e not in (cls, cdo) and e not in functions]
        rest.sort(key=lambda e: self.full_name(e).lower())
        b.exports = list(reversed(functions)) + [cls, cdo] + rest
        b.extra_names.add(self.pkg_path)
        if self.localized:
            b.summary.flags |= PKG_REQUIRES_LOCALIZATION_GATHER
        p = b.build()
        return derive_preload(p)

    def script_source(self, d):
        if 'script' not in d:
            return None
        import os
        path = os.path.join(d.get('_dir', '.'), d['script'])
        with open(path) as fh:
            return fh.read(), path

    def script_overrides(self, d):
        """Names of the functions the script declares @override."""
        import ast
        src = self.script_source(d)
        if not src:
            return set()
        return {f.name for f in ast.parse(src[0]).body if isinstance(f, ast.FunctionDef)
                and any(isinstance(x, ast.Name) and x.id == 'override' for x in f.decorator_list)}

    def script(self, d, cls, parent, variables):
        """Compile the description's script into function exports.

        Returns (function exports, variable properties, overridden names).
        """
        from bpkit import script as S
        src = self.script_source(d)
        if not src:
            return [], [], set()
        source, path = src
        members = {}
        for n, c in variables:
            members[n] = 'object:' + self.class_type_of(c)
        functions = {}
        links = Links(self, cls, self.class_name, functions)
        unit = S.Unit(source, db(), links, class_key(parent), members, path)
        fn_cls = self.native_class('/Script/CoreUObject.Function')
        fn_cdo = self.class_default('/Script/CoreUObject.Function')
        for f in unit.functions:
            functions[f.name] = self.b.export_object(f.name, fn_cls, cls, template=fn_cdo,
                                                     object_flags=0x1)
        exports = []
        for compiled in unit.compile():
            e = functions[compiled.name]
            e.super = compiled.super_function
            x = StructExport()
            x.kind = 'function'
            x.props, x.guid = [], None
            x.super = Ref(compiled.super_function)
            x.children = []
            x.properties = compiled.properties
            x.script = compiled.script
            x.fields = {'function_flags': compiled.flags, 'event_graph_function': Ref(None),
                        'event_graph_call_offset': 0}
            if compiled.flags & 0x40:
                x.fields['rep_offset'] = b'\0\0'        # FUNC_Net: RepOffset, 0 in a Blueprint
            e.data = encode_export(x)
            exports.append(e)
        # a script variable named as a widget is the widget at run time
        # (SkullButton's Press: its click pressed itself, for ever)
        clash = sorted(set(unit.variables) & set(members))
        if clash:
            raise ValueError('%s: script variables share widget names: %s' % (path, ', '.join(clash)))
        nets = [f for f, (_p, _r, flags, _s) in unit.signatures.items() if flags & 0x40]
        if ('root' in d or d.get('kind') == 'widget') and (unit.replicated or nets):
            raise ValueError('%s: a widget is never replicated; replicated variables and RPCs '
                             'belong on an actor (%s)' % (path, ', '.join(
                                 sorted(unit.replicated) + nets)))
        script_vars = []
        defaults = {}
        for name, (t, default) in unit.variables.items():
            q = S.make_property(unit, name, t, S.VARIABLE_FLAGS, S.VARIABLE_FIELD_FLAGS)
            if name in unit.replicated:
                from bpkit.replication import CPF_NET, CPF_REPNOTIFY
                notify, cond = unit.replicated[name]
                q.property_flags |= CPF_NET | (CPF_REPNOTIFY if notify else 0)
                q.rep_index = unit.rep_index(name)
                q.rep_notify = Name(*split_name(notify)) if notify else Name('None')
                q.rep_condition = cond
            script_vars.append(q)
            if default is not None:
                import ast
                try:
                    value = ast.literal_eval(default)
                except ValueError:
                    raise S.CompileError('%s: a variable\'s default must be a plain literal '
                                         '(a number, text, True/False); leave it off for the '
                                         'type\'s zero' % name, default) from None
                if value not in (0, 0.0, False, '', None):
                    defaults[name] = (t, value)
        self.script_defaults = defaults
        self.net_count = len(unit.replicated)
        if unit.replicated:
            from bpkit.replication import register
            register((d['path'] + '.' + self.class_name).lower(), self.net_count)
        return exports, script_vars, {f.name for f in unit.functions}

    def replication_props(self):
        """NumReplicatedProperties for the class, when the script replicates anything."""
        if not getattr(self, 'net_count', 0):
            return []
        return [Prop(Name('NumReplicatedProperties'), Name('IntProperty'), 0, {}, None,
                     Raw(struct.pack('<i', self.net_count)))]

    def class_type_of(self, ref):
        """The type name of a class import handle: a native's name, a Blueprint's path."""
        if ref.class_name == 'Class':
            return ref.name
        pkg = ref.outer.name
        return '%s.%s' % (pkg, ref.name)

    def bindings(self, widgets, cls):
        """A ComponentDelegateBinding for every widget event bound to a function."""
        entries = []
        for w, e, slot, parent_export, slot_desc in widgets:
            for delegate, function in (w.get('events') or {}).items():
                entries.append(Struct('tagged', [
                    Prop(Name('ComponentPropertyName'), Name('NameProperty'), 0, {}, None,
                         Name(*split_name(w['name']))),
                    Prop(Name('DelegatePropertyName'), Name('NameProperty'), 0, {}, None,
                         Name(*split_name(delegate))),
                    Prop(Name('FunctionNameToBind'), Name('NameProperty'), 0, {}, None,
                         Name(*split_name(function)))]))
        if not entries:
            return None
        binding_cls = self.native_class('/Script/Engine.ComponentDelegateBinding')
        binding_cdo = self.class_default('/Script/Engine.ComponentDelegateBinding')
        e = self.b.export_object('ComponentDelegateBinding_0', binding_cls, cls,
                                 template=binding_cdo, object_flags=0)
        tag = {'name': Name('ComponentDelegateBindings'), 'type': Name('StructProperty'),
               'index': 0, 'struct_name': Name('BlueprintComponentDelegateBinding'),
               'struct_guid': b'\0' * 16, 'guid': None}
        e.data = encode_object([Prop(Name('ComponentDelegateBindings'), Name('ArrayProperty'), 0,
                                     {'inner_type': Name('StructProperty')}, None,
                                     Array(entries, tag))], b'\0\0\0\0')
        return e

    def full_name(self, e):
        parts = []
        x = e
        while x is not None:
            parts.append(x.name)
            x = x.outer
        parts.reverse()
        path = self.pkg_path + '.' + parts[0] + ''.join(
            (':' if i == 1 else '.') + n for i, n in enumerate(parts[1:], 1))
        klass = e.klass.name if not isinstance(e.klass, str) else e.klass
        return '%s %s' % (klass, path)

    def variable_property(self, name, cls_ref):
        q = Property(Name('ObjectProperty'))
        q.name = Name(*split_name(name))
        q.flags = VARIABLE_FIELD_FLAGS
        q.array_dim, q.element_size = 1, 8
        q.property_flags = VARIABLE_PROPERTY_FLAGS
        q.rep_index = 0
        q.rep_notify = Name('None')
        q.rep_condition = 0
        q.refs = {'property_class': Ref(cls_ref)}
        return q

    def variable_default(self, cls):
        seen = set()
        while cls and cls not in seen:
            if cls in VARIABLE_DEFAULTS:
                return VARIABLE_DEFAULTS[cls]
            seen.add(cls)
            c = db()['classes'].get(cls) or {}
            cls = c.get('super') or c.get('usmap_super')
        return True

    def ordered(self, cls, props):
        """Order properties as the engine serialises them: the class's own first,
        then each ancestor's, each in declaration order."""
        order = []
        seen = set()
        c = cls
        while c and c not in seen:
            seen.add(c)
            entry = db()['classes'].get(c) or {}
            order.extend(entry.get('properties', {}))
            c = entry.get('super') or entry.get('usmap_super')
        rank = {}
        for i, n in enumerate(order):
            rank.setdefault(n.lower(), i)
        return sorted(props, key=lambda q: rank.get(q.name.text.lower(), len(rank)))


def cdo_flags(defaults):
    """The CDO's object flags: Public | ClassDefaultObject | ArchetypeObject,
    plus Transactional once it sets defaults of its own (measured: every one of
    the game's layout widgets that sets one has it, none that does not)."""
    return CDO_OBJECT_FLAGS | (0x8 if defaults else 0)


def fstring(s):
    if s == '':
        return struct.pack('<i', 0)
    if any(ord(c) > 127 for c in s):
        data = s.encode('utf-16-le') + b'\0\0'
        return struct.pack('<i', -(len(data) // 2)) + data
    return struct.pack('<i', len(s) + 1) + s.encode('latin-1') + b'\0'


def text_value(data):
    """FText from description data: a string is culture-invariant text."""
    if isinstance(data, str):
        data = {'text': data, 'flags': TEXT_CULTURE_INVARIANT}
    if 'text' in data:
        return Text(Raw(struct.pack('<i', data['flags'])), -1,
                    [Raw(struct.pack('<i', 1)), Raw(fstring(data['text']))])
    if data.get('empty'):
        return Text(Raw(struct.pack('<i', data.get('flags', 0))), -1, [Raw(struct.pack('<i', 0))])
    if 'table' in data:
        return Text(Raw(struct.pack('<i', 0)), 11, [Name(*split_name(data['table'])),
                                                    Raw(fstring(data['key']))])
    return Text(Raw(struct.pack('<i', data.get('flags', 0))), 0,
                [Raw(fstring(data['namespace'])), Raw(fstring(data['key'])),
                 Raw(fstring(data['source']))])


def duplicate_names(root):
    """Widget and slot names used more than once in one tree. The game refuses
    such a package as it loads ("... does not have all load flags") and dies."""
    seen, dups = set(), []

    def walk(node):
        widget = node.get('widget', node)
        for name in (widget.get('name'), (node.get('slot') or {}).get('name')):
            if name and name in seen and name not in dups:
                dups.append(name)
            seen.add(name)
        for child in widget.get('children') or []:
            walk(child)
    walk(root)
    return dups


def compile(desc):
    c = Compiler(desc)
    p = c.compile()
    p.variables = c.variables
    return p


# -- building ------------------------------------------------------------------

def verify(package, label, path=None):
    """Reasons a built package must not ship; empty when it is sound.

    Independent of the compiler that made it: the bytes are read back by
    bpkit's decoders and by pkgcheck, which shares no code with the writer, and
    the package must survive its own round trip.
    """
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import pkgcheck
    from bpkit.check_structs import kind_of
    from bpkit.package import Package
    head, body = package.write()
    bad = ['pkgcheck: %s' % p for p in pkgcheck.problems(head, body, label)]
    again = Package.read(head, body)
    if again.write() != (head, body):
        bad.append('the package does not survive its own round trip')
    names = [str(n) for n in again.names]
    for i, e in enumerate(again.exports, 1):
        kind = kind_of(again.object_name(e.class_index) or 'Class')
        try:
            if kind:
                decode_export(e.data, kind[0], names, lambda v: v, kind[1])
            else:
                decode_object(e.data, names, lambda v: v)
        except Exception as ex:
            bad.append('%s does not decode: %s' % (again.object_name(i), ex))
    if path and any(again.object_name(e.class_index) == 'Function' for e in again.exports):
        from bpkit.check_script import verify_logic
        bad += verify_logic(again, path, db())
    return bad


def declare(descs):
    """Adds a skeleton of every description's class to the type database: its
    path, parent, variables and function signatures, but no bytecode. Run
    before compiling any of them, so each may name the others whatever the
    order (a menu creates its rows, and a row calls back into its menu). Each
    compiled package then replaces its skeleton (typedb.add_package)."""
    import os
    from bpkit import script as S
    entries = {}
    for d in descs:
        name = d['name'] + '_C'
        key = (d['path'] + '.' + name).lower()
        widget = 'root' in d or d.get('kind') == 'widget'
        parent = d.get('parent', '/Script/Engine.Actor')
        entries[key] = db()['classes'][key] = {
            'super': class_key(parent), 'kind': 'blueprint', 'source': 'skeleton',
            'path': d['path'] + '.' + name,
            'generated': 'WidgetBlueprintGeneratedClass' if widget else 'BlueprintGeneratedClass',
            'properties': {}}
    for d in descs:
        key = (d['path'] + '.' + d['name'] + '_C').lower()
        entry = entries[key]
        c = Compiler(d)
        if 'root' in d:
            def walk(w):
                default = c.variable_default(class_key(w['class']))
                if w.get('variable', default) or w.get('events'):
                    wclass = w['class'] if w['class'].startswith('/Script/') else w['class']
                    entry['properties'][w['name']] = 'object:' + (
                        class_key(wclass) if wclass.startswith('/Script/') else wclass)
                for child in w.get('children', ()):
                    walk(child['widget'])
                for content in w.get('named_slots', {}).values():
                    walk(content)
            walk(d['root'])
        if 'script' not in d:
            continue
        with open(os.path.join(d.get('_dir', '.'), d['script'])) as fh:
            source = fh.read()
        unit = S.Unit(source, db(), None, entry['super'], dict(entry['properties']),
                      d['script'])
        for name, (t, _default) in unit.variables.items():
            entry['properties'][name] = t
        if unit.replicated:
            entry['net_count'] = len(unit.replicated)
        for f in unit.functions:
            params, ret, flags, _super = unit.signatures[f.name]
            ps = [{'name': n, 'type': t, 'out': out, 'ref': False, 'const': False,
                   'return': False} for n, t, out in params]
            if ret:
                ps.append({'name': 'ReturnValue', 'type': ret, 'out': True, 'ref': False,
                           'const': False, 'return': True})
            db()['functions']['%s:%s' % (key, f.name.lower())] = {
                'owner': key, 'static': False, 'function_flags': flags, 'params': ps,
                'locals': {}, 'source': 'blueprint'}


def content_path(game_path):
    """'/Game/Mods/X/Y' -> 'OMD/Content/Mods/X/Y', where a pak keeps it."""
    return 'OMD/Content/' + game_path[len('/Game/'):]


def main(argv=None):
    import argparse
    import os
    import sys
    import yaml
    ap = argparse.ArgumentParser(description='Compile widget and actor descriptions to cooked '
                                 'packages; a later one may name an earlier one\'s class.')
    sub = ap.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build', help='compile one or more descriptions')
    b.add_argument('descriptions', nargs='+')
    b.add_argument('--out', help='write .uasset/.uexp under this directory')
    b.add_argument('--pak', help='also write every package into this v4 pak')
    args = ap.parse_args(argv)
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    'UE Assets'))
    import pakv4
    files = {}
    failed = False
    descs = []
    for path in args.descriptions:
        with open(path) as fh:
            desc = yaml.safe_load(fh)
        desc['_dir'] = os.path.dirname(os.path.abspath(path))
        if 'root' in desc and duplicate_names(desc['root']):
            raise SystemExit('%s: names used twice in one tree: %s'
                             % (path, ', '.join(duplicate_names(desc['root']))))
        descs.append(desc)
    declare(descs)
    for desc in descs:
        if 'root' in desc or desc.get('kind') == 'widget':
            package = compile(desc)
        elif desc.get('kind') == 'object':
            from bpkit import uobject
            package = uobject.compile(desc)
        else:
            from bpkit import actor
            package = actor.compile(desc)
        problems = verify(package, desc['name'], desc['path'])
        if problems:
            failed = True
            print('%s: NOT BUILT' % desc['name'])
            for p in problems:
                print('   ' + p)
            continue
        # later descriptions may name this one's class (Asset(...)) and call it
        from bpkit.typedb import add_package
        add_package(db(), package, desc['path'])
        head, body = package.write()
        target = content_path(desc['path'])
        files[target + '.uasset'] = head
        files[target + '.uexp'] = body
        print('%s: %d exports, %d imports, %d bytes' % (desc['name'], len(package.exports),
                                                       len(package.imports), len(head) + len(body)))
        for w in getattr(package, 'warnings', ()):
            print('   warning: ' + w)
        if args.out:
            os.makedirs(os.path.join(args.out, os.path.dirname(target)), exist_ok=True)
            for ext, data in (('.uasset', head), ('.uexp', body)):
                with open(os.path.join(args.out, target + ext), 'wb') as fh:
                    fh.write(data)
    if failed:
        return 1
    if args.pak:
        pakv4.write(args.pak, files)
        print('wrote %s' % args.pak)
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
