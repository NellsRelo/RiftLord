"""The type database for Orcs Must Die! Deathtrap (UE 5.3) Blueprints, in bpkit's form
(vendor/bpkit/typedb.py: the same tables and canonical type texts), so bpkit's script compiler
can be pointed at Deathtrap.

Sources:

* native classes, structs, enums, functions and delegates: Deathtrap's UHT dump
  (`OMDD/Binaries/Win64/UHTHeaderDump`, every module, Engine, CoreUObject and UMG included),
  read with bpkit.headers;
* every shipped Blueprint class and user struct (bpclass5): its variables, functions and
  their parameters, keyed by full lowercased path as bpkit keys them;
* function meta the dump leaves out (wildcard parameters of custom-thunk functions,
  WorldContext): the stock UE 4.27.2 headers of the same modules (STOCK_META), where the
  function exists there; a static native the game calls by reference where CallMath would
  do has a custom thunk, so its placeholder parameters (PLACEHOLDERS) take any type;
* the shipped bytecode: a native function the game calls by name (EX_VirtualFunction) is
  virtual, so not final, unless the game also calls it by reference outside a parent call
  (the dump drops `virtual`); `calls` records every call for the opcode check;
* struct element sizes: the ElementSize of every shipped StructProperty;
* which bools are one-bit bitfields (`bitfields`), from the dump's declarations.

    db = load()            # built once, cached in CACHE
"""

import json
import os
import re
import struct

from riftlord.core import bpkitlink
from riftlord.formats.omdd import bpclass5, kismet5, trace_omdd

bpkitlink.install()
from bpkit import headers as bpheaders          # noqa: E402
from bpkit import typedb as bptypedb            # noqa: E402

#: The UHT dump, inside the install.
DUMP = ('OMDD', 'Binaries', 'Win64', 'UHTHeaderDump')
CACHE = os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'),
                                                                     '.cache'),
                     'rift-lord', 'omdd', 'typedb5.json')
#: Bumped when what build() records changes.
VERSION = 6
#: Stock UE 4.27.2 header folders read for function meta only.
STOCK_META = {'Engine': ('Engine/Classes', 'Engine/Public'), 'CoreUObject': ('CoreUObject/Public',),
              'UMG': ('UMG/Public',)}
#: Meta keys taken from the stock headers.
META_KEYS = bptypedb.WILDCARD_META + ('WorldContext', 'DeterminesOutputType',
                                      'DynamicOutputParam', 'DefaultToSelf')
#: FPackageObjectIndex null.
NULL_INDEX = 0xFFFFFFFFFFFFFFFF
#: Parameter types a custom-thunk function declares for a parameter of any type.
PLACEHOLDERS = ('array<int>', 'map<int,int>', 'set<int>', 'struct:GenericStruct', 'int')
CALL_OPS = ('FinalFunction', 'LocalFinalFunction', 'CallMath')
BY_NAME_OPS = ('VirtualFunction', 'LocalVirtualFunction')


def empty():
    return {'classes': {}, 'structs': {}, 'enums': {}, 'functions': {}, 'delegates': {},
            'usmap_only': {}, 'struct_sizes': {}, 'version': VERSION}


# -- natives ---------------------------------------------------------------------------------

def _cpp(text, delegates):
    return bptypedb.cpp_type(text, delegates)


BITFIELD = re.compile(r'\buint8\s+(\w+)\s*:\s*1\s*;')
CLASS_HEAD = re.compile(r'^(?:class|struct)\s+(?:[A-Z0-9_]+_API\s+)?[AUF]?([A-Z]\w*)\s*'
                        r'(?::[^{;]*)?\{', re.M)


def bitfields(dump):
    """{class or struct: [property]} of the one-bit bitfield bools (`uint8 bX : 1`) the dump
    declares. An unversioned property block writes such a bool when false (831 of 831 in the
    shipped widgets), where a plain bool is zero-masked (138 of 138)."""
    out = {}
    for mod in sorted(os.listdir(dump)):
        for root, _dirs, files in os.walk(os.path.join(dump, mod)):
            for f in files:
                if not f.endswith('.h'):
                    continue
                text = open(os.path.join(root, f), errors='replace').read()
                heads = [(m.start(), m.group(1)) for m in CLASS_HEAD.finditer(text)]
                for m in BITFIELD.finditer(text):
                    owner = [n for at, n in heads if at < m.start()]
                    if owner:
                        out.setdefault(owner[-1], []).append(m.group(1))
    return out


