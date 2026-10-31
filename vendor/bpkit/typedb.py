"""The type database: every class, struct, enum and function a Blueprint can name.

Three sources, merged into one table keyed by reflection name:

* native C++ API: `bpkit.headers` over the game's UHT dump (131 modules), plus
  the stock 4.27.2 Engine and CoreUObject headers, which the dump lacks
* `Mappings.usmap`: the live game's reflection data -- every native class's
  and struct's properties and every enum, but no functions and no object
  classes; it confirms what the headers say exists
* the game's own Blueprints: each generated class decoded by `bpkit.structs`,
  with its variables and its functions' parameters

Every type is normalised to one canonical text, whatever it was read from (a
C++ declaration, a usmap kind, a cooked FProperty), so that two sources can be
compared exactly:

    bool int8 int16 int int64 byte uint16 uint32 uint64 float double
    name str text fieldpath
    object:Class  class:MetaClass  softobject:Class  softclass:MetaClass
    weakobject:Class  lazyobject:Class  interface:Class
    struct:Name  enum:EName  delegate:Name  mcdelegate:Name
    array<T>  set<T>  map<K,V>

    db = load()                       # built once, cached in cache/typedb.json
    db['functions']['KismetSystemLibrary:PrintString']
"""

import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WIN64 = os.path.dirname(ROOT)
DUMP = os.path.join(WIN64, 'UHTHeaderDump')
USMAP = os.path.join(WIN64, 'Mappings.usmap')
ENGINE = '/mnt/2tb_pro_a/UnrealEngine-4.27.2-release/Engine/Source/Runtime'
CACHE = os.path.join(ROOT, 'cache', 'typedb.json')

#: Stock modules the dump lacks, read from the engine's own headers.
ENGINE_MODULES = {'Engine': ('Engine/Classes', 'Engine/Public', 'Engine/Private'),
                  'CoreUObject': ('CoreUObject/Public',)}

# -- FProperty flags that mark parameters (ObjectMacros.h) ---------------------
CPF_CONST_PARM = 0x2
CPF_PARM = 0x80
CPF_OUT_PARM = 0x100
CPF_RETURN_PARM = 0x400
CPF_REFERENCE_PARM = 0x8000000
FUNC_STATIC = 0x2000

#: Meta keys naming parameters whose declared type is only a placeholder.
WILDCARD_META = ('ArrayParm', 'ArrayTypeDependentParams', 'CustomStructureParam',
                 'MapParam', 'MapKeyParam', 'MapValueParam', 'SetParam')


# -- canonical types ----------------------------------------------------------

CPP_SCALARS = {'bool': 'bool', 'int8': 'int8', 'int16': 'int16', 'int32': 'int',
               'int': 'int', 'int64': 'int64', 'uint8': 'byte', 'uint16': 'uint16',
               'uint32': 'uint32', 'uint64': 'uint64', 'float': 'float', 'double': 'double',
               'FName': 'name', 'FString': 'str', 'FText': 'text', 'FFieldPath': 'fieldpath'}
CPP_WRAPPERS = {'TSubclassOf': 'class', 'TSoftObjectPtr': 'softobject',
                'TSoftClassPtr': 'softclass', 'TWeakObjectPtr': 'weakobject',
                'TLazyObjectPtr': 'lazyobject', 'TScriptInterface': 'interface'}


def _name(cpp):
    """A C++ type name as reflection names it."""
    from bpkit.headers import reflection_name
    cpp = cpp.split('::')[-1] if not cpp.endswith('::Type') else cpp.split('::')[-2]
    return cpp if cpp.startswith('E') and cpp[1:2].isupper() else reflection_name(cpp)


