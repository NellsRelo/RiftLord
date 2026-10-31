"""Offline checks on the Crystal map piece (riftlord/mapeditor/crystal.py): the save, the
build's refusals, the unstable rifts it opens, the GameType line, and a build into a copy of
Scenario_02 with its gates.

    python3 test_crystal.py [SCRATCH]
"""
import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.mapeditor import crystal  # noqa: E402
from riftlord.mapeditor import rifttear  # noqa: E402
from riftlord.missions import customcampaign  # noqa: E402
from riftlord.missions import missionevents  # noqa: E402
from riftlord.missions import records  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


SCRATCH = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix='crystal-test-')
os.makedirs(SCRATCH, exist_ok=True)
PLAN = crystal.check_plan()
PLAN['spawners'][0]['index'] = 3
PLAN['spawners'][1]['index'] = 4
ITEM = PLAN['added'][0]
GROUP = ITEM['crystal']['groups'][0]

# 1. the save keeps one crystal with its settings and Rift Tear groups
two = copy.deepcopy(PLAN)
two['added'].append(copy.deepcopy(ITEM))
_w, saved = records.validate_plan({'plan': two})
kept = [a for a in saved['added'] if a.get('kind') == crystal.KIND]
check('the save keeps one crystal', len(kept) == 1, len(kept))
check('the save keeps its settings and groups',
      kept and kept[0]['crystal'] == {'difficultyHealth': [800.0, 1500.0, 2500.0, 3000.0],
                                      'bobHeight': 40.0, 'bobSeconds': 6.0, 'riftGap': 3.0,
                                      'groups': [GROUP]},
      kept and kept[0]['crystal'])
for label, value in (('a negative health', -5), ('a health that is not a number', 'x')):
    bad = copy.deepcopy(PLAN)
    bad['added'][0]['crystal']['difficultyHealth'][2] = value
    try:
        records.validate_plan({'plan': bad})
        check('%s is refused' % label, False)
    except ValueError:
        check('%s is refused' % label, True)
blank = copy.deepcopy(PLAN)
blank['added'][0]['crystal']['difficultyHealth'] = [None, 1500.0, '', 0]
_w, got = records.validate_plan({'plan': blank})
check('a blank health saves as 0 (no value)',
      [a for a in got['added'] if a.get('kind') == crystal.KIND][0]['crystal']['difficultyHealth']
      == [0.0, 1500.0, 0.0, 0.0])
older = copy.deepcopy(PLAN)
del older['added'][0]['crystal']['difficultyHealth']
older['added'][0]['crystal'].update(healthPerRiftPoint=5.0, minHealth=1200.0)
_w, got = records.validate_plan({'plan': older})
oc = [a for a in got['added'] if a.get('kind') == crystal.KIND][0]['crystal']
check('a plan with Health per Rift Point reads as its Minimum Health at every difficulty',
      oc['difficultyHealth'] == [1200.0] * 4 and 'healthPerRiftPoint' not in oc
      and 'minHealth' not in oc
      and crystal.values(older['added'][0])['difficultyHealth'] == [1200.0] * 4, oc)
check('it writes DifficultyHealth only, never HealthPerRiftPoint or MinHealth',
      {k: x for k, x in crystal.written_properties(older, older['added'][0], {}).items()
       if 'Health' in k} == {'DifficultyHealth': [1200.0] * 4},
      crystal.written_properties(older, older['added'][0], {}))
rows_item = {'kind': crystal.KIND, 'matrix': ITEM['matrix'],
             'crystal': {'rifts': [{'at': 75.0, 'spawner': 7, 'unit': 'OrcLight', 'count': 3}]}}
check('older rows write RiftAt / RiftWave / RiftIndex and the spawner\'s units only',
      crystal.uses_rows(rows_item)
      and crystal.rows_arrays(PLAN, rows_item) == {'RiftAt': [75.0], 'RiftWave': [0],
                                                    'RiftIndex': [3]}
      and crystal.rows_spawner_fields(PLAN, rows_item, crystal.unit_guids())
      == {3: (tuple(crystal.unit_guids()['OrcLight']), 3)},
      crystal.rows_arrays(PLAN, rows_item))
old = rifttear.from_rows([{'at': 75.0, 'spawner': 7, 'unit': 'OrcLight', 'count': 3}])
check('an older rift row reads as a group of one with its unit and health trigger',
      old == [{'name': 'Rift Group 1', 'rifts': [{'spawner': 7, 'difficulty': 5, 'players': 0}],
               'difficulty': 5, 'players': 0,
               'minions': [{'unit': 'OrcLight', 'count': 3, 'addCount': 0}],
               'triggers': [{'at': 75.0, 'wave': None, 'both': False, 'open': 1,
                             'alive': 0.0, 'launchAfter': 0.0, 'launchAlive': 0.0,
                             'repeat': 0}]}], old)
