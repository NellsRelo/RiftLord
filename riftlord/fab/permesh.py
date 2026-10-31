# Generated from OMD2-Converter/thirdparty/fabconvert/permesh.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Per-mesh material bakes: a material whose textures sit in more than one coordinate frame
(the mesh's UVs and world space) cannot be baked once per material -- the textures in the
other frame would be read as their mean (Medieval Village plaster walls, Eastern Village cliff
moss, City of Brass rocks). Here the material is evaluated at every texel of one mesh slot,
laid out in a UV channel that does not overlap, with the true interpolated inputs (every UV
channel, the world-planar coordinate, vertex colour, surface normal) and no texture averaged.

User decision 2026-10-14: bake per mesh for those materials, at extra texture memory for the
affected meshes only.
"""

import math

import numpy as np

from riftlord.fab import matconvert as MC
from riftlord.fab import matgraph as mg
from riftlord.fab import meshconvert as MS

#: Texels per world unit at the mesh's final scale, and the clamp on the texture side.
TEXELS_PER_UU = 0.5
MIN_PX, MAX_PX = 256, 2048
#: A bake channel may have at most this share of its covered texels claimed by two triangles.
OVERLAP_MAX = 0.02
#: Texels a bake pads outward from the covered area (against mip and filtering seams).
PAD_TEXELS = 8
#: Rasteriser for the overlap test (texels a side).
OVERLAP_PX = 512
#: Evaluate in chunks of this many texels.
CHUNK = 1 << 17
#: Reasons that mark a cross-frame average, the thing this bake removes.
CROSS_FRAME = ('(frame is ', 'long-period texture')


def needs_bake(reasons):
    return any(any(m in r for m in CROSS_FRAME) for r in (reasons or []))


def _raster(uv, size):
    """Texel centres covered by each triangle: (texel flat index, triangle index, l0, l1, l2)."""
    out_i, out_t, out_l = [], [], []
    px = uv * size - 0.5
    for t in range(len(px)):
        (x0, y0), (x1, y1), (x2, y2) = px[t]
        den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(den) < 1e-12:
            continue
        lo = np.floor(px[t].min(0)).astype(int)
        hi = np.ceil(px[t].max(0)).astype(int)
        lo = np.clip(lo, 0, size - 1)
        hi = np.clip(hi, 0, size - 1)
        if hi[0] < lo[0] or hi[1] < lo[1]:
            continue
        xs, ys = np.meshgrid(np.arange(lo[0], hi[0] + 1), np.arange(lo[1], hi[1] + 1))
        X, Y = xs.ravel().astype(np.float64), ys.ravel().astype(np.float64)
        l0 = ((y1 - y2) * (X - x2) + (x2 - x1) * (Y - y2)) / den
        l1 = ((y2 - y0) * (X - x2) + (x0 - x2) * (Y - y2)) / den
        l2 = 1 - l0 - l1
        ins = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
        if not ins.any():
            continue
        out_i.append((Y[ins] * size + X[ins]).astype(np.int64))
        out_t.append(np.full(int(ins.sum()), t, np.int64))
        out_l.append(np.stack([l0[ins], l1[ins], l2[ins]], 1))
    if not out_i:
        return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros((0, 3))
    return np.concatenate(out_i), np.concatenate(out_t), np.concatenate(out_l)


def _overlap(uv):
    idx, _t, _l = _raster(np.clip(uv, 0, 1), OVERLAP_PX)
    if not len(idx):
        return 1.0
    counts = np.bincount(idx, minlength=OVERLAP_PX * OVERLAP_PX)
    covered = (counts > 0).sum()
    return float((counts > 1).sum()) / max(covered, 1)


def choose_channel(c, sel, lightmap_index):
    """The first UV channel (the lightmap's first) inside [0, 1] whose triangles do not overlap."""
    order = []
    if isinstance(lightmap_index, int) and 0 <= lightmap_index < len(c['UV']):
        order.append(lightmap_index)
    order += [k for k in range(len(c['UV'])) if k not in order]
    for k in order:
        uv = c['UV'][k][sel]
        if uv.min() < -1e-3 or uv.max() > 1 + 1e-3:
            continue
        if _overlap(uv) <= OVERLAP_MAX:
            return k
    return None


#: Space left around each chart of a generated layout, in texels at the final size.
CHART_PAD_TEXELS = 4


def make_layout(P):
    """A non-overlapping UV layout for triangles P (T, 3, 3), as the editor's lightmap UVs would
    be (the packs' meshes carry none in their source data): triangles grouped by the axis they
    face, split into connected charts, each projected flat in world units and shelf-packed into
    the unit square with CHART_PAD_TEXELS of padding. Returns (T, 3, 2)."""
    T = len(P)
    fn = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    ax = np.argmax(np.abs(fn), 1)
    sign = np.sign(fn[np.arange(T), ax])
    group = ax * 2 + (sign > 0)
    # connected components within a group, by shared (rounded) vertex positions
    key = np.round(P.reshape(-1, 3), 3)
    _u, vid = np.unique(key, axis=0, return_inverse=True)
    vid = vid.reshape(T, 3)
    parent = np.arange(T)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    first = {}
    for t in range(T):
        for v in vid[t]:
            k = (int(group[t]), int(v))
            if k in first:
                a, b = find(t), find(first[k])
                if a != b:
                    parent[a] = b
            else:
                first[k] = t
    roots = np.array([find(t) for t in range(T)])
    charts = []
    for r in np.unique(roots):
        tris = np.nonzero(roots == r)[0]
        a = int(ax[tris[0]])
        i, j = [(1, 2), (0, 2), (0, 1)][a]
        q = _min_rect(P[tris][..., [i, j]].astype(np.float64))
        charts.append((tris, q, q.reshape(-1, 2).max(0)))
    return _shelf_pack(charts, T)


#: Chart rotations tried for the smallest bounding rectangle (degrees, from 0 to under 90).
ROTATE_STEP_DEG = 5
#: Each failed packing grows the square side by this factor.
PACK_GROW = 1.03


def _min_rect(q):
    """Chart points q (n, 3, 2) rotated to the smallest-area axis-aligned rectangle, moved to the origin."""
    flat = q.reshape(-1, 2)
    best = None
    for deg in range(0, 90, ROTATE_STEP_DEG):
        t = math.radians(deg)
        rot = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
        f = flat @ rot
        ext = f.max(0) - f.min(0)
        if best is None or ext[0] * ext[1] < best[0] - 1e-9:
            best = (ext[0] * ext[1], rot)
    f = flat @ best[1]
    f = f - f.min(0)
    if f[:, 1].max() > f[:, 0].max():                  # lie flat: shelves pack wide charts better
        f = f[:, ::-1]
    return f.reshape(q.shape)


def _shelf_pack(charts, T):
    """Shelf-pack charts (tris, q, extent) into the unit square, tallest first, with
    CHART_PAD_TEXELS of padding; the square side starts at the charts' total area and grows by
    PACK_GROW until everything fits."""
    area = sum((max(c[2][0], 1e-3)) * (max(c[2][1], 1e-3)) for c in charts)
    side = max(math.sqrt(area), max(max(c[2][0], c[2][1]) for c in charts))
    order = sorted(charts, key=lambda c: -c[2][1])
    while True:
        pad = CHART_PAD_TEXELS * side / MAX_PX
        x = y = shelf = 0.0
        pos = []
        ok = True
        for tris, q, ext in order:
            w, hgt = ext[0] + 2 * pad, ext[1] + 2 * pad
            if x + w > side:
                x, y, shelf = 0.0, y + shelf, 0.0
            if y + hgt > side or w > side:
                ok = False
                break
            pos.append((tris, q + np.array([x + pad, y + pad])))
            x += w
            shelf = max(shelf, hgt)
        if ok:
            break
        side *= PACK_GROW
    out = np.zeros((T, 3, 2))
    for tris, q in pos:
        out[tris] = q / side
    return out.astype(np.float32)


def size_for(c, sel, uvb, scale):
    P = c['P'][sel] * scale
    a3 = np.linalg.norm(np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]), axis=1).sum() / 2
    u = uvb
    a2 = np.abs((u[:, 1, 0] - u[:, 0, 0]) * (u[:, 2, 1] - u[:, 0, 1])
                - (u[:, 1, 1] - u[:, 0, 1]) * (u[:, 2, 0] - u[:, 0, 0])).sum() / 2
    side = math.sqrt(a3 / max(a2, 1e-9)) * TEXELS_PER_UU
    px = 2 ** int(round(math.log2(max(side, 1))))
    return int(min(MAX_PX, max(MIN_PX, px)))


