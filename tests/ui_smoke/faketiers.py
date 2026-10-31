"""Stand-in Scramble tier mods for the `scrambletiers` UI smoke case and
tests/offline/test_scramble_tiers.py: bucket tables cloned from the shipped first tier.

    python3 tests/ui_smoke/faketiers.py PAKS_DIR     # PAKS_DIR/~mods/TierMod_P.pak: Tier_21
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.build import modtable  # noqa: E402
from riftlord.core import paksource  # noqa: E402
from riftlord.missions import missionscramble as M  # noqa: E402
from riftlord.missions import scrambletable  # noqa: E402

#: The stand-in tier mod: its key, and its one tier as the editor cooks it ('Tier', 22).
MOD = 'TierMod'
TIER = ('Tier', 22)


def guid_bytes(n):
    """A test Guid, 16 bytes as stored."""
    return struct.pack('<4I', 0x7E570000 + n, 0x11, 0x22, 0x33)


def registered_path(mod):
    """A mod's registered bucket table."""
    return '/Game/Mods/%s/Tables/%s_RandomModeBucketProtos' % (mod, mod)


def bucket_files(rows, table_path=None, keep_shipped=False):
    """{pak entry: bytes} of a bucket table holding `rows` [(string, number, guid)] cloned
    from the shipped first tier (after the shipped rows with keep_shipped), at
    `table_path` (default: the shipped table's own path)."""
    t = scrambletable.bucket_table()
    template = t.rows[0]
    out = list(t.rows) if keep_shipped else []
    for name, number, guid in rows:
        row = M._row(t, template, (name, number))
        t.field(row, 'Guid').payload = bytearray(guid)
        out.append(row)
    t.rows = out
    path = table_path or M.BUCKET_TABLE
    if table_path:
        modtable.rename_package(t.pkg, M.BUCKET_TABLE, M.BUCKET_TABLE.rsplit('/', 1)[-1],
                                path, path.rsplit('/', 1)[-1])
    ua, ux = t.write()
    stem = 'OMD/Content' + path[len('/Game'):]
    return {stem + '.uasset': ua, stem + '.uexp': ux}


def write(paks_dir):
    """PAKS_DIR/~mods/TierMod_P.pak with TierMod's registered Tier_21. Returns its path."""
    folder = os.path.join(paks_dir, '~mods')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, '%s_P.pak' % MOD)
    paksource.write_pak(path, bucket_files([TIER + (guid_bytes(21),)], registered_path(MOD)))
    return path


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(write(sys.argv[1]))
