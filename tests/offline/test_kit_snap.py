"""Kit pieces on a scratch lattice (web/mapeditor/src/kitplace.js snapGrid): runs
web/mapeditor/test/kitplace_check.mjs with node against the UI build's three.js
(tools/build.py WEB_STAGE), from a temporary copy so nothing is written beside the sources.
Skips when node or the staged node_modules are missing.

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


def main():
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
