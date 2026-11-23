"""Widget Blueprints for Orcs Must Die! Deathtrap (UE 5.3), compiled editor-free into a Zen
package: a description in bpkit's widget format (vendor/bpkit/widget.py) plus a script in
bpkit's script language (vendor/bpkit/script.py, compiled by bpkit's own front end over the
Deathtrap type database, typedb5).

    desc = load('W_Probe.yaml')
    pkg = compile(desc)                  # a zenpackage.ZenPackage
    problems = verify(pkg.to_bytes())    # [] when the package is sound

    python3 -m riftlord.formats.omdd.bpwidget5 build Desc.yaml --out DIR [--container NAME_P]

A description:

    name: W_RiftLordCustomMapsProbe
    path: /Game/Mods/RiftLord/W_RiftLordCustomMapsProbe
    parent: /Script/UMG.UserWidget
    script: W_RiftLordCustomMapsProbe.py
    root:
      name: Root
      class: /Script/UMG.VerticalBox
      children:
        - slot: {HorizontalAlignment: HAlign_Center, Padding: {Top: 40}}
          widget: {name: Title, class: /Script/UMG.TextBlock, variable: true,
                   properties: {Text: Custom Maps}}
        - widget: {name: ClickButton, class: /Script/UMG.Button, events: {OnClicked: Clicked}}

Values: numbers, booleans, strings; an enum by its value name (HAlign_Center or
EHorizontalAlignment::HAlign_Center); a binary struct (Vector2D, LinearColor) a list; any other
struct a mapping; text a string (culture invariant) or {namespace, key, source}; an object
reference Class'/Script/Module.Object' or Texture2D'/Game/Path/Asset.Asset'. A widget's class may
be another package's Blueprint (/Game/UI/Buttons/W_ButtonBasic.W_ButtonBasic_C); descriptions
built together (`compile_all`) may import each other's classes. Slots are named
<SlotClass>_<n>, n counting each slot class from 0 in the package, as the editor names them.

What is written is what the shipped widget Blueprints hold (measured on all 295,
docs/research/DEATHTRAP-MAPS.md "P2 Blueprint writer"):

- exports sorted by (class name, package-relative path), imports by (class name, full path),
  both case-insensitively; a script package (/Script/UMG) is imported for every module used;
- names: those the export data uses, sorted case-insensitively, then the export names not among
  them in export order, then the package name;
- object flags: class 0x9, CDO 0x31, functions 0x1 (public, with an export hash), widget tree
  objects 0x8, the delegate binding 0; class flags 0xA41800, config name Engine, the class
  tags map {BlueprintType: BPTYPE_Normal}; package flags 0x80002200 (+0x40000 with localized
  text);
- a variable widget becomes an ObjectProperty with property flags 0x200008009001C; the class's
  widget variables are sorted as FNames, the script's follow in script order; the FuncMap is in
  script order and Children the reverse;
- every object of the tree, the CDO and the binding end with bHasGuid = 0 after their
  properties;
- dependency bundles by `dependencies` (the rules reproduce 202 of the 227 shipped widget
  packages whose exports all read, exactly), the export bundle by `bundle_order`.

Events compile as functions bound by name (a ComponentDelegateBinding), not into an ubergraph.
"""

import argparse
import copy
import os
import sys

from riftlord.core import bpkitlink
from riftlord.formats.omdd import (bpclass5, iostore_write, kismet5, trace_omdd, typedb5,
                                   unversioned_write, zenpackage)

bpkitlink.install()
from bpkit import script as bpscript                   # noqa: E402
from bpkit import widget as bpwidget                   # noqa: E402

CLASS_FLAGS = 0xA41800
CLASS_CONFIG = 'Engine'
CLASS_TAGS = (('BlueprintType', 'BPTYPE_Normal'),)
PACKAGE_FLAGS = 0x80002200
PKG_REQUIRES_LOCALIZATION_GATHER = 0x40000
CLASS_OBJECT_FLAGS, CDO_OBJECT_FLAGS, TREE_OBJECT_FLAGS = 0x9, 0x31, 0x8
FUNCTION_OBJECT_FLAGS, BINDING_OBJECT_FLAGS = 0x1, 0x0
RF_CLASS_DEFAULT_OBJECT = 0x10
VARIABLE_PROPERTY_FLAGS = 0x200008009001C
VARIABLE_FIELD_FLAGS = 0x200001
#: Element sizes in this build where they differ from bpkit's table (measured on the shipped
#: FProperties: DelegateProperty 595 x 32, MulticastInlineDelegateProperty 53 x 24).
ELEMENT_SIZES = {'DelegateProperty': 32, 'MulticastInlineDelegateProperty': 24,
                 'MulticastSparseDelegateProperty': 24}
#: What a CDO records when the script implements neither event (as bpkit writes for OMD3,
#: and as the shipped UserWidget children hold).
CDO_DEFAULTS = (('bHasScriptImplementedTick', 'Tick'), ('bHasScriptImplementedPaint', 'OnPaint'))
#: bHasGuid, after the properties of every object that is not a struct export.
NO_GUID = b'\0\0\0\0'
#: The package tag after the last export.
PACKAGE_TAG = bytes.fromhex('c1832a9e')
TEXT_CULTURE_INVARIANT = 2
#: Class names of script objects other than classes, by what they are.
SCRIPT_CLASS = {'package': 'Package', 'function': 'Function', 'struct': 'ScriptStruct',
                'enum': 'Enum', 'class': 'Class', 'delegate': 'DelegateFunction'}
#: Import kinds: a native object, an object of another package, another package itself (the
#: legacy package object, which a Zen import map holds as a null entry).
SCRIPT, PACKAGE, NULL = 'script', 'package', 'null'


class BuildError(ValueError):
    pass


# -- objects of the package ------------------------------------------------------------------

class Obj:
    """An object the package refers to. An import has `path` ('/Script/UMG.TextBlock',
    '/Game/UI/Buttons/W_ButtonBasic.W_ButtonBasic_C'), `class_name` (the name of its class, for
    sorting) and `kind` (SCRIPT, PACKAGE or NULL); an import of another package's object also
    has `package` and `relative` (its path inside that package, 'Asset_C/Child', which its
    public export hash names). An export has a name, its class, outer, template and super (Obj
    or None), object flags, `public` and an `encode(ctx)` giving its bytes."""

    def __init__(self, name, path=None, class_name=None, cls=None, outer=None, template=None,
                 super_=None, flags=0, public=False, encode=None, kind=SCRIPT, package=None,
                 relative=None):
        self.name, self.path, self.class_name = name, path, class_name
        self.cls, self.outer, self.template, self.super = cls, outer, template, super_
        self.flags, self.public, self.encode = flags, public, encode
        self.kind, self.package, self.relative = kind, package, relative
        self.value = None                    # the FPackageObjectIndex, once laid out

    @property
    def is_import(self):
        return self.path is not None

    def relative_path(self):
        """Package-relative path of an export, as its public hash names it: 'Asset/Child'."""
        parts, o = [], self
        while o is not None:
            parts.append(o.name)
            o = o.outer
        return '/'.join(reversed(parts))

    def __repr__(self):
        return '<Obj %s>' % (self.path or self.relative_path())


class Link:
    """An object reference in property data: written by unversioned_write as its index,
    which is known once the tables are laid out."""

    def __init__(self, obj, ctx):
        self.obj, self.ctx = obj, ctx

    @property
    def index(self):
        return self.ctx.index(self.obj)


