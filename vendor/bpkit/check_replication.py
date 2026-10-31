"""Gate for replication: every shipped Blueprint that replicates, against bpkit.

For each Blueprint class in the game with a replicated variable or an RPC:

1. round trip: its class and function exports decode and re-encode
   byte-identically against their own and shuffled tables (check_structs);
2. derivation: the RepIndex bpkit derives for each replicated variable (the
   parent's ClassReps size plus its rank) equals the cooked one, and
   NumReplicatedProperties equals the count;
3. fresh compile: a script declaring the same variables and RPCs (written
   from the shipped declarations, with the same parent) compiles through
   bpkit, passes verify (decoders, pkgcheck, verify_logic), and its
   replicated properties and RPC functions equal the shipped ones field for
   field. Property flags are compared without the editor-only bits a variable's
   details panel sets (EDITOR_ONLY), which bpkit does not author for any
   variable. A set of each variable must compile to FlushNetDormancy, the
   assignment, MarkPropertyDirtyFromRepIndex(self, RepIndex, name) -- the
   shipped statement, text for text -- and the RepNotify call; a call of each
   RPC to VirtualFunction by name, as shipped.

    python3 bpkit/check_replication.py [-v]
"""

import argparse
import json
import multiprocessing as mp
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'UE Assets'))

import gamefiles                                          # noqa: E402
from bpkit.check_structs import check as roundtrip, kind_of   # noqa: E402
from bpkit.package import Package                         # noqa: E402
from bpkit.structs import decode_export                   # noqa: E402

CPF_NET = 0x20
FUNC_NET = 0x40
#: CPF_DisableEditOnTemplate and CPF_DisableEditOnInstance: the details
#: panel's "Instance Editable" and the actor-reference rule; editor metadata.
EDITOR_ONLY = 0x800 | 0x10000
KINDS = ((0x200000, 'server'), (0x1000000, 'client'), (0x4000, 'multicast'))


def scan(stem):
    """(stem, [class records]) for a package with replication, else None."""
    os.chdir(ROOT)
    try:
        ua = gamefiles.read(stem + '.uasset')
    except KeyError:
        return None
    if b'GeneratedClass' not in ua:
        return None
    p = Package.read(ua, gamefiles.read(stem + '.uexp'))
    names = [str(n) for n in p.names]
    classes, funcs, plain = [], [], {}
    for i, e in enumerate(p.exports, 1):
        k = kind_of(p.object_name(e.class_index) or 'Class')
        if not k or k[0] == 'struct':
            continue
        x = decode_export(e.data, k[0], names, lambda v: v, k[1])
        if k[0] == 'class':
            nets = [q for q in x.properties if q.property_flags & CPF_NET]
            num = next((int.from_bytes(q.value.data, 'little', signed=True) for q in x.props
                        if q.name.text == 'NumReplicatedProperties'), 0)
            classes.append({'name': p.object_name(i), 'export': i, 'num': num,
                            'props': [prop_record(q) for q in nets]})
        elif x.fields['function_flags'] & FUNC_NET:
            funcs.append({'name': p.object_name(i), 'record': func_record(x)})
        else:
            plain[p.object_name(i)] = x.fields['function_flags']
    notifies = {q['rep_notify']: plain.get(q['rep_notify']) for c in classes for q in c['props']
                if q['rep_notify'] != 'None'}
    if not funcs and not any(c['props'] for c in classes):
        return None
    marks = {}
    for line in statements(p, names):
        m = re.search(r'CallMath\(@MarkPropertyDirtyFromRepIndex, Self\(\), IntConst\((\d+)\), '
                      r'NameConst\((\w+)\)', line)
        if m:
            marks[m.group(2)] = line.strip()
    return stem, {'classes': classes, 'funcs': funcs, 'marks': marks, 'notifies': notifies}


def prop_record(q):
    return {'name': q.name.display(), 'type': q.type.text, 'flags': q.flags,
            'array_dim': q.array_dim, 'element_size': q.element_size,
            'property_flags': q.property_flags, 'rep_index': q.rep_index,
            'rep_notify': q.rep_notify.display(), 'rep_condition': q.rep_condition}


def func_record(x):
    return {'function_flags': x.fields['function_flags'],
            'rep_offset': x.fields.get('rep_offset', b'').hex(),
            'params': [(q.name.display(), q.type.text, q.property_flags) for q in x.properties
                       if q.property_flags & 0x80]}


