# Generated from OMD2-Converter/thirdparty/fabconvert/thumbs.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Offline thumbnail of a converted mesh: a small software rasteriser (no GPU), textured with
the baked base colour of each slot, lit by one directional light, seen from the editor
thumbnail's angle (round mat2 measured it: azimuth OrbitYaw + 90 with OrbitYaw -157.5)."""

import math

import numpy as np
from PIL import Image

#: Thumbnail edge (px) and the share of it the mesh fills.
SIZE = 256
FILL = 0.86
#: View: the editor thumbnail azimuth (mat2 THUMB_VIEW_AZIMUTH) and a downward pitch (deg).
AZIMUTH = -157.5 + 90.0
PITCH = -25.0
#: Light direction (toward the light, world) and the ambient share.
LIGHT = np.array([0.35, -0.55, 0.76])
AMBIENT = 0.38
BACKGROUND = (38, 41, 48)
#: Colour of a slot with no baked texture (dropped or substituted).
NEUTRAL = (150, 150, 150)


def _lin(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _srgb(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055) * 255.0


def _sample(t, u, v):
    """Nearest texel of a wrapped texture at UVs (u, v)."""
    h, wd = t.shape[:2]
    return t[np.clip(((v % 1) * h).astype(int), 0, h - 1), np.clip(((u % 1) * wd).astype(int), 0, wd - 1)]


def lerp_weights(colors, channels):
    """Per-vertex layer weights (V, 1 + len(channels)) of a successive vertex-colour blend: layer 0
    under all, each later layer lerped over the result by its colour channel (0-3 = RGBA)."""
    c = np.asarray(colors, np.float64) / 255.0
    w = np.zeros((len(c), 1 + len(channels)))
    w[:, 0] = 1.0
    for k, ch in enumerate(channels, 1):
        w *= (1.0 - c[:, ch:ch + 1])
        w[:, k] = c[:, ch]
    return w


def render(desc, slot_bc, size=SIZE):
    """desc: written mesh description; slot_bc: {slot: (H, W, 4) uint8 sRGB BC, or
    {'layers': [BC, ...], 'weights': (V, layers) per-vertex blend}, or None}."""
    P = np.asarray(desc['positions'], np.float64)
    T = np.asarray(desc['triangles'], np.int64)
    UV = np.asarray(desc['uvs'][0], np.float64)
    M = np.asarray(desc['materials'], np.int64)
    a, p = math.radians(AZIMUTH), math.radians(PITCH)
    fwd = np.array([math.cos(a) * math.cos(p), math.sin(a) * math.cos(p), math.sin(p)])
    up = np.array([0, 0, 1.0]) - fwd * fwd[2]
    up /= np.linalg.norm(up)
    right = np.cross(up, fwd)
    X, Y, Z = P @ right, P @ up, P @ fwd
    span = max(np.ptp(X), np.ptp(Y), 1e-6)
    s = size * FILL / span
    sx = size / 2 + (X - (X.min() + X.max()) / 2) * s
    sy = size / 2 - (Y - (Y.min() + Y.max()) / 2) * s
    img = np.empty((size, size, 3))
    img[:] = BACKGROUND
    zb = np.full((size, size), np.inf)
    fn = np.cross(P[T[:, 2]] - P[T[:, 0]], P[T[:, 1]] - P[T[:, 0]])
    fn /= np.linalg.norm(fn, axis=1, keepdims=True) + 1e-12
    L = LIGHT / np.linalg.norm(LIGHT)
    shade = AMBIENT + (1 - AMBIENT) * np.clip(fn @ L, 0, 1)
    # draw far triangles first only matters for ties; the depth buffer decides
    layered = {k: v for k, v in slot_bc.items() if isinstance(v, dict)}
    slot_bc = {k: v for k, v in slot_bc.items() if k not in layered}
    tex = {k: (None if v is None else _lin(np.asarray(v, np.float64)[..., :3])) for k, v in slot_bc.items()}
    alpha = {k: (None if v is None else np.asarray(v)[..., 3]) for k, v in slot_bc.items()}
    for k, v in layered.items():
        tex[k] = [_lin(np.asarray(t, np.float64)[..., :3]) for t in v['layers']]
        wt = np.asarray(v['weights'], np.float64)
        layered[k] = wt / np.maximum(wt.sum(1, keepdims=True), 1e-9)
    for k in range(len(T)):
        i0, i1, i2 = T[k]
        xs, ys = sx[[i0, i1, i2]], sy[[i0, i1, i2]]
        x0, x1 = int(max(math.floor(xs.min()), 0)), int(min(math.ceil(xs.max()), size - 1))
        y0, y1 = int(max(math.floor(ys.min()), 0)), int(min(math.ceil(ys.max()), size - 1))
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + .5, np.arange(y0, y1 + 1) + .5)
        den = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
        if abs(den) < 1e-12:
            continue
        l0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / den
        l1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / den
        l2 = 1 - l0 - l1
        ins = (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
        if not ins.any():
            continue
        z = l0 * Z[i0] + l1 * Z[i1] + l2 * Z[i2]
        sub = zb[y0:y1 + 1, x0:x1 + 1]
        w = ins & (z < sub)
        if not w.any():
            continue
        t = tex.get(int(M[k]))
        if t is None:
            col = np.broadcast_to(_lin(np.array(NEUTRAL, np.float64)), gx.shape + (3,))
        else:
            u = l0 * UV[i0, 0] + l1 * UV[i1, 0] + l2 * UV[i2, 0]
            v = l0 * UV[i0, 1] + l1 * UV[i1, 1] + l2 * UV[i2, 1]
            wt = layered.get(int(M[k]))
            if wt is not None:
                bw = l0[..., None] * wt[i0] + l1[..., None] * wt[i1] + l2[..., None] * wt[i2]
                col = sum(bw[..., n:n + 1] * _sample(t[n], u, v) for n in range(len(t)))
            else:
                col = _sample(t, u, v)
            al = None if wt is not None else alpha.get(int(M[k]))
            if al is not None:
                w = w & (_sample(al, u, v) >= 128)
        sub[w] = z[w]
        img[y0:y1 + 1, x0:x1 + 1][w] = _srgb(col * shade[k])[w]
    return Image.fromarray(np.clip(np.rint(img), 0, 255).astype(np.uint8), 'RGB')
