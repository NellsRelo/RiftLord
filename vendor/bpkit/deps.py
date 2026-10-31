"""Preload dependencies, derived the way SavePackage.cpp derives them.

A cooked export lists the objects the event-driven loader must have ready
before it creates or serialises that export, in four sets. A package with a
missing entry loads objects out of order and crashes; one with an extra entry
can deadlock. This module computes all four from the package's tables and the
order of the object references in each export's payload, following the cooker
(SavePackage.cpp, "PreloadDependencies"):

    serialization_before_create   the export's class; its template; the template's
                                  default subobjects
    serialization_before_serialization
                                  what GetPreloadDependencies returns, if in the
                                  tables; the outer of a CDO/archetype not
                                  directly in the package; for a class, the
                                  archetypes of its CDO's subobjects
    create_before_serialization   every object the export references while it
                                  is serialised (its payload, then its class,
                                  then its template) -- and, on first meeting
                                  an import, that import's outers -- non-native
                                  ones first; never the class's own CDO
    create_before_create          the export's outer and its super

Nothing in /Script/CoreUObject is ever a dependency, nor is a top-level
package. When written, a serialization-before-serialization entry already in
serialization-before-create is dropped, and a create-before-serialization entry
already in any other set is dropped. Each set keeps insertion order.

GetPreloadDependencies (Obj.cpp, Class.cpp, Property*.cpp,
BlueprintGeneratedClass.cpp):

    UObject     its class, and that class's CDO (for a CDO, the class's
                GetDefaultObjectPreloadDependencies instead)
    UStruct     + SuperStruct, children that are not functions, and per property:
                StructProperty its struct, Byte/EnumProperty its enum,
                containers their inner properties'
    Blueprint class
                + the super class's CDO, the ubergraph function, each default
                subobject of the CDO (its class and archetype), the
                InheritableComponentHandler, the SimpleConstructionScript
    Blueprint CDO
                + (via the class) every SCS node of the class and its template
"""

RF_CLASS_DEFAULT_OBJECT = 0x10
RF_ARCHETYPE_OBJECT = 0x20
RF_DEFAULT_SUB_OBJECT = 0x40000

CORE = '/Script/CoreUObject'


class Tables:
    """What the derivation needs to know about every object in a package."""

    def __init__(self, package):
        self.p = package

    def outer(self, i):
        return (self.p.exports[i - 1].outer_index if i > 0
                else self.p.imports[-i - 1].outer)

    def class_name(self, i):
        if i > 0:
            return self.p.object_name(self.p.exports[i - 1].class_index) or 'Class'
        return self.p.name(self.p.imports[-i - 1].class_name)

    def outermost(self, i):
        """The package an object lives in: an import's top package, or None (this package)."""
        while i:
            o = self.outer(i)
            if i < 0 and o == 0:
                return self.p.object_name(i)
            if i > 0 and o == 0:
                return None
            i = o
        return None

    def is_core(self, i):
        return i < 0 and self.outermost(i) == CORE

    def is_native(self, i):
        top = self.outermost(i)
        return top is not None and top.startswith('/Script/')

    def is_top_package(self, i):
        return i < 0 and self.outer(i) == 0 and self.class_name(i) == 'Package'

    def import_class(self, i):
        """The import that is import i's class, if the table has it."""
        imp = self.p.imports[-i - 1]
        want = self.p.name(imp.class_name)
        package = self.p.name(imp.class_package)
        for k, other in enumerate(self.p.imports):
            if self.p.name(other.object_name) == want and \
                    self.outermost(-(k + 1)) == self.outermost(i) if not package.startswith('/Script/') \
                    else self.p.name(other.object_name) == want and self.outermost(-(k + 1)) == package:
                return -(k + 1)
        return None

    def flags(self, i):
        return self.p.exports[i - 1].object_flags if i > 0 else 0

    def children_of(self, i):
        """Objects in the tables whose outer is i."""
        out = [-(k + 1) for k, imp in enumerate(self.p.imports) if imp.outer == i]
        out += [k + 1 for k, e in enumerate(self.p.exports) if e.outer_index == i]
        return out

    def cdo_of(self, cls):
        """The class default object of a class, if it is in the tables: for an
        exported class, the export flagged CDO whose class it is; for an
        imported one, the import "Default__<name>" beside it."""
        if not cls:
            return None
        if cls > 0:
            return next((k for k, e in enumerate(self.p.exports, 1)
                         if e.class_index == cls and e.object_flags & RF_CLASS_DEFAULT_OBJECT),
                        None)
        name = 'Default__' + self.p.object_name(cls)
        outer = self.outer(cls)
        return next((-(k + 1) for k, imp in enumerate(self.p.imports)
                     if imp.outer == outer and self.p.name(imp.object_name) == name), None)


