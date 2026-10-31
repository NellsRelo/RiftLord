"""Draw Rift Lord's icon: the game's rift marker -- a thick saw-toothed ring round a
hollow centre -- in the app's purple, with the title magenta as its glow.

    python3 workshop/make_icon.py    # writes web/public/favicon.png, icon.png, icon.ico
"""

import math
import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIZE = 1024                  # drawn large, then scaled down for every icon size
PURPLE = (160, 90, 192, 255)     # the Map Editor's accent, #a05ac0
HOT = (220, 80, 200, 255)        # its highlight, #dc50c8
DARK = (16, 19, 26, 255)         # the app's background, #10131a
TEETH = 10                       # the game's rift ring has about ten


def saw_ring(outer, tooth, inner, count, colour):
    """The game's rift marker: a thick ring whose teeth lean one way, like a saw blade
    (ICON_Minimap_Rift has fourteen). Each tooth rises steeply to its tip and falls
    back along a slope to the next."""
    layer = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    c = SIZE / 2
    step = 2 * math.pi / count
    pts = []
    for i in range(count):
        a = i * step - math.pi / 2
        for frac, rise in TOOTH:
            r = outer + tooth * rise
            pts.append((c + r * math.cos(a + frac * step), c + r * math.sin(a + frac * step)))
    d.polygon(pts, fill=colour)
    d.ellipse((c - inner, c - inner, c + inner, c + inner), fill=(0, 0, 0, 0))
    return layer


#: One tooth's outline across its slot, as (fraction of the slot, fraction of the tooth's
#: height): a long ramp up to the tip and a sharp drop, every tooth leaning the same
#: way like a saw blade -- the marker's teeth -- then a little ring before the next.
TOOTH = ((0.0, 0.0), (0.18, 0.35), (0.42, 0.72), (0.62, 1.0), (0.7, 0.0))
#: The ring's teeth, as on the game's rift marker.
RIFT_TEETH = 14


def draw():
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((24, 24, SIZE - 24, SIZE - 24), radius=210, fill=DARK)
    ring = saw_ring(262, 82, 182, RIFT_TEETH, PURPLE)
    # the glow the game's marker has, in the title magenta
    glow = saw_ring(262, 82, 182, RIFT_TEETH, HOT).filter(ImageFilter.GaussianBlur(46))
    img.alpha_composite(glow)
    img.alpha_composite(Image.eval(glow, lambda v: int(v * 0.6)))
    img.alpha_composite(ring)
    # a faint lighter edge round the hollow, as the marker's inner rim catches light
    c = SIZE / 2
    rim = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(rim).ellipse((c - 182, c - 182, c + 182, c + 182), outline=HOT[:3] + (150,),
                                width=14)
    img.alpha_composite(rim.filter(ImageFilter.GaussianBlur(3)))
    return img


def main():
    img = draw()
    out_png = os.path.join(ROOT, 'web', 'shell', 'public', 'favicon.png')
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    img.resize((128, 128), Image.LANCZOS).save(out_png)
    img.resize((256, 256), Image.LANCZOS).save(os.path.join(ROOT, 'shell', 'icon.png'))
    img.save(os.path.join(ROOT, 'shell', 'icon.ico'),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == '__main__':
    main()