def statements(p, names):
    """Every top-level statement of every function, as text with names resolved."""
    def text(n):
        parts = []
        for k, v in n.ops:
            if k == 'expr':
                parts.append(text(v))
            elif k in ('obj', 'owner'):
                parts.append('@' + (p.object_name(v) if v else 'null'))
            elif k in ('name', 'pname'):
                parts.append(names[v[0]] + ('_%d' % (v[1] - 1) if v[1] else ''))
            elif k != 'pathlen':
                parts.append(repr(v) if k == 'cstr' else str(v))
        return '%s(%s)' % (n.name, ', '.join(parts))
    out = []
    for e in p.exports:
        k = kind_of(p.object_name(e.class_index) or 'Class')
        if k and k[0] == 'function':
            x = decode_export(e.data, 'function', names, lambda v: v, k[1])
            out += [text(n) for n in x.script.nodes]
    return out


# -- the fresh compile ---------------------------------------------------------

def script_for(db, key, shipped):
    """(script text, unsupported RPC names) declaring the shipped class's
    replicated variables and RPCs, plus SetX / CallX functions using them."""
    entry = db['classes'][key]
    lines, funcs, skipped = [], [], []
    from bpkit.replication import condition  # noqa: F401  (names below are checked by the compiler)
    enum = db['enums']['ELifetimeCondition']
    for q in shipped['props']:
        t = entry['properties'][q['name']]
        cond = enum['values'][enum['numbers'].index(q['rep_condition'])][5:]
        if q['rep_notify'] != 'None':
            lines.append('%s: RepNotify[%r, %r, %s]' % (q['name'], t, q['rep_notify'], cond))
            funcs.append('def %s():\n    pass\n' % q['rep_notify'])
        else:
            lines.append('%s: Replicated[%r, %s]' % (q['name'], t, cond))
        funcs.append('def Set%s(Value: %r):\n    self.%s = Value\n' % (q['name'], t, q['name']))
    for f in shipped['funcs']:
        name = f['name']
        if not name.isidentifier():
            skipped.append(name)
            continue
        fn = db['functions'][('%s:%s' % (key, name)).lower()]
        flags = f['record']['function_flags']
        kind = next(k for bit, k in KINDS if flags & bit)
        deco = '@%s(reliable=True)' % kind if flags & 0x80 else '@' + kind
        params = ', '.join('%s: %r' % (p['name'], p['type']) for p in fn['params'])
        args = ', '.join(p['name'] for p in fn['params'])
        funcs.append('%s\ndef %s(%s):\n    pass\n' % (deco, name, params))
        funcs.append('def Call%s(%s):\n    self.%s(%s)\n' % (name, params, name, args))
    return '\n'.join(lines) + '\n\n\n' + '\n\n'.join(funcs), skipped


def compile_fresh(db, key, shipped, workdir):
    """(package, problems, unsupported) for the fresh script of a shipped class."""
    from bpkit import actor, uobject, widget
    entry = db['classes'][key]
    parent_key = entry['super']
    parent = db['classes'][parent_key]
    parent_path = parent['path'] if 'generated' in parent else \
        '/Script/%s.%s' % (parent['module'], parent_key)
    text, skipped = script_for(db, key, shipped)
    short = entry['path'].rsplit('.', 1)[1][:-2] + 'Fresh'
    with open(os.path.join(workdir, short + '.py'), 'w') as fh:
        fh.write(text)
    desc = {'name': short, 'path': '/Game/Mods/ReplicationGate/' + short,
            'parent': parent_path, 'script': short + '.py', '_dir': workdir}
    if not any(c in ('Actor',) for c in ancestors(db, parent_key)):
        desc['kind'] = 'object'
    widget.declare([desc])
    package = (uobject if desc.get('kind') == 'object' else actor).compile(desc)
    return package, widget.verify(package, short, desc['path']), skipped, text


def ancestors(db, key):
    out = []
    while key and key not in out:
        out.append(key)
        e = db['classes'].get(key) or {}
        key = e.get('super') or e.get('usmap_super')
    return out


