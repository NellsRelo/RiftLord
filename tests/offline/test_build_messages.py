"""Offline checks on how a build speaks (riftlord/core/buildmessage.py), sorted three ways:

A. what the build fixes by itself says what it did (a route point moved onto the floor
   within its Reach Radius, a corridor joined at the next floor edge, an added spawner's
   taken index renumbered and stored back into the plan);
B. what needs a design decision names the piece, point or spawner as the Map Editor does,
   with its position, says what to change, and carries Checks rows (target and position)
   the Map Editor's Checks list selects and frames;
C. Rift Lord's own faults read "Rift Lord could not ...", never as the designer's task,
   with a fallback where one is safe (an unlinkable added piece left out, the shipped floor
   kept, the shipped light maps kept) and a saved report where none is.

No message tells anyone to send the map anywhere. Each failure is triggered on a small
fixture or a stub; section 9 runs the C checks on pinned real builds, so a fault in a
real build fails here. Section 10 runs web/mapeditor/test/checks_check.mjs.

    python3 tests/offline/test_build_messages.py
"""
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests', 'fixtures'))

from riftlord.core import buildmessage  # noqa: E402

checks = []
SCRATCH = tempfile.mkdtemp(prefix='build-messages-')


def check(name, ok, detail=''):
    checks.append(bool(ok))
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


#: export names and numbers a message must not show
EXPORTS = re.compile(r'RiftLord(Spawner|Point|Rift|Door|Kit|Mesh)|export \d|\bexports?\b')
#: what a B message says to do
ACTIONS = ('Move', 'move', 'Put', 'put', 'Give', 'give', 'Remove', 'remove', 'Place', 'place',
           'Raise', 'Lower', 'Pick', 'pick', 'Turn', 'turn', 'Install', 'Update', 'Open it in',
           'point that branch')


def plain(name, text, names=(), kind='B'):
    """Check one message: it names each of `names`, holds no export name, jargon or
    "send the map"; a B message says what to do, a C one is Rift Lord's."""
    text = str(text)
    bad = buildmessage.jargon(text)
    check('%s: names %s' % (name, ', '.join(names) or 'nothing it need not'),
          all(n in text for n in names), text)
    check('%s: no export names, jargon or sending' % name,
          not EXPORTS.search(text) and not bad and not buildmessage.SENDS.search(text),
          (bad, text))
    if kind == 'B':
        check('%s: says what to change' % name, any(a in text for a in ACTIONS), text)
    elif kind == 'C':
        check('%s: is Rift Lord\'s, not the designer\'s task' % name,
              text.startswith('Rift Lord could not') or ' Rift Lord could not ' in text, text)


def raises(fn):
    try:
        fn()
    except (Exception, SystemExit) as exc:          # noqa: BLE001
        return exc
    return None


# 1. a ground route the build cannot join (navsurfextend): B, named and placed
from riftlord.build.navigation import navsurfextend as nx  # noqa: E402

SQUARE = [(0.0, 0.0, 0.0), (1000.0, 0.0, 0.0), (1000.0, 1000.0, 0.0), (0.0, 1000.0, 0.0)]
SMALL = [(0.0, 0.0, 0.0), (200.0, 0.0, 0.0), (200.0, 200.0, 0.0), (0.0, 200.0, 0.0)]


def join(run, before, after, names=None, floor=SQUARE, fixed=None):
    nx._NAMES.clear()
    nx._TARGETS.clear()
    for at, (label, target) in (names or {}).items():
        nx._NAMES.setdefault(nx._key(at), []).append((at[2], label))
        nx._TARGETS.setdefault(nx._key(at), []).append((at[2], target))
    try:
        return raises(lambda: nx.join_run([floor], [[0, 0, 0, 0]], run, before, after, 0,
                                          set(), fixed))
    finally:
        nx._NAMES.clear()
        nx._TARGETS.clear()


far = (9000.0, 500.0, 0.0)
exc = join([far], (500.0, 500.0, 0.0), None, {far: ('Path Point 4', {'kind': 'point', 'id': 4})})
check('a point far from any floor is a Problem with its detail',
      isinstance(exc, buildmessage.Problem) and exc.detail, exc)
