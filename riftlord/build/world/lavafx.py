"""Lava effects: bursts on lava, embers and ash over it, and over the lava by spawner doors.

A shipped level that ships lava also ships its effects, placed by hand: bubbling bursts on
the surface (`BP_LavaBurst_01_C`), long embers and ash embers floating over it (Emitter
actors with `P_LavaEmbers_Long_1` / `P_AshEmbers_1`), and smoke at a door. A level whose
liquid a plan turns INTO lava has none of them, so they are placed here, spaced and raised
as the lava levels place theirs (measured on Scenario_06 and Scenario_01, 2026-10-15).

Each effect is a shipped actor lifted from a donor level (DONORS) and moved, as kit pieces
are. A plan can also place one by hand: `{'kind': 'fx', 'fx': <DONORS key>, 'at': [x, y, z]}`
in `added`. `plan['look']['lavaFx'] = False` turns the automatic ones off.

    placements(level, plan)   # [(fx key, (x, y, z))] the plan gets automatically
"""

import functools
import hashlib
import json
import math
import os
import struct

#: Effect -> (donor package, donor actor name).
DONORS = {
    'LavaBurst': ('/Game/Maps/Campaign/Scenario_06/Scenario_06', 'P_LavaBurst_1'),
    'LavaEmbers': ('/Game/Maps/Campaign/Scenario_06/Scenario_06', 'P_LavaEmbers_Long_10'),
    'AshEmbers': ('/Game/Maps/Campaign/Scenario_01/Scenario_01_VFX', 'P_AshEmbers2'),
    'AshHigh': ('/Game/Maps/Campaign/Scenario_06/Scenario_06', 'P_AshEmbers_10'),
    'LavaPillow': ('/Game/Maps/Campaign/Scenario_01/Scenario_01_Environment',
                   'SM_LSC_LavaPillow_C_4'),
    'LavaFall': ('/Game/Maps/Campaign/Scenario_01/Scenario_01_Environment', 'LavaFall_02_8'),
    'DoorSmoke': ('/Game/Maps/Campaign/Scenario_01/Scenario_01', 'P_Scenario_06DoorSmoke_2'),
}

#: Nearest-neighbour spacing of each effect over lava (uu), from the shipped levels: bursts
#: 2,657 / 2,248 (Scenario_06 / 01); long embers 3,263 and ash (P_AshEmbers) 3,534 on North
#: Wing, whose heavy ash the user asked for; high ash (P_AshEmbers_1) 2,314 / 4,268. The
#: pillow lava has one shipped example; at the burst spacing the user asked for more, so half.
SPACING = {'LavaBurst': 2450.0, 'LavaEmbers': 3260.0, 'AshEmbers': 3530.0,
           'AshHigh': 3300.0, 'LavaPillow': 1200.0}
#: Height of each effect's origin over the lava surface (uu): North Wing's long embers 183
#: (median), its P_AshEmbers 622, the pooled high ash 1,780; bursts sit on the surface and
#: North Wing's pillow has its origin 134 under it.
RISE = {'LavaBurst': 0.0, 'LavaEmbers': 183.0, 'AshEmbers': 622.0, 'AshHigh': 1780.0,
        'LavaPillow': -134.0}