def stock_meta():
    """{(class, function): meta} of the stock UE 4.27.2 headers in STOCK_META, the keys in
    META_KEYS only; {} when the engine source is not there."""
    root = bptypedb.ENGINE
    if not os.path.isdir(root):
        return {}
    raw = bpheaders.empty()
    for mod, dirs in STOCK_META.items():
        for d in dirs:
            if os.path.isdir(os.path.join(root, d)):
                bpheaders.parse_tree(os.path.join(root, d), mod, raw)
    out = {}
    for cname, c in raw['classes'].items():
        for fname, f in c.get('functions', {}).items():
            meta = {k: v for k, v in f['meta'].items() if k in META_KEYS}
            if meta:
                out[(cname, fname)] = meta
    return out


def native(db, dump):
    """Fold the UHT dump's modules into `db` (bpkit.typedb._native over another dump), with
    the stock headers' meta where the dump has none."""
    raw = bpheaders.empty()
    for mod in sorted(os.listdir(dump)):
        if os.path.isdir(os.path.join(dump, mod)):
            bpheaders.parse_tree(os.path.join(dump, mod), mod, raw)
    stock = stock_meta()
    for cname, c in raw['classes'].items():
        for fname, f in c.get('functions', {}).items():
            for k, v in stock.get((cname, fname), {}).items():
                f['meta'].setdefault(k, v)
    delegates = {}
    for name, d in raw['delegates'].items():
        kind = 'mcdelegate' if d['multicast'] else 'delegate'
        delegates[name] = '%s:%s' % (kind, bptypedb._name(name))
    for name, e in raw['enums'].items():
        db['enums'][name] = {'values': e['values'], 'numbers': e['numbers'], 'kind': e['kind'],
                             'module': e['module'], 'source': 'headers'}
    for name, s in raw['structs'].items():
        db['structs'][name] = {
            'super': s['super'], 'module': s['module'], 'source': 'headers',
            'properties': {p['name']: _cpp(p['type'], delegates) for p in s['properties']}}
    for name, c in raw['classes'].items():
        if 'module' not in c:
            continue
        db['classes'][name] = {
            'super': c['super'], 'module': c['module'], 'kind': c['kind'],
            'interfaces': c.get('interfaces', []), 'source': 'headers',
            'properties': {p['name']: _cpp(p['type'], delegates) for p in c['properties']}}
        for fname, f in c['functions'].items():
            wild = set()
            for key in bptypedb.WILDCARD_META:
                wild.update(w.strip() for w in re.split(r'[,|]', f['meta'].get(key, ''))
                            if w.strip())
            params = [{'name': p['name'], 'type': _cpp(p['type'], delegates),
                       'out': p['out'] or p['ref'], 'ref': p['ref'], 'const': p['const'],
                       'return': False, 'default': p['default'],
                       'wildcard': p['name'] in wild} for p in f['params']]
            if f['return']:
                params.append({'name': 'ReturnValue', 'type': _cpp(f['return'], delegates),
                               'out': False, 'ref': False, 'const': False, 'return': True})
            db['functions']['%s:%s' % (name, fname)] = {
                'owner': name, 'static': f['static'], 'specifiers': f['flags'],
                'meta': f['meta'], 'module': c['module'],
                'function_flags': bptypedb.native_function_flags(f, c.get('kind')),
                'params': params, 'source': 'headers'}
    for name, d in raw['delegates'].items():
        params = [{'name': p['name'], 'type': _cpp(p['type'], delegates), 'out': False,
                   'ref': False, 'const': False, 'return': False} for p in d['params']]
        db['delegates'][bptypedb._name(name)] = {'params': params, 'multicast': d['multicast'],
                                                 'owner': d['owner'], 'module': d['module']}


# -- script objects --------------------------------------------------------------------------

def script_paths(game):
    """{FPackageObjectIndex: full path ('/Script/UMG.TextBlock:SetText')} of every native
    object, from the script objects chunk (name, global index, outer index, CDO class)."""
    entries = {}
    for c in game.containers:
        i = c.by_id.get((bytes(8), trace_omdd.CHUNK_SCRIPT))
        if i is None:
            continue
        b = c.read(i)
        names, o = trace_omdd.name_batch(b, 0)
        n, = struct.unpack_from('<i', b, o)
        o += 4
        for k in range(n):
            ni, num, gidx, outer, _cdo = struct.unpack_from('<IIQQQ', b, o + 32 * k)
            base = names[ni & 0x3FFFFFFF]
            entries[gidx] = (base if not num else '%s_%d' % (base, num - 1), outer)
    paths = {}

    def path(g):
        if g in paths:
            return paths[g]
        name, outer = entries[g]
        if outer == NULL_INDEX or outer not in entries:
            paths[g] = name
        else:
            parent = path(outer)
            sep = '.' if '.' not in parent else ':'
            paths[g] = parent + sep + name
        return paths[g]

    for g in entries:
        path(g)
    return paths


def script_key(path):
    """The database key of a native object path: 'TextBlock', 'TextBlock:SetText'."""
    return path.split('.', 1)[1] if '.' in path else path


# -- shipped Blueprints ----------------------------------------------------------------------