plain('off the floor', exc, ['Path Point 4 at (9000, 500, 0)', 'not on floor enemies can '
                             'walk', 'within 3000 units', 'put a floor piece under it'])
check('it carries a Checks row on the point, at its position',
      exc.checks == [buildmessage.check(exc.plain, {'kind': 'point', 'id': 4}, far)],
      getattr(exc, 'checks', None))
exc = join([(210.0, 100.0, 350.0)], (100.0, 100.0, 0.0), None, floor=SMALL)
plain('too high over every floor edge', exc, ['The route point at (210, 100, 350)',
                                              'too far above'])
exc = raises(lambda: nx.corridor([SQUARE], [[0, 0, 0, 0]],
                                 [(1300.0, 500.0, 0.0), (1500.0, 500.0, 800.0)],
                                 (500.0, 500.0, 0.0), None, 0, set()))
plain('a steep leg', exc, ['the route point at (1300, 500, 0)', 'stairs or a ramp'])
exc = raises(lambda: nx.corridor([SQUARE], [[0, 0, 0, 0]],
                                 [(1300.0, 500.0, 0.0), (2000.0, 500.0, 0.0),
                                  (1300.0, 520.0, 0.0)], (500.0, 500.0, 0.0), None, 0, set()))
plain('a sharp turn', exc, ['turns too sharply at the route point at (2000, 500, 0)'])

# 2. A: a corridor that folds at its nearest edge joins at the next one, and says so
FOLD = [(1340.0, -375.0, 0.0), (1040.0, -1175.0, 0.0)]
first = raises(lambda: nx.corridor([SQUARE], [[0, 0, 0, 0]], FOLD, None, (300.0, 300.0, 0.0),
                                   0, set()))
check('the nearest edge folds the corridor (the case being fixed)',
      isinstance(first, nx.BadJoin), first)
fixed = []
got = join(FOLD, None, (300.0, 300.0, 0.0),
           {FOLD[-1]: ('Path Point 7', {'kind': 'point', 'id': 7})}, fixed=fixed)
check('the next edge serves: no failure', got is None, got)
check('the fix is one line naming the point',
      len(fixed) == 1 and fixed[0].startswith('Joined the ground route at Path Point 7 at '
                                              '(1040, -1175, 0) to the next floor edge'), fixed)
plain('the fold fix', fixed[0] if fixed else '', kind='A')
#: with no other edge facing the point, the fold's own Problem
exc = join([(1100.0, 0.0, 0.0), (1900.0, 0.0, 0.0)], None, (500.0, 500.0, 0.0),
           {(1900.0, 0.0, 0.0): ('Path Point 8', {'kind': 'point', 'id': 8})})
plain('a route that bends back at every edge', exc,
      ['Path Point 8 at (1900, 0, 0)', 'bends back on itself', 'open floor in front of it'])

# 3. A: a route point off the floor moves onto the nearest floor inside its Reach Radius
got = nx.nearest_floor([SQUARE], (1100.0, 500.0, 10.0), 300.0)
check('a point 100 units past the floor edge lands on the floor, the agent radius in',
      got and abs(got[0][0] - (1000.0 - nx.AGENT_RADIUS)) < 1e-6 and abs(got[0][1] - 500.0) < 1
      and abs(got[0][2] - nx.POINT_ABOVE_FLOOR) < 1e-6 and got[1] < 300.0, got)
check('one farther than its Reach Radius is left alone',
      nx.nearest_floor([SQUARE], (1400.0, 500.0, 10.0), 300.0) is None)
check('the move is measured in 3D: a point high over the edge is too far',
      nx.nearest_floor([SQUARE], (1050.0, 500.0, 400.0), 300.0) is None)
from riftlord.build import emit  # noqa: E402
check('snap_point moves only a plan path point the Problem names',
      emit.snap_point(buildmessage.Problem('x', target={'name': 'OMDPathPoint1'}), {}, b'', b'',
                      'L', set()) is None and
      emit.snap_point(buildmessage.Problem('x'), {}, b'', b'', 'L', set()) is None)

