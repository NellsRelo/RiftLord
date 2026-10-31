"""Flight volumes fitted to a level's free space.

`Grid.for_plan(level, plan, region)` voxelises the final geometry of a Rift Lord plan
(the level's pieces with the plan's moves, removals and additions, plus blocking
volumes) and grows it by a flyer's clearance. `Fitter(grid)` turns a route leg into
boxes that contain the route and no geometry: each span starts as the box around its
stretch of route and grows outward while it stays free, up to the corridor padding.
A span whose own stretch touches geometry is halved until it does not.

    python3 navvolclear.py --record RECORD.json     # report a mission's fitted graph
    python3 navvolclear.py --shipped Scenario_Ice_01  # shipped volumes vs geometry
"""

import math
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

#: Voxel edge (uu).
CELL = 50.0
#: Space kept between a flight volume and geometry (uu): a Hellbat's capsule (the
#: ACharacter default 34 radius / 88 half height; its Blueprints do not override it)
#: plus a margin.
CLEARANCE = 100.0
#: Half-size of the box around a stretch of route that a volume always keeps (uu).
CORE_HALF = 25.0
#: Shortest stretch of route a span is halved down to before it is given up as
#: blocked (uu).
MIN_SPAN = 300.0
#: Longest stretch of route one volume is fitted around (uu); a longer segment is cut
#: into equal pieces so each volume fits its own part of the level.
MAX_SPAN = 1000.0
#: Extra margin around the route's bounds when gathering geometry (uu).
REGION_MARGIN = 400.0

#: Words in a mesh or actor name that mark decoration without collision a flyer
#: meets: lights, cloth, effects and liquid surfaces.
SKIP_WORDS = ('torch', 'banner', 'flag', 'chandelier', 'candle', 'light', 'cobweb',
              'web', 'fx', 'decal', 'plane', 'water', 'lava', 'liquid', 'fog',
              'cloth', 'rope', 'chain', 'vine', 'ivy', 'grass', 'foliage', 'sky')

#: Most surface sample points held in memory at once while voxelising.
POINTS_PER_BATCH = 2000000

#: Blocking-volume kinds a flyer cannot pass (riftlord.volumes).
BLOCKING = ('everyone',)


