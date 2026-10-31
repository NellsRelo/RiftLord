"""The mode select's square button art (OrcsMustUI's Custom Maps row):
each shipped mode banner rebuilt at another width with its own torn border and
glow. The border is the original's left and right edges (EDGE pixels each)
around a middle cut from (or, when wider, stretched from) the original's middle;
the picture inside the border is the original's picture cropped to the new
width (or, when wider, scaled up to cover it). Custom Maps uses Scramble's
border round the Custom Maps tab's image.

    python3 modeart.py SOURCE_DIR OUT_DIR
    python3 modeart.py card SOURCE_DIR PICTURE OUT_PREFIX   a campaign's card
SOURCE_DIR holds <texture>_rgba.png for each SOURCES entry (exported RGBA;
omui.sh texture exports them).
"""
import os
import sys

import numpy as np
from PIL import Image

#: The shipped banners' size, and the torn border plus glow at each side
#: (measured: the picture starts at x 136-193 and ends at 1858-1912 of 2048).
HEIGHT = 1052
EDGE = 260
#: The picture's rows in the shipped banners (measured: opaque from y 127-141
#: to 911-921 of 1052).
PICTURE_TOP, PICTURE_BOTTOM = 133, 911
#: Columns inside the picture on every row (measured: the torn edge reaches
#: x 193 on the left and 1858 on the right).
PICTURE_LEFT, PICTURE_RIGHT = 200, 1850
#: The buttons' shape on screen, width over height (ModeRow.yaml: squares
#: 516 x 516).
SQUARE = 1.0

#: Output name: (the normal banner its border comes from, the hovered one,
#: picture source or None for the banner's own, width over height). Each makes
#: <name>_Off and <name>_On.
OUTPUTS = {
    'MODE_Endless_Square': ('ICON_Mode_Endless_Off', 'ICON_Mode_Endless_On', None, SQUARE),
    'MODE_CustomMaps_Square': ('ICON_Mode_Scramble_Off', 'ICON_Mode_Scramble_On', 'custom', SQUARE),
}


def resized(a, w, h):
    return np.array(Image.fromarray(a).resize((w, h), Image.LANCZOS))


def border(src, width):
    """The source's left and right edges round its middle, cut or stretched."""
    h, w = src.shape[:2]
    middle = width - 2 * EDGE
    inner = src[:, EDGE:w - EDGE]
    if middle <= inner.shape[1]:
        at = (inner.shape[1] - middle) // 2
        inner = inner[:, at:at + middle]
    else:
        inner = resized(inner, middle, h)
    return np.concatenate([src[:, :EDGE], inner, src[:, w - EDGE:]], axis=1)


def cover(pic, width, height):
    """The picture scaled to cover width x height, centred and cropped."""
    h, w = pic.shape[:2]
    if (w, h) == (width, height):
        return pic
    scale = max(width / w, height / h)
    big = resized(pic, max(width, round(w * scale)), max(height, round(h * scale)))
    y = (big.shape[0] - height) // 2
    x = (big.shape[1] - width) // 2
    return big[y:y + height, x:x + width]


def build(src, pic, aspect):
    width = round(HEIGHT * aspect)
    frame = border(src, width)
    if pic is None:
        if width <= src.shape[1]:
            at = (src.shape[1] - width) // 2
            inside = src[:, at:at + width]
        else:
            inside = cover(src[PICTURE_TOP:PICTURE_BOTTOM, PICTURE_LEFT:PICTURE_RIGHT],
                           width, PICTURE_BOTTOM - PICTURE_TOP)
            inside = np.concatenate([frame[:PICTURE_TOP], inside, frame[PICTURE_BOTTOM:]], axis=0)
    else:
        rows = cover(pic, width, PICTURE_BOTTOM - PICTURE_TOP)
        inside = np.concatenate([frame[:PICTURE_TOP], rows, frame[PICTURE_BOTTOM:]], axis=0)
    out = frame.copy()
    solid = frame[:, :, 3] == 255
    out[solid, :3] = inside[solid, :3]
    return out


def hovered(normal, edge_off, edge_on, aspect):
    """The hovered art: the normal art with the game's hover added, the
    difference between its normal and hovered banners at the same place (the
    brighter glow outside the picture and the lit rim inside its torn edge)."""
    width = round(HEIGHT * aspect)
    off = border(edge_off, width).astype(np.int16)
    on = border(edge_on, width)
    out = on.copy()
    solid = on[:, :, 3] == 255
    lit = normal[:, :, :3].astype(np.int16) + (on[:, :, :3].astype(np.int16) - off[:, :, :3])
    out[solid, :3] = np.clip(lit, 0, 255).astype(np.uint8)[solid]
    return out