def _face_frame(P, uv):
    """Per triangle (T, B) of a UV layout (the tangent frame a normal map is read in)."""
    e1, e2 = P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
    d1, d2 = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
    r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(r) < 1e-12, 1e-12, r)
    T = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / r[:, None]
    B = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) / r[:, None]
    T /= np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-12)
    B /= np.maximum(np.linalg.norm(B, axis=1, keepdims=True), 1e-12)
    return T, B


def _pad(img, filled):
    """Grow covered texels outward PAD_TEXELS times (nearest covered neighbour)."""
    img = img.copy()
    mask = filled.copy()
    for _ in range(PAD_TEXELS):
        if mask.all():
            break
        acc = np.zeros_like(img)
        cnt = np.zeros(mask.shape, np.float32)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            sm = np.roll(mask, (dy, dx), (0, 1))
            si = np.roll(img, (dy, dx), (0, 1))
            acc += si * sm[..., None]
            cnt += sm
        grow = (~mask) & (cnt > 0)
        img[grow] = acc[grow] / cnt[grow][:, None]
        mask |= grow
    return img


def bake(ctx, plan, src, slot, uvb, size, overrides=None):
    """({pin: (size, size, C)}, covered mask (size, size)) for one slot of one mesh, laid out in
    `uvb` ((T_slot, 3, 2)); texels outside the slot are zero and unpadded."""
    c = MS.corners(src)
    sel = c['slot'] == slot
    idx, tri, lam = _raster(uvb, size)
    P0 = c['P'][sel]
    fn = MS._unit(np.cross(P0[:, 2] - P0[:, 0], P0[:, 1] - P0[:, 0]))
    wuv = MS.world_planar(P0, fn)

    def interp(arr):
        a = arr[tri]                                       # (n, 3, k)
        return (a * lam[:, :, None]).sum(1).astype(np.float32)
    uvs = [interp(u[sel]) for u in c['UV']]
    zeros2 = np.zeros((len(idx), 2), np.float32)
    uvs += [zeros2] * (MC.MAX_UV - len(uvs))
    N = interp(c['N'][sel])
    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-9)
    # materials see vertex colour as the 8-bit sRGB-encoded value (as the variant bakes do)
    vc = MS.seen_colours(interp(c['C'][sel])).astype(np.float32) if c['C'] is not None else np.ones((len(idx), 4), np.float32)
    W = interp(wuv)
    out = {}
    for s0 in range(0, len(idx), CHUNK):
        s1 = min(len(idx), s0 + CHUNK)
        dom = {'uv': [u[s0:s1] for u in uvs], 'wuv': W[s0:s1], 'vc': vc[s0:s1], 'normal': N[s0:s1]}
        ev = mg.Evaluator(ctx.pack, plan['_src']['graph'], plan['_src']['params'], dom, ctx.tex,
                          sample_override=overrides)
        for k, v in ev.pins().items():
            out.setdefault(k, []).append(v.a)
    pins = {k: np.concatenate(v, 0) for k, v in out.items()}
    # a normal map is read in the UV0 tangent frame; the bake is read in the bake channel's
    if 'Normal' in pins:
        T0, B0 = _face_frame(P0, c['UV'][0][sel])
        T1, B1 = _face_frame(P0, uvb)
        n = pins['Normal'][:, :3].astype(np.float64)
        Nf = fn[tri]
        world = T0[tri] * n[:, :1] + B0[tri] * n[:, 1:2] + Nf * n[:, 2:3]
        pins['Normal'] = np.stack([(world * T1[tri]).sum(1), (world * B1[tri]).sum(1), (world * Nf).sum(1)],
                                  1).astype(np.float32)
    filled = np.zeros(size * size, bool)
    filled[idx] = True
    filled = filled.reshape(size, size)
    grids = {}
    for k, v in pins.items():
        img = np.zeros((size * size, v.shape[1]), np.float32)
        img[idx] = v
        grids[k] = img.reshape(size, size, -1)
    return grids, filled


