"""Copy an OMD3 level and give it an onslaught of its own.

The waves a mission plays come from its MAP, not its mission proto: a level's
persistent package names its coordinator directly. So a custom mission with
custom waves needs a copy of the persistent level pointed at a copy of the
coordinator -- and only the persistent level, because the geometry, lighting and
audio live in sublevels it references by absolute path and which are left alone.

    python3 makelevel.py --map Scenario_01 --mod MyMod --out .
"""

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.onslaught import bppatch  # noqa: E402
from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.build import modtable  # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

ONSLAUGHT_ROOT = '/Game/Data/Onslaughts/'


def coordinator_of(map_name):
    """The coordinator a level names, read from the level itself."""
    from riftlord.core import gamepath
    files = navpatch.fetch_map(mapdata.map_path(map_name), gamepath.scratch('.levelscan', gamepath.CODE_ROOT))
    from riftlord.formats.ue.uheader import parse as parse_header
    names = parse_header(files['.umap'])['names']
    # A shipped level names a coordinator under the game's own onslaught folder; a
    # custom mission built by these tools keeps its own under `/Game/Mods/<mod>/`, so a
    # mod map has to be recognised too or the editor cannot open what it produced.
    # A shipped level names a coordinator under the game's own onslaught folder; a
    # custom mission built by these tools keeps its own under `/Game/Mods/<mod>/`, so a
    # mod map has to be recognised too or the editor cannot open what it produced. The
    # base class itself is not a level's coordinator.
    paths = [n for n in names
             if 'Onslaught' in n.rsplit('/', 1)[-1]
             and (n.startswith(ONSLAUGHT_ROOT) or n.startswith('/Game/Mods/'))
             and not n.endswith('BP_OMDOnslaughtCoordinator')]
    if not paths:
        raise SystemExit('%s names no onslaught coordinator' % map_name)
    path = paths[0]
    return path, path.rsplit('/', 1)[-1]


def author_waves(fill='Kobold', last='LizardmanRanger', per_wave=5,
                 waves=None, new_wave=None):
    """A mutate that writes a roster into a coordinator's waves.

    `waves` is {wave number: [unit, ...]} and `new_wave` the list for the
    appended final wave; each wave's live spawn calls cycle through its units.
    Without them every wave becomes `fill` and the new one `last`. The wave the
    new one follows is given the trailing break it now needs, since a break
    ends a wave.
    """
    from riftlord.onslaught import onslaught
    from riftlord.formats.ue.kismet import find_bytecode, memory_map

    def mutate(pkg, names, resolve):
        protos = onslaught.protos()
        plan = dict(waves or {})
        tail = list(new_wave or [last])
        for unit in [u for v in plan.values() for u in v] + tail + [fill]:
            if unit not in protos:
                raise SystemExit('unknown unit %r' % unit)
        export = next(x for x, n in bppatch.functions(pkg, names, resolve)
                      if n.startswith('ExecuteUbergraph'))
        _o, _m, _s, w = find_bytecode(export['data'], names, resolve)
        mm = memory_map(w.nodes)
        eps = bppatch.entry_points(pkg, names, resolve)
        wmap, _seen = bppatch.wave_map(mm, eps['Onslaught_Start'], names, resolve)
        blocks = set(wmap.values())
        spawner = min(sp for _wv, sp in wmap)
        numbered = sorted(wv for wv, sp in wmap if sp == spawner)
        close_wave = max(numbered)
        donor_wave = max(wv for wv in numbered if wv != close_wave)
        donor = bppatch.block_statements(
            mm, wmap[(donor_wave, spawner)],
            bppatch.block_bounds(mm, [], wmap[(donor_wave, spawner)]) | (blocks - {wmap[(donor_wave, spawner)]}))

        def edit(nodes, mmap):
            for wave in numbered:
                if wave == close_wave:
                    continue
                units = plan.get(wave) or [fill]
                n = 0
                for i in bppatch.block_sequence(
                        mmap, wmap[(wave, spawner)],
                        blocks - {wmap[(wave, spawner)]}):
                    for call in bppatch.spawn_calls_in(mmap[i][2]):
                        live = n < per_wave
                        bppatch.set_spawn(
                            call['call'], guid=protos[units[n % len(units)]],
                            count=1,
                            difficulty=(bppatch.ALL_DIFFICULTIES if live
                                        else bppatch.OTHER_DIFFICULTY))
                        n += 1
            clones, entry = bppatch.clone_block(mmap, donor,
                                                wmap[(donor_wave, spawner)])
            at = len(nodes) - 1
            for k, c in enumerate(clones):
                nodes.insert(at + k, c)
            slots = [c for node in clones for c in bppatch.spawn_calls_in(node)]
            for k, call in enumerate(slots):
                bppatch.set_spawn(
                    call['call'], guid=protos[tail[k % len(tail)]], count=1,
                    difficulty=(bppatch.ALL_DIFFICULTIES if k < max(per_wave, len(tail))
                                else bppatch.OTHER_DIFFICULTY))
            brk = bppatch.clone_break(mmap, resolve, 'OnslaughtWaveBreak',
                                      seconds=10.0)
            if brk is not None:
                seq = bppatch.block_sequence(
                    mmap, wmap[(donor_wave, spawner)],
                    blocks - {wmap[(donor_wave, spawner)]})
                nodes.insert(nodes.index(mmap[seq[-1]][2]), brk)
            return bppatch.append_wave(nodes, mmap, names, resolve,
                                       eps['Onslaught_Start'], close_wave,
                                       spawner, entry, wmap[(close_wave, spawner)])

        bppatch.edit_ubergraph(pkg, names, resolve, edit)
    return mutate