def cpp_type(text, delegates=()):
    """Canonical type of a C++ declaration's type text."""
    from bpkit.headers import split_top
    t = re.sub(r'\b(const|class|struct|enum|typename)\b', ' ', text)
    t = t.replace('&', ' ').strip()
    t = ' '.join(t.split())
    pointer = t.endswith('*')
    t = t.rstrip('* ').strip()
    m = re.match(r'(\w+)\s*<(.*)>$', t)
    if m:
        outer, inner = m.group(1), m.group(2)
        args = [a.strip() for a in split_top(inner)]
        if outer == 'TArray':
            return 'array<%s>' % cpp_type(args[0], delegates)
        if outer == 'TSet':
            return 'set<%s>' % cpp_type(args[0], delegates)
        if outer == 'TMap':
            return 'map<%s,%s>' % (cpp_type(args[0], delegates), cpp_type(args[1], delegates))
        if outer == 'TEnumAsByte':
            return 'enum:%s' % _name(args[0].strip())
        if outer in CPP_WRAPPERS:
            return '%s:%s' % (CPP_WRAPPERS[outer], _name(args[0].rstrip('* ')))
        if outer == 'TFieldPath':
            return 'fieldpath'
        return 'unknown:%s' % t
    if t in CPP_SCALARS:
        return CPP_SCALARS[t]
    if pointer:
        name = _name(t)
        return 'class:Object' if name == 'Class' else 'object:%s' % name
    short = t.split('::')[-1]
    if short in delegates:
        return delegates[short]
    if re.match(r'E[A-Z]', t) or t.endswith('::Type'):
        return 'enum:%s' % _name(t)
    if re.match(r'F[A-Z]', t):
        return 'struct:%s' % _name(t)
    return 'unknown:%s' % t


def object_path(package, index, own_path):
    """Full path of a package index: '/Script/UMG.Widget', '/Game/X/Y.Y_C:Fn'.

    An import's chain ends at its package import; an export's ends at the
    package being read, which has no entry of its own, so `own_path` is used.
    """
    parts = []
    while index:
        parts.append(package.object_name(index))
        if index > 0:
            index = package.exports[index - 1].outer_index
            if not index:
                parts.append(own_path)
        else:
            index = package.imports[-index - 1].outer
    parts.reverse()
    return parts[0] + ''.join(('.' if i == 1 else ':') + n for i, n in enumerate(parts[1:], 1))


def db_key(name):
    """The key a type's class or struct name is stored under: Blueprint paths
    are lowercased, native names kept."""
    return name.lower() if name and name.startswith('/') else name


def object_type_name(package, index, own_path):
    """How a type names an object: like object_key, but keeping the original
    case, since a tag carries the real spelling (an enum's or struct's name)."""
    if not index:
        return None
    path = object_path(package, index, own_path)
    return path.split('.', 1)[1] if path.startswith('/Script/') else path


def object_key(package, index, own_path):
    """How the database names an object. A native is named without its module
    ('Widget', 'KismetSystemLibrary:PrintString'); anything from a /Game
    package by its full path, lowercased, since Blueprint names repeat across
    packages."""
    if not index:
        return None
    path = object_path(package, index, own_path)
    # Package paths are case-insensitive, and the game spells some two ways
    # ("/Game/UI/HUB/HUBScreen_UMG" for the file HubScreen_UMG).
    return path.split('.', 1)[1] if path.startswith('/Script/') else path.lower()


def _signature(key):
    """'KismetSystemLibrary:OnAssetLoaded__DelegateSignature' -> 'OnAssetLoaded'."""
    return key.split(':')[-1].replace('__DelegateSignature', '')


def property_type(p, object_name):
    """Canonical type of a cooked FProperty (`bpkit.structs.Property`).

    `object_name` maps a reference's target to the database's key for it.
    """
    t = p.type.text
    simple = {'IntProperty': 'int', 'Int8Property': 'int8', 'Int16Property': 'int16',
              'Int64Property': 'int64', 'UInt16Property': 'uint16', 'UInt32Property': 'uint32',
              'UInt64Property': 'uint64', 'FloatProperty': 'float', 'DoubleProperty': 'double',
              'BoolProperty': 'bool', 'NameProperty': 'name', 'StrProperty': 'str',
              'TextProperty': 'text', 'FieldPathProperty': 'fieldpath'}
    if t in simple:
        return simple[t]
    ref = lambda key: object_name(p.refs[key].target)          # noqa: E731
    if t == 'ByteProperty':
        enum = ref('enum')
        return 'enum:%s' % enum if enum else 'byte'
    if t == 'EnumProperty':
        return 'enum:%s' % ref('enum')
    if t == 'ObjectProperty':
        return 'object:%s' % ref('property_class')
    if t == 'ClassProperty':
        return 'class:%s' % ref('meta_class')
    if t == 'SoftObjectProperty':
        return 'softobject:%s' % ref('property_class')
    if t == 'SoftClassProperty':
        return 'softclass:%s' % ref('meta_class')
    if t == 'WeakObjectProperty':
        return 'weakobject:%s' % ref('property_class')
    if t == 'LazyObjectProperty':
        return 'lazyobject:%s' % ref('property_class')
    if t == 'InterfaceProperty':
        return 'interface:%s' % ref('interface_class')
    if t == 'StructProperty':
        return 'struct:%s' % ref('struct')
    # A delegate type is named by its signature alone, whatever declares it.
    if t == 'DelegateProperty':
        return 'delegate:%s' % _signature(ref('signature'))
    if t.startswith('Multicast'):
        return 'mcdelegate:%s' % _signature(ref('signature'))
    if t == 'ArrayProperty':
        return 'array<%s>' % property_type(p.children['inner'], object_name)
    if t == 'SetProperty':
        return 'set<%s>' % property_type(p.children['element'], object_name)
    if t == 'MapProperty':
        return 'map<%s,%s>' % (property_type(p.children['key'], object_name),
                               property_type(p.children['value'], object_name))
    return 'unknown:%s' % t


