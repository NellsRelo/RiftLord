"""Extract a cooked UTexture2D's top mip as an image.

Most minimaps are PF_DXT5 (BC3) with every mip inline in the .uexp. Rather than
write a block decoder, a compressed mip is wrapped in a DDS header and handed to
Pillow, which already decodes DXT5 -- the same trick that reads OMD2's shipped
minimap .dds. The DLC campaigns ship theirs uncompressed instead, so raw
formats are read straight into an image.
"""

import io
import os
import struct

DDS_MAGIC = b'DDS '
DDSD = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000       # caps|height|width|pixelformat|linearsize
FOURCC = {'PF_DXT1': b'DXT1', 'PF_DXT3': b'DXT3', 'PF_DXT5': b'DXT5',
          'PF_BC5': b'ATI2', 'PF_BC4': b'ATI1'}
BLOCK_BYTES = {'PF_DXT1': 8, 'PF_BC4': 8, 'PF_DXT3': 16, 'PF_DXT5': 16,
               'PF_BC5': 16}
#: Uncompressed formats, as (Pillow raw mode, bytes per pixel).
RAW_MODES = {'PF_B8G8R8A8': ('BGRA', 4), 'PF_R8G8B8A8': ('RGBA', 4),
             'PF_G8': ('L', 1), 'PF_A8': ('L', 1)}
#: FByteBulkData flag: the payload is in the .ubulk beside the package.
BULKDATA_IN_SEPARATE_FILE = 0x0100


def dds_header(width, height, fmt, size):
    hdr = bytearray(128)
    hdr[0:4] = DDS_MAGIC
    struct.pack_into('<I', hdr, 4, 124)
    struct.pack_into('<I', hdr, 8, DDSD)
    struct.pack_into('<I', hdr, 12, height)
    struct.pack_into('<I', hdr, 16, width)
    struct.pack_into('<I', hdr, 20, size)          # linear size
    struct.pack_into('<I', hdr, 76, 32)            # pixelformat size
    struct.pack_into('<I', hdr, 80, 0x4)           # DDPF_FOURCC
    hdr[84:88] = FOURCC[fmt]
    struct.pack_into('<I', hdr, 108, 0x1000)       # DDSCAPS_TEXTURE
    return bytes(hdr)


def mip_bytes(width, height, fmt):
    """Byte length of one mip at this size, or None for an unknown format."""
    if fmt in RAW_MODES:
        return width * height * RAW_MODES[fmt][1]
    block = BLOCK_BYTES.get(fmt)
    if block is None:
        return None
    return ((width + 3) // 4) * ((height + 3) // 4) * block


def parse(uasset_path, uexp_path, ubulk_path=None, want=None):
    """The largest readable mip of a cooked Texture2D, as a dict; with `want`, the smallest
    readable mip whose longest edge is `want` or more (the largest when none is).

    Streaming mips live in a `.ubulk` sidecar; pass its path to reach them.
    Without it only the small inline mips are available.
    """
    import sys
    from riftlord.formats.ue.uheader import parse as parse_header
    from riftlord.formats.ue.parseuasset import R

    info = parse_header(uasset_path)
    names = info['names']
    hdr = os.path.getsize(uasset_path)
    with open(uexp_path, 'rb') as fh:
        uexp = fh.read()

    export = info['exports'][0]
    r = R(uexp, export['off'] - hdr)

    # Skip the tagged properties.
    while True:
        ni = r.i32(); r.i32()
        nm = names[ni] if 0 <= ni < len(names) else '?'
        if nm == 'None':
            break
        ti = r.i32(); r.i32()
        tp = names[ti] if 0 <= ti < len(names) else '?'
        size = r.i32(); r.i32()
        if tp == 'BoolProperty':
            r.u8(); r.u8(); continue
        if tp == 'StructProperty':
            r.i32(); r.i32(); r.raw(17)
        elif tp in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            r.i32(); r.i32(); r.u8()
        else:
            r.u8()
        r.raw(size)

    # The fields between the properties and the platform data vary, so rather
    # than assume each one, anchor on the pixel-format string: SizeX, SizeY and
    # PackedData are the three int32s immediately before its length prefix.
    tail = uexp[r.o:]
    marker = tail.find(b'PF_')
    if marker < 0:
        raise ValueError('no pixel format found; texture may not be cooked')
    base = r.o + marker
    length = struct.unpack_from('<i', uexp, base - 4)[0]
    fmt = uexp[base:base + length].split(b'\0')[0].decode('ascii')
    width, height, _packed = struct.unpack_from('<iii', uexp, base - 16)
    if fmt not in RAW_MODES and fmt not in BLOCK_BYTES:
        raise ValueError('unsupported pixel format %s' % fmt)

    bulk = b''
    if ubulk_path and os.path.exists(ubulk_path):
        with open(ubulk_path, 'rb') as fh:
            bulk = fh.read()

    # Walk the mip chain. The declared SizeX/SizeY are the source texture's, not
    # the largest cooked mip's -- a cook that drops the top mip leaves them
    # disagreeing -- so each mip carries its own dimensions at its tail.
    o = base + length + 4               # past the format string and skip offset
    num_mips = struct.unpack_from('<i', uexp, o)[0]
    o += 4
    mips = []
    for _ in range(num_mips):
        o += 4                          # bCooked
        flags, count, on_disk = struct.unpack_from('<iii', uexp, o)
        o += 12
        offset = struct.unpack_from('<q', uexp, o)[0]
        o += 8
        if flags & BULKDATA_IN_SEPARATE_FILE:
            data = bulk[offset:offset + on_disk]
        else:
            data = uexp[o:o + on_disk]
            o += on_disk
        sx, sy, _sz = struct.unpack_from('<3i', uexp, o)
        o += 12
        if len(data) == on_disk == mip_bytes(sx, sy, fmt):
            mips.append({'width': sx, 'height': sy, 'data': data})

    if not mips:
        raise ValueError('no readable mip; payload may be in a sidecar file')
    best = max(mips, key=lambda m: m['width'] * m['height'])
    if want:
        big = [m for m in mips if max(m['width'], m['height']) >= want]
        if big:
            best = min(big, key=lambda m: m['width'] * m['height'])
    return {'width': best['width'], 'height': best['height'], 'format': fmt,
            'mips': num_mips, 'data': best['data'],
            'declared': (width, height), 'expected': len(best['data'])}


def to_image(info):
    from PIL import Image
    size = (info['width'], info['height'])
    raw = RAW_MODES.get(info['format'])
    if raw:
        return Image.frombytes('RGBA' if raw[1] == 4 else 'L', size,
                               info['data'], 'raw', raw[0]).convert('RGBA')
    blob = dds_header(info['width'], info['height'], info['format'],
                      len(info['data'])) + info['data']
    return Image.open(io.BytesIO(blob)).convert('RGBA')
