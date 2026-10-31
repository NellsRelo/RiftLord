"""The type database bpkit.typedb.load() reads, made from the three caches:

    engine      Engine and CoreUObject from the user's UE headers (riftlord.types.engine)
    game        every other module from the game's exe (riftlord.types.exe)
    blueprints  the game's Blueprints from its paks (riftlord.types.blueprints)

    db, replicated, sizes = merge.build(engine, game, blueprints)

The steps follow bpkit.typedb.build(): native declarations, then the live reflection
(the usmap_* fields, here from the exe), then Blueprints. Engine entries are made as
bpkit's _native makes them; game entries carry the exe's own names, flags and types.
Every native entry keeps bpkit's 'source': 'headers' and says where it came from in
'from' ('ue headers' or 'exe').

`replicated` is bpkit.replication's {class: {'super', 'replicated'}}; `sizes` is
bpkit.typedb's NATIVE_SIZES table, {struct: size or None}, for every struct the exe names.
"""

import re

#: The modules read from the engine headers (bpkit.typedb.ENGINE_MODULES).
ENGINE_MODULES = ('Engine', 'CoreUObject')
#: Meta keys naming parameters whose declared type is only a placeholder.
WILDCARD_META = ('ArrayParm', 'ArrayTypeDependentParams', 'CustomStructureParam',
                 'MapParam', 'MapKeyParam', 'MapValueParam', 'SetParam')

# EPropertyFlags
CPF_CONST_PARM = 0x2
CPF_NET = 0x20
CPF_PARM = 0x80
CPF_OUT_PARM = 0x100
CPF_RETURN_PARM = 0x400
CPF_REFERENCE_PARM = 0x8000000
# EFunctionFlags
FUNC_FINAL = 0x1
FUNC_AUTHORITY_ONLY = 0x4
FUNC_COSMETIC = 0x8
FUNC_NET = 0x40
FUNC_RELIABLE = 0x80
FUNC_EXEC = 0x200
FUNC_NATIVE = 0x400
FUNC_EVENT = 0x800
FUNC_STATIC = 0x2000
FUNC_MULTICAST = 0x4000
FUNC_MULTICAST_DELEGATE = 0x10000
FUNC_SERVER = 0x200000
FUNC_CLIENT = 0x1000000
FUNC_CALLABLE = 0x4000000
FUNC_BP_EVENT = 0x8000000
FUNC_PURE = 0x10000000
FUNC_VALIDATE = 0x80000000
# EClassFlags
CLASS_INTERFACE = 0x4000
#: The C++ text of each property kind with no reference.
CPP_SIMPLE = {'Int8': 'int8', 'Int16': 'int16', 'Int': 'int32', 'Int64': 'int64',
              'UInt16': 'uint16', 'UInt32': 'uint32', 'UInt64': 'uint64', 'UnsizedInt': 'int32',
              'UnsizedUInt': 'uint32', 'Float': 'float', 'Double': 'double', 'Bool': 'bool',
              'Name': 'FName', 'Str': 'FString', 'Text': 'FText',
              'FieldPath': 'TFieldPath<FProperty>'}
DELEGATE_SUFFIX = '__DelegateSignature'


def _typedb():
    from riftlord.core import bpkitlink
    bpkitlink.install()
    from bpkit import typedb
    return typedb


# -- engine part (bpkit.typedb._native over the engine headers alone) -------------------

def _engine_native(db, raw, delegates):
    typedb = _typedb()
    cpp_type, native_function_flags = typedb.cpp_type, typedb.native_function_flags
    for name, e in raw['enums'].items():
        db['enums'][name] = {'values': e['values'], 'numbers': e['numbers'], 'kind': e['kind'],
                             'module': e['module'], 'source': 'headers', 'from': 'ue headers'}
    for name, s in raw['structs'].items():
        db['structs'][name] = {
            'super': s['super'], 'module': s['module'], 'source': 'headers',
            'from': 'ue headers',
            'properties': {p['name']: cpp_type(p['type'], delegates) for p in s['properties']},
            'cpp': {p['name']: p['type'] for p in s['properties']}}
    for name, c in raw['classes'].items():
        if 'module' not in c:
            continue
        db['classes'][name] = {
            'super': c['super'], 'module': c['module'], 'kind': c['kind'],
            'interfaces': c.get('interfaces', []), 'source': 'headers', 'from': 'ue headers',
            'properties': {p['name']: cpp_type(p['type'], delegates) for p in c['properties']},
            'cpp': {p['name']: p['type'] for p in c['properties']}}
        for fname, f in c['functions'].items():
            wild = set()
            for key in WILDCARD_META:
                wild.update(w.strip() for w in re.split(r'[,|]', f['meta'].get(key, ''))
                            if w.strip())
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
                'params': params, 'source': 'headers', 'from': 'ue headers'}
    for name, d in raw['delegates'].items():
        params = [{'name': p['name'], 'type': cpp_type(p['type'], delegates), 'out': False,
                   'ref': False, 'const': False, 'return': False} for p in d['params']]
        db['delegates'][typedb._name(name)] = {'params': params, 'multicast': d['multicast'],
                                               'owner': d['owner'], 'module': d['module']}