def pad_image(im, filled):
    """An 8-bit image grown outward from its covered texels (see _pad)."""
    return np.clip(np.round(_pad(im.astype(np.float32), filled)), 0, 255).astype(np.uint8)


def layout(c, sel, lightmap_index, scale):
    """(UVs (T_sel, 3, 2), channel or None, side in px) for the triangles `sel`: a clean UV
    channel or a generated layout, whichever needs the smaller texture (the channel on a tie)."""
    best = None
    ch = choose_channel(c, sel, lightmap_index)
    if ch is not None:
        uv = c['UV'][ch][sel]
        best = (uv, ch, size_for(c, sel, uv, scale))
    gen = make_layout(c['P'][sel] * scale)
    gsize = size_for(c, sel, gen, scale)
    if best is None or gsize < best[2]:
        best = (gen, None, gsize)
    return best


#: A per-mesh bake is kept only where it looks different from the shared bake (user 2026-10-15:
#: "if a per-mesh bake would make no visible difference, there's probably no point"). Both are
#: evaluated over the mesh at TEST_PX: as baked per mesh, and with the shared bake's averaged
#: samples. Two measures, both in 8-bit levels:
#: * broad: base colour averaged over BLOCK_PX squares (fully covered ones); the change less its
#:   median (a uniform shift is not visible: Winter City windows, 4-5 levels, look identical), the
#:   VISIBLE_SHARE quantile of its largest channel. Low-contrast mottling over large areas is
#:   visible (Medieval Village plaster, flat when shared);
#: * fine: per texel, the largest change of base colour or normal map, its VISIBLE_SHARE quantile.
#:   Winter City's world grunge reaches 6 and looks identical.
TEST_PX = 128
BLOCK_PX = 8
VISIBLE_SHARE = 0.9
#: Kept when broad reaches BROAD_LEVELS or fine reaches FINE_LEVELS (measured 2026-10-15 on 80
#: slots each: Winter City broad <= 4.0, fine <= 6, renders identical; Medieval Village plaster
#: broad 7.5-11.3 visibly mottled; fortress stone fine 70-109, its world-projected normal relief).
BROAD_LEVELS = 5.0
FINE_LEVELS = 24.0


