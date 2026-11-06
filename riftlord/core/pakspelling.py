"""One package path, two FName spellings: the check on every mod pak a mission depends on.

A cooked FName stores a trailing `_<digits>` (no leading zero) either split, as a number
('MI_Crystal' #1), or whole ('MI_Crystal_0' #0). The engine names a package from its file
path split (FName from a string), and loads name-table entries unsplit, so the two
spellings are two different names for one file: the async loader then loads the package
twice and fails ("Package ... was reloaded before it even closed the linker", Crystal
Probe A, 2026-10-23).

For each package in a pak this records, per `/Game/` path: the package's own name as the
engine makes it, its top-level exports' stored names, every package import's stored name
and the stored name of each object imported from a package. A path held under two
spellings is a problem.

    python3 -m riftlord.core.pakspelling PAK [PAK ...]
"""

import argparse
import os
import struct

_CACHE = {}


def fname_split(text):
    """(string, number) the engine makes of `text` (apply.fname_split)."""
    base, sep, digits = str(text).rpartition('_')
    if sep and base and digits.isdigit() and (digits == '0' or digits[0] != '0'):
        return base, int(digits) + 1
    return str(text), 0


def spelled(name, number):
    return name if not number else '%s_%d' % (name, number - 1)


def spellings(files):
    """{path: {(name, number): [package entry]}} over the packages in `files`
    ({inner path: bytes})."""
    from riftlord.formats.ue.uassetio import Package
    out = {}

    def add(path, form, where):
        if path.startswith('/Game/'):
            out.setdefault(path, {}).setdefault(form, []).append(where)
    for key, data in files.items():
        stem, ext = os.path.splitext(key)
        if ext not in ('.uasset', '.umap') or not stem.startswith('OMD/Content/'):
            continue
        where = os.path.basename(key)
        own = '/Game/' + stem[len('OMD/Content/'):]
        try:
            pkg = Package(data)
        except Exception:                            # noqa: BLE001
            continue
        names = [n for n, _h in pkg.names]
        add(own, fname_split(own), where)
        for e in pkg.exports:
            meta = e['meta']
            if struct.unpack_from('<i', meta, 12)[0] == 0:
                form = (names[struct.unpack_from('<i', meta, 16)[0]],
                        struct.unpack_from('<i', meta, 20)[0])
                add(own + '.' + spelled(*form), form, where)

        def rec(i):
            r = pkg.imports[i]
            return (names[struct.unpack_from('<i', r, 8)[0]], struct.unpack_from('<i', r, 16)[0],
                    (names[struct.unpack_from('<i', r, 20)[0]], struct.unpack_from('<i', r, 24)[0]))
        for i in range(len(pkg.imports)):
            cls, outer, form = rec(i)
            if outer == 0 and cls == 'Package':
                add(spelled(*form), form, where)
            elif outer < 0:
                ocls, oouter, oform = rec(-outer - 1)
                if ocls == 'Package' and oouter == 0:
                    add(spelled(*oform) + '.' + spelled(*form), form, where)
    return out


def problems(path):
    """[text]: the paths a pak holds under two spellings, read again when it changes."""
    from riftlord.core import paksource
    st = os.stat(path)
    stamp = (st.st_size, st.st_mtime_ns)
    hit = _CACHE.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    out = []
    for p, forms in sorted(spellings(paksource.entries(path)).items()):
        if len(forms) < 2:
            continue
        out.append('%s as %s' % (p, ' and '.join(
            "'%s' #%d (%s)" % (n, k, ', '.join(sorted(set(w))[:2]))
            for (n, k), w in sorted(forms.items()))))
    _CACHE[path] = (stamp, out)
    return out


def dependency_paks(plan):
    """[(mod name, pak path)] of the detected supported-mod paks a mission with `plan`
    depends on: OrcsMustUI always (every mission's tables and notes), and each mod a
    placed piece needs."""
    from riftlord.core import supportedmods
    used = {supportedmods.FEATURES[f][0].id for f in supportedmods.plan_features(plan)}
    out = []
    for mod in supportedmods.MODS:
        if mod.id != 'OrcsMustUI' and mod.id not in used:
            continue
        for full in supportedmods.paks_of(mod.id):
            out.append((mod.name, full))
    return out


def plan_problems(plan):
    """[text]: one line per dependency pak holding a path under two spellings."""
    out = []
    for name, path in dependency_paks(plan):
        bad = problems(path)
        if bad:
            print('%s (%s) holds %d package path(s) under two FName spellings: %s'
                  % (name, os.path.basename(path), len(bad), '; '.join(bad[:3])))
            out.append('the installed %s (%s) has files the game would load twice and crash '
                       'on. Update or reinstall %s, then build again'
                       % (name, os.path.basename(path), name))
    return out


def gates(plan):
    """[(name, ok, detail)]: each dependency pak holds every path under one spelling."""
    return [('%s (%s): every package path under one FName spelling'
             % (name, os.path.basename(path)), not problems(path),
             '; '.join(problems(path)[:3])) for name, path in dependency_paks(plan)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('paks', nargs='+')
    args = ap.parse_args(argv)
    bad = 0
    for path in args.paks:
        got = problems(path)
        print('%s: %s' % (path, 'one spelling each' if not got else '%d problem(s)' % len(got)))
        for line in got:
            print('  ' + line)
        bad += len(got)
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
