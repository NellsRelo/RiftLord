"""The logic compiler: a Python-syntax script to Blueprint functions and bytecode.

A script holds a Blueprint class's variables and functions. It is parsed with
Python's own `ast` and compiled to what the editor's Blueprint compiler emits
(KismetCompilerVMBackend.cpp), statement for statement:

    Count: int = 0                         # a class variable, with its default

    def Add(A: int, B: int) -> int:        # a callable function
        return A + B

    @pure
    def Half(X: float) -> float:
        return X / 2.0

    @override                              # a parent's event (Construct, Tick, ...)
    def Construct():
        self.Message.SetText("Ready")

    def Clicked():                         # bound to a button in the widget's YAML
        self.Count = self.Count + 1

Types are written as names -- int, float, bool, str (FString), Name, Text,
byte, a class (TextBlock), a struct (Vector2D), an enum (ESlateVisibility) --
or list[T], ClassOf[T], SoftObject[T], SoftClass[T], or a canonical type in
quotes ("object:/Game/X/Y.Y_C").

Statements: assignment (to a local, a class variable, another object's
property), augmented assignment, if/elif/else, while, for over range(...) or
an array, break, continue, return, pass and calls. Expressions: literals,
self, variables, attribute access, calls (own functions, inherited and native
methods, any Blueprint function library by name or Library.Function, methods
on other objects), arithmetic and comparison operators (KismetMathLibrary),
and/or/not (both sides of and/or are evaluated, as in a Blueprint),
cast(Class, value), Enum.Value, Asset("/Game/X/Y.Y_C") (a Blueprint class) or
Asset("Texture2D'/Game/X/Y.Y'") (any other asset),
super().Function(...) (the parent class's version, from an override), struct literals Struct(a, b), struct members,
array index, len() and str(). A call's out parameters are passed as plain
local names, which are declared if they are new.

A script may name a Blueprint class once, at the top, and use the name as a
type, in cast() and as a class value:

    NewsModal = Blueprint("/Game/UI/MainMenu/News_UMG.News_UMG_C")

An actor or object Blueprint can replicate (never a widget):

    Score: Replicated[int]                 # CPF_Net
    Secret: Replicated[int, OwnerOnly]     # with an ELifetimeCondition
    Health: RepNotify[float]               # calls OnRep_Health, defined below
    Mode: RepNotify[int, "ModeChanged", SkipOwner]

    @server(reliable=True)                 # also @client, @multicast; unreliable
    def ServerFire(Target: Actor):         # when reliable is not given
        self.Score += 1                    # Flush, set, mark dirty, OnRep: as "Set w/ Notify"

Not supported yet: latent calls (Delay) and anything else that needs the
event graph's persistent frame, delegates as values, maps,
sets and interfaces as values. A multicast delegate can be bound and
unbound, as the editor's CreateDelegate node does:

    Entry.TextBtnEntry_Clicked.add(self.Hub, "OnMissionSelected")
"""

import ast
import struct

import kismet
from bpkit.build import Name, split_name
from bpkit.calls import opcode as call_opcode
from bpkit.structs import Property, Script

OP = {name: code for code, name in kismet.EX.items()}

# EFunctionFlags (Script.h)
FUNC_BLUEPRINT_CALLABLE = 0x4000000
FUNC_BLUEPRINT_EVENT = 0x8000000
FUNC_BLUEPRINT_PURE = 0x10000000
FUNC_PUBLIC = 0x20000
FUNC_PROTECTED = 0x80000
FUNC_PRIVATE = 0x40000
FUNC_HAS_OUT_PARMS = 0x400000
FUNC_HAS_DEFAULTS = 0x800000
FUNC_FUNC_INHERIT = 0x200 | 0x800 | 0x4000000 | 0x8000000 | 0x4 | 0x8 | 0x40000000
FUNC_STATIC = 0x2000
# EPropertyFlags (ObjectMacros.h), as the Blueprint compiler writes them
CPF_PARM, CPF_OUT, CPF_RETURN = 0x80, 0x100, 0x400
PARAM_FLAGS = 0x94                 # Parm | BlueprintVisible | BlueprintReadOnly
OUT_FLAGS = 0x180                  # Parm | OutParm
RETURN_FLAGS = 0x580               # Parm | OutParm | ReturnParm
VARIABLE_FLAGS = 0x10005           # Edit | BlueprintVisible | DisableEditOnInstance
CPF_INSTANCED_REFERENCE = 0x80000
FIELD_FLAGS = 0x1                  # RF_Public, on a function's properties
VARIABLE_FIELD_FLAGS = 0x200001    # on a class's variables
BOOL_FIELDS = bytes.fromhex('010001ff0101')   # FieldSize, ByteOffset, masks, size, native

#: Bytes each kind of property occupies in this build (FName is 12).
ELEMENT_SIZE = {'bool': 1, 'byte': 1, 'int8': 1, 'int16': 2, 'uint16': 2, 'int': 4,
                'uint32': 4, 'float': 4, 'int64': 8, 'uint64': 8, 'double': 8, 'name': 12,
                'str': 16, 'text': 24, 'object': 8, 'class': 8, 'weakobject': 8,
                'interface': 16, 'softobject': 48, 'softclass': 48, 'delegate': 20,
                'mcdelegate': 16, 'array': 16, 'set': 80, 'map': 80, 'enum': 1}
PROPERTY_CLASS = {'bool': 'BoolProperty', 'byte': 'ByteProperty', 'int8': 'Int8Property',
                  'int16': 'Int16Property', 'uint16': 'UInt16Property', 'int': 'IntProperty',
                  'uint32': 'UInt32Property', 'float': 'FloatProperty', 'int64': 'Int64Property',
                  'uint64': 'UInt64Property', 'double': 'DoubleProperty', 'name': 'NameProperty',
                  'str': 'StrProperty', 'text': 'TextProperty'}
#: Libraries searched, in order, for a function named without its class.
LIBRARIES = ('KismetMathLibrary', 'KismetSystemLibrary', 'KismetStringLibrary',
             'KismetTextLibrary', 'KismetArrayLibrary', 'GameplayStatics',
             'WidgetBlueprintLibrary', 'WidgetLayoutLibrary', 'KismetInputLibrary',
             'DataTableFunctionLibrary', 'BlueprintMapLibrary', 'BlueprintSetLibrary',
             'KismetNodeHelperLibrary', 'KismetMaterialLibrary', 'SlateBlueprintLibrary')
#: Array methods -> KismetArrayLibrary.
ARRAY_METHODS = {'append': 'Array_Add', 'clear': 'Array_Clear', 'contains': 'Array_Contains',
                 'find': 'Array_Find', 'remove_at': 'Array_Remove'}
#: Python operators -> the KismetMathLibrary function family.
BINARY = {ast.Add: 'Add', ast.Sub: 'Subtract', ast.Mult: 'Multiply', ast.Div: 'Divide',
          ast.Mod: 'Percent', ast.FloorDiv: 'Divide', ast.Pow: 'MultiplyMultiply'}
COMPARE = {ast.Lt: 'Less', ast.Gt: 'Greater', ast.LtE: 'LessEqual', ast.GtE: 'GreaterEqual',
           ast.Eq: 'EqualEqual', ast.NotEq: 'NotEqual'}
#: Type -> the suffix KismetMathLibrary uses for it.
SUFFIX = {'int': 'IntInt', 'float': 'FloatFloat', 'bool': 'BoolBool', 'byte': 'ByteByte',
          'name': 'NameName', 'int64': 'Int64Int64', 'str': 'StrStr', 'text': 'TextText'}


class CompileError(Exception):
    def __init__(self, message, node=None):
        where = ' (line %d)' % node.lineno if node is not None and hasattr(node, 'lineno') else ''
        super().__init__(message + where)


def N(name, *ops):
    """A bytecode node: an opcode and its operands in serialised order."""
    node = kismet.Node(OP[name], name, 0)
    node.ops = list(ops)
    return node


# -- sizes and offsets ---------------------------------------------------------

def sizes(node):
    """(serialised, in-memory) size of a node and its children.

    A pointer is 4 bytes on disk and 8 in memory; a standalone FName 8 and 12;
    a field path (count + names + owner) collapses to one 8-byte pointer.
    """
    ser = mem = 1
    for kind, val in node.ops:
        if kind == 'expr':
            s, m = sizes(val)
            ser, mem = ser + s, mem + m
        elif kind == 'cstr':
            n = len(val.encode('utf-8')) + 1
            ser, mem = ser + n, mem + n
        elif kind == 'obj':
            ser, mem = ser + 4, mem + 8
        elif kind == 'name':
            ser, mem = ser + 8, mem + 12
        elif kind == 'pathlen':
            ser, mem = ser + 4, mem + 8
        elif kind == 'pname':
            ser += 8
        elif kind == 'owner':
            ser += 4
        else:
            n = struct.calcsize(kismet.PACK[kind])
            ser, mem = ser + n, mem + n
    return ser, mem


