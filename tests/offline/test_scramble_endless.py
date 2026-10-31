"""Scramble and Endless: Endless is a difficulty (EGameDifficulty::Endless), so any mission,
one with Endless on included, joins Scramble; its entry plays at a tier difficulty, never
Endless, and references the mission's main row (the round ends). Endless tiers are not
offered or joined.

    python3 tests/offline/test_scramble_endless.py [BUILT_MISSION]

BUILT_MISSION is a built mission (a pak, or a loose build folder; only read); default: the
pinned baseline build of the test mission 900_Fixture_Edit_P (buildcompare.baseline_pak).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import buildcompare  # noqa: E402
from riftlord.missions import missionscramble as M  # noqa: E402

BUILT = buildcompare.baseline_pak('900_Fixture_Edit_P')
checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


TIERS = [('Tier_01-Start', 'WarMage'), ('Endless_Hard', 'RiftLord'), ('Tier_02', 'WarMage')]

got = M.clean({'on': True, 'tiers': ['Endless_Hard', 'Tier_02']})
check('an Endless tier is not a choice', got['tiers'] == ['Tier_02'], got)
check('a record saved with an Endless tier joins none',
      M.clean({'on': True, 'tier': 'Endless_Hard'}) is None)
got = M.clean({'on': True, 'tiers': ['Endless_Hard', 'Tier_02'],
               'rows': {'Endless_Hard': {'weight': 50}, 'Tier_02': {'weight': 50}}})
check('rows for an Endless tier are dropped', list(got['rows']) == ['Tier_02'], got)
got = M.clean({'on': True, 'tier': 'All', 'rows': {'Endless_Hard': {'weight': 50},
                                                    'Tier_02': {'weight': 50}}})
check('rows for an Endless tier are dropped from a record saved with All',
      list(got['rows']) == ['Tier_02'], got)
entries, _unmatched = M.joined({'on': True, 'tier': 'All'}, TIERS)
check('a record saved with All never joins an Endless tier',
      [e[0] for e in entries] == ['Tier_01-Start', 'Tier_02'], entries)

real = M.tier_defaults
M.tier_defaults = lambda t: TIERS
try:
    shown = [t['name'] for t in M.view()['tiers']]
finally:
    M.tier_defaults = real
check('the dialog is never offered an Endless tier', 'Endless_Hard' not in shown and shown, shown)

path = sys.argv[1] if len(sys.argv) > 1 else BUILT
check('a built mission to check (the test mission\'s pinned build: buildcompare --pin)',
      path and os.path.exists(path), path)
if path and os.path.exists(path):
    from riftlord.core import paksource
    from riftlord.missions import scrambletable
    files = paksource.entries(path)
    rows = {}
    for key in files:
        if key.endswith('_MissionProtos.uasset'):
            rows = scrambletable.read_table(files[key], files[key[:-len('.uasset')] + '.uexp'])
    main = next((r for r in rows if r.endswith('_Mission01')), None)
    twin = next((r for r in rows if r.endswith('_Endless01')), None)
    check('the built mission has Endless on (an Endless twin)', main and twin, sorted(rows))
    if main:
        mod = main.rsplit('_', 1)[0]
        guid = bytes.fromhex(str(rows[main]['Guid'])[:32])
        scramble = {'on': True, 'tiers': [t['name'] for t in M.tiers()]}
        built, report = M.extension(mod, guid, scramble)
        entries = [tuple(e) for e in report['scramble']]
        check('the Endless mission joins Scramble', built and entries, entries)
        check('no entry plays at Endless difficulty', all(d in dict(M.DIFFICULTIES)
                                                         for _t, d, _w in entries), entries)
        files2 = dict(files, **built)
        check('the entries read back naming the main row',
              not M.problems(files2, mod, guid, entries), M.problems(files2, mod, guid, entries))
        check('the main row is a normal round (it has default characters)',
              rows[main].get('DefaultCharacters'), rows[main].get('DefaultCharacters'))
        report = {'scramble': entries, 'mission_row': main, 'mission_guid': guid.hex()}
        check('the build check passes it', not M.endless_problems(files2, report))
        report['scramble'] = [('Tier_02', 'Endless', 10.0)]
        check('an entry at Endless difficulty is refused', M.endless_problems(files2, report))

for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else '  (%s)' % (detail,)))
print('%d checks, %d failed' % (len(checks), sum(not ok for _n, ok, _d in checks)))
raise SystemExit(0 if all(ok for _n, ok, _d in checks) else 1)
