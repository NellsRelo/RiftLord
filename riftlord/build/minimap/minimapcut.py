"""Minimap mode Cut: the source level's SHIPPED minimap, cut to the mission's play area.

A wall is drawn across every door the mission has, only the floor the rift reaches inside
them is kept, the wall band is closed where the cut leaves floor open, and the frame is
refitted to what is left, turned as the shipped one so it reads the same way up. Made for
a mission played in part of a big level (Beset: Front Lawn's fortress, approved by the
user 2026-10-16). Restored 2026-10-17 from the minimapredraw.py of 2026-10-16
(prepare_cut), unchanged in what it draws.
"""

import collections
import math

#: Width across a door of each class, in uu (3W = three 300 uu cells). A force field's is
#: its DoorMesh plane (100 uu) times its larger scale.
DOOR_WIDTHS = {'BP_Door_Interior_3W_C': 900.0, 'BP_Door_Exterior_3W_C': 900.0,
               'BP_Door_Exterior_3W_SAND_C': 900.0, 'BP_Door_Player_3W_C': 900.0,
               'BP_Door_Exterior_2W_C': 600.0, 'BP_Door_Interior_4W_C': 1200.0}
FORCEFIELD_PLANE = 100.0
#: A door's cut runs this far past each jamb, in uu, so it meets the walls either side.
CUT_OVERLAP = 150.0
#: A shipped minimap pixel is drawn when its alpha is over this, and floor when its
#: colour is under CUT_DARK (walls are white, floor black).
CUT_DRAWN = 0
CUT_DARK = 128


def _grow(mask, steps):
    """`mask` (bool array) dilated by `steps` pixels (an octagon)."""
    out = mask.copy()
    for k in range(int(steps)):
        m = out.copy()
        m[1:, :] |= out[:-1, :]
        m[:-1, :] |= out[1:, :]
        m[:, 1:] |= out[:, :-1]
        m[:, :-1] |= out[:, 1:]
        if k % 2:
            m[1:, 1:] |= out[:-1, :-1]
            m[:-1, :-1] |= out[1:, 1:]
            m[1:, :-1] |= out[:-1, 1:]
            m[:-1, 1:] |= out[1:, :-1]
        out = m
    return out


def _floor_near(img, u, v, reach):
    """The unreached floor pixel (255) nearest (u, v) within `reach` pixels, or None."""
    w, h = img.size
    for r in range(reach + 1):
        for du in range(-r, r + 1):
            for dv in range(-r, r + 1):
                if max(abs(du), abs(dv)) != r:
                    continue
                x, y = u + du, v + dv
                if 0 <= x < w and 0 <= y < h and img.getpixel((x, y)) == 255:
                    return (x, y)
    return None


def _mission_doors(plan, level):
    """[(x, y, yaw degrees, width uu)] of the doors after the plan."""
    gone = {w.get('name') for w in plan.get('wiring') or [] if w.get('gone')}
    out = []
    for d in (level.get('doors') or {}).values():
        if d['export'] in gone:
            continue
        rot = d.get('rotation')
        yaw = rot[1] if isinstance(rot, (list, tuple)) else float(rot or 0.0)
        scale = (d.get('components') or {}).get('DoorMesh', {}).get('scale') or (1, 1, 1)
        width = DOOR_WIDTHS.get(d['cls'], FORCEFIELD_PLANE * max(scale[0], scale[1]))
        out.append((d['location'][0], d['location'][1], yaw, width))
    for d in plan.get('doors') or []:
        cls = d.get('cls') or 'BP_Door_Interior_3W_C'
        width = DOOR_WIDTHS.get(cls, FORCEFIELD_PLANE * max(float(d.get('scaleX') or 1),
                                                            float(d.get('scaleY') or 1)))
        out.append((d['at'][0], d['at'][1], float(d.get('rotation') or 0.0), width))
    return out


def _band_px(walls):
    """The shipped wall band's width in pixels: the commonest run of wall pixels."""
    import numpy as np
    runs = collections.Counter()
    for row in walls[::16]:
        edges = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(int), [0]))))
        for a, b in zip(edges[::2], edges[1::2]):
            runs[b - a] += 1
    return max(2, runs.most_common(1)[0][0]) if runs else 4