#: Effects that are static meshes rather than emitters or Blueprints.
MESH_FX = ('LavaPillow', 'LavaFall')
#: Effects spread over the open lava, in order. Pillow lava runs along rivers instead.
SPREAD = ('LavaBurst', 'LavaEmbers', 'AshEmbers', 'AshHigh')
#: Pillow lava is chained along each open area's centre lines, as North Wing's bright lava
#: trails along its rivers (spread on a grid it read as nodes, not rivers - user, Beset Lava FX 4).
#: Pillows sit this far apart along a line (uu), inside the fortress and outside it: under
#: the pillow's 1,400 uu length, so they overlap into one trail. The fortress is the lava
#: planes around the rift; Beset wanted more trails there and fewer outside (Bake B).
PILLOW_STEP = {'inside': 700.0, 'outside': 1600.0}
#: A centre-line cell lies at least this many grid cells from the bank, so a thin lip of open
#: lava draws no trail. Inside the fortress the narrow pits beside the rift take trails too.
RIDGE_MIN = {'inside': 1, 'outside': 2}
#: A pillow is turned to the river's direction over centre-line cells within this (uu).
RIDGE_REACH = 600.0
#: The pillow mesh's long axis is its local +y, so its yaw is the river's heading less this.
PILLOW_YAW_OFFSET = 90.0
#: A lava fall is a list of segments (plan `look.lavaFalls`, [{'top': [x, y, z], 'bottom':
#: [x, y, z], 'yaw': deg}]): each is one fall mesh stretched from top to bottom and tilted
#: back to lie along a slope, so a fall can start at a cliff's lip and run down the face
#: (a single upright mesh started part-way down Beset's cliffs). The mesh is FALL_LENGTH
#: tall with its origin mid-height, falls along its +x (North Wing's do), and stands
#: FALL_OUT out from the face.
FALL_LENGTH = 2000.0
FALL_OUT = 150.0
#: Rocks within this of open lava (uu, plan distance from their bounds) take their mesh's
#: lava material (LAVA_ROCKS); None paints every rock, as North Wing does all ~210 of its
#: own (at 2,000 the user saw too few on Beset).
ROCK_REACH = None
#: A rock mesh's lava material, as North Wing uses it (Small_stone_02A from Scenario_06).
LAVA_ROCKS = {
    'Big_stone_01A': 'BigStone_01A_Lava_INST',
    'Big_Stone_02A': 'BigStone_02A_Lava_INST',
    'Big_Stone_03_A': 'BigStone_03A_Lava_INST',
    'Small_stone_01A_A': 'SmallStone_Lava_INST',
    'Small_stone_01B_A': 'SmallStone_Lava_INST',
    'Small_stone_02A': 'SmallStone_02_Lava_WS_INST',
    'Small_stone_03A': 'SmallStone_03_Lava_INST',
    'Stone_Pebble_1': 'SmallStone_Lava_INST',
    'Stone_Pebble_6': 'SmallStone_Lava_INST',
    #: the rest of the pebble set, as 1 and 6 (Beset Bake B: rock still unpainted)
    'Stone_Pebble_2': 'SmallStone_Lava_INST',
    'Stone_Pebble_3': 'SmallStone_Lava_INST',
    'Stone_Pebble_4': 'SmallStone_Lava_INST',
    'Stone_Pebble_5': 'SmallStone_Lava_INST',
}
#: The lava rock look is a world-height overlay (the material's BC_WorldOverlay, RockGradient:
#: dark red-black low, no change high) at BC_WorldOverlay_Position z with BC_WorldOverlay_Size
#: z tall, set for North Wing: z 900 and 3,750, i.e. from 485 under its lava (-490) to just over
#: its rocks. Beset's cliffs climb to 7,500 and stayed grey above the band (Bake C), so a build
#: ships its own copies of the lava rock materials with the band reaching the top of the highest
#: painted rock. The gradient's last tenth is near black (luminance 89 down to 20 of 255): with
#: the band starting just under the lava, every rock by the lava was near black and clashed with
#: the grey and purple rock around it (Bake D). This share of the band lies under the lava, so a
#: rock at the lava's edge starts at the gradient's mid purple-red (luminance ~140).
#: The overlay's x/y stay as shipped.
ROCK_BAND_UNDER_LAVA = 0.25
#: Where a mission's lava rock materials live, under its mod folder.
ROCK_MATERIAL_FOLDER = 'LavaRock'
#: The lava rock materials' folder in the game.
ROCK_MATERIAL_SOURCE = '/Game/Environments/LandScapes/rock/Mat/LavaRock'
#: A spawner door gets embers and ash over the nearest open lava within this (uu), and none
#: without open lava that near: the shipped spacing of ash embers.
DOOR_LAVA_REACH = SPACING['AshEmbers']
#: Open lava is found on a grid this fine (uu): narrow enough to see a pit between floor
#: slabs.
DETECT_STEP = 150.0
#: The smallest open area that gets effects (uu^2): about a 450 x 450 pit.
MIN_AREA = 200000.0
#: Grid points tested against a plane's triangles at once.
POINTS_PER_BATCH = 2000
#: Effects stay within the level's NavMeshBoundsVolume grown by this (uu): Front Lawn's
#: side water planes run out to 18,000 uu as backdrop, where effects are unseen cost.
REGION_MARGIN = 2000.0
#: Geometry less than this over the lava surface does not cover it (uu): banks and the
#: liquid's own edging.
COVER = 20.0
#: Covering geometry is binned on cells this wide (uu).
TERRAIN_CELL = 100.0
#: A candidate point is covered when solid geometry stands between COVER and this over the
#: lava (uu): terrain, floors, bridges and the fort over Front Lawn's water planes, which run
#: on under them.
OVERHEAD = 1500.0


def wanted(plan):
    return ((plan or {}).get('look') or {}).get('lavaFx', True) is not False


def _lava_planes(level, plan):
    """[(piece, (lo, hi))] liquid planes the plan turns into lava (from another kind)."""
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import leveldata
    from riftlord.build.world import liquidlights
    look = (plan or {}).get('look') or {}
    level_wide = look.get('liquid')
    planes = look.get('planes') or {}
    out = []
    for p in leveldata.level(level)['pieces']:
        if not p.get('liquid') or p.get('hidden'):
            continue
        material = planes.get(str(p['id'])) or planes.get(p['id']) or level_wide
        if not material or omd3level.liquid_kind(material) != 'lava':
            continue
        if liquidlights.kind_of(p) == 'lava':
            continue                       # already lava: it shipped with its own effects
        out.append((p, liquidlights._footprint(p)))
    return out


def _plane_triangles(piece):
    """World triangles of a liquid plane's mesh, or []."""
    import numpy as np
    from riftlord.build.navigation import navregen
    got = navregen._mesh(piece['mesh'])
    if got is None:
        return []
    v, t = got
    m = piece['matrix']
    w = np.array([[m[r] * p[0] + m[4 + r] * p[1] + m[8 + r] * p[2] + m[12 + r]
                   for r in range(3)] for p in v])
    return w[t]