def build_coordinator(source_name, new_path, mutate=None):
    """A renamed copy of a coordinator, optionally with its waves edited."""
    pkg, names, resolve = bppatch.load(source_name)
    if mutate is not None:
        mutate(pkg, names, resolve)
    new_object = new_path.rsplit('/', 1)[-1]
    modtable.retarget(pkg,
                      exact={ONSLAUGHT_ROOT + source_name: new_path},
                      substring=[(source_name, new_object)])
    return pkg.write()


def build_level(map_name, new_path, coord_path, coord_object,
                sublevels=None, keep_sublevels=None):
    """A renamed copy of a persistent level, pointed at another coordinator.

    `sublevels` optionally re-points streamed sublevels: {old package path:
    new package path}. Each one is referenced by a single `WorldAsset`
    SoftObjectProperty holding the whole path, so swapping one is a name-table
    rewrite like every other rename here -- which is what lets a custom map
    keep a shipped level's gameplay actors while bringing its own geometry.

    `keep_sublevels` optionally names the ONLY host sublevels the copy still streams
    (host package paths, before any re-pointing); the rest are dropped from the World's
    streaming list. Re-pointing `_Castle` alone leaves the host's `_Environment`,
    `_Foliage` and `_VFX` loading, which is why a converted map showed the host's
    terrain standing in its own geometry.
    """
    from riftlord.core import gamepath
    files = navpatch.fetch_map(mapdata.map_path(map_name),
                               gamepath.scratch('.levelcopy', gamepath.CODE_ROOT))
    pkg = Package(open(files['.umap'], 'rb').read(),
                  open(files['.uexp'], 'rb').read())
    pkg.load_payloads()
    old_path = mapdata.map_path(map_name)
    old_coord_path, old_coord_object = coordinator_of(map_name)
    new_object = new_path.rsplit('/', 1)[-1]
    # The level's own identity, and nothing else: every other Scenario_NN name
    # is a sublevel or asset that must keep pointing at the shipped copy.
    # `Scenario_01_C` is a PREFIX of `Scenario_01_Castle`, so the level's
    # derived names are matched exactly. Only two substrings are unambiguous:
    # the ubergraph name (which also appears inside a VO reference property)
    # and the coordinator's own name.
    swaps = {}
    for old_sub, new_sub in (sublevels or {}).items():
        leaf_old, leaf_new = old_sub.rsplit('/', 1)[-1], new_sub.rsplit('/', 1)[-1]
        swaps['%s.%s' % (old_sub, leaf_old)] = '%s.%s' % (new_sub, leaf_new)
        swaps[old_sub] = new_sub
    # THE WORLD'S OWN ASSET PATH, WHATEVER THE SHIPPED DATA CALLS IT.
    #
    # A soft object path is `<package>.<asset>`, and this assumed the asset is named
    # after the map -- which is true of most levels and NOT of Scenario_12, _14 and
    # _18, which hold `<package>.Scenario` although their world export is named after
    # the map like everywhere else. Those three kept their spawners' `StartingDoor` and
    # `StartingPathPoint` naming the SHIPPED package, which in the copy resolves to
    # null, so the soft-reference check refused the build and Rift Lord could not save
    # those levels at all. Read from the name table instead of assumed: there is
    # exactly one such entry per level, and it is this self-reference.
    exact = dict(swaps)
    exact.update({old_path: new_path,
                  map_name: new_object,
                  map_name + '_C': new_object + '_C',
                  'Default__%s_C' % map_name: 'Default__%s_C' % new_object,
                  old_coord_path: coord_path})
    world_base, world_number = world_name(pkg)
    # A world named with a trailing number (`Scenario` + 17 = `Scenario_16`) keeps its
    # name in the copy, so its own references keep that object name and number.
    self_object = world_base if world_number else new_object
    for inside in {s for s, _h in pkg.names if s.startswith(old_path + '.')} \
            | {'%s.%s' % (old_path, map_name)}:
        exact[inside] = '%s.%s' % (new_path, self_object)
    # Before the rename, while the entries still name the HOST's packages.
    if keep_sublevels is not None:
        from riftlord.build import levelbuild
        dropped, kept = levelbuild.keep_world_streams(pkg, keep_sublevels)
        print('   sublevels: kept %d, dropped %d%s'
              % (len(kept), len(dropped),
                 (' (%s)' % ', '.join(p.rsplit('/', 1)[-1] for p in dropped if p))
                 if dropped else ''))
    own = {i for i, (s, _h) in enumerate(pkg.names) if s.startswith(old_path + '.')}
    changed = modtable.retarget(
        pkg, exact=exact,
        substring=[('ExecuteUbergraph_' + map_name,
                    'ExecuteUbergraph_' + new_object),
                   (old_coord_object, coord_object)])
    if not world_number:
        clear_self_numbers(pkg, own)
    return pkg.write(), changed


