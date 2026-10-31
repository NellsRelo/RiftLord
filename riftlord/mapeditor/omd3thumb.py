"""Render a small textured preview of an OMD3 mesh, without a GPU.

    python3 -m riftlord.mapeditor.omd3thumb --mesh RailingShortC --out rail.png
    python3 -m riftlord.mapeditor.omd3thumb --build     # every mesh, cached to previews/omd3

The selection sidebar has to show a person what they are about to place, and a
list of 967 names does not. Loading every mesh into the browser to render a
thumbnail would, so the thumbnails are made once, here, and served as PNGs.

There is no GL in this process, so it is a small software rasteriser: project the
triangles down a fixed three-quarter orthographic view, keep the nearest with a depth
buffer, colour each pixel from its section's base-colour texture at its UV0 (resolved as
the Map Editor viewport resolves it: `surfaces`), and shade it by how much its face faces
the light. A section with no texture takes its material's constant colour, else `tint`.

A model here is a served mesh (omd3serve.mesh, kitactors.mesh) or a `merge` of `place`d
meshes: {'verts', 'tris', 'uvs', 'faces' (surface index per triangle), 'surfaces'}.
"""

import argparse
import functools
import math
import os
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.formats.omd3 import meshcat  # noqa: E402

PREVIEWS = os.path.join(gamepath.scratch('previews', gamepath.CODE_ROOT), 'omd3')
#: The drawing's version, in every preview's cache name (omd3serve, kitdonors, kitactors,
#: compounds, crystal) and in the Map Editor's preview URLs (main.js THUMB_STYLE).
STYLE = 'tex1'

#: A three-quarter view, the angle a kit piece is usually drawn at.
VIEW_YAW = math.radians(35.0)
VIEW_PITCH = math.radians(28.0)

#: The Map Editor viewport's default light rig (main.js `fill` and `sun`): a hemisphere light
#: (sky and ground colours, intensity) and a sun (intensity, scene position; the scene is
#: Unreal's axes swizzled to (x, z, -y)).
HEMI_SKY = 0xbcd2ff
HEMI_GROUND = 0x202430
HEMI_INTENSITY = 2.2
SUN_INTENSITY = 1.6
SUN_SCENE = (8000.0, 14000.0, 10000.0)

#: Render this many times larger and shrink. A kit piece can be 15,000 triangles
#: and a thumbnail 96 pixels, so most triangles land inside a single pixel; drawn
#: at 1:1 they come out as a spray of dots that reads as broken geometry. Drawing
#: bigger gives them room, and the downsample does the anti-aliasing for free.
SUPERSAMPLE = 3

#: Most candidate pixels a raster pass tests at once (memory per chunk is ~100 bytes each).
FRAGMENT_CHUNK = 1 << 21

#: Colour of a section with neither a texture nor a material colour.
TINT = (196, 170, 128)
BACKGROUND = (27, 31, 39)
#: Exponent from a linear colour multiplier to the sRGB one (material tints are linear).
GAMMA = 2.2
#: Mip edge bounds: the smallest mip whose edge gives a texel per drawn pixel, within these.
TEXTURE_SIDE_MIN = 16
TEXTURE_SIDE_MAX = 512
#: Decoded mips kept in memory.
TEXTURE_CACHE = 128
#: Share of a ghost surface's colour shown where other geometry hides it (review sheets).
GHOST_MIX = 0.6


# -- materials ---------------------------------------------------------------------------

def _tint(material):
    """[r, g, b] linear base-colour multiplier of a material (leveldata.material_tint), or None."""
    if not material:
        return None
    try:
        from riftlord.mapeditor import leveldata
        return leveldata.material_tint(material)
    except Exception:                                # noqa: BLE001
        return None


def _material_texture(material):
    try:
        from riftlord.mapeditor import omd3serve
        return omd3serve.material_texture(material)
    except Exception:                                # noqa: BLE001
        return None


