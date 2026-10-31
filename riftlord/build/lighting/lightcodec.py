"""Encode and decode OMD3 lightmap texels: BC1/BC3 blocks and Lightmass coefficients.

Two layers, both from the UE 4.27 source:

* **Texels.** A light map atlas is a `LightMapTexture2D` twice as tall as its
  allocation space: the top half holds one coefficient, the bottom half the
  next (LightMap.cpp `EncodeCoefficientTexture`, top/bottom atlased). The HQ
  texture (PF_DXT5) holds coefficients 0 and 1, the LQ texture (PF_DXT1)
  coefficients 2 and 3. `decode` / `encode` convert a block-compressed mip to
  and from an RGBA uint8 array; `mips` builds the chain the way the editor
  does (box filter in encoded space, ignoring unmapped texels).
* **Coefficients.** `quantize` is Lightmass's `QuantizeLightSamples`
  (UnrealLightmass/Private/Lighting/LightmapData.cpp): float light samples in,
  bytes plus the per-mesh scale and add vectors out. The shader that reverses
  it is `GetLightMapColorHQ` in LightmapCommon.ush.

A mesh's texels sit in its atlas rectangle: atlas UV = lightmap UV *
CoordinateScale + CoordinateBias, in the half-height space; the editor pads
each allocation by one texel on every side (`PostEncode`, bPadMappings).
"""

import numpy as np

#: Lightmass constants (LightmapData.cpp).
LOG_SCALE = 11.5
LOG_BLACK_POINT = 2.0 ** (-0.5 * LOG_SCALE)
SIMPLE_LOG_SCALE = 16.0
SIMPLE_LOG_BLACK_POINT = 2.0 ** (-0.5 * SIMPLE_LOG_SCALE)
#: The SH constant term Lightmass forces into AddVectors[1].w and [3].w.
SH_CONSTANT = 0.282095
#: Lightmass's luminance weights (GetLUVW).
LUMA = np.array([0.3, 0.59, 0.11])
#: Gamma UVW is stored with. The shader squares (LightmapCommon.ush), so 2.0 inverts
#: it exactly; Lightmass writes 2.2 (LIGHTMASS_UVW_GAMMA), which reads back as x^0.91.
UVW_GAMMA = 2.0
LIGHTMASS_UVW_GAMMA = 2.2
#: Smallest range Lightmass divides by (FMath DELTA).
DELTA = 0.00001
#: Floor on the LQ colour add vector, "bias to avoid divide by zero in shader".
LQ_ADD_FLOOR = 1e-2
#: Blocks encoded at once; bounds the encoder's working memory (~100 MB per chunk).
ENCODE_CHUNK = 1 << 15
#: Texels of padding the editor leaves round each allocation.
PAD = 1


# ---------------------------------------------------------------- BC1 / BC3

def _unpack565(c):
    r = ((c >> 11) & 31).astype(np.float32) * (255.0 / 31)
    g = ((c >> 5) & 63).astype(np.float32) * (255.0 / 63)
    b = (c & 31).astype(np.float32) * (255.0 / 31)
    return np.stack([r, g, b], -1)


def _pack565(rgb):
    q = np.clip(np.rint(rgb), 0, 255)
    r = np.rint(q[..., 0] * 31 / 255).astype(np.uint32)
    g = np.rint(q[..., 1] * 63 / 255).astype(np.uint32)
    b = np.rint(q[..., 2] * 31 / 255).astype(np.uint32)
    return (r << 11) | (g << 5) | b


def _colour_palette(c0, c1, four):
    p0, p1 = _unpack565(c0), _unpack565(c1)
    p2 = np.where(four[:, None], (2 * p0 + p1) / 3, (p0 + p1) / 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1) / 3, 0)
    return np.stack([p0, p1, p2, p3], 1)          # (n, 4, 3)


def _blocks_to_image(blocks, w, h, channels):
    bw, bh = (w + 3) // 4, (h + 3) // 4
    img = blocks.reshape(bh, bw, 4, 4, channels).transpose(0, 2, 1, 3, 4)
    return img.reshape(bh * 4, bw * 4, channels)[:h, :w]