# 4. C: the floor the build adds or changes failing its own checks is Rift Lord's
saved = {k: getattr(nx, k) for k in ('_surface_of', 'needs_extending', '_bridgeable',
                                      'plan_corridors', 'check_buffer')}
saved_gen = nx.navsurfgen.buffer_per_agent
try:
    nx._surface_of = lambda ua, ux: b''
    nx.needs_extending = lambda buf, chains: [(1.0, 2.0, 3.0)]
    nx._bridgeable = lambda buf, chains: False
    nx.plan_corridors = lambda buf, chains: ([], [], [{'bbox': [0] * 6}])
    nx.navsurfgen.buffer_per_agent = lambda per, bbox: b'x'
    nx.check_buffer = lambda buf, chains: ['graph 1 does not parse',
                                           '2 edge id(s) quoted an odd number of times']
    exc = raises(lambda: nx._extend(b'', b'', [[(0, 0, 0)]]))
finally:
    for k, v in saved.items():
        setattr(nx, k, v)
    nx.navsurfgen.buffer_per_agent = saved_gen
check('the extended floor failing its checks is a Fault', isinstance(exc, buildmessage.Fault),
      type(exc))
plain('the extended floor failing its checks', exc, kind='C')
check('the checks stay in the detail', 'edge id' in getattr(exc, 'detail', ''), exc)

from riftlord.build.navigation import navsurfadd  # noqa: E402
from riftlord.build.navigation import navsurfcarve as nc  # noqa: E402

saved = {k: getattr(nc, k) for k in ('added_floor', 'check', 'broken_legs')}
saved_x, saved_add = nx._surface_of, (navsurfadd.ENABLED, navsurfadd.add_buffer,
                                      navsurfadd.check)
saved_on = nc.ENABLED


def carve(add_bad=(), graph_bad=(), broken=()):
    nc.ENABLED = False
    navsurfadd.ENABLED = True
    nx._surface_of = lambda ua, ux: b'shipped'
    nc.added_floor = lambda plan: [('Floor_4x4_A', ((0.0, 0.0, 0.0), (400.0, 400.0, 20.0)))]
    navsurfadd.add_buffer = lambda buf, boxes: (b'changed', [], {})
    navsurfadd.check = lambda before, after: list(add_bad)
    nc.check = lambda buf: list(graph_bad)
    nc.broken_legs = lambda before, after, chains: (list(broken), [])
    return raises(lambda: nc.carve_level(b'', b'', 'Scenario_01', {}, []))


try:
    exc = carve(add_bad=['agent 0: record 12 vertex 3 tag changed'])
    plain('an added floor piece breaking the floor', exc,
          ['Floor_4x4_A at (200, 200, 10)', 'meets the floor edge to edge'])
    exc = carve(graph_bad=['graph 0 does not parse'])
    check('the changed floor failing its checks is a Fault',
          isinstance(exc, buildmessage.Fault), type(exc))
    plain('the changed floor failing its checks', exc, kind='C')
    exc = carve(broken=[((10.0, 20.0, 0.0), (30.0, 40.0, 0.0))])
    plain('removed floor cutting a route', exc, ['(10, 20, 0) and (30, 40, 0)',
                                                 'Put the floor back'])
finally:
    for k, v in saved.items():
        setattr(nc, k, v)
    nx._surface_of = saved_x
    navsurfadd.ENABLED, navsurfadd.add_buffer, navsurfadd.check = saved_add
    nc.ENABLED = saved_on

# 5. how a build turns a step's failure into its message, and the report a fault saves
got = buildmessage.refusal(ValueError('regenerated graph fails its checks: checksum does '
                                      'not verify'), 'build the floor enemies walk on')
check('a technical step failure is a Fault', isinstance(got, buildmessage.Fault), type(got))
plain('a technical step failure', got, ['build the floor enemies walk on'], kind='C')
check('its detail keeps the technical text', 'checksum' in got.detail, got.detail)
problem = buildmessage.Problem('Path Point 3 at (1, 2, 3) is ...', 'x',
                               target={'kind': 'point', 'id': 3}, at=(1, 2, 3))
