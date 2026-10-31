"""riftlord.fab.analyse.depth_map as a per-triangle loop, as it was before vectorising
(2026-10-24): the reference test_fab.py holds the vectorised one to, bit for bit."""
import math

import numpy as np

SEAM_GROW = 2.0
PEEL_EPS = 1.0


def depth_map(Q, T, step, frame=None, two=None, cap=None, with_raw=False):
    """Q: points in (u, v, depth) coordinates. Frontmost depth per (u, v) sample and the facing
    (normal . +depth) of the triangle that put it there; a triangle flagged in `two` (drawn
    two-sided) faces the viewer from either side. frame=(lo, shape) samples on another map's
    lattice. cap: per sample, only geometry at least PEEL_EPS behind this depth (depth peeling).
    with_raw: also the unsigned-free facing and whether the sample's triangle is two-sided."""
    if frame is not None:
        lo, shape = np.asarray(frame[0], np.float64), tuple(frame[1])
    else:
        lo = Q[:, :2].min(0)
        hi = Q[:, :2].max(0)
        shape = (max(1, int(math.ceil((hi[0] - lo[0]) / step))), max(1, int(math.ceil((hi[1] - lo[1]) / step))))
    depth = np.full(shape, -np.inf)
    facing = np.zeros(shape)
    tri = Q[T]
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    n = np.cross(c - a, b - a)
    # (u, v, depth) is right-handed when built as (u, v, n) with n = u x v ... the frames below are
    # built so; the sign is checked against the true normals by the caller
    nl = np.linalg.norm(n, axis=1)
    face = n[:, 2] / np.maximum(nl, 1e-12)
    raw_face = face
    if two is not None:
        face = np.where(two, np.abs(face), face)
    rawmap = np.zeros(shape)
    twomap = np.zeros(shape, bool)
    t2 = tri[..., :2] - lo
    d3 = tri[..., 2]
    mn, mx = t2.min(1) - SEAM_GROW, t2.max(1) + SEAM_GROW
    i0 = np.clip(np.floor(mn[:, 0] / step - 0.5).astype(int), 0, shape[0])
    i1 = np.clip(np.floor(mx[:, 0] / step - 0.5).astype(int) + 1, 0, shape[0])
    j0 = np.clip(np.floor(mn[:, 1] / step - 0.5).astype(int), 0, shape[1])
    j1 = np.clip(np.floor(mx[:, 1] / step - 0.5).astype(int) + 1, 0, shape[1])
    for k in np.nonzero((i1 > i0) & (j1 > j0) & (np.abs(face) > 1e-3))[0]:
        xs = (np.arange(i0[k], i1[k]) + 0.5) * step
        ys = (np.arange(j0[k], j1[k]) + 0.5) * step
        X, Y = np.meshgrid(xs, ys, indexing='ij')
        (x0, y0), (x1, y1), (x2, y2) = t2[k]
        den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(den) < 1e-9:
            continue
        l0 = ((y1 - y2) * (X - x2) + (x2 - x1) * (Y - y2)) / den
        l1 = ((y2 - y0) * (X - x2) + (x0 - x2) * (Y - y2)) / den
        l2 = 1 - l0 - l1
        # barycentric slack per edge: SEAM_GROW over the altitude to that edge
        e = np.linalg.norm(t2[k][[1, 2, 0]] - t2[k][[2, 0, 1]], axis=1)
        slack = SEAM_GROW * e / max(abs(den), 1e-9)
        ins = (l0 >= -slack[0]) & (l1 >= -slack[1]) & (l2 >= -slack[2])
        if not ins.any():
            continue
        z = np.clip(l0 * d3[k, 0] + l1 * d3[k, 1] + l2 * d3[k, 2], d3[k].min(), d3[k].max())
        sub = depth[i0[k]:i1[k], j0[k]:j1[k]]
        w = ins & (z > sub)
        if cap is not None:
            w &= z < cap[i0[k]:i1[k], j0[k]:j1[k]] - PEEL_EPS
        sub[w] = z[w]
        facing[i0[k]:i1[k], j0[k]:j1[k]][w] = face[k]
        if with_raw:
            rawmap[i0[k]:i1[k], j0[k]:j1[k]][w] = raw_face[k]
            twomap[i0[k]:i1[k], j0[k]:j1[k]][w] = bool(two[k]) if two is not None else False
    if with_raw:
        return depth, facing, lo, rawmap, twomap
    return depth, facing, lo
