"""Offline checks on the floor under unstable rifts (riftlord/build/navigation/riftfloor.py):
the drop to the walkable floor, the refusal and warning text (Map Editor labels, a high
rift warned about, not refused), the solid-piece segment test, the Map Editor's height snap
on Scenario_02, the gate on the Crystal and Unstable Rift probes when their paks are built,
the mission build, the ground path point snap, and Acid Factory's (Scenario_16) rift route.

    python3 test_riftfloor.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402

from riftlord.build.navigation import riftfloor  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def square(z, half=200.0, cx=0.0, cy=0.0):
    return [(cx - half, cy - half, z), (cx + half, cy - half, z),
            (cx + half, cy + half, z), (cx - half, cy + half, z)]


# 1. the drop to the floor under a rift, and the highest floor below it
low, high = square(0.0), square(300.0)
got = riftfloor.floor_under([low, high], (0.0, 0.0, 380.0))
check('the highest floor below the rift is the one under it', got and got[0] == 1
      and abs(got[2] - 80.0) < 1e-6, got)
got = riftfloor.floor_under([low, high], (0.0, 0.0, 250.0))
check('a floor above the rift is not under it', got and got[0] == 0, got)
rows = riftfloor.check_rift([[low]], (0.0, 0.0, riftfloor.SHIPPED_OFFSET))
check('a rift at the shipped offset passes', rows[0][4], rows)

# 2. a rift over no floor is refused, a high one is a warning; both name the rift by its
#    Map Editor label, in plain words
JARGON = ('graph', 'polygon', 'uu limit', 'RiftLordSpawner')
plain = lambda lines: all(not any(j in t for j in JARGON) for t in lines)
rows = riftfloor.check_rift([[low], [low]], (0.0, 0.0, riftfloor.DROP_LIMIT + 50.0))
check('a rift too high is not refused', riftfloor.describe('Unstable Rift 7', rows) == []
      and all(r[4] for r in rows), rows)
text = riftfloor.high_notes('Unstable Rift 7', rows)
check('a rift too high is one warning with its height and the shipped highest',
      len(text) == 1 and text[0].startswith('Unstable Rift 7 stands %.0f uu above the floor'
                                            % (riftfloor.DROP_LIMIT + 50.0))
      and '(%.0f uu)' % riftfloor.SHIPPED_DROP_MAX[0] in text[0] and plain(text), text)
check('a rift at the shipped offset has no warning', riftfloor.high_notes(
    'Unstable Rift 7', riftfloor.check_rift([[low]], (0.0, 0.0, riftfloor.SHIPPED_OFFSET)))
      == [])
rows = riftfloor.check_rift([[low]], (900.0, 0.0, 100.0))
text = riftfloor.describe('Unstable Rift 8', rows)
check('a rift over no floor is refused', text == ['Unstable Rift 8 has no walkable floor '
                                                  'under it'], text)
rows = riftfloor.check_rift([[low], [square(0.0, cx=900.0)]], (0.0, 0.0, 90.0))
text = riftfloor.describe('Unstable Rift 8', rows)
check('a rift over floor of one agent graph only names the enemies without it',
      text == ['Unstable Rift 8 has no walkable floor under it for large enemies'], text)
sunk_at = (0.0, 0.0, -40.0)
rows = riftfloor.check_rift([[low]], sunk_at)
text = riftfloor.describe('Unstable Rift 9', rows, riftfloor.sunk_depth([low], sunk_at))
check('a rift under the floor is told to go up', text == [
    'Unstable Rift 9 is 40 uu under the walkable floor; raise it above the floor'], text)

# 3. the solid segment between rift and floor
tri = np.array([[[-100.0, -100.0, 50.0], [100.0, -100.0, 50.0], [0.0, 100.0, 50.0]]])
check('a piece between the rift and its floor is found',
      riftfloor._hits(tri, 0.0, 0.0, 100.0, riftfloor.FLOOR_CLEAR) == 50.0)
tri_floor = tri.copy()
tri_floor[:, :, 2] = 10.0
check('the floor surface itself is not a hit',
      riftfloor._hits(tri_floor, 0.0, 0.0, 100.0, riftfloor.FLOOR_CLEAR) is None)
check('a piece beside the segment is not a hit',
      riftfloor._hits(tri, 500.0, 0.0, 100.0, riftfloor.FLOOR_CLEAR) is None)

# 4. the Map Editor's snap on Scenario_02 (shipped graph): the rift Unstable Rift A used
snapped = riftfloor.snap('Scenario_02', [2150.0, 6950.0, 460.0])
check('the editor puts a rift the shipped offset over the floor',
      snapped['at'] and abs(snapped['at'][2] - snapped['floor'] - riftfloor.SHIPPED_OFFSET)
      < 0.11, snapped)
check('the editor leaves a rift over no floor where it was',
      riftfloor.snap('Scenario_02', [-90000.0, -90000.0, 0.0])['at'] is None)

# 5. the gate on the probes, when built
PROBES = os.path.expanduser('~/.cache/rift-lord/probes')
for title in ('Crystal_Probe_A', 'Unstable_Rift_A'):
    pak = os.path.join(PROBES, title, '_out', '900_%s_P.pak' % title)
    rec = os.path.join(PROBES, title, '.riftlord', 'records', '900_%s_P.json' % title)
    if not (os.path.exists(pak) and os.path.exists(rec)):
        continue
    from riftlord.core import paksource
    record = json.load(open(rec))
    rows = riftfloor.gates(paksource.entries(pak), record.get('plan'),
                           record.get('level') or record['plan'].get('level'))
    check('%s: the rift-floor gates pass' % title, rows and all(ok for _n, ok, _d in rows),
          rows)
    files = paksource.entries(pak)
    umap = next(n for n in files if n.endswith('_Map01.umap'))
    from riftlord.build.navigation import navsurfextend
    graphs = riftfloor.polygons(navsurfextend._surface_of(files[umap],
                                                          files[umap[:-5] + '.uexp']))
    for name, index, at in riftfloor.placed(files[umap], files[umap[:-5] + '.uexp']):
        raised = (at[0], at[1], at[2] + 200.0)
        rows = riftfloor.check_rift(graphs, raised)
        label = riftfloor.rift_label(index)
        text = riftfloor.high_notes(label, rows)
        check('%s: %s raised 200 uu is a warning, not a refusal' % (title, label),
              riftfloor.describe(label, rows) == [] and text and text[0].startswith(label),
              text)

# 6. the mission build (bucket._bake): a written level whose rift stands too high builds
#    with a console warning naming the rift; one over no floor is refused; the real one
#    passes
import shutil  # noqa: E402
import struct  # noqa: E402
import tempfile  # noqa: E402

title = 'Unstable_Rift_A'
built = os.path.join(PROBES, title, '900_%s_P' % title)
rec_path = os.path.join(PROBES, title, '.riftlord', 'records', '900_%s_P.json' % title)
if os.path.isdir(built) and os.path.exists(rec_path):
    record = json.load(open(rec_path))
    check('build check: the written level passes',
          riftfloor.build_problems(built, record['plan'], record['level']) == [])
    scratch = tempfile.mkdtemp(prefix='riftfloor-test-')
    raised = os.path.join(scratch, 'raised')
    shutil.copytree(built, raised)
    from riftlord.core import paksource
    uexp = next(n for n in paksource.names(raised) if n.endswith('_Map01.uexp'))
    upath = os.path.join(raised, *uexp.split('/'))
    data = bytearray(open(upath, 'rb').read())
    at = next(sp['at'] for sp in record['plan']['spawners'] if sp.get('unstable'))
    old = struct.pack('<3f', *at)
    check('build check: the rift location is in the level once', data.count(old) == 1,
          data.count(old))
    data[data.index(old):data.index(old) + 12] = struct.pack('<3f', at[0], at[1], at[2] + 200.0)
    open(upath, 'wb').write(bytes(data))
    bad, high = riftfloor.build_report(raised, record['plan'], record['level'])
    check('build check: a rift 200 uu up is a warning naming it, not a problem',
          bad == [] and len(high) == 1 and high[0].startswith('Unstable Rift 3 stands'),
          (bad, high))
    away = os.path.join(scratch, 'away')
    shutil.copytree(built, away)
    gone = bytearray(open(os.path.join(built, *uexp.split('/')), 'rb').read())
    gone[gone.index(old):gone.index(old) + 12] = struct.pack('<3f', -90000.0, -90000.0, at[2])
    open(os.path.join(away, *uexp.split('/')), 'wb').write(bytes(gone))
    bad = riftfloor.build_problems(away, record['plan'], record['level'])
    check('build check: a rift over no floor is refused, by its label',
          bad == ['Unstable Rift 3 has no walkable floor under it'], bad)

    os.environ['OMD3_BUCKET'] = os.path.join(scratch, 'bucket')
    from riftlord.missions import bucket
    from riftlord.build import emit
    os.makedirs(os.path.join(bucket.root(), '.riftlord', 'records'), exist_ok=True)
    ident = record['id']
    rec = dict(record, spec=dict(record['spec'], events=record['plan'].get('events')))
    bucket._write_json(bucket._record_path(ident), rec)
    real_write = emit.write

    def fake_write(level, out=None, mod=None, **_kw):
        shutil.copytree(raised, out, dirs_exist_ok=True)
        return {'written': True, 'mod': mod, 'applied': {}}
    from riftlord.core import console
    warned = []
    real_warn, real_refresh = console.warn, bucket.refresh

    def stop():
        raise SystemExit('past the level checks')
    emit.write, bucket.refresh = fake_write, stop
    console.warn = lambda text, *_a, **_k: warned.append(text)
    try:
        bucket._bake(ident, None)
        why = 'built'
    except SystemExit as exc:
        why = str(exc)
    finally:
        emit.write, bucket.refresh, console.warn = real_write, real_refresh, real_warn
    check('mission build goes on past the raised rift, warning with its label',
          why == 'past the level checks' and
          any('Unstable Rift 3 stands' in w for w in warned), (why, warned))
    raised = away
    emit.write = fake_write
    try:
        bucket._bake(ident, None)
        why = 'built'
    except SystemExit as exc:
        why = str(exc)
    finally:
        emit.write = real_write
    check('mission build fails on a rift over no floor, naming it',
          'could not be built: Unstable Rift 3 has no walkable floor under it' in why, why)
    shutil.rmtree(scratch, ignore_errors=True)

# 7. the gate rows: a high rift is a note row that passes
from riftlord.build.navigation import navsurfextend  # noqa: E402
check('the height row is named as a note', 'note' in riftfloor.HEIGHT_GATE)

# 8. a ground path point snaps onto the floor a build puts it on (navsurfextend.on_surface),
#    POINT_ABOVE_FLOOR over it; one over a gap, beyond on_surface's reach, is left alone.
#    Acid Factory (Scenario_16): Point 31, Spawner 30's start, at (-2749, -4464, 52.73), ~50
#    uu over floor at z 2.7
P31 = [-2749.0, -4464.0, 52.73]
snapped = riftfloor.snap_point('Scenario_16', P31)
check('Point 31 snaps POINT_ABOVE_FLOOR over its floor',
      snapped['at'] and abs(snapped['at'][2] - snapped['floor']
                            - navsurfextend.POINT_ABOVE_FLOOR) < 0.11
      and abs(snapped['floor'] - 2.7) < 1.0, snapped)
high = [P31[0], P31[1], 2.7 + navsurfextend.SURFACE_ABOVE + 50.0]
check('a point beyond the build\'s on-floor reach is left where it is',
      riftfloor.snap_point('Scenario_16', high)['at'] is None)
check('a point over no floor is left where it is',
      riftfloor.snap_point('Scenario_16', [-90000.0, -90000.0, 0.0])['at'] is None)

# 9. Acid Factory's unstable-rift route builds: Spawner 30 (Unstable Rift 7) at z 169.96 and
#    its start Point 31 stand over the shipped walkable floor, so the build adds no floor
#    for them; the same route with Point 31 raised over the reach still gets floor
from riftlord.build.navigation import navsurf  # noqa: E402
from riftlord.mapeditor import planactors  # noqa: E402
acid = {'spawners': [{'id': 30, 'at': [-2951.49, -4982.91, 169.96], 'kind': 'ground',
                      'index': 7, 'door': None, 'start': 31, 'flyerStart': None,
                      'unstable': True}],
        'points': [{'id': 31, 'at': P31, 'next': [{'to': 10, 'weight': 1.0}]},
                   {'id': 10, 'at': [-2151.0, -2923.0, 42.22],
                    'next': [{'to': 'OMDPathPoint8', 'weight': 1.0}]}]}
level16 = planactors.level_actors('Scenario_16')
chains = planactors.ground_chains(acid, level16)
buf16 = navsurf.buffer('Scenario_16')
check('Acid Factory: Spawner 30\'s route is on the walkable floor',
      chains and navsurfextend.needs_extending(buf16, chains) == [], chains)
rows = riftfloor.check_rift(riftfloor.polygons(buf16), acid['spawners'][0]['at'])
check('Acid Factory: Unstable Rift 7 is a warning, not a refusal',
      riftfloor.describe('Unstable Rift 7', rows) == []
      and riftfloor.high_notes('Unstable Rift 7', rows), rows)
lifted = {**acid, 'points': [dict(acid['points'][0], at=high)] + acid['points'][1:]}
off = navsurfextend.needs_extending(buf16, planactors.ground_chains(lifted, level16))
check('Acid Factory: Point 31 over the reach is off the floor and gets floor',
      [list(p) for p in off] == [high], off)

# 10. the snaps on the floor the open plan leaves (riftfloor.plan_floor), on North Wing
#     (Scenario_01): a kit floor raised over the level's own floor, and a removed floor piece
#     (367, a Floor_Squarebrick_1x4 whose removal drops graph polygons, navsurfcarve.doomed)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'fixtures'))
from makemissions import matrix  # noqa: E402
from riftlord.mapeditor import scratch as scratchmod  # noqa: E402

RAISED = 130.0
kit = {'name': 'Floor_RB_Trim_v1_4x4_C', 'kind': 'kit', 'matrix': matrix((450.0, 450.0, RAISED))}
kit_top = scratchmod.floor_boxes({'added': [kit]})[0][1][2]
ON_KIT = [1050.0, 1050.0, kit_top + 20.0]
level_floor = riftfloor.snap_point('Scenario_01', [ON_KIT[0], ON_KIT[1], 20.0])
check('North Wing has its own floor under the raised kit floor',
      level_floor['at'] and kit_top - level_floor['floor'] > 100.0, (level_floor, kit_top))
check('without the plan the level\'s floor is taken there (the old snap)',
      riftfloor.snap_point('Scenario_01', ON_KIT)['floor'] == level_floor['floor'])
NEW_LEVEL = {'version': 1, 'base': 'Scenario_01', 'name': 'Floor Snap'}
fresh = {'level': 'Scenario_01', 'scratch': NEW_LEVEL, 'added': [kit]}
for label, plan in (('scratch', fresh), ('edited', {'level': 'Scenario_01', 'added': [kit]})):
    got = riftfloor.snap_point('Scenario_01', ON_KIT, plan)
    check('%s level: a point snaps POINT_ABOVE_FLOOR over the added floor' % label,
          got['at'] and abs(got['floor'] - kit_top) < 0.11 and
          abs(got['at'][2] - kit_top - navsurfextend.POINT_ABOVE_FLOOR) < 0.11, got)
    got = riftfloor.snap_spawner('Scenario_01', ON_KIT, plan)
    check('%s level: a ground spawner snaps SPAWNER_ABOVE_FLOOR over it' % label,
          got['at'] and abs(got['at'][2] - kit_top - navsurfextend.SPAWNER_ABOVE_FLOOR) < 0.11,
          got)
    got = riftfloor.snap('Scenario_01', ON_KIT, plan=plan)
    check('%s level: an unstable rift snaps SHIPPED_OFFSET over it' % label,
          got['at'] and abs(got['at'][2] - kit_top - riftfloor.SHIPPED_OFFSET) < 0.11, got)
lost = {'kind': 'fab', 'pack': 'RiftLordNoSuchLibrary', 'name': 'fab/RiftLordNoSuchLibrary/x',
        'object': '/Game/Mods/RiftLordNoSuchLibrary/x', 'role': 'floor', 'matrix': matrix(ON_KIT)}
got = riftfloor.snap_point('Scenario_01', ON_KIT, dict(fresh, added=[kit, lost]))
check('a Fab piece whose library is not installed is left out; the rest still snaps',
      got['at'] and abs(got['floor'] - kit_top) < 0.11, got)
check('scratch level: the host\'s floor is not floor',
      riftfloor.snap_point('Scenario_01', [ON_KIT[0], ON_KIT[1], 20.0], fresh)['at'] is None)
check('scratch level with no floor placed: left as placed',
      riftfloor.snap_point('Scenario_01', ON_KIT, {'scratch': NEW_LEVEL, 'added': []})['at']
      is None)
ON_367 = [-4054.0, 2550.0, 32.0]
before = riftfloor.snap_point('Scenario_01', ON_367)
edited = {'level': 'Scenario_01', 'removed': [367], 'added': [kit]}
check('edited level: piece 367 holds floor up in the level\'s graph', before['at'], before)
check('edited level: its floor removed, a point there is left as placed',
      riftfloor.snap_point('Scenario_01', ON_367, edited)['at'] is None and
      riftfloor.snap('Scenario_01', ON_367, plan=edited)['at'] is None)
check('edited level: the added floor still snaps beside the removal',
      abs(riftfloor.snap_point('Scenario_01', ON_KIT, edited)['floor'] - kit_top) < 0.11)
ELSEWHERE = [-1950.0, 450.0, 30.0]
heights = lambda got: (got['at'], got['floor'])
check('edited level: untouched floor snaps as the level\'s graph does',
      heights(riftfloor.snap_point('Scenario_01', ELSEWHERE, edited)) ==
      heights(riftfloor.snap_point('Scenario_01', ELSEWHERE)))
plain_plan = {'level': 'Scenario_01', 'added': [], 'removed': [], 'edits': []}
check('a plan that changes no floor uses the level\'s graph itself',
      riftfloor._floor('Scenario_01', plain_plan) is riftfloor._level_graph('Scenario_01'))
from riftlord.build.navigation import navsurfcarve  # noqa: E402
from riftlord.formats.omd3 import meshcat  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402
no_piece = max(p['id'] for p in leveldata.level('Scenario_01')['pieces']) + 1
check('a removal that holds up no floor changes nothing',
      riftfloor._floor('Scenario_01', dict(plain_plan, removed=[no_piece]))
      is riftfloor._level_graph('Scenario_01'))
MOVED_TO = (-3150.0, 2400.0, RAISED)
moved = {'level': 'Scenario_01', 'edits': [{'id': 367, 'matrix': matrix(MOVED_TO)}]}
mesh367 = next(p['mesh'] for p in leveldata.level('Scenario_01')['pieces'] if p['id'] == 367)
top367 = scratchmod.walk_box({'kind': 'mesh', 'name': mesh367, 'matrix': matrix(MOVED_TO)},
                            navsurfcarve._box(meshcat.load()[mesh367], matrix(MOVED_TO)))[1][2]
got = riftfloor.snap_point('Scenario_01', [MOVED_TO[0], MOVED_TO[1], top367 + 20.0], moved)
check('edited level: a moved floor piece is floor at its new place',
      got['at'] and abs(got['floor'] - top367) < 0.11, (got, top367))

# 11. an edited shipped level's snap never lands on a piece that is not floor: a kit
#     column, a column mesh and a prop cube (the UI case's 1M_Cube) standing on the level's
#     floor under the click leave every snap where the level's graph puts it
SPOT = (ELSEWHERE[0], ELSEWHERE[1], 0.0)
props = {'level': 'Scenario_01', 'added': [
    {'kind': 'kit', 'name': 'ColumnBlock_1_2_Dry_RB_C', 'matrix': matrix(SPOT)},
    {'kind': 'mesh', 'name': 'ColumnTileA_CastStone_Base', 'matrix': matrix(SPOT)},
    {'kind': 'mesh', 'name': '1M_Cube', 'matrix': matrix(SPOT)}]}
CLICK = [SPOT[0], SPOT[1], 500.0]
for label, fn in (('point', riftfloor.snap_point), ('spawner', riftfloor.snap_spawner),
                  ('rift', lambda lv, at, plan=None: riftfloor.snap(lv, at, plan=plan))):
    for at in (CLICK, ELSEWHERE):
        check('edited level: a %s at z %.0f over a column, a column mesh and a prop snaps as '
              'the level\'s graph does' % (label, at[2]),
              heights(fn('Scenario_01', at, props)) == heights(fn('Scenario_01', at)),
              (fn('Scenario_01', at, props), fn('Scenario_01', at)))
check('a column, a column mesh and a prop add no floor',
      riftfloor._floor('Scenario_01', props) is riftfloor._level_graph('Scenario_01'))

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d checks, %d failed' % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