def world_name(pkg):
    """(base string, FName number) of the level's World export."""
    from riftlord.build import levelbuild
    names = [s for s, _h in pkg.names]
    for e in pkg.exports:
        if levelbuild.resolve(pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] == 'World':
            idx, number = struct.unpack_from('<ii', e['meta'], 16)
            return names[idx], number
    raise SystemExit('the level has no World export')


def _full(base, number):
    return base if not number else '%s_%d' % (base, number - 1)


def self_ref_problems(pkg, own_path):
    """Soft references to the level's own actors that do not name its World object."""
    names = [s for s, _h in pkg.names]
    world = _full(*world_name(pkg))
    want = '%s.%s' % (own_path, world)
    bad = 0
    indices = [i for i, s in enumerate(names) if s.startswith(own_path + '.')]
    for e in pkg.exports:
        buf = e.get('data') or b''
        for idx in indices:
            head = struct.pack('<i', idx)
            at = buf.find(head)
            while at >= 0:
                if at + 12 + len(SELF_SUBPATH) <= len(buf) and \
                        buf[at + 12:at + 12 + len(SELF_SUBPATH)] == SELF_SUBPATH:
                    number = struct.unpack_from('<i', buf, at + 4)[0]
                    if _full(names[idx], number) != want:
                        bad += 1
                at = buf.find(head, at + 1)
    return bad


#: The sub-path every soft reference to one of the level's own actors starts with.
SELF_SUBPATH = b'PersistentLevel.'


def clear_self_numbers(pkg, indices):
    """Zero the FName number on soft references to the level's own package.

    A map named with a trailing number (`Scenario_16`) is stored as the base name plus
    a number, so a renamed entry keeps that number and every reference resolves to
    `<new path>_16`. Each reference is (name index, number, sub-path length,
    `PersistentLevel.`...). Returns how many were cleared."""
    cleared = 0
    for e in pkg.exports:
        data = e.get('data')
        if not data:
            continue
        buf = bytearray(data)
        for idx in indices:
            head = struct.pack('<i', idx)
            at = buf.find(head)
            while at >= 0:
                num = struct.unpack_from('<i', buf, at + 4)[0] if at + 8 <= len(buf) else 0
                sub_len = struct.unpack_from('<i', buf, at + 8)[0] if at + 12 <= len(buf) else 0
                if num > 0 and 0 < sub_len < 512 and \
                        buf[at + 12:at + 12 + len(SELF_SUBPATH)] == SELF_SUBPATH:
                    struct.pack_into('<i', buf, at + 4, 0)
                    cleared += 1
                at = buf.find(head, at + 1)
        if cleared:
            e['data'] = bytes(buf)
    return cleared


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--map', default='Scenario_01')
    ap.add_argument('--mod', default='MyMod')
    ap.add_argument('--out')
    args = ap.parse_args()

    coord_src_path, coord_src = coordinator_of(args.map)
    print('%s uses %s' % (args.map, coord_src))
    root = '/Game/Mods/%s' % args.mod
    coord_path = '%s/%s_Onslaught01' % (root, args.mod)
    level_path = '%s/Maps/%s_Map01/%s_Map01' % (root, args.mod, args.mod)

    cfiles = build_coordinator(coord_src, coord_path)
    lfiles, changed = build_level(args.map, level_path, coord_path,
                                  coord_path.rsplit('/', 1)[-1])
    print('coordinator -> %s (%d + %d bytes)' % (coord_path, len(cfiles[0]), len(cfiles[1])))
    print('level       -> %s (%d + %d bytes), %d names retargeted'
          % (level_path, len(lfiles[0]), len(lfiles[1]), changed))

    back = Package(lfiles[0], lfiles[1])
    names = [s for s, _h in back.names]
    left = [n for n in names if args.map in n and 'Images' not in n]
    print()
    print('names still mentioning %s (should be sublevels and built data only):' % args.map)
    for n in sorted(left):
        print('   ', n)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        for path, blobs, ext in ((coord_path, cfiles, '.uasset'),
                                 (level_path, lfiles, '.umap')):
            base = os.path.join(args.out, path.rsplit('/', 1)[-1])
            open(base + ext, 'wb').write(blobs[0])
            open(base + '.uexp', 'wb').write(blobs[1])
        print('\nwrote to %s' % args.out)


if __name__ == '__main__':
    main()
