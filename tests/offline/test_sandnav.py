"""Offline checks on the walkable-graph rebuild's geometry and its failures, and on pieces
whose class differs from its asset in case (marker: sandnav): triangles wholly outside the
gathering region left out (Untrained Grounds' landscape reaches 201,600 uu and Recast found
no floor), box components that stop walkers taken as solid (Sandstorm Shelter's floor is
Box_FloorCollision boxes on a level Blueprint with no mesh), a landscape left out when it
collides with nothing or is not used for navigation, hidden pieces whose collision stops
walkers, exclusion volumes from every package, a failed rebuild refused naming the places
to change when the map causes it and the shipped floor kept (and said) otherwise, and
Sludge Shelves' eight Wall_RB_MT_v3_1x4_Angle walls read as late pieces so stored piece
ids keep their pieces.

    python3 test_sandnav.py
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.core import buildmessage  # noqa: E402
from riftlord.build.navigation import navregen  # noqa: E402
from riftlord.build.navigation import navsurfgen  # noqa: E402
from riftlord.build.navigation import recastgen  # noqa: E402
from riftlord.formats.omd3 import omd3actors  # noqa: E402
from riftlord.formats.omd3 import omd3level  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402

#: Untrained Grounds: its Environment landscape spans +-201,600 uu
SAND_01 = 'Scenario_Sand_01'
#: Sandstorm Shelter: floor collision boxes on BP_Scenario_DLC_Sand_04_TrapGrid
SAND_04 = 'Scenario_Sand_04'
#: Sludge Shelves: eight Wall_RB_MT_v3_1x4_Angle_C actors, asset Wall_RB_MT_v3_1x4_angle
SLUDGE = 'Scenario_14'
ANGLE = 'Wall_RB_MT_v3_1x4_Angle_C'

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def gates(level, plan=None):
    """(polygons per agent, route legs not joined, spawners and doors with no floor the
    shipped graph has floor under) of the level's rebuilt graph."""
    from riftlord.build.navigation import navsurf
    got = navregen.graphs_for(level, plan or {}, log=lambda *_a: None)
    buf = navsurfgen.buffer_per_agent([(x[0], x[1], x[3], None, None, None, x[2])
                                       for x in got], navsurfgen.bbox_of(got[0][0]))
    ship_bare = set(navregen.unsupported(navsurf.buffer(level), level, {}))
    return ([len(x[0]) for x in got], navregen.disconnected(buf, level, plan),
            [b for b in navregen.unsupported(buf, level, plan) if b not in ship_bare])


# 1. triangles wholly outside the gathering region
w = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [500, 500, 0], [510, 500, 0],
              [500, 510, 0], [90, 0, 0]], np.float64)
tri = np.array([[0, 1, 2], [3, 4, 5], [1, 6, 3]])
w2, t2 = recastgen.inside_region(w, tri, np.array([-1.0, -1, -1]), np.array([100.0, 100, 1]))
check('a triangle wholly outside the region is dropped, one crossing it kept',
      len(t2) == 2 and np.allclose(w2[t2], w[tri[[0, 2]]]), t2.tolist())
check('and the vertices only it used go too', len(w2) == 5, len(w2))
w3, t3 = recastgen.inside_region(w[:3], tri[:1], np.array([-1.0, -1, -1]),
                                 np.array([100.0, 100, 1]))
check('a mesh wholly inside comes back as it was', w3 is w[:3] or np.array_equal(w3, w[:3])
      and np.array_equal(t3, tri[:1]), '')
lo, hi = navregen.gather_region(SAND_01, {})
verts, tris, _walk = recastgen.geometry(SAND_01, {})
reach = np.maximum(lo - verts.min(axis=0), verts.max(axis=0) - hi).max()
span = np.ptp(verts[tris], axis=1).max()
check('Untrained Grounds: geometry ends within one triangle of the gathering region '
      '(it reached 201,600 uu)', reach <= span, (reach, span))