def prepare(level_name, plan, src, level=None):
    """{volume, reframe, painter, floor, notes, cut} for a build: the shipped minimap of
    `src` (the level's MapData volume) cut to the floor inside the doors."""
    import numpy as np
    from riftlord.build.minimap import mapdata
    from PIL import Image, ImageDraw
    from riftlord.mapeditor import leveldata
    from riftlord.build.minimap import mapdataemit
    from riftlord.mapeditor import scratch
    level = level if level is not None else leveldata.level(level_name)
    img = np.array(mapdata.minimap_image(src).convert('RGBA'))
    n = img.shape[0]
    drawn = img[..., 3] > CUT_DRAWN
    dark = img[..., :3].mean(-1) < CUT_DARK
    floor_px, wall_px = drawn & dark, drawn & ~dark
    band = _band_px(wall_px)
    project = mapdata.projector(src)

    def px(x, y):
        u, v = project((x, y))
        return (u * n, v * n)
    cut = Image.new('L', (n, n), 0)
    pen = ImageDraw.Draw(cut)
    doors = _mission_doors(plan, level)
    for x, y, yaw, width in doors:
        half = width / 2.0 + CUT_OVERLAP
        dx, dy = math.cos(math.radians(yaw)) * half, math.sin(math.radians(yaw)) * half
        pen.line([px(x - dx, y - dy), px(x + dx, y + dy)], fill=255, width=band)
    cut = np.asarray(cut) > 0
    #: a copy: an image made from an array shares its buffer, and flood fill wrote nothing
    walk = Image.fromarray(np.where(floor_px & ~cut, 255, 0).astype('uint8'), 'L').copy()
    rifts, _doors = mapdataemit.markers_for(level_name, plan, level=level)
    if not rifts:
        raise SystemExit('the minimap cannot be cut: the mission has no rift to keep')
    for r in rifts:
        u, v = px(r[0], r[1])
        at = _floor_near(walk, int(u), int(v), band * 4)
        if at is not None:
            ImageDraw.floodfill(walk, at, 128)
    kept = np.asarray(walk) == 128
    if not kept.any():
        raise SystemExit('the minimap cannot be cut: no floor found at the rift')
    near = _grow(kept, band)
    walls = near & ~kept & (drawn | cut)
    out = np.zeros_like(img)
    wall_alpha = int(np.median(img[..., 3][wall_px])) if wall_px.any() else scratch.MAP_ALPHA
    out[kept] = img[kept]
    out[walls, :3] = 255
    out[walls, 3] = np.where(wall_px[walls], img[..., 3][walls], wall_alpha)
    edited = Image.fromarray(out, 'RGBA')
    # the frame: a square around what is left, turned as the shipped one, with the
    # shipped margin; a crop of the source texture, its centre mapped back to the world
    rows, cols = np.nonzero(kept | walls)
    o = np.array(project((0.0, 0.0)))
    m = np.array([np.array(project((1.0, 0.0))) - o, np.array(project((0.0, 1.0))) - o]).T
    cu, cv = (cols.min() + cols.max() + 1) / 2.0 / n, (rows.min() + rows.max() + 1) / 2.0 / n
    span = max(cols.max() + 1 - cols.min(), rows.max() + 1 - rows.min()) / float(n)
    span /= (1.0 - 2.0 * scratch.MAP_MARGIN)
    cx, cy = np.linalg.solve(m, np.array([cu, cv]) - o)
    half = span * max(abs(src['extent'][0]), abs(src['extent'][1]))
    volume = {'translation': (float(cx), float(cy), float(src['translation'][2])),
              'rotation': tuple(src['rotation']),
              'extent': (float(half), float(half), abs(float(src['extent'][2])))}

    def painter(size):
        # output pixel -> source pixel: the crop, scaled
        step = span * n / size
        coeffs = (step, 0.0, (cu - span / 2.0) * n, 0.0, step, (cv - span / 2.0) * n)
        return edited.transform((size, size), Image.AFFINE, coeffs, resample=Image.BILINEAR)
    notes = ['the shipped minimap, cut at %d door(s) to the floor the rift reaches'
             % len(doors)]
    return {'volume': volume, 'reframe': True, 'floor': None, 'notes': notes,
            'painter': painter, 'cut': True}