class Context:
    """Turns objects and names into the package's indices while export data is encoded. In
    the first pass (`collect`) names are only recorded; then the name map is fixed."""

    def __init__(self, imports, exports):
        self.imports, self.exports = imports, exports
        self.collect, self.used = True, set()
        self.used_imports = set()
        self.names = []

    def index(self, obj):
        if obj is None:
            return 0
        if obj.is_import:
            self.used_imports.add(id(obj))
            return -self.imports.index(obj) - 1
        return self.exports.index(obj) + 1

    def name_ref(self, text):
        base, number = zenpackage.split_name(text)
        if self.collect:
            self.used.add(base)
            return 0, number
        return self.names.index(base), number

    def link(self, obj):
        return Link(obj, self)


# -- the description's values as unversioned properties ---------------------------------------

def is_zero(t, v, bitfield=False):
    """Whether a value is written zero-masked: all its bits are zero. A struct with a schema of
    its own (Margin) is never masked as a whole, its members are; a one-bit bitfield bool is
    written even when false."""
    kind = t[0]
    if kind == 'Bool' and bitfield:
        return False
    if kind == 'Struct':
        fmt = trace_omdd.NATIVE.get(t[1])
        return bool(fmt) and all(x == 0 for x in v)
    if kind in ('Object', 'Interface', 'WeakObject'):
        return v is None or (isinstance(v, Link) and v.obj is None)
    if kind in ('Bool', 'Byte', 'Int', 'Float', 'Double', 'UInt64', 'UInt32', 'UInt16', 'Int64',
                'Int16', 'Int8'):
        return v == 0
    if kind == 'Enum':
        return v == 0
    if kind == 'Name':
        return v == 'None'
    if kind in ('Str', 'Array', 'Set', 'Map'):
        return len(v) == 0
    return False


class Values:
    """Builds trace_omdd.Fields from description values, by the usmap's (and Blueprint
    classes') schemas."""

    def __init__(self, compiler):
        self.c = compiler

    def schema_index(self, struct_name, prop):
        schema = self.c.types.schema(struct_name)
        if schema is None:
            raise BuildError('no schema for %s' % struct_name)
        hits = sorted(i for i, (n, _t) in schema.items() if n.lower() == prop.lower())
        if not hits:
            raise BuildError('%s has no property %s' % (struct_name, prop))
        return hits[0], schema[hits[0]]

    def fields(self, struct_name, values):
        """Fields of `struct_name` holding `values` ({property: description value or a ready
        value: Link, Fields, FText})."""
        out = trace_omdd.Fields(struct_name)
        for prop, raw in values.items():
            i, (real, t) = self.schema_index(struct_name, prop)
            v = self.value(t, raw)
            out[real] = v
            out.entries.append((i, is_zero(t, v, self.bitfield(struct_name, i, real)), v))
        out.entries.sort(key=lambda x: x[0])
        return out

    def bitfield(self, struct_name, index, prop):
        """Whether schema entry `index` of a struct is a bitfield bool: by the class or struct
        that declares it (own properties first, then each super's)."""
        name, types = struct_name, self.c.types
        while name:
            count = types.count(name) - (types.count(types_super(types, name))
                                         if types_super(types, name) else 0)
            if index < count:
                return prop in self.c.db.get('bitfields', {}).get(name, ())
            index -= count
            name = types_super(types, name)
        return False

    def value(self, t, raw):
        kind = t[0]
        if isinstance(raw, (Link, trace_omdd.Fields, trace_omdd.FText)):
            return raw
        if kind == 'Bool':
            return int(bool(raw))
        if kind in trace_omdd.SIZES:
            return raw
        if kind == 'Enum':
            return self.enum(t[1], raw)
        if kind in ('Name', 'Str'):
            return str(raw)
        if kind == 'Text':
            return text_value(raw, self.c)
        if kind in ('Object', 'Interface', 'WeakObject'):
            if raw is None:
                return self.c.ctx.link(None)
            if isinstance(raw, Obj):
                return self.c.ctx.link(raw)
            return self.c.ctx.link(self.c.object_text(raw))
        if kind == 'Struct':
            fmt = trace_omdd.NATIVE.get(t[1])
            if fmt:
                return tuple(raw)
            if not isinstance(raw, dict):
                raise BuildError('%s is a mapping of its members' % t[1])
            return self.fields(t[1], raw)
        if kind == 'Array':
            return [self.value(t[1], x) for x in raw]
        raise BuildError('values of type %s are not supported in a description' % kind)

    def enum(self, ename, raw):
        if isinstance(raw, int):
            return raw
        value = str(raw).split('::')[-1]
        entry = self.c.db['enums'].get(ename)
        if not entry or value not in entry['values']:
            raise BuildError('%s has no value %s' % (ename, value))
        return entry['numbers'][entry['values'].index(value)]


def types_super(types, name):
    """The super of a class or struct in a BlueprintTypes (its own or the usmap's)."""
    if name in types.extra:
        return types.extra[name][0]
    return types.usmap.structs.get(name, (None,))[0]


def text_value(raw, compiler=None):
    """An FText: a string is culture-invariant text; {namespace, key, source} localized."""
    if isinstance(raw, str):
        return trace_omdd.FText(raw, TEXT_CULTURE_INVARIANT, -1, (1, raw))
    if compiler is not None:
        compiler.localized = True
    return trace_omdd.FText(raw['source'], raw.get('flags', 0), 0,
                            (raw['namespace'], raw['key'], raw['source']))


# -- the logic compiler's package handles (bpkit.script.Unit's `refs`) -------------------------

class Links:
    """Package handles bpkit's script compiler asks for, as Obj."""

    def __init__(self, compiler):
        self.c = compiler
        self.own_class = compiler.cls
        self.own_key = compiler.class_key
        self.functions = compiler.functions

    def own_function(self, name):
        return self.functions[name]

    def cls(self, key):
        if key == 'Class':
            return self.c.script('/Script/CoreUObject.Class', 'class')
        if key.startswith('/'):
            if key.lower() == self.own_key:
                return self.own_class
            return self.c.game_object(self.c.blueprint_path(key))
        return self.c.script('%s.%s' % (self.c.module('classes', key), key), 'class')

    def struct(self, name):
        if name.startswith('/'):
            return self.c.game_object(self.c.db['structs'][name.lower()]['path'])
        return self.c.script('%s.%s' % (self.c.module('structs', name), name), 'struct')

    def enum(self, name):
        return self.c.script('%s.%s' % (self.c.module('enums', name), name), 'enum')

    def function(self, owner, name):
        if owner.startswith('/'):
            return self.c.game_object('%s:%s' % (self.c.blueprint_path(owner), name))
        return self.c.script('%s.%s:%s' % (self.c.module('classes', owner), owner, name),
                             'delegate' if name.endswith('__DelegateSignature') else 'function')

    def package_function(self, package, name):
        """A function whose outer is a package: a native delegate's signature."""
        return self.c.script('%s.%s' % (package, name), 'delegate')

    def object(self, text):
        return self.c.object_text(text)

    def cdo(self, owner):
        if owner.startswith('/'):
            return self.c.cdo(self.c.blueprint_path(owner))
        return self.c.cdo('%s.%s' % (self.c.module('classes', owner), owner))


class FunctionCompiler5(bpscript.FunctionCompiler):
    """bpkit's statement compiler, its properties sized for this build."""

    def property(self, v):
        q = super().property(v)
        fix_sizes(q)
        return q


class Unit5(bpscript.Unit):
    def compile(self):
        return [FunctionCompiler5(self, f).compile() for f in self.functions]


def fix_sizes(q):
    """Element sizes of a bpkit Property tree for this build (ELEMENT_SIZES)."""
    if q is None:
        return
    size = ELEMENT_SIZES.get(q.type.text)
    if size:
        q.element_size = size
    for child in q.children.values():
        fix_sizes(child)


# -- the compiler ------------------------------------------------------------------------------