def _image_to_blocks(img):
    h, w, c = img.shape
    ph, pw = max(4, -(-h // 4) * 4), max(4, -(-w // 4) * 4)
    if (ph, pw) != (h, w):
        img = np.pad(img, ((0, ph - h), (0, pw - w), (0, 0)), mode='edge')
    return img.reshape(ph // 4, 4, pw // 4, 4, c).transpose(0, 2, 1, 3, 4).reshape(-1, 16, c)


def decode(fmt, w, h, data):
    """RGBA uint8 (h, w, 4) from one BC1 (PF_DXT1) or BC3 (PF_DXT5) mip."""
    step = {'PF_DXT1': 8, 'PF_DXT5': 16}[fmt]
    raw = np.frombuffer(data, np.uint8).reshape(-1, step)
    n = raw.shape[0]
    colour = raw[:, step - 8:]
    c0 = colour[:, 0].astype(np.uint32) | (colour[:, 1].astype(np.uint32) << 8)
    c1 = colour[:, 2].astype(np.uint32) | (colour[:, 3].astype(np.uint32) << 8)
    bits = colour[:, 4:8].copy().view('<u4')[:, 0]
    four = (c0 > c1) | (fmt == 'PF_DXT5')         # BC3 colour is always 4-colour
    pal = _colour_palette(c0, c1, four)
    idx = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3
    rgb = np.take_along_axis(pal, idx[..., None].astype(np.int64), 1)
    alpha = np.full((n, 16), 255.0, np.float32)
    if fmt == 'PF_DXT1':
        alpha[(idx == 3) & ~four[:, None]] = 0
    else:
        a0 = raw[:, 0].astype(np.float32)
        a1 = raw[:, 1].astype(np.float32)
        abits = np.zeros(n, np.uint64)
        for k in range(6):
            abits |= raw[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
        aidx = (abits[:, None] >> (3 * np.arange(16, dtype=np.uint64))) & np.uint64(7)
        apal = alpha_palette(a0, a1)
        alpha = np.take_along_axis(apal, aidx.astype(np.int64), 1)
    out = np.concatenate([rgb, alpha[..., None]], -1)
    return np.clip(np.rint(_blocks_to_image(out, w, h, 4)), 0, 255).astype(np.uint8)


def alpha_palette(a0, a1):
    """The eight BC3 alpha levels per block, from its two endpoints."""
    eight = a0 > a1
    cols = [a0, a1]
    for k in range(1, 7):
        cols.append(np.where(eight, ((7 - k) * a0 + k * a1) / 7,
                             ((5 - k) * a0 + k * a1) / 5 if k <= 4 else 0))
    pal = np.stack(cols, 1)
    pal[:, 6] = np.where(eight, pal[:, 6], 0)
    pal[:, 7] = np.where(eight, pal[:, 7], 255)
    return pal


def _encode_colour(rgb):
    """BC1 colour blocks (n, 8) uint8, 4-colour mode, from (n, 16, 3) float."""
    n = rgb.shape[0]
    mean = rgb.mean(1, keepdims=True)
    d = rgb - mean
    cov = np.einsum('nki,nkj->nij', d, d)
    axis = np.ones((n, 3)) / np.sqrt(3)
    for _ in range(8):                             # power iteration on the covariance
        axis = np.einsum('nij,nj->ni', cov, axis)
        norm = np.linalg.norm(axis, axis=1, keepdims=True)
        axis = np.where(norm > 1e-9, axis / np.maximum(norm, 1e-9), 1 / np.sqrt(3))
    proj = np.einsum('nki,ni->nk', d, axis)
    lo = np.take_along_axis(rgb, proj.argmin(1)[:, None, None].repeat(3, 2), 1)[:, 0]
    hi = np.take_along_axis(rgb, proj.argmax(1)[:, None, None].repeat(3, 2), 1)[:, 0]
    c0, c1 = _pack565(hi), _pack565(lo)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    four = np.ones(n, bool)
    pal = _colour_palette(c0, c1, four)
    dist = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)
    idx = dist.argmin(-1).astype(np.uint32)
    idx[c0 == c1] = 0                              # equal endpoints: 3-colour mode, use p0
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))).sum(1).astype(np.uint32)
    out = np.zeros((n, 8), np.uint8)
    out[:, 0], out[:, 1] = c0 & 255, c0 >> 8
    out[:, 2], out[:, 3] = c1 & 255, c1 >> 8
    out[:, 4:8] = bits.astype('<u4').view(np.uint8).reshape(n, 4)
    return out