class OrderedSet:
    def __init__(self):
        self.items = []
        self.seen = set()

    def add(self, i):
        if i and i not in self.seen:
            self.seen.add(i)
            self.items.append(i)

    def __contains__(self, i):
        return i in self.seen


def preload_dependencies(package, refs, kinds, struct_deps, props=None):
    """{export index: {kind: [package index]}} for every export.

    `refs[i]` is the ordered list of object references in export i's payload.
    `kinds[i]` is 'class', 'function', 'struct' or None. `struct_deps[i]` is,
    for a struct export, the extra GetPreloadDependencies objects its UStruct
    part adds after SuperStruct (from its properties). `props[i]` is export i's
    decoded tagged properties, which the SCS walk reads.
    """
    props = props or {}
    t = Tables(package)
    out = {}
    for i, e in enumerate(package.exports, 1):
        def ok(x):
            return x and not t.is_core(x)

        sbc = OrderedSet()                                       # serialization before create
        if ok(e.class_index):
            sbc.add(e.class_index)
        template = e.template_index
        if ok(template):
            sbc.add(template)
        if template:
            for sub in t.children_of(template):
                if ok(sub):
                    sbc.add(sub)

        deps = []                                                # GetPreloadDependencies
        cls = e.class_index
        if cls and not t.is_core(cls):
            deps.append(cls)
            if e.object_flags & RF_CLASS_DEFAULT_OBJECT:
                deps.extend(class_default_object_deps(t, cls, kinds, props))
            elif e.object_flags & (RF_DEFAULT_SUB_OBJECT | RF_ARCHETYPE_OBJECT):
                # Observed on every default subobject and archetype in the game,
                # not found in the source: its class's CDO is not listed, even
                # when the package imports it.
                pass
            else:
                cdo = t.cdo_of(cls)
                if cdo:
                    deps.append(cdo)
        if kinds.get(i) in ('class', 'function', 'struct'):
            deps.append(e.super_index)
            deps.extend(struct_deps.get(i, ()))
        if kinds.get(i) == 'class':
            deps.extend(blueprint_class_deps(t, i, e))
        sbs = OrderedSet()                                       # serialization before serialization
        for d in deps:
            if ok(d):
                sbs.add(d)
        if e.object_flags & (RF_ARCHETYPE_OBJECT | RF_CLASS_DEFAULT_OBJECT):
            if e.outer_index and ok(e.outer_index):
                sbs.add(e.outer_index)
        if kinds.get(i) == 'class':
            cdo = t.cdo_of(i)
            for sub in (t.children_of(cdo) if cdo else ()):
                if sub > 0 and ok(package.exports[sub - 1].template_index):
                    sbs.add(package.exports[sub - 1].template_index)

        own_cdo = t.cdo_of(i) if kinds.get(i) == 'class' else None
        tagged = list(refs.get(i, ())) + [e.class_index]
        if e.outer_index and t.outermost(e.outer_index) is not None:
            tagged.append(e.outer_index)
        tagged.append(template)
        plain, native = OrderedSet(), OrderedSet()               # the tagger's two lists

        def tag(d):
            # FArchiveSaveTagImports::operator<<: record an object once, and on
            # first meeting an import, visit its outer too (and so on up).
            if not d or t.is_top_package(d):
                return
            target = native if t.is_native(d) else plain
            if d in target:
                return
            if d != own_cdo:
                target.add(d)
            if d < 0:
                tag(t.outer(d))
                # Observed, not found in SavePackage.cpp: the default object of
                # a Blueprint class, met as an import, brings its class too.
                if t.p.object_name(d).startswith('Default__'):
                    cls = t.import_class(d)
                    if cls and not t.is_native(cls):
                        tag(cls)
        for d in tagged:
            tag(d)
        cbs = OrderedSet()                                       # create before serialization
        # Observed, not found in the source: among native objects, class default
        # objects (the Default__ targets of library calls) come first.
        natives = ([d for d in native.items if t.p.object_name(d).startswith('Default__')] +
                   [d for d in native.items if not t.p.object_name(d).startswith('Default__')])
        for d in plain.items + natives:
            if ok(d):
                cbs.add(d)

        cbc = OrderedSet()                                       # create before create
        for d in (e.outer_index, e.super_index):
            if ok(d):
                cbc.add(d)

        out[i] = {
            'serialization_before_serialization': [d for d in sbs.items if d not in sbc],
            'create_before_serialization': [d for d in cbs.items if d not in sbc and
                                            d not in sbs and d not in cbc],
            'serialization_before_create': sbc.items,
            'create_before_create': cbc.items,
        }
    return out


