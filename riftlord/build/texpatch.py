"""Rewrite a cooked texture's pixels in place: every mip re-encoded at its own
size and format, so no offset or size in the package changes.

    mips = texpatch.mips(blobs)                 # where each mip's bytes are
    texpatch.write(blobs, lambda w, h: image)   # each mip from an RGBA image

`blobs` is {'.uasset': bytes, '.uexp': bytes, '.ubulk': bytes?}, changed in
place. Block formats are encoded by bcn (DXT1, DXT5); uncompressed BGRA
and RGBA are written directly.
"""

import io
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.formats.ue import texture  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

#: Pillow's DDS pixel_format for each block format it can write.
PILLOW_FORMATS = {'PF_DXT1': 'DXT1', 'PF_DXT5': 'DXT5'}
#: The encoder sources encoder_id() digests, under the riftlord package (the frozen app
#: carries them as text: tools/build.py).
ENCODER_SOURCES = ('formats/ue/ispctex.py', 'build/lighting/bcn.py')
#: Minimap mips this many pixels a side and over are encoded as class 'minimap-top' (ISPC,
#: ispctex.chosen), smaller ones as 'minimap' (ispctex.PYTHON_KINDS: the Python encoder).
#: Measured on 19 minimap pictures (docs/PLAN.md round 11), ISPC's alpha-weighted colour
#: RMSE against the Python encoder's: -0.046 to +0.021 at 1024 and 2048 (within
#: bccertify.SLACK, 0.05), up to +0.18 at 512, up to +13.7 below; alpha identical. Kept
#: here, outside ENCODER_SOURCES, so the split leaves encoder_id('texture'), the Fab
#: libraries' key, as it was.
MINIMAP_TOP = 1024


def mip_class(kind, size):
    """The texture class a mip of `size` (width, height) of a `kind` texture is encoded as:
    a minimap's mips of MINIMAP_TOP and over 'minimap-top', else `kind`."""
    if kind == 'minimap' and min(size) >= MINIMAP_TOP:
        return 'minimap-top'
    return kind


def encoder_id(kind='texture'):
    """Which BC encoder writes textures of class `kind`, for cache keys: 'python' when the
    Python encoder does (RIFTLORD_BC_ENCODER=python, or no ISPC library), else 'ispc-' and a
    digest of the ISPC library file and ENCODER_SOURCES; a minimap's, whose mips from
    MINIMAP_TOP up ISPC encodes and the rest the Python encoder, ends '-top' and that size."""
    from riftlord.formats.ue import ispctex
    if os.environ.get(ispctex.ENCODER_ENV, 'ispc') == 'python' or not ispctex.available():
        return 'python'
    if kind == 'minimap':
        return 'ispc-%s-top%d' % (_ispc_digest(ispctex.library_path()), MINIMAP_TOP)
    if kind in ispctex.PYTHON_KINDS:
        return 'python'
    return 'ispc-' + _ispc_digest(ispctex.library_path())


_DIGESTS = {}


def _ispc_digest(library):
    """12 hex digits over the library file and ENCODER_SOURCES (computed once per library)."""
    if library not in _DIGESTS:
        import hashlib
        h = hashlib.sha256()
        package = os.path.dirname(HERE)                       # the riftlord package
        for path in [library] + [os.path.join(package, rel) for rel in ENCODER_SOURCES]:
            with open(path, 'rb') as fh:
                h.update(fh.read())
        _DIGESTS[library] = h.hexdigest()[:12]
    return _DIGESTS[library]


def mips(blobs):
    """(format, [{'width', 'height', 'file', 'at', 'size'}]) for a texture
    package: each mip's bytes are blobs[file][at:at + size]."""
    pkg = Package(blobs['.uasset'], blobs['.uexp'])
    names = [s for s, _h in pkg.names]
    uexp = blobs['.uexp']
    fmt = None
    base = None
    for name in names:
        if name.startswith('PF_') and name.encode() in uexp:
            at = uexp.find(name.encode() + b'\0')
            if at >= 4 and struct.unpack_from('<i', uexp, at - 4)[0] == len(name) + 1:
                fmt, base = name, at
                break
    if fmt is None:
        raise SystemExit('no pixel format found')
    o = base + len(fmt) + 1 + 4
    count = struct.unpack_from('<i', uexp, o)[0]
    o += 4
    out = []
    for _ in range(count):
        o += 4
        flags, _n, on_disk = struct.unpack_from('<iii', uexp, o)
        o += 12
        offset = struct.unpack_from('<q', uexp, o)[0]
        o += 8
        if flags & texture.BULKDATA_IN_SEPARATE_FILE:
            where, at = '.ubulk', offset
        else:
            where, at = '.uexp', o
            o += on_disk
        sx, sy, _sz = struct.unpack_from('<3i', uexp, o)
        o += 12
        out.append({'width': sx, 'height': sy, 'file': where, 'at': at, 'size': on_disk})
    return fmt, out


def decode(blobs, mip):
    """One mip as an RGBA image."""
    fmt, _ = mips(blobs)
    data = blobs[mip['file']][mip['at']:mip['at'] + mip['size']]
    info = {'width': mip['width'], 'height': mip['height'], 'format': fmt, 'data': data}
    return texture.to_image(info).convert('RGBA')


def encode(image, fmt, kind='texture'):
    """An RGBA image as one mip's bytes in this format; `kind` is its texture class (the BC
    encoder's choice, ispctex.chosen, by mip_class)."""
    if fmt in texture.RAW_MODES:
        mode, _bpp = texture.RAW_MODES[fmt]
        return image.convert('RGBA').tobytes('raw', mode)
    if fmt in ('PF_DXT1', 'PF_DXT5', 'PF_BC5'):
        from riftlord.build.lighting import bcn
        return bcn.encode(image, fmt, mip_class(kind, image.size))[
            :texture.mip_bytes(image.width, image.height, fmt)]
    pf = PILLOW_FORMATS.get(fmt)
    if pf is None:
        raise SystemExit('cannot encode %s' % fmt)
    buf = io.BytesIO()
    src = image.convert('RGBA' if pf == 'DXT5' else 'RGB')
    src.save(buf, format='DDS', pixel_format=pf)
    raw = buf.getvalue()
    return raw[128:128 + texture.mip_bytes(image.width, image.height, fmt)]


def write(blobs, picture, kind='texture'):
    """Each mip replaced by picture(width, height) (an RGBA image), encoded in
    the texture's own format at the mip's own size."""
    fmt, every = mips(blobs)
    for mip in every:
        if mip['file'] not in blobs:
            continue
        image = picture(mip['width'], mip['height'])
        data = encode(image, fmt, kind)
        if len(data) != mip['size']:
            raise SystemExit('mip %dx%d: %d bytes encoded, %d expected'
                             % (mip['width'], mip['height'], len(data), mip['size']))
        buf = bytearray(blobs[mip['file']])
        buf[mip['at']:mip['at'] + mip['size']] = data
        blobs[mip['file']] = bytes(buf)
    return fmt, every