def _encode_alpha(a):
    """BC3 alpha blocks (n, 8) uint8 from (n, 16) float, 8-level mode."""
    n = a.shape[0]
    a0 = np.clip(np.rint(a.max(1)), 0, 255)
    a1 = np.clip(np.rint(a.min(1)), 0, 255)
    pal = alpha_palette(a0, a1)
    idx = np.abs(a[:, :, None] - pal[:, None, :]).argmin(-1).astype(np.uint64)
    idx[a0 == a1] = 0
    bits = (idx << (3 * np.arange(16, dtype=np.uint64))).sum(1)
    out = np.zeros((n, 8), np.uint8)
    out[:, 0], out[:, 1] = a0, a1
    for k in range(6):
        out[:, 2 + k] = (bits >> np.uint64(8 * k)) & np.uint64(255)
    return out


def encode(fmt, img, kind='lightmap'):
    """BC1/BC3 bytes for an RGBA uint8 (h, w, 4) image; `kind` is its texture class."""
    if fmt not in ('PF_DXT1', 'PF_DXT5'):
        raise ValueError('cannot encode %s' % fmt)
    from riftlord.formats.ue import ispctex
    if ispctex.chosen(fmt, kind):
        return ispctex.encode(img, fmt)
    blocks = _image_to_blocks(img)
    out = []
    for at in range(0, blocks.shape[0], ENCODE_CHUNK):
        part = blocks[at:at + ENCODE_CHUNK].astype(np.float64)
        colour = _encode_colour(part[:, :, :3])
        if fmt == 'PF_DXT5':
            colour = np.concatenate([_encode_alpha(part[:, :, 3]), colour], 1)
        out.append(colour.tobytes())
    return b''.join(out)


