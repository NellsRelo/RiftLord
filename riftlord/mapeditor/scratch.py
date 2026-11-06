"""A level from scratch: a mission whose level keeps nothing of its host but what a
level needs to load.

A scratch level is an ordinary Map Editor mission over a shipped host level, with a
`scratch` block in its plan. Save, Build and Install treat it like any other mission;
`emit.build` calls into this module where a scratch level differs:

* the host's geometry sublevels are dropped from the copy's streaming list and its
  `_Lighting` is replaced by the light rig `authorlevel` lifts (sun, sky light, fog,
  post process, skybox), the arrangement `shipemit` ships converted maps with;
* the host's own decoration in the persistent level (meshes, emitters, blocking and
  kill volumes) is parked, and its gameplay actors are removed by the plan, except the
  rift, the exit point and the three player starts, which the plan moves;
* the walkable navgraph is generated from the kit floors the plan places;
* KillZ goes below the lowest floor;
* the MapData gets a minimap drawn from the floors, and the level names that MapData;
* a light bake (lightbake, lightalloc) bakes the persistent level's added pieces and the
  Static lights placed in it, and nothing of the host's (streamed_scene).

Plan block: {"scratch": {"version": 1, "base": "Scenario_01", "name": "..."}}.
"""

import copy
import functools
import json
import math
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

VERSION = 1

#: The hosts a scratch level can borrow, with what the user is told about each.
#: Scenario_01 is the only one: `authorlevel`'s light rig is lifted from its
#: `_Lighting`, it has no flight graph, one spawner and one door, and it is the host
#: `shipemit` converted maps load on.
BASES = {'Scenario_01': 'North Wing: night sky and its light rig'}
DEFAULT_BASE = 'Scenario_01'

#: The trap-grid cell the editor snaps to, in uu.
GRID_CELL = 300.0
#: How far the ground grid reaches from the origin, in uu.
GRID_HALF = 9000.0
#: Height of the ground grid and of the pre-placed actors' floor.
GROUND_Z = 0.0

#: Heights above the walkable surface, measured on Scenario_01's own navgraph:
#: rift 339.3, exit point 59.6, player starts 101.4 / 99.8 / 93.9.
RIFT_ABOVE_FLOOR = 339.3
EXIT_ABOVE_FLOOR = 59.6
START_ABOVE_FLOOR = 98.4

#: Where the pre-placed actors go, on the grid: (x, y) in uu.
RIFT_AT = (0.0, 0.0)
BREAK_STARTS = ((-300.0, 1800.0), (300.0, 1800.0))
WAVE_START = (0.0, 1200.0)
#: Player starts face away from the rift, towards where a route comes in.
START_YAW = 90.0

#: The host's sublevels a scratch level still streams, unchanged.
KEEP_HOST_STREAMS = ('Audio',)
#: The host sublevels replaced by our own package.
OWN_STREAMS = ('Lighting',)

#: Persistent-level actor classes parked in a scratch level. Measured on Scenario_01's
#: actor list: 9 StaticMeshActor, 2 SkeletalMeshActor, 7 Emitter, 4 lava bursts, 11
#: BlockingVolume, 8 OMDPlayerBlockingVolume, 1 OMDCameraBlockingVolume, 3
#: OMDCharacterKillVolume, 4 NavPowerExclusionVolume, 3 PrecomputedVisibilityVolume.
PARK_CLASSES = ('StaticMeshActor', 'SkeletalMeshActor', 'Emitter',
                'BP_LavaBurst_01_C', 'BP_LavaBurst_02_C', 'BlockingVolume',
                'OMDPlayerBlockingVolume', 'OMDCameraBlockingVolume',
                'OMDCharacterKillVolume', 'NavPowerExclusionVolume',
                'PrecomputedVisibilityVolume', 'PointLight', 'SpotLight', 'RectLight')

#: A parked host spawner's new indexInLevel is this plus its order, so the low indices
#: the host's onslaught dispatches on are free for the spawners the user adds.
PARKED_INDEX_BASE = 90

#: Kill plane depth under the lowest floor; the value `shipemit` uses.
KILL_MARGIN = 500.0

#: How far short of a whole cell a floor box may fall and still cover it, in uu.
CELL_TOLERANCE = 25.0

#: A floor turned within this many degrees of a grid axis counts as on the axes.
SQUARE_TOLERANCE = 0.5
#: A floor turned further than SQUARE_TOLERANCE but within this many degrees is walked on
#: its inscribed axis-aligned rectangle; further still, on the grid cells it covers whole.
NEAR_SQUARE = 10.0
#: Walkable-cover lattice lines closer than this (uu) are joined into one.
LINE_MERGE = 30.0
#: Floor edges within this (uu) of the same offset on the GRID_CELL lattice share a phase.
PHASE_TOLERANCE = 1.0
#: A floor kit piece's stair part whose rise leans off its main axis by more than this
#: share of it (a corner flight) is left to the floor around it.
RAMP_AXIS_LEAN = 0.5
#: The walkable cover on a lattice through the floors' edges with per-part obstacles;
#: False is the cover before 2026-10-21 (whole world-grid cells, whole-piece boxes).
COVER_LATTICE = True

#: Kit classes whose name marks a sloped walkable piece.
SLOPE_WORDS = ('Stairs', 'Ramp')
#: Fab library roles walked on as floor, and as slopes. Every other Fab role is an obstacle.
#: A terrain kit ramp (geometry.terrain.kind 'ramp', role floor) is walked as a slope.
FAB_FLOOR_ROLES = ('floor',)
FAB_SLOPE_ROLES = ('stairs',)
#: A non-floor piece blocks the floor cell under it when its box reaches lower than
#: this above the floor ...
OBSTACLE_BELOW = 192.0
#: ... and higher than this.
OBSTACLE_ABOVE = 48.0
#: Obstacle boxes grow by the agent radius before they take cells away.
AGENT_RADIUS = 42.0
#: A grown obstacle takes a cell away when it reaches this far into it on both axes.
OBSTACLE_OVERLAP = 20.0

#: True while the emitter writes an added kit piece's whole transform (apply_plan does);
#: False makes the floor generator use the translation only.
KIT_ROTATION_EMITTED = True

#: Minimap: share of the square left empty around the floors.
MAP_MARGIN = 0.12
#: Minimap: the volume's half-height, in uu.
MAP_HALF_HEIGHT = 1500.0
#: Minimap colours, measured on Scenario_01's minimap: floor RGB 0, walls RGB 255,
#: both at alpha 38 over an alpha 0 background.
MAP_ALPHA = 38
#: Minimap wall band width, as a share of the image width.
MAP_WALL_BAND = 0.01


# -- the plan ----------------------------------------------------------------------

def is_scratch(plan):
    return bool(isinstance(plan, dict) and isinstance(plan.get('scratch'), dict))


def clean(raw):
    """A plan's `scratch` block, validated, or None."""
    if not isinstance(raw, dict):
        return None
    base = str(raw.get('base') or DEFAULT_BASE)
    if base not in BASES:
        raise ValueError('no such scratch base: %r' % base)
    out = {'version': VERSION, 'base': base,
           'name': str(raw.get('name') or '').strip()[:200]}
    #: where a converted level came from (omd2record.py): {kind, name, shift}
    src = raw.get('source')
    if isinstance(src, dict) and src.get('kind') and src.get('name'):
        out['source'] = {'kind': str(src['kind'])[:40], 'name': str(src['name'])[:200]}
        if isinstance(src.get('shift'), (list, tuple)) and len(src['shift']) == 3:
            out['source']['shift'] = [float(v) for v in src['shift']]
    return out


