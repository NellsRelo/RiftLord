# Generated from OMD2-Converter/thirdparty/fabconvert/vaulttex.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Source pixels of a pack's EDITOR textures, and sampling them as a material would.

The source is a PNG stream inside the .uasset (inline or in zlib-compressed bulk chunks);
measured on every Texture2D of the nine surveyed packs (thirdparty/inventory/
texture-sources.txt). The PNG holds the source's BGRA bytes as if they were RGBA, so R and
B are swapped back (proven in game: rounds mat1/mat2). This is round mat1's
`vault_texture`, moved here unchanged in behaviour.
"""

import io
import struct
import zlib

import numpy as np
from PIL import Image

#: Sources larger than this are box-downsampled once on load (the library never ships
#: textures above 2048, so finer source detail cannot reach the output).
MAX_SOURCE_PX = 2048
#: Samplers that decode an sRGB texture to linear (UE: the sRGB sampler types).
SRGB_SAMPLERS = ('SAMPLERTYPE_Color', 'SAMPLERTYPE_Grayscale', None)
GREY_SAMPLERS = ('SAMPLERTYPE_Grayscale', 'SAMPLERTYPE_LinearGrayscale')
NORMAL_SAMPLERS = ('SAMPLERTYPE_Normal',)
#: The bulk-chunk tag that precedes a zlib-compressed source payload.
CHUNK_TAG = bytes.fromhex('c1832a9e00000000')
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
JPEG_MAGIC = b'\xff\xd8\xff'
#: A JPEG source starts its payload; look this far in for the marker.
JPEG_SEARCH = 64


#: Source formats whose PNG holds true RGBA. Every other source (TSF_BGRA8, and packs that name
#: no format) is stored as BGRA bytes in an RGBA PNG and has R and B swapped back. Measured on
#: Magic Potion (UE 5.7, TSF_RGBA16): its ORM decodes R 0 / G 160 / B 180 when swapped.
TRUE_RGBA_SOURCES = ('TSF_RGBA16', 'TSF_RGBA16F', 'TSF_RGBA32F')
#: Part of the decoded-source cache key: raise it when decoding changes.
DECODE_VERSION = 2


def _png_order(path):
    """The channel order that turns a source PNG's RGBA into true RGBA."""
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'survey-tools'))
    from riftlord.fab.survey import pkgread
    names = set(pkgread.Package(path).names)
    return [0, 1, 2, 3] if names.intersection(TRUE_RGBA_SOURCES) else [2, 1, 0, 3]


def source_image(path):
    """RGBA PIL image of an editor Texture2D's source."""
    d = open(path, 'rb').read()
    blobs = [d]
    q = d.find(CHUNK_TAG)
    while q >= 0:
        try:
            o = q + 16
            _csz, usz = struct.unpack_from('<qq', d, o)
            o += 16
            ch, tot = [], 0
            while tot < usz:
                c, u = struct.unpack_from('<qq', d, o)
                o += 16
                ch.append((c, u))
                tot += u
            out = bytearray()
            for c, _u in ch:
                out += zlib.decompress(d[o:o + c])
                o += c
            blobs.append(bytes(out))
        except Exception:                            # noqa: BLE001 - not a chunk header
            pass
        q = d.find(CHUNK_TAG, q + 8)
    found = []
    for b in blobs:
        p = b.find(PNG_MAGIC)
        while p >= 0:
            w, h = struct.unpack_from('>II', b, p + 16)
            found.append((w * h, b, p))
            p = b.find(PNG_MAGIC, p + 8)
    for _n, b, p in sorted(found, key=lambda f: -f[0]):
        try:
            im = Image.open(io.BytesIO(b[p:]))
            im.load()
        except Exception:                            # noqa: BLE001
            continue
        if im.mode in ('RGB', 'RGBA'):
            a = np.asarray(im.convert('RGBA'))[..., _png_order(path)]
            return Image.fromarray(np.ascontiguousarray(a), 'RGBA')
        if im.mode in ('I;16', 'I;16B', 'I'):
            a = (np.asarray(im).astype(np.float64) / 257.0).clip(0, 255).astype(np.uint8)
            return Image.fromarray(a, 'L').convert('RGBA')
        return im.convert('RGBA')
    im = _ue5_source(path)
    if im is not None:
        return im
    raise ValueError('%s: no decodable PNG source' % path)


