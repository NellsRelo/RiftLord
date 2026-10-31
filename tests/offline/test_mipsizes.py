"""BC mip byte lengths for sides that are not multiples of 4: texture.mip_bytes, and what
texpatch.encode (each mip texpatch.write stores) returns, against whole blocks rounded up
(riftlord.build.texsizes.need). A short mip crashed the game on Retry (2026-10-24).

    python3 tests/offline/test_mipsizes.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np                                        # noqa: E402
from PIL import Image                                     # noqa: E402

from riftlord.build import texpatch, texsizes             # noqa: E402
from riftlord.formats.ue import texture                   # noqa: E402

SIZES = ((3, 5), (6, 6), (10, 7), (13, 13), (1, 1), (4, 4), (64, 32))
FORMATS = ('PF_DXT1', 'PF_DXT5', 'PF_BC5')
checks = []


def check(name, ok, detail=''):
    checks.append(ok)
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def main():
    rng = np.random.default_rng(5)
    for fmt in FORMATS:
        for w, h in SIZES:
            want = texsizes.need(fmt, w, h)
            got = texture.mip_bytes(w, h, fmt)
            image = Image.fromarray(rng.integers(0, 256, (h, w, 4), dtype=np.uint8))
            for kind in ('texture', 'minimap'):
                data = texpatch.encode(image, fmt, kind)
                check('%s %dx%d (%s): mip_bytes %s, encoded %d, need %d' % (fmt, w, h, kind, got,
                                                                          len(data), want),
                      got == want == len(data))
    print('%d checks, %d failed' % (len(checks), checks.count(False)))
    return 0 if all(checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
