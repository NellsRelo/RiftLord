"""Offline checks on mission scripts (riftlord/missions/missionscript.py): the plan item survives
a save, settings replace the script's defaults, `put` and `remove` edit a record, and a script
compiles into a Blueprint that imports what it names.

    python3 test_missionscript.py
"""
import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.missions import missionscript as M  # noqa: E402
from riftlord.missions import records  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
SOURCE = '''"""A test script."""

Shake = Blueprint("/Game/Proto/FX/CameraShake/CameraShakeGeneric.CameraShakeGeneric_C")

Seconds: float = 3.0               # a timing
Loud: bool = False
Label: str = "plain"
Count: int = 2
Mode: Replicated[int]


@override
def ReceiveTick(DeltaSeconds: float):
    if self.Loud and self.Count > 0:
        Waited: float = 0.0
        PlaySound2D(self, Asset("SoundCue'/Game/Traps/FallingBoomBarrel/Audio/FallingBB_Explosion_SC.FallingBB_Explosion_SC'"), self.Seconds, 1.0, 0.0, None, None, False)
    if IsServer(self):
        self.Mode = 1
'''
ITEM = {'name': 'TestIntro', 'kind': 'script', 'matrix': IDENT, 'source': SOURCE,
        'settings': {'Seconds': 4.5, 'Loud': True}, 'actor': {'replicates': True, 'tick': 0.1}}
PLAN = {'level': 'Scenario_02', 'version': 2, 'look': {}, 'world': {}, 'rifts': [],
        'doors': [], 'points': [], 'playerStarts': [], 'wiring': [], 'removed': [],
        'waves': [], 'edits': [], 'spawners': [],
        'added': [{'name': 'BP_Crystal_C', 'kind': 'crystal',
                   'matrix': IDENT[:12] + [100.0, 200.0, 30.0, 1.0], 'crystal': {}},
                  copy.deepcopy(ITEM)]}

# 1. what a plan item may hold
for bad, why in (('Intro_1', 'an underscore'), ('1Intro', 'a leading digit'), ('', 'empty')):
    try:
        M.clean_piece(dict(ITEM, name=bad), IDENT)
        check('a script named %r is refused (%s)' % (bad, why), False)
    except ValueError:
        check('a script named %r is refused (%s)' % (bad, why), True)
for field, value in (('source', ''), ('settings', {'Seconds': {'a': 1}}),
                     ('actor', {'colour': 'red'})):
    try:
        M.clean_piece(dict(ITEM, **{field: value}), IDENT)
        check('a script with bad %s is refused' % field, False)
    except ValueError:
        check('a script with bad %s is refused' % field, True)

# 2. the save keeps it whole, and a saved plan saves unchanged
_w, saved = records.validate_plan({'plan': copy.deepcopy(PLAN)})
got = M.pieces(saved)
check('save keeps the script', len(got) == 1 and got[0]['source'] == SOURCE
      and got[0]['settings'] == ITEM['settings'] and got[0]['actor'] == ITEM['actor'], got)
_w, again = records.validate_plan({'plan': copy.deepcopy(saved)})
check('a saved plan with a script saves unchanged', again == saved)

# 3. settings replace the defaults they name, and only those
text = M.source_with(ITEM)
check('a float setting replaces its default', 'Seconds: float = 4.5' in text)
check('a bool setting replaces its default', 'Loud: bool = True' in text)
check('an unset default is kept', 'Label: str = "plain"' in text and 'Count: int = 2' in text)
check('a local in a function is not a setting', 'Waited' not in M.declared(SOURCE))
check('a declaration without a default is not a setting', 'Mode' not in M.declared(SOURCE))
check('text settings are quoted', 'Label: str = "loud"' in
      M.source_with(dict(ITEM, settings={'Label': 'loud'})))
check('assets named: inline ones, not an alias left unused',
      M.named_assets(SOURCE) == ['FallingBB_Explosion_SC'], M.named_assets(SOURCE))
check('assets named: an alias in use', 'CameraShakeGeneric_C' in M.named_assets(
    SOURCE + '\n\ndef Shook():\n    GetAllActorsOfClass(self, Shake, Found)\n'))
try:
    M.source_with(dict(ITEM, settings={'Nope': 1}))
    check('a setting the script does not declare is refused', False)
except SystemExit:
    check('a setting the script does not declare is refused', True)

# 4. put and remove on a record
rec = {'id': 'X', 'plan': {'added': [PLAN['added'][0]], 'rifts': []}}
one = M.put(rec, 'TestIntro', SOURCE, {'Seconds': 5.0}, {'replicates': True})
item = M.pieces(one['plan'])[0]
check('put stands a new script at the Crystal', item['matrix'][12:15] == [100.0, 200.0, 30.0],
      item['matrix'])
item['matrix'][12] = 999.0
two = M.put(one, 'TestIntro', SOURCE.replace('a timing', 'the timing'), {'Loud': True})
items = M.pieces(two['plan'])
check('put again replaces it in place, keeping its place and settings',
      len(items) == 1 and items[0]['matrix'][12] == 999.0
      and items[0]['settings'] == {'Seconds': 5.0, 'Loud': True}
      and 'the timing' in items[0]['source'], items)
check('put leaves the record it was given alone', not M.pieces(rec['plan']))
three = M.remove(two, 'TestIntro')
check('remove takes it out and keeps the rest', not M.pieces(three['plan'])
      and three['plan']['added'] == [PLAN['added'][0]])
check('put stands at the first rift without a Crystal', M.put(
    {'id': 'Y', 'plan': {'added': [], 'rifts': [{'at': [7.0, 8.0, 9.0]}]}},
    'TestIntro', SOURCE)['plan']['added'][0]['matrix'][12:15] == [7.0, 8.0, 9.0])

# 5. it compiles into a replicating Blueprint that imports what it names
from riftlord.types import setup  # noqa: E402
if setup.unavailable():
    check('compile (type data not built here: %s)' % setup.unavailable(), True)
else:
    from riftlord.formats.ue.uassetio import Package
    folder = tempfile.mkdtemp(prefix='missionscript-')
    built = M.package('Scenario_02', 'RiftLordScriptTest', {'added': [copy.deepcopy(ITEM)]})
    path = M.package_path('RiftLordScriptTest', 'TestIntro')
    check('package builds one Blueprint at its path', list(built) == [path], list(built))
    if path in built:
        uasset, uexp = built[path]
        names = [n for n, _h in Package(uasset, uexp).names]
        files = {'OMD/Content' + path[len('/Game'):] + '.uasset': uasset,
                 'OMD/Content' + path[len('/Game'):] + '.uexp': uexp}
        rows = {r['name']: r for r in M.check_files(files, {'added': [ITEM],
                                                            '_modKey': 'RiftLordScriptTest'})}
        check('the Blueprint is found in the files',
              rows['mission script TestIntro Blueprint in the pak']['ok'], rows)
        check('it imports every asset its source names',
              rows['mission script TestIntro imports every asset it names']['ok'], rows)
        check('without the level, it is not placed',
              not rows['mission script TestIntro placed with its class preloaded']['ok'])
        check('it replicates as its actor keys say', 'bReplicates' in names)
    try:
        M.package('Scenario_02', 'RiftLordScriptTest', {'added': [ITEM, copy.deepcopy(ITEM)]})
        check('two scripts with one name are refused', False)
    except SystemExit:
        check('two scripts with one name are refused', True)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('PASS' if ok else 'FAIL', name, ('  -- %s' % (detail,))[:300] if not ok and detail else ''))
print('%d/%d passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