def surfaces(mesh, material=None):
    """(surface index per triangle, [surface]) of a served mesh as the Map Editor viewport
    colours it (main.js, the level's pieces and partMesh): an override `material` paints the
    whole mesh with its texture (else the mesh's own) and its tint; a Fab or kit mesh, or a
    mesh of several sections with a slot texture, gives each section its slot's texture and
    tint; otherwise the whole mesh takes the mesh's texture and its material's tint.
    A surface is {'texture': path or None, 'tint': [r, g, b] or None}."""
    count = len(mesh.get('tris') or []) // 3
    whole = [0] * count
    if material:
        return whole, [{'texture': _material_texture(material) or mesh.get('texture'),
                        'tint': _tint(material)}]
    fab = bool(mesh.get('fab'))
    secs = mesh.get('sections') or []
    slot_tex = mesh.get('slotTextures') or []
    if secs and (fab or (len(secs) > 1 and any(slot_tex))):
        slots = mesh.get('slots') or []
        tints = mesh.get('slotTints')
        n = max([len(slot_tex)] + [int(s.get('slot') or 0) + 1 for s in secs])
        out = [{'texture': slot_tex[k] if k < len(slot_tex) else None,
                'tint': (tints[k] if tints and k < len(tints) else
                         None if fab or k >= len(slots) else _tint(slots[k]))}
               for k in range(n)]
        faces = list(whole)
        for s in secs:
            first = int(s.get('first') or 0) // 3
            for i in range(first, min(count, first + int(s.get('tris') or 0))):
                faces[i] = int(s.get('slot') or 0)
        return faces, out
    tint = mesh.get('tint') if 'tint' in mesh else (None if fab else _tint(mesh.get('material')))
    return whole, [{'texture': mesh.get('texture'), 'tint': tint}]


def place(mesh, matrix=None, material=None, texture=None, tint=None, mix=None, ghost=False):
    """One mesh (a served mesh or a model) for a preview, as numpy arrays: its vertices moved
    by `matrix` (3x4 or 4x4 rows), UV0, and its surfaces (`surfaces`, an override `material`
    or `texture` on every one). `tint` multiplies every surface (linear [r, g, b]); `mix`
    ([r, g, b], share) blends every surface's colour toward that colour; `ghost` shows it
    through what hides it (review sheets). None when it has no triangles."""
    import numpy as np
    verts, tris = mesh.get('verts'), mesh.get('tris')
    if verts is None or tris is None or not len(verts) or not len(tris):
        return None
    v = np.asarray(verts, dtype=np.float64).reshape(-1, 3)
    t = np.asarray(tris, dtype=np.int64)
    t = t[:len(t) - len(t) % 3].reshape(-1, 3)
    if 'faces' in mesh and 'surfaces' in mesh and not material:
        faces, surfs = mesh['faces'], [dict(s) for s in mesh['surfaces']]
    else:
        faces, surfs = surfaces(mesh, material)
    f = np.asarray(faces, dtype=np.int64)[:len(t)]
    if len(f) < len(t):
        f = np.concatenate([f, np.zeros(len(t) - len(f), np.int64)])
    uvs = mesh.get('uvs')
    uv = np.asarray(uvs, dtype=np.float64).reshape(-1, 2) if uvs is not None and \
        len(uvs) == len(v) else None
    if uv is None:
        surfs = [dict(s, texture=None) for s in surfs]
        uv = np.zeros((len(v), 2))
    for s in surfs:
        if texture:
            s['texture'] = texture
        if tint is not None:
            s['tint'] = [a * b for a, b in zip(s.get('tint') or (1.0, 1.0, 1.0), tint)]
        if mix:
            s['mix'] = (tuple(mix[0]), float(mix[1]))
        if ghost:
            s['ghost'] = True
    if matrix is not None:
        m = np.asarray(matrix, dtype=np.float64)
        v = v @ m[:3, :3].T + m[:3, 3]
    keep = (t >= 0).all(axis=1) & (t.max(axis=1) < len(v))
    return {'v': v, 't': t[keep], 'uv': uv, 'f': f[keep], 's': surfs}


def _surface_key(s):
    return (s.get('texture'), tuple(s['tint']) if s.get('tint') else None,
            s.get('mix'), bool(s.get('ghost')))


def merge(parts):
    """A model of `place`d parts (None ones skipped), lists like a served mesh's, its equal
    surfaces shared: {'verts', 'tris', 'uvs', 'faces', 'surfaces'}; None when nothing draws."""
    import numpy as np
    parts = [p for p in parts if p is not None and len(p['t'])]
    if not parts:
        return None
    surfs, index = [], {}
    verts, tris, uvs, faces = [], [], [], []
    base = 0
    for p in parts:
        remap = []
        for s in p['s']:
            key = _surface_key(s)
            if key not in index:
                index[key] = len(surfs)
                surfs.append(s)
            remap.append(index[key])
        verts.append(p['v'])
        uvs.append(p['uv'])
        tris.append(p['t'] + base)
        faces.append(np.asarray(remap, dtype=np.int64)[p['f']])
        base += len(p['v'])
    return {'verts': np.concatenate(verts).tolist(),
            'tris': np.concatenate(tris).reshape(-1).tolist(),
            'uvs': np.concatenate(uvs).tolist(),
            'faces': np.concatenate(faces).tolist(), 'surfaces': surfs}