class Resolver:
    """Names of the objects one package references, as the database keys them."""

    def __init__(self, game, paths, pkg):
        self.game, self.paths, self.pkg = game, paths, pkg

    def path(self, ref):
        """Full path of an FPackageIndex: '/Script/UMG.TextBlock', '/Game/X/Y.Y_C:Fn'."""
        p = self.pkg
        if ref == 0:
            return None
        if ref > 0:
            return export_path(p, ref - 1)
        v = p.imports[-ref - 1] if -ref - 1 < len(p.imports) else NULL_INDEX
        if v >> 62 == trace_omdd.IDX_SCRIPT:
            return self.paths.get(v)
        got = p.import_target(-ref - 1)
        if not got or got[0] == 'script':
            return None
        return export_path(got[0], got[1])

    def key(self, ref):
        path = self.path(ref)
        if path is None:
            return None
        return script_key(path) if path.startswith('/Script/') else path.lower()


def package_name(pkg):
    """A trace_omdd.Package's name as its summary spells it (the game's file index is
    lowercased)."""
    index, number = struct.unpack_from('<II', pkg.data, 8)
    base = pkg.names[index & 0x3FFFFFFF]
    return base if not number else '%s_%d' % (base, number - 1)


def export_path(pkg, k):
    """'/Game/X/Y.Y_C' or '/Game/X/Y.Y_C:Fn' of export k of a trace_omdd.Package."""
    parts, seen = [], set()
    while k not in seen:
        seen.add(k)
        e = pkg.exports[k]
        parts.append(e['name'])
        if e['outer'] >> 62 != 0:
            break
        k = e['outer']
    parts.reverse()
    return package_name(pkg) + '.' + parts[0] + ''.join(':' + n for n in parts[1:])


def property_type(p, key):
    """Canonical type of a bpclass5.Property; `key(ref)` names a referenced object."""
    simple = {'IntProperty': 'int', 'Int8Property': 'int8', 'Int16Property': 'int16',
              'Int64Property': 'int64', 'UInt16Property': 'uint16', 'UInt32Property': 'uint32',
              'UInt64Property': 'uint64', 'FloatProperty': 'float', 'DoubleProperty': 'double',
              'BoolProperty': 'bool', 'NameProperty': 'name', 'StrProperty': 'str',
              'TextProperty': 'text', 'FieldPathProperty': 'fieldpath'}
    t = p.kind
    if t in simple:
        return simple[t]
    if t == 'ByteProperty':
        enum = key(p.refs['enum'])
        return 'enum:%s' % enum if enum else 'byte'
    if t == 'EnumProperty':
        return 'enum:%s' % key(p.refs['enum'])
    if t in ('ObjectProperty', 'SoftObjectProperty', 'WeakObjectProperty', 'LazyObjectProperty'):
        return '%s:%s' % (t[:-len('Property')].lower(), key(p.refs['property_class']))
    if t in ('ClassProperty', 'SoftClassProperty'):
        return '%s:%s' % (t[:-len('Property')].lower(), key(p.refs['meta_class']))
    if t == 'InterfaceProperty':
        return 'interface:%s' % key(p.refs['interface_class'])
    if t == 'StructProperty':
        return 'struct:%s' % key(p.refs['struct'])
    if t == 'DelegateProperty':
        return 'delegate:%s' % signature_name(key(p.refs['signature']))
    if t.startswith('Multicast'):
        return 'mcdelegate:%s' % signature_name(key(p.refs['signature']))
    if t == 'ArrayProperty':
        return 'array<%s>' % property_type(p.children['inner'], key)
    if t == 'SetProperty':
        return 'set<%s>' % property_type(p.children['element'], key)
    if t == 'MapProperty':
        return 'map<%s,%s>' % (property_type(p.children['key'], key),
                               property_type(p.children['value'], key))
    return 'unknown:%s' % t


def signature_name(key):
    """A delegate type's name: its signature function's, without __DelegateSignature (a
    Blueprint's key is lowercased, so the suffix is matched in any case)."""
    name = (key or '').split(':')[-1]
    if name.lower().endswith('__delegatesignature'):
        name = name[:-len('__DelegateSignature')]
    return name


CPF_OUT_PARM, CPF_RETURN_PARM, CPF_PARM = 0x100, 0x400, 0x80
CPF_REFERENCE_PARM, CPF_CONST_PARM = 0x8000000, 0x2


