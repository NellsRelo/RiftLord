"""The Regenerate minimap painter's fast paths draw what the plain ones drew:

1. minimapredraw._spread_height and _spread (front-only steps) and _reach (runs reached
   round by round) against the plain whole-picture versions, on random pictures with
   edges, windows, infinities and labels;
2. minimapsolid.samples (off-square triangles skipped, triangles grouped by one sort,
   lattice one axis at a time, stable-sort dedupe) against the plain sampler, on made-up
   meshes standing inside, across and outside the square;
3. minimapsolid.Grid from sorted samples, against np.unique's counts;
4. the solid-grid cache: a read refreshes a grid's time, the least recently used go first
   past GRID_BYTES, and a failed write leaves no part file;
5. whole paintings, as the build paints them (minimapredraw.prepare's painters,
   scratch_minimap for a scratch level), against the pictures the plain painter drew
   (minimap_painter/*.png) from the plans in minimap_painter/plans.json: North Wings
   (Regenerate), Proving Grounds (scratch), Cellar's two floors (Regenerate, Cut at
   Doors). Needs the game's paks; MINIMAP_SOLID_CACHE moves the solid grids' cache.

    python3 tests/offline/test_minimappaint.py [--quick]   # --quick: 1-4 only
"""
import collections
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import numpy as np                                        # noqa: E402

from riftlord.build.minimap import minimapredraw as R     # noqa: E402
from riftlord.build.minimap import minimapsolid as S      # noqa: E402

HERE = os.path.join(ROOT, 'tests', 'offline', 'minimap_painter')
checks = []


def check(name, ok, detail=''):
    checks.append(bool(ok))
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % detail) if detail else ''))


def same_bits(a, b):
    a, b = np.ascontiguousarray(a), np.ascontiguousarray(b)
    return a.dtype == b.dtype and a.shape == b.shape and np.array_equal(
        a.view(np.uint8), b.view(np.uint8))


# -- the plain versions, as the painter had them before the fast paths ---------------

def plain_spread_height(H, floor, steps):
    out = H.copy()
    for _ in range(int(steps)):
        need = floor & np.isnan(out)
        if not need.any():
            break
        filled = np.nan_to_num(out, nan=-1e30)
        for dy, dx in R._NEIGHBOURS:
            src = R._shifted(filled, dy, dx)
            take = need & (src > -1e29)
            out[take] = src[take]
            need &= ~take
    return out


def plain_spread(lab, steps, within=None):
    out = lab.copy()
    for _ in range(int(steps)):
        empty = out == 0
        if within is not None:
            empty &= within
        if not empty.any():
            break
        for dy, dx in R._NEIGHBOURS:
            src = R._shifted(out, dy, dx)
            take = empty & (src > 0)
            out[take] = src[take]
            empty &= ~take
    return out


def plain_reach(mask, seeds):
    def run_ids(m):
        flat = m.ravel()
        start = flat.copy()
        start[1:] &= ~flat[:-1]
        start[::m.shape[1]] = flat[::m.shape[1]]
        return np.where(flat, np.cumsum(start), 0).reshape(m.shape)
    rows, cols = run_ids(mask), run_ids(mask.T)
    got = seeds & mask
    while True:
        before = int(got.sum())
        hit = np.unique(rows[got])
        got = np.isin(rows, hit[hit > 0])
        hit = np.unique(cols[got.T])
        got = np.isin(cols, hit[hit > 0]).T
        if int(got.sum()) == before:
            return got