def _overhead(level, planes, plan=None):
    """{(i, j)} TERRAIN_CELL bins holding solid geometry between COVER and OVERHEAD over the
    lava planes: terrain the planes run under, and the floors, bridges and walls over them.
    Only pieces whose bounds reach into that band over the planes are read, and each is
    dropped after it is binned, so memory stays bounded."""
    import numpy as np
    from riftlord.build.navigation import navregen
    lo = np.min([b[0] for _p, b in planes], axis=0)
    hi = np.max([b[1] for _p, b in planes], axis=0)
    zlo, zhi = lo[2] + COVER, hi[2] + OVERHEAD
    liquid = {p['mesh'] for p, _b in planes}
    out = set()
    #: the FINAL geometry: a bridge the plan adds covers lava, one it removes uncovers it
    for item in navregen.final_pieces(level, plan or {}):
        name, matrix = item[0], np.asarray(item[1])
        if name in liquid:
            continue
        got = navregen._mesh(name)
        if got is None:
            continue
        w = got[0] @ matrix[:3, :3] + matrix[3, :3]
        if (w[:, 2].max() < zlo or w[:, 2].min() > zhi or w[:, 0].max() < lo[0]
                or w[:, 0].min() > hi[0] or w[:, 1].max() < lo[1] or w[:, 1].min() > hi[1]):
            continue
        tri = w[got[1]]
        keep = (tri[:, :, 2].max(1) >= zlo) & (tri[:, :, 2].min(1) <= zhi)
        tri = tri[keep]
        if not len(tri):
            continue
        #: each triangle sampled at about one point per cell: its corners, its centre,
        #: and a barycentric lattice on the ones larger than a cell
        pts = [tri.reshape(-1, 3), tri.mean(1)]
        size = np.linalg.norm(tri[:, [1, 2, 0], :2] - tri[:, :, :2], axis=2).max(1)
        for t in tri[size > TERRAIN_CELL]:
            n = int(math.ceil(np.linalg.norm(t[[1, 2, 0], :2] - t[:, :2], axis=1).max()
                              / TERRAIN_CELL))
            u, v = np.meshgrid(np.arange(n + 1), np.arange(n + 1))
            m = (u + v) <= n
            a, b = u[m] / n, v[m] / n
            pts.append(t[0] + a[:, None] * (t[1] - t[0]) + b[:, None] * (t[2] - t[0]))
        pts = np.vstack(pts)
        pts = pts[(pts[:, 2] >= zlo) & (pts[:, 2] <= zhi)]
        out.update(map(tuple, np.floor(pts[:, :2] / TERRAIN_CELL).astype(int)))
    return out


def _covered(cover, x, y):
    return (int(math.floor(x / TERRAIN_CELL)), int(math.floor(y / TERRAIN_CELL))) in cover


def _spread(candidates, spacing, must=0):
    """Candidates kept at least `spacing` apart, in order; the first `must` are kept
    whatever their spacing (one per open area)."""
    kept = list(candidates[:must])
    for p in candidates[must:]:
        if all(math.hypot(p[0] - q[0], p[1] - q[1]) >= spacing for q in kept):
            kept.append(p)
    return kept


def _door_effects(plan, open_cells):
    """[(fx, at)]: embers and ash over the open lava nearest each spawner's door, when
    there is open lava within DOOR_LAVA_REACH of it (smoke on a door's floor read as out
    of place, and in front of Beset's force field it came up through the floor)."""
    doors = {d.get('id'): d for d in (plan or {}).get('doors') or []}
    out = []
    for sp in (plan or {}).get('spawners') or []:
        door = doors.get(sp.get('door'))
        if not door or not door.get('at') or not open_cells:
            continue
        near = min(open_cells, key=lambda c: math.hypot(c[0] - door['at'][0], c[1] - door['at'][1]))
        if math.hypot(near[0] - door['at'][0], near[1] - door['at'][1]) > DOOR_LAVA_REACH:
            continue
        out.append(('LavaEmbers', (near[0], near[1], near[2] + RISE['LavaEmbers'])))
        out.append(('AshEmbers', (near[0], near[1], near[2] + RISE['AshEmbers'])))
    return out


def _open_lava(level, plan, planes):
    """[(x, y, z)] open lava surface points on a DETECT_STEP grid: on a plane's mesh, not
    under geometry, within the region."""
    import numpy as np
    from riftlord.build.navigation import navregen
    cover = _overhead(level, planes, plan)
    region = navregen.region(level)
    out = []
    for piece, (lo, hi) in planes:
        tris = _plane_triangles(piece)
        if not len(tris):
            continue
        x0, x1, y0, y1 = lo[0], hi[0], lo[1], hi[1]
        if region is not None:
            x0, x1 = max(x0, region[0][0] - REGION_MARGIN), min(x1, region[1][0] + REGION_MARGIN)
            y0, y1 = max(y0, region[0][1] - REGION_MARGIN), min(y1, region[1][1] + REGION_MARGIN)
        if x0 >= x1 or y0 >= y1:
            continue
        gx, gy = np.meshgrid(np.arange(x0 + DETECT_STEP / 2, x1, DETECT_STEP),
                             np.arange(y0 + DETECT_STEP / 2, y1, DETECT_STEP))
        pts = np.c_[gx.ravel(), gy.ravel()]
        z = np.full(len(pts), np.nan)
        a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
        d = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
        ok = np.abs(d) > 1e-9
        a, b, c, d = a[ok], b[ok], c[ok], d[ok]
        for s0 in range(0, len(pts), POINTS_PER_BATCH):
            P = pts[s0:s0 + POINTS_PER_BATCH]
            l1 = ((b[None, :, 1] - c[None, :, 1]) * (P[:, None, 0] - c[None, :, 0])
                  + (c[None, :, 0] - b[None, :, 0]) * (P[:, None, 1] - c[None, :, 1])) / d[None]
            l2 = ((c[None, :, 1] - a[None, :, 1]) * (P[:, None, 0] - c[None, :, 0])
                  + (a[None, :, 0] - c[None, :, 0]) * (P[:, None, 1] - c[None, :, 1])) / d[None]
            l3 = 1 - l1 - l2
            hit = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
            first = hit.argmax(1)
            any_hit = hit.any(1)
            rows = np.nonzero(any_hit)[0]
            t = first[rows]
            z[s0 + rows] = (l1[rows, t] * a[t, 2] + l2[rows, t] * b[t, 2] + l3[rows, t] * c[t, 2])
        for (x, y), zz in zip(pts, z):
            if not np.isnan(zz) and not _covered(cover, x, y):
                out.append((float(x), float(y), float(zz)))
    return out


