"""Offline checks on missions with more than one rift (riftlord/mapeditor/planactors.py
rift_exits, docs/research/MULTI-RIFT.md): each placed rift's own exit point (named, or the
added point with no branches inside it), routes ending at any rift, 'rift' and a point with
no branches still leading to the level's exit, the save's errors (a rift with no exit, two
rifts sharing one, an exit that is not a placed point), the level's exit removed with a
placed rift's exit taking its routes, the older plan whose first rift takes the level's
moved exit unchanged, the walkable graph's spawner-to-exit walks (navregen._route_exits),
the Map Editor's graph (web/mapeditor/src/actors.js, run with node) agreeing with the
build's, and the save keeping a rift's `exit`.

    python3 test_multirift.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from riftlord.mapeditor import planactors  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


# A level of four points: L0 -> L1 -> L3 (the exit, under the level's rift), L2 a side
# point leading to L1.
LEVEL = {'exit': 'L3', 'points': {
    'L0': {'index': 0, 'location': (0.0, 0.0, 0.0), 'next': [{'to': 'L1', 'weight': 1.0}],
           'exit': False, 'start': True},
    'L1': {'index': 1, 'location': (2000.0, 0.0, 0.0), 'next': [{'to': 'L3', 'weight': 1.0}],
           'exit': False, 'start': False},
    'L2': {'index': 2, 'location': (2000.0, 2000.0, 0.0), 'next': [{'to': 'L1', 'weight': 1.0}],
           'exit': False, 'start': False},
    'L3': {'index': 3, 'location': (4000.0, 0.0, 0.0), 'next': [], 'exit': True,
           'start': False}}, 'spawners': {}, 'doors': {}, 'starts': []}
#: the placed rift, far from the level's
RIFT_AT = [0.0, -6000.0, 300.0]


def pt(pid, at, nxt=()):
    return planactors.clean_point({'id': pid, 'at': list(at),
                                   'next': [{'to': n, 'weight': 1.0 / max(1, len(nxt))}
                                            for n in nxt]}, pid)


def plan_of(points=(), rifts=(), wiring=(), spawners=()):
    return {'version': 2, 'points': list(points), 'rifts': [dict(r) for r in rifts],
            'spawners': list(spawners), 'playerStarts': [], 'wiring': list(wiring)}


def edges(nodes):
    return {str(k): sorted(str(b['to']) for b in n['next']) for k, n in nodes.items()}


# 1. two rifts: L0 forks to L1 (the level's rift) and to point 10 -> point 11, the placed
#    rift's own exit
under = [RIFT_AT[0], RIFT_AT[1], RIFT_AT[2] - planactors.RIFT_OVER_EXIT]
two = plan_of(points=[pt(10, (0.0, -3000.0, 0.0), [11]), pt(11, under)],
              rifts=[{'id': 20, 'at': RIFT_AT, 'rotation': 0.0, 'exit': 11}],
              wiring=[{'name': 'L0', 'kind': 'point', 'gone': False,
                       'next': [{'to': 'L1', 'weight': 0.5}, {'to': 10, 'weight': 0.5}]}])
nodes, _removed, problems = planactors.graph(two, LEVEL)
errors, warnings = planactors.check(two, 'x', LEVEL)
check('two rifts: two exits, the level\'s and the placed rift\'s',
      sorted(map(str, planactors.exits(nodes))) == ['11', 'L3'], planactors.exits(nodes))
check('two rifts: the placed rift\'s exit leads nowhere', nodes[11]['next'] == [] and
      nodes[11].get('rift') == 20, nodes[11])
check('two rifts: every point reaches a rift; no errors or warnings',
      not errors and not warnings and not problems, (errors, warnings, problems))
check('two rifts: the exit stands inside its rift',
      planactors.in_rift(under, RIFT_AT), under)
check('the rift-over-exit drop sits inside the enter area',
      planactors.in_rift([0, 0, -planactors.RIFT_OVER_EXIT], [0, 0, 0]))
check('a point beside the rift, past its radius, is outside it',
      not planactors.in_rift([planactors.RIFT_ENTER_RADIUS + 1, 0,
                              -planactors.RIFT_ENTER_DROP], [0, 0, 0]))

# 2. an older plan's rift with no `exit` takes the added point with no branches inside it
adopt = plan_of(points=[pt(10, (0.0, -3000.0, 0.0), [11]),
                        pt(11, [RIFT_AT[0] + 100, RIFT_AT[1], RIFT_AT[2] - 90]),
                        pt(12, [RIFT_AT[0] + 150, RIFT_AT[1], RIFT_AT[2] - 90])],
                rifts=[{'id': 20, 'at': RIFT_AT, 'rotation': 0.0}])
own = planactors.rift_exits(adopt, LEVEL)
check('a rift naming no exit takes the nearest added point with no branches inside it',
      own == {11: 20}, own)
errors, _w = planactors.check(adopt, 'x', LEVEL)
check('point 12, also inside but not the exit, goes on to the level\'s exit',
      not errors and planactors.graph(adopt, LEVEL)[0][12]['next'] == [
          {'to': 'L3', 'weight': 1.0}], errors)

# 3. the save's errors
lonely = plan_of(points=[pt(10, (0.0, -3000.0, 0.0))],
                 rifts=[{'id': 20, 'at': RIFT_AT, 'rotation': 0.0}])
errors, _w = planactors.check(lonely, 'x', LEVEL)
check('a rift with no exit point is an error naming it',
      any('Rift 20 has no exit point' in e for e in errors), errors)
shared = plan_of(points=[pt(11, under)],
                 rifts=[{'id': 20, 'at': RIFT_AT, 'rotation': 0.0, 'exit': 11},
                        {'id': 21, 'at': RIFT_AT, 'rotation': 0.0, 'exit': 11}])
errors, _w = planactors.check(shared, 'x', LEVEL)
check('two rifts sharing an exit point is an error',
      any('share exit point 11' in e for e in errors), errors)
bad = plan_of(rifts=[{'id': 20, 'at': RIFT_AT, 'rotation': 0.0, 'exit': 99}])
errors, _w = planactors.check(bad, 'x', LEVEL)
check('an exit that is not a placed point is an error',
      any('exit point 99 is not a path point you placed' in e for e in errors), errors)
far = plan_of(points=[pt(11, (RIFT_AT[0] + 900, RIFT_AT[1], RIFT_AT[2]))],
              rifts=[{'id': 20, 'at': RIFT_AT, 'rotation': 0.0, 'exit': 11}])
_e, warnings = planactors.check(far, 'x', LEVEL)
check('an exit outside its rift\'s enter area is a warning',
      any('outside the rift' in w for w in warnings), warnings)

# 4. the level's exit removed, the placed rift's exit takes 'rift' and the branches into it
gone_exit = plan_of(points=[pt(10, (0.0, -3000.0, 0.0)), pt(11, under)],
                    rifts=[{'id': 20, 'at': RIFT_AT, 'rotation': 0.0, 'exit': 11}],
                    wiring=[{'name': 'L3', 'kind': 'point', 'gone': True}])
nodes, removed, problems = planactors.graph(gone_exit, LEVEL)
errors, _w = planactors.check(gone_exit, 'x', LEVEL)
check('the level\'s exit removed: its branches and \'rift\' go to the placed rift\'s exit',
      'L3' in removed and edges(nodes)['L1'] == ['11'] and edges(nodes)['10'] == ['11']
      and not errors and not problems, (edges(nodes), errors, problems))
check('the level\'s exit removed: not moved, since the rift has its own',
      not planactors.moves_exit(gone_exit, LEVEL))

# 5. an older plan: the first rift (no exit) takes the level's moved exit, as before
moved = plan_of(points=[pt(10, (0.0, -3000.0, 0.0))],
                rifts=[{'id': 20, 'at': RIFT_AT, 'rotation': 0.0}],
                wiring=[{'name': 'L3', 'kind': 'point', 'gone': True}])
nodes, removed, problems = planactors.graph(moved, LEVEL)
check('older plan: the level\'s exit moves to the first rift',
      planactors.moves_exit(moved, LEVEL) and 'L3' not in removed
      and nodes['L3']['at'] == tuple(RIFT_AT) and edges(nodes)['10'] == ['L3']
      and planactors.rift_exits(moved, LEVEL) == {} and not problems,
      (edges(nodes), removed, problems))

# 6. a plan with no rifts: graph() as before (no exit fields on plan points, 'rift' -> L3)
plain = plan_of(points=[pt(10, (0.0, -3000.0, 0.0))])
nodes, _r, _p = planactors.graph(plain, LEVEL)
check('no placed rift: one exit, the level\'s; a point with no branches goes to it',
      planactors.exits(nodes) == ['L3'] and edges(nodes)['10'] == ['L3'], edges(nodes))

# 7. the walkable graph's spawner walks: to each exit its route can end at
from riftlord.build.navigation import navregen  # noqa: E402
nodes, _r, _p = planactors.graph(two, LEVEL)
ex = planactors.exits(nodes)
check('a route forking to two rifts walks to both exits',
      sorted(map(str, navregen._route_exits(nodes, 'L0', ex))) == ['11', 'L3'])
check('a route to the placed rift walks to its exit only',
      navregen._route_exits(nodes, 10, ex) == [11])

#    and a flyer route to the placed rift ends at its exit (the flight graph's route)
flyer = dict(two, spawners=[{'id': 30, 'at': [0.0, -2000.0, 900.0], 'rotation': 0.0,
                             'kind': 'flyer', 'index': 0, 'door': None, 'start': None,
                             'flyerStart': 10}])
routes = planactors.flyer_routes(flyer, LEVEL)
check('a flyer route to the placed rift ends at its exit point',
      len(routes) == 1 and [round(v, 2) for v in routes[0][0][-1]] ==
      [round(v, 2) for v in under], routes)

# 8. the save keeps a rift's exit
from riftlord.mapeditor import leveldata  # noqa: E402
got = leveldata._object({'id': 20, 'at': RIFT_AT, 'rotation': 0, 'exit': '11'}, 'rift', 1)
check('the save keeps a rift\'s exit as a point id', got.get('exit') == 11, got)
got = leveldata._object({'id': 20, 'at': RIFT_AT, 'rotation': 0}, 'rift', 1)
check('a rift with no exit saves none', 'exit' not in got, got)

# 9. the Map Editor's graph agrees with the build's (node)
ACTORS = os.path.join(ROOT, 'web', 'mapeditor', 'src', 'actors.js')
JS = r'''
const m = await import(process.argv[1])
const cases = JSON.parse(process.argv[2])
const level = JSON.parse(process.argv[3])
const out = {}
for (const [name, plan] of Object.entries(cases)) {
  const g = m.pathGraph(plan, level)
  const edges = {}
  for (const [k, v] of g.nodes) edges[k.slice(1)] = v.map(w => w.slice(1)).sort()
  out[name] = { edges, exits: g.exits.map(k => k.slice(1)).sort(),
                own: [...m.riftExits(plan, level)].map(([p, r]) => [p, r]) }
}
console.log(JSON.stringify(out))
'''
js_level = {'points': {n: {'export': n, 'index': p['index'], 'location': list(p['location']),
                           'exit': p['exit'],
                           'next': [{'PointIndex': LEVEL['points'][b['to']]['index'],
                                     'BranchWeight': b['weight']} for b in p['next']]}
                       for n, p in LEVEL['points'].items()}}
cases = {'two': two, 'adopt': adopt, 'gone_exit': gone_exit, 'moved': moved, 'plain': plain}
try:
    run = subprocess.run(['node', '--input-type=module', '-e', JS, ACTORS,
                          json.dumps(cases), json.dumps(js_level)],
                         capture_output=True, text=True, timeout=60)
    js = json.loads(run.stdout) if run.returncode == 0 else None
except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
    js, run = None, exc
check('node ran actors.js', js is not None, getattr(run, 'stderr', run))
for name, plan in cases.items():
    if js is None:
        break
    nodes, _r, _p = planactors.graph(plan, LEVEL)
    py = {'edges': edges(nodes), 'exits': sorted(map(str, planactors.exits(nodes))),
          'own': sorted([p, r] for p, r in planactors.rift_exits(plan, LEVEL).items())}
    got = js[name]
    check('Map Editor graph = build graph (%s)' % name,
          got['edges'] == py['edges'] and got['exits'] == py['exits']
          and sorted(got['own']) == py['own'], (got, py))

# Crystal Defense takes one rift (user, 2026-10-30): Scenario_01 has one of its own
from riftlord.mapeditor import crystal  # noqa: E402
one = {'level': 'Scenario_01', 'rifts': [], 'wiring': []}
two = {'level': 'Scenario_01', 'rifts': [{'id': 11, 'at': RIFT_AT, 'rotation': 0.0}],
       'wiring': []}
moved = {'level': 'Scenario_01', 'rifts': [{'id': 11, 'at': RIFT_AT, 'rotation': 0.0}],
         'wiring': [{'name': 'BP_OMDRift_2', 'kind': 'rift', 'gone': True}]}
scratch = {'scratch': {'base': 'Scenario_01'}, 'rifts': [{'id': 11, 'at': RIFT_AT}],
           'wiring': []}
check('rifts in play: the level\'s own, placed ones, less removed ones',
      [crystal.rifts_in_play(p) for p in (one, two, moved, scratch)] == [1, 2, 1, 2],
      [crystal.rifts_in_play(p) for p in (one, two, moved, scratch)])
refusal = [t for t in crystal.problems(dict(two, added=[{'kind': crystal.KIND}]))
           if t.startswith('Crystal Defense takes one rift')]
check('a Crystal with two rifts is refused', len(refusal) == 1, refusal)
check('a Crystal with one rift is not refused for its rifts',
      not [t for t in crystal.problems(dict(moved, added=[{'kind': crystal.KIND}]))
           if t.startswith('Crystal Defense takes one rift')])
check('two rifts without a Crystal are not refused', crystal.problems(two) == [])

failed = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else '  -- %s' % (detail,)))
print('%d of %d passed' % (len(checks) - len(failed), len(checks)))
sys.exit(1 if failed else 0)
