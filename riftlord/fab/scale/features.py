# Generated from OMD2-Converter/thirdparty/scale-tools/features.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Geometry measurements shared by the OMD3 and pack sides: openings and stairs.

Input everywhere: `verts` (N x 3 float array, uu) and `tris` (M x 3 int array).

* `opening()` projects a door, arch or window mesh onto its wall plane, rasterises it,
  and measures the empty region the frame encloses: clear width at every height, clear
  height, sill height, and the tallest capsule of a given radius that passes.
* `stairs()` finds tread faces, clusters them into steps, and returns riser height and
  tread depth.
"""

import numpy as np

#: Raster cell for openings, uu. Small against any door (>= 100uu) and against the
#: thinnest frame member measured (about 10uu).
RES = 4.0

#: Triangle edges are also stamped at this spacing (fraction of RES), so faces seen
#: edge-on (the reveal of an arch) still block.
EDGE_STEP = 0.5

#: A face is a tread when |n.z| is at least this (same rule as stairfit.TREAD).
TREAD_NZ = 0.8

#: Flat faces within this many uu of the level below are the same step (nosing bevels
#: sit 2-4uu under the tread). No staircase measured here has a riser under 10uu.
STEP_MERGE = 8.0

#: Minimum tread area to count as a step, uu^2 (drops bevel slivers).
MIN_TREAD_AREA = 200.0

#: A height level is a step only when its tread area is at least this fraction of the
#: median area of the larger half of levels. Bevelled nosings make thin flat strips a
#: few uu below each tread; without this they read as 2.7uu risers.
STEP_AREA_FRAC = 0.3

#: An opening is a DOOR when its bottom is at most this far above the floor, uu: OMD3's
#: NavPower/Recast AgentMaxStepHeight (DefaultEngine.ini, measured). Higher is a window.
DOOR_STEP_MAX = 48.0

#: A frame is only an opening when the empty region is at least this wide and tall.
MIN_OPENING = 40.0


def _plane_axes(verts):
    """(normal axis, horizontal axis) for a wall-like mesh: the thinner of X/Y is the normal."""
    ext = verts.max(0) - verts.min(0)
    n = 0 if ext[0] <= ext[1] else 1
    return n, 1 - n


def raster(verts, tris, haxis, res=RES):
    """Occupancy grid in the (haxis, Z) plane. grid[row=z, col=u]."""
    P = np.stack([verts[:, haxis], verts[:, 2]], 1).astype(float)
    lo = P.min(0)
    hi = P.max(0)
    W = int(np.ceil((hi[0] - lo[0]) / res)) + 1
    H = int(np.ceil((hi[1] - lo[1]) / res)) + 1
    g = np.zeros((H, W), bool)
    Q = (P - lo) / res
    for t in tris:
        a, b, c = Q[t[0]], Q[t[1]], Q[t[2]]
        # edges
        for p, q in ((a, b), (b, c), (c, a)):
            n = int(np.ceil(np.abs(q - p).max() / EDGE_STEP)) + 1
            s = np.linspace(0, 1, n)[:, None]
            pts = p + (q - p) * s
            g[np.clip(np.rint(pts[:, 1]).astype(int), 0, H - 1),
              np.clip(np.rint(pts[:, 0]).astype(int), 0, W - 1)] = True
        # interior
        x0, x1 = int(np.floor(min(a[0], b[0], c[0]))), int(np.ceil(max(a[0], b[0], c[0])))
        y0, y1 = int(np.floor(min(a[1], b[1], c[1]))), int(np.ceil(max(a[1], b[1], c[1])))
        if x1 - x0 < 1 or y1 - y0 < 1:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(d) < 1e-9:
            continue
        l1 = ((b[1] - c[1]) * (xs - c[0]) + (c[0] - b[0]) * (ys - c[1])) / d
        l2 = ((c[1] - a[1]) * (xs - c[0]) + (a[0] - c[0]) * (ys - c[1])) / d
        l3 = 1 - l1 - l2
        m = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
        yy = np.clip(ys[m], 0, H - 1)
        xx = np.clip(xs[m], 0, W - 1)
        g[yy, xx] = True
    return g, lo


def _components(empty):
    """Label 4-connected components of True cells. Returns label array (0 = none)."""
    H, W = empty.shape
    lab = np.zeros((H, W), int)
    cur = 0
    for y in range(H):
        for x in range(W):
            if empty[y, x] and not lab[y, x]:
                cur += 1
                stack = [(y, x)]
                lab[y, x] = cur
                while stack:
                    cy, cx = stack.pop()
                    for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                        if 0 <= ny < H and 0 <= nx < W and empty[ny, nx] and not lab[ny, nx]:
                            lab[ny, nx] = cur
                            stack.append((ny, nx))
    return lab, cur


def opening(verts, tris, capsules=(), floor_z=None):
    """Measure the opening a frame encloses, or None.

    `floor_z` is the walking floor in mesh space (default: the mesh's lowest point).
    Returns dict: kind ('door' when the opening's bottom is within DOOR_STEP_MAX of the
    floor, else 'window'), width_min_lower_half, width_at_base, height (clear, at the widest column),
    sill (bottom of the opening above the mesh base), mesh_height, and for each
    (radius, height) in `capsules` whether a vertical capsule of that size fits through.
    """
    verts = np.asarray(verts, float)
    tris = np.asarray(tris, int)
    n, h = _plane_axes(verts)
    g, lo = raster(verts, tris, h)
    H, W = g.shape
    lab, k = _components(~g)
    best = None
    for c in range(1, k + 1):
        m = lab == c
        ys, xs = np.nonzero(m)
        if xs.min() == 0 or xs.max() == W - 1:
            continue          # outside the frame, left or right
        if ys.max() == H - 1:
            continue          # open to the top: not enclosed (a gap between posts)
        area = m.sum()
        if best is None or area > best[0]:
            best = (area, m, ys, xs)
    if best is None:
        return None
    _a, m, ys, xs = best
    widths = m.sum(1) * RES                 # empty cells per row (the region is one span)
    rows = np.nonzero(widths)[0]
    bottom, top = rows.min(), rows.max()
    clear_h = (top - bottom + 1) * RES
    w = widths[bottom:top + 1]
    if clear_h < MIN_OPENING or w.max() < MIN_OPENING:
        return None
    lower = w[:max(1, len(w) // 2)]
    sill_z = float(lo[1] + bottom * RES)
    floor = float(verts[:, 2].min()) if floor_z is None else float(floor_z)
    out = {'kind': 'door' if sill_z - floor <= DOOR_STEP_MAX else 'window',
           'width_at_base': round(float(w[0]), 1),
           'width_max': round(float(w.max()), 1),
           'width_min_lower_half': round(float(lower.min()), 1),
           'height': round(float(clear_h), 1),
           'sill': round(sill_z - floor, 1),
           'mesh_height': round(float(verts[:, 2].max() - verts[:, 2].min()), 1),
           'normal_axis': 'XY'[n]}
    # tallest clear rectangle of each width, anchored on the opening's floor
    fits = {}
    for r, hh in capsules:
        need = 2 * r
        ok = w >= need
        run = 0
        for v in ok:
            if not v:
                break
            run += 1
        fits['r%d' % r] = round(run * RES, 1)
    out['clear_height_for_width'] = fits
    # clear width of every RES-high row from the opening's floor up, so a caller can test
    # any capsule at any scale (scale_report.clear_height)
    out['profile_res'] = RES
    out['profile'] = [int(x) for x in w]
    return out


def _top_heightmap(V, T, res):
    """Highest flat (|n.z| >= TREAD_NZ) surface over each res x res column, seen from above.

    Taking the TOP flat face per column is what a walker stands on. It hides plank
    undersides (8uu below each tread), nosing bevels and stair stringers, and it does
    not depend on triangle winding, which is inconsistent both in OMD3's cooked meshes
    (stairfit.py) and in some 4.20 pack meshes (Stylized Egypt SM_stairs_01).
    """
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    nrm = np.cross(b - a, c - a)
    ar = np.linalg.norm(nrm, axis=1)
    ok = ar > 1e-6
    nz = np.zeros(len(T))
    nz[ok] = np.abs(nrm[ok, 2]) / ar[ok]
    flat = np.nonzero(ok & (nz >= TREAD_NZ))[0]
    if len(flat) < 4:
        return None
    lo = V[:, :2].min(0)
    hi = V[:, :2].max(0)
    W = int(np.ceil((hi[0] - lo[0]) / res)) + 1
    H = int(np.ceil((hi[1] - lo[1]) / res)) + 1
    hm = np.full((H, W), -np.inf)
    for i in flat:
        p = (np.array([a[i, :2], b[i, :2], c[i, :2]]) - lo) / res
        z = (a[i, 2] + b[i, 2] + c[i, 2]) / 3.0
        x0, x1 = int(np.floor(p[:, 0].min())), int(np.ceil(p[:, 0].max()))
        y0, y1 = int(np.floor(p[:, 1].min())), int(np.ceil(p[:, 1].max()))
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        P0, P1, P2 = p
        d = (P1[1] - P2[1]) * (P0[0] - P2[0]) + (P2[0] - P1[0]) * (P0[1] - P2[1])
        if abs(d) < 1e-9:
            continue
        l1 = ((P1[1] - P2[1]) * (xs - P2[0]) + (P2[0] - P1[0]) * (ys - P2[1])) / d
        l2 = ((P2[1] - P0[1]) * (xs - P2[0]) + (P0[0] - P2[0]) * (ys - P2[1])) / d
        m = (l1 >= -1e-6) & (l2 >= -1e-6) & (1 - l1 - l2 >= -1e-6)
        yy = np.clip(ys[m], 0, H - 1)
        xx = np.clip(xs[m], 0, W - 1)
        hm[yy, xx] = np.maximum(hm[yy, xx], z)
    return hm, lo


#: Raster cell for the top-down tread heightmap, uu.
TREAD_RES = 4.0


def stairs(verts, tris):
    """Riser and going of a staircase mesh, or None.

    Treads come from the top-down heightmap of flat faces (_top_heightmap), grouped into
    levels (STEP_MERGE), filtered by area (STEP_AREA_FRAC, MIN_TREAD_AREA). The climb axis
    is the horizontal axis whose level centroids best follow height; `going` is the median
    centroid spacing along it, `riser` the median height step.
    """
    V = np.asarray(verts, float)
    T = np.asarray(tris, int)
    got = _top_heightmap(V, T, TREAD_RES)
    if got is None:
        return None
    hm, lo = got
    ys, xs = np.nonzero(np.isfinite(hm))
    zs_all = hm[ys, xs]
    order = np.argsort(zs_all)
    levels = []
    for i in order:
        z = zs_all[i]
        if levels and z - levels[-1]['top'] <= STEP_MERGE:
            L = levels[-1]
            L['top'] = z
        else:
            L = {'top': z, 'idx': []}
            levels.append(L)
        L['idx'].append(i)
    cell = TREAD_RES * TREAD_RES
    steps = []
    for L in levels:
        idx = np.array(L['idx'])
        area = len(idx) * cell
        if area < MIN_TREAD_AREA:
            continue
        px = lo[0] + (xs[idx] + 0.5) * TREAD_RES
        py = lo[1] + (ys[idx] + 0.5) * TREAD_RES
        steps.append({'z': float(np.median(zs_all[idx])), 'area': area,
                      'c': np.array([px.mean(), py.mean()]),
                      'ext': np.array([np.ptp(px) + TREAD_RES, np.ptp(py) + TREAD_RES])})
    if steps:
        ars = np.sort([s['area'] for s in steps])
        ref = float(np.median(ars[len(ars) // 2:]))
        steps = [s for s in steps if s['area'] >= STEP_AREA_FRAC * ref]
    if len(steps) < 3:
        return None
    zs = np.array([s['z'] for s in steps])
    cs = np.array([s['c'] for s in steps])
    corr = [abs(np.corrcoef(cs[:, k], zs)[0, 1]) if np.std(cs[:, k]) > 1e-6 else 0.0 for k in (0, 1)]
    ax = int(np.argmax(corr))
    risers = np.diff(zs)
    return {'steps': len(steps), 'riser_median': round(float(np.median(risers)), 1),
            'riser_min': round(float(risers.min()), 1), 'riser_max': round(float(risers.max()), 1),
            'going_median': round(float(np.median(np.abs(np.diff(cs[:, ax])))), 1),
            'climb_axis': 'XY'[ax], 'axis_corr': round(float(corr[ax]), 3),
            'rise_total': round(float(zs[-1] - zs[0]), 1),
            'width': round(float(np.median([s['ext'][1 - ax] for s in steps])), 1)}