def param_entry(p, object_name):
    """A cooked parameter as the database records one."""
    f = p.property_flags
    return {'name': p.name.display(), 'type': property_type(p, object_name),
            'out': bool(f & CPF_OUT_PARM) and not f & CPF_RETURN_PARM,
            'ref': bool(f & CPF_REFERENCE_PARM), 'const': bool(f & CPF_CONST_PARM),
            'return': bool(f & CPF_RETURN_PARM)}


# -- building -----------------------------------------------------------------

# EFunctionFlags (Script.h).
FUNC_FINAL = 0x1
FUNC_BLUEPRINT_AUTHORITY_ONLY = 0x4
FUNC_BLUEPRINT_COSMETIC = 0x8
FUNC_NET = 0x40
FUNC_NET_RELIABLE = 0x80
FUNC_NATIVE = 0x400
FUNC_EVENT = 0x800
FUNC_NET_MULTICAST = 0x4000
FUNC_PUBLIC = 0x20000
FUNC_NET_SERVER = 0x200000
FUNC_HAS_OUT_PARMS = 0x400000
FUNC_NET_CLIENT = 0x1000000
FUNC_BLUEPRINT_CALLABLE = 0x4000000
FUNC_BLUEPRINT_EVENT = 0x8000000
FUNC_BLUEPRINT_PURE = 0x10000000
FUNC_CONST = 0x40000000


def native_function_flags(f, owner_kind):
    """The EFunctionFlags UHT gives a C++ UFUNCTION, as far as a call depends on them.

    Native unless BlueprintImplementableEvent; final unless virtual or an
    event; static functions are final too. Interface functions are never
    final. Network, cosmetic and authority flags from the specifiers.
    """
    spec = set(f['flags'])
    flags = FUNC_PUBLIC
    implementable = 'BlueprintImplementableEvent' in spec
    native_event = 'BlueprintNativeEvent' in spec
    if not implementable:
        flags |= FUNC_NATIVE
    if implementable or native_event:
        flags |= FUNC_EVENT | FUNC_BLUEPRINT_EVENT
    if f['static']:
        flags |= STATIC_FLAG
    if (f['static'] or not (f.get('virtual') or implementable or native_event)) \
            and owner_kind != 'interface':
        flags |= FUNC_FINAL
    if spec & {'Server', 'Client', 'NetMulticast'}:
        flags |= FUNC_NET
        flags |= {'Server': FUNC_NET_SERVER, 'Client': FUNC_NET_CLIENT,
                  'NetMulticast': FUNC_NET_MULTICAST}[next(iter(spec & {'Server', 'Client', 'NetMulticast'}))]
    if 'Reliable' in spec:
        flags |= FUNC_NET_RELIABLE
    if 'BlueprintCosmetic' in spec:
        flags |= FUNC_BLUEPRINT_COSMETIC
    if 'BlueprintAuthorityOnly' in spec:
        flags |= FUNC_BLUEPRINT_AUTHORITY_ONLY
    if 'BlueprintCallable' in spec or 'BlueprintPure' in spec:
        flags |= FUNC_BLUEPRINT_CALLABLE
    if 'BlueprintPure' in spec:
        flags |= FUNC_BLUEPRINT_PURE
    if f.get('const'):
        flags |= FUNC_CONST
    if any(p['out'] or p.get('ref') for p in f['params']) or f.get('return'):
        flags |= FUNC_HAS_OUT_PARMS
    return flags


