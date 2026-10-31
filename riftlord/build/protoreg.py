"""Register an extra proto DataTable with the game, without touching the stock ones.

`BP_OMDProtoManager`'s class default object holds `DataTables`, an array of 34
object references -- the whole proto registry. Adding an entry there makes a
mod's own table part of the registry, so its rows are found alongside the
shipped ones and no shipped file is modified.

That is additive for anything that is a NEW row: a mission, or a whole campaign.
It cannot change an existing row, so listing a custom mission inside a shipped
campaign still means patching CampaignProtos itself.

    python3 protoreg.py --show
    python3 protoreg.py --add /Game/Mods/MyMod/MyMod_Protos --out .
"""

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

MANAGER = '/Game/Game/BP_OMDProtoManager'
FIELD = 'DataTables'


def load():
    files = mapdata._fetch(MANAGER)
    pkg = Package(open(files['.uasset'], 'rb').read(),
                  open(files['.uexp'], 'rb').read())
    pkg.load_payloads()
    return pkg


def _names(pkg):
    return [s for s, _h in pkg.names]


def resolve(pkg, index):
    names = _names(pkg)
    if index < 0:
        return names[struct.unpack_from('<I', pkg.imports[-index - 1], 20)[0]]
    if index > 0:
        return names[struct.unpack_from('<I', pkg.exports[index - 1]['meta'], 16)[0]]
    return 'null'


def find_array(pkg):
    """(cdo, payload offset, header offset, entries) for the DataTables array."""
    names = _names(pkg)
    cdo = pkg.exports[-1]
    d = cdo['data']
    o = 0
    while o < len(d) - 8:
        ni = struct.unpack_from('<i', d, o)[0]
        nm = names[ni] if 0 <= ni < len(names) else '?'
        if nm == 'None':
            break
        ti = struct.unpack_from('<i', d, o + 8)[0]
        kind = names[ti] if 0 <= ti < len(names) else '?'
        size = struct.unpack_from('<i', d, o + 16)[0]
        head = 24 + 8 + 1 if kind in ('ArrayProperty', 'ByteProperty',
                                      'EnumProperty') else 24 + 1
        if kind == 'StructProperty':
            head = 24 + 8 + 17
        elif kind == 'BoolProperty':
            head, size = 26, 0
        if nm == FIELD:
            count = struct.unpack_from('<i', d, o + head)[0]
            entries = list(struct.unpack_from('<%di' % count, d, o + head + 4))
            return cdo, o, head, entries
        o += head + size
    raise SystemExit('no %s array in the class default object' % FIELD)


def add_table(pkg, game_path):
    """Add a DataTable to the registry. Returns the new entry count."""
    cdo, at, head, entries = find_array(pkg)
    obj_name = game_path.rsplit('/', 1)[-1]
    names = _names(pkg)
    for e in entries:
        if e < 0 and names[struct.unpack_from('<I', pkg.imports[-e - 1], 20)[0]] == obj_name:
            raise SystemExit('%s is already registered' % obj_name)
    package_ref = pkg.add_import('/Script/CoreUObject', 'Package', 0, game_path)
    table_ref = pkg.add_import('/Script/Engine', 'DataTable', package_ref, obj_name)
    entries.append(table_ref)

    # The class default object holds the array, so the table must be CREATED
    # before the CDO is serialised -- that is what the shipped 34 declare. An
    # entry added without this is read before the loader has made the object,
    # which crashes the game on start rather than failing to find the table.
    cdo_index = pkg.exports.index(cdo)
    pkg.add_preload_dependency(cdo_index, table_ref, 'create_before_serialization')

    d = bytearray(cdo['data'])
    body = struct.pack('<i', len(entries)) + struct.pack('<%di' % len(entries),
                                                         *entries)
    old_size = struct.unpack_from('<i', d, at + 16)[0]
    d[at + head:at + head + old_size] = body
    struct.pack_into('<i', d, at + 16, len(body))
    cdo['data'] = bytes(d)
    return len(entries)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--add', metavar='/Game/PATH/TABLE')
    ap.add_argument('--out')
    args = ap.parse_args()

    pkg = load()
    if args.show:
        _cdo, _at, _h, entries = find_array(pkg)
        print('%s registers %d tables:' % (FIELD, len(entries)))
        for e in entries:
            print('   %s' % resolve(pkg, e))
        return
    if args.add:
        n = add_table(pkg, args.add)
        print('registered %s -- %s now holds %d tables' % (args.add, FIELD, n))
        ua, ux = pkg.write()
        back = Package(ua, ux)
        back.load_payloads()
        _c, _a, _h, entries = find_array(back)
        print('re-read: %d entries, last resolves to %r'
              % (len(entries), resolve(back, entries[-1])))
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            base = os.path.join(args.out, 'BP_OMDProtoManager')
            with open(base + '.uasset', 'wb') as fh:
                fh.write(ua)
            with open(base + '.uexp', 'wb') as fh:
                fh.write(ux)
            print('wrote %s.uasset (%d) and .uexp (%d)' % (base, len(ua), len(ux)))
        return
    ap.error('need --show or --add')


if __name__ == '__main__':
    main()