class Compiler:
    def __init__(self, desc, db=None, siblings=None):
        """`db` is used as given (compile_all copies it once for a build); `siblings` maps the
        package names built together to {relative path lowercased: (relative path, class)}."""
        self.d = desc
        self.db = db if db is not None else copy.deepcopy(typedb5.load())
        self.siblings = siblings or {}
        self.game = trace_omdd.game()
        self.types = bpclass5.BlueprintTypes(self.game)
        self.pkg_path = desc['path']
        if self.pkg_path.rsplit('/', 1)[1] != desc['name']:
            raise BuildError('path %s does not end in the name %s' % (self.pkg_path, desc['name']))
        self.class_name = desc['name'] + '_C'
        self.class_key = (self.pkg_path + '.' + self.class_name).lower()
        self.imports, self.exports = {}, []
        self.ctx = Context([], [])
        self.values = Values(self)
        self.localized = False
        self.functions = {}
        self.slot_counts = {}

    # imports
    def module(self, table, name):
        entry = self.db[table].get(name) or {}
        if 'module' not in entry:
            raise BuildError('no module known for %s' % name)
        return '/Script/' + entry['module']

    def script(self, path, what):
        """The import of a native object; its outers (the class of a function, the script
        package) are imported too, as a legacy import table holds them."""
        if path not in self.imports:
            self.imports[path] = Obj(script_name(path), path=path, class_name=SCRIPT_CLASS[what])
            outer = import_outer(path)
            if outer:
                self.script(outer, 'class' if '.' in outer else 'package')
        return self.imports[path]

    def cdo(self, class_path):
        """The import of a class's default object (a native or another package's class)."""
        module, short = class_path.split('.', 1)
        path = '%s.Default__%s' % (module, short)
        if not module.startswith('/Script/'):
            return self.game_object(path)
        if path not in self.imports:
            self.imports[path] = Obj('Default__' + short, path=path, class_name=short)
            self.script(module, 'package')
        return self.imports[path]

    def native_class(self, path):
        """The import of a class: a native one, or another package's Blueprint class."""
        if not path.startswith('/Script/'):
            return self.game_object(path)
        return self.script(path, 'class')

    def blueprint_path(self, key):
        """'/Game/X/Y.Y_C' of a Blueprint class's database key."""
        entry = self.db['classes'].get(key.lower())
        if not entry or 'path' not in entry:
            raise BuildError('no Blueprint class %s in the game or this build' % key)
        return entry['path']

    def resolve(self, package, relative):
        """(path inside the package as it is spelled, the object's class name) of an object
        of another package: one of the game's or one built in the same build."""
        if package.lower() in self.siblings:
            got = self.siblings[package.lower()].get(relative.lower())
            if got:
                return got
        else:
            pkg = self.game.package(package)
            if pkg is not None:
                j = pkg.by_hash.get(zenpackage.export_hash(relative))
                if j is not None:
                    exact = typedb5.export_path(pkg, j).split('.', 1)[1].replace(':', '/')
                    return exact, pkg.class_name(j)
        raise BuildError('no %s in %s' % (relative, package))

    def game_object(self, path):
        """The import of an object of another package ('/Game/X/Y.Y_C', '/Game/X/Y.Y_C:Fn'),
        with what a legacy import table holds beside it, as the shipped packages do: the
        package object (a null import), a function's class, and a Blueprint class's default
        object (873 of 873 class imports of the shipped widgets have theirs)."""
        for o in self.imports.values():
            if o.path.lower() == path.lower():
                return o
        package, rest = path.split('.', 1)
        exact, class_name = self.resolve(package, rest.replace(':', '/'))
        full = package + '.' + exact.replace('/', ':')
        o = Obj(exact.rsplit('/', 1)[-1], path=full, class_name=class_name, kind=PACKAGE,
                package=package, relative=exact)
        self.imports[full] = o
        if package not in self.imports:
            self.imports[package] = Obj(package, path=package, class_name='Package', kind=NULL)
        if ':' in full:
            self.game_object(import_outer(full))
        if class_name.endswith('GeneratedClass'):
            self.game_object('%s.Default__%s' % (package, o.name))
        return o

    def object_text(self, text):
        """An object named as a description names one: Class'/Package/Path.Object'."""
        klass, package, rest = bpwidget.parse_object_text(text)
        if not package.startswith('/Script/'):
            return self.game_object('%s.%s' % (package, ':'.join(rest)))
        kind = 'class' if klass == 'Class' else 'struct' if klass == 'ScriptStruct' else \
            'enum' if klass == 'Enum' else None
        if kind is None:
            raise BuildError('object references to %s are not supported yet' % klass)
        return self.script('%s.%s' % (package, '.'.join(rest)), kind)

    def struct_name(self, cls):
        """The schema name the property reader knows a class by; another package's Blueprint
        class is registered from its class export."""
        if cls.kind == SCRIPT:
            return cls.name
        if cls.package.lower() not in self.siblings:
            pkg = self.game.package(cls.package)
            self.types.register(pkg, pkg.by_hash[zenpackage.export_hash(cls.relative)])
        return cls.name

    # exports
    def export(self, name, cls, outer=None, template=None, super_=None, flags=0, public=False,
               encode=None):
        e = Obj(name, cls=cls, outer=outer, template=template, super_=super_, flags=flags,
                public=public, encode=encode)
        self.exports.append(e)
        return e

    def slot_name(self, slot_class):
        short = slot_class.rsplit('.', 1)[1]
        n = self.slot_counts.get(short, 0)
        self.slot_counts[short] = n + 1
        return '%s_%d' % (short, n)

    # the build
    def compile(self):
        d = self.d
        parent = d.get('parent', '/Script/UMG.UserWidget')
        parent_key = bpwidget.class_key(parent)
        parent_cls = self.native_class(parent)
        wbpgc = self.native_class('/Script/UMG.WidgetBlueprintGeneratedClass')
        self.cls = cls = self.export(self.class_name, wbpgc, None, self.cdo(wbpgc.path),
                                     parent_cls, CLASS_OBJECT_FLAGS, True)
        cdo = self.export('Default__' + self.class_name, cls, None, self.cdo(parent), None,
                          CDO_OBJECT_FLAGS, True)
        tree = None
        if 'root' in d:
            tree_cls = self.native_class('/Script/UMG.WidgetTree')
            tree = self.export('WidgetTree', tree_cls, cls, self.cdo(tree_cls.path), None,
                               TREE_OBJECT_FLAGS)
        twice = bpwidget.duplicate_names(d['root']) if 'root' in d else []
        if twice:
            raise BuildError('widget names used twice: %s' % ', '.join(twice))
        widgets = []

        def declare(w, slot_desc=None, panel=None):
            wcls = self.native_class(w['class'])
            e = self.export(w['name'], wcls, tree, self.cdo(w['class']), None, TREE_OBJECT_FLAGS)
            slot = None
            if panel is not None:
                slot_desc = dict(slot_desc or {})
                slot_class = slot_desc.pop('class', None) or self.slot_class(panel.cls.path)
                scls = self.native_class(slot_class)
                slot = self.export(slot_desc.pop('name', None) or self.slot_name(slot_class),
                                   scls, panel, self.cdo(slot_class), None, TREE_OBJECT_FLAGS)
            widgets.append((w, e, slot, panel, slot_desc))
            for child in w.get('children', ()):
                declare(child['widget'], child.get('slot'), e)

        if tree is not None:
            declare(d['root'])

        # class variables: the widgets that are variables (sorted as FNames), then the script's
        variables = []
        for w, e, _slot, _panel, _sd in widgets:
            default = self.variable_default(bpwidget.class_key(w['class']))
            w['_variable'] = bool(w.get('variable', default) or w.get('events'))
            w['_default_variable'] = default
            if w['_variable']:
                variables.append((w['name'], e.cls))
        variables.sort(key=lambda v: (zenpackage.split_name(v[0])[0].lower(),
                                      zenpackage.split_name(v[0])[1]))
        self.declare_class(parent_key, variables)
        unit, compiled = self.compile_script(parent_key, variables)
        script_vars = []
        defaults = {}
        if unit is not None:
            for name, (t, default) in unit.variables.items():
                q = bpscript.make_property(unit, name, t, bpscript.VARIABLE_FLAGS,
                                           bpscript.VARIABLE_FIELD_FLAGS)
                fix_sizes(q)
                script_vars.append((name, t, q))
                if default is not None:
                    import ast
                    value = ast.literal_eval(default)
                    if value not in (0, 0.0, False, '', None):
                        defaults[name] = value
        own_props = ([(n, 1, ('Object',)) for n, _c in variables]
                     + [(n, 1, usmap_type(t)) for n, t, _q in script_vars])
        self.types.extra[self.class_name] = (parent_key, len(own_props), own_props)
        self.types._schemas.pop(self.class_name, None)

        # widgets and slots
        for w, e, slot, panel, slot_desc in widgets:
            props = dict(w.get('properties') or {})
            children = [c for c in widgets if c[3] is e]
            if children:
                props['Slots'] = [self.ctx.link(c[2]) for c in children]
            if slot is not None:
                props['Slot'] = self.ctx.link(slot)
            if w['_variable'] != w['_default_variable']:
                props['bIsVariable'] = w['_variable']
            e.encode = self.object_encoder(self.struct_name(e.cls), props)
            if slot is not None:
                sprops = dict(slot_desc)
                sprops['Parent'] = self.ctx.link(panel)
                sprops['Content'] = self.ctx.link(e)
                slot.encode = self.object_encoder(slot.cls.path.rsplit('.', 1)[1], sprops)
        if tree is not None:
            tree.encode = self.object_encoder('WidgetTree',
                                              {'RootWidget': self.ctx.link(widgets[0][1])})

        binding = self.bindings(widgets, cls)
        # the CDO
        overrides = {f.name for f in (unit.functions if unit else [])}
        cdo_values = {n: v for n, v in defaults.items()}
        for prop, event in CDO_DEFAULTS:
            if event not in overrides:
                cdo_values[prop] = False
        if defaults:
            cdo.flags |= 0x8
        cdo.encode = self.object_encoder(self.class_name, cdo_values)

        # the class
        class_values = {}
        if tree is not None:
            class_values['WidgetTree'] = self.ctx.link(tree)
        if binding is not None:
            class_values['DynamicBindingObjects'] = [self.ctx.link(binding)]
        for k, v in (d.get('class_properties') or {}).items():
            class_values[k] = v
        order = [self.functions[f.name] for f in (unit.functions if unit else [])]
        object_cls = self.native_class('/Script/CoreUObject.Object')

        def encode_class(ctx):
            x = bpclass5.StructExport('class')
            x.super = ctx.index(parent_cls)
            x.children = [ctx.index(f) for f in reversed(order)]
            x.properties = [self.variable_property(ctx, n, c) for n, c in variables]
            x.properties += [self.property5(ctx, q) for _n, _t, q in script_vars]
            x.fields = {'func_map': [(ctx.name_ref(f.name), ctx.index(f)) for f in order],
                        'class_flags': CLASS_FLAGS, 'class_within': ctx.index(object_cls),
                        'class_config_name': ctx.name_ref(CLASS_CONFIG),
                        'class_generated_by': 0, 'interfaces': [],
                        'deprecated_force_script_order': 0, 'dummy': ctx.name_ref('None'),
                        'cooked': 1, 'class_default_object': ctx.index(cdo),
                        'tags': [(ctx.name_ref(k), (v, len(v) + 1)) for k, v in CLASS_TAGS]}
            props = self.encode_props('WidgetBlueprintGeneratedClass', class_values, ctx)
            return props + bpclass5.write_struct(x)
        cls.encode = encode_class

        # the functions
        for c in compiled:
            e = self.functions[c.name]
            e.super = c.super_function
            e.encode = self.function_encoder(c)
        return self.assemble()

    def slot_class(self, panel_path):
        """The slot class a panel makes for its children: <Panel>Slot (VerticalBoxSlot), the
        grids' GridSlot and UniformGridSlot, else PanelSlot."""
        module, short = panel_path.split('.', 1)
        for name in (short + 'Slot', short.replace('Panel', '') + 'Slot'):
            if name in self.db['classes']:
                return '%s.%s' % (module, name)
        return '/Script/UMG.PanelSlot'

    def variable_default(self, cls, db=None):
        """UWidget::bIsVariable as the widget class's constructor sets it (bpkit's table), by
        the nearest listed ancestor."""
        db = db if db is not None else self.db
        seen = set()
        while cls and cls not in seen:
            if cls in bpwidget.VARIABLE_DEFAULTS:
                return bpwidget.VARIABLE_DEFAULTS[cls]
            seen.add(cls)
            cls = (db['classes'].get(cls) or {}).get('super')
        return True

    def declare_class(self, parent_key, variables):
        """Our class in the type database, so the script can name it and its variables."""
        self.db['classes'][self.class_key] = {
            'super': parent_key, 'kind': 'class', 'source': 'build',
            'path': self.pkg_path + '.' + self.class_name,
            'generated': 'WidgetBlueprintGeneratedClass',
            'properties': {n: type_of(c) for n, c in variables}}

    def compile_script(self, parent_key, variables):
        d = self.d
        if 'script' not in d:
            return None, []
        path = os.path.join(d.get('_dir', '.'), d['script'])
        with open(path) as fh:
            source = fh.read()
        members = {n: type_of(c) for n, c in variables}
        import ast
        tree = ast.parse(source, path)
        fn_cls = self.native_class('/Script/CoreUObject.Function')
        fn_cdo = self.cdo(fn_cls.path)
        for f in tree.body:
            if isinstance(f, ast.FunctionDef):
                self.functions[f.name] = self.export(f.name, fn_cls, self.cls, fn_cdo, None,
                                                     FUNCTION_OBJECT_FLAGS, True)
        unit = Unit5(source, self.db, Links(self), parent_key, members, path)
        clash = sorted(set(unit.variables) & set(members))
        if clash:
            raise BuildError('%s: script variables share widget names: %s' % (path, clash))
        if unit.replicated or any(s[2] & 0x40 for s in unit.signatures.values()):
            raise BuildError('%s: a widget is never replicated' % path)
        return unit, unit.compile()

    def bindings(self, widgets, cls):
        """A ComponentDelegateBinding for every widget event bound to a function."""
        entries = []
        for w, _e, _s, _p, _sd in widgets:
            for delegate, function in (w.get('events') or {}).items():
                if function not in self.functions:
                    raise BuildError('%s.%s is bound to %s, which the script does not define'
                                     % (w['name'], delegate, function))
                entries.append({'ComponentPropertyName': w['name'],
                                'DelegatePropertyName': delegate,
                                'FunctionNameToBind': function})
        if not entries:
            return None
        bcls = self.native_class('/Script/Engine.ComponentDelegateBinding')
        e = self.export('ComponentDelegateBinding_0', bcls, cls, self.cdo(bcls.path), None,
                        BINDING_OBJECT_FLAGS)
        e.encode = self.object_encoder('ComponentDelegateBinding', {
            'ComponentDelegateBindings': [
                self.values.fields('BlueprintComponentDelegateBinding', x) for x in entries]})
        return e

    # encoders
    def encode_props(self, struct_name, values, ctx):
        fields = self.values.fields(struct_name, values)
        return unversioned_write.Writer(self.types, ctx.name_ref).struct(struct_name, fields)

    def object_encoder(self, struct_name, values):
        """The encoder of an object's properties. The values are read now, so every object
        they name is imported before the tables are laid out."""
        fields = self.values.fields(struct_name, values)
        return lambda ctx: unversioned_write.Writer(self.types, ctx.name_ref).struct(
            struct_name, fields) + NO_GUID

    def variable_property(self, ctx, name, cls):
        return bpclass5.Property(
            'ObjectProperty', ctx.name_ref('ObjectProperty'), name=ctx.name_ref(name),
            flags=VARIABLE_FIELD_FLAGS, array_dim=1, element_size=8,
            property_flags=VARIABLE_PROPERTY_FLAGS, rep_notify=ctx.name_ref('None'),
            refs={'property_class': ctx.index(cls)})

    def property5(self, ctx, q):
        """A bpclass5.Property of a bpkit Property."""
        if q is None:
            return bpclass5.Property('None', ctx.name_ref('None'))
        p = bpclass5.Property(q.type.text, ctx.name_ref(q.type.display()))
        p.name = ctx.name_ref(q.name.display())
        p.flags, p.array_dim, p.element_size = q.flags, q.array_dim, q.element_size
        p.property_flags, p.rep_index = q.property_flags, q.rep_index
        p.rep_notify = ctx.name_ref(q.rep_notify.display())
        p.rep_condition = q.rep_condition
        p.refs = {k: ctx.index(r.target) for k, r in q.refs.items()}
        if q.type.text == 'BoolProperty':
            p.extra = bytes(q.extra)
        elif q.type.text == 'FieldPathProperty':
            p.extra = ctx.name_ref(q.extra.display())
        p.children = {k: self.property5(ctx, c) for k, c in q.children.items()}
        return p

    def function_encoder(self, c):
        def encode(ctx):
            x = bpclass5.StructExport('function')
            x.super = ctx.index(c.super_function)
            x.properties = [self.property5(ctx, q) for q in c.properties]
            nodes = [convert_node(n, c.script.names, ctx) for n in c.script.nodes]
            x.set_nodes(nodes)
            if x.script_memory != c.script.memory_size:
                raise BuildError('%s: bpkit sizes the script %d bytes in memory, kismet5 %d'
                                 % (c.name, c.script.memory_size, x.script_memory))
            x.fields = {'function_flags': c.flags, 'event_graph_function': 0,
                        'event_graph_call_offset': 0}
            if c.flags & bpclass5.FUNC_NET:
                x.fields['rep_offset'] = 0
            props = unversioned_write.Writer(self.types, ctx.name_ref).struct(
                'Function', trace_omdd.Fields('Function'))
            return props + bpclass5.write_struct(x)
        return encode

    # layout
    def assemble(self):
        imports = sorted(self.imports.values(),
                         key=lambda o: (o.class_name.lower(), o.path.lower()))
        exports = sorted(self.exports, key=lambda o: (o.cls.name.lower(),
                                                      o.relative_path().lower()))
        ctx = self.ctx
        ctx.imports[:], ctx.exports[:] = imports, exports
        for e in exports:
            e.encode(ctx)
            for o in (e.cls, e.template, e.super, e.outer):
                if o is not None:
                    ctx.index(o)
        # only what is referenced (the logic compiler names functions it calls by name), and
        # the script package of each
        keep = set()
        for o in imports:
            path = o.path if id(o) in ctx.used_imports else None
            while path:
                keep.add(path)
                if self.imports[path].class_name.endswith('GeneratedClass') and \
                        self.imports[path].kind == PACKAGE:
                    keep.add(path.rsplit('.', 1)[0] + '.Default__' + self.imports[path].name)
                path = import_outer(path)
        imports = [o for o in imports if o.path in keep]
        ctx.imports[:] = imports
        used = sorted(ctx.used, key=str.lower)
        names = list(used)
        for e in exports:
            base = zenpackage.split_name(e.name)[0]
            if base not in names:
                names.append(base)
        names.append(self.pkg_path)
        ctx.names, ctx.collect = names, False
        z = new_package(self.pkg_path, names)
        if self.localized:
            z.flags |= PKG_REQUIRES_LOCALIZATION_GATHER
        packages = sorted({o.package for o in imports if o.kind == PACKAGE},
                          key=zenpackage.package_id)
        z.imported_packages = [(p, 0) for p in packages]
        for o in imports:
            if o.kind == SCRIPT:
                o.value = zenpackage.script_import(o.path)
            elif o.kind == NULL:
                o.value = zenpackage.NULL_INDEX
            else:
                h = zenpackage.export_hash(o.relative)
                if h not in z.imported_hashes:
                    z.imported_hashes.append(h)
                o.value = (zenpackage.KIND_PACKAGE << 62 | packages.index(o.package) << 32
                           | z.imported_hashes.index(h))
        z.imports = [o.value for o in imports]
        self.final_imports = imports
        at = 0
        for e in exports:
            data = e.encode(ctx)
            ni, number = ctx.name_ref(e.name)
            z.exports.append(zenpackage.Export(
                name=ni, number=number, outer=object_index(ctx, e.outer),
                cls=object_index(ctx, e.cls), super=object_index(ctx, e.super),
                template=object_index(ctx, e.template), flags=e.flags,
                hash=zenpackage.export_hash(e.relative_path()) if e.public else 0,
                data=data, offset=at))
            at += len(data)
        return z

    def finish(self, z):
        """The dependency bundles, the export bundle and the header size, once every package of
        the build can be read (an import's outers come from the package it names)."""
        view = trace_omdd.Package(self.game, z.name, None, None, z.to_bytes())
        deps = dependencies(view, z, bpclass5.BlueprintTypes(self.game))
        z.dep_headers, z.dep_entries = [], []
        for lists in deps:
            z.dep_headers.append((len(z.dep_entries), *[len(x) for x in lists]))
            for x in lists:
                z.dep_entries += x
        z.bundle = bundle_order(z, [view.class_name(k) for k in range(len(z.exports))])
        z.cooked_header_size = legacy_header_size(z, self.final_imports, self.db)
        return z