STATIC_FLAG = 0x2000


def _native(db):
    from bpkit import headers
    raw = headers.empty()
    for mod in sorted(os.listdir(DUMP)):
        headers.parse_tree(os.path.join(DUMP, mod), mod, raw)
    for mod, dirs in ENGINE_MODULES.items():
        for d in dirs:
            headers.parse_tree(os.path.join(ENGINE, d), mod, raw)
    delegates = {}
    for name, d in raw['delegates'].items():
        kind = 'mcdelegate' if d['multicast'] else 'delegate'
        delegates[name] = '%s:%s' % (kind, _name(name))
    for name, e in raw['enums'].items():
        db['enums'][name] = {'values': e['values'], 'numbers': e['numbers'], 'kind': e['kind'],
                             'module': e['module'], 'source': 'headers'}
    for name, s in raw['structs'].items():
        db['structs'][name] = {
            'super': s['super'], 'module': s['module'], 'source': 'headers',
            'properties': {p['name']: cpp_type(p['type'], delegates) for p in s['properties']},
            'cpp': {p['name']: p['type'] for p in s['properties']}}
    for name, c in raw['classes'].items():
        if 'module' not in c:
            continue                  # functions seen for a class never declared
        db['classes'][name] = {
            'super': c['super'], 'module': c['module'], 'kind': c['kind'],
            'interfaces': c.get('interfaces', []), 'source': 'headers',
            'properties': {p['name']: cpp_type(p['type'], delegates) for p in c['properties']},
            'cpp': {p['name']: p['type'] for p in c['properties']}}
        for fname, f in c['functions'].items():
            # Custom-thunk functions declare a placeholder type (TArray<int32>,
            # FGenericStruct) for parameters that accept any type; meta names them.
            wild = set()
            for key in WILDCARD_META:
                wild.update(w.strip() for w in re.split(r'[,|]', f['meta'].get(key, '')) if w.strip())
            params = [{'name': p['name'], 'type': cpp_type(p['type'], delegates),
                       'out': p['out'] or p['ref'], 'ref': p['ref'], 'const': p['const'],
                       'return': False, 'default': p['default'],
                       'wildcard': p['name'] in wild} for p in f['params']]
            if f['return']:
                params.append({'name': 'ReturnValue', 'type': cpp_type(f['return'], delegates),
                               'out': False, 'ref': False, 'const': False, 'return': True})
            db['functions']['%s:%s' % (name, fname)] = {
                'owner': name, 'static': f['static'], 'specifiers': f['flags'], 'meta': f['meta'],
                'function_flags': native_function_flags(f, c.get('kind')),
                'params': params, 'source': 'headers'}
    for name, d in raw['delegates'].items():
        params = [{'name': p['name'], 'type': cpp_type(p['type'], delegates), 'out': False,
                   'ref': False, 'const': False, 'return': False} for p in d['params']]
        db['delegates'][_name(name)] = {'params': params, 'multicast': d['multicast'],
                                        'owner': d['owner'], 'module': d['module']}


def _stock_module_dirs():
    """{module: [source dir]} for every module the stock engine has source for,
    found by its <Module>.Build.cs, however deeply it is nested."""
    out = {}
    engine = os.path.dirname(os.path.dirname(ENGINE))
    for root in (os.path.join(engine, 'Source'), os.path.join(engine, 'Plugins')):
        for base, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith('.Build.cs'):
                    out.setdefault(f[:-len('.Build.cs')], []).append(base)
    return out


def _members(db, cls):
    """Lowercased property names of a class and its ancestors."""
    out, seen = set(), set()
    while cls and cls not in seen:
        seen.add(cls)
        c = db['classes'].get(cls) or {}
        out |= {k.lower() for k in c.get('properties', {})}
        out |= {k.lower() for k in c.get('usmap_properties', [])}
        cls = c.get('super') or c.get('usmap_super')
    return out