#: launch timing: blank 0, ranges checked; an older plan's `close` dropped without an error
trig = {'name': 'G', 'rifts': [{'spawner': 7}], 'minions': [],
        'triggers': [{'at': 50, 'wave': None, 'open': 1, 'close': 4,
                      'launchAfter': '', 'launchAlive': None}]}
got = rifttear.clean_groups([trig])[0]['triggers'][0]
check('a trigger\'s blank launch fields are 0 and an older Stay Open Until Wave is dropped',
      got.get('launchAfter') == 0.0 and got.get('launchAlive') == 0.0 and 'close' not in got, got)
check('a trigger without Repeat Every (Waves) marks once (repeat 0)', got.get('repeat') == 0, got)
for value, want_repeat in (('', 0), (None, 0), (0, 0), (1, 1), ('2', 2), (2.6, 3), (99, 99)):
    ok = copy.deepcopy(trig)
    ok['triggers'][0]['repeat'] = value
    got = rifttear.clean_groups([ok])[0]['triggers'][0]['repeat']
    check('repeat %r is kept as %d' % (value, want_repeat), got == want_repeat, got)
for key, value in (('launchAfter', 601), ('launchAfter', -1), ('launchAlive', 100),
                   ('launchAlive', 'x'), ('repeat', 100), ('repeat', -1), ('repeat', 'x')):
    bad = copy.deepcopy(trig)
    bad['triggers'][0][key] = value
    try:
        rifttear.clean_groups([bad])
        check('%s %r is refused' % (key, value), False)
    except ValueError:
        check('%s %r is refused' % (key, value), True)

# 2. only what differs from the class default is written, and the groups' arrays
plain = {'added': [{'kind': crystal.KIND, 'matrix': ITEM['matrix'],
                    'crystal': dict(crystal.defaults())}]}
base_min = crystal.class_defaults()['MinHealth']
check('defaults write the four healths (the class MinHealth) and nothing else',
      crystal.written_properties(plain, plain['added'][0])
      == {'DifficultyHealth': [base_min] * 4},
      crystal.written_properties(plain, plain['added'][0]))
guids = crystal.unit_guids()
got = crystal.written_properties(PLAN, ITEM, guids)
want = {'DifficultyHealth': [800.0, 1500.0, 2500.0, 3000.0],
        'BobHeight': 40.0, 'BobSeconds': 6.0, 'RiftGap': 3.0,
        'GroupStart': [0], 'GroupSize': [2], 'GroupMembers': [3, 4],
        'MemberDifficulty': [5, 3], 'MemberPlayers': [0, 2],
        'GroupDifficulty': [5], 'GroupPlayers': [0],
        'GroupUnitStart': [0], 'GroupUnitSize': [2],
        'UnitProto': [tuple(guids['OrcLight']), tuple(guids['OrcMedium'])],
        'UnitCount': [3, 2], 'UnitEndlessAdd': [0, 1],
        'RiftGroup': [0, 0], 'RiftAt': [75.0, 40.0], 'RiftWave': [0, 3],
        'RiftBoth': [False, True], 'RiftPick': [1, 2],
        'RiftLaunchAfter': [0.0, 20.0], 'RiftLaunchAlive': [0.0, 30.0],
        'RiftAlive': [0.0, 0.0], 'RiftRepeat': [0, 2], 'GroupName': ['North-west']}
check('settings and Rift Tear arrays are written (triggers at full length; Any = 5; '
      'no RiftClose)', got == want, got)
norepeat = copy.deepcopy(PLAN)
for t in norepeat['added'][0]['crystal']['groups'][0]['triggers']:
    del t['repeat']
got = crystal.written_properties(norepeat, norepeat['added'][0], guids)
check('an older plan without repeat writes RiftRepeat 0 at full length',
      got.get('RiftRepeat') == [0, 0] and len(got['RiftRepeat']) == len(got['RiftAt']), got)

# 3. the build refuses a second crystal, a group naming no unstable rift, no minions
check('a sound plan has no problems', crystal.problems(PLAN) == [], crystal.problems(PLAN))
stray = copy.deepcopy(PLAN)
stray['added'][0]['crystal']['groups'][0]['rifts'][0]['spawner'] = 99
check('a group naming no unstable rift is refused', crystal.problems(stray),
      crystal.problems(stray))