# -- game part (the exe's reflection) ---------------------------------------------------

def _is_actor(game, engine_raw, name):
    seen = set()
    while name and name not in seen:
        if name == 'Actor':
            return True
        seen.add(name)
        c = game['classes'].get(name) or engine_raw['classes'].get(name) or {}
        name = c.get('super')
    return False


def delegate_owners(game):
    """{signature function name: the class declaring it, or None}."""
    out = {}
    for key, d in game['delegates'].items():
        out.setdefault(key.split(':', 1)[1], d['owner'])
    return out


def cpp_text(p, game, engine_raw, where=None, owners=None):
    """C++ text of an exe property of class or struct `where`, written as the UHT dump
    writes it."""
    k = p['kind']
    owners = owners if owners is not None else delegate_owners(game)

    def cls(name):
        if not name:
            return 'UObject'
        return ('A' if _is_actor(game, engine_raw, name) else 'U') + name

    def enum(name):
        return (game['enums'].get(name) or {}).get('cpp') or name

    def inner(q):
        return cpp_text(q, game, engine_raw, where, owners)

    if k in CPP_SIMPLE:
        return CPP_SIMPLE[k]
    if k == 'Byte':
        return 'TEnumAsByte<%s>' % enum(p['enum']) if p.get('enum') else 'uint8'
    if k == 'Enum':
        return enum(p.get('enum')) if p.get('enum') else 'uint8'
    if k == 'Object':
        return '%s*' % cls(p.get('class'))
    if k == 'Class':
        if p.get('meta_class') in (None, 'Object'):
            return 'UClass*'
        return 'TSubclassOf<%s>' % cls(p.get('meta_class'))
    if k == 'SoftClass':
        return 'TSoftClassPtr<%s>' % cls(p.get('meta_class'))
    if k in ('SoftObject', 'WeakObject', 'LazyObject'):
        return 'T%sPtr<%s>' % (k, cls(p.get('class')))
    if k == 'Interface':
        return 'TScriptInterface<I%s>' % p.get('class')
    if k == 'Struct':
        return 'F%s' % p.get('struct')
    if k in ('Delegate', 'InlineMulticastDelegate', 'SparseMulticastDelegate'):
        sig = p.get('signature') or ''
        owner = owners.get(sig)
        scope = '%s::' % cls(owner) if owner and owner != where else ''
        return scope + 'F%s' % sig.replace(DELEGATE_SUFFIX, '')
    if k == 'Array':
        return 'TArray<%s>' % inner(p['inner'])
    if k == 'Set':
        return 'TSet<%s>' % inner(p['element'])
    if k == 'Map':
        return 'TMap<%s, %s>' % (inner(p['key']), inner(p['value']))
    return k


def specifiers(flags):
    """UFUNCTION specifiers of an exe function's flags."""
    out = []
    if flags & FUNC_CALLABLE:
        out.append('BlueprintCallable')
    if flags & FUNC_PURE:
        out.append('BlueprintPure')
    if flags & FUNC_BP_EVENT:
        out.append('BlueprintNativeEvent' if flags & FUNC_NATIVE else 'BlueprintImplementableEvent')
    if flags & FUNC_SERVER:
        out.append('Server')
    if flags & FUNC_CLIENT:
        out.append('Client')
    if flags & FUNC_MULTICAST:
        out.append('NetMulticast')
    if flags & FUNC_NET:
        out.append('Reliable' if flags & FUNC_RELIABLE else 'Unreliable')
    if flags & FUNC_VALIDATE:
        out.append('WithValidation')
    if flags & FUNC_AUTHORITY_ONLY:
        out.append('BlueprintAuthorityOnly')
    if flags & FUNC_COSMETIC:
        out.append('BlueprintCosmetic')
    if flags & FUNC_EXEC:
        out.append('Exec')
    return sorted(out)


def meta(params):
    """The meta keys a call depends on, from parameter names (shipping exes carry none)."""
    names = {p['name'].lower(): p['name'] for p in params}
    out = {}
    for world in ('worldcontextobject', 'worldcontext'):
        if world in names:
            out['WorldContext'] = names[world]
            break
    if 'latentinfo' in names:
        out['Latent'] = ''
        out['LatentInfo'] = names['latentinfo']
    return out


