"""Terrain tiles on the shipped Floor_4x4 footprint, cut from a painted ground mesh.

Every number below is measured from the shipped kit (riftlord/data/omd3-meshes.json,
kit-geometry.json, the decoded Stairs_4x_4_SB) or from the Fab grid analysis it must pass:

* Footprint: Floor_4x4 is X 0..1200, Y 0..1200, its top at Z 0 and its slab down to Z -80;
  its trap grid is 4 x 4 cells of 300 uu over exactly that square. A tile has the same
  footprint, pivot and slab, so it snaps on the kit lattice by its own 4 x 4 grid.
* Flat: the top is Z 0 on every edge. Inside, the ground's relief is carved downward only, at
  most RELIEF_DEPTH, below the Fab analysis's one-level tolerance (analyse.LEVEL_TOL 15 uu), so
  the suggested grid is one 4 x 4 at Z 0.
* Ramp: Z rises RISE over the 1200 uu depth toward -Y (as Stairs_4x_4_SB climbs), the rise of
  the shipped kit ramps and stairs (RampMesh_SB_MT_4x2 rises 300 over 600, Stairs_4x_4_SB 600
  over 1200: 1 in 2, 26.6 deg). Its ends are rounded (ramp_profile, RAMP_FILLET), level with the
  floor at the foot and the top, so the middle is a little steeper (29.3 deg; walkable limits:
  analyse.SLOPE_FLOOR_MAX_DEG 45, recastgen 60).
* No stepped tiles (user, 2026-10-28: terrain climbs by ramps). The shipped ramps
  (RampMesh_*) all keep the 1 in 2 slope, so no other ramp shape is cut.
* Edges: every tile meets the lattice at its edges (Z 0, or the ramp's line), and its paint
  goes to the art set's border paint within PAINT_EDGE_BAND of an edge, so any two tiles of a
  library meet without a seam. UV0 is planar with a whole number of texture repeats per tile.
"""

import math

import numpy as np

#: Floor_4x4's footprint edge (uu), trap cell (uu) and slab thickness (uu).
TILE = 1200.0
CELL = 300.0
SLAB = 80.0
#: Ramp rise over the tile's depth (uu).
RISE = 600.0
#: Ramp ends rounded over this depth each (uu), level with the floor at both. Measured
#: (terrain-kit/scripts/s27, s28, 2026-10-28): up to 175 the Fab analysis still offers one 4 x 4
#: slope grid, but the top strays below that grid's plane by 12.1 uu at 130, 14.9 at 140 and
#: 17.2 at 150 (analyse.LEVEL_TOL 15); 130 keeps a margin. Steepest slope 29.3 deg.
RAMP_FILLET = 130.0
#: How far a ramp's top may stray from its slope grid's plane (uu): analyse.LEVEL_TOL.
RAMP_GRID_TOL = 15.0
#: A ramp's top is built in rows this far apart along its climb (uu).
RAMP_ROW = 25.0
#: Height samples per tile edge (75 uu apart).
SAMPLES = 16
#: Deepest relief carved into a flat or ramp top (uu).
RELIEF_DEPTH = 12.0
#: fitted_tile makes a tile's relief shallower in steps of this (uu) until its grid fits.
RELIEF_STEP = 3.0
#: Relief and paint fade to the edge over these distances (uu).
RELIEF_EDGE_BAND = 150.0
PAINT_EDGE_BAND = 150.0
#: Window origins on the source ground step this far (uu).
WINDOW_STRIDE = 300.0
#: A source window is flat ground when its fitted plane is at most this steep (deg).
FLAT_SOURCE_SLOPE_DEG = 15.0
#: Tiles per art set.
FLAT_TILES = 6
RAMP_TILES = 2
#: Texture scale is kept within this factor of the source's when choosing whole repeats.
UV_SCALE_RANGE = 2.0
#: A layer counts as repeating a whole number of times per tile within this share of a repeat.
UV_WHOLE_TOL = 0.02


def step():
    return TILE / SAMPLES


# -- the source raster --------------------------------------------------------------------------