def _fix_param_names(db):
    """Undo the UHT dump's renaming of parameters.

    The dump is generated as compilable C++, where a parameter may not share a
    class member's name, so a colliding parameter is written with a "New"
    prefix: the engine's `SetNativeID(FGuid NativeID)` is `NewNativeID` in the
    dump. Bytecode names the real parameter. Every spelling is kept: the most
    reliable is `name`, the rest are `aliases`.

    * a stock module: the engine's own source has the real names, and is used
      wherever it declares the same function with as many parameters;
    * the dump also drops `virtual`, which decides whether a call is final; a
      stock module's own source restores it;
    * a module without source (OMD's own): "NewX" whose X is a member of the
      class, and whose text appears nowhere in the game's executable, was
      renamed, so X is the name; if "NewX" is in the executable (the string
      pool is shared, so that proves nothing), it stays and X is an alias.
    """
    from bpkit import headers
    stock_dirs = _stock_module_dirs()
    stock = headers.empty()
    for mod in {c['module'] for c in db['classes'].values()
                if c.get('source') == 'headers' and c.get('module') not in ENGINE_MODULES}:
        for d in stock_dirs.get(mod, ()):
            headers.parse_tree(d, mod, stock)
    with open(os.path.join(WIN64, 'OMD-Win64-Shipping.exe'), 'rb') as fh:
        exe = fh.read()
    in_exe = lambda text: (b'\0' + text.encode() + b'\0') in exe          # noqa: E731
    for key, f in db['functions'].items():
        if f.get('source') != 'headers':
            continue
        owner, fname = key.split(':', 1)
        module = db['classes'].get(owner, {}).get('module')
        if module in ENGINE_MODULES:
            continue                    # already read from the engine's source
        real = stock['classes'].get(owner, {}).get('functions', {}).get(fname)
        if real and real.get('virtual') and not real.get('static') and \
                f['function_flags'] & FUNC_FINAL:
            f['function_flags'] &= ~FUNC_FINAL
            f['virtual_from'] = 'engine source'
        params = [q for q in f['params'] if not q['return']]
        if real and len(real['params']) == len(params):
            for q, r in zip(params, real['params']):
                if r['name'] and r['name'].lower() != q['name'].lower():
                    q['aliases'] = [q['name']]
                    q['name'] = r['name']
            f['names_from'] = 'engine source'
            continue
        members = None
        for q in params:
            if not (q['name'].startswith('New') and len(q['name']) > 3):
                continue
            bare = q['name'][3:]
            members = members if members is not None else _members(db, owner)
            if bare.lower() not in members:
                continue
            if in_exe(q['name']):
                q['aliases'] = [bare]
            else:
                q['aliases'] = [q['name']]
                q['name'] = bare


def _usmap(db):
    """Fold in the live reflection data: properties and enums the headers missed."""
    sys.path.insert(0, ROOT)
    import usmap
    data = open(USMAP, 'rb').read()
    body = data[12:12 + struct.unpack_from('<I', data, 8)[0]]
    r = usmap.R(body)
    names = [r.name() for _ in range(r.i32())]
    for _ in range(r.i32()):
        ename = names[r.i32()]
        values = [names[r.i32()] for _ in range(r.u8())]
        e = db['enums'].setdefault(ename, {'values': values, 'source': 'usmap'})
        e['usmap'] = values
    for _ in range(r.i32()):
        cname = names[r.i32()]
        sup_i = r.i32()
        sup = names[sup_i] if 0 <= sup_i < len(names) else None
        r.u16()
        props = {}
        for _ in range(r.u16()):
            r.u16()
            r.u8()
            pname = names[r.i32()]
            props[pname] = usmap._type(r, names)
        table = db['classes'] if cname in db['classes'] else (
            db['structs'] if cname in db['structs'] else None)
        if table is None:
            # Not declared in any header read: a class or struct the game
            # reflects that we know only from usmap.
            table = db['usmap_only']
            table[cname] = {'super': sup, 'properties': {}}
        entry = table[cname]
        entry['usmap_super'] = sup
        entry['usmap_properties'] = sorted(props)


