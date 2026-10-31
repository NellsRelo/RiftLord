"""Gate for bpkit.props: decode every tagged object in the game's widgets and write it back.

Two checks per object, both required:

1. identity: decode, encode, resolve the fragments against the package's own
   tables -- the bytes must equal the original;
2. permuted: resolve the same fragments against SHUFFLED name and object
   tables, decode that with the shuffled tables, and require the same values.

The second proves that every name and reference the decoder DOES recognise is
placed consistently under any table, which is what the builder relies on. It
cannot see a name the decoder reads as plain data -- that is written back as
the same integer under every table and so agrees with itself. check_coverage.py
is the check aimed at that; reading each format from the engine source is the
rest of the defence.

    python3 bpkit/check_props.py                  # objects in widget Blueprints
    python3 bpkit/check_props.py --blueprints     # objects in every Blueprint
"""

import argparse
import collections
import multiprocessing as mp
import os
import random
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'UE Assets'))

import gamefiles                                          # noqa: E402
from bpkit.build import Name, Ref                         # noqa: E402
from bpkit.package import Package                         # noqa: E402
from bpkit.props import DecodeError, decode_object, encode_object  # noqa: E402

WIDGET = b'WidgetBlueprintGeneratedClass'
ANY_BP = (b'BlueprintGeneratedClass', WIDGET, b'AnimBlueprintGeneratedClass')
#: Exports that are not a tagged object first: step 3's business.
SKIP_CLASSES = {'Function', 'BlueprintGeneratedClass', 'WidgetBlueprintGeneratedClass',
                'AnimBlueprintGeneratedClass'}
#: Out of scope, reported as excluded rather than failed. A widget animation's
#: compiled evaluation template holds polymorphic section templates (a struct
#: type name, then that struct) and custom-serialised segments. It is derived
#: data the editor recompiles from the animation at every cook, and authoring
#: animations is not planned yet.
EXCLUDED_CLASSES = {'MovieSceneCompiledData'}


def resolve(frags, name_index, ref_index):
    out = bytearray()
    for f in frags:
        if isinstance(f, Name):
            out += struct.pack('<ii', name_index[f.text], f.number)
        elif isinstance(f, Ref):
            out += struct.pack('<i', ref_index(f.target))
        else:
            out += f
    return bytes(out)


def permutation(p, seed):
    """Shuffled names, and a shuffle of imports among imports, exports among exports."""
    rng = random.Random(seed)
    names = list(p.names)
    rng.shuffle(names)
    imps = list(range(1, len(p.imports) + 1))
    exps = list(range(1, len(p.exports) + 1))
    shuffled_i, shuffled_e = imps[:], exps[:]
    rng.shuffle(shuffled_i)
    rng.shuffle(shuffled_e)
    fwd = {0: 0}
    fwd.update({-a: -b for a, b in zip(imps, shuffled_i)})
    fwd.update({a: b for a, b in zip(exps, shuffled_e)})
    back = {v: k for k, v in fwd.items()}
    return names, fwd, back


def check(args):
    stem, want = args
    os.chdir(ROOT)
    try:
        ua = gamefiles.read(stem + '.uasset')
    except KeyError:
        return None
    if not any(g in ua for g in want):
        return None
    p = Package.read(ua, gamefiles.read(stem + '.uexp'))
    names = [str(n) for n in p.names]
    own = {n: i for i, n in enumerate(names)}
    pnames, fwd, back = permutation(p, zlib.crc32(stem.encode()))
    pown = {n: i for i, n in enumerate(pnames)}
    results = []
    for e in p.exports:
        klass = p.object_name(e.class_index) or 'Class'
        if klass in SKIP_CLASSES or klass.endswith('GeneratedClass'):
            continue
        try:
            props, tail = decode_object(e.data, names, lambda i: i)
        except (DecodeError, struct.error, KeyError) as ex:
            verdict = 'excluded' if klass in EXCLUDED_CLASSES else 'undecodable'
            results.append((klass, verdict, str(ex)[:90], 0))
            continue
        frags = encode_object(props, tail)
        if resolve(frags, own, lambda t: t) != e.data:
            results.append((klass, 'identity differs', '', len(tail)))
            continue
        shuffled = resolve(frags, pown, lambda t: fwd[t])
        try:
            again, again_tail = decode_object(shuffled, pnames, lambda i: back[i])
        except (DecodeError, struct.error, KeyError) as ex:
            results.append((klass, 'permuted undecodable', str(ex)[:90], len(tail)))
            continue
        if again != props or again_tail != tail:
            results.append((klass, 'permuted differs', '', len(tail)))
            continue
        if tail not in (b'', b'\0\0\0\0'):
            results.append((klass, 'tail not an empty bHasGuid', tail[:8].hex(), len(tail)))
            continue
        results.append((klass, 'ok', '', len(tail)))
    return stem, results


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--blueprints', action='store_true',
                    help='every Blueprint, not only widget Blueprints')
    args = ap.parse_args()
    want = ANY_BP if args.blueprints else (WIDGET,)
    os.chdir(ROOT)
    stems = sorted(k[:-len('.uasset')] for k in gamefiles.index() if k.endswith('.uasset'))
    verdicts = collections.Counter()
    by_class = collections.defaultdict(collections.Counter)
    tails = collections.defaultdict(collections.Counter)
    samples = collections.defaultdict(list)
    with mp.Pool(12) as pool:
        for got in pool.imap_unordered(check, [(s, want) for s in stems], chunksize=32):
            if not got:
                continue
            stem, results = got
            for klass, verdict, detail, tail in results:
                verdicts[verdict] += 1
                by_class[klass][verdict] += 1
                tails[klass][tail] += 1
                if verdict not in ('ok', 'excluded') and len(samples[(verdict, detail[:50])]) < 2:
                    samples[(verdict, detail[:50])].append((stem.split('/Content/')[-1], klass,
                                                            detail))
    print(dict(verdicts))
    bad = {k: c for k, c in by_class.items() if set(c) - {'ok', 'excluded'}}
    print('\nclasses with failures: %d of %d' % (len(bad), len(by_class)))
    for k, c in sorted(bad.items(), key=lambda kv: -sum(kv[1].values()))[:30]:
        print('  %-40s %s' % (k, dict(c)))
    print('\nfailure samples:')
    for (verdict, _), items in sorted(samples.items()):
        for stem, klass, detail in items:
            print('  %-20s %-28s %s\n      %s' % (verdict, klass, detail, stem))
    print('\ntail sizes after None (class: {bytes: objects}), non-zero only:')
    for k, c in sorted(tails.items()):
        if set(c) != {0}:
            print('  %-40s %s' % (k, dict(c)))
    return 0 if set(verdicts) <= {'ok', 'excluded'} else 1


if __name__ == '__main__':
    sys.exit(main())