def import_outer(path):
    """The outer of a script object path: '/Script/UMG.TextBlock:SetText' ->
    '/Script/UMG.TextBlock' -> '/Script/UMG' -> None."""
    if ':' in path:
        return path.rsplit(':', 1)[0]
    if '.' in path:
        return path.split('.', 1)[0]
    return None


def script_name(path):
    """The object name at the end of a script path ('/Script/UMG.TextBlock:SetText')."""
    return path.replace(':', '.').rsplit('.', 1)[-1] if '.' in path else path


def object_index(ctx, obj):
    """The FPackageObjectIndex of an Obj in the export map's form."""
    if obj is None:
        return zenpackage.NULL_INDEX
    if obj.is_import:
        return obj.value
    return ctx.exports.index(obj)


def type_of(cls):
    """The canonical object type of a class import: a native's name, a Blueprint's key."""
    return 'object:' + (cls.name if cls.kind == SCRIPT else cls.path.lower())


def usmap_type(t):
    """The usmap type tuple of a canonical type (bpkit.typedb), for the class's own schema."""
    k = bpscript.kind_of(t)
    simple = {'bool': 'Bool', 'int': 'Int', 'int64': 'Int64', 'byte': 'Byte', 'float': 'Float',
              'double': 'Double', 'name': 'Name', 'str': 'Str', 'text': 'Text',
              'object': 'Object', 'class': 'Object', 'softobject': 'SoftObject',
              'softclass': 'SoftObject', 'weakobject': 'WeakObject', 'interface': 'Interface'}
    if k in simple:
        return (simple[k],)
    if k == 'struct':
        return ('Struct', bpscript.arg_of(t))
    if k == 'enum':
        return ('Enum', bpscript.arg_of(t), ('Byte',))
    if k == 'array':
        return ('Array', usmap_type(t[6:-1]))
    raise BuildError('class variables of type %s are not supported yet' % t)