# 2. box components that stop walkers
profiles = navregen.walker_profiles()
check('BlockAll, OMDBlockAllNoTrap and OMDTrapGridMesh boxes stop walkers',
      profiles.get('BlockAll') and profiles.get('OMDBlockAllNoTrap')
      and profiles.get('OMDTrapGridMesh'), '')
check('OMDCameraBlockingVolume, OMDTrap, OMDTriggerVolume and a box\'s own '
      'OverlapAllDynamic do not',
      not any(profiles.get(p) for p in ('OMDCameraBlockingVolume', 'OMDTrap',
                                        'OMDTriggerVolume', 'OverlapAllDynamic')), '')
check('a box with no BodyInstance anywhere takes OverlapAllDynamic',
      not navregen.box_stops_walkers({}), '')
top = navregen.box_triangles((50.0, 50.0, 10.0), np.eye(4))
up = [t for t in top if np.allclose(t[:, 2], 10.0)]
check('a box\'s top is wound as a render mesh floor (clockwise seen from above)',
      len(up) == 2 and all(np.cross(t[1] - t[0], t[2] - t[0])[2] < 0 for t in up), '')
mirrored = navregen.box_triangles((50.0, 50.0, 10.0), np.diag([-1.0, 1.0, 1.0, 1.0]))
up = [t for t in mirrored if np.allclose(t[:, 2], 10.0)]
check('and stays so under a mirroring transform',
      all(np.cross(t[1] - t[0], t[2] - t[0])[2] < 0 for t in up), '')
grid = [b for b in omd3level.boxes(leveldata.level(SAND_04)['subPackages']['persistent'])
        if b['name'].startswith('Box_FloorCollision')]
check('Sandstorm Shelter: its Box_FloorCollision boxes are read with the class template\'s '
      'extent and profile', grid and all(navregen.box_stops_walkers(b['body']) for b in grid)
      and all(b['extent'] != list(omd3level.BOX_EXTENT) for b in grid), len(grid))
boxes = navregen.collision_boxes(SAND_04, {})
check('and the rebuild takes them as solid', len(boxes) >= len(grid), (len(boxes), len(grid)))
polys, cut, bare = gates(SAND_04)
check('Sandstorm Shelter rebuilds joining every route leg with floor under every spawner '
      'and door', all(polys) and not cut and not bare, (polys, cut, bare))

# a box follows its actor: kit pieces' trap grid boxes on North Wing
LEVEL = 'Scenario_01'
level = leveldata.level(LEVEL)
blueprint = {((p.get('sub') or 'persistent'), p.get('export')) for p in level['pieces']
             if p.get('source') == 'blueprint'}
owned = [(tag, b) for tag, path in level['subPackages'].items()
         for b in omd3level.boxes(path)
         if navregen.box_stops_walkers(b['body']) and (tag, b['actor']) in blueprint]
check('North Wing has kit actors with boxes that stop walkers', owned, len(owned))
if owned:
    tag, first = owned[0]
    parts = [p for p in level['pieces'] if p.get('export') == first['actor']
             and (p.get('sub') or 'persistent') == tag]
    mine = sum(1 for t, b in owned if (t, b['actor']) == (tag, first['actor']))
    base = navregen.collision_boxes(LEVEL, {})
    gone = navregen.collision_boxes(LEVEL, {'removed': [p['id'] for p in parts]})
    check('removing every piece of an actor removes its boxes', len(gone) == len(base) - mine,
          (len(base), len(gone), mine))
    moved = []
    for p in parts:
        M = np.array(p['matrix'], np.float64).reshape(4, 4).copy()
        M[3, 2] += 300.0
        moved.append({'id': p['id'], 'matrix': M.flatten().tolist()})
    moved = navregen.collision_boxes(LEVEL, {'edits': moved})
    lift = [round(float(b[:, :, 2].min() - a[:, :, 2].min()), 3)
            for a, b in zip(base, moved) if not np.allclose(a, b)]
    check('and moving them moves its boxes with them', len(lift) == mine
          and all(abs(z - 300.0) < 1e-3 for z in lift), lift[:4])

