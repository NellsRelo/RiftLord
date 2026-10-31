"""Offline checks on Supported Mods (riftlord/core/supportedmods.py): detection by content
(a pak's index holding the mod's folder, whatever its name, merged paks too), pak index and
class-package header in a temp Paks folder (present, absent, a class missing,
a class package that does not parse, which nothing reading the kit may trip on), the cache,
the features each mod gates, the build refusal, and the chest and Crystal reading the
detected paks with nothing copied.

    python3 test_supportedmods.py [SCRATCH]
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'tests', 'ui_smoke'))

import fakemods  # noqa: E402
from riftlord.core import supportedmods as sm  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


SCRATCH = sys.argv[1] if len(sys.argv) > 1 else None
ROOT = tempfile.mkdtemp(prefix='supportedmods-', dir=SCRATCH)
#: The Paks folder the caller gave (the gate's stand-in), put back at the end.
GIVEN = os.environ.get(sm.PAKS_ENV)
os.environ[sm.PAKS_ENV] = ROOT


def status():
    return {row['id']: row for row in sm.detect()}


try:
    # 1. the registry: three mods, each file under Paks, each feature's classes named
    check('three supported mods, in order',
          [m.id for m in sm.MODS] == ['OrcsMustUI', 'CrystalDefense', 'TreasureChests'])
    check('files are relative to Paks', all(not os.path.isabs(f) and '\\' not in f
                                            for m in sm.MODS for f in m.files))
    check('every feature names its classes and plan items',
          all(f.classes and f.items for m in sm.MODS for f in m.features))
    check('feature ids', sorted(sm.FEATURES) == ['crystal', 'staminaPotion', 'treasureChest'],
          sorted(sm.FEATURES))
    check('Paks root follows %s' % sm.PAKS_ENV, sm.paks_root() == ROOT)
    check('each mod has its content folder', all(m.folder for m in sm.MODS))

    # 2. nothing installed
    got = status()
    check('an empty Paks folder: every mod Not detected',
          all(r['status'] == sm.MISSING and r['label'] == 'Not detected' for r in got.values()),
          {k: r['status'] for k, r in got.items()})
    check('an empty Paks folder: every feature off', not any(sm.features().values()), sm.features())
    check('an empty Paks folder: Settings lists no mods', sm.state()['mods'] == [],
          sm.state()['mods'])

    # 3. all installed
    for mod in sm.MODS:
        fakemods.write(ROOT, mod.id, 'installed')
    got = status()
    check('every mod Detected', all(r['status'] == sm.INSTALLED and r['label'] == 'Detected'
                                    for r in got.values()),
          {k: r['status'] for k, r in got.items()})
    check('every feature on', all(sm.features().values()), sm.features())
    check('Settings lists all three', [r['id'] for r in sm.state()['mods']] ==
          ['OrcsMustUI', 'CrystalDefense', 'TreasureChests'])
    check('the detected file is where it is expected',
          got['TreasureChests']['files'][0]['full'] == os.path.join(ROOT, 'LogicMods',
                                                                    'TreasureChests.pak')
          and got['TreasureChests']['files'][0]['present'])
    check('holder finds the pak with a class',
          sm.holder('OMD/Content/Mods/CrystalDefense/BP_Crystal.uasset')
          == os.path.join(ROOT, '~mods', 'CrystalDefense_P.pak'))
    for mod in sm.MODS:
        path = sm.file_path(mod.id)
        for feat in mod.features:
            for c in feat.classes:
                check('the stand-in %s package parses and exports it' % c.rsplit('.', 1)[-1],
                      sm._parse_problem(path, c) is None, sm._parse_problem(path, c))

    # 3a. by content: two mods in one merged pak under another name (a subfolder of ~mods),
    # one mod missing; the missing one is not listed, the merged ones read from that pak
    merged = fakemods.merged(ROOT, '~mods/Fear/Merged_P.pak', ['CrystalDefense', 'TreasureChests'])
    fakemods.write(ROOT, 'OrcsMustUI', 'missing')
    got = status()
    check('a merged pak: both its mods Detected',
          got['CrystalDefense']['status'] == sm.INSTALLED
          and got['TreasureChests']['status'] == sm.INSTALLED,
          {k: r['status'] for k, r in got.items()})
    check('a merged pak: their file is the merged pak',
          got['CrystalDefense']['files'][0]['path'] == '~mods/Fear/Merged_P.pak'
          and sm.file_path('TreasureChests') == merged, got['CrystalDefense']['files'])
    check('a merged pak: their features on, the missing mod\'s off', sm.features() == {
        'staminaPotion': False, 'crystal': True, 'treasureChest': True}, sm.features())
    check('a missing mod is not listed', [r['id'] for r in sm.state()['mods']] ==
          ['CrystalDefense', 'TreasureChests'], sm.state()['mods'])
    check('holder finds a class in the merged pak',
          sm.holder('OMD/Content/Mods/CrystalDefense/BP_Crystal.uasset') == merged)
    from riftlord.mapeditor import crystal as _crystal
    check('the Crystal reads the merged pak', _crystal.pak() == merged, _crystal.pak())
    check('a merged build needs nothing', sm.build_problems(
        {'added': [{'kind': 'crystal', 'name': 'BP_Crystal_C'}]}) == [])
    # one mod under another mod's pak name: detected by content, not by the file name
    os.remove(merged)
    other = os.path.join(ROOT, *sm.BY_ID['TreasureChests'].files[0].split('/'))
    fakemods._write(other, fakemods.mod_files('OrcsMustUI', 'installed'))
    got = status()
    check('a pak named as another mod\'s: detected by what it carries',
          got['OrcsMustUI']['status'] == sm.INSTALLED
          and got['OrcsMustUI']['files'][0]['path'] == 'LogicMods/TreasureChests.pak'
          and got['TreasureChests']['status'] == sm.MISSING
          and got['CrystalDefense']['status'] == sm.MISSING,
          {k: (r['status'], [f['path'] for f in r['files']]) for k, r in got.items()})
    os.remove(other)
    for mod in sm.MODS:
        fakemods.write(ROOT, mod.id, 'installed')

    # 3b. the fallback stand-in: a shipped package renamed to the class
    donor_pak = os.path.join(ROOT, 'renamed.pak')
    cls = '/Game/Mods/TreasureChests/BP_TreasureChest.BP_TreasureChest_C'
    ua, ux, _src = fakemods.renamed_package(cls)
    from riftlord.core import paksource
    paksource.write_pak(donor_pak, {sm.package_entry(cls) + '.uasset': ua,
                                    sm.package_entry(cls) + '.uexp': ux})
    check('a shipped package renamed to the class parses and exports it',
          sm._parse_problem(donor_pak, cls) is None, sm._parse_problem(donor_pak, cls))
    os.remove(donor_pak)

    # 3c. class packages that do not parse: incompatible, the reason listed and logged once,
    # nothing that reads the kit breaks
    from riftlord.core import console
    from riftlord.mapeditor import crystal
    from riftlord.mapeditor import kitactors
    warned = []
    keep_warn = console.warn
    console.warn = lambda text, source='': warned.append(text)
    try:
        time.sleep(0.01)
        fakemods.write(ROOT, 'TreasureChests', 'corrupt')
        fakemods.write(ROOT, 'OrcsMustUI', 'corrupt')
        fakemods.write(ROOT, 'CrystalDefense', 'corrupt')
        got = status()
        status()
    finally:
        console.warn = keep_warn
    tc = got['TreasureChests']
    check('a class package that does not parse: Detected (incompatible version)',
          tc['status'] == sm.INCOMPATIBLE, tc['status'])
    check('its reason is listed', tc['missing'] == [
        '/Game/Mods/TreasureChests/BP_TreasureChest.BP_TreasureChest_C '
        '(does not parse: not a UE package)'], tc['missing'])
    check('every feature off', not any(sm.features().values()), sm.features())
    check('each problem logged once', len(warned) == 3, warned)
    try:
        rows = kitactors.catalogue_rows()
        check('the catalogue rows still build',
              {r['name']: r['parts'] for r in rows}.get('BP_TreasureChest_C') == 0
              and {r['name']: r['parts'] for r in rows}.get('BP_Pickup_StaminaPotion_C') == 0)
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        check('the catalogue rows still build', False, exc)
    check('the chest and potion draw as stand-ins (no parts)',
          kitactors.parts('BP_TreasureChest_C') == [] and kitactors.view_parts(
              'BP_Pickup_StaminaPotion_C') == [])
    check('no previews for them', kitactors.thumbnail('BP_TreasureChest_C') is None
          and kitactors.thumbnail('BP_Pickup_StaminaPotion_C') is None)
    check('the Crystal reads no pak and keeps its defaults',
          crystal.pak() is None and crystal.class_defaults()['HealthPerRiftPoint'] == 20.0)
    for mod in sm.MODS:
        fakemods.write(ROOT, mod.id, 'installed')

    # 4. a class missing: Detected (incompatible version), its feature off, the class named
    time.sleep(0.01)
    fakemods.write(ROOT, 'TreasureChests', 'incompatible')
    got = status()
    tc = got['TreasureChests']
    check('a pak without the class: Detected (incompatible version)',
          tc['status'] == sm.INCOMPATIBLE and tc['label'] == 'Detected (incompatible version)',
          tc['status'])
    check('the missing class is listed',
          tc['missing'] == ['/Game/Mods/TreasureChests/BP_TreasureChest.BP_TreasureChest_C'],
          tc['missing'])
    check('its feature is off, the others on', sm.features() == {
        'staminaPotion': True, 'crystal': True, 'treasureChest': False}, sm.features())

    # 5. the cache follows size and time: a changed file is read again
    fakemods.write(ROOT, 'TreasureChests', 'installed')
    check('a rewritten pak is read again', sm.available('treasureChest'))
    fakemods.write(ROOT, 'CrystalDefense', 'missing')
    check('a removed pak: Not detected',status()['CrystalDefense']['status'] == sm.MISSING)

    # 6. plan items and the build refusal
    plan = {'added': [{'kind': 'kit', 'name': 'BP_TreasureChest_C'},
                      {'kind': 'kit', 'name': 'BP_TreasureChest_C'},
                      {'kind': 'kit', 'name': 'BP_Pickup_StaminaPotion_C'},
                      {'kind': 'crystal', 'name': 'BP_Crystal_C'},
                      {'kind': 'kit', 'name': 'BP_Pickup_Coin_C'}]}
    check('plan features counted', sm.plan_features(plan) == {
        'treasureChest': 2, 'staminaPotion': 1, 'crystal': 1}, sm.plan_features(plan))
    check('required mods in order', sm.required_mods(plan) ==
          ['OrcsMustUI', 'Crystal Defense', 'Treasure Chests'], sm.required_mods(plan))
    bad = sm.build_problems(plan)
    check('a missing mod refuses the build in one line naming it',
          bad == ['Crystal Defense is not installed (Crystal x1 needs ~mods/CrystalDefense_P.pak)'],
          bad)
    fakemods.write(ROOT, 'TreasureChests', 'incompatible')
    bad = sm.build_problems(plan)
    check('an incompatible mod refuses too', len(bad) == 2 and bad[1].startswith(
        'Treasure Chests is an incompatible version (Treasure Chest x2; lacks '), bad)
    check('a plan without mod pieces needs nothing',
          sm.build_problems({'added': [{'kind': 'kit', 'name': 'BP_Pickup_Coin_C'}]}) == [])
    report = {'requires': ['x']}
    sm.note_build(plan, report)
    check('the report names the required mods', report['requires'] == sorted(
        ['x', 'OrcsMustUI', 'Crystal Defense', 'Treasure Chests']), report['requires'])

    # 7. the chest, potion and Crystal read the detected paks; nothing is copied
    from riftlord.mapeditor import crystal
    from riftlord.mapeditor import kitactors
    check('no kit copy left in kitactors', not hasattr(kitactors, 'add_kits')
          and not hasattr(kitactors, 'KIT_FILES'))
    check('the chest kit is the detected TreasureChests pak',
          kitactors.KITS['TreasureChests']['pak'] == os.path.join(ROOT, 'LogicMods',
                                                                  'TreasureChests.pak'))
    check('the potion kit is the detected OrcsMustUI pak',
          kitactors.KITS['OrcsMustUI']['pak'] == os.path.join(ROOT, 'LogicMods',
                                                              'OrcsMustUI.pak'))
    feats = {cls: sm.feature_of({'kind': 'kit', 'name': cls}) for cls in kitactors.CLASSES}
    check('New Object rows: chest and potion gated, shipped pickups not',
          feats['BP_TreasureChest_C'] == 'treasureChest'
          and feats['BP_Pickup_StaminaPotion_C'] == 'staminaPotion'
          and not any(v for k, v in feats.items()
                      if k not in ('BP_TreasureChest_C', 'BP_Pickup_StaminaPotion_C')), feats)
    check('the Crystal has no pak while its mod is missing (no dist fallback)',
          crystal.pak() is None)
    check('the Crystal build refusal leaves the missing mod to Supported Mods',
          not any('installed' in p for p in crystal.problems(
              {'added': [{'kind': 'crystal', 'name': 'BP_Crystal_C', 'matrix': [0] * 16}]})))
    fakemods.write(ROOT, 'CrystalDefense', 'installed')
    check('the Crystal reads the detected pak',
          crystal.pak() == os.path.join(ROOT, '~mods', 'CrystalDefense_P.pak'))
finally:
    os.environ.pop(sm.PAKS_ENV, None)
    if GIVEN:
        os.environ[sm.PAKS_ENV] = GIVEN
    shutil.rmtree(ROOT, ignore_errors=True)

# 8. this machine, or the caller's Paks folder (reported, not checked: it depends on what is
# installed)
for row in sm.detect():
    print('     this machine: %-16s %s' % (row['name'], row['label']))

for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
failed = [c for c in checks if not c[1]]
print('%d checks, %d failed' % (len(checks), len(failed)))
sys.exit(1 if failed else 0)
