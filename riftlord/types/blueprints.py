"""The game's Blueprint classes, structs and functions, read from its paks with bpkit's
own decoder (bpkit.typedb.add_package), as bpkit.typedb._blueprints reads them.

    bp = blueprints.read(log=print, progress=lambda done, total: ...)
    python3 -m riftlord.types.blueprints [--out blueprints.json]

{'classes', 'structs', 'functions', 'struct_sizes'} as add_package fills them, plus
'virtual_names' (function names the bytecode calls by name) and 'final_references'
(functions it calls by reference other than a parent call), and 'packages' read.
"""

import json
import sys
import time

#: What marks a package worth decoding.
MARKERS = (b'BlueprintGeneratedClass', b'UserDefinedStruct')


def read(log=None, progress=None, refresh=False):
    """The Blueprint data; `refresh` reads the paks' index again first."""
    from riftlord.core import bpkitlink, gamefiles
    bpkitlink.install()
    from bpkit.package import Package
    from bpkit.typedb import add_package
    log = log or (lambda _s: None)
    t0 = time.time()
    keys = [k for k in gamefiles.index(refresh=refresh)
            if k.endswith('.uasset') and '/Content/' in k]
    log('index: %d packages in %.1f s' % (len(keys), time.time() - t0))
    db = {'classes': {}, 'structs': {}, 'functions': {}, 'struct_sizes': {}}
    by_name, by_reference = set(), set()
    read_count = failed = 0
    for n, key in enumerate(keys):
        if progress and n % 200 == 0:
            progress(n, len(keys))
        ua = gamefiles.read(key)
        if not any(m in ua for m in MARKERS):
            continue
        try:
            p = Package.read(ua, gamefiles.read(key[:-7] + '.uexp'))
        except Exception:                                 # noqa: BLE001
            failed += 1
            continue
        add_package(db, p, '/Game/' + key.split('/Content/', 1)[1][:-len('.uasset')], by_name,
                    by_reference)
        read_count += 1
    if progress:
        progress(len(keys), len(keys))
    log('blueprints: %d packages decoded (%d unreadable) in %.1f s' % (
        read_count, failed, time.time() - t0))
    db['virtual_names'] = sorted(by_name)
    db['final_references'] = sorted(by_reference)
    db['packages'] = read_count
    return db


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', help='write the result as JSON here')
    args = ap.parse_args(argv)
    got = read(log=lambda s: print(s, file=sys.stderr))
    for k in ('classes', 'structs', 'functions', 'struct_sizes'):
        print('%-12s %d' % (k, len(got[k])))
    try:
        import resource
        print('peak memory %d MB' % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024))
    except ImportError:
        pass
    if args.out:
        with open(args.out, 'w') as fh:
            json.dump(got, fh)


if __name__ == '__main__':
    main()