def convert_node(n, names, ctx):
    """A kismet5 Node of a bpkit script node: object handles become package indices, names
    the package's names, a field path (pathlen, pname..., owner) one operand."""
    out = kismet5.Node(n.op)
    ops = list(n.ops)
    i = 0
    while i < len(ops):
        kind, v = ops[i]
        if kind == 'expr':
            out.ops.append(('expr', convert_node(v, names, ctx)))
        elif kind == 'obj':
            out.ops.append(('obj', ctx.index(v)))
        elif kind == 'name':
            out.ops.append(('name', ctx.name_ref(bpname_text(names, v))))
        elif kind == 'pathlen':
            path = [ctx.name_ref(bpname_text(names, ops[i + 1 + k][1])) for k in range(v)]
            owner = ops[i + 1 + v]
            if owner[0] != 'owner':
                raise BuildError('a field path without its owner')
            out.ops.append(('field', (path, ctx.index(owner[1]))))
            i += v + 1
        elif kind == 'cstr':
            out.ops.append(('str', v))
        else:
            out.ops.append((kind, v))
        i += 1
    return out


def bpname_text(names, v):
    index, number = v
    return zenpackage.join_name(names[index], number)


def new_package(name, names):
    """An empty ZenPackage named `name` with the given name map."""
    z = zenpackage.ZenPackage.__new__(zenpackage.ZenPackage)
    z.names = list(names)
    z.name_index, z.name_number = z.names.index(name), 0
    z.flags, z.cooked_header_size = PACKAGE_FLAGS, 0
    z.bulk, z.imported_hashes, z.imports, z.exports = [], [], [], []
    z.bundle, z.dep_headers, z.dep_entries, z.imported_packages = [], [], [], []
    z.header_tail, z.tail = b'', PACKAGE_TAG
    return z


