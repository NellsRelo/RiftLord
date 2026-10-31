"""A level's solid geometry seen from above, for the Regenerate minimap.

Every placed piece's triangles are sampled in the world at the resolution of the grid
they are drawn into (riftlord/minimapredraw.py draws at its own size) and dropped into
it as (pixel, height) samples; the grid is kept on disk per frame. riftlord/
minimapredraw.py then asks, per pixel and relative to the floor there: is there geometry
standing in the cut band above the floor (a wall, railing, pillar), or a surface at the
floor's own height (floor to grow onto)?

That is how a floor plan is drawn: a horizontal cut a little above the floor, what the
cut passes through drawn as wall.
"""

import functools
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: Surfaces are sampled this many times per pixel side of the grid they are drawn into
#: (two samples a pixel leave no pixel of a face unhit).
SAMPLES_PER_PIXEL = 2.0
#: Up a wall a triangle is sampled this far apart in height, in uu (half the walkable
#: graph's 48 uu step: the cut band is 100 uu deep).
Z_SAMPLE = 24.0
#: A triangle is cut into at most this many steps a side (a 6,000 uu ground triangle at
#: 3 uu a step).
MAX_STEPS = 2000
#: Bump when the sampling changes, so old cache files are not read.
CACHE_VERSION = 3
#: meshcat categories left out of the solid: plants, props on walls and floors, effects,
#: traps (the shipped minimaps draw none of them).
UNDRAWN = ('foliage', 'decoration', 'effect', 'trap')
#: Pieces whose footprint is smaller than this are props, in uu squared (a 150 uu square:
#: barrels, mushrooms, small stones; riftlord.minimapredraw.PROP_AREA).
PROP_AREA = 150.0 * 150.0
#: Meshes kept in memory between drawings, and how many are read from the paks at once.
MESH_MEMORY = 512
MESH_WORKERS = 4
#: Meshes already read this session.
_READ = set()


#: A folder for the solid's caches in place of the app's cache folder (the scoring
#: harness keeps every level's grids on the scratch disk).
CACHE_ENV = 'MINIMAP_SOLID_CACHE'


def _cache_dir():
    from riftlord.core import gamepath
    return os.environ.get(CACHE_ENV) or os.path.join(gamepath.scratch('cache', gamepath.CODE_ROOT),
                                                     'minimap-solid')


def _mesh_path(mesh):
    from riftlord.mapeditor import omd3serve
    return os.path.join(_cache_dir(), 'meshes', '%s.npz' % omd3serve.safe_name(mesh))


def _read_mesh(mesh):
    """(verts, tris) arrays of a mesh read for the solid alone, kept as a small binary
    file: the Map Editor's own mesh cache (omd3serve) when it already has the mesh,
    else straight from the paks (omd3mesh), without writing omd3serve's JSON, which on a
    cold cache took most of the first preview's time (32 s of 43 on Scenario_13)."""
    import json
    import numpy as np
    from riftlord.mapeditor import omd3serve
    path = _mesh_path(mesh)
    if os.path.exists(path):
        try:
            got = np.load(path)
            return got['verts'], got['tris']
        except Exception:                            # noqa: BLE001
            pass
    got = omd3serve.models().get(mesh)
    if got is None:
        cached = os.path.join(omd3serve.MESH_CACHE, '%s.json' % omd3serve.safe_name(mesh))
        if os.path.exists(cached):
            try:
                with open(cached) as fh:
                    got = json.load(fh)
            except (OSError, ValueError):
                got = None
    if got is None:
        try:
            from riftlord.formats.omd3 import omd3mesh
            got = omd3mesh.read(mesh)
        except Exception:                            # noqa: BLE001
            got = None
        if got is None:
            try:
                from riftlord.formats.omd3 import omd3skm
                got = omd3skm.read(mesh)
            except Exception:                        # noqa: BLE001
                got = None
    if not got or not got.get('verts') or not got.get('tris'):
        return None
    v = np.asarray(got['verts'], dtype=np.float32).reshape(-1, 3)
    t = np.asarray(got['tris'], dtype=np.int32).reshape(-1, 3)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        np.savez(path + '.part.npz', verts=v, tris=t)
        os.replace(path + '.part.npz', path)
    except OSError:
        pass
    return v, t