def _areas(cells):
    """`cells` grouped into separate open areas (DETECT_STEP grid neighbours)."""
    key = {(round(c[0] / DETECT_STEP), round(c[1] / DETECT_STEP)): c for c in cells}
    seen, groups = set(), []
    for k in key:
        if k in seen:
            continue
        stack, members = [k], []
        seen.add(k)
        while stack:
            q = stack.pop()
            members.append(key[q])
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (q[0] + dx, q[1] + dy)
                    if n in key and n not in seen:
                        seen.add(n)
                        stack.append(n)
        groups.append(members)
    return groups


def _fortress(level, plan, planes):
    """Triangle arrays of the lava planes that make up the fortress: each plane whose
    footprint holds a rift, and each plane whose footprint lies inside one of those."""
    from riftlord.mapeditor import leveldata
    rifts = [tuple(r['loc'][:2]) for r in (leveldata.level(level).get('rifts') or [])]
    rifts += [tuple(r['at'][:2]) for r in (plan or {}).get('rifts') or [] if r.get('at')]

    def holds(box, x, y):
        return box[0][0] <= x <= box[1][0] and box[0][1] <= y <= box[1][1]
    homes = [b for _p, b in planes if any(holds(b, x, y) for x, y in rifts)]
    return [_plane_triangles(p) for p, b in planes
            if any(holds(h, b[0][0], b[0][1]) and holds(h, b[1][0], b[1][1]) for h in homes)]


def _inside(fortress, x, y):
    """Whether (x, y) lies on one of the fortress planes' triangles."""
    import numpy as np
    for t in fortress:
        if not len(t):
            continue
        a, b, c = t[:, 0, :2], t[:, 1, :2], t[:, 2, :2]

        def side(p, q):
            return (q[:, 0] - p[:, 0]) * (y - p[:, 1]) - (q[:, 1] - p[:, 1]) * (x - p[:, 0])
        d1, d2, d3 = side(a, b), side(b, c), side(c, a)
        if np.any(((d1 >= 0) & (d2 >= 0) & (d3 >= 0)) | ((d1 <= 0) & (d2 <= 0) & (d3 <= 0))):
            return True
    return False


def _rivers(members, fortress=()):
    """[((x, y, z), yaw)] pillow places along an open area's centre lines: cells farther
    from the bank than any neighbour (at least RIDGE_MIN cells in), PILLOW_STEP apart, each
    turned along the line through the centre-line cells near it. Cells on the `fortress`
    planes take the 'inside' values, the rest 'outside'."""
    import collections
    key = {(round(c[0] / DETECT_STEP), round(c[1] / DETECT_STEP)): c for c in members}
    near = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
    depth = {}
    q = collections.deque()
    for k in key:
        if any((k[0] + dx, k[1] + dy) not in key for dx, dy in near):
            depth[k] = 1
            q.append(k)
    while q:
        k = q.popleft()
        for dx, dy in near:
            n = (k[0] + dx, k[1] + dy)
            if n in key and n not in depth:
                depth[n] = depth[k] + 1
                q.append(n)
    where = {k: 'inside' if _inside(fortress, key[k][0], key[k][1]) else 'outside'
             for k in depth}
    ridge = [k for k, d in depth.items() if d >= RIDGE_MIN[where[k]]
             and all(depth.get((k[0] + dx, k[1] + dy), 0) <= d for dx, dy in near)]
    ridge.sort(key=lambda k: -depth[k])
    pts = [key[k] for k in ridge]
    step = {id(key[k]): PILLOW_STEP[where[k]] for k in ridge}
    out = []
    for p in pts:
        if any(math.hypot(p[0] - q[0][0], p[1] - q[0][1]) < step[id(p)] for q in out):
            continue
        local = [r for r in pts if math.hypot(r[0] - p[0], r[1] - p[1]) <= RIDGE_REACH]
        mx = sum(r[0] for r in local) / len(local)
        my = sum(r[1] for r in local) / len(local)
        sxx = sum((r[0] - mx) ** 2 for r in local)
        syy = sum((r[1] - my) ** 2 for r in local)
        sxy = sum((r[0] - mx) * (r[1] - my) for r in local)
        heading = math.degrees(0.5 * math.atan2(2 * sxy, sxx - syy))
        out.append((p, heading - PILLOW_YAW_OFFSET))
    return out


