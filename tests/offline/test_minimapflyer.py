"""Offline checks on the flyer band of a Regenerate minimap
(riftlord/build/minimap/minimapredraw.py): the band follows every flyer route after the
plan (a plan's added flyer spawner, the level's own, removed, moved and re-routed ones),
is drawn in Scenario_Ice_02's measured width and grey at the plate alpha, stops at
walkable floor of a kit cell or more and at the plan's added floors, and a preview
drawing is redrawn when a flyer route moves.

    python3 test_minimapflyer.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402

from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.build.minimap import minimapredraw as mm  # noqa: E402
from riftlord.mapeditor import planactors  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def near(a, b, tol=1.0):
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))


# 1. the band's width and grey are Scenario_Ice_02's (its flyer approach off the east wall)
vol = mm.sources('Scenario_Ice_02')[0][0]
art = np.array(mapdata.minimap_image(vol).convert('RGBA'))
n = art.shape[0]
uu = 2.0 * max(abs(vol['extent'][0]), abs(vol['extent'][1])) / n
spawner = next(s for s in planactors.level_actors('Scenario_Ice_02')['spawners'].values()
               if s['flyerStart'] and s['location'][2] > 900)
u, v = mapdata.projector(vol)(spawner['location'][:2])
col = int(u * n) - 100
grey = (art[:, col, 3] > 0) & (np.abs(art[:, col, 0].astype(int) - mm.FLYER_GREY) <= 0)
rows = np.nonzero(grey)[0]
rows = rows[np.abs(rows - v * n) < n / 8]
check('Ice_02 band is FLYER_GREY', len(rows) > 0, col)
if len(rows):
    width = (rows.max() - rows.min() + 1) * uu
    check('Ice_02 band is FLYER_BAND wide', abs(width - mm.FLYER_BAND) < uu,
          (width, mm.FLYER_BAND))
    check('Ice_02 band is centred on its spawner', abs((rows.max() + rows.min()) / 2.0 - v * n) <= 1,
          ((rows.max() + rows.min()) / 2.0, v * n))
    check('Ice_02 band at its plate alpha', set(art[rows, col, 3]) == {64}, set(art[rows, col, 3]))

# 2. routes after the plan
check('North Wing ships no flyer route', mm._flyer_routes('Scenario_01', {}, True) == [])
added = {'level': 'Scenario_01',
         'spawners': [{'id': 2, 'at': [3196.0, -2451.9, 69.0], 'kind': 'flyer', 'index': 2,
                       'door': None, 'start': None, 'flyerStart': 3}],
         'points': [{'id': 3, 'at': [1958.0, -2432.5, -328.2], 'next': [{'to': 4, 'weight': 1.0}]},
                    {'id': 4, 'at': [-630.9, -951.0, 333.3], 'next': [{'to': 5, 'weight': 1.0}]},
                    {'id': 5, 'at': [-1852.0, 3592.0, 511.3], 'next': []}]}
legs = mm._flyer_routes('Scenario_01', added, True)
exit_at = planactors.level_actors('Scenario_01')['points']['OMDPathPoint6']['location']
check('an added flyer spawner draws its route', len(legs) == 4, legs)
check('... from the spawner', legs and near(legs[0][0], added['spawners'][0]['at']), legs[:1])
check('... to the level\'s exit', any(near(b, exit_at) for _a, b in legs), legs)
check('... and without the spawner leg on request',
      len(mm._flyer_routes('Scenario_01', added, False)) == 3)

ice = planactors.level_actors('Scenario_Ice_01')
flyer_name = next(k for k, s in ice['spawners'].items() if s['flyerStart'])
shipped = mm._flyer_routes('Scenario_Ice_01', {}, True)
check('Ice_01\'s own flyer route', len(shipped) == 4, shipped)
check('... from its spawner', shipped and near(shipped[0][0], ice['spawners'][flyer_name]['location']))
gone = {'wiring': [{'name': flyer_name, 'kind': 'spawner', 'gone': True}]}
check('a removed flyer spawner draws nothing', mm._flyer_routes('Scenario_Ice_01', gone, True) == [])
moved = {'wiring': [{'name': flyer_name, 'kind': 'spawner', 'at': [0.0, 0.0, 1950.0]}]}
got = mm._flyer_routes('Scenario_Ice_01', moved, True)
check('a moved flyer spawner draws from where it stands', got and near(got[0][0], (0, 0, 1950)), got[:1])
ground_name = next(k for k, s in ice['spawners'].items() if not s['flyerStart'])
rerouted = {'points': [{'id': 1, 'at': [100.0, 200.0, 900.0], 'next': []}],
            'wiring': [{'name': ground_name, 'kind': 'spawner', 'flyerStart': 1}]}
got = mm._flyer_routes('Scenario_Ice_01', rerouted, True)
check('a level spawner given a flyer start draws it',
      any(near(a, ice['spawners'][ground_name]['location']) and near(b, (100, 200, 900))
          for a, b in got), got)

# branches that meet again: both legs into the meeting point
fork = copy.deepcopy(added)
fork['points'] = [{'id': 3, 'at': [0.0, 0.0, 500.0], 'next': [{'to': 4, 'weight': 1.0},
                                                             {'to': 5, 'weight': 1.0}]},
                  {'id': 4, 'at': [1000.0, 0.0, 500.0], 'next': [{'to': 6, 'weight': 1.0}]},
                  {'id': 5, 'at': [0.0, 1000.0, 500.0], 'next': [{'to': 6, 'weight': 1.0}]},
                  {'id': 6, 'at': [1000.0, 1000.0, 500.0], 'next': []}]
got = {(a[:2], b[:2]) for a, b in mm._flyer_routes('Scenario_01', fork, False)}
check('branches meeting again draw both legs',
      ((1000.0, 0.0), (1000.0, 1000.0)) in got and ((0.0, 1000.0), (1000.0, 1000.0)) in got, got)

# 3. the floor a build draws carries the plan's flyer route
fl = mm.floor('Scenario_01', added)
check('floor() carries the added flyer route', len(fl.get('flyer_legs') or []) == 4,
      fl.get('flyer_legs'))

# ... and a scratch level's
from riftlord.mapeditor import scratch  # noqa: E402
sp = scratch.new_plan('Flyer Scratch', 'Scenario_01')
sp['spawners'] = [{'id': 1, 'at': [0.0, 0.0, 800.0], 'kind': 'flyer', 'index': 1, 'door': None,
                   'start': None, 'flyerStart': 2}]
sp['points'] = [{'id': 2, 'at': [1000.0, 0.0, 800.0], 'next': []}]
got = mm._scratch_floor('Scenario_01', sp).get('flyer_legs') or []
check('a scratch level carries its flyer route',
      got and near(got[0][0], (0, 0, 800)) and near(got[0][1], (1000, 0, 800)), got)

# 4. walkable pieces the band stops at
big = [(0, 0, 0), (300, 0, 0), (300, 300, 0), (0, 300, 0)]
small = [(1000, 0, 0), (1299, 0, 0), (1299, 299, 0), (1000, 299, 0)]
check('pieces of a kit cell or more', mm._pieces_of_area([big, small], [[], []],
                                                         mm.FLYER_FLOOR_AREA) == [big])

# 5. the band drawn: grey at the plate alpha off the floor, stopping at floor and added floors
sq = [(0, 0, 0), (3000, 0, 0), (3000, 3000, 0), (0, 3000, 0)]
other = [(-4000, 1000, 600), (-3500, 1000, 600), (-3500, 2000, 600), (-4000, 2000, 600)]
fl = {'polys': [sq], 'rects': [], 'erase': [], 'block': [], 'doors': [], 'level_name': None,
      'seeds': [], 'rocks': [], 'every': [sq, other], 'flyer_floor': [sq, other],
      'flyer_legs': [((-5000, 1500, 500), (-1000, 1500, 500)),
                     ((-1000, 1500, 500), (1500, 1500, 500))],
      'every_rects': [(((-2600, 1000, 0), (-2400, 2000, 0)), None)]}
volume = {'translation': (0, 1500, 0), 'rotation': (0, 0, 0, 1), 'extent': (6000, 6000, 3000)}
size = 256
lay = mm.layers(fl, volume, size)
project = mapdata.projector(volume)


def px(x, y):
    a, b = project((x, y))
    return int(b * size), int(a * size)


band = lay['flyer_band']
check('the band is drawn off the floor', band[px(-1500, 1500)], px(-1500, 1500))
check('... not past the route\'s end', not band[px(-5600, 1500)])
check('... and stops at other walkable floor', not band[px(-3750, 1500)])
check('... and at the plan\'s added floors', not band[px(-2500, 1500)])
img = np.array(mm.styled(lay, 38))
y, x = px(-1500, 1500)
check('band grey at the plate alpha', tuple(img[y, x]) == (mm.FLYER_GREY,) * 3 + (38,),
      tuple(img[y, x]))
y, x = px(1500, 1500)
check('the floor stays floor under the route', tuple(img[y, x][:3]) == (mm.FLOOR_RGB,) * 3,
      tuple(img[y, x]))
turn = {'polys': [], 'rects': [], 'erase': [], 'block': [], 'doors': [], 'level_name': None,
        'seeds': [], 'rocks': [], 'every': [], 'flyer_floor': [], 'every_rects': [],
        'flyer_legs': [((-4000, 0, 0), (0, 0, 0)), ((0, 0, 0), (0, 4000, 0))]}
lay = mm.layers(dict(turn, polys=[sq], every=[sq]), volume, size)
check('the band runs round a turn', lay['flyer_band'][px(250, -250)], px(250, -250))

# 6. a preview drawing follows the flyer routes, not the ground routes
plan = dict(copy.deepcopy(added), minimap={'mode': 'regenerate'})
key = mm.preview_key('Scenario_01', plan, 64)
flown = copy.deepcopy(plan)
flown['points'][1]['at'] = [-630.9, -500.0, 333.3]
check('moving a flyer point redraws the preview', mm.preview_key('Scenario_01', flown, 64) != key)
walked = copy.deepcopy(plan)
walked['points'].append({'id': 9, 'at': [400.0, 5000.0, 30.0], 'next': []})
check('adding a point off the flyer routes keeps the drawing',
      mm.preview_key('Scenario_01', walked, 64) == key)

bad = [c for c in checks if not c[1]]
for name_, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name_, '' if ok else ' (%s)' % (detail,)))
print('%d checks, %d failed' % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
