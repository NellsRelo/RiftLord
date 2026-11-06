"""The floor under an unstable rift: a walkable polygon of the mission's walkable graph
directly below the rift actor, a short drop down, with no solid level piece in between.

The Crystal (Crystal Defense) spawns an unstable rift's units at the rift's own location
(SpawnEntityForEntity, AdjustIfPossibleButAlwaysSpawn); they land on the floor under it.
This module measures that drop on a built level's walkable graph (graph 0, and graph 1
where the buffer has it), checks the segment between the rift and that floor against the
level's solid pieces, and gives the Map Editor the height to place a rift, a ground
spawner or a ground path point at, over the floor the open plan leaves (plan_floor: a
scratch level's own floor pieces; a shipped level's graph with the plan's floor added and
removed). No floor under a rift, or a solid piece in between, refuses a build; a rift
higher than the shipped ones is a warning.

    python3 -m riftlord.build.navigation.riftfloor --level Scenario_02 --at 2150,6950,537
    python3 -m riftlord.build.navigation.riftfloor --pak PAK --record RECORD.json
"""

import argparse
import collections
import hashlib
import json
import math
import struct
import sys
import threading

from riftlord.build.navigation import navsurf
from riftlord.build.navigation import navsurfgen
from riftlord.core import buildmessage

#: Highest a shipped rift stands over the floor under it, per agent graph (uu):
#: Scenario_18's 11 red rifts (BP_OMDAISpawner_VorwickRift_C) stand 61.5 to 102.8 uu over
#: graph 0 (median 90.7) and 76.8 to 118.3 over graph 1; Unstable Rift A (proven in game)
#: 99.5 and 109.2.
SHIPPED_DROP_MAX = (102.8, 118.3)
#: Height over the floor past which a build warns, per agent graph (uu): SHIPPED_DROP_MAX
#: plus navsurfextend.GRAPH_HEIGHT_SLACK (13) for a regenerated graph's planes, rounded up.
#: A warning, never a refusal.
DROP_LIMITS = (116.0, 132.0)
#: The graph-0 limit, for messages.
DROP_LIMIT = DROP_LIMITS[0]
#: Least drop (uu): a floor plane up to GRAPH_HEIGHT_SLACK above the rift still counts as
#: under it (no shipped red rift is under 61.5).
DROP_MIN = -13.0
#: Height the Map Editor places a rift at over the floor (uu): the shipped red rifts'
#: median over graph 0.
SHIPPED_OFFSET = 90.7
#: The solid check stops this far above the graph's floor (uu): shipped floor polygons sit
#: about 6 uu over the real surface and a regenerated graph's planes move up to 13.
FLOOR_CLEAR = 20.0
#: Grid cell for the per-level polygon lookup (uu).
CELL = 500.0
#: Export names of the spawners Rift Lord adds (planactors.emit).
ADDED_PREFIX = 'RiftLordSpawner'


def polygons(buf):
    """[[vertices] per polygon] per agent graph of a surface buffer."""
    return [[list(s['verts']) for s in navsurf.sections(buf, h)] for h in navsurf.graphs(buf)]


def floor_under(polys, at):
    """(polygon index, floor z, drop) of the highest polygon under `at`'s xy whose plane
    there lies at most -DROP_MIN above `at`, or None."""
    best = None
    for i, p in enumerate(polys):
        if not navsurf._inside_xy(p, at[0], at[1]):
            continue
        z = navsurfgen.plane_z(p, at[0], at[1])
        drop = at[2] - z
        if drop >= DROP_MIN and (best is None or drop < best[2]):
            best = (i, z, drop)
    return best


def limit_of(graph):
    """DROP_LIMITS for agent graph `graph` (the last one for any further graph)."""
    return DROP_LIMITS[min(graph, len(DROP_LIMITS) - 1)]


def shipped_max_of(graph):
    """SHIPPED_DROP_MAX for agent graph `graph` (the last one for any further graph)."""
    return SHIPPED_DROP_MAX[min(graph, len(SHIPPED_DROP_MAX) - 1)]


#: What each agent graph's enemies are called in messages: graph 0 is the ordinary agent
#: (radius 42), graph 1 the large one (radius 80).
GRAPH_ENEMIES = ('ordinary enemies', 'large enemies')
#: How far under a floor plane a rift is still reported as sunk into that floor (uu):
#: navsurfextend.SURFACE_BELOW.
SUNK_REACH = 100.0


