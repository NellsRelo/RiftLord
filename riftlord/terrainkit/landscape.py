"""Landscape ground for terrain tiles: a Landscape actor's heights as a world-space height grid
around the map's routes, sampled like a ground mesh's raster (tiles.raster's shape).

Heights come from each LandscapeComponent's heightmap texture (16-bit, R high byte, G low),
read per subsection: a component of NumSubsections x SubsectionSizeQuads quads stores each
subsection's (SubsectionSizeQuads + 1) texels, its edge texel repeated in the next subsection
(UE's GenerateHeightmapMips layout; collision equals these heights texel for texel on
single-subsection landscapes, survey 2026-10-28).
"""

import math
import struct

import numpy as np

#: Unreal's landscape height scale: local z = (height - MID) * ZSCALE.
ZSCALE = 1.0 / 128.0
MID = 32768
#: Window search area: the map's route points and spawners, widened by this (uu).
ROUTE_MARGIN = 3000.0


def _props(info, uexp, hdr, i):
    from riftlord.onslaught import onslaught
    return dict((p[0], p) for p in onslaught.read_props(info, uexp, hdr, info['exports'][i]))


def heights(package, actor_export):
    """{'grid' (ny, nx) float local z, 'origin' (qx, qy) of grid[0, 0] in quads}, or None."""
    from riftlord.formats.omd3 import omd3landscape as L
    from riftlord.formats.omd3 import omd3level
    from riftlord.onslaught import onslaught
    info, uexp, hdr = omd3level._load(package)
    ubulk = None
    comps = []
    for j, e in enumerate(info['exports']):
        if str(e.get('class') or e.get('cls') or '') != 'LandscapeComponent' or e.get('outer') != actor_export:
            continue
        p = _props(info, uexp, hdr, j)
        size = onslaught.as_int(p['ComponentSizeQuads'][2])
        subq = onslaught.as_int(p['SubsectionSizeQuads'][2]) if 'SubsectionSizeQuads' in p else size
        nsub = onslaught.as_int(p['NumSubsections'][2]) if 'NumSubsections' in p else 1
        tex = onslaught.as_int(p['HeightmapTexture'][2])
        bias = struct.unpack('<4f', p['HeightmapScaleBias'][2]) if 'HeightmapScaleBias' in p else (0, 0, 0, 0)
        loc = onslaught.as_vec(p['RelativeLocation'][2]) if 'RelativeLocation' in p else (0.0, 0.0, 0.0)
        comps.append((size, subq, nsub, tex - 1, bias, loc))
    if not comps:
        return None
    texcache = {}
    qx0 = int(round(min(c[5][0] for c in comps)))
    qy0 = int(round(min(c[5][1] for c in comps)))
    qx1 = int(round(max(c[5][0] + c[0] for c in comps)))
    qy1 = int(round(max(c[5][1] + c[0] for c in comps)))
    grid = np.full((qy1 - qy0 + 1, qx1 - qx0 + 1), np.nan, np.float32)
    for size, subq, nsub, t, bias, loc in comps:
        if t not in texcache:
            hm = L._heightmap(info, uexp, hdr, t)
            want = int(round(1.0 / bias[0])) if bias[0] else 0
            if hm is None or hm[0] < want:
                if ubulk is None:
                    ubulk = L._ubulk(package)
                hm = L._heightmap(info, uexp, hdr, t, ubulk)
            texcache[t] = hm
        hm = texcache[t]
        if hm is None:
            continue
        tw, th, data = hm
        img = np.frombuffer(data, np.uint8).reshape(th, tw, 4)
        h = (img[..., 2].astype(np.int32) << 8) | img[..., 1]
        ox, oy = int(round(bias[2] * tw)), int(round(bias[3] * th))
        q = np.arange(size + 1)
        sub = np.minimum(q // subq, nsub - 1)
        tex_i = sub * (subq + 1) + (q - sub * subq)
        if oy + tex_i.max() >= th or ox + tex_i.max() >= tw:
            continue
        block = h[np.ix_(oy + tex_i, ox + tex_i)]
        gx, gy = int(round(loc[0])) - qx0, int(round(loc[1])) - qy0
        grid[gy:gy + size + 1, gx:gx + size + 1] = (block - MID) * ZSCALE
    return {'grid': grid, 'origin': (qx0, qy0)}


def route_box(level):
    """(xmin, ymin, xmax, ymax) world box of the map's route points and spawners, widened by
    ROUTE_MARGIN, or None."""
    from riftlord.onslaught import onslaught
    try:
        r = onslaught.map_report(level)
    except (Exception, SystemExit):                  # noqa: BLE001
        return None
    pts = [v['location'] for v in (r.get('points') or {}).values() if v.get('location')]
    pts += [s['location'] for s in r.get('spawners') or [] if s.get('location')]
    if not pts:
        return None
    a = np.array(pts, np.float64)
    return (a[:, 0].min() - ROUTE_MARGIN, a[:, 1].min() - ROUTE_MARGIN,
            a[:, 0].max() + ROUTE_MARGIN, a[:, 1].max() + ROUTE_MARGIN)


def raster(source, step):
    """tiles.raster's {origin, z, rgba (None)} of a landscape source over its route box."""
    got = heights(source['package'], source['export'])
    mt = np.array(source['matrix'], np.float64).reshape(4, 4).T
    sx, sy, sz = mt[0, 0], mt[1, 1], mt[2, 2]
    tx, ty, tz = mt[:3, 3]
    box = source.get('box')
    grid, (qx0, qy0) = got['grid'], got['origin']
    if box is None:
        ny, nx = grid.shape
        box = (tx + qx0 * sx, ty + qy0 * sy, tx + (qx0 + nx - 1) * sx, ty + (qy0 + ny - 1) * sy)
    lo = np.floor(np.array(box[:2]) / step) * step
    hi = np.ceil(np.array(box[2:]) / step) * step
    xs = np.arange(lo[0], hi[0] + step / 2, step)
    ys = np.arange(lo[1], hi[1] + step / 2, step)
    gx, gy = np.meshgrid(xs, ys)
    qx = (gx - tx) / sx - qx0
    qy = (gy - ty) / sy - qy0
    ny, nx = grid.shape
    ok = (qx >= 0) & (qy >= 0) & (qx <= nx - 1) & (qy <= ny - 1)
    x0 = np.clip(np.floor(qx).astype(int), 0, nx - 2)
    y0 = np.clip(np.floor(qy).astype(int), 0, ny - 2)
    fx, fy = np.clip(qx - x0, 0, 1), np.clip(qy - y0, 0, 1)
    z = (grid[y0, x0] * (1 - fx) * (1 - fy) + grid[y0, x0 + 1] * fx * (1 - fy) +
         grid[y0 + 1, x0] * (1 - fx) * fy + grid[y0 + 1, x0 + 1] * fx * fy)
    z = np.where(ok & np.isfinite(z), z * sz + tz, -np.inf)
    return {'origin': lo, 'z': z, 'rgba': None, 'quad': float(sx)}