# -- textures ----------------------------------------------------------------------------

@functools.lru_cache(maxsize=TEXTURE_CACHE)
def texture_rgb(path, side):
    """(H, W, 3) uint8 sRGB of texture `path` (`/Game/...`, or `/kit/...` and `/fab/...` as
    /api/texture serves them) at the smallest mip whose longest edge is `side` or more (the
    largest there is when none is), or None when it does not read."""
    import io
    import numpy as np
    from PIL import Image
    try:
        if str(path).startswith('/kit/'):
            from riftlord.mapeditor import kitactors
            png = kitactors.texture_png(path, side)
            image = Image.open(io.BytesIO(png)) if png else None
        elif str(path).startswith('/fab/'):
            from riftlord.mapeditor import fablibrary
            png = fablibrary.texture_png(path, side)
            image = Image.open(io.BytesIO(png)) if png else None
        else:
            image = _game_texture(path, side)
    except Exception:                                # noqa: BLE001
        image = None
    if image is None:
        return None
    return np.asarray(image.convert('RGB'), dtype=np.uint8)


def _game_texture(path, side):
    """A shipped texture's mip as texture.parse picks it for `side`: the inline mips first,
    the `.ubulk` sidecar only when none of them is big enough. PIL image or None."""
    import tempfile
    from riftlord.core import gamefiles
    from riftlord.formats.ue import texture
    stem = str(path).replace('/Game/', '../../../OMD/Content/', 1)
    with tempfile.TemporaryDirectory(prefix='omd3thumb-') as tmp:
        staged = {}
        for ext in ('.uasset', '.uexp'):
            staged[ext] = os.path.join(tmp, 't' + ext)
            with open(staged[ext], 'wb') as fh:
                fh.write(gamefiles.read(stem + ext))
        try:
            info = texture.parse(staged['.uasset'], staged['.uexp'], want=side)
        except ValueError:
            info = None
        if info is None or max(info['width'], info['height']) < side:
            try:
                bulk = gamefiles.read(stem + '.ubulk')
            except Exception:                        # noqa: BLE001
                bulk = None
            if bulk:
                staged['.ubulk'] = os.path.join(tmp, 't.ubulk')
                with open(staged['.ubulk'], 'wb') as fh:
                    fh.write(bulk)
                info = texture.parse(staged['.uasset'], staged['.uexp'], staged['.ubulk'],
                                     want=side)
        return texture.to_image(info) if info else None


def _luminance(rgb_hex):
    """Linear luminance of an sRGB colour given as 0xRRGGBB."""
    c = [((rgb_hex >> s) & 255) / 255.0 for s in (16, 8, 0)]
    c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _shade(normal, toward):
    """sRGB multiplier per face: the viewport rig's linear light on a face whose world normal
    is `normal` (n, 3), turned to face the viewer when `toward` (n,) is False as a
    double-sided material is lit, over the most light any face gets (so none clips)."""
    import numpy as np
    n = np.where(toward[:, None], normal, -normal)
    n = n / np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
    sky = _luminance(HEMI_SKY) * HEMI_INTENSITY
    ground = _luminance(HEMI_GROUND) * HEMI_INTENSITY
    x, y, z = SUN_SCENE
    sun = np.asarray((x, -z, y)) / math.sqrt(x * x + y * y + z * z)
    up = np.asarray((0.0, 0.0, 0.5 * (sky - ground)))
    # light = up . n + sun-term + the rest; its largest value is at n along up + SUN * sun
    most = np.linalg.norm(up + SUN_INTENSITY * sun) + 0.5 * (sky + ground)
    light = n @ up + 0.5 * (sky + ground) + SUN_INTENSITY * np.maximum(0.0, n @ sun)
    return (light / most) ** (1.0 / GAMMA)


def _side(texels):
    """The mip edge for `texels` texels across: the next power of two, within the bounds."""
    if not texels > 0:
        return TEXTURE_SIDE_MIN
    return int(min(TEXTURE_SIDE_MAX, max(TEXTURE_SIDE_MIN, 2 ** math.ceil(math.log2(texels)))))


# -- drawing -----------------------------------------------------------------------------