def raster(P, T, C):
    """{origin (x, y), z (ny, nx), rgba (ny, nx, 4)} of a ground mesh's top surface sampled every
    step() uu; uncovered samples are -inf. Only up-facing triangles cover; the highest wins."""
    s = step()
    lo = np.floor(P[:, :2].min(0) / s) * s
    hi = np.ceil(P[:, :2].max(0) / s) * s
    nx, ny = (np.round((hi - lo) / s).astype(int) + 1)
    Z = np.full((ny, nx), -np.inf)
    RGBA = np.zeros((ny, nx, 4))
    C = np.asarray(C, np.float64)
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    nz = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    for k in range(len(T)):
        if abs(nz[k]) < 1e-6:
            continue
        tri = P[T[k]]
        n3 = np.cross(tri[2] - tri[0], tri[1] - tri[0])
        if abs(n3[2]) < 1e-9 or abs(n3[2]) / np.linalg.norm(n3) < 0.05:
            continue
        x0, y0 = np.floor((tri[:, :2].min(0) - lo) / s).astype(int)
        x1, y1 = np.ceil((tri[:, :2].max(0) - lo) / s).astype(int)
        xs = np.arange(max(x0, 0), min(x1, nx - 1) + 1)
        ys = np.arange(max(y0, 0), min(y1, ny - 1) + 1)
        if not len(xs) or not len(ys):
            continue
        gx, gy = np.meshgrid(lo[0] + xs * s, lo[1] + ys * s)
        d = nz[k]
        w1 = ((gx - tri[0, 0]) * (tri[2, 1] - tri[0, 1]) - (gy - tri[0, 1]) * (tri[2, 0] - tri[0, 0])) / d
        w2 = ((tri[1, 0] - tri[0, 0]) * (gy - tri[0, 1]) - (tri[1, 1] - tri[0, 1]) * (gx - tri[0, 0])) / d
        w0 = 1.0 - w1 - w2
        eps = -1e-6
        inside = (w0 >= eps) & (w1 >= eps) & (w2 >= eps)
        if not inside.any():
            continue
        z = w0 * tri[0, 2] + w1 * tri[1, 2] + w2 * tri[2, 2]
        sub = Z[np.ix_(ys, xs)]
        take = inside & (z > sub)
        if not take.any():
            continue
        col = (w0[..., None] * C[T[k, 0]] + w1[..., None] * C[T[k, 1]] + w2[..., None] * C[T[k, 2]])
        jj, ii = np.nonzero(take)
        Z[ys[jj], xs[ii]] = z[jj, ii]
        RGBA[ys[jj], xs[ii]] = col[jj, ii]
    return {'origin': lo, 'z': Z, 'rgba': RGBA}


