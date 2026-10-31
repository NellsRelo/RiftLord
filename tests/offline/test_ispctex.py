"""The ISPC Texture Compressor backend against our own BC encoder, on shipped textures.

    python3 tests/offline/test_ispctex.py

For each texture and format, both encoders' output is decoded and compared with the
source: the ISPC result's RMSE (alpha-weighted colour, and alpha) may exceed ours by at
most RMSE_SLACK. Prints both errors and times. Without the library (not built), it says
so and passes: the backend is optional.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np                                        # noqa: E402

from riftlord.formats.ue import ispctex                   # noqa: E402

#: Shipped textures to encode (UI icons with soft alpha edges).
TEXTURES = ('/Game/UI/Icons/ICON_Mode_Story_Off', '/Game/UI/Icons/ICON_Mode_Story_On')
#: Side the textures are scaled to (our encoder takes about 50 s on a 2048 one).
SIDE = 512
#: How much larger the ISPC RMSE may be than ours (0-255 scale).
RMSE_SLACK = 1.0
checks = []


def rmse(decoded, source, fmt):
    a = decoded.astype(np.float64)
    s = source.astype(np.float64)
    w = s[..., 3:4] / 255.0                          # colour error counts where it shows
    colour = np.sqrt((((a[..., :3] - s[..., :3]) ** 2) * w).sum() / max(w.sum() * 3, 1))
    alpha = np.sqrt(((a[..., 3] - s[..., 3]) ** 2).mean()) if fmt == 'PF_DXT5' else 0.0
    return colour, alpha


def main():
    if not ispctex.available():
        print('SKIP  the ISPC Texture Compressor is not built (native/ispctexcomp/build.py)')
        return 0
    from riftlord.build.lighting import bcn, lightcodec
    from riftlord.build.minimap import mapdata
    from riftlord.formats.ue import texture
    from PIL import Image
    images = []
    for path in TEXTURES:
        f = mapdata._fetch(path)
        info = texture.parse(f['.uasset'], f['.uexp'], f.get('.ubulk'))
        image = texture.to_image(info).convert('RGBA').resize((SIDE, SIDE), Image.LANCZOS)
        images.append((path.rsplit('/', 1)[-1], np.array(image)))
    y, x = np.mgrid[0:256, 0:256]
    images.append(('gradient', np.stack([x % 256, y % 256, (x + y) // 2 % 256,
                                         np.full(x.shape, 255)], -1).astype(np.uint8)))
    for name, img in images:
        h, w = img.shape[:2]
        img = img[:h - h % 4, :w - w % 4]
        h, w = img.shape[:2]
        for fmt in ('PF_DXT1', 'PF_DXT5'):
            # our encoder: bcn.encode takes ISPC by default (ispctex.chosen)
            os.environ[ispctex.ENCODER_ENV] = 'python'
            try:
                t = time.time()
                ours = bcn.encode(Image.fromarray(img), fmt)
                t_ours = time.time() - t
            finally:
                os.environ.pop(ispctex.ENCODER_ENV, None)
            t = time.time()
            theirs = ispctex.encode(img, fmt)
            t_theirs = time.time() - t
            a = rmse(lightcodec.decode(fmt, w, h, ours), img, fmt)
            b = rmse(lightcodec.decode(fmt, w, h, theirs), img, fmt)
            ok = len(ours) == len(theirs) and b[0] <= a[0] + RMSE_SLACK and b[1] <= a[1] + RMSE_SLACK
            checks.append(ok)
            print('%s  %-22s %s %4dx%-4d colour %.2f vs ours %.2f, alpha %.2f vs %.2f; '
                  '%.3f s vs %.3f s' % ('PASS' if ok else 'FAIL', name, fmt, w, h, b[0], a[0],
                                        b[1], a[1], t_theirs, t_ours))
    print('%d checks, %d failed' % (len(checks), checks.count(False)))
    return 0 if all(checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