def _arrays(model):
    """(v, t, uv, faces, surfaces) of a model, a served mesh or a {'verts', 'tris'} one."""
    if 'v' in model and 't' in model:
        p = model
    else:
        p = place(model)
        if p is None:
            return None
    return p['v'], p['t'], p['uv'], p['f'], p['s'] or [{'texture': None, 'tint': None}]


def render(model, size=96, background=BACKGROUND, tint=TINT, yaw=VIEW_YAW, pitch=VIEW_PITCH,
           box=None, supersample=SUPERSAMPLE):
    """A textured preview of one model (a served mesh, a `merge`, or plain {'verts', 'tris'},
    drawn in `tint`) as a PIL image, `size` square; None when it has no triangles. `box`
    ((lo, hi) world corners) frames the view instead of the model's own extent."""
    from PIL import Image
    got = _arrays(model)
    if got is None:
        return None
    big = _draw(*got, size * supersample, background, tint, yaw, pitch, box)
    return big.resize((size, size), Image.LANCZOS)


def _edges(sx, sy, t):
    """Per triangle: its signed screen area and the (x, y, constant) coefficients of its
    edge functions f0 (a to b), f1 (b to c) and f2 (c to a), each positive inside for
    either winding once multiplied by the area's sign."""
    a, b, c = t[:, 0], t[:, 1], t[:, 2]
    ax, ay, bx, by, cx, cy = sx[a], sy[a], sx[b], sy[b], sx[c], sy[c]
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    out = []
    for (px, py), (qx, qy) in (((ax, ay), (bx, by)), ((bx, by), (cx, cy)),
                               ((cx, cy), (ax, ay))):
        out.append((py - qy, qx - px, (qy - py) * px - (qx - px) * py))
    return area, out


def _raster(sx, sy, z, t, size, sel):
    """(depth, triangle) per pixel of the nearest of triangles `sel` (a mask), the earlier
    triangle on a tie; triangle -1 where none covers it. A pixel is covered when its centre
    is within half a pixel of the triangle (closes seams, keeps sub-pixel triangles); both
    windings draw."""
    import numpy as np
    a, b, c = t[:, 0], t[:, 1], t[:, 2]
    ax, ay, bx, by, cx, cy = sx[a], sy[a], sx[b], sy[b], sx[c], sy[c]
    area, edges = _edges(sx, sy, t)
    keep = sel & (np.abs(area) >= 1e-9)
    lo_x = np.maximum(0, np.trunc(np.minimum(np.minimum(ax, bx), cx)) - 1)
    hi_x = np.minimum(size - 1, np.trunc(np.maximum(np.maximum(ax, bx), cx)) + 1)
    lo_y = np.maximum(0, np.trunc(np.minimum(np.minimum(ay, by), cy)) - 1)
    hi_y = np.minimum(size - 1, np.trunc(np.maximum(np.maximum(ay, by), cy)) + 1)
    keep &= (hi_x >= lo_x) & (hi_y >= lo_y)
    idx = np.nonzero(keep)[0]
    depth = np.full(size * size, 1e30)
    owner = np.full(size * size, -1, dtype=np.int64)
    if not len(idx):
        return depth, owner
    # per kept triangle: edge functions turned positive inside, offset by half a pixel's
    # slack (half the edge's length), and depth as a plane over the screen
    flip = np.where(area[idx] < 0, -1.0, 1.0)
    inv = 1.0 / area[idx]
    lines = []
    for ex, ey, ec in edges:
        ex, ey, ec = ex[idx], ey[idx], ec[idx]
        lines.append((ex * flip, ey * flip, ec * flip + np.hypot(ex, ey) * 0.5 + 1e-9))
    za, zb, zc = z[a[idx]], z[b[idx]], z[c[idx]]
    # z = zb + (zc - zb) w0 + (za - zb) w1, with w0 = f0 / area and w1 = f1 / area
    (e0x, e0y, e0c), (e1x, e1y, e1c) = edges[0], edges[1]
    zx = ((zc - zb) * e0x[idx] + (za - zb) * e1x[idx]) * inv
    zy = ((zc - zb) * e0y[idx] + (za - zb) * e1y[idx]) * inv
    z0 = zb + ((zc - zb) * e0c[idx] + (za - zb) * e1c[idx]) * inv
    x0 = lo_x[idx].astype(np.int64)
    y0 = lo_y[idx].astype(np.int64)
    wide = (hi_x[idx] - lo_x[idx] + 1).astype(np.int64)
    count = wide * (hi_y[idx] - lo_y[idx] + 1).astype(np.int64)
    ends = np.cumsum(count)
    start = 0
    while start < len(idx):
        base = ends[start - 1] if start else 0
        stop = max(start + 1, int(np.searchsorted(ends, base + FRAGMENT_CHUNK, side='right')))
        k = count[start:stop]
        of = np.repeat(np.arange(start, stop, dtype=np.int64), k)
        first = np.cumsum(k) - k
        off = np.arange(int(k.sum()), dtype=np.int64) - np.repeat(first, k)
        w = wide[of]
        px = x0[of] + off % w
        py = y0[of] + off // w
        fx = px + 0.5
        fy = py + 0.5
        inside = np.ones(len(of), dtype=bool)
        for lx, ly, lc in lines:
            inside &= lx[of] * fx + ly[of] * fy + lc[of] >= 0
        of, px, py, fx, fy = of[inside], px[inside], py[inside], fx[inside], fy[inside]
        zz = zx[of] * fx + zy[of] * fy + z0[of]
        pix = py * size + px
        # stable: equal depths keep fragment order, which is triangle order
        order = np.lexsort((zz, pix))
        pix, zz, of = pix[order], zz[order], of[order]
        lead = np.ones(len(pix), dtype=bool)
        lead[1:] = pix[1:] != pix[:-1]
        pix, zz, of = pix[lead], zz[lead], of[lead]
        near = zz < depth[pix]
        at = pix[near]
        depth[at] = zz[near]
        owner[at] = idx[of[near]]
        start = stop
    return depth, owner