# -- dependency bundles and the export bundle ------------------------------------------------

def value_refs(v, out):
    """Object references (FPackageIndex) of a raw property value, in serialized order."""
    if isinstance(v, trace_omdd.Ref):
        out.append(v.index)
    elif isinstance(v, trace_omdd.Fields):
        for _i, zero, x in v.entries:
            if not zero:
                value_refs(x, out)
    elif isinstance(v, trace_omdd.Removed):
        for x in v.removed:
            value_refs(x, out)
        for x in v:
            value_refs(x, out)
    elif isinstance(v, (list, tuple)):
        for x in v:
            value_refs(x, out)


def struct_refs(x, out):
    """Object references of a bpclass5.StructExport, in serialized order."""
    out.append(x.super)
    out += x.children

    def prop(q):
        for key in bpclass5.REFS.get(q.kind, ()):
            out.append(q.refs[key])
        for key in bpclass5.CHILDREN.get(q.kind, ()):
            if q.children[key].kind != 'None':
                prop(q.children[key])
    for q in x.properties:
        prop(q)
    if x.script:
        for top in x.nodes():
            for node in top.walk():
                for kind, v in node.ops:
                    if kind == 'obj':
                        out.append(v)
                    elif kind == 'field':
                        out.append(v[1])
    f = x.fields
    if 'function_flags' in f:
        out.append(f['event_graph_function'])
    if 'func_map' in f:
        out += [r for _n, r in f['func_map']]
        out += [f['class_within'], f['class_generated_by']]
        out += [i[0] for i in f['interfaces']]
        out.append(f['class_default_object'])


def export_refs(view, k, types):
    """Every object reference of export k of a trace_omdd.Package, in serialized order."""
    e = view.exports[k]
    cname = types.register_class_of(view, k)
    r = trace_omdd.Props(view, view.data, e['at'], e['at'] + e['size'], raw=True)
    r.types = types
    out = []
    value_refs(r.struct(cname), out)
    kind = bpclass5.kind_of_class(view.class_name(k))
    if kind:
        struct_refs(bpclass5.read_struct(view.blob(k), r.o - e['at'], kind, view.names), out)
    return out


def import_outers(view, z, v):
    """The imports that are outers of package import `v` (FPackageIndex), outermost first."""
    out = []
    raw = z.imports[-v - 1]
    while raw != zenpackage.NULL_INDEX and raw >> 62 == zenpackage.KIND_PACKAGE:
        pi = (raw & 0x3FFFFFFFFFFFFFFF) >> 32
        got = view.import_target(-v - 1)
        if not got or got[0] == 'script':
            break
        other, j = got
        outer = other.exports[j]['outer']
        if outer >> 62 != 0 or other.exports[outer]['hash'] not in z.imported_hashes:
            break
        want = zenpackage.KIND_PACKAGE << 62 | pi << 32 | \
            z.imported_hashes.index(other.exports[outer]['hash'])
        if want not in z.imports:
            break
        v = -z.imports.index(want) - 1
        out.insert(0, v)
        raw = want
    return out


def dependencies(view, z, types):
    """[(create before create, serialize before create, create before serialize, serialize
    before serialize)] per export, FPackageIndex lists. Rules (measured, see the docstring):

    - create before create: the outer, then the super struct (exports or package imports);
    - serialize before create: the class, then the template;
    - serialize before serialize: a function's super struct; a class's super, its super's
      default object, then its ubergraph function;
    - create before serialize: every other export or package import the export's data
      references, in serialized order, each package import preceded by its import outers; a
      class leaves out its default object, a default object starts with its class's super.
    Script imports and null imports are never listed."""
    cdo_of = {e.cls: k for k, e in enumerate(z.exports) if e.flags & RF_CLASS_DEFAULT_OBJECT
              and e.cls >> 62 == zenpackage.KIND_EXPORT}

    def ref(v):
        if v == zenpackage.NULL_INDEX:
            return None
        if v >> 62 == zenpackage.KIND_EXPORT:
            return v + 1
        if v >> 62 == zenpackage.KIND_PACKAGE:
            return -z.imports.index(v) - 1
        return None

    out = []
    for k, e in enumerate(z.exports):
        cbc, sbc, cbs, sbs = [], [], [], []
        kind = bpclass5.kind_of_class(view.class_name(k))
        for v in (e.outer, e.super):
            if ref(v) and ref(v) not in cbc:
                cbc.append(ref(v))
        for v in (e.cls, e.template):
            if ref(v) and ref(v) not in sbc:
                sbc.append(ref(v))
        if kind in ('function', 'sparse') and ref(e.super):
            sbs.append(ref(e.super))
        if kind and kind.startswith('class'):
            if ref(e.super):
                sbs.append(ref(e.super))
                cdo = cdo_of.get(k)
                if cdo is not None and ref(z.exports[cdo].template):
                    sbs.append(ref(z.exports[cdo].template))
            fields, _end = view.props_end(k, raw=True)
            ug = fields.get('UberGraphFunction')
            if ug is not None and ug.index > 0:
                sbs.append(ug.index)
        refs = export_refs(view, k, types)
        if e.flags & RF_CLASS_DEFAULT_OBJECT and e.cls >> 62 == zenpackage.KIND_EXPORT:
            sup = ref(z.exports[e.cls].super)
            if sup:
                refs = [sup] + refs
        skip = {cdo_of[k] + 1} if kind and kind.startswith('class') and k in cdo_of else set()
        expanded = []
        for v in refs:
            if v < 0:
                expanded += import_outers(view, z, v)
            expanded.append(v)
        for v in expanded:
            if v == 0 or v in skip or v in cbc or v in sbc or v in cbs or v in sbs:
                continue
            if v < 0 and z.imports[-v - 1] >> 62 != zenpackage.KIND_PACKAGE:
                continue
            cbs.append(v)
        out.append((cbc, sbc, cbs, sbs))
    return out


