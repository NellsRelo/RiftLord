"""The Engine and CoreUObject declarations, read from the user's UE 4.27 headers with
bpkit's own header parser (bpkit.headers), the two modules the game's reflection is
read beside.

    raw, report = engine.parse(ue_root)
    python3 -m riftlord.types.engine [--ue ROOT] [--out engine.json]

`raw` is bpkit.headers' plain data ({'classes', 'structs', 'enums', 'delegates'}) plus
'intrinsics': {class: super} for the classes CoreUObject declares in C++ without a
UCLASS. `report` lists the folders read and the folders not there.
"""

import json
import os
import re
import sys
import time

#: The modules read, and their header folders under Engine/Source/Runtime (bpkit.typedb's
#: ENGINE_MODULES). A folder marked optional may be missing from a launcher install.
MODULES = {'Engine': ('Engine/Classes', 'Engine/Public', 'Engine/Private'),
           'CoreUObject': ('CoreUObject/Public',)}
OPTIONAL = {'Engine/Private'}
#: C++ classes declared with DECLARE_CLASS / DECLARE_CASTED_CLASS_INTRINSIC and no UCLASS.
INTRINSIC = re.compile(r'\bDECLARE_(?:CASTED_)?CLASS(?:_INTRINSIC\w*)?'
                       r'\s*\(\s*U(\w+)\s*,\s*U(\w+)\s*[,)]')


def runtime(ue_root):
    return os.path.join(ue_root, 'Engine', 'Source', 'Runtime')


def folders(ue_root):
    """[(module, folder, path, present)] of every folder MODULES names."""
    out = []
    for module, dirs in MODULES.items():
        for d in dirs:
            path = os.path.join(runtime(ue_root), d)
            out.append((module, d, path, os.path.isdir(path)))
    return out


def missing_required(ue_root):
    """The required folders not in `ue_root` (empty when it can be read)."""
    return [d for _m, d, _p, present in folders(ue_root) if not present and d not in OPTIONAL]


def parse(ue_root, log=None):
    """(raw, report) of the engine headers at `ue_root`."""
    from riftlord.core import bpkitlink
    bpkitlink.install()
    from bpkit import headers
    log = log or (lambda _s: None)
    raw = headers.empty()
    report = {'read': [], 'skipped': [], 'headers': 0}
    intrinsics = {}
    for module, d, path, present in folders(ue_root):
        if not present:
            report['skipped'].append(d)
            log('skipped %s (not in this engine)' % d)
            continue
        t0 = time.time()
        headers.parse_tree(path, module, raw)
        count = 0
        for base, _dirs, files in os.walk(path):
            for f in sorted(files):
                if f.endswith('.h'):
                    count += 1
                    with open(os.path.join(base, f), encoding='utf-8', errors='replace') as fh:
                        for m in INTRINSIC.finditer(fh.read()):
                            if m.group(1) != m.group(2):
                                intrinsics.setdefault(m.group(1), m.group(2))
        report['read'].append(d)
        report['headers'] += count
        log('read %s: %d headers in %.1f s' % (d, count, time.time() - t0))
    raw['intrinsics'] = intrinsics
    return raw, report


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--ue', help='the UE 4.27 folder (default: riftlord.core.ueroot.find())')
    ap.add_argument('--out', help='write the result as JSON here')
    args = ap.parse_args(argv)
    root = args.ue
    if not root:
        from riftlord.core import ueroot
        root = ueroot.find()
        if not root:
            raise SystemExit('no Unreal Engine 4.27 found')
    raw, report = parse(root, log=lambda s: print(s, file=sys.stderr))
    for k in ('classes', 'structs', 'enums', 'delegates', 'intrinsics'):
        print('%-10s %d' % (k, len(raw[k])))
    print('skipped', report['skipped'])
    if args.out:
        with open(args.out, 'w') as fh:
            json.dump({'raw': raw, 'report': report}, fh)


if __name__ == '__main__':
    main()