def bases():
    return [{'name': k, 'title': v, 'default': k == DEFAULT_BASE} for k, v in BASES.items()]


def game_modes():
    """[{gameType, label, default}] for the New Level dialog: every game mode
    (riftlord.onslaught.gamemode.MODES) whose piece's supported mod is detected now
    (riftlord.core.supportedmods), Rift Defense first and the default."""
    from riftlord.core import supportedmods
    from riftlord.mapeditor import crystal
    from riftlord.onslaught import gamemode
    #: the supported mod feature each game type's piece comes from
    needs = {crystal.GAME_TYPE: crystal.FEATURE}
    out = []
    for game_type, mode in gamemode.MODES.items():
        feature = needs.get(game_type)
        try:
            if feature and not supportedmods.available(feature):
                continue
        except Exception:                            # noqa: BLE001
            continue
        out.append({'gameType': game_type, 'label': mode['label'],
                    'default': game_type == gamemode.RIFT_DEFENSE})
    return out


#: Endings stripped from a sky material's name to make its label.
SKY_NAME_ENDINGS = re.compile(r'(_INST\d*|_Inst\d*)$')


def _level_title(titles, name):
    """The mission name of level `name`, trying shorter `_` prefixes (Scenario_18_Endgame
    -> Scenario_18); None when there is none."""
    parts = (name or '').split('_')
    for n in range(len(parts), 1, -1):
        hit = titles.get('_'.join(parts[:n]))
        if hit:
            return hit
    return None


#: Name parts left out of a sky's label (Ice_Scen02_Sky_INST -> Ice Sky).
SKY_NAME_SKIP = re.compile(r'^Scen\d+$')


