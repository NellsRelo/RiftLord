"""Pick the mission-hub enemy preview that best matches a wave roster.

`FeaturedMinionsClass` on a mission proto points at a Blueprint of posed
skeletal meshes -- a hand-built diorama, not a data list -- so a custom mission
cannot generate one. What it CAN do is point at whichever of the 33 shipped
previews shows the units it actually spawns, instead of inheriting the source
map's.

    python3 featured.py --units Kobold LizardmanRanger
    python3 featured.py --list
"""

import argparse
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

_CACHE = None


def _norm(text):
    return re.sub(r'[^a-z0-9]', '', text.lower())


def previews():
    """{asset path: set of creature names} for every shipped enemy preview."""
    global _CACHE
    if _CACHE is None:
        out = {}
        for key in navpatch._index():
            if 'BP_UI_Enemies' not in key or not key.endswith('.uasset'):
                continue
            path = key[:-7]
            try:
                f = mapdata._fetch(path)
                pkg = Package(open(f['.uasset'], 'rb').read(),
                              open(f['.uexp'], 'rb').read())
            except Exception:
                continue
            names = [s for s, _h in pkg.names]
            found = set()
            for e in pkg.exports:
                nm = names[struct.unpack_from('<I', e['meta'], 16)[0]]
                if nm.endswith('_GEN_VARIABLE') and '_SKM' in nm:
                    found.add(re.sub(r'_SKM\d*_GEN_VARIABLE$', '', nm))
            if found:
                out[path] = found
        _CACHE = out
    return _CACHE


def aliases(unit):
    """The spellings a spawn proto might appear under in a preview."""
    from riftlord.onslaught import onslaught
    names = mapdata.mission_names  # noqa: F401  (kept for symmetry)
    out = {_norm(unit)}
    try:
        f = mapdata._fetch('/Game/Data/AICharacterProtos')
        with open(f['.uexp'], 'rb') as fh:
            runs = mapdata._strings(fh.read())
        for i, (_o, t) in enumerate(runs):
            if t == unit + '_Name' and i + 1 < len(runs):
                out.add(_norm(runs[i + 1][1]))
    except Exception:
        pass
    return out


def best(units, limit=5):
    """[(path, matched units, extras)] ranked by how well a preview fits."""
    wanted = {}
    for u in units:
        wanted[u] = aliases(u)
    scored = []
    for path, creatures in previews().items():
        keys = {_norm(c) for c in creatures}
        hit = {u for u, al in wanted.items() if al & keys}
        if not hit:
            continue
        extras = len(keys) - len(hit)
        # every wanted unit shown, with as little else as possible
        scored.append((len(hit) / len(wanted), -extras, path, sorted(hit), extras))
    scored.sort(reverse=True)
    return [(p, h, x) for _s, _e, p, h, x in scored[:limit]]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--units', nargs='+')
    ap.add_argument('--list', action='store_true')
    args = ap.parse_args()

    if args.list:
        for path, creatures in sorted(previews().items()):
            print('  %-44s %s' % (path.rsplit('/', 1)[-1], ', '.join(sorted(creatures))))
        print('%d previews' % len(previews()))
        return
    if not args.units:
        ap.error('need --units or --list')
    for path, hit, extras in best(args.units):
        print('  %-44s shows %-28s plus %d other(s)'
              % (path.rsplit('/', 1)[-1], ', '.join(hit), extras))


if __name__ == '__main__':
    main()
