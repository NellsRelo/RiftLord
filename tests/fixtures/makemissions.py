"""Write the test missions the gate and the release check build and open (tests/fixtures/missions,
a Missions folder): small, made only from what this file states, never from the user's
Missions.

    python3 tests/fixtures/makemissions.py          # (re)write tests/fixtures/missions

- EDIT (900_Fixture_Edit_P), a shipped-level edit of North Wing (Scenario_01): a new sky (a
  light bake, ISPC-encoded light maps), a Cut minimap, removed pieces (parked), removed
  effects and sounds, a moved piece, added flyer spawners with their doors and route
  (facing it), added kit pieces (one mirrored), a compound and an effect, one wave,
  Endless on. The UI suites open it.
- SCRATCH (900_Fixture_Scratch_P), a scratch level on North Wing: a corridor of kit floors,
  a Regenerate minimap cut at the doors (small: a Regenerate of the whole North Wing takes
  minutes), an added blocking volume, made-up Fab pieces (one mirrored) and a terrain tile
  (fixturelibs), a spawner with its door and route, a Crystal with two Unstable Rifts in a
  Rift Group, one wave sending the made-up mod minion (carried), two Scramble tiers (one no
  mod defines), listed in a new custom campaign (CAMPAIGN_NAME).
Plans go through the Map Editor's save (leveldata.save_plan) as a saved plan does; the
libraries and mod paks come from fixturelibs.ensure (into a scratch folder).
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import fixturelibs  # noqa: E402

MISSIONS = os.path.join(HERE, 'missions')
LEVEL = 'Scenario_01'
EDIT = '900_Fixture_Edit_P'
SCRATCH = '900_Fixture_Scratch_P'
CAMPAIGN_NAME = 'Fixture Campaign'
#: Fixed identities: mod key and row Guids (as stored) of each mission.
IDENTITY = {
    EDIT: ('RiftLordFixtureEdit', {'RiftLordFixtureEdit_Mission01': 'f1e0d1000000000000000000000000e1',
                                   'RiftLordFixtureEdit_Endless01': 'f1e0d1000000000000000000000000e2'}),
    SCRATCH: ('RiftLordFixtureScratch',
              {'RiftLordFixtureScratch_Mission01': 'f1e0d2000000000000000000000000e1'}),
}
#: The time stamps every record carries.
STAMP = 1793000000.0


def matrix(at, yaw=0.0, scale=(1.0, 1.0, 1.0), mirror_y=False):
    """A plan matrix (flat, column-major, translation last) turned `yaw` degrees about z."""
    import math
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    sx, sy, sz = scale
    if mirror_y:
        sy = -sy
    cols = [(c * sx, s * sx, 0.0), (-s * sy, c * sy, 0.0), (0.0, 0.0, sz)]
    out = []
    for col in cols:
        out += [round(v, 6) for v in col] + [0.0]
    return out + [float(at[0]), float(at[1]), float(at[2]), 1.0]


def point(pid, at, nxt, flyers=False):
    return {'id': pid, 'at': list(at), 'reachRadius': 300.0, 'formationSize': 3,
            'formationSpacing': 0.0, 'flightHeight': 0.0, 'flyersOnly': flyers,
            'groundOnly': False, 'next': [{'to': n, 'weight': 1.0} for n in nxt]}


def row(rid, unit, count, difficulty=5, guid=None):
    out = {'id': rid, 'unit': unit, 'count': count, 'promote': 0.5, 'before': None,
           'after': None, 'difficulty': difficulty, 'addCount': None}
    if guid:
        out['guid'] = guid
    return out


def compound(key, at, group):
    """`added` items of compound `key` at its default params, anchored at `at`."""
    from riftlord.mapeditor import compounds
    out = []
    for k, (name, kind, m) in enumerate(compounds.layout(key)):
        flat = [m[r][c] for c in range(4) for r in range(4)]
        flat[12:15] = [flat[12] + at[0], flat[13] + at[1], flat[14] + at[2]]
        item = {'name': name, 'kind': kind, 'matrix': [round(v, 6) for v in flat], 'group': group}
        if k == 0:
            item.update(compound=key, anchor=True)
        out.append(item)
    return out


#: More flyer spawners on EDIT's route, each with a door of its own: enough door tabs that
#: the Onslaught Editor's tab strip overflows at the UI suite's narrow width.
EXTRA_FLYERS = 4


def edit_plan():
    """EDIT's plan, before the save cleans it."""
    flyer_route = [(1958.0, -2432.45, -328.21), (-235.73, -2674.33, -281.31),
                   (-630.93, -951.0, 333.28), (-793.96, 2521.0, 365.0), (-1852.0, 3592.0, 511.27),
                   (-3193.0, 3489.0, 318.48)]
    points = [dict(point(3 + k, at, [4 + k] if k + 1 < len(flyer_route) else [], flyers=True),
                   isStart=k == 0, isExit=k + 1 == len(flyer_route))
              for k, at in enumerate(flyer_route)]
    stone = [2.199903, 1.146958, -0.108858, 0.0, -1.148061, 2.20202, -0.0, 0.0, 0.112934,
             0.05888, 2.902636, 0.0, 1391.584717, -9252.480469, -1053.501587, 1.0]
    return {
        'level': LEVEL, 'version': 2,
        'look': {'sky': 'Stormy_Sky_INST'},
        'minimap': {'mode': 'cut'},
        'world': {'storyDialog': 'remove', 'intro': 'remove'},
        'doors': [{'id': 1, 'at': [718.14, -2646.92, -299.05], 'rotation': 80.0,
                   'cls': 'BP_Door_Forcefield_C', 'health': 0.0, 'slotSpacing': 0.0,
                   'scaleX': 10.0, 'scaleY': 25.0, 'scaleZ': 1.0}]
        + [{'id': 20 + k, 'at': [718.14, -2646.92 + 400.0 * (k + 1), -299.05], 'rotation': 80.0,
            'cls': 'BP_Door_Forcefield_C', 'health': 0.0, 'slotSpacing': 0.0,
            'scaleX': 10.0, 'scaleY': 25.0, 'scaleZ': 1.0} for k in range(EXTRA_FLYERS)],
        'spawners': [{'id': 2, 'at': [3196.0, -2451.92, 69.0], 'rotation': 0.0, 'kind': 'flyer',
                      'index': 2, 'maxSpawnPerRow': 0, 'door': 1, 'points': points}]
        + [{'id': 30 + k, 'at': [3196.0, -2451.92 + 400.0 * (k + 1), 69.0], 'rotation': 0.0,
            'kind': 'flyer', 'index': 3 + k, 'maxSpawnPerRow': 0, 'door': 20 + k, 'start': None,
            'flyerStart': 3} for k in range(EXTRA_FLYERS)],
        'removed': [452, 692, 1153],
        'removedFx': ['Audio|11', 'Lighting|45', 'VFX|4'],
        'edits': [{'id': 1203, 'mesh': 'Big_Stone_03_A', 'matrix': stone}],
        'added': ([{'name': 'Floor_RB_Trim_v1_4x4_C', 'kind': 'kit',
                    'matrix': matrix((-4200.0, -1500.0, 30.0))},
                   {'name': 'Floor_RB_Trim_v1_4x4_C', 'kind': 'kit',
                    'matrix': matrix((-4200.0, 1100.0, 30.0), mirror_y=True)},
                   {'name': 'LavaBurst', 'kind': 'fx', 'fx': 'LavaBurst',
                    'matrix': matrix((-3600.0, -900.0, 60.0))}]
                  + compound('column_a', (-3300.0, -1500.0, 30.0), 1)),
    }