def windows(r):
    """[{i, j, plane (a, b, c), slope, rms}] of every tile-sized window wholly on the ground,
    origins every WINDOW_STRIDE uu; (i, j) is the window's first sample (column, row)."""
    n = SAMPLES + 1
    stride = int(round(WINDOW_STRIDE / step()))
    cov = np.isfinite(r['z']).astype(np.int64)
    S = np.pad(cov, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    ny, nx = cov.shape
    xs = np.arange(n) * step()
    gx, gy = np.meshgrid(xs, xs)
    A = np.c_[gx.ravel(), gy.ravel(), np.ones(n * n)]
    pinv = np.linalg.pinv(A)
    out = []
    for j in range(0, ny - n + 1, stride):
        for i in range(0, nx - n + 1, stride):
            if S[j + n, i + n] - S[j, i + n] - S[j + n, i] + S[j, i] != n * n:
                continue
            z = r['z'][j:j + n, i:i + n].ravel()
            p = pinv @ z
            res = z - A @ p
            out.append({'i': i, 'j': j, 'plane': p.tolist(),
                        'slope': math.degrees(math.atan(math.hypot(p[0], p[1]))),
                        'rms': float(np.sqrt(np.mean(res ** 2)))})
    return out


def window_paint(r, w):
    n = SAMPLES + 1
    return r['rgba'][w['j']:w['j'] + n, w['i']:w['i'] + n].reshape(-1, 4)


def choose(cands, count, feature, first):
    """Up to `count` windows: `first` (an index into cands), then each the one farthest in
    `feature` space from those chosen, never overlapping one."""
    if not cands:
        return []
    F = np.array([feature(w) for w in cands])
    chosen = [first]
    reach = SAMPLES

    def clear(k):
        return all(abs(cands[k]['i'] - cands[c]['i']) >= reach or
                   abs(cands[k]['j'] - cands[c]['j']) >= reach for c in chosen)
    while len(chosen) < count:
        d = np.min(np.linalg.norm(F[:, None, :] - F[chosen][None, :, :], axis=2), axis=1)
        order = np.argsort(-d)
        nxt = next((int(k) for k in order if d[k] > 0 and clear(int(k))), None)
        if nxt is None:
            break
        chosen.append(nxt)
    return [cands[k] for k in chosen]


# -- texture repeats ---------------------------------------------------------------------------

def uv_span(source_span, tile_sizes):
    """(UV0 units per tile, worst seam share) with every layer (UV x TileSize) repeating a whole
    number of times per tile, nearest the source's `source_span`, within UV_SCALE_RANGE of it;
    when no span does that for every layer, one that does for the first (the base layer)."""
    best = None
    for ts in sorted(set(round(t, 6) for t in tile_sizes)):
        for n in range(1, 64):
            span = n / ts
            if not source_span / UV_SCALE_RANGE <= span <= source_span * UV_SCALE_RANGE:
                continue
            seam = max(abs(span * t - round(span * t)) for t in tile_sizes)
            first = abs(span * tile_sizes[0] - round(span * tile_sizes[0]))
            key = (seam > UV_WHOLE_TOL, first > UV_WHOLE_TOL, abs(math.log(span / source_span)))
            if best is None or key < best[0]:
                best = (key, span, seam)
    if best is None:
        return source_span, max(abs(source_span * t - round(source_span * t)) for t in tile_sizes)
    return best[1], best[2]


# -- tile meshes -------------------------------------------------------------------------------

def smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def edge_distance(x, y):
    return np.minimum(np.minimum(x, TILE - x), np.minimum(y, TILE - y))


def relief(z, plane, depth=None):
    """Carved relief (<= 0) of a window's heights `z` (n, n): the residual from its plane, its
    highest point at 0, at most RELIEF_DEPTH deep, faded to 0 at the edges."""
    n = SAMPLES + 1
    xs = np.arange(n) * step()
    gx, gy = np.meshgrid(xs, xs)
    res = z - (plane[0] * gx + plane[1] * gy + plane[2])
    res = res - res.max()
    most = RELIEF_DEPTH if depth is None else depth
    deep = -res.min()
    if deep > most:
        res *= most / deep if deep > 0 else 0.0
    return res * smoothstep(edge_distance(gx, gy) / RELIEF_EDGE_BAND)


def edge_paint(rgba, border, x, y):
    """Paint lerped to `border` within PAINT_EDGE_BAND of an edge."""
    t = smoothstep(edge_distance(x, y) / PAINT_EDGE_BAND)[:, None]
    return border + (rgba - border) * t


class Mesh:
    """Vertices and outward-facing triangles in the writer's winding."""

    def __init__(self):
        self.P, self.N, self.UV, self.UV1, self.C, self.T = [], [], [], [], [], []

    def add(self, p, n, uv, uv1, c):
        self.P.append(p); self.N.append(n); self.UV.append(uv); self.UV1.append(uv1); self.C.append(c)
        return len(self.P) - 1

    def tri(self, a, b, c, outward):
        """Triangle a b c turned so its front ((p2-p0) x (p1-p0)) faces `outward`."""
        p0, p1, p2 = (np.asarray(self.P[k]) for k in (a, b, c))
        if np.dot(np.cross(p2 - p0, p1 - p0), outward) < 0:
            b, c = c, b
        self.T.append((a, b, c))

    def quad(self, a, b, c, d, outward):
        """Quad a b c d (in order round its edge)."""
        self.tri(a, b, c, outward)
        self.tri(a, c, d, outward)

    def desc(self):
        P = np.array(self.P, np.float64)
        T = np.array(self.T, np.int64)
        N = np.array(self.N, np.float64)
        return {'positions': P.astype(np.float32), 'normals': N.astype(np.float32),
                'tangents': None, 'signs': None,
                'uvs': [np.array(self.UV, np.float32), np.array(self.UV1, np.float32)],
                'colors': (np.clip(np.rint(np.array(self.C)), 0, 255).astype(np.uint8)
                           if self.C and self.C[0] is not None else None),
                'triangles': T, 'materials': np.zeros(len(T), np.int64)}


def ramp_profile(y, fillet=None):
    """Height of a ramp's top at depth y (0..TILE, climbing +Y): 0 at the foot and RISE at the top,
    level at both (slope 0). The slope grows linearly to the middle's m = RISE / (TILE - fillet)
    over `fillet` uu at each end (constant curvature), and holds m between."""
    L = RAMP_FILLET if fillet is None else fillet
    y = np.clip(np.asarray(y, np.float64), 0.0, TILE)
    if L <= 0:
        return RISE * y / TILE
    m = RISE / (TILE - L)
    return np.where(y < L, m * y * y / (2 * L),
                    np.where(y > TILE - L, RISE - m * (TILE - y) ** 2 / (2 * L), m * (y - L / 2)))


def ramp_steepest_deg(fillet=None):
    """The ramp's steepest slope (its middle), degrees."""
    L = RAMP_FILLET if fillet is None else fillet
    return math.degrees(math.atan(RISE / (TILE - max(L, 0.0))))


def _resample_rows(a, ys):
    """Rows of a (SAMPLES + 1, ...) grid sampled every step() along Y, linearly resampled at ys."""
    src = np.arange(SAMPLES + 1) * step()
    k = np.clip(np.searchsorted(src, ys, side='right') - 1, 0, SAMPLES - 1)
    t = ((ys - src[k]) / step()).reshape((-1,) + (1,) * (a.ndim - 1))
    return a[k] * (1 - t) + a[k + 1] * t


def _grid_normals(Z, xs, ys):
    """Per-sample normals of a height grid Z[j, i] (rows ys along +Y, columns xs along +X)."""
    gy, gx = np.gradient(Z, ys, xs)
    n = np.dstack([-gx, -gy, np.ones_like(Z)])
    return n / np.linalg.norm(n, axis=2, keepdims=True)


def _skirts(m, xs, ys, Ztop, paint, uv_per_uu):
    """Vertical walls from every top edge down to -SLAB."""
    nx, ny = len(xs), len(ys)
    sides = [([(i, 0) for i in range(nx)], (0, -1, 0)), ([(i, ny - 1) for i in range(nx)], (0, 1, 0)),
             ([(0, j) for j in range(ny)], (-1, 0, 0)), ([(nx - 1, j) for j in range(ny)], (1, 0, 0))]
    for run, out in sides:
        along = 0 if out[1] else 1
        ups, downs = [], []
        for i, j in run:
            x, y, z = xs[i], ys[j], Ztop[j, i]
            t = (x, y)[along]
            ups.append(m.add((x, y, z), out, (t * uv_per_uu, -z * uv_per_uu),
                             (t / TILE, 0.5 + z / (4 * TILE)), paint[j, i]))
            downs.append(m.add((x, y, -SLAB), out, (t * uv_per_uu, SLAB * uv_per_uu),
                               (t / TILE, 0.5 - SLAB / (4 * TILE)), paint[j, i]))
        for k in range(len(run) - 1):
            m.quad(ups[k], ups[k + 1], downs[k + 1], downs[k], np.array(out, np.float64))


def surface_tile(kind, z, rgba, plane, border, uv_per_uu, relief_depth=None, fillet=None):
    """A flat (kind 'flat') or ramp ('ramp') tile from a source window's heights z (n, n) and
    paint rgba (n*n, 4); rgba None: no vertex colours (a material that reads none). A ramp's top
    follows ramp_profile in rows RAMP_ROW apart."""
    n = SAMPLES + 1
    xs = np.arange(n) * step()
    rel = relief(z, plane, relief_depth)
    if kind == 'ramp':
        ys = np.linspace(0.0, TILE, int(round(TILE / RAMP_ROW)) + 1)
        rel = _resample_rows(rel, ys)
    else:
        ys = xs
    gx, gy = np.meshgrid(xs, ys)
    base = ramp_profile(gy, fillet) if kind == 'ramp' else np.zeros_like(gx)
    Z = base + rel
    if rgba is None:
        paint = np.full(gx.shape, None, object)
    else:
        grid = np.asarray(rgba, np.float64).reshape(n, n, 4)
        if kind == 'ramp':
            grid = _resample_rows(grid, ys)
        paint = edge_paint(grid.reshape(-1, 4), border, gx.ravel(), gy.ravel()).reshape(gx.shape + (4,))
    m = Mesh()
    N = _grid_normals(Z, xs, ys)
    ids = np.zeros(gx.shape, np.int64)
    for j in range(len(ys)):
        for i in range(n):
            ids[j, i] = m.add((xs[i], ys[j], Z[j, i]), N[j, i], (xs[i] * uv_per_uu, ys[j] * uv_per_uu),
                              (xs[i] / TILE, ys[j] / TILE), paint[j, i])
    up = np.array([0.0, 0.0, 1.0])
    for j in range(len(ys) - 1):
        for i in range(n - 1):
            m.quad(ids[j, i], ids[j, i + 1], ids[j + 1, i + 1], ids[j + 1, i], up)
    _skirts(m, xs, ys, Z, paint, uv_per_uu)
    return climb_minus_y(m.desc()) if kind == 'ramp' else m.desc()


def one_full_grid(desc, kind):
    """True when the Fab grid analysis offers exactly one 4 x 4 grid on the tile's top (a floor
    grid on a flat tile, a slope grid on a ramp)."""
    from riftlord.fab import analyse as AN
    coll = {'boxes': [], 'convex': [], 'trace': 'complex_as_simple', 'double_sided': False,
            'collides': True}
    want = 'slope_floor' if kind == 'ramp' else 'floor'
    grids = AN.analyse({'positions': desc['positions'], 'triangles': desc['triangles']}, 'floor',
                       coll, 1.0)['grids']
    tops = [g for g in grids if g['kind'] in ('floor', 'slope_floor')]
    return len(tops) == 1 and tops[0]['kind'] == want and list(tops[0]['cells']) == [4.0, 4.0]


def slope_grid_gap(desc):
    """(cells, uu) of a ramp tile: its largest offered slope grid's cells and the farthest any top
    vertex inside that grid sits from the grid's plane."""
    from riftlord.fab import analyse as AN
    coll = {'boxes': [], 'convex': [], 'trace': 'complex_as_simple', 'double_sided': False,
            'collides': True}
    grids = [g for g in AN.analyse({'positions': desc['positions'], 'triangles': desc['triangles']}, 'floor',
                                   coll, 1.0)['grids'] if g['kind'] == 'slope_floor']
    if not grids:
        return None, None
    g = max(grids, key=lambda g: g['cells'][0] * g['cells'][1])
    nrm, u, v = (np.array(g['axes'][k], np.float64) for k in ('x', 'y', 'z'))
    P = np.asarray(desc['positions'], np.float64)
    N = np.asarray(desc['normals'], np.float64)
    P = P[N[:, 2] > 0.5] - np.array(g['center'])
    inside = (np.abs(P @ u) <= g['extent'][1] + 1e-3) & (np.abs(P @ v) <= g['extent'][2] + 1e-3)
    return list(g['cells']), float(np.abs(P[inside] @ nrm).max())


def fitted_tile(kind, z, rgba, plane, border, uv_per_uu):
    """(desc, relief depth kept) of a tile whose top still takes its full 4 x 4 grid: the relief
    is made shallower, step by RELIEF_STEP down to none, until the grid is offered and (on a ramp)
    the top lies within RAMP_GRID_TOL of the grid's plane."""
    depth = RELIEF_DEPTH
    while True:
        desc = surface_tile(kind, z, rgba, plane, border, uv_per_uu, relief_depth=depth)
        ok = one_full_grid(desc, kind)
        if ok and kind == 'ramp':
            _cells, gap = slope_grid_gap(desc)
            ok = gap <= RAMP_GRID_TOL
        if ok or depth <= 0:
            return desc, depth
        depth = max(0.0, depth - RELIEF_STEP)


def climb_minus_y(desc):
    """A tile built climbing +Y turned to climb -Y within the same footprint (Y -> TILE - Y)."""
    out = dict(desc)
    P = np.array(desc['positions'], np.float64)
    P[:, 1] = TILE - P[:, 1]
    N = np.array(desc['normals'], np.float64)
    N[:, 1] = -N[:, 1]
    out['positions'] = P.astype(np.float32)
    out['normals'] = N.astype(np.float32)
    out['triangles'] = np.asarray(desc['triangles'])[:, [0, 2, 1]]
    out['uvs'] = [np.array(u) for u in desc['uvs']]
    out['uvs'][0][:, 1] = -out['uvs'][0][:, 1]
    return out
