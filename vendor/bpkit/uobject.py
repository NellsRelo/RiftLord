"""Object Blueprints: a Blueprint class whose parent is a plain UObject class.

The description is an actor description without the construction script:

    name: MyModule
    path: /Game/Mods/MyMod/MyModule
    parent: /Script/OMD.OMDRandomModeModule
    script: MyModule.py

The package holds the functions, the class and its CDO, as the game's own
RandomModeModule_* Blueprints do.
"""

from bpkit.build import Name, Ref, split_name
from bpkit.props import encode_object
from bpkit.structs import Script, StructExport, encode_export
from bpkit.widget import CLASS_CONFIG, CLASS_OBJECT_FLAGS, PKG_REQUIRES_LOCALIZATION_GATHER, class_key
from bpkit.actor import ActorCompiler
from bpkit.deps import derive_preload

#: Class flags of the game's RandomModeModule_* Blueprints.
CLASS_FLAGS = 264208
CDO_OBJECT_FLAGS = 0x31


class ObjectCompiler(ActorCompiler):
    def compile(self):
        d, b = self.d, self.b
        parent = d['parent']
        parent_cls = self.class_ref(parent)
        parent_cdo = self.class_default(parent)
        bpgc = self.native_class('/Script/Engine.BlueprintGeneratedClass')
        bpgc_cdo = self.class_default('/Script/Engine.BlueprintGeneratedClass')
        object_cls = self.native_class('/Script/CoreUObject.Object')

        cls = b.export_object(self.class_name, bpgc, None, super=parent_cls, template=bpgc_cdo,
                              object_flags=CLASS_OBJECT_FLAGS, is_asset=1)
        cdo = b.export_object('Default__' + self.class_name, cls, None, template=parent_cdo,
                              object_flags=d.get('cdo_flags', CDO_OBJECT_FLAGS))
        self.exports = {}
        self.localized = False

        functions, script_vars, _overridden = self.script(d, cls, parent, [])
        self.warnings = []
        own = self.props(class_key(parent), d.get('defaults', {}))
        own_defaults = [self.prop(n, t, None, v)
                        for n, (t, v) in getattr(self, 'script_defaults', {}).items()]
        cdo.data = encode_object(own_defaults + self.ordered(class_key(parent), own))

        x = StructExport()
        x.kind = 'class'
        x.props = self.ordered('BlueprintGeneratedClass',
                               self.props('BlueprintGeneratedClass', d.get('class_properties', {}))
                               + self.replication_props())
        x.guid = None
        x.super = Ref(parent_cls)
        x.children = [Ref(e) for e in reversed(functions)]
        x.properties = script_vars
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
        self.variables = []

        rest = [e for e in b.exports if e not in (cls, cdo) and e not in functions]
        rest.sort(key=lambda e: self.full_name(e).lower())
        b.exports = list(reversed(functions)) + [cls, cdo] + rest
        b.extra_names.add(self.pkg_path)
        if self.localized:
            b.summary.flags |= PKG_REQUIRES_LOCALIZATION_GATHER
        return derive_preload(b.build())


def compile(desc):
    """A cooked package for an object Blueprint description."""
    c = ObjectCompiler(desc)
    p = c.compile()
    p.variables = c.variables
    p.warnings = c.warnings
    return p
