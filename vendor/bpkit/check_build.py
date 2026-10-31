"""Gate B for bpkit.build: rebuild shipped packages from a description of them.

Each package is taken apart into what a caller of `Builder` would supply --
imports by identity in a SHUFFLED order, the name set in a shuffled order,
exports with their payloads, preload dependencies as handles, generations left
for the builder to derive -- and rebuilt. Byte-identical output means the
builder derives name order, import order, every index, the name hashes and the
generation record exactly as the cooker did.

Payloads stay opaque here: the names and indices inside them are only right
because the derived tables come out in the same order. Resolving payloads
symbolically is the job of the serialisers built on top of this.

    python3 bpkit/check_build.py                 # every Blueprint package
    python3 bpkit/check_build.py --all           # every package and level
    python3 bpkit/check_build.py --authored      # summaries derived from scratch
    python3 bpkit/check_build.py --symbolic      # payloads as Name/Ref fragments
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

import gamefiles                                   # noqa: E402
from bpkit.build import Builder, ImportRef         # noqa: E402
from bpkit.package import Package, authored_summary  # noqa: E402
from bpkit.props import DecodeError, decode_object, encode_object  # noqa: E402
from bpkit.structs import decode_export, encode_export    # noqa: E402
from bpkit.check_structs import kind_of                   # noqa: E402

FIELDS = ('object_flags', 'forced_export', 'not_for_client', 'not_for_server',
          'package_guid', 'package_flags', 'not_always_loaded_for_editor_game', 'is_asset')
GENERATED = (b'BlueprintGeneratedClass', b'WidgetBlueprintGeneratedClass',
             b'AnimBlueprintGeneratedClass')


def describe(p, seed, game_path=None, symbolic=False):
    """A Builder holding package `p`'s contents, with no table positions in it.

    With `game_path`, the summary is the one `authored_summary` derives from
    scratch, keeping only the original's Guid, which a cook picks at random,
    and its flags, which depend on the payloads.

    With `symbolic`, every export `bpkit.props` or `bpkit.structs` can decode
    is given as Name and Ref fragments instead of bytes, so the builder places
    its names and references itself. `b.symbolic` counts them.
    """
    rng = random.Random(seed)
    if game_path:
        summary = authored_summary(game_path)
        summary.guid = p.summary.guid
        # Content, not convention: PKG_RequiresLocalizationGather is set when the
        # package holds localizable text, which a serialiser above this decides.
        summary.flags = p.summary.flags
    else:
        summary = p.summary
        summary.generations = None
    b = Builder(summary=summary)
    refs = {}
    order = list(range(len(p.imports)))
    rng.shuffle(order)
    for i in order:
        imp = p.imports[i]
        # Display text, as a caller would write it: the builder must split the
        # numbers back out exactly as the engine stored them.
        refs[-(i + 1)] = ImportRef(p.name(imp.class_package), p.name(imp.class_name),
                                   None, p.name(imp.object_name))
    for i in order:
        if p.imports[i].outer:
            refs[-(i + 1)].outer = refs[p.imports[i].outer]
        b.imports.append(refs[-(i + 1)])
    for i, e in enumerate(p.exports):
        refs[i + 1] = b.export_object(p.name(e.object_name), None,
                                      **{f: getattr(e, f) for f in FIELDS})
    b.symbolic = 0
    for i, e in enumerate(p.exports):
        r = refs[i + 1]
        r.klass, r.super = refs.get(e.class_index), refs.get(e.super_index)
        r.template, r.outer = refs.get(e.template_index), refs.get(e.outer_index)
        r.preload = {k: [refs.get(t) for t in v] for k, v in e.preload.items()}
        r.data = [e.data]
        if symbolic:
            names = [str(n) for n in p.names]
            kind = kind_of(p.object_name(e.class_index) or 'Class')
            try:
                if kind:
                    r.data = encode_export(decode_export(e.data, kind[0], names, refs.get,
                                                         kind[1]))
                else:
                    props, tail = decode_object(e.data, names, refs.get)
                    r.data = encode_object(props, tail)
            except (DecodeError, struct.error, KeyError, IndexError, ValueError):
                continue
            b.symbolic += 1
    names = list(p.names)
    rng.shuffle(names)
    b.extra_names.update(names)
    # Only consulted among names the cooker's comparison calls equal.
    b.tie_order = {n: i for i, n in enumerate(p.names)}
    return b


def first_difference(a, b):
    return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))


def check(stem):
    os.chdir(ROOT)
    try:
        ua = gamefiles.read(stem + '.uasset')
    except KeyError:
        try:
            ua = gamefiles.read(stem + '.umap')
        except KeyError:
            return 'skip', None
    if WANT_BLUEPRINTS and not any(g in ua for g in GENERATED):
        return 'skip', None
    try:
        ux = gamefiles.read(stem + '.uexp')
    except KeyError:
        ux = b''
    try:
        original = Package.read(ua, ux)
    except ValueError as ex:
        return 'refused', repr(ex)[:100]
    try:
        game_path = '/Game/' + stem.split('/Content/', 1)[1] if AUTHORED else None
        b = describe(Package.read(ua, ux), zlib.crc32(stem.encode()), game_path, SYMBOLIC)
        rebuilt = b.build()
    except Exception as ex:
        return 'error', repr(ex)[:100]
    head, body = rebuilt.write()
    if head == ua and body == ux:
        return 'ok', ('%d symbolic' % b.symbolic) if SYMBOLIC else None
    why = []
    if [str(n).lower() for n in rebuilt.names] != [str(n).lower() for n in original.names]:
        at = first_difference(rebuilt.names, original.names)
        why.append('name order at %d: %r vs %r' % (at, original.names[at:at + 2],
                                                   rebuilt.names[at:at + 2]))
    elif rebuilt.name_hashes != original.name_hashes:
        at = first_difference(rebuilt.name_hashes, original.name_hashes)
        why.append('name hash of %r' % original.names[at])
    if [i.write() for i in rebuilt.imports] != [i.write() for i in original.imports]:
        at = first_difference([i.write() for i in rebuilt.imports],
                              [i.write() for i in original.imports])
        why.append('import order at %d: %s' % (at, original.path(-(at + 1))))
    if not why:
        why.append('header byte %d' % first_difference(head, ua) if head != ua else 'body')
    return 'differs', '; '.join(why)


WANT_BLUEPRINTS = True
AUTHORED = False
SYMBOLIC = False


def _mode(blueprints_only, authored, symbolic):
    global WANT_BLUEPRINTS, AUTHORED, SYMBOLIC
    WANT_BLUEPRINTS, AUTHORED, SYMBOLIC = blueprints_only, authored, symbolic


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--all', action='store_true', help='every package, not only Blueprints')
    ap.add_argument('--authored', action='store_true',
                    help='derive each summary from scratch instead of copying it')
    ap.add_argument('--symbolic', action='store_true',
                    help='give decodable payloads as Name/Ref fragments, not bytes')
    ap.add_argument('--limit', type=int)
    args = ap.parse_args()
    os.chdir(ROOT)
    stems = sorted(k.rsplit('.', 1)[0] for k in gamefiles.index()
                   if k.endswith(('.uasset', '.umap')))
    if args.limit:
        stems = stems[:args.limit]
    verdicts = collections.Counter()
    kinds = collections.defaultdict(list)
    symbolic = 0
    with mp.Pool(12, initializer=_mode, initargs=(not args.all, args.authored, args.symbolic)) as pool:
        for stem, (verdict, detail) in zip(stems, pool.imap(check, stems, chunksize=64)):
            verdicts[verdict] += 1
            if verdict == 'ok':
                symbolic += int(detail.split()[0]) if detail else 0
            elif detail:
                kinds[(verdict, detail.split(':')[0])].append((stem, detail))
    print(dict(verdicts))
    if args.symbolic:
        print('exports given as Name/Ref fragments: %d' % symbolic)
    for (verdict, kind), items in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
        print('  %4d %-8s %s' % (len(items), verdict, kind))
        for stem, detail in items[:3]:
            print('         %s\n           %s' % (stem.split('/Content/')[-1], detail))
    return 0 if not (verdicts['differs'] or verdicts['error']) else 1


if __name__ == '__main__':
    sys.exit(main())
