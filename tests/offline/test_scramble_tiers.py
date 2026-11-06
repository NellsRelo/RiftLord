"""Scramble tiers a mod adds: discovery (a registered table of bucket rows, a replacement
table switched on or off), listing with each tier's mod, a mission joining a mod tier
(its Extends row named and stored exactly as the tier row), a tier typed by name that no
mod defines (dormant rows in both FName forms), no tier mod in `requires`, and the build
gates. The record's `tiers`: none joined until one is named, several in order, numbers
as Tier_NN, no All; a record saved with `tier` (one, or All) read as before.

    python3 tests/offline/test_scramble_tiers.py

Fake paks are written to a temporary folder; nothing of the game install is written.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'ui_smoke'))

from riftlord.core import paksource  # noqa: E402
from riftlord.missions import missionscramble as M  # noqa: E402
from riftlord.missions import scrambletable  # noqa: E402
from faketiers import bucket_files, guid_bytes, registered_path  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def row_guids(files, mod):
    """{row name: Guid hex} of a built extension."""
    key = M._entry_key(mod)
    return {n: str(v.get('Guid'))[:32] for n, v in scrambletable.read_table(
        files[key], files[key[:-len('.uasset')] + '.uexp']).items()}


ROOT = tempfile.mkdtemp(prefix='rl-scramble-tiers-')
try:
    shipped = M._table_rows(scrambletable.bucket_table())
    shipped_names = [r['name'] for r in shipped]
    shipped_guids = {r['guid'] for r in shipped}
    TIER_MOD_GUID = guid_bytes(21)
    # a tier mod's registered table: Tier_21 as the editor cooks it ('Tier', number 22)
    paksource.write_pak(os.path.join(ROOT, 'TierMod_P.pak'), bucket_files(
        [('Tier', 22, TIER_MOD_GUID)], registered_path('TierMod')))
    # a replacement table switched off (Campaign Scramble's route): the shipped tiers + Tier_06
    os.makedirs(os.path.join(ROOT, 'sub'))
    remix = os.path.join(ROOT, 'sub', '501-FakeRemix_P.pak.disabled')
    paksource.write_pak(remix, bucket_files([('Tier_06', 0, guid_bytes(6))], keep_shipped=True))
    # an Extends-only pak naming Tier_22: an extension, never a tier
    paksource.write_pak(os.path.join(ROOT, 'OtherMod_P.pak'), bucket_files(
        [('Tier', 23, guid_bytes(22))], M.extension_path('OtherMod')))

    # 1. discovery
    found = M.tiers(ROOT)
    by = {t['name']: t for t in found}
    check('the shipped tiers come first, live, with no mod',
          [t['name'] for t in found[:len(shipped_names)]] == shipped_names and
          all(by[n]['live'] and by[n]['mod'] is None for n in shipped_names),
          [t['name'] for t in found])
    check('a registered table adds Tier_21, live, from TierMod, stored as (Tier, 22)',
          by.get('Tier_21', {}).get('mod') == 'TierMod' and by['Tier_21']['live'] and
          by['Tier_21']['forms'] == [('Tier', 22)] and by['Tier_21']['guid'] ==
          TIER_MOD_GUID.hex(), by.get('Tier_21'))
    check('a switched-off replacement table lists Tier_06 from FakeRemix, not live',
          by.get('Tier_06', {}).get('mod') == 'FakeRemix' and not by['Tier_06']['live'] and
          not by['Tier_06']['enabled'], by.get('Tier_06'))
    check('an Extends row is not a tier', 'Tier_22' not in by, sorted(by))
    first = M.pak_tiers(os.path.join(ROOT, 'TierMod_P.pak'))
    check('a pak is read once while its size and time stay',
          M.pak_tiers(os.path.join(ROOT, 'TierMod_P.pak')) is first)
    before = M._PAK_TIERS[remix]
    st = os.stat(remix)
    os.utime(remix, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
    again = M.pak_tiers(remix)
    check('a pak whose time changed is read again', M._PAK_TIERS[remix] is not before and
          again and again[0]['rows'][-1]['name'] == 'Tier_06', again)
    shown = {t['name']: t for t in M.view(ROOT)['tiers']}
    check('the dialog is offered the mod tiers with their mod',
          shown.get('Tier_21', {}).get('mod') == 'TierMod' and
          shown.get('Tier_06', {}).get('mod') == 'FakeRemix', shown.get('Tier_21'))

    # 2. joining a mod tier
    mission = guid_bytes(1)
    files, report = M.extension('TierProbe', mission, {'tiers': ['Tier_21']}, ROOT)
    entries = [tuple(e) for e in report['scramble']]
    check('the mission joins Tier_21', [e[0] for e in entries] == ['Tier_21'], entries)
    key = M._entry_key('TierProbe')
    check('its Extends row is stored as the tier row is: (Tier, 22)',
          M.stored_forms(files[key], files[key[:-len('.uasset')] + '.uexp']) == [('Tier', 22)])
    check('its Extends row carries the tier row\'s Guid, no shipped tier\'s',
          row_guids(files, 'TierProbe') == {'Tier_21': TIER_MOD_GUID.hex()},
          row_guids(files, 'TierProbe'))
    check('the tier report names TierMod', report['scramble_tiers']['Tier_21']['mod'] ==
          'TierMod', report['scramble_tiers'])
    check('it reads back', not M.problems(files, 'TierProbe', mission, entries,
                                          report['scramble_tiers']),
          M.problems(files, 'TierProbe', mission, entries, report['scramble_tiers']))
    build = dict(report, mission_row='TierProbe_Mission01', mission_guid=mission.hex())
    check('the build check passes it', all(ok for _n, ok, _d in M.checks(files, build)),
          M.checks(files, build))
    check('the extension adds nothing to requires',
          not {'requires', 'scramble_requires'} & set(report), sorted(report))
    bad = dict(files)
    bad[key[:-len('.uasset')] + '.uexp'] = bad[key[:-len('.uasset')] + '.uexp'].replace(
        TIER_MOD_GUID, bytes.fromhex(shipped[0]['guid']))
    check('the build check refuses an Extends row carrying a shipped tier\'s Guid',
          M.problems(bad, 'TierProbe', mission, entries, report['scramble_tiers']))
    check('the build check refuses a pak that registers tier rows of its own',
          M.problems(dict(files, **bucket_files([('Tier_07', 0, guid_bytes(7))],
                                                '/Game/Mods/TierProbe/Tables/TierProbe_X')),
                     'TierProbe', mission, entries, report['scramble_tiers']))

    # 3. a tier typed by name that no mod defines: dormant rows in both FName forms
    files, report = M.extension('TierProbe', mission, {'tiers': ['30']}, ROOT)
    entries = [tuple(e) for e in report['scramble']]
    check('a tier typed as a number is joined as Tier_30', [e[0] for e in entries] ==
          ['Tier_30'], entries)
    check('its rows are stored unsplit and split: (Tier_30, 0), (Tier, 31)',
          M.stored_forms(files[key], files[key[:-len('.uasset')] + '.uexp']) ==
          [('Tier_30', 0), ('Tier', 31)])
    check('its rows carry a Guid of their own, no shipped tier\'s',
          set(row_guids(files, 'TierProbe').values()).isdisjoint(shipped_guids) and
          row_guids(files, 'TierProbe')['Tier_30'] ==
          M.row_guid('TierProbe', 'Tier_30', None).hex(), row_guids(files, 'TierProbe'))
    check('it reads back', not M.problems(files, 'TierProbe', mission, entries,
                                          report['scramble_tiers']))
    lines = [line for warn, line in M.describe(report) if warn]
    check('the build console says it waits for a tier mod',
          any('Tier_30 waits for a tier mod' in line for line in lines), lines)
    check('a zero-padded name has one form', M.fname_forms('Tier_06') == [('Tier_06', 0)])

    # 4. several tiers: exactly those, in order, each at its own difficulty and weight
    first = shipped_names[0]
    several = {'tiers': ['Tier_21', first, 'Tier_30'],
               'rows': {first: {'difficulty': 'Apprentice', 'weight': 500},
                        'Tier_30': {'weight': 7}}}
    files, report = M.extension('TierProbe', mission, several, ROOT)
    entries = [tuple(e) for e in report['scramble']]
    check('several tiers are joined, in order, none other',
          [e[0] for e in entries] == ['Tier_21', first, 'Tier_30'], entries)
    check('each joined tier has its own difficulty and weight',
          entries[1][1:] == ('Apprentice', 500.0) and entries[2][2] == 7.0 and
          entries[0][2] == M.DEFAULT_WEIGHT, entries)
    check('they read back', not M.problems(files, 'TierProbe', mission, entries,
                                           report['scramble_tiers']),
          M.problems(files, 'TierProbe', mission, entries, report['scramble_tiers']))

    # 5. added to Scramble with no tier named: joins none
    files, report = M.extension('TierProbe', mission, {'on': True, 'tiers': []}, ROOT)
    check('no tier named joins none and writes no extension',
          not files and report['scramble'] == [], report['scramble'])
    check('it stays added, with no tier', M.clean({'on': True, 'tiers': ['', ' ']}) ==
          {'tiers': [], 'rows': {}} and M.value({'tiers': []}) ==
          {'on': True, 'tiers': [], 'rows': {}}, M.clean({'on': True, 'tiers': ['']}))
    check('a mission never added has no tiers', M.value(None) ==
          {'on': False, 'tiers': [], 'rows': {}}, M.value(None))

    # 6. the record's tiers as a page sends them
    got = M.clean({'on': True, 'tiers': ['7', ' Tier_21 ', 'Tier_21', 'All', 'Endless_X',
                                         'Some  Mod Tier'],
                   'rows': {'Tier_07': {'weight': 3}, 'Tier_02': {'weight': 9}}})
    check('numbers are Tier_NN, names collapsed and kept once, no All or Endless tier',
          got['tiers'] == ['Tier_07', 'Tier_21', 'Some Mod Tier'], got)
    check('rows are kept only for joined tiers', got['rows'] == {'Tier_07': {'weight': 3.0}}, got)

    # 7. records saved before `tiers`: one tier, or All, read as before
    got = M.clean({'on': True, 'tier': 'Tier_21', 'rows': {'Tier_21': {'weight': 5},
                                                          'Tier_02': {'weight': 9}},
                   'extra': {'Tier_21': 'TierMod'}})
    check('a record saved with one tier joins that tier',
          got == {'tiers': ['Tier_21'], 'rows': {'Tier_21': {'weight': 5.0}}}, got)
    files, report = M.extension('TierProbe', mission, {'tier': 'All',
                                                      'extra': {'Tier_30': ''}}, ROOT)
    names = [e[0] for e in report['scramble']]
    check('a record saved with All joins the shipped tiers, the mod tiers and its typed tier',
          names[:len(shipped_names)] == shipped_names and
          {'Tier_06', 'Tier_21', 'Tier_30'} <= set(names), names)
    lines = [line for warn, line in M.describe(report) if warn]
    check('the build console says Tier_06 waits for FakeRemix, switched off',
          any('Tier_06 waits for FakeRemix, which is switched off' in line for line in lines),
          lines)
    got = M.clean({'on': True, 'tier': 'All', 'extra': {'Tier_30': '', 'Endless_X': '',
                                                        'All': ''}})
    check('a record saved with All keeps All and its extra, without All or Endless tiers',
          got['tier'] == 'All' and got['extra'] == {'Tier_30': ''}, got)
    check('the dialog is given a record saved with All as All',
          M.value({'tier': 'All'})['tier'] == 'All' and M.value({'tier': 'All'})['on'],
          M.value({'tier': 'All'}))
    check('a record saved with no tier at all is All, as before',
          M.clean({'on': True}).get('tier') == 'All', M.clean({'on': True}))
    check('a tier recorded from a mod no longer installed is still listed, with its mod',
          {t['name']: t['mod'] for t in M.tiers(ROOT, {'Tier_40': 'GoneMod'})}.get('Tier_40')
          == 'GoneMod')
    check('the dialog is offered no All choice', 'all' not in M.view(ROOT), sorted(M.view(ROOT)))

    # 8. the replacement switched on: its tiers are live; a shipped tier it lacks waits
    os.rename(remix, remix[:-len('.disabled')])
    by = {t['name']: t for t in M.tiers(ROOT)}
    check('switched on, the replacement table is the live one',
          M.live_source(ROOT) == remix[:-len('.disabled')], M.live_source(ROOT))
    check('Tier_06 is live, from FakeRemix', by['Tier_06']['live'] and
          by['Tier_06']['mod'] == 'FakeRemix', by['Tier_06'])

    # 9. a registered row named like a live tier: ProtoRegistry would match two rows
    clash = os.path.join(ROOT, 'clash')
    os.makedirs(clash)
    paksource.write_pak(os.path.join(clash, 'ClashMod_P.pak'), bucket_files(
        [('Tier_02', 0, guid_bytes(2))], registered_path('ClashMod')))
    _files, report = M.extension('TierProbe', mission, {'tiers': ['Tier_02']}, clash)
    lines = [line for warn, line in M.describe(report) if warn]
    check('the build console names the clash', any('Tier_02 is also a tier of ClashMod' in line
                                                    for line in lines), lines)
finally:
    shutil.rmtree(ROOT, ignore_errors=True)

for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else '  (%s)' % (detail,)))
print('%d checks, %d failed' % (len(checks), sum(not ok for _n, ok, _d in checks)))
raise SystemExit(0 if all(ok for _n, ok, _d in checks) else 1)
