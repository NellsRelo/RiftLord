"""The merged EntitlementsProtos for installed paks that still ship a copy of it.

Missions now declare their entitlements in their own content table, which
OrcsMustUI reads (makemod.entitle). Paks built before that ship a whole copy of
`/Game/Data/EntitlementsProtos`, and the game reads only the copy in the last-sorting
pak, so while any is installed, MERGED_PAK holds the shipped table plus every entry
those paks add. With none installed, MERGED_PAK is removed.

    merge(mods_dir) -> report
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: The pak written into ~mods; its name sorts after every mission pak and after
#: zz_OrcsMustUI_P.pak, so its copy of the table is the one the game reads.
MERGED_PAK = 'zz_RiftLordEntitlements_P.pak'
#: The table's path inside a pak.
INSIDE = 'OMD/Content/Data/EntitlementsProtos'
#: The arrays of mission pointers merged per row.
ARRAYS = ('MissionEntitlements', 'MissionUnlocks')


def _paks(mods_dir):
    out = []
    for folder, _dirs, files in os.walk(mods_dir):
        for name in files:
            if name.lower().endswith('.pak') and name != MERGED_PAK:
                out.append(os.path.join(folder, name))
    return sorted(out)


def _table(ua, ux):
    from riftlord.build import missionproto
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(ua, ux)
    pkg.load_payloads()
    return missionproto.Table(pkg)


def merge(mods_dir):
    """Write MERGED_PAK into `mods_dir` from every pak there that ships the table, or
    remove it when every pak was read and none does. Returns {'from': [pak names],
    'added': n, 'pak': path or None, 'removed': bool, 'unreadable': [pak names]}."""
    from riftlord.build import makemod
    from riftlord.build import missionproto
    from riftlord.core import paksource
    out = os.path.join(mods_dir, MERGED_PAK)
    sources, unreadable = [], []
    for path in _paks(mods_dir):
        try:
            files = paksource.entries(path)
        except Exception:                            # noqa: BLE001
            unreadable.append(os.path.basename(path))
            continue
        if INSIDE + '.uasset' in files and INSIDE + '.uexp' in files:
            sources.append((os.path.basename(path),
                            _table(files[INSIDE + '.uasset'], files[INSIDE + '.uexp'])))
    if not sources:
        removed = not unreadable and os.path.exists(out)
        if removed:
            from riftlord.core import pakguard
            pakguard.remove(out)
        return {'from': [], 'added': 0, 'removed': removed, 'unreadable': unreadable,
                'pak': out if os.path.exists(out) else None}
    merged = missionproto.read(makemod.ENTITLEMENTS_TABLE)
    added = 0
    for _name, table in sources:
        for row in table.rows:
            mine = merged.row(row['name'])
            if mine is None:
                continue
            for array in ARRAYS:
                theirs, ours = table.field(row, array), merged.field(mine, array)
                if theirs is None or ours is None:
                    continue
                have = set(merged.struct_array(ours)[2])
                for element in table.struct_array(theirs)[2]:
                    if element not in have:
                        merged.append_struct(ours, element)
                        have.add(element)
                        added += 1
    ua, ux = merged.write()
    paksource.write(out, {INSIDE + '.uasset': ua, INSIDE + '.uexp': ux})
    return {'from': [n for n, _t in sources], 'added': added, 'pak': out,
            'removed': False, 'unreadable': unreadable}