check('two crystals are refused', crystal.problems(two))
nounit = copy.deepcopy(PLAN)
nounit['added'][0]['crystal']['groups'][0]['minions'] = []
nounit['added'][0]['crystal']['groups'][0]['waves'] = []
#: minions and triggers are set in the Onslaught Editor, which opens only a built level
check('a group sending no minions yet builds, with a warning',
      crystal.problems(nounit) == [] and any(
          'sends no minions' in w for w in rifttear.warnings(
              crystal.values(nounit['added'][0]))),
      crystal.problems(nounit))
notrig = copy.deepcopy(PLAN)
notrig['added'][0]['crystal']['groups'][0]['triggers'] = []
check('a group with no trigger yet builds, with a warning',
      crystal.problems(notrig) == [] and any(
          'never opens' in w for w in rifttear.warnings(crystal.values(notrig['added'][0]))),
      crystal.problems(notrig))
blank = copy.deepcopy(PLAN)
blank['added'][0]['crystal']['groups'][0]['triggers'][0]['at'] = None
check('a trigger with no Health % and no Wave builds, with a warning',
      crystal.problems(blank) == [] and any(
          'no When' in w for w in rifttear.warnings(crystal.values(blank['added'][0]))),
      crystal.problems(blank))
nostart = copy.deepcopy(PLAN)
nostart['spawners'][1]['start'] = None
#: named by its index in level (4), as both editors number Unstable Rifts
check('a Crystal rift with no start point is refused',
      any('Unstable Rift 4' in p and 'start point' in p for p in crystal.problems(nostart)),
      crystal.problems(nostart))
many = copy.deepcopy(PLAN)
many['added'][0]['crystal']['groups'][0]['triggers'][0]['open'] = 3
check('opening more rifts than the group has warns',
      rifttear.warnings(crystal.values(many['added'][0])), [])

# 3b. the Onslaught Editor's half merges onto the plan, the Map Editor's half is kept
view = rifttear.editor_view(PLAN)
view['groups'][0]['name'] = 'Renamed'
view['groups'][0]['rifts'] = [{'spawner': 8, 'difficulty': 1, 'players': 1}]
view['groups'][0]['minions'] = [{'unit': 'Ogre', 'count': 1, 'addCount': 0}]
view['riftGap'] = 1.5
merged = copy.deepcopy(PLAN)
changed = rifttear.merge_editor(merged, view)
mg = merged['added'][0]['crystal']['groups'][0]
check('the Onslaught Editor keeps the Map Editor\'s name and rifts, takes its own fields',
      changed and mg['name'] == 'North-west' and [r['spawner'] for r in mg['rifts']] == [7, 8]
      and mg['rifts'][1]['difficulty'] == 1 and mg['minions'][0]['unit'] == 'Ogre'
      and merged['added'][0]['crystal']['riftGap'] == 1.5, mg)
check('the Rift Tear tab shows the plan\'s health per difficulty',
      view['health']['values'] == [800.0, 1500.0, 2500.0, 3000.0]
      and view['health']['difficulties'] == ['Apprentice', 'War Mage', 'Rift Lord', 'Endless'],
      view['health'])
view['health']['values'] = [900, 1500, None, 5000]
merged = copy.deepcopy(PLAN)
rifttear.merge_editor(merged, view)
check('the Rift Tear tab\'s health is the plan\'s, which the Map Editor reads (blank 0)',
      merged['added'][0]['crystal']['difficultyHealth'] == [900.0, 1500.0, 0.0, 5000.0]
      and rifttear.editor_view(merged)['health']['values'] == [900.0, 1500.0, 0.0, 5000.0],
      merged['added'][0]['crystal'])
same = copy.deepcopy(older)
check('an unchanged health from the tab changes nothing',
      not rifttear.merge_editor(same, rifttear.editor_view(older)))

# 4. a rift the crystal opens needs no Mission Events piece and no open event, and no wave
#    may send units through it
spec = {'waves': [{'spawns': {'3': [{'unit': 'OrcLight', 'count': 3}]}}]}
check('a crystal rift passes the rift checks', missionevents.rift_problems(PLAN, spec) == [],
      missionevents.rift_problems(PLAN, spec))
check('a wave through the crystal\'s rift is refused',
      any('wave 1 sends units through unstable rift 3' in p
          for p in crystal.problems(PLAN, spec)), crystal.problems(PLAN, spec))
check('waves elsewhere are not', crystal.problems(PLAN, {'waves': [{'spawns': {'1': [
    {'unit': 'OrcLight', 'count': 3}]}}]}) == [])