# 3. a landscape's own collision and navigation settings
def landscape_rows(level_name):
    flags = navregen.component_flags(level_name)
    walk = {n: w for n, _M, w in navregen.final_pieces(level_name, {}, walk=True)}
    return [(flags.get((p.get('sub') or 'persistent', p['export'])), walk.get(p['mesh']))
            for p in leveldata.level(level_name)['pieces'] if p.get('source') == 'landscape']


check('Untrained Grounds: its landscape (bUsedForNavigation false) is left out of the floor',
      landscape_rows(SAND_01) == [(navregen.NOT_NAVIGATION, None)], landscape_rows(SAND_01))
check('Sand Sea Castle: its landscape (BodyInstance NoCollision) is left out',
      landscape_rows('Scenario_Sand_02') == [(False, None)], landscape_rows('Scenario_Sand_02'))
check('Desert Wall: its landscape (OMDBlockAllNoTrap, used for navigation) is floor',
      landscape_rows('Scenario_Sand_03') == [(None, True)], landscape_rows('Scenario_Sand_03'))
# hidden pieces whose collision stops walkers, and exclusion volumes in sublevels
sand03 = leveldata.level('Scenario_Sand_03')['pieces']
walls = navregen.hidden_walls('Scenario_Sand_03')
check('Desert Wall: its hidden Scenario_Sand_03_Ground (OMDTrapGridMesh) stops walkers',
      [sand03[i]['mesh'] for i in walls] == ['Scenario_Sand_03_Ground'],
      [sand03[i]['mesh'] for i in walls])
flags = navregen.component_flags('Scenario_Sand_03')
ground = sand03[next(iter(walls))] if walls else {}
check('and the rebuild takes it', walls and navregen.collides(ground, flags), '')
lightning = [p['id'] for p in leveldata.level('Scenario_04')['pieces']
             if p.get('hidden') and p['mesh'] == 'SM_LightningInverseSphere']
check('Hidden Dock\'s hidden NoCollision lightning spheres do not',
      lightning and not set(lightning) & navregen.hidden_walls('Scenario_04'), lightning)
excl = navregen.exclusion_volumes('Scenario_Ice_02')
check('Icebound Mine: exclusion volumes are read from every package (9 persistent, 1 Castle)',
      len(excl) == 10 and len(navregen.nav_volumes('Scenario_Ice_02', (navregen.EXCLUSION,)))
      == 9, len(excl))

polys, cut, bare = gates(SAND_01)
check('Untrained Grounds rebuilds joining every route leg with floor under every spawner '
      'and door', all(polys) and not cut and not bare, (polys, cut, bare))

# 4. a failed rebuild: the map named and what to change, else the shipped floor kept
TITLE = 'Sandstorm Shelter (Scenario_Sand_04)'
SAID, LOGGED = [], []


def outcome(**patches):
    """('refused', message) or ('kept', note) from install() with module attributes patched
    ({'module__attr': f}); console lines go to LOGGED."""
    from riftlord.core import console
    saved = [(console, 'debug', console.debug), (console, 'warn', console.warn)]
    console.debug = lambda text, source='': LOGGED.append(('debug', text))
    console.warn = lambda text, source='': LOGGED.append(('warn', text))
    for key, value in patches.items():
        mod, attr = key.split('__')
        target = {'navregen': navregen, 'recastgen': recastgen}[mod]
        saved.append((target, attr, getattr(target, attr)))
        setattr(target, attr, value)
    try:
        ua, ux, notes = navregen.install(b'ua', b'ux', SAND_04, {}, log=lambda *_a: None)
    except navregen.RebuildFailed as exc:
        SAID.append(str(exc))
        return 'refused', str(exc)
    except Exception as exc:                         # noqa: BLE001
        return 'NOT PLAIN', '%s: %s' % (type(exc).__name__, exc)
    finally:
        for target, attr, value in saved:
            setattr(target, attr, value)
    SAID.extend(notes)
    return ('kept' if (ua, ux) == (b'ua', b'ux') else 'rebuilt'), ' '.join(notes)


