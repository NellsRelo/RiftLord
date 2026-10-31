"""Actor Blueprints, and the ModActor that UE4SS's BPModLoader spawns.

The description is a widget description without the tree:

    name: ModActor
    path: /Game/Mods/MyMod/ModActor
    parent: /Script/Engine.Actor            # the default
    script: ModActor.py
    defaults: {bCanBeDamaged: false}        # the parent class's properties

The editor gives every Actor Blueprint a construction script holding one
SceneComponent, DefaultSceneRoot; the compiler writes the same three objects
(the component template, its SCS_Node and the SimpleConstructionScript).
Everything else -- class, CDO, functions, imports, export order, preload
dependencies -- is the widget compiler's.

A child of another Blueprint can take `inherit_components: <the parent's
bpkit.package.Package>`: the CDO then gets its own copy of each of the parent
CDO's default subobjects, as the cooker writes a child Blueprint (a shipped
skin's CharacterMesh0 and the rest). Each copy is templated on the parent's
and holds the parent's properties, so the CDO waits for the parent's
subobjects to load. Without them a child made before its parent's subobjects
have loaded takes its grandparent's (a hero showing BP_Hero's Hero_RIg_SKM).

BPModLoader loads Content/Paks/LogicMods/<Mod>.pak, spawns
/Game/Mods/<Mod>/ModActor.ModActor_C on every map load, then calls the
actor's PreBeginPlay and, from its BeginPlay, PostBeginPlay: plain functions
with no parameters, if the script declares them. PostBeginPlay is called once
per installed mod, not once per actor, so a ModActor sets itself up in an
@override of ReceiveBeginPlay, which the engine calls once per actor; the
build warns about a PostBeginPlay.
"""

import hashlib

from bpkit.build import Name, Ref, split_name
from bpkit.props import Array, Prop, Struct, decode_object, encode_object
from bpkit.structs import Property, Script, StructExport, encode_export
from bpkit.widget import (CLASS_CONFIG, CLASS_OBJECT_FLAGS, PKG_REQUIRES_LOCALIZATION_GATHER,
                          VARIABLE_FIELD_FLAGS, Compiler, class_key)
from bpkit.deps import derive_preload

# -- constants the editor writes for every Actor Blueprint (measured on the
# four installed ModActors, which agree) -------------------------------------
CLASS_FLAGS = 8652820
CDO_OBJECT_FLAGS = 0x31              # whether or not it sets defaults
ROOT_OBJECT_FLAGS = 0x29             # the component template
SCS_OBJECT_FLAGS = 0x8               # SimpleConstructionScript and SCS_Node
ROOT_PROPERTY_FLAGS = 0x4000400080004
ROOT_NAME = 'DefaultSceneRoot'
DEFAULT_PARENT = '/Script/Engine.Actor'
RF_CLASS_DEFAULT_OBJECT = 0x10
RF_DEFAULT_SUB_OBJECT = 0x40000
WARNINGS = {
    'PostBeginPlay': 'BPModLoader calls PostBeginPlay once per installed mod, not once per '
                     'actor; set up in an @override of ReceiveBeginPlay instead',
}