#: Bytes per pixel of the raw source formats UE5 packs use (measured: Winter City, Orasot).
UE5_BPP = {'TSF_BGRA8': 4, 'TSF_G8': 1, 'TSF_G16': 2, 'TSF_RGBA16': 8, 'TSF_RGBA16F': 8}
#: TSCF_UEDELTA stores every UEDELTA_STRIP-th row raw and the others as byte deltas from the row
#: above (heroes/lib/ue5tex.py, proven on the Techne textures).
UEDELTA_STRIP = 32


def _ue5_source(path):
    """A UE5-saved Texture2D: its source sits in an Oodle FCompressedBuffer, as a PNG
    (TSCF_PNG) or raw pixels (optionally TSCF_UEDELTA-filtered)."""
    import math
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'survey-tools'))
    from riftlord.fab.survey import meshgeo
    from riftlord.fab.survey import pkgread
    pk = pkgread.Package(path)
    if not pk.ue5:
        return None
    names = pk.names
    payloads = sorted(meshgeo.ue5_payloads(pk.d), key=lambda x: -len(x[1]))
    for _at, raw in payloads:
        q = raw.find(PNG_MAGIC)
        if q >= 0:
            try:
                im = Image.open(io.BytesIO(raw[q:]))
                im.load()
            except Exception:                        # noqa: BLE001
                continue
            if im.mode in ('RGB', 'RGBA'):
                a = np.asarray(im.convert('RGBA'))[..., _png_order(path)]
                return Image.fromarray(np.ascontiguousarray(a), 'RGBA')
            if im.mode in ('I;16', 'I;16B', 'I'):
                a = (np.asarray(im).astype(np.float64) / 257.0).clip(0, 255).astype(np.uint8)
                return Image.fromarray(a, 'L').convert('RGBA')
            return im.convert('RGBA')
    if 'TSCF_JPEG' in names:
        # JPEG sources decode in true RGB order (measured: Orasot normal maps average B 254,
        # R/G 127), unlike the PNG sources, whose bytes are BGRA
        for _at, raw in payloads:
            q = raw.find(JPEG_MAGIC, 0, JPEG_SEARCH)
            if q < 0:
                continue
            try:
                im = Image.open(io.BytesIO(raw[q:]))
                im.load()
            except Exception:                        # noqa: BLE001
                continue
            return im.convert('RGBA')
    fmt = next((n for n in names if n in UE5_BPP), None)
    if fmt is None or not payloads:
        return None
    bpp = UE5_BPP[fmt]
    raw = payloads[0][1]
    side = math.isqrt(len(raw) // bpp)
    if side * side * bpp != len(raw):
        return None                                   # not one square mip: unsupported
    a = np.frombuffer(raw, np.uint8).reshape(side, side * bpp)
    if 'TSCF_UEDELTA' in names:
        out = a.copy()
        for st in range(0, side, UEDELTA_STRIP):
            out[st:st + UEDELTA_STRIP] = np.cumsum(a[st:st + UEDELTA_STRIP].astype(np.uint32), axis=0).astype(np.uint8)
        a = out
    if fmt == 'TSF_BGRA8':
        px = a.reshape(side, side, 4)[..., [2, 1, 0, 3]]
    elif fmt == 'TSF_G8':
        px = np.repeat(a.reshape(side, side, 1), 3, -1)
        px = np.concatenate([px, np.full((side, side, 1), 255, np.uint8)], -1)
    elif fmt == 'TSF_G16':
        g = (a.view('<u2').reshape(side, side).astype(np.float64) / 257.0).clip(0, 255).astype(np.uint8)
        px = np.concatenate([np.repeat(g[..., None], 3, -1), np.full((side, side, 1), 255, np.uint8)], -1)
    elif fmt == 'TSF_RGBA16':
        px = (a.view('<u2').reshape(side, side, 4).astype(np.float64) / 257.0).clip(0, 255).astype(np.uint8)
    else:                                             # TSF_RGBA16F
        px = (a.view('<f2').reshape(side, side, 4).astype(np.float64).clip(0, 1) * 255.0).round().astype(np.uint8)
    return Image.fromarray(np.ascontiguousarray(px), 'RGBA')


def srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


class Textures:
    """Loads and samples pack textures by /Game path. pack: packgraph.Pack."""

    def __init__(self, pack, disk=None):
        self.pack = pack
        self.disk = disk            # folder for decoded sources (npy), or None
        self.cache = {}             # path -> decoded 8-bit array (or None)
        self.fcache = {}            # path -> float32 0..1 array

    def info(self, path):
        return self.pack.texture(path)

    def pixels(self, path):
        """(H, W, 4) float32 0..1 of the stored values (not yet sRGB-decoded), or None."""
        if path in self.fcache:
            return self.fcache[path]
        if path not in self.cache:
            self.cache[path] = self._decoded(path)
        raw = self.cache[path]
        arr = None if raw is None else raw.astype(np.float32) / 255.0
        self.fcache[path] = arr
        return arr

    def _decoded(self, path):
        """The source at most MAX_SOURCE_PX a side as its 8-bit array; decoded once per source
        file (path, size, mtime) into `disk` and loaded from there after."""
        f = self.pack.file_of(path)
        if not f:
            return None
        npy = None
        if self.disk:
            import hashlib
            import os
            st = os.stat(f)
            key = hashlib.sha1(('%s|%d|%d|%d|%d' % (f, st.st_size, st.st_mtime_ns, MAX_SOURCE_PX,
                                                       DECODE_VERSION)).encode()).hexdigest()[:20]
            npy = os.path.join(self.disk, key[:2], key + '.npy')
            if os.path.exists(npy):
                try:
                    return np.load(npy)
                except (OSError, ValueError):
                    pass
        try:
            im = source_image(f)
            if max(im.size) > MAX_SOURCE_PX:
                k = max(im.size) / MAX_SOURCE_PX
                im = im.resize((max(1, round(im.width / k)), max(1, round(im.height / k))), Image.BOX)
            raw = np.ascontiguousarray(np.asarray(im))
        except ValueError:
            return None
        if npy:
            import os
            os.makedirs(os.path.dirname(npy), exist_ok=True)
            tmp = '%s.%d.tmp' % (npy, os.getpid())
            with open(tmp, 'wb') as fh:
                np.save(fh, raw)
            os.replace(tmp, npy)
        return raw

    def size(self, path):
        a = self.pixels(path)
        return None if a is None else (a.shape[1], a.shape[0])

    def mean(self, path, sampler):
        a = self.pixels(path)
        if a is None:
            return None
        return self.decode(path, a.reshape(-1, 4), sampler).mean(0)

    def decode(self, path, px, sampler):
        info = self.info(path) or {}
        srgb = info.get('SRGB', True)
        out = px.copy()
        if sampler in NORMAL_SAMPLERS:
            xy = px[:, :2] * 2 - 1
            z = np.sqrt(np.clip(1 - (xy ** 2).sum(1, keepdims=True), 0, 1))
            out[:, :3] = np.concatenate([xy, z], 1)
            return out
        if srgb and sampler in SRGB_SAMPLERS:
            out[:, :3] = srgb_to_linear(px[:, :3])
        if sampler in GREY_SAMPLERS:
            out[:, 1:3] = out[:, :1]
        return out

    def sample(self, path, uv, sampler):
        """(P,4) bilinear, wrapped, decoded; None when the texture has no source here."""
        a = self.pixels(path)
        if a is None:
            return None
        h, w = a.shape[:2]
        x = uv[:, 0].astype(np.float64) * w - 0.5
        y = uv[:, 1].astype(np.float64) * h - 0.5
        x0, y0 = np.floor(x), np.floor(y)
        fx, fy = (x - x0)[:, None].astype(np.float32), (y - y0)[:, None].astype(np.float32)
        x0 = x0.astype(np.int64) % w
        y0 = y0.astype(np.int64) % h
        x1, y1 = (x0 + 1) % w, (y0 + 1) % h
        px = (a[y0, x0] * (1 - fx) * (1 - fy) + a[y0, x1] * fx * (1 - fy)
              + a[y1, x0] * (1 - fx) * fy + a[y1, x1] * fx * fy)
        return self.decode(path, px, sampler)