def check_rift(graphs, at):
    """[(graph, polygon, floor z, drop, ok)] per agent graph for a rift at `at`;
    polygon and floor z are None, and ok False, where no floor lies under it. A drop over
    DROP_LIMITS is still ok (high_notes reports it)."""
    out = []
    for g, polys in enumerate(graphs):
        got = floor_under(polys, at)
        if got is None:
            out.append((g, None, None, None, False))
        else:
            i, z, drop = got
            out.append((g, i, z, drop, True))
    return out


def sunk_depth(polys, at):
    """How far `at` lies under the nearest floor plane above it within SUNK_REACH, or None."""
    best = None
    for p in polys:
        if not navsurf._inside_xy(p, at[0], at[1]):
            continue
        depth = navsurfgen.plane_z(p, at[0], at[1]) - at[2]
        if -DROP_MIN < depth <= SUNK_REACH and (best is None or depth < best):
            best = depth
    return best


def _enemies(graphs):
    return ' and '.join(GRAPH_ENEMIES[min(g, len(GRAPH_ENEMIES) - 1)] for g in graphs)


def _named(label, at):
    return '%s at (%.0f, %.0f, %.0f)' % ((label,) + tuple(at[:3])) if at else label


def describe(label, rows, sunk=None, at=None):
    """[text] refusing a rift labelled `label` ("Unstable Rift 7"), at `at` when given,
    that has no walkable floor under it in one or more agent graphs (rows of check_rift);
    `sunk`, the depth sunk_depth measured on graph 0, says it lies under the floor instead."""
    missing = [g for g, poly, _z, _d, _ok in rows if poly is None]
    if not missing:
        return []
    if sunk is not None and 0 in missing:
        return ['%s is %.0f units under the floor enemies walk on. Raise it above the floor.'
                % (_named(label, at), sunk)]
    if len(missing) == len(rows):
        return ['%s has no floor enemies can walk under it. Move it over floor, or put a '
                'floor piece under it.' % _named(label, at)]
    return ['%s has no floor under it that %s can walk on. Move it over wider open floor, '
            'or put a floor piece under it.' % (_named(label, at), _enemies(missing))]


def high_notes(label, rows):
    """[text] warning that a rift labelled `label` stands higher over its floor than
    DROP_LIMITS allows on some agent graph (rows of check_rift), with the shipped highest."""
    for g, poly, _z, drop, _ok in rows:
        if poly is not None and drop > limit_of(g):
            return ['%s stands %.0f units above the floor, higher than any shipped rift '
                    '(%.0f units). Its enemies should still land; test it in game, or lower '
                    'it.' % (label, drop, shipped_max_of(g))]
    return []


def rift_label(index):
    """The Map Editor's label for the unstable rift with indexInLevel `index`."""
    return 'Unstable Rift %d' % index


# -- the built level ---------------------------------------------------------------------

def _props(data, names):
    from riftlord.build import apply as applier
    return {name: (kind, at, size) for name, kind, at, size in applier._properties(data, names)}


def placed(uasset, uexp):
    """[(export name, indexInLevel, (x, y, z))] of the unstable rifts Rift Lord placed in a
    built level (their root component's RelativeLocation)."""
    from riftlord.formats.ue.uassetio import Package
    from riftlord.mapeditor import planactors
    pkg = Package(uasset, uexp)
    pkg.load_payloads()
    names = [n for n, _h in pkg.names]

    def export_name(i):
        return names[struct.unpack_from('<I', pkg.exports[i]['meta'], 16)[0]]
    out = []
    for i, e in enumerate(pkg.exports):
        if not export_name(i).startswith(ADDED_PREFIX):
            continue
        cls = struct.unpack_from('<i', e['meta'], 0)[0]
        if cls >= 0 or names[struct.unpack_from('<I', pkg.imports[-cls - 1], 20)[0]] \
                != planactors.UNSTABLE_CLASS:
            continue
        props = _props(e['data'], names)
        index = struct.unpack_from('<i', e['data'], props['indexInLevel'][1])[0] \
            if 'indexInLevel' in props else 1
        loc = None
        if 'RootComponent' in props:
            root = struct.unpack_from('<i', e['data'], props['RootComponent'][1])[0]
            if 0 < root <= len(pkg.exports):
                cdata = pkg.exports[root - 1]['data']
                cprops = _props(cdata, names)
                if 'RelativeLocation' in cprops:
                    loc = struct.unpack_from('<3f', cdata, cprops['RelativeLocation'][1])
        out.append((export_name(i), index, loc))
    return out