got = buildmessage.refusal(problem, 's')
check('a Problem keeps its words and Checks rows', isinstance(got, buildmessage.Refusal)
      and str(got).startswith('Path Point 3') and got.detail == 'x' and got.checks, got)
reports = os.path.join(SCRATCH, 'reports')
try:
    {}['MapBuildDataId']
except KeyError as err:
    shown, detail, rows = buildmessage.explain(err, 'Lightmap', reports, 'Some Map')
plain('an unexpected error', shown, ['Rift Lord could not bake the light maps'], kind='C')
path = re.search(r'saved as (.+)\.$', shown)
check('a fault\'s report is saved, with the traceback',
      path and os.path.isfile(path.group(1))
      and 'Traceback' in open(path.group(1)).read(), shown)
from riftlord.build.navigation import navpatch  # noqa: E402
exc = raises(lambda: navpatch.open_pak('/nonexistent/x.pak'))
shown, detail, _r = buildmessage.explain(exc, 'Level')
plain('a technical SystemExit from the build', shown, ['Rift Lord could not write the level'],
      kind='C')
check('its text is kept as detail', 'no such file' in detail, detail)
exc = raises(lambda: (_ for _ in ()).throw(SystemExit('Enter a name.')))
check('a SystemExit raised outside the build passes as written',
      buildmessage.explain(exc) == ('Enter a name.', '', []), buildmessage.explain(exc))

# 6. the path checks on a built pak (emit.path_checks), on a stubbed level: B rows name and
#    place the actor and carry Checks rows; Rift Lord's own failures are faults
from riftlord.build.navigation import navinfo, navsurf  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402
from riftlord.onslaught import onslaught  # noqa: E402

PLAN = {'spawners': [{'id': 7, 'index': 5, 'at': [5000.0, 0.0, 99.0], 'start': 3}],
        'points': [{'id': 3, 'at': [5200.0, 0.0, 10.0], 'next': []}]}
REPORT = {
    'points': {'RiftLordPoint3': {'index': 20, 'location': (5200.0, 0.0, 10.0), 'next': []},
               'OMDPathPoint1': {'index': 1, 'exit': True, 'location': (0.0, 0.0, 10.0),
                                 'next': []}},
    'spawners': [{'export': 'RiftLordSpawner0', 'index': 5, 'location': (5000.0, 0.0, 99.0),
                  'start': 'Map.RiftLordPoint3', 'flyer': None},
                 {'export': 'MobSpawnerDoor1', 'index': 5, 'location': (100.0, 0.0, 99.0),
                  'start': 'Map.OMDPathPoint1', 'flyer': None}]}
INFO = {'exports': [{'name': 'Path', 'class': 'OMDAIPath'}], 'names': []}
stubs = [(navpatch, 'open_pak', lambda *a, **k: None),
         (navpatch, 'close_pak', lambda *a, **k: None),
         (onslaught, 'map_report', lambda name: REPORT),
         (onslaught, 'load_map', lambda name: (INFO, b'', None)),
         (onslaught, 'read_props', lambda info, uexp, hdr, e: [
             ('PathPoints', 'ArrayProperty', struct.pack('<i', 2))]),
         (leveldata, 'level', lambda name: {'rifts': [{'name': 'Rift', 'loc': (0.0, 0.0, 0.0)}]}),
         (navinfo, 'omd3_map', lambda name, scratch: {'min': (-10000.0,) * 3,
                                                      'max': (10000.0,) * 3}),
         (navsurf, 'buffer', lambda name: None)]
kept = [(m, k, getattr(m, k)) for m, k, _v in stubs]
try:
    for m, k, v in stubs:
        setattr(m, k, v)
    rows = emit.path_checks('fake.pak', 'Map', PLAN)
finally:
    for m, k, v in kept:
        setattr(m, k, v)
failed = {r['name']: r for r in rows if not r['ok']}
row = failed.get('every spawner has a unique indexInLevel')
check('a shared index in level after the build renumbers is a fault (C)',
      row and row.get('fault'), sorted(failed))
if row:
    plain('a shared index in level', row['plain'], ['Spawner 7 at (5000, 0, 99)',
                                                    'MobSpawnerDoor1 at (100, 0, 99)'], kind='C')
