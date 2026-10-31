"""Offline checks of the Trace tool's copy removal (trace.drop_covered) and of the
Unchained base-colour choice (trace_omdu.pick_base). No game files needed.

    python3 tests/offline/test_trace_copies.py

Exit 0 when every check passes.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np                                    # noqa: E402

from riftlord.formats.omdu import trace_omdu          # noqa: E402
from riftlord.mapeditor import trace                  # noqa: E402

FAILED = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILED.append(what)


def square(x0, y0, side, z=0.0):
    """Two triangles of a horizontal square."""
    a, b, c, d = (x0, y0, z), (x0 + side, y0, z), (x0 + side, y0 + side, z), (x0, y0 + side, z)
    return [[a, b, c], [a, c, d]]


def run(pieces):
    """drop_covered over [(owner, triangles)]: the kept mask per piece."""
    tris = np.asarray([t for _o, ts in pieces for t in ts], np.float32)
    owner = np.asarray([o for o, ts in pieces for _t in ts], np.int64)
    keep = trace.drop_covered(tris, owner)
    out, at = [], 0
    for _o, ts in pieces:
        out.append(keep[at:at + len(ts)].tolist())
        at += len(ts)
    return out


def main():
    got = run([(0, square(0, 0, 400)), (1, square(0, 0, 400))])
    check(sum(got[0]) + sum(got[1]) == 2 and all(got[0]),
          'two placements of one square: the earlier stays, the copy goes (%s)' % got)
    got = run([(0, square(0, 0, 400)), (1, square(400, 0, 400))])
    check(all(got[0]) and all(got[1]), 'tiles sharing an edge both stay (%s)' % got)
    got = run([(1, square(0, 0, 1600)), (0, square(400, 400, 200))])
    check(all(got[0]) and not any(got[1]), 'a small tile on a big floor goes, the floor stays (%s)' % got)
    got = run([(0, square(0, 0, 400)), (1, square(0, 0, 400, z=2.0))])
    check(all(got[0]) and all(got[1]), 'squares 2 uu apart both stay (%s)' % got)
    got = run([(0, square(0, 0, 400)), (1, square(300, 300, 400))])
    check(all(got[0]) and all(got[1]), 'squares overlapping in a corner both stay (%s)' % got)
    got = run([(0, square(0, 0, 400) + square(400, 0, 400)), (1, square(0, 0, 800))])
    check(all(got[1]) and not any(got[0]), 'tiles under one bigger copy of their floor go (%s)' % got)
    got = run([(0, square(0, 0, 400)), (0, square(0, 0, 400))])
    check(all(got[0]) and all(got[1]), 'repeats inside one placement are left to the stencil (%s)' % got)

    pick = trace_omdu.pick_base
    check(pick([('BaseAOMap', 'wallbuttress_df', 'ao'), ('BaseNormalMap', 'wallbuttress_nm', 'nm'),
                ('DetailDiffuseMap', 'hl_castlebrick2_hi_df', 'detail')]) == 'detail',
          'an AO map named _df is never the base colour; the detail map is')
    check(pick([('DiffuseMap', 'stone_df', 'base'), ('DetailDiffuseMap', 'brick_hi_df', 'detail')]) == 'base',
          'a real diffuse map beats a detail map')
    check(pick([('RedDiffuseMap', 'moss_df', 'paint'), ('DetailDiffuseMap', 'brick_hi_df', 'detail')]) == 'detail',
          'a detail map beats a mesh-paint layer')
    check(pick([('BaseAOMap', 'rock_df', 'ao'), ('RGBMask', 'blank_df', 'blank')]) == 'ao',
          'with nothing else, an AO map beats the blank placeholder')
    check(pick([('NormalMap', 'blank_nm', 'nm')]) is None, 'a blank normal map is never a base colour')
    print('trace copies: %d failed' % len(FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    raise SystemExit(main())