def sky_label(material):
    """A sky material's readable name: SnowMorning_Sky_INST -> Snow Morning Sky."""
    words = []
    for part in SKY_NAME_ENDINGS.sub('', material).split('_'):
        if not part or SKY_NAME_SKIP.match(part):
            continue
        part = re.sub(r'\d+$', '', part)
        words.extend(re.findall(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+', part) or [part])
    return ' '.join(words)


@functools.lru_cache(maxsize=4)
def _skies(base):
    from riftlord.mapeditor import leveldata
    from riftlord.build.world import skyfog
    try:
        from riftlord.build.minimap import mapdata
        titles = mapdata.mission_names()
    except Exception:                                # noqa: BLE001
        titles = {}
    own = own_sky(base)
    homes = skyfog.homes()
    materials = set(leveldata.looks().get('sky') or [])
    if own:
        materials.add(own)
    out = []
    for material in materials:
        home = (homes.get(material) or (None, None))[0]
        level = _level_title(titles, home) if home else None
        title = '%s (%s)' % (sky_label(material), level) if level else sky_label(material)
        out.append({'material': material, 'home': home, 'title': title,
                    'default': material == own})
    return sorted(out, key=lambda s: (not s['default'], s['title'].lower()))


def skies(base=DEFAULT_BASE):
    """[{material, home, title, default}] for the New Level dialog: every shipped sky
    material, titled by its name and the level it comes from (riftlord.skyfog), the host's
    own first. The sky is the dome's material only; the light rig keeps its lighting."""
    return copy.deepcopy(_skies(base))


def own_sky(base):
    """The sky material the host level paints its skybox with, or None."""
    from riftlord.mapeditor import leveldata
    try:
        return (leveldata.level(base).get('sky') or {}).get('material')
    except Exception:                                # noqa: BLE001
        return None


def _host_actors(base):
    from riftlord.mapeditor import leveldata
    from riftlord.mapeditor import planactors
    lvl = leveldata.level(base)
    actors = planactors.level_actors(base)
    return lvl, actors


def new_plan(name, base=DEFAULT_BASE, sky=None):
    """The plan of a new scratch level: the host's rift, exit point and player starts
    moved onto the grid, everything else of its gameplay removed. `sky` is a sky
    material (skies()); the host's own when None."""
    from riftlord.mapeditor import leveldata
    block = clean({'base': base, 'name': name})
    base = block['base']
    lvl, actors = _host_actors(base)
    plan = leveldata.empty_plan(base)
    plan.update({'version': 2, 'points': [], 'playerStarts': [], 'world': {},
                 'mission': {'title': block['name']} if block['name'] else {},
                 'scratch': block})
    wiring = []
    rifts = lvl.get('rifts') or []
    if len(rifts) != 1 or not actors.get('exit'):
        raise SystemExit('%s needs exactly one rift and one exit to host a new level'
                         % base)
    wiring.append({'name': rifts[0]['name'], 'kind': 'rift',
                   'at': [RIFT_AT[0], RIFT_AT[1], GROUND_Z + RIFT_ABOVE_FLOOR],
                   'rotation': 0.0})
    wiring.append({'name': actors['exit'], 'kind': 'point',
                   'at': [RIFT_AT[0], RIFT_AT[1], GROUND_Z + EXIT_ABOVE_FLOOR]})
    breaks = [s for s in actors['starts'] if s['phase'] in ('breaks', 'both')]
    waves = [s for s in actors['starts'] if s['phase'] == 'waves']
    if len(breaks) < len(BREAK_STARTS) or not waves:
        raise SystemExit('%s does not have the player starts a new level needs' % base)
    for s, (x, y) in zip(breaks, BREAK_STARTS):
        wiring.append({'name': s['export'], 'kind': 'playerStart',
                       'at': [x, y, GROUND_Z + START_ABOVE_FLOOR], 'rotation': START_YAW})
    wiring.append({'name': waves[0]['export'], 'kind': 'playerStart',
                   'at': [WAVE_START[0], WAVE_START[1], GROUND_Z + START_ABOVE_FLOOR],
                   'rotation': START_YAW})
    kept = {w['name'] for w in wiring}
    for s in actors['starts']:
        if s['export'] not in kept:
            wiring.append({'name': s['export'], 'kind': 'playerStart', 'gone': True})
    for name in actors['spawners']:
        wiring.append({'name': name, 'kind': 'spawner', 'gone': True})
    for name in actors['doors']:
        wiring.append({'name': name, 'kind': 'door', 'gone': True})
    for name in actors['points']:
        if name != actors['exit']:
            wiring.append({'name': name, 'kind': 'point', 'gone': True})
    plan['wiring'] = wiring
    if sky and sky != own_sky(base):
        plan['look'] = {'sky': str(sky)}
    return plan


def view(base):
    """What the Map Editor draws for a scratch level on `base`: the host's sky and the
    actors the plan keeps, no geometry."""
    from riftlord.formats.omd3 import omd3level
    lvl, _actors = _host_actors(base)
    out = {k: v for k, v in lvl.items()
           if k not in ('pieces', 'lights', 'vfx', 'sounds', 'spawners', 'doors', 'points')}
    #: the host's actors where a new level puts them
    at = {w['name']: w['at'] for w in new_plan('', base)['wiring'] if w.get('at')}
    rifts = [dict(r, loc=at.get(r['name'], r['loc'])) for r in lvl.get('rifts') or []]
    points = {k: dict(v, location=at.get(k, v.get('location')))
              for k, v in lvl['points'].items() if v.get('exit')}
    starts = [dict(s, location=at.get(s['export'], s.get('location')))
              for s in lvl.get('player_starts') or [] if s['export'] in at]
    out.update({'pieces': [p for p in lvl['pieces'] if p['mesh'] in omd3level.SKYBOX_MESHES],
                'lights': [], 'vfx': [], 'sounds': [], 'spawners': [], 'doors': {},
                'rifts': rifts, 'player_starts': starts, 'points': points,
                'sublevels': [], 'subPackages': {}, 'materials': {},
                'materialTints': {},
                'bounds': [[-GRID_HALF, -GRID_HALF, GROUND_Z],
                           [GRID_HALF, GRID_HALF, GROUND_Z]],
                'focus': [[-GRID_HALF / 3, GRID_HALF / 3], [-GRID_HALF / 3, GRID_HALF / 3],
                          [GROUND_Z, GROUND_Z + RIFT_ABOVE_FLOOR]],
                'scratch': {'base': base, 'grid': GRID_CELL, 'half': GRID_HALF,
                            'ground': GROUND_Z}})
    return out


def mesh_names(base):
    """The meshes a scratch level on `base` draws of its host: the sky's."""
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import leveldata
    return sorted({p['mesh'] for p in leveldata.level(base)['pieces']
                   if p['mesh'] in omd3level.SKYBOX_MESHES})


def still_needed(plan):
    """What the user still has to add before the level plays, as short lines."""
    out = []
    if not floor_boxes(plan):
        out.append('a floor: kit pieces with a floor trap grid, or Fab floor pieces')
    if not plan.get('spawners'):
        out.append('a spawner')
    if not plan.get('points'):
        out.append('a route: path points from the spawner to the rift')
    return out


# -- floors ------------------------------------------------------------------------

def _apply(m3, v):
    return tuple(sum(m3[i][k] * v[k] for k in range(3)) for i in range(3))


def _piece_matrix(item):
    """(rotation 3x3, translation) of an added piece as the emitter writes it."""
    m = item['matrix']
    t = (m[12], m[13], m[14])
    if KIT_ROTATION_EMITTED or item.get('kind') != 'kit':
        cols = [(m[0], m[1], m[2]), (m[4], m[5], m[6]), (m[8], m[9], m[10])]
        return tuple(tuple(cols[j][i] for j in range(3)) for i in range(3)), t
    return ((1, 0, 0), (0, 1, 0), (0, 0, 1)), t


def _inverse(m):
    """The inverse of a 3x3 rows matrix."""
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise SystemExit('an added piece is scaled flat')
    return ((( e * i - f * h) / det, -(b * i - c * h) / det, ( b * f - c * e) / det),
            (-(d * i - f * g) / det, ( a * i - c * g) / det, -(a * f - c * d) / det),
            (( d * h - e * g) / det, -(a * h - b * g) / det, ( a * e - b * d) / det))


def _fab_entry(item):
    from riftlord.mapeditor import fablibrary
    try:
        return fablibrary.entry_for(item.get('pack'), item.get('object'), item.get('source'))
    except Exception:
        return None


def _piece_local(item, cat):
    """((lo, hi) of an added kit or Fab piece's meshes in the piece's own frame, rotation
    3x3, translation), or None."""
    if item.get('kind') == 'fab':
        entry = _fab_entry(item)
        if not entry:
            return None
        rot, t = _piece_matrix(item)
        return (list(entry['bounds']['min']), list(entry['bounds']['max'])), rot, t
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import leveldata
    parts = leveldata.kit_parts(item['name']).get('parts') or []
    rot, t = _piece_matrix(item)
    pts = []
    for part in parts:
        info = cat.get(part.get('mesh'))
        if not info:
            continue
        o, e = info['origin'], info['extent']
        local = omd3level._matrix(part.get('loc') or (0, 0, 0),
                                  part.get('rot') or (0, 0, 0),
                                  part.get('scale') or (1, 1, 1))
        for dx in (-1, 1):
            for dy in (-1, 1):
                for dz in (-1, 1):
                    c = (o[0] + dx * e[0], o[1] + dy * e[1], o[2] + dz * e[2])
                    pts.append(tuple(sum(local[r][k] * c[k] for k in range(3))
                                     + local[r][3] for r in range(3)))
    if not pts:
        return None
    lo = [min(p[k] for p in pts) for k in range(3)]
    hi = [max(p[k] for p in pts) for k in range(3)]
    return (lo, hi), rot, t


def _piece_box(item, cat):
    """World (lo, hi) of an added kit piece's meshes, or None."""
    got = _piece_local(item, cat)
    if got is None:
        return None
    (lo, hi), rot, t = got
    xs, ys, zs = [], [], []
    for x in (lo[0], hi[0]):
        for y in (lo[1], hi[1]):
            for z in (lo[2], hi[2]):
                c = _apply(rot, (x, y, z))
                xs.append(c[0] + t[0])
                ys.append(c[1] + t[1])
                zs.append(c[2] + t[2])
    return [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]


def _turned_cells(item, cat, box):
    """[(lo, hi)] one per GRID_CELL cell, on a grid through the piece's origin, that a floor
    turned off the grid axes covers whole."""
    (llo, lhi), rot, t = _piece_local(item, cat)
    inv = _inverse(rot)
    lo, hi = box
    out = []
    i0, i1 = int(math.floor((lo[0] - t[0]) / GRID_CELL)), int(math.ceil((hi[0] - t[0]) / GRID_CELL))
    j0, j1 = int(math.floor((lo[1] - t[1]) / GRID_CELL)), int(math.ceil((hi[1] - t[1]) / GRID_CELL))
    for i in range(i0, i1):
        for j in range(j0, j1):
            inside = True
            for cx in (i * GRID_CELL + CELL_TOLERANCE, (i + 1) * GRID_CELL - CELL_TOLERANCE):
                for cy in (j * GRID_CELL + CELL_TOLERANCE,
                           (j + 1) * GRID_CELL - CELL_TOLERANCE):
                    p = _apply(inv, (cx, cy, hi[2] - t[2]))
                    if not (llo[0] <= p[0] <= lhi[0] and llo[1] <= p[1] <= lhi[1]):
                        inside = False
            if inside:
                out.append(([t[0] + i * GRID_CELL, t[1] + j * GRID_CELL, lo[2]],
                            [t[0] + (i + 1) * GRID_CELL, t[1] + (j + 1) * GRID_CELL, hi[2]]))
    return out


def _yaw_of(item):
    rot, _t = _piece_matrix(item)
    return math.degrees(math.atan2(rot[1][0], rot[0][0]))


def _whole_cells(lo, hi):
    """(lo, hi) shrunk in x and y to the grid cells the box covers whole, or (None,
    None): the cover before COVER_LATTICE."""
    lo, hi = list(lo), list(hi)
    for k in (0, 1):
        lo[k] = GRID_CELL * math.ceil((lo[k] - CELL_TOLERANCE) / GRID_CELL)
        hi[k] = GRID_CELL * math.floor((hi[k] + CELL_TOLERANCE) / GRID_CELL)
        if hi[k] <= lo[k]:
            return None, None
    return lo, hi


def _inscribed(item, cat, box):
    """(lo, hi) of the largest axis-aligned rectangle, centred on the piece, inside a floor
    turned a little off the grid axes, or (None, None)."""
    (llo, lhi), rot, t = _piece_local(item, cat)
    cols = [[rot[r][c] for r in range(3)] for c in range(3)]
    a = (lhi[0] - llo[0]) / 2.0 * math.hypot(*cols[0])
    b = (lhi[1] - llo[1]) / 2.0 * math.hypot(*cols[1])
    turn = math.degrees(math.atan2(cols[0][1], cols[0][0]))
    quarters = int(round(turn / 90.0))
    off = math.radians(turn - 90.0 * quarters)
    ax, ay = (a, b) if quarters % 2 == 0 else (b, a)
    s, c = abs(math.sin(off)), math.cos(off)
    den = c * c - s * s
    if den <= 1e-9:
        return None, None
    u, v = (ax * c - ay * s) / den, (ay * c - ax * s) / den
    if u <= 0 or v <= 0:
        return None, None
    mid = [(llo[k] + lhi[k]) / 2.0 for k in range(3)]
    centre = _apply(rot, mid)
    cx, cy = centre[0] + t[0], centre[1] + t[1]
    return [cx - u, cy - v, box[0][2]], [cx + u, cy + v, box[1][2]]


def _walkable(row):
    if row and row.get('kind') == 'fab':
        role = row.get('role')
        return 'floor' if role in FAB_FLOOR_ROLES else 'slope' if role in FAB_SLOPE_ROLES else None
    if not row or row.get('kind') != 'kit':
        return None
    if any(w in row['name'] for w in SLOPE_WORDS):
        return 'slope'
    if 'Floor' in (row.get('surfaces') or []):
        return 'floor'
    return None


def _fab_row(entry):
    """The walkability row of a Fab catalogue entry, or None."""
    if not entry:
        return None
    ramp = ((entry.get('geometry') or {}).get('terrain') or {}).get('kind') == 'ramp'
    return {'kind': 'fab', 'role': FAB_SLOPE_ROLES[0] if ramp else entry.get('role')}


def walk_box(item, box):
    """A flat floor piece's world box (lo, hi) with its top at the height a walker stands on
    it (navregen.item_walk_top), where the piece has an upward face."""
    from riftlord.build.navigation import navregen
    z = navregen.item_walk_top(item)
    if z is None or z < box[0][2]:
        return box
    return list(box[0]), [box[1][0], box[1][1], z]


#: _walk_parts of an added kit piece over its own translation, by its piece and turn
_WALK_PARTS = {}


def _walk_parts(item):
    """(flat, ramps) of a floor kit piece with stair parts (recastgen.is_ramp) rising along
    one axis, or None: flat = (lo, hi) XY of its upward faces within navregen.CELL_HEIGHT of
    the height a walker stands on it (navregen.walk_top), or None; ramps = [(lo, hi, yaw,
    falling)], one per such stair part: the box of its upward faces, yaw 90 for a rise
    along x and 0 along y, and falling True where it drops as that coordinate grows."""
    import numpy as np
    from riftlord.build.navigation import navregen
    from riftlord.build.navigation import recastgen
    m = item.get('matrix')
    if not m or item.get('kind') != 'kit':
        return None
    key = json.dumps([item.get(k) for k in ('name', 'collision')]
                     + [round(v, 6) for v in m[:12]], default=str)
    if key not in _WALK_PARTS:
        at = dict(item, matrix=list(m[:12]) + [0.0, 0.0, 0.0, 1.0])
        pieces = navregen.added_meshes(at)
        got = None
        if any(recastgen.is_ramp(name) for name, _M in pieces):
            top = navregen.walk_top(pieces)
            faces = [(name, navregen._up_faces(name, np.asarray(M, np.float64)))
                     for name, M in pieces]
            ramps = []
            for name, f in faces:
                if not recastgen.is_ramp(name) or not len(f):
                    continue
                #: the faces' plane z = a + gx x + gy y, weighted by each face's area from above
                c = f.mean(axis=1)
                w = np.abs(np.cross(f[:, 1] - f[:, 0], f[:, 2] - f[:, 0])[:, 2]) / 2.0
                A = np.c_[np.ones(len(c)), c[:, 0], c[:, 1]] * np.sqrt(w)[:, None]
                g = np.linalg.lstsq(A, c[:, 2] * np.sqrt(w), rcond=None)[0][1:]
                axis = 0 if abs(g[0]) >= abs(g[1]) else 1
                if abs(g[axis]) < 1e-6 or abs(g[1 - axis]) > RAMP_AXIS_LEAN * abs(g[axis]):
                    continue
                lo, hi = f.min(axis=(0, 1)), f.max(axis=(0, 1))
                ramps.append((lo.tolist(), hi.tolist(), 90.0 if axis == 0 else 0.0,
                              bool(g[axis] < 0)))
            if ramps:
                flat = None
                if top is not None:
                    every = np.vstack([f for _n, f in faces])
                    near = every[(np.abs(every[:, :, 2] - top) <= navregen.CELL_HEIGHT).all(axis=1)]
                    if len(near):
                        flat = (near[:, :, :2].min(axis=(0, 1)).tolist(),
                                near[:, :, :2].max(axis=(0, 1)).tolist())
                got = (flat, ramps)
        _WALK_PARTS[key] = got
    got = _WALK_PARTS[key]
    if got is None:
        return None
    t = m[12:15]
    flat, ramps = got
    if flat is not None:
        flat = ([flat[0][0] + t[0], flat[0][1] + t[1]], [flat[1][0] + t[0], flat[1][1] + t[1]])
    return flat, [([lo[k] + t[k] for k in range(3)], [hi[k] + t[k] for k in range(3)], yaw, falling)
                  for lo, hi, yaw, falling in ramps]


def floor_boxes(plan):
    """[(lo, hi, sloped, yaw[, falling])] for the plan's added walkable kit and Fab pieces;
    `falling` on the stair parts of a floor kit piece (_walk_parts)."""
    return _boxes(plan, obstacles=False)[0]


def _boxes(plan, obstacles=True):
    """(floors, others): the plan's walkable floor boxes, and with `obstacles` the outlines
    of its other kit and Fab pieces (empty without)."""
    from riftlord.formats.omd3 import meshcat
    from riftlord.mapeditor import leveldata
    cat = meshcat.load()
    rows = {r['name']: r for r in leveldata.catalogue()}
    floors, others = [], []
    for item in plan.get('added') or []:
        if item.get('kind') == 'fab':
            row = _fab_row(_fab_entry(item))
        else:
            row = rows.get(item.get('name'))
        if not row or row.get('kind') not in ('kit', 'fab'):
            continue
        box = _piece_box(item, cat)
        if box is None:
            continue
        kind = _walkable(row)
        if kind == 'floor':
            box = walk_box(item, box)
        yaw = _yaw_of(item)
        off = abs(yaw / 90.0 - round(yaw / 90.0)) * 90.0
        if not COVER_LATTICE:
            if kind == 'floor' and off > SQUARE_TOLERANCE:
                floors.extend((lo, hi, False, yaw) for lo, hi in _turned_cells(item, cat, box))
            elif kind:
                lo, hi = _whole_cells(box[0], box[1])
                if lo is not None:
                    floors.append((lo, hi, kind == 'slope', yaw))
            elif obstacles:
                (x0, y0, _z0), (x1, y1, _z1) = box
                r = AGENT_RADIUS
                others.append((box[0], box[1], [(x0 - r, y0 - r), (x1 + r, y0 - r),
                                                (x1 + r, y1 + r), (x0 - r, y1 + r)]))
            continue
        if kind == 'floor' and off > NEAR_SQUARE:
            floors.extend((lo, hi, False, yaw) for lo, hi in _turned_cells(item, cat, box))
        elif kind == 'floor' and off > SQUARE_TOLERANCE:
            lo, hi = _inscribed(item, cat, box)
            if lo is not None:
                floors.append((lo, hi, False, yaw))
        elif kind == 'floor' and (parts := _walk_parts(item)):
            flat, ramps = parts
            if flat is None:
                floors.append((list(box[0]), list(box[1]), False, yaw))
            else:
                floors.append(([flat[0][0], flat[0][1], box[0][2]],
                               [flat[1][0], flat[1][1], box[1][2]], False, yaw))
            floors.extend((lo, hi, True, ramp_yaw, falling)
                          for lo, hi, ramp_yaw, falling in ramps)
        elif kind:
            floors.append((list(box[0]), list(box[1]), kind == 'slope', yaw))
        elif not obstacles:
            continue
        elif item.get('kind') == 'kit':
            others.extend(_part_outlines(item, cat, AGENT_RADIUS))
        else:
            others.append((box[0], box[1], _footprint(item, cat, AGENT_RADIUS)))
    #: grinder pits are walked over like the floor around them (riftlord/compounds.py)
    from riftlord.mapeditor import compounds
    floors.extend(compounds.pit_floors(plan))
    return floors, others


def _part_outlines(item, cat, grow):
    """[(lo, hi, XY outline grown by `grow`)] per mesh of a kit piece: an archway blocks
    with its sides, not across its opening."""
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import leveldata
    rot, t = _piece_matrix(item)
    whole = [[rot[r][c] for c in range(3)] + [t[r]] for r in range(3)] + [[0, 0, 0, 1]]
    out = []
    for part in leveldata.kit_parts(item['name']).get('parts') or []:
        info = cat.get(part.get('mesh'))
        if not info:
            continue
        m = omd3level._matmul(whole, omd3level._matrix(part.get('loc') or (0, 0, 0),
                                                         part.get('rot') or (0, 0, 0),
                                                         part.get('scale') or (1, 1, 1)))
        o, e = info['origin'], info['extent']
        pad = [grow / max(math.sqrt(sum(m[r][k] ** 2 for r in range(3))), 1e-6)
               for k in range(3)]
        pts = []
        for dx in (-1, 1):
            for dy in (-1, 1):
                for dz in (-1, 1):
                    c = (o[0] + dx * (e[0] + pad[0]), o[1] + dy * (e[1] + pad[1]),
                         o[2] + dz * e[2])
                    pts.append(tuple(sum(m[r][k] * c[k] for k in range(3)) + m[r][3]
                                     for r in range(3)))
        lo = [min(p[k] for p in pts) for k in range(3)]
        hi = [max(p[k] for p in pts) for k in range(3)]
        out.append((lo, hi, _hull([(p[0], p[1]) for p in pts])))
    return out


def _hull(points):
    """Convex hull of 2D points, counter-clockwise (monotone chain)."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def turn(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _footprint(item, cat, grow):
    """The XY outline of a piece's own (turned) box, grown by `grow` uu on each side."""
    (lo, hi), rot, t = _piece_local(item, cat)
    cols = [[rot[r][c] for r in range(3)] for c in range(3)]
    pad = [grow / max(math.hypot(*cols[k]), 1e-6) for k in range(3)]
    pts = []
    for x in (lo[0] - pad[0], hi[0] + pad[0]):
        for y in (lo[1] - pad[1], hi[1] + pad[1]):
            for z in (lo[2], hi[2]):
                c = _apply(rot, (x, y, z))
                pts.append((c[0] + t[0], c[1] + t[1]))
    return _hull(pts)


def _clip(poly, x0, y0, x1, y1):
    """A convex polygon clipped to the rectangle (Sutherland-Hodgman)."""
    def cut(pts, inside, cross):
        out = []
        for k, cur in enumerate(pts):
            prev = pts[k - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
        return out

    def at_x(x):
        return lambda a, b: (x, a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]))

    def at_y(y):
        return lambda a, b: (a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]), y)
    pts = list(poly)
    for inside, cross in ((lambda p: p[0] >= x0, at_x(x0)), (lambda p: p[0] <= x1, at_x(x1)),
                          (lambda p: p[1] >= y0, at_y(y0)), (lambda p: p[1] <= y1, at_y(y1))):
        if not pts:
            break
        pts = cut(pts, inside, cross)
    return pts


