"""Name coverage: do the decoders find every name a package uses?

The permutation checks in check_props.py and check_structs.py cannot see a
name the decoder does not know is a name: it is read as a plain integer,
written back as the same integer under any table, and read again the same way,
so both sides agree. Nothing that only compares the decoder with itself can
find such a field.

This check uses something outside the decoder. The cooker writes a name into
the name table because something in the package uses it. So the names found
by decoding -- in every export's payload, plus the import and export tables --
must account for the whole table. A name used only at a position the decoder
reads as plain data shows up here as unaccounted for.

It cannot see a missed position whose name is also used somewhere else; it
narrows the blind spot, it does not close it.

    python3 bpkit/check_coverage.py
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
from bpkit.build import Name, split_name                  # noqa: E402
from bpkit.check_structs import kind_of                   # noqa: E402
from bpkit.package import Package                         # noqa: E402
from bpkit.props import DecodeError, decode_object, encode_object  # noqa: E402
from bpkit.structs import decode_export, encode_export    # noqa: E402

GENERATED = (b'BlueprintGeneratedClass', b'WidgetBlueprintGeneratedClass',
             b'AnimBlueprintGeneratedClass')
EMPTY_TAILS = (b'', b'\0\0\0\0')


def payload_names(p, e, names):
    """Names found in one export's payload, or None if any of it is not decoded."""
    klass = p.object_name(e.class_index) or 'Class'
    kind = kind_of(klass)
    try:
        if kind:
            frags = encode_export(decode_export(e.data, kind[0], names, lambda i: i, kind[1]))
        else:
            props, tail = decode_object(e.data, names, lambda i: i)
            if tail not in EMPTY_TAILS:
                return None
            frags = encode_object(props, tail)
    except (DecodeError, struct.error, KeyError, IndexError, ValueError):
        return None
    return {f.text for f in frags if isinstance(f, Name)}


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
    used = {'None'}
    for imp in p.imports:
        used.update(names[i[0]] for i in (imp.class_package, imp.class_name, imp.object_name))
    for e in p.exports:
        used.add(names[e.object_name[0]])
        found = payload_names(p, e, names)
        if found is None:
            return stem, 'not fully decoded', []
        used |= found
    # The package's own path: the cooker always registers it, as the implicit
    # outer of every top-level export.
    # Split like any FName: "BP_UI_Enemies_Scenario_10" is entry "..._Scenario", number 11.
    leaf = split_name(stem.rsplit('/', 1)[-1])[0].lower()
    used.update(n for n in names if n.startswith('/') and n.lower().endswith('/' + leaf))
    # A soft reference's package: the decoded path is "/Game/X.Asset", and the
    # cooker also registers "/Game/X" for its soft package reference list, which
    # a cooked package does not write. Accounted for only when that path was
    # actually decoded.
    packages = {n.split('.', 1)[0] for n in used if n.startswith('/') and '.' in n}
    used |= packages
    missing = sorted(set(names) - used)
    return stem, ('covered' if not missing else 'unaccounted names'), missing


def main():
    os.chdir(ROOT)
    stems = sorted(k[:-len('.uasset')] for k in gamefiles.index() if k.endswith('.uasset'))
    verdicts = collections.Counter()
    missing_names = collections.Counter()
    samples = []
    with mp.Pool(12) as pool:
        for got in pool.imap_unordered(check, stems, chunksize=32):
            if not got:
                continue
            stem, verdict, missing = got
            verdicts[verdict] += 1
            missing_names.update(missing)
            if missing and len(samples) < 10:
                samples.append((stem.split('/Content/')[-1], missing[:6]))
    print(dict(verdicts))
    if missing_names:
        print('names never found by decoding (packages lacking each):')
        for n, c in missing_names.most_common(25):
            print('  %5d  %s' % (c, n))
        for stem, miss in samples:
            print('  %s\n      %s' % (stem, miss))
    return 0 if not missing_names else 1


if __name__ == '__main__':
    sys.exit(main())