def compare(package, shipped, marks, notifies):
    """Differences between the fresh package and the shipped declarations."""
    names = [str(n) for n in package.names]
    bad = []
    got_props, got_funcs, num = {}, {}, 0
    for i, e in enumerate(package.exports, 1):
        k = kind_of(package.object_name(e.class_index) or 'Class')
        if not k:
            continue
        x = decode_export(e.data, k[0], names, lambda v: v, k[1])
        if k[0] == 'class':
            got_props = {q.name.display(): prop_record(q) for q in x.properties
                         if q.property_flags & CPF_NET}
            num = next((int.from_bytes(q.value.data, 'little', signed=True) for q in x.props
                        if q.name.text == 'NumReplicatedProperties'), 0)
        elif x.fields['function_flags'] & FUNC_NET:
            got_funcs[package.object_name(i)] = func_record(x)
        elif package.object_name(i) in notifies:
            if x.fields['function_flags'] != notifies[package.object_name(i)]:
                bad.append('%s flags %#x, shipped %#x' % (package.object_name(i),
                           x.fields['function_flags'], notifies[package.object_name(i)]))
    want_num = len(shipped['props'])
    if num != want_num:
        bad.append('NumReplicatedProperties %d, shipped %d' % (num, want_num))
    for q in shipped['props']:
        g = got_props.get(q['name'])
        if g is None:
            bad.append('%s not replicated' % q['name'])
            continue
        for field, want in q.items():
            have = g[field]
            if field == 'property_flags':
                have, want = have & ~EDITOR_ONLY, want & ~EDITOR_ONLY
            if have != want:
                bad.append('%s.%s: %r, shipped %r' % (q['name'], field, have, want))
    for f in shipped['funcs']:
        if f['name'] in got_funcs and got_funcs[f['name']] != f['record']:
            bad.append('%s: %r, shipped %r' % (f['name'], got_funcs[f['name']], f['record']))
    lines = statements(package, names)
    for q in shipped['props']:
        n = q['name']
        mark = next((ln for ln in lines if 'NameConst(%s)' % n in ln
                     and 'MarkPropertyDirtyFromRepIndex' in ln), None)
        if n in marks and mark != marks[n]:
            bad.append('set %s marks %s, shipped %s' % (n, mark, marks[n]))
        i = lines.index(mark) if mark in lines else -1
        if i < 2 or not lines[i - 1].startswith(('Let', 'LetBool', 'LetObj')) or \
                not any('FlushNetDormancy' in ln for ln in lines[:i]):
            bad.append('set %s: not flush, assign, mark' % n)
        if q['rep_notify'] != 'None' and not lines[i + 1].startswith(
                'LocalVirtualFunction(%s' % q['rep_notify']):
            bad.append('set %s does not call %s next' % (n, q['rep_notify']))
    for f in shipped['funcs']:
        if f['name'].isidentifier() and not any(ln.startswith('VirtualFunction(%s' % f['name'])
                                                for ln in lines):
            bad.append('%s is not called by name' % f['name'])
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-v', action='store_true', help='print each fresh script')
    args = ap.parse_args()
    os.chdir(ROOT)
    from bpkit.replication import replicated_count
    from bpkit.typedb import shared
    db = shared()
    stems = sorted(k[:-len('.uasset')] for k in gamefiles.index() if k.endswith('.uasset'))
    with mp.Pool(12) as pool:
        found = [r for r in pool.imap_unordered(scan, stems, chunksize=32) if r]
        trips = dict(pool.map(roundtrip, [s for s, _ in found]))
    failures = 0
    totals = {'packages': 0, 'variables': 0, 'rpcs': 0, 'unsupported': 0}
    work = tempfile.mkdtemp(prefix='bpkit-repgate-')
    for stem, info in sorted(found):
        totals['packages'] += 1
        label = stem.split('/Content/')[-1]
        bad = ['round trip: %s %s' % (k, v) for k, v, _ in trips[stem] if v != 'ok']
        for c in info['classes']:
            pkg_path = '/Game/' + stem.split('/Content/')[-1]
            key = ('%s.%s' % (pkg_path, c['name'])).lower()
            entry = db['classes'][key]
            if c['props']:
                base = replicated_count(db, entry['super'])
                for rank, q in enumerate(c['props']):
                    if q['rep_index'] != base + rank:
                        bad.append('%s RepIndex %d, derived %d' % (q['name'], q['rep_index'],
                                                                    base + rank))
                if c['num'] != len(c['props']):
                    bad.append('NumReplicatedProperties %d for %d' % (c['num'], len(c['props'])))
            shipped = {'props': c['props'], 'funcs': info['funcs']}
            totals['variables'] += len(c['props'])
            totals['rpcs'] += len(info['funcs'])
            package, problems, skipped, text = compile_fresh(db, key, shipped, work)
            totals['unsupported'] += len(skipped)
            bad += ['fresh: ' + p for p in problems]
            bad += ['fresh: ' + p for p in compare(package, shipped, info['marks'], info['notifies'])]
            if args.v:
                print('---- %s\n%s' % (label, text))
        verdict = 'ok' if not bad else 'FAIL'
        failures += bool(bad)
        print('%-4s %s: %d variables, %d RPCs%s' % (
            verdict, label, sum(len(c['props']) for c in info['classes']), len(info['funcs']),
            ''.join('\n       ' + b for b in bad)))
    print(json.dumps(totals))
    print('%d of %d packages pass' % (totals['packages'] - failures, totals['packages']))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