def _phase(edges):
    """The floor edge the most floor edges lie on the GRID_CELL lattice of, within
    PHASE_TOLERANCE; of equal counts, the lowest edge."""
    import numpy as np
    e = np.sort(np.asarray(edges, np.float64))
    d = (e[None, :] - e[:, None] + GRID_CELL / 2.0) % GRID_CELL - GRID_CELL / 2.0
    return float(e[int(np.argmax((np.abs(d) <= PHASE_TOLERANCE).sum(axis=1)))])


def _lattice(floors):
    """(xs, ys): ascending lines through every floor edge and every GRID_CELL step from the
    floors' own lattice phase (_phase) between them, lines closer than LINE_MERGE joined (a
    floor edge wins over a grid line)."""
    out = []
    for axis in (0, 1):
        edges = [b[0][axis] for b in floors] + [b[1][axis] for b in floors]
        origin = _phase(edges)
        first = origin + GRID_CELL * math.floor((min(edges) - origin) / GRID_CELL)
        last = origin + GRID_CELL * math.ceil((max(edges) - origin) / GRID_CELL)
        grid = [first + GRID_CELL * k for k in range(int(round((last - first) / GRID_CELL)) + 1)]
        values = sorted([(v, True) for v in edges] + [(v, False) for v in grid])
        groups = []
        for v, is_edge in values:
            if groups and v - groups[-1][0][0] <= LINE_MERGE:
                groups[-1].append((v, is_edge))
            else:
                groups.append([(v, is_edge)])
        lines = []
        for g in groups:
            own = [v for v, is_edge in g if is_edge]
            lines.append(sum(own) / len(own) if own else g[0][0])
        out.append(lines)
    return out[0], out[1]


