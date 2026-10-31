"""Gate for bpkit.calls: predict the opcode of every direct call in the game.

Every FinalFunction, LocalFinalFunction and CallMath in the game's bytecode
names its target, so its opcode can be predicted from the type database and
compared. A VirtualFunction names only a function, not its class: it is
checked against every native function of that name, and is wrong if all of
them are final (the database would have the compiler call it as final).

    python3 bpkit/check_calls.py
"""

import collections
import multiprocessing as mp
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'UE Assets'))

import gamefiles                                          # noqa: E402
from bpkit.calls import opcode                            # noqa: E402
from bpkit.check_structs import kind_of                   # noqa: E402
from bpkit.package import Package                         # noqa: E402
from bpkit.structs import decode_export                   # noqa: E402
from bpkit.typedb import load, object_key                 # noqa: E402

DB = None


BY_NAME = None


def _init():
    global DB, BY_NAME
    DB = load()
    BY_NAME = collections.defaultdict(list)
    for key, fn in DB['functions'].items():
        if fn.get('source') == 'headers':
            BY_NAME[key.split(':', 1)[1].lower()].append(fn)


def check(stem):
    os.chdir(ROOT)
    try:
        ua = gamefiles.read(stem + '.uasset')
    except KeyError:
        return None
    if b'BlueprintGeneratedClass' not in ua:
        return None
    p = Package.read(ua, gamefiles.read(stem + '.uexp'))
    names = [str(n) for n in p.names]
    path = '/Game/' + stem.split('/Content/', 1)[1]
    out = collections.Counter()
    wrong = []
    own_functions = {p.object_name(k) for k, x in enumerate(p.exports, 1)
                     if p.object_name(x.class_index) == 'Function'}
    for i, e in enumerate(p.exports, 1):
        kind = kind_of(p.object_name(e.class_index) or 'Class')
        if not kind or kind[0] != 'function':
            continue
        try:
            x = decode_export(e.data, 'function', names, lambda v: v, kind[1])
        except Exception:
            continue
        caller = p.object_name(i)
        if caller.startswith('ExecuteUbergraph'):
            # An event's parent call is made from the ubergraph; the event it
            # belongs to is any event stub whose name matches the target.
            caller = None
        for top in x.script.nodes:
            for n in top.walk():
                if n.name == 'VirtualFunction':
                    fname = names[n.ops[0][1][0]] if isinstance(n.ops[0][1], tuple) else str(n.ops[0][1])
                    natives = BY_NAME.get(fname.lower())
                    if not natives:
                        continue                # a Blueprint function: not ours to judge
                    if all(f['function_flags'] & 0x1 for f in natives):
                        out['virtual call, predicted final'] += 1
                        wrong.append((fname, 'VirtualFunction', 'FinalFunction'))
                    else:
                        out['virtual call, ok'] += 1
                    continue
                if n.name not in ('FinalFunction', 'LocalFinalFunction', 'CallMath'):
                    continue
                target = object_key(p, n.ops[0][1], path)
                fn = DB['functions'].get(target)
                if fn is None:
                    out['target unknown'] += 1
                    continue
                want = opcode(fn, DB)
                if want == n.name:
                    out['ok'] += 1
                elif ((n.name, want) in (('FinalFunction', 'VirtualFunction'),
                                         ('LocalFinalFunction', 'LocalVirtualFunction'))
                      and (p.object_name(n.ops[0][1]) == caller or
                           (caller is None and p.object_name(n.ops[0][1]) in own_functions))):
                    # Super: an override calling the parent's version of itself,
                    # which the compiler always calls as final.
                    out['parent call, ok'] += 1
                else:
                    out['differs'] += 1
                    wrong.append((target, n.name, want))
    return out, wrong


def main():
    os.chdir(ROOT)
    _init()
    stems = sorted(k[:-len('.uasset')] for k in gamefiles.index() if k.endswith('.uasset'))
    total = collections.Counter()
    wrong = collections.Counter()
    with mp.Pool(12, initializer=_init) as pool:
        for got in pool.imap_unordered(check, stems, chunksize=32):
            if got:
                total.update(got[0])
                wrong.update(got[1])
    print(dict(total))
    for (target, have, want), n in wrong.most_common(30):
        print('  %5d  %-55s cooked %-18s predicted %s' % (n, target, have, want))
    return 0 if not wrong else 1


if __name__ == '__main__':
    sys.exit(main())