def edit_spec(mod):
    return {'map': '%s_Map01' % mod, 'title': 'Fixture Edit', 'par': 600, 'war': False,
            'intoCampaign': 'CustomMaps', 'endless': True, 'bundle': True,
            'details': {'author': 'Rift Lord tests', 'version': '1', 'description': 'Fixture'},
            'waves': [{'id': 1, 'breakKind': 'wave', 'breakSeconds': 10, 'coin': 1000,
                       'actions': [], 'spawns': {'1': [row(11, 'OrcLight', 2)],
                                                 '2': [row(12, 'HellbatBaby', 1)]}}],
            'events': []}


#: SCRATCH's floor: corridor tiles along +y from the rift end, their top at Z.
Z = 1000.0
TILES = 6


def scratch_plan():
    """SCRATCH's plan, before the save cleans it."""
    from riftlord.mapeditor import crystal
    fab, terrain = fixturelibs.FAB_PACK, 'Terrain_%s' % fixturelibs.TERRAIN_LEVEL
    lib = os.path.join(WORK, 'fab', fab, 'catalogue.json')
    with open(lib) as fh:
        by_name = {m['name']: m for m in json.load(fh)['meshes']}
    with open(os.path.join(WORK, 'terrain', terrain, 'catalogue.json')) as fh:
        tile = next(m for m in json.load(fh)['meshes'] if m['name'] == 'Dirt_Ground_4x4')

    def fab_piece(entry, pack, m, grids=('G00',)):
        from riftlord.mapeditor import fablibrary
        return {'kind': 'fab', 'pack': pack, 'name': fablibrary.mesh_key(entry['package']),
                'object': entry['object_path'], 'source': entry['source'], 'role': entry['role'],
                'grids': list(grids), 'matrix': m}
    floors = [{'name': 'Floor_RB_Trim_v1_4x4_C', 'kind': 'kit', 'matrix': matrix((0.0, 1200.0 * k, Z))}
              for k in range(TILES)]
    wall, slab = by_name['FixtureWall'], by_name['FixtureSlab']
    group = {'name': 'Fixture Group', 'difficulty': 5, 'players': 0,
             'rifts': [{'spawner': 7, 'difficulty': 5, 'players': 0},
                       {'spawner': 8, 'difficulty': 5, 'players': 0}],
             'minions': [{'unit': 'OrcLight', 'count': 2, 'addCount': 0}],
             'triggers': [{'at': 75.0, 'wave': None, 'alive': 0.0, 'both': False, 'open': 1,
                           'launchAfter': 0.0, 'launchAlive': 0.0}]}

    def rift(sid, at):
        return {'id': sid, 'at': list(at), 'rotation': 0.0, 'kind': 'ground', 'index': 0,
                'maxSpawnPerRow': 0, 'door': None, 'start': 9, 'flyerStart': None,
                'unstable': True}
    return {
        'level': LEVEL, 'version': 2,
        'scratch': {'version': 1, 'base': LEVEL, 'name': 'Fixture Scratch'},
        'minimap': {'mode': 'regenerate', 'cutAtDoors': True},
        'volumes': [{'name': 'RiftLordVolume1', 'added': True, 'blocks': 'players',
                     'matrix': matrix((-600.0, 4200.0, Z + 200.0), scale=(2.0, 2.0, 2.0))}],
        'world': {'storyDialog': 'remove', 'intro': 'remove'},
        'doors': [{'id': 3, 'at': [600.0, 6000.0, Z + 260.0], 'rotation': 0.0,
                   'cls': 'BP_Door_Exterior_3W_C', 'health': 0.0, 'slotSpacing': 0.0,
                   'scaleX': 1.0, 'scaleY': 1.0, 'scaleZ': 1.0}],
        'spawners': [{'id': 1, 'at': [600.0, 6900.0, Z + 220.0], 'rotation': 0.0, 'kind': 'ground',
                      'index': 1, 'maxSpawnPerRow': 0, 'door': 3, 'start': 2, 'flyerStart': None},
                     rift(7, (300.0, 3000.0, Z + 220.0)), rift(8, (900.0, 3000.0, Z + 220.0))],
        'points': [point(2, (600.0, 6500.0, Z + 70.0), [4]),
                   point(4, (600.0, 3600.0, Z + 70.0), ['OMDPathPoint6']),
                   point(9, (600.0, 2600.0, Z + 70.0), ['OMDPathPoint6'])],
        'wiring': [{'name': 'BP_OMDRift_2', 'kind': 'rift', 'gone': False,
                    'at': [600.0, 300.0, Z + 410.0], 'rotation': 0.0},
                   {'name': 'OMDPathPoint6', 'kind': 'point', 'gone': False,
                    'at': [600.0, 450.0, Z + 45.0]},
                   {'name': 'PlayerStartDuringGOBreaks', 'kind': 'playerStart', 'gone': False,
                    'at': [400.0, 1800.0, Z + 60.0], 'rotation': 90.0},
                   {'name': 'PlayerStartDuringGOBreaks2', 'kind': 'playerStart', 'gone': False,
                    'at': [800.0, 1800.0, Z + 60.0], 'rotation': 90.0},
                   {'name': 'PlayerStartDuringWaves', 'kind': 'playerStart', 'gone': False,
                    'at': [600.0, 1500.0, Z + 60.0], 'rotation': 90.0}]
        + [{'name': n, 'kind': k, 'gone': True} for n, k in
           [('BP_OMDAISpawner_2', 'spawner'), ('Door01', 'door')]
           + [('OMDPathPoint%d' % i, 'point') for i in range(6)]],
        'added': floors + [
            fab_piece(wall, fab, matrix((600.0, -20.0, Z))),
            fab_piece(wall, fab, matrix((1220.0, 2400.0, Z), yaw=90.0, mirror_y=True)),
            fab_piece(slab, fab, matrix((1800.0, 600.0, Z - 40.0)), grids=('G00',)),
            fab_piece(tile, terrain, matrix((-1200.0, 1200.0, Z))),
            {'kind': crystal.KIND, 'name': crystal.CLASS,
             'matrix': matrix((600.0, 900.0, Z + 150.0)),
             crystal.KEY: {crystal.MAP_DIFFICULTY: 1.5, 'pointHealth': 250.0,
                           'bobHeight': 40.0, 'bobSeconds': 6.0, 'riftGap': 3.0,
                           'groups': [group]}}],
    }


