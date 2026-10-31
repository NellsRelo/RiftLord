"""Draw a map's minimap in the game's own style, marking rift, doors and spawners.

The picture answers "where does a group come in, and which number addresses it":
door and AI spawner markers carry the index an onslaught uses. Art, placement and
icon style follow the in-game minimap; a map with several floors has one volume
per floor, each with its own texture.

    python3 mapview.py --map Scenario_13 --out scen13.png
    python3 mapview.py --map Scenario_15 --volume 2
    python3 mapview.py --list
"""

import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.onslaught import onslaught  # noqa: E402
from riftlord.build.minimap import mapdata  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import numpy as np
except ImportError:
    raise SystemExit('this needs Pillow and numpy:  pip install Pillow numpy')

BG = (13, 16, 26)
FILL = (31, 37, 56)            # the volume's floor area
LINE = (168, 178, 202)         # walls and stairs
GOLD = (226, 199, 9)           # door icon, sampled from the game's HUD
GOLD_DIM = (150, 130, 10)
INK = (10, 10, 12)             # the door icon's interior
RIFT = (38, 118, 230)          # rift ring, sampled from the game's HUD
RIFT_DIM = (8, 65, 149)
SPAWN = (92, 206, 196)
TEXT = (226, 232, 240)
DIM = (126, 136, 162)

SS = 4                         # icon supersampling factor
FONTS = ('/usr/share/fonts/liberation/LiberationSans-Bold.ttf',
         '/usr/share/fonts/TTF/LiberationSans-Bold.ttf',
         '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf')


def _font(size):
    for path in FONTS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def art_layer(volume, px):
    """The volume's minimap as an RGBA layer px wide: floor fill plus wall lines.

    The cooked texture carries the line art in RGB and the floor silhouette in
    alpha, so the two are separated by channel rather than by threshold.
    """
    tex = mapdata.minimap_image(volume)
    if tex is None:
        return None
    arr = np.asarray(tex)
    alpha = arr[..., 3].astype(float)
    alpha /= max(alpha.max(), 1.0)
    inside = alpha > 0.05
    line = inside & (arr[..., 0] > 128)

    h, w = inside.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[inside] = (*FILL, 255)
    out[line] = (*LINE, 255)
    return Image.fromarray(out, 'RGBA').resize((px, px), Image.LANCZOS)


def _arch(cx, cy, r):
    """Polygon for the game's door plaque: straight sides under a domed top."""
    h = 2.15 * r
    top, bottom = cy - h / 2, cy + h / 2
    pts = [(cx - r, bottom), (cx - r, top + r)]
    for a in range(180, 361, 4):
        t = math.radians(a)
        pts.append((cx + r * math.cos(t), (top + r) + r * math.sin(t)))
    pts += [(cx + r, bottom)]
    return pts


def draw_door(dr, x, y, r):
    """A gold plaque with a barred gate, the in-game door marker."""
    x, y, r = x * SS, y * SS, r * SS
    t = r * 0.26
    dr.polygon(_arch(x, y, r), fill=GOLD)
    dr.polygon(_arch(x, y, r - t), fill=INK)
    half = r - t * 1.45
    hi, lo = y - 1.075 * r + t + r * 0.42, y + 1.075 * r - t * 1.25
    for a, b in (((x - half, hi), (x + half, lo)),
                 ((x + half, hi), (x - half, lo))):
        dr.line([a, b], fill=GOLD, width=max(1, int(t * 1.25)))


def draw_rift(dr, x, y, r):
    """A toothed blue ring, the in-game rift marker."""
    x, y, r = x * SS, y * SS, r * SS
    pts = []
    for i in range(241):
        t = i * math.tau / 240.0
        rad = r * (0.93 + 0.07 * math.cos(10 * t))
        pts.append((x + rad * math.cos(t), y + rad * math.sin(t)))
    dr.polygon(pts, fill=RIFT)
    dr.ellipse([x - r * 0.62, y - r * 0.62, x + r * 0.62, y + r * 0.62],
               fill=(0, 0, 0, 0))