def game_param(p):
    """An exe parameter as bpkit.typedb records a native one (a string passed by value
    is const, as the UHT dump writes it)."""
    f = p['flags']
    ret = bool(f & CPF_RETURN_PARM)
    const = bool(f & CPF_CONST_PARM) or (p['type'] == 'str' and not f & (
        CPF_OUT_PARM | CPF_REFERENCE_PARM))
    out = {'name': p['name'], 'type': p['type'],
           'out': bool(f & CPF_OUT_PARM) and not ret and not const,
           'ref': bool(f & CPF_REFERENCE_PARM) and not ret and not const,
           'const': const and not ret, 'return': ret}
    if not ret:
        out['default'] = None
        out['wildcard'] = False
    return out


def _game_function(owner, f):
    params = [game_param(p) for p in f['params']]
    params = [q for q in params if not q['return']] + [q for q in params if q['return']]
    keys = meta(params)
    return {'owner': owner, 'static': bool(f['flags'] & FUNC_STATIC),
            'specifiers': sorted(specifiers(f['flags']) + list(keys)), 'meta': keys,
            'function_flags': f['flags'], 'params': params, 'source': 'headers', 'from': 'exe'}


def runtime_values(name, values, form):
    """The enumerators' short names the game holds at run time: the exe's, then the
    <prefix>_MAX that UEnum::SetEnums adds unless the enum already has it (any case)."""
    def full(text):
        return text if form == 0 or '::' in text else '%s::%s' % (name, text)

    prefix = values[0] if values else ''
    for v in values[1:]:
        while prefix and not v.startswith(prefix):
            prefix = prefix[:-1]
    cut = prefix.rfind('_')
    prefix = prefix[:cut] if cut > 0 else ''
    prefix = prefix or name
    have = {v.lower() for v in values}
    short = [v.split('::')[-1] for v in values]
    if full('MAX').lower() in have or full(prefix + '_MAX').lower() in have:
        return short
    return short + [full(prefix + '_MAX').split('::')[-1]]


def _dump_values(name, e):
    """(values, numbers) as the UHT dump writes an enum: the run-time list less a last
    enumerator ending in _MAX (any case)."""
    values = runtime_values(name, e['values'], e['form'])
    if values and values[-1].lower().endswith('_max'):
        values = values[:-1]
    return values, e['numbers'][:len(values)]


def _game_native(db, game, engine_raw):
    kinds = {0: 'plain', 1: 'namespace', 2: 'class'}
    owners = delegate_owners(game)
    for name, e in game['enums'].items():
        if e['module'] in ENGINE_MODULES:
            continue
        values, numbers = _dump_values(name, e)
        db['enums'][name] = {'values': values, 'numbers': numbers, 'kind': kinds[e['form']],
                             'module': e['module'], 'source': 'headers', 'from': 'exe'}
    for name, s in game['structs'].items():
        if s['module'] in ENGINE_MODULES:
            continue
        db['structs'][name] = {
            'super': s['super'], 'module': s['module'], 'source': 'headers', 'from': 'exe',
            'properties': {p['name']: p['type'] for p in s['properties']},
            'cpp': {p['name']: cpp_text(p, game, engine_raw, name, owners)
                    for p in s['properties']}}
    for name, c in game['classes'].items():
        if c['module'] in ENGINE_MODULES:
            continue
        db['classes'][name] = {
            'super': c['super'], 'module': c['module'],
            'kind': 'interface' if c['flags'] & CLASS_INTERFACE else 'class',
            'interfaces': [i['class'] for i in c['interfaces']], 'source': 'headers',
            'from': 'exe',
            'properties': {p['name']: p['type'] for p in c['properties']},
            'cpp': {p['name']: cpp_text(p, game, engine_raw, name, owners)
                    for p in c['properties']}}
    for key, f in game['functions'].items():
        owner = f['owner']
        if (game['classes'].get(owner) or {}).get('module') in ENGINE_MODULES:
            continue
        db['functions'][key] = _game_function(owner, f)
    # an interface's native functions, declared again on each native class that
    # implements it directly (as the UHT dump writes them)
    for name, c in game['classes'].items():
        if c['module'] in ENGINE_MODULES:
            continue
        for i in c['interfaces']:
            face = game['classes'].get(i['class']) or {}
            if i['k2']:
                continue
            for fname in face.get('functions', ()):
                f = game['functions'].get('%s:%s' % (i['class'], fname))
                if not f or f['flags'] & FUNC_BP_EVENT or '%s:%s' % (name, fname) in db['functions']:
                    continue
                entry = _game_function(name, f)
                entry['interface'] = i['class']
                db['functions']['%s:%s' % (name, fname)] = entry
    for key, d in game['delegates'].items():
        module = d['module'] or (game['classes'].get(d['owner']) or {}).get('module')
        if module in ENGINE_MODULES:
            continue
        sig = key.split(':', 1)[1]
        params = [{'name': p['name'], 'type': p['type'], 'out': False, 'ref': False,
                   'const': False, 'return': False}
                  for p in d['params'] if not p['flags'] & CPF_RETURN_PARM]
        db['delegates'][sig[:-len(DELEGATE_SUFFIX)]] = {
            'params': params, 'multicast': bool(d['flags'] & FUNC_MULTICAST_DELEGATE),
            'owner': d['owner'], 'module': module}


