"""Offline checks on the Collides switch of the level's own pieces (marker: collision): the
shipped state read as the engine resolves it (bUseDefaultCollision and the mesh's own
profile), the switch stored on the piece's edit and kept by a save, written into a copy
of the sublevel and read back on, off and on-with-an-explicit-profile, the walkable-graph
rebuild taking the switched pieces in or out, an edit that only switches collision not
counted as a move (precomputed visibility and the light bake), the rebuild resolving every
component's collision by the same rule, and the Map Editor's switch.

    python3 test_collides.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.core import gamefiles  # noqa: E402
from riftlord.build import apply  # noqa: E402
from riftlord.build.lighting import lightbake  # noqa: E402
from riftlord.build.navigation import navregen  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402
from riftlord.mapeditor import geomedits  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402
from riftlord.mapeditor import piececollision as pc  # noqa: E402

#: Repaired Mine's level; piece 2689 is the Stairs_2x_05_SB it ships with collision off
LEVEL = 'Scenario_Ice_02'
STAIR = 2689
#: Scenario_01's Column_Sec_1_4_RB StaticMeshActors: 14 off and 4 on, as measured
COLUMN_LEVEL = 'Scenario_01'
COLUMN = 'Column_Sec_1_4_RB'
#: a Scenario_01 StaticMeshActor whose mesh's own profile is NoCollision
SPIKE = 807
#: a Repaired Mine Woodbeam2 whose BodyInstance alone says NoCollision
BEAM = 2991

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


# 1. the mesh table and the engine's rule
col = pc.mesh_collision(COLUMN)
check('the mesh table: %s has a shape and BlockAll' % COLUMN,
      col == {'shape': True, 'profile': 'BlockAll'}, col)
off_body = {'CollisionEnabled': 'ECollisionEnabled::NoCollision',
            'CollisionProfileName': 'NoCollision'}
check('engine rule: a StaticMeshActor component with no bUseDefaultCollision takes the '
      "mesh's profile, over its own BodyInstance",
      pc.resolved(None, None, True, col) and pc.resolved(None, off_body, True, col) and
      not pc.resolved(None, None, True, {'shape': True, 'profile': 'NoCollision'}), '')
check('engine rule: bUseDefaultCollision false takes the BodyInstance; another owner '
      'defaults it to false',
      not pc.resolved(False, off_body, True, col) and pc.resolved(False, None, True, col)
      and not pc.resolved(None, off_body, False, col), '')

# 2. shipped state on the pieces
lv = leveldata.level(COLUMN_LEVEL)
cols = [p.get('collides') for p in lv['pieces']
        if p['mesh'] == COLUMN and p.get('source') == 'component']
check('%s: %s placed off 14, on 4' % (COLUMN_LEVEL, COLUMN),
      (cols.count(False), cols.count(True)) == (14, 4), cols)
check('a Blueprint part has no switch',
      all('collides' not in p for p in lv['pieces'] if p.get('source') == 'blueprint'), '')
ice = leveldata.level(LEVEL)
byid = {p['id']: p for p in ice['pieces']}
check('%s piece %d (%s) ships off' % (LEVEL, STAIR, byid[STAIR]['mesh']),
      byid[STAIR].get('collides') is False, byid[STAIR].get('collides'))
on_piece = next(p for p in ice['pieces'] if p.get('collides') is True and p.get('sub') == 'Castle')

# 3. the plan: the stair moved up 300 and switched on, a colliding piece switched off
moved = list(byid[STAIR]['matrix'])
moved[14] += 300
plan = {'level': LEVEL, 'edits': [
    {'id': STAIR, 'mesh': byid[STAIR]['mesh'], 'matrix': moved, 'collision': True},
    {'id': on_piece['id'], 'mesh': on_piece['mesh'], 'matrix': on_piece['matrix'],
     'collision': False}]}
check('edited: the plan switches both', pc.edited(plan) == {STAIR: True, on_piece['id']: False},
      pc.edited(plan))
check('only the stair edit moves a piece',
      [e['id'] for e in pc.moving_edits(plan)] == [STAIR], pc.moving_edits(plan))
only = {'level': LEVEL, 'edits': [plan['edits'][1]]}
check('an edit that only switches collision keeps precomputed visibility',
      not geomedits.visibility_is_stale(only) and geomedits.visibility_is_stale(plan), '')
check('an edit that only switches collision is no moved piece for the light bake',
      not any('moved' in r for r in lightbake.stale(only)), lightbake.stale(only))
same = {'level': LEVEL, 'edits': [dict(plan['edits'][0], collision=False)]}
check('a switch that matches the shipped state writes nothing',
      not pc.switched(LEVEL, same), pc.switched(LEVEL, same))

# 4. written into a copy of the sublevel and read back
edits = apply.sublevel_edits(LEVEL, plan)
castle = edits.get('Castle') or {}
check('sublevel_edits: both switches in the Castle sublevel',
      castle.get('collision') == {byid[STAIR]['export']: (True, byid[STAIR]['mesh']),
                                  on_piece['export']: (False, on_piece['mesh'])},
      castle.get('collision'))
ua, ux = gamefiles.package(ice['subPackages']['Castle'])
ua2, ux2, report = apply.apply_to_sublevel(ua, ux, LEVEL + '_Castle', castle)
check('two switched, the stair moved', report.get('collision_switched') == 2
      and report['moved'] == 1 and not report['skipped'], report)
pkg = Package(ua2, ux2)
pkg.load_payloads()
rows = pc._component_rows(pkg)
stair_row, off_row = rows[byid[STAIR]['export']][:3], rows[on_piece['export']][:3]
check('the stair reads back on: both properties removed, the mesh profile rules',
      stair_row == (None, None, True) and pc.resolved(*stair_row, pc.mesh_collision(
          byid[STAIR]['mesh'])), stair_row)
check('the switched-off piece reads back off as the shipped decor is',
      off_row == (False, off_body, True), off_row)

# 5. on, where the mesh's own profile collides with nothing: an explicit BlockAll body
spike = {p['id']: p for p in lv['pieces']}[SPIKE]
check('Scenario_01 piece %d (%s): its mesh profile is NoCollision' % (SPIKE, spike['mesh']),
      pc.mesh_collision(spike['mesh'])['profile'] == 'NoCollision'
      and spike.get('collides') is False, spike.get('collides'))
on_plan = {'level': COLUMN_LEVEL, 'edits': [
    {'id': SPIKE, 'mesh': spike['mesh'], 'matrix': spike['matrix'], 'collision': True}]}
tag = spike.get('sub') or 'persistent'
got = apply.sublevel_edits(COLUMN_LEVEL, on_plan).get(tag) or {}
ua, ux = gamefiles.package(lv['subPackages'][tag])
ua3, ux3, report = apply.apply_to_sublevel(ua, ux, COLUMN_LEVEL + '_' + tag, got)
pkg = Package(ua3, ux3)
pkg.load_payloads()
row = pc._component_rows(pkg)[spike['export']][:3]
check('it reads back with bUseDefaultCollision false and a BlockAll BodyInstance',
      row == (False, {'CollisionProfileName': 'BlockAll'}, True), row)

# 6. the walkable-graph rebuild


def has(p, mesh, z):
    return any(n == mesh and abs(M[3][2] - z) < 1 for n, M in navregen.final_pieces(LEVEL, p))


bare = {'level': LEVEL, 'edits': [{k: v for k, v in plan['edits'][0].items() if k != 'collision'}]}
check('the rebuild takes the moved stair when switched on, not when only moved',
      has(plan, byid[STAIR]['mesh'], moved[14]) and not has(bare, byid[STAIR]['mesh'], moved[14]), '')
check('the rebuild leaves the switched-off piece out',
      has({'level': LEVEL}, on_piece['mesh'], on_piece['matrix'][14])
      and not has(plan, on_piece['mesh'], on_piece['matrix'][14]), '')
check('a switch alone asks for a rebuild', navregen.wanted(LEVEL, only), '')
#: the rebuild resolves collision as the engine does (piececollision.level_collides):
#: Repaired Mine's Woodbeam2 piece 2991 is a StaticMeshActor whose BodyInstance says
#: NoCollision but whose bUseDefaultCollision (default true) takes the mesh's BlockAll
flags = navregen.component_flags(LEVEL)
engine = pc.level_collides(LEVEL)
beam = byid[BEAM]
row = pc._level_rows(LEVEL)[(beam['sub'], beam['export'])]
check('piece %d (%s): BodyInstance NoCollision, bUseDefaultCollision unstored, a StaticMeshActor'
      % (BEAM, beam['mesh']), row[0] is None and pc._off(row[1]) and row[2], row)
check('the rebuild takes it, as the engine does',
      engine[(beam['sub'], beam['export'])] and navregen.collides(beam, flags), '')
check('the rebuild leaves out exactly the components the engine resolves as not colliding',
      {k for k, v in flags.items() if v is False} == {k for k, v in engine.items() if not v}, '')
ghosts = [g[0] for g in navregen.walkless_pieces(LEVEL, plan)]
check('the switched-on stair is no walkless ghost', not any(str(STAIR) in g for g in ghosts), ghosts)

# 7. a save keeps the switch (leveldata.save_plan), into a scratch plans folder
leveldata.PLANS = tempfile.mkdtemp(prefix='collides-plans-')
leveldata.save_plan(LEVEL, plan)
with open(os.path.join(leveldata.PLANS, os.listdir(leveldata.PLANS)[0])) as fh:
    saved = json.load(fh).get('edits') or []
check('a save keeps each edit\'s collision', [e.get('collision') for e in saved] == [True, False],
      saved)

# 8. the Map Editor's switch
src = open(os.path.join(ROOT, 'web', 'mapeditor', 'src', 'main.js')).read()
check('the piece panel offers the switch on level pieces',
      "typeof p.collides === 'boolean'" in src and 'function setPieceCollision' in src
      and 'e.collision = on' in src, '')

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d of %d checks passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