# -- solid pieces between a rift and its floor -------------------------------------------

def _hits(tris, x, y, top, bottom):
    """Height of the highest triangle crossing the vertical segment (x, y, top..bottom),
    or None. `tris` is an (n, 3, 3) array."""
    import numpy as np
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    d = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
    ok = np.abs(d) > 1e-9
    d = np.where(ok, d, 1.0)
    u = ((b[:, 1] - c[:, 1]) * (x - c[:, 0]) + (c[:, 0] - b[:, 0]) * (y - c[:, 1])) / d
    v = ((c[:, 1] - a[:, 1]) * (x - c[:, 0]) + (a[:, 0] - c[:, 0]) * (y - c[:, 1])) / d
    w = 1.0 - u - v
    inside = ok & (u >= 0) & (v >= 0) & (w >= 0)
    z = u * a[:, 2] + v * b[:, 2] + w * c[:, 2]
    crossing = inside & (z < top) & (z > bottom)
    return float(z[crossing].max()) if crossing.any() else None


def solid_between(level_name, plan, at, floor_z, pieces=None):
    """(mesh, height) of the highest solid level piece crossing the segment from `at` down
    to FLOOR_CLEAR above `floor_z`, or None. Pieces are the plan's final ones
    (navregen.final_pieces) less what a Crystal parks."""
    import numpy as np
    from riftlord.build.navigation import navregen
    from riftlord.formats.omd3 import meshcat
    if pieces is None:
        from riftlord.mapeditor import crystalpark
        pieces = navregen.final_pieces(level_name, crystalpark.with_parked(level_name, plan))
    top, bottom = float(at[2]), float(floor_z) + FLOOR_CLEAR
    if top <= bottom:
        return None
    cat = meshcat.load()
    best = None
    for name, M in pieces:
        info = cat.get(name)
        if info:
            o, e = np.array(info['origin']), np.array(info['extent'])
            corners = np.array([[o[0] + sx * e[0], o[1] + sy * e[1], o[2] + sz * e[2], 1.0]
                                for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
            w = corners @ M
            lo, hi = w[:, :3].min(0), w[:, :3].max(0)
            if not (lo[0] <= at[0] <= hi[0] and lo[1] <= at[1] <= hi[1]
                    and lo[2] < top and hi[2] > bottom):
                continue
        got = navregen._mesh(name)
        if got is None:
            continue
        v, tri = got
        wv = (np.c_[v, np.ones(len(v))] @ M)[:, :3]
        z = _hits(wv[tri], float(at[0]), float(at[1]), top, bottom)
        if z is not None and (best is None or z > best[1]):
            best = (name, z)
    return best


# -- the probe gate ----------------------------------------------------------------------

#: The gate row that reports rift heights: a note, never a failure.
HEIGHT_GATE = 'unstable rift heights over their floor (a note)'


def assess(files, plan, level_name):
    """{floor, solid, notes, seen, solid_note, count} for the unstable rifts Rift Lord
    placed in a written level (`files`: its .umap and .uexp, leveltitle.level_files): `floor` refuses a rift
    over no walkable floor, `solid` one with a solid piece between it and its floor, `notes`
    warn of a rift higher over its floor than DROP_LIMITS, `seen` gives each rift's height.
    None when the level places no unstable rift."""
    from riftlord.build import leveltitle
    from riftlord.build.navigation import navsurfextend
    umap = leveltitle.level_entry(files)
    if umap is None or umap[:-5] + '.uexp' not in files:
        return None
    uexp = files[umap[:-5] + '.uexp']
    rifts = placed(files[umap], uexp)
    if not rifts:
        return None
    graphs = polygons(navsurfextend._surface_of(files[umap], uexp))
    floor_bad, solid_bad, notes, seen, solid_note = [], [], [], [], ''
    pieces = None
    for _name, index, at in sorted(rifts, key=lambda r: r[1]):
        label = rift_label(index)
        if at is None:
            print('riftfloor: %s has no location in the built level' % label)
            notes.append('Rift Lord could not find %s in the built level, so its floor is '
                         'not checked.' % label)
            continue
        rows = check_rift(graphs, at)
        sunk = sunk_depth(graphs[0], at) if graphs and rows[0][1] is None else None
        floor_bad += describe(label, rows, sunk, at)
        notes += high_notes(label, rows)
        g0 = rows[0] if rows else None
        if g0 and g0[1] is not None:
            seen.append('%s %.0f uu over the floor' % (label, g0[3]))
            try:
                if pieces is None:
                    from riftlord.build.navigation import navregen
                    from riftlord.mapeditor import crystalpark
                    pieces = navregen.final_pieces(
                        level_name, crystalpark.with_parked(level_name, plan or {}))
                hit = solid_between(level_name, plan, at, g0[2], pieces)
            except Exception as exc:                 # noqa: BLE001
                hit, solid_note = None, 'not checked: %s' % exc
            if hit:
                solid_bad.append('%s has a solid piece (%s, at height %.0f) between it and '
                                 'the floor under it. Move the piece or the rift, or turn '
                                 'the piece\'s Collides off.'
                                 % (_named(label, at), hit[0], hit[1]))
    return {'floor': floor_bad, 'solid': solid_bad, 'notes': notes, 'seen': seen,
            'solid_note': solid_note, 'count': len(rifts)}


def gates(files, plan, level_name):
    """[(name, ok, detail)]: every unstable rift Rift Lord placed stands over walkable floor
    of each agent graph, with no solid piece between it and graph 0's floor; a third row,
    always ok, notes the rifts higher over their floor than DROP_LIMITS. Empty when the
    level places none."""
    got = assess(files, plan, level_name)
    if got is None:
        return []
    return [('unstable rifts stand over walkable floor (%d)' % got['count'],
             not got['floor'], '; '.join(got['floor']) or '; '.join(got['seen'])),
            ('no solid piece between an unstable rift and its floor', not got['solid'],
             '; '.join(got['solid']) or got['solid_note'] or 'clear'),
            (HEIGHT_GATE, True, '; '.join(got['notes']) or 'within the shipped heights')]


def _level_files(source):
    from riftlord.build import leveltitle
    return leveltitle.level_files(source)


def build_report(source, plan, level_name):
    """(problems, warnings) for a written mission (a loose build folder or a pak):
    problems refuse the build (a rift over no walkable floor, or a solid piece between a
    rift and its floor), warnings name a rift higher over its floor than any shipped one.
    Both empty when the level places no unstable rift."""
    got = assess(_level_files(source), plan, level_name)
    if got is None:
        return [], []
    return got['floor'] + got['solid'], got['notes']


def build_problems(source, plan, level_name):
    """The problems of build_report."""
    return build_report(source, plan, level_name)[0]


# -- the Map Editor ----------------------------------------------------------------------

_graphs = {}


def _gridded(polys):
    """(polys, {cell: [index]}): each polygon listed in every CELL its xy box touches."""
    grid = {}
    for i, p in enumerate(polys):
        xs, ys = [v[0] for v in p], [v[1] for v in p]
        for gx in range(int(math.floor(min(xs) / CELL)), int(math.floor(max(xs) / CELL)) + 1):
            for gy in range(int(math.floor(min(ys) / CELL)), int(math.floor(max(ys) / CELL)) + 1):
                grid.setdefault((gx, gy), []).append(i)
    return polys, grid


def _level_graph(level_name):
    """Graph 0's polygons of the level's shipped (or installed) walkable graph, gridded:
    (polys, {cell: [index]}), or None."""
    if level_name not in _graphs:
        try:
            buf = navsurf.buffer(level_name)
        except Exception:                            # noqa: BLE001
            buf = None
        _graphs[level_name] = _gridded(polygons(buf)[0]) if buf else None
    return _graphs[level_name]


def _box_top(box):
    """The top face of a world box (lo, hi), counter-clockwise from above."""
    (x0, y0, _z0), (x1, y1, z) = box
    return [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)]


def _moved_floor(level_name, plan):
    """[box] of the floor-class level pieces (navsurfcarve.is_floor) the plan moves, at their
    new place, topped at the height a walker stands on them (scratch.walk_box)."""
    from riftlord.build.navigation import navsurfcarve
    from riftlord.mapeditor import scratch
    from riftlord.formats.omd3 import meshcat
    from riftlord.mapeditor import leveldata
    moved = {int(e['id']): e['matrix'] for e in plan.get('edits') or []
             if isinstance(e, dict) and 'id' in e and e.get('matrix')}
    if not moved:
        return []
    cat = meshcat.load()
    out = []
    for i, piece in enumerate(leveldata.level(level_name).get('pieces') or []):
        info = cat.get(piece.get('mesh'))
        if piece.get('id', i) not in moved or not info or piece.get('instanced'):
            continue
        box = navsurfcarve._box(info, moved[piece.get('id', i)])
        if navsurfcarve.is_floor(piece.get('mesh'), box):
            item = {'kind': 'mesh', 'name': piece.get('mesh'),
                    'matrix': moved[piece.get('id', i)]}
            out.append(scratch.walk_box(item, box))
    return out


def _readable(plan):
    """`plan`, less the Fab pieces whose library cannot be read (not installed, or being
    rebuilt): the Map Editor draws those as missing and a build refuses them, so the rest
    of the floor still snaps."""
    from riftlord.mapeditor import fablibrary
    added = plan.get('added') or []
    bad = set()
    for pack in {a.get('pack') for a in added if a.get('kind') == 'fab'}:
        try:
            fablibrary.catalogue(pack)
        except SystemExit:
            bad.add(pack)
    if not bad:
        return plan
    return dict(plan, added=[a for a in added
                             if not (a.get('kind') == 'fab' and a.get('pack') in bad)])


def plan_floor(level_name, plan):
    """Graph 0's polygons of the walkable floor a mission will have, for the Map Editor's
    snaps. On a scratch level, the plan's own floor alone: its walkable kit, Fab and terrain
    pieces and grinder pits as the build's graph has them (scratch.floor_quads, before walls
    take cells away), none of the host's. On a shipped level, the level's graph less the
    polygons standing on floor the plan removes or moves away (navsurfcarve.doomed), plus
    the same plan floor and the tops of the floor-class meshes the plan adds or moves
    (navsurfcarve.is_floor), each at the height a walker stands on it (scratch.walk_box).
    The level graph's own list when the plan changes no floor;
    None when there is no floor at all."""
    from riftlord.mapeditor import scratch
    plan = _readable(plan)
    quads = scratch.floor_quads(plan)
    if scratch.is_scratch(plan):
        return quads or None
    from riftlord.build.navigation import navsurfcarve
    got = _level_graph(level_name)
    polys = got[0] if got else []
    if plan.get('removed') or plan.get('edits'):
        ids = dict(plan, removed=[r['id'] if isinstance(r, dict) else r
                                  for r in plan.get('removed') or []])
        gone, cover = navsurfcarve.floor_boxes(level_name, ids)
        doomed = navsurfcarve.doomed(polys, navsurfcarve.cut_boxes(gone, cover)) \
            if gone else set()
        if doomed:
            polys = [p for i, p in enumerate(polys) if i not in doomed]
        quads += [_box_top(box) for box in _moved_floor(level_name, plan)]
    for item in plan.get('added') or []:
        if item.get('kind', 'mesh') == 'mesh':
            quads += [_box_top(scratch.walk_box(item, box))
                      for _name, box in navsurfcarve.added_floor({'added': [item]})]
    if quads:
        polys = list(polys) + quads
    return polys or None


#: The plan entries the floor under a click depends on.
FLOOR_PLAN_KEYS = ('scratch', 'added', 'removed', 'edits')
#: Plan floors kept for the Map Editor's snaps, the latest used last.
PLAN_FLOORS_KEPT = 4
_plan_floors = collections.OrderedDict()
#: The server answers snaps on several threads.
_plan_floors_lock = threading.Lock()


def _floor(level_name, plan=None):
    """plan_floor gridded, (polys, {cell: [index]}) or None; the level's graph without a
    plan. Kept per level and plan floor (FLOOR_PLAN_KEYS), so a click after the first on an
    unchanged floor reads it back."""
    if not plan:
        return _level_graph(level_name)
    #: a Fab library installed or removed since is a different floor
    plan = _readable(plan)
    key = hashlib.sha1(json.dumps([level_name] + [plan.get(k) for k in FLOOR_PLAN_KEYS],
                                  sort_keys=True, default=str).encode()).hexdigest()
    with _plan_floors_lock:
        if key in _plan_floors:
            _plan_floors.move_to_end(key)
            return _plan_floors[key]
    polys = plan_floor(level_name, plan)
    base = _level_graph(level_name)
    got = base if base and polys is base[0] else _gridded(polys) if polys else None
    with _plan_floors_lock:
        _plan_floors[key] = got
        while len(_plan_floors) > PLAN_FLOORS_KEPT:
            _plan_floors.popitem(last=False)
    return got


def snap(level_name, at, reach=DROP_LIMIT + 200.0, offset=SHIPPED_OFFSET, plan=None):
    """{at, floor, polygon} for a rift (or, with `offset`
    navsurfextend.SPAWNER_ABOVE_FLOOR, a ground spawner) clicked at `at`: `offset` over the
    walkable floor under it (the highest one at most `reach` below the click's height plus
    `offset`), or {at: None} when there is none there. The floor is the level's graph, or
    with `plan` the floor that plan leaves (plan_floor)."""
    got = _floor(level_name, plan)
    if got is None:
        return {'at': None, 'floor': None, 'polygon': None, 'why': 'no walkable graph'}
    polys, grid = got
    key = (int(math.floor(at[0] / CELL)), int(math.floor(at[1] / CELL)))
    near = [polys[i] for i in grid.get(key, [])]
    probe = (at[0], at[1], at[2] + offset)
    hit = floor_under(near, probe)
    if hit is None or hit[2] > reach:
        return {'at': None, 'floor': None, 'polygon': None, 'why': 'no walkable floor under it'}
    i, z, _d = hit
    return {'at': [round(at[0], 1), round(at[1], 1), round(z + offset, 1)],
            'floor': round(z, 1), 'polygon': grid[key][i], 'why': ''}


#: How far below a ground spawner's height (plus SPAWNER_ABOVE_FLOOR) its floor is looked
#: for (uu): reaches a spawner dragged or left well above its floor.
SPAWNER_REACH = 1000.0


def snap_spawner(level_name, at, plan=None):
    """snap() for a ground spawner: navsurfextend.SPAWNER_ABOVE_FLOOR over its floor, the
    highest one at most SPAWNER_REACH below."""
    from riftlord.build.navigation import navsurfextend
    return snap(level_name, at, reach=SPAWNER_REACH, offset=navsurfextend.SPAWNER_ABOVE_FLOOR,
                plan=plan)


def snap_point(level_name, at, plan=None):
    """{at, floor, polygon} for a ground path point at `at`:
    navsurfextend.POINT_ABOVE_FLOOR over the floor polygon navsurfextend.on_surface puts it
    on (the floor a build treats it as standing on), or {at: None} when there is none, as
    for a point over a gap, which a build gives floor of its own. The floor is as snap()'s."""
    from riftlord.build.navigation import navsurfextend
    got = _floor(level_name, plan)
    if got is None:
        return {'at': None, 'floor': None, 'polygon': None, 'why': 'no walkable graph'}
    polys, grid = got
    key = (int(math.floor(at[0] / CELL)), int(math.floor(at[1] / CELL)))
    cand = grid.get(key, [])
    k = navsurfextend.on_surface([polys[i] for i in cand], at)
    if k is None:
        return {'at': None, 'floor': None, 'polygon': None, 'why': 'no walkable floor under it'}
    z = navsurfgen.plane_z(polys[cand[k]], at[0], at[1])
    return {'at': [round(at[0], 1), round(at[1], 1),
                   round(z + navsurfextend.POINT_ABOVE_FLOOR, 1)],
            'floor': round(z, 1), 'polygon': cand[k], 'why': ''}


def editor_snap(level_name, at, mode='', plan=None):
    """The Map Editor's height for an object at `at`: snap_spawner for `mode` 'spawner',
    snap_point for 'point', else snap() (an unstable rift), on the floor `plan` leaves."""
    if mode == 'spawner':
        return snap_spawner(level_name, at, plan)
    if mode == 'point':
        return snap_point(level_name, at, plan)
    return snap(level_name, at, plan=plan)


# -- ground spawners: how high they stand ------------------------------------------------

#: Most a ground spawner stands above the walkable floor under it in the shipped levels
#: (uu): the shipped median is 98 and Kobold spawners stand 54 to 142.
SPAWNER_DROP_MAX = 206.6


def placed_ground_spawners(uasset, uexp):
    """[(export name, indexInLevel, (x, y, z))] of the ground spawners (a
    StartingPathPoint, not an unstable rift) Rift Lord placed in a built level."""
    from riftlord.formats.ue.uassetio import Package
    from riftlord.mapeditor import planactors
    pkg = Package(uasset, uexp)
    pkg.load_payloads()
    names = [n for n, _h in pkg.names]
    out = []
    for i, e in enumerate(pkg.exports):
        name = names[struct.unpack_from('<I', e['meta'], 16)[0]]
        if not name.startswith(ADDED_PREFIX):
            continue
        cls = struct.unpack_from('<i', e['meta'], 0)[0]
        if cls < 0 and names[struct.unpack_from('<I', pkg.imports[-cls - 1], 20)[0]] \
                == planactors.UNSTABLE_CLASS:
            continue
        props = _props(e['data'], names)
        if 'StartingPathPoint' not in props or 'RootComponent' not in props:
            continue
        index = struct.unpack_from('<i', e['data'], props['indexInLevel'][1])[0] \
            if 'indexInLevel' in props else 1
        root = struct.unpack_from('<i', e['data'], props['RootComponent'][1])[0]
        if not 0 < root <= len(pkg.exports):
            continue
        cdata = pkg.exports[root - 1]['data']
        cprops = _props(cdata, names)
        if 'RelativeLocation' in cprops:
            out.append((name, index, struct.unpack_from('<3f', cdata,
                                                        cprops['RelativeLocation'][1])))
    return out


def spawner_labels(plan):
    """{indexInLevel: "Spawner N"} for the plan's added spawners, N the plan id the Map
    Editor shows."""
    return {sp['index']: 'Spawner %s' % sp.get('id') for sp in (plan or {}).get('spawners')
            or [] if sp.get('index') is not None}


def spawner_heights(graph, spawners, labels=None):
    """[text] for each ground spawner of `spawners` ([(name, index, at)]) standing more than
    SPAWNER_DROP_MAX above the walkable floor of `graph` (one agent graph's polygons) under
    it, floor_under as the Map Editor's snap measures it. `labels` ({index: label}) names
    them; one not in it is named by its export."""
    out = []
    for name, index, at in spawners:
        got = floor_under(graph, at)
        if got is not None and got[2] > SPAWNER_DROP_MAX:
            out.append('%s stands %.0f units above the floor, higher than any shipped '
                       'ground spawner (%.0f units). Lower it toward the floor, or test it '
                       'in game.' % ((labels or {}).get(index, name), got[2],
                                     SPAWNER_DROP_MAX))
    return out


def spawner_warnings(source, plan=None):
    """[text] of spawner_heights for a written mission (a loose build folder or a pak), on
    graph 0 of its own walkable graph, naming each spawner as the Map Editor does for
    `plan`. Empty when nothing stands too high."""
    from riftlord.build import leveltitle
    from riftlord.build.navigation import navsurfextend
    level = leveltitle.level_files(source)
    if not level:
        return []
    umap = next(n for n in level if n.endswith('.umap'))
    ua, ux = level[umap], level[umap[:-5] + '.uexp']
    spawners = placed_ground_spawners(ua, ux)
    if not spawners:
        return []
    graphs = polygons(navsurfextend._surface_of(ua, ux))
    return spawner_heights(graphs[0], spawners, spawner_labels(plan)) if graphs else []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--level', help='a level, for --at')
    ap.add_argument('--at', help='x,y,z of a rift: where the editor would place it')
    ap.add_argument('--pak', help='a built mission pak: its unstable rifts')
    ap.add_argument('--record', help='the mission record (its plan) for --pak')
    args = ap.parse_args(argv)
    if args.at:
        at = [float(v) for v in args.at.split(',')]
        print(json.dumps(snap(args.level, at)))
        return 0
    from riftlord.core import paksource
    files = paksource.entries(args.pak)
    rec = json.load(open(args.record)) if args.record else {}
    plan = rec.get('plan') or {}
    rows = gates(files, plan, rec.get('level') or plan.get('level'))
    for name, ok, detail in rows:
        print('  %s  %s: %s' % ('PASS' if ok else 'FAIL', name, detail))
    return 0 if all(ok for _n, ok, _d in rows) else 1


if __name__ == '__main__':
    sys.exit(main())