class Label:
    """A jump target: the statement placed after it."""

    def __init__(self):
        self.offset = None


# -- types -----------------------------------------------------------------------

class Types:
    """Canonical types (see bpkit.typedb) and what the database says about them."""

    def __init__(self, db):
        self.db = db
        self.aliases = {}               # a script's Blueprint("...") names -> class path

    def entry(self, name):
        from bpkit.typedb import db_key
        k = db_key(name)
        return (self.db['classes'].get(k) or self.db['structs'].get(k)
                or self.db['usmap_only'].get(k))

    def ancestors(self, cls):
        from bpkit.typedb import db_key
        out, cls = [], db_key(cls)
        while cls and cls not in out:
            out.append(cls)
            c = self.entry(cls) or {}
            cls = db_key(c.get('super') or c.get('usmap_super'))
        return out

    def is_struct(self, name):
        from bpkit.typedb import db_key
        return db_key(name) in self.db['structs'] or name in self.db['struct_sizes']

    def is_enum(self, name):
        return name in self.db['enums']

    def is_class(self, name):
        from bpkit.typedb import db_key
        return db_key(name) in self.db['classes'] or db_key(name) in self.db['usmap_only']

    def subclass(self, a, b):
        return b.lower() in [x.lower() for x in self.ancestors(a)] if a and b else False

    def instanced(self, cls):
        """Is an object of this class referenced as an instanced subobject (a
        component or a widget), which marks its property CPF_InstancedReference?"""
        return any(c in ('ActorComponent', 'Visual') for c in self.ancestors(cls))

    def property_of(self, cls, name):
        """(declaring class, canonical type) of a property on a class or its ancestors."""
        for c in self.ancestors(cls):
            entry = self.entry(c) or {}
            for k, t in entry.get('properties', {}).items():
                if k.lower() == name.lower():
                    return c, t, k
        # a Blueprint property whose name has spaces ("Primary Mana Cost") is
        # written with underscores (Primary_Mana_Cost); only when no exact match
        if '_' in name:
            spaced = name.replace('_', ' ').lower()
            for c in self.ancestors(cls):
                entry = self.entry(c) or {}
                for k, t in entry.get('properties', {}).items():
                    if ' ' in k and k.lower() == spaced:
                        return c, t, k
        return None

    def function_of(self, cls, name):
        """(owner class, function entry) of a function on a class or its ancestors."""
        for c in self.ancestors(cls):
            fn = self.db['functions'].get('%s:%s' % (c, name)) or \
                self.db['functions'].get(('%s:%s' % (c, name)).lower())
            if fn:
                return c, fn
        return None

    def spaced_function(self, cls, name):
        """A Blueprint function whose name has spaces ("Calculate Flinch Chance") is written
        with underscores (Calculate_Flinch_Chance): the spaced name when only it exists on
        the class or its ancestors, else the name as written."""
        if '_' in name and not self.function_of(cls, name):
            spaced = name.replace('_', ' ')
            if self.function_of(cls, spaced):
                return spaced
        return name

    def library(self, name):
        """A static function of a function library, found by name."""
        for lib in LIBRARIES:
            fn = self.db['functions'].get('%s:%s' % (lib, name))
            if fn:
                return lib, fn
        return None

    def annotation(self, node):
        """A canonical type from an annotation."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Constant) and node.value is None:
            return None
        if isinstance(node, ast.Name):
            simple = {'int': 'int', 'float': 'float', 'bool': 'bool', 'str': 'str',
                      'Name': 'name', 'Text': 'text', 'byte': 'byte', 'int64': 'int64'}
            if node.id in simple:
                return simple[node.id]
            if node.id in self.aliases:
                return 'object:' + self.aliases[node.id]
            if self.is_enum(node.id):
                return 'enum:' + node.id
            if self.is_struct(node.id):
                return 'struct:' + node.id
            if self.is_class(node.id):
                return 'object:' + node.id
            raise CompileError('unknown type %s' % node.id, node)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                and node.value.id == 'dict':
            # dict[Key, Value]: a map; its key and value types as any variable's
            pair = node.slice
            if not (isinstance(pair, ast.Tuple) and len(pair.elts) == 2):
                raise CompileError('a map is dict[Key, Value]', node)
            return 'map<%s,%s>' % (self.annotation(pair.elts[0]), self.annotation(pair.elts[1]))
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            inner = self.annotation(node.slice)
            wrap = {'list': 'array<%s>', 'ClassOf': 'class:%s', 'SoftObject': 'softobject:%s',
                    'SoftClass': 'softclass:%s'}.get(node.value.id)
            if wrap is None:
                raise CompileError('unknown type %s' % node.value.id, node)
            if node.value.id == 'list':
                return wrap % inner
            return wrap % (inner.split(':', 1)[1] if ':' in inner else inner)
        raise CompileError('cannot read this type', node)


def kind_of(t):
    """The family of a canonical type: 'int', 'object', 'struct', 'array', ..."""
    if t.startswith('array<'):
        return 'array'
    if t.startswith('set<'):
        return 'set'
    if t.startswith('map<'):
        return 'map'
    return t.split(':', 1)[0]


def arg_of(t):
    return t.split(':', 1)[1] if ':' in t else None


# -- variables and expressions ---------------------------------------------------

class Var:
    """Something with a property: a parameter, a local, the return value, a member."""

    def __init__(self, name, type_, role, owner=None):
        self.name, self.type, self.role, self.owner = name, type_, role, owner
        self.declaring = None           # for a member: the class that declares it


class Expr:
    """A compiled expression: its node, its type, and the variable it is, if any."""

    def __init__(self, node, type_, var=None, literal=None):
        self.node, self.type, self.var, self.literal = node, type_, var, literal


# -- the compiler ------------------------------------------------------------------

class Unit:
    """A whole script: the class's variables and functions.

    `refs` supplies package handles: refs.cls(key), refs.struct(name),
    refs.enum(name), refs.function(owner_key, name), refs.cdo(key),
    refs.own_class, refs.own_function(name). `members` maps the class's
    variables that are not in the script (a widget's variable widgets) to
    their types. `parent` is the parent class's key.
    """

    def __init__(self, source, db, refs, parent, members=None, filename='<script>'):
        self.types = Types(db)
        self.db = db
        self.refs = refs
        self.parent = parent
        self.tree = ast.parse(source, filename)
        self.variables = {}             # name -> (type, default node)
        self.members = dict(members or {})
        self.functions = []             # ast.FunctionDef
        self.replicated = {}            # name -> (RepNotify function or None, condition)
        for item in self.tree.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                t = self.types.annotation(self.replication(item))
                self.variables[item.target.id] = (t, item.value)
                self.members[item.target.id] = t
            elif isinstance(item, ast.FunctionDef):
                self.functions.append(item)
            elif self.alias(item):
                continue
            elif isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant):
                continue                # a docstring
            else:
                raise CompileError('only variables and functions at the top of a script', item)
        for f in self.functions:
            # an override of a spaced Blueprint function takes the function's real name
            if 'override' in self.decorators(f):
                f.name = self.types.spaced_function(self.parent, f.name)
        self.notifies = {n for n, _c in self.replicated.values() if n}
        own = {f.name: f for f in self.functions}
        for var, (notify, _c) in self.replicated.items():
            if notify and notify not in own:
                raise CompileError('%s notifies %s, which the script does not define'
                                   % (var, notify))
        self.signatures = {f.name: self.signature(f) for f in self.functions}
        self._rep_index = None

    def replication(self, item):
        """A class variable's type annotation with any replication wrapper
        taken off and recorded: Replicated[T], Replicated[T, Condition],
        RepNotify[T], RepNotify[T, "OnRep_X"], RepNotify[T, "OnRep_X", Condition]."""
        a = item.annotation
        if not (isinstance(a, ast.Subscript) and isinstance(a.value, ast.Name)
                and a.value.id in ('Replicated', 'RepNotify')):
            return a
        from bpkit.replication import condition
        args = list(a.slice.elts) if isinstance(a.slice, ast.Tuple) else [a.slice]
        name = item.target.id
        notify, cond = None, 0
        rest = args[1:]
        if a.value.id == 'RepNotify':
            notify = 'OnRep_' + name
            if rest and isinstance(rest[0], ast.Constant) and isinstance(rest[0].value, str):
                notify = rest.pop(0).value
        if rest:
            c = rest.pop(0)
            if isinstance(c, ast.Constant) and c.value is None:
                c = ast.Name('None')
            if not isinstance(c, ast.Name):
                raise CompileError('a replication condition is a bare name (OwnerOnly)', item)
            try:
                cond = condition(c.id)
            except ValueError as ex:
                raise CompileError(str(ex), item) from None
        if rest:
            raise CompileError('%s[type, ...] takes at most %s' % (
                a.value.id, 'a condition' if a.value.id == 'Replicated'
                else 'a function name and a condition'), item)
        self.replicated[name] = (notify, cond)
        return args[0]

    def rep_index(self, name):
        """A replicated variable's RepIndex: the parent's ClassReps size plus
        its rank among the class's own replicated variables."""
        if self._rep_index is None:
            from bpkit.replication import replicated_count
            base = replicated_count(self.db, self.parent)
            self._rep_index = {n: base + i for i, n in enumerate(
                n for n in self.variables if n in self.replicated)}
        return self._rep_index[name]

    def rpc(self, f):
        """The net flags an @server / @client / @multicast decorator gives, 0 if none."""
        from bpkit.replication import FUNC_NET, FUNC_NET_RELIABLE, RPC_KINDS
        flags = 0
        for d in f.decorator_list:
            call = d if isinstance(d, ast.Call) else None
            name = (call.func if call else d)
            if not isinstance(name, ast.Name) or name.id.lower() not in RPC_KINDS:
                continue
            if flags:
                raise CompileError('%s: one of @server, @client, @multicast' % f.name, f)
            flags = FUNC_NET | RPC_KINDS[name.id.lower()]
            for k in (call.keywords if call else ()):
                if k.arg != 'reliable' or not isinstance(k.value, ast.Constant):
                    raise CompileError('%s: an RPC decorator takes reliable=True/False' % f.name, f)
                if k.value.value:
                    flags |= FUNC_NET_RELIABLE
            if call and call.args:
                raise CompileError('%s: write @%s(reliable=True)' % (f.name, name.id), f)
        return flags

    def alias(self, item):
        """`Name = Blueprint("/Game/X/Y.Y_C")`: names a Blueprint class for the
        rest of the script. False for anything else."""
        if not (isinstance(item, ast.Assign) and len(item.targets) == 1
                and isinstance(item.targets[0], ast.Name) and isinstance(item.value, ast.Call)
                and isinstance(item.value.func, ast.Name) and item.value.func.id == 'Blueprint'):
            return False
        args = item.value.args
        if len(args) != 1 or not isinstance(args[0], ast.Constant) \
                or not isinstance(args[0].value, str):
            raise CompileError('Blueprint takes one path, "/Game/X/Y.Y_C"', item)
        entry = self.db['classes'].get(args[0].value.lower())
        if not entry or 'generated' not in entry:
            raise CompileError('no Blueprint class %s in the game or this build'
                               % args[0].value, item)
        self.types.aliases[item.targets[0].id] = entry['path']
        return True

    def decorators(self, f):
        return {d.id for d in f.decorator_list if isinstance(d, ast.Name)}

    def signature(self, f):
        """(params [(name, type, out)], return type, flags, super function entry)."""
        decos = self.decorators(f)
        net = self.rpc(f)
        if net or f.name in self.notifies:
            # an RPC is a custom event; a RepNotify function is made by the
            # editor without an access specifier: neither is Public
            from bpkit.replication import EVENT_FLAGS
            what = 'an RPC' if net else 'a RepNotify function'
            if decos & {'override', 'pure'}:
                raise CompileError('%s: %s cannot be @override or @pure' % (f.name, what), f)
            if f.returns is not None:
                raise CompileError('%s: %s returns nothing' % (f.name, what), f)
            if not net and [a for a in f.args.args if a.arg != 'self']:
                raise CompileError('%s: a RepNotify function takes no parameters' % f.name, f)
            params = []
            for a in f.args.args:
                if a.arg == 'self':
                    continue
                if a.annotation is None:
                    raise CompileError('parameter %s needs a type' % a.arg, f)
                params.append((a.arg, self.types.annotation(a.annotation), False))
            return params, None, EVENT_FLAGS | net, None
        if 'override' in decos:
            found = self.types.function_of(self.parent, f.name)
            if not found:
                raise CompileError('%s overrides nothing on the parent class' % f.name, f)
            owner, parent_fn = found
            params = [(p['name'], p['type'], p['out']) for p in parent_fn['params']
                      if not p.get('return')]
            ret = next((p['type'] for p in parent_fn['params'] if p.get('return')), None)
            flags = parent_fn.get('function_flags', 0) & (FUNC_FUNC_INHERIT | FUNC_PUBLIC |
                                                          FUNC_PROTECTED | FUNC_PRIVATE |
                                                          FUNC_BLUEPRINT_PURE)
            return params, ret, flags, (owner, f.name)
        params = []
        for a in f.args.args:
            if a.arg == 'self':
                continue
            if a.annotation is None:
                raise CompileError('parameter %s needs a type' % a.arg, f)
            params.append((a.arg, self.types.annotation(a.annotation), False))
        ret = self.types.annotation(f.returns) if f.returns is not None else None
        flags = FUNC_BLUEPRINT_CALLABLE | FUNC_BLUEPRINT_EVENT | FUNC_PUBLIC
        if 'pure' in decos:
            flags |= FUNC_BLUEPRINT_PURE
        return params, ret, flags, None

    def compile(self):
        """[CompiledFunction] for every function in the script."""
        return [FunctionCompiler(self, f).compile() for f in self.functions]


class CompiledFunction:
    def __init__(self, name, flags, properties, script, super_function):
        self.name, self.flags, self.properties = name, flags, properties
        self.script, self.super_function = script, super_function


class FunctionCompiler:
    def __init__(self, unit, f):
        self.u, self.f = unit, f
        self.t = unit.types
        self.names, self.name_index = [], {}
        self.vars = {}                  # local name -> Var
        self.order = []                 # params, return, then locals, as properties
        self.statements = []
        self.loops = []                 # (continue label, break label)
        self.temps = {}
        params, ret, flags, self.super = unit.signatures[f.name]
        self.flags = flags
        for name, t, out in params:
            v = self.declare(name, t, 'out' if out else 'param')
            if ' ' in name:
                # a spaced parameter of an overridden Blueprint function, by its underscore spelling
                self.vars.setdefault(name.replace(' ', '_'), v)
        self.ret = self.declare('ReturnValue', ret, 'return') if ret else None
        self.end = Label()

    # names and variables
    def name(self, text):
        entry, number = split_name(text)
        if entry not in self.name_index:
            self.name_index[entry] = len(self.names)
            self.names.append(entry)
        return (self.name_index[entry], number)

    def declare(self, name, t, role):
        if name in self.vars:
            return self.vars[name]
        v = Var(name, t, role, owner=self.u.refs.own_function(self.f.name))
        self.vars[name] = v
        self.order.append(v)
        return v

    def temp(self, stem, t):
        n = self.temps.get(stem, 0)
        self.temps[stem] = n + 1
        return self.declare(stem if n == 0 else '%s_%d' % (stem, n), t, 'local')

    def path(self, var):
        """The FFieldPath operands naming a variable's property."""
        if var is None:
            return [('pathlen', 0), ('owner', None)]
        owner = var.owner if var.role != 'member' else var.declaring
        return [('pathlen', 1), ('pname', self.name(var.name)), ('owner', owner)]

    def var_expr(self, var):
        if var.role == 'member':
            op = 'InstanceVariable'
        elif var.role in ('out', 'return'):
            op = 'LocalOutVariable'
        else:
            op = 'LocalVariable'
        return Expr(N(op, *self.path(var)), var.type, var)

    def member(self, name, node=None):
        """A variable of this class (script or widget) or an inherited one."""
        if name in self.u.members:
            v = Var(name, self.u.members[name], 'member')
            v.declaring = self.u.refs.own_class
            return v
        found = self.t.property_of(self.u.parent, name)
        if found:
            cls, t, real = found
            v = Var(real, t, 'member')
            v.declaring = self.u.refs.cls(cls)
            return v
        return None

    # statements
    def emit(self, node):
        self.statements.append(node)

    def compile(self):
        for stmt in self.f.body:
            self.statement(stmt)
        self.statements.append(self.end)
        self.emit(N('Return', ('expr', self.var_expr(self.ret).node if self.ret else N('Nothing'))))
        self.emit(N('EndOfScript'))
        nodes = self.layout()
        memory = sum(sizes(n)[1] for n in nodes)
        flags = self.flags
        if any(v.role in ('out', 'return') for v in self.order):
            flags |= FUNC_HAS_OUT_PARMS
        if any(v.role == 'local' and kind_of(v.type) in ('str', 'text', 'array', 'struct', 'map',
                                                         'set') for v in self.order):
            flags |= FUNC_HAS_DEFAULTS
        props = [self.property(v) for v in self.ordered()]
        super_fn = self.u.refs.function(*self.super) if self.super else None
        return CompiledFunction(self.f.name, flags, props,
                                Script(memory, nodes, self.names, lambda h: h), super_fn)

    def ordered(self):
        role_rank = {'param': 0, 'out': 0, 'return': 1, 'local': 2}
        return sorted(self.order, key=lambda v: role_rank[v.role])

    def layout(self):
        """Resolve labels to in-memory offsets; return the plain node list."""
        nodes, offset = [], 0
        for s in self.statements:
            if isinstance(s, Label):
                s.offset = offset
            else:
                nodes.append(s)
                offset += sizes(s)[1]
        for n in nodes:
            for top in n.walk():
                top.ops = [(k, v.offset if isinstance(v, Label) else v) for k, v in top.ops]
        return nodes

    def property(self, v):
        flags = {'param': PARAM_FLAGS, 'out': OUT_FLAGS, 'return': RETURN_FLAGS,
                 'local': 0}[v.role]
        if kind_of(v.type) == 'delegate':
            from bpkit.build import Ref
            q = Property(Name('DelegateProperty'))
            where, signature = v.signature
            q.refs = {'signature': Ref(self.u.refs.package_function(where, signature)
                                       if where.startswith('/Script/')
                                       else self.u.refs.function(where, signature))}
            _common(q, v.name, flags, FIELD_FLAGS, ELEMENT_SIZE['delegate'])
            return q
        return make_property(self.u, v.name, v.type, flags, FIELD_FLAGS)

    def statement(self, s):
        if isinstance(s, ast.Expr):
            if isinstance(s.value, ast.Constant):
                return                  # a docstring
            self.expression(s.value, discard=True)
        elif isinstance(s, ast.Pass):
            pass
        elif isinstance(s, ast.AnnAssign):
            t = self.t.annotation(s.annotation)
            v = self.declare(s.target.id, t, 'local')
            if s.value is not None:
                self.assign_to(self.var_expr(v), s.value)
        elif isinstance(s, ast.Assign):
            if len(s.targets) != 1:
                raise CompileError('one target per assignment', s)
            self.assign(s.targets[0], s.value)
        elif isinstance(s, ast.AugAssign):
            self.assign(s.target, ast.BinOp(left=_load(s.target), op=s.op, right=s.value,
                                            lineno=s.lineno, col_offset=s.col_offset))
        elif isinstance(s, ast.If):
            self.if_(s)
        elif isinstance(s, ast.While):
            self.while_(s)
        elif isinstance(s, ast.For):
            self.for_(s)
        elif isinstance(s, ast.Break):
            if not self.loops:
                raise CompileError('break outside a loop', s)
            self.emit(N('Jump', ('u32', self.loops[-1][1])))
        elif isinstance(s, ast.Continue):
            if not self.loops:
                raise CompileError('continue outside a loop', s)
            self.emit(N('Jump', ('u32', self.loops[-1][0])))
        elif isinstance(s, ast.Return):
            if s.value is not None:
                if not self.ret:
                    raise CompileError('%s returns nothing' % self.f.name, s)
                self.assign_to(self.var_expr(self.ret), s.value)
            self.emit(N('Jump', ('u32', self.end)))
        else:
            raise CompileError('%s is not supported' % type(s).__name__, s)

    def if_(self, s):
        cond = self.condition(s.test)
        other, done = Label(), Label()
        self.emit(N('JumpIfNot', ('u32', other), ('expr', cond.node)))
        for x in s.body:
            self.statement(x)
        if s.orelse:
            self.emit(N('Jump', ('u32', done)))
        self.statements.append(other)
        for x in s.orelse:
            self.statement(x)
        self.statements.append(done)

    def while_(self, s):
        top, done = Label(), Label()
        self.statements.append(top)
        cond = self.condition(s.test)
        self.emit(N('JumpIfNot', ('u32', done), ('expr', cond.node)))
        self.loops.append((top, done))
        for x in s.body:
            self.statement(x)
        self.loops.pop()
        self.emit(N('Jump', ('u32', top)))
        self.statements.append(done)

    def for_(self, s):
        if not isinstance(s.target, ast.Name):
            raise CompileError('for needs one loop variable', s)
        it = s.iter
        top, step, done = Label(), Label(), Label()
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == 'range':
            args = it.args
            if len(args) > 2:
                raise CompileError('range takes no step here (start, stop only)', s)
            start = args[0] if len(args) > 1 else ast.Constant(0)
            stop = args[1] if len(args) > 1 else args[0]
            i = self.declare(s.target.id, 'int', 'local')
            limit = self.temp('Temp_int_Limit', 'int')
            self.assign_to(self.var_expr(i), start)
            self.assign_to(self.var_expr(limit), stop)
            self.statements.append(top)
            less = self.call_library('Less_IntInt', [self.var_expr(i), self.var_expr(limit)], s)
            self.emit(N('JumpIfNot', ('u32', done), ('expr', less.node)))
            self.loops.append((step, done))
            for x in s.body:
                self.statement(x)
            self.loops.pop()
            self.statements.append(step)
            nxt = self.call_library('Add_IntInt', [self.var_expr(i), self.literal(1, 'int')], s)
            self.let(self.var_expr(i), nxt)
            self.emit(N('Jump', ('u32', top)))
            self.statements.append(done)
            return
        array = self.expression(it)
        if kind_of(array.type) != 'array':
            raise CompileError('for needs range(...) or an array', s)
        element = array.type[6:-1]
        item = self.declare(s.target.id, element, 'local')
        index = self.temp('Temp_int_Loop_Counter_Variable', 'int')
        self.let(self.var_expr(index), self.literal(0, 'int'))
        self.statements.append(top)
        length = self.call_library('Array_Length', [array], s)
        less = self.call_library('Less_IntInt', [self.var_expr(index), length], s)
        self.emit(N('JumpIfNot', ('u32', done), ('expr', less.node)))
        self.call_library('Array_Get', [array, self.var_expr(index), self.var_expr(item)], s,
                          discard=True)
        self.loops.append((step, done))
        for x in s.body:
            self.statement(x)
        self.loops.pop()
        self.statements.append(step)
        nxt = self.call_library('Add_IntInt', [self.var_expr(index), self.literal(1, 'int')], s)
        self.let(self.var_expr(index), nxt)
        self.emit(N('Jump', ('u32', top)))
        self.statements.append(done)

    # assignment
    def assign(self, target, value):
        if isinstance(target, ast.Name):
            if target.id in self.vars:
                dest = self.var_expr(self.vars[target.id])
            else:
                if self.member(target.id) is not None:
                    raise CompileError('%s is a variable of the class: set it as self.%s, or '
                                       'name the local something else'
                                       % (target.id, target.id), target)
                src = self.expression(value)
                v = self.declare(target.id, src.type, 'local')
                self.let(self.var_expr(v), src)
                return
            return self.assign_to(dest, value)
        if isinstance(target, ast.Attribute):
            if isinstance(target.value, ast.Name) and target.value.id == 'self':
                m = self.member(target.attr, target)
                if m is None:
                    raise CompileError('no variable %s on this class' % target.attr, target)
                if target.attr in self.u.replicated:
                    return self.replicated_set(m, value, target)
                return self.assign_to(self.var_expr(m), value)
            obj = self.expression(target.value)
            if kind_of(obj.type) == 'struct':
                # a member of a struct variable: Let names the member, its
                # destination is the member within the variable
                return self.assign_to(self.struct_member(obj, target.attr, target), value)
            dest = self.other_property(obj, target.attr, target)
            return self.assign_to(dest, value)
        raise CompileError('cannot assign to this', target)

    def replicated_set(self, m, value, node):
        """The editor's set of a replicated variable (VariableSetHandler):
        FlushNetDormancy on an actor, the assignment, MarkPropertyDirtyFromRepIndex,
        then the RepNotify function, called locally as "Set w/ Notify" does."""
        notify, _cond = self.u.replicated[m.name]
        if self.t.subclass(self.u.parent, 'Actor'):
            owner, fn = self.t.function_of(self.u.parent, 'FlushNetDormancy')
            self.invoke(None, owner, 'FlushNetDormancy', fn, node, True, compiled=[])
        self.assign_to(self.var_expr(m), value)
        fn = self.u.db['functions']['NetPushModelHelpers:MarkPropertyDirtyFromRepIndex']
        self.invoke(None, 'NetPushModelHelpers', 'MarkPropertyDirtyFromRepIndex', fn, node, True,
                    compiled=[Expr(N('Self'), 'object:Object'),
                              self.literal(self.u.rep_index(m.name), 'int'),
                              self.literal(m.name, 'name')])
        if notify:
            self.invoke(None, None, notify, self.own_entry(notify), node, True, compiled=[])

    def assign_to(self, dest, value_node):
        if isinstance(value_node, ast.List):
            # SetArray: the array, its new elements, EndArray.
            if kind_of(dest.type) != 'array':
                raise CompileError('a list goes into an array', value_node)
            element = dest.type[6:-1]
            items = [self.coerce(self.expression(x, want=element), element, x).node
                     for x in value_node.elts]
            self.emit(N('SetArray', ('expr', dest.node), *[('expr', i) for i in items],
                        ('expr', N('EndArray'))))
            return
        src = self.expression(value_node, want=dest.type)
        self.let(dest, self.coerce(src, dest.type, value_node))

    def let(self, dest, src):
        """The assignment the compiler picks for the destination's type
        (EmitDestinationExpression)."""
        k = kind_of(dest.type)
        if k in ('array', 'set', 'map'):
            node = N('Let', *self.dest_path(dest), ('expr', dest.node), ('expr', src.node))
        elif k == 'bool':
            node = N('LetBool', ('expr', dest.node), ('expr', src.node))
        elif k in ('object', 'class'):
            node = N('LetObj', ('expr', dest.node), ('expr', src.node))
        elif k == 'weakobject':
            node = N('LetWeakObjPtr', ('expr', dest.node), ('expr', src.node))
        else:
            node = N('Let', *self.dest_path(dest), ('expr', dest.node), ('expr', src.node))
        self.emit(node)

    def dest_path(self, dest):
        return self.path(dest.var)

    # expressions
    def literal(self, value, t):
        k = kind_of(t)
        if k in ('int', 'uint32'):
            return Expr(N('IntConst', ('i32', int(value))), t, literal=value)
        if k in ('float', 'double'):
            return Expr(N('FloatConst', ('f32', float(value))), t, literal=value)
        if k == 'bool':
            return Expr(N('True' if value else 'False'), t, literal=value)
        if k in ('byte', 'enum'):
            return Expr(N('ByteConst', ('u8', int(value))), t, literal=value)
        if k == 'int64':
            return Expr(N('Int64Const', ('i64', int(value))), t, literal=value)
        if k == 'str':
            return Expr(self.string(value), t, literal=value)
        if k == 'name':
            return Expr(N('NameConst', ('name', self.name(value))), t, literal=value)
        if k == 'text':
            return Expr(N('TextConst', ('u8', 2), ('expr', self.string(value))), t, literal=value)
        if k in ('object', 'class', 'interface') and value is None:
            return Expr(N('NoObject'), t, literal=value)
        raise CompileError('no literal of type %s' % t)

    def string(self, value):
        if any(ord(c) > 127 for c in value):
            raise CompileError('only ASCII string literals are supported')
        return N('StringConst', ('cstr', value))

    def condition(self, node):
        e = self.expression(node, want='bool')
        if kind_of(e.type) in ('object', 'class'):
            return self.call_library('IsValid', [e], node)
        return self.coerce(e, 'bool', node)

    def expression(self, node, want=None, discard=False):
        if isinstance(node, ast.Constant):
            v = node.value
            if v is None:
                return self.literal(None, want if want and kind_of(want) in ('object', 'class')
                                    else 'object:Object')
            if isinstance(v, bool):
                return self.literal(v, 'bool')
            if isinstance(v, int):
                if want and kind_of(want) in ('float', 'double', 'byte', 'int64'):
                    return self.literal(v, want)
                return self.literal(v, 'int')
            if isinstance(v, float):
                return self.literal(v, 'float')
            if isinstance(v, str):
                if want and kind_of(want) in ('name', 'text'):
                    return self.literal(v, want)
                return self.literal(v, 'str')
            raise CompileError('unsupported literal', node)
        if isinstance(node, ast.Name):
            if node.id == 'self':
                return Expr(N('Self'), 'object:' + self.u.refs.own_key)
            if node.id in self.vars:
                return self.var_expr(self.vars[node.id])
            m = self.member(node.id)
            if m is not None:
                return self.var_expr(m)
            if node.id in self.t.aliases:
                path = self.t.aliases[node.id]
                return Expr(N('ObjectConst', ('obj', self.u.refs.cls(path.lower()))),
                            'class:' + path)
            if self.t.is_class(node.id) and not self.t.is_struct(node.id):
                # a native class named as a value: OMDMessageBox
                return Expr(N('ObjectConst', ('obj', self.u.refs.cls(node.id))), 'class:' + node.id)
            raise CompileError('unknown name %s' % node.id, node)
        if isinstance(node, ast.Attribute):
            return self.attribute(node)
        if isinstance(node, ast.Call):
            return self.call(node, want=want, discard=discard)
        if isinstance(node, ast.BinOp):
            return self.binop(node)
        if isinstance(node, ast.Compare):
            return self.compare(node)
        if isinstance(node, ast.BoolOp):
            fn = 'BooleanAND' if isinstance(node.op, ast.And) else 'BooleanOR'
            result = self.condition(node.values[0])
            for v in node.values[1:]:
                result = self.call_library(fn, [result, self.condition(v)], node)
            return result
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return self.call_library('Not_PreBool', [self.condition(node.operand)], node)
            if isinstance(node.op, ast.USub):
                if isinstance(node.operand, ast.Constant):
                    return self.expression(ast.Constant(-node.operand.value), want)
                e = self.expression(node.operand)
                zero = self.literal(0, e.type)
                return self.call_library('Subtract_' + SUFFIX[kind_of(e.type)], [zero, e], node)
        if isinstance(node, ast.Subscript):
            array = self.expression(node.value)
            if kind_of(array.type) != 'array':
                raise CompileError('only arrays can be indexed', node)
            item = self.temp('CallFunc_Array_Get_Item', array.type[6:-1])
            self.call_library('Array_Get', [array, self.expression(node.slice, want='int'),
                                            self.var_expr(item)], node, discard=True)
            return self.var_expr(item)
        raise CompileError('%s is not supported' % type(node).__name__, node)

    def attribute(self, node):
        base = node.value
        if isinstance(base, ast.Name) and base.id == 'self':
            m = self.member(node.attr, node)
            if m is None:
                raise CompileError('no variable %s on this class' % node.attr, node)
            return self.var_expr(m)
        if isinstance(base, ast.Name) and self.t.is_enum(base.id) and base.id not in self.vars:
            enum = self.u.db['enums'][base.id]
            if node.attr not in enum['values']:
                raise CompileError('%s has no value %s' % (base.id, node.attr), node)
            number = enum['numbers'][enum['values'].index(node.attr)]
            return self.literal(number, 'enum:' + base.id)
        obj = self.expression(base)
        if kind_of(obj.type) == 'struct':
            return self.struct_member(obj, node.attr, node)
        return self.other_property(obj, node.attr, node)

    def struct_member(self, obj, field, node):
        sname = arg_of(obj.type)
        # a member may be declared by a parent struct (OMDMissionProto's Guid
        # is OMDProtoBase's)
        found = self.t.property_of(sname, field)
        if found is None:
            raise CompileError('%s has no member %s' % (sname, field), node)
        declaring, t, real = found
        v = Var(real, t, 'member')
        v.declaring = self.u.refs.struct(declaring)
        if obj.var is None:
            raise CompileError('take a struct into a variable before reading its members', node)
        return Expr(N('StructMemberContext', *self.path(v), ('expr', obj.node)), v.type, v)

    def other_property(self, obj, name, node):
        """obj.Name: a property read through a Context, as an rvalue-able term."""
        if kind_of(obj.type) not in ('object', 'class'):
            raise CompileError('%s is not an object' % ast.unparse(node.value), node)
        found = self.t.property_of(arg_of(obj.type), name)
        if not found:
            raise CompileError('%s has no property %s' % (arg_of(obj.type), name), node)
        cls, t, real = found
        v = Var(real, t, 'member')
        v.declaring = self.u.refs.cls(cls)
        v.declaring_key = cls
        inner = N('InstanceVariable', *self.path(v))
        return Expr(self.context(obj.node, v, inner), t, v)

    def context(self, obj_node, rvalue, member):
        """Context: the object, the in-memory size of what follows, the property
        receiving the result, then the member expression."""
        return N('Context', ('expr', obj_node), ('u32', sizes(member)[1]),
                 *self.path(rvalue), ('expr', member))

    def binop(self, node):
        if isinstance(node.op, ast.Add):
            left = self.expression(node.left)
            if kind_of(left.type) == 'str':
                right = self.coerce(self.expression(node.right, want='str'), 'str', node)
                return self.call_library('Concat_StrStr', [left, right], node)
        else:
            left = self.expression(node.left)
        right = self.expression(node.right)
        family = BINARY.get(type(node.op))
        if family is None:
            raise CompileError('operator not supported', node)
        t = 'float' if 'float' in (kind_of(left.type), kind_of(right.type)) else kind_of(left.type)
        if isinstance(node.op, ast.Pow):
            t = 'float'                 # the library raises floats only
        elif isinstance(node.op, ast.FloorDiv) and t != 'int':
            raise CompileError('// divides whole numbers (Divide_IntInt rounds toward zero)', node)
        left, right = self.coerce(left, t, node), self.coerce(right, t, node)
        return self.call_library('%s_%s' % (family, SUFFIX[t]), [left, right], node)

    def compare(self, node):
        if len(node.ops) != 1:
            raise CompileError('one comparison at a time', node)
        left = self.expression(node.left)
        right = self.expression(node.comparators[0], want=left.type)
        family = COMPARE[type(node.ops[0])]
        lk, rk = kind_of(left.type), kind_of(right.type)
        if lk in ('object', 'class') or rk in ('object', 'class'):
            if family not in ('EqualEqual', 'NotEqual'):
                raise CompileError('objects compare only with == and !=', node)
            return self.call_library('%s_ObjectObject' % family, [left, right], node)
        if lk == 'enum' or rk == 'enum':
            return self.call_library('%s_ByteByte' % family, [left, right], node)
        t = 'float' if 'float' in (lk, rk) else lk
        if family in ('EqualEqual', 'NotEqual') and t == 'str':
            return self.call_library('%s_StrStr' % family, [left, right], node)
        left, right = self.coerce(left, t, node), self.coerce(right, t, node)
        return self.call_library('%s_%s' % (family, SUFFIX[t]), [left, right], node)

    def object_fits(self, have, want, node):
        """An object of class `have` into a place typed `want`: refused when
        neither class derives from the other. A function then called on it
        through the place's type runs on an object of another class."""
        a, b = arg_of(have), arg_of(want)
        if not a or not b or self.t.subclass(a, b) or self.t.subclass(b, a):
            return
        # only classes whose ancestry the type database knows to Object
        if not (self.t.subclass(a, 'Object') and self.t.subclass(b, 'Object')):
            return
        raise CompileError('a %s is not a %s: use another variable, or cast()' % (a, b), node)

    def coerce(self, e, t, node=None):
        """Convert where the Blueprint compiler would insert a conversion node."""
        if t is None or e.type == t:
            return e
        have, want = kind_of(e.type), kind_of(t)
        if have == want == 'object':
            self.object_fits(e.type, t, node)
            return e
        if have == want and have in ('class', 'softobject', 'softclass', 'struct', 'enum', 'array'):
            return e
        if (have, want) in (('enum', 'byte'), ('byte', 'enum')):
            return e
        if (have, want) == ('weakobject', 'object'):
            # read through the VM, a weak pointer arrives as the object it
            # points to (FObjectPropertyBase::CopyCompleteValueToScriptVM)
            return e
        if e.literal is not None and not isinstance(e.literal, str) and want in (
                'float', 'double', 'byte', 'enum', 'int64'):
            return self.literal(e.literal, t)
        if isinstance(e.literal, str) and want in ('name', 'text'):
            return self.literal(e.literal, t)
        conv = {('int', 'float'): 'Conv_IntToFloat', ('int', 'str'): 'Conv_IntToString',
                ('float', 'str'): 'Conv_FloatToString', ('bool', 'str'): 'Conv_BoolToString',
                ('name', 'str'): 'Conv_NameToString', ('text', 'str'): 'Conv_TextToString',
                ('str', 'text'): 'Conv_StringToText', ('str', 'name'): 'Conv_StringToName',
                ('int', 'text'): 'Conv_IntToText', ('float', 'text'): 'Conv_FloatToText',
                ('object', 'str'): 'Conv_ObjectToString', ('byte', 'int'): 'Conv_ByteToInt',
                ('int', 'byte'): 'Conv_IntToByte', ('bool', 'int'): 'Conv_BoolToInt',
                ('name', 'text'): 'Conv_NameToText'}.get((have, want))
        if conv:
            return self.call_library(conv, [e], node)
        raise CompileError('cannot use %s as %s' % (e.type, t), node)

    # calls
    def call(self, node, want=None, discard=False):
        func = node.func
        if isinstance(func, ast.Name):
            n = func.id
            if n == 'cast':
                return self.cast(node)
            if n == 'class_cast':
                return self.class_cast(node)
            if n == 'Asset':
                return self.asset(node)
            if n == 'len':
                arg = self.expression(node.args[0])
                if arg.type == 'str':
                    return self.call_library('Len', [arg], node)
                return self.call_library('Array_Length', [arg], node)
            if n == 'str':
                return self.coerce(self.expression(node.args[0]), 'str', node)
            if n in ('Text', 'Name') and len(node.args) == 1:
                t = n.lower()
                return self.coerce(self.expression(node.args[0], want=t), t, node)
            if self.t.is_struct(n) and not self.t.is_class(n):
                return self.struct_literal(n, node)
            if n in self.u.signatures:
                return self.invoke(None, None, n, self.own_entry(n), node, discard)
            found = self.t.function_of(self.u.parent, n)
            if found:
                return self.invoke(None, found[0], n, found[1], node, discard)
            lib = self.t.library(n)
            if lib:
                return self.invoke(None, lib[0], n, lib[1], node, discard)
            raise CompileError('no function %s' % n, node)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call) and \
                isinstance(func.value.func, ast.Name) and func.value.func.id == 'super':
            real = self.t.spaced_function(self.u.parent, func.attr)
            found = self.t.function_of(self.u.parent, real)
            if not found:
                raise CompileError('the parent class has no function %s' % func.attr, node)
            return self.invoke(None, found[0], real, found[1], node, discard, parent=True)
        if isinstance(func, ast.Attribute):
            base = func.value
            if isinstance(base, ast.Name) and base.id not in self.vars and \
                    self.u.db['functions'].get('%s:%s' % (base.id, func.attr)):
                fn = self.u.db['functions']['%s:%s' % (base.id, func.attr)]
                return self.invoke(None, base.id, func.attr, fn, node, discard)
            if isinstance(base, ast.Name) and base.id == 'self':
                return self.call(ast.Call(func=ast.Name(func.attr), args=node.args,
                                          keywords=node.keywords, lineno=node.lineno,
                                          col_offset=node.col_offset), want, discard)
            obj = self.expression(base)
            if kind_of(obj.type) == 'mcdelegate' and func.attr in ('add', 'remove'):
                return self.delegate(obj, func.attr, node)
            if kind_of(obj.type) == 'array' and func.attr == 'insert':
                # list.insert(index, item) -> Array_Insert(array, item, index)
                if len(node.args) != 2:
                    raise CompileError('insert takes an index and an item', node)
                item = self.coerce(self.expression(node.args[1], want=obj.type[6:-1]),
                                   obj.type[6:-1], node)
                index = self.expression(node.args[0], want='int')
                return self.call_library('Array_Insert', [obj, item, index], node, discard=discard)
            if kind_of(obj.type) == 'array' and func.attr in ARRAY_METHODS:
                args = [obj] + [self.expression(a, want=obj.type[6:-1]) for a in node.args]
                if func.attr == 'append':
                    args[1] = self.coerce(args[1], obj.type[6:-1], node)
                return self.call_library(ARRAY_METHODS[func.attr], args, node, discard=discard)
            if kind_of(obj.type) not in ('object', 'class'):
                raise CompileError('%s is not an object' % ast.unparse(base), node)
            real = self.t.spaced_function(arg_of(obj.type), func.attr)
            found = self.t.function_of(arg_of(obj.type), real)
            if not found:
                raise CompileError('%s has no function %s' % (arg_of(obj.type), func.attr), node)
            return self.invoke(obj, found[0], real, found[1], node, discard)
        raise CompileError('cannot call this', node)

    def delegate(self, prop, method, node):
        """Event.add(Object, "Function") or Event.remove(...): the editor's
        CreateDelegate node (BindDelegate into a delegate local), then
        Add/RemoveMulticastDelegate."""
        if len(node.args) != 2 or not isinstance(node.args[1], ast.Constant) or \
                not isinstance(node.args[1].value, str):
            raise CompileError('%s takes an object and a function name in quotes' % method, node)
        owner = getattr(prop.var, 'declaring_key', None)
        signature = '%s__DelegateSignature' % arg_of(prop.type)
        sig = self.u.db['functions'].get(('%s:%s' % (owner, signature)).lower()) or \
            self.u.db['functions'].get('%s:%s' % (owner, signature))
        where = owner
        if sig is None:
            # a native delegate's signature belongs to its module's package
            native = self.u.db['delegates'].get(arg_of(prop.type))
            if native is not None and native.get('owner') is None:
                sig, where = native, '/Script/' + native['module']
            elif native is not None and native.get('owner') == owner:
                # one declared in a native class: its signature is the class's
                sig = native
        if owner is None or sig is None:
            raise CompileError('no signature known for %s' % ast.unparse(node.func.value), node)
        target = self.expression(node.args[0])
        fname = node.args[1].value
        if isinstance(node.args[0], ast.Name) and node.args[0].id == 'self' and \
                fname in self.u.signatures:
            fn = self.own_entry(fname)
        else:
            if kind_of(target.type) != 'object':
                raise CompileError('%s is not an object' % ast.unparse(node.args[0]), node)
            found = self.t.function_of(arg_of(target.type), fname)
            if not found:
                raise CompileError('%s has no function %s' % (arg_of(target.type), fname), node)
            fn = found[1]
        want = [p['type'].lower() for p in sig['params']]
        have = [p['type'].lower() for p in fn['params']]
        if want != have:
            raise CompileError('%s takes (%s); %s takes (%s)' % (
                ast.unparse(node.func.value), ', '.join(want), fname, ', '.join(have)), node)
        v = self.temp('K2Node_CreateDelegate_OutputDelegate', 'delegate:' + arg_of(prop.type))
        v.signature = (where, signature)
        self.emit(N('BindDelegate', ('name', self.name(fname)),
                    ('expr', self.var_expr(v).node), ('expr', target.node)))
        op = 'AddMulticastDelegate' if method == 'add' else 'RemoveMulticastDelegate'
        self.emit(N(op, ('expr', prop.node), ('expr', self.var_expr(v).node)))
        return Expr(N('Nothing'), None)

    def own_entry(self, name):
        params, ret, flags, _ = self.u.signatures[name]
        ps = [{'name': n, 'type': t, 'out': out, 'return': False} for n, t, out in params]
        if ret:
            ps.append({'name': 'ReturnValue', 'type': ret, 'out': True, 'return': True})
        return {'owner': None, 'params': ps, 'function_flags': flags, 'own': True}

    def call_library(self, name, args, node, discard=False):
        lib = self.t.library(name)
        if not lib:
            raise CompileError('no library function %s' % name, node)
        return self.invoke(None, lib[0], name, lib[1], node, discard, compiled=args)

    def invoke(self, obj, owner, name, fn, node, discard, compiled=None, parent=False):
        """Emit a call statement; return the result as a variable expression.
        `parent` calls the parent class's version (super()), by reference."""
        params = [p for p in fn['params'] if not p.get('return')]
        ret = next((p for p in fn['params'] if p.get('return')), None)
        args = list(compiled) if compiled is not None else self.arguments(fn, params, node)
        if len(args) != len(params):
            raise CompileError('%s takes %d arguments, %d given' % (name, len(params), len(args)),
                               node)
        terms = []
        for p, a in zip(params, args):
            if p.get('out'):
                if a.var is None:
                    raise CompileError('%s: out parameter %s needs a variable' % (name, p['name']),
                                       node)
                terms.append(a.node)
            else:
                terms.append(self.coerce(a, None if p.get('wildcard') else p['type'], node).node)
        # what is called, and how
        if fn.get('own') and fn['function_flags'] & 0x40:
            # an RPC is called by name and never through the Local* forms,
            # which would skip the engine's choice of where it runs
            fref = self.u.refs.own_function(name)
            op = 'VirtualFunction'
        elif fn.get('own'):
            fref = self.u.refs.own_function(name)
            op = 'LocalVirtualFunction' if not fn['function_flags'] & 0x1 else 'LocalFinalFunction'
        else:
            fref = self.u.refs.function(owner, name)
            op = call_opcode(fn, self.u.db, parent_context=parent)
        call_ops = ([('name', self.name(name))] if op.endswith('VirtualFunction')
                    else [('obj', fref)])
        call = N(op, *call_ops, *[('expr', t) for t in terms], ('expr', N('EndFunctionParms')))
        result = None
        if ret is not None and not discard:
            result = self.temp('CallFunc_%s_ReturnValue' % name, ret['type'])
        elif ret is not None:
            result = self.temp('CallFunc_%s_ReturnValue' % name, ret['type'])
        # the target object: none for a math call or a call on self; the class's
        # CDO for any other static call
        if op != 'CallMath':
            static = fn.get('function_flags', 0) & FUNC_STATIC
            if static:
                obj_node = N('ObjectConst', ('obj', self.u.refs.cdo(owner)))
            elif obj is not None:
                obj_node = obj.node
            else:
                obj_node = None
            if obj_node is not None:
                call = self.context(obj_node, result, call)
        if result is not None:
            self.let(self.var_expr(result), Expr(call, ret['type']))
            return self.var_expr(result)
        self.emit(call)
        return Expr(N('Nothing'), None)

    def arguments(self, fn, params, node):
        """Positional and keyword arguments matched to parameters, the world
        context filled with self, defaults taken from the declaration."""
        given = list(node.args)
        keywords = {k.arg: k.value for k in node.keywords}
        world = (fn.get('meta') or {}).get('WorldContext')
        out = []
        for p in params:
            if keywords and p['name'] in keywords:
                src = keywords.pop(p['name'])
            elif p['name'] == world and len(given) < sum(1 for q in params if q['name'] != world) + 1:
                out.append(Expr(N('Self'), 'object:Object'))
                continue
            elif given:
                src = given.pop(0)
            elif p.get('default') is not None:
                out.append(self.default(p, node))
                continue
            else:
                raise CompileError('missing argument %s' % p['name'], node)
            if p.get('out') and isinstance(src, ast.Name) and src.id not in self.vars \
                    and self.member(src.id) is None:
                self.declare(src.id, p['type'], 'local')
            out.append(self.expression(src, want=p['type']))
        if given or keywords:
            raise CompileError('too many arguments', node)
        return out

    def default(self, p, node):
        """A parameter's C++ default as a literal."""
        text = p['default'].strip()
        k = kind_of(p['type'])
        try:
            if k == 'bool':
                return self.literal(text == 'true', 'bool')
            if k in ('int', 'byte', 'int64'):
                return self.literal(int(text, 0), p['type'])
            if k in ('float', 'double'):
                return self.literal(float(text.rstrip('fF')), p['type'])
            if k in ('object', 'class') and text in ('nullptr', 'NULL', '0'):
                return self.literal(None, p['type'])
            if k == 'enum':
                enum = self.u.db['enums'][arg_of(p['type'])]
                value = text.split('::')[-1]
                return self.literal(enum['numbers'][enum['values'].index(value)], p['type'])
            if k in ('str', 'name', 'text'):
                inner = text
                for wrap in ('FString(', 'FName(', 'FText::FromString(', 'TEXT(', 'INVTEXT('):
                    if inner.startswith(wrap):
                        inner = inner[len(wrap):-1]
                inner = inner.strip()
                if inner.startswith('TEXT('):
                    inner = inner[5:-1]
                if inner in ('NAME_None', 'FText::GetEmpty()', 'FString()', 'FText()'):
                    inner = '""' if k != 'name' else '"None"'
                return self.literal(ast.literal_eval(inner), p['type'])
        except (ValueError, KeyError, SyntaxError, IndexError):
            pass
        raise CompileError('give %s explicitly (default %r is not supported)' % (p['name'], text),
                           node)

    def cast(self, node):
        if len(node.args) != 2 or not isinstance(node.args[0], ast.Name):
            raise CompileError('cast(Class, value)', node)
        cls = node.args[0].id
        stem = cls
        if cls in self.t.aliases:
            cls = self.t.aliases[cls]
        elif not self.t.is_class(cls):
            raise CompileError('unknown class %s' % cls, node)
        value = self.expression(node.args[1])
        result = self.temp('K2Node_DynamicCast_As%s' % stem, 'object:' + cls)
        key = cls.lower() if cls.startswith('/') else cls
        self.emit(N('LetObj', ('expr', self.var_expr(result).node),
                    ('expr', N('DynamicCast', ('obj', self.u.refs.cls(key)), ('expr', value.node)))))
        return self.var_expr(result)

    def class_cast(self, node):
        """class_cast(Class, value): a class value as a class of Class, or None
        when it is not one (MetaCast, the editor's Cast To Class)."""
        if len(node.args) != 2 or not isinstance(node.args[0], ast.Name):
            raise CompileError('class_cast(Class, value)', node)
        cls = node.args[0].id
        stem = cls
        if cls in self.t.aliases:
            cls = self.t.aliases[cls]
        elif not self.t.is_class(cls):
            raise CompileError('unknown class %s' % cls, node)
        value = self.expression(node.args[1])
        result = self.temp('K2Node_ClassDynamicCast_As%s' % stem, 'class:' + cls)
        key = cls.lower() if cls.startswith('/') else cls
        self.emit(N('LetObj', ('expr', self.var_expr(result).node),
                    ('expr', N('MetaCast', ('obj', self.u.refs.cls(key)), ('expr', value.node)))))
        return self.var_expr(result)

    def asset(self, node):
        """Asset("/Game/X/Y.Y_C"): a Blueprint class, as an ObjectConst. The class
        is one of the game's or one built earlier in the same build.
        Asset("Texture2D'/Game/X/Y.Y'"): any other asset, named as the widget
        descriptions name one, by class and path."""
        if len(node.args) != 1 or not isinstance(node.args[0], ast.Constant) \
                or not isinstance(node.args[0].value, str):
            raise CompileError('Asset takes one path, "/Game/X/Y.Y_C"', node)
        path = node.args[0].value
        if "'" in path:
            klass = path.split("'", 1)[0]
            if not self.t.is_class(klass):
                raise CompileError('unknown class %s' % klass, node)
            return Expr(N('ObjectConst', ('obj', self.u.refs.object(path))), 'object:' + klass)
        entry = self.u.db['classes'].get(path.lower())
        if not entry or 'generated' not in entry:
            raise CompileError('no Blueprint class %s in the game or this build' % path, node)
        return Expr(N('ObjectConst', ('obj', self.u.refs.cls(path.lower()))),
                    'class:' + entry['path'])

    def struct_literal(self, sname, node):
        """Struct(a, b, ...): a StructConst of literal members, in declaration order."""
        entry = self.t.entry(sname) or {}
        fields = list(entry.get('properties', {}).items())
        if sname in ('Vector', 'Rotator') and all(isinstance(a, ast.Constant) for a in node.args):
            op = 'VectorConst' if sname == 'Vector' else 'RotationConst'
            values = [float(a.value) for a in node.args] + [0.0] * (3 - len(node.args))
            return Expr(N(op, *[('f32', v) for v in values]), 'struct:' + sname)
        values = dict(zip([f for f, _ in fields], node.args))
        values.update({k.arg: k.value for k in node.keywords})
        members = []
        for f, t in fields:
            src = values.get(f)
            if src is None:
                members.append(self.zero(t, node))
                continue
            if not isinstance(src, (ast.Constant, ast.UnaryOp)):
                raise CompileError('struct literals take literal members', node)
            members.append(self.coerce(self.expression(src, want=t), t, node).node)
        size = self.u.db['struct_sizes'].get(sname)
        if size is None:
            from bpkit.typedb import native_size
            size = native_size(self.u.db, sname)
        if size is None:
            raise CompileError('size of %s unknown: no Blueprint in the game holds one, '
                               'and the exe does not name it' % sname, node)
        return Expr(N('StructConst', ('obj', self.u.refs.struct(sname)), ('i32', size),
                      *[('expr', m) for m in members], ('expr', N('EndStructConst'))),
                    'struct:' + sname)

    def zero(self, t, node):
        k = kind_of(t)
        if k in ('int', 'float', 'byte', 'enum', 'bool', 'int64'):
            return self.literal(0 if k != 'bool' else False, t).node
        if k == 'str':
            return self.string('')
        if k == 'name':
            return self.literal('None', 'name').node
        if k in ('object', 'class'):
            return N('NoObject')
        if k == 'struct':
            return self.struct_literal(arg_of(t), ast.Call(func=None, args=[], keywords=[],
                                                           lineno=getattr(node, 'lineno', 0))).node
        raise CompileError('no zero for %s' % t, node)