def _blueprints(db):
    """Every Blueprint generated class in the game, decoded; and every native
    function the game's bytecode calls by name made virtual (not final).

    The UHT dump drops `virtual`, and OMD's own modules have no source to
    restore it from; the Blueprint compiler calls only a non-final function by
    name, so such a call proves it. Applied to every native function of the
    name except those the bytecode calls by reference outside a parent call,
    which proves them final; calling a final one by name still runs it."""
    sys.path.insert(0, os.path.join(ROOT, 'UE Assets'))
    import gamefiles
    from bpkit.check_structs import kind_of
    from bpkit.package import Package
    from bpkit.structs import decode_export
    os.chdir(ROOT)
    by_name = set()
    by_reference = set()
    for key in gamefiles.index():
        if not key.endswith('.uasset'):
            continue
        ua = gamefiles.read(key)
        if b'BlueprintGeneratedClass' not in ua and b'UserDefinedStruct' not in ua:
            continue
        p = Package.read(ua, gamefiles.read(key[:-7] + '.uexp'))
        add_package(db, p, '/Game/' + key.split('/Content/', 1)[1][:-len('.uasset')], by_name,
                    by_reference)
    for key, f in db['functions'].items():
        if f.get('source') == 'headers' and key.split(':', 1)[1].lower() in by_name \
                and key not in by_reference \
                and f['function_flags'] & FUNC_FINAL and not f['function_flags'] & STATIC_FLAG:
            f['function_flags'] &= ~FUNC_FINAL
            f['virtual_from'] = 'game bytecode'


def add_package(db, p, path, by_name=None, by_reference=None):
    """Add one package's Blueprint classes, structs and functions to `db`.
    `by_name` collects the names its bytecode calls with VirtualFunction,
    `by_reference` the functions it calls with FinalFunction, other than an
    override calling its parent's version."""
    from bpkit.check_structs import kind_of
    from bpkit.structs import decode_export
    if True:
        names = [str(n) for n in p.names]
        decoded = {}
        own = {p.object_name(k) for k, x in enumerate(p.exports, 1)
               if p.object_name(x.class_index) == 'Function'}
        for i, e in enumerate(p.exports, 1):
            kind = kind_of(p.object_name(e.class_index) or 'Class')
            if not kind:
                continue
            try:
                decoded[i] = (kind[0], decode_export(e.data, kind[0], names, lambda x: x,
                                                     kind[1]), e)
            except Exception:
                continue
            if by_name is not None:
                caller = p.object_name(i)
                for top in decoded[i][1].script.nodes:
                    for n in top.walk():
                        if n.name == 'VirtualFunction':
                            by_name.add(names[n.ops[0][1][0]].lower())
                        elif n.name == 'FinalFunction':
                            # an event's parent call is made from the ubergraph,
                            # to a function named like one of the class's events
                            target = p.object_name(n.ops[0][1])
                            if target == caller or (caller.startswith('ExecuteUbergraph')
                                                    and target in own):
                                continue
                            by_reference.add(object_key(p, n.ops[0][1], path))
        key = lambda idx: object_key(p, idx, path)          # noqa: E731
        tname = lambda idx: object_type_name(p, idx, path)  # noqa: E731
        for i, (kind, x, e) in decoded.items():
            # Element sizes of the structs the game's Blueprints hold, which a
            # compiled property must state and nothing else records.
            for q in x.properties:
                if q.type.text == 'StructProperty':
                    db['struct_sizes'][object_type_name(p, q.refs['struct'].target, path)] = q.element_size
            if kind == 'struct':
                db['structs'][key(i)] = {
                    'super': key(e.super_index), 'source': 'blueprint', 'path': tname(i),
                    'properties': {q.name.display(): property_type(q, tname) for q in x.properties}}
            if kind != 'class':
                continue
            db['classes'][key(i)] = {
                'super': key(e.super_index), 'kind': 'blueprint', 'source': 'blueprint',
                'path': tname(i), 'generated': p.object_name(e.class_index),
                'properties': {q.name.display(): property_type(q, tname) for q in x.properties}}
        for i, (kind, x, e) in decoded.items():
            if kind != 'function':
                continue
            owner = key(e.outer_index)
            params = [param_entry(q, tname) for q in x.properties if q.property_flags & CPF_PARM]
            locals_ = {q.name.display(): property_type(q, tname) for q in x.properties
                       if not q.property_flags & CPF_PARM}
            db['functions'][key(i)] = {
                'owner': owner, 'static': bool(x.fields['function_flags'] & FUNC_STATIC),
                'function_flags': x.fields['function_flags'], 'params': params,
                'locals': locals_, 'source': 'blueprint'}