def _weights(owner, at, size, sx, sy, t):
    """(w0, w1) of triangles `owner` at pixels `at`: w0 weights a triangle's third vertex,
    w1 its first, at the pixel's centre."""
    area, edges = _edges(sx, sy, t[owner])
    fx = (at % size) + 0.5
    fy = (at // size) + 0.5
    (e0x, e0y, e0c), (e1x, e1y, e1c) = edges[0], edges[1]
    return (e0x * fx + e0y * fy + e0c) / area, (e1x * fx + e1y * fy + e1c) / area


def _colour(tri, w0, w1, t, uv, faces, surfs, shade, sides, tint):
    """(n, 3) uint8 colours of n pixels showing triangles `tri` at barycentrics w0, w1: each
    surface's texture at the interpolated UV (wrapped) times its tint, else its constant
    colour, times the face's light; an emissive (`_EM`) texture is unshaded."""
    import numpy as np
    out = np.empty((len(tri), 3), dtype=np.float64)
    lit = shade[tri].copy()
    which = faces[tri]
    for s in np.unique(which):
        m = which == s
        surf = surfs[s]
        k = np.clip(np.asarray(surf.get('tint') or (1.0, 1.0, 1.0), dtype=np.float64),
                    0.0, None) ** (1.0 / GAMMA)
        path = surf.get('texture')
        tex = texture_rgb(path, sides[s]) if path else None
        if tex is not None:
            tt = t[tri[m]]
            a0, a1 = w0[m], w1[m]
            a2 = 1.0 - a0 - a1
            # orthographic view: screen barycentrics interpolate UVs exactly
            u = uv[tt[:, 0], 0] * a1 + uv[tt[:, 1], 0] * a2 + uv[tt[:, 2], 0] * a0
            v = uv[tt[:, 0], 1] * a1 + uv[tt[:, 1], 1] * a2 + uv[tt[:, 2], 1] * a0
            h, w = tex.shape[:2]
            x = np.minimum(((u - np.floor(u)) * w).astype(np.int64), w - 1)
            y = np.minimum(((v - np.floor(v)) * h).astype(np.int64), h - 1)
            out[m] = tex[y, x] * k
            if str(path).endswith('_EM'):
                lit[m] = 1.0
        elif surf.get('tint'):
            out[m] = 255.0 * k
        else:
            out[m] = tint
        if surf.get('mix'):
            rgb, share = surf['mix']
            out[m] = out[m] * (1.0 - share) + np.asarray(rgb, dtype=np.float64) * share
    return np.minimum(255.0, out * lit[:, None]).astype(np.uint8)


def _draw(v, t, uv, faces, surfs, size, background, tint, yaw, pitch, box):
    """The preview at `size` pixels as a PIL image (render does the downsample)."""
    import numpy as np
    from PIL import Image
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)

    def turn(p):
        x = p[:, 0] * cy - p[:, 1] * sy
        y = p[:, 0] * sy + p[:, 1] * cy
        return np.stack([x, y * sp + p[:, 2] * cp, y * cp - p[:, 2] * sp], axis=1)
    view = turn(v)
    frame = view
    if box is not None:
        lo, hi = box
        frame = turn(np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                               for z in (lo[2], hi[2])], dtype=np.float64))
    span = max(np.ptp(frame[:, 0]), np.ptp(frame[:, 1]), 1e-6)
    scale = (size - 8) / span
    ox = (size - (frame[:, 0].max() + frame[:, 0].min()) * scale) / 2.0
    oy = (size + (frame[:, 1].max() + frame[:, 1].min()) * scale) / 2.0
    sx = view[:, 0] * scale + ox
    syy = oy - view[:, 1] * scale
    out = np.empty((size * size, 3), dtype=np.uint8)
    out[:] = np.asarray(background, dtype=np.uint8)
    if not len(t):
        return Image.fromarray(out.reshape(size, size, 3), 'RGB')
    a, b, c = t[:, 0], t[:, 1], t[:, 2]
    n = np.cross(v[b] - v[a], v[c] - v[a])
    shade = _shade(n, np.cross(view[b] - view[a], view[c] - view[a])[:, 2] >= 0)
    # mip per surface: a texel per drawn pixel, from its triangles' screen and UV areas
    screen = np.abs((sx[b] - sx[a]) * (syy[c] - syy[a]) - (syy[b] - syy[a]) * (sx[c] - sx[a]))
    texel = np.abs((uv[b, 0] - uv[a, 0]) * (uv[c, 1] - uv[a, 1])
                   - (uv[b, 1] - uv[a, 1]) * (uv[c, 0] - uv[a, 0]))
    on = np.bincount(faces, weights=screen, minlength=len(surfs))
    tex_area = np.bincount(faces, weights=texel, minlength=len(surfs))
    sides = [_side(math.sqrt(on[k] / tex_area[k]) if tex_area[k] > 0 else 0)
             for k in range(len(surfs))]
    everything = np.ones(len(t), dtype=bool)
    depth, owner = _raster(sx, syy, view[:, 2], t, size, everything)
    at = np.nonzero(owner >= 0)[0]
    w0, w1 = _weights(owner[at], at, size, sx, syy, t)
    out[at] = _colour(owner[at], w0, w1, t, uv, faces, surfs, shade, sides, tint)
    ghost = np.array([bool(s.get('ghost')) for s in surfs])[faces]
    if ghost.any() and not ghost.all():
        d2, o2 = _raster(sx, syy, view[:, 2], t, size, ghost)
        at = np.nonzero((o2 >= 0) & (d2 > depth + 1e-3))[0]
        w0, w1 = _weights(o2[at], at, size, sx, syy, t)
        seen = _colour(o2[at], w0, w1, t, uv, faces, surfs, shade, sides, tint)
        out[at] = (GHOST_MIX * seen + (1 - GHOST_MIX) * out[at]).astype(np.uint8)
    return Image.fromarray(out.reshape(size, size, 3), 'RGB')