def _snapped(floors, lines):
    """The floors with their x and y edges moved onto the nearest lattice line; a floor
    that closes up is dropped."""
    import bisect

    def near(v, ls):
        k = bisect.bisect_left(ls, v)
        return min(ls[max(0, k - 1):k + 1], key=lambda line: abs(line - v))
    out = []
    for box in floors:
        lo, hi = box[0], box[1]
        lo2 = [near(lo[0], lines[0]), near(lo[1], lines[1]), lo[2]]
        hi2 = [near(hi[0], lines[0]), near(hi[1], lines[1]), hi[2]]
        if hi2[0] - lo2[0] > 1e-6 and hi2[1] - lo2[1] > 1e-6:
            out.append((lo2, hi2) + tuple(box[2:]))
    return out


def floor_quads(plan, floors=None):
    """Walkable quads for the plan's floors (`floors`, from _boxes, when given), before
    walls take cells away: the floor's heights, which cover() keeps."""
    from riftlord.build.navigation import navsurffloors
    if floors is None:
        floors = floor_boxes(plan)
    if not floors:
        return []
    if COVER_LATTICE:
        lines = _lattice(floors)
        return navsurffloors.cover(_snapped(floors, lines), cell=GRID_CELL, lines=lines)
    return navsurffloors.cover(floors, cell=GRID_CELL)