def scratch_spec(mod, placement):
    return {'map': '%s_Map01' % mod, 'title': 'Fixture Scratch', 'par': 600, 'war': False,
            'intoCampaign': placement, 'endless': False, 'bundle': True,
            'details': {'author': 'Rift Lord tests', 'version': '1', 'description': 'Fixture'},
            'waves': [{'id': 1, 'breakKind': 'wave', 'breakSeconds': 10, 'coin': 1000,
                       'actions': [], 'spawns': {'1': [
                           row(11, 'OrcLight', 2),
                           row(12, fixturelibs.MINION_ROW, 2, guid=fixturelibs.MINION_GUID)]}}],
            'events': []}


def cleaned(plan):
    """`plan` as the Map Editor saves it (leveldata.save_plan, then load_plan)."""
    from riftlord.mapeditor import leveldata
    leveldata.save_plan(plan['level'], plan)
    return leveldata.load_plan(plan['level'])


def record(ident, title, plan, spec, placement, endless, scramble=None):
    mod, guids = IDENTITY[ident]
    rec = {'id': ident, 'created': STAMP, 'updated': STAMP, 'tools': ['onslaught', 'riftlord'],
           'kind': 'map', 'level': LEVEL, 'title': title, 'mod': mod, 'map': '%s_Map01' % mod,
           'guids': guids, 'details': {'author': 'Rift Lord tests', 'version': '1',
                                       'description': 'A test mission (tests/fixtures).'},
           'placement': placement, 'endless': endless, 'resetDoors': False,
           'plan': plan, 'spec': spec}
    if scramble:
        rec['scramble'] = scramble
        spec['scramble'] = scramble
    return rec


