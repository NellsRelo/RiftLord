"""Offline checks on added floor in the walkable graph a build writes: added kit pieces in an
edited shipped level's regenerated graph (navregen.added_meshes, final_pieces, wanted) and in
flyers' airspace (navvolclear.final_pieces), and
the height a walker stands on a floor piece (navregen.walk_top) where its box top is higher,
in a scratch level's floor (scratch.floor_boxes, cover) and the Map Editor's snaps
(riftfloor.plan_floor).

    python3 test_floorbuild.py [--no-build]      # --no-build: skip the regenerated graph
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests', 'fixtures'))

import numpy as np  # noqa: E402

from makemissions import matrix, point  # noqa: E402
from riftlord.build.navigation import navregen, navsurf, navsurfgen, riftfloor  # noqa: E402
from riftlord.formats.omd3 import meshcat  # noqa: E402
from riftlord.mapeditor import leveldata, scratch  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


LEVEL = 'Scenario_01'
cat = meshcat.load()

# 1. an added kit piece is its static meshes, as the level's own Blueprint actors are
FLOOR = 'Floor_SB_4x4_C'
tile = {'name': FLOOR, 'kind': 'kit', 'matrix': matrix((1200.0, 600.0, 0.0), yaw=90.0)}
parts = [p for p in leveldata.kit_parts(FLOOR)['parts'] if not p.get('skeletal')]
got = navregen.added_meshes(tile)
check('a kit floor gives each of its static meshes',
      [m for m, _M in got] == [p['mesh'] for p in parts], got)
corners = np.array([[x, y, 0.0, 1.0] for x in (0.0, 1.0) for y in (0.0, 1.0)])
want = scratch._piece_box(tile, cat)
info = cat[parts[0]['mesh']]
o, e = np.array(info['origin']), np.array(info['extent'])
box = np.array([[o[0] + sx * e[0], o[1] + sy * e[1], o[2] + sz * e[2], 1.0]
                for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]) @ got[0][1]
check('a kit part is placed by its own transform and then the piece\'s',
      np.allclose(box[:, :3].min(0), want[0], atol=0.01)
      and np.allclose(box[:, :3].max(0), want[1], atol=0.01), (box[:, :3].min(0), want))
check('a kit actor Rift Lord places with its own build adds no geometry',
      navregen.added_meshes({'name': 'BP_TreasureChest_C', 'kind': 'kit',
                             'matrix': matrix((0.0, 0.0, 0.0))}) == [])
check('a door kit adds no geometry (doors block with their own obstruction)',
      navregen.added_meshes({'name': 'BP_Door_Exterior_3W_C', 'kind': 'kit',
                             'matrix': matrix((0.0, 0.0, 0.0))}) == [])
check('an item with no matrix adds nothing', navregen.added_meshes({'name': FLOOR, 'kind': 'kit'})
      == [])
check('a plan adding only kit floor regenerates the graph',
      navregen.wanted(LEVEL, {'level': LEVEL, 'added': [tile]}))
check('a plan adding only a kit actor does not',
      not navregen.wanted(LEVEL, {'level': LEVEL, 'added': [
          {'name': 'BP_TreasureChest_C', 'kind': 'kit', 'matrix': matrix((0.0, 0.0, 0.0))}]}))
base = navregen.final_pieces(LEVEL, {'level': LEVEL})
more = navregen.final_pieces(LEVEL, {'level': LEVEL, 'added': [tile]})
check('the final pieces carry the kit floor\'s meshes',
      [m for m, _M in more[len(base):]] == [p['mesh'] for p in parts], more[len(base):])

# 2. the height a walker stands on a floor piece
CORNER = 'BP_Scenario_06_CornerFloor_C'
#: Name TBD's corner floor (900_Arena_P), placed at z 150.05
corner = {'name': CORNER, 'kind': 'kit',
          'matrix': [0.0, 1.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, -0.0, 1.0, 0.0,
                     -4175.4347, 297.9208, 150.0512, 1.0]}
z = navregen.item_walk_top(corner)
box = scratch._piece_box(corner, cat)
#: its floor is Wall_Sec_2_2_V2_RB slabs laid flat: their collision top is the piece's own z
check('the corner floor\'s walk height is its slabs\' top, within a span',
      z is not None and 0.0 <= z - 150.0512 <= navregen.CELL_HEIGHT, z)
check('its box top stands over 100 uu higher (crenellations)', box[1][2] - z > 100.0,
      (box[1][2], z))
mirrored = dict(corner, matrix=matrix((0.0, 0.0, 0.0), yaw=90.0, mirror_y=True))
check('a mirrored piece stands at the same height, within a span',
      abs(navregen.item_walk_top(mirrored) - (z - 150.0512)) <= navregen.CELL_HEIGHT,
      navregen.item_walk_top(mirrored))
#: Floor_WD_V2_4x4: a plank slab over a lower one; the lower one's top is hidden
wood = {'name': 'Floor_WD_V2_4x4_C', 'kind': 'kit', 'matrix': matrix((0.0, 0.0, 0.0))}
check('a face hidden under another part is not floor',
      abs(navregen.item_walk_top(wood) - scratch._piece_box(wood, cat)[1][2])
      <= navregen.CELL_HEIGHT, navregen.item_walk_top(wood))
plan = {'level': LEVEL, 'scratch': {'version': 1, 'base': LEVEL, 'name': 'Walk Top'},
        'added': [corner]}
floors = scratch.floor_boxes(plan)
check('a scratch level\'s floor box is topped at the walk height',
      len(floors) == 1 and floors[0][1][2] == z, floors)
AT = (-4296.0, 358.0)
heights = [navsurfgen.plane_z(q, *AT) for q in scratch.cover(plan) if navsurf._inside_xy(q, *AT)]
check('the scratch graph stands at the walk height where Name TBD\'s point 18 stands',
      heights and abs(heights[0] - z) < 0.01, heights)
snapped = riftfloor.snap_point(LEVEL, [AT[0], AT[1], 146.0], plan)
check('the Map Editor snaps a point on it to the walk height',
      snapped['at'] and abs(snapped['floor'] - z) < 0.11, snapped)
slab = {'name': 'Floor_SB_4x4_C', 'kind': 'kit', 'matrix': matrix((0.0, 0.0, 0.0))}
check('a plain slab\'s walk height stays within a span of its box top',
      abs(navregen.item_walk_top(slab) - scratch._piece_box(slab, cat)[1][2])
      <= navregen.CELL_HEIGHT, navregen.item_walk_top(slab))

# 3. flyers' airspace (navvolclear): an added kit wall is geometry, high over North Wing
from riftlord.build.navigation import navvolclear  # noqa: E402
WALL = 'Wall_RB_4x4_C'
wall = {'name': WALL, 'kind': 'kit', 'matrix': matrix((0.0, 0.0, 5000.0))}
base, _level = navvolclear.final_pieces(LEVEL, {'level': LEVEL})
more, _level = navvolclear.final_pieces(LEVEL, {'level': LEVEL, 'added': [wall]})
check('flyers\' airspace takes an added kit wall\'s meshes',
      [m for m, _M in more[len(base):]] == [m for m, _M in navregen.added_meshes(wall)]
      and more[len(base):], more[len(base):])
lo, hi = scratch._piece_box(wall, cat)
region = ([v - navvolclear.CLEARANCE for v in lo], [v + navvolclear.CLEARANCE for v in hi])
solid = [navvolclear.Grid.for_plan(LEVEL, plan, region).solid.sum()
         for plan in ({'level': LEVEL}, {'level': LEVEL, 'added': [wall]})]
check('the wall fills airspace there', solid[1] > solid[0], solid)
check('a kit actor Rift Lord places with its own build fills none',
      len(navvolclear.final_pieces(LEVEL, {'level': LEVEL, 'added': [
          {'name': 'BP_TreasureChest_C', 'kind': 'kit', 'matrix': matrix((0.0, 0.0, 0.0))}]})[0])
      == len(base))

# 4. the regenerated graph of an edited North Wing: a 3x3 kit floor block east of the
#    level's graph, crossed by a ground route, is floor in both agents' graphs
if '--no-build' not in sys.argv:
    from riftlord.build.navigation import recastgen
    X0, Y0, SIDE = 1200.0, 600.0, 1200.0
    tiles = [{'name': FLOOR, 'kind': 'kit', 'matrix': matrix((X0 + SIDE * i, Y0 + SIDE * j, 0.0))}
             for i in range(3) for j in range(3)]
    plan = {'level': LEVEL, 'version': 2, 'added': tiles,
            'spawners': [{'id': 40, 'at': [4200.0, 2400.0, 99.0], 'rotation': 180.0,
                          'kind': 'ground', 'index': 2, 'maxSpawnPerRow': 0, 'door': None,
                          'start': 41, 'flyerStart': None}],
            'points': [point(41, (3000.0, 2400.0, 11.0), ['OMDPathPoint1'])]}
    top = navregen.item_walk_top(tiles[0])

    def missed(polys, radius):
        """Samples over the middle tile, kept clear of its edge by the agent's radius and a
        column, with no polygon within a step (FLAG_MERGE) of the kit floor; and the count."""
        inset = radius + navregen.CELL
        out, n = [], 0
        for x in np.arange(X0 + SIDE + inset, X0 + 2 * SIDE - inset + 1.0, navregen.CELL):
            for y in np.arange(Y0 + SIDE + inset, Y0 + 2 * SIDE - inset + 1.0, navregen.CELL):
                n += 1
                got = riftfloor.floor_under(polys, (x, y, top + navregen.FLAG_MERGE))
                if got is None or abs(got[1] - top) > navregen.FLAG_MERGE:
                    out.append((x, y))
        return out, n
    gone, n = missed(riftfloor.polygons(navsurf.buffer(LEVEL))[0], navregen.AGENTS[0][0])
    check('North Wing ships no floor over the middle tile', len(gone) == n, (len(gone), n))
    try:
        recastgen.binary()
    except SystemExit as exc:
        print('SKIP  the regenerated graph: %s' % exc)
    else:
        graphs = riftfloor.polygons(navregen.buffer_for(LEVEL, plan, log=lambda *_a: None))
        for g, polys in enumerate(graphs):
            gone, _n = missed(polys, navregen.AGENTS[g][0])
            check('regenerated graph %d: the kit floor\'s middle tile is floor' % g, not gone,
                  gone[:5])

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d checks, %d failed' % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