def add_package(db, game, paths, pkg, calls=None, by_name=None, by_reference=None):
    """Add one package's Blueprint classes, user structs and functions to `db`; collect its
    bytecode's calls: `calls` [(native key, opcode)], `by_name` {lowercased name},
    `by_reference` {native key} (calls of a parent's own version left out)."""
    r = Resolver(game, paths, pkg)
    names = pkg.names
    text = bpclass5.Cursor(b'', 0, names).text
    for k, e in enumerate(pkg.exports):
        cls = pkg.class_name(k)
        kind = bpclass5.kind_of_class(cls)
        if kind is None:
            continue
        try:
            _f, end = pkg.props_end(k, raw=True)
            x = bpclass5.read_struct(pkg.blob(k), end - e['at'], kind, names)
        except (trace_omdd.PropError, bpclass5.FormatError, IndexError, KeyError, ValueError):
            continue
        key = export_path(pkg, k).lower()
        for p in x.properties:
            # record the element size of every struct a property holds
            stack = [p]
            while stack:
                q = stack.pop()
                if q.kind == 'StructProperty':
                    sname = r.key(q.refs['struct'])
                    if sname:
                        db['struct_sizes'].setdefault(sname, q.element_size)
                stack += [c for c in q.children.values() if c.kind != 'None']
        props = {text(p.name): property_type(p, r.key) for p in x.properties}
        if kind.startswith('class'):
            db['classes'][key] = {'super': r.key(x.super), 'kind': 'class', 'generated': cls,
                                  'path': export_path(pkg, k), 'properties': props,
                                  'source': 'package'}
        elif kind == 'struct':
            db['structs'][key] = {'super': r.key(x.super), 'path': export_path(pkg, k),
                                  'properties': props, 'source': 'package'}
        else:
            owner = export_path(pkg, e['outer']).lower() if e['outer'] >> 62 == 0 else None
            params = []
            for p in x.properties:
                f = p.property_flags
                if not f & CPF_PARM:
                    continue
                params.append({'name': text(p.name), 'type': property_type(p, r.key),
                               'out': bool(f & CPF_OUT_PARM) and not f & CPF_RETURN_PARM,
                               'ref': bool(f & CPF_REFERENCE_PARM),
                               'const': bool(f & CPF_CONST_PARM),
                               'return': bool(f & CPF_RETURN_PARM)})
            db['functions'][key] = {'owner': owner, 'params': params,
                                    'function_flags': x.fields['function_flags'],
                                    'source': 'package'}
            if calls is None or not x.script:
                continue
            try:
                nodes = x.nodes()
            except (kismet5.BytecodeError, bpclass5.FormatError):
                continue
            fname = e['name']
            for top in nodes:
                for node in top.walk():
                    if node.name in CALL_OPS:
                        target = r.path(node.ops[0][1])
                        if target and target.startswith('/Script/'):
                            nk = script_key(target)
                            calls.append((nk, node.name))
                            if nk.split(':', 1)[1] != fname:
                                by_reference.add(nk)
                    elif node.name in BY_NAME_OPS:
                        by_name.add(text(node.ops[0][1]).lower())


def blueprints(db, game, paths, calls):
    """Every shipped Blueprint into `db`; then natives the game calls by name made virtual."""
    by_name, by_reference = set(), set()
    for key in sorted(game.files):
        c, i = game.files[key]
        pkg = game.package(key)
        if pkg is None or not any(bpclass5.kind_of_class(pkg.class_name(k))
                                  for k in range(len(pkg.exports))):
            continue
        add_package(db, game, paths, pkg, calls, by_name, by_reference)
    from bpkit.calls import opcode
    for key, op in calls:
        f = db['functions'].get(key)
        if f is None or op != 'FinalFunction' or opcode(f, db) != 'CallMath':
            continue
        # a static native the game calls by reference instead of CallMath has a custom thunk:
        # its placeholder parameters take any type
        for p in f['params']:
            if p['type'] in PLACEHOLDERS and not p.get('return'):
                p['wildcard'] = True
                p['wildcard_from'] = 'game bytecode'
    for key, f in db['functions'].items():
        if f.get('source') == 'headers' and key.split(':', 1)[1].lower() in by_name \
                and key not in by_reference and f['function_flags'] & bptypedb.FUNC_FINAL \
                and not f['function_flags'] & bptypedb.STATIC_FLAG:
            f['function_flags'] &= ~bptypedb.FUNC_FINAL
            f['virtual_from'] = 'game bytecode'


def build(game=None):
    game = game or trace_omdd.game()
    db = empty()
    native(db, os.path.join(trace_omdd.install(), *DUMP))
    db['bitfields'] = bitfields(os.path.join(trace_omdd.install(), *DUMP))
    paths = script_paths(game)
    calls = []
    blueprints(db, game, paths, calls)
    db['calls'] = calls
    return db


def load(rebuild=False, cache=CACHE):
    """The database, from `cache` when it is there and current."""
    if not rebuild and os.path.exists(cache):
        with open(cache) as fh:
            db = json.load(fh)
        if db.get('version') == VERSION:
            return db
    db = build()
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache + '.part', 'w') as fh:
        json.dump(db, fh)
    os.replace(cache + '.part', cache)
    return db
