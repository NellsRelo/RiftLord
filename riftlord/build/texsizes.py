"""Every texture in a built pak: each mip's stored byte length against what its pixel format
and size need (whole 4x4 blocks, rounded up), and whether its bytes exist in its .uexp or
.ubulk. A BC mip whose side is not a multiple of 4 stored short (floor division) made the
render thread read past it on texture re-init: a crash on Retry (OrcsMustUI ICON_Flag,
2026-10-24).

    python3 -m riftlord.build.texsizes PAK [PAK ...]     # exit 1 on any problem
"""

import sys

#: (block side in pixels, bytes per block) per pixel format.
BLOCK = {'PF_DXT1': (4, 8), 'PF_DXT5': (4, 16), 'PF_BC5': (4, 16), 'PF_BC4': (4, 8),
         'PF_BC7': (4, 16), 'PF_B8G8R8A8': (1, 4), 'PF_G8': (1, 1), 'PF_FloatRGBA': (1, 8)}


def need(fmt, width, height):
    """Bytes one mip of this format and size takes: whole blocks, rounded up."""
    side, size = BLOCK[fmt]
    return max(1, -(-width // side)) * max(1, -(-height // side)) * size


def problems(files):
    """(textures checked, [problem lines]) for a pak's entries ({name: bytes})."""
    from riftlord.build import texpatch
    count, out = 0, []
    for name in sorted(files):
        if not name.endswith('.uasset'):
            continue
        stem = name[:-len('.uasset')]
        blobs = {e: files[stem + e] for e in ('.uasset', '.uexp', '.ubulk') if stem + e in files}
        if '.uexp' not in blobs:
            continue
        try:
            fmt, mips = texpatch.mips(blobs)
        except BaseException:                         # noqa: BLE001 - not a texture
            continue
        count += 1
        for i, m in enumerate(mips):
            bad = []
            if fmt in BLOCK and m['size'] != need(fmt, m['width'], m['height']):
                bad.append('size %d, needs %d' % (m['size'], need(fmt, m['width'], m['height'])))
            src = blobs.get(m['file'], b'')
            if m['at'] + m['size'] > len(src):
                bad.append('points past the end of %s (%d + %d > %d)'
                           % (m['file'], m['at'], m['size'], len(src)))
            if bad:
                out.append('%s %s mip %d %dx%d: %s' % (stem.split('Content/')[-1], fmt, i,
                                                       m['width'], m['height'], '; '.join(bad)))
    return count, out


def main(argv=None):
    from riftlord.core import paksource
    argv = sys.argv[1:] if argv is None else argv
    total = []
    for path in argv:
        count, bad = problems(paksource.entries(path))
        for line in bad:
            print(line)
        print('%s: %d textures checked, %d problem(s)' % (path.rsplit('/', 1)[-1], count, len(bad)))
        total += bad
    return 1 if total else 0


if __name__ == '__main__':
    raise SystemExit(main())