def _centre(members):
    """The member nearest the area's centre."""
    cx = sum(m[0] for m in members) / len(members)
    cy = sum(m[1] for m in members) / len(members)
    return min(members, key=lambda m: math.hypot(m[0] - cx, m[1] - cy))


#: Where computed placements are kept, keyed by everything they depend on.
CACHE_DIR = os.path.expanduser('~/.cache/rift-lord/lavafx')
#: Bump when the placement rules change, so old cached placements are not reused.
RULES_VERSION = 6


def _cache_key(level, plan):
    plan = plan or {}
    look = plan.get('look') or {}
    keyed = {'v': RULES_VERSION, 'level': level,
             'look': {k: look.get(k) for k in ('liquid', 'planes', 'lavaFx')},
             'geometry': {k: plan.get(k) for k in ('removed', 'edits', 'added')},
             'doors': plan.get('doors'), 'spawners': plan.get('spawners'),
             'consts': [SPACING, RISE, SPREAD, PILLOW_STEP, RIDGE_MIN, RIDGE_REACH,
                        PILLOW_YAW_OFFSET, ROCK_REACH, DOOR_LAVA_REACH, DETECT_STEP, MIN_AREA, COVER,
                        TERRAIN_CELL, OVERHEAD, REGION_MARGIN]}
    return hashlib.sha1(json.dumps(keyed, sort_keys=True, default=str).encode()).hexdigest()


def placements(level, plan):
    """[(fx key, (x, y, z))]: effects for every plane the plan turns into lava, and at
    every spawner door when it does. Kept in CACHE_DIR: the cover scan reads every piece
    near the lava."""
    if not wanted(plan):
        return []
    path = os.path.join(CACHE_DIR, _cache_key(level, plan) + '.json')
    try:
        with open(path) as fh:
            return [(e[0], tuple(e[1])) + tuple(e[2:]) for e in json.load(fh)]
    except (OSError, ValueError):
        pass
    got = _placements(level, plan)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path + '.tmp', 'w') as fh:
            json.dump(got, fh)
        os.replace(path + '.tmp', path)
    except OSError:
        pass
    return got


def _placements(level, plan):
    planes = _lava_planes(level, plan)
    if not planes:
        return []
    cells = _open_lava(level, plan, planes)
    if not cells:
        return []
    areas = [m for m in _areas(cells) if len(m) * DETECT_STEP * DETECT_STEP >= MIN_AREA]
    #: every separate open area gets each effect first (a pit narrower than the spacing
    #: lost out to a neighbour picked before it), then the rest fill in at SPACING
    seeds = [_centre(m) for m in areas]
    rest = [c for m in areas for c in m]
    out = []
    for fx in SPREAD:
        kept = _spread(seeds + rest, SPACING[fx], must=len(seeds))
        out += [(fx, (x, y, z + RISE[fx])) for x, y, z in kept]
    fortress = _fortress(level, plan, planes)
    for members in areas:
        for (x, y, z), yaw in _rivers(members, fortress):
            out.append(('LavaPillow', (x, y, z + RISE['LavaPillow']), yaw))
    return out + _door_effects(plan, rest)


@functools.lru_cache(maxsize=None)
def donor(fx):
    """(package, zero-based export index) of an effect's donor actor, or None."""
    from riftlord.core import gamefiles
    from riftlord.formats.omd3 import omd3level
    from riftlord.formats.ue.uassetio import Package
    path, name = DONORS[fx]
    try:
        info, _uexp, _hdr = omd3level._load(path)
        #: ZERO-based, as levelbuild.lift_actor takes it (see apply._class_at)
        index = next((i for i, e in enumerate(info['exports'])
                      if str(e.get('name') or '') == name), None)
        if index is None:
            return None
        pkg = Package(*gamefiles.package(path))
        pkg.load_payloads()
    except Exception:                                # noqa: BLE001
        return None
    return pkg, index


#: What each effect is, for the Map Editor's library.
FX_LABELS = {'LavaBurst': 'Lava burst', 'LavaEmbers': 'Lava embers', 'AshEmbers': 'Ash embers',
             'AshHigh': 'High ash', 'LavaPillow': 'Pillow lava', 'LavaFall': 'Lava fall',
             'DoorSmoke': 'Door smoke'}
#: The library group of the hand-picked effects above.
LAVA_GROUP = 'Lava and ash'


def _surveyed():
    try:
        from riftlord.mapeditor import fxsurvey
        return fxsurvey.load()
    except Exception:                                # noqa: BLE001
        return {}


#: Every other effect a shipped level places, one donor per particle system
#: (riftlord/fxsurvey.py, fx-donors.json), keyed by its template name.
SURVEYED = _surveyed()
for _key, _rec in SURVEYED.items():
    DONORS.setdefault(_key, tuple(_rec['donor']))