row = failed.get('added GROUND actors sit inside the navgraph')
check('a route off the walkable floor fails (B)', row and not row.get('fault'), sorted(failed))
if row:
    plain('a route off the floor', row['plain'],
          ['Path Point 3 at (5200, 0, 10)', 'Spawner 7 at (5000, 0, 99)',
           'not on floor enemies can walk'])
    check('its Checks rows target the point and the spawner, placed',
          sorted((str(c.get('target')), tuple(c.get('at') or ())) for c in row['checks']) ==
          sorted([(str({'kind': 'point', 'id': 3}), (5200.0, 0.0, 10.0)),
                  (str({'kind': 'spawner', 'id': 7}), (5000.0, 0.0, 99.0))]), row['checks'])
row = failed.get('spawner 5 start route ends at an exit')
check('a route that never reaches a rift fails (B)', row and not row.get('fault'),
      sorted(failed))
if row:
    plain('a route with no rift', row['plain'], ['Spawner 7 at (5000, 0, 99)', 'never reaches a '
                                                 'rift', 'branch to a rift'])
check('every failed path check has a plain message, a fault none of the designer\'s',
      all(r.get('plain') for r in failed.values()) and
      all(r['plain'].startswith('Rift Lord could not') for r in failed.values()
          if r.get('fault')), [n for n, r in failed.items() if not r.get('plain')])

# 7. A: an added spawner's taken index is renumbered, said, and kept in the plan; the
#    editor's Checks list says so before the build; planactors.check no longer refuses it
from riftlord.mapeditor import checks as editorchecks  # noqa: E402
from riftlord.mapeditor import planactors  # noqa: E402
from riftlord.build import apply as applier  # noqa: E402
LEVEL = 'Scenario_02'
level = planactors.level_actors(LEVEL)
taken = sorted(applier.used_spawner_indices(LEVEL))
clash_plan = {'spawners': [{'id': 4, 'index': taken[0], 'kind': 'ground',
                            'at': [-2956.0, -4870.0, 407.0], 'start': 1}],
              'points': [{'id': 1, 'at': [-2000.0, -4800.0, 308.0],
                          'next': [{'to': 'rift', 'weight': 1.0}]}]}
errors, _w = planactors.check(clash_plan, LEVEL, level)
check('an added spawner on a taken index is not a refusal',
      not any('index in level' in e for e in errors), errors)
fixes = editorchecks.index_fixes(LEVEL, clash_plan)
check('the build gives it the lowest free index', len(fixes) == 1 and fixes[0][1] == taken[0]
      and fixes[0][2] not in taken, fixes)
got = editorchecks.for_plan(LEVEL, clash_plan)['checks']
fixed_rows = [r for r in got if r['level'] == 'fixed']
check('the editor lists it as fixed by the build, on the spawner',
      len(fixed_rows) == 1 and fixed_rows[0]['target'] == {'kind': 'spawner', 'id': 4}
      and fixed_rows[0]['at'] == [-2956.0, -4870.0, 407.0], fixed_rows)
from riftlord.missions import bucket  # noqa: E402
rec = {'plan': {'spawners': [{'id': 4, 'index': 2}], 'points': [{'id': 1, 'at': [0, 0, 0]}]}}
bucket.take_plan_fixes(rec, [{'kind': 'spawner', 'id': 4, 'index': 9},
                             {'kind': 'point', 'id': 1, 'at': [5.0, 6.0, 7.0]}])
check('fixes are stored back into the record\'s plan',
      rec['plan']['spawners'][0]['index'] == 9 and rec['plan']['points'][0]['at'] == [5.0, 6.0, 7.0],
      rec)
bad_plan = {'spawners': [], 'points': [{'id': 6, 'at': [-2000.0, -4800.0, 308.0],
                                        'next': [{'to': 99, 'weight': 1.0}]}]}
got = editorchecks.for_plan(LEVEL, bad_plan)['checks']
errs = [r for r in got if r['level'] == 'error']
check('a plan error is a Checks row on the point it names',
      errs and errs[0]['target'] == {'kind': 'point', 'id': 6}
      and errs[0]['at'] == [-2000.0, -4800.0, 308.0], errs)