def dependency_edges(z):
    """[(before, after)] of (export, command) nodes from the dependency bundles."""
    edges = []
    for k, (first, *counts) in enumerate(z.dep_headers):
        lists, at = [], first
        for n in counts:
            lists.append(z.dep_entries[at:at + n])
            at += n
        cbc, sbc, cbs, sbs = lists
        edges.append(((k, zenpackage.CREATE), (k, zenpackage.SERIALIZE)))
        edges += [((v - 1, zenpackage.CREATE), (k, zenpackage.CREATE)) for v in cbc if v > 0]
        edges += [((v - 1, zenpackage.SERIALIZE), (k, zenpackage.CREATE)) for v in sbc if v > 0]
        edges += [((v - 1, zenpackage.CREATE), (k, zenpackage.SERIALIZE)) for v in cbs
                  if v > 0 and v - 1 != k]
        edges += [((v - 1, zenpackage.SERIALIZE), (k, zenpackage.SERIALIZE)) for v in sbs if v > 0]
    return edges


def valid_order(z, order):
    pos = {x: i for i, x in enumerate(order)}
    return len(pos) == 2 * len(z.exports) and all(pos[a] < pos[b]
                                                  for a, b in dependency_edges(z))


def bundle_order(z, class_names):
    """The export bundle: (export, command) in load order. As 212 of the 295 shipped widget
    packages order it: the class created; its functions created, then serialized; the other
    exports the class serializes against created; the class serialized; its default object
    created and serialized; then every other export created, then serialized, each in export
    order. When that breaks a dependency, a depth-first order over the dependencies."""
    n = len(z.exports)
    out, done = [], set()

    def emit(k, c):
        if (k, c) not in done:
            done.add((k, c))
            out.append((k, c))

    classes = [k for k in range(n) if class_names[k] and class_names[k].endswith('GeneratedClass')]
    if classes:
        cls = classes[0]
        emit(cls, zenpackage.CREATE)
        funcs = [k for k in range(n) if z.exports[k].outer == cls and class_names[k] == 'Function']
        for k in funcs:
            emit(k, zenpackage.CREATE)
        for k in funcs:
            emit(k, zenpackage.SERIALIZE)
        first, *counts = z.dep_headers[cls]
        cbs_sbs = z.dep_entries[first + counts[0] + counts[1]:first + sum(counts)]
        for k in sorted(v - 1 for v in cbs_sbs if v > 0):
            emit(k, zenpackage.CREATE)
        emit(cls, zenpackage.SERIALIZE)
        cdo = next((k for k in range(n) if z.exports[k].flags & RF_CLASS_DEFAULT_OBJECT
                    and z.exports[k].cls == cls), None)
        if cdo is not None:
            emit(cdo, zenpackage.CREATE)
            emit(cdo, zenpackage.SERIALIZE)
    for k in range(n):
        emit(k, zenpackage.CREATE)
    for k in range(n):
        emit(k, zenpackage.SERIALIZE)
    if valid_order(z, out):
        return out
    preds = {}
    for a, b in dependency_edges(z):
        preds.setdefault(b, []).append(a)
    out, done = [], set()

    def visit(x):
        if x in done:
            return
        done.add(x)
        for y in preds.get(x, ()):
            visit(y)
        out.append(x)
    for k in range(n):
        visit((k, zenpackage.CREATE))
        visit((k, zenpackage.SERIALIZE))
    return out


def legacy_header_size(z, imports, db):
    """CookedHeaderSize: the size of the package's header in the legacy format, as retoc's
    to-legacy writes it (checked by the P2 gate on the probe): a 212-byte summary and the
    package name, the name map (each name an FString and 4 hash bytes) holding the Zen names
    plus every import's object name, class name and class package, 32 bytes an import, 104 an
    export, a 4-byte depends list per export, a 4-byte asset registry count and 4 bytes a
    preload dependency. UE's own value is larger (its legacy name map keeps names a Zen package
    drops: 8 to 194 bytes more on 8 native-only shipped widgets); the game uses the value only
    to place inline bulk data, which a widget Blueprint has none of."""
    names = set(z.names)
    for o in imports:
        names.add(zenpackage.split_name(o.name)[0])
        names.add(o.class_name)
        names.add(class_package(o, db))
    name_bytes = sum(4 + len(n.encode('utf-8')) + 1 + 4 for n in names)
    return (212 + 4 + len(z.name) + 1 + name_bytes + 32 * len(z.imports)
            + (104 + 4) * len(z.exports) + 4 + 4 * len(z.dep_entries))


def class_package(o, db):
    """The package of an import's class: /Script/CoreUObject for classes, functions, structs,
    enums and packages; a native class's module; a Blueprint default object's own package."""
    if o.class_name in SCRIPT_CLASS.values():
        return '/Script/CoreUObject'
    if o.kind == SCRIPT and o.name.startswith('Default__'):
        return o.path.split('.', 1)[0]
    entry = db['classes'].get(o.class_name) or {}
    if 'module' in entry:
        return '/Script/' + entry['module']
    return o.path.split('.', 1)[0]


# -- checks --------------------------------------------------------------------------------

def verify(data, db=None):
    """Reasons a built package must not ship ([] when sound), found by reading it back with
    the readers (trace_omdd, zenpackage, bpclass5, kismet5), sharing no code with the compiler:
    the chunk round-trips; every export's properties read and re-encode, every struct export
    re-serializes and its script walks with the stated sizes; every object reference is in its
    table and every import resolves to a native object; every field path's owner resolves and
    has the property; every called function exists; every delegate binding names a variable
    and a function of the class; the export bundle keeps every dependency."""
    db = db or typedb5.load()
    game = trace_omdd.game()
    bad = []
    z = zenpackage.ZenPackage(data)
    if z.to_bytes() != bytes(data):
        bad.append('the package does not survive its own round trip')
    view = trace_omdd.Package(game, z.name, None, None, data)
    types = bpclass5.BlueprintTypes(game)
    paths = typedb5.script_paths(game)
    resolver = typedb5.Resolver(game, paths, view)
    text = bpclass5.Cursor(b'', 0, view.names).text
    for i, v in enumerate(z.imports):
        if v >> 62 == zenpackage.KIND_SCRIPT and v not in paths:
            bad.append('import %016x is no native object of the game' % v)
        elif v >> 62 == zenpackage.KIND_PACKAGE and not view.import_target(i):
            bad.append('import %d (%016x) names no object of its package' % (i, v))
    structs = {}
    for k, e in enumerate(view.exports):
        label = 'export %d %s' % (k, e['name'])
        try:
            cname = types.register_class_of(view, k)
            r = trace_omdd.Props(view, view.data, e['at'], e['at'] + e['size'], raw=True)
            r.types = types
            fields = r.struct(cname)
            again = unversioned_write.Writer(types).struct(cname, fields)
            if again != view.data[e['at']:r.o]:
                bad.append('%s: its properties do not re-encode' % label)
        except (trace_omdd.PropError, bpclass5.FormatError, IndexError, KeyError,
                ValueError) as ex:
            bad.append('%s: properties do not read: %s' % (label, ex))
            continue
        kind = bpclass5.kind_of_class(view.class_name(k))
        rest = view.blob(k)[r.o - e['at']:]
        if not kind:
            if rest != NO_GUID:
                bad.append('%s: %d unexpected bytes after its properties' % (label, len(rest)))
            continue
        try:
            x = bpclass5.read_struct(view.blob(k), r.o - e['at'], kind, view.names)
        except bpclass5.FormatError as ex:
            bad.append('%s: %s' % (label, ex))
            continue
        if bpclass5.write_struct(x) != rest:
            bad.append('%s: does not re-serialize' % label)
        structs[k] = (x, fields)
        refs = []
        struct_refs(x, refs)
        for v in refs:
            if not -len(z.imports) <= v <= len(z.exports):
                bad.append('%s: reference %d is outside the tables' % (label, v))
        if x.script:
            try:
                nodes = x.nodes()
            except (kismet5.BytecodeError, bpclass5.FormatError) as ex:
                bad.append('%s: the script does not walk: %s' % (label, ex))
                continue
            if kismet5.sizes(nodes)[1] != x.script_memory or nodes[-1].name != 'EndOfScript':
                bad.append('%s: script sizes or end are wrong' % label)
            bad += check_script(view, z, k, x, nodes, resolver, db, text, label)
    bad += check_bindings(view, structs, text)
    if not valid_order(z, [tuple(b) for b in z.bundle]):
        bad.append('the export bundle breaks a dependency')
    return bad


