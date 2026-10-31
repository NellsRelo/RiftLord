"""BC1 (DXT1), BC3 (DXT5) and BC5 (two-channel normal map) encoding, all blocks at once with numpy.

Each 4x4 block's colours are fitted along their principal axis, weighted by
alpha (a transparent pixel's colour is never shown, so it must not pull the
block's colours: Pillow's encoder lets it, which strews wrong-coloured specks
along every soft edge), then refined by least squares over the chosen
indices.

    data = bcn.encode(image, 'PF_DXT5')     # image: RGBA, sides multiples of 4
"""
import numpy as np

REFINE = 3


def blocks(a):
    """(H, W, C) as (N, 16, C), row by row of 4x4 blocks."""
    h, w, c = a.shape
    return a.reshape(h // 4, 4, w // 4, 4, c).transpose(0, 2, 1, 3, 4).reshape(-1, 16, c)


def to565(c):
    """Colours (N, 3, 0-255) as 5:6:5 ints, and back as the colours they make."""
    r = np.clip(np.round(c[:, 0] * 31 / 255), 0, 31).astype(np.int64)
    g = np.clip(np.round(c[:, 1] * 63 / 255), 0, 63).astype(np.int64)
    b = np.clip(np.round(c[:, 2] * 31 / 255), 0, 31).astype(np.int64)
    packed = (r << 11) | (g << 5) | b
    back = np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], 1).astype(np.float64)
    return packed, back


def palette(e0, e1):
    """The four colours a block's endpoints give (BC3 and 4-colour BC1)."""
    return np.stack([e0, e1, (2 * e0 + e1) / 3, (e0 + 2 * e1) / 3], 1)


#: Each index's share of endpoint 0 (index 0: e0, 1: e1, 2: 2/3, 3: 1/3).
SHARE = np.array([1.0, 0.0, 2 / 3, 1 / 3])


def colour_blocks(px, weight):
    """BC1-style colour halves: (packed e0, packed e1, (N, 16) indices)."""
    w = weight + 1e-4
    mean = (px * w[..., None]).sum(1) / w.sum(1)[:, None]
    d = px - mean[:, None, :]
    cov = np.einsum('nki,nkj,nk->nij', d, d, w)
    axis = np.ones((len(px), 3)) / np.sqrt(3)
    for _ in range(8):
        axis = np.einsum('nij,nj->ni', cov, axis)
        axis /= np.linalg.norm(axis, axis=1, keepdims=True) + 1e-9
    t = np.einsum('nki,ni->nk', d, axis)
    big = np.where(weight > 0, t, -1e9).max(1)
    small = np.where(weight > 0, t, 1e9).min(1)
    flat = big < small
    big[flat] = 0
    small[flat] = 0
    e0 = np.clip(mean + axis * big[:, None], 0, 255)
    e1 = np.clip(mean + axis * small[:, None], 0, 255)
    for step in range(REFINE + 1):
        p0, q0 = to565(e0)
        p1, q1 = to565(e1)
        pal = palette(q0, q1)
        dist = ((px[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)
        idx = dist.argmin(2)
        if step == REFINE:
            break
        # least squares: px ~ s*e0 + (1-s)*e1, weighted
        s = SHARE[idx]
        a = (w * s * s).sum(1)
        b = (w * s * (1 - s)).sum(1)
        c = (w * (1 - s) * (1 - s)).sum(1)
        x0 = (w[..., None] * s[..., None] * px).sum(1)
        x1 = (w[..., None] * (1 - s)[..., None] * px).sum(1)
        det = a * c - b * b
        ok = np.abs(det) > 1e-6
        n0 = np.where(ok[:, None], (c[:, None] * x0 - b[:, None] * x1) / np.where(ok, det, 1)[:, None], e0)
        n1 = np.where(ok[:, None], (a[:, None] * x1 - b[:, None] * x0) / np.where(ok, det, 1)[:, None], e1)
        e0 = np.clip(n0, 0, 255)
        e1 = np.clip(n1, 0, 255)
    return p0, p1, idx


def bc1_bytes(p0, p1, idx, four_colour):
    """Colour blocks as bytes. In BC1, four-colour mode needs e0 > e1: where it
    is not, the endpoints swap and the indices follow."""
    p0 = p0.copy()
    p1 = p1.copy()
    idx = idx.copy()
    if four_colour:
        swap = p0 < p1
        p0[swap], p1[swap] = p1[swap], p0[swap].copy()
        flip = np.array([1, 0, 3, 2])
        idx[swap] = flip[idx[swap]]
        same = p0 == p1
        idx[same] = 0
    bits = (idx.astype(np.uint64) << (2 * np.arange(16, dtype=np.uint64))).sum(1)
    out = np.zeros((len(p0), 8), np.uint8)
    out[:, 0:2] = p0.astype('<u2').view(np.uint8).reshape(-1, 2)
    out[:, 2:4] = p1.astype('<u2').view(np.uint8).reshape(-1, 2)
    out[:, 4:8] = bits.astype('<u4').view(np.uint8).reshape(-1, 4)
    return out


def alpha_blocks(al):
    """BC3 alpha halves: a0 = most, a1 = least, eight-step mode."""
    a0 = al.max(1)
    a1 = al.min(1)
    steps = np.stack([a0, a1] + [((7 - k) * a0 + k * a1) / 7 for k in range(1, 7)], 1)
    idx = np.abs(al[:, :, None] - steps[:, None, :]).argmin(2)
    idx[a0 == a1] = 0
    bits = (idx.astype(np.uint64) << (3 * np.arange(16, dtype=np.uint64))).sum(1)
    out = np.zeros((len(al), 8), np.uint8)
    out[:, 0] = np.round(a0).astype(np.uint8)
    out[:, 1] = np.round(a1).astype(np.uint8)
    b = bits.astype('<u8').view(np.uint8).reshape(-1, 8)
    out[:, 2:8] = b[:, 0:6]
    return out


def encode(image, fmt, kind='texture'):
    """BC bytes of an image; `kind` is its texture class (ispctex.chosen)."""
    from riftlord.formats.ue import ispctex
    if ispctex.chosen(fmt, kind):
        return ispctex.encode(np.asarray(image.convert('RGBA')), fmt)
    a = np.asarray(image.convert('RGBA')).astype(np.float64)
    h, w = a.shape[:2]
    if h % 4 or w % 4:
        ph, pw = (h + 3) // 4 * 4, (w + 3) // 4 * 4
        a = np.pad(a, ((0, ph - h), (0, pw - w), (0, 0)), mode='edge')
    b = blocks(a)
    px, al = b[..., :3], b[..., 3]
    if fmt == 'PF_DXT5':
        p0, p1, idx = colour_blocks(px, al / 255.0)
        return np.concatenate([alpha_blocks(al), bc1_bytes(p0, p1, idx, False)], 1).tobytes()
    if fmt == 'PF_BC5':
        # two BC4 channels (red, then green), each as BC3's alpha
        return np.concatenate([alpha_blocks(b[..., 0]), alpha_blocks(b[..., 1])], 1).tobytes()
    if fmt == 'PF_DXT1':
        p0, p1, idx = colour_blocks(px, np.ones(al.shape))
        return bc1_bytes(p0, p1, idx, True).tobytes()
    raise ValueError(fmt)
