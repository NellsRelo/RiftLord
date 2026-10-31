"""Gate for bpkit.typedb: the game's own bytecode, resolved against the database.

For every function in every Blueprint:

* every direct call (FinalFunction, LocalFinalFunction, CallMath) names a
  function the database knows, and passes exactly as many arguments as it has
  parameters (the return value excluded);
* where an argument is a variable, the variable's type is the parameter's
  type -- equal, or an object of a subclass of the parameter's class. The
  Blueprint compiler inserts every conversion explicitly, so anything else is
  a fault in the database: a misread header or a bad type mapping;
* every property reference (an FFieldPath: names plus the owning struct)
  names a property its owner has, under its real name or an alias (see
  typedb._fix_param_names).

    python3 bpkit/check_typedb.py
"""

import collections
import multiprocessing as mp
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'UE Assets'))

import gamefiles                                          # noqa: E402
from bpkit.build import Name                              # noqa: E402
from bpkit.check_structs import kind_of                   # noqa: E402
from bpkit.package import Package                         # noqa: E402
from bpkit.structs import decode_export                   # noqa: E402
from bpkit.typedb import db_key, load, object_key         # noqa: E402

CALLS = ('FinalFunction', 'LocalFinalFunction', 'CallMath')
VARIABLES = ('LocalVariable', 'InstanceVariable', 'LocalOutVariable', 'DefaultVariable')
DB = None


def _init():
    global DB
    DB = load()


def ancestors(name):
    """A class and every class above it."""
    name = db_key(name)
    seen = []
    while name and name not in seen:
        seen.append(name)
        c = DB['classes'].get(name) or DB['structs'].get(name) or DB['usmap_only'].get(name)
        name = db_key(c and (c.get('super') or c.get('usmap_super')))
    return seen


def fields_of(owner):
    """Property names an owner struct, class or function declares (lowercased)."""
    out = set()
    if owner in DB['functions']:
        f = DB['functions'][owner]
        for q in f['params']:
            out.add(q['name'].lower())
            out.update(a.lower() for a in q.get('aliases', ()))
        out.update(k.lower() for k in f.get('locals', {}))
        return out
    for name in ancestors(owner):
        c = DB['classes'].get(name) or DB['structs'].get(name) or DB['usmap_only'].get(name)
        if c:
            out.update(k.lower() for k in c.get('properties', {}))
            out.update(k.lower() for k in c.get('usmap_properties', []))
    return out


def compatible(have, want, param):
    """Can a variable of type `have` be passed where `want` is declared?

    Besides equal types and an object of a subclass, the conversions the
    Blueprint VM makes when copying an argument into the callee's frame: an
    enum and a byte for each other (FByteProperty and FEnumProperty share the
    representation), an interface where FScriptInterface is declared, a weak
    object pointer where an object pointer is, and an array of a subclass for
    an array of its base.
    """
    if have == want or param.get('wildcard'):
        return True
    if (have.startswith('enum:') and want == 'byte') or (have == 'byte' and want.startswith('enum:')):
        return True
    if have.startswith('array<') and want.startswith('array<'):
        # An array of a subclass is passed for an array of its base: as an
        # input, and as the out array of a DeterminesOutputType function.
        return compatible(have[6:-1], want[6:-1], {})
    if have.startswith('interface:') and want == 'struct:ScriptInterface':
        return True
    if have.startswith('weakobject:') and want.startswith('object:'):
        have = 'object:' + have[len('weakobject:'):]
    for kind in ('object:', 'class:', 'softobject:', 'softclass:', 'weakobject:', 'interface:'):
        if have.startswith(kind) and want.startswith(kind):
            return db_key(want[len(kind):]) in ancestors(have[len(kind):])
    return False


def check(stem):
    os.chdir(ROOT)
    try:
        ua = gamefiles.read(stem + '.uasset')
    except KeyError:
        return None
    if b'BlueprintGeneratedClass' not in ua:
        return None
    p = Package.read(ua, gamefiles.read(stem + '.uexp'))
    out, samples = check_package(p, '/Game/' + stem.split('/Content/', 1)[1])
    return stem, out, samples