@functools.lru_cache(maxsize=None)
def donor_mesh(fx):
    """The static mesh a mesh effect (MESH_FX) draws, by asset name, or None."""
    if fx not in MESH_FX:
        return None
    from riftlord.build import levelbuild
    from riftlord.formats.omd3 import meshcat
    got = donor(fx)
    if got is None:
        return None
    pkg, index = got
    names = [n for n, _h in pkg.names]
    actor = pkg.exports[index]['data']
    top = meshcat._walk(actor, names, 0, len(actor))
    if 'RootComponent' not in top:
        return None
    root = struct.unpack_from('<i', actor, top['RootComponent'][1])[0]
    if root <= 0:
        return None
    data = pkg.exports[root - 1]['data']
    comp = meshcat._walk(data, names, 0, len(data))
    if 'StaticMesh' not in comp:
        return None
    ref = struct.unpack_from('<i', data, comp['StaticMesh'][1])[0]
    return levelbuild._import_name(pkg, -ref - 1) if ref < 0 else None


def catalogue_rows():
    """The effects as Map Editor library rows (kind 'fx', category 'effects'), with the
    group they are listed under and the levels they come from."""
    rows = []
    for fx in DONORS:
        rec = SURVEYED.get(fx) or {}
        rows.append({'name': fx, 'kind': 'fx', 'category': 'effects',
                     'label': FX_LABELS.get(fx) or rec.get('label') or fx,
                     'group': LAVA_GROUP if fx in FX_LABELS else rec.get('group', 'Other'),
                     'levels': rec.get('levels') or [], 'template': rec.get('template'),
                     'actorClass': rec.get('actorClass'),
                     'mesh': donor_mesh(fx), 'thumb': donor_mesh(fx) or '', 'cells': [0, 0, 0],
                     'trapgrid': False, 'parts': 1, 'surfaces': []})
    return rows


def falls(plan):
    """[(origin, (pitch, yaw, roll), scale)] of the plan's lava-fall segments."""
    out = []
    for f in ((plan or {}).get('look') or {}).get('lavaFalls') or []:
        if not f.get('top') or not f.get('bottom'):
            continue
        yaw = float(f.get('yaw') or 0.0)
        r = math.radians(yaw)
        ox, oy = math.cos(r) * FALL_OUT, math.sin(r) * FALL_OUT
        top = [float(v) for v in f['top']]
        bottom = [float(v) for v in f['bottom']]
        run = math.hypot(bottom[0] - top[0], bottom[1] - top[1])
        drop = top[2] - bottom[2]
        length = math.hypot(run, drop)
        if length <= 0:
            continue
        #: positive pitch leans the mesh's top back over -x, onto the slope behind it
        pitch = math.degrees(math.atan2(run, drop))
        mid = ((top[0] + bottom[0]) / 2 + ox, (top[1] + bottom[1]) / 2 + oy,
               (top[2] + bottom[2]) / 2)
        out.append((mid, (pitch, yaw, 0.0), (1.0, 1.0, length / FALL_LENGTH)))
    return out


@functools.lru_cache(maxsize=1)
def _lava_rock_sources():
    """{material: /Game path} of every shipped lava rock material (the LavaRock folders)."""
    from riftlord.core import gamefiles
    out = {}
    for key in gamefiles.index():
        if key.endswith('.uasset') and (ROCK_MATERIAL_SOURCE.split('/Game/')[1] + '/') in key \
                or (key.endswith('.uasset') and
                    ROCK_MATERIAL_SOURCE.split('/Game/')[1] + '_WarScenario/' in key):
            leaf = key.rsplit('/', 1)[-1][:-len('.uasset')]
            out[leaf] = key.replace('../../../OMD/Content', '/Game')[:-len('.uasset')]
    return out


class _LavaRocks:
    """Membership test for the lava rock materials, read from the game's files."""
    def __contains__(self, material):
        return material in _lava_rock_sources()


#: The lava rock materials (every one in the shipped LavaRock folders): a piece painted
#: with one gets the mission's own copy with its height band fitted (rock_materials).
LAVA_ROCK_MATERIALS = _LavaRocks()


def rock_band(level, plan):
    """(centre z, height) of the lava rock overlay for the plan's lava-rock-painted pieces,
    or None."""
    planes = _lava_planes(level, plan)
    paints = {t: {e: m for e, m in rocks.items()
                  if any(x in LAVA_ROCK_MATERIALS for x in materials_in(m))}
              for t, rocks in _rock_paints(level, plan).items()}
    if not planes or not any(paints.values()):
        return None
    from riftlord.mapeditor import leveldata
    from riftlord.build.world import liquidlights
    lava = min(b[1][2] for _p, b in planes)
    tops = [liquidlights._footprint(p)[1][2] for p in leveldata.level(level)['pieces']
            if paint_target(p) in paints.get(p.get('sub') or 'persistent', {})]
    top = max(tops + [lava + 1.0])
    height = (top - lava) / (1.0 - ROCK_BAND_UNDER_LAVA)
    return top - height / 2.0, height


def rock_material_path(mod, material):
    return '/Game/Mods/%s/%s/%s' % (mod, ROCK_MATERIAL_FOLDER, material)