def mips(top, count, coverage=None):
    """The mip chain from a top mip, as the editor builds it: each level a 2x2 box
    average in encoded space over mapped texels only (GenerateLightmapMipsAndDilateColor).
    `coverage` is a bool (h, w) mask; None treats every texel as mapped."""
    out = [top]
    img = top.astype(np.float64)
    cov = np.ones(top.shape[:2]) if coverage is None else coverage.astype(np.float64)
    for _ in range(count - 1):
        h, w = img.shape[:2]
        nh, nw = max(h // 2, 1), max(w // 2, 1)
        fy, fx = h // nh, w // nw
        weighted = (img * cov[..., None]).reshape(nh, fy, nw, fx, -1).sum((1, 3))
        cnt = cov.reshape(nh, fy, nw, fx).sum((1, 3))
        img = np.where(cnt[..., None] > 0, weighted / np.maximum(cnt, 1)[..., None], 0)
        cov = (cnt > 0).astype(np.float64)
        out.append(np.clip(np.rint(img), 0, 255).astype(np.uint8))
    return out


# --------------------------------------------------------------- coefficients

def _luvw(rgb):
    rgb = np.maximum(rgb, 0)
    lum = rgb @ LUMA
    safe = lum >= 1e-4
    uvw = np.where(safe[..., None], rgb / np.where(safe, lum, 1)[..., None], 1.0)
    return lum, uvw


def quantize(hq_colour, hq_dir, lq_colour=None, lq_dir=None, mapped=None):
    """Lightmass QuantizeLightSamples for one mesh's texels.

    Inputs are float arrays (..., 3): `hq_colour` is Coefficients[0] (incident
    light times the luminance SH's DC term), `hq_dir` Coefficients[1] (the
    directional SH terms Lightmass scales so the DC is SH_CONSTANT). LQ defaults
    to the HQ values. Returns (texels (..., 4, 4) uint8 indexed [coef][rgba],
    scale (4, 4), add (4, 4)) -- scale/add are the FLightMap2D vectors.
    """
    lq_colour = hq_colour if lq_colour is None else lq_colour
    lq_dir = hq_dir if lq_dir is None else lq_dir
    shape = hq_colour.shape[:-1]
    mapped = np.ones(shape, bool) if mapped is None else mapped

    l0, uvw0 = _luvw(hq_colour)
    log_l0 = np.log2(l0 + LOG_BLACK_POINT)
    damp0 = np.clip(l0 * 100, 0, 1)[..., None]
    l2, uvw2 = _luvw(lq_colour)
    log_l2 = np.log2(l2 + SIMPLE_LOG_BLACK_POINT) / SIMPLE_LOG_SCALE + 0.5
    log_rgb2 = log_l2[..., None] * uvw2
    damp2 = np.clip(l2 * 100, 0, 1)[..., None]

    channels = [
        np.concatenate([uvw0, log_l0[..., None]], -1),         # coefficient 0
        np.concatenate([damp0 * hq_dir, np.zeros(shape + (1,))], -1),
        np.concatenate([log_rgb2, np.zeros(shape + (1,))], -1),
        np.concatenate([damp2 * lq_dir, np.zeros(shape + (1,))], -1),
    ]
    lo = np.zeros((4, 4))
    hi = np.zeros((4, 4))
    for k, ch in enumerate(channels):
        if mapped.any():
            lo[k] = ch[mapped].min(0)
            hi[k] = ch[mapped].max(0)
    # coefficient 2's and 3's alpha are never ranged (stored as 255)
    lo[1:, 3] = hi[1:, 3] = 0
    rng = np.maximum(hi - lo, DELTA)
    mul = 1.0 / rng
    addq = -lo / rng
    scale = 1.0 / mul
    add = -addq / mul
    add[2, :3] = np.maximum(add[2, :3], LQ_ADD_FLOOR)
    scale[1, 3], add[1, 3] = 0.0, SH_CONSTANT
    scale[3, 3], add[3, 3] = 0.0, SH_CONSTANT

    tex = np.zeros(shape + (4, 4), np.uint8)

    def byte(v):
        return np.clip(np.rint(v * 255.0), 0, 255).astype(np.uint8)

    u = uvw0 * mul[0, :3] + addq[0, :3]
    log_q = log_l0 * mul[0, 3] + addq[0, 3]
    residual = log_q * 255.0 - np.rint(log_q * 255.0) + 0.5
    tex[..., 0, :3] = byte(np.maximum(u, 0) ** (1.0 / UVW_GAMMA))
    tex[..., 0, 3] = byte(log_q)
    tex[..., 1, :3] = byte(hq_dir * mul[1, :3] + addq[1, :3])
    tex[..., 1, 3] = byte(residual)
    tex[..., 2, :3] = byte(log_rgb2 * mul[2, :3] + addq[2, :3])
    tex[..., 2, 3] = 255
    tex[..., 3, :3] = byte(lq_dir * mul[3, :3] + addq[3, :3])
    tex[..., 3, 3] = 255
    return tex, scale, add


def shade_hq(t0, t1, scale, add, normal=(0.0, 0.0, 1.0)):
    """What GetLightMapColorHQ returns for texels t0/t1 (uint8 rgba) and a normal."""
    t0 = np.asarray(t0, np.float64) / 255.0
    t1 = np.asarray(t1, np.float64) / 255.0
    log_l = t0[..., 3] + t1[..., 3] / 255.0 - 0.5 / 255.0
    log_l = log_l * scale[0][3] + add[0][3]
    uvw = t0[..., :3] ** 2 * np.asarray(scale[0][:3]) + np.asarray(add[0][:3])
    lum = 2.0 ** log_l - 0.01858136
    sh = t1 * np.asarray(scale[1]) + np.asarray(add[1])
    n = np.asarray(normal, np.float64)
    basis = np.array([n[1], n[2], n[0], 1.0])
    directionality = np.maximum(0.0, sh @ basis)
    return (lum * directionality)[..., None] * uvw


def flat_light(colour):
    """(Coefficients[0], Coefficients[1]) for light `colour` arriving evenly, so a
    surface of any normal shows `colour` (directionality is the SH constant alone)."""
    colour = np.asarray(colour, np.float64)
    return colour / SH_CONSTANT, np.zeros_like(colour)


def allocation_rect(coord_scale, coord_bias, size_x, half_y):
    """(x0, y0, x1, y1) texel rectangle of a mesh in its atlas's half-height space,
    padding included, clamped to the atlas."""
    x0 = int(round(coord_bias[0] * size_x)) - PAD
    y0 = int(round(coord_bias[1] * half_y)) - PAD
    x1 = int(round((coord_bias[0] + coord_scale[0]) * size_x)) + PAD
    y1 = int(round((coord_bias[1] + coord_scale[1]) * half_y)) + PAD
    return max(x0, 0), max(y0, 0), min(x1, size_x), min(y1, half_y)