def delegate_types(engine_raw, game):
    """{C++ delegate name: canonical type} for bpkit.typedb.cpp_type."""
    typedb = _typedb()
    out = {}
    for key, d in game['delegates'].items():
        sig = key.split(':', 1)[1][:-len(DELEGATE_SUFFIX)]
        kind = 'mcdelegate' if d['flags'] & FUNC_MULTICAST_DELEGATE else 'delegate'
        out['F' + sig] = '%s:%s' % (kind, sig)
    for name, d in engine_raw['delegates'].items():
        kind = 'mcdelegate' if d['multicast'] else 'delegate'
        out[name] = '%s:%s' % (kind, typedb._name(name))
    return out


# -- live reflection (bpkit.typedb._usmap, from the exe) ---------------------------------

def _reflection(db, game, engine_raw):
    for name, e in game['enums'].items():
        short = runtime_values(name, e['values'], e['form'])
        entry = db['enums'].setdefault(name, {'values': short, 'source': 'usmap'})
        entry['usmap'] = short
    tables = list(game['classes'].items()) + list(game['structs'].items())
    # a deprecated class runs under its name without DEPRECATED_
    tables += [(name[len('DEPRECATED_'):], c) for name, c in game['classes'].items()
               if name.startswith('DEPRECATED_')]
    for name, c in tables:
        table = db['classes'] if name in db['classes'] else (
            db['structs'] if name in db['structs'] else None)
        if table is None:
            table = db['usmap_only']
            table[name] = {'super': c['super'], 'properties': {}}
        entry = table[name]
        entry['usmap_super'] = c['super']
        entry['usmap_properties'] = sorted(p['name'] for p in c['properties'])
    for name, sup in sorted(engine_raw.get('intrinsics', {}).items()):
        known = db['classes'].get(name) or db['structs'].get(name) or db['usmap_only'].get(name)
        if known is not None:
            if 'usmap_super' not in known:
                known['usmap_super'] = sup
                known['usmap_properties'] = []
            continue
        db['usmap_only'][name] = {'super': sup, 'properties': {}, 'usmap_super': sup,
                                  'usmap_properties': [], 'from': 'ue headers'}


# -- Blueprints (bpkit.typedb._blueprints, from blueprints.json) -------------------------

def _blueprints(db, bp):
    typedb = _typedb()
    for table in ('classes', 'structs', 'functions'):
        db[table].update(bp[table])
    db['struct_sizes'].update(bp['struct_sizes'])
    by_name = set(bp['virtual_names'])
    by_reference = set(bp['final_references'])
    for key, f in db['functions'].items():
        if f.get('from') != 'ue headers':
            continue
        if key.split(':', 1)[1].lower() in by_name and key not in by_reference \
                and f['function_flags'] & typedb.FUNC_FINAL \
                and not f['function_flags'] & typedb.STATIC_FLAG:
            f['function_flags'] &= ~typedb.FUNC_FINAL
            f['virtual_from'] = 'game bytecode'


# -- the whole --------------------------------------------------------------------------

def build(engine, game, bp):
    """(typedb, replicated, native sizes) from the three caches' contents."""
    raw = engine['raw']
    db = {'classes': {}, 'structs': {}, 'enums': {}, 'functions': {}, 'delegates': {},
          'usmap_only': {}, 'struct_sizes': {}}
    delegates = delegate_types(raw, game)
    _game_native(db, game, raw)
    _engine_native(db, raw, delegates)
    _reflection(db, game, raw)
    _blueprints(db, bp)
    return db, replicated(raw, game), native_sizes(game)


def replicated(raw, game):
    """bpkit.replication's native table: engine classes from the headers, the rest
    from the exe's CPF_Net properties."""
    out = {}
    for name, c in game['classes'].items():
        if c['module'] in ENGINE_MODULES:
            continue
        out[name] = {'super': c['super'],
                     'replicated': [p['name'] for p in c['properties'] if p['flags'] & CPF_NET]}
    for name, c in raw['classes'].items():
        own = [p['name'] for p in c.get('properties', ())
               if any(s.lower().startswith('replicated') for s in p['specifiers'])]
        out[name] = {'super': c.get('super'), 'replicated': own}
    return out


def native_sizes(game):
    """{struct: size} for every struct the exe names once; None for a name it gives twice."""
    out = {name: s['size'] for name, s in game['structs'].items()}
    for name in game['duplicates']['structs']:
        out[name] = None
    return out