def visible(score):
    return score['broad'] >= BROAD_LEVELS or score['fine'] >= FINE_LEVELS


def difference(ctx, plan, src, slot, uvb):
    """({'broad', 'fine'} 8-bit scores, covered texels) of this slot baked per mesh against
    the shared bake's look."""
    p = dict(plan)
    p['size'], p['frame'] = TEST_PX, None
    g1, filled = bake(ctx, p, src, slot, uvb, TEST_PX)
    g0, _f = bake(ctx, p, src, slot, uvb, TEST_PX, overrides=plan.get('_overrides'))
    with np.errstate(all='ignore'):
        i1, i0 = MC.images(p, g1), MC.images(p, g0)
    out = {'broad': 0.0, 'fine': 0.0}
    if not filled.any():
        return out, 0
    worst = np.zeros(int(filled.sum()))
    for kind in ('BC', 'NM'):
        if kind in i1 and kind in i0:
            d = np.abs(i1[kind][filled][:, :3].astype(np.int16) - i0[kind][filled][:, :3].astype(np.int16)).max(1)
            worst = np.maximum(worst, d)
    out['fine'] = float(np.quantile(worst, VISIBLE_SHARE))
    if 'BC' in i1 and 'BC' in i0:
        n = TEST_PX // BLOCK_PX
        blk = filled.reshape(n, BLOCK_PX, n, BLOCK_PX).all((1, 3))
        if blk.any():
            m1 = i1['BC'][..., :3].astype(np.float32).reshape(n, BLOCK_PX, n, BLOCK_PX, 3).mean((1, 3))
            m0 = i0['BC'][..., :3].astype(np.float32).reshape(n, BLOCK_PX, n, BLOCK_PX, 3).mean((1, 3))
            dm = (m1 - m0)[blk]
            # how much the change varies across the surface: a uniform shift is not visible
            out['broad'] = float(np.quantile(np.abs(dm - np.median(dm, 0)).max(1), VISIBLE_SHARE))
    return out, int(filled.sum())
