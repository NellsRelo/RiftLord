"""Network replication: what the Blueprint compiler writes for replicated
variables and RPCs, and the class data the engine replicates them from.

A replicated variable is an FProperty with CPF_Net (plus CPF_RepNotify and
RepNotifyFunc for a RepNotify), BlueprintReplicationCondition (an
ELifetimeCondition) and RepIndex, its place in the class's ClassReps: the
parent's replicated property count plus its rank among the class's own, in
property order (UClass::SetUpRuntimeReplicationData). The class records how
many it adds as the tagged property NumReplicatedProperties, which
UBlueprintGeneratedClass::GetLifetimeBlueprintReplicationList reads: without
it nothing the Blueprint adds replicates.

An RPC is a function with FUNC_Net and one of NetServer, NetClient or
NetMulticast (plus NetReliable), written with its int16 RepOffset (0).

    replicated_count(db, key)    # ClassReps size of a class (native or Blueprint)
"""

import json
import os

# EPropertyFlags
CPF_NET = 0x20
CPF_REPNOTIFY = 0x100000000
# EFunctionFlags
FUNC_NET = 0x40
FUNC_NET_RELIABLE = 0x80
FUNC_NET_MULTICAST = 0x4000
FUNC_NET_SERVER = 0x200000
FUNC_NET_CLIENT = 0x1000000
FUNC_NET_FUNC_FLAGS = FUNC_NET | FUNC_NET_RELIABLE | FUNC_NET_MULTICAST | FUNC_NET_SERVER | \
    FUNC_NET_CLIENT
#: A custom event's flags (BlueprintCallable | BlueprintEvent): no access specifier.
EVENT_FLAGS = 0xc000000
#: Decorator -> the RPC kind's flag.
RPC_KINDS = {'server': FUNC_NET_SERVER, 'client': FUNC_NET_CLIENT,
             'multicast': FUNC_NET_MULTICAST}

#: Actor description keys -> the Actor property each sets on the CDO.
ACTOR_KEYS = {'replicates': 'bReplicates', 'replicate_movement': 'bReplicateMovement',
              'always_relevant': 'bAlwaysRelevant',
              'only_relevant_to_owner': 'bOnlyRelevantToOwner',
              'net_use_owner_relevancy': 'bNetUseOwnerRelevancy',
              'net_load_on_client': 'bNetLoadOnClient',
              'net_update_frequency': 'NetUpdateFrequency',
              'min_net_update_frequency': 'MinNetUpdateFrequency',
              'net_priority': 'NetPriority', 'net_dormancy': 'NetDormancy',
              'net_cull_distance_squared': 'NetCullDistanceSquared'}

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.path.dirname(HERE), 'cache', 'replicated.json')

_native = None
_blueprint = {}


def condition(text):
    """An ELifetimeCondition by name, 'OwnerOnly' or 'COND_OwnerOnly' -> its number."""
    from bpkit.typedb import shared
    enum = shared()['enums']['ELifetimeCondition']
    name = text if text.startswith('COND_') else 'COND_' + text
    if name not in enum['values'] or name == 'COND_Max':
        raise ValueError('no replication condition %s (%s)' % (
            text, ', '.join(v[5:] for v in enum['values'] if v != 'COND_Max')))
    return enum['numbers'][enum['values'].index(name)]


def native_replicated(rebuild=False):
    """{native class: [its own replicated property names]}, from the headers
    the type database reads (UPROPERTY Replicated / ReplicatedUsing). Cached."""
    global _native
    if _native is not None and not rebuild:
        return _native
    if os.path.exists(CACHE) and not rebuild:
        with open(CACHE) as fh:
            _native = json.load(fh)
        return _native
    from bpkit import headers, typedb
    raw = headers.empty()
    for mod in sorted(os.listdir(typedb.DUMP)):
        if os.path.isdir(os.path.join(typedb.DUMP, mod)):
            headers.parse_tree(os.path.join(typedb.DUMP, mod), mod, raw)
    for mod, dirs in typedb.ENGINE_MODULES.items():
        for d in dirs:
            headers.parse_tree(os.path.join(typedb.ENGINE, d), mod, raw)
    _native = {}
    for name, c in raw['classes'].items():
        own = [p['name'] for p in c.get('properties', ())
               if any(s.lower().startswith('replicated') for s in p['specifiers'])]
        _native[name] = {'super': c.get('super'), 'replicated': own}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, 'w') as fh:
        json.dump(_native, fh)
    return _native


def register(key, count):
    """Record how many replicated properties a Blueprint built in this run adds."""
    _blueprint[key.lower()] = count


def _own_blueprint_count(entry):
    """Replicated properties a game Blueprint class adds, read from its package."""
    import struct as _struct
    import sys
    sys.path.insert(0, os.path.dirname(HERE))
    import gamefiles
    from bpkit.check_structs import kind_of
    from bpkit.package import Package
    from bpkit.structs import decode_export
    pkg = entry['path'].rsplit('.', 1)[0]
    stem = '../../../OMD/Content/' + pkg[len('/Game/'):]
    index = gamefiles.index()
    found = stem + '.uasset' if stem + '.uasset' in index else next(
        (k for k in index if k.lower() == (stem + '.uasset').lower()), None)
    if found is None:
        raise ValueError('no package for %s' % entry['path'])
    cwd = os.getcwd()
    os.chdir(os.path.dirname(HERE))
    try:
        p = Package.read(gamefiles.read(found), gamefiles.read(found[:-7] + '.uexp'))
    finally:
        os.chdir(cwd)
    names = [str(n) for n in p.names]
    short = entry['path'].rsplit('.', 1)[1]
    for i, e in enumerate(p.exports, 1):
        k = kind_of(p.object_name(e.class_index) or 'Class')
        if k and k[0] == 'class' and p.object_name(i) == short:
            x = decode_export(e.data, 'class', names, lambda v: v)
            return sum(q.array_dim for q in x.properties if q.property_flags & CPF_NET)
    raise ValueError('no class %s in its package' % short)


def replicated_count(db, key):
    """The size of a class's ClassReps: every replicated property element
    declared by it and its ancestors. `key` is a type database class key."""
    total = 0
    seen = set()
    natives = native_replicated()
    while key and key.lower() not in seen:
        seen.add(key.lower())
        entry = db['classes'].get(key) or db['classes'].get(key.lower()) or {}
        if key.lower() in _blueprint:
            total += _blueprint[key.lower()]
        elif 'generated' in entry:
            if entry.get('source') == 'skeleton':
                total += entry.get('net_count', 0)
            else:
                cached = entry.get('_net_count')
                if cached is None:
                    cached = entry['_net_count'] = _own_blueprint_count(entry)
                total += cached
        elif key in natives:
            total += len(natives[key]['replicated'])
        else:
            raise ValueError('cannot count replicated properties of %s' % key)
        key = entry.get('super') or entry.get('usmap_super') or \
            (natives.get(key) or {}).get('super')
    return total


def actor_defaults(desc):
    """CDO defaults from an actor description's replication keys (empty when
    it has none). A key also given under `defaults` is an error."""
    out = {}
    for k, prop in ACTOR_KEYS.items():
        if k in desc:
            if prop in (desc.get('defaults') or {}):
                raise ValueError('%s and defaults.%s both given' % (k, prop))
            value = desc[k]
            if prop == 'NetDormancy' and isinstance(value, str) and '::' not in value:
                value = 'ENetDormancy::' + value
            out[prop] = value
    return out
