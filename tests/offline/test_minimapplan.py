"""Offline checks on the plan's minimap block (riftlord/build/minimap/minimapredraw.py):
Map Settings' Cut at Doors switch is kept as `cutAtDoors: true` on a Regenerate block,
dropped everywhere else, and survives a save and a load (leveldata.save_plan keeps only
what minimapredraw.clean keeps); without it a mission draws all reachable floor.

    python3 test_minimapplan.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PLANS = tempfile.mkdtemp(prefix='rl-minimapplan-')
os.environ['RIFTLORD_PLANS'] = PLANS

from riftlord.build.minimap import minimapredraw  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


# 1. the block as saved
clean = minimapredraw.clean
check('Regenerate keeps Cut at Doors', clean({'mode': 'regenerate', 'cutAtDoors': True})
      == {'mode': 'regenerate', 'cutAtDoors': True})
check('Cut at Doors off is left out', clean({'mode': 'regenerate', 'cutAtDoors': False})
      == {'mode': 'regenerate'})
check('only true counts', clean({'mode': 'regenerate', 'cutAtDoors': 'yes'})
      == {'mode': 'regenerate'})
check('Cut mode drops it', clean({'mode': 'cut', 'cutAtDoors': True}) == {'mode': 'cut'})
check('Source Level drops it', clean({'mode': 'source', 'cutAtDoors': True}) == {})
check('an area is kept beside it', clean({'mode': 'regenerate', 'cutAtDoors': True,
                                          'area': [3, 4, 1, 2]})
      == {'mode': 'regenerate', 'area': [1.0, 2.0, 3.0, 4.0], 'cutAtDoors': True})

# 2. what a mission draws
cut = minimapredraw.cut_at_doors
check('by default all reachable floor', not cut({'minimap': {'mode': 'regenerate'}}))
check('Cut at Doors on stops at the doors',
      cut({'minimap': {'mode': 'regenerate', 'cutAtDoors': True}}))
check('no minimap block: all reachable floor', not cut({}) and not cut(None))

# 3. a save and a load
name = 'minimapplan_test'
try:
    for block, want in (({'mode': 'regenerate', 'cutAtDoors': True},
                         {'mode': 'regenerate', 'cutAtDoors': True}),
                        ({'mode': 'regenerate', 'cutAtDoors': False}, {'mode': 'regenerate'})):
        leveldata.save_plan(name, {'level': 'Scenario_01', 'minimap': block})
        with open(leveldata.plan_path(name)) as fh:
            saved = json.load(fh).get('minimap')
        check('saved %s' % json.dumps(block), saved == want, saved)
        loaded = (leveldata.load_plan(name) or {}).get('minimap')
        check('loaded %s' % json.dumps(block), loaded == want, loaded)
finally:
    try:
        os.remove(leveldata.plan_path(name))
        os.rmdir(PLANS)
    except OSError:
        pass

bad = [c for c in checks if not c[1]]
for name_, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name_, '' if ok else ' (%s)' % (detail,)))
print('%d checks, %d failed' % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