def _read_all(names):
    """Read `names` on MESH_WORKERS daemon threads: not a ThreadPoolExecutor, whose
    threads the interpreter joins at exit, so a preview drawing while the app quit kept
    the server from stopping."""
    import queue
    import threading
    todo = queue.Queue()
    for name in names:
        todo.put(name)

    def work():
        while True:
            try:
                name = todo.get_nowait()
            except queue.Empty:
                return
            try:
                _mesh(name)
            except Exception:                        # noqa: BLE001
                pass
    threads = [threading.Thread(target=work, name='minimap-mesh-%d' % k, daemon=True)
               for k in range(max(1, min(MESH_WORKERS, len(names))))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


@functools.lru_cache(maxsize=MESH_MEMORY)
def _mesh(mesh):
    """(verts (N, 3) float64, tris (T, 3) int64) of a mesh, or None."""
    import numpy as np
    got = _read_mesh(mesh)
    if got is None:
        return None
    v = np.asarray(got[0], dtype=np.float64).reshape(-1, 3)
    t = np.asarray(got[1], dtype=np.int64).reshape(-1, 3)
    if not len(v) or not len(t) or t.max() >= len(v):
        return None
    return v, t


def drawn(mesh, category, lo, hi):
    """True when a piece belongs in the solid: not a plant, prop or effect."""
    if category in UNDRAWN:
        return False
    if (hi[0] - lo[0]) * (hi[1] - lo[1]) < PROP_AREA:
        return False
    return True


def samples(pieces, project, size):
    """(pixel index int64, z float32) of the surfaces of `pieces` [(mesh, matrix)] inside
    the `size` square of `project` (world (x, y) -> (u, v) in 0..1). Each mesh's
    placements are done together: every vertex and triangle centre is a sample, and a
    triangle longer than a sample step is also cut into steps of SAMPLES_PER_PIXEL a
    pixel side. One sample is kept per pixel and height step."""
    import collections
    import numpy as np
    o = np.array(project((0.0, 0.0)), dtype=np.float64) * size
    ex = (np.array(project((1.0, 0.0)), dtype=np.float64) * size) - o
    ey = (np.array(project((0.0, 1.0)), dtype=np.float64) * size) - o
    uu = 1.0 / float(np.hypot(ex[0], ex[1]))
    step = uu / SAMPLES_PER_PIXEL
    by_mesh = collections.defaultdict(list)
    for mesh, matrix in pieces:
        if matrix is not None:
            by_mesh[mesh].append(matrix)
    # meshes not read yet are pulled out of the paks together
    todo = [m for m in by_mesh if m not in _READ]
    if todo:
        _read_all(todo)
        _READ.update(todo)
    out = []

    def keep(pts):
        u = o[0] + pts[:, 0] * ex[0] + pts[:, 1] * ey[0]
        v = o[1] + pts[:, 0] * ex[1] + pts[:, 1] * ey[1]
        ok = (u >= 0) & (u < size) & (v >= 0) & (v < size)
        if ok.any():
            pix = v[ok].astype(np.int64) * size + u[ok].astype(np.int64)
            zq = np.round(pts[ok, 2] / Z_STEP).astype(np.int64)
            out.append(_unique(pix * Z_KEY + (zq + Z_KEY // 2)))

    def keep_lattice(A, W1, W2, fa, fb):
        # the points A + W1 fa + W2 fb, one axis at a time (the same sums as the whole
        # (triangles, points, 3) array, without it), z only where kept
        def axis(c, at=None):
            if at is None:
                return (A[:, c, None] + W1[:, c, None] * fa) + W2[:, c, None] * fb
            t, k = at
            return (A[t, c] + W1[t, c] * fa[k]) + W2[t, c] * fb[k]
        px, py = axis(0), axis(1)
        u = o[0] + px * ex[0] + py * ey[0]
        v = o[1] + px * ex[1] + py * ey[1]
        t, k = np.nonzero((u >= 0) & (u < size) & (v >= 0) & (v < size))
        if len(t):
            pix = v[t, k].astype(np.int64) * size + u[t, k].astype(np.int64)
            zq = np.round(axis(2, (t, k)) / Z_STEP).astype(np.int64)
            out.append(_unique(pix * Z_KEY + (zq + Z_KEY // 2)))
    for mesh, mats in by_mesh.items():
        got = _mesh(mesh)
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
        # long triangles: cut into steps along two edges, each edge's step count set by
        # its run (a pixel apart over the ground) and its rise (Z_SAMPLE apart up a wall)
        e1, e2 = lb - la, lc - la
        big_local = local_edge > 0
        if not big_local.any():
            continue
        R, T = M[:, :3, :3], M[:, 3, :3]
        # (placements, triangles, 3) edge vectors in the world
        w1 = np.matmul(e1[None, :, :], R)
        w2 = np.matmul(e2[None, :, :], R)
        ka = np.ceil(np.maximum(np.hypot(w1[..., 0], w1[..., 1]) / step,
                                np.abs(w1[..., 2]) / Z_SAMPLE)).astype(np.int64)
        kb = np.ceil(np.maximum(np.hypot(w2[..., 0], w2[..., 1]) / step,
                                np.abs(w2[..., 2]) / Z_SAMPLE)).astype(np.int64)
        need = (ka > 1) | (kb > 1)
        if not need.any():
            continue
        pi, ti = np.nonzero(need)
        kA_all = np.clip(ka[pi, ti], 1, MAX_STEPS)
        kB_all = np.clip(kb[pi, ti], 1, MAX_STEPS)
        A = np.matmul(la[ti][:, None, :], R[pi])[:, 0, :] + T[pi]
        W1, W2 = w1[pi, ti], w2[pi, ti]
        # a triangle wholly off the square (OFF_MARGIN to spare) has no step on it (97%
        # of North Wings' 3.6 billion steps at 1024 lay on triangles wholly off its frame)
        on = np.ones(len(A), bool)
        for d in (0, 1):
            ends = [o[d] + c[:, 0] * ex[d] + c[:, 1] * ey[d] for c in (A, A + W1, A + W2)]
            lo = np.minimum(np.minimum(ends[0], ends[1]), ends[2])
            hi = np.maximum(np.maximum(ends[0], ends[1]), ends[2])
            on &= (hi >= -OFF_MARGIN) & (lo <= size + OFF_MARGIN)
        if not on.any():
            continue
        A, W1, W2, kA_all, kB_all = A[on], W1[on], W2[on], kA_all[on], kB_all[on]
        # the triangles grouped by their step counts (one sort)
        pairs = kA_all * (MAX_STEPS + 1) + kB_all
        order = np.argsort(pairs, kind='stable')
        pairs = pairs[order]
        cuts = np.flatnonzero(np.concatenate([[True], pairs[1:] != pairs[:-1], [True]]))
        for g0, g1 in zip(cuts[:-1], cuts[1:]):
            sel = order[g0:g1]
            kA, kB = int(pairs[g0] // (MAX_STEPS + 1)), int(pairs[g0] % (MAX_STEPS + 1))
            i, j = np.meshgrid(np.arange(kA + 1), np.arange(kB + 1), indexing='ij')
            fa, fb = (i / float(kA)).ravel(), (j / float(kB)).ravel()
            m = fa + fb <= 1.0 + 1e-9
            fa, fb = fa[m], fb[m]
            chunk = max(1, 3000000 // len(fa))
            for s0 in range(0, len(sel), chunk):
                q = sel[s0:s0 + chunk]
                keep_lattice(A[q], W1[q], W2[q], fa, fb)
    if not out:
        return np.zeros(0, np.int64), np.zeros(0, np.float32)
    k = _unique(np.concatenate(out))
    return k // Z_KEY, ((k % Z_KEY) - Z_KEY // 2).astype(np.float32) * Z_STEP


#: A triangle is left out of the sampling only when it lies this many pixels past the
#: square's edge: its steps sit inside the triangle up to float rounding, far under a
#: pixel, so none of them would land in the square.
OFF_MARGIN = 1.0


def _unique(keys):
    """Sorted unique `keys`: a stable sort, which takes the presorted runs a triangle's
    steps make whole (North Wings at 1024: np.unique 32 s, this 6 s)."""
    import numpy as np
    s = np.sort(keys, kind='stable')
    if len(s) < 2:
        return s
    first = np.empty(len(s), bool)
    first[0] = True
    np.not_equal(s[1:], s[:-1], out=first[1:])
    return s[first]


#: Height steps a sample key holds per pixel (z within +-2^20 steps of 0).
Z_KEY = 1 << 21


#: Sample heights are kept to this step, in uu, so samples one above another in a pixel
#: collapse into a few.
Z_STEP = 4.0


class Grid(object):
    """The solid's samples in a `size` pixel square, sorted for height queries: `keys`
    (pixel * span + height step, unique) and how many samples each holds (`counts`), so
    pieces can be taken out and put back."""

    def __init__(self, pix, z, size, z0=None, span=None):
        import numpy as np
        self.size = size
        if not len(pix):
            self.keys = np.zeros(0, np.int64)
            self.counts = np.zeros(0, np.int32)
            self.z0, self.span = (0.0 if z0 is None else z0), (1 if span is None else span)
            self._done()
            return
        self.z0 = float(z.min()) - Z_STEP if z0 is None else z0
        zq = np.round((z - self.z0) / Z_STEP).astype(np.int64)
        self.span = int(zq.max()) + 2 if span is None else span
        zq = np.clip(zq, 0, self.span - 1)
        keys = pix.astype(np.int64) * self.span + zq
        if len(keys) < 2 or bool((keys[1:] > keys[:-1]).all()):
            # already sorted and unique (samples' own order): one sample each
            self.keys, self.counts = keys, np.ones(len(keys), np.int32)
        else:
            self.keys, counts = np.unique(keys, return_counts=True)
            self.counts = counts.astype(np.int32)
        self._done()

    def _done(self):
        import numpy as np
        self.empty = not len(self.keys)
        occ = np.zeros(self.size * self.size, bool)
        if len(self.keys):
            occ[self.keys // self.span] = True
        self.occupied = occ.reshape(self.size, self.size)

    def edited(self, gone, new):
        """A copy with the samples of Grid-less (pix, z) `gone` taken out and `new` put in,
        on this grid's height steps."""
        import numpy as np
        out = Grid(np.zeros(0, np.int64), np.zeros(0, np.float32), self.size, self.z0, self.span)
        keys, counts = self.keys.copy(), self.counts.copy()
        for (pix, z), sign in ((gone, -1), (new, 1)):
            if not len(pix):
                continue
            zq = np.clip(np.round((z - self.z0) / Z_STEP).astype(np.int64), 0, self.span - 1)
            k, c = np.unique(pix.astype(np.int64) * self.span + zq, return_counts=True)
            if sign < 0:
                if not len(keys):
                    continue
                at = np.clip(np.searchsorted(keys, k), 0, len(keys) - 1)
                hit = keys[at] == k
                np.subtract.at(counts, at[hit], c[hit].astype(np.int32))
                keep = counts > 0
                keys, counts = keys[keep], counts[keep]
            else:
                allk = np.concatenate([keys, k])
                allc = np.concatenate([counts, c.astype(np.int32)])
                order = np.argsort(allk, kind='stable')
                allk, allc = allk[order], allc[order]
                first = np.concatenate([[True], allk[1:] != allk[:-1]])
                idx = np.flatnonzero(first)
                keys, counts = allk[idx], np.add.reduceat(allc, idx).astype(np.int32)
        out.keys, out.counts = keys, counts
        out._done()
        return out

    def between(self, flat, lo, hi):
        """Bool per flat pixel index: a sample at that pixel with lo < z < hi (arrays)."""
        import numpy as np
        if self.empty:
            return np.zeros(len(flat), bool)
        a = np.clip(np.ceil((np.asarray(lo) - self.z0) / Z_STEP + 1e-6), 0, self.span - 1)
        b = np.clip(np.floor((np.asarray(hi) - self.z0) / Z_STEP - 1e-6), -1, self.span - 1)
        base = np.asarray(flat, np.int64) * self.span
        i = np.searchsorted(self.keys, base + a.astype(np.int64), 'left')
        j = np.searchsorted(self.keys, base + b.astype(np.int64), 'right')
        return (j > i) & (b >= a)


    def highest(self, flat, lo, hi):
        """Float per flat pixel index: the highest sample with lo < z < hi there, or nan."""
        import numpy as np
        out = np.full(len(flat), np.nan, np.float32)
        if self.empty:
            return out
        a = np.clip(np.ceil((np.asarray(lo) - self.z0) / Z_STEP + 1e-6), 0, self.span - 1)
        b = np.clip(np.floor((np.asarray(hi) - self.z0) / Z_STEP - 1e-6), -1, self.span - 1)
        base = np.asarray(flat, np.int64) * self.span
        j = np.searchsorted(self.keys, base + b.astype(np.int64), 'right') - 1
        ok = (j >= 0) & (b >= a)
        jj = np.where(ok, j, 0)
        key = self.keys[jj]
        ok &= (key >= base + a.astype(np.int64)) & (key <= base + b.astype(np.int64))
        out[ok] = (key[ok] - base[ok]).astype(np.float32) * Z_STEP + self.z0
        return out


#: Grids kept on disk: the least recently used (written or read) dropped past
#: GRID_BYTES in all, or past GRID_KEEP files when set. No count by default: the Map
#: Editor's preview writes a small grid (7-8 MB) per edited plan, so a count of 24
#: dropped a level's build grid (North Wings at 1024: 262 MB) after 24 previews; the
#: app's cache held 22 such preview grids written 16:17-17:46 on 2026-10-30.
GRID_KEEP = None
GRID_BYTES = 1500 * 1024 * 1024
#: Bump when the grid's file layout changes.
GRID_VERSION = 2
#: An edited level's grid is its unedited grid with the changed pieces taken out and
#: put back when at most this many pieces differ; past it the grid is built whole.
EDIT_MAX = 400


def _identity(mesh, matrix):
    import numpy as np
    return (str(mesh), np.round(np.asarray(matrix, np.float64), 2).tobytes())


def _grid_path(pieces, project, size):
    import hashlib
    h = hashlib.sha1()
    h.update(('%d %d %d %r %r' % (CACHE_VERSION, GRID_VERSION, size,
                                    tuple(project((0.0, 0.0))),
                                    tuple(project((1000.0, 1000.0))))).encode())
    for mesh, matrix in pieces:
        m, b = _identity(mesh, matrix)
        h.update(m.encode())
        h.update(b)
    return os.path.join(_cache_dir(), 'grid_%s.npz' % h.hexdigest()[:24])


def _load(path, size):
    import numpy as np
    got = np.load(path)
    g = Grid(np.zeros(0, np.int64), np.zeros(0, np.float32), size,
             float(got['z0']), int(got['span']))
    g.keys, g.counts = got['keys'], got['counts']
    g._done()
    return g


def _save(path, g):
    import numpy as np
    tmp = path + '.part.npz'
    try:
        os.makedirs(_cache_dir(), exist_ok=True)
        np.savez(tmp, keys=g.keys, counts=g.counts, z0=g.z0, span=g.span)
        os.replace(tmp, path)
    except OSError:
        # a full disk leaves a part file behind
        try:
            os.remove(tmp)
        except OSError:
            pass
    _evict()


def _evict():
    """Drop the least recently used grids past GRID_BYTES (and GRID_KEEP)."""
    try:
        names = [os.path.join(_cache_dir(), f) for f in os.listdir(_cache_dir())
                 if f.startswith('grid_')]
        old = sorted(((os.path.getmtime(f), os.path.getsize(f), f) for f in names),
                     reverse=True)
    except OSError:
        return
    total = 0
    for k, (_t, n, f) in enumerate(old):
        total += n
        if (GRID_KEEP is not None and k >= GRID_KEEP) or total > GRID_BYTES:
            try:
                os.remove(f)
            except OSError:
                pass


def grid(pieces, project, size, base=None):
    """A Grid of `pieces` [(mesh, matrix)] in the `size` square of `project`, kept on disk
    by a digest of the pieces and the frame. With `base` (the unedited level's pieces)
    an edited level's grid is the base grid with the changed pieces taken out and put
    back, so moving one piece costs that piece, not the level."""
    import collections
    path = _grid_path(pieces, project, size)
    if os.path.exists(path):
        try:
            got = _load(path, size)
        except Exception:                            # noqa: BLE001
            got = None
        if got is not None:
            # a read counts as a use: _evict drops the least recently used
            try:
                os.utime(path)
            except OSError:
                pass
            return got
    g = None
    if base is not None and base is not pieces:
        have = collections.Counter(_identity(m, x) for m, x in base)
        want = collections.Counter(_identity(m, x) for m, x in pieces)
        gone_ids, new_ids = have - want, want - have
        if sum(gone_ids.values()) + sum(new_ids.values()) <= EDIT_MAX:
            root = grid(base, project, size)
            if not gone_ids and not new_ids:
                # no solid piece changed: the base grid is this one (already saved
                # under this path when the two lists are in the same order)
                if not os.path.exists(path):
                    _save(path, root)
                return root
            gone, new = [], []
            for items, ids, out in ((base, gone_ids, gone), (pieces, new_ids, new)):
                for m, x in items:
                    key = _identity(m, x)
                    if ids.get(key, 0) > 0:
                        ids[key] -= 1
                        out.append((m, x))
            g = root.edited(samples(gone, project, size), samples(new, project, size))
    if g is None:
        pix, z = samples(pieces, project, size)
        g = Grid(pix, z, size)
    _save(path, g)
    return g