def _prop(props, name):
    """A tagged property's value by name, or None."""
    for q in props or ():
        if q.name.text == name:
            return q.value
    return None


def _refs(value):
    from bpkit.build import Ref
    from bpkit.props import Array
    if isinstance(value, Ref):
        return [value.target] if value.target else []
    if isinstance(value, Array):
        return [v.target for v in value.items if isinstance(v, Ref) and v.target]
    return []


def class_default_object_deps(t, cls, kinds, props):
    """UBlueprintGeneratedClass::GetDefaultObjectPreloadDependencies, for this
    package's own class: each SCS node, depth first from the root nodes, then
    the component template it uses; then the class's ComponentTemplates and
    ComponentClassOverrides. Nodes inherited from a parent class are only
    reachable through the parent's package, which is not read here.
    """
    if cls < 0 or kinds.get(cls) != 'class':
        return []
    out = []
    for scs in _refs(_prop(props.get(cls), 'SimpleConstructionScript')):
        def walk(node):
            out.append(node)
            out.extend(_refs(_prop(props.get(node), 'ComponentTemplate')))
            for child in _refs(_prop(props.get(node), 'ChildNodes')):
                walk(child)
        for root in _refs(_prop(props.get(scs), 'RootNodes')):
            walk(root)
    out.extend(_refs(_prop(props.get(cls), 'ComponentTemplates')))
    return out


def blueprint_class_deps(t, i, e):
    """UBlueprintGeneratedClass::GetPreloadDependencies, after UStruct's part."""
    out = []
    sup = e.super_index
    sup_cdo = t.cdo_of(sup) if sup else None
    if sup_cdo:
        out.append(sup_cdo)
    for k, x in enumerate(t.p.exports, 1):
        if x.outer_index == i and t.p.object_name(k).startswith('ExecuteUbergraph_'):
            out.append(k)
    cdo = t.cdo_of(i)
    for sub in (t.children_of(cdo) if cdo else ()):
        if sub > 0 and t.flags(sub) & RF_DEFAULT_SUB_OBJECT:
            out.append(t.p.exports[sub - 1].class_index)
            out.append(t.p.exports[sub - 1].template_index)
    for k, x in enumerate(t.p.exports, 1):
        if x.outer_index == i and t.class_name(k) == 'InheritableComponentHandler':
            out.append(k)
    for k, x in enumerate(t.p.exports, 1):
        if x.outer_index == i and t.class_name(k) == 'SimpleConstructionScript':
            out.append(k)
    return out


def property_deps(prop, target):
    """FProperty::GetPreloadDependencies, recursively; `target` maps a Ref to an index."""
    out = []
    t = prop.type.text
    if t == 'StructProperty':
        out.append(target(prop.refs['struct']))
    elif t in ('ByteProperty', 'EnumProperty'):
        out.append(target(prop.refs['enum']))
    for child in prop.children.values():
        if child is not None:
            out.extend(property_deps(child, target))
    return out


def derive_preload(package):
    """Fill in every export's preload dependencies from its decoded payload.

    Decodes each export with bpkit.props or bpkit.structs to learn the order of
    its object references, then applies `preload_dependencies`. Raises if an
    export does not decode: a dependency list guessed for it could crash the
    loader.
    """
    from bpkit.build import Ref
    from bpkit.check_structs import kind_of
    from bpkit.props import decode_object, encode_object
    from bpkit.structs import decode_export, encode_export
    names = [str(n) for n in package.names]
    refs, kinds, struct_deps, tagged = {}, {}, {}, {}
    for i, e in enumerate(package.exports, 1):
        kind = kind_of(package.object_name(e.class_index) or 'Class')
        if kind:
            x = decode_export(e.data, kind[0], names, lambda v: v, kind[1])
            frags = encode_export(x)
            kinds[i] = kind[0]
            tagged[i] = x.props
            extra = []
            for c in x.children:
                cls = (package.object_name(package.exports[c.target - 1].class_index)
                       if c.target > 0 else package.name(package.imports[-c.target - 1].class_name))
                if cls != 'Function':
                    extra.append(c.target)
            for q in x.properties:
                extra.extend(property_deps(q, lambda r: r.target))
            struct_deps[i] = extra
        else:
            props, tail = decode_object(e.data, names, lambda v: v)
            frags = encode_object(props, tail)
            tagged[i] = props
        refs[i] = [f.target for f in frags if isinstance(f, Ref)]
    for i, deps in preload_dependencies(package, refs, kinds, struct_deps, tagged).items():
        package.exports[i - 1].preload = deps
    return package