def _words(name):
    return [w.lower() for w in re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+', name or '')]


def skipped(piece):
    """True for a piece left out of the obstacles (decoration, see SKIP_WORDS)."""
    from riftlord.formats.omd3 import omd3level
    words = set(_words(piece.get('mesh', ''))) | set(_words(piece.get('actor', '')))
    return any(w in words for w in SKIP_WORDS) or omd3level.is_liquid(piece.get('mesh'))


def _matrix(flat):
    return np.array(flat, dtype=np.float64).reshape(4, 4)


def final_pieces(level_name, plan):
    """[(mesh, 4x4 matrix)]: the level's pieces after the plan's edits, removals and
    additions (added meshes, and each static mesh of an added kit piece), decoration left
    out."""
    from riftlord.mapeditor import leveldata
    level = leveldata.level(level_name)
    edits = {e['id']: e for e in plan.get('edits') or []}
    removed = set()
    for r in plan.get('removed') or []:
        removed.add(r['id'] if isinstance(r, dict) else r)
    out = []
    for p in level['pieces']:
        if p['id'] in removed or skipped(p):
            continue
        e = edits.get(p['id'])
        out.append((p['mesh'], _matrix((e or p)['matrix'])))
    parts = {}
    for a in plan.get('added') or []:
        kind = a.get('kind', 'mesh')
        if kind == 'kit':
            #: each static mesh of a kit piece, as the walkable graph takes them
            from riftlord.build.navigation import navregen
            out += [(name, M) for name, M in navregen.added_meshes(a, parts)
                    if not skipped({'mesh': name, 'actor': a.get('name')})]
            continue
        if kind != 'mesh' or skipped({'mesh': a.get('name')}):
            continue
        out.append((a['name'], _matrix(a['matrix'])))
    return out, level


def _mesh(name, cache={}):
    if name not in cache:
        from riftlord.mapeditor import omd3serve
        m = omd3serve.mesh(name)
        if m and m.get('verts') and m.get('tris'):
            v = np.array(m['verts'], dtype=np.float64)
            t = np.array(m['tris'], dtype=np.int64).reshape(-1, 3)
            cache[name] = (v, t)
        else:
            cache[name] = None
    return cache[name]


class Grid:
    """Occupancy of a region, grown by CLEARANCE, with box-emptiness queries."""

    def __init__(self, lo, hi, cell=CELL):
        self.cell = cell
        self.lo = np.array(lo, dtype=np.float64)
        self.shape = tuple(int(math.ceil((hi[k] - lo[k]) / cell)) + 1 for k in range(3))
        self.solid = np.zeros(self.shape, dtype=bool)
        self.missing = []

    # -- filling ----------------------------------------------------------------

    def add_triangles(self, tri):
        """Mark every cell a triangle passes through. `tri` is (n, 3, 3) world."""
        if not len(tri):
            return
        hi = self.lo + np.array(self.shape) * self.cell
        inside = np.all(tri.max(1) >= self.lo, axis=1) & np.all(tri.min(1) <= hi, axis=1)
        tri = tri[inside]
        if not len(tri):
            return
        a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
        edge = np.maximum(np.maximum(np.linalg.norm(b - a, axis=1),
                                     np.linalg.norm(c - a, axis=1)),
                          np.linalg.norm(c - b, axis=1))
        steps = np.clip(np.ceil(edge / (self.cell * 0.5)), 1, 400).astype(np.int64)
        for n in np.unique(steps):
            idx = np.nonzero(steps == n)[0]
            u, v = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing='ij')
            keep = (u + v) <= n
            u = (u[keep] / n)[None, :, None]
            v = (v[keep] / n)[None, :, None]
            per = max(1, POINTS_PER_BATCH // u.shape[1])
            for s in range(0, len(idx), per):
                sel = idx[s:s + per]
                A, B, C = a[sel][:, None, :], b[sel][:, None, :], c[sel][:, None, :]
                self.add_points((A + u * (B - A) + v * (C - A)).reshape(-1, 3))

    def add_points(self, pts):
        idx = np.floor((pts - self.lo) / self.cell).astype(np.int64)
        ok = np.all((idx >= 0) & (idx < np.array(self.shape)), axis=1)
        idx = idx[ok]
        self.solid[idx[:, 0], idx[:, 1], idx[:, 2]] = True

    def add_box(self, lo, hi):
        i0 = np.clip(np.floor((np.array(lo) - self.lo) / self.cell).astype(int), 0,
                     np.array(self.shape) - 1)
        i1 = np.clip(np.floor((np.array(hi) - self.lo) / self.cell).astype(int), 0,
                     np.array(self.shape) - 1)
        self.solid[i0[0]:i1[0] + 1, i0[1]:i1[1] + 1, i0[2]:i1[2] + 1] = True

    def finish(self, clearance=CLEARANCE):
        """Grow the solid cells by `clearance` and build the summed table."""
        r = int(math.ceil(clearance / self.cell))
        grown = self.solid.copy()
        for axis in range(3):
            src = grown.copy()
            for s in range(1, r + 1):
                grown[tuple(slice(s, None) if k == axis else slice(None)
                            for k in range(3))] |= src[tuple(
                                slice(None, -s) if k == axis else slice(None)
                                for k in range(3))]
                grown[tuple(slice(None, -s) if k == axis else slice(None)
                            for k in range(3))] |= src[tuple(
                                slice(s, None) if k == axis else slice(None)
                                for k in range(3))]
        self.grown = grown
        s = grown.astype(np.int32).cumsum(0).cumsum(1).cumsum(2)
        self.sat = np.zeros(tuple(n + 1 for n in self.shape), dtype=np.int32)
        self.sat[1:, 1:, 1:] = s
        s = self.solid.astype(np.int32).cumsum(0).cumsum(1).cumsum(2)
        self.sat_raw = np.zeros_like(self.sat)
        self.sat_raw[1:, 1:, 1:] = s
        self.clear_cells = r
        return self

    # -- queries ----------------------------------------------------------------

    def cells(self, lo, hi):
        """Inclusive cell range covering the world box [lo, hi], clipped."""
        i0 = np.floor((np.array(lo) - self.lo) / self.cell).astype(int)
        i1 = np.floor((np.array(hi) - self.lo) / self.cell).astype(int)
        top = np.array(self.shape) - 1
        return np.clip(i0, 0, top), np.clip(i1, 0, top)

    def count(self, i0, i1, raw=False):
        """Solid cells (grown by the clearance unless `raw`) in the inclusive range."""
        x0, y0, z0 = i0
        x1, y1, z1 = (int(i1[0]) + 1, int(i1[1]) + 1, int(i1[2]) + 1)
        S = self.sat_raw if raw else self.sat
        return int(S[x1, y1, z1] - S[x0, y1, z1] - S[x1, y0, z1] - S[x1, y1, z0]
                   + S[x0, y0, z1] + S[x0, y1, z0] + S[x1, y0, z0] - S[x0, y0, z0])

    def free(self, lo, hi, raw=False):
        i0, i1 = self.cells(lo, hi)
        return self.count(i0, i1, raw) == 0

    def solid_share(self, lo, hi, grown=False):
        """Share of the box's cells that hold geometry (or lie within clearance)."""
        i0, i1 = self.cells(lo, hi)
        src = self.grown if grown else self.solid
        sub = src[i0[0]:i1[0] + 1, i0[1]:i1[1] + 1, i0[2]:i1[2] + 1]
        return float(sub.mean()) if sub.size else 0.0

    def to_world(self, i0, i1):
        return (tuple(float(self.lo[k] + i0[k] * self.cell) for k in range(3)),
                tuple(float(self.lo[k] + (i1[k] + 1) * self.cell) for k in range(3)))

    # -- building ---------------------------------------------------------------

    @classmethod
    def for_pieces(cls, pieces, region, volumes=()):
        lo, hi = region
        g = cls(lo, hi)
        boxlo, boxhi = np.array(lo), np.array(hi)
        for name, M in pieces:
            got = _mesh(name)
            if got is None:
                g.missing.append(name)
                continue
            v, t = got
            w = np.c_[v, np.ones(len(v))] @ M
            w = w[:, :3]
            if np.any(w.max(0) < boxlo) or np.any(w.min(0) > boxhi):
                continue
            g.add_triangles(w[t])
        for vol in volumes:
            g.add_box(vol['worldMin'], vol['worldMax'])
        return g

    @classmethod
    def for_plan(cls, level_name, plan, region):
        pieces, level = final_pieces(level_name, plan)
        vols = [v for v in level.get('volumes') or []
                if v.get('stops') in BLOCKING and v.get('worldMin')]
        return cls.for_pieces(pieces, region, vols).finish()

    @classmethod
    def for_level(cls, level_name, region):
        return cls.for_plan(level_name, {}, region)


def region_of(routes, pad_xy, pad_z, margin=REGION_MARGIN):
    pts = np.array([p for pts, anchors in routes for p in list(pts) + list(anchors)],
                   dtype=np.float64)
    pad = np.array([pad_xy, pad_xy, pad_z]) + margin
    return tuple(pts.min(0) - pad), tuple(pts.max(0) + pad)


class Fitter:
    """Route legs to free boxes on a Grid."""

    def __init__(self, grid, pad_xy, pad_z):
        self.grid = grid
        self.pad = (pad_xy, pad_xy, pad_z)
        self.blocked = []

    def _core(self, held):
        lo = [min(p[k] for p in held) - CORE_HALF for k in range(3)]
        hi = [max(p[k] for p in held) + CORE_HALF for k in range(3)]
        return lo, hi

    def grow(self, lo, hi):
        """The core grown outward, one cell per face per round, while it holds no
        geometry and stays within the padding; then every face that stopped against
        geometry is drawn back by the clearance, never past the core."""
        g = self.grid
        i0, i1 = g.cells(lo, hi)
        i0, i1 = list(i0), list(i1)
        cap = [int(math.ceil(self.pad[k] / g.cell)) for k in range(3)]
        start0, start1 = list(i0), list(i1)
        top = [n - 1 for n in g.shape]
        hit0, hit1 = [False] * 3, [False] * 3
        while True:
            # the face on the box's narrowest axis grows first, so a long box does not
            # stay a thin slab because its long faces met something far along it
            faces = []
            for k in range(3):
                if not hit0[k] and i0[k] > 0 and start0[k] - i0[k] < cap[k]:
                    faces.append((i1[k] - i0[k], k, 0))
                if not hit1[k] and i1[k] < top[k] and i1[k] - start1[k] < cap[k]:
                    faces.append((i1[k] - i0[k], k, 1))
            if not faces:
                break
            _size, k, side = min(faces)
            t0, t1 = list(i0), list(i1)
            if side == 0:
                t0[k] -= 1
                t1[k] = t0[k]
            else:
                t1[k] += 1
                t0[k] = t1[k]
            if g.count(t0, t1, raw=True) == 0:
                if side == 0:
                    i0[k] -= 1
                else:
                    i1[k] += 1
            elif side == 0:
                hit0[k] = True
            else:
                hit1[k] = True
        r = g.clear_cells
        for k in range(3):
            if hit0[k]:
                i0[k] = min(i0[k] + r, start0[k])
            if hit1[k]:
                i1[k] = max(i1[k] - r, start1[k])
        return g.to_world(i0, i1)

    def spans(self, a, b, held=()):
        """[(start, end, held)] pieces of segment a->b whose cores hold no geometry,
        halving as needed. A piece that cannot be freed is kept and recorded in
        `blocked`."""
        lo, hi = self._core([a, b] + list(held))
        free = self.grid.free(lo, hi, raw=True)
        if free or math.dist(a, b) <= MIN_SPAN:
            if not free:
                self.blocked.append((a, b))
            return [(a, b, list(held))]
        m = tuple((a[k] + b[k]) / 2.0 for k in range(3))
        return self.spans(a, m, held) + self.spans(m, b)

    def _free_piece(self, piece):
        a, b, held = piece
        lo, hi = self._core([a, b] + list(held))
        return self.grid.free(lo, hi, raw=True)

    def leg(self, points, anchors=()):
        """(boxes, cores) for one leg, in travel order."""
        boxes, cores, pieces = [], [], []
        pts = [tuple(float(x) for x in p) for p in points]
        # the spawner (the first anchor) is where a flyer appears, so its way to the
        # first point is fitted like any other stretch
        if anchors:
            pts.insert(0, tuple(float(x) for x in anchors[0]))
            anchors = ()
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            n = max(1, int(math.ceil(math.dist(a, b) / MAX_SPAN)))
            cuts = [tuple(a[k] + (b[k] - a[k]) * j / n for k in range(3))
                    for j in range(n + 1)]
            for j, (p, q) in enumerate(zip(cuts, cuts[1:])):
                held = [tuple(float(x) for x in z) for z in anchors] \
                    if i == 0 and j == 0 else []
                pieces += self.spans(p, q, held)
        # consecutive pieces that are both blocked become one
        merged = []
        for a, b, held in pieces:
            if merged and not self._free_piece(merged[-1]) \
                    and not self._free_piece((a, b, held)):
                merged[-1] = (merged[-1][0], b, merged[-1][2] + held)
            else:
                merged.append((a, b, held))
        for a, b, held in merged:
            lo, hi = self._core([a, b] + held)
            if not self.grid.free(lo, hi, raw=True):
                lo, hi = self._core([a, b])
            cores.append((tuple(lo), tuple(hi)))
            if self.grid.free(lo, hi, raw=True):
                boxes.append(self.grow(lo, hi))
            else:
                boxes.append((tuple(lo), tuple(hi)))
        return boxes, cores


#: Shipped-style flight graphs (`airspace`): a few thick boxes tiling the open air around the
#: routes, as every shipped flight graph is built (3-16 volumes, smallest edge 335 uu, most
#: 400+), instead of a corridor of thin boxes along the route, which left gaps and thin
#: spots where flyers turned back (Cellar, Beset, 2026-10-15).
#: Most volumes one graph gets.
AIR_MAX_VOLUMES = 32
#: Longest edge a volume may grow to (uu).
AIR_MAX_EDGE = 6000.0
#: Route cells tried as seeds for each new volume; the largest box wins.
AIR_SEEDS = 40
#: Space kept between an airspace volume and geometry: a Hellbat's capsule radius (34)
#: plus a margin (uu).
AIR_NARROW_CLEARANCE = 40.0
#: A flyer spawner's own volume holds the spawner, the flyer start and the door, and reaches
#: this far past the door towards the next point (uu): shipped Ice_01's first volume holds all
#: three and ends 373 uu past its force field. Flyers split across small volumes at the door
#: turned back right after leaving the force field (Cellar, Beset).
AIR_DOOR_REACH = 300.0
#: Margin around the spawner, start and door in that volume (uu).
AIR_SPAWN_MARGIN = 150.0


def _dilate(solid, cells):
    """`solid` grown by `cells` along each axis (a box neighbourhood)."""
    grown = solid.copy()
    for axis in range(3):
        src = grown.copy()
        for s in range(1, cells + 1):
            grown[tuple(slice(s, None) if k == axis else slice(None) for k in range(3))] |= \
                src[tuple(slice(None, -s) if k == axis else slice(None) for k in range(3))]
            grown[tuple(slice(None, -s) if k == axis else slice(None) for k in range(3))] |= \
                src[tuple(slice(s, None) if k == axis else slice(None) for k in range(3))]
    return grown


def _sat(blocked):
    s = blocked.astype(np.int32).cumsum(0).cumsum(1).cumsum(2)
    out = np.zeros(tuple(n + 1 for n in blocked.shape), dtype=np.int32)
    out[1:, 1:, 1:] = s
    return out


def _sum(S, i0, i1):
    x0, y0, z0 = i0
    x1, y1, z1 = i1[0] + 1, i1[1] + 1, i1[2] + 1
    return int(S[x1, y1, z1] - S[x0, y1, z1] - S[x1, y0, z1] - S[x1, y1, z0]
               + S[x0, y0, z1] + S[x0, y1, z0] + S[x1, y0, z0] - S[x0, y0, z0])


def _route_cells(grid, routes):
    """[[cell index triple, ...] per leg], every route's legs sampled at half a cell,
    spawner and door anchors included as one-cell legs."""
    legs = []
    hi = np.array(grid.shape) - 1
    def cell(p):
        return tuple(int(v) for v in np.clip(np.floor((np.asarray(p) - grid.lo) / grid.cell), 0, hi))
    for pts, anchors in routes:
        for a in anchors:
            legs.append([cell(a)])
        for a, b in zip(pts, pts[1:]):
            a, b = np.asarray(a, float), np.asarray(b, float)
            n = max(1, int(np.ceil(np.linalg.norm(b - a) / (grid.cell / 2))))
            seq = []
            for t in np.linspace(0, 1, n + 1):
                c = cell(a + t * (b - a))
                #: face-connected: a diagonal step becomes one axis step at a time, so a
                #: step between two volumes always crosses a face they could share
                while seq and seq[-1] != c:
                    last = list(seq[-1])
                    k = max(range(3), key=lambda k: abs(c[k] - last[k]))
                    last[k] += 1 if c[k] > last[k] else -1
                    seq.append(tuple(last))
                if not seq:
                    seq.append(c)
            legs.append(seq)
    return legs


def airspace(grid, routes):
    """(boxes, adjacency, blocked, gaps): volumes tiling the free air the routes pass.

    Each new volume is the largest box grown (one cell per face per round, while it stays
    free of geometry within CLEARANCE and of earlier volumes) from AIR_SEEDS route cells
    no volume covers yet, until every route cell is covered or AIR_MAX_VOLUMES is
    reached. Free means outside AIR_NARROW_CLEARANCE of geometry.
    `blocked` lists route cells no volume may hold; `gaps` lists route steps between two
    volumes that share no face (a flyer cannot plan across them)."""
    legs = _route_cells(grid, routes)
    #: grown against a flyer-sized clearance everywhere: shipped volumes come within
    #: CLEARANCE of geometry freely (Ice_01's volume 3: 43% of its cells), and holding them
    #: to CLEARANCE chopped the air into 50 uu slivers
    narrow = _dilate(grid.solid, int(math.ceil(AIR_NARROW_CLEARANCE / grid.cell)))
    allowed = ~narrow
    route = {c for leg in legs for c in leg}
    blocked_cells = [c for c in route if not allowed[c]]
    covered = np.zeros(grid.shape, dtype=bool)
    owner = {}
    boxes_i = []
    limit = int(AIR_MAX_EDGE / grid.cell)
    shape = np.array(grid.shape)
    #: spawn volumes first, geometry or not (they take in the door frame, as shipped's does)
    for pts, anchors in routes:
        if len(anchors) < 2 or len(pts) < 2:
            continue
        door = np.asarray(anchors[1], float)
        out = door[:2] - np.asarray(anchors[0], float)[:2]
        #: the first route point beyond the door, seen from the spawner
        nxt = next((np.asarray(p, float) for p in pts
                    if np.dot(np.asarray(p, float)[:2] - door[:2], out) > 0), None)
        held = [np.asarray(anchors[0], float), np.asarray(pts[0], float), door]
        if nxt is not None:
            d = nxt - door
            held.append(door + d / max(np.linalg.norm(d), 1e-6) * AIR_DOOR_REACH)
        lo_w = np.min(held, axis=0) - AIR_SPAWN_MARGIN
        hi_w = np.max(held, axis=0) + AIR_SPAWN_MARGIN
        i0, i1 = grid.cells(tuple(lo_w), tuple(hi_w))
        i0, i1 = [int(v) for v in i0], [int(v) for v in i1]
        if covered[i0[0]:i1[0] + 1, i0[1]:i1[1] + 1, i0[2]:i1[2] + 1].any():
            continue
        covered[i0[0]:i1[0] + 1, i0[1]:i1[1] + 1, i0[2]:i1[2] + 1] = True
        n = len(boxes_i)
        boxes_i.append((i0, i1))
        for c in route:
            if all(i0[k] <= c[k] <= i1[k] for k in range(3)):
                owner[c] = n
    todo = [c for c in sorted(route) if allowed[c] and c not in owner]
    while todo and len(boxes_i) < AIR_MAX_VOLUMES:
        S = _sat(~allowed | covered)
        step = max(1, len(todo) // AIR_SEEDS)
        best = None
        for seed in todo[::step]:
            lo, hi = list(seed), list(seed)
            grow = [True] * 6
            while any(grow):
                for f in range(6):
                    if not grow[f]:
                        continue
                    k, up = f // 2, f % 2
                    nlo, nhi = lo[:], hi[:]
                    if up:
                        nhi[k] += 1
                    else:
                        nlo[k] -= 1
                    if nlo[k] < 0 or nhi[k] >= shape[k] or nhi[k] - nlo[k] + 1 > limit:
                        grow[f] = False
                        continue
                    slab_lo, slab_hi = nlo[:], nhi[:]
                    if up:
                        slab_lo[k] = nhi[k]
                    else:
                        slab_hi[k] = nlo[k]
                    if _sum(S, slab_lo, slab_hi):
                        grow[f] = False
                    else:
                        lo, hi = nlo, nhi
            size = (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)
            if best is None or size > best[0]:
                best = (size, lo, hi)
        _size, lo, hi = best
        covered[lo[0]:hi[0] + 1, lo[1]:hi[1] + 1, lo[2]:hi[2] + 1] = True
        n = len(boxes_i)
        boxes_i.append((lo, hi))
        for c in todo:
            if all(lo[k] <= c[k] <= hi[k] for k in range(3)):
                owner[c] = n
        todo = [c for c in todo if c not in owner]
    #: neighbours whose union is itself a box become one volume (fewer slivers)
    merged = True
    while merged:
        merged = False
        for a in range(len(boxes_i)):
            for b in range(a + 1, len(boxes_i)):
                (alo, ahi), (blo, bhi) = boxes_i[a], boxes_i[b]
                for k in range(3):
                    o = [j for j in range(3) if j != k]
                    if all(alo[j] == blo[j] and ahi[j] == bhi[j] for j in o) and \
                            (ahi[k] + 1 == blo[k] or bhi[k] + 1 == alo[k]):
                        lo = [min(alo[j], blo[j]) for j in range(3)]
                        hi = [max(ahi[j], bhi[j]) for j in range(3)]
                        if hi[k] - lo[k] + 1 <= limit:
                            boxes_i[a] = (lo, hi)
                            del boxes_i[b]
                            owner = {c: (a if i == b else (i - 1 if i > b else i))
                                     for c, i in owner.items()}
                            merged = True
                            break
                if merged:
                    break
            if merged:
                break
    boxes = [grid.to_world(lo, hi) for lo, hi in boxes_i]
    from riftlord.build.navigation import navvolgen
    adjacency = []
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            if navvolgen.shared_face(boxes[a], boxes[b], tolerance=1.0) is not None:
                adjacency.append((a, b))
    joined = {(a, b) for a, b in adjacency}
    gaps = []
    for leg in legs:
        ids = [owner.get(c) for c in leg]
        for c0, c1, i0, i1 in zip(leg, leg[1:], ids, ids[1:]):
            if i0 is None or i1 is None or i0 == i1:
                continue
            if (min(i0, i1), max(i0, i1)) not in joined:
                gaps.append((grid.to_world(c0, c0)[0], grid.to_world(c1, c1)[0]))
    uncovered = [c for c in route if allowed[c] and c not in owner]
    blocked = [(grid.to_world(c, c)[0],) * 2 for c in blocked_cells + uncovered]
    return boxes, adjacency, blocked, gaps


def check_clear(grid, boxes, cores=None):
    """[(volume, share of its cells holding geometry)] for volumes that hold any."""
    out = []
    for i, (lo, hi) in enumerate(boxes):
        s = grid.solid_share(lo, hi)
        if s > 0:
            out.append((i, round(s, 4)))
    return out


def _main():
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--record')
    ap.add_argument('--shipped')
    a = ap.parse_args()
    from riftlord.build.navigation import navvolgen
    if a.record:
        rec = json.load(open(a.record))
        plan = rec['plan']
        from riftlord.mapeditor import planactors
        routes = planactors.flyer_routes(plan, planactors.level_actors(plan['level']))
        buf, info = navvolgen.for_routes(routes, clear=(plan['level'], plan))
        print({k: v for k, v in info.items() if k != 'checks'})
    if a.shipped:
        import tempfile
        from riftlord.build.navigation import navpatch
        from riftlord.build.navigation import navvolinject
        d = tempfile.mkdtemp()
        f = navpatch.fetch_map(navpatch.map_path(a.shipped), d)
        buf = navvolinject.volumetric_of(open(f['.umap'], 'rb').read(),
                                         open(f['.uexp'], 'rb').read())
        boxes, _adj = navvolgen.boxes_of(buf)
        lo = tuple(min(b[0][k] for b in boxes) - REGION_MARGIN for k in range(3))
        hi = tuple(max(b[1][k] for b in boxes) + REGION_MARGIN for k in range(3))
        g = Grid.for_level(a.shipped, (lo, hi))
        for i, (blo, bhi) in enumerate(boxes):
            print('volume %d  geometry %.3f  within clearance %.3f'
                  % (i, g.solid_share(blo, bhi), g.solid_share(blo, bhi, grown=True)))


if __name__ == '__main__':
    _main()