def owner_properties(view, ref, db, resolver):
    """Lowercased property names an FFieldPath owner declares, or None when unknown."""
    if ref > 0:
        k = ref - 1
        kind = bpclass5.kind_of_class(view.class_name(k))
        if not kind:
            return None
        e = view.exports[k]
        _f, end = view.props_end(k, raw=True)
        x = bpclass5.read_struct(view.blob(k), end - e['at'], kind, view.names)
        text = bpclass5.Cursor(b'', 0, view.names).text
        return {text(p.name).lower() for p in x.properties}
    key = resolver.key(ref)
    entry = db['classes'].get(key) or db['structs'].get(key)
    if entry is None:
        return None
    names, seen = set(), set()
    while entry and key not in seen:
        seen.add(key)
        names |= {n.lower() for n in entry.get('properties', {})}
        key = entry.get('super')
        entry = db['classes'].get(key) or db['structs'].get(key)
    return names


def check_script(view, z, k, x, nodes, resolver, db, text, label):
    bad = []
    for top in nodes:
        for node in top.walk():
            for kind, v in node.ops:
                if kind == 'field':
                    path, owner = v
                    if not path:
                        continue
                    if owner == 0:
                        bad.append('%s: field %s has no owner' % (label, text(path[0])))
                        continue
                    have = owner_properties(view, owner, db, resolver)
                    if have is None:
                        bad.append('%s: owner of %s is unknown' % (label, text(path[0])))
                    elif text(path[0]).lower() not in have:
                        bad.append('%s: %s is not a property of its owner' % (label,
                                                                             text(path[0])))
                elif kind == 'obj' and v == 0:
                    bad.append('%s: %s refers to nothing' % (label, node.name))
            if node.name in typedb5.CALL_OPS:
                target = node.ops[0][1]
                if target < 0:
                    key = resolver.key(target)
                    if key not in db['functions']:
                        bad.append('%s: calls %s, which the game does not have' % (label, key))
            elif node.name in typedb5.BY_NAME_OPS:
                pass                # a call by name is resolved at run time on the object
    return bad


def check_bindings(view, structs, text):
    """Every ComponentDelegateBinding entry names a class variable and a class function."""
    bad = []
    classes = {k: x for k, (x, _f) in structs.items() if x.kind.startswith('class')}
    for k in range(len(view.exports)):
        if view.class_name(k) != 'ComponentDelegateBinding':
            continue
        outer = view.exports[k]['outer']
        x = classes.get(outer)
        if x is None:
            bad.append('a delegate binding outside the class')
            continue
        variables = {text(p.name) for p in x.properties}
        functions = {text(n) for n, _r in x.fields['func_map']}
        for entry in view.props(k).get('ComponentDelegateBindings', []):
            if entry.get('ComponentPropertyName') not in variables:
                bad.append('binding names %s, no variable of the class'
                           % entry.get('ComponentPropertyName'))
            if entry.get('FunctionNameToBind') not in functions:
                bad.append('binding calls %s, no function of the class'
                           % entry.get('FunctionNameToBind'))
    return bad


# -- entry points --------------------------------------------------------------------------

def load(path):
    import yaml
    with open(path) as fh:
        d = yaml.safe_load(fh)
    d['_dir'] = os.path.dirname(os.path.abspath(path))
    return d


def compile_all(descs, db=None):
    """ZenPackages of descriptions built together: each may name the others' classes (their
    class skeletons, widget variables included, are declared first). The packages are made
    readable through trace_omdd.game() so their imports of each other resolve."""
    db = copy.deepcopy(db or typedb5.load())
    siblings = {}
    for d in descs:
        declare(db, d)
        name = d['name'] + '_C'
        siblings[d['path'].lower()] = {
            name.lower(): (name, 'WidgetBlueprintGeneratedClass'),
            ('Default__' + name).lower(): ('Default__' + name, name)}
    compilers = [Compiler(d, db, siblings) for d in descs]
    packages = [c.compile() for c in compilers]
    game = trace_omdd.game()
    for z in packages:
        register(game, z)
    for c, z in zip(compilers, packages):
        c.finish(z)
        register(game, z)
    compile_all.db = db
    return packages


def compile(desc, db=None):
    return compile_all([desc], db)[0]


def declare(db, d):
    """A description's class skeleton in the type database: path, parent, widget variables."""
    parent = d.get('parent', '/Script/UMG.UserWidget')
    variables = {}

    def walk(w):
        cls = bpwidget.class_key(w['class'])
        default = Compiler.variable_default(Compiler.__new__(Compiler), cls, db)
        if w.get('variable', default) or w.get('events'):
            variables[w['name']] = 'object:' + cls
        for child in w.get('children', ()):
            walk(child['widget'])
    if 'root' in d:
        walk(d['root'])
    db['classes'][(d['path'] + '.' + d['name'] + '_C').lower()] = {
        'super': bpwidget.class_key(parent), 'kind': 'class', 'source': 'build',
        'path': d['path'] + '.' + d['name'] + '_C', 'generated': 'WidgetBlueprintGeneratedClass',
        'properties': variables}


def register(game, z):
    """Make a built package readable through `game` (trace_omdd.Game), as if installed."""
    game._packages[z.name.lower()] = trace_omdd.Package(game, z.name, None, None, z.to_bytes())


def write_container(utoc, packages, container_name=None):
    """A container (.utoc, .ucas, .pak) holding ZenPackages, each at OMDD/Content/<path>."""
    name = container_name or os.path.basename(utoc)[:-5]
    chunks = []
    header = iostore_write.ContainerHeader(zenpackage.container_id(name))
    for z in packages:
        pid = zenpackage.package_id(z.name)
        if not z.name.startswith('/Game/'):
            raise BuildError('only /Game packages are written: %s' % z.name)
        chunks.append(iostore_write.Chunk(iostore_write.chunk_id(pid, iostore_write.CHUNK_PACKAGE),
                                          z.to_bytes(),
                                          'OMDD/Content/%s.uasset' % z.name[len('/Game/'):]))
        header.packages[pid] = iostore_write.store_entry(z)
    return iostore_write.write_container(utoc, chunks, header)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build', help='compile descriptions into a container')
    b.add_argument('descs', nargs='+')
    b.add_argument('--out', required=True)
    b.add_argument('--container', default='RiftLordWidgets_P')
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    packages = compile_all([load(path) for path in args.descs])
    for path, z in zip(args.descs, packages):
        problems = verify(z.to_bytes(), compile_all.db)
        if problems:
            for p in problems:
                print('%s: %s' % (path, p), file=sys.stderr)
            return 1
        print('%s: %s, %d exports, %d imports' % (path, z.name, len(z.exports), len(z.imports)))
    write_container(os.path.join(args.out, args.container + '.utoc'), packages)
    print('wrote %s.{utoc,ucas,pak} in %s' % (args.container, args.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