got = outcome(navregen__anchored=lambda *_a: [])
check('no spawner or door on any floor: refused, naming them and what to change',
      got[0] == 'refused' and got[1].startswith(
          'The walkable floor for %s could not be rebuilt: Spawner MobSpawnerDoor1 at '
          '(-1690, -3850, -200)' % TITLE)
      and got[1].endswith('stand on no floor the ordinary enemies can walk. Your plan is '
                          'unchanged; move them onto the floor, or put floor under them.'), got)
KEPT = 'enemies walk the floor the level shipped with'
got = outcome(navregen__region=lambda _l: None)
check('no navigation area: the shipped floor kept, said as such',
      got == ('kept', '%s has no navigation area, so its walkable floor cannot be rebuilt; '
                      '%s.' % (TITLE, navregen.KEPT)), got)
LOGGED.clear()
got = outcome(recastgen__run=lambda *_a: [])
check('Recast finds no floor: the shipped floor kept, said as such',
      got[0] == 'kept' and got[1] == 'The walkable floor for %s could not be rebuilt, so %s.'
      % (TITLE, navregen.KEPT), got)
check('and the designer is told in the console, the detail kept at debug level',
      ('warn', got[1]) in LOGGED and any(k == 'debug' and 'Recast found no floor' in t
                                         for k, t in LOGGED), LOGGED)


def broken(*_a, **_k):
    raise IndexError('list index out of range')


LOGGED.clear()
got = outcome(recastgen__planar=broken)
check('an unforeseen error inside the rebuild keeps the shipped floor',
      got[0] == 'kept' and KEPT in got[1], got)
check('and its detail is kept for the console',
      any(k == 'debug' and 'IndexError: list index out of range' in t for k, t in LOGGED),
      LOGGED)


def no_binary(*_a, **_k):
    raise SystemExit(recastgen.UNAVAILABLE % ('upstream', '/nowhere'))


got = outcome(recastgen__binary=no_binary)
check('a missing navigation builder keeps the shipped floor', got[0] == 'kept', got)
got = outcome(navregen__check=lambda _b: ['graph 0: pointer slot 3 empty'])
check('a rebuilt floor that fails its checks keeps the shipped floor', got[0] == 'kept', got)
check('no message sends the designer to a developer, holds brackets after the map name or '
      'build jargon',
      SAID and not any('developer' in m or '(' in m.split(TITLE, 1)[-1].split(' at (')[0]
                       or buildmessage.jargon(m) for m in SAID),
      [m for m in SAID if 'developer' in m or buildmessage.jargon(m)])
check('a refused rebuild is a buildmessage.Problem, so a build shows its words',
      issubclass(navregen.RebuildFailed, buildmessage.Problem), '')

# 5. a class found only by its lower-cased asset name: late pieces
check('Wall_RB_MT_v3_1x4_Angle_C is found by case only, a class named as its asset is not',
      omd3actors.found_by_case_only(ANGLE)
      and not omd3actors.found_by_case_only('Wall_RB_Angle_1x1_C'), '')
check('its parts are read', len(omd3actors.late_parts(ANGLE)) == 5,
      len(omd3actors.late_parts(ANGLE)))
now = omd3level.read(SLUDGE)['pieces']
real = omd3actors.late_parts
omd3actors.late_parts = lambda _cls: []
try:
    before = omd3level.read(SLUDGE)['pieces']
finally:
    omd3actors.late_parts = real


def same(a, b):
    return all(a.get(k) == b.get(k) for k in ('id', 'mesh', 'matrix', 'export', 'sub',
                                               'actor', 'source'))


added = now[len(before):]
check('Sludge Shelves: every piece read before keeps its id',
      len(now) > len(before) and all(same(a, b) for a, b in zip(before, now)),
      (len(before), len(now)))
check('and the 8 Angle walls (5 parts each) come after them',
      len(added) == 40 and all(p.get('actor') == ANGLE for p in added)
      and len({p['export'] for p in added}) == 8, len(added))

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d of %d checks passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
