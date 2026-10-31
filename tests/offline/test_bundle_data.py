"""Every data file the code reads through gamepath.data is bundled with the app
(tools/build.py DATA), apart from those only build and survey tools read (DEV_ONLY); every
source file the terrain kits' Setup key reads is bundled as text (tools/build.py
fab_code_files); and every file Setup's type-data keys read (riftlord.types.setup.CODE) is
bundled as text where setup._code_hash reads it (tools/build.py types_code_files).

    python3 tests/offline/test_bundle_data.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import build  # noqa: E402

#: Data files read only by build, survey and conversion tools, never by the app.
DEV_ONLY = {'compound-curation.json', 'omd2-models.json', 'omd3-models.json',
            'prefab-map.json'}
READ_RE = re.compile(r"gamepath\.data\('([^']+)'\)")


def key_sources():
    """The source files under riftlord/ the terrain kits' Setup key reads
    (riftlord.terrainkit.setup.key), as paths under it."""
    import builtins
    from riftlord.fab import library
    from riftlord.terrainkit import build as terrain_build
    from riftlord.terrainkit import setup as terrain_setup
    package = os.path.join(ROOT, 'riftlord')
    read, real = set(), builtins.open

    def spy(path, *a, **kw):
        if isinstance(path, (str, os.PathLike)) and os.fspath(path).endswith('.py'):
            read.add(os.path.relpath(os.path.abspath(os.fspath(path)), package))
        return real(path, *a, **kw)
    library._LOGIC.clear()
    builtins.open = spy
    try:
        try:
            terrain_setup.key()
        except (Exception, SystemExit):              # noqa: BLE001 -- no game: no paks stamp
            terrain_build.code_hash()
            library.logic_hash(*library.PIECE_CODE)
    finally:
        builtins.open = real
    return read


def main():
    read = set()
    for base, _dirs, files in os.walk(os.path.join(ROOT, 'riftlord')):
        for name in files:
            if name.endswith('.py'):
                with open(os.path.join(base, name), encoding='utf-8') as fh:
                    read.update(READ_RE.findall(fh.read()))
    missing = sorted(read - set(build.DATA) - DEV_ONLY)
    present = [d for d in build.DATA if not os.path.exists(os.path.join(ROOT, 'riftlord', 'data', d))]
    unbundled = sorted(key_sources() - set(build.fab_code_files()))
    from riftlord.core import bpkitlink
    from riftlord.types import setup as types_setup
    bundled = {(folder, os.path.basename(src)) for src, folder in build.types_code_files()}
    want = {('bpkit', n[6:]) if n.startswith('bpkit:') else
            (os.path.join('riftlord', 'types'), n) for names in types_setup.CODE.values()
            for n in names}
    types_missing = sorted('%s/%s' % w for w in want - bundled)
    types_absent = sorted(n for names in types_setup.CODE.values() for n in names
                          if not os.path.isfile(os.path.join(bpkitlink.VENDORED, n[6:])
                                                if n.startswith('bpkit:') else
                                                os.path.join(ROOT, 'riftlord', 'types', n)))
    checks = [('every data file the app reads is bundled', not missing, ', '.join(missing)),
              ('every bundled data file exists', not present, ', '.join(present)),
              ('every source file the terrain kits\' Setup key reads is bundled as text',
               not unbundled, ', '.join(unbundled)),
              ('every file Setup\'s type-data keys read is bundled as text where they read it',
               not types_missing, ', '.join(types_missing)),
              ('every file Setup\'s type-data keys read exists', not types_absent,
               ', '.join(types_absent))]
    for name, ok, detail in checks:
        print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + detail) if detail else ''))
    failed = sum(1 for _n, ok, _d in checks if not ok)
    print('%d checks, %d failed' % (len(checks), failed))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
