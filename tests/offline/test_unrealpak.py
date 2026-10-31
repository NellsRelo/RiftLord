"""The UnrealPak backend against our own pak writer, on a real built mission.

    python3 tests/offline/test_unrealpak.py [BUILT_MISSION]

Writes BUILT_MISSION's files (a loose build folder or a pak; only read; default the
pinned baseline build of the test mission 900_Fixture_Edit_P) with both writers into a
scratch folder and
reads both back: every entry must be byte-identical (the containers differ: UnrealPak
writes version 11, ours version 4). Without UnrealPak built in the engine, it says so and
passes: the backend is optional.
"""
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import buildcompare                                        # noqa: E402
from riftlord.core import paksource, unrealpak             # noqa: E402
from riftlord.formats.ue import pak, pakv4                 # noqa: E402

BUILT = buildcompare.baseline_pak('900_Fixture_Edit_P')


def main():
    if not unrealpak.available():
        print('SKIP  UnrealPak is not built in %s' % unrealpak.root())
        return 0
    src = sys.argv[1] if len(sys.argv) > 1 else BUILT
    if not src or not os.path.exists(src):
        print('FAIL  no built mission to write: the test mission\'s pinned build is missing '
              '(tools/buildcompare.py --pin)')
        return 1
    files = paksource.entries(src)
    tmp = tempfile.mkdtemp(prefix='unrealpak-test-')
    try:
        ours, theirs = os.path.join(tmp, 'ours.pak'), os.path.join(tmp, 'theirs.pak')
        t = time.time()
        pakv4.write(ours, files)
        t_ours = time.time() - t
        t = time.time()
        unrealpak.write(theirs, files)
        t_theirs = time.time() - t
        # a v11 index names the mount point's own folder '/', which the reader keeps (as
        # it does for the shipped DLC paks): compare names with that slash folded
        fold = lambda files: {k.replace('//', '/'): v for k, v in files.items()}  # noqa: E731
        a, b = fold(paksource.entries(ours)), fold(paksource.entries(theirs))
        same = sorted(a) == sorted(b) and all(a[k] == b[k] for k in a)
        version = pak.Pak(theirs).version if hasattr(pak.Pak(theirs), 'version') else '?'
        print('%s  %d entries read back %s; UnrealPak pak version %s, %d bytes in %.1f s; '
              'ours %d bytes in %.1f s' % ('PASS' if same else 'FAIL', len(files),
                                           'identical' if same else 'DIFFERENT', version,
                                           os.path.getsize(theirs), t_theirs,
                                           os.path.getsize(ours), t_ours))
        if not same:
            print('  only ours: %s; only UnrealPak: %s; different: %s' % (
                sorted(set(a) - set(b))[:4], sorted(set(b) - set(a))[:4],
                [k for k in a if k in b and a[k] != b[k]][:4]))
        return 0 if same else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
