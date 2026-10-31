"""A level's NavPower walkable graph, regenerated from its final geometry.

The graph is built the way Recast builds one, then written with `navsurfgen`:

1. the level's pieces, with a plan's moves, removals and additions, are rasterised into
   columns of solid spans (render meshes, landscape terrain, blocking volumes);
2. a span top is a floor when a walkable triangle (slope within `MAX_SLOPE_DEG`) reaches
   it and the open space above it is the agent's height; neighbouring floors join when
   their heights differ by the agent's step and both have the agent's headroom;
3. floors inside a `NavPowerExclusionVolume` or outside the `NavMeshBoundsVolume` are
   dropped and the rest is eroded by the agent's radius;
4. the floor is cut into planar patches (one tile at most), each patch into monotone
   regions, each region's contour is simplified, triangulated and merged into convex
   polygons of at most `MAX_VERTS` vertices (Recast's regions, contours and poly mesh);
5. shared edges get one edge id each, and `navsurfgen` writes both agents' graphs.

    python3 navregen.py --level Scenario_09                 # regenerate and compare
    python3 navregen.py --level Scenario_09 --record R.json  # a mission's final geometry
"""

import argparse
import collections
import json
import math
import os
import struct
import sys
import time

import numpy as np
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navsurf  # noqa: E402
from riftlord.build.navigation import navsurfgen  # noqa: E402

#: Regenerate the walkable graph when a plan removes, moves or adds geometry. On since
#: 2026-10-15: the 'recast-upstream' generator (R8) played clean on Front Lawn, Beset and
#: Cellar's ground routes, and the user chose it for Rift Lord.
ENABLED = True
#: With ENABLED, regenerate even when the plan leaves the geometry alone.
ALWAYS = False
#: Which polygon generator: 'navregen' (this module's heightfield and welding), 'recast'
#: (UE 4.27's own Recast run offline, recastgen/recastgen.py) or 'recast-upstream' (the
#: same with open-source recastnavigation, the default: zlib licence, so it can ship).
GENERATOR = 'recast-upstream'
#: With GENERATOR 'roundtrip': keep the shipped corner tags (-3/-2); False writes -4 on
#: every corner, as generated graphs have.
ROUNDTRIP_TAGS = True

#: Column edge (uu).
CELL = 20.0
#: Height quantum of a span (uu).
CELL_HEIGHT = 5.0
#: Steepest walkable triangle (degrees), DefaultEngine.ini AgentMaxSlope.
MAX_SLOPE_DEG = 45.0
#: A non-walkable top this far above a walkable surface in the same span still counts
#: as floor (uu): Recast's flag merge threshold, the agent's step.
FLAG_MERGE = 48.0
#: Largest height difference from a rectangle's fitted plane to one of its cells (uu).
PLANE_TOL = 32.0
#: Side of the square tiles a planar patch never crosses (uu): bounds a polygon's size.
TILE = 4800.0
#: Largest distance a simplified wall edge strays from the floor's raw outline (uu).
MAX_ERROR = 40.0
#: Longest wall edge after simplification (uu).
MAX_EDGE = 1200.0
#: Most vertices one polygon carries (the shipped maximum is 12 on Scenario_09).
MAX_VERTS = 15
#: Patches smaller than this many cells join a neighbouring patch.
MIN_PATCH_CELLS = 150
#: Keep floor no route, spawner or door reaches. Off keeps those pieces plus every island
#: within ISLAND_REACH of them: shipped graphs keep the islands around the play area
#: (pits, ledges), and dropping all of them left Beset's third spawner and its door
#: with no floor; keeping everything adds out-of-play terrain the shipped graphs lack
#: (Scenario_01: 48M uu2 of floor against the shipped 18M).
KEEP_UNANCHORED = False
#: Distance from kept floor within which an island is kept too (uu) ...
ISLAND_REACH = 400.0
#: ... unless it stands more than this above that floor (roofs, tower tops) (uu) ...
ISLAND_RISE = 100.0
#: ... or is smaller than this (uu2).
MIN_ISLAND_AREA = 40000.0
#: Keep the shipped polygons at each door crossing (doorsplice.py).
KEEP_SHIPPED_DOORS = False
#: Record flags byte of a generated polygon (shipped: 2 on every triangle).
DEFAULT_FLAGS = 2
#: Insert every corner lying inside another polygon's edge into that edge.
MEND = True
#: Give both sides of every shared edge the same corner heights (weld).
WELD = True
#: Smallest piece cut from a welded polygon (uu2); a smaller one means the whole
#: polygon is fanned instead.
MIN_PIECE_AREA = 50.0
#: Thinnest polygon kept after welding (uu): thinner ones are slivers no agent uses.
MIN_WIDTH = 5.0
#: Weld passes (each can pair edges the last one's triangles made).
WELD_ROUNDS = 3
#: Largest height difference left between the two ends of a shared edge (uu).
WELD_EPS = 0.01
#: Largest distance of a corner from its polygon's plane before it is triangulated (uu).
PLANAR_EPS = 0.5
#: Most vertices a record holds: the shipped maximum on every level.
MAX_RECORD_VERTS = 15
#: Smallest island of floor kept, in cells.
MIN_ISLAND_CELLS = 4
#: Extra margin around the NavMeshBoundsVolume when gathering geometry (uu): shipped
#: graphs reach up to 1,800 uu past it in xy and 780 in z (Scenario_09).
REGION_MARGIN = 3000.0
#: Drop floor outside the NavMeshBoundsVolume itself.
CLIP_TO_BOUNDS = False
#: Where rasterised heightfields are kept between runs.
CACHE = os.path.expanduser('~/.cache/rift-lord/navregen')
#: Bumped when the rasteriser changes, so old caches are not reused.
RASTER_VERSION = 4
#: Most sample points held at once while rasterising.
POINTS_PER_BATCH = 4000000

#: Agent (radius, step, height) per graph, from the agent words navsurfgen writes.
AGENTS = tuple(tuple(struct.unpack('<f', struct.pack('<I', w))[0] for w in words[2:5])
               for words in navsurfgen.AGENT_WORDS)

#: Neighbour directions: +x, +y, -x, -y.
DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))

#: Words in a mesh or actor name that mark geometry with no collision a walker meets.
SKIP_WORDS = ('torch', 'banner', 'flag', 'pennant', 'chandelier', 'candle', 'cobweb',
              'web', 'fx', 'decal', 'plane', 'water', 'lava', 'liquid', 'fog', 'cloth',
              'rope', 'chain', 'vine', 'ivy', 'grass', 'skybox', 'sky', 'portal')

#: Actor class prefixes left out of the geometry: doors block NavPower with their own
#: obstruction component at run time.
SKIP_ACTOR_PREFIXES = ('BP_Door_', 'BP_WSForceFieldDoor')

#: Volume classes whose brushes are collision (riftlord.volumes.CLASSES), and the
#: kinds of them that stop enemies. Scenario_01's spawn ledge is a NoCollision rock
#: standing on BlockingVolume2_3, and the shipped graph walks on that brush.
BLOCKING_CLASSES = ('BlockingVolume',)
BLOCKING_STOPS = ('everyone',)

#: Volume classes this module reads from the persistent level.
EXCLUSION = 'NavPowerExclusionVolume'
BOUNDS = 'NavMeshBoundsVolume'