def cover(plan):
    """Walkable quads for the plan's floors, with cells under walls taken out."""
    floors, others = _boxes(plan)
    polys = floor_quads(plan, floors)
    if not polys:
        return []

    def blocks(lo, hi, outline, x0, y0, x1, y1, cz):
        """The piece's grown outline reaches OBSTACLE_OVERLAP into the quad on both axes."""
        if not (lo[2] < cz + OBSTACLE_BELOW and hi[2] > cz + OBSTACLE_ABOVE):
            return False
        inside = _clip(outline, x0, y0, x1, y1)
        if len(inside) < 3:
            return False
        return (max(p[0] for p in inside) - min(p[0] for p in inside) > OBSTACLE_OVERLAP
                and max(p[1] for p in inside) - min(p[1] for p in inside) > OBSTACLE_OVERLAP)

    #: floor a plan door's opening crosses stays walkable (riftlord/compounds.py)
    from riftlord.mapeditor import compounds
    openings = compounds.door_openings(plan)
    out = []
    for q in polys:
        x0, x1 = min(v[0] for v in q), max(v[0] for v in q)
        y0, y1 = min(v[1] for v in q), max(v[1] for v in q)
        cz = sum(v[2] for v in q) / len(q)
        if not any(blocks(lo, hi, outline, x0, y0, x1, y1, cz) for lo, hi, outline in others) \
                or compounds.crosses(q, openings, OBSTACLE_BELOW):
            out.append(q)
    return out


def surface_buffer(plan):
    """(buffer or None, note) for the plan's walkable navgraph."""
    from riftlord.build.navigation import navsurfgen
    polys = cover(plan)
    if not polys:
        return None, "no floor placed yet, so the host's walkable graph is kept"
    return (navsurfgen.buffer(polys, navsurfgen.bbox_of(polys)),
            'walkable graph from %d floor quad(s)' % len(polys))


def install_surface(uasset, uexp, plan):
    """(uasset, uexp, notes) with the generated walkable graph installed."""
    from riftlord.build.navigation import navsurfinject
    buf, note = surface_buffer(plan)
    if buf is None:
        return uasset, uexp, [note]
    ua, ux, checks = navsurfinject.set_surface_bytes(uasset, uexp, buf)
    bad = [name for name, ok in checks if not ok]
    if bad:
        raise SystemExit('the walkable graph did not install: %s' % '; '.join(bad))
    return ua, ux, [note]


def kill_z(plan):
    floors = floor_boxes(plan)
    low = min([b[0][2] for b in floors] + [GROUND_Z])
    return low - KILL_MARGIN


def prepare(plan):
    """A copy of the plan as a build should see it: KillZ under the floors unless the
    user set one."""
    plan = copy.deepcopy(plan)
    world = plan.setdefault('world', {})
    if world.get('killZ') is None:
        world['killZ'] = kill_z(plan)
    return plan


# -- the level copy ----------------------------------------------------------------

def taken_indices(level_name, plan):
    """Spawner indices the host's LIVE spawners hold; parked ones give theirs up."""
    from riftlord.mapeditor import planactors
    gone = {w['name'] for w in plan.get('wiring') or [] if w.get('gone')}
    level = planactors.level_actors(level_name)
    return {sp['index'] for name, sp in level['spawners'].items()
            if name not in gone and isinstance(sp.get('index'), int)}


def park_host(builder, plan, level_name, by_name, report):
    """Park the host's decoration and re-index its removed spawners."""
    from riftlord.build import levelbuild
    from riftlord.build import apply
    from riftlord.mapeditor import geomedits
    from riftlord.mapeditor import planactors
    parked = 0
    for ref in builder.actor_list():
        if ref <= 0:
            continue
        if apply._class_at(builder.pkg, ref - 1) in PARK_CLASSES:
            apply.place_whole(builder, ref, location=apply.PARKED)
            geomedits.hide_markers(builder, ref)
            parked += 1
    level = planactors.level_actors(level_name)
    gone = [w['name'] for w in plan.get('wiring') or []
            if w.get('gone') and w['name'] in level['spawners']]
    for n, name in enumerate(sorted(gone)):
        index = by_name.get(name)
        if index:
            builder.set_property(index, 'indexInLevel', 'IntProperty',
                                 struct.pack('<i', PARKED_INDEX_BASE + n))
    report.setdefault('notes', []).append(
        'new level: %d host decoration actor(s) parked, %d host spawner(s) re-indexed'
        % (parked, len(gone)))
    return parked


def streams(level_name, mod, plan=None):
    """(sublevels remap, keep_sublevels, extra_files, report) for makemod.build. The
    plan's sky (`look.sky`), lighting (riftlord.build.world.lighting) and scene settings
    are written into the light rig."""
    from riftlord.build import authorlevel
    from riftlord.build.minimap import mapdata
    host = mapdata.map_path(level_name)
    root = '/Game/Mods/%s' % mod
    ours = {s: '%s/Maps/%s_Map01/%s_Map01_%s' % (root, mod, mod, s) for s in OWN_STREAMS}
    host_of = {s: '%s_%s' % (host, s) for s in OWN_STREAMS + KEEP_HOST_STREAMS}
    light, report = authorlevel.lighting(mod)
    light, report['look'] = _look_onto(light, '%s_Map01_Lighting' % mod, plan)
    extra = {ours['Lighting']: (light, '.umap')}
    remap = {host_of[s]: ours[s] for s in OWN_STREAMS}
    keep = [host_of[s] for s in OWN_STREAMS + KEEP_HOST_STREAMS]
    return remap, keep, extra, {'lighting': report, 'streams': sorted(keep)}


def streamed_scene(plan):
    """What a bake of the plan's level keeps of its host (lightbaker.bake spec keys): {}
    for a level that streams its host whole; for a level from scratch, no host package's
    Static lights (`streamed` []), none of its pieces (`host` False, park_host parks the
    persistent level's), no Static sky (the light rig is authorlevel's, all Movable) and
    no host lava (`lava_emitters` False)."""
    if not is_scratch(plan):
        return {}
    return {'streamed': [], 'host': False, 'static_sky': False, 'lava_emitters': False}