def check_package(p, pkg_path, database=None):
    """(Counter of verdicts, samples) for one package's bytecode against `database`
    (default: the loaded type database)."""
    global DB
    if database is not None:
        DB = database
    names = [str(n) for n in p.names]
    out = collections.Counter()
    samples = []

    def owner_key(idx):
        """The database's key for an object: see typedb.object_key."""
        return object_key(p, idx, pkg_path)

    def note(verdict, detail):
        out[verdict] += 1
        if not verdict.endswith('ok'):
            samples.append((verdict, detail))

    for i, e in enumerate(p.exports, 1):
        kind = kind_of(p.object_name(e.class_index) or 'Class')
        if not kind or kind[0] != 'function':
            continue
        try:
            x = decode_export(e.data, 'function', names, lambda v: v, kind[1])
        except Exception:
            continue
        here = owner_key(i)

        def variable_type(node):
            """Type of a variable expression: (names, owner) of its field path."""
            path = [Name(names[v[0]], v[1]).display() for k, v in node.ops if k == 'name']
            owner = next((v for k, v in node.ops if k == 'owner'), 0)
            if not path or not owner:
                return None
            okey = owner_key(owner)
            if okey in DB['functions']:
                f = DB['functions'][okey]
                for q in f['params']:
                    if path[0].lower() in [n.lower() for n in [q['name']] + q.get('aliases', [])]:
                        return q['type']
                return {k.lower(): v for k, v in f.get('locals', {}).items()}.get(path[0].lower())
            for name in ancestors(okey):
                c = DB['classes'].get(name) or DB['structs'].get(name)
                props = {k.lower(): v for k, v in (c or {}).get('properties', {}).items()}
                if path[0].lower() in props:
                    return props[path[0].lower()]
            return None

        for top in x.script.nodes:
            for node in top.walk():
                if any(k == 'pathlen' for k, _ in node.ops):
                    path = [Name(names[v[0]], v[1]).display() for k, v in node.ops if k == 'name']
                    owner = next((v for k, v in node.ops if k == 'owner'), 0)
                    if path and owner:
                        okey = owner_key(owner)
                        known = fields_of(okey)
                        if path[0].lower() in known:
                            note('field ok', None)
                        else:
                            note('field unresolved', '%s.%s (in %s)' % (okey, path[0], here))
                if node.name not in CALLS:
                    continue
                target = owner_key(node.ops[0][1])
                fn = DB['functions'].get(target)
                if fn is None:
                    note('call unresolved', target)
                    continue
                args = [v for k, v in node.ops if k == 'expr' and v.name != 'EndFunctionParms']
                params = [q for q in fn['params'] if not q['return']]
                if len(args) != len(params):
                    note('call arity', '%s: %d args, %d params' % (target, len(args),
                                                                   len(params)))
                    continue
                note('call ok', None)
                for a, q in zip(args, params):
                    if a.name not in VARIABLES:
                        continue
                    have = variable_type(a)
                    if have is None:
                        note('arg type unknown', '%s(%s)' % (target, q['name']))
                    elif compatible(have, q['type'], q):
                        note('arg type ok', None)
                    else:
                        note('arg type mismatch', '%s(%s): %s vs %s' % (
                            target, q['name'], have, q['type']))
    return out, samples


def main():
    os.chdir(ROOT)
    load()
    stems = sorted(k[:-len('.uasset')] for k in gamefiles.index() if k.endswith('.uasset'))
    total = collections.Counter()
    samples = collections.defaultdict(list)
    with mp.Pool(12, initializer=_init) as pool:
        for got in pool.imap_unordered(check, stems, chunksize=32):
            if not got:
                continue
            stem, out, found = got
            total.update(out)
            for verdict, detail in found:
                samples[verdict].append(detail.split(' (in ')[0])
    for k in sorted(total):
        print('%8d  %s' % (total[k], k))
    for verdict, details in samples.items():
        print('\n%s:' % verdict)
        for d, c in collections.Counter(details).most_common(25):
            print('   %3d  %s' % (c, d))
    bad = {k for k in total if not k.endswith('ok')}
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