class ActorCompiler(Compiler):
    def compile(self):
        d, b = self.d, self.b
        d.setdefault('parent', DEFAULT_PARENT)
        parent = d['parent']
        parent_cls = self.class_ref(parent)
        parent_cdo = self.class_default(parent)
        bpgc = self.native_class('/Script/Engine.BlueprintGeneratedClass')
        bpgc_cdo = self.class_default('/Script/Engine.BlueprintGeneratedClass')
        object_cls = self.native_class('/Script/CoreUObject.Object')
        scene_cls = self.native_class('/Script/Engine.SceneComponent')

        cls = b.export_object(self.class_name, bpgc, None, super=parent_cls, template=bpgc_cdo,
                              object_flags=CLASS_OBJECT_FLAGS, is_asset=1)
        cdo = b.export_object('Default__' + self.class_name, cls, None, template=parent_cdo,
                              object_flags=d.get('cdo_flags', CDO_OBJECT_FLAGS))
        self.exports = {}
        self.localized = False

        # the construction script: one SceneComponent as the root
        root = b.export_object(ROOT_NAME + '_GEN_VARIABLE', scene_cls, cls,
                               template=self.class_default('/Script/Engine.SceneComponent'),
                               object_flags=ROOT_OBJECT_FLAGS)
        scs = b.export_object('SimpleConstructionScript_0',
                              self.native_class('/Script/Engine.SimpleConstructionScript'), cls,
                              template=self.class_default('/Script/Engine.SimpleConstructionScript'),
                              object_flags=SCS_OBJECT_FLAGS)
        node = b.export_object('SCS_Node_0', self.native_class('/Script/Engine.SCS_Node'), scs,
                               template=self.class_default('/Script/Engine.SCS_Node'),
                               object_flags=SCS_OBJECT_FLAGS)
        root.data = encode_object([], b'\0\0\0\0')
        guid = hashlib.md5(('%s.%s:%s' % (d['path'], self.class_name, ROOT_NAME))
                           .encode()).digest()
        node.data = encode_object(self.ordered('SCS_Node', [
            Prop(Name('ComponentClass'), Name('ObjectProperty'), 0, {}, None, Ref(scene_cls)),
            Prop(Name('ComponentTemplate'), Name('ObjectProperty'), 0, {}, None, Ref(root)),
            Prop(Name('VariableGuid'), Name('StructProperty'), 0,
                 {'struct_name': Name('Guid'), 'struct_guid': b'\0' * 16}, None,
                 Struct('native', guid)),
            Prop(Name('InternalVariableName'), Name('NameProperty'), 0, {}, None,
                 Name(ROOT_NAME))]), b'\0\0\0\0')
        nodes = Array([Ref(node)])
        scs.data = encode_object(self.ordered('SimpleConstructionScript', [
            Prop(Name('RootNodes'), Name('ArrayProperty'), 0,
                 {'inner_type': Name('ObjectProperty')}, None, nodes),
            Prop(Name('AllNodes'), Name('ArrayProperty'), 0,
                 {'inner_type': Name('ObjectProperty')}, None, Array([Ref(node)])),
            Prop(Name('DefaultSceneRootNode'), Name('ObjectProperty'), 0, {}, None,
                 Ref(node))]), b'\0\0\0\0')

        # the script, then the CDO: the class's own variables before its parent's
        variables = [(ROOT_NAME, scene_cls)]
        functions, script_vars, overridden = self.script(d, cls, parent, variables)
        self.warnings = ['%s: %s' % (n, WARNINGS[n]) for n in sorted(overridden)
                         if n in WARNINGS]
        from bpkit.replication import actor_defaults
        own = self.props(class_key(parent), dict(d.get('defaults', {}), **actor_defaults(d)))
        if 'ReceiveTick' in self.script_overrides(d) and \
                'primaryactortick' not in {q.name.text.lower() for q in own}:
            own.append(self.value(class_key(parent), 'PrimaryActorTick', {'bCanEverTick': True}))
        own_defaults = [self.prop(n, t, None, v)
                        for n, (t, v) in getattr(self, 'script_defaults', {}).items()]
        inherited = (self.inherit_components(d['inherit_components'], parent, cdo)
                     if d.get('inherit_components') is not None else [])
        cdo.data = encode_object(own_defaults + self.ordered(class_key(parent), own) + inherited)

        # the class
        x = StructExport()
        x.kind = 'class'
        x.props = self.ordered('BlueprintGeneratedClass', [
            Prop(Name('SimpleConstructionScript'), Name('ObjectProperty'), 0, {}, None, Ref(scs))]
            + self.props('BlueprintGeneratedClass', d.get('class_properties', {}))
            + self.replication_props())
        x.guid = None
        x.super = Ref(parent_cls)
        x.children = [Ref(e) for e in reversed(functions)]
        x.properties = [self.root_property(scene_cls)] + script_vars
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
        self.variables = [ROOT_NAME]

        rest = [e for e in b.exports if e not in (cls, cdo) and e not in functions]
        rest.sort(key=lambda e: self.full_name(e).lower())
        b.exports = list(reversed(functions)) + [cls, cdo] + rest
        b.extra_names.add(self.pkg_path)
        if self.localized:
            b.summary.flags |= PKG_REQUIRES_LOCALIZATION_GATHER
        return derive_preload(b.build())

    def inherit_components(self, p, parent, cdo):
        """Our CDO's copies of the parent CDO's default subobjects (`p` the parent's package),
        their references to the parent's subobjects turned to ours. Returns the CDO properties
        that point at them."""
        b = self.b
        names = [str(n) for n in p.names]
        parent_cdo = next(i for i, e in enumerate(p.exports, 1)
                          if e.object_flags & RF_CLASS_DEFAULT_OBJECT)
        subs = [i for i, e in enumerate(p.exports, 1)
                if e.outer_index == parent_cdo and e.object_flags & RF_DEFAULT_SUB_OBJECT]
        mine = {parent_cdo: cdo}

        def mirror(i):
            if i == 0:
                return None
            if i > 0:
                if i not in mine:
                    raise ValueError('%s: a subobject refers to %s, which is not one of the '
                                     'parent CDO\'s subobjects' % (self.pkg_path, p.path(i)))
                return mine[i]
            imp = p.imports[-i - 1]
            return b.import_object(p.name(imp.class_package), p.name(imp.class_name),
                                   p.name(imp.object_name), mirror(imp.outer))

        for i in subs:
            e = p.exports[i - 1]
            klass = e.class_index
            template = b.import_object(p.path(p.imports[-klass - 1].outer), p.object_name(klass),
                                       p.object_name(i), self.class_default(parent))
            mine[i] = b.export_object(p.object_name(i), mirror(klass), cdo, template=template,
                                      object_flags=e.object_flags)
        for i in subs:
            props, tail = decode_object(p.exports[i - 1].data, names, mirror)
            mine[i].data = encode_object(props, tail)
        props, _tail = decode_object(p.exports[parent_cdo - 1].data, names, lambda i: i)
        return [Prop(q.name, q.type, q.index, q.extra, q.guid, Ref(mine[q.value.target]))
                for q in props if isinstance(q.value, Ref) and q.value.target in subs]

    def root_property(self, scene_cls):
        q = Property(Name('ObjectProperty'))
        q.name = Name(ROOT_NAME)
        q.flags = VARIABLE_FIELD_FLAGS
        q.array_dim, q.element_size = 1, 8
        q.property_flags = ROOT_PROPERTY_FLAGS
        q.rep_index = 0
        q.rep_notify = Name('None')
        q.rep_condition = 0
        q.refs = {'property_class': Ref(scene_cls)}
        return q


def compile(desc):
    c = ActorCompiler(desc)
    p = c.compile()
    p.variables = c.variables
    p.warnings = c.warnings
    return p