def rock_materials(level, plan, mod):
    """{game path: {'.uasset', '.uexp'}} of the mission's lava rock materials, the band
    set by rock_band; {} when the plan paints no rocks."""
    from riftlord.build import materialclone
    band = rock_band(level, plan)
    if band is None:
        return {}
    centre, height = band
    out = {}
    for material in sorted({x for rocks in _rock_paints(level, plan).values()
                            for m in rocks.values() for x in materials_in(m)
                            if x in LAVA_ROCK_MATERIALS}):
        source = _lava_rock_sources()[material]
        size = materialclone.material_params(source)['BC_WorldOverlay_Size']
        path = rock_material_path(mod, material)
        pkg = materialclone.clone_material({'from': source, 'vectors': {
            'BC_WorldOverlay_Position': [0.0, 0.0, centre, 1.0],
            'BC_WorldOverlay_Size': [size[0], size[1], height, 1.0]}}, path)
        out[path] = dict(zip(('.uasset', '.uexp'), pkg.write()))
    return out


def rock_paints(level, plan):
    """{sublevel tag: {component export: material}} the plan paints (`look.paints`); a lava
    rock material becomes the mission's own copy (rock_materials) when the build names
    its mod in plan['_modKey']."""
    out = _rock_paints(level, plan)
    mod = (plan or {}).get('_modKey')
    if not mod or rock_band(level, plan) is None:
        return out
    own = lambda m: rock_material_path(mod, m) if m in LAVA_ROCK_MATERIALS else m
    return {tag: {e: ({slot: own(x) for slot, x in m.items()} if isinstance(m, dict)
                      else own(m))
                  for e, m in rocks.items()}
            for tag, rocks in out.items()}


#: The plan key holding painted materials: {piece id: material instance name, or
#: {slot: material}} (riftlord/README.md "Painted materials"). Set by the designer (the
#: Map Editor's Material field) or by suggest(); nothing paints a piece the plan does
#: not name.
PAINTS_KEY = 'paints'


def materials_in(value):
    """The material names one `paints` value holds: a name, or {slot: name}."""
    if isinstance(value, dict):
        return [str(v) for v in value.values() if v]
    return [str(value)] if value else []


def paint_target(piece):
    """The export a painted level piece is written on: a component piece's own, a
    Blueprint part's component export when the level has one; None for anything else
    (skeletal parts are not painted)."""
    if piece.get('source') == 'component':
        return piece.get('export')
    if piece.get('source') == 'blueprint' and not piece.get('skeletal'):
        return piece.get('component')
    return None


#: The part key of an added mesh's own (root) component in its `paints`.
MESH_PART = ''


def _material_import(builder, name):
    """An import of material instance `name` (an asset name or a full /Game path), or
    None when the game has no such asset."""
    from riftlord.formats.omd3 import omd3actors
    if name.startswith('/Game/'):
        owner = builder._import('/Script/CoreUObject', 'Package', 0, name)
        return builder._import('/Script/Engine', 'MaterialInstanceConstant', owner,
                               name.rsplit('/', 1)[-1])
    game = (omd3actors._asset_for(name) or '').replace('../../../OMD/Content/', '/Game/')
    if not game.startswith('/Game/'):
        return None
    owner = builder._import('/Script/CoreUObject', 'Package', 0, game)
    return builder._import('/Script/Engine', 'MaterialInstanceConstant', owner, name)


def paint_added(builder, added, placed, report):
    """Write the materials painted on added meshes and kit pieces (`added[n].paints`,
    {part: material or {slot: material}}; MESH_PART is a mesh's own component, any other
    key a kit piece's component by name) as OverrideMaterials. `placed` is {n: actor}.
    Returns how many components were painted."""
    from riftlord.build import apply as applier
    done = 0
    for n, actor in sorted(placed.items()):
        paints = (added[n] or {}).get(PAINTS_KEY) or {}
        for part, value in sorted(paints.items()):
            target = builder._root_component(actor) if part == MESH_PART \
                else applier._child_named(builder, actor, part)
            if not target:
                from riftlord.core import buildmessage
                report['skipped'].append(buildmessage.left_out(
                    'the paint on part %s of the added piece %s'
                    % (part, buildmessage.piece_label(added[n])), 'no such component',
                    'Paint another part of it, or clear that paint.'))
                continue
            slots = value if isinstance(value, dict) else {'0': value}
            refs = {int(k): _material_import(builder, str(v)) for k, v in slots.items() if v}
            if not refs or any(r is None for r in refs.values()):
                from riftlord.core import buildmessage
                report['skipped'].append(buildmessage.left_out(
                    'the paint on the added piece %s' % buildmessage.piece_label(added[n]),
                    'no asset for a material of %r' % (part or 'its mesh'),
                    'Pick another material for it.'))
                continue
            #: a plain material is slot 0; {slot: material} leaves the other slots to the
            #: mesh (a null entry)
            builder.set_object_array(target, 'OverrideMaterials',
                                     [refs.get(k, 0) for k in range(max(refs) + 1)])
            done += 1
    if done:
        report['notes'].append('%d added component(s) painted' % done)
    return done


def _rock_paints(level, plan):
    """{sublevel tag: {component export: material or {slot: material}}} for the plan's
    painted pieces."""
    from riftlord.mapeditor import leveldata
    paints = ((plan or {}).get('look') or {}).get(PAINTS_KEY) or {}
    if not paints:
        return {}
    removed = {r['id'] if isinstance(r, dict) else r for r in (plan or {}).get('removed') or []}
    out = {}
    for p in leveldata.level(level)['pieces']:
        material = paints.get(str(p.get('id')))
        target = paint_target(p)
        if not material or not target or p.get('id') in removed:
            continue
        out.setdefault(p.get('sub') or 'persistent', {})[target] = material
    return out