#: The campaign row's square banner (2048 x 2048) whose torn border and glow a
#: campaign card wears (OrcsMustUI's mode select carousel).
CARD_BANNER = ('ICON_Mode_Story_Off', 'ICON_Mode_Story_On')
#: The hovered card's lit rim: the hover's difference is added this far in
#: from the torn edge (pixels of 2048). Further in, the Story banners' own
#: pictures differ between rest and hover, which would show through.
CARD_RIM = 48
#: The Story banner's hover inside that rim: the picture brightened, on =
#: gain x off + lift, by distance from the torn edge (pixels of 2048). Measured
#: by least squares of ICON_Mode_Story_On against _Off over each band (the
#: wide mode banners, whose pictures do not change, fit the same form).
CARD_LIFT = ((48, 96, 1.009, 12.8), (96, 192, 1.021, 12.6), (192, 384, 1.056, 9.1),
             (384, 1 << 30, 1.093, 5.1))
#: The picture window of the square banner: the bounding box (x, y, width,
#: height on the 2048 square) of ICON_Mode_Story_Off's opaque pixels, which are
#: the only ones that show the picture (measured with card_window: x 112-1933,
#: y 109-1933).
CARD_WINDOW = (112, 109, 1822, 1825)
#: The square banner's size in pixels.
CARD_SIZE = 2048


def card_window(edge_off):
    """(x, y, width, height): the bounding box of a banner's opaque pixels."""
    ys, xs = np.nonzero(edge_off[:, :, 3] == 255)
    return (int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1),
            int(ys.max() - ys.min() + 1))


def card_box(width, height, crop=None, centre=None):
    """[x, y, w, h] (fractions of a width x height picture): `crop` made the
    window's shape (CARD_WINDOW) about its own centre and kept inside the
    picture, or with no crop the largest box of that shape about `centre`
    ((x, y) fractions; the picture's centre when None)."""
    aspect = CARD_WINDOW[2] / CARD_WINDOW[3]
    if crop is not None:
        x, y, w, h = (float(v) for v in crop)
        cx, cy = (x + w / 2) * width, (y + h / 2) * height
        bw, bh = w * width, h * height
        bw, bh = min(bw, bh * aspect), min(bh, bw / aspect)
    else:
        cx, cy = ((centre[0] * width, centre[1] * height) if centre is not None
                  else (width / 2, height / 2))
        bw = bh = float('inf')
    bw = max(1.0, min(bw, width, height * aspect))
    bh = bw / aspect
    x = min(max(cx - bw / 2, 0.0), width - bw)
    y = min(max(cy - bh / 2, 0.0), height - bh)
    return [round(v, 6) for v in (x / width, y / height, bw / width, bh / height)]


