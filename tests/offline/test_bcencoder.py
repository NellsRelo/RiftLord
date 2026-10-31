"""Which BC encoder each texture class takes: minimap mips under texpatch.MINIMAP_TOP the
Python encoder, always, and larger ones ISPC (texpatch.mip_class: 'minimap-top'); the rest
ISPC when its library loads (riftlord.formats.ue.ispctex.chosen); texpatch.encoder_id, the
cache keys' name for it, agreeing with that choice; and the bound the minimap split was
measured against: on minimap pictures as Rift Lord draws them
(tests/offline/minimap_pictures), ISPC's error on a mip it encodes is within bccertify.SLACK
of the Python encoder's, and the first size kept on the Python encoder still needs it.

    python3 tests/offline/test_bcencoder.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import numpy as np                                        # noqa: E402
from PIL import Image                                     # noqa: E402

import bccertify                                          # noqa: E402
from riftlord.build import texpatch                       # noqa: E402
from riftlord.build.lighting import bcn, lightcodec       # noqa: E402
from riftlord.formats.ue import ispctex                   # noqa: E402

#: Minimap pictures as the build paints them (scratch.minimap's painter): North Wings
#: (Regenerate), Proving Grounds (a scratch level), Beset (Cut).
FIXTURES = os.path.join(ROOT, 'tests', 'offline', 'minimap_pictures')
PICTURES = ('north_wings.png', 'proving_grounds.png', 'beset.png')
checks = []


def check(name, ok, detail=''):
    checks.append(ok)
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % detail) if detail else ''))


def python_bytes(image, fmt):
    os.environ[ispctex.ENCODER_ENV] = 'python'
    try:
        return bcn.encode(image, fmt)
    finally:
        os.environ.pop(ispctex.ENCODER_ENV, None)


def errors(image, data):
    """bccertify.rmse (colour, alpha) of DXT5 `data` against `image`."""
    a = np.asarray(image)
    return bccertify.rmse(lightcodec.decode('PF_DXT5', image.width, image.height, data), a,
                          'PF_DXT5')


def quality():
    """ISPC against the Python encoder on the fixture pictures, mipped as scratch.minimap
    mips them (a BOX resize of the full picture)."""
    side = texpatch.MINIMAP_TOP
    worse_below = []
    for name in PICTURES:
        full = Image.open(os.path.join(FIXTURES, name)).convert('RGBA')
        for n in (side, side // 2):
            image = full if n == full.width else full.resize((n, n), Image.BOX)
            py = errors(image, python_bytes(image, 'PF_DXT5'))
            isp = errors(image, ispctex.encode(np.asarray(image), 'PF_DXT5'))
            detail = 'colour %.4f vs %.4f, alpha %.4f vs %.4f' % (isp[0], py[0], isp[1], py[1])
            within = isp[0] <= py[0] + bccertify.SLACK and isp[1] <= py[1] + bccertify.SLACK
            if n >= side:
                check('%s %d: ISPC within the Python encoder\'s RMSE + %.2f' % (name, n, bccertify.SLACK),
                      within, detail)
            else:
                print('      %s %d: %s' % (name, n, detail))
                worse_below.append(not within)
    check('%d px: ISPC beyond the Python encoder\'s RMSE + %.2f on some picture (the split is needed)'
          % (side // 2, bccertify.SLACK), any(worse_below))


def main():
    y, x = np.mgrid[0:64, 0:64]
    rgba = np.stack([x * 4 % 256, y * 4 % 256, (x + y) * 2 % 256,
                     np.where((x // 8 + y // 8) % 2, 255, 96)], -1).astype(np.uint8)
    image = Image.fromarray(rgba)
    side = texpatch.MINIMAP_TOP
    big = Image.fromarray(np.tile(rgba, (side // 64, side // 64, 1)))
    os.environ.pop(ispctex.ENCODER_ENV, None)
    check('minimap mips under %d px: class minimap' % side,
          texpatch.mip_class('minimap', (side // 2, side // 2)) == 'minimap'
          == texpatch.mip_class('minimap', (side, side // 2)))
    check('minimap mips of %d px and over: class minimap-top' % side,
          texpatch.mip_class('minimap', (side, side)) == 'minimap-top'
          == texpatch.mip_class('minimap', (2 * side, 2 * side)))
    check('other classes keep their class at any size',
          texpatch.mip_class('texture', (side, side)) == 'texture'
          and texpatch.mip_class('lightmap', (2 * side, side)) == 'lightmap')
    check('class minimap: never ISPC', not ispctex.chosen('PF_DXT5', 'minimap'))
    for fmt in ('PF_DXT1', 'PF_DXT5'):
        check('minimap %s under %d px: the Python encoder\'s bytes' % (fmt, side),
              bcn.encode(image, fmt, 'minimap') == python_bytes(image, fmt))
        check('texpatch minimap %s under %d px: the Python encoder\'s bytes' % (fmt, side),
              texpatch.encode(image, fmt, 'minimap') == python_bytes(image, fmt)[:len(
                  texpatch.encode(image, fmt, 'minimap'))])
    if ispctex.available():
        check('class minimap-top: ISPC', ispctex.chosen('PF_DXT5', 'minimap-top'))
        check('texpatch minimap DXT5 at %d px: ISPC\'s bytes' % side,
              texpatch.encode(big, 'PF_DXT5', 'minimap') == ispctex.encode(np.asarray(big), 'PF_DXT5'))
        check('lightmap: ISPC', ispctex.chosen('PF_DXT1', 'lightmap'))
        check('lightmap DXT1: ISPC\'s bytes',
              lightcodec.encode('PF_DXT1', rgba) == ispctex.encode(rgba, 'PF_DXT1'))
        check('other textures: ISPC', bcn.encode(image, 'PF_DXT5') == ispctex.encode(rgba, 'PF_DXT5'))
    else:
        print('SKIP  the ISPC library is not built: minimap top mips, lightmap and texture checks')
    os.environ[ispctex.ENCODER_ENV] = 'python'
    check('RIFTLORD_BC_ENCODER=python: no ISPC for lightmaps', not ispctex.chosen('PF_DXT1', 'lightmap'))
    check('RIFTLORD_BC_ENCODER=python: no ISPC for minimap top mips',
          not ispctex.chosen('PF_DXT5', 'minimap-top'))
    check('encoder_id under RIFTLORD_BC_ENCODER=python: python',
          texpatch.encoder_id('texture') == 'python' == texpatch.encoder_id('lightmap')
          == texpatch.encoder_id('minimap'))
    os.environ.pop(ispctex.ENCODER_ENV, None)
    if ispctex.available():
        got = texpatch.encoder_id('texture')
        check('encoder_id for textures: ispc- and 12 hex digits, the same for lightmaps',
              len(got) == 17 and got.startswith('ispc-') and all(c in '0123456789abcdef' for c in got[5:])
              and got == texpatch.encoder_id('lightmap'), got)
        check('encoder_id for minimaps: the textures\' and the split',
              texpatch.encoder_id('minimap') == '%s-top%d' % (got, side), texpatch.encoder_id('minimap'))
        texpatch._DIGESTS.clear()
        check('encoder_id is stable', texpatch.encoder_id('texture') == got)
        texpatch._DIGESTS[ispctex.library_path()] = 'x' * 12          # a changed library or source
        check('encoder_id follows the digest', texpatch.encoder_id('texture') == 'ispc-' + 'x' * 12)
        texpatch._DIGESTS.clear()
        quality()
    # every minimap texture is written by scratch.minimap: its texpatch.write says 'minimap'
    path = os.path.join(ROOT, 'riftlord', 'mapeditor', 'scratch.py')
    with open(path) as fh:
        tree = ast.parse(fh.read())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'minimap')
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, 'attr', '') == 'write'
             and getattr(c.func.value, 'id', '') == 'texpatch']
    kinds = [k.value.value for c in calls for k in c.keywords
             if k.arg == 'kind' and isinstance(k.value, ast.Constant)]
    check('scratch.minimap writes its texture as kind minimap', bool(calls) and kinds == ['minimap'] * len(calls),
          kinds)
    print('%d checks, %d failed' % (len(checks), checks.count(False)))
    return 0 if all(checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
