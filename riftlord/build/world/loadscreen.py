"""A mission's own loading screen: a retitled copy of a loading widget, optionally
showing the mission's own picture.

A MissionProtos row names its loading screen by `MissionLoadScreen`, a widget
class. The DLC, Ice and Sand maps (and the Endless and Weekly Challenge rows)
use a per-map widget whose TextBlock holds the map's title as a literal FText
(namespace `MissionProtos`) over the map's screenshot, a 3840 x 2160 texture that
fills the widget's 1920 x 1080 frame (16:9, letterboxed by a ScaleBox). `build`
copies that widget into the mod with the title rewritten, and the campaign line under
it set to the mission's campaign (campaign_line()). Base-campaign rows use
trap-tip and help screens with no title or picture; they are kept, unless the
mission has a picture, which then shows on a copy of UNTITLED_DONOR.

A picture is a file and a crop (fractions of the image, 16:9 in pixels); it is
written as a copy of PICTURE_DONOR, a single-mip UI texture, in its own format.

    python3 loadscreen.py UBP_LoadingScreen_Scenario_Sand_01
"""

import struct
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.build import modtable  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

#: The FText namespace a widget's mission title is written under.
TITLE_NAMESPACE = 'MissionProtos'
#: Bytes from a TextProperty tag's start to its FText namespace string: the
#: 25-byte tag (name, type, size, array index, has-guid) then flags and history.
TAG_TO_NAMESPACE = 30
#: Offset of the payload size inside a property tag.
TAG_SIZE_AT = 16

#: The widget a picture is shown on when the source's own screen has no title.
UNTITLED_DONOR = '/Game/UI/LoadingScreens/UBP_LoadingScreen_Endless_NorthWing'
#: Namespaces of the campaign line under a widget's title ('' on the DLC, Ice and
#: Sand screens, CampaignProtos on the Endless ones).
SUBTITLE_NAMESPACES = ('CampaignProtos', '')
#: The campaign line of a mission listed in no campaign.
NO_CAMPAIGN = ('RiftLord', 'CustomMap', 'Custom Map')
#: Namespace of a custom campaign's line.
CUSTOM_NAMESPACE = 'RiftLord'
#: The texture a picture is written over: 3840 x 2160, one mip, TEXTUREGROUP_UI.
PICTURE_DONOR = '/Game/Maps/Campaign/Scenario_01/Images/Scenario_01_ScreenShot'
#: Width:height of the frame the widget shows the picture in.
PICTURE_ASPECT = (16, 9)
#: Folder of the shipped screenshots a loading widget names.
SCREENSHOT_ROOT = '/Game/Maps/'
#: Pillow's DDS pixel format for each block format a picture may be written in.
PILLOW_FORMATS = {'PF_DXT1': 'DXT1', 'PF_DXT5': 'DXT5'}