for r in errs:
    plain('the editor row "%s"' % r['text'][:40], r['text'])

# 8. C with a fallback: an unlinkable added piece is left out; a level piece is a fault
from riftlord.build import levelbuild  # noqa: E402


def meta(name_index, number, outer):
    m = bytearray(100)
    struct.pack_into('<i', m, levelbuild.OUTER_AT, outer)
    struct.pack_into('<ii', m, levelbuild.NAME_AT, name_index, number)
    return bytes(m)


class Pkg:
    names = [('RiftLordKit', 0), ('StaticMeshComponent0', 0), ('Torch2', 0)]
    exports = [{'meta': meta(0, 4, 0)}, {'meta': meta(1, 0, 1)}, {'meta': meta(2, 0, 0)}]


class Builder:
    pkg = Pkg()


LINK_PLAN = {'added': [{}, {}, {}, {'kind': 'kit', 'name': 'BP_WallTorch_A_C'}]}
exc = applier.unlinked(Builder(), [1], LINK_PLAN, '1 listed actors are not preloaded')
check('an added piece that cannot be linked is left out (LeaveOut, item 3)',
      isinstance(exc, buildmessage.LeaveOut) and exc.items == [3]
      and exc.names == ['the added piece BP_WallTorch_A'], (type(exc), exc))
plain('an added piece that cannot be linked', exc, ['BP_WallTorch_A'], kind='C')
exc = applier.unlinked(Builder(), [2], LINK_PLAN, 'null preload dependency on Torch2')
check('a level piece that cannot be linked is a Fault', isinstance(exc, buildmessage.Fault)
      and not isinstance(exc, buildmessage.LeaveOut), type(exc))
plain('an unlinkable level piece', exc, kind='C')
plain('a part left out (C)', buildmessage.left_out('the moons', 'KeyError: x'),
      ['Rift Lord could not write the moons', 'built without it'], kind='C')
plain('a part left out with an effect (C)',
      buildmessage.left_out('the intro card', 'x', effect='it may still show', verb='remove'),
      ['Rift Lord could not remove the intro card; it may still show.'], kind='C')
plain('a part left out for a design reason (B)',
      buildmessage.left_out('the painted material M', '', 'Pick another material for it.'),
      ['The build left out the painted material M: pick another material for it.'])
check('the shipped-floor and light-map fallbacks are Rift Lord\'s',
      emit.SHIPPED_FLOOR_NOTE.startswith('Rift Lord could not')
      and emit.LIGHTS_NOTE.startswith('Rift Lord could not'))
from riftlord.build.navigation import navregen  # noqa: E402
_ua, _ux, kept_notes = navregen.kept(b'a', b'x', 'Scenario_01', 'detail')
check('a rebuild that keeps the shipped floor marks its note as kept',
      kept_notes and all(isinstance(n, navregen.KeptNote) for n in kept_notes), kept_notes)
_emit_src = open(emit.__file__).read()
check('the build lists a kept-floor note with its fallbacks and treats the floor as not rebuilt',
      'fallbacks.extend(kept)' in _emit_src and 'regenerated = not kept' in _emit_src)
from riftlord.build.lighting import lightbake  # noqa: E402
was = lightbake._bake_now
lightbake._bake_now = True
lightbake.give_up()
check('a failed bake gives up the bake for this build', not lightbake._bake_now)
lightbake._bake_now = was
#: the bucket's line for a fault: saved report, no designer task
os.environ['OMD3_BUCKET'] = os.path.join(SCRATCH, 'bucket')
os.makedirs(os.path.join(bucket.root(), '.riftlord', 'records'), exist_ok=True)
from riftlord.core import console  # noqa: E402
try:
    raise RuntimeError('wave bytecode: statement 12 has no target')
except RuntimeError as err:
    shown, rows = bucket._say_failure(err, 'none', 'Bucket Map')
plain('a failed build\'s line', shown, ['Rift Lord could not build this mission'], kind='C')
check('its report sits in the Missions folder\'s reports',
      shown.count(bucket.reports_dir()) == 1 and not rows, shown)