def _words(name):
    import re
    return [w.lower() for w in re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+', name or '')]


# -- geometry -------------------------------------------------------------------------

def _hulls(cache={}):
    if not cache:
        with open(gamepath.data('omd3-hulls.json')) as fh:
            cache.update(json.load(fh)['hulls'])
    return cache


#: component_flags' mark for a component that blocks but is never floor (NAV_FLAG off).
OBSTACLE_ONLY = 'obstacle'
#: Leave out components with `bCanEverAffectNavigation` false. Off: NavPower ignores that
#: UE flag. Shipped graphs leave a hole under 89/98 such pieces on Front Lawn, 13/13 on
#: North Wing, 50/51 and 46/47 on two more levels (5/5 free-standing in open floor; free-
#: standing NoCollision pieces: 1/794). Left out, enemies walked through them (Recast R1).
NAV_FLAG = False
#: Collision profile names that collide with nothing.
NO_COLLISION_PROFILES = ('NoCollision',)


def component_flags(level_name, cache={}):
    """{(sublevel tag, export): False} for every mesh component the navigation build
    leaves out: `bCanEverAffectNavigation` false, or collision switched off."""
    if level_name in cache:
        return cache[level_name]
    from riftlord.core import gamepath
    from riftlord.build import levelbuild
    from riftlord.formats.omd3 import meshcat
    from riftlord.build.navigation import navpatch
    from riftlord.build.navigation import navvolinject
    from riftlord.mapeditor import leveldata
    from riftlord.formats.ue.uassetio import Package
    out = {}
    for tag, path in (leveldata.level(level_name).get('subPackages') or {}).items():
        try:
            f = navpatch.fetch_map(path, gamepath.scratch('.subcopy', gamepath.CODE_ROOT))
            with open(f['.umap'], 'rb') as fa, open(f['.uexp'], 'rb') as fx:
                pkg = Package(fa.read(), fx.read())
            pkg.load_payloads()
        except Exception:                            # noqa: BLE001
            continue
        names = [n for n, _h in pkg.names]
        for i, e in enumerate(pkg.exports):
            cls = levelbuild.resolve(pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1]
            if 'MeshComponent' not in (cls or ''):
                continue
            d = e['data']
            for nm, _tp, st, end in navvolinject.walk_properties(d, pkg.names):
                if nm == 'bCanEverAffectNavigation' and not d[st + 24]:
                    if NAV_FLAG:
                        out[(tag, i + 1)] = False
                    else:
                        out.setdefault((tag, i + 1), OBSTACLE_ONLY)
                elif nm == 'BodyInstance':
                    body = meshcat._walk(d, names, st + 24 + 8 + 17, end)
                    got = []
                    for field in ('CollisionProfileName', 'CollisionEnabled'):
                        if field in body and body[field][2] >= 4:
                            k = struct.unpack_from('<i', d, body[field][1])[0]
                            got.append(names[k] if 0 <= k < len(names) else '')
                    if any(g in NO_COLLISION_PROFILES or g.endswith('::NoCollision')
                           for g in got):
                        out[(tag, i + 1)] = False
    cache[level_name] = out
    return out


def collides(piece, flags=None):
    """True for a piece a walker stands on or bumps into."""
    from riftlord.formats.omd3 import omd3level
    if piece.get('hidden'):
        return False
    if flags and flags.get((piece.get('sub') or 'persistent', piece.get('export'))) is False:
        return False
    words = set(_words(piece.get('mesh', '')))
    if any(w in words for w in SKIP_WORDS) or omd3level.is_liquid(piece.get('mesh')):
        return False
    if str(piece.get('actor') or '').startswith(SKIP_ACTOR_PREFIXES):
        return False
    if piece.get('foliage') and piece.get('mesh') not in _hulls():
        return False
    return True


def final_pieces(level_name, plan, walk=False):
    """[(mesh, 4x4 row-vector matrix)] after the plan's removals, moves and additions; with
    `walk`, [(mesh, matrix, walkable)], walkable False for an OBSTACLE_ONLY component."""
    from riftlord.mapeditor import leveldata
    level = leveldata.level(level_name)
    edits = {e['id']: e for e in plan.get('edits') or []}
    removed = {r['id'] if isinstance(r, dict) else r for r in plan.get('removed') or []}
    gone = set()
    for w in plan.get('wiring') or []:
        if not w.get('gone'):
            continue
        for kind in ('doors', 'spawners', 'teleports'):
            rec = level.get(kind)
            rec = dict(rec) if isinstance(rec, list) and rec and isinstance(rec[0], tuple) \
                else rec
            if isinstance(rec, dict) and w['name'] in rec and \
                    isinstance(rec[w['name']].get('index'), int):
                gone.add(rec[w['name']]['index'])
    out = []
    flags = component_flags(level_name)
    for p in level['pieces']:
        if p['id'] in removed or not collides(p, flags):
            continue
        if (p.get('sub') or 'persistent') == 'persistent' and p.get('export') in gone:
            continue
        e = edits.get(p['id'])
        M = np.array((e or p)['matrix'], dtype=np.float64).reshape(4, 4)
        key = (p.get('sub') or 'persistent', p.get('export'))
        out.append((p['mesh'], M, flags.get(key) != OBSTACLE_ONLY) if walk else (p['mesh'], M))
    parts = {}
    for a in plan.get('added') or []:
        for name, M in added_meshes(a, parts):
            out.append((name, M, True) if walk else (name, M))
    return out


def added_meshes(item, parts=None):
    """[(mesh, 4x4 row-vector matrix)] a walker stands on or bumps into, of one plan `added`
    item: an added mesh; a Fab piece's mesh (fab_mesh_name); each static mesh of a kit
    piece, as the level's own Blueprint actors are taken (collides), less the kit actors
    Rift Lord places with their own build (riftlord/mapeditor/kitactors.py: chests,
    pickups, the chandelier). `parts` keeps each kit class's parts between calls."""
    if not item.get('matrix'):
        return []
    #: a piece placed with collision off (riftlord/mapeditor/piececollision.py)
    from riftlord.mapeditor import piececollision
    if piececollision.is_off(dict(item, kind=item.get('kind', 'mesh'))):
        return []
    M = np.array(item['matrix'], dtype=np.float64).reshape(4, 4)
    kind = item.get('kind', 'mesh')
    if kind == 'fab':
        name = fab_mesh_name(item)
        return [(name, M)] if name else []
    if kind == 'mesh':
        return [(item['name'], M)] if collides({'mesh': item.get('name')}) else []
    if kind != 'kit':
        return []
    cls = item.get('name') or ''
    from riftlord.mapeditor import kitactors
    if kitactors.handles(cls):
        return []
    if parts is None:
        parts = {}
    if cls not in parts:
        from riftlord.mapeditor import leveldata
        parts[cls] = leveldata.kit_parts(cls).get('parts') or []
    from riftlord.formats.omd3 import omd3level
    out = []
    for part in parts[cls]:
        mesh = part.get('mesh')
        if not mesh or part.get('skeletal') or \
                not collides({'mesh': mesh, 'actor': cls}):
            continue
        #: the part's column-vector local transform, as a row-vector matrix, then the piece's
        local = np.array(omd3level._matrix(part.get('loc') or (0, 0, 0),
                                           part.get('rot') or (0, 0, 0),
                                           part.get('scale') or (1, 1, 1)), dtype=np.float64)
        out.append((mesh, local.T @ M))
    return out


#: Fab library roles nothing walks on or bumps into.
FAB_SKIP_ROLES = ('foliage',)


def fab_mesh_name(item):
    """The `fab/<PackId>/...` mesh name of a placed Fab piece in the current library, or
    None for a piece that does not collide or is not in the library."""
    from riftlord.mapeditor import fablibrary
    from riftlord.mapeditor import scratch
    entry = scratch._fab_entry(item)
    if not entry or entry.get('role') in FAB_SKIP_ROLES or not fablibrary.collides(entry):
        return None
    return fablibrary.mesh_key(entry['package'])


def _mesh(name, cache={}):
    if name not in cache:
        from riftlord.formats.omd3 import omd3landscape
        from riftlord.mapeditor import omd3serve
        if str(name).startswith('fab/'):
            from riftlord.mapeditor import fablibrary
            m = fablibrary.mesh(name)
        else:
            m = omd3landscape.mesh(name) if omd3landscape.is_landscape_mesh(name) \
                else omd3serve.mesh(name)
        if m and m.get('verts') and m.get('tris'):
            cache[name] = (np.array(m['verts'], dtype=np.float64),
                           np.array(m['tris'], dtype=np.int64).reshape(-1, 3))
        else:
            cache[name] = None
    return cache[name]


#: Each mesh's collision as [(vertices, triangles wound outward)], one per convex part of
#: omd3-hulls.json; [] for a mesh with none (its render triangles are its collision).
_COLLISION = {}


def _collision(name):
    if name not in _COLLISION:
        parts = []
        for part in (_hulls().get(name) or {}).get('parts') or []:
            v = np.asarray(part['verts'], np.float64)
            if part.get('tris'):
                t = np.asarray(part['tris'], np.int64).reshape(-1, 3)
            else:
                from riftlord.build.navigation import recastgen
                t = recastgen._hull_tris(v)
            if not len(t):
                continue
            tri = v[t]
            n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            #: outward: away from the convex part's centre
            inward = np.einsum('ij,ij->i', n, tri.mean(1) - v.mean(0)) < 0
            t[inward] = t[inward][:, [0, 2, 1]]
            parts.append((v, t))
        _COLLISION[name] = parts
    return _COLLISION[name]


def _up_faces(name, M):
    """(n, 3, 3) world triangles of a placed mesh's collision that face up no steeper than
    MAX_SLOPE_DEG: its convex hulls (_collision), else its render triangles, which UE winds
    clockwise seen from outside; either reversed under a mirror."""
    parts = _collision(name)
    if parts:
        tris = [((np.c_[v, np.ones(len(v))] @ M)[:, :3])[t] for v, t in parts]
        tri = np.vstack(tris)
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    else:
        got = _mesh(name)
        if got is None:
            return np.zeros((0, 3, 3))
        v, t = got
        tri = ((np.c_[v, np.ones(len(v))] @ M)[:, :3])[t]
        n = -np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    if np.linalg.det(M[:3, :3]) < 0:
        n = -n
    size = np.linalg.norm(n, axis=1)
    up = (n[:, 2] >= np.cos(np.radians(MAX_SLOPE_DEG)) * size) & (size > 1e-9)
    return tri[up]


def walk_top(pieces):
    """The height a walker stands at on one placed piece made of `pieces` ([(mesh, 4x4
    row-vector matrix)]), or None when it has no upward face: the highest upward collision
    face (_up_faces) over each column (CELL), then the span's height (CELL_HEIGHT) holding
    the most columns, and the highest of them. A piece's box top stands higher wherever
    crenellations, trim or rails stand on its floor, and a face hidden under another part
    is not floor."""
    tri = [_up_faces(name, np.asarray(M, np.float64)) for name, M in pieces]
    tri = np.vstack(tri) if tri else np.zeros((0, 3, 3))
    if not len(tri):
        return None
    lo = tri[:, :, :2].min(axis=(0, 1))
    shape = np.floor((tri[:, :, :2].max(axis=(0, 1)) - lo) / CELL).astype(int) + 1
    top = np.full(shape, -np.inf)
    for a, b, c in tri:
        #: the column centres inside the triangle's xy box
        i0, j0 = np.ceil((np.minimum(np.minimum(a, b), c)[:2] - lo) / CELL - 0.5).astype(int)
        i1, j1 = np.floor((np.maximum(np.maximum(a, b), c)[:2] - lo) / CELL - 0.5).astype(int)
        if i1 < i0 or j1 < j0:
            continue
        x = lo[0] + (np.arange(i0, i1 + 1) + 0.5) * CELL
        y = lo[1] + (np.arange(j0, j1 + 1) + 0.5) * CELL
        X, Y = np.meshgrid(x, y, indexing='ij')
        d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(d) < 1e-9:
            continue
        u = ((b[1] - c[1]) * (X - c[0]) + (c[0] - b[0]) * (Y - c[1])) / d
        v = ((c[1] - a[1]) * (X - c[0]) + (a[0] - c[0]) * (Y - c[1])) / d
        inside = (u >= 0) & (v >= 0) & (u + v <= 1)
        z = np.where(inside, u * a[2] + v * b[2] + (1 - u - v) * c[2], -np.inf)
        cell = top[i0:i1 + 1, j0:j1 + 1]
        np.maximum(cell, z, out=cell)
    z = np.sort(top[np.isfinite(top)])
    if not len(z):
        #: faces too small to cover a column centre: the highest of them
        return float(tri[:, :, 2].max())
    #: the columns from i up to end[i] lie within one span's height of column i
    end = np.searchsorted(z, z + CELL_HEIGHT, side='right')
    best = int(np.argmax(end - np.arange(len(z))))
    return float(z[end[best] - 1])


#: walk_top of an added item over its own translation, by its piece and turn (item_walk_top)
_WALK_OFFSETS = {}


def item_walk_top(item):
    """walk_top of one plan `added` item (added_meshes), or None. Kept per piece and turn,
    so the same piece placed again costs nothing."""
    m = item.get('matrix')
    if not m:
        return None
    key = json.dumps([item.get(k) for k in ('kind', 'name', 'pack', 'object', 'source',
                                           'collision')]
                     + [round(v, 6) for v in m[:12]], default=str)
    if key not in _WALK_OFFSETS:
        at = dict(item, matrix=list(m[:12]) + [0.0, 0.0, 0.0, 1.0])
        _WALK_OFFSETS[key] = walk_top(added_meshes(at))
    off = _WALK_OFFSETS[key]
    return None if off is None else off + float(m[14])


def nav_volumes(level_name, classes=(EXCLUSION, BOUNDS)):
    """[{cls, name, min, max, matrix (rows), inverse}] for the level's nav volumes."""
    from riftlord.build import levelbuild
    from riftlord.formats.omd3 import meshcat
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import volumes
    pkg = volumes._package(level_name)
    names = [n for n, _h in pkg.names]
    out = []
    for i, ex in enumerate(pkg.exports):
        cls = levelbuild.resolve(pkg, struct.unpack_from('<i', ex['meta'], 0)[0])[1]
        if cls not in classes:
            continue
        data = ex['data']
        props = meshcat._walk(data, names, 0, len(data))
        if 'RootComponent' not in props:
            continue
        root = struct.unpack_from('<i', data, props['RootComponent'][1])[0]
        if not 0 < root <= len(pkg.exports):
            continue
        cdata = pkg.exports[root - 1]['data']
        cprops = meshcat._walk(cdata, names, 0, len(cdata))

        def vec(field, default):
            return (struct.unpack_from('<3f', cdata, cprops[field][1])
                    if field in cprops else default)
        if 'BrushBodySetup' not in cprops:
            continue
        setup = struct.unpack_from('<i', cdata, cprops['BrushBodySetup'][1])[0]
        elements = _convex_elements(pkg, names, setup) if 0 < setup <= len(
            pkg.exports) else []
        points = [q for e in elements for q in e]
        if not points:
            continue
        m = np.array(omd3level._matrix(vec('RelativeLocation', (0.0, 0.0, 0.0)),
                                       vec('RelativeRotation', (0.0, 0.0, 0.0)),
                                       vec('RelativeScale3D', (1.0, 1.0, 1.0))))
        out.append({'cls': cls, 'name': levelbuild._name_of(pkg, i), 'index': i + 1,
                    'min': np.min(points, axis=0), 'max': np.max(points, axis=0),
                    'elements': [np.array(e, dtype=np.float64) for e in elements],
                    'matrix': m, 'inverse': np.linalg.inv(m)})
    return out


def _convex_elements(pkg, names, index):
    """[[local point]] per convex element of one BodySetup export."""
    from riftlord.formats.omd3 import meshcat
    from riftlord.formats.omd3 import meshhull
    data = pkg.exports[index - 1]['data']
    top = meshcat._walk(data, names, 0, len(data))
    if 'AggGeom' not in top:
        return []
    _k, at, size = top['AggGeom']
    agg = meshcat._walk(data, names, at, at + size)
    if 'ConvexElems' not in agg:
        return []
    _ak, ia, asize = agg['ConvexElems']
    count = struct.unpack_from('<i', data, ia)[0]
    o, out = ia + 4 + meshhull.INNER_TAG, []
    for _ in range(max(count, 0)):
        el, o = meshhull._walk_to_end(data, names, o, ia + asize)
        if 'VertexData' not in el:
            continue
        _vk, va, _vs = el['VertexData']
        n = struct.unpack_from('<i', data, va)[0]
        out.append([struct.unpack_from('<3f', data, va + 4 + meshhull.INNER_TAG + k * 12)
                    for k in range(n)])
    return out


def hull_triangles(points):
    """(n, 3, 3) triangles on the convex hull of a small point set (every triple whose
    plane has all the points on one side)."""
    p = np.unique(np.round(np.asarray(points, dtype=np.float64), 3), axis=0)
    n = len(p)
    out = []
    if n < 4:
        return np.zeros((0, 3, 3))
    scale = np.ptp(p, axis=0).max() or 1.0
    for a in range(n):
        for b in range(a + 1, n):
            for c in range(b + 1, n):
                normal = np.cross(p[b] - p[a], p[c] - p[a])
                if np.linalg.norm(normal) < 1e-6 * scale * scale:
                    continue
                side = (p - p[a]) @ normal
                tol = 1e-4 * scale * np.linalg.norm(normal)
                if (side <= tol).all() or (side >= -tol).all():
                    out.append((p[a], p[b], p[c]))
    return np.array(out) if out else np.zeros((0, 3, 3))


def inside_volume(vol, pts):
    """Mask of world points inside a volume's local box."""
    q = (np.c_[pts, np.ones(len(pts))] @ vol['inverse'].T)[:, :3]
    return np.all((q >= vol['min']) & (q <= vol['max']), axis=1)


def blocking_volumes(level_name, plan):
    """World triangles of the brushes of the volumes that stop enemies: their convex
    elements, which a walker stands on like any other collision."""
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import leveldata
    from riftlord.mapeditor import volumes
    edits = {v['name']: v for v in volumes.clean(plan.get('volumes'))}
    stops = {}
    #: export index -> the world matrix the plan's move, turn or resize gives it
    placed = {}
    for v in leveldata.level(level_name).get('volumes') or []:
        e = edits.get(v.get('name')) or {}
        if not e.get('gone'):
            #: what the plan's Blocks makes it stop, else what it stops as shipped
            stops[v['index']] = e['blocks'] if e.get('blocks') and v.get('blocks') \
                else v.get('stops')
            if e.get('at') or e.get('matrix'):
                placed[v['index']] = np.array(omd3level._matrix(*volumes.edited_transform(v, e)))
    out = []
    #: a player blocking volume the plan makes stop everyone blocks too
    for v in nav_volumes(level_name, tuple(set(BLOCKING_CLASSES) | set(volumes.BLOCK_CLASSES))):
        if stops.get(v['index']) not in BLOCKING_STOPS:
            continue
        matrix = placed.get(v['index'], v['matrix'])
        for element in v['elements']:
            tri = hull_triangles(element)
            if len(tri):
                w = np.c_[tri.reshape(-1, 3), np.ones(len(tri) * 3)] @ matrix.T
                out.append(w[:, :3].reshape(-1, 3, 3))
    #: the volumes the plan adds that stop enemies: the BlockAll donor's brush where the
    #: plan puts it, sized to the added box
    added = [v for v in (volumes.added_volume(e) for e in edits.values() if e.get('added'))
             if v and v['stops'] in BLOCKING_STOPS]
    donor = volumes.donor('everyone') if added else None
    if donor:
        brush = next((v for v in nav_volumes(volumes.DONOR_LEVEL, BLOCKING_CLASSES)
                      if v['index'] == donor['index']), None)
        for e in added if brush else []:
            matrix = np.array(omd3level._matrix(e['location'], e['rotation'],
                                                volumes._donor_scale(e['scale'], e['min'],
                                                                     e['max'], donor)))
            for element in brush['elements']:
                tri = hull_triangles(element)
                if len(tri):
                    w = np.c_[tri.reshape(-1, 3), np.ones(len(tri) * 3)] @ matrix.T
                    out.append(w[:, :3].reshape(-1, 3, 3))
    return out


# -- rasterising ----------------------------------------------------------------------

class Heightfield:
    """Solid voxels of a region, kept sparse as sorted (column, level) keys."""

    def __init__(self, lo, hi, cell=CELL, ch=CELL_HEIGHT):
        self.cell, self.ch = cell, ch
        self.lo = np.array(lo, dtype=np.float64)
        self.hi = np.array(hi, dtype=np.float64)
        self.nx = int(math.ceil((hi[0] - lo[0]) / cell))
        self.ny = int(math.ceil((hi[1] - lo[1]) / cell))
        self.nz = int(math.ceil((hi[2] - lo[2]) / ch)) + 1
        self.chunks = []
        self.missing = collections.Counter()
        self.triangles = 0

    def add_points(self, pts, walk):
        ix = np.floor((pts[:, 0] - self.lo[0]) / self.cell).astype(np.int64)
        iy = np.floor((pts[:, 1] - self.lo[1]) / self.cell).astype(np.int64)
        iz = np.floor((pts[:, 2] - self.lo[2]) / self.ch).astype(np.int64)
        ok = (ix >= 0) & (ix < self.nx) & (iy >= 0) & (iy < self.ny) & \
             (iz >= 0) & (iz < self.nz)
        if not ok.any():
            return
        key = ((ix[ok] * self.ny + iy[ok]) * self.nz + iz[ok])
        z = pts[ok, 2]
        wz = np.where(walk[ok], z, -np.inf)
        self.chunks.append(self._reduce(key, z, wz))

    @staticmethod
    def _reduce(key, z, wz):
        order = np.argsort(key, kind='stable')
        key, z, wz = key[order], z[order], wz[order]
        first = np.r_[True, key[1:] != key[:-1]]
        starts = np.nonzero(first)[0]
        return (key[starts], np.maximum.reduceat(z, starts),
                np.maximum.reduceat(wz, starts))

    def add_triangles(self, tri):
        """Sample every triangle of `tri` (n, 3, 3) at half a cell."""
        hi, lo = self.hi, self.lo
        keep = np.all(tri.max(1) >= lo, axis=1) & np.all(tri.min(1) <= hi, axis=1)
        tri = tri[keep]
        if not len(tri):
            return
        self.triangles += len(tri)
        a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
        n = np.cross(b - a, c - a)
        length = np.linalg.norm(n, axis=1)
        nz = np.where(length > 0, n[:, 2] / np.maximum(length, 1e-12), 0.0)
        #: either winding: the run's top decides which side is the floor
        walk = np.abs(nz) >= math.cos(math.radians(MAX_SLOPE_DEG))
        edge = np.maximum(np.maximum(np.linalg.norm(b - a, axis=1),
                                     np.linalg.norm(c - a, axis=1)),
                          np.linalg.norm(c - b, axis=1))
        step = min(self.cell, self.ch * 4) * 0.5
        steps = np.clip(np.ceil(edge / step), 1, 600).astype(np.int64)
        for k in np.unique(steps):
            idx = np.nonzero(steps == k)[0]
            u, v = np.meshgrid(np.arange(k + 1), np.arange(k + 1), indexing='ij')
            m = (u + v) <= k
            u = (u[m] / k)[None, :, None]
            v = (v[m] / k)[None, :, None]
            per = max(1, POINTS_PER_BATCH // u.shape[1])
            for s in range(0, len(idx), per):
                sel = idx[s:s + per]
                A, B, C = a[sel][:, None, :], b[sel][:, None, :], c[sel][:, None, :]
                pts = (A + u * (B - A) + v * (C - A)).reshape(-1, 3)
                self.add_points(pts, np.repeat(walk[sel], u.shape[1]))

    def add_box(self, lo, hi):
        """A solid box: every column it covers is solid over its height."""
        xs = np.arange(max(lo[0], self.lo[0]), min(hi[0], self.hi[0]) + self.cell * 0.5,
                       self.cell * 0.5)
        ys = np.arange(max(lo[1], self.lo[1]), min(hi[1], self.hi[1]) + self.cell * 0.5,
                       self.cell * 0.5)
        zs = np.arange(lo[2], hi[2] + self.ch, self.ch)
        if not (len(xs) and len(ys) and len(zs)):
            return
        X, Y, Z = np.meshgrid(xs, ys, zs, indexing='ij')
        pts = np.c_[X.ravel(), Y.ravel(), Z.ravel()]
        self.add_points(pts, np.zeros(len(pts), dtype=bool))

    def voxels(self):
        """(keys, top z, walkable top z) over every voxel, keys sorted."""
        key = np.concatenate([c[0] for c in self.chunks])
        z = np.concatenate([c[1] for c in self.chunks])
        wz = np.concatenate([c[2] for c in self.chunks])
        self.chunks = [self._reduce(key, z, wz)]
        return self.chunks[0]


def runs(hf, min_gap):
    """Solid runs per column: (column, floor z, ceiling z, walkable).

    A gap narrower than `min_gap` (no agent fits) joins the runs either side.
    """
    key, z, wz = hf.voxels()
    col = key // hf.nz
    iz = key % hf.nz
    gap = (iz[1:] - iz[:-1] - 1) * hf.ch
    start = np.r_[True, (col[1:] != col[:-1]) | (gap >= min_gap)]
    starts = np.nonzero(start)[0]
    ends = np.r_[starts[1:], len(key)] - 1
    floor = np.maximum.reduceat(z, starts)
    walk_top = np.maximum.reduceat(wz, starts)
    walkable = walk_top >= floor - FLAG_MERGE
    rcol = col[starts]
    bottom = hf.lo[2] + iz[starts] * hf.ch
    nxt_same = np.r_[rcol[1:] == rcol[:-1], False]
    ceiling = np.where(nxt_same, np.r_[bottom[1:], np.inf], np.inf)
    del ends
    return rcol, floor, ceiling, walkable


class Floor:
    """Walkable spans of one agent with their four neighbour links."""

    def __init__(self, hf, rcol, floor, ceiling, walkable, agent):
        radius, step, height = agent
        keep = walkable & (ceiling - floor >= height)
        self.hf = hf
        self.col = rcol[keep]
        self.z = floor[keep]
        self.top = ceiling[keep]
        self.agent = agent
        self.link()

    def link(self):
        hf = self.hf
        radius, step, height = self.agent
        n = len(self.col)
        ix, iy = self.col // hf.ny, self.col % hf.ny
        order = np.lexsort((self.z, self.col))
        assert np.all(order == np.arange(n)) or True
        col_sorted = self.col[order]
        self.nb = np.full((n, 4), -1, dtype=np.int64)
        for d, (dx, dy) in enumerate(DIRS):
            jx, jy = ix + dx, iy + dy
            ok = (jx >= 0) & (jx < hf.nx) & (jy >= 0) & (jy < hf.ny)
            want = jx * hf.ny + jy
            lo = np.searchsorted(col_sorted, want, 'left')
            hi = np.searchsorted(col_sorted, want, 'right')
            best = np.full(n, np.inf)
            pick = np.full(n, -1, dtype=np.int64)
            for k in range(int((hi - lo).max()) if n else 0):
                cand = lo + k
                valid = ok & (cand < hi)
                c = order[np.minimum(cand, n - 1)]
                dz = np.abs(self.z[c] - self.z)
                room = np.minimum(self.top[c], self.top) - np.maximum(self.z[c], self.z)
                good = valid & (dz <= step) & (room >= height) & (dz < best)
                best = np.where(good, dz, best)
                pick = np.where(good, c, pick)
            self.nb[:, d] = pick

    def keep(self, mask):
        """Drop every span not in `mask`, relinking."""
        self.col, self.z, self.top = self.col[mask], self.z[mask], self.top[mask]
        self.link()

    def xy(self):
        hf = self.hf
        ix, iy = self.col // hf.ny, self.col % hf.ny
        return (hf.lo[0] + (ix + 0.5) * hf.cell, hf.lo[1] + (iy + 0.5) * hf.cell)

    def erode(self):
        """Drop spans nearer than the agent radius to an edge (chamfer distance)."""
        radius = self.agent[0]
        n = len(self.col)
        if not n:
            return
        nb = self.nb
        dist = np.where((nb < 0).any(axis=1), 0, 10 ** 6).astype(np.int64)
        #: chamfer units: 2 per cell step, so the radius in half cells
        limit = int(round(2.0 * radius / self.hf.cell))
        for _ in range(limit * 4):
            new = dist.copy()
            for d in range(4):
                j = nb[:, d]
                has = j >= 0
                new[has] = np.minimum(new[has], dist[j[has]] + 2)
                #: diagonal: two steps, d then d+1
                k = np.where(has, nb[np.maximum(j, 0), (d + 1) % 4], -1)
                hk = has & (k >= 0)
                new[hk] = np.minimum(new[hk], dist[k[hk]] + 3)
            if np.array_equal(new, dist):
                break
            dist = new
        self.keep(dist >= limit)

    def islands(self, min_cells=MIN_ISLAND_CELLS):
        """Drop connected pieces of floor smaller than `min_cells`."""
        n = len(self.col)
        parent = np.arange(n)

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        src = np.repeat(np.arange(n), 4)
        dst = self.nb.ravel()
        ok = dst >= 0
        for a, b in zip(src[ok].tolist(), dst[ok].tolist()):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        roots = np.array([find(i) for i in range(n)])
        counts = collections.Counter(roots.tolist())
        self.keep(np.array([counts[r] >= min_cells for r in roots.tolist()], dtype=bool))


# -- polygons -------------------------------------------------------------------------

def _plane(xs, ys, zs):
    """(a, b, c) of z = a + b x + c y, least squares (flat when the points are)."""
    if len(zs) < 3 or np.ptp(zs) <= 1e-3:
        return (float(np.mean(zs)), 0.0, 0.0)
    A = np.c_[np.ones(len(xs)), xs, ys]
    coef, *_ = np.linalg.lstsq(A, zs, rcond=None)
    return tuple(float(c) for c in coef)


def patches(fl):
    """(patch per span, plane per patch): planar pieces of floor inside one tile each."""
    from collections import deque
    hf = fl.hf
    n = len(fl.col)
    ix = fl.col // hf.ny
    iy = fl.col % hf.ny
    cx = (hf.lo[0] + (ix + 0.5) * hf.cell)
    cy = (hf.lo[1] + (iy + 0.5) * hf.cell)
    per_tile = max(1, int(round(TILE / hf.cell)))
    tile = ((ix // per_tile) * 100000 + iy // per_tile).tolist()
    nb = fl.nb.tolist()
    z = fl.z.tolist()
    cxl, cyl = cx.tolist(), cy.tolist()
    pid = [-1] * n
    planes = []
    for s in np.lexsort((ix, iy)).tolist():
        if pid[s] >= 0:
            continue
        p = len(planes)
        pid[s] = p
        members = [s]
        todo = deque([s])
        x0, y0 = cxl[s], cyl[s]
        a, b, c = z[s], 0.0, 0.0
        refit = 4
        while todo:
            i = todo.popleft()
            for j in nb[i]:
                if j < 0 or pid[j] >= 0 or tile[j] != tile[s]:
                    continue
                if abs(z[j] - (a + b * (cxl[j] - x0) + c * (cyl[j] - y0))) > PLANE_TOL:
                    continue
                pid[j] = p
                members.append(j)
                todo.append(j)
                if len(members) >= refit:
                    m = np.array(members)
                    a, b, c = _plane(cx[m] - x0, cy[m] - y0, fl.z[m])
                    refit *= 2
        m = np.array(members)
        a, b, c = _plane(cx[m] - x0, cy[m] - y0, fl.z[m])
        planes.append((a - b * x0 - c * y0, b, c))
    return _absorb(fl, np.array(pid), planes, tile, cxl, cyl, z)


def _absorb(fl, pid, planes, tile, cx, cy, z):
    """Patches smaller than MIN_PATCH_CELLS joined to the neighbouring patch (same
    tile) whose plane is nearest their own heights, within the agent's step."""
    nb = fl.nb.tolist()
    step = fl.agent[1]
    for _round in range(4):
        members = collections.defaultdict(list)
        for i, p in enumerate(pid.tolist()):
            members[p].append(i)
        moved = 0
        for p, cells in sorted(members.items(), key=lambda kv: len(kv[1])):
            if len(cells) >= MIN_PATCH_CELLS:
                break
            best = None
            for i in cells:
                for j in nb[i]:
                    q = int(pid[j]) if j >= 0 else -1
                    if q < 0 or q == p or tile[j] != tile[i]:
                        continue
                    a, b, c = planes[q]
                    err = max(abs(z[k] - (a + b * cx[k] + c * cy[k])) for k in cells)
                    if err <= step and (best is None or err < best[0]):
                        best = (err, q)
            if best:
                pid[cells] = best[1]
                moved += 1
        if not moved:
            break
    return pid, planes


def regions(fl, pid):
    """Region per span (1-based): monotone sweeps within each patch, so no region
    surrounds a hole (Recast's rcBuildRegionsMonotone)."""
    hf = fl.hf
    ix = (fl.col // hf.ny).tolist()
    iy = fl.col % hf.ny
    nb = fl.nb.tolist()
    pidl = pid.tolist()
    reg = [0] * len(ix)
    nxt = 1
    order = np.lexsort((np.array(ix), iy)).tolist()
    rows = collections.OrderedDict()
    iyl = iy.tolist()
    for s in order:
        rows.setdefault(iyl[s], []).append(s)
    for _y, row in rows.items():
        sweep_of = {}
        sweeps = []                       #: [nei, ns]
        prev = collections.Counter()
        for s in row:
            j = nb[s][2]
            sid = sweep_of.get(j) if j >= 0 and pidl[j] == pidl[s] else None
            if sid is None:
                sid = len(sweeps)
                sweeps.append([0, 0])
            k = nb[s][3]
            if k >= 0 and pidl[k] == pidl[s] and reg[k]:
                nr = reg[k]
                sw = sweeps[sid]
                if sw[0] in (0, nr):
                    sw[0] = nr
                    sw[1] += 1
                    prev[nr] += 1
                else:
                    sw[0] = -1
            sweep_of[s] = sid
        ids = []
        for nei, ns in sweeps:
            if nei > 0 and prev[nei] == ns:
                ids.append(nei)
            else:
                ids.append(nxt)
                nxt += 1
        for s in row:
            reg[s] = ids[sweep_of[s]]
    return np.array(reg)


#: Corner (dx, dy) of a cell recorded for its boundary on each side, walking clockwise.
CORNER = ((1, 0), (1, 1), (0, 1), (0, 0))


def _walk(s, d, r, reg, nb, ix, iy, seen):
    """Clockwise raw contour [(gx, gy, region across the edge ending here)]."""
    pts = []
    i, dd = s, d
    for _guard in range(4 * len(reg) + 8):
        j = nb[i][dd]
        if j < 0 or reg[j] != r:
            seen.add((i, dd))
            ox, oy = CORNER[dd]
            pts.append((ix[i] + ox, iy[i] + oy, reg[j] if j >= 0 else 0))
            dd = (dd + 3) % 4
        else:
            i = j
            dd = (dd + 1) % 4
        if i == s and dd == d:
            return pts
    raise ValueError('contour walk did not close')


def _dp(pts, a, b, tol, keep):
    """Douglas-Peucker on pts[a..b] (indices into a closed list, b may wrap)."""
    n = len(pts)
    idx = [k % n for k in range(a, b + 1)]
    if len(idx) <= 2:
        return
    (x0, y0), (x1, y1) = pts[idx[0]][:2], pts[idx[-1]][:2]
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    best, at = -1.0, None
    for k in idx[1:-1]:
        px, py = pts[k][0] - x0, pts[k][1] - y0
        dist = abs(dx * py - dy * px) / length if length else math.hypot(px, py)
        if dist > best:
            best, at = dist, k
    if best > tol:
        keep.add(at)
        pos = idx.index(at)
        _dp(pts, a, a + pos, tol, keep)
        _dp(pts, a + pos, b, tol, keep)


def simplify(raw, cell):
    """Simplified clockwise contour [(gx, gy)]: every point where the region across
    changes is kept, wall runs are reduced to within MAX_ERROR, region borders stay
    straight between their ends, and no edge runs longer than MAX_EDGE."""
    n = len(raw)
    keep = {k for k in range(n) if raw[k][2] != raw[(k + 1) % n][2]}
    if not keep:
        lo = min(range(n), key=lambda k: (raw[k][0] + raw[k][1], raw[k][0]))
        hi = max(range(n), key=lambda k: (raw[k][0] + raw[k][1], raw[k][0]))
        keep = {lo, hi}
    tol = MAX_ERROR / cell
    ks = sorted(keep)
    for a, b in zip(ks, ks[1:] + [ks[0] + n]):
        if raw[(a + 1) % n][2] == 0:
            _dp(raw, a, b, tol, keep)
    ks = sorted(keep)
    longest = MAX_EDGE / cell
    out = []
    for a, b in zip(ks, ks[1:] + [ks[0] + n]):
        out.append(a)
        if raw[(a + 1) % n][2] != 0:
            continue
        span = [k % n for k in range(a, b + 1)]
        last = 0
        for pos in range(1, len(span)):
            p, q = raw[span[last]], raw[span[pos]]
            if math.hypot(p[0] - q[0], p[1] - q[1]) > longest and pos - 1 > last:
                out.append(span[pos - 1])
                last = pos - 1
    return [raw[k][:2] for k in out]


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _in_tri(p, a, b, c):
    return _cross(a, b, p) >= 0 and _cross(b, c, p) >= 0 and _cross(c, a, p) >= 0


def triangulate(poly):
    """Ear-clipped triangles (index triples) of a simple counter-clockwise polygon."""
    idx = list(range(len(poly)))
    tris = []
    guard = 0
    while len(idx) > 3 and guard < 10 * len(poly) + 10:
        guard += 1
        m = len(idx)
        found = False
        for k in range(m):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % m]
            if _cross(poly[a], poly[b], poly[c]) <= 0:
                continue
            if any(_in_tri(poly[o], poly[a], poly[b], poly[c]) and poly[o] not in (
                    poly[a], poly[b], poly[c]) for o in idx if o not in (a, b, c)):
                continue
            tris.append((a, b, c))
            del idx[k]
            found = True
            break
        if not found:
            #: only reflex or flat corners left: drop a flat one
            flat = next((k for k in range(m) if _cross(poly[idx[k - 1]], poly[idx[k]],
                                                       poly[idx[(k + 1) % m]]) == 0), None)
            if flat is None:
                break
            del idx[flat]
    if len(idx) == 3 and _cross(poly[idx[0]], poly[idx[1]], poly[idx[2]]) > 0:
        tris.append(tuple(idx))
    return tris


def _convex(poly_idx, pts):
    n = len(poly_idx)
    for k in range(n):
        if _cross(pts[poly_idx[k - 1]], pts[poly_idx[k]], pts[poly_idx[(k + 1) % n]]) < 0:
            return False
    return True


def merge_convex(tris, pts):
    """Triangles merged greedily (longest shared edge first) into convex polygons of at
    most MAX_VERTS vertices."""
    polys = {k: list(t) for k, t in enumerate(tris)}
    while True:
        edge = {}
        for k, p in polys.items():
            for e in range(len(p)):
                edge[(p[e], p[(e + 1) % len(p)])] = k
        best = None
        for (u, v), k in edge.items():
            q = edge.get((v, u))
            if q is None or q <= k:
                continue
            P, Q = polys[k], polys[q]
            if len(P) + len(Q) - 2 > MAX_VERTS:
                continue
            i = P.index(u)
            j = Q.index(u)
            #: P from v round to u, then Q's vertices strictly between u and v
            merged = P[i + 1:] + P[:i + 1]
            merged = merged + (Q[j:] + Q[:j])[1:-1]
            if len(set(merged)) != len(merged) or not _convex(merged, pts):
                continue
            length = (pts[u][0] - pts[v][0]) ** 2 + (pts[u][1] - pts[v][1]) ** 2
            if best is None or length > best[0]:
                best = (length, k, q, merged)
        if best is None:
            return list(polys.values())
        _l, k, q, merged = best
        polys[k] = merged
        del polys[q]


def polygons(fl, log=None):
    """(polys, edge ids): convex planar polygons covering the floor, counter-clockwise
    from above, one id per edge two polygons share."""
    hf = fl.hf
    pid, planes = patches(fl)
    reg = regions(fl, pid)
    nb = fl.nb.tolist()
    ix = (fl.col // hf.ny).tolist()
    iy = (fl.col % hf.ny).tolist()
    regl = reg.tolist()
    pidl = pid.tolist()
    seen = set()
    done = set()
    polys = []
    loops = collections.Counter()
    for s in range(len(regl)):
        r = regl[s]
        for d in range(4):
            j = nb[s][d]
            if (j >= 0 and regl[j] == r) or (s, d) in seen:
                continue
            raw = _walk(s, d, r, regl, nb, ix, iy, seen)
            loops[r] += 1
            if r in done:
                continue
            done.add(r)
            cw = simplify(raw, hf.cell)
            ccw = cw[::-1]
            clean = []
            for p in ccw:
                if not clean or clean[-1] != p:
                    clean.append(p)
            while len(clean) > 1 and clean[0] == clean[-1]:
                clean.pop()
            if len(clean) < 3:
                continue
            a, b, c = planes[pidl[s]]
            for tri in merge_convex(triangulate(clean), clean):
                poly = []
                for k in tri:
                    x = float(hf.lo[0] + clean[k][0] * hf.cell)
                    y = float(hf.lo[1] + clean[k][1] * hf.cell)
                    poly.append((x, y, float(a + b * x + c * y)))
                polys.append(poly)
    holes = sum(1 for n in loops.values() if n > 1)
    if log and holes:
        log('  %d region(s) with more than one boundary loop' % holes)
    if MEND:
        polys = _mended(polys)
    ids = navsurfgen.allocate_edge_ids(polys, tol=hf.cell * 0.25, step=fl.agent[1])
    if WELD:
        for _round in range(WELD_ROUNDS):
            polys, ids = weld(polys, ids, hf.cell * 0.25, fl.agent[1])
            if MEND:
                polys = _mended(polys)
                ids = navsurfgen.allocate_edge_ids(polys, tol=hf.cell * 0.25,
                                                   step=fl.agent[1])
        #: a last weld for the corners the last mend inserted (it adds no corners)
        polys, ids = weld(polys, ids, hf.cell * 0.25, fl.agent[1])
        polys = [p for p in polys if _width(p) >= MIN_WIDTH]
        if MEND:
            #: corners still inside a neighbour's edge go into it, on that edge's own
            #: plane; the split edges pair only where both sides agree
            polys = _mended(polys)
        ids = navsurfgen.allocate_edge_ids(polys, tol=hf.cell * 0.25, step=fl.agent[1])
        ids = _unweld_strays(polys, ids)
    return polys, ids


def _unweld_strays(polys, ids):
    """Ids whose two edges still disagree in height after welding, cleared to walls."""
    by_id = collections.defaultdict(list)
    for i, row in enumerate(ids):
        for k, e in enumerate(row):
            if e:
                by_id[e].append((i, k))
    out = [list(r) for r in ids]
    for e, members in by_id.items():
        if len(members) != 2:
            continue
        (i, k), (j, m) = members
        ni, nj = len(polys[i]), len(polys[j])
        if abs(polys[i][k][2] - polys[j][(m + 1) % nj][2]) > WELD_EPS or \
                abs(polys[i][(k + 1) % ni][2] - polys[j][m][2]) > WELD_EPS:
            out[i][k] = out[j][m] = 0
    return out


def _mended(polys):
    """Polygons with every T-junction corner inserted, none over MAX_RECORD_VERTS."""
    from riftlord.build.navigation import doorsplice
    rings = doorsplice._mend([[(v, doorsplice.NEW_TAG, None) for v in p] for p in polys])
    return [[r[0] for r in piece] for ring in rings for piece in doorsplice._cap(ring)]


def weld(polys, ids, tol, step):
    """(polys, ids) with every shared edge the same 3D segment on both sides.

    Shipped graphs agree to the float on both ends of every shared edge (all 675 pairs on
    Scenario_09, all 292 on Scenario_16); patch planes meeting at a seam disagree by up
    to 70 uu. Each set of linked corners takes one member's height, and a polygon left
    non-planar by that has its moved corners cut off into planar pieces.
    """
    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    by_id = collections.defaultdict(list)
    for i, row in enumerate(ids):
        for k, e in enumerate(row):
            if e:
                by_id[e].append((i, k))
    for members in by_id.values():
        if len(members) != 2:
            continue
        (i, k), (j, m) = members
        ni, nj = len(polys[i]), len(polys[j])
        for a, b in (((i, k), (j, (m + 1) % nj)), ((i, (k + 1) % ni), (j, m))):
            parent[find(a)] = find(b)
    #: each set of linked corners takes the height of its member on the largest
    #: polygon, so that polygon keeps its plane and only smaller ones are cut
    area = [abs(_area_xy(p)) for p in polys]
    chosen = {}
    for corner in list(parent):
        root = find(corner)
        if root not in chosen or area[corner[0]] > area[chosen[root][0]]:
            chosen[root] = corner
    out = [list(p) for p in polys]
    for corner in list(parent):
        src = chosen[find(corner)]
        x, y, _z = out[corner[0]][corner[1]]
        out[corner[0]][corner[1]] = (x, y, polys[src[0]][src[1]][2])
    final = []
    for before, poly in zip(polys, out):
        if len(poly) > 3 and _off_plane(poly) > PLANAR_EPS:
            final += _carve(before, poly)
        elif len(poly) > MAX_RECORD_VERTS:
            half = len(poly) // 2
            final += [poly[:half + 1], poly[half:] + poly[:1]]
        else:
            final.append(poly)
    return final, navsurfgen.allocate_edge_ids(final, tol=tol, step=step)


def _carve(before, poly):
    """Planar pieces of a welded polygon: its unmoved corners keep the old plane, and
    each run of moved corners is cut off with the unmoved corners either side of it."""
    n = len(poly)
    moved = [abs(a[2] - b[2]) > PLANAR_EPS for a, b in zip(before, poly)]
    still = [k for k in range(n) if not moved[k]]
    if len(still) < 3 or _area_xy([poly[k] for k in still]) < MIN_PIECE_AREA:
        return _fan(poly)
    pieces = [[poly[k] for k in still]]
    for i, a in enumerate(still):
        b = still[(i + 1) % len(still)]
        run = [(a + j) % n for j in range(1, (b - a) % n)]
        if not run:
            continue
        sub = [poly[a]] + [poly[k] for k in run] + [poly[b]]
        if _area_xy(sub) < MIN_PIECE_AREA:
            return _fan(poly)
        pieces += [sub] if len(sub) == 3 or _off_plane(sub) <= PLANAR_EPS else _fan(sub)
    return pieces


def _width(poly):
    """A polygon's area over its longest edge, in xy: about its thickness (uu)."""
    n = len(poly)
    longest = max(math.dist(poly[k][:2], poly[(k + 1) % n][:2]) for k in range(n))
    return abs(_area_xy(poly)) / longest if longest else 0.0


def _area_xy(poly):
    n = len(poly)
    return 0.5 * sum(poly[k][0] * poly[(k + 1) % n][1] - poly[(k + 1) % n][0] * poly[k][1]
                     for k in range(n))


def _strict(poly, k):
    """True when corner k of a polygon turns (is not on a straight run)."""
    n = len(poly)
    o, a, b = poly[k - 1], poly[k], poly[(k + 1) % n]
    cross = (a[0] - o[0]) * (b[1] - a[1]) - (a[1] - o[1]) * (b[0] - a[0])
    return abs(cross) > 1e-6 * (math.dist(o[:2], a[:2]) * math.dist(a[:2], b[:2]) or 1.0)


def _fan(poly):
    """Triangles covering a convex polygon with no zero-area piece and no corner left
    inside another triangle's edge: a quad on its shorter diagonal, anything else by
    clipping ears at turning corners."""
    n = len(poly)
    if n == 4 and all(_strict(poly, k) for k in range(4)):
        #: a quad: the shorter diagonal
        if math.dist(poly[0][:2], poly[2][:2]) <= math.dist(poly[1][:2], poly[3][:2]):
            return [[poly[0], poly[1], poly[2]], [poly[0], poly[2], poly[3]]]
        return [[poly[1], poly[2], poly[3]], [poly[1], poly[3], poly[0]]]
    #: clip ears at turning corners, the one with the shortest new edge first: every
    #: corner is used and no ear is flat
    ring = list(poly)
    tris = []
    while len(ring) > 3:
        m = len(ring)
        turning = [k for k in range(m) if _strict(ring, k)]
        if not turning:
            break
        k = min(turning, key=lambda k: math.dist(ring[k - 1][:2], ring[(k + 1) % m][:2]))
        tris.append([ring[k - 1], ring[k], ring[(k + 1) % m]])
        del ring[k]
    if len(ring) == 3 and abs(_area_xy(ring)) > 1e-6:
        tris.append(ring)
    return tris


def _off_plane(poly):
    """Largest distance of a vertex from the polygon's Newell plane."""
    nx = ny = nz = 0.0
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    c = [sum(v[k] for v in poly) / n for k in range(3)]
    return max(abs(((v[0] - c[0]) * nx + (v[1] - c[1]) * ny + (v[2] - c[2]) * nz) / length)
               for v in poly)


# -- the whole job --------------------------------------------------------------------

def region(level_name):
    """(lo, hi) of the level's NavMeshBoundsVolume, or None."""
    vols = [v for v in nav_volumes(level_name, (BOUNDS,))]
    if not vols:
        return None
    los, his = [], []
    for v in vols:
        corners = np.array([[x, y, z, 1.0] for x in (v['min'][0], v['max'][0])
                            for y in (v['min'][1], v['max'][1])
                            for z in (v['min'][2], v['max'][2])])
        w = (corners @ v['matrix'].T)[:, :3]
        los.append(w.min(0))
        his.append(w.max(0))
    return np.min(los, axis=0), np.max(his, axis=0)


def heightfield(level_name, plan, log=print):
    bounds = region(level_name)
    if bounds is None:
        raise ValueError('%s has no NavMeshBoundsVolume' % level_name)
    lo, hi = bounds[0] - REGION_MARGIN, bounds[1] + REGION_MARGIN
    hf = Heightfield(lo, hi)
    t = time.time()
    pieces = final_pieces(level_name, plan)
    boxes = blocking_volumes(level_name, plan)
    box_key = [b.round(1).tobytes() for b in boxes]
    import hashlib
    digest = hashlib.sha1(repr((RASTER_VERSION, NAV_FLAG, level_name, CELL, CELL_HEIGHT,
                                MAX_SLOPE_DEG, lo.tolist(), hi.tolist(), box_key,
                                [(n, M.round(3).tolist()) for n, M in pieces])
                               ).encode()).hexdigest()
    cached = os.path.join(CACHE, digest + '.npz')
    if os.path.exists(cached):
        got = np.load(cached)
        hf.chunks = [(got['key'], got['z'], got['wz'])]
        hf.bounds = bounds
        log('  heightfield from cache (%d pieces)' % len(pieces))
        return hf
    for name, M in pieces:
        got = _mesh(name)
        if got is None:
            hf.missing[name] += 1
            continue
        v, tri = got
        w = (np.c_[v, np.ones(len(v))] @ M)[:, :3]
        if np.any(w.max(0) < lo) or np.any(w.min(0) > hi):
            continue
        hf.add_triangles(w[tri])
    for tri in boxes:
        hf.add_triangles(tri)
    key, z, wz = hf.voxels()
    os.makedirs(CACHE, exist_ok=True)
    np.savez(cached, key=key, z=z, wz=wz)
    log('  rasterised %d pieces, %d triangles in %.0fs (%d meshes unread)'
        % (len(pieces), hf.triangles, time.time() - t, len(hf.missing)))
    hf.bounds = bounds
    return hf


def floors(hf, level_name, log=print):
    """[Floor] per agent: linked, inside bounds, outside exclusions, eroded."""
    t = time.time()
    min_gap = min(a[2] for a in AGENTS)
    rcol, floor, ceiling, walkable = runs(hf, min_gap)
    excl = nav_volumes(level_name, (EXCLUSION,))
    out = []
    for agent in AGENTS:
        fl = Floor(hf, rcol, floor, ceiling, walkable, agent)
        x, y = fl.xy()
        pts = np.c_[x, y, fl.z]
        ok = np.ones(len(pts), dtype=bool)
        if CLIP_TO_BOUNDS:
            ok &= np.all((pts >= hf.bounds[0]) & (pts <= hf.bounds[1]), axis=1)
        for v in excl:
            ok &= ~inside_volume(v, pts + np.array([0.0, 0.0, 1.0]))
        fl.keep(ok)
        fl.erode()
        fl.islands()
        out.append(fl)
        log('  agent r%.0f h%.0f: %d floor cells' % (agent[0], agent[2], len(fl.col)))
    log('  floors in %.0fs (%d exclusion volumes)' % (time.time() - t, len(excl)))
    return out


def graphs_for(level_name, plan=None, log=print):
    """[(polys, ids, tags, flags)] per agent for the level's final geometry."""
    plan = plan or {}
    if GENERATOR == 'roundtrip':
        #: the level's own shipped polygons, ids, tags and flags, through our writer
        from riftlord.build.navigation import doorsplice
        out = []
        for g in range(len(AGENTS)):
            rows = doorsplice.shipped_graph(level_name, g)
            tags = [r[2] for r in rows] if ROUNDTRIP_TAGS else \
                [[(-4 if t in (-3, -2) else t) for t in r[2]] for r in rows]
            out.append(([r[0] for r in rows], [r[1] for r in rows], tags,
                        [r[3] for r in rows]))
        return out
    if GENERATOR in ('recast', 'recast-upstream'):
        from riftlord.build.navigation import recastgen
        recastgen.SOURCE = 'upstream' if GENERATOR == 'recast-upstream' else 'ue'
        return recastgen.graphs_for(level_name, plan, log)
    hf = heightfield(level_name, plan, log)
    legs, _exits = route_legs(level_name, plan)
    anchors = [p for _l, a, b in legs for p in (a, b)]
    anchors += [at for _l, at in gameplay_points(level_name, plan)]
    out = []
    for g, fl in enumerate(floors(hf, level_name, log)):
        t = time.time()
        polys, ids = polygons(fl, log)
        tags, flags = [None] * len(polys), [DEFAULT_FLAGS] * len(polys)
        if KEEP_SHIPPED_DOORS:
            from riftlord.build.navigation import doorsplice
            polys, ids, tags, flags, note = doorsplice.splice(
                polys, ids, level_name, plan, g, fl.agent[1])
            log('  agent r%.0f: %s' % (fl.agent[0], note))
        if not KEEP_UNANCHORED:
            before = len(polys)
            chosen = anchored(polys, ids, anchors)
            polys, ids, tags, flags = [[x[i] for i in chosen]
                                       for x in (polys, ids, tags, flags)]
            log('  agent r%.0f: kept %d of %d polygons on floor a route reaches'
                % (fl.agent[0], len(polys), before))
        polys, ids, tags, flags = wall_last(polys, ids, tags, flags)
        log('  agent r%.0f: %d polygons in %.0fs' % (fl.agent[0], len(polys),
                                                       time.time() - t))
        out.append((polys, ids, tags, flags))
    return out


def wall_last(polys, ids, tags, flags):
    """The same polygons, one with a wall edge moved last and turned so that edge is its
    final one: the last record's final edge cannot carry an id (navsurfgen
    drop_last_edge_id), so no shared edge is lost to it."""
    for i in range(len(polys) - 1, -1, -1):
        if 0 in ids[i]:
            k = ids[i].index(0)
            turn = (k + 1) % len(ids[i])
            rest = [j for j in range(len(polys)) if j != i]
            out = []
            for x in (polys, ids, tags):
                row = x[i]
                if row is not None:
                    row = row[turn:] + row[:turn]
                out.append([x[j] for j in rest] + [row])
            return out[0], out[1], out[2], [flags[j] for j in rest] + [flags[i]]
    return polys, ids, tags, flags


def anchored(polys, ids, anchors):
    """Indices of the polygons in connected pieces of floor holding at least one anchor;
    all of them when none does."""
    parent = list(range(len(polys)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    by_id = collections.defaultdict(list)
    for i, row in enumerate(ids):
        for e in row:
            if e:
                by_id[e].append(i)
    for members in by_id.values():
        for j in members[1:]:
            parent[find(j)] = find(members[0])
    keep = set()
    for pt in anchors:
        keep |= {find(i) for i, p in enumerate(polys) if _over(p, pt)}
    if not keep:
        return list(range(len(polys)))
    #: islands within ISLAND_REACH of kept floor and not above it by more than
    #: ISLAND_RISE stay too: a unit can fall or be knocked onto them, never climb
    cell = 250.0
    grow = int(math.ceil(ISLAND_REACH / cell))
    top = {}
    for i, p in enumerate(polys):
        if find(i) in keep:
            for v in p:
                gx, gy = int(v[0] // cell), int(v[1] // cell)
                for dx in range(-grow, grow + 1):
                    for dy in range(-grow, grow + 1):
                        k = (gx + dx, gy + dy)
                        if v[2] > top.get(k, -1e18):
                            top[k] = v[2]
    area = collections.defaultdict(float)
    for i, p in enumerate(polys):
        area[find(i)] += navsurf.polygon_area(p)
    for i, p in enumerate(polys):
        if find(i) not in keep and area[find(i)] >= MIN_ISLAND_AREA and any(
                v[2] <= top.get((int(v[0] // cell), int(v[1] // cell)), -1e18) + ISLAND_RISE
                for v in p):
            keep.add(find(i))
    return [i for i in range(len(polys)) if find(i) in keep]


def _over(poly, pt):
    """True when `pt` stands over `poly`, at most FLOOR_BELOW above it."""
    if not navsurf._inside_xy(poly, pt[0], pt[1]):
        return False
    return -50.0 <= pt[2] - navsurfgen.plane_z(poly, pt[0], pt[1]) <= FLOOR_BELOW


def gameplay_points(level_name, plan=None):
    """[(label, location)] of every ground spawner in the final level and every door one
    of them uses (flyer-only spawners and their doors hang in the air)."""
    from riftlord.mapeditor import leveldata
    plan = plan or {}
    level = leveldata.level(level_name)
    wiring = {w['name']: w for w in plan.get('wiring') or []}
    where, ground_doors = [], set()
    for sp in level.get('spawners') or []:
        w = wiring.get(sp['export']) or {}
        start = w['start'] if 'start' in w else sp.get('start')
        if w.get('gone') or not start:
            continue
        where.append(('spawner %s' % sp['export'], tuple(w.get('at') or sp['location'])))
        door = w['door'] if 'door' in w else sp.get('door')
        if door is not None:
            ground_doors.add(str(door).split('.')[-1])
    for sp in plan.get('spawners') or []:
        if sp.get('kind') == 'flyer' or sp.get('start') is None:
            continue
        where.append(('spawner %s' % sp.get('id'), tuple(sp['at'])))
        if sp.get('door') is not None:
            ground_doors.add(sp['door'])
    for name, d in (level.get('doors') or {}).items():
        w = wiring.get(name) or {}
        if not w.get('gone') and name in ground_doors:
            where.append(('door %s' % name, tuple(w.get('at') or d['location'])))
    for d in plan.get('doors') or []:
        if d.get('id') in ground_doors:
            where.append(('door %s' % d.get('id'), tuple(d['at'])))
    return where


def buffer_for(level_name, plan=None, log=print):
    """A sealed surface buffer for the level's final geometry."""
    got = graphs_for(level_name, plan, log)
    items = [(polys, ids, flags, None, None, None, tags)
             for polys, ids, tags, flags in got]
    bbox = navsurfgen.bbox_of(got[0][0])
    return navsurfgen.buffer_per_agent(items, bbox)


def install(uasset, uexp, level_name, plan, log=print):
    """(uasset, uexp, notes) with the walkable graph regenerated."""
    from riftlord.build.navigation import navsurfinject
    buf = buffer_for(level_name, plan, log)
    bad = check(buf)
    if bad:
        raise ValueError('regenerated graph fails its checks: %s' % '; '.join(bad[:3]))
    ua, ux, rows = navsurfinject.set_surface_bytes(uasset, uexp, buf)
    failed = [name for name, ok in rows if not ok]
    if failed:
        raise ValueError('installing the regenerated graph: %s' % '; '.join(failed))
    counts = '/'.join(str(len(navsurf.sections(buf, h))) for h in navsurf.graphs(buf))
    return ua, ux, ['walkable graph regenerated from the final geometry (%s polygons)'
                    % counts]


def wanted(level_name, plan):
    """True when the switch is on and the plan removes, moves or adds a piece a walker
    stands on or bumps into."""
    if not ENABLED:
        return False
    if ALWAYS:
        return True
    parts = {}
    if any(added_meshes(a, parts) for a in plan.get('added') or []):
        return True
    from riftlord.mapeditor import leveldata
    touched = {r['id'] if isinstance(r, dict) else r for r in plan.get('removed') or []}
    touched |= {e['id'] for e in plan.get('edits') or []}
    if not touched:
        return False
    flags = component_flags(level_name)
    return any(p['id'] in touched and collides(p, flags)
               for p in leveldata.level(level_name)['pieces'])


# -- checks and comparison ------------------------------------------------------------

def check(buf):
    """Problems with a regenerated buffer: parse, tree, pointer and id rules."""
    out = []
    heads = navsurf.graphs(buf)
    if len(heads) != 2:
        return ['expected two agent graphs, found %d' % len(heads)]
    from riftlord.build.navigation import navvol
    if not navvol.verify(buf):
        out.append('checksum does not verify')
    for g, h in enumerate(heads):
        got = navsurf.sections(buf, h)
        if not got:
            out.append('graph %d does not parse' % g)
            continue
        out += navsurf.check_sections(buf, h, got)
        out += navsurf.check_tree(buf, h, got)
        counts = collections.Counter(e for s in got for e in s['edge_ids'] if e)
        odd = [e for e, c in counts.items() if c % 2]
        if odd:
            out.append('graph %d: %d edge id(s) quoted an odd number of times' % (g, len(odd)))
        out += pointer_problems(buf, h, got, g)
    return out


def pointer_problems(buf, head, got, g):
    """Edge k's across-pointer sits in slot k-1 (`+40` for edge 0) and names the record
    that shares edge k's id (navsurfgen.pointer_slot)."""
    starts = {s['at'] - head['origin']: i for i, s in enumerate(got)}
    by_id = collections.defaultdict(set)
    for i, s in enumerate(got):
        for e in s['edge_ids']:
            if e:
                by_id[e].add(i)
    bad = 0
    for i, s in enumerate(got):
        slots = [struct.unpack_from('<Q', buf, s['at'] + 40)[0]] + list(s['neighbours'])
        for k, e in enumerate(s['edge_ids']):
            want = by_id.get(e, set()) - {i} if e else set()
            have = slots[k] if k < len(slots) else 0
            if want and starts.get(have) not in want:
                bad += 1
            if not want and have:
                bad += 1
    return ['graph %d: %d edge pointer(s) break the slot rule' % (g, bad)] if bad else []


def _raster(polys, cell):
    """{(ix, iy): [z]} sampled at cell centres."""
    out = collections.defaultdict(list)
    for p in polys:
        xs = [v[0] for v in p]
        ys = [v[1] for v in p]
        for ix in range(int(math.floor(min(xs) / cell)), int(math.floor(max(xs) / cell)) + 1):
            x = (ix + 0.5) * cell
            for iy in range(int(math.floor(min(ys) / cell)),
                            int(math.floor(max(ys) / cell)) + 1):
                y = (iy + 0.5) * cell
                if navsurf._inside_xy(p, x, y):
                    out[(ix, iy)].append(navsurfgen.plane_z(p, x, y))
    return out


#: Sample spacing of the coverage comparison (uu).
COMPARE_CELL = 50.0
#: Two samples at one xy match when their heights are this close (uu).
COMPARE_DZ = 60.0
#: Two edges quoting one id are the same edge when their midpoints are this close (uu).
EDGE_MEET = 600.0


def coverage(shipped, ours, cell=COMPARE_CELL, dz=COMPARE_DZ):
    """(IoU, shipped covered by ours, ours covered by shipped) over xy samples."""
    a, b = _raster(shipped, cell), _raster(ours, cell)
    inter = only_a = only_b = 0
    for key in set(a) | set(b):
        za, zb = a.get(key, []), b.get(key, [])
        ma = sum(1 for z in za if any(abs(z - w) <= dz for w in zb))
        mb = sum(1 for z in zb if any(abs(z - w) <= dz for w in za))
        inter += min(ma, mb)
        only_a += len(za) - ma
        only_b += len(zb) - mb
    union = inter + only_a + only_b
    return (inter / union if union else 0.0,
            inter / (inter + only_a) if inter + only_a else 0.0,
            inter / (inter + only_b) if inter + only_b else 0.0)


def walker(polys, ids):
    """walk(a, b) -> distance crossing each shared edge at its point nearest the walker
    (a greedy string pull), or None."""
    import heapq
    from riftlord.build.navigation import navsurfextend
    #: Shipped graphs reuse one id for two unrelated shared edges (70 of 745 ids on
    #: Scenario_09 join polygons 3,000-17,000 uu apart), so only edges that meet count.
    by_id = collections.defaultdict(list)
    for i, row in enumerate(ids):
        n = len(row)
        for k, e in enumerate(row):
            if e:
                a, b = polys[i][k], polys[i][(k + 1) % n]
                by_id[e].append((i, ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0),
                                 (a[:2], b[:2])))
    #: portals[i]: [(neighbour, shared edge)] of each edge polygon i shares
    portals = collections.defaultdict(list)
    for members in by_id.values():
        for i, mi, seg in members:
            for j, mj, _s in members:
                if j != i and math.dist(mi, mj) <= EDGE_MEET:
                    portals[i].append((j, seg))

    def nearest(p, seg):
        (ax, ay), (bx, by) = seg
        dx, dy = bx - ax, by - ay
        t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / ((dx * dx + dy * dy) or 1.0)
        t = min(1.0, max(0.0, t))
        return (ax + t * dx, ay + t * dy)
    grid = collections.defaultdict(list)
    for i, p in enumerate(polys):
        for gx in range(int(min(v[0] for v in p) // 500), int(max(v[0] for v in p) // 500) + 1):
            for gy in range(int(min(v[1] for v in p) // 500),
                            int(max(v[1] for v in p) // 500) + 1):
                grid[(gx, gy)].append(i)

    def at(pt):
        cand = grid.get((int(pt[0] // 500), int(pt[1] // 500)), [])
        sub = [polys[i] for i in cand]
        k = navsurfextend.on_surface(sub, pt)
        return cand[k] if k is not None else None

    def walk(a, b):
        sa, sb = at(a), at(b)
        if sa is None or sb is None:
            return None
        if sa == sb:
            return math.dist(a[:2], b[:2])
        #: one state per polygon: the cheapest entry point found
        best, todo = {sa: 0.0}, [(0.0, sa, tuple(a[:2]))]
        while todo:
            d, i, p = heapq.heappop(todo)
            if i == sb:
                return d + math.dist(p, b[:2])
            if d > best[i]:
                continue
            for j, seg in portals[i]:
                q = nearest(p, seg)
                nd = d + math.dist(p, q)
                if nd < best.get(j, 1e18):
                    best[j] = nd
                    heapq.heappush(todo, (nd, j, q))
        return None
    walk.at = at
    return walk


def route_legs(level_name, plan=None):
    """[(label, a, b)]: each ground route leg, and each spawner's floor to the exit."""
    from riftlord.build.navigation import navobstruct
    from riftlord.build.navigation import navsurfextend
    from riftlord.mapeditor import planactors
    plan = plan or {}
    legs, _keep = navobstruct.route_legs(level_name, plan)
    out = [('leg %d' % n, a, b) for n, (a, b) in enumerate(legs)]
    level = planactors.level_actors(level_name)
    nodes, _removed, _p = planactors.graph(plan, level)
    exit_at = nodes[level['exit']]['at'] if level.get('exit') in nodes else None
    #: every exit: the level's and each rift's own (planactors.rift_exits)
    exit_keys = planactors.exits(nodes)
    wiring = {w['name']: w for w in plan.get('wiring') or []}
    spawners = [(n, tuple((wiring.get(n) or {}).get('at') or sp['location']),
                 (wiring.get(n) or {}).get('start') or sp.get('start'))
                for n, sp in level['spawners'].items()
                if not (wiring.get(n) or {}).get('gone')
                and ((wiring.get(n) or {}).get('start') or sp.get('start'))]
    spawners += [('spawner %s' % sp.get('id'), tuple(sp['at']), sp.get('start'))
                 for sp in plan.get('spawners') or [] if sp.get('start') is not None]
    for name, at, start in spawners:
        floor = (at[0], at[1], at[2] - navsurfextend.SPAWNER_ABOVE_FLOOR)
        for key in _route_exits(nodes, start, exit_keys):
            label = '%s to exit' % name if key == level.get('exit') else \
                '%s to exit %s' % (name, key)
            out.append((label, floor, nodes[key]['at']))
    return out, [exit_at] + [nodes[k]['at'] for k in exit_keys if k != level.get('exit')]


def _route_exits(nodes, start, exit_keys):
    """The exits a route from `start` reaches, in `exit_keys` order: each a walk a spawner's
    units make. The level's exit alone when the route reaches none (a route the plan check
    refuses)."""
    seen, todo = set(), [start]
    while todo:
        cur = todo.pop()
        if cur in seen or cur not in nodes:
            continue
        seen.add(cur)
        todo.extend(b['to'] for b in nodes[cur]['next'])
    got = [k for k in exit_keys if k in seen]
    return got or [k for k in exit_keys if nodes[k]['kind'] == 'level'][:1]


def disconnected(buf, level_name, plan=None):
    """[label] of each route leg (and spawner-to-exit walk) the first agent's graph in
    `buf` does not join."""
    head = navsurf.graphs(buf)[0]
    secs = navsurf.sections(buf, head)
    walk = walker([list(s['verts']) for s in secs], [list(s['edge_ids']) for s in secs])
    legs, _exits = route_legs(level_name, plan)
    return [label for label, a, b in legs if walk(a, b) is None]


#: How far below a spawner or door its floor may lie (uu).
FLOOR_BELOW = 1500.0


def unsupported(buf, level_name, plan=None):
    """[label] of each spawner and door in the final level with no floor of the first
    agent's graph under it (within FLOOR_BELOW)."""
    head = navsurf.graphs(buf)[0]
    polys = [list(s['verts']) for s in navsurf.sections(buf, head)]
    return [label for label, at in gameplay_points(level_name, plan)
            if not any(_over(p, at) for p in polys)]


def compare(level_name, shipped_buf, ours, plan=None, log=print):
    """Coverage, connectivity and leg lengths of `ours` against the shipped graph."""
    report = {'level': level_name, 'agents': []}
    heads = navsurf.graphs(shipped_buf)
    legs, exits = route_legs(level_name, plan)
    for g, (item, head) in enumerate(zip(ours, heads)):
        polys, ids = item[0], item[1]
        secs = navsurf.sections(shipped_buf, head)
        sp = [list(s['verts']) for s in secs]
        sids = [list(s['edge_ids']) for s in secs]
        iou, recall, precision = coverage(sp, polys)
        row = {'agent': g, 'shipped_polys': len(sp), 'our_polys': len(polys),
               'shipped_area': sum(navsurf.polygon_area(p) for p in sp) / 1e6,
               'our_area': sum(navsurf.polygon_area(p) for p in polys) / 1e6,
               'iou': iou, 'recall': recall, 'precision': precision, 'legs': []}
        if g == 0:
            ws, wo = walker(sp, sids), walker(polys, ids)
            for label, a, b in legs:
                row['legs'].append((label, ws(a, b), wo(a, b),
                                    ws.at(a) is not None, wo.at(a) is not None,
                                    ws.at(b) is not None, wo.at(b) is not None))
        report['agents'].append(row)
        log('  agent %d: polys %d vs %d shipped, area %.1fM vs %.1fM, IoU %.3f, '
            'recall %.3f, precision %.3f' % (g, len(polys), len(sp), row['our_area'],
                                              row['shipped_area'], iou, recall, precision))
    for label, s, o, sa, oa, sb, ob in report['agents'][0]['legs']:
        log('    %s: shipped %s, ours %s%s' % (
            label, '%.0f' % s if s is not None else 'none',
            '%.0f' % o if o is not None else 'NONE',
            '' if (oa and ob) else ' (ends off our floor: %s/%s)' % (oa, ob)))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--level', required=True)
    ap.add_argument('--record', help='a Missions record or plan .json')
    ap.add_argument('--json', help='write the comparison here')
    args = ap.parse_args(argv)
    plan = {}
    if args.record:
        with open(args.record) as fh:
            got = json.load(fh)
        plan = got.get('plan', got)
    t = time.time()
    ours = graphs_for(args.level, plan)
    buf = navsurfgen.buffer_per_agent(
        [(x[0], x[1], x[3], None, None, None, x[2]) for x in ours],
        navsurfgen.bbox_of(ours[0][0]))
    bad = check(buf)
    print('  buffer %d bytes, checks: %s' % (len(buf), '; '.join(bad[:4]) or 'ok'))
    report = compare(args.level, navsurf.buffer(args.level), ours, plan)
    report['seconds'] = time.time() - t
    report['checks'] = bad
    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(report, fh, indent=1, default=str)
    return 0 if not bad else 1


if __name__ == '__main__':
    raise SystemExit(main())