def _look_onto(blobs, object_name, plan):
    """((uasset, uexp), report) of the light rig with the plan's sky painted on its skybox
    and the plan's lighting (riftlord.build.world.lighting) and scene settings written in."""
    from riftlord.build.world import levelfx
    from riftlord.build.world import lighting
    plan = lighting.migrated(plan)
    look = (plan or {}).get('look') or {}
    if not look.get('sky') and not levelfx.clean((plan or {}).get('world')) and \
            not lighting.edits_for(lighting.rig(), lighting.RIG_TAG, plan):
        return blobs, {}
    from riftlord.build import levelbuild
    from riftlord.build import apply
    builder = levelbuild.Builder.around(blobs[0], blobs[1], name=object_name)
    got = {'notes': [], 'skipped': []}
    if look.get('sky'):
        apply.paint_look(builder, {'look': {'sky': look['sky']}}, got)
    got['lighting'] = lighting.write_rig(builder, plan, got)
    got['scene'] = levelfx.apply(builder, (plan or {}).get('world') or {}, got)
    return builder.write(), got


def retarget_map_data(uasset, uexp, level_name, md_path, tex_path):
    """The level copy with its MapData and minimap imports pointed at ours."""
    from riftlord.build.minimap import mapdata
    from riftlord.build import modtable
    from riftlord.formats.ue.uassetio import Package
    host_md = '%s_MapData' % mapdata.map_path(level_name)
    host_vols = (mapdata.read(level_name) or {}).get('volumes') or [{}]
    swaps = {host_md: md_path}
    # one texture per MapData element when `tex_path` is a list
    for k, vol in enumerate(host_vols if isinstance(tex_path, (list, tuple)) else host_vols[:1]):
        host_tex = (vol.get('texture') or '').split('.')[0]
        path = tex_path[k] if isinstance(tex_path, (list, tuple)) else tex_path
        if host_tex and path:
            swaps.setdefault(host_tex, path)
    pkg = Package(uasset, uexp)
    pkg.load_payloads()
    changed = modtable.retarget(pkg, exact=swaps)
    ua, ux = pkg.write()
    return ua, ux, changed


#: Class of the level actor whose brush transform the HUD places minimap dots by.
MAP_VOLUME_CLASS = 'OMDMapVolume'
#: How close (uu) a level's map volume actor sits to the MapData volume it pairs with.
MAP_VOLUME_MATCH = 1.0
#: A map volume brush's transform properties at their engine defaults, which the cooker
#: leaves out of the export.
BRUSH_DEFAULTS = {'RelativeLocation': (0.0, 0.0, 0.0), 'RelativeRotation': (0.0, 0.0, 0.0),
                  'RelativeScale3D': (1.0, 1.0, 1.0)}


def frame_volume_actors(uasset, uexp, level_name, volume):
    """The level copy with every OMDMapVolume actor's brush moved, turned and scaled to
    cover `volume`, as `set_volume` frames the MapData. Each actor's brush size is read
    off the host MapData volume at the same place. Returns (uasset, uexp, count)."""
    from riftlord.build.minimap import mapdata
    from riftlord.formats.ue.uassetio import Package
    host = (mapdata.read(level_name) or {}).get('volumes') or []
    pkg = Package(uasset, uexp)
    pkg.load_payloads()
    names = [n for n, _h in pkg.names]

    def import_class(index):
        """The object name of the class import `index` names."""
        if index >= 0 or -index - 1 >= len(pkg.imports):
            return None
        at = struct.unpack_from('<i', pkg.imports[-index - 1], 20)[0]
        return names[at] if 0 <= at < len(names) else None

    #: one volume per host MapData element when `volume` is a list
    per = volume if isinstance(volume, (list, tuple)) else None
    count = 0
    for actor in pkg.exports:
        if import_class(struct.unpack_from('<i', actor['meta'], 0)[0]) != MAP_VOLUME_CLASS:
            continue
        data = actor['data']
        root = [struct.unpack_from('<i', data, at)[0]
                for nm, _k, _s, at, _z in _tags(data, names, 0, len(data))
                if nm == 'RootComponent']
        if not root or not 0 < root[0] <= len(pkg.exports):
            raise SystemExit('%s has a map volume with no brush to frame' % level_name)
        comp = pkg.exports[root[0] - 1]
        fields = {nm: at for nm, _k, _s, at, _z in _tags(comp['data'], names, 0, len(comp['data']))}
        missing = [f for f in BRUSH_DEFAULTS if f not in fields]
        if missing:
            # a property at its default is not stored: add it with that value to rewrite
            from riftlord.build import levelbuild
            b = levelbuild.Builder.__new__(levelbuild.Builder)
            b.pkg = pkg
            for f in missing:
                b.set_vector(root[0], f, BRUSH_DEFAULTS[f],
                             'Rotator' if f == 'RelativeRotation' else 'Vector')
            names = [n for n, _h in pkg.names]
            fields = {nm: at for nm, _k, _s, at, _z in _tags(comp['data'], names, 0,
                                                              len(comp['data']))}
        cdata = bytearray(comp['data'])
        loc = struct.unpack_from('<3f', cdata, fields['RelativeLocation'])
        scale = struct.unpack_from('<3f', cdata, fields['RelativeScale3D'])
        k_old = next((k for k, v in enumerate(host) if max(abs(a - b) for a, b in zip(
            v['translation'], loc)) <= MAP_VOLUME_MATCH), None)
        if k_old is None:
            raise SystemExit('%s has a map volume its MapData does not describe' % level_name)
        old = host[k_old]
        volume = per[k_old] if per else volume
        want = volume['extent']
        yaw = math.degrees(mapdata._yaw_of(volume['rotation']))
        # the brush's own half-size; a quarter turn puts its local X along world Y
        ex, ey, ez = old['extent']
        if abs(math.sin(mapdata._yaw_of(old['rotation']))) > 0.5:
            ex, ey = ey, ex
        brush = (ex / scale[0], ey / scale[1], ez / scale[2])
        struct.pack_into('<3f', cdata, fields['RelativeLocation'], *volume['translation'])
        struct.pack_into('<3f', cdata, fields['RelativeRotation'], 0.0, yaw, 0.0)
        struct.pack_into('<3f', cdata, fields['RelativeScale3D'],
                         *(w / b for w, b in zip(want, brush)))
        comp['data'] = bytes(cdata)
        count += 1
    ua, ux = pkg.write()
    return ua, ux, count


# -- the minimap -------------------------------------------------------------------

def _tags(data, names, start, end):
    """[(name, kind, struct name, payload start, size)] of a tagged list in data[start:end]."""
    out, o = [], start
    while o + 8 <= end:
        nm = names[struct.unpack_from('<i', data, o)[0]]
        if nm == 'None':
            out.append((nm, None, None, o + 8, 0))
            break
        kind = names[struct.unpack_from('<i', data, o + 8)[0]]
        size = struct.unpack_from('<i', data, o + 16)[0]
        sname = None
        if kind == 'StructProperty':
            sname = names[struct.unpack_from('<i', data, o + 24)[0]]
            head = 49
        elif kind == 'BoolProperty':
            head, size = 26, 0
        elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            head = 33
        else:
            head = 25
        out.append((nm, kind, sname, o + head, size))
        o += head + size
    return out