check('an editor-check refusal stays B with its rows',
      isinstance(bucket._failed(buildmessage.Refusal('x', checks=[{'text': 'x'}]), 'x',
                                [{'text': 'x'}]), buildmessage.Refusal))

# 9. no message sends the designer anywhere: every string of the build's code, scanned
#    (navregen.py and recastgen.py belong to the walkable-floor worker and are listed)
OTHERS = ('riftlord/build/navigation/navregen.py', 'riftlord/build/navigation/recastgen.py')
#: the pattern's own home
PATTERN_HOME = 'riftlord/core/buildmessage.py'
hits, others = [], []
for folder, _dirs, files in os.walk(os.path.join(ROOT, 'riftlord')):
    if '__pycache__' in folder or os.sep + 'fab' in folder:
        continue
    for f in files:
        if not f.endswith('.py'):
            continue
        path = os.path.join(folder, f)
        rel = os.path.relpath(path, ROOT).replace(os.sep, '/')
        if rel == PATTERN_HOME:
            continue
        text = open(path, encoding='utf-8').read()
        for m in re.finditer(r"'[^'\n]*(send (the|this|your) map|to the developer)[^'\n]*'", text):
            (others if rel in OTHERS else hits).append('%s: %s' % (rel, m.group(0)[:80]))
check('no build message tells anyone to send the map (%d in the floor worker\'s files)'
      % len(others), not hits, hits)

#    the C checks on the pinned builds of the test missions (tools/buildcompare.py
#    BASELINES, built by this code): a fault in a real build fails here
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import buildcompare  # noqa: E402
from riftlord.build import leveltitle  # noqa: E402
from riftlord.build.navigation import navfan  # noqa: E402
paks = []
for record, base in sorted(buildcompare.BASELINES.items()):
    folder = os.path.join(buildcompare.PINNED, record, base.get('digest') or 'none')
    paks += [os.path.join(folder, f) for f in sorted(os.listdir(folder))
             if f.endswith('.pak')] if os.path.isdir(folder) else []
built = 0
for pak in paks:
    name = os.path.basename(pak)
    mapname = next((m for m in navpatch.maps_in_pak(pak) if m.endswith('_Map01')), None)
    level_files = leveltitle.level_files(pak)
    if not mapname or not level_files:
        continue
    built += 1
    umap = next(n for n in level_files if n.endswith('.umap'))
    ua, ux = level_files[umap], level_files[umap[:-5] + '.uexp']
    b = levelbuild.Builder.around(ua, ux)
    check('%s: no null preloads and no unlinked actors' % name,
          not applier.null_preloads(b) and not b.level_preload_gaps())
    check('%s: its walkable floor passes its own checks' % name,
          nx.check_buffer(nx._surface_of(ua, ux), []) == [],
          nx.check_buffer(nx._surface_of(ua, ux), []))
    check('%s: no vertex fan freezes the game' % name, navfan.build_problems(pak) == [],
          navfan.build_problems(pak)[:3])
    rows = emit.path_checks(pak, mapname) + emit.flow_checks(
        pak, '%s_Onslaught01' % mapname[:-len('_Map01')])
    bad = [r['name'] for r in rows if r.get('fault') and not r['ok']]
    check('%s: no Rift Lord fault in the path and wave checks' % name, not bad, bad)
if not built:
    print('SKIP  no pinned build of a test mission here (tools/buildcompare.py --pin)')

# 10. the Map Editor's Checks list (web/mapeditor/test/checks_check.mjs)
node = shutil.which('node')
if node:
    got = subprocess.run([node, 'test/checks_check.mjs'], capture_output=True, text=True,
                         cwd=os.path.join(ROOT, 'web', 'mapeditor'))
    check('checks.js: rows in order, selectable targets (%s)'
          % (got.stdout.strip().splitlines() or ['no output'])[-1], got.returncode == 0,
          got.stdout[-400:] + got.stderr[-400:])
else:
    print('SKIP  node is not on PATH')

shutil.rmtree(SCRATCH, ignore_errors=True)
print('%d checks, %d failed' % (len(checks), checks.count(False)))
sys.exit(0 if all(checks) else 1)
