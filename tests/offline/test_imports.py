"""Import every module of the riftlord package, with data, cache and config in a scratch
folder; check no old flat-layout name is loaded, bpkit comes from the vendored copy
(vendor/bpkit) and that copy is the one vendor/VENDORED.md records.

    python3 tests/offline/test_imports.py
"""
import importlib
import os
import pkgutil
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

#: Old top-level module names that must not appear in sys.modules except as bpkit's aliases.
OLD_NAMES = ('navpatch', 'bucket', 'uassetio', 'leveldata', 'makemod', 'omd3level',
             'mapdata', 'emit', 'server', 'trapmod', 'modifiermod', 'omd2roster')


def main():
    scratch = tempfile.mkdtemp(prefix='rl-imports-')
    try:
        for var, sub in (('XDG_DATA_HOME', 'data'), ('XDG_CACHE_HOME', 'cache'),
                         ('XDG_CONFIG_HOME', 'config'), ('RIFTLORD_SOURCE_CACHE', 'source'),
                         ('OMD3_BUCKET', 'Missions')):
            os.environ[var] = os.path.join(scratch, sub)
        import riftlord
        bad, n = [], 0
        for info in pkgutil.walk_packages(riftlord.__path__, 'riftlord.'):
            if info.name.endswith('__main__'):
                continue
            n += 1
            try:
                importlib.import_module(info.name)
            except BaseException as exc:                 # noqa: BLE001
                bad.append('%s: %s: %s' % (info.name, type(exc).__name__, str(exc)[:160]))
        leaked = [m for m in OLD_NAMES if m in sys.modules]
        import bpkit
        vendored = os.path.realpath(os.path.dirname(bpkit.__file__)) == \
            os.path.realpath(os.path.join(REPO, 'vendor', 'bpkit'))
        sys.path.insert(0, os.path.join(REPO, 'tools'))
        import vendor_bpkit
        recorded = vendor_bpkit.digest(vendor_bpkit.files(vendor_bpkit.VENDOR)) == \
            vendor_bpkit.recorded()
        for line in bad:
            print('FAIL', line)
        if leaked:
            print('FAIL old flat-layout modules loaded: %s' % ', '.join(leaked))
        if not vendored:
            print('FAIL bpkit did not come from vendor/bpkit')
        if not recorded:
            print('FAIL vendor/bpkit differs from vendor/VENDORED.md (tools/vendor_bpkit.py)')
        ok = not bad and not leaked and vendored and recorded
        print('%d modules imported, %d failed; bpkit from %s' % (n, len(bad), bpkit.__file__))
        return 0 if ok else 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