def plain_samples(pieces, project, size):
    o = np.array(project((0.0, 0.0)), dtype=np.float64) * size
    ex = (np.array(project((1.0, 0.0)), dtype=np.float64) * size) - o
    ey = (np.array(project((0.0, 1.0)), dtype=np.float64) * size) - o
    uu = 1.0 / float(np.hypot(ex[0], ex[1]))
    step = uu / S.SAMPLES_PER_PIXEL
    by_mesh = collections.defaultdict(list)
    for mesh, matrix in pieces:
        if matrix is not None:
            by_mesh[mesh].append(matrix)
    out = []

    def keep(pts):
        u = o[0] + pts[:, 0] * ex[0] + pts[:, 1] * ey[0]
        v = o[1] + pts[:, 0] * ex[1] + pts[:, 1] * ey[1]
        ok = (u >= 0) & (u < size) & (v >= 0) & (v < size)
        if ok.any():
            pix = v[ok].astype(np.int64) * size + u[ok].astype(np.int64)
            zq = np.round(pts[ok, 2] / S.Z_STEP).astype(np.int64)
            out.append(np.unique(pix * S.Z_KEY + (zq + S.Z_KEY // 2)))
    for mesh, mats in by_mesh.items():
        got = S._mesh(mesh)
        if got is None:
            continue
        verts, tris = got
        la, lb, lc = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
        cent = (la + lb + lc) / 3.0
        local_edge = np.maximum(np.maximum(np.linalg.norm(lb - la, axis=1),
                                           np.linalg.norm(lc - lb, axis=1)),
                                np.linalg.norm(la - lc, axis=1))
        base = np.concatenate([verts, cent])
        M = np.asarray(mats, dtype=np.float64).reshape(-1, 4, 4)
        per = max(1, 4000000 // max(1, len(base)))
        for p0 in range(0, len(M), per):
            Mp = M[p0:p0 + per]
            keep((np.matmul(base[None, :, :], Mp[:, :3, :3])
                  + Mp[:, None, 3, :3]).reshape(-1, 3))
        e1, e2 = lb - la, lc - la
        if not (local_edge > 0).any():
            continue
        Rm, T = M[:, :3, :3], M[:, 3, :3]
        w1 = np.matmul(e1[None, :, :], Rm)
        w2 = np.matmul(e2[None, :, :], Rm)
        ka = np.ceil(np.maximum(np.hypot(w1[..., 0], w1[..., 1]) / step,
                                np.abs(w1[..., 2]) / S.Z_SAMPLE)).astype(np.int64)
        kb = np.ceil(np.maximum(np.hypot(w2[..., 0], w2[..., 1]) / step,
                                np.abs(w2[..., 2]) / S.Z_SAMPLE)).astype(np.int64)
        need = (ka > 1) | (kb > 1)
        if not need.any():
            continue
        pi, ti = np.nonzero(need)
        kA_all = np.clip(ka[pi, ti], 1, S.MAX_STEPS)
        kB_all = np.clip(kb[pi, ti], 1, S.MAX_STEPS)
        A = np.matmul(la[ti][:, None, :], Rm[pi])[:, 0, :] + T[pi]
        W1, W2 = w1[pi, ti], w2[pi, ti]
        pairs = kA_all * (S.MAX_STEPS + 1) + kB_all
        for pk in np.unique(pairs):
            sel = np.flatnonzero(pairs == pk)
            kA, kB = int(pk // (S.MAX_STEPS + 1)), int(pk % (S.MAX_STEPS + 1))
            i, j = np.meshgrid(np.arange(kA + 1), np.arange(kB + 1), indexing='ij')
            fa, fb = (i / float(kA)).ravel(), (j / float(kB)).ravel()
            m = fa + fb <= 1.0 + 1e-9
            fa, fb = fa[m], fb[m]
            chunk = max(1, 3000000 // len(fa))
            for s0 in range(0, len(sel), chunk):
                q = sel[s0:s0 + chunk]
                keep((A[q, None, :] + W1[q, None, :] * fa[None, :, None]
                      + W2[q, None, :] * fb[None, :, None]).reshape(-1, 3))
    if not out:
        return np.zeros(0, np.int64), np.zeros(0, np.float32)
    k = np.unique(np.concatenate(out))
    return k // S.Z_KEY, ((k % S.Z_KEY) - S.Z_KEY // 2).astype(np.float32) * S.Z_STEP


# -- 1. spreading ---------------------------------------------------------------------

def spreading():
    rng = np.random.default_rng(5)
    bad_h = bad_l = bad_r = 0
    n = 1500
    for trial in range(n):
        h, w = (int(x) for x in rng.integers(1, 30, 2))
        dt = (np.float32, np.float64)[trial % 2]
        H = np.full((h, w), np.nan, dt)
        m = rng.random((h, w)) < rng.random() * 0.3
        H[m] = rng.normal(0, 500, int(m.sum()))
        for val in (np.inf, -np.inf, -2e29, -1e31):
            H[rng.random((h, w)) < 0.02] = val
        floor = rng.random((h, w)) < rng.random()
        if trial % 7 == 0:
            floor[:] = True
        if trial % 11 == 0:
            floor = np.asfortranarray(floor)
        steps = int(rng.integers(0, 12))
        if trial % 5 == 0 and h > 4 and w > 4:
            # a window of a bigger picture, as lay_flight passes
            big = np.full((h + 6, w + 6), np.nan, dt)
            big[3:3 + h, 2:2 + w] = H
            H = big[3:3 + h, 2:2 + w]
        if not same_bits(R._spread_height(H, floor, steps),
                         plain_spread_height(H, floor, steps)):
            bad_h += 1
        lab = np.zeros((h, w), (np.int32, np.int64, np.int16)[trial % 3])
        m = rng.random((h, w)) < rng.random() * 0.2
        lab[m] = rng.integers(-3, 9, int(m.sum()))
        within = None if trial % 3 == 0 else rng.random((h, w)) < rng.random()
        if not same_bits(R._spread(lab, steps, within), plain_spread(lab, steps, within)):
            bad_l += 1
        mask = rng.random((h, w)) < rng.random()
        if trial % 6 == 0:
            mask[:] = False
        seeds = rng.random((h, w)) < rng.random() * 0.05
        if not np.array_equal(R._reach(mask, seeds), plain_reach(mask, seeds)):
            bad_r += 1
    check('_spread_height as the plain steps', not bad_h, '%d of %d differ' % (bad_h, n))
    check('_reach as the plain rounds', not bad_r, '%d of %d differ' % (bad_r, n))
    check('_spread as the plain steps', not bad_l, '%d of %d differ' % (bad_l, n))


# -- 2. sampling ----------------------------------------------------------------------

def _box_mesh(rng, sx, sy, sz, cut):
    """A box `cut` times subdivided on its top (long thin triangles too)."""
    xs = np.linspace(-sx, sx, cut + 1)
    ys = np.linspace(-sy, sy, cut + 1)
    verts, tris = [], []
    for z in (0.0, sz):
        for y in ys:
            for x in xs:
                verts.append((x, y, z))
    n = cut + 1
    for layer in (0, n * n):
        for r in range(cut):
            for c in range(cut):
                a = layer + r * n + c
                tris += [(a, a + 1, a + n), (a + 1, a + n + 1, a + n)]
    # walls between the two layers' rims
    rim = [r * n for r in range(n)] + [r * n + cut for r in range(n)]
    for k in range(len(rim) - 1):
        a, b = rim[k], rim[k + 1]
        tris += [(a, b, a + n * n), (b, b + n * n, a + n * n)]
    v = np.array(verts, np.float64) + rng.normal(0, 0.01, (len(verts), 3))
    return v, np.array(tris, np.int64)


def _placement(rng, at):
    a = rng.uniform(0, 2 * np.pi)
    m = np.eye(4)
    s = rng.uniform(0.5, 2.0)
    m[:3, :3] = np.array([[np.cos(a), np.sin(a), 0], [-np.sin(a), np.cos(a), 0],
                          [0, 0, 1]]) * s
    m[3, :3] = at
    return m.tolist()


def sampling():
    from riftlord.build.minimap import mapdata
    rng = np.random.default_rng(3)
    meshes = {'ground': _box_mesh(rng, 9000.0, 7000.0, 40.0, 3),
              'wall': _box_mesh(rng, 300.0, 20.0, 600.0, 2),
              'tile': _box_mesh(rng, 150.0, 150.0, 10.0, 4)}
    real = S._mesh
    S._mesh = lambda name: meshes.get(name)
    S._READ.update(meshes)
    try:
        frame = {'translation': (500.0, -300.0, 0.0), 'rotation': (0.0, 0.0, 0.3826834, 0.9238795),
                 'extent': (2500.0, 2500.0, 2000.0)}
        project = mapdata.projector(frame)
        pieces = [('ground', _placement(rng, (0.0, 0.0, -50.0)))]
        # inside, across the edge, and wholly off the square
        for _ in range(40):
            pieces.append(('wall', _placement(rng, (rng.uniform(-6000, 6000),
                                                     rng.uniform(-6000, 6000), 0.0))))
            pieces.append(('tile', _placement(rng, (rng.uniform(-9000, 9000),
                                                     rng.uniform(-9000, 9000),
                                                     rng.uniform(-100, 400)))))
        for size in (64, 200, 256):
            a = S.samples(pieces, project, size)
            b = plain_samples(pieces, project, size)
            check('samples at %d as the plain sampler' % size,
                  same_bits(a[0], b[0]) and same_bits(a[1], b[1]), '%d samples' % len(a[0]))
            g = S.Grid(a[0], a[1], size)
            keys = a[0].astype(np.int64) * g.span + np.clip(
                np.round((a[1] - g.z0) / S.Z_STEP).astype(np.int64), 0, g.span - 1)
            ref_keys, ref_counts = np.unique(keys, return_counts=True)
            check('Grid at %d from sorted samples as np.unique' % size,
                  np.array_equal(g.keys, ref_keys)
                  and same_bits(g.counts, ref_counts.astype(np.int32)))
    finally:
        S._mesh = real
        S._READ.difference_update(meshes)
    # samples out of order still count
    pix = np.array([5, 3, 5, 3, 9], np.int64)
    z = np.array([0, 0, 0, 4, 8], np.float32)
    g = S.Grid(pix, z, 4)
    check('Grid from unsorted samples counts repeats', g.counts.tolist() == [1, 1, 2, 1],
          str(g.counts.tolist()))


# -- 4. the cache ---------------------------------------------------------------------

def cache():
    folder = tempfile.mkdtemp(prefix='rl-minimapsolid-')
    keep_env, keep_bytes, keep_count = os.environ.get(S.CACHE_ENV), S.GRID_BYTES, S.GRID_KEEP
    os.environ[S.CACHE_ENV] = folder
    try:
        g = S.Grid(np.arange(1000, dtype=np.int64), np.zeros(1000, np.float32), 64)
        paths = [os.path.join(folder, 'grid_%d.npz' % k) for k in range(3)]
        for k, p in enumerate(paths):
            S._save(p, g)
            os.utime(p, (1000.0 + k, 1000.0 + k))
        one = os.path.getsize(paths[0])
        # grid_0 read last: it is the most recently used
        real_path = S._grid_path
        S._grid_path = lambda pieces, project, size: paths[0]
        try:
            got = S.grid([], None, 64)
        finally:
            S._grid_path = real_path
        check('a cached grid is read back', np.array_equal(got.keys, g.keys))
        S.GRID_BYTES, S.GRID_KEEP = int(2.5 * one), None
        S._evict()
        left = sorted(f for f in os.listdir(folder) if f.startswith('grid_'))
        check('the least recently used grid goes first past GRID_BYTES',
              left == ['grid_0.npz', 'grid_2.npz'], str(left))
        real_savez = np.savez

        def full(path, **kw):
            with open(path, 'wb') as fh:
                fh.write(b'part')
            raise OSError(28, 'No space left on device')
        np.savez = full
        try:
            S._save(os.path.join(folder, 'grid_9.npz'), g)
        finally:
            np.savez = real_savez
        left = [f for f in os.listdir(folder) if 'grid_9' in f]
        check('a failed write leaves no part file', not left, str(left))
    finally:
        S.GRID_BYTES, S.GRID_KEEP = keep_bytes, keep_count
        if keep_env is None:
            os.environ.pop(S.CACHE_ENV, None)
        else:
            os.environ[S.CACHE_ENV] = keep_env


# -- 5. whole paintings ---------------------------------------------------------------

def paintings():
    from PIL import Image
    from riftlord.mapeditor import scratch
    with open(os.path.join(HERE, 'plans.json')) as fh:
        plans = json.load(fh)
    for name, rec in sorted(plans.items()):
        t = time.time()
        if scratch.is_scratch(rec['plan']):
            painters = [R.scratch_minimap(rec['level'], rec['plan'])[1]]
        else:
            red = R.prepare(rec['level'], rec['plan'])
            painters = red.get('painters') or [red['painter']]
        for g, paint in enumerate(painters):
            got = np.asarray(paint(rec['size']))
            want = np.asarray(Image.open(os.path.join(HERE, '%s_%d.png' % (name, g))))
            check('%s floor %d as the plain painter drew it' % (name, g),
                  got.shape == want.shape and np.array_equal(got, want),
                  '%.0f s' % (time.time() - t))


spreading()
sampling()
cache()
if '--quick' not in sys.argv:
    paintings()
print('%d/%d passed' % (sum(checks), len(checks)))
sys.exit(0 if all(checks) else 1)
