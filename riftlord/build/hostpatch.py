"""Edit the host level a converted mission is built on.

    python3 hostpatch.py --show Scenario_01

So far there is one thing to edit: `KillZ`.

A converted level is anchored on OMD2's own player start, which says nothing about
how far the level extends *below* it. Servant Entrance (`UpAndDown`) starts the
player near the top of the map with 2,475 units of level underneath, and
Scenario_01's `KillZ` is **-800**, so the whole lower half sat in the kill region
and the map was unplayable.

Lifting the level clears the kill plane but breaks the spawn: the player still
appears at the host's start, which is then below every floor. Lowering `KillZ`
fixes both at once and costs nothing, because the level being patched is **our own
copy** -- `makelevel.build_level` writes it to `/Game/Mods/<mod>/Maps/...` and the
shipped map is never touched.

`KillZ` is a `FloatProperty`, so this overwrites four bytes in place. No property
changes size, so no export offset moves and the header stays valid -- which is the
reason to do it this way rather than by rebuilding the package.
"""

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build import levelbuild  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

#: Far enough down that no converted level reaches it, and still finite so a
#: player who falls out of the world is eventually cleaned up.
SAFE_KILL_Z = -100000.0


def _properties(uexp, names, start, size):
    """Walk one export's tagged properties, yielding (name, type, payload offset, size)."""
    o = start
    end = start + size
    while o + 8 <= end:
        ni = struct.unpack_from('<i', uexp, o)[0]
        if not 0 <= ni < len(names):
            return
        name = names[ni]
        if name == 'None':
            return
        ti = struct.unpack_from('<i', uexp, o + 8)[0]
        kind = names[ti] if 0 <= ti < len(names) else ''
        psize = struct.unpack_from('<i', uexp, o + 16)[0]
        head = 24 + 1
        if kind == 'BoolProperty':
            head, psize = 26, 0
        elif kind == 'StructProperty':
            head = 24 + 8 + 17
        elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            head = 24 + 8 + 1
        yield name, kind, o + head, psize
        o += head + psize


def find_kill_z(umap, uexp):
    """Every offset in `uexp` holding a WorldSettings `KillZ`, with its value.

    The export's own bytes are located through its `SerialOffset`, which sits at
    offset 36 of the 104-byte FObjectExport and is measured from the start of the
    .umap -- so the .umap's length comes off it to index into the .uexp.
    """
    pkg = Package(umap, uexp)
    pkg.load_payloads()
    names = [n for n, _h in pkg.names]
    found = []
    for e in pkg.exports:
        cls = levelbuild.resolve(
            pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] or ''
        if 'WorldSettings' not in cls:
            continue
        base = struct.unpack_from('<q', e['meta'], 36)[0] - len(umap)
        if base < 0 or base >= len(uexp):
            continue
        for name, kind, at, size in _properties(
                uexp, names, base, len(e['data'])):
            if name == 'KillZ' and kind == 'FloatProperty' and size == 4:
                found.append((at, struct.unpack_from('<f', uexp, at)[0]))
    return found


def set_kill_z(umap, uexp, value=SAFE_KILL_Z):
    """Rewrite every WorldSettings `KillZ`. Returns (uexp, [(old, new)])."""
    out = bytearray(uexp)
    changed = []
    for at, old in find_kill_z(umap, uexp):
        struct.pack_into('<f', out, at, value)
        changed.append((old, value))
    return bytes(out), changed


def patch_files(files, value=SAFE_KILL_Z):
    """Lower `KillZ` in every level package in a mod's file map, in place.

    Returns what changed, so a build can say so rather than hoping.
    """
    changed = {}
    for path in sorted(files):
        if not path.endswith('.umap'):
            continue
        stem = path[:-len('.umap')]
        body = stem + '.uexp'
        if body not in files:
            continue
        umap, uexp = files[path], files[body]
        if not isinstance(umap, (bytes, bytearray)):
            continue
        try:
            new, done = set_kill_z(bytes(umap), bytes(uexp), value)
        except Exception:                                # noqa: BLE001
            continue
        if done:
            files[body] = new
            changed[path] = done
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--show', metavar='MAP', required=True)
    args = ap.parse_args(argv)
    from riftlord.core import gamefiles
    path = [m for m in gamefiles.find(ext='.umap')
            if m.rsplit('/', 1)[-1][:-5] == args.show]
    if not path:
        print('no map called %s' % args.show)
        return 1
    umap, uexp = gamefiles.package(path[0][:-5])
    got = find_kill_z(umap, uexp)
    if not got:
        print('%s: no WorldSettings KillZ found' % args.show)
        return 1
    for at, value in got:
        print('%s: KillZ = %.1f (at uexp offset %d)' % (args.show, value, at))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