def volume_for(plan):
    """{translation, rotation, extent} of a minimap volume framing the floors."""
    floors = floor_boxes(plan)
    if floors:
        xs = [b[0][0] for b in floors] + [b[1][0] for b in floors]
        ys = [b[0][1] for b in floors] + [b[1][1] for b in floors]
    else:
        xs = [-GRID_HALF / 3, GRID_HALF / 3]
        ys = [-GRID_HALF / 3, GRID_HALF / 3]
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    half = max(max(xs) - min(xs), max(ys) - min(ys)) / 2.0
    half = max(half, GRID_CELL) / (1.0 - 2.0 * MAP_MARGIN)
    return {'translation': (cx, cy, GROUND_Z), 'rotation': (0.0, 0.0, 0.0, 1.0),
            'extent': (half, half, MAP_HALF_HEIGHT)}


def set_volume(md_files, volume, tex_path=None):
    """MapData files with every MapVolumes element framed on `volume` (None keeps each
    element's own frame) and, with `tex_path`, showing that texture. `volume` and
    `tex_path` may also be lists, one per element (a multi-floor minimap). Sizes never
    change."""
    from riftlord.formats.ue.uassetio import Package
    ua, ux = md_files
    pkg = Package(ua, ux)
    pkg.load_payloads()
    paths = tex_path if isinstance(tex_path, (list, tuple)) else None
    tex_names = {}
    for path in (paths or ([tex_path] if tex_path else [])):
        if path and path not in tex_names:
            # a full 'package.object' reference is written as given
            ref = path if '.' in path.rsplit('/', 1)[-1] else '%s.%s' % (path, path.rsplit('/', 1)[-1])
            tex_names[path] = pkg.add_name(ref)
    names = [n for n, _h in pkg.names]
    data = bytearray(pkg.exports[0]['data'])
    wrote = 0
    for nm, kind, _s, at, size in _tags(data, names, 0, len(data)):
        if nm != 'MapVolumes':
            continue
        count = struct.unpack_from('<i', data, at)[0]
        o = at + 4 + 49
        for element in range(count):
            vol_all = volume
            volume = (vol_all[element] if isinstance(vol_all, (list, tuple))
                      and vol_all and isinstance(vol_all[0], dict) else vol_all)
            path = paths[element] if paths else tex_path
            tex_name = tex_names.get(path) if path else None
            fields = _tags(data, names, o, at + size)
            for fn, fk, fs, fat, fsize in fields:
                if fn == 'Transform':
                    for tn, _tk, _ts, tat, _tsz in _tags(data, names, fat, fat + fsize):
                        if volume is None:
                            continue
                        if tn == 'Rotation':
                            struct.pack_into('<4f', data, tat, *volume['rotation'])
                        elif tn == 'Translation':
                            struct.pack_into('<3f', data, tat, *volume['translation'])
                    wrote += 1
                elif fn == 'Extent' and volume is not None:
                    struct.pack_into('<3f', data, fat, *volume['extent'])
                elif fn == 'MapTexture' and tex_name is not None:
                    struct.pack_into('<ii', data, fat, tex_name, 0)
            o = fields[-1][3]
            volume = vol_all
    pkg.exports[0]['data'] = bytes(data)
    if not wrote:
        raise SystemExit('the MapData has no volume to frame the new level')
    return pkg.write()


def draw(plan, volume, size):
    """The minimap as an RGBA image `size` pixels square."""
    from PIL import Image, ImageDraw
    from riftlord.build.minimap import mapdata
    project = mapdata.projector(volume)
    img = Image.new('RGBA', (size, size), (255, 255, 255, 0))
    pen = ImageDraw.Draw(img)
    floors, others = _boxes(plan)
    band = max(1, int(round(MAP_WALL_BAND * size)))

    def rect(lo, hi, grow=0):
        u0, v0 = project((lo[0], lo[1]))
        u1, v1 = project((hi[0], hi[1]))
        xs = sorted((u0 * size, u1 * size))
        ys = sorted((v0 * size, v1 * size))
        return [xs[0] - grow, ys[0] - grow, xs[1] + grow, ys[1] + grow]
    for lo, hi, _sl, _y in floors:
        pen.rectangle(rect(lo, hi, band), fill=(255, 255, 255, MAP_ALPHA))
    for lo, hi, _sl, _y in floors:
        pen.rectangle(rect(lo, hi), fill=(0, 0, 0, MAP_ALPHA))
    tops = [b[1][2] for b in floors]
    for lo, hi, _outline in others:
        if any(lo[2] < z + OBSTACLE_BELOW and hi[2] > z + OBSTACLE_ABOVE for z in tops):
            pen.rectangle(rect(lo, hi), fill=(255, 255, 255, MAP_ALPHA))
    return img


def minimap(plan, level_name, tex_path, volume=None, painter=None, donor=None):
    """((uasset, uexp) of the minimap texture at `tex_path`, report).

    `volume` frames it (default `volume_for(plan)`; with a `painter` it may be None,
    for a picture that keeps the source frames), `painter(size)` draws it as an RGBA
    image (default `draw`), and `donor` names the texture package to clone (default
    the level's first minimap)."""
    from riftlord.build.minimap import mapdata
    from riftlord.build import modtable
    from riftlord.build import texpatch
    from riftlord.formats.ue.uassetio import Package
    #: 'package.object' names the copy's object too (the source texture's own)
    tex_obj = None
    if '.' in tex_path.rsplit('/', 1)[-1]:
        tex_path, tex_obj = tex_path.rsplit('.', 1)
    host = donor or (mapdata.read(level_name) or {}).get('volumes', [{}])[0].get('texture', '')
    host = host.split('.')[0]
    if not host:
        raise SystemExit('%s has no minimap to copy' % level_name)
    got = mapdata._fetch(host, exts=('.uasset', '.uexp', '.ubulk'))
    if '.ubulk' in got:
        raise SystemExit('%s streams its mips from a .ubulk, which a mod pak here '
                         'cannot carry' % host)
    blobs = {}
    for ext, path in got.items():
        with open(path, 'rb') as fh:
            blobs[ext] = fh.read()
    if painter is None:
        volume = volume or volume_for(plan)
        painter = (lambda size: draw(plan, volume, size))
    picture = {}

    def at(w, h):
        from PIL import Image
        if 'full' not in picture:
            picture['full'] = painter(max(w, h))
        # each smaller mip box-filtered (a 2x2 average per halving)
        return picture['full'].resize((w, h), Image.BOX)
    fmt, mips = texpatch.write(blobs, at, kind='minimap')
    pkg = Package(blobs['.uasset'], blobs['.uexp'])
    pkg.load_payloads()
    leaf_old, leaf_new = host.rsplit('/', 1)[-1], tex_obj or tex_path.rsplit('/', 1)[-1]
    modtable.retarget(pkg, exact={host: tex_path,
                                  '%s.%s' % (host, leaf_old): '%s.%s' % (tex_path, leaf_new),
                                  leaf_old: leaf_new})
    ua, ux = pkg.write()
    #: each inline mip's FByteBulkData records its absolute file offset, which moves
    #: with the header
    ux = bytearray(ux)
    for mip in mips:
        struct.pack_into('<q', ux, mip['at'] - 8, len(ua) + mip['at'])
    return (ua, bytes(ux)), {'from': host, 'to': tex_path, 'format': fmt,
                             'mips': len(mips), 'volume': volume}