def build(out_dir=PREVIEWS, size=96, limit=None):
    """Render every mesh the catalogue can read."""
    from riftlord.mapeditor import omd3serve
    os.makedirs(out_dir, exist_ok=True)
    names = sorted(meshcat.load())
    if limit:
        names = names[:limit]
    made = skipped = 0
    for name in names:
        target = os.path.join(out_dir, omd3serve.preview_name(name))
        if os.path.exists(target):
            skipped += 1
            continue
        model = omd3serve.mesh(name)
        if not model:
            continue
        image = render(model, size)
        if image is None:
            continue
        image.save(target)
        made += 1
    return made, skipped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--mesh')
    ap.add_argument('--out')
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--size', type=int, default=96)
    ap.add_argument('--limit', type=int)
    args = ap.parse_args(argv)

    if args.mesh:
        from riftlord.mapeditor import omd3serve
        model = omd3serve.mesh(args.mesh)
        if not model:
            print('%s: could not read' % args.mesh)
            return 1
        image = render(model, args.size)
        out = args.out or ('%s.png' % args.mesh)
        image.save(out)
        print('%s -> %s (%d triangles)' % (args.mesh, out, len(model['tris']) // 3))
        return 0

    if args.build:
        made, skipped = build(size=args.size, limit=args.limit)
        print('rendered %d preview(s), %d already there -> %s'
              % (made, skipped, PREVIEWS))
        return 0

    ap.error('need --mesh or --build')


if __name__ == '__main__':
    raise SystemExit(main())