def _load(target):
    """An assignment target as a value expression (for +=)."""
    if isinstance(target, ast.Name):
        return ast.Name(target.id, ast.Load(), lineno=target.lineno, col_offset=target.col_offset)
    if isinstance(target, ast.Attribute):
        return ast.Attribute(target.value, target.attr, ast.Load(), lineno=target.lineno,
                             col_offset=target.col_offset)
    raise CompileError('cannot update this', target)


def make_property(unit, name, t, property_flags, field_flags):
    """An FProperty for a canonical type: its class, size, references and children."""
    from bpkit.build import Ref
    k = kind_of(t)
    refs = unit.refs
    if k in PROPERTY_CLASS:
        q = Property(Name(PROPERTY_CLASS[k]))
        if k == 'byte':
            q.refs = {'enum': Ref(None)}
    elif k == 'enum':
        enum = unit.db['enums'].get(arg_of(t), {})
        if enum.get('kind') == 'class':
            q = Property(Name('EnumProperty'))
            under = Property(Name('ByteProperty'))
            _common(under, 'UnderlyingType', 0, FIELD_FLAGS, 1)
            under.refs = {'enum': Ref(None)}
            q.children = {'underlying': under}
        else:
            q = Property(Name('ByteProperty'))
        q.refs = {'enum': Ref(refs.enum(arg_of(t)))}
    elif k == 'struct':
        q = Property(Name('StructProperty'))
        q.refs = {'struct': Ref(refs.struct(arg_of(t)))}
    elif k in ('object', 'weakobject', 'softobject'):
        q = Property(Name({'object': 'ObjectProperty', 'weakobject': 'WeakObjectProperty',
                           'softobject': 'SoftObjectProperty'}[k]))
        q.refs = {'property_class': Ref(refs.cls(arg_of(t)))}
        if k == 'object' and unit.types.instanced(arg_of(t)):
            property_flags |= CPF_INSTANCED_REFERENCE
    elif k in ('class', 'softclass'):
        q = Property(Name('ClassProperty' if k == 'class' else 'SoftClassProperty'))
        q.refs = {'property_class': Ref(refs.cls('Class')), 'meta_class': Ref(refs.cls(arg_of(t)))}
    elif k == 'array':
        q = Property(Name('ArrayProperty'))
        q.children = {'inner': make_property(unit, name, t[6:-1], 0, FIELD_FLAGS)}
    elif k == 'map':
        key, value = map_types(t)
        q = Property(Name('MapProperty'))
        q.children = {'key': map_part(unit, name + '_Key', key),
                      'value': map_part(unit, name + '_Value', value)}
    else:
        raise CompileError('no property for type %s' % t)
    if k == 'struct':
        size = unit.db['struct_sizes'].get(arg_of(t))
        if size is None:
            from bpkit.typedb import native_size
            size = native_size(unit.db, arg_of(t))
        if size is None:
            raise CompileError('size of %s unknown: no Blueprint in the game holds one, '
                               'and the exe does not name it' % arg_of(t))
    else:
        size = ELEMENT_SIZE[k]
    _common(q, name, property_flags, field_flags, size)
    if k == 'bool':
        q.extra = BOOL_FIELDS
    return q


def map_types(t):
    """A map type's key and value types: map<K,V>, either possibly nested."""
    inner, depth = t[4:-1], 0
    for i, c in enumerate(inner):
        if c == '<':
            depth += 1
        elif c == '>':
            depth -= 1
        elif c == ',' and depth == 0:
            return inner[:i], inner[i + 1:]
    raise CompileError('cannot read map type %s' % t)


#: An FName's true size in this build, as a map's pair lays it out (UField::Next
#: is 0x28, so UObjectBase's NamePrivate is 8 bytes; the native proto index's
#: name-keyed elements are 0x18 apart).
MAP_NAME_SIZE = 8


def map_part(unit, name, t):
    """A map's key or value property: sized as the engine lays out the pair."""
    q = make_property(unit, name, t, 0, FIELD_FLAGS)
    if kind_of(t) == 'name':
        q.element_size = MAP_NAME_SIZE
    return q


def _common(q, name, property_flags, field_flags, size):
    q.name = Name(*split_name(name))
    q.flags = field_flags
    q.array_dim, q.element_size = 1, size
    q.property_flags = property_flags
    q.rep_index = 0
    q.rep_notify = Name('None')
    q.rep_condition = 0