def draw_spawner(dr, x, y, r):
    """A ringed dot: where a wave enters before it reaches its door."""
    x, y, r = x * SS, y * SS, r * SS
    dr.ellipse([x - r, y - r, x + r, y + r], fill=(*SPAWN, 60),
               outline=SPAWN, width=int(r * 0.24))
    dr.ellipse([x - r * 0.34, y - r * 0.34, x + r * 0.34, y + r * 0.34],
               fill=SPAWN)


def _badge(img, x, y, label, colour, font):
    """A numbered chip under a marker, drawn on the finished image."""
    dr = ImageDraw.Draw(img, 'RGBA')
    box = dr.textbbox((0, 0), label, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    pad = 4
    x0, y0 = x - w / 2 - pad, y - pad
    dr.rounded_rectangle([x0, y0, x0 + w + 2 * pad, y0 + h + 2 * pad],
                         radius=4, fill=(*BG, 225), outline=colour)
    dr.text((x0 + pad - box[0], y0 + pad - box[1]), label, fill=colour, font=font)


def _flatten(layer, w, h, opacity):
    """A supersampled marker layer brought down to size with a soft glow."""
    small = layer.resize((w, h), Image.LANCZOS)
    glow = layer.filter(ImageFilter.GaussianBlur(radius=SS * 2.5)
                        ).resize((w, h), Image.LANCZOS)
    flat = Image.alpha_composite(glow, small)
    if opacity < 1.0:
        alpha = flat.getchannel('A').point(lambda v: int(v * opacity))
        flat.putalpha(alpha)
    return flat


def _columns(lines, rows):
    """Lines dealt into columns of at most `rows` each."""
    return [lines[i:i + rows] for i in range(0, len(lines), rows)] or [[]]


def render(map_name, size=1100, out='map.png', volume=None):
    rep = onslaught.map_report(map_name)
    md = mapdata.read(map_name)
    if not md or not md['volumes']:
        raise SystemExit('%s has no MapData' % map_name)

    rift_pts = [p['location'] for p in rep['points'].values()
                if p.get('exit') and p.get('location')] or md['rifts']
    if volume is None:
        volume = mapdata.pick_volume(md['volumes'], rift_pts[0]) if rift_pts else 0
    vol = md['volumes'][volume]
    project = mapdata.projector(vol)
    if project is None:
        raise SystemExit('volume %d of %s has no extent' % (volume, map_name))

    doors = [(n, d['location']) for n, d in sorted(rep['doors'].items())
             if d['location']]
    spawners = [(s, s['location']) for s in sorted(rep['spawners'],
                                                   key=lambda s: s['index'])
                if s['location']]
    marks = ([(p, RIFT) for p in rift_pts] + [(p, GOLD) for _, p in doors]
             + [(p, SPAWN) for _, p in spawners])

    # Spawners often sit outside the volume, so the canvas grows to hold them.
    uvs = [project(p) for p, _ in marks]
    pad = 0.05
    umin = min([0.0] + [u - pad for u, _ in uvs])
    umax = max([1.0] + [u + pad for u, _ in uvs])
    vmin = min([0.0] + [v - pad for _, v in uvs])
    vmax = max([1.0] + [v + pad for _, v in uvs])
    scale = size / max(umax - umin, vmax - vmin)
    W = max(1, int(round((umax - umin) * scale)))
    H = max(1, int(round((vmax - vmin) * scale)))

    def to_px(p):
        u, v = project(p)
        return ((u - umin) * scale, (v - vmin) * scale)

    img = Image.new('RGB', (W, H), BG)
    art = art_layer(vol, max(1, int(round(scale))))
    if art is not None:
        img.paste(art, (int(round(-umin * scale)), int(round(-vmin * scale))), art)

    # A point on another floor is still drawn, faded, so a door that belongs to
    # the storey below is not mistaken for a misplaced one.
    def floor_of(p):
        return mapdata.pick_volume(md['volumes'], p)

    here = Image.new('RGBA', (W * SS, H * SS), (0, 0, 0, 0))
    away = Image.new('RGBA', (W * SS, H * SS), (0, 0, 0, 0))
    dr_here, dr_away = ImageDraw.Draw(here, 'RGBA'), ImageDraw.Draw(away, 'RGBA')
    r = max(13.0, size / 44.0)

    def layer(p):
        return dr_here if floor_of(p) == volume else dr_away

    for _, p in spawners:
        x, y = to_px(p)
        draw_spawner(layer(p), x, y, r * 0.58)
    for _, p in doors:
        x, y = to_px(p)
        draw_door(layer(p), x, y, r * 0.82)
    for p in rift_pts:
        x, y = to_px(p)
        draw_rift(layer(p), x, y, r)

    base = img.convert('RGBA')
    base = Image.alpha_composite(base, _flatten(away, W, H, 0.4))
    base = Image.alpha_composite(base, _flatten(here, W, H, 1.0))
    img = base.convert('RGB')

    font = _font(max(12, int(size / 72)))
    many = len(md['volumes']) > 1

    def tag(label, p):
        f = floor_of(p)
        return '%s f%d' % (label, f + 1) if many and f != volume else label

    for i, (name, p) in enumerate(doors, 1):
        x, y = to_px(p)
        _badge(img, x, y + r * 1.05, tag('D%d' % i, p), GOLD, font)
    for s, p in spawners:
        x, y = to_px(p)
        _badge(img, x, y + r * 0.72, tag('S%d' % s['index'], p), SPAWN, font)

    # Captions get their own bands so nothing is drawn over the map.
    head = _font(max(13, int(size / 62)))
    step = head.size + 6
    title = mapdata.mission_names().get(map_name)
    top = ['%s   volume %d of %d   %d spawners, %d doors'
           % ('%s  -  %s' % (map_name, title) if title else map_name,
              volume + 1, len(md['volumes']), len(spawners), len(doors)),
           'D# door   S# SpawnerIndex (what an onslaught addresses)   ring = rift']
    foot = ['S%-2d -> %s' % (s['index'], (s['door'] or '-').split('.')[-1])
            for s, _ in spawners]
    cols = _columns(foot, 8)
    top_h = len(top) * step + 14
    foot_h = max(len(c) for c in cols) * step + 14 if foot else 0

    page = Image.new('RGB', (W, H + top_h + foot_h), BG)
    page.paste(img, (0, top_h))
    dr = ImageDraw.Draw(page, 'RGBA')
    for i, t in enumerate(top):
        dr.text((14, 7 + i * step), t, fill=TEXT if i == 0 else DIM, font=head)
    cw = max((max(dr.textlength(t, font=head) for t in c) for c in cols),
             default=0) + 26
    for ci, col in enumerate(cols):
        for i, t in enumerate(col):
            dr.text((14 + ci * cw, H + top_h + 7 + i * step), t, fill=DIM, font=head)
    img = page

    img.save(out)
    return rep, out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--map')
    ap.add_argument('--out', default='map.png')
    ap.add_argument('--size', type=int, default=1100)
    ap.add_argument('--volume', type=int,
                    help='which floor to draw; default is the rift\'s')
    ap.add_argument('--list', action='store_true',
                    help='every campaign map with its spawner, door and volume counts')
    args = ap.parse_args()

    if args.list:
        print('%-18s %-20s %8s %6s %7s %s'
              % ('map', 'name', 'spawners', 'doors', 'volumes', 'path points'))
        titles = mapdata.mission_names()
        for m in mapdata.maps():
            try:
                rep = onslaught.map_report(m)
                md = mapdata.read(m)
            except SystemExit as err:
                print('%-18s %s' % (m, err))
                continue
            print('%-18s %-20s %8d %6d %7d %d'
                  % (m, titles.get(m, '?'), len(rep['spawners']),
                     len(rep['doors']), len(md['volumes']) if md else 0,
                     len(rep['points'])))
        return

    if not args.map:
        ap.error('need --map or --list')
    rep, out = render(args.map, args.size, args.out, args.volume)
    print('wrote %s  (%d spawners, %d doors)'
          % (out, len(rep['spawners']), len(rep['doors'])))


if __name__ == '__main__':
    main()