def suggest_paints(level, plan):
    """{piece id: lava rock material} for the rocks a designer may want painted: every rock
    with a lava variant (LAVA_ROCKS) when the plan turns a plane into lava."""
    planes = _lava_planes(level, plan)
    if not planes:
        return {}
    from riftlord.mapeditor import leveldata
    from riftlord.build.world import liquidlights
    cells = _open_lava(level, plan, planes)
    if not cells:
        return {}
    import numpy as np
    pts = np.array([(c[0], c[1]) for c in cells])
    removed = {r['id'] if isinstance(r, dict) else r for r in (plan or {}).get('removed') or []}
    out = {}
    for p in leveldata.level(level)['pieces']:
        material = LAVA_ROCKS.get(p.get('mesh'))
        if (not material or p.get('source') != 'component' or p.get('hidden')
                or p.get('id') in removed):
            continue
        lo, hi = liquidlights._footprint(p)
        dx = np.maximum(np.maximum(lo[0] - pts[:, 0], 0), pts[:, 0] - hi[0])
        dy = np.maximum(np.maximum(lo[1] - pts[:, 1], 0), pts[:, 1] - hi[1])
        if ROCK_REACH is None or np.min(np.hypot(dx, dy)) <= ROCK_REACH:
            out[str(p['id'])] = material
    return out


def items(plan):
    """[(fx key, location, rotator or None, scale or None)] of the effects the plan places:
    its `added` items of kind 'fx' (a matrix, or the older `at` + `yaw`) and any older
    `look.lavaFalls` segments. Nothing is placed that the plan does not list."""
    from riftlord.build import apply as applier
    out = []
    for i in (plan or {}).get('added') or []:
        if i.get('kind') != 'fx' or i.get('fx') not in DONORS:
            continue
        if i.get('matrix'):
            loc, rot, scale = applier.transform_of(i['matrix'])
            out.append((i['fx'], tuple(loc), tuple(rot), tuple(scale)))
        elif i.get('at'):
            out.append((i['fx'], tuple(i['at']), (0.0, float(i.get('yaw') or 0.0), 0.0), None))
    for at, rot, scale in falls(plan):
        out.append(('LavaFall', tuple(at), rot, scale))
    return out


def fx_matrix(location, rotator=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)):
    """The column-major matrix an `added` fx item stores (omd3level._matrix's layout)."""
    from riftlord.formats.omd3 import omd3level
    m = omd3level._matrix(location, rotator, scale)
    #: _matrix is m[row][col] for column vectors; a plan matrix is column-major, so the
    #: translation lands at 12-14
    return [float(m[r][c]) for c in range(4) for r in range(4)]


def suggest(level, plan):
    """{'added': [fx items], 'paints': {piece id: material}}: lava effects and lava rock paint
    a designer may start from -- bursts, embers and ash over the open lava, pillow trails
    along its rivers, smoke at spawner doors over lava, and every rock with a lava
    variant. Suggestions only: the editor adds them to the plan, where they are edited
    like any piece."""
    added = []
    for e in placements(level, plan):
        yaw = e[2] if len(e) > 2 else 0.0
        added.append({'kind': 'fx', 'fx': e[0], 'name': e[0],
                      'matrix': fx_matrix(e[1], (0.0, yaw, 0.0))})
    return {'added': added, 'paints': suggest_paints(level, plan)}


def apply(builder, level, plan, report):
    """Place the effects the plan lists (items). Returns the count."""
    from riftlord.build import apply as applier
    from riftlord.mapeditor import fabemit
    from riftlord.mapeditor import geomedits
    listed = items(plan)
    done = 0
    for n, (fx, at, rot, scale) in enumerate(listed):
        got = donor(fx)
        if got is None:
            from riftlord.core import buildmessage
            report['skipped'].append(buildmessage.left_out(
                'the %s effect at %s' % (fx, buildmessage.at(at)), 'no donor', verb='place'))
            continue
        try:
            index = builder.lift_actor(got[0], got[1], rename='RiftLordFx_%s_%d' % (fx, n))
        except Exception as exc:                     # noqa: BLE001
            from riftlord.core import buildmessage
            report['skipped'].append(buildmessage.left_out(
                'the %s effect at %s' % (fx, buildmessage.at(at)), 'lift failed: %s' % exc,
                verb='place'))
            continue
        if fx in MESH_FX:
            #: a lifted mesh keeps its donor's MapBuildDataId and would draw North Wing's
            #: baked light; a Movable component never reads a lightmap. Its LODData stays:
            #: these carry painted vertex colours fabemit.forget_lighting cannot parse.
            root = builder._root_component(index)
            if root:
                builder.set_property(root, 'Mobility', 'ByteProperty',
                                     struct.pack('<ii', builder.pkg.add_name(
                                         fabemit.PIECE_MOBILITY), 0),
                                     struct_name='EComponentMobility')
        applier.place_whole(builder, index, location=at, rotation=rot, scale=scale)
        #: a lifted primitive keeps the donor level's VisibilityId (precomputed visibility)
        geomedits.forget_visibility(builder, index)
        done += 1
    if done:
        report.setdefault('notes', []).append('%d effect(s) placed' % done)
    return done