def _fstring(text):
    """An FString: Latin-1 with a positive length, else UTF-16 with a negative one."""
    try:
        raw = text.encode('latin-1') + b'\0'
        return struct.pack('<i', len(raw)) + raw
    except UnicodeEncodeError:
        raw = text.encode('utf-16-le') + b'\0\0'
        return struct.pack('<i', -(len(raw) // 2)) + raw


def _read_fstring(buf, o):
    n = struct.unpack_from('<i', buf, o)[0]
    if n < 0:
        end = o + 4 - 2 * n
        if end > len(buf):
            return None, o
        return buf[o + 4:end - 2].decode('utf-16-le', 'replace'), end
    if n == 0 or o + 4 + n > len(buf):
        return None, o
    return buf[o + 4:o + 4 + n - 1].decode('latin-1'), o + 4 + n


def soft_class(path):
    """`/Game/X/Y.Y_C` -> `/Game/X/Y`, the widget's package path."""
    return path.split('.', 1)[0]


def titles(pkg, namespace=TITLE_NAMESPACE):
    """[(export index, tag offset, key, source)] for each literal FText under
    `namespace` held by a TextProperty in `pkg`."""
    names = [s for s, _h in pkg.names]
    needle = _fstring(namespace)
    out = []
    for i, e in enumerate(pkg.exports):
        d = bytes(e['data'])
        at = d.find(needle)
        while at >= 0:
            tag = at - TAG_TO_NAMESPACE
            if tag >= 0:
                ti = struct.unpack_from('<i', d, tag + 8)[0]
                if 0 <= ti < len(names) and names[ti] == 'TextProperty' \
                        and d[at - 5:at] == b'\0\0\0\0\0':
                    key, o = _read_fstring(d, at + len(needle))
                    src, _o = _read_fstring(d, o)
                    if key is not None and src is not None:
                        out.append((i, tag, key, src))
            at = d.find(needle, at + 1)
    return out


def _set_text(pkg, found, namespace, key, source):
    """Rewrite one titles() entry as (namespace, key, source)."""
    i, tag, _k, _s = found
    data = bytearray(pkg.exports[i]['data'])
    old_size = struct.unpack_from('<i', data, tag + TAG_SIZE_AT)[0]
    start = tag + TAG_TO_NAMESPACE - 5
    payload = (b'\0\0\0\0\0' + _fstring(namespace) + _fstring(key) + _fstring(source))
    data[start:start + old_size] = payload
    struct.pack_into('<i', data, tag + TAG_SIZE_AT, len(payload))
    pkg.exports[i]['data'] = bytes(data)
    pkg.exports[i]['size'] = len(data)


def _load(package_path, exts=('.uasset', '.uexp')):
    files = mapdata._fetch(package_path, exts)
    blobs = []
    for ext in exts:
        with open(files[ext], 'rb') as fh:
            blobs.append(fh.read())
    pkg = Package(blobs[0], blobs[1])
    pkg.load_payloads()
    return pkg


def crop_box(size, crop, aspect=PICTURE_ASPECT):
    """Pixel box (l, t, r, b) of a crop given as [x, y, w, h] fractions of `size`,
    widened or narrowed about its centre to `aspect` and kept inside the image."""
    width, height = size
    x, y, w, h = [float(v) for v in crop]
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
        raise SystemExit('the picture crop is outside the image')
    cw, ch = w * width, h * height
    want = aspect[0] / aspect[1]
    cx, cy = x * width + cw / 2, y * height + ch / 2
    if cw / ch > want:
        cw = ch * want
    else:
        ch = cw / want
    left = min(max(cx - cw / 2, 0), width - cw)
    top = min(max(cy - ch / 2, 0), height - ch)
    return (left, top, left + cw, top + ch)


def picture_texture(image_path, crop, new_path, donor=PICTURE_DONOR,
                    aspect=PICTURE_ASPECT):
    """((uasset, uexp), report) for the cropped picture written as a copy of
    `donor` at `new_path`, each mip at the donor's size and format."""
    import io
    from riftlord.build import texpatch
    from riftlord.formats.ue import texture
    from PIL import Image
    files = mapdata._fetch(donor)
    blobs = {}
    for ext in ('.uasset', '.uexp'):
        with open(files[ext], 'rb') as fh:
            blobs[ext] = fh.read()
    fmt, mips = texpatch.mips(blobs)
    if any(m['file'] != '.uexp' for m in mips):
        raise SystemExit('%s streams its mips; a picture needs an inline texture'
                         % donor)
    with Image.open(image_path) as source:
        source = source.convert('RGB')
        box = crop_box(source.size, crop, aspect)
        framed = source.crop(tuple(round(v) for v in box))

    def picture(width, height):
        image = framed.resize((width, height), Image.LANCZOS)
        if fmt in PILLOW_FORMATS:
            buf = io.BytesIO()
            image.save(buf, format='DDS', pixel_format=PILLOW_FORMATS[fmt])
            return buf.getvalue()[128:128 + texture.mip_bytes(width, height, fmt)]
        return texpatch.encode(image.convert('RGBA'), fmt)

    for mip in mips:
        data = picture(mip['width'], mip['height'])
        if len(data) != mip['size']:
            raise SystemExit('picture mip %dx%d: %d bytes, %d expected'
                             % (mip['width'], mip['height'], len(data), mip['size']))
        buf = bytearray(blobs[mip['file']])
        buf[mip['at']:mip['at'] + mip['size']] = data
        blobs[mip['file']] = bytes(buf)
    pkg = Package(blobs['.uasset'], blobs['.uexp'])
    pkg.load_payloads()
    old_object = donor.rsplit('/', 1)[-1]
    modtable.retarget(pkg, exact={donor: new_path,
                                  old_object: new_path.rsplit('/', 1)[-1]})
    return pkg.write(), '%s %dx%d from %dx%d of the image' % (
        fmt, mips[0]['width'], mips[0]['height'], box[2] - box[0], box[3] - box[1])


def screenshot_of(pkg):
    """(package path, object name) of the shipped screenshot a widget shows, or None."""
    names = [s for s, _h in pkg.names]
    paths = [n for n in names if n.startswith(SCREENSHOT_ROOT)]
    if len(paths) != 1 or paths[0].rsplit('/', 1)[-1] not in names:
        return None
    return paths[0], paths[0].rsplit('/', 1)[-1]


def subtitles(pkg):
    """titles() entries of the campaign line under a widget's title."""
    return [t for ns in SUBTITLE_NAMESPACES for t in titles(pkg, ns)]


def campaign_line(shipped=None, name=None, key=None):
    """(namespace, key, source) of the campaign line: a shipped campaign row's own
    CampaignName, a custom campaign's `name` (under `key`), or NO_CAMPAIGN."""
    if shipped:
        from riftlord.build import missionproto
        table = missionproto.read(missionproto.CAMPAIGN_TABLE)
        row = table.row(shipped)
        field = table.field(row, 'CampaignName') if row else None
        if field is not None:
            data = bytes(field.payload)
            if data[4] == 0:                      # a Base FText: namespace, key, source
                ns, o = _read_fstring(data, 5)
                k, o = _read_fstring(data, o)
                src, _o = _read_fstring(data, o)
                if None not in (ns, k, src):
                    return (ns, k, src)
    if name:
        return (CUSTOM_NAMESPACE, key or 'Campaign_%s' % name, name)
    return NO_CAMPAIGN


def build(widget_class, new_path, key, title, picture=None, picture_path=None,
          subtitle=None):
    """(files, soft class path, report) or (None, None, reason).

    `widget_class` is the row's MissionLoadScreen (`/Game/.../W.W_C`); the copy
    lives at `new_path` and shows `title` under (`MissionProtos`, `key`). `picture`
    is {'path', 'crop'} or None; its texture goes to `picture_path`. `subtitle` is
    the campaign line (campaign_line()). `files` is {package path: (uasset, uexp)}.
    """
    source = soft_class(widget_class) if widget_class and widget_class != 'None' else None
    pkg = _load(source) if source else None
    found = titles(pkg) if pkg is not None else []
    said = []
    if len(found) != 1:
        if not picture:
            return None, None, ('%s shows no map title; kept'
                                % (source or 'the source row').rsplit('/', 1)[-1])
        source = UNTITLED_DONOR
        pkg = _load(source)
        found = titles(pkg)
        said.append('the source screen has no title, so %s is used'
                    % source.rsplit('/', 1)[-1])
    old_object = source.rsplit('/', 1)[-1]
    old_title = found[0][3]
    _set_text(pkg, found[0], TITLE_NAMESPACE, key, title)
    said.insert(0, 'retitled %s (%r -> %r)' % (old_object, old_title, title))
    lines = subtitles(pkg)
    if subtitle and len(lines) == 1:
        _set_text(pkg, lines[0], *subtitle)
        said.append('campaign line %r -> %r' % (lines[0][3], subtitle[2]))
    elif subtitle and lines:
        said.append('%d campaign lines; left as shipped' % len(lines))
    files = {}
    exact = {source: new_path}
    if picture:
        shot = screenshot_of(pkg)
        if shot is None:
            raise SystemExit('%s does not name exactly one screenshot' % old_object)
        files[picture_path], how = picture_texture(picture['path'], picture['crop'],
                                                   picture_path)
        exact.update({shot[0]: picture_path, shot[1]: picture_path.rsplit('/', 1)[-1]})
        said.append('picture %s' % how)
    new_object = new_path.rsplit('/', 1)[-1]
    modtable.retarget(pkg, exact=exact, substring=[(old_object, new_object)])
    files[new_path] = pkg.write()
    return files, '%s.%s_C' % (new_path, new_object), '; '.join(said)


def read_title(blobs):
    """The (key, source) of the one title FText in a written widget, or None."""
    pkg = Package(blobs[0], blobs[1])
    pkg.load_payloads()
    found = titles(pkg)
    return (found[0][2], found[0][3]) if len(found) == 1 else None


def main():
    for name in sys.argv[1:]:
        path = '/Game/UI/LoadingScreens/%s' % name
        pkg = _load(path)
        print(name, [(k, s) for _i, _t, k, s in titles(pkg)],
              [(k, s) for _i, _t, k, s in subtitles(pkg)], screenshot_of(pkg))


if __name__ == '__main__':
    main()
