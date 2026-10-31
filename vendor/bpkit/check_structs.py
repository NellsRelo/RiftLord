"""Gate for bpkit.structs: every function, struct and class export in every Blueprint.

The same two checks as check_props.py, now over the exports that hold
properties, bytecode and class tables:

1. identity: decode, encode, resolve against the package's own tables -- the
   bytes must equal the original;
2. permuted: resolve against SHUFFLED name and object tables, decode with
   those, encode again -- the fragments must equal the first encoding.

As in check_props.py, the second proves recognised names and references are
placed consistently; a field read as plain data agrees with itself under any
table, so check_coverage.py is what looks for those.

    python3 bpkit/check_structs.py
"""

import collections
import multiprocessing as mp
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'UE Assets'))

import gamefiles                                          # noqa: E402
from bpkit.check_props import permutation, resolve       # noqa: E402
from bpkit.package import Package                         # noqa: E402
from bpkit.props import DecodeError                       # noqa: E402
from bpkit.structs import decode_export, encode_export    # noqa: E402

GENERATED = (b'BlueprintGeneratedClass', b'WidgetBlueprintGeneratedClass',
             b'AnimBlueprintGeneratedClass', b'UserDefinedStruct')
FUNCTIONS = {'Function': False, 'DelegateFunction': False, 'SparseDelegateFunction': True}


def kind_of(klass):
    """(kind, sparse) for a struct export's class -- kind is 'function', 'struct'
    or 'class' -- else None."""
    if klass in FUNCTIONS:
        return 'function', FUNCTIONS[klass]
    if klass == 'UserDefinedStruct':
        return 'struct', False
    if klass.endswith('GeneratedClass'):
        return 'class', False
    return None


def check(stem):
    os.chdir(ROOT)
    try:
        ua = gamefiles.read(stem + '.uasset')
    except KeyError:
        return None
    if not any(g in ua for g in GENERATED):
        return None
    p = Package.read(ua, gamefiles.read(stem + '.uexp'))
    names = [str(n) for n in p.names]
    own = {n: i for i, n in enumerate(names)}
    pnames, fwd, back = permutation(p, zlib.crc32(stem.encode()))
    pown = {n: i for i, n in enumerate(pnames)}
    results = []
    for e in p.exports:
        klass = p.object_name(e.class_index) or 'Class'
        kind = kind_of(klass)
        if kind is None:
            continue
        try:
            x = decode_export(e.data, kind[0], names, lambda i: i, kind[1])
        except (DecodeError, struct.error, KeyError, IndexError, ValueError) as ex:
            results.append((klass, 'undecodable', str(ex)[:100]))
            continue
        frags = encode_export(x)
        if resolve(frags, own, lambda t: t) != e.data:
            results.append((klass, 'identity differs', ''))
            continue
        shuffled = resolve(frags, pown, lambda t: fwd[t])
        try:
            again = decode_export(shuffled, kind[0], pnames, lambda i: back[i], kind[1])
        except (DecodeError, struct.error, KeyError, IndexError, ValueError) as ex:
            results.append((klass, 'permuted undecodable', str(ex)[:100]))
            continue
        if encode_export(again) != frags:
            results.append((klass, 'permuted differs', ''))
            continue
        results.append((klass, 'ok', ''))
    return stem, results


def main():
    os.chdir(ROOT)
    stems = sorted(k[:-len('.uasset')] for k in gamefiles.index() if k.endswith('.uasset'))
    verdicts = collections.Counter()
    by_class = collections.defaultdict(collections.Counter)
    samples = collections.defaultdict(list)
    with mp.Pool(12) as pool:
        for got in pool.imap_unordered(check, stems, chunksize=32):
            if not got:
                continue
            stem, results = got
            for klass, verdict, detail in results:
                verdicts[verdict] += 1
                by_class[klass][verdict] += 1
                if verdict != 'ok' and len(samples[(verdict, detail[:40])]) < 2:
                    samples[(verdict, detail[:40])].append((stem.split('/Content/')[-1],
                                                            klass, detail))
    print(dict(verdicts))
    for k, c in sorted(by_class.items(), key=lambda kv: -sum(kv[1].values())):
        print('  %-40s %s' % (k, dict(c)))
    for (verdict, _), items in sorted(samples.items()):
        for stem, klass, detail in items:
            print('  %-20s %-24s %s\n      %s' % (verdict, klass, detail, stem))
    return 0 if set(verdicts) == {'ok'} else 1


if __name__ == '__main__':
    sys.exit(main())