def build():
    db = {'classes': {}, 'structs': {}, 'enums': {}, 'functions': {}, 'delegates': {},
          'usmap_only': {}, 'struct_sizes': {}}
    _native(db)
    _usmap(db)
    _fix_param_names(db)
    _blueprints(db)
    return db


_SHARED = None


def shared():
    """The database one process builds against, loaded once: assets compiled
    earlier in a build are added to it, so later ones can name them."""
    global _SHARED
    if _SHARED is None:
        _SHARED = load()
    return _SHARED


#: Element sizes of structs no Blueprint in the game holds, so the measured
#: table (struct_sizes) cannot know them; from the 4.27.2 source's members.
SOURCE_STRUCT_SIZES = {
    'QualifiedFrameTime': 16,   # FFrameTime (int32 frame, float subframe) + FFrameRate (2 x int32)
    'FrameTime': 8,
    'FrameRate': 8,
    'Box2D': 20,                # 2 x FVector2D + uint8 bIsValid, padded to 4
    'AnchorData': 40,           # FMargin Offsets + FAnchors (2 x FVector2D) + FVector2D Alignment
    'ScrollBarStyle': 1448,     # FSlateWidgetStyle's vtable + 9 FSlateBrush (160, measured)
    'ProgressBarStyle': 488,    # FSlateWidgetStyle's vtable + 3 FSlateBrush (160)
    'Vector4': 16,              # 4 x float
    'SoftObjectPath': 24, 'SoftClassPath': 24, 'OMDMapColors': 56, 'OMDUpgradeModuleData': 72,       # FName AssetPathName + FString SubPathString
    'DrawToRenderTargetContext': 16,  # UTextureRenderTarget2D* + FDrawEvent*
    'SlateChildSize': 8,        # TEnumAsByte SizeRule + float Value
    'OMDRandomModeModifierEntry': 20,  # float Weight + FOMDSoftProtoPtr (FGuid); the roll steps by 0x14
    'OMDInventoryItemUpgrades': 32,    # 2 x TArray<FOMDSoftProtoPtr>; the save's Inventory map steps by 0x38
    # read from the exe's FStructParams (python3 -m bpkit.structsize): the
    # shipping sizes. The measured table can hold larger, editor-built sizes
    # (OMDPlayerCharacterProto 576 measured, 496 in the exe); the engine
    # relinks every struct property's size and offset as it loads a class.
    'OMDPlayerAnimationSet': 528,
}


#: Struct sizes read from the exe (native_size), kept between builds.
NATIVE_SIZES = os.path.join(os.path.dirname(CACHE), 'native_struct_sizes.json')


def native_size(db, name):
    """A struct's size when neither the measured table nor SOURCE_STRUCT_SIZES
    has it: read from the exe's FStructParams (bpkit.structsize), cached in
    NATIVE_SIZES and added to db. None when the exe does not name it once."""
    known = json.load(open(NATIVE_SIZES)) if os.path.exists(NATIVE_SIZES) else {}
    if name not in known:
        from bpkit import structsize
        data = open(structsize.EXE, 'rb').read()
        base, secs = structsize.sections(data)
        found = structsize.measure(data, base, secs, name)
        known[name] = found[0][0] if len(found) == 1 else None
        os.makedirs(os.path.dirname(NATIVE_SIZES), exist_ok=True)
        with open(NATIVE_SIZES, 'w') as fh:
            json.dump(known, fh, indent=1, sort_keys=True)
    if known[name] is not None:
        db['struct_sizes'][name] = known[name]
    return known[name]


def load(rebuild=False):
    if not rebuild and os.path.exists(CACHE):
        with open(CACHE) as fh:
            db = json.load(fh)
        for name, size in SOURCE_STRUCT_SIZES.items():
            db['struct_sizes'].setdefault(name, size)
        return db
    db = build()
    for name, size in SOURCE_STRUCT_SIZES.items():
        db['struct_sizes'].setdefault(name, size)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, 'w') as fh:
        json.dump(db, fh)
    return db


if __name__ == '__main__':
    db = load(rebuild='--rebuild' in sys.argv)
    for k in ('classes', 'structs', 'enums', 'functions', 'delegates', 'usmap_only'):
        print('%-10s %d' % (k, len(db[k])))
