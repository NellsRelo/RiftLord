"""Pieces on the shipped lattice: a new scratch level's ground grid, rift, exit point and
player starts on the 150 uu lattice at a height that is a multiple of 50
(riftlord/mapeditor/scratch.py); the wall classes kitplace_check.mjs takes its class
geometry from, measured by riftlord/mapeditor/kitfit.py, against the kit placement census's
own measures of them (docs/research/kit-placement-census.md sections 3 and 9); then
web/mapeditor/test/kitplace_check.mjs (kitplace.js
latticeDelta, footprintDelta, and the kit placement census's rules on class geometry as
/api/kit serves it) with node against the UI build's three.js (tools/build.py
WEB_STAGE), from a temporary copy so nothing is written beside the sources. The node part
skips when node or the staged node_modules are missing.

    python3 tests/offline/test_kit_snap.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import build  # noqa: E402

WEB = os.path.join(ROOT, 'web', 'mapeditor')
sys.path.insert(0, ROOT)

from riftlord.mapeditor import kitfit, scratch  # noqa: E402

#: kitplace.js LATTICE and HEIGHT_STEP, in uu.
LATTICE = 150.0
HEIGHT_STEP = 50.0


def on(value, step):
    return abs(value / step - round(value / step)) < 1e-9


def scratch_lattice():
    """Failures of the scratch level's ground and pre-placed actors against the lattice."""
    bad = []
    if not on(scratch.GRID_CELL, LATTICE) or not on(scratch.GRID_HALF, scratch.GRID_CELL):
        bad.append('ground grid lines %s every %s' % (scratch.GRID_HALF, scratch.GRID_CELL))
    if not on(scratch.GROUND_Z, HEIGHT_STEP):
        bad.append('ground height %s' % scratch.GROUND_Z)
    spots = {'rift and exit': scratch.RIFT_AT, 'wave start': scratch.WAVE_START}
    spots.update(('break start %d' % i, at) for i, at in enumerate(scratch.BREAK_STARTS))
    for what, (x, y) in spots.items():
        if not (on(x, LATTICE) and on(y, LATTICE)):
            bad.append('%s at %s, %s' % (what, x, y))
    return bad


#: Each wall class kitplace_check.mjs uses: (mesh below the grid's bottom, skirting reach in
#: front of its plane) as the census measured them, in uu, to its 0.5 uu rounding.
CENSUS_FIT = {
    'Wall_RB_MT_v1_1x4_C': (35.0, 48.0),
    'Wall_RB_2x2_C': (0.0, -1.5),
    'Wall_RB_3x1_C': (0.0, -4.0),
    'Wall_RB_WDMTL_v1_1x2_C': (38.0, 39.5),
    'Wall_RB_WDMT_v1_2x3_C': (38.5, 41.5),
    'Wall_RB_MT_v2_2x3_C': (35.0, 48.0),
}
#: The census rounds its measures to this many uu.
CENSUS_ROUNDING = 0.5


def census_fit():
    """Failures of kitfit's measures of CENSUS_FIT's classes against the census's."""
    bad = []
    for cls, (below, reach) in CENSUS_FIT.items():
        got = [f for f in kitfit.fits(cls) if f]
        if not got:
            bad.append('%s: no wall grid fit' % cls)
            continue
        f = got[0]
        if abs(f['below'] - below) > CENSUS_ROUNDING or abs(f['reach'] - reach) > CENSUS_ROUNDING:
            bad.append('%s: below %s, reach %s; census %s, %s' % (cls, f['below'], f['reach'], below, reach))
    return bad


def main():
    bad = census_fit()
    for line in bad:
        print('FAIL  kitfit: ' + line, flush=True)
    if bad:
        return 1
    print('PASS  kitfit: %d wall classes measure as the census measured them' % len(CENSUS_FIT),
          flush=True)
    bad = scratch_lattice()
    for line in bad:
        print('FAIL  scratch: ' + line, flush=True)
    if bad:
        return 1
    print('PASS  scratch ground, rift, exit and starts on the lattice', flush=True)
    node = shutil.which('node')
    modules = os.path.join(build.WEB_STAGE, 'web', 'mapeditor', 'node_modules')
    if not node or not os.path.isdir(os.path.join(modules, 'three')):
        print('SKIP  node or %s missing' % modules)
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, 'src'))
        os.makedirs(os.path.join(tmp, 'test'))
        shutil.copy2(os.path.join(WEB, 'src', 'kitplace.js'), os.path.join(tmp, 'src'))
        shutil.copy2(os.path.join(WEB, 'test', 'kitplace_check.mjs'), os.path.join(tmp, 'test'))
        shutil.copy2(os.path.join(WEB, 'package.json'), tmp)
        os.symlink(modules, os.path.join(tmp, 'node_modules'))
        got = subprocess.run([node, 'test/kitplace_check.mjs'], cwd=tmp, timeout=120)
    return got.returncode


if __name__ == '__main__':
    raise SystemExit(main())
