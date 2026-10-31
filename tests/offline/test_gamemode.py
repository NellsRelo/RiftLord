"""Offline checks on the Onslaught Editor's game mode (riftlord/onslaught/gamemode.py): a plan
with Rift Tear groups, a plan from before groups, a plan without a Crystal, Crystal Probe A's
record and its built level, the Missions record found for a map, and the build's refusal of
a wave through a rift the Crystal opens.

    python3 test_gamemode.py [SCRATCH]

Crystal Probe A (riftlord.mapeditor.probe) is read from RIFTLORD_CRYSTAL_PROBE, else
~/.cache/rift-lord/probes/Crystal_Probe_A; its checks are skipped when it is not there. It is
only read: the Missions check works on a copy in SCRATCH.
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

SCRATCH = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix='gamemode-test-')
os.makedirs(SCRATCH, exist_ok=True)
#: the Missions folder bucket.root() reads, set before anything reads it; removed at the end
MISSIONS = tempfile.mkdtemp(prefix='Missions-', dir=SCRATCH)
os.environ['OMD3_BUCKET'] = MISSIONS

from riftlord.mapeditor import crystal  # noqa: E402
from riftlord.onslaught import gamemode  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


PROBE = os.environ.get('RIFTLORD_CRYSTAL_PROBE') or os.path.join(
    os.path.expanduser('~'), '.cache', 'rift-lord', 'probes', 'Crystal_Probe_A')
PROBE_ID = '900_Crystal_Probe_A_P'
PROBE_MAP = 'RiftLordCrystalProbeA_Map01'


def rift(sid, index):
    return {'id': sid, 'at': [0.0, 0.0, 0.0], 'rotation': 0.0, 'kind': 'ground', 'index': index,
            'maxSpawnPerRow': 0, 'door': None, 'start': None, 'flyerStart': None,
            'unstable': True}


def plan_with(settings, spawners):
    return {'level': 'Scenario_02', 'version': 2, 'spawners': spawners,
            'added': [{'kind': crystal.KIND, 'name': crystal.CLASS,
                       'matrix': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                       crystal.KEY: settings}]}


# 1. the modes, keyed by the GameType the build writes
rd, cd = gamemode.view(''), gamemode.view(crystal.GAME_TYPE)
check('Rift Defense is the GameType-less mode and hides nothing',
      rd['label'] == 'Rift Defense' and rd['hide'] == [] and rd['opens'] == [], rd)
check('Crystal Defense is keyed CrystalDefense and hides rift points',
      cd['gameType'] == 'CrystalDefense' and cd['label'] == 'Crystal Defense'
      and cd['hide'] == ['riftPoints'], cd)

# 2. a plan with Rift Tear groups: the rifts of every group
groups = {crystal.HEALTH_KEY: [800.0, 1500.0, 2500.0, 3000.0],
          'groups': [{'name': 'North', 'difficulty': 5, 'players': 0,
                      'rifts': [{'spawner': 7, 'difficulty': 5, 'players': 0}],
                      'minions': [], 'triggers': []},
                     {'name': 'South', 'difficulty': 5, 'players': 0,
                      'rifts': [{'spawner': 8, 'difficulty': 5, 'players': 0}],
                      'minions': [], 'triggers': []}]}
got = gamemode.of_plan(plan_with(groups, [rift(7, 4), rift(8, 5), rift(9, 6)]))
check('a plan with groups: Crystal Defense, opening its groups\' rifts only',
      got['gameType'] == crystal.GAME_TYPE and got['opens'] == [4, 5], got)

# 3. a plan from before groups (rows, healthPerRiftPoint)
rows = {'healthPerRiftPoint': 5.0, 'bobHeight': 25.0, 'bobSeconds': 4.0,
        'rifts': [{'at': 75.0, 'spawner': 1, 'unit': 'OrcLight', 'count': 3}]}
got = gamemode.of_plan(plan_with(rows, [rift(1, 3)]))
check('an older plan with rift rows: Crystal Defense, opening the rows\' rift',
      got['gameType'] == crystal.GAME_TYPE and got['opens'] == [3], got)
got = gamemode.of_plan(plan_with({}, []))
check('a Crystal with no rifts: Crystal Defense, opening none',
      got['gameType'] == crystal.GAME_TYPE and got['opens'] == [], got)

# 4. a plan without a Crystal, and no plan
got = gamemode.of_plan({'level': 'Scenario_02', 'added': [{'kind': 'chest'}],
                        'spawners': [rift(1, 3)]})
check('a plan without a Crystal: Rift Defense', got == rd, got)
check('no plan: Rift Defense', gamemode.of_plan(None) == rd)

# 5. built levels: a placed Crystal's arrays, and none
check('a built Crystal with groups opens its members',
      gamemode.of_level([{'cls': crystal.CLASS, 'arrays': {'GroupMembers': [5, 4, 4]}}])['opens']
      == [4, 5])
check('a built level without a game-type piece: Rift Defense',
      gamemode.of_level([]) == rd and gamemode.of_level(None) == rd)

# 6. the build's refusal
spec = {'waves': [{'spawns': {'1': [{'unit': 'OrcLight', 'count': 2}]}},
                  {'spawns': {'3': [{'unit': 'OrcLight', 'count': 2}]}}]}
got = gamemode.spec_problems(spec, gamemode.view(crystal.GAME_TYPE, [3]))
check('a wave through a rift the Crystal opens is refused, others are not',
      len(got) == 1 and got[0].startswith('wave 2 '), got)
check('Rift Defense refuses no wave', gamemode.spec_problems(spec, rd) == [])

# 6b. Rift Groups per wave (contract 2026-10-28): rows, pauses, chances, RiftAlive, names;
#     the built arrays read back as the editor's view, and the view saved again writes them
from riftlord.mapeditor import rifttear  # noqa: E402
guids = crystal.unit_guids()
waved = {crystal.HEALTH_KEY: [900.0, 1200.0, 1500.0, 2000.0], 'waveCount': 3,
         'groups': [{'name': 'West', 'difficulty': 5, 'players': 0,
                     'rifts': [{'spawner': 7, 'difficulty': 5, 'players': 0,
                                'chances': [100.0, 0.0, 60.0]},
                               {'spawner': 8, 'difficulty': 5, 'players': 2}],
                     'minions': [],
                     'waves': [[{'unit': 'OrcLight', 'count': 3, 'addCount': 1, 'promote': 0.5,
                                 'difficulty': 5, 'before': None,
                                 'after': {'kind': 'wait', 'value': 4}},
                                {'unit': 'OrcMedium', 'count': 2, 'addCount': 0, 'promote': 0.5,
                                 'difficulty': 5, 'before': None,
                                 'after': {'kind': 'gate', 'value': 50}},
                                {'unit': 'OrcHeavy', 'count': 1, 'addCount': 0, 'promote': 0.25,
                                 'difficulty': 2, 'before': None, 'after': None}],
                               [],
                               [{'unit': 'OrcLight', 'count': 5, 'addCount': 0, 'promote': 0.5,
                                 'difficulty': 5, 'before': {'kind': 'wait', 'value': 2},
                                 'after': None}]],
                     'triggers': [{'at': None, 'wave': 2, 'alive': 40.0, 'both': True,
                                   'open': 1, 'launchAfter': 0.0, 'launchAlive': 30.0,
                                   'repeat': 3}]}]}
plan_w = plan_with(waved, [rift(7, 4), rift(8, 5)])
clean = rifttear.clean_groups(waved['groups'])
check('clean_groups keeps per-wave rows, pauses and chances',
      clean[0]['waves'][0][1]['after'] == {'kind': 'gate', 'value': 50.0}
      and clean[0]['rifts'][0]['chances'] == [100.0, 0.0, 60.0]
      and 'chances' not in clean[0]['rifts'][1] and clean[0]['triggers'][0]['alive'] == 40.0,
      clean)
a = rifttear.arrays(waved, plan_w, guids)
check('per-wave arrays: slots, rows, the three pause kinds, chances (100 past a rift\'s own)',
      a['WaveCount'] == 3 and a['GroupWaveStart'] == [0, 3, 3] and a['GroupWaveSize'] == [3, 0, 1]
      and a['RowCount'] == [3, 2, 1, 5] and a['RowEndlessAdd'] == [1, 0, 0, 0]
      and a['RowDifficulty'] == [5, 5, 2, 5] and a['RowPromote'] == [0.5, 0.5, 0.25, 0.5]
      and a['RowPauseKind'] == [0, 1, 2, 1] and a['RowPauseValue'] == [0.0, 4.0, 50.0, 2.0]
      and a['MemberChance'] == [100.0, 0.0, 60.0, 100.0, 100.0, 100.0]
      and a['RiftAlive'] == [40.0] and a['RiftRepeat'] == [3] and a['GroupName'] == ['West']
      and a['RowProto'] == [tuple(guids[u]) for u in ('OrcLight', 'OrcMedium', 'OrcHeavy',
                                                      'OrcLight')], a)
check('no wave count writes no per-wave arrays',
      not any(k in rifttear.arrays(dict(waved, waveCount=0), plan_w, guids)
              for k in rifttear.PER_WAVE + (rifttear.WAVE_COUNT,)))
back = rifttear.level_view(dict(a, DifficultyHealth=waved[crystal.HEALTH_KEY]),
                           {'WaveCount': 3, 'RiftGap': 2.0})
again = rifttear.arrays(dict(back, groups=[dict(g, rifts=[dict(r, spawner={4: 7, 5: 8}[r['spawner']])
                                                           for r in g['rifts']])
                                           for g in back['groups']]), plan_w, guids)
check('arrays read back as the editor\'s view write the same arrays',
      {k: again[k] for k in a} == a and back['groups'][0]['name'] == 'West'
      and back['health']['values'] == waved[crystal.HEALTH_KEY],
      {k: (a[k], again.get(k)) for k in a if again.get(k) != a[k]})
check('a read-back trigger keeps Repeat Every (Waves); a Crystal without RiftRepeat reads 0',
      back['groups'][0]['triggers'][0]['repeat'] == 3
      and rifttear.level_view({k: x for k, x in a.items() if k != 'RiftRepeat'},
                              {'WaveCount': 3})['groups'][0]['triggers'][0]['repeat'] == 0,
      back['groups'][0]['triggers'])
for text in ('West', 'Ost-Süd'):
    payload, _inner = rifttear._array_payload(None, 'str', [text, 'B'])
    check('GroupName %r round-trips as an FString array' % text,
          rifttear.decode('str', payload) == [text, 'B'])
#: the view as the Onslaught Editor saves it: rows and chances per wave, onto the plan
saved_plan = plan_with({crystal.HEALTH_KEY: [1000.0] * 4,
                        'groups': [{'name': 'West', 'rifts': [{'spawner': 7}, {'spawner': 8}],
                                    'minions': [{'unit': 'OrcLight', 'count': 1}],
                                    'triggers': []}]},
                       [rift(7, 4), rift(8, 5)])
view = rifttear.editor_view(saved_plan)
view['groups'] = waved['groups']
view['waveCount'] = 3
view['health']['values'] = [1.0, 2.0, 3.0, 4.0]
check('merge_editor takes rows, chances, wave count and health', rifttear.merge_editor(saved_plan, view))
got = crystal.piece(saved_plan)[crystal.KEY]
check('the plan keeps the Map Editor\'s names and rifts with the Onslaught Editor\'s per-wave data',
      got['waveCount'] == 3 and got['groups'][0]['name'] == 'West'
      and got['groups'][0]['waves'][2][0]['count'] == 5
      and got['groups'][0]['rifts'][0]['chances'] == [100.0, 0.0, 60.0]
      and got[crystal.HEALTH_KEY] == [1.0, 2.0, 3.0, 4.0], got)

# 6b2. an older Crystal's rift (RiftIndex) sends its spawner's own units
older = rifttear.level_view({'RiftIndex': [3], 'RiftAt': [75.0]}, {},
                            {3: (tuple(guids['OrcLight']), 3)})
check('an older rift row reads its spawner\'s units as the group\'s rows',
      older['groups'][0]['minions'] == [{'unit': 'OrcLight', 'count': 3, 'addCount': 0}]
      and older['groups'][0]['triggers'][0]['repeat'] == 0, older)

# 6c. a built Crystal Defense level opened with no record (Crystal Probe B, when installed):
#     the Map Editor's plan item for its Crystal, its rifts as the level's own
from riftlord.build.navigation import navpatch  # noqa: E402
if 'CrystalProbeB_Map01' in navpatch.maps():
    from riftlord.missions import records as _records
    item = crystal.adopt('CrystalProbeB_Map01')
    check('a built level\'s Crystal adopts with its transform, groups and level name',
          bool(item) and item.get('level') and len(item['crystal']['groups']) == 2
          and all(r['spawner'] < 0 for g in item['crystal']['groups'] for r in g['rifts']),
          item)
    _w, adopted = _records.validate_plan({'plan': {'level': 'CrystalProbeB_Map01', 'version': 2,
                                                   'added': [item]}})
    piece = crystal.piece(adopted)
    got = crystal.written_properties(adopted, piece, guids)
    check('the adopted Crystal keeps its level name and writes its rifts\' own indices',
          piece.get('level') == item['level'] and got['GroupMembers'] == [3, 5, 4], got)
    from riftlord.onslaught import gamedata as _gd
    rifts = [d for d in _gd.map_detail('CrystalProbeB_Map01')['doors'] if d['kind'] == 'rift']
    check('the Onslaught Editor numbers each Unstable Rift by its index in level',
          [(d['label'], d['short']) for d in rifts]
          == [('Unstable Rift %d' % d['spawners'][0], str(d['spawners'][0])) for d in rifts]
          and sorted(d['spawners'][0] for d in rifts) == [3, 4, 5], rifts)
else:
    check('Crystal Probe B not installed: adopt skipped', True)

# 7. the test missions' records (tests/fixtures/missions): a shipped-level edit is Rift
#    Defense
fixture = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fixtures',
                       'missions', '.riftlord', 'records', '900_Fixture_Edit_P.json')
with open(fixture) as fh:
    got = gamemode.of_plan(json.load(fh).get('plan'))
check('the test mission Fixture Edit\'s record: Rift Defense', got == rd, got)

# 8. Crystal Probe A: its record (a plan from before groups), its built level, and the
#    Missions record found for its map
record = os.path.join(PROBE, '.riftlord', 'records', PROBE_ID + '.json')
pak = os.path.join(PROBE, '_out', PROBE_ID + '.pak')
if os.path.isfile(record) and os.path.isfile(pak):
    from riftlord.build.navigation import navpatch
    from riftlord.missions import bucket
    from riftlord.onslaught import gamedata, onslaught
    with open(record) as fh:
        rec = json.load(fh)
    got = gamemode.of_plan(rec['plan'])
    check('Crystal Probe A\'s record: Crystal Defense opening rift 3',
          got['gameType'] == crystal.GAME_TYPE and got['opens'] == [3], got)
    navpatch.open_pak(pak)
    gamedata._CACHE.clear()
    pieces = onslaught.map_report(PROBE_MAP)['pieces']
    got = gamemode.of_level(pieces)
    check('Crystal Probe A\'s built level, opened without its record: Crystal Defense '
          'opening rift 3', got['gameType'] == crystal.GAME_TYPE and got['opens'] == [3], pieces)
    got = gamedata.map_detail(PROBE_MAP)['mode']
    check('the editor\'s map detail of Crystal Probe A: Crystal Defense',
          got['gameType'] == crystal.GAME_TYPE and got['hide'] == ['riftPoints'], got)
    navpatch.close_pak(pak, every=True)
    #: a copy of the probe as a Missions item: its record is the map's
    os.makedirs(os.path.join(MISSIONS, '.riftlord', 'records'), exist_ok=True)
    shutil.copy2(record, os.path.join(MISSIONS, '.riftlord', 'records'))
    shutil.copytree(os.path.join(PROBE, PROBE_ID), os.path.join(MISSIONS, PROBE_ID))
    bucket.activate()
    found = gamemode.record_of(PROBE_MAP)
    check('the Missions record of Crystal Probe A\'s map is found',
          bool(found) and found.get('id') == PROBE_ID, found and found.get('id'))
    got = gamemode.of_map(PROBE_MAP, [])
    check('of_map takes the record\'s plan over the level', got['gameType'] == crystal.GAME_TYPE
          and got['opens'] == [3], got)
    item = bucket.item(PROBE_ID)
    check('bucket.item carries the plan\'s mode and the Rift Tear view',
          (item or {}).get('mode', {}).get('gameType') == crystal.GAME_TYPE
          and bool((item or {}).get('riftTear')), item and item.get('mode'))
    check('a shipped map: Rift Defense',
          gamemode.of_map('Scenario_02', onslaught.map_report('Scenario_02')['pieces']) == rd)
else:
    check('Crystal Probe A (not at %s): skipped' % PROBE, True)
shutil.rmtree(MISSIONS, ignore_errors=True)

# 7. the Rift Group trigger words and the build's range checks (Open, When Wave)
check('no trigger label says "Launch"',
      not any('launch' in v.lower() for v in rifttear.LABELS.values()), rifttear.LABELS)
tear_spec = {'waves': [{'spawns': {}}, {'spawns': {}}],
             'riftTear': {'rifts': [{'id': 7, 'index': 4}],
                          'groups': [{'name': 'West', 'rifts': [{'spawner': 7}],
                                      'triggers': [{'at': 50, 'wave': None, 'open': 1}]}]}}
check('a trigger in range builds', rifttear.spec_problems(tear_spec) == [],
      rifttear.spec_problems(tear_spec))
over = json.loads(json.dumps(tear_spec))
over['riftTear']['groups'][0]['triggers'] = [{'at': None, 'wave': 3, 'open': 2}]
got = rifttear.spec_problems(over)
check('Open past the group\'s rifts and a Wave past the onslaught\'s are refused',
      got == ['Rift Group West: Open 2 Rifts, but it has 1 Unstable Rift',
              'Rift Group West: When Wave 3, but the onslaught has 2 waves'], got)

for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
failed = [c for c in checks if not c[1]]
print('%d checks, %d failed' % (len(checks), len(failed)))
sys.exit(1 if failed else 0)
