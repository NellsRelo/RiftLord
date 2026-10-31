"""Build a mod's own proto DataTable by cloning a shipped one and renaming it.

A cooked DataTable carries its RowStruct as an import, its own package path as a
name, and its asset name as the export's ObjectName. Cloning one and rewriting
those three things gives a valid table of the same row type that a mod owns
outright -- so its rows can be registered with protoreg.py instead of being
patched into a shipped file.

    python3 modtable.py --from MissionProtos --keep Scenario_TestA \\
        --path /Game/Mods/MyMod/MyMod_MissionProtos --out .
"""

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build import missionproto  # noqa: E402
from riftlord.formats.ue.uassetio import Package, fname_hashes  # noqa: E402

SOURCES = {
    'MissionProtos': '/Game/Data/Campaign/MissionProtos',
    'CampaignProtos': '/Game/Data/Campaign/CampaignProtos',
}


def retarget(pkg, exact=(), substring=()):
    """Rewrite name-table entries. Returns how many changed.

    `exact` replaces a whole name, `substring` replaces part of one -- which is
    how an asset's derived names (`_C`, `Default__`, `ExecuteUbergraph_`) all
    follow from renaming the asset itself. Order matters: exact first, so a
    substring rule cannot mangle a path that has its own replacement.
    """
    exact = dict(exact)
    changed = 0
    for i, (s, _h) in enumerate(list(pkg.names)):
        new = exact.get(s)
        if new is None:
            new = s
            for old, sub in substring:
                if old in new:
                    new = new.replace(old, sub)
        if new != s:
            pkg.names[i] = (new, fname_hashes(new))
            changed += 1
    return changed


def rename_package(pkg, old_path, old_object, new_path, new_object):
    """Point a cloned package at a new path and asset name.

    The name table holds both strings, so they are replaced in place with fresh
    hashes; the export's ObjectName index is left pointing at the same slot.
    """
    names = [s for s, _h in pkg.names]
    changed = []
    for i, s in enumerate(names):
        if s == old_path:
            pkg.names[i] = (new_path, fname_hashes(new_path))
            changed.append('path')
        elif s == old_object:
            pkg.names[i] = (new_object, fname_hashes(new_object))
            changed.append('object')
    if 'object' not in changed:
        raise SystemExit('the source package does not name itself %r' % old_object)
    return changed


def build(source, keep, new_path):
    """A cloned table holding only `keep`, renamed to `new_path`."""
    if source not in SOURCES:
        raise SystemExit('unknown source %r; try %s' % (source, list(SOURCES)))
    t = missionproto.read(SOURCES[source])
    wanted = list(keep)
    missing = [r for r in wanted if t.row(r) is None]
    if missing:
        raise SystemExit('no such row(s): %s' % ', '.join(missing))
    t.rows = [r for r in t.rows if r['name'] in wanted]
    if not t.rows:
        raise SystemExit('refusing to build an empty table')
    new_object = new_path.rsplit('/', 1)[-1]
    rename_package(t.pkg, SOURCES[source], source, new_path, new_object)
    t.export['data'] = t.serialise()
    return t.pkg.write(), [r['name'] for r in t.rows]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--from', dest='source', default='MissionProtos',
                    choices=sorted(SOURCES))
    ap.add_argument('--keep', action='append', default=[], required=True,
                    help='repeatable; row names to carry over')
    ap.add_argument('--path', required=True, metavar='/Game/PATH/NAME')
    ap.add_argument('--out')
    args = ap.parse_args()

    (ua, ux), rows = build(args.source, args.keep, args.path)
    print('built %s from %s with %d row(s): %s'
          % (args.path, args.source, len(rows), ', '.join(rows)))

    back = Package(ua, ux)
    back.load_payloads()
    names = [s for s, _h in back.names]
    obj = names[struct.unpack_from('<I', back.exports[0]['meta'], 16)[0]]
    t = missionproto.Table(back)
    bad = [s for s, h in back.names if fname_hashes(s) != h]
    print('re-read: export object name %r, %d row(s) %s, name hashes %s'
          % (obj, len(t.rows), [r['name'] for r in t.rows],
             'all valid' if not bad else 'BAD: %s' % bad[:2]))
    if args.path.rsplit('/', 1)[-1] != obj:
        raise SystemExit('rename did not take')

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        base = os.path.join(args.out, obj)
        with open(base + '.uasset', 'wb') as fh:
            fh.write(ua)
        with open(base + '.uexp', 'wb') as fh:
            fh.write(ux)
        print('wrote %s.uasset (%d) and .uexp (%d)' % (base, len(ua), len(ux)))


if __name__ == '__main__':
    main()
