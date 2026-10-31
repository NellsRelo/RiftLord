"""Does the NavPower loader check the container's +12 hash word?

This gates everything about *writing* a navgraph. The flight-graph format is decoded
(see navvol.py) and a generator is buildable, but only if a buffer whose hash we cannot
compute is still accepted. One flipped bit answers it.

The test flips the low bit of the hash and changes nothing else -- same length, same
payload, same flags -- so a rejection can only be the hash.

    python3 hashtest.py --map Scenario_Ice_01 --build
    python3 hashtest.py --map Scenario_Ice_01 --install control
    python3 hashtest.py --map Scenario_Ice_01 --install volumes

Scenario_Ice_01 (Northern Rampart) is the default target because it is the only kind of
level that carries **both** buffers, and its wisps ride the flyer chain by default. That
makes each pak self-controlling: flipping the volumetric hash should leave ground units
walking, and flipping the surface hash should leave flyers flying. If one buffer dies
and the other lives, the hash is checked per buffer and we have learnt which.

Install ONE pak at a time. Three paks overriding the same map is a race, not a test,
so --install removes the other two.

What to look for, from the known signature of a refused graph: no mover snap, no
minimap dots, and no motion anywhere. A stuck unit still attacks what is beside it, so
judge by travel, never by "it hit the door".
"""

import argparse
import os
import shutil
import struct
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.formats.ue import pakv4  # noqa: E402

OUT = gamepath.beside_code(gamepath.CODE_ROOT, 'patched/hashtest')
HASH_AT = 12

#: One pak per question. The control comes first because a failure that reproduces on
#: the control is a packaging failure and says nothing about the hash.
CASES = {
    'control': ('713-HashControl_P.pak', None,
                'repacked unchanged -- proves the pak overrides the base game'),
    'surface': ('714-HashSurface_P.pak', 'surface',
                'surface hash flipped -- do GROUND units still move?'),
    'volumes': ('715-HashVolumes_P.pak', 'volumes',
                'volumetric hash flipped -- do FLYERS still fly?'),
}


def build_one(map_name, which, out_dir):
    """Write the map with one container's hash flipped. Returns a report."""
    work = os.path.join(out_dir, which or 'control')
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)
    game_path = navpatch.map_path(map_name)
    files = navpatch.fetch_map(game_path, work)
    found = navpatch.nav_buffers(files['.umap'], files['.uexp'])
    if not found:
        raise SystemExit('no navgraph buffer in %s' % map_name)
    _e, surf_off, surf_size, vol_off, vol_size = found
    if which == 'volumes' and not vol_size:
        raise SystemExit('%s ships no volumetric buffer -- pick a flight level'
                         % map_name)

    with open(files['.uexp'], 'rb') as fh:
        uexp = bytearray(fh.read())
    before = bytes(uexp)

    at = None
    if which == 'surface':
        at = surf_off + HASH_AT
    elif which == 'volumes':
        at = vol_off + HASH_AT

    old = new = None
    if at is not None:
        old = struct.unpack_from('<I', uexp, at)[0]
        new = old ^ 1
        struct.pack_into('<I', uexp, at, new)
        with open(files['.uexp'], 'wb') as fh:
            fh.write(uexp)

    #: Readback, because the writer's own reader is blind to its own damage. Confirm
    #: exactly four bytes moved and that both containers still describe themselves.
    with open(files['.uexp'], 'rb') as fh:
        after = fh.read()
    differing = [i for i in range(len(before)) if before[i] != after[i]]
    checks = []
    checks.append(('package length unchanged', len(after) == len(before)))
    if at is None:
        checks.append(('nothing changed', not differing))
    else:
        # Flipping one bit moves ONE byte, not four. The check that matters is that
        # every byte which moved lies inside the hash word and nowhere else.
        checks.append(('something changed', bool(differing)))
        checks.append(('every changed byte is inside the hash word',
                       all(at <= i < at + 4 for i in differing)))
    for label, off, size in (('surface', surf_off, surf_size),
                             ('volumes', vol_off, vol_size)):
        if not size:
            continue
        w = struct.unpack_from('<6I', after, off)
        checks.append(('%s payload still == size - 24' % label,
                       w[2] + 24 == size))
        checks.append(('%s version still 2' % label, w[1] == 2))
        checks.append(('%s flags still 0x01000001' % label, w[5] == 0x01000001))

    return {'files': files, 'game_path': game_path, 'which': which,
            'old': old, 'new': new, 'at': at, 'checks': checks,
            'surface': (surf_off, surf_size), 'volumes': (vol_off, vol_size)}


def pak_for(rep, name, dest_dir):
    inner = rep['game_path'].replace('/Game/', 'OMD/Content/')
    dest = os.path.join(dest_dir, name)
    os.makedirs(dest_dir, exist_ok=True)
    info = pakv4.write(dest, {
        inner + '.umap': rep['files']['.umap'],
        inner + '.uexp': rep['files']['.uexp'],
    })
    pakv4.read(info['pak'])          # refuses to report success on an unreadable pak
    return info


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--map', default='Scenario_Ice_01')
    ap.add_argument('--build', action='store_true', help='build all three paks')
    ap.add_argument('--install', choices=sorted(CASES),
                    help='install exactly one, removing the other two')
    a = ap.parse_args()
    if not a.build and not a.install:
        ap.error('need --build or --install')

    mods = os.path.join(navpatch.PAK_DIR, '~mods')
    wanted = sorted(CASES) if a.build else [a.install]

    for case in wanted:
        name, which, why = CASES[case]
        rep = build_one(a.map, which, OUT)
        info = pak_for(rep, name, OUT if a.build else mods)
        print('%-8s %s' % (case, why))
        if rep['at'] is not None:
            print('         hash at uexp +%d: %#010x -> %#010x'
                  % (rep['at'], rep['old'], rep['new']))
        bad = [lbl for lbl, ok in rep['checks'] if not ok]
        print('         readback %d checks, %s'
              % (len(rep['checks']), 'all pass' if not bad else 'FAILED: %s' % bad))
        if bad:
            raise SystemExit('refusing to ship a pak that fails readback')
        print('         %s  (%d files, %d bytes)'
              % (info['pak'], info['files'], info['size']))

    if a.install:
        for case, (name, _w, _why) in CASES.items():
            if case == a.install:
                continue
            stale = os.path.join(mods, name)
            if os.path.exists(stale):
                from riftlord.core import pakguard
                pakguard.remove(stale)
                print('removed  %s' % stale)
        print()
        print('Installed %r for %s. Load the level and judge by TRAVEL:' % (a.install, a.map))
        print('  ground units walking their routes, and flyers crossing their chain.')
        print('No motion at all on the control means the pak pipeline, not the hash.')
    else:
        print()
        print('Built in %s' % OUT)
        print('Install one at a time:  python3 hashtest.py --map %s --install control'
              % a.map)


if __name__ == '__main__':
    main()