def campaign(missions, guid_hex):
    """Make CAMPAIGN_NAME in `missions` listing the mission `guid_hex`; returns its
    placement value."""
    from riftlord.missions import customcampaign
    got = customcampaign.place(bytes.fromhex(guid_hex),
                               {customcampaign.NEW: True, 'name': CAMPAIGN_NAME,
                                'description': 'Test campaign', 'hub': 'OldFriends'},
                               mods=missions)
    return customcampaign.CUSTOM + '%s:%s' % (os.path.basename(got['campaign_pak']),
                                              got['campaign_row'])


#: Where fixturelibs writes the libraries while the missions are made.
WORK = None


def main():
    global WORK
    WORK = tempfile.mkdtemp(prefix='rl-fixture-missions-')
    try:
        env = fixturelibs.environment(WORK)
        os.environ.update(env, RIFTLORD_PLANS=os.path.join(WORK, 'plans'))
        stage = os.path.join(WORK, 'missions')
        os.makedirs(os.path.join(stage, '.riftlord', 'records'))
        os.environ['OMD3_BUCKET'] = stage
        records = []
        mod, guids = IDENTITY[EDIT]
        records.append(record(EDIT, 'Fixture Edit', cleaned(edit_plan()), edit_spec(mod),
                              'CustomMaps', True))
        mod, guids = IDENTITY[SCRATCH]
        placement = campaign(stage, guids['%s_Mission01' % mod])
        records.append(record(SCRATCH, 'Fixture Scratch', cleaned(scratch_plan()),
                              scratch_spec(mod, placement), placement, False,
                              {'tiers': ['Tier_01-Start', 'Tier_30'],
                               'rows': {'Tier_01-Start': {'weight': 50.0}}}))
        for rec in records:
            with open(os.path.join(stage, '.riftlord', 'records', rec['id'] + '.json'), 'w') as fh:
                json.dump(rec, fh, indent=1, sort_keys=True)
        shutil.rmtree(MISSIONS, ignore_errors=True)
        shutil.copytree(stage, MISSIONS)
        for base, _dirs, names in os.walk(MISSIONS):
            for n in sorted(names):
                print(os.path.relpath(os.path.join(base, n), MISSIONS))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == '__main__':
    main()
