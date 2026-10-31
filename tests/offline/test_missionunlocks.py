"""Hero and item unlocks (missionunlocks.py): the elements written read back in the shipped
layout (byte for byte against Scenario_18_WS's own), the choices leave out entitlement-gated,
DLC, development and internal rows, and a mod's item is offered and written while its pak is
installed, warned about and left out once it is not.

    python3 tests/offline/test_missionunlocks.py

Reads the game's shipped tables; the mod is a stand-in written into a scratch Paks folder
(tests/ui_smoke/fakeunlocks.py), never the game's ~mods.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests', 'ui_smoke'))

import fakeunlocks  # noqa: E402
from riftlord.build import missionproto, modtable  # noqa: E402
from riftlord.missions import missionunlocks as U  # noqa: E402
from riftlord.missions import modminions  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def mission_files(mod='UnlockTest', endless=True):
    """{pak entry: bytes} of a mission table as makemod writes one: Scenario_01 cloned with
    its four reward arrays emptied, and its Endless twin."""
    t = missionproto.read()
    main = t.clone('Scenario_01', '%s_Mission01' % mod)
    rows = [main]
    if endless:
        rows.append(t.clone('Scenario_01', '%s_Endless01' % mod))
    for row in rows:
        for f in ('MissionUnlocks', 'ItemUnlocks', 'SlotUnlocks', 'CharacterUnlocks'):
            t.clear_array(row, f)
    t.rows = rows
    path = '/Game/Mods/%s/Tables/%s_MissionProtos' % (mod, mod)
    modtable.rename_package(t.pkg, missionproto.TABLE, 'MissionProtos', path,
                            path.rsplit('/', 1)[-1])
    ua, ux = t.write()
    stem = 'OMD/Content' + path[len('/Game'):]
    return {stem + '.uasset': ua, stem + '.uexp': ux}


# -- the shipped layout -------------------------------------------------------------------
stock = missionproto.read()
s18 = stock.row('Scenario_18_WS')
for field, template, hidden in ((U.HERO_FIELD, U.HERO_TEMPLATE, False),
                                (U.ITEM_FIELD, U.ITEM_TEMPLATE, True)):
    _n, _t, shipped = stock.struct_array(stock.field(s18, field))
    made = []
    for e in shipped:
        at = U._walk(e, stock.names)
        g = at['Guid'][1]
        made.append(U.element(stock, missionproto.read(), template, e[g:g + 16].hex(), hidden))
    check('%s elements are Scenario_18_WS\'s, byte for byte' % field, made == shipped,
          [len(e) for e in made])

known = U.index()
by_row = {r['row']: r for r in known.values()}
files = mission_files()
unlocks = {'heroes': [{'guid': by_row['Vorwick']['guid']}],
           'items': [{'guid': by_row['Haymaker']['guid']}, {'guid': by_row['SwingingMace']['guid']}]}
built, report = U.apply(files, unlocks)
got = U.granted(built)
check('the hero is in CharacterUnlocks', got[U.HERO_FIELD] == [(by_row['Vorwick']['guid'], False)],
      got[U.HERO_FIELD])
check('the items, then the hero\'s weapon hidden, are in ItemUnlocks',
      got[U.ITEM_FIELD] == [(by_row['Haymaker']['guid'], False, False),
                            (by_row['SwingingMace']['guid'], False, False),
                            (by_row['ElvenShortswords']['guid'], False, True)], got[U.ITEM_FIELD])
check('elements are the shipped sizes', got['sizes'] == {U.ITEM_FIELD: [182], U.HERO_FIELD: [156]},
      got['sizes'])
check('the Endless twin grants nothing', not any(got['twin'].values()), got['twin'])
failed = [n for n, ok, _d in U.checks(built, report) if not ok]
check('the build checks pass', not failed, failed)
again, _r = U.apply(built, {'items': [{'guid': by_row['Haymaker']['guid']}]})
check('applied again, the arrays are replaced, not added to',
      U.granted(again)[U.ITEM_FIELD] == [(by_row['Haymaker']['guid'], False, False)]
      and not U.granted(again)[U.HERO_FIELD], U.granted(again))
none, _r = U.apply(built, None)
check('no unlocks empties both arrays',
      not U.granted(none)[U.ITEM_FIELD] and not U.granted(none)[U.HERO_FIELD])
variant, rep = U.apply(files, unlocks, endless_only=True)
check('a mission placed in Endless grants nothing', not U.granted(variant)[U.ITEM_FIELD]
      and not U.granted(variant)[U.HERO_FIELD] and rep['unlocks'] == [], rep['unlocks'])

# -- what is left out ---------------------------------------------------------------------
got = U.scan()
offered = {r['guid'] for r in got['heroes'] + got['items']}
ent = missionproto.read(U.ENTITLEMENTS)
gated = U.gated_guids(ent)
check('no DLC item (an entitlement\'s ItemEntitlements) is offered', not (gated & offered),
      sorted(gated & offered))
for path in ('/Game/Data/TrapProtos', '/Game/Data/WeaponProtos', '/Game/Data/TrinketProtos'):
    t = missionproto.read(path)
    bad = [r['name'] for r in t.rows
           if (U._flag(t, r, 'bRequiresEntitlement') or U._flag(t, r, 'bDevelopmentOnly')
               or modminions._enum(t, t.field(r, 'AquiredBy')) == 'Internal')
           and U._guid(t, r) in offered]
    check('%s: no entitlement, development or internal row is offered' % path.rsplit('/', 1)[-1],
          not bad, bad)
check('every offered row has a name', all((r['name'] or '').strip()
                                          for r in got['heroes'] + got['items']))
for row, why in (('MoltenGold', 'entitlement'), ('Bladestaff_Overlord', 'entitlement'),
                 ('RiftLordUnlock', 'internal'), ('Cauldron_ENV', 'internal')):
    check('%s is left out (%s)' % (row, why), by_row[row].get('why') == why, by_row[row].get('why'))
check('the free heroes are offered, the DLC-granted ones too (a grant is not a gate)',
      {'Cygnus', 'Vorwick', 'Gabriella', 'Max'} <= {r['row'] for r in got['heroes']})
try:
    U.clean({'items': [by_row['MoltenGold']['guid']]})
    check('a gated Guid is refused when saved', False)
except SystemExit as exc:
    check('a gated Guid is refused when saved', 'entitlement' in str(exc), str(exc))
_files, rep = U.apply(files, {'items': [{'guid': by_row['MoltenGold']['guid']}]})
check('a gated Guid in a record is never written', rep['unlocks'] == []
      and rep['unlocks_warnings'], rep)

# -- a mod's items ------------------------------------------------------------------------
scratch = tempfile.mkdtemp(prefix='unlocks-', dir=os.environ.get('RIFTLORD_TEST_TMP')
                           or ('/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None))
real_roots, real_cache = modminions.ROOTS, U._cache_file
modminions.ROOTS = [os.path.join(scratch, '~mods')]
U._cache_file = lambda: os.path.join(scratch, 'choices.json')
try:
    pak = fakeunlocks.write(scratch)
    got = U.scan()
    mod_items = {r['row']: r for r in got['items'] if r['mod'] == fakeunlocks.MOD}
    left = {r['row']: r['why'] for r in got['excluded'] if r['mod'] == fakeunlocks.MOD}
    check('the mod\'s free trap is offered, with its mod', fakeunlocks.OPEN[0] in mod_items
          and mod_items[fakeunlocks.OPEN[0]]['label'] == 'Unlock Mod', sorted(mod_items))
    check('the mod\'s entitled trap is left out',
          left.get(fakeunlocks.ENTITLED[0]) == 'entitlement', left)
    check('the trap the mod\'s entitlement table lists is left out',
          left.get(fakeunlocks.LISTED[0]) == 'dlc', left)
    mod_guid = fakeunlocks.OPEN[3].hex()
    stored = U.clean({'items': [mod_guid, by_row['Haymaker']['guid']]})
    check('the mod trap saves with its mod', stored['items'][0]['mod'] == fakeunlocks.MOD, stored)
    built, report = U.apply(files, stored)
    check('the installed mod trap is written', [g for _f, g, _h in report['unlocks']]
          == [mod_guid, by_row['Haymaker']['guid']], report['unlocks'])
    check('it reads back', U.granted(built)[U.ITEM_FIELD][0][0] == mod_guid)
    os.remove(pak)
    got = U.scan()
    check('the list is read again once the pak is gone',
          not any(r['mod'] == fakeunlocks.MOD for r in got['items']))
    _built, report = U.apply(files, stored)
    check('without the mod the build warns and leaves it out',
          [g for _f, g, _h in report['unlocks']] == [by_row['Haymaker']['guid']]
          and any('not installed' in w and fakeunlocks.MOD in w
                  for w in report['unlocks_warnings']), report)
    kept = U.clean({'items': [mod_guid]}, previous=stored)
    check('a saved choice whose mod is gone is kept, named', kept and
          kept['items'][0]['name'] == fakeunlocks.OPEN[2], kept)
finally:
    modminions.ROOTS, U._cache_file = real_roots, real_cache
    U._SCAN.clear()
    shutil.rmtree(scratch, ignore_errors=True)

for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else '  (%s)' % (detail,)))
print('%d checks, %d failed' % (len(checks), sum(not ok for _n, ok, _d in checks)))
raise SystemExit(0 if all(ok for _n, ok, _d in checks) else 1)