alone = copy.deepcopy(PLAN)
alone['added'] = []
check('the same rift without the crystal is refused', missionevents.rift_problems(alone, spec))

# 5. the notes carry GameType, keep it through a details edit, and drop it without a crystal
loose = os.path.join(SCRATCH, '900_Crystal_Test_P')
os.makedirs(loose, exist_ok=True)
row = 'RiftLordCrystalTest_Mission01'
customcampaign.write_notes(loose, maps=[{'Mission': row, 'Description': 'x',
                                         'GameType': crystal.game_type(PLAN)}])
check('notes give GameType CrystalDefense',
      customcampaign.read_game_type(loose, row) == crystal.GAME_TYPE,
      customcampaign.read_game_type(loose, row))
customcampaign.write_notes(loose, maps=[{'Mission': row, 'Description': 'y'}])
check('a details edit keeps it', customcampaign.read_game_type(loose, row) == crystal.GAME_TYPE)
customcampaign.write_notes(loose, maps=[{'Mission': row, 'Description': 'y',
                                         'GameType': crystal.game_type(alone)}])
check('a build without a crystal drops it', customcampaign.read_game_type(loose, row) == '')

# 6. where it goes on Scenario_02: on the rift base; turned so the mod's pieces stand on the
#    level's pillars (a quarter turn of the axes), else facing the routes from the south
where = crystal.spot('Scenario_02')
from riftlord.mapeditor import crystalpark  # noqa: E402
want_yaws = (0.0, 180.0) if crystalpark.manifest() else (-90.08,)
check('Scenario_02: on the RiftBase top, turned to its pillars or the routes',
      abs(where['at'][2] - 532.48) < 0.05 and any(abs(where['yaw'] - y) < 1.0 for y in want_yaws),
      where)
if crystalpark.manifest():
    got = crystalpark.parked('Scenario_02', where['matrix'])
    check('Scenario_02: the 20 platform pieces are parked (4 each of 3 pillar parts, 8 chain parts)',
          len(got) == 20, ['%d %s' % (r['id'], r['mesh']) for r in got])

# 7. a build into a copy of Scenario_02 with every gate
if crystal.pak():
    from riftlord.build import levelbuild  # noqa: E402
    fails = crystal.check(SCRATCH, log=lambda _s: None)
    check('build into Scenario_02: pkgcheck, imports, preloads, gates', not fails,
          '; '.join(fails[:3]))

    def built(folder):
        with open(os.path.join(folder, 'CrystalCheck_Map01.umap'), 'rb') as fh:
            ua = fh.read()
        with open(os.path.join(folder, 'CrystalCheck_Map01.uexp'), 'rb') as fh:
            ux = fh.read()
        return levelbuild.Builder.around(ua, ux)

    def read_back(folder):
        return crystal.placed(built(folder))[0][1]
    props = read_back(SCRATCH)
    check('read back: DifficultyHealth as set, HealthPerRiftPoint and MinHealth unwritten',
          props.get('DifficultyHealth') == [800.0, 1500.0, 2500.0, 3000.0]
          and 'HealthPerRiftPoint' not in props and 'MinHealth' not in props, props)
    level = built(SCRATCH)
    tear = rifttear.read(level.pkg, crystal.placed(level)[0][0])
    view = rifttear.level_view(tear)
    check('read back: RiftRepeat as written, and a no-record view\'s triggers repeat as planned',
          tear.get('RiftRepeat') == [0, 2]
          and [t['repeat'] for t in view['groups'][0]['triggers']] == [0, 2],
          (tear.get('RiftRepeat'), view['groups']))
    blank_dir = os.path.join(SCRATCH, 'blank')
    os.makedirs(blank_dir, exist_ok=True)
    bplan = crystal.check_plan()
    bplan['added'][0]['crystal']['difficultyHealth'] = [0, 1800.0, 0, 0]
    #: and the second rift without a start point: only the start gate fails
    bplan['spawners'][1]['start'] = None
    fails = crystal.check(blank_dir, log=lambda _s: None, plan=bplan)
    props = read_back(blank_dir)
    check('read back: blank healths written as 0, all four',
          props.get('DifficultyHealth') == [0.0, 1800.0, 0.0, 0.0], props)
    check('the start gate fails on a Crystal rift with no StartingPathPoint, and only it',
          len(fails) == 1 and 'start at a placed path point' in fails[0], fails)
else:
    check('build into Scenario_02 (no %s to read)' % crystal.pak_name(), True)

for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
failed = [c for c in checks if not c[1]]
print('%d checks, %d failed' % (len(checks), len(failed)))
sys.exit(1 if failed else 0)
