"""A stand-in item mod for the `unlocks` UI smoke case and tests/offline/test_missionunlocks.py:
three traps cloned from shipped rows into the mod's own table, one of them DLC-gated by the
mod's own entitlement table.

    python3 tests/ui_smoke/fakeunlocks.py PAKS_DIR    # PAKS_DIR/~mods/UnlockMod_P.pak
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.build import missionproto, modtable  # noqa: E402
from riftlord.core import paksource  # noqa: E402

MOD = 'UnlockMod'
TRAPS = '/Game/Data/TrapProtos'
ENTITLEMENTS = '/Game/Data/EntitlementsProtos'
#: The stand-in rows: (row, shipped template, display name, Guid as stored). The template
#: carries the flags: Haymaker none, SawBladeLauncher_DLC3 bRequiresEntitlement.
OPEN = ('UnlockMod_OpenTrap', 'Haymaker', 'Stand-in Open Trap',
        struct.pack('<4I', 0x0B1E0001, 0x11, 0x22, 0x33))
ENTITLED = ('UnlockMod_EntitledTrap', 'SawBladeLauncher_DLC3', 'Stand-in Entitled Trap',
            struct.pack('<4I', 0x0B1E0002, 0x11, 0x22, 0x33))
#: Free by its own row, gated by the mod's entitlement table (ItemEntitlements).
LISTED = ('UnlockMod_ListedTrap', 'Haymaker', 'Stand-in Listed Trap',
          struct.pack('<4I', 0x0B1E0003, 0x11, 0x22, 0x33))


def _entry(path, blobs):
    stem = 'OMD/Content' + path[len('/Game'):]
    return {stem + '.uasset': blobs[0], stem + '.uexp': blobs[1]}


def trap_table():
    """{pak entry: bytes} of the mod's trap table."""
    t = missionproto.read(TRAPS)
    rows = []
    for name, template, title, guid in (OPEN, ENTITLED, LISTED):
        row = t.clone(template, name)
        t.field(row, 'Guid').payload = bytearray(guid)
        t.set_text(t.field(row, 'DisplayName'), 'TrapProtos', name + '_Name', title)
        rows.append(row)
    t.rows = rows
    path = '/Game/Mods/%s/Tables/%s_TrapProtos' % (MOD, MOD)
    modtable.rename_package(t.pkg, TRAPS, 'TrapProtos', path, path.rsplit('/', 1)[-1])
    return _entry(path, t.write())


def entitlement_table():
    """{pak entry: bytes} of the mod's Extends entitlement table: a DLC3_Bundle row whose
    ItemEntitlements names LISTED alone (ProtoRegistry 1.6's Steam<AppId> folder)."""
    t = missionproto.read(ENTITLEMENTS)
    row = t.row('DLC3_Bundle')
    field = t.field(row, 'ItemEntitlements')
    _n, tag, items = t.struct_array(field)
    element = bytearray(items[0])
    element[-24:-8] = LISTED[3]
    field.payload = t._pack_array(tag, [bytes(element)])
    t.rows = [row]
    path = '/Game/Mods/%s/Extends/Steam3291710/%s_EntitlementsProtos' % (MOD, MOD)
    modtable.rename_package(t.pkg, ENTITLEMENTS, 'EntitlementsProtos', path,
                            path.rsplit('/', 1)[-1])
    return _entry(path, t.write())


def write(paks_dir):
    """PAKS_DIR/~mods/UnlockMod_P.pak. Returns its path."""
    folder = os.path.join(paks_dir, '~mods')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, '%s_P.pak' % MOD)
    paksource.write_pak(path, dict(trap_table(), **entitlement_table()))
    return path


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(write(sys.argv[1]))