def edge_distance(solid, step=4):
    """Each pixel's distance in from the torn edge (pixels), to within step:
    the opaque area eroded a pixel at a time at 1/step of the size."""
    from PIL import ImageFilter
    h, w = solid.shape
    small = Image.fromarray((solid * 255).astype(np.uint8)).resize((w // step, h // step), Image.NEAREST)
    out = np.zeros((small.height, small.width))
    rings = 0
    while True:
        inside = np.array(small) > 127
        if not inside.any():
            break
        rings += 1
        out[inside] = rings * step
        small = small.filter(ImageFilter.MinFilter(3))
    return np.repeat(np.repeat(out, step, 0), step, 1)[:h, :w]


def card(edge_off, edge_on, pic):
    """A campaign card: the square banner's own border and glow round the
    picture (scaled to cover the banner's picture window, centred), and its
    hovered version: the hovered banner's glow outside, its lit rim inside
    the torn edge, and the picture within brightened as the Story banner's
    is (CARD_LIFT)."""
    from PIL import ImageFilter
    x, y, w, h = CARD_WINDOW
    inside = edge_off.copy()
    inside[y:y + h, x:x + w] = cover(pic, w, h)
    normal = edge_off.copy()
    solid = edge_off[:, :, 3] == 255
    normal[solid, :3] = inside[solid, :3]
    # CARD_RIM passes of a 3 x 3 erosion: the same as one (2 x CARD_RIM + 1) square, faster
    core = Image.fromarray((solid * 255).astype(np.uint8))
    for _ in range(CARD_RIM):
        core = core.filter(ImageFilter.MinFilter(3))
    core = np.array(core) > 127
    rim = solid & ~core
    out = edge_on.copy()
    out[solid, :3] = normal[solid, :3]
    lit = normal[:, :, :3].astype(np.int16) + (edge_on[:, :, :3].astype(np.int16) - edge_off[:, :, :3])
    out[rim, :3] = np.clip(lit, 0, 255).astype(np.uint8)[rim]
    far = edge_distance(solid)
    for near, beyond, gain, lift in CARD_LIFT:
        band = solid & ~rim & (far > near) & (far <= beyond)
        out[band, :3] = np.clip(normal[band, :3] * gain + lift, 0, 255).astype(np.uint8)
    return normal, out


#: The Story banners in the game's paks (CARD_BANNER's textures).
CARD_BANNER_PATHS = ('/Game/UI/Icons/ICON_Mode_Story_Off', '/Game/UI/Icons/ICON_Mode_Story_On')


_BANNERS = {}


def story_banners():
    """(normal, hovered) Story banners as RGBA arrays, read from the game's paks."""
    if not _BANNERS:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        from riftlord.build.minimap import mapdata
        from riftlord.formats.ue import texture
        for path in CARD_BANNER_PATHS:
            f = mapdata._fetch(path)
            info = texture.parse(f['.uasset'], f['.uexp'], f.get('.ubulk'))
            _BANNERS[path] = np.array(texture.to_image(info).convert('RGBA'))
    return _BANNERS[CARD_BANNER_PATHS[0]], _BANNERS[CARD_BANNER_PATHS[1]]


def open_picture(picture):
    """A path, PNG or JPG bytes, or a PIL image as an RGB image the way it
    displays (its EXIF orientation applied)."""
    import io
    from PIL import ImageOps
    if isinstance(picture, (bytes, bytearray)):
        picture = Image.open(io.BytesIO(picture))
    elif not isinstance(picture, Image.Image):
        picture = Image.open(picture)
    return ImageOps.exif_transpose(picture).convert('RGB')


def card_picture(picture, crop):
    """The picture's `crop` ([x, y, w, h] fractions, made the window's shape
    by card_box) at the picture window's size: one LANCZOS resample of the
    full-resolution picture."""
    w, h = picture.size
    x, y, bw, bh = card_box(w, h, crop)
    box = (x * w, y * h, (x + bw) * w, (y + bh) * h)
    return picture.resize(CARD_WINDOW[2:], Image.LANCZOS, box=box)


def campaign_card(picture, crop=None):
    """A campaign's mode select card and its hovered version as PNG bytes
    (2048 x 2048), for CustomMaps.yaml's Card and CardHovered: the Story
    banner's border and glow, read from the game's paks, round the picture
    (a path, PNG or JPG bytes, or a PIL image) cut to `crop` ([x, y, w, h]
    fractions; card_box's default when None)."""
    import io
    picture = open_picture(picture)
    shown = card_picture(picture, crop if crop is not None else card_box(*picture.size))
    edge_off, edge_on = story_banners()
    normal, lit = card(edge_off, edge_on, np.array(shown.convert('RGBA')))
    out = []
    for image in (normal, lit):
        buf = io.BytesIO()
        Image.fromarray(image).save(buf, 'PNG')
        out.append(buf.getvalue())
    return out[0], out[1]


def card_frame(size=512):
    """(frame, mask) PNG bytes at `size` x `size` for drawing a card's preview:
    the normal Story banner with its opaque pixels cleared, and those opaque
    pixels as a white mask. A picture filling CARD_WINDOW, kept where the mask
    is, under the frame, is the card as campaign_card makes it."""
    import io
    edge_off, _on = story_banners()
    solid = edge_off[:, :, 3] == 255
    frame = edge_off.copy()
    frame[solid, 3] = 0
    mask = np.zeros_like(edge_off)
    mask[solid] = 255
    out = []
    for a in (frame, mask):
        image = Image.fromarray(a)
        if size != image.width:
            image = image.resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, 'PNG')
        out.append(buf.getvalue())
    return out[0], out[1]


def card_main(source, picture, out_prefix):
    """modeart.py card SOURCE_DIR PICTURE OUT_PREFIX: OUT_PREFIX_Off.png and
    _On.png, a campaign's card for the mode select (2048 x 2048)."""
    def load(name):
        return np.array(Image.open(os.path.join(source, name + '_rgba.png')).convert('RGBA'))
    pic = np.array(Image.open(picture).convert('RGBA'))
    normal, lit = card(load(CARD_BANNER[0]), load(CARD_BANNER[1]), pic)
    for suffix, image in (('_Off', normal), ('_On', lit)):
        Image.fromarray(image).save(out_prefix + suffix + '.png')
        print(out_prefix + suffix + '.png', image.shape[1], 'x', image.shape[0])


def main(source, out):
    os.makedirs(out, exist_ok=True)
    cache = {}

    def load(name):
        if name not in cache:
            cache[name] = np.array(Image.open(os.path.join(source, name + '_rgba.png')).convert('RGBA'))
        return cache[name]

    for name, (edge_off, edge_on, pic, aspect) in OUTPUTS.items():
        normal = build(load(edge_off), load(pic) if pic else None, aspect)
        lit = hovered(normal, load(edge_off), load(edge_on), aspect)
        for suffix, image in (('_Off', normal), ('_On', lit)):
            Image.fromarray(image).save(os.path.join(out, name + suffix + '.png'))
            print(name + suffix, image.shape[1], 'x', image.shape[0])


if __name__ == '__main__':
    if sys.argv[1] == 'card':
        card_main(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        main(sys.argv[1], sys.argv[2])
